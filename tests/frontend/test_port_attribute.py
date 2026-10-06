"""Port Attributes -- explicit request: a new "Add Port Attribute"/
"Edit Port Attribute" right-click pair, top-level-hierarchy-only,
letting the user assign a Supply Set to each of the top-level block's
own I/O ports (or, since the table-based rework below, each individual
BIT of a multi-bit port). Diagram-only for this pass, same "mock-first"
scoping decision already established for every other diagram-only
construct in this app (see power_domain_overlay.py's own module
docstring).

The table-based-editor-specific tests near the bottom of this file
(bit-wise ungrouping, direction-based column locking) build their own
`Port` lists directly rather than going through `build_mock_design()`
-- every mock top-level port there is a plain single-bit IN/OUT port,
so a real multi-bit/INOUT case needs a hand-built fixture regardless."""

import gc

import pytest
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QDialog, QMenu

from frontend.src.power_intent_view.port_attribute_dialog import PortAttributeDialog
from frontend.src.power_intent_view.power_domain_overlay import SupplySetPlacement
from frontend.src.power_intent_view.power_intent_tab import PowerIntentTab
from frontend.src.shared.mock_backend import build_mock_design
from frontend.src.shared.models.rtl import Port, PortDirection
from frontend.src.shared.widgets.table_filter import TableFilterBar


def _deselect_and_collect(tab: PowerIntentTab) -> None:
    if tab.diagram._view.scene() is not None:
        tab.diagram._view.scene().clearSelection()
    gc.collect()


# --- top-level-only gating ---------------------------------------------


