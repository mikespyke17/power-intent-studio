"""Power Switch -- explicit request: attaches to a Supply Port/Internal
Supply icon (never a hierarchy block directly), diagram-only/mock-first
for this pass, same scoping decision already established for Supply
Ports/Retention (see supply_port_overlay.py/retention_overlay.py's own
module docstrings): nothing here is reflected into the tab's
PowerIntentDesign.

Architecturally closest to Retention (tool palette entry, hover+click
placement, right-click context-menu Add/Edit/Delete triad, Power-
Domain-existence validation reusing find_effective_domain_for_path())
-- but its TARGET is a Supply Port/Internal Supply icon, not a
hierarchy block, so `PowerSwitchPlacement.target_id` references a
SupplyPortPlacement's own id (SupplyPortDiagramState.supply_port_placements/
pg_pin_placements), not an owner_path directly. `owner_path` (below) is
the TARGET's own owner_path, cached at creation time purely so
find_switch_for_target()/find_effective_domain_for_path() callers don't
need the live SupplyPortDiagramState just to resolve which Power
Domain a switch belongs to -- always kept in sync with the target's
own owner_path by construction (a Supply Port's own owner_path never
changes after placement; only its edge/t do).

Two anchor points, confirmed via explicit follow-up (the diagram's
top/bottom anchors are NOT literal Supply In/Supply Out selections --
see power_switch_dialog.py's own module docstring for the full
resolved architecture):
- BOTTOM anchor -- AUTOMATIC, fixed, 1:1 with `target_id`. This is
  "Supply Out": always and only the Supply Port/Internal Supply the
  switch is attached to. Rendered as a connector line the moment the
  switch is placed, never independently created/deleted/dragged by the
  user (see power_switch_overlay.build_power_switch_overlay()).
- TOP anchor -- MANUALLY DRAWN via the EXISTING Supply Net tool/
  connector-drag gesture (DiagramMode.SUPPLY_NET), exactly like any
  other Supply Port-to-Supply Port connection -- this IS "Supply In":
  whatever is connected there (resolved via the exact same
  connected_supply_port() core supply_port_overlay.py already uses for
  "what's on the other end of this anchor"), or unresolved/empty if
  nothing is connected yet. No independent dropdown/stored id for this
  -- it is derived fresh from SupplyPortDiagramState.supply_connector_placements
  every time it's needed (dialog open, PortList-adjacent display),
  never persisted redundantly on PowerSwitchPlacement itself.

Input/Output Supply Port + Control Port (rework, explicit request --
REVERSES the earlier "Cell Type dropdown referencing a reusable
PowerSwitchCellPlacement library" model back to per-instance free text):
`input_supply_port`/`output_supply_port`/`control_port` are now plain,
required, no-spaces pin-name fields directly on PowerSwitchPlacement
itself -- there is no more separate, reusable Cell library entity to
create first. `cell_type` is a genuinely separate, OPTIONAL free-text
label (no longer a dropdown referencing anything) -- a switch with an
empty Cell Type is not a blocking error, just flagged via a
generation-time warning (see power_switch_dialog.py/properties_panel.py's
own docstrings), same non-blocking framing the removed library model
already established, just keyed off an empty string instead of an
unselected/unassigned reference.

The Supply In/Supply Out ELECTRICAL connection resolution described
above (the two anchor points) is completely unchanged by any of this --
only the PIN-NAME/Cell-Type story changed. `input_supply_port`/
`output_supply_port` double as the UPF role tag for the exported
`-input_supply_port {<role> <net>}`/`-output_supply_port {<role> <net>}`
flags, `<net>` being the ELECTRICALLY resolved Supply In/Out name (see
power_intent_text_export.py). `on_state`/`off_state` (dialog labels
"ON State"/"OFF State") are unaffected by this rework -- ON State stays
required, OFF State is optional.
"""

from __future__ import annotations

import re

from dataclasses import dataclass, field, replace
from functools import lru_cache
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QByteArray, QPointF, QRectF
from PySide6.QtGui import QBrush, QColor, QPainter, QPen
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QGraphicsItem, QGraphicsSimpleTextItem

from frontend.src.power_intent_view.supply_port_overlay import (
    HIGHLIGHT_COLOR,
    LEVELLED_STATE_TYPES,
    OFF,
    ConnectorHandleItem,
    SupplyPortDiagramState,
    SupplyPortIconItem,
    SupplyPortPlacement,
    SupplyPortState,
    connected_supply_port,
)
from frontend.src.shared.diagram.svg_tint import render_tinted_svg

