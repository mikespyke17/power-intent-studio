"""RTL filelist (`.f`) parsing, the manual DEFINE: field, and how the two
combine.

The parsing rules live in `frontend/src/hierarchy_view/filelist.py` as
pure functions over text and paths, so almost everything here needs no
widget. The handful of tests that do drive the Load Design tab are the
ones about the TAB's own behaviour -- which button stages what, and what
reaches elaboration.

One test reaches the real backend, and says so: proving a define genuinely
changes which `` `ifdef `` branch compiles cannot be done against mock
data, because a mock elaborator would only agree with the mock.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from frontend.src.hierarchy_view.filelist import (
    FilelistError,
    format_define,
    merge_defines,
    parse_defines_field,
    parse_filelist,
    substitute_env_vars,
)


def _write(directory: Path, name: str, text: str) -> Path:
    path = directory / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


# --- (a) plain paths, DEFINES and INCDIRS -----------------------------------


def test_a_a_filelist_with_paths_defines_and_incdirs_is_fully_parsed(tmp_path):
    _write(tmp_path, "rtl/top.sv", "module top; endmodule\n")
    _write(tmp_path, "rtl/sub.v", "module sub; endmodule\n")
    (tmp_path / "inc").mkdir()
    filelist = _write(tmp_path, "top.f", """
// the design
+define+CPU
+define+CORE=16
+incdir+inc

rtl/top.sv
rtl/sub.v
""")

    parsed = parse_filelist(str(filelist))

    assert [Path(f).name for f in parsed.files] == ["top.sv", "sub.v"]
    assert parsed.defines == {"CPU": None, "CORE": "16"}
    assert parsed.include_dirs == [str((tmp_path / "inc").resolve())]


def test_a_several_defines_and_incdirs_can_share_one_line(tmp_path):
    """The `+`-separated form, with the conventional trailing `+` --
    pinned against `dalance/verilog-filelist-parser`'s own test cases."""
    filelist = _write(tmp_path, "top.f",
                      "+define+A=1+B+C=x+\n"
                      "+incdir+d1+d2+\n")
    (tmp_path / "d1").mkdir()
    (tmp_path / "d2").mkdir()

    parsed = parse_filelist(str(filelist))

    assert parsed.defines == {"A": "1", "B": None, "C": "x"}
    assert [Path(d).name for d in parsed.include_dirs] == ["d1", "d2"]


def test_a_a_define_value_may_itself_contain_an_equals_sign(tmp_path):
    """`+define+W=a=b` defines W as `a=b` -- the split is at the FIRST
    `=`. The reference parser documents this shape explicitly."""
    filelist = _write(tmp_path, "top.f", "+define+W=a=b\n")
    assert parse_filelist(str(filelist)).defines == {"W": "a=b"}


@pytest.mark.parametrize("comment", ["// slashes", "# hash"])
def test_a_comment_lines_are_not_mistaken_for_source_files(tmp_path, comment):
    filelist = _write(tmp_path, "top.f", f"{comment}\n")
    assert parse_filelist(str(filelist)).files == []


def test_a_an_unrecognised_option_is_skipped_not_treated_as_a_file(tmp_path):
    """A real filelist carries tool-specific switches this app has no
    use for. Refusing to read the file over one, or staging `-y` as a
    source, would both be worse than recording and skipping it."""
    _write(tmp_path, "a.sv", "module a; endmodule\n")
    filelist = _write(tmp_path, "top.f", "-y /some/lib/dir\n+libext+.v+\na.sv\n")

    parsed = parse_filelist(str(filelist))

    assert [Path(f).name for f in parsed.files] == ["a.sv"]
    assert [token for _, token in parsed.ignored_options] == ["-y /some/lib/dir", "+libext+.v+"]


# --- (b) nested filelists ---------------------------------------------------


