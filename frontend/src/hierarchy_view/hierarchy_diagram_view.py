"""QGraphicsView wrapper for the Visualize Design tab's Diagram group
box. Builds the shared base hierarchy scene and makes instance boxes
selectable/clickable, synced with the Hierarchy Tree via
HierarchySelectionController -- this tab's own selection concept,
independent of power_intent_view's construct-kind selection.

Selection sync is bidirectional through that one shared controller:
clicking a tree item calls apply_selection() here (highlights the matching
box); clicking a box fires _on_scene_selection_changed(), which emits
back through the controller so the Hierarchy Tree highlights the
matching item -- neither side is "primary," both just react to the same
signal (see selection_controller.py's hub-and-spoke docstring).

Multi-select is symmetric with the Tree (Ctrl+click there also
multi-selects rows -- see hierarchy_tree_widget.py): every box already
has ItemIsSelectable set below, and Qt's own default QGraphicsScene
mouse handling already implements Ctrl+click-to-toggle/plain-click-to-
replace out of the box (confirmed directly -- nothing hand-rolled here,
unlike the pan-vs-click distinction below which genuinely needs custom
mouse handling). controller.selectionChanged carries the *complete*
current selection (a frozenset[str] of paths, possibly empty), so this
view and the Tree always converge to the same set regardless of which
side originated the change.

Two related but deliberately distinct sets of paths are tracked:
`_selected_paths` (exactly what controller.selectionChanged carries --
the boxes that are actually Qt-selected, setSelected(True)) and
`_highlighted_paths` (`_selected_paths` plus every descendant of each
selected path -- the boxes that get the light-blue set_highlighted(True)
treatment). Selecting a container visually cascades the highlight down
into its own nested children (so the selection reads as "this whole
assembly," not just its outer border -- matters most for a macro box
nested a level or two down, whose own highlight would otherwise be easy
to miss buried inside an unhighlighted container), but the *selection*
itself never cascades: only the exact box(es) the controller says are
selected ever get setSelected(True) or get reported back out of
_on_scene_selection_changed(). Conflating the two would silently expand
what counts as "selected" to the Properties panel (whose PortList/
Sequential union is keyed off controller.selectionChanged, not off this
view's own highlight set) every time a container got selected.

Zoom is mouse-wheel only (wheelEvent, anchored under the cursor) -- no
on-screen zoom buttons. Panning is left-click-drag, with the cursor
showing an open hand at rest and a closed hand while dragging -- the
same visual convention as Qt's built-in ScrollHandDrag mode, but
hand-rolled (mousePressEvent/mouseMoveEvent/mouseReleaseEvent) rather
than using ScrollHandDrag itself: that built-in mode intercepts every
left-click for panning and never forwards it to the scene, which would
silently break box-click selection above. The distinguishing signal is
movement past a small pixel threshold: a press+release with no real
movement still reaches the scene as a normal item-selection click; only
once the cursor has actually moved does it commit to panning (and swallow
the rest of that gesture so it doesn't *also* register as a click).

Also mirrors the Hierarchy Tree's Hide/Unhide state (controller.
visibilityChanged): a hidden instance's whole subtree is dropped from
the layout and the scene is rebuilt (see hierarchy_scene_builder.py's
`hidden_paths` docstring) -- boxes disappear, not just dim, and their
former parent shrinks to fit what's left. The view re-fits itself after
every such rebuild, since the diagram's overall size just changed.

Right-click a box here to Hide it too, same effect as the Tree's own
Hide -- this view doesn't own hidden-path state, so it only ever emits
controller.hideRequested(paths) and lets the Tree (the authoritative
owner) apply it. Multi-select-aware, same convention as the Tree's own
context menu: if the right-clicked box is part of the current
multi-selection (`_selected_paths`), the whole selection is targeted,
not just the box under the cursor. Only "Hide" is offered, never
"Unhide": once a box is hidden it's gone from this scene entirely, so
there's nothing left here to right-click back into visibility -- that
only lives on the Tree's own context menu.

Also offers "Copy Path" (single-target only, same restriction and same
clipboard behavior as the Tree's own context menu) -- copies the
right-clicked box's full hierarchy path. Disabled (visible, greyed out
-- not omitted, same convention every multi-select-incompatible action
in this diagram follows) whenever the menu would otherwise target more
than one box via the multi-select rule above.

Zoom-out is clamped, not infinite: it won't scale below whatever the
Fit-View scale is for the current scene/viewport (see _fit_scale()).
Once a zoom-out tick would cross that floor, it snaps exactly to it
instead of overshooting; once already at or below it, further zoom-out
ticks are a no-op -- deliberately never a zoom-*in* correction. A zoom-
out gesture must never net an increase in scale, even in the (real,
tested) edge case of a design that's already smaller than the viewport
at the default scale, where the floor sits above 1.0.

A real, reproduced-standalone PySide hazard, not hypothetical: destroying
a QGraphicsScene while one of its items is still selected can segfault --
the item's C++ destructor fires selectionChanged() back into this
(possibly also-being-destroyed) Python object mid-teardown, and
shiboken's callback dispatch crashes if the Python side is already
partially collected. `_rebuild()` clears the outgoing scene's selection
before replacing it (covers hide/unhide and reload); `closeEvent()`
clears the current scene's selection too (covers this widget being
explicitly closed, which is how pytest-qt tears down test widgets). Both
are cheap and unconditionally safe to call even when nothing is selected.

`root` (explicit request: opening a sub-module in its own diagram tab,
see hierarchy_diagram_tabs.py) lets `load()` render a scene rooted
somewhere other than the design's own top, via
hierarchy_scene_builder.py's own `root` parameter -- None (the default)
preserves the original "always the real top" behavior exactly, so every
existing single-diagram caller (Visualize Design/Power Intent's own
root tab) needs no changes at all. Double-clicking a box emits
`openSubHierarchyRequested(path)` -- this view has no opinion on what
happens next (opening/switching to a tab for that path is
hierarchy_diagram_tabs.py's job), it only reports the gesture.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QGuiApplication, QMouseEvent, QPainter, QWheelEvent
from PySide6.QtWidgets import QGraphicsItem, QGraphicsView, QMenu, QWidget

from frontend.src.hierarchy_view.selection_controller import HierarchySelectionController
from frontend.src.shared.diagram.hierarchy_scene_builder import InstanceBoxItem, build_hierarchy_scene
from frontend.src.shared.models.rtl import Design, HierarchyNode

_ZOOM_FACTOR = 1.25
_PAN_THRESHOLD_PX = 4  # movement below this, while the button is held, still counts as a click


class HierarchyDiagramView(QGraphicsView):
    openSubHierarchyRequested = Signal(str)  # double-clicked box's instance path

    def __init__(self, controller: HierarchySelectionController, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._controller = controller
        self._design: Optional[Design] = None
        self._root: Optional[HierarchyNode] = None
        self._boxes: dict = {}
        self._selected_paths: frozenset = frozenset()
        self._highlighted_paths: frozenset = frozenset()
        self._hidden_paths: frozenset = frozenset()
        self._pan_start: Optional[QPoint] = None
        self._panning = False
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        # Explicit request ("icons... displaying as pixelated/low
        # quality"): every icon pixmap on this view (Supply Port,
        # Retention badges, tool-palette cursors) is already rendered
        # from its own SVG source at a supersampled resolution (see
        # diagram_tool_icons.py/supply_port_overlay.py/retention_overlay.py's
        # own _RENDER_SUPERSAMPLE), but this view's own zoom transform
        # (mouse-wheel/Zoom Lens) still scales that already-rasterized
        # pixmap up further -- without this hint, Qt uses fast
        # nearest-neighbor scaling for that, which is exactly what reads
        # as "pixelated" once zoomed in. SmoothPixmapTransform makes any
        # further scaling bilinear-interpolated instead; Antialiasing
        # covers this view's own vector-drawn items (box borders,
        # connector lines, badge/handle shapes).
        self.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context_menu)
        controller.selectionChanged.connect(self.apply_selection)
        controller.visibilityChanged.connect(self._on_visibility_changed)

    def load(self, design: Design, root: Optional[HierarchyNode] = None) -> None:
        self._design = design
        self._root = root
        self._hidden_paths = frozenset()
        self._selected_paths = frozenset()
        self._highlighted_paths = frozenset()
        self._rebuild()

    def refresh(self) -> None:
        """Re-renders against whatever design/root this view already has
        -- used by hierarchy_diagram_tabs.py to bring a tab up to date
        when the user switches to it (e.g. a supply port added while a
        *different* tab was active -- see power_intent_diagram_view.py's
        SupplyPortDiagramState, shared across every tab). A thin public
        wrapper so callers outside this class don't need to reach for
        the protected _rebuild() directly."""
        self._rebuild()

    def _effective_highlight_set(self, paths: frozenset) -> frozenset:
        """`paths` plus every descendant of each -- a box is a descendant
        of `p` if its own path starts with f"{p}/". String-prefix
        matching, not a tree walk: box paths already encode the full
        hierarchy path, same as everywhere else this diagram/tree code
        determines ancestry (e.g. hierarchy_tree_widget.py's own
        _apply_greying())."""
        return frozenset(
            box_path
            for box_path in self._boxes
            if box_path in paths or any(box_path.startswith(f"{p}/") for p in paths)
        )

    def _on_visibility_changed(self, hidden_paths) -> None:
        self._hidden_paths = frozenset(hidden_paths)
        self._rebuild()
        self.fit_view()

    def _extra_top_margin(self) -> dict:
        """Overridable hook (same "base class stays generic, subclass
        customizes" precedent as _pan_enabled()/_set_own_cursor()) --
        {path: pixels} of extra room build_hierarchy_scene() should
        reserve above that box, for whatever reason a subclass has.
        Empty here: this base class (hierarchy_view's own Visualize
        Design tab) has no power-intent overlay content that would ever
        need it -- see _PowerIntentGraphicsView's own override
        (power_intent_diagram_view.py), which reserves room for Power
        Switch icons."""
        return {}

    def _extra_left_margin(self) -> dict:
        """Same overridable-hook shape as _extra_top_margin() above, for
        room reserved OUTSIDE a box's own LEFT edge (explicit request,
        Isolation's own "make room" layout, Location=parent/fanout) --
        empty here for the same "this base class has no power-intent
        overlay content" reason; see _PowerIntentGraphicsView's own
        override."""
        return {}

    def _extra_interior_top(self) -> dict:
        """Same overridable-hook shape as _extra_top_margin() above, for
        room reserved INSIDE a box's own top (explicit request,
        Supply-Port/Isolation "make room" layout -- a top-edge Supply
        Port/Internal Supply pushing the box's own children down, or an
        internally-placed Isolation badge, Location=self) -- empty here
        for the same reason; see _PowerIntentGraphicsView's own
        override."""
        return {}

    def _extra_interior_bottom(self) -> dict:
        """Same overridable-hook shape as _extra_interior_top() above,
        for room reserved INSIDE a box's own BOTTOM (explicit request --
        a Ground-type Supply Port/Internal Supply/macro PG pin anchored
        there needs the same power-net-routing clearance a top-anchored
        one already gets, just mirrored below instead of above) --
        empty here for the same "no power-intent overlay content"
        reason; see _PowerIntentGraphicsView's own override."""
        return {}

    def _extra_interior_min_width(self) -> dict:
        """Same overridable-hook shape as _extra_interior_top() above,
        for a MINIMUM total width a box's own natural size must reach
        (explicit bug-fix request -- adjacent same-edge Supply Port/
        Internal Supply labels must never overlap, which a narrow LEAF
        box's own fixed LEAF_WIDTH can't guarantee regardless of how
        many icons end up anchored to it) -- empty here for the same
        "no power-intent overlay content" reason; see
        _PowerIntentGraphicsView's own override."""
        return {}

    def _rebuild(self) -> None:
        if self._design is None:
            return
        old_scene = getattr(self, "_scene", None)
        if old_scene is not None:
            old_scene.clearSelection()  # see module docstring -- avoids a segfault on teardown
        scene, boxes = build_hierarchy_scene(
            self._design,
            self._hidden_paths,
            root=self._root,
            extra_top_margin=self._extra_top_margin(),
            extra_left_margin=self._extra_left_margin(),
            extra_interior_top=self._extra_interior_top(),
            extra_interior_bottom=self._extra_interior_bottom(),
            extra_interior_min_width=self._extra_interior_min_width(),
        )
        self._scene = scene  # setScene() doesn't keep Python's own reference alive; without this
        # the local `scene` here can get garbage-collected out from under the view once this
        # method returns, since QGraphicsView.setScene() doesn't reparent it in Qt's ownership
        # sense either -- a real, previously-hit segfault (an unheld QGraphicsScene freed while
        # still referenced by the view), not a hypothetical.
        self._boxes = boxes
        for box in boxes.values():
            box.setFlag(QGraphicsItem.ItemIsSelectable)
        scene.selectionChanged.connect(self._on_scene_selection_changed)
        self.setScene(scene)
        self._selected_paths = frozenset(p for p in self._selected_paths if p in self._boxes)
        self._highlighted_paths = self._effective_highlight_set(self._selected_paths)
        for path in self._highlighted_paths:
            self._boxes[path].set_highlighted(True)
        for path in self._selected_paths:
            self._boxes[path].setSelected(True)

    def _on_scene_selection_changed(self) -> None:
        selected = self.scene().selectedItems()
        paths = frozenset(path for path, box in self._boxes.items() if box in selected)
        self._controller.selectionChanged.emit(paths)

    def apply_selection(self, paths) -> None:
        """controller.selectionChanged handler (the Tree -> Diagram
        direction, and this view's own scene re-notifying itself after a
        real click -- see _on_scene_selection_changed()). Calls
        setSelected() on every exactly-selected box, not just
        set_highlighted() -- otherwise a tree-driven highlight never gets
        Qt's own "item is selected" paint decoration that a real diagram
        click gets for free (that path goes through Qt's native
        click-to-select machinery, which does call setSelected()), making
        a tree-driven highlight visually much fainter than a
        diagram-driven one for the exact same path. setSelected() is
        never called on a cascaded-only descendant, though -- see the
        module docstring's `_selected_paths` vs `_highlighted_paths` note.

        Only the boxes whose state actually changes are touched (a
        set-difference against the previous state), not every box in
        `paths`/its cascaded set unconditionally -- matters now that a
        selection can span multiple paths, each with its own subtree: a
        box that was already highlighted (directly or via cascade from a
        different still-selected ancestor) and stays highlighted
        shouldn't be redundantly un-set-then-reset on every update.

        The scene is signal-blocked for the duration: setSelected() fires
        scene.selectionChanged synchronously, which is wired to
        _on_scene_selection_changed() -> controller.selectionChanged.emit()
        -- i.e. this very call would otherwise immediately re-trigger
        itself (and the Tree's own apply_selection()) recursively for a
        selection change that didn't originate from a real click. Same
        "block while driving state programmatically" reasoning as
        HierarchyTreeWidget.apply_selection()'s own blockSignals() call.
        """
        paths = frozenset(paths)
        effective = self._effective_highlight_set(paths)
        scene = self.scene()
        if scene is not None:
            scene.blockSignals(True)
        try:
            for old_path in self._highlighted_paths - effective:
                box = self._boxes.get(old_path)
                if box is not None:
                    box.set_highlighted(False)
            for new_path in effective - self._highlighted_paths:
                box = self._boxes.get(new_path)
                if box is not None:
                    box.set_highlighted(True)
            self._highlighted_paths = effective

            for old_path in self._selected_paths - paths:
                box = self._boxes.get(old_path)
                if box is not None:
                    box.setSelected(False)
            for new_path in paths - self._selected_paths:
                box = self._boxes.get(new_path)
                if box is not None:
                    box.setSelected(True)
            self._selected_paths = paths
        finally:
            if scene is not None:
                scene.blockSignals(False)

    def fit_view(self) -> None:
        """Fit View button handler -- scales/pans so the whole diagram
        fits inside the current viewport, preserving aspect ratio."""
        scene = self.scene()
        if scene is None or not scene.items():
            return
        self.fitInView(scene.itemsBoundingRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def _box_at(self, pos) -> Optional[InstanceBoxItem]:
        """Deliberately `self.items(pos)` (every item under `pos`,
        already stacking-order-sorted top-to-bottom), not
        `self.itemAt(pos)` (only the single topmost) -- a real,
        confirmed bug: `power_intent_view`'s own Milestone-2 overlay
        items (`SwitchIconItem`/`SupplyRailItem` in `diagram_overlay.py`)
        are bare top-level scene items with no parent at all
        (`scene.addItem(...)`, never `setParentItem()`), at a Z-value
        above `InstanceBoxItem`'s own -- when one of those geometrically
        happens to sit over a box (a real, common case: a power switch
        icon is positioned at a domain's *region* top edge, which can
        coincide with a member box underneath it, macros included), the
        single-topmost `itemAt()` call used to return that overlay item,
        and walking `parentItem()` from it hit `None` immediately (it
        has no parent to walk up to), so this method returned `None` --
        "no box here" -- even though a real box existed directly
        underneath. Same "walk the whole stack, not just the top"
        pattern already established in power_intent_diagram_view.py's
        own `_connector_handle_at()`/`_supply_port_icon_at()` for the
        analogous stacking-order pitfall with *parented* deep items --
        this fixes the equivalent problem for *unparented* siblings."""
        for item in self.items(pos):
            candidate = item
            while candidate is not None and not isinstance(candidate, InstanceBoxItem):
                candidate = candidate.parentItem()  # e.g. a click that landed on the box's label, not its rect
            if candidate is not None:
                return candidate
        return None

    def _hide_targets(self, box: InstanceBoxItem) -> frozenset[str]:
        """Right-clicking a box that's part of the current multi-selection
        targets the whole selection, not just the box under the cursor --
        same convention as the Tree's own context menu (see
        HierarchyTreeWidget._on_context_menu())."""
        if box.path in self._selected_paths and len(self._selected_paths) > 1:
            return self._selected_paths
        return frozenset({box.path})

    def _copy_path(self, path: str) -> None:
        """"Copy Path" context menu action -- the extracted, directly-
        testable half (menu.exec() itself can't be driven headlessly).
        Same method name on HierarchyTreeWidget for the same action
        there."""
        QGuiApplication.clipboard().setText(path)

    def _extra_context_menu_actions(self, menu: QMenu, box: InstanceBoxItem) -> list:
        """Hook for a subclass to append its own items after Copy Path/Hide
        -- returns [(action, callback)] pairs, dispatched by _on_context_menu()
        if chosen. `callback` may be None for an action added disabled
        (menu.exec() never returns a disabled QAction as the chosen one
        anyway, but the dispatch loop tolerates it regardless). Empty by
        default; this base class (Visualize Design's own diagram) offers
        nothing extra. Kept as a hook rather than each subclass
        reimplementing _on_context_menu() wholesale, so Copy Path/Hide's
        own logic (including the multi-select targeting rule) stays
        defined in exactly one place."""
        return []

    def _build_context_menu(self, box: InstanceBoxItem) -> tuple:
        """Builds the right-click menu for `box` without ever calling
        menu.exec() -- split out from _on_context_menu() so a test can
        inspect the built actions' own enabled state directly (that
        method's own menu.exec() is a blocking modal call that can't be
        driven headlessly, same constraint this class's own module
        docstring already documents for Copy Path/Hide).

        Explicit request: "Copy Path" follows the SAME "visible, greyed
        out -- not omitted" convention every other multi-select-
        incompatible action here already establishes (Edit/Remove Power
        Domain, Add Power Supply, etc. -- see power_intent_diagram_view.py's
        own _extra_context_menu_actions()) rather than disappearing from
        the menu outright."""
        target_paths = self._hide_targets(box)
        menu = QMenu(self)
        copy_action = menu.addAction("Copy Path")
        copy_action.setEnabled(len(target_paths) == 1)
        hide_action = menu.addAction("Hide")
        extra_actions = self._extra_context_menu_actions(menu, box)
        return menu, copy_action, hide_action, extra_actions, target_paths

    def _on_context_menu(self, pos) -> None:
        box = self._box_at(pos)
        if box is None:
            return
        menu, copy_action, hide_action, extra_actions, target_paths = self._build_context_menu(box)
        chosen = menu.exec(self.viewport().mapToGlobal(pos))
        if chosen is copy_action:
            self._copy_path(next(iter(target_paths)))
        elif chosen is hide_action:
            self._controller.hideRequested.emit(target_paths)
        else:
            for action, callback in extra_actions:
                if chosen is action:
                    if callback is not None:
                        callback()
                    return

    def _fit_scale(self) -> Optional[float]:
        """The scale factor Fit View would land on for the current
        scene/viewport -- the floor wheelEvent()'s zoom-out won't go
        below. None when there's nothing to compute against.

        Mirrors QGraphicsView.fitInView()'s own formula exactly, margin
        included (Qt's real implementation insets the viewport rect by 2px
        on each side before computing the ratio) -- confirmed against Qt's
        source after an initial version without the margin landed a
        measurably different (~0.6%) scale than fit_view() itself actually
        produces, which would have made the clamp floor inconsistent with
        what Fit View does."""
        scene = self.scene()
        if scene is None:
            return None
        scene_rect = scene.itemsBoundingRect()
        margin = 2
        viewport_rect = self.viewport().rect().adjusted(margin, margin, -margin, -margin)
        if scene_rect.width() <= 0 or scene_rect.height() <= 0 or viewport_rect.width() <= 0 or viewport_rect.height() <= 0:
            return None
        return min(viewport_rect.width() / scene_rect.width(), viewport_rect.height() / scene_rect.height())

    def wheelEvent(self, event: QWheelEvent) -> None:
        if event.angleDelta().y() > 0:
            self.scale(_ZOOM_FACTOR, _ZOOM_FACTOR)
        else:
            min_scale = self._fit_scale()
            current_scale = self.transform().m11()
            if min_scale is not None and current_scale <= min_scale:
                pass  # already at/below the floor -- zooming out further is a no-op, never a zoom-in
            elif min_scale is not None and current_scale / _ZOOM_FACTOR < min_scale:
                self.fit_view()  # the next normal step would overshoot the floor -- snap exactly to it
            else:
                self.scale(1 / _ZOOM_FACTOR, 1 / _ZOOM_FACTOR)
        event.accept()

    def _pan_enabled(self) -> bool:
        """Whether a plain click-drag on empty canvas should pan the view
        -- True unconditionally here, this class's own only interaction
        mode. Overridden by _PowerIntentGraphicsView to gate this behind
        its own DiagramMode.PAN (explicit request: "Pan now owns that
        responsibility exclusively" -- Regular Cursor/Select no longer
        pans on drag, only a dedicated Pan tool does), without touching
        this class's own standalone behavior at all (still used directly,
        with no DiagramMode concept, by the hidden-but-kept
        VisualizeDesignTab). Consulted at two points, kept in sync
        deliberately: mousePressEvent() (whether to arm `_pan_start` at
        all) and mouseReleaseEvent() (whether a release should reset the
        cursor to the open-hand "at rest" shape -- a real bug, found
        live, from only gating the first of the two: every left-click
        release was resetting the cursor regardless of mode, stomping
        over whatever set_mode() had actually set)."""
        return True

    def _set_own_cursor(self, shape: Qt.CursorShape) -> None:
        """Overridable hook for the pan-cursor feedback below (open hand
        at rest, closed hand while actively panning) -- this class's own
        default sets it directly on itself, matching its single-cursor
        world. _PowerIntentGraphicsView overrides this to redirect to
        `self.viewport()` instead, since its own per-tool cursor scheme
        (see set_mode()) lives there exclusively -- without this hook,
        the pan-cursor feedback below would set a cursor that's shadowed
        by the viewport's own and never actually show."""
        self.setCursor(shape)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.RightButton:
            # Explicit request: right-click + context menu must be a
            # non-destructive, read-only inspection of the current
            # selection -- the yellow-fluo highlight must stay exactly
            # as it was, whether the right-click lands on an already-
            # selected item or an unselected one. Real, confirmed bug
            # (reproduced against a bare QGraphicsScene/QGraphicsView,
            # zero custom code involved): Qt's own default
            # QGraphicsScene mouse handling clears the ENTIRE scene
            # selection on ANY button press, including a right-click on
            # an item that's already part of a multi-selection -- not
            # just the item that isn't selected. Skipping the scene
            # forwarding entirely for a right-click sidesteps this
            # unconditionally; the context menu itself is driven through
            # a genuinely separate mechanism (customContextMenuRequested/
            # contextMenuEvent, Qt's own native right-click translation,
            # independent of mousePressEvent -- confirmed empirically:
            # skipping this forwarding doesn't affect it at all), so
            # nothing about "Copy Path"/"Hide"/every other context-menu
            # action here is affected.
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton and self._pan_enabled():
            self._pan_start = event.position().toPoint()
            self._panning = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._pan_start is not None and event.buttons() & Qt.MouseButton.LeftButton:
            current = event.position().toPoint()
            delta = current - self._pan_start
            if not self._panning and delta.manhattanLength() > _PAN_THRESHOLD_PX:
                self._panning = True
                self._set_own_cursor(Qt.CursorShape.ClosedHandCursor)
            if self._panning:
                h_bar = self.horizontalScrollBar()
                v_bar = self.verticalScrollBar()
                h_bar.setValue(h_bar.value() - delta.x())
                v_bar.setValue(v_bar.value() - delta.y())
                self._pan_start = current
                event.accept()
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            was_panning = self._panning
            self._pan_start = None
            self._panning = False
            # A real, confirmed bug: this used to run unconditionally,
            # regardless of _pan_enabled() -- meaning every plain left-
            # click release on a _PowerIntentGraphicsView (in ANY mode,
            # not just Pan) reset the viewport's own cursor to
            # OpenHandCursor, stomping over whatever set_mode() had
            # actually set (e.g. the plain arrow for Select). Gating this
            # the same way mousePressEvent()'s own _pan_start arming
            # already is keeps this class's own standalone behavior
            # unchanged (pan always enabled there, so this cursor reset
            # is its correct permanent "at rest" cursor either way).
            if self._pan_enabled():
                self._set_own_cursor(Qt.CursorShape.OpenHandCursor)
            if was_panning:
                event.accept()
                return  # swallow the release so it isn't also treated as a click-to-select
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        """Explicit request: double-click a box to open its own
        sub-hierarchy diagram tab. Only reports the gesture
        (openSubHierarchyRequested) -- see module docstring."""
        if event.button() == Qt.MouseButton.LeftButton:
            box = self._box_at(event.position().toPoint())
            if box is not None:
                self.openSubHierarchyRequested.emit(box.path)
                event.accept()
                return
        super().mouseDoubleClickEvent(event)

    def closeEvent(self, event) -> None:
        if self.scene() is not None:
            self.scene().clearSelection()  # see module docstring
        super().closeEvent(event)
