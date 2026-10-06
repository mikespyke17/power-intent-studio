"""power_intent_view/power_state_table_tab.py -- the "Power State Table"
tab's own QTableWidget/live-update wiring, against mock/fixture data
only. Drives the shared diagram-only state objects directly (the same
"directly-testable core" precedent already established elsewhere in
this codebase, e.g. Supply Port drag-and-drop) rather than through the
full diagram's own click/drag machinery, then emits
`diagramStateChanged` manually to trigger this tab's own reaction --
see power_state_table_tab.py's own module docstring for why it observes
that signal rather than PowerIntentDiagramView's own mutation methods
directly."""

from PySide6.QtGui import QColor, QUndoStack

from frontend.src.hierarchy_view.selection_controller import HierarchySelectionController
from frontend.src.power_intent_view.power_domain_overlay import PowerDomainPlacement, SupplySetPlacement
from frontend.src.power_intent_view.power_intent_diagram_view import PowerIntentDiagramView
from frontend.src.power_intent_view.power_intent_tab import PowerIntentTab
from frontend.src.power_intent_view.power_state_table_tab import PowerStateTableTab, _uniform_net_column_width
from frontend.src.power_intent_view.selection_controller import PowerIntentSelectionController
from frontend.src.power_intent_view.supply_port_overlay import SupplyPortPlacement, SupplyPortState
from frontend.src.shared.mock_backend import build_mock_design


def _tab(qtbot):
    diagram = PowerIntentDiagramView(HierarchySelectionController(), PowerIntentSelectionController(), QUndoStack())
    qtbot.addWidget(diagram)
    tab = PowerStateTableTab(diagram)
    qtbot.addWidget(tab)
    return tab, diagram


def _port(id_, name, states=None):
    return SupplyPortPlacement(id=id_, owner_path="top", name=name, color=QColor("#ff0000"), states=states or [])


def _domain(id_, name):
    return PowerDomainPlacement(id=id_, owner_path="top", name=name, color=QColor("#00ff00"))


# --- (a) zero-row start, correct headers -------------------------------------


def test_starts_with_zero_rows_and_the_two_fixed_column_headers_always_visible(qtbot):
    tab, _diagram = _tab(qtbot)

    assert tab.table.rowCount() == 0
    headers = [tab.table.horizontalHeaderItem(c).text() for c in range(tab.table.columnCount())]
    assert headers[0] == "Power Domain"
    assert headers[1] == "State Name"


# --- UI change: "Add State" moved below the table, before the Diagnostic ----
# --- section; "Check Power Intent" stays where it was -----------------------


def _central_layout_index_containing(layout, widget) -> int:
    """Index of whichever top-level central_layout item -- a bare
    widget (possibly itself containing `widget` as a nested descendant,
    e.g. the Diagnostic QGroupBox containing its own QTableView), or the
    QHBoxLayout wrapping a right-aligned button -- actually contains
    `widget`. Lets a test assert relative vertical ORDER without
    hardcoding exact indices tied to the layout's own internal
    structure."""
    for i in range(layout.count()):
        item = layout.itemAt(i)
        if item.widget() is not None and (item.widget() is widget or item.widget().isAncestorOf(widget)):
            return i
        if item.layout() is not None and any(item.layout().itemAt(j).widget() is widget for j in range(item.layout().count())):
            return i
    raise AssertionError(f"{widget} not found in this layout")


def test_add_state_sits_below_the_table_and_above_the_diagnostic_section(qtbot):
    tab, _diagram = _tab(qtbot)
    central_layout = tab.centralWidget().layout()

    table_index = _central_layout_index_containing(central_layout, tab.table)
    add_state_index = _central_layout_index_containing(central_layout, tab.add_state_button)
    diagnostics_index = _central_layout_index_containing(central_layout, tab.diagnostics_table)
    check_power_intent_index = _central_layout_index_containing(central_layout, tab.check_power_intent_button)

    assert table_index < add_state_index < diagnostics_index < check_power_intent_index


