"""The Elements picker is SHARED by the Isolation and Level Shifter
dialogs, not copy-pasted into each (audit fix, category 07).

Before the extraction both dialogs declared their own identical
`_ELEMENT_COLUMNS`, their own identical `_ExclusiveCheckboxGroup`, their
own identical `_apply_direction_filter()` and their own four identical
button handlers. Nothing was broken -- the point is that they only
LOOKED alike and would drift, and drift had already started inside the
duplicated constant (the "Receiver Supply" / "Driver Supply Set"
asymmetry, copied identically into both).

These tests assert the sharing itself, so a future change to one dialog
can't silently re-fork the other.
"""

import ast
from pathlib import Path

from PySide6.QtCore import Qt

from frontend.src.shared.widgets import elements_table as shared


def _dialog_source(name: str) -> str:
    return (Path(__file__).parent.parent.parent / "frontend" / "src" / "power_intent_view" / f"{name}.py").read_text()


def test_neither_dialog_declares_its_own_element_columns():
    for name in ("isolation_dialog", "level_shifter_dialog"):
        assert '_ELEMENT_COLUMNS = [' not in _dialog_source(name), f"{name} re-declared the columns"


def test_neither_dialog_declares_its_own_exclusive_checkbox_group():
    for name in ("isolation_dialog", "level_shifter_dialog"):
        tree = ast.parse(_dialog_source(name))
        classes = [n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
        assert "_ExclusiveCheckboxGroup" not in classes, f"{name} re-declared the checkbox group"


def test_both_dialogs_build_their_picker_from_the_shared_helper():
    for name in ("isolation_dialog", "level_shifter_dialog"):
        assert "_build_elements_picker(" in _dialog_source(name), f"{name} builds its own table"


def test_the_exclusive_group_still_allows_no_selection(qtbot):
    """Load-bearing, and the reason the class was moved VERBATIM rather
    than tidied: Isolation's Clamp Value and Isolation Sense are
    required fields that use this same class, and rely on None meaning
    "not chosen yet" so accept() can reject it. An always-one-selected
    variant would make them impossible to leave blank, and therefore
    impossible to reject."""
    group = shared.ExclusiveCheckboxGroup(None, ("a", "b"), {"a": "A", "b": "B"})
    assert group.selected is None

    group.checkboxes["a"].setChecked(True)
    assert group.selected == "a"
    group.checkboxes["b"].setChecked(True)
    assert group.selected == "b"  # exclusive -- "a" was cleared


def test_direction_filter_treats_inout_as_matching_either_direction(qtbot):
    from PySide6.QtWidgets import QTableWidgetItem

    picker = shared.build_elements_picker(None, lambda: None, lambda: None)
    table = picker.table
    # Sorting off while populating -- the table is built with sorting
    # ENABLED, and Qt re-sorts on every setItem(), scattering rows into
    # the wrong indices mid-populate. The real dialogs already toggle it
    # off around their own populate for exactly this reason.
    table.setSortingEnabled(False)
    table.setRowCount(3)
    for row, direction in enumerate(("IN", "OUT", "INOUT")):
        name_item = QTableWidgetItem(f"sig{row}")
        name_item.setCheckState(Qt.CheckState.Unchecked)
        table.setItem(row, shared.NAME_COL, name_item)
        table.setItem(row, shared.DIRECTION_COL, QTableWidgetItem(direction))

    picker.direction_filter_group.selected = "in"
    shared.apply_direction_filter(table, picker.filter_bar, picker.direction_filter_group)
    assert [table.isRowHidden(r) for r in range(3)] == [False, True, False]  # INOUT counts as an input too

    picker.direction_filter_group.selected = "out"
    shared.apply_direction_filter(table, picker.filter_bar, picker.direction_filter_group)
    assert [table.isRowHidden(r) for r in range(3)] == [True, False, False]  # ...and as an output


def test_direction_filter_never_touches_checked_state(qtbot):
    from PySide6.QtWidgets import QTableWidgetItem

    picker = shared.build_elements_picker(None, lambda: None, lambda: None)
    table = picker.table
    table.setSortingEnabled(False)  # see the note in the test above
    table.setRowCount(2)
    for row, direction in enumerate(("IN", "OUT")):
        name_item = QTableWidgetItem(f"sig{row}")
        name_item.setCheckState(Qt.CheckState.Checked)
        table.setItem(row, shared.NAME_COL, name_item)
        table.setItem(row, shared.DIRECTION_COL, QTableWidgetItem(direction))

    picker.direction_filter_group.selected = "in"
    shared.apply_direction_filter(table, picker.filter_bar, picker.direction_filter_group)

    # The OUT row is hidden but still CHECKED -- the included-element set
    # must accumulate across filter changes.
    assert table.isRowHidden(1) is True
    assert table.item(1, shared.NAME_COL).checkState() == Qt.CheckState.Checked


def test_building_the_picker_does_not_fire_the_direction_callback(qtbot):
    """Regression: centralising construction made a latent ordering
    dependency real. Setting the "both" default fires on_change, and a
    caller's handler reads attributes it assigns from what this function
    RETURNS -- so firing during construction raised AttributeError in
    both dialogs until the default was applied before wiring."""
    calls = []
    shared.build_elements_picker(None, lambda: calls.append("direction"), lambda: None)
    assert calls == []
