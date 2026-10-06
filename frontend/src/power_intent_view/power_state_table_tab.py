""""Power State Table" -- a new top-level tab (same level as Load
Design/Load Power Intent/Power Intent), enabled by the EXACT SAME
condition that enables "Power Intent" itself (see main_window.py's own
`_on_power_intent_loaded()`), since this tab's own dynamic columns are
entirely driven by Power Domains/Supply Nets that only exist once Power
Intent has been loaded.

Reads the SAME shared, cross-tab diagram-only state objects the "Power
Intent" tab's own diagram already owns (SupplyPortDiagramState/
PowerDomainDiagramState/PowerSwitchDiagramState) via a direct reference
to that tab's `PowerIntentDiagramView` -- this tab never mutates them,
only reads and reacts to `diagramStateChanged`. See power_state_table.py's
own module docstring for the row/cascade data model this tab is a thin
UI layer over.

Diagnostics reuse `DiagnosticsTableModel` (the exact same replace-not-
accumulate-per-source model `diagnostics/diagnostics_dock.py`'s own
`DiagnosticsDock` is built on) -- but embedded directly inline in this
tab's own central layout (table, then Diagnostic section, then buttons,
top to bottom, per the explicit spec) rather than as a separate
QDockWidget the way Load Design/Load Power Intent's diagnostics are:
those two auto-run their own diagnostics-producing action and need a
dock that can auto-show/hide/float independently; this one only ever
updates on an explicit "Check Power Intent" click, so a fixed inline
section already matches "visible exactly when the user asked for it"
with no dock machinery needed.
"""

from __future__ import annotations

import uuid
from typing import Optional

from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QTableView,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from frontend.src.diagnostics.diagnostics_model import DiagnosticsTableModel
from frontend.src.power_intent_view.power_intent_diagram_view import PowerIntentDiagramView
from frontend.src.power_intent_view.power_state_table import (
    DIAGNOSTICS_SOURCE,
    PowerStateTableDiagramState,
    add_row,
    net_effective_state_keys,
    net_state_display,
    net_state_snapshot,
    ordered_supply_nets,
    reconcile_net_state_renames_and_removals,
    remove_row,
    remove_rows_for_domain,
    validate_power_state_table,
)

_NO_SELECTION = ""  # the blank/unselected placeholder entry, always first in every combo


def _make_combo(options: list[str], current: Optional[str]) -> QComboBox:
    """A plain-text combo (Power Domain, and any other column whose
    display text IS its value)."""
    combo = QComboBox()
    combo.addItem(_NO_SELECTION)
    combo.addItems(options)
    combo.setCurrentText(current if current is not None else _NO_SELECTION)
    return combo


def _make_net_combo(keys: list[str], current: Optional[str]) -> QComboBox:
    """A net column's combo: SHOWS the display form, CARRIES the key.

    The two differ, and must (explicit request, section 4): a levelled
    state displays as the bare number, so FULL_ON 1.2 and PARTIAL_ON 1.2
    both read "1.2" while being different states. Display text therefore
    cannot identify a selection -- the canonical key does, carried as
    item data and read back with currentData()."""
    combo = QComboBox()
    combo.addItem(_NO_SELECTION, None)
    for key in keys:
        combo.addItem(net_state_display(key), key)
    index = combo.findData(current) if current is not None else 0
    combo.setCurrentIndex(index if index >= 0 else 0)
    return combo


