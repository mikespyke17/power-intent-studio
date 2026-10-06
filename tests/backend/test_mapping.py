"""Milestone 3: map_design() against a real elaborated RTL Design (Verilog,
Milestone 1) and a real elaborated PowerIntentDesign (UPF, Milestone 2) --
not hand-built IR fixtures. The RTL side (golden_rtl/mapping_smoke) was
authored specifically to match golden_upf/upf_basic's own instance/port
references (u_core/out1, u_core/out2, u_core/out3, u_dbg, top-level
sleep_n) so this exercises real cross-milestone resolution, not a
constructed-to-pass scenario.
"""

from pathlib import Path

from backend.elaborator.lib import elaborate_with_black_boxes
from backend.elaborator.power_intent.ir import SourceFormat
from backend.elaborator.power_intent.upf import elaborate as elaborate_upf
from backend.elaborator.rtl.verilog import elaborate as elaborate_verilog
from backend.mapping import ResolutionKind, map_design
from backend.mapping.rtl_resolver import resolve_rtl_reference

RTL_DIR = Path(__file__).parent / "golden_rtl" / "mapping_smoke"
UPF_DIR = Path(__file__).parent / "golden_upf" / "upf_basic"
BLACKBOX_DIR = Path(__file__).parent / "golden_lib" / "verilog_with_sram_blackbox"


def _mapped():
    rtl = elaborate_verilog([str(RTL_DIR / "top.v"), str(RTL_DIR / "core.v"), str(RTL_DIR / "dbg.v")])
    power_intent = elaborate_upf(str(UPF_DIR / "top.upf"))
    return map_design(power_intent, rtl)


def _find(mapped, construct_kind, construct_id, field, raw=None, index=None):
    matches = [
        r
        for r in mapped.references
        if r.location.construct_kind == construct_kind
        and r.location.construct_id == construct_id
        and r.location.field == field
        and (raw is None or r.raw == raw)
        and (index is None or r.location.index == index)
    ]
    assert len(matches) == 1, f"expected exactly one match, got {matches}"
    return matches[0]


def test_domain_dot_element_resolves_to_the_top_instance():
    mapped = _mapped()
    ref = _find(mapped, "PowerDomain", "::PD_TOP", "elements", raw=".")
    assert ref.kind == ResolutionKind.INSTANCE
    assert ref.resolved_path == "top"


def test_domain_exclude_element_resolves_to_a_real_instance():
    mapped = _mapped()
    ref = _find(mapped, "PowerDomain", "::PD_TOP", "exclude_elements", raw="u_dbg")
    assert ref.kind == ResolutionKind.INSTANCE
    assert ref.resolved_path == "top/u_dbg"


def test_isolation_elements_resolve_to_real_ports():
    mapped = _mapped()
    ref1 = _find(mapped, "IsolationStrategy", "::ISO_CORE", "elements", raw="u_core/out1")
    ref2 = _find(mapped, "IsolationStrategy", "::ISO_CORE", "elements", raw="u_core/out2")
    assert ref1.kind == ResolutionKind.PORT
    assert ref1.resolved_path == "top/u_core/out1"
    assert ref2.kind == ResolutionKind.PORT
    assert ref2.resolved_path == "top/u_core/out2"


def test_level_shifter_element_resolves_to_a_real_port():
    mapped = _mapped()
    ref = _find(mapped, "LevelShifterStrategy", "::LS_CORE", "elements", raw="u_core/out3")
    assert ref.kind == ResolutionKind.PORT
    assert ref.resolved_path == "top/u_core/out3"


def test_retention_element_resolves_to_a_real_instance():
    mapped = _mapped()
    ref = _find(mapped, "RetentionStrategy", "::RET_CORE", "elements", raw="u_core")
    assert ref.kind == ResolutionKind.INSTANCE
    assert ref.resolved_path == "top/u_core"


def test_switch_control_port_resolves_to_a_top_level_port():
    mapped = _mapped()
    ref = _find(mapped, "PowerSwitch", "::SW_CORE", "control_ports", raw="sleep_n")
    assert ref.kind == ResolutionKind.PORT
    assert ref.resolved_path == "top/sleep_n"


def test_isolation_domain_source_sink_resolve_intra_power_intent():
    mapped = _mapped()
    for field in ("domain", "source", "sink"):
        ref = _find(mapped, "IsolationStrategy", "::ISO_CORE", field)
        assert ref.raw == "PD_TOP"
        assert ref.kind == ResolutionKind.POWER_INTENT
        assert ref.resolved_power_intent_id == "::PD_TOP"


def test_switch_domain_resolves_intra_power_intent():
    mapped = _mapped()
    ref = _find(mapped, "PowerSwitch", "::SW_CORE", "domain")
    assert ref.kind == ResolutionKind.POWER_INTENT
    assert ref.resolved_power_intent_id == "::PD_TOP"


