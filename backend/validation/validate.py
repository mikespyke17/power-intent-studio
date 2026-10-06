"""Milestone 3, cross-cutting: turns a MappedDesign's raw resolution
results into a ValidationReport.

Severity mapping (approved design, see docs/milestone3_mapping.md's
"backend/validation/" section for the full writeup):

- DANGLING -> ERROR, one finding per reference. The core structural check
  this milestone exists for: a named reference that resolves against
  nothing.
- UNRESOLVABLE_BLACK_BOX -> WARNING, one finding per reference. A real
  resolution failure, but not certain to be wrong the way DANGLING is --
  might be a real pin the .lib file just didn't declare.
- NOT_ATTEMPTED -> EXCLUDED, deduplicated into one finding per
  (construct_kind, field) rather than one per instance -- a design with
  many power states would otherwise produce one coverage-note finding per
  state, burying real ERROR/WARNING findings under an entirely
  tool-coverage concern, not a design defect.
- Every other kind (INSTANCE/PORT/NET/BLACK_BOX_PORT/POWER_INTENT/VIRTUAL)
  -> no finding. Resolved successfully or correctly classified as
  non-RTL; emitting a finding per success would be noise, not signal.
"""

from __future__ import annotations

from collections import defaultdict

from backend.mapping import MappedDesign, ResolutionKind, ResolvedReference
from backend.validation.schema import Finding, Severity, ValidationReport


def _location_str(reference: ResolvedReference) -> str:
    loc = reference.location
    field = loc.field if loc.index is None else f"{loc.field}[{loc.index}]"
    return f"{loc.construct_kind}::{loc.construct_id}.{field}"


def validate(mapped: MappedDesign) -> ValidationReport:
    findings: list[Finding] = []
    not_attempted_groups: dict[tuple[str, str], list[ResolvedReference]] = defaultdict(list)

    for ref in mapped.references:
        if ref.kind == ResolutionKind.DANGLING:
            findings.append(
                Finding(
                    severity=Severity.ERROR,
                    summary=f"{_location_str(ref)}: {ref.detail}",
                    reference=ref,
                )
            )
        elif ref.kind == ResolutionKind.UNRESOLVABLE_BLACK_BOX:
            findings.append(
                Finding(
                    severity=Severity.WARNING,
                    summary=f"{_location_str(ref)}: {ref.detail}",
                    reference=ref,
                )
            )
        elif ref.kind == ResolutionKind.NOT_ATTEMPTED:
            not_attempted_groups[(ref.location.construct_kind, ref.location.field)].append(ref)

    for (construct_kind, field), refs in sorted(not_attempted_groups.items()):
        findings.append(
            Finding(
                severity=Severity.EXCLUDED,
                summary=f"{construct_kind}.{field}: {len(refs)} reference(s) not checked ({refs[0].detail})",
                reference=None,
                count=len(refs),
            )
        )

    return ValidationReport(findings=findings)
