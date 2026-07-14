"""Unit tests beyond the conformance vectors.

The vector suite (test_vectors.py) is the behavioral contract; these tests
cover what it cannot reach: exact error messages on parser error paths,
API-level type handling, repr, defensive branches in the static checks, and
evaluator paths the vectors happen not to exercise.
"""

from __future__ import annotations

import inspect
import time
from datetime import datetime, timezone

import pytest

import dtrexp
from dtrexp import DTRExpSyntaxError, DTRExpWarning, ValidationResult
from dtrexp.checks import (
    _enum_selector,
    _enum_years,
    _item_set,
    _possible_domain_sizes,
    _resolve,
    _satisfiable,
    static_warnings,
)
from dtrexp.fields import days_in_quarter, weeks_in_week_year
from dtrexp.nodes import Branch, Selector, Stride, Value, VRange
from dtrexp.parser import _parse_branch

UTC = timezone.utc


# ------------------------------------------------------------------ API level


def test_parse_rejects_non_string():
    with pytest.raises(DTRExpSyntaxError, match="expression must be a string"):
        dtrexp.parse(123)  # type: ignore[arg-type]


def test_covers_accepts_iso_string_instant():
    expr = dtrexp.parse("M7")
    assert expr.covers("2026-07-07T10:00:00+00:00") is True
    assert expr.covers("2026-06-07T10:00:00+00:00") is False


def test_covers_accepts_naive_datetime_as_utc():
    expr = dtrexp.parse("H10")
    assert expr.covers(datetime(2026, 7, 7, 10, 30)) is True
    assert expr.covers(datetime(2026, 7, 7, 11, 30)) is False


def test_repr():
    assert repr(dtrexp.parse("M1")) == "Expression('M1')"


# ------------------------------------------------------------------ validate()


def test_validate_valid():
    assert dtrexp.validate("M7") == ValidationResult(True, (), ())


def test_validate_valid_with_warnings_matches_expression():
    res = dtrexp.validate("M3 Q2")
    assert res.valid is True
    assert res.errors == ()
    assert res.warnings == dtrexp.parse("M3 Q2").warnings


def test_validate_invalid_carries_one_positioned_error():
    res = dtrexp.validate("Y*/3")
    assert res.valid is False
    assert res.warnings == ()
    (err,) = res.errors
    assert isinstance(err, DTRExpSyntaxError)
    assert err.position == 1
    assert "anchorless stride" in str(err)


def test_validate_never_raises_on_non_string():
    res = dtrexp.validate(123)  # type: ignore[arg-type]
    assert res.valid is False
    assert res.errors[0].position is None


# --------------------------------------------------------------- error object


def test_syntax_error_renders_position():
    with pytest.raises(DTRExpSyntaxError) as ei:
        dtrexp.parse("M13")
    assert ei.value.position == 0
    assert str(ei.value) == "value 13 out of domain for 'M' (1-12) (at 0)"


def test_syntax_error_without_position_renders_bare_message():
    err = DTRExpSyntaxError("expression must be a string")
    assert err.position is None
    assert str(err) == "expression must be a string"


# --------------------------------------------------------- parser error paths


