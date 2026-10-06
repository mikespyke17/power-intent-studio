"""Hand-built IR for the two minimal golden_rtl fixtures.

Purpose: prove the schema itself is language-agnostic *before* any real
parser exists. If the Verilog and VHDL fixtures (structurally identical,
different syntax for width/reset) don't converge to the same IR shape here,
the schema is wrong regardless of what 1a/1b later produce.
"""

from backend.elaborator.rtl.ir import (
    BitRange,
    Definition,
    Design,
    Instance,
    Port,
    PortConnection,
    PortDirection,
    SourceLanguage,
    make_definition_id,
)
from tests.backend.golden_ir_fixtures import build_design, build_sub


def test_definition_id_is_deterministic_and_language_independent():
    verilog_sub = build_sub(SourceLanguage.VERILOG, "sub.v")
    vhdl_sub = build_sub(SourceLanguage.VHDL, "sub.vhd")
    assert verilog_sub.definition_id == vhdl_sub.definition_id


def test_verilog_and_vhdl_fixtures_converge_to_the_same_ir_shape():
    verilog = build_design(SourceLanguage.VERILOG, "sub.v", "top.v")
    vhdl = build_design(SourceLanguage.VHDL, "sub.vhd", "top.vhd")

    verilog_sub = verilog.definitions[verilog.definitions[verilog.top].instances[0].definition_id]
    vhdl_sub = vhdl.definitions[vhdl.definitions[vhdl.top].instances[0].definition_id]

    assert [p.name for p in verilog_sub.ports] == [p.name for p in vhdl_sub.ports]
    assert [p.width for p in verilog_sub.ports] == [p.width for p in vhdl_sub.ports]
    assert verilog_sub.sequential_elements == vhdl_sub.sequential_elements


def test_bit_range_width_and_direction():
    descending = BitRange(7, 0)
    ascending = BitRange(0, 7)
    assert descending.width == ascending.width == 8
    assert descending.direction.value == "descending"
    assert ascending.direction.value == "ascending"


def test_scalar_port_has_no_declared_range_but_width_one():
    clk = Port("clk", PortDirection.IN)
    assert clk.bit_range is None
    assert clk.width == 1


def test_duplicate_instantiation_dedupes_to_one_definition():
    """top instantiates sub twice under different names -> one Definition, two Instances."""
    sub = build_sub(SourceLanguage.VERILOG, "sub.v")
    top = Definition(
        definition_id=make_definition_id("top", {}),
        base_name="top",
        language=SourceLanguage.VERILOG,
        source_file="top.v",
        ports=[
            Port("clk", PortDirection.IN),
            Port("rst_n", PortDirection.IN),
            Port("data_in_a", PortDirection.IN, BitRange(7, 0)),
            Port("data_out_a", PortDirection.OUT, BitRange(7, 0)),
            Port("data_in_b", PortDirection.IN, BitRange(7, 0)),
            Port("data_out_b", PortDirection.OUT, BitRange(7, 0)),
        ],
        instances=[
            Instance(
                name="u_sub_a",
                definition_id=sub.definition_id,
                connections=[
                    PortConnection("clk", "clk"),
                    PortConnection("rst_n", "rst_n"),
                    PortConnection("d", "data_in_a"),
                    PortConnection("q", "data_out_a"),
                ],
            ),
            Instance(
                name="u_sub_b",
                definition_id=sub.definition_id,
                connections=[
                    PortConnection("clk", "clk"),
                    PortConnection("rst_n", "rst_n"),
                    PortConnection("d", "data_in_b"),
                    PortConnection("q", "data_out_b"),
                ],
            ),
        ],
    )
    design = Design(definitions={sub.definition_id: sub, top.definition_id: top}, top=top.definition_id)

    # dedup: exactly one Definition node for "sub" despite two instantiations
    sub_definitions = [d for d in design.definitions.values() if d.base_name == "sub"]
    assert len(sub_definitions) == 1
    assert len(design.definitions) == 2  # sub + top, nothing duplicated

    top_def = design.definition(top.definition_id)
    assert [inst.name for inst in top_def.instances] == ["u_sub_a", "u_sub_b"]
    assert top_def.instances[0].definition_id == top_def.instances[1].definition_id

    # independent, non-aliased connections per instance
    assert top_def.instances[0].connections != top_def.instances[1].connections

    # hierarchy walk sees two distinct paths, both backed by the same Definition object
    nodes = list(design.walk())
    assert [n.path for n in nodes] == ["top", "top/u_sub_a", "top/u_sub_b"]
    assert nodes[1].definition is nodes[2].definition is sub


def test_make_definition_id_same_bindings_same_id():
    assert make_definition_id("sub", {"WIDTH": "8"}) == make_definition_id("sub", {"WIDTH": "8"})


def test_make_definition_id_different_bindings_different_id():
    id_w8 = make_definition_id("sub", {"WIDTH": "8"})
    id_w16 = make_definition_id("sub", {"WIDTH": "16"})
    assert id_w8 != id_w16


def test_make_definition_id_is_order_independent():
    id_a = make_definition_id("sub", {"WIDTH": "8", "DEPTH": "4"})
    id_b = make_definition_id("sub", {"DEPTH": "4", "WIDTH": "8"})
    assert id_a == id_b
