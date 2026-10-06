"""Real pyslang-driven parsing of verilog_flop_min/, checked against the
hand-built golden IR in golden_ir_fixtures.py (the same expected output
test_ir_schema.py validates the schema shape against).
"""

from pathlib import Path

import pytest

from backend.elaborator.rtl.ir import Edge, PortDirection, ResetKind, SourceLanguage
from backend.elaborator.rtl.verilog import elaborate
from backend.elaborator.rtl.verilog.elaborate import ElaborationError
from tests.backend.golden_ir_fixtures import build_design, build_sub, build_sub_no_reset

FIXTURE_DIR = Path(__file__).parent / "golden_rtl" / "verilog_flop_min"
REORDERED_FIXTURE_DIR = Path(__file__).parent / "golden_rtl" / "verilog_flop_sensitivity_reordered"
NO_RESET_FIXTURE_DIR = Path(__file__).parent / "golden_rtl" / "verilog_flop_no_reset"
PARAMETRIZED_FIXTURE_DIR = Path(__file__).parent / "golden_rtl" / "verilog_parametrized"
INTERFACE_FIXTURE_DIR = Path(__file__).parent / "golden_rtl" / "verilog_interface"
INTERFACE_ARRAY_FIXTURE_DIR = Path(__file__).parent / "golden_rtl" / "verilog_interface_arrays"
INTERFACE_FORWARDING_FIXTURE_DIR = Path(__file__).parent / "golden_rtl" / "verilog_interface_forwarding"
TYPEDEF_FIXTURE_DIR = Path(__file__).parent / "golden_rtl" / "verilog_typedef_signal"
MULTI_REG_FIXTURE_DIR = Path(__file__).parent / "golden_rtl" / "verilog_multi_register_block"
GENERATE_FIXTURE_DIR = Path(__file__).parent / "golden_rtl" / "verilog_generate_blocks"
PACKAGE_FIXTURE_DIR = Path(__file__).parent / "golden_rtl" / "verilog_packages"
HIERARCHY_FIXTURE_DIR = Path(__file__).parent / "golden_rtl" / "verilog_three_level_hierarchy"


def _elaborate_fixture():
    return elaborate([str(FIXTURE_DIR / "sub.v"), str(FIXTURE_DIR / "top.v")])


def test_verilog_flop_min_matches_golden_ir():
    actual = _elaborate_fixture()
    expected = build_design(SourceLanguage.VERILOG, str(FIXTURE_DIR / "sub.v"), str(FIXTURE_DIR / "top.v"))

    actual_top = actual.definition(actual.top)
    expected_top = expected.definition(expected.top)
    assert actual_top.base_name == expected_top.base_name
    assert actual_top.ports == expected_top.ports
    assert [i.name for i in actual_top.instances] == [i.name for i in expected_top.instances]
    assert actual_top.instances[0].connections == expected_top.instances[0].connections
    assert actual_top.sequential_elements == expected_top.sequential_elements

    actual_sub = actual.definition(actual_top.instances[0].definition_id)
    expected_sub = expected.definition(expected_top.instances[0].definition_id)
    assert actual_sub.base_name == expected_sub.base_name
    assert actual_sub.ports == expected_sub.ports
    assert actual_sub.sequential_elements == expected_sub.sequential_elements
    assert actual_sub.instances == []


def test_verilog_flop_min_definition_ids_match_hand_built_ones():
    """Same base_name + empty parameters must hash to the identical id as the hand-built IR."""
    actual = _elaborate_fixture()
    expected = build_design(SourceLanguage.VERILOG, "sub.v", "top.v")
    assert set(actual.definitions.keys()) == set(expected.definitions.keys())


def test_verilog_flop_min_hierarchy_walk():
    design = _elaborate_fixture()
    nodes = list(design.walk())
    assert [n.path for n in nodes] == ["top", "top/u_sub"]
    assert nodes[1].definition.base_name == "sub"


