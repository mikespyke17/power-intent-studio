"""1d: mixed-language instantiation resolution, orchestrating the real
pyslang and GHDL+Yosys front-ends -- not a mock of either.

Requires `ghdl`/`yosys` on PATH the same way test_vhdl_elaborate.py does
(any fixture here touches VHDL on at least one side); skipped, not failed,
when they aren't available. See docs/milestone1_rtl_elaborator.md for the
full design writeup (the two pre-implementation spikes, the leaf-first
merge algorithm, and the v1 scope boundaries this suite checks).
"""

import shutil
from pathlib import Path

import pytest

from backend.elaborator.rtl.ir import Edge, ResetKind, SourceLanguage
from backend.elaborator.rtl.mixed_language import elaborate_mixed

pytestmark = pytest.mark.skipif(
    shutil.which("ghdl") is None or shutil.which("yosys") is None,
    reason="ghdl/yosys not on PATH -- see docs/milestone1_rtl_elaborator.md for setup (OSS CAD Suite or equivalent)",
)

VERILOG_TOP_DIR = Path(__file__).parent / "golden_rtl" / "mixed_verilog_top"
VHDL_TOP_DIR = Path(__file__).parent / "golden_rtl" / "mixed_vhdl_top"
ALTERNATING_CHAIN_DIR = Path(__file__).parent / "golden_rtl" / "mixed_alternating_chain"


def test_verilog_top_instantiating_vhdl_leaf():
    """top.sv instantiates known_leaf (Verilog, resolves natively) and
    vhdl_entity (VHDL, needs real cross-language resolution). Checks the
    VHDL leaf is genuinely, fully elaborated -- real ports, real
    connectivity, and a real SequentialElement with correctly-traced reset
    polarity -- not just structurally present as an empty placeholder.
    """
    design = elaborate_mixed(
        [str(VERILOG_TOP_DIR / "top.sv"), str(VERILOG_TOP_DIR / "known_leaf.v"), str(VERILOG_TOP_DIR / "vhdl_entity.vhd")],
        top="top",
    )
    top = design.definition(design.top)
    assert [i.name for i in top.instances] == ["u_known", "u_vhdl"]
    assert len(design.definitions) == 3  # top, known_leaf, vhdl_entity

    known_inst, vhdl_inst = top.instances
    known_def = design.definition(known_inst.definition_id)
    vhdl_def = design.definition(vhdl_inst.definition_id)

    assert known_def.base_name == "known_leaf"
    assert known_def.language == SourceLanguage.VERILOG  # known_leaf.v is a .v file, not .sv

    assert vhdl_def.base_name == "vhdl_entity"
    assert vhdl_def.language == SourceLanguage.VHDL
    assert [(p.name, p.width) for p in vhdl_def.ports] == [("clk", 1), ("rst_n", 1), ("d", 8), ("q", 8)]

    vhdl_conns = {c.instance_port: c.connected_to for c in vhdl_inst.connections}
    assert vhdl_conns == {"clk": "clk", "rst_n": "rst_n", "d": "data_in", "q": "vhdl_out"}

    assert len(vhdl_def.sequential_elements) == 1
    seq = vhdl_def.sequential_elements[0]
    assert seq.clock_ref == "clk"
    assert seq.edge == Edge.RISING
    assert seq.reset_ref == "rst_n"
    assert seq.reset_kind == ResetKind.ASYNC
    assert seq.reset_active_low is True


