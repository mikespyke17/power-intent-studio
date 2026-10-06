"""power_intent_view/power_switch_dialog.py -- Add/Edit Power Switch.

Rework (explicit request, REVERSES the earlier "Cell Type dropdown
referencing a reusable PowerSwitchCellPlacement library" model): Input
Supply Port/Output Supply Port/Control Port are now plain, required,
no-spaces free-text fields directly on the dialog/instance; Cell Type
is a genuinely separate, OPTIONAL free-text label (no longer a
dropdown, no more inline "Add new..." creation flow, no more separate
CreatePowerSwitchCellDialog).

Supply In/Supply Out remain completely UNCHANGED by this rework: both
are READ-ONLY displays (Supply Out always the target itself; Supply In
reflects whatever the caller resolved from the diagram's own top-anchor
connector), never editable/dropdown fields."""

from PySide6.QtWidgets import QDialog, QFormLayout, QFrame, QLabel, QLineEdit

from frontend.src.power_intent_view.power_domain_overlay import PowerDomainPlacement
from frontend.src.power_intent_view.power_switch_dialog import NOT_CONNECTED_TEXT, PowerSwitchDialog
from frontend.src.power_intent_view.power_switch_overlay import PowerSwitchPlacement
from frontend.src.shared.widgets.target_hierarchy_label import APPLIES_TO_CAPTION


def _domain() -> PowerDomainPlacement:
    from PySide6.QtGui import QColor

    return PowerDomainPlacement(id="d1", owner_path="top", name="PD_TOP", color=QColor("#ff0000"), primary_supply_set="s1")


def _fill_required_ports(dialog: PowerSwitchDialog) -> None:
    dialog.input_supply_port_edit.setText("in")
    dialog.output_supply_port_edit.setText("out")
    dialog.control_port_edit.setText("ctrl")
    # "Switch Control" -- the RTL signal driving the control pin. Required
    # (review follow-up section 1): the ON/OFF State expressions are
    # written in terms of it, so there is nothing to write them against
    # until it exists.
    dialog.control_signal_edit.setText("sleep_b")


def test_ok_disabled_until_name_ports_and_on_state_are_entered(qtbot):
    """OK button now gates on Name, Input/Output/Control Port, AND ON
    State -- Input/Output/Control Port are new required fields
    (rework), none of them alone is enough."""
    dialog = PowerSwitchDialog(["top"], "VDD_ALWAYS_ON", _domain(), None, {}, parent=None)
    qtbot.addWidget(dialog)

    assert dialog._ok_button.isEnabled() is False
    dialog.name_edit.setText("SW1")
    assert dialog._ok_button.isEnabled() is False  # ports/ON State still missing
    _fill_required_ports(dialog)
    assert dialog._ok_button.isEnabled() is False  # ON State still missing
    dialog.on_state_edit.setText("sleep_b")
    assert dialog._ok_button.isEnabled() is True


def test_ok_stays_disabled_if_any_one_port_field_is_blank(qtbot):
    dialog = PowerSwitchDialog(["top"], "VDD_ALWAYS_ON", _domain(), None, {}, parent=None)
    qtbot.addWidget(dialog)
    dialog.name_edit.setText("SW1")
    dialog.on_state_edit.setText("sleep_b")

    for blank_field in ("input_supply_port_edit", "output_supply_port_edit", "control_port_edit"):
        _fill_required_ports(dialog)
        getattr(dialog, blank_field).setText("")
        assert dialog._ok_button.isEnabled() is False, f"{blank_field} left blank should keep OK disabled"


def test_cell_type_is_optional_ok_enabled_with_it_blank(qtbot):
    dialog = PowerSwitchDialog(["top"], "VDD_ALWAYS_ON", _domain(), None, {}, parent=None)
    qtbot.addWidget(dialog)
    dialog.name_edit.setText("SW1")
    _fill_required_ports(dialog)
    dialog.on_state_edit.setText("sleep_b")

    assert dialog.cell_type_edit.text() == ""
    assert dialog._ok_button.isEnabled() is True
    assert dialog.cell_type is None


def test_power_domain_field_is_prefilled_and_read_only(qtbot):
    dialog = PowerSwitchDialog(["top"], "VDD_ALWAYS_ON", _domain(), None, {}, parent=None)
    qtbot.addWidget(dialog)

    assert dialog.domain_label.text() == "PD_TOP"


