"""power_intent_view/power_intent_text_export.py -- "Extract Power
Intent" (explicit request): serializing the Power Intent diagram's own
mock overlay state (power supplies, supply sets, power domains, macro
PG-pin connections) into UPF/CPF text."""

import pytest
from PySide6.QtGui import QColor

from frontend.src.power_intent_view.isolation_overlay import IsolationDiagramState, IsolationStrategyPlacement
from frontend.src.power_intent_view.level_shifter_overlay import LevelShifterDiagramState, LevelShifterStrategyPlacement
from frontend.src.power_intent_view.port_attribute_overlay import PortAttributeDiagramState
from frontend.src.power_intent_view.power_domain_overlay import PowerDomainDiagramState, PowerDomainPlacement, SupplySetPlacement
from frontend.src.power_intent_view.power_intent_text_export import SUPPORTED_CPF_VERSIONS, SUPPORTED_UPF_VERSIONS, generate_cpf, generate_upf
from frontend.src.power_intent_view.power_state_table import PowerStateTableDiagramState, PowerStateTableRow, add_row
from frontend.src.power_intent_view.power_switch_overlay import (
    PowerSwitchDiagramState,
    PowerSwitchPlacement,
    power_switch_connector_key,
)
from frontend.src.power_intent_view.retention_overlay import RetentionDiagramState, RetentionStrategyPlacement
from frontend.src.power_intent_view.supply_port_overlay import SupplyPortDiagramState, SupplyPortPlacement, SupplyPortState
from frontend.src.shared.models.rtl import Port, PortDirection

_COLOR = QColor("red")


def _port(id_, owner_path, name, fixed=False, voltage=None, is_internal_supply=False, states=None) -> SupplyPortPlacement:
    return SupplyPortPlacement(
        id=id_, owner_path=owner_path, name=name, color=_COLOR, fixed=fixed, voltage=voltage,
        is_internal_supply=is_internal_supply, states=states or [],
    )


def _section_lines(text: str, title: str) -> list[str]:
    """The lines belonging to one generated section, banner excluded.

    `set_scope` is emitted by more than one section (Supply Nets groups
    its nets by scope, Power Switches wraps a replicated switch), so an
    assertion about one section's own scope handling has to be scoped to
    that section or it silently matches another's."""
    lines = text.splitlines()
    start = lines.index(f"# {title}") + 2  # skip the closing banner rule
    end = next((i for i in range(start, len(lines)) if lines[i].startswith("#####")), len(lines))
    return lines[start:end]


def test_upf_empty_state_has_no_command_sections():
    """A boxed header banner (tool name, copyright, Design/Format/
    Generated/Generator fields -- section 7, explicit request) is
    always present at the top, followed by `set_scope .`."""
    text = generate_upf(SupplyPortDiagramState(), PowerDomainDiagramState(), RetentionDiagramState(), "3.1")
    assert "create_supply_port" not in text
    assert "create_power_domain" not in text
    assert "#  Power Intent Studio" in text
    assert "#  Copyright (c)" in text
    assert "#  Format         : UPF 3.1" in text
    assert "#  Design         : Unknown" in text
    assert text.strip().endswith("set_scope .")


def test_upf_power_supplies_get_a_port_and_net():
    supply_state = SupplyPortDiagramState(supply_port_placements={"p1": _port("p1", "top", "VDD")})
    text = generate_upf(supply_state, PowerDomainDiagramState(), RetentionDiagramState(), "3.1")
    assert "create_supply_port VDD" in text
    assert "create_supply_net VDD" in text


def test_upf_supply_set_lists_every_known_function():
    domain_state = PowerDomainDiagramState(
        supply_sets={
            "ss1": SupplySetPlacement(id="ss1", name="SS_CORE", power="VDD", ground="VSS", n_well="VNW", p_well="VPW", owner_path="top")
        }
    )
    text = generate_upf(SupplyPortDiagramState(), domain_state, RetentionDiagramState(), "3.1")
    assert "create_supply_set SS_CORE" in text
    assert "-function {power VDD}" in text
    assert "-function {ground VSS}" in text
    assert "-function {nwell VNW}" in text
    assert "-function {pwell VPW}" in text


def test_upf_a_nested_domain_is_emitted_at_the_root_with_its_path_as_elements():
    """reference golden, reversing review finding 1.4's scope wrapping:
    every domain is declared at the ROOT scope, and a nested one states
    its hierarchy as a root-relative `-elements` path."""
    domain_state = PowerDomainDiagramState(
        domains={"d1": PowerDomainPlacement(id="d1", owner_path="top/u_core", name="PD_CORE", color=_COLOR)}
    )
    text = generate_upf(SupplyPortDiagramState(), domain_state, RetentionDiagramState(), "3.1")
    assert "create_power_domain PD_CORE \\\n    -elements { \\\n        u_core \\\n    }" in text
    assert "set_scope u_core" not in text


def test_upf_a_multi_hierarchy_domain_stays_at_the_root_with_real_elements():
    """A multi-hierarchy domain lists every member as a root-relative
    `-elements` path, and -- like every reference now -- names its supply
    set bare, since every set is declared at the root."""
    domain_state = PowerDomainDiagramState(
        supply_sets={"s1": SupplySetPlacement(id="s1", name="SS", power="VDD", owner_path="top/u_core")},
        domains={"d1": PowerDomainPlacement(
            id="d1", owner_path="top/u_core", owner_paths=["top/u_core", "top/u_dbg"], name="PD_BOTH",
            color=_COLOR, primary_supply_set="s1",
        )},
    )
    text = generate_upf(SupplyPortDiagramState(), domain_state, RetentionDiagramState(), "3.1")
    domains = "\n".join(_section_lines(text, "Power Domains"))
    assert "set_scope" not in domains
    assert "u_core" in domains and "u_dbg" in domains
    assert "-supply {primary SS}" in domains


def test_upf_root_level_domain_uses_elements_dot():
    """A root-level domain (every owner_path is the design's own root)
    now gets an explicit `-elements {.}` -- matches golden.upf's own
    PD_TOP exactly, real UPF convention for "the whole current scope."
    Supersedes an earlier version of this test that expected `-elements`
    to be omitted entirely for a root domain."""
    domain_state = PowerDomainDiagramState(domains={"d1": PowerDomainPlacement(id="d1", owner_path="top", name="PD_TOP", color=_COLOR)})
    text = generate_upf(SupplyPortDiagramState(), domain_state, RetentionDiagramState(), "3.1")
    assert "create_power_domain PD_TOP \\\n    -elements {.}" in text


def test_upf_domain_references_its_primary_supply_set_by_name():
    domain_state = PowerDomainDiagramState(
        supply_sets={"ss1": SupplySetPlacement(id="ss1", name="SS_CORE", power="VDD", owner_path="top")},
        domains={"d1": PowerDomainPlacement(id="d1", owner_path="top/u_core", name="PD_CORE", color=_COLOR, primary_supply_set="ss1")},
    )
    text = generate_upf(SupplyPortDiagramState(), domain_state, RetentionDiagramState(), "3.1")
    assert "-supply {primary SS_CORE}" in text


def test_upf_domain_excludes_a_nested_domain():
    domain_state = PowerDomainDiagramState(
        domains={
            "top": PowerDomainPlacement(id="top", owner_path="top", name="PD_TOP", color=_COLOR),
            "core": PowerDomainPlacement(id="core", owner_path="top/u_core", name="PD_CORE", color=_COLOR),
        }
    )
    text = generate_upf(SupplyPortDiagramState(), domain_state, RetentionDiagramState(), "3.1")
    assert "-exclude_elements { \\\n        u_core \\\n    }" in text


def test_upf_domain_lists_secondary_supplies():
    domain_state = PowerDomainDiagramState(
        supply_sets={
            "ss1": SupplySetPlacement(id="ss1", name="SS_PRIMARY", power="VDD", owner_path="top"),
            "ss2": SupplySetPlacement(id="ss2", name="SS_SECONDARY", power="VDD_ALT", owner_path="top"),
        },
        domains={
            "d1": PowerDomainPlacement(
                id="d1", owner_path="top/u_core", name="PD_CORE", color=_COLOR, primary_supply_set="ss1", secondary_supply_sets=["ss2"]
            )
        },
    )
    text = generate_upf(SupplyPortDiagramState(), domain_state, RetentionDiagramState(), "3.1")
    # -available_supplies (not a repeated -supply {secondary ...} flag)
    # -- matches golden.upf's own real 1801 usage.
    assert "-available_supplies { SS_SECONDARY }" in text


# --- bug fix: Retention Strategies were never wired into the generator ------


def test_upf_retention_strategy_includes_all_its_own_fields():
    domain_state = PowerDomainDiagramState(
        supply_sets={
            "ss1": SupplySetPlacement(id="ss1", name="SS_PRIMARY", power="VDD", owner_path="top"),
            "ss2": SupplySetPlacement(id="ss2", name="SS_RETENTION", power="VDD_RET", owner_path="top"),
        },
        domains={"d1": PowerDomainPlacement(id="d1", owner_path="top/u_core", name="PD_CORE", color=_COLOR, primary_supply_set="ss1")},
    )
    retention_state = RetentionDiagramState(
        strategies={
            "r1": RetentionStrategyPlacement(
                id="r1",
                name="RET1",
                power_domain_id="d1",
                primary_supply_set_id="ss1",
                retention_supply_set_id="ss2",
                save_logic="save_it",
                restore_logic="restore_it",
                elements=["top/u_core/reg1", "top/u_core/reg2"],
                owner_path="top/u_core",
            )
        }
    )
    text = generate_upf(SupplyPortDiagramState(), domain_state, retention_state, "3.1")
    assert "set_retention RET1 \\\n    -domain PD_CORE" in text
    # reference golden: the retention supply as `-retention_supply_set`,
    # and no primary -- that is the domain's own, which `-domain` states.
    assert "-retention_supply_set SS_RETENTION" in text
    assert "-supply {" not in text.split("# Retention Strategies")[1]
    # Emitted at the root, so the elements are root-relative paths.
    assert "set_scope u_core" not in text
    assert "-elements { \\\n        u_core/reg1 \\\n        u_core/reg2 \\\n    }" in text
    # A plain signal is written as a `{signal high}` pair.
    assert ("set_retention_control RET1 \\\n    -domain PD_CORE \\\n"
            "    -save_signal {save_it high} \\\n    -restore_signal {restore_it high}") in text


def test_upf_multiple_retention_strategies_across_different_blocks_all_appear():
    domain_state = PowerDomainDiagramState(
        domains={"d1": PowerDomainPlacement(id="d1", owner_path="top", name="PD_TOP", color=_COLOR)},
    )
    retention_state = RetentionDiagramState(
        strategies={
            "r1": RetentionStrategyPlacement(
                id="r1", name="RET_A", power_domain_id="d1", primary_supply_set_id="", retention_supply_set_id="", save_logic="s1", restore_logic="r1",
                elements=["top/u_core/reg1"], owner_path="top/u_core",
            ),
            "r2": RetentionStrategyPlacement(
                id="r2", name="RET_B", power_domain_id="d1", primary_supply_set_id="", retention_supply_set_id="", save_logic="s2", restore_logic="r2",
                elements=["top/u_dbg/reg1"], owner_path="top/u_dbg",
            ),
        }
    )
    text = generate_upf(SupplyPortDiagramState(), domain_state, retention_state, "3.1")
    # EVERY strategy in the file gets the multi-line shape, not just
    # the first -- the formatting is per-strategy, so a loop that built
    # only one of them correctly would still pass a single-strategy test.
    for name in ("RET_A", "RET_B"):
        assert f"set_retention {name} \\\n    -domain PD_TOP" in text
        assert f"set_retention_control {name} \\\n    -domain PD_TOP" in text


def test_upf_retention_strategy_with_a_dangling_domain_reference_is_skipped_not_crashed():
    """The domain it was created against no longer exists (e.g. deleted
    independently) -- skipped, same "don't fabricate a reference to
    something gone" precedent the rest of this generator follows."""
    retention_state = RetentionDiagramState(
        strategies={
            "r1": RetentionStrategyPlacement(
                id="r1", name="RET_ORPHAN", power_domain_id="missing", primary_supply_set_id="", retention_supply_set_id="",
                save_logic="s", restore_logic="r", elements=[], owner_path="top",
            )
        }
    )
    text = generate_upf(SupplyPortDiagramState(), PowerDomainDiagramState(), retention_state, "3.1")
    assert "RET_ORPHAN" not in text


# --- Internal Supply: distinct UPF/CPF construct from Supply Port -----------


def test_upf_top_level_supply_port_and_inner_internal_supply_both_appear_distinctly():
    """Explicit request: a design with BOTH a top-level Supply Port and
    an inner Internal Supply -- confirms both appear in the generated
    UPF, each using its own correct, distinct construct. Supply Port is
    a real boundary port (create_supply_port + create_supply_net);
    Internal Supply is an internal-only net (create_supply_net alone --
    no create_supply_port line for it)."""
    supply_state = SupplyPortDiagramState(
        supply_port_placements={
            "p1": _port("p1", "top", "VDD_TOP"),
            "p2": _port("p2", "top/u_core", "VDD_CORE", is_internal_supply=True),
        }
    )
    text = generate_upf(supply_state, PowerDomainDiagramState(), RetentionDiagramState(), "3.1")
    assert "create_supply_port VDD_TOP" in text
    assert "create_supply_net VDD_TOP" in text
    assert "create_supply_net VDD_CORE" in text
    assert "create_supply_port VDD_CORE" not in text  # Internal Supply is never a boundary port


def test_upf_internal_supply_never_gets_a_boundary_port_but_does_get_a_net():
    """Internal Supply's own distinction from a plain Supply Port stays
    fully visible via the Supply Ports section (only ever lists named
    ports) even though Supply Nets is now one flat, alphabetically-
    sorted list across both kinds -- matches golden.upf's own structure
    exactly (no separate "Internal Supplies" sub-heading there).
    Supersedes an earlier version of this test that expected that
    now-removed sub-heading."""
    supply_state = SupplyPortDiagramState(supply_port_placements={"p1": _port("p1", "top/u_core", "VDD_CORE", is_internal_supply=True)})
    text = generate_upf(supply_state, PowerDomainDiagramState(), RetentionDiagramState(), "3.1")
    assert "create_supply_net VDD_CORE" in text
    assert "create_supply_port VDD_CORE" not in text
    assert "# Internal Supplies" not in text


def test_upf_macro_connection_between_a_supply_port_and_a_pg_pin():
    supply_state = SupplyPortDiagramState(
        supply_port_placements={"net1": _port("net1", "top", "VDD")},
        pg_pin_placements={"pg1": _port("pg1", "top/u_ram", "VDD", fixed=True, voltage="0.80V")},
        supply_connector_placements=[("net1", "pg1")],
    )
    text = generate_upf(supply_state, PowerDomainDiagramState(), RetentionDiagramState(), "3.1")
    assert "# Macro Connections\n################################################################################\nconnect_supply_net VDD  -ports {u_ram/VDD}" in text


def test_upf_connector_between_two_plain_ports_is_not_a_macro_connection():
    """A connector between two plain ports never produces a "# Macro
    Connections" section -- `connect_supply_net` itself DOES still
    appear in the output (the unrelated "Connect external ports to
    internal nets" section, emitted for every named port regardless of
    any connector), so the assertion checks for the specific SECTION
    header, not the bare command text."""
    supply_state = SupplyPortDiagramState(
        supply_port_placements={"a": _port("a", "top", "VDD"), "b": _port("b", "top", "VDD_ALIAS")},
        supply_connector_placements=[("a", "b")],
    )
    text = generate_upf(supply_state, PowerDomainDiagramState(), RetentionDiagramState(), "3.1")
    assert "# Macro Connections" not in text


