"""Interface-member connections/continuous-assigns and the trace-engine
priority fix -- three real, confirmed bugs found via real `aura` design
use (Milestone-3-adjacent connectivity tracing for the Port List's own
Driver/Receiver Supply Set columns):

  1. A plain (non-interface-typed) instance port connected through a
     modport-qualified interface member (`.clk(bus.clk)`, `.cen(~bus.req)`)
     previously resolved wrong -- a unary-wrapped connection recorded as
     `connected_to=None` ("unconnected"), and even a bare interface-member
     connection lost its `bus.` qualifier, matching no real net anywhere.
  2. A continuous assign targeting or sourced from an interface member
     (`assign bus.rdata = q_int;`, `assign rdata_out = bus.rdata;`) was
     silently dropped (target case) or mis-recorded as non-pass-through
     with an empty `referenced_signals` (source case).
  3. Both `backend/elaborator/rtl/ir/flatten.py` and its frontend mirror
     checked "does this dotted signal belong to an interface PORT" BEFORE
     checking "does a local continuous assign already drive/receive this
     exact (possibly dotted) signal" -- permanently dead-ending a LOCALLY
     -declared interface instance's own driven/received member (e.g.
     `top_bank`'s own `bus`, driven via `assign bus.req = sel & req_in;`)
     at UNDRIVEN/FLOATING even once (1) and (2) were fixed.

Fixture (`golden_rtl/verilog_interface_member_connections/`) mirrors the
real `aura` shape at minimal scale: `bus_if.mem bus` is `mid_wrap`'s own
interface PORT (mirroring `sram_1rw_wrap`'s own `bus`); `top_bank`'s own
`bus` is a LOCALLY-declared interface instance (mirroring `sram_bank`'s
own `bus`), one level up, driven via plain continuous assigns.
"""

from pathlib import Path

from backend.elaborator.rtl.ir import ContinuousAssign
from backend.elaborator.rtl.ir.flatten import TraceKind, trace_driver, trace_receivers
from backend.elaborator.rtl.verilog import elaborate

FIXTURE_DIR = Path(__file__).parent / "golden_rtl" / "verilog_interface_member_connections"
FILES = [str(FIXTURE_DIR / f) for f in ("bus_if.sv", "leaf_macro.sv", "mid_wrap.sv", "top_bank.sv")]


def _elaborate():
    return elaborate(FILES, top="top_bank")


# --- extraction: PortConnection.connected_to ----------------------------


def test_unary_and_bare_interface_member_port_connections_are_qualified():
    design = _elaborate()
    wrap_def = next(d for d in design.definitions.values() if d.base_name == "mid_wrap")
    leaf_inst = next(i for i in wrap_def.instances if i.name == "u_leaf")
    conns = {c.instance_port: c.connected_to for c in leaf_inst.connections}
    assert conns == {"clk": "bus.clk", "cen": "bus.req", "wen": "bus.we", "q": "q_int"}


# --- extraction: ContinuousAssign target/source qualification -----------


def test_continuous_assign_target_rooted_in_an_interface_port_is_qualified():
    design = _elaborate()
    wrap_def = next(d for d in design.definitions.values() if d.base_name == "mid_wrap")
    assert wrap_def.continuous_assigns == [ContinuousAssign(target="bus.rdata", source="q_int", referenced_signals=("q_int",))]


def test_continuous_assign_target_and_source_rooted_in_a_local_interface_instance_are_qualified():
    design = _elaborate()
    top = design.definition(design.top)
    by_target = {ca.target: ca for ca in top.continuous_assigns}
    assert by_target["bus.req"] == ContinuousAssign(target="bus.req", source=None, referenced_signals=("sel", "req_in"))
    assert by_target["bus.we"] == ContinuousAssign(target="bus.we", source="we_in", referenced_signals=("we_in",))
    assert by_target["rdata_out"] == ContinuousAssign(target="rdata_out", source="bus.rdata", referenced_signals=("bus.rdata",))


# --- end-to-end trace: backend/elaborator/rtl/ir/flatten.py -------------


def test_driver_traces_through_unary_logic_and_a_local_interface_instance_to_the_real_origin():
    """CEN/WEN previously dead-ended UNDRIVEN either at u_leaf's own pin
    (the `connected_to=None` bug) or one hop up at `bus.req`/`bus.we`
    themselves (the trace-ordering bug) -- now resolves all the way to
    the REAL driving logic, `sel & req_in` / `we_in`, at top_bank's own
    scope."""
    design = _elaborate()
    macro_path = "top_bank/u_wrap/u_leaf"

    # `bus.req` is driven by REAL logic (`sel & req_in`) -- terminal
    # right there, matching the established "real logic is its own
    # terminal driver, never traced through" precedent.
    cen_driver = trace_driver(design, macro_path, "cen")
    assert cen_driver.kind == TraceKind.RESOLVED
    assert cen_driver.endpoint.path == "top_bank"
    assert cen_driver.endpoint.signal == "bus.req"

    # `bus.we` is a pure PASS-THROUGH of `we_in` -- the trace correctly
    # continues one more hop, all the way to the real top-level input
    # port itself (the design's own boundary, `node.instance is None`).
    wen_driver = trace_driver(design, macro_path, "wen")
    assert wen_driver.kind == TraceKind.RESOLVED
    assert wen_driver.endpoint.path == "top_bank"
    assert wen_driver.endpoint.signal == "we_in"


def test_receiver_traces_from_the_macro_output_through_both_interface_hops_to_the_real_top_level_port():
    """`q`'s own forward trace previously dead-ended FLOATING at
    `mid_wrap`'s own `bus.rdata` (the dropped-continuous-assign bug) --
    now reaches `top_bank`'s own `rdata_out` output port, a real,
    externally-visible endpoint."""
    design = _elaborate()
    macro_path = "top_bank/u_wrap/u_leaf"

    q_receiver = trace_receivers(design, macro_path, "q")
    assert q_receiver.kind == TraceKind.RESOLVED
    assert len(q_receiver.endpoints) == 1
    assert q_receiver.endpoints[0].path == "top_bank"
    assert q_receiver.endpoints[0].signal == "rdata_out"
