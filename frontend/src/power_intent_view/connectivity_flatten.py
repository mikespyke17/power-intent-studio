"""Connectivity flattening -- a genuine, separately-written DUPLICATE of
backend/elaborator/rtl/ir/flatten.py's own algorithm, operating on
frontend/src/shared/models/rtl.Design instead of the real backend IR. Per
CLAUDE.md's milestone-independence rule, frontend/ never imports backend/
-- this mirrors the same "duplicate, not import" precedent already
established for rtl.py itself (see that module's own docstring).

Traces a signal's TRUE driver (backward) and TRUE receiver(s) (forward)
across arbitrary hierarchy depth and combinational pass-through chains
(ContinuousAssign), driving the Port List tab's "Driver Supply Set"/
"Receiver Supply Set" columns. Pure RTL-structural concern here too --
zero knowledge of Power Domains/Supply Sets; a separate, later consumer
(properties_table_widget.py) resolves a RESOLVED Endpoint's owning cell
to a Power Domain's own Primary Supply Set.

Kept structurally identical to the backend module (same dataclass
shapes, same net_group()/trace_driver()/trace_receivers() algorithm,
same two real bugs already found and fixed there -- see the two
`is_parent_own_port`/FLOATING-fallback comments below, ported over
rather than re-discovered) so the two stay easy to reconcile if/when a
real Milestone-1g flattening pass or the Final Milestone integration
ever needs to compare them.

PortDirection.PG/PG_INTERNAL (frontend-only, no backend equivalent) fall
through to net_group()'s existing "unknown" role -- same treatment as a
genuine INOUT -- since a macro's own PG pins are excluded from the Port
List entirely (see properties_table_widget.py) and never actually reach
this module in practice, but nothing here assumes that and crashes if it
did.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Optional

from frontend.src.shared.models.rtl import Definition, Design, HierarchyNode, Instance, PortDirection


class TraceKind(Enum):
    RESOLVED = "resolved"
    UNDRIVEN = "undriven"
    FLOATING = "floating"
    LOOP = "loop"
    MULTIPLE_DRIVERS = "multiple_drivers"


@dataclass(frozen=True)
class Endpoint:
    path: str
    signal: str


@dataclass(frozen=True)
class DriverResult:
    kind: TraceKind
    endpoint: Optional[Endpoint] = None
    cycle: Optional[tuple[Endpoint, ...]] = None
    candidates: Optional[tuple[Endpoint, ...]] = None


@dataclass(frozen=True)
class ReceiverResult:
    kind: TraceKind
    endpoints: tuple[Endpoint, ...] = ()
    loops: tuple[tuple[Endpoint, ...], ...] = ()


@dataclass(frozen=True)
class ConnectivityWarning:
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
    role: str  # "driver" | "sink" | "unknown"
    is_parent_own_port: bool
    instance_name: Optional[str]
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
    """One Definition's own scope: which instance ports AND the
    Definition's own boundary ports share `net_name`. A boundary port's
    role is direction-INVERTED relative to internal flow (an IN boundary
    port is a DRIVER from inside this scope); a child instance's port is
    the mirror of its own declared direction (an OUT child port drives
    the net, an IN child port sinks it).

    A bit-indexed `net_name` ("clk_dom[4]") also MERGES in members found
    under the WHOLE, un-indexed vector's own name ("clk_dom") -- real,
    confirmed gap found via real `aura` design use (`clk_rst_gen`'s own
    per-domain `clk_dom[d]` continuous-assign targets, consumed one
    level up via a single whole-vector `.clk_dom(clk_dom)` port binding
    with no per-bit granularity on the CONNECTING side at all, while
    every actual CONSUMER references one specific bit via a constant
    element-select, e.g. `.clk(clk_dom[DOM_CPU])`) -- see the backend's
    own `flatten.py` net_group() for the full mechanism writeup, ported
    here structurally identically. Every member found via the
    whole-vector fallback has the SAME "[N]" index suffix re-attached to
    its own `port_name`, so descending into a driver/sink's own scope
    keeps asking the SAME specific-bit question all the way down the
    hierarchy."""
    members = list(_net_group_members(design, defn, net_name))
    base_name = _strip_constant_index(net_name)
    if base_name is not None:
        suffix = net_name[len(base_name) :]
        members.extend(
            replace(member, port_name=f"{member.port_name}{suffix}") for member in _net_group_members(design, defn, base_name)
        )
    return _NetGroup(net_name, tuple(members))


# --- Interface-aware tracing --------------------------------------------
# Ported from the real bug found and fixed in the backend module via real
# `aura` design use (an interface-heavy SoC using SV interfaces as its
# PRIMARY connectivity mechanism): net_group() above only ever matches a
# child instance's connection against `inst_defn.ports` -- every
# interface-typed connection was silently skipped. See
# backend/elaborator/rtl/ir/flatten.py's own "Interface-aware tracing"
# section for the full mechanism writeup (redirect into a bound interface
# instance via a compound "iface_port.signal" reference, plus a peer
# lookup across an interface instance's own parent-scope siblings using
# each one's modport-derived direction) -- reproduced here structurally
# identically, against this module's own mocked rtl.Design instead.


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
    a single-element bind gives exactly one unindexed connection; a
    whole-array bind was split into one connection per element
    (instance_port == "name[i]") -- returns every element's own
    connected_to in that case, sorted by index."""
    exact = [c.connected_to for c in instance.connections if c.instance_port == base_name and c.connected_to is not None]
    if exact:
        return exact
    indexed = sorted(
        (c for c in instance.connections if c.instance_port.startswith(f"{base_name}[") and c.connected_to is not None),
        key=lambda c: c.instance_port,
    )
    return [c.connected_to for c in indexed]


