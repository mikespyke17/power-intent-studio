"""RTL hierarchy/netlist view model.

Mirrors backend/elaborator/rtl/ir/schema.py field-for-field -- this is a
genuine duplicate, not an import. Per CLAUDE.md's milestone-independence
rule, frontend/ never imports from backend/, not even for type shapes:
doing so would make the GUI's importability depend on the back end's
(GHDL, pyslang, ...), defeating "buildable and demoable with zero
dependency on the real back end." The goal of mirroring the shape exactly
is that backend/api/ (Final Milestone) has an existing, exercised target
shape to produce, not a redesign of this module.

Deliberately kept a real dataclass module, not just "whatever shape is
convenient for today's mock data" -- see rtl.SourceLoc's line/column
split below, which matters even before any real RTL is involved.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class PortDirection(Enum):
    IN = "in"
    OUT = "out"
    INOUT = "inout"
    PG = "pg"  # macro (.lib) power pin -- see PG_INTERNAL below
    PG_INTERNAL = "pg_internal"  # macro (.lib) internal power/ground pin
    PG_WELL = "pg_well"  # macro (.lib) EXPOSED well-bias pin -- mirrors the real schema's own PortDirection.PG_WELL

    # Mirrors a real backend value: backend/elaborator/lib/elaborate.py's
    # _build_pg_pin_port() reads a Liberty cell's `pg_pin` groups
    # (pg_type primary_power/primary_ground/backup_power/backup_ground ->
    # PG, internal_power/internal_ground -> PG_INTERNAL) so a real
    # black-box Definition's ports do carry these today, not just mock
    # demo data -- confirmed against real aura .lib macros' VDD/VSS
    # primary_power/primary_ground pins. PG_WELL mirrors the real
    # schema's own docstring for the full "why" (an EXPOSED well-bias
    # pin, e.g. VPW -- no related_bias_pin, a real connectable macro
    # pin, distinct from an IMPLICIT one like VPB).


class SourceLanguage(Enum):
    VERILOG = "verilog"
    SYSTEMVERILOG = "systemverilog"
    VHDL = "vhdl"
    LIBERTY = "liberty"


@dataclass(frozen=True)
class SourceLoc:
    """Real RTL SourceLoc: `line` is required, `column` is optional --
    NOT the same shape as power_intent.SourceLoc below, which has no
    column field at all. The diagnostics layer depends on this
    distinction being real, not flattened away here."""

    file: str
    line: int
    column: Optional[int] = None


@dataclass(frozen=True)
class Port:
    name: str
    direction: PortDirection
    width: int = 1
    # A PG (or PG_INTERNAL) pin's own nominal voltage, raw as authored
    # (e.g. "0.80V"), for display only (power_intent_view/
    # supply_port_overlay.py's PG-pin icon label). Resolved on the real
    # backend from a .lib pg_pin's `voltage_name` through the library's
    # own `voltage_map` entries (backend.elaborator.lib.elaborate,
    # mirrored onto backend/elaborator/rtl/ir/schema.py's own Port) --
    # previously a tracked gap here (mock/diagram-only data could set it
    # directly, but a real loaded design never had it populated at all),
    # closed after being found via real `aura` .lib use. None for every
    # non-PG port, and for a PG port whose voltage_name has no matching
    # voltage_map entry.
    voltage: Optional[str] = None
    # Mirrors the real schema's Port.related_power_pin exactly -- a
    # functional (non-PG) Liberty pin's own `related_power_pin`, the
    # pg_pin name that actually powers it (e.g. "VDD"). Used by
    # power_intent_view/power_resolution_cache.py to resolve a macro
    # pin's own "facing the macro" Driver/Receiver Supply Set column
    # authoritatively from LIB data, not guessed at via whichever Power
    # Domain happens to own the macro's own hierarchy path.
    related_power_pin: Optional[str] = None
    # Mirrors the real schema's Port.related_ground_pin exactly -- the
    # GROUND-side counterpart of related_power_pin above, needed so
    # power_resolution_cache.py can independently validate BOTH rails of
    # a macro pin's own PG-pin pair (a pin whose power rail is wired but
    # whose ground rail is left open, or vice versa, is a real, distinct
    # partially-connected state -- "Macro - Ground Floating"/"Macro -
    # Power Floating" -- and two rails wired to DIFFERENT Supply Sets is
    # a real, distinct "Conflict - Supply Set" state).
    related_ground_pin: Optional[str] = None


@dataclass(frozen=True)
class Net:
    name: str
    width: int = 1


@dataclass(frozen=True)
class PortConnection:
    instance_port: str
    connected_to: Optional[str] = None


@dataclass(frozen=True)
class Instance:
    name: str
    definition_id: str
    connections: list[PortConnection] = field(default_factory=list)
    source_loc: Optional[SourceLoc] = None


@dataclass(frozen=True)
class SequentialElement:
    """Trimmed mirror of the real SequentialElement (backend/elaborator/rtl/ir/schema.py):
    only `output_signal`/`is_latch`, the two fields the Properties panel's
    Register/Latch tabs need -- same trimming precedent as Port/Net above
    (plain int width, no bit_range/source_loc). Width isn't a field on the
    real type either; it's resolved by cross-referencing `output_signal`
    against the owning Definition's ports/nets by name.

    `clock_ref`/`reset_ref`/`set_ref` (found via a real design's own
    top-level `always_ff`, connectivity_flatten.py's own Driver/Receiver
    Supply Set tracing): a flop/latch's own clock/reset/set signal is a
    real, legitimate receiver of that signal -- previously untrimmed-but-
    unused-then-dropped-again gap, now mirrored so this trimming no
    longer silently defeats that tracing for every design loaded through
    the validation harness.

    `data_ref` mirrors the real schema's own SequentialElement.data_ref
    exactly (see its own docstring for the full "why" -- reversed from
    an earlier, stricter "no data-path modeling at all" decision, found
    blocking correct Driver/Receiver Supply Set results): the SAME
    narrow "is the RHS a bare, same-width single-signal reference" test
    already applied to ContinuousAssign.source, applied to a sequential
    element's own next-state assignment. None whenever that's anything
    else (an operator, a case arm picking between different signals) --
    general next-state EXPRESSION modeling remains explicitly out of
    scope."""

    output_signal: str
    is_latch: bool
    clock_ref: Optional[str] = None
    reset_ref: Optional[str] = None
    set_ref: Optional[str] = None
    data_ref: Optional[str] = None


@dataclass(frozen=True)
class ModportPort:
    """One signal as exposed through a modport -- direction is the
    modport's own view, not a property of the signal itself (e.g. the
    same signal is `output` from one modport and `input` from another).
    Mirrors backend/elaborator/rtl/ir/schema.py's ModportPort exactly."""

    name: str
    direction: PortDirection