@pytest.mark.parametrize(
    ("expression", "match", "pos"),
    [
        # date/bounds/cadence scanning
        ("*:20", "malformed date literal", 2),
        ("20260101:20", "malformed date literal", 9),
        ("*", r"a bare '\*' component is not valid", 0),
        ("*:*", "bounds require at least one date-literal endpoint", 0),
        ("20260101T09", "malformed time part in date literal", 0),
        ("20260101T2500", "invalid time part in date literal", 0),
        ("20261301", "not a real calendar date", 0),
        ("20260101:20250101", "backwards bounds range", 0),
        ("20260101/", "malformed cadence", 9),
        ("20260101/0D", "cadence period must be >= 1", 9),
        ("20260101/1D/0D", "cadence duration must be >= 1", 12),
        ("20260101/1D/1M", "month/year duration unit requires a month/year period", 12),
        ("20260101/1D/1D", "cadence duration must be conservatively smaller", 12),
        # selector value parsing
        ("M", "designator 'M' without value", 0),
        ("M1-2", "malformed value '1-2'", 1),
        ("M--1", "malformed value '--1'", 1),
        ("M-0", "'-0' is not a value", 1),
        ("M!", "designator 'M' without value", 2),
        ("M1:2:3", "malformed range '1:2:3'", 1),
        ("M1:", "malformed range end ''", 3),
        ("M:1", "malformed range start ''", 1),
        ("M1,*", r"bare '\*' in a list", 3),
        ("M1.5", "'.' is valid only inside T literals", 2),
        ("M1!2", "exclusion '!' is valid only immediately after the designator", 2),
        ("M!1/2", "either an exclusion or carries a stride", 3),
        ("M!1#2", "ordinal '#' cannot combine with exclusion", 3),
        # ordinal
        ("M1#2", "ordinal '#' is valid only on E", 2),
        ("E1,2#1", "single weekday value and a single ordinal", 1),
        ("E1#2#3", "single weekday value and a single ordinal", 1),
        ("E0#1", "weekday 0 out of domain", 1),
        ("E8#1", "weekday 8 out of domain", 1),
        ("E-8#1", "weekday -8 out of domain", 1),
        ("E1#0", "ordinal zero", 3),
        ("E1#6", "ordinal out of range", 3),
        # stride
        ("M1/2/1/1", "too many '/' parts in stride", 1),
        ("M1,2/3", "stride not allowed on a list", 2),
        ("M1/-2", "stride interval/duration must be positive integers", 3),
        ("M1:3:5/2", "malformed range '1:3:5'", 1),
        ("M*:5/2", "anchorless stride", 1),
        ("M*/2", "anchorless stride", 1),
        ("M-1/2", "stride start must be non-negative", 1),
        ("M-1:5/2", "stride start must be non-negative", 1),
        ("M1/1", "stride interval must be >= 2", 3),
        ("M1/3/3", "stride duration must be >= 1 and < interval", 5),
        # T selector
        ("T090", "malformed time value '090'", 1),
        ("T005960", "leap seconds are not representable", 1),
        ("T0060", "minute out of range in time value", 1),
        ("T24", "hour 24 is written exactly '2400'", 1),
        ("T25", "hour out of range in time value", 1),
        ("T!0900", "T takes no exclusion", 1),
        ("T", "designator 'T' without value", 0),
        ("T0900:1200:1800", "malformed time range '0900:1200:1800'", 1),
        ("T0900:0900", "equal endpoints", 1),
        ("T0900,2500", "hour out of range in time value", 6),
        # cross-value validation — positioned at the offending component
        ("M0", "value 0 out of domain for 'M'", 0),
        ("Y0", "year 0 out of domain", 0),
        ("Y-1", "negative value on Y", 0),
        ("Q1 D93", "value 93 out of domain for 'D'", 3),
        ("M1/20", "stride interval 20 exceeds the parent domain size", 0),
        ("Y2030:2020", "backwards range on Y", 0),
        ("Y2030:2020/2", "backwards range on Y", 0),
        ("M5:2/2", "wrap ranges take no stride", 0),
        # structure — offsets are absolute, also in a later union branch
        ("", "empty expression", 0),
        ("M1|", "empty union branch", 3),
        ("M1 | M13", "value 13 out of domain for 'M'", 5),
        ("x", "unexpected character 'x' at position 0", 0),
        ("M1 M2", "duplicate designator 'M'", 3),
        ("T0900 T1000", "duplicate designator 'T'", 6),
        ("20260101 20270101", "at most one bounds component", 9),
        ("20260101/2D 20270101/2D", "at most one cadence", 12),
        # date-literal scanning — field slices and time-part bounds
        ("202613019", r"'20261301' is not a real calendar date", 0),
        ("20260101T0900123", "malformed date literal at position 15", 15),
        ("20260101T0060", "invalid time part in date literal", 0),
        ("20260101T2400", "invalid time part in date literal", 0),
        ("20260101T000060", "invalid time part in date literal", 0),
        # datelike '*'/':' boundary handling
        ("*:", "malformed date literal at position 2", 2),
        ("20260101:", "malformed date literal at position 9", 9),
        # cadence duration/period conservative check — positions and units
        ("20260101/1D", "cadence duration must be conservatively smaller", 9),
        ("20260101/1D/1Y", "month/year duration unit requires a month/year period", 12),
        ("20260101/1Y/12M", "cadence duration must be conservatively smaller", 12),
        ("20260101/1D/24H", "cadence duration must be conservatively smaller", 12),
    ],
)
def test_invalid_error_messages(expression, match, pos):
    with pytest.raises(DTRExpSyntaxError, match=match) as ei:
        dtrexp.parse(expression)
    assert ei.value.position == pos


def test_parse_branch_all_whitespace_is_defensive():
    # Unreachable through parse() — parse_branches strips each union part
    # first — but _parse_branch still guards an all-whitespace input itself.
    with pytest.raises(DTRExpSyntaxError, match="empty expression"):
        _parse_branch("   ")


# ------------------------------------------------------- evaluator edge paths


def test_covers_all_kind_selector():
    assert dtrexp.parse("M*").covers(datetime(2026, 1, 15, tzinfo=UTC)) is True


def test_bare_millisecond_time_value_spans_one_millisecond():
    expr = dtrexp.parse("T090000.500")
    assert expr.covers(datetime(2026, 7, 7, 9, 0, 0, 500_000, tzinfo=UTC)) is True
    assert expr.covers(datetime(2026, 7, 7, 9, 0, 0, 501_000, tzinfo=UTC)) is False


def test_covers_year_exclusion():
    expr = dtrexp.parse("Y!2020")
    assert expr.covers(datetime(2020, 6, 1, tzinfo=UTC)) is False
    assert expr.covers(datetime(2021, 6, 1, tzinfo=UTC)) is True


def test_covers_weekday_ordinal_in_quarter_scope():
    # 2026-07-07 is the first Tuesday of Q3 (doq 7); 2026-07-14 the second.
    expr = dtrexp.parse("Q3 E2#1")
    assert expr.covers(datetime(2026, 7, 7, tzinfo=UTC)) is True
    assert expr.covers(datetime(2026, 7, 14, tzinfo=UTC)) is False


def test_cadence_hourly_with_naive_instant():
    expr = dtrexp.parse("20260101T0000/2H/1H")
    assert expr.covers(datetime(2026, 1, 1, 0, 30)) is True
    assert expr.covers(datetime(2026, 1, 1, 1, 30)) is False