_ICON_PATH = Path(__file__).resolve().parents[2] / "icons" / "power-switch.svg"
# Same visual weight as a Supply Port/Retention badge icon on the same
# diagram (supply_port_overlay.py's own _ICON_SIZE / retention_overlay.py's
# own _BADGE_SIZE, both 28) -- explicit precedent for "icon anchored
# near a box/port," not the tool palette's own much smaller icon size.
_ICON_SIZE = 28
_GAP = 6.0  # vertical clearance between the switch's own bottom edge and its target's own top edge
_HANDLE_RADIUS = 4.0  # matches ConnectorHandleItem's own module-level constant (supply_port_overlay.py) -- kept in sync by construction, see PowerSwitchIconItem.__init__

Z_POWER_SWITCH = 6  # same layer as Supply Port icons (Z_SUPPLY_PORT) -- both are per-target anchored icons
Z_POWER_SWITCH_CONNECTOR = 5  # same layer as Supply Port connectors (Z_SUPPLY_PORT_CONNECTOR) -- below every icon

# Item 5 (explicit request): how much extra vertical room a top-edge-
# anchored target needs reserved above its own owning hierarchy box so
# a Power Switch attached to it never visually overlaps a neighboring
# box -- the exact same "icon's own full footprint above the target"
# distance PowerSwitchIconItem.update_position() already computes
# (half the icon's own height + the gap + half the icon's own height
# again, i.e. _ICON_SIZE + _GAP), kept as one shared public constant so
# power_intent_diagram_view.py's own _extra_top_margin() override can't
# silently drift out of sync with the icon's real rendered size.
TOP_MARGIN_FOR_SWITCH = _ICON_SIZE + _GAP


def power_switch_connector_key(placement_id: str, target_id: str) -> str:
    """The connector-anchor key for ONE specific replicated instance of
    a (possibly multi-hierarchy) Power Switch -- deliberately DISTINCT
    from `placement.id` itself (the switch's own shared identity,
    still used everywhere the switch is treated as ONE logical entity:
    find_switch_for_target(), the Properties panel's own single row,
    edit/delete).

    Bug fix: `PowerSwitchIconItem.key` used to just BE `placement.id`
    -- correct for a single-target switch, but AMBIGUOUS the moment a
    switch replicates onto several targets (items 9/10): every
    replica shared the exact same `.key`, so a manually-drawn Supply
    In connector's own persisted `(upstream_key, switch_key)` pair
    could not tell which REPLICA it belonged to once rebuilt (the
    generic `_rebuild()` restoration loop looks icons up BY key), and
    `_shorts_power_switch()`'s own `a.placement.target_id` comparison
    (the shared record's "primary" target only) silently mis-
    validated every non-primary replica. Computable from persisted
    data alone (a placement id plus one specific target id), so a
    caller can resolve/rebuild a specific replica's own Supply In
    without needing a live icon for it at all."""
    return f"switch:{placement_id}:{target_id}"


# Example port names, used ONLY as placeholder text in the dialog.
#
# These were fixed ABSTRACT TOKENS that `create_power_switch` emitted,
# with `map_power_switch -port_map` bridging them to the user's own field
# values. That whole indirection is RETIRED (explicit decision): a
# switch's port names ARE the four field values now, emitted directly,
# and no `-port_map` is generated at all -- which returns
# `create_power_switch` to the shape it had before the external review
# introduced the tokens. What survives is these strings' use as
# realistic examples in the dialog, nothing more.
ABSTRACT_IN = "IN"
ABSTRACT_OUT = "OUT"
ABSTRACT_CTRL = "CTRL"
ABSTRACT_ACK = "ACK"

# The NAME given to the Type=OFF state a Power Switch contributes to an
# Internal Supply's inherited list. Deliberately NOT "OFF": a source
# supply may plausibly have its own state called that, and two entries
# sharing a name in one list is exactly what the per-supply
# duplicate-name check exists to reject. This one is tool-derived, so it
# must not collide by construction -- if a source genuinely has a
# "SWITCH-OFF" of its own, the duplicate check fires and the user
# resolves it, which is the right outcome rather than silently producing
# two identically-named entries.
SWITCH_OFF_STATE_NAME = "SWITCH-OFF"

# What counts as part of an identifier, for deciding whether an
# occurrence of a name inside a boolean expression is the WHOLE name or
# just a substring of a longer one. Deliberately not `\b`, whose meaning
# flips when the pattern itself starts or ends with a non-word character
# -- and a real HDL name legitimately contains `/`, `[`, `]` or `.`.
# Explicit lookarounds behave the same either way.
_IDENTIFIER_CHAR = r"[A-Za-z0-9_$]"


def _signal_pattern(signal: str) -> "re.Pattern[str]":
    return re.compile(rf"(?<!{_IDENTIFIER_CHAR}){re.escape(signal)}(?!{_IDENTIFIER_CHAR})")


