<p align="center"><picture><source media="(prefers-color-scheme: dark)" srcset="./.github/logo-dark.svg"><img src="./.github/logo.svg" width="200" alt="dtrexp-py" /></picture></p>

# dtrexp-py

Python implementation of **[DTRExp](https://github.com/DTRExp/dtrexp)** (read: "**DTR Expression**") — a compact string expression for date-time ranges and recurrence, evaluated by **coverage** rather than enumeration.

```
T0900:1800 E1:5          Mon–Fri, 09:00–18:00
E7#-1 M4                 last Sunday of April, every year
20200106/10D             every 10 days from 2020-01-06 (cron can't say this)
M!7                      every month except July
```

Scope: **parsing, validation and coverage evaluation** — the spec's core interface. Rendering, description and RRULE export are out of scope; the [reference implementation][js] has them.

## Install

```sh
pip install dtrexp
```

Python 3.11+, stdlib only (`zoneinfo` for IANA zones).

## Usage

```python
from datetime import datetime, timezone
import dtrexp

dtr = dtrexp.parse("T0900:1800 E1:5")   # business hours, Mon–Fri

dtr.covers(datetime(2026, 7, 7, 10, 0, tzinfo=timezone.utc))
# —> True (UTC — the default)
dtr.covers(datetime(2026, 7, 7, 7, 30, tzinfo=timezone.utc), tz="Europe/Berlin")
# —> True (09:30 Berlin local time)
```

Note that you parse **once** (at write/config time) and evaluate **many**; `Expression` objects are immutable, and `covers` is a single calendar-field extraction followed by integer comparisons (no occurrence iteration). The zone is an evaluation parameter, never part of the expression. `covers` also accepts an ISO 8601 string for the instant.

## Errors and Warnings

Both carry a **position**; the 0-based character offset into the source:

```python
dtrexp.parse("Y*/3")
# raises DTRExpSyntaxError: "anchorless stride — an explicit start is required (at 1)"
# error.position —> 1

res = dtrexp.validate("D30 M2")   # never raises
res.valid                         # True — it parses
res.warnings                      # (DTRExpWarning(message="unsatisfiable …", position=0),)
```

- `parse(text)` raises `DTRExpSyntaxError` (a `ValueError`) on invalid input; *position* points at the offending character and is appended to the rendered message.
- `validate(text)` never raises; typo-shaped input comes back as data. Returns a frozen `ValidationResult` with *valid* `bool`, *errors* (parsing stops at the first syntax error, so at most one) and *warnings*.
- Warnings are the spec's [§9.1](https://github.com/DTRExp/dtrexp/blob/main/spec.md#91-the-existence-rule) unsatisfiability lint: expressions that parse but can never match. They are `DTRExpWarning` objects (*message* `str`, *position* `int | None`); `str(warning)` is the message. `Expression.warnings` and `validate(text).warnings` carry the same content.

## Conformance & Quality

- The test suite is driven by the shared [`vectors.json`][vectors] from the spec repo (draft 2.8): every coverage, rejection, warning and quiet vector, including the calendar traps (Feb 29 across 2000/2024/**2100**, `W53` existence, DST gap/overlap in `Europe/Berlin`). See [VECTORS.md][vectors-md] for how the suite works.
- 100% line + branch coverage, enforced (`uv run poe cover`); mutation-tested with mutmut (`uv run poe mutation`).
- Zero dependencies.

## Related Projects

- [**dtrexp** (spec)][spec]: the DTRExp specification (grammar, semantics, conformance vectors) this package implements.
- [**dtrexp-js**][js]: the reference implementation; adds `intersect`, `next`, `describe`, `toRRule` and canonicalization.
- [**dtrexp-go**][go] · [**dtrexp-swift**][swift] · [**dtrexp-rs**][rs] · [**dtrexp-java**][java]: the other ports; same core interface.

## License

© 2026, Onur Yıldırım. [**MIT**](LICENSE) License.

[spec]: https://github.com/DTRExp/dtrexp
[js]: https://github.com/DTRExp/dtrexp-js
[go]: https://github.com/DTRExp/dtrexp-go
[swift]: https://github.com/DTRExp/dtrexp-swift
[rs]: https://github.com/DTRExp/dtrexp-rs
[java]: https://github.com/DTRExp/dtrexp-java
[vectors]: https://github.com/DTRExp/dtrexp/blob/main/vectors.json
[vectors-md]: https://github.com/DTRExp/dtrexp/blob/main/VECTORS.md
