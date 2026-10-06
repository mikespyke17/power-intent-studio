"""Shared "Filter" button widget: a checkable-action popup menu over a
fixed set of categories. Originally private to hierarchy_view/
properties_table_widget.py (used twice there, for PortList's Dir filter
and Sequential's Register/Latch filter); extracted here once a third
consumer needed the exact same UI (power_intent_view's Diagram category
filter) rather than reaching into that module's private class or
duplicating the ~15 lines a third time.
"""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtWidgets import QMenu, QPushButton, QWidget


class FilterButton(QPushButton):
    """Checkable-action popup menu button. Every category starts checked
    (show everything by default); toggling one calls `on_change` so the
    owning panel can react -- `on_change` may be a genuine no-op
    (`lambda: None`) for a caller that only wants the menu UI itself,
    with no filtering behavior wired up yet.

    `include_all`, when set (explicit request, Design Properties' own
    PortList/Sequential panels only -- power_intent_view's Diagram filter
    stays plain), adds a leading "All" entry kept bidirectionally in sync
    with the individual categories below it: checking "All" checks every
    category; unchecking any individual category unchecks "All" (a
    partial selection isn't "all" anymore); re-checking every category by
    hand re-checks "All" too. Since every category already starts
    checked, "All" starts checked too -- the requested default.

    `label` overrides the button's own displayed text ("Filter" by
    default) -- Design Properties' own two consumers rename it
    "Visualize" (explicit request), Diagram's stays "Filter"."""

    def __init__(
        self,
        categories: list[str],
        on_change: Callable[[], None],
        parent: Optional[QWidget] = None,
        include_all: bool = False,
        label: str = "Filter",
    ):
        super().__init__(label, parent)
        self._on_change = on_change
        self._menu = QMenu(self)
        self._actions = {}
        self._all_action = None
        self._syncing = False

        if include_all:
            self._all_action = self._menu.addAction("All")
            self._all_action.setCheckable(True)
            self._all_action.setChecked(True)
            self._all_action.toggled.connect(self._on_all_toggled)
            self._menu.addSeparator()

        for category in categories:
            action = self._menu.addAction(category)
            action.setCheckable(True)
            action.setChecked(True)
            action.toggled.connect(self._on_category_toggled)
            self._actions[category] = action
        self.setMenu(self._menu)

    def _on_all_toggled(self, checked: bool) -> None:
        if self._syncing:
            return
        if checked:
            self._syncing = True
            for action in self._actions.values():
                action.setChecked(True)
            self._syncing = False
        self._on_change()

    def _on_category_toggled(self, _checked: bool) -> None:
        if self._syncing:
            return
        if self._all_action is not None:
            self._syncing = True
            self._all_action.setChecked(all(a.isChecked() for a in self._actions.values()))
            self._syncing = False
        self._on_change()

    def active_categories(self) -> set[str]:
        return {name for name, action in self._actions.items() if action.isChecked()}
