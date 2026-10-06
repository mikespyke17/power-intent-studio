"""Power Supply icons on the Power Intent tab's Diagram -- two sources:

- User-placed, via "Add Power Supply" (see hierarchy_diagram_view.py's
  _extra_context_menu_actions hook and power_intent_diagram_view.py's
  override + mouse handling for the connector-drag gesture). Freely
  draggable along a box's edges, user-named, user-colored.
- Auto-derived from a macro (.lib black-box) instance's own PG pins (its
  Definition.ports entries with direction == PortDirection.PG or
  PG_INTERNAL -- SupplyPortPlacement.is_internal distinguishes the two,
  see its own docstring; PG_INTERNAL support added on explicit request,
  see build_supply_port_overlay()'s docstring). Fixed: not draggable, not user-removable -- the pin exists
  because the macro's own definition says so, not because a user placed
  it. (A brief experiment made PG pins draggable too; explicitly
  reverted -- back to fixed for both macro and non-macro/"soft" hierarchy
  instances, i.e. everywhere a PG pin can appear.) Starts a neutral grey;
  once connected to a real (non-fixed) supply port via the same handle-
  drag gesture, it recolors to match that supply's colour (see
  SupplyPortIconItem.recolor()) -- "same colour as the power net which
  will be connected" from the original request, read as "the net's
  identity, proxied by colour, propagates onto the pin once wired up"
  since there is no separate net-colour concept anywhere yet.

Deliberately separate from diagram_overlay.py: that module's items are
all *derived* from a MappedDesign's resolved references (a pure function
of backend data, rebuilt fresh on every load_mapped()). A user-placed
icon here is the opposite -- user-positioned/user-colored state with no
backend field to hold it (UPF/CPF have no notion of diagram layout or
icon colour at all). This first pass is diagram-only: nothing here is
reflected into the tab's PowerIntentDesign or Properties panel (no
Properties row, no backend.modification.ops.add() call) -- see the
"mock-first" scoping decision for this feature. PG-pin icons are a
partial exception -- they *are* derived from real Definition data (the
macro's own ports), just not from a MappedDesign/backend.mapping.

Icon = supply-port.svg (or internal-supply.svg for an "Internal
Supply" -- SupplyPortPlacement.is_internal_supply, see its own
docstring), rendered LIVE every paint() call via a single shared
QSvgRenderer per icon type (_supply_port_svg_renderer()/
_internal_supply_svg_renderer(), each cached with lru_cache so its own
SVG is only ever parsed once for the whole app, not once per icon
instance or per repaint) rather than a cached QPixmap -- see
SupplyPortIconItem's own class docstring for the "why" (a fixed-
resolution raster item was found to stay visibly pixelated at real
diagram zoom levels, confirmed via a live before/after screenshot
comparison, no matter how high the supersampling factor; a genuinely
live vector render is resolution-independent at any zoom).

"Internal Supply" (explicit request) is a second, distinct entity type
sharing this exact same dataclass/dict/icon class/connector/rename-
propagation machinery as a plain "Supply Port" -- the two are
mutually exclusive by hierarchy level (Supply Port only on the design's
true top-level block; Internal Supply only on an inner, non-top-level
block, both excluding macros -- see
power_intent_diagram_view.py's own _is_top_level_box() and placement-
gating docstrings), never both at once on the same box, so there is no
risk of the two kinds colliding in the same owner_path's own name
uniqueness/rename-propagation scope.

"Virtual Supply" (explicit request) is a THIRD, distinct entity type,
sharing this same dataclass/dict/icon class (SupplyPortPlacement.
is_virtual_supply, mutually exclusive with is_internal_supply) but
structurally unlike either of the other two: placeable at ANY
hierarchy level (no level restriction at all -- see
power_intent_diagram_view.py's own add_virtual_supply()), exists
ONLY to drive a macro's own INTERNAL PG pins (a one-directional
connection-target restriction enforced in try_connect_supply_ports(),
never on what may connect TO a macro's internal PG pin -- a regular
Supply Port/Internal Supply remains freely able to, exactly as
before), never belongs to any Power Domain, and is never selectable as
a Supply Set rail (known_supply_names_for_owner()/_globally()/
known_supply_names() all exclude it). Its own icon
(SupplyPortIconItem._dropped_handle_key()) drops its EXTERIOR handle
instead of the interior one every other icon type here drops -- "no
top anchor at all," since nothing can ever drive a Virtual Supply.
Unlike Internal Supply's DERIVED effective states, a Virtual Supply's
Power States are directly AUTHORED (like a plain Supply Port's) and
REQUIRED at creation, never empty -- see add_virtual_supply()'s own
docstring. Name uniqueness (unlike Supply Port/Internal Supply, which
freely share names with each other -- CHANGE 1) is enforced ONLY at
Virtual Supply creation/rename time, checked against the FULL set of
every Supply Port/Internal Supply/Virtual Supply name in the design.

Per-instance colour (explicit request -- the SVG asset itself was
updated to always render as a solid black silhouette specifically so it
can act as a mask/stencil) is applied at PAINT time, not baked into the
SVG source the way the old string-replace-based tint used to work: see
shared/diagram/svg_tint.py's own render_tinted_svg() (QPainter
composition -- draw the black silhouette into an isolated temporary
image, then QPainter.CompositionMode_SourceIn-fill it with the port's
own stored colour, then composite that onto the real scene painter).
The same helper is reused, with a different colour, for the click-
selection highlight -- see recolor()/paint()'s own docstrings for the
full "which colour wins when" story.

Anchoring (both kinds of icon): an icon's *center* sits exactly on the
owning box's border line, parented to the box so it tracks the box's
own POSITION automatically (ordinary Qt child-coordinate behavior) --
see _edge_point(). Bug fix, correcting an earlier version of this very
paragraph: being parented does NOT also keep this icon glued to the
correct point on the box's edge across a SIZE change -- _edge_point()'s
own result is computed once, from whatever rect() the box had at
placement/construction time, and is otherwise never touched again; a
later resize (e.g. the "make room for a new element" expansion logic
growing this SAME box for some OTHER, unrelated element) left it
silently stale until resnap_to_owner_edge() was added specifically to
correct it, registered on the owner box's own on_resize_callbacks (see
that method's own docstring).

A non-fixed icon is QGraphicsItem.ItemIsMovable; itemChange() clamps
every drag onto the box's own rectangle boundary (_clamp_to_edge()),
which is what makes it "slide along the hierarchy edges" on any of the 4
sides, not just the one it was created on -- and also derives the
edge/t the drag landed on, written back onto the icon's own
SupplyPortPlacement so a later rebuild restores the same position (see
below). A fixed (PG-pin) icon has no ItemIsMovable flag at all, so it
never receives ItemPositionChange in the first place -- its persisted
SupplyPortPlacement still exists (see build_supply_port_overlay()'s
docstring) so its *colour* still persists once wired up, it just never
changes edge/t since nothing ever moves it.

Rotation (explicit request, both kinds of icon): whichever edge the icon
is currently anchored to determines its own visual rotation -- top: 0,
right: 90, bottom: 180, left: -90 (_ROTATION_FOR_EDGE) -- so the icon's
own arrow shape always points outward from the hierarchy box it's
anchored to. Applied live inside SupplyPortIconItem.paint() itself
(painter.save()/painter.rotate()/QSvgRenderer.render()/painter.restore(),
around the item's own local origin -- always the icon's own center,
see boundingRect()) rather than via QGraphicsItem.setRotation() on the
icon item itself -- the latter would also rotate every child item
(handles, label) by inheritance, which would leave the label sideways/
upside-down purely as a side effect; the request was for the icon's
own shape to rotate, not its label. (Previously baked into a cached
QPixmap via QTransform at construction/edge-change time -- removed
along with the rest of the pixmap-caching approach, see the module's
own opening paragraph.)

Each icon carries only 2 ConnectorHandleItems -- never 4 (left/right
dropped on explicit request) -- but *which* 2 depends on the icon's
current edge (_handle_layout()): top/bottom-anchored icons get
top/bottom handles (vertical entry/exit), left/right-anchored icons get
left/right handles (horizontal entry/exit) instead -- explicit request:
"when moving the power supply on the left and right corners, the
connectors should move to the left and right side". Re-laid-out (not
destroyed/recreated -- see SupplyPortIconItem._relayout_handles()) every
time the icon's edge changes, alongside the pixmap's own rotation.

A handle's clickable area (shape(), what itemAt() hit-tests against) is
deliberately much larger than its drawn dot (_HANDLE_HIT_PADDING) --
found, not assumed: the drawn 2*_HANDLE_RADIUS dot alone was reported as
essentially impossible to click precisely once a real hierarchy diagram
is zoomed to fit the view, since both the dot and the click precision
needed shrink together in scene units. Each handle also sets its own Qt
cursor (crosshair) so hovering over it -- "moving cursor on top a
connector point" -- visibly previews that a connector can be started
there; QGraphicsView auto-enables the hover/mouse-tracking this needs
the moment any item requests a cursor, no extra wiring required.

The name label (SupplyPortLabelItem) is also independently draggable
(explicit request: "move the label around it but not too far away") --
itemChange() clamps it to within `_LABEL_MAX_RADIUS` of the icon's own
local origin (its center), so it can be repositioned anywhere around the
icon but never floats away from it. Its dragged offset persists the same
way the icon's own edge/t does (SupplyPortPlacement.label_dx/dy).

Connector routing is orthogonal ("polygonal"/elbow -- like PowerPoint's
own elbow connectors -- not a straight diagonal line), and direction-
aware at each endpoint (_exit_direction(): vertical for a top/bottom-
anchored icon, horizontal for a left/right-anchored one, matching
whichever handle the connector is actually attached to) -- a same-
direction pair (top+top, left+right, etc.) gets a 3-segment elbow, a
mixed pair (e.g. one vertical, one horizontal) gets a simpler 2-segment
L-bend, computed fresh on every update_route() call so a connector stays
correctly shaped if either icon is later dragged onto a different edge.
Both the committed SupplyPortConnectorItem and the live drag-preview
line in power_intent_diagram_view.py share the same
orthogonal_connector_path() helper, so the preview shown while dragging
is exactly the shape that gets committed on release. Drawn noticeably
thicker (_CONNECTOR_LINE_WIDTH) than a typical thin diagram line -- both
the preview and the committed connector -- explicit request.

Persistence across a rebuild: a rebuild of the underlying hierarchy
scene (Hide/Unhide, a fresh load()) destroys every InstanceBoxItem and,
with it, any icon parented to one -- the icon objects themselves cannot
survive that (same "don't hold a Python reference to a destroyed C++
item" hazard already documented in hierarchy_diagram_view.py/
hierarchy_scene_builder.py). What *does* survive, held by
power_intent_diagram_view.py across rebuilds and fed back into
build_supply_port_overlay() every time, is plain, Qt-independent data:
- `SupplyPortPlacement` per user-placed port (owner path, name, colour,
  last-dragged edge/t, last-dragged label offset) -- restored verbatim.
- `pg_pin_placements` -- one SupplyPortPlacement per PG pin too, same
  shape as a user-placed one; the only difference is *how* a missing
  entry gets created (from the macro's own Definition.ports, evenly
  spread along the default top edge, instead of from the "Add Power
  Supply" dialog) -- once created, it persists (and can be dragged) the
  exact same way. build_supply_port_overlay() mutates this dict in place
  when it discovers a PG pin with no existing entry -- a deliberate,
  documented exception to this module's otherwise-pure-function style.
- `connector_placements` (pairs of icon keys) -- restored by reconnecting
  (see power_intent_diagram_view.py's _rebuild()).
A placement whose owner box is currently hidden simply isn't
materialized this pass (there's no border left to anchor it to) -- it
stays in the persisted dict and reappears once the box is unhidden
again, rather than being discarded.

`SupplyPortDiagramState` bundles exactly those 3 persisted pieces into
one object, shared (not copied) across every tab of a Power Intent
Diagram (see hierarchy_diagram_tabs.py/power_intent_diagram_view.py) --
explicit request: a supply port added while looking at a sub-module's
own diagram tab must show up in the whole-hierarchy tab too, and vice
versa. Since every tab is just a different *view* onto the same
underlying placements (each materializing only whichever placements
fall within its own currently-rendered boxes), sharing this one object
is the entire mechanism -- no separate cross-tab sync/broadcast needed.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from functools import lru_cache
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QByteArray, QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QGraphicsEllipseItem, QGraphicsItem, QGraphicsPathItem, QGraphicsSimpleTextItem

from frontend.src.shared.diagram.hierarchy_scene_builder import InstanceBoxItem
from frontend.src.shared.diagram.svg_tint import render_tinted_svg
from frontend.src.shared.models.rtl import PortDirection

_ICON_PATH = Path(__file__).resolve().parents[2] / "icons" / "supply-port.svg"
_INTERNAL_SUPPLY_ICON_PATH = Path(__file__).resolve().parents[2] / "icons" / "internal-supply.svg"
# Bug fix (explicit report): the combined virtual-supply.svg (diamond +
# white "V" text) was being tinted as ONE single silhouette -- since
# render_tinted_svg() masks purely by rendered ALPHA (see its own
# docstring: "expected to be a solid black silhouette... only true
# black fill is masked"), and the "V" glyph is drawn fully OPAQUE
# (fill="#ffffff", not a transparent cutout) entirely inside the
# already-fully-opaque diamond, the glyph contributed no distinct alpha
# region at all -- the whole icon (diamond AND glyph) got recoloured to
# the SAME live colour, silently erasing the "V" on screen. Split into
# two separate SVG assets/layers so only the diamond ever gets tinted:
# virtual-supply-fill.svg (the diamond alone, tinted via
# render_tinted_svg() exactly like every other icon type here) and
# virtual-supply-glyph.svg (the "V" text alone, transparent background,
# rendered PLAIN/untinted directly on top -- always its own literal
# fill="#ffffff", never touched by the live colour). The original
# combined virtual-supply.svg is kept as-is for diagram_tool_icons.py's
# own toolbar-button/cursor use, which renders one static, never-tinted
# icon and has no colour-binding concept at all -- only THIS live,
# per-instance-coloured diagram icon needed the two-layer split.
_VIRTUAL_SUPPLY_FILL_ICON_PATH = Path(__file__).resolve().parents[2] / "icons" / "virtual-supply-fill.svg"
_VIRTUAL_SUPPLY_GLYPH_ICON_PATH = Path(__file__).resolve().parents[2] / "icons" / "virtual-supply-glyph.svg"
_ICON_SIZE = 28
# Explicit request, "make room" layout: how much INTERIOR top room a
# top-edge Supply Port/Internal Supply needs reserved on its own owner
# box -- same "icon size + a little clearance" shape as
# power_switch_overlay.py's own TOP_MARGIN_FOR_SWITCH (a EXTERIOR
# margin; this one is INTERIOR -- see hierarchy_scene_builder.py's own
# extra_interior_top docstring for the distinction), public (not
# underscore-prefixed) for the same reason that one is: consumed by
# power_intent_diagram_view.py's own _extra_interior_top() override.
INTERIOR_TOP_MARGIN_FOR_SUPPLY_PORT = _ICON_SIZE + 6.0
_HANDLE_RADIUS = 4.0  # drawn dot size -- see _HANDLE_HIT_PADDING for the much larger clickable area
_HANDLE_HIT_PADDING = 8.0
_LABEL_MAX_RADIUS = 60.0  # how far the name label can be dragged from the icon's own center
_PG_PIN_DEFAULT_COLOR = QColor("#9e9e9e")  # neutral grey -- "not connected to anything yet"
# Explicit request: one shared fluorescent-yellow constant for *every*
# hover/drop-target highlight this module and power_intent_diagram_view.py
# use -- ConnectorHandleItem.set_hover_highlight() (Supply Net) and the
# Supply Port drag-and-drop's own block-boundary overlay both reuse this
# exact colour, one place to tune later.
HIGHLIGHT_COLOR = QColor(255, 255, 0)
_CONNECTOR_LINE_WIDTH = 4
_OBSTACLE_MARGIN = 12.0  # clearance kept between a rerouted segment and the macro box it's avoiding
_SEGMENT_HIT_PADDING = 6.0  # generous hit area around a thin segment line, same precedent as _HANDLE_HIT_PADDING

_ROTATION_FOR_EDGE = {"top": 0.0, "right": 90.0, "bottom": 180.0, "left": -90.0}

Z_SUPPLY_PORT = 6
Z_SUPPLY_PORT_CONNECTOR = 5  # below the icons themselves, above badges/instance boxes


# UPF's own supply-state vocabulary, which this app now uses directly
# rather than translating a private "voltage"/"off" pair into it.
FULL_ON = "FULL_ON"
PARTIAL_ON = "PARTIAL_ON"
OFF = "OFF"
UNDETERMINED = "UNDETERMINED"

SUPPLY_STATE_TYPES = (FULL_ON, PARTIAL_ON, OFF, UNDETERMINED)
# The two that carry a level. The others have none, and their Voltage
# cell is disabled rather than merely left blank.
LEVELLED_STATE_TYPES = (FULL_ON, PARTIAL_ON)


def duplicate_state_names(states) -> list[str]:
    """Names appearing more than once in ONE supply's own state list.

    Uniqueness is per supply, deliberately: two different supplies may
    each have an "ON", and forcing global uniqueness would make a
    designer invent VDD_ON/VSS_ON for no reason. Within one supply the
    name is what a legacy `add_pst_state` column references, so a
    duplicate there is genuinely ambiguous."""
    seen: set[str] = set()
    duplicates: list[str] = []
    for state in states:
        if not state.name:
            continue
        if state.name in seen and state.name not in duplicates:
            duplicates.append(state.name)
        seen.add(state.name)
    return duplicates


def state_key(state: "SupplyPortState") -> str:
    """A stable, unambiguous identifier for one rail state.

    Deliberately NOT the display text: FULL_ON 1.2 and PARTIAL_ON 1.2
    both DISPLAY as "1.2" (section 4) and are legitimately different
    states, so display text cannot be used to reference one. This is also
    exactly the token UPF wants inside a `-supply_expr`, so the Power
    State Table's stored value and the generated output agree by
    construction."""
    if state.state_type in LEVELLED_STATE_TYPES:
        return f"{state.state_type} {state.voltage}"
    return state.state_type


def state_label(state: "SupplyPortState") -> str:
    """A rail state written out for a human.

    The authored NAME leads, with the type and level after it --
    "ON (FULL_ON 1.2V)" -- so the Properties table and hover tooltip show
    what the user called it AND what it physically is. Falls back to the
    type-and-level form for a state with no name, which is what an
    imported state has until someone edits it."""
    # The unnamed form is EXACTLY what it was -- "FULL_ON (1.0V)" -- so
    # an imported design's tooltips and Properties rows are unchanged.
    physical = (
        f"{state.state_type} ({state.voltage}V)"
        if state.state_type in LEVELLED_STATE_TYPES and state.voltage
        else state.state_type
    )
    return f"{state.name} [{physical}]" if state.name else physical


def state_display(state: "SupplyPortState") -> str:
    """What a Power State Table cell/dropdown SHOWS.

    The authored NAME (section 5) -- "ON", "RETENTION" -- rather than the
    raw "FULL_ON 1.2" it used to show. The underlying dropdown DATA is
    still state_key(), so generation keeps deriving `-supply_expr` from
    the real Type/Voltage; only the visible label changed.

    Falls back to the physical form when a state has no name, so an
    imported design still reads sensibly before anyone names anything."""
    if state.name:
        return state.name
    if state.state_type in LEVELLED_STATE_TYPES:
        return state.voltage or ""
    return state.state_type


@dataclass
class SupplyPortState:
    """One entry in a Supply Port's or Virtual Supply's own power-state
    list.

    NAME IS BACK, alongside the type/voltage model. It was removed when
    the type vocabulary was introduced, on the reasoning that naming
    belonged only at the Supply Set and Power State Table levels; that is
    now reversed. `name` is authored per state, REQUIRED, no spaces, and
    unique only WITHIN one supply's own list -- two different supplies
    may each have a state called "ON" with no conflict.

    What the name is FOR, precisely, because the boundary matters:
      - Legacy `add_port_state`/`add_pst_state` declare and reference it.
      - The Power State Table displays it in its per-net cells.
      - Modern Tier 1 does NOT read it. Its combined supply-set states
        stay STATE_1/STATE_2/..., because a combined state spans several
        rails and borrowing the power rail's name would collide whenever
        two combinations shared a power value but differed elsewhere.

    Because the name disambiguates, SEVERAL STATES MAY SHARE A TYPE --
    two FULL_ON states at different voltages is an ordinary DVFS case and
    is explicitly legal.

    That alignment is not a coincidence worth losing: UPF writes a rail
    state as `{FULL_ON 1.2}`, where the leading token IS this vocabulary.
    So a state here maps to UPF with no translation, in both directions.

    `voltage` is a required numeric string for FULL_ON/PARTIAL_ON and is
    always None for OFF/UNDETERMINED, neither of which has a level.

    .pis files written under the old schema do NOT load into this --
    accepted explicitly as a breaking change, with no migration."""

    state_type: str = FULL_ON
    voltage: Optional[str] = None
    # LAST, deliberately, even though the dialog shows it FIRST. The
    # field order is an API, and `SupplyPortState("FULL_ON", "1.2")` is
    # written positionally in ~100 places; putting the name first made
    # every one of those silently misassign -- the type became the name
    # and the voltage became the type, with no error anywhere. Column
    # order in the widget is set by COL_NAME/COL_TYPE/COL_VOLTAGE and is
    # independent of this.
    name: str = ""


@dataclass
class SupplyPortPlacement:
    """Plain, Qt-item-independent record of one supply-port icon -- the
    thing that survives a scene rebuild when the icon itself can't (see
    module docstring). `id` doubles as the icon's dict key everywhere
    (power_intent_diagram_view.py's _supply_port_icons/
    _supply_connector_placements) -- a uuid4 hex for a user-placed port,
    or the deterministic f"pg:{owner_path}:{pin_name}" for a PG pin.
    `label_dx`/`label_dy` are None until the label is first materialized
    (its default offset depends on its own rendered width, only known
    once the text exists -- see SupplyPortIconItem.__init__) or until
    it's been dragged, whichever comes first."""

    id: str
    owner_path: str
    name: str
    color: QColor
    edge: str = "top"
    t: float = 0.5
    fixed: bool = False
    label_dx: Optional[float] = None
    label_dy: Optional[float] = None
    # A macro PG pin's own nominal voltage (see rtl.Port.voltage's own
    # docstring on why this is display-only, sourced straight from the
    # macro's Definition.ports -- see build_supply_port_overlay()) --
    # always None for a user-placed port (no dialog field for it; the
    # request was specifically about macro PG pins). Shown on its own
    # label line, in parens, when set -- see SupplyPortIconItem's own
    # _label_text().
    voltage: Optional[str] = None
    # True for a macro PG pin whose own Liberty pg_type was
    # internal_power/internal_ground (PortDirection.PG_INTERNAL) rather
    # than primary/backup (PortDirection.PG) -- always False for a
    # user-placed port. Distinguishes "Macro PG" from "Macro PG -
    # Internal" in the Properties panel's Supply Ports tab (explicit
    # request); the diagram icon/connector logic itself treats both
    # identically (both are just `fixed=True`).
    is_internal: bool = False
    # True for a user-placed "Internal Supply" (explicit request: a
    # second, distinct entity type from a plain user-placed "Supply
    # Port", placeable only on an inner/non-top-level hierarchy block,
    # excluding macros -- the mirror image of Supply Port's own
    # top-level-only rule, see power_intent_diagram_view.py's own
    # _is_top_level_box()). Deliberately a SEPARATE flag from
    # `is_internal` above, not a repurposing of it -- that one is about a
    # macro's own PG_INTERNAL pin sub-classification (always `fixed=True`,
    # never user-placed); this one is about a user-placed, freely-
    # draggable icon (`fixed=False`) the same as a plain Supply Port,
    # just restricted to a different hierarchy level. Always False for a
    # plain Supply Port and for any macro PG pin. Both kinds otherwise
    # share this exact same dataclass/dict/icon class/connector/rename-
    # propagation machinery deliberately -- see build_supply_port_overlay()
    # and SupplyPortIconItem.paint()'s own docstrings for the only two
    # places that actually branch on it.
    is_internal_supply: bool = False
    # True for a user-placed "Virtual Supply" (explicit request): a
    # THIRD, distinct supply type, structurally unlike either Supply
    # Port or Internal Supply. Placeable at ANY hierarchy level (no
    # level restriction the way Supply Port/Internal Supply have), and
    # exists ONLY to drive a macro's own INTERNAL PG pins -- see
    # try_connect_supply_ports()'s own one-directional connection-
    # target restriction. Never belongs to any Power Domain, never
    # selectable as a Supply Set rail (see known_supply_names_*()'s own
    # exclusion), and carries its own AUTHORED Power States (like a
    # plain Supply Port, unlike Internal Supply's derived ones) --
    # REQUIRED at creation (see add_virtual_supply()), never empty.
    # Deliberately a THIRD, separate flag from `is_internal_supply`
    # (never True at the same time) -- both kinds otherwise share this
    # exact same dataclass/dict/icon class/connector machinery, same
    # "one flag, several consumers" precedent `is_internal_supply`
    # itself already establishes.
    is_virtual_supply: bool = False
    # Audit fix (category 04): True ONLY when this placement currently
    # sits at `edge == "bottom"` because the Ground-role classifier put
    # it there, never because the user dragged it. Without this flag the
    # classifier had no way to tell its OWN past decisions apart from a
    # deliberate user placement, so it could only ever move a port one
    # way (top -> bottom) and a supply that lost its Ground role stayed
    # anchored at the bottom forever -- unrecoverable even by a full
    # rebuild. Cleared the moment the user drags this icon to any edge
    # themselves (see SupplyPortIconItem.itemChange()), so a manual
    # placement is never silently overridden afterward.
    ground_auto_anchored: bool = False
    # A plain Supply Port's own user-authored power states (DVFS-style --
    # see SupplyPortState's own docstring). Always empty for a macro PG
    # pin (fixed=True) -- pins have no independent state concept. Always
    # empty/unused for an Internal Supply too (is_internal_supply=True)
    # -- an Internal Supply's own *effective* states are never stored
    # here; they're derived live by walking the connectivity chain back
    # to a real source Supply Port (see power_switch_overlay.py's own
    # resolve_effective_states()), so this field simply never gets
    # populated for one.
    states: list[SupplyPortState] = field(default_factory=list)
    # UPF `create_supply_port -direction {in|out|inout}`. SUPPLY PORT
    # ONLY -- never set for an Internal Supply, a Virtual Supply, or a
    # macro PG pin, none of which is a boundary port that could have a
    # direction (the dialog does not even show the field for them; see
    # add_power_supply_dialog.AddPowerSupplyDialog).
    #
    # "" means "no direction", and that is a REAL, distinct value rather
    # than merely unset: a port newly created in this app defaults to
    # "in", while one IMPORTED from a file that carried no `-direction`
    # flag is genuinely "" -- and the generator must then omit the flag
    # rather than inventing one. Hence a plain str, not Optional[str]:
    # there is no third "unknown" state to represent.
    direction: str = ""


