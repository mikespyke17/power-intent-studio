"""Shared base hierarchy diagram builder.

Both hierarchy_view (Visualize Design tab) and power_intent_view (Power
Intent tab) render from this exact same layout code, not two independently
maintained diagrams that happen to look similar -- see
docs/frontend_milestone2_power_intent_visualization.md for why "not a
separate rendering" specifically meant extracting this shared builder
rather than duplicating layout logic per tab.

Nested "block inside block" (matrioska) layout: each instance's box fully
contains its own children's boxes, packed via MaxRects bin-packing (see
shared/diagram/rect_packing.py's own docstring -- a pure-Python,
Qt-independent module, unit-tested on its own in
tests/frontend/test_rect_packing.py before being wired in here) rather
than a uniform grid or fixed rows -- both of those were tried first (as
was an earlier hand-rolled skyline packer, also replaced) and all left
real, reported amounts of wasted white space on a real design that mixes
a few complex leaf modules with many simple integration-only ones (the
common real shape -- inner blocks hold the real logic, outer levels just
wire things together): a uniform grid pads every cell to the size of the
single largest child, row-based packing wastes the vertical gap below a
short child sitting next to a tall one in the same row, and even the
skyline packer only ever grew space downward/rightward from a single
profile. MaxRects tracks the *actual* remaining free rectangles (see
_pack_children()) and best-area-fits each child into them, letting a
later child reuse leftover space irregularly shaped in ways a single
skyline profile can't represent -- genuinely L/T-shaped occupancy, not
just rectangular rows or a grid. Inset from the parent's own border by a
fixed padding plus a title strip reserved for the instance-name label. A
definition with no instances -- always true for a black-box `.lib` macro, see
backend/elaborator/lib/elaborate.py's module docstring: a macro's
Definition has permanently empty `instances` -- is drawn as a plain leaf
box with nothing nested inside it, sized by its own complexity (see
_leaf_size()) rather than a fixed size regardless of content. No
connecting lines are drawn between levels; nesting itself conveys the
hierarchy, replacing the previous layered-row-plus-connector-line
rendering.

A leaf box centers its instance-name label; a container box keeps its
label pinned to the top-left title strip, since a container's middle is
occupied by its own nested children, not empty space the way a leaf's is.

Fill/pen encode language, not nesting depth or leaf-vs-container: every
RTL box (is_black_box=False) is white-filled with a normal-weight
border; every macro box (is_black_box=True) is grey-filled with a
thicker border, regardless of where it sits in the nesting. A caveat
worth naming, not silently absorbed: because a container box's white
fill sits *between* it and any power-intent domain-region overlay drawn
behind the whole scene (power_intent_view/diagram_overlay.py), a domain
whose only member sits two-or-more nesting levels deep can have its
region's padding margin partly hidden behind an intervening container's
own fill wherever the two overlap on screen -- Qt always draws a box's
descendants on top of it regardless of Z-value, so there's no cheap
Z-order fix for this; a real one would mean re-parenting each region
into the nesting tree itself, not attempted here.

A selected RTL box switches to a fluorescent-yellow border ONLY
(set_highlighted(), _HIGHLIGHT_PEN -- no fill change at all, explicit
request superseding an earlier light-blue border+fill combination)
regardless of nesting depth -- `self._boxes` in hierarchy_diagram_view.py
is a flat {path: InstanceBoxItem} map covering every box in the
hierarchy, so a deeply-nested box highlights exactly the same way a
top-level one does. The border fully replaces the box's own normal
_RTL_PEN while highlighted, not blended with it; the interior fill
(_RTL_BRUSH, or a Power Domain's own _domain_brush) is left completely
untouched either way. Selecting
a container cascades the highlight down into every one of its own
nested children too (this box included, plus its whole subtree), so the
selection visually reads as "this whole assembly," not just the outer
border -- but never cascades *up*: selecting a child never highlights
its own unselected ancestor. That asymmetric cascade direction (down
only, done by the caller -- see hierarchy_diagram_view.py's
`_effective_highlight_set()`) is deliberate, not an oversight: an
ancestor visually contains its selected descendant already just by
nesting, so highlighting it too would be redundant, but a descendant
does not visually imply anything about its ancestor.

A macro (is_black_box) box is a deliberate, explicitly-requested
exception to all of the above: its grey color never changes, whether
it's directly selected or only cascade-highlighted via a selected
ancestor -- see set_highlighted()'s own docstring. Its Qt isSelected()
state and controller-level selection membership are unaffected by this
(a direct click still selects it normally); only its rendered color is
pinned, so grey reliably means "black-box cell" independent of the
current selection.

`hidden_paths` (build_hierarchy_scene's second argument) drives the
Hierarchy Tree's Hide/Unhide (hierarchy_view/hierarchy_tree_widget.py):
an instance whose path is in the set is excluded from the layout
entirely -- not drawn, not sized for, not recursed into -- so its
ancestors' boxes shrink to fit only what's still visible, rather than
leaving a blank gap or just dimming the hidden block in place. Callers
that don't care about this (power_intent_view) simply never pass it,
defaulting to "everything visible," identical to the pre-hide-feature
behavior.

Interface instances (Definition.is_interface=True) are excluded the same
way as a hidden path, but unconditionally rather than by user choice --
an interface is a bundle of wiring with modport constraints, not a block
of logic, so it never belongs in this diagram at all (same reasoning as
the Hierarchy Tree; see hierarchy_tree_widget.py's own docstring). Its
signals surface instead as PortList rows on whichever module has an
interface-typed port referencing it (properties_table_widget.py).
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QPainterPath, QPen
from PySide6.QtWidgets import QGraphicsRectItem, QGraphicsScene, QGraphicsSimpleTextItem

from typing import Optional

from frontend.src.shared.diagram.rect_packing import Block, pack_rectangles
from frontend.src.shared.models.rtl import Definition, Design, HierarchyNode, Instance, Port, PortDirection
from frontend.src.shared.models.rtl_io import module_io_ports

LEAF_WIDTH = 140.0
LEAF_HEIGHT = 70.0
TITLE_HEIGHT = 22.0  # space reserved at the top of a container box for its instance-name label
PADDING = 28.0  # inset between a container's border and its children (explicit request: doubled from 14.0 -- blocks were too tightly abutted)
GAP = 36.0  # space between sibling blocks (explicit request: doubled from 18.0, same reason) -- "a bit of space between blocks"

_COMPLEXITY_GROWTH_PER_UNIT = 6.0  # extra px (width and height) per port/net/sequential-element on a leaf
_MAX_COMPLEXITY_GROWTH = 220.0  # cap so one outsized leaf module doesn't dwarf its siblings in the same grid

# Z-values, lowest to highest -- power-intent overlay items (domain
# regions, switch icons, badges) are added on top of this base layer by
# power_intent_view, never interleaved with it.
Z_INSTANCE_BOX = 1
Z_PORT_ANCHOR = 2

_RTL_BRUSH = QColor("#ffffff")
_RTL_PEN = QPen(QColor("#404040"), 1.5)
_MACRO_BRUSH = QColor("#c8c8c8")
_MACRO_PEN = QPen(QColor("#202020"), 3.0)
# Bug 2 (explicit request): fluorescent-yellow, BORDER-ONLY -- replaces
# the earlier light-blue border+fill combination. Same RGB (255, 255, 0)
# as power_intent_view/supply_port_overlay.py's own HIGHLIGHT_COLOR (the
# shared hover/click-selection highlight used throughout that tab's own
# diagram) -- kept as its own small, independent constant rather than
# importing that one directly, since this module is deliberately kept
# free of any power-intent awareness at all (see this module's own
# docstring) and a 3-int-literal colour is a trivial, low-risk
# duplication, not real shared logic.
_HIGHLIGHT_PEN = QPen(QColor(255, 255, 0), 3.0)  # selected-box border, either direction -- see set_highlighted()


class InstanceBoxItem(QGraphicsRectItem):
    """One hierarchy node's box. Exposes port_anchor() so overlay badges
    (isolation/level-shifter/retention -- power_intent_view's job, not
    this module's) have a stable, parent-relative attachment point.

    Also exposes set_highlighted() (same interface as power_intent_view's
    SelectableItemMixin, duck-typed rather than shared via inheritance --
    this module has no power-intent awareness at all): a PowerDomain
    selection highlights both its region item and every member instance
    box, and those boxes are this class, not a power_intent_view type.

    `rect` is always local (origin at (0, 0)) -- nesting position is
    expressed via setParentItem()/setPos(), not by baking an offset into
    the rect itself, so a box's own children can be parented directly to
    it and placed in its local coordinate system.
    """

    def __init__(
        self,
        path: str,
        instance: Instance | None,
        definition: Definition,
        rect: QRectF,
        is_leaf: bool,
        io_ports: Optional[list[Port]] = None,
    ):
        super().__init__(rect)
        self.path = path
        self.instance = instance
        self.definition = definition
        # The block's FULL I/O: plain ports plus every signal its
        # SystemVerilog interface ports expose (shared/models/rtl_io.py).
        # `definition.ports` alone misses the interface signals -- which
        # is how the Isolation/Level Shifter pickers lost them. Resolving
        # an interface needs the whole Design, which a box does not hold,
        # so the builder computes it; a box built without one (tests,
        # older callers) falls back to the plain ports.
        self.io_ports: list[Port] = list(io_ports) if io_ports is not None else list(definition.ports)
        # Stored (not just used locally for label centering below) so
        # power_intent_view's own supply_port_overlay.py can key its
        # "no bottom/interior anchor" rule off it directly -- a leaf
        # (no visible child hierarchy blocks; a macro is always one,
        # since a black-box definition can never have children) has
        # nothing "below" for that anchor to ever meaningfully connect
        # to. See SupplyPortIconItem's own docstring.
        self.is_leaf = is_leaf
        # Generic "something needs to know when my RECT (not just my
        # pos()) changes" registry -- bug fix (explicit request): a
        # leaf block's instance-name label, an edge-anchored Supply
        # Port icon, and a Retention badge (power_intent_view's own
        # concern -- this module stays power-intent-agnostic, same
        # "type-agnostic registry, one-way dependency" precedent
        # SupplyPortIconItem.on_move_callbacks already establishes for
        # POSITION changes) all computed their own local offset ONCE,
        # from whatever rect() existed at construction time -- a LATER
        # resize (e.g. the "make room for a new element" expansion
        # logic growing THIS SAME box for some other, unrelated
        # element) left every one of them silently stale, still
        # positioned for the OLD geometry. setRect() below fires this
        # for every registered callback, but only for a genuine size
        # change -- see setRect()'s own docstring.
        self.on_resize_callbacks: list = []
        # Bug fix (explicit request -- a recurring regression, previously
        # reported as "Power Switch not positioned correctly after prior
        # insertions"): a QGraphicsItem's own itemChange(ItemPositionHasChanged)
        # fires ONLY for a change to ITS OWN local pos() -- Qt does NOT
        # fire it on a CHILD just because an ANCESTOR moved, even though
        # the child's own SCENE position (and anything tracking it, e.g.
        # a scene-level PowerSwitchIconItem registered on a Supply Port
        # icon's own on_move_callbacks) visibly shifts too. Confirmed
        # empirically: reflow_hierarchy_scene()'s own setPos() calls
        # (e.g. a SIBLING block repacking to make room for a brand-new
        # element added to a DIFFERENT block) previously left every such
        # dependent silently stale -- a Power Switch rendering above a
        # target on a box that just got repositioned never actually
        # followed it. This registry (fired by setPos() below, mirroring
        # on_resize_callbacks/setRect()'s own established pattern for
        # SIZE changes) is the "this box's own POSITION changed" analog
        # -- SupplyPortIconItem registers onto it to re-notify its own
        # dependents (see that class's own _on_owner_box_moved()).
        self.on_move_callbacks: list = []
        self.setZValue(Z_INSTANCE_BOX)
        if definition.is_black_box:
            self.setBrush(_MACRO_BRUSH)
            self.setPen(_MACRO_PEN)
        else:
            self.setBrush(_RTL_BRUSH)
            self.setPen(_RTL_PEN)
        self._normal_pen = self.pen()
        self._normal_brush = self.brush()

        self._label = QGraphicsSimpleTextItem(instance.name if instance is not None else definition.base_name, self)
        self._reposition_label()

        self._port_anchor_offsets: dict[str, QPointF] = {}
        self._layout_port_anchors(rect)
        # Set by set_domain_color() (power_intent_view/power_domain_overlay.py)
        # -- None means "no Power Domain claims this box", i.e. the
        # original white/grey _normal_brush above is still what
        # set_highlighted(False) restores. Kept here (not in
        # power_intent_view) because it has to interact directly with
        # set_highlighted()'s own un-highlight branch below.
        self._domain_brush: QBrush | None = None
        self._is_highlighted = False  # tracks set_highlighted()'s own state -- see its docstring on why this is
        # NOT the same thing as Qt's own isSelected() (a cascade-highlighted descendant is highlighted without
        # ever being individually selected itself); set_domain_color() needs the real one, not that proxy.

    def _layout_port_anchors(self, rect: QRectF) -> None:
        inputs = [p for p in self.definition.ports if p.direction == PortDirection.IN]
        outputs = [p for p in self.definition.ports if p.direction != PortDirection.IN]
        for i, port in enumerate(inputs):
            y = rect.y() + (i + 1) * rect.height() / (len(inputs) + 1)
            self._port_anchor_offsets[port.name] = QPointF(rect.x(), y)
        for i, port in enumerate(outputs):
            y = rect.y() + (i + 1) * rect.height() / (len(outputs) + 1)
            self._port_anchor_offsets[port.name] = QPointF(rect.x() + rect.width(), y)

    def _reposition_label(self) -> None:
        rect = self.rect()
        if self.is_leaf:
            # A leaf's whole interior is otherwise empty -- center the label
            # in it rather than pinning it to the corner like a container
            # (whose corner placement leaves room for nested children below).
            label_rect = self._label.boundingRect()
            self._label.setPos(rect.center().x() - label_rect.width() / 2, rect.center().y() - label_rect.height() / 2)
        else:
            self._label.setPos(rect.x() + 6, rect.y() + 6)

    def setRect(self, *args) -> None:
        """Bug fix (explicit request): overriding this (rather than
        leaving every caller of setRect() responsible for remembering
        to re-derive dependent geometry itself) is what makes the
        on_resize_callbacks contract in __init__'s own docstring
        actually hold -- re-centers this box's own instance-name label
        and refreshes _port_anchor_offsets, then notifies every
        registered callback, exactly once, on every genuine size
        change. Guarded on an ACTUAL size change (not just any call) --
        every real call site already only calls setRect() when the
        rect actually differs (see reflow_hierarchy_scene()'s own
        `if box.rect() != new_rect:` guard), but staying defensive here
        costs nothing and protects any future/test caller that doesn't
        bother with that same guard."""
        before = self.rect()
        super().setRect(*args)
        after = self.rect()
        if after.size() == before.size():
            return
        self._reposition_label()
        self._layout_port_anchors(after)
        for callback in list(self.on_resize_callbacks):
            callback()

    def setPos(self, *args) -> None:
        """Bug fix -- see on_move_callbacks' own docstring in __init__
        for the full "why". Same "override so every caller gets this for
        free, guard on an ACTUAL change" shape as setRect() above, just
        for POSITION instead of SIZE."""
        before = self.pos()
        super().setPos(*args)
        after = self.pos()
        if after == before:
            return
        for callback in list(self.on_move_callbacks):
            callback()

    def port_anchor(self, port_name: str) -> QPointF:
        """Scene-coordinate position of `port_name` along this box's edge.
        Raises KeyError for an unknown port -- callers (overlay badge
        placement) should already have validated the port exists via
        resolution, not guess at a fallback position for a bad name."""
        return self.mapToScene(self._port_anchor_offsets[port_name])

    def port_anchor_local(self, port_name: str) -> QPointF:
        """Item-local-coordinate position of `port_name`, for a caller
        that wants to setParentItem() a badge onto this box (so the badge
        tracks the box automatically if it ever moves) rather than place
        it at a fixed scene position."""
        return self._port_anchor_offsets[port_name]

    def set_highlighted(self, highlighted: bool) -> None:
        """Changes ONLY the border pen for an RTL box -- fluorescent
        yellow, border-only (bug 2, explicit request: replaces an
        earlier light-blue border+fill combination; the interior fill is
        deliberately left completely untouched now, still whatever
        _normal_brush/_domain_brush already had it as). A macro
        (is_black_box) box is a deliberate, explicitly-requested
        exception: its grey color never changes here at all, whether
        it's directly selected or only cascade-highlighted via a
        selected ancestor (see hierarchy_diagram_view.py's
        `_effective_highlight_set()`) -- a macro's own Qt isSelected()
        state and controller-level selection membership are untouched by
        this (a direct click still selects it normally, e.g. for the
        Properties panel), only its *color* stays fixed, so grey reliably
        means "black-box cell" regardless of what's currently selected."""
        if self.definition.is_black_box:
            return
        self._is_highlighted = highlighted
        self.setPen(_HIGHLIGHT_PEN if highlighted else self._normal_pen)

    def set_domain_color(self, color: QColor | None, transparency: float = 0.5) -> None:
        """Explicit request: once a Power Domain is created, every box in
        its subtree gets the domain's own colour, except a macro
        (is_black_box) box, which keeps its grey ALWAYS -- same
        unconditional exception set_highlighted() already makes, for the
        same reason (grey reliably means "black-box cell", independent
        of anything else going on). `color=None` clears it, reverting to
        the box's own original white/grey. `transparency` is a 0..1
        fraction (0 = fully opaque, 1 = fully invisible) -- alpha is
        `1 - transparency`, so the documented 50% default transparency
        lands on alpha 0.5, a genuinely translucent fill the underlying
        hierarchy nesting/labels stay legible through. Applies the brush
        unconditionally now, regardless of `_is_highlighted` -- since
        set_highlighted() (bug 2) no longer touches the fill at all
        (border-only), there is no longer a "the highlight is currently
        drawing over this" reason to ever defer it; deferring would now
        actually be a real staleness bug (a domain-colour change made
        while a box happens to be highlighted would otherwise not become
        visible until the highlight later cleared, even though the fill
        was plainly showing the whole time)."""
        if self.definition.is_black_box:
            return
        if color is None:
            self._domain_brush = None
        else:
            tinted = QColor(color)
            tinted.setAlphaF(max(0.0, min(1.0, 1.0 - transparency)))
            self._domain_brush = QBrush(tinted)
        self.setBrush(self._domain_brush if self._domain_brush is not None else self._normal_brush)

    def paint(self, painter, option, widget=None) -> None:
        """Bug fix: a translucent domain fill (set_domain_color()) used
        to visibly BLEED across Power Domain boundaries -- e.g. a green
        inner-domain box nested inside a blue outer-domain box rendered
        as a green/blue BLEND, not pure green, even though each box's
        OWN brush color was already correctly, independently assigned
        (apply_domain_coloring() gives every box in the tree its own
        direct domain-color assignment, "deepest wins" -- verified by
        reading brush.color() directly: never blended at the DATA
        level). Root cause was Qt's own parent-then-children paint
        order: a container box's own translucent fill covers its FULL
        rect first (including the area under every nested child), and
        each child then paints ITS OWN translucent fill on top of that
        already-tinted backdrop -- alpha compositing two translucent
        layers instead of showing one clean colour. Confirmed
        empirically before fixing (measuring actual rendered pixel
        values against the "single blend" vs "double blend" formulas,
        not assumed from reading code alone) -- this also silently
        affected a SAME-domain nested box too (e.g. a sibling under the
        same outer domain, no visible hue change but measurably darker/
        over-saturated than the documented transparency value actually
        promises).

        Fixed by punching a hole for every DIRECT child InstanceBoxItem
        out of this box's OWN fill region before painting it -- so a
        child's own fill always composites against the true, untinted
        backdrop, never a translucent ancestor fill. Applies
        unconditionally (domain-coloured or the plain default fill
        alike) since it's a correct no-op either way for an opaque
        fill (the child already fully overwrites that region regardless)
        and only visually corrective for a translucent one -- keeps this
        purely structural, no Power-Domain-specific concept needed here
        at all (this module stays power-intent-agnostic, same "generic,
        purpose-agnostic mechanism" precedent extra_top_margin already
        established). The border pen is drawn separately, over the
        FULL (unpunched) rect -- only the fill needs excluding, not the
        outline.

        Real, confirmed PySide6/shiboken hazard found writing this fix
        (same "verify empirically, don't assume" discipline this
        codebase already applies elsewhere): `QPainterPath -= QRectF`
        (the operator-overload spelling, relying on an implicit
        QRectF->QPainterPath conversion) segfaults the interpreter here
        -- reproduced in isolation, with and without nested children,
        confirmed the exact line via a bisected minimal repro. Using
        the explicit `.subtracted(QPainterPath)` method instead, with
        each child's own rect wrapped in its own QPainterPath first,
        does not crash -- used below instead of the operator form."""
        child_boxes = [c for c in self.childItems() if isinstance(c, InstanceBoxItem)]
        if not child_boxes:
            super().paint(painter, option, widget)
            return
        fill_path = QPainterPath()
        fill_path.addRect(self.rect())
        for child in child_boxes:
            child_path = QPainterPath()
            child_path.addRect(self.mapRectFromItem(child, child.rect()))
            fill_path = fill_path.subtracted(child_path)
        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self.brush())
        painter.drawPath(fill_path)
        painter.restore()
        painter.save()
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(self.pen())
        painter.drawRect(self.rect())
        painter.restore()


