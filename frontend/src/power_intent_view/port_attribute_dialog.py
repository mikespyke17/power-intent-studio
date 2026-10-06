""""Port Attributes" dialog -- triggered from the Power Intent tab's
Diagram right-click "Add Port Attribute"/"Edit Port Attribute" actions,
gated to the design's own true top-level hierarchy block only (see
power_intent_diagram_view.py's _is_top_level_box()/_extra_context_menu_actions()).
Diagram-only for this pass, same scoping decision as every other
dialog here (see supply_port_overlay.py's own module docstring) -- this
dialog only ever hands back plain data, never touches shared state
itself; the view's own set_port_attributes() does that.

**Table-based editor** (explicit follow-up request, replacing the
original one-QComboBox-per-whole-port form layout): a QTableWidget,
one row per BIT this top-level block's own ports contribute
(`port_attribute_row_keys()`, port_attribute_overlay.py -- a genuinely
single-bit port keeps its own bare name as one row; a multi-bit port
gets one "<name>[i]" row per bit, 0-indexed ascending), columns
"Port Name" / "Direction" / "Receiver Supply Set" / "Driver Supply
Set". Each Supply Set combo is populated the same way the Retention
dialog's own Primary/Retention Supply Set pickers already are (every
Supply Set in the design plus an explicit empty/unassigned option --
see `_refresh_combo()`).

**Direction-based column locking**: an INPUT row's own Receiver combo
is disabled and forced empty (an input's receiver is always internal
to the design, never a boundary Port Attribute concern) -- only Driver
is editable. An OUTPUT row's own Driver combo is the one disabled and
forced empty -- only Receiver is editable. This mirrors the exact same
Driver-for-IN/Receiver-for-OUT boundary-facing convention already
established for top-level ports elsewhere in this app (see
power_resolution_cache.py's own `_resolve_ordinary_endpoint()`). The
locked combo is never merely visually disabled -- it structurally never
appears in `self.assignments` for that row at all (see `_driver_combos`/
`_receiver_combos` below: a row contributes to EXACTLY ONE of the two
dicts, never both), so there is no risk of a stray locked-column value
leaking into the submitted data.

**INOUT** is not addressed by the locking rule above (the spec only
ever defines IN/OUT) -- an assumption, not a guess left silent: both
combos stay editable and are kept MIRRORED to the exact same underlying
value (`_mirror_combos()`), since `PortAttributeDiagramState.assignments`
is still one Supply Set id per row/key, not a genuinely independent
pair -- inventing a second, per-direction value here would be a bigger,
unrequested data-model change. Driver is the authoritative combo
`self.assignments` reads an INOUT row's value from; Receiver is a pure
mirror.

**Filter bar** (bug fix -- present in the original spec, dropped in
the initial build): a `TableFilterBar` (`shared/widgets/table_filter.py`,
via `wrap_with_filter()`) sits directly above the table -- the EXACT
SAME widget/matching logic (`wildcard_match()`, glob-style, no wildcard
character required for an exact match) every other Properties table
filter already uses, not a bespoke reimplementation. Wildcard rows
filter live as the user types; a multi-bit port's own ungrouped rows
(e.g. "d[0]".."d[31]") all match a pattern like "d*" the same way any
other row-level text match does -- there is nothing bit-wise-specific
about it, since filtering only ever looks at each row's own Port Name/
Direction text, never which port a row's key happens to belong to.

"Create Supply Set..." remains a single, SHARED button (not one per
row) -- a newly-created Supply Set has no natural "which port/bit"
association at creation time, so it's made selectable from EVERY
editable combo's own dropdown instead of being auto-assigned to
whichever row happened to trigger it (same "offer, don't force"
precedent _SupplyNameCombo.offer_name() already establishes for supply
names). Reuses CreateSupplySetDialog exactly as-is (same "do NOT build
a new one" precedent every other placement tool here follows) with
`primary_available=False` -- Port Attribute has no Primary/Secondary
concept at all, so its own "Set as Primary" checkbox is simply left
permanently disabled/inert here rather than forking a third variant of
the Supply Set creation dialog.
"""

from __future__ import annotations

