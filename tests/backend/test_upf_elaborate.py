"""2a: UPF elaborator, exercising the real Tcl interpreter (tkinter.Tcl()) --
not a mock of Tcl or of UPF command parsing.

See docs/milestone2_powerintent_elaborator.md for the full design writeup: the
Tcl-runner spike (and its two gotchas -- list-arg splitting and
exception-message preservation, both required to be centralized wrappers,
not per-handler), the version-handling algorithm (auto-detect only,
immediate rejection of unsupported/missing/conflicting versions, a
whole-design not per-file backstop), and the accumulate-then-freeze
builder design this module's tests indirectly exercise via the public
`elaborate()` entry point.
"""

import os
from pathlib import Path

import pytest

from backend.elaborator.power_intent.ir import PowerStateShape, make_power_intent_id
from backend.elaborator.power_intent.upf import elaborate

GOLDEN = Path(__file__).parent / "golden_upf"


# --- full strategy coverage, single file ---------------------------------


def test_basic_upf_covers_every_construct_category():
    """One file exercising every construct category the doc's 'full
    strategy coverage from day one' decision requires: domains, supply
    ports/nets/sets, switches, isolation, retention, level-shifters, power
    states, domain-supply connections, power state groups.
    """
    design = elaborate(str(GOLDEN / "upf_basic" / "top.upf"))

    assert design.source_version == "3.1"

    domain = design.domains["::PD_TOP"]
    assert domain.name == "PD_TOP"
    assert domain.elements == ["."]
    assert domain.exclude_elements == ["u_dbg"]

    assert design.supply_ports["::VDD_IN"].direction == "in"
    assert "::VDD" in design.supply_nets

    supply_set = design.supply_sets["::SS_CORE"]
    assert (supply_set.power_net, supply_set.ground_net) == ("VDD", "VSS")

    switch = design.switches["::SW_CORE"]
    assert switch.input_supply == "VDD"
    assert switch.output_supply == "VDD_CORE"
    assert switch.control_ports == ["sleep_n"]

    iso = design.isolation_strategies["::ISO_CORE"]
    assert (iso.source, iso.sink, iso.location, iso.clamp_value, iso.isolation_supply) == (
        "PD_TOP",
        "PD_TOP",
        "fanout",
        "0",
        "SS_CORE",
    )
    assert iso.elements == ["u_core/out1", "u_core/out2"]

    ret = design.retention_strategies["::RET_CORE"]
    assert (ret.elements, ret.retention_supply, ret.save_signal, ret.restore_signal) == (
        ["u_core"],
        "SS_CORE",
        "save_n",
        "restore_n",
    )

    ls = design.level_shifter_strategies["::LS_CORE"]
    assert (ls.source, ls.sink, ls.location) == ("PD_TOP", "PD_TOP", "self")
    assert ls.elements == ["u_core/out3"]

    on_state = design.power_states["::SS_CORE.ON"]
    off_state = design.power_states["::SS_CORE.OFF"]
    assert on_state.expr == "sleep_n == 1"
    assert off_state.expr == "sleep_n == 0"
    assert on_state.shape == PowerStateShape.ADD_POWER_STATE
    assert (on_state.domain, on_state.supply, on_state.complete) == ("PD_TOP", "SS_CORE", True)
    assert (off_state.domain, off_state.supply, off_state.complete) == (None, None, False)

    # create_supply_net -domain, connect_supply_net -domain, and
    # set_domain_supply_net all synthesize DomainSupplyConnection --
    # confirms none of the three silently drops the association just
    # because it wasn't one of the other two.
    connections = {(c.domain, c.supply_net, c.role) for c in design.domain_supply_connections}
    assert ("PD_TOP", "VDD", None) in connections
    assert ("PD_TOP", "VDD_CORE", None) in connections
    assert ("PD_TOP", "VDD", "primary_power") in connections
    assert ("PD_TOP", "VSS", "primary_ground") in connections

    group = design.power_state_groups["::PSG_TOP"]
    assert group.members == ["SS_CORE"]


# --- hierarchical scope (load_upf) ----------------------------------------


