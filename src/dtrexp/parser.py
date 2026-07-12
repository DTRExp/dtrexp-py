"""Parser and static validation for DTRExp draft 2.8."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from .errors import DTRExpSyntaxError as Err
from .nodes import (
    D_MAX_BY_SCOPE,
    DOMAIN_HI,
    DOMAIN_LO,
    MICROS_PER_DAY,
    Bounds,
    Branch,
    Cadence,
    Item,
    Selector,
    Stride,
    TimeSel,
    UNIT_DESIGNATORS,
    Value,
    VRange,
)

_SELECTOR_DESIGNATORS = set(UNIT_DESIGNATORS) | {"T"}
_VALUE_CHARS = set("0123456789,:*/!#.-")
_CADENCE_UNITS = "YMWDHm"
_YEAR_MAX = 9999

# Conservative fixed unit lengths in minutes: (max, min) — spec §5.2.
_UNIT_MINUTES = {
    "Y": (366 * 1440, 365 * 1440),
    "M": (31 * 1440, 28 * 1440),
    "W": (7 * 1440, 7 * 1440),
    "D": (1440, 1440),
    "H": (60, 60),
    "m": (1, 1),
}

_INT_RE = re.compile(r"-?\d+")
_TIMEVAL_RE = re.compile(r"(\d{2})(\d{2})?(\d{2})?(?:\.(\d{3}))?")


@dataclass(frozen=True)
class _DateLit:
    dt: datetime  # span start
    span_end: datetime  # exclusive


# ---------------------------------------------------------------- scanning


def _scan_date(s: str, i: int) -> tuple[_DateLit, int]:
    if not re.match(r"\d{8}", s[i:]):
        raise Err(f"malformed date literal at position {i} (8+ digits required)", i)
    ds = s[i : i + 8]
    j = i + 8
    hh = mi = ss = 0
    unit = timedelta(days=1)
    # The T-glue is unconditional (spec §8): a 'T' after 8 digits belongs to the
    # literal, so a malformed time-part is an error — never re-tokenized as a selector.
    if j < len(s) and s[j] == "T":
        if not re.match(r"\d{4}", s[j + 1 :]):
            raise Err(f"malformed time part in date literal at position {i} — expected Thhmm[ss]", i)
        hh, mi = int(s[j + 1 : j + 3]), int(s[j + 3 : j + 5])
        j += 5
        unit = timedelta(minutes=1)
        if re.match(r"\d{2}", s[j:]):
            ss = int(s[j : j + 2])
            j += 2
            unit = timedelta(seconds=1)
    if hh > 23 or mi > 59 or ss > 59:
        raise Err(f"invalid time part in date literal '{s[i:j]}'", i)
    try:
        dt = datetime(int(ds[:4]), int(ds[4:6]), int(ds[6:8]), hh, mi, ss)
    except ValueError:
        raise Err(f"'{ds}' is not a real calendar date", i) from None
    return _DateLit(dt, dt + unit), j


def _make_bounds(a: _DateLit | None, b: _DateLit | None, pos: int) -> Bounds:
    start = a.dt if a else None
    end = b.span_end if b else None
    if start is not None and end is not None and start >= end:
        raise Err("backwards bounds range", pos)
    return Bounds(start, end)


def _scan_cadence_tail(anchor: _DateLit, s: str, j: int) -> tuple[Cadence, int]:
    def num_unit(k: int) -> tuple[int, str, int]:
        m = re.match(r"(\d+)(.)?", s[k:])
        if not m or not m.group(2) or m.group(2) not in _CADENCE_UNITS:
            raise Err("malformed cadence — expected <n><unit> with unit in Y M W D H m", k)
        return int(m.group(1)), m.group(2), k + m.end()

    per_pos = j + 1
    per_n, per_u, j = num_unit(per_pos)
    if per_n == 0:
        raise Err("cadence period must be >= 1", per_pos)
    dur_pos = per_pos
    dur_n, dur_u = 1, per_u  # default: 1 of the period's unit
    if j < len(s) and s[j] == "/":
        dur_pos = j + 1
        dur_n, dur_u, j = num_unit(dur_pos)
        if dur_n == 0:
            raise Err("cadence duration must be >= 1", dur_pos)
    if dur_u in ("Y", "M") and per_u not in ("Y", "M"):
        raise Err("month/year duration unit requires a month/year period", dur_pos)
    if dur_u == per_u:
        ok = dur_n < per_n
    else:
        ok = dur_n * _UNIT_MINUTES[dur_u][0] < per_n * _UNIT_MINUTES[per_u][1]
    if not ok:
        raise Err(
            "cadence duration must be conservatively smaller than the period "
            "(duration x max unit length < period x min unit length)",
            dur_pos,
        )
    return Cadence(anchor.dt, per_n, per_u, dur_n, dur_u), j


def _scan_datelike(s: str, i: int):
    """Parse a component that starts with a digit or '*': bounds or cadence."""
    if s[i] == "*":
        if i + 1 >= len(s) or s[i + 1] != ":":
            raise Err("a bare '*' component is not valid — bounds need a ':' range", i)
        j = i + 2
        if j < len(s) and s[j] == "*":
            raise Err("bounds require at least one date-literal endpoint — an unbounded window is spelled by omitting bounds", i)
        lit, j = _scan_date(s, j)
        return _make_bounds(None, lit, i), j
    lit, j = _scan_date(s, i)
    if j < len(s) and s[j] == "/":
        return _scan_cadence_tail(lit, s, j)
    if j < len(s) and s[j] == ":":
        j += 1
        if j < len(s) and s[j] == "*":
            return _make_bounds(lit, None, i), j + 1
        lit2, j = _scan_date(s, j)
        return _make_bounds(lit, lit2, i), j
    return _make_bounds(lit, lit, i), j


# ---------------------------------------------------------------- selectors


def _parse_int(text: str, pos: int, what: str = "value") -> int:
    if not _INT_RE.fullmatch(text):
        raise Err(f"malformed {what} '{text}'", pos)
    if text.startswith("-") and int(text) == 0:
        # the sign requires a nonzero integer: '-0' is not a value (spec §3)
        raise Err(f"'-0' is not a {what}", pos)
    return int(text)


def _parse_items(designator: str, text: str, pos: int) -> tuple[Item, ...]:
    if text == "":
        raise Err(f"designator '{designator}' without value", pos)
    items: list[Item] = []
    k = pos
    for part in text.split(","):
        if part == "*":
            raise Err("bare '*' in a list — the list is already the whole domain", k)
        if ":" in part:
            a, _, b = part.partition(":")
            if ":" in b:
                raise Err(f"malformed range '{part}'", k)
            start = None if a == "*" else _parse_int(a, k, "range start")
            end = None if b == "*" else _parse_int(b, k + len(a) + 1, "range end")
            items.append(VRange(start, end))
        else:
            items.append(Value(_parse_int(part, k)))
        k += len(part) + 1
    return tuple(items)


def _parse_selector(designator: str, vp: str, pos: int) -> Selector:
    vpos = pos + 1  # the value part starts right after the designator
    if vp == "":
        raise Err(f"designator '{designator}' without value", pos)
    if "!" in vp[1:]:
        raise Err("exclusion '!' is valid only immediately after the designator", vpos + vp.index("!", 1))
    if "." in vp:
        raise Err("'.' is valid only inside T literals", vpos + vp.index("."))
    if vp == "*":
        return Selector(designator, "all", pos=pos)
    if vp[0] == "!":
        rest = vp[1:]
        if "/" in rest:
            raise Err("a component is either an exclusion or carries a stride — never both", vpos + 1 + rest.index("/"))
        if "#" in rest:
            raise Err("ordinal '#' cannot combine with exclusion", vpos + 1 + rest.index("#"))
        return Selector(designator, "exclusion", _parse_items(designator, rest, vpos + 1), pos=pos)
    if "#" in vp:
        if designator != "E":
            raise Err(f"ordinal '#' is valid only on E, not '{designator}'", vpos + vp.index("#"))
        head, _, tail = vp.partition("#")
        if any(c in head for c in ",:*/") or "#" in tail:
            raise Err("ordinal takes a single weekday value and a single ordinal", vpos)
        weekday = _parse_int(head, vpos, "weekday")
        if weekday == 0 or not -7 <= weekday <= 7:
            raise Err(f"weekday {weekday} out of domain (1-7)", vpos)
        if weekday < 0:
            weekday = 8 + weekday
        opos = vpos + len(head) + 1  # past the '#'
        ordinal = _parse_int(tail, opos, "ordinal")
        if ordinal == 0:
            raise Err("ordinal zero", opos)
        if abs(ordinal) > 5:
            raise Err("ordinal out of range (-5..-1, 1..5)", opos)
        return Selector("E", "ordinal", ordinal=(weekday, ordinal), pos=pos)
    if "/" in vp:
        head, *nums = vp.split("/")
        if len(nums) > 2:
            raise Err("too many '/' parts in stride", vpos)
        if "," in head:
            raise Err("stride not allowed on a list", vpos + head.index(","))
        npos = vpos + len(head) + 1  # past the first '/'
        for n in nums:
            if not n.isdigit():
                raise Err("stride interval/duration must be positive integers", npos)
            npos += len(n) + 1
        interval = int(nums[0])
        duration = int(nums[1]) if len(nums) == 2 else 1
        if ":" in head:
            a, _, b = head.partition(":")
            if ":" in b:
                raise Err(f"malformed range '{head}'", vpos)
            if a == "*":
                raise Err("anchorless stride — an explicit range start is required", vpos)
            if a.startswith("-"):
                raise Err("stride start must be non-negative (end-relative anchors shift per parent instance)", vpos)
            start = _parse_int(a, vpos, "stride start")
            end = None if b == "*" else _parse_int(b, vpos + len(a) + 1, "stride end")
        else:
            if head == "*" or head == "":
                raise Err("anchorless stride — an explicit start is required", vpos)
            if head.startswith("-"):
                raise Err("stride start must be non-negative (end-relative anchors shift per parent instance)", vpos)
            start = _parse_int(head, vpos, "stride start")
            end = None
        if interval < 2:
            raise Err("stride interval must be >= 2", vpos + len(head) + 1)
        if not 1 <= duration < interval:
            raise Err("stride duration must be >= 1 and < interval", npos - len(nums[-1]) - 1)
        return Selector(designator, "stride", stride=Stride(start, end, interval, duration), pos=pos)
    return Selector(designator, "list", _parse_items(designator, vp, vpos), pos=pos)


# ------------------------------------------------------------- T selector


def _timeval_us(text: str, pos: int, *, is_range_end: bool) -> int:
    m = _TIMEVAL_RE.fullmatch(text)
    if not m or (m.group(4) and not m.group(3)):
        raise Err(f"malformed time value '{text}'", pos)
    hh = int(m.group(1))
    mm = int(m.group(2) or 0)
    ss = int(m.group(3) or 0)
    ms = int(m.group(4) or 0)
    if ss > 59:
        raise Err("second out of range in time value (leap seconds are not representable)", pos)
    if mm > 59:
        raise Err("minute out of range in time value", pos)
    if hh == 24 and not (is_range_end and text == "2400"):
        raise Err("hour 24 is written exactly '2400', and only as a range end", pos)
    if hh > 24:
        raise Err("hour out of range in time value", pos)
    return ((hh * 60 + mm) * 60 + ss) * 1_000_000 + ms * 1000


def _timeval_unit_us(text: str) -> int:
    if "." in text:
        return 1_000
    return {2: 3_600_000_000, 4: 60_000_000, 6: 1_000_000}[len(text)]


def _parse_time_selector(vp: str, pos: int) -> TimeSel:
    vpos = pos + 1  # the value part starts right after the 'T'
    if vp == "":
        raise Err("designator 'T' without value", pos)
    for ch, what in (("!", "exclusion"), ("*", "'*'"), ("/", "stride"), ("#", "ordinal"), ("-", "negative value")):
        if ch in vp:
            raise Err(f"T takes no {what} — only values, ranges and lists", vpos + vp.index(ch))
    spans: list[tuple[int, int]] = []
    k = vpos
    for part in vp.split(","):
        if ":" in part:
            a, _, b = part.partition(":")
            if ":" in b:
                raise Err(f"malformed time range '{part}'", k)
            sa = _timeval_us(a, k, is_range_end=False)
            sb = _timeval_us(b, k + len(a) + 1, is_range_end=True)
            if sa == sb:  # half-open — equal endpoints cover nothing; typo-shaped, fail loudly (spec §4)
                raise Err(f"T range '{part}' has equal endpoints — half-open, it covers nothing", k)
            if sa < sb:
                spans.append((sa, sb))
            else:  # midnight wrap, within each covered day
                spans.append((0, sb))
                spans.append((sa, MICROS_PER_DAY))
        else:
            start = _timeval_us(part, k, is_range_end=False)
            spans.append((start, start + _timeval_unit_us(part)))
        k += len(part) + 1
    return TimeSel(tuple(spans))


# ----------------------------------------------------------- validation


def _domain(designator: str, scope: str) -> tuple[int, int | None]:
    """(lo, max hi) — hi is None for Y (unbounded)."""
    if designator == "Y":
        return 1, None
    if designator == "D":
        return 1, D_MAX_BY_SCOPE[scope]
    return DOMAIN_LO[designator], DOMAIN_HI[designator]


def _check_value(designator: str, v: int, lo: int, hi: int | None, pos: int | None) -> None:
    if designator == "Y":
        if v < 0:
            raise Err("negative value on Y — no edge to count back from", pos)
        if v == 0 or v > _YEAR_MAX:
            raise Err(f"year {v} out of domain (1-{_YEAR_MAX})", pos)
        return
    assert hi is not None
    if v >= 0:
        if v < lo:
            raise Err(f"value {v} out of domain for '{designator}' ({lo}-{hi})", pos)
        if v > hi:
            raise Err(f"value {v} out of domain for '{designator}' ({lo}-{hi})", pos)
    elif v < -(hi + 1 - lo):
        raise Err(f"value {v} out of domain for '{designator}' (-{hi + 1 - lo}..-1)", pos)


def _validate_selector(sel: Selector) -> None:
    lo, hi = _domain(sel.designator, sel.scope)
    if sel.kind in ("list", "exclusion"):
        for item in sel.items:
            if isinstance(item, Value):
                _check_value(sel.designator, item.v, lo, hi, sel.pos)
            else:
                if item.start is not None:
                    _check_value(sel.designator, item.start, lo, hi, sel.pos)
                if item.end is not None:
                    _check_value(sel.designator, item.end, lo, hi, sel.pos)
                if (
                    sel.designator == "Y"
                    and item.start is not None
                    and item.end is not None
                    and item.start > item.end
                ):
                    raise Err("backwards range on Y — no edge to wrap around", sel.pos)
    elif sel.kind == "stride":
        st = sel.stride
        assert st is not None
        _check_value(sel.designator, st.start, lo, hi, sel.pos)
        if st.end is not None:
            _check_value(sel.designator, st.end, lo, hi, sel.pos)
            if st.end >= lo and st.start > st.end:
                if sel.designator == "Y":
                    raise Err("backwards range on Y — no edge to wrap around", sel.pos)
                raise Err("wrap ranges take no stride", sel.pos)
        if hi is not None and st.interval > hi + 1 - lo:
            raise Err(
                f"stride interval {st.interval} exceeds the parent domain size "
                f"({hi + 1 - lo}) — use a date-anchored cadence",
                sel.pos,
            )


# ------------------------------------------------------------- branches


def _parse_branch(s: str, start: int = 0, end: int | None = None) -> Branch:
    i, n = start, len(s) if end is None else end
    selectors: dict[str, Selector] = {}
    time_sel: TimeSel | None = None
    cadence: Cadence | None = None
    bounds: Bounds | None = None
    while i < n:
        ch = s[i]
        if ch.isspace():
            i += 1
            continue
        if ch == "*" or ch.isdigit():
            comp, j = _scan_datelike(s, i)
            if isinstance(comp, Cadence):
                if cadence is not None:
                    raise Err("at most one cadence per expression", i)
                cadence = comp
            else:
                if bounds is not None:
                    raise Err("at most one bounds component per expression", i)
                bounds = comp
            i = j
        elif ch in _SELECTOR_DESIGNATORS:
            j = i + 1
            while j < n and s[j] in _VALUE_CHARS:
                j += 1
            vp = s[i + 1 : j]
            if ch == "T":
                if time_sel is not None:
                    raise Err("duplicate designator 'T' in one expression", i)
                time_sel = _parse_time_selector(vp, i)
            else:
                if ch in selectors:
                    raise Err(f"duplicate designator '{ch}' in one expression", i)
                selectors[ch] = _parse_selector(ch, vp, i)
            i = j
        else:
            raise Err(f"unexpected character {ch!r} at position {i}", i)
    if not (selectors or time_sel or cadence or bounds):
        raise Err("empty expression", start)

    # Resolve D / E-ordinal scope: nearest of M, Q, Y present, else M (spec §2).
    scope = "M" if "M" in selectors else "Q" if "Q" in selectors else "Y" if "Y" in selectors else "M"
    final: list[Selector] = []
    for designator, sel in selectors.items():
        if designator == "D" or (designator == "E" and sel.kind == "ordinal"):
            sel = replace(sel, scope=scope)
        _validate_selector(sel)
        final.append(sel)
    return Branch(tuple(final), time_sel, cadence, bounds, has_w="W" in selectors)


def parse_branches(text: str) -> tuple[Branch, ...]:
    if text.strip() == "":
        raise Err("empty expression", 0)
    branches = []
    start = 0
    for part in text.split("|"):
        if part.strip() == "":
            raise Err("empty union branch", start)
        branches.append(_parse_branch(text, start, start + len(part)))
        start += len(part) + 1
    return tuple(branches)