# --- (b) Add State correctness ------------------------------------------------


def test_add_state_adds_one_fully_empty_row(qtbot):
    tab, diagram = _tab(qtbot)
    diagram.shared_domain_state.domains["d1"] = _domain("d1", "PD1")
    diagram.diagramStateChanged.emit()

    tab.add_state_button.click()

    assert tab.table.rowCount() == 1
    domain_combo = tab.table.cellWidget(0, 0)
    name_edit = tab.table.cellWidget(0, 1)
    assert domain_combo.currentText() == ""  # starts EMPTY/unselected -- no default domain
    assert name_edit.text() == ""


def test_add_state_twice_adds_two_independent_rows(qtbot):
    tab, _diagram = _tab(qtbot)

    tab.add_state_button.click()
    tab.add_state_button.click()

    assert tab.table.rowCount() == 2
    assert len(tab._state.rows) == 2


# --- (c) delete-row correctness -----------------------------------------------


def test_delete_row_removes_only_that_row_and_keeps_the_others_data(qtbot):
    tab, _diagram = _tab(qtbot)
    tab.add_state_button.click()
    tab.add_state_button.click()
    tab.table.cellWidget(0, 1).setText("first")
    tab.table.cellWidget(1, 1).setText("second")

    delete_button = tab.table.cellWidget(0, tab.table.columnCount() - 1)
    delete_button.click()

    assert tab.table.rowCount() == 1
    assert tab.table.cellWidget(0, 1).text() == "second"


# --- (d)/(e) Check Power Intent: duplicates, empty cells, clears each run ---


def test_check_power_intent_flags_duplicate_names_in_the_same_domain(qtbot):
    tab, diagram = _tab(qtbot)
    diagram.shared_domain_state.domains["d1"] = _domain("d1", "PD1")
    diagram.shared_supply_state.supply_port_placements["p1"] = _port("p1", "VDD", states=[SupplyPortState("FULL_ON", "1.0")])
    diagram.diagramStateChanged.emit()

    for _ in range(2):
        tab.add_state_button.click()
    for r in range(2):
        tab.table.cellWidget(r, 0).setCurrentText("PD1")
        tab.table.cellWidget(r, 1).setText("SAME")
        tab.table.cellWidget(r, 2).setCurrentText("ACTIVE")

    tab.check_power_intent_button.click()

    codes = [tab.diagnostics_model.data(tab.diagnostics_model.index(r, 1)) for r in range(tab.diagnostics_model.rowCount())]
    assert "POWER-STATE-TABLE-DUPLICATE-NAME" in codes


def test_check_power_intent_flags_empty_cells(qtbot):
    tab, _diagram = _tab(qtbot)
    tab.add_state_button.click()

    tab.check_power_intent_button.click()

    codes = [tab.diagnostics_model.data(tab.diagnostics_model.index(r, 1)) for r in range(tab.diagnostics_model.rowCount())]
    assert "POWER-STATE-TABLE-EMPTY-CELL" in codes


def test_check_power_intent_on_a_zero_row_table_flags_the_empty_table(qtbot):
    tab, _diagram = _tab(qtbot)
    assert tab.table.rowCount() == 0

    tab.check_power_intent_button.click()

    codes = [tab.diagnostics_model.data(tab.diagnostics_model.index(r, 1)) for r in range(tab.diagnostics_model.rowCount())]
    assert "POWER-STATE-TABLE-EMPTY-TABLE" in codes


