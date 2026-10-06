"""One blank line between distinct instances, in every section of both
formats.

THE RULE (explicit request). A generated file gets dense fast once a
design has several domains, switches and rules, so each distinct
INSTANCE's block is separated from the next by exactly one blank line --
not zero, not two. The separator marks a boundary BETWEEN instances and
never appears inside one: not between the continuation lines of a single
multi-line command, and not between a construct and its own directly
paired second command (`create_power_switch`/`map_power_switch`,
`set_level_shifter`/`map_level_shifter_cell`, `set_retention`/
`set_retention_control`, CPF's `create_*_rule`/`update_*_rules` and
`create_power_domain`/`update_power_domain`).

WHY A WHOLE FILE, AND WHY IT ASSERTS BY COUNTING. Every section grew its
own separator logic independently, which is exactly how they drifted --
some separated instances, some separated nothing, and two put the blank
INSIDE an instance. Counting blocks rather than matching a literal
snippet is what catches the two failure modes a snippet misses: a
MISSING separator (two instances read as one block) and a DOUBLED one.

The design below deliberately carries 2+ of every construct, across two
different nested hierarchies, so every section under test genuinely has
something to separate.
"""

from __future__ import annotations

import pytest
from PySide6.QtGui import QColor

from frontend.src.power_intent_view.isolation_overlay import IsolationDiagramState, IsolationStrategyPlacement
from frontend.src.power_intent_view.level_shifter_overlay import (
    LevelShifterDiagramState,
    LevelShifterStrategyPlacement,
)
from frontend.src.power_intent_view.macro_power_intent_overlay import MacroPowerIntentDiagramState
from frontend.src.power_intent_view.power_domain_overlay import (
    PowerDomainDiagramState,
    PowerDomainPlacement,
    SupplySetPlacement,
)
from frontend.src.power_intent_view.power_intent_text_export import generate_cpf, generate_upf
from frontend.src.power_intent_view.power_state_table import PowerStateTableDiagramState, PowerStateTableRow
from frontend.src.power_intent_view.power_switch_overlay import (
    PowerSwitchDiagramState,
    PowerSwitchPlacement,
    power_switch_connector_key,
)
from frontend.src.power_intent_view.retention_overlay import RetentionDiagramState, RetentionStrategyPlacement
from frontend.src.power_intent_view.supply_port_overlay import (
    SupplyPortDiagramState,
    SupplyPortPlacement,
    SupplyPortState,
)

TOP, A, B = "top", "top/u_a", "top/u_b"
MACRO = "top/u_m1"
_C = QColor("#ff0000")


# --- the kitchen-sink design ------------------------------------------------


