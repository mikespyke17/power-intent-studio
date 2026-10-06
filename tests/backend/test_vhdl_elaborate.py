"""1b: real GHDL+Yosys-driven parsing of the VHDL golden fixtures, checked
against the same golden IR the SV side already proved converges (1c) and,
for new VHDL-only concerns, against direct assertions.

Requires `ghdl` and `yosys` (with the ghdl-yosys-plugin) on PATH -- see
docs/milestone1_rtl_elaborator.md for the distribution/packaging status
(explicitly deferred, not resolved). `GHDL_PREFIX` must point at GHDL's
VHDL library install (e.g. OSS CAD Suite's `lib/ghdl`) if `ghdl` doesn't
already resolve it on its own.

Every test here is skipped (not failed) when `ghdl`/`yosys` aren't on
PATH -- this toolchain isn't pip-installable (that's the whole distribution
finding), so it must not break `pytest` for anyone who hasn't set up OSS
CAD Suite or equivalent locally.
"""

import shutil
from pathlib import Path

import pytest

from backend.elaborator.rtl.ir import Edge, ResetKind, SourceLanguage
from backend.elaborator.rtl.vhdl import elaborate
from tests.backend.golden_ir_fixtures import build_sub, build_sub_no_reset

pytestmark = pytest.mark.skipif(
    shutil.which("ghdl") is None or shutil.which("yosys") is None,
    reason="ghdl/yosys not on PATH -- see docs/milestone1_rtl_elaborator.md for setup (OSS CAD Suite or equivalent)",
)

FIXTURE_DIR = Path(__file__).parent / "golden_rtl" / "vhdl_flop_min"
THREE_LEVEL_DIR = Path(__file__).parent / "golden_rtl" / "vhdl_three_level_hierarchy"
PARAMETRIZED_DIR = Path(__file__).parent / "golden_rtl" / "vhdl_parametrized"
NO_RESET_DIR = Path(__file__).parent / "golden_rtl" / "vhdl_flop_no_reset"
PACKAGES_DIR = Path(__file__).parent / "golden_rtl" / "vhdl_packages"
MULTIPLE_PROCESSES_DIR = Path(__file__).parent / "golden_rtl" / "vhdl_multiple_processes"
COMMENT_DIR = Path(__file__).parent / "golden_rtl" / "vhdl_comment_with_entity_keyword"
GENERIC_AT_DEFAULT_DIR = Path(__file__).parent / "golden_rtl" / "vhdl_generic_at_default"


def test_vhdl_flop_min_matches_golden_ir():
    """Same golden IR verilog_flop_min already converges to in test_ir_schema.py
    and test_verilog_elaborate.py -- proves the VHDL front-end lands on the
    identical shape as the SV one for a structurally-equivalent design, the
    same convergence property 1c was designed to guarantee.
    """
    design = elaborate([str(FIXTURE_DIR / "sub.vhd"), str(FIXTURE_DIR / "top.vhd")], top="top")
    actual_top = design.definition(design.top)
    actual_sub = design.definition(actual_top.instances[0].definition_id)

    expected_sub = build_sub(SourceLanguage.VHDL, str(FIXTURE_DIR / "sub.vhd"))
    assert actual_sub.ports == expected_sub.ports
    assert actual_sub.sequential_elements == expected_sub.sequential_elements

    assert [p.name for p in actual_top.ports] == ["clk", "rst_n", "data_in", "data_out"]
    assert [i.name for i in actual_top.instances] == ["u_sub"]
    conns = {c.instance_port: c.connected_to for c in actual_top.instances[0].connections}
    assert conns == {"clk": "clk", "rst_n": "rst_n", "d": "data_in", "q": "data_out"}


