"""Power Intent tab's Properties group box: one QTableWidget per
construct kind, read-only this pass (no add/update/remove -- that's the
dedicated interactive-editing pass, deferred per the approved scope).

DomainSupplyConnection (a real 2c construct with no tab of its own in
the originally-specified seven) is folded into its closest relative tab
as a secondary table within Power Domains (see
docs/frontend_milestone2_power_intent_visualization.md). The standalone
"Power States" tab (which used to show the real PowerIntentDesign's own
PowerState/PowerStateGroup constructs) was REMOVED (explicit request,
superseded by the new diagram-only "Power State Table" top-level tab --
see power_state_table.py/power_state_table_tab.py's own module
docstrings) -- neither construct is shown anywhere in this panel
anymore.

"Supply Ports" and "Supply Sets" are two separate tabs (explicit
request -- an earlier version combined them into one "Supply Ports/Nets"
tab with a Kind column folding Sets in too; Supply Sets now get their
own tab with dedicated Power/Ground/N-Well/P-Well columns instead of a
single squeezed-together string).

Two independent data sources feed these tables, tracked separately
(`self._power_intent`/`self._supply_state`/`self._domain_state`) and
merged together on every _rebuild():
  - `load(power_intent)` -- the real, loaded PowerIntentDesign (Load
    Power Intent's own output). Drives every tab as it always has.
  - `refresh_diagram_overlay(supply_state, domain_state)` -- the
    Power Diagram's own diagram-only mock state (Add Power Supply/
    Create Power Domain -- see supply_port_overlay.py/
    power_domain_overlay.py's own module docstrings for why these are
    diagram-only, never touching a real PowerIntentDesign). Adds rows
    to "Supply Ports"/"Supply Sets"/"Power Domains" alongside the real
    ones, distinguished by a "Port"/"Net"/"Supply Port"/"Macro PG"/
    "Macro PG - Internal" Type value where the table has one at all.
    Called by power_intent_tab.py every time PowerIntentDiagramView's
    own `diagramStateChanged` signal fires (see that class's own
    docstring for exactly when).
  - Either source can be missing (`None`) -- e.g. diagram edits made
    before Load Power Intent has ever run, or a real design with no
    diagram annotations at all yet. _rebuild() tolerates both.

"Linked to the hierarchy where they were created" (explicit request):
a diagram-only row's own Name column is prefixed with its owner
hierarchy, via _hierarchy_prefixed_name() -- except when defined
directly on the design's own root instance ("top level"), which shows
as a plain name with no prefix. A Supply Set's own 4 field values
(Power/Ground/N-Well/P-Well, themselves just supply *names* -- see
SupplySetPlacement's own docstring) get the identical treatment, using
that Supply Set's own owner_path.

**No dedicated "Scope" column anywhere anymore** (explicit request,
removed from Power Domains/Supply Ports/Supply Sets): every table here
is instead always ordered "top scope to deeper one" (`_depth()`, the
primary sort key everywhere it's used -- real per-column click-to-sort,
see `_make_table()`, still overrides this until the table is next
rebuilt), and Power Domains folds scope directly into its own
"Elements" column instead (`_format_scope()` -- "." at the design
root). See `_load_domains()`/`_load_supply_ports()`/`_load_supply_sets()`'s
own docstrings/comments for the full column-by-column reasoning.

**"PortList"/"Sequential Elements" (explicit request, moved here from
the now-hidden Visualize Design tab -- see main_window.py's own module
docstring) are this panel's own first two top-level tabs**, ahead of
every construct-kind tab above -- promoted individually, not grouped
under one "Design Properties" container tab (an earlier version of this
move did group them that way; tightened on a direct follow-up request).
Still built via `HierarchyPropertiesTableWidget`
(`hierarchy_view/properties_table_widget.py`, unchanged, same class
Visualize Design's own copy still uses) bound to a *second*, independent
`HierarchySelectionController` (`hierarchy_controller` -- distinct from
`controller`/`PowerIntentSelectionController` above, which drives every
*other* tab here) -- `power_intent_tab.py` passes its own already-
existing controller (the one its Hierarchy Tree/Diagram already share),
so clicking a node there drives these two tabs too, the same way
Visualize Design's own Tree used to drive its own copy. `self.
design_properties` (the `HierarchyPropertiesTableWidget` instance) is
never itself shown -- only its own `._port_panel`/`._sequential_panel`
are, `addTab()`'d directly onto this panel's own `_tabs`, reparenting
them away from `design_properties`'s own (display-less) shell; `self.
design_properties` stays alive purely so `load_design()` and the two
panels' own internal wiring keep working through it. Fed via
`load_design()`, independent of `load()`/`refresh_diagram_overlay()`
above (RTL-shaped data, not power-intent-shaped). `hierarchy_controller`
defaults to a private, unshared `HierarchySelectionController()` when
omitted (every existing caller/test that never cared about hierarchy-
node selection at all) rather than being a required parameter.
"""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QPalette
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QLabel,
    QListWidget,
    QMenu,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from frontend.src.hierarchy_view.properties_table_widget import ConnectivityCellDisplay, HierarchyPropertiesTableWidget
from frontend.src.hierarchy_view.selection_controller import HierarchySelectionController
from frontend.src.power_intent_view.isolation_overlay import IsolationDiagramState
from frontend.src.power_intent_view.level_shifter_overlay import LevelShifterDiagramState
from frontend.src.power_intent_view.port_attribute_overlay import PortAttributeDiagramState
from frontend.src.power_intent_view.power_domain_overlay import PowerDomainDiagramState, PowerDomainPlacement
from frontend.src.power_intent_view.power_resolution_cache import PowerResolutionCache, StructuralTraceCache, build_structural_cache, resolve_all
from frontend.src.power_intent_view.power_switch_overlay import PowerSwitchDiagramState, power_switch_connector_key, resolve_effective_states
from frontend.src.power_intent_view.retention_overlay import RetentionDiagramState
from frontend.src.power_intent_view.selection_controller import PowerIntentSelectionController
from frontend.src.power_intent_view.supply_port_overlay import state_label, SupplyPortDiagramState, connected_supply_port
from frontend.src.shared.models.power_intent import PowerIntentDesign
from frontend.src.shared.models.rtl import Design
from frontend.src.shared.widgets.table_filter import TableFilterBar, wrap_with_filter
from frontend.src.shared.widgets.shrinkable_tabs import allow_narrow_resize

_ISOLATION_CLAMP_LABELS = {"low": "Low", "high": "High", "keep": "Keep"}
_ISOLATION_SENSE_LABELS = {"low": "Low", "high": "High"}
_LEVEL_SHIFTER_APPLIES_TO_LABELS = {"inputs": "Inputs", "outputs": "Outputs", "both": "Both", "elements": "Elements"}
_LEVEL_SHIFTER_RULE_LABELS = {"low_to_high": "Low to High", "high_to_low": "High to Low", "both": "Both"}

# Explicit request: Edit/Remove from Properties table rows, exactly
# these 7 entity types. The remaining secondary table (Domain-Supply
# Connections) is deliberately excluded -- no edit/delete backend exists
# for it, and it was never asked for here. "PowerSwitchCell"/
# "LevelShifterCell" (the removed reusable-library-entity kinds) are
# gone entirely -- see power_switch_overlay.py/level_shifter_overlay.py's
# own history.
_EDITABLE_KINDS = {
    "PowerDomain",
    "SupplyPort",
    "SupplySet",
    "PowerSwitch",
    "IsolationStrategy",
    "RetentionStrategy",
    "LevelShifterStrategy",
}


def _hierarchy_prefixed_name(name: str, path: str) -> str:
    """`name` prefixed with `path`'s hierarchy, minus its own root
    segment -- e.g. path="top/u_dut" -> "u_dut/{name}"; path="top" (the
    design's own root instance -- "top level", explicit request: "no
    need hierarchy simply the name") -> just `name`, unprefixed."""
    parts = path.split("/") if path else []
    if len(parts) <= 1:
        return name
    return "/".join(parts[1:]) + "/" + name


def _relative_path(owner_path: str) -> str:
    """Strips a diagram-only owner_path's own root instance segment --
    "top/u_core" -> "u_core"; "top" (root level) -> "" -- same
    convention as _hierarchy_prefixed_name() above, applied to a bare
    path instead of a name. Puts a diagram-only owner_path into the same
    root-relative shape a real PowerIntentDesign's own `.scope` field is
    always already in (see _format_scope()/_depth() below, both meant to
    apply uniformly across real and diagram-only rows once normalized
    this way)."""
    parts = owner_path.split("/") if owner_path else []
    return "/".join(parts[1:])


def _format_scope(scope: str) -> str:
    """"." for the design's own root scope (real PowerIntentDesign's
    `.scope == ""`, or a diagram-only owner_path already run through
    _relative_path() above), the bare scope string otherwise."""
    return scope if scope else "."


