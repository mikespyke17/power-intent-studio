"""Makes a floating (undocked) QDockWidget behave like a normal
top-level window -- explicit request: "Properties"/"Hierarchy Tree"
docks that have been dragged out of the main window should be
maximizable/minimizable and should participate in ordinary
click-to-front window stacking with the main window, not stay
permanently above it.

Root cause: QDockWidget's own default floating window uses the
Qt.WindowType.Tool flag (a small title bar, no minimize/maximize
buttons) -- and Tool windows are, by Qt/platform convention, kept
stacked above their owner regardless of focus, which is also why the
main window couldn't be brought back in front of a floating dock by
clicking it.

Only swaps in Qt.Window while actually floating, restoring Qt.Widget
the moment it's re-docked -- a widget carrying Qt.Window is always
treated as its own top-level window regardless of its Qt parent, so
leaving it set while docked would make the dock refuse to re-embed
into the main window's layout.

Changing a widget's window flags after it's already been shown hides
it as a side effect (a new native window has to be created for the new
flags) -- re-`.show()` afterward is required, not optional.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDockWidget

_FLOATING_WINDOW_FLAGS = (
    Qt.WindowType.Window
    | Qt.WindowType.WindowTitleHint
    | Qt.WindowType.WindowSystemMenuHint
    | Qt.WindowType.WindowMinimizeButtonHint
    | Qt.WindowType.WindowMaximizeButtonHint
    | Qt.WindowType.WindowCloseButtonHint
)


def make_dock_a_normal_window_when_floating(dock: QDockWidget) -> None:
    def _on_top_level_changed(floating: bool) -> None:
        dock.setWindowFlags(_FLOATING_WINDOW_FLAGS if floating else Qt.WindowType.Widget)
        dock.show()

    dock.topLevelChanged.connect(_on_top_level_changed)
