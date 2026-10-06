"""Retention Strategy creation on the Power Intent tab's Diagram --
diagram-only/mock-first, same scoping decision already established for
supply ports and power domains (see supply_port_overlay.py/
power_domain_overlay.py's own module docstrings): nothing here is
reflected into the tab's PowerIntentDesign, no backend.modification.ops.add()
call.

Deliberately separate from diagram_overlay.py's own RetentionBadgeItem
(Milestone 2's read-only, MappedDesign-driven per-instance badge, built
from a real PowerIntentDesign's resolved references via backend.mapping)
-- a genuinely different data source and a genuinely different
lifecycle. This module's RetentionStrategyPlacement/RetentionDiagramState
mirror SupplyPortPlacement/SupplyPortDiagramState's own shape instead:
plain, Qt-independent, shared (not copied) across every tab of the same
Power Intent Diagram, restored verbatim across a scene rebuild.

A Retention Strategy's own `elements` are stored as flat
"<scope_path>/<sequential_name>" strings (only the ENABLED ones -- an
element left unchecked in the dialog's own Elements list simply isn't
part of the strategy at all, there is no separate "disabled member"
concept to persist), matching how the dialog itself displays and edits
them. `power_domain_id` is a reference by id into PowerDomainDiagramState.domains,
same "reference by id, not by value" precedent SupplySetPlacement
references already follow for a PowerDomainPlacement's own primary/
secondary Supply Sets.
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

from frontend.src.power_intent_view.power_domain_overlay import PowerDomainPlacement, SupplySetPlacement, find_effective_domain_for_path
from frontend.src.power_intent_view.supply_port_overlay import (
    HIGHLIGHT_COLOR,
    INTERIOR_TOP_MARGIN_FOR_SUPPLY_PORT,
    SupplyPortIconItem,
    top_edge_supply_port_owners,
)
from frontend.src.shared.diagram.hierarchy_scene_builder import InstanceBoxItem
from frontend.src.shared.diagram.svg_tint import render_tinted_svg

_DEFAULT_COLOR = QColor("#9e9e9e")  # same "unconnected" default every other supply reference here uses (see isolation_overlay.py's own copy)

_ICON_PATH = Path(__file__).resolve().parents[2] / "icons" / "retention-flop.svg"
# Explicit request: match supply_port_overlay.py's own _ICON_SIZE (28) --
# same dimensions/visual weight as a Supply Port icon on the same block,
# not the tool palette's own much smaller _ICON_SIZE (14) this used to
# follow.
_BADGE_SIZE = 28
_BADGE_GAP = 2.0

Z_RETENTION_BADGE = 6  # same layer as supply-port icons (Z_SUPPLY_PORT) -- both are per-box anchored icons


@dataclass
class RetentionStrategyPlacement:
    """Plain, Qt-item-independent record of one Retention Strategy.
    `owner_path` is the block the strategy was created (or last edited)
    from -- kept for dialog re-open context (pre-filling "Add Retention"
    from the same block again) and diagnostic display only; which blocks
    actually get a badge icon is derived from `elements`' own scope
    prefixes (see owner_paths_for_elements()), not from this field --
    a single strategy can span many blocks, `owner_path` is only ever
    one of them."""

    id: str
    name: str
    power_domain_id: str
    primary_supply_set_id: str
    retention_supply_set_id: str
    save_logic: str
    restore_logic: str
    elements: list[str] = field(default_factory=list)
    owner_path: str = ""


@dataclass
class RetentionDiagramState:
    """Shared, Qt-independent Retention Strategy state for an entire
    Power Intent Diagram (every tab -- same cross-tab-sharing mechanism
    as SupplyPortDiagramState/PowerDomainDiagramState, see their own
    docstrings for the full reasoning). One instance is constructed once
    by PowerIntentDiagramView and handed to every _PowerIntentGraphicsView
    it creates."""

    strategies: dict[str, RetentionStrategyPlacement] = field(default_factory=dict)


def copy_retention_diagram_state(state: RetentionDiagramState) -> RetentionDiagramState:
    """An independent snapshot -- same "why" as
    supply_port_overlay.py's own copy_supply_port_diagram_state(): Ctrl+Z/
    Ctrl+Y undo/redo needs a genuinely separate copy, not one still
    sharing the same mutable list objects (`elements`) with the live
    state."""
    return RetentionDiagramState(strategies={k: replace(v, elements=list(v.elements)) for k, v in state.strategies.items()})


def restore_retention_diagram_state(state: RetentionDiagramState, snapshot: RetentionDiagramState) -> None:
    """Copies `snapshot`'s fields onto the *live* `state` object in
    place -- never replaces `state` itself, since it's shared by
    reference across every tab (see the class's own docstring)."""
    restored = copy_retention_diagram_state(snapshot)
    state.strategies = restored.strategies


def owner_paths_for_elements(elements: list[str]) -> set[str]:
    """Distinct block paths owning at least one of `elements` (each
    "<scope>/<sequential_name>") -- drives which blocks get a Retention
    badge icon: "EVERY block owning at least one ENABLED element from
    the strategy must show the Retention icon" (explicit request) -- see
    a later commit's build_retention_overlay()."""
    return {element.rsplit("/", 1)[0] for element in elements}


def collect_retention_elements(boxes: dict[str, InstanceBoxItem], target_path: str, domains: dict[str, PowerDomainPlacement]) -> list[str]:
    """Every sequential element under `target_path` (inclusive of
    `target_path` itself) whose own *effective* Power Domain matches
    `target_path`'s own -- recursively EXCLUDING any nested sub-hierarchy
    that belongs to a DIFFERENT Power Domain (explicit warning honored: a
    naive "walk all descendants" traversal is wrong here -- a sub-module
    carved out by its own, more specific domain must not contribute its
    elements to a strategy rooted in an ancestor domain).

    The recursion collapses to a flat per-node comparison rather than
    needing its own subtree-stop bookkeeping: find_effective_domain_for_path()
    already implements "deepest owner wins", the exact same cascading
    rule apply_domain_coloring() uses to recolour boxes -- so asking it
    independently for every candidate node under `target_path` is already
    correct. A node whose own effective domain differs from the target's
    is excluded, and by construction so is everything genuinely
    *underneath* it that still resolves to that same (or an even more
    specific, still-different) domain -- there is nothing left to
    special-case.

    Scoped to `boxes` (the diagram's own currently-materialized boxes,
    same scoping SupplyPortDiagramState/PowerDomainDiagramState already
    use) -- a currently-hidden sub-hierarchy simply isn't offered this
    pass, same "not materialized this pass, not discarded" precedent
    those already follow.
    """
    target_domain = find_effective_domain_for_path(domains, target_path)
    target_domain_id = target_domain.id if target_domain is not None else None
    elements: list[str] = []
    for path, box in boxes.items():
        if path != target_path and not path.startswith(f"{target_path}/"):
            continue
        node_domain = find_effective_domain_for_path(domains, path)
        node_domain_id = node_domain.id if node_domain is not None else None
        if node_domain_id != target_domain_id:
            continue
        for seq in box.definition.sequential_elements:
            elements.append(f"{path}/{seq.output_signal}")
    return elements


def collect_retention_elements_for_domain(
    boxes: dict[str, InstanceBoxItem], domain: PowerDomainPlacement, domains: dict[str, PowerDomainPlacement]
) -> list[str]:
    """Multi-hierarchy Power Domain (explicit request): a Retention
    Strategy's own Elements list must cover sequential elements from
    EVERY one of the domain's member hierarchies, not just the single
    block that happened to trigger "Add Retention" -- reuses
    collect_retention_elements() as-is, run once per `domain.owner_paths`
    entry (each an independent recursion root, same "deepest owner
    wins" exclusion rule applying within each) and merged, de-duplicated
    while preserving first-seen order. For a single-hierarchy domain
    (`owner_paths` has exactly one entry, the common case) this is
    exactly equivalent to calling collect_retention_elements() directly."""
    seen: set[str] = set()
    elements: list[str] = []
    for owner_path in domain.owner_paths:
        for element in collect_retention_elements(boxes, owner_path, domains):
            if element not in seen:
                seen.add(element)
                elements.append(element)
    return elements


def claimed_elements_by_other_strategies(
    strategies: dict[str, RetentionStrategyPlacement], domain_id: str, exclude_strategy_id: Optional[str]
) -> set[str]:
    """Every element string already ENABLED by some OTHER Retention
    Strategy sharing the same Power Domain -- explicit constraint: "if a
    sequential element is part of the first retention strategy, when
    creating a second one [in the same hierarchy/power domain], that
    element can't be part of it" -- the Retention Strategy dialog's own
    Elements list shows these greyed out/italic and un-checkable (see
    retention_dialog.py). Scoped to `domain_id`, not the whole diagram --
    two strategies in genuinely different domains can never collide over
    the same element anyway (each domain's own hierarchy is disjoint by
    construction -- a nested domain always carves its own subtree out of
    its ancestor's, see power_domain_overlay.py's own module docstring).
    `exclude_strategy_id` (the strategy currently being edited, if any)
    keeps its OWN already-claimed elements from appearing locked against
    itself."""
    claimed: set[str] = set()
    for strategy in strategies.values():
        if strategy.id == exclude_strategy_id:
            continue
        if strategy.power_domain_id != domain_id:
            continue
        claimed |= set(strategy.elements)
    return claimed


def find_duplicate_strategy(
    strategies: dict[str, RetentionStrategyPlacement],
    power_domain_id: str,
    primary_supply_set_id: str,
    retention_supply_set_id: str,
    save_logic: str,
    restore_logic: str,
    elements: list[str],
    exclude_strategy_id: Optional[str] = None,
) -> Optional[RetentionStrategyPlacement]:
    """A strategy is a "complete duplicate" of another only when EVERY
    one of these fields matches exactly (explicit request): Power
    Domain, Primary Supply Set, Retention Supply Set, Save Logic,
    Restore Logic, AND the enabled Elements. Elements are compared as a
    set (order never matters for this equality). Returns the first
    matching existing strategy (there should only ever be one, since a
    confirmed merge is meant to eliminate duplicates as they're found --
    but this doesn't assume that invariant, it just returns whichever
    is found first).

    A PARTIAL match (some but not all of the above fields equal) is
    deliberately NOT treated as a duplicate -- falls through to the
    normal "add as a new, separate strategy" path instead. This was an
    explicitly acknowledged assumption (not a stated requirement) at
    the time this was built -- see the calling code's own comment."""
    target_elements = set(elements)
    for strategy in strategies.values():
        if strategy.id == exclude_strategy_id:
            continue
        if (
            strategy.power_domain_id == power_domain_id
            and strategy.primary_supply_set_id == primary_supply_set_id
            and strategy.retention_supply_set_id == retention_supply_set_id
            and strategy.save_logic == save_logic
            and strategy.restore_logic == restore_logic
            and set(strategy.elements) == target_elements
        ):
            return strategy
    return None


# Explicit request ("icons... displaying as pixelated/low quality,
# still pixelated after supersampling"): a cached QPixmap -- no matter
# how high its own source resolution -- is still a fixed-resolution
# raster once the diagram is zoomed in far enough, confirmed via a live
# before/after screenshot comparison at high zoom (see
# supply_port_overlay.py's own SupplyPortIconItem for the full
# writeup). RetentionBadgeIconItem below renders the SVG live instead,
# via this ONE shared QSvgRenderer (explicit request: "one per icon
# type, not one per instance"), cached with lru_cache so the SVG is
# parsed exactly once for the whole process, not once per badge
# instance or per repaint -- lazily constructed on first call (not at
# import time) since QSvgRenderer needs a QApplication/QGuiApplication
# to already exist.
@lru_cache(maxsize=1)
def _retention_svg_renderer() -> QSvgRenderer:
    return QSvgRenderer(QByteArray(_ICON_PATH.read_bytes()))


def resolve_retention_color_source(
    strategy: RetentionStrategyPlacement, supply_sets: dict[str, SupplySetPlacement], supply_port_icons: dict[str, SupplyPortIconItem]
) -> Optional[SupplyPortIconItem]:
    """The real SupplyPortIconItem this badge's own colour should
    follow -- explicit request, new feature: Retention previously had
    no colour concept of its own at all (see RetentionBadgeIconItem's
    own prior docstring). Follows the "Retention Supply Set" field's
    own `.power` rail, scoped to the SUPPLY SET's OWN `owner_paths` --
    same mechanism, deliberately duplicated rather than imported,
    isolation_overlay.py's own resolve_isolation_color_source()
    already establishes for the analogous Isolation Supply Set case
    (small, self-contained duplication over a forced cross-module
    import, same precedent this app already follows elsewhere, e.g.
    power_switch_dialog.py's own _optional_label()). None if the
    Supply Set is unresolved, has no Power field set, or no matching
    icon is currently materialized on any of those blocks -- the
    badge falls back to the default grey in that case, same as any
    other unresolved supply reference."""
    supply_set = supply_sets.get(strategy.retention_supply_set_id)
    if supply_set is None or not supply_set.power:
        return None
    for icon in supply_port_icons.values():
        if icon.owner_box.path in supply_set.owner_paths and icon.name == supply_set.power:
            return icon
    return None


class RetentionBadgeIconItem(QGraphicsItem):
    """One Retention Strategy's own badge on ONE of the (potentially
    several) blocks it spans. "Multiple strategies on the same block
    render as multiple STACKED icons" -- an explicit design decision
    (confirmed via AskUserQuestion, not guessed, since it also shapes
    the right-click Edit/Info/Delete menu design: each icon here belongs
    to exactly one strategy, so right-clicking a specific icon acts on
    that strategy alone). Stacked left-to-right from the block's own
    top-right corner via `offset_index` -- the same
    "offset_index * (size + gap)" layout diagram_overlay.py's own
    IsolationBadgeItem/LevelShifterBadgeItem already use for multiple
    pin-level badges sharing one anchor, adapted here to a whole-icon
    (not polygon) badge and a fixed corner (not a port anchor).

    A plain QGraphicsItem with a custom paint() (explicit request) --
    NOT QGraphicsPixmapItem anymore, same "render the SVG live instead
    of caching a fixed-resolution pixmap" fix as SupplyPortIconItem's
    own (see that class's docstring for the full "why", and
    _retention_svg_renderer() above). No rotation concept for this
    badge (unlike Supply Port -- it's always anchored the same way at a
    block's own top-right corner).

    Colour (explicit request, new feature -- supersedes this class's
    own earlier "no per-strategy colour, never had one" docstring):
    follows the Retention Supply Set's own Power rail colour, live,
    same "read a resolved source icon's own .color property fresh,
    plus an on_recolor_callbacks subscription for state Qt's own
    paint() cycle wouldn't otherwise re-derive" mechanism
    IsolationBadgeIconItem/PowerSwitchIconItem already establish for
    their own colour-binding -- see resolve_retention_color_source()'s
    own docstring and this class's own `color` property/sync_color()
    below. Falls back to the shared _DEFAULT_COLOR (grey) when
    unresolved. The click-selection highlight (bug 4, explicit
    request: clicking ANY one of a strategy's own (potentially
    several) icons highlights ALL of them together) still overrides
    this colour entirely while active -- see set_click_highlighted()/
    paint()'s own docstring. boundingRect() must be implemented
    explicitly -- previously derived implicitly from the cached
    QPixmap's own size."""

    def __init__(
        self,
        strategy_id: str,
        owner_box: InstanceBoxItem,
        offset_index: int,
        color_source: Optional[SupplyPortIconItem] = None,
        extra_top_clearance: float = 0.0,
    ):
        super().__init__(owner_box)
        self.strategy_id = strategy_id
        self.owner_box = owner_box
        self._color_source = color_source
        if color_source is not None:
            color_source.on_recolor_callbacks.append(self.sync_color)
        # Bug 4 (explicit request): click-selection highlight, managed
        # externally by _PowerIntentGraphicsView._on_scene_selection_changed()
        # -- see set_click_highlighted() below. Unlike SupplyPortIconItem
        # (where only the literally-clicked icon is Qt-"selected"), a
        # RetentionBadgeIconItem's own SIBLINGS (every other icon of the
        # same strategy, on other blocks) also need this set True despite
        # never being Qt-selected themselves -- that's the entire reason
        # this is a plain attribute here rather than just reading
        # self.isSelected() directly in paint().
        self._click_highlighted = False
        self._offset_index = offset_index
        # Bug fix (explicit request, "sram example" repro -- see
        # supply_port_overlay.top_edge_supply_port_owners()'s own
        # docstring): stored (not just used once) so _reposition() -- ALSO
        # invoked later by the box's own on_resize_callbacks -- keeps
        # applying the same clearance below a top-edge Supply Port/
        # Internal Supply's own label after a resize, not just at
        # construction time.
        self._extra_top_clearance = extra_top_clearance
        self._reposition()
        self.setZValue(Z_RETENTION_BADGE)
        self.setFlag(QGraphicsItem.ItemIsSelectable)
        # Bug fix (explicit request): this badge's own anchor is
        # box_rect.right()-based -- unlike a left/top-based anchor
        # (always 0 regardless of size, since InstanceBoxItem's own
        # rect() origin is always local (0, 0)), "right" genuinely
        # depends on the box's current WIDTH, so a later resize (e.g.
        # the "make room for a new element" expansion logic growing
        # this SAME box for some OTHER, unrelated element) left this
        # badge silently stale at the OLD right edge -- same root
        # cause/fix shape as SupplyPortIconItem.resnap_to_owner_edge().
        owner_box.on_resize_callbacks.append(self._reposition)

    def _reposition(self) -> None:
        half = _BADGE_SIZE / 2
        box_rect = self.owner_box.rect()
        self.setPos(
            box_rect.right() - half - self._offset_index * (_BADGE_SIZE + _BADGE_GAP), box_rect.top() + half + self._extra_top_clearance
        )

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
        # Same shared render_tinted_svg() composition SupplyPortIconItem/
        # IsolationBadgeIconItem's own colour rendering already uses
        # (shared/diagram/svg_tint.py) -- tints the badge's own
        # rendered shape to a single flat colour (fluorescent yellow
        # while click-highlighted, taking priority over -- not
        # combined with -- the resolved Retention colour otherwise;
        # the SAME tinting already happened here before this feature,
        # just previously ONLY for the highlight case, never for a
        # normal per-strategy colour since none existed yet).
        # ONE tinted render of an OUTLINE-ONLY asset (bug fix, explicit
        # report: the badge rendered as a solid block with its "RET"
        # label lost inside it).
        #
        # render_tinted_svg() composites with CompositionMode_SourceIn,
        # which replaces EVERY non-transparent pixel with the flat
        # colour. The asset used to give its flop rectangles a solid
        # white fill, so tinting turned the whole icon -- outlines,
        # fills and label alike -- into one block, with the label still
        # being drawn underneath it.
        #
        # The fix is in the ASSET, not here: retention-flop.svg now
        # draws strokes and the label only, `fill="none"` on both
        # rectangles. Tinting therefore colours exactly the lines and
        # the text, and the interior stays transparent -- so whatever
        # the badge sits on (a power domain's own translucent fill)
        # shows through, which is also what makes the label readable.
        color = HIGHLIGHT_COLOR if self._click_highlighted else self.color
        render_tinted_svg(painter, _retention_svg_renderer(), rect, color)

    def set_click_highlighted(self, highlighted: bool) -> None:
        if self._click_highlighted == highlighted:
            return
        self._click_highlighted = highlighted
        self.update()


def build_retention_overlay(
    boxes: dict[str, InstanceBoxItem],
    strategies: dict[str, RetentionStrategyPlacement],
    supply_sets: Optional[dict[str, SupplySetPlacement]] = None,
    supply_port_icons: Optional[dict[str, SupplyPortIconItem]] = None,
) -> dict[str, list[RetentionBadgeIconItem]]:
    """Materializes every Retention Strategy's own badge icon(s) against
    the current (just-rebuilt) `boxes` -- called after every hierarchy-
    scene rebuild, see power_intent_diagram_view.py's _rebuild()
    override. Returns a {strategy_id: [icons]} map -- one entry per
    block the strategy actually spans (see owner_paths_for_elements()),
    not necessarily one, matching "EVERY block owning at least one
    ENABLED element from the strategy must show the Retention icon,
    not just the block it was added from" (explicit request).

    `supply_sets`/`supply_port_icons` (explicit request, new colour
    feature -- both optional, defaulting to "resolve nothing" rather
    than required, unlike build_isolation_overlay()'s own equivalent
    params, so an existing caller/test that only cares about badge
    PLACEMENT keeps working unchanged): resolved ONCE per strategy via
    resolve_retention_color_source(), same "one shared color_source
    across every block/variant icon of the same strategy" precedent
    build_isolation_overlay() already establishes.

    Strategies are visited in a stable (name, id) order so that, when
    several strategies share a block, their icons always stack in the
    same left-to-right order across rebuilds -- otherwise dict iteration
    order (insertion order in practice, but not a guarantee to build on)
    could silently reshuffle which icon sits where. A block whose owner
    is currently hidden simply isn't materialized this pass, same
    "not materialized, not discarded" precedent every other diagram-only
    placement here already follows (see SupplyPortPlacement's own
    docstring)."""
    icons: dict[str, list[RetentionBadgeIconItem]] = {}
    block_counts: dict[str, int] = {}
    top_edge_owners = top_edge_supply_port_owners(supply_port_icons or {})
    for strategy in sorted(strategies.values(), key=lambda s: (s.name, s.id)):
        strategy_icons = []
        color_source = resolve_retention_color_source(strategy, supply_sets or {}, supply_port_icons or {})
        for owner_path in sorted(owner_paths_for_elements(strategy.elements)):
            box = boxes.get(owner_path)
            if box is None:
                continue
            index = block_counts.get(owner_path, 0)
            block_counts[owner_path] = index + 1
            # Bug fix (explicit request, "sram example" repro -- see
            # supply_port_overlay.top_edge_supply_port_owners()'s own
            # docstring): Retention is ALWAYS interior/top-right corner
            # (no location concept of its own, unlike Isolation/Level
            # Shifter), so it unconditionally needs this same clearance
            # whenever the block has a top-edge Supply Port/Internal
            # Supply.
            extra_top_clearance = INTERIOR_TOP_MARGIN_FOR_SUPPLY_PORT if owner_path in top_edge_owners else 0.0
            strategy_icons.append(RetentionBadgeIconItem(strategy.id, box, index, color_source, extra_top_clearance))
        icons[strategy.id] = strategy_icons
    return icons