@dataclass
class SupplyPortDiagramState:
    """Shared, Qt-independent supply-port state for an entire Power
    Intent Diagram (every tab -- see module docstring's own paragraph on
    this). One instance is constructed once by PowerIntentDiagramView
    and handed to every _PowerIntentGraphicsView it creates (root tab and
    every later-opened sub-hierarchy tab alike)."""

    supply_port_placements: dict[str, SupplyPortPlacement] = field(default_factory=dict)
    pg_pin_placements: dict[str, SupplyPortPlacement] = field(default_factory=dict)
    supply_connector_placements: list[tuple[str, str]] = field(default_factory=list)
    # A manually-dragged middle segment (explicit request: per-segment
    # editing), keyed by the same (source_key, target_key) pair as its
    # entry above -- (segment_index, delta_from_the_auto_route's_own_value),
    # not absolute coordinates. See SupplyPortConnectorItem's own class
    # docstring for why a relative delta specifically: an *unrelated*
    # Hide/Unhide elsewhere in the tree can repack the whole diagram and
    # shift every box/icon's absolute scene position, even though
    # nothing about this connector's own endpoints changed -- a relative
    # offset, reapplied on top of a freshly recomputed auto-route every
    # time, survives that (and gracefully follows a deliberate endpoint
    # move too) instead of being silently discarded by either.
    manual_connector_offsets: dict[tuple[str, str], tuple[int, float]] = field(default_factory=dict)


