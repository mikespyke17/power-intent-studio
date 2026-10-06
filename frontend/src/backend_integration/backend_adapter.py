"""Converts real backend.api dataclasses into the frontend's own mirrored
shared/models/ shapes, so the diagram/tree/properties widgets (built and
tested entirely against mock data) need zero changes to work with real
data.

**This is the one place in the entire frontend that imports from
backend/** -- and only from backend.api, the sanctioned seam, never
backend internals directly. Everywhere else, frontend/ stays exactly as
independent as CLAUDE.md's milestone-independence rule requires. This
module is the real-backend wiring `frontend/src/app/main.py`'s own
`main()` uses by default (see backend/api/'s module docstring and
docs/backend_integration.md) -- not the fully-designed Final Milestone
integration described in CLAUDE.md (no progress reporting, no format
auto-detection, no mixed-language RTL -- see that doc for the full,
still-current list of scope cuts), but the app's real, single,
standard entry point rather than a separate prototype. The conversion
here is a mechanical field-by-field copy for whatever fields exist on
*both* the real and mirrored shapes.
shared/models/ was authored as a trimmed subset of the real schemas (only
the fields the mock-data widgets were ever built to read), not an
exhaustive mirror -- fields the mirror doesn't carry are silently
dropped, not an error. The two known, accepted losses worth naming
explicitly (not just "some fields," these are real, resolvable data):
`IsolationStrategy.exclude_elements` and `LevelShifterStrategy.
exclude_elements` are real RTL-hierarchy fields `backend/mapping/`
resolves, but `shared/models/power_intent.py`'s mirrors don't have a
field to hold the result, so those specific resolved references are
computed by the real pipeline and then discarded here. Resolving this
generally for every future consumer is explicitly out of scope for this
pass, per its own approval.
"""

from __future__ import annotations

import dataclasses
import enum

from typing import Optional

import backend.api as real
import frontend.src.shared.models as mock
from frontend.src.shared.models.diagnostics import Diagnostic
from frontend.src.shared.models.rtl import SourceLoc as MockRtlSourceLoc
from frontend.src.shared.models.power_intent import SourceLoc as MockPowerIntentSourceLoc
from frontend.src.shared.models.validation import Severity

# --- RTL ---------------------------------------------------------------


def _adapt_rtl_source_loc(loc) -> Optional[MockRtlSourceLoc]:
    if loc is None:
        return None
    return MockRtlSourceLoc(file=loc.file, line=loc.line, column=loc.column)


def _adapt_port(port) -> mock.Port:
    return mock.Port(
        name=port.name,
        direction=mock.PortDirection[port.direction.name],
        width=port.width,
        voltage=port.voltage,
        related_power_pin=port.related_power_pin,
        related_ground_pin=port.related_ground_pin,
    )


def _adapt_net(net) -> mock.Net:
    return mock.Net(name=net.name, width=net.width)


def _adapt_port_connection(conn) -> mock.PortConnection:
    return mock.PortConnection(instance_port=conn.instance_port, connected_to=conn.connected_to)


def _adapt_instance(instance) -> mock.Instance:
    return mock.Instance(
        name=instance.name,
        definition_id=instance.definition_id,
        connections=[_adapt_port_connection(c) for c in instance.connections],
        source_loc=_adapt_rtl_source_loc(instance.source_loc),
    )


def _adapt_continuous_assign(ca) -> mock.ContinuousAssign:
    # referenced_signals forwarded too (same "adapter forgot to forward
    # a field" bug class already found and fixed repeatedly for this
    # module -- see Port.related_power_pin/InterfacePort.array_size/
    # SequentialElement.clock_ref et al.).
    return mock.ContinuousAssign(target=ca.target, source=ca.source, referenced_signals=tuple(ca.referenced_signals))


def _adapt_well_bias_pin(wb) -> mock.WellBiasPin:
    return mock.WellBiasPin(pg_type=wb.pg_type, related_bias_pin=wb.related_bias_pin)


def _adapt_sequential_element(seq) -> mock.SequentialElement:
    # edge/reset_kind/reset_active_low/set_active_low dropped -- see
    # mock.SequentialElement's own docstring, same trimming precedent as
    # Port/Net. clock_ref/reset_ref/set_ref/data_ref ARE forwarded (real,
    # confirmed gap: connectivity_flatten.py's own Driver/Receiver Supply
    # Set tracing needs them to recognize a flop's own clock/reset/set/
    # data as a real receiver -- previously silently dropped here, same
    # "adapter forgot to forward a field" bug class as Port.
    # related_power_pin/InterfacePort.array_size/Definition.continuous_assigns).
    return mock.SequentialElement(
        output_signal=seq.output_signal,
        is_latch=seq.is_latch,
        clock_ref=seq.clock_ref,
        reset_ref=seq.reset_ref,
        set_ref=seq.set_ref,
        data_ref=seq.data_ref,
    )