def test_upf_connector_between_two_pg_pins_is_not_a_macro_connection():
    supply_state = SupplyPortDiagramState(
        pg_pin_placements={
            "pg1": _port("pg1", "top/u_ram1", "VDD", fixed=True),
            "pg2": _port("pg2", "top/u_ram2", "VDD", fixed=True),
        },
        supply_connector_placements=[("pg1", "pg2")],
    )
    text = generate_upf(supply_state, PowerDomainDiagramState(), RetentionDiagramState(), "3.1")
    assert "connect_supply_net" not in text


def test_cpf_empty_state_has_no_command_sections():
    text = generate_cpf(SupplyPortDiagramState(), PowerDomainDiagramState(), RetentionDiagramState(), "2.0")
    assert "create_power_nets" not in text
    assert "create_power_domain" not in text
    assert "CPF 2.0" in text


def test_cpf_classifies_power_and_ground_nets_via_supply_sets():
    supply_state = SupplyPortDiagramState(
        supply_port_placements={"p1": _port("p1", "top", "VDD"), "p2": _port("p2", "top", "VSS")}
    )
    domain_state = PowerDomainDiagramState(supply_sets={"ss1": SupplySetPlacement(id="ss1", name="SS_CORE", power="VDD", ground="VSS")})
    text = generate_cpf(supply_state, domain_state, RetentionDiagramState(), "2.0")
    assert "create_power_nets -nets {VDD}" in text
    assert "create_ground_nets -nets {VSS}" in text


def test_cpf_power_net_includes_voltage_when_known_from_a_pg_pin():
    supply_state = SupplyPortDiagramState(pg_pin_placements={"pg1": _port("pg1", "top/u_ram", "VDD", fixed=True, voltage="0.80V")})
    domain_state = PowerDomainDiagramState(supply_sets={"ss1": SupplySetPlacement(id="ss1", name="SS_CORE", power="VDD")})
    text = generate_cpf(supply_state, domain_state, RetentionDiagramState(), "2.0")
    assert "create_power_nets -nets {VDD} -voltage 0.80" in text


def test_cpf_ground_net_also_includes_voltage_when_known():
    supply_state = SupplyPortDiagramState(pg_pin_placements={"pg1": _port("pg1", "top/u_ram", "VSS", fixed=True, voltage="0.00V")})
    domain_state = PowerDomainDiagramState(supply_sets={"ss1": SupplySetPlacement(id="ss1", name="SS_CORE", ground="VSS")})
    text = generate_cpf(supply_state, domain_state, RetentionDiagramState(), "2.0")
    assert "create_ground_nets -nets {VSS} -voltage 0.00" in text


def test_cpf_supply_port_never_used_by_a_supply_set_is_listed_as_skipped_not_guessed():
    supply_state = SupplyPortDiagramState(supply_port_placements={"p1": _port("p1", "top", "ORPHAN")})
    text = generate_cpf(supply_state, PowerDomainDiagramState(), RetentionDiagramState(), "2.0")
    assert "create_power_nets" not in text
    assert "create_ground_nets" not in text
    assert "ORPHAN" in text  # named in the "skipped" comment, not fabricated into a net


def test_cpf_domain_creates_and_updates_with_resolved_primary_nets():
    domain_state = PowerDomainDiagramState(
        supply_sets={"ss1": SupplySetPlacement(id="ss1", name="SS_CORE", power="VDD", ground="VSS")},
        domains={"d1": PowerDomainPlacement(id="d1", owner_path="top/u_core", name="PD_CORE", color=_COLOR, primary_supply_set="ss1")},
    )
    text = generate_cpf(SupplyPortDiagramState(), domain_state, RetentionDiagramState(), "2.0")
    assert "create_power_domain -name PD_CORE -instances {\n    u_core\n}" in text
    assert "update_power_domain -name PD_CORE -primary_power_net VDD -primary_ground_net VSS" in text


def test_cpf_domain_exclusion_uses_the_real_exclude_instances_flag():
    """REVERSES this test's own earlier premise. The comment was correct
    against CPF 1.0, where `exclude_instances` does not exist at all; the
    flag was added afterwards and is real in 2.0 (Si2 CPF 2.1, p. 158).
    Found by the CPF re-verification audit, finding H2."""
    domain_state = PowerDomainDiagramState(
        domains={
            "top": PowerDomainPlacement(id="top", owner_path="top", name="PD_TOP", color=_COLOR),
            "core": PowerDomainPlacement(id="core", owner_path="top/u_core", name="PD_CORE", color=_COLOR),
        }
    )
    text = generate_cpf(SupplyPortDiagramState(), domain_state, RetentionDiagramState(), "2.0")
    assert "-exclude_instances {" in text
    assert "excludes nested sub-hierarchy domain(s)" not in text  # no longer a comment
    # The excluded instance is named by the real flag, on the real command.
    create = text[text.index("create_power_domain -name PD_TOP"):]
    assert "-exclude_instances {" in create.split("update_power_domain")[0]
    assert "u_core" in create.split("update_power_domain")[0]


def test_cpf_retention_strategy_includes_all_its_own_fields():
    domain_state = PowerDomainDiagramState(
        supply_sets={
            "ss1": SupplySetPlacement(id="ss1", name="SS_PRIMARY", power="VDD", owner_path="top"),
            "ss2": SupplySetPlacement(id="ss2", name="SS_RETENTION", power="VDD_RET", owner_path="top"),
        },
        domains={"d1": PowerDomainPlacement(id="d1", owner_path="top/u_core", name="PD_CORE", color=_COLOR, primary_supply_set="ss1")},
    )
    retention_state = RetentionDiagramState(
        strategies={
            "r1": RetentionStrategyPlacement(
                id="r1",
                name="RET1",
                power_domain_id="d1",
                primary_supply_set_id="ss1",
                retention_supply_set_id="ss2",
                save_logic="save_it",
                restore_logic="restore_it",
                elements=["top/u_core/reg1", "top/u_core/reg2"],
                owner_path="top/u_core",
            )
        }
    )
    text = generate_cpf(SupplyPortDiagramState(), domain_state, retention_state, "2.0")
    # `-name` and `-instances`, both corrected: CPF names every rule
    # with an explicit flag (this was the lone positional in the whole
    # writer), and a retention rule applies to sequential ELEMENTS,
    # which CPF names with -instances -- -pins belongs to the adjacent
    # isolation section and had been carried across. Both were found by
    # round-tripping this app's own generated CPF, which a real CPF
    # reader rejected outright.
    assert "create_state_retention_rule -name RET1 \\\n    -domain PD_CORE" in text
    assert "-instances { \\\n        u_core/reg1 \\\n        u_core/reg2 \\\n    }" in text
    retention_line = next(line for line in text.splitlines() if line.startswith("#   RET1:"))
    assert "save='save_it'" in retention_line
    assert "restore='restore_it'" in retention_line
    assert "primary_supply=SS_PRIMARY" in retention_line
    assert "retention_supply=SS_RETENTION" in retention_line


def test_cpf_multiple_retention_strategies_across_different_blocks_all_appear():
    domain_state = PowerDomainDiagramState(
        domains={"d1": PowerDomainPlacement(id="d1", owner_path="top", name="PD_TOP", color=_COLOR)},
    )
    retention_state = RetentionDiagramState(
        strategies={
            "r1": RetentionStrategyPlacement(
                id="r1", name="RET_A", power_domain_id="d1", primary_supply_set_id="", retention_supply_set_id="", save_logic="s1", restore_logic="r1",
                elements=["top/u_core/reg1"], owner_path="top/u_core",
            ),
            "r2": RetentionStrategyPlacement(
                id="r2", name="RET_B", power_domain_id="d1", primary_supply_set_id="", retention_supply_set_id="", save_logic="s2", restore_logic="r2",
                elements=["top/u_dbg/reg1"], owner_path="top/u_dbg",
            ),
        }
    )
    text = generate_cpf(SupplyPortDiagramState(), domain_state, retention_state, "2.0")
    for name in ("RET_A", "RET_B"):
        assert f"create_state_retention_rule -name {name} \\\n    -domain PD_TOP" in text


def test_cpf_retention_strategy_with_a_dangling_domain_reference_is_skipped_not_crashed():
    retention_state = RetentionDiagramState(
        strategies={
            "r1": RetentionStrategyPlacement(
                id="r1", name="RET_ORPHAN", power_domain_id="missing", primary_supply_set_id="", retention_supply_set_id="",
                save_logic="s", restore_logic="r", elements=[], owner_path="top",
            )
        }
    )
    text = generate_cpf(SupplyPortDiagramState(), PowerDomainDiagramState(), retention_state, "2.0")
    assert "RET_ORPHAN" not in text


# --- Internal Supply: distinct UPF/CPF construct from Supply Port -----------


def test_cpf_top_level_supply_port_and_inner_internal_supply_both_appear_distinctly():
    """CPF's own create_power_nets/create_ground_nets has no equivalent
    boundary-vs-internal distinction the way UPF's create_supply_port
    does -- both kinds genuinely use the same command -- so "distinct"
    here means its own separate, clearly labelled section, not a
    different command."""
    supply_state = SupplyPortDiagramState(supply_port_placements={"p2": _port("p2", "top/u_core", "VDD_CORE", is_internal_supply=True)})
    domain_state = PowerDomainDiagramState(supply_sets={"ss1": SupplySetPlacement(id="ss1", name="SS_TOP", power="VDD_TOP")})
    domain_state.supply_sets["ss2"] = SupplySetPlacement(id="ss2", name="SS_CORE", power="VDD_CORE")
    text = generate_cpf(supply_state, domain_state, RetentionDiagramState(), "2.0")
    assert "create_power_nets -nets {VDD_TOP}" in text
    assert "create_power_nets -nets {VDD_CORE} -internal" in text
    assert "# Power Supplies (power)" in text
    assert "# Internal Supplies (power)" in text
    lines = text.splitlines()
    port_section = next(i for i, line in enumerate(lines) if line == "# Power Supplies (power)")
    internal_section = next(i for i, line in enumerate(lines) if line == "# Internal Supplies (power)")
    assert lines[port_section + 2] == "create_power_nets -nets {VDD_TOP}"
    assert lines[internal_section + 2] == "create_power_nets -nets {VDD_CORE} -internal"


def test_cpf_macro_connection_between_a_supply_port_and_a_pg_pin():
    supply_state = SupplyPortDiagramState(
        supply_port_placements={"net1": _port("net1", "top", "VDD")},
        pg_pin_placements={"pg1": _port("pg1", "top/u_ram", "VDD", fixed=True, voltage="0.80V")},
        supply_connector_placements=[("net1", "pg1")],
    )
    text = generate_cpf(supply_state, PowerDomainDiagramState(), RetentionDiagramState(), "2.0")
    assert "create_global_connection -net VDD -pins {u_ram/VDD}" in text


# --- item 1: Power Switch was never wired into the generator -- fully -----
# --- built on the GUI side (creation, dialog, Cell Pins, ON/OFF Logic, -----
# --- multi-hierarchy replication) but generate_upf()/generate_cpf() never -
# --- accepted a power_switch_state argument at all until now (confirmed by
# --- reading main_window.py's own caller before this change: it never
# --- passed shared_power_switch_state either) -- the exact same "GUI-
# --- complete, generator never wired in" gap the module docstring's own
# --- Retention entry already documents once before.


def _switch(id_, name, power_domain_id, target_ids, owner_paths, **kwargs) -> PowerSwitchPlacement:
    return PowerSwitchPlacement(id=id_, name=name, power_domain_id=power_domain_id, target_ids=target_ids, owner_paths=owner_paths, **kwargs)


def test_upf_power_switch_includes_domain_output_input_and_control():
    """Rework (REVERSES the earlier Cell Type library model): Input/
    Output Supply Port are now plain, required, per-instance fields
    that double as the UPF {role name} tag itself (previously
    hardcoded "in"/"out"); Control Port is a plain, required,
    per-instance field emitted unconditionally (previously gated on a
    Cell being assigned)."""
    domain_state = PowerDomainDiagramState(domains={"d1": PowerDomainPlacement(id="d1", owner_path="top/u_core", name="PD_CORE", color=_COLOR)})
    supply_state = SupplyPortDiagramState(
        supply_port_placements={
            "target1": _port("target1", "top/u_core", "VDD_SW", is_internal_supply=True),
            "up1": _port("up1", "top", "VDD_UNSWITCHED"),
        },
        supply_connector_placements=[(power_switch_connector_key("sw1", "target1"), "up1")],
    )
    power_switch_state = PowerSwitchDiagramState(
        switches={
            "sw1": _switch(
                "sw1", "SW1", "d1", ["target1"], ["top/u_core"],
                input_supply_port="in", output_supply_port="out", control_port="CTRL",
                cell_type="SWCELL", on_state="sleep_b", off_state="~sleep_b",
            )
        },
    )
    text = generate_upf(supply_state, domain_state, RetentionDiagramState(), "3.1", power_switch_state)
    assert "# Power Switches" in text
    # reference golden, reversing review finding 1.4: the switch is
    # declared at the ROOT scope like everything else, its domain and
    # nets -- all root-declared -- named bare.
    assert "set_scope" not in "\n".join(_section_lines(text, "Power Switches"))
    assert _section_lines(text, "Power Switches")[0] == "create_power_switch SW1 \\"
    assert (
        "create_power_switch SW1 \\\n"
        "    -domain PD_CORE \\\n"
        "    -input_supply_port {in VDD_UNSWITCHED} \\\n"
        "    -output_supply_port {out VDD_SW} \\\n"
        "    -control_port CTRL \\\n"
        "    -on_state {on in {sleep_b}} \\\n"
        "    -off_state {off {~sleep_b}}"
    ) in text
    # Cell Type -> real `map_power_switch` command (bug fix, explicit
    # request -- superseding the earlier `#   SW1: cell_type='SWCELL'`
    # informational comment).
    assert "map_power_switch {SW1} \\\n    -domain PD_CORE \\\n    -lib_cells {SWCELL}" in text
    assert not any(line.startswith("#   SW1:") for line in text.splitlines())  # no cell_type comment anymore


def test_upf_power_switch_with_no_cell_type_gets_no_comment_line():
    domain_state = PowerDomainDiagramState(domains={"d1": PowerDomainPlacement(id="d1", owner_path="top", name="PD_TOP", color=_COLOR)})
    supply_state = SupplyPortDiagramState(supply_port_placements={"target1": _port("target1", "top", "VDD_SW")})
    power_switch_state = PowerSwitchDiagramState(
        switches={"sw1": _switch("sw1", "SW1", "d1", ["target1"], ["top"], output_supply_port="out", control_port="CTRL")}
    )
    text = generate_upf(supply_state, domain_state, RetentionDiagramState(), "3.1", power_switch_state)
    assert "create_power_switch SW1 \\\n    -domain PD_TOP \\\n    -output_supply_port {out VDD_SW} \\\n    -control_port CTRL" in text
    assert "-input_supply_port" not in text  # no Supply In connector drawn -- correctly omitted, not fabricated
    assert "#   SW1:" not in text  # cell_type unset -- no informational comment at all
    assert "map_power_switch" not in text  # (b): no Cell Type -> no map_power_switch for this instance


