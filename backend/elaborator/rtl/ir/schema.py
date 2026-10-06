"""Unified, language-agnostic RTL hierarchy/netlist IR (Milestone 1c).

Both the Verilog/SystemVerilog front-end (1a) and the VHDL front-end (1b)
normalize into these types. Nothing here is aware of source syntax; widths,
parameter values, and reset polarity are all post-elaboration/resolved.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class PortDirection(Enum):
    IN = "in"
    OUT = "out"
    INOUT = "inout"
    PG = "pg"  # black-box (.lib) power/ground pin -- see PG_INTERNAL below
    PG_INTERNAL = "pg_internal"  # black-box (.lib) internal power/ground pin
    PG_WELL = "pg_well"  # black-box (.lib) EXPOSED well-bias pin -- see below

    # PG/PG_INTERNAL only ever appear on a black-box (is_black_box=True)
    # Definition's ports, built from a Liberty cell's `pg_pin` groups (see
    # backend/elaborator/lib/elaborate.py's _build_pg_pin_port) -- real
    # RTL front-ends (verilog/, vhdl/) never produce them; a module's
    # actual power/ground connectivity isn't RTL-visible the way a .lib
    # cell's supply pins are declared.
    #
    # PG_WELL (real macro PG-pin-connectivity golden fixture, sram.v/
    # sram.lib with an EXPOSED VPW pwell pin): a well-type pg_pin
    # (nwell/pwell) WITHOUT a related_bias_pin -- unlike an IMPLICIT
    # well pin (e.g. VPB, WITH a related_bias_pin, see WellBiasPin's own
    # docstring), the LIB states no expected net for this one at all; it
    # is a genuine, independently connectable macro pin the user must
    # wire explicitly, exactly like PG/PG_INTERNAL. Deliberately its OWN
    # PortDirection value, not reusing PG -- power_resolution_cache.py's
    # own default-domain bias-conflict check for an exposed well pin
    # needs to find it specifically, distinct from an ordinary primary
    # power/ground pin.


class SourceLanguage(Enum):
    VERILOG = "verilog"
    SYSTEMVERILOG = "systemverilog"
    VHDL = "vhdl"
    LIBERTY = "liberty"  # .lib black-box macro (Milestone 1e) -- not written in any HDL


class RangeDirection(Enum):
    ASCENDING = "ascending"    # VHDL `to`
    DESCENDING = "descending"  # Verilog `[msb:lsb]`, VHDL `downto`


class Edge(Enum):
    RISING = "rising"
    FALLING = "falling"


class ResetKind(Enum):
    NONE = "none"
    SYNC = "sync"
    ASYNC = "async"


@dataclass(frozen=True)
class SourceLoc:
    file: str
    line: int
    column: Optional[int] = None


@dataclass(frozen=True)
class BitRange:
    """Declared bit range, preserved exactly (not normalized to zero-based)."""

    left: int
    right: int

    @property
    def width(self) -> int:
        return abs(self.left - self.right) + 1

    @property
    def direction(self) -> RangeDirection:
        return (
            RangeDirection.DESCENDING
            if self.left >= self.right
            else RangeDirection.ASCENDING
        )


@dataclass(frozen=True)
class Port:
    name: str
    direction: PortDirection
    bit_range: Optional[BitRange] = None  # None => scalar, no declared range
    signed: bool = False
    source_mode: Optional[str] = None  # raw VHDL mode when not a 1:1 map (e.g. "buffer", "linkage")
    source_loc: Optional[SourceLoc] = None
    # A PG (or PG_INTERNAL) pin's own nominal voltage, resolved from a
    # Liberty .lib pg_pin's `voltage_name` through the library's own
    # `voltage_map (name, value)` entries (backend.elaborator.lib.elaborate),
    # formatted e.g. "0.80V". None for every non-PG port, and for a PG port
    # whose voltage_name has no matching voltage_map entry (not guessed at).
    voltage: Optional[str] = None
    # A FUNCTIONAL (non-PG) Liberty pin's own `related_power_pin` --
    # which pg_pin group on the SAME cell actually powers this pin (e.g.
    # `pin(CE) { related_power_pin: VDD; }`). Found via real `aura`
    # design use (backend/elaborator/rtl/ir/flatten.py's own Port List
    # Driver/Receiver Supply Set columns): a macro pin's own "facing the
    # macro" side must be authoritatively LIB-sourced, not guessed at via
    # which Power Domain happens to own the macro's own hierarchy path
    # (a macro can have multiple PG rails, e.g. VDD_CORE vs VDD_IO, and a
    # given functional pin is only really powered by ONE of them).
    # None for a PG/PG_INTERNAL port itself (a power pin has no "related"
    # power pin of its own), and for a functional pin whose own Liberty
    # `pin()` group never declared `related_power_pin` at all (not
    # guessed at).
    related_power_pin: Optional[str] = None
    # The GROUND-side counterpart of related_power_pin above -- a
    # functional pin's own `related_ground_pin` (e.g.
    # `pin(CE) { related_ground_pin: VSS; }`). Previously deliberately
    # NOT parsed (Supply Set resolution was power-anchored only) --
    # reopened after a real macro PG-pin-connectivity golden test
    # (sram.v/sram.lib, a VSS_INT internal-ground pg_pin) needed to
    # independently validate BOTH rails of a PG-pin pair, not just the
    # power one: a macro pin whose power rail is wired but whose ground
    # rail is left open (or vice versa) is a real, distinct
    # partially-connected state ("Macro - Ground Floating"/"Macro -
    # Power Floating"), and two rails wired to DIFFERENT Supply Sets is
    # a real, distinct conflict state ("Conflict - Supply Set") --
    # neither is expressible from related_power_pin alone. None for a
    # PG/PG_INTERNAL port itself, and for a functional pin whose own
    # Liberty `pin()` group never declared `related_ground_pin` at all.
    related_ground_pin: Optional[str] = None

    @property
    def width(self) -> int:
        return self.bit_range.width if self.bit_range is not None else 1


@dataclass(frozen=True)
class Net:
    name: str
    bit_range: Optional[BitRange] = None
    signed: bool = False
    source_loc: Optional[SourceLoc] = None

    @property
    def width(self) -> int:
        return self.bit_range.width if self.bit_range is not None else 1


@dataclass(frozen=True)
class SequentialElement:
    """A single flop or latch. Scope: identity + clock/reset/set/data refs.

    General next-state EXPRESSION modeling (what a register is computed
    from when that computation is more than a single bare signal --
    an operator, a mux, a case-selected value) remains out of scope for
    Milestone 1, same as ever. `data_ref` (reversed from an earlier,
    stricter "no data-path modeling at all" decision -- found blocking
    correct Driver/Receiver Supply Set results on a real design, where
    nothing recorded that a flop's own D input was a real receiver of
    its driving signal) is narrower than that: it's the SAME "is the RHS
    a bare, same-width single-signal reference" pass-through test
    ContinuousAssign.source already applies to a plain `assign`, applied
    here to a sequential element's own next-state assignment(s) instead
    (scoped to the "not currently resetting" branch only -- a reset
    branch's own constant/override assignment is never data-path
    information). None whenever that assignment is anything else (an
    operator, a width-changing conversion, a case arm assigning a
    DIFFERENT bare signal than another arm) -- not guessed at, same
    "don't fabricate a fallback" principle as everywhere else in this
    module.
    """

    output_signal: str
    is_latch: bool
    clock_ref: Optional[str] = None  # for latches, this is the enable/gate signal, not a clock
    edge: Optional[Edge] = None  # always None when is_latch=True
    reset_ref: Optional[str] = None
    reset_kind: ResetKind = ResetKind.NONE
    reset_active_low: Optional[bool] = None
    set_ref: Optional[str] = None
    set_active_low: Optional[bool] = None
    data_ref: Optional[str] = None
    source_loc: Optional[SourceLoc] = None


@dataclass(frozen=True)
class ModportPort:
    """One signal as exposed through a modport -- direction is the
    modport's own view (e.g. `output` from a `req` modport, `input` from
    the `cmp` modport looking at the very same underlying signal), not a
    property of the signal itself. Reuses PortDirection rather than a
    separate enum -- pyslang's own ArgumentDirection maps onto it exactly
    the same way a regular port's direction does."""

    name: str
    direction: PortDirection