def test_b_a_nested_filelist_is_parsed_recursively(tmp_path):
    _write(tmp_path, "core/core.sv", "module core; endmodule\n")
    _write(tmp_path, "core/core.f", "+define+FROM_NESTED\ncore.sv\n")
    _write(tmp_path, "top.sv", "module top; endmodule\n")
    filelist = _write(tmp_path, "top.f", "top.sv\n-f core/core.f\n")

    parsed = parse_filelist(str(filelist))

    assert [Path(f).name for f in parsed.files] == ["top.sv", "core.sv"]
    assert parsed.defines == {"FROM_NESTED": None}
    assert [Path(f).name for f in parsed.filelists] == ["top.f", "core.f"]


@pytest.mark.parametrize("flag", ["-f", "-F", "-file"])
def test_b_every_nested_filelist_spelling_is_followed(tmp_path, flag):
    """`-F` is VCS's "paths relative to the filelist" variant and `-f`
    its "relative to the invocation directory" one. Both are followed,
    and both resolve the filelist way -- see the module docstring for
    why a GUI has no meaningful invocation directory."""
    _write(tmp_path, "nested.sv", "module nested; endmodule\n")
    _write(tmp_path, "nested.f", "nested.sv\n")
    filelist = _write(tmp_path, "top.f", f"{flag} nested.f\n")

    assert [Path(f).name for f in parse_filelist(str(filelist)).files] == ["nested.sv"]


# --- (c) cycles -------------------------------------------------------------


def test_c_a_filelist_referencing_itself_is_an_error_not_a_hang(tmp_path):
    filelist = _write(tmp_path, "loop.f", "-f loop.f\n")

    with pytest.raises(FilelistError) as excinfo:
        parse_filelist(str(filelist))
    assert "cycle" in str(excinfo.value).lower()
    assert "loop.f" in str(excinfo.value)


def test_c_an_indirect_cycle_is_detected_too(tmp_path):
    """a -> b -> c -> a. The direct case is the easy one; a chain is
    what a naive "did I just come from here" check misses."""
    _write(tmp_path, "a.f", "-f b.f\n")
    _write(tmp_path, "b.f", "-f c.f\n")
    _write(tmp_path, "c.f", "-f a.f\n")

    with pytest.raises(FilelistError) as excinfo:
        parse_filelist(str(tmp_path / "a.f"))
    assert "cycle" in str(excinfo.value).lower()


def test_c_the_same_filelist_reached_by_two_different_spellings_is_one_file(tmp_path):
    """`./nested.f` and `nested.f` are the same file, so reaching it
    twice down two branches is NOT a cycle -- cycle detection is on the
    resolved path, and a diamond is legal."""
    _write(tmp_path, "shared.sv", "module shared; endmodule\n")
    _write(tmp_path, "shared.f", "shared.sv\n")
    _write(tmp_path, "left.f", "-f ./shared.f\n")
    _write(tmp_path, "right.f", "-f shared.f\n")
    filelist = _write(tmp_path, "top.f", "-f left.f\n-f right.f\n")

    parsed = parse_filelist(str(filelist))  # must not raise
    assert [Path(f).name for f in parsed.files] == ["shared.sv"]  # deduplicated


def test_c_a_missing_filelist_is_reported_clearly(tmp_path):
    with pytest.raises(FilelistError) as excinfo:
        parse_filelist(str(tmp_path / "nope.f"))
    assert "nope.f" in str(excinfo.value)


# --- (d) relative paths resolve against the FILELIST's directory ------------


def test_d_relative_paths_resolve_against_the_filelist_not_the_cwd(tmp_path, monkeypatch):
    """The easy, common mistake this test exists for: resolving against
    the process working directory instead. The cwd is deliberately moved
    somewhere else entirely, and pointed at a DECOY file of the same
    name, so resolving the wrong way silently succeeds with the wrong
    file rather than failing loudly."""
    project = tmp_path / "project"
    _write(project, "rtl/top.sv", "module top; endmodule\n")
    (project / "inc").mkdir()
    filelist = _write(project, "f/top.f", "+incdir+../inc\n../rtl/top.sv\n")

    elsewhere = tmp_path / "elsewhere"
    _write(elsewhere, "rtl/top.sv", "module DECOY; endmodule\n")
    monkeypatch.chdir(elsewhere)

    parsed = parse_filelist(str(filelist))

    assert parsed.files == [str((project / "rtl/top.sv").resolve())]
    assert parsed.include_dirs == [str((project / "inc").resolve())]
    assert "elsewhere" not in parsed.files[0]