def test_cadence_year_duration_window():
    expr = dtrexp.parse("20260101/2Y/1Y")
    assert expr.covers(datetime(2026, 6, 1, tzinfo=UTC)) is True
    assert expr.covers(datetime(2027, 6, 1, tzinfo=UTC)) is False
    assert expr.covers(datetime(2028, 6, 1, tzinfo=UTC)) is True


def test_cadence_week_duration_window():
    expr = dtrexp.parse("20260101/1M/1W")
    assert expr.covers(datetime(2026, 1, 3, tzinfo=UTC)) is True
    assert expr.covers(datetime(2026, 1, 10, tzinfo=UTC)) is False
    assert expr.covers(datetime(2026, 2, 3, tzinfo=UTC)) is True


# ------------------------------------------------------ static-warning paths


def test_quiet_month_all_with_quarter():
    assert dtrexp.parse("M* Q1").warnings == ()


def test_quiet_month_matching_quarter():
    assert dtrexp.parse("M1 Q1").warnings == ()


def test_warn_month_exclusion_never_in_quarter():
    assert dtrexp.parse("Q1 M!1:3").warnings != ()


def test_warn_month_stride_never_in_quarter():
    # M1/6 selects months {1, 7}; Q2 is months {4, 5, 6}.
    assert dtrexp.parse("Q2 M1/6").warnings != ()


def test_warn_week53_with_enumerable_year_stride():
    # Y2021:2023/2 selects {2021, 2023}; neither ISO week-year has 53 weeks.
    assert dtrexp.parse("Y2021:2023/2 W53").warnings != ()


def test_quiet_week53_with_open_year_stride():
    # An open-ended Y stride is not statically enumerable — W53 stays quiet.
    assert dtrexp.parse("Y2020/5 W53").warnings == ()


def test_enum_selector_ordinal_is_opaque():
    # Ordinal selectors have no static value set (defensive: E-only in practice).
    assert _enum_selector(Selector("E", "ordinal", ordinal=(1, 1)), 1, 7) is None


def test_static_warnings_skip_non_enumerable_mq():
    # Defensive: the parser never yields ordinal-kind M/Q selectors, but the
    # M-and-Q intersection check must stay quiet when either side is opaque.
    m_ordinal = Selector("M", "ordinal", ordinal=(1, 1))
    q_list = Selector("Q", "list", (Value(1),))
    assert static_warnings(Branch((m_ordinal, q_list), None, None, None, False)) == []
    m_list = Selector("M", "list", (Value(1),))
    q_ordinal = Selector("Q", "ordinal", ordinal=(1, 1))
    assert static_warnings(Branch((m_list, q_ordinal), None, None, None, False)) == []


# ----------------------------------------------------- calendar-size helpers


def test_days_in_quarter():
    assert days_in_quarter(2026, 1) == 90
    assert days_in_quarter(2024, 1) == 91
    assert days_in_quarter(2026, 2) == 91
    assert days_in_quarter(2026, 3) == 92
    assert days_in_quarter(2026, 4) == 92


def test_weeks_in_week_year():
    assert weeks_in_week_year(2020) == 53
    assert weeks_in_week_year(2025) == 52
    assert weeks_in_week_year(2026) == 53


# --------------------------------------------- static-check helper contracts


def test_checks_resolve():
    assert _resolve(5, 1, 12) == 5
    assert _resolve(-1, 1, 12) == 12
    assert _resolve(-12, 1, 12) == 1


def test_item_set_values_and_ranges():
    assert _item_set(Value(3), 1, 12) == {3}
    assert _item_set(Value(-1), 1, 12) == {12}
    assert _item_set(Value(-13), 1, 12) == set()
    assert _item_set(VRange(11, 1), 1, 12) == {11, 12, 1}
    assert _item_set(VRange(2, 2), 1, 12) == {2}
    assert _item_set(VRange(2, -1), 1, 12) == set(range(2, 13))
    assert _item_set(VRange(None, 3), 1, 12) == {1, 2, 3}
    assert _item_set(VRange(10, None), 1, 12) == {10, 11, 12}
    assert _item_set(VRange(22, 3), 0, 23) == {22, 23, 0, 1, 2, 3}


def test_enum_selector_kinds():
    assert _enum_selector(None, 1, 12) is None
    assert _enum_selector(Selector("M", "all"), 1, 12) == set(range(1, 13))
    assert _enum_selector(Selector("M", "list", (Value(2), VRange(5, 7))), 1, 12) == {2, 5, 6, 7}
    assert _enum_selector(Selector("M", "exclusion", (Value(1),)), 1, 12) == set(range(2, 13))
    assert _enum_selector(Selector("H", "stride", stride=Stride(4, 10, 3, 2)), 0, 23) == {4, 5, 7, 8, 10}
    assert _enum_selector(Selector("H", "stride", stride=Stride(0, 9, 3, 1)), 0, 23) == {0, 3, 6, 9}
    assert _enum_selector(Selector("M", "stride", stride=Stride(2, -2, 3, 1)), 1, 12) == {2, 5, 8, 11}
    assert _enum_selector(Selector("M", "stride", stride=Stride(2, None, 5, 1)), 1, 12) == {2, 7, 12}


def test_enum_years():
    assert _enum_years(None) is None
    assert _enum_years(Selector("Y", "all")) is None
    assert _enum_years(Selector("Y", "stride", stride=Stride(2020, None, 3, 1))) is None
    assert _enum_years(Selector("Y", "stride", stride=Stride(2020, 2026, 3, 2))) == {2020, 2021, 2023, 2024, 2026}
    assert _enum_years(Selector("Y", "list", (Value(2020), VRange(2022, 2024)))) == {2020, 2022, 2023, 2024}
    assert _enum_years(Selector("Y", "list", (VRange(2020, None),))) is None
    assert _enum_years(Selector("Y", "list", (VRange(None, 2024),))) is None