def test_vhdl_three_level_hierarchy():
    """Mirrors test_three_level_hierarchy_walk_and_connectivity (Verilog/1g) --
    top -> mid -> leaf, direct entity instantiation at every level, no generics.
    """
    design = elaborate(
        [str(THREE_LEVEL_DIR / "leaf.vhd"), str(THREE_LEVEL_DIR / "mid.vhd"), str(THREE_LEVEL_DIR / "top.vhd")],
        top="top",
    )
    nodes = list(design.walk())
    assert [n.path for n in nodes] == ["top", "top/u_mid", "top/u_mid/u_leaf"]
    assert [n.definition.base_name for n in nodes] == ["top", "mid", "leaf"]

    top = design.definition(design.top)
    mid = design.definition(top.instances[0].definition_id)
    leaf = design.definition(mid.instances[0].definition_id)

    top_conns = {c.instance_port: c.connected_to for c in top.instances[0].connections}
    assert top_conns == {"clk": "clk", "rst_n": "rst_n", "d": "data_in", "q": "data_out"}
    mid_conns = {c.instance_port: c.connected_to for c in mid.instances[0].connections}
    assert mid_conns == {"clk": "clk", "rst_n": "rst_n", "d": "d", "q": "q"}

    assert len(leaf.sequential_elements) == 1
    assert leaf.sequential_elements[0].output_signal == "q"


def test_vhdl_parametrized_instances_produce_distinct_definitions():
    """Mirrors test_parametrized_instances_produce_distinct_definitions
    (Verilog/1a), but exercises a completely different code path: Yosys/GHDL
    encode resolved generics into a mangled module name
    (`sub_Brtl_8`/`sub_Brtl_16`), not a resolved-parameters API the way
    pyslang's ParameterSymbol.value was. This is what
    _reconstruct_base_name_and_parameters exists to undo -- confirming it
    actually works end-to-end, not just recognizing the module exists.
    """
    design = elaborate([str(PARAMETRIZED_DIR / "sub.vhd"), str(PARAMETRIZED_DIR / "top.vhd")], top="top")
    top = design.definition(design.top)
    assert [i.name for i in top.instances] == ["u_sub_a", "u_sub_b"]

    inst_a, inst_b = top.instances
    assert inst_a.definition_id != inst_b.definition_id

    def_a = design.definition(inst_a.definition_id)
    def_b = design.definition(inst_b.definition_id)
    assert def_a.base_name == def_b.base_name == "sub"
    # VHDL is case-insensitive; GHDL normalizes identifiers to lowercase --
    # "WIDTH" in source, "width" recovered here. Intentional, not a bug.
    assert def_a.parameters == {"width": "8"}
    assert def_b.parameters == {"width": "16"}

    d_port_a = next(p for p in def_a.ports if p.name == "d")
    d_port_b = next(p for p in def_b.ports if p.name == "d")
    assert d_port_a.width == 8
    assert d_port_b.width == 16

    assert len(design.definitions) == 3  # sub(WIDTH=8), sub(WIDTH=16), top


def test_vhdl_flop_no_reset_matches_golden_ir():
    design = elaborate([str(NO_RESET_DIR / "sub.vhd")], top="sub")
    actual = design.definition(design.top)
    expected = build_sub_no_reset(SourceLanguage.VHDL, str(NO_RESET_DIR / "sub.vhd"))
    assert actual.ports == expected.ports
    assert actual.sequential_elements == expected.sequential_elements

    seq = actual.sequential_elements[0]
    assert seq.reset_ref is None
    assert seq.reset_kind == ResetKind.NONE
    assert seq.reset_active_low is None


def test_vhdl_package_constant_resolves_port_width_with_no_code_changes():
    """`use work.pkg.all;` + an unqualified package constant sizing a port --
    same finding as the SV package test (test_package_scoped_parameters_resolve_correctly):
    GHDL's elaboration resolves this through the same path as any other
    static expression, no special-casing needed in elaborate.py.
    """
    design = elaborate([str(PACKAGES_DIR / "pkg.vhd"), str(PACKAGES_DIR / "sub.vhd")], top="sub")
    sub = design.definition(design.top)
    assert [(p.name, p.width) for p in sub.ports] == [("clk", 1), ("d", 8), ("q", 8)]
    assert len(sub.sequential_elements) == 1