def _definition_ids_containing_macro(design: Design) -> frozenset[str]:
    """Explicit request ("Macros can never be hidden"): every Definition
    (by id) that IS a `.lib` macro (`.is_black_box`) or TRANSITIVELY
    instantiates one at any nesting depth, computed once per layout
    pass over the design's own STATIC definition graph -- independent
    of `hidden_paths`/any specific instance path, since "does this
    module contain a macro" is a property of the module itself, not of
    where it happens to be instantiated. Used by `_visible_children()`
    to exempt a Macro from a "hide hierarchy" action even when nested
    several levels inside the hidden subtree. A definition with a
    cyclic instantiation graph (shouldn't exist in real RTL, defensive
    only) can't infinite-loop here: `memo` is seeded False for a
    definition_id the moment recursion into it starts, so a cycle
    resolves to "no macro found via this path" rather than recursing
    forever."""
    result: set[str] = set()
    memo: dict[str, bool] = {}

    def contains_macro(definition_id: str) -> bool:
        if definition_id in memo:
            return memo[definition_id]
        memo[definition_id] = False
        definition = design.definitions[definition_id]
        found = definition.is_black_box or any(contains_macro(inst.definition_id) for inst in definition.instances)
        memo[definition_id] = found
        if found:
            result.add(definition_id)
        return found

    for definition_id in design.definitions:
        contains_macro(definition_id)
    return frozenset(result)


