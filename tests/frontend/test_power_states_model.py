"""The redesigned power-states model: supply-set-level states plus one
global power state group.

Section 7's own list, one section per lettered item. These are the tests
that pin the MODEL itself; the per-surface consequences (dialog columns,
Properties display, tooltip text) live with their own widgets' suites.

The old model -- per-state names, a binary Voltage/Off type, one
distinctly-named SWITCH-OFF state per Power Switch, per-net independent
`add_power_state`, and a legacy-PST translation -- is gone. Where a test
below replaces one that asserted the old behaviour, it says so, because
"this used to work differently" is the thing a reader most needs.
"""

from __future__ import annotations

import pytest
from PySide6.QtGui import QColor

from frontend.src.power_intent_view.power_domain_overlay import (
    PowerDomainDiagramState,
    PowerDomainPlacement,
    SupplySetPlacement,
)
from frontend.src.power_intent_view.power_intent_tab import PowerIntentTab
from frontend.src.power_intent_view.power_intent_text_export import generate_cpf, generate_upf
from frontend.src.power_intent_view.power_state_table import PowerStateTableDiagramState, PowerStateTableRow
from frontend.src.power_intent_view.power_switch_overlay import (
    SWITCH_OFF_STATE_NAME,
    power_switch_connector_key,
    resolve_effective_states,
)
from frontend.src.power_intent_view.retention_overlay import RetentionDiagramState
from frontend.src.power_intent_view.supply_port_overlay import (
    SupplyPortDiagramState,
    SupplyPortPlacement,
    SupplyPortState,
)
from frontend.src.shared.mock_backend import build_mock_design

_C = QColor("#ff0000")


def _port(pid: str, name: str, states=None, **kwargs) -> SupplyPortPlacement:
    return SupplyPortPlacement(id=pid, owner_path="top", name=name, color=_C, states=states or [], **kwargs)


# --- (a) the worked example: STATE_N dedup, simstate, NO -complete -------


def _worked_example(rows=None):
    """SS_VDD + SS_VDD_INT, ON/RET/OFF -- the example the spec works
    through. VDD_INT is a plain port here, not an internal supply, so the
    table can select its states directly."""
    supply = SupplyPortDiagramState(supply_port_placements={
        "vdd": _port("vdd", "VDD", [SupplyPortState("FULL_ON", "1.2"), SupplyPortState("OFF", name=SWITCH_OFF_STATE_NAME)]),
        "vss": _port("vss", "VSS", [SupplyPortState("FULL_ON", "0.0")]),
        "vint": _port("vint", "VDD_INT", [SupplyPortState("FULL_ON", "1.2"), SupplyPortState("OFF", name=SWITCH_OFF_STATE_NAME)]),
    })
    domain = PowerDomainDiagramState(
        supply_sets={
            "ss": SupplySetPlacement(id="ss", name="SS_VDD", power="VDD", ground="VSS", owner_path="top", owner_paths=["top"]),
            "ssi": SupplySetPlacement(id="ssi", name="SS_VDD_INT", power="VDD_INT", ground="VSS", owner_path="top", owner_paths=["top"]),
        },
        domains={
            "d": PowerDomainPlacement(id="d", owner_path="top", owner_paths=["top"], name="PD_TOP", color=_C, primary_supply_set="ss"),
            "di": PowerDomainPlacement(id="di", owner_path="top/u_core", owner_paths=["top/u_core"], name="PD_CORE", color=_C, primary_supply_set="ssi"),
        },
    )
    table = PowerStateTableDiagramState(rows=rows or {
        "r1": PowerStateTableRow(id="r1", power_domain_id="d", state_name="ON",
                                 net_states={"vdd": "FULL_ON 1.2", "vss": "FULL_ON 0.0", "vint": "FULL_ON 1.2"}),
        "r2": PowerStateTableRow(id="r2", power_domain_id="d", state_name="RET",
                                 net_states={"vdd": "FULL_ON 1.2", "vss": "FULL_ON 0.0", "vint": "OFF"}),
        "r3": PowerStateTableRow(id="r3", power_domain_id="d", state_name="OFF",
                                 net_states={"vdd": "OFF", "vss": "FULL_ON 0.0", "vint": "OFF"}),
    })
    return supply, domain, table


def _upf(rows=None) -> str:
    supply, domain, table = _worked_example(rows)
    return generate_upf(supply, domain, RetentionDiagramState(), "3.1",
                        design_name="soc_top", power_state_table_state=table)