def test_load_upf_scopes_nested_constructs_and_inherits_version():
    """sub_domain.upf doesn't redeclare a version -- must inherit the top's,
    not error, and its constructs must carry the load_upf -scope prefix."""
    design = elaborate(str(GOLDEN / "upf_hierarchical" / "top.upf"))

    assert design.source_version == "3.1"
    assert "::PD_TOP" in design.domains
    sub = design.domains["u_core::PD_SUB"]
    assert sub.scope == "u_core"
    assert sub.elements == ["u_inner"]

    assert "u_core::ISO_SUB" in design.isolation_strategies


def test_load_upf_relative_path_resolves_against_sourcing_file_not_cwd(tmp_path, monkeypatch):
    """Real bug caught during manual smoke-testing: Tcl's `source` resolves
    a relative path against the process cwd, not the sourcing file's own
    directory. Proven here by running from an unrelated cwd.
    """
    monkeypatch.chdir(tmp_path)
    design = elaborate(str(GOLDEN / "upf_hierarchical" / "top.upf"))
    assert "u_core::PD_SUB" in design.domains


# --- legacy UPF 2.1 PST -> canonical PowerState translation ---------------


def test_legacy_pst_rows_translate_to_conjunctive_power_states():
    design = elaborate(str(GOLDEN / "upf_pst_legacy" / "top.upf"))

    assert design.source_version == "2.1"
    on_state = design.power_states["::PST_MAIN.ON_STATE"]
    off_state = design.power_states["::PST_MAIN.OFF_STATE"]

    assert on_state.applies_to == "PST_MAIN"
    assert on_state.expr == "VDD == 1.0 && VDD_IO == 1.8"
    assert on_state.shape == PowerStateShape.PST_ROW

    assert off_state.expr == "VDD == 0 && VDD_IO == 0"
    assert off_state.shape == PowerStateShape.PST_ROW


# --- version handling: the whole point of this review round ---------------


def test_missing_top_level_version_declaration_raises_clearly():
    with pytest.raises(Exception, match="version declaration"):
        elaborate(str(GOLDEN / "upf_version_missing" / "top.upf"))


def test_unsupported_version_raises_naming_the_exact_version():
    with pytest.raises(Exception, match="2.0"):
        elaborate(str(GOLDEN / "upf_version_unsupported" / "top.upf"))


def test_conflicting_per_file_versions_raise_naming_both():
    with pytest.raises(Exception, match="3.1") as exc_info:
        elaborate(str(GOLDEN / "upf_version_conflict" / "top.upf"))
    assert "2.1" in str(exc_info.value)


def test_power_intent_verb_before_any_version_declaration_raises_immediately():
    """Reached through a sourced helper file, not just directly in the top
    file -- confirms the check isn't scoped to only the top-level file's
    own command sequence."""
    with pytest.raises(Exception, match="version declaration"):
        elaborate(str(GOLDEN / "upf_verb_before_version" / "top.upf"))


def test_degenerate_whole_design_backstop_fires_when_nothing_resolves_a_version():
    with pytest.raises(Exception, match="never declared a version"):
        elaborate(str(GOLDEN / "upf_degenerate_backstop" / "top.upf"))


def test_helper_file_before_version_declaration_never_fails_on_its_own():
    """The backstop and the verb-before-version check are both whole-design
    scoped, not per-file: a sourced helper with zero power-intent commands
    and no version of its own must never fail just for existing, as long as
    the design as a whole eventually resolves a version and does real work.
    """
    design = elaborate(str(GOLDEN / "upf_helper_before_version" / "top.upf"))
    assert design.source_version == "3.1"
    assert "::PD_TOP" in design.domains


# --- Tcl spike gotchas, re-confirmed against the real elaborator ----------


def test_unrecognized_command_is_reported_with_its_line_not_silently():
    """SUPERSEDES "fails loudly" (explicit request): a UPF verb that is not
    registered no longer aborts the whole file -- it is skipped and
    RECORDED, with its line, so the rest of the file is still read and
    the user is told exactly what was not understood. Still never a
    silent no-op."""
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "unknown.upf"
        path.write_text("upf_version 3.1\nsome_totally_unregistered_upf_command foo bar\ncreate_supply_port VDD\n")
        design = elaborate(str(path))
    assert [(u.command, u.source_loc.line) for u in design.unrecognized] == [("some_totally_unregistered_upf_command", 2)]
    assert [p.name for p in design.supply_ports.values()] == ["VDD"]


