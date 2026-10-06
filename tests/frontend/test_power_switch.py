"""Power Switch -- explicit request: a new diagram element attaching to
a Supply Port/Internal Supply icon (never a hierarchy block directly).
See power_switch_overlay.py's own module docstring for the full
architecture (two anchor points, "Supply Out" = automatic/fixed to the
target, "Supply In" = derived from a manually-drawn Supply Net
connector).

This file starts with the data-model/validation-rule layer (Stage 1 of
the staged commit plan) -- diagram rendering, placement, dialog, and
context-menu integration each get their own dedicated test file/module
as those stages land."""

from frontend.src.power_intent_view.power_switch_overlay import (
    PowerSwitchDiagramState,
    PowerSwitchPlacement,
    copy_power_switch_diagram_state,
    find_switch_for_target,
    restore_power_switch_diagram_state,
)

# --- data model: PowerSwitchDiagramState copy/restore -----------------------


def test_copy_power_switch_diagram_state_is_a_real_independent_copy():
    state = PowerSwitchDiagramState()
    state.switches["sw1"] = PowerSwitchPlacement(id="sw1", name="SW1", target_id="port1", owner_path="top", power_domain_id="d1")

    snapshot = copy_power_switch_diagram_state(state)
    state.switches["sw1"].name = "MUTATED"

    assert snapshot.switches["sw1"].name == "SW1"


def test_restore_power_switch_diagram_state_mutates_in_place_not_by_reference():
    state = PowerSwitchDiagramState()
    state.switches["sw1"] = PowerSwitchPlacement(id="sw1", name="SW1", target_id="port1", owner_path="top", power_domain_id="d1")
    snapshot = copy_power_switch_diagram_state(state)
    same_object = state

    state.switches.clear()
    restore_power_switch_diagram_state(state, snapshot)

    assert state is same_object
    assert "sw1" in state.switches


# --- validation rule A: max ONE Power Switch per Supply Port/Internal Supply -


def test_find_switch_for_target_returns_none_when_no_switches_exist():
    assert find_switch_for_target({}, "port1") is None


def test_find_switch_for_target_finds_the_switch_attached_to_this_target():
    switches = {
        "sw1": PowerSwitchPlacement(id="sw1", name="SW1", target_id="port1", owner_path="top", power_domain_id="d1"),
        "sw2": PowerSwitchPlacement(id="sw2", name="SW2", target_id="port2", owner_path="top", power_domain_id="d1"),
    }

    found = find_switch_for_target(switches, "port1")

    assert found is not None
    assert found.id == "sw1"


def test_find_switch_for_target_returns_none_when_only_other_targets_are_switched():
    switches = {"sw1": PowerSwitchPlacement(id="sw1", name="SW1", target_id="port1", owner_path="top", power_domain_id="d1")}

    assert find_switch_for_target(switches, "port2") is None
