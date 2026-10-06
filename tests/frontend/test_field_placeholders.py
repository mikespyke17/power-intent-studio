"""Every fillable field tells the user what belongs in it.

Written as a SWEEP rather than one assertion per field on purpose: the
value of this is catching the field that gets added next, by someone who
has not read the convention. A per-field list would pass forever while
new fields arrive without hints.

Scope is genuinely free-text widgets only. A dropdown, checkbox, colour
picker or read-only display is self-explanatory by its own nature and is
skipped -- including the QLineEdit Qt builds INSIDE an editable QComboBox,
which is not a field the user types a fresh value into.
"""

from __future__ import annotations

import pytest
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QComboBox, QLineEdit

from frontend.src.power_intent_view.add_power_supply_dialog import AddPowerSupplyDialog
from frontend.src.power_intent_view.create_power_domain_dialog import (
    CreatePowerDomainDialog,
    CreateSupplySetDialog,
)
from frontend.src.power_intent_view.isolation_dialog import IsolationDialog
from frontend.src.power_intent_view.level_shifter_dialog import LevelShifterDialog
from frontend.src.power_intent_view.port_attribute_dialog import PortAttributeDialog
from frontend.src.power_intent_view.power_domain_overlay import PowerDomainPlacement
from frontend.src.power_intent_view.power_switch_dialog import PowerSwitchDialog
from frontend.src.power_intent_view.retention_dialog import RetentionStrategyDialog

_COLOR = QColor("#ff0000")


def _domain() -> PowerDomainPlacement:
    return PowerDomainPlacement(id="d1", owner_path="top", name="PD_TOP", color=_COLOR)


def _free_text_line_edits(widget) -> list[QLineEdit]:
    """Every QLineEdit the user types a fresh value into.

    Excludes the one Qt creates inside an editable QComboBox: that is a
    dropdown wearing a text box, and the options themselves are the
    hint."""
    return [
        line_edit
        for line_edit in widget.findChildren(QLineEdit)
        if not isinstance(line_edit.parent(), QComboBox)
    ]


# Minimal constructor arguments, matching the set
# test_dialog_resizing_and_messages.py already verified against each
# class definition -- reused rather than re-derived so the two sweeps
# cannot drift into disagreeing about how a dialog is built.
def _resolve_connectivity(_elements):
    return ({}, {})


_DIALOGS = {
    "Power Switch": lambda: PowerSwitchDialog(["top"], "top", _domain(), None, {}),
    "Isolation": lambda: IsolationDialog({}, {}, {}, _resolve_connectivity, {}),
    "Retention": lambda: RetentionStrategyDialog(_domain(), {}, [], [], set(), {}),
    "Level Shifter": lambda: LevelShifterDialog({}, _domain(), _resolve_connectivity, {}),
    "Supply Port / Internal Supply / Virtual Supply": lambda: AddPowerSupplyDialog(target_path="top"),
    "Supply Set": lambda: CreateSupplySetDialog([]),
    "Power Domain": lambda: CreatePowerDomainDialog({}, [], {}, "top"),
    "Port Attributes": lambda: PortAttributeDialog([], {}, [], "top"),
}


@pytest.mark.parametrize("label", sorted(_DIALOGS))
def test_every_free_text_field_in_every_dialog_has_placeholder_text(qtbot, label):
    dialog = _DIALOGS[label]()
    qtbot.addWidget(dialog)

    missing = [
        line_edit.objectName() or line_edit.accessibleName() or repr(line_edit)
        for line_edit in _free_text_line_edits(dialog)
        if not line_edit.placeholderText().strip()
    ]
    assert missing == [], f"{label}: free-text field(s) with no placeholder hint: {missing}"


@pytest.mark.parametrize("label", sorted(_DIALOGS))
def test_every_placeholder_follows_the_house_style(qtbot, label):
    """"e.g. <realistic example>", optionally followed by an em-dash
    clarification. Consistency is the point: a hint written in a
    different voice reads as an error message rather than an example.

    A filter box is exempt -- it takes a PATTERN, not a value, and its
    own placeholder has to keep explaining the wildcard syntax (see
    table_filter._filter_placeholder)."""
    dialog = _DIALOGS[label]()
    qtbot.addWidget(dialog)

    offenders = [
        text
        for line_edit in _free_text_line_edits(dialog)
        if (text := line_edit.placeholderText().strip())
        and "wildcards" not in text
        and not text.startswith("e.g. ")
    ]
    assert offenders == [], f"{label}: placeholder(s) not in 'e.g. ...' style: {offenders}"


def test_the_sweep_actually_inspects_something(qtbot):
    """Guards the two sweeps above from passing vacuously.

    Both are "no offenders" assertions, which an empty list satisfies --
    so if _free_text_line_edits() ever stopped finding fields (a Qt
    parenting change, a dialog that builds its form lazily), they would
    go green while checking nothing at all. The floor is deliberately
    well below the current count: this is a smoke check, not a second
    place to update whenever a field is added."""
    found = sum(len(_free_text_line_edits(build())) for build in _DIALOGS.values())
    assert found >= 15, f"the placeholder sweep only found {found} free-text fields"