# --- version_gates.py: -location fanout requires UPF 3.1+ -----------------


def test_location_fanout_rejected_under_2_1_on_isolation():
    with pytest.raises(Exception, match="requires UPF 3.1"):
        elaborate(str(GOLDEN / "upf_location_gate_isolation_rejected" / "top.upf"))


def test_location_fanout_rejected_under_3_0_on_level_shifter():
    with pytest.raises(Exception, match="requires UPF 3.1"):
        elaborate(str(GOLDEN / "upf_location_gate_level_shifter_rejected" / "top.upf"))


def test_location_fanout_allowed_under_3_1():
    """upf_basic/top.upf already uses -location fanout under upf_version
    3.1 -- confirms the gate doesn't reject the version it's meant for."""
    design = elaborate(str(GOLDEN / "upf_basic" / "top.upf"))
    assert design.isolation_strategies["::ISO_CORE"].location == "fanout"


def test_non_gated_location_value_allowed_under_2_1():
    """Only 'fanout' is gated -- confirms the gate doesn't over-reject other
    -location values (self/other/parent) under earlier versions."""
    design = elaborate(str(GOLDEN / "upf_location_non_gated_value_allowed" / "top.upf"))
    assert design.isolation_strategies["::ISO_CORE"].location == "parent"


# --- feature-completeness audit follow-ups ---------------------------------


def test_unrecognized_option_on_a_known_command_is_rejected_not_silently_dropped():
    """The systemic gap found by the audit: a handler that only reads the
    flags it knows about via _one(parsed, key) would otherwise silently
    drop any other flag's data with no error. Now enforced centrally in
    tcl_runtime.py's _parse_args, before the handler ever runs."""
    # SUPERSEDES the abort (explicit request): the command is skipped
    # WHOLE -- never half-applied with the option's data dropped -- and
    # recorded with its line and the option it did not recognize.
    design = elaborate(str(GOLDEN / "upf_unrecognized_option_rejected" / "top.upf"))
    (record,) = design.unrecognized
    assert "unrecognized option" in record.reason
    assert record.source_loc.line is not None


def test_connect_supply_net_ports_form_is_recorded_not_rejected():
    """SUPERSEDES an earlier test asserting the opposite. `-ports` was a
    deliberately-unregistered gap: it doesn't fit the net-to-domain shape
    DomainSupplyConnection was built for, and failing loud was preferred
    over guessing at a schema.

    Round-trip import supplied the motivating usage that gap was waiting
    for -- the GUI generates this form for boundary port wiring AND for
    every macro PG-pin connection, so its own output could not be read
    back. Resolved by adding `ports` to DomainSupplyConnection, with
    `domain` left empty: `-ports` names ports and carries no domain at
    all, and inventing one would be exactly the guess the original gap
    avoided."""
    design = elaborate(str(GOLDEN / "upf_connect_supply_net_ports" / "top.upf"))

    connections = [c for c in design.domain_supply_connections if c.ports]
    assert len(connections) == 1
    assert connections[0].supply_net == "VDD"
    assert connections[0].ports == ["p1", "p2"]
    assert connections[0].domain == ""  # no domain in the -ports form
    assert connections[0].role is None


def test_connect_supply_net_with_no_recognized_data_raises():
    with pytest.raises(Exception, match="no recognized data to record"):
        elaborate(str(GOLDEN / "upf_connect_supply_net_no_data" / "top.upf"))


# --- the app's own generated dialect ---------------------------------------
#
# Every construct below is one the GUI's "Extract Power Intent" emits, in
# the exact spelling it emits, mirroring this project's own
# examples/upf_example_1_sram/upf/golden.upf.
#
# None of it parsed before. The elaborator was scoped to a narrower UPF
# subset chosen when the goal was reading third-party files, so the app
# could not read its OWN output -- a generated file failed on its very
# first line (`set_scope .`), and all 14 generated constructs were
# rejected when tested individually. The generator is the spec-correct
# side here (its syntax was researched against golden.upf), so the
# elaborator was extended to meet it rather than the reverse.