def copy_supply_port_diagram_state(state: SupplyPortDiagramState) -> SupplyPortDiagramState:
    """An independent snapshot of `state` -- explicit request: Ctrl+Z/
    Ctrl+Y undo/redo for Power Intent Diagram edits (see
    power_intent_diagram_view.py's own _SupplyPortStateCommand). Each
    SupplyPortPlacement is individually copied (dataclasses.replace(), a
    genuinely separate object), not just the dict shallow-copied: a live
    SupplyPortIconItem holds a direct reference to its own `.placement`
    and mutates its fields in place (drag, rename, recolor) -- a shallow
    dict copy would still share the *same* mutable SupplyPortPlacement
    objects between an already-taken "before" snapshot and the live
    state, silently corrupting the snapshot the moment a later edit
    touches the live object. `color` (a QColor) is not further copied --
    safe, since every real recolor path in this module always
    reassigns a whole new QColor rather than mutating one in place.
    `states` (a *list* of SupplyPortState) is separately deep-copied for
    the same reason -- dataclasses.replace() only shallow-copies a list
    field, so without this the snapshot's own list would still be the
    exact same list object a later states edit mutates in place."""
    return SupplyPortDiagramState(
        supply_port_placements={k: replace(v, states=[replace(s) for s in v.states]) for k, v in state.supply_port_placements.items()},
        pg_pin_placements={k: replace(v) for k, v in state.pg_pin_placements.items()},
        supply_connector_placements=list(state.supply_connector_placements),
        manual_connector_offsets=dict(state.manual_connector_offsets),
    )


def restore_supply_port_diagram_state(state: SupplyPortDiagramState, snapshot: SupplyPortDiagramState) -> None:
    """Copies `snapshot`'s fields onto the *live* `state` object in
    place -- never replaces `state` itself, since it's shared by
    reference across every tab of the same Power Intent Diagram (see
    the class's own docstring); reassigning `state` itself here would
    leave every other tab's own `self._shared_state` reference pointing
    at the old, now-stale object."""
    restored = copy_supply_port_diagram_state(snapshot)
    state.supply_port_placements = restored.supply_port_placements
    state.pg_pin_placements = restored.pg_pin_placements
    state.supply_connector_placements = restored.supply_connector_placements
    state.manual_connector_offsets = restored.manual_connector_offsets


# Explicit request ("icons... displaying as pixelated/low quality,
# still pixelated after supersampling"): a cached QPixmap -- no matter
# how high its own source resolution -- is still a fixed-resolution
# raster once the diagram is zoomed in far enough, confirmed via a live
# before/after screenshot comparison at high zoom. The actual fix is to
# stop caching a pixmap at all and render the SVG live, at the item's
# own current on-screen size, every single paint() call -- resolution-
# independent at any zoom level, the same way any other vector-drawn
# item in this diagram (box borders, connector lines) already is.
#
# ONE shared QSvgRenderer for the whole icon asset (explicit request:
# "one per icon type, not one per instance") -- lru_cache(maxsize=1)
# keyed on this function's own (no-argument) call, so the SVG is parsed
# exactly once for the process's whole lifetime no matter how many
# SupplyPortIconItems exist, avoiding needless re-parsing overhead once
# a real diagram has many supply ports. Lazily constructed on first
# call (not at import time) since QSvgRenderer is a Qt object that
# needs a QApplication/QGuiApplication to already exist.
@lru_cache(maxsize=1)
def _supply_port_svg_renderer() -> QSvgRenderer:
    return QSvgRenderer(QByteArray(_ICON_PATH.read_bytes()))


# Same one-shared-renderer-per-icon-type precedent as
# _supply_port_svg_renderer() above, for "Internal Supply"'s own
# distinct icon (explicit request: "ideally the same live-paint()
# QSvgRenderer approach... rather than introducing a third rendering
# pattern" -- this reuses the exact same paint()/render_tinted_svg()
# machinery, just picking this renderer instead when the placement is
# flagged `is_internal_supply`).
@lru_cache(maxsize=1)
def _internal_supply_svg_renderer() -> QSvgRenderer:
    return QSvgRenderer(QByteArray(_INTERNAL_SUPPLY_ICON_PATH.read_bytes()))


# Same one-shared-renderer-per-icon-type precedent, for "Virtual
# Supply"'s own TWO-LAYER icon (see _VIRTUAL_SUPPLY_FILL_ICON_PATH's own
# comment above for why it's split): the fill layer is tinted via
# render_tinted_svg() exactly like every other icon type here; the
# glyph layer is rendered plain/untinted, always its own literal white.
@lru_cache(maxsize=1)
def _virtual_supply_fill_svg_renderer() -> QSvgRenderer:
    return QSvgRenderer(QByteArray(_VIRTUAL_SUPPLY_FILL_ICON_PATH.read_bytes()))


@lru_cache(maxsize=1)
def _virtual_supply_glyph_svg_renderer() -> QSvgRenderer:
    return QSvgRenderer(QByteArray(_VIRTUAL_SUPPLY_GLYPH_ICON_PATH.read_bytes()))


def _edge_point(rect: QRectF, edge: str, t: float) -> QPointF:
    if edge == "top":
        return QPointF(rect.x() + t * rect.width(), rect.y())
    if edge == "bottom":
        return QPointF(rect.x() + t * rect.width(), rect.y() + rect.height())
    if edge == "left":
        return QPointF(rect.x(), rect.y() + t * rect.height())
    return QPointF(rect.x() + rect.width(), rect.y() + t * rect.height())  # "right"


def _default_label_offset(edge: str, label_rect: QRectF, half: float) -> tuple[float, float]:
    """Bug 2 (explicit request): a Ground-type icon (edge=="bottom",
    the only edge this diagram ever auto-assigns rather than the user
    dragging to) sits ON the block's bottom boundary line -- the same
    "below the icon" offset every other edge already defaults to would
    place the label OUTSIDE the block, under the bottom edge, not
    "inside the block boundary" as required. Only "bottom" gets its own
    branch: every other edge (top, and left/right, never auto-assigned
    by this module) keeps the original always-below default unchanged.
    Horizontally centered either way (dx alone), matching every other
    edge."""
    dx = -label_rect.width() / 2
    if edge == "bottom":
        return dx, -half - 2 - label_rect.height()
    return dx, half + 2


def is_ground_supply(owner_path: str, name: str, ground_supply_keys: "frozenset[tuple[str, str]] | set[tuple[str, str]]") -> bool:
    """Bug 2 (explicit request, "Derive from Supply Set usage"): a
    plain Supply Port/Internal Supply counts as Ground when some Supply
    Set names it via `.ground`.

    Audit fix (category 04): the key is now the `(owner_path, name)`
    PAIR, not the bare name. Matching on name alone was correct only
    while Supply Port/Internal Supply names were globally unique -- a
    rule deliberately removed later ("two placements may now freely
    share a name with no forced relationship"), after which a Supply Set
    on one hierarchy naming `VSS` as its ground silently re-anchored
    EVERY placement called `VSS` design-wide, including ports on
    unrelated hierarchies belonging to no Supply Set at all. The caller
    (power_intent_diagram_view.py's `_ground_supply_keys()`) decides
    which pairs qualify, applying the same cascade-to-descendants rule
    Power Domains already use, so this module still never needs to
    import power_domain_overlay.py's own SupplySetPlacement -- that
    import would be circular (power_domain_overlay.py already imports
    SupplyPortIconItem from here)."""
    return (owner_path, name) in ground_supply_keys


def resolve_ground_edge_change(placement: SupplyPortPlacement, ground_supply_keys: "frozenset[tuple[str, str]] | set[tuple[str, str]]") -> Optional[str]:
    """The edge `placement` SHOULD move to for Ground-role reasons, or
    None when it's already correct (the common case).

    Audit fix (category 04): this replaces what used to be a one-way
    `top -> bottom` snap duplicated in two places (the live reclassifier
    and the rebuild-time pass). Both now share this single rule, so they
    can't drift, and it resolves in BOTH directions:

    - gains a Ground role while still at the untouched "top" default
      -> "bottom";
    - LOSES its Ground role while sitting at "bottom" *because this
      classifier put it there* (`ground_auto_anchored`) -> back to
      "top".

    A placement the user dragged anywhere themselves is never moved by
    either branch -- dragging clears `ground_auto_anchored`, so the
    revert branch can't fire, and a dragged-to-bottom port is not at
    "top" so the snap branch can't either. A fixed (macro PG pin)
    placement is never touched at all: its edge comes from Liberty data
    at construction and never changes."""
    if placement.fixed:
        return None
    is_ground = is_ground_supply(placement.owner_path, placement.name, ground_supply_keys)
    if is_ground and placement.edge == "top":
        return "bottom"
    if not is_ground and placement.ground_auto_anchored and placement.edge == "bottom":
        return "top"
    return None


def apply_ground_edge_classification(
    placement: SupplyPortPlacement,
    ground_supply_keys: "frozenset[tuple[str, str]] | set[tuple[str, str]]",
    icon: "Optional[SupplyPortIconItem]" = None,
) -> bool:
    """Applies `resolve_ground_edge_change()` and keeps the
    `ground_auto_anchored` bookkeeping in one place, so no caller has to
    remember to set/clear it. Returns whether anything actually moved.

    `icon` routes through `SupplyPortIconItem.set_edge()` (relayouts
    handles, resets the label to the new edge's own default, reroutes
    connectors, fires on_move_callbacks) when the placement is currently
    materialized; otherwise the placement is mutated directly and its
    label offsets cleared so a later materialization recomputes them
    fresh -- the same two-path treatment the previous one-way version
    already needed for a placement whose owner box is hidden."""
    new_edge = resolve_ground_edge_change(placement, ground_supply_keys)
    if new_edge is None:
        return False
    if icon is not None:
        icon.set_edge(new_edge)
    else:
        placement.edge = new_edge
        placement.label_dx = None
        placement.label_dy = None
    placement.ground_auto_anchored = new_edge == "bottom"
    return True


