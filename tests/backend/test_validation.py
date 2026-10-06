"""backend/validation/: turns a real MappedDesign (Milestone 3's resolvers,
run against real elaborated fixtures -- see test_mapping.py) into a
ValidationReport. Exercises the approved severity mapping end to end, not
just against hand-built ResolvedReference objects.
"""

from pathlib import Path

from backend.elaborator.lib import elaborate_with_black_boxes
from backend.elaborator.power_intent.ir import SourceFormat
from backend.elaborator.power_intent.upf import elaborate as elaborate_upf
from backend.elaborator.rtl.verilog import elaborate as elaborate_verilog
from backend.mapping import ResolutionKind, map_design
from backend.mapping.rtl_resolver import resolve_rtl_reference
from backend.mapping.schema import MappedDesign, ReferenceLocation, ResolvedReference
from backend.validation import Severity, validate

RTL_DIR = Path(__file__).parent / "golden_rtl" / "mapping_smoke"
UPF_DIR = Path(__file__).parent / "golden_upf" / "upf_basic"
PARTIAL_DIR = Path(__file__).parent / "golden_upf" / "upf_partial_dangling"
BLACKBOX_DIR = Path(__file__).parent / "golden_lib" / "verilog_with_sram_blackbox"


def _rtl():
    return elaborate_verilog([str(RTL_DIR / "top.v"), str(RTL_DIR / "core.v"), str(RTL_DIR / "dbg.v")])


def _mapped_upf_basic():
    return map_design(elaborate_upf(str(UPF_DIR / "top.upf")), _rtl())


def test_dangling_reference_produces_an_error_finding():
    """upf_basic's -primary_ground_net VSS is real fixture data that never
    independently declares VSS via create_supply_net -- confirmed DANGLING
    in test_mapping.py's own domain-supply-connection test. That real
    dangling reference must surface as an ERROR finding here.
    """
    report = validate(_mapped_upf_basic())
    dangling_errors = [
        f
        for f in report.findings
        if f.severity == Severity.ERROR and f.reference is not None and f.reference.raw == "VSS"
    ]
    assert len(dangling_errors) == 1
    assert dangling_errors[0].reference.kind == ResolutionKind.DANGLING
    assert "VSS" in dangling_errors[0].summary
    assert report.has_errors is True


def test_partial_dangling_list_produces_exactly_one_error_for_the_bad_entry():
    rtl = _rtl()
    power_intent = elaborate_upf(str(PARTIAL_DIR / "top.upf"))
    mapped = map_design(power_intent, rtl)
    report = validate(mapped)

    element_errors = [
        f
        for f in report.findings
        if f.severity == Severity.ERROR and f.reference is not None and f.reference.location.field == "elements"
    ]
    assert len(element_errors) == 1
    assert element_errors[0].reference.raw == "u_nonexistent"
    assert report.has_errors is True


def test_unresolvable_black_box_produces_a_warning_not_an_error():
    rtl = elaborate_with_black_boxes([str(BLACKBOX_DIR / "top.v")], [str(BLACKBOX_DIR / "sram.lib")])
    kind, path, def_id, detail = resolve_rtl_reference("u_sram/nonexistent_pin", "", SourceFormat.UPF, rtl)
    assert kind == ResolutionKind.UNRESOLVABLE_BLACK_BOX  # sanity, matches test_mapping.py

    ref = ResolvedReference(
        raw="u_sram/nonexistent_pin",
        location=ReferenceLocation("IsolationStrategy", "::ISO_FAKE", "elements", 0),
        kind=kind,
        resolved_definition_id=def_id,
        detail=detail,
    )
    mapped = MappedDesign(power_intent=elaborate_upf(str(UPF_DIR / "top.upf")), rtl=rtl, references=[ref])
    report = validate(mapped)

    warnings = [f for f in report.findings if f.severity == Severity.WARNING]
    assert len(warnings) == 1
    assert warnings[0].reference is ref
    assert report.has_errors is False  # a warning alone must not flip has_errors


def test_not_attempted_references_are_deduplicated_per_field_not_per_instance():
    """upf_basic populates five distinct not-yet-wired fields for real
    (isolation_supply, retention_supply, PowerState.supply,
    PowerState.applies_to, PowerStateGroup.members) -- confirmed by
    test_mapping.py's own NOT_ATTEMPTED test. Each must collapse into
    exactly one EXCLUDED finding carrying the right count, not one finding
    per instance.
    """
    report = validate(_mapped_upf_basic())
    excluded = {f.summary.split(":")[0]: f for f in report.findings if f.severity == Severity.EXCLUDED}

    assert set(excluded) == {
        "IsolationStrategy.isolation_supply",
        "RetentionStrategy.retention_supply",
        "PowerState.supply",
        "PowerState.applies_to",
        "PowerStateGroup.members",
    }
    # PowerState.applies_to is set on both the ON and OFF states (the
    # positional add_power_state object arg is always given); PowerState.
    # supply is only set on ON (OFF's add_power_state never passed -supply)
    # -- real, asymmetric fixture data, not a guess.
    assert excluded["PowerState.applies_to"].count == 2
    assert excluded["PowerState.supply"].count == 1
    for f in excluded.values():
        assert f.reference is None  # deduplicated findings don't point at a single instance


def test_successful_resolutions_produce_no_findings_at_all():
    mapped = _mapped_upf_basic()
    report = validate(mapped)
    resolved_refs = [
        r for r in mapped.references if r.kind in (ResolutionKind.INSTANCE, ResolutionKind.PORT, ResolutionKind.POWER_INTENT)
    ]
    assert len(resolved_refs) > 0  # sanity: there is real successful-resolution data to check against
    finding_refs = {id(f.reference) for f in report.findings if f.reference is not None}
    assert not any(id(r) in finding_refs for r in resolved_refs)


def test_virtual_kind_produces_no_finding():
    rtl = _rtl()
    kind, path, def_id, detail = resolve_rtl_reference(
        "some_logic_port", "", SourceFormat.UPF, rtl, virtual_names=frozenset({"some_logic_port"})
    )
    ref = ResolvedReference(
        raw="some_logic_port",
        location=ReferenceLocation("IsolationStrategy", "::ISO_FAKE", "elements", 0),
        kind=kind,
    )
    mapped = MappedDesign(power_intent=elaborate_upf(str(UPF_DIR / "top.upf")), rtl=rtl, references=[ref])
    report = validate(mapped)
    assert report.findings == []