class PowerStateTableTab(QMainWindow):
    def __init__(self, power_intent_diagram: PowerIntentDiagramView, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._diagram = power_intent_diagram
        self._state = PowerStateTableDiagramState()
        self._previous_net_snapshot = net_state_snapshot(self._diagram.shared_supply_state, self._diagram.shared_power_switch_state)
        self._previous_domain_ids = set(self._diagram.shared_domain_state.domains.keys())

        self.table = QTableWidget(0, 0)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        bold = QFont()
        bold.setBold(True)
        self.table.horizontalHeader().setFont(bold)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)

        self.diagnostics_model = DiagnosticsTableModel(self)
        self.diagnostics_model.set_visible_sources({DIAGNOSTICS_SOURCE})
        self.diagnostics_table = QTableView()
        self.diagnostics_table.setModel(self.diagnostics_model)
        self.diagnostics_table.horizontalHeader().setStretchLastSection(False)
        self.diagnostics_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        diagnostics_box = QGroupBox("Diagnostic")
        diagnostics_layout = QVBoxLayout(diagnostics_box)
        diagnostics_layout.addWidget(self.diagnostics_table)

        self.add_state_button = QPushButton("Add State")
        self.add_state_button.clicked.connect(self._on_add_state)
        # Cosmetic-only styling hook (green-accent theme, see
        # app/theme.py) -- these two are primary actions but not dialog
        # `:default` buttons, so QSS needs an explicit property to target
        # them. No behaviour is attached to it.
        self.add_state_button.setProperty("accent", True)
        add_state_row = QHBoxLayout()
        add_state_row.addStretch(1)
        add_state_row.addWidget(self.add_state_button)

        self.check_power_intent_button = QPushButton("Check Power Intent")
        self.check_power_intent_button.clicked.connect(self._on_check_power_intent)
        self.check_power_intent_button.setProperty("accent", True)
        check_power_intent_row = QHBoxLayout()
        check_power_intent_row.addStretch(1)
        check_power_intent_row.addWidget(self.check_power_intent_button)

        central = QWidget()
        central_layout = QVBoxLayout(central)
        central_layout.addWidget(self.table, stretch=1)
        # Explicit request: "Add State" moved below the table, ABOVE the
        # Diagnostic section (was previously alongside "Check Power
        # Intent"); "Check Power Intent" itself stays exactly where it
        # was -- below the Diagnostic section.
        central_layout.addLayout(add_state_row)
        central_layout.addWidget(diagnostics_box)
        central_layout.addLayout(check_power_intent_row)
        self.setCentralWidget(central)

        self._diagram.diagramStateChanged.connect(self._on_diagram_state_changed)
        self._rebuild_table()

    # -- save / load project (explicit request) --------------------------

    @property
    def state(self) -> PowerStateTableDiagramState:
        """The live Power State Table state -- read by 'Save Project'."""
        return self._state

    def load_state(self, state: PowerStateTableDiagramState) -> None:
        """'Load Project': replace the whole table with the restored
        rows and re-render. The net/domain snapshots are re-baselined to
        the just-loaded diagram state so the live-update cascade
        (_on_diagram_state_changed()) doesn't spuriously fire on the
        first real edit after a load."""
        self._state.rows = dict(state.rows)
        self._previous_net_snapshot = net_state_snapshot(self._diagram.shared_supply_state, self._diagram.shared_power_switch_state)
        self._previous_domain_ids = set(self._diagram.shared_domain_state.domains.keys())
        self._rebuild_table()

    # -- row management --------------------------------------------------

    def _on_add_state(self) -> None:
        add_row(self._state, uuid.uuid4().hex)
        self._rebuild_table()

    def _on_remove_row(self, row_id: str) -> None:
        remove_row(self._state, row_id)
        self._rebuild_table()

    # -- live-update cascade (see power_state_table.py's own docstring) --

    def _on_diagram_state_changed(self) -> None:
        supply_state = self._diagram.shared_supply_state
        domain_state = self._diagram.shared_domain_state
        switch_state = self._diagram.shared_power_switch_state

        current_net_snapshot = net_state_snapshot(supply_state, switch_state)
        reconcile_net_state_renames_and_removals(self._state, self._previous_net_snapshot, current_net_snapshot)
        self._previous_net_snapshot = current_net_snapshot

        current_domain_ids = set(domain_state.domains.keys())
        for domain_id in self._previous_domain_ids - current_domain_ids:
            remove_rows_for_domain(self._state, domain_id)
        self._previous_domain_ids = current_domain_ids

        self._rebuild_table()

    # -- "Check Power Intent" ---------------------------------------------

    def _on_check_power_intent(self) -> None:
        diagnostics = validate_power_state_table(self._state, self._diagram.shared_domain_state, self._diagram.shared_supply_state)
        self.diagnostics_model.set_diagnostics(DIAGNOSTICS_SOURCE, diagnostics)

    # -- rebuild ------------------------------------------------------------

    def _rebuild_table(self) -> None:
        supply_state = self._diagram.shared_supply_state
        domain_state = self._diagram.shared_domain_state
        switch_state = self._diagram.shared_power_switch_state
        nets = ordered_supply_nets(supply_state, domain_state)
        domains = sorted(domain_state.domains.values(), key=lambda d: d.name)

        headers = ["Power Domain", "State Name"] + [n.name for n in nets] + [""]
        self.table.setSortingEnabled(False)
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)

        rows = list(self._state.rows.values())
        self.table.setRowCount(len(rows))

        uniform_net_width = _uniform_net_column_width(self.table, nets, supply_state, switch_state)

        for r, row in enumerate(rows):
            domain_combo = _make_combo([d.name for d in domains], next((d.name for d in domains if d.id == row.power_domain_id), None))
            domain_ids_by_name = {d.name: d.id for d in domains}

            def _on_domain_changed(text: str, row=row, domain_ids_by_name=domain_ids_by_name) -> None:
                row.power_domain_id = domain_ids_by_name.get(text)

            domain_combo.currentTextChanged.connect(_on_domain_changed)
            self.table.setCellWidget(r, 0, domain_combo)

            name_edit = QLineEdit(row.state_name)
            name_edit.setPlaceholderText("e.g. ON, RET, OFF")

            def _on_name_changed(text: str, row=row) -> None:
                row.state_name = text

            name_edit.textChanged.connect(_on_name_changed)
            self.table.setCellWidget(r, 1, name_edit)

            for c, net in enumerate(nets):
                keys = net_effective_state_keys(net, supply_state, switch_state)
                net_combo = _make_net_combo(keys, row.net_states.get(net.id))
                net_combo.setMinimumWidth(uniform_net_width)
                net_combo.setMaximumWidth(uniform_net_width)

                def _on_net_changed(_index: int, row=row, net_id=net.id, combo=net_combo) -> None:
                    # currentData(), not the display text -- see
                    # _make_net_combo()'s own docstring.
                    row.net_states[net_id] = combo.currentData()

                net_combo.currentIndexChanged.connect(_on_net_changed)
                self.table.setCellWidget(r, 2 + c, net_combo)

            delete_button = QPushButton("-")
            delete_button.setFixedWidth(24)
            delete_button.clicked.connect(lambda checked=False, row_id=row.id: self._on_remove_row(row_id))
            self.table.setCellWidget(r, len(headers) - 1, delete_button)

        self.table.setSortingEnabled(True)


def _uniform_net_column_width(table: QTableWidget, nets, supply_state, switch_state) -> int:
    """One shared width (explicit request) sized to fit the LONGEST
    single entry across the ENTIRE set of every net's own name AND every
    per-net Power State DISPLAY across ALL nets -- computed globally, not
    per-column, so every dynamic net-column cell is the exact same
    width. Measures what is actually SHOWN (a bare number for a levelled
    state), not the longer canonical key behind it."""
    longest = ""
    for net in nets:
        if len(net.name) > len(longest):
            longest = net.name
        for key in net_effective_state_keys(net, supply_state, switch_state):
            shown = net_state_display(key)
            if len(shown) > len(longest):
                longest = shown
    metrics = table.fontMetrics()
    return max(metrics.horizontalAdvance(longest) + 40, 80)