def _label_text(placement: SupplyPortPlacement) -> str:
    """The icon's rendered label -- just its name, unless it's a macro
    PG pin with a known voltage (explicit request), in which case the
    voltage goes on its own second line, in parens (e.g. "VDD\\n(0.80V)")
    -- QGraphicsSimpleTextItem renders an embedded newline as a real
    second line natively, no wrapping/rich-text machinery needed."""
    if placement.voltage:
        return f"{placement.name}\n({placement.voltage})"
    return placement.name


def _handle_layout(edge: str, half: float) -> dict[str, QPointF]:
    """Which 2 handle positions (in the icon's own local coordinates) an
    icon anchored to `edge` gets -- see module docstring. Left/right
    edges get horizontal (left/right) handles instead of the usual
    vertical (top/bottom) ones, matching the direction a connector
    naturally needs to exit/enter in from that side."""
    if edge in ("left", "right"):
        return {"left": QPointF(-half, 0), "right": QPointF(half, 0)}
    return {"top": QPointF(0, -half), "bottom": QPointF(0, half)}


# Explicit request, generalized beyond the original macro-only case: any
# icon anchored to a LEAF hierarchy block (InstanceBoxItem.is_leaf -- no
# visible child hierarchy blocks; a macro is always one, since a
# black-box definition can never have children) has its own "interior"
# handle -- the one on the side facing INTO the block's own body, since
# the icon sits exactly on the box's boundary line -- dropped entirely:
# there is nothing "below" a leaf for that anchor to ever meaningfully
# connect to (see SupplyPortIconItem._interior_key()'s own docstring for
# the full reasoning and the pre-generalization history). Always the
# opposite key from whichever edge the icon is anchored to.
_INTERIOR_HANDLE_FOR_EDGE = {"top": "bottom", "bottom": "top", "left": "right", "right": "left"}


def _exit_direction(icon) -> str:
    """The direction a connector attached to `icon` should exit/enter in
    -- follows directly from _handle_layout(): horizontal for a
    left/right-anchored SupplyPortIconItem, vertical otherwise.

    Generalized (explicit request, Power Switch) via getattr() rather
    than a direct `icon.placement.edge` attribute access: a
    PowerSwitchIconItem (power_switch_overlay.py) exposes `.placement`
    too (its own PowerSwitchPlacement, needed for other reasons) but
    that dataclass has no `.edge` field at all -- a switch only ever has
    top/bottom handles, never left/right, so "no edge concept" should
    just mean "vertical," the same default a bare `getattr(..., None)`
    naturally produces. This is what lets PowerSwitchIconItem plug into
    SupplyPortConnectorItem/try_connect_supply_ports() unmodified,
    instead of needing its own parallel connector implementation."""
    edge = getattr(getattr(icon, "placement", None), "edge", None)
    return "horizontal" if edge in ("left", "right") else "vertical"


def _clamp_to_edge(rect: QRectF, point: QPointF) -> tuple[QPointF, str, float]:
    """Clamps `point` onto whichever of the rect's 4 edges is nearest --
    the drag-along-the-edges mechanic: wherever the icon is dragged
    (inside, outside, or already on the border), its center snaps onto
    the closest point of the box's own boundary. Also returns which edge
    it landed on and the (0..1) fraction along that edge -- the inverse
    of _edge_point() -- so the caller can persist the result."""
    cx = min(max(point.x(), rect.left()), rect.right())
    cy = min(max(point.y(), rect.top()), rect.bottom())
    d_left = cx - rect.left()
    d_right = rect.right() - cx
    d_top = cy - rect.top()
    d_bottom = rect.bottom() - cy
    nearest = min(d_left, d_right, d_top, d_bottom)
    width = rect.width() or 1.0
    height = rect.height() or 1.0
    if nearest == d_left:
        return QPointF(rect.left(), cy), "left", (cy - rect.top()) / height
    if nearest == d_right:
        return QPointF(rect.right(), cy), "right", (cy - rect.top()) / height
    if nearest == d_top:
        return QPointF(cx, rect.top()), "top", (cx - rect.left()) / width
    return QPointF(cx, rect.bottom()), "bottom", (cx - rect.left()) / width


def _clamp_to_radius(point: QPointF, max_radius: float) -> QPointF:
    """Clamps `point` (already in the icon's own local coordinates, i.e.
    relative to its center) to within `max_radius` -- the label-drag
    mechanic: it can move anywhere around the icon, but never past a
    fixed distance from it."""
    length = (point.x() ** 2 + point.y() ** 2) ** 0.5
    if length <= max_radius or length == 0:
        return point
    scale = max_radius / length
    return QPointF(point.x() * scale, point.y() * scale)


def is_nested_pair(path_a: str, path_b: str) -> bool:
    """True if the two instance paths are the SAME box, or one is a
    strict descendant of the other -- the "inner supply port" connector-
    target rule, checked in either direction (see module docstring). A
    sibling pair (two distinct, unrelated boxes at the same level) is
    the one case this rejects.

    The same-path case (found via Power Switch -- a switch's own TOP
    anchor commonly needs to connect to an upstream Supply Port placed
    on the exact SAME box as the switch's own target, e.g. both on the
    design's top-level block) is a degenerate/reflexive case of
    "nested," not a loosening of the sibling-rejection rule: two
    DIFFERENT icons already anchored to the identical box are exactly as
    legitimate a connection target as an ancestor-descendant pair --
    `source is target` (the literal same icon) is rejected separately,
    by try_connect_supply_ports() itself, before this function is ever
    consulted."""
    return path_a == path_b or path_a.startswith(f"{path_b}/") or path_b.startswith(f"{path_a}/")


def _base_waypoints(p1: QPointF, p2: QPointF, dir1: str, dir2: str) -> list[QPointF]:
    """The un-obstructed orthogonal ("polygonal"/elbow) route between two
    points, shaped by each endpoint's own exit direction (see
    _exit_direction()) -- matching, and only ever needing to change if, a
    handle is on a left/right- vs top/bottom-anchored icon:
    - both vertical: a 3-segment elbow (exit down/up from p1, across, up/
      down into p2), crossing at the vertical midpoint.
    - both horizontal: the same shape rotated 90 degrees -- exit left/
      right from p1, across, into p2 -- crossing at the horizontal
      midpoint.
    - one of each: a single 2-segment L-bend is enough (no middle
      segment needed) -- go from p1 in p1's own exit direction until
      reaching p2's coordinate on that axis, then one perpendicular
      segment straight into p2."""
    if dir1 == "vertical" and dir2 == "vertical":
        mid_y = (p1.y() + p2.y()) / 2
        return [p1, QPointF(p1.x(), mid_y), QPointF(p2.x(), mid_y), p2]
    if dir1 == "horizontal" and dir2 == "horizontal":
        mid_x = (p1.x() + p2.x()) / 2
        return [p1, QPointF(mid_x, p1.y()), QPointF(mid_x, p2.y()), p2]
    if dir1 == "vertical":  # dir2 == "horizontal"
        return [p1, QPointF(p1.x(), p2.y()), p2]
    return [p1, QPointF(p2.x(), p1.y()), p2]  # dir1 == "horizontal", dir2 == "vertical"


def _segment_intersects_rect(a: QPointF, b: QPointF, rect: QRectF) -> bool:
    if a.y() == b.y():  # horizontal segment
        y = a.y()
        x_lo, x_hi = sorted((a.x(), b.x()))
        return rect.top() <= y <= rect.bottom() and x_hi >= rect.left() and x_lo <= rect.right()
    x = a.x()  # vertical segment
    y_lo, y_hi = sorted((a.y(), b.y()))
    return rect.left() <= x <= rect.right() and y_hi >= rect.top() and y_lo <= rect.bottom()


def _avoid_obstacles(waypoints: list[QPointF], obstacles: list[QRectF]) -> list[QPointF]:
    """Nudges every *middle* segment (index 1..len-3 -- i.e. never the
    first or last, which must stay perpendicular to their own anchor,
    see module docstring's perpendicular-anchor guarantee) away from any
    macro box it crosses, detouring around whichever side (above/below
    for a horizontal segment, left/right for a vertical one) needs the
    smaller shift. A 2-segment path (an L-bend between mixed directions)
    has no middle segment to nudge at all -- one is synthesized first,
    at the L-bend's own corner position, purely so there's something to
    route an obstacle around with (still 100% orthogonal, just one more
    waypoint). Two full passes over every (segment, obstacle) pair --
    bounded, not a general multi-obstacle solver -- lets a second
    obstacle's adjustment account for a first one's, without unbounded
    iteration."""
    if len(waypoints) == 3:
        p1, corner, p2 = waypoints
        if corner.x() == p1.x():  # first leg vertical, second horizontal
            mid_y = corner.y()
            waypoints = [p1, QPointF(p1.x(), mid_y), QPointF(p2.x(), mid_y), p2]
        else:
            mid_x = corner.x()
            waypoints = [p1, QPointF(mid_x, p1.y()), QPointF(mid_x, p2.y()), p2]

    waypoints = list(waypoints)
    for _pass in range(2):
        for obstacle in obstacles:
            for i in range(1, len(waypoints) - 2):
                a, b = waypoints[i], waypoints[i + 1]
                if not _segment_intersects_rect(a, b, obstacle):
                    continue
                if a.y() == b.y():  # horizontal -- detour above or below
                    above = obstacle.top() - _OBSTACLE_MARGIN
                    below = obstacle.bottom() + _OBSTACLE_MARGIN
                    new_y = above if abs(above - a.y()) <= abs(below - a.y()) else below
                    waypoints[i] = QPointF(a.x(), new_y)
                    waypoints[i + 1] = QPointF(b.x(), new_y)
                else:  # vertical -- detour left or right
                    left = obstacle.left() - _OBSTACLE_MARGIN
                    right = obstacle.right() + _OBSTACLE_MARGIN
                    new_x = left if abs(left - a.x()) <= abs(right - a.x()) else right
                    waypoints[i] = QPointF(new_x, a.y())
                    waypoints[i + 1] = QPointF(new_x, b.y())
    return waypoints


def compute_connector_waypoints(
    p1: QPointF, p2: QPointF, dir1: str = "vertical", dir2: str = "vertical", obstacles: list[QRectF] = ()
) -> list[QPointF]:
    """The full waypoint list for a connector -- _base_waypoints() plus
    obstacle avoidance (explicit request: "line does not cross macro").
    `obstacles` is normally macro boxes' own scene rects (see
    SupplyPortConnectorItem._collect_obstacles()); empty by default so
    callers that don't care (most tests) get the plain routed shape."""
    waypoints = _base_waypoints(p1, p2, dir1, dir2)
    if obstacles:
        waypoints = _avoid_obstacles(waypoints, obstacles)
    return waypoints


def path_from_waypoints(waypoints: list[QPointF]) -> QPainterPath:
    path = QPainterPath(waypoints[0])
    for point in waypoints[1:]:
        path.lineTo(point)
    return path


def orthogonal_connector_path(p1: QPointF, p2: QPointF, dir1: str = "vertical", dir2: str = "vertical") -> QPainterPath:
    """Convenience wrapper: compute_connector_waypoints() (no obstacles)
    then path_from_waypoints(). Kept as its own function since most
    callers (most tests, the live drag-preview line before it has a real
    scene of macros to check) only ever want the plain routed shape, not
    the full waypoint list SupplyPortConnectorItem itself needs for
    obstacle avoidance and per-segment dragging."""
    return path_from_waypoints(compute_connector_waypoints(p1, p2, dir1, dir2))


def collect_macro_obstacles(boxes: dict, exclude: tuple = ()) -> list[QRectF]:
    """Every macro (.lib black-box) box's own scene rect, for
    compute_connector_waypoints() to route around -- explicit request:
    "line does not cross macro". `exclude` (a connector's own two
    endpoint boxes) keeps a connector from trying to detour around the
    very macro its own PG pin lives on -- the route is *supposed* to
    touch that box's edge."""
    return [box.sceneBoundingRect() for box in boxes.values() if box.definition.is_black_box and box not in exclude]


def known_supply_names(state: SupplyPortDiagramState) -> list[str]:
    """Every distinct supply-port name currently known anywhere in the
    diagram -- both user-placed ports and macro PG pins -- sorted for a
    stable, predictable display order. Kept for callers that genuinely
    want every name diagram-wide; power_domain_overlay.py's own Supply
    Set creation flow uses the box-scoped known_supply_names_for_owner()
    below instead (explicit request: "list of supply ports which have
    been already created on the hierarchy").

    Includes a Virtual Supply's own name.
    RULE REVERSAL: a Virtual Supply used to be EXCLUDED here ("cannot be
    used as a rail within a Supply Set"). It is now selectable as
    Power/Ground/Nwell/Pwell exactly like a Supply Port or an Internal
    Supply, and participates in its Supply Set's own generation with no
    separate path. Supply Set membership is a LOGICAL grouping, distinct
    from the physical connection restriction -- which is unchanged: a
    Virtual Supply still only ever connects to a macro's internal PG pins
    in the diagram, still has no Power/Ground type of its own, and still
    cannot host a Power Switch. It also remains usable standalone, so the
    two modes are not exclusive."""
    names = {p.name for p in state.supply_port_placements.values()} | {p.name for p in state.pg_pin_placements.values()}
    return sorted(names)