def test_supply_out_always_shows_the_target_name(qtbot):
    dialog = PowerSwitchDialog(["top"], "VDD_ALWAYS_ON", _domain(), None, {}, parent=None)
    qtbot.addWidget(dialog)

    assert dialog.supply_out_label.text() == "VDD_ALWAYS_ON"


def test_supply_in_shows_not_connected_when_nothing_is_wired(qtbot):
    dialog = PowerSwitchDialog(["top"], "VDD_ALWAYS_ON", _domain(), None, {}, parent=None)
    qtbot.addWidget(dialog)

    assert dialog.supply_in_label.text() == NOT_CONNECTED_TEXT


def test_supply_in_reflects_whatever_the_caller_resolved(qtbot):
    dialog = PowerSwitchDialog(["top"], "VDD_ALWAYS_ON", _domain(), "VDD_UNSWITCHED", {}, parent=None)
    qtbot.addWidget(dialog)

    assert dialog.supply_in_label.text() == "VDD_UNSWITCHED"


def test_on_off_state_round_trip(qtbot):
    dialog = PowerSwitchDialog(["top"], "VDD_ALWAYS_ON", _domain(), None, {}, parent=None)
    qtbot.addWidget(dialog)

    dialog.on_state_edit.setText("sleep_b")
    dialog.off_state_edit.setText("~sleep_b")

    assert dialog.on_state == "sleep_b"
    assert dialog.off_state == "~sleep_b"


def test_off_state_is_optional_ok_enabled_with_it_blank(qtbot):
    dialog = PowerSwitchDialog(["top"], "VDD_ALWAYS_ON", _domain(), None, {}, parent=None)
    qtbot.addWidget(dialog)

    dialog.name_edit.setText("SW1")
    _fill_required_ports(dialog)
    dialog.on_state_edit.setText("sleep_b")

    assert dialog.off_state == ""
    assert dialog._ok_button.isEnabled() is True


def test_input_output_control_port_round_trip(qtbot):
    dialog = PowerSwitchDialog(["top"], "VDD_ALWAYS_ON", _domain(), None, {}, parent=None)
    qtbot.addWidget(dialog)

    dialog.input_supply_port_edit.setText("in")
    dialog.output_supply_port_edit.setText("out")
    dialog.control_port_edit.setText("ctrl")
    dialog.cell_type_edit.setText("SWCELL")

    assert dialog.input_supply_port == "in"
    assert dialog.output_supply_port == "out"
    assert dialog.control_port == "ctrl"
    assert dialog.cell_type == "SWCELL"


def test_duplicate_name_is_rejected(qtbot, monkeypatch):
    import frontend.src.power_intent_view.power_switch_dialog as mod

    warnings = []
    monkeypatch.setattr(mod.message_box, "warning", lambda *a, **k: warnings.append(a))
    dialog = PowerSwitchDialog(["top"], "VDD_ALWAYS_ON", _domain(), None, {"sw1": "SW1"}, parent=None)
    qtbot.addWidget(dialog)
    dialog.name_edit.setText("SW1")
    _fill_required_ports(dialog)
    dialog.on_state_edit.setText("sleep_b")

    dialog.accept()

    assert warnings
    assert dialog.result() != QDialog.DialogCode.Accepted


def test_editing_excludes_its_own_current_name_from_the_duplicate_check(qtbot):
    dialog = PowerSwitchDialog(
        ["top"], "VDD_ALWAYS_ON", _domain(), None, {"sw1": "SW1"}, editing_switch_id="sw1",
        initial=PowerSwitchPlacement(
            id="sw1", name="SW1", target_id="port1", owner_path="top", power_domain_id="d1",
            input_supply_port="in", output_supply_port="out", control_port="ctrl", control_signal="sleep_b",
            on_state="sleep_b",
        ),
        parent=None,
    )
    qtbot.addWidget(dialog)

    dialog.accept()  # name unchanged ("SW1") -- must NOT be rejected as a duplicate of itself

    assert dialog.result() == QDialog.DialogCode.Accepted


