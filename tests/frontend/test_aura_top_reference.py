"""The aura_top reference UPF, promoted to a permanent fixture.

`examples/aura_top/upf/aura_top.upf` is real, hand-authored power intent
for a multi-domain SoC. Reading it line by line against this app's
elaborator surfaced TEN real defects in one pass -- the audit's category
(b) -- which is why it lives here now rather than being read once and
forgotten. Every test below pins one of those fixes against the actual
file, so a regression is caught by the thing that found the bug.

WHAT THIS FILE DELIBERATELY DOES NOT TEST
-----------------------------------------
TWO groups of constructs are neutralised before elaboration, each for a
different and deliberate reason. They are listed explicitly rather than
quietly skipped, because "why doesn't this test the whole file" is the
first question a reader will have -- and
`test_capstone_exactly_these_blockers_remain` pins the complete list, so
the answer cannot drift.

  CATEGORY (c) -- genuinely unsupported, correctly rejected. There is no
      data model for these, and inventing one is a product decision, not
      a fix. Stubbed so the walk can continue past them;
      `test_category_c_commands_are_still_unsupported` asserts they are
      still rejected rather than silently accepted.

  THE FILE'S OWN TCL DEFECT -- six `-isolation_signal iso_en_w[2]`
      values are written UNBRACED, which in Tcl is command substitution:
      it tries to run a command called `2`. Every other bit-select in
      the file is correctly braced. Any real UPF reader would fail here
      identically, so this is repaired in the test's own copy rather
      than tolerated in the app.
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

import pytest

from backend.api import elaborate_power_intent

REFERENCE = Path(__file__).resolve().parents[2] / "examples" / "aura_top" / "upf" / "aura_top.upf"

# Commands with no data model at all (audit category (c)).
CATEGORY_C_COMMANDS = (
    "set_design_top",
    "add_state_transition",
    "add_port_state",
    "map_isolation_cell",
    "map_retention_cell",
    "set_related_supply_net",
)
# Flags with no data model, on commands that otherwise work.
CATEGORY_C_FLAGS = ("-retention_condition", "-assert_r_mutex", "-force_shift")
# WAS flagged-uncertain and is now accepted on real-world evidence --
# see test_set_isolation_name_prefix_is_accepted_like_the_level_shifters.
# Kept as an empty tuple rather than deleted so the shape of _prepared()
# still says there is a category for this, should another flag land in it.
UNCERTAIN_FLAGS: tuple[str, ...] = ()


def _repair_unbraced_bit_selects(text: str) -> str:
    """The file's own Tcl defect -- see this module's docstring."""
    return re.sub(r"(-isolation_signal\s+)(\S+)", r"\1{\2}", text)


def _drop_lines_starting_with(text: str, prefixes: tuple[str, ...]) -> str:
    """Remove whole flag lines, carrying the line continuation with them.

    A dropped FINAL line of a continued command leaves the previous
    line's trailing backslash dangling, which turns the next command into
    an argument of this one -- so the backslash goes too."""
    out: list[str] = []
    for line in text.splitlines():
        if line.lstrip().startswith(prefixes):
            if not line.rstrip().endswith("\\") and out and out[-1].rstrip().endswith("\\"):
                out[-1] = out[-1].rstrip()[:-1].rstrip()
            continue
        out.append(line)
    return "\n".join(out)


def _prepared(include_uncertain: bool = False, include_category_c: bool = False) -> str:
    text = _repair_unbraced_bit_selects(REFERENCE.read_text())
    drop = CATEGORY_C_FLAGS if include_uncertain else CATEGORY_C_FLAGS + UNCERTAIN_FLAGS
    text = _drop_lines_starting_with(text, tuple(d for d in drop if d))
    if not include_category_c:
        stubs = "\n".join(f"proc {c} args {{}}" for c in CATEGORY_C_COMMANDS)
        text = text.replace("upf_version 3.1", f"upf_version 3.1\n{stubs}", 1)
    return text


def _elaborate(text: str):
    directory = Path(tempfile.mkdtemp())
    path = directory / "aura_top.upf"
    path.write_text(text)
    return elaborate_power_intent(str(path), "upf")


@pytest.fixture(scope="module")
def design():
    if not REFERENCE.exists():
        pytest.skip(f"reference UPF not present: {REFERENCE}")
    return _elaborate(_prepared())


# --- the whole file ------------------------------------------------------


def test_the_reference_file_elaborates(design):
    """Before the audit's fixes this failed on line 19, the SECOND
    command, and never reached the end of the file."""
    assert len(design.domains) == 6
    assert len(design.supply_ports) == 6
    assert len(design.supply_sets) == 7
    assert len(design.switches) == 3
    assert len(design.isolation_strategies) == 6
    assert len(design.retention_strategies) == 2
    assert len(design.level_shifter_strategies) == 6
    assert len(design.port_attributes) == 5


# --- B1: -reuse ----------------------------------------------------------


def test_b1_reused_nets_are_one_net_each_not_a_duplicate_collision(design):
    """The file declares ten `create_supply_net X -domain PD -reuse`
    continuations of rails already declared at the root. Each one used to
    collide with the original and reject the whole file, because
    `-domain` does not change the scope the duplicate check looks at.

    Nine distinct rails, one record each -- a reuse continues a net, it
    does not create a second one."""
    names = sorted({net.name for net in design.supply_nets.values()})
    assert names == [
        "VDD_AON", "VDD_CPU", "VDD_DSP", "VDD_HI",
        "VDD_MEM", "VDD_PER", "VDD_RET", "VDD_TOP", "VSS",
    ]
    assert len(design.supply_nets) == len(names)


def test_b1_each_reuse_still_records_its_own_domain_association(design):
    """The reuse is not a no-op: VSS is continued into five domains, and
    each of those associations has to survive."""
    vss_domains = sorted(
        c.domain for c in design.domain_supply_connections if c.supply_net == "VSS"
    )
    for domain in ("PD_AON", "PD_CPU", "PD_DSP", "PD_MEM", "PD_PERIPH", "PD_TOP"):
        assert domain in vss_domains, f"{domain} lost its VSS association"


# --- B2 / B3: add_power_state shapes ------------------------------------


def test_b2_b3_supply_set_states_parse_in_the_references_own_shape(design):
    """Two independent fixes meet here. The file writes
    `add_power_state SS_TOP -supply -state HV {-supply_expr {...}}`:
    a BARE `-supply` marker after a positional object (B2), and the state
    NAME outside its own group (B3). Either alone rejected the command."""
    names = sorted({s.name for s in design.power_states.values() if s.supply})
    assert names == ["BOOST", "HV", "LV", "NOM", "OFF", "ON", "ON_HV", "ON_LV", "RET"]


def test_b3_each_state_keeps_its_own_expression(design):
    """The failure mode B3 guards against is subtle: with the split form
    half-handled, every state on a command would share the FIRST state's
    expression. SS_TOP's two states must differ.

    Note what the expressions actually contain. The file writes
    `{FULL_ON $DVDD_NOM}` inside braces, and Tcl does NOT substitute
    variables inside braces -- so the literal `$DVDD_NOM` reaches the IR,
    not `0.80`. That is Tcl behaving correctly, and it is pinned here
    because it looks like a bug at first glance and is not one: the
    expression is a string a consumer parses later."""
    ss_top = {s.name: s.expr for s in design.power_states.values() if s.supply == "SS_TOP"}
    assert ss_top["HV"] != ss_top["LV"]
    assert "$DVDD_NOM" in ss_top["HV"]
    assert "$DVDD_LOW" in ss_top["LV"]


# --- B4 / B5 / B6 / B7 ---------------------------------------------------


def test_b4_b5_switch_mapping_keeps_its_domain_and_its_whole_cell_list(design):
    """`map_power_switch SW_CPU -domain PD_CPU -lib_cells {HDR_X16 HDR_X8}`
    -- `-domain` used to be rejected outright (B4), and only the first
    cell of the list was kept (B5)."""
    cells = sorted(s.cell_type or "" for s in design.switches.values())
    assert cells == ["HDR_X16 HDR_X8", "HDR_X16 HDR_X8", "HDR_X8"]


def test_b5_level_shifter_mapping_keeps_its_whole_cell_list(design):
    """Same fix, reached through `map_level_shifter_cell` (B10's command)."""
    cells = sorted(x.cell_type or "" for x in design.level_shifter_strategies.values())
    assert cells == [
        "ELS_X2", "LSHL_X2 LSLH_X2", "LSHL_X2 LSLH_X2",
        "LSHL_X2 LSLH_X2", "LSHL_X4", "LSLH_X4",
    ]


def test_b6_supply_ports_carry_their_domain(design):
    """Every `create_supply_port X -domain PD_TOP` in the file used to be
    rejected on `-domain`."""
    assert sorted(p.name for p in design.supply_ports.values()) == [
        "VDD_AON", "VDD_HI", "VDD_MEM", "VDD_RET", "VDD_TOP", "VSS",
    ]


def test_b7_include_scope_means_the_same_as_elements_dot(design):
    """`create_power_domain PD_TOP -include_scope` normalises onto the
    `-elements {.}` this app already models and generates."""
    pd_top = next(d for d in design.domains.values() if d.name == "PD_TOP")
    assert pd_top.elements == ["."]


# --- B9: the reinterpretation is now visible -----------------------------


def test_b9_the_non_standard_state_type_is_reported_not_silent(design):
    """`add_power_state SS_MEM ... -state RET {-supply_expr {power == {ON
    $MVDD_RET}}}` uses `ON`, which is not one of this app's four state
    types. It is still read as FULL_ON at that level -- that behaviour is
    deliberately unchanged, since whether `ON` is legal UPF is one of the
    audit's own open questions. What changed is that it now SAYS so.

    Asserted through the importer, which is where the diagnostic is
    raised; the elaborator itself has no opinion on the vocabulary."""
    pytest.importorskip("PySide6")
    from frontend.src.backend_integration.backend_adapter import adapt_power_intent
    from frontend.src.power_intent_view.power_intent_import import import_power_intent
    from frontend.src.shared.mock_backend import build_mock_design

    imported = import_power_intent(adapt_power_intent(design), build_mock_design())
    reinterpreted = [d for d in imported.warnings if "is not one of" in d.message]
    assert reinterpreted, "the ON -> FULL_ON substitution was not reported"
    assert any("'ON'" in d.message and "FULL_ON" in d.message for d in reinterpreted)


# --- what must STILL fail ------------------------------------------------


@pytest.mark.parametrize("command", CATEGORY_C_COMMANDS)
def test_category_c_commands_are_still_unsupported(command):
    """These have no data model, so they must keep failing -- and fail
    NAMING themselves, not with a generic parse error a reader cannot act
    on. Tcl's own "invalid command name" does exactly that."""
    if not REFERENCE.exists():
        pytest.skip("reference UPF not present")
    text = _prepared(include_category_c=True)
    with pytest.raises(Exception) as excinfo:
        _elaborate(text)
    message = str(excinfo.value)
    assert command in message or "invalid command name" in message


def test_set_isolation_name_prefix_is_accepted_like_the_level_shifters(design):
    """RESOLVED, and on real-world evidence rather than a signature.

    This flag was the audit's headline uncertainty: no published option
    list for `set_isolation` carries it (five were checked), while
    `set_level_shifter`'s is confirmed. This file uses it SIX times on
    `set_isolation`, and rejecting a flag a designer actually writes is
    worse than accepting one whose LRM status is unconfirmed --
    accepting on READ asserts nothing into any generated file.

    The deeper question stays open on its own terms, and the generator
    still does not emit it here: reading tolerantly and writing
    conservatively are separate decisions."""
    prefixes = sorted(x.name_prefix or "" for x in design.isolation_strategies.values())
    assert prefixes == [
        "iso_cpu_", "iso_cpu_rdy_", "iso_dsp_", "iso_mem_", "iso_per_", "iso_per_hi_",
    ]
    # Parity with the case it was aligned to.
    assert all(x.name_prefix for x in design.level_shifter_strategies.values())


def test_capstone_exactly_these_blockers_remain():
    """THE capstone for the whole audit: what still stops the UNMODIFIED
    file, enumerated exactly.

    Every blocker is walked in order and classified. The assertion is on
    the COMPLETE list, so this fails both if something new starts
    blocking the file AND if something on the list quietly stops -- which
    is what makes it a record of the remaining scope rather than a
    smoke test.

    Nine of the ten are category (c): constructs with no data model,
    whose implementation is a product decision. The tenth is a defect in
    the FILE itself."""
    if not REFERENCE.exists():
        pytest.skip("reference UPF not present")

    text = REFERENCE.read_text()
    blockers: list[tuple[str, str]] = []
    stubs: list[str] = []
    dropped: list[str] = []
    for _ in range(30):
        candidate = text
        if stubs:
            procs = "\n".join(f"proc {c} args {{}}" for c in stubs)
            candidate = candidate.replace("upf_version 3.1", f"upf_version 3.1\n{procs}", 1)
        try:
            _elaborate(candidate)
            break
        except Exception as exc:  # noqa: BLE001 -- the failure IS the measurement
            message = " ".join(str(exc).split())
            unknown = re.search(r'invalid command name "([^"]+)"', message)
            if unknown and unknown.group(1) == "2":
                blockers.append(("file-tcl-defect", "unbraced -isolation_signal bit-select"))
                text = _repair_unbraced_bit_selects(text)
                continue
            if unknown:
                blockers.append(("category-c-command", unknown.group(1)))
                stubs.append(unknown.group(1))
                continue
            flag = re.search(r"unrecognized option '(-[a-z_0-9]+)'", message)
            if flag:
                blockers.append(("category-c-flag", flag.group(1)))
                dropped.append(flag.group(1))
                text = _drop_lines_starting_with(text, tuple(dropped))
                continue
            raise AssertionError(f"unclassified blocker: {message}") from exc
    else:
        raise AssertionError("the walk did not terminate")

    assert blockers == [
        ("category-c-command", "set_design_top"),
        ("category-c-command", "add_state_transition"),
        ("category-c-command", "add_port_state"),
        ("file-tcl-defect", "unbraced -isolation_signal bit-select"),
        ("category-c-command", "map_isolation_cell"),
        ("category-c-flag", "-retention_condition"),
        ("category-c-flag", "-assert_r_mutex"),
        ("category-c-command", "map_retention_cell"),
        ("category-c-flag", "-force_shift"),
        ("category-c-command", "set_related_supply_net"),
    ]
    # Every category-(b) fix is implied by the absence of its own flag
    # from that list -- and `-name_prefix`, which blocked the walk before
    # this change, is notably gone from it.
    assert not any("name_prefix" in name for _, name in blockers)


def test_the_files_own_unbraced_bit_selects_are_a_tcl_error():
    """Not an app defect, and pinned so it is not "fixed" in the app by a
    future reader. `-isolation_signal iso_en_w[2]` unbraced is Tcl
    command substitution: it runs a command called `2`. Every real UPF
    reader fails here the same way."""
    if not REFERENCE.exists():
        pytest.skip("reference UPF not present")
    raw = REFERENCE.read_text()
    assert "-isolation_signal  iso_en_w[2]" in raw  # still unbraced in the file
    text = _drop_lines_starting_with(raw, CATEGORY_C_FLAGS + UNCERTAIN_FLAGS)
    stubs = "\n".join(f"proc {c} args {{}}" for c in CATEGORY_C_COMMANDS)
    text = text.replace("upf_version 3.1", f"upf_version 3.1\n{stubs}", 1)
    with pytest.raises(Exception) as excinfo:
        _elaborate(text)
    assert 'invalid command name "2"' in str(excinfo.value)
