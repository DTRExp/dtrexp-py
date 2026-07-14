"""Conformance runner over the shared DTRExp test vectors.

The vectors — not the spec prose — are the contract (spec §12):
(a) every ``invalid`` expression must be rejected at parse time,
(b) every ``warnings`` expression must parse while reporting a warning,
(c) every ``quiet`` expression must parse with NO warnings,
(d) every instant in every ``coverage`` group must return the expected
    boolean from ``covers(instant, tz)``.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

import dtrexp
from dtrexp import DTRExpSyntaxError

VECTORS = json.loads((Path(__file__).parent / "vectors.json").read_text())


def _ascii_id(reason: str) -> str:
    """Parametrize IDs must stay ASCII: pytest escapes non-ASCII IDs, and the
    escaped form cannot be re-selected by a second in-process ``pytest.main``
    run in the same interpreter — which is exactly how mutmut drives its
    mutant workers."""
    return reason.replace("—", "-").replace("…", "...")

_coverage_cases = [
    (group["id"], group["expression"], group["tz"], instant, expected)
    for group in VECTORS["coverage"]
    for instant, expected in group["cases"].items()
]


@pytest.mark.parametrize(
    ("gid", "expression", "tz", "instant", "expected"),
    _coverage_cases,
    ids=[f"{gid}--{instant}" for gid, _, _, instant, _ in _coverage_cases],
)
def test_coverage(gid, expression, tz, instant, expected):
    expr = dtrexp.parse(expression)
    assert expr.covers(datetime.fromisoformat(instant), tz=tz) is expected


@pytest.mark.parametrize(
    ("expression", "reason"),
    [(v["expression"], v["reason"]) for v in VECTORS["invalid"]],
    ids=[_ascii_id(v["reason"]) for v in VECTORS["invalid"]],
)
def test_invalid(expression, reason):
    with pytest.raises(DTRExpSyntaxError):
        dtrexp.parse(expression)


@pytest.mark.parametrize(
    ("expression", "warning"),
    [(v["expression"], v["warning"]) for v in VECTORS["warnings"]],
    ids=[v["expression"] for v in VECTORS["warnings"]],
)
def test_warnings(expression, warning):
    expr = dtrexp.parse(expression)  # must parse (accepted) ...
    assert expr.warnings, f"expected a warning for {expression!r}: {warning}"


@pytest.mark.parametrize(
    ("expression", "note"),
    [(v["expression"], v["note"]) for v in VECTORS["quiet"]],
    ids=[v["expression"] for v in VECTORS["quiet"]],
)
def test_quiet(expression, note):
    expr = dtrexp.parse(expression)
    assert not expr.warnings, f"expected NO warning for {expression!r} ({note}), got {expr.warnings}"
