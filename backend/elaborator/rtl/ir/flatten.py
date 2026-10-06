"""Connectivity flattening -- Milestone 1g's own explicitly-deferred
"on-demand flattening" step ("nothing traces a signal end-to-end across
hierarchical IR"), now built. Traces a signal's TRUE driver (backward)
and TRUE receiver(s) (forward) across arbitrary hierarchy depth and
combinational pass-through chains (see ContinuousAssign's own
docstring in schema.py) -- built now, ahead of a more general Milestone
1g pass, specifically because an incomplete (single-hierarchy-level-
only) trace has real design-correctness consequences downstream
(isolation-cell requirement decisions in the Power Intent Studio GUI),
not a cosmetic limitation.

Pure RTL-structural concern -- zero knowledge of Power Domains/Supply
Sets (a separate, downstream, Power-Intent-level concept resolved by a
different, later consumer from this module's own plain structural
facts). This module never imports from backend/mapping,
backend/validation, or backend/elaborator/power_intent -- Milestone 1
(RTL elaboration) must not depend on Milestone 3 (mapping/validation),
the same "don't quietly couple two tracks that are supposed to stay
independent" discipline this whole project already applies at the
frontend/backend boundary, one milestone boundary over. This is also
WHY `ConnectivityWarning` below is its own small, local type rather
than reusing backend.validation.schema.Finding: that type's own
`reference` field is typed to Milestone 3's ResolvedReference, which
would make this module import something two milestones ahead of where
it lives.

A resolved `Endpoint` is reported as plain structural data (path +
signal name) -- it does NOT self-classify as "a macro" or "the design's
own top-level boundary": the caller already needs to look up
`design.definition(...)` for that endpoint to resolve anything
Power-Domain-flavored anyway, so classifying "is this a macro?"/"is
this the top?" via `design`/`Endpoint.path` at that point (trivial:
`design.definition(...).is_black_box`, `endpoint.path == design.top`)
is simpler than this module pre-classifying it into a parallel enum
value that would just be redundant with data the caller already has.

Relationship to a future, real Milestone-1g/backend-side flattening
pass: this module IS that step, built now under real design-
correctness pressure rather than in Milestone-1g's own originally
envisioned build order. Its own data shapes (Endpoint/DriverResult/
ReceiverResult) are deliberately plain and small, so a later, more
general flattening effort (e.g. one also modeling tristate/inout
semantics, which this pass explicitly does not -- see trace_driver()'s
own docstring) can supersede or reconcile against this one without
a structural rewrite of its own callers.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Optional

from backend.elaborator.rtl.ir.schema import ContinuousAssign, Definition, Design, HierarchyNode, Instance, PortDirection


class TraceKind(Enum):
    RESOLVED = "resolved"  # >=1 concrete endpoint found -- an ordinary cell, a macro, or a top-level boundary port; caller classifies via Endpoint.path
    UNDRIVEN = "undriven"  # trace_driver only -- no driver found anywhere (a real LINT issue)
    FLOATING = "floating"  # trace_receivers only -- no receiver found anywhere (a real LINT issue)
    LOOP = "loop"
    MULTIPLE_DRIVERS = "multiple_drivers"  # trace_driver only -- never silently picked one


@dataclass(frozen=True)
class Endpoint:
    path: str
    signal: str


@dataclass(frozen=True)
class DriverResult:
    kind: TraceKind
    endpoint: Optional[Endpoint] = None  # RESOLVED only
    cycle: Optional[tuple[Endpoint, ...]] = None  # LOOP only
    candidates: Optional[tuple[Endpoint, ...]] = None  # MULTIPLE_DRIVERS only


@dataclass(frozen=True)
class ReceiverResult:
    kind: TraceKind
    endpoints: tuple[Endpoint, ...] = ()  # RESOLVED only, deduplicated
    loops: tuple[tuple[Endpoint, ...], ...] = ()  # every distinct loop branch found while tracing forward, even alongside real endpoints


@dataclass(frozen=True)
class ConnectivityWarning:
    """A LOOP or MULTIPLE_DRIVERS finding worth surfacing to the user --
    deliberately NOT backend.validation.schema.Finding, see this
    module's own docstring for why."""

    kind: TraceKind  # LOOP or MULTIPLE_DRIVERS only
    endpoint: Endpoint
    detail: tuple[Endpoint, ...]


@dataclass(frozen=True)
class FlattenedConnectivity:
    drivers: dict[Endpoint, DriverResult]
    receivers: dict[Endpoint, ReceiverResult]
    warnings: tuple[ConnectivityWarning, ...] = ()


@dataclass
class _TraceContext:
    """Layer-1-style memoization scoped to ONE flatten_design() pass (or
    one standalone trace_driver()/trace_receivers() call) -- overlapping
    paths (adjacent ports sharing intermediate pass-through chains)
    aren't re-walked redundantly within that same pass, per the explicit
    "caching/performance" requirement. `node_by_path` is built once
    (a single design.walk()), not re-walked per lookup."""

    design: Design
    node_by_path: dict[str, HierarchyNode] = field(default_factory=dict)
    driver_memo: dict[Endpoint, DriverResult] = field(default_factory=dict)
    receiver_memo: dict[Endpoint, ReceiverResult] = field(default_factory=dict)


def _build_context(design: Design) -> _TraceContext:
    return _TraceContext(design=design, node_by_path={node.path: node for node in design.walk()})


def _parent_path(path: str) -> Optional[str]:
    if "/" not in path:
        return None
    return path.rsplit("/", 1)[0]


def _connected_net_for_instance_port(instance: Instance, port_name: str) -> Optional[str]:
    for conn in instance.connections:
        if conn.instance_port == port_name:
            return conn.connected_to
    return None


def _dedup_endpoints(endpoints: list[Endpoint]) -> tuple[Endpoint, ...]:
    seen: set[Endpoint] = set()
    result: list[Endpoint] = []
    for ep in endpoints:
        if ep not in seen:
            seen.add(ep)
            result.append(ep)
    return tuple(result)


@dataclass(frozen=True)
class _NetGroupMember:
    role: str  # "driver" | "sink" | "unknown" (inout -- see trace_driver()'s own docstring on scope)
    is_parent_own_port: bool
    instance_name: Optional[str]  # None iff is_parent_own_port
    port_name: str


@dataclass(frozen=True)
class _NetGroup:
    net_name: str
    members: tuple[_NetGroupMember, ...]