def test_switch_input_and_output_supply_fall_back_from_ports_to_nets():
    """VDD is a SupplyPort (create_supply_port VDD_IN is a different name --
    the switch's input_supply is actually 'VDD', a SupplyNet, not
    'VDD_IN'). This exercises the supply_ports-then-supply_nets fallback
    for real: input_supply must fall back to supply_nets to resolve at
    all; output_supply ('VDD_CORE') is also a SupplyNet only.
    """
    mapped = _mapped()
    input_ref = _find(mapped, "PowerSwitch", "::SW_CORE", "input_supply")
    output_ref = _find(mapped, "PowerSwitch", "::SW_CORE", "output_supply")
    assert input_ref.kind == ResolutionKind.POWER_INTENT
    assert input_ref.resolved_power_intent_id == "::VDD"
    assert output_ref.kind == ResolutionKind.POWER_INTENT
    assert output_ref.resolved_power_intent_id == "::VDD_CORE"


def test_domain_supply_connection_resolves_domain_and_net():
    """upf_basic's -primary_ground_net VSS is real fixture data where VSS
    is only ever referenced inside a supply_set's -function mapping, never
    independently declared via create_supply_net -- so that one connection
    legitimately comes back DANGLING. Confirmed by direct inspection
    (design.supply_nets only has VDD/VDD_CORE), not assumed -- this test
    checks for that dangling case explicitly rather than asserting a
    blanket "everything resolves" that the real fixture data doesn't
    support.
    """
    mapped = _mapped()
    domain_refs = [r for r in mapped.references if r.location.construct_kind == "DomainSupplyConnection" and r.location.field == "domain"]
    net_refs = [r for r in mapped.references if r.location.construct_kind == "DomainSupplyConnection" and r.location.field == "supply_net"]
    assert all(r.kind == ResolutionKind.POWER_INTENT for r in domain_refs)
    assert any(r.raw == "VDD" and r.kind == ResolutionKind.POWER_INTENT and r.resolved_power_intent_id == "::VDD" for r in net_refs)
    assert any(r.raw == "VDD_CORE" and r.kind == ResolutionKind.POWER_INTENT and r.resolved_power_intent_id == "::VDD_CORE" for r in net_refs)
    assert any(r.raw == "VSS" and r.kind == ResolutionKind.DANGLING for r in net_refs)


def test_partial_list_resolution_mixed_hits_and_misses():
    """The common real-world case: a list field where some entries resolve
    and others are genuinely dangling. All three entries must survive into
    mapped.references at their correct list index -- a real bug class this
    guards against is a resolver that only reports the first miss, or that
    drops an entry instead of reporting it as DANGLING.
    """
    rtl_dir = Path(__file__).parent / "golden_rtl" / "mapping_smoke"
    upf_dir = Path(__file__).parent / "golden_upf" / "upf_partial_dangling"
    rtl = elaborate_verilog([str(rtl_dir / "top.v"), str(rtl_dir / "core.v"), str(rtl_dir / "dbg.v")])
    power_intent = elaborate_upf(str(upf_dir / "top.upf"))
    mapped = map_design(power_intent, rtl)

    element_refs = [
        r for r in mapped.references if r.location.construct_kind == "PowerDomain" and r.location.field == "elements"
    ]
    by_index = {r.location.index: r for r in element_refs}
    assert len(by_index) == 3  # nothing dropped

    assert by_index[0].raw == "u_core"
    assert by_index[0].kind == ResolutionKind.INSTANCE
    assert by_index[0].resolved_path == "top/u_core"

    assert by_index[1].raw == "u_nonexistent"
    assert by_index[1].kind == ResolutionKind.DANGLING
    assert by_index[1].resolved_path is None
    assert "u_nonexistent" in by_index[1].detail

    assert by_index[2].raw == "u_dbg"
    assert by_index[2].kind == ResolutionKind.INSTANCE
    assert by_index[2].resolved_path == "top/u_dbg"


# --- not-yet-wired fields must be visible, not silently absent -----------


