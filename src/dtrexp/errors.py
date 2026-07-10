"""Error and warning types for dtrexp."""

from __future__ import annotations

from dataclasses import dataclass


class DTRExpSyntaxError(ValueError):
    """Raised when an expression fails to parse or violates a static validity rule.

    ``position`` is the 0-based character offset into the source where the
    error was detected; ``None`` only when there is no source to point into
    (a non-string input).
    """

    def __init__(self, message: str, position: int | None = None):
        super().__init__(message)
        self.position = position

    def __str__(self) -> str:
        message: str = self.args[0]
        return message if self.position is None else f"{message} (at {self.position})"


@dataclass(frozen=True)
class DTRExpWarning:
    """A spec §9.1 unsatisfiability warning.

    ``position`` is the 0-based offset of the offending component in the
    source; ``None`` where no single component is derivable from the AST.
    """

    message: str
    position: int | None = None

    def __str__(self) -> str:
        return self.message
