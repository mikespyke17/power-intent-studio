"""Four UPF semantic equivalences, each specified explicitly.

Every one of these says "construct A means the same thing as construct
B", where B is something this app already models. The tests are written
as equivalences for that reason: each asserts that the two spellings
produce the SAME imported state, rather than merely that the new one
parses.

Reaches the real backend, like the other two fixture suites, because the
question is what the elaborator and importer do with real UPF.
"""

from __future__ import annotations

import textwrap

import pytest

from frontend.src.backend_integration.backend_adapter import adapt_power_intent
from frontend.src.power_intent_view.power_intent_import import _states_for_supply

DESIGN_HEADER = "upf_version 3.1\n"


def elaborate(tmp_path, body: str):
    """Each case gets its own directory, so two spellings of the same
    design can be elaborated side by side and compared."""
    from backend.api import elaborate_power_intent

    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "case.upf"
    path.write_text(DESIGN_HEADER + textwrap.dedent(body))
    return adapt_power_intent(elaborate_power_intent(str(path), "upf"))


def only_domain(design):
    return next(iter(design.domains.values()))


# --- MAPPING 1: -handle <PD>.primary == -supply {primary SS} -------------


def test_mapping_1_primary_handle_equals_the_supply_primary_flag(tmp_path):
    via_associate = elaborate(tmp_path / "a", """
        create_power_domain PD_TOP -elements {.}
        create_supply_net VDD
        create_supply_net VSS
        create_supply_set SS_VDD -supply {power VDD} -supply {ground VSS}
        associate_supply_set SS_VDD -handle PD_TOP.primary
    """)
    via_flag = elaborate(tmp_path / "b", """
        create_supply_net VDD
        create_supply_net VSS
        create_supply_set SS_VDD -supply {power VDD} -supply {ground VSS}
        create_power_domain PD_TOP -elements {.} -supply {primary SS_VDD}
    """)

    assert only_domain(via_associate).primary_supply == "SS_VDD"
    assert only_domain(via_associate).primary_supply == only_domain(via_flag).primary_supply


# --- MAPPING 2: -handle <PD>.<any> == -available_supplies {SS} -----------


def test_mapping_2_a_non_primary_handle_equals_available_supplies(tmp_path):
    """The specified equivalence. An earlier version REJECTED every
    non-primary handle outright: right that folding one into `primary`
    would attach the wrong supply set, wrong that the alternative was
    refusal -- a secondary association belongs in available_supplies,
    and rejecting it failed perfectly valid files."""
    via_associate = elaborate(tmp_path / "a", """
        create_power_domain PD_TOP -elements {.}
        create_supply_net VDD
        create_supply_net VSS
        create_supply_set SS_VDD -supply {power VDD} -supply {ground VSS}
        associate_supply_set SS_VDD -handle PD_TOP.aon
    """)
    via_flag = elaborate(tmp_path / "b", """
        create_supply_net VDD
        create_supply_net VSS
        create_supply_set SS_VDD -supply {power VDD} -supply {ground VSS}
        create_power_domain PD_TOP -elements {.} -available_supplies {SS_VDD}
    """)

    assert only_domain(via_associate).available_supplies == ["SS_VDD"]
    assert only_domain(via_associate).available_supplies == only_domain(via_flag).available_supplies
    assert only_domain(via_associate).primary_supply is None  # NOT folded into primary


def test_mapping_2_primary_and_secondary_handles_coexist(tmp_path):
    design = elaborate(tmp_path, """
        create_power_domain PD_TOP -elements {.}
        create_supply_net VDD
        create_supply_net VBAK
        create_supply_net VSS
        create_supply_set SS_MAIN -supply {power VDD}  -supply {ground VSS}
        create_supply_set SS_AON  -supply {power VBAK} -supply {ground VSS}
        create_supply_set SS_RET  -supply {power VBAK} -supply {ground VSS}
        associate_supply_set SS_MAIN -handle PD_TOP.primary
        associate_supply_set SS_AON  -handle PD_TOP.aon
        associate_supply_set SS_RET  -handle PD_TOP.default_retention
    """)
    domain = only_domain(design)

    assert domain.primary_supply == "SS_MAIN"
    # Appended, in order: several handles can associate several sets.
    assert domain.available_supplies == ["SS_AON", "SS_RET"]


