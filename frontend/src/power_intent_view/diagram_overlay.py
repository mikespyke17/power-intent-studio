"""Power-intent overlay drawn onto the shared hierarchy diagram (see
shared/diagram/hierarchy_scene_builder.py). Adds domain regions, switch
icons + supply connectors, and isolation/level-shifter/retention badges
to a scene that already contains the base hierarchy -- never a separate
scene or a different notation. Power states/PST are deliberately never
drawn here (see docs/frontend_milestone2_power_intent_visualization.md);
they're tabular-only, in the properties panel.

Requires a MappedDesign, not just a PowerIntentDesign: domain membership,
badge placement, and switch connections all depend on *resolved*
references (which instance/port a raw string actually points at), and
that resolution is backend/mapping/'s job -- mirrored here, not
reimplemented. See build_power_intent_overlay()'s docstring for what
happens before Mapping has run at all.
"""

from __future__ import annotations

from collections import defaultdict

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QPen, QPolygonF
from PySide6.QtWidgets import (
    QGraphicsEllipseItem,
    QGraphicsItem,
    QGraphicsLineItem,
    QGraphicsPolygonItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
)

from frontend.src.shared.diagram.hierarchy_scene_builder import InstanceBoxItem
from frontend.src.shared.models.mapping import MappedDesign, ResolutionKind, ResolvedReference

Z_DOMAIN_REGION = 0  # behind instance boxes (Z=1), so members stay clickable on top
Z_SUPPLY_RAIL = 2
Z_SWITCH_ICON = 3
Z_BADGE = 4
Z_HIGHLIGHT = 10

_DOMAIN_PALETTE = [
    QColor(66, 133, 244, 60),
    QColor(219, 68, 55, 60),
    QColor(244, 180, 0, 60),
    QColor(15, 157, 88, 60),
    QColor(171, 71, 188, 60),
    QColor(0, 172, 193, 60),
]

_BADGE_PADDING = 4.0


class SelectableItemMixin:
    """Common bookkeeping every clickable power-intent overlay item needs:
    which construct it represents, and the outline to restore after a
    temporary selection highlight."""

    def __init__(self, construct_kind: str, construct_id: str):
        self.construct_kind = construct_kind
        self.construct_id = construct_id
        self._normal_pen: QPen | None = None

    def set_highlighted(self, highlighted: bool) -> None:
        if self._normal_pen is None:
            self._normal_pen = self.pen()
        if highlighted:
            self.setPen(QPen(QColor("#ffdd00"), 3))
            self.setZValue(Z_HIGHLIGHT)
        else:
            self.setPen(self._normal_pen)
            self.setZValue(self._base_z_value)


class DomainRegionItem(SelectableItemMixin, QGraphicsRectItem):
    _base_z_value = Z_DOMAIN_REGION

    def __init__(self, construct_id: str, rect: QRectF, color: QColor):
        QGraphicsRectItem.__init__(self, rect)
        SelectableItemMixin.__init__(self, "PowerDomain", construct_id)
        self.setZValue(Z_DOMAIN_REGION)
        self.setBrush(QBrush(color))
        self.setPen(QPen(color.darker(150), 2, Qt.PenStyle.DashLine))
        self.setFlag(QGraphicsItem.ItemIsSelectable)


class SwitchIconItem(SelectableItemMixin, QGraphicsPolygonItem):
    _base_z_value = Z_SWITCH_ICON

    def __init__(self, construct_id: str, center: QPointF, size: float = 14.0):
        polygon = QPolygonF(
            [
                QPointF(center.x(), center.y() - size),
                QPointF(center.x() + size, center.y()),
                QPointF(center.x(), center.y() + size),
                QPointF(center.x() - size, center.y()),
            ]
        )
        QGraphicsPolygonItem.__init__(self, polygon)
        SelectableItemMixin.__init__(self, "PowerSwitch", construct_id)
        self.setZValue(Z_SWITCH_ICON)
        self.setBrush(QBrush(QColor("#607d8b")))
        self.setPen(QPen(QColor("#263238"), 1.5))
        self.setFlag(QGraphicsItem.ItemIsSelectable)