def references_control_signal(expression: str, signal: str) -> bool:
    """Whether `expression` mentions `signal` as a whole identifier.

    `pwr_en` does NOT count as a reference inside `pwr_en_cpu` -- that is
    a different net, and treating it as a match would rewrite half a name
    into the port name."""
    if not expression or not signal:
        return False
    return _signal_pattern(signal).search(expression) is not None


def substitute_control_signal(expression: str, signal: str, token: str) -> str:
    """An ON/OFF State expression rewritten for UPF.

    The user writes these in terms of the REAL RTL signal, because that
    is what the design contains and what they can check against the HDL.
    UPF instead wants the switch's own CONTROL PORT name, so every
    whole-identifier occurrence of the signal becomes `token`, and
    everything else is left exactly as authored:

        signal="pwr_switch", token="pg_ctrl"
          "pwr_switch"               -> "pg_ctrl"
          "pwr_switch && some_other" -> "pg_ctrl && some_other"
          "sleep_n"  (unrelated)     -> "sleep_n", unchanged

    `token` was the fixed `CTRL`; it is the user's own Control Port field
    value now, since that is what the generated `-control_port` actually
    declares. Passing it in rather than reading a module constant is what
    makes the declaration and the expression agree by construction.

    The third case is deliberate rather than an error: the expression may
    reference something this app has no field for, and emitting it
    verbatim is the honest outcome. The dialog's own inline hint is what
    tells the user, while they can still act on it."""
    if not expression or not signal or not token:
        return expression
    return _signal_pattern(signal).sub(token, expression)


def substitute_abstract_control(expression: str, signal: str, token: str) -> str:
    """The inverse, for reading a generated file back in.

    Without it a round trip silently rewrites the user's own expression
    into the port's vocabulary and leaves it there: what they typed
    ("pwr_switch") comes back as the port name, and the next export has
    nothing left to substitute. Whole-identifier only, so a name merely
    CONTAINING the token is untouched."""
    if not expression or not signal or not token:
        return expression
    return _signal_pattern(token).sub(signal, expression)


@dataclass
class PowerSwitchPlacement:
    """Plain, Qt-item-independent record of one Power Switch -- see
    module docstring for the full anchor-point/Supply-In-Out story.

    `target_ids`/`owner_paths` (explicit request, item 9 -- multi-
    hierarchy Power Domain replication): a Power Switch added to an
    Internal Supply belonging to one member hierarchy of a multi-
    hierarchy Power Domain automatically applies to the "corresponding"
    Internal Supply (same name) on EVERY OTHER member hierarchy too --
    ONE shared PowerSwitchPlacement spans all of them, exactly the same
    "one shared record, elements/owner_paths spanning multiple
    hierarchies" shape RetentionStrategyPlacement/PowerDomainPlacement
    already establish (not a separate replication-group-id scheme) --
    editing/deleting from ANY one target updates/removes the whole
    shared record, automatically covering every other target too, with
    zero extra bookkeeping. `target_id`/`owner_path` (singular) are kept
    as real fields, not derived properties, reconciled with their own
    plural counterparts in __post_init__ -- the exact same "either form
    fills in the other" backward-compatibility precedent
    PowerDomainPlacement/SupplySetPlacement already establish for their
    own owner_path/owner_paths pair, so every existing single-target
    call site (test or production) that only ever passes `target_id=`
    keeps working completely unchanged."""

    id: str
    name: str
    power_domain_id: str
    target_id: str = ""  # the SupplyPortPlacement.id this switch is attached to -- "Supply Out". Reconciled with target_ids below.
    owner_path: str = ""  # the target's own owner_path, cached -- see module docstring. Reconciled with owner_paths below.
    target_ids: list[str] = field(default_factory=list)
    owner_paths: list[str] = field(default_factory=list)
    # Rework (explicit request, supersedes the earlier "Cell Type
    # dropdown referencing a reusable PowerSwitchCellPlacement library"
    # model): the pin-name fields live directly on the instance again --
    # no more separate library entity to create first. All three are
    # required, no-spaces (pin-name-like), free text.
    # These three are the REAL LIBRARY CELL PIN names (review finding
    # 2.3). They are what `map_power_switch -port_map` maps the abstract
    # UPF switch ports IN/OUT/CTRL onto -- without that mapping, UPF
    # falls back to matching the abstract names against the cell's own
    # pins literally, which fails whenever a real cell's pins are not
    # themselves called IN/OUT/CTRL.
    input_supply_port: str = ""
    output_supply_port: str = ""
    control_port: str = ""
    # The RTL SIGNAL that actually drives this switch's control input
    # (review finding 2.1) -- a genuinely different thing from
    # `control_port` above, which is a pin on the cell. Real UPF needs
    # BOTH: `-control_port {CTRL <this net>}` connects the abstract
    # control port to a real signal, and without a net there is no
    # connection to emit at all. Optional: a switch whose control signal
    # is not decided yet is not blocked, the same way a missing Cell
    # Type is not.
    control_signal: str = ""
    # The switch's ACKNOWLEDGE output: the library cell's own pin
    # (`ack_port`) and the real RTL signal it drives (`ack_signal`),
    # mirroring the control pair exactly. Both OPTIONAL -- a switch with
    # no acknowledge output is completely ordinary, and an empty pair
    # generates no acknowledge construct at all rather than an empty one.
    ack_port: str = ""
    ack_signal: str = ""
    # Genuinely separate from the pin-name fields above -- a purely
    # descriptive, OPTIONAL free-text label (no longer a reference to
    # anything). None/empty is a valid, non-blocking state -- see
    # properties_panel.py's own docstring for the generation-time
    # warning this triggers instead.
    cell_type: Optional[str] = None
    on_state: str = ""
    off_state: str = ""

    def __post_init__(self) -> None:
        if self.target_ids:
            self.target_id = self.target_ids[0]
        else:
            self.target_ids = [self.target_id]
        if self.owner_paths:
            self.owner_path = self.owner_paths[0]
        else:
            self.owner_paths = [self.owner_path]