def connected_supply_port(anchor_id: str, state: SupplyPortDiagramState) -> Optional[SupplyPortPlacement]:
    """Whichever plain (non-fixed) Supply Port/Internal Supply `anchor_id`
    is wired to via a drawn power-net connector, if any -- `anchor_id` is
    typically a macro's own fixed PG pin, but works for any anchor id. A
    connector to another PG pin (not a plain port) doesn't count -- same
    "macro connection" shape power_intent_text_export.py's own
    _macro_connections() and properties_panel.py's own
    _connected_master_name() need; this is the shared, bare-placement-
    returning core both build on, rather than each re-walking
    supply_connector_placements independently (explicit request: reuse
    one generic "what Supply [Set] is this anchor point connected to"
    resolver rather than a one-off). Returns the raw SupplyPortPlacement
    (name + owner_path, un-formatted) -- None when no such connector
    exists."""
    for a_id, b_id in state.supply_connector_placements:
        other_id = b_id if a_id == anchor_id else (a_id if b_id == anchor_id else None)
        if other_id is None:
            continue
        master = state.supply_port_placements.get(other_id)
        if master is not None:
            return master
    return None


def known_supply_names_for_owner(state: SupplyPortDiagramState, owner_path: str) -> list[str]:
    """Same as known_supply_names(), scoped to one hierarchy box --
    explicit request: the Power/Ground/N-Well/P-Well pickers in Create/
    Edit Power Domain should only offer supply ports already anchored to
    *this* hierarchy, not every supply port anywhere in the diagram.
    "Add New..." (see create_power_domain_dialog.py's _SupplyNameCombo)
    is still how a name absent from this list gets introduced. Excludes
    a Virtual Supply's own name -- see known_supply_names()'s own
    docstring."""
    # A Virtual Supply anchored to this box is offered too -- see
    # known_supply_names()'s own note on that rule reversal.
    names = {p.name for p in state.supply_port_placements.values() if p.owner_path == owner_path}
    names |= {p.name for p in state.pg_pin_placements.values() if p.owner_path == owner_path}
    return sorted(names)


def known_supply_names_globally(state: SupplyPortDiagramState) -> list[str]:
    """Bug 9 (explicit request): the Power/Ground/N-Well/P-Well pickers
    in Create/Edit Power Domain now offer every plain Supply Port/
    Internal Supply DESIGN-WIDE, not just the ones already anchored to
    the hierarchy being composed on (known_supply_names_for_owner()/
    _for_owners() above stay exactly as they were -- still used
    elsewhere, e.g. Retention's own pickers, untouched by this bug).
    Deliberately EXCLUDES macro (.lib) PG pins -- unlike
    known_supply_names()/_for_owner() above, which both include them --
    a fixed PG pin's own position/identity is entirely derived from
    Liberty data, never something a Supply Set should reference or a
    Power Switch should attach to via this picker; a design-wide PG-pin
    name (routinely repeated, e.g. every macro's own "VDD"/"VSS") would
    also make this list far less useful. Selecting a name already
    anchored to a DIFFERENT hierarchy than the one being composed on is
    what triggers power_intent_diagram_view.py's own
    _auto_link_cross_hierarchy_supply_sets() -- see that method's own
    docstring. INCLUDES a Virtual Supply's own name -- see
    known_supply_names()'s own note on that rule reversal."""
    # Virtual Supplies included -- see known_supply_names()'s own note.
    return sorted({p.name for p in state.supply_port_placements.values()})


def known_supply_names_for_owners(state: SupplyPortDiagramState, owner_paths: list[str]) -> list[str]:
    """Union of known_supply_names_for_owner() across several hierarchy
    boxes at once (explicit request, multi-hierarchy Power Domain): the
    supply-name pool offered while composing a Supply Set for a domain
    spanning several member hierarchies must cover ANY of them, not
    just one."""
    names: set[str] = set()
    for owner_path in owner_paths:
        names |= set(known_supply_names_for_owner(state, owner_path))
    return sorted(names)


class ConnectorHandleItem(QGraphicsEllipseItem):
    def __init__(self, owner: "SupplyPortIconItem", local_pos: QPointF, color: QColor):
        r = _HANDLE_RADIUS
        super().__init__(local_pos.x() - r, local_pos.y() - r, r * 2, r * 2, owner)
        self.owner = owner
        self.setBrush(QBrush(color))
        self.setPen(QPen(color.darker(150), 1))
        self.setZValue(Z_SUPPLY_PORT + 1)
        self.setCursor(Qt.CursorShape.CrossCursor)  # hover feedback -- see module docstring

    def set_hover_highlight(self, active: bool) -> None:
        """Visual feedback while DiagramMode.SUPPLY_NET is active and the
        cursor is over this handle (explicit request, driven by
        power_intent_diagram_view.py's own _update_supply_net_hover()) --
        a bright, thicker ring; restores the handle's own resting pen
        (matching __init__'s own styling exactly) when the cursor moves
        off. Never touches the handle's brush -- that still reflects
        "wired to this colour or not" (see SupplyPortIconItem.recolor()),
        a genuinely different signal from "the cursor happens to be here
        right now". Uses the same shared HIGHLIGHT_COLOR the Supply Port
        drag-and-drop's own block-boundary overlay uses."""
        if active:
            self.setPen(QPen(HIGHLIGHT_COLOR, 3))
        else:
            self.setPen(QPen(self.brush().color().darker(150), 1))

    def reposition(self, local_pos: QPointF) -> None:
        """Moves this same handle object to a new local position -- used
        when the owning icon's edge changes (see
        SupplyPortIconItem._relayout_handles()) instead of destroying and
        recreating it, so an in-progress connector drag started from this
        handle (power_intent_diagram_view.py's `_connector_drag_origin`)
        stays valid even if the icon's edge somehow changes mid-drag."""
        r = _HANDLE_RADIUS
        self.setRect(local_pos.x() - r, local_pos.y() - r, r * 2, r * 2)

    def scene_center(self) -> QPointF:
        """The handle's actual drawn/visual center, in scene coordinates.

        NOT the same as self.scenePos() -- a real bug, found by driving a
        real QTest mouse press through the actual view/scene rather than
        only unit-testing the extracted logic: this item is never moved
        via setPos() (see __init__/reposition() -- its offset from the
        icon is expressed entirely via the ellipse's own rect(), so it
        can share the icon's local coordinate system for _clamp_to_edge()-
        style positioning). That leaves scenePos() (the item's origin)
        permanently equal to the *icon's* own scenePos(), not the
        handle's drawn location -- silently wrong for anything that
        needs "where is this handle, visually", which is exactly what
        the connector-drag line's start point needs."""
        return self.mapToScene(self.rect().center())

    def shape(self) -> QPainterPath:
        """A much larger hit-test area than the drawn dot -- see module
        docstring's _HANDLE_HIT_PADDING note. itemAt() (both
        _connector_handle_at() and Qt's own hover/cursor handling) uses
        shape(), not boundingRect(), so this is what actually makes the
        handle reliably clickable."""
        path = QPainterPath()
        center = self.rect().center()
        radius = _HANDLE_RADIUS + _HANDLE_HIT_PADDING
        path.addEllipse(center, radius, radius)
        return path


def connector_anchor_scene_pos(icon, prefer_key: Optional[str] = None) -> QPointF:
    """The scene position a power-net CONNECTOR should actually
    terminate at for `icon` -- its own correct ANCHOR HANDLE
    (ConnectorHandleItem.scene_center()), never the icon's own bare
    scenePos().

    Bug fix: SupplyPortConnectorItem's own route computation
    (_auto_waypoints()/update_route()/follow_endpoint_move()) used
    `icon.scenePos()` directly -- but ConnectorHandleItem.scene_center()'s
    own docstring already documents, from an EARLIER fix to the
    connector-DRAG preview line, that scenePos() is NOT the handle's
    drawn/visual location: a handle is never moved via setPos() (its
    offset from the icon is expressed entirely via its own ellipse
    rect()), so scenePos() permanently equals the *icon's* own origin
    (its geometric center), not any anchor point on its edge. That
    earlier fix was only ever applied to the drag-preview line, never
    to this class's own COMMITTED route -- this generalizes the exact
    same fix here, for every element type with anchor handles (Supply
    Port, Internal Supply, Power Switch).

    `prefer_key`, if given and present in `icon.handles`, wins outright
    -- the one case that needs a SPECIFIC handle regardless of the
    icon's own natural default: the AUTOMATIC Power Switch <-> target
    connector (build_power_switch_overlay()'s own construction, see its
    call sites) needs the switch's own "bottom" handle (Supply Out),
    not whatever this function would otherwise default to for it.
    Otherwise: a SupplyPortIconItem (or anything else with a `.placement.edge`)
    resolves via that edge -- the exact same key _INTERIOR_HANDLE_FOR_EDGE
    never drops, i.e. the icon's own EXTERIOR/outward-facing handle, the
    one meant for a connection to something outside its own box.
    Anything else with handles but no `.placement.edge` (a
    PowerSwitchIconItem, reached here only via the MANUAL/generic
    connector path -- its own AUTOMATIC "bottom" role is always covered
    by `prefer_key` above) resolves to "top" (Supply In, the only role a
    manual connection to a switch can ever represent). Falls back to
    `icon.scenePos()` if `icon` has no `.handles` at all, or the
    resolved key isn't actually present in it (defensive; every real
    icon this is called on today always has at least one handle)."""
    handles = getattr(icon, "handles", None)
    if not handles:
        return icon.scenePos()
    if prefer_key is not None and prefer_key in handles:
        return handles[prefer_key].scene_center()
    placement = getattr(icon, "placement", None)
    key = getattr(placement, "edge", None) if placement is not None else None
    if key is None or key not in handles:
        key = "top" if "top" in handles else next(iter(handles))
    return handles[key].scene_center()


class SupplyPortLabelItem(QGraphicsSimpleTextItem):
    """The icon's name -- independently draggable, clamped to stay within
    _LABEL_MAX_RADIUS of the icon's own center (see module docstring).
    Deliberately does NOT inherit the icon's own rotation (unlike a
    plain child item would by default -- but this class positions itself
    via its own itemChange()/persisted label_dx/dy, entirely independent
    of the icon's pixmap rotation, so there is nothing to counter-rotate
    in the first place)."""

    def __init__(self, owner: "SupplyPortIconItem", text: str):
        super().__init__(text, owner)
        self.owner = owner
        self.setFlag(QGraphicsItem.ItemIsMovable)
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges)

    def paint(self, painter, option, widget=None):
        """Explicit request: a 2-line label (name + voltage) must be
        *centered*, each line over the other -- not left-aligned, which
        is all QGraphicsSimpleTextItem's own default paint() does for
        multi-line text (every line starts flush at the same x, so a
        short "VDD" sits flush-left of a longer "(0.80V)" below it
        instead of centered above it). Draws each line manually,
        horizontally centered within the item's own (unchanged)
        boundingRect() -- callers positioning/clamping this item by that
        rect (see SupplyPortIconItem.__init__, itemChange() below) are
        unaffected, only the rendering changes."""
        painter.setFont(self.font())
        painter.setPen(QPen(self.brush().color()))
        metrics = QFontMetrics(self.font())
        rect = self.boundingRect()
        line_height = metrics.height()
        y = rect.top() + metrics.ascent()
        for line in self.text().split("\n"):
            x = rect.left() + (rect.width() - metrics.horizontalAdvance(line)) / 2
            painter.drawText(QPointF(x, y), line)
            y += line_height

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionChange:
            return _clamp_to_radius(value, _LABEL_MAX_RADIUS)
        if change == QGraphicsItem.ItemPositionHasChanged:
            self.owner.placement.label_dx = self.pos().x()
            self.owner.placement.label_dy = self.pos().y()
        return super().itemChange(change, value)


