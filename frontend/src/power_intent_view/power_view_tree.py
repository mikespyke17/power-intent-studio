"""Power View tab (explicit request, Power Intent tab's Hierarchy Tree
dock split into "Design View"/"Power View" -- see power_intent_tab.py's
own docstring): a tree organized by Power Domain rather than raw RTL
hierarchy -- Supply Set(s), Power Switch(es), Isolation(s), Retention(s),
nested child Power Domain(s), then Macro(s), in that fixed order, applied
uniformly at every nesting level (see the request's own worked example).

Deliberately scoped to the diagram-only mock state
(PowerDomainDiagramState/RetentionDiagramState/IsolationDiagramState/
PowerSwitchDiagramState) -- the SAME scoping every interactive
(Edit/Remove-capable) entity in this diagram already follows (see
power_intent_diagram_view.py's own can_edit_properties_row()/
can_remove_properties_row(): a REAL, backend-loaded PowerIntentDesign
construct has no edit/remove wiring at all here). Section 7's own
right-click Edit/Remove would have nothing to dispatch to for a real
row anyway, so this is the only scope that makes every node here
uniformly interactive, not an arbitrary cut.

Two independent pieces, mirroring this app's own "plain data, then a
Qt widget on top" precedent (e.g. supply_port_overlay.py's own
SupplyPortPlacement vs. SupplyPortIconItem):
  - `build_power_view_tree()` -- pure data, no QWidget involved, so the
    domain-nesting/ordering logic is directly testable without a live
    scene.
  - `PowerViewTree` -- a QTreeWidget rendering that data, wired to the
    SAME PowerIntentSelectionController/HierarchySelectionController
    hub-and-spoke selection mechanism, and the SAME
    edit_properties_row()/remove_properties_row() dispatch, every other
    entry point into this diagram's own construct editing already uses.

Domain containment (explicit request: "based on containment
relationship between domains... walking the existing Power Domain
membership/hierarchy data already used elsewhere") reuses the exact
same owner_paths prefix-matching convention
power_domain_overlay.py's own apply_domain_coloring()/
find_effective_domain_for_path() already establish, generalized from
path-vs-domain to domain-vs-domain -- `_is_nested_in()`/
`_direct_child_domains()` below are near-duplicates of
properties_panel.py's own `_domain_is_nested_in()`/
`_direct_excluded_children()` (same "small duplication over forced
cross-module import" precedent already established repeatedly in this
package, e.g. `_DEFAULT_COLOR` itself).

Color (section 3/4): resolved FRESH on every rebuild, not cached or
pushed via a separate on_recolor_callbacks registration the way a live
Qt diagram icon needs to -- a QTreeWidgetItem has no paint() cycle to
re-derive a color from automatically, but this tree is already rebuilt
wholesale on every `diagramStateChanged` (see PowerViewTree.rebuild()'s
own call site in power_intent_tab.py), which already fires on every
edit that could change a relevant color (Supply Port recolor via Edit
Power Supply, a domain's own Edit Power Domain, etc. -- see
power_intent_diagram_view.py's own edit_supply_port()/
edit_power_domain(), both ending in _notify_changed()). Reuses the
EXACT SAME resolve_isolation_color_source()/resolve_retention_color_source()
functions the diagram's own badges already call, rather than a second,
parallel resolution -- see each's own docstring in
isolation_overlay.py/retention_overlay.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import QHeaderView, QMenu, QTreeWidget, QTreeWidgetItem, QWidget

from frontend.src.hierarchy_view.selection_controller import HierarchySelectionController
from frontend.src.power_intent_view.isolation_overlay import IsolationDiagramState, resolve_isolation_color_source
from frontend.src.power_intent_view.level_shifter_overlay import LevelShifterDiagramState, resolve_level_shifter_color
from frontend.src.power_intent_view.power_domain_overlay import (
    PowerDomainDiagramState,
    PowerDomainPlacement,
    SupplySetPlacement,
    resolve_supply_set_color_source,
)
from frontend.src.power_intent_view.power_switch_overlay import PowerSwitchDiagramState, PowerSwitchPlacement
from frontend.src.power_intent_view.retention_overlay import RetentionDiagramState, resolve_retention_color_source
from frontend.src.power_intent_view.selection_controller import PowerIntentSelectionController
from frontend.src.power_intent_view.supply_port_overlay import SupplyPortIconItem
from frontend.src.shared.diagram.hierarchy_scene_builder import InstanceBoxItem

_DEFAULT_COLOR = QColor("#9e9e9e")  # same "unconnected" default every other supply reference here uses

# Only these kinds have any Edit/Remove backing at all (see module
# docstring) -- same set properties_panel.py's own _EDITABLE_KINDS
# establishes, minus PowerSwitchCell (no node of its own here) plus
# nothing new; Macro is deliberately never editable/removable from this
# tree (it isn't a power-intent construct, just an RTL instance).
_EDITABLE_KINDS = {"PowerDomain", "SupplySet", "PowerSwitch", "IsolationStrategy", "RetentionStrategy", "LevelShifterStrategy"}

# Explicit request: an italic type-identifier label next to every
# element's name, exact text per kind. Deliberately independent of
# `node.color` (section 3's own color-binding) -- rendered via a SECOND
# column (see PowerViewTree.__init__), the same "secondary annotation
# lives in its own adjacent column" convention hierarchy_tree_widget.py's
# own "Instance"/"Module" two-column tree already establishes, so it
# renders immediately next to the name on the same row without needing
# a custom rich-text delegate.
_TYPE_LABELS = {
    "PowerDomain": "(Power Domain)",
    "SupplySet": "(Supply Set)",
    "PowerSwitch": "(Power Switch)",
    "IsolationStrategy": "(Isolation)",
    "LevelShifterStrategy": "(Level Shifter)",
    "RetentionStrategy": "(Retention)",
    "Macro": "(Macro)",
}
_TYPE_LABEL_COLOR = QColor("#9e9e9e")  # neutral/muted -- same grey default this module already uses elsewhere


@dataclass
class PowerViewNode:
    """One row of the Power View tree -- plain data, independent of any
    live QTreeWidgetItem. `construct_id` is whatever id
    apply_construct_selection()/edit_properties_row()/
    remove_properties_row() (power_intent_diagram_view.py) already
    expect for `kind` -- for "Macro" (not a power-intent construct kind
    at all), it's the box's own hierarchy path instead, handled
    separately by PowerViewTree's own selection handler (see its own
    docstring)."""

    kind: str
    construct_id: str
    label: str
    color: Optional[QColor] = None
    children: list["PowerViewNode"] = field(default_factory=list)


def _is_nested_in(inner: PowerDomainPlacement, outer: PowerDomainPlacement) -> bool:
    """True if ANY of `inner`'s own owner_paths falls inside ANY of
    `outer`'s own owner_paths' subtrees -- domain-vs-domain counterpart
    of power_domain_overlay.py's own path-vs-domain
    find_effective_domain_for_path() (see module docstring)."""
    return any(
        inner_path == outer_path or inner_path.startswith(f"{outer_path}/") for inner_path in inner.owner_paths for outer_path in outer.owner_paths
    )


def _root_domains(all_domains: list[PowerDomainPlacement]) -> list[PowerDomainPlacement]:
    """Domains not nested inside any other domain -- the Power View
    tree's own top-level nodes."""
    return [d for d in all_domains if not any(other.id != d.id and _is_nested_in(d, other) for other in all_domains)]