@dataclass
class PowerSwitchDiagramState:
    """Shared, Qt-independent Power Switch state for an entire Power
    Intent Diagram -- same cross-tab-sharing mechanism as
    SupplyPortDiagramState/RetentionDiagramState, see their own
    docstrings for the full reasoning. One instance is constructed once
    by PowerIntentDiagramView and handed to every _PowerIntentGraphicsView
    it creates."""

    switches: dict[str, PowerSwitchPlacement] = field(default_factory=dict)


def copy_power_switch_diagram_state(state: PowerSwitchDiagramState) -> PowerSwitchDiagramState:
    """An independent snapshot -- same "why" as every other diagram-only
    state's own copy_*_diagram_state(): Ctrl+Z/Ctrl+Y undo/redo needs a
    genuinely separate copy, not one still sharing the same mutable
    dict/PowerSwitchPlacement objects (nor their own `target_ids`/
    `owner_paths` LIST fields, item 9) with the live state -- same
    "replace() the list fields explicitly, dataclasses.replace() alone
    only shallow-copies them" precedent copy_power_domain_diagram_state()
    already establishes for owner_paths/secondary_supply_sets."""
    return PowerSwitchDiagramState(
        switches={k: replace(v, target_ids=list(v.target_ids), owner_paths=list(v.owner_paths)) for k, v in state.switches.items()},
    )


def restore_power_switch_diagram_state(state: PowerSwitchDiagramState, snapshot: PowerSwitchDiagramState) -> None:
    """Copies `snapshot`'s fields onto the *live* `state` object in
    place -- never replaces `state` itself, since it's shared by
    reference across every tab (see the class's own docstring)."""
    restored = copy_power_switch_diagram_state(snapshot)
    state.switches = restored.switches


def find_switch_for_target(switches: dict[str, PowerSwitchPlacement], target_id: str) -> Optional[PowerSwitchPlacement]:
    """Validation rule A (max ONE Power Switch per Supply Port/Internal
    Supply): the single source of truth both the "Add Power Switch"
    menu/tool's own enable/disable state AND add_power_switch()'s own
    data-model-level guard consult -- never rely on UI-disabling alone,
    per the explicit requirement (a second add attempt reachable another
    way, e.g. a stale/re-entrant dialog, must still be rejected here).

    Checks membership in `target_ids` (item 9: a multi-hierarchy switch
    spans several targets, any one of which counts as "already
    switched") rather than the singular `target_id` alone."""
    return next((s for s in switches.values() if target_id in s.target_ids), None)