def test_d_a_nested_filelist_resolves_its_own_paths_against_its_own_directory(tmp_path):
    """Each filelist is its own base -- a nested one does NOT inherit
    the parent's directory, which is what makes a filelist movable with
    its own subtree."""
    _write(tmp_path, "core/core.sv", "module core; endmodule\n")
    _write(tmp_path, "core/core.f", "core.sv\n")  # relative to core/, not to the root
    filelist = _write(tmp_path, "top.f", "-f core/core.f\n")

    parsed = parse_filelist(str(filelist))
    assert parsed.files == [str((tmp_path / "core/core.sv").resolve())]


def test_d_an_absolute_path_is_used_as_is(tmp_path):
    absolute = _write(tmp_path, "abs/top.sv", "module top; endmodule\n").resolve()
    filelist = _write(tmp_path, "f/top.f", f"{absolute}\n")
    assert parse_filelist(str(filelist)).files == [str(absolute)]


def test_d_environment_variables_are_substituted(tmp_path, monkeypatch):
    _write(tmp_path, "src/top.sv", "module top; endmodule\n")
    monkeypatch.setenv("PIS_SRC", "src")
    for spelling in ("$PIS_SRC", "${PIS_SRC}", "$(PIS_SRC)"):
        filelist = _write(tmp_path, "top.f", f"{spelling}/top.sv\n")
        assert parse_filelist(str(filelist)).files == [str((tmp_path / "src/top.sv").resolve())], spelling


def test_d_an_unset_environment_variable_expands_to_nothing():
    assert substitute_env_vars("$DEFINITELY_NOT_SET_PIS/x") == "/x"


# --- Change 3 (a): the comma-separated DEFINE: field ------------------------


def test_3a_bare_and_value_carrying_defines_are_both_parsed():
    assert parse_defines_field("CPU, CORE=16, SRAM32x8") == {
        "CPU": None, "CORE": "16", "SRAM32x8": None,
    }


def test_3a_whitespace_around_entries_does_not_matter():
    assert parse_defines_field("CPU, CORE=16") == parse_defines_field("CPU,CORE=16")
    assert parse_defines_field("  CPU ,  CORE = 16  ") == {"CPU": None, "CORE": "16"}


def test_3a_empty_entries_are_skipped_rather_than_producing_a_nameless_define():
    assert parse_defines_field("") == {}
    assert parse_defines_field("CPU,,") == {"CPU": None}
    assert parse_defines_field(" , = , CPU") == {"CPU": None}


def test_3a_a_field_value_may_contain_an_equals_sign_like_a_filelist_one():
    """Consistent with `+define+` on purpose: the two sources are merged,
    so they must not disagree about what a define MEANS."""
    assert parse_defines_field("W=a=b") == {"W": "a=b"}


def test_3a_a_bare_define_and_an_empty_valued_one_are_different():
    """`CPU` and `CPU=` are not the same: one has no value, the other an
    empty one. `format_define` renders them differently."""
    assert parse_defines_field("CPU") == {"CPU": None}
    assert parse_defines_field("CPU=") == {"CPU": ""}
    assert format_define("CPU", None) == "`define CPU"
    assert format_define("CPU", "") == "`define CPU "


# --- Change 3 (c): precedence -----------------------------------------------


def test_3c_the_manual_field_wins_a_conflicting_define():
    """The CONFIRMED precedence rule: the typed field is the more
    immediate, explicit action and the one visible in the tab."""
    merged, conflicts = merge_defines({"CORE": "8"}, {"CORE": "16"})

    assert merged == {"CORE": "16"}
    assert conflicts == [("CORE", "8", "16")]


