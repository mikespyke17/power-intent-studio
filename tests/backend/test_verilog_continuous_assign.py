"""ContinuousAssign extraction (Verilog/SV) -- the new combinational-
logic primitive backend/elaborator/rtl/ir/flatten.py's own connectivity-
flattening engine needs to distinguish TRUE pass-through wiring
(`source` set) from real combinational logic (`source=None`, a
stopping point). See ContinuousAssign's own docstring for the full
"why" -- verified live against real pyslang output before writing this
extraction (not assumed): `assign y = a;` gives a bare NamedValue RHS,
while `~a`/`sel?a:b`/`a&b`/a width-changing implicit conversion each
give a distinct, easily-distinguished ExpressionKind.
"""

from pathlib import Path

from backend.elaborator.rtl.ir import ContinuousAssign
from backend.elaborator.rtl.verilog import elaborate

ASSIGN_SHAPES_DIR = Path(__file__).parent / "golden_rtl" / "verilog_continuous_assign_shapes"


def test_continuous_assign_shapes_classified_correctly():
    design = elaborate([str(ASSIGN_SHAPES_DIR / "sub.v")])
    sub = design.definition(design.top)

    by_target = {ca.target: ca for ca in sub.continuous_assigns}

    assert by_target["y_passthrough"] == ContinuousAssign(target="y_passthrough", source="a", referenced_signals=("a",))
    assert by_target["y_inverted"] == ContinuousAssign(target="y_inverted", source=None, referenced_signals=("a",))
    assert by_target["y_mux"] == ContinuousAssign(target="y_mux", source=None, referenced_signals=("sel", "a", "b"))
    assert by_target["y_and"] == ContinuousAssign(target="y_and", source=None, referenced_signals=("a", "b"))


def test_width_changing_conversion_is_not_treated_as_pass_through():
    """A bare-looking `assign y_widened = narrow;` where the RHS is
    narrower than the LHS still gets an implicit width-extending
    conversion wrapped around it by pyslang -- must NOT be classified
    as pass-through (source left None), per the explicit "never trace
    through a width mismatch" requirement."""
    design = elaborate([str(ASSIGN_SHAPES_DIR / "sub.v")])
    sub = design.definition(design.top)

    by_target = {ca.target: ca for ca in sub.continuous_assigns}
    assert by_target["y_widened"] == ContinuousAssign(target="y_widened", source=None, referenced_signals=("narrow",))


def test_same_width_signed_reinterpretation_is_still_pass_through():
    """A same-width signedness-only conversion also wraps the RHS in a
    ConversionExpression (confirmed live) -- must still be recognized
    as pass-through once unwrapped, since the underlying value is
    identical, just reinterpreted."""
    source = """
    module sub(input signed [7:0] a, output [7:0] y);
        assign y = a;
    endmodule
    """
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".v", mode="w", delete=False) as f:
        f.write(source)
        path = f.name
    design = elaborate([path])
    sub = design.definition(design.top)
    assert sub.continuous_assigns == [ContinuousAssign(target="y", source="a", referenced_signals=("a",))]


def test_non_bare_lhs_is_skipped_not_mis_recorded():
    """A concatenation LHS (`assign {hi, lo} = wide;`) is an explicit,
    logged scope cut -- not represented as a ContinuousAssign at all,
    rather than being silently misrecorded as if it were a simple
    single-target assign."""
    source = """
    module sub(input [15:0] wide, output hi, output lo);
        assign {hi, lo} = wide[15:14];
    endmodule
    """
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".v", mode="w", delete=False) as f:
        f.write(source)
        path = f.name
    design = elaborate([path])
    sub = design.definition(design.top)
    assert sub.continuous_assigns == []