def trace_upstream_source(
    start_id: str,
    shared_state: SupplyPortDiagramState,
    switches: dict[str, PowerSwitchPlacement],
) -> tuple[Optional[SupplyPortPlacement], list[PowerSwitchPlacement]]:
    """Walks backward from `start_id` (a SupplyPortPlacement.id -- almost
    always an Internal Supply's own id, though nothing here requires
    that) through any Power Switches and plain pass-through connectors
    encountered along the way, to find its ultimate source Supply
    Port/Internal Supply. Built fresh by composing the existing
    single-hop primitives (connected_supply_port(), find_switch_for_
    target(), power_switch_connector_key()) -- no such multi-hop
    "walk the chain" operation already existed in this codebase.

    A cascade of switches is modeled as a CHAIN of distinct targets, not
    several switches sharing one target (validation already forbids
    that -- see find_switch_for_target()'s own docstring): switch B's
    own target is internal supply Z; switch B's own Supply In (top
    handle) is manually wired to internal supply Y; Y is itself switch
    A's own target; switch A's own Supply In is wired onward to the real
    source Supply Port X. Tracing from Z walks Z -> (via switch B) -> Y
    -> (via switch A) -> X, collecting both switches along the way, in
    nearest-to-`start_id`-first order.

    Returns (None, []) for a not-yet-connected anchor -- deliberately
    NOT `(some placement, ...)` unless at least one real hop actually
    happened, so a brand-new, unconnected Internal Supply never
    incorrectly appears as its own source. `visited` guards against a
    malformed/cyclic wiring looping forever; recomputed fresh on every
    call (no caching) so it's always safe to call from a live rendering
    path (Properties table, Info preview) and see up-to-date results the
    moment connectivity or an upstream state list changes.

    Bug fix: STOPS the moment it reaches a genuine, non-Internal-Supply
    Supply Port -- such a placement is always a TERMINAL real source
    (its own `.states` are plain, user-authored data, never a further
    derived trace -- the exact same top-level distinction
    resolve_effective_states() below already makes), never a pass-
    through node to keep walking beyond, REGARDLESS of whether it
    happens to have yet ANOTHER connector of its own. Found via the
    exact combination CHANGE 2's own ancestor Supply Set sharing
    (`_auto_link_cross_hierarchy_supply_sets()`) creates: a real,
    root-level Supply Port (e.g. "VDD" at "top", carrying its own
    authored Power States) can ALSO have a second, unrelated connector
    of its own -- an auto-linked "mirror" copy of the SAME net,
    materialized as a SEPARATE, empty-states Internal Supply on a
    sharing hierarchy. Without this check, reaching the real source via
    a switch chain didn't stop there: `connected_supply_port()` (a
    single-hop, direction-agnostic lookup with no notion of "which edge
    did we arrive via") picked WHICHEVER of the real source's two
    connectors happens to be listed first, silently wandering PAST the
    real source and into that empty-states mirror copy instead -- the
    trace's own final `resolved` ended up pointing at the mirror
    (states=[]), not the real source, so only the switch-generated
    "SWITCH-OFF" state ever showed up, with the real source's own
    inherited states silently dropped."""
    visited = {start_id}
    switches_found: list[PowerSwitchPlacement] = []
    current_id = start_id
    resolved: Optional[SupplyPortPlacement] = None
    while True:
        switch = find_switch_for_target(switches, current_id)
        probe_key = power_switch_connector_key(switch.id, current_id) if switch is not None else current_id
        next_placement = connected_supply_port(probe_key, shared_state)
        if next_placement is None or next_placement.id in visited:
            break
        if switch is not None:
            switches_found.append(switch)
        visited.add(next_placement.id)
        current_id = next_placement.id
        resolved = next_placement
        if not next_placement.is_internal_supply:
            break
    return resolved, switches_found


def resolve_effective_states(
    placement: SupplyPortPlacement,
    shared_state: SupplyPortDiagramState,
    switches: dict[str, PowerSwitchPlacement],
) -> list[SupplyPortState]:
    """A plain Supply Port's effective states are just its own
    user-authored list, unchanged. An Internal Supply has NO independent
    editable list at all (see SupplyPortPlacement.states's own
    docstring) -- its effective states are entirely DERIVED: the
    ultimate source Supply Port's own states (via trace_upstream_source()
    above), PLUS a single plain Type=OFF state when ANY Power Switch sits
    in the chain (the inherited levelled states remain present too --
    representing "switch is ON, passing the source through unchanged").
    An unconnected Internal Supply resolves to an empty list -- callers
    show their own "Not yet connected" fallback text for that case, this
    function just reports the plain data.

    REDESIGNED, superseding the earlier rule that each switch generated
    its own distinctly-named "SWITCH-OFF (<switch name>)" state:

      - the added state is a PLAIN OFF, in the same vocabulary every
        other state now uses, with no switch name in it;
      - if the inherited states ALREADY contain an OFF, none is added --
        a source that can be off does not become more off downstream;
      - CASCADED switches collapse to AT MOST ONE OFF in total, not one
        per switch. Two switches in series can only produce one
        "unpowered" condition, so the extra entries said nothing.

    The deduplication is tool-derived, not a user-facing conflict: there
    is nothing for the user to resolve."""
    if not placement.is_internal_supply:
        return list(placement.states)
    source, switches_found = trace_upstream_source(placement.id, shared_state, switches)
    inherited = list(source.states) if source is not None else []
    if switches_found and not any(s.state_type == OFF for s in inherited):
        return [*inherited, SupplyPortState(state_type=OFF, name=SWITCH_OFF_STATE_NAME)]
    return inherited