def test_reordered_sensitivity_list_still_identifies_clk_not_rst_n_as_clock():
    """Regression test for a real bug: the first extraction picked whichever edge
    signal came first in the sensitivity list as the clock, which only worked on
    verilog_flop_min because `clk` happened to be listed first. Here the reset's
    `negedge` is listed *before* the clock's `posedge`, so a naive "first edge
    wins" implementation would misattribute rst_n as the clock and clk as the
    reset. Must produce the exact same SequentialElement as verilog_flop_min/sub.v.
    """
    design = elaborate([str(REORDERED_FIXTURE_DIR / "sub.v")])
    actual_sub = design.definition(design.top)

    expected_sub = build_sub(SourceLanguage.VERILOG, str(REORDERED_FIXTURE_DIR / "sub.v"))
    assert actual_sub.sequential_elements == expected_sub.sequential_elements

    seq = actual_sub.sequential_elements[0]
    assert seq.clock_ref == "clk"
    assert seq.reset_ref == "rst_n"
    assert seq.reset_kind == ResetKind.ASYNC


def test_flop_with_no_reset_is_identified_without_misattributing_a_reset():
    """A bare clocked assignment (no if/else at all) must still be recognized as
    a flop, with no reset fields populated -- not skipped, and not accidentally
    given a reset_kind/reset_ref it doesn't have.
    """
    design = elaborate([str(NO_RESET_FIXTURE_DIR / "sub.v")])
    actual_sub = design.definition(design.top)

    expected_sub = build_sub_no_reset(SourceLanguage.VERILOG, str(NO_RESET_FIXTURE_DIR / "sub.v"))
    assert actual_sub.ports == expected_sub.ports
    assert actual_sub.sequential_elements == expected_sub.sequential_elements

    seq = actual_sub.sequential_elements[0]
    assert seq.clock_ref == "clk"
    assert seq.edge is not None
    assert seq.reset_ref is None
    assert seq.reset_kind == ResetKind.NONE
    assert seq.reset_active_low is None


def test_parametrized_instances_produce_distinct_definitions():
    """top instantiates sub #(.WIDTH(8)) and sub #(.WIDTH(16)) -- same source module,
    different resolved parameter bindings. These must elaborate to two distinct
    Definition nodes (different definition_id, different port widths), not get
    deduped as if they were the same module. This is the real end-to-end version
    of what test_make_definition_id_different_bindings_different_id checks in
    isolation with synthetic inputs -- here the parameter resolution itself is
    real pyslang elaboration output, not a hand-picked string.
    """
    design = elaborate([str(PARAMETRIZED_FIXTURE_DIR / "sub.v"), str(PARAMETRIZED_FIXTURE_DIR / "top.v")])
    top = design.definition(design.top)
    assert [i.name for i in top.instances] == ["u_sub_a", "u_sub_b"]

    inst_a, inst_b = top.instances
    assert inst_a.definition_id != inst_b.definition_id

    def_a = design.definition(inst_a.definition_id)
    def_b = design.definition(inst_b.definition_id)
    assert def_a.base_name == def_b.base_name == "sub"
    assert def_a.parameters == {"WIDTH": "8"}
    assert def_b.parameters == {"WIDTH": "16"}

    d_port_a = next(p for p in def_a.ports if p.name == "d")
    d_port_b = next(p for p in def_b.ports if p.name == "d")
    assert d_port_a.width == 8
    assert d_port_b.width == 16

    # sub (WIDTH=8), sub (WIDTH=16), top -- three definitions, not two
    assert len(design.definitions) == 3


def test_interface_instance_elaborates_as_an_is_interface_definition():
    """simple_bus (an `interface`, not a `module`) must come back as a real
    Definition with is_interface=True, its two plain `logic` declarations
    as nets (no port list of its own), and both modports fully extracted
    with real per-signal directions -- confirmed against a real design
    that previously hit this as a hard NotImplementedError, not assumed."""
    design = elaborate([str(INTERFACE_FIXTURE_DIR / "iface.sv")])

    top = design.definition(design.top)
    (b_instance,) = [i for i in top.instances if i.name == "b"]
    bus_def = design.definition(b_instance.definition_id)

    assert bus_def.is_interface is True
    assert bus_def.base_name == "simple_bus"
    assert {n.name for n in bus_def.nets} == {"data", "valid"}
    assert bus_def.ports == []

    modports = {m.name: m for m in bus_def.modports}
    assert set(modports) == {"producer", "consumer"}
    producer_ports = {p.name: p.direction for p in modports["producer"].ports}
    assert producer_ports == {"data": PortDirection.OUT, "valid": PortDirection.OUT}
    consumer_ports = {p.name: p.direction for p in modports["consumer"].ports}
    assert consumer_ports == {"data": PortDirection.IN, "valid": PortDirection.IN}


