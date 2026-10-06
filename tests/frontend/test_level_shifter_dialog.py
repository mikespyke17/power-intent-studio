"""level_shifter_dialog.py -- exact field layout/separators (section 2 of
the rework spec) and Cell Type's new free-text/optional shape (section 1's
"Same treatment as Power Switch" -- REVERSES the earlier "Level Shifter
Cell Type dropdown referencing a reusable library" model)."""

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QFrame

from frontend.src.power_intent_view.level_shifter_dialog import LevelShifterDialog
from frontend.src.power_intent_view.power_domain_overlay import PowerDomainPlacement
from frontend.src.power_intent_view.power_intent_tab import PowerIntentTab
from frontend.src.shared.mock_backend import build_mock_design
from frontend.src.shared.widgets.target_hierarchy_label import APPLIES_TO_CAPTION


def _dialog(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    domain = PowerDomainPlacement(id="d1", owner_path="top", name="PD_TOP", color=QColor("#ff0000"))
    view._domain_state.domains["d1"] = domain
    dialog = LevelShifterDialog(view._boxes, domain, view._resolve_connectivity, {})
    qtbot.addWidget(dialog)
    return tab, dialog


def _find_row_by_label_text(dialog, text):
    fields_form = dialog.layout().itemAt(0).layout()
    for row in range(fields_form.rowCount()):
        label_item = fields_form.itemAt(row, fields_form.ItemRole.LabelRole)
        if label_item is not None and label_item.widget() is not None and label_item.widget().text() == text:
            return row, fields_form
    raise AssertionError(f"no {text!r} row found in dialog")


def test_field_layout_and_separators_match_the_spec_exactly(qtbot):
    tab, dialog = _dialog(qtbot)

    fields_form = dialog.layout().itemAt(0).layout()
    kinds = []
    for row in range(fields_form.rowCount()):
        label_item = fields_form.itemAt(row, fields_form.ItemRole.LabelRole)
        field_item = fields_form.itemAt(row, fields_form.ItemRole.FieldRole)
        if label_item is None and field_item is not None and isinstance(field_item.widget(), QFrame):
            kinds.append("separator")
        else:
            kinds.append(label_item.widget().text())

    assert kinds == [
        APPLIES_TO_CAPTION,
        "separator",
        "Name:",
        "Power Domain:",
        # Prefix for the names of the inserted CELL instances -- distinct
        # from "Name", which names the strategy itself. Optional.
        "Name Prefix:",
        "Applies To All:",
        "Rule:",
        "Location:",
        "Threshold:",
        "Cell Type:",
        "separator",
    ]


def test_optional_label_renders_to_the_right_of_threshold_and_cell_type(qtbot):
    """Global styling rule (section 4): "(Optional)" renders to the
    RIGHT of the field it modifies, italic -- never baked into the
    row's own caption label."""
    tab, dialog = _dialog(qtbot)

    for caption, edit in (("Threshold:", dialog.threshold_edit), ("Cell Type:", dialog.cell_type_edit)):
        row, fields_form = _find_row_by_label_text(dialog, caption)
        assert caption == fields_form.itemAt(row, fields_form.ItemRole.LabelRole).widget().text()
        field_widget = fields_form.itemAt(row, fields_form.ItemRole.FieldRole).widget()
        layout = field_widget.layout()
        assert layout.itemAt(0).widget() is edit
        optional_label = layout.itemAt(1).widget()
        assert optional_label.text() == "(Optional)"
        assert optional_label.font().italic() is True


def test_cell_type_is_a_plain_optional_free_text_field(qtbot):
    """Rework (explicit request, REVERSES the earlier dropdown model):
    Cell Type is now a plain QLineEdit, not a QComboBox -- leaving it
    blank does not block OK."""
    from PySide6.QtWidgets import QLineEdit

    tab, dialog = _dialog(qtbot)

    assert isinstance(dialog.cell_type_edit, QLineEdit)
    assert not hasattr(dialog, "cell_type_combo")
    dialog.name_edit.setText("LS1")
    dialog.applies_to_group.selected = "both"
    dialog.rule_group.selected = "both"

    assert dialog.cell_type_edit.text() == ""
    assert dialog.cell_type is None
    assert dialog._ok_button.isEnabled() is True


def test_cell_type_round_trips_free_text(qtbot):
    tab, dialog = _dialog(qtbot)

    dialog.cell_type_edit.setText("LSCELL")

    assert dialog.cell_type == "LSCELL"


def test_edit_mode_prefills_cell_type_from_initial(qtbot):
    from frontend.src.power_intent_view.level_shifter_overlay import LevelShifterStrategyPlacement

    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    domain = PowerDomainPlacement(id="d1", owner_path="top", name="PD_TOP", color=QColor("#ff0000"))
    view._domain_state.domains["d1"] = domain
    initial = LevelShifterStrategyPlacement(
        id="ls1", name="LS1", power_domain_id="d1", applies_to="both", rule="both", cell_type="LSCELL", elements=[]
    )
    dialog = LevelShifterDialog(view._boxes, domain, view._resolve_connectivity, {"ls1": "LS1"}, editing_strategy_id="ls1", initial=initial)
    qtbot.addWidget(dialog)

    assert dialog.cell_type_edit.text() == "LSCELL"
    assert dialog.cell_type == "LSCELL"


def test_create_level_shifter_cell_dialog_no_longer_exists(qtbot):
    """Rework (explicit request): the reusable "Level Shifter Cell"
    library entity/dialog is removed entirely, not left disconnected."""
    import frontend.src.power_intent_view.level_shifter_dialog as mod

    assert not hasattr(mod, "CreateLevelShifterCellDialog")


def test_level_shifter_dialog_applies_to_is_capped_at_ten_hierarchies(qtbot):
    """(c): the SAME shared make_target_hierarchy_label() capping
    behavior (see test_target_hierarchy_label.py for the full coverage)
    is wired into the Level Shifter dialog too, not just tested in
    isolation -- reads directly from `domain.owner_paths`."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    paths = [f"top/u_{i}" for i in range(15)]
    domain = PowerDomainPlacement(id="d1", owner_path="top", name="PD_TOP", color=QColor("#ff0000"), owner_paths=paths)
    view._domain_state.domains["d1"] = domain
    dialog = LevelShifterDialog(view._boxes, domain, view._resolve_connectivity, {})
    qtbot.addWidget(dialog)

    row, fields_form = _find_row_by_label_text(dialog, APPLIES_TO_CAPTION)
    applies_to_value = fields_form.itemAt(row, fields_form.ItemRole.FieldRole).widget()
    lines = applies_to_value.text().split("\n")
    assert lines[:10] == paths[:10]
    assert lines[10] == "... and 5 more"


# --- "Name Prefix" ------------------------------------------------------


def test_name_prefix_is_optional_and_reaches_the_placement(qtbot):
    _tab, dialog = _dialog(qtbot)

    assert dialog.name_prefix == ""
    dialog.name_prefix_edit.setText("  ls_lvf_  ")
    assert dialog.name_prefix == "ls_lvf_"


def test_name_prefix_has_the_house_style_placeholder(qtbot):
    _tab, dialog = _dialog(qtbot)

    # A Level-Shifter-appropriate example, not Isolation's own "iso_lvf_".
    assert dialog.name_prefix_edit.placeholderText().startswith("e.g. ls_lvf_")


def test_level_shifter_has_no_isolation_signal_equivalent_to_rename(qtbot):
    """Recorded as a NON-finding: the request asked to apply the
    "Isolation Control" -> "Isolation Signal" rename to this dialog's own
    equivalent field if it had one. It does not -- a level shifter has no
    control signal at all (it is a continuous voltage translation, not a
    gated one), so there was nothing to rename."""
    _tab, dialog = _dialog(qtbot)

    assert not hasattr(dialog, "isolation_control_edit")
    assert not any(
        "signal" in name.lower() or "control" in name.lower()
        for name in vars(dialog)
    )
