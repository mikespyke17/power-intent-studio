"""The generator against the hand-fixed reference UPF.

The single_clock_design project (`main_top.pis`) was exported by this
app and its UPF hand-fixed into the reference form
(`main_top.fixed.upf`). Regenerating from the same project must
reproduce that file exactly (apart from the generated-at timestamp and
the app version in the header).

The fixes it pinned, each with a named test below so a red build says
WHICH one regressed:

  - FLAT, ROOT-SCOPED: no `set_scope <hierarchy>` blocks and no `-reuse`
    replicas -- every construct at the root, a nested domain stated by a
    root-relative `-elements` path (reverses review findings 1.4/1.5).
  - every reference bare (`VDD_COUNTER`, not `u_.../VDD_COUNTER`).
  - `create_supply_set` one `-function` per continued line.
  - `set_retention -retention_supply_set` (no `-supply {primary ...}`),
    root-relative elements, a blank line before `set_retention_control`,
    and `{signal high|low}` save/restore pairs.
  - isolation elements root-relative.
  - a multi-bit element (every bit selected) written `<path>[*]`.
  - `add_pst_state` names prefixed `PST_`.

Self-contained: the .pis carries the elaborated RTL, so no RTL
elaboration is needed. Note the one difference between the app's
original export and the golden that was NOT a generator fix:
`-isolation_signal iso_en` -> `iso_n` was a data correction, already
saved in the vendored .pis.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from frontend.src.app.project_file import deserialize_project
from frontend.src.power_intent_view.power_intent_import import _retention_logic
from frontend.src.power_intent_view.power_intent_text_export import (
    PST_STATE_PREFIX,
    _pst_state_name,
    _retention_signal,
    _upf_element,
    generate_upf,
)

FIXTURE = Path(__file__).parent / "golden_upf_output" / "single_clock_design"
_TIMESTAMP = re.compile(r"^(#  Generated      : ).*$")
# The app's own version, stamped in the header: changes every release and
# says nothing about the power intent, so it is blanked like the
# timestamp (its presence and position still compared).
_GENERATOR = re.compile(r"^(#  Generator      : Power Intent Studio v).*$")


@pytest.fixture(scope="module")
def generated() -> str:
    """Exactly what File > Extract Power Intent > UPF 3.1 (Legacy power
    states) writes for this project -- the same arguments
    MainWindow._on_extract_power_intent() passes."""
    data = deserialize_project((FIXTURE / "main_top.pis").read_bytes())
    rtl = data.rtl_design
    return generate_upf(
        data.supply_state,
        data.domain_state,
        data.retention_state,
        "3.1",
        data.power_switch_state,
        data.level_shifter_state,
        rtl.definitions[rtl.top].base_name,
        output_dir=str(FIXTURE),
        legacy_power_states=True,
        isolation_state=data.isolation_state,
        port_attribute_state=data.port_attribute_state,
        top_level_ports=rtl.definitions[rtl.top].ports,
        power_state_table_state=data.power_state_table_state,
        macro_power_intent_state=data.macro_power_intent_state,
        rtl_design=rtl,
    )


def _comparable(text: str) -> list[str]:
    return [_GENERATOR.sub(r"\1<version>", _TIMESTAMP.sub(r"\1<timestamp>", line)) for line in text.splitlines()]


def test_the_generated_upf_matches_the_reference_golden(generated):
    assert _comparable(generated) == _comparable((FIXTURE / "main_top.fixed.upf").read_text())


def test_no_construct_is_wrapped_in_a_nested_scope(generated):
    assert [line for line in generated.splitlines() if line.startswith("set_scope")] == ["set_scope ."]
    assert "-reuse" not in generated


def test_every_net_is_declared_once_at_the_root(generated):
    nets = [line for line in generated.splitlines() if line.startswith("create_supply_net")]
    assert nets == ["create_supply_net VDD", "create_supply_net VSS", "create_supply_net VDD_COUNTER"]


def test_a_nested_domain_states_its_hierarchy_through_elements(generated):
    assert (
        "create_power_domain PD_COUNTER \\\n"
        "    -elements { \\\n"
        "        u_data_subsystem/u_counter \\\n"
        "    } \\\n"
        "    -supply {primary S_VDD_COUNTER} \\\n"
        "    -available_supplies { SS_VDD }"
    ) in generated


def test_references_to_nested_supplies_are_bare(generated):
    assert "u_counter/VDD_COUNTER" not in generated
    assert "add_supply_state VDD_COUNTER \\" in generated
    assert "create_pst PST -supplies {VDD VDD_COUNTER VSS}" in generated


def test_retention_uses_retention_supply_set_and_signal_sense_pairs(generated):
    assert (
        "set_retention retention_counter \\\n"
        "    -domain PD_COUNTER \\\n"
        "    -retention_supply_set SS_VDD \\\n"
        "    -elements { \\\n"
        "        u_data_subsystem/u_counter/count[*] \\\n"
        "    }\n"
        "\n"
        "set_retention_control retention_counter \\\n"
        "    -domain PD_COUNTER \\\n"
        "    -save_signal {enable high} \\\n"
        "    -restore_signal {enable low}"
    ) in generated
    assert "-supply {primary" not in generated.split("# Retention Strategies")[1].split("# Power Switches")[0]


def test_isolation_elements_are_root_relative(generated):
    assert "  -elements { \\\n        u_data_subsystem/u_counter/count[*] \\\n    } \\\n  -location parent" in generated


def test_a_multi_bit_element_with_every_bit_selected_is_written_with_star(generated):
    """`count` is 8 bits (CNT_W), and an element key always names the
    WHOLE signal -- so it is written `count[*]`, in both strategies."""
    assert generated.count("u_data_subsystem/u_counter/count[*]") == 2
    assert "u_data_subsystem/u_counter/count \\" not in generated


@pytest.mark.parametrize(
    "element, widths, upf",
    [
        ("top/u_a/q", {"top/u_a/q": 32}, "u_a/q[*]"),    # multi-bit: all bits
        ("top/u_a/en", {"top/u_a/en": 1}, "u_a/en"),     # scalar: no suffix
        ("top/u_a/x", {}, "u_a/x"),                      # width unknown: no suffix
        ("top/u_a/q[3]", {"top/u_a/q[3]": 32}, "u_a/q[3]"),  # own bit-select kept
    ],
)
def test_upf_element_suffix_rule(element, widths, upf):
    assert _upf_element(element, widths) == upf


def test_pst_state_names_carry_the_pst_prefix(generated):
    rows = [line.split()[1] for line in generated.splitlines() if line.startswith("add_pst_state")]
    assert rows == ["PST_FULL_ON", "PST_FULL_RET", "PST_FULL_OFF"]


@pytest.mark.parametrize(
    "logic, upf",
    [
        ("enable", "enable high"),
        ("!enable", "enable low"),
        ("~ret_n", "ret_n low"),
        ("(!enable)", "enable low"),
        ("u_ctrl/save[0]", "u_ctrl/save[0] high"),
        ("enable posedge", "enable posedge"),   # already a {signal sense} pair
        ("a && b", "a && b"),                   # not expressible: verbatim, not guessed
    ],
)
def test_retention_signal_translation(logic, upf):
    assert _retention_signal(logic) == upf


@pytest.mark.parametrize(
    "upf, logic",
    [("enable high", "enable"), ("enable low", "!enable"), ("enable posedge", "enable posedge"), (None, None)],
)
def test_retention_signal_import_reverses_it(upf, logic):
    assert _retention_logic(upf) == logic


def test_a_pst_name_is_prefixed_exactly_once():
    assert _pst_state_name("FULL_ON") == f"{PST_STATE_PREFIX}FULL_ON"
    assert _pst_state_name(f"{PST_STATE_PREFIX}FULL_ON") == f"{PST_STATE_PREFIX}FULL_ON"
