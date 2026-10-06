"""Isolation Cell creation on the Power Intent tab's Diagram --
diagram-only/mock-first, same scoping decision already established for
supply ports/power domains/retention (see supply_port_overlay.py/
power_domain_overlay.py/retention_overlay.py's own module docstrings):
nothing here is reflected into the tab's PowerIntentDesign.

Architecturally follows the RETENTION model, not Power Switch (explicit
request): ONE shared IsolationStrategyPlacement can be attached to
several boundary blocks at once (a multi-hierarchy Power Domain's own
`owner_paths`), rendered as one badge icon PER attached block, all
representing the SAME underlying entity -- clicking any one of them
group-highlights every sibling together (see
_PowerIntentGraphicsView._on_scene_selection_changed()'s own dispatch,
extended for IsolationBadgeIconItem the same way it already handles
RetentionBadgeIconItem/PowerSwitchIconItem).

Unlike Retention, though, an Isolation's own attachment points are NOT
derived from its `elements` (an Isolation's elements are the boundary
block's own top-level I/O ports, which -- for a multi-hierarchy domain
-- can legitimately share the exact same bit-key across two different
member hierarchies, e.g. "clk" on both "top/a" and "top/b"; deriving
attachment from `element.rsplit("/", 1)[0]` the way
retention_overlay.py's own owner_paths_for_elements() does would still
work correctly here too (each element is namespaced with its own full
owner_path prefix, same convention), so it's reused as-is rather than
duplicated.

Color (explicit request): "the Isolation icon's colour follows the
Isolation Supply Set's colour" -- a SupplySetPlacement has no colour of
its own (see its own docstring: just Power/Ground/N-Well/P-Well *name*
references), so this resolves to the colour of whichever real
SupplyPortIconItem's own name matches the Supply Set's `.power` field,
scoped to the SAME owner_path as the boundary block the badge is
attached to (the same owner_path-scoped name-match convention
SupplySetPlacement's own fields already use elsewhere -- see
power_intent_diagram_view.py's _propagate_supply_port_rename()). Kept
LIVE via the exact same on_recolor_callbacks mechanism Power Switch's
own "colour follows Supply Out" rule already established
(power_switch_overlay.py's own PowerSwitchIconItem.color/sync_color())
-- `.color` here is a property reading the resolved source icon live,
not a cached QColor, so the badge repaints automatically the moment
that source port is recoloured (e.g. by being wired to a macro PG pin
-- see SupplyPortIconItem.recolor()). Falls back to the same default
"unconnected" grey (#9e9e9e) every other unresolved supply reference in
this diagram already uses when no matching port is found.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from functools import lru_cache
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QByteArray, QRectF
from PySide6.QtGui import QColor, QPainter
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QGraphicsItem

from frontend.src.power_intent_view.power_domain_overlay import PowerDomainPlacement, SupplySetPlacement
from frontend.src.power_intent_view.retention_overlay import owner_paths_for_elements
from frontend.src.power_intent_view.supply_port_overlay import (
    HIGHLIGHT_COLOR,
    INTERIOR_TOP_MARGIN_FOR_SUPPLY_PORT,
    SupplyPortIconItem,
    top_edge_supply_port_owners,
)
from frontend.src.shared.diagram.hierarchy_scene_builder import InstanceBoxItem
from frontend.src.shared.diagram.svg_tint import render_tinted_svg
from frontend.src.shared.models.rtl import PortDirection

_ICON_PATH = Path(__file__).resolve().parents[2] / "icons" / "isolation-cell.svg"
_BADGE_SIZE = 28  # matches SupplyPortIconItem/RetentionBadgeIconItem's own _ICON_SIZE/_BADGE_SIZE
_BADGE_GAP = 2.0
_DEFAULT_COLOR = QColor("#9e9e9e")  # same "unconnected" default every other supply reference here uses

Z_ISOLATION_BADGE = 6  # same layer as Supply Port/Retention icons -- all per-box anchored

# Explicit request, "make room" layout (item 3): public (not
# underscore-prefixed), consumed by power_intent_diagram_view.py's own
# _extra_left_margin()/_extra_interior_top() overrides -- see
# hierarchy_scene_builder.py's own docstring for the EXTERIOR-left-vs-
# INTERIOR-top distinction. A mixed-direction block materializes TWO
# stacked badges (see variants_for_block()); the reserved room only
# ever needs to fit ONE badge width/height at a time (they stack, not
# widen/heighten beyond a single badge's own footprint), so this is
# not doubled for that case.
LEFT_MARGIN_FOR_ISOLATION = _BADGE_SIZE + _BADGE_GAP
INTERIOR_TOP_MARGIN_FOR_ISOLATION = _BADGE_SIZE + _BADGE_GAP


@dataclass
class IsolationStrategyPlacement:
    """Plain, Qt-item-independent record of one Isolation Cell rule --
    same shape/lifecycle as RetentionStrategyPlacement (see that class's
    own docstring), attached to one or more boundary blocks of a Power
    Domain at once. `source_supply_set_id`/`sink_supply_set_id` are
    OPTIONAL (explicit request); `clamp_value`/`isolation_sense` are
    each one of exactly 2-3 mutually-exclusive string values, validated
    by the dialog before ever reaching here (see isolation_dialog.py) --
    kept as plain `str` rather than an enum for the same "diagram-only
    mock data, not a real schema" reason every other placement type
    here stays plain data."""

    id: str
    name: str
    power_domain_id: str
    isolation_supply_set_id: str
    clamp_value: str  # "low" | "high" | "keep"
    isolation_control: str
    isolation_sense: str  # "low" | "high"
    source_supply_set_id: Optional[str] = None
    sink_supply_set_id: Optional[str] = None
    location: str = "self"  # "self" | "parent" | "fanout"
    elements: list[str] = field(default_factory=list)
    # "Applies To All" (same choice Level Shifter has): "inputs" |
    # "outputs" | "both" apply the rule to the domain's whole boundary
    # in that direction, with `elements` empty; "elements" uses the
    # explicit `elements` selection. Defaults to "elements" -- every
    # strategy created before this field existed was element-based, so
    # an older .pis project loads unchanged.
    applies_to: str = "elements"
    # Prefix for the names of the isolation CELL INSTANCES a tool
    # inserts -- one strategy covers many ports, so the tool needs
    # distinguishable per-port instance names and this is the handle on
    # them. OPTIONAL: empty means "let the tool use its own default".
    name_prefix: str = ""
    owner_path: str = ""


@dataclass
class IsolationDiagramState:
    """Shared, Qt-independent Isolation Cell state for an entire Power
    Intent Diagram -- same cross-tab-sharing mechanism as
    RetentionDiagramState/PowerSwitchDiagramState, see their own
    docstrings for the full reasoning."""

    strategies: dict[str, IsolationStrategyPlacement] = field(default_factory=dict)


def copy_isolation_diagram_state(state: IsolationDiagramState) -> IsolationDiagramState:
    """An independent snapshot -- same "why" as every other diagram-only
    state's own copy_*_diagram_state(): Ctrl+Z/Ctrl+Y undo/redo needs a
    genuinely separate copy, not one still sharing the same mutable
    `elements` list objects with the live state."""
    return IsolationDiagramState(strategies={k: replace(v, elements=list(v.elements)) for k, v in state.strategies.items()})


def restore_isolation_diagram_state(state: IsolationDiagramState, snapshot: IsolationDiagramState) -> None:
    """Copies `snapshot`'s fields onto the *live* `state` object in
    place -- never replaces `state` itself, since it's shared by
    reference across every tab (see the class's own docstring)."""
    restored = copy_isolation_diagram_state(snapshot)
    state.strategies = restored.strategies


def collect_isolation_elements_for_domain(boxes: dict[str, InstanceBoxItem], domain: PowerDomainPlacement) -> list[str]:
    """The I/O of EVERY one of `domain`'s own boundary blocks
    (`domain.owner_paths` -- a Power Domain's own owner_paths ARE
    exactly its boundary/topmost blocks by construction: creating a
    domain on a block anchors it there and colouring cascades DOWN from
    it, see power_domain_overlay.py's own apply_domain_coloring()
    docstring; there is no separate "topmost block" resolution needed).
    Single-hierarchy domain (`owner_paths` has exactly one entry, the
    common case): just that one block's own ports. Multi-hierarchy:
    the COMBINED I/O of every member's own boundary block, in one flat
    list (explicit request), de-duplicated while preserving first-seen
    order -- two different members can legitimately declare a
    same-named port ("clk" on both "top/a" and "top/b"), which is why
    each element key is namespaced with its own full owner_path prefix
    ("<owner_path>/<port_name>"), same convention
    collect_retention_elements()'s own "<scope>/<sequential_name>" keys
    already establish, letting owner_paths_for_elements() work
    unmodified for Isolation too.

    Bug fix, explicit request: ONE element per whole PORT, never
    per-bit ("d[0]"/"d[1]"/...) -- an earlier version mirrored
    port_attribute_overlay.py's own port_attribute_row_keys() bit-wise
    ungrouping, which silently broke every multi-bit port's own
    Receiver/Driver Supply resolution (verified empirically): the
    PortList tab's own connectivity keys (hierarchy_view/
    properties_table_widget.py's own `_PortListPanel._refresh()`,
    `(node.path, p.name)`) -- and therefore PowerResolutionCache's own
    resolve_all(), which resolves at the same whole-port granularity,
    confirmed by reading `_resolve_driver()`'s own
    `p.name == endpoint.signal` comparison -- are keyed by the BARE
    port name only, never a per-bit suffix. A per-bit element key like
    "top/u_sub/d[0]" therefore matched NOTHING in the resolved
    (path, signal) dicts, rendering every cell blank for any port wider
    than 1 bit. Matching PortList's own row/key granularity exactly
    (one row per port, same as the tab whose data this reuses) is what
    "reuse the EXISTING Receiver/Driver Supply Set data already
    computed... for the Port List tab" actually requires.

    A currently-hidden boundary block (not in `boxes`) simply
    contributes nothing this pass, same "not materialized, not
    discarded" precedent every other diagram-only placement here
    already follows."""
    seen: set[str] = set()
    elements: list[str] = []
    for owner_path in domain.owner_paths:
        box = boxes.get(owner_path)
        if box is None:
            continue
        for port in box_io_ports(box):
            element = f"{owner_path}/{port.name}"
            if element not in seen:
                seen.add(element)
                elements.append(element)
    return elements


_DIRECTION_DISPLAY = {PortDirection.IN: "IN", PortDirection.OUT: "OUT", PortDirection.INOUT: "INOUT"}


def box_io_ports(box) -> list:
    """A block's FULL I/O -- plain ports plus SystemVerilog interface
    signals (`InstanceBoxItem.io_ports`, see shared/models/rtl_io.py).
    Every I/O list here reads it, so an element, its Direction and its
    badge orientation all see the same ports. Falls back to the plain
    ports for a box built without the expansion."""
    io_ports = getattr(box, "io_ports", None)
    return io_ports if io_ports is not None else list(box.definition.ports)


def port_direction_display(box: InstanceBoxItem, owner_path: str, port_name: str) -> str:
    """The SAME IN/OUT/INOUT display convention the Port List tab
    itself uses (hierarchy_view/properties_table_widget.py's own
    `_DIR_DISPLAY`, private to that module -- reproduced here rather
    than imported, but with the identical 3 values, so the Isolation
    dialog's own "Direction" column (section 4) reads identically to
    what the same port already shows there) for the port named
    `port_name` on `box` (`owner_path` only used for the "not found"
    fallback message, kept explicit rather than silent). Empty string
    if the name doesn't resolve to a real port on this box (shouldn't
    happen in practice -- see collect_isolation_elements_for_domain()'s
    own docstring)."""
    port = next((p for p in box_io_ports(box) if p.name == port_name), None)
    return _DIRECTION_DISPLAY.get(port.direction, "") if port is not None else ""


def find_duplicate_isolation(
    strategies: dict[str, IsolationStrategyPlacement],
    power_domain_id: str,
    isolation_supply_set_id: str,
    source_supply_set_id: Optional[str],
    sink_supply_set_id: Optional[str],
    clamp_value: str,
    location: str,
    isolation_control: str,
    isolation_sense: str,
    elements: list[str],
    exclude_strategy_id: Optional[str] = None,
    applies_to: str = "elements",
) -> Optional[IsolationStrategyPlacement]:
    """Same "complete duplicate" concept as retention_overlay.py's own
    find_duplicate_strategy() (every field must match exactly, Elements
    compared as a set) -- kept as its own, separate function (not
    shared) since the two placements have entirely different field
    shapes; a partial match is deliberately NOT a duplicate here either,
    same precedent."""
    target_elements = set(elements)
    for strategy in strategies.values():
        if strategy.id == exclude_strategy_id:
            continue
        if (
            strategy.power_domain_id == power_domain_id
            and strategy.isolation_supply_set_id == isolation_supply_set_id
            and strategy.source_supply_set_id == source_supply_set_id
            and strategy.sink_supply_set_id == sink_supply_set_id
            and strategy.clamp_value == clamp_value
            and strategy.location == location
            and strategy.isolation_control == isolation_control
            and strategy.isolation_sense == isolation_sense
            and strategy.applies_to == applies_to
            and set(strategy.elements) == target_elements
        ):
            return strategy
    return None


@lru_cache(maxsize=1)
def _isolation_svg_renderer() -> QSvgRenderer:
    return QSvgRenderer(QByteArray(_ICON_PATH.read_bytes()))


def resolve_isolation_color_source(
    strategy: IsolationStrategyPlacement, supply_sets: dict[str, SupplySetPlacement], supply_port_icons: dict[str, SupplyPortIconItem]
) -> Optional[SupplyPortIconItem]:
    """The real SupplyPortIconItem this badge's own colour should
    follow -- whichever placed icon has a name matching the Isolation
    Supply Set's own `.power` field, scoped to the SUPPLY SET's OWN
    `owner_paths` (see module docstring). None if the Supply Set is
    unresolved, has no Power field set, or no matching icon is
    currently materialized on any of those blocks -- the badge falls
    back to the default grey in that case, same as any other
    unresolved supply reference.

    Bug fix, explicit request/verified empirically: an earlier version
    scoped this search to the BADGE's own attachment block (the
    Isolation's boundary block) instead -- wrong whenever the Supply
    Set (and the real port its own `.power` field names) was actually
    composed on a DIFFERENT hierarchy than where the Isolation itself
    is attached (e.g. a Supply Port placed on "top", a Supply Set
    referencing it, and an Isolation on a nested "top/u_sub" boundary
    block that uses that Supply Set as its own Isolation Supply Set --
    a normal, common pattern, not an edge case) -- the search never
    found the real port at all, silently falling back to default grey
    regardless of what colour the actual Supply Port had. Matching
    Supply Set's own `owner_paths` (where it was actually composed --
    see SupplySetPlacement's own docstring) instead of the badge's own
    unrelated attachment point is the correct scope."""
    supply_set = supply_sets.get(strategy.isolation_supply_set_id)
    if supply_set is None or not supply_set.power:
        return None
    for icon in supply_port_icons.values():
        if icon.owner_box.path in supply_set.owner_paths and icon.name == supply_set.power:
            return icon
    return None


_INSIDE_LOCATIONS = {"self"}  # every other location ("parent", "fanout") places OUTSIDE -- see module docstring


def local_direction_composition(strategy: IsolationStrategyPlacement, box: InstanceBoxItem, owner_path: str) -> tuple[bool, bool]:
    """(has_input, has_output) for JUST the subset of `strategy.elements`
    that belong to `owner_path` (explicit request -- section 5's own
    "PER-BLOCK, NOT GLOBAL" scope: a multi-hierarchy Isolation's icon
    variant is decided independently per boundary block, from that
    block's own local slice of the full included-element set, not the
    rule's element set as a whole). An INOUT port -- not addressed by
    the spec's own binary IN/OUT rule -- counts as BOTH (contributes to
    has_input AND has_output): an inout port genuinely both drives and
    receives, so a selection containing one is "mixed" the same way a
    genuine IN+OUT mix is, which is the more conservative (and more
    correct) reading than silently picking one side -- an assumption,
    not a guess left silent. A referenced port name not found on
    `box.definition` (shouldn't happen in practice -- elements are
    always derived from the box's own ports, see
    collect_isolation_elements_for_domain()) contributes to neither."""
    prefix = f"{owner_path}/"
    port_names = {element[len(prefix):] for element in strategy.elements if element.startswith(prefix)}
    ports_by_name = {p.name: p for p in box_io_ports(box)}
    has_input = False
    has_output = False
    for name in port_names:
        port = ports_by_name.get(name)
        if port is None:
            continue
        if port.direction in (PortDirection.IN, PortDirection.INOUT):
            has_input = True
        if port.direction in (PortDirection.OUT, PortDirection.INOUT):
            has_output = True
    return has_input, has_output


def variants_for_block(strategy: IsolationStrategyPlacement, box: InstanceBoxItem, owner_path: str) -> list[bool]:
    """Which icon(s) this ONE boundary block gets for `strategy`, per
    section 5's own rule -- a list of `flipped` booleans, one entry per
    icon to materialize (in stacking order): all-input -> `[False]`
    (one normal icon); all-output -> `[True]` (one flipped icon); a
    genuine local mix (or no resolvable direction at all, e.g. every
    referenced port name unresolved) -> `[False, True]` (both, side by
    side) -- the safer default when direction can't be determined
    cleanly, rather than silently guessing a single orientation."""
    has_input, has_output = local_direction_composition(strategy, box, owner_path)
    if has_input and not has_output:
        return [False]
    if has_output and not has_input:
        return [True]
    return [False, True]


ISOLATION_APPLIES_TO_ELEMENTS = "elements"
_BROAD_ISOLATION_VARIANTS = {"inputs": [False], "outputs": [True], "both": [False, True]}


def isolation_attachment_owner_paths(
    strategy: IsolationStrategyPlacement, domains: Optional[dict[str, PowerDomainPlacement]]
) -> set[str]:
    """Which boundary block(s) get this strategy's badge(s).

    "elements" mode: the blocks its elements belong to, as always. A
    broad Inputs/Outputs/Both strategy has NO elements, so deriving from
    them would render no badge at all (the exact bug Level Shifter's own
    "Applies To All" first shipped with -- see level_shifter_overlay's
    attachment_owner_paths(), which this mirrors): it attaches to every
    boundary block of its Power Domain instead, falling back to the
    block it was created on if the domain is unknown."""
    if strategy.applies_to == ISOLATION_APPLIES_TO_ELEMENTS:
        return owner_paths_for_elements(strategy.elements)
    domain = (domains or {}).get(strategy.power_domain_id)
    if domain is not None:
        return set(domain.owner_paths)
    return {strategy.owner_path} if strategy.owner_path else set()


def isolation_variants_on_block(strategy: IsolationStrategyPlacement, box: InstanceBoxItem, owner_path: str) -> list[bool]:
    """variants_for_block() for an element-based strategy; for a broad
    one there is no per-block element mix to read, so the orientation
    follows `applies_to` directly: Inputs -> normal, Outputs -> flipped,
    Both -> both (Level Shifter's own rule)."""
    if strategy.applies_to == ISOLATION_APPLIES_TO_ELEMENTS:
        return variants_for_block(strategy, box, owner_path)
    return _BROAD_ISOLATION_VARIANTS.get(strategy.applies_to, [False, True])


def _centered_y(box_rect: QRectF, extra_top_clearance: float, offset_index: int, total_count: int) -> float:
    """Bug 3 (explicit request, revises the earlier "append downward
    from a fixed start" spec): the group of `total_count` badges shares
    one row-stacking axis, centered on the block's own vertical midpoint
    (or, when `extra_top_clearance` carves out room for a top-edge
    Supply Port/Internal Supply label, the midpoint of the space BELOW
    that clearance instead -- same anti-collision guarantee the old
    formula already provided, just re-expressed as "center within the
    available band" rather than "start below the label"). total_count=1
    lands exactly at the center; 2 land symmetric one row above/below
    it; 3 land one AT the center, one above, one below; generalizes to N
    via `(offset_index - (total_count - 1) / 2)` rows of offset from the
    band's own center -- shared verbatim by
    LevelShifterBadgeIconItem.recenter_in_group() (imported from here),
    since the two now share one unified row-stacking system."""
    band_top = box_rect.top() + extra_top_clearance
    band_center = band_top + (box_rect.bottom() - band_top) / 2
    return band_center + (offset_index - (total_count - 1) / 2) * (_BADGE_SIZE + _BADGE_GAP)


class IsolationBadgeIconItem(QGraphicsItem):
    """One Isolation Cell rule's own badge on ONE of the (potentially
    several) boundary blocks it's attached to -- same "multiple
    strategies/multiple attachment points" shape as
    RetentionBadgeIconItem (see that class's own docstring for the full
    "why", including the click-highlight bookkeeping this mirrors
    exactly). Real differences from Retention's own badge:

    - Anchored to the block's own LEFT edge (explicit request,
      distinct from both Retention's top-right corner and Power
      Switch's own top anchor), stacked top-to-bottom via
      `offset_index` when several badges (or variants, see below) share
      the same block, rather than Retention's left-to-right top-right
      stacking.
    - `location` (explicit request, item 2): "self" places the badge
      INSIDE the block instead of outside -- same left-edge reference,
      mirrored to the interior side, near the top-left interior corner
      (a consistent mirror-image of the external case, not an
      unrelated placement rule). "parent"/"fanout" keep the existing
      outside-left placement unchanged.
    - `flipped` (explicit request, item 5): a genuinely mirrored
      (horizontally-flipped) render of the same glyph, for an
      all-OUTPUT local element set -- see variants_for_block(). Applied
      as a painter-level horizontal scale(-1, 1) around the icon's own
      local origin, the same "transform at paint time, never bake a
      second asset" precedent every rotation/tint in this diagram
      already follows (see SupplyPortIconItem.paint()'s own rotation).
    - Colour follows its target Supply Port live (see module docstring
      and resolve_isolation_color_source()) -- `.color` is a property, not a
      cached field, mirroring PowerSwitchIconItem's own `.color`/
      `sync_color()` precedent exactly; `sync_color()` here just
      schedules a repaint (update()), since paint() itself always reads
      `.color` fresh -- there are no separate handle objects to push a
      static colour onto the way a Power Switch's own 2 connector
      handles need. Applies uniformly across every variant/instance
      (explicit request, item 7) -- there is only ever one colour
      concept here, shared by construction, not something that could
      diverge between a normal and flipped icon of the same strategy.
    - Click-highlight (`set_click_highlighted()`) is likewise uniform
      across every variant/instance of the SAME strategy (item 7) --
      _PowerIntentGraphicsView._on_scene_selection_changed() already
      looks the clicked icon's own `strategy_id` up in the full
      `self._isolation_icons[strategy_id]` list (every icon on every
      block, every variant included) and highlights all of them
      together, with no special-casing needed here for multiple
      variants sharing a block."""

    def __init__(
        self,
        strategy_id: str,
        owner_box: InstanceBoxItem,
        offset_index: int,
        color_source: Optional[SupplyPortIconItem],
        location: str = "parent",
        flipped: bool = False,
        extra_top_clearance: float = 0.0,
        total_count: int = 1,
    ):
        super().__init__(owner_box)
        self.strategy_id = strategy_id
        self.owner_box = owner_box
        self.flipped = flipped
        # Stored (not just consumed for `x` below) so a LATER badge on
        # the same block can tell inside from outside when computing its
        # own stacking slot -- see build_isolation_overlay()'s own
        # `inside_slots`/`outside_slots` and
        # _materialize_isolation_badges()'s equivalent inline counting.
        self.location = location
        self._color_source = color_source
        if color_source is not None:
            color_source.on_recolor_callbacks.append(self.sync_color)
        self._click_highlighted = False
        # Bug 3 (explicit request): stored, not just consumed for `y`
        # below -- recenter_in_group() (called by
        # power_intent_diagram_view.py's own _apply_group_centering(),
        # once the TRUE combined Isolation+Level-Shifter total for this
        # block is known) recomputes Y from these later, without needing
        # to re-derive them.
        self.offset_index = offset_index
        self._extra_top_clearance = extra_top_clearance
        box_rect = owner_box.rect()
        half = _BADGE_SIZE / 2
        y = _centered_y(box_rect, extra_top_clearance, offset_index, total_count)
        if location in _INSIDE_LOCATIONS:
            x = box_rect.left() + half + _BADGE_GAP  # inside -- mirrored to the interior side of the same left-edge reference
        else:
            x = box_rect.left() - half - _BADGE_GAP  # outside (parent/fanout) -- existing/unchanged placement
        self.setPos(x, y)
        self.setZValue(Z_ISOLATION_BADGE)
        self.setFlag(QGraphicsItem.ItemIsSelectable)

    def recenter_in_group(self, total_count: int) -> None:
        """Bug 3 (explicit request): repositions this badge's own Y
        using its STORED offset_index/extra_top_clearance (fixed since
        construction) against a (possibly just-changed) `total_count`
        for its whole (owner block, inside/outside) group -- see
        _centered_y()'s own docstring for the exact formula. X is
        unaffected -- only Y ever depends on group size."""
        y = _centered_y(self.owner_box.rect(), self._extra_top_clearance, self.offset_index, total_count)
        self.setPos(self.pos().x(), y)

    @property
    def color(self) -> QColor:
        return QColor(self._color_source.color) if self._color_source is not None else QColor(_DEFAULT_COLOR)

    def sync_color(self) -> None:
        self.update()

    def boundingRect(self) -> QRectF:
        half = _BADGE_SIZE / 2
        return QRectF(-half, -half, _BADGE_SIZE, _BADGE_SIZE)

    def paint(self, painter: QPainter, option, widget=None) -> None:
        half = _BADGE_SIZE / 2
        rect = QRectF(-half, -half, _BADGE_SIZE, _BADGE_SIZE)
        color = HIGHLIGHT_COLOR if self._click_highlighted else self.color
        painter.save()
        if self.flipped:
            painter.scale(-1, 1)
        render_tinted_svg(painter, _isolation_svg_renderer(), rect, color)
        painter.restore()

    def set_click_highlighted(self, highlighted: bool) -> None:
        if self._click_highlighted == highlighted:
            return
        self._click_highlighted = highlighted
        self.update()


def build_isolation_overlay(
    boxes: dict[str, InstanceBoxItem],
    strategies: dict[str, IsolationStrategyPlacement],
    supply_sets: dict[str, SupplySetPlacement],
    supply_port_icons: dict[str, SupplyPortIconItem],
    domains: Optional[dict[str, PowerDomainPlacement]] = None,
) -> dict[str, list[IsolationBadgeIconItem]]:
    """Materializes every Isolation Cell's own badge icon(s) against the
    current (just-rebuilt) `boxes` -- called after every hierarchy-scene
    rebuild, same "don't guess, recompute from the source of truth"
    lifecycle build_retention_overlay() already establishes. Attachment
    blocks are derived from `strategy.elements`' own scope prefixes
    (owner_paths_for_elements(), reused from retention_overlay.py) --
    NOT from `domain.owner_paths` directly, so a strategy edited to drop
    some elements (and therefore some blocks) is reflected here too,
    same as Retention's own "which blocks actually get a badge is
    derived from elements" rule. This is also how an edit's own new
    direction composition (item 6) reaches the diagram live -- a full
    recompute from `strategy.elements` on every call, same as every
    other field, no separate live-update path needed.

    Strategies are visited in a stable (name, id) order, and badges
    stack top-to-bottom per block in that same order -- variants of the
    SAME strategy on the SAME block (see variants_for_block()) consume
    one stacking slot each, adjacent to one another, never overlapping
    -- so several strategies/variants sharing a block always stack
    identically across rebuilds, same determinism precedent
    build_retention_overlay() already establishes for its own
    left-to-right stacking.

    INSIDE (Location=self) and OUTSIDE (Location=parent/fanout) badges
    on the SAME block stack in TWO INDEPENDENT sequences (explicit
    request, "sram example" repro -- align a self+parent pair on the
    same horizontal line, straddling the block's edge): `inside_slots`/
    `outside_slots` each count 0, 1, 2, ... per block, on their own --
    the FIRST self badge and the FIRST parent/fanout badge on a block
    always land in slot 0 together, sharing the identical Y (mirrored
    across the boundary), regardless of how many of each kind exist
    (a second self badge lands in slot 1, below the first, and only
    collides with a second parent/fanout badge's own slot 1, never with
    slot 0's pair). A block with only one location present just stacks
    normally within its own sequence, same as before this change.

    `extra_top_clearance` (INTERIOR_TOP_MARGIN_FOR_SUPPLY_PORT when
    `owner_path` has a top-edge Supply Port/Internal Supply, whose own
    label renders INTO the block's interior) is now applied UNIFORMLY
    to both sequences on a qualifying block, not just the inside one --
    an outside/exterior badge never risked overlapping that label on
    its own, but leaving it unshifted would break the slot-0 alignment
    this change exists for the moment a block needing this clearance
    also has a parent/fanout badge: the self badge would sit lower than
    its paired outside badge, no longer sharing a Y. Shifting the whole
    row (both sequences) down together keeps the pair aligned at
    whatever height slot 0 actually needs to clear."""
    icons: dict[str, list[IsolationBadgeIconItem]] = {}
    inside_slots: dict[str, int] = {}
    outside_slots: dict[str, int] = {}
    top_edge_owners = top_edge_supply_port_owners(supply_port_icons)
    for strategy in sorted(strategies.values(), key=lambda s: (s.name, s.id)):
        strategy_icons = []
        color_source = resolve_isolation_color_source(strategy, supply_sets, supply_port_icons)
        is_inside = strategy.location in _INSIDE_LOCATIONS
        slots = inside_slots if is_inside else outside_slots
        # isolation_attachment_owner_paths(), not the elements directly: a
        # broad "Applies To All" strategy has none (needs `domains`).
        for owner_path in sorted(isolation_attachment_owner_paths(strategy, domains)):
            box = boxes.get(owner_path)
            if box is None:
                continue
            extra_top_clearance = INTERIOR_TOP_MARGIN_FOR_SUPPLY_PORT if owner_path in top_edge_owners else 0.0
            for flipped in isolation_variants_on_block(strategy, box, owner_path):
                index = slots.get(owner_path, 0)
                slots[owner_path] = index + 1
                strategy_icons.append(
                    IsolationBadgeIconItem(strategy.id, box, index, color_source, strategy.location, flipped, extra_top_clearance)
                )
        icons[strategy.id] = strategy_icons
    # Bug 3 (explicit request): self-centers this module's OWN badges
    # using the totals just computed above -- correct on its own for any
    # caller building Isolation alone (including every existing direct
    # test). power_intent_diagram_view.py's own _apply_group_centering()
    # OVERRIDES this immediately afterward with the TRUE combined
    # Isolation+Level-Shifter total whenever both share a block -- see
    # that method's own docstring. `max(inside, outside)`, not each
    # side's own count -- see power_intent_diagram_view.py's own
    # _combined_badge_slot_counts() docstring for why a self+parent pair
    # sharing a slot index must always share the SAME total to stay
    # aligned on the same Y.
    for strategy_icons in icons.values():
        for icon in strategy_icons:
            total = max(inside_slots.get(icon.owner_box.path, 0), outside_slots.get(icon.owner_box.path, 0))
            icon.recenter_in_group(max(total, 1))
    return icons