def test_a_tier_1_derives_one_state_per_distinct_rail_combination():
    """ON and RET both leave SS_VDD's own rails at 1.2/0.0, so they are
    ONE derived state reused -- not two. OFF differs, so it is a second."""
    text = _upf()
    block = text[text.index("add_power_state -supply SS_VDD \\"):text.index("add_power_state -supply SS_VDD_INT")]

    assert block.count("-state {") == 2
    assert "-supply_expr { power == {FULL_ON 1.2} && ground == {FULL_ON 0.0} }" in block
    assert "-supply_expr { power == {OFF} && ground == {FULL_ON 0.0} }" in block


def test_a_derived_states_are_named_in_order_of_first_appearance():
    text = _upf()

    assert "-state { STATE_1 \\" in text
    assert "-state { STATE_2 \\" in text


def test_a_simstate_is_corrupt_only_when_a_rail_is_off_or_undetermined():
    text = _upf()
    block = text[text.index("add_power_state -supply SS_VDD \\"):text.index("add_power_state -supply SS_VDD_INT")]

    assert block.count("-simstate NORMAL") == 1  # the 1.2/0.0 combination
    assert block.count("-simstate CORRUPT") == 1  # the one with power OFF


def test_a_complete_is_never_emitted():
    """CONFIRMED REVERSAL of the earlier worked example, which included
    it. The final behaviour omits it entirely."""
    assert "-complete" not in _upf()


def test_a_the_old_per_net_and_pst_generation_are_both_gone():
    text = _upf()

    assert "add_power_state VDD" not in text
    assert "create_pst" not in text
    assert "add_pst_state" not in text


def test_b_a_third_distinct_combination_does_not_collapse():
    """Section 7(b): dedup must not over-collapse."""
    rows = {
        "r1": PowerStateTableRow(id="r1", power_domain_id="d", state_name="ON",
                                 net_states={"vdd": "FULL_ON 1.2", "vss": "FULL_ON 0.0", "vint": "FULL_ON 1.2"}),
        "r2": PowerStateTableRow(id="r2", power_domain_id="d", state_name="LOW",
                                 net_states={"vdd": "PARTIAL_ON 0.9", "vss": "FULL_ON 0.0", "vint": "FULL_ON 1.2"}),
        "r3": PowerStateTableRow(id="r3", power_domain_id="d", state_name="OFF",
                                 net_states={"vdd": "OFF", "vss": "FULL_ON 0.0", "vint": "OFF"}),
    }
    text = _upf(rows)
    block = text[text.index("add_power_state -supply SS_VDD \\"):text.index("add_power_state -supply SS_VDD_INT")]

    assert block.count("-state {") == 3
    assert "-state { STATE_3 \\" in block
    assert "power == {PARTIAL_ON 0.9}" in block


def test_b_full_on_and_partial_on_at_the_same_voltage_stay_distinct():
    """They DISPLAY identically, so a model keyed on display text would
    wrongly collapse them."""
    rows = {
        "r1": PowerStateTableRow(id="r1", power_domain_id="d", state_name="A",
                                 net_states={"vdd": "FULL_ON 1.2", "vss": "FULL_ON 0.0", "vint": "FULL_ON 1.2"}),
        "r2": PowerStateTableRow(id="r2", power_domain_id="d", state_name="B",
                                 net_states={"vdd": "PARTIAL_ON 1.2", "vss": "FULL_ON 0.0", "vint": "FULL_ON 1.2"}),
    }
    block = _upf(rows)
    block = block[block.index("add_power_state -supply SS_VDD \\"):block.index("add_power_state -supply SS_VDD_INT")]

    assert block.count("-state {") == 2


# --- (c) Internal Supply + Power Switch: one OFF, never more ------------


def _chain(qtbot, source_states, switch_count):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    source = view.add_supply_port(view._boxes["top"], "VDD", _C, states=source_states)
    view.create_power_domain(view._boxes["top/u_core"], "PD_CORE", QColor("#00ff00"), 0.5, None, [], {})
    previous = source
    last = source
    for i in range(switch_count):
        node = view.add_internal_supply(view._boxes["top/u_core"], f"N{i}", _C)
        switch = view.add_power_switch(node, f"SW{i}", "in", "out", "ctrl", None, "on", "off")
        view._shared_state.supply_connector_placements.append(
            (power_switch_connector_key(switch.id, node.key), previous.key)
        )
        previous = node
        last = node
    return view, last