def test_the_power_switch_dialog_matches_the_specified_reference_text(qtbot):
    """The reference the whole convention is derived from, pinned exactly
    -- every other dialog is only required to match its SHAPE, so this is
    the one place the wording itself is checked.

    The three port descriptions changed from "the library cell's … pin
    name" when the abstract-token layer was retired: these fields ARE the
    switch's own port names now, declared directly by
    create_power_switch, with no cell-pin mapping in between. The labels
    themselves are unchanged pending a decision on renaming them."""
    dialog = _DIALOGS["Power Switch"]()
    qtbot.addWidget(dialog)

    assert dialog.name_edit.placeholderText() == "e.g. PSW_CPU"
    assert dialog.input_supply_port_edit.placeholderText() == "e.g. IN — the switch's own input port name"
    assert dialog.output_supply_port_edit.placeholderText() == "e.g. OUT — the switch's own output port name"
    assert dialog.control_port_edit.placeholderText() == "e.g. CTRL — the switch's own control port name"
    assert dialog.cell_type_edit.placeholderText() == "e.g. PSWITCH_X4 — the mapped library cell name"
    assert dialog.control_signal_edit.placeholderText() == "e.g. pwr_en_cpu — the RTL signal driving the control port"
    assert dialog.on_state_edit.placeholderText() == "e.g. pwr_en_cpu — boolean expression for when the switch is ON"
    assert dialog.off_state_edit.placeholderText() == "e.g. !pwr_en_cpu — boolean expression for when the switch is OFF"


def test_the_boolean_expression_fields_carry_a_supplementary_tooltip(qtbot):
    """The one case the placeholder alone under-sells: the example is a
    bare signal name, and the field accepts a full expression."""
    dialog = _DIALOGS["Power Switch"]()
    qtbot.addWidget(dialog)

    for line_edit in (dialog.on_state_edit, dialog.off_state_edit):
        assert "boolean expression" in line_edit.toolTip()
        assert "&&" in line_edit.toolTip()  # shows that operators are allowed


def test_a_placeholder_does_not_stand_in_for_a_value(qtbot):
    """Placeholder text is a HINT, never content: a field showing one is
    still empty, and must not satisfy a required-field gate."""
    dialog = _DIALOGS["Power Switch"]()
    qtbot.addWidget(dialog)

    assert dialog.name_edit.text() == ""
    assert dialog.name_edit.placeholderText() != ""
    assert dialog._ok_button.isEnabled() is False


# --- fields that live in table cells, built per row ---------------------


def test_the_power_states_voltage_cell_has_a_placeholder(qtbot):
    """A levelled state REQUIRES a voltage, and "1.2" is the shape it
    wants -- not "1.2V", not "1200m"."""
    from frontend.src.power_intent_view.supply_port_states_widget import SupplyPortStatesWidget

    widget = SupplyPortStatesWidget()
    qtbot.addWidget(widget)
    widget._add_row()

    # Two free-text cells per row now -- Name and Voltage -- so each is
    # checked for its own hint rather than asserting one blanket value.
    placeholders = {edit.placeholderText() for edit in _free_text_line_edits(widget)}
    assert "e.g. 1.2" in placeholders   # Voltage
    assert "e.g. ON" in placeholders    # Name


def test_the_power_state_table_row_name_cell_has_a_placeholder(qtbot):
    from PySide6.QtGui import QUndoStack

    from frontend.src.hierarchy_view.selection_controller import HierarchySelectionController
    from frontend.src.power_intent_view.power_intent_diagram_view import PowerIntentDiagramView
    from frontend.src.power_intent_view.power_state_table_tab import PowerStateTableTab
    from frontend.src.power_intent_view.selection_controller import PowerIntentSelectionController

    diagram = PowerIntentDiagramView(
        HierarchySelectionController(), PowerIntentSelectionController(), QUndoStack()
    )
    qtbot.addWidget(diagram)
    tab = PowerStateTableTab(diagram)
    qtbot.addWidget(tab)
    tab.add_state_button.click()

    name_cells = [
        cell for cell in _free_text_line_edits(tab.table)
        if cell.placeholderText().startswith("e.g. ON")
    ]
    assert name_cells, "the State Name cell has no placeholder"


# --- filter boxes: a PATTERN, not a value -------------------------------


def test_a_filter_box_keeps_explaining_its_wildcards_and_gains_an_example():
    from frontend.src.shared.widgets.table_filter import _filter_placeholder

    # Without an example, unchanged -- the generic text every table used
    # before still applies where no realistic example exists.
    assert "wildcards" in _filter_placeholder(None)
    # With one, the example is ADDED, never substituted: a user must not
    # lose the explanation of what * and ? do just because the table
    # gained an example.
    with_example = _filter_placeholder("data_out*")
    assert "data_out*" in with_example
    assert "wildcards" in with_example