def _visible_children(
    design: Design,
    definition: Definition,
    path: str,
    hidden_paths: frozenset[str],
    macro_definition_ids: frozenset[str] = frozenset(),
    macro_only: bool = False,
) -> list[Instance]:
    # Interface instances are excluded unconditionally, not just when
    # explicitly hidden -- an interface is a bundle of wiring with modport
    # constraints, not a block of logic, so it never belongs in this
    # "block inside block" diagram at all (see module docstring). Its
    # signals surface instead as PortList rows on whichever module has an
    # interface-typed port referencing it (properties_table_widget.py).
    #
    # `macro_definition_ids`/`macro_only` (explicit request, "Macros can
    # never be hidden"): a child whose own path is in `hidden_paths` --
    # or ANY child at all, once `macro_only` is already True, meaning
    # some ANCESTOR was itself hidden-but-exempted -- is still included
    # if (and only if) it IS a macro or transitively contains one
    # (`c.definition_id in macro_definition_ids`); every other
    # hidden/macro-only-scoped child is excluded exactly as before this
    # feature existed. The surrounding non-Macro structure still
    # collapses normally -- only the path(s) actually leading to a
    # Macro stay visible, per the recursive `macro_only` propagation
    # each of _compute_size()/_place()/_reflow_place() applies to a
    # child's own further recursion (see each one's own docstring).
    result = []
    for c in definition.instances:
        if design.definition(c.definition_id).is_interface:
            continue
        hidden = macro_only or f"{path}/{c.name}" in hidden_paths
        if not hidden or c.definition_id in macro_definition_ids:
            result.append(c)
    return result