def _generated_dialect():
    return elaborate(str(GOLDEN / "upf_generated_dialect" / "top.upf"))


def test_set_upf_version_and_set_scope_are_accepted():
    """`set_scope .` opens every generated file and was an unknown
    command; the version appeared only in a comment banner, so the file
    never actually declared it."""
    design = _generated_dialect()
    assert design.source_version == "3.1"
    # `.` normalizes to the empty top scope, so a construct declared
    # after set_scope . is scoped identically to one declared before any.
    assert design.domains[make_power_intent_id("", "PD_TOP")].scope == ""


def test_supply_set_accepts_the_supply_spelling_as_well_as_function():
    design = _generated_dialect()
    supply_set = design.supply_sets[make_power_intent_id("", "SS_VDD")]
    assert (supply_set.power_net, supply_set.ground_net) == ("VDD", "VSS")


def test_power_domain_supply_pairs_split_into_primary_and_available():
    """`-supply` is a repeatable {role set} PAIR. Reading it as a scalar
    previously made the literal list ['primary', 'SS_VDD'] the primary
    supply; secondary roles had nowhere to go at all."""
    design = _generated_dialect()
    top = design.domains[make_power_intent_id("", "PD_TOP")]
    assert top.primary_supply == "SS_VDD"
    assert top.available_supplies == ["SS_VDD_SUB"]
    # `-available_supplies {..}` is the other spelling for the same thing
    # (golden.upf uses it); both land in the same field.
    sub = design.domains[make_power_intent_id("", "PD_SUB")]
    assert sub.primary_supply == "SS_VDD_SUB"
    assert sub.available_supplies == ["SS_VDD"]


def test_power_switch_paired_ports_states_and_mapped_cell():
    design = _generated_dialect()
    switch = design.switches[make_power_intent_id("", "PSW_SUB")]
    # {role net} pairs -- previously the whole ['in', 'VDD'] list.
    assert switch.input_supply == "VDD"
    assert switch.output_supply == "VDD_SUB"
    assert switch.control_ports == ["pwr_en"]
    assert [(s.name, s.boolean_expr) for s in switch.states] == [("on", "ctrl"), ("off", "!ctrl")]
    # map_power_switch is a SEPARATE command refining the same switch.
    assert switch.cell_type == "HDR_CELL"


def test_isolation_supply_set_signal_and_sense():
    design = _generated_dialect()
    iso = design.isolation_strategies[make_power_intent_id("", "ISO_SUB")]
    assert iso.isolation_supply == "SS_VDD"  # from -isolation_supply_set
    assert iso.isolation_signal == "iso_en"
    assert iso.isolation_sense == "high"
    assert iso.clamp_value == "0"


def test_retention_supply_pairs_and_separate_control_command():
    design = _generated_dialect()
    ret = design.retention_strategies[make_power_intent_id("", "RET_SUB")]
    assert ret.primary_supply == "SS_VDD_SUB"
    assert ret.retention_supply == "SS_VDD"
    # set_retention_control is a separate command filling in the signals.
    assert (ret.save_signal, ret.restore_signal) == ("save_n", "restore_n")


def test_level_shifter_applies_to_rule_threshold_and_mapped_cell():
    design = _generated_dialect()
    ls = design.level_shifter_strategies[make_power_intent_id("", "LS_SUB")]
    assert (ls.applies_to, ls.rule, ls.threshold) == ("outputs", "low_to_high", "0.4")
    assert ls.cell_type == "LS_CELL"  # from the separate set_level_shifter_cell


def test_add_power_state_repeatable_state_pairs_become_separate_states():
    """`-state` is both repeatable AND pair-valued. The original handler
    read one scalar, so a three-state supply collapsed into a single
    state whose name was the literal list ['ON', '1.2']."""
    design = _generated_dialect()
    by_supply = {}
    for ps in design.power_states.values():
        by_supply.setdefault(ps.applies_to, {})[ps.name] = ps.expr
    assert by_supply["VDD"] == {"ON": "1.2", "OFF": "off"}
    assert by_supply["VDD_SUB"] == {"ON": "1.2", "SWITCH_OFF": "0.0", "OFF": "off"}