def _strip_constant_index(net_name: str) -> Optional[str]:
    """"clk_dom[4]" -> "clk_dom"; a name with no "[...]" suffix -> None
    (nothing to strip -- also the guard that stops net_group()'s own
    fallback below from looping)."""
    if net_name.endswith("]") and "[" in net_name:
        return net_name[: net_name.index("[")]
    return None


def _net_group_members(design: Design, defn, net_name: str) -> tuple[_NetGroupMember, ...]:
    members: list[_NetGroupMember] = []
    for port in defn.ports:
        if port.name != net_name:
            continue
        if port.direction == PortDirection.OUT:
            role = "sink"
        elif port.direction == PortDirection.IN:
            role = "driver"
        else:
            role = "unknown"
        members.append(_NetGroupMember(role, True, None, port.name))
    for inst in defn.instances:
        inst_defn = design.definition(inst.definition_id)
        for conn in inst.connections:
            if conn.connected_to != net_name:
                continue
            inst_port = next((p for p in inst_defn.ports if p.name == conn.instance_port), None)
            if inst_port is None:
                continue
            if inst_port.direction == PortDirection.OUT:
                role = "driver"
            elif inst_port.direction == PortDirection.IN:
                role = "sink"
            else:
                role = "unknown"
            members.append(_NetGroupMember(role, False, inst.name, conn.instance_port))
    return tuple(members)


def net_group(design: Design, defn, net_name: str) -> _NetGroup:
    """Net-mediated connectivity aggregation -- the piece that didn't
    exist anywhere in this codebase before this feature (confirmed by
    investigation): for one Definition's own scope, which instance
    ports AND the Definition's own boundary ports share a given net
    name. A boundary port's role is direction-INVERTED relative to
    internal signal flow: an IN boundary port is a DRIVER from inside
    this scope (data flows in from outside); an OUT boundary port is a
    SINK (data flows out, something inside must feed it). A child
    instance's port is the mirror image of its own declared direction:
    an OUT port on the child drives the parent's net, an IN port on the
    child is a sink of it.

    Scoped to exactly ONE Definition/net_name pair per call -- never
    compares nets across different hierarchy levels or different
    Definitions (a `connected_to` name is only ever meaningful within
    the one Definition scope it was recorded against, per PortConnection's
    own schema docstring), so there's no risk of two same-named-but-
    unrelated nets at different depths being conflated.

    A bit-indexed `net_name` ("clk_dom[4]") also MERGES in members found
    under the WHOLE, un-indexed vector's own name ("clk_dom") -- real,
    confirmed gap found via real `aura` design use (`clk_rst_gen`'s own
    per-domain `clk_dom[d]` continuous-assign targets -- each an exact,
    genuinely per-bit DRIVER match on its own -- consumed one level up
    via a single whole-vector `.clk_dom(clk_dom)` port binding with no
    per-bit granularity on the CONNECTING side at all, while every actual
    CONSUMER references one specific bit via a constant element-select,
    e.g. `.clk(clk_dom[DOM_CPU])`, itself an exact, genuinely per-bit
    SINK match): a plain string-equality net_group lookup can never
    match an indexed query against a whole-vector connection, so without
    this, the exact-indexed lookup alone finds the SINK half of the
    picture (a real member IS found -- an earlier version of this
    fallback that only activated on a completely EMPTY result missed
    this exact case) but never the whole-vector DRIVER, permanently
    reporting UNDRIVEN despite a real, correct driver existing one query
    away. Unconditionally MERGED (not a conditional fallback) when
    `net_name` has an index at all -- a plain string's own `connected_to`
    can never coincidentally collide with an indexed one (a given
    PortConnection's `connected_to` is either the exact indexed form or
    the bare whole-vector form, never both), so there is no
    double-counting risk. The returned group is still labeled with the
    ORIGINAL (indexed) `net_name`, not the whole-vector base name, so
    callers see exactly what they asked for.

    Every member found via the whole-vector fallback has the SAME "[N]"
    index suffix re-attached to its own `port_name` (`replace()`'d in,
    the dataclass being frozen) -- real, confirmed follow-on gap found
    the same way: without this, descending into e.g. `u_clk_rst`'s own
    `clk_dom` OUTPUT port to find ITS driver would ask for the bare,
    unindexed "clk_dom" there too, but `u_clk_rst` only ever has PER-BIT
    continuous-assign targets ("clk_dom[0]".."clk_dom[5]", one per
    `generate for` iteration) -- never a single assign covering the
    whole vector at once -- so an un-reindexed descend would ALSO find
    nothing. Re-attaching the suffix keeps asking the SAME specific-bit
    question all the way down the hierarchy, exactly matching how the
    exact-match half of this same net_group() call already only ever
    returns members that inherently answer the specific-bit question
    directly (e.g. `u_mem`'s own non-arrayed `clk` port, matched via the
    exact indexed connected_to string, needs no suffix of its own to
    begin with)."""
    members = list(_net_group_members(design, defn, net_name))
    base_name = _strip_constant_index(net_name)
    if base_name is not None:
        suffix = net_name[len(base_name) :]
        members.extend(
            replace(member, port_name=f"{member.port_name}{suffix}") for member in _net_group_members(design, defn, base_name)
        )
    return _NetGroup(net_name, tuple(members))


