"""power_intent_view/power_state_table.py -- the directly-testable data
model/cascade core for the new "Power State Table" feature, against
mock/fixture data only. See test_power_state_table_tab.py for the UI-
level (QTableWidget, live diagramStateChanged wiring) tests."""

from PySide6.QtGui import QColor

from frontend.src.power_intent_view.power_domain_overlay import PowerDomainDiagramState, PowerDomainPlacement, SupplySetPlacement
from frontend.src.power_intent_view.power_state_table import (
    PowerStateTableDiagramState,
    add_row,
    clear_net_state_references,
    net_effective_state_keys,
    net_state_snapshot,
    ordered_supply_nets,
    reconcile_net_state_renames_and_removals,
    remove_row,
    remove_rows_for_domain,
    rename_net_state_references,
    validate_power_state_table,
)
from frontend.src.power_intent_view.power_switch_overlay import PowerSwitchDiagramState
from frontend.src.power_intent_view.supply_port_overlay import SupplyPortDiagramState, SupplyPortPlacement, SupplyPortState


def _port(id_, name, states=None):
    return SupplyPortPlacement(id=id_, owner_path="top", name=name, color=QColor("#ff0000"), states=states or [])


def _domain(id_, name):
    return PowerDomainPlacement(id=id_, owner_path="top", name=name, color=QColor("#00ff00"))


# --- ordered_supply_nets: Power-then-Ground grouping ------------------------


def test_ordered_supply_nets_groups_power_before_ground():
    supply_state = SupplyPortDiagramState(
        supply_port_placements={
            "vss": _port("vss", "VSS"),
            "vdd": _port("vdd", "VDD"),
        }
    )
    domain_state = PowerDomainDiagramState(supply_sets={"ss1": SupplySetPlacement(id="ss1", name="SS1", power="VDD", ground="VSS")})

    nets = ordered_supply_nets(supply_state, domain_state)

    assert [n.name for n in nets] == ["VDD", "VSS"]


def test_a_name_never_referenced_as_ground_defaults_to_the_power_group():
    supply_state = SupplyPortDiagramState(supply_port_placements={"vdd": _port("vdd", "VDD")})
    domain_state = PowerDomainDiagramState()  # no Supply Set references VDD as ground at all

    nets = ordered_supply_nets(supply_state, domain_state)

    assert [n.name for n in nets] == ["VDD"]


# --- net_effective_state_keys -----------------------------------------------


def test_net_effective_state_keys_reads_a_plain_ports_own_states():
    port = _port("p1", "VDD", states=[SupplyPortState("FULL_ON", "1.0"), SupplyPortState("OFF")])
    supply_state = SupplyPortDiagramState(supply_port_placements={"p1": port})

    assert net_effective_state_keys(port, supply_state, None) == ["FULL_ON 1.0", "OFF"]


# --- row management -----------------------------------------------------------


def test_add_row_creates_an_empty_row():
    state = PowerStateTableDiagramState()

    row = add_row(state, "r1")

    assert state.rows == {"r1": row}
    assert row.power_domain_id is None
    assert row.state_name == ""
    assert row.net_states == {}


def test_remove_row_removes_only_the_named_row():
    state = PowerStateTableDiagramState()
    add_row(state, "r1")
    add_row(state, "r2")

    remove_row(state, "r1")

    assert set(state.rows) == {"r2"}


def test_remove_row_on_an_unknown_id_is_a_no_op():
    state = PowerStateTableDiagramState()
    add_row(state, "r1")

    remove_row(state, "does-not-exist")

    assert set(state.rows) == {"r1"}


def test_remove_rows_for_domain_deletes_every_row_scoped_to_that_domain():
    """Confirmed decision: a Power Domain being deleted deletes every
    row scoped to it entirely, rather than leaving an emptied cell."""
    state = PowerStateTableDiagramState()
    r1 = add_row(state, "r1")
    r1.power_domain_id = "d1"
    r2 = add_row(state, "r2")
    r2.power_domain_id = "d2"
    r3 = add_row(state, "r3")
    r3.power_domain_id = "d1"

    removed = remove_rows_for_domain(state, "d1")

    assert set(removed) == {"r1", "r3"}
    assert set(state.rows) == {"r2"}


# --- rename/remove cascade (snapshot diffing) --------------------------------