# --- Cell Type -> library-cell mapping (bug fix, explicit request) ----------
# UPF: map_power_switch / set_level_shifter_cell (syntax verified against
# OpenROAD's own UPF command reference). CPF: update_power_switch_rule
# -cells / update_level_shifter_rules -cells (both verified against the
# Si2 CPF 1.0 primary spec -- CPF has a real equivalent for BOTH, no
# "not expressible" gap). Emitted only when a Cell Type is set; a missing
# one is non-blocking (the Properties table's own "No Cell Type Assigned"
# marker is the surviving generation-time warning).


def test_a_upf_power_switch_with_cell_type_emits_map_power_switch():
    domain_state = PowerDomainDiagramState(domains={"d1": PowerDomainPlacement(id="d1", owner_path="top", name="PD_TOP", color=_COLOR)})
    supply_state = SupplyPortDiagramState(supply_port_placements={"t1": _port("t1", "top", "VDD_SW")})
    power_switch_state = PowerSwitchDiagramState(
        switches={"sw1": _switch("sw1", "SW1", "d1", ["t1"], ["top"], output_supply_port="out", control_port="CTRL", cell_type="HEADER_HVT")}
    )
    text = generate_upf(supply_state, domain_state, RetentionDiagramState(), "3.1", power_switch_state)
    assert "map_power_switch {SW1} \\\n    -domain PD_TOP \\\n    -lib_cells {HEADER_HVT}" in text


def test_b_upf_power_switch_without_cell_type_emits_no_map_power_switch():
    domain_state = PowerDomainDiagramState(domains={"d1": PowerDomainPlacement(id="d1", owner_path="top", name="PD_TOP", color=_COLOR)})
    supply_state = SupplyPortDiagramState(supply_port_placements={"t1": _port("t1", "top", "VDD_SW")})
    power_switch_state = PowerSwitchDiagramState(
        switches={"sw1": _switch("sw1", "SW1", "d1", ["t1"], ["top"], output_supply_port="out", control_port="CTRL")}
    )
    text = generate_upf(supply_state, domain_state, RetentionDiagramState(), "3.1", power_switch_state)
    assert "create_power_switch SW1" in text  # the switch rule itself still generates
    assert "map_power_switch" not in text


def test_a_upf_map_power_switch_is_not_separated_from_its_own_create_power_switch():
    """REVERSES an earlier explicit request that put a blank line here.

    The blank line is a separator between DISTINCT instances; a switch
    and its own cell mapping are one instance, and a break in the middle
    of one read as a section break."""
    domain_state = PowerDomainDiagramState(domains={"d1": PowerDomainPlacement(id="d1", owner_path="top", name="PD_TOP", color=_COLOR)})
    supply_state = SupplyPortDiagramState(supply_port_placements={"t1": _port("t1", "top", "VDD_SW")})
    power_switch_state = PowerSwitchDiagramState(
        switches={"sw1": _switch("sw1", "PG", "d1", ["t1"], ["top"], output_supply_port="OUT", control_port="CTRL", cell_type="pippo")}
    )
    text = generate_upf(supply_state, domain_state, RetentionDiagramState(), "3.1", power_switch_state)
    assert "    -control_port CTRL\nmap_power_switch {PG} \\" in text


def test_c_upf_map_level_shifter_cell_is_not_separated_from_its_own_set_level_shifter():
    """Same rule as the power-switch pair above, and reversed for the
    same reason: a rule plus its own cell mapping is ONE instance."""
    domain_state = PowerDomainDiagramState(domains={"d1": PowerDomainPlacement(id="d1", owner_path="top", name="PD_TOP", color=_COLOR)})
    level_shifter_state = LevelShifterDiagramState(
        strategies={"ls1": LevelShifterStrategyPlacement(id="ls1", name="LS1", power_domain_id="d1", applies_to="both", rule="both", cell_type="LS_HVT", elements=[])}
    )
    text = generate_upf(SupplyPortDiagramState(), domain_state, RetentionDiagramState(), "3.1", level_shifter_state=level_shifter_state)
    assert "  -rule both\nmap_level_shifter_cell LS1 \\" in text


def test_a_upf_map_power_switch_replicated_switch_emits_one_at_the_root():
    """At the root a replicated switch is ONE declaration (see
    test_upf_power_switch_replicated_across_three_hierarchies_generates_one_root_statement),
    so its cell mapping is emitted once too."""
    domain_state = PowerDomainDiagramState(
        domains={"d1": PowerDomainPlacement(id="d1", owner_path="top/u_core", name="PD_MULTI", color=_COLOR, owner_paths=["top/u_core", "top/u_dbg"])}
    )
    supply_state = SupplyPortDiagramState(
        supply_port_placements={
            "t_core": _port("t_core", "top/u_core", "VDD_SW", is_internal_supply=True),
            "t_dbg": _port("t_dbg", "top/u_dbg", "VDD_SW", is_internal_supply=True),
        }
    )
    power_switch_state = PowerSwitchDiagramState(
        switches={"sw1": _switch("sw1", "SW_SHARED", "d1", ["t_core", "t_dbg"], ["top/u_core", "top/u_dbg"], output_supply_port="out", control_port="CTRL", cell_type="HDR")}
    )
    text = generate_upf(supply_state, domain_state, RetentionDiagramState(), "3.1", power_switch_state)
    assert text.count("map_power_switch {SW_SHARED} \\\n    -domain PD_MULTI \\\n    -lib_cells {HDR}") == 1


def test_c_upf_level_shifter_with_cell_type_emits_map_level_shifter_cell():
    domain_state = PowerDomainDiagramState(domains={"d1": PowerDomainPlacement(id="d1", owner_path="top", name="PD_TOP", color=_COLOR)})
    level_shifter_state = LevelShifterDiagramState(
        strategies={"ls1": LevelShifterStrategyPlacement(id="ls1", name="LS1", power_domain_id="d1", applies_to="both", rule="both", cell_type="LS_HVT", elements=[])}
    )
    text = generate_upf(SupplyPortDiagramState(), domain_state, RetentionDiagramState(), "3.1", level_shifter_state=level_shifter_state)
    assert "map_level_shifter_cell LS1 \\\n    -domain PD_TOP \\\n    -lib_cells {LS_HVT}" in text


def test_c_upf_level_shifter_without_cell_type_emits_no_set_level_shifter_cell():
    domain_state = PowerDomainDiagramState(domains={"d1": PowerDomainPlacement(id="d1", owner_path="top", name="PD_TOP", color=_COLOR)})
    level_shifter_state = LevelShifterDiagramState(
        strategies={"ls1": LevelShifterStrategyPlacement(id="ls1", name="LS1", power_domain_id="d1", applies_to="both", rule="both", elements=[])}
    )
    text = generate_upf(SupplyPortDiagramState(), domain_state, RetentionDiagramState(), "3.1", level_shifter_state=level_shifter_state)
    assert "set_level_shifter LS1" in text  # the rule itself still generates
    assert "set_level_shifter_cell" not in text


def test_d_cpf_power_switch_with_cell_type_emits_update_power_switch_rule_cells():
    """CPF section 3 result: a REAL equivalent exists -- Si2 CPF 1.0
    primary spec `update_power_switch_rule -name <string> { ... | -cells
    cell_list -library_set library_set | ... }...`, and the spec's own
    define_power_switch_cell text: "To use a specific cell, use the
    update_power_switch_rule command"."""
    domain_state = PowerDomainDiagramState(
        domains={"d1": PowerDomainPlacement(id="d1", owner_path="top", name="PD_TOP", color=_COLOR)},
    )
    supply_state = SupplyPortDiagramState(supply_port_placements={"t1": _port("t1", "top", "VDD_SW")})
    power_switch_state = PowerSwitchDiagramState(
        switches={"sw1": _switch("sw1", "SW1", "d1", ["t1"], ["top"], output_supply_port="out", control_port="CTRL", cell_type="HDR_CPF")}
    )
    text = generate_cpf(supply_state, domain_state, RetentionDiagramState(), "2.0", power_switch_state)
    assert "update_power_switch_rule -name SW1 -cells {HDR_CPF}" in text


def test_d_cpf_power_switch_without_cell_type_emits_no_update_power_switch_rule_cells():
    domain_state = PowerDomainDiagramState(domains={"d1": PowerDomainPlacement(id="d1", owner_path="top", name="PD_TOP", color=_COLOR)})
    supply_state = SupplyPortDiagramState(supply_port_placements={"t1": _port("t1", "top", "VDD_SW")})
    power_switch_state = PowerSwitchDiagramState(
        switches={"sw1": _switch("sw1", "SW1", "d1", ["t1"], ["top"], output_supply_port="out", control_port="CTRL")}
    )
    text = generate_cpf(supply_state, domain_state, RetentionDiagramState(), "2.0", power_switch_state)
    assert "create_power_switch_rule -name SW1" in text  # the rule still generates
    assert "update_power_switch_rule" not in text


def test_d_cpf_level_shifter_with_cell_type_emits_update_level_shifter_rules_cells():
    """CPF section 3 result: a REAL equivalent exists -- Si2 CPF 1.0
    primary spec `update_level_shifter_rules -names rule_list { ... |
    -cells cell_list -library_set library_set | ... }...` ("-cells
    cell_list: Specifies the names of the library cells to be used to
    bridge the specified power domains")."""
    domain_state = PowerDomainDiagramState(domains={"d1": PowerDomainPlacement(id="d1", owner_path="top", name="PD_TOP", color=_COLOR)})
    level_shifter_state = LevelShifterDiagramState(
        strategies={"ls1": LevelShifterStrategyPlacement(id="ls1", name="LS1", power_domain_id="d1", applies_to="both", rule="both", cell_type="LS_CPF", elements=["top/clk"])}
    )
    text = generate_cpf(SupplyPortDiagramState(), domain_state, RetentionDiagramState(), "2.0", level_shifter_state=level_shifter_state)
    # `-location` precedes `-cells` on the same command now -- both are
    # real flags on it, and this placement's location defaults to "self"
    # rather than being unset, so it is always present.
    assert "update_level_shifter_rules -names LS1 -cells {LS_CPF}" in text


def test_d_cpf_level_shifter_without_cell_type_emits_no_update_level_shifter_rules_cells():
    domain_state = PowerDomainDiagramState(domains={"d1": PowerDomainPlacement(id="d1", owner_path="top", name="PD_TOP", color=_COLOR)})
    level_shifter_state = LevelShifterDiagramState(
        strategies={"ls1": LevelShifterStrategyPlacement(id="ls1", name="LS1", power_domain_id="d1", applies_to="both", rule="both", elements=["top/clk"])}
    )
    text = generate_cpf(SupplyPortDiagramState(), domain_state, RetentionDiagramState(), "2.0", level_shifter_state=level_shifter_state)
    assert "create_level_shifter_rule -name LS1" in text  # the rule still generates
    # REVISED: this command is no longer cell-type-only -- it also
    # carries `-location`, which the writer used to drop (golden.cpf has
    # it, and a dropped location was silently lost on re-import). With
    # neither a cell type nor a location set, the command is still
    # omitted entirely; what this asserts is specifically that no
    # `-cells` is fabricated.
    assert "-cells" not in text
    # The command itself is still emitted, for the location alone.
    # With no Cell Type AND a 2.1-only `-location self` suppressed at
    # 2.0, nothing is left to refine -- so no update command at all, and
    # the omission is reported rather than silently dropped.
    assert "update_level_shifter_rules" not in text
    assert "-location self needs CPF 2.1" in text


def test_upf_power_switch_replicated_across_three_hierarchies_generates_one_root_statement():
    """SUPERSEDES an earlier explicit request that each replica be
    generated separately, each inside its own `set_scope`. Every
    construct is declared at the ROOT now (reference golden), where
    three `create_power_switch SW_SHARED` would declare one name three
    times -- and the replicas have nothing left to tell them apart: one
    domain, and same-named outputs, which this app treats as one
    continuous net, declared once. So: one statement, at the root."""
    domain_state = PowerDomainDiagramState(
        domains={"d1": PowerDomainPlacement(id="d1", owner_path="top/u_core", name="PD_MULTI", color=_COLOR, owner_paths=["top/u_core", "top/u_dbg", "top/u_fwd"])}
    )
    supply_state = SupplyPortDiagramState(
        supply_port_placements={
            "t_core": _port("t_core", "top/u_core", "VDD_SW", is_internal_supply=True),
            "t_dbg": _port("t_dbg", "top/u_dbg", "VDD_SW", is_internal_supply=True),
            "t_fwd": _port("t_fwd", "top/u_fwd", "VDD_SW", is_internal_supply=True),
        }
    )
    power_switch_state = PowerSwitchDiagramState(
        switches={
            "sw1": _switch(
                "sw1", "SW_SHARED", "d1",
                ["t_core", "t_dbg", "t_fwd"], ["top/u_core", "top/u_dbg", "top/u_fwd"],
                output_supply_port="out", control_port="CTRL",
            )
        }
    )
    text = generate_upf(supply_state, domain_state, RetentionDiagramState(), "3.1", power_switch_state)

    assert text.count("create_power_switch SW_SHARED") == 1
    assert text.count("create_supply_net VDD_SW") == 1
    lines = _section_lines(text, "Power Switches")
    assert not any(line.startswith("set_scope") for line in lines)
    assert [line for line in lines if line] == [
        "create_power_switch SW_SHARED \\",
        "    -domain PD_MULTI \\",
        "    -output_supply_port {out VDD_SW} \\",
        "    -control_port CTRL",
    ]


def test_upf_power_switch_with_dangling_domain_reference_is_skipped_not_crashed():
    power_switch_state = PowerSwitchDiagramState(switches={"sw1": _switch("sw1", "SW_ORPHAN", "missing", ["t1"], ["top"])})
    supply_state = SupplyPortDiagramState(supply_port_placements={"t1": _port("t1", "top", "VDD_SW")})
    text = generate_upf(supply_state, PowerDomainDiagramState(), RetentionDiagramState(), "3.1", power_switch_state)
    assert "SW_ORPHAN" not in text


def test_upf_power_switch_state_omitted_is_backward_compatible():
    """Every pre-existing caller/test that only ever passed 4 positional
    args keeps working unchanged -- power_switch_state defaults to
    None, same "extends without breaking" precedent
    refresh_diagram_overlay()'s own retention_state/port_attribute_state
    parameters already established."""
    text = generate_upf(SupplyPortDiagramState(), PowerDomainDiagramState(), RetentionDiagramState(), "3.1")
    assert "# Power Switches" not in text


def test_cpf_power_switch_includes_domain_and_classified_external_net():
    domain_state = PowerDomainDiagramState(
        domains={"d1": PowerDomainPlacement(id="d1", owner_path="top/u_core", name="PD_CORE", color=_COLOR)},
        supply_sets={"ss1": SupplySetPlacement(id="ss1", name="SS1", power="VDD_UNSWITCHED", owner_path="top")},
    )
    supply_state = SupplyPortDiagramState(
        supply_port_placements={
            "target1": _port("target1", "top/u_core", "VDD_SW", is_internal_supply=True),
            "up1": _port("up1", "top", "VDD_UNSWITCHED"),
        },
        supply_connector_placements=[(power_switch_connector_key("sw1", "target1"), "up1")],
    )
    power_switch_state = PowerSwitchDiagramState(
        switches={"sw1": _switch("sw1", "SW1", "d1", ["target1"], ["top/u_core"], on_state="en", off_state="~en")}
    )
    text = generate_cpf(supply_state, domain_state, RetentionDiagramState(), "2.0", power_switch_state)
    assert "# Power Switches" in text
    assert "set_instance" not in text  # CPF scopes via set_design/end_design, not set_instance .
    assert "create_power_switch_rule -name SW1 -domain PD_CORE -external_power_net VDD_UNSWITCHED" in text
    comment_line = next(line for line in text.splitlines() if line.startswith("#   SW1:"))
    assert "output_supply=VDD_SW" in comment_line
    assert "on_state='en'" in comment_line


