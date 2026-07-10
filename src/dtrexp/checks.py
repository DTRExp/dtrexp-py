"""Static unsatisfiability warnings (spec §9.1 — SHOULD-flag, not syntax errors)."""

from __future__ import annotations

import calendar

from .errors import DTRExpWarning
from .fields import weeks_in_week_year
from .nodes import Branch, Selector, Value, VRange

_MONTH_DAYS = {1: {31}, 2: {28, 29}, 3: {31}, 4: {30}, 5: {31}, 6: {30},
               7: {31}, 8: {31}, 9: {30}, 10: {31}, 11: {30}, 12: {31}}
_QUARTER_DAYS = {1: {90, 91}, 2: {91}, 3: {92}, 4: {92}}


def _resolve(v: int, lo: int, hi: int) -> int:
    return v if v >= lo else hi + 1 + v


def _item_set(item, lo: int, hi: int) -> set[int]:
    domain = range(lo, hi + 1)
    if isinstance(item, Value):
        r = _resolve(item.v, lo, hi)
        return {r} if lo <= r <= hi else set()
    s_raw, e_raw = item.start, item.end
    # Wrap needs literal endpoints; s_raw > e_raw >= lo already implies s_raw >= lo.
    if s_raw is not None and e_raw is not None and e_raw >= lo and s_raw > e_raw:
        return {v for v in domain if v >= s_raw or v <= e_raw}
    s = lo if s_raw is None else _resolve(s_raw, lo, hi)
    e = hi if e_raw is None else _resolve(e_raw, lo, hi)
    return set(range(max(s, lo), min(e, hi) + 1))


def _enum_selector(sel: Selector | None, lo: int, hi: int) -> set[int] | None:
    """Statically resolve a fixed-domain selector to its value set (None = unknown)."""
    if sel is None:
        return None
    if sel.kind == "all":
        return set(range(lo, hi + 1))
    if sel.kind == "list":
        return set().union(*(_item_set(i, lo, hi) for i in sel.items))
    if sel.kind == "exclusion":
        return set(range(lo, hi + 1)) - set().union(*(_item_set(i, lo, hi) for i in sel.items))
    if sel.kind == "stride":
        st = sel.stride
        assert st is not None
        end = hi if st.end is None else _resolve(st.end, lo, hi)
        return {v for v in range(st.start, min(end, hi) + 1)
                if v >= lo and (v - st.start) % st.interval < st.duration}
    return None  # ordinal


def _enum_years(sel: Selector | None) -> set[int] | None:
    """Finite year set of a Y selector, if statically enumerable."""
    if sel is None or sel.kind not in ("list", "stride"):
        return None
    if sel.kind == "stride":
        st = sel.stride
        assert st is not None
        if st.end is None:
            return None
        return {y for y in range(st.start, st.end + 1)
                if (y - st.start) % st.interval < st.duration}
    years: set[int] = set()
    for item in sel.items:
        if isinstance(item, Value):
            years.add(item.v)
        else:
            if item.start is None or item.end is None:
                return None
            years.update(range(item.start, item.end + 1))
    return years


def _possible_domain_sizes(sel: Selector, by_desig: dict[str, Selector]) -> tuple[int, ...] | None:
    d = sel.designator
    if d == "Y":
        return None
    if d in ("Q", "M", "E", "H", "m", "s"):
        return {"Q": (4,), "M": (12,), "E": (7,), "H": (23,), "m": (59,), "s": (59,)}[d]
    if d == "W":
        years = _enum_years(by_desig.get("Y"))
        if years:
            return tuple(sorted({weeks_in_week_year(y) for y in years}))
        return (52, 53)
    # D — by resolved scope
    if sel.scope == "M":
        months = _enum_selector(by_desig.get("M"), 1, 12) or set(_MONTH_DAYS)
        return tuple(sorted(set().union(*(_MONTH_DAYS[m] for m in months)))) if months else None
    if sel.scope == "Q":
        quarters = _enum_selector(by_desig.get("Q"), 1, 4) or set(_QUARTER_DAYS)
        return tuple(sorted(set().union(*(_QUARTER_DAYS[q] for q in quarters)))) if quarters else None
    # with W present, Y is the week-year while day-of-year stays calendar
    # (spec §2) — cross-selector territory, stays quiet (spec §9.1)
    if "W" not in by_desig:
        years = _enum_years(by_desig.get("Y"))
        if years:
            return tuple(sorted({366 if calendar.isleap(y) else 365 for y in years}))
    return (365, 366)


def _satisfiable(sel: Selector, lo: int, hi: int) -> bool:
    if sel.kind in ("all", "ordinal"):
        return True
    if sel.kind == "stride":
        st = sel.stride
        assert st is not None
        end = hi if st.end is None else _resolve(st.end, lo, hi)
        return lo <= st.start <= min(end, hi)
    matched = set().union(*(_item_set(i, lo, hi) for i in sel.items))
    if sel.kind == "list":
        return bool(matched)
    return len(matched) < hi + 1 - lo  # exclusion: something must survive


def static_warnings(branch: Branch) -> list[DTRExpWarning]:
    warnings: list[DTRExpWarning] = []
    by_desig = {s.designator: s for s in branch.selectors}

    # M and Q are both absolute within the year — their intersection is static.
    if "M" in by_desig and "Q" in by_desig:
        months = _enum_selector(by_desig["M"], 1, 12)
        quarters = _enum_selector(by_desig["Q"], 1, 4)
        if months is not None and quarters is not None:
            q_months = {m for q in quarters for m in range(3 * q - 2, 3 * q + 1)}
            if not months & q_months:
                warnings.append(DTRExpWarning(
                    "statically unsatisfiable: the selected months never fall in the selected quarters",
                    by_desig["M"].pos,
                ))

    for sel in branch.selectors:
        sizes = _possible_domain_sizes(sel, by_desig)
        if not sizes:
            continue
        lo = 0 if sel.designator in "Hms" else 1
        if not any(_satisfiable(sel, lo, hi) for hi in sizes):
            warnings.append(DTRExpWarning(
                f"statically unsatisfiable: the '{sel.designator}' component can never match "
                "in any parent instance selected by this expression",
                sel.pos,
            ))
    return warnings
