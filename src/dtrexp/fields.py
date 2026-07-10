"""Calendar-field extraction: one extraction per covers() call (spec §9 step 1)."""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, datetime, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo


def days_in_month(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def days_in_year(year: int) -> int:
    return 366 if calendar.isleap(year) else 365


def days_in_quarter(year: int, quarter: int) -> int:
    first = 3 * (quarter - 1) + 1
    return sum(days_in_month(year, m) for m in (first, first + 1, first + 2))


def weeks_in_week_year(week_year: int) -> int:
    """52 or 53 — ISO week of Dec 28, which always lies in the last week."""
    return date(week_year, 12, 28).isocalendar().week


@lru_cache(maxsize=None)
def _zone(tz: str):
    return timezone.utc if tz == "UTC" else ZoneInfo(tz)


@dataclass(frozen=True)
class Fields:
    local: datetime  # naive wall-clock datetime in the evaluation zone
    year: int
    quarter: int
    month: int
    day: int
    hour: int
    minute: int
    second: int
    doq: int  # day of quarter (1-based)
    doy: int  # day of year (1-based)
    week_year: int
    week: int
    weekday: int  # ISO: 1 = Monday .. 7 = Sunday
    dim: int  # days in month
    diq: int  # days in quarter
    diy: int  # days in year
    wiy: int  # weeks in ISO week-year
    tod_us: int  # time of day in microseconds


def compute_fields(instant: datetime, tz: str) -> Fields:
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    local = instant.astimezone(_zone(tz)).replace(tzinfo=None)
    d = local.date()
    iso = d.isocalendar()
    quarter = (local.month - 1) // 3 + 1
    doy = d.timetuple().tm_yday
    doq = (d - date(local.year, 3 * (quarter - 1) + 1, 1)).days + 1
    tod_us = (
        ((local.hour * 60 + local.minute) * 60 + local.second) * 1_000_000
        + local.microsecond
    )
    return Fields(
        local=local,
        year=local.year,
        quarter=quarter,
        month=local.month,
        day=local.day,
        hour=local.hour,
        minute=local.minute,
        second=local.second,
        doq=doq,
        doy=doy,
        week_year=iso.year,
        week=iso.week,
        weekday=iso.weekday,
        dim=days_in_month(local.year, local.month),
        diq=days_in_quarter(local.year, quarter),
        diy=days_in_year(local.year),
        wiy=weeks_in_week_year(iso.year),
        tod_us=tod_us,
    )
