"""Canned mock MappedDesign/ValidationReport, and the diagnostic adapters
for all three phases (Load Design, Load Power Intent, Mapping).

MappedDesign here is hand-authored to match what a real resolver would
produce for mock_rtl's Design + mock_power_intent's PowerIntentDesign --
not computed by re-implementing backend/mapping/'s actual resolution
algorithm. That would be over-engineering for fake data and risks the
mock quietly diverging from the real algorithm's edge-case behavior;
since it's fake, it should just declare its answers directly, the same
way mock_rtl.py is literal canned data rather than a fake parser.

The severity-mapping + NOT_ATTEMPTED-dedup transformation in
_findings_from_references(), however, IS worth mirroring behaviorally
(not just shape): it's the one simple, already-fully-specified piece of
backend/validation/validate.py's logic, confined entirely to this mock
layer, and exercising it now means the diagram overlay and diagnostics
dock are validated against realistic Finding data from day one.
"""

from __future__ import annotations

from collections import defaultdict

from frontend.src.shared.models.diagnostics import Diagnostic
from frontend.src.shared.models.mapping import MappedDesign, ReferenceLocation, ResolutionKind, ResolvedReference
from frontend.src.shared.models.power_intent import PowerIntentDesign
from frontend.src.shared.models.rtl import Design
from frontend.src.shared.models.validation import Finding, Severity, ValidationReport


def build_mock_mapped_design(power_intent: PowerIntentDesign, rtl: Design) -> MappedDesign:
    def ref(raw, construct_kind, construct_id, field, kind, **extra):
        return ResolvedReference(raw=raw, location=ReferenceLocation(construct_kind, construct_id, field), kind=kind, **extra)

    references = [
        ref("u_core", "PowerDomain", "::PD_CORE", "elements", ResolutionKind.INSTANCE, resolved_path="top/u_core", resolved_definition_id="core"),
        ref("SS1", "PowerDomain", "::PD_CORE", "primary_supply", ResolutionKind.NOT_ATTEMPTED, detail="no independently verified target dict for this field yet"),
        ref("u_nonexistent", "PowerDomain", "::PD_EMPTY", "elements", ResolutionKind.DANGLING, detail="'u_nonexistent': no instance at that path"),
        ref("sleep_n", "PowerSwitch", "::SW_CORE", "control_ports", ResolutionKind.PORT, resolved_path="top/sleep_n", resolved_definition_id="top"),
        ref("PD_CORE", "PowerSwitch", "::SW_CORE", "domain", ResolutionKind.POWER_INTENT, resolved_power_intent_id="::PD_CORE"),
        ref("VDD", "PowerSwitch", "::SW_CORE", "input_supply", ResolutionKind.POWER_INTENT, resolved_power_intent_id="::VDD"),
        ref("VDD_CORE", "PowerSwitch", "::SW_CORE", "output_supply", ResolutionKind.POWER_INTENT, resolved_power_intent_id="::VDD_CORE"),
        ref("u_core/out1", "IsolationStrategy", "::ISO1", "elements", ResolutionKind.PORT, resolved_path="top/u_core/out1", resolved_definition_id="core"),
        ref("PD_CORE", "IsolationStrategy", "::ISO1", "domain", ResolutionKind.POWER_INTENT, resolved_power_intent_id="::PD_CORE"),
        ref("u_core/out2", "LevelShifterStrategy", "::LS1", "elements", ResolutionKind.PORT, resolved_path="top/u_core/out2", resolved_definition_id="core"),
        ref("PD_CORE", "LevelShifterStrategy", "::LS1", "domain", ResolutionKind.POWER_INTENT, resolved_power_intent_id="::PD_CORE"),
        ref("u_core", "RetentionStrategy", "::RET1", "elements", ResolutionKind.INSTANCE, resolved_path="top/u_core", resolved_definition_id="core"),
        ref("PD_CORE", "RetentionStrategy", "::RET1", "domain", ResolutionKind.POWER_INTENT, resolved_power_intent_id="::PD_CORE"),
        ref("PD_CORE", "PowerState", "::SS1.ON", "domain", ResolutionKind.POWER_INTENT, resolved_power_intent_id="::PD_CORE"),
        ref("SS1", "PowerState", "::SS1.ON", "applies_to", ResolutionKind.NOT_ATTEMPTED, detail="no independently verified target dict for this field yet"),
        ref("SS1", "PowerState", "::SS1.OFF", "applies_to", ResolutionKind.NOT_ATTEMPTED, detail="no independently verified target dict for this field yet"),
        ref("SS1", "PowerStateGroup", "::PSG1", "members", ResolutionKind.NOT_ATTEMPTED, detail="no independently verified target dict for this field yet"),
        ref("PD_CORE", "DomainSupplyConnection", "[0]", "domain", ResolutionKind.POWER_INTENT, resolved_power_intent_id="::PD_CORE"),
        ref("VDD", "DomainSupplyConnection", "[0]", "supply_net", ResolutionKind.POWER_INTENT, resolved_power_intent_id="::VDD"),
        ref("PD_CORE", "DomainSupplyConnection", "[1]", "domain", ResolutionKind.POWER_INTENT, resolved_power_intent_id="::PD_CORE"),
        ref("VSS", "DomainSupplyConnection", "[1]", "supply_net", ResolutionKind.POWER_INTENT, resolved_power_intent_id="::VSS"),
    ]
    return MappedDesign(power_intent=power_intent, rtl=rtl, references=references)