# --- Interface-aware tracing --------------------------------------------
# Real, confirmed gap found via real `aura` design use (an interface-
# heavy SoC that uses SV interfaces as its PRIMARY connectivity
# mechanism -- see docs/milestone1_rtl_elaborator.md's own "SystemVerilog
# interfaces" entry): net_group() above only ever matches a child
# instance's connection against `inst_defn.ports` (plain signals) --
# every interface-typed connection was silently skipped (inst_port ends
# up None, `continue`s past it), making an interface-mediated signal
# invisible to trace_driver()/trace_receivers() entirely, regardless of
# the whole-array connection-naming bug fixed separately in
# verilog/elaborate.py's own _interface_connections_for_port().
#
# A signal reference into an interface bundle is addressed as a compound
# "iface_port_name.signal_name" string (same naming convention
# hierarchy_view/properties_table_widget.py's own _interface_port_rows()
# already established for display) -- _trace_driver()/_trace_receivers()
# both check for "." at the very top and dispatch here instead of the
# plain-signal path below.
#
# Two genuinely different mechanisms, not one:
#   (a) REDIRECT INTO a bound interface instance -- `some_module.some_
#       iface_port.some_signal` resolves the port's own Instance
#       connection to find which interface instance it's bound to, then
#       hands off to the ordinary (non-compound) trace machinery at that
#       instance's own path/signal -- an interface instance is a normal
#       HierarchyNode with its own Definition (ports/nets/instances), so
#       once redirected, nothing else needs to change.
#   (b) PEER LOOKUP for one of an interface instance's OWN internal nets
#       -- an interface Definition (e.g. `apb_if`) has no instances of
#       its own to drive/consume its internal signals (`paddr` etc.); the
#       real drivers/receivers are OTHER, sibling instances at the
#       interface instance's own PARENT scope that bind an interface-
#       typed port to this SAME interface instance. Their role (driver vs
#       sink) is determined by that binding's own effective modport --
#       "direction is the modport's own view" (ModportPort's own
#       docstring) -- exactly mirroring net_group()'s existing OUT=driver/
#       IN=sink convention, just evaluated across sibling instances
#       instead of child instances of the SAME Definition.
#
# A peer, once found, is reported as a terminal RESOLVED endpoint
# immediately (not recursed into further) -- structurally identical to
# how a black-box macro is always terminal: this IR has no way to trace
# INSIDE a module's own body from one of its interface-port bindings
# (that would require following a hierarchical/dotted LHS assignment
# like `apb.paddr <= internal_reg;`, which _build_continuous_assign()
# already can't represent -- only a bare single-signal LHS is captured
# at all), so the driving/receiving CELL itself -- exactly the
# granularity Power-Domain resolution actually needs -- is as far as
# this can honestly go.


def _find_interface_definition(design: Design, interface_name: str) -> Optional[Definition]:
    for candidate in design.definitions.values():
        if candidate.is_interface and candidate.base_name == interface_name:
            return candidate
    return None


def _effective_modport(iface_port) -> Optional[str]:
    return iface_port.modport or iface_port.inferred_modport


def _modport_direction(iface_defn: Definition, modport_name: str, signal_name: str) -> Optional[PortDirection]:
    modport = next((m for m in iface_defn.modports if m.name == modport_name), None)
    if modport is None:
        return None
    member = next((p for p in modport.ports if p.name == signal_name), None)
    return member.direction if member is not None else None


def _connected_targets_for_iface_port(instance: Instance, base_name: str) -> list[str]:
    """Every bound target for interface port `base_name` on `instance` --
    a single-element bind (`.apb(apb_bus)`) gives exactly one unindexed
    connection (`instance_port == base_name`); a whole-array bind
    (`.mst_port(axi_mst)`, both sides arrays) was split into one
    connection per element by verilog/elaborate.py's own
    _interface_connections_for_port() (`instance_port == "mst_port[i]"`)
    -- returns every element's own `connected_to` in that case, sorted
    by index, so a caller can merge/fan-out across all of them rather
    than guessing at just one (same "never silently pick one" discipline
    MULTIPLE_DRIVERS already established)."""
    exact = [c.connected_to for c in instance.connections if c.instance_port == base_name and c.connected_to is not None]
    if exact:
        return exact
    indexed = sorted(
        (c for c in instance.connections if c.instance_port.startswith(f"{base_name}[") and c.connected_to is not None),
        key=lambda c: c.instance_port,
    )
    return [c.connected_to for c in indexed]


def _interface_instance_peers(ctx: _TraceContext, iface_instance_path: str, signal_name: str) -> list[tuple[str, Endpoint]]:
    """(role, Endpoint) for every sibling instance, at `iface_instance_path`'s
    own parent scope, that binds an interface-typed port to this same
    interface instance and has a resolvable modport direction for
    `signal_name` -- see this section's own "(b) PEER LOOKUP" docstring
    above. `role` is "driver" (modport marks it OUT) or "sink" (IN); a
    sibling with no resolvable modport, or whose modport doesn't mention
    `signal_name` at all, is silently excluded (an honest "can't
    determine," not a guess) -- e.g. aura's own crossbar (`u_fabric`)
    binds its `mst_port`/`slv_port` arrays with NO modport at all (`the
    crossbar picks the modport` per its own source comment), so it's
    correctly never offered as a driver/sink candidate here."""
    parent_path = _parent_path(iface_instance_path)
    if parent_path is None:
        return []
    parent_node = ctx.node_by_path.get(parent_path)
    if parent_node is None:
        return []
    iface_instance_name = iface_instance_path[len(parent_path) + 1 :]
    peers: list[tuple[str, Endpoint]] = []
    for inst in parent_node.definition.instances:
        inst_defn = ctx.design.definition(inst.definition_id)
        iface_ports_by_base = {ip.name: ip for ip in inst_defn.interface_ports}
        for conn in inst.connections:
            if conn.connected_to != iface_instance_name:
                continue
            iface_port = iface_ports_by_base.get(_base_iface_port_name(conn.instance_port))
            if iface_port is None:
                continue
            effective_modport = _effective_modport(iface_port)
            if effective_modport is None:
                continue
            target_defn = _find_interface_definition(ctx.design, iface_port.interface_name)
            if target_defn is None:
                continue
            direction = _modport_direction(target_defn, effective_modport, signal_name)
            if direction == PortDirection.OUT:
                role = "driver"
            elif direction == PortDirection.IN:
                role = "sink"
            else:
                continue
            # `conn.instance_port` (not `iface_port.name`) so an
            # ARRAY-INDEXED binding (e.g. "mst_port[0]") reports the
            # precise element actually connected here, not the bare,
            # ambiguous base name.
            peers.append((role, Endpoint(f"{parent_path}/{inst.name}", f"{conn.instance_port}.{signal_name}")))
    return peers


def _base_iface_port_name(iface_port_name: str) -> str:
    """Strips a possible "[i]" index suffix -- Definition.interface_ports
    is always keyed by the port's own BASE name (pyslang gives exactly
    ONE InterfacePortSymbol regardless of array size, see InterfacePort.
    array_size's own docstring), even when the compound signal reference
    itself is index-qualified ("mst_port[0].awvalid")."""
    return iface_port_name.split("[", 1)[0]


