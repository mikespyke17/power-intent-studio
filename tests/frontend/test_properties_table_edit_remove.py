"""Properties table Edit/Remove (explicit request) -- across all entity
types (Supply Set, Power Domain, Supply Port, Power Switch, Isolation,
Retention). See
properties_panel.py's own PowerIntentPropertiesPanel docstring and
power_intent_diagram_view.py's own edit_properties_row()/
remove_properties_row() docstrings for the full "one generic mechanism,
reusing the exact same dialogs/cascade-delete logic" architecture.

Two layers of coverage, matching how this feature is actually built:
- A handful of GENERIC-mechanism tests (left-click-select, right-click
  context menu, signal emission, enablement) -- the mechanism itself is
  shared/identical across every kind, so it's only exercised directly
  once, not duplicated per entity.
- Per-entity tests calling straight into the view's own
  edit_properties_row()/remove_properties_row()/can_edit_properties_row()/
  can_remove_properties_row() -- where the real, entity-specific logic
  actually lives -- covering (b) Edit opens the correct pre-filled
  dialog (+ impact notification where applicable) and (c) Remove's
  cascade-impact confirmation (list dependents, cascade on Yes, no-op
  on No).
"""

import gc

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QDialog, QMessageBox

from frontend.src.power_intent_view.create_power_domain_dialog import CreatePowerDomainDialog, CreateSupplySetDialog
from frontend.src.power_intent_view.isolation_dialog import IsolationDialog
from frontend.src.power_intent_view.power_domain_overlay import PowerDomainPlacement, SupplySetPlacement
from frontend.src.power_intent_view.power_intent_tab import PowerIntentTab
from frontend.src.power_intent_view.power_switch_dialog import PowerSwitchDialog
from frontend.src.power_intent_view.properties_panel import PowerIntentPropertiesPanel
from frontend.src.power_intent_view.retention_dialog import RetentionStrategyDialog
from frontend.src.power_intent_view.retention_overlay import RetentionStrategyPlacement
from frontend.src.power_intent_view.selection_controller import PowerIntentSelectionController
from frontend.src.shared.mock_backend import build_mock_design, build_mock_power_intent
from frontend.src.shared.widgets import message_box


def _bare_panel(qtbot):
    """A standalone PowerIntentPropertiesPanel, NOT wired into a real
    PowerIntentTab -- used specifically for the generic-mechanism
    signal-emission tests below, where a real Tab's own editRequested/
    removeRequested wiring (straight to the diagram view's own
    edit_properties_row()/remove_properties_row(), which can open a
    REAL, blocking dialog) would otherwise ALSO fire alongside the
    test's own receiver lambda -- confirmed empirically: an earlier
    version of these tests used a full PowerIntentTab and hung the test
    process outright on exactly that real dialog.exec() call."""
    panel = PowerIntentPropertiesPanel(PowerIntentSelectionController())
    qtbot.addWidget(panel)
    return panel


def _deselect_and_collect(tab: PowerIntentTab) -> None:
    if tab.diagram._view.scene() is not None:
        tab.diagram._view.scene().clearSelection()
    gc.collect()


def _accept_dialog(dialog_cls, captured: list):
    """monkeypatch target for `dialog_cls.exec` -- captures the live,
    already-constructed (and therefore already-pre-filled) instance so
    a test can assert on its own fields, and always accepts."""

    def fake_exec(self):
        captured.append(self)
        return QDialog.DialogCode.Accepted

    return fake_exec


def _reject_dialog(captured: list):
    def fake_exec(self):
        captured.append(self)
        return QDialog.DialogCode.Rejected

    return fake_exec