def _direct_child_domains(domain: PowerDomainPlacement, all_domains: list[PowerDomainPlacement]) -> list[PowerDomainPlacement]:
    """Domains nested inside `domain`'s own subtree(s), minus any that
    are themselves nested inside another, closer nested domain (already
    represented under that domain's own node instead) -- mirrors
    power_domain_overlay.py's own apply_domain_coloring() cascade
    ("deepest/most-specific always wins"), same shape as
    properties_panel.py's own _direct_excluded_children()."""
    nested = [d for d in all_domains if d.id != domain.id and _is_nested_in(d, domain)]
    return [d for d in nested if not any(other.id != d.id and _is_nested_in(d, other) for other in nested)]


def _macros_for_domain(
    domain: PowerDomainPlacement, boxes: dict[str, InstanceBoxItem], child_domains: list[PowerDomainPlacement]
) -> list[InstanceBoxItem]:
    """Every macro (`is_black_box`) box directly in `domain`'s own
    subtree(s) -- excluding one that actually belongs to a NESTED CHILD
    domain instead (that macro is represented under the child domain's
    own node, not duplicated here). A hierarchy block with no power-
    domain content of its own (neither a macro nor another domain) is a
    transparent pass-through -- it simply never becomes a node, but its
    own descendants (further down) are still reached by the same
    subtree-prefix membership check."""
    child_paths = [p for child in child_domains for p in child.owner_paths]
    result = []
    for path, box in boxes.items():
        if not box.definition.is_black_box:
            continue
        if not any(path == p or path.startswith(f"{p}/") for p in domain.owner_paths):
            continue
        if any(path == cp or path.startswith(f"{cp}/") for cp in child_paths):
            continue
        result.append(box)
    return sorted(result, key=lambda b: b.path)


