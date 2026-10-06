"""The shared Elements picker used by the Isolation and Level Shifter
dialogs -- a checkable element table with a wildcard text filter, an
exclusive Inputs/Outputs/Both direction filter, and the four
Add/Clear Selected/All buttons.

Extracted by the 2026-09-11 codebase health audit (category 07), which
found the two dialogs carrying byte-identical copies of all of it: the
same `_ELEMENT_COLUMNS` list declared separately in each file, the same
`_ExclusiveCheckboxGroup` class, the same `_apply_direction_filter()`
logic, and the same four button handlers. Nothing was broken -- the
finding was that they merely LOOKED alike and would drift, and drift had
already begun inside the duplicated constant itself: the columns read
"Receiver Supply" against "Driver Supply **Set**", an asymmetry copied
identically into both files (left as-is here; renaming a visible column
header is a product decision, not a refactor, and both dialogs at least
now share one definition to change).

Retention deliberately does NOT use this. Its Elements picker is a
QListWidget of sequential-element paths with no Direction concept at
all, so it has nothing to share beyond the four button labels -- forcing
a common abstraction over both shapes would cost more than the
duplication it removed. That is a judgement, not an oversight.

The dialogs keep their own `elements_table`/`elements_filter`/
`direction_filter_group`/`add_selected_button`/... attributes, assigned
from what `build_elements_picker()` returns, so their existing
`accept()`, populate and test code needed no changes.
"""

from __future__ import annotations

from typing import Callable, NamedTuple, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QHBoxLayout,
    QHeaderView,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QWidget,
)

from frontend.src.shared.widgets.table_filter import wrap_with_filter

# One definition, previously one per dialog. The "Set" asymmetry between
# the last two headers predates this extraction -- see module docstring.
ELEMENT_COLUMNS = ["Name", "Direction", "Receiver Supply", "Driver Supply Set"]
NAME_COL = 0
DIRECTION_COL = 1

DIRECTION_FILTER_VALUES = ("in", "out", "both")
DIRECTION_FILTER_LABELS = {"in": "Inputs", "out": "Outputs", "both": "Both"}


class ExclusiveCheckboxGroup:
    """Backs a "exactly one of N mutually-exclusive CHECKBOXES" field
    (explicit request: "three mutually-exclusive checkboxes", not radio
    buttons -- QCheckBox visually, but enforced one-at-a-time the same
    way a QButtonGroup already enforces it for radio buttons, since
    QButtonGroup's own exclusivity doesn't care what widget TYPE its
    buttons are). Starts with NONE checked (a required field with no
    default silently pre-selected) -- `selected` is None until the user
    actually checks one, and accept()-time validation rejects submitting
    with nothing chosen.

    Moved here verbatim from isolation_dialog.py (audit fix, category
    07) where level_shifter_dialog.py held a byte-identical copy. The
    "may be None" behaviour is load-bearing and deliberately preserved:
    Isolation's own Clamp Value and Isolation Sense fields use this same
    class and rely on None meaning "the user hasn't chosen yet", so a
    tidier always-one-selected variant would silently make those two
    required fields impossible to leave blank -- and therefore
    impossible to reject."""

    def __init__(self, parent: QWidget, values: tuple[str, ...], labels: dict[str, str], on_change: Optional[Callable[[], None]] = None):
        self.checkboxes: dict[str, QCheckBox] = {value: QCheckBox(labels[value]) for value in values}
        self._on_change = on_change
        for value, checkbox in self.checkboxes.items():
            checkbox.toggled.connect(lambda checked, v=value: self._on_toggled(v, checked))

    def _on_toggled(self, value: str, checked: bool) -> None:
        if not checked:
            if self._on_change is not None:
                self._on_change()
            return
        for other_value, checkbox in self.checkboxes.items():
            if other_value != value:
                checkbox.blockSignals(True)
                checkbox.setChecked(False)
                checkbox.blockSignals(False)
        if self._on_change is not None:
            self._on_change()

    @property
    def selected(self) -> Optional[str]:
        return next((value for value, checkbox in self.checkboxes.items() if checkbox.isChecked()), None)

    @selected.setter
    def selected(self, value: Optional[str]) -> None:
        for v, checkbox in self.checkboxes.items():
            checkbox.setChecked(v == value)

    def row(self) -> QHBoxLayout:
        layout = QHBoxLayout()
        for checkbox in self.checkboxes.values():
            layout.addWidget(checkbox)
        layout.addStretch(1)
        return layout


class ElementsPicker(NamedTuple):
    """Everything a dialog needs to wire into its own layout. Returned as
    a tuple of the same widgets the dialogs already held individually,
    rather than a composite QWidget, so each dialog keeps full control of
    where each piece sits in its own form."""

    table: QTableWidget
    container: QWidget
    filter_bar: object
    direction_filter_group: ExclusiveCheckboxGroup
    add_selected_button: QPushButton
    clear_selected_button: QPushButton
    add_all_button: QPushButton
    clear_all_button: QPushButton