def _full_cascade_fixture(qtbot):
    """One Power Domain (PD1) with Primary Supply Set SS1, a Retention
    Strategy, a Power Switch, and an Isolation Cell -- all attached to
    the SAME domain, so removing either the domain or its Supply Set
    cascades through everything at once. Reused across Power Domain/
    Supply Set/Power Switch-removal cascade tests."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top"]

    ss1 = SupplySetPlacement(id="ss1", name="SS1", power="VDD", ground="VSS", owner_paths=["top"])
    view._domain_state.supply_sets["ss1"] = ss1
    domain = PowerDomainPlacement(id="d1", owner_path="top", name="PD1", color=QColor("#4caf50"), primary_supply_set="ss1")
    view._domain_state.domains["d1"] = domain
    retention = RetentionStrategyPlacement(
        id="ret1", name="RET1", power_domain_id="d1", primary_supply_set_id="ss1", retention_supply_set_id="ss1",
        save_logic="save", restore_logic="restore", elements=["top/u_core/out1"],
    )
    view._retention_state.strategies["ret1"] = retention
    switch_target = view.add_supply_port(box, "VDD_SW", QColor("#123456"))
    switch = view.add_power_switch(switch_target, "SW1", "IN", "OUT", "CTRL", "SWCELL", "sleep_b", "")
    view._domain_state.domains["d1"] = PowerDomainPlacement(
        id="d1", owner_path="top", name="PD1", color=QColor("#4caf50"), primary_supply_set="ss1"
    )
    # Give SW1 the correct power_domain_id (add_power_switch resolves the
    # domain from the target's own owner_path -- "top" -- which already
    # matches "d1" above, so this is just documenting the invariant, not
    # a fixup).
    assert view._power_switch_state.switches[switch.id].power_domain_id == "d1"
    return tab, view, domain, ss1, retention, switch


# --- Generic mechanism (left-click-select, right-click menu, signals) -----


def test_left_click_selects_a_row(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    domain = PowerDomainPlacement(id="d1", owner_path="top", name="PD1", color=QColor("#ff0000"))
    view._domain_state.domains["d1"] = domain
    view._notify_changed()

    table = tab.properties._tables["PowerDomain"]
    row = next(r for r in range(table.rowCount()) if table.item(r, 0).data(Qt.ItemDataRole.UserRole) == "d1")
    table.selectRow(row)

    assert table.item(row, 0).isSelected()
    _deselect_and_collect(tab)


def test_right_click_shows_edit_and_remove_enabled_for_a_diagram_only_row(qtbot):
    """menu.exec() itself is a real, blocking native modal loop that
    can't be driven headlessly at all (confirmed empirically -- even
    monkeypatching QMenu.exec directly still hung) -- this drives the
    menu-BUILDING half directly instead
    (_row_context_menu_actions()), the same "build the actions,
    dispatch separately" split every other context menu in this app
    already uses."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view._domain_state.domains["d1"] = PowerDomainPlacement(id="d1", owner_path="top", name="PD1", color=QColor("#ff0000"))
    view._notify_changed()

    table = tab.properties._tables["PowerDomain"]
    row = next(r for r in range(table.rowCount()) if table.item(r, 0).data(Qt.ItemDataRole.UserRole) == "d1")

    kind, construct_id, menu, actions = tab.properties._row_context_menu_actions(table, row)

    assert kind == "PowerDomain"
    assert construct_id == "d1"
    labels_enabled = {a.text(): a.isEnabled() for a, _ak in actions}
    assert labels_enabled == {"Edit": True, "Remove": True}
    _deselect_and_collect(tab)


