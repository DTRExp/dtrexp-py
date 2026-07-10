"""AST nodes for parsed DTRExp expressions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

#: Designators of unit selectors (excludes the special time-of-day selector T).
UNIT_DESIGNATORS = "YQMWDEHms"

#: Lower domain edge per designator (H/m/s are 0-based, the rest 1-based).
DOMAIN_LO = {"Y": 1, "Q": 1, "M": 1, "W": 1, "D": 1, "E": 1, "H": 0, "m": 0, "s": 0}

#: Maximum upper domain edge per fixed-domain designator.
DOMAIN_HI = {"Q": 4, "M": 12, "W": 53, "E": 7, "H": 23, "m": 59, "s": 59}

#: Maximum day-domain size for D by scope.
D_MAX_BY_SCOPE = {"M": 31, "Q": 92, "Y": 366}

MICROS_PER_DAY = 86_400_000_000


@dataclass(frozen=True)
class Value:
    """A single (possibly negative) integer value."""

    v: int


@dataclass(frozen=True)
class VRange:
    """A range item; ``None`` endpoint means ``*`` (the domain edge on that side)."""

    start: int | None
    end: int | None


Item = Value | VRange


@dataclass(frozen=True)
class Stride:
    """``start[:end]/interval[/duration]`` — ``end`` of ``None`` means the domain edge."""

    start: int
    end: int | None
    interval: int
    duration: int


@dataclass(frozen=True)
class Selector:
    """A unit selector component (any designator except T)."""

    designator: str
    kind: str  # 'all' | 'list' | 'exclusion' | 'stride' | 'ordinal'
    items: tuple[Item, ...] = ()
    stride: Stride | None = None
    ordinal: tuple[int, int] | None = None  # (weekday 1..7, ordinal -5..-1|1..5)
    scope: str = ""  # for D, and for E-with-ordinal: 'M' | 'Q' | 'Y'
    pos: int | None = None  # 0-based offset of the designator in the source


@dataclass(frozen=True)
class TimeSel:
    """Time-of-day selector: half-open microsecond spans within each covered day."""

    spans: tuple[tuple[int, int], ...]


@dataclass(frozen=True)
class Cadence:
    """``<date>/<n><unit>[/<n><unit>]`` — anchor-based recurrence."""

    anchor: datetime  # naive local
    period: int
    period_unit: str  # Y M W D H m
    duration: int
    duration_unit: str


@dataclass(frozen=True)
class Bounds:
    """Absolute window; ``start`` inclusive, ``end`` exclusive; ``None`` = open."""

    start: datetime | None
    end: datetime | None


@dataclass(frozen=True)
class Branch:
    """One expression of a ``|``-union: intersected components."""

    selectors: tuple[Selector, ...]
    time: TimeSel | None
    cadence: Cadence | None
    bounds: Bounds | None
    has_w: bool