def test_accept_rejects_a_space_in_any_port_field(qtbot, monkeypatch):
    import frontend.src.power_intent_view.power_switch_dialog as mod

    monkeypatch.setattr(mod.message_box, "warning", lambda *a, **k: None)
    for field_name in ("input_supply_port_edit", "output_supply_port_edit", "control_port_edit"):
        dialog = PowerSwitchDialog(["top"], "VDD_ALWAYS_ON", _domain(), None, {}, parent=None)
        dialog.name_edit.setText("SW1")
        _fill_required_ports(dialog)
        dialog.on_state_edit.setText("sleep_b")
        getattr(dialog, field_name).setText("has space")

        dialog.accept()

        assert dialog.result() != QDialog.DialogCode.Accepted, f"{field_name} with a space should have been rejected"


def test_edit_mode_prefills_every_field_from_initial(qtbot):
    initial = PowerSwitchPlacement(
        id="sw1", name="SW1", target_id="port1", owner_path="top", power_domain_id="d1",
        input_supply_port="in", output_supply_port="out", control_port="ctrl", control_signal="sleep_b",
        cell_type="SWCELL", on_state="sleep_b", off_state="~sleep_b",
    )
    dialog = PowerSwitchDialog(
        ["top"], "VDD_ALWAYS_ON", _domain(), "VDD_UNSWITCHED", {"sw1": "SW1"}, editing_switch_id="sw1", initial=initial, parent=None
    )
    qtbot.addWidget(dialog)

    assert dialog.name == "SW1"
    assert dialog.input_supply_port == "in"
    assert dialog.output_supply_port == "out"
    assert dialog.control_port == "ctrl"
    assert dialog.cell_type == "SWCELL"
    assert dialog.on_state == "sleep_b"
    assert dialog.off_state == "~sleep_b"
    assert dialog.supply_out_label.text() == "VDD_ALWAYS_ON"
    assert dialog.supply_in_label.text() == "VDD_UNSWITCHED"


def test_edit_mode_prefills_a_blank_cell_type_as_none(qtbot):
    initial = PowerSwitchPlacement(
        id="sw1", name="SW1", target_id="port1", owner_path="top", power_domain_id="d1",
        input_supply_port="in", output_supply_port="out", control_port="ctrl", control_signal="sleep_b",
        on_state="sleep_b",
    )
    dialog = PowerSwitchDialog(["top"], "VDD_ALWAYS_ON", _domain(), None, {"sw1": "SW1"}, editing_switch_id="sw1", initial=initial, parent=None)
    qtbot.addWidget(dialog)

    assert dialog.cell_type_edit.text() == ""
    assert dialog.cell_type is None


def test_window_title_reflects_add_vs_edit(qtbot):
    add_dialog = PowerSwitchDialog(["top"], "VDD_ALWAYS_ON", _domain(), None, {}, parent=None)
    qtbot.addWidget(add_dialog)
    edit_dialog = PowerSwitchDialog(
        ["top"], "VDD_ALWAYS_ON", _domain(), None, {}, editing_switch_id="sw1",
        initial=PowerSwitchPlacement(id="sw1", name="SW1", target_id="port1", owner_path="top", power_domain_id="d1"),
        parent=None,
    )
    qtbot.addWidget(edit_dialog)

    assert add_dialog.windowTitle() == "Add Power Switch"
    assert edit_dialog.windowTitle() == "Edit Power Switch"


# --- item 3: "Applies to" for a multi-hierarchy Power Switch ---------------
# Reuses shared/widgets/target_hierarchy_label.py's own make_target_hierarchy_label()/
# APPLIES_TO_CAPTION -- the SAME widget/logic RetentionStrategyDialog
# already uses, not a separate implementation.


def _find_row_by_label_text(dialog, text):
    fields_form = dialog.layout().itemAt(0).layout()
    for row in range(fields_form.rowCount()):
        label_item = fields_form.itemAt(row, fields_form.ItemRole.LabelRole)
        if label_item is not None and label_item.widget() is not None and label_item.widget().text() == text:
            return row, fields_form
    raise AssertionError(f"no {text!r} row found in dialog")


def _find_applies_to_value_label(dialog):
    row, fields_form = _find_row_by_label_text(dialog, APPLIES_TO_CAPTION)
    return fields_form.itemAt(row, fields_form.ItemRole.FieldRole).widget()