def _state():
    """2+ of every construct this generator can emit."""
    def st(name, kind, voltage=None):
        return SupplyPortState(kind, voltage, name=name)

    supply = SupplyPortDiagramState()
    supply.supply_port_placements.update({
        "vdd": SupplyPortPlacement(id="vdd", owner_path=TOP, name="VDD", color=_C, direction="in",
                                   states=[st("ON", "FULL_ON", "1.2"), st("OFF", "OFF")]),
        "vss": SupplyPortPlacement(id="vss", owner_path=TOP, name="VSS", color=_C, direction="in",
                                   states=[st("ON", "FULL_ON", "0.0")]),
        "vdd_a": SupplyPortPlacement(id="vdd_a", owner_path=A, name="VDD_A", color=_C, is_internal_supply=True),
        "vdd_b": SupplyPortPlacement(id="vdd_b", owner_path=B, name="VDD_B", color=_C, is_internal_supply=True),
    })
    supply.pg_pin_placements.update({
        "pg1": SupplyPortPlacement(id="pg1", owner_path=MACRO, name="VDD", color=_C, fixed=True),
        "pg2": SupplyPortPlacement(id="pg2", owner_path=MACRO, name="VSS", color=_C, fixed=True),
    })
    supply.supply_connector_placements += [
        ("vdd", "vdd_a"), ("vdd", "vdd_b"), ("vdd", "pg1"), ("vss", "pg2"),
        (power_switch_connector_key("sw_a", "vdd_a"), "vdd"),
        (power_switch_connector_key("sw_b", "vdd_b"), "vdd"),
    ]

    domain = PowerDomainDiagramState()
    domain.supply_sets.update({
        "ss_top": SupplySetPlacement(id="ss_top", name="SS_TOP", power="VDD", ground="VSS", owner_path=TOP),
        "ss_a": SupplySetPlacement(id="ss_a", name="SS_A", power="VDD_A", ground="VSS", owner_path=A),
        "ss_b": SupplySetPlacement(id="ss_b", name="SS_B", power="VDD_B", ground="VSS", owner_path=B),
    })
    domain.domains.update({
        "pd_top": PowerDomainPlacement(id="pd_top", owner_path=TOP, name="PD_TOP", color=_C, primary_supply_set="ss_top"),
        "pd_a": PowerDomainPlacement(id="pd_a", owner_path=A, name="PD_A", color=_C, primary_supply_set="ss_a",
                                     secondary_supply_sets=["ss_top"]),
        "pd_b": PowerDomainPlacement(id="pd_b", owner_path=B, name="PD_B", color=_C, primary_supply_set="ss_b"),
    })

    switch = PowerSwitchDiagramState(switches={
        "sw_a": PowerSwitchPlacement(id="sw_a", name="SW_A", power_domain_id="pd_a", target_id="vdd_a",
                                     owner_path=A, target_ids=["vdd_a"], owner_paths=[A],
                                     input_supply_port="VIN", output_supply_port="VOUT", control_port="SLEEP",
                                     control_signal="sleep_a", cell_type="HDR", ack_port="ACKO",
                                     ack_signal="ack_a", on_state="sleep_a", off_state="!sleep_a"),
        "sw_b": PowerSwitchPlacement(id="sw_b", name="SW_B", power_domain_id="pd_b", target_id="vdd_b",
                                     owner_path=B, target_ids=["vdd_b"], owner_paths=[B],
                                     input_supply_port="VIN", output_supply_port="VOUT", control_port="SLEEP",
                                     control_signal="sleep_b", cell_type="HDR",
                                     on_state="sleep_b", off_state="!sleep_b"),
    })
    retention = RetentionDiagramState(strategies={
        "r1": RetentionStrategyPlacement(id="r1", name="RET_A", power_domain_id="pd_a",
                                         primary_supply_set_id="ss_a", retention_supply_set_id="ss_top",
                                         save_logic="save_a", restore_logic="restore_a",
                                         elements=[f"{A}/q_reg"], owner_path=A),
        "r2": RetentionStrategyPlacement(id="r2", name="RET_B", power_domain_id="pd_b",
                                         primary_supply_set_id="ss_b", retention_supply_set_id="ss_top",
                                         save_logic="save_b", restore_logic="restore_b",
                                         elements=[f"{B}/q_reg"], owner_path=B),
    })
    isolation = IsolationDiagramState(strategies={
        "i1": IsolationStrategyPlacement(id="i1", name="ISO_A", power_domain_id="pd_a",
                                         isolation_supply_set_id="ss_top", clamp_value="low",
                                         isolation_control="iso_a", isolation_sense="high",
                                         location="parent", elements=[f"{A}/data_out"], owner_path=A),
        "i2": IsolationStrategyPlacement(id="i2", name="ISO_B", power_domain_id="pd_b",
                                         isolation_supply_set_id="ss_top", clamp_value="high",
                                         isolation_control="iso_b", isolation_sense="low",
                                         location="parent", elements=[f"{B}/data_out"], owner_path=B),
    })
    level_shifter = LevelShifterDiagramState(strategies={
        "l1": LevelShifterStrategyPlacement(id="l1", name="LS_A", power_domain_id="pd_a", applies_to="outputs",
                                            rule="low_to_high", location="parent", threshold="0.1",
                                            cell_type="LS_HL", elements=[f"{A}/data_out"], owner_path=A),
        "l2": LevelShifterStrategyPlacement(id="l2", name="LS_B", power_domain_id="pd_b", applies_to="inputs",
                                            rule="high_to_low", location="self", threshold="0.2",
                                            cell_type="LS_LH", elements=[f"{B}/data_in"], owner_path=B),
    })
    table = PowerStateTableDiagramState(rows={
        "t1": PowerStateTableRow(id="t1", power_domain_id="pd_top", state_name="ALL_ON",
                                 net_states={"vdd": "FULL_ON 1.2", "vss": "FULL_ON 0.0",
                                             "vdd_a": "FULL_ON 1.2", "vdd_b": "FULL_ON 1.2"}),
        "t2": PowerStateTableRow(id="t2", power_domain_id="pd_top", state_name="A_OFF",
                                 net_states={"vdd": "FULL_ON 1.2", "vss": "FULL_ON 0.0",
                                             "vdd_a": "OFF", "vdd_b": "FULL_ON 1.2"}),
        "t3": PowerStateTableRow(id="t3", power_domain_id="pd_top", state_name="ALL_OFF",
                                 net_states={"vdd": "OFF", "vss": "FULL_ON 0.0",
                                             "vdd_a": "OFF", "vdd_b": "OFF"}),
    })
    macro = MacroPowerIntentDiagramState(attachments={MACRO: "/tmp/m1.upf", "top/u_m2": "/tmp/m2.cpf"})
    return dict(supply=supply, domain=domain, switch=switch, retention=retention,
                isolation=isolation, level_shifter=level_shifter, table=table, macro=macro)