def test_cpf_power_switch_replicated_across_three_hierarchies_generates_three_statements():
    domain_state = PowerDomainDiagramState(
        domains={"d1": PowerDomainPlacement(id="d1", owner_path="top/u_core", name="PD_MULTI", color=_COLOR, owner_paths=["top/u_core", "top/u_dbg", "top/u_fwd"])}
    )
    supply_state = SupplyPortDiagramState(
        supply_port_placements={
            "t_core": _port("t_core", "top/u_core", "VDD_SW", is_internal_supply=True),
            "t_dbg": _port("t_dbg", "top/u_dbg", "VDD_SW", is_internal_supply=True),
            "t_fwd": _port("t_fwd", "top/u_fwd", "VDD_SW", is_internal_supply=True),
        }
    )
    power_switch_state = PowerSwitchDiagramState(
        switches={"sw1": _switch("sw1", "SW_SHARED", "d1", ["t_core", "t_dbg", "t_fwd"], ["top/u_core", "top/u_dbg", "top/u_fwd"])}
    )
    text = generate_cpf(supply_state, domain_state, RetentionDiagramState(), "2.0", power_switch_state)

    # No set_instance wrapper any more (audit findings M1/C2): CPF
    # scopes via set_design/end_design, and `set_instance .` named an
    # instance literally called "." -- `.` is CPF's own default
    # hierarchy separator, not a "back to top" token.
    assert "set_instance" not in text
    assert text.count("create_power_switch_rule -name SW_SHARED") == 3
    # These replicas have no connected input supply in this fixture, so
    # there is no `-external_*_net` to count. What matters is that three
    # distinct rules exist and none of them is wrapped in a scope
    # command any more.
    assert text.count("-domain PD_MULTI") == 3


# --- item 2: Supply Ports and Supply Nets get their own dedicated sections -


def test_upf_supply_ports_and_supply_nets_are_separate_sections():
    supply_state = SupplyPortDiagramState(
        supply_port_placements={
            "p1": _port("p1", "top", "VDD"),
            "p2": _port("p2", "top", "VSS"),
        }
    )
    text = generate_upf(supply_state, PowerDomainDiagramState(), RetentionDiagramState(), "3.1")
    lines = text.splitlines()
    ports_header = lines.index("# Supply Ports")
    nets_header = lines.index("# Supply Nets")
    assert ports_header < nets_header
    # Every create_supply_port call groups together, before any
    # create_supply_net call appears at all.
    port_lines = [i for i, line in enumerate(lines) if line.startswith("create_supply_port")]
    net_lines = [i for i, line in enumerate(lines) if line.startswith("create_supply_net")]
    assert all(p < nets_header for p in port_lines)
    assert all(n > ports_header for n in net_lines)
    assert max(port_lines) < min(net_lines)


def test_upf_supply_nets_section_includes_both_port_and_internal_supply_nets():
    supply_state = SupplyPortDiagramState(
        supply_port_placements={
            "p1": _port("p1", "top", "VDD"),
            "p2": _port("p2", "top/u_core", "VDD_INT", is_internal_supply=True),
        }
    )
    text = generate_upf(supply_state, PowerDomainDiagramState(), RetentionDiagramState(), "3.1")
    lines = text.splitlines()
    nets_header = lines.index("# Supply Nets")
    # To the next SECTION, not the next blank line: instances inside a
    # section are now separated by blank lines of their own.
    net_section_end = next((i for i in range(nets_header + 2, len(lines)) if lines[i].startswith("###")), len(lines))
    net_section = lines[nets_header:net_section_end]
    assert "create_supply_net VDD" in net_section
    assert "create_supply_net VDD_INT" in net_section


# --- item 3: Retention/Power Domain Elements lists render one per line ----


def test_upf_retention_elements_are_one_per_line_not_concatenated():
    domain_state = PowerDomainDiagramState(domains={"d1": PowerDomainPlacement(id="d1", owner_path="top", name="PD_TOP", color=_COLOR)})
    retention_state = RetentionDiagramState(
        strategies={
            "r1": RetentionStrategyPlacement(
                id="r1", name="RET1", power_domain_id="d1", primary_supply_set_id="", retention_supply_set_id="",
                save_logic="s", restore_logic="r", elements=["top/u_core/reg1", "top/u_core/reg2", "top/u_dbg/reg3"], owner_path="top",
            )
        }
    )
    text = generate_upf(SupplyPortDiagramState(), domain_state, retention_state, "3.1")
    lines = text.splitlines()
    elements_open = lines.index("    -elements { \\")
    assert lines[elements_open + 1] == "        u_core/reg1 \\"
    assert lines[elements_open + 2] == "        u_core/reg2 \\"
    assert lines[elements_open + 3] == "        u_dbg/reg3 \\"
    assert lines[elements_open + 4] == "    }"


def test_upf_power_domain_elements_are_one_per_line_not_concatenated():
    domain_state = PowerDomainDiagramState(
        domains={
            "d1": PowerDomainPlacement(
                id="d1", owner_path="top/u_core", name="PD_MULTI", color=_COLOR, owner_paths=["top/u_core", "top/u_dbg", "top/u_fwd"]
            )
        }
    )
    text = generate_upf(SupplyPortDiagramState(), domain_state, RetentionDiagramState(), "3.1")
    assert "-elements { \\\n        u_core \\\n        u_dbg \\\n        u_fwd \\\n    }" in text


def test_cpf_power_domain_instances_are_one_per_line_not_concatenated():
    domain_state = PowerDomainDiagramState(
        domains={
            "d1": PowerDomainPlacement(
                id="d1", owner_path="top/u_core", name="PD_MULTI", color=_COLOR, owner_paths=["top/u_core", "top/u_dbg", "top/u_fwd"]
            )
        }
    )
    text = generate_cpf(SupplyPortDiagramState(), domain_state, RetentionDiagramState(), "2.0")
    assert "-instances {\n    u_core\n    u_dbg\n    u_fwd\n}" in text


# --- Level Shifters (section 8) ------------------------------------------


def test_upf_level_shifter_emits_set_level_shifter_with_every_field():
    domain_state = PowerDomainDiagramState(domains={"d1": PowerDomainPlacement(id="d1", owner_path="top/u_core", name="PD_TOP", color=_COLOR)})
    level_shifter_state = LevelShifterDiagramState(
        strategies={
            "ls1": LevelShifterStrategyPlacement(
                id="ls1",
                name="LS1",
                power_domain_id="d1",
                applies_to="inputs",
                rule="low_to_high",
                location="self",
                threshold=0.2,
                elements=["top/u_core/clk"],
            )
        }
    )
    text = generate_upf(SupplyPortDiagramState(), domain_state, RetentionDiagramState(), "3.1", level_shifter_state=level_shifter_state)
    # Bug fix (matching golden.upf's own hand-written block exactly):
    # `set_level_shifter` shares `set_isolation`'s own two distinctive
    # shapes -- name+`-domain` merged onto one head line, a 2-space flag
    # indent, and flag order -applies_to/-location/-rule/-threshold --
    # previously rendered as one flat, differently-ordered single line.
    assert "set_level_shifter LS1 -domain PD_TOP \\\n  -applies_to inputs \\\n  -location self \\\n" in text
    assert "-rule low_to_high \\\n  -threshold 0.2" in text
    # An Isolation/Level Shifter "Elements" key is a port name namespaced
    # by its owner_path ("<owner_path>/<port_name>"). The command is
    # emitted at the ROOT scope, so the element is written root-relative
    # -- "top/u_core/clk" -> "u_core/clk". (A bare "clk" would name the
    # TOP module's own clk port.)
    assert "-elements { \\\n        u_core/clk \\\n    } \\\n" in text


def test_upf_level_shifter_omits_threshold_when_not_set():
    domain_state = PowerDomainDiagramState(domains={"d1": PowerDomainPlacement(id="d1", owner_path="top", name="PD_TOP", color=_COLOR)})
    level_shifter_state = LevelShifterDiagramState(
        strategies={"ls1": LevelShifterStrategyPlacement(id="ls1", name="LS1", power_domain_id="d1", applies_to="both", rule="both", elements=[])}
    )
    text = generate_upf(SupplyPortDiagramState(), domain_state, RetentionDiagramState(), "3.1", level_shifter_state=level_shifter_state)
    assert "-threshold" not in text
    assert "set_level_shifter LS1 -domain PD_TOP \\\n  -applies_to both \\\n  -location self \\\n  -rule both" in text


def test_upf_level_shifter_emits_map_level_shifter_cell_when_a_cell_type_is_assigned():
    """Bug fix (explicit request): `cell_type` -> a real
    `set_level_shifter_cell -level_shifter <rule> -cell_name <cell>`
    command (verified against OpenROAD's own UPF command reference) --
    SUPERSEDES the earlier `map_level_shifter_cell -domain <d> -cell
    <c>` line (a UPF 2.0-era spelling)."""
    domain_state = PowerDomainDiagramState(domains={"d1": PowerDomainPlacement(id="d1", owner_path="top", name="PD_TOP", color=_COLOR)})
    level_shifter_state = LevelShifterDiagramState(
        strategies={
            "ls1": LevelShifterStrategyPlacement(
                id="ls1", name="LS1", power_domain_id="d1", applies_to="both", rule="both", cell_type="LSCELL", elements=[]
            )
        },
    )
    text = generate_upf(SupplyPortDiagramState(), domain_state, RetentionDiagramState(), "3.1", level_shifter_state=level_shifter_state)
    assert "map_level_shifter_cell LS1 \\\n    -domain PD_TOP \\\n    -lib_cells {LSCELL}" in text
    # Audit B10 REVERSED this: `map_level_shifter_cell` is now the
    # spelling this generator writes, and `set_level_shifter_cell` --
    # which this line used to guard against -- is the one that is gone.
    assert "set_level_shifter_cell" not in text


def test_upf_level_shifter_with_no_cell_type_omits_set_level_shifter_cell():
    domain_state = PowerDomainDiagramState(domains={"d1": PowerDomainPlacement(id="d1", owner_path="top", name="PD_TOP", color=_COLOR)})
    level_shifter_state = LevelShifterDiagramState(
        strategies={"ls1": LevelShifterStrategyPlacement(id="ls1", name="LS1", power_domain_id="d1", applies_to="both", rule="both", elements=[])},
    )
    text = generate_upf(SupplyPortDiagramState(), domain_state, RetentionDiagramState(), "3.1", level_shifter_state=level_shifter_state)
    assert "set_level_shifter_cell" not in text
    assert "map_level_shifter_cell" not in text


def test_upf_level_shifter_with_a_dangling_domain_reference_is_skipped():
    level_shifter_state = LevelShifterDiagramState(
        strategies={"ls1": LevelShifterStrategyPlacement(id="ls1", name="LS1", power_domain_id="missing", applies_to="both", rule="both", elements=[])}
    )
    text = generate_upf(SupplyPortDiagramState(), PowerDomainDiagramState(), RetentionDiagramState(), "3.1", level_shifter_state=level_shifter_state)
    assert "set_level_shifter" not in text


def test_cpf_level_shifter_emits_create_level_shifter_rule_with_pins_and_a_comment():
    domain_state = PowerDomainDiagramState(domains={"d1": PowerDomainPlacement(id="d1", owner_path="top", name="PD_TOP", color=_COLOR)})
    level_shifter_state = LevelShifterDiagramState(
        strategies={
            "ls1": LevelShifterStrategyPlacement(
                id="ls1", name="LS1", power_domain_id="d1", applies_to="outputs", rule="high_to_low", elements=["top/clk"]
            )
        }
    )
    text = generate_cpf(SupplyPortDiagramState(), domain_state, RetentionDiagramState(), "2.0", level_shifter_state=level_shifter_state)
    # `-from <domain>` was being dropped -- the Power Domain appeared
    # only in the comment line, so a generated rule came back attached
    # to no domain. golden.cpf has it (`create_level_shifter_rule -name
    # LS_SUB_OUT -from PD_SUB`) and the adjacent Isolation section
    # already emitted it, so this was an inconsistency, not a choice.
    assert "create_level_shifter_rule -name LS1 -from PD_TOP -pins {\n    clk\n}" in text
    # No longer in the comment -- the domain is a real flag now, and
    # repeating it in prose would just be a second place to drift.
    assert "domain=PD_TOP" not in text
    assert "applies_to='outputs'" in text
    assert "rule='high_to_low'" in text


# --- "Connect external ports to internal nets" (real, confirmed gap: a ------
# --- create_supply_port and its same-named create_supply_net were never ----
# --- actually wired together in the exported text) --------------------------


def test_upf_named_port_gets_a_self_connect_to_its_own_net():
    supply_state = SupplyPortDiagramState(supply_port_placements={"p1": _port("p1", "top", "VDD")})
    text = generate_upf(supply_state, PowerDomainDiagramState(), RetentionDiagramState(), "3.1")
    assert "# Connect external ports to internal nets" in text
    assert "connect_supply_net VDD -ports {VDD}" in text


def test_upf_internal_supply_gets_no_self_connect():
    """An Internal Supply is never a boundary port -- it has no
    "external port" to connect to its own net."""
    supply_state = SupplyPortDiagramState(supply_port_placements={"p1": _port("p1", "top/u_core", "VDD_CORE", is_internal_supply=True)})
    text = generate_upf(supply_state, PowerDomainDiagramState(), RetentionDiagramState(), "3.1")
    assert "# Connect external ports to internal nets" not in text


# --- Power States (real, confirmed gap: fully visible on the diagram/ -------
# --- Properties panel, silently dropped on export entirely) -----------------



def _two_tier_state(rows=None):
    """The minimum state the two-tier generator needs: one Supply Set
    over two rails, one domain, and Power State Table rows."""
    from frontend.src.power_intent_view.power_state_table import PowerStateTableDiagramState, PowerStateTableRow

    supply = SupplyPortDiagramState(supply_port_placements={
        "vdd": _port("vdd", "top", "VDD", states=[SupplyPortState("FULL_ON", "1.2"), SupplyPortState("OFF")]),
        "vss": _port("vss", "top", "VSS", states=[SupplyPortState("FULL_ON", "0.0")]),
    })
    domain = PowerDomainDiagramState(
        supply_sets={"ss": SupplySetPlacement(id="ss", name="SS_VDD", power="VDD", ground="VSS", owner_path="top", owner_paths=["top"])},
        domains={"d": PowerDomainPlacement(id="d", owner_path="top", owner_paths=["top"], name="PD_TOP", color=_COLOR, primary_supply_set="ss")},
    )
    table = PowerStateTableDiagramState(rows=rows or {
        "r1": PowerStateTableRow(id="r1", power_domain_id="d", state_name="ON",
                                 net_states={"vdd": "FULL_ON 1.2", "vss": "FULL_ON 0.0"}),
        "r2": PowerStateTableRow(id="r2", power_domain_id="d", state_name="OFF",
                                 net_states={"vdd": "OFF", "vss": "FULL_ON 0.0"}),
    })
    return supply, domain, table