def _display_state(state: SupplyPortState) -> str:
    """One rail state, for the read-only "Inherited Power States"
    preview.

    The authored NAME leads, so the preview faithfully reflects the
    source supply's own definitions rather than just their physics --
    "ON: FULL_ON — 1.2V". A state with no name (an imported one, until
    someone edits it) shows the physical form alone, exactly as before."""
    physical = (
        f"{state.state_type} — {state.voltage}V"
        if state.state_type in LEVELLED_STATE_TYPES and state.voltage
        else state.state_type
    )
    return f"{state.name}: {physical}" if state.name else physical


def describe_inherited_states(
    placement: SupplyPortPlacement,
    shared_state: SupplyPortDiagramState,
    switches: dict[str, PowerSwitchPlacement],
) -> Optional[list[str]]:
    """One formatted line per inherited state, EACH carrying its own
    provenance (explicit request) -- "<TYPE> — <voltage>V (from
    <source_port_name>)" for a state inherited directly from the real
    source Supply Port, and a single "SWITCH-OFF: OFF (via <switch
    names>)" line for
    the one OFF a Power Switch in the chain contributes.

    Follows resolve_effective_states()'s own redesigned rule exactly: ONE
    OFF at most, none at all when the source already has one, and no
    switch name baked into the state itself -- the provenance suffix is
    the only place a switch is named, which is what this preview is for.
    Used by AddPowerSupplyDialog's own read-only
    "Inherited Power States" preview -- kept separate from
    resolve_effective_states() above (which returns plain
    SupplyPortState objects with no provenance, for consumers like the
    Properties table that don't need it).

    Returns None when `placement` isn't connected to anything yet (the
    caller shows its own "Not yet connected" message for that case) --
    distinct from an empty list, which means genuinely connected but
    with nothing to inherit (the source itself has no states, and no
    switch was encountered along the way)."""
    if not placement.is_internal_supply:
        return None
    source, switches_found = trace_upstream_source(placement.id, shared_state, switches)
    if source is None:
        return None
    lines = [f"{_display_state(s)} (from {source.name})" for s in source.states]
    if switches_found and not any(s.state_type == OFF for s in source.states):
        via = ", ".join(sw.name for sw in switches_found)
        # Named like every other line, so the preview and the effective
        # state list agree about what this entry is called.
        lines.append(f"{SWITCH_OFF_STATE_NAME}: {OFF} (via {via})")
    return lines


@lru_cache(maxsize=1)
def _power_switch_svg_renderer() -> QSvgRenderer:
    """Lazily constructed (needs a live QApplication/QGuiApplication) --
    same "one shared renderer per icon type, parsed once for the whole
    process" precedent as _retention_svg_renderer()/_supply_port_svg_renderer()."""
    return QSvgRenderer(QByteArray(_ICON_PATH.read_bytes()))


