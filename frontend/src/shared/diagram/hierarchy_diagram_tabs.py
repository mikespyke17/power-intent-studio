"""Tab container for a "Diagram" group box (Visualize Design and Power
Intent both use one) -- one always-open, non-closable tab for the whole
design, plus any number of user-opened sub-hierarchy tabs (double-click
a box in any open diagram tab, or "Visualize Diagram" from the Hierarchy
Tree's own context menu -- see hierarchy_tree_widget.py), each its own
independently pannable/zoomable view rooted at that instance instead of
the design's own top.

Deliberately generic over *which* view type it hosts (plain
HierarchyDiagramView for Visualize Design, the Power-Intent-specific
subclass for the other tab) -- this container only ever calls the base
class's own public interface (`load()`/`openSubHierarchyRequested`/
`fit_view()`/`refresh()`), never anything overlay-specific. Two
callbacks carry everything type-specific:

- `view_factory()` constructs one fresh, empty view. Called once at
  construction (the root tab) and again every time a new sub-hierarchy
  tab is opened. Power Intent's own factory closes over one shared
  SupplyPortDiagramState object (see power_intent_diagram_view.py) so
  every tab -- root or sub-hierarchy -- renders against the exact same
  supply-port placements; that's what makes a supply port added while
  looking at a sub-module's tab show up in the whole-hierarchy tab too
  (explicit request), not a separate mechanism.
- `populate(view, root)` renders one view for a given root node (None
  for the design's own top). Power Intent's own populate calls
  `view.load_mapped(mapped, root=...)` (the full power-intent overlay);
  Visualize Design's calls the plain `view.load(design, root=...)`. This
  container never needs to know which -- reset()/open_path() just call
  whatever was handed to reset().

Tab naming: the full instance path, elided by Qt's own QTabBar (never a
hand-rolled truncation) with the un-elided path set as the tab's tooltip
-- explicit request: hovering the tab shows the full hierarchy path next
to the cursor even when the label itself is cut short.

Switching tabs calls the newly-current view's own refresh() -- supply-
port state can change while a *different* tab is active (no signal
fires for that, unlike Hide/Unhide which already propagates live to
every tab via the shared HierarchySelectionController), so this
guarantees whatever's currently visible is never stale by the time you
actually look at it.
"""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QTabBar, QTabWidget, QWidget

from frontend.src.hierarchy_view.hierarchy_diagram_view import HierarchyDiagramView
from frontend.src.shared.models.rtl import Design, HierarchyNode

PopulateCallback = Callable[[HierarchyDiagramView, Optional[HierarchyNode]], None]


class HierarchyDiagramTabs(QTabWidget):
    def __init__(self, view_factory: Callable[[], HierarchyDiagramView], parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._view_factory = view_factory
        self._design: Optional[Design] = None
        self._populate: Optional[PopulateCallback] = None
        self._views_by_path: dict[str, HierarchyDiagramView] = {}

        self.setTabsClosable(True)
        self.setElideMode(Qt.TextElideMode.ElideMiddle)
        self.tabCloseRequested.connect(self._on_tab_close_requested)
        self.currentChanged.connect(self._on_current_changed)

        self._root_view = self._create_view()
        self.addTab(self._root_view, "")
        self.tabBar().setTabButton(0, QTabBar.ButtonPosition.RightSide, None)  # root tab: never closable

    def _create_view(self) -> HierarchyDiagramView:
        view = self._view_factory()
        view.openSubHierarchyRequested.connect(self.open_path)
        return view

    def reset(self, design: Design, populate: PopulateCallback) -> None:
        """Called once per fresh Design/MappedDesign load. Closes every
        previously-open sub-hierarchy tab (their own root instance may
        not even exist in the newly loaded design) and re-renders the
        root tab against the new data via `populate`."""
        self._design = design
        self._populate = populate
        for index in range(self.count() - 1, 0, -1):
            widget = self.widget(index)
            self.removeTab(index)
            widget.deleteLater()
        self._views_by_path = {}

        root_path = next(iter(design.walk())).path
        self._populate(self._root_view, None)
        self._views_by_path[root_path] = self._root_view
        self.setTabText(0, root_path)
        self.setTabToolTip(0, root_path)

    def open_path(self, path: str) -> None:
        """Opens a new sub-hierarchy tab rooted at `path`, or switches to
        it if already open -- the shared handler behind double-click
        (HierarchyDiagramView.openSubHierarchyRequested) and the
        Hierarchy Tree's own "Visualize Diagram" context-menu action."""
        existing = self._views_by_path.get(path)
        if existing is not None:
            self.setCurrentWidget(existing)
            return
        if self._design is None or self._populate is None:
            return
        node = next((n for n in self._design.walk() if n.path == path), None)
        if node is None:
            return
        view = self._create_view()
        self._populate(view, node)
        index = self.addTab(view, path)
        self.setTabToolTip(index, path)
        self._views_by_path[path] = view
        self.setCurrentIndex(index)

    def _on_tab_close_requested(self, index: int) -> None:
        if index == 0:
            return  # root tab is never closable
        widget = self.widget(index)
        path = next((p for p, v in self._views_by_path.items() if v is widget), None)
        if path is not None:
            del self._views_by_path[path]
        self.removeTab(index)
        widget.deleteLater()

    def _on_current_changed(self, index: int) -> None:
        view = self.widget(index)
        if view is not None:
            view.refresh()

    @property
    def current_view(self) -> Optional[HierarchyDiagramView]:
        return self.currentWidget()

    def all_views(self) -> list[HierarchyDiagramView]:
        """Every view currently hosted here (the always-open root tab
        plus any open sub-hierarchy tabs) -- generic, not tied to any
        one view type's own concept (e.g. Power Intent's own diagram
        mode; see power_intent_diagram_view.py's PowerIntentDiagramView.
        set_mode()), matching this class's own "deliberately generic
        over which view type it hosts" principle. Reads the QTabWidget's
        own actual pages (`self.widget(i)`), not `_views_by_path` -- a
        real bug, found by a caller using this before any design was
        ever loaded: `_views_by_path` only starts tracking the root tab
        once reset() first runs, but the root tab itself already exists
        (added in __init__) well before that, so a mode/state change
        applied before the first load would silently never reach it."""
        return [self.widget(i) for i in range(self.count())]

    def fit_view(self) -> None:
        view = self.current_view
        if view is not None:
            view.fit_view()