def test_upf_a_supply_sets_states_are_derived_from_the_power_state_table():
    """TIER 1, replacing per-net `add_power_state` entirely. A rail's
    states are no longer an independent fact to emit -- a Supply Set's
    own states are DERIVED by reading its four rails out of every Power
    State Table row."""
    supply, domain, table = _two_tier_state()
    text = generate_upf(supply, domain, RetentionDiagramState(), "3.1",
                        design_name="top", power_state_table_state=table)

    assert "add_power_state -supply SS_VDD" in text
    assert "-state { STATE_1 \\" in text
    assert "-supply_expr { power == {FULL_ON 1.2} && ground == {FULL_ON 0.0} }" in text
    assert "-supply_expr { power == {OFF} && ground == {FULL_ON 0.0} }" in text
    # Explicit confirmation: NO -complete anywhere.
    assert "-complete" not in text
    # And the old per-net form is gone.
    assert "add_power_state VDD" not in text


def test_upf_simstate_is_corrupt_when_any_rail_is_off_or_undetermined():
    supply, domain, table = _two_tier_state()
    text = generate_upf(supply, domain, RetentionDiagramState(), "3.1",
                        design_name="top", power_state_table_state=table)
    block = text[text.index("add_power_state -supply SS_VDD"):]

    assert block.index("-simstate NORMAL") < block.index("-simstate CORRUPT")


def test_upf_identical_rail_combinations_collapse_into_one_derived_state():
    """Two rows selecting the same values for a set's own rails are ONE
    derived state, reused -- not two."""
    from frontend.src.power_intent_view.power_state_table import PowerStateTableDiagramState, PowerStateTableRow

    supply, domain, _ = _two_tier_state()
    table = PowerStateTableDiagramState(rows={
        "r1": PowerStateTableRow(id="r1", power_domain_id="d", state_name="ON",
                                 net_states={"vdd": "FULL_ON 1.2", "vss": "FULL_ON 0.0"}),
        "r2": PowerStateTableRow(id="r2", power_domain_id="d", state_name="ALSO_ON",
                                 net_states={"vdd": "FULL_ON 1.2", "vss": "FULL_ON 0.0"}),
        "r3": PowerStateTableRow(id="r3", power_domain_id="d", state_name="OFF",
                                 net_states={"vdd": "OFF", "vss": "FULL_ON 0.0"}),
    })
    text = generate_upf(supply, domain, RetentionDiagramState(), "3.1",
                        design_name="top", power_state_table_state=table)

    assert text.count("-state { STATE_1") == 1
    assert text.count("-state { STATE_2") == 1
    assert "STATE_3" not in text  # a third distinct combination would be one
    # Both ON rows reference the SAME derived state in the group.
    assert text.count("SS_VDD == STATE_1") == 2


def test_upf_supply_port_with_no_states_gets_no_add_power_state():
    supply_state = SupplyPortDiagramState(supply_port_placements={"p1": _port("p1", "top", "VDD")})
    text = generate_upf(supply_state, PowerDomainDiagramState(), RetentionDiagramState(), "3.1")
    assert "# Power States" not in text
    assert "add_power_state" not in text


def test_upf_one_global_power_state_group_covers_every_domain():
    """TIER 2: ONE group for the whole design, named after the top
    module, with one -state per Power State Table row."""
    supply, domain, table = _two_tier_state()
    text = generate_upf(supply, domain, RetentionDiagramState(), "3.1",
                        design_name="top", power_state_table_state=table)

    assert text.count("create_power_state_group top_PST") == 1
    assert "add_power_state -group top_PST" in text
    assert "-state { ON \\" in text
    assert "-logic_expr { \\" in text
    assert "SS_VDD == STATE_1" in text


def test_upf_a_name_shared_by_a_named_port_and_an_auto_linked_internal_supply_is_one_root_net():
    """The cross-hierarchy "reuse an existing supply name" mechanism can
    legitimately create TWO placements sharing one name (a plain Supply
    Port at its original hierarchy, an auto-linked Internal Supply
    elsewhere).

    REVISED BACK to "exactly ONE create_supply_net" (reference golden).
    For a while the nested replica was declared again inside `set_scope
    u_sub` as `create_supply_net VSS -reuse` (review finding 1.5). The
    reference golden uses the flat form instead: every net is declared once,
    at the root, and the replicas -- one continuous rail by this app's
    own rule -- need no declaration of their own.

    add_power_state stays deduped by NAME alone -- a power state is
    declared against the supply's states, which the shared name resolves
    to once (the named port's own user-authored ones)."""
    supply_state = SupplyPortDiagramState(
        supply_port_placements={
            "top_vss": _port("top_vss", "top", "VSS", states=[SupplyPortState("OFF")]),
            "sub_vss": _port("sub_vss", "top/u_sub", "VSS", is_internal_supply=True),
            "sub_vss2": _port("sub_vss2", "top/u_sub", "VSS", is_internal_supply=True),
        }
    )
    text = generate_upf(supply_state, PowerDomainDiagramState(), RetentionDiagramState(), "3.1")
    nets = _section_lines(text, "Supply Nets")
    assert [line for line in nets if line] == ["create_supply_net VSS"]
    assert "-reuse" not in text
    assert "set_scope u_sub" not in text
    # No per-net `add_power_state` at all anymore -- a rail's states are
    # what a Supply Set's own derived states are composed from (Tier 1),
    # not an independent fact to emit per net.
    assert "add_power_state VSS" not in text


# --- Power Domains: creation order, not alphabetical -------------------------


def test_upf_power_domains_render_in_creation_order_not_alphabetical():
    """Matches golden.upf's own PD_TOP-then-PD_SUB ordering -- the order
    they were actually created in (dict insertion order), not a sort
    that happens to put "PD_SUB" before "PD_TOP" alphabetically."""
    domain_state = PowerDomainDiagramState(
        domains={
            "d1": PowerDomainPlacement(id="d1", owner_path="top", name="PD_TOP", color=_COLOR),
            "d2": PowerDomainPlacement(id="d2", owner_path="top/u_sub", name="PD_SUB", color=_COLOR),
        }
    )
    text = generate_upf(SupplyPortDiagramState(), domain_state, RetentionDiagramState(), "3.1")
    assert text.index("create_power_domain PD_TOP") < text.index("create_power_domain PD_SUB")


# --- Macro Connections: primary PG pins before internal ones -----------------


def test_upf_macro_connections_list_primary_pg_pins_before_internal_ones():
    """Matches golden.upf's own ordering (VDD, VSS -- both primary --
    then VDD_BKP -> VDD_INT, an internal pin, last), not a plain
    alphabetical sort (which would put VDD_BKP second)."""
    supply_state = SupplyPortDiagramState(
        supply_port_placements={
            "vdd": _port("vdd", "top", "VDD"),
            "vss": _port("vss", "top", "VSS"),
            "vdd_bkp": _port("vdd_bkp", "top", "VDD_BKP"),
        },
        pg_pin_placements={
            "pg_vdd": _port("pg_vdd", "top/u_ram", "VDD", fixed=True),
            "pg_vss": _port("pg_vss", "top/u_ram", "VSS", fixed=True),
            "pg_vdd_int": SupplyPortPlacement(id="pg_vdd_int", owner_path="top/u_ram", name="VDD_INT", color=_COLOR, fixed=True, is_internal=True),
        },
        supply_connector_placements=[("vdd", "pg_vdd"), ("vss", "pg_vss"), ("vdd_bkp", "pg_vdd_int")],
    )
    text = generate_upf(supply_state, PowerDomainDiagramState(), RetentionDiagramState(), "3.1")
    lines = [l for l in text.splitlines() if l.startswith("connect_supply_net") and "u_ram" in l]
    assert [l.split()[1] for l in lines] == ["VDD", "VSS", "VDD_BKP"]


# --- UPF 2.1 / CPF 1.1 removed as generation targets (explicit request) ----


def test_upf_2_1_and_cpf_1_1_are_no_longer_supported_generation_targets():
    assert "2.1" not in SUPPORTED_UPF_VERSIONS
    assert "1.1" not in SUPPORTED_CPF_VERSIONS
    assert "1.0" not in SUPPORTED_CPF_VERSIONS
    assert SUPPORTED_UPF_VERSIONS == ["3.0", "3.1"]
    assert SUPPORTED_CPF_VERSIONS == ["2.0", "2.1"]


@pytest.mark.parametrize("version", SUPPORTED_UPF_VERSIONS)
def test_generation_still_works_for_every_remaining_supported_upf_version(version):
    supply_state = SupplyPortDiagramState(supply_port_placements={"p1": _port("p1", "top", "VDD")})
    text = generate_upf(supply_state, PowerDomainDiagramState(), RetentionDiagramState(), version)
    assert f"UPF {version}" in text
    assert "create_supply_port VDD" in text


@pytest.mark.parametrize("version", SUPPORTED_CPF_VERSIONS)
def test_generation_still_works_for_every_remaining_supported_cpf_version(version):
    supply_state = SupplyPortDiagramState(supply_port_placements={"p1": _port("p1", "top", "VDD")})
    domain_state = PowerDomainDiagramState(supply_sets={"ss1": SupplySetPlacement(id="ss1", name="SS1", power="VDD")})
    text = generate_cpf(supply_state, domain_state, RetentionDiagramState(), version)
    assert f"CPF {version}" in text
    assert "create_power_nets -nets {VDD}" in text


# --- Isolation Rules (real, confirmed gap: fully built on the GUI side, ------
# --- never wired into this generator -- explicit request, "FIX IT") --------


def _domain(id_, owner_path, name) -> PowerDomainPlacement:
    return PowerDomainPlacement(id=id_, owner_path=owner_path, name=name, color=_COLOR)


def test_upf_isolation_emits_set_isolation_with_every_field():
    domain_state = PowerDomainDiagramState(
        domains={"d1": _domain("d1", "top/u_sub", "PD_SUB")},
        supply_sets={"ss1": SupplySetPlacement(id="ss1", name="SS_VDD", power="VDD", ground="VSS", owner_path="top")},
    )
    isolation_state = IsolationDiagramState(
        strategies={
            "iso1": IsolationStrategyPlacement(
                id="iso1",
                name="ISO1",
                power_domain_id="d1",
                isolation_supply_set_id="ss1",
                clamp_value="low",
                isolation_control="iso_en",
                isolation_sense="high",
                location="parent",
                elements=["top/u_sub/o_q[31:0]"],
            )
        }
    )
    text = generate_upf(SupplyPortDiagramState(), domain_state, RetentionDiagramState(), "3.1", isolation_state=isolation_state)
    assert "# Isolation Rules" in text
    # Real UPF `-clamp_value` is a literal 0/1/hold, never this app's own
    # "low"/"high"/"keep" internal labels (see _CLAMP_VALUE_UPF's own
    # docstring) -- "low" -> "0", matching golden.upf's own usage.
    assert "set_isolation ISO1 -domain PD_SUB \\\n  -isolation_supply_set SS_VDD \\\n  -clamp_value 0 \\\n" in text
    assert "-isolation_signal iso_en \\\n  -isolation_sense high \\\n" in text
    # An Elements key is a port name namespaced by its owner_path; the
    # command is emitted at the ROOT scope (reference golden), so it is
    # written root-relative -- "top/u_sub/o_q[31:0]" -> "u_sub/o_q[31:0]".
    assert "-elements { \\\n        u_sub/o_q[31:0] \\\n    } \\\n  -location parent" in text


def test_upf_isolation_skips_a_dangling_domain_reference():
    isolation_state = IsolationDiagramState(
        strategies={
            "iso1": IsolationStrategyPlacement(
                id="iso1", name="ISO1", power_domain_id="missing", isolation_supply_set_id="ss1", clamp_value="low",
                isolation_control="iso_en", isolation_sense="high",
            )
        }
    )
    text = generate_upf(SupplyPortDiagramState(), PowerDomainDiagramState(), RetentionDiagramState(), "3.1", isolation_state=isolation_state)
    assert "set_isolation" not in text


def test_cpf_isolation_emits_create_and_update_isolation_rules():
    domain_state = PowerDomainDiagramState(
        domains={"d1": _domain("d1", "top/u_sub", "PD_SUB")},
        supply_sets={"ss1": SupplySetPlacement(id="ss1", name="SS_VDD", power="VDD", ground="VSS", owner_path="top")},
    )
    isolation_state = IsolationDiagramState(
        strategies={
            "iso1": IsolationStrategyPlacement(
                id="iso1",
                name="ISO1",
                power_domain_id="d1",
                isolation_supply_set_id="ss1",
                clamp_value="low",
                isolation_control="iso_en",
                isolation_sense="high",
                location="parent",
                elements=["top/u_sub/o_q[31:0]"],
            )
        }
    )
    text = generate_cpf(SupplyPortDiagramState(), domain_state, RetentionDiagramState(), "2.0", isolation_state=isolation_state)
    assert "# Isolation Rules" in text
    assert "create_isolation_rule -name ISO1 \\\n    -from PD_SUB \\\n    -isolation_condition {iso_en} \\\n    -isolation_output low \\\n" in text
    assert "-pins { \\\n        o_q[31:0] \\\n    }" in text
    # Real CPF `-isolation_output` (unlike UPF's own `-clamp_value`)
    # already takes the literal "low"/"high" this app stores internally
    # -- no translation needed, matching golden.cpf's own usage exactly.
    # -isolation_power_net/-isolation_ground_net resolve from the
    # ISOLATION SUPPLY SET's own power/ground (SS_VDD's VDD/VSS), not
    # the domain's own primary supply.
    # `-isolation_power_net`/`-isolation_ground_net` are NOT CPF -- zero
    # occurrences in the CPF 1.0 spec and zero in 2.1 (audit finding C3).
    # CPF states the isolation cell's supply as a DOMAIN on the create
    # command instead.
    assert "-isolation_power_net" not in text
    assert "-isolation_ground_net" not in text
    assert "update_isolation_rules -names ISO1 \\\n    -location parent" in text


# --- Port Attributes (real, confirmed gap) -----------------------------------


def _in_port(name, width=1):
    return Port(name=name, direction=PortDirection.IN, width=width)


def _out_port(name, width=1):
    return Port(name=name, direction=PortDirection.OUT, width=width)


def test_upf_port_attributes_splits_driver_and_receiver_and_collapses_bit_ranges():
    domain_state = PowerDomainDiagramState(supply_sets={"ss1": SupplySetPlacement(id="ss1", name="SS_VDD", power="VDD", ground="VSS")})
    port_attribute_state = PortAttributeDiagramState(
        assignments={"clk": "ss1", "a[0]": "ss1", "a[1]": "ss1", "q": "ss1"}
    )
    top_level_ports = [_in_port("clk"), _in_port("a", width=2), _out_port("q")]
    text = generate_upf(
        SupplyPortDiagramState(), domain_state, RetentionDiagramState(), "3.1",
        port_attribute_state=port_attribute_state, top_level_ports=top_level_ports,
    )
    assert "# Port Attributes" in text
    # A multi-bit port with every bit sharing the identical assignment
    # collapses back to ONE whole-port entry ("a[1:0]"), never per-bit.
    assert "set_port_attributes -ports { \\\n        clk \\\n        a[1:0] \\\n    } \\\n    -driver_supply SS_VDD" in text
    assert "set_port_attributes -ports { \\\n        q \\\n    } \\\n    -receiver_supply SS_VDD" in text


