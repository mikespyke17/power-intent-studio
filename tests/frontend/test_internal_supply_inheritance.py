"""Internal Supply "effective states" -- explicit request: an Internal
Supply has NO independent editable States list; instead its effective
states are DERIVED by walking the existing power-net connectivity chain
back to its ultimate source Supply Port, additively picking up one
"SWITCH-OFF (<power_switch_name>)" state per Power Switch encountered
along the way. Flagged by the user as the highest-risk part of this
whole feature -- see power_switch_overlay.py's own trace_upstream_source()/
resolve_effective_states() docstrings for the full design.
"""

from PySide6.QtGui import QColor

from frontend.src.power_intent_view.power_domain_overlay import SupplySetPlacement
from frontend.src.power_intent_view.power_intent_tab import PowerIntentTab
from frontend.src.power_intent_view.power_switch_overlay import SWITCH_OFF_STATE_NAME, power_switch_connector_key, resolve_effective_states
from frontend.src.power_intent_view.supply_port_overlay import SupplyPortState
from frontend.src.shared.mock_backend import build_mock_design


def _tab_and_view(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    return tab, tab.diagram._view


def _effective(view, icon):
    return resolve_effective_states(icon.placement, view._shared_state, view._power_switch_state.switches)


# --- (a): no switch in chain, unconnected -------------------------------------


def test_unconnected_internal_supply_has_no_effective_states(qtbot):
    tab, view = _tab_and_view(qtbot)
    internal = view.add_internal_supply(view._boxes["top/u_core"], "Y", QColor("#ff0000"))

    assert _effective(view, internal) == []


# --- (a): no switch in chain, direct wire -------------------------------------


def test_direct_wire_with_no_switch_shows_exactly_the_sources_states(qtbot):
    tab, view = _tab_and_view(qtbot)
    source = view.add_supply_port(
        view._boxes["top"],
        "VDD",
        QColor("#ff0000"),
        states=[SupplyPortState("FULL_ON", "1.0"), SupplyPortState("OFF", name=SWITCH_OFF_STATE_NAME)],
    )
    internal = view.add_internal_supply(view._boxes["top/u_core"], "Y", QColor("#ff0000"))
    view._shared_state.supply_connector_placements.append((source.key, internal.key))

    assert _effective(view, internal) == source.placement.states


# --- (b): one switch in chain -------------------------------------------------


def test_one_switch_in_chain_shows_source_voltage_states_plus_one_switch_off_state(qtbot):
    tab, view = _tab_and_view(qtbot)
    source = view.add_supply_port(view._boxes["top"], "VDD", QColor("#ff0000"), states=[SupplyPortState("FULL_ON", "1.0")])
    internal = view.add_internal_supply(view._boxes["top/u_core"], "Y", QColor("#ff0000"))
    view.create_power_domain(view._boxes["top/u_core"], "PD_CORE", QColor("#00ff00"), 0.5, None, [], {})
    switch = view.add_power_switch(internal, "SW_A", "in", "out", "ctrl", None, "on", "off")
    view._shared_state.supply_connector_placements.append((power_switch_connector_key(switch.id, internal.key), source.key))

    effective = _effective(view, internal)

    assert SupplyPortState("FULL_ON", "1.0") in effective
    assert SupplyPortState("OFF", name=SWITCH_OFF_STATE_NAME) in effective
    assert len(effective) == 2


# --- (c): two cascaded switches ------------------------------------------------


def test_two_cascaded_switches_still_produce_exactly_one_off_state(qtbot):
    tab, view = _tab_and_view(qtbot)
    source = view.add_supply_port(view._boxes["top"], "VDD", QColor("#ff0000"), states=[SupplyPortState("FULL_ON", "1.0")])
    view.create_power_domain(view._boxes["top/u_core"], "PD_CORE", QColor("#00ff00"), 0.5, None, [], {})
    y = view.add_internal_supply(view._boxes["top/u_core"], "Y", QColor("#ff0000"))
    z = view.add_internal_supply(view._boxes["top/u_core"], "Z", QColor("#ff0000"))
    switch_a = view.add_power_switch(y, "SW_A", "in", "out", "ctrl", None, "on", "off")
    switch_b = view.add_power_switch(z, "SW_B", "in", "out", "ctrl", None, "on", "off")
    view._shared_state.supply_connector_placements.append((power_switch_connector_key(switch_a.id, y.key), source.key))
    view._shared_state.supply_connector_placements.append((power_switch_connector_key(switch_b.id, z.key), y.key))

    effective = _effective(view, z)

    # REDESIGNED: cascaded switches collapse to AT MOST ONE OFF in
    # total, not one per switch. Two switches in series can only produce
    # one "unpowered" condition, so the extra entry said nothing -- and
    # the state carries no switch name anymore either.
    assert effective == [SupplyPortState("FULL_ON", "1.0"), SupplyPortState("OFF", name=SWITCH_OFF_STATE_NAME)]


# --- (d): editing the source's states later live-updates the inherited list --


def test_editing_the_sources_states_live_updates_the_internal_supplys_inherited_list(qtbot):
    tab, view = _tab_and_view(qtbot)
    source = view.add_supply_port(view._boxes["top"], "VDD", QColor("#ff0000"), states=[SupplyPortState("FULL_ON", "1.0")])
    internal = view.add_internal_supply(view._boxes["top/u_core"], "Y", QColor("#ff0000"))
    view._shared_state.supply_connector_placements.append((source.key, internal.key))
    assert len(_effective(view, internal)) == 1

    view.edit_supply_port(
        source,
        "VDD",
        QColor("#ff0000"),
        states=[SupplyPortState("FULL_ON", "1.0"), SupplyPortState("FULL_ON", "0.6")],
    )

    effective = _effective(view, internal)
    assert SupplyPortState("FULL_ON", "0.6") in effective
    assert len(effective) == 2


# --- (e): renaming a Power Switch in the chain live-updates its derived name --


def test_renaming_a_power_switch_does_not_change_the_derived_off_state(qtbot):
    """RETIRED BEHAVIOUR, asserted in its new form rather than deleted.

    A switch used to contribute a state literally NAMED
    "SWITCH-OFF (<switch name>)", so renaming the switch renamed the
    state. States carry no name at all now, and a switch contributes a
    plain OFF -- so a rename is correctly invisible here. The switch name
    survives only in the Inherited Power States preview's own provenance
    suffix, which is what that preview is for."""
    tab, view = _tab_and_view(qtbot)
    source = view.add_supply_port(view._boxes["top"], "VDD", QColor("#ff0000"), states=[SupplyPortState("FULL_ON", "1.0")])
    internal = view.add_internal_supply(view._boxes["top/u_core"], "Y", QColor("#ff0000"))
    view.create_power_domain(view._boxes["top/u_core"], "PD_CORE", QColor("#00ff00"), 0.5, None, [], {})
    switch = view.add_power_switch(internal, "SW_A", "in", "out", "ctrl", None, "on", "off")
    view._shared_state.supply_connector_placements.append((power_switch_connector_key(switch.id, internal.key), source.key))

    before = _effective(view, internal)
    view.edit_power_switch(switch.id, "SW_RENAMED", "in", "out", "ctrl", None, "on", "off")
    after = _effective(view, internal)

    assert before == after == [SupplyPortState("FULL_ON", "1.0"), SupplyPortState("OFF", name=SWITCH_OFF_STATE_NAME)]


# --- REGRESSION (explicit, exact golden repro): a Power Switch chain that ---
# --- passes through a CHANGE 2 ancestor-shared Supply Net must still show ---
# --- the real source's own inherited states, not just the switch's own -----


def test_switch_chain_through_a_change2_shared_supply_net_still_inherits_the_real_sources_states(qtbot):
    """The exact golden repro (explicit request, replaces the earlier
    vague report): PD_TOP/SS_VDD (VDD=Power w/ ON 1.2V + OFF states,
    VSS=Ground) at the top level; PD_SUB at "top/u_core" sharing SS_VDD
    as its own Secondary (CHANGE 2's own ancestor-sharing dropdown) and
    composing a brand-new SS_VDD_INT (VDD_INT=Power, VSS=Ground -- the
    ground name reused/shared within this SAME new domain) as its own
    Primary; a Power Switch on VDD_INT, with a power net drawn from the
    switch's own top (Supply In) anchor to VDD at the TOP level.

    ROOT CAUSE (found via this exact repro): CHANGE 2's own
    `_auto_link_cross_hierarchy_supply_sets()` gives the real, root-level
    "VDD" Supply Port a SECOND, unrelated connector of its own -- an
    auto-linked "mirror" copy of the identical net, materialized as a
    separate, empty-states Internal Supply on "top/u_core" (the sharing
    hierarchy). `trace_upstream_source()`'s own walk reached the REAL
    "VDD" via the switch chain correctly, but then kept walking (nothing
    previously told it a resolved, non-Internal-Supply placement is
    already a TERMINAL source) and wandered onto that empty-states
    mirror copy instead -- so only "SWITCH-OFF (<name>)" ever showed up,
    silently dropping the real source's own inherited "ON"/"OFF". Fixed
    by stopping trace_upstream_source() the moment it resolves a
    genuine, non-Internal-Supply Supply Port (see that function's own
    updated docstring)."""
    tab, view = _tab_and_view(qtbot)
    top_box = view._boxes["top"]
    sub_box = view._boxes["top/u_core"]

    ss_top = SupplySetPlacement(id="ss_top", name="SS_VDD", power="VDD", ground="VSS", owner_path="top")
    view.create_power_domain(
        top_box,
        "PD_TOP",
        QColor("#4caf50"),
        0.5,
        "ss_top",
        [],
        {"ss_top": ss_top},
        new_supply_ports={"VDD": QColor("#ff0000"), "VSS": QColor("#0000ff")},
        new_supply_port_states={
            "VDD": [SupplyPortState("FULL_ON", "1.2"), SupplyPortState("OFF", name=SWITCH_OFF_STATE_NAME)]
        },
    )

    ss_sub = SupplySetPlacement(id="ss_sub", name="SS_VDD_INT", power="VDD_INT", ground="VSS", owner_path="top/u_core")
    view.create_power_domain(
        sub_box, "PD_SUB", QColor("#2196f3"), 0.5, "ss_sub", ["ss_top"], {"ss_sub": ss_sub}, new_supply_ports={"VDD_INT": QColor("#00aa00")}
    )
    # The direct consequence of sharing SS_VDD as PD_SUB's own Secondary
    # (CHANGE 2) -- same call CreatePowerDomainDialog's own accept flow
    # makes, bypassing the dialog chain per this codebase's own
    # established "directly-testable core" test precedent.
    view._auto_link_cross_hierarchy_supply_sets(ss_top, target_owner_paths=["top/u_core"])

    sub_placements = {p.name: p for p in view._shared_state.supply_port_placements.values() if p.owner_path == "top/u_core"}
    vdd_int = sub_placements["VDD_INT"]
    vdd_top = next(p for p in view._shared_state.supply_port_placements.values() if p.name == "VDD" and p.owner_path == "top")

    switch = view.add_power_switch(view._supply_port_icons[vdd_int.id], "SW_A", "in", "out", "ctrl", None, "on", "off")
    switch_icon = view._power_switch_icons[switch.id][0]
    connected = view.try_connect_supply_ports(switch_icon, view._supply_port_icons[vdd_top.id])
    assert connected is True

    effective = resolve_effective_states(vdd_int, view._shared_state, view._power_switch_state.switches)

    assert effective == [SupplyPortState("FULL_ON", "1.2"), SupplyPortState("OFF", name=SWITCH_OFF_STATE_NAME)]


# --- (f): inherited states carry their NAMES ----------------------------


def test_f_inherited_states_show_their_real_names_and_a_named_switch_off(qtbot):
    """The preview must reflect the SOURCE's own definitions -- names
    included, not just their physics -- and the tool-contributed OFF
    carries the confirmed "SWITCH-OFF" name."""
    from frontend.src.power_intent_view.power_switch_overlay import describe_inherited_states

    tab, view = _tab_and_view(qtbot)
    source = view.add_supply_port(
        view._boxes["top"], "VDD", QColor("#ff0000"),
        states=[SupplyPortState("FULL_ON", "1.2", name="ON"),
                SupplyPortState("FULL_ON", "0.6", name="LOW_POWER")],
    )
    internal = view.add_internal_supply(view._boxes["top/u_core"], "Y", QColor("#ff0000"))
    view.create_power_domain(view._boxes["top/u_core"], "PD_CORE", QColor("#00ff00"), 0.5, None, [], {})
    switch = view.add_power_switch(internal, "SW_A", "in", "out", "ctrl", None, "on", "off")
    view._shared_state.supply_connector_placements.append(
        (power_switch_connector_key(switch.id, internal.key), source.key)
    )

    effective = _effective(view, internal)
    assert [(s.name, s.state_type, s.voltage) for s in effective] == [
        ("ON", "FULL_ON", "1.2"),
        ("LOW_POWER", "FULL_ON", "0.6"),
        (SWITCH_OFF_STATE_NAME, "OFF", None),
    ]

    # ...and the read-only preview prints those names.
    lines = describe_inherited_states(internal.placement, view._shared_state, view._power_switch_state.switches)
    assert any(line.startswith("ON:") for line in lines)
    assert any(line.startswith("LOW_POWER:") for line in lines)
    assert any(SWITCH_OFF_STATE_NAME in line for line in lines)


def test_f_a_source_state_already_called_switch_off_is_flagged_as_a_duplicate(qtbot):
    """The confirmed name was chosen to avoid colliding with a plausible
    user "OFF". If a source genuinely has its own "SWITCH-OFF", the
    per-supply duplicate-name check is what catches it -- the right
    outcome, rather than silently producing two identical names."""
    from frontend.src.power_intent_view.supply_port_overlay import duplicate_state_names

    colliding = [
        SupplyPortState("FULL_ON", "1.2", name="ON"),
        SupplyPortState("OFF", name=SWITCH_OFF_STATE_NAME),
        SupplyPortState("UNDETERMINED", name=SWITCH_OFF_STATE_NAME),
    ]
    assert duplicate_state_names(colliding) == [SWITCH_OFF_STATE_NAME]

    # A source that already has an OFF gets NO switch-contributed one at
    # all, so the ordinary case cannot collide in the first place.
    tab, view = _tab_and_view(qtbot)
    source = view.add_supply_port(
        view._boxes["top"], "VDD", QColor("#ff0000"),
        states=[SupplyPortState("OFF", name=SWITCH_OFF_STATE_NAME)],
    )
    internal = view.add_internal_supply(view._boxes["top/u_core"], "Y", QColor("#ff0000"))
    view.create_power_domain(view._boxes["top/u_core"], "PD_CORE", QColor("#00ff00"), 0.5, None, [], {})
    switch = view.add_power_switch(internal, "SW_A", "in", "out", "ctrl", None, "on", "off")
    view._shared_state.supply_connector_placements.append(
        (power_switch_connector_key(switch.id, internal.key), source.key)
    )

    effective = _effective(view, internal)
    assert [s.name for s in effective] == [SWITCH_OFF_STATE_NAME]
    assert duplicate_state_names(effective) == []
