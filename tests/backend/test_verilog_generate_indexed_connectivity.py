"""Regression suite for a batch of real, confirmed bugs found via a
2026-09-01 validation pass ("run real SystemVerilog `generate` examples
through the real elaborator, not a mocked fixture") -- every case here was
first observed against real, hand-authored `.sv` examples outside this
repo (not golden fixtures), then reduced to the minimal golden fixtures
below once each root cause was isolated. See the validation report itself
(the conversation this suite's own commit landed in) for the full write-up
of each finding; this docstring only summarizes what each test locks in.

All five findings shared one general pattern: a `generate for` loop
driving a per-iteration bit/element-slice of a wider signal lost that
slice's identity somewhere between extraction and connectivity resolution
-- the exact shape differed (an instance's own OUTPUT port connection, a
locally-declared net, a continuous-assign target, a sequential element's
own target, a black-box macro's own connection), but the failure mode
recurred across every mechanism.

1. Indexed OUTPUT port connection from an ordinary (non-black-box)
   instance (`.q(data_o[bit_idx])`) collapsed to the bare, un-indexed
   parent signal name -- pyslang wraps an output connection's own
   expression in an `AssignmentExpression` that `_resolve_connected_name()`
   never unwrapped before walking its ElementSelect layers. Fixed in
   `_resolve_connected_name()`.
2. A constant-indexed driver query (`d_next[0]`) never matched a
   WHOLE-vector continuous-assign target (`assign d_next = ...;`) --
   `flatten.py`'s own driver search did an exact string match with no
   "also try the whole-vector base name" fallback, unlike `net_group()`'s
   own already-established handling of the identical shape for
   connections. Fixed in `_trace_driver()`.
3. A net (and its own feeding continuous assign) declared LOCALLY inside a
   `generate for` iteration was extracted under its own bare name with NO
   per-iteration prefix at all, unlike an instance's own name -- every
   iteration's own genuinely distinct local net collapsed into
   identically-named IR entries, and worse, `flatten.py`'s own exact-match
   driver search then silently resolved every iteration's own query to the
   SAME (accidentally identical) target -- a confidently wrong answer, not
   a visible gap. Fixed in `_walk_body_members()`/`_build_net()`/
   `_prefix_local_connections()`/`_prefix_local_connections_dict()`.
4. ANY indexed or range-sliced connection to a black-box (.lib) macro
   instance was dropped entirely (`connected_to=None`) -- the raw-syntax-
   text extraction this class of instance requires only ever recognized a
   bare identifier. Fixed in `_extract_uninstantiated_connections()`/
   `_resolve_uninstantiated_connection_text()`, including resolving a
   genvar-element-select index via a newly-threaded `genvar_values`
   mapping (a genvar's own per-iteration constant value, not recoverable
   from raw syntax text alone).
5. A `generate for` loop directly containing an `always_ff` with NO
   submodule instantiation at all (Category 2 of the validation pass) --
   every generated flop's own `output_signal` collapsed to the bare,
   un-indexed target name, the same ElementSelect-auto-unwrap gap as (1),
   in `_collect_assignment_targets()`'s own separate, duplicate unwrapping
   logic. Fixed by reusing `_resolve_connected_name()` there too.
"""

from pathlib import Path

from backend.elaborator.lib import elaborate_with_black_boxes
from backend.elaborator.rtl import verilog
from backend.elaborator.rtl.ir.flatten import Endpoint, TraceKind, trace_driver

FIXTURE_DIR = Path(__file__).parent / "golden_rtl" / "verilog_generate_indexed_connectivity"
LIB_DIR = Path(__file__).parent / "golden_lib"


# --- Findings 1 & 2: ordinary (non-black-box) instance, indexed output ----
# --- connections + a whole-vector continuous-assign target ----------------


def _elaborate_ordinary():
    return verilog.elaborate([str(FIXTURE_DIR / "ordinary.sv")], top="gen_ordinary_top")


def test_indexed_output_connection_preserves_its_own_bit_index():
    design = _elaborate_ordinary()
    top_def = design.definition(design.top)
    for i in range(4):
        inst = next(inst for inst in top_def.instances if inst.name == f"g_bit[{i}].u_reg")
        conns = {c.instance_port: c.connected_to for c in inst.connections}
        assert conns["q"] == f"q_o[{i}]"
        assert conns["d"] == f"d_next[{i}]"


def test_each_generated_leafs_output_resolves_to_its_own_distinct_driver():
    """The real end-to-end consequence of finding 1: querying the driver of
    each bit of the top-level output vector must resolve to that EXACT
    bit's own leaf instance -- not an ambiguous multiple-drivers result
    with no candidates (the pre-fix symptom)."""
    design = _elaborate_ordinary()
    for i in range(4):
        result = trace_driver(design, "gen_ordinary_top", f"q_o[{i}]")
        assert result.kind == TraceKind.RESOLVED
        assert result.endpoint.path == f"gen_ordinary_top/g_bit[{i}].u_reg"
        assert result.endpoint.signal == "q"