def test_set_port_attributes_records_one_construct_per_occurrence():
    """A file legitimately issues several (the GUI emits one per supply
    role); merging them by port would lose which command said what."""
    design = _generated_dialect()
    records = sorted(design.port_attributes.values(), key=lambda p: p.ports)
    assert [(r.ports, r.driver_supply, r.receiver_supply) for r in records] == [
        (["clk", "cen_b"], "SS_VDD", None),
        (["q"], None, "SS_VDD"),
    ]


def test_map_power_switch_on_an_unknown_switch_raises():
    """A refining command must not silently create the thing it refines."""
    import tempfile

    path = os.path.join(tempfile.mkdtemp(), "t.upf")
    with open(path, "w") as f:
        f.write("set_upf_version 3.1\nmap_power_switch -switch_name_list {NOPE} -lib_cells {C}\n")
    with pytest.raises(Exception, match="no power switch of that name"):
        elaborate(path)


def test_set_level_shifter_cell_on_an_unknown_strategy_raises():
    import tempfile

    path = os.path.join(tempfile.mkdtemp(), "t.upf")
    with open(path, "w") as f:
        f.write("set_upf_version 3.1\nset_level_shifter_cell -level_shifter NOPE -cell_name C\n")
    with pytest.raises(Exception, match="no level shifter strategy of that name"):
        elaborate(path)


def test_load_upf_accepts_the_file_flag_as_well_as_the_positional_form():
    """1801 spells it positionally; this app generates `-file <path>`.
    Both are accepted so neither side has to change a shipped form."""
    import tempfile

    directory = tempfile.mkdtemp()
    with open(os.path.join(directory, "sub.upf"), "w") as f:
        f.write("create_supply_port VDD_SUB_ONLY\n")
    with open(os.path.join(directory, "top.upf"), "w") as f:
        f.write("set_upf_version 3.1\nload_upf -file sub.upf -instance u_sub\n")

    design = elaborate(os.path.join(directory, "top.upf"))
    # -instance acts as -scope's alias: the sub-file's construct is scoped
    # under the named instance.
    assert make_power_intent_id("u_sub", "VDD_SUB_ONLY") in design.supply_ports


def test_a_relative_entry_point_path_is_not_doubled(tmp_path, monkeypatch):
    """Found by the v1.0 clean-clone smoke test.

    `install_relative_source`'s `source` override resolves a relative
    path against `dirname(state.current_file)` -- and for the OUTERMOST
    call that is this same file, so a relative `top_file` was joined to
    its own directory: "a/b/top.upf" became "a/b/a/b/top.upf" and failed
    with a confusing "couldn't read file". An absolute path
    short-circuits the override's `isabs` check, which is exactly why
    this went unnoticed: the GUI's QFileDialog always returns one.
    """
    from backend.api import elaborate_power_intent

    (tmp_path / "upf").mkdir()
    (tmp_path / "upf" / "top.upf").write_text(
        "upf_version 3.1\nset_scope .\ncreate_supply_port VDD\n"
    )
    monkeypatch.chdir(tmp_path)

    design = elaborate_power_intent("upf/top.upf", "upf")  # relative, on purpose

    assert [p.name for p in design.supply_ports.values()] == ["VDD"]


def test_a_relative_cpf_entry_point_path_is_not_doubled(tmp_path, monkeypatch):
    """The CPF elaborator had the identical shape."""
    from backend.api import elaborate_power_intent

    (tmp_path / "cpf").mkdir()
    (tmp_path / "cpf" / "top.cpf").write_text(
        "set_cpf_version 2.0\nset_design top\ncreate_power_nets -nets {VDD}\nend_design\n"
    )
    monkeypatch.chdir(tmp_path)

    design = elaborate_power_intent("cpf/top.cpf", "cpf")

    assert [n.name for n in design.supply_nets.values()] == ["VDD"]