def test_interface_typed_port_and_its_connection_resolve_correctly():
    """producer_mod/consumer_mod each declare one modport-constrained
    interface port (`simple_bus.producer bus`) -- must show up as an
    InterfacePort (not a Port: no single direction/width), and top's
    instantiation of each must connect that port to 'b' by name, exactly
    like a plain signal connection would."""
    design = elaborate([str(INTERFACE_FIXTURE_DIR / "iface.sv")])

    top = design.definition(design.top)
    producer_def = design.definition(next(i for i in top.instances if i.name == "u_prod").definition_id)
    consumer_def = design.definition(next(i for i in top.instances if i.name == "u_cons").definition_id)

    assert producer_def.ports == []  # bus is the *only* port, and it's an interface port, not a plain one
    (bus_port,) = producer_def.interface_ports
    assert bus_port.name == "bus"
    assert bus_port.interface_name == "simple_bus"
    assert bus_port.modport == "producer"

    (consumer_bus_port,) = consumer_def.interface_ports
    assert consumer_bus_port.modport == "consumer"

    prod_instance = next(i for i in top.instances if i.name == "u_prod")
    (bus_connection,) = [c for c in prod_instance.connections if c.instance_port == "bus"]
    assert bus_connection.connected_to == "b"


def test_unconstrained_interface_port_forwarded_to_a_single_modport_qualified_child_infers_it():
    """fwd_single's own `fwd_bus` port has no modport at its own
    declaration, but is immediately forwarded, unmodified, to
    consumer_mod's own `simple_bus.consumer bus` port -- a real, common
    RTL idiom (a forwarding wrapper leaves its own port unconstrained).
    inferred_modport must pick up "consumer" from that child, without
    requiring fwd_single itself to be instantiated anywhere (this must
    work purely from fwd_single's own body, matching how a real forwarding
    module's effective usage doesn't depend on its own caller)."""
    design = elaborate([str(INTERFACE_FORWARDING_FIXTURE_DIR / "top.sv")], top="fwd_single")

    fwd_single = design.definition(design.top)
    (fwd_bus,) = fwd_single.interface_ports
    assert fwd_bus.modport is None
    assert fwd_bus.inferred_modport == "consumer"


def test_unconstrained_interface_port_forwarded_to_conflicting_modports_stays_unresolved():
    """fwd_ambiguous forwards its own unconstrained port to both a
    producer_mod (needs "producer") and a consumer_mod (needs "consumer")
    child -- genuinely ambiguous, must not guess either one."""
    design = elaborate([str(INTERFACE_FORWARDING_FIXTURE_DIR / "top.sv")], top="fwd_ambiguous")

    fwd_ambiguous = design.definition(design.top)
    (fwd_bus,) = fwd_ambiguous.interface_ports
    assert fwd_bus.modport is None
    assert fwd_bus.inferred_modport is None


def test_dangling_interface_instance_elaborates_to_an_unused_but_real_definition():
    """An interface instance with nothing connected to it anywhere is
    unusual but not actually invalid RTL -- previously a hard
    NotImplementedError (the port-level guard alone didn't catch this
    case, since it has no InterfacePortSymbol pointing at it at all); now
    just an ordinary, if unreferenced-elsewhere, Instance/Definition pair."""
    design = elaborate([str(INTERFACE_FIXTURE_DIR / "dangling_iface.sv")])

    top = design.definition(design.top)
    (b_instance,) = top.instances
    bus_def = design.definition(b_instance.definition_id)

    assert bus_def.is_interface is True
    assert bus_def.base_name == "simple_bus2"
    assert {n.name for n in bus_def.nets} == {"data"}


def test_arrayed_interface_instance_expands_to_one_instance_per_element():
    """`simple_bus buses [4] ()` -- pyslang reports this as one
    InstanceArraySymbol wrapping 4 real (but empty-named) InstanceSymbol
    elements, a different shape than a lone InstanceSymbol; confirmed
    against a real design (aura's own axi_mst/axi_slv/pmu_ch arrays) that
    this was previously silently skipped entirely (not even an error --
    just absent from the IR), not just unsupported the loud way interface
    ports/instances were. Each element must appear as its own real
    Instance, named positionally ("buses[i]") since pyslang's own .name
    for an array element is empty."""
    design = elaborate([str(INTERFACE_ARRAY_FIXTURE_DIR / "array_iface.sv")])

    top = design.definition(design.top)
    bus_instances = {i.name: i for i in top.instances if i.name.startswith("buses[")}
    assert set(bus_instances) == {"buses[0]", "buses[1]", "buses[2]", "buses[3]"}
    for inst in bus_instances.values():
        assert design.definition(inst.definition_id).base_name == "simple_bus"