def test_c_one_switch_adds_exactly_one_plain_off(qtbot):
    """RETRACTS the old "SWITCH-OFF (<switch name>)" naming: the added
    state is a plain OFF in the same vocabulary as every other state,
    with no switch name in it."""
    view, node = _chain(qtbot, [SupplyPortState("FULL_ON", "1.0")], 1)

    effective = resolve_effective_states(node.placement, view._shared_state, view._power_switch_state.switches)
    assert effective == [SupplyPortState("FULL_ON", "1.0"), SupplyPortState("OFF", name=SWITCH_OFF_STATE_NAME)]


def test_c_a_source_that_already_has_off_gets_no_second_one(qtbot):
    view, node = _chain(qtbot, [SupplyPortState("FULL_ON", "1.0"), SupplyPortState("OFF", name=SWITCH_OFF_STATE_NAME)], 1)

    effective = resolve_effective_states(node.placement, view._shared_state, view._power_switch_state.switches)
    assert effective == [SupplyPortState("FULL_ON", "1.0"), SupplyPortState("OFF", name=SWITCH_OFF_STATE_NAME)]
    assert sum(1 for s in effective if s.state_type == "OFF") == 1


def test_c_two_cascaded_switches_still_produce_exactly_one_off(qtbot):
    """Two switches in series can only produce one "unpowered"
    condition, so the extra entries the old model added said nothing."""
    view, node = _chain(qtbot, [SupplyPortState("FULL_ON", "1.0")], 2)

    effective = resolve_effective_states(node.placement, view._shared_state, view._power_switch_state.switches)
    assert effective == [SupplyPortState("FULL_ON", "1.0"), SupplyPortState("OFF", name=SWITCH_OFF_STATE_NAME)]


def test_c_the_dedup_is_not_reported_as_a_conflict(qtbot):
    """Tool-derived, not user-facing: there is nothing to resolve."""
    from frontend.src.power_intent_view.power_switch_overlay import describe_inherited_states

    view, node = _chain(qtbot, [SupplyPortState("FULL_ON", "1.0")], 2)
    lines = describe_inherited_states(node.placement, view._shared_state, view._power_switch_state.switches)

    assert lines is not None
    # The switch-contributed line is NAMED now, so it leads with the name
    # rather than the type.
    assert sum(1 for line in lines if line.startswith("SWITCH-OFF:")) == 1
    # The switches are named only in the provenance suffix.
    assert any("SW0" in line and "SW1" in line for line in lines)


# --- (e) one global group across several domains ------------------------


def test_e_one_global_group_covers_every_domain_including_a_nested_one():
    """Section 5's confirmed scoping: ONE group for the whole design, not
    one per domain -- even though PD_CORE is nested inside PD_TOP."""
    text = _upf()

    assert text.count("create_power_state_group") == 1
    assert "create_power_state_group soc_top_PST" in text
    assert text.count("add_power_state -group soc_top_PST") == 1


def test_e_each_row_constrains_only_the_sets_it_speaks_about():
    """A row leaving a set's rails unselected says nothing about that
    set, and that set is omitted from the row's own -logic_expr rather
    than being constrained to something invented."""
    rows = {
        "r1": PowerStateTableRow(id="r1", power_domain_id="d", state_name="TOP_ONLY",
                                 net_states={"vdd": "FULL_ON 1.2", "vss": "FULL_ON 0.0"}),
    }
    text = _upf(rows)
    block = text[text.index("add_power_state -group"):]

    assert "SS_VDD == STATE_1" in block
    assert "SS_VDD_INT" not in block  # unconstrained, not invented


def test_e_every_row_becomes_one_state_of_the_single_group():
    text = _upf()
    block = text[text.index("add_power_state -group"):]

    for name in ("ON", "RET", "OFF"):
        assert f"-state {{ {name} \\" in block


# --- (f) CPF ------------------------------------------------------------


def _cpf(rows=None) -> str:
    supply, domain, table = _worked_example(rows)
    return generate_cpf(supply, domain, RetentionDiagramState(), "2.0",
                        design_name="soc_top", power_state_table_state=table)


def test_f_cpf_emits_one_nominal_condition_per_distinct_voltage():
    """CPF was ALREADY structurally two-tier, which is why it needed
    reshaping rather than inventing: create_nominal_condition is Tier 1
    and create_power_mode is Tier 2. Verified against the Si2 CPF 1.0
    specification directly."""
    text = _cpf()

    assert "create_nominal_condition -name COND_1 -voltage 1.2" in text
    assert "create_nominal_condition -name COND_2 -voltage 0.0" in text