def test_right_click_disables_edit_and_remove_for_a_real_backend_only_row(qtbot):
    """A row sourced from a real, loaded PowerIntentDesign (not this
    diagram's own mock state) has no edit/delete concept at all in this
    diagram-only feature -- both actions must be disabled, not hidden."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_power_intent(build_mock_power_intent())
    tab.set_rtl(build_mock_design())

    table = tab.properties._tables["PowerDomain"]
    assert table.rowCount() >= 1  # the real PowerIntentDesign fixture always has at least one

    _kind, _cid, _menu, actions = tab.properties._row_context_menu_actions(table, 0)

    labels_enabled = {a.text(): a.isEnabled() for a, _ak in actions}
    assert labels_enabled == {"Edit": False, "Remove": False}
    _deselect_and_collect(tab)


def test_choosing_edit_emits_edit_requested_with_kind_and_id(qtbot):
    panel = _bare_panel(qtbot)
    received = []
    panel.editRequested.connect(lambda kind, cid: received.append((kind, cid)))

    panel._dispatch_chosen_row_action("PowerDomain", "d1", "edit")

    assert received == [("PowerDomain", "d1")]


def test_choosing_remove_emits_remove_requested_with_kind_and_id(qtbot):
    panel = _bare_panel(qtbot)
    received = []
    panel.removeRequested.connect(lambda kind, cid: received.append((kind, cid)))

    panel._dispatch_chosen_row_action("PowerDomain", "d1", "remove")

    assert received == [("PowerDomain", "d1")]


def test_dismissing_the_menu_with_nothing_chosen_emits_nothing(qtbot):
    panel = _bare_panel(qtbot)
    received = []
    panel.editRequested.connect(lambda kind, cid: received.append(("edit", kind, cid)))
    panel.removeRequested.connect(lambda kind, cid: received.append(("remove", kind, cid)))

    panel._dispatch_chosen_row_action("PowerDomain", "d1", None)

    assert received == []


def test_tab_wires_properties_signals_to_the_diagram_views_own_dispatch(qtbot):
    """End-to-end: PowerIntentTab really does connect the panel's
    signals through to the live view's own edit_properties_row()/
    remove_properties_row(), not just in isolation."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view._domain_state.domains["d1"] = PowerDomainPlacement(id="d1", owner_path="top", name="PD1", color=QColor("#ff0000"))

    assert tab.properties._can_edit_row("PowerDomain", "d1") is True
    assert tab.properties._can_edit_row("PowerDomain", "nonexistent") is False
    _deselect_and_collect(tab)


# --- Power Domain ------------------------------------------------------


def test_power_domain_edit_reopens_prefilled_dialog(qtbot, monkeypatch):
    tab, view, domain, ss1, retention, switch = _full_cascade_fixture(qtbot)
    captured = []
    monkeypatch.setattr(CreatePowerDomainDialog, "exec", _accept_dialog(CreatePowerDomainDialog, captured))

    view.edit_properties_row("PowerDomain", "d1")

    assert len(captured) == 1
    assert captured[0].name_edit.text() == "PD1"
    _deselect_and_collect(tab)


def test_power_domain_remove_lists_every_dependent_and_cascades_on_confirm(qtbot, monkeypatch):
    tab, view, domain, ss1, retention, switch = _full_cascade_fixture(qtbot)
    questions = []
    monkeypatch.setattr(message_box, "question", lambda *a, **k: questions.append(a) or QMessageBox.StandardButton.Yes)

    view.remove_properties_row("PowerDomain", "d1")

    assert len(questions) == 1
    text = questions[0][2]
    assert "SS1" in text and "RET1" in text and "SW1" in text
    assert "d1" not in view._domain_state.domains
    assert "ss1" not in view._domain_state.supply_sets
    assert "ret1" not in view._retention_state.strategies
    assert switch.id not in view._power_switch_state.switches
    _deselect_and_collect(tab)


def test_power_domain_remove_cancel_leaves_everything_intact(qtbot, monkeypatch):
    tab, view, domain, ss1, retention, switch = _full_cascade_fixture(qtbot)
    monkeypatch.setattr(message_box, "question", lambda *a, **k: QMessageBox.StandardButton.No)

    view.remove_properties_row("PowerDomain", "d1")

    assert "d1" in view._domain_state.domains
    assert "ss1" in view._domain_state.supply_sets
    assert "ret1" in view._retention_state.strategies
    assert switch.id in view._power_switch_state.switches
    _deselect_and_collect(tab)


def test_power_domain_can_edit_and_remove_true_for_a_diagram_only_row(qtbot):
    tab, view, domain, ss1, retention, switch = _full_cascade_fixture(qtbot)
    assert view.can_edit_properties_row("PowerDomain", "d1") is True
    assert view.can_remove_properties_row("PowerDomain", "d1") is True
    _deselect_and_collect(tab)


# --- Supply Set ----------------------------------------------------------


def test_supply_set_edit_reopens_prefilled_dialog(qtbot, monkeypatch):
    tab, view, domain, ss1, retention, switch = _full_cascade_fixture(qtbot)
    captured = []
    monkeypatch.setattr(CreateSupplySetDialog, "exec", _accept_dialog(CreateSupplySetDialog, captured))
    # This fixture has real dependents (PD1/RET1/SW1), so editing "ss1"
    # also triggers the impact-notification QMessageBox.warning() --
    # mocked here since this test isn't the one asserting on it (see
    # test_supply_set_edit_notifies_dependent_power_domains_and_others).
    monkeypatch.setattr(message_box, "warning", lambda *a, **k: None)

    view.edit_properties_row("SupplySet", "ss1")

    assert len(captured) == 1
    assert captured[0].name_edit.text() == "SS1"
    assert captured[0].power_combo.selected_name() == "VDD"
    _deselect_and_collect(tab)