def test_arrayed_plain_module_instance_also_expands_correctly():
    """Same InstanceArraySymbol code path, exercised by a plain module
    array (`leaf u_leaves [3] ()`) with no interface involved at all --
    confirms the fix isn't accidentally interface-specific."""
    design = elaborate([str(INTERFACE_ARRAY_FIXTURE_DIR / "array_iface.sv")])

    top = design.definition(design.top)
    leaf_instances = {i.name: i for i in top.instances if i.name.startswith("u_leaves[")}
    assert set(leaf_instances) == {"u_leaves[0]", "u_leaves[1]", "u_leaves[2]"}
    for inst in leaf_instances.values():
        assert design.definition(inst.definition_id).base_name == "leaf"


def test_interface_port_bound_to_one_array_element_names_the_connection_by_index():
    """u_cons0/u_cons2 each connect their interface port directly to one
    element of the `buses` array, not a separately-named instance --
    pyslang reports the bound instance's own .name as empty in that case
    (.arrayName/.arrayPath give the real identity instead, confirmed via
    aura's own u_dsp.cfg -> axi_slv[1] pattern). connected_to must name
    it the same way the array-expansion above names the element itself,
    so the two actually match up."""
    design = elaborate([str(INTERFACE_ARRAY_FIXTURE_DIR / "array_iface.sv")])

    top = design.definition(design.top)
    cons0 = next(i for i in top.instances if i.name == "u_cons0")
    cons2 = next(i for i in top.instances if i.name == "u_cons2")

    (cons0_bus,) = [c for c in cons0.connections if c.instance_port == "bus"]
    (cons2_bus,) = [c for c in cons2.connections if c.instance_port == "bus"]
    assert cons0_bus.connected_to == "buses[0]"
    assert cons2_bus.connected_to == "buses[2]"


def test_typedefd_port_and_net_resolve_bit_range_through_the_alias():
    """A port/net declared with a package typedef (`word_pkg::byte_t`, an
    alias for `logic [7:0]`) previously crashed with a bare AttributeError
    -- .type was a TypeAliasType, which has no .range of its own even
    though .isPackedArray reports true; only .canonicalType resolves
    through to the real underlying packed type. Found via a real design
    (a typedef'd internal signal that elaboration only reached once
    interface support let it recurse this deep -- previously masked by
    the interface guard raising first), not hypothetical."""
    design = elaborate([str(TYPEDEF_FIXTURE_DIR / "typedef_signal.sv")])

    leaf = design.definition(design.top)
    assert [(p.name, p.width) for p in leaf.ports] == [("in_byte", 8), ("out_byte", 8)]
    (stage_net,) = leaf.nets
    assert stage_net.name == "stage"
    assert stage_net.width == 8


def test_one_always_ff_resetting_several_registers_together_yields_one_element_each():
    """The real bug: `if (!rst_n) begin a<=0; b<=0; c<=0; end else case(sel)
    ...` used to contribute *nothing* to the IR, because the old extractor
    only recognized a single top-level assignment per branch. Found via a
    real design (rv_core.sv resetting 6 registers in one always_ff) where
    only 4 of 46 real always_ff blocks were being recognized at all --
    confirmed by counting, not assumed. Must now produce one
    SequentialElement per register, all sharing the same clock/reset
    metadata (same block, same clock, same reset)."""
    design = elaborate([str(MULTI_REG_FIXTURE_DIR / "multi_reg.sv")], top="multi_reg")

    top = design.definition(design.top)
    by_signal = {s.output_signal: s for s in top.sequential_elements}
    assert set(by_signal) == {"a_q", "b_q", "c_q"}
    for element in by_signal.values():
        assert element.clock_ref == "clk"
        assert element.edge == Edge.RISING
        assert element.reset_ref == "rst_n"
        assert element.reset_active_low is True
        assert element.reset_kind == ResetKind.ASYNC  # rst_n is in the sensitivity list (negedge rst_n)
        assert element.is_latch is False