def _trace_driver_interface_member(ctx: _TraceContext, node: HierarchyNode, path: str, signal: str, stack2: tuple[Endpoint, ...]) -> DriverResult:
    iface_port_name, inner_signal = signal.split(".", 1)
    iface_port = next((ip for ip in node.definition.interface_ports if ip.name == _base_iface_port_name(iface_port_name)), None)
    if iface_port is None or node.instance is None:
        # Unresolvable reference, or the design's own top-level module
        # exposing an interface port directly at its own boundary (no
        # instantiation exists to look up a connection from) -- flagged,
        # not guessed at.
        return DriverResult(TraceKind.UNDRIVEN, endpoint=Endpoint(path, signal))
    parent_path = _parent_path(path)
    targets = _connected_targets_for_iface_port(node.instance, iface_port_name) if parent_path is not None else []
    if not targets:
        return DriverResult(TraceKind.UNDRIVEN, endpoint=Endpoint(path, signal))
    parent_node = ctx.node_by_path.get(parent_path)
    parent_iface_port_names = {ip.name for ip in parent_node.definition.interface_ports} if parent_node is not None else set()
    results = []
    for target in targets:
        if target in parent_iface_port_names:
            # `target` is itself an interface PORT of the PARENT scope
            # (a FORWARDING port, e.g. `dsp_mem`'s own `wr_bus`, bound
            # one level further up to a genuinely local instance) --
            # real, confirmed multi-hop gap found via real `aura` design
            # use: descending as if `target` were a concrete CHILD
            # instance (the branch below) fabricates a hierarchy path no
            # real node exists at ("dsp_mem_instance_path/wr_bus" --
            # `wr_bus` is a PORT, not a child of dsp_mem). Staying at
            # THIS SAME parent scope and re-qualifying as
            # "wr_bus.inner_signal" lets the "." dispatch in
            # _trace_driver() correctly recognize it as a port in its
            # own right and continue the ascension chain from there,
            # exactly like the original (single-hop) port case already
            # does one level down.
            results.append(_trace_driver(ctx, parent_path, f"{target}.{inner_signal}", stack2))
        else:
            results.append(_trace_driver(ctx, f"{parent_path}/{target}", inner_signal, stack2))
    return _merge_driver_results(results)


def _trace_receivers_interface_member(ctx: _TraceContext, node: HierarchyNode, path: str, signal: str, stack2: tuple[Endpoint, ...]) -> ReceiverResult:
    iface_port_name, inner_signal = signal.split(".", 1)
    iface_port = next((ip for ip in node.definition.interface_ports if ip.name == _base_iface_port_name(iface_port_name)), None)
    if iface_port is None or node.instance is None:
        return ReceiverResult(TraceKind.FLOATING)
    parent_path = _parent_path(path)
    targets = _connected_targets_for_iface_port(node.instance, iface_port_name) if parent_path is not None else []
    if not targets:
        return ReceiverResult(TraceKind.FLOATING)
    parent_node = ctx.node_by_path.get(parent_path)
    parent_iface_port_names = {ip.name for ip in parent_node.definition.interface_ports} if parent_node is not None else set()
    endpoints: list[Endpoint] = []
    loops: list[tuple[Endpoint, ...]] = []
    for target in targets:
        # Mirrors the driver-side fix immediately above -- a forwarding
        # PORT of the parent scope is traced at that SAME scope level
        # (re-qualified), never descended into as if it were a concrete
        # child instance.
        if target in parent_iface_port_names:
            sub = _trace_receivers(ctx, parent_path, f"{target}.{inner_signal}", stack2)
        else:
            sub = _trace_receivers(ctx, f"{parent_path}/{target}", inner_signal, stack2)
        if sub.kind == TraceKind.LOOP:
            loops.extend(sub.loops)
        elif sub.kind == TraceKind.RESOLVED:
            endpoints.extend(sub.endpoints)
    deduped = _dedup_endpoints(endpoints)
    if deduped:
        return ReceiverResult(TraceKind.RESOLVED, endpoints=deduped, loops=tuple(loops))
    if loops:
        return ReceiverResult(TraceKind.LOOP, loops=tuple(loops))
    return ReceiverResult(TraceKind.FLOATING)


def trace_driver(design: Design, path: str, signal: str) -> DriverResult:
    """Traces `signal` (a port or net name) at hierarchy `path` BACKWARD
    to its true driver, walking through combinational pass-through
    chains (ContinuousAssign) and across arbitrary hierarchy depth.

    Explicitly out of scope, flagged not silently mishandled: `inout`
    ports (PortDirection.INOUT) -- net_group() tags them "unknown" and
    neither driver nor sink resolution ever matches that role, so an
    inout-only path resolves as UNDRIVEN rather than being traced
    (tristate/bus semantics genuinely need a different model than plain
    single-driver/single-direction tracing)."""
    ctx = _build_context(design)
    return _trace_driver(ctx, path, signal, ())


def trace_receivers(design: Design, path: str, signal: str) -> ReceiverResult:
    """The forward mirror of trace_driver() -- see its own docstring.
    Plural by construction: fan-out is normal, not exceptional, so
    unlike trace_driver() there is no MULTIPLE_DRIVERS-equivalent kind
    here -- every distinct resolved sink is simply collected."""
    ctx = _build_context(design)
    return _trace_receivers(ctx, path, signal, ())