# --- MAPPING 3: a supply-SET state == per-rail states -------------------


def test_mapping_3_a_supply_set_state_decomposes_into_per_rail_states(tmp_path):
    """The specified equivalence, verbatim: one supply-set state with a
    -supply_expr means exactly the per-net states it describes.

    Without this a file declaring states the supply-set way -- the
    idiomatic UPF 3.x style -- imported with NO states on any supply at
    all, silently: they existed in the IR, attached to a set name no
    supply lookup would ever match."""
    via_set = elaborate(tmp_path / "a", """
        create_supply_net VDD
        create_supply_net VSS
        create_supply_set SS_VDD -supply {power VDD} -supply {ground VSS}
        add_power_state -supply SS_VDD \\
            -state {ON \\
                -supply_expr {power == {FULL_ON 1.2} && ground == {FULL_ON 0.0}} \\
                -simstate NORMAL \\
            } \\
            -state {OFF \\
                -supply_expr {power == OFF && ground == {FULL_ON 0.0}} \\
                -simstate CORRUPT \\
            } \\
            -complete
    """)
    via_nets = elaborate(tmp_path / "b", """
        create_supply_net VDD
        create_supply_net VSS
        create_supply_set SS_VDD -supply {power VDD} -supply {ground VSS}
        add_power_state VDD -state {FULL_ON 1.2}
        add_power_state VDD -state {OFF}
        add_power_state VSS -state {FULL_ON 0.0}
    """)

    def summary(design, net):
        # States have no names now: the TYPE is the identity, drawn from
        # UPF's own vocabulary, so a rail state maps with no translation.
        return [(s.state_type, s.voltage) for s in _states_for_supply(design, net)]

    assert summary(via_set, "VDD") == [("FULL_ON", "1.2"), ("OFF", None)]
    assert summary(via_set, "VSS") == [("FULL_ON", "0.0")]
    # ...and that is exactly what the per-net spelling produces.
    assert summary(via_set, "VDD") == summary(via_nets, "VDD")
    assert summary(via_set, "VSS") == summary(via_nets, "VSS")


def test_mapping_3_the_rail_state_type_comes_from_the_term_not_the_set_state(tmp_path):
    """`-state {ON -supply_expr {power == {FULL_ON 0.9} ...}}` declares a
    rail state of TYPE FULL_ON, not one called ON. Easy to get backwards,
    and the equivalence above is what settles it -- doubly so now that
    the leading token IS the rail's own type vocabulary."""
    design = elaborate(tmp_path, """
        create_supply_net VDD
        create_supply_net VSS
        create_supply_set SS -supply {power VDD} -supply {ground VSS}
        add_power_state -supply SS -state {ON -supply_expr {power == {FULL_ON 0.9}}}
    """)

    assert [(s.state_type, s.voltage) for s in _states_for_supply(design, "VDD")] == [("FULL_ON", "0.9")]


def test_mapping_3_a_rail_named_by_several_set_states_is_not_duplicated(tmp_path):
    """A set's ground is typically FULL_ON in every one of its states, so
    the same rail state arrives once per set-level state."""
    design = elaborate(tmp_path, """
        create_supply_net VDD
        create_supply_net VSS
        create_supply_set SS -supply {power VDD} -supply {ground VSS}
        add_power_state -supply SS \\
            -state {ON  -supply_expr {power == {FULL_ON 1.0} && ground == {FULL_ON 0.0}}} \\
            -state {OFF -supply_expr {power == OFF && ground == {FULL_ON 0.0}}}
    """)

    assert [(s.state_type, s.voltage) for s in _states_for_supply(design, "VSS")] == [("FULL_ON", "0.0")]