def _base_iface_port_name(iface_port_name: str) -> str:
    """Strips a possible "[i]" index suffix -- Definition.interface_ports
    is always keyed by the port's own BASE name."""
    return iface_port_name.split("[", 1)[0]


def _interface_instance_peers(ctx: _TraceContext, iface_instance_path: str, signal_name: str) -> list[tuple[str, Endpoint]]:
    """(role, Endpoint) for every sibling instance, at `iface_instance_path`'s
    own parent scope, that binds an interface-typed port to this same
    interface instance and has a resolvable modport direction for
    `signal_name`. `role` is "driver" (modport marks it OUT) or "sink"
    (IN); a sibling with no resolvable modport is silently excluded (an
    honest "can't determine," not a guess)."""
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
            peers.append((role, Endpoint(f"{parent_path}/{inst.name}", f"{conn.instance_port}.{signal_name}")))
    return peers


def _trace_driver_interface_member(ctx: _TraceContext, node: HierarchyNode, path: str, signal: str, stack2: tuple[Endpoint, ...]) -> DriverResult:
    iface_port_name, inner_signal = signal.split(".", 1)
    iface_port = next((ip for ip in node.definition.interface_ports if ip.name == _base_iface_port_name(iface_port_name)), None)
    if iface_port is None or node.instance is None:
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
            # real node exists at. Staying at THIS SAME parent scope and
            # re-qualifying as "wr_bus.inner_signal" lets the "." dispatch
            # in _trace_driver() correctly recognize it as a port in its
            # own right and continue the ascension chain from there.
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
    """Traces `signal` at hierarchy `path` BACKWARD to its true driver.
    `inout`/PG-role ports are explicitly out of scope (net_group() tags
    them "unknown", so they resolve as UNDRIVEN rather than being traced
    -- tristate semantics need a different model)."""
    ctx = _build_context(design)
    return _trace_driver(ctx, path, signal, ())


def trace_receivers(design: Design, path: str, signal: str) -> ReceiverResult:
    """The forward mirror of trace_driver() -- plural by construction,
    fan-out is normal, not exceptional."""
    ctx = _build_context(design)
    return _trace_receivers(ctx, path, signal, ())