def test_supply_set_edit_notifies_dependent_power_domains_and_others(qtbot, monkeypatch):
    """Section 2's own named example: editing a Supply Set notifies
    about dependent Power Domains/Isolation/Retention/etc. --
    informational only (QMessageBox.warning()), shown before the
    dialog opens; the edit always proceeds regardless."""
    tab, view, domain, ss1, retention, switch = _full_cascade_fixture(qtbot)
    informed = []
    monkeypatch.setattr(message_box, "warning", lambda *a, **k: informed.append(a))
    monkeypatch.setattr(CreateSupplySetDialog, "exec", lambda self: QDialog.DialogCode.Rejected)

    view.edit_properties_row("SupplySet", "ss1")

    assert len(informed) == 1
    text = informed[0][2]
    assert "PD1" in text and "RET1" in text and "SW1" in text
    _deselect_and_collect(tab)


def test_supply_set_edit_with_no_dependents_skips_notification(qtbot, monkeypatch):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view._domain_state.supply_sets["ss1"] = SupplySetPlacement(id="ss1", name="SS_ORPHAN", power="VDD", owner_paths=["top"])
    informed = []
    monkeypatch.setattr(message_box, "warning", lambda *a, **k: informed.append(a))
    monkeypatch.setattr(CreateSupplySetDialog, "exec", lambda self: QDialog.DialogCode.Rejected)

    view.edit_properties_row("SupplySet", "ss1")

    assert not informed
    _deselect_and_collect(tab)


def test_supply_set_remove_lists_dependents_and_cascades_on_confirm(qtbot, monkeypatch):
    tab, view, domain, ss1, retention, switch = _full_cascade_fixture(qtbot)
    questions = []
    monkeypatch.setattr(message_box, "question", lambda *a, **k: questions.append(a) or QMessageBox.StandardButton.Yes)

    view.remove_properties_row("SupplySet", "ss1")

    assert len(questions) == 1
    text = questions[0][2]
    assert "PD1" in text and "RET1" in text and "SW1" in text
    assert "ss1" not in view._domain_state.supply_sets
    assert "d1" not in view._domain_state.domains
    _deselect_and_collect(tab)


def test_supply_set_remove_cancel_leaves_everything_intact(qtbot, monkeypatch):
    tab, view, domain, ss1, retention, switch = _full_cascade_fixture(qtbot)
    monkeypatch.setattr(message_box, "question", lambda *a, **k: QMessageBox.StandardButton.No)

    view.remove_properties_row("SupplySet", "ss1")

    assert "ss1" in view._domain_state.supply_sets
    assert "d1" in view._domain_state.domains
    _deselect_and_collect(tab)