class SupplyPortIconItem(QGraphicsItem):
    """A plain QGraphicsItem with a custom paint() (explicit request) --
    NOT QGraphicsPixmapItem anymore. The earlier pixmap-based version
    (even supersampled 4x and smoothly downscaled, even with
    `transformationMode` forced to SmoothTransformation) was confirmed,
    via a live before/after screenshot comparison at real diagram zoom
    levels, to still visibly soften/pixelate once the view's own zoom
    stretched that fixed-resolution raster far enough -- a ceiling any
    cached-pixmap approach has by construction. paint() below instead
    renders the SVG live, straight from the single shared
    _supply_port_svg_renderer(), scaled to this item's own current
    boundingRect() every call -- genuinely resolution-independent at
    any zoom, the same way this diagram's own vector-drawn items (box
    borders, connector lines) already are.

    boundingRect() must now be implemented explicitly (previously
    derived implicitly from the cached QPixmap's own size/offset) --
    without it, Qt has no bounds to hit-test clicks/selection against
    or to know how much of the scene needs repainting."""

    def __init__(self, owner_box: InstanceBoxItem, placement: SupplyPortPlacement):
        super().__init__(owner_box)
        self.owner_box = owner_box
        self.placement = placement
        self.key = placement.id
        self.fixed = placement.fixed
        self.color = QColor(placement.color)
        self.connectors: list["SupplyPortConnectorItem"] = []
        # Generic "something needs to know when I move" registry -- e.g.
        # a Power Switch attached to this port (power_switch_overlay.py's
        # own PowerSwitchIconItem, a deliberately SCENE-LEVEL item, not a
        # child of this icon -- see that class's own docstring for why)
        # registers its own update_position() here so it tracks this
        # port's position exactly like `connectors`' own update_route()
        # already does, without this module ever needing to know that
        # Power Switch exists (a plain callable, not a typed reference --
        # keeps the dependency direction power_switch_overlay.py ->
        # supply_port_overlay.py one-way, never the reverse).
        self.on_move_callbacks: list = []
        # Same generic, type-agnostic registry shape as on_move_callbacks
        # above, for "something needs to know when I RECOLOR" instead of
        # "when I move" -- a Power Switch attached to this port (item 6,
        # explicit request: its own icon color live-follows its target's,
        # not a one-time copy taken at creation) registers here too.
        self.on_recolor_callbacks: list = []
        # Bug 2 (explicit request): click-selection highlight, managed
        # externally by _PowerIntentGraphicsView._on_scene_selection_changed()
        # -- see set_click_highlighted() below.
        self._click_highlighted = False

        half = _ICON_SIZE / 2
        self.setZValue(Z_SUPPLY_PORT)
        if not placement.fixed:
            self.setFlag(QGraphicsItem.ItemIsMovable)
        self.setFlag(QGraphicsItem.ItemIsSelectable)
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges)
        self.setPos(_edge_point(owner_box.rect(), placement.edge, placement.t))
        # Bug fix (explicit request): being parented to owner_box only
        # makes this icon track the box's own POS() automatically (Qt's
        # ordinary child-coordinate behavior) -- its own LOCAL offset
        # above is computed ONCE, from whatever rect() the box had at
        # THIS construction moment, and is otherwise never touched
        # again. A later resize of the SAME box (e.g. the "make room
        # for a new element" expansion logic growing it for some OTHER,
        # unrelated element added afterward) left this icon's own
        # fractional edge position silently stale -- no longer the
        # correct point along the box's own (now different) edge. See
        # resnap_to_owner_edge()'s own docstring for the fix and why it
        # deliberately does NOT cascade into a connector/Power-Switch
        # recompute the way a real drag does.
        owner_box.on_resize_callbacks.append(self.resnap_to_owner_edge)
        # Bug fix (explicit request -- recurring regression, previously
        # reported as "Power Switch not positioned correctly after prior
        # insertions"): being parented to owner_box ALSO only makes this
        # icon's own SCENE position track the box's own setPos() via
        # Qt's ordinary child-coordinate rendering -- it does NOT fire
        # THIS icon's own itemChange(ItemPositionHasChanged) (Qt only
        # fires that for a change to an item's OWN local pos(), never
        # because an ancestor moved), so anything registered on THIS
        # icon's own on_move_callbacks (a scene-level PowerSwitchIconItem,
        # notably -- it isn't parented to its target specifically to
        # dodge an unrelated Z-order bug, see that class's own docstring,
        # so it has no other way to learn its target moved) previously
        # went silently stale the moment a SIBLING box repacked to make
        # room for a brand-new element inserted on a DIFFERENT block.
        # See _on_owner_box_moved()'s own docstring for why this is a
        # narrower, cheaper update than a real drag's own cascade.
        owner_box.on_move_callbacks.append(self._on_owner_box_moved)

        # Generalized (explicit request, superseding an earlier
        # macro-only, `placement.fixed`-keyed version of this same
        # check): an icon anchored to a LEAF hierarchy block -- a macro
        # is always one, but so is any plain RTL block with no visible
        # child blocks of its own -- never gets its own "interior"
        # handle (see _interior_key()'s own docstring). A fixed
        # (macro PG-pin) icon's own edge never changes (no
        # ItemIsMovable -- see below), so this is only ever computed
        # once for it; a movable icon on a leaf block DOES need this
        # re-evaluated on every edge change, which _relayout_handles()
        # below does via the same shared helper.
        dropped_key = self._dropped_handle_key(placement.edge)
        self.handles = {
            key: ConnectorHandleItem(self, pos, self.color)
            for key, pos in _handle_layout(placement.edge, half).items()
            if key != dropped_key
        }

        self.label = SupplyPortLabelItem(self, _label_text(placement))
        if placement.label_dx is None or placement.label_dy is None:
            label_rect = self.label.boundingRect()
            placement.label_dx, placement.label_dy = _default_label_offset(placement.edge, label_rect, half)
        self.label.setPos(placement.label_dx, placement.label_dy)
        # Audit fix (category 04): from here on, any itemChange() position
        # notification is a real USER drag -- every programmatic
        # reposition in this class (set_edge(), resnap_to_owner_edge())
        # suppresses ItemSendsGeometryChanges around its own setPos(), and
        # __init__'s own initial setPos() above happens while this is
        # still False. Without that distinction, constructing an icon
        # cleared `ground_auto_anchored` on its own placement, so the flag
        # never survived a rebuild and the Ground revert could only ever
        # fire once.
        self._accepts_user_moves = True

    @property
    def name(self) -> str:
        return self.placement.name

    def boundingRect(self) -> QRectF:
        half = _ICON_SIZE / 2
        return QRectF(-half, -half, _ICON_SIZE, _ICON_SIZE)

    def paint(self, painter: QPainter, option, widget=None) -> None:
        """Live vector render -- see class docstring. Rotation is
        applied here (painter.save()/rotate()/restore()), not baked
        into a cached pixmap: rotating about the *current* coordinate
        origin, which is always this item's own local (0, 0) -- the
        icon's own center, per boundingRect() above -- exactly
        reproduces the old baked-in-pixmap rotation's own visual pivot,
        and (since render_tinted_svg() below composites via
        painter.drawImage(), which is subject to whatever transform is
        active) applies correctly to the tinted result too, at any
        rotation.

        Colour: this port's own stored `self.color` -- the SVG asset
        itself is a solid black silhouette, tinted at paint time via
        shared/diagram/svg_tint.py's render_tinted_svg() (see module
        docstring), not baked into a per-colour cached pixmap -- or the
        shared click-selection HIGHLIGHT_COLOR instead while
        `self._click_highlighted` (bug 2, explicit request): a temporary
        override of the port's own colour for the duration of the
        highlight, not a replacement of it -- `self.color` itself is
        untouched, so clearing the highlight (set_click_highlighted(False))
        reverts to the port's real colour with nothing further to
        restore."""
        half = _ICON_SIZE / 2
        painter.save()
        painter.rotate(_ROTATION_FOR_EDGE[self.placement.edge])
        color = HIGHLIGHT_COLOR if self._click_highlighted else self.color
        # Bug fix (explicit request): a macro's own INTERNAL PG pin
        # (`is_internal` -- PortDirection.PG_INTERNAL, e.g. VDD_INT) now
        # also renders with the Internal Supply icon, same as a user-
        # placed Internal Supply (`is_internal_supply`) -- previously
        # only `is_internal_supply` was checked here, so an internal PG
        # pin wrongly rendered with the plain Supply Port icon despite
        # terminating a macro's own INTERNAL pg_pin, not its main one.
        # Deliberately an OR of two genuinely separate flags (see
        # SupplyPortPlacement's own docstring on why they're kept
        # distinct, not unified into one) -- this is the one place both
        # happen to map onto the same visual choice; the general
        # top-level-vs-inner-hierarchy Supply Port/Internal Supply rule
        # itself (`is_internal_supply` alone, for a real user-placed
        # icon) is completely unaffected -- `is_internal` is always
        # False for any user-placed icon, so this OR is a no-op for
        # every case that isn't a macro's own internal PG pin. Handle
        # removal (no bottom/interior anchor) already applies uniformly
        # to every FIXED macro PG pin regardless of main-vs-internal --
        # see `interior_key` in __init__ above, keyed on `placement.fixed`
        # alone, never `is_internal` -- so no change was needed there.
        use_internal_supply_icon = self.placement.is_internal_supply or self.placement.is_internal
        icon_rect = QRectF(-half, -half, _ICON_SIZE, _ICON_SIZE)
        if self.placement.is_virtual_supply:
            # Two-layer render (bug fix, see _VIRTUAL_SUPPLY_FILL_ICON_PATH's
            # own comment): tint the diamond fill to the live colour, then
            # paint the "V" glyph plain/untinted on top -- always its own
            # literal white, completely unaffected by `color` above.
            render_tinted_svg(painter, _virtual_supply_fill_svg_renderer(), icon_rect, color)
            _virtual_supply_glyph_svg_renderer().render(painter, icon_rect)
        else:
            if use_internal_supply_icon:
                renderer = _internal_supply_svg_renderer()
            else:
                renderer = _supply_port_svg_renderer()
            render_tinted_svg(painter, renderer, icon_rect, color)
        painter.restore()

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionChange:
            point, _edge, _t = _clamp_to_edge(self.owner_box.rect(), value)
            return point
        if change == QGraphicsItem.ItemPositionHasChanged:
            _point, edge, t = _clamp_to_edge(self.owner_box.rect(), self.pos())
            if edge != self.placement.edge:
                self._relayout_handles(edge)
                self.update()  # schedule a repaint at the new rotation -- paint() reads self.placement.edge live
            self.placement.edge = edge
            self.placement.t = t
            # Audit fix (category 04): the user has now positioned this
            # icon themselves, so the Ground classifier must never move
            # it again -- see ground_auto_anchored's own docstring. Set
            # whenever `edge` changed or not: dragging along the SAME
            # edge is still a deliberate placement, and leaving the flag
            # set would let a later loss of Ground role yank the icon
            # back to the top edge underneath the user. Gated on
            # `_accepts_user_moves` so __init__'s own initial setPos()
            # doesn't count as a drag (see that flag's own comment).
            if getattr(self, "_accepts_user_moves", False):
                self.placement.ground_auto_anchored = False
            for connector in self.connectors:
                connector.update_route()
            for callback in self.on_move_callbacks:
                callback()
        return super().itemChange(change, value)

    def resnap_to_owner_edge(self) -> None:
        """Registered onto owner_box.on_resize_callbacks at construction
        -- see that call site's own docstring for the bug this fixes.
        Recomputes this icon's LOCAL edge-anchored position from the
        box's CURRENT rect(), keeping the same edge/t this icon was
        already anchored to (a resize never changes WHICH edge/fraction
        a port sits at, only where that point now falls in local
        coordinates).

        Deliberately suppresses itemChange()'s own connector-reroute /
        on_move_callback cascade for this one repositioning (toggling
        ItemSendsGeometryChanges off, then back on, around the
        setPos() call) -- this is a corrective realignment of THIS
        icon's own anchor caused by its OWN box RESIZING (a subtle
        in-place shift, e.g. a few pixels along the same edge), not a
        "this net moved" event -- power_intent_diagram_view.py's own
        _reflow_layout() docstring establishes that an insertion-driven
        layout change must never recompute an EXISTING net's own
        OPTIMAL route just because ITS OWN box grew slightly. The fix
        this enables is for whatever LATER reads this icon's scenePos()
        (e.g. inserting a BRAND NEW Power Switch targeting this same
        port) -- that must see the corrected position, even though
        nothing currently attached to this icon reacts to the
        correction immediately.

        Bug fix (explicit follow-up request, distinct from the above):
        this narrow "no cascade" scope was ALWAYS specific to a RESIZE
        of THIS icon's own box -- it was never meant to (and, per a
        separate, confirmed regression, incorrectly ended up) covering
        the case where the box MOVES to a genuinely different position
        (e.g. a sibling repacking to make room for something inserted
        elsewhere) -- see _on_owner_box_moved() below, registered
        separately on owner_box.on_move_callbacks, for that case: a
        Power Switch rendering above this icon, or a connector attached
        to it, DOES need to stay visually attached when the icon's own
        SCENE position genuinely relocates, or it renders floating,
        disconnected from what it's supposed to represent.

        Bug fix (found via Bug 2, "Ground supply renders at the bottom
        edge" -- previously latent, since every FIXED (macro PG-pin)
        icon was always "top"-anchored before that change, and "top"'s
        own _edge_point() formula never depends on box HEIGHT at all,
        so skipping this method for a fixed icon was a harmless no-op in
        practice): a fixed icon's own edge/t are set once at construction
        and never change (no ItemIsMovable -- see __init__), but its
        SCENE position still must track a LATER resize of its own box,
        exactly like a movable icon's does -- `t` is still a fraction of
        the box's own current width/height, same _edge_point() formula
        either way. Confirmed empirically: a bottom-anchored macro
        ground PG pin left stale (still at the box's OLD bottom edge,
        now floating mid-block) after its box grew taller, with nothing
        else in this class re-deriving it for a fixed icon."""
        new_pos = _edge_point(self.owner_box.rect(), self.placement.edge, self.placement.t)
        if new_pos == self.pos():
            return
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, False)
        self.setPos(new_pos)
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)

    def set_edge(self, edge: str) -> None:
        """Bug 2 (explicit request): programmatically moves this icon to
        a different edge of its owner box, mirroring exactly what a
        manual drag-to-that-edge already does via itemChange()'s own
        _clamp_to_edge() cascade (relayout handles, recompute the label's
        default offset, reroute connectors, fire on_move_callbacks) --
        used for a plain Ground Supply Port/Internal Supply, whose Ground
        role (unlike a macro PG pin's, always known upfront from Liberty
        data) can only become known LATER, once some Supply Set actually
        names it via `.ground` -- see
        power_intent_diagram_view.py's own _reclassify_ground_supply_edges().
        A no-op for a fixed (macro PG-pin) icon -- its edge is decided
        once, at build_supply_port_overlay() construction time, and
        never changes afterward, same rule resnap_to_owner_edge() already
        establishes.

        Deliberately always resets the label to `edge`'s own fresh
        default (never preserves a previously-dragged custom label
        offset) -- the same "unconditional reassignment on every edge
        change" precedent _relayout_handles() below already establishes
        for handles; a user who re-drags the label afterward is free to
        move it again, exactly as before."""
        if self.fixed or edge == self.placement.edge:
            return
        half = _ICON_SIZE / 2
        self._relayout_handles(edge)
        self.placement.edge = edge
        label_rect = self.label.boundingRect()
        self.placement.label_dx, self.placement.label_dy = _default_label_offset(edge, label_rect, half)
        self.label.setPos(self.placement.label_dx, self.placement.label_dy)
        new_pos = _edge_point(self.owner_box.rect(), edge, self.placement.t)
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, False)
        self.setPos(new_pos)
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)
        self.update()
        for connector in self.connectors:
            connector.update_route()
        for callback in self.on_move_callbacks:
            callback()

    def _on_owner_box_moved(self) -> None:
        """Registered onto owner_box.on_move_callbacks at construction --
        see that call site's own docstring for the bug this fixes, and
        resnap_to_owner_edge()'s own docstring for how this differs from
        (and is registered separately from) the RESIZE case.

        This icon's own LOCAL pos()/edge/t relationship to its box is
        completely unaffected by the box MOVING (only resizing changes
        that) -- Qt's ordinary parent-child transform already keeps this
        icon's own rendered SCENE position correctly following the box
        for free. What's missing is notifying whatever else tracks this
        icon's own scene position independently: connectors (kept
        visually attached via the CHEAP follow_endpoint_move() -- the
        same "slide the endpoint, don't re-run the full obstacle-
        avoiding search" precedent an in-progress live drag already
        uses, deliberately NOT a full update_route() recompute, so an
        insertion-driven reflow elsewhere still never disturbs an
        EXISTING net's own carefully-avoided obstacle routing, matching
        _reflow_layout()'s own established scope) and on_move_callbacks
        (which is what actually repositions a scene-level
        PowerSwitchIconItem rendered above this icon -- see that
        class's own docstring on why it can't rely on Qt's parent-child
        transform at all)."""
        for connector in self.connectors:
            connector.follow_endpoint_move()
        for callback in self.on_move_callbacks:
            callback()

    def _dropped_handle_key(self, edge: str) -> Optional[str]:
        """Which of _handle_layout(edge)'s own 2 keys this icon should
        NOT materialize a ConnectorHandleItem for -- at most one is
        ever dropped.

        A Virtual Supply (explicit request: "the icon has ONLY A
        BOTTOM ANCHOR -- no top anchor at all... nothing can ever
        drive a Virtual Supply, so there is no upstream connection
        point to offer") ALWAYS drops its own EXTERIOR handle instead
        -- the key literally named after `edge` itself (see
        _handle_layout()'s own naming: the handle whose name matches
        the icon's own anchor axis points straight OUTWARD from the
        block; the opposite-named one, `_INTERIOR_HANDLE_FOR_EDGE[edge]`,
        points back IN). This OVERRIDES _interior_key()'s own
        is_leaf-based rule entirely for a Virtual Supply -- the two
        never compose -- so a Virtual Supply anchored on a leaf block
        still keeps exactly one handle (its own interior one), never
        zero."""
        if self.placement.is_virtual_supply:
            return edge
        return self._interior_key(edge)

    def _interior_key(self, edge: str) -> Optional[str]:
        """Which of _handle_layout(edge)'s own 2 keys is this icon's own
        "interior" one -- the one facing INTO its owner block's body --
        to be dropped entirely, or None if this icon should keep both.
        Generalized (explicit request) from an earlier macro-only,
        `placement.fixed`-keyed check to instead key off
        `owner_box.is_leaf` directly -- a macro is always a leaf (a
        black-box definition can never have children), so this is a
        strict generalization, not a behavior change, for every
        pre-existing macro PG-pin case; it now ALSO applies to a real,
        movable user-placed Supply Port/Internal Supply anchored to a
        plain RTL block that simply has no visible children of its own.
        `owner_box.is_leaf` is fixed for this icon's whole lifetime (the
        box object itself is rebuilt, along with every icon on it,
        whenever hierarchy visibility changes -- see
        hierarchy_scene_builder.py's own InstanceBoxItem.is_leaf), so
        this is safe to call fresh on every edge change rather than
        caching it once."""
        return _INTERIOR_HANDLE_FOR_EDGE[edge] if self.owner_box.is_leaf else None

    def _relayout_handles(self, edge: str) -> None:
        """Moves the icon's existing handle object(s) (never destroys/
        recreates them -- see ConnectorHandleItem.reposition()'s own
        docstring) to whichever positions/keys _handle_layout() says this
        edge should have -- e.g. top/bottom handles become left/right
        ones when the icon lands on a left/right edge. A leaf-block icon
        (see _interior_key()) has only ever had ONE handle since
        construction -- the interior key is excluded here too, so that
        single handle keeps being reassigned to the new edge's own
        non-interior position/key, never regrowing a second handle."""
        half = _ICON_SIZE / 2
        dropped_key = self._dropped_handle_key(edge)
        layout = [(key, pos) for key, pos in _handle_layout(edge, half).items() if key != dropped_key]
        new_handles = {}
        for handle, (key, pos) in zip(self.handles.values(), layout):
            handle.reposition(pos)
            new_handles[key] = handle
        self.handles = new_handles

    def recolor(self, color: QColor) -> None:
        """Used when this icon is a PG pin (fixed=True) that just got
        connected to a real supply port -- see module docstring. Updates
        the icon's own drawn glyph (self.update() schedules a repaint --
        paint() reads self.color live, there is no cached/pre-rendered
        colour to invalidate), the 2 handles, and the persisted placement
        colour together, so a later rebuild restores the new colour, not
        the original default grey."""
        self.color = QColor(color)
        self.placement.color = QColor(color)
        self.update()
        for handle in self.handles.values():
            handle.setBrush(QBrush(self.color))
            handle.setPen(QPen(self.color.darker(150), 1))
        for callback in self.on_recolor_callbacks:
            callback()

    def set_click_highlighted(self, highlighted: bool) -> None:
        """Bug 2 (explicit request): the fluorescent-yellow click-
        selection highlight -- managed externally by
        _PowerIntentGraphicsView._on_scene_selection_changed(), not by
        this icon's own mouse handling (Qt's native ItemIsSelectable
        selection already decides *which* icon is selected; this is
        purely the visual reaction to that)."""
        if self._click_highlighted == highlighted:
            return
        self._click_highlighted = highlighted
        self.update()


