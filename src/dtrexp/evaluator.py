"""Coverage evaluation (spec §9)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .fields import Fields, days_in_month, _zone
from .nodes import Branch, Cadence, Selector, Value


def _resolve(v: int, lo: int, hi: int) -> int:
    """Resolve a possibly-negative value against the actual parent instance."""
    return v if v >= lo else hi + 1 + v


def _field_and_domain(sel: Selector, f: Fields) -> tuple[int, int, int]:
    d = sel.designator
    if d == "Q":
        return f.quarter, 1, 4
    if d == "M":
        return f.month, 1, 12
    if d == "W":
        return f.week, 1, f.wiy
    if d == "D":
        if sel.scope == "Q":
            return f.doq, 1, f.diq
        if sel.scope == "Y":
            return f.doy, 1, f.diy
        return f.day, 1, f.dim
    if d == "E":
        return f.weekday, 1, 7
    if d == "H":
        return f.hour, 0, 23
    if d == "m":
        return f.minute, 0, 59
    return f.second, 0, 59  # 's'


def _item_covers(item, v: int, lo: int, hi: int) -> bool:
    if isinstance(item, Value):
        r = _resolve(item.v, lo, hi)
        return lo <= r <= hi and v == r
    s_raw, e_raw = item.start, item.end
    # Wrap is decided syntactically, on literal non-negative endpoints only
    # (spec §3); s_raw > e_raw >= lo already implies s_raw >= lo.
    if s_raw is not None and e_raw is not None and e_raw >= lo and s_raw > e_raw:
        return v >= s_raw or v <= e_raw
    s = lo if s_raw is None else _resolve(s_raw, lo, hi)
    e = hi if e_raw is None else _resolve(e_raw, lo, hi)
    return s <= v <= e


def _scope_pos(scope: str, f: Fields) -> tuple[int, int]:
    if scope == "Q":
        return f.doq, f.diq
    if scope == "Y":
        return f.doy, f.diy
    return f.day, f.dim


def _year_covers(sel: Selector, v: int) -> bool:
    if sel.kind == "all":
        return True

    def item_ok(item) -> bool:
        if isinstance(item, Value):
            return v == item.v
        lo_ok = item.start is None or v >= item.start
        hi_ok = item.end is None or v <= item.end
        return lo_ok and hi_ok

    if sel.kind == "list":
        return any(item_ok(i) for i in sel.items)
    if sel.kind == "exclusion":
        return not any(item_ok(i) for i in sel.items)
    st = sel.stride
    assert st is not None
    if v < st.start or (st.end is not None and v > st.end):
        return False
    return (v - st.start) % st.interval < st.duration


def _selector_covers(sel: Selector, f: Fields, has_w: bool) -> bool:
    if sel.designator == "Y":
        return _year_covers(sel, f.week_year if has_w else f.year)
    if sel.kind == "ordinal":
        weekday, ordinal = sel.ordinal  # type: ignore[misc]
        if f.weekday != weekday:
            return False
        pos, length = _scope_pos(sel.scope, f)
        if ordinal > 0:
            return (pos - 1) // 7 + 1 == ordinal
        return (length - pos) // 7 + 1 == -ordinal
    v, lo, hi = _field_and_domain(sel, f)
    if sel.kind == "all":
        return True
    if sel.kind == "list":
        return any(_item_covers(i, v, lo, hi) for i in sel.items)
    if sel.kind == "exclusion":
        return not any(_item_covers(i, v, lo, hi) for i in sel.items)
    st = sel.stride
    assert st is not None
    end = hi if st.end is None else _resolve(st.end, lo, hi)
    if not st.start <= v <= end:
        return False
    return (v - st.start) % st.interval < st.duration


# ---------------------------------------------------------------- cadence


def _add_months_constrained(dt: datetime, months: int) -> datetime:
    total = dt.year * 12 + (dt.month - 1) + months
    year, month0 = divmod(total, 12)
    month = month0 + 1
    day = min(dt.day, days_in_month(year, month))
    return dt.replace(year=year, month=month, day=day)


def _window_end(start: datetime, cadence: Cadence) -> datetime:
    n, u = cadence.duration, cadence.duration_unit
    if u == "Y":
        return _add_months_constrained(start, 12 * n)
    if u == "M":
        return _add_months_constrained(start, n)
    if u == "W":
        return start + timedelta(weeks=n)
    if u == "D":
        return start + timedelta(days=n)
    if u == "H":
        return start + timedelta(hours=n)
    return start + timedelta(minutes=n)


def _cadence_covers(c: Cadence, local: datetime, instant: datetime, tz: str) -> bool:
    unit = c.period_unit
    if unit in "Hm":
        # Absolute elapsed time (spec §9.3); anchor resolved with fold=0
        # (earlier occurrence in overlaps; resolves forward through gaps).
        # The grid arithmetic MUST run in UTC: same-zone aware arithmetic in
        # Python is wall-clock, which would drift the grid across DST shifts.
        if instant.tzinfo is None:
            instant = instant.replace(tzinfo=timezone.utc)
        anchor_abs = c.anchor.replace(tzinfo=_zone(tz)).astimezone(timezone.utc)
        period = timedelta(hours=c.period) if unit == "H" else timedelta(minutes=c.period)
        elapsed = instant - anchor_abs
        if elapsed < timedelta(0):
            return False
        # The absolute grid is exact: floor division pins the only candidate
        # occurrence (the duration is strictly shorter than the period, so the
        # previous window always ends at or before this one's start).
        start = anchor_abs + (elapsed // period) * period
        return start <= instant < _window_end(start, c)
    if unit in "DW":
        step_days = c.period * (7 if unit == "W" else 1)
        # k0 is a date-difference floor, so the k0 occurrence may still start
        # later in the day than `local` — probe one occurrence back; k0 + 1
        # always starts on a strictly later date, so it can never cover.
        k0 = (local.date() - c.anchor.date()).days // step_days
        for k in (k0 - 1, k0):
            if k < 0:
                continue
            start = c.anchor + timedelta(days=k * step_days)
            if start <= local < _window_end(start, c):
                return True
        return False
    # Month / year periods: constrained anchor arithmetic (spec §9.2) — each
    # occurrence is computed from the original anchor, never iteratively.
    # k0 floors a month-index difference, so the k0 occurrence may start later
    # in the month than `local` — probe one back; k0 + 1 lands on a strictly
    # later month, so it can never cover.
    step_months = c.period * (12 if unit == "Y" else 1)
    month_diff = (local.year - c.anchor.year) * 12 + (local.month - c.anchor.month)
    k0 = month_diff // step_months
    for k in (k0 - 1, k0):
        if k < 0:
            continue
        start = _add_months_constrained(c.anchor, k * step_months)
        if start <= local < _window_end(start, c):
            return True
    return False


# ---------------------------------------------------------------- branch


def branch_covers(branch: Branch, f: Fields, instant: datetime, tz: str) -> bool:
    b = branch.bounds
    if b is not None:
        if b.start is not None and f.local < b.start:
            return False
        if b.end is not None and f.local >= b.end:
            return False
    if branch.time is not None and not any(
        lo <= f.tod_us < hi for lo, hi in branch.time.spans
    ):
        return False
    for sel in branch.selectors:
        if not _selector_covers(sel, f, branch.has_w):
            return False
    if branch.cadence is not None and not _cadence_covers(branch.cadence, f.local, instant, tz):
        return False
    return True