class SupplyRailItem(SelectableItemMixin, QGraphicsEllipseItem):
    _base_z_value = Z_SUPPLY_RAIL

    def __init__(self, construct_kind: str, construct_id: str, name: str, center: QPointF, radius: float = 10.0):
        QGraphicsEllipseItem.__init__(self, center.x() - radius, center.y() - radius, radius * 2, radius * 2)
        SelectableItemMixin.__init__(self, construct_kind, construct_id)
        self.setZValue(Z_SUPPLY_RAIL)
        self.setBrush(QBrush(QColor("#ffffff")))
        self.setPen(QPen(QColor("#333333"), 1.5))
        self.setFlag(QGraphicsItem.ItemIsSelectable)
        label = QGraphicsSimpleTextItem(name, self)
        label.setPos(-radius, radius + 2)


class IsolationBadgeItem(SelectableItemMixin, QGraphicsPolygonItem):
    """Orange triangle -- pin-level, parented to the port anchor's owning
    box and positioned via the box's *local* coordinates so it tracks the
    box automatically."""

    _base_z_value = Z_BADGE

    def __init__(self, construct_id: str, local_pos: QPointF, offset_index: int, parent_box: InstanceBoxItem):
        size = 6.0
        x = local_pos.x() + offset_index * (size + 2)
        polygon = QPolygonF([QPointF(x, local_pos.y() - size), QPointF(x + size, local_pos.y() + size), QPointF(x - size, local_pos.y() + size)])
        QGraphicsPolygonItem.__init__(self, polygon, parent_box)
        SelectableItemMixin.__init__(self, "IsolationStrategy", construct_id)
        self.setZValue(Z_BADGE)
        self.setBrush(QBrush(QColor("#ff9800")))
        self.setPen(QPen(QColor("#e65100"), 1))
        self.setFlag(QGraphicsItem.ItemIsSelectable)


class LevelShifterBadgeItem(SelectableItemMixin, QGraphicsPolygonItem):
    """Purple diamond -- pin-level, same parenting/offset scheme as
    IsolationBadgeItem."""

    _base_z_value = Z_BADGE

    def __init__(self, construct_id: str, local_pos: QPointF, offset_index: int, parent_box: InstanceBoxItem):
        size = 6.0
        x = local_pos.x() + offset_index * (size + 2)
        polygon = QPolygonF(
            [QPointF(x, local_pos.y() - size), QPointF(x + size, local_pos.y()), QPointF(x, local_pos.y() + size), QPointF(x - size, local_pos.y())]
        )
        QGraphicsPolygonItem.__init__(self, polygon, parent_box)
        SelectableItemMixin.__init__(self, "LevelShifterStrategy", construct_id)
        self.setZValue(Z_BADGE)
        self.setBrush(QBrush(QColor("#9c27b0")))
        self.setPen(QPen(QColor("#4a148c"), 1))
        self.setFlag(QGraphicsItem.ItemIsSelectable)


class ConnectorLineItem(QGraphicsLineItem):
    """A switch's connector line to a supply-rail marker. Not
    independently clickable (not a SelectableItemMixin -- there's no
    separate construct it represents on its own), but still needs to
    visually highlight when its owning PowerSwitch is selected, so it
    gets the same set_highlighted() interface directly rather than being
    silently skipped by setSelection()'s loop."""

    def __init__(self, x1: float, y1: float, x2: float, y2: float):
        super().__init__(x1, y1, x2, y2)
        self._normal_pen: QPen | None = None

    def set_highlighted(self, highlighted: bool) -> None:
        if self._normal_pen is None:
            self._normal_pen = self.pen()
        self.setPen(QPen(QColor("#ffdd00"), 3) if highlighted else self._normal_pen)