def flatten_design(design: Design) -> FlattenedConnectivity:
    """The full, batched trace -- every port of every hierarchy node,
    driver AND receivers, computed once (memoized within this one
    pass via a single shared _TraceContext) -- this is what "run at
    Elaborate time" means for the real backend: called once, right
    after a successful elaborate(), not per-port on demand.

    `drivers`/`receivers` stay keyed by PORT only (matching this
    function's own "every port" framing above and its real consumer,
    the PortList UI, which never displays a bare internal net) -- but
    a combinational loop or multi-driver net can exist ENTIRELY among
    internal nets, never touching a port at all (every ContinuousAssign
    target/source is always either a port or a net -- there is no third
    kind of named signal in this IR -- so ports+nets together are a
    complete scan for warning purposes). Those are traced too, purely
    to surface warnings, and never added to the two port-keyed dicts.
    Loop results are deliberately NOT memoized (see _trace_driver's own
    stack-relative handling), so the identical loop can otherwise be
    rediscovered and reported twice -- once reached via a port, once via
    a net -- with a different (but equivalent, rotated) cycle tuple each
    time; deduplicated below by the loop's own endpoint SET rather than
    its exact tuple order."""
    ctx = _build_context(design)
    drivers: dict[Endpoint, DriverResult] = {}
    receivers: dict[Endpoint, ReceiverResult] = {}
    warnings: list[ConnectivityWarning] = []
    seen_warnings: set[tuple] = set()

    def _record(warning: ConnectivityWarning) -> None:
        key = (warning.kind, frozenset(warning.detail)) if warning.kind == TraceKind.LOOP else (warning.kind, warning.endpoint, warning.detail)
        if key not in seen_warnings:
            seen_warnings.add(key)
            warnings.append(warning)

    for node in design.walk():
        for port in node.definition.ports:
            ep = Endpoint(node.path, port.name)
            dr = _trace_driver(ctx, node.path, port.name, ())
            drivers[ep] = dr
            if dr.kind == TraceKind.LOOP and dr.cycle is not None:
                _record(ConnectivityWarning(TraceKind.LOOP, ep, dr.cycle))
            elif dr.kind == TraceKind.MULTIPLE_DRIVERS and dr.candidates is not None:
                _record(ConnectivityWarning(TraceKind.MULTIPLE_DRIVERS, ep, dr.candidates))

            rr = _trace_receivers(ctx, node.path, port.name, ())
            receivers[ep] = rr
            for cycle in rr.loops:
                _record(ConnectivityWarning(TraceKind.LOOP, ep, cycle))

        for net in node.definition.nets:
            ep = Endpoint(node.path, net.name)
            dr = _trace_driver(ctx, node.path, net.name, ())
            if dr.kind == TraceKind.LOOP and dr.cycle is not None:
                _record(ConnectivityWarning(TraceKind.LOOP, ep, dr.cycle))
            elif dr.kind == TraceKind.MULTIPLE_DRIVERS and dr.candidates is not None:
                _record(ConnectivityWarning(TraceKind.MULTIPLE_DRIVERS, ep, dr.candidates))

            rr = _trace_receivers(ctx, node.path, net.name, ())
            for cycle in rr.loops:
                _record(ConnectivityWarning(TraceKind.LOOP, ep, cycle))

        for iface_port in node.definition.interface_ports:
            # Every signal the bound interface actually carries (its own
            # formal ports + internal nets -- ports/nets are the same
            # regardless of which modport view a given consumer sees them
            # through, so this doesn't need per-consumer modport
            # resolution the way _interface_port_rows() does for
            # display). Unresolvable (interface_name doesn't match any
            # elaborated interface Definition) -- skipped, not guessed at.
            target_defn = _find_interface_definition(design, iface_port.interface_name)
            if target_defn is None:
                continue
            member_names = [p.name for p in target_defn.ports] + [n.name for n in target_defn.nets]
            # An ARRAYED interface port (`array_size` set) names N
            # genuinely DIFFERENT physical signals, one per index (each
            # wired to a separate interface instance) -- enumerated
            # per-index ("mst_port[0]", "mst_port[1]", ...) rather than
            # once under the bare base name, which would otherwise merge
            # unrelated per-index results together as if they were one
            # shared signal (a real, confirmed bug found via real `aura`
            # design use: a bare "mst_port.awaddr" trace merged index 0's
            # genuinely resolved driver with index 1's genuinely UNDRIVEN
            # one into a misleading single-candidate MULTIPLE_DRIVERS
            # result).
            port_names = [f"{iface_port.name}[{i}]" for i in range(iface_port.array_size)] if iface_port.array_size else [iface_port.name]
            for port_name in port_names:
                for member_name in member_names:
                    compound = f"{port_name}.{member_name}"
                    ep = Endpoint(node.path, compound)
                    dr = _trace_driver(ctx, node.path, compound, ())
                    drivers[ep] = dr
                    if dr.kind == TraceKind.LOOP and dr.cycle is not None:
                        _record(ConnectivityWarning(TraceKind.LOOP, ep, dr.cycle))
                    elif dr.kind == TraceKind.MULTIPLE_DRIVERS and dr.candidates is not None:
                        _record(ConnectivityWarning(TraceKind.MULTIPLE_DRIVERS, ep, dr.candidates))

                    rr = _trace_receivers(ctx, node.path, compound, ())
                    receivers[ep] = rr
                    for cycle in rr.loops:
                        _record(ConnectivityWarning(TraceKind.LOOP, ep, cycle))
    return FlattenedConnectivity(drivers=drivers, receivers=receivers, warnings=tuple(warnings))


def _merge_driver_results(results: list[DriverResult]) -> DriverResult:
    """Collects ALL driver candidates before classifying -- never stops
    at the first one found. A single, unique resolved endpoint (every
    candidate agreeing) collapses to RESOLVED; anything else (more than
    one distinct endpoint, or any candidate that isn't itself cleanly
    RESOLVED) surfaces as MULTIPLE_DRIVERS rather than silently picking
    one -- per the explicit "never silently select a driver" requirement."""
    if len(results) == 1:
        return results[0]
    resolved_endpoints = _dedup_endpoints([r.endpoint for r in results if r.kind == TraceKind.RESOLVED and r.endpoint is not None])
    if len(resolved_endpoints) == 1 and all(r.kind == TraceKind.RESOLVED for r in results):
        return DriverResult(TraceKind.RESOLVED, endpoint=resolved_endpoints[0])
    return DriverResult(TraceKind.MULTIPLE_DRIVERS, candidates=resolved_endpoints)