def test_possible_domain_sizes_fixed():
    assert _possible_domain_sizes(Selector("Y", "all"), {}) is None
    assert _possible_domain_sizes(Selector("Q", "all"), {}) == (4,)
    assert _possible_domain_sizes(Selector("M", "all"), {}) == (12,)
    assert _possible_domain_sizes(Selector("E", "all"), {}) == (7,)
    assert _possible_domain_sizes(Selector("H", "all"), {}) == (23,)
    assert _possible_domain_sizes(Selector("m", "all"), {}) == (59,)
    assert _possible_domain_sizes(Selector("s", "all"), {}) == (59,)


def test_possible_domain_sizes_weeks():
    w = Selector("W", "all")
    assert _possible_domain_sizes(w, {}) == (52, 53)
    assert _possible_domain_sizes(w, {"Y": Selector("Y", "list", (Value(2020),))}) == (53,)
    assert _possible_domain_sizes(w, {"Y": Selector("Y", "list", (Value(2025),))}) == (52,)


def test_possible_domain_sizes_day_month_scope():
    d = Selector("D", "list", (Value(1),), scope="M")
    assert _possible_domain_sizes(d, {}) == (28, 29, 30, 31)
    assert _possible_domain_sizes(d, {"M": Selector("M", "list", (Value(2),))}) == (28, 29)
    assert _possible_domain_sizes(d, {"M": Selector("M", "list", (Value(-12),))}) == (31,)
    assert _possible_domain_sizes(d, {"M": Selector("M", "list", (Value(4),))}) == (30,)


def test_possible_domain_sizes_day_quarter_scope():
    d = Selector("D", "list", (Value(1),), scope="Q")
    assert _possible_domain_sizes(d, {}) == (90, 91, 92)
    assert _possible_domain_sizes(d, {"Q": Selector("Q", "list", (Value(1),))}) == (90, 91)
    assert _possible_domain_sizes(d, {"Q": Selector("Q", "list", (Value(3),))}) == (92,)
    assert _possible_domain_sizes(d, {"Q": Selector("Q", "list", (Value(-1),))}) == (92,)


def test_possible_domain_sizes_day_year_scope():
    d = Selector("D", "list", (Value(1),), scope="Y")
    assert _possible_domain_sizes(d, {}) == (365, 366)
    assert _possible_domain_sizes(d, {"Y": Selector("Y", "list", (Value(2020),))}) == (366,)
    assert _possible_domain_sizes(d, {"Y": Selector("Y", "list", (Value(2021),))}) == (365,)
    y2020 = Selector("Y", "list", (Value(2020),))
    assert _possible_domain_sizes(d, {"W": Selector("W", "all"), "Y": y2020}) == (365, 366)


def test_satisfiable():
    assert _satisfiable(Selector("M", "all"), 1, 12) is True
    assert _satisfiable(Selector("E", "ordinal", ordinal=(1, 1)), 1, 7) is True
    assert _satisfiable(Selector("H", "stride", stride=Stride(0, None, 3, 1)), 0, 23) is True
    assert _satisfiable(Selector("M", "stride", stride=Stride(12, None, 6, 1)), 1, 12) is True
    assert _satisfiable(Selector("D", "stride", stride=Stride(31, None, 5, 1)), 1, 30) is False
    assert _satisfiable(Selector("M", "list", (Value(5),)), 1, 12) is True
    assert _satisfiable(Selector("M", "exclusion", (VRange(1, 12),)), 1, 12) is False
    assert _satisfiable(Selector("M", "exclusion", (VRange(1, 11),)), 1, 12) is True


# --------------------------------------------------- warning-content pinning

_MQ_MSG = "statically unsatisfiable: the selected months never fall in the selected quarters"


def test_month_quarter_negative_values_quiet():
    assert dtrexp.parse("M-1 Q4").warnings == ()
    assert dtrexp.parse("M12 Q-1").warnings == ()


def test_month_quarter_warning_message_exact():
    assert dtrexp.parse("M3 Q2").warnings == (DTRExpWarning(_MQ_MSG, 0),)
    assert dtrexp.parse("M4 Q2").warnings == ()
    assert dtrexp.parse("M6 Q2").warnings == ()


def test_month_quarter_warning_position_is_the_month_component():
    (w,) = dtrexp.parse("Q2 M3").warnings
    assert w.position == 3


def test_selector_warning_message_exact():
    assert dtrexp.parse("Y2025 W53").warnings == (
        DTRExpWarning(
            "statically unsatisfiable: the 'W' component can never match "
            "in any parent instance selected by this expression",
            6,
        ),
    )


def test_warning_str_is_the_message():
    (w,) = dtrexp.parse("M3 Q2").warnings
    assert str(w) == w.message == _MQ_MSG


def test_zero_based_domains_quiet():
    assert dtrexp.parse("H0").warnings == ()
    assert dtrexp.parse("m0").warnings == ()
    assert dtrexp.parse("s0").warnings == ()


def test_full_exclusion_warns():
    assert dtrexp.parse("M!1:12").warnings != ()


# ------------------------------------------------- evaluator domain pinning