def _leaf_size(definition: Definition) -> tuple[float, float]:
    """Bigger leaf modules get bigger boxes, proxied by port + net +
    sequential-element count (fields already on Definition, no new data
    needed) rather than a fixed size regardless of content -- makes the
    diagram reflect how big the underlying logic actually is. Capped so
    one outsized module doesn't dwarf its siblings in the same grid cell
    row/column."""
    complexity = len(definition.ports) + len(definition.nets) + len(definition.sequential_elements)
    growth = min(complexity * _COMPLEXITY_GROWTH_PER_UNIT, _MAX_COMPLEXITY_GROWTH)
    return LEAF_WIDTH + growth, LEAF_HEIGHT + growth


def _pack_children(sizes: list[tuple[float, float]]) -> tuple[float, float, list[tuple[float, float]]]:
    """Adapts pack_rectangles() (shared/diagram/rect_packing.py -- pure
    Python, no Qt dependency, independently unit-tested in
    tests/frontend/test_rect_packing.py) to this module's own
    (content_width, content_height, offsets) contract, so _compute_size()
    and _place() below don't need to know or care that the actual
    packing happens in a separate, Qt-free module: `sizes` (positional,
    one (width, height) per child) is wrapped into indexed Block objects,
    packed with GAP as the required padding between every pair of
    siblings (same role GAP always played for the packer this replaced),
    and the results are (a) reduced to the bounding box of the whole
    packed layout for content_width/content_height, and (b) returned as
    offsets in the *same order as `sizes`*, not MaxRects' own
    largest-first placement order -- callers don't need to care about
    that internal order, same guarantee the previous skyline packer gave.
    """
    if not sizes:
        return 0.0, 0.0, []

    blocks = [Block(id=str(i), width=w, height=h) for i, (w, h) in enumerate(sizes)]
    placed = pack_rectangles(blocks, padding=GAP)

    content_width = max(r.x + r.w for r in placed.values())
    content_height = max(r.y + r.h for r in placed.values())
    offsets = [(placed[str(i)].x, placed[str(i)].y) for i in range(len(sizes))]
    return content_width, content_height, offsets


