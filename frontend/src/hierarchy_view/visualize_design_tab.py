"""'Visualize Design' tab: three group boxes -- Hierarchy Tree, Diagram,
Properties -- side by side in a resizable QSplitter (same horizontal-
splitter pattern as hierarchy_view/load_design_tab.py), kept in sync via
HierarchySelectionController. Enabled only once 'Load Design' succeeds
(see app/main_window.py).

Diagram gets more horizontal space by default than its two neighbors --
both an initial 1:3:1 QSplitter.setSizes() split and matching
setStretchFactor()s, so it stays proportionally larger as the window
resizes too, not just on first show.

The Diagram box's top-right row holds only "Fit View" now -- zoom is
mouse-wheel-driven and panning is click-drag (see
HierarchyDiagramView's own docstring); no on-screen zoom buttons. The
Hierarchy Tree box's bottom-right "Clear" button unhides everything
(HierarchyTreeWidget.clear_hidden()) -- the per-item Hide/Unhide toggle
itself lives on the tree's own right-click menu, not here.

Ctrl+Z/Ctrl+Y (undo/redo Hide/Unhide -- see HierarchyTreeWidget's own
`undo_stack`) are wired here, not on the Tree widget itself, via
QShortcut with the default WindowShortcut context -- so they fire
regardless of which of the three group boxes currently has keyboard
focus, not only when the Tree happens to be focused. Explicit
"Ctrl+Y" for redo rather than QKeySequence.Redo, which resolves to
Ctrl+Shift+Z on some platforms -- the user asked for Ctrl+Y specifically.

`self.diagram` is a HierarchyDiagramTabs (explicit request: double-click
a box, or "Visualize Diagram" from the Tree's own context menu, opens
that instance's own sub-hierarchy in a new closable tab; a single
always-open, non-closable tab shows the whole design) -- not a bare
HierarchyDiagramView directly anymore. Design Properties keeps working
regardless of which diagram tab is active since it's driven entirely by
the one shared HierarchySelectionController every tab's view is
constructed against, same as the Tree -- nothing tab-specific needed
there at all.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QDockWidget, QGroupBox, QHBoxLayout, QMainWindow, QPushButton, QVBoxLayout, QWidget

from frontend.src.hierarchy_view.hierarchy_diagram_view import HierarchyDiagramView
from frontend.src.hierarchy_view.hierarchy_tree_widget import HierarchyTreeWidget
from frontend.src.hierarchy_view.properties_table_widget import HierarchyPropertiesTableWidget
from frontend.src.hierarchy_view.selection_controller import HierarchySelectionController
from frontend.src.shared.diagram.hierarchy_diagram_tabs import HierarchyDiagramTabs
from frontend.src.shared.models.rtl import Design


class VisualizeDesignTab(QMainWindow):
    """Explicit request: Hierarchy Tree and Design Properties are
    QDockWidgets now (closable/reopenable independently, visible by
    default), not QGroupBoxes inside a QSplitter -- Diagram stays a
    plain QGroupBox as this tab's own central widget. This tab hosting
    dock widgets of its own is exactly why it's a QMainWindow now, not a
    QWidget -- a QMainWindow nests perfectly well as one page of the
    outer app/main_window.py's own QTabWidget, it doesn't need to *be*
    the application's own top-level window to support docking. Never
    calls self.menuBar() -- only the real top-level MainWindow should
    ever have a menu bar; this one would otherwise get its own, separate
    (and empty) one for free just by being a QMainWindow."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._controller = HierarchySelectionController()
        self._design: Optional[Design] = None

        self.tree = HierarchyTreeWidget(self._controller)
        self.diagram = HierarchyDiagramTabs(lambda: HierarchyDiagramView(self._controller))
        self.tree.visualizeDiagramRequested.connect(self.diagram.open_path)
        self.properties = HierarchyPropertiesTableWidget(self._controller)

        self.fit_view_button = QPushButton("Fit View")
        self.fit_view_button.clicked.connect(self.diagram.fit_view)

        self.clear_hidden_button = QPushButton("Clear")
        self.clear_hidden_button.clicked.connect(self.tree.clear_hidden)

        self.undo_shortcut = QShortcut(QKeySequence("Ctrl+Z"), self)
        self.undo_shortcut.activated.connect(self.tree.undo_stack.undo)
        self.redo_shortcut = QShortcut(QKeySequence("Ctrl+Y"), self)
        self.redo_shortcut.activated.connect(self.tree.undo_stack.redo)

        diagram_box = QGroupBox("Design Diagram")
        diagram_layout = QVBoxLayout(diagram_box)
        diagram_top_row = QHBoxLayout()
        diagram_top_row.addStretch(1)
        diagram_top_row.addWidget(self.fit_view_button)
        diagram_layout.addLayout(diagram_top_row)
        diagram_layout.addWidget(self.diagram)
        self.setCentralWidget(diagram_box)

        tree_container = QWidget()
        tree_layout = QVBoxLayout(tree_container)
        tree_layout.setContentsMargins(0, 0, 0, 0)
        tree_layout.addWidget(self.tree)
        tree_bottom_row = QHBoxLayout()
        tree_bottom_row.addStretch(1)
        tree_bottom_row.addWidget(self.clear_hidden_button)
        tree_layout.addLayout(tree_bottom_row)
        self.tree_dock = QDockWidget("Hierarchy Tree")
        self.tree_dock.setWidget(tree_container)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.tree_dock)

        self.properties_dock = QDockWidget("Design Properties")
        self.properties_dock.setWidget(self.properties)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.properties_dock)

    def load(self, design: Design) -> None:
        self._design = design
        self.tree.load(design)
        self.diagram.reset(design, populate=lambda view, root: view.load(design, root=root))
        self.properties.load(design)
