""""Retention Strategy" dialog -- triggered from the Power Intent tab's
Diagram via the Retention tool's own hover+click placement (see
power_intent_diagram_view.py's DiagramMode.RETENTION handling) or the
right-click "Add Retention" context-menu action, both funneling through
the identical (_validate_retention_target() then this dialog) flow,
same "do NOT build a new one" precedent every other placement tool here
already follows.

Diagram-only for this pass, same scoping decision as supply ports/power
domains (see retention_overlay.py's own module docstring) -- this
dialog only ever hands back plain data, never touches shared state
itself; the view's own create_retention_strategy()/edit_retention_strategy()
does that.

Field order matches the spec exactly: Name, Power Domain (read-only,
auto-filled), Primary Supply Set, Retention Supply Set (with its own
inline "Add new Supply Set..." -- reusing CreateSupplySetDialog exactly
as-is, same "do NOT build a new one" precedent), Save Signal, Restore
Signal, Elements (checkbox list, already-claimed elements shown
disabled/grey/italic -- see retention_overlay.py's own
claimed_elements_by_other_strategies()).

Elements list controls (explicit follow-up request, superseding an
earlier single "All" button): Shift+Click/Ctrl+Click range-selects
list ROWS (ExtendedSelection), a plain list-UI selection state kept
deliberately distinct from each element's own checkbox state -- 4
buttons in 2 rows act on one or the other: Add Selected/Clear Selected
(only the list-selected rows, disabled with nothing selected) and Add
All/Clear All (every element regardless of list-selection, always
enabled) -- see the dialog's own "Elements" section below for the full
reasoning.
"""

from __future__ import annotations

from typing import Optional
from uuid import uuid4

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from frontend.src.power_intent_view.create_power_domain_dialog import CreateSupplySetDialog
from frontend.src.power_intent_view.name_validation import NO_SPACES_ERROR, validate_no_spaces
from frontend.src.power_intent_view.power_domain_overlay import PowerDomainPlacement, SupplySetPlacement
from frontend.src.power_intent_view.retention_overlay import RetentionStrategyPlacement
from frontend.src.power_intent_view.supply_port_overlay import SupplyPortState
from frontend.src.shared.widgets import message_box
from frontend.src.shared.widgets.form_separator import form_separator
from frontend.src.shared.widgets.table_filter import ListFilterBar
from frontend.src.shared.widgets.target_hierarchy_label import APPLIES_TO_CAPTION, make_target_hierarchy_label

_ADD_NEW_SUPPLY_SET = "Add new Supply Set..."
_CLAIMED_ELEMENT_COLOR = QColor("#9e9e9e")  # grey -- explicit request