def _segment_value(a: QPointF, b: QPointF) -> float:
    """The one coordinate that actually varies along a segment's own
    drag axis -- Y for a horizontal segment, X for a vertical one."""
    return a.y() if a.y() == b.y() else a.x()


def _set_segment_value(waypoints: list[QPointF], index: int, value: float) -> list[QPointF]:
    """Returns a new waypoint list with segment `index`'s own two
    endpoints shifted to `value` along its drag axis (Y for horizontal,
    X for vertical) -- shared by SupplyPortConnectorItem.update_route()
    (reapplying a persisted offset) and set_manual_segment_position()
    (a live drag) so both go through the exact same, single mutation
    logic. Shifting only the *shared* axis keeps the two neighboring
    segments (whose endpoints these are)
    still orthogonal for free."""
    waypoints = list(waypoints)
    a, b = waypoints[index], waypoints[index + 1]
    if a.y() == b.y():
        waypoints[index] = QPointF(a.x(), value)
        waypoints[index + 1] = QPointF(b.x(), value)
    else:
        waypoints[index] = QPointF(value, a.y())
        waypoints[index + 1] = QPointF(value, b.y())
    return waypoints


class SupplyPortConnectorItem(QGraphicsPathItem):
    """A permanent orthogonal connector between two SupplyPortIconItems,
    colored like the (non-fixed) supply side of the connection. Added
    directly to the scene (not parented to either icon -- it spans two
    different parents), and kept in sync via each icon's own
    itemChange(ItemPositionHasChanged) calling back into update_route()
    whenever either endpoint moves.

    `waypoints` (the full point list behind the rendered path, see
    compute_connector_waypoints()) is what makes two further explicit
    requests possible on top of the base routing:
    - "line does not cross macro": update_route() collects every macro
      box currently in the scene (except the 2 this connector's own
      endpoints are anchored to -- a PG-pin connector's own target macro
      is not an obstacle to route *around*, the route is supposed to
      reach its edge) and detours middle segments around them.
    - Per-segment dragging: segment_at()/segment_orientation()/
      set_manual_segment_position() let power_intent_diagram_view.py's
      own mouse handling let the user drag any *middle* segment (never
      the first/last -- those must stay perpendicular to their own
      anchor, guaranteed simply by never being offered as draggable) --
      horizontal ones vertically, vertical ones horizontally.

    A dragged segment is remembered as `_manual_offset` -- (segment
    index, *delta* from wherever the auto-router would otherwise have
    put that segment), not as absolute coordinates. That distinction is
    load-bearing, found via a real end-to-end check (drag a segment,
    trigger an unrelated rebuild, confirm it's still there) rather than
    assumed: an *unrelated* Hide/Unhide elsewhere in the tree can repack
    the whole diagram and shift the root box's own scene origin -- and
    with it, every single box/icon's absolute scene position -- even
    though nothing about this connector's own endpoints changed at all.
    Storing an absolute waypoint list would silently stop matching after
    a totally unrelated hide, discarding a manual edit for no reason a
    user would ever expect. A relative offset, reapplied on top of a
    freshly (re)computed auto-route every time, is translation-invariant
    by construction -- it survives an unrelated global shift *and*
    gracefully follows a deliberate move of either connected icon,
    rather than being discarded outright by either.

    Endpoints are resolved via connector_anchor_scene_pos() (bug fix:
    an icon's own scenePos() is its geometric center, not its correct
    ANCHOR HANDLE) -- `port_a_handle_key`/`port_b_handle_key` only ever
    need overriding for the ONE case that needs a SPECIFIC handle
    regardless of the icon's own natural default: the AUTOMATIC Power
    Switch <-> target connector (its own construction sites pass
    port_a_handle_key="bottom" for the switch side) -- every other
    caller (a fresh user-drawn "Supply Net", or a manually-drawn Power
    Switch Supply In connector) leaves both None and gets each icon's
    own natural default.

    follow_endpoint_move() is a cheap counterpart to the full
    update_route() -- it just slides this connector's own two ENDPOINTS
    to wherever port_a/port_b currently are, leaving every interior
    waypoint untouched. Used when an icon's OWNER BOX moves without the
    icon itself being dragged (see SupplyPortIconItem._on_owner_box_moved())
    -- an insertion-driven layout change must never recompute an
    EXISTING net's own route just because a sibling box shifted to make
    room for something else, but the connector still needs to visibly
    stay attached to its target.
    """

    def __init__(
        self,
        port_a: SupplyPortIconItem,
        port_b: SupplyPortIconItem,
        port_a_handle_key: Optional[str] = None,
        port_b_handle_key: Optional[str] = None,
    ):
        super().__init__()
        self.port_a = port_a
        self.port_b = port_b
        self._port_a_handle_key = port_a_handle_key
        self._port_b_handle_key = port_b_handle_key
        self.waypoints: list[QPointF] = []
        self._manual_offset: Optional[tuple[int, float]] = None
        self.setZValue(Z_SUPPLY_PORT_CONNECTOR)
        self.setPen(QPen(port_a.color, _CONNECTOR_LINE_WIDTH))
        # Bug fix (explicit request): this pen was previously set ONCE
        # here and never touched again -- for a Power Switch's own
        # connector (port_a=PowerSwitchIconItem, whose own `.color` is
        # already a LIVE property reading port_b/target_icon's colour,
        # see PowerSwitchIconItem's own docstring), the drawn line
        # itself still went stale the moment port_b actually recoloured,
        # even though port_a.color would have reported the new value if
        # read again. Registers a live pen refresh onto whichever
        # endpoint(s) actually expose on_recolor_callbacks -- duck-typed
        # (getattr(..., None)) since a PowerSwitchIconItem endpoint has
        # no such list of its own (it's a CONSUMER of its target's list,
        # not a provider -- see that class's own docstring), so this
        # correctly fires from whichever endpoint(s) really do support
        # it without needing to know which one that is. Cleaned up
        # symmetrically in _remove_connector()/_remove_power_switch_placement()
        # (power_intent_diagram_view.py) -- same dangling-callback hazard
        # PowerSwitchIconItem.sync_color's own registration already
        # established the precedent for.
        for endpoint in (port_a, port_b):
            callbacks = getattr(endpoint, "on_recolor_callbacks", None)
            if callbacks is not None:
                callbacks.append(self._sync_pen)
        self.update_route()

    def _sync_pen(self) -> None:
        self.setPen(QPen(self.port_a.color, _CONNECTOR_LINE_WIDTH))

    def _collect_obstacles(self) -> list[QRectF]:
        scene = self.scene()
        if scene is None:
            return []
        boxes = {item.path: item for item in scene.items() if isinstance(item, InstanceBoxItem)}
        return collect_macro_obstacles(boxes, exclude=(self.port_a.owner_box, self.port_b.owner_box))

    def _auto_waypoints(self) -> list[QPointF]:
        start = connector_anchor_scene_pos(self.port_a, self._port_a_handle_key)
        end = connector_anchor_scene_pos(self.port_b, self._port_b_handle_key)
        dir_a = _exit_direction(self.port_a)
        dir_b = _exit_direction(self.port_b)
        return compute_connector_waypoints(start, end, dir_a, dir_b, self._collect_obstacles())

    def update_route(self) -> None:
        waypoints = self._auto_waypoints()
        if self._manual_offset is not None:
            index, delta = self._manual_offset
            if 0 < index < len(waypoints) - 2:  # still a genuine middle segment in this (possibly re-shaped) route
                base_value = _segment_value(waypoints[index], waypoints[index + 1])
                waypoints = _set_segment_value(waypoints, index, base_value + delta)
            else:
                self._manual_offset = None  # obstacle avoidance reshaped the route enough that this index no longer applies
        self.waypoints = waypoints
        self.setPath(path_from_waypoints(self.waypoints))

    def apply_manual_offset(self, offset: tuple[int, float]) -> None:
        """Restores a previously-dragged segment from persisted state
        (see SupplyPortDiagramState.manual_connector_offsets) -- just
        sets `_manual_offset` and lets update_route() do the actual, and
        only, application logic."""
        self._manual_offset = offset
        self.update_route()

    def follow_endpoint_move(self) -> None:
        """Cheap live feedback for a target box moving without the icon
        itself being dragged (see class docstring) -- slides this
        connector's own two ENDPOINTS to wherever port_a/port_b
        currently are, leaving every interior waypoint exactly where it
        was."""
        if len(self.waypoints) < 2:
            self.update_route()
            return
        self.waypoints[0] = connector_anchor_scene_pos(self.port_a, self._port_a_handle_key)
        self.waypoints[-1] = connector_anchor_scene_pos(self.port_b, self._port_b_handle_key)
        self.setPath(path_from_waypoints(self.waypoints))

    def hit_test(self, scene_pos: QPointF) -> bool:
        """Whether `scene_pos` is near ANY segment of this connector --
        every one of them, including the first/last anchor-touching
        stubs segment_at() deliberately excludes (those aren't
        draggable, but the whole line -- explicit request -- is still a
        valid right-click "Delete" target end to end)."""
        for i in range(len(self.waypoints) - 1):
            a, b = self.waypoints[i], self.waypoints[i + 1]
            rect = QRectF(a, b).normalized().adjusted(-_SEGMENT_HIT_PADDING, -_SEGMENT_HIT_PADDING, _SEGMENT_HIT_PADDING, _SEGMENT_HIT_PADDING)
            if rect.contains(scene_pos):
                return True
        return False

    def segment_at(self, scene_pos: QPointF) -> Optional[int]:
        """Which *middle* segment (never the first or last -- see class
        docstring) `scene_pos` is near enough to (within
        _SEGMENT_HIT_PADDING of the line, not just exactly on it -- same
        generous-hit-area precedent as ConnectorHandleItem.shape()), or
        None."""
        for i in range(1, len(self.waypoints) - 2):
            a, b = self.waypoints[i], self.waypoints[i + 1]
            rect = QRectF(a, b).normalized().adjusted(-_SEGMENT_HIT_PADDING, -_SEGMENT_HIT_PADDING, _SEGMENT_HIT_PADDING, _SEGMENT_HIT_PADDING)
            if rect.contains(scene_pos):
                return i
        return None

    def segment_orientation(self, index: int) -> str:
        a, b = self.waypoints[index], self.waypoints[index + 1]
        return "horizontal" if a.y() == b.y() else "vertical"

    def set_manual_segment_position(self, index: int, value: float) -> None:
        """Drags segment `index` -- a horizontal segment moves vertically
        (`value` is the new Y), a vertical one moves horizontally
        (`value` is the new X). Computes and stores `_manual_offset` as
        the delta from the *current* auto-route's own value at this
        index (see class docstring on why a delta, not an absolute
        coordinate) -- called on every mouse-move during a drag, so this
        recomputes the auto baseline each time; acceptable, the geometry
        involved is cheap for realistic diagram sizes."""
        auto_waypoints = self._auto_waypoints()
        if not (0 < index < len(auto_waypoints) - 2):
            return
        auto_value = _segment_value(auto_waypoints[index], auto_waypoints[index + 1])
        self._manual_offset = (index, value - auto_value)
        self.waypoints = _set_segment_value(auto_waypoints, index, value)
        self.setPath(path_from_waypoints(self.waypoints))