def test_vhdl_top_instantiating_verilog_leaf():
    """Reverse direction: top.vhd declares an unbound `component
    verilog_leaf` and instantiates it; verilog_leaf.v provides the real
    definition. Checks the Verilog leaf's own SequentialElement (extracted
    via the always-block AST walk, a completely different code path than
    the VHDL-owns-top direction's $adff-cell walk) comes through correctly
    too.
    """
    design = elaborate_mixed([str(VHDL_TOP_DIR / "top.vhd"), str(VHDL_TOP_DIR / "verilog_leaf.v")], top="top")
    top = design.definition(design.top)
    assert [i.name for i in top.instances] == ["u_leaf"]
    assert len(design.definitions) == 2

    leaf_inst = top.instances[0]
    leaf_def = design.definition(leaf_inst.definition_id)
    assert leaf_def.base_name == "verilog_leaf"
    assert leaf_def.language == SourceLanguage.VERILOG

    conns = {c.instance_port: c.connected_to for c in leaf_inst.connections}
    assert conns == {"clk": "clk", "rst_n": "rst_n", "a": "data_in", "y": "data_out"}

    assert len(leaf_def.sequential_elements) == 1
    seq = leaf_def.sequential_elements[0]
    assert seq.reset_ref == "rst_n"
    assert seq.reset_kind == ResetKind.ASYNC
    assert seq.reset_active_low is True


def test_undefined_reference_with_no_leaf_files_fails_clearly():
    """No VHDL files given at all -- must name what's missing, not crash
    confusingly or silently produce an incomplete Design.
    """
    with pytest.raises(ValueError, match="vhdl_entity"):
        elaborate_mixed(
            [str(VERILOG_TOP_DIR / "top.sv"), str(VERILOG_TOP_DIR / "known_leaf.v")],
            top="top",
        )


def test_parametrized_cross_language_reference_not_supported(tmp_path):
    """v1 scope boundary: a cross-language instantiation with a #(...)
    override must fail loudly, not silently resolve against the
    referenced entity's default configuration. Needs its own top.sv (with
    the override) rather than the plain mixed_verilog_top fixture.
    """
    param_file = tmp_path / "top.sv"
    param_file.write_text(
        """
        module top (input wire clk, input wire [15:0] d, output wire [15:0] q);
            vhdl_entity #(.WIDTH(16)) u_vhdl (.clk(clk), .d(d), .q(q));
        endmodule
        """
    )

    with pytest.raises(NotImplementedError, match="parameter override"):
        elaborate_mixed([str(param_file), str(VERILOG_TOP_DIR / "vhdl_entity.vhd")], top="top")


def test_alternating_chain_is_rejected_not_silently_mishandled():
    """v1 scope boundary: top.sv (Verilog) instantiates vhdl_middle (VHDL),
    which itself instantiates verilog_innermost (Verilog) -- an alternating
    chain, deliberately out of scope. verilog_innermost.v is intentionally
    NOT provided in this fixture. Must fail with a clear, traceable error
    (naming both the outer reference being resolved and the inner cause),
    not hang, silently drop the innermost instance, or produce a wrong Design.
    """
    with pytest.raises(ValueError, match="verilog_innermost"):
        elaborate_mixed(
            [str(ALTERNATING_CHAIN_DIR / "top.sv"), str(ALTERNATING_CHAIN_DIR / "vhdl_middle.vhd")],
            top="top",
        )


def test_top_language_ambiguity_requires_explicit_disambiguation(tmp_path):
    """A name defined in both source sets must not be silently guessed at."""
    verilog_file = tmp_path / "dup.v"
    verilog_file.write_text("module dup (input wire clk); endmodule\n")

    vhdl_file = tmp_path / "dup.vhd"
    vhdl_file.write_text(
        """
        library ieee;
        use ieee.std_logic_1164.all;
        entity dup is port (clk : in std_logic); end entity dup;
        architecture rtl of dup is begin end architecture rtl;
        """
    )

    with pytest.raises(ValueError, match="defined in both"):
        elaborate_mixed([str(verilog_file), str(vhdl_file)], top="dup")

    # Explicit top_language resolves the ambiguity.
    design = elaborate_mixed([str(verilog_file), str(vhdl_file)], top="dup", top_language="verilog")
    assert design.definition(design.top).language == SourceLanguage.VERILOG