def test_rename_net_state_references_updates_every_matching_row():
    state = PowerStateTableDiagramState()
    r1 = add_row(state, "r1")
    r1.net_states["n1"] = "FULL_ON 1.0"
    r2 = add_row(state, "r2")
    r2.net_states["n1"] = "OFF"  # different value -- must not be touched

    rename_net_state_references(state, "n1", "FULL_ON 1.0", "ON")

    assert r1.net_states["n1"] == "ON"
    assert r2.net_states["n1"] == "OFF"


def test_clear_net_state_references_blanks_every_matching_row_without_deleting_it():
    state = PowerStateTableDiagramState()
    r1 = add_row(state, "r1")
    r1.net_states["n1"] = "FULL_ON 1.0"
    r1.state_name = "keep-me"

    clear_net_state_references(state, "n1", "FULL_ON 1.0")

    assert set(state.rows) == {"r1"}  # row NOT deleted
    assert state.rows["r1"].state_name == "keep-me"
    assert r1.net_states["n1"] is None


def test_reconcile_treats_a_same_position_rename_as_a_rename_not_a_removal():
    state = PowerStateTableDiagramState()
    row = add_row(state, "r1")
    row.net_states["n1"] = "FULL_ON 1.0"

    previous = {"n1": ["FULL_ON 1.0", "OFF"]}
    current = {"n1": ["ON", "OFF"]}
    reconcile_net_state_renames_and_removals(state, previous, current)

    assert row.net_states["n1"] == "ON"


def test_reconcile_treats_a_vanished_name_with_no_positional_match_as_removed():
    state = PowerStateTableDiagramState()
    row = add_row(state, "r1")
    row.net_states["n1"] = "FULL_ON 1.0"

    previous = {"n1": ["FULL_ON 1.0", "OFF"]}
    current = {"n1": ["OFF"]}  # ACTIVE genuinely removed, not renamed (list shrank)
    reconcile_net_state_renames_and_removals(state, previous, current)

    assert row.net_states["n1"] is None


def test_net_state_snapshot_reflects_a_plain_ports_own_states():
    port = _port("p1", "VDD", states=[SupplyPortState("FULL_ON", "1.0")])
    supply_state = SupplyPortDiagramState(supply_port_placements={"p1": port})

    snapshot = net_state_snapshot(supply_state, PowerSwitchDiagramState())

    assert snapshot == {"p1": ["FULL_ON 1.0"]}


# --- validate_power_state_table: empty cells ---------------------------------


def test_validate_flags_a_row_with_every_cell_empty():
    state = PowerStateTableDiagramState()
    add_row(state, "r1")
    domain_state = PowerDomainDiagramState(domains={"d1": _domain("d1", "PD1")})
    supply_state = SupplyPortDiagramState(supply_port_placements={"p1": _port("p1", "VDD", states=[SupplyPortState("FULL_ON", "1.0")])})

    diagnostics = validate_power_state_table(state, domain_state, supply_state)

    assert any(d.code == "POWER-STATE-TABLE-EMPTY-CELL" for d in diagnostics)


def test_validate_does_not_flag_a_fully_populated_row():
    state = PowerStateTableDiagramState()
    row = add_row(state, "r1")
    row.power_domain_id = "d1"
    row.state_name = "ACTIVE_STATE"
    row.net_states["p1"] = "FULL_ON 1.0"
    domain_state = PowerDomainDiagramState(domains={"d1": _domain("d1", "PD1")})
    supply_state = SupplyPortDiagramState(supply_port_placements={"p1": _port("p1", "VDD", states=[SupplyPortState("FULL_ON", "1.0")])})

    diagnostics = validate_power_state_table(state, domain_state, supply_state)

    assert diagnostics == []


# --- validate_power_state_table: duplicate name, per-domain scoped ----------


def test_validate_flags_duplicate_state_names_within_the_same_domain():
    state = PowerStateTableDiagramState()
    domain_state = PowerDomainDiagramState(domains={"d1": _domain("d1", "PD1")})
    supply_state = SupplyPortDiagramState(supply_port_placements={"p1": _port("p1", "VDD", states=[SupplyPortState("FULL_ON", "1.0")])})
    for rid in ("r1", "r2"):
        row = add_row(state, rid)
        row.power_domain_id = "d1"
        row.state_name = "SAME_NAME"
        row.net_states["p1"] = "FULL_ON 1.0"

    diagnostics = validate_power_state_table(state, domain_state, supply_state)

    assert sum(d.code == "POWER-STATE-TABLE-DUPLICATE-NAME" for d in diagnostics) == 1


