""""Level Shifter" dialog (Add/Edit) -- triggered from the Power Intent
tab's Diagram via the Level Shifter tool's own hover+click placement or
the right-click "Add Level Shifter" context-menu action, both funneling
through the identical (validate then this dialog) flow, same "do NOT
build a new one" precedent every other placement tool here already
follows (see power_intent_diagram_view.py's own _prompt_add_level_shifter()).

Diagram-only for this pass, same scoping decision as every other dialog
here (see supply_port_overlay.py's own module docstring) -- this dialog
only ever hands back plain data, never touches shared state itself.

Field order (per the explicit request):
  - Name (text, required, globally unique)
  - Power Domain (dropdown, required -- populated with every existing
    Power Domain, pre-filled with the target block's own domain,
    READ-ONLY -- explicit request: "same convention as Retention's
    Power Domain field", unlike Isolation's own user-selectable
    dropdown. A plain QLabel, not a QComboBox, matching
    retention_dialog.py's own read-only domain display exactly.)
  - Applies To All: Inputs | Outputs | Both | Elements (mutually
    exclusive tick boxes -- reworked from the original 3-option
    "Applies To" field, explicit follow-up request). Selecting
    Inputs/Outputs/Both means the Level Shifter applies broadly to all
    matching I/O of that direction -- Elements is NOT required in that
    case. Selecting "Elements" instead means the user wants to target
    SPECIFIC named signals -- Elements becomes REQUIRED in that case.
  - Rule: Low to High | High to Low | Both (mutually exclusive tick
    boxes) -- ADVISORY/USER-ASSERTED ONLY, no automatic validation
    (see level_shifter_overlay.LevelShifterStrategyPlacement's own
    docstring).
  - Location: Self | Parent | Fanout (dropdown, default Self) -- the
    EXACT SAME widget/values as Isolation's own Location field
    (isolation_dialog.py's own `_LOCATION_VALUES`, duplicated here
    verbatim -- same "small duplication over forced cross-dialog
    import" precedent this app already establishes repeatedly).
  - Threshold (OPTIONAL numeric input, italic "(Optional)" label,
    rendered to the RIGHT of the field -- global styling rule, see
    shared/widgets/optional_field.py) -- ADVISORY/USER-ASSERTED ONLY,
    same no-validation status as Rule.
  - Cell Type (rework, explicit request -- REVERSES the earlier "Level
    Shifter Cell Type dropdown referencing a reusable
    LevelShifterCellPlacement library" model): plain, OPTIONAL free-text
    input -- no more separate library entity to create first. Leaving it
    blank is not a blocking error -- see properties_panel.py's own
    docstring for the generation-time warning this triggers instead,
    same "No Cell Type Assigned" convention Power Switch's own Cell Type
    already establishes.
  - Elements (CONDITIONALLY REQUIRED -- explicit follow-up rework:
    required only when Applies To All is set to "Elements"; DISABLED
    (greyed out, non-interactive) whenever Applies To All is Inputs/
    Outputs/Both instead, since it's not relevant there -- see
    `_update_elements_enabled()`) -- the SAME Name/Direction/Receiver
    Supply/Driver Supply Set table, direction filter group, and
    field-scoped wildcard filter Isolation's own dialog already builds.
    Duplicated here (not factored into a shared widget and imported)
    rather than modifying isolation_dialog.py itself -- that module is
    a separate, already fully-tested feature, and touching it risks a
    regression there for no benefit to it; this app's own established
    precedent is small duplication over reaching into another feature's
    file (e.g. _optional_label(), _DEFAULT_COLOR).
  - OK/Cancel.

EDIT mode reopens this same dialog pre-filled with the existing Level
Shifter's current values, same convention as every other Edit flow
here.
"""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QDoubleValidator
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

from frontend.src.power_intent_view.isolation_overlay import collect_isolation_elements_for_domain, port_direction_display
from frontend.src.power_intent_view.level_shifter_overlay import LevelShifterStrategyPlacement
from frontend.src.power_intent_view.name_validation import NO_SPACES_ERROR, validate_no_spaces
from frontend.src.power_intent_view.power_domain_overlay import PowerDomainPlacement
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

_APPLIES_TO_VALUES = ("inputs", "outputs", "both", "elements")
_APPLIES_TO_LABELS = {"inputs": "Inputs", "outputs": "Outputs", "both": "Both", "elements": "Elements"}
_ELEMENTS_REQUIRING_VALUE = "elements"  # the only Applies To All choice that makes Elements required/relevant
_RULE_VALUES = ("low_to_high", "high_to_low", "both")
_RULE_LABELS = {"low_to_high": "Low to High", "high_to_low": "High to Low", "both": "Both"}
_LOCATION_VALUES = ("self", "parent", "fanout")


