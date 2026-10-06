"""Level Shifter "Applies To All" rework (explicit follow-up request,
superseding the original "Elements always required" design):

  - "Applies To:" renamed "Applies To All:".
  - A 4th mutually-exclusive option, "Elements", added alongside
    Inputs/Outputs/Both.
  - Elements is REQUIRED only when "Elements" is the selected choice --
    Inputs/Outputs/Both apply broadly, with no explicit element
    selection needed (and the Elements list widget is DISABLED in that
    case, per the follow-up correction that replaced an earlier
    "show a non-blocking warning" design entirely).
"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QDialog, QMessageBox

from frontend.src.power_intent_view.level_shifter_dialog import LevelShifterDialog
from frontend.src.power_intent_view.power_domain_overlay import PowerDomainPlacement
from frontend.src.power_intent_view.power_intent_tab import PowerIntentTab
from frontend.src.shared.mock_backend import build_mock_design


def _dialog(qtbot, monkeypatch):
    import frontend.src.power_intent_view.level_shifter_dialog as mod

    monkeypatch.setattr(mod.message_box, "warning", lambda *a, **k: None)
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top"]
    domain = PowerDomainPlacement(id="d1", owner_path="top", name="PD_TOP", color=QColor("#ff0000"))
    view._domain_state.domains["d1"] = domain
    dialog = LevelShifterDialog(view._boxes, domain, view._resolve_connectivity, {})
    qtbot.addWidget(dialog)
    dialog.name_edit.setText("LS1")
    dialog.rule_group.selected = "both"
    return tab, dialog


# --- (a)/(b): "Elements" requires at least one checked element --------------


def test_elements_selected_with_empty_list_is_rejected(qtbot, monkeypatch):
    tab, dialog = _dialog(qtbot, monkeypatch)
    dialog.applies_to_group.selected = "elements"

    dialog.accept()

    assert dialog.result() != QDialog.DialogCode.Accepted


def test_elements_selected_with_at_least_one_checked_is_accepted(qtbot, monkeypatch):
    tab, dialog = _dialog(qtbot, monkeypatch)
    dialog.applies_to_group.selected = "elements"
    assert dialog.elements_table.rowCount() > 0
    dialog.elements_table.item(0, 0).setCheckState(Qt.CheckState.Checked)

    dialog.accept()

    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.elements == [dialog.elements_table.item(0, 0).data(Qt.ItemDataRole.UserRole)]


# --- (c): Inputs/Outputs/Both never require Elements ------------------------


def test_inputs_selected_with_empty_elements_is_accepted(qtbot, monkeypatch):
    tab, dialog = _dialog(qtbot, monkeypatch)
    dialog.applies_to_group.selected = "inputs"

    dialog.accept()

    assert dialog.result() == QDialog.DialogCode.Accepted


def test_outputs_selected_with_empty_elements_is_accepted(qtbot, monkeypatch):
    tab, dialog = _dialog(qtbot, monkeypatch)
    dialog.applies_to_group.selected = "outputs"

    dialog.accept()

    assert dialog.result() == QDialog.DialogCode.Accepted


def test_both_selected_with_empty_elements_is_accepted(qtbot, monkeypatch):
    tab, dialog = _dialog(qtbot, monkeypatch)
    dialog.applies_to_group.selected = "both"

    dialog.accept()

    assert dialog.result() == QDialog.DialogCode.Accepted


# --- (d): Inputs/Outputs/Both + some elements STILL checked underneath -----


def test_broad_choice_with_leftover_checked_elements_is_accepted_and_elements_are_ignored(qtbot, monkeypatch):
    """Explicit correction: no warning at all -- broad choices simply
    never submit Elements, regardless of whatever is still checked in
    the (disabled) table underneath."""
    from PySide6.QtCore import Qt

    tab, dialog = _dialog(qtbot, monkeypatch)
    dialog.applies_to_group.selected = "elements"
    dialog.elements_table.item(0, 0).setCheckState(Qt.CheckState.Checked)
    dialog.applies_to_group.selected = "inputs"  # switch away -- leftover checkmark deliberately left in place

    assert dialog.elements == []  # irrelevant while a broad choice is active

    dialog.accept()

    assert dialog.result() == QDialog.DialogCode.Accepted


# --- Elements widget enable/disable ------------------------------------------


def test_elements_widget_starts_disabled_by_default(qtbot, monkeypatch):
    tab, dialog = _dialog(qtbot, monkeypatch)
    assert dialog.elements_group_widget.isEnabled() is False


def test_elements_widget_enabled_only_when_elements_is_selected(qtbot, monkeypatch):
    tab, dialog = _dialog(qtbot, monkeypatch)

    dialog.applies_to_group.selected = "inputs"
    assert dialog.elements_group_widget.isEnabled() is False
    dialog.applies_to_group.selected = "outputs"
    assert dialog.elements_group_widget.isEnabled() is False
    dialog.applies_to_group.selected = "both"
    assert dialog.elements_group_widget.isEnabled() is False
    dialog.applies_to_group.selected = "elements"
    assert dialog.elements_group_widget.isEnabled() is True


def test_switching_away_from_elements_preserves_the_checkmarks_rather_than_clearing_them(qtbot, monkeypatch):
    """Explicit decision (the task left this open): checkmarks are
    preserved, not force-cleared, so switching back to "Elements" later
    restores the user's own prior selection."""
    from PySide6.QtCore import Qt

    tab, dialog = _dialog(qtbot, monkeypatch)
    dialog.applies_to_group.selected = "elements"
    dialog.elements_table.item(0, 0).setCheckState(Qt.CheckState.Checked)

    dialog.applies_to_group.selected = "both"
    assert dialog.elements_table.item(0, 0).checkState() == Qt.CheckState.Checked  # not cleared

    dialog.applies_to_group.selected = "elements"
    assert dialog.elements_table.item(0, 0).checkState() == Qt.CheckState.Checked  # restored/still there
    assert dialog.elements == [dialog.elements_table.item(0, 0).data(Qt.ItemDataRole.UserRole)]


# --- label rename -------------------------------------------------------


def test_applies_to_all_label_is_used_not_applies_to(qtbot, monkeypatch):
    tab, dialog = _dialog(qtbot, monkeypatch)
    from PySide6.QtWidgets import QLabel

    labels = [label.text() for label in dialog.findChildren(QLabel)]
    assert "Applies To All:" in labels
    assert "Applies To:" not in labels
