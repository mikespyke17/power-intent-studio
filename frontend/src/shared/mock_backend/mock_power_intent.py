"""Canned mock PowerIntentDesign -- fakes what backend/elaborator/
power_intent/'s elaborate() would produce, in the frontend's own mirrored
shape (shared/models/power_intent.py). Literal, hand-built data, used by
the mocked "Mapping" button's input (and eventually "Load Power Intent").

Deliberately includes PD_EMPTY, a domain whose -elements reference an
instance that doesn't exist in mock_rtl's design -- this is the canned
scenario's own real "zero resolved members" case (see
power_intent_view/diagram_overlay.py's DomainRegionItem handling), not
just something constructed ad hoc in a test.
"""

from __future__ import annotations

from frontend.src.shared.models.power_intent import (
    DomainSupplyConnection,
    IsolationStrategy,
    LevelShifterStrategy,
    PowerDomain,
    PowerIntentDesign,
    PowerState,
    PowerStateGroup,
    PowerSwitch,
    RetentionStrategy,
    SourceFormat,
    SupplyNet,
    SupplyPort,
    SupplySet,
    make_power_intent_id,
)


def build_mock_power_intent() -> PowerIntentDesign:
    scope = ""

    domains = {
        make_power_intent_id(scope, "PD_CORE"): PowerDomain(
            id=make_power_intent_id(scope, "PD_CORE"), name="PD_CORE", scope=scope, elements=["u_core"], primary_supply="SS1"
        ),
        make_power_intent_id(scope, "PD_EMPTY"): PowerDomain(
            id=make_power_intent_id(scope, "PD_EMPTY"), name="PD_EMPTY", scope=scope, elements=["u_nonexistent"]
        ),
    }

    supply_ports = {
        make_power_intent_id(scope, "VDD_IN"): SupplyPort(id=make_power_intent_id(scope, "VDD_IN"), name="VDD_IN", scope=scope, direction="in"),
    }
    supply_nets = {
        make_power_intent_id(scope, "VDD"): SupplyNet(id=make_power_intent_id(scope, "VDD"), name="VDD", scope=scope),
        make_power_intent_id(scope, "VDD_CORE"): SupplyNet(id=make_power_intent_id(scope, "VDD_CORE"), name="VDD_CORE", scope=scope),
        make_power_intent_id(scope, "VSS"): SupplyNet(id=make_power_intent_id(scope, "VSS"), name="VSS", scope=scope),
    }
    supply_sets = {
        make_power_intent_id(scope, "SS1"): SupplySet(id=make_power_intent_id(scope, "SS1"), name="SS1", scope=scope, power_net="VDD", ground_net="VSS"),
    }

    switches = {
        make_power_intent_id(scope, "SW_CORE"): PowerSwitch(
            id=make_power_intent_id(scope, "SW_CORE"),
            name="SW_CORE",
            scope=scope,
            domain="PD_CORE",
            input_supply="VDD",
            output_supply="VDD_CORE",
            control_ports=["sleep_n"],
        ),
    }

    isolation_strategies = {
        make_power_intent_id(scope, "ISO1"): IsolationStrategy(
            id=make_power_intent_id(scope, "ISO1"), name="ISO1", scope=scope, domain="PD_CORE", elements=["u_core/out1"], clamp_value="0"
        ),
    }
    level_shifter_strategies = {
        make_power_intent_id(scope, "LS1"): LevelShifterStrategy(
            id=make_power_intent_id(scope, "LS1"), name="LS1", scope=scope, domain="PD_CORE", elements=["u_core/out2"]
        ),
    }
    retention_strategies = {
        make_power_intent_id(scope, "RET1"): RetentionStrategy(
            id=make_power_intent_id(scope, "RET1"), name="RET1", scope=scope, domain="PD_CORE", elements=["u_core"]
        ),
    }

    power_states = {
        make_power_intent_id(scope, "SS1.ON"): PowerState(
            id=make_power_intent_id(scope, "SS1.ON"), name="ON", scope=scope, applies_to="SS1", domain="PD_CORE", expr="sleep_n == 1"
        ),
        make_power_intent_id(scope, "SS1.OFF"): PowerState(
            id=make_power_intent_id(scope, "SS1.OFF"), name="OFF", scope=scope, applies_to="SS1", expr="sleep_n == 0"
        ),
    }
    power_state_groups = {
        make_power_intent_id(scope, "PSG1"): PowerStateGroup(id=make_power_intent_id(scope, "PSG1"), name="PSG1", scope=scope, members=["SS1"]),
    }

    domain_supply_connections = [
        DomainSupplyConnection(domain="PD_CORE", supply_net="VDD", scope=scope, role="primary_power"),
        DomainSupplyConnection(domain="PD_CORE", supply_net="VSS", scope=scope, role="primary_ground"),
    ]

    return PowerIntentDesign(
        source_format=SourceFormat.UPF,
        source_version="3.1",
        domains=domains,
        supply_ports=supply_ports,
        supply_nets=supply_nets,
        supply_sets=supply_sets,
        switches=switches,
        isolation_strategies=isolation_strategies,
        retention_strategies=retention_strategies,
        level_shifter_strategies=level_shifter_strategies,
        power_states=power_states,
        power_state_groups=power_state_groups,
        domain_supply_connections=domain_supply_connections,
    )