class LevelShifterDialog(QDialog):
    """The same dialog class serves both Add and Edit (same precedent
    as IsolationDialog/RetentionStrategyDialog). `target_paths` is the
    domain's own boundary blocks (Retention/Isolation-model "Applies to"
    display, read-only) -- unlike Isolation's own user-selectable
    Power Domain combo, this dialog's own Power Domain is fixed context
    (explicit request: "same convention as Retention's Power Domain
    field"), so Elements/Source pool are computed ONCE from `domain`,
    never re-derived on a domain change the way IsolationDialog's own
    `_on_domain_changed()` has to."""

    def __init__(
        self,
        boxes: dict[str, InstanceBoxItem],
        domain: PowerDomainPlacement,
        resolve_connectivity: Callable[[list[tuple[str, str]]], tuple[dict, dict]],
        existing_names: dict[str, str],
        editing_strategy_id: Optional[str] = None,
        initial: Optional[LevelShifterStrategyPlacement] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.setSizeGripEnabled(True)  # explicit request (Bug 5): every dialog here must be user-resizable
        self.setWindowTitle("Edit Level Shifter" if editing_strategy_id is not None else "Add Level Shifter")
        self._boxes = boxes
        self._domain = domain
        self._resolve_connectivity = resolve_connectivity
        self._existing_names = dict(existing_names)
        self._editing_strategy_id = editing_strategy_id

        self.name_edit = QLineEdit(initial.name if initial is not None else "")
        self.name_edit.setPlaceholderText("e.g. LS_CPU_OUT")
        # Prefix for the inserted CELL instances, not for this strategy's
        # own name. Optional.
        self.name_prefix_edit = QLineEdit(initial.name_prefix if initial is not None else "")
        self.name_prefix_edit.setPlaceholderText("e.g. ls_lvf_ \u2014 prefix for inserted level shifter cell instances")
        self.domain_label = QLabel(domain.name)  # read-only -- auto-filled from the target's own domain

        self.applies_to_group = _ExclusiveCheckboxGroup(self, _APPLIES_TO_VALUES, _APPLIES_TO_LABELS, on_change=self._update_elements_enabled)
        self.rule_group = _ExclusiveCheckboxGroup(self, _RULE_VALUES, _RULE_LABELS)

        self.location_combo = QComboBox()
        for value in _LOCATION_VALUES:
            self.location_combo.addItem(value, value)

        self.threshold_edit = QLineEdit(str(initial.threshold) if initial is not None and initial.threshold is not None else "")
        self.threshold_edit.setPlaceholderText("e.g. 0.2 \u2014 minimum voltage delta to trigger insertion")
        self.threshold_edit.setValidator(QDoubleValidator())

        self.cell_type_edit = QLineEdit((initial.cell_type or "") if initial is not None else "")
        self.cell_type_edit.setPlaceholderText("e.g. LS_HL_X2")

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



        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        self._ok_button = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        self._ok_button.setEnabled(bool(self.name_edit.text().strip()))
        self.name_edit.textChanged.connect(lambda text: self._ok_button.setEnabled(bool(text.strip())))

        fields_form = QFormLayout()
        fields_form.addRow(APPLIES_TO_CAPTION, make_target_hierarchy_label(domain.owner_paths))
        fields_form.addRow(form_separator())
        fields_form.addRow("Name:", self.name_edit)
        fields_form.addRow("Power Domain:", self.domain_label)
        fields_form.addRow("Name Prefix:", wrap_optional_field(self.name_prefix_edit))
        fields_form.addRow("Applies To All:", self.applies_to_group.row())
        fields_form.addRow("Rule:", self.rule_group.row())
        fields_form.addRow("Location:", self.location_combo)
        fields_form.addRow("Threshold:", wrap_optional_field(self.threshold_edit))
        fields_form.addRow("Cell Type:", wrap_optional_field(self.cell_type_edit))
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
        # Bug fix/rework (explicit follow-up request, superseding the
        # earlier "non-blocking warning" design): the whole Elements
        # section is wrapped in ONE QWidget specifically so
        # _update_elements_enabled() can disable/enable everything in it
        # (label, filter bar, direction filter, table, all 4 buttons)
        # with a single setEnabled() call -- Qt's own disabled-cascades-
        # to-children behavior, rather than toggling each child widget
        # individually.
        self.elements_group_widget = QWidget()
        self.elements_group_widget.setLayout(elements_column)

        outer = QVBoxLayout(self)
        outer.addLayout(fields_form)
        outer.addWidget(self.elements_group_widget, 1)
        outer.addWidget(self.buttons)

        self._refresh_elements_table(set(initial.elements) if initial is not None else set())
        if initial is not None:
            self.applies_to_group.selected = initial.applies_to
            self.rule_group.selected = initial.rule
            index = self.location_combo.findData(initial.location)
            if index >= 0:
                self.location_combo.setCurrentIndex(index)
        self._update_elements_enabled()

    def _update_elements_enabled(self) -> None:
        """Bug fix/rework (explicit follow-up request): the Elements
        list is only ever relevant when Applies To All is set to
        "Elements" -- Inputs/Outputs/Both mean the Level Shifter applies
        broadly, with no specific element selection needed at all.
        Disabling (rather than the earlier "show a non-blocking
        warning" design) makes this state impossible to get wrong:
        greyed out and non-interactive whenever a broad option is
        selected, enabled only for "Elements". Deliberately does NOT
        clear whatever was previously checked -- switching back to
        "Elements" later restores the user's own prior selection rather
        than forcing them to redo it; `self.elements` itself already
        returns an empty list whenever Applies To All isn't "Elements"
        regardless of what's still checked underneath (see that
        property's own docstring), so no stale/irrelevant data can ever
        actually be submitted either way."""
        self.elements_group_widget.setEnabled(self.applies_to_group.selected == _ELEMENTS_REQUIRING_VALUE)

    # --- Elements table (identical shape to IsolationDialog's own) -------

    def _refresh_elements_table(self, preserve: set[str]) -> None:
        elements = collect_isolation_elements_for_domain(self._boxes, self._domain)
        endpoints = [self._endpoint_for_element(element) for element in elements]
        receiver_by_key, driver_by_key = self._resolve_connectivity(endpoints) if endpoints else ({}, {})

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
        owner_path, row_key = element.rsplit("/", 1)
        return owner_path, row_key

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
                message_box.warning(self, "Duplicate Level Shifter Name", f"A Level Shifter named '{name}' already exists. Choose a different name.")
                return
        if self.applies_to is None:
            message_box.warning(self, "Missing Applies To All", "An Applies To All value (Inputs, Outputs, Both, or Elements) is required.")
            return
        if self.rule is None:
            message_box.warning(self, "Missing Rule", "A Rule value (Low to High, High to Low, or Both) is required.")
            return
        # Rework (explicit follow-up request, section 3): Elements is
        # only REQUIRED when Applies To All is specifically "Elements" --
        # Inputs/Outputs/Both apply broadly with no explicit element
        # selection needed at all (the Elements list is disabled in
        # that case too -- see _update_elements_enabled()).
        if self.applies_to == _ELEMENTS_REQUIRING_VALUE and not self.elements:
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
    def power_domain_id(self) -> str:
        return self._domain.id

    @property
    def applies_to(self) -> Optional[str]:
        return self.applies_to_group.selected

    @property
    def rule(self) -> Optional[str]:
        return self.rule_group.selected

    @property
    def location(self) -> str:
        return self.location_combo.currentData()

    @property
    def threshold(self) -> Optional[float]:
        text = self.threshold_edit.text().strip()
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None

    @property
    def cell_type(self) -> Optional[str]:
        return self.cell_type_edit.text().strip() or None

    @property
    def elements(self) -> list[str]:
        """Rework (explicit follow-up request): always empty unless
        Applies To All is specifically "Elements" -- even if some rows
        are still checked underneath (deliberately left as-is when
        switching away, see _update_elements_enabled()'s own docstring),
        they're irrelevant while a broad Inputs/Outputs/Both choice is
        active, and must never be submitted as if they were a genuine,
        intentional element selection."""
        if self.applies_to != _ELEMENTS_REQUIRING_VALUE:
            return []
        return [
            self.elements_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
            for row in range(self.elements_table.rowCount())
            if self.elements_table.item(row, 0).checkState() == Qt.CheckState.Checked
        ]


class LevelShifterInfoDialog(QDialog):
    """Read-only "Info" view of a Level Shifter's own current values --
    triggered from the badge icon's own right-click menu. Same
    convention as IsolationInfoDialog/RetentionStrategyInfoDialog
    (explicit request precedent: "reuse that widget/pattern") -- a
    plain QFormLayout of QLabels, and for Elements specifically, the
    exact SAME disabled QListWidget display convention."""

    def __init__(
        self,
        strategy: LevelShifterStrategyPlacement,
        domain_name: str,
        cell_type_name: str,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.setSizeGripEnabled(True)  # explicit request (Bug 5): every dialog here must be user-resizable
        self.setWindowTitle(f"Level Shifter: {strategy.name}")
        elements_list = QListWidget()
        elements_list.addItems(strategy.elements)
        elements_list.setEnabled(False)

        form = QFormLayout(self)
        form.addRow("Name:", QLabel(strategy.name))
        form.addRow("Power Domain:", QLabel(domain_name))
        form.addRow("Applies To All:", QLabel(_APPLIES_TO_LABELS.get(strategy.applies_to, strategy.applies_to)))
        form.addRow("Rule:", QLabel(_RULE_LABELS.get(strategy.rule, strategy.rule)))
        form.addRow("Location:", QLabel(strategy.location))
        form.addRow("Threshold:", QLabel(str(strategy.threshold) if strategy.threshold is not None else ""))
        form.addRow("Cell Type:", QLabel(cell_type_name))
        form.addRow("Elements:", elements_list)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
