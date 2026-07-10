# dtrexp

Python implementation of **DTRExp** (Date-Time Range & Recurrence Expression), spec draft 2.8 — a compact string expression denoting a possibly-infinite set of time intervals, evaluated by coverage.

This is an independent implementation built solely from the specification documents and the shared conformance vectors. Scope: **parsing/validation and coverage evaluation** (`covers`). Rendering, description, and RRULE export are out of scope.

```python
from datetime import datetime, timezone
import dtrexp

expr = dtrexp.parse("T0900:1800 E1:5")          # business hours, Mon-Fri
expr.covers(datetime(2026, 7, 7, 10, 0, tzinfo=timezone.utc))          # True (UTC)
expr.covers(datetime(2026, 7, 7, 7, 30, tzinfo=timezone.utc), tz="Europe/Berlin")  # True (09:30 local)

dtrexp.parse("D30 M2").warnings                  # statically unsatisfiable -> warning
dtrexp.parse("Y*/3")                             # raises DTRExpSyntaxError (anchorless stride)
dtrexp.validate("Y*/3")                          # never raises -> ValidationResult
```

Errors and warnings both carry a **position**; the 0-based character offset into the source:

- `parse(text)` raises `DTRExpSyntaxError` (a `ValueError`) on invalid input. `error.position` points at the offending character and is appended to the rendered message: `"anchorless stride — an explicit start is required (at 1)"`.
- `validate(text)` never raises. It returns a frozen `ValidationResult` with *valid* `bool`, *errors* (positioned syntax errors; parse failure is the only failure, so at most one) and *warnings*.
- Warnings are `DTRExpWarning` objects: *message* `str` plus *position* (the offset of the offending component, `None` where the AST does not pin one down). `str(warning)` is the message; `Expression.warnings` and `validate().warnings` carry the same content.

- Python 3.11+, stdlib only (`zoneinfo` for IANA time zones).
- Conformance: `uv run pytest` runs the full `vectors.json` suite plus the unit tests.
- Coverage gate: `uv run poe cover` (pytest-cov, 100% line + branch enforced).
- Mutation testing: `uv run poe mutation` (mutmut).