def test_f_off_is_a_zero_volt_condition_not_a_state_flag():
    """Straight from the spec: "A power domain is switched off if it is
    associated with a nominal condition whose voltage is 0." CPF 1.0's
    own create_nominal_condition defines no -state option at all."""
    text = _cpf()

    assert "-voltage 0.0" in text
    assert "-state off" not in text


def test_f_power_modes_carry_real_domain_conditions_with_one_default():
    """The per-domain data -domain_conditions always needed only became
    available with this redesign's own supply-set derivation -- it used
    to be emitted as a comment."""
    text = _cpf()

    assert "create_power_mode -name ON -domain_conditions {PD_CORE@COND_1 PD_TOP@COND_1} -default" in text
    assert "create_power_mode -name RET -domain_conditions {PD_CORE@COND_2 PD_TOP@COND_1}" in text
    # Spec: one, and only one, default mode.
    # Counted over MODES: a root power DOMAIN carries `-default` too now.
    assert len([l for l in text.splitlines() if l.startswith("create_power_mode") and "-default" in l]) == 1


def test_f_cpf_reports_what_it_structurally_cannot_express():
    """A CPF nominal condition is a single VOLTAGE and a power mode maps
    DOMAINS -- so the ground/nwell/pwell rails and the supply-set
    grouping itself have no CPF equivalent. Reported, not fabricated."""
    text = _cpf()

    assert "CPF has no supply-set construct" in text
    assert "add_power_state -supply" not in text  # no UPF-only construct leaks in


# --- scope qualification of a nested Supply Set -------------------------
#
# Reported by using the app: a Supply Set composed on a nested hierarchy
# is DECLARED inside a `set_scope` block, but both power-state tiers are
# emitted at the root scope, where its bare name resolves to nothing.


def _nested_set_example():
    """SS_VDD at the root, SS_INT composed on `top/u_core`."""
    supply = SupplyPortDiagramState(supply_port_placements={
        "vdd": _port("vdd", "VDD", [SupplyPortState("FULL_ON", "1.2"), SupplyPortState("OFF", name=SWITCH_OFF_STATE_NAME)]),
        "vss": _port("vss", "VSS", [SupplyPortState("FULL_ON", "0.0")]),
        "vint": SupplyPortPlacement(
            id="vint", owner_path="top/u_core", name="VDD_INT", color=_C, is_internal_supply=True,
            states=[SupplyPortState("FULL_ON", "1.2"), SupplyPortState("OFF", name=SWITCH_OFF_STATE_NAME)],
        ),
    })
    domain = PowerDomainDiagramState(
        supply_sets={
            "ss": SupplySetPlacement(id="ss", name="SS_VDD", power="VDD", ground="VSS", owner_path="top"),
            "ssi": SupplySetPlacement(id="ssi", name="SS_INT", power="VDD_INT", ground="VSS", owner_path="top/u_core"),
        },
        domains={
            "d": PowerDomainPlacement(id="d", owner_path="top", name="PD_TOP", color=_C, primary_supply_set="ss"),
            "di": PowerDomainPlacement(id="di", owner_path="top/u_core", name="PD_CORE", color=_C, primary_supply_set="ssi"),
        },
    )
    table = PowerStateTableDiagramState(rows={
        "r1": PowerStateTableRow(id="r1", power_domain_id="d", state_name="ON",
                                 net_states={"vdd": "FULL_ON 1.2", "vss": "FULL_ON 0.0", "vint": "FULL_ON 1.2"}),
        "r2": PowerStateTableRow(id="r2", power_domain_id="d", state_name="OFF",
                                 net_states={"vdd": "OFF", "vss": "FULL_ON 0.0", "vint": "OFF"}),
    })
    return supply, domain, table


def test_a_nested_supply_sets_tier_1_state_names_it_bare():
    """Every supply set is declared at the root scope (the reference golden's
    flat form), so a nested one is referenced bare like any other
    -- a `u_core/SS_INT` would name a set u_core never declares."""
    supply, domain, table = _nested_set_example()
    text = generate_upf(supply, domain, RetentionDiagramState(), "3.1",
                        design_name="top", power_state_table_state=table)
    assert "add_power_state -supply SS_INT" in text
    assert "u_core/SS_INT" not in text
    assert "add_power_state -supply SS_VDD" in text


def test_a_nested_supply_set_is_named_bare_in_the_group_logic_expr():
    supply, domain, table = _nested_set_example()
    text = generate_upf(supply, domain, RetentionDiagramState(), "3.1",
                        design_name="top", power_state_table_state=table)
    group = text[text.index("create_power_state_group"):]
    assert "\n                SS_INT == STATE_1" in group
    assert "SS_VDD == STATE_1" in group
    assert "u_core/SS_INT" not in group