def test_applies_to_shows_a_single_path_for_a_single_hierarchy_switch(qtbot):
    dialog = PowerSwitchDialog(["top"], "VDD_ALWAYS_ON", _domain(), None, {}, parent=None)
    qtbot.addWidget(dialog)

    assert _find_applies_to_value_label(dialog).text() == "top"


def test_applies_to_lists_all_three_paths_for_a_multi_hierarchy_switch(qtbot):
    """Explicit regression request: a Power Switch on an Internal Supply
    belonging to a multi-hierarchy Power Domain spanning 3 hierarchies
    -- "Applies to:" lists all 3 paths on separate lines, matching the
    Retention dialog's own existing multi-line formatting exactly."""
    paths = ["top/u_core", "top/u_dbg", "top/u_fwd"]
    dialog = PowerSwitchDialog(paths, "VDD_SW", _domain(), None, {}, parent=None)
    qtbot.addWidget(dialog)

    assert _find_applies_to_value_label(dialog).text() == "\n".join(paths)


# --- section 1: exact field layout + separators -----------------------------


def test_field_layout_and_separators_match_the_spec_exactly(qtbot):
    dialog = PowerSwitchDialog(["top"], "VDD_ALWAYS_ON", _domain(), None, {}, parent=None)
    qtbot.addWidget(dialog)

    fields_form: QFormLayout = dialog.layout().itemAt(0).layout()
    kinds = []
    for row in range(fields_form.rowCount()):
        label_item = fields_form.itemAt(row, fields_form.ItemRole.LabelRole)
        field_item = fields_form.itemAt(row, fields_form.ItemRole.FieldRole)
        if label_item is None and field_item is not None and isinstance(field_item.widget(), QFrame):
            kinds.append("separator")
        elif label_item is None and field_item is not None and field_item.layout() is not None:
            # The paired two-column block: a spanning row with no label
            # of its own, because it carries its own labels in BOTH
            # columns. Asserted in detail by the paired-rows test below.
            kinds.append("paired grid")
        else:
            kinds.append(label_item.widget().text())

    assert kinds == [
        # Applies to / Name / Power Domain are one group, before the
        # first separator -- confirmed unchanged in purpose; only the
        # separator that used to split "Applies to" from "Name" is gone,
        # matching the specified structure.
        APPLIES_TO_CAPTION,
        "Name:",
        "Power Domain:",
        "separator",
        "Cell Type:",
        # Input/Output/Control/Ack Port each paired with the real value
        # it connects to -- see test_the_paired_rows_are_two_column().
        # Supply In/Supply Out moved INTO this block from their own
        # former section, and "Switch Control" was renamed to "Control
        # Signal" and moved here from beside the State fields.
        "paired grid",
        "separator",
        "ON State:",
        "OFF State:",
    ]


def test_optional_label_renders_to_the_right_of_off_state_and_cell_type(qtbot):
    """Global styling rule (section 4): "(Optional)" renders to the
    RIGHT of the field it modifies, italic -- never baked into the
    row's own caption label."""
    dialog = PowerSwitchDialog(["top"], "VDD_ALWAYS_ON", _domain(), None, {}, parent=None)
    qtbot.addWidget(dialog)

    for caption, edit in (("Cell Type:", dialog.cell_type_edit), ("OFF State:", dialog.off_state_edit)):
        row, fields_form = _find_row_by_label_text(dialog, caption)
        assert caption == fields_form.itemAt(row, fields_form.ItemRole.LabelRole).widget().text()
        field_widget = fields_form.itemAt(row, fields_form.ItemRole.FieldRole).widget()
        layout = field_widget.layout()
        assert layout.itemAt(0).widget() is edit
        optional_label = layout.itemAt(1).widget()
        assert optional_label.text() == "(Optional)"
        assert optional_label.font().italic() is True


def test_create_power_switch_cell_dialog_no_longer_exists(qtbot):
    """Rework (explicit request): the reusable "Power Switch Cell"
    library entity/dialog is removed entirely, not left disconnected."""
    import frontend.src.power_intent_view.power_switch_dialog as mod

    assert not hasattr(mod, "CreatePowerSwitchCellDialog")


# --- "Switch Control" (review follow-up, section 1) ---------------------