def _compute_size(
    design: Design,
    path: str,
    definition: Definition,
    hidden_paths: frozenset[str],
    extra_top_margin: Optional[dict[str, float]] = None,
    extra_left_margin: Optional[dict[str, float]] = None,
    extra_interior_top: Optional[dict[str, float]] = None,
    extra_interior_bottom: Optional[dict[str, float]] = None,
    extra_interior_min_width: Optional[dict[str, float]] = None,
    macro_definition_ids: frozenset[str] = frozenset(),
    macro_only: bool = False,
) -> tuple[float, float]:
    """Bottom-up sizing: a leaf is sized by its own complexity (see
    _leaf_size()); a container is just big enough to hold its *visible*
    children packed together (see _pack_children()), plus padding and
    the title strip -- a hidden child contributes nothing to its
    parent's size, which is how the parent visibly shrinks when a
    descendant is hidden. Each child's own size here is itself the
    result of this same function recursing into it first -- i.e. a
    container's effective width/height one level up is always its own
    already-packed bounding box, never a fixed/estimated size.

    `extra_top_margin` (optional, {path: pixels}) reserves additional
    height ABOVE this box's own natural size -- generic and power-
    intent-agnostic by design (this module stays pure RTL layout, see
    its own module docstring): the caller decides WHY a path needs
    extra room above it (e.g. power_intent_view.py's own Power Switch
    icons, which render above their target's box -- see
    _PowerIntentGraphicsView._extra_top_margin()), this function only
    ever reserves the space. Folded into the RETURNED height so the
    box's own PARENT reserves enough room for it among ITS OWN
    siblings via the ordinary recursive packing -- no separate sibling-
    reflow pass needed. _place() below is what actually keeps the
    box's own visible rect at its natural (unmargined) size and shifts
    it down within its own allocated slot, leaving genuine empty scene
    space above it.

    `extra_left_margin` (optional, {path: pixels}, explicit request --
    Supply-Port/Isolation "make room" layout): the exact mirror image
    of `extra_top_margin` on the horizontal axis -- reserves additional
    width to the LEFT of this box's own natural size, for something
    rendering outside/beside it (e.g. an externally-placed Isolation
    badge, Location=parent/fanout -- see
    _PowerIntentGraphicsView._extra_left_margin()). Same "just reserve
    the space, the caller decides why" contract.

    `extra_interior_top` (optional, {path: pixels}, explicit request):
    UNLIKE the two margins above (which reserve space OUTSIDE this
    box, shifting the box itself within its own parent's packing),
    this one reserves space INSIDE this box's own top -- both growing
    its own height AND (for a container with children -- see _place()/
    _reflow_place()) pushing its own children's packing origin down by
    the same amount, so the box's own top strip stays genuinely clear
    for something anchored there instead (a Supply Port/Internal
    Supply on the box's own top edge whose own children would
    otherwise be packed right up against it; an internally-placed
    Isolation badge near the top-left interior corner, Location=self).
    For a LEAF (no children to push down), this still grows the box's
    own height by the same amount -- "make room inside" degenerates to
    "grow the box" exactly when there's nothing inside to push.

    `extra_interior_bottom` (optional, {path: pixels}, explicit request
    -- Ground-anchor "make room for power net routing" layout, the
    exact mirror image of `extra_interior_top` on the vertical axis):
    reserves additional room inside this box's own BOTTOM, for a
    Ground-type Supply Port/Internal Supply/macro PG pin anchored
    there (see _PowerIntentGraphicsView._extra_interior_bottom()) --
    simpler than `extra_interior_top`'s own case, since nothing needs
    to be pushed to make room at the bottom (children already pack
    from the top; growing the box's own total height is enough to
    leave the extra space genuinely clear below whatever's already
    packed) -- so, unlike `extra_interior_top`, this never affects
    `start_y` in _place()/_reflow_place() at all, only the height
    returned here.

    `extra_interior_min_width` (optional, {path: pixels}, explicit
    request -- Supply-Port/Internal-Supply adjacent-label-collision fix,
    the WIDTH-axis sibling of `extra_interior_top`/`extra_interior_bottom`):
    a MINIMUM total width this box's own natural size must reach --
    unlike every margin/interior reservation above (which ADD room),
    this one only ever GROWS the box up to the given floor (via `max()`,
    never shrinks it below its own natural size) so multiple same-edge
    Supply Port/Internal Supply labels have enough physical room to sit
    side by side without overlapping (see
    _PowerIntentGraphicsView._extra_interior_min_width()) -- most
    relevant for a LEAF box, whose own natural LEAF_WIDTH is otherwise
    fixed regardless of how many top/bottom-edge icons end up anchored
    to it.

    `macro_definition_ids`/`macro_only` (explicit request, "Macros can
    never be hidden" -- see _visible_children()'s own docstring for the
    inclusion rule): `macro_only` is propagated to EACH child's own
    recursive call as `macro_only or f"{path}/{c.name}" in hidden_paths`
    -- i.e. it starts True the moment a hidden-but-macro-exempted child
    is entered, and stays True for every descendant from there on, so
    only the path(s) actually leading to a Macro stay visible all the
    way down; every other sibling/descendant within that same
    hidden subtree still collapses normally."""
    children = _visible_children(design, definition, path, hidden_paths, macro_definition_ids, macro_only)
    if not children:
        width, height = _leaf_size(definition)
    else:
        child_sizes = [
            _compute_size(
                design,
                f"{path}/{c.name}",
                design.definition(c.definition_id),
                hidden_paths,
                extra_top_margin,
                extra_left_margin,
                extra_interior_top,
                extra_interior_bottom,
                extra_interior_min_width,
                macro_definition_ids,
                macro_only or f"{path}/{c.name}" in hidden_paths,
            )
            for c in children
        ]
        content_width, content_height, _offsets = _pack_children(child_sizes)
        width = max(LEAF_WIDTH, content_width + PADDING * 2)
        height = TITLE_HEIGHT + content_height + PADDING * 2
    width = max(width, (extra_interior_min_width or {}).get(path, 0.0))
    interior_top = (extra_interior_top or {}).get(path, 0.0)
    interior_bottom = (extra_interior_bottom or {}).get(path, 0.0)
    height += interior_top + interior_bottom
    top_margin = (extra_top_margin or {}).get(path, 0.0)
    left_margin = (extra_left_margin or {}).get(path, 0.0)
    return width + left_margin, height + top_margin


