"""Multi-hop interface-PORT-forwarding -- a real, confirmed gap found via
real `aura` design use, one level deeper than the local-interface-instance
gap tests/backend/test_verilog_interface_member_connections.py already
covers: `dsp_mem`'s own `.bus_a(wr_bus)` connection, where `wr_bus` is
ITSELF an interface PORT of `dsp_mem` (not a concrete local instance),
forwarded ANOTHER level up to a genuinely local instance `mem_wr` in
`dsp_mem`'s own PARENT, `dsp_subsys`.

pyslang's own `pc.ifaceConn`/`pc.expression.getSymbolReference()`
resolves straight through ANY number of such interface-PORT-to-
interface-PORT forwarding hops to the ULTIMATE bound instance's identity
directly (confirmed live) -- no "shallow" one-hop view exists anywhere
in its own API. Two distinct, compounding bugs this fixture isolates:

  1. Extraction (`_local_interface_identity_name()` in verilog/
     elaborate.py): naively using pyslang's own resolved name as
     `PortConnection.connected_to` produces a value ("mem_wr") not
     reachable from the connecting Definition's own local scope at all
     -- fixed by preferring, when available, the name this Definition's
     own `interface_identities` (its own ports + local interface
     instances) already knows this SAME resolved identity by ("wr_bus"
     here), letting the *existing* one-hop-at-a-time ascension continue
     correctly from there.
  2. Trace-time (`_trace_driver_interface_member()`/
     `_trace_receivers_interface_member()` in flatten.py, both here and
     in the frontend's own mirror): even with (1) fixed, descending into
     "parent_path/wr_bus" as if `wr_bus` were a concrete CHILD instance
     fabricates a hierarchy path no real node exists at -- `wr_bus` is a
     PORT of the parent scope, not a child of it. Fixed by staying at
     the SAME parent scope, re-qualified, whenever the target names one
     of the parent's own interface ports.

Fixture (`golden_rtl/verilog_interface_forwarding_chain/`) mirrors the
real `aura` shape at minimal scale: `leaf.sig` <- `mid`'s own
`bus_if.mem bus` PORT <- `outer`'s own `bus_if.mem bus` PORT (pure
forwarding, mirroring `dsp_mem`'s `wr_bus`) <- `top`'s own LOCAL `bus`
instance (mirroring `dsp_subsys`'s `mem_wr`), driven by real logic
(`a & b`).
"""

from pathlib import Path

from backend.elaborator.rtl.ir.flatten import TraceKind, trace_driver
from backend.elaborator.rtl.verilog import elaborate

FIXTURE_DIR = Path(__file__).parent / "golden_rtl" / "verilog_interface_forwarding_chain"
FILES = [str(FIXTURE_DIR / f) for f in ("bus_if.sv", "leaf.sv", "mid.sv", "outer.sv", "top.sv")]


def _elaborate():
    return elaborate(FILES, top="top")


def test_forwarding_port_connection_is_qualified_by_its_own_local_name_not_the_deep_resolved_one():
    design = _elaborate()
    outer_def = next(d for d in design.definitions.values() if d.base_name == "outer")
    mid_inst = next(i for i in outer_def.instances if i.name == "u_mid")
    connected_to = next(c.connected_to for c in mid_inst.connections if c.instance_port == "bus")
    # NOT "mem_wr"-equivalent (the deep, ultimate identity pyslang itself
    # resolves to) -- "bus", `outer`'s own port name, reachable from
    # `outer`'s own local scope.
    assert connected_to == "bus"


def test_driver_traces_through_both_forwarding_hops_to_the_real_local_instance():
    design = _elaborate()
    leaf_path = "top/u_outer/u_mid/u_leaf"

    result = trace_driver(design, leaf_path, "sig")
    assert result.kind == TraceKind.RESOLVED
    assert result.endpoint.path == "top"
    assert result.endpoint.signal == "bus.req"