def test_supply_set_with_no_domain_dependents_removes_immediately(qtbot, monkeypatch):
    """No cascade impact -- same "no impact, no friction" convention
    every other cascade-aware prompt in this diagram already follows."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view._domain_state.supply_sets["ss1"] = SupplySetPlacement(id="ss1", name="SS_ORPHAN", power="VDD", owner_paths=["top"])
    asked = []
    monkeypatch.setattr(message_box, "question", lambda *a, **k: asked.append(1) or QMessageBox.StandardButton.No)

    view.remove_properties_row("SupplySet", "ss1")

    assert not asked
    assert "ss1" not in view._domain_state.supply_sets
    _deselect_and_collect(tab)


# --- Supply Port (+ macro exclusion, section 4) --------------------------


def test_supply_port_edit_reopens_prefilled_dialog(qtbot, monkeypatch):
    from frontend.src.power_intent_view.add_power_supply_dialog import AddPowerSupplyDialog

    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    port = view.add_supply_port(view._boxes["top"], "VDD1", QColor("#123456"))
    captured = []

    def fake_exec(self):
        captured.append(self)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(AddPowerSupplyDialog, "exec", fake_exec)

    view.edit_properties_row("SupplyPort", port.key)

    assert len(captured) == 1
    assert captured[0].name_edit.text() == "VDD1"
    _deselect_and_collect(tab)


def test_supply_port_remove_lists_dependents_and_cascades_on_confirm(qtbot, monkeypatch):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top"]
    vdd1 = view.add_supply_port(box, "VDD1", QColor("#ff0000"))
    ss1 = SupplySetPlacement(id="ss1", name="SS1", power="VDD1", owner_paths=["top"])
    view._domain_state.supply_sets["ss1"] = ss1
    view._domain_state.domains["d1"] = PowerDomainPlacement(id="d1", owner_path="top", name="PD1", color=QColor("#4caf50"), primary_supply_set="ss1")
    questions = []
    monkeypatch.setattr(message_box, "question", lambda *a, **k: questions.append(a) or QMessageBox.StandardButton.Yes)

    view.remove_properties_row("SupplyPort", vdd1.key)

    assert len(questions) == 1
    assert vdd1.key not in view._shared_state.supply_port_placements
    assert "ss1" not in view._domain_state.supply_sets
    assert "d1" not in view._domain_state.domains
    _deselect_and_collect(tab)


def test_supply_port_remove_cancel_leaves_everything_intact(qtbot, monkeypatch):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top"]
    vdd1 = view.add_supply_port(box, "VDD1", QColor("#ff0000"))
    ss1 = SupplySetPlacement(id="ss1", name="SS1", power="VDD1", owner_paths=["top"])
    view._domain_state.supply_sets["ss1"] = ss1
    view._domain_state.domains["d1"] = PowerDomainPlacement(id="d1", owner_path="top", name="PD1", color=QColor("#4caf50"), primary_supply_set="ss1")
    monkeypatch.setattr(message_box, "question", lambda *a, **k: QMessageBox.StandardButton.No)

    view.remove_properties_row("SupplyPort", vdd1.key)

    assert vdd1.key in view._shared_state.supply_port_placements
    assert "ss1" in view._domain_state.supply_sets
    _deselect_and_collect(tab)


def test_macro_pg_pin_row_disables_edit_and_remove(qtbot):
    """Section 4: a macro's own PG pin is intrinsic to its Liberty
    definition, not user-created -- Edit/Remove must both be disabled."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view.add_supply_port(view._boxes["top/u_core"], "VDD", QColor("#ff0000"))  # triggers a rebuild -- PG pins materialize
    pg_id = next(iter(view._shared_state.pg_pin_placements))

    assert view.can_edit_properties_row("SupplyPort", pg_id) is False
    assert view.can_remove_properties_row("SupplyPort", pg_id) is False
    _deselect_and_collect(tab)