def test_a_multi_hierarchy_supply_set_is_declared_and_referenced_once():
    """NOT one declaration or reference per hierarchy: a spanning set is
    CREATED exactly once, at the root, and referenced by its bare name."""
    supply, domain, table = _nested_set_example()
    domain.supply_sets["ssi"] = SupplySetPlacement(
        id="ssi", name="SS_INT", power="VDD_INT", ground="VSS",
        owner_paths=["top/u_core", "top/u_other"],
    )
    text = generate_upf(supply, domain, RetentionDiagramState(), "3.1",
                        design_name="top", power_state_table_state=table)
    assert text.count("create_supply_set SS_INT") == 1
    assert text.count("add_power_state -supply SS_INT") == 1
    assert "u_core/SS_INT" not in text
    assert "u_other/SS_INT" not in text


# --- per-supply state NAMES (final spec) --------------------------------
#
# Name is back on the per-supply states table, and the boundary is the
# whole point: legacy `add_port_state`/`add_pst_state` and the Power
# State Table's own cells use it; modern Tier 1 does NOT and keeps
# deriving STATE_1/STATE_2/... Each of those is asserted separately
# below, because "accidentally started using Names in Tier 1" is exactly
# the regression this spec warns against.


def _named(name, state_type, voltage=None):
    return SupplyPortState(state_type, voltage, name=name)


def _named_states():
    """The spec's own worked example: three supplies, DVFS on two of
    them, names reused freely across supplies."""
    supply = SupplyPortDiagramState(supply_port_placements={
        "vdd": _port("vdd", "VDD", [_named("ON", "FULL_ON", "1.2"),
                                    _named("LOW_POWER", "FULL_ON", "0.6"),
                                    _named("OFF", "OFF")]),
        "vsub": _port("vsub", "VDD_SUB", [_named("ON", "FULL_ON", "1.2"),
                                          _named("LOW_POWER", "FULL_ON", "0.6"),
                                          _named("OFF", "OFF")]),
        "vss": _port("vss", "VSS", [_named("ON", "FULL_ON", "0.0")]),
    })
    domain = PowerDomainDiagramState(
        supply_sets={"ss": SupplySetPlacement(id="ss", name="SS_VDD", power="VDD",
                                              ground="VSS", owner_path="top")},
        domains={"pd": PowerDomainPlacement(id="pd", owner_path="top", name="PD_TOP",
                                            color=_C, primary_supply_set="ss")},
    )
    table = PowerStateTableDiagramState(rows={
        "r1": PowerStateTableRow(id="r1", power_domain_id="pd", state_name="ON",
                                 net_states={"vdd": "FULL_ON 1.2", "vsub": "FULL_ON 1.2",
                                             "vss": "FULL_ON 0.0"}),
        "r2": PowerStateTableRow(id="r2", power_domain_id="pd", state_name="IDLE",
                                 net_states={"vdd": "FULL_ON 0.6", "vsub": "FULL_ON 0.6",
                                             "vss": "FULL_ON 0.0"}),
        "r3": PowerStateTableRow(id="r3", power_domain_id="pd", state_name="RETENTION",
                                 net_states={"vdd": "FULL_ON 0.6", "vsub": "OFF",
                                             "vss": "FULL_ON 0.0"}),
    })
    return supply, domain, table


def test_c_the_same_name_on_two_different_supplies_is_no_conflict():
    """Uniqueness is PER SUPPLY. VDD and VDD_SUB both have "ON",
    "LOW_POWER" and "OFF" here, which is ordinary."""
    supply, _domain, _table = _named_states()
    for net in ("vdd", "vsub"):
        names = [s.name for s in supply.supply_port_placements[net].states]
        assert names == ["ON", "LOW_POWER", "OFF"]