def _trace_driver(ctx: _TraceContext, path: str, signal: str, stack: tuple[Endpoint, ...]) -> DriverResult:
    here = Endpoint(path, signal)
    if here in ctx.driver_memo:
        return ctx.driver_memo[here]
    if here in stack:
        cycle = stack[stack.index(here) :] + (here,)
        return DriverResult(TraceKind.LOOP, cycle=cycle)  # stack-relative -- deliberately not memoized
    stack2 = stack + (here,)

    node = ctx.node_by_path.get(path)
    if node is None:
        result = DriverResult(TraceKind.UNDRIVEN, endpoint=here)
        ctx.driver_memo[here] = result
        return result
    defn = node.definition

    if defn.is_black_box:
        # Real, confirmed bug (found via real design use, Port List's own
        # Driver/Receiver Supply Set columns): a macro's own OUTPUT pin
        # is always its own terminal driver -- the macro's own ports
        # carry no continuous_assigns/instances to trace through
        # regardless, so treating it as RESOLVED-here is structurally
        # necessary for that direction, not just a shortcut. But an
        # INPUT pin's driver is NOT the macro itself -- it's whatever
        # EXTERNAL logic feeds it, exactly like any non-macro hierarchy
        # block's own input boundary port. Unconditionally treating
        # every macro pin as its own driver (the previous behavior) made
        # an input pin's Driver column silently identical to its own
        # Receiver column, always showing the macro's own Power Domain
        # instead of the real upstream driver. An INPUT pin falls
        # through to the ordinary IN-boundary-port ascension logic below
        # instead (never mismatched with a black box's own INOUT/
        # unresolvable-port case, which stays terminal here -- tristate
        # semantics are explicitly out of scope, same as elsewhere in
        # this module).
        this_port = next((p for p in defn.ports if p.name == signal), None)
        if this_port is None or this_port.direction != PortDirection.IN:
            result = DriverResult(TraceKind.RESOLVED, endpoint=here)
            ctx.driver_memo[here] = result
            return result

    # Checked BEFORE the interface-member branch below -- real, confirmed
    # ordering bug found via real `aura` design use: a LOCALLY-declared
    # interface instance's own member (e.g. `sram_bank`'s own
    # `assign bus.req = sel & req;`, `bus` a plain local instance, not a
    # port) is legitimately driven by a continuous assign right here in
    # THIS scope -- but `signal` ("bus.req") still contains a "." the
    # same way a PORT-rooted interface member does, so the old ordering
    # unconditionally routed it into _trace_driver_interface_member()
    # (which only ever ascends via a PORT's own connection to the parent
    # scope) before this scope's own `continuous_assigns` was ever
    # consulted, permanently dead-ending at UNDRIVEN despite a real local
    # driver sitting right there. A dotted signal that ISN'T a local
    # continuous-assign target (the ordinary port case, e.g. `bus.clk` on
    # `sram_1rw_wrap` itself) still falls through to the interface-member
    # branch exactly as before, since `matching_assigns` is empty there.
    # sort_key pairs each matching assign with the "[N]" suffix (if any)
    # that must be RE-ATTACHED to its own `source` before recursing --
    # empty for an exact-target match (the assign already answers this
    # exact query), the original query's own suffix for a whole-vector
    # fallback match (see the "Bug fix" comment below).
    matching_assigns: list[tuple[ContinuousAssign, str]] = [(ca, "") for ca in defn.continuous_assigns if ca.target == signal]
    # Bug fix (found via real `generate`-block validation): a constant-
    # indexed query ("d_next[0]") must also match a WHOLE-vector
    # continuous-assign target ("d_next", e.g. `assign d_next = enable ?
    # data_i : data_o;`) -- the exact same "merge whole-vector members"
    # gap net_group() already fixes for connections (see its own
    # docstring), for continuous-assign targets instead. Unconditionally
    # merged, not a conditional fallback, matching net_group()'s own
    # reasoning: a real design's own `target` is either the exact indexed
    # form or the bare whole-vector form for a given conceptual signal,
    # never both, so there's no double-counting risk. When a matched
    # whole-vector assign is itself a TRUE pass-through (`ca.source` set,
    # e.g. `assign d_next = d_i;`), the SAME "[N]" suffix this query
    # itself carries must be re-attached to `ca.source` before recursing
    # -- otherwise "d_next[0]"'s own driver search would silently descend
    # into the WHOLE `d_i` vector instead of `d_i[0]` specifically, the
    # exact same "keep asking the same specific-bit question all the way
    # down" requirement net_group()'s own docstring already establishes
    # for connections.
    base_name = _strip_constant_index(signal)
    if base_name is not None:
        suffix = signal[len(base_name) :]
        matching_assigns += [(ca, suffix) for ca in defn.continuous_assigns if ca.target == base_name]
    if matching_assigns:
        results = [
            DriverResult(TraceKind.RESOLVED, endpoint=here) if ca.source is None else _trace_driver(ctx, path, f"{ca.source}{suffix}", stack2)
            for ca, suffix in matching_assigns
        ]
        result = _merge_driver_results(results)
        ctx.driver_memo[here] = result
        return result

    if "." in signal:
        # A compound "iface_port.signal" reference -- see this module's
        # own "Interface-aware tracing" section above.
        result = _trace_driver_interface_member(ctx, node, path, signal, stack2)
        ctx.driver_memo[here] = result
        return result

    port = next((p for p in defn.ports if p.name == signal), None)
    if port is not None and port.direction in (PortDirection.IN, PortDirection.INOUT):
        # `signal` is THIS Definition's own boundary input -- its driver
        # lives in the parent scope (or nowhere at all, if this IS the
        # design's own top with no parent to ascend into).
        if node.instance is None:
            result = DriverResult(TraceKind.RESOLVED, endpoint=here)  # the design's own top-level input boundary -- caller identifies via path == design.top
            ctx.driver_memo[here] = result
            return result
        parent_path = _parent_path(path)
        net_name = _connected_net_for_instance_port(node.instance, signal) if parent_path is not None else None
        if parent_path is None or net_name is None:
            result = DriverResult(TraceKind.UNDRIVEN, endpoint=here)
            ctx.driver_memo[here] = result
            return result
        result = _trace_driver(ctx, parent_path, net_name, stack2)
        ctx.driver_memo[here] = result
        return result

    # `signal` is an internal net, or this Definition's own OUTPUT
    # boundary port -- either way, its driver (if any) lives WITHIN this
    # scope: a child instance's own output port, found via net_group().
    group = net_group(ctx.design, defn, signal)
    driver_members = [m for m in group.members if m.role == "driver"]
    if not driver_members:
        if defn.is_interface:
            # This Definition's own internal net has no local driver --
            # unsurprising, an interface has no instances of its own to
            # drive it (see this module's own "(b) PEER LOOKUP" docstring
            # above). The real driver, if any, is a sibling instance at
            # THIS interface instance's own parent scope.
            peers = [ep for role, ep in _interface_instance_peers(ctx, path, signal) if role == "driver"]
            if peers:
                result = _merge_driver_results([DriverResult(TraceKind.RESOLVED, endpoint=ep) for ep in _dedup_endpoints(peers)])
                ctx.driver_memo[here] = result
                return result
            # No peer module instance drives this interface member via a
            # modport-qualified connection -- also check whether the
            # ENCLOSING scope drives it directly via a plain continuous
            # assign targeting "instanceName.signal" (e.g. `sram_bank`'s
            # own `assign bus.req = sel & req;`, where `bus` is THIS very
            # interface instance's own local name in that scope). Real,
            # confirmed gap found via real `aura` design use: an interface
            # instance can be driven straight from its enclosing scope,
            # not just from a peer module instance connected through a
            # modport port -- the peer-lookup above has no visibility
            # into a plain assign at all. Only "wins" (returns) when this
            # actually resolves something -- an UNDRIVEN result here just
            # falls through to the ordinary sequential-elements/UNDRIVEN
            # handling below, unchanged.
            parent_path = _parent_path(path)
            if parent_path is not None:
                local_name = path.rsplit("/", 1)[-1]
                fallback = _trace_driver(ctx, parent_path, f"{local_name}.{signal}", stack2)
                if fallback.kind != TraceKind.UNDRIVEN:
                    ctx.driver_memo[here] = fallback
                    return fallback
        if any(signal == element.output_signal for element in defn.sequential_elements):
            # Real, confirmed gap (found tracing a cross-hierarchy signal
            # backward from a receiving instance's own input port, e.g.
            # sub0.i_q, all the way up through the parent's own wiring to
            # the flop's own output net, e.g. top's `q_int`): a flop
            # DRIVES its own output_signal -- that's the entire point of
            # a flop -- but nothing marked it as a driving entity on the
            # DRIVER side (unlike the receiver side, which already treats
            # clock_ref/reset_ref/set_ref/data_ref as legitimate
            # receivers). Without this, `q_int` traced back correctly
            # (never stopping short at the wrong hierarchy level, never
            # guessing) but then reported itself UNDRIVEN, even though a
            # real, identifiable flop drives it. Mirrors how a black-box
            # macro's own output pin is ALREADY always its own terminal
            # driver above -- a flop is exactly as opaque here: we know
            # IT drives `signal`, but (same "no data-path/next-state
            # EXPRESSION modeling" boundary SequentialElement.data_ref's
            # own docstring already draws) we don't trace WHAT computes
            # the next-state value beyond that, so this is terminal, not
            # a further hop.
            result = DriverResult(TraceKind.RESOLVED, endpoint=here)
            ctx.driver_memo[here] = result
            return result
        result = DriverResult(TraceKind.UNDRIVEN, endpoint=here)
        ctx.driver_memo[here] = result
        return result
    results = []
    for member in driver_members:
        if member.is_parent_own_port:
            # Structurally unreachable in practice given the IN-port
            # branch above already intercepts every case where `signal`
            # itself names one of this Definition's own input ports --
            # kept as a defensive, correct fallback (not dead code
            # removed outright) in case net_group() is ever reused
            # against a signal that reaches this point differently.
            results.append(DriverResult(TraceKind.RESOLVED, endpoint=Endpoint(path, member.port_name)))
        else:
            child_path = f"{path}/{member.instance_name}"
            results.append(_trace_driver(ctx, child_path, member.port_name, stack2))
    result = _merge_driver_results(results)
    ctx.driver_memo[here] = result
    return result