class RetentionStrategyDialog(QDialog):
    """The same dialog class serves both Add and Edit (same precedent as
    CreatePowerDomainDialog): passing `initial` (an existing
    RetentionStrategyPlacement) and `editing_strategy_id` pre-populates
    every field and excludes that strategy's own current name from the
    global uniqueness check at accept() time.

    `existing_names` (strategy id -> name, every Retention Strategy in
    the ENTIRE design -- explicit request: uniqueness is global, not
    scoped to any one hierarchy or Power Domain, unlike Supply Port's
    own per-owner-path check) backs the accept()-time duplicate-name
    QMessageBox.

    `available_elements`/`claimed_elements` are pre-computed by the
    caller (collect_retention_elements()/claimed_elements_by_other_strategies()
    in retention_overlay.py) -- this dialog only ever renders them, it
    doesn't do the domain-aware traversal itself."""

    def __init__(
        self,
        domain: PowerDomainPlacement,
        supply_sets: dict[str, SupplySetPlacement],
        available_supply_names: list[str],
        available_elements: list[str],
        claimed_elements: set[str],
        existing_names: dict[str, str],
        editing_strategy_id: Optional[str] = None,
        initial: Optional[RetentionStrategyPlacement] = None,
        parent: Optional[QWidget] = None,
        target_paths: Optional[list[str]] = None,
    ):
        super().__init__(parent)
        self.setSizeGripEnabled(True)  # explicit request (Bug 5): every dialog here must be user-resizable
        self.setWindowTitle("Edit Retention Strategy" if editing_strategy_id is not None else "Add Retention")
        self._domain = domain
        self._target_paths = target_paths
        self._supply_sets = dict(supply_sets)
        self.new_supply_sets: dict[str, SupplySetPlacement] = {}
        self.new_supply_ports: dict[str, QColor] = {}
        # Bug fix (explicit report, same root cause/fix as
        # CreatePowerDomainDialog's own new_supply_port_states): tracked
        # alongside new_supply_ports, same "accumulate live via
        # CreateSupplySetDialog.new_port_states, prune to referenced
        # names at accept()" lifecycle.
        self.new_supply_port_states: dict[str, list[SupplyPortState]] = {}
        self._available_supply_names = list(available_supply_names)
        self._existing_names = dict(existing_names)
        self._editing_strategy_id = editing_strategy_id
        self._claimed_elements = set(claimed_elements)

        self.name_edit = QLineEdit(initial.name if initial is not None else "")
        self.name_edit.setPlaceholderText("e.g. RET_CPU")
        self.domain_label = QLabel(domain.name)  # read-only -- auto-filled from the target block's own domain

        self.primary_combo = QComboBox()
        self.retention_combo = QComboBox()
        self.retention_combo.currentIndexChanged.connect(self._on_retention_combo_changed)
        self._refresh_supply_set_combos()
        if initial is not None:
            self._select_combo_data(self.primary_combo, initial.primary_supply_set_id)
            self._select_combo_data(self.retention_combo, initial.retention_supply_set_id)

        self.save_logic_edit = QLineEdit(initial.save_logic if initial is not None else "")
        self.save_logic_edit.setPlaceholderText("e.g. save_en \u2014 RTL signal/expression triggering save")
        self.restore_logic_edit = QLineEdit(initial.restore_logic if initial is not None else "")
        self.restore_logic_edit.setPlaceholderText("e.g. restore_en \u2014 RTL signal/expression triggering restore")

        self.elements_list = QListWidget()
        # Explicit request: this list must actually grow/shrink when the
        # dialog is resized, not stay pinned at its own size hint --
        # QListWidget already defaults to an Expanding size policy, but
        # the real fix is in the layout structure below (a QFormLayout
        # row can't correctly negotiate extra space for a nested QLayout
        # field the way a top-level QVBoxLayout stretch factor can); set
        # explicitly here too so this stays correct even if the layout
        # is ever restructured again.
        self.elements_list.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        # Explicit request: a wildcard filter directly above the list,
        # reusing the exact same matching logic the Properties tables'
        # own TableFilterBar already uses (see table_filter.py's own
        # wildcard_match()) -- ListFilterBar only hides non-matching
        # items (QListWidgetItem.setHidden()), never touches checkState,
        # so a filtered-out element's own enabled state is unaffected.
        self.elements_filter = ListFilterBar(self.elements_list, example="*/state_reg*")
        # Explicit request: Shift+Click range-selection within the list
        # itself (the ROWS, a UI selection state distinct from each
        # element's own ENABLED/DISABLED checkbox state -- see the
        # button handlers below, which are careful never to conflate
        # the two) -- QListWidget defaults to SingleSelection, which
        # doesn't support Shift+Click range-select at all.
        self.elements_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.elements_list.itemSelectionChanged.connect(self._update_selected_buttons_enabled)
        # Explicit request: "All" replaced by 4 buttons in 2 rows --
        # Add Selected/Clear Selected (act on whatever's currently
        # list-selected, disabled when nothing is) and Add All/Clear
        # All (renamed from "All"; act on every element regardless of
        # list-selection, always enabled).
        self.add_selected_button = QPushButton("Add Selected")
        self.add_selected_button.clicked.connect(self._add_selected_elements)
        self.clear_selected_button = QPushButton("Clear Selected")
        self.clear_selected_button.clicked.connect(self._clear_selected_elements)
        self.add_all_button = QPushButton("Add All")
        self.add_all_button.clicked.connect(self._add_all_elements)
        self.clear_all_button = QPushButton("Clear All")
        self.clear_all_button.clicked.connect(self._clear_all_elements)
        self._update_selected_buttons_enabled()
        initial_elements = set(initial.elements) if initial is not None else set()
        for element in available_elements:
            item = QListWidgetItem(element)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            if element in self._claimed_elements:
                # Explicit constraint: an element already claimed (enabled)
                # by another Retention Strategy in the same Power Domain
                # can't also be enabled here -- shown, not hidden, so the
                # user can see why an element is missing from their
                # options, but disabled/greyed/italic and forced unchecked.
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
                item.setForeground(QBrush(_CLAIMED_ELEMENT_COLOR))
                font = item.font()
                font.setItalic(True)
                item.setFont(font)
                item.setCheckState(Qt.CheckState.Unchecked)
            else:
                item.setCheckState(Qt.CheckState.Checked if element in initial_elements else Qt.CheckState.Unchecked)
            self.elements_list.addItem(item)

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        self._ok_button = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        self._ok_button.setEnabled(bool(self.name_edit.text().strip()))
        self.name_edit.textChanged.connect(lambda text: self._ok_button.setEnabled(bool(text.strip())))

        # Explicit request: the Elements list must expand to fill
        # available space when the dialog is resized, while the fixed
        # fields above (Name/Power Domain/Supply Sets/Save/Restore
        # Logic) keep their own natural size. A single QFormLayout for
        # the whole dialog can't do this -- QFormLayout sizes each row
        # from its own field widget's size hint, and a *nested layout*
        # (elements_column, not a plain widget) added as a field has no
        # size POLICY of its own for QFormLayout to negotiate extra
        # space against, so the row never grows past its hint. Splitting
        # into a top-level QVBoxLayout with an explicit stretch factor
        # on the Elements section is the actual fix -- top-level box
        # layouts DO honor stretch factors for nested layouts, unlike
        # QFormLayout rows.
        fields_form = QFormLayout()
        if self._target_paths is not None:
            fields_form.addRow(APPLIES_TO_CAPTION, make_target_hierarchy_label(self._target_paths))
        fields_form.addRow(form_separator())
        fields_form.addRow("Name:", self.name_edit)
        fields_form.addRow("Power Domain:", self.domain_label)
        fields_form.addRow("Primary Supply Set:", self.primary_combo)
        fields_form.addRow("Retention Supply Set:", self.retention_combo)
        # Labelled "Signal", not "Logic" (explicit rename): these carry
        # the RTL signals that drive save/restore, which is what UPF
        # calls them too (`-save_signal`/`-restore_signal`). The field
        # names stay `save_logic`/`restore_logic` -- renaming those
        # would reach into the placement dataclass, project files and
        # generation, and this is a label change.
        fields_form.addRow("Save Signal:", self.save_logic_edit)
        fields_form.addRow("Restore Signal:", self.restore_logic_edit)
        fields_form.addRow(form_separator())

        # Explicit request: 2 rows of buttons at the bottom of the
        # Elements section -- Add Selected/Clear Selected first row,
        # Add All/Clear All second row.
        selected_row = QHBoxLayout()
        selected_row.addWidget(self.add_selected_button)
        selected_row.addWidget(self.clear_selected_button)

        all_row = QHBoxLayout()
        all_row.addWidget(self.add_all_button)
        all_row.addWidget(self.clear_all_button)

        elements_column = QVBoxLayout()
        elements_column.addWidget(QLabel("Elements:"))
        elements_column.addWidget(self.elements_filter)
        elements_column.addWidget(self.elements_list)
        elements_column.addLayout(selected_row)
        elements_column.addLayout(all_row)

        outer = QVBoxLayout(self)
        outer.addLayout(fields_form)
        outer.addLayout(elements_column, 1)  # stretch=1 -- the only section that grows
        outer.addWidget(self.buttons)

    # --- Supply Set pickers -----------------------------------------------

    def _all_supply_sets(self) -> dict[str, SupplySetPlacement]:
        combined = dict(self._supply_sets)
        combined.update(self.new_supply_sets)
        return combined

    def _domain_supply_set_ids(self) -> set[str]:
        """The Power Domain's own Supply Sets -- primary plus every
        secondary -- PLUS any created fresh via this dialog's own
        "Add new Supply Set..." (explicit request: reuse the existing
        Supply-Set-per-Domain data source)."""
        ids = {self._domain.primary_supply_set, *self._domain.secondary_supply_sets}
        ids.discard(None)
        ids |= set(self.new_supply_sets)
        return ids

    def _refresh_supply_set_combos(self, select_combo: Optional[QComboBox] = None, select_id: Optional[str] = None) -> None:
        all_sets = self._all_supply_sets()
        # Only ids this dialog can actually RESOLVE. An unresolvable one
        # used to raise KeyError from inside the sort key -- during
        # __init__, so the dialog never finished constructing and the
        # user saw the action do nothing at all. Degrading to "that
        # entry is missing from the picker" is a visible, recoverable
        # failure; a dead menu item is not.
        pool = {sid for sid in self._domain_supply_set_ids() if sid in all_sets}
        for combo, add_new in ((self.primary_combo, False), (self.retention_combo, True)):
            previous_data = combo.currentData()
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("", None)
            for set_id in sorted(pool, key=lambda sid: all_sets[sid].name):
                combo.addItem(all_sets[set_id].name, set_id)
            if add_new:
                combo.addItem(_ADD_NEW_SUPPLY_SET, _ADD_NEW_SUPPLY_SET)
            if select_id is not None and combo is select_combo:
                index = combo.findData(select_id)
            elif previous_data not in (None, _ADD_NEW_SUPPLY_SET):
                index = combo.findData(previous_data)
            else:
                index = -1
            combo.setCurrentIndex(index if index >= 0 else 0)
            combo.blockSignals(False)

    def _select_combo_data(self, combo: QComboBox, data) -> None:
        index = combo.findData(data)
        if index >= 0:
            combo.setCurrentIndex(index)

    def _on_retention_combo_changed(self, index: int) -> None:
        if self.retention_combo.itemData(index) != _ADD_NEW_SUPPLY_SET:
            return
        # Bug fix: the supply materialized from this inline "Create Supply
        # Set..." flow becomes an Internal Supply the moment it's placed
        # anywhere but the design's true top-level block (see
        # power_intent_diagram_view.py's own _add_missing_supply_ports(),
        # which uses `domain.owner_path` for Retention specifically) --
        # the nested AddPowerSupplyDialog must not offer an editable Power
        # States section in that case.
        dialog = CreateSupplySetDialog(self._available_supply_names, self, is_internal_supply="/" in self._domain.owner_path)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            supply_set = SupplySetPlacement(
                id=uuid4().hex,
                name=dialog.name,
                power=dialog.power,
                ground=dialog.ground,
                n_well=dialog.n_well,
                p_well=dialog.p_well,
                owner_path=self._domain.owner_path,
            )
            self.new_supply_sets[supply_set.id] = supply_set
            self.new_supply_ports.update(dialog.new_port_colors)
            self.new_supply_port_states.update(dialog.new_port_states)
            self._available_supply_names = sorted(set(self._available_supply_names) | set(dialog.new_port_colors))
            self._refresh_supply_set_combos(select_combo=self.retention_combo, select_id=supply_set.id)
        else:
            self._refresh_supply_set_combos()

    # --- Elements -----------------------------------------------------------
    # Two genuinely separate states on each item, never conflated (explicit
    # request): `isSelected()` (Shift+Click row highlighting, a plain list-UI
    # selection) vs. `checkState()` (the element's own enabled/disabled
    # checkbox, what actually ends up in `.elements`). "Selected" in every
    # method/button name below always means the former.

    def _update_selected_buttons_enabled(self) -> None:
        """Add Selected/Clear Selected are only meaningful with at least
        one row list-selected -- kept in sync live via
        elements_list.itemSelectionChanged (connected in __init__),
        firing on every Shift+Click/Ctrl+Click/click change."""
        has_selection = bool(self.elements_list.selectedItems())
        self.add_selected_button.setEnabled(has_selection)
        self.clear_selected_button.setEnabled(has_selection)

    def _add_selected_elements(self) -> None:
        """Checks only the currently list-selected elements -- skips a
        claimed/disabled one even if it happens to be list-selected
        (same "never touch a disabled item's checkState" rule Add
        All/Clear All already follow)."""
        for item in self.elements_list.selectedItems():
            if item.flags() & Qt.ItemFlag.ItemIsEnabled:
                item.setCheckState(Qt.CheckState.Checked)

    def _clear_selected_elements(self) -> None:
        for item in self.elements_list.selectedItems():
            if item.flags() & Qt.ItemFlag.ItemIsEnabled:
                item.setCheckState(Qt.CheckState.Unchecked)

    def _add_all_elements(self) -> None:
        """Renamed from the earlier single "All" button (explicit
        request) -- acts on EVERY element regardless of list-selection,
        always enabled (same as before)."""
        for i in range(self.elements_list.count()):
            item = self.elements_list.item(i)
            if item.flags() & Qt.ItemFlag.ItemIsEnabled:
                item.setCheckState(Qt.CheckState.Checked)

    def _clear_all_elements(self) -> None:
        """New (explicit request) -- the "uncheck everything" mirror
        image of Add All, same "act on every element regardless of
        list-selection, always enabled" convention."""
        for i in range(self.elements_list.count()):
            item = self.elements_list.item(i)
            if item.flags() & Qt.ItemFlag.ItemIsEnabled:
                item.setCheckState(Qt.CheckState.Unchecked)

    # --- accept validation ----------------------------------------------

    def accept(self) -> None:
        name = self.name
        if not validate_no_spaces(name):
            message_box.warning(self, "Invalid Name", NO_SPACES_ERROR)
            return
        for strategy_id, existing_name in self._existing_names.items():
            if strategy_id != self._editing_strategy_id and existing_name == name:
                message_box.warning(self, "Duplicate Retention Strategy Name", f"A Retention Strategy named '{name}' already exists. Choose a different name.")
                return
        if self.primary_supply_set_id is None:
            message_box.warning(self, "Missing Primary Supply Set", "A Primary Supply Set is required.")
            return
        if self.retention_supply_set_id is None:
            message_box.warning(self, "Missing Retention Supply Set", "A Retention Supply Set is required.")
            return
        if not self.save_logic:
            message_box.warning(self, "Missing Save Signal", "A Save Signal is required.")
            return
        if not self.restore_logic:
            message_box.warning(self, "Missing Restore Signal", "A Restore Signal is required.")
            return
        if not self.elements:
            message_box.warning(self, "No Elements Selected", "At least one element must be selected.")
            return
        self._discard_unused_new_entities()
        super().accept()

    def _discard_unused_new_entities(self) -> None:
        """Bug fix (explicit request, same architectural gap found and
        fixed in CreatePowerDomainDialog -- see that class's own
        `_discard_unused_new_entities()` docstring for the full
        reasoning): a Supply Set created via this dialog's own inline
        "Add new Supply Set..." and then superseded before this outer OK
        -- by picking `_ADD_NEW_SUPPLY_SET` again (creating a second one)
        or by selecting a pre-existing Supply Set instead, either of
        which leaves retention_combo no longer pointing at it -- must
        never be committed. Prunes `self.new_supply_sets` down to only
        the id(s) actually referenced by Primary/Retention right now,
        then `self.new_supply_ports` down to only the names actually
        referenced by one of those retained sets."""
        referenced_ids = {sid for sid in (self.primary_supply_set_id, self.retention_supply_set_id) if sid}
        self.new_supply_sets = {sid: s for sid, s in self.new_supply_sets.items() if sid in referenced_ids}
        referenced_names = {n for s in self.new_supply_sets.values() for n in (s.power, s.ground, s.n_well, s.p_well) if n}
        self.new_supply_ports = {name: color for name, color in self.new_supply_ports.items() if name in referenced_names}
        self.new_supply_port_states = {name: states for name, states in self.new_supply_port_states.items() if name in referenced_names}

    # --- accepted values -------------------------------------------------

    @property
    def name(self) -> str:
        return self.name_edit.text().strip()

    @property
    def primary_supply_set_id(self) -> Optional[str]:
        return self.primary_combo.currentData()

    @property
    def retention_supply_set_id(self) -> Optional[str]:
        data = self.retention_combo.currentData()
        return None if data == _ADD_NEW_SUPPLY_SET else data

    @property
    def save_logic(self) -> str:
        return self.save_logic_edit.text().strip()

    @property
    def restore_logic(self) -> str:
        return self.restore_logic_edit.text().strip()

    @property
    def elements(self) -> list[str]:
        return [
            self.elements_list.item(i).text()
            for i in range(self.elements_list.count())
            if self.elements_list.item(i).checkState() == Qt.CheckState.Checked
        ]


