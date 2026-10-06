"""'Power Intent' tab: Hierarchy Tree, Diagram, Properties side by side
in a resizable QSplitter -- same horizontal-splitter arrangement as
Visualize Design's own.

Hierarchy Tree and Diagram now have full feature parity with Visualize
Design's own (multi-select, Hide/Unhide, Copy Path, Ctrl+Z/Ctrl+Y undo,
Fit View, mouse-wheel zoom, click-drag pan) -- see
power_intent_diagram_view.py's own docstring for how the Diagram side
achieves this by actually reusing HierarchyDiagramView, not
reimplementing it. Both this tab's Tree and Diagram share the *same*
HierarchySelectionController instance, so Hide/Unhide and highlight sync
bidirectionally between them exactly like Visualize Design's own
Tree/Diagram pair -- a separate, independent controller
(PowerIntentSelectionController) drives the power-intent-construct
selection concept (domain/switch/badge clicks -> Properties panel),
layered on top of, not replacing, the hierarchy one.

The Diagram's own "Filter" button (bottom-right corner, matching the
Properties tables' own Filter button placement/style -- see
shared/widgets/filter_button.py) is a placeholder only, per explicit
request: the category checkboxes (Supply Ports/Supply Nets/Power
Domains/Power Switches/Isolations/Retention/Level Shifters) exist and
are toggleable, but toggling one does not actually filter anything yet.

Diagram starts in the bare-hierarchy-plus-placeholder state (set_rtl())
and only shows the full power-intent overlay once Mapping has actually
run (set_mapped_design()) -- see PowerIntentDiagramView's docstring for
why the overlay can't be built from a PowerIntentDesign alone.

"PortList"/"Sequential Elements" (explicit request) moved here as this
tab's own Properties panel's first two top-level tabs, now that
Visualize Design is hidden from the main window (see main_window.py's
own module docstring) -- fed via set_rtl() -> self.properties.load_design(rtl),
driven by this tab's own self._hierarchy_controller (see
PowerIntentPropertiesPanel's own docstring for the full wiring).

The Properties dock itself is titled "Properties" (renamed from "Power
Intent Properties", explicit request -- shorter, and this tab's own
name already says "Power Intent").
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QDockWidget, QGroupBox, QHBoxLayout, QMainWindow, QPushButton, QTabWidget, QVBoxLayout, QWidget

from frontend.src.hierarchy_view.hierarchy_tree_widget import HierarchyTreeWidget
from frontend.src.hierarchy_view.selection_controller import HierarchySelectionController
from frontend.src.power_intent_view.diagram_tool_palette import DiagramToolPalette
from frontend.src.power_intent_view.power_intent_diagram_view import PowerIntentDiagramView
from frontend.src.power_intent_view.power_view_tree import PowerViewTree, build_power_view_tree
from frontend.src.power_intent_view.properties_panel import PowerIntentPropertiesPanel
from frontend.src.power_intent_view.selection_controller import PowerIntentSelectionController
from frontend.src.shared.models.mapping import MappedDesign
from frontend.src.shared.models.power_intent import PowerIntentDesign
from frontend.src.shared.models.rtl import Design
from frontend.src.shared.widgets.dock_window_behavior import make_dock_a_normal_window_when_floating
from frontend.src.shared.widgets.filter_button import FilterButton
from frontend.src.shared.widgets.shrinkable_tabs import allow_narrow_resize

_DIAGRAM_FILTER_CATEGORIES = [
    "Supply Ports",
    "Supply Nets",
    "Power Domains",
    "Power Switches",
    "Isolations",
    "Retention",
    "Level Shifters",
]


class PowerIntentTab(QMainWindow):
    """Explicit request: Hierarchy Tree and Properties are
    QDockWidgets (visible by default, closable/reopenable), Power
    Diagram stays a plain QGroupBox as this tab's own central widget --
    same shape as hierarchy_view/visualize_design_tab.py's own
    QMainWindow conversion; see that class's own docstring for why a
    QMainWindow nests fine as a QTabWidget page and for the
    never-call-self.menuBar() rule."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._hierarchy_controller = HierarchySelectionController()
        self._power_intent_controller = PowerIntentSelectionController()

        self.tree = HierarchyTreeWidget(self._hierarchy_controller)
        # Same QUndoStack Hide/Unhide already pushes onto -- explicit
        # request: Ctrl+Z/Ctrl+Y for Power Intent edits too, uniformly
        # with Hide/Unhide rather than a second, independent stack (see
        # PowerIntentDiagramView's own docstring).
        self.diagram = PowerIntentDiagramView(self._hierarchy_controller, self._power_intent_controller, self.tree.undo_stack)
        self.tree.visualizeDiagramRequested.connect(self.diagram.open_path)
        # `self._hierarchy_controller` passed through too (explicit
        # request: "Design Properties" moved here, as this panel's own
        # first tab -- see PowerIntentPropertiesPanel's own docstring) --
        # the same controller already shared between this tab's own Tree
        # and Diagram, so a Tree click drives Design Properties' own
        # selection too, exactly like Visualize Design's own Tree used to
        # drive its own (now-hidden) copy.
        self.properties = PowerIntentPropertiesPanel(self._power_intent_controller, self._hierarchy_controller)
        # Explicit request: Edit/Remove directly from Properties table
        # rows -- the panel itself has no diagram/view context, so it
        # only ever fires (kind, construct_id); this tab, owning both
        # the panel and the diagram, dispatches to the diagram's own
        # currently-active view (self.diagram._view), which reuses the
        # EXACT SAME dialogs/cascade-delete logic the diagram's own
        # right-click-on-icon flow already uses (see
        # _PowerIntentGraphicsView.edit_properties_row()/
        # remove_properties_row()'s own docstring).
        self.properties.editRequested.connect(lambda kind, cid: self.diagram._view.edit_properties_row(kind, cid))
        self.properties.removeRequested.connect(lambda kind, cid: self.diagram._view.remove_properties_row(kind, cid))
        self.properties.set_action_enablement_hooks(
            lambda kind, cid: self.diagram._view.can_edit_properties_row(kind, cid),
            lambda kind, cid: self.diagram._view.can_remove_properties_row(kind, cid),
        )
        # "Power View" (explicit request): a second Hierarchy Tree dock
        # tab, organized by Power Domain rather than raw RTL hierarchy --
        # see power_view_tree.py's own module docstring. Shares the SAME
        # PowerIntentSelectionController every other construct-selection
        # entry point here already uses (diagram click, Properties table
        # row), and the SAME hierarchy_controller Design View's own tree/
        # diagram already share (for its own Macro-node selection) --
        # NOT a parallel selection mechanism.
        self.power_view_tree = PowerViewTree(self._power_intent_controller, self._hierarchy_controller)
        self.power_view_tree.editRequested.connect(lambda kind, cid: self.diagram._view.edit_properties_row(kind, cid))
        self.power_view_tree.removeRequested.connect(lambda kind, cid: self.diagram._view.remove_properties_row(kind, cid))
        self.power_view_tree.set_action_enablement_hooks(
            lambda kind, cid: self.diagram._view.can_edit_properties_row(kind, cid),
            lambda kind, cid: self.diagram._view.can_remove_properties_row(kind, cid),
        )
        # Diagram-only supply ports/domains (Add Power Supply/Create
        # Power Domain) live entirely outside any PowerIntentDesign --
        # see properties_panel.py's own module docstring -- so the
        # Properties panel needs its own separate feed for them,
        # refreshed on every diagram edit (add/edit/delete/create/
        # remove/undo/redo), not just once at Load Power Intent time.
        self.diagram.diagramStateChanged.connect(self._refresh_diagram_overlay_properties)
        self.diagram.diagramStateChanged.connect(self._refresh_power_view_tree)
        # Section 7's dirty flag. Deliberately the same signal the two
        # refreshes above already use -- "the diagram state changed" is
        # exactly the question, so there is no separate notion of
        # dirtiness to keep in sync with the real one. Reset by whatever
        # REPLACES the state wholesale (a project load, an import), never
        # by an edit.
        self.diagram.diagramStateChanged.connect(self._mark_dirty)
        self._dirty = False
        # The Isolation dialog's own Elements table reuses PortList's own
        # Driver/Receiver Supply Set computation (explicit request: don't
        # recompute independently) -- self.properties owns the
        # PowerResolutionCache-backed cache this needs, but is
        # constructed AFTER self.diagram above, so this is wired as a
        # post-construction setter rather than a constructor parameter
        # (see PowerIntentDiagramView.set_connectivity_resolver()'s own
        # docstring).
        self.diagram.set_connectivity_resolver(self.properties.resolve_port_connectivity)

        # Explicit request: a grouped tool palette ("like KiCad's icon
        # toolbar") docked to the side of the Diagram's own QGraphicsView
        # -- see diagram_tool_palette.py's own module docstring. Only
        # emits modeChanged; self.diagram.set_mode() (which propagates to
        # every open diagram tab, current and future) owns what a mode
        # actually does.
        self.tool_palette = DiagramToolPalette()
        self.tool_palette.modeChanged.connect(self.diagram.set_mode)
        # Explicit request: Escape resets tool state back to Regular
        # Cursor -- the diagram view cancels its own in-progress
        # interaction and only announces that it happened (see
        # _PowerIntentGraphicsView.keyPressEvent()'s own docstring);
        # checking this action is what actually drives the mode back to
        # SELECT, through the exact same modeChanged -> set_mode() chain
        # a normal toolbar click already uses.
        self.diagram.escapePressed.connect(lambda: self.tool_palette.select_action.setChecked(True))

        self.fit_view_button = QPushButton("Fit View")
        self.fit_view_button.clicked.connect(self.diagram.fit_view)

        self.clear_hidden_button = QPushButton("Clear")
        self.clear_hidden_button.clicked.connect(self.tree.clear_hidden)

        # Placeholder only -- see class docstring. lambda: None is a
        # genuine no-op; FilterButton still needs an on_change callable.
        self.diagram_filter_button = FilterButton(_DIAGRAM_FILTER_CATEGORIES, lambda: None)

        self.undo_shortcut = QShortcut(QKeySequence("Ctrl+Z"), self)
        self.undo_shortcut.activated.connect(self.tree.undo_stack.undo)
        self.redo_shortcut = QShortcut(QKeySequence("Ctrl+Y"), self)
        self.redo_shortcut.activated.connect(self.tree.undo_stack.redo)

        diagram_box = QGroupBox("Power Diagram")
        diagram_layout = QVBoxLayout(diagram_box)
        diagram_top_row = QHBoxLayout()
        diagram_top_row.addStretch(1)
        diagram_top_row.addWidget(self.fit_view_button)
        diagram_layout.addLayout(diagram_top_row)
        # Tool palette docked to the left of the QGraphicsView itself
        # (explicit request), matching KiCad's own left-side vertical
        # icon toolbar convention -- inside this group box, alongside
        # self.diagram, not a separate QMainWindow-level dock (Hierarchy
        # Tree/Properties are already docks; this is specific to the
        # Diagram's own canvas).
        diagram_canvas_row = QHBoxLayout()
        diagram_canvas_row.addWidget(self.tool_palette)
        diagram_canvas_row.addWidget(self.diagram, 1)
        diagram_layout.addLayout(diagram_canvas_row)
        diagram_filter_row = QHBoxLayout()
        diagram_filter_row.addStretch(1)
        diagram_filter_row.addWidget(self.diagram_filter_button)
        diagram_layout.addLayout(diagram_filter_row)
        self.setCentralWidget(diagram_box)

        tree_container = QWidget()
        tree_layout = QVBoxLayout(tree_container)
        tree_layout.setContentsMargins(0, 0, 0, 0)
        tree_layout.addWidget(self.tree)
        tree_bottom_row = QHBoxLayout()
        tree_bottom_row.addStretch(1)
        tree_bottom_row.addWidget(self.clear_hidden_button)
        tree_layout.addLayout(tree_bottom_row)

        # "Design View"/"Power View" (explicit request): the Hierarchy
        # Tree dock's own widget is now a QTabWidget -- "Design View" is
        # `tree_container`, moved here unchanged (same tree/behavior/
        # right-click actions, same `self.tree`/`self.clear_hidden_button`
        # attribute references every existing caller/test already uses);
        # "Power View" is the new PowerViewTree (see power_view_tree.py).
        # Same narrow-resize fix as the Properties panel: fewer tabs, so
        # a smaller floor, but this dock is equally draggable and the
        # constraint is the same one.
        self.tree_view_tabs = allow_narrow_resize(QTabWidget())
        self.tree_view_tabs.addTab(tree_container, "Design View")
        self.tree_view_tabs.addTab(self.power_view_tree, "Power View")

        self.tree_dock = QDockWidget("Hierarchy Tree")
        self.tree_dock.setWidget(self.tree_view_tabs)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.tree_dock)
        make_dock_a_normal_window_when_floating(self.tree_dock)

        self.properties_dock = QDockWidget("Properties")
        self.properties_dock.setWidget(self.properties)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.properties_dock)
        make_dock_a_normal_window_when_floating(self.properties_dock)

    def set_rtl(self, rtl: Design) -> None:
        """Pre-Mapping state -- see class docstring."""
        self.tree.load(rtl)
        self.diagram.set_hierarchy_only(rtl)
        self.properties.load_design(rtl)
        self._refresh_power_view_tree()

    def set_power_intent(self, power_intent: PowerIntentDesign) -> None:
        self.properties.load(power_intent)

    def load_project(
        self,
        rtl: Design,
        supply_state,
        domain_state,
        retention_state,
        port_attribute_state,
        power_switch_state,
        isolation_state,
        level_shifter_state,
        macro_power_intent_state=None,
        hidden_paths=None,
    ) -> None:
        """'Load Project' (explicit request): rebuild this tab wholesale
        from a restored `.pis` file. Loads the RTL hierarchy (same
        set_hierarchy_only() path as the normal pre-Mapping flow), bulk-
        restores every diagram-only state family into the diagram's own
        live shared objects (PowerIntentDiagramView.restore_all_shared_states(),
        which also rebuilds every open tab's scene), reveals the diagram
        (there is no Mapping step on this path -- same as 'Create New'),
        and refreshes the Properties overlay + Power View tree from the
        now-loaded state."""
        self.tree.load(rtl)
        self.diagram.set_hierarchy_only(rtl)
        self.properties.load_design(rtl)
        self.diagram.restore_all_shared_states(
            supply_state,
            domain_state,
            retention_state,
            port_attribute_state,
            power_switch_state,
            isolation_state,
            level_shifter_state,
            macro_power_intent_state,
        )
        self.diagram.show_bare_hierarchy()
        # The hierarchies hidden when the project was saved. Last, once
        # the diagram exists: the tree's visibilityChanged rebuilds it
        # without them. Not undoable -- see restore_hidden_paths().
        if hidden_paths:
            self.tree.restore_hidden_paths(hidden_paths)
        self._refresh_diagram_overlay_properties()
        self._refresh_power_view_tree()
        self.mark_clean()

    # --- Section 7: dirty tracking for a re-load ---------------------------

    def _mark_dirty(self) -> None:
        self._dirty = True

    def is_dirty(self) -> bool:
        """Whether the diagram has been edited since the state was last
        replaced wholesale.

        Read by LoadPowerIntentTab before a RE-load, which discards this
        state entirely -- deliberately a plain boolean, not a diff: a
        re-load is a full replace (spec section 7 is explicit that no
        incremental merge is to be built), so the only question worth
        asking is whether anything would be lost at all."""
        return self._dirty

    def mark_clean(self) -> None:
        self._dirty = False

    def apply_imported_power_intent(self, rtl: Design, imported) -> None:
        """Replace this tab's whole diagram state with an imported
        UPF/CPF file's own (spec section 2's Load Power Intent mapping).

        Delegates to `load_project()` rather than reimplementing the
        restore: 'a complete set of diagram-only states arrives from
        outside and replaces everything' is the identical operation, and
        an import that restored the eight families itself would be a
        second place to keep in sync with `restore_all_shared_states()`.
        `imported` is a power_intent_import.ImportedPowerIntent; it is
        typed loosely here only to keep this module import-free of the
        importer, which pulls in the backend-facing IR mirror."""
        self.load_project(
            rtl,
            imported.supply,
            imported.domain,
            imported.retention,
            imported.port_attribute,
            imported.power_switch,
            imported.isolation,
            imported.level_shifter,
            imported.macro_power_intent,
        )

    def _refresh_diagram_overlay_properties(self) -> None:
        self.properties.refresh_diagram_overlay(
            self.diagram.shared_supply_state,
            self.diagram.shared_domain_state,
            self.diagram.shared_retention_state,
            self.diagram.shared_port_attribute_state,
            self.diagram.shared_power_switch_state,
            self.diagram.shared_isolation_state,
            self.diagram.shared_level_shifter_state,
        )

    def _refresh_power_view_tree(self) -> None:
        """Rebuilds "Power View" wholesale from the diagram's own
        currently-active view -- see power_view_tree.py's own module
        docstring for why a full rebuild-on-signal is sufficient for
        live color updates AND live add/remove reflection alike (both
        sections 3/4 and 6), with no separate per-item callback wiring
        needed. Connected to `diagramStateChanged` (every diagram edit)
        and also called directly after set_rtl() (a fresh design has an
        empty tree until any power intent is actually applied, but must
        not show stale data from a PREVIOUSLY loaded design)."""
        view = self.diagram._view
        nodes = build_power_view_tree(
            view._boxes,
            self.diagram.shared_domain_state,
            self.diagram.shared_retention_state,
            self.diagram.shared_isolation_state,
            self.diagram.shared_power_switch_state,
            view._supply_port_icons,
            self.diagram.shared_level_shifter_state,
        )
        self.power_view_tree.rebuild(nodes)

    def set_mapped_design(self, mapped: MappedDesign) -> None:
        """Post-Mapping state -- full overlay."""
        self.diagram.set_mapped_design(mapped)
        self._refresh_power_view_tree()

    def show_diagram_without_mapping(self) -> None:
        """'Create New' succeeded on the Load Power Intent tab -- see
        PowerIntentDiagramView.show_bare_hierarchy()'s own docstring for
        why this tab's Diagram needs a distinct reaction from Mapping's
        own set_mapped_design()."""
        self.diagram.show_bare_hierarchy()
        self._refresh_power_view_tree()