def test_mapping_3_nwell_and_pwell_terms_reach_their_own_rails(tmp_path):
    """The decomposition keys off the supply-set FUNCTION, so it works
    for every function the set declares, not just power/ground."""
    design = elaborate(tmp_path, """
        create_supply_net VDD
        create_supply_net VSS
        create_supply_net VNW
        create_supply_set SS \\
            -supply {power VDD} -supply {ground VSS} -supply {nwell VNW}
        add_power_state -supply SS \\
            -state {ON -supply_expr {power == {FULL_ON 1.0} && nwell == {BIASED 1.8}}}
    """)

    # BIASED is outside the vocabulary, so it is kept as a levelled
    # state rather than having a level invented or discarded.
    assert [(s.state_type, s.voltage) for s in _states_for_supply(design, "VNW")] == [("FULL_ON", "1.8")]


def test_mapping_3_simstate_is_accepted_and_an_unknown_nested_flag_is_not(tmp_path):
    """-simstate parses and is skipped (no IR field -- simulation-only
    constructs are out of scope by standing decision), but an UNKNOWN
    nested flag is a hard error: the "unrecognized flags must be
    rejected" rule, applied one level down."""
    design = elaborate(tmp_path / "ok", """
        create_supply_net VDD
        create_supply_set SS -supply {power VDD}
        add_power_state -supply SS -state {ON -supply_expr {power == {FULL_ON 1.0}} -simstate NORMAL}
    """)
    assert [(s.state_type, s.voltage) for s in _states_for_supply(design, "VDD")] == [("FULL_ON", "1.0")]

    with pytest.raises(Exception) as excinfo:
        elaborate(tmp_path / "bad", """
            create_supply_net VDD
            create_supply_set SS -supply {power VDD}
            add_power_state -supply SS -state {ON -supply_expr {power == {FULL_ON 1.0}} -bogus X}
        """)
    assert "-bogus" in str(excinfo.value)


# --- MAPPING 4: -applies_to matches the rule's own elements -------------


@pytest.fixture(scope="module")
def rtl():
    from pathlib import Path

    from backend.api import elaborate_rtl
    from frontend.src.backend_integration.backend_adapter import adapt_design

    design_dir = Path(__file__).parent / "golden_simple10" / "design"
    return adapt_design(
        elaborate_rtl(
            [str(design_dir / "sram.v"), str(design_dir / "sub.sv")],
            [str(design_dir / "sram.lib")],
            top="sram",
        )
    )


def _isolation_elements(rtl, applies_to: str, elements: str):
    """The elements an isolation rule ends up with, after -applies_to is
    matched against them."""
    from frontend.src.power_intent_view.power_intent_import import _elements_matching_applies_to

    return [
        e.rsplit("/", 1)[-1]
        for e in _elements_matching_applies_to(rtl, elements, applies_to)
    ]


def test_mapping_4_applies_to_outputs_keeps_only_the_output_elements(rtl):
    """`-applies_to` describes the same selection the element list does,
    so it is resolved INTO that list rather than carried as a separate
    field this GUI has nowhere to put."""
    mixed = ["sram/q", "sram/d", "sram/clk"]  # q is out; d and clk are in

    assert _isolation_elements(rtl, "outputs", mixed) == ["q"]
    assert _isolation_elements(rtl, "inputs", mixed) == ["d", "clk"]


def test_mapping_4_both_and_unset_keep_everything(rtl):
    mixed = ["sram/q", "sram/d"]

    assert _isolation_elements(rtl, "both", mixed) == ["q", "d"]
    assert _isolation_elements(rtl, "", mixed) == ["q", "d"]
    assert _isolation_elements(rtl, None, mixed) == ["q", "d"]


def test_mapping_4_an_unresolvable_element_is_kept_not_dropped(rtl):
    """Filtering on a guess would silently discard a port the file
    explicitly listed, which is worse than keeping one it arguably did
    not select."""
    assert _isolation_elements(rtl, "outputs", ["sram/not_a_port"]) == ["not_a_port"]
    assert _isolation_elements(rtl, "outputs", ["nowhere/q"]) == ["q"]


def test_mapping_4_applies_to_is_no_longer_reported_as_unmodelled():
    from frontend.src.power_intent_view.supported_features import Support, field_support

    entry = field_support("IsolationStrategy", "applies_to")
    assert entry is not None and entry.support is Support.MODELLED
