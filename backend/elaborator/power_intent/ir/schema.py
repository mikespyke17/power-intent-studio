"""Unified, format-agnostic power-intent IR (Milestone 2c).

Both the UPF elaborator (2a) and the CPF elaborator (2b) normalize into
these types. Nothing here is aware of Tcl syntax or which format/version
produced a given construct beyond the provenance fields noted below.

Canonical shape: UPF 3.1's refined power-state/isolation model. UPF 2.1's
legacy PST rows and CPF's differing vocabulary (`power_mode`,
`isolation_rule`, etc.) normalize up into these same types -- the same way
Milestone 1's IR normalizes Verilog and VHDL regardless of source language.
See docs/milestone2_powerintent_elaborator.md for the full research/design
writeup this schema is built on.

Deliberately no `Design.walk()`/`top` the way backend/elaborator/rtl/ir does:
RTL's IR is a tree because elaboration produces a hierarchy of Definitions.
Power intent isn't tree-shaped the same way -- it's a flat collection of
domains/strategies, each independently tagged with its own raw `scope`
path string (built from nested `load_upf -scope`/CPF-equivalent context).
Hierarchy resolution against real RTL instance paths is Milestone 3's job;
2c does not require RTL to be elaborated first and does not attempt to
resolve or validate these paths.

`scope` was only present on PowerDomain/SupplyPort/SupplyNet/SupplySet/
PowerStateGroup through the end of 2b -- IsolationStrategy,
LevelShifterStrategy, RetentionStrategy, PowerSwitch, PowerState, and
DomainSupplyConnection didn't carry it explicitly (only baked opaquely into
`id` via make_power_intent_id, which is deliberately never parsed back
apart). Added explicitly to all six during Milestone 3 planning: the
RTL-hierarchy resolver needs each construct's own authoring scope to
resolve its relative element/port/signal references, and reaching into
`id` to get it would violate `id`'s own "opaque, don't parse" contract.

`source_format` and the design's resolved semantic version live once on
`PowerIntentDesign`, not repeated per construct: both are whole-design facts
given "UPF and CPF are never mixed within one design" and the elaborator's
own single-resolved-version-per-design rule (conflicting per-file version
declarations are a hard error, not silently merged -- see the doc).
`PowerState.shape` is the one provenance field kept at construct
granularity, since a single UPF 2.1 file can legitimately mix legacy PST
rows and native `add_power_state` calls in the same design.

Every frozen type here mirrors a *finished* construct. Tcl-driven
elaboration is inherently incremental -- CPF's `create_X`/`update_X` pairs
and UPF's successive-refinement methodology both build a single named
construct up across multiple commands, arriving in file order, not
necessarily in a single call. That accumulation happens against mutable,
internal-only builder objects private to elaborate.py (2a/2b); only a
single freeze pass at the end of elaboration ever constructs these frozen
types. Nothing in this module is, or needs to be, mutable.
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
    # Tcl exposes no column info the way pyslang does, and reliable line
    # numbers depend on interpreter frame bookkeeping not yet verified
    # against real multi-file sourcing -- may end up file-only in practice.
    line: Optional[int] = None


@dataclass(frozen=True)
class SupplyPort:
    id: str
    name: str
    scope: str  # hierarchical scope path as authored; raw, unresolved -- see module docstring
    direction: Optional[str] = None  # raw as authored; not force-fit into rtl.ir.PortDirection until verified
    source_loc: Optional[SourceLoc] = None


@dataclass(frozen=True)
class SupplyNet:
    id: str
    name: str
    scope: str
    voltage: Optional[str] = None  # CPF create_power_nets/create_ground_nets -voltage, raw as authored
    # `create_supply_net <n> -reuse`: this net is the SAME net as the one
    # already declared in an outer scope, continued into this one -- not a
    # new, independent net sharing its name. The distinction is
    # electrical: without -reuse, a child-scope redeclaration is a
    # separate, floating rail.
    reuse: bool = False
    source_loc: Optional[SourceLoc] = None


@dataclass(frozen=True)
class SupplySet:
    """UPF supply_set (power/ground function handles). CPF has no exact
    equivalent construct -- captured on PowerDomain directly for
    CPF-sourced designs instead of synthesizing a construct CPF never
    declared (normalize shape, not force every format through identical
    constructs it doesn't have)."""

    id: str
    name: str
    scope: str
    power_net: Optional[str] = None
    ground_net: Optional[str] = None
    # UPF `create_supply_set -function {nwell VNW} -function {pwell VPW}`.
    # Real, and common in a bias-aware flow. Their absence did not just
    # lose the data -- the elaborator raised on the function kind (fail
    # loud, correctly, since there was nowhere to put it), so an
    # otherwise-valid file was rejected OUTRIGHT for rails this app's own
    # SupplySetPlacement already models with n_well/p_well fields. Found
    # by hand-authoring a bias-aware fixture; see
    # tests/frontend/golden_simple10/README.md, iteration 6.
    nwell_net: Optional[str] = None
    pwell_net: Optional[str] = None
    source_loc: Optional[SourceLoc] = None


@dataclass(frozen=True)
class PowerDomain:
    id: str
    name: str
    scope: str
    elements: list[str] = field(default_factory=list)  # raw instance paths, exactly as authored
    exclude_elements: list[str] = field(default_factory=list)  # UPF -exclude_elements: elements to omit from `elements`
    primary_supply: Optional[str] = None  # supply-set / CPF default-supply reference, raw name

    # Verified directly against the CPF 1.0 primary spec's create_power_domain
    # (-shutoff_condition, -power_up_states, -default_restore_edge,
    # -default_save_edge, -default). Kept as domain-level defaults, not
    # forced onto every RetentionStrategy: a RetentionStrategy with no
    # explicit save/restore signal of its own falls back to these.
    # NOT verified against UPF's own primary spec (IEEE 1801 is paywalled,
    # unlike CPF's freely available spec) -- confirm equivalent UPF fields
    # exist with matching semantics once 2a starts.
    shutoff_condition: Optional[str] = None  # raw boolean expression string, not evaluated
    power_up_state: Optional[str] = None  # raw: e.g. "high"/"low"/"random" (CPF vocabulary)
    default_restore_edge: Optional[str] = None
    default_save_edge: Optional[str] = None
    is_default: bool = False  # CPF: exactly one domain in a design must be the default

    # Also verified against the CPF 1.0 primary spec's create_power_domain
    # (-boundary_ports) -- read during the same research pass as the fields
    # directly above, but not added to the schema until the 2b audit caught
    # the gap. See docs/milestone2_powerintent_elaborator.md's note on this
    # being the second time researched-but-unwired fields surfaced.
    # No UPF equivalent found -- unlike update_power_domain's
    # -internal_power_net/-internal_ground_net (see below), this one stays
    # CPF-only, same treatment as SupplySet.
    boundary_ports: list[str] = field(default_factory=list)  # ports/pins considered part of this domain's boundary

    # UPF `create_power_domain -available_supplies {SS_A SS_B}`: supply
    # sets usable by this domain beyond its own -supply {primary ...}.
    # Added for round-trip import -- the GUI's own Secondary Supply Sets
    # generate exactly this flag, and the elaborator previously rejected
    # it outright, so a generated file could not be read back.
    available_supplies: list[str] = field(default_factory=list)

    # NOTE: CPF's update_power_domain -internal_power_net/-internal_ground_net
    # is NOT modeled as fields here. It's the same concept as UPF's
    # set_domain_supply_net -primary_power_net/-primary_ground_net --
    # "the net that drives this domain's functional logic" -- just named
    # differently per format. Unified onto DomainSupplyConnection instead
    # (role="primary_power"/"primary_ground", shared with UPF) so a
    # format-agnostic consumer doesn't have to branch on source_format to
    # answer "what powers this domain." See DomainSupplyConnection and
    # cpf/verbs.py's _update_power_domain for the caveat on how confident
    # this equivalence actually is.

    source_loc: Optional[SourceLoc] = None


@dataclass(frozen=True)
class PowerSwitchState:
    name: str
    boolean_expr: Optional[str] = None  # raw expression string, not evaluated


@dataclass(frozen=True)
class PowerSwitch:
    id: str
    name: str
    scope: str
    domain: str
    input_supply: Optional[str] = None
    output_supply: Optional[str] = None
    # The PORT half of `-input_supply_port {port net}` /
    # `-output_supply_port {port net}`. `input_supply`/`output_supply`
    # above hold the NET half, which is what connectivity needs; these
    # hold the switch cell's own pin name, which is what an editor
    # needs. Storing only the net silently lost the pin name.
    input_supply_port: Optional[str] = None
    output_supply_port: Optional[str] = None
    # `-control_port {ctrl pwr_en}` names a PAIR: the switch cell's own
    # pin, then the net driving it. They are different things with
    # different consumers, so both halves are kept -- `control_ports`
    # holds the NETS (what the mapper resolves as a real signal
    # reference), `control_port_pins` the PIN names (what a GUI showing
    # the cell's own pinout needs). Keeping only the net silently
    # relabelled a pin as its net for any file using the pair form,
    # which a file this app generates itself never does (it writes the
    # bare pin) -- found by hand-authoring a vendor-style fixture, see
    # tests/frontend/golden_simple10/README.md, iteration 2.
    control_ports: list[str] = field(default_factory=list)
    control_port_pins: list[str] = field(default_factory=list)
    # The switch's ACKNOWLEDGE output, split the same way
    # -control_port is: `ack_ports` holds the NETS (the real design-side
    # signal the pin drives -- also where CPF's own
    # `update_power_switch_rule -acknowledge_receiver` normalises to),
    # `ack_port_pins` the switch cell's own pin names.
    ack_ports: list[str] = field(default_factory=list)
    ack_port_pins: list[str] = field(default_factory=list)
    states: list[PowerSwitchState] = field(default_factory=list)
    # Library cell from the separate UPF `map_power_switch <sw>
    # -lib_cells {C}` command (CPF: `update_power_switch_rule -cells`).
    # A second command refining an already-declared switch, the same
    # accumulate-then-freeze shape CPF's own create/update pairs use.
    cell_type: Optional[str] = None
    # `map_power_switch -port_map {IN VDDH OUT VDDL CTRL SLEEP}`: the
    # abstract UPF switch port -> the real library cell pin implementing
    # it. Without it UPF falls back to literal name matching between the
    # two, which fails whenever a real cell's pins are not themselves
    # named IN/OUT/CTRL.
    port_map: dict[str, str] = field(default_factory=dict)
    source_loc: Optional[SourceLoc] = None


@dataclass(frozen=True)
class IsolationStrategy:
    """Field shapes verified directly against the CPF 1.0 primary spec's
    create_isolation_rule/update_isolation_rules; UPF's own shapes are
    secondary-source-informed only (IEEE 1801 is paywalled -- see module
    docstring). Two things confirmed to NOT be the clean equivalence they
    might look like:

    - `location`: UPF's `-location {self|other|parent|fanout}` (placement
      relative to the domain boundary) and CPF's `-location {from|to}`
      (found on the *separate* update_isolation_rules command -- which
      domain's instance-list the cell is counted under for implementation,
      default "to") share a field name here purely as a raw-string
      passthrough convenience. They are NOT a verified semantic
      equivalence -- do not compare this field across source_format
      without further validation-layer work.
    - CPF's create_isolation_rule `-from`/`-to` are actually domain *lists*
      used as pin filters (output pins in -from domains, input pins in -to
      domains, combinable), not a strict single-source/single-sink pair.
      Only the common single-domain-each case maps cleanly onto
      `source`/`sink` below; multi-domain CPF isolation rules are a
      tracked gap (see the doc), not modeled as a list here on a hunch.
    """

    id: str
    name: str
    scope: str
    # Optional, not required: CPF's create_isolation_rule has no -domain
    # flag at all (it selects pins via -from/-to/-pins instead) -- verified
    # directly against the primary spec while building 2b. UPF's
    # set_isolation does take -domain.
    domain: Optional[str] = None
    source: Optional[str] = None  # UPF -source / CPF -from (single-domain case only, see docstring)
    sink: Optional[str] = None  # UPF -sink / CPF -to (single-domain case only, see docstring)
    elements: list[str] = field(default_factory=list)  # UPF -elements / CPF -pins: specific ports/pins, not just source/sink
    exclude_elements: list[str] = field(default_factory=list)  # CPF -exclude; UPF equivalent not yet verified
    condition: Optional[str] = None  # CPF -isolation_condition: raw boolean expression, not evaluated
    location: Optional[str] = None  # raw passthrough; NOT cross-format-comparable, see docstring
    isolation_target: Optional[str] = None  # CPF -isolation_target {from|to}; UPF equivalent not yet verified
    # UPF `set_isolation -applies_to {inputs|outputs|both}`: which
    # direction of the domain's boundary ports the strategy covers. Real,
    # and used by idiomatic files -- the elaborator rejected it outright
    # until one such file was tried. Same field NAME as
    # LevelShifterStrategy.applies_to and the same vocabulary, but kept
    # per-construct rather than shared, matching how every other
    # same-named-different-construct field here is handled.
    applies_to: Optional[str] = None
    clamp_value: Optional[str] = None  # UPF 0/1/Z/latch/value or CPF high/low/hold, raw as authored
    # UPF's `-isolation_supply_set` names a SUPPLY SET; CPF names the two
    # rails separately (`update_isolation_rules -isolation_power_net` /
    # `-isolation_ground_net`, both in this project's own golden.cpf). The
    # set name lands here and the CPF power net does too -- they are
    # different KINDS of name, so a consumer must look at source_format
    # before deciding which dict to resolve against. CPF's ground half
    # gets its own field rather than being dropped; UPF has no separate
    # ground name to put there, since the set carries both.
    isolation_supply: Optional[str] = None
    isolation_ground_net: Optional[str] = None
    # UPF `-isolation_signal` (the control net) and `-isolation_sense`
    # (which polarity of it activates isolation). Both appear in this
    # project's own golden.upf and are generated by the GUI, but the
    # elaborator recognized neither -- added for round-trip import.
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
    # Optional: CPF's create_state_retention_rule allows {-domain |
    # -instances} -- either is valid, not just -domain. Verified directly
    # against the primary spec while building 2b.
    domain: Optional[str] = None
    elements: list[str] = field(default_factory=list)
    retention_supply: Optional[str] = None
    # UPF `set_retention <r> -supply {primary SS_A} -supply {retention SS_B}`
    # names TWO supply sets: the domain-normal one and the one that stays
    # live to hold retained state. Only the retention half had a field, so
    # round-trip import silently dropped the primary -- added here.
    primary_supply: Optional[str] = None
    save_signal: Optional[str] = None  # falls back to PowerDomain.default_save_edge when None
    restore_signal: Optional[str] = None  # falls back to PowerDomain.default_restore_edge when None
    source_loc: Optional[SourceLoc] = None


@dataclass(frozen=True)
class LevelShifterStrategy:
    """Same `location` caveat as IsolationStrategy: CPF's
    update_level_shifter_rules `-location {from|to}` and UPF's
    `-location` are a raw passthrough, not a verified equivalence."""

    id: str
    name: str
    scope: str
    domain: Optional[str] = None  # optional, same reason as IsolationStrategy.domain -- CPF has no -domain flag here either
    source: Optional[str] = None
    sink: Optional[str] = None
    elements: list[str] = field(default_factory=list)  # UPF -elements / CPF -pins: specific ports/pins, not just source/sink
    exclude_elements: list[str] = field(default_factory=list)  # CPF -exclude; UPF equivalent not yet verified
    location: Optional[str] = None
    # UPF `-applies_to {inputs|outputs|both}`, `-rule {low_to_high|...}`
    # and `-threshold <v>`; plus the cell name from the separate
    # `set_level_shifter_cell -level_shifter <this> -cell_name <c>`
    # command (CPF: `update_level_shifter_rules -cells`). All four appear
    # in golden.upf and are generated by the GUI; none were recognized.
    applies_to: Optional[str] = None
    rule: Optional[str] = None
    threshold: Optional[str] = None
    # UPF `set_level_shifter -input_supply`/`-output_supply`: the supply
    # sets on each side of the shift. Real, and used by idiomatic files.
    # This app's own Level Shifter has no such pair (it tracks one Power
    # Domain plus a free-text Cell Type), so they are captured here and
    # reported as unmodelled rather than being rejected at parse time --
    # which is what used to happen, failing the whole file.
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
    """Provenance only -- every PowerState means the same thing once in the
    IR; mirrors why rtl.ir.Definition keeps a `language` tag even though
    downstream consumers don't branch on it."""

    ADD_POWER_STATE = "add_power_state"  # UPF 2.0+ native
    PST_ROW = "pst_row"  # UPF legacy create_pst/add_pst_state, mechanically translated -- see doc
    CPF_POWER_MODE = "cpf_power_mode"  # CPF create_power_mode
    # UPF legacy `add_port_state` / `add_supply_state <net> -state {NAME value}`:
    # a NAMED per-supply state whose `expr` is its voltage, or "off".
    PORT_STATE = "port_state"


@dataclass(frozen=True)
class PowerState:
    id: str
    name: str
    scope: str
    # Optional: UPF's add_power_state always names a single supply-set/
    # domain object to apply to. CPF's create_power_mode has no equivalent
    # single referent -- a power mode's -domain_conditions spans multiple
    # domains at once, so there's no one object to name here. Verified
    # directly against the primary spec while building 2b.
    applies_to: Optional[str] = None
    expr: Optional[str] = None  # raw expression, or synthesized PST-row conjunction
    # A legacy PST row's own per-supply values, {supply name: state name}.
    # `expr` already encodes the same thing, but only as a STRING this
    # codebase synthesises itself -- a consumer wanting the values back
    # had to re-parse a private format, which is two places that must
    # agree on it. Populated only for PowerStateShape.PST_ROW; empty for
    # every other shape, which has no per-supply table to record.
    supply_states: dict[str, str] = field(default_factory=dict)
    shape: PowerStateShape = PowerStateShape.ADD_POWER_STATE
    is_default: bool = False  # CPF create_power_mode -default; UPF equivalent not yet verified
    # UPF 2.1 additions to add_power_state, per the version research
    # ("Added -supply, -domain and -complete to add_power_state"). `domain`
    # and `supply` are captured as raw, additional scoping references
    # alongside `applies_to` (the positional object arg) -- not verified as
    # an alternative to it, since exact UPF add_power_state semantics
    # aren't primary-source-confirmable (IEEE 1801 is paywalled).
    domain: Optional[str] = None
    supply: Optional[str] = None
    # UPF `add_power_state -group <g>`: this state belongs to a
    # `create_power_state_group`, i.e. it is a SYSTEM-level state naming
    # other objects' states in its own `-logic_expr`, not a supply's own
    # physical state. A third possible referent alongside `supply` and
    # `domain`, and the one a two-tier model's Tier 2 uses.
    group: Optional[str] = None
    complete: bool = False
    source_loc: Optional[SourceLoc] = None


@dataclass(frozen=True)
class PowerStateGroup:
    """UPF 3.0+ create_power_state_group. No CPF equivalent found in
    research; left empty (never populated) for CPF-sourced designs."""

    id: str
    name: str
    scope: str
    members: list[str] = field(default_factory=list)
    source_loc: Optional[SourceLoc] = None


@dataclass(frozen=True)
class DomainSupplyConnection:
    """UPF connect_supply_net/set_domain_supply_net (roles "primary_power"/
    "primary_ground" for the latter, None for the roleless
    create_supply_net -domain case); CPF's update_power_domain
    -internal_power_net/-internal_ground_net, unified onto the same
    "primary_power"/"primary_ground" roles -- confirmed to be the same
    concept as UPF's primary_power_net/primary_ground_net (both mean "the
    net driving this domain's functional logic"), not just a naming
    coincidence, during the 2b audit. See cpf/verbs.py's
    _update_power_domain for a caveat on how confident that equivalence
    actually is."""

    domain: str
    supply_net: str
    scope: str
    role: Optional[str] = None  # "primary_power"/"primary_ground", or None for the roleless UPF case; raw as authored
    # UPF `connect_supply_net <net> -ports {a b ...}`: the port/pin refs
    # the net is wired to. Was a deliberately-logged Milestone 2a gap
    # ("-ports deliberately not a recognized option") -- closed when
    # round-trip import needed it, since the generator emits this form
    # both for boundary port-to-net wiring and for every macro PG-pin
    # connection. `domain` is "" for this shape: -ports names ports
    # directly and carries no domain at all, unlike the -domain form.
    ports: list[str] = field(default_factory=list)
    source_loc: Optional[SourceLoc] = None


@dataclass(frozen=True)
class PortAttribute:
    """UPF `set_port_attributes -ports {...} -driver_supply <set>` /
    `-receiver_supply <set>`. One record per command occurrence, holding
    the ports it named and whichever supply role it asserted.

    Added for round-trip import: the GUI has authored Port Attributes
    since Milestone 3 and generates this command, but the elaborator had
    no way to read it back -- there was no IR construct for it at all.
    Deliberately NOT folded onto SupplyPort or PowerDomain: the command
    asserts a boundary-facing supply for a *design port*, which is
    neither of those, and CPF expresses the same intent through a
    domain's own -boundary_ports instead (see PowerDomain.boundary_ports)."""

    id: str
    scope: str
    ports: list[str] = field(default_factory=list)
    driver_supply: Optional[str] = None
    receiver_supply: Optional[str] = None
    source_loc: Optional[SourceLoc] = None


@dataclass(frozen=True)
class UnrecognizedConstruct:
    """A command, or a command carrying an option, that the elaborator
    does not recognize. The command is SKIPPED rather than failing the
    whole file, and recorded here so the consumer can report it with its
    own line (UPF only -- see upf.elaborate)."""

    command: str  # the command name, e.g. "set_design_attributes"
    text: str  # the command as written (first line), for the message
    reason: str
    source_loc: Optional[SourceLoc] = None


@dataclass(frozen=True)
class PowerIntentDesign:
    source_format: SourceFormat
    source_version: str  # the single resolved version for the whole design -- see doc's design question 1
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
    # Constructs skipped because they are not recognized -- see
    # UnrecognizedConstruct. Always empty for CPF.
    unrecognized: list[UnrecognizedConstruct] = field(default_factory=list)


def make_power_intent_id(scope: str, name: str) -> str:
    """Deterministic key for a named construct at a given hierarchical scope.

    Unlike rtl.ir.make_definition_id, this is a plain, readable composite
    (not a hash): there is no "parameters" axis of identity here the way an
    RTL module has -- a power domain isn't instantiated multiple times with
    different bindings -- so there is nothing worth hiding behind an opaque
    id, and scope/name are already exposed as separate fields on every
    construct.
    """
    return f"{scope}::{name}"