def _upf(legacy=False):
    s = _state()
    return generate_upf(s["supply"], s["domain"], s["retention"], "3.1", s["switch"], s["level_shifter"],
                        design_name=TOP, isolation_state=s["isolation"], power_state_table_state=s["table"],
                        macro_power_intent_state=s["macro"], legacy_power_states=legacy)


def _cpf():
    s = _state()
    return generate_cpf(s["supply"], s["domain"], s["retention"], "2.0", s["switch"], s["level_shifter"],
                        design_name=TOP, isolation_state=s["isolation"], power_state_table_state=s["table"],
                        macro_power_intent_state=s["macro"])


# --- reading a section back -------------------------------------------------


def _section(text: str, title: str) -> list[str]:
    """The body of one section: everything after its header rule, up to
    the next section's own banner."""
    lines = text.splitlines()
    start = lines.index(f"# {title}") + 2  # skip the title and its closing rule
    end = next((i for i in range(start, len(lines)) if lines[i].startswith("####")), len(lines))
    body = lines[start:end]
    while body and not body[-1].strip():
        body.pop()
    return body


def _blocks(body: list[str]) -> list[list[str]]:
    """The body split into instance blocks on blank lines.

    Asserts the invariants a plain split would hide: no leading blank, no
    doubled blank. Both are real failure modes -- a doubled blank reads
    as a section break, and a leading one detaches the first instance
    from its own header."""
    assert not body or body[0].strip(), "section body starts with a blank line"
    blocks: list[list[str]] = []
    current: list[str] = []
    previous_blank = False
    for line in body:
        if not line.strip():
            assert not previous_blank, "two blank lines in a row -- one separator, never two"
            previous_blank = True
            if current:
                blocks.append(current)
                current = []
            continue
        previous_blank = False
        current.append(line)
    if current:
        blocks.append(current)
    return blocks


# --- UPF --------------------------------------------------------------------