def _findings_from_references(references: list[ResolvedReference]) -> list[Finding]:
    findings: list[Finding] = []
    not_attempted_groups: dict[tuple[str, str], list[ResolvedReference]] = defaultdict(list)

    for r in references:
        if r.kind == ResolutionKind.DANGLING:
            loc = r.location
            findings.append(Finding(severity=Severity.ERROR, summary=f"{loc.construct_kind}::{loc.construct_id}.{loc.field}: {r.detail}", reference=r))
        elif r.kind == ResolutionKind.UNRESOLVABLE_BLACK_BOX:
            loc = r.location
            findings.append(Finding(severity=Severity.WARNING, summary=f"{loc.construct_kind}::{loc.construct_id}.{loc.field}: {r.detail}", reference=r))
        elif r.kind == ResolutionKind.NOT_ATTEMPTED:
            not_attempted_groups[(r.location.construct_kind, r.location.field)].append(r)

    for (construct_kind, field), refs in sorted(not_attempted_groups.items()):
        findings.append(
            Finding(severity=Severity.EXCLUDED, summary=f"{construct_kind}.{field}: {len(refs)} reference(s) not checked ({refs[0].detail})", count=len(refs))
        )
    return findings


def build_mock_validation_report(mapped: MappedDesign) -> ValidationReport:
    return ValidationReport(findings=_findings_from_references(mapped.references))


_SEVERITY_TO_CODE_PREFIX = {
    Severity.ERROR: "MAP-ERR",
    Severity.WARNING: "MAP-WARN",
    Severity.EXCLUDED: "MAP-EXCLUDED",
}


def finding_to_diagnostic(finding: Finding) -> Diagnostic:
    """Mapping/validation findings never carry a SourceLoc (ResolvedReference
    has none -- location is expressed as a construct/field, not a file
    position), so file and line are always None here -- not "usually
    None," structurally absent for this source."""
    kind_suffix = f"-{finding.reference.kind.name}" if finding.reference is not None else ""
    return Diagnostic(
        severity=finding.severity,
        code=f"{_SEVERITY_TO_CODE_PREFIX[finding.severity]}{kind_suffix}",
        message=finding.summary,
        file=None,
        line=None,
        source="mapping",
    )


def build_mock_rtl_diagnostics() -> list[Diagnostic]:
    """Canned RTL-phase diagnostics. RTL SourceLoc has both file and line
    always available whenever a SourceLoc exists at all -- both rows here
    are fully populated, matching that real shape (unlike UPF/CPF below,
    there's no real "missing" case for RTL under file+line)."""
    return [
        Diagnostic(severity=Severity.WARNING, code="RTL-UNUSED-NET", message="net 'internal_n1' in 'core' is never read", file="core.v", line=12, source="load_design"),
        Diagnostic(severity=Severity.WARNING, code="RTL-IMPLICIT-WIDTH", message="port 'sleep_n' has no declared width, defaulting to 1 bit", file="top.v", line=3, source="load_design"),
    ]


def build_mock_power_intent_diagnostics() -> list[Diagnostic]:
    """Canned UPF/CPF-phase diagnostics. Power-intent SourceLoc's `file`
    is required whenever a SourceLoc exists, but `line` is optional --
    both rows here keep `file` populated, only the second omits `line`."""
    return [
        Diagnostic(severity=Severity.WARNING, code="UPF-LEGACY-PST", message="legacy create_pst/add_pst_state translated to the canonical power-state shape", file="soc_top.upf", line=8, source="load_power_intent"),
        Diagnostic(severity=Severity.WARNING, code="UPF-NO-LINE", message="command executed via a synthesized/dynamic scope -- no source line available", file="soc_top.upf", line=None, source="load_power_intent"),
    ]
