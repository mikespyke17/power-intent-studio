""""Isolation Cell" dialog (Add/Edit) -- triggered from the Power
Intent tab's Diagram via the Isolation Cell tool's own hover+click
placement or the right-click "Add Isolation" context-menu action, both
funneling through the identical (validate then this dialog) flow, same
"do NOT build a new one" precedent every other placement tool here
already follows (see power_intent_diagram_view.py's own
_prompt_add_isolation()).

Diagram-only for this pass, same scoping decision as every other
dialog here (see supply_port_overlay.py's own module docstring) -- this
dialog only ever hands back plain data, never touches shared state
itself.

Unlike Retention (whose own Power Domain is fixed, read-only context --
the block "Add Retention" was invoked on already determines it), this
dialog's own "Power Domain" field is itself a required, user-selectable
dropdown (explicit request: "populated with ALL existing Power Domains
in the design") -- so `boxes`/`resolve_connectivity` are threaded in
and the Elements table/"Source Supply Set" pool are both RE-DERIVED
live every time the Domain selection changes (`_on_domain_changed()`),
not computed once up front the way Retention's own dialog receives
them pre-computed from its caller.

Elements table (explicit request, Feature 2 reuse): columns Name/
Direction/Receiver Supply/Driver Supply Set, sourced from
isolation_overlay.collect_isolation_elements_for_domain() (which
element keys), isolation_overlay.port_direction_display() (the
Direction column -- reproduces PortList's own IN/OUT/INOUT display
values without importing that module's private symbol), and
`resolve_connectivity` (the SAME PowerResolutionCache-backed Driver/
Receiver computation the PortList tab already uses -- see
power_intent_diagram_view.py's own _resolve_port_connectivity()
wiring -- not recomputed independently here). A per-row checkbox on
the Name cell (Qt.ItemIsUserCheckable) is the actual "included in this
Isolation" state; QTableWidget's own row SELECTION (Shift+Click) is a
completely separate, purely-UI concept the Add Selected/Clear Selected
buttons act on -- same two-state split retention_dialog.py's own
Elements QListWidget already establishes, just adapted to a
QTableWidget (a real table, not a bare name list, per this dialog's
own 3-column requirement) rather than reused verbatim (the underlying
widget type genuinely differs; the SELECTED-vs-CHECKED split and the
4-button semantics are mirrored exactly).

Filter (explicit request, NEW capability, Isolation-only): the
Elements table is wrapped with shared/widgets/table_filter.py's own
`wrap_with_filter()`/`TableFilterBar` -- already field-scoped (an
"Apply to" column combo, see that module's own docstring) and already
built/tested, so no new filter UI needed at all. Deliberately NOT
retention_dialog.py's own `ListFilterBar` (wildcard-only, no column
picker, list-shaped) -- reusing TableFilterBar here doesn't touch
ListFilterBar or Retention's own dialog in any way, matching the
explicit "do NOT modify Retention's existing filter" constraint while
still satisfying "build it as a shared, reusable component" (it
already is one).

A second, independent "Inputs"/"Outputs"/"Both" (default Both)
exclusive filter group (`direction_filter_group`, an
`_ExclusiveCheckboxGroup` extended with an `on_change` callback) sits
above the table alongside the text filter -- VISIBILITY ONLY, combined
with (never replacing) TableFilterBar's own filter via
`_apply_direction_filter()`: reapply the text filter first, then
further hide rows failing the direction filter. Checked/included state
lives entirely on each row's own checkbox and is never touched by row
hiding, so it accumulates correctly as the user switches filters.
"""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from frontend.src.power_intent_view.isolation_overlay import (
    ISOLATION_APPLIES_TO_ELEMENTS,
    IsolationStrategyPlacement,
    collect_isolation_elements_for_domain,
    port_direction_display,
)
from frontend.src.power_intent_view.name_validation import NO_SPACES_ERROR, validate_no_spaces
from frontend.src.power_intent_view.power_domain_overlay import PowerDomainPlacement, SupplySetPlacement
from frontend.src.shared.widgets import message_box
from frontend.src.shared.diagram.hierarchy_scene_builder import InstanceBoxItem
from frontend.src.shared.widgets.form_separator import form_separator
from frontend.src.shared.widgets.optional_field import wrap_optional_field
from frontend.src.shared.widgets.elements_table import (
    DIRECTION_COL as _DIRECTION_COL,
    ExclusiveCheckboxGroup as _ExclusiveCheckboxGroup,
    apply_direction_filter as _shared_apply_direction_filter,
    build_elements_picker as _build_elements_picker,
    selected_rows as _shared_selected_rows,
    set_checked_for_rows as _shared_set_checked_for_rows,
)
from frontend.src.shared.widgets.target_hierarchy_label import APPLIES_TO_CAPTION, make_target_hierarchy_label