def test_validate_allows_the_same_state_name_across_different_domains():
    state = PowerStateTableDiagramState()
    domain_state = PowerDomainDiagramState(domains={"d1": _domain("d1", "PD1"), "d2": _domain("d2", "PD2")})
    supply_state = SupplyPortDiagramState(supply_port_placements={"p1": _port("p1", "VDD", states=[SupplyPortState("FULL_ON", "1.0")])})
    row1 = add_row(state, "r1")
    row1.power_domain_id = "d1"
    row1.state_name = "SAME_NAME"
    row1.net_states["p1"] = "FULL_ON 1.0"
    row2 = add_row(state, "r2")
    row2.power_domain_id = "d2"
    row2.state_name = "SAME_NAME"
    row2.net_states["p1"] = "FULL_ON 1.0"

    diagnostics = validate_power_state_table(state, domain_state, supply_state)

    assert not any(d.code == "POWER-STATE-TABLE-DUPLICATE-NAME" for d in diagnostics)


# --- validate_power_state_table: duplicate full configuration, per-domain ---


def test_validate_flags_two_rows_in_the_same_domain_with_the_identical_net_selection():
    state = PowerStateTableDiagramState()
    domain_state = PowerDomainDiagramState(domains={"d1": _domain("d1", "PD1")})
    supply_state = SupplyPortDiagramState(supply_port_placements={"p1": _port("p1", "VDD", states=[SupplyPortState("FULL_ON", "1.0")])})
    row1 = add_row(state, "r1")
    row1.power_domain_id = "d1"
    row1.state_name = "NAME_A"
    row1.net_states["p1"] = "FULL_ON 1.0"
    row2 = add_row(state, "r2")
    row2.power_domain_id = "d1"
    row2.state_name = "NAME_B"  # different name -- still a duplicate CONFIGURATION
    row2.net_states["p1"] = "FULL_ON 1.0"

    diagnostics = validate_power_state_table(state, domain_state, supply_state)

    assert sum(d.code == "POWER-STATE-TABLE-DUPLICATE-CONFIGURATION" for d in diagnostics) == 1


def test_validate_allows_the_identical_net_selection_across_different_domains():
    """Confirmed decision: duplicate-configuration scoping is SAME-DOMAIN
    only -- two different domains may legitimately share one."""
    state = PowerStateTableDiagramState()
    domain_state = PowerDomainDiagramState(domains={"d1": _domain("d1", "PD1"), "d2": _domain("d2", "PD2")})
    supply_state = SupplyPortDiagramState(supply_port_placements={"p1": _port("p1", "VDD", states=[SupplyPortState("FULL_ON", "1.0")])})
    row1 = add_row(state, "r1")
    row1.power_domain_id = "d1"
    row1.state_name = "NAME_A"
    row1.net_states["p1"] = "FULL_ON 1.0"
    row2 = add_row(state, "r2")
    row2.power_domain_id = "d2"
    row2.state_name = "NAME_B"
    row2.net_states["p1"] = "FULL_ON 1.0"

    diagnostics = validate_power_state_table(state, domain_state, supply_state)

    assert not any(d.code == "POWER-STATE-TABLE-DUPLICATE-CONFIGURATION" for d in diagnostics)


def test_validate_does_not_double_flag_an_incomplete_row_as_also_a_duplicate_configuration():
    """A row with an empty cell is already flagged by the empty-cell
    rule -- it should never also count toward the duplicate-configuration
    comparison (every incomplete row would otherwise spuriously "match"
    every other incomplete row on their shared None values)."""
    state = PowerStateTableDiagramState()
    domain_state = PowerDomainDiagramState(domains={"d1": _domain("d1", "PD1")})
    supply_state = SupplyPortDiagramState(supply_port_placements={"p1": _port("p1", "VDD", states=[SupplyPortState("FULL_ON", "1.0")])})
    for rid in ("r1", "r2"):
        row = add_row(state, rid)
        row.power_domain_id = "d1"
        row.state_name = f"NAME_{rid}"
        # net_states["p1"] left unset -- both rows are incomplete

    diagnostics = validate_power_state_table(state, domain_state, supply_state)

    assert not any(d.code == "POWER-STATE-TABLE-DUPLICATE-CONFIGURATION" for d in diagnostics)
    assert sum(d.code == "POWER-STATE-TABLE-EMPTY-CELL" for d in diagnostics) == 2
