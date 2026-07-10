"""dtrexp — DTRExp (Date-Time Range & Recurrence Expression) draft 2.8.

Parsing/validation and coverage evaluation only::

    import dtrexp
    expr = dtrexp.parse("T0900:1800 E1:5")
    expr.warnings                                   # () — spec §9.1 warnings, if any
    expr.covers(datetime(2026, 7, 7, 10, tzinfo=timezone.utc), tz="Europe/Berlin")
    dtrexp.validate("T0900:1800 E1:5")              # never raises — a ValidationResult
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .checks import static_warnings
from .errors import DTRExpSyntaxError, DTRExpWarning
from .evaluator import branch_covers
from .fields import compute_fields
from .nodes import Branch
from .parser import parse_branches

__all__ = ["parse", "validate", "Expression", "ValidationResult", "DTRExpSyntaxError", "DTRExpWarning"]


class Expression:
    """A parsed DTRExp: a union of one or more intersected-component branches."""

    __slots__ = ("text", "branches", "warnings")

    def __init__(self, text: str, branches: tuple[Branch, ...], warnings: tuple[DTRExpWarning, ...]):
        self.text = text
        self.branches = branches
        self.warnings = warnings

    def covers(self, instant: datetime | str, tz: str = "UTC") -> bool:
        """Is the absolute ``instant`` inside the covered set, evaluated in ``tz``?"""
        if isinstance(instant, str):
            instant = datetime.fromisoformat(instant)
        fields = compute_fields(instant, tz)
        return any(branch_covers(b, fields, instant, tz) for b in self.branches)

    def __repr__(self) -> str:
        return f"Expression({self.text!r})"


@dataclass(frozen=True)
class ValidationResult:
    """Result of :func:`validate` — the non-raising counterpart of :func:`parse`."""

    valid: bool
    errors: tuple[DTRExpSyntaxError, ...]
    warnings: tuple[DTRExpWarning, ...]


def parse(text: str) -> Expression:
    """Parse a DTRExp, raising :class:`DTRExpSyntaxError` on invalid input.

    Statically unsatisfiable (but syntactically valid) expressions parse
    successfully and carry entries in :attr:`Expression.warnings`.
    """
    if not isinstance(text, str):
        raise DTRExpSyntaxError("expression must be a string")
    branches = parse_branches(text)
    warnings: list[DTRExpWarning] = []
    for branch in branches:
        warnings.extend(static_warnings(branch))
    return Expression(text, branches, tuple(warnings))


def validate(text: str) -> ValidationResult:
    """Check a DTRExp without raising — errors and warnings come back as data.

    Parse failure is the only failure, so ``errors`` carries at most one
    entry. ``warnings`` is the same content as :attr:`Expression.warnings`.
    """
    try:
        expr = parse(text)
    except DTRExpSyntaxError as err:
        return ValidationResult(False, (err,), ())
    return ValidationResult(True, (), expr.warnings)