class RetentionBadgeItem(SelectableItemMixin, QGraphicsEllipseItem):
    """Blue circle -- instance-level, parented directly to the instance
    box (not a port anchor), fixed corner offset."""

    _base_z_value = Z_BADGE

    def __init__(self, construct_id: str, parent_box: InstanceBoxItem, radius: float = 6.0):
        box_rect = parent_box.rect()
        x = box_rect.right() - radius
        y = box_rect.top() + radius
        QGraphicsEllipseItem.__init__(self, x - radius, y - radius, radius * 2, radius * 2, parent_box)
        SelectableItemMixin.__init__(self, "RetentionStrategy", construct_id)
        self.setZValue(Z_BADGE)
        self.setBrush(QBrush(QColor("#2196f3")))
        self.setPen(QPen(QColor("#0d47a1"), 1))
        self.setFlag(QGraphicsItem.ItemIsSelectable)


def _refs_by_location(references: list[ResolvedReference], construct_kind: str, field: str) -> dict[str, list[ResolvedReference]]:
    """Groups resolved references by owning construct_id, for one
    (construct_kind, field) pair -- e.g. every PowerDomain's `elements`
    entries, keyed by which domain they belong to."""
    grouped: dict[str, list[ResolvedReference]] = defaultdict(list)
    for r in references:
        if r.location.construct_kind == construct_kind and r.location.field == field:
            grouped[r.location.construct_id].append(r)
    return grouped


def _split_resolved_port_path(resolved_path: str) -> tuple[str, str]:
    instance_path, _, port_name = resolved_path.rpartition("/")
    return instance_path, port_name


def build_power_intent_overlay(
    scene: QGraphicsScene, instance_boxes: dict[str, InstanceBoxItem], mapped: MappedDesign
) -> dict[tuple[str, str], list[QGraphicsItem]]:
    """Adds the power-intent overlay to `scene` (already containing the
    base hierarchy from build_hierarchy_scene()). Returns a
    (construct_kind, construct_id) -> [items] index for selection sync --
    every construct that could plausibly be clicked gets an entry, even
    domains that render nothing (see the empty-region fallback below):
    a valid, selectable-from-the-properties-panel construct with nothing
    to highlight is a different, real state from an unknown construct,
    and the index reflects that rather than silently omitting the key.
    """
    index: dict[tuple[str, str], list[QGraphicsItem]] = {}
    power_intent = mapped.power_intent

    domain_elements = _refs_by_location(mapped.references, "PowerDomain", "elements")
    domain_boxes: dict[str, list[InstanceBoxItem]] = {}
    for i, domain_id in enumerate(sorted(power_intent.domains)):
        member_paths = [r.resolved_path for r in domain_elements.get(domain_id, []) if r.kind == ResolutionKind.INSTANCE]
        boxes = [instance_boxes[p] for p in member_paths if p in instance_boxes]
        domain_boxes[domain_id] = boxes

        if not boxes:
            # Empty-fallback: a domain whose -elements resolved to zero
            # real members (e.g. every reference was DANGLING) gets NO
            # region item -- there is no real geometry to bound, and
            # drawing a degenerate/zero-size rect would be a broken-
            # looking glyph, not a graceful "nothing here." The domain
            # stays fully visible and selectable in the properties panel;
            # it's only the diagram side that has nothing to show.
            index[("PowerDomain", domain_id)] = []
            continue

        union_rect = boxes[0].sceneBoundingRect()
        for b in boxes[1:]:
            union_rect = union_rect.united(b.sceneBoundingRect())
        union_rect = union_rect.adjusted(-16, -16, 16, 16)

        region = DomainRegionItem(domain_id, union_rect, _DOMAIN_PALETTE[i % len(_DOMAIN_PALETTE)])
        scene.addItem(region)
        index[("PowerDomain", domain_id)] = [region, *boxes]

    # Supply rail: one marker per distinct supply construct actually
    # referenced by a switch, laid out left-to-right above everything else.
    switch_supply_refs = [
        r for r in mapped.references if r.location.construct_kind == "PowerSwitch" and r.location.field in ("input_supply", "output_supply") and r.kind == ResolutionKind.POWER_INTENT
    ]
    referenced_supply_ids = sorted({r.resolved_power_intent_id for r in switch_supply_refs})
    rail_y = -80.0
    rail_items: dict[str, SupplyRailItem] = {}
    if referenced_supply_ids:
        start_x = -(len(referenced_supply_ids) - 1) * 60 / 2
        for i, supply_id in enumerate(referenced_supply_ids):
            kind = "SupplyPort" if supply_id in power_intent.supply_ports else "SupplyNet"
            name = (power_intent.supply_ports.get(supply_id) or power_intent.supply_nets[supply_id]).name
            marker = SupplyRailItem(kind, supply_id, name, QPointF(start_x + i * 60, rail_y))
            scene.addItem(marker)
            rail_items[supply_id] = marker
            index[(kind, supply_id)] = [marker]

    switch_domain = _refs_by_location(mapped.references, "PowerSwitch", "domain")
    for switch_id, switch in power_intent.switches.items():
        domain_refs = switch_domain.get(switch_id, [])
        domain_ref = next((r for r in domain_refs if r.kind == ResolutionKind.POWER_INTENT), None)
        boxes = domain_boxes.get(domain_ref.resolved_power_intent_id, []) if domain_ref else []
        if not boxes:
            # Same fallback principle as domain regions: a switch whose
            # domain has no renderable region has no boundary to sit at.
            index[("PowerSwitch", switch_id)] = []
            continue

        region_rect = index[("PowerDomain", domain_ref.resolved_power_intent_id)][0].rect()
        icon_center = QPointF(region_rect.center().x(), region_rect.top())
        icon = SwitchIconItem(switch_id, icon_center)
        scene.addItem(icon)
        items = [icon]

        for r in switch_supply_refs:
            if r.location.construct_id != switch_id:
                continue
            marker = rail_items.get(r.resolved_power_intent_id)
            if marker is None:
                continue
            line = ConnectorLineItem(icon_center.x(), icon_center.y(), marker.rect().center().x() + marker.x(), marker.rect().center().y() + marker.y())
            line.setPen(QPen(QColor("#607d8b"), 1, Qt.PenStyle.DashLine))
            scene.addItem(line)
            items.append(line)

        index[("PowerSwitch", switch_id)] = items

    _build_pin_badges(scene, instance_boxes, mapped, "IsolationStrategy", "elements", IsolationBadgeItem, index)
    _build_pin_badges(scene, instance_boxes, mapped, "LevelShifterStrategy", "elements", LevelShifterBadgeItem, index)
    _build_instance_badges(instance_boxes, mapped, "RetentionStrategy", "elements", RetentionBadgeItem, index)

    return index