@dataclass(frozen=True)
class Modport:
    name: str
    ports: list[ModportPort] = field(default_factory=list)


@dataclass(frozen=True)
class InterfacePort:
    """An interface-typed port (`axi_lite_if.mst dbus`) -- structurally
    alien to Port (one signal/one direction/one width): an interface port
    bundles many signals, each with a direction that depends on which
    modport it's viewed through, not the port itself.

    `interface_name` is the referenced interface's *base name* (e.g.
    "apb_if"), not a definition_id -- unlike a child module Instance,
    there is no single parameter-resolved interface Definition uniquely
    determined at the port-declaration site; the concrete bound instance
    (with its own resolved parameters) is only known per-instantiation,
    via the owning Instance's own `connections` -- same division of labor
    as Port/PortConnection already have for plain signals.

    `modport` is None for a genuinely unconstrained interface port (rare
    -- e.g. `axi_lite_if mem_port` with no `.modport` suffix), matching
    pyslang's own InterfacePortSymbol.modport being an empty string in
    that case.

    `inferred_modport` is only ever set when `modport` is None: a *derived*
    guess at the effective modport, found by tracing this exact port
    through pure pass-through wiring to a *child* instance inside this same
    module's own body whose own port on that same interface *is*
    modport-qualified (e.g. `axi_lite_if mem_port` immediately forwarded to
    `adapter u_adapter (.mem_port(mem_port))`, where `adapter`'s own port is
    declared `axi_lite_if.slv mem_port`). Real, common RTL style: a
    forwarding module leaves its own port unconstrained but every actual
    consumer downstream is modport-qualified. None if no such child is
    found, or if more than one child disagrees on the modport (genuinely
    ambiguous -- not guessed at, same "don't fabricate a fallback"
    principle used elsewhere for unresolved references).
    """

    name: str
    interface_name: str
    modport: Optional[str] = None
    inferred_modport: Optional[str] = None
    # None for a scalar interface port; the declared element count for an
    # ARRAYED one (`axi_lite_if mst_port [N]`) -- found via real `aura`
    # design use (backend/elaborator/rtl/ir/flatten.py's own interface-
    # aware connectivity tracing): pyslang gives exactly ONE
    # InterfacePortSymbol regardless of array size, with no per-index
    # breakdown at the port-declaration level, so array-ness has to be
    # captured explicitly here (from InterfacePortSymbol.declaredRange)
    # rather than inferred from the connection side. Needed so a bare
    # "mst_port.awvalid"-style compound signal reference can be expanded
    # into one genuinely distinct per-index signal
    # ("mst_port[0].awvalid", "mst_port[1].awvalid", ...) instead of
    # being wrongly treated as if it named one shared physical signal --
    # each array element is wired to a DIFFERENT interface instance
    # (see verilog/elaborate.py's own _interface_connections_for_port()).
    array_size: Optional[int] = None