def _filled(dialog: PowerSwitchDialog) -> PowerSwitchDialog:
    dialog.name_edit.setText("SW1")
    _fill_required_ports(dialog)
    dialog.on_state_edit.setText("sleep_b")
    return dialog


def test_switch_control_is_required(qtbot):
    """No "Optional" marker in the spec's own layout, unlike Cell Type
    and OFF State -- and the State expressions are written in terms of
    it, so there is nothing to write them against until it exists."""
    dialog = _filled(PowerSwitchDialog(["top"], "VDD_ALWAYS_ON", _domain(), None, {}, parent=None))
    qtbot.addWidget(dialog)

    assert dialog._ok_button.isEnabled() is True
    dialog.control_signal_edit.setText("")
    assert dialog._ok_button.isEnabled() is False


def test_switch_control_round_trips_through_the_dialog(qtbot):
    dialog = PowerSwitchDialog(
        ["top"], "VDD_ALWAYS_ON", _domain(), None, {},
        initial=PowerSwitchPlacement(
            id="sw1", name="SW1", target_id="port1", owner_path="top", power_domain_id="d1",
            input_supply_port="in", output_supply_port="out", control_port="ctrl",
            control_signal="pwr_en_cpu", on_state="pwr_en_cpu",
        ),
        parent=None,
    )
    qtbot.addWidget(dialog)

    assert dialog.control_signal_edit.text() == "pwr_en_cpu"
    assert dialog.control_signal == "pwr_en_cpu"
    # ...and it stays distinct from the PIN name beside it.
    assert dialog.control_port == "ctrl"


def test_the_state_hint_warns_only_when_an_expression_ignores_the_control_signal(qtbot):
    """Case (c) of the substitution spec, at the point the user can still
    act on it. Deliberately an inline label, NOT a modal at accept time:
    a modal there blocks headlessly forever, and a warning shown only
    after the user has committed is worse guidance."""
    dialog = _filled(PowerSwitchDialog(["top"], "VDD_ALWAYS_ON", _domain(), None, {}, parent=None))
    qtbot.addWidget(dialog)

    # _fill_required_ports sets Switch Control to "sleep_b", which the ON
    # State above references -- no warning.
    assert dialog.stray_state_expressions() == []
    assert "⚠" not in dialog.state_hint.text()

    dialog.on_state_edit.setText("something_else")
    assert dialog.stray_state_expressions() == ["ON State"]
    assert "⚠" in dialog.state_hint.text()
    assert "sleep_b" in dialog.state_hint.text()  # names the real signal, not a guessed token

    # ...and it is only a hint: accept still succeeds.
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted


def test_a_longer_signal_containing_the_control_signal_does_not_count_as_a_reference(qtbot):
    dialog = _filled(PowerSwitchDialog(["top"], "VDD_ALWAYS_ON", _domain(), None, {}, parent=None))
    qtbot.addWidget(dialog)
    dialog.control_signal_edit.setText("pwr_en")
    dialog.on_state_edit.setText("pwr_en_cpu")

    assert dialog.stray_state_expressions() == ["ON State"]


# --- (a) the paired two-column block ------------------------------------


def _paired_rows(dialog: PowerSwitchDialog) -> list[tuple[str, str]]:
    """(left label, right label) per row of the paired grid."""
    grid = dialog.paired_grid
    rows = []
    for row in range(grid.rowCount()):
        left = grid.itemAtPosition(row, 0).widget().text()
        right = grid.itemAtPosition(row, 2).widget().text()
        rows.append((left, right))
    return rows


def test_the_paired_rows_are_two_column_in_the_specified_order(qtbot):
    """Each row is a library cell PIN on the left and the real thing it
    connects to on the right. The pairing is the point: it is what makes
    Control Port (a cell pin) and Control Signal (an RTL net) read as two
    halves of one fact rather than two unrelated fields."""
    dialog = PowerSwitchDialog(["top"], "VDD_ALWAYS_ON", _domain(), None, {}, parent=None)
    qtbot.addWidget(dialog)

    assert _paired_rows(dialog) == [
        ("Input Supply Port:", "Supply In:"),
        ("Output Supply Port:", "Supply Out:"),
        ("Control Port:", "Control Signal:"),
        ("Ack Port:", "Ack Signal:"),
    ]


