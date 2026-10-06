"""THE SUPPORTED-FEATURES LIST -- what this GUI actually models of the
power-intent IR, field by field (explicit request, section 3: "a real
deliverable, not just an implementation detail -- this should be
inspectable/reviewable").

WHY FIELDS, NOT TCL COMMANDS. The obvious formulation -- "list the
UPF/CPF commands we support" -- turns out to be the wrong axis, for two
reasons. The backend elaborator already decides which *commands* parse
(an unknown command is its own, separate outcome). What this list has to
answer is the question section 4 actually asks: a construct parsed
cleanly and its references resolved fine, but does the GUI have anywhere
to PUT it? That is a per-field question about the elaborated IR, not a
per-command question about Tcl syntax. So every field of every IR
construct is classified here as MODELLED or UNMODELLED, with a reason.

WHY THAT MATTERS MORE THAN IT SOUNDS. Because the axis is the IR schema,
the list is machine-checkable against it: `test_supported_features.py`
asserts every field of every IR dataclass appears here exactly once, so
a field added to the backend schema later CANNOT silently become an
invisible gap -- the test fails until someone classifies it. That is the
property that makes this a maintained list rather than a snapshot that
rots, which is the thing section 3 is really asking for.

HOW IT IS USED. The importer walks the elaborated IR and, for every
construct, reports any field that (a) carries a real authored value and
(b) is UNMODELLED here -- as a WARNING, naming the field and why. That
is the new classification section 4 asks for, deliberately distinct from
the existing DANGLING -> ERROR (a reference that doesn't resolve against
the RTL) and from NOT_ATTEMPTED/UNRESOLVABLE_BLACK_BOX (which mean
something else again). Three different reasons, three different
messages; never conflated, never silently dropped.

READING THE `UNMODELLED` ENTRIES. Each one is a deliberate scope
decision with its reason recorded, not an oversight. They fall into a
few honest categories:
  - the concept exists in UPF/CPF but this GUI has no equivalent at all
    (e.g. a domain's shutoff condition -- there is no boolean-expression
    editor anywhere in this app);
  - the GUI derives the same thing live instead of storing it (e.g. an
    Internal Supply's power states, which are traced through the switch
    chain rather than authored);
  - it is provenance or bookkeeping with no user-facing meaning (e.g.
    which Tcl shape a power state arrived in).
A reader deciding whether to implement one should start from the reason,
not from the field name.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class Support(Enum):
    MODELLED = "modelled"  # the GUI has a real place to put this
    UNMODELLED = "unmodelled"  # parsed by the backend, no GUI equivalent -> WARNING when authored


@dataclass(frozen=True)
class FieldSupport:
    """One IR field's status. `gui` names where a MODELLED field lands in
    this app's own diagram model, so the list doubles as the importer's
    own specification; `reason` explains an UNMODELLED one."""

    construct: str  # IR dataclass name, e.g. "PowerDomain"
    field: str  # field name on that dataclass
    support: Support
    gui: Optional[str] = None  # MODELLED: the diagram-model target
    reason: Optional[str] = None  # UNMODELLED: why not


def _m(construct: str, field: str, gui: str) -> FieldSupport:
    return FieldSupport(construct, field, Support.MODELLED, gui=gui)


def _u(construct: str, field: str, reason: str) -> FieldSupport:
    return FieldSupport(construct, field, Support.UNMODELLED, reason=reason)


# Identity/provenance fields every construct carries. Deliberately not
# listed per-construct below: `id` is an opaque backend key the GUI never
# surfaces, and `source_loc` feeds diagnostics rather than the diagram.
UNIVERSAL_FIELDS = ("id", "source_loc")


SUPPORTED_FIELDS: tuple[FieldSupport, ...] = (
    # --- PowerDomain ------------------------------------------------------
    _m("PowerDomain", "name", "PowerDomainPlacement.name"),
    _m("PowerDomain", "scope", "PowerDomainPlacement.owner_paths (resolved against the elaborated RTL)"),
    _m("PowerDomain", "elements", "PowerDomainPlacement.owner_paths -- each element resolves to a hierarchy block"),
    _m("PowerDomain", "exclude_elements", "computed by _direct_excluded_children(); imported value seeds it"),
    _m("PowerDomain", "primary_supply", "PowerDomainPlacement.primary_supply_set"),
    _u("PowerDomain", "shutoff_condition", "no boolean-expression editor exists anywhere in this GUI"),
    _u("PowerDomain", "power_up_state", "CPF power-up vocabulary (high/low/random) has no diagram equivalent"),
    _u("PowerDomain", "default_restore_edge", "Retention save/restore is authored per strategy here, never as a domain default"),
    _u("PowerDomain", "default_save_edge", "a domain-level save-edge default has no GUI home; Retention authors Save Logic per strategy"),
    _u("PowerDomain", "is_default", "CPF's exactly-one-default-domain rule has no representation in the diagram"),
    _m("PowerDomain", "available_supplies", "PowerDomainPlacement.secondary_supply_sets"),
    _u("PowerDomain", "boundary_ports", "CPF-only; this GUI expresses boundary intent via Port Attributes instead"),
    # --- SupplyPort / SupplyNet / SupplySet -------------------------------
    _m("SupplyPort", "name", "SupplyPortPlacement.name (is_internal_supply=False)"),
    _m("SupplyPort", "scope", "SupplyPortPlacement.owner_path"),
    # MODELLED as of the Direction feature. Was UNMODELLED, and the
    # reason recorded then -- "not modelled; placement edge is derived
    # from Ground role instead" -- conflated two separate things: the
    # placement EDGE really is derived from the Ground role and still is,
    # but that was never what `-direction` means. A boundary port's
    # in/out/inout is now a real, editable field.
    _m("SupplyPort", "direction", "SupplyPortPlacement.direction (Supply Port only; \"\" when the file declared none)"),
    _m("SupplyNet", "name", "SupplyPortPlacement.name -- an internal-only net becomes an Internal Supply"),
    _m("SupplyNet", "scope", "SupplyPortPlacement.owner_path"),
    _m("SupplyNet", "voltage", "SupplyPortState.voltage when it carries one"),
    _m("SupplyNet", "reuse", "a net continued from an outer scope -- the same thing this GUI models as a cross-hierarchy auto-linked replica sharing one name"),
    _m("SupplySet", "name", "SupplySetPlacement.name"),
    _m("SupplySet", "scope", "SupplySetPlacement.owner_paths"),
    _m("SupplySet", "power_net", "SupplySetPlacement.power"),
    _m("SupplySet", "ground_net", "SupplySetPlacement.ground"),
    _m("SupplySet", "nwell_net", "SupplySetPlacement.n_well"),
    _m("SupplySet", "pwell_net", "SupplySetPlacement.p_well"),
    # --- PowerSwitch ------------------------------------------------------
    _m("PowerSwitch", "name", "PowerSwitchPlacement.name"),
    _m("PowerSwitch", "scope", "PowerSwitchPlacement.owner_paths"),
    _m("PowerSwitch", "domain", "PowerSwitchPlacement.power_domain_id"),
    _m("PowerSwitch", "input_supply", "PowerSwitchPlacement.input_supply_port"),
    _m("PowerSwitch", "output_supply", "PowerSwitchPlacement.output_supply_port"),
    _m("PowerSwitch", "input_supply_port", "PowerSwitchPlacement.input_supply_port (the switch's own port name)"),
    _m("PowerSwitch", "output_supply_port", "PowerSwitchPlacement.output_supply_port (the switch's own port name)"),
    _m("PowerSwitch", "control_port_pins", "PowerSwitchPlacement.control_port (the PIN half; first entry -- this GUI models one control pin)"),
    _m("PowerSwitch", "ack_ports", "PowerSwitchPlacement.ack_signal -- the \"Ack Signal\" field, the RTL net the acknowledge pin drives"),
    _m("PowerSwitch", "ack_port_pins", "PowerSwitchPlacement.ack_port (the cell's own acknowledge pin name; first entry)"),
    # The NET half of `-control_port {pin net}`. This GUI tracks the
    # switch cell's own PIN name and has no field for the net driving
    # it, so the net is genuinely not carried. Reading it INTO
    # control_port silently relabelled a pin as its net -- see
    # tests/frontend/golden_simple10/README.md, iteration 2.
    # WAS unmodelled ("this GUI tracks the cell pin name, with no field
    # for the net driving it") -- the "Control Signal" field is exactly
    # that missing net, added after the review found there was nowhere to
    # put the real signal driving a switch.
    _m("PowerSwitch", "control_ports", "PowerSwitchPlacement.control_signal -- the \"Control Signal\" field, the RTL net driving the control pin"),
    _m("PowerSwitch", "states", "PowerSwitchPlacement.on_state / .off_state"),
    _m("PowerSwitch", "cell_type", "PowerSwitchPlacement.cell_type (free text since eceb7de)"),
    _u("PowerSwitch", "port_map", "abstract-port-to-cell-pin mapping: retired -- a switch's ports ARE the dialog's own field values now, so nothing needs bridging. Read back if a file carries one, never generated"),
    _m("PowerSwitchState", "name", "the on/off state name"),
    _u("PowerSwitchState", "boolean_expr", "the expression is stored as authored text only; never evaluated or edited structurally"),
    # --- IsolationStrategy ------------------------------------------------
    _m("IsolationStrategy", "name", "IsolationStrategyPlacement.name"),
    _m("IsolationStrategy", "scope", "IsolationStrategyPlacement.owner_paths"),
    _m("IsolationStrategy", "domain", "IsolationStrategyPlacement.power_domain_id"),
    _m("IsolationStrategy", "elements", "IsolationStrategyPlacement.elements"),
    _m("IsolationStrategy", "location", "IsolationStrategyPlacement.location (self/parent)"),
    _m("IsolationStrategy", "name_prefix", "IsolationStrategyPlacement.name_prefix (the Name Prefix field; exported to CPF only -- set_isolation has no confirmed -name_prefix)"),
    _m("IsolationStrategy", "clamp_value", "IsolationStrategyPlacement.clamp_value (0/1/hold <-> low/high/keep)"),
    _m("IsolationStrategy", "isolation_supply", "IsolationStrategyPlacement.isolation_supply_set_id"),
    _m("IsolationStrategy", "isolation_signal", "IsolationStrategyPlacement.isolation_control"),
    _m("IsolationStrategy", "isolation_sense", "IsolationStrategyPlacement.isolation_sense"),
    # CPF's `update_isolation_rules -isolation_ground_net`. Its power
    # counterpart shares `isolation_supply` with UPF's supply-set name, so
    # the ground half is where CPF's separate-rails shape shows through --
    # this GUI references one Supply Set, which carries both rails at
    # once, so a ground net named independently of a power net has nowhere
    # to go.
    _u("IsolationStrategy", "isolation_ground_net", "the GUI references a Supply Set (both rails at once), not an independently-named ground net"),
    # UPF `-source`/`-sink` name a supply SET, which is exactly the
    # dialog's Source/Sink Supply Set -- generated and read back. CPF's
    # `-from`/`-to` land in the same fields as a DOMAIN, which is read
    # only as the domain link (power_intent_import._strategy_domain_id).
    _m("IsolationStrategy", "source", "IsolationStrategyPlacement.source_supply_set_id (UPF -source; CPF's -from is read as the domain link)"),
    _m("IsolationStrategy", "sink", "IsolationStrategyPlacement.sink_supply_set_id (UPF -sink; CPF's -to is read as the domain link)"),
    _u("IsolationStrategy", "exclude_elements", "no exclude-list editor for Isolation elements in this GUI"),
    _u("IsolationStrategy", "condition", "CPF -isolation_condition: no boolean-expression editor exists"),
    _u("IsolationStrategy", "isolation_target", "CPF -isolation_target {from|to}: no diagram equivalent"),
    # With no elements: the dialog's "Applies To All" Inputs/Outputs/Both
    # (IsolationStrategyPlacement.applies_to). Alongside `-elements {a b c}`
    # it describes one selection, so it is RESOLVED INTO the element list.
    # See power_intent_import._elements_matching_applies_to().
    _m("IsolationStrategy", "applies_to", "IsolationStrategyPlacement.applies_to when there are no elements; otherwise matched against IsolationStrategyPlacement.elements"),
    # --- RetentionStrategy ------------------------------------------------
    _m("RetentionStrategy", "name", "RetentionStrategyPlacement.name"),
    _m("RetentionStrategy", "scope", "RetentionStrategyPlacement.owner_paths"),
    _m("RetentionStrategy", "domain", "RetentionStrategyPlacement.power_domain_id"),
    _m("RetentionStrategy", "elements", "RetentionStrategyPlacement.elements"),
    _m("RetentionStrategy", "retention_supply", "RetentionStrategyPlacement.retention_supply_set_id"),
    _m("RetentionStrategy", "primary_supply", "RetentionStrategyPlacement.primary_supply_set_id"),
    _m("RetentionStrategy", "save_signal", "RetentionStrategyPlacement.save_logic"),
    _m("RetentionStrategy", "restore_signal", "RetentionStrategyPlacement.restore_logic"),
    # --- LevelShifterStrategy ---------------------------------------------
    _m("LevelShifterStrategy", "name", "LevelShifterStrategyPlacement.name"),
    _m("LevelShifterStrategy", "scope", "LevelShifterStrategyPlacement.owner_paths"),
    _m("LevelShifterStrategy", "domain", "LevelShifterStrategyPlacement.power_domain_id"),
    _m("LevelShifterStrategy", "elements", "LevelShifterStrategyPlacement.elements"),
    _m("LevelShifterStrategy", "location", "LevelShifterStrategyPlacement.location"),
    _m("LevelShifterStrategy", "name_prefix", "LevelShifterStrategyPlacement.name_prefix (the Name Prefix field; set_level_shifter -name_prefix)"),
    _m("LevelShifterStrategy", "applies_to", "LevelShifterStrategyPlacement.applies_to"),
    _m("LevelShifterStrategy", "rule", "LevelShifterStrategyPlacement.rule"),
    _m("LevelShifterStrategy", "threshold", "LevelShifterStrategyPlacement.threshold"),
    # This app's Level Shifter tracks ONE Power Domain plus a free-text
    # Cell Type; it has no per-side supply-set pair to put these in.
    _u("LevelShifterStrategy", "input_supply", "this GUI's Level Shifter references one Power Domain, not a supply set on each side of the shift"),
    _u("LevelShifterStrategy", "output_supply", "see `input_supply` -- there is no output-side supply-set field either"),
    _m("LevelShifterStrategy", "cell_type", "LevelShifterStrategyPlacement.cell_type (free text since eceb7de)"),
    _u("LevelShifterStrategy", "source", "source/sink domain filtering is not modelled, same as Isolation"),
    _u("LevelShifterStrategy", "sink", "sink-domain filtering is not modelled either; see `source` above -- same reasoning as Isolation"),
    _u("LevelShifterStrategy", "exclude_elements", "no exclude-list editor for Level Shifter elements"),
    # --- PowerState -------------------------------------------------------
    _m("PowerState", "name", "SupplyPortState.name"),
    _m("PowerState", "scope", "resolved to the owning Supply Port/Virtual Supply's own owner_path"),
    _m("PowerState", "applies_to", "selects which Supply Port/Supply Set the state is authored on"),
    _m("PowerState", "supply", "same role as applies_to when present (UPF 2.1 -supply)"),
    _m("PowerState", "expr", "SupplyPortState.voltage, or state_type='off' when the expression is literally 'off'"),
    # A legacy PST row's per-supply values -> one Power State Table
    # row. UPF's create_pst/add_pst_state IS this app's Power State
    # Table; nothing read it back until iteration 5 of the
    # hand-authored fixture exercise.
    _m("PowerState", "supply_states", "PowerStateTableRow.net_states (one row per PST state)"),
    # Read to decide HOW a state is imported: a per-net state, a supply-set
    # state, a legacy PST row, or a named legacy port state each land
    # differently (see power_intent_import._states_for_supply()).
    _m("PowerState", "shape", "selects how the state is imported: per-net, supply-set, PST row or legacy port state"),
    _u("PowerState", "is_default", "CPF create_power_mode -default has no diagram equivalent"),
    _u("PowerState", "domain", "a power state is attached to a supply here, never to a domain directly"),
    _u("PowerState", "complete", "UPF -complete is a completeness assertion about the state set, not state data"),
    # `add_power_state -group <g>` -- a SYSTEM-level state, naming other
    # objects' states in its own -logic_expr. This app GENERATES these
    # (Tier 2 of its own power-state model) from the Power State Table,
    # and reads them back into it: the group's states ARE the table's
    # rows. Not a per-rail fact, which is why it is the group name that
    # is modelled here rather than the state's own contents.
    _m("PowerState", "group", "PowerStateTableRow (a group's states are the Power State Table's own rows)"),
    # --- PowerStateGroup --------------------------------------------------
    # MODELLED as of the two-tier power-state redesign. The old reason
    # ("not modelled at all -- the Power State Table is per-net, not
    # grouped") described the model that redesign replaced: the table is
    # now exactly a group's worth of system-level states, and this app
    # both generates one and reads it back.
    _m("PowerStateGroup", "name", "the design's single global group; its states are the Power State Table's own rows"),
    _u("PowerStateGroup", "scope", "the whole PowerStateGroup construct is unmodelled -- see its `name` entry for the reason"),
    _u("PowerStateGroup", "members", "a group's membership is read from its own states' -group flag, not from a separate member list"),
    # --- DomainSupplyConnection -------------------------------------------
    _m("DomainSupplyConnection", "domain", "the domain whose Supply Set the net is attached to"),
    _m("DomainSupplyConnection", "supply_net", "resolved to a SupplySetPlacement.power/.ground assignment"),
    _m("DomainSupplyConnection", "scope", "resolved against the elaborated RTL"),
    _m("DomainSupplyConnection", "ports", "a power net drawn between two supply icons; a macro PG pin connection when a port is one"),
    _m("DomainSupplyConnection", "role", "primary_power -> SupplySet.power, primary_ground -> SupplySet.ground"),
    # --- PortAttribute ----------------------------------------------------
    _m("PortAttribute", "scope", "resolved against the elaborated RTL; Port Attributes are top-level-only in this GUI"),
    _m("PortAttribute", "ports", "PortAttributeDiagramState.assignments keys (ungrouped per bit for a multi-bit port)"),
    _m("PortAttribute", "driver_supply", "the assigned Supply Set for an OUT/INOUT port's Driver column"),
    _m("PortAttribute", "receiver_supply", "the assigned Supply Set for an IN/INOUT port's Receiver column"),
    # --- PowerIntentDesign (whole-design) ---------------------------------
    _m("PowerIntentDesign", "source_format", "selects UPF vs CPF handling; not stored in the diagram"),
    _m("PowerIntentDesign", "source_version", "recorded for the export default; not stored in the diagram"),
    _m("PowerIntentDesign", "domains", "iterated into PowerDomainPlacements"),
    _m("PowerIntentDesign", "supply_ports", "iterated into SupplyPortPlacements"),
    _m("PowerIntentDesign", "supply_nets", "iterated into SupplyPortPlacements (Internal Supply where internal-only)"),
    _m("PowerIntentDesign", "supply_sets", "iterated into SupplySetPlacements"),
    _m("PowerIntentDesign", "switches", "iterated into PowerSwitchPlacements"),
    _m("PowerIntentDesign", "isolation_strategies", "iterated into IsolationStrategyPlacements"),
    _m("PowerIntentDesign", "retention_strategies", "iterated into RetentionStrategyPlacements"),
    _m("PowerIntentDesign", "level_shifter_strategies", "iterated into LevelShifterStrategyPlacements"),
    _m("PowerIntentDesign", "power_states", "iterated into SupplyPortState lists"),
    _u("PowerIntentDesign", "power_state_groups", "see PowerStateGroup.name -- the construct itself is unmodelled"),
    _m("PowerIntentDesign", "port_attributes", "iterated into PortAttributeDiagramState.assignments"),
    _m("PowerIntentDesign", "domain_supply_connections", "iterated into SupplySet power/ground assignments"),
    _m("PowerIntentDesign", "unrecognized", "each one becomes a mapping diagnostic naming its UPF line"),
    # --- UnrecognizedConstruct --------------------------------------------
    _m("UnrecognizedConstruct", "command", "named in the diagnostic message"),
    _m("UnrecognizedConstruct", "text", "the command's own line, quoted in the diagnostic message"),
    _m("UnrecognizedConstruct", "reason", "why it was skipped, appended to the diagnostic message"),
)


# --- lookup API used by the importer ---------------------------------------

_BY_KEY: dict[tuple[str, str], FieldSupport] = {(f.construct, f.field): f for f in SUPPORTED_FIELDS}


def field_support(construct: str, field: str) -> Optional[FieldSupport]:
    """The classification for one IR field, or None if this list has
    never heard of it. None is itself meaningful -- it means the backend
    schema gained a field nobody has classified, which
    `test_supported_features.py` fails on, so the importer can treat it
    as unmodelled without that silently becoming permanent."""
    return _BY_KEY.get((construct, field))


def is_modelled(construct: str, field: str) -> bool:
    entry = field_support(construct, field)
    return entry is not None and entry.support is Support.MODELLED


def unmodelled_fields(construct: str) -> tuple[FieldSupport, ...]:
    return tuple(f for f in SUPPORTED_FIELDS if f.construct == construct and f.support is Support.UNMODELLED)


def modelled_count() -> int:
    return sum(1 for f in SUPPORTED_FIELDS if f.support is Support.MODELLED)


def unmodelled_count() -> int:
    return sum(1 for f in SUPPORTED_FIELDS if f.support is Support.UNMODELLED)


@dataclass(frozen=True)
class KnownLimitation:
    """A gap that is NOT a per-field classification.

    SUPPORTED_FIELDS answers "does the GUI have anywhere to put this IR
    field". Some real limitations are not shaped like that: they are
    about a concept this app models differently, or an open design
    question with no field to hang a reason on. Those would otherwise go
    unrecorded anywhere a user can see -- which is exactly how a known
    limitation turns into a surprise."""

    title: str
    detail: str


KNOWN_LIMITATIONS: tuple[KnownLimitation, ...] = (
    KnownLimitation(
        "Macro (.lib) retention cannot be expressed",
        "Retention is modelled as an RTL-sequential-element strategy, and adding one to a "
        "macro block is explicitly rejected -- a black box has no extractable sequential "
        "elements to retain. So there is currently NO correct way here to state that a macro "
        "retains its contents across a power-down. The ad-hoc pattern of naming a state "
        "\"RETENTION\" and keeping an internal rail alive via -available_supplies does NOT "
        "express it: real macro retention needs proper Liberty/UPF macro power definitions. "
        "This is a genuine open design question -- how should this app represent a macro "
        "instance retaining state at all? -- not a missing flag, and it is recorded here "
        "rather than left to be rediscovered.",
    ),
    KnownLimitation(
        "-simstate is written on the supply set, never on a power domain",
        "Researched rather than assumed, and deliberately left as is. IEEE 1801 puts simstate "
        "on SUPPLY SET power states -- it defines how logic powered by that supply set behaves "
        "in simulation -- and a power domain's own states carry logic expressions over those, "
        "with no simstate of their own. A domain's `.primary` handle IS a supply set, so "
        "`add_power_state PD.primary -supply` and this app's `add_power_state <set> -supply` "
        "name the same object once the set is associated as primary. Some flows are reported "
        "not to propagate between the two spellings; that is flow-specific, and emitting BOTH "
        "would declare states twice on one object, which 1801 reads as refinement (-update) "
        "rather than as two independent declarations. Not changed on a secondary-source "
        "report alone.",
    ),
)


def render_summary() -> str:
    """A human-readable rendering of the whole list -- the "inspectable/
    reviewable" half of section 3's deliverable. Printed by
    `scripts/show_supported_features.py` so it can be reviewed without
    reading Python."""
    lines: list[str] = []
    lines.append("POWER INTENT IMPORT -- SUPPORTED FEATURES")
    lines.append("=" * 72)
    lines.append(f"{modelled_count()} fields modelled, {unmodelled_count()} deliberately not.")
    lines.append("An UNMODELLED field carrying a real authored value produces a WARNING")
    lines.append("on import -- never a silent drop. See supported_features.py's docstring.")
    lines.append("")
    current = None
    for entry in SUPPORTED_FIELDS:
        if entry.construct != current:
            current = entry.construct
            lines.append(f"{current}")
        if entry.support is Support.MODELLED:
            lines.append(f"    [x] {entry.field:<26} -> {entry.gui}")
        else:
            lines.append(f"    [ ] {entry.field:<26} !! {entry.reason}")
    lines.append("")
    lines.append("KNOWN LIMITATIONS")
    lines.append("=" * 72)
    lines.append("Not per-field gaps -- concepts this app models differently, or open")
    lines.append("design questions. Recorded so they are not rediscovered as surprises.")
    for limitation in KNOWN_LIMITATIONS:
        lines.append("")
        lines.append(f"  * {limitation.title}")
        lines.append(f"    {limitation.detail}")
    return "\n".join(lines)