def test_covers_tz_default_is_utc():
    assert inspect.signature(dtrexp.Expression.covers).parameters["tz"].default == "UTC"


def test_negative_values_resolve_against_domains():
    assert dtrexp.parse("Q-1").covers(datetime(2026, 10, 15, tzinfo=UTC)) is True
    assert dtrexp.parse("Q-1").covers(datetime(2026, 7, 15, tzinfo=UTC)) is False
    assert dtrexp.parse("M-12").covers(datetime(2026, 1, 15, tzinfo=UTC)) is True
    assert dtrexp.parse("Q3 D-92").covers(datetime(2026, 7, 1, tzinfo=UTC)) is True
    assert dtrexp.parse("Y2026 D-365").covers(datetime(2026, 1, 1, tzinfo=UTC)) is True
    assert dtrexp.parse("m-60").covers(datetime(2026, 1, 1, 10, 0, tzinfo=UTC)) is True
    assert dtrexp.parse("s-60").covers(datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)) is True


def test_year_range_bounds():
    expr = dtrexp.parse("Y2020:2022")
    assert expr.covers(datetime(2022, 6, 1, tzinfo=UTC)) is True  # end inclusive
    assert expr.covers(datetime(2023, 6, 1, tzinfo=UTC)) is False  # above end
    expr = dtrexp.parse("Y2020:*")
    assert expr.covers(datetime(2019, 6, 1, tzinfo=UTC)) is False
    assert expr.covers(datetime(2035, 6, 1, tzinfo=UTC)) is True
    expr = dtrexp.parse("Y*:2022")
    assert expr.covers(datetime(2019, 6, 1, tzinfo=UTC)) is True
    assert expr.covers(datetime(2023, 6, 1, tzinfo=UTC)) is False


def test_year_stride_end_inclusive():
    expr = dtrexp.parse("Y2020:2024/2")
    assert expr.covers(datetime(2024, 6, 1, tzinfo=UTC)) is True  # stride end inclusive
    assert expr.covers(datetime(2025, 6, 1, tzinfo=UTC)) is False


def test_range_edge_semantics():
    expr = dtrexp.parse("M5:5")
    assert expr.covers(datetime(2026, 5, 15, tzinfo=UTC)) is True
    assert expr.covers(datetime(2026, 6, 15, tzinfo=UTC)) is False
    expr = dtrexp.parse("M2:-1")
    assert expr.covers(datetime(2026, 12, 15, tzinfo=UTC)) is True
    assert expr.covers(datetime(2026, 1, 15, tzinfo=UTC)) is False


def test_weekday_ordinal_scopes_distinguish_position():
    # Aug 4 2026 is the 5th Tuesday of Q3 but the 1st Tuesday of August.
    assert dtrexp.parse("Q3 E2#5").covers(datetime(2026, 8, 4, tzinfo=UTC)) is True
    # Feb 3 2026 is the 5th Tuesday of the year but the 1st Tuesday of February.
    assert dtrexp.parse("Y2026 E2#5").covers(datetime(2026, 2, 3, tzinfo=UTC)) is True


def test_weekday_negative_ordinal_boundary():
    # Aug 24 2026 (Monday) is exactly 7 days before the month's last Monday.
    assert dtrexp.parse("E1#-2").covers(datetime(2026, 8, 24, tzinfo=UTC)) is True
    assert dtrexp.parse("E1#-1").covers(datetime(2026, 8, 24, tzinfo=UTC)) is False
    assert dtrexp.parse("E1#-1").covers(datetime(2026, 8, 31, tzinfo=UTC)) is True


def test_bare_time_value_units():
    expr = dtrexp.parse("T09")
    assert expr.covers(datetime(2026, 1, 1, 9, 59, 59, tzinfo=UTC)) is True
    assert expr.covers(datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)) is False
    expr = dtrexp.parse("T0930")
    assert expr.covers(datetime(2026, 1, 1, 9, 30, 59, tzinfo=UTC)) is True
    assert expr.covers(datetime(2026, 1, 1, 9, 31, 0, tzinfo=UTC)) is False
    expr = dtrexp.parse("T093015")
    assert expr.covers(datetime(2026, 1, 1, 9, 30, 15, 999_999, tzinfo=UTC)) is True
    assert expr.covers(datetime(2026, 1, 1, 9, 30, 16, tzinfo=UTC)) is False


# ------------------------------------------------- cadence boundary pinning


def test_cadence_window_boundaries_exact():
    expr = dtrexp.parse("20260101T0000/2H/1H")
    assert expr.covers(datetime(2026, 1, 1, 0, 0, tzinfo=UTC)) is True  # start inclusive
    assert expr.covers(datetime(2026, 1, 1, 1, 0, tzinfo=UTC)) is False  # end exclusive
    assert expr.covers(datetime(2026, 1, 1, 2, 0, tzinfo=UTC)) is True  # next occurrence
    assert expr.covers(datetime(2025, 12, 31, 23, 0, tzinfo=UTC)) is False  # before anchor


def test_cadence_year_duration_boundary():
    expr = dtrexp.parse("20260101/2Y/1Y")
    assert expr.covers(datetime(2027, 1, 15, tzinfo=UTC)) is False


def test_cadence_day_window_reaching_from_previous_occurrence():
    expr = dtrexp.parse("20260101T1200/2D/40H")
    assert expr.covers(datetime(2026, 1, 3, 3, 0, tzinfo=UTC)) is True
    assert expr.covers(datetime(2026, 1, 3, 5, 0, tzinfo=UTC)) is False