def test_3c_the_two_sources_are_combined_not_replaced():
    """The other half of the rule, and the easier one to get wrong: a
    define present in only one source survives from either."""
    merged, conflicts = merge_defines({"FROM_F": "1"}, {"FROM_FIELD": None})

    assert merged == {"FROM_F": "1", "FROM_FIELD": None}
    assert conflicts == []


def test_3c_the_same_value_from_both_sources_is_not_a_conflict():
    """Agreement is not an override, and reporting it would be noise."""
    _, conflicts = merge_defines({"CORE": "16"}, {"CORE": "16"})
    assert conflicts == []


def test_3c_a_bare_define_against_a_valued_one_counts_as_a_conflict():
    """`+define+CPU` and `CPU=1` genuinely differ, and the difference is
    visible to the preprocessor."""
    merged, conflicts = merge_defines({"CPU": None}, {"CPU": "1"})
    assert merged == {"CPU": "1"}
    assert conflicts == [("CPU", None, "1")]


# --- the Load Design tab itself ---------------------------------------------


@pytest.fixture
def tab(qtbot):
    from frontend.src.hierarchy_view.load_design_tab import LoadDesignTab

    t = LoadDesignTab()
    qtbot.addWidget(t)
    return t


def _load_filelist(tab, monkeypatch, path):
    from frontend.src.hierarchy_view import load_design_tab as module

    monkeypatch.setattr(module.QFileDialog, "getOpenFileName",
                        staticmethod(lambda *a, **k: (str(path), "")))
    tab._browse_filelist()


def test_the_filelist_button_stages_every_source_it_names(tab, monkeypatch, tmp_path):
    """Confirmed placement: a third button in the same Browse row,
    feeding the SAME staging list, so "Add" -> Loaded works unchanged."""
    _write(tmp_path, "a.sv", "module a; endmodule\n")
    _write(tmp_path, "b.v", "module b; endmodule\n")
    _write(tmp_path, "m.lib", "library(m){}\n")
    filelist = _write(tmp_path, "top.f", "+define+CPU\na.sv\nb.v\nm.lib\n")

    assert tab.browse_filelist_button.text() == "Load Filelist (.f)..."
    _load_filelist(tab, monkeypatch, filelist)

    staged = [tab.staging_list.item(i).text() for i in range(tab.staging_list.count())]
    assert [s.split("]")[0] + "]" for s in staged] == ["[RTL]", "[RTL]", "[LIB]"]
    # ...and the usual Add step moves them on untouched.
    tab._load_staged_files()
    assert tab.loaded_list.count() == 3


def test_a_broken_filelist_reports_and_stages_nothing(tab, monkeypatch, tmp_path):
    """A cyclic filelist must leave the tab exactly as it was -- partial
    staging would put it in a state the user never asked for."""
    from frontend.src.hierarchy_view import load_design_tab as module

    filelist = _write(tmp_path, "loop.f", "-f loop.f\n")
    shown = []
    monkeypatch.setattr(module.message_box, "critical", lambda *a, **k: shown.append(a[2]))

    _load_filelist(tab, monkeypatch, filelist)

    assert shown and "cycle" in shown[0].lower()
    assert tab.staging_list.count() == 0


def test_the_tab_combines_filelist_and_manual_defines(tab, monkeypatch, tmp_path):
    filelist = _write(tmp_path, "top.f", "+define+FROM_F=1\n+define+CORE=8\n")
    _load_filelist(tab, monkeypatch, filelist)
    tab.defines_edit.setText("CPU, CORE=16")

    assert tab.current_defines() == {"FROM_F": "1", "CORE": "16", "CPU": None}
    assert tab.define_conflicts() == [("CORE", "8", "16")]


def test_a_define_override_is_a_non_blocking_warning(tab, monkeypatch, tmp_path):
    """Reported, never silent -- and never fatal: overriding a filelist
    from the field is a legitimate everyday action."""
    from frontend.src.shared.models.validation import Severity

    filelist = _write(tmp_path, "top.f", "+define+CORE=8\n")
    _load_filelist(tab, monkeypatch, filelist)
    tab.defines_edit.setText("CORE=16")

    diagnostics = tab._define_conflict_diagnostics()
    assert len(diagnostics) == 1
    assert diagnostics[0].severity is Severity.WARNING
    assert diagnostics[0].code == "DEFINE-OVERRIDE"
    assert "'8'" in diagnostics[0].message and "'16'" in diagnostics[0].message
    assert "DEFINE: field wins" in diagnostics[0].message