@dataclass(frozen=True)
class Modport:
    name: str
    ports: list[ModportPort] = field(default_factory=list)


@dataclass(frozen=True)
class InterfacePort:
    """An interface-typed port (`axi_lite_if.mst dbus`) -- structurally
    alien to Port (one signal/one direction/one width). `interface_name`
    is the referenced interface's base_name, not a definition_id -- same
    reasoning as the real schema's InterfacePort: the concrete bound
    interface instance is only known per-instantiation, via the owning
    Instance's connections. `modport` is None for a genuinely
    unconstrained interface port. `inferred_modport` -- only ever set
    when `modport` is None -- is a modport *derived* from tracing this
    port's pass-through wiring to a modport-qualified child instance
    inside this same module's own body; see the real schema's
    InterfacePort docstring for the full reasoning."""

    name: str
    interface_name: str
    modport: Optional[str] = None
    inferred_modport: Optional[str] = None
    # Mirrors the real schema's InterfacePort.array_size exactly -- None
    # for a scalar interface port, the declared element count for an
    # ARRAYED one. See the real schema's own docstring for the full "why"
    # (power_intent_view/connectivity_flatten.py's own interface-aware
    # tracing needs it to expand a bare "mst_port.signal"-style reference
    # into distinct per-index Endpoints, matching the real backend).
    array_size: Optional[int] = None