_CLAMP_VALUES = ("low", "high", "keep")
_CLAMP_LABELS = {"low": "Low", "high": "High", "keep": "Keep"}
_SENSE_VALUES = ("low", "high")
_SENSE_LABELS = {"low": "Low", "high": "High"}
_LOCATION_VALUES = ("self", "parent", "fanout")
# "Applies To All" -- the same choice, values and labels as Level
# Shifter's own (level_shifter_dialog.py). Only "elements" makes the
# Elements section relevant; the others apply the rule to the domain's
# whole boundary in that direction (UPF `-applies_to`).
_APPLIES_TO_VALUES = ("inputs", "outputs", "both", ISOLATION_APPLIES_TO_ELEMENTS)
_APPLIES_TO_LABELS = {"inputs": "Inputs", "outputs": "Outputs", "both": "Both", "elements": "Elements"}


class IsolationDialog(QDialog):
    """The same dialog class serves both Add and Edit (same precedent
    as CreatePowerDomainDialog/RetentionStrategyDialog): passing
    `initial` (an existing IsolationStrategyPlacement) and
    `editing_strategy_id` pre-populates every field and excludes that
    strategy's own current name from the global uniqueness check at
    accept() time.

    `existing_names` (strategy id -> name, every Isolation Cell in the
    ENTIRE design -- explicit request: "the SAME global-uniqueness
    convention already used for Retention Strategy names") backs the
    accept()-time duplicate-name QMessageBox.

    `initial_domain_id` (Add mode only -- ignored when `initial` is
    given, whose own `power_domain_id` always wins) pre-selects the
    Power Domain combo to whichever domain the block the user actually
    clicked/hovered owns, rather than defaulting to the alphabetically-
    first entry in the list."""

    def __init__(
        self,
        boxes: dict[str, InstanceBoxItem],
        domains: dict[str, PowerDomainPlacement],
        supply_sets: dict[str, SupplySetPlacement],
        resolve_connectivity: Callable[[list[tuple[str, str]]], tuple[dict, dict]],
        existing_names: dict[str, str],
        editing_strategy_id: Optional[str] = None,
        initial: Optional[IsolationStrategyPlacement] = None,
        parent: Optional[QWidget] = None,
        target_paths: Optional[list[str]] = None,
        initial_domain_id: Optional[str] = None,
    ):
        super().__init__(parent)
        self.setSizeGripEnabled(True)  # explicit request (Bug 5): every dialog here must be user-resizable
        self.setWindowTitle("Edit Isolation" if editing_strategy_id is not None else "Add Isolation")
        self._boxes = boxes
        self._domains = dict(domains)
        self._supply_sets = dict(supply_sets)
        self._resolve_connectivity = resolve_connectivity
        self._existing_names = dict(existing_names)
        self._editing_strategy_id = editing_strategy_id
        self._initial = initial
        # Only the INITIAL domain's own already-enabled elements are
        # ever pre-checked (Edit mode) -- switching the Domain combo
        # away from it, even back again, resets checked state, since a
        # different domain's own Elements are a genuinely different
        # I/O pool (see _on_domain_changed()'s own docstring).
        self._initial_checked = set(initial.elements) if initial is not None else set()
        self._elements_populated_once = False

        self.name_edit = QLineEdit(initial.name if initial is not None else "")
        self.name_edit.setPlaceholderText("e.g. ISO_CPU_OUT")
        # Prefix for the inserted CELL instances, not for this strategy's
        # own name. Optional.
        self.name_prefix_edit = QLineEdit(initial.name_prefix if initial is not None else "")
        self.name_prefix_edit.setPlaceholderText("e.g. iso_lvf_ \u2014 prefix for inserted isolation cell instances")

        self.domain_combo = QComboBox()
        for domain_id, domain in sorted(self._domains.items(), key=lambda kv: kv[1].name):
            self.domain_combo.addItem(domain.name, domain_id)
        self.domain_combo.currentIndexChanged.connect(self._on_domain_changed)

        self.isolation_supply_combo = QComboBox()
        self.isolation_supply_combo.addItem("", None)
        for set_id, supply_set in sorted(self._supply_sets.items(), key=lambda kv: kv[1].name):
            self.isolation_supply_combo.addItem(supply_set.name, set_id)

        self.source_supply_combo = QComboBox()
        self.sink_supply_combo = QComboBox()
        self.sink_supply_combo.addItem("", None)
        for set_id, supply_set in sorted(self._supply_sets.items(), key=lambda kv: kv[1].name):
            self.sink_supply_combo.addItem(supply_set.name, set_id)

        self.clamp_group = _ExclusiveCheckboxGroup(self, _CLAMP_VALUES, _CLAMP_LABELS)
        self.sense_group = _ExclusiveCheckboxGroup(self, _SENSE_VALUES, _SENSE_LABELS)
        self.applies_to_group = _ExclusiveCheckboxGroup(
            self, _APPLIES_TO_VALUES, _APPLIES_TO_LABELS, on_change=self._update_elements_enabled
        )

        self.location_combo = QComboBox()
        for value in _LOCATION_VALUES:
            self.location_combo.addItem(value, value)

        self.isolation_control_edit = QLineEdit(initial.isolation_control if initial is not None else "")
        self.isolation_control_edit.setPlaceholderText("e.g. iso_en \u2014 the RTL signal controlling isolation")

        # Audit fix (category 07): the table, its two filters and its four
        # buttons are built by the shared picker
        # (shared/widgets/elements_table.py) rather than assembled
        # identically here and in the other dialog. The attribute names
        # are unchanged, so populate/accept/test code is untouched.
        _picker = _build_elements_picker(self, self._apply_direction_filter, self._update_selected_buttons_enabled)
        self.elements_table = _picker.table
        self._elements_container = _picker.container
        self.elements_filter = _picker.filter_bar
        self.direction_filter_group = _picker.direction_filter_group
        self.add_selected_button = _picker.add_selected_button
        self.clear_selected_button = _picker.clear_selected_button
        self.add_all_button = _picker.add_all_button
        self.clear_all_button = _picker.clear_all_button
        self._update_selected_buttons_enabled()
        # Section 4, explicit request: 3 mutually-exclusive tick boxes
        # ABOVE the table, defaulting to "Both" -- filter table VISIBILITY
        # only (setRowHidden(), never touches checkState()) -- see
        # _apply_direction_filter()'s own docstring for how this combines
        # with elements_filter's own wildcard text filter without either
        # one clobbering the other's decision.
        # Reapplied together with the direction filter on every text-filter-
        # triggering event too (see _apply_direction_filter()'s own
        # docstring) -- reaching into TableFilterBar's own private text/
        # column widgets is the simplest way to hook "after every text
        # filter change" without TableFilterBar needing its own new public
        # API just for this one caller.



        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        self._ok_button = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        self._ok_button.setEnabled(bool(self.name_edit.text().strip()))
        self.name_edit.textChanged.connect(lambda text: self._ok_button.setEnabled(bool(text.strip())))

        fields_form = QFormLayout()
        if target_paths is not None:
            fields_form.addRow(APPLIES_TO_CAPTION, make_target_hierarchy_label(target_paths))
        fields_form.addRow(form_separator())
        fields_form.addRow("Name:", self.name_edit)
        fields_form.addRow("Power Domain:", self.domain_combo)
        fields_form.addRow("Name Prefix:", wrap_optional_field(self.name_prefix_edit))
        fields_form.addRow("Isolation Supply Set:", self.isolation_supply_combo)
        fields_form.addRow("Source Supply Set:", wrap_optional_field(self.source_supply_combo))
        fields_form.addRow("Sink Supply Set:", wrap_optional_field(self.sink_supply_combo))
        fields_form.addRow("Clamp Value:", self.clamp_group.row())
        fields_form.addRow("Location:", self.location_combo)
        fields_form.addRow("Isolation Signal:", self.isolation_control_edit)
        fields_form.addRow("Isolation Sense:", self.sense_group.row())
        fields_form.addRow("Applies To All:", self.applies_to_group.row())
        fields_form.addRow(form_separator())

        elements_row = QHBoxLayout()
        elements_row.addWidget(self.add_selected_button)
        elements_row.addWidget(self.clear_selected_button)
        elements_all_row = QHBoxLayout()
        elements_all_row.addWidget(self.add_all_button)
        elements_all_row.addWidget(self.clear_all_button)

        elements_column = QVBoxLayout()
        elements_column.addWidget(QLabel("Elements:"))
        elements_column.addLayout(self.direction_filter_group.row())
        elements_column.addWidget(self._elements_container)
        elements_column.addLayout(elements_row)
        elements_column.addLayout(elements_all_row)
        # One wrapper widget, so _update_elements_enabled() greys out the
        # whole Elements section (label, filters, table, buttons) with a
        # single setEnabled() -- same as Level Shifter's dialog.
        self.elements_group_widget = QWidget()
        self.elements_group_widget.setLayout(elements_column)

        outer = QVBoxLayout(self)
        outer.addLayout(fields_form)
        outer.addWidget(self.elements_group_widget, 1)
        outer.addWidget(self.buttons)

        # Initial population -- ALWAYS calls _on_domain_changed() explicitly
        # (never relies on currentIndexChanged firing as a side effect of
        # setCurrentIndex(), which is a documented Qt no-op when the index
        # doesn't actually change -- e.g. `initial`'s own domain happening
        # to already be index 0) -- both source_supply_combo and the
        # Elements table must be populated BEFORE selecting `initial`'s own
        # Source Supply Set/checked elements into them.
        if initial is not None:
            index = self.domain_combo.findData(initial.power_domain_id)
            if index >= 0:
                self.domain_combo.blockSignals(True)
                self.domain_combo.setCurrentIndex(index)
                self.domain_combo.blockSignals(False)
            self._on_domain_changed()
            self._select_combo_data(self.isolation_supply_combo, initial.isolation_supply_set_id)
            self._select_combo_data(self.source_supply_combo, initial.source_supply_set_id)
            self._select_combo_data(self.sink_supply_combo, initial.sink_supply_set_id)
            self.clamp_group.selected = initial.clamp_value
            self.sense_group.selected = initial.isolation_sense
            self.applies_to_group.selected = initial.applies_to
            index = self.location_combo.findData(initial.location)
            if index >= 0:
                self.location_combo.setCurrentIndex(index)
        elif self.domain_combo.count() > 0:
            if initial_domain_id is not None:
                index = self.domain_combo.findData(initial_domain_id)
                if index >= 0:
                    self.domain_combo.blockSignals(True)
                    self.domain_combo.setCurrentIndex(index)
                    self.domain_combo.blockSignals(False)
            self._on_domain_changed()
        # A new strategy starts on "Elements" -- the element-picking
        # behaviour this dialog always had -- rather than unselected.
        if self.applies_to_group.selected is None:
            self.applies_to_group.selected = ISOLATION_APPLIES_TO_ELEMENTS
        self._update_elements_enabled()

    def _update_elements_enabled(self) -> None:
        """The Elements section is selectable only when Applies To All is
        "Elements". Checked rows are left as they are when switching
        away, so switching back restores them; `elements` ignores them
        meanwhile."""
        self.elements_group_widget.setEnabled(self.applies_to_group.selected == ISOLATION_APPLIES_TO_ELEMENTS)

    def _select_combo_data(self, combo: QComboBox, data) -> None:
        index = combo.findData(data)
        if index >= 0:
            combo.setCurrentIndex(index)

    # --- Domain-driven Source Supply Set pool + Elements table ------------

    def _on_domain_changed(self) -> None:
        """Re-derives the Source Supply Set pool and the Elements table
        every time "Power Domain" changes -- unlike Retention (fixed,
        read-only domain context), this dialog's own Domain field is a
        user-selectable dropdown, so both must be live-recomputed, not
        computed once by the caller. Resets the Elements table's own
        checked state (a different domain's own boundary blocks are a
        genuinely different I/O pool -- there is no meaningful "carry
        over" between them), EXCEPT the very first population when
        `initial` names this exact domain (Edit mode's own already-
        enabled elements, see __init__)."""
        domain = self._domains.get(self.domain_combo.currentData())
        self._refresh_source_supply_combo(domain)
        self._refresh_elements_table(domain)

    def _refresh_source_supply_combo(self, domain: Optional[PowerDomainPlacement]) -> None:
        previous = self.source_supply_combo.currentData() if self.source_supply_combo.count() else None
        self.source_supply_combo.blockSignals(True)
        self.source_supply_combo.clear()
        self.source_supply_combo.addItem("", None)
        if domain is not None:
            # Explicit request: "Supply Sets belonging specifically to
            # the Power Domain" -- owner_path membership only. NOT
            # power_domain_overlay.supply_sets_for_owners() as-is: that
            # helper ALSO excludes any Supply Set already claimed as
            # another domain's own Primary/Secondary, a rule specific to
            # Create/Edit Power Domain's own picker semantics that
            # doesn't apply to "Source Supply Set" (a different kind of
            # reference, not a claim) -- reusing it here would silently
            # hide legitimate choices the spec never asked to exclude.
            for set_id, supply_set in sorted(self._supply_sets.items(), key=lambda kv: kv[1].name):
                if any(op in supply_set.owner_paths for op in domain.owner_paths):
                    self.source_supply_combo.addItem(supply_set.name, set_id)
        index = self.source_supply_combo.findData(previous)
        self.source_supply_combo.setCurrentIndex(index if index >= 0 else 0)
        self.source_supply_combo.blockSignals(False)

    def _refresh_elements_table(self, domain: Optional[PowerDomainPlacement]) -> None:
        """Repopulates the Elements table for `domain`. Checked state is
        preserved ONLY on the very first population (this dialog's own
        initial construction) when editing an existing strategy whose
        `power_domain_id` matches `domain` -- that's `initial`'s own
        already-enabled elements (see __init__). Any LATER call (the
        user actually changing the Domain combo) starts every row
        unchecked: a different domain's own boundary I/O is a genuinely
        different pool, there is nothing meaningful to carry over (see
        this method's own class-level docstring note in
        _on_domain_changed())."""
        elements = collect_isolation_elements_for_domain(self._boxes, domain) if domain is not None else []
        endpoints = [self._endpoint_for_element(element) for element in elements]
        receiver_by_key, driver_by_key = self._resolve_connectivity(endpoints) if endpoints else ({}, {})
        is_first_population = not self._elements_populated_once
        self._elements_populated_once = True
        editing_this_domain = self._initial is not None and domain is not None and domain.id == self._initial.power_domain_id
        preserve = self._initial_checked if (is_first_population and editing_this_domain) else set()

        self.elements_table.setSortingEnabled(False)
        self.elements_table.setRowCount(len(elements))
        for row, element in enumerate(elements):
            endpoint = endpoints[row]
            owner_path, port_name = endpoint
            receiver = receiver_by_key.get(endpoint)
            driver = driver_by_key.get(endpoint)
            name_item = QTableWidgetItem(element)
            name_item.setFlags(name_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            name_item.setCheckState(Qt.CheckState.Checked if element in preserve else Qt.CheckState.Unchecked)
            name_item.setData(Qt.ItemDataRole.UserRole, element)
            self.elements_table.setItem(row, 0, name_item)
            box = self._boxes.get(owner_path)
            direction = port_direction_display(box, owner_path, port_name) if box is not None else ""
            self.elements_table.setItem(row, _DIRECTION_COL, QTableWidgetItem(direction))
            self.elements_table.setItem(row, 2, QTableWidgetItem(receiver.text if receiver is not None else ""))
            self.elements_table.setItem(row, 3, QTableWidgetItem(driver.text if driver is not None else ""))
        self.elements_table.setSortingEnabled(True)
        self.elements_filter.reapply()
        self._apply_direction_filter()
        self._update_selected_buttons_enabled()

    def _apply_direction_filter(self) -> None:
        """Delegates to the shared implementation (audit fix, category
        07) -- see shared/widgets/elements_table.py's own
        apply_direction_filter() for the full "why" of how the direction
        filter and the wildcard text filter intersect without either
        clobbering the other."""
        _shared_apply_direction_filter(self.elements_table, self.elements_filter, self.direction_filter_group)
    @staticmethod
    def _endpoint_for_element(element: str) -> tuple[str, str]:
        """"<owner_path>/<row_key>" -> (owner_path, row_key), the exact
        (path, signal) key shape resolve_all()/`resolve_connectivity`
        already use (see power_resolution_cache.py's own resolve_all())
        -- `owner_path` itself can contain further "/" separators, so
        this splits on the LAST one, matching
        collect_isolation_elements_for_domain()'s own construction."""
        owner_path, row_key = element.rsplit("/", 1)
        return owner_path, row_key

    # --- Elements: 4 buttons (same semantics as retention_dialog.py's own,
    # adapted to a QTableWidget's own row-selection/per-item-checkbox shape) --

    def _update_selected_buttons_enabled(self) -> None:
        has_selection = bool(self.elements_table.selectedItems())
        self.add_selected_button.setEnabled(has_selection)
        self.clear_selected_button.setEnabled(has_selection)

    # Audit fix (category 07): these four thin wrappers stay so existing
    # tests that call them directly keep working; the behaviour itself
    # now lives in shared/widgets/elements_table.py.
    def _selected_rows(self) -> set[int]:
        return _shared_selected_rows(self.elements_table)

    def _add_selected_elements(self) -> None:
        _shared_set_checked_for_rows(self.elements_table, self._selected_rows(), True)

    def _clear_selected_elements(self) -> None:
        _shared_set_checked_for_rows(self.elements_table, self._selected_rows(), False)

    def _add_all_elements(self) -> None:
        _shared_set_checked_for_rows(self.elements_table, range(self.elements_table.rowCount()), True)

    def _clear_all_elements(self) -> None:
        _shared_set_checked_for_rows(self.elements_table, range(self.elements_table.rowCount()), False)

    # --- accept validation -----------------------------------------------

    def accept(self) -> None:
        name = self.name
        if not validate_no_spaces(name):
            message_box.warning(self, "Invalid Name", NO_SPACES_ERROR)
            return
        for strategy_id, existing_name in self._existing_names.items():
            if strategy_id != self._editing_strategy_id and existing_name == name:
                message_box.warning(self, "Duplicate Isolation Name", f"An Isolation named '{name}' already exists. Choose a different name.")
                return
        if self.power_domain_id is None:
            message_box.warning(self, "Missing Power Domain", "A Power Domain is required.")
            return
        if self.isolation_supply_set_id is None:
            message_box.warning(self, "Missing Isolation Supply Set", "An Isolation Supply Set is required.")
            return
        if self.clamp_group.selected is None:
            message_box.warning(self, "Missing Clamp Value", "A Clamp Value (Low, High, or Keep) is required.")
            return
        if not validate_no_spaces(self.isolation_control):
            message_box.warning(self, "Invalid Isolation Control", NO_SPACES_ERROR)
            return
        if not self.isolation_control:
            message_box.warning(self, "Missing Isolation Control", "Isolation Control is required.")
            return
        if self.sense_group.selected is None:
            message_box.warning(self, "Missing Isolation Sense", "An Isolation Sense (Low or High) is required.")
            return
        if self.applies_to is None:
            message_box.warning(self, "Missing Applies To All", "An Applies To All value (Inputs, Outputs, Both, or Elements) is required.")
            return
        if self.applies_to == ISOLATION_APPLIES_TO_ELEMENTS and not self.elements:
            message_box.warning(self, "No Elements Selected", 'At least one element must be selected when Applies To All is set to "Elements".')
            return
        super().accept()

    # --- accepted values -------------------------------------------------

    @property
    def name_prefix(self) -> str:
        return self.name_prefix_edit.text().strip()

    @property
    def name(self) -> str:
        return self.name_edit.text().strip()

    @property
    def power_domain_id(self) -> Optional[str]:
        return self.domain_combo.currentData()

    @property
    def isolation_supply_set_id(self) -> Optional[str]:
        return self.isolation_supply_combo.currentData()

    @property
    def source_supply_set_id(self) -> Optional[str]:
        return self.source_supply_combo.currentData()

    @property
    def sink_supply_set_id(self) -> Optional[str]:
        return self.sink_supply_combo.currentData()

    @property
    def clamp_value(self) -> Optional[str]:
        return self.clamp_group.selected

    @property
    def location(self) -> str:
        return self.location_combo.currentData()

    @property
    def isolation_control(self) -> str:
        return self.isolation_control_edit.text().strip()

    @property
    def isolation_sense(self) -> Optional[str]:
        return self.sense_group.selected

    @property
    def applies_to(self) -> Optional[str]:
        return self.applies_to_group.selected

    @property
    def elements(self) -> list[str]:
        """Empty unless Applies To All is "Elements" -- rows still checked
        underneath a broad Inputs/Outputs/Both choice are never
        submitted."""
        if self.applies_to != ISOLATION_APPLIES_TO_ELEMENTS:
            return []
        return [
            self.elements_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
            for row in range(self.elements_table.rowCount())
            if self.elements_table.item(row, 0).checkState() == Qt.CheckState.Checked
        ]


class IsolationInfoDialog(QDialog):
    """Read-only "Info" view of an Isolation Cell's own current values --
    triggered from the badge icon's own right-click menu. Same
    convention as RetentionStrategyInfoDialog (explicit request: "reuse
    that widget/pattern") -- a plain QFormLayout of QLabels, and for
    Elements specifically, the exact SAME disabled QListWidget display
    convention (not the dialog's own editable QTableWidget -- Info is
    read-only, and a plain name list is all that's needed here, matching
    Retention's own Info view precedent exactly)."""

    def __init__(
        self,
        strategy: IsolationStrategyPlacement,
        domain_name: str,
        isolation_supply_name: str,
        source_supply_name: str,
        sink_supply_name: str,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.setSizeGripEnabled(True)  # explicit request (Bug 5): every dialog here must be user-resizable
        self.setWindowTitle(f"Isolation Cell: {strategy.name}")
        elements_list = QListWidget()
        elements_list.addItems(strategy.elements)
        elements_list.setEnabled(False)

        form = QFormLayout(self)
        form.addRow("Name:", QLabel(strategy.name))
        form.addRow("Power Domain:", QLabel(domain_name))
        form.addRow("Isolation Supply Set:", QLabel(isolation_supply_name))
        form.addRow("Source Supply Set:", QLabel(source_supply_name))
        form.addRow("Sink Supply Set:", QLabel(sink_supply_name))
        form.addRow("Clamp Value:", QLabel(_CLAMP_LABELS.get(strategy.clamp_value, strategy.clamp_value)))
        form.addRow("Location:", QLabel(strategy.location))
        form.addRow("Isolation Signal:", QLabel(strategy.isolation_control))
        form.addRow("Isolation Sense:", QLabel(_SENSE_LABELS.get(strategy.isolation_sense, strategy.isolation_sense)))
        form.addRow("Applies To All:", QLabel(_APPLIES_TO_LABELS.get(strategy.applies_to, strategy.applies_to)))
        form.addRow("Elements:", elements_list)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
