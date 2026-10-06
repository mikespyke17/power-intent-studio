"""Same hub-and-spoke selection pattern as power_intent_view's
PowerIntentSelectionController -- see that module's docstring for why
`setSelection()` never re-emits. This one carries a set of hierarchy
paths instead of a (kind, id) pair.

selectionChanged carries the *complete* current selection (a
frozenset[str] of instance paths), not a single "the" selected path --
multi-select (Ctrl+click, symmetric across the Hierarchy Tree and the
Diagram -- see hierarchy_tree_widget.py's and hierarchy_diagram_view.py's
own docstrings) needs to represent "these N nodes are selected," and an
empty set is itself meaningful (Ctrl-click deselecting the last
remaining item clears every listener's highlight, rather than leaving
stale state behind). Same "re-emit the whole set, not an incremental
event" reasoning as visibilityChanged below.

visibilityChanged carries the *complete* current set of explicitly
hidden top-level paths (a frozenset[str]), not an incremental add/remove
event -- the Hierarchy Tree (hierarchy_tree_widget.py) owns the
authoritative hidden-path state and re-emits the whole set on every
Hide/Unhide, so the Diagram (hierarchy_diagram_view.py) never needs to
duplicate that bookkeeping or risk drifting out of sync with it.

hideRequested is the Diagram's own right-click "Hide" asking the Tree
(the authoritative owner) to add one or more paths to that hidden set --
a frozenset[str], not a single path, since it's multi-select-aware the
same way the Tree's own Hide/Unhide context menu is (right-clicking a
box that's part of the current multi-selection targets every selected
box, not just the one under the cursor -- see
hierarchy_diagram_view.py's own docstring). The Diagram never mutates
hidden state itself, same reasoning as above. It's one-directional and
hide-only (never toggles to unhide): a box already gone from the diagram
can't be right-clicked there to bring it back, so that action only ever
makes sense from the Tree's own context menu."""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal


class HierarchySelectionController(QObject):
    selectionChanged = Signal(object)  # frozenset[str] of currently selected instance paths
    visibilityChanged = Signal(object)  # frozenset[str] of explicitly hidden top-level paths
    hideRequested = Signal(object)  # frozenset[str] of paths -- Diagram's own right-click "Hide" request
