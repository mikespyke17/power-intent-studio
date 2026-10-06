"""Constant-indexed vector bit-select connectivity -- two real, confirmed
gaps found via real `aura` design use (`clk_rst_gen`'s own per-domain
`clk_dom[N_DOMAIN-1:0]` clock gating, `aura_top`'s own `.clk(clk_dom[DOM_CPU])`
-style per-domain fanout):

  1. Extraction (`_resolve_connected_name()`/`_build_continuous_assign()`
     in verilog/elaborate.py): a single-bit ELEMENT-select with a
     COMPILE-TIME-CONSTANT index (`clk_dom[DOM_CPU]`, `clk_dom[d]` inside
     a `generate for` loop) previously either lost its index entirely
     (`getSymbolReference()`'s own auto-unwrap on the CONNECTION side) or
     was silently dropped altogether (the LHS-kind guard on the
     CONTINUOUS-ASSIGN side, which only ever accepted a bare/interface-
     member reference).
  2. Trace-time (`net_group()` in flatten.py, both here and in the
     frontend's own mirror): even with (1) fixed, a bit-indexed query
     ("clk_dom[0]") can never exact-string-match a WHOLE-vector
     connection ("clk_dom", e.g. `.clk_dom(clk_dom)` at the consuming
     instantiation) -- fixed by merging in whole-vector members too,
     re-attaching the same index suffix so descending into the driver's
     own scope keeps asking the SAME specific-bit question.

Fixture (`golden_rtl/verilog_constant_indexed_vector/`) mirrors the real
`aura` shape at minimal scale: `clk_gen` drives `clk_dom[N-1:0]` one bit
per `generate for` iteration; `top` connects the WHOLE vector to `clk_gen`
(bare, no index) but consumes each bit individually via a
localparam-indexed element-select, matching two DIFFERENT consumers to
two DIFFERENT bits -- proving bits aren't conflated/aliased together.
"""

from pathlib import Path

from backend.elaborator.rtl.ir import ContinuousAssign
from backend.elaborator.rtl.ir.flatten import TraceKind, trace_driver
from backend.elaborator.rtl.verilog import elaborate

FIXTURE_DIR = Path(__file__).parent / "golden_rtl" / "verilog_constant_indexed_vector"
FILES = [str(FIXTURE_DIR / f) for f in ("clk_gen.sv", "consumer.sv", "top.sv")]


def _elaborate():
    return elaborate(FILES, top="top")


def test_constant_element_select_connection_preserves_its_own_index():
    design = _elaborate()
    top_def = design.definition(design.top)
    conns = {i.name: {c.instance_port: c.connected_to for c in i.connections} for i in top_def.instances}
    assert conns["u_cons_a"]["clk"] == "clk_dom[0]"
    assert conns["u_cons_b"]["clk"] == "clk_dom[1]"


def test_generate_for_per_bit_continuous_assign_targets_are_captured_separately():
    design = _elaborate()
    gen_def = next(d for d in design.definitions.values() if d.base_name == "clk_gen")
    by_target = {ca.target: ca for ca in gen_def.continuous_assigns}
    assert by_target["clk_dom[0]"].source is None  # real logic (clk_ref & en[0]), not a pass-through
    assert by_target["clk_dom[1]"].source is None
    assert ContinuousAssign(target="clk_dom[0]", source=None, referenced_signals=by_target["clk_dom[0]"].referenced_signals) in gen_def.continuous_assigns


def test_each_consumer_resolves_to_its_own_distinct_bit_not_conflated():
    design = _elaborate()

    driver_a = trace_driver(design, "top/u_cons_a", "clk")
    driver_b = trace_driver(design, "top/u_cons_b", "clk")

    assert driver_a.kind == TraceKind.RESOLVED
    assert driver_a.endpoint.path == "top/u_gen"
    assert driver_a.endpoint.signal == "clk_dom[0]"

    assert driver_b.kind == TraceKind.RESOLVED
    assert driver_b.endpoint.path == "top/u_gen"
    assert driver_b.endpoint.signal == "clk_dom[1]"

    assert driver_a.endpoint != driver_b.endpoint