def test_resetless_multi_assignment_block_also_yields_one_element_each():
    """Same generalization applies with no if/else at all -- a resetless
    block driving more than one register together, previously only
    working for exactly one assignment."""
    design = elaborate([str(MULTI_REG_FIXTURE_DIR / "multi_reg.sv")], top="multi_reg_resetless")

    top = design.definition(design.top)
    by_signal = {s.output_signal: s for s in top.sequential_elements}
    assert set(by_signal) == {"x_q", "y_q"}
    for element in by_signal.values():
        assert element.clock_ref == "clk"
        assert element.reset_ref is None
        assert element.reset_kind == ResetKind.NONE


# --- generate if/for: previously invisible to elaboration entirely -----


def test_generate_if_else_only_the_live_branch_contributes_its_instance():
    """gen_if_select(SIZE=1) selects g_a (leaf_a), not g_b (leaf_b) -- the
    *other* branch also exists as a sibling GenerateBlockSymbol (pyslang
    doesn't just omit it), so this also proves the extraction actually
    checks which branch is live rather than picking whichever appears
    first. Found via a real design where a `generate if/else if` selecting
    between two hard-macro configurations meant the selected macro
    instance never appeared in the IR at all -- not partially, not at
    all, since nothing handled GenerateBlockSymbol as a member kind."""
    design = elaborate([str(GENERATE_FIXTURE_DIR / "generate_blocks.sv")], top="top")

    top = design.definition(design.top)
    sel_inst = next(i for i in top.instances if i.name == "u_sel")
    sel_def = design.definition(sel_inst.definition_id)

    (leaf_inst,) = sel_def.instances
    # The generate block is a hierarchy level: `g_a.u_leaf`, the name a UPF
    # and every EDA tool use -- not the bare `u_leaf` this used to pin.
    assert leaf_inst.name == "g_a.u_leaf"
    leaf_def = design.definition(leaf_inst.definition_id)
    assert leaf_def.base_name == "leaf_a"  # g_a, not g_b's leaf_b
    assert [s.output_signal for s in leaf_def.sequential_elements] == ["q"]  # the leaf's own content, intact


def test_generate_for_produces_one_instance_per_iteration_with_disambiguated_names():
    """gen_for_array(N=3) declares one `u_inst` per loop iteration -- the
    *same* literal name in each iteration's own scope. Without
    disambiguation, three same-named Instances would collide (and in a
    dict-keyed structure elsewhere, silently overwrite one another);
    confirms the "{array_name}[{index}]." prefix keeps them distinct and
    that all three are actually present, not just one."""
    design = elaborate([str(GENERATE_FIXTURE_DIR / "generate_blocks.sv")], top="top")

    top = design.definition(design.top)
    arr_inst = next(i for i in top.instances if i.name == "u_arr")
    arr_def = design.definition(arr_inst.definition_id)

    names = sorted(i.name for i in arr_def.instances)
    assert names == ["g_tap[0].u_inst", "g_tap[1].u_inst", "g_tap[2].u_inst"]
    for inst in arr_def.instances:
        assert design.definition(inst.definition_id).base_name == "leaf_a"


def test_package_scoped_parameters_resolve_correctly():
    """Exercises both `import pkg::*;` (sub.sv, WIDTH used unqualified) and
    explicit `pkg::WIDTH` (top.sv, no import at all). Both must resolve port
    widths correctly with zero extra handling in elaborate.py -- pyslang
    resolves package constants through the same type-resolution path as local
    parameters. Also confirms package constants do NOT leak into
    Definition.parameters: a package constant is a single global value, not a
    per-instance override, so it must not affect definition_id.
    """
    design = elaborate(
        [
            str(PACKAGE_FIXTURE_DIR / "pkg.sv"),
            str(PACKAGE_FIXTURE_DIR / "sub.sv"),
            str(PACKAGE_FIXTURE_DIR / "top.sv"),
        ]
    )
    top = design.definition(design.top)
    assert [(p.name, p.width) for p in top.ports] == [
        ("clk", 1),
        ("rst_n", 1),
        ("data_in", 8),
        ("data_out", 8),
    ]
    assert top.parameters == {}

    sub = design.definition(top.instances[0].definition_id)
    expected_sub = build_sub(SourceLanguage.SYSTEMVERILOG, str(PACKAGE_FIXTURE_DIR / "sub.sv"))
    assert sub.ports == expected_sub.ports
    assert sub.sequential_elements == expected_sub.sequential_elements
    assert sub.parameters == {}