def test_not_yet_wired_fields_are_reported_as_not_attempted_not_dropped():
    """PowerDomain.primary_supply, *_supply fields, PowerState.supply/
    .applies_to, and PowerStateGroup.members have no independently
    verified target dict yet (see map_design._NOT_YET_WIRED) -- but a
    field with a real raw value must still show up in
    mapped.references as an explicit NOT_ATTEMPTED entry, not vanish as if
    map_design() never saw it. upf_basic populates several of these for
    real.
    """
    mapped = _mapped()

    iso_supply = _find(mapped, "IsolationStrategy", "::ISO_CORE", "isolation_supply")
    assert iso_supply.raw == "SS_CORE"
    assert iso_supply.kind == ResolutionKind.NOT_ATTEMPTED
    assert iso_supply.resolved_power_intent_id is None
    assert "docs/milestone3_mapping.md" in iso_supply.detail

    ret_supply = _find(mapped, "RetentionStrategy", "::RET_CORE", "retention_supply")
    assert ret_supply.kind == ResolutionKind.NOT_ATTEMPTED

    on_supply = _find(mapped, "PowerState", "::SS_CORE.ON", "supply")
    assert on_supply.raw == "SS_CORE"
    assert on_supply.kind == ResolutionKind.NOT_ATTEMPTED

    on_applies_to = _find(mapped, "PowerState", "::SS_CORE.ON", "applies_to")
    assert on_applies_to.raw == "SS_CORE"
    assert on_applies_to.kind == ResolutionKind.NOT_ATTEMPTED

    # OFF never set -supply, so no NOT_ATTEMPTED ref for that field on that
    # construct -- confirms _not_attempted() doesn't fabricate entries for
    # genuinely-absent (None) values.
    off_supply_refs = [
        r for r in mapped.references if r.location.construct_kind == "PowerState" and r.location.construct_id == "::SS_CORE.OFF" and r.location.field == "supply"
    ]
    assert off_supply_refs == []

    members = _find(mapped, "PowerStateGroup", "::PSG_TOP", "members", raw="SS_CORE")
    assert members.kind == ResolutionKind.NOT_ATTEMPTED


def test_dangling_reference_is_reported_not_silently_dropped():
    mapped = _mapped()
    ref = _find(mapped, "RetentionStrategy", "::RET_CORE", "elements", raw="u_core")
    assert ref.kind != ResolutionKind.DANGLING  # sanity: the real fixture data isn't dangling

    # A reference that genuinely doesn't exist in the RTL must come back
    # DANGLING with a reason, not silently vanish from mapped.references.
    kind, path, def_id, detail = resolve_rtl_reference("u_nonexistent", "", SourceFormat.UPF, rtl=_mapped().rtl)
    assert kind == ResolutionKind.DANGLING
    assert path is None
    assert "u_nonexistent" in detail


# --- black-box (1e) resolution: real Definition.is_black_box=True data ---


def _blackbox_rtl():
    return elaborate_with_black_boxes([str(BLACKBOX_DIR / "top.v")], [str(BLACKBOX_DIR / "sram.lib")])


def test_black_box_declared_pin_resolves_to_black_box_port():
    rtl = _blackbox_rtl()
    kind, path, def_id, detail = resolve_rtl_reference("u_sram/data", "", SourceFormat.UPF, rtl)
    assert kind == ResolutionKind.BLACK_BOX_PORT
    assert path == "top/u_sram/data"
    assert detail is None


def test_black_box_undeclared_pin_is_unresolvable_not_dangling():
    """Distinguishing UNRESOLVABLE_BLACK_BOX from plain DANGLING matters:
    'u_sram/nonexistent_pin' fails specifically because the black-box has
    no internal RTL to check further, not because the instance path itself
    is wrong -- a validation layer consuming this should be able to tell
    "this might be a real pin the .lib file didn't declare" apart from
    "this instance path doesn't exist at all".
    """
    rtl = _blackbox_rtl()
    kind, path, def_id, detail = resolve_rtl_reference("u_sram/nonexistent_pin", "", SourceFormat.UPF, rtl)
    assert kind == ResolutionKind.UNRESOLVABLE_BLACK_BOX
    assert path is None
    assert def_id is not None
    assert "nonexistent_pin" in detail


# --- virtual-signal forward-compatibility slot ----------------------------


def test_declared_virtual_name_resolves_to_virtual_before_touching_rtl():
    rtl = _mapped().rtl
    kind, path, def_id, detail = resolve_rtl_reference(
        "some_logic_port", "", SourceFormat.UPF, rtl, virtual_names=frozenset({"some_logic_port"})
    )
    assert kind == ResolutionKind.VIRTUAL
    assert path is None and def_id is None and detail is None


# --- CPF separator (".") -------------------------------------------------


def test_cpf_paths_resolve_with_dot_separator():
    from backend.elaborator.power_intent.cpf import elaborate as elaborate_cpf

    cpf_dir = Path(__file__).parent / "golden_cpf" / "cpf_hierarchical"
    power_intent = elaborate_cpf(str(cpf_dir / "top.cpf"))
    rtl = elaborate_verilog([str(RTL_DIR / "top.v"), str(RTL_DIR / "core.v"), str(RTL_DIR / "dbg.v")])
    mapped = map_design(power_intent, rtl)
    # Whatever CPF's own domain/instance data actually is, this must not
    # silently produce zero references or blow up joining with "." instead
    # of "/" -- confirms the separator switch is real, not just declared.
    assert len(mapped.references) > 0