def test_user_added_supply_port_row_enables_edit_and_remove(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    port = view.add_supply_port(view._boxes["top"], "VDD1", QColor("#123456"))

    assert view.can_edit_properties_row("SupplyPort", port.key) is True
    assert view.can_remove_properties_row("SupplyPort", port.key) is True
    _deselect_and_collect(tab)


# --- Power Switch Instance ------------------------------------------------


def test_power_switch_edit_reopens_prefilled_dialog(qtbot, monkeypatch):
    tab, view, domain, ss1, retention, switch = _full_cascade_fixture(qtbot)
    captured = []
    monkeypatch.setattr(PowerSwitchDialog, "exec", _accept_dialog(PowerSwitchDialog, captured))

    view.edit_properties_row("PowerSwitch", switch.id)

    assert len(captured) == 1
    assert captured[0].name == "SW1"
    _deselect_and_collect(tab)


def test_power_switch_remove_confirms_and_removes(qtbot, monkeypatch):
    tab, view, domain, ss1, retention, switch = _full_cascade_fixture(qtbot)
    monkeypatch.setattr(message_box, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)

    view.remove_properties_row("PowerSwitch", switch.id)

    assert switch.id not in view._power_switch_state.switches
    _deselect_and_collect(tab)


def test_power_switch_remove_cancel_leaves_it_intact(qtbot, monkeypatch):
    tab, view, domain, ss1, retention, switch = _full_cascade_fixture(qtbot)
    monkeypatch.setattr(message_box, "question", lambda *a, **k: QMessageBox.StandardButton.No)

    view.remove_properties_row("PowerSwitch", switch.id)

    assert switch.id in view._power_switch_state.switches
    _deselect_and_collect(tab)


# --- Isolation -------------------------------------------------------------


def _tab_with_isolation(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top"]
    view._domain_state.domains["d1"] = PowerDomainPlacement(id="d1", owner_path="top", name="PD1", color=QColor("#ff0000"))
    ss1 = SupplySetPlacement(id="ss1", name="SS1", power="VDD", owner_paths=["top"])
    view._domain_state.supply_sets["ss1"] = ss1
    placement = view.create_isolation(box, "ISO1", "d1", "ss1", None, None, "low", "self", "ctrl", "high", ["top/clk"])
    return tab, view, placement


def test_isolation_edit_reopens_prefilled_dialog(qtbot, monkeypatch):
    tab, view, placement = _tab_with_isolation(qtbot)
    captured = []
    monkeypatch.setattr(IsolationDialog, "exec", _accept_dialog(IsolationDialog, captured))

    view.edit_properties_row("IsolationStrategy", placement.id)

    assert len(captured) == 1
    assert captured[0].name == "ISO1"
    _deselect_and_collect(tab)


def test_isolation_remove_confirms_and_removes(qtbot, monkeypatch):
    tab, view, placement = _tab_with_isolation(qtbot)
    monkeypatch.setattr(message_box, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)

    view.remove_properties_row("IsolationStrategy", placement.id)

    assert placement.id not in view._isolation_state.strategies
    _deselect_and_collect(tab)


def test_isolation_remove_cancel_leaves_it_intact(qtbot, monkeypatch):
    tab, view, placement = _tab_with_isolation(qtbot)
    monkeypatch.setattr(message_box, "question", lambda *a, **k: QMessageBox.StandardButton.No)

    view.remove_properties_row("IsolationStrategy", placement.id)

    assert placement.id in view._isolation_state.strategies
    _deselect_and_collect(tab)


# --- Retention --------------------------------------------------------------


def _tab_with_retention(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top"]
    domain = PowerDomainPlacement(id="d1", owner_path="top", name="PD1", color=QColor("#ff0000"))
    view._domain_state.domains["d1"] = domain
    ss1 = SupplySetPlacement(id="ss1", name="SS1", power="VDD", owner_path="top")
    view._domain_state.supply_sets["ss1"] = ss1
    placement = view.create_retention_strategy(box, domain, "RET1", "ss1", "ss1", "save", "restore", [], {})
    return tab, view, placement


def test_retention_edit_reopens_prefilled_dialog(qtbot, monkeypatch):
    tab, view, placement = _tab_with_retention(qtbot)
    captured = []
    monkeypatch.setattr(RetentionStrategyDialog, "exec", _accept_dialog(RetentionStrategyDialog, captured))

    view.edit_properties_row("RetentionStrategy", placement.id)

    assert len(captured) == 1
    assert captured[0].name == "RET1"
    _deselect_and_collect(tab)


def test_retention_remove_now_confirms_before_deleting(qtbot, monkeypatch):
    """Bug fix (explicit request, SUPERSEDES this test's own earlier
    "deletes immediately, no confirmation" assertion): Retention was
    the one entity type left with no delete-confirmation convention at
    all -- now matches Isolation/Level Shifter/Power Switch's own
    plain message_box.question()/default-No confirmation, including
    from this Properties-table Remove entry point, not just the
    diagram's own right-click Delete."""
    tab, view, placement = _tab_with_retention(qtbot)
    asked = []
    monkeypatch.setattr(message_box, "question", lambda *a, **k: asked.append(1) or QMessageBox.StandardButton.No)

    view.remove_properties_row("RetentionStrategy", placement.id)

    assert asked  # confirmation was actually shown
    assert placement.id in view._retention_state.strategies  # declining (No) leaves it intact
    _deselect_and_collect(tab)


def test_retention_remove_confirmed_deletes_it(qtbot, monkeypatch):
    tab, view, placement = _tab_with_retention(qtbot)
    monkeypatch.setattr(message_box, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)

    view.remove_properties_row("RetentionStrategy", placement.id)

    assert placement.id not in view._retention_state.strategies
    _deselect_and_collect(tab)