@dataclass(frozen=True)
class ContinuousAssign:
    """Mirrors backend/elaborator/rtl/ir/schema.py's ContinuousAssign
    exactly -- the combinational-logic primitive power_intent_view/
    connectivity_flatten.py's own trace_driver()/trace_receivers() need
    to distinguish true pass-through wiring (`source` set) from real
    logic (`source=None`, a stopping point). See the real schema's own
    ContinuousAssign docstring for the full "why", including
    `referenced_signals` (every signal referenced anywhere in the RHS,
    not just the special bare-pass-through `source` case -- needed to
    recognize a signal feeding INTO real logic as a legitimate
    receiver)."""

    target: str
    source: Optional[str] = None
    referenced_signals: tuple[str, ...] = ()


@dataclass(frozen=True)
class WellBiasPin:
    """Mirrors backend/elaborator/rtl/ir/schema.py's WellBiasPin exactly
    -- a black-box (.lib) macro's own NWELL/PWELL body-bias pg_pin (e.g.
    VPB/VNB), never a connectable Port. See the real schema's own
    docstring for the full "why" -- power_intent_view/
    power_resolution_cache.py's own bias-conflict check needs it to
    validate a resolved Supply Set's own Nwell/Pwell against what the
    LIB implies is expected."""

    pg_type: str
    related_bias_pin: str


@dataclass(frozen=True)
class Definition:
    definition_id: str
    base_name: str
    language: SourceLanguage
    is_black_box: bool = False
    is_interface: bool = False  # a real `interface ... endinterface`, not a module -- Verilog/SV only
    ports: list[Port] = field(default_factory=list)
    nets: list[Net] = field(default_factory=list)
    instances: list[Instance] = field(default_factory=list)
    sequential_elements: list[SequentialElement] = field(default_factory=list)
    interface_ports: list[InterfacePort] = field(default_factory=list)
    modports: list[Modport] = field(default_factory=list)  # only ever non-empty when is_interface=True
    continuous_assigns: list[ContinuousAssign] = field(default_factory=list)
    well_bias_pins: list[WellBiasPin] = field(default_factory=list)  # only ever non-empty for a black-box (.lib) macro
    source_loc: Optional[SourceLoc] = None
    # Mirrors the real schema's own Definition.source_file exactly -- a
    # real elaborated design's own path to the .v/.sv/.vhd/.lib file this
    # Definition came from (never a search-heuristic guess: it's the SAME
    # path each front-end's own elaborate() call already resolved). Found
    # missing entirely from this frontend mirror while adding "Inspect
    # Source File" (power_intent_diagram_view.py's own context menu):
    # every real Definition already carries this on the backend, but
    # backend_adapter.py never forwarded it and this dataclass never
    # declared it at all -- the same "adapter forgot to forward a field"
    # bug class found and fixed repeatedly earlier this session (see
    # Port.related_power_pin/InterfacePort.array_size/
    # Definition.continuous_assigns et al.). Defaults to "" (not None) --
    # matching the real backend's own required-str field shape exactly
    # (see backend/elaborator/rtl/ir/schema.py's own Definition.source_file
    # docstring for why VHDL specifically can legitimately produce an
    # empty string here, not just "always populated").
    source_file: str = ""


@dataclass(frozen=True)
class HierarchyNode:
    path: str
    definition: Definition
    instance: Optional[Instance] = None


@dataclass(frozen=True)
class Design:
    definitions: dict[str, Definition]
    top: str

    def definition(self, definition_id: str) -> Definition:
        return self.definitions[definition_id]

    def walk(self, separator: str = "/") -> Iterator[HierarchyNode]:
        """Mirrors the real Design.walk()'s behavior, not just its
        signature -- the tree/diagram builders exercise the same
        depth-first traversal pattern they'll use against real data."""
        root = self.definitions[self.top]
        yield from _walk(self, self.top, root.base_name, None, separator)


def _walk(
    design: Design, definition_id: str, path: str, instance: Optional[Instance], separator: str
) -> Iterator[HierarchyNode]:
    definition = design.definitions[definition_id]
    yield HierarchyNode(path=path, definition=definition, instance=instance)
    for child in definition.instances:
        yield from _walk(design, child.definition_id, f"{path}{separator}{child.name}", child, separator)