class PowerSwitchIconItem(QGraphicsItem):
    """A plain QGraphicsItem with a custom paint() -- same "live SVG
    render, not a cached QPixmap" precedent as SupplyPortIconItem/
    RetentionBadgeIconItem (see either's own docstring for the pixelation
    "why").

    Deliberately a SCENE-LEVEL item (no QGraphicsItem parent at all,
    added directly to the scene -- same "spans/escapes its own natural
    container" precedent SupplyPortConnectorItem already establishes),
    NOT a child of its target SupplyPortIconItem the way an earlier
    version of this class was. Real, confirmed bug class this avoids --
    the exact same one already found and fixed multiple times elsewhere
    in this diagram (_BoxHighlightOverlay's own hit-test-shadowing;
    diagram_overlay.py's own unparented SwitchIconItem/SupplyRailItem
    fix for _box_at()): Qt's "a child always stacks above its own
    parent" rule ONLY resolves ordering within a shared ancestor
    context -- a descendant that visually pokes OUTSIDE its own parent's
    rect (this icon, rendered ABOVE its target, routinely extends past
    the target's own owning hierarchy box's top edge) can end up
    UNDERNEATH an entirely unrelated SIBLING box that happens to occupy
    that same screen region, if that sibling was inserted into the
    scene later -- because cross-branch comparisons happen at the
    nearest COMMON ancestor, where this icon's own high LOCAL Z value
    never even enters the comparison. Being scene-level instead removes
    the ambiguity entirely: Z_POWER_SWITCH (6) is compared DIRECTLY
    against every hierarchy box's own flat Z_INSTANCE_BOX (1) at the
    same top-level context, so this icon always renders above every box
    regardless of insertion order, no matter how far it pokes outside
    its target's own containing box.

    Since it's no longer a child, its position is NOT automatically
    kept in sync with its target by Qt's own parent-child transform --
    `update_position()` recomputes it explicitly (target's own
    scenePos() plus a fixed vertical offset, always "above," never
    edge-clamped or independently draggable: a Power Switch's position
    is entirely derived from its target, matching "rendered ABOVE the
    Supply Port/Internal Supply it's attached to" literally), called
    once at construction and registered onto the target's own
    `on_move_callbacks` (supply_port_overlay.py's own generic "notify me
    when I move" registry, added specifically for this -- kept
    type-agnostic there so that module never needs to know Power Switch
    exists). Since the target's own edge-rotation is applied entirely
    inside SupplyPortIconItem.paint() (painter.rotate(), never
    QGraphicsItem.setRotation()), this icon was never affected by it
    even back when it WAS a child -- "above" stays visually correct
    regardless of which edge the target currently sits on, unchanged by
    this rework.

    Exposes `.placement` (`.edge` deliberately absent -- see
    supply_port_overlay._exit_direction()'s own generalized getattr()
    fallback, defaulting to "vertical", exactly right for a switch that
    only ever has top/bottom handles), `.color`, and `.owner_box`
    (borrowed from the target's own owner_box) purely so this class can
    be handed to SupplyPortConnectorItem/is_nested_pair()/
    try_connect_supply_ports() interchangeably with a real
    SupplyPortIconItem -- see power_intent_diagram_view.py's own
    build_power_switch_overlay() call site for the auto bottom connector,
    and its Supply Net mousePressEvent/try_connect_supply_ports() branch
    for the user-drawn top connector (both reuse the EXISTING generic
    connector machinery unmodified, rather than a bespoke parallel
    implementation)."""

    def __init__(self, target_icon: SupplyPortIconItem, placement: PowerSwitchPlacement):
        super().__init__()
        self.target_icon = target_icon
        self.owner_box = target_icon.owner_box
        self.placement = placement
        # Bug fix: per-replica-unique, NOT bare placement.id -- see
        # power_switch_connector_key()'s own docstring.
        self.key = power_switch_connector_key(placement.id, target_icon.key)
        self.fixed = False  # never Qt-draggable -- position is entirely derived from the target, see class docstring
        self.connectors: list = []
        self._click_highlighted = False

        half = _ICON_SIZE / 2
        self.setZValue(Z_POWER_SWITCH)
        self.setFlag(QGraphicsItem.ItemIsSelectable)
        self.update_position()
        target_icon.on_move_callbacks.append(self.update_position)
        target_icon.on_recolor_callbacks.append(self.sync_color)

        self.handles = {
            "top": ConnectorHandleItem(self, QPointF(0, -half), self.color),
            "bottom": ConnectorHandleItem(self, QPointF(0, half), self.color),
        }

        # Item 7 (explicit request): the switch's own Name, rendered to
        # the RIGHT of its icon -- a plain QGraphicsSimpleTextItem child
        # (safe to parent here despite this icon's own "must be scene-
        # level" rule -- see class docstring -- since the LABEL never
        # needs to render outside ITS OWN parent's bounds the way the
        # icon itself does relative to the target's box; a child of an
        # already-always-on-top item is simply always-on-top too).
        # Deliberately plain, not styled to match Power Domain's own
        # bold-italic label -- no styling precedent this codebase
        # already has is a strong enough match to be worth copying
        # (explicit request: "doesn't need to match... unless it makes
        # sense to").
        self.label = QGraphicsSimpleTextItem(placement.name, self)
        self._position_label()

    def _position_label(self) -> None:
        half = _ICON_SIZE / 2
        label_height = self.label.boundingRect().height()
        self.label.setPos(half + 4, -label_height / 2)

    def set_name(self, name: str) -> None:
        """Keeps the rendered label text in sync with a renamed switch
        -- called by edit_power_switch() alongside updating `.placement`
        itself."""
        self.label.setText(name)
        self._position_label()  # re-centers -- a longer/shorter name changes the label's own boundingRect()

    def update_position(self) -> None:
        """Recomputes this icon's own scene position from its target's
        CURRENT scenePos() -- see class docstring for why this can't
        just be a local-coordinate offset via QGraphicsItem parenting
        anymore. Called once at construction and every time the target
        itself moves (registered onto the target's own
        on_move_callbacks).

        Bug fix (explicit report: "the power net is not very anchored
        to the top anchor of the power switch... moving an internal
        supply port... seems getting disconnecting from the power
        net"): this class has no itemChange() override (unlike
        SupplyPortIconItem, whose own itemChange() re-routes every
        connector in `self.connectors` on every real move) -- calling
        setPos() here alone left this icon's OWN connectors pointing at
        its stale pre-move position. The auto bottom connector
        (Supply Out, to the target) happened to still look correct
        after a drag purely by accident -- it's ALSO registered in the
        TARGET's own `.connectors`, and the target's real itemChange()
        already re-routes it -- but the manually-drawn TOP connector
        (Supply In, to whatever upstream is connected) is registered
        ONLY here and on the upstream icon, neither of which moved, so
        it was never re-routed at all: confirmed empirically, its own
        drawn path stayed pinned to the switch's pre-drag position
        after the target moved. Re-routing both here (not just relying
        on the target's own itemChange for the bottom one) keeps this
        self-contained and correct regardless of which connector(s)
        happen to exist at the time."""
        half = _ICON_SIZE / 2
        offset = QPointF(0, -(half + _GAP + _ICON_SIZE / 2))
        self.setPos(self.target_icon.scenePos() + offset)
        for connector in self.connectors:
            connector.update_route()

    @property
    def color(self) -> QColor:
        """A LIVE reference to the target's own current colour (item 6,
        explicit request: "not a one-time copy taken at Power Switch
        creation time") -- deliberately a property reading
        `target_icon.color` fresh every time, not a cached QColor field
        the way this class (and SupplyPortIconItem itself) used to work.
        paint() already reads this live, so no further wiring is needed
        for the icon's own glyph; sync_color() below is only needed to
        also push the new colour onto the 2 CHILD handles, which (unlike
        paint()) hold their own static brush/pen set at construction
        time, not read live."""
        return self.target_icon.color

    def sync_color(self) -> None:
        """Registered onto the target's own on_recolor_callbacks --
        pushes the target's new colour onto this icon's own 2 handles
        (their brush/pen are static, not read live -- see `color`'s own
        docstring) and schedules a repaint of the icon's own glyph
        (`paint()` reads `self.color` -- the live property above -- on
        every call, so update() alone is enough there)."""
        self.update()
        for handle in self.handles.values():
            handle.setBrush(QBrush(self.color))
            handle.setPen(QPen(self.color.darker(150), 1))

    @property
    def name(self) -> str:
        return self.placement.name

    def boundingRect(self) -> QRectF:
        half = _ICON_SIZE / 2
        return QRectF(-half, -half, _ICON_SIZE, _ICON_SIZE)

    def paint(self, painter: QPainter, option, widget=None) -> None:
        half = _ICON_SIZE / 2
        rect = QRectF(-half, -half, _ICON_SIZE, _ICON_SIZE)
        color = HIGHLIGHT_COLOR if self._click_highlighted else self.color
        render_tinted_svg(painter, _power_switch_svg_renderer(), rect, color)

    def set_click_highlighted(self, highlighted: bool) -> None:
        if self._click_highlighted == highlighted:
            return
        self._click_highlighted = highlighted
        self.update()