def test_the_paired_grid_puts_each_field_in_its_own_column(qtbot):
    """Column 1 is the pin field, column 3 the real-value field -- a real
    QGridLayout rather than four independent horizontal boxes, so the
    right-hand column actually lines up across all four rows."""
    dialog = PowerSwitchDialog(["top"], "VDD_ALWAYS_ON", _domain(), None, {}, parent=None)
    qtbot.addWidget(dialog)
    grid = dialog.paired_grid

    def widgets_at(column):
        return [grid.itemAtPosition(r, column).widget() for r in range(grid.rowCount())]

    assert dialog.input_supply_port_edit in widgets_at(1)
    assert dialog.control_port_edit in widgets_at(1)
    # The optional pair is wrapped, so its edit is a descendant rather
    # than the cell widget itself.
    assert dialog.ack_port_edit in widgets_at(1) or any(
        dialog.ack_port_edit in w.findChildren(QLineEdit) for w in widgets_at(1)
    )
    assert dialog.supply_in_label in widgets_at(3)
    assert dialog.control_signal_edit in widgets_at(3)


def test_supply_in_and_supply_out_stay_read_only_in_their_new_position(qtbot):
    """They MOVED, nothing about them changed: still auto-resolved
    display labels, never editable, so the diagram's own connector
    remains the only thing that decides them."""
    dialog = PowerSwitchDialog(["top"], "VDD_TARGET", _domain(), "VDD_SRC", {}, parent=None)
    qtbot.addWidget(dialog)

    assert isinstance(dialog.supply_in_label, QLabel)
    assert isinstance(dialog.supply_out_label, QLabel)
    assert "VDD_SRC" in dialog.supply_in_label.text()
    assert dialog.supply_out_label.text() == "VDD_TARGET"


def test_name_and_power_domain_survive_the_restructure(qtbot):
    """Confirmed rather than assumed: both are required for entity
    identity and are still present, grouped with "Applies to" before the
    first separator. Power Domain stays a READ-ONLY label -- a switch's
    domain is derived from its target's domain, never chosen."""
    dialog = PowerSwitchDialog(["top"], "VDD_ALWAYS_ON", _domain(), None, {}, parent=None)
    qtbot.addWidget(dialog)

    assert isinstance(dialog.name_edit, QLineEdit)
    assert isinstance(dialog.domain_label, QLabel)
    assert dialog.domain_label.text() == "PD_TOP"


# --- (b) the acknowledge pair is optional -------------------------------


def test_the_acknowledge_pair_is_optional_for_accept(qtbot):
    dialog = _filled(PowerSwitchDialog(["top"], "VDD_ALWAYS_ON", _domain(), None, {}, parent=None))
    qtbot.addWidget(dialog)

    assert dialog.ack_port == "" and dialog.ack_signal == ""
    assert dialog._ok_button.isEnabled() is True
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted


def test_a_given_acknowledge_value_still_has_to_be_a_legal_name(qtbot, monkeypatch):
    """Optional means "may be empty", not "unvalidated"."""
    import frontend.src.power_intent_view.power_switch_dialog as mod

    warned = []
    monkeypatch.setattr(mod.message_box, "warning", lambda *a, **k: warned.append(a[1]))
    dialog = _filled(PowerSwitchDialog(["top"], "VDD_ALWAYS_ON", _domain(), None, {}, parent=None))
    qtbot.addWidget(dialog)
    dialog.ack_port_edit.setText("has a space")

    dialog.accept()
    assert warned == ["Invalid Ack Port"]
    assert dialog.result() != QDialog.DialogCode.Accepted


def test_the_acknowledge_pair_round_trips_through_the_dialog(qtbot):
    dialog = PowerSwitchDialog(
        ["top"], "VDD_ALWAYS_ON", _domain(), None, {},
        initial=PowerSwitchPlacement(
            id="sw1", name="SW1", target_id="port1", owner_path="top", power_domain_id="d1",
            input_supply_port="in", output_supply_port="out", control_port="ctrl",
            control_signal="pwr_en", ack_port="ACKO", ack_signal="pwr_ack", on_state="pwr_en",
        ),
        parent=None,
    )
    qtbot.addWidget(dialog)

    assert (dialog.ack_port, dialog.ack_signal) == ("ACKO", "pwr_ack")