def test_d_legacy_declares_and_references_the_users_own_names():
    """The spec's worked example, verbatim in shape: `add_port_state`
    declares each name with its value, and `add_pst_state` references
    those bare names positionally -- no inline voltages."""
    supply, domain, table = _named_states()
    text = generate_upf(supply, domain, RetentionDiagramState(), "3.1",
                        design_name="top", power_state_table_state=table,
                        legacy_power_states=True)

    assert "add_port_state VDD \\\n    -state {ON 1.2} \\\n    -state {LOW_POWER 0.6} \\\n    -state {OFF off}" in text
    assert "add_port_state VSS \\\n    -state {ON 0.0}" in text
    assert "create_pst PST -supplies {VDD VDD_SUB VSS}" in text
    # Bare NAMES in the PST rows, not "FULL_ON 1.2".
    pst_rows = [l for l in text.splitlines() if l.startswith("add_pst_state")]
    assert len(pst_rows) == 3
    assert pst_rows[0].split("-state")[1].split() == ["{ON", "ON", "ON}"]
    assert pst_rows[2].split("-state")[1].split() == ["{LOW_POWER", "OFF", "ON}"]
    assert "FULL_ON" not in "\n".join(pst_rows)


def test_e_tier_1_still_uses_generic_state_n_and_never_the_names():
    """The RETRACTION, asserted explicitly: Tier 1's combined supply-set
    states stay STATE_1/STATE_2/... A combined state spans several rails,
    so borrowing the power rail's name would collide whenever two
    combinations shared a power value but differed elsewhere."""
    supply, domain, table = _named_states()
    text = generate_upf(supply, domain, RetentionDiagramState(), "3.1",
                        design_name="top", power_state_table_state=table)

    tier1 = text[text.index("add_power_state -supply SS_VDD"):].split("\n\n")[0]
    assert "STATE_1" in tier1 and "STATE_2" in tier1
    # None of the authored per-net names leak into Tier 1.
    for name in ("ON", "LOW_POWER"):
        assert f"{{ {name} " not in tier1
    # ...and its expressions still carry the real Type/Voltage values.
    assert "power == {FULL_ON 1.2}" in tier1


def test_g_the_table_displays_names_while_generation_uses_type_and_voltage():
    """Section 5's split: the CELL shows the Name, the stored DATA stays
    the physical key, and Tier 1's `-supply_expr` is built from the
    latter."""
    from frontend.src.power_intent_view.supply_port_overlay import state_display, state_key

    supply, domain, table = _named_states()
    on_state = supply.supply_port_placements["vdd"].states[0]
    assert state_display(on_state) == "ON"          # what a cell shows
    assert state_key(on_state) == "FULL_ON 1.2"     # what the row stores

    text = generate_upf(supply, domain, RetentionDiagramState(), "3.1",
                        design_name="top", power_state_table_state=table)
    assert "power == {FULL_ON 1.2}" in text         # what generation uses


# --- legacy: the right COMMAND per net, and exact positional alignment --
#
# `add_port_state` is for an object that genuinely IS a port -- here, a
# plain Supply Port, the only kind this generator also emits a
# `create_supply_port` for. An Internal Supply and a Virtual Supply are
# each emitted as `create_supply_net` ALONE, so neither is port-backed.


def _mixed_net_design(qtbot):
    """One of each net kind, with the Internal Supply genuinely CONNECTED
    so it inherits real named states -- an unconnected one has none, and
    the bug this guards was invisible without the connection."""
    from PySide6.QtGui import QColor

    from frontend.src.power_intent_view.power_intent_tab import PowerIntentTab
    from frontend.src.shared.mock_backend import build_mock_design

    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view

    def st(name, state_type, voltage=None):
        return SupplyPortState(state_type, voltage, name=name)

    vdd = view.add_supply_port(view._boxes["top"], "VDD", QColor("#ff0000"),
                               states=[st("ON", "FULL_ON", "1.2"), st("OFF", "OFF")])
    sub = view.add_internal_supply(view._boxes["top/u_core"], "VDD_SUB", QColor("#ff0000"))
    view.try_connect_supply_ports(vdd, sub)
    virt = view.add_virtual_supply(view._boxes["top"], "VDD_V", QColor("#00ff00"),
                                   states=[st("ON", "FULL_ON", "1.2")])
    vss = view.add_supply_port(view._boxes["top"], "VSS", QColor("#000080"),
                               states=[st("ON", "FULL_ON", "0.0")])

    domain = view._domain_state
    domain.supply_sets["ss"] = SupplySetPlacement(id="ss", name="SS", power="VDD",
                                                  ground="VSS", owner_path="top")
    domain.domains["pd"] = PowerDomainPlacement(id="pd", owner_path="top", name="PD",
                                                color=_C, primary_supply_set="ss")
    keys = {"VDD": vdd.key, "VDD_SUB": sub.key, "VDD_V": virt.key, "VSS": vss.key}
    table = PowerStateTableDiagramState(rows={
        "r1": PowerStateTableRow(
            id="r1", power_domain_id="pd", state_name="FULL_ON",
            net_states={keys["VDD"]: "FULL_ON 1.2", keys["VDD_SUB"]: "FULL_ON 1.2",
                        keys["VDD_V"]: "FULL_ON 1.2", keys["VSS"]: "FULL_ON 0.0"}),
        "r2": PowerStateTableRow(
            id="r2", power_domain_id="pd", state_name="FULL_OFF",
            net_states={keys["VDD"]: "OFF", keys["VDD_SUB"]: "OFF",
                        keys["VDD_V"]: "FULL_ON 1.2", keys["VSS"]: "FULL_ON 0.0"}),
    })
    text = generate_upf(view._shared_state, domain, RetentionDiagramState(), "3.1",
                        view._power_switch_state, design_name="top",
                        power_state_table_state=table, legacy_power_states=True)
    return text