def test_cadence_week_grid():
    expr = dtrexp.parse("20260101/2W")
    assert expr.covers(datetime(2026, 1, 8, tzinfo=UTC)) is False
    assert expr.covers(datetime(2026, 1, 15, tzinfo=UTC)) is True


def test_cadence_month_window_reaching_from_previous_occurrence():
    expr = dtrexp.parse("20260115/2M/46D")
    assert expr.covers(datetime(2026, 3, 1, tzinfo=UTC)) is True
    assert expr.covers(datetime(2026, 3, 3, tzinfo=UTC)) is False


def test_cadence_year_far_from_anchor():
    expr = dtrexp.parse("20020601/1Y/1D")
    assert expr.covers(datetime(2026, 6, 1, tzinfo=UTC)) is True
    assert expr.covers(datetime(2026, 6, 2, tzinfo=UTC)) is False


def test_hourly_cadence_grid_is_absolute_across_dst():
    # 2025-03-09 02:00 America/New_York is the spring-forward gap; the 2H grid
    # anchored at 01:00 EST advances in absolute time, not wall-clock time:
    # 01:00 EST = 06:00 UTC, so the k=1 occurrence starts 08:00 UTC (04:00 EDT).
    expr = dtrexp.parse("20250309T0100/2H/1H")
    assert expr.covers(datetime(2025, 3, 9, 8, 30, tzinfo=UTC), tz="America/New_York") is True
    assert expr.covers(datetime(2025, 3, 9, 7, 30, tzinfo=UTC), tz="America/New_York") is False


def test_hourly_cadence_grid_immune_to_host_timezone(monkeypatch):
    # Same scenario, but with the *process-local* timezone forced into the DST
    # transition: the UTC grid arithmetic must not pick up host wall-clock.
    monkeypatch.setenv("TZ", "America/New_York")
    time.tzset()
    try:
        expr = dtrexp.parse("20250309T0100/2H/1H")
        assert expr.covers(datetime(2025, 3, 9, 8, 30, tzinfo=UTC), tz="America/New_York") is True
        assert expr.covers(datetime(2025, 3, 9, 7, 30, tzinfo=UTC), tz="America/New_York") is False
    finally:
        monkeypatch.undo()
        time.tzset()



# ---------------------------------------------------------------------------
# Mutation-pass kills: exact messages, exact positions, boundary behavior.
# The substring-matching table above cannot see message rewording or position
# slips; these tables assert the whole rendered error. Parametrize ids stay
# ASCII (the expression), never the message — see test_vectors._ascii_id.

_T_SELECTOR_ERRORS = [
    ("T", "designator 'T' without value (at 0)"),
    ("T*", "T takes no '*' — only values, ranges and lists (at 1)"),
    ("T0900/2/2", "T takes no stride — only values, ranges and lists (at 5)"),
    ("T0900#2", "T takes no ordinal — only values, ranges and lists (at 5)"),
    ("T-0900", "T takes no negative value — only values, ranges and lists (at 1)"),
    (
        "T005960",
        "second out of range in time value "
        "(leap seconds are not representable) (at 1)",
    ),
    ("T0060", "minute out of range in time value (at 1)"),
    ("T24", "hour 24 is written exactly '2400', and only as a range end (at 1)"),
    ("T25", "hour out of range in time value (at 1)"),
]


@pytest.mark.parametrize(
    ("expression", "message"),
    _T_SELECTOR_ERRORS,
    ids=[e for e, _ in _T_SELECTOR_ERRORS],
)
def test_time_selector_exact_error_messages(expression, message):
    with pytest.raises(DTRExpSyntaxError) as ei:
        dtrexp.parse(expression)
    assert str(ei.value) == message


def test_time_range_endpoint_error_positions():
    # a bad range start is positioned at the range start
    with pytest.raises(DTRExpSyntaxError) as ei:
        dtrexp.parse("T2500:0900")
    assert "hour out of range" in str(ei.value)
    assert ei.value.position == 1
    # a bad range end is positioned just after 'HHMM:'
    with pytest.raises(DTRExpSyntaxError) as ei:
        dtrexp.parse("T0900:2500")
    assert "hour out of range" in str(ei.value)
    assert ei.value.position == 6


def test_bare_2400_is_rejected_only_a_range_end_may_use_it():
    with pytest.raises(DTRExpSyntaxError, match="hour 24 is written exactly '2400'"):
        dtrexp.parse("T2400")


def test_millisecond_time_value_requires_seconds():
    with pytest.raises(DTRExpSyntaxError, match="malformed time value '1230.500'"):
        dtrexp.parse("T1230.500")


def test_time_value_accepts_boundary_minute_and_second():
    assert dtrexp.parse("T0959").covers(datetime(2026, 1, 1, 9, 59, 0, tzinfo=timezone.utc)) is True
    assert (
        dtrexp.parse("T093059").covers(datetime(2026, 1, 1, 9, 30, 59, tzinfo=timezone.utc)) is True
    )


def test_midnight_wrap_range_covers_exact_midnight():
    expr = dtrexp.parse("T2200:0600")
    assert expr.covers(datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)) is True
    assert expr.covers(datetime(2026, 1, 1, 5, 0, 0, tzinfo=timezone.utc)) is True
    assert expr.covers(datetime(2026, 1, 1, 23, 0, 0, tzinfo=timezone.utc)) is True
    assert expr.covers(datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)) is False