@pytest.mark.parametrize(
    "title, count, opener",
    [
        ("Supply Sets", 3, "create_supply_set"),
        ("Power Domains", 3, "create_power_domain"),
        # Two strategies, each `set_retention` + `set_retention_control`
        # with a blank line between them (the reference golden's
        # shape) -- so four blocks.
        ("Retention Strategies", 4, "set_retention"),
        ("Power Switches", 2, "create_power_switch"),
        ("Isolation Rules", 2, "set_isolation"),
        ("Level Shifter Rules", 2, "set_level_shifter"),
        ("Supply Set Power States", 3, "add_power_state"),
        ("Load Upf", 2, "load_upf"),
    ],
)
def test_upf_sections_separate_each_instance_with_exactly_one_blank_line(title, count, opener):
    blocks = _blocks(_section(_upf(), title))
    assert len(blocks) == count, (
        f"{title}: expected {count} instance blocks, got {len(blocks)}\n"
        + "\n---\n".join("\n".join(b) for b in blocks)
    )
    for block in blocks:
        assert block[0].startswith(opener), f"{title}: block opens with {block[0]!r}"


def test_upf_port_attributes_separate_each_command():
    """Its own test rather than a parametrized case: this section is the
    only one that needs the elaborated top-level PORT list, which the
    kitchen-sink state above deliberately does not carry."""
    from frontend.src.power_intent_view.port_attribute_overlay import PortAttributeDiagramState
    from frontend.src.shared.models.rtl import Port, PortDirection

    s = _state()
    s["domain"].supply_sets["ss_b2"] = SupplySetPlacement(
        id="ss_b2", name="SS_ALT", power="VDD", ground="VSS", owner_path=TOP)
    ports = [
        Port(name="i1", direction=PortDirection.IN, width=1),
        Port(name="o1", direction=PortDirection.OUT, width=1),
    ]
    text = generate_upf(
        s["supply"], s["domain"], s["retention"], "3.1", design_name=TOP,
        port_attribute_state=PortAttributeDiagramState(assignments={"i1": "ss_top", "o1": "ss_b2"}),
        top_level_ports=ports,
    )
    blocks = _blocks(_section(text, "Port Attributes"))
    assert len(blocks) == 2
    for block in blocks:
        assert block[0].startswith("set_port_attributes")


# --- the REFINED rule: declaration sections pack, and mark SCOPE instead ---
#
# A later refinement of the rule above, applying to these sections ONLY:
# "# Supply Ports", "# Supply Nets", "# Connect external ports to internal
# nets" and "# Macro Connections" pack their declarations together.
# A blank line there used to mark a SCOPE CHANGE; every declaration is at
# the root scope now (reference golden), so there is none left to mark
# and all four are fully packed. Everything else keeps one blank line per
# instance -- the tests above and below are what hold that line.

_PACKED_UPF_SECTIONS = [
    "Supply Ports",
    "Supply Nets",
    "Connect external ports to internal nets",
    "Macro Connections",
]


@pytest.mark.parametrize("title", _PACKED_UPF_SECTIONS)
def test_upf_single_scope_declaration_sections_have_no_blank_lines_at_all(title):
    """All four are declared at one (the root) scope, so there is no
    transition to mark and the whole section is one packed block."""
    body = _section(_upf(), title)
    assert len(body) >= 2, f"{title}: needs 2+ declarations to be meaningful"
    assert all(line.strip() for line in body), f"{title} is not packed:\n" + "\n".join(body)


def test_upf_supply_nets_are_one_packed_root_block():
    """Asserted line for line: every net declared once, at the root --
    the root hierarchy's nets first, then each nested hierarchy's,
    shallowest first -- with no `set_scope` and no `-reuse` replica of a
    root net inside a nested hierarchy."""
    body = _section(_upf(), "Supply Nets")
    assert body == [
        "create_supply_net VDD",
        "create_supply_net VSS",
        "create_supply_net VDD_A",
        "create_supply_net VDD_B",
    ], body