def test_a_an_internal_supply_net_uses_add_supply_state_not_add_port_state(qtbot):
    """The reported bug. An Internal Supply is `create_supply_net` with
    no port declaration anywhere, so it is not a port -- and it was
    missing from this section ENTIRELY before, because the generator read
    its authored `.states` (an Internal Supply authors none) rather than
    its effective ones."""
    text = _mixed_net_design(qtbot)

    assert "add_supply_state VDD_SUB \\" in text
    assert "add_port_state VDD_SUB" not in text
    # ...with the names it INHERITED from its source.
    block = text[text.index("add_supply_state VDD_SUB"):].split("\n\n")[0]
    assert "-state {ON 1.2}" in block and "-state {OFF off}" in block


def test_b_a_genuine_supply_port_still_uses_add_port_state(qtbot):
    text = _mixed_net_design(qtbot)
    assert "add_port_state VDD \\" in text
    assert "add_port_state VSS \\" in text
    assert "add_supply_state VDD \\" not in text


def test_c_a_virtual_supply_net_also_uses_add_supply_state(qtbot):
    """Follows from the same rule rather than from the repro: a Virtual
    Supply is emitted as a standard supply net, never a port."""
    text = _mixed_net_design(qtbot)
    assert "add_supply_state VDD_V \\" in text
    assert "add_port_state VDD_V" not in text


def test_d_every_pst_row_has_exactly_one_token_per_supply(qtbot):
    """The alignment property, asserted by COUNTING. The old fallback for
    an unnamed state was the physical key -- "FULL_ON 1.2", TWO tokens --
    which silently shifted every later column by one."""
    text = _mixed_net_design(qtbot)

    create = next(l for l in text.splitlines() if l.startswith("create_pst"))
    supplies = create.split("-supplies {")[1].rstrip("}").split()
    assert supplies == ["VDD", "VDD_SUB", "VDD_V", "VSS"]

    rows = [l for l in text.splitlines() if l.startswith("add_pst_state")]
    assert rows
    for row in rows:
        tokens = row.split("-state {")[1].rstrip("}").split()
        assert len(tokens) == len(supplies), row


def test_e_no_other_part_of_legacy_generation_assumes_a_net_is_port_backed(qtbot):
    """The broadening check, asserted rather than reasoned about.

    `# Port State` was the one place that named a command per net, and
    it is fixed. The other two legacy constructs carry no port
    assumption by construction -- `create_pst -supplies` takes supply
    NETS, and `add_pst_state` references state NAMES -- so both must
    list the non-port-backed nets exactly like the port-backed ones,
    with no `create_supply_port` appearing for them anywhere."""
    text = _mixed_net_design(qtbot)

    create = next(l for l in text.splitlines() if l.startswith("create_pst"))
    # The two nets with no port behind them are in the table on equal terms.
    assert "VDD_SUB" in create and "VDD_V" in create
    # ...and nothing anywhere declared a port for either of them.
    assert "create_supply_port VDD_V" not in text
    assert "create_supply_port VDD_SUB" not in text
    # Only the genuinely port-backed nets got a port declaration.
    ports = {l.split()[1] for l in text.splitlines() if l.startswith("create_supply_port ")}
    assert ports == {"VDD", "VSS"}