def test_defines_and_incdirs_reach_the_elaborate_action(tab, monkeypatch, tmp_path):
    """The whole point of the two features: what the tab collected has
    to arrive at elaboration."""
    (tmp_path / "inc").mkdir()
    _write(tmp_path, "a.sv", "module a; endmodule\n")
    filelist = _write(tmp_path, "top.f", "+define+FROM_F\n+incdir+inc\na.sv\n")
    _load_filelist(tab, monkeypatch, filelist)
    tab._load_staged_files()
    tab.defines_edit.setText("CPU, CORE=16")

    seen = {}

    def _action(rtl_files, lib_files, top, defines=None, include_dirs=None):
        seen.update(defines=defines, include_dirs=include_dirs, rtl=rtl_files)
        return None, []

    tab._elaborate_action = _action
    tab._finish_real_elaborate()

    assert seen["defines"] == {"FROM_F": None, "CPU": None, "CORE": "16"}
    assert [Path(d).name for d in seen["include_dirs"]] == ["inc"]
    assert [Path(f).name for f in seen["rtl"]] == ["a.sv"]


def test_a_three_argument_elaborate_action_still_works(tab):
    """Every pre-existing ElaborateAction takes three arguments. Widening
    the protocol outright would break all of them at once, so the two
    new arguments are passed only to a callable with room for them."""
    calls = []

    def _legacy(rtl_files, lib_files, top):
        calls.append((rtl_files, lib_files, top))
        return None, []

    tab._elaborate_action = _legacy
    tab._finish_real_elaborate()  # must not raise
    assert len(calls) == 1


def test_the_shim_does_not_swallow_a_type_error_from_inside_the_action(tab):
    """A `try/except TypeError` around the wide call would also catch a
    genuine TypeError raised INSIDE the action and silently retry -- the
    real error gone, presenting as "Elaborate did nothing"."""
    def _raises(rtl_files, lib_files, top, defines=None, include_dirs=None):
        raise TypeError("a real bug inside the action")

    tab._elaborate_action = _raises
    with pytest.raises(TypeError, match="a real bug inside the action"):
        tab._finish_real_elaborate()


# --- Change 3 (b): defines genuinely reach the REAL elaborator --------------
#
# WHY THIS ONE TOUCHES THE BACKEND. Everything above proves the defines are
# parsed and handed on. Only a real elaboration can prove they CHANGE what
# compiles -- against a mock, the assertion would be that the mock agrees
# with itself. Same sanctioned exception as the Load Power Intent mapping
# suite, and it reaches the backend only through `backend.api`.


def test_3b_a_define_from_either_source_changes_which_ifdef_branch_compiles(tmp_path):
    from backend.api import elaborate_rtl

    _write(tmp_path, "dut.sv", """
module dut(input wire clk);
`ifdef CPU
  cpu_flavour u_impl(.clk(clk));
`else
  plain_flavour u_impl(.clk(clk));
`endif
endmodule
module cpu_flavour(input wire clk); endmodule
module plain_flavour(input wire clk); endmodule
""")
    sources = [str(tmp_path / "dut.sv")]

    def _chosen(defines):
        design = elaborate_rtl(sources, [], "dut", defines=defines)
        instance = design.definitions[design.top].instances[0]
        return design.definitions[instance.definition_id].base_name

    # No defines at all: unchanged behaviour, the `else` branch.
    assert _chosen(None) == "plain_flavour"
    # From the filelist...
    filelist = _write(tmp_path, "top.f", "+define+CPU\ndut.sv\n")
    assert _chosen(parse_filelist(str(filelist)).defines) == "cpu_flavour"
    # ...and from the DEFINE: field, reaching the same place.
    assert _chosen(parse_defines_field("CPU")) == "cpu_flavour"