_VALIDATION_ERRORS = [
    ("Y-1", "negative value on Y — no edge to count back from", 0),
    ("M-13", "value -13 out of domain for 'M' (-12..-1)", 0),
    ("Y2030:2020", "backwards range on Y — no edge to wrap around", 0),
    ("Y2030:2020/2", "backwards range on Y — no edge to wrap around", 0),
    ("M5:2/2", "wrap ranges take no stride", 0),
    (
        "M1/20",
        "stride interval 20 exceeds the parent domain size (12) — use a date-anchored cadence",
        0,
    ),
    # stride-branch endpoint domain checks carry the selector position
    ("M13/2", "value 13 out of domain for 'M' (1-12)", 0),
    ("M1:13/2", "value 13 out of domain for 'M' (1-12)", 0),
]


@pytest.mark.parametrize(
    ("expression", "message", "pos"),
    _VALIDATION_ERRORS,
    ids=[e for e, _, _ in _VALIDATION_ERRORS],
)
def test_validation_exact_error_message_and_position(expression, message, pos):
    with pytest.raises(DTRExpSyntaxError) as ei:
        dtrexp.parse(expression)
    assert str(ei.value) == f"{message} (at {pos})"
    assert ei.value.position == pos


def test_exclusion_value_out_of_domain_is_rejected():
    with pytest.raises(DTRExpSyntaxError, match=r"value 13 out of domain for 'M'") as ei:
        dtrexp.parse("M!13")
    assert ei.value.position == 0


def test_range_endpoints_out_of_domain_are_positioned():
    with pytest.raises(DTRExpSyntaxError) as ei:
        dtrexp.parse("M13:20")  # range start out of domain
    assert ei.value.position == 0
    with pytest.raises(DTRExpSyntaxError) as ei:
        dtrexp.parse("M1:20")  # range end out of domain
    assert ei.value.position == 0


def test_equal_endpoint_year_range_is_accepted():
    # start == end is a one-year range, not a backwards wrap.
    assert dtrexp.validate("Y2020:2020").valid is True


def test_wrap_range_stride_with_end_at_domain_low_rejected():
    # end == lo is still an in-domain wrap range; a stride on it is rejected.
    with pytest.raises(DTRExpSyntaxError, match="wrap ranges take no stride") as ei:
        dtrexp.parse("M5:1/2")
    assert ei.value.position == 0


def test_single_value_stride_range_is_accepted():
    # start == end (a one-value range) carries a stride without error.
    assert dtrexp.validate("M2:2/2").valid is True


def test_stride_interval_equal_to_domain_size_is_accepted():
    assert dtrexp.validate("M1/12").valid is True


def test_stride_interval_just_over_domain_size_is_rejected():
    with pytest.raises(DTRExpSyntaxError, match="stride interval 13 exceeds the parent domain size") as ei:
        dtrexp.parse("M1/13")
    assert ei.value.position == 0


def test_backwards_year_stride_uses_year_lower_bound():
    # The Y lower bound (1) gates the in-domain wrap test for Y strides.
    with pytest.raises(DTRExpSyntaxError, match="backwards range on Y") as ei:
        dtrexp.parse("Y5:1/2")
    assert ei.value.position == 0


_STRUCTURE_ERRORS = [
    ("", "empty expression (at 0)"),
    ("M1|", "empty union branch (at 3)"),
    ("M1,*", "bare '*' in a list — the list is already the whole domain (at 3)"),
    ("20260101:20250101", "backwards bounds range (at 0)"),
    ("20260101 20270101", "at most one bounds component per expression (at 9)"),
    ("20260101/2D 20270101/2D", "at most one cadence per expression (at 12)"),
    ("T0900 T1000", "duplicate designator 'T' in one expression (at 6)"),
    ("MQ1", "designator 'M' without value (at 0)"),
    # a third union branch: its absolute start offset must keep accumulating
    ("M1|M2|M13", "value 13 out of domain for 'M' (1-12) (at 6)"),
]


@pytest.mark.parametrize(
    ("expression", "rendered"),
    _STRUCTURE_ERRORS,
    ids=[e if e else "empty" for e, _ in _STRUCTURE_ERRORS],
)
def test_structure_error_message_exact(expression, rendered):
    with pytest.raises(DTRExpSyntaxError) as ei:
        dtrexp.parse(expression)
    assert str(ei.value) == rendered


def test_parse_non_string_message_exact():
    with pytest.raises(DTRExpSyntaxError) as ei:
        dtrexp.parse(123)  # type: ignore[arg-type]
    assert str(ei.value) == "expression must be a string"
    assert ei.value.position is None


def test_parse_branch_empty_expression_message_and_position():
    # Direct call reaches _parse_branch's own empty guard; it renders with the
    # exact text and position == the branch start (the default 0).
    with pytest.raises(DTRExpSyntaxError) as ei:
        _parse_branch("   ")
    assert str(ei.value) == "empty expression (at 0)"


def test_date_literal_time_bounds_accept_maxima():
    # mi=59 and ss=59 are valid, not rejected as out of range.
    assert dtrexp.parse("20260101T0059").covers(datetime(2026, 1, 1, 0, 59, 30, tzinfo=timezone.utc)) is True
    expr = dtrexp.parse("20260101T000059")
    assert expr.covers(datetime(2026, 1, 1, 0, 0, 59, tzinfo=timezone.utc)) is True
    assert expr.covers(datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)) is False