from typing import Optional
from uuid import uuid4

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHeaderView,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from frontend.src.power_intent_view.create_power_domain_dialog import CreateSupplySetDialog
from frontend.src.power_intent_view.port_attribute_overlay import port_attribute_row_keys
from frontend.src.power_intent_view.supply_port_overlay import SupplyPortState
from frontend.src.power_intent_view.power_domain_overlay import (
    PowerDomainPlacement,
    SupplySetPlacement,
    supply_set_name_used_by_another_domain,
)
from frontend.src.shared.widgets import message_box
from frontend.src.shared.models.rtl import Port, PortDirection
from frontend.src.shared.widgets.table_filter import TableFilterBar, wrap_with_filter
from frontend.src.shared.widgets.target_hierarchy_label import APPLIES_TO_CAPTION, make_target_hierarchy_label

_DIRECTION_DISPLAY = {
    PortDirection.IN: "IN",
    PortDirection.OUT: "OUT",
    PortDirection.INOUT: "INOUT",
}

_COLUMN_HEADERS = ["Port Name", "Direction", "Receiver Supply Set", "Driver Supply Set"]


class PortAttributeDialog(QDialog):
    def __init__(
        self,
        ports: list[Port],
        existing_supply_sets: dict[str, SupplySetPlacement],
        available_supply_names: list[str],
        target_path: str,
        initial: Optional[dict[str, Optional[str]]] = None,
        all_domains: Optional[dict[str, PowerDomainPlacement]] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.setSizeGripEnabled(True)  # explicit request (Bug 5): every dialog here must be user-resizable
        self.setWindowTitle("Port Attributes")
        self._existing_supply_sets = dict(existing_supply_sets)
        self.new_supply_sets: dict[str, SupplySetPlacement] = {}
        self.new_supply_ports: dict[str, QColor] = {}
        # Bug fix (explicit report, same root cause/fix as
        # CreatePowerDomainDialog's own new_supply_port_states): tracked
        # alongside new_supply_ports, same "accumulate live via
        # CreateSupplySetDialog.new_port_states, prune to referenced
        # names at accept()" lifecycle.
        self.new_supply_port_states: dict[str, list[SupplyPortState]] = {}
        self._available_supply_names = list(available_supply_names)
        self._target_path = target_path
        self._global_domains = dict(all_domains) if all_domains else {}
        self._ports = list(ports)
        # One (key, direction) per ROW -- a single-bit port contributes
        # its own bare name once; a multi-bit port contributes one
        # "<name>[i]" row per bit (see port_attribute_row_keys()'s own
        # docstring for the full bit-wise-ungrouping rationale).
        self._row_specs: list[tuple[str, PortDirection]] = [
            (key, port.direction) for port in self._ports for key in port_attribute_row_keys(port)
        ]
        # A row contributes to EXACTLY ONE of these two dicts (IN ->
        # driver only, OUT -> receiver only, INOUT -> driver, mirrored
        # into the receiver combo below) -- see this class's own module
        # docstring for the full direction-locking rationale.
        self._driver_combos: dict[str, QComboBox] = {}
        self._receiver_combos: dict[str, QComboBox] = {}
        # Every combo that is genuinely user-editable (i.e. NOT locked
        # empty/disabled) -- refreshed together whenever "Create Supply
        # Set..." adds a new one, regardless of which of the two dicts
        # above (if either) it ends up feeding.
        self._editable_combos: list[QComboBox] = []

        initial = initial or {}

        applies_to_form = QFormLayout()
        applies_to_form.addRow(APPLIES_TO_CAPTION, make_target_hierarchy_label([target_path]))

        self.table = QTableWidget(len(self._row_specs), len(_COLUMN_HEADERS))
        self.table.setHorizontalHeaderLabels(_COLUMN_HEADERS)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.verticalHeader().setVisible(False)

        for row, (key, direction) in enumerate(self._row_specs):
            self.table.setItem(row, 0, QTableWidgetItem(key))
            direction_item = QTableWidgetItem(_DIRECTION_DISPLAY.get(direction, direction.name))
            direction_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 1, direction_item)

            receiver_combo = QComboBox()
            driver_combo = QComboBox()
            receiver_locked = direction == PortDirection.IN
            driver_locked = direction == PortDirection.OUT
            current = initial.get(key)

            if receiver_locked:
                receiver_combo.addItem("", None)
                receiver_combo.setEnabled(False)
            else:
                self._refresh_combo(receiver_combo)
                self._editable_combos.append(receiver_combo)
            if driver_locked:
                driver_combo.addItem("", None)
                driver_combo.setEnabled(False)
            else:
                self._refresh_combo(driver_combo)
                self._editable_combos.append(driver_combo)

            if receiver_locked:
                # IN -- only Driver is the boundary-facing/editable cell.
                index = driver_combo.findData(current) if current is not None else 0
                driver_combo.setCurrentIndex(index if index >= 0 else 0)
                self._driver_combos[key] = driver_combo
            elif driver_locked:
                # OUT -- only Receiver is the boundary-facing/editable cell.
                index = receiver_combo.findData(current) if current is not None else 0
                receiver_combo.setCurrentIndex(index if index >= 0 else 0)
                self._receiver_combos[key] = receiver_combo
            else:
                # INOUT -- see this class's own module docstring: both
                # stay editable and mirrored to the SAME single value.
                index = driver_combo.findData(current) if current is not None else 0
                driver_combo.setCurrentIndex(index if index >= 0 else 0)
                self._copy_selection(driver_combo, receiver_combo)
                self._driver_combos[key] = driver_combo
                self._mirror_combos(driver_combo, receiver_combo)

            self.table.setCellWidget(row, 2, receiver_combo)
            self.table.setCellWidget(row, 3, driver_combo)

        # Real, confirmed omission (bug report): the original spec called
        # for a filter above the table, dropped in the initial build.
        # Reuses the EXACT same widget/matching logic every other
        # Properties table filter already uses (TableFilterBar/
        # wrap_with_filter, shared/widgets/table_filter.py) -- not a
        # bespoke reimplementation. Its own "Apply to" column picker
        # defaults to "All Columns", same as every other caller -- since
        # Receiver/Driver Supply Set are QComboBox CELL WIDGETS
        # (setCellWidget()), not QTableWidgetItems, TableFilterBar's own
        # `item(row, col)` lookup finds nothing there regardless of which
        # column is selected, so in practice this already filters by
        # Port Name (and Direction) live as the user types, exactly as
        # required, with no extra wiring needed.
        self.table_filter: TableFilterBar
        table_container, self.table_filter = wrap_with_filter(self.table, example="data_in*")

        self.create_supply_set_button = QPushButton("Create Supply Set...")
        self.create_supply_set_button.clicked.connect(self._prompt_create_supply_set)

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)

        outer = QVBoxLayout(self)
        outer.addLayout(applies_to_form)
        outer.addWidget(table_container)
        outer.addWidget(self.create_supply_set_button)
        outer.addWidget(self.buttons)
        self.resize(640, 420)

    # --- Supply Set picker -------------------------------------------------

    def _all_supply_sets(self) -> dict[str, SupplySetPlacement]:
        combined = dict(self._existing_supply_sets)
        combined.update(self.new_supply_sets)
        return combined

    def _refresh_combo(self, combo: QComboBox) -> None:
        previous = combo.currentData() if combo.count() else None
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("", None)
        for set_id, supply_set in sorted(self._all_supply_sets().items(), key=lambda kv: kv[1].name):
            combo.addItem(supply_set.name, set_id)
        if previous is not None:
            index = combo.findData(previous)
            combo.setCurrentIndex(index if index >= 0 else 0)
        combo.blockSignals(False)

    @staticmethod
    def _copy_selection(source: QComboBox, target: QComboBox) -> None:
        data = source.currentData()
        index = target.findData(data) if data is not None else 0
        target.blockSignals(True)
        target.setCurrentIndex(index if index >= 0 else 0)
        target.blockSignals(False)

    def _mirror_combos(self, a: QComboBox, b: QComboBox) -> None:
        """Keeps `a`/`b` selecting the SAME Supply Set in both
        directions -- `blockSignals()` inside `_copy_selection()` is what
        stops this from recursing forever (the programmatic update on the
        target never re-fires its own `currentIndexChanged`)."""
        a.currentIndexChanged.connect(lambda _index: self._copy_selection(a, b))
        b.currentIndexChanged.connect(lambda _index: self._copy_selection(b, a))

    def _prompt_create_supply_set(self) -> None:
        # Bug fix: the supply materialized from this inline "Create Supply
        # Set..." flow becomes an Internal Supply the moment `self.
        # _target_path` isn't the design's true top-level block -- the
        # nested AddPowerSupplyDialog must not offer an editable Power
        # States section in that case.
        dialog = CreateSupplySetDialog(
            self._available_supply_names, self, primary_available=False, is_internal_supply="/" in self._target_path, target_path=self._target_path
        )
        while dialog.exec() == QDialog.DialogCode.Accepted:
            if supply_set_name_used_by_another_domain(dialog.name, self._global_domains, self._all_supply_sets()):
                message_box.warning(
                    self,
                    "Duplicate Supply Set Name",
                    f"A Supply Set named '{dialog.name}' is already used by another Power Domain. Choose a different name.",
                )
                continue
            # Bug fix (explicit policy correction): the pin-combination
            # duplicate check is intentionally NOT applied here at all --
            # its whole purpose is avoiding an ambiguous tie between the
            # SAME Power Domain's own Primary/Secondary Supply Sets (see
            # create_power_domain_dialog.py's own module docstring), but
            # a Supply Set created via this dialog's own inline flow
            # isn't assigned as any domain's Primary/Secondary at all --
            # it's just added to the shared pool, to be associated with
            # ports independently. Checking it against the whole design
            # here was the same over-broad mistake this bug fixes
            # elsewhere, just via a different entry point with no
            # domain scope to narrow it to in the first place.
            supply_set = SupplySetPlacement(
                id=uuid4().hex,
                name=dialog.name,
                power=dialog.power,
                ground=dialog.ground,
                n_well=dialog.n_well,
                p_well=dialog.p_well,
                owner_path=self._target_path,
            )
            self.new_supply_sets[supply_set.id] = supply_set
            self.new_supply_ports.update(dialog.new_port_colors)
            self.new_supply_port_states.update(dialog.new_port_states)
            self._available_supply_names = sorted(set(self._available_supply_names) | set(dialog.new_port_colors))
            for combo in self._editable_combos:
                self._refresh_combo(combo)
            return

    # --- accept -----------------------------------------------------------

    def accept(self) -> None:
        self._discard_unused_new_entities()
        super().accept()

    def _discard_unused_new_entities(self) -> None:
        """Bug fix (explicit request, same architectural gap found and
        fixed in CreatePowerDomainDialog/RetentionStrategyDialog -- see
        CreatePowerDomainDialog's own `_discard_unused_new_entities()`
        docstring for the full reasoning): a Supply Set created via
        "Create Supply Set..." and never actually assigned to any row
        (or assigned, then reassigned away to something else) before
        this outer OK must never be committed -- unlike the other two
        dialogs, there's no single Primary/Retention slot here, so
        "referenced" means "assigned to at least one row's own
        Driver/Receiver combo" (`self.assignments`, read BEFORE this
        dialog closes -- both combo dicts stay live at accept() time).
        Prunes `self.new_supply_sets` down to only the id(s) actually
        assigned anywhere right now, then `self.new_supply_ports` down
        to only the names actually referenced by one of those retained
        sets."""
        referenced_ids = {sid for sid in self.assignments.values() if sid}
        self.new_supply_sets = {sid: s for sid, s in self.new_supply_sets.items() if sid in referenced_ids}
        referenced_names = {n for s in self.new_supply_sets.values() for n in (s.power, s.ground, s.n_well, s.p_well) if n}
        self.new_supply_ports = {name: color for name, color in self.new_supply_ports.items() if name in referenced_names}
        self.new_supply_port_states = {name: states for name, states in self.new_supply_port_states.items() if name in referenced_names}

    # --- accepted values -----------------------------------------------

    @property
    def assignments(self) -> dict[str, Optional[str]]:
        result: dict[str, Optional[str]] = {}
        for key, combo in self._driver_combos.items():
            result[key] = combo.currentData()
        for key, combo in self._receiver_combos.items():
            result[key] = combo.currentData()
        return result