def _build_pin_badges(scene, instance_boxes, mapped: MappedDesign, construct_kind: str, field: str, badge_cls, index) -> None:
    by_construct = _refs_by_location(mapped.references, construct_kind, field)
    constructs = getattr(mapped.power_intent, _CONSTRUCT_DICT_ATTR[construct_kind])
    for construct_id in constructs:
        refs = [r for r in by_construct.get(construct_id, []) if r.kind == ResolutionKind.PORT]
        items = []
        pin_use_count: dict[tuple[str, str], int] = defaultdict(int)
        for r in refs:
            instance_path, port_name = _split_resolved_port_path(r.resolved_path)
            box = instance_boxes.get(instance_path)
            if box is None:
                continue
            offset_index = pin_use_count[(instance_path, port_name)]
            pin_use_count[(instance_path, port_name)] += 1
            local_pos = box.port_anchor_local(port_name)
            badge = badge_cls(construct_id, local_pos, offset_index, box)
            items.append(badge)
        index[(construct_kind, construct_id)] = items


def _build_instance_badges(instance_boxes, mapped: MappedDesign, construct_kind: str, field: str, badge_cls, index) -> None:
    by_construct = _refs_by_location(mapped.references, construct_kind, field)
    constructs = getattr(mapped.power_intent, _CONSTRUCT_DICT_ATTR[construct_kind])
    for construct_id in constructs:
        refs = [r for r in by_construct.get(construct_id, []) if r.kind == ResolutionKind.INSTANCE]
        items = []
        for r in refs:
            box = instance_boxes.get(r.resolved_path)
            if box is None:
                continue
            items.append(badge_cls(construct_id, box))
        index[(construct_kind, construct_id)] = items


_CONSTRUCT_DICT_ATTR = {
    "IsolationStrategy": "isolation_strategies",
    "LevelShifterStrategy": "level_shifter_strategies",
    "RetentionStrategy": "retention_strategies",
}
