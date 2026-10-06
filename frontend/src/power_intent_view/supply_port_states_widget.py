"""SupplyPortStatesWidget -- the free-form, user-managed list of DVFS-
style power states on a Supply Port (State Name/Type/Voltage). Shared
identically by both AddPowerSupplyDialog ("Add/Edit Power Supply",
standalone) and Create Power Domain's own inline Supply Port creation
flow -- the latter reuses AddPowerSupplyDialog wholesale already (see
its own module docstring), so building this widget once here and
embedding it in that one dialog class satisfies both entry points
automatically, with no second implementation.

No existing "user freely adds/removes rows they type into" widget
precedent existed anywhere in this codebase before this (grepped for
"Add Row"/"Remove Row"/"insertRow"/"removeRow(" -- empty) -- every other
list-management UI here (Isolation/Level Shifter's own Elements table,
Retention's own list) is a checkbox-toggle over a PRE-POPULATED candidate
pool (existing ports/instances), not free-form authoring. Built as a
plain QTableWidget with per-row Type combo-box/Voltage line-edit cell
widgets, plus a column of small per-row "-" (remove this row) buttons
and a single "+" (add a row) button positioned OUTSIDE the table itself,
to its right (explicit follow-up request, replacing an earlier "Add
State"/"Remove Selected" button-pair design).

State *names* are never reserved/special-cased (explicit constraint) --
"ON"/"OFF" are ordinary text with no special meaning; the Voltage-vs-Off
distinction comes entirely from the Type column.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QDoubleValidator
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QSizePolicy,
    QSpacerItem,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from frontend.src.power_intent_view.name_validation import NO_SPACES_ERROR, validate_no_spaces
from frontend.src.power_intent_view.supply_port_overlay import (
    FULL_ON,
    LEVELLED_STATE_TYPES,
    SUPPLY_STATE_TYPES,
    SupplyPortState,
    state_display,
    duplicate_state_names,
)

# THREE columns: "Name" is back, leading. It was removed when the Type
# vocabulary arrived, on the reasoning that naming belonged only at the
# Supply Set and Power State Table levels; that is reversed. The name is
# what legacy `add_port_state`/`add_pst_state` declare and reference, and
# what the Power State Table's own cells display -- while modern Tier 1
# still derives STATE_1/STATE_2/... and never reads it. See
# SupplyPortState's own docstring for that boundary.
COL_NAME, COL_TYPE, COL_VOLTAGE = 0, 1, 2
# Explicit request: the unit must always be visible on the header
# itself, not just implied -- applies everywhere this column appears
# (this widget, and any Properties table/Info view showing it).
VOLTAGE_HEADER = "Voltage [V]"
# Explicit request: a Type=Off state's Voltage cell shows "0.0" as a
# display-only convenience rather than being left blank -- the
# underlying stored value stays None (see `.states` below), preserving
# the "off" (disconnected) vs. "an active supply at 0.0V" distinction
# for anything reading the real data (e.g. UPF generation).
OFF_STATE_DISPLAY_VOLTAGE = "0.0"
# Explicit bug report: the table/dialog was too narrow for its own 3
# columns (State Name/Type/Voltage) to display without clipping.
_MIN_TABLE_WIDTH = 380
_ROW_HEIGHT = 26
_ROW_BUTTON_SIZE = 22


class SupplyPortStatesWidget(QWidget):
    """A 3-column (State Name/Type/Voltage), all center-aligned,
    editable table -- one row per SupplyPortState -- with a column of
    per-row "-" buttons and a single "+" button OUTSIDE the table, to
    its right (explicit request). `.states` is the get/set property
    every caller reads at accept() time / writes at pre-fill time;
    `.validate()` returns an error message string (or None) for the
    owning dialog's own accept().

    Bug fix (explicit request, "the actual underlying problem the
    section split was trying to work around"): the table's own height
    is kept an exact FIXED match to its current row count
    (_sync_table_height(), no internal scrollbar at all -- "fully
    displays all content... without requiring the user to manually
    resize") rather than relying on QTableWidget's own default
    viewport-based size hint, which does NOT grow to fit added rows on
    its own. `rowsChanged` fires alongside every height sync so the
    owning dialog can call adjustSize() and genuinely re-fit the whole
    window, not just this one widget, whenever a row is added/removed
    or the whole list is replaced."""

    rowsChanged = Signal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.table = QTableWidget(0, 3, self)
        self.table.setHorizontalHeaderLabels(["Name", "Type", VOLTAGE_HEADER])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(_ROW_HEIGHT)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.table.setMinimumWidth(_MIN_TABLE_WIDTH)
        self.setMinimumWidth(_MIN_TABLE_WIDTH + _ROW_BUTTON_SIZE + 8)

        # The per-row "-" buttons plus the single "+" button, OUTSIDE
        # the table itself (explicit request) -- a separate column to
        # its right, rebuilt wholesale on every add/remove (_refresh_
        # row_buttons()) so each "-" button's own row index always
        # stays correct, the same "rebuild rather than incrementally
        # patch" precedent this app already uses elsewhere for anything
        # keyed by a row's own position.
        self._row_buttons_layout = QVBoxLayout()
        self._row_buttons_layout.setContentsMargins(0, 0, 0, 0)
        self._row_buttons_layout.setSpacing(0)
        # Matches the table's own header row height, so the first "-"
        # button lines up with the first data row, not the header.
        self._row_buttons_layout.addSpacerItem(QSpacerItem(0, self.table.horizontalHeader().sizeHint().height(), QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed))
        self._add_button = QToolButton()
        self._add_button.setText("+")
        self._add_button.setFixedSize(_ROW_BUTTON_SIZE, _ROW_BUTTON_SIZE)
        self._add_button.setToolTip("Add State")
        self._add_button.clicked.connect(self._add_row)
        self._row_buttons_layout.addWidget(self._add_button)

        table_row = QHBoxLayout()
        table_row.setContentsMargins(0, 0, 0, 0)
        table_row.addWidget(self.table)
        # Bug fix: an earlier version had an addStretch(1) here, between
        # the last "-" button and the "+" button -- an EXPANDING spacer
        # with no counterbalancing constraint elsewhere, which happily
        # absorbed any EXTRA height this row's own HBoxLayout was given
        # (e.g. by AddPowerSupplyDialog's own window resize), silently
        # growing this widget's own ALLOCATED layout space well beyond
        # what the table itself actually needed -- confirmed empirically
        # (states_widget's own allocated height ended up ~339px for a
        # table that only needed 177px). AlignTop instead: this column's
        # own natural height (spacer + N "-" buttons + the "+" button)
        # is now the ONLY thing that determines its size, matching the
        # table's own fixed height exactly, with no elastic absorption.
        table_row.addLayout(self._row_buttons_layout)
        table_row.setAlignment(self._row_buttons_layout, Qt.AlignmentFlag.AlignTop)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(table_row)
        layout.setAlignment(table_row, Qt.AlignmentFlag.AlignTop)

    def _add_row(self) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setRowHeight(row, _ROW_HEIGHT)
        self._install_name_edit(row, "")
        self._install_type_combo(row, FULL_ON)
        self._install_voltage_edit(row, "")
        self._refresh_row_buttons()

    def _remove_row(self, row: int) -> None:
        self.table.removeRow(row)
        self._refresh_row_buttons()

    def _refresh_row_buttons(self) -> None:
        """Rebuilds the "-" button column from scratch to match the
        table's own CURRENT row count -- see class docstring for why a
        full rebuild, not an incremental patch, is the correct approach
        here (a row's own index can shift out from under a stale
        closure otherwise)."""
        # Layout index 0 is the header-height spacer (kept), the last
        # is the "+" button (kept) -- only the per-row "-" buttons in
        # between are torn down and rebuilt.
        while self._row_buttons_layout.count() > 2:
            item = self._row_buttons_layout.takeAt(1)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        for row in range(self.table.rowCount()):
            button = QToolButton()
            button.setText("−")  # unicode minus -- a cleaner glyph than a hyphen
            button.setFixedSize(_ROW_BUTTON_SIZE, _ROW_HEIGHT)
            button.setToolTip("Remove This State")
            button.clicked.connect(lambda _checked=False, r=row: self._remove_row(r))
            self._row_buttons_layout.insertWidget(1 + row, button)
        self._sync_table_height()

    def _sync_table_height(self) -> None:
        """The table's own actual widget height always matches its
        CURRENT row content exactly -- see class docstring. Computed
        from the header's own height plus one _ROW_HEIGHT per row plus
        the table's own frame width, not a fixed/guessed constant, so it
        stays correct regardless of style/platform."""
        header_height = self.table.horizontalHeader().sizeHint().height()
        frame = 2 * self.table.frameWidth()
        content_height = header_height + self.table.rowCount() * _ROW_HEIGHT + frame
        self.table.setFixedHeight(content_height)
        self.updateGeometry()
        self.rowsChanged.emit()

    def required_height(self) -> int:
        """The EXACT total height this widget needs to show its current
        row count without any clipping -- returns `self.table.height()`
        directly (confirmed, empirically, accurate immediately after
        every _sync_table_height() call, unlike sizeHint()/layout
        queries at ANY level of this widget or its owning dialog, both
        found to cache a stale value rather than reliably re-deriving
        one -- see AddPowerSupplyDialog's own module docstring for the
        fuller "why"). The owning dialog uses this to compute its own
        target window size directly, sidestepping Qt's layout-
        repropagation machinery entirely rather than depending on it."""
        return self.table.height()

    def _install_name_edit(self, row: int, name: str) -> None:
        edit = QLineEdit(name)
        edit.setPlaceholderText("e.g. ON")
        self.table.setCellWidget(row, COL_NAME, edit)

    def _install_type_combo(self, row: int, state_type: str) -> None:
        combo = QComboBox()
        combo.addItems(list(SUPPLY_STATE_TYPES))
        # Explicit request: center-align every column, Type included --
        # QComboBox has no direct "center the current text" API, so this
        # is the standard Qt trick: make it editable but READ-ONLY (no
        # typing possible, only the dropdown itself changes the value),
        # then center-align that line edit's own text.
        combo.setEditable(True)
        combo.lineEdit().setReadOnly(True)
        combo.lineEdit().setAlignment(Qt.AlignmentFlag.AlignCenter)
        combo.setCurrentText(state_type if state_type in SUPPLY_STATE_TYPES else FULL_ON)
        combo.currentTextChanged.connect(lambda _text, r=row: self._on_type_changed(r))
        self.table.setCellWidget(row, COL_TYPE, combo)

    def _install_voltage_edit(self, row: int, voltage: str) -> None:
        edit = QLineEdit(voltage)
        edit.setPlaceholderText("e.g. 1.2")
        edit.setValidator(QDoubleValidator())
        edit.setAlignment(Qt.AlignmentFlag.AlignCenter)  # explicit request: center-align the voltage value
        self.table.setCellWidget(row, COL_VOLTAGE, edit)

    def _on_type_changed(self, row: int) -> None:
        combo = self.table.cellWidget(row, COL_TYPE)
        edit = self.table.cellWidget(row, COL_VOLTAGE)
        if combo is None or edit is None:
            return
        # FULL_ON/PARTIAL_ON carry a level; OFF/UNDETERMINED do not, and
        # their Voltage cell is DISABLED rather than merely left blank.
        has_level = combo.currentText() in LEVELLED_STATE_TYPES
        edit.setEnabled(has_level)
        if not has_level:
            # Display-only convenience (explicit request): an Off-type
            # state shows "0.0" in its own (still disabled/read-only)
            # Voltage cell rather than leaving it blank. Purely cosmetic
            # -- `.states` below still always reports voltage=None for a
            # non-voltage state regardless of this cell's own text, so
            # the underlying "off" (disconnected) vs. "an active supply
            # at 0.0V" distinction this app already established stays
            # intact for anything (e.g. UPF generation) reading `.states`.
            edit.setText(OFF_STATE_DISPLAY_VOLTAGE)

    @property
    def states(self) -> list[SupplyPortState]:
        result: list[SupplyPortState] = []
        for row in range(self.table.rowCount()):
            name_edit = self.table.cellWidget(row, COL_NAME)
            name = name_edit.text().strip() if name_edit is not None else ""
            combo = self.table.cellWidget(row, COL_TYPE)
            state_type = combo.currentText() if combo is not None else FULL_ON
            edit = self.table.cellWidget(row, COL_VOLTAGE)
            voltage_text = edit.text().strip() if edit is not None else ""
            has_level = state_type in LEVELLED_STATE_TYPES
            result.append(SupplyPortState(
                name=name,
                state_type=state_type,
                voltage=voltage_text if (has_level and voltage_text) else None,
            ))
        return result

    @states.setter
    def states(self, states: list[SupplyPortState]) -> None:
        self.table.setRowCount(0)
        for state in states:
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setRowHeight(row, _ROW_HEIGHT)
            self._install_name_edit(row, state.name)
            self._install_type_combo(row, state.state_type)
            self._install_voltage_edit(row, state.voltage or "")
            self._on_type_changed(row)
        self._refresh_row_buttons()

    def validate(self) -> Optional[str]:
        """Voltage is now MANDATORY whenever the Type carries one, which
        retires the old "two ambiguous no-voltage Active states" conflict
        check outright -- that ambiguity is unreachable once a level is
        required. What remains is a plain duplicate-ROW check: two states
        with the same Type AND Voltage (or two OFF entries, or two
        UNDETERMINED entries) say the same thing twice."""
        states = self.states
        for state in states:
            if not state.name:
                return "Every Power State needs a Name."
            if not validate_no_spaces(state.name):
                return f"Power State name '{state.name}': {NO_SPACES_ERROR}"
            if state.state_type in LEVELLED_STATE_TYPES and not state.voltage:
                return f"Power State '{state.name}' is {state.state_type} and needs a voltage value."
        # NAME is the uniqueness constraint now, and the only one. Two
        # states may legitimately share a Type at different voltages --
        # an ordinary DVFS case, and exactly what the name exists to
        # disambiguate -- so the old duplicate-(Type, Voltage) check is
        # retired along with it. Uniqueness is per SUPPLY: another supply
        # having its own "ON" is not a conflict.
        duplicates = duplicate_state_names(states)
        if duplicates:
            return (
                f"Duplicate Power State name '{duplicates[0]}' -- names must be unique "
                "within this supply's own states."
            )
        return None