def _adapt_modport_port(p) -> mock.ModportPort:
    return mock.ModportPort(name=p.name, direction=mock.PortDirection[p.direction.name])


def _adapt_modport(m) -> mock.Modport:
    return mock.Modport(name=m.name, ports=[_adapt_modport_port(p) for p in m.ports])


def _adapt_interface_port(p) -> mock.InterfacePort:
    return mock.InterfacePort(name=p.name, interface_name=p.interface_name, modport=p.modport, inferred_modport=p.inferred_modport, array_size=p.array_size)


def _adapt_definition(definition) -> mock.Definition:
    return mock.Definition(
        definition_id=definition.definition_id,
        base_name=definition.base_name,
        language=mock.SourceLanguage[definition.language.name],
        is_black_box=definition.is_black_box,
        is_interface=definition.is_interface,
        ports=[_adapt_port(p) for p in definition.ports],
        nets=[_adapt_net(n) for n in definition.nets],
        instances=[_adapt_instance(i) for i in definition.instances],
        sequential_elements=[_adapt_sequential_element(s) for s in definition.sequential_elements],
        interface_ports=[_adapt_interface_port(p) for p in definition.interface_ports],
        modports=[_adapt_modport(m) for m in definition.modports],
        continuous_assigns=[_adapt_continuous_assign(c) for c in definition.continuous_assigns],
        well_bias_pins=[_adapt_well_bias_pin(w) for w in definition.well_bias_pins],
        source_loc=_adapt_rtl_source_loc(definition.source_loc),
        # Real, confirmed gap found while wiring up "Inspect Source
        # File" (power_intent_diagram_view.py's own context menu): this
        # field was never forwarded at all -- same "adapter forgot to
        # forward a field" bug class already found and fixed repeatedly
        # this session (Port.related_power_pin/InterfacePort.array_size/
        # Definition.continuous_assigns/well_bias_pins et al.).
        source_file=definition.source_file,
    )


def adapt_design(design: real.Design) -> mock.Design:
    return mock.Design(definitions={k: _adapt_definition(v) for k, v in design.definitions.items()}, top=design.top)


# --- Power intent --------------------------------------------------------


def _adapt_pi_source_loc(loc) -> Optional[MockPowerIntentSourceLoc]:
    if loc is None:
        return None
    return MockPowerIntentSourceLoc(file=loc.file, line=loc.line)


# --- power intent: one generic copy, not 11 hand-written adapters -------
#
# These used to be a per-field adapter per construct, which is precisely
# where fields get silently dropped -- this module's own docstring already
# named two such losses (IsolationStrategy/LevelShifterStrategy
# .exclude_elements) as accepted gaps, and power-intent import found
# thirty more once it needed the full shape.
#
# The mirror in shared/models/power_intent.py is now field-for-field
# identical to the backend schema (enforced by
# test_power_intent_mirror_completeness.py), which makes a generic,
# field-driven copy both possible and strictly safer: it cannot omit a
# field, because it never names one. A field added to the backend schema
# flows through automatically, and the completeness test fails first if
# the mirror hasn't kept up.
#
# Deliberately scoped to power intent. The RTL adapters above stay
# explicit: shared/models/rtl.py IS a genuine trimmed subset by design
# (it carries only what the widgets read), so a generic copy there would
# fail on the fields it intentionally doesn't mirror.

_PI_ENUMS = {"SourceFormat": mock.SourceFormat, "PowerStateShape": mock.PowerStateShape}