def test_the_empty_table_finding_clears_once_a_valid_row_exists_and_check_reruns(qtbot):
    """"the existing 'diagnostic resets on every check' rule" (explicit
    request) -- confirms the empty-table finding from a first run
    doesn't linger once the table is no longer empty on a later run."""
    tab, diagram = _tab(qtbot)
    diagram.shared_domain_state.domains["d1"] = _domain("d1", "PD1")
    diagram.diagramStateChanged.emit()

    tab.check_power_intent_button.click()
    codes = [tab.diagnostics_model.data(tab.diagnostics_model.index(r, 1)) for r in range(tab.diagnostics_model.rowCount())]
    assert "POWER-STATE-TABLE-EMPTY-TABLE" in codes

    tab.add_state_button.click()
    tab.table.cellWidget(0, 0).setCurrentText("PD1")
    tab.table.cellWidget(0, 1).setText("ACTIVE_STATE")

    tab.check_power_intent_button.click()

    assert tab.diagnostics_model.rowCount() == 0


def test_check_power_intent_clears_previous_results_instead_of_accumulating(qtbot):
    tab, diagram = _tab(qtbot)
    diagram.shared_domain_state.domains["d1"] = _domain("d1", "PD1")
    diagram.diagramStateChanged.emit()
    tab.add_state_button.click()

    tab.check_power_intent_button.click()
    first_run_count = tab.diagnostics_model.rowCount()
    assert first_run_count > 0

    # Fix the row completely (no net columns exist here, so the row is
    # now fully populated) -- a second run must show ONLY the current,
    # clean result, never the stale first-run diagnostic alongside it.
    tab.table.cellWidget(0, 0).setCurrentText("PD1")
    tab.table.cellWidget(0, 1).setText("ACTIVE_STATE")

    tab.check_power_intent_button.click()

    assert tab.diagnostics_model.rowCount() == 0


# --- (f) live column insertion on Supply Net add, correctly positioned ------


def test_adding_a_supply_net_inserts_a_new_column_immediately_and_correctly_positioned(qtbot):
    tab, diagram = _tab(qtbot)
    diagram.shared_supply_state.supply_port_placements["p1"] = _port("p1", "VDD")
    diagram.diagramStateChanged.emit()
    assert [tab.table.horizontalHeaderItem(c).text() for c in range(tab.table.columnCount())] == ["Power Domain", "State Name", "VDD", ""]

    diagram.shared_supply_state.supply_port_placements["p2"] = _port("p2", "VDD2")
    diagram.diagramStateChanged.emit()

    headers = [tab.table.horizontalHeaderItem(c).text() for c in range(tab.table.columnCount())]
    assert headers == ["Power Domain", "State Name", "VDD", "VDD2", ""]


def test_a_new_ground_net_is_grouped_after_every_power_net(qtbot):
    tab, diagram = _tab(qtbot)
    diagram.shared_supply_state.supply_port_placements["p1"] = _port("p1", "VDD")
    diagram.shared_domain_state.supply_sets["ss1"] = SupplySetPlacement(id="ss1", name="SS1", power="VDD")
    diagram.diagramStateChanged.emit()

    diagram.shared_supply_state.supply_port_placements["p2"] = _port("p2", "VSS")
    diagram.shared_domain_state.supply_sets["ss1"].ground = "VSS"
    diagram.diagramStateChanged.emit()

    headers = [tab.table.horizontalHeaderItem(c).text() for c in range(tab.table.columnCount())]
    assert headers == ["Power Domain", "State Name", "VDD", "VSS", ""]


# --- bug fix: a shared (CHANGE 2 ancestor-sharing) Supply Net must appear ---
# --- as exactly ONE column, never once per referencing Power Domain --------


