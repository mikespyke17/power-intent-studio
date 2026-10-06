"""Hub-and-spoke selection sync between the power-intent diagram overlay
and the properties panel.

Exactly one thing may ever call `selectionChanged.emit()`: a widget's own
user-interaction handler (a diagram click, a properties-table row click).
`setSelection()` on every subscriber -- including the widget that
originated the emit -- is a pure visual update that never re-emits. This
hub-and-spoke shape (not widget-to-widget direct connections) is what
prevents the obvious infinite-loop bug: if `setSelection()` itself could
emit, a diagram click would update the properties panel, which would
re-emit, which would update the diagram again, forever.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal


class PowerIntentSelectionController(QObject):
    selectionChanged = Signal(str, str)  # construct_kind, construct_id