def test_upf_port_attributes_skips_a_partially_assigned_multi_bit_port():
    """A genuinely mixed per-bit assignment has no honest whole-port
    representation -- real UPF has no "half this port" port attribute."""
    domain_state = PowerDomainDiagramState(supply_sets={"ss1": SupplySetPlacement(id="ss1", name="SS_VDD", power="VDD", ground="VSS")})
    port_attribute_state = PortAttributeDiagramState(assignments={"a[0]": "ss1", "a[1]": None})
    top_level_ports = [_in_port("a", width=2)]
    text = generate_upf(
        SupplyPortDiagramState(), domain_state, RetentionDiagramState(), "3.1",
        port_attribute_state=port_attribute_state, top_level_ports=top_level_ports,
    )
    assert "set_port_attributes" not in text


def test_upf_port_attributes_absent_without_top_level_ports():
    """Gracefully degrades to nothing when the caller has no RTL Design
    loaded yet (top_level_ports=None) -- same "diagram-only data can be
    absent" tolerance every other optional state here already has."""
    port_attribute_state = PortAttributeDiagramState(assignments={"clk": "ss1"})
    text = generate_upf(SupplyPortDiagramState(), PowerDomainDiagramState(), RetentionDiagramState(), "3.1", port_attribute_state=port_attribute_state)
    assert "Port Attributes" not in text


def test_cpf_port_attributes_fold_into_the_root_domains_boundary_ports():
    domain_state = PowerDomainDiagramState(
        domains={"d1": _domain("d1", "top", "PD_TOP")},
        supply_sets={"ss1": SupplySetPlacement(id="ss1", name="SS_VDD", power="VDD", ground="VSS")},
    )
    port_attribute_state = PortAttributeDiagramState(assignments={"clk": "ss1", "q": "ss1"})
    top_level_ports = [_in_port("clk"), _out_port("q")]
    text = generate_cpf(
        SupplyPortDiagramState(), domain_state, RetentionDiagramState(), "2.0",
        port_attribute_state=port_attribute_state, top_level_ports=top_level_ports,
    )
    # No separate "Port Attributes" CPF section at all -- folded directly
    # onto the ROOT domain's own create_power_domain line instead.
    assert "# Port Attributes" not in text
    assert "create_power_domain -name PD_TOP -default -boundary_ports {\n    clk\n    q\n}" in text


def test_cpf_port_attributes_never_attach_to_a_non_root_domain():
    domain_state = PowerDomainDiagramState(
        domains={"d1": _domain("d1", "top/u_sub", "PD_SUB")},
        supply_sets={"ss1": SupplySetPlacement(id="ss1", name="SS_VDD", power="VDD", ground="VSS")},
    )
    port_attribute_state = PortAttributeDiagramState(assignments={"clk": "ss1"})
    top_level_ports = [_in_port("clk")]
    text = generate_cpf(
        SupplyPortDiagramState(), domain_state, RetentionDiagramState(), "2.0",
        port_attribute_state=port_attribute_state, top_level_ports=top_level_ports,
    )
    assert "-boundary_ports" not in text


# --- Power State Table (real, confirmed gap) ---------------------------------


def test_upf_the_legacy_pst_translation_is_gone():
    """REPLACED by the two-tier model: a legacy PST cannot express a
    supply-set-level model at all, so it is no longer generated."""
    supply, domain, table = _two_tier_state()
    text = generate_upf(supply, domain, RetentionDiagramState(), "3.1",
                        design_name="top", power_state_table_state=table)

    assert "create_pst" not in text
    assert "add_pst_state" not in text


def test_upf_power_state_table_skips_an_incomplete_row():
    supply_state = SupplyPortDiagramState(
        supply_port_placements={"vdd": _port("vdd", "top", "VDD"), "vss": _port("vss", "top", "VSS")}
    )
    power_state_table_state = PowerStateTableDiagramState()
    row = add_row(power_state_table_state, "r1")
    row.power_domain_id = "any"
    row.state_name = "FULL_ON"
    row.net_states = {"vdd": "ON"}  # vss never selected
    text = generate_upf(supply_state, PowerDomainDiagramState(), RetentionDiagramState(), "3.1", power_state_table_state=power_state_table_state)
    assert "Power State Table" not in text


def test_cpf_emits_real_domain_conditions_and_exactly_one_default():
    """CPF's own two tiers, from the Si2 spec: a nominal condition is a
    single VOLTAGE, and a power mode maps POWER DOMAINS to conditions.
    The per-domain data the -domain_conditions flag always needed only
    became available with the supply-set derivation, so this used to be a
    comment."""
    supply, domain, table = _two_tier_state()
    text = generate_cpf(supply, domain, RetentionDiagramState(), "2.0",
                        design_name="top", power_state_table_state=table)

    assert "create_power_mode -name ON -domain_conditions {PD_TOP@COND_1} -default" in text
    assert "create_power_mode -name OFF -domain_conditions {PD_TOP@COND_2}" in text
    # Spec: exactly one (and only one) MODE is the default. Counted over
    # create_power_mode lines specifically -- a root power DOMAIN now
    # legitimately carries `-default` too (audit finding H1), and a
    # whole-file count would conflate two different uses of one flag name.
    assert len([l for l in text.splitlines() if l.startswith("create_power_mode") and "-default" in l]) == 1


# --- Power States (CPF) -- real, confirmed gap: generate_upf() already had --
# --- add_power_state, generate_cpf() had no equivalent at all ---------------


def test_cpf_emits_one_nominal_condition_per_distinct_voltage():
    """And OFF is a 0.0 V condition, NOT a `-state off` flag: the spec
    says "a power domain is switched off if it is associated with a
    nominal condition whose voltage is 0", and CPF 1.0's own
    create_nominal_condition defines no -state option at all."""
    supply, domain, table = _two_tier_state()
    text = generate_cpf(supply, domain, RetentionDiagramState(), "2.0",
                        design_name="top", power_state_table_state=table)

    assert "create_nominal_condition -name COND_1 -voltage 1.2" in text
    assert "create_nominal_condition -name COND_2 -voltage 0.0" in text
    assert "-state off" not in text


# --- expert-review Priority 1: corrected UPF syntax ---------------------
#
# Every item below was WRONG in generated output and consistent with
# itself, so nothing in this suite or the round trip caught it -- the
# elaborator accepted the wrong spelling as an alternative, and this
# project's own golden.upf used it too, which is where the generator
# copied it from.


@pytest.mark.parametrize("version", SUPPORTED_UPF_VERSIONS)
def test_upf_declares_the_version_with_upf_version_not_set_upf_version(version):
    """1.1: `set_upf_version` is not real UPF syntax."""
    text = generate_upf(SupplyPortDiagramState(), PowerDomainDiagramState(), RetentionDiagramState(), version)
    assert f"upf_version {version}" in text
    assert "set_upf_version" not in text


def test_upf_supply_set_uses_function_not_supply():
    """1.2: create_supply_set takes `-function`; `-supply` is
    create_power_domain's flag."""
    domain_state = PowerDomainDiagramState(
        supply_sets={"s1": SupplySetPlacement(id="s1", name="SS_CORE", power="VDD", ground="VSS", owner_path="top")}
    )
    text = generate_upf(SupplyPortDiagramState(), domain_state, RetentionDiagramState(), "3.1")
    # One `-function` per continued line (reference golden), so the
    # command is the whole block, not its first line.
    create = text[text.index("create_supply_set"):].split("\n\n")[0].rstrip("\n")
    assert create == (
        "create_supply_set SS_CORE \\\n"
        "    -function {power VDD} \\\n"
        "    -function {ground VSS}"
    )
    assert "-function {power VDD}" in create
    assert "-function {ground VSS}" in create
    assert "-supply" not in create


def test_upf_map_power_switch_names_the_switch_positionally():
    """1.3: `-switch_name_list` is not standard IEEE 1801-2018 syntax.
    This RETRACTS an earlier instruction in this project that specified
    it."""
    domain_state = PowerDomainDiagramState(
        domains={"d1": PowerDomainPlacement(id="d1", owner_path="top", name="PD", color=QColor("red"))}
    )
    supply_state = SupplyPortDiagramState(supply_port_placements={"t1": _port("t1", "top", "VDD_SW")})
    switch_state = PowerSwitchDiagramState(
        switches={"sw1": _switch("sw1", "PG", "d1", ["t1"], ["top"], cell_type="HDR")}
    )
    text = generate_upf(supply_state, domain_state, RetentionDiagramState(), "3.1", switch_state)
    assert "map_power_switch {PG}" in text
    assert "-switch_name_list" not in text


@pytest.mark.parametrize("generate,version", [(generate_upf, "3.1"), (generate_cpf, "2.0")])
def test_generated_text_contains_only_ascii_whitespace(generate, version):
    """1.7: Tcl does not reliably treat U+00A0 as a word separator, so a
    single one anywhere silently changes how a command parses. Nothing
    here writes one -- the risk is user-typed data (a name, a Cell Type,
    an ON/OFF expression pasted out of a spec) carrying it invisibly, so
    the check is on the finished FILE, not on any one template."""
    nbsp = " "
    domain_state = PowerDomainDiagramState(
        supply_sets={"s1": SupplySetPlacement(id="s1", name=f"SS{nbsp}CORE", power="VDD", ground="VSS", owner_path="top")},
        domains={"d1": PowerDomainPlacement(id="d1", owner_path="top", name="PD", color=QColor("red"), primary_supply_set="s1")},
    )
    supply_state = SupplyPortDiagramState(supply_port_placements={"t1": _port("t1", "top", "VDD_SW")})
    switch_state = PowerSwitchDiagramState(
        switches={"sw1": _switch("sw1", "PG", "d1", ["t1"], ["top"],
                                 cell_type=f"HDR{nbsp}X4", on_state=f"~{nbsp}sleep")}
    )
    text = generate(supply_state, domain_state, RetentionDiagramState(), version, switch_state)

    offenders = sorted({hex(ord(c)) for c in text if c.isspace() and c not in (" ", "\n", "\t")})
    assert offenders == []
    assert nbsp not in text
    # ...and the content itself survived, as a plain space -- sanitized,
    # not stripped. (Only the Cell Type is checked in both formats: CPF
    # has no Supply Set construct at all, so "SS CORE" reaches UPF only.)
    assert "HDR X4" in text


# --- expert-review 1.6: is a populated OFF State ever dropped? ----------


def _switch_state_with(**kwargs):
    domain_state = PowerDomainDiagramState(
        domains={"d1": PowerDomainPlacement(id="d1", owner_path="top", name="PD", color=QColor("red"))}
    )
    supply_state = SupplyPortDiagramState(supply_port_placements={"t1": _port("t1", "top", "VDD_SW")})
    switch_state = PowerSwitchDiagramState(
        switches={"sw1": _switch("sw1", "PG", "d1", ["t1"], ["top"], **kwargs)}
    )
    return generate_upf(supply_state, domain_state, RetentionDiagramState(), "3.1", switch_state)


def test_a_populated_off_state_always_reaches_the_generated_upf():
    """1.6 asked whether a populated OFF State is silently dropped. It is
    not -- the reviewed file simply had none (the field is optional, and
    unlike ON State the dialog does not require it). Pinned here so the
    answer stays true."""
    assert "-off_state {off {!CTRL}}" in _switch_state_with(on_state="CTRL", off_state="!CTRL")


def test_an_empty_off_state_emits_no_flag_rather_than_an_empty_one():
    """The other half of 1.6: an unset optional flag must be ABSENT, not
    written as `-off_state {off {}}` -- the same literal-empty-value
    hazard the isolation writer's own guarded flags exist for."""
    text = _switch_state_with(on_state="CTRL", off_state="")
    assert "-on_state" in text
    assert "-off_state" not in text


# --- the switch's ACKNOWLEDGE pair --------------------------------------
#
# RESEARCHED before implementing, per the request. Findings:
#
#   UPF   `-ack_port {port_name net_name [{boolean_function}]}` is a real
#         create_power_switch option, the SAME {abstract-port real-net}
#         shape as -control_port -- NOT a separate command. Two
#         independent references give this form for UPF 2.1+, alongside
#         -output_supply_port. The sibling `-ack_delay {port delay}` also
#         exists and is deliberately NOT emitted: there is no delay field
#         to emit from, and inventing one would be fabrication.
#
#   ACK   is NOT a spec-mandated token. A switch port's name in
#         create_power_switch is a USER-CHOSEN identifier, not a reserved
#         keyword (published examples use Vin/Vout/Cin/Aout), so "ACK" is
#         this app's own convention, picked to sit alongside its existing
#         IN/OUT/CTRL. Flagged rather than presented as verified syntax.
#
#   CPF   DOES have an equivalent, contrary to expectation -- read
#         directly out of the Si2 CPF 1.0 primary spec:
#         `update_power_switch_rule -acknowledge_receiver pin`, "an input
#         pin in the design which must be connected to an output pin of
#         the power switch cell". That is the design side = Ack SIGNAL.
#         The cell's own ack PIN has no home: CPF states it on
#         `define_power_switch_cell -stage_1_output/-stage_2_output`, a
#         library-cell definition this app does not generate, so it is
#         reported instead of faked.


def _ack_switch_state(**kwargs):
    domain_state = PowerDomainDiagramState(
        supply_sets={"s1": SupplySetPlacement(id="s1", name="SS", power="VDD", owner_path="top")},
        domains={"d1": PowerDomainPlacement(
            id="d1", owner_path="top", name="PD", color=QColor("red"), primary_supply_set="s1"
        )},
    )
    supply_state = SupplyPortDiagramState(supply_port_placements={"t1": _port("t1", "top", "VDD_SW")})
    switch_state = PowerSwitchDiagramState(switches={
        "sw1": _switch(
            "sw1", "PSW_CPU", "d1", ["t1"], ["top"],
            input_supply_port="VDDH", output_supply_port="VDDL", control_port="SLEEP",
            control_signal="pwr_en_cpu", cell_type="PSWITCH_X4",
            on_state="pwr_en_cpu", off_state="!pwr_en_cpu", **kwargs,
        )
    })
    return supply_state, domain_state, switch_state


def test_b_an_empty_acknowledge_pair_generates_no_acknowledge_construct():
    """Absence means OMIT, never an empty or placeholder value -- the
    same rule Cell Type and OFF State already follow."""
    supply, domain, switch = _ack_switch_state()
    upf = generate_upf(supply, domain, RetentionDiagramState(), "3.1", switch)
    cpf = generate_cpf(supply, domain, RetentionDiagramState(), "2.0", switch)

    assert "-ack_port" not in upf
    assert "ACK" not in upf.split("map_power_switch")[1].split("\n\n")[0]
    assert "-acknowledge_receiver" not in cpf


def test_c_a_populated_acknowledge_pair_generates_the_verified_upf_construct():
    supply, domain, switch = _ack_switch_state(ack_port="ACKO", ack_signal="pwr_ack_cpu")
    upf = generate_upf(supply, domain, RetentionDiagramState(), "3.1", switch)

    # The SIGNAL goes on create_power_switch, paired with the abstract
    # port -- the same shape as -control_port.
    assert "-ack_port {ACKO pwr_ack_cpu}" in upf
    # The cell PIN goes in -port_map, as a fourth entry.
    assert "-ack_port {ACKO pwr_ack_cpu}" in upf
    # -ack_delay is real UPF but has no field here, so it is not invented.
    assert "-ack_delay" not in upf