def _adapt_pi_value(value):
    """One backend power-intent value -> its mirror equivalent.

    Handles the four shapes that actually occur: SourceLoc (its own
    mirror type), enums (mapped by NAME, never by value -- the backend
    and mirror enums are separate types that happen to share members),
    nested dataclasses such as PowerSwitchState, and containers of any of
    those. Plain scalars pass through untouched."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, enum.Enum):
        mirror_enum = _PI_ENUMS.get(type(value).__name__)
        if mirror_enum is None:
            raise ValueError(f"no mirror enum for {type(value).__name__}")
        return mirror_enum[value.name]
    if isinstance(value, list):
        return [_adapt_pi_value(v) for v in value]
    if isinstance(value, dict):
        return {k: _adapt_pi_value(v) for k, v in value.items()}
    if dataclasses.is_dataclass(value):
        if type(value).__name__ == "SourceLoc":
            return MockPowerIntentSourceLoc(file=value.file, line=value.line)
        return _adapt_pi_construct(value)
    raise ValueError(f"cannot adapt power-intent value of type {type(value).__name__}")


def _adapt_pi_construct(obj):
    """A backend power-intent dataclass -> the mirror class of the same
    name, copying every field by name. Raises if the mirror lacks the
    class rather than quietly returning the backend object -- letting a
    real backend type leak into frontend state is the failure this mirror
    exists to prevent."""
    mirror_cls = getattr(mock, type(obj).__name__, None)
    if mirror_cls is None:
        raise ValueError(f"shared/models/power_intent.py has no mirror for {type(obj).__name__}")
    return mirror_cls(**{f.name: _adapt_pi_value(getattr(obj, f.name)) for f in dataclasses.fields(obj)})


def adapt_power_intent(power_intent: real.PowerIntentDesign) -> mock.PowerIntentDesign:
    """Whole-design copy. Every collection is adapted generically, so a
    new construct type needs only a mirror class of the same name -- no
    edit here."""
    return _adapt_pi_construct(power_intent)


# --- Mapping / validation --------------------------------------------------


def _adapt_reference_location(loc) -> mock.ReferenceLocation:
    return mock.ReferenceLocation(construct_kind=loc.construct_kind, construct_id=loc.construct_id, field=loc.field, index=loc.index)


def _adapt_resolved_reference(ref) -> mock.ResolvedReference:
    return mock.ResolvedReference(
        raw=ref.raw,
        location=_adapt_reference_location(ref.location),
        kind=mock.ResolutionKind[ref.kind.name],
        resolved_path=ref.resolved_path,
        resolved_definition_id=ref.resolved_definition_id,
        resolved_power_intent_id=ref.resolved_power_intent_id,
        detail=ref.detail,
    )


def adapt_mapped_design(mapped: real.MappedDesign, adapted_power_intent: mock.PowerIntentDesign, adapted_rtl: mock.Design) -> mock.MappedDesign:
    """Takes the already-adapted power_intent/rtl rather than re-adapting
    them, so a caller holding onto both (as real_actions.py's BackendState
    does, since Mapping needs the *real* objects too) doesn't pay to
    convert the same data twice."""
    return mock.MappedDesign(power_intent=adapted_power_intent, rtl=adapted_rtl, references=[_adapt_resolved_reference(r) for r in mapped.references])


def _adapt_finding(finding) -> mock.Finding:
    return mock.Finding(
        severity=mock.Severity[finding.severity.name],
        summary=finding.summary,
        reference=_adapt_resolved_reference(finding.reference) if finding.reference is not None else None,
        count=finding.count,
    )


def adapt_validation_report(report: real.ValidationReport) -> mock.ValidationReport:
    return mock.ValidationReport(findings=[_adapt_finding(f) for f in report.findings])


# --- Errors ------------------------------------------------------------


def error_to_diagnostic(exc: Exception, source: str, code: str) -> Diagnostic:
    """Coarse by default -- message only, no file/line -- for any
    exception that isn't a real.ElaborationError. Most exceptions this
    wraps (ValueError from elaborate_power_intent/run_mapping/run_validation)
    are still coarse today: UPF/CPF elaboration errors and mapping/
    validation findings have no equivalent structured-location exception
    type yet, a known, accepted limitation (see
    docs/backend_integration.md), not something attempted here.

    RTL elaboration is the one real exception (real.ElaborationError,
    confirmed against a real syntax bug -- a missing comma in a port
    connection list -- that previously produced a bare ValueError naming
    only a diagnostic code): it carries real `.file`/`.line` from
    pyslang's own diagnostic location. Checked via isinstance against the
    type re-exported from backend.api (this module's one sanctioned
    import), not duck-typed via getattr -- precise, not "anything that
    happens to have these attributes."
    """
    if isinstance(exc, real.ElaborationError):
        return Diagnostic(severity=Severity.ERROR, code=code, message=str(exc), file=exc.file, line=exc.line, source=source)
    return Diagnostic(severity=Severity.ERROR, code=code, message=str(exc), file=None, line=None, source=source)