def test_explicit_top_selects_a_submodule_instantiated_elsewhere_in_the_file_set():
    """A real, previously-broken workflow: compiling just a DUT submodule
    out of a larger loaded design. 'mid' is instantiated by 'top' in this
    fixture -- pyslang's own auto-detected topInstances would never
    contain 'mid' at all (it's not a natural root), so this only works
    because elaborate() now passes CompilationOptions.topModules, not
    just filtering auto-detected roots by name after the fact. 'top's own
    now-unused declaration existing in the same file set must not raise
    either.
    """
    design = elaborate(
        [str(HIERARCHY_FIXTURE_DIR / "top.v"), str(HIERARCHY_FIXTURE_DIR / "mid.v"), str(HIERARCHY_FIXTURE_DIR / "leaf.v")],
        top="mid",
    )
    top_def = design.definition(design.top)
    assert top_def.base_name == "mid"
    assert [i.name for i in top_def.instances] == ["u_leaf"]


def test_explicit_top_naming_a_nonexistent_module_raises_a_clear_error():
    with pytest.raises(ValueError, match="not a valid top-level module"):
        elaborate(
            [str(HIERARCHY_FIXTURE_DIR / "top.v"), str(HIERARCHY_FIXTURE_DIR / "mid.v"), str(HIERARCHY_FIXTURE_DIR / "leaf.v")],
            top="does_not_exist",
        )


def test_syntax_error_produces_a_real_message_with_structured_file_and_line():
    """Regression test for a real, reported bug: a missing comma in a port
    connection list previously raised a ValueError naming only the bare
    diagnostic code ("DiagCode(ExpectedToken)") -- no file, no line, no
    description. Must now carry the real file/line as structured
    attributes (not just embedded in free text) and a human-readable
    message with the actual source line and a caret at the offending
    token, using pyslang's own diagnostic-formatting machinery.
    """
    fixture = Path(__file__).parent / "golden_rtl" / "verilog_syntax_error" / "top.v"
    with pytest.raises(ElaborationError) as exc_info:
        elaborate([str(fixture)])

    err = exc_info.value
    # pyslang's SourceManager echoes back whatever path form it was given
    # to SyntaxTree.fromFile(), not necessarily normalized to match
    # str(fixture) exactly -- checked against the real returned value,
    # not assumed -- so this checks the identifying suffix, not equality.
    assert err.file.endswith("verilog_syntax_error/top.v")
    assert err.line == 16
    assert "expected ','" in str(err)
    assert ".d" in str(err)  # the real source line is included, not just the location


GENERATE_NAMING_SV = """
module leaf (input logic a);
  logic q;
  always_ff @(posedge a) q <= ~q;
endmodule

module top #(parameter int P = 1) (input logic a);
  if (P == 1) begin : g_named
    leaf u_a (.a(a));
    logic n_named;
  end else begin : g_other
    leaf u_b (.a(a));
  end
  if (P == 1) begin
    leaf u_c (.a(a));
  end
  case (P)
    1: begin : g_case leaf u_d (.a(a)); end
    default: begin leaf u_e (.a(a)); end
  endcase
  for (genvar i = 0; i < 2; i++) begin : g_for
    if (i == 0) begin : g_inner
      leaf u_f (.a(a));
    end
  end
endmodule
"""


def test_every_kind_of_generate_block_is_a_level_of_the_instance_name(tmp_path):
    """Generate blocks are hierarchy levels (IEEE 1800 27.6), so an
    instance inside one is named through it -- the path a UPF has to use
    (`u_wrap/g_1024x32.u_macro` on aura_top). A named `if` block used to
    contribute NO prefix while a `for` entry did. Covered here: a named
    `if`, an UNNAMED `if` (implicit `genblk<N>`), a `case`, and an `if`
    nested inside a `for` iteration. Each expected name is pyslang's own
    hierarchicalPath, relative to `top`."""
    source = tmp_path / "gen_naming.sv"
    source.write_text(GENERATE_NAMING_SV)
    design = elaborate([str(source)], top="top")
    top = design.definition(design.top)

    assert sorted(i.name for i in top.instances) == sorted([
        "g_named.u_a",          # named if
        "genblk2.u_c",          # unnamed if -> implicit genblk<N>
        "g_case.u_d",           # case
        "g_for[0].g_inner.u_f",  # if nested inside a for iteration
    ])
    # Not only instances: a net declared inside the block is named the same way.
    assert "g_named.n_named" in {n.name for n in top.nets}