def build_supply_port_overlay(
    boxes: dict[str, InstanceBoxItem],
    placements: dict[str, SupplyPortPlacement],
    pg_pin_placements: dict[str, SupplyPortPlacement],
    ground_supply_keys: "frozenset[tuple[str, str]] | set[tuple[str, str]]" = frozenset(),
) -> dict[str, SupplyPortIconItem]:
    """Materializes every supply-port icon (user-placed + auto-derived PG
    pins) against the current (just-rebuilt) `boxes` -- called after
    every hierarchy-scene rebuild, see power_intent_diagram_view.py's
    _rebuild() override. Returns a {key: icon} map (see
    SupplyPortPlacement.id's own docstring for what `key` is per kind),
    for the caller to also restore connectors against.

    A PG pin's SupplyPortPlacement is created once, the first time that
    pin is ever seen (evenly spread along the default top edge -- index i
    of n -> t=(i+1)/(n+1), same even-spacing convention
    hierarchy_scene_builder.py's own _layout_port_anchors() uses for
    input/output ports), and inserted into `pg_pin_placements` so it's
    found (and reused verbatim -- same dragged edge/t/colour/label offset
    as last time) on every later rebuild, exactly like a user-placed
    port's own placement. Bug 2 exception: a macro PG pin classified as
    Ground (see below) seeds "bottom" instead of "top" as ITS OWN
    default, same one-time-at-creation treatment.

    `ground_supply_keys` (Bug 2, explicit request; re-scoped by the
    category-04 audit fix): the set of `(owner_path, name)` pairs
    currently carrying a Ground role (see `is_ground_supply()` for why
    this is a pair rather than a bare name). Every PLAIN (non-fixed)
    placement is re-resolved against it here via the shared
    `apply_ground_edge_classification()` -- the same two-way rule
    power_intent_diagram_view.py's own `_reclassify_ground_supply_edges()`
    applies live, between rebuilds, for an already-materialized icon
    (see that method's own docstring for why both paths are needed).
    """
    icons: dict[str, SupplyPortIconItem] = {}

    for placement in placements.values():
        box = boxes.get(placement.owner_path)
        if box is None:
            continue  # owner is currently hidden -- nothing to anchor to this pass, see module docstring
        # Audit fix (category 04): shared with the live reclassifier via
        # apply_ground_edge_classification(), and now two-way -- see
        # resolve_ground_edge_change()'s own docstring. Runs BEFORE the
        # icon exists, so no icon is passed; the placement is mutated
        # directly and materialized at the resolved edge below.
        apply_ground_edge_classification(placement, ground_supply_keys)
        icons[placement.id] = SupplyPortIconItem(box, placement)

    for path, box in boxes.items():
        if not box.definition.is_black_box:
            continue
        # PG_WELL (real macro PG-pin-connectivity golden fixture, sram.v/
        # sram.lib with an EXPOSED VPW pwell pin): a real, independently
        # connectable macro pin -- same auto-derived-icon treatment as
        # PG/PG_INTERNAL, see PortDirection.PG_WELL's own docstring.
        pg_ports = [p for p in box.definition.ports if p.direction in (PortDirection.PG, PortDirection.PG_INTERNAL, PortDirection.PG_WELL)]
        count = len(pg_ports)
        # Bug 2 (explicit request, "derive from receiver_ground supply of
        # ports"): the backend schema never records power-vs-ground on a
        # PG pin ITSELF (Milestone 1e deliberately collapses primary_power/
        # primary_ground alike down to just PortDirection.PG) -- but a
        # FUNCTIONAL pin's own Port.related_ground_pin already names
        # exactly which PG pin grounds it (see that field's own
        # docstring, rtl.py), so any PG pin named by SOME functional
        # pin's related_ground_pin is, transitively, a real ground pin.
        # A PG pin no functional pin ever references this way keeps the
        # existing "top" default -- not guessed at.
        ground_pg_names = {p.related_ground_pin for p in box.definition.ports if p.related_ground_pin}
        for i, port in enumerate(pg_ports):
            key = macro_pg_pin_id(path, port.name)
            placement = pg_pin_placements.get(key)
            if placement is None:
                placement = SupplyPortPlacement(
                    id=key,
                    owner_path=path,
                    name=port.name,
                    color=QColor(_PG_PIN_DEFAULT_COLOR),
                    edge="bottom" if port.name in ground_pg_names else "top",
                    t=(i + 1) / (count + 1),
                    fixed=True,
                    voltage=port.voltage,
                    is_internal=port.direction == PortDirection.PG_INTERNAL,
                )
                pg_pin_placements[key] = placement
            icons[key] = SupplyPortIconItem(box, placement)

    return icons


def macro_pg_pin_id(macro_path: str, pin_name: str) -> str:
    """The id of a macro's own auto-derived PG-pin placement.

    Deterministic and shared on purpose. These placements are derived
    from the `.lib` definition when the scene is built, never imported
    or saved -- so the UPF/CPF import has to be able to NAME one (to
    draw a `connect_supply_net -ports {u_sram/VDD}` connector) before it
    exists. Both sides call this rather than each spelling the format
    out, so the key cannot drift between them."""
    return f"pg:{macro_path}:{pin_name}"


def top_edge_supply_port_owners(supply_port_icons: dict[str, SupplyPortIconItem]) -> set[str]:
    """Owner-box paths with at least one top-edge Supply Port/Internal
    Supply icon materialized this pass -- shared by every interior-
    anchored badge type (Isolation Location=self, Level Shifter
    Location=self, Retention -- always interior/top-right corner, no
    location concept of its own) that needs to reserve
    INTERIOR_TOP_MARGIN_FOR_SUPPLY_PORT worth of extra clearance below
    such an icon's own name label, rather than silently rendering
    underneath/overlapping it.

    Bug fix (explicit request, "sram example" repro): a top-edge Supply
    Port/Internal Supply icon sits HALF INSIDE the box by construction
    (see INTERIOR_TOP_MARGIN_FOR_SUPPLY_PORT's own docstring), and its
    own name label renders BELOW that, further into the interior --
    confirmed empirically (a live scene: two Supply Ports near the left
    portion of a block's own top edge, then an Isolation Location=self
    badge on the same block) that this label's own bounding rect
    genuinely intersects an interior badge's own fixed top-left-corner
    position, which was computed with NO awareness of what else already
    occupies that corner. Reserving one shared, deterministic band of
    extra clearance (this function, `INTERIOR_TOP_MARGIN_FOR_SUPPLY_PORT`
    itself) -- rather than attempting true per-pixel collision detection
    against an arbitrarily-dragged Supply Port's own current position --
    matches this diagram's own established "make room" margin-budget
    philosophy (_extra_interior_top()'s own fixed-band reservations),
    not a new, bespoke mechanism."""
    return {icon.owner_box.path for icon in supply_port_icons.values() if icon.placement.edge == "top"}