def _place(
    design: Design,
    path: str,
    instance: Instance | None,
    definition: Definition,
    width: float,
    height: float,
    parent_item: InstanceBoxItem | None,
    boxes: dict[str, InstanceBoxItem],
    hidden_paths: frozenset[str],
    extra_top_margin: Optional[dict[str, float]] = None,
    extra_left_margin: Optional[dict[str, float]] = None,
    extra_interior_top: Optional[dict[str, float]] = None,
    extra_interior_bottom: Optional[dict[str, float]] = None,
    extra_interior_min_width: Optional[dict[str, float]] = None,
    macro_definition_ids: frozenset[str] = frozenset(),
    macro_only: bool = False,
) -> InstanceBoxItem:
    """`width`/`height` here are the box's own ALLOCATED slot size (as
    returned by _compute_size(), every margin already folded in) -- the
    box's own VISIBLE rect is built at its natural (unmargined) size
    instead, so a caller positioning this box within its own slot (see
    the loop below, and build_hierarchy_scene()'s own root placement)
    must offset it by its own top/left margin to leave that reserved
    space genuinely empty around it, not swallowed invisibly inside the
    box's own rect. `extra_interior_top`/`extra_interior_bottom`, unlike
    the two margins, stay INSIDE the box's own natural size (see
    _compute_size()'s own docstring) -- already folded into `height` by
    the time it reaches here, so `own_height` (which only ever
    subtracts the OUTSIDE `top_margin`) already reflects both
    correctly with no separate handling needed for either; only
    `interior_top` pushes this box's OWN children's packing origin
    (`start_y`) down -- `interior_bottom` never needs to, since it
    reserves room below whatever's already packed, not above it.
    `macro_definition_ids`/`macro_only` -- see _compute_size()'s own
    docstring, same propagation rule."""
    top_margin = (extra_top_margin or {}).get(path, 0.0)
    left_margin = (extra_left_margin or {}).get(path, 0.0)
    interior_top = (extra_interior_top or {}).get(path, 0.0)
    own_width = width - left_margin
    own_height = height - top_margin
    children = _visible_children(design, definition, path, hidden_paths, macro_definition_ids, macro_only)
    box = InstanceBoxItem(
        path, instance, definition, QRectF(0, 0, own_width, own_height), is_leaf=not children,
        io_ports=module_io_ports(design, definition),
    )
    if parent_item is not None:
        box.setParentItem(parent_item)
    boxes[path] = box

    if not children:
        return box

    child_sizes = [
        _compute_size(
            design,
            f"{path}/{c.name}",
            design.definition(c.definition_id),
            hidden_paths,
            extra_top_margin,
            extra_left_margin,
            extra_interior_top,
            extra_interior_bottom,
            extra_interior_min_width,
            macro_definition_ids,
            macro_only or f"{path}/{c.name}" in hidden_paths,
        )
        for c in children
    ]
    content_width, _content_height, offsets = _pack_children(child_sizes)
    start_x = (own_width - content_width) / 2
    start_y = TITLE_HEIGHT + PADDING + interior_top

    for child, (child_width, child_height), (off_x, off_y) in zip(children, child_sizes, offsets):
        child_definition = design.definition(child.definition_id)
        child_box = _place(
            design,
            f"{path}/{child.name}",
            child,
            child_definition,
            child_width,
            child_height,
            box,
            boxes,
            hidden_paths,
            extra_top_margin,
            extra_left_margin,
            extra_interior_top,
            extra_interior_bottom,
            extra_interior_min_width,
            macro_definition_ids,
            macro_only or f"{path}/{child.name}" in hidden_paths,
        )
        child_top_margin = (extra_top_margin or {}).get(f"{path}/{child.name}", 0.0)
        child_left_margin = (extra_left_margin or {}).get(f"{path}/{child.name}", 0.0)
        child_box.setPos(start_x + off_x + child_left_margin, start_y + off_y + child_top_margin)

    return box