def _resolved_color(icon: Optional[SupplyPortIconItem]) -> QColor:
    return QColor(icon.color) if icon is not None else QColor(_DEFAULT_COLOR)


def _power_switch_color(switch: PowerSwitchPlacement, supply_port_icons: dict[str, SupplyPortIconItem]) -> QColor:
    """Section 3: "Power Switch node color = Supply Out's color" --
    Supply Out is always and only the switch's own target (see
    power_switch_overlay.py's own module docstring), so this is simply
    the target's own live icon color, same as PowerSwitchIconItem.color
    itself resolves it."""
    return _resolved_color(supply_port_icons.get(switch.target_id))


def _build_domain_node(
    domain: PowerDomainPlacement,
    all_domains: list[PowerDomainPlacement],
    boxes: dict[str, InstanceBoxItem],
    domain_state: PowerDomainDiagramState,
    retention_state: RetentionDiagramState,
    isolation_state: IsolationDiagramState,
    power_switch_state: PowerSwitchDiagramState,
    supply_port_icons: dict[str, SupplyPortIconItem],
    level_shifter_state: Optional[LevelShifterDiagramState] = None,
) -> PowerViewNode:
    children: list[PowerViewNode] = []

    # 1. Supply Set(s) -- primary first, then secondaries, in order.
    supply_set_ids = [domain.primary_supply_set, *domain.secondary_supply_sets]
    for sid in supply_set_ids:
        if sid is None:
            continue
        supply_set = domain_state.supply_sets.get(sid)
        if supply_set is None:
            continue
        color = _resolved_color(resolve_supply_set_color_source(supply_set, supply_port_icons))
        children.append(PowerViewNode(kind="SupplySet", construct_id=sid, label=supply_set.name, color=color))

    # 2. Power Switch(es).
    switches = sorted((s for s in power_switch_state.switches.values() if s.power_domain_id == domain.id), key=lambda s: s.name)
    for switch in switches:
        children.append(PowerViewNode(kind="PowerSwitch", construct_id=switch.id, label=switch.name, color=_power_switch_color(switch, supply_port_icons)))

    # 3. Isolation(s).
    isolations = sorted((i for i in isolation_state.strategies.values() if i.power_domain_id == domain.id), key=lambda i: i.name)
    for iso in isolations:
        color = _resolved_color(resolve_isolation_color_source(iso, domain_state.supply_sets, supply_port_icons))
        children.append(PowerViewNode(kind="IsolationStrategy", construct_id=iso.id, label=iso.name, color=color))

    # 4. Level Shifter(s) -- follows the Isolation model (explicit
    # request, section 5), positioned right after Isolation and before
    # Retention: a judgment call (the original Power View ordering spec
    # predates Level Shifter), grouping the two boundary/direction-based
    # cell strategies back to back.
    if level_shifter_state is not None:
        level_shifters = sorted((s for s in level_shifter_state.strategies.values() if s.power_domain_id == domain.id), key=lambda s: s.name)
        for ls in level_shifters:
            color = resolve_level_shifter_color(ls, domain_state.domains)
            children.append(PowerViewNode(kind="LevelShifterStrategy", construct_id=ls.id, label=ls.name, color=color))

    # 5. Retention(s).
    retentions = sorted((r for r in retention_state.strategies.values() if r.power_domain_id == domain.id), key=lambda r: r.name)
    for ret in retentions:
        color = _resolved_color(resolve_retention_color_source(ret, domain_state.supply_sets, supply_port_icons))
        children.append(PowerViewNode(kind="RetentionStrategy", construct_id=ret.id, label=ret.name, color=color))

    # 6. Nested child Power Domain(s).
    child_domains = _direct_child_domains(domain, all_domains)
    for child in sorted(child_domains, key=lambda d: d.name):
        children.append(
            _build_domain_node(
                child, all_domains, boxes, domain_state, retention_state, isolation_state, power_switch_state, supply_port_icons, level_shifter_state
            )
        )

    # 7. Macro(s) -- leaf nodes.
    for box in _macros_for_domain(domain, boxes, child_domains):
        name = box.path.rsplit("/", 1)[-1]
        children.append(PowerViewNode(kind="Macro", construct_id=box.path, label=name))

    return PowerViewNode(kind="PowerDomain", construct_id=domain.id, label=domain.name, color=QColor(domain.color), children=children)