def test_indexed_query_resolves_through_a_whole_vector_continuous_assign():
    """Finding 2: `d_next[i]` (the leaf's own `d` connection, already
    correctly indexed) must resolve back through the WHOLE-vector
    `assign d_next = d_i;` -- not report UNDRIVEN despite a real driver
    existing one query away."""
    design = _elaborate_ordinary()
    for i in range(4):
        result = trace_driver(design, f"gen_ordinary_top/g_bit[{i}].u_reg", "d")
        assert result.kind == TraceKind.RESOLVED
        assert result.endpoint == Endpoint("gen_ordinary_top", f"d_next[{i}]")


# --- Findings 3 & 4: black-box macro, locally-declared generate-scope -----
# --- net + indexed/range-sliced connections --------------------------------


def _elaborate_blackbox():
    return elaborate_with_black_boxes(
        [str(FIXTURE_DIR / "blackbox_top.sv")],
        [str(LIB_DIR / "gen_macro.lib")],
        top="blackbox_gen_top",
    )


def test_locally_declared_generate_scope_net_is_disambiguated_per_iteration():
    design = _elaborate_blackbox()
    top_def = design.definition(design.top)
    net_names = {n.name for n in top_def.nets}
    assert net_names == {"g_inst[0].local_en_b", "g_inst[1].local_en_b", "g_inst[2].local_en_b"}


def test_locally_declared_net_is_not_accidentally_shared_across_iterations():
    """Finding 3's real consequence: before the fix, every iteration's own
    `en_b` driver query silently resolved to the SAME (accidentally
    identical) continuous assign -- a confidently wrong answer. Each
    iteration must now resolve to its OWN assign, and the three resolved
    endpoints must be pairwise distinct."""
    design = _elaborate_blackbox()
    results = [trace_driver(design, f"blackbox_gen_top/g_inst[{i}].u_macro", "en_b") for i in range(3)]
    for i, result in enumerate(results):
        assert result.kind == TraceKind.RESOLVED
        assert result.endpoint.signal == f"g_inst[{i}].local_en_b"
    assert len({r.endpoint for r in results}) == 3  # pairwise distinct, not the same accidental answer 3 times


def test_blackbox_genvar_element_select_output_connection_is_not_dropped():
    """Finding 4 (element-select-with-genvar branch): `.q(q_o[i])` on a
    BLACK-BOX instance must resolve to the exact indexed connection, not
    None (the pre-fix symptom -- every indexed connection to a black-box
    macro was silently dropped, regardless of whether the index was even
    a compile-time constant)."""
    design = _elaborate_blackbox()
    top_def = design.definition(design.top)
    for i in range(3):
        inst = next(inst for inst in top_def.instances if inst.name == f"g_inst[{i}].u_macro")
        conns = {c.instance_port: c.connected_to for c in inst.connections}
        assert conns["q"] == f"q_o[{i}]"


def test_blackbox_range_select_input_connection_collapses_to_the_bare_signal():
    """Finding 4 (range-select branch): `.a(addr_i[7:0])` must resolve to
    the bare `addr_i` -- matching `_resolve_connected_name()`'s own
    already-established convention for an ordinary instance's range-select
    connection (range info discarded, not modeled as a suffix) -- not None."""
    design = _elaborate_blackbox()
    top_def = design.definition(design.top)
    for i in range(3):
        inst = next(inst for inst in top_def.instances if inst.name == f"g_inst[{i}].u_macro")
        conns = {c.instance_port: c.connected_to for c in inst.connections}
        assert conns["a"] == "addr_i"


def test_blackbox_bare_identifier_connections_are_unaffected():
    """Sanity check: the pre-existing, already-correct bare-identifier
    connection path (`.clk(clk)`) must still work exactly as before."""
    design = _elaborate_blackbox()
    top_def = design.definition(design.top)
    for i in range(3):
        inst = next(inst for inst in top_def.instances if inst.name == f"g_inst[{i}].u_macro")
        conns = {c.instance_port: c.connected_to for c in inst.connections}
        assert conns["clk"] == "clk"


# --- Finding 5: generate-for directly containing always_ff, no instances --


def _elaborate_inline_flops():
    return verilog.elaborate([str(FIXTURE_DIR / "inline_flops.sv")], top="gen_inline_flops")


def test_inline_generated_flops_have_no_submodule_instances():
    design = _elaborate_inline_flops()
    top_def = design.definition(design.top)
    assert top_def.instances == []


def test_inline_generated_flops_are_each_disambiguated_by_bit_index():
    """Finding 5: every generated flop's own `output_signal` used to
    collapse to the identical bare `q_o`, indistinguishable from one
    another despite the correct COUNT already being recognized (3, not
    silently dropped) -- each must now carry its own distinct index."""
    design = _elaborate_inline_flops()
    top_def = design.definition(design.top)
    assert len(top_def.sequential_elements) == 3
    output_signals = {se.output_signal for se in top_def.sequential_elements}
    assert output_signals == {"q_o[0]", "q_o[1]", "q_o[2]"}
    for se in top_def.sequential_elements:
        assert se.is_latch is False
        assert se.clock_ref == "clk"
        assert se.reset_ref == "reset_n"