# Column indices into the Supply Ports table
# (["Type", "Name", "Direction", "Connected", "States"]), named rather
# than inline: adding "Direction" between Name and Connected shifted both
# by one, and the two call sites that index them directly -- the
# "Floating" italic and the States cell widget -- had the old positions
# hardcoded.
_SUPPLY_PORT_CONNECTED_COLUMN = 3
_SUPPLY_PORT_STATES_COLUMN = 4
# Named for the same reason as the two above: inserting a column shifts
# every later index, and a bare literal here is what silently breaks the
# cell widget's placement when that happens.
_RETENTION_ELEMENTS_COLUMN = 7
# "Applies To All" was inserted before Elements, shifting it from 9.
_ISOLATION_ELEMENTS_COLUMN = 10


def _depth(scope: str) -> int:
    """Hierarchy depth of an already-root-relative scope string (see
    _relative_path()/PowerIntentDesign's own `.scope`) -- 0 at the
    design root, +1 per "/" segment. Drives "keep the table organized
    always from top scope to deeper one" (explicit request) as the
    primary sort key on every tab it's used for."""
    return 0 if not scope else scope.count("/") + 1


def _prefixed_or_dash(value: Optional[str], owner_path: str) -> str:
    """A Supply Set field value (a supply *name*, e.g. "VDD") with the
    same hierarchy-prefix treatment as _hierarchy_prefixed_name(), or
    "-" if the field itself was never set."""
    return _hierarchy_prefixed_name(value, owner_path) if value else "-"


def _connected_master_name(pg_id: str, supply_state: SupplyPortDiagramState) -> Optional[str]:
    """The plain (non-fixed) supply port `pg_id` (a macro PG pin) is
    wired to via a drawn connector, if any -- display-formatted (hierarchy-
    prefixed). None (rendered "Floating", in italics) when no such
    connector exists. Built on connected_supply_port() (supply_port_overlay.py),
    the shared bare-placement-returning core also used by
    power_resolution_cache.py's own macro power-net Supply Set resolution."""
    master = connected_supply_port(pg_id, supply_state)
    return _hierarchy_prefixed_name(master.name, master.owner_path) if master is not None else None


def _domain_is_nested_in(inner: PowerDomainPlacement, outer: PowerDomainPlacement) -> bool:
    """True if ANY of `inner`'s own owner_paths falls inside ANY of
    `outer`'s own owner_paths' subtrees -- same helper as
    power_intent_text_export.py's own copy (duplicated, not shared, per
    this module's own established precedent below), generalizing the
    single-owner_path nesting check for a multi-hierarchy domain
    (explicit request)."""
    return any(
        inner_path == outer_path or inner_path.startswith(f"{outer_path}/") for inner_path in inner.owner_paths for outer_path in outer.owner_paths
    )


def _nested_owner_paths(inner: PowerDomainPlacement, outer: PowerDomainPlacement) -> list[str]:
    """The subset of `inner`'s own owner_paths that actually fall
    inside `outer`'s own subtree(s) -- a multi-hierarchy `inner` domain
    could in principle have SOME members nested under `outer` and
    others elsewhere; only the nested ones belong in `outer`'s own
    "Exclude Elements" display."""
    return [p for p in inner.owner_paths if any(p == op or p.startswith(f"{op}/") for op in outer.owner_paths)]


def _direct_excluded_children(domain: PowerDomainPlacement, all_domains: list[PowerDomainPlacement]) -> list[PowerDomainPlacement]:
    """Domains nested inside `domain`'s own subtree(s), minus any that
    are themselves nested inside another, *closer* nested domain
    (already excluded transitively by that domain's own entry here) --
    mirrors power_domain_overlay.py's own apply_domain_coloring()
    cascade ("a new domain's own coloring excludes hierarchies which
    already have a power domain defined", deepest/most-specific always
    wins), surfaced here as an explicit list rather than a rendering
    side effect."""
    descendants = [d for d in all_domains if d.id != domain.id and _domain_is_nested_in(d, domain)]
    return [d for d in descendants if not any(other.id != d.id and _domain_is_nested_in(d, other) for other in descendants)]


class _EscClearsSelectionTable(QTableWidget):
    """Explicit request: pressing Escape while a row is selected clears
    that selection -- the same "Esc cancels/clears the current state"
    principle the diagram's own Escape handling already establishes
    (_PowerIntentGraphicsView.keyPressEvent()), extended here to
    Properties table row selection. No realistic conflict with the
    diagram's own Escape handling: that lives in a completely separate
    widget hierarchy and only ever fires when the DIAGRAM itself has
    keyboard focus, exactly like any other widget's own key handling --
    a Properties table only ever sees this Escape when IT has focus."""

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape and self.selectionModel() is not None and self.selectionModel().hasSelection():
            self.clearSelection()
            event.accept()
            return
        super().keyPressEvent(event)


def _make_table(headers: list[str]) -> QTableWidget:
    table = _EscClearsSelectionTable(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    bold = QFont()
    bold.setBold(True)
    table.horizontalHeader().setFont(bold)
    # Explicit request: click-to-sort on every table here. Left enabled
    # permanently except while _rebuild() repopulates a table's rows (see
    # _rebuild()'s own comment) -- setItem() calls while sorting is live
    # can re-sort mid-populate and scatter rows across the wrong indices.
    table.setSortingEnabled(True)
    # Auto-size every column to its content (same convention already
    # established for PortList/Sequential Elements in
    # hierarchy_view/properties_table_widget.py's own _make_table()) --
    # ResizeToContents is a persistent mode, not a one-shot call, so it
    # keeps re-fitting columns on every _rebuild() row change for free.
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    # Multi-hierarchy Power Domain/Supply Set rows (explicit request):
    # a cell can now hold several newline-joined lines (one per member
    # hierarchy -- see _load_domains()/_load_supply_sets()'s own
    # docstrings). Word wrap on + resizeRowsToContents() after every
    # _rebuild() (see that method's own loop) is what actually makes
    # Qt's default item delegate render each embedded "\n" as its own
    # line instead of squashing the cell to single-line height.
    table.setWordWrap(True)
    return table


def _format_supply_state(state) -> str:
    """One "States" column line. Delegates to the shared
    `state_label()`: states no longer carry an authored name, so the
    Type is the label and a levelled state shows its voltage."""
    return state_label(state)


def _set_row(table: QTableWidget, row: int, values: list[str], construct_id: str) -> None:
    for col, value in enumerate(values):
        item = QTableWidgetItem(value)
        if col == 0:
            item.setData(Qt.ItemDataRole.UserRole, construct_id)
        table.setItem(row, col, item)


class _ElementsCellWidget(QWidget):
    """A Properties-table "Elements" cell: shows up to 10 elements, one
    per line, plus a "Show all (N)" button that reveals the complete
    list when there are more. Shared, identical widget for BOTH
    Isolation's and Retention's own Properties-table Elements cell
    (explicit request: build once, reuse -- not two separate
    implementations); does NOT touch either dialog's own Elements list
    (Isolation's Elements table, Retention's own QListWidget) -- this
    is the read-only Properties-table summary cell only. A
    `setCellWidget()` cell rather than a plain item -- the only way to
    embed an interactive button inside a QTableWidget cell. Collapsing
    back is not offered (explicit request: "not required if it
    complicates the implementation") -- expand is one-way.

    Bug fix (explicit request, verified empirically): an earlier
    version just grew the SAME QLabel's own text to the full list on
    expand -- since the table row's own height was already fixed via
    resizeRowsToContents() at POPULATION time (before "Show all" was
    ever clicked), a label growing from 10 lines to N lines AFTER that
    had nothing telling the row to grow with it, so the newly-revealed
    lines simply rendered past the cell's already-fixed bounds
    (visually clipped/overlapping the row below) rather than actually
    being shown. Fixed per the requested approach: "Show all" now
    swaps in a bounded-height QListWidget (an ordinary
    QAbstractScrollArea, so it grows its own internal scrollbar
    automatically once content exceeds `_EXPANDED_MAX_HEIGHT`) instead
    of trying to grow the row to fit an unbounded list -- the row
    itself only ever needs to accommodate this one fixed height,
    resized once via `on_expand` (a callback into the owning table,
    since this widget has no reference to its own table/row and stays
    directly constructible/testable on its own).

    Bug fix (explicit request): a plain QTableWidgetItem automatically
    switches its own text to the palette's HighlightedText role when its
    row is selected -- built into Qt's own item-view painting. A cell
    WIDGET (set via setCellWidget(), which is what this class always is)
    is a plain child widget sitting in the viewport, entirely outside
    that item-painting system, so its own QLabel never picked up the
    "selected row" text colour on its own -- confirmed empirically (it
    stayed the default/black text colour while every other, plain-item
    column in the same selected row correctly went white). Fixed by
    optionally taking `table`/`row` (every real call site now passes
    both) and reacting to the table's own itemSelectionChanged signal,
    switching the label's own text colour between the palette's
    Text/HighlightedText roles to match -- the exact same "logic" that
    already colours every other column, just applied here explicitly
    since this widget sits outside the mechanism that does it for free
    everywhere else."""

    _MAX_VISIBLE = 10
    _EXPANDED_MAX_HEIGHT = 160

    def __init__(
        self,
        elements: list[str],
        on_expand: Optional[Callable[[], None]] = None,
        parent: Optional[QWidget] = None,
        table: Optional[QTableWidget] = None,
        row: Optional[int] = None,
    ):
        super().__init__(parent)
        self._elements = list(elements)
        self._on_expand = on_expand
        self._table = table
        self._row = row
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)
        self._label = QLabel("\n".join(self._elements[: self._MAX_VISIBLE]))
        layout.addWidget(self._label)
        self._list_widget: Optional[QListWidget] = None
        if table is not None:
            table.itemSelectionChanged.connect(self._update_selection_style)
            self._update_selection_style()
        if len(self._elements) > self._MAX_VISIBLE:
            self._show_all_button = QPushButton(f"Show all ({len(self._elements)})")
            self._show_all_button.setFlat(True)
            self._show_all_button.clicked.connect(self._expand)
            layout.addWidget(self._show_all_button)
        else:
            self._show_all_button = None

    def _expand(self) -> None:
        self._label.hide()
        self._show_all_button.hide()
        self._list_widget = QListWidget()
        self._list_widget.addItems(self._elements)
        self._list_widget.setMaximumHeight(self._EXPANDED_MAX_HEIGHT)
        self.layout().addWidget(self._list_widget)
        if self._on_expand is not None:
            self._on_expand()

    def _update_selection_style(self) -> None:
        if self._table is None or self._row is None:
            return
        item = self._table.item(self._row, 0)
        selected = item is not None and item.isSelected()
        role = QPalette.ColorRole.HighlightedText if selected else QPalette.ColorRole.Text
        color = self._table.palette().color(role).name()
        self._label.setStyleSheet(f"color: {color};")