def test_vhdl_flop_min_reset_polarity_traced_through_inverter():
    """The specific finding from the 1b spike: GHDL's synthesis frontend
    does not always wire a flop's async-reset pin directly to the named
    reset signal -- for an active-low source condition (`if rst_n = '0'`)
    it can insert a `$not` cell between the named signal and the $adff
    primitive's own (active-high-normalized) ARST pin. Reading
    ARST_POLARITY at face value without tracing through that inverter would
    get this backwards. This asserts the traced-through result directly,
    not just equality with the golden IR, so a regression here fails
    specifically on the polarity/tracing logic rather than an opaque
    equality diff.
    """
    design = elaborate([str(FIXTURE_DIR / "sub.vhd"), str(FIXTURE_DIR / "top.vhd")], top="top")
    top = design.definition(design.top)
    sub = design.definition(top.instances[0].definition_id)
    seq = sub.sequential_elements[0]

    assert seq.clock_ref == "clk"
    assert seq.edge == Edge.RISING
    assert seq.reset_ref == "rst_n"
    assert seq.reset_kind == ResetKind.ASYNC
    assert seq.reset_active_low is True


def test_vhdl_multiple_processes_both_flops_recognized():
    """VHDL has no direct analog of the SV 'multiple always blocks per
    module' gap. Sequential-element extraction here works on Yosys's
    already-elaborated cell list ($dff/$adff primitives), not by walking
    one `process` statement's AST shape at a time the way the SV side walks
    one `always` block at a time -- so a second, independent `process` in
    the same architecture, driving a different register, is picked up
    automatically rather than requiring its own recognized code path.
    Verified here rather than assumed from the implementation.
    """
    design = elaborate([str(MULTIPLE_PROCESSES_DIR / "multiproc.vhd")], top="multiproc")
    mod = design.definition(design.top)
    assert len(mod.sequential_elements) == 2

    by_output = {seq.output_signal: seq for seq in mod.sequential_elements}
    assert by_output["a_out"].reset_ref == "rst_n"
    assert by_output["a_out"].reset_kind == ResetKind.ASYNC
    assert by_output["b_out"].reset_ref is None
    assert by_output["b_out"].reset_kind == ResetKind.NONE


def test_vhdl_comment_mentioning_entity_keyword_is_not_mistaken_for_a_declaration():
    """Regression test for a real bug found while documenting the generics
    text-scanner's boundaries: a comment like
    `-- this used to be "entity old_counter is"` was matched by the
    entity-declaration regex before comment-stripping was added, producing
    a phantom 'old_counter' entry in the generic-name lookup table. Fixed
    by _strip_comments, which blanks out `--`/`/* */` comments (respecting
    string literals) before any scanning happens.
    """
    design = elaborate([str(COMMENT_DIR / "commented.vhd")], top="commented")
    assert len(design.definitions) == 1
    mod = design.definition(design.top)
    assert mod.base_name == "commented"
    assert [p.name for p in mod.ports] == ["clk", "d", "q"]


def test_vhdl_generic_left_at_default_still_recovers_its_value():
    """This is the shape that broke _reconstruct_base_name_and_parameters
    while adding the two tests above it, caught by a full suite run before
    calling anything done: a *nested* entity with a declared generic, whose
    instantiation supplies no generic map at all (`u_sub : entity work.sub
    port map (...)`, no `generic map`), does NOT come back from Yosys as a
    bare `sub_brtl` the way a truly generic-less nested entity does --
    it still gets the resolved value appended (`sub_brtl_8`), same as an
    explicit override would. An earlier version of the reconstruction logic
    assumed "unmangled or default" were the same case and got this backwards.
    """
    design = elaborate(
        [str(GENERIC_AT_DEFAULT_DIR / "sub.vhd"), str(GENERIC_AT_DEFAULT_DIR / "top.vhd")], top="top"
    )
    top = design.definition(design.top)
    sub = design.definition(top.instances[0].definition_id)
    assert sub.base_name == "sub"
    assert sub.parameters == {"width": "8"}
    assert next(p for p in sub.ports if p.name == "d").width == 8