@dataclass(frozen=True)
class ContinuousAssign:
    """One `assign target = ...;` (Verilog/SV) or concurrent signal
    assignment (VHDL, `target <= ...;` outside a process) -- the
    combinational-logic primitive this IR had no representation for at
    all before the connectivity-flattening feature (backend/elaborator/rtl/ir/flatten.py):
    every prior Milestone-1 pass either ignored continuous assigns
    entirely (Verilog/SV: no ContinuousAssignSymbol branch existed in
    _walk_body_members()) or only ever consulted the equivalent VHDL/
    Yosys netlist data transiently, one hop at a time, to trace reset
    polarity (vhdl/elaborate.py's _trace_through_one_inverter()/
    _find_reset_mux()) -- never storing it as real IR data.

    `source` is set ONLY when the RHS is a bare, same-width, single-
    signal reference (Verilog/SV: pyslang's own NamedValueExpression,
    optionally through one ConversionExpression; VHDL: the target's own
    bit-id list exactly equals a different single named signal's, with
    no cell between them in the Yosys netlist) -- i.e. TRUE pass-
    through wiring, nothing else. Any other RHS shape (an operator, a
    mux, a concatenation, a width mismatch even when the RHS IS a bare
    reference) records `source=None`: real combinational logic drives
    `target` HERE, at this exact point -- a stopping point for
    flatten.py's own trace_driver()/trace_receivers(), never walked
    through as if transparent.

    `referenced_signals` (found tracing a signal that feeds INTO a
    real-logic RHS as one of possibly several operands, e.g. `sleep_int`
    in `assign o_sleep = ~sleep_int;` -- nothing previously recorded
    that `sleep_int` is CONSUMED here at all, since `source` only ever
    captures the single-bare-signal pass-through case): every distinct
    signal name referenced anywhere in the RHS expression, real logic or
    not (for a true pass-through, this is just `(source,)`). Lets
    flatten.py's own trace_receivers() recognize "this signal feeds a
    real-logic assign" as a legitimate receiver fact, symmetric with how
    trace_driver() already recognizes `source=None` as "real logic
    drives `target` here" -- without inspecting the general RHS
    EXPRESSION itself (still no data-path/next-state modeling beyond
    "which signals are referenced")."""

    target: str
    source: Optional[str] = None
    referenced_signals: tuple[str, ...] = ()