def flatten_design(design: Design) -> FlattenedConnectivity:
    """The full batched trace -- every port AND internal net of every
    hierarchy node. `drivers`/`receivers` stay keyed by PORT only
    (matching the Port List UI, the only real consumer); internal nets
    are traced purely to surface LOOP/MULTIPLE_DRIVERS warnings that
    would otherwise be invisible if they never touch a port. This is
    Layer 1 of the two-layer caching design (see
    power_resolution_cache.py): computed once when a design loads,
    since RTL structure never changes after that point."""
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
            target_defn = _find_interface_definition(design, iface_port.interface_name)
            if target_defn is None:
                continue
            member_names = [p.name for p in target_defn.ports] + [n.name for n in target_defn.nets]
            # An ARRAYED interface port names N genuinely different
            # physical signals, one per index -- enumerated per-index
            # rather than once under the bare base name (which would
            # otherwise merge unrelated per-index results together as if
            # they were one shared signal).
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
        return DriverResult(TraceKind.LOOP, cycle=cycle)
    stack2 = stack + (here,)

    node = ctx.node_by_path.get(path)
    if node is None:
        result = DriverResult(TraceKind.UNDRIVEN, endpoint=here)
        ctx.driver_memo[here] = result
        return result
    defn = node.definition

    if defn.is_black_box:
        # Ported from the real bug fixed in the backend module (found via
        # real design use, Port List's own Driver/Receiver Supply Set
        # columns): a macro's own OUTPUT pin is always its own terminal
        # driver, but an INPUT pin's driver is EXTERNAL -- falls through
        # to the ordinary IN-boundary-port ascension logic below instead
        # of wrongly reporting the macro itself as its own input's
        # driver (which made Driver and Receiver show the identical
        # value for an input pin).
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
    matching_assigns = [ca for ca in defn.continuous_assigns if ca.target == signal]
    if matching_assigns:
        results = [
            DriverResult(TraceKind.RESOLVED, endpoint=here) if ca.source is None else _trace_driver(ctx, path, ca.source, stack2)
            for ca in matching_assigns
        ]
        result = _merge_driver_results(results)
        ctx.driver_memo[here] = result
        return result

    if "." in signal:
        result = _trace_driver_interface_member(ctx, node, path, signal, stack2)
        ctx.driver_memo[here] = result
        return result

    port = next((p for p in defn.ports if p.name == signal), None)
    if port is not None and port.direction in (PortDirection.IN, PortDirection.INOUT):
        if node.instance is None:
            result = DriverResult(TraceKind.RESOLVED, endpoint=here)
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

    group = net_group(ctx.design, defn, signal)
    driver_members = [m for m in group.members if m.role == "driver"]
    if not driver_members:
        if defn.is_interface:
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
            # Ported from the real bug fixed in the backend module (found
            # tracing a cross-hierarchy signal backward all the way up to
            # a flop's own output net, e.g. sub0.i_q -> top's `q_int`): a
            # flop DRIVES its own output_signal -- mirrors how a black-box
            # macro's own output pin is already always its own terminal
            # driver above. Terminal, not a further hop (same "no data-
            # path/next-state EXPRESSION modeling" boundary
            # SequentialElement.data_ref's own docstring already draws).
            result = DriverResult(TraceKind.RESOLVED, endpoint=here)
            ctx.driver_memo[here] = result
            return result
        result = DriverResult(TraceKind.UNDRIVEN, endpoint=here)
        ctx.driver_memo[here] = result
        return result
    results = []
    for member in driver_members:
        if member.is_parent_own_port:
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
        return ReceiverResult(TraceKind.LOOP, loops=(cycle,))
    stack2 = stack + (here,)

    node = ctx.node_by_path.get(path)
    if node is None:
        result = ReceiverResult(TraceKind.FLOATING)
        ctx.receiver_memo[here] = result
        return result
    defn = node.definition

    if defn.is_black_box:
        # Mirrors the driver-side fix immediately above: a macro's own
        # INPUT pin is always its own terminal receiver, but an OUTPUT
        # pin's real receiver(s) are EXTERNAL -- falls through to the
        # ordinary OUT-boundary-port ascension logic below.
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

    for ca in defn.continuous_assigns:
        if ca.source == signal:
            _absorb(_trace_receivers(ctx, path, ca.target, stack2))
        elif ca.source is None and signal in ca.referenced_signals:
            # Ported from the real bug fixed in the backend module
            # (found tracing a signal feeding INTO real logic living
            # directly in a PARENT hierarchy level, positioned after a
            # child instance's own output): `signal` is consumed by
            # real logic here -- a legitimate receiver -- but terminal
            # at `here` (THIS signal's own identity), NOT
            # Endpoint(path, ca.target), which would be indistinguishable
            # from that same Endpoint's own, unrelated "flows out of the
            # design" boundary meaning when ca.target happens to also be
            # a root-level port name (an OUTPUT port's own name
            # colliding with its own real-logic target is ordinary and
            # common).
            endpoints.append(here)

    port = next((p for p in defn.ports if p.name == signal), None)
    if port is not None and port.direction in (PortDirection.OUT, PortDirection.INOUT):
        if node.instance is None:
            endpoints.append(here)
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
            # Ported from the real bug fixed in the backend module: this
            # exact port was already handled (including ascending into
            # the parent) by the OUT/INOUT branch above -- re-registering
            # it here would wrongly report an intermediate hierarchy
            # boundary as a terminal receiver.
            continue
        child_path = f"{path}/{member.instance_name}"
        _absorb(_trace_receivers(ctx, child_path, member.port_name, stack2))

    for element in defn.sequential_elements:
        # Ported from the real bug fixed in the backend module (found via
        # a real design's own top-level `always_ff`): a flop/latch's own
        # clock/reset/set/data signal is a real, legitimate receiver --
        # the flop itself consumes it -- but ContinuousAssign-based
        # tracing has zero visibility into procedural blocks.
        # `output_signal` stands in for "the flop itself" as the
        # terminal receiver identity. `data_ref` included on the same
        # footing as clock/reset/set (reversed from an earlier decision
        # that excluded it -- see SequentialElement.data_ref's own
        # docstring); general next-state EXPRESSION modeling remains out
        # of scope.
        if signal in (element.clock_ref, element.reset_ref, element.set_ref, element.data_ref):
            endpoints.append(Endpoint(path, element.output_signal))

    if not endpoints and not loops and "." in signal:
        # The primary interface-member mechanism -- ascending via a
        # PORT's own connection to the parent scope (e.g. `bus.clk` on
        # `sram_1rw_wrap`, "bus" a real interface port) -- tried only
        # once nothing ordinary above already resolved this signal.
        _absorb(_trace_receivers_interface_member(ctx, node, path, signal, stack2))

    if not endpoints and not loops and defn.is_interface:
        endpoints.extend(ep for role, ep in _interface_instance_peers(ctx, path, signal) if role == "sink")

    if not endpoints and not loops and defn.is_interface:
        # Mirrors the driver-side fallback in _trace_driver(): no peer
        # module instance receives this interface member via a
        # modport-qualified connection -- also check whether the
        # ENCLOSING scope reads it directly via a plain continuous assign
        # SOURCED from "instanceName.signal" (e.g. `sram_bank`'s own
        # `assign rdata = bus.rdata;`, where `bus` is THIS very interface
        # instance's own local name in that scope). Real, confirmed gap
        # found via real `aura` design use: `u_macro`'s own `Q` pin
        # forward-traces through `q_macro` to `bus.rdata` (a real,
        # correctly-qualified ContinuousAssign target on sram_1rw_wrap by
        # this point) but previously dead-ended there, since nothing
        # connected "bus.rdata" onward to sram_bank's own `rdata` output.
        parent_path = _parent_path(path)
        if parent_path is not None:
            local_name = path.rsplit("/", 1)[-1]
            _absorb(_trace_receivers(ctx, parent_path, f"{local_name}.{signal}", stack2))

    if not endpoints and not loops and node.instance is not None and port is not None and port.direction == PortDirection.IN:
        # Ported from the real bug fixed in the backend module (found via
        # the SAME instance's own Port List showing a DIFFERENT Driver/
        # Receiver answer than its parent's Port List reported for the
        # identical, single-hop-wired signal -- a genuine "viewing
        # angle" inconsistency, not a tracing gap): a child instance's
        # own INPUT port is always itself a legitimate receiver. Moved
        # here from a single call site (only fired when a parent's own
        # net_group() sink loop happened to cross into this exact
        # child) to an intrinsic property of THIS endpoint's own
        # resolution -- the underlying trace result for a given net must
        # be identical regardless of which node's own Port List is
        # asking.
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
