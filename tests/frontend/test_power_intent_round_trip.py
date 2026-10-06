"""Round-trip fidelity: generate, re-load, expect the same thing back.

This is the Load Power Intent mapping feature's own definition of done
(spec section 8). For every construct type this app can GENERATE, writing
it out and immediately re-loading the generated file through the import
path must produce ZERO errors, ZERO warnings, and equivalent internal
state. Anything that does not survive that trip is either a generator gap
or an importer gap, and this file is what makes the difference visible.

WHY THIS TEST IS ALLOWED TO TOUCH THE BACKEND. Every other file under
tests/frontend/ runs against mock data, per the milestone-independence
rule in CLAUDE.md. This one cannot: a round trip has to go through the
REAL UPF elaborator, because the whole question is whether what the
generator writes is what the elaborator reads. It therefore sits in the
same category as test_real_backend_integration.py -- reaching the backend
only through `backend.api` via `frontend/src/backend_integration/`, never
into backend internals.

WHAT "EQUIVALENT" MEANS HERE. Not identical objects: placement ids are
freshly generated on import, colours are invented (the file carries
none), and a domain's colour is derived later from its supply set rather
than stored. Equivalence is asserted on the fields that carry the user's
actual intent -- names, scopes, which supply set a domain points at,
which elements a strategy covers, a switch's pin names and states --
normalised by `snapshot()` below. A field that genuinely cannot survive
the trip is asserted as a named, explained exception, never quietly
excluded from the comparison.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtGui import QColor

from frontend.src.power_intent_view.isolation_overlay import IsolationDiagramState, IsolationStrategyPlacement
from frontend.src.power_intent_view.level_shifter_overlay import (
    LevelShifterDiagramState,
    LevelShifterStrategyPlacement,
)
from frontend.src.power_intent_view.macro_power_intent_overlay import MacroPowerIntentDiagramState
from frontend.src.power_intent_view.port_attribute_overlay import PortAttributeDiagramState
from frontend.src.power_intent_view.power_domain_overlay import (
    PowerDomainDiagramState,
    PowerDomainPlacement,
    SupplySetPlacement,
)
from frontend.src.power_intent_view.power_intent_import import import_power_intent, rtl_top_path
from frontend.src.power_intent_view.power_intent_text_export import generate_upf
from frontend.src.power_intent_view.power_state_table import PowerStateTableDiagramState, PowerStateTableRow
from frontend.src.power_intent_view.power_switch_overlay import (
    PowerSwitchDiagramState,
    PowerSwitchPlacement,
    power_switch_connector_key,
)
from frontend.src.power_intent_view.retention_overlay import RetentionDiagramState, RetentionStrategyPlacement
from frontend.src.power_intent_view.supply_port_overlay import (
    SupplyPortDiagramState,
    SupplyPortPlacement,
    SupplyPortState,
)

# The design is the one VENDORED under tests/, not an examples/ one.
# These tests used to point at examples/soc_with_ram, and broke the day
# that example was curated away -- a test should not depend on assets
# maintained for users' benefit, which are free to change or disappear.
# golden_simple10/design/ exists precisely to be a fixture.
EXAMPLE_DIR = Path(__file__).parent / "golden_simple10"
DESIGN_DIR = EXAMPLE_DIR / "design"

# The elaborated fixture hierarchy, for reference while reading the state
# built below:
#   soc_top
#   soc_top/u_cpu                  (soft)
#   soc_top/u_mem                  (soft)
#   soc_top/u_mem/u_sram_instr     (macro, .lib)
#   soc_top/u_mem/u_sram_data      (macro, .lib)
#   soc_top/u_periph               (soft)
TOP = "sram"
CPU = "sram/u_sub"            # the soft sub-block
SRAM = "sram/u_sram"          # the .lib macro


# --- the real pipeline ------------------------------------------------------


@pytest.fixture(scope="module")
def rtl():
    """The real elaborated design every reference resolves against.

    Module-scoped: elaboration is the slow part, and nothing in this file
    mutates the Design."""
    from backend.api import elaborate_rtl
    from frontend.src.backend_integration.backend_adapter import adapt_design

    return adapt_design(
        elaborate_rtl(
            [str(DESIGN_DIR / "sram.v"), str(DESIGN_DIR / "sub.sv")],
            [str(DESIGN_DIR / "sram.lib")],
            top=TOP,
        )
    )


def reload_generated(text: str, rtl, tmp_path: Path):
    """Run generated UPF text back through the full real pipeline:
    elaborate, map, adapt, import. Returns the ImportedPowerIntent."""
    from backend.api import elaborate_power_intent, run_mapping
    from frontend.src.backend_integration.backend_adapter import (
        adapt_mapped_design,
        adapt_power_intent,
    )

    path = tmp_path / "generated.upf"
    path.write_text(text)

    power_intent = elaborate_power_intent(str(path), "upf")
    adapted = adapt_power_intent(power_intent)
    mapped = adapt_mapped_design(run_mapping(power_intent, _backend_design(rtl)), adapted, rtl)
    return import_power_intent(adapted, rtl, mapped, str(path))


_backend_designs: dict = {}


def _backend_design(adapted_rtl):
    """The BACKEND Design matching the adapted one, for run_mapping().

    Mapping resolves against the backend's own IR, while the import works
    on the adapted mirror -- re-elaborating once per session and caching
    is cheaper than threading both objects through every helper."""
    if "design" not in _backend_designs:
        from backend.api import elaborate_rtl

        _backend_designs["design"] = elaborate_rtl(
            [str(DESIGN_DIR / "sram.v"), str(DESIGN_DIR / "sub.sv")],
            [str(DESIGN_DIR / "sram.lib")],
            top=TOP,
        )
    return _backend_designs["design"]


def _top_ports(rtl):
    return rtl.definitions[rtl.top].ports


# --- state builders, one per construct type --------------------------------
#
# Each returns the minimum complete state for its own construct, on top of
# the supplies/sets/domains it needs, so a failure names the construct
# rather than "the big fixture".


def _supplies_and_sets():
    """Two boundary supply ports, one internal supply, one virtual
    supply, and the two supply sets over them."""
    supply = SupplyPortDiagramState(
        supply_port_placements={
            "vdd": SupplyPortPlacement(
                id="vdd", owner_path=TOP, name="VDD", color=QColor("#4285f4"),
                states=[
                    SupplyPortState("FULL_ON", "1.20"),
                    SupplyPortState("OFF"),
                ],
            ),
            "vss": SupplyPortPlacement(
                id="vss", owner_path=TOP, name="VSS", color=QColor("#262a30"),
                states=[SupplyPortState("FULL_ON", "0.0")],
            ),
            "vdd_cpu": SupplyPortPlacement(
                id="vdd_cpu", owner_path=CPU, name="VDD_CPU", color=QColor("#db4437"),
                is_internal_supply=True,
            ),
        }
    )
    domain = PowerDomainDiagramState(
        supply_sets={
            "ss_top": SupplySetPlacement(
                id="ss_top", name="SS_TOP", power="VDD", ground="VSS",
                owner_path=TOP, owner_paths=[TOP],
            ),
            "ss_cpu": SupplySetPlacement(
                id="ss_cpu", name="SS_CPU", power="VDD_CPU", ground="VSS",
                owner_path=CPU, owner_paths=[CPU],
            ),
        }
    )
    return supply, domain


def _with_domains(supply, domain):
    domain.domains["pd_top"] = PowerDomainPlacement(
        id="pd_top", owner_path=TOP, owner_paths=[TOP], name="PD_TOP",
        color=QColor("#4285f4"), primary_supply_set="ss_top",
    )
    domain.domains["pd_cpu"] = PowerDomainPlacement(
        id="pd_cpu", owner_path=CPU, owner_paths=[CPU], name="PD_CPU",
        color=QColor("#db4437"), primary_supply_set="ss_cpu",
        secondary_supply_sets=["ss_top"],
    )
    # What the app's own auto-link adds when PD_CPU is created: SS_CPU's
    # VSS and the shared SS_TOP's VDD/VSS live at the top, so each gets a
    # same-named Internal Supply on PD_CPU's hierarchy, wired to the top
    # port -- one continuous rail. A flat UPF declares each net once, so
    # the importer rebuilds these copies from the domains and sets.
    for name, source in (("VDD", "vdd"), ("VSS", "vss")):
        copy_id = f"{source}_at_cpu"
        supply.supply_port_placements[copy_id] = SupplyPortPlacement(
            id=copy_id, owner_path=CPU, name=name,
            color=supply.supply_port_placements[source].color, is_internal_supply=True,
        )
        supply.supply_connector_placements.append((source, copy_id))
    return supply, domain


def build_state(rtl):
    """Every generatable construct at once -- the full round trip."""
    supply, domain = _with_domains(*_supplies_and_sets())

    switch = PowerSwitchDiagramState(
        switches={
            "sw": PowerSwitchPlacement(
                id="sw", name="SW_CPU", power_domain_id="pd_cpu",
                target_id="vdd_cpu", owner_path=CPU,
                target_ids=["vdd_cpu"], owner_paths=[CPU],
                input_supply_port="in_p", output_supply_port="out_p",
                control_port="sleep_ctrl", control_signal="pwr_en_cpu", cell_type="HDR",
                ack_port="ack_pin", ack_signal="pwr_ack_cpu",
                # Authored against the REAL signal, as the dialog's own
                # hint says: exercised both as the bare signal and inside
                # a compound expression, since those substitute
                # differently on the way out and back.
                on_state="pwr_en_cpu", off_state="!pwr_en_cpu && test_mode",
            )
        }
    )
    # The switch's Supply In, wired back to the top-level VDD.
    supply.supply_connector_placements.append((power_switch_connector_key("sw", "vdd_cpu"), "vdd"))

    retention = RetentionDiagramState(
        strategies={
            "ret": RetentionStrategyPlacement(
                id="ret", name="RET_CPU", power_domain_id="pd_cpu",
                primary_supply_set_id="ss_cpu", retention_supply_set_id="ss_top",
                save_logic="save_clk", restore_logic="restore_clk",
                elements=[f"{CPU}/o_q"], owner_path=CPU,
            )
        }
    )

    isolation = IsolationDiagramState(
        strategies={
            "iso": IsolationStrategyPlacement(
                id="iso", name="ISO_CPU", power_domain_id="pd_cpu",
                isolation_supply_set_id="ss_top", clamp_value="low",
                isolation_control="iso_en", isolation_sense="high",
                location="parent", elements=[f"{CPU}/o_q"], owner_path=CPU,
                # Optional Source/Sink -- emitted as -source/-sink and
                # read back (the reference golden carries both).
                source_supply_set_id="ss_cpu", sink_supply_set_id="ss_top",
            )
        }
    )

    level_shifter = LevelShifterDiagramState(
        strategies={
            "ls": LevelShifterStrategyPlacement(
                id="ls", name="LS_CPU", power_domain_id="pd_cpu",
                applies_to="outputs", rule="low_to_high", location="parent",
                threshold="0.1", cell_type="LS_HL",
                elements=[f"{CPU}/o_q"], owner_path=CPU,
            )
        }
    )

    port_attribute = PortAttributeDiagramState(assignments={})
    for port in _top_ports(rtl):
        if port.width == 1 and port.name not in ("VDD", "VSS"):
            port_attribute.assignments[port.name] = "ss_top"
            break

    # Every rail selected in every row: a supply's own states now reach
    # the file THROUGH its Supply Set's derived states (Tier 1), so a row
    # that leaves a rail unselected says nothing about that rail and the
    # set gets no state from it. Two rows, so the derivation has
    # something to deduplicate and the group has more than one member.
    power_state_table = PowerStateTableDiagramState(
        rows={
            "r1": PowerStateTableRow(
                id="r1", power_domain_id="pd_top", state_name="ALL_ON",
                net_states={"vdd": "FULL_ON 1.20", "vss": "FULL_ON 0.0", "vdd_cpu": "FULL_ON 1.20"},
            ),
            "r2": PowerStateTableRow(
                id="r2", power_domain_id="pd_top", state_name="CPU_OFF",
                net_states={"vdd": "FULL_ON 1.20", "vss": "FULL_ON 0.0", "vdd_cpu": "OFF"},
            ),
            "r3": PowerStateTableRow(
                id="r3", power_domain_id="pd_top", state_name="ALL_OFF",
                net_states={"vdd": "OFF", "vss": "FULL_ON 0.0", "vdd_cpu": "OFF"},
            ),
        }
    )

    macro = MacroPowerIntentDiagramState(
        # Any real UPF file does -- what is under test is that the
        # ATTACHMENT survives, not its content. It has to declare the
        # same 3.1 the generated file does, though: a design may not mix
        # semantic versions, and the elaborator rightly refuses one that
        # tries.
        attachments={SRAM: str(EXAMPLE_DIR / "power_intent" / "02_vendor_dense_switch_iso_ret.upf")}
    )

    return {
        "supply": supply,
        "domain": domain,
        "retention": retention,
        "switch": switch,
        "isolation": isolation,
        "level_shifter": level_shifter,
        "port_attribute": port_attribute,
        "power_state_table": power_state_table,
        "macro": macro,
    }


def generate(state, rtl, output_dir=None) -> str:
    return generate_upf(
        state["supply"],
        state["domain"],
        state["retention"],
        "3.1",
        state["switch"],
        state["level_shifter"],
        design_name=TOP,
        isolation_state=state["isolation"],
        port_attribute_state=state["port_attribute"],
        top_level_ports=_top_ports(rtl),
        power_state_table_state=state["power_state_table"],
        macro_power_intent_state=state["macro"],
        output_dir=output_dir,
        rtl_design=rtl,
    )


def roundtrip(state, rtl, tmp_path: Path):
    text = generate(state, rtl, output_dir=str(tmp_path))
    return reload_generated(text, rtl, tmp_path), text


# --- normalisation for the equivalence comparison --------------------------


def _supply_key(placement):
    return (
        placement.name,
        placement.owner_path,
        placement.is_internal_supply,
        placement.is_virtual_supply,
        tuple(sorted((s.state_type, s.voltage or "") for s in placement.states)),
    )


def snapshot(state_or_imported) -> dict:
    """The comparable form of a diagram state: names and intent, with
    every id and colour dropped (see this module's docstring)."""
    if isinstance(state_or_imported, dict):
        supply = state_or_imported["supply"]
        domain = state_or_imported["domain"]
        retention = state_or_imported["retention"]
        switch = state_or_imported["switch"]
        isolation = state_or_imported["isolation"]
        level_shifter = state_or_imported["level_shifter"]
        macro = state_or_imported["macro"]
    else:
        supply = state_or_imported.supply
        domain = state_or_imported.domain
        retention = state_or_imported.retention
        switch = state_or_imported.power_switch
        isolation = state_or_imported.isolation
        level_shifter = state_or_imported.level_shifter
        macro = state_or_imported.macro_power_intent

    set_name = {s.id: s.name for s in domain.supply_sets.values()}
    domain_name = {d.id: d.name for d in domain.domains.values()}

    return {
        "supplies": sorted(
            _supply_key(p) for p in supply.supply_port_placements.values() if not p.fixed
        ),
        "supply_sets": sorted(
            (s.name, s.power, s.ground, s.n_well, s.p_well, tuple(sorted(s.owner_paths)))
            for s in domain.supply_sets.values()
        ),
        "domains": sorted(
            (
                d.name,
                tuple(sorted(d.owner_paths)),
                set_name.get(d.primary_supply_set),
                tuple(sorted(set_name.get(i) for i in d.secondary_supply_sets)),
            )
            for d in domain.domains.values()
        ),
        "retention": sorted(
            (
                r.name,
                domain_name.get(r.power_domain_id),
                set_name.get(r.primary_supply_set_id),
                set_name.get(r.retention_supply_set_id),
                r.save_logic,
                r.restore_logic,
                tuple(sorted(r.elements)),
            )
            for r in retention.strategies.values()
        ),
        "switches": sorted(
            (
                s.name,
                domain_name.get(s.power_domain_id),
                s.input_supply_port,
                s.output_supply_port,
                s.control_port,
                s.cell_type,
                s.on_state,
                s.off_state,
            )
            for s in switch.switches.values()
        ),
        "isolation": sorted(
            (
                i.name,
                domain_name.get(i.power_domain_id),
                set_name.get(i.isolation_supply_set_id),
                set_name.get(i.source_supply_set_id),
                set_name.get(i.sink_supply_set_id),
                i.clamp_value,
                i.isolation_control,
                i.isolation_sense,
                i.location,
                i.applies_to,
                tuple(sorted(i.elements)),
            )
            for i in isolation.strategies.values()
        ),
        "level_shifters": sorted(
            (
                ls.name,
                domain_name.get(ls.power_domain_id),
                ls.applies_to,
                ls.rule,
                ls.location,
                ls.threshold,
                tuple(sorted(ls.elements)),
            )
            for ls in level_shifter.strategies.values()
        ),
        "macro_attachments": sorted(macro.attachments),
    }


# --- section 8: the round trip itself --------------------------------------


def test_the_full_round_trip_produces_no_errors_and_no_warnings(rtl, tmp_path):
    """The headline requirement. Every construct this app generates,
    written out and read straight back, with nothing unresolved and
    nothing unrecognized -- the app must understand its own output
    completely."""
    imported, text = roundtrip(build_state(rtl), rtl, tmp_path)

    assert [d.message for d in imported.errors] == []
    assert [d.message for d in imported.warnings] == []
    assert text  # and it really did generate something


def test_the_full_round_trip_produces_equivalent_state(rtl, tmp_path):
    imported, _ = roundtrip(build_state(rtl), rtl, tmp_path)
    before = snapshot(build_state(rtl))
    after = snapshot(imported)

    for key in sorted(before):
        assert after[key] == before[key], key


@pytest.mark.parametrize(
    "key",
    ["supplies", "supply_sets", "domains", "retention", "switches", "isolation", "level_shifters", "macro_attachments"],
)
def test_each_construct_type_survives_the_round_trip(rtl, tmp_path, key):
    """Parametrized per construct so a regression names the construct
    that broke rather than failing one opaque whole-state assertion."""
    imported, _ = roundtrip(build_state(rtl), rtl, tmp_path)
    assert snapshot(imported)[key] == snapshot(build_state(rtl))[key]


def test_a_supply_ports_own_power_states_survive_the_round_trip(rtl, tmp_path):
    """States are what the Power State Table and the Internal Supply
    inheritance both read, so they are asserted on their own rather than
    only inside the whole-supply tuple."""
    imported, _ = roundtrip(build_state(rtl), rtl, tmp_path)

    vdd = next(p for p in imported.supply.supply_port_placements.values() if p.name == "VDD")
    assert sorted((s.state_type, s.voltage or "") for s in vdd.states) == [
        ("FULL_ON", "1.20"),
        ("OFF", ""),
    ]


def test_a_nested_supply_nets_scope_survives_the_round_trip(rtl, tmp_path):
    """Every net is declared at the ROOT scope now (the reference golden's
    flat form), so the file no longer states a nested Internal
    Supply's hierarchy directly -- the importer recovers it from the
    domain whose supply set uses the net. This is the regression guard
    for the net coming back on the root block."""
    imported, text = roundtrip(build_state(rtl), rtl, tmp_path)

    assert "set_scope u_sub" not in text
    assert "create_supply_net VDD_CPU\n" in text
    cpu = next(p for p in imported.supply.supply_port_placements.values() if p.name == "VDD_CPU")
    assert cpu.owner_path == CPU
    assert cpu.is_internal_supply is True


def test_the_switchs_pin_names_and_states_survive_the_round_trip(rtl, tmp_path):
    """The generator writes the GUI's On/Off State as the state
    EXPRESSION and invents a state name around it, so the importer has to
    read the expression back, not the name.

    Also pins the whole Switch Control path: the three pin names travel
    through `-port_map`, the real signal through `-control_port {CTRL
    <net>}`, and the State expressions are rewritten to CTRL on the way
    out and back to the real signal on the way in. Asserted on the TEXT
    as well, because a substitution bug that is symmetric would round
    trip perfectly while writing UPF nobody else can read."""
    imported, text = roundtrip(build_state(rtl), rtl, tmp_path)

    # What reaches the FILE: abstract ports, with the real names stated
    # where UPF expects each of them.
    # The port NAMES are the user's own field values -- no abstract-token
    # layer, and no `-port_map` bridging one to the other.
    assert "-input_supply_port {in_p VDD}" in text
    assert "-output_supply_port {out_p VDD_CPU}" in text
    assert "-control_port {sleep_ctrl pwr_en_cpu}" in text
    assert "-ack_port {ack_pin pwr_ack_cpu}" in text
    assert "-port_map" not in text
    # ...so the ON/OFF substitution targets the CONTROL PORT's own name.
    assert "-on_state {on in_p {sleep_ctrl}}" in text
    assert "-off_state {off {!sleep_ctrl && test_mode}}" in text

    switch = next(iter(imported.power_switch.switches.values()))
    assert switch.name == "SW_CPU"
    assert switch.input_supply_port == "in_p"
    assert switch.output_supply_port == "out_p"
    assert switch.control_port == "sleep_ctrl"
    assert switch.control_signal == "pwr_en_cpu"
    # The acknowledge pair travels the same two routes as the control
    # pair: the cell PIN through -port_map, the real SIGNAL through
    # -ack_port's own net half.
    assert switch.ack_port == "ack_pin"
    assert switch.ack_signal == "pwr_ack_cpu"
    # ...and what comes BACK is what the user actually typed.
    assert switch.on_state == "pwr_en_cpu"
    assert switch.off_state == "!pwr_en_cpu && test_mode"
    assert switch.cell_type == "HDR"


def test_an_unconnected_switchs_input_port_name_cannot_survive_and_that_is_expected(rtl, tmp_path):
    """A named round-trip exception, RESTORED.

    UPF writes the input port as `-input_supply_port {<name> <net>}`, so
    a switch whose Supply In was never wired has no net to pair the name
    with, and the flag is omitted entirely -- there is no legal UPF
    carrying the port name alone.

    This exception was briefly retired when `map_power_switch -port_map`
    stated the names independently of any net. With `-port_map` retired
    in turn, it is back, and for its original reason. Everything else
    about the switch still round-trips."""
    state = build_state(rtl)
    state["supply"].supply_connector_placements.clear()
    imported, text = roundtrip(state, rtl, tmp_path)

    assert "-input_supply_port" not in text
    assert "-port_map" not in text
    switch = next(iter(imported.power_switch.switches.values()))
    assert switch.input_supply_port == ""   # the one field that cannot come back
    assert switch.output_supply_port == "out_p"
    assert switch.control_port == "sleep_ctrl"
    assert imported.errors == []
    assert imported.warnings == []


def test_a_macro_attachment_round_trips_as_an_attachment_not_as_flattened_state(rtl, tmp_path):
    """Section 5's distinction, end to end: the generated
    `load_upf -instance <macro>` comes back as an attachment, and the
    macro's own domains never leak into this design's diagram."""
    imported, text = roundtrip(build_state(rtl), rtl, tmp_path)

    assert f"-instance {SRAM.split('/', 1)[1]}" in text or SRAM in text
    assert list(imported.macro_power_intent.attachments) == [SRAM]
    # The attached file declares PD_TOP/PD_PERIPH of its own; only this
    # design's two domains are present.
    assert {d.name for d in imported.domain.domains.values()} == {"PD_TOP", "PD_CPU"}


# --- CPF EXPORT only: CPF is no longer a Load Power Intent format, so
# nothing here maps a CPF file back -- this checks what the CPF writer emits.


def generate_cpf_text(state, rtl) -> str:
    from frontend.src.power_intent_view.power_intent_text_export import generate_cpf

    return generate_cpf(
        state["supply"],
        state["domain"],
        state["retention"],
        "2.0",
        state["switch"],
        state["level_shifter"],
        design_name=TOP,
        isolation_state=state["isolation"],
        port_attribute_state=state["port_attribute"],
        top_level_ports=_top_ports(rtl),
        power_state_table_state=state["power_state_table"],
        macro_power_intent_state=state["macro"],
    )


def _elaborate_cpf(text: str, tmp_path: Path):
    from backend.api import elaborate_power_intent

    path = tmp_path / "elaborate_only.cpf"
    path.write_text(text)
    return elaborate_power_intent(str(path), "cpf")


def test_the_generator_no_longer_emits_set_instance_at_all(rtl, tmp_path):
    """REPLACES a test that asserted `set_instance .` returns to the top
    scope. It does not: `.` is CPF's own DEFAULT HIERARCHY SEPARATOR
    (Si2 CPF 2.1, p. 44), so `set_instance .` named an instance literally
    called ".", and the string never appears in the spec. The elaborator
    still NORMALISES it for any file that has one -- reading tolerantly
    is separate from writing conservatively -- but this generator now
    scopes structurally, via set_design/end_design, which is what the
    hand-written golden CPF 2.0 does too.

    Audit findings M1 and C2."""
    text = generate_cpf_text(build_state(rtl), rtl)
    assert "set_instance" not in text
    assert "set_design " in text and text.rstrip().endswith("end_design")

    power_intent = _elaborate_cpf(text, tmp_path)
    assert all(not id_.startswith(".::") for id_ in power_intent.isolation_strategies)
    assert all(s.scope != "." for s in power_intent.isolation_strategies.values())


# --- a second UPF round trip ------------------------------------------


def test_re_generating_from_the_imported_state_is_stable(rtl, tmp_path):
    """A second trip changes nothing more than the first did -- the
    import is not slowly degrading the state (a construct silently
    dropped on trip two would not show up in a single round trip)."""
    first, _ = roundtrip(build_state(rtl), rtl, tmp_path)
    second_state = {
        "supply": first.supply,
        "domain": first.domain,
        "retention": first.retention,
        "switch": first.power_switch,
        "isolation": first.isolation,
        "level_shifter": first.level_shifter,
        "port_attribute": first.port_attribute,
        # Carried through, not blanked: the import now rebuilds the
        # table from the generated power state group, and under the
        # two-tier model a supply's states reach the file only VIA that
        # table -- so dropping it here would make trip two lose every
        # state and look like a regression that is really this test
        # throwing the data away.
        "power_state_table": first.power_state_table,
        "macro": first.macro_power_intent,
    }
    second = reload_generated(generate(second_state, rtl, output_dir=str(tmp_path)), rtl, tmp_path)

    assert second.errors == []
    assert second.warnings == []
    assert snapshot(second) == snapshot(first)


# --- the new power-state model ------------------------------------------


def test_supply_set_states_and_the_global_group_round_trip(rtl, tmp_path):
    """The two-tier model, end to end. Tier 1's derived states come back
    as the per-rail states they were composed from; Tier 2's group comes
    back as a group with one state per Power State Table row."""
    imported, text = roundtrip(build_state(rtl), rtl, tmp_path)

    # Tier 1 was written per SUPPLY SET, not per net.
    assert "add_power_state -supply SS_TOP" in text
    assert "add_power_state VDD" not in text
    assert "-complete" not in text  # confirmed: never emitted
    # Tier 2: ONE global group for the whole design.
    assert text.count("create_power_state_group") == 1
    assert f"add_power_state -group {TOP}_PST" in text

    # ...and it all reads back, with no errors.
    assert [d.message for d in imported.errors] == []


def test_a_nested_supply_sets_states_are_bare_and_round_trip(rtl, tmp_path):
    """SS_CPU is composed on a nested hierarchy, but -- like every
    construct -- it is DECLARED at the root scope, so both power-state
    tiers name it bare. The importer recovers its hierarchy from the
    domain it is the primary supply of.

    Asserted on the TEXT as well as on the round trip: a scope-qualified
    `u_sub/SS_CPU` would name a set that is not declared in u_sub."""
    imported, text = roundtrip(build_state(rtl), rtl, tmp_path)

    scope = CPU.split("/", 1)[1]
    assert "add_power_state -supply SS_CPU" in text
    assert "SS_CPU == STATE_" in text
    assert f"{scope}/SS_CPU" not in text
    assert "add_power_state -supply SS_TOP" in text

    # ...and the bare reference reads back to the same supply set, on the
    # same nested hierarchy, with no errors.
    assert [d.message for d in imported.errors] == []
    cpu_set = next(s for s in imported.domain.supply_sets.values() if s.name == "SS_CPU")
    assert (cpu_set.owner_path, cpu_set.power, cpu_set.ground) == (CPU, "VDD_CPU", "VSS")
    # VDD_CPU itself stays state-less: it is an Internal Supply, whose
    # states are derived from its source, never authored (see the model).
    cpu_rail = next(p for p in imported.supply.supply_port_placements.values() if p.name == "VDD_CPU")
    assert cpu_rail.is_internal_supply and cpu_rail.states == []


def test_a_rail_state_no_table_row_selects_does_not_survive(rtl, tmp_path):
    """A NAMED, asserted property of the model, not an accident.

    A rail's states are no longer written out independently -- they reach
    the file only by being composed into a Supply Set's derived states
    (Tier 1), which are read off the Power State Table's rows. So a state
    no row ever selects says nothing about any supply set, is part of no
    derived expression, and legitimately does not round-trip.

    The main fixture above deliberately selects every authored state in
    some row, so it round-trips completely; this one shows what happens
    when that is not true."""
    state = build_state(rtl)
    # Drop the row that was the only user of VDD's OFF state.
    state["power_state_table"].rows.pop("r3")
    imported, _ = roundtrip(state, rtl, tmp_path)

    vdd = next(p for p in imported.supply.supply_port_placements.values() if p.name == "VDD")
    assert [(s.state_type, s.voltage) for s in vdd.states] == [("FULL_ON", "1.20")]


def test_a_multi_bit_element_is_written_with_star_and_reads_back_whole(rtl, tmp_path):
    """`o_q` is a multi-bit signal with every bit selected, so it is
    written `u_sub/o_q[*]` (the reference golden's spelling) -- and comes
    back as the whole signal under its FULL path, top instance included."""
    state = build_state(rtl)
    imported, text = roundtrip(state, rtl, tmp_path)

    assert "u_sub/o_q[*]" in text
    assert [d.message for d in imported.errors] == []
    for strategies in (imported.retention.strategies, imported.isolation.strategies):
        assert {e for s in strategies.values() for e in s.elements} == {f"{CPU}/o_q"}


@pytest.mark.parametrize("direction", ["inputs", "outputs", "both"])
def test_an_applies_to_all_isolation_round_trips_without_elements(rtl, tmp_path, direction):
    """"Applies To All" Inputs/Outputs/Both is written `-applies_to <dir>`
    with no `-elements`, and reads back as the same broad choice -- not
    as an element list."""
    state = build_state(rtl)
    iso = state["isolation"].strategies["iso"]
    iso.applies_to, iso.elements = direction, []
    imported, text = roundtrip(state, rtl, tmp_path)

    block = text[text.index("set_isolation ISO_CPU"):].split("\n\n")[0]
    assert f"  -applies_to {direction} \\" in block
    assert "-elements" not in block
    assert [d.message for d in imported.errors] == []
    (back,) = imported.isolation.strategies.values()
    assert (back.applies_to, back.elements) == (direction, [])


def test_an_elements_level_shifter_writes_no_applies_to_and_reads_back_as_elements(rtl, tmp_path):
    """"Applies To All: Elements" is the dialog's own choice, not a UPF
    value: the rule is written with `-elements` only -- never
    `-applies_to elements`, which is not valid syntax -- and a rule with
    elements and no `-applies_to` reads back as "Elements"."""
    state = build_state(rtl)
    state["level_shifter"].strategies["ls"].applies_to = "elements"
    imported, text = roundtrip(state, rtl, tmp_path)

    block = text[text.index("set_level_shifter LS_CPU"):].split("\n\n")[0]
    assert "-applies_to" not in block
    assert "-elements {" in block
    assert [d.message for d in imported.errors] == []
    (back,) = imported.level_shifter.strategies.values()
    assert (back.applies_to, back.elements) == ("elements", [f"{CPU}/o_q"])


def test_a_bit_selected_isolation_signal_is_braced_and_reads_back(rtl, tmp_path):
    """Found preparing the aura_top example for release: an isolation
    signal such as `iso_en_w[2]` was written BARE, which Tcl -- every
    UPF reader -- treats as command substitution, rejecting the whole
    file with `invalid command name "2"`. It is now braced, and comes
    back as the literal signal name."""
    state = build_state(rtl)
    state["isolation"].strategies["iso"].isolation_control = "iso_en_w[2]"
    imported, text = roundtrip(state, rtl, tmp_path)

    assert "-isolation_signal {iso_en_w[2]}" in text
    assert [d.message for d in imported.errors] == []
    (back,) = imported.isolation.strategies.values()
    assert back.isolation_control == "iso_en_w[2]"


def test_a_plain_isolation_signal_stays_unbraced(rtl, tmp_path):
    """Only a value Tcl would misread is braced -- `iso_en` is written as
    is, like the reference golden's `-isolation_signal iso_n`."""
    text = generate(build_state(rtl), rtl, output_dir=str(tmp_path))
    assert "-isolation_signal iso_en \\" in text