def build_power_view_tree(
    boxes: dict[str, InstanceBoxItem],
    domain_state: PowerDomainDiagramState,
    retention_state: RetentionDiagramState,
    isolation_state: IsolationDiagramState,
    power_switch_state: PowerSwitchDiagramState,
    supply_port_icons: dict[str, SupplyPortIconItem],
    level_shifter_state: Optional[LevelShifterDiagramState] = None,
) -> list[PowerViewNode]:
    """Every top-level (not-nested-in-another) Power Domain, each fully
    built via `_build_domain_node()` (recursive -- nested child domains
    are the SAME node shape, one level deeper). Empty list when no
    domains exist at all (a design with no power intent applied yet).
    `level_shifter_state` defaults to None (kept optional so any
    pre-existing caller/test that predates Level Shifter keeps working
    unchanged) -- when omitted, Level Shifter nodes simply never
    appear."""
    all_domains = list(domain_state.domains.values())
    roots = sorted(_root_domains(all_domains), key=lambda d: d.name)
    return [
        _build_domain_node(d, all_domains, boxes, domain_state, retention_state, isolation_state, power_switch_state, supply_port_icons, level_shifter_state)
        for d in roots
    ]


class PowerViewTree(QTreeWidget):
    """The Qt-facing half -- see module docstring. `rebuild()` is called
    every time PowerIntentDiagramView's own `diagramStateChanged` fires
    (power_intent_tab.py), the SAME signal properties_panel.py's own
    Properties tables already rebuild from, so this stays live for
    every one of sections 3/4/6's own requirements with no separate
    per-item callback wiring needed (see module docstring)."""

    editRequested = Signal(str, str)  # kind, construct_id
    removeRequested = Signal(str, str)

    def __init__(
        self,
        power_intent_controller: PowerIntentSelectionController,
        hierarchy_controller: Optional[HierarchySelectionController] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.setHeaderHidden(True)
        self.setColumnCount(2)
        # Auto-size both columns to their content -- same persistent-mode
        # convention hierarchy_tree_widget.py's own "Instance"/"Module"
        # two-column tree already uses -- so the type-label column sits
        # immediately adjacent to the (tightly-fit) name column, reading
        # as "right after the name" on every row.
        self.header().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self._controller = power_intent_controller
        self._hierarchy_controller = hierarchy_controller
        self._can_edit_row: Callable[[str, str], bool] = lambda kind, construct_id: True
        self._can_remove_row: Callable[[str, str], bool] = lambda kind, construct_id: True
        self._kind_and_id_by_item: dict[QTreeWidgetItem, tuple[str, str]] = {}
        self._item_by_kind_and_id: dict[tuple[str, str], QTreeWidgetItem] = {}

        self.itemSelectionChanged.connect(self._on_item_selected)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)
        power_intent_controller.selectionChanged.connect(self.apply_selection)

    def rebuild(self, nodes: list[PowerViewNode]) -> None:
        self.blockSignals(True)
        self.clear()
        self._kind_and_id_by_item.clear()
        self._item_by_kind_and_id.clear()
        for node in nodes:
            self._add_item(None, node)
        self.expandAll()
        self.blockSignals(False)

    def _add_item(self, parent_item: Optional[QTreeWidgetItem], node: PowerViewNode) -> QTreeWidgetItem:
        type_label = _TYPE_LABELS.get(node.kind, "")
        item = QTreeWidgetItem([node.label, type_label])
        if parent_item is None:
            self.addTopLevelItem(item)
        else:
            parent_item.addChild(item)
        if node.color is not None:
            item.setForeground(0, QBrush(node.color))
        # Type label: italic, neutral grey -- independent of the name's
        # own color-binding above (explicit request).
        italic_font = QFont(item.font(1))
        italic_font.setItalic(True)
        item.setFont(1, italic_font)
        item.setForeground(1, QBrush(_TYPE_LABEL_COLOR))
        self._kind_and_id_by_item[item] = (node.kind, node.construct_id)
        self._item_by_kind_and_id[(node.kind, node.construct_id)] = item
        for child in node.children:
            self._add_item(item, child)
        return item

    def _on_item_selected(self) -> None:
        items = self.selectedItems()
        if not items:
            return
        kind_id = self._kind_and_id_by_item.get(items[0])
        if kind_id is None:
            return
        kind, construct_id = kind_id
        if kind == "Macro":
            # A macro is an RTL instance path, not a power-intent
            # construct -- highlighted via the SAME hierarchy-path
            # selection hub Design View's own tree/diagram already
            # share, not the construct-kind one.
            if self._hierarchy_controller is not None:
                self._hierarchy_controller.selectionChanged.emit(frozenset({construct_id}))
            return
        self._controller.selectionChanged.emit(kind, construct_id)

    def apply_selection(self, construct_kind: str, construct_id: str) -> None:
        """controller.selectionChanged handler -- keeps this tree's own
        highlighted row in sync when some OTHER widget (a diagram click,
        a Properties table row) drives the selection, same "hub-and-
        spoke, every subscriber applies visually" precedent
        properties_panel.py's own setSelection() already follows.

        Bug fix (regression, explicit request): this method used to be
        named `setSelection()` -- QTreeWidget (via QAbstractItemView)
        already has its own built-in virtual `setSelection(QRect,
        QItemSelectionModel.SelectionFlags)`, used INTERNALLY by Qt's
        own mouse-click handling to actually perform a real click's
        selection. A same-named override with an incompatible signature
        silently shadowed it: every real click on a Power View row
        called straight into THIS method instead (with a QRect and a
        SelectionFlags value in place of the two expected strings),
        which harmlessly did nothing (`self._item_by_kind_and_id.get((rect,
        flags))` is always None) -- so Qt's own click never actually
        selected anything, and `_on_item_selected()` never fired at all.
        Confirmed empirically (a live scene: QTest.mouseClick() on a row
        left `item.isSelected()` False and `selectedItems()` empty, even
        though `currentItem()` correctly became the clicked item) --
        this is the EXACT same shadowing bug class already documented
        and fixed once before in this codebase for
        HierarchyTreeWidget.apply_selection() (see that method's own
        docstring), just recurring here under a different, still-
        colliding name. Renamed to `apply_selection()`, matching that
        established precedent exactly, and matching the sibling
        HierarchyDiagramView.apply_selection()/
        HierarchyPropertiesTableWidget.apply_selection() methods this
        codebase already uses for the identical reason."""
        item = self._item_by_kind_and_id.get((construct_kind, construct_id))
        if item is None:
            return
        self.blockSignals(True)
        self.setCurrentItem(item)
        self.scrollToItem(item)
        self.blockSignals(False)

    def set_action_enablement_hooks(self, can_edit: Callable[[str, str], bool], can_remove: Callable[[str, str], bool]) -> None:
        self._can_edit_row = can_edit
        self._can_remove_row = can_remove

    def _row_context_menu_actions(self, item: QTreeWidgetItem) -> tuple[Optional[str], Optional[str], Optional[QMenu], list]:
        """Same "build the actions, dispatch separately" split
        properties_panel.py's own _row_context_menu_actions() already
        establishes -- menu.exec() is a real, blocking modal loop that
        can't be driven headlessly at all."""
        kind_id = self._kind_and_id_by_item.get(item)
        if kind_id is None or kind_id[0] not in _EDITABLE_KINDS:
            return None, None, None, []
        kind, construct_id = kind_id
        menu = QMenu(self)
        edit_action = menu.addAction("Edit")
        edit_action.setEnabled(self._can_edit_row(kind, construct_id))
        remove_action = menu.addAction("Remove")
        remove_action.setEnabled(self._can_remove_row(kind, construct_id))
        return kind, construct_id, menu, [(edit_action, "edit"), (remove_action, "remove")]

    def _dispatch_chosen_row_action(self, kind: str, construct_id: str, action_kind: Optional[str]) -> None:
        if action_kind == "edit":
            self.editRequested.emit(kind, construct_id)
        elif action_kind == "remove":
            self.removeRequested.emit(kind, construct_id)

    def _show_context_menu(self, pos) -> None:
        item = self.itemAt(pos)
        if item is None:
            return
        self.setCurrentItem(item)
        kind, construct_id, menu, actions = self._row_context_menu_actions(item)
        if menu is None:
            return
        chosen = menu.exec(self.viewport().mapToGlobal(pos))
        action_kind = next((ak for action, ak in actions if action is chosen), None)
        self._dispatch_chosen_row_action(kind, construct_id, action_kind)