def _trace_receivers(ctx: _TraceContext, path: str, signal: str, stack: tuple[Endpoint, ...]) -> ReceiverResult:
    here = Endpoint(path, signal)
    if here in ctx.receiver_memo:
        return ctx.receiver_memo[here]
    if here in stack:
        cycle = stack[stack.index(here) :] + (here,)
        return ReceiverResult(TraceKind.LOOP, loops=(cycle,))  # stack-relative -- deliberately not memoized
    stack2 = stack + (here,)

    node = ctx.node_by_path.get(path)
    if node is None:
        result = ReceiverResult(TraceKind.FLOATING)
        ctx.receiver_memo[here] = result
        return result
    defn = node.definition

    if defn.is_black_box:
        # Mirrors the driver-side fix immediately above: a macro's own
        # INPUT pin is always its own terminal receiver (the macro
        # itself consumes it), but an OUTPUT pin's real receiver(s) are
        # EXTERNAL -- falls through to the ordinary OUT-boundary-port
        # ascension logic below instead of being wrongly reported as its
        # own receiver.
        this_port = next((p for p in defn.ports if p.name == signal), None)
        if this_port is None or this_port.direction != PortDirection.OUT:
            result = ReceiverResult(TraceKind.RESOLVED, endpoints=(here,))
            ctx.receiver_memo[here] = result
            return result

    # Checked AFTER the ordinary mechanisms below now (not an unconditional
    # early return) -- real, confirmed ordering bug found via real `aura`
    # design use, mirroring the driver-side fix in _trace_driver(): a
    # dotted signal ("bus.rdata") can legitimately be found by the
    # ordinary continuous_assigns loop right below (e.g. `sram_bank`'s
    # own `assign rdata = bus.rdata;`, `ca.source == "bus.rdata"`) --
    # the old unconditional early return skipped that loop entirely for
    # ANY dotted signal, permanently missing this case. The ordinary
    # port/net_group/sequential-elements checks below are naturally
    # no-ops for a dotted signal (none of their names ever contain a
    # "."), so letting them run first for a dotted signal too is safe.
    endpoints: list[Endpoint] = []
    loops: list[tuple[Endpoint, ...]] = []

    def _absorb(sub: ReceiverResult) -> None:
        if sub.kind == TraceKind.LOOP:
            loops.extend(sub.loops)
        elif sub.kind == TraceKind.RESOLVED:
            endpoints.extend(sub.endpoints)
        # FLOATING contributes nothing -- a genuine dead end, not a real receiver.

    for ca in defn.continuous_assigns:
        if ca.source == signal:
            _absorb(_trace_receivers(ctx, path, ca.target, stack2))
        elif ca.source is None and signal in ca.referenced_signals:
            # Real, confirmed bug (found tracing a signal that feeds
            # INTO real logic living directly in a PARENT hierarchy
            # level, positioned after a child instance's own output --
            # e.g. `sleep_int` in top-level `assign o_sleep =
            # ~sleep_int;`, fed by a child instance's own output):
            # `signal` is consumed by REAL LOGIC here -- a completely
            # real, legitimate receiver fact (this module.py's own
            # docstring on ContinuousAssign.referenced_signals) -- but
            # deliberately terminal at `here` (THIS signal's own
            # identity), NOT `Endpoint(path, ca.target)` (the real
            # logic's own output). Using the real logic's OWN target as
            # the receiver identity would make it INDISTINGUISHABLE from
            # that exact same Endpoint's OTHER, unrelated meaning: when
            # `ca.target` also happens to be a design-root port name
            # (Verilog forbids assigning to an INPUT port, but an OUTPUT
            # port's own name colliding with its own real-logic target
            # is completely ordinary and common), that identical
            # Endpoint is ALSO the genuine "flows out of the design"
            # terminal boundary reached by a completely different,
            # unrelated query (that port's own direct receiver trace).
            # Two genuinely different facts must not collapse onto one
            # shared, ambiguous Endpoint value -- `here` is always
            # unique to `signal`'s own identity, never collides.
            endpoints.append(here)

    port = next((p for p in defn.ports if p.name == signal), None)
    if port is not None and port.direction in (PortDirection.OUT, PortDirection.INOUT):
        if node.instance is None:
            endpoints.append(here)  # the design's own top-level output boundary -- caller identifies via path == design.top
        else:
            parent_path = _parent_path(path)
            net_name = _connected_net_for_instance_port(node.instance, signal) if parent_path is not None else None
            if parent_path is not None and net_name is not None:
                _absorb(_trace_receivers(ctx, parent_path, net_name, stack2))

    group = net_group(ctx.design, defn, signal)
    for member in group.members:
        if member.role != "sink":
            continue
        if member.is_parent_own_port:
            # Real, confirmed bug (found via a live 3-level pass-through
            # trace, not assumed): a net_group() sink member with
            # `is_parent_own_port=True` ALWAYS has `port_name == signal`
            # by construction (net_group() only ever matches this
            # Definition's own ports by exact name equality against the
            # `signal` being queried) -- meaning the direct OUT/INOUT-
            # boundary-port branch above has ALREADY handled this exact
            # port (same name, same direction), including correctly
            # ASCENDING into the parent scope to keep tracing forward.
            # Re-registering it here as a bare, un-ascended
            # Endpoint(path, member.port_name) is not a harmless
            # duplicate -- it wrongly reports this intermediate hierarchy
            # boundary itself as a TERMINAL receiver, short-circuiting
            # the real downstream trace the branch above already
            # continues. Skipped entirely; nothing further to do here.
            continue
        child_path = f"{path}/{member.instance_name}"
        _absorb(_trace_receivers(ctx, child_path, member.port_name, stack2))

    for element in defn.sequential_elements:
        # Real, confirmed gap (found via a real design's own top-level
        # `always_ff @(posedge clk or negedge reset_n)`): a flop/latch's
        # own clock/reset/set/data signal is a completely real,
        # legitimate receiver -- the flop itself consumes it -- but
        # ContinuousAssign-based tracing has zero visibility into
        # procedural (always_ff/always) blocks at all, so without this,
        # a signal that ONLY ever feeds a sequential element (never a
        # plain continuous assign or child instance) was silently
        # reported FLOATING. SequentialElement has no hierarchy path of
        # its own -- it's metadata on THIS Definition, at THIS SAME
        # path -- so its own `output_signal` (already real IR data, a
        # Net or Port on this same Definition) stands in for "the flop
        # itself" as the terminal receiver identity. `data_ref` (see its
        # own docstring on SequentialElement) is included here on the
        # SAME footing as clock/reset/set -- reversed from an earlier
        # decision that excluded it; general next-state EXPRESSION
        # modeling (an operator, a mux) remains out of scope, but the
        # narrow bare-signal case is now a real, captured structural
        # fact just like the other three.
        if signal in (element.clock_ref, element.reset_ref, element.set_ref, element.data_ref):
            endpoints.append(Endpoint(path, element.output_signal))

    if not endpoints and not loops and "." in signal:
        # The primary interface-member mechanism -- ascending via a
        # PORT's own connection to the parent scope (e.g. `bus.clk` on
        # `sram_1rw_wrap`, "bus" a real interface port) -- tried only
        # once nothing ordinary above already resolved this signal.
        _absorb(_trace_receivers_interface_member(ctx, node, path, signal, stack2))

    if not endpoints and not loops and defn.is_interface:
        # Mirrors _trace_driver()'s own peer-lookup fallback above -- this
        # Definition's own internal net has no local sink (an interface
        # has no instances of its own to consume it); the real
        # receiver(s), if any, are sibling instances at THIS interface
        # instance's own parent scope.
        endpoints.extend(ep for role, ep in _interface_instance_peers(ctx, path, signal) if role == "sink")

    if not endpoints and not loops and defn.is_interface:
        # Mirrors the driver-side fallback in _trace_driver(): no peer
        # module instance receives this interface member via a
        # modport-qualified connection -- also check whether the
        # ENCLOSING scope reads it directly via a plain continuous assign
        # SOURCED from "instanceName.signal" (e.g. `sram_bank`'s own
        # `assign rdata = bus.rdata;`, where `bus` is THIS very interface
        # instance's own local name in that scope). Real, confirmed gap
        # found via real `aura` design use: a macro's own output pin
        # forward-traces through an internal net to a `bus.rdata`-shaped
        # ContinuousAssign target on the interface-PORT-owning Definition
        # but previously dead-ended there, since nothing connected
        # "bus.rdata" onward to the enclosing scope's own output.
        parent_path = _parent_path(path)
        if parent_path is not None:
            local_name = path.rsplit("/", 1)[-1]
            _absorb(_trace_receivers(ctx, parent_path, f"{local_name}.{signal}", stack2))

    if not endpoints and not loops and node.instance is not None and port is not None and port.direction == PortDirection.IN:
        # Real, confirmed bug (found via the SAME instance's own Port
        # List showing a DIFFERENT Driver/Receiver answer than its
        # parent's Port List reported for the identical, single-hop-
        # wired signal -- a genuine "viewing angle" inconsistency, not a
        # tracing gap): a child instance's own INPUT port is ALWAYS
        # itself a legitimate receiver, regardless of whether that
        # instance's own internal body does anything FURTHER with it --
        # "nothing consumes it deeper inside this instance" is not the
        # same claim as "nothing receives it at all". This used to be
        # applied ONLY at the ONE call site where a PARENT's own
        # net_group() sink loop happened to cross into a child instance
        # and got FLOATING back -- correct for that specific path, but
        # meant the exact same Endpoint, queried DIRECTLY (e.g. from
        # flatten_design()'s own per-node port loop, or this instance's
        # own Port List tab), fell through to a bare, unfixed-up
        # FLOATING instead. The underlying trace result for a given net
        # must be identical regardless of which node's own Port List
        # happens to be asking -- moved here, as an intrinsic property
        # of THIS endpoint's own resolution, not a property of whoever
        # called in. Scoped to a real, non-root instance (`node.instance
        # is not None` -- the design's own top-level input boundary has
        # its own, separate "no receiver found -> FLOATING" semantics,
        # unrelated to this) with an actual INPUT-direction port.
        endpoints.append(here)

    deduped = _dedup_endpoints(endpoints)
    if deduped:
        result = ReceiverResult(TraceKind.RESOLVED, endpoints=deduped, loops=tuple(loops))
    elif loops:
        result = ReceiverResult(TraceKind.LOOP, loops=tuple(loops))
    else:
        result = ReceiverResult(TraceKind.FLOATING)
    ctx.receiver_memo[here] = result
    return result
