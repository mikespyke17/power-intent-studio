"""A tracked GOLDEN for what this app generates, byte for byte.

Every other UPF test here asserts a fragment -- `assert "-reuse" in text`.
That catches a construct disappearing, and misses everything about the
file as a whole: section order, scope nesting, blank lines, a flag that
starts being emitted twice, a `set_scope` that is opened and never
closed. The expert review found several defects of exactly that shape,
and a fragment assertion would not have surfaced any of them.

So this file pins the WHOLE output for one scenario -- the one the review
itself worked through (`sram` / `u_sub` / `u_sram`, VDD / VSS / VDD_SUB /
VDD_INT, Power Switch PG) -- against
`golden_upf_output/expert_review_scenario.upf`. The scenario is chosen
because it exercises every Priority 1 and 2 finding at once:

    1.1  upf_version, not set_upf_version
    1.2  create_supply_set -function, not -supply
    1.3  map_power_switch names the switch positionally
    1.4  each nested construct inside its own set_scope
    1.5  a net continued from an outer scope carries -reuse
    1.7  no non-ASCII whitespace anywhere
    2.1  -control_port {CTRL <real RTL signal>}
    2.3  -port_map bridging abstract ports to real cell pins

Built from DIAGRAM STATE rather than by importing a file: the generator
is what is under test here, and feeding it an imported file would make
an importer bug look like a generator bug.

To regenerate after a deliberate change:

    UPDATE_GOLDEN=1 python3 -m pytest tests/frontend/test_generated_upf_golden.py

...then READ THE DIFF before committing it. A golden that is regenerated
reflexively pins whatever the code happens to do, which is worse than
having no golden at all.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from PySide6.QtGui import QColor

from frontend.src.power_intent_view.power_domain_overlay import (
    PowerDomainDiagramState,
    PowerDomainPlacement,
    SupplySetPlacement,
)
from frontend.src.power_intent_view.power_intent_text_export import generate_upf
from frontend.src.power_intent_view.power_switch_overlay import (
    PowerSwitchDiagramState,
    PowerSwitchPlacement,
    power_switch_connector_key,
)
from frontend.src.power_intent_view.retention_overlay import RetentionDiagramState
from frontend.src.power_intent_view.supply_port_overlay import (
    SupplyPortDiagramState,
    SupplyPortPlacement,
)

GOLDEN = Path(__file__).parent / "golden_upf_output" / "expert_review_scenario.upf"

TOP = "sram"
SUB = "sram/u_sub"
MACRO = "sram/u_sram"

_BLUE = QColor("#4285f4")
_GREY = QColor("#262a30")
_RED = QColor("#db4437")


def _build_state():
    """The review's own scenario, as diagram state.

    Mirrors `golden_simple10/design/` (top `sram`, child `u_sub`, macro
    `u_sram` whose .lib declares VDD / VSS / VDD_INT), so the names here
    are the ones a real elaboration of that design produces rather than
    invented ones."""
    supply = SupplyPortDiagramState(
        supply_port_placements={
            # Boundary ports at the top.
            "vdd": SupplyPortPlacement(id="vdd", owner_path=TOP, name="VDD", color=_BLUE, direction="in"),
            "vss": SupplyPortPlacement(id="vss", owner_path=TOP, name="VSS", color=_GREY, direction="in"),
            # The switched rail, internal to u_sub...
            "vdd_sub": SupplyPortPlacement(
                id="vdd_sub", owner_path=SUB, name="VDD_SUB", color=_RED, is_internal_supply=True
            ),
            # ...and the two rails CONTINUED into u_sub from the top. These
            # are what finding 1.5 is about: same name, same physical net,
            # so they must be declared `-reuse` rather than re-declared.
            "vdd_in_sub": SupplyPortPlacement(
                id="vdd_in_sub", owner_path=SUB, name="VDD", color=_BLUE, is_internal_supply=True
            ),
            "vss_in_sub": SupplyPortPlacement(
                id="vss_in_sub", owner_path=SUB, name="VSS", color=_GREY, is_internal_supply=True
            ),
        },
        # The macro's own PG pins, as the .lib declares them.
        pg_pin_placements={
            "pg:VDD": SupplyPortPlacement(id="pg:VDD", owner_path=MACRO, name="VDD", color=_BLUE, fixed=True),
            "pg:VSS": SupplyPortPlacement(id="pg:VSS", owner_path=MACRO, name="VSS", color=_GREY, fixed=True),
            "pg:VDD_INT": SupplyPortPlacement(
                id="pg:VDD_INT", owner_path=MACRO, name="VDD_INT", color=_BLUE, fixed=True, is_internal=True
            ),
        },
    )
    # Macro hookup, and the switch's own Supply In.
    supply.supply_connector_placements.extend([
        ("vdd", "pg:VDD"),
        ("vss", "pg:VSS"),
        (power_switch_connector_key("pg_sw", "vdd_sub"), "vdd_in_sub"),
    ])

    domain = PowerDomainDiagramState(
        supply_sets={
            "ss_top": SupplySetPlacement(
                id="ss_top", name="SS_VDD", power="VDD", ground="VSS", owner_path=TOP
            ),
            "ss_sub": SupplySetPlacement(
                id="ss_sub", name="SS_VDD_SUB", power="VDD_SUB", ground="VSS", owner_path=SUB
            ),
        },
        domains={
            "pd_top": PowerDomainPlacement(
                id="pd_top", owner_path=TOP, name="PD_TOP", color=_BLUE,
                primary_supply_set="ss_top", secondary_supply_sets=["ss_sub"],
            ),
            "pd_sub": PowerDomainPlacement(
                id="pd_sub", owner_path=SUB, name="PD_SUB", color=_RED, primary_supply_set="ss_sub",
            ),
        },
    )

    switch = PowerSwitchDiagramState(
        switches={
            "pg_sw": PowerSwitchPlacement(
                id="pg_sw", name="PG", power_domain_id="pd_sub",
                target_id="vdd_sub", owner_path=SUB,
                target_ids=["vdd_sub"], owner_paths=[SUB],
                # Real library cell pin names -- deliberately NOT called
                # IN/OUT/CTRL, which is the whole point of -port_map.
                input_supply_port="VDDH", output_supply_port="VDDL", control_port="SLEEP",
                # The real RTL signal. `sleep_b` is a genuine top-level
                # port of golden_simple10's own sram.v.
                control_signal="sleep_b",
                cell_type="cell_type_pg",
                on_state="sleep_b", off_state="!sleep_b",
            )
        }
    )
    return supply, domain, switch


def _generate() -> str:
    supply, domain, switch = _build_state()
    return generate_upf(
        supply, domain, RetentionDiagramState(), "3.1", switch, design_name=TOP
    )


# The header banner stamps the time of generation, so it differs on every
# run and cannot be pinned. Blanked on BOTH sides rather than dropped, so
# the line's presence and position are still compared -- losing the
# banner entirely is exactly the kind of change this golden exists to
# catch.
_TIMESTAMP = re.compile(r"^(#  Generated      : ).*$")
# The app's own version, stamped in the header: changes every release and
# says nothing about the power intent, so it is blanked like the
# timestamp (its presence and position still compared).
_GENERATOR = re.compile(r"^(#  Generator      : Power Intent Studio v).*$")


def _comparable(text: str) -> list[str]:
    return [_GENERATOR.sub(r"\1<version>", _TIMESTAMP.sub(r"\1<timestamp>", line)) for line in text.splitlines()]


def test_the_generated_upf_matches_the_golden_byte_for_byte():
    text = _generate()
    if os.environ.get("UPDATE_GOLDEN"):
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(text)
    # Compared line by line: a whole-file mismatch reports as a wall of
    # text, and the line the diff points at is the actionable part.
    assert _comparable(text) == _comparable(GOLDEN.read_text())


def test_the_golden_pins_a_real_generated_timestamp_line():
    """Guards the normalisation above from quietly matching nothing: if
    the banner's wording changes, _comparable() stops blanking anything
    and the byte-for-byte test starts failing on the clock instead of on
    a real difference."""
    assert any(line.startswith("#  Generated      : ") for line in GOLDEN.read_text().splitlines())


# --- one assertion per finding, so a failure NAMES what regressed ------
#
# The byte-for-byte test above catches everything; these say WHICH thing.
# Without them a reader of a red build sees only "the golden changed".


def test_finding_1_1_the_version_is_declared_with_upf_version():
    text = _generate()
    assert "upf_version 3.1" in text
    assert "set_upf_version" not in text


def test_finding_1_2_supply_sets_use_function():
    text = _generate()
    # One `-function` per continued line, so the command is the block.
    create = text[text.index("create_supply_set SS_VDD "):].split("\n\n")[0]
    assert "-function {power VDD}" in create and "-supply" not in create


def test_finding_1_3_map_power_switch_names_the_switch_positionally():
    text = _generate()
    assert "map_power_switch {PG}" in text
    assert "-switch_name_list" not in text


def test_finding_1_4_reversed_every_construct_sits_at_the_root():
    """REVERSES review finding 1.4 (reference golden). PD_SUB,
    SS_VDD_SUB and PG all belong to u_sub, and used to be emitted inside
    `set_scope u_sub`. The reference golden uses the flat form instead: every
    construct at the root, the hierarchy stated by root-relative
    `-elements` paths."""
    lines = _generate().splitlines()
    scope = ""
    seen: dict[str, str] = {}
    for line in lines:
        if line.startswith("set_scope "):
            target = line.split(None, 1)[1]
            scope = "" if target == "." else target
            continue
        for prefix, key in (
            ("create_power_domain PD_SUB", "PD_SUB"),
            ("create_supply_set SS_VDD_SUB", "SS_VDD_SUB"),
            ("create_power_switch PG", "PG"),
            ("map_power_switch {PG}", "map PG"),
            ("create_power_domain PD_TOP", "PD_TOP"),
        ):
            if line.startswith(prefix):
                seen[key] = scope
    assert seen == {"PD_TOP": "", "SS_VDD_SUB": "", "PD_SUB": "", "PG": "", "map PG": ""}
    assert "create_power_domain PD_SUB \\\n    -elements { \\\n        u_sub \\\n    }" in _generate()


def test_finding_1_4_every_set_scope_is_closed():
    """An unclosed `set_scope` silently relocates everything after it."""
    depth = 0
    for line in _generate().splitlines():
        if line == "set_scope .":
            depth = 0
        elif line.startswith("set_scope "):
            assert depth == 0, f"nested set_scope without returning first: {line}"
            depth = 1
    assert depth == 0, "file ends inside a scope"


def test_finding_1_5_reversed_rails_continued_into_u_sub_are_declared_once():
    """REVERSES review finding 1.5 (reference golden): a rail continued
    into u_sub is the same root net, declared once -- no `-reuse`
    replica inside a nested scope."""
    text = _generate()
    assert "-reuse" not in text
    for net in ("VDD", "VSS", "VDD_SUB"):
        assert text.count(f"create_supply_net {net}\n") == 1


def test_finding_1_7_no_non_ascii_whitespace():
    offenders = sorted({hex(ord(c)) for c in _generate() if c.isspace() and c not in (" ", "\n", "\t")})
    assert offenders == []


def test_the_control_port_pairs_the_users_port_name_with_the_real_signal():
    """`sleep_b` is a real port of the design; `SLEEP` is what the user
    typed into Control Port. Both halves are the user's own values --
    there is no fixed token on either side any more."""
    assert "-control_port {SLEEP sleep_b}" in _generate()


def test_on_off_state_are_rewritten_to_the_switchs_own_control_port():
    """Authored as `sleep_b` / `!sleep_b`, emitted against the DECLARED
    control port `SLEEP` -- the user's own field value, not a fixed
    token."""
    text = _generate()
    assert "-on_state {on VDDH {SLEEP}}" in text
    assert "-off_state {off {!SLEEP}}" in text
    assert "sleep_b}}" not in text  # no un-substituted expression survives


def test_map_power_switch_carries_its_domain():
    """Added after the fact: `-domain` was missing entirely, the same gap
    already fixed for map_level_shifter_cell. Verified shape is
    `map_power_switch switch_name -domain domain_name -lib_cells list`,
    so it follows the positional name."""
    assert "map_power_switch {PG} \\\n    -domain PD_SUB \\" in _generate()


def test_no_port_map_is_ever_emitted():
    """RETIRED. It bridged a fixed abstract port name to the user's own
    field value; with the fields BEING the port names, nothing is left to
    bridge, so no switch gets one under any circumstances."""
    text = _generate()
    assert "-port_map" not in text
    # The names it used to carry are declared directly instead.
    assert "-input_supply_port {VDDH VDD}" in text
    assert "-output_supply_port {VDDL VDD_SUB}" in text


def test_the_macro_pg_pins_are_connected_by_hierarchical_reference():
    text = _generate()
    # The name column is padded in this section, so the assertion is on
    # the tokens rather than on one exact spacing.
    macro = text[text.index("# Macro Connections"):]
    assert [line.split() for line in macro.splitlines() if line.startswith("connect_supply_net")] == [
        ["connect_supply_net", "VDD", "-ports", "{u_sram/VDD}"],
        ["connect_supply_net", "VSS", "-ports", "{u_sram/VSS}"],
    ]


def test_instances_are_separated_by_one_blank_line_and_pairs_are_not():
    """The separator convention, as it applies to THIS whole file.

    A named assertion alongside the byte-for-byte comparison, so a red
    build says which property broke rather than only "the golden
    changed" -- the reason every other finding here has one too.
    `tests/frontend/test_generation_blank_line_separation.py` is the
    exhaustive per-section suite; this pins the shape of a real file."""
    text = _generate()
    # A declaration section PACKS: every net at the root, one block.
    assert "create_supply_port VDD -direction in\ncreate_supply_port VSS" in text
    assert "connect_supply_net VDD -ports {VDD}\nconnect_supply_net VSS" in text
    assert "create_supply_net VDD\ncreate_supply_net VSS\ncreate_supply_net VDD_SUB\n" in text
    # A domain and the next one: separated, as the general rule says.
    assert "-supply {primary SS_VDD} \\\n    -available_supplies { SS_VDD_SUB }\n\ncreate_power_domain PD_SUB" in text
    # A switch and its own cell mapping: NOT separated.
    assert "-off_state {off {!SLEEP}}\nmap_power_switch {PG} \\" in text
    # ...and nowhere in the file are there two blank lines in a row.
    lines = text.splitlines()
    assert not any(not lines[i].strip() and not lines[i - 1].strip() for i in range(1, len(lines)))


def test_the_header_carries_the_current_app_version():
    """The golden comparison blanks the Generator version, so it is
    pinned here instead: the header names THIS build's version."""
    from frontend.src.power_intent_view.power_intent_text_export import _TOOL_VERSION

    assert f"#  Generator      : Power Intent Studio v{_TOOL_VERSION}" in _generate()
