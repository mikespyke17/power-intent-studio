"""Validation report types (Milestone 3, cross-cutting per CLAUDE.md's
repo layout).

Pure data -- no rule logic (that's validate.py). Deliberately a findings
list, not a pass/fail gate: what severity blocks what (export? GUI save?
nothing at all?) is a decision for whatever consumes a ValidationReport
(GUI, CLI, eventually backend/api/), not something this module bakes in.
See docs/product_workflow.md's "not signoff-grade verification" section --
`has_errors` is offered as a convenience, not the primary interface.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from backend.mapping import ResolvedReference


class Severity(Enum):
    ERROR = "error"  # a real structural defect: DANGLING
    WARNING = "warning"  # a real resolution failure, but not certain to be wrong: UNRESOLVABLE_BLACK_BOX
    EXCLUDED = "excluded"  # not a defect -- a coverage gap in the tool itself: NOT_ATTEMPTED


@dataclass(frozen=True)
class Finding:
    severity: Severity
    summary: str
    # Set for a single-reference finding (DANGLING/UNRESOLVABLE_BLACK_BOX).
    # None for a deduplicated EXCLUDED coverage-note finding, which speaks
    # for `count` references at once rather than any one of them.
    reference: Optional[ResolvedReference] = None
    count: int = 1


@dataclass(frozen=True)
class ValidationReport:
    findings: list[Finding]

    @property
    def has_errors(self) -> bool:
        return any(f.severity == Severity.ERROR for f in self.findings)