def test_upf_two_nets_in_the_same_nested_scope_are_packed_together():
    """Two nets of the SAME nested hierarchy are declared together,
    alphabetically, in that hierarchy's slot of the root block."""
    s = _state()
    # A second Internal Supply on the SAME hierarchy as VDD_A.
    from PySide6.QtGui import QColor

    from frontend.src.power_intent_view.supply_port_overlay import SupplyPortPlacement

    s["supply"].supply_port_placements["vdd_a2"] = SupplyPortPlacement(
        id="vdd_a2", owner_path=A, name="VDD_A2", color=QColor("#ff0000"), is_internal_supply=True)
    text = generate_upf(s["supply"], s["domain"], s["retention"], "3.1", s["switch"],
                        s["level_shifter"], design_name=TOP, isolation_state=s["isolation"],
                        power_state_table_state=s["table"], macro_power_intent_state=s["macro"])
    body = _section(text, "Supply Nets")
    start = body.index("create_supply_net VDD_A")
    assert body[start:start + 2] == ["create_supply_net VDD_A", "create_supply_net VDD_A2"], body
    assert "set_scope u_a" not in body


@pytest.mark.parametrize(
    "title",
    ["Power Supplies (power)", "Internal Supplies (power)", "Power Supplies (ground)",
     "Macro Connections"],
)
def test_cpf_net_declaration_sections_are_packed_too(title):
    """The CPF counterparts, checked rather than assumed to follow: CPF
    emits its net declarations at one scope, so these sections have no
    transition to mark and pack completely."""
    body = _section(_cpf(), title)
    assert all(line.strip() for line in body), f"{title} is not packed:\n" + "\n".join(body)


def test_the_refinement_did_not_leak_into_the_other_sections():
    """The scoped-refinement guarantee, stated as one assertion: every
    section NOT on the packed list still separates its instances. Named
    explicitly because the risk in a change like this is applying it too
    widely, which no individual section's own test would catch."""
    text = _upf()
    for title in ["Supply Sets", "Power Domains", "Retention Strategies", "Power Switches",
                  "Isolation Rules", "Level Shifter Rules", "Supply Set Power States", "Load Upf"]:
        body = _section(text, title)
        assert "" in body, f"{title} lost its instance separators"


def test_upf_legacy_port_state_separates_each_nets_own_block():
    """The legacy per-net declarations used to run straight together."""
    blocks = _blocks(_section(_upf(legacy=True), "Port State"))
    assert len(blocks) == 4
    # Bare names: every net is declared at the root.
    assert [b[0].split()[1] for b in blocks] == ["VDD", "VDD_A", "VDD_B", "VSS"]


# --- the inverse: no blank INSIDE an instance -------------------------------


def test_upf_a_switch_and_its_own_map_power_switch_are_one_block():
    """The paired command must stay attached. This is a REVERSAL of an
    earlier explicit request that separated them -- a break in the
    middle of one switch read as a section break."""
    for block in _blocks(_section(_upf(), "Power Switches")):
        joined = "\n".join(block)
        assert "create_power_switch" in joined and "map_power_switch" in joined
        # ...and exactly one of each, so the pair is one instance, not two merged.
        assert joined.count("create_power_switch ") == 1
        assert joined.count("map_power_switch ") == 1


def test_upf_a_level_shifter_and_its_own_cell_mapping_are_one_block():
    for block in _blocks(_section(_upf(), "Level Shifter Rules")):
        joined = "\n".join(block)
        assert "set_level_shifter " in joined and "map_level_shifter_cell " in joined


def test_upf_a_retention_strategy_is_followed_by_its_own_control_as_the_next_block():
    """REVERSES the earlier "one block" rule: the reference golden
    puts one blank line between `set_retention` and its own
    `set_retention_control`. They still come strictly in pairs."""
    blocks = _blocks(_section(_upf(), "Retention Strategies"))
    assert len(blocks) % 2 == 0
    for strategy, control in zip(blocks[::2], blocks[1::2]):
        name = strategy[0].split()[1]
        assert strategy[0] == f"set_retention {name} \\"
        assert control[0] == f"set_retention_control {name} \\"


def test_upf_a_multi_line_command_is_never_broken_by_a_blank_line():
    """A continuation line ends in a backslash, so the line after one can
    never be blank -- that would sever the command itself."""
    lines = _upf().splitlines()
    for i, line in enumerate(lines[:-1]):
        if line.rstrip().endswith("\\"):
            assert lines[i + 1].strip(), f"line {i + 1} continues into a blank line: {line!r}"


