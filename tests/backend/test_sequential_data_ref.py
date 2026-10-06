"""SequentialElement.data_ref (Verilog/SV, verified via a real elaborate()
run) -- reversed from an earlier, stricter "no data-path/next-state
modeling at all" decision (see SequentialElement's own docstring for the
full "why": found blocking correct Driver/Receiver Supply Set results on
a real design, tests/frontend/test_power_resolution_cache.py's own
golden pass-through-vs-real-logic test case, specifically signal "d"
feeding a flop's own data input with nothing recording that connection).
See test_vhdl_sequential_data_ref.py for the VHDL-side counterpart
(written, unverified in this sandbox -- no ghdl/yosys on PATH).

`data_ref` is deliberately narrow -- the SAME "is the RHS a bare, same-
width single-signal reference" pass-through test ContinuousAssign.source
already applies to a plain `assign`, applied here to a sequential
element's own next-state assignment(s), scoped to the "not currently
resetting" branch only. General next-state EXPRESSION modeling (an
operator, a case-selected mux between two different signals) remains
explicitly out of scope -- these tests lock in exactly that boundary.
"""

from pathlib import Path

from backend.elaborator.rtl.verilog import elaborate as elaborate_verilog

VERILOG_DIR = Path(__file__).parent / "golden_rtl" / "verilog_sequential_data_ref"


def _by_output_signal(elements):
    return {e.output_signal: e for e in elements}


# --- Verilog/SV (verified, real elaborate() run) --------------------------


def test_clean_bare_pass_through_data_ref_is_the_source_signal():
    design = elaborate_verilog([str(VERILOG_DIR / "top.v")])
    top = design.definition(design.top)
    elements = _by_output_signal(top.sequential_elements)
    assert elements["q_clean"].data_ref == "a"


def test_case_arms_with_different_bare_sources_yield_no_data_ref():
    """`if (sel) q_mux <= a; else q_mux <= b;` -- two DIFFERENT bare
    sources for the same target is real next-state logic (a mux), not a
    pass-through -- must not collapse to either one, silently picking a
    winner."""
    design = elaborate_verilog([str(VERILOG_DIR / "top.v")])
    top = design.definition(design.top)
    elements = _by_output_signal(top.sequential_elements)
    assert elements["q_mux"].data_ref is None


def test_real_operator_on_the_rhs_yields_no_data_ref():
    """`q_op <= a + b;` -- an operator, not a bare signal reference."""
    design = elaborate_verilog([str(VERILOG_DIR / "top.v")])
    top = design.definition(design.top)
    elements = _by_output_signal(top.sequential_elements)
    assert elements["q_op"].data_ref is None


def test_width_changing_conversion_yields_no_data_ref():
    """`q_widen <= narrow;` (4-bit -> 8-bit) -- same "never trace through
    a width mismatch" rule ContinuousAssign.source already applies."""
    design = elaborate_verilog([str(VERILOG_DIR / "top.v")])
    top = design.definition(design.top)
    elements = _by_output_signal(top.sequential_elements)
    assert elements["q_widen"].data_ref is None


def test_reset_branch_constant_never_pollutes_data_ref():
    """Every one of the four flops in the reset-having always_ff block
    also assigns a constant (`<= 0`) in the RESET branch -- data_ref
    extraction must be scoped to the "not resetting" branch only, never
    letting that constant disqualify or otherwise affect the real
    (non-reset) data_ref answer. q_clean's own data_ref == "a" (not None,
    not disqualified by its own reset-branch `<= 0`) already proves this;
    this test names the requirement explicitly."""
    design = elaborate_verilog([str(VERILOG_DIR / "top.v")])
    top = design.definition(design.top)
    elements = _by_output_signal(top.sequential_elements)
    assert elements["q_clean"].data_ref == "a"


def test_resetless_block_data_ref_still_resolves():
    """A bare `always_ff @(posedge clk) q <= a;` with no if/else at all
    -- the whole body IS the "normal operation" data path."""
    design = elaborate_verilog([str(VERILOG_DIR / "top.v")])
    top = design.definition(design.top)
    elements = _by_output_signal(top.sequential_elements)
    assert elements["q_resetless"].data_ref == "a"