@dataclass(frozen=True)
class WellBiasPin:
    """A black-box (.lib) macro's own NWELL/PWELL body-bias pg_pin
    (Liberty `pg_type: nwell`/`pwell`, e.g. VPB/VNB) -- found via a real
    macro PG-pin-connectivity golden fixture needing to validate a
    resolved Supply Set's own Nwell/Pwell against what the LIB implies
    is expected. Deliberately NOT modeled as a `Port` the way primary/
    internal power/ground pg_pins are (see Port.direction's own
    PortDirection.PG/PG_INTERNAL docstring) -- a well-bias pin is not a
    connectable port at all; it's implicit metadata: the well is
    understood to ride on whatever Supply Set is actually connected to
    the PG pin its own `related_bias_pin` names, never wired directly
    in the diagram.

    `pg_type` is the raw Liberty value, "nwell" or "pwell" -- selects
    which SupplySetPlacement field (n_well/p_well respectively) the
    resolved Supply Set's own value must be compared against.
    `related_bias_pin` names the OTHER pg_pin (e.g. "VDD") this well
    should bias from -- power_intent_view/power_resolution_cache.py's
    own bias-conflict check matches this against whichever of a
    functional pin's own related_power_pin/related_ground_pin it
    equals, so a well isn't hardcoded to only ever check power (for
    nwell) or only ever check ground (for pwell) -- it follows
    whatever the LIB itself actually declares."""

    pg_type: str
    related_bias_pin: str


@dataclass(frozen=True)
class PortConnection:
    instance_port: str
    connected_to: Optional[str] = None  # name of a Port/Net/Instance in the parent Definition; None = unconnected


@dataclass(frozen=True)
class Instance:
    name: str
    definition_id: str
    connections: list[PortConnection] = field(default_factory=list)
    source_loc: Optional[SourceLoc] = None