def test_f_an_unnamed_state_is_declared_under_a_derived_name_not_dropped(qtbot):
    """An IMPORTED design can carry states with no name -- the dialog
    requires one, an imported file need not have carried one. Skipping
    such a state left it undeclared, which in turn dropped every table
    row selecting it: silently, and without the row being wrong.

    It is declared under its TYPE instead, which is one token (the
    property `add_pst_state`'s positional list depends on) and states
    nothing the state does not already say."""
    from PySide6.QtGui import QColor

    from frontend.src.power_intent_view.power_intent_tab import PowerIntentTab
    from frontend.src.shared.mock_backend import build_mock_design

    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view

    # Two unnamed states of the SAME type on one net: the second must
    # not silently collide with the first.
    vdd = view.add_supply_port(view._boxes["top"], "VDD", QColor("#ff0000"),
                               states=[SupplyPortState("FULL_ON", "1.2"),
                                       SupplyPortState("FULL_ON", "0.9")])
    vss = view.add_supply_port(view._boxes["top"], "VSS", QColor("#000080"),
                               states=[SupplyPortState("FULL_ON", "0.0", name="ON")])

    domain = view._domain_state
    domain.supply_sets["ss"] = SupplySetPlacement(id="ss", name="SS", power="VDD",
                                                  ground="VSS", owner_path="top")
    domain.domains["pd"] = PowerDomainPlacement(id="pd", owner_path="top", name="PD",
                                                color=_C, primary_supply_set="ss")
    table = PowerStateTableDiagramState(rows={
        "r1": PowerStateTableRow(id="r1", power_domain_id="pd", state_name="HIGH",
                                 net_states={vdd.key: "FULL_ON 1.2", vss.key: "FULL_ON 0.0"}),
        "r2": PowerStateTableRow(id="r2", power_domain_id="pd", state_name="LOW",
                                 net_states={vdd.key: "FULL_ON 0.9", vss.key: "FULL_ON 0.0"}),
    })
    text = generate_upf(view._shared_state, domain, RetentionDiagramState(), "3.1",
                        view._power_switch_state, design_name="top",
                        power_state_table_state=table, legacy_power_states=True)

    block = text[text.index("add_port_state VDD"):].split("\n\n")[0]
    assert "-state {FULL_ON 1.2}" in block
    assert "-state {FULL_ON_2 0.9}" in block      # distinct, not a collision

    # Neither row was dropped, and each references its own derived name.
    rows = [l for l in text.splitlines() if l.startswith("add_pst_state")]
    assert len(rows) == 2
    assert "FULL_ON " in rows[0].split("-state {")[1]
    assert "FULL_ON_2" in rows[1]
    # The authored name on the other net is untouched by the derivation.
    assert "add_port_state VSS \\\n    -state {ON 0.0}" in text


def test_the_switch_added_off_state_is_written_switch_underscore_off_in_upf(qtbot):
    """The switch-contributed OFF is named "SWITCH-OFF" on screen, but `-`
    is not valid in a UPF state name: legacy UPF writes it "SWITCH_OFF",
    both where the state is declared (`add_supply_state`) and where a PST
    row references it (`add_pst_state`). Found on aura_top."""
    from frontend.src.power_intent_view.power_state_table import ordered_supply_nets

    view, node = _chain(qtbot, [SupplyPortState("FULL_ON", "1.0", name="ON")], 1)
    domain = view._domain_state
    domain.supply_sets["ss"] = SupplySetPlacement(id="ss", name="SS", power="N0", owner_path="top/u_core")
    domain.domains["pd"] = PowerDomainPlacement(id="pd", owner_path="top/u_core", name="PD", color=_C, primary_supply_set="ss")
    nets = {net.name: net.id for net in ordered_supply_nets(view._shared_state, domain)}
    table = PowerStateTableDiagramState(rows={
        "r1": PowerStateTableRow(id="r1", power_domain_id="pd", state_name="RUN",
                                 net_states={nets["VDD"]: "FULL_ON 1.0", nets["N0"]: "FULL_ON 1.0"}),
        "r2": PowerStateTableRow(id="r2", power_domain_id="pd", state_name="SLEEP",
                                 net_states={nets["VDD"]: "FULL_ON 1.0", nets["N0"]: "OFF"}),
    })
    text = generate_upf(view._shared_state, domain, RetentionDiagramState(), "3.1", view._power_switch_state,
                        design_name="top", power_state_table_state=table, legacy_power_states=True)

    assert "SWITCH-OFF" not in text
    assert "add_supply_state N0 \\\n    -state {ON 1.0} \\\n    -state {SWITCH_OFF off}" in text
    sleep = next(line for line in text.splitlines() if line.startswith("add_pst_state PST_SLEEP"))
    assert sleep.split("-state {")[1].rstrip("}").split() == ["ON", "SWITCH_OFF"]
    # On screen it keeps its name -- only the UPF spelling changes.
    assert SWITCH_OFF_STATE_NAME == "SWITCH-OFF"