def build_elements_picker(parent: QWidget, on_direction_change: Callable[[], None], on_selection_change: Callable[[], None]) -> ElementsPicker:
    """The table, its filters and its four buttons, configured
    identically for every caller. `on_direction_change` is invoked for
    any event that can change which rows should be visible (direction
    toggle, text filter, column choice, re-sort) -- callers pass their
    own `_apply_direction_filter`, which `apply_direction_filter()`
    below implements."""
    table = QTableWidget(0, len(ELEMENT_COLUMNS))
    table.setHorizontalHeaderLabels(ELEMENT_COLUMNS)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
    table.setSortingEnabled(True)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    table.itemSelectionChanged.connect(on_selection_change)
    table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    # These rows are always PORT names, in both dialogs that use this
    # picker, so one example serves both.
    container, filter_bar = wrap_with_filter(table, example="data_out*")

    # Built with NO on_change, defaulted, and only then wired up. Setting
    # `selected` fires on_change, and the caller's own handler reads
    # `self.elements_table` -- which it hasn't assigned yet at this point,
    # since it assigns from what this function RETURNS. The old per-dialog
    # code happened to be safe only because it built the table first and
    # the group second; centralising the construction made that ordering
    # dependency real, so the default is applied before anything can
    # observe it.
    direction_filter_group = ExclusiveCheckboxGroup(parent, DIRECTION_FILTER_VALUES, DIRECTION_FILTER_LABELS)
    direction_filter_group.selected = "both"
    direction_filter_group._on_change = on_direction_change

    # Reaching into TableFilterBar's own private text/column widgets is
    # the simplest way to hook "after every text filter change" without
    # TableFilterBar needing new public API for this one caller --
    # unchanged from what both dialogs already did individually.
    filter_bar._line_edit.textChanged.connect(on_direction_change)
    filter_bar._column_combo.currentIndexChanged.connect(on_direction_change)
    table.horizontalHeader().sortIndicatorChanged.connect(on_direction_change)

    add_selected_button = QPushButton("Add Selected")
    add_selected_button.clicked.connect(lambda: set_checked_for_rows(table, selected_rows(table), True))
    clear_selected_button = QPushButton("Clear Selected")
    clear_selected_button.clicked.connect(lambda: set_checked_for_rows(table, selected_rows(table), False))
    add_all_button = QPushButton("Add All")
    add_all_button.clicked.connect(lambda: set_checked_for_rows(table, range(table.rowCount()), True))
    clear_all_button = QPushButton("Clear All")
    clear_all_button.clicked.connect(lambda: set_checked_for_rows(table, range(table.rowCount()), False))

    return ElementsPicker(
        table=table,
        container=container,
        filter_bar=filter_bar,
        direction_filter_group=direction_filter_group,
        add_selected_button=add_selected_button,
        clear_selected_button=clear_selected_button,
        add_all_button=add_all_button,
        clear_all_button=clear_all_button,
    )


def selected_rows(table: QTableWidget) -> set[int]:
    model = table.selectionModel()
    return {index.row() for index in model.selectedRows()} if model is not None else set()


def set_checked_for_rows(table: QTableWidget, rows, checked: bool) -> None:
    state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
    for row in rows:
        item = table.item(row, NAME_COL)
        if item is not None:
            item.setCheckState(state)


def apply_direction_filter(table: QTableWidget, filter_bar, direction_filter_group: ExclusiveCheckboxGroup) -> None:
    """Filters table VISIBILITY only (setRowHidden()) by each row's own
    Direction column -- never touches checkState(), so the underlying
    included-elements set accumulates correctly across filter changes (a
    row hidden by the filter keeps whatever checked state it had,
    exactly like TableFilterBar's own text filter already guarantees for
    its own hiding).

    Combines with the wildcard text filter by running
    `filter_bar.reapply()` FIRST (recomputing hidden state from the text
    filter alone, discarding whatever this function left behind on a
    previous call) and only ADDING further hiding on top for a row that
    fails the direction filter -- so the two intersect (a row shows only
    if it passes BOTH) rather than one clobbering the other's decision.

    INOUT, which the Inputs/Outputs framing doesn't address, matches
    EITHER filter -- the same "counts as both" assumption the
    direction-composition logic elsewhere already makes, kept consistent
    rather than answered differently here."""
    filter_bar.reapply()
    selected = direction_filter_group.selected or "both"
    if selected == "both":
        return
    wanted = {"IN", "INOUT"} if selected == "in" else {"OUT", "INOUT"}
    for row in range(table.rowCount()):
        if table.isRowHidden(row):
            continue  # already hidden by the text filter -- nothing to narrow further
        direction_item = table.item(row, DIRECTION_COL)
        direction = direction_item.text() if direction_item is not None else ""
        if direction not in wanted:
            table.setRowHidden(row, True)