def test_a_supply_net_shared_across_two_power_domains_appears_as_one_column_not_two(qtbot):
    """Reproduces CHANGE 2's own "ancestor Supply Set sharing" worked
    example (test_ancestor_supply_set_sharing.py's own
    test_worked_example_exactly_3_internal_supplies_with_correct_auto_links):
    a Power Domain sharing an ANCESTOR's Supply Set gets its own,
    genuinely separate SupplyPortPlacement (its own id) auto-
    instantiated and auto-connected under the SAME name, for every one
    of that Supply Set's pins -- so "VDD"/"VSS" end up as TWO distinct
    placements (one at "top", one auto-linked Internal Supply at
    "top/u_core"), the same physical net. Before the fix, each showed
    up as its own column; the fix deduplicates by name."""
    real_tab = PowerIntentTab()
    qtbot.addWidget(real_tab)
    real_tab.set_rtl(build_mock_design())
    view = real_tab.diagram._view
    top_box = view._boxes["top"]

    ss_top = SupplySetPlacement(id="ss_top", name="SS_VDD", power="VDD", ground="VSS", owner_path="top")
    domain_top = view.create_power_domain(
        top_box, "PD_TOP", QColor("#4caf50"), 0.5, "ss_top", [], {"ss_top": ss_top}, {"VDD": QColor("#ff0000"), "VSS": QColor("#0000ff")}
    )
    # The directly-testable core of "share this ancestor Supply Set with
    # a new hierarchy" -- see _auto_link_cross_hierarchy_supply_sets()'s
    # own docstring: this is exactly what CreatePowerDomainDialog's own
    # ancestor-dropdown selection triggers, bypassing the dialog chain
    # per this codebase's own established test precedent.
    view._auto_link_cross_hierarchy_supply_sets(ss_top, target_owner_paths=["top/u_core"])

    sub_placements = {p.name: p for p in view._shared_state.supply_port_placements.values() if p.owner_path == "top/u_core"}
    assert set(sub_placements) >= {"VDD", "VSS"}  # confirms the auto-link actually happened -- 2 SEPARATE placements, same names as "top"'s own

    tab = PowerStateTableTab(real_tab.diagram)
    qtbot.addWidget(tab)

    headers = [tab.table.horizontalHeaderItem(c).text() for c in range(tab.table.columnCount())]
    assert headers.count("VDD") == 1
    assert headers.count("VSS") == 1
    assert domain_top.id in view._domain_state.domains  # sanity: the domain really was created


# --- (g) live column removal, including data --------------------------------


def test_removing_a_supply_net_removes_its_column_and_its_data_immediately(qtbot):
    tab, diagram = _tab(qtbot)
    diagram.shared_supply_state.supply_port_placements["p1"] = _port("p1", "VDD", states=[SupplyPortState("FULL_ON", "1.0")])
    diagram.diagramStateChanged.emit()
    tab.add_state_button.click()
    tab.table.cellWidget(0, 2).setCurrentIndex(1)  # the net's first real state
    assert tab._state.rows[next(iter(tab._state.rows))].net_states["p1"] == "FULL_ON 1.0"

    del diagram.shared_supply_state.supply_port_placements["p1"]
    diagram.diagramStateChanged.emit()

    headers = [tab.table.horizontalHeaderItem(c).text() for c in range(tab.table.columnCount())]
    assert "VDD" not in headers
    assert tab.table.rowCount() == 1  # the row itself survives -- only the column/data for that net is gone


# --- (h) live cell update on a per-net Power State rename --------------------


def test_changing_a_power_states_value_updates_any_cell_referencing_it(qtbot):
    """Was "renaming a power state". States have no name now, so the
    equivalent -- and the thing the positional before/after diff actually
    keys off -- is a state whose VALUE changes in place: its canonical
    key changes while it stays at the same position in the list."""
    tab, diagram = _tab(qtbot)
    state_obj = SupplyPortState("FULL_ON", "1.0")
    diagram.shared_supply_state.supply_port_placements["p1"] = _port("p1", "VDD", states=[state_obj])
    diagram.diagramStateChanged.emit()
    tab.add_state_button.click()
    tab.table.cellWidget(0, 2).setCurrentIndex(1)
    row_id = next(iter(tab._state.rows))
    assert tab._state.rows[row_id].net_states["p1"] == "FULL_ON 1.0"

    state_obj.voltage = "0.9"  # edited in place, same position in the list
    diagram.diagramStateChanged.emit()

    assert tab._state.rows[row_id].net_states["p1"] == "FULL_ON 0.9"
    assert tab.table.cellWidget(0, 2).currentText() == "0.9"  # DISPLAY is the bare number