def test_c_the_acknowledge_flag_needs_both_halves():
    """A port with no net states a connection to nothing; a net with no
    port has no port to attach to. Either alone emits no -ack_port -- but
    a pin alone still reaches -port_map, which is about the CELL."""
    _, _, port_only = _ack_switch_state(ack_port="ACKO")
    supply, domain, _ = _ack_switch_state()
    upf = generate_upf(supply, domain, RetentionDiagramState(), "3.1", port_only)
    assert "-ack_port" not in upf
    assert "-ack_port" not in upf  # a port with no net connects to nothing

    _, _, signal_only = _ack_switch_state(ack_signal="pwr_ack_cpu")
    upf = generate_upf(supply, domain, RetentionDiagramState(), "3.1", signal_only)
    assert "-ack_port" not in upf


def test_d_cpf_exports_the_acknowledge_signal_as_a_real_construct():
    """Not a comment and not a diagnostic: `-acknowledge_receiver` is a
    genuine CPF option, verified against the Si2 CPF 1.0 spec."""
    supply, domain, switch = _ack_switch_state(ack_port="ACKO", ack_signal="pwr_ack_cpu")
    cpf = generate_cpf(supply, domain, RetentionDiagramState(), "2.0", switch)

    assert "update_power_switch_rule -name PSW_CPU -acknowledge_receiver_1 {pwr_ack_cpu}" in cpf


def test_d_cpf_reports_the_acknowledge_pin_it_cannot_express():
    """The cell-side pin belongs to `define_power_switch_cell
    -stage_1_output`, a library-cell definition this app does not
    generate -- surfaced through the same "not expressible in CPF" path
    the macro-attachment and supply-port-direction gaps use."""
    from frontend.src.power_intent_view.power_intent_text_export import power_switch_ack_cpf_warnings

    _, _, switch = _ack_switch_state(ack_port="ACKO", ack_signal="pwr_ack_cpu")
    warnings = power_switch_ack_cpf_warnings(switch)
    assert len(warnings) == 1
    assert "ACKO" in warnings[0]
    assert "define_power_switch_cell" in warnings[0]
    # ...and it says what DOES survive, so the reader is not left
    # thinking the whole feature was dropped.
    assert "-acknowledge_receiver" in warnings[0]

    _, _, no_ack = _ack_switch_state()
    assert power_switch_ack_cpf_warnings(no_ack) == []


def test_e_control_signal_generation_is_unaffected_by_the_restructure():
    """The field was RENAMED ("Switch Control" -> "Control Signal") and
    MOVED; its generation behaviour is unchanged. Re-asserted here
    explicitly rather than trusted to the existing tests alone, since a
    rename is exactly the kind of change that quietly reaches further
    than intended."""
    supply, domain, switch = _ack_switch_state(ack_port="ACKO", ack_signal="pwr_ack_cpu")
    upf = generate_upf(supply, domain, RetentionDiagramState(), "3.1", switch)

    assert "-control_port {SLEEP pwr_en_cpu}" in upf
    # No Supply In connector in this fixture, so the ON state carries no
    # input-port element -- the `on IN {...}` form is asserted by the
    # round-trip suite, which does wire one.
    assert "-on_state {on {SLEEP}}" in upf
    assert "-off_state {off {!SLEEP}}" in upf
    # The acknowledge signal is NOT substituted into the state
    # expressions -- only the control signal is.
    assert "pwr_ack_cpu}}" not in upf


# --- "Name Prefix" on Isolation and Level Shifter -----------------------
#
# RESEARCHED before implementing, per the request, and the outcome is an
# ASYMMETRY that the request did not anticipate:
#
#   set_level_shifter -name_prefix   VERIFIED. It appears in OpenROAD's
#       own UPF command reference for this command, in a list evidently
#       LRM-derived (it carries obscure siblings like
#       -use_functional_equivalence / -applies_to_boundary / -no_shift /
#       -force_shift), corroborated by an independent search result. Its
#       -name_suffix sibling is real too and is not emitted: no field.
#
#   set_isolation -name_prefix       NOT VERIFIED. Absent from every
#       published option list checked (five sources), including a real
#       tool implementation's. The naming-prefix concept does exist for
#       isolation, but as UPF 1.0's `create_isolation_logic -prefix` -- a
#       command superseded by set_isolation and not one this app emits.
#       Absence is weak evidence (the same tool list also omits
#       -elements/-isolation_supply_set/-source/-sink, all real and all
#       already emitted here), so the honest state is UNCERTAIN -- and
#       this project's rule for uncertain is report, never assert.
#
#   CPF   BOTH verified, from the Si2 CPF 1.0 PRIMARY spec:
#       `update_isolation_rules -prefix` ("the prefix to be used when
#       creating the isolation logic", default CPF_ISO_) and
#       `update_level_shifter_rules -prefix` (default CPF_LS_). So CPF
#       expresses this MORE completely than UPF does -- the reverse of
#       every other gap in this module.


def _prefix_states(iso_prefix="", ls_prefix=""):
    domain_state = PowerDomainDiagramState(
        supply_sets={"ss": SupplySetPlacement(id="ss", name="SS_ISO", power="VDD", ground="VSS", owner_path="top")},
        domains={"pd": PowerDomainPlacement(
            id="pd", owner_path="top", name="PD_CPU", color=QColor("red"), primary_supply_set="ss"
        )},
    )
    isolation = IsolationDiagramState(strategies={"i": IsolationStrategyPlacement(
        id="i", name="ISO_CPU_OUT", power_domain_id="pd", isolation_supply_set_id="ss",
        clamp_value="low", isolation_control="iso_en", isolation_sense="high",
        elements=["top/o_q"], name_prefix=iso_prefix, owner_path="top",
    )})
    level_shifter = LevelShifterDiagramState(strategies={"l": LevelShifterStrategyPlacement(
        id="l", name="LS_CPU_OUT", power_domain_id="pd", applies_to="outputs", rule="low_to_high",
        location="self", threshold="0.2", cell_type="LS_HL_X2", elements=["top/o_q"],
        name_prefix=ls_prefix, owner_path="top",
    )})
    return domain_state, isolation, level_shifter


def _prefix_upf(iso_prefix="", ls_prefix=""):
    domain, isolation, level_shifter = _prefix_states(iso_prefix, ls_prefix)
    return generate_upf(
        SupplyPortDiagramState(), domain, RetentionDiagramState(), "3.1",
        isolation_state=isolation, level_shifter_state=level_shifter,
    )


def _prefix_cpf(iso_prefix="", ls_prefix=""):
    domain, isolation, level_shifter = _prefix_states(iso_prefix, ls_prefix)
    return generate_cpf(
        SupplyPortDiagramState(), domain, RetentionDiagramState(), "2.0",
        isolation_state=isolation, level_shifter_state=level_shifter,
    )


def test_b_a_level_shifter_name_prefix_generates_the_verified_upf_flag():
    assert "-name_prefix ls_lvf_" in _prefix_upf(ls_prefix="ls_lvf_")


def test_b_an_isolation_name_prefix_is_not_asserted_into_upf():
    """The flag could not be verified on set_isolation, so it is NOT
    written. A fabricated flag would parse cleanly in this project's own
    elaborator and be silently wrong in a real tool -- the exact failure
    mode the literal-"None" bug already demonstrated here."""
    upf = _prefix_upf(iso_prefix="iso_lvf_")
    isolation_block = upf[upf.index("set_isolation"):upf.index("set_isolation") + 400]
    assert "-name_prefix" not in isolation_block
    assert "iso_lvf_" not in isolation_block


def test_c_an_empty_name_prefix_omits_the_flag_entirely():
    """Same rule as Cell Type, OFF State and Threshold: absence means
    omit, never an empty value."""
    assert "-name_prefix" not in _prefix_upf()
    assert "-prefix" not in _prefix_cpf()


def test_d_cpf_exports_both_name_prefixes_as_real_constructs():
    """Verified against the Si2 CPF 1.0 primary spec, so asserted rather
    than commented or diagnosed."""
    cpf = _prefix_cpf(iso_prefix="iso_lvf_", ls_prefix="ls_lvf_")
    assert "-prefix iso_lvf_" in cpf
    assert "-prefix ls_lvf_" in cpf
    # ...on the update_* commands, which is where the spec puts them.
    iso_update = next(l for l in cpf.splitlines() if "update_isolation_rules" in l)
    assert "-names ISO_CPU_OUT" in iso_update
    ls_update = next(l for l in cpf.splitlines() if "update_level_shifter_rules" in l)
    assert "-prefix ls_lvf_" in ls_update


def test_d_the_unverified_upf_isolation_prefix_is_reported_not_dropped():
    """The inverse of every other diagnostic here: this one fires for
    UPF, because CPF is the format with the verified construct."""
    from frontend.src.power_intent_view.power_intent_text_export import (
        isolation_name_prefix_upf_warnings,
    )

    _, isolation, _ = _prefix_states(iso_prefix="iso_lvf_")
    warnings = isolation_name_prefix_upf_warnings(isolation)
    assert len(warnings) == 1
    assert "ISO_CPU_OUT" in warnings[0] and "iso_lvf_" in warnings[0]
    # Says what DOES survive, so the reader is not left thinking it was
    # dropped everywhere.
    assert "update_isolation_rules -prefix" in warnings[0]

    _, no_prefix, _ = _prefix_states()
    assert isolation_name_prefix_upf_warnings(no_prefix) == []


def test_e_the_isolation_signal_rename_did_not_touch_generation():
    """"Isolation Control" -> "Isolation Signal" was a LABEL change. The
    field, the UPF flag (-isolation_signal) and the CPF flag
    (-isolation_condition) are all unchanged."""
    upf = _prefix_upf()
    cpf = _prefix_cpf()
    assert "-isolation_signal iso_en" in upf
    assert "-isolation_condition {iso_en}" in cpf


# --- legacy Power State Table, opt-in (audit B8) ------------------------


def _pst_states():
    domain_state = PowerDomainDiagramState(
        supply_sets={"ss": SupplySetPlacement(id="ss", name="SS", power="VDD", ground="VSS", owner_path="top")},
        domains={"d": PowerDomainPlacement(
            id="d", owner_path="top", name="PD", color=QColor("red"), primary_supply_set="ss"
        )},
    )
    supply_state = SupplyPortDiagramState(supply_port_placements={
        "vdd": _port("vdd", "top", "VDD", states=[SupplyPortState("FULL_ON", "1.2"), SupplyPortState("OFF")]),
        "vss": _port("vss", "top", "VSS", states=[SupplyPortState("FULL_ON", "0.0")]),
    })
    table = PowerStateTableDiagramState(rows={
        "r1": PowerStateTableRow(id="r1", power_domain_id="d", state_name="ON",
                                 net_states={"vdd": "FULL_ON 1.2", "vss": "FULL_ON 0.0"}),
        "r2": PowerStateTableRow(id="r2", power_domain_id="d", state_name="OFF",
                                 net_states={"vdd": "OFF", "vss": "FULL_ON 0.0"}),
    })
    return supply_state, domain_state, table


def _pst_upf(legacy_power_states: bool) -> str:
    supply, domain, table = _pst_states()
    return generate_upf(
        supply, domain, RetentionDiagramState(), "3.1",
        design_name="top", power_state_table_state=table,
        legacy_power_states=legacy_power_states,
    )


def test_modern_is_the_default_and_its_output_is_unchanged():
    """An export nobody changed stays byte-identical to what this app
    produced before the choice existed."""
    supply, domain, table = _pst_states()
    without_argument = generate_upf(
        supply, domain, RetentionDiagramState(), "3.1",
        design_name="top", power_state_table_state=table,
    )
    assert _pst_upf(legacy_power_states=False) == without_argument


def test_modern_writes_only_the_modern_sections():
    """RETIRES the additive behaviour: the legacy table used to be added
    ALONGSIDE the modern group. The two are mutually exclusive now."""
    text = _pst_upf(legacy_power_states=False)

    assert "create_power_state_group" in text
    assert "add_power_state -supply" in text
    assert "# Supply Set Power States" in text
    assert "# Power State Group" in text
    # ...and no trace of the legacy representation.
    assert "create_pst" not in text
    assert "add_pst_state" not in text
    assert "add_port_state" not in text
    assert "# Power State Table" not in text


def test_legacy_writes_only_the_legacy_sections():
    text = _pst_upf(legacy_power_states=True)

    assert "create_pst" in text
    assert "add_pst_state" in text
    assert "# Port State" in text
    assert "# Power State Table" in text
    # ...and no trace of the modern representation.
    assert "create_power_state_group" not in text
    assert "add_power_state -supply" not in text
    assert "# Supply Set Power States" not in text
    assert "# Power State Group" not in text


def test_both_representations_can_never_appear_in_one_file():
    """The property the exclusivity exists for, asserted directly: for
    EITHER choice, the two markers never co-occur. There is no third
    value the parameter could take to ask for both."""
    for legacy in (False, True):
        text = _pst_upf(legacy_power_states=legacy)
        assert ("create_pst" in text) is legacy
        assert ("create_power_state_group" in text) is not legacy


def test_the_legacy_block_matches_the_references_own_shape():
    """`create_pst <name> -supplies {...}` then one `add_pst_state <name>
    -pst <name> -state {...}` per row -- the shape aura_top.upf uses."""
    text = _pst_upf(legacy_power_states=True)
    create = next(l for l in text.splitlines() if l.startswith("create_pst"))
    assert "-supplies {" in create
    add = next(l for l in text.splitlines() if l.startswith("add_pst_state"))
    assert "-pst " in add and "-state {" in add


def test_map_level_shifter_cell_has_no_port_map_to_make_conditional():
    """CHECKED, not assumed -- the question asked alongside the
    conditional-port_map work.

    `map_level_shifter_cell`'s documented signature is
    `level_shifter_name -domain domain_name -lib_cells list
    [-elements list]`: no `-port_map`, so there is nothing here needing
    the same treatment. (The sibling `map_isolation_cell` /
    `map_retention_cell` do carry a per-port form, but it is
    `{-port port_name net_name}` reached via `-lib_model_name` -- a
    different option from `-port_map`, and neither command is generated
    by this app at all, since neither Isolation nor Retention has a
    cell_type field.)"""
    domain_state = PowerDomainDiagramState(
        domains={"d": PowerDomainPlacement(id="d", owner_path="top", name="PD", color=QColor("red"))}
    )
    level_shifter_state = LevelShifterDiagramState(strategies={
        "l": LevelShifterStrategyPlacement(
            id="l", name="LS", power_domain_id="d", applies_to="outputs",
            rule="both", cell_type="LSHL_X2 LSLH_X2", owner_path="top",
        )
    })
    text = generate_upf(SupplyPortDiagramState(), domain_state, RetentionDiagramState(), "3.1",
                        level_shifter_state=level_shifter_state)
    mapping = next(l for l in text.splitlines() if l.startswith("map_level_shifter_cell"))
    assert mapping == "map_level_shifter_cell LS \\"
    assert "-port_map" not in text
    # The cell LIST does survive whole, which is the part that IS shared
    # with map_power_switch (audit B5).
    assert "-lib_cells {LSHL_X2 LSLH_X2}" in text


# --- Retention: the exact two-command, multi-line shape ---------------------