class RetentionStrategyInfoDialog(QDialog):
    """Read-only "Info" view of a Retention Strategy's own current
    values -- triggered from the badge icon's own right-click menu (see
    power_intent_diagram_view.py's _retention_context_menu_actions()).
    No existing "Info" convention exists elsewhere in this diagram to
    reuse (Supply Port's own right-click menu only ever offers Edit/
    Delete, confirmed by reading it before building this) -- a new,
    minimal QFormLayout of plain labels, consistent with every other
    dialog's own layout style in this module rather than inventing a
    different look for a read-only view."""

    def __init__(self, strategy: RetentionStrategyPlacement, domain_name: str, primary_name: str, retention_name: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setSizeGripEnabled(True)  # explicit request (Bug 5): every dialog here must be user-resizable
        self.setWindowTitle(f"Retention Strategy: {strategy.name}")
        elements_list = QListWidget()
        elements_list.addItems(strategy.elements)
        elements_list.setEnabled(False)  # read-only -- Info, not Edit

        form = QFormLayout(self)
        form.addRow("Name:", QLabel(strategy.name))
        form.addRow("Power Domain:", QLabel(domain_name))
        form.addRow("Primary Supply Set:", QLabel(primary_name))
        form.addRow("Retention Supply Set:", QLabel(retention_name))
        form.addRow("Save Signal:", QLabel(strategy.save_logic))
        form.addRow("Restore Signal:", QLabel(strategy.restore_logic))
        form.addRow("Elements:", elements_list)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