def build_power_switch_overlay(
    supply_port_icons: dict[str, SupplyPortIconItem], switches: dict[str, PowerSwitchPlacement]
) -> dict[str, list[PowerSwitchIconItem]]:
    """Materializes every Power Switch's own icon(s) against the current
    (just-rebuilt) `supply_port_icons` -- called after
    build_supply_port_overlay() itself (a switch's own icon positions
    itself FROM its TARGET icon's own scenePos(), which must already
    exist -- see PowerSwitchIconItem's own docstring). Returns a
    {switch_id: [icons]} map -- one entry per TARGET the switch actually
    spans (item 9: `target_ids`, plural -- a multi-hierarchy Power
    Domain replicates one switch across every member hierarchy's own
    corresponding Internal Supply), same shape build_retention_overlay()
    already establishes for a Retention Strategy spanning multiple
    blocks. Icons ONLY, NOT yet added to any scene -- same split
    build_supply_port_overlay() already establishes for
    SupplyPortConnectorItem between itself and its caller's own
    connector-restoration loop: this function has no `scene` parameter,
    so power_intent_diagram_view.py's own _rebuild() (right after this
    call) is what actually calls scene.addItem() for each icon AND
    wires up each one's own auto bottom connector (switch <-> target).

    A target whose own owner box is currently hidden (so its own
    SupplyPortIconItem isn't materialized this pass) simply doesn't get
    an icon for THAT target -- same "not materialized, not discarded"
    precedent every other diagram-only placement here already follows
    (see SupplyPortPlacement's own docstring); the switch's OTHER,
    still-visible targets are unaffected."""
    icons: dict[str, list[PowerSwitchIconItem]] = {}
    for placement in switches.values():
        switch_icons = []
        for target_id in placement.target_ids:
            target_icon = supply_port_icons.get(target_id)
            if target_icon is None:
                continue
            switch_icons.append(PowerSwitchIconItem(target_icon, placement))
        icons[placement.id] = switch_icons
    return icons