def test_date_literal_seconds_bounds_range():
    # A ':' bounds range following a full T-literal with seconds parses cleanly.
    expr = dtrexp.parse("20260101T090059:20260301")
    assert expr.covers(datetime(2026, 2, 1, tzinfo=timezone.utc)) is True


def test_open_ended_upper_bounds():
    # 'date:*' is a lower-bounded, open-ended window.
    expr = dtrexp.parse("20260101:*")
    assert expr.covers(datetime(2027, 1, 1, tzinfo=timezone.utc)) is True
    assert expr.covers(datetime(2025, 1, 1, tzinfo=timezone.utc)) is False


def test_open_ended_upper_bounds_then_selector():
    # The scan resumes at the right offset after 'date:*', so a following selector parses.
    expr = dtrexp.parse("20260101:*M1")
    assert expr.covers(datetime(2027, 1, 15, tzinfo=timezone.utc)) is True
    assert expr.covers(datetime(2027, 2, 15, tzinfo=timezone.utc)) is False


_CADENCE_BOUNDS_ERRORS = [
    ("20260101/", "malformed cadence — expected <n><unit> with unit in Y M W D H m"),
    ("20260101/0D", "cadence period must be >= 1"),
    ("20260101/1D/0D", "cadence duration must be >= 1"),
    ("20260101/1D/1M", "month/year duration unit requires a month/year period"),
    (
        "20260101/1D/1D",
        "cadence duration must be conservatively smaller than the period "
        "(duration x max unit length < period x min unit length)",
    ),
    ("*", "a bare '*' component is not valid — bounds need a ':' range"),
    (
        "*:*",
        "bounds require at least one date-literal endpoint — "
        "an unbounded window is spelled by omitting bounds",
    ),
]


@pytest.mark.parametrize(
    ("expression", "message"),
    _CADENCE_BOUNDS_ERRORS,
    ids=[e for e, _ in _CADENCE_BOUNDS_ERRORS],
)
def test_cadence_bounds_error_message_exact(expression, message):
    with pytest.raises(DTRExpSyntaxError) as ei:
        dtrexp.parse(expression)
    assert ei.value.args[0] == message


_SELECTOR_ERRORS = [
    ("M1!2", "exclusion '!' is valid only immediately after the designator", 2),
    ("M!1!2", "exclusion '!' is valid only immediately after the designator", 3),
    ("M1!2!3", "exclusion '!' is valid only immediately after the designator", 2),
    ("M1.5", "'.' is valid only inside T literals", 2),
    ("M1.2.3", "'.' is valid only inside T literals", 2),
    ("M!1/2", "a component is either an exclusion or carries a stride — never both", 3),
    ("M!1/2/3", "a component is either an exclusion or carries a stride — never both", 3),
    ("M!1#2", "ordinal '#' cannot combine with exclusion", 3),
    ("M!1#2#3", "ordinal '#' cannot combine with exclusion", 3),
    ("M!13", "value 13 out of domain for 'M' (1-12)", 0),
    ("M1#2#3", "ordinal '#' is valid only on E, not 'M'", 2),
    ("E1,2#1", "ordinal takes a single weekday value and a single ordinal", 1),
    ("E--1#1", "malformed weekday '--1'", 1),
    ("E1#--1", "malformed ordinal '--1'", 3),
    ("E1#0", "ordinal zero", 3),
    ("E1#6", "ordinal out of range (-5..-1, 1..5)", 3),
    ("M1/2/1/1", "too many '/' parts in stride", 1),
    ("M1,2/3", "stride not allowed on a list", 2),
    ("M1,2,3/4", "stride not allowed on a list", 2),
    ("M1/-2", "stride interval/duration must be positive integers", 3),
    ("M*:5/2", "anchorless stride — an explicit range start is required", 1),
    ("M-1:5/2", "stride start must be non-negative (end-relative anchors shift per parent instance)", 1),
    ("M1-2:5/2", "malformed stride start '1-2'", 1),
    ("M1:2-3/2", "malformed stride end '2-3'", 3),
    ("M/2", "anchorless stride — an explicit start is required", 1),
    ("M*/2", "anchorless stride — an explicit start is required", 1),
    ("M-1/2", "stride start must be non-negative (end-relative anchors shift per parent instance)", 1),
    ("M1-2/2", "malformed stride start '1-2'", 1),
    ("M1/1", "stride interval must be >= 2", 3),
    ("M1/3/3", "stride duration must be >= 1 and < interval", 5),
]


@pytest.mark.parametrize(
    ("expression", "message", "pos"),
    _SELECTOR_ERRORS,
    ids=[e for e, _, _ in _SELECTOR_ERRORS],
)
def test_selector_error_message_and_position_exact(expression, message, pos):
    with pytest.raises(DTRExpSyntaxError) as ei:
        dtrexp.parse(expression)
    assert ei.value.args[0] == message
    assert ei.value.position == pos


def test_weekday_negative_seven_lower_bound_is_inclusive():
    # -7 is in domain (maps to weekday 1, Monday); the check is `-7 <= w`, not `-7 < w`.
    expr = dtrexp.parse("E-7#1")
    assert expr.covers(datetime(2026, 8, 3, tzinfo=timezone.utc)) is True  # first Monday of Aug 2026