def _reflow_place(
    design: Design,
    path: str,
    definition: Definition,
    width: float,
    height: float,
    boxes: dict[str, InstanceBoxItem],
    hidden_paths: frozenset[str],
    extra_top_margin: Optional[dict[str, float]],
    changed_paths: set[str],
    extra_left_margin: Optional[dict[str, float]] = None,
    extra_interior_top: Optional[dict[str, float]] = None,
    extra_interior_bottom: Optional[dict[str, float]] = None,
    extra_interior_min_width: Optional[dict[str, float]] = None,
    macro_definition_ids: frozenset[str] = frozenset(),
    macro_only: bool = False,
) -> None:
    """Same recursive geometry computation as _place() above, but
    updates the ALREADY-EXISTING InstanceBoxItem at `boxes[path]` in
    place (setRect on it) instead of constructing a brand new one --
    see reflow_hierarchy_scene()'s own docstring for why this needs to
    exist as a genuinely separate function from _place(), not a flag
    on it. A path with no existing box (e.g. currently hidden) is
    silently skipped, same "not materialized, not discarded" precedent
    _place()'s own hidden-path handling already establishes elsewhere
    -- a Macro newly exposed by a hide-state CHANGE only ever gets its
    own box materialized via a full _place()/build_hierarchy_scene()
    rebuild, never through this incremental, geometry-only reflow path.
    `changed_paths` collects every path whose own rect() size and/or
    parent-relative pos() this call actually touched, so the caller
    knows exactly which live overlay items (Power Switch icons,
    connectors, ...) anchored to a moved/resized box need their own
    position re-derived afterward -- see that caller's own docstring.
    `macro_definition_ids`/`macro_only` -- see _compute_size()'s own
    docstring, same propagation rule."""
    box = boxes.get(path)
    if box is None:
        return
    top_margin = (extra_top_margin or {}).get(path, 0.0)
    left_margin = (extra_left_margin or {}).get(path, 0.0)
    interior_top = (extra_interior_top or {}).get(path, 0.0)
    own_width = width - left_margin
    own_height = height - top_margin
    new_rect = QRectF(0, 0, own_width, own_height)
    if box.rect() != new_rect:
        box.setRect(new_rect)
        changed_paths.add(path)

    children = _visible_children(design, definition, path, hidden_paths, macro_definition_ids, macro_only)
    if not children:
        return

    child_sizes = [
        _compute_size(
            design,
            f"{path}/{c.name}",
            design.definition(c.definition_id),
            hidden_paths,
            extra_top_margin,
            extra_left_margin,
            extra_interior_top,
            extra_interior_bottom,
            extra_interior_min_width,
            macro_definition_ids,
            macro_only or f"{path}/{c.name}" in hidden_paths,
        )
        for c in children
    ]
    content_width, _content_height, offsets = _pack_children(child_sizes)
    start_x = (own_width - content_width) / 2
    start_y = TITLE_HEIGHT + PADDING + interior_top

    for child, (child_width, child_height), (off_x, off_y) in zip(children, child_sizes, offsets):
        child_path = f"{path}/{child.name}"
        child_box = boxes.get(child_path)
        if child_box is not None:
            child_top_margin = (extra_top_margin or {}).get(child_path, 0.0)
            child_left_margin = (extra_left_margin or {}).get(child_path, 0.0)
            new_pos = QPointF(start_x + off_x + child_left_margin, start_y + off_y + child_top_margin)
            if child_box.pos() != new_pos:
                child_box.setPos(new_pos)
                changed_paths.add(child_path)
        child_definition = design.definition(child.definition_id)
        _reflow_place(
            design,
            child_path,
            child_definition,
            child_width,
            child_height,
            boxes,
            hidden_paths,
            extra_top_margin,
            changed_paths,
            extra_left_margin,
            extra_interior_top,
            extra_interior_bottom,
            extra_interior_min_width,
            macro_definition_ids,
            macro_only or f"{path}/{child.name}" in hidden_paths,
        )