def test_upf_never_switches_into_a_nested_scope():
    """Every construct is declared at the root (reference golden): the
    file's only `set_scope` is the opening `set_scope .`, even though
    this design spreads its constructs over two nested hierarchies."""
    lines = _upf().splitlines()
    assert [line for line in lines if line.startswith("set_scope")] == ["set_scope ."]


# --- CPF --------------------------------------------------------------------


@pytest.mark.parametrize(
    "title, count, opener",
    [
        ("Power Domains", 3, "create_power_domain"),
        ("Retention Strategies", 2, "create_state_retention_rule"),
        ("Power Switches", 2, "create_power_switch_rule"),
        ("Isolation Rules", 2, "create_isolation_rule"),
        ("Level Shifter Rules", 2, "create_level_shifter_rule"),
        ("Nominal Conditions", 2, "create_nominal_condition"),
        ("Power Modes", 3, "create_power_mode"),
    ],
)
def test_cpf_sections_separate_each_instance_with_exactly_one_blank_line(title, count, opener):
    body = _section(_cpf(), title)
    if title == "Power Modes":
        # The section's own closing note explains CPF's limits; it belongs
        # to the section, not to the last mode.
        body = [l for l in body if not l.startswith("#   A CPF") and not l.startswith("#   DOMAIN")
                and not l.startswith("#   supply set's") and not l.startswith("#   ground/")
                and not l.startswith("#   expressible")]
        while body and not body[-1].strip():
            body.pop()
    # The Power Switches section no longer opens with `set_instance` or
    # closes with `set_instance .` -- CPF scopes via set_design/end_design
    # (audit findings M1/C2).
    blocks = _blocks(body)
    assert len(blocks) == count, (
        f"{title}: expected {count} instance blocks, got {len(blocks)}\n"
        + "\n---\n".join("\n".join(b) for b in blocks)
    )
    for block in blocks:
        assert block[0].startswith(opener), f"{title}: block opens with {block[0]!r}"


def test_cpf_a_domain_and_its_own_update_are_one_block():
    for block in _blocks(_section(_cpf(), "Power Domains")):
        joined = "\n".join(block)
        assert "create_power_domain " in joined and "update_power_domain " in joined


def test_cpf_a_rule_and_its_own_update_are_one_block():
    for title, create, update in [
        ("Isolation Rules", "create_isolation_rule ", "update_isolation_rules "),
        ("Level Shifter Rules", "create_level_shifter_rule ", "update_level_shifter_rules "),
    ]:
        for block in _blocks(_section(_cpf(), title)):
            joined = "\n".join(block)
            assert create in joined and update in joined, f"{title}: {joined}"


def test_cpf_a_switch_keeps_its_rule_updates_and_summary_together():
    body = _section(_cpf(), "Power Switches")
    blocks = _blocks(body)
    for block in blocks:
        joined = "\n".join(block)
        assert block[0].startswith("create_power_switch_rule ")
        assert "create_power_switch_rule " in joined
        assert "update_power_switch_rule " in joined
        assert joined.splitlines()[-1].startswith("#   ")  # its own summary comment


def test_cpf_a_multi_line_command_is_never_broken_by_a_blank_line():
    lines = _cpf().splitlines()
    for i, line in enumerate(lines[:-1]):
        if line.rstrip().endswith("\\"):
            assert lines[i + 1].strip(), f"line {i + 1} continues into a blank line: {line!r}"


# --- the property that makes the rest meaningful ----------------------------


def test_neither_format_ever_emits_two_blank_lines_in_a_row():
    """A doubled separator reads as a section break. Asserted over the
    WHOLE file, so it holds for the sections this file does not name
    individually too."""
    for text in (_upf(), _upf(legacy=True), _cpf()):
        lines = text.splitlines()
        for i in range(1, len(lines)):
            assert lines[i].strip() or lines[i - 1].strip(), f"two blank lines at {i}"
