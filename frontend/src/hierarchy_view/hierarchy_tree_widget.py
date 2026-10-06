"""QTreeWidget wrapper for the Visualize Design tab's Hierarchy Tree
group box, built from the mock Design's Instance graph via
Design.walk() -- the same traversal pattern used against real data later.

Right-click Hide/Unhide: this widget owns the authoritative set of
explicitly hidden top-level paths (`_hidden_paths`) and re-emits the
whole set via `controller.visibilityChanged` on every toggle -- see
selection_controller.py's docstring for why it's the full set, not an
incremental event. Here in the tree, "hidden" means the item's own text
(and every descendant's) turns light grey -- the item itself always
stays in the tree, still clickable/selectable. hierarchy_diagram_view.py
reacts to the same signal differently: there, a hidden instance's box
disappears from the diagram entirely rather than dimming in place.

Un-hiding clears the ForegroundRole override entirely (setData(..., None))
rather than resetting it to a hardcoded black -- a real bug, not just a
style nit: hardcoded black text is invisible/low-contrast against a dark
theme's row background, which made the tree look broken/unresponsive
even though selection itself was working underneath. Clearing the
override lets each item fall back to the palette's real (light-or-dark-
aware) text color instead.

The bottom-right "Clear" button (wired up in visualize_design_tab.py,
calling clear_hidden() here) unhides everything in one step.

hide_paths() answers the Diagram's own right-click "Hide" (controller.
hideRequested, now a frozenset[str] -- also multi-select-aware, see
below) -- add-only, never toggles, since the Diagram can't right-click
something back into visibility once its box is gone (see
hideRequested's own docstring on selection_controller.py).

Interface instances (Definition.is_interface=True) are never shown here
at all -- an interface is just a bundle of wiring with modport
constraints, not a block of logic, so it doesn't belong in an
*instance hierarchy* tree the way a real module does. Its signals show up
instead as extra rows in the Properties panel's PortList tab, wherever a
module has an interface-typed port referencing it (see
properties_table_widget.py). design.walk() itself is unfiltered (it's a
generic traversal utility other callers may still want raw); the
filtering happens here, at this specific consumer.

Multi-select: ExtendedSelection mode, so Ctrl+click toggles individual
rows in/out of the selection (Qt's own standard behavior for this mode --
nothing hand-rolled here). Symmetric with the Diagram (Ctrl+click also
multi-selects boxes there, via Qt's native QGraphicsScene click handling
-- see hierarchy_diagram_view.py): whichever side you Ctrl+click in,
controller.selectionChanged carries the *complete* current set of
selected paths (a frozenset[str], possibly empty -- an empty set is
itself meaningful, e.g. Ctrl-clicking the last selected item off clears
every listener's highlight), and both sides converge to the same set.
Right-click Hide/Unhide is multi-select-aware: if the right-clicked row
is itself part of the current multi-selection, the action (and its
Hide/Unhide label) applies to every selected row, not just the one under
the cursor -- matches the common tree/list UX convention (most file
managers, IDEs) where right-clicking *inside* an existing multi-selection
acts on the whole selection, but right-clicking a row *outside* it first
collapses the selection down to just that row. The label reads "Unhide"
only when *every* targeted row is currently hidden; otherwise it reads
"Hide" and hides all of them (a harmless no-op for any already-hidden
ones in the batch). See _apply_hide_action() -- the shared bulk-mutation
core both this and hide_paths()/clear_hidden() go through, so a
multi-row toggle mutates `_hidden_paths` and emits `visibilityChanged`
exactly once, not once per row (which would otherwise trigger one full
Diagram scene rebuild per row).

Unhiding a path also unhides every *other* `_hidden_paths` entry nested
underneath it, not just that one path -- real scenario: "top" gets
explicitly hidden, then (redundantly, but not prevented) "top/u_core"
also gets explicitly hidden on its own while it's already covered by
"top"'s own hide; un-hiding "top" alone must bring the whole assembly
back, not leave "top/u_core" stuck hidden because it has its own,
now-orphaned entry. See _apply_hide_action()'s own docstring for the
exact purge rule.

Right-click also offers "Copy Path" (single-target only -- omitted
whenever the context menu would otherwise target more than one row via
the multi-select rule above, since there's no single unambiguous path to
copy), copying the full hierarchy path to the clipboard. Same feature,
same single-target restriction, on the Diagram's own right-click menu
(hierarchy_diagram_view.py).

Also offers "Visualize Diagram" (single-target only, same restriction as
Copy Path) -- explicit request: opens (or switches to) a diagram tab
rooted at the right-clicked instance, via
`visualizeDiagramRequested(path)`, wired by whichever tab owns this
widget straight to its own Diagram's HierarchyDiagramTabs.open_path()
(see hierarchy_diagram_tabs.py). This widget has no opinion on what
"visualize" means beyond reporting the request -- same pattern as
HierarchyDiagramView's own openSubHierarchyRequested for the double-
click gesture.

Hide/Unhide is undoable: `undo_stack` (a real QUndoStack) records one
QUndoCommand per _apply_hide_action() call that actually changes
anything -- a whole multi-row Hide/Unhide (or a cascaded Unhide that
also purges nested entries) is one undo step, not one per path, matching
"one user action = one undo step." VisualizeDesignTab wires Ctrl+Z/Ctrl+Y
to it; selection changes are deliberately NOT part of this stack (not
the kind of thing a user expects Ctrl+Z to reverse).
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QItemSelectionModel, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QGuiApplication, QUndoCommand, QUndoStack
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QMenu, QTreeWidget, QTreeWidgetItem, QWidget

from frontend.src.hierarchy_view.selection_controller import HierarchySelectionController
from frontend.src.shared.models.rtl import Design

_PATH_ROLE = Qt.ItemDataRole.UserRole
_HIDDEN_TEXT = QBrush(QColor("#a0a0a0"))


class _HideUnhideCommand(QUndoCommand):
    """One Hide/Unhide/Clear action (possibly multi-row, possibly with a
    cascaded nested-entry purge) as a single undo step -- stores full
    before/after snapshots of `_hidden_paths` rather than replaying the
    add/discard ops, since the purge rule makes "undo" not simply the
    inverse of "redo" op-by-op."""

    def __init__(self, tree: "HierarchyTreeWidget", before: frozenset[str], after: frozenset[str], label: str):
        super().__init__(label)
        self._tree = tree
        self._before = before
        self._after = after

    def redo(self) -> None:
        self._tree._set_hidden_paths(self._after)

    def undo(self) -> None:
        self._tree._set_hidden_paths(self._before)


class HierarchyTreeWidget(QTreeWidget):
    visualizeDiagramRequested = Signal(str)  # right-clicked instance path

    def __init__(self, controller: HierarchySelectionController, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._controller = controller
        self.setHeaderLabels(["Instance", "Module"])
        # Auto-size both columns to their content, same convention as
        # Properties' own tables (hierarchy_view/properties_table_widget.py,
        # power_intent_view/properties_panel.py) -- a persistent mode, not
        # a one-shot call, so it keeps re-fitting as load()/expandAll()
        # change row content, with no eliding/tooltip needed (grows instead,
        # matching that same precedent).
        self.header().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._items_by_path: dict[str, QTreeWidgetItem] = {}
        self._hidden_paths: set[str] = set()
        self.undo_stack = QUndoStack(self)
        self.itemSelectionChanged.connect(self._on_item_selected)
        controller.selectionChanged.connect(self.apply_selection)
        controller.hideRequested.connect(self.hide_paths)

        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context_menu)

    def load(self, design: Design) -> None:
        self.clear()
        self._items_by_path = {}
        self._hidden_paths = set()
        self.undo_stack.clear()  # a fresh design load must not let Ctrl+Z reach into a prior design's hide state
        parents: dict[str, QTreeWidgetItem] = {}

        for node in design.walk():
            if node.definition.is_interface:
                continue
            label = node.instance.name if node.instance is not None else node.definition.base_name
            item = QTreeWidgetItem([label, node.definition.base_name])
            item.setData(0, _PATH_ROLE, node.path)
            parent_path = node.path.rsplit("/", 1)[0] if "/" in node.path else None
            if parent_path is not None and parent_path in parents:
                parents[parent_path].addChild(item)
            else:
                self.addTopLevelItem(item)
            parents[node.path] = item
            self._items_by_path[node.path] = item

        self.expandAll()

    def _on_item_selected(self) -> None:
        paths = frozenset(item.data(0, _PATH_ROLE) for item in self.selectedItems())
        self._controller.selectionChanged.emit(paths)

    def apply_selection(self, paths) -> None:
        """controller.selectionChanged handler. Named apply_selection(),
        not setSelection() -- QTreeWidget (via QAbstractItemView) already
        has its own built-in virtual setSelection(QRect, SelectionFlags),
        used internally for click/rubber-band selection; a same-named
        override with an incompatible signature silently shadowed it and
        broke every real mouse click in this widget (Qt's internal call
        failed with "takes 2 positional arguments but 3 were given," a
        real, previously-shipped bug, not hypothetical). See
        HierarchyDiagramView.apply_selection() and
        HierarchyPropertiesTableWidget.apply_selection() for the sibling
        methods this was renamed to match, even though neither of their
        base classes (QGraphicsView, QTabWidget) actually collides."""
        paths = frozenset(paths)
        self.blockSignals(True)
        self.clearSelection()
        items = [self._items_by_path[p] for p in paths if p in self._items_by_path]
        for item in items:
            item.setSelected(True)
        if items:
            # NoUpdate: moves the keyboard-focus/"current" indicator to
            # the last item without touching selection state -- plain
            # setCurrentItem() defaults to a ClearAndSelect command that
            # would wipe every other item's setSelected(True) above back
            # down to just this one (confirmed directly), breaking a
            # multi-path restore.
            self.setCurrentItem(items[-1], 0, QItemSelectionModel.SelectionFlag.NoUpdate)
        self.blockSignals(False)

    def _hide_targets(self, item: QTreeWidgetItem) -> list[str]:
        """Right-clicking a row that's part of the current multi-selection
        targets every selected row, not just the one under the cursor;
        right-clicking a row outside it (or with nothing/one row selected)
        targets just that row -- see module docstring. Same convention,
        same method name, as HierarchyDiagramView._hide_targets()."""
        selected_items = self.selectedItems()
        target_items = selected_items if item in selected_items and len(selected_items) > 1 else [item]
        return [i.data(0, _PATH_ROLE) for i in target_items]

    def _copy_path(self, path: str) -> None:
        """"Copy Path" context menu action -- the extracted, directly-
        testable half (menu.exec() itself can't be driven headlessly).
        Same method name on HierarchyDiagramView for the same action
        there."""
        QGuiApplication.clipboard().setText(path)

    def _on_context_menu(self, pos) -> None:
        item = self.itemAt(pos)
        if item is None:
            return
        target_paths = self._hide_targets(item)

        menu = QMenu(self)
        copy_action = menu.addAction("Copy Path") if len(target_paths) == 1 else None
        visualize_action = menu.addAction("Visualize Diagram") if len(target_paths) == 1 else None
        all_hidden = all(p in self._hidden_paths for p in target_paths)
        hide_action = menu.addAction("Unhide" if all_hidden else "Hide")
        chosen = menu.exec(self.viewport().mapToGlobal(pos))
        if chosen is copy_action:
            self._copy_path(target_paths[0])
        elif chosen is visualize_action:
            self.visualizeDiagramRequested.emit(target_paths[0])
        elif chosen is hide_action:
            self._apply_hide_action(target_paths, hide=not all_hidden)

    def _toggle_hidden(self, path: str) -> None:
        """Single-path Hide/Unhide -- kept as the directly-testable unit
        both existing tests and _on_context_menu's own single-row case
        use (menu.exec() itself can't be driven headlessly)."""
        self._apply_hide_action([path], hide=path not in self._hidden_paths)

    def _apply_hide_action(self, paths: list[str], hide: bool) -> None:
        """Shared bulk-mutation core for _toggle_hidden(), hide_paths(),
        and clear_hidden() -- computes the *complete* resulting
        `_hidden_paths` set for every path in `paths` at once (not one
        path at a time), pushes a single _HideUnhideCommand onto
        `undo_stack` if anything actually changes (QUndoStack.push()
        calls the command's redo() immediately, which is what actually
        applies the new state -- see _set_hidden_paths()), and is a
        no-op (no command pushed, nothing emitted) if nothing in `paths`
        changes anything.

        Un-hiding also purges every *other* `_hidden_paths` entry nested
        under the path being unhidden -- otherwise a redundant, more
        specific entry underneath (see module docstring's "top" /
        "top/u_core" scenario) would survive un-hiding the container
        above it and stay incorrectly hidden."""
        new_hidden = set(self._hidden_paths)
        for path in paths:
            if hide:
                new_hidden.add(path)
            else:
                new_hidden.discard(path)
                new_hidden -= {p for p in new_hidden if p.startswith(f"{path}/")}
        if new_hidden == self._hidden_paths:
            return
        before = frozenset(self._hidden_paths)
        after = frozenset(new_hidden)
        label = "Hide" if hide else "Unhide"
        self.undo_stack.push(_HideUnhideCommand(self, before, after, label))

    def _set_hidden_paths(self, paths: frozenset[str]) -> None:
        """The actual state mutation -- called only by _HideUnhideCommand's
        redo()/undo(), never directly, so every Hide/Unhide change goes
        through the undo stack uniformly."""
        self._hidden_paths = set(paths)
        self._apply_greying()
        self._controller.visibilityChanged.emit(frozenset(self._hidden_paths))

    def hidden_paths(self) -> list[str]:
        """The explicitly hidden paths, sorted -- what Save Project
        stores."""
        return sorted(self._hidden_paths)

    def restore_hidden_paths(self, paths) -> None:
        """Re-apply a saved project's hidden paths after `load()`.

        NOT pushed onto the undo stack: it is part of loading the
        project, and Ctrl+Z must not 'unhide' what the file says is
        hidden (load() has just cleared the stack for the same reason).
        A path that no longer names a hierarchy in this design (the RTL
        changed since the save) is dropped rather than kept as an
        invisible entry."""
        known = [p for p in paths if p in self._items_by_path]
        if set(known) != self._hidden_paths:
            self._set_hidden_paths(frozenset(known))

    def hide_paths(self, paths) -> None:
        """controller.hideRequested handler -- the Diagram's own
        right-click Hide, now multi-select-aware there too (see
        HierarchyDiagramView._on_context_menu()). Add-only (never
        removes/toggles), same idempotency reasoning as clear_hidden()."""
        self._apply_hide_action(list(paths), hide=True)

    def clear_hidden(self) -> None:
        """'Clear' button handler -- unhides every explicitly hidden
        path in one step. A no-op (no signal emitted) if nothing is
        currently hidden, so clicking it repeatedly doesn't churn the
        Diagram's scene rebuild for no reason -- _apply_hide_action()'s
        own no-op-if-unchanged guard covers this for free."""
        self._apply_hide_action(list(self._hidden_paths), hide=False)

    def _apply_greying(self) -> None:
        for path, item in self._items_by_path.items():
            greyed = any(path == hidden or path.startswith(f"{hidden}/") for hidden in self._hidden_paths)
            for column in (0, 1):
                if greyed:
                    item.setForeground(column, _HIDDEN_TEXT)
                else:
                    item.setData(column, Qt.ItemDataRole.ForegroundRole, None)  # back to the theme's own color
