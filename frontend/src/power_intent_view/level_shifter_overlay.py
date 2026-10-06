"""Level Shifter -- diagram-only/mock-first, same scoping decision
already established for Retention/Isolation/Power Switch (see those
modules' own docstrings): nothing here is reflected into the tab's
PowerIntentDesign.

Architecturally follows the ISOLATION model (explicit request, section
5 of the original spec): ONE shared LevelShifterStrategyPlacement can
be attached to several boundary blocks at once (a multi-hierarchy Power
Domain's own `owner_paths`), rendered as one badge icon PER attached
block, all representing the SAME underlying entity -- clicking any one
group-highlights every sibling together, same mechanism Isolation's own
badges already use. Reuses Isolation's own `collect_isolation_elements_for_domain()`/
`port_direction_display()`/`local_direction_composition()`/
`variants_for_block()` DIRECTLY (imported, not duplicated) -- all four
are already fully generic (they only ever read `.elements`/a domain/a
box, nothing Isolation-specific), so Level Shifter's own "Applies To"
per-block icon variant (normal vs. flipped) is computed the exact same
way Isolation's own direction-derived variant already is, with zero new
logic needed for that part.

Color (explicit request, section 3/5 -- but with a real, flagged
deviation from Isolation's own model): Isolation's badge follows its
own Isolation Supply Set's Power rail, because Isolation HAS a Supply
Set reference to follow. Level Shifter's own dialog fields (section 4
of the spec: Name/Power Domain/Applies To/Rule/Location/Threshold/
Level Shifter Cell Type/Elements) deliberately have NO Supply Set
field at all -- there is nothing analogous to bind a live Supply-Port
color to. The best-available fallback, and the one implemented here,
is the strategy's own Power Domain's `.color` (a plain, already-stored
QColor -- see power_domain_overlay.PowerDomainPlacement) -- flagged
explicitly, not silently assumed, since it's a genuine deviation from
"the same convention as Isolation" the original request otherwise
established for every other aspect of this feature. Unlike a Supply
Port's own color (a live property on a still-existing Qt item, kept in
sync via `on_recolor_callbacks`), a Power Domain's `.color` is a plain
value on a plain dataclass with no such live-callback mechanism -- so
staying live here means re-deriving the color from `domain_state`
fresh every time this module's own build function runs, and the VIEW
(power_intent_diagram_view.py) is responsible for re-running it
whenever a domain's own color could have changed (edit_power_domain()),
the same way it already reruns build_isolation_overlay() after any
edit that could affect an Isolation badge.
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

from frontend.src.power_intent_view.isolation_overlay import (
    _centered_y,
    local_direction_composition,
    port_direction_display,
    variants_for_block,
)
from frontend.src.power_intent_view.power_domain_overlay import PowerDomainPlacement
from frontend.src.power_intent_view.retention_overlay import owner_paths_for_elements
from frontend.src.power_intent_view.supply_port_overlay import (
    HIGHLIGHT_COLOR,
    INTERIOR_TOP_MARGIN_FOR_SUPPLY_PORT,
    SupplyPortIconItem,
    top_edge_supply_port_owners,
)
from frontend.src.shared.diagram.hierarchy_scene_builder import InstanceBoxItem
from frontend.src.shared.diagram.svg_tint import render_tinted_svg

__all__ = [
    "port_direction_display",
    "local_direction_composition",
    "variants_for_block",
]

_ICON_PATH = Path(__file__).resolve().parents[2] / "icons" / "level-shifter.svg"
_BADGE_SIZE = 28  # matches Isolation/Retention/Supply Port's own shared icon footprint on this diagram
_BADGE_GAP = 2.0
_DEFAULT_COLOR = QColor("#9e9e9e")  # same "unconnected"/fallback grey every other supply reference here uses

Z_LEVEL_SHIFTER_BADGE = 6  # same layer as Isolation/Retention/Supply Port icons -- all per-box anchored

# Explicit request, "make room" layout (mirrors Isolation's own
# LEFT_MARGIN_FOR_ISOLATION/INTERIOR_TOP_MARGIN_FOR_ISOLATION exactly --
# see that module's own docstring for the EXTERIOR-left-vs-INTERIOR-top
# distinction): a mixed-direction block materializes TWO stacked
# badges; the reserved room only ever needs to fit ONE badge at a time.
LEFT_MARGIN_FOR_LEVEL_SHIFTER = _BADGE_SIZE + _BADGE_GAP
INTERIOR_TOP_MARGIN_FOR_LEVEL_SHIFTER = _BADGE_SIZE + _BADGE_GAP

_APPLIES_TO_VALUES = ("inputs", "outputs", "both")
_RULE_VALUES = ("low_to_high", "high_to_low", "both")
_LOCATION_VALUES = ("self", "parent", "fanout")


@dataclass
class LevelShifterStrategyPlacement:
    """Plain, Qt-item-independent record of one Level Shifter strategy --
    same shape/lifecycle as IsolationStrategyPlacement (see that class's
    own docstring), attached to one or more boundary blocks of a Power
    Domain at once.

    `applies_to`: "inputs" | "outputs" | "both".
    `rule`: "low_to_high" | "high_to_low" | "both" -- ADVISORY/USER-
    ASSERTED ONLY (explicit request): no automatic validation against
    real voltage values, since Supply Set doesn't model voltage
    magnitude at all yet.
    `threshold`: optional minimum voltage delta before insertion is
    considered necessary -- same advisory-only status as `rule`, stored
    and displayed, never validated.
    `location`: "self" | "parent" | "fanout", default "self" -- the
    EXACT SAME three-option field/semantics as Isolation's own
    `location` (see isolation_overlay.IsolationStrategyPlacement)."""

    id: str
    name: str
    power_domain_id: str
    applies_to: str
    rule: str
    location: str = "self"
    threshold: Optional[float] = None
    # Rework (explicit request, supersedes the earlier "Level Shifter
    # Cell Type dropdown referencing a reusable LevelShifterCellPlacement
    # library" model): a purely descriptive, OPTIONAL free-text label --
    # no more separate library entity to create first, and no pin-name
    # fields to carry over either (the removed Cell entity never had any
    # -- see the module's own prior history).
    cell_type: Optional[str] = None
    elements: list[str] = field(default_factory=list)
    # Prefix for the names of the level-shifter CELL INSTANCES a tool
    # inserts -- one strategy covers many ports, so the tool needs
    # distinguishable per-port instance names and this is the handle on
    # them. OPTIONAL: empty means "let the tool use its own default".
    name_prefix: str = ""
    owner_path: str = ""


@dataclass
class LevelShifterDiagramState:
    """Shared, Qt-independent Level Shifter state for an entire Power
    Intent Diagram -- same cross-tab-sharing mechanism as
    IsolationDiagramState/PowerSwitchDiagramState, see their own
    docstrings for the full reasoning."""

    strategies: dict[str, LevelShifterStrategyPlacement] = field(default_factory=dict)


def copy_level_shifter_diagram_state(state: LevelShifterDiagramState) -> LevelShifterDiagramState:
    """An independent snapshot -- same "why" as every other diagram-only
    state's own copy_*_diagram_state(): Ctrl+Z/Ctrl+Y undo/redo needs a
    genuinely separate copy, not one still sharing the same mutable
    `elements` list objects with the live state."""
    return LevelShifterDiagramState(
        strategies={k: replace(v, elements=list(v.elements)) for k, v in state.strategies.items()},
    )


def restore_level_shifter_diagram_state(state: LevelShifterDiagramState, snapshot: LevelShifterDiagramState) -> None:
    """Copies `snapshot`'s fields onto the *live* `state` object in
    place -- never replaces `state` itself, since it's shared by
    reference across every tab (see the class's own docstring)."""
    restored = copy_level_shifter_diagram_state(snapshot)
    state.strategies = restored.strategies


def find_duplicate_level_shifter(
    strategies: dict[str, LevelShifterStrategyPlacement],
    power_domain_id: str,
    applies_to: str,
    rule: str,
    location: str,
    threshold: Optional[float],
    cell_type: Optional[str],
    elements: list[str],
    exclude_strategy_id: Optional[str] = None,
) -> Optional[LevelShifterStrategyPlacement]:
    """Same "complete duplicate" concept as isolation_overlay.py's own
    find_duplicate_isolation() (every field must match exactly, Elements
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
            and strategy.applies_to == applies_to
            and strategy.rule == rule
            and strategy.location == location
            and strategy.threshold == threshold
            and strategy.cell_type == cell_type
            and set(strategy.elements) == target_elements
        ):
            return strategy
    return None


@lru_cache(maxsize=1)
def _level_shifter_svg_renderer() -> QSvgRenderer:
    return QSvgRenderer(QByteArray(_ICON_PATH.read_bytes()))


def resolve_level_shifter_color(strategy: LevelShifterStrategyPlacement, domains: dict[str, PowerDomainPlacement]) -> QColor:
    """The color this badge should render in -- see module docstring
    for why this follows the strategy's own Power Domain color rather
    than a Supply Set (a real, flagged deviation from Isolation's own
    model: Level Shifter has no Supply Set field to follow). Falls back
    to the same default "unconnected" grey every other unresolved
    reference in this diagram already uses when the domain itself is a
    dangling reference (e.g. deleted out from under a still-existing
    strategy -- shouldn't happen in practice, since deleting a domain
    cascades its own Level Shifters away too, but this stays safe on
    its own regardless)."""
    domain = domains.get(strategy.power_domain_id)
    return QColor(domain.color) if domain is not None else QColor(_DEFAULT_COLOR)


# "Applies To All" -> which orientation(s) to render when there are no
# specific elements to derive a per-block direction from (see
# _variants_for_broad_strategy()'s own docstring).
_BROAD_VARIANTS = {"inputs": [False], "outputs": [True], "both": [False, True]}
_ELEMENTS_APPLIES_TO_VALUE = "elements"


def attachment_owner_paths(strategy: LevelShifterStrategyPlacement, domains: dict[str, PowerDomainPlacement]) -> set[str]:
    """Bug fix (explicit request, "Level Shifter icon not rendered"
    repro): which boundary block(s) get this strategy's own badge(s).

    A real, confirmed gap -- the "Applies To All" rework (section 3)
    made Elements OPTIONAL (empty and irrelevant whenever a broad
    Inputs/Outputs/Both choice is selected -- see
    level_shifter_dialog.LevelShifterDialog.elements's own docstring),
    but this module's own attachment-block derivation was never updated
    to match: it still unconditionally called
    owner_paths_for_elements(strategy.elements) (Isolation's own
    always-non-empty-elements assumption, copied over when this module
    was first built) -- for a broad strategy, `elements` is now
    genuinely `[]`, so that call returns an EMPTY set and NO badge ever
    materializes anywhere, even though the strategy itself was created
    successfully (confirmed empirically: a strategy with `applies_to=
    "inputs"` and `elements=[]` produced a real
    LevelShifterStrategyPlacement but `{strategy_id: []}` in
    `_level_shifter_icons` -- an icon-side gap, not a data-model one,
    the same "one side of a feature built, the other silently
    incomplete" pattern already seen once for Power Switch and once for
    Retention-in-generation).

    Fixed by branching on `applies_to` itself: "elements" mode keeps the
    EXACT SAME Isolation-derived behavior (attachment follows whichever
    blocks the chosen elements actually belong to); any broad choice
    (Inputs/Outputs/Both) instead attaches to EVERY ONE of the
    strategy's own Power Domain's boundary blocks (`domain.owner_paths`)
    -- consistent with "applies broadly across the whole domain," and
    correctly multi-hierarchy-aware (mirrors how Isolation's own
    Elements table is itself populated from every boundary block of a
    multi-hierarchy domain, so a broad Level Shifter reaches the same
    set of blocks a fully-populated Elements selection would have).
    Falls back to the strategy's own `owner_path` (the block it was
    literally created from) if the domain itself is a dangling
    reference -- shouldn't happen in practice (deleting a domain
    cascades its own Level Shifters away too), but stays safe on its
    own regardless, same "never silently render nothing when there's a
    reasonable fallback" precedent every other dangling-reference case
    in this diagram already follows."""
    if strategy.applies_to == _ELEMENTS_APPLIES_TO_VALUE:
        return owner_paths_for_elements(strategy.elements)
    domain = domains.get(strategy.power_domain_id)
    if domain is not None:
        return set(domain.owner_paths)
    return {strategy.owner_path} if strategy.owner_path else set()


def _variants_for_broad_strategy(strategy: LevelShifterStrategyPlacement) -> list[bool]:
    """Which icon variant(s) (`flipped`, see variants_for_block()'s own
    convention) a broad (non-"elements") strategy gets on EVERY block it
    attaches to -- there are no specific elements to derive a per-block
    LOCAL direction from here (unlike variants_for_block(), which stays
    the exact right tool for the "elements" case), so the orientation is
    derived directly from `applies_to` itself instead: Inputs -> normal,
    Outputs -> flipped, Both -> both, uniformly across every attached
    block (a broad choice has no notion of "this particular block's own
    local mix differs from another's" the way a specific Elements
    selection can)."""
    return _BROAD_VARIANTS.get(strategy.applies_to, [False, True])


def variants_for_strategy_on_block(strategy: LevelShifterStrategyPlacement, box: InstanceBoxItem, owner_path: str) -> list[bool]:
    """Dispatches to variants_for_block() (Isolation's own element-
    derived logic, unchanged) for "elements" mode, or
    _variants_for_broad_strategy() for a broad Inputs/Outputs/Both
    choice -- see attachment_owner_paths()'s own docstring for the full
    "why" this split exists at all."""
    if strategy.applies_to == _ELEMENTS_APPLIES_TO_VALUE:
        return variants_for_block(strategy, box, owner_path)
    return _variants_for_broad_strategy(strategy)


class LevelShifterBadgeIconItem(QGraphicsItem):
    """One Level Shifter strategy's own badge on ONE of the (potentially
    several) boundary blocks it's attached to -- same shape as
    IsolationBadgeIconItem (see that class's own docstring for the full
    "why", including the click-highlight bookkeeping this mirrors
    exactly): left-edge anchored, `location` controls inside-vs-outside
    placement, `flipped` renders a horizontally-mirrored glyph for an
    all-output local element set (variants_for_block(), imported from
    isolation_overlay.py -- see module docstring).

    Color is a plain snapshotted QColor (`color`), NOT a live-binding
    `color_source` reference the way Isolation's badge holds one -- see
    module docstring: there is no live Qt item (like a SupplyPortIconItem)
    to register an on_recolor_callback on here, since the color source
    is a Power Domain's own plain `.color` field. Kept live in practice
    by the VIEW re-running build_level_shifter_overlay() (which re-reads
    the domain's CURRENT color) every time a Power Domain edit could
    have changed it -- see power_intent_diagram_view.py's own
    edit_power_domain()."""

    def __init__(
        self,
        strategy_id: str,
        owner_box: InstanceBoxItem,
        offset_index: int,
        color: QColor,
        location: str = "parent",
        flipped: bool = False,
        extra_top_clearance: float = 0.0,
        total_count: int = 1,
    ):
        super().__init__(owner_box)
        self.strategy_id = strategy_id
        self.owner_box = owner_box
        self.flipped = flipped
        # Stored (not just consumed for `x` below) so a LATER badge --
        # of EITHER type, Isolation now included, see the unified row-
        # stacking system this module's own build_level_shifter_overlay()
        # and power_intent_diagram_view._materialize_level_shifter_badges()/
        # _materialize_isolation_badges() all now share -- can tell
        # inside from outside when computing its own stacking slot.
        self.location = location
        self.color = QColor(color)
        self._click_highlighted = False
        # Bug 3 (explicit request): stored, not just consumed for `y`
        # below -- recenter_in_group() (called by
        # power_intent_diagram_view.py's own _apply_group_centering(),
        # same as IsolationBadgeIconItem's own) recomputes Y from these
        # later, without needing to re-derive them.
        self.offset_index = offset_index
        self._extra_top_clearance = extra_top_clearance
        box_rect = owner_box.rect()
        half = _BADGE_SIZE / 2
        y = _centered_y(box_rect, extra_top_clearance, offset_index, total_count)
        if location == "self":
            x = box_rect.left() + half + _BADGE_GAP  # inside -- mirrored to the interior side of the same left-edge reference
        else:
            x = box_rect.left() - half - _BADGE_GAP  # outside (parent/fanout) -- matches Isolation's own placement
        self.setPos(x, y)
        self.setZValue(Z_LEVEL_SHIFTER_BADGE)
        self.setFlag(QGraphicsItem.ItemIsSelectable)

    def recenter_in_group(self, total_count: int) -> None:
        """Bug 3 (explicit request): mirrors
        IsolationBadgeIconItem.recenter_in_group() exactly -- see that
        method's own docstring."""
        y = _centered_y(self.owner_box.rect(), self._extra_top_clearance, self.offset_index, total_count)
        self.setPos(self.pos().x(), y)

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
        render_tinted_svg(painter, _level_shifter_svg_renderer(), rect, color)
        painter.restore()

    def set_click_highlighted(self, highlighted: bool) -> None:
        if self._click_highlighted == highlighted:
            return
        self._click_highlighted = highlighted
        self.update()


def build_level_shifter_overlay(
    boxes: dict[str, InstanceBoxItem],
    strategies: dict[str, LevelShifterStrategyPlacement],
    domains: dict[str, PowerDomainPlacement],
    supply_port_icons: Optional[dict[str, SupplyPortIconItem]] = None,
    initial_inside_slots: Optional[dict[str, int]] = None,
    initial_outside_slots: Optional[dict[str, int]] = None,
) -> dict[str, list[LevelShifterBadgeIconItem]]:
    """Materializes every Level Shifter's own badge icon(s) against the
    current (just-rebuilt) `boxes` -- called after every hierarchy-scene
    rebuild, same "don't guess, recompute from the source of truth"
    lifecycle build_isolation_overlay() already establishes. Attachment
    blocks are derived from `strategy.elements`' own scope prefixes
    (owner_paths_for_elements(), reused from retention_overlay.py), NOT
    domain.owner_paths directly -- same reasoning as Isolation's own
    build_isolation_overlay().

    Strategies are visited in a stable (name, id) order, and badges
    stack top-to-bottom per block in that same order -- same determinism
    precedent build_isolation_overlay()/build_retention_overlay() both
    already establish.

    Bug fixes (explicit request):
    - `supply_port_icons` (optional -- defaults to "resolve nothing," same
      backward-compatible-optional precedent build_retention_overlay()'s
      own params already establish): a top-edge Supply Port/Internal
      Supply's own label renders into the block's interior -- a badge
      here needs the same INTERIOR_TOP_MARGIN_FOR_SUPPLY_PORT clearance
      IsolationBadgeIconItem's own Y calc already applies, or it
      visually overlaps that label (confirmed empirically, "sram
      example" repro). Applied uniformly to every badge on a qualifying
      block, not just Location=self ones -- see class docstring.
    - `initial_inside_slots`/`initial_outside_slots` (explicit request,
      extension -- Isolation and Level Shifter now share ONE unified
      row-stacking system, SPLIT into the same two independent inside-
      vs-outside sequences build_isolation_overlay() itself already
      establishes for a same-type self+parent pair, generalized across
      types: "by type then creation order" within each side, confirmed
      against this codebase's own already-established convention --
      Isolation is always built FIRST in every code path here, and this
      parameter pair is how Level Shifter's own stacking continues past
      wherever Isolation left off on each block, per side, rather than
      landing on an identical slot -- see
      power_intent_diagram_view.py's own _isolation_badge_slot_counts_per_box()
      and _rebuild()). A Level Shifter self badge continuing the inside
      sequence CAN land on the same slot as an Isolation (or another
      Level Shifter) parent/fanout badge continuing the outside
      sequence -- sharing a Y, mirrored across the block's edge, the
      exact same way two same-type badges already do."""
    icons: dict[str, list[LevelShifterBadgeIconItem]] = {}
    inside_slots: dict[str, int] = dict(initial_inside_slots or {})
    outside_slots: dict[str, int] = dict(initial_outside_slots or {})
    top_edge_owners = top_edge_supply_port_owners(supply_port_icons or {})
    for strategy in sorted(strategies.values(), key=lambda s: (s.name, s.id)):
        strategy_icons = []
        color = resolve_level_shifter_color(strategy, domains)
        is_inside = strategy.location == "self"
        slots = inside_slots if is_inside else outside_slots
        # Bug fix (explicit request, "Level Shifter icon not rendered"
        # repro) -- see attachment_owner_paths()'s own docstring for the
        # full "why": NOT unconditionally owner_paths_for_elements()
        # anymore, since a broad (non-"elements") strategy has no
        # elements at all to derive attachment from.
        for owner_path in sorted(attachment_owner_paths(strategy, domains)):
            box = boxes.get(owner_path)
            if box is None:
                continue
            extra_top_clearance = INTERIOR_TOP_MARGIN_FOR_SUPPLY_PORT if owner_path in top_edge_owners else 0.0
            for flipped in variants_for_strategy_on_block(strategy, box, owner_path):
                index = slots.get(owner_path, 0)
                slots[owner_path] = index + 1
                strategy_icons.append(
                    LevelShifterBadgeIconItem(strategy.id, box, index, color, strategy.location, flipped, extra_top_clearance)
                )
        icons[strategy.id] = strategy_icons
    # Bug 3 (explicit request): self-centers using the totals just
    # computed above -- when `initial_inside_slots`/`initial_outside_slots`
    # seeded Isolation's own contribution (the normal production path),
    # these are already the TRUE combined totals, so this alone is
    # already correct; power_intent_diagram_view.py's own
    # _apply_group_centering() still re-applies uniformly afterward (a
    # no-op here, but it's what keeps Isolation's OWN earlier-built
    # badges correctly centered too -- see that method's own docstring).
    # `max(inside, outside)`, not each side's own count -- see
    # power_intent_diagram_view.py's own _combined_badge_slot_counts()
    # docstring for why a self+parent pair sharing a slot index must
    # always share the SAME total to stay aligned on the same Y.
    for strategy_icons in icons.values():
        for icon in strategy_icons:
            total = max(inside_slots.get(icon.owner_box.path, 0), outside_slots.get(icon.owner_box.path, 0))
            icon.recenter_in_group(max(total, 1))
    return icons