def reflow_hierarchy_scene(
    design: Design,
    boxes: dict[str, InstanceBoxItem],
    hidden_paths: frozenset[str] = frozenset(),
    root: Optional[HierarchyNode] = None,
    extra_top_margin: Optional[dict[str, float]] = None,
    extra_left_margin: Optional[dict[str, float]] = None,
    extra_interior_top: Optional[dict[str, float]] = None,
    extra_interior_bottom: Optional[dict[str, float]] = None,
    extra_interior_min_width: Optional[dict[str, float]] = None,
) -> set[str]:
    """Recomputes box geometry (size/position) exactly like
    build_hierarchy_scene() does, but MUTATES the ALREADY-EXISTING
    InstanceBoxItem objects in `boxes` in place (setRect/setPos)
    instead of tearing down the scene and building a fresh one.

    Why this needs to exist at all: `extra_top_margin` (see
    _compute_size()'s own docstring) is how a caller reserves room
    above a box for something rendering ABOVE it rather than ON it --
    power_intent_view's own Power Switch icon is the one real user of
    this today. But the caller that FIRST learns a box now needs extra
    margin (power_intent_diagram_view.py's own add_power_switch()) is
    also the same call that must hand back a LIVE, freshly-created
    PowerSwitchIconItem to whoever's waiting on it (the dialog flow, a
    test) -- and a full destructive rebuild (build_hierarchy_scene(),
    called from _rebuild()) would invalidate that very item (and every
    other live Qt object anyone might still be holding a reference to)
    out from under the caller, the exact same "QUndoStack.push()'s
    synchronous first redo() destroying a just-created item" hazard
    this app has hit before -- see _DiagramStateCommand's own docstring.
    This function is the fix: it corrects box geometry to match the
    NEW `extra_top_margin` without destroying or recreating a single
    InstanceBoxItem, so every live reference (the box itself, any icon
    parented to it) stays valid.

    Returns the set of paths whose own box actually moved and/or
    resized -- the caller still needs to re-sync anything whose own
    position is CACHED rather than continuously Qt-parent-derived
    (Power Switch icons are scene-level, not parented to their target,
    precisely to dodge an earlier Z-order bug -- see
    PowerSwitchIconItem's own docstring -- so their own cached position
    goes stale the moment their target's owning box moves without an
    explicit update_position() call; a Supply Port icon itself IS
    parented to its own box and needs no such fixup, Qt already
    reflects a parent's new position in every child's own scenePos()
    automatically -- but its own CONNECTORS still cache their route
    independently and need update_route() called explicitly too, same
    "itemChange() only fires for MY OWN pos(), not a parent's" reason)."""
    changed_paths: set[str] = set()
    root_node = root if root is not None else next(iter(design.walk()))
    macro_definition_ids = _definition_ids_containing_macro(design)
    # Bug fix, "Macros can never be hidden" -- same root-level exemption
    # build_hierarchy_scene() now applies, see that function's own
    # comment for the full "why".
    root_hidden = root_node.path in hidden_paths
    root_exempt = root_hidden and root_node.definition.definition_id in macro_definition_ids
    if root_hidden and not root_exempt:
        return changed_paths
    root_macro_only = root_exempt
    root_box = boxes.get(root_node.path)
    if root_box is None:
        return changed_paths
    root_width, root_height = _compute_size(
        design,
        root_node.path,
        root_node.definition,
        hidden_paths,
        extra_top_margin,
        extra_left_margin,
        extra_interior_top,
        extra_interior_bottom,
        extra_interior_min_width,
        macro_definition_ids,
        root_macro_only,
    )
    root_top_margin = (extra_top_margin or {}).get(root_node.path, 0.0)
    root_left_margin = (extra_left_margin or {}).get(root_node.path, 0.0)
    new_root_rect = QRectF(0, 0, root_width - root_left_margin, root_height - root_top_margin)
    if root_box.rect() != new_root_rect:
        root_box.setRect(new_root_rect)
        changed_paths.add(root_node.path)
    new_root_pos = QPointF(-root_width / 2 + root_left_margin, root_top_margin)
    if root_box.pos() != new_root_pos:
        root_box.setPos(new_root_pos)
        changed_paths.add(root_node.path)
    _reflow_place(
        design,
        root_node.path,
        root_node.definition,
        root_width,
        root_height,
        boxes,
        hidden_paths,
        extra_top_margin,
        changed_paths,
        extra_left_margin,
        extra_interior_top,
        extra_interior_bottom,
        extra_interior_min_width,
        macro_definition_ids,
        root_macro_only,
    )
    return changed_paths


def build_hierarchy_scene(
    design: Design,
    hidden_paths: frozenset[str] = frozenset(),
    root: Optional[HierarchyNode] = None,
    extra_top_margin: Optional[dict[str, float]] = None,
    extra_left_margin: Optional[dict[str, float]] = None,
    extra_interior_top: Optional[dict[str, float]] = None,
    extra_interior_bottom: Optional[dict[str, float]] = None,
    extra_interior_min_width: Optional[dict[str, float]] = None,
) -> tuple[QGraphicsScene, dict[str, InstanceBoxItem]]:
    """Returns (scene, {instance_path: InstanceBoxItem}). No power-intent
    awareness at all -- pure RTL hierarchy layout. `hidden_paths` (see
    module docstring) excludes those instances and their whole subtree
    from the layout; a caller that never hides anything doesn't need to
    pass it at all.

    `root` (explicit request: opening a sub-module in its own diagram
    tab) renders the scene starting from an arbitrary node deep in the
    hierarchy instead of the design's own top -- e.g. `root=HierarchyNode(
    path="top/u_core", ...)` produces exactly the same *kind* of scene
    (same packing/sizing/labeling rules) but rooted at "top/u_core",
    containing only that instance and its own descendants. None (the
    default) preserves the original "always the design's real top"
    behavior exactly. _compute_size()/_place() were already written
    generically off a passed-in (path, instance, definition) rather than
    assuming they're at the design's true top, so no changes were needed
    there -- only this one entry point had the assumption baked in
    (`next(iter(design.walk()))`, always the first/topmost node).

    `extra_top_margin` (optional, {path: pixels}, default None/empty --
    see _compute_size()'s own docstring for the full "why"): every
    EXISTING caller (hierarchy_view's own Visualize Design tab, and
    power_intent_view's own base construction before this parameter
    existed) keeps working completely unchanged by omitting it."""
    scene = QGraphicsScene()
    boxes: dict[str, InstanceBoxItem] = {}

    root_node = root if root is not None else next(iter(design.walk()))
    macro_definition_ids = _definition_ids_containing_macro(design)
    # Bug fix, "Macros can never be hidden" (explicit request): the ROOT
    # node itself can be hidden too (a real, reachable case -- nothing
    # in the tree/hide UI actually prevents hiding the design's own
    # top), and the ORIGINAL check here (`if root_node.path in
    # hidden_paths: return scene, boxes`, unconditional) would still
    # bail out to a completely EMPTY scene even when the root itself
    # transitively contains a Macro -- the exact same exemption
    # `_visible_children()` already grants a DESCENDANT hidden path
    # must apply here too. `root_macro_only` starts the SAME
    # `macro_only` propagation _compute_size()/_place() already
    # establish for a descendant, at the root level.
    root_hidden = root_node.path in hidden_paths
    root_exempt = root_hidden and root_node.definition.definition_id in macro_definition_ids
    if root_hidden and not root_exempt:
        return scene, boxes
    root_macro_only = root_exempt
    root_width, root_height = _compute_size(
        design,
        root_node.path,
        root_node.definition,
        hidden_paths,
        extra_top_margin,
        extra_left_margin,
        extra_interior_top,
        extra_interior_bottom,
        extra_interior_min_width,
        macro_definition_ids,
        root_macro_only,
    )
    root_box = _place(
        design,
        root_node.path,
        root_node.instance,
        root_node.definition,
        root_width,
        root_height,
        None,
        boxes,
        hidden_paths,
        extra_top_margin,
        extra_left_margin,
        extra_interior_top,
        extra_interior_bottom,
        extra_interior_min_width,
        macro_definition_ids,
        root_macro_only,
    )
    root_top_margin = (extra_top_margin or {}).get(root_node.path, 0.0)
    root_left_margin = (extra_left_margin or {}).get(root_node.path, 0.0)
    root_box.setPos(-root_width / 2 + root_left_margin, root_top_margin)
    scene.addItem(root_box)

    return scene, boxes
