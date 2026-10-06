"""Mutable, accumulate-then-freeze state, shared across UPF (2a) and CPF
(2b, not started).

Every public schema type (backend/elaborator/power_intent/ir/schema.py) is
frozen, but CPF's create_X/update_X pairs build one named construct across
multiple commands. The builder dataclasses here accumulate that state;
`_freeze_all` converts them to the public frozen types in one pass at the
very end. Confirmed version-agnostic by construction: builders mirror the
canonical UPF 3.1 shape regardless of which supported version the source
design declares -- see docs/milestone2_powerintent_elaborator.md's design
question 2 for why 3.1 is the one canonical shape everything normalizes
into. None of this module is, or needs to be, exported outside the
`power_intent` package.

Genuinely format-agnostic, confirmed by construction, not just claimed:
`_ElaborationState` carries no format-specific fields (the one exception --
legacy UPF PST bookkeeping -- lives in `state.scratch`, a generic
extensibility bag any format's verb modules can use for their own private
multi-command bookkeeping, rather than a UPF-named field on the shared
state itself) and `_freeze_all` takes `source_format` as an explicit
parameter rather than hardcoding it, so a future CPF elaborator can reuse
this module as-is.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Any, Callable, Optional

from backend.elaborator.power_intent.ir import (
    DomainSupplyConnection,
    IsolationStrategy,
    LevelShifterStrategy,
    PowerDomain,
    PowerIntentDesign,
    PowerState,
    PowerStateGroup,
    PowerStateShape,
    PowerSwitch,
    PowerSwitchState,
    PortAttribute,
    RetentionStrategy,
    SourceFormat,
    SourceLoc,
    SupplyNet,
    UnrecognizedConstruct,
    SupplyPort,
    SupplySet,
)


@dataclass
class _DomainBuilder:
    id: str
    name: str
    scope: str
    elements: list[str] = field(default_factory=list)
    exclude_elements: list[str] = field(default_factory=list)
    primary_supply: Optional[str] = None
    # Verified against CPF's primary spec, not UPF's -- left unpopulated by
    # the UPF verb handler until real UPF flag names are confirmed. See
    # backend/elaborator/power_intent/upf/elaborate.py's module docstring.
    shutoff_condition: Optional[str] = None
    power_up_state: Optional[str] = None
    default_restore_edge: Optional[str] = None
    default_save_edge: Optional[str] = None
    is_default: bool = False
    boundary_ports: list[str] = field(default_factory=list)
    available_supplies: list[str] = field(default_factory=list)
    # No internal_power_net/internal_ground_net builder fields -- that data
    # is unified onto DomainSupplyConnection instead, see schema.py.
    source_loc: Optional[SourceLoc] = None


@dataclass
class _SupplyPortBuilder:
    id: str
    name: str
    scope: str
    direction: Optional[str] = None
    source_loc: Optional[SourceLoc] = None


@dataclass
class _SupplyNetBuilder:
    id: str
    name: str
    scope: str
    voltage: Optional[str] = None
    # `create_supply_net <n> -reuse`: this net CONTINUES the same net
    # already declared in an outer scope rather than declaring a new,
    # independent one that happens to share its name.
    reuse: bool = False
    source_loc: Optional[SourceLoc] = None


@dataclass
class _SupplySetBuilder:
    id: str
    name: str
    scope: str
    power_net: Optional[str] = None
    ground_net: Optional[str] = None
    nwell_net: Optional[str] = None
    pwell_net: Optional[str] = None
    source_loc: Optional[SourceLoc] = None


@dataclass
class _PowerSwitchBuilder:
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
    ack_ports: list[str] = field(default_factory=list)
    ack_port_pins: list[str] = field(default_factory=list)
    states: list[PowerSwitchState] = field(default_factory=list)
    # Set by a LATER map_power_switch command refining this same switch --
    # the accumulate-then-freeze shape this module exists for.
    cell_type: Optional[str] = None
    # map_power_switch -port_map: abstract UPF switch port (IN/OUT/CTRL)
    # -> the real library cell pin implementing it.
    port_map: dict[str, str] = field(default_factory=dict)
    source_loc: Optional[SourceLoc] = None


@dataclass
class _IsolationBuilder:
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


@dataclass
class _RetentionBuilder:
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


@dataclass
class _LevelShifterBuilder:
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
    # Set by a LATER set_level_shifter_cell command refining this same
    # strategy -- same accumulate-then-freeze shape as the switch's own.
    cell_type: Optional[str] = None
    # Prefix for the names of the CELL INSTANCES a tool inserts for this
    # strategy. UPF: `set_level_shifter -name_prefix` (verified) --
    # set_isolation has no confirmed equivalent. CPF: `-prefix` on both
    # update_isolation_rules and update_level_shifter_rules.
    name_prefix: Optional[str] = None
    source_loc: Optional[SourceLoc] = None


@dataclass
class _PowerStateBuilder:
    id: str
    name: str
    scope: str
    applies_to: Optional[str] = None
    expr: Optional[str] = None
    supply_states: dict[str, str] = field(default_factory=dict)
    shape: PowerStateShape = PowerStateShape.ADD_POWER_STATE
    domain: Optional[str] = None
    supply: Optional[str] = None
    group: Optional[str] = None
    complete: bool = False
    is_default: bool = False
    source_loc: Optional[SourceLoc] = None


@dataclass
class _PortAttributeBuilder:
    id: str
    scope: str
    ports: list[str] = field(default_factory=list)
    driver_supply: Optional[str] = None
    receiver_supply: Optional[str] = None
    source_loc: Optional[SourceLoc] = None


@dataclass
class _PowerStateGroupBuilder:
    id: str
    name: str
    scope: str
    members: list[str] = field(default_factory=list)
    source_loc: Optional[SourceLoc] = None


def _freeze_one(builder, frozen_cls):
    """Builders mirror their frozen counterpart field-for-field by
    construction, so conversion is a direct field copy -- not
    dataclasses.asdict, which would also (incorrectly) deep-convert nested
    dataclasses like PowerSwitchState into plain dicts."""
    return frozen_cls(**{f.name: getattr(builder, f.name) for f in fields(builder)})


class _ElaborationState:
    def __init__(self, top_file: str):
        self.top_file = top_file
        self.source_version: Optional[str] = None
        self.version_declared_by: dict[str, str] = {}
        self._file_stack: list[str] = []
        self._scope_stack: list[str] = [""]

        self.domains: dict[str, _DomainBuilder] = {}
        self.supply_ports: dict[str, _SupplyPortBuilder] = {}
        self.supply_nets: dict[str, _SupplyNetBuilder] = {}
        self.supply_sets: dict[str, _SupplySetBuilder] = {}
        self.switches: dict[str, _PowerSwitchBuilder] = {}
        self.isolation_strategies: dict[str, _IsolationBuilder] = {}
        self.retention_strategies: dict[str, _RetentionBuilder] = {}
        self.level_shifter_strategies: dict[str, _LevelShifterBuilder] = {}
        self.power_states: dict[str, _PowerStateBuilder] = {}
        self.power_state_groups: dict[str, _PowerStateGroupBuilder] = {}
        self.port_attributes: dict[str, _PortAttributeBuilder] = {}
        self.domain_supply_connections: list[DomainSupplyConnection] = []
        # Generic bag for a format's own private, multi-command bookkeeping
        # that doesn't correspond to any canonical construct -- e.g. UPF
        # legacy PST's create_pst-name -> supply-list mapping, needed by
        # add_pst_state later in the same file. Keeps this shared state
        # class free of format-specific fields.
        self.scratch: dict[str, Any] = {}
        # Opt-in per format (UPF sets both; CPF leaves them off, so its
        # behaviour is unchanged): `line_lookup` returns the line of the
        # command being run, for every SourceLoc; `skip_unrecognized`
        # makes an unrecognized command or option a recorded skip
        # (`unrecognized`) instead of a failure of the whole file.
        self.line_lookup: Optional[Callable[[], tuple[Optional[int], str]]] = None
        self.skip_unrecognized = False
        self.unrecognized: list[UnrecognizedConstruct] = []

    @property
    def current_file(self) -> str:
        return self._file_stack[-1]

    @property
    def current_scope(self) -> str:
        return self._scope_stack[-1]

    def push_file(self, path: str) -> None:
        self._file_stack.append(path)

    def pop_file(self) -> None:
        self._file_stack.pop()

    def push_scope(self, scope: str) -> None:
        self._scope_stack.append(scope)

    def pop_scope(self) -> None:
        self._scope_stack.pop()

    def set_scope(self, scope: str) -> None:
        """Replaces the current scope in place, rather than nesting a new
        one. UPF's load_upf nests (push/pop) because file-sourcing has a
        real call/return structure to unwind. CPF's set_instance/end_design
        don't -- verified against the primary spec's worked example and its
        own text ("set_instance specifies... a hierarchical instance in the
        top design"): CPF's hierarchy is a flat sequence of independent
        set_design/end_design blocks, each scoped by the most recent
        set_instance call (relative to top), not a nested push/pop stack."""
        self._scope_stack[-1] = scope

    def loc(self) -> SourceLoc:
        line = self.line_lookup()[0] if self.line_lookup is not None else None
        return SourceLoc(file=self.current_file, line=line)

    def record_unrecognized(self, command: str, reason: str) -> None:
        line, text = self.line_lookup() if self.line_lookup is not None else (None, command)
        self.unrecognized.append(
            UnrecognizedConstruct(
                command=command,
                text=text.splitlines()[0].rstrip(" \\") if text else command,
                reason=reason,
                source_loc=SourceLoc(file=self.current_file, line=line),
            )
        )


def _freeze_all(state: _ElaborationState, source_format: SourceFormat) -> PowerIntentDesign:
    if state.source_version is None:
        raise ValueError(
            f"design rooted at '{state.top_file}' never declared a version and issued no "
            "power-intent commands -- a design must declare its version"
        )
    return PowerIntentDesign(
        source_format=source_format,
        source_version=state.source_version,
        domains={k: _freeze_one(v, PowerDomain) for k, v in state.domains.items()},
        supply_ports={k: _freeze_one(v, SupplyPort) for k, v in state.supply_ports.items()},
        supply_nets={k: _freeze_one(v, SupplyNet) for k, v in state.supply_nets.items()},
        supply_sets={k: _freeze_one(v, SupplySet) for k, v in state.supply_sets.items()},
        switches={k: _freeze_one(v, PowerSwitch) for k, v in state.switches.items()},
        isolation_strategies={k: _freeze_one(v, IsolationStrategy) for k, v in state.isolation_strategies.items()},
        retention_strategies={k: _freeze_one(v, RetentionStrategy) for k, v in state.retention_strategies.items()},
        level_shifter_strategies={
            k: _freeze_one(v, LevelShifterStrategy) for k, v in state.level_shifter_strategies.items()
        },
        power_states={k: _freeze_one(v, PowerState) for k, v in state.power_states.items()},
        power_state_groups={k: _freeze_one(v, PowerStateGroup) for k, v in state.power_state_groups.items()},
        domain_supply_connections=list(state.domain_supply_connections),
        port_attributes={k: _freeze_one(v, PortAttribute) for k, v in state.port_attributes.items()},
        unrecognized=list(state.unrecognized),
    )