class PowerIntentPropertiesPanel(QWidget):
    """Item (Edit/Remove from Properties table rows, explicit request):
    this panel itself has no diagram/view context (owner_box, live
    icons, dialogs) -- it only ever renders rows from plain state. Left-
    click-select + right-click "Edit"/"Remove" is wired here as a
    GENERIC mechanism (one context-menu handler for every table, keyed
    by construct kind), but ACTING on a choice is delegated entirely to
    whoever owns both this panel and the diagram view
    (PowerIntentTab) -- `editRequested`/`removeRequested` fire
    (kind, construct_id) and the owner dispatches to the view's own
    edit_properties_row()/remove_properties_row() (power_intent_diagram_view.py),
    which reuses the EXACT SAME dialogs/cascade-delete logic the
    diagram's own right-click-on-icon flow already uses, per explicit
    request -- a second entry point, not a parallel implementation.
    `set_action_enablement_hooks()` lets that same owner say whether a
    specific row's Edit/Remove should be enabled at all (e.g. a macro
    PG pin's own Supply Port row, or a real-backend-only row with no
    diagram-only counterpart) -- defaults to "always enabled" so a
    caller that never calls it (e.g. most existing tests) keeps working
    unmodified."""

    editRequested = Signal(str, str)  # kind, construct_id
    removeRequested = Signal(str, str)

    def __init__(
        self,
        controller: PowerIntentSelectionController,
        hierarchy_controller: Optional[HierarchySelectionController] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self._controller = controller
        self._can_edit_row: Callable[[str, str], bool] = lambda kind, construct_id: True
        self._can_remove_row: Callable[[str, str], bool] = lambda kind, construct_id: True
        self._kind_for_table: dict[QTableWidget, str] = {}
        self._power_intent: Optional[PowerIntentDesign] = None
        self._supply_state: Optional[SupplyPortDiagramState] = None
        self._domain_state: Optional[PowerDomainDiagramState] = None
        self._retention_state: Optional[RetentionDiagramState] = None
        self._power_switch_state: Optional[PowerSwitchDiagramState] = None
        self._isolation_state: Optional[IsolationDiagramState] = None
        self._level_shifter_state: Optional[LevelShifterDiagramState] = None
        self._port_attribute_state: Optional[PortAttributeDiagramState] = None
        # Two-layer connectivity cache behind PortList's own "Receiver
        # Supply Set"/"Driver Supply Set" columns -- see
        # power_resolution_cache.py's own module docstring. Layer 1
        # (_structural_cache) is rebuilt only in load_design() (a new
        # RTL design); Layer 2 (_resolution_cache) is invalidated and
        # its plain-data view re-pushed on every refresh_diagram_overlay()
        # (Power Domain/Supply Set/Port Attribute edit).
        self._structural_cache: Optional[StructuralTraceCache] = None
        self._resolution_cache = PowerResolutionCache()

        # Explicit request: the tab bar reads flush-left (indented from
        # the group's own left edge), not centered/stretched across the
        # panel's own full width -- QTabBar's default `expanding` mode
        # stretches every tab to fill available width, which reads as
        # visually centered on a wide panel; turning it off leaves tabs
        # their own natural (content-driven) width, left to right.
        #
        # allow_narrow_resize() additionally forces scroll buttons on.
        # Without it this dock cannot be dragged narrower than all NINE
        # tabs laid end to end on the macOS style -- see that helper's
        # own docstring for the measurements.
        self._tabs = allow_narrow_resize(QTabWidget())
        self._tables: dict[str, QTableWidget] = {}
        self._kind_for_tab: dict[int, str] = {}
        # Explicit request: every table here gets its own filter bar
        # (frontend/src/shared/widgets/table_filter.py) -- re-applied
        # after every _rebuild() below, since repopulating a table's
        # rows doesn't reliably preserve which ones a filter had hidden.
        self._filter_bars: list[TableFilterBar] = []

        # "PortList"/"Sequential Elements" (explicit request: promoted to
        # this panel's own top-level tabs, no longer grouped under one
        # "Design Properties" container tab) -- still built via
        # HierarchyPropertiesTableWidget (moved here from the now-hidden
        # Visualize Design tab originally, see module docstring), but
        # `self.design_properties` itself is never shown anywhere now --
        # only its own two child panels (`._port_panel`/
        # `._sequential_panel`) are, added directly as this QTabWidget's
        # own first two tabs. addTab() reparents each panel away from
        # `design_properties`'s own (never-displayed) QTabWidget shell,
        # standard Qt "move a page to a different tab widget" behavior --
        # `design_properties` itself stays alive purely as the object
        # `load_design()`/its own internal wiring (filter/apply_selection)
        # still runs through, holding no tabs of its own anymore. Added
        # before the specs loop below so every other tab's own index
        # naturally shifts by two, no separate bookkeeping needed for
        # _kind_for_tab (which only ever maps the *construct-kind* tabs
        # added by that loop).
        self.design_properties = HierarchyPropertiesTableWidget(hierarchy_controller or HierarchySelectionController())
        # Explicit request: renamed ("PortList" -> "Port List",
        # "Sequential Elements" -> "Sequential") and reordered (Port
        # List/Sequential first, then Supply Port/Supply Set BEFORE
        # Power Domain -- previously Power Domain led the specs loop
        # below) -- label text and tab order only, no change to either
        # panel's own content/columns/behavior. See the specs list's own
        # comment for the rest of the reordering/renaming.
        self._tabs.addTab(self.design_properties._port_panel, "Port List")
        self._tabs.addTab(self.design_properties._sequential_panel, "Sequential")

        # Explicit request: renamed ("Power Domains" -> "Power Domain",
        # "Supply Ports" -> "Supply Port", "Supply Sets" -> "Supply Set",
        # "Power Switch Instances" -> "Switch", "Level Shifter Instances"
        # -> "Level Shifter") and reordered to Supply Port/Supply
        # Set/Power Domain (previously Power Domain led this list,
        # ahead of Supply Port/Supply Set) -- full final tab order:
        # Port List, Sequential, Supply Port, Supply Set, Power Domain,
        # Switch, Isolation, Retention, Level Shifter. Label text and
        # order only -- each tab's own columns/content are unchanged.
        # ("Power States" was the final tab here; REMOVED, explicit
        # request -- see this module's own docstring.)
        #
        # Rework (explicit request, REVERSES the earlier "Cell Type
        # dropdown referencing a reusable library" model): the "Switch
        # Cell"/"Level Shifter Cell" secondary tables/tabs are GONE
        # entirely -- there is no more reusable library entity to show a
        # secondary table of. "Switch"/"Level Shifter" are now plain,
        # single-table tabs again. Cell Type is tracked directly on each
        # instance (PowerSwitchPlacement.cell_type/
        # LevelShifterStrategyPlacement.cell_type, free text) and shown
        # via a plain "Cell Type" column on each instance table (renamed
        # from "Power Switch Cell Type"/"Level Shifter Cell Type" -- no
        # more "Power Switch"/"Level Shifter" prefix, since it no longer
        # references a distinctly-named library entity). Power Switch
        # also gains "Input Supply Port"/"Output Supply Port"/
        # "Control Port" columns directly (previously only visible on
        # the now-removed Switch Cell secondary table).
        specs = [
            ("SupplyPort", "Supply Port", ["Type", "Name", "Direction", "Connected", "States"]),
            ("SupplySet", "Supply Set", ["Name", "Power", "Ground", "N-Well", "P-Well"]),
            ("PowerDomain", "Power Domain", ["Name", "Elements", "Exclude Elements", "Primary Supply", "Secondary Supplies"]),
            (
                "PowerSwitch",
                "Switch",
                [
                    "Name",
                    "Power Domain",
                    "Supply In",
                    "Supply Out",
                    "Input Supply Port",
                    "Output Supply Port",
                    "Control Port",
                    "Cell Type",
                    # The RTL net driving the control pin, as distinct
                    # from "Control Port" above, which is the cell's own
                    # pin. Placed beside the State columns because those
                    # are boolean expressions over it.
                    "Control Signal",
                    "Ack Port",
                    "Ack Signal",
                    "On State",
                    "Off State",
                ],
            ),
            (
                "IsolationStrategy",
                "Isolation",
                [
                    "Name",
                    "Power Domain",
                    "Isolation Supply Set",
                    "Source Supply Set",
                    "Sink Supply Set",
                    "Clamp Value",
                    "Location",
                    "Isolation Control",
                    "Isolation Sense",
                    "Applies To All",
                    "Elements",
                ],
            ),
            # "Save Signal"/"Restore Signal" sit between the supplies and
            # Elements, matching the dialog's own field order. Unlike
            # "Primary Supply" (blank for a real row -- the backend
            # schema has no such field), these two are populated for BOTH
            # sources: the real RetentionStrategy has carried
            # `save_signal`/`restore_signal` all along.
            ("RetentionStrategy", "Retention",
             ["Name", "Scope", "Power Domain", "Primary Supply", "Retention Supply",
              "Save Signal", "Restore Signal", "Elements"]),
            (
                "LevelShifterStrategy",
                "Level Shifter",
                ["Name", "Power Domain", "Applies To All", "Rule", "Location", "Threshold", "Cell Type", "Elements"],
            ),
        ]
        self._domain_connections_table = _make_table(["Domain", "Supply Net", "Role", "Scope"])
        domain_connections_container, domain_connections_filter = wrap_with_filter(self._domain_connections_table)
        self._filter_bars += [domain_connections_filter]
        secondary_tables = {
            "PowerDomain": ("Domain-Supply Connections", domain_connections_container),
        }

        for kind, title, headers in specs:
            table = _make_table(headers)
            table.itemSelectionChanged.connect(lambda k=kind, t=table: self._on_row_selected(k, t))
            self._tables[kind] = table
            if kind in _EDITABLE_KINDS:
                self._wire_row_context_menu(kind, table)
            table_container, table_filter = wrap_with_filter(table)
            self._filter_bars.append(table_filter)

            if kind in secondary_tables:
                secondary_title, secondary_container = secondary_tables[kind]
                container = QWidget()
                container_layout = QVBoxLayout(container)
                container_layout.addWidget(table_container, stretch=2)
                container_layout.addWidget(QLabel(secondary_title))
                container_layout.addWidget(secondary_container, stretch=1)
                index = self._tabs.addTab(container, title)
            else:
                index = self._tabs.addTab(table_container, title)
            self._kind_for_tab[index] = kind

        layout = QVBoxLayout(self)
        layout.addWidget(self._tabs)

        controller.selectionChanged.connect(self.setSelection)

    def load(self, power_intent: PowerIntentDesign) -> None:
        self._power_intent = power_intent
        self._rebuild()

    def load_design(self, design: Design) -> None:
        """Feeds the "Design Properties" tab -- independent of
        load()/refresh_diagram_overlay() above (RTL-shaped data, not
        power-intent-shaped; see this class's own docstring). Also
        (re)builds Layer 1 of the PortList connectivity cache -- the
        expensive structural trace, run once here since RTL structure
        never changes after a design loads (mirrors "run at Elaborate
        time" for the real backend's own flatten_design() call)."""
        self.design_properties.load(design)
        self._structural_cache = build_structural_cache(design)
        self._resolution_cache = PowerResolutionCache()
        self._push_port_connectivity()

    def refresh_diagram_overlay(
        self,
        supply_state: SupplyPortDiagramState,
        domain_state: PowerDomainDiagramState,
        retention_state: Optional[RetentionDiagramState] = None,
        port_attribute_state: Optional[PortAttributeDiagramState] = None,
        power_switch_state: Optional[PowerSwitchDiagramState] = None,
        isolation_state: Optional[IsolationDiagramState] = None,
        level_shifter_state: Optional[LevelShifterDiagramState] = None,
    ) -> None:
        """Called every time PowerIntentDiagramView's own
        `diagramStateChanged` fires -- see this module's own docstring
        and that signal's docstring for the full "why". `retention_state`
        (explicit request: the Retention tab must be "fully populated
        based on the actual Retention strategies... rather than being
        empty/stale/hardcoded") defaults to None so any existing caller
        that only ever cared about supply ports/domains keeps working
        unchanged. `port_attribute_state` (same default-None treatment,
        same reason) feeds PortList's own top-level-port override -- a
        Power Domain/Supply Set/Port Attribute edit only ever invalidates
        Layer 2 of the connectivity cache (cheap to recompute); Layer 1's
        own expensive structural trace is untouched. `power_switch_state`
        (same default-None treatment, same reason): feeds the "Power
        Switches" tab's own diagram-only rows -- previously entirely
        unwired here, that tab only ever showed real PowerIntentDesign
        rows (see _load_switches()'s own docstring). `isolation_state`
        (same default-None treatment, same reason): feeds the
        "Isolation" tab's own diagram-only rows -- same two-source-merge
        precedent _load_retention() already establishes. `level_shifter_state`
        (same default-None treatment, same reason): feeds "Level Shifter
        Instances"/"Level Shifter Cells" -- same two-source-merge
        precedent, plus the SAME "Cells" secondary-table split Power
        Switch's own pair already establishes."""
        self._supply_state = supply_state
        self._domain_state = domain_state
        self._retention_state = retention_state
        self._port_attribute_state = port_attribute_state
        self._power_switch_state = power_switch_state
        self._isolation_state = isolation_state
        self._level_shifter_state = level_shifter_state
        # PERFORMANCE, measured by the 2026-09-11 codebase health audit
        # (category 02) and deliberately NOT changed here.
        #
        # This invalidates Layer 2 wholesale and then re-resolves EVERY
        # driver and receiver endpoint in the design, on every
        # diagramStateChanged -- including edits that cannot affect
        # connectivity at all (measured: attaching a power-intent file to
        # a macro, pure metadata, still triggers a full resolve_all()).
        # On the 6-node mock that is 21 + 21 endpoints; on a real SoC it
        # is O(all ports) per edit. Two further multipliers were measured
        # alongside it: three independent listeners each rebuild
        # wholesale on this same signal, and trace_upstream_source() is
        # explicitly uncached with six live callers.
        #
        # Left alone on purpose. Every fix worth making here (scoping
        # invalidation to the state that actually changed, or making
        # resolution lazy per displayed row) trades a correctness risk --
        # a stale Driver/Receiver cell is silently wrong, not visibly
        # broken -- against a benefit nobody has sized on a real design.
        # Profile first, then choose; don't guess.
        self._resolution_cache.invalidate_all()
        self._push_port_connectivity()
        self._rebuild()

    def resolve_port_connectivity(self, endpoints: list[tuple[str, str]]) -> tuple[dict, dict]:
        """Exposes this panel's own PortList connectivity resolution
        (`resolve_all()`, PowerResolutionCache-backed) to OTHER
        consumers -- specifically the Isolation dialog's own Elements
        table (see power_intent_diagram_view.py's own
        _resolve_connectivity wiring) -- so they reuse the exact same
        Driver/Receiver computation instead of recomputing it
        independently (explicit request). `endpoints` is accepted for
        API symmetry with the caller's own usage but not otherwise
        filtered against here -- resolve_all() already only ever
        resolves what Layer 1's own structural trace actually knows
        about, and returning the FULL (receiver_by_key, driver_by_key)
        dicts lets the caller simply look up whichever keys it needs.
        Returns empty dicts if no design has been loaded yet
        (`self._structural_cache is None`)."""
        if self._structural_cache is None:
            return {}, {}
        return resolve_all(self._structural_cache, self._resolution_cache, self._supply_state, self._domain_state, self._port_attribute_state)

    def _push_port_connectivity(self) -> None:
        if self._structural_cache is None:
            return
        receiver_by_key, driver_by_key = resolve_all(
            self._structural_cache, self._resolution_cache, self._supply_state, self._domain_state, self._port_attribute_state
        )
        self.design_properties.set_connectivity(
            {k: ConnectivityCellDisplay(v.text, v.italic, v.italic_prefix_lens) for k, v in receiver_by_key.items()},
            {k: ConnectivityCellDisplay(v.text, v.italic, v.italic_prefix_lens) for k, v in driver_by_key.items()},
        )

    def _rebuild(self) -> None:
        all_tables = [
            *self._tables.values(),
            self._domain_connections_table,
        ]
        for table in all_tables:
            table.setSortingEnabled(False)
        self._load_domains()
        self._load_supply_ports()
        self._load_supply_sets()
        self._load_switches()
        self._load_isolation()
        self._load_retention()
        self._load_level_shifters()
        self._load_domain_supply_connections()
        for table in all_tables:
            table.setSortingEnabled(True)
            table.resizeRowsToContents()
        for filter_bar in self._filter_bars:
            filter_bar.reapply()

    def _load_domain_supply_connections(self) -> None:
        table = self._domain_connections_table
        conns = self._power_intent.domain_supply_connections if self._power_intent is not None else []
        table.setRowCount(len(conns))
        for row, c in enumerate(conns):
            _set_row(table, row, [c.domain, c.supply_net, c.role or "", c.scope], f"[{row}]")

    def _load_domains(self) -> None:
        """Elements/Exclude Elements/Secondary Supplies (explicit
        request, redefined from what they showed before -- see this
        method's own row-building below): "Scope" as a dedicated column
        is gone, folded into "Elements" instead (the domain's own scope,
        "." at the design root) -- a real UPF/CPF domain's genuine
        -elements list (previously shown here) isn't shown anymore.
        "Exclude Elements" for a diagram-only domain is now *computed*
        from sibling diagram-only domains nested inside its own subtree
        (_direct_excluded_children()) rather than always blank; a real
        domain's own already-authored exclude_elements is left exactly
        as-is (not merged with anything computed -- a different, RTL-
        instance-shaped field from a different coordinate space than the
        scope-shaped one computed here, not safely comparable)."""
        table = self._tables["PowerDomain"]
        rows: list[tuple[int, str, str, str, str, str, str]] = []  # depth, name, elements, exclude, primary, secondary, id_
        if self._power_intent is not None:
            for id_, d in self._power_intent.domains.items():
                rows.append((_depth(d.scope), d.name, _format_scope(d.scope), "\n".join(d.exclude_elements), d.primary_supply or "", "", id_))
        if self._domain_state is not None:
            all_diagram_domains = list(self._domain_state.domains.values())
            for id_, d in self._domain_state.domains.items():
                # Multi-hierarchy Power Domain (explicit request): one
                # row still covers the WHOLE domain -- every member
                # hierarchy's own relative scope is listed together in
                # this one row's own "Elements" cell, one per line, not
                # split across several rows.
                rel_scopes = sorted({_relative_path(p) for p in d.owner_paths})
                elements_display = "\n".join(_format_scope(s) for s in rel_scopes)
                excluded = _direct_excluded_children(d, all_diagram_domains)
                excluded_paths = {p for c in excluded for p in _nested_owner_paths(c, d)}
                # One excluded hierarchy per line, like the Elements column.
                excluded_display = "\n".join(sorted(_format_scope(_relative_path(p)) for p in excluded_paths))
                # Fix 3A (explicit request): Name/Primary Supply/Secondary
                # Supplies show the BARE name only -- the hierarchy-scope
                # prefix convention every other diagram-only row here still
                # uses is dropped for this tab specifically (domain names
                # are already unique diagram-wide, so a prefix was never
                # load-bearing for uniqueness, only informational).
                primary = ""
                if d.primary_supply_set is not None:
                    supply_set = self._domain_state.supply_sets.get(d.primary_supply_set)
                    if supply_set is not None:
                        primary = supply_set.name
                secondary_names = []
                for ss_id in d.secondary_supply_sets:
                    ss = self._domain_state.supply_sets.get(ss_id)
                    if ss is not None:
                        secondary_names.append(ss.name)
                rows.append(
                    (
                        min(_depth(s) for s in rel_scopes) if rel_scopes else 0,
                        d.name,
                        elements_display,
                        excluded_display,
                        primary,
                        "\n".join(secondary_names),  # multi-line cell -- one Secondary Supply per line
                        id_,
                    )
                )
        rows.sort(key=lambda r: (r[0], r[1]))  # explicit request: top scope to deeper one
        table.setRowCount(len(rows))
        for row, (_depth_val, name, elements, exclude, primary, secondary, id_) in enumerate(rows):
            _set_row(table, row, [name, elements, exclude, primary, secondary], id_)

    def _load_supply_ports(self) -> None:
        """Type/Connected (explicit request, redefined from Kind/
        Direction-Voltage): "Scope" is gone; a real Port/Net row's own
        direction/voltage isn't shown anymore either -- "Connected" means
        something different now (see _connected_master_name()): "-" for
        every real, top-level Port/Net or diagram-only plain Supply Port
        (nothing "upstream" of a supply declaration itself), the wired
        plain supply port's own name for a macro PG pin connected to
        one, or "Floating" (italic) for a macro PG pin with no such
        connector.

        Internal Supply entries (SupplyPortPlacement.is_internal_supply)
        are included here too now (Type="Internal Supply") -- both kinds
        share this one table (the same "one table, several Type values"
        shape Port/Net/Supply Port/Macro PG already establish) rather
        than a separate top-level tab. Virtual Supply entries
        (SupplyPortPlacement.is_virtual_supply, Type="Virtual Supply")
        share it too (spec section 6: "appear in the EXISTING 'Supply
        Port' Properties tab... with a 'Kind' column"). "States"
        (explicit request): a plain Supply Port OR Virtual Supply shows
        its own user-authored list; an Internal Supply shows its DERIVED
        effective list instead (resolve_effective_states() -- walks the
        connectivity chain back to a real source, additively including
        one "SWITCH-OFF (<name>)" state per Power Switch encountered),
        recomputed fresh on every rebuild so it stays live -- no caching;
        resolve_effective_states() itself already branches on
        `is_internal_supply` alone, so a Virtual Supply falls through to
        its own plain `.states` with zero changes needed there. A real
        Port/Net row has no such concept in the backend schema at all --
        blank, same "schema doesn't have it" precedent every other
        real-row blank column here already follows. Uses the same
        _ElementsCellWidget multi-line/"Show all" convention as
        Isolation/Retention/Level Shifter's own Elements column."""
        table = self._tables["SupplyPort"]
        rows: list[tuple[int, str, str, str, str, list, str]] = []  # depth, type, name, direction, connected, states, id_
        if self._power_intent is not None:
            for id_, p in self._power_intent.supply_ports.items():
                # A real (backend) supply port DOES carry a direction --
                # the import is what puts it on the diagram-only rows
                # below, but a row read straight from the
                # PowerIntentDesign can show its own too.
                rows.append((_depth(p.scope), "Port", p.name, p.direction or "-", "-", [], id_))
            for id_, n in self._power_intent.supply_nets.items():
                # A supply NET has no direction: only a boundary PORT
                # does. Blank, the same "schema doesn't have it"
                # convention every other real-row blank here follows.
                rows.append((_depth(n.scope), "Net", n.name, "-", "-", [], id_))
        if self._supply_state is not None:
            switches = self._power_switch_state.switches if self._power_switch_state is not None else {}
            for id_, p in self._supply_state.supply_port_placements.items():
                if p.is_internal_supply:
                    type_label = "Internal Supply"
                elif p.is_virtual_supply:
                    type_label = "Virtual Supply"
                else:
                    type_label = "Supply Port"
                states = resolve_effective_states(p, self._supply_state, switches)
                # Bug fix: an Internal Supply CAN be genuinely connected
                # to an upstream Supply Port -- manually-drawn, or
                # auto-created by the cross-hierarchy name-linking
                # mechanism (see power_intent_diagram_view.py's own
                # _auto_link_cross_hierarchy_supply_sets()), which
                # behaves identically to a manual connection in every
                # respect, including here. Reuses connected_supply_port()
                # -- the SAME generic resolver _connected_master_name()
                # already uses for a macro PG pin below, confirmed by its
                # own docstring to work for "any anchor id", not just PG
                # pins. A PLAIN (non-internal) Supply Port keeps the
                # existing "-" -- unchanged, still "nothing upstream of a
                # supply declaration itself" by design.
                connected = "-"
                if p.is_internal_supply:
                    master = connected_supply_port(id_, self._supply_state)
                    if master is not None:
                        connected = _hierarchy_prefixed_name(master.name, master.owner_path)
                # Direction is SUPPLY PORT ONLY. An Internal or Virtual
                # Supply is not a boundary port and has no such field at
                # all (its dialog does not even offer one), so it shows
                # blank rather than an empty-looking value that might
                # read as "direction not set yet".
                direction = p.direction or "-" if type_label == "Supply Port" else "-"
                rows.append((_depth(_relative_path(p.owner_path)), type_label, _hierarchy_prefixed_name(p.name, p.owner_path), direction, connected, states, id_))
            for id_, p in self._supply_state.pg_pin_placements.items():
                type_label = "Macro PG - Internal" if p.is_internal else "Macro PG"
                master = _connected_master_name(id_, self._supply_state)
                connected = master if master is not None else "Floating"
                # A macro PG pin is fixed by its Liberty definition and is
                # not a user-declared boundary port either.
                rows.append((_depth(_relative_path(p.owner_path)), type_label, _hierarchy_prefixed_name(p.name, p.owner_path), "-", connected, [], id_))
        rows.sort(key=lambda r: (r[0], r[1], r[2]))  # explicit request: top scope to deeper one
        table.setRowCount(len(rows))
        for row, (_depth_val, type_label, name, direction, connected, states, id_) in enumerate(rows):
            _set_row(table, row, [type_label, name, direction, connected], id_)
            if connected == "Floating":
                item = table.item(row, _SUPPLY_PORT_CONNECTED_COLUMN)
                font = QFont(item.font())
                font.setItalic(True)
                item.setFont(font)
            # Bug fix: a QTableWidgetItem set at a cell is NOT
            # automatically cleared by a later setCellWidget() at the
            # same cell (confirmed empirically -- table.item() still
            # returns it), and vice versa -- across two rebuilds where
            # the same row index switches between "no states" (a plain
            # item) and "has states" (a cell widget), the stale one was
            # left behind underneath the new one. Explicitly clear
            # whichever kind isn't being used before setting the other.
            if states:
                table.takeItem(row, _SUPPLY_PORT_STATES_COLUMN)
                table.setCellWidget(row, _SUPPLY_PORT_STATES_COLUMN, _ElementsCellWidget([_format_supply_state(s) for s in states], on_expand=lambda t=table, r=row: t.resizeRowToContents(r), table=table, row=row))
            else:
                table.removeCellWidget(row, _SUPPLY_PORT_STATES_COLUMN)
                table.setItem(row, _SUPPLY_PORT_STATES_COLUMN, QTableWidgetItem("-"))

    def _load_supply_sets(self) -> None:
        table = self._tables["SupplySet"]
        rows: list[tuple[int, str, str, str, str, str, str]] = []  # depth, name, power, ground, n_well, p_well, id_
        if self._power_intent is not None:
            for id_, s in self._power_intent.supply_sets.items():
                rows.append((_depth(s.scope), s.name, s.power_net or "-", s.ground_net or "-", "-", "-", id_))
        if self._domain_state is not None:
            for id_, s in self._domain_state.supply_sets.items():
                # Fix 3B (explicit request): bare names only, no
                # hierarchy-scope prefix -- and still exactly ONE row
                # per Supply Set even for a multi-hierarchy Power
                # Domain (already true here: one SupplySetPlacement/one
                # `id_` is the single shared record every member
                # hierarchy's own replicated Supply Port/Internal
                # Supply instance references -- see
                # SupplySetPlacement.owner_paths's own docstring; the
                # DIAGRAM still shows each member's own replicated
                # instance individually, this table just doesn't).
                name = s.name
                power = s.power or "-"
                ground = s.ground or "-"
                n_well = s.n_well or "-"
                p_well = s.p_well or "-"
                depth = min(_depth(_relative_path(p)) for p in s.owner_paths)
                rows.append((depth, name, power, ground, n_well, p_well, id_))
        rows.sort(key=lambda r: (r[0], r[1]))  # explicit request: top scope to deeper one
        table.setRowCount(len(rows))
        for row, (_depth_val, name, power, ground, n_well, p_well, id_) in enumerate(rows):
            _set_row(table, row, [name, power, ground, n_well, p_well], id_)

    def _load_switches(self) -> None:
        """"Switch" (rework, explicit request -- REVERSES the earlier
        "Cell Type dropdown referencing a reusable library" model):
        Input/Output/Control Port and Cell Type are now tracked directly
        on PowerSwitchPlacement itself (power_switch_overlay.py) --
        there is no more separate "Switch Cell" secondary table to merge
        in. Columns: Name/Domain/Supply In/Supply Out/Input Supply
        Port/Output Supply Port/Control Port/Cell Type/On State/Off
        State. Merges real (backend PowerIntentDesign) rows with
        diagram-only ones, same two-source-merge precedent every other
        tab here already follows.

        "Supply In"/"Supply Out": unchanged from before this rework --
        Supply Out is the switch's own attached target(s), Supply In is
        whatever's actually wired to the switch's own top anchor right
        now (connected_supply_port()) -- "Not Connected" when nothing
        is.

        "Cell Type" (generation-time warning, unchanged mechanism):
        `placement.cell_type` (free text now, not a library reference),
        or an ITALIC "No Cell Type Assigned" warning when it's empty --
        reusing the SAME "italic warning text in place of a normal
        value" convention this app's closest existing "Elaborate-time
        warning" mechanism already establishes (power_resolution_cache.py's
        own Combinational Loop/Multiple Drivers/Floating markers) rather
        than a dock or a blocking dialog, since an empty Cell Type is
        explicitly NOT a blocking error at creation/edit time -- see
        power_switch_dialog.py's own module docstring. A real
        PowerIntentDesign PowerSwitch has no Cell Type/pin-name/
        On-Off-State concept at all (shared/models/power_intent.py's own
        PowerSwitch schema) -- "-" for a real row, same "schema doesn't
        have it" precedent every other real-row blank column here
        already follows."""
        table = self._tables["PowerSwitch"]
        rows: list[tuple[str, str, str, str, str, str, str, str, str, bool, str, str, str, str, str, str]] = []
        # sort_key, name, domain, supply_in, supply_out, input_port, output_port, control_port, cell_type, cell_type_is_warning, control_signal, ack_port, ack_signal, on_state, off_state, id_
        if self._power_intent is not None:
            for id_, s in sorted(self._power_intent.switches.items()):
                supply_in = s.input_supply or "Not Connected"
                supply_out = s.output_supply or "-"
                # A real PowerIntentDesign switch carries -control_port's
                # own net in `control_ports` -- the same thing "Switch
                # Control" is -- so this one is NOT a "-" the way the
                # pin-name columns are.
                control_signal = s.control_ports[0] if s.control_ports else "-"
                ack_port = s.ack_port_pins[0] if s.ack_port_pins else "-"
                ack_signal = s.ack_ports[0] if s.ack_ports else "-"
                rows.append((s.name, s.name, s.domain or "", supply_in, supply_out, "-", "-", "-", "-", False, control_signal, ack_port, ack_signal, "-", "-", id_))
        if self._power_switch_state is not None and self._domain_state is not None and self._supply_state is not None:
            for id_, placement in self._power_switch_state.switches.items():
                domain = self._domain_state.domains.get(placement.power_domain_id)
                domain_name = domain.name if domain is not None else ""
                # Bug fix: each replicated target has its OWN
                # per-instance connector-anchor key now
                # (power_switch_connector_key(), items 9/10's own
                # multi-hierarchy support) -- checking bare
                # `placement.id` alone (the shared record's own
                # identity, never a real connector-pair endpoint for a
                # multi-target switch) never found anything for a
                # replicated switch. Checks every target in turn,
                # taking the first that resolves -- same documented
                # "whichever ONE replica's own connection is found
                # first" granularity this cell already had.
                connected = next(
                    (
                        c
                        for target_id in placement.target_ids
                        if (c := connected_supply_port(power_switch_connector_key(placement.id, target_id), self._supply_state)) is not None
                    ),
                    None,
                )
                supply_in = _hierarchy_prefixed_name(connected.name, connected.owner_path) if connected is not None else "Not Connected"
                target_names = []
                for target_id in placement.target_ids:
                    target = self._supply_state.supply_port_placements.get(target_id)
                    if target is not None:
                        target_names.append(_hierarchy_prefixed_name(target.name, target.owner_path))
                supply_out = "\n".join(target_names) if target_names else "-"
                cell_type = placement.cell_type or "No Cell Type Assigned"
                cell_type_is_warning = not placement.cell_type
                on_state = placement.on_state or "-"
                off_state = placement.off_state or "-"
                rows.append(
                    (
                        placement.name,
                        placement.name,
                        domain_name,
                        supply_in,
                        supply_out,
                        placement.input_supply_port,
                        placement.output_supply_port,
                        placement.control_port,
                        cell_type,
                        cell_type_is_warning,
                        placement.control_signal or "-",
                        placement.ack_port or "-",
                        placement.ack_signal or "-",
                        on_state,
                        off_state,
                        id_,
                    )
                )
        rows.sort(key=lambda r: r[0])  # explicit request: alphabetical, same as Retention's own sort
        table.setRowCount(len(rows))
        for row, (_key, name, domain_name, supply_in, supply_out, input_port, output_port, control_port, cell_type, cell_type_is_warning, control_signal, ack_port, ack_signal, on_state, off_state, id_) in enumerate(rows):
            _set_row(table, row, [name, domain_name, supply_in, supply_out, input_port, output_port, control_port, cell_type, control_signal, ack_port, ack_signal, on_state, off_state], id_)
            if cell_type_is_warning:
                item = table.item(row, 7)
                font = QFont(item.font())
                font.setItalic(True)
                item.setFont(font)

    def _load_isolation(self) -> None:
        """Merges real (backend PowerIntentDesign) rows with diagram-
        only ones (IsolationStrategyPlacement -- see isolation_overlay.py),
        same two-source-merge precedent _load_retention() already
        follows. Bare names only, no hierarchy-prefix (explicit request
        -- unlike Retention's own Domain/Primary/Retention Supply
        columns, which DO prefix). The real backend's own IsolationStrategy
        schema (shared/models/power_intent.py) has no location/
        isolation_control/isolation_sense fields at all -- left blank
        for a real row, same "schema doesn't have it" precedent
        Retention's own real-row Primary Supply blank already follows.
        "Elements" is a `setCellWidget()` cell (_ElementsCellWidget),
        not a plain item -- the only column here that is."""
        table = self._tables["IsolationStrategy"]
        rows: list[tuple[str, str, str, str, str, str, str, str, str, str, str, list[str], str]] = []
        # sort_key, name, domain, isolation_supply, source_supply, sink_supply, clamp, location, control, sense, applies_to, elements, id_
        if self._power_intent is not None:
            for id_, iso in sorted(self._power_intent.isolation_strategies.items()):
                clamp = _ISOLATION_CLAMP_LABELS.get(iso.clamp_value, iso.clamp_value) if iso.clamp_value else ""
                rows.append(
                    (iso.name, iso.name, iso.domain or "", iso.isolation_supply or "", iso.source or "", iso.sink or "", clamp, "", "", "",
                     _LEVEL_SHIFTER_APPLIES_TO_LABELS.get(iso.applies_to, iso.applies_to) if iso.applies_to else "", list(iso.elements), id_)
                )
        if self._isolation_state is not None and self._domain_state is not None:
            supply_sets = self._domain_state.supply_sets
            for id_, iso in self._isolation_state.strategies.items():
                domain = self._domain_state.domains.get(iso.power_domain_id)
                domain_name = domain.name if domain is not None else ""
                isolation_name = supply_sets[iso.isolation_supply_set_id].name if iso.isolation_supply_set_id in supply_sets else ""
                source_name = supply_sets[iso.source_supply_set_id].name if iso.source_supply_set_id in supply_sets else ""
                sink_name = supply_sets[iso.sink_supply_set_id].name if iso.sink_supply_set_id in supply_sets else ""
                clamp = _ISOLATION_CLAMP_LABELS.get(iso.clamp_value, iso.clamp_value)
                sense = _ISOLATION_SENSE_LABELS.get(iso.isolation_sense, iso.isolation_sense)
                rows.append(
                    (iso.name, iso.name, domain_name, isolation_name, source_name, sink_name, clamp, iso.location, iso.isolation_control, sense,
                     _LEVEL_SHIFTER_APPLIES_TO_LABELS.get(iso.applies_to, iso.applies_to), list(iso.elements), id_)
                )
        rows.sort(key=lambda r: r[0])
        table.setRowCount(len(rows))
        for row, (_key, name, domain_name, isolation_name, source_name, sink_name, clamp, location, control, sense, applies_to, elements, id_) in enumerate(rows):
            _set_row(table, row, [name, domain_name, isolation_name, source_name, sink_name, clamp, location, control, sense, applies_to], id_)
            table.setCellWidget(row, _ISOLATION_ELEMENTS_COLUMN, _ElementsCellWidget(elements, on_expand=lambda t=table, r=row: t.resizeRowToContents(r), table=table, row=row))

    def _load_retention(self) -> None:
        """Merges real (backend PowerIntentDesign) rows with diagram-only
        ones (RetentionStrategyPlacement -- see retention_overlay.py),
        same two-source-merge precedent every other tab here already
        follows. "Primary Supply"/"Retention Supply" (explicit request:
        positioned before "Elements", grouping the two supply-related
        columns together ahead of the Elements summary column) is blank
        for a real row -- the real RetentionStrategy schema
        (shared/models/power_intent.py) has no primary_supply field at
        all, only retention_supply; a diagram-only row resolves both
        from RetentionStrategyPlacement's own
        `primary_supply_set_id`/`retention_supply_set_id`, through the
        Supply Set they reference (by id), hierarchy-prefixed the same
        way Power Domains' own Primary/Secondary Supply columns already
        are. "Elements" (explicit request, item 4 -- the SAME shared
        _ElementsCellWidget Isolation's own Elements column uses, not a
        second, separate implementation) is a `setCellWidget()` cell
        now, not a plain comma-joined string -- scoped ONLY to this
        Properties-table summary cell, never touching
        retention_dialog.py's own Add/Edit Elements list (filter,
        buttons, behavior) at all.

        New non-blocking warning (explicit request): when a Retention
        Strategy's own target hierarchy (`owner_path` -- the block "Add
        Retention" was invoked on) has NO sequential elements anywhere
        in its subtree, the Name cell gets a "(No Sequential Elements)"
        suffix, italicized -- reusing the SAME "italic cell text is a
        non-blocking, Elaborate-time-style warning" convention already
        established for a Power Switch's own missing Cell Type
        (_load_switches()'s own docstring) and a floating PG pin's own
        "Floating" Connected cell, rather than a popup or a new
        mechanism. Purely advisory -- creation is never blocked (see
        power_intent_diagram_view.py's own create_retention_strategy(),
        untouched by this). Scoped to diagram-only rows only (the ONLY
        way to interactively add Retention in this milestone -- see this
        class's own module docstring on the real-vs-diagram-only split);
        a real PowerIntentDesign RetentionStrategy has no owner_path
        resolvable against `self.design_properties._design` without a
        real mapping step, so this check is simply never run for one."""
        table = self._tables["RetentionStrategy"]
        rows: list[tuple[str, str, str, str, str, str, str, str, list[str], str, bool]] = []
        # sort_key, name, scope, domain, primary, retention, save, restore, elements, id_, no_sequential_elements
        if self._power_intent is not None:
            for id_, ret in sorted(self._power_intent.retention_strategies.items()):
                # `save_signal`/`restore_signal` are real fields on the
                # backend schema, so a real row fills them in rather than
                # being blanked the way "Primary Supply" has to be.
                rows.append((ret.name, ret.name, ret.scope, ret.domain or "", "", ret.retention_supply or "",
                             ret.save_signal or "", ret.restore_signal or "", list(ret.elements), id_, False))
        if self._retention_state is not None and self._domain_state is not None:
            for id_, ret in self._retention_state.strategies.items():
                domain = self._domain_state.domains.get(ret.power_domain_id)
                domain_name = _hierarchy_prefixed_name(domain.name, domain.owner_path) if domain is not None else ""
                scope = _format_scope(_relative_path(ret.owner_path))
                primary_set = self._domain_state.supply_sets.get(ret.primary_supply_set_id)
                retention_set = self._domain_state.supply_sets.get(ret.retention_supply_set_id)
                primary_name = _hierarchy_prefixed_name(primary_set.name, primary_set.owner_path) if primary_set is not None else ""
                retention_name = _hierarchy_prefixed_name(retention_set.name, retention_set.owner_path) if retention_set is not None else ""
                no_sequential_elements = self._hierarchy_has_no_sequential_elements(ret.owner_path)
                rows.append((ret.name, ret.name, scope, domain_name, primary_name, retention_name,
                             ret.save_logic, ret.restore_logic, list(ret.elements), id_, no_sequential_elements))
        rows.sort(key=lambda r: r[0])
        table.setRowCount(len(rows))
        for row, (_key, name, scope, domain_name, primary_name, retention_name, save_signal, restore_signal, elements, id_, no_sequential_elements) in enumerate(rows):
            display_name = f"{name} (No Sequential Elements)" if no_sequential_elements else name
            _set_row(table, row, [display_name, scope, domain_name, primary_name, retention_name, save_signal, restore_signal], id_)
            if no_sequential_elements:
                item = table.item(row, 0)
                font = QFont(item.font())
                font.setItalic(True)
                item.setFont(font)
            table.setCellWidget(row, _RETENTION_ELEMENTS_COLUMN, _ElementsCellWidget(elements, on_expand=lambda t=table, r=row: t.resizeRowToContents(r), table=table, row=row))

    def _hierarchy_has_no_sequential_elements(self, owner_path: str) -> bool:
        """True when `owner_path`'s own subtree (itself plus every
        descendant instance) contains zero SequentialElements anywhere
        -- same subtree-walk shape hierarchy_view/properties_table_widget.py's
        own _SequentialPanel._refresh() already uses for the Sequential
        tab, reading `self.design_properties._design` directly (the
        Design set by load_design()) rather than duplicating a second
        copy of that walk. Conservatively False (never warns) when no
        design has been loaded yet -- nothing to check against."""
        design = self.design_properties._design
        if design is None:
            return False
        for node in design.walk():
            if node.path == owner_path or node.path.startswith(f"{owner_path}/"):
                if node.definition.sequential_elements:
                    return False
        return True

    def _load_level_shifters(self) -> None:
        """"Level Shifter" -- merges real (backend PowerIntentDesign)
        rows with diagram-only ones (LevelShifterStrategyPlacement --
        see level_shifter_overlay.py), same two-source-merge precedent
        Isolation/Retention already follow. The real backend
        LevelShifterStrategy schema (shared/models/power_intent.py) has
        no applies_to/rule/location/threshold/cell-type concept at all
        -- left blank for a real row, same "schema doesn't have it"
        precedent every other real-row blank column here already
        follows. "Elements" is the SAME shared _ElementsCellWidget
        Isolation/Retention's own Elements column already uses.

        "Cell Type" (rework, explicit request -- REVERSES the earlier
        "Level Shifter Cell Type dropdown referencing a reusable
        library" model): `strategy.cell_type` directly (free text now),
        with the SAME "No Cell Type Assigned" italic generation-time
        warning Power Switch's own Cell Type column already establishes
        -- "same treatment as Power Switch" per the explicit request."""
        table = self._tables["LevelShifterStrategy"]
        rows: list[tuple[str, str, str, str, str, str, str, str, bool, list[str], str]] = []
        # sort_key, name, domain, applies_to, rule, location, threshold, cell_type, cell_type_is_warning, elements, id_
        if self._power_intent is not None:
            for id_, ls in sorted(self._power_intent.level_shifter_strategies.items()):
                rows.append((ls.name, ls.name, ls.domain or "", "", "", "", "", "", False, list(ls.elements), id_))
        if self._level_shifter_state is not None and self._domain_state is not None:
            for id_, ls in self._level_shifter_state.strategies.items():
                domain = self._domain_state.domains.get(ls.power_domain_id)
                domain_name = domain.name if domain is not None else ""
                applies_to = _LEVEL_SHIFTER_APPLIES_TO_LABELS.get(ls.applies_to, ls.applies_to)
                rule = _LEVEL_SHIFTER_RULE_LABELS.get(ls.rule, ls.rule)
                threshold = str(ls.threshold) if ls.threshold is not None else ""
                cell_type = ls.cell_type or "No Cell Type Assigned"
                cell_type_is_warning = not ls.cell_type
                rows.append((ls.name, ls.name, domain_name, applies_to, rule, ls.location, threshold, cell_type, cell_type_is_warning, list(ls.elements), id_))
        rows.sort(key=lambda r: r[0])
        table.setRowCount(len(rows))
        for row, (_key, name, domain_name, applies_to, rule, location, threshold, cell_type, cell_type_is_warning, elements, id_) in enumerate(rows):
            _set_row(table, row, [name, domain_name, applies_to, rule, location, threshold, cell_type], id_)
            if cell_type_is_warning:
                item = table.item(row, 6)
                font = QFont(item.font())
                font.setItalic(True)
                item.setFont(font)
            table.setCellWidget(row, 7, _ElementsCellWidget(elements, on_expand=lambda t=table, r=row: t.resizeRowToContents(r), table=table, row=row))

    def _on_row_selected(self, kind: str, table: QTableWidget) -> None:
        rows = table.selectionModel().selectedRows()
        if not rows:
            return
        item = table.item(rows[0].row(), 0)
        construct_id = item.data(Qt.ItemDataRole.UserRole)
        self._controller.selectionChanged.emit(kind, construct_id)

    def set_action_enablement_hooks(self, can_edit: Callable[[str, str], bool], can_remove: Callable[[str, str], bool]) -> None:
        """Called once by whoever owns both this panel and the diagram
        view (PowerIntentTab) -- lets the OWNER decide whether a given
        row's Edit/Remove should be enabled (macro PG pin Supply Port
        rows, real-backend-only rows with no diagram-only counterpart,
        etc.), since this panel has no such context of its own. Not
        called at all by most existing tests -- the constructor default
        ("always enabled") keeps them working unmodified."""
        self._can_edit_row = can_edit
        self._can_remove_row = can_remove

    def _wire_row_context_menu(self, kind: str, table: QTableWidget) -> None:
        """LEFT-CLICK already selects a row (QTableWidget's own default
        SingleSelection behavior, unchanged) -- this adds RIGHT-CLICK,
        selecting whatever row is under the cursor (same "act on the
        thing under the cursor" convention the diagram's own right-
        click menus already use, rather than requiring a separate
        left-click first) and showing "Edit"/"Remove", each individually
        enabled/disabled via set_action_enablement_hooks() above."""
        self._kind_for_table[table] = kind
        table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        table.customContextMenuRequested.connect(lambda pos, t=table: self._show_row_context_menu(t, pos))

    def _row_context_menu_actions(self, table: QTableWidget, row: int) -> tuple[str, str, QMenu, list]:
        """Builds the menu/actions WITHOUT executing it -- split out
        specifically so this stays directly testable, same "build the
        actions, dispatch separately" precedent every other context menu
        in this app (e.g. _isolation_context_menu_actions()) already
        establishes: menu.exec() is a real, blocking native modal loop
        that cannot be driven headlessly at all (confirmed empirically --
        monkeypatching QMenu.exec itself still hung a real test).
        Returns (kind, construct_id, menu, [(action, action_kind), ...])
        so _show_row_context_menu() below can still just call exec()
        and match the result against this same list for real,
        interactive use."""
        construct_id = table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        kind = self._kind_for_table[table]
        menu = QMenu(table)
        edit_action = menu.addAction("Edit")
        edit_action.setEnabled(self._can_edit_row(kind, construct_id))
        remove_action = menu.addAction("Remove")
        remove_action.setEnabled(self._can_remove_row(kind, construct_id))
        return kind, construct_id, menu, [(edit_action, "edit"), (remove_action, "remove")]

    def _dispatch_chosen_row_action(self, kind: str, construct_id: str, action_kind: Optional[str]) -> None:
        """The directly-testable core of "the user picked X from the
        row's own context menu" -- takes a plain action_kind string
        ("edit"/"remove"/None for dismissed-with-nothing-chosen) rather
        than a live QAction, so a test can drive this without ever
        touching the real menu.exec() call at all."""
        if action_kind == "edit":
            self.editRequested.emit(kind, construct_id)
        elif action_kind == "remove":
            self.removeRequested.emit(kind, construct_id)

    def _show_row_context_menu(self, table: QTableWidget, pos) -> None:
        row = table.rowAt(pos.y())
        if row < 0:
            return
        table.selectRow(row)
        kind, construct_id, menu, actions = self._row_context_menu_actions(table, row)
        chosen = menu.exec(table.viewport().mapToGlobal(pos))
        action_kind = next((ak for action, ak in actions if action is chosen), None)
        self._dispatch_chosen_row_action(kind, construct_id, action_kind)

    def setSelection(self, construct_kind: str, construct_id: str) -> None:
        table = self._tables.get(construct_kind)
        if table is None:
            return
        # Not setCurrentWidget(table) -- a real bug, found by running the
        # test suite: `table` is no longer the tab page's own direct
        # child widget now that each one is wrapped with its own filter
        # bar (see wrap_with_filter()); setCurrentWidget() silently
        # no-ops for a widget that isn't a registered tab page at all.
        tab_index = next(i for i, kind in self._kind_for_tab.items() if kind == construct_kind)
        self._tabs.setCurrentIndex(tab_index)
        table.blockSignals(True)
        for row in range(table.rowCount()):
            if table.item(row, 0).data(Qt.ItemDataRole.UserRole) == construct_id:
                table.selectRow(row)
                table.scrollToItem(table.item(row, 0))
                break
        table.blockSignals(False)