def test_add_port_attribute_enabled_only_on_the_top_level_block(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view

    menu = QMenu()
    top_actions = view._extra_context_menu_actions(menu, view._boxes["top"])
    by_text_top = {a.text(): a for a, _cb in top_actions}
    assert by_text_top["Add Port Attribute"].isEnabled() is True
    assert by_text_top["Edit Port Attribute"].isEnabled() is False  # none assigned yet

    menu2 = QMenu()
    inner_actions = view._extra_context_menu_actions(menu2, view._boxes["top/u_core"])
    by_text_inner = {a.text(): a for a, _cb in inner_actions}
    assert by_text_inner["Add Port Attribute"].isEnabled() is False
    assert by_text_inner["Edit Port Attribute"].isEnabled() is False
    _deselect_and_collect(tab)


def test_port_attribute_disabled_on_a_macro_even_if_it_were_top_level(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view

    menu = QMenu()
    actions = view._extra_context_menu_actions(menu, view._boxes["top/u_ram"])  # macro, not top-level either
    by_text = {a.text(): a for a, _cb in actions}
    assert by_text["Add Port Attribute"].isEnabled() is False
    _deselect_and_collect(tab)


def test_port_attribute_disabled_for_a_multi_selection(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view._boxes["top/u_core"].setSelected(True)
    view._boxes["top/u_dbg"].setSelected(True)

    menu = QMenu()
    actions = view._extra_context_menu_actions(menu, view._boxes["top/u_core"])
    by_text = {a.text(): a for a, _cb in actions}
    assert by_text["Add Port Attribute"].isEnabled() is False
    assert by_text["Edit Port Attribute"].isEnabled() is False
    _deselect_and_collect(tab)


def test_edit_port_attribute_enables_once_an_assignment_exists(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top"]

    view.set_port_attributes(box, {"clk": None}, {})

    menu = QMenu()
    actions = view._extra_context_menu_actions(menu, box)
    by_text = {a.text(): a for a, _cb in actions}
    assert by_text["Add Port Attribute"].isEnabled() is False
    assert by_text["Edit Port Attribute"].isEnabled() is True
    _deselect_and_collect(tab)


# --- directly-testable core: set_port_attributes() ---------------------


def test_empty_selections_are_accepted_and_saved(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top"]

    view.set_port_attributes(box, {"clk": None, "sleep_n": None}, {})

    assert view._port_attribute_state.assignments == {"clk": None, "sleep_n": None}
    _deselect_and_collect(tab)


def test_set_port_attributes_assigns_a_real_supply_set(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top"]
    supply_set = SupplySetPlacement(id="s1", name="SS_TOP", power="VDD", owner_path="top")
    view._domain_state.supply_sets["s1"] = supply_set

    view.set_port_attributes(box, {"clk": "s1"}, {})

    assert view._port_attribute_state.assignments["clk"] == "s1"
    _deselect_and_collect(tab)


def test_set_port_attributes_is_undoable(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top"]

    view.set_port_attributes(box, {"clk": None}, {})
    assert view._port_attribute_state.assignments == {"clk": None}

    view._undo_stack.undo()
    assert view._port_attribute_state.assignments == {}

    view._undo_stack.redo()
    assert view._port_attribute_state.assignments == {"clk": None}
    _deselect_and_collect(tab)


# --- dialog: creating a new Supply Set from within the picker -----------


def _fake_create_supply_set_exec(name: str, power="VDD", ground="VSS", n_well="VDD", p_well="VSS"):
    def fake_exec(self):
        self.name_edit.setText(name)
        self.power_combo.setCurrentText(power)
        self.ground_combo.setCurrentText(ground)
        self.n_well_combo.setCurrentText(n_well)
        self.p_well_combo.setCurrentText(p_well)
        return QDialog.DialogCode.Accepted

    return fake_exec


def test_creating_a_new_supply_set_from_the_dialog_becomes_selected(qtbot, monkeypatch):
    """Creating a new Supply Set from within the dropdown works and the
    new set becomes selected (explicit request) -- selected in whatever
    row's combo the caller then picks it from."""
    import frontend.src.power_intent_view.port_attribute_dialog as mod

    monkeypatch.setattr(mod.CreateSupplySetDialog, "exec", _fake_create_supply_set_exec("SS_NEW"))
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top"]

    dialog = PortAttributeDialog(box.definition.ports, view._domain_state.supply_sets, [], box.path, parent=None)
    qtbot.addWidget(dialog)
    dialog.create_supply_set_button.click()

    assert len(dialog.new_supply_sets) == 1
    new_id = next(iter(dialog.new_supply_sets))
    combo = dialog._driver_combos["clk"]  # "clk" is IN -- Driver is its boundary-facing/editable cell
    index = combo.findData(new_id)
    assert index >= 0
    combo.setCurrentIndex(index)

    assert dialog.assignments["clk"] == new_id
    _deselect_and_collect(tab)


def test_creating_a_supply_set_materializes_new_supply_ports_on_the_top_level_box(qtbot, monkeypatch):
    """A brand-new supply-port *name* introduced via CreateSupplySetDialog's
    own nested "Add New..." combo flow (not just an already-known name)
    must actually get materialized as a real Supply Port on the top-
    level block once set_port_attributes() commits."""
    import frontend.src.power_intent_view.port_attribute_dialog as mod
    from frontend.src.power_intent_view.add_power_supply_dialog import AddPowerSupplyDialog

    def fake_add_power_supply_exec(self):
        self.name_edit.setText("VDD_NEW")
        self.color = QColor("#123456")
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(AddPowerSupplyDialog, "exec", fake_add_power_supply_exec)

    def fake_create_supply_set_exec(self):
        self.name_edit.setText("SS_NEW")
        index = self.power_combo.findText("Add New...")
        self.power_combo._on_index_changed(index)  # drives the nested AddPowerSupplyDialog above
        self.ground_combo.setCurrentText("VDD_NEW")
        self.n_well_combo.setCurrentText("VDD_NEW")
        self.p_well_combo.setCurrentText("VDD_NEW")
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(mod.CreateSupplySetDialog, "exec", fake_create_supply_set_exec)
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top"]

    dialog = PortAttributeDialog(box.definition.ports, view._domain_state.supply_sets, [], box.path, parent=None)
    qtbot.addWidget(dialog)
    dialog.create_supply_set_button.click()
    new_id = next(iter(dialog.new_supply_sets))
    assert "VDD_NEW" in dialog.new_supply_ports

    view.set_port_attributes(box, {"clk": new_id}, dialog.new_supply_sets, dialog.new_supply_ports)

    ports = [p for p in view._shared_state.supply_port_placements.values() if p.owner_path == "top" and p.name == "VDD_NEW"]
    assert len(ports) == 1
    assert ports[0].is_internal_supply is False  # top-level -- a real Supply Port, not Internal Supply
    _deselect_and_collect(tab)


def _fake_create_supply_set_exec_with_new_names(name: str, power: str, ground: str):
    """Like this file's own _fake_create_supply_set_exec above, but ALSO
    seeds new_port_colors directly (standing in for a real "Add New..."
    round trip -- see test_power_intent_retention.py's own equivalent
    helper) so this test can confirm an abandoned Supply Set's own
    supply names don't survive to accept() either, not just the Supply
    Set record itself."""

    def fake_exec(self):
        self.name_edit.setText(name)
        self.new_port_colors[power] = QColor("#123456")
        self.new_port_colors[ground] = QColor("#654321")
        for combo, value in ((self.power_combo, power), (self.ground_combo, ground)):
            combo.blockSignals(True)
            if combo.findText(value) < 0:
                combo.addItem(value)
            combo.setCurrentText(value)
            combo.blockSignals(False)
        return QDialog.DialogCode.Accepted

    return fake_exec


def test_accept_discards_a_supply_set_created_but_never_assigned_to_any_row(qtbot, monkeypatch):
    """Same architectural bug/fix as CreatePowerDomainDialog/
    RetentionStrategyDialog's own _discard_unused_new_entities() (see
    test_power_domain.py/test_power_intent_retention.py for their own
    versions of this regression test): "Create Supply Set..." is a
    single, SHARED button here, not tied to any one row -- a Supply Set
    created via it and never actually assigned to ANY row's combo before
    this dialog's own OK must not be committed, along with any supply
    name it introduced."""
    import frontend.src.power_intent_view.port_attribute_dialog as mod

    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top"]
    dialog = PortAttributeDialog(box.definition.ports, view._domain_state.supply_sets, [], box.path, parent=None)
    qtbot.addWidget(dialog)

    monkeypatch.setattr(mod.CreateSupplySetDialog, "exec", _fake_create_supply_set_exec_with_new_names("SS_ABANDONED", "VDD_X", "VSS_X"))
    dialog.create_supply_set_button.click()
    abandoned_id = next(iter(dialog.new_supply_sets))

    monkeypatch.setattr(mod.CreateSupplySetDialog, "exec", _fake_create_supply_set_exec_with_new_names("SS_USED", "VDD_Y", "VSS_Y"))
    dialog.create_supply_set_button.click()
    used_id = next(sid for sid in dialog.new_supply_sets if sid != abandoned_id)
    combo = dialog._driver_combos["clk"]  # "clk" is IN -- Driver is its boundary-facing/editable cell
    combo.setCurrentIndex(combo.findData(used_id))  # only SS_USED ever gets assigned to a row

    dialog.accept()

    assert dialog.result() == QDialog.DialogCode.Accepted
    assert set(dialog.new_supply_sets) == {used_id}
    assert set(dialog.new_supply_ports) == {"VDD_Y", "VSS_Y"}
    _deselect_and_collect(tab)


# --- table-based editor: bit-wise ungrouping + direction-based locking --


def _bit_wise_test_ports() -> list[Port]:
    return [
        Port(name="clk", direction=PortDirection.IN),  # width 1 -- stays one plain row, no "[0]" suffix
        Port(name="a", direction=PortDirection.IN, width=4),  # multi-bit IN -- 4 rows, a[0]..a[3]
        Port(name="q", direction=PortDirection.OUT, width=3),  # multi-bit OUT -- 3 rows, q[0]..q[2]
        Port(name="bd", direction=PortDirection.INOUT, width=2),  # multi-bit INOUT -- 2 rows, bd[0]..bd[1]
    ]


def test_single_bit_port_gets_one_plain_row_with_no_bracket_suffix(qtbot):
    dialog = PortAttributeDialog(_bit_wise_test_ports(), {}, [], "top", parent=None)
    qtbot.addWidget(dialog)

    keys = [key for key, _direction in dialog._row_specs]
    assert keys.count("clk") == 1
    assert not any(key.startswith("clk[") for key in keys)


def test_multi_bit_ports_are_ungrouped_into_one_row_per_bit(qtbot):
    dialog = PortAttributeDialog(_bit_wise_test_ports(), {}, [], "top", parent=None)
    qtbot.addWidget(dialog)

    keys = [key for key, _direction in dialog._row_specs]
    assert [k for k in keys if k.startswith("a[")] == ["a[0]", "a[1]", "a[2]", "a[3]"]
    assert [k for k in keys if k.startswith("q[")] == ["q[0]", "q[1]", "q[2]"]
    # 1 (clk) + 4 (a) + 3 (q) + 2 (bd) rows total.
    assert dialog.table.rowCount() == 1 + 4 + 3 + 2


def test_input_row_locks_receiver_and_leaves_only_driver_editable(qtbot):
    dialog = PortAttributeDialog(_bit_wise_test_ports(), {}, [], "top", parent=None)
    qtbot.addWidget(dialog)

    for key in ("clk", "a[0]", "a[1]", "a[2]", "a[3]"):
        assert key in dialog._driver_combos
        assert key not in dialog._receiver_combos
        row = [k for k, _d in dialog._row_specs].index(key)
        receiver_combo = dialog.table.cellWidget(row, 2)
        assert receiver_combo.isEnabled() is False
        assert receiver_combo.currentData() is None


def test_output_row_locks_driver_and_leaves_only_receiver_editable(qtbot):
    dialog = PortAttributeDialog(_bit_wise_test_ports(), {}, [], "top", parent=None)
    qtbot.addWidget(dialog)

    for key in ("q[0]", "q[1]", "q[2]"):
        assert key in dialog._receiver_combos
        assert key not in dialog._driver_combos
        row = [k for k, _d in dialog._row_specs].index(key)
        driver_combo = dialog.table.cellWidget(row, 3)
        assert driver_combo.isEnabled() is False
        assert driver_combo.currentData() is None


def test_locked_column_never_appears_in_submitted_assignments(qtbot):
    """Direct confirmation of the "never considered part of the
    submitted Port Attribute data for that row" requirement -- even
    setting a Supply Set on an editable row leaves every OTHER row's
    locked column absent from `assignments` (there is no key collision
    risk since each row structurally feeds only one of the two combo
    dicts, but this pins the observable, end-to-end behavior)."""
    supply_set = SupplySetPlacement(id="s1", name="SS_TOP", power="VDD", ground="VSS", owner_path="top")
    dialog = PortAttributeDialog(_bit_wise_test_ports(), {"s1": supply_set}, [], "top", parent=None)
    qtbot.addWidget(dialog)

    dialog._driver_combos["clk"].setCurrentIndex(dialog._driver_combos["clk"].findData("s1"))
    dialog._receiver_combos["q[0]"].setCurrentIndex(dialog._receiver_combos["q[0]"].findData("s1"))

    assignments = dialog.assignments
    assert assignments["clk"] == "s1"
    assert assignments["q[0]"] == "s1"
    assert set(assignments) == {"clk", "a[0]", "a[1]", "a[2]", "a[3]", "q[0]", "q[1]", "q[2]", "bd[0]", "bd[1]"}


def test_inout_row_keeps_both_combos_editable_and_mirrored(qtbot):
    supply_set = SupplySetPlacement(id="s1", name="SS_TOP", power="VDD", ground="VSS", owner_path="top")
    dialog = PortAttributeDialog(_bit_wise_test_ports(), {"s1": supply_set}, [], "top", parent=None)
    qtbot.addWidget(dialog)

    row = [k for k, _d in dialog._row_specs].index("bd[0]")
    receiver_combo = dialog.table.cellWidget(row, 2)
    driver_combo = dialog.table.cellWidget(row, 3)
    assert receiver_combo.isEnabled() is True
    assert driver_combo.isEnabled() is True

    driver_combo.setCurrentIndex(driver_combo.findData("s1"))
    assert receiver_combo.currentData() == "s1"  # mirrored from Driver

    assert dialog.assignments["bd[0]"] == "s1"


# --- filter bar (bug fix: present in the original spec, dropped in the -
# --- initial build) -----------------------------------------------------


def _wide_bus_test_ports() -> list[Port]:
    return [
        Port(name="clk", direction=PortDirection.IN),
        Port(name="d", direction=PortDirection.IN, width=32),
        Port(name="q", direction=PortDirection.OUT, width=32),
    ]


def _visible_row_names(dialog: PortAttributeDialog) -> list[str]:
    return [
        dialog.table.item(row, 0).text()
        for row in range(dialog.table.rowCount())
        if not dialog.table.isRowHidden(row)
    ]


def test_filter_bar_is_present_and_is_a_real_table_filter_bar(qtbot):
    """The exact same widget/matching-logic class every other Properties
    table filter already uses (TableFilterBar, shared/widgets/
    table_filter.py) -- not a bespoke reimplementation."""
    dialog = PortAttributeDialog(_bit_wise_test_ports(), {}, [], "top", parent=None)
    qtbot.addWidget(dialog)

    assert isinstance(dialog.table_filter, TableFilterBar)


def test_filter_matches_port_name_live_as_the_user_types(qtbot):
    dialog = PortAttributeDialog(_bit_wise_test_ports(), {}, [], "top", parent=None)
    qtbot.addWidget(dialog)

    dialog.table_filter.setText("clk")

    assert _visible_row_names(dialog) == ["clk"]


def test_filter_wildcard_matches_every_bit_of_a_multi_bit_port(qtbot):
    """"d*" must show every ungrouped bit row of the 32-bit `d` port
    (d[0]..d[31]), not just a single bus-level match -- filtering only
    ever looks at each ROW's own text, and bit-wise ungrouping already
    gave every bit its own row/text before the filter is even involved."""
    dialog = PortAttributeDialog(_wide_bus_test_ports(), {}, [], "top", parent=None)
    qtbot.addWidget(dialog)

    dialog.table_filter.setText("d*")

    visible = _visible_row_names(dialog)
    assert visible == [f"d[{i}]" for i in range(32)]
    assert "clk" not in visible
    assert "q[0]" not in visible


def test_filter_clearing_shows_every_row_again(qtbot):
    dialog = PortAttributeDialog(_bit_wise_test_ports(), {}, [], "top", parent=None)
    qtbot.addWidget(dialog)

    dialog.table_filter.setText("clk")
    assert len(_visible_row_names(dialog)) == 1

    dialog.table_filter.setText("")
    assert len(_visible_row_names(dialog)) == dialog.table.rowCount()


def test_filtering_does_not_lose_selection_state_of_a_row_that_stays_visible(qtbot):
    """Filtering hides rows via setRowHidden() only -- it never touches a
    row's own selection state, so a still-visible selected row stays
    selected exactly as it was before the filter narrowed the view (the
    same "filtering never disturbs selection for surviving rows"
    guarantee TableFilterBar already provides everywhere else it's
    used, e.g. the Retention Strategy dialog's own Elements list)."""
    dialog = PortAttributeDialog(_bit_wise_test_ports(), {}, [], "top", parent=None)
    qtbot.addWidget(dialog)

    clk_row = [k for k, _d in dialog._row_specs].index("clk")
    dialog.table.selectRow(clk_row)
    assert dialog.table.item(clk_row, 0).isSelected() is True

    dialog.table_filter.setText("clk")

    assert dialog.table.item(clk_row, 0).isSelected() is True
    assert dialog.table.isRowHidden(clk_row) is False
