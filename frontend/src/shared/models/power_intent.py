"""Power-intent (UPF/CPF) view model.

Mirrors backend/elaborator/power_intent/ir/schema.py field-for-field --
see rtl.py's module docstring for why this is a duplicate, not an import.

THAT CLAIM IS NOW ENFORCED, not just stated. It used to be aspirational:
the mirror was a pragmatic subset carrying only the fields the mock-data
widgets happened to read, and 30 real backend fields (plus two whole
constructs) were missing. `test_power_intent_mirror_completeness.py`
now asserts the two schemas match field-for-field, in both directions.

Completeness is load-bearing for power-intent IMPORT specifically, not
tidiness. The importer has to warn when a file uses a construct this GUI
doesn't model -- and it cannot see that a field carried a value if the
mirror simply doesn't have the field. A subset mirror makes the whole
UNRECOGNIZED-construct warning impossible by construction, which is why
this was closed rather than left as an accepted gap.

SourceLoc here is deliberately NOT the same shape as rtl.SourceLoc:
`line` is optional and there is no `column` field at all -- Tcl (UPF/CPF's
underlying parser) doesn't expose column info the way pyslang does for
RTL. This is the real backend shape, not a simplification for mock data's
sake; the diagnostics layer is designed around this being genuinely true.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class SourceFormat(Enum):
    UPF = "upf"
    CPF = "cpf"


@dataclass(frozen=True)
class SourceLoc:
    file: str
    line: Optional[int] = None  # no `column` field -- see module docstring


@dataclass(frozen=True)
class SupplyPort:
    id: str
    name: str
    scope: str
    direction: Optional[str] = None
    source_loc: Optional[SourceLoc] = None


@dataclass(frozen=True)
class SupplyNet:
    id: str
    name: str
    scope: str
    voltage: Optional[str] = None
    # `create_supply_net <n> -reuse`: the SAME net as the one already
    # declared in an outer scope, continued into this one -- not a new,
    # independent net sharing its name.
    reuse: bool = False
    source_loc: Optional[SourceLoc] = None


@dataclass(frozen=True)
class SupplySet:
    id: str
    name: str
    scope: str
    power_net: Optional[str] = None
    ground_net: Optional[str] = None
    nwell_net: Optional[str] = None
    pwell_net: Optional[str] = None
    source_loc: Optional[SourceLoc] = None


@dataclass(frozen=True)
class PowerDomain:
    id: str
    name: str
    scope: str
    elements: list[str] = field(default_factory=list)
    exclude_elements: list[str] = field(default_factory=list)
    primary_supply: Optional[str] = None
    shutoff_condition: Optional[str] = None
    power_up_state: Optional[str] = None
    default_restore_edge: Optional[str] = None
    default_save_edge: Optional[str] = None
    is_default: bool = False
    boundary_ports: list[str] = field(default_factory=list)
    available_supplies: list[str] = field(default_factory=list)
    source_loc: Optional[SourceLoc] = None


@dataclass(frozen=True)
class PowerSwitchState:
    name: str
    boolean_expr: Optional[str] = None


@dataclass(frozen=True)
class PowerSwitch:
    id: str
    name: str
    scope: str
    domain: str
    input_supply: Optional[str] = None
    output_supply: Optional[str] = None
    input_supply_port: Optional[str] = None
    output_supply_port: Optional[str] = None
    control_ports: list[str] = field(default_factory=list)
    control_port_pins: list[str] = field(default_factory=list)
    # The switch's ACKNOWLEDGE output: nets in `ack_ports`, the cell's
    # own pin names in `ack_port_pins` -- the same split as
    # -control_port.
    ack_ports: list[str] = field(default_factory=list)
    ack_port_pins: list[str] = field(default_factory=list)
    states: list[PowerSwitchState] = field(default_factory=list)
    cell_type: Optional[str] = None
    # `map_power_switch -port_map {IN VDDH OUT VDDL CTRL SLEEP}`: the
    # abstract UPF switch port -> the real library cell pin implementing
    # it.
    port_map: dict[str, str] = field(default_factory=dict)
    source_loc: Optional[SourceLoc] = None


@dataclass(frozen=True)
class IsolationStrategy:
    id: str
    name: str
    scope: str
    domain: Optional[str] = None
    source: Optional[str] = None
    sink: Optional[str] = None
    elements: list[str] = field(default_factory=list)
    exclude_elements: list[str] = field(default_factory=list)
    condition: Optional[str] = None
    location: Optional[str] = None
    isolation_target: Optional[str] = None
    applies_to: Optional[str] = None
    clamp_value: Optional[str] = None
    isolation_supply: Optional[str] = None
    isolation_ground_net: Optional[str] = None
    isolation_signal: Optional[str] = None
    isolation_sense: Optional[str] = None
    # Prefix for the names of the CELL INSTANCES a tool inserts for this
    # strategy. UPF: `set_level_shifter -name_prefix` (verified) --
    # set_isolation has no confirmed equivalent. CPF: `-prefix` on both
    # update_isolation_rules and update_level_shifter_rules.
    name_prefix: Optional[str] = None
    source_loc: Optional[SourceLoc] = None


@dataclass(frozen=True)
class RetentionStrategy:
    id: str
    name: str
    scope: str
    domain: Optional[str] = None
    elements: list[str] = field(default_factory=list)
    retention_supply: Optional[str] = None
    primary_supply: Optional[str] = None
    save_signal: Optional[str] = None
    restore_signal: Optional[str] = None
    source_loc: Optional[SourceLoc] = None


@dataclass(frozen=True)
class LevelShifterStrategy:
    id: str
    name: str
    scope: str
    domain: Optional[str] = None
    source: Optional[str] = None
    sink: Optional[str] = None
    elements: list[str] = field(default_factory=list)
    exclude_elements: list[str] = field(default_factory=list)
    location: Optional[str] = None
    applies_to: Optional[str] = None
    rule: Optional[str] = None
    threshold: Optional[str] = None
    input_supply: Optional[str] = None
    output_supply: Optional[str] = None
    cell_type: Optional[str] = None
    # Prefix for the names of the CELL INSTANCES a tool inserts for this
    # strategy. UPF: `set_level_shifter -name_prefix` (verified) --
    # set_isolation has no confirmed equivalent. CPF: `-prefix` on both
    # update_isolation_rules and update_level_shifter_rules.
    name_prefix: Optional[str] = None
    source_loc: Optional[SourceLoc] = None


class PowerStateShape(Enum):
    """Provenance only -- mirrors the backend enum of the same name."""

    ADD_POWER_STATE = "add_power_state"
    PST_ROW = "pst_row"
    CPF_POWER_MODE = "cpf_power_mode"
    PORT_STATE = "port_state"


@dataclass(frozen=True)
class PowerState:
    id: str
    name: str
    scope: str
    applies_to: Optional[str] = None
    expr: Optional[str] = None
    supply_states: dict[str, str] = field(default_factory=dict)
    shape: PowerStateShape = PowerStateShape.ADD_POWER_STATE
    is_default: bool = False
    domain: Optional[str] = None
    supply: Optional[str] = None
    group: Optional[str] = None
    complete: bool = False
    source_loc: Optional[SourceLoc] = None


@dataclass(frozen=True)
class PowerStateGroup:
    id: str
    name: str
    scope: str
    members: list[str] = field(default_factory=list)
    source_loc: Optional[SourceLoc] = None


@dataclass(frozen=True)
class DomainSupplyConnection:
    domain: str
    supply_net: str
    scope: str
    role: Optional[str] = None
    ports: list[str] = field(default_factory=list)
    source_loc: Optional[SourceLoc] = None


@dataclass(frozen=True)
class PortAttribute:
    id: str
    scope: str
    ports: list[str] = field(default_factory=list)
    driver_supply: Optional[str] = None
    receiver_supply: Optional[str] = None
    source_loc: Optional[SourceLoc] = None


@dataclass(frozen=True)
class UnrecognizedConstruct:
    """Mirrors the backend record of a command skipped because it, or one
    of its options, is not recognized."""

    command: str
    text: str
    reason: str
    source_loc: Optional[SourceLoc] = None


@dataclass(frozen=True)
class PowerIntentDesign:
    source_format: SourceFormat
    source_version: str
    domains: dict[str, PowerDomain] = field(default_factory=dict)
    supply_ports: dict[str, SupplyPort] = field(default_factory=dict)
    supply_nets: dict[str, SupplyNet] = field(default_factory=dict)
    supply_sets: dict[str, SupplySet] = field(default_factory=dict)
    switches: dict[str, PowerSwitch] = field(default_factory=dict)
    isolation_strategies: dict[str, IsolationStrategy] = field(default_factory=dict)
    retention_strategies: dict[str, RetentionStrategy] = field(default_factory=dict)
    level_shifter_strategies: dict[str, LevelShifterStrategy] = field(default_factory=dict)
    power_states: dict[str, PowerState] = field(default_factory=dict)
    power_state_groups: dict[str, PowerStateGroup] = field(default_factory=dict)
    domain_supply_connections: list[DomainSupplyConnection] = field(default_factory=list)
    port_attributes: dict[str, PortAttribute] = field(default_factory=dict)
    unrecognized: list[UnrecognizedConstruct] = field(default_factory=list)


def make_power_intent_id(scope: str, name: str) -> str:
    return f"{scope}::{name}"