# --- (i) live cell clear (not row delete) on Power State removal ------------


def test_removing_a_power_state_clears_the_cell_without_deleting_the_row(qtbot):
    tab, diagram = _tab(qtbot)
    # Two genuinely DISTINCT states -- same Type and Voltage would be a
    # duplicate now, and the list would not shrink observably.
    active_state = SupplyPortState("FULL_ON", "1.0")
    off_state = SupplyPortState("OFF")
    placement = _port("p1", "VDD", states=[active_state, off_state])
    diagram.shared_supply_state.supply_port_placements["p1"] = placement
    diagram.diagramStateChanged.emit()
    tab.add_state_button.click()
    tab.table.cellWidget(0, 2).setCurrentIndex(1)  # the FULL_ON state
    row_id = next(iter(tab._state.rows))
    tab.table.cellWidget(0, 1).setText("keep-me")

    placement.states = [off_state]  # FULL_ON genuinely removed (list shrank)
    diagram.diagramStateChanged.emit()

    assert tab.table.rowCount() == 1  # row NOT deleted
    assert tab._state.rows[row_id].net_states["p1"] is None
    assert tab._state.rows[row_id].state_name == "keep-me"
    assert tab.table.cellWidget(0, 2).currentText() == ""


# --- (j) globally-computed uniform dynamic-column width ----------------------


def test_every_dynamic_net_column_shares_one_globally_computed_width(qtbot):
    tab, diagram = _tab(qtbot)
    diagram.shared_supply_state.supply_port_placements["p1"] = _port("p1", "V", states=[SupplyPortState("FULL_ON", "1.0")])
    diagram.shared_supply_state.supply_port_placements["p2"] = _port(
        "p2", "A_MUCH_LONGER_NET_NAME_THAN_ANY_STATE", states=[SupplyPortState("FULL_ON", "1.0")]
    )
    diagram.diagramStateChanged.emit()

    tab.add_state_button.click()

    combo_1 = tab.table.cellWidget(0, 2)
    combo_2 = tab.table.cellWidget(0, 3)
    assert combo_1.minimumWidth() == combo_2.minimumWidth()
    # The shared width must be driven by the single longest entry across
    # BOTH nets' names AND BOTH nets' own state lists, not the longest
    # entry within any one column alone.
    expected = _uniform_net_column_width(
        tab.table, [placement for placement in [diagram.shared_supply_state.supply_port_placements["p1"], diagram.shared_supply_state.supply_port_placements["p2"]]], diagram.shared_supply_state, diagram.shared_power_switch_state
    )
    assert combo_1.minimumWidth() == expected


# --- Power Domain deletion / rename cascade ----------------------------------


def test_deleting_a_power_domain_deletes_every_row_scoped_to_it(qtbot):
    tab, diagram = _tab(qtbot)
    diagram.shared_domain_state.domains["d1"] = _domain("d1", "PD1")
    diagram.diagramStateChanged.emit()
    tab.add_state_button.click()
    tab.table.cellWidget(0, 0).setCurrentText("PD1")
    assert tab.table.rowCount() == 1

    del diagram.shared_domain_state.domains["d1"]
    diagram.diagramStateChanged.emit()

    assert tab.table.rowCount() == 0


def test_renaming_a_power_domain_updates_the_dropdown_selection_automatically(qtbot):
    tab, diagram = _tab(qtbot)
    diagram.shared_domain_state.domains["d1"] = _domain("d1", "PD1")
    diagram.diagramStateChanged.emit()
    tab.add_state_button.click()
    tab.table.cellWidget(0, 0).setCurrentText("PD1")

    diagram.shared_domain_state.domains["d1"].name = "PD1_RENAMED"
    diagram.diagramStateChanged.emit()

    assert tab.table.cellWidget(0, 0).currentText() == "PD1_RENAMED"
    assert tab._state.rows[next(iter(tab._state.rows))].power_domain_id == "d1"  # id-based reference, unchanged