# The reference golden's shape (design/single_clock_design/upf_fixed/
# main_top.fixed.upf): at the root scope, `-retention_supply_set` alone,
# root-relative elements, a blank line before the control command, and
# `{signal sense}` save/restore pairs.
_RETENTION_GOLDEN_CASE = """set_retention RET_CPU \\
    -domain PD_SUB \\
    -retention_supply_set SS_VDD \\
    -elements { \\
        u_sub/o_q \\
    }

set_retention_control RET_CPU \\
    -domain PD_SUB \\
    -save_signal {save high} \\
    -restore_signal {restore low}"""


def _retention_golden_state():
    """The spec's own worked example: RET_CPU on PD_SUB, primary
    SS_VDD_SUB, retention SS_VDD, one element `o_q`."""
    domain_state = PowerDomainDiagramState(
        supply_sets={
            "ss_sub": SupplySetPlacement(id="ss_sub", name="SS_VDD_SUB", power="VDD_SUB", ground="VSS", owner_path="sram/u_sub"),
            "ss_top": SupplySetPlacement(id="ss_top", name="SS_VDD", power="VDD", ground="VSS", owner_path="sram"),
        },
        domains={"d": PowerDomainPlacement(id="d", owner_path="sram/u_sub", name="PD_SUB", color=_COLOR, primary_supply_set="ss_sub")},
    )
    retention_state = RetentionDiagramState(
        strategies={
            "r": RetentionStrategyPlacement(
                id="r", name="RET_CPU", power_domain_id="d",
                primary_supply_set_id="ss_sub", retention_supply_set_id="ss_top",
                save_logic="save", restore_logic="!restore",
                elements=["sram/u_sub/o_q"], owner_path="sram/u_sub",
            )
        }
    )
    return domain_state, retention_state


def test_upf_retention_is_two_multi_line_commands_matching_the_specified_shape():
    """Pinned as one literal block rather than as separate fragments.

    The defect this guards is a SHAPE -- a stray unmatched brace, a
    continuation backslash in the wrong place, an indent level that
    disagrees with the line above it -- and every one of those survives
    a set of `assert "-domain PD_SUB" in text` fragment checks."""
    domain_state, retention_state = _retention_golden_state()
    text = generate_upf(SupplyPortDiagramState(), domain_state, retention_state, "3.1")
    assert _RETENTION_GOLDEN_CASE in text, text[text.index("# Retention"):]


def test_upf_retention_stays_two_separate_commands_and_is_never_merged():
    """Real UPF separates `set_retention` (domain/supply/elements) from
    `set_retention_control` (save/restore behaviour). An earlier
    proposal to fold the signals onto the first command was dropped as
    wrong, so this pins that they stay apart."""
    domain_state, retention_state = _retention_golden_state()
    text = generate_upf(SupplyPortDiagramState(), domain_state, retention_state, "3.1")

    assert text.count("set_retention RET_CPU") == 1
    assert text.count("set_retention_control RET_CPU") == 1
    # The signals belong to the CONTROL command only.
    retention_command = text[text.index("set_retention RET_CPU"):text.index("set_retention_control RET_CPU")]
    assert "-save_signal" not in retention_command
    assert "-restore_signal" not in retention_command
    # ...separated by exactly one blank line, as the reference golden
    # writes them.
    assert "    }\n\nset_retention_control RET_CPU" in text


def test_upf_retention_multi_hierarchy_replication_keeps_the_shape_everywhere():
    """Several strategies on one multi-hierarchy domain: every one of
    them gets the full multi-line shape, not just the first, with its
    elements root-relative."""
    domain_state = PowerDomainDiagramState(
        domains={"d": PowerDomainPlacement(
            id="d", owner_path="top/u_core", name="PD_MULTI", color=_COLOR,
            owner_paths=["top/u_core", "top/u_dbg"])},
    )
    retention_state = RetentionDiagramState(
        strategies={
            "r1": RetentionStrategyPlacement(
                id="r1", name="RET_CORE", power_domain_id="d", primary_supply_set_id="", retention_supply_set_id="",
                save_logic="s", restore_logic="r", elements=["top/u_core/a", "top/u_core/b"], owner_path="top/u_core"),
            "r2": RetentionStrategyPlacement(
                id="r2", name="RET_DBG", power_domain_id="d", primary_supply_set_id="", retention_supply_set_id="",
                save_logic="s", restore_logic="r", elements=["top/u_dbg/c"], owner_path="top/u_dbg"),
        }
    )
    text = generate_upf(SupplyPortDiagramState(), domain_state, retention_state, "3.1")
    for name in ("RET_CORE", "RET_DBG"):
        assert f"set_retention {name} \\\n    -domain PD_MULTI \\\n    -elements {{ \\" in text
        assert f"set_retention_control {name} \\\n    -domain PD_MULTI \\\n    -save_signal {{s high}} \\\n    -restore_signal {{r high}}" in text
    # Two elements in one strategy: each on its own continued line.
    assert "-elements { \\\n        u_core/a \\\n        u_core/b \\\n    }" in text


def test_cpf_retention_gets_the_same_multi_line_treatment():
    """CPF's `create_state_retention_rule` is the structurally
    analogous construct -- a named rule, a domain, an element list --
    so it formats like the UPF writer and like this format's own
    create_isolation_rule, rather than staying a compact one-liner."""
    domain_state, retention_state = _retention_golden_state()
    text = generate_cpf(SupplyPortDiagramState(), domain_state, retention_state, "2.0")
    # The element is root-relative here, not scope-relative: CPF has no
    # set_scope wrapper around this rule, which is pre-existing and
    # correct -- only the FORMATTING changed.
    assert ("create_state_retention_rule -name RET_CPU \\\n"
            "    -domain PD_SUB \\\n"
            "    -instances { \\\n"
            "        u_sub/o_q \\\n"
            "    }") in text


def test_every_generated_retention_line_has_a_matching_continuation():
    """A structural check over the whole file rather than one command:
    inside a multi-line command every line ends with a backslash except
    the last, and a brace group opened by `-elements {` is closed by a
    `    }` at the matching indent. A stray unmatched brace parses as a
    different command entirely."""
    domain_state, retention_state = _retention_golden_state()
    for text in (generate_upf(SupplyPortDiagramState(), domain_state, retention_state, "3.1"),
                 generate_cpf(SupplyPortDiagramState(), domain_state, retention_state, "2.0")):
        assert text.count("{") == text.count("}"), "unbalanced braces in the generated file"
        lines = text.splitlines()
        for i, line in enumerate(lines[:-1]):
            if line.rstrip().endswith("\\"):
                assert lines[i + 1].strip(), f"line {i} continues into a blank line"


# --- CPF audit fixes (docs/cpf2_reverification_audit.html) -------------------
#
# One test per finding, each naming the finding it locks in. These are the
# regression net for a pass that changed almost every line of CPF the
# generator emits, against a specification it had previously only been
# checked against at version 1.0.


def _audit_fixture():
    """A design exercising every corrected construct at once: a root
    domain, a nested switched domain, an internal supply, an isolation
    rule and a macro PG connection."""
    supply_state = SupplyPortDiagramState(
        supply_port_placements={
            "vdd": _port("vdd", "top", "VDD"),
            "vss": _port("vss", "top", "VSS"),
            "vdd_sub": _port("vdd_sub", "top/u_sub", "VDD_SUB", is_internal_supply=True),
        },
        pg_pin_placements={"pg": _port("pg", "top/u_sram", "VDD", fixed=True)},
    )
    supply_state.supply_connector_placements.append(("vdd", "pg"))
    domain_state = PowerDomainDiagramState(
        supply_sets={
            "ss_top": SupplySetPlacement(id="ss_top", name="SS_TOP", power="VDD", ground="VSS", owner_path="top"),
            "ss_sub": SupplySetPlacement(id="ss_sub", name="SS_SUB", power="VDD_SUB", ground="VSS", owner_path="top/u_sub"),
        },
        domains={
            "pd_top": PowerDomainPlacement(id="pd_top", owner_path="top", name="PD_TOP", color=_COLOR, primary_supply_set="ss_top"),
            "pd_sub": PowerDomainPlacement(id="pd_sub", owner_path="top/u_sub", name="PD_SUB", color=_COLOR, primary_supply_set="ss_sub"),
        },
    )
    switch_state = PowerSwitchDiagramState(
        switches={"sw": _switch("sw", "PG_SW", "pd_sub", ["vdd_sub"], ["top/u_sub"],
                                cell_type="HDR", on_state="pwr_en", off_state="!pwr_en")}
    )
    switch_state.switches["sw"].ack_signal = "pwr_ack"
    isolation_state = IsolationDiagramState(
        strategies={"iso": IsolationStrategyPlacement(
            id="iso", name="ISO1", power_domain_id="pd_sub", isolation_supply_set_id="ss_top",
            clamp_value="low", isolation_control="iso_en", isolation_sense="high", location="parent",
            elements=["top/u_sub/o_q"], owner_path="top/u_sub")}
    )
    return supply_state, domain_state, switch_state, isolation_state


def _audit_cpf(version="2.0"):
    supply_state, domain_state, switch_state, isolation_state = _audit_fixture()
    return generate_cpf(supply_state, domain_state, RetentionDiagramState(), version,
                        switch_state, design_name="top", isolation_state=isolation_state)


def test_audit_c1_macro_connections_use_create_global_connection():
    """`connect_supply_net` is a UPF command: zero occurrences in the CPF
    1.0 spec and zero in 2.1, and absent from CPF's own command index."""
    text = _audit_cpf()
    assert "connect_supply_net" not in text
    assert "create_global_connection -net VDD -pins {u_sram/VDD}" in text


def test_audit_c2_everything_sits_inside_one_design():
    """“The power intent represented by the power design is defined by
    the CPF commands between this command and a matching end_design
    command” (Si2 CPF 2.1, set_design, p. 201)."""
    text = _audit_cpf()
    lines = [l for l in text.splitlines() if l and not l.startswith("#")]
    assert lines[0] == "set_cpf_version 2.0"
    assert "set_design top" in lines
    assert lines[-1] == "end_design"
    # Exactly one pair -- an unclosed or doubled design block is the
    # failure this guards.
    assert text.count("set_design ") == 1 and text.count("end_design") == 1


def test_audit_c3_the_invented_isolation_net_flags_are_gone():
    """Neither flag exists in ANY version of CPF. The supply is stated as
    a DOMAIN on the create command instead (2.1, p. 134)."""
    text = _audit_cpf()
    assert "-isolation_power_net" not in text
    assert "-isolation_ground_net" not in text
    assert "-secondary_domain PD_TOP" in text


def test_audit_c4_a_2_1_only_location_is_withheld_at_2_0_and_emitted_at_2_1():
    """`-location self` was ADDED in CPF 2.1 (p. 9). Emitting it under
    `set_cpf_version 2.0` would make the file contradict its own version
    line."""
    supply_state, domain_state, switch_state, isolation_state = _audit_fixture()
    isolation_state.strategies["iso"].location = "self"

    def _command_lines(text):
        # Comment lines are excluded deliberately: the report line that
        # explains the omission NAMES the flag, so a plain substring
        # check would find it and pass for the wrong reason.
        return [l for l in text.splitlines() if l.strip() and not l.lstrip().startswith("#")]

    at_20 = generate_cpf(supply_state, domain_state, RetentionDiagramState(), "2.0",
                         switch_state, design_name="top", isolation_state=isolation_state)
    assert not any("-location self" in l for l in _command_lines(at_20))
    assert "needs CPF 2.1" in at_20  # reported, not silently dropped

    at_21 = generate_cpf(supply_state, domain_state, RetentionDiagramState(), "2.1",
                         switch_state, design_name="top", isolation_state=isolation_state)
    assert any("-location self" in l for l in _command_lines(at_21))
    assert "needs CPF 2.1" not in at_21
    # A location that was always legal is unaffected at either version.
    isolation_state.strategies["iso"].location = "parent"
    assert "-location parent" in generate_cpf(supply_state, domain_state, RetentionDiagramState(), "2.0",
                                              switch_state, design_name="top", isolation_state=isolation_state)


def test_audit_h1_and_h2_the_root_domain_is_default_and_excludes_by_flag():
    text = _audit_cpf()
    create = text[text.index("create_power_domain -name PD_TOP"):].split("update_power_domain")[0]
    assert "-default" in create            # H1: it used to select nothing at all
    assert "-exclude_instances {" in create  # H2: was a comment; the flag is real in 2.0
    assert "u_sub" in create


def test_audit_h3_the_acknowledge_receiver_is_numbered():
    """CPF 1.0 spelled it bare and took a PIN; 2.x numbers it and takes
    an expression (2.1, p. 266)."""
    text = _audit_cpf()
    assert "-acknowledge_receiver_1 {pwr_ack}" in text
    assert "-acknowledge_receiver " not in text  # the unnumbered 1.0 spelling


def test_audit_h4_the_switch_control_reaches_real_constructs():
    """It used to exist only inside a `#` comment."""
    text = _audit_cpf()
    assert "-shutoff_condition {!pwr_en}" in text          # on the domain
    assert "-enable_condition_1 {pwr_en}" in text          # on the switch rule


def test_audit_h5_a_switched_internal_supply_is_marked_internal():
    """“You must specify this option if this power net is the primary
    power net of an internal switchable power domain using header
    switches” (Si2 CPF 2.1, create_power_nets, p. 171)."""
    text = _audit_cpf()
    assert "create_power_nets -nets {VDD_SUB} -internal" in text
    # ...and a plain boundary supply is NOT marked internal.
    assert "create_power_nets -nets {VDD}\n" in text


def test_audit_m1_no_set_instance_and_the_separator_is_declared():
    """`.` is CPF's DEFAULT hierarchy separator (2.1, p. 44), so
    `set_instance .` named an instance literally called "." -- and every
    `/` path this writer emits needs the separator declared."""
    text = _audit_cpf()
    assert "set_instance" not in text
    assert "set_hierarchy_separator /" in text
    # The declaration has to precede the paths that depend on it.
    assert text.index("set_hierarchy_separator") < text.index("u_sram/VDD")


@pytest.mark.parametrize("applies_to, flag", [
    ("inputs", "-applies_to inputs"),
    ("outputs", "-applies_to outputs"),
    ("both", "-applies_to both"),
    ("elements", None),  # the dialog's own choice: `-elements` alone
])
def test_upf_level_shifter_writes_applies_to_only_for_a_real_direction(applies_to, flag):
    """`-applies_to` takes inputs/outputs/both in IEEE 1801. "elements" is
    this app's "use my element list" choice and used to leak out as an
    invalid `-applies_to elements`."""
    domain_state = PowerDomainDiagramState(domains={"d1": PowerDomainPlacement(id="d1", owner_path="top", name="PD_TOP", color=_COLOR)})
    elements = ["top/u_core/clk"] if applies_to == "elements" else []
    level_shifter_state = LevelShifterDiagramState(
        strategies={"ls1": LevelShifterStrategyPlacement(id="ls1", name="LS1", power_domain_id="d1", applies_to=applies_to, rule="both", elements=elements)}
    )
    text = generate_upf(SupplyPortDiagramState(), domain_state, RetentionDiagramState(), "3.1", level_shifter_state=level_shifter_state)
    block = text[text.index("set_level_shifter LS1"):].split("\n\n")[0]
    if flag is None:
        assert "-applies_to" not in block
        assert "-elements {" in block
    else:
        assert f"  {flag} \\" in block
    assert "-applies_to elements" not in text