@pytest.mark.parametrize("width", ["8", "16"])
def test_3b_a_value_carrying_define_arrives_with_its_actual_value(tmp_path, width):
    """A bare define only has to EXIST. A valued one has to arrive with
    the RIGHT value -- a different thing to get wrong, and invisible to
    an `ifdef`, which only tests existence. Asserted on a real port
    width, which the value determines."""
    from backend.api import elaborate_rtl

    _write(tmp_path, "dut.sv", """
module dut(input wire [`WIDTH-1:0] bus);
endmodule
""")
    design = elaborate_rtl([str(tmp_path / "dut.sv")], [], "dut",
                           defines=parse_defines_field(f"WIDTH={width}"))
    bus = next(p for p in design.definitions[design.top].ports if p.name == "bus")
    assert bus.width == int(width)


def test_3b_combined_defines_from_both_sources_reach_the_elaborator_together(tmp_path):
    """Both sources at once, which is the case the "combine, don't
    replace" rule exists for."""
    from backend.api import elaborate_rtl

    _write(tmp_path, "dut.sv", """
module dut(input wire clk);
`ifdef FROM_FILELIST
  `ifdef FROM_FIELD
    both u_impl(.clk(clk));
  `else
    only_filelist u_impl(.clk(clk));
  `endif
`else
  neither u_impl(.clk(clk));
`endif
endmodule
module both(input wire clk); endmodule
module only_filelist(input wire clk); endmodule
module neither(input wire clk); endmodule
""")
    filelist = _write(tmp_path, "top.f", "+define+FROM_FILELIST\ndut.sv\n")
    merged, _ = merge_defines(parse_filelist(str(filelist)).defines,
                              parse_defines_field("FROM_FIELD"))

    design = elaborate_rtl([str(tmp_path / "dut.sv")], [], "dut", defines=merged)
    instance = design.definitions[design.top].instances[0]
    assert design.definitions[instance.definition_id].base_name == "both"


def test_3c_the_manual_value_is_what_actually_reaches_the_elaborator(tmp_path):
    """The precedence rule, proven where it matters -- not just in the
    merge helper, but in what the compiler actually saw."""
    from backend.api import elaborate_rtl

    _write(tmp_path, "dut.sv", """
module dut(input wire [`CORE-1:0] bus);
endmodule
""")
    filelist = _write(tmp_path, "top.f", "+define+CORE=8\ndut.sv\n")
    merged, conflicts = merge_defines(parse_filelist(str(filelist)).defines,
                                      parse_defines_field("CORE=16"))
    assert conflicts == [("CORE", "8", "16")]

    design = elaborate_rtl([str(tmp_path / "dut.sv")], [], "dut", defines=merged)
    bus = next(p for p in design.definitions[design.top].ports if p.name == "bus")
    assert bus.width == 16  # the MANUAL value, not the filelist's 8


def test_the_define_field_sits_directly_above_top_module(tab):
    """Placement, pinned in VISUAL order rather than by asserting the
    two widgets merely exist -- "directly above" is the whole point of
    the request, and existence alone would pass with the field anywhere
    on the tab.

    Both live in the "Loaded" column now, in the order Elaborate reads
    them: what to define, then what to elaborate."""
    from PySide6.QtWidgets import QLabel, QLineEdit

    column = tab.defines_edit.parent().layout()
    order = []

    def _walk(layout):
        for index in range(layout.count()):
            item = layout.itemAt(index)
            if item.widget() is not None:
                order.append(item.widget())
            elif item.layout() is not None:
                _walk(item.layout())

    _walk(column)
    labels = [w.text() for w in order if isinstance(w, QLabel)]
    assert labels.index("DEFINE:") < labels.index("Top Module")

    # ...and nothing sits between them: the DEFINE field is immediately
    # followed by the Top Module label.
    assert order[order.index(tab.defines_edit) + 1].text() == "Top Module"
    assert isinstance(order[order.index(tab.defines_edit) - 1], QLabel)
    assert order[order.index(tab.defines_edit) - 1].text() == "DEFINE:"