@dataclass(frozen=True)
class Definition:
    definition_id: str
    base_name: str
    language: SourceLanguage
    # The .v/.sv/.vhd/.lib file path this Definition came from, always
    # set by every front-end's own elaborate() call -- Verilog/SV
    # (verilog/elaborate.py) and Liberty (lib/elaborate.py) always
    # resolve a real, non-empty path here. VHDL (vhdl/elaborate.py) is
    # the one exception: it's looked up via a base_name -> source_file
    # map built while walking GHDL's own JSON netlist, and falls back to
    # `""` (not a guess or a raised error) for an entity that map
    # doesn't cover -- a real, confirmed possibility, not merely
    # theoretical, found while wiring up power_intent_view's own
    # "Inspect Source File" context menu action (frontend/src/shared/
    # models/rtl.py's own mirrored field carries the identical caveat).
    source_file: str
    parameters: dict[str, str] = field(default_factory=dict)
    is_black_box: bool = False
    is_interface: bool = False  # a `interface ... endinterface` definition, not a module
    source_loc: Optional[SourceLoc] = None
    ports: list[Port] = field(default_factory=list)
    nets: list[Net] = field(default_factory=list)
    instances: list[Instance] = field(default_factory=list)
    sequential_elements: list[SequentialElement] = field(default_factory=list)
    interface_ports: list[InterfacePort] = field(default_factory=list)
    modports: list[Modport] = field(default_factory=list)  # only ever non-empty when is_interface=True
    continuous_assigns: list[ContinuousAssign] = field(default_factory=list)
    well_bias_pins: list[WellBiasPin] = field(default_factory=list)  # only ever non-empty for a black-box (.lib) macro


@dataclass(frozen=True)
class Design:
    definitions: dict[str, Definition]  # keyed by definition_id
    top: str

    def definition(self, definition_id: str) -> Definition:
        return self.definitions[definition_id]

    def walk(self, separator: str = "/") -> Iterator["HierarchyNode"]:
        """Depth-first walk from the top Definition down through instances.

        Paths are derived on demand, never stored: a Definition instantiated
        multiple times (see test_duplicate_instantiation_dedupes_to_one_definition)
        appears once in `definitions` but multiple times here, once per path.

        `separator` defaults to `/` (unchanged behavior for every existing
        RTL-only caller). Milestone 3 passes whatever separator the
        power-intent source actually declares -- UPF paths are `/`-joined,
        CPF's is configurable via `set_hierarchy_separator` (default `.`,
        not `/`). RTL's own hierarchy has no separator convention of its
        own to defend; there's nothing wrong with joining paths with `.`
        instead of `/` when the caller needs that.
        """
        root = self.definitions[self.top]
        yield from _walk(self, self.top, root.base_name, None, separator)


@dataclass(frozen=True)
class HierarchyNode:
    path: str
    definition: Definition
    instance: Optional[Instance] = None  # None for the design root


def _walk(
    design: Design, definition_id: str, path: str, instance: Optional[Instance], separator: str = "/"
) -> Iterator[HierarchyNode]:
    definition = design.definitions[definition_id]
    yield HierarchyNode(path=path, definition=definition, instance=instance)
    for child in definition.instances:
        yield from _walk(design, child.definition_id, f"{path}{separator}{child.name}", child, separator)


def normalize_identifier_case(name: str, language: SourceLanguage) -> str:
    """Case-fold `name` per `language`'s own identifier semantics.

    VHDL identifiers are case-insensitive, so a VHDL-side lookup (matching
    against a VHDL Definition's ports, or resolving a power-intent reference
    against a VHDL leaf) must fold to lowercase to line up with a
    differently-cased spelling from a Verilog/UPF/CPF author who doesn't
    necessarily know or care about VHDL's case folding. Verilog/
    SystemVerilog and Liberty black-box identifiers are case-sensitive, so
    they pass through unchanged.

    Extracted from Milestone 1d's mixed_language._normalize (verified there
    against real pyslang/GHDL case-sensitivity behavior) for reuse by
    Milestone 3's RTL-hierarchy resolver, which needs the identical rule
    when resolving a power-intent reference against a VHDL leaf definition.
    """
    return name.lower() if language == SourceLanguage.VHDL else name


def make_definition_id(base_name: str, parameters: dict[str, str]) -> str:
    """Deterministic, opaque key for an elaborated definition variant.

    Same base_name + same resolved parameter bindings => same id, so
    re-elaborating an already-seen (module, parameter) pair is a cache hit.
    The hash suffix is for human readability only; callers must use
    base_name/parameters, never parse it back out of the id.
    """
    canonical = json.dumps([base_name, sorted(parameters.items())])
    short_hash = hashlib.sha1(canonical.encode()).hexdigest()[:8]
    return f"{base_name}__{short_hash}"
