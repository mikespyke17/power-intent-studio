"""Two-layer caching behind the Port List tab's "Driver Supply Set"/
"Receiver Supply Set" columns (hierarchy_view/properties_table_widget.py).

Layer 1 -- `StructuralTraceCache`: the expensive, pure-RTL-structural
connectivity_flatten.flatten_design() pass. Built exactly ONCE, when a
design loads (PowerIntentTab.set_rtl()), since RTL structure never
changes after that point -- mirrors "run at Elaborate time" for the
mock-first frontend the same way the real backend runs flatten_design()
once right after a successful elaborate().

Layer 2 -- `PowerResolutionCache`: a cheap, Power-Domain-flavored
resolution ON TOP of Layer 1's plain structural Endpoints -- which
Supply Set a resolved endpoint's owning cell belongs to (via
power_domain_overlay.find_effective_domain_for_path()'s existing
"deepest owner wins" rule and that domain's own Primary Supply Set), or
which special-case display text applies (a Port Attribute override at
the top-level boundary, "External - No Port Attribute Associated" when
none is assigned, Undriven/Floating/Combinational Loop/Multiple
Drivers). Invalidated
wholesale via `.invalidate_all()` --
a full recompute is cheap since Layer 1 (the actual trace) is never
touched -- wired to the existing `diagramStateChanged` signal
(power_intent_diagram_view.py), which already fires on every Power
Domain/Supply Set/Port Attribute edit and every undo/redo.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from frontend.src.power_intent_view.connectivity_flatten import (
    DriverResult,
    Endpoint,
    FlattenedConnectivity,
    ReceiverResult,
    TraceKind,
    flatten_design,
)
from frontend.src.power_intent_view.port_attribute_overlay import PortAttributeDiagramState, port_attribute_row_keys
from frontend.src.power_intent_view.power_domain_overlay import PowerDomainDiagramState, SupplySetPlacement, find_effective_domain_for_path
from frontend.src.power_intent_view.supply_port_overlay import SupplyPortDiagramState, connected_supply_port
from frontend.src.shared.models.rtl import Definition, Design, HierarchyNode, PortDirection, WellBiasPin

UNDRIVEN_TEXT = "Undriven"
FLOATING_TEXT = "Floating"
LOOP_TEXT = "Combinational Loop"
MULTIPLE_DRIVERS_TEXT = "Multiple Drivers"
# Nomenclature cleanup (explicit request): renamed from the earlier
# "No Power Associated" -- same meaning (a top-level boundary port has
# no Port Attribute assigned, so the integration/external side has no
# defined supply), clearer name -- "External" makes explicit this is
# specifically the top-level-boundary case, not a catch-all.
# Further renamed (explicit follow-up request, "more appropriate"):
# "No Power Associated" -> "No Port Attribute Associated" -- names the
# actual absent thing (a Port Attribute assignment), not "power" in the
# abstract, matching what this branch is actually checking
# (port_attribute_state.assignments).
NO_PORT_ATTRIBUTE_ASSOCIATED_TEXT = "External - No Port Attribute Associated"
NO_PRIMARY_SUPPLY_TEXT = "No Primary Supply"
# Nomenclature cleanup (explicit request): a distinct, VALID marker
# scoped ONLY to RTL (non-macro) nodes -- never a macro pin (see
# MACRO_NO_SUPPLY_SET_TEXT below, which owns that case exclusively).
# Used when the connectivity trace successfully finds real
# driving/receiving RTL logic (NOT Undriven/Floating -- a driver/sink
# genuinely exists), but that logic's own hierarchy has no Power Domain
# assigned at all, so there's no Primary Supply Set to report. Formerly
# a separate NO_DOMAIN_TEXT ("No Power Domain") constant/marker existed
# for exactly this case -- retired in favor of this one, since the two
# were never actually distinguishable by the user (both mean "real
# logic exists here, but nothing to resolve a Supply Set from").
NO_SUPPLY_SET_TEXT = "No Supply Set"
# Nomenclature cleanup (explicit request), renamed from the earlier
# "Macro - Floating": that name collided with the UNRELATED, pre-
# existing plain FLOATING_TEXT LINT-warning marker above (a signal with
# zero sinks anywhere -- see the top2.sv/sub21.sv/sub22.sv fixture's own
# sub22.o_sleep) even though the two concepts are structurally
# different: FLOATING_TEXT means "this signal has no destination node
# at all"; this marker means "this macro pin's own functional signal is
# perfectly connected -- what's missing is its PG-Type's own power-net
# connection (or, more broadly, ANY reason this macro pin's own Supply
# Set couldn't be resolved)." "Macro - No Supply Set" makes that
# distinction unambiguous, and is now the ONLY marker
# _resolve_macro_power_net_supply() ever returns for a macro pin that
# doesn't resolve to a real Supply Set -- covering all three of its own
# non-resolving branches uniformly (no related_power_pin/no PG-pin
# connector drawn/connected but no matching Supply Set composed yet),
# so the bare NO_SUPPLY_SET_TEXT above can never appear for a macro pin,
# closing the earlier "No Supply Set" bug report: that text wasn't
# actually wrong in concept, it was just unscoped -- shared between the
# RTL-only and macro-only cases under one literal string.
MACRO_NO_SUPPLY_SET_TEXT = "Macro - No Supply Set"
# Real macro PG-pin-connectivity golden fixture (sram.v/sram.lib with a
# VSS_INT internal-ground pg_pin) surfaced that a macro pin's own
# "facing the macro" resolution was a simple binary connected/not
# check against a SINGLE rail (related_power_pin only,
# related_ground_pin previously not even parsed) -- too coarse to
# express two real, distinct partially-connected states: the POWER
# rail is wired but the GROUND rail is left open (or vice versa). Each
# gets its own marker naming specifically WHICH rail is missing, rather
# than collapsing both into the generic "nothing at all is connected"
# MACRO_NO_SUPPLY_SET_TEXT above.
MACRO_GROUND_FLOATING_TEXT = "Macro - Ground Floating"
MACRO_POWER_FLOATING_TEXT = "Macro - Power Floating"
# The fourth distinct state the same fixture surfaced: BOTH rails of a
# pin's own PG-pin pair ARE connected (neither is floating), but they
# resolve to DIFFERENT Supply Sets -- e.g. the power rail lands on
# SS_VDD_SUB's own power net while the ground rail lands on SS_VDD's
# own ground net. A genuine wiring mistake in the design (crossed
# rails between two unrelated Supply Sets), not expressible as either
# a floating state or a normal resolved Supply Set name.
CONFLICT_SUPPLY_SET_TEXT = "Conflict - Supply Set"
# New display rule (explicit request): when a top-level port HAS a Port
# Attribute manually assigned, its boundary-facing cell shows this
# prefix (italic) ahead of the assigned Supply Set's own name -- e.g.
# "External - SS_VDD" -- distinguishing a MANUALLY-ASSERTED claim about
# what's outside the design boundary from a Supply Set name that came
# from genuine internal connectivity tracing (which stays a plain,
# unprefixed name, e.g. "SS_VDD_INT"). Deliberately the same "External"
# word already used by NO_PORT_ATTRIBUTE_ASSOCIATED_TEXT below -- both
# describe the same boundary-facing cell, just present-vs-absent.
EXTERNAL_PREFIX = "External - "
# A real macro PG-pin-connectivity golden fixture (sram.v/sram.lib with
# VPB/VNB well-bias pg_pins, pg_type nwell/pwell) surfaced a FIFTH,
# genuinely different state from all the above: the resolved Supply Set
# itself is perfectly fine (both rails agree, no Conflict) -- but that
# Supply Set's own declared Nwell/Pwell doesn't match what the LIB's
# own well-bias pin implies is expected (normally, Nwell riding on the
# same net as Power, Pwell on the same net as Ground). This is a VALID,
# if unusual, body-bias design choice (e.g. wells intentionally shorted
# to ground or to power) -- worth flagging for review, not blocking or
# treated as a resolution failure the way Conflict/Floating are. Suffix
# form (appended to the real, successfully-resolved Supply Set name,
# e.g. "SS_VDD - Bias Conflict"), not a standalone marker -- resolution
# still succeeds and shows the real name, this only adds a warning.
BIAS_CONFLICT_SUFFIX = " - Bias Conflict"


@dataclass(frozen=True)
class ResolvedSupplyDisplay:
    """One Port List cell's own resolved text -- `italic` marks every
    non-ordinary case (Undriven/Floating/Macro - No Supply Set/Macro -
    Ground Floating/Macro - Power Floating/Conflict - Supply Set/
    Combinational Loop/Multiple Drivers/No Primary Supply/No Supply
    Set/External - No Port Attribute Associated), matching the explicit "shown in
    italic" requirement for each of those; an ordinary resolved Supply
    Set name is the only plain case -- EXCEPT when a BIAS_CONFLICT_SUFFIX
    is appended to it (e.g. "SS_VDD - Bias Conflict"): the whole
    compound string is still shown in italic, matching every other
    multi-word marker here (there is no per-substring styling
    mechanism for THAT case -- QTableWidgetItem's own font applies to
    the whole cell, see hierarchy_view/properties_table_widget.py's own
    ConnectivityCellDisplay), even though the base Supply Set name
    portion did genuinely resolve.

    `italic_prefix_lens` is a SECOND, narrower styling mechanism (added
    for the EXTERNAL_PREFIX rule above): a per-LINE tuple, one entry per
    `text.split("\\n")` line, each entry the number of leading
    characters of THAT line rendered in italic while the rest of that
    line stays normal -- unlike `italic` above, which always styles the
    WHOLE cell (every line, in full). Mutually exclusive with
    `italic=True` in practice (never both set on the same display).
    Empty tuple (the default) means "no per-line prefix styling" --
    `hierarchy_view/properties_table_widget.py`'s own delegate falls
    back to `italic`/plain whole-cell rendering whenever this tuple is
    empty OR its length doesn't match the actual number of lines in
    `text` (e.g. a value built by hand without going through this
    dataclass's own construction sites below).

    Originally (single-bit Port Attribute only) this only ever needed
    to style ONE line -- extended to a genuine per-line tuple for
    bit-wise Port Attribute assignments (port_attribute_overlay.py's own
    port_attribute_row_keys()): a multi-bit top-level port whose own
    bits carry DIFFERENT Supply Set assignments now resolves to a
    multi-line, deduplicated "External - <name>" cell (one line per
    distinct assigned Supply Set, reusing the exact SAME "\\n".join()
    dedup convention _resolve_receiver()'s own multi-sink fanout
    already established) -- every one of those lines needs its own
    "External - " prefix in italic, not just the first."""

    text: str
    italic: bool = False
    italic_prefix_lens: tuple[int, ...] = ()


@dataclass
class StructuralTraceCache:
    """Layer 1 -- immutable once built (RTL structure never changes
    after a design loads)."""

    design: Design
    connectivity: FlattenedConnectivity
    node_by_path: dict[str, HierarchyNode] = field(default_factory=dict)


def build_structural_cache(design: Design) -> StructuralTraceCache:
    return StructuralTraceCache(design=design, connectivity=flatten_design(design), node_by_path={n.path: n for n in design.walk()})


def _macro_rail_candidates(
    endpoint: Endpoint,
    related_pin: str,
    supply_state: SupplyPortDiagramState,
    domain_state: PowerDomainDiagramState,
    field_name: str,
) -> tuple[bool, list[SupplySetPlacement]]:
    """One rail (power OR ground) of a macro pin's own PG-pin pair --
    `field_name` is "power" or "ground", selecting which SupplySetPlacement
    field must match the connected port's own name (a power rail must
    land on a Supply Set's own `.power`, a ground rail on its own
    `.ground` -- never cross-matched). Returns (connected, candidates):
    `connected` is True the moment a power-net connector is drawn to
    this rail's own pg-pin icon at all, regardless of whether any
    matching Supply Set is found. `candidates` is EVERY Supply Set
    matching this ONE rail alone -- deliberately not narrowed to a
    single "the" answer here, since a single rail may legally be shared
    by more than one Supply Set (e.g. two Supply Sets both using VSS as
    Ground but different Power -- see Cases 5/6 below); disambiguating
    which one is actually meant requires cross-checking the OTHER rail
    too, which only the caller (with both rails' own candidate lists in
    hand) can do."""
    pg_id = f"pg:{endpoint.path}:{related_pin}"
    connected = connected_supply_port(pg_id, supply_state)
    if connected is None:
        return False, []
    candidates = [
        supply_set
        for supply_set in domain_state.supply_sets.values()
        if getattr(supply_set, field_name) == connected.name and connected.owner_path in supply_set.owner_paths
    ]
    return True, candidates


def _macro_well_bias_conflict(
    well_bias_pins: list[WellBiasPin],
    related_power_pin: Optional[str],
    related_ground_pin: Optional[str],
    resolved: SupplySetPlacement,
) -> bool:
    """True when the LIB declares a well-bias pg_pin (VPB/VNB-style)
    tied to one of THIS pin's own connected rails, AND `resolved`'s own
    declared Nwell/Pwell doesn't match what that well pin implies is
    expected. Deliberately NOT hardcoded to "nwell always checks
    power, pwell always checks ground" -- each WellBiasPin's own
    `related_bias_pin` is matched against WHICHEVER of
    related_power_pin/related_ground_pin it actually equals (per the
    explicit scoping requirement: this follows whatever the LIB itself
    declares, not an assumed convention), then the correspondingly-typed
    field (n_well for pg_type "nwell", p_well for "pwell") is the one
    compared. A well pin whose own `related_bias_pin` matches NEITHER
    rail (e.g. VDD_INT/VSS_INT in the sram.v/sram.lib fixture, which has
    no well association at all) contributes nothing -- no check, no
    annotation, matching the explicit "skip this check entirely" scoping
    rule."""
    for well_bias_pin in well_bias_pins:
        if well_bias_pin.related_bias_pin not in (related_power_pin, related_ground_pin):
            continue
        if well_bias_pin.pg_type == "nwell" and resolved.n_well != well_bias_pin.related_bias_pin:
            return True
        if well_bias_pin.pg_type == "pwell" and resolved.p_well != well_bias_pin.related_bias_pin:
            return True
    return False


def _macro_exposed_well_bias_conflict(
    endpoint: Endpoint,
    macro_definition: Definition,
    related_power_pin: Optional[str],
    related_ground_pin: Optional[str],
    resolved: SupplySetPlacement,
    supply_state: SupplyPortDiagramState,
    domain_state: PowerDomainDiagramState,
) -> bool:
    """True when this functional pin resolves via the macro's own
    PRIMARY (non-internal) PG-pin pair -- both related_power_pin and
    related_ground_pin name pins whose own Port.direction is
    PortDirection.PG, derived operationally (no primary-vs-backup
    pg_type distinction is retained anywhere in the schema, and none is
    needed by any real fixture today -- both primary_power/backup_power
    already collapse to the same PortDirection.PG) rather than
    hardcoded to any specific pin name -- AND an EXPOSED well pin
    (PortDirection.PG_WELL, e.g. VPW) on the SAME macro is connected to
    a Supply Set that DIFFERS from `resolved`.

    Confirmed inferred rule (explicit request: confirm against the
    codebase before implementing): with no related_bias_pin stated in
    the LIB at all for an exposed well pin, the DEFAULT expectation is
    that it rides on the SAME Supply Set as the macro's own primary PG
    domain. This is directly grounded in CreateSupplySetDialog's own
    existing validation -- N-Well/P-Well must always equal either a
    Supply Set's own Power or Ground name, never an independent third
    net -- so "does the well's connected port belong to the SAME Supply
    Set" reduces to matching EITHER field, exactly like the ORIGINAL
    (pre-per-rail-split) macro resolution already did, rather than
    requiring a specific field match the way the IMPLICIT well-pin
    check above does. Only checks DOMAIN-level consistency -- which
    SPECIFIC Supply Port within that Supply Set the well is wired to
    (the same physical port as VDD/VSS, or a distinct one) is a design
    choice this function deliberately never second-guesses (explicit
    scope note -- only the tool's macro design owner can validate
    that)."""
    primary_pg_names = {p.name for p in macro_definition.ports if p.direction == PortDirection.PG}
    if related_power_pin not in primary_pg_names or related_ground_pin not in primary_pg_names:
        return False
    for well_port in macro_definition.ports:
        if well_port.direction != PortDirection.PG_WELL:
            continue
        pg_id = f"pg:{endpoint.path}:{well_port.name}"
        connected = connected_supply_port(pg_id, supply_state)
        if connected is None:
            continue
        well_supply_set = next(
            (
                s
                for s in domain_state.supply_sets.values()
                if (s.power == connected.name or s.ground == connected.name) and connected.owner_path in s.owner_paths
            ),
            None,
        )
        if well_supply_set is not None and well_supply_set.id != resolved.id:
            return True
    return False


def _macro_resolved_display(
    endpoint: Endpoint,
    resolved: SupplySetPlacement,
    related_power_pin: Optional[str],
    related_ground_pin: Optional[str],
    macro_definition: Definition,
    supply_state: SupplyPortDiagramState,
    domain_state: PowerDomainDiagramState,
) -> ResolvedSupplyDisplay:
    """The final step once a macro pin's own Supply Set has genuinely
    resolved (via either the full-pair match or the single-rail
    fallback in _resolve_macro_power_net_supply() below) -- appends
    BIAS_CONFLICT_SUFFIX (italic, whole compound string) when EITHER
    _macro_well_bias_conflict() (an IMPLICIT well pin, e.g. VPB) OR
    _macro_exposed_well_bias_conflict() (an EXPOSED one, e.g. VPW)
    finds a mismatch, otherwise the plain resolved name (not italic),
    unchanged from before this feature."""
    conflict = _macro_well_bias_conflict(
        macro_definition.well_bias_pins, related_power_pin, related_ground_pin, resolved
    ) or _macro_exposed_well_bias_conflict(endpoint, macro_definition, related_power_pin, related_ground_pin, resolved, supply_state, domain_state)
    if conflict:
        return ResolvedSupplyDisplay(f"{resolved.name}{BIAS_CONFLICT_SUFFIX}", italic=True)
    return ResolvedSupplyDisplay(resolved.name)


def _resolve_macro_power_net_supply(
    endpoint: Endpoint,
    related_power_pin: Optional[str],
    related_ground_pin: Optional[str],
    supply_state: Optional[SupplyPortDiagramState],
    domain_state: Optional[PowerDomainDiagramState],
    macro_definition: Definition,
) -> ResolvedSupplyDisplay:
    """The "facing the macro" side of a macro's own functional pin --
    explicit requirement: authoritatively resolved via the diagram's own
    REAL power-net connectivity, not just a name-coincidence match
    against the raw LIB pg-pin string(s). A macro's own pg-pin icon is a
    FIXED SupplyPortPlacement (id `f"pg:{macro_path}:{pin_name}"`, see
    build_supply_port_overlay()) -- `related_power_pin`/`related_ground_pin`
    alone (e.g. "VDD"/"VSS") are not themselves Supply Set names; the
    actual answer is whichever Supply Port/Internal Supply the user has
    drawn a power-net connector from that pg-pin icon TO
    (connected_supply_port(), supply_port_overlay.py -- the same core
    the Supply Ports tab's own "Connected" column already uses), and
    then whichever Supply Set references THAT connected port's own name
    on its own Power (for the power rail) or Ground (for the ground
    rail) field, scoped to the CONNECTED port's own owner_path.

    A macro pin's own PG-pin pair (e.g. VDD_INT/VSS_INT) is validated
    per-rail via _macro_rail_candidates() -- reopened after a real
    golden fixture (sram.v/sram.lib, a VSS_INT internal-ground pg_pin)
    found the earlier power-only check couldn't express two real,
    distinct partially-connected states. Five possible outcomes:
      1. Neither rail declared in the LIB at all -- "Macro - No Supply
         Set".
      2. Both rails declared but NEITHER has a power-net connector
         drawn -- "Macro - No Supply Set" (the pre-existing, unchanged
         fully-unconnected case).
      3. Power rail connected, ground rail not (declared but open) --
         "Macro - Ground Floating" -- the GROUND side is what's
         missing.
      4. Ground rail connected, power rail not -- "Macro - Power
         Floating" -- the POWER side is what's missing (the mirror of
         #3).
      5. BOTH rails connected: resolved by finding the ONE Supply Set
         present in BOTH rails' own candidate lists (i.e. whose OWN
         Power matches the connected power rail AND whose OWN Ground
         matches the connected ground rail simultaneously) -- normal
         resolution, that Supply Set's own name. If no single Supply
         Set satisfies both simultaneously, but each rail still
         resolves to SOME (different) Supply Set on its own,
         "Conflict - Supply Set" (a genuine cross-domain wiring
         mistake). If either connected rail resolves to no Supply Set
         at all, "Macro - No Supply Set" (nothing conclusive to
         report).
    A rail whose own related_*_pin was never declared by the LIB at all
    is treated as inapplicable (not "floating") -- only ever relevant
    when just one of the pair is declared, which no real fixture
    exercises today but is handled without guessing.

    Real, confirmed bug found via Cases 5/6's own golden fixture (two
    Supply Sets legally sharing exactly ONE rail, e.g. both using VSS as
    Ground but different Power -- explicitly legal per the pin-
    combination-uniqueness validation's own "partial overlap stays
    legal" rule): an earlier version of this function resolved each
    rail to a SINGLE Supply Set independently (picking whichever
    candidate happened to be first in dict-iteration order when a rail
    was legally shared by more than one), then compared power's answer
    against ground's answer -- which spuriously reported "Conflict -
    Supply Set" whenever the shared rail's own arbitrary first-match
    didn't happen to agree with the OTHER, uniquely-resolving rail's own
    answer. Fixed by resolving the PAIR as a unit: collect every
    candidate for EACH rail independently (a rail can have zero, one, or
    several), then look for the Supply Set appearing in BOTH lists at
    once -- there is always exactly one once the pin-combination
    uniqueness rule is enforced at Supply Set creation time (no two
    Supply Sets can share BOTH rails, only ever at most one), so a
    full-pair match is unambiguous whenever it exists; only a genuine
    absence of any full-pair match (each rail resolves, but to two
    Supply Sets that don't agree with each other) is a real Conflict.

    Deliberately NEVER the bare NO_SUPPLY_SET_TEXT (that marker is
    reserved for the RTL-only "real logic found but its hierarchy has no
    Power Domain" case in _resolve_ordinary_endpoint() below -- the two
    were previously conflated under one literal string, which was the
    actual root cause of an earlier "No Supply Set" bug report), nor the
    plain FLOATING_TEXT a genuinely zero-sink signal gets. Applies
    uniformly regardless of which specific PG-pin pair (main VDD/VSS vs.
    an internal VDD_INT/VSS_INT, etc.) is involved -- confirmed against
    sram.lib's own POWER_GOOD (VDD/VSS) vs. Q (VDD_INT/VSS_INT) pins.

    Once a Supply Set genuinely resolves (either the full-pair match
    above, or the single-rail fallback below), a SIXTH check runs:
    _macro_well_bias_conflict() -- a real, distinct warning-only
    annotation (BIAS_CONFLICT_SUFFIX), not a resolution failure, for
    when the resolved Supply Set's own Nwell/Pwell doesn't match what
    the LIB's own well-bias pg_pin (VPB/VNB-style) implies is expected.
    See that helper's own docstring for the full "why" -- this only
    ever fires for a PG-pin domain the LIB actually associates a well
    pin with (never for VDD_INT/VSS_INT in the sram.v/sram.lib fixture,
    which has no well association at all)."""
    if supply_state is None or domain_state is None or (related_power_pin is None and related_ground_pin is None):
        return ResolvedSupplyDisplay(MACRO_NO_SUPPLY_SET_TEXT, italic=True)

    power_connected, power_candidates = (
        _macro_rail_candidates(endpoint, related_power_pin, supply_state, domain_state, "power") if related_power_pin is not None else (None, [])
    )
    ground_connected, ground_candidates = (
        _macro_rail_candidates(endpoint, related_ground_pin, supply_state, domain_state, "ground") if related_ground_pin is not None else (None, [])
    )

    if related_power_pin is not None and related_ground_pin is not None:
        if not power_connected and not ground_connected:
            return ResolvedSupplyDisplay(MACRO_NO_SUPPLY_SET_TEXT, italic=True)
        if power_connected and not ground_connected:
            return ResolvedSupplyDisplay(MACRO_GROUND_FLOATING_TEXT, italic=True)
        if ground_connected and not power_connected:
            return ResolvedSupplyDisplay(MACRO_POWER_FLOATING_TEXT, italic=True)
        # Both connected -- resolve the PAIR as a unit, not each rail
        # independently (see this function's own docstring for the
        # real bug this fixes).
        power_ids = {s.id for s in power_candidates}
        full_match = next((s for s in ground_candidates if s.id in power_ids), None)
        if full_match is not None:
            return _macro_resolved_display(endpoint, full_match, related_power_pin, related_ground_pin, macro_definition, supply_state, domain_state)
        if power_candidates and ground_candidates:
            return ResolvedSupplyDisplay(CONFLICT_SUPPLY_SET_TEXT, italic=True)
        return ResolvedSupplyDisplay(MACRO_NO_SUPPLY_SET_TEXT, italic=True)

    # Only one of the pair was ever declared by the LIB -- validate that
    # single rail alone, same shape as the pre-existing power-only check.
    connected, candidates = (power_connected, power_candidates) if related_power_pin is not None else (ground_connected, ground_candidates)
    if not connected or not candidates:
        return ResolvedSupplyDisplay(MACRO_NO_SUPPLY_SET_TEXT, italic=True)
    return _macro_resolved_display(endpoint, candidates[0], related_power_pin, related_ground_pin, macro_definition, supply_state, domain_state)


def _macro_port_facing_itself(
    structural: StructuralTraceCache, endpoint: Endpoint
) -> Optional[tuple[PortDirection, Optional[str], Optional[str], Definition]]:
    """None unless `endpoint` names a black-box Definition's own
    FUNCTIONAL (non-PG) port -- in which case (direction,
    related_power_pin, related_ground_pin, definition) for that exact
    port -- `definition` is the OWNING macro's own full Definition (not
    scoped to this one port), since both well_bias_pins (implicit well
    pins) and any exposed well pin (PortDirection.PG_WELL) are metadata
    on the CELL, not on any one functional pin --
    _resolve_macro_power_net_supply() itself does the per-rail matching
    for both. A macro's own INPUT pin can only ever be reached here as a
    RECEIVER-side terminal endpoint (see connectivity_flatten.py's own
    is_black_box handling: driver-tracing always ascends PAST an input
    pin to find the real external driver, never stopping here) --
    symmetrically an OUTPUT pin can only ever be a DRIVER-side terminal
    endpoint. So the caller doesn't need to know whether it's resolving
    a driver or a receiver: this function alone determines whether the
    LIB rule applies."""
    node = structural.node_by_path.get(endpoint.path)
    if node is None or not node.definition.is_black_box:
        return None
    port = next((p for p in node.definition.ports if p.name == endpoint.signal), None)
    if port is None or port.direction not in (PortDirection.IN, PortDirection.OUT):
        return None
    return port.direction, port.related_power_pin, port.related_ground_pin, node.definition


def _is_top_level_port(structural: StructuralTraceCache, endpoint: Endpoint) -> bool:
    """True iff `endpoint` names one of the design's own root
    Definition's declared ports -- extracted out of
    _resolve_ordinary_endpoint() below (see its own extensive comment
    for the two real, confirmed bugs fixing this exact check already
    surfaced) so _resolve_receiver() can also consult it directly (see
    that function's own docstring for why)."""
    node = structural.node_by_path.get(endpoint.path)
    return node is not None and node.instance is None and any(p.name == endpoint.signal for p in node.definition.ports)


def _resolve_ordinary_endpoint(
    structural: StructuralTraceCache,
    endpoint: Endpoint,
    supply_state: Optional[SupplyPortDiagramState],
    domain_state: Optional[PowerDomainDiagramState],
    port_attribute_state: Optional[PortAttributeDiagramState],
    is_driver_side: bool,
) -> ResolvedSupplyDisplay:
    """A single concrete structural Endpoint -> its own Supply Set
    display text."""
    # Real, confirmed bug (found via a real elaborated design loaded
    # through the validation harness -- golden_rtl-style hand-built test
    # fixtures never caught this because they happened to set
    # `definition_id="top"` literally): `design.top` is a DEFINITION ID
    # (make_definition_id() ALWAYS appends a hash suffix, e.g.
    # "top__42988143", even with zero parameters), never equal to
    # `endpoint.path` (a hierarchy PATH string, built from the root
    # Definition's own base_name, e.g. "top" -- see Design.walk()).
    # Comparing the two directly meant this entire top-level-boundary
    # branch (Port Attribute lookup / "No Port Attribute Associated") was
    # silently dead code for every real elaborated design -- every
    # top-level port's own boundary-facing side fell through to the
    # ordinary Power-Domain lookup below instead. Fixed by using the
    # SAME structural "is this the design's own root" test the trace
    # engine itself already relies on everywhere else: a HierarchyNode's
    # own `.instance` is None if and only if it's the root (every
    # descendant is given a real Instance by Design.walk()'s own
    # recursive definition).
    #
    # A second, real, confirmed bug found fixing the first one (a real
    # design's own top-level `always_ff` surfaced it): checking the PATH
    # alone isn't enough -- an INTERNAL net/variable declared directly
    # inside the top module (e.g. a flop's own `logic q_int;`, now also
    # a legitimate receiver via the sequential-element fix above) sits at
    # this exact same root path too, but is NOT a boundary port a Port
    # Attribute could ever be assigned to -- Port Attributes are
    # explicitly a per-PORT concept. Requiring `endpoint.signal` to
    # actually be one of the root Definition's own declared ports (not
    # just any signal living at the root path) keeps this branch scoped
    # to real boundary ports only, matching its original intent.
    node = structural.node_by_path.get(endpoint.path)
    is_top_level_port = _is_top_level_port(structural, endpoint)
    # Real, confirmed bug (golden fixture top1.sv/sub1.sv, a deliberate
    # variant of the pass-through-vs-real-logic fixture with a SECOND
    # inversion stage living directly in the PARENT/root module itself --
    # `assign o_sleep = ~sleep_int;` in top1, fed by sub1's own o_sleep
    # output): `is_top_level_port` alone can't tell apart two genuinely
    # different situations that share the exact same Endpoint identity --
    # (1) this signal genuinely IS the design's own untraceable external
    # boundary, vs. (2) this signal's NAME happens to collide with a
    # root-level port name, but this specific Endpoint was actually
    # reached as the OUTPUT of real logic (a continuous assign with
    # source=None) living at the design's own root. Verilog structurally
    # forbids assigning to an INPUT port, so case (2) can only ever arise
    # on the DRIVER side (an OUTPUT-direction port); the receiver side
    # has no equivalent ambiguity and must keep using the plain
    # boundary-port rule unconditionally. When both hold -- driver-side
    # AND a real-logic assign targets this exact signal at this exact
    # path -- fall through to the ordinary Power-Domain lookup below
    # instead of reporting the top-level-boundary case, so the real
    # logic's own owning domain (here, PD_COR/SS_VDD) wins.
    real_logic_drives_here = is_driver_side and node is not None and any(ca.target == endpoint.signal and ca.source is None for ca in node.definition.continuous_assigns)
    if is_top_level_port and not real_logic_drives_here:
        # Bit-wise Port Attribute assignments (port_attribute_row_keys()):
        # a multi-bit port's own bits can each carry a DIFFERENT Supply
        # Set -- collect every DISTINCT resolved name across every bit
        # this port owns (dedup by name, first-seen order), then render
        # one "External - <name>" line per distinct value, all deduped
        # exactly like _resolve_receiver()'s own multi-sink fanout join
        # below. A single-bit port (or a multi-bit port whose bits all
        # happen to agree) still collapses to the original one-line
        # shape -- no behavior change for that case.
        port_obj = next((p for p in node.definition.ports if p.name == endpoint.signal), None) if node is not None else None
        resolved_names: dict[str, str] = {}
        if port_attribute_state is not None and domain_state is not None and port_obj is not None:
            for key in port_attribute_row_keys(port_obj):
                assigned = port_attribute_state.assignments.get(key)
                if not assigned:
                    continue
                supply_set = domain_state.supply_sets.get(assigned)
                if supply_set is not None:
                    resolved_names.setdefault(supply_set.name, supply_set.name)
        if resolved_names:
            lines = [f"{EXTERNAL_PREFIX}{name}" for name in resolved_names.values()]
            return ResolvedSupplyDisplay("\n".join(lines), italic_prefix_lens=tuple(len(EXTERNAL_PREFIX) for _ in lines))
        return ResolvedSupplyDisplay(NO_PORT_ATTRIBUTE_ASSOCIATED_TEXT, italic=True)

    macro_pin = _macro_port_facing_itself(structural, endpoint)
    if macro_pin is not None:
        _direction, related_power_pin, related_ground_pin, macro_definition = macro_pin
        return _resolve_macro_power_net_supply(endpoint, related_power_pin, related_ground_pin, supply_state, domain_state, macro_definition)

    domain = find_effective_domain_for_path(domain_state.domains, endpoint.path) if domain_state is not None else None
    if domain is None:
        # Nomenclature cleanup (explicit request): real RTL logic was
        # genuinely found here (this is NOT Undriven/Floating) -- it's
        # just that this hierarchy has no Power Domain assigned at all,
        # so there's no Supply Set to report. See NO_SUPPLY_SET_TEXT's
        # own module-level docstring for the full "why".
        return ResolvedSupplyDisplay(NO_SUPPLY_SET_TEXT, italic=True)
    if not domain.primary_supply_set:
        return ResolvedSupplyDisplay(NO_PRIMARY_SUPPLY_TEXT, italic=True)
    supply_set = domain_state.supply_sets.get(domain.primary_supply_set) if domain_state is not None else None
    if supply_set is None:
        return ResolvedSupplyDisplay(NO_PRIMARY_SUPPLY_TEXT, italic=True)
    return ResolvedSupplyDisplay(supply_set.name)


def _join_displays(displays: list[ResolvedSupplyDisplay]) -> ResolvedSupplyDisplay:
    """Combines several already-resolved, single-endpoint displays into
    one "\\n".join()'d multi-line cell -- the general multi-sink dedup
    shape _resolve_receiver() has always built, generalized here to also
    preserve each contributing display's own per-line italic styling
    (not just a single whole-cell bool) once it started being possible
    for ONE of the joined displays to itself already be multi-line and
    per-line-prefix-styled (a multi-bit top-level port whose own bits
    disagree on Supply Set, see _resolve_ordinary_endpoint()'s own
    boundary-case comment). A contributing display with no per-line
    data of its own (`italic_prefix_lens` empty, or mismatched against
    its own line count -- i.e. every PRE-EXISTING marker in this module)
    degrades cleanly: `italic=True` becomes "the whole line is italic"
    (prefix length == the full line), `italic=False` becomes "no italic
    at all" (prefix length 0) -- both exactly reproduce the plain
    whole-cell-QFont rendering these markers already had, just expressed
    through the same per-line mechanism instead of a second, parallel
    code path."""
    lines: list[str] = []
    prefix_lens: list[int] = []
    for display in displays:
        display_lines = display.text.split("\n")
        if len(display.italic_prefix_lens) == len(display_lines):
            display_lens = list(display.italic_prefix_lens)
        elif display.italic:
            display_lens = [len(line) for line in display_lines]
        else:
            display_lens = [0] * len(display_lines)
        lines.extend(display_lines)
        prefix_lens.extend(display_lens)
    return ResolvedSupplyDisplay("\n".join(lines), italic=any(d.italic for d in displays), italic_prefix_lens=tuple(prefix_lens))


def _resolve_driver(
    structural: StructuralTraceCache,
    result: Optional[DriverResult],
    supply_state: Optional[SupplyPortDiagramState],
    domain_state: Optional[PowerDomainDiagramState],
    port_attribute_state: Optional[PortAttributeDiagramState],
) -> ResolvedSupplyDisplay:
    if result is None or result.kind == TraceKind.UNDRIVEN:
        return ResolvedSupplyDisplay(UNDRIVEN_TEXT, italic=True)
    if result.kind == TraceKind.LOOP:
        return ResolvedSupplyDisplay(LOOP_TEXT, italic=True)
    if result.kind == TraceKind.MULTIPLE_DRIVERS:
        return ResolvedSupplyDisplay(MULTIPLE_DRIVERS_TEXT, italic=True)
    assert result.endpoint is not None
    return _resolve_ordinary_endpoint(structural, result.endpoint, supply_state, domain_state, port_attribute_state, is_driver_side=True)


def _resolve_receiver(
    structural: StructuralTraceCache,
    queried_endpoint: Endpoint,
    result: Optional[ReceiverResult],
    supply_state: Optional[SupplyPortDiagramState],
    domain_state: Optional[PowerDomainDiagramState],
    port_attribute_state: Optional[PortAttributeDiagramState],
) -> ResolvedSupplyDisplay:
    """`queried_endpoint` is the port whose OWN Receiver cell this is.

    Real, confirmed bug found via the FIRST implementation attempt of
    the fix described below: short-circuiting to Port-Attribute-only
    for EVERY top-level port's own Receiver query (regardless of
    `result.endpoints`) broke every INPUT top-level port's own
    Receiver -- an INPUT port's Receiver legitimately means "what does
    this signal feed INSIDE the design" (e.g. `top.a` fanning out to
    two internal sinks, test_multiple_sinks_resolving_to_the_same_supply_set_are_deduplicated),
    which is exactly the general multi-sink trace, not a boundary
    check -- confirmed empirically (23 tests failed) before narrowing
    the condition to what's below.

    The real, correct condition (golden fixture simple8: a macro pin
    driving BOTH the design's own top-level boundary port AND real
    internal RTL simultaneously, e.g. sram.q <- u_sram.Q -> both sram's
    own `q` output AND u_sub.i_q): `queried_endpoint` itself must be
    BOTH a top-level port AND literally present as one of the entries
    in `result.endpoints` -- i.e. the structural trace found that this
    exact port's own identity is among its own returned receivers (the
    "reached my own external boundary" self-reference), not merely
    that it happens to be a top-level port in general. Only THEN does
    the generic dedup/join loop below risk combining that
    self-reference with an unrelated sibling sink's own resolution into
    one wrong multi-line cell (e.g. "External - No Port Attribute
    Associated\nSS_VDD_SUB") -- correct for the OTHER endpoint's OWN
    row (a macro pin is a genuine graph node, its Receiver cell must
    show the FULL fanout) but wrong for the top-level port's OWN row,
    which must strictly reflect Port Attribute (or its absence) and
    never report what else the same underlying net does inside the
    design."""
    if result is None or result.kind == TraceKind.FLOATING:
        return ResolvedSupplyDisplay(FLOATING_TEXT, italic=True)
    if result.kind == TraceKind.LOOP:
        return ResolvedSupplyDisplay(LOOP_TEXT, italic=True)
    if queried_endpoint in result.endpoints and _is_top_level_port(structural, queried_endpoint):
        return _resolve_ordinary_endpoint(structural, queried_endpoint, supply_state, domain_state, port_attribute_state, is_driver_side=False)
    # RESOLVED -- plural by construction; dedupe by resolved DISPLAY TEXT
    # (not by raw Endpoint -- two different sink endpoints legitimately
    # resolving to the same Supply Set collapse to one line), preserving
    # first-seen order.
    seen: dict[str, ResolvedSupplyDisplay] = {}
    for endpoint in result.endpoints:
        display = _resolve_ordinary_endpoint(structural, endpoint, supply_state, domain_state, port_attribute_state, is_driver_side=False)
        seen.setdefault(display.text, display)
    displays = list(seen.values())
    if not displays:
        return ResolvedSupplyDisplay(FLOATING_TEXT, italic=True)
    if len(displays) == 1:
        return displays[0]
    return _join_displays(displays)


@dataclass
class PowerResolutionCache:
    """Layer 2 -- invalidated wholesale on any Power-Domain/Supply-Set/
    Port-Attribute edit (see module docstring). Deliberately a full
    clear rather than fine-grained per-port invalidation: recompute is
    cheap (Layer 1's own expensive trace is untouched), so there is no
    correctness/perf reason to track which specific ports a given edit
    could have affected."""

    _driver_display: dict[Endpoint, ResolvedSupplyDisplay] = field(default_factory=dict)
    _receiver_display: dict[Endpoint, ResolvedSupplyDisplay] = field(default_factory=dict)

    def invalidate_all(self) -> None:
        self._driver_display.clear()
        self._receiver_display.clear()

    def driver_display(
        self,
        endpoint: Endpoint,
        structural: StructuralTraceCache,
        supply_state: Optional[SupplyPortDiagramState],
        domain_state: Optional[PowerDomainDiagramState],
        port_attribute_state: Optional[PortAttributeDiagramState],
    ) -> ResolvedSupplyDisplay:
        if endpoint not in self._driver_display:
            result = structural.connectivity.drivers.get(endpoint)
            self._driver_display[endpoint] = _resolve_driver(structural, result, supply_state, domain_state, port_attribute_state)
        return self._driver_display[endpoint]

    def receiver_display(
        self,
        endpoint: Endpoint,
        structural: StructuralTraceCache,
        supply_state: Optional[SupplyPortDiagramState],
        domain_state: Optional[PowerDomainDiagramState],
        port_attribute_state: Optional[PortAttributeDiagramState],
    ) -> ResolvedSupplyDisplay:
        if endpoint not in self._receiver_display:
            result = structural.connectivity.receivers.get(endpoint)
            self._receiver_display[endpoint] = _resolve_receiver(structural, endpoint, result, supply_state, domain_state, port_attribute_state)
        return self._receiver_display[endpoint]


def resolve_all(
    structural: StructuralTraceCache,
    cache: PowerResolutionCache,
    supply_state: Optional[SupplyPortDiagramState],
    domain_state: Optional[PowerDomainDiagramState],
    port_attribute_state: Optional[PortAttributeDiagramState],
) -> tuple[dict[tuple[str, str], ResolvedSupplyDisplay], dict[tuple[str, str], ResolvedSupplyDisplay]]:
    """Eagerly resolves Layer 2 for every port Layer 1 already knows
    about, keyed by the plain (path, signal) tuple hierarchy_view/
    properties_table_widget.py itself understands (its own
    ConnectivityCellDisplay) -- that module stays completely power-
    intent-agnostic (Front-End Milestone 1's own directory must not
    import Milestone 2/3 concepts), so this is the boundary where
    Endpoint/ResolvedSupplyDisplay get flattened into plain data before
    crossing into it. Returns (receiver_by_key, driver_by_key)."""
    receiver_by_key = {}
    driver_by_key = {}
    for endpoint in structural.connectivity.drivers:
        driver_by_key[(endpoint.path, endpoint.signal)] = cache.driver_display(endpoint, structural, supply_state, domain_state, port_attribute_state)
    for endpoint in structural.connectivity.receivers:
        receiver_by_key[(endpoint.path, endpoint.signal)] = cache.receiver_display(
            endpoint, structural, supply_state, domain_state, port_attribute_state
        )
    return receiver_by_key, driver_by_key
