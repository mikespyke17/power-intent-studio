"""Translate an elaborated PowerIntentDesign into this app's own diagram
model (spec sections 4 and 6).

This is the genuinely new layer. Parsing, reference resolution and
validation already exist in the backend; what did not exist is the step
from the elaborated IR -- a flat, format-agnostic set of constructs -- to
the richer GUI model, which has concepts the IR has no notion of
(Internal vs. Virtual Supply, Supply Sets shared across domains, diagram
colours and placement, authored vs. inherited power states).

THREE OUTCOMES, NEVER TWO. Section 4 is explicit that "didn't resolve"
and "resolved but we don't model it" are different failures and must not
be conflated:

  - ERROR   -- a reference that doesn't resolve against the elaborated
               RTL (an instance or port path that isn't there). This
               reuses the backend's existing DANGLING classification
               directly rather than re-deriving it.
  - WARNING -- the reference resolved fine, but the construct or field
               isn't something this GUI models. NEW classification,
               deliberately separate from the backend's existing
               NOT_ATTEMPTED/UNRESOLVABLE_BLACK_BOX warnings, which mean
               something else again. Driven entirely by
               supported_features.py, so "what do we support" has one
               answer and it is inspectable.
  - mapped  -- resolved AND modelled: becomes real diagram state.

Nothing is ever silently dropped. A construct this GUI cannot represent
still produces a diagnostic naming the field and why, which is the whole
reason supported_features.py records a reason per unmodelled field.

SCOPE RESOLUTION. IR scopes are authored relative paths ("" for the top,
"u_sub" for a sub-hierarchy); the GUI addresses blocks by full hierarchy
path ("top", "top/u_sub"). `resolve_owner_path()` is the single place
that bridges them, including UPF's `.` meaning "this scope itself".
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Optional

from PySide6.QtGui import QColor

from frontend.src.power_intent_view.import_palette import assign_supply_colors
from frontend.src.power_intent_view.isolation_overlay import (
    ISOLATION_APPLIES_TO_ELEMENTS,
    IsolationDiagramState,
    IsolationStrategyPlacement,
)
from frontend.src.power_intent_view.level_shifter_overlay import LevelShifterDiagramState, LevelShifterStrategyPlacement
from frontend.src.power_intent_view.macro_power_intent_overlay import MacroPowerIntentDiagramState
from frontend.src.power_intent_view.port_attribute_overlay import (
    PortAttributeDiagramState,
    port_attribute_row_keys,
)
from frontend.src.power_intent_view.power_domain_overlay import (
    DOMAIN_FIXED_TRANSPARENCY,
    PowerDomainDiagramState,
    PowerDomainPlacement,
    SupplySetPlacement,
    resolve_supply_set_color_from_placements,
)
from frontend.src.power_intent_view.power_state_table import (
    PowerStateTableDiagramState,
    PowerStateTableRow,
)
from frontend.src.power_intent_view.power_switch_overlay import (
    ABSTRACT_ACK,
    ABSTRACT_CTRL,
    ABSTRACT_IN,
    ABSTRACT_OUT,
    PowerSwitchDiagramState,
    PowerSwitchPlacement,
    power_switch_connector_key,
    substitute_abstract_control,
)
from frontend.src.power_intent_view.retention_overlay import RetentionDiagramState, RetentionStrategyPlacement
from frontend.src.power_intent_view.supply_port_overlay import (
    FULL_ON,
    LEVELLED_STATE_TYPES,
    OFF,
    SUPPLY_STATE_TYPES,
    UNDETERMINED,
    SupplyPortDiagramState,
    SupplyPortPlacement,
    SupplyPortState,
    macro_pg_pin_id,
    state_key,
    state_label,
)
from frontend.src.power_intent_view.supported_features import Support, field_support
from frontend.src.shared.models.diagnostics import Diagnostic
from frontend.src.shared.models.mapping import MappedDesign, ResolutionKind
from frontend.src.shared.models.power_intent import (
    PowerIntentDesign,
    PowerStateShape,
)
from frontend.src.shared.models.rtl import Design, PortDirection
from frontend.src.shared.models.rtl_io import module_io_ports
from frontend.src.shared.models.validation import Severity

# Its OWN source key, not "load_power_intent". DiagnosticsTableModel
# replaces per source, so sharing a key with the load phase would make
# each batch silently erase the other's -- the import is a third phase
# after load and mapping, and its findings sit alongside theirs.
DIAGNOSTIC_SOURCE = "power_intent_import"
UNRESOLVED_CODE = "POWER-INTENT-IMPORT-UNRESOLVED"
UNRECOGNIZED_CODE = "POWER-INTENT-IMPORT-UNRECOGNIZED"
# A UPF command, or an option on one, that the elaborator does not
# recognize: skipped, and reported with its line (see
# unrecognized_syntax_diagnostics()).
UNRECOGNIZED_SYNTAX_CODE = "UPF-SYNTAX-NOT-RECOGNIZED"

# Shown only when a domain's colour genuinely cannot be derived (no
# primary supply set, or its power rail names no imported supply). A
# neutral grey rather than a colour that reads as a real choice --
# nothing about this domain determined it.
_UNDERIVABLE_DOMAIN_COLOR = "#9e9e9e"


@dataclass
class ImportedPowerIntent:
    """Every diagram-only state family the import produces, plus what it
    could not represent. Mirrors exactly the set
    `PowerIntentDiagramView.restore_all_shared_states()` accepts, so
    applying an import is the same operation as loading a project."""

    supply: SupplyPortDiagramState = field(default_factory=SupplyPortDiagramState)
    domain: PowerDomainDiagramState = field(default_factory=PowerDomainDiagramState)
    retention: RetentionDiagramState = field(default_factory=RetentionDiagramState)
    port_attribute: PortAttributeDiagramState = field(default_factory=PortAttributeDiagramState)
    power_switch: PowerSwitchDiagramState = field(default_factory=PowerSwitchDiagramState)
    isolation: IsolationDiagramState = field(default_factory=IsolationDiagramState)
    level_shifter: LevelShifterDiagramState = field(default_factory=LevelShifterDiagramState)
    macro_power_intent: MacroPowerIntentDiagramState = field(default_factory=MacroPowerIntentDiagramState)
    # The NINTH family, and the one `restore_all_shared_states()` does
    # NOT take: the Power State Table is owned by its own tab rather than
    # the diagram (see power_state_table_tab.py), so applying an import
    # hands this one to that tab separately.
    power_state_table: PowerStateTableDiagramState = field(default_factory=PowerStateTableDiagramState)
    diagnostics: list[Diagnostic] = field(default_factory=list)
    # (construct name, raw reference) pairs a builder already reported an
    # ERROR for. The backend's own DANGLING sweep sees many of the same
    # references, and reporting one fact twice reads as two problems --
    # this is what the sweep filters itself against. A pair recorded here
    # always has a matching diagnostic in `diagnostics`; it is the KEY,
    # not a second copy of the finding.
    reported_references: set[tuple[str, str]] = field(default_factory=set)
    # Supply state TYPE tokens that were outside this app's own
    # vocabulary (FULL_ON/PARTIAL_ON/OFF/UNDETERMINED) and carried a
    # level, so _rail_state() read them as FULL_ON on a best-effort
    # basis. Collected rather than reported inline because the same
    # token typically recurs across many rails, and one finding per
    # distinct token is the honest count -- see the sweep that drains
    # this in import_power_intent().
    reinterpreted_state_types: set[str] = field(default_factory=set)

    @property
    def errors(self) -> list[Diagnostic]:
        return [d for d in self.diagnostics if d.severity is Severity.ERROR]

    @property
    def warnings(self) -> list[Diagnostic]:
        return [d for d in self.diagnostics if d.severity is Severity.WARNING]


# --- scope / path resolution ------------------------------------------------


def rtl_top_path(rtl: Design) -> str:
    """The elaborated design's own top instance path -- the root every
    GUI owner_path starts from. Read from the top Definition's base_name,
    never from `design.top` itself, which is a definition ID carrying a
    parameter hash (e.g. "top__42988143"); confusing the two is a bug
    this codebase has already hit once, in the connectivity resolver."""
    return rtl.definitions[rtl.top].base_name


def resolve_owner_path(rtl: Design, scope: str, element: Optional[str] = None) -> str:
    """An IR scope (+ optional element) -> a GUI hierarchy path.

    IR scopes are authored relative to the design root and use "" for the
    top; the GUI addresses blocks by full path from the top instance.
    UPF's `.` means "this scope itself" and appears wherever a construct
    applies to its own scope rather than a child -- `-elements {.}` on a
    top-level domain, for instance."""
    parts = [rtl_top_path(rtl)]
    if scope:
        parts.append(scope)
    if element and element != ".":
        parts.append(element)
    return "/".join(parts)


def path_exists(rtl: Design, path: str) -> bool:
    return any(node.path == path for node in rtl.walk())


def is_macro(rtl: Design, path: str) -> bool:
    """Whether a hierarchy path names a black-box (.lib) macro. The
    macro-vs-soft-hierarchy distinction section 5 turns on -- see
    `import_power_intent`'s handling of load_upf."""
    for node in rtl.walk():
        if node.path == path:
            return bool(node.definition.is_black_box)
    return False


# --- diagnostics -------------------------------------------------------------


def _diagnostic(severity: Severity, code: str, message: str, construct) -> Diagnostic:
    loc = getattr(construct, "source_loc", None)
    return Diagnostic(
        severity=severity,
        code=code,
        message=message,
        file=getattr(loc, "file", None),
        line=getattr(loc, "line", None),
        source=DIAGNOSTIC_SOURCE,
    )


# Reference fields where an IMPORT BUILDER, not the generic mapper, is
# the authority -- excluded from the DANGLING sweep below. Each entry is
# a measured case, found by round-tripping this app's own generated
# output (tests/frontend/test_power_intent_round_trip.py), not a
# precaution: the mapper resolves every one of these as an RTL
# hierarchy path relative to the construct's own scope, which is not
# what the reference means in this app's dialect.
_BUILDER_OWNED_FIELDS: dict[tuple[str, str], str] = {
    # `set_isolation`/`set_level_shifter -elements` are PORTS OF THE
    # REFERENCED DOMAIN, resolved relative to that domain -- not to the
    # strategy's own scope. Confirmed against the hand-written
    # examples/upf_example_1_sram/upf/golden.upf, whose `set_isolation
    # -domain PD_SUB -elements {o_q[31:0]}` names a bare port of a
    # domain one level down while the command itself sits at root scope.
    ("IsolationStrategy", "elements"): "domain-relative port names, not scope-relative paths",
    ("LevelShifterStrategy", "elements"): "domain-relative port names, not scope-relative paths",
    # `set_retention -elements` are SEQUENTIAL ELEMENTS (registers). The
    # RTL resolver only ever resolves instances, ports and nets, so a
    # register can never resolve through it -- by construction, not
    # because a given name is wrong.
    ("RetentionStrategy", "elements"): "sequential elements; the RTL resolver resolves only instances/ports/nets",
    # A switch's input/output supply is a SUPPLY NET, looked up by name.
    # The mapper's by-name lookup is scope-qualified, so a switch written
    # at root scope referencing a net declared in a nested scope reads as
    # dangling even though both exist -- the builder matches on the name
    # it actually imported.
    ("PowerSwitch", "output_supply"): "a supply net resolved by name, not an RTL path",
    ("PowerSwitch", "input_supply"): "a supply net resolved by name, not an RTL path",
    # `-control_port {CTRL <net>}`'s net half IS a real RTL signal, and
    # the GUI now has a field for it ("Control Signal"). It is still
    # builder-owned rather than mapper-resolved: the builder imports the
    # name as authored, and the generic RTL resolver would report a
    # perfectly valid control signal as dangling simply because it
    # resolves paths, not signal names.
    ("PowerSwitch", "control_ports"): "an RTL signal name imported as authored into the Control Signal field",
    ("PowerSwitch", "ack_ports"): "an RTL signal name imported as authored into the Ack Signal field",
    # Every strategy's own domain link. The builders resolve these by
    # name against the domains they actually imported
    # (`domain_ids`/`_strategy_domain_id`), and report a miss themselves.
    # The mapper's own by-name lookup is scope-qualified, so a strategy
    # declared in a nested scope referencing a root-scope domain -- or a
    # CPF rule naming its domain through `-from` rather than `-domain` --
    # reads as dangling even though both exist.
    ("PowerSwitch", "domain"): "resolved by name against the imported domains",
    ("IsolationStrategy", "domain"): "resolved by name against the imported domains",
    ("IsolationStrategy", "source"): "CPF's -from; read as the domain link, resolved by name",
    ("IsolationStrategy", "sink"): "CPF's -to; read as the domain link, resolved by name",
    ("LevelShifterStrategy", "domain"): "resolved by name against the imported domains",
    ("LevelShifterStrategy", "source"): "CPF's -from; read as the domain link, resolved by name",
    ("LevelShifterStrategy", "sink"): "CPF's -to; read as the domain link, resolved by name",
    ("RetentionStrategy", "domain"): "resolved by name against the imported domains",
}


def unresolved_diagnostics(
    mapped: Optional[MappedDesign],
    already_reported: Optional[set[tuple[str, str]]] = None,
) -> list[Diagnostic]:
    """ERROR per reference that doesn't resolve against the RTL.

    Reuses the backend's own DANGLING classification rather than
    re-deriving "does this path exist" here -- the resolver already did
    that work, and a second implementation would be free to disagree with
    it. NOT_ATTEMPTED and UNRESOLVABLE_BLACK_BOX are deliberately NOT
    swept in: they are the backend's own separate warning kinds and mean
    something different (see backend/mapping/schema.py).

    Two things are filtered out, both for the same reason -- one fact
    should be reported once, by whichever layer actually understands it:

    `already_reported` drops a reference a builder has already reported
    in its own, more specific terms (which path it looked for, and what
    the import did about it) -- see
    ImportedPowerIntent.reported_references. A construct id here is
    scope-qualified ("::PD_EMPTY") while a builder reports the bare name,
    so the comparison is on the trailing segment.

    `_BUILDER_OWNED_FIELDS` drops a whole field whose meaning the generic
    mapper gets wrong for this dialect; see that table's own per-entry
    reasons."""
    if mapped is None:
        return []
    covered = already_reported or set()
    out: list[Diagnostic] = []
    for ref in mapped.references:
        if ref.kind is not ResolutionKind.DANGLING:
            continue
        if not ref.raw:
            # An absent reference is not a broken one. `connect_supply_net
            # -ports` legitimately has no domain, and the mapper resolves
            # that empty string rather than skipping it.
            continue
        if (ref.location.construct_kind, ref.location.field) in _BUILDER_OWNED_FIELDS:
            continue
        bare_name = str(ref.location.construct_id).rsplit("::", 1)[-1]
        if (bare_name, ref.raw) in covered:
            continue
        where = f"{ref.location.construct_kind} '{ref.location.construct_id}'"
        out.append(
            Diagnostic(
                severity=Severity.ERROR,
                code=UNRESOLVED_CODE,
                message=(
                    f"{where}: {ref.location.field} references '{ref.raw}', which does not exist "
                    "in the elaborated design."
                ),
                file=None,
                line=None,
                source=DIAGNOSTIC_SOURCE,
            )
        )
    return out


# An UNMODELLED field that carries the SAME value as a MODELLED
# companion field is not a dropped fact -- the two just came from one
# ambiguous piece of syntax, and the value did get imported.
#
# The only case so far, and a real one: `-control_port <x>` in its bare
# (single-value) form is ambiguous, and the elaborator records it as
# both the pin and the net, because both readings genuinely occur (see
# upf/verbs.py::_control_port_net). The pin is MODELLED, the net is not,
# so warning "the net was dropped" on a bare value would fire on this
# app's own generated output while nothing was actually lost.
_REDUNDANT_WHEN_EQUAL_TO: dict[tuple[str, str], str] = {
    ("PowerSwitch", "control_ports"): "control_port_pins",
}


def _is_redundant_with_a_modelled_field(construct, field_name: str, value) -> bool:
    companion = _REDUNDANT_WHEN_EQUAL_TO.get((type(construct).__name__, field_name))
    if companion is None:
        return False
    return value == getattr(construct, companion, None)


def _is_authored(construct, field_name: str) -> bool:
    """Whether a field carries something the file actually said, as
    opposed to the schema default. Only an authored value is worth
    warning about -- reporting every unmodelled field on every construct
    would bury the real findings.

    Compared against the field's own DEFAULT, not just against
    emptiness. An enum field with a default (PowerState.shape) is never
    None or empty, so an emptiness test alone reported it as authored on
    every single state -- four false warnings on the very first
    round-trip run, which is what surfaced this."""
    import dataclasses

    value = getattr(construct, field_name, None)
    if value is None or value is False:
        return False
    if isinstance(value, (list, tuple, dict, str)) and len(value) == 0:
        return False
    if _is_redundant_with_a_modelled_field(construct, field_name, value):
        return False
    for spec in dataclasses.fields(construct):
        if spec.name != field_name:
            continue
        if spec.default is not dataclasses.MISSING and value == spec.default:
            return False
        if spec.default_factory is not dataclasses.MISSING and value == spec.default_factory():
            return False
    return True


def unrecognized_diagnostics(power_intent: PowerIntentDesign) -> list[Diagnostic]:
    """WARNING per authored field this GUI does not model.

    The NEW classification section 4 asks for. Entirely driven by
    supported_features.py -- this function contains no opinion of its own
    about what is supported, so the list stays the single answer to that
    question."""
    out: list[Diagnostic] = []
    collections = (
        power_intent.domains, power_intent.supply_ports, power_intent.supply_nets,
        power_intent.supply_sets, power_intent.switches, power_intent.isolation_strategies,
        power_intent.retention_strategies, power_intent.level_shifter_strategies,
        power_intent.power_states, power_intent.power_state_groups, power_intent.port_attributes,
    )
    seen: set[tuple[str, str, str]] = set()
    for collection in collections:
        for construct in collection.values():
            kind = type(construct).__name__
            for field_name in (f.name for f in _fields_of(construct)):
                if field_name in ("id", "source_loc"):
                    continue
                entry = field_support(kind, field_name)
                if entry is not None and entry.support is not Support.UNMODELLED:
                    continue
                if not _is_authored(construct, field_name):
                    continue
                name = getattr(construct, "name", getattr(construct, "id", "?"))
                key = (kind, field_name, name)
                if key in seen:
                    continue
                seen.add(key)
                reason = entry.reason if entry is not None else "this field is not classified in supported_features.py"
                out.append(
                    _diagnostic(
                        Severity.WARNING,
                        UNRECOGNIZED_CODE,
                        f"{kind} '{name}': '{field_name}' is not modelled by this tool and was not imported -- {reason}",
                        construct,
                    )
                )
    return out


def _fields_of(obj):
    import dataclasses

    return dataclasses.fields(obj)


# --- building the diagram model ---------------------------------------------
#
# Ordering matters and is not arbitrary. Supplies come first because
# Supply Sets reference them by name; Supply Sets before Power Domains
# because a domain names its primary set; domains before every strategy,
# because each strategy names its domain. A construct whose referent is
# missing is skipped with a diagnostic rather than being materialized
# half-wired -- the GUI's own invariants assume a strategy's domain
# exists, and a dangling placement would break the diagram rather than
# merely look wrong.


_CLAMP_FROM_UPF = {"0": "low", "1": "high", "hold": "keep", "latch": "keep"}

# UPF `set_retention_control -save_signal/-restore_signal {signal sense}`
# back to the dialog's own vocabulary -- the reverse of
# power_intent_text_export._retention_signal(): `{enable high}` ->
# `enable`, `{enable low}` -> `!enable`. An edge sense (posedge/negedge)
# has no dialog equivalent and passes through as authored.
_SIGNAL_SENSE = re.compile(r"^(\S+)\s+(high|low)$")


def _retention_logic(signal: Optional[str]) -> Optional[str]:
    if not signal:
        return signal
    match = _SIGNAL_SENSE.match(signal.strip())
    if match is None:
        return signal
    name, sense = match.groups()
    return name if sense == "high" else f"!{name}"


def _new_id() -> str:
    return uuid.uuid4().hex


# A supply-set state's own `-supply_expr` term:
#   power  == {FULL_ON 1.2}      -> that set's POWER rail, state FULL_ON at 1.2
#   ground == {FULL_ON 0.0}      -> that set's GROUND rail, state FULL_ON at 0.0
#   power  == OFF                -> that set's POWER rail, state OFF, no level
# `function == value` where the function names one of the set's own rails.
_SUPPLY_EXPR_TERM = re.compile(
    r"(?P<function>\w+)\s*==\s*(?:\{\s*(?P<braced>[^}]*?)\s*\}|(?P<bare>[\w.]+))"
)
# Which SupplySet field each supply-set function handle names.
_SUPPLY_FUNCTION_FIELDS = {
    "power": "power_net",
    "ground": "ground_net",
    "nwell": "nwell_net",
    "pwell": "pwell_net",
}


def _find_supply_set_reference(power_intent: PowerIntentDesign, reference: Optional[str]):
    """A supply-set reference as a root-scope command writes it.

    A set declared inside a nested `set_scope` block is named from the
    root by a qualified reference -- `u_core/SS_INT`. This app's own
    generator no longer writes that form (it declares every set at the
    root and names it bare), but older exports and hand-written files
    do, so reading them back means resolving both spellings.

    The qualified form matches on (scope, name), which is strictly
    better than the bare-name lookup everywhere else here: two sets with
    the same name in different scopes stay distinct. A bare reference
    falls back to matching on the tail alone, since that is all an
    unqualified file ever gives."""
    if not reference:
        return None
    scope, _, name = reference.rpartition("/")
    if scope:
        for construct in power_intent.supply_sets.values():
            if construct.name == name and (construct.scope or "") == scope:
                return construct
    return _find_by_name(power_intent.supply_sets, name)


def _supply_set_state_terms(expr: str, reinterpreted: Optional[set] = None) -> dict[str, SupplyPortState]:
    """A `-supply_expr` decomposed into one state per supply FUNCTION.

    `power == {FULL_ON 1.2} && ground == {FULL_ON 0.0}` yields
    `{"power": FULL_ON@1.2, "ground": FULL_ON@0.0}`. The per-rail state
    NAME comes from inside the term, not from the set-level state's own
    name -- `-state {ON -supply_expr {power == {FULL_ON 1.2} ...}}`
    declares a rail state called FULL_ON, not ON."""
    terms: dict[str, SupplyPortState] = {}
    for match in _SUPPLY_EXPR_TERM.finditer(expr or ""):
        function = match.group("function").lower()
        if function not in _SUPPLY_FUNCTION_FIELDS:
            continue
        value = match.group("braced") if match.group("braced") is not None else match.group("bare")
        parts = (value or "").split()
        if not parts:
            continue
        terms[function] = _rail_state(parts[0], parts[1] if len(parts) > 1 else None, reinterpreted)
    return terms


def _rail_state(token: str, level: Optional[str], reinterpreted: Optional[set] = None) -> SupplyPortState:
    """One UPF rail state -> one SupplyPortState.

    Direct, with no translation: UPF writes a rail state as
    `{FULL_ON 1.2}` / `OFF` / `UNDETERMINED`, and that leading token IS
    this app's own state-type vocabulary now (see SupplyPortState's own
    docstring). A token outside the vocabulary is kept as a levelled
    state when it came with a level and treated as UNDETERMINED
    otherwise -- neither invents a level that was not written.

    `reinterpreted` collects any token that had to go through the
    best-effort fallback, so the caller can SAY SO. The fallback itself
    is unchanged and deliberately so -- reading an unrecognized
    level-bearing token as FULL_ON is a reasonable default, and whether
    a name like `ON` is legal UPF is a separate open question. What was
    wrong was doing it silently: a real reference file uses `ON`, and
    the state arrived as FULL_ON with nothing anywhere saying a
    substitution had happened."""
    upper = (token or "").upper()
    if upper in SUPPLY_STATE_TYPES:
        if upper in LEVELLED_STATE_TYPES:
            return SupplyPortState(state_type=upper, voltage=level)
        return SupplyPortState(state_type=upper)
    if level is not None:
        if reinterpreted is not None and token:
            reinterpreted.add(token)
        return SupplyPortState(state_type=FULL_ON, voltage=level)
    return SupplyPortState(state_type=UNDETERMINED)


def _port_state(power_state) -> SupplyPortState:
    """One legacy `-state {NAME value}` -> a NAMED SupplyPortState: the
    value is a voltage, or "off". (A legacy port state carries no
    FULL_ON/PARTIAL_ON distinction; FULL_ON is the generator's own.)"""
    value = (power_state.expr or "").strip()
    if value.lower() in ("", "off"):
        return SupplyPortState(state_type=OFF, name=power_state.name)
    return SupplyPortState(state_type=FULL_ON, voltage=value, name=power_state.name)


def _named_state_keys(power_intent: PowerIntentDesign) -> dict[tuple[str, str], str]:
    """{(supply, state NAME): state_key} from the legacy port states -- the
    translation a legacy PST row needs, since its cells name a supply's
    state by NAME while the Power State Table stores its key."""
    return {
        (ps.applies_to, ps.name): state_key(_port_state(ps))
        for ps in power_intent.power_states.values()
        if ps.shape is PowerStateShape.PORT_STATE and ps.applies_to
    }


def _states_for_supply(power_intent: PowerIntentDesign, supply_name: str, reinterpreted: Optional[set] = None) -> list[SupplyPortState]:
    """The authored power states for one supply, in declaration order.

    TWO shapes reach a supply, and only the first used to:

      - PER-NET: `add_power_state VDD -state {ON 1.2}` names the net
        directly. UPF writes the level in `expr`, with the literal "off"
        marking the off state -- the same split this app's own
        SupplyPortState makes, so the translation is direct.
      - PER-SUPPLY-SET: `add_power_state -supply SS_VDD -state {ON
        -supply_expr {power == {FULL_ON 1.2} && ground == {FULL_ON 0.0}}}`
        declares the state on the SET, and its `-supply_expr` says what
        each of the set's own rails is doing. That is equivalent to
        writing `add_power_state VDD -state {FULL_ON 1.2}` and
        `add_power_state VSS -state {FULL_ON 0.0}` separately, so it is
        decomposed into exactly those per-rail states here.

    Without the second, a file declaring its states the supply-set way --
    which is the idiomatic UPF 3.x style -- imported with NO states on
    any supply at all, silently: the states existed in the IR, attached
    to a set name that no supply lookup would ever match."""
    states: list[SupplyPortState] = []
    for power_state in power_intent.power_states.values():
        if power_state.shape is PowerStateShape.PORT_STATE:
            # Legacy `add_port_state`/`add_supply_state`: a NAMED state.
            if power_state.applies_to == supply_name:
                states.append(_port_state(power_state))
            continue
        target = power_state.applies_to or power_state.supply
        if target == supply_name:
            expr = (power_state.expr or "").strip()
            # A per-net state names its type and carries its level in
            # `expr`: `-state {FULL_ON 1.2}` -> ("FULL_ON", "1.2"), and
            # `-state {OFF}` -> ("OFF", None). The older `-state {OFF off}`
            # spelling (level literally the word "off") still reads as
            # OFF, since the type token alone decides.
            level = None if expr.lower() in ("", "off") else expr
            states.append(_rail_state(power_state.name, level, reinterpreted))
            continue
        supply_set = _find_supply_set_reference(power_intent, target)
        if supply_set is None:
            continue
        for function, rail_state in _supply_set_state_terms(power_state.expr or "", reinterpreted).items():
            if getattr(supply_set, _SUPPLY_FUNCTION_FIELDS[function], None) == supply_name:
                states.append(rail_state)
    # One rail can be named by several set-level states (a set's ground
    # is typically FULL_ON in every one of them), so the same rail state
    # arrives repeatedly -- keep the first of each. Deduplicated by
    # `state_key()`, the canonical (type, level) identity, so FULL_ON 1.2
    # and PARTIAL_ON 1.2 stay distinct rather than collapsing.
    unique: list[SupplyPortState] = []
    seen: set[str] = set()
    for candidate in states:
        key = state_key(candidate)
        if key not in seen:
            seen.add(key)
            unique.append(candidate)
    return unique


def _supply_roles(power_intent: PowerIntentDesign) -> tuple[list[str], list[str]]:
    """(power names, ground names), power ordered so the supplies backing
    a DOMAIN's primary supply set come first -- see
    import_palette.assign_supply_colors() for why that ordering is what
    makes domains distinguishable.

    TWO SOURCES, not one. A UPF file states the roles on its supply sets.
    A CPF file has no supply-set construct at all and states exactly the
    same thing per domain instead (`update_power_domain
    -primary_power_net/-primary_ground_net`, which the elaborator
    normalises into DomainSupplyConnections).

    Reading only the supply sets meant NO name was classified as power
    for any CPF file, so every supply fell back to the neutral grey and
    every domain came out the same colour -- the exact opposite of
    section 6's one hard requirement, and invisible until a fixture was
    hand-authored in real CPF idiom (see
    tests/frontend/golden_simple10/README.md, iteration 4). The connections
    cannot simply replace the sets either: this runs BEFORE
    build_supply_sets(), which is where a CPF set gets synthesised, so
    the raw connections are the only thing available at this point."""
    primary_first: list[str] = []
    grounds: list[str] = []

    def add_power(name: Optional[str]) -> None:
        if name and name not in primary_first:
            primary_first.append(name)

    def add_ground(name: Optional[str]) -> None:
        if name and name not in grounds:
            grounds.append(name)

    # A domain's own primary rails first, whichever way the file said it.
    for domain in power_intent.domains.values():
        supply_set = _find_by_name(power_intent.supply_sets, domain.primary_supply)
        if supply_set is not None:
            add_power(supply_set.power_net)
            add_ground(supply_set.ground_net)
            continue
        for connection in power_intent.domain_supply_connections:
            if connection.domain != domain.name:
                continue
            if connection.role == "primary_power":
                add_power(connection.supply_net)
            elif connection.role == "primary_ground":
                add_ground(connection.supply_net)
    # Then everything else any supply set names.
    for supply_set in power_intent.supply_sets.values():
        add_power(supply_set.power_net)
        add_ground(supply_set.ground_net)
    return primary_first, grounds


def _find_by_name(collection: dict, name: Optional[str]):
    if not name:
        return None
    for construct in collection.values():
        if construct.name == name:
            return construct
    return None


def _boundary_port_for_net(net_name: str, power_intent: PowerIntentDesign, port_names: set[str]):
    """The declared supply PORT a net crosses the boundary through, or
    None if it does not.

    Two ways a net can be a boundary net, and only the first was
    recognised before a real, idiomatic file was reviewed:

      - SAME NAME as a `create_supply_port`. This is what this app's own
        generator writes (`create_supply_port VDD` +
        `create_supply_net VDD` + a self-connect), so it was the only
        case the round trip ever exercised.
      - CONNECTED to one by `connect_supply_net <net> -ports {PORT}`,
        where the port and the net are DIFFERENTLY named -- e.g.
        `create_supply_port VDD_IN -direction in` feeding
        `create_supply_net VDD_ALWAYS_ON`. Completely ordinary UPF, and
        arguably the more natural style, since it lets the boundary pin
        and the internal rail carry different names.

    Missing the second case made EVERY net in such a file import as an
    Internal Supply -- wrong on its own terms (an Internal Supply belongs
    on an inner block, not the design top) -- and silently dropped the
    port's `-direction`, because that lookup was by name too."""
    for connection in power_intent.domain_supply_connections:
        if connection.supply_net != net_name:
            continue
        for port in connection.ports:
            bare = port.rsplit("/", 1)[-1]
            if bare in port_names:
                return _find_by_name(power_intent.supply_ports, bare)
    if net_name in port_names:
        return _find_by_name(power_intent.supply_ports, net_name)
    return None


def _net_direction(net_name: str, power_intent: PowerIntentDesign, port_names: set[str]) -> str:
    """The `-direction` of whichever boundary port this net crosses
    through, or "" when it has none or declared none."""
    port = _boundary_port_for_net(net_name, power_intent, port_names)
    return (port.direction or "") if port is not None else ""


def _is_internal_supply(net, power_intent: PowerIntentDesign, port_names: set[str]) -> bool:
    """Whether a declared net is an Internal Supply rather than a
    boundary Supply Port.

    UPF says it explicitly -- a net accompanied by a `create_supply_port`
    of the same name crosses the boundary; one without does not. So the
    port declaration is read as the file's own statement of intent. (A
    CPF-specific rule lived here while CPF could be mapped; it no longer
    can -- see load_power_intent_tab.py.)"""
    if bool(net.scope):
        return True
    return _boundary_port_for_net(net.name, power_intent, port_names) is None


# FLAT, ROOT-SCOPED files. This app's own generator declares every UPF
# construct at the root scope (see the FLAT, ROOT-SCOPED note in
# power_intent_text_export.py), and so do many hand-written files. A
# root-declared supply net or set then carries no scope to say which
# hierarchy it belongs to -- but the domains do, through their
# root-relative `-elements`. These helpers recover the hierarchy from
# them; a nested `set_scope` declaration still wins wherever a file has
# one.


def _domain_owner_paths(rtl: Design, domain) -> list[str]:
    """The blocks a domain covers, resolved the same way build_domains()
    resolves them (unresolvable elements are dropped here; build_domains
    reports them)."""
    paths: list[str] = []
    for element in domain.elements or ["."]:
        owner_path = resolve_owner_path(rtl, domain.scope, element)
        if path_exists(rtl, owner_path) and owner_path not in paths:
            paths.append(owner_path)
    return paths


def _shallowest(candidates: list[list[str]]) -> Optional[list[str]]:
    """The candidate owner-path list sitting highest in the hierarchy
    (ties keep file order)."""
    candidates = [c for c in candidates if c]
    if not candidates:
        return None
    return min(candidates, key=lambda paths: min(p.count("/") for p in paths))


def _flat_supply_set_owner_paths(power_intent: PowerIntentDesign, rtl: Design, set_name: str) -> Optional[list[str]]:
    """Where a root-declared supply set belongs: with the domain it is
    the PRIMARY supply of -- the hierarchy Create Power Domain composes a
    set on. A set shared further down as a secondary (`-available_supplies`)
    stays with its primary domain. The shallowest such domain wins if
    several share it as primary; None (keep the root) when none does."""
    primary = _shallowest([
        _domain_owner_paths(rtl, domain)
        for domain in power_intent.domains.values()
        if domain.primary_supply == set_name
    ])
    if primary is not None:
        return primary
    # Not anyone's primary: a set composed for a domain as one of its
    # SECONDARY supplies (`-available_supplies`) lives with that domain.
    return _shallowest([
        _domain_owner_paths(rtl, domain)
        for domain in power_intent.domains.values()
        if set_name in domain.available_supplies
    ])


def _flat_internal_supply_owner_path(power_intent: PowerIntentDesign, rtl: Design, net_name: str) -> Optional[str]:
    """Where a root-declared Internal Supply belongs: the hierarchy of
    what it powers -- the domain of a switch driving it, or the
    (recovered) hierarchy of a supply set using it as a rail. An Internal
    Supply always sits on an inner block, so a root-only answer is no
    answer (None)."""
    domains_by_name = {d.name: d for d in power_intent.domains.values()}
    candidates: list[list[str]] = []
    for switch in power_intent.switches.values():
        if switch.output_supply == net_name and switch.domain in domains_by_name:
            candidates.append(_domain_owner_paths(rtl, domains_by_name[switch.domain]))
    for supply_set in power_intent.supply_sets.values():
        rails = (supply_set.power_net, supply_set.ground_net, supply_set.nwell_net, supply_set.pwell_net)
        if net_name in rails:
            candidates.append(_flat_supply_set_owner_paths(power_intent, rtl, supply_set.name) or [])
    root = rtl_top_path(rtl)
    inner = [[p for p in paths if p != root] for paths in candidates]
    best = _shallowest(inner)
    return best[0] if best else None


def build_supplies(power_intent: PowerIntentDesign, rtl: Design, result: ImportedPowerIntent) -> dict[str, str]:
    """Supply ports and nets -> SupplyPortPlacements. Returns
    {supply name: placement id} for later constructs to reference.

    A net declared at the top scope with a matching port is a boundary
    Supply Port; one declared deeper, or with no port of the same name,
    is an Internal Supply -- which is exactly the boundary-vs-internal
    distinction UPF draws by whether `create_supply_port` accompanies
    `create_supply_net`, so this reads the file's own intent rather than
    guessing."""
    power_names, ground_names = _supply_roles(power_intent)
    colors = assign_supply_colors(power_names, ground_names)
    port_names = {p.name for p in power_intent.supply_ports.values()}
    # `create_supply_port -direction` lives on the PORT, while a diagram
    # supply is built from the matching NET, so the two have to be
    # associated -- by `connect_supply_net -ports` OR by a shared name,
    # whichever the file used (see _boundary_port_for_net). A port that
    # declared no direction leaves the placement at "", which is the
    # correct imported value, distinct from the "in" a NEWLY created port
    # defaults to (see SupplyPortPlacement.direction).

    by_name: dict[str, str] = {}
    for net in power_intent.supply_nets.values():
        owner_path = resolve_owner_path(rtl, net.scope)
        if not net.scope and _is_internal_supply(net, power_intent, port_names):
            owner_path = _flat_internal_supply_owner_path(power_intent, rtl, net.name) or owner_path
        if not path_exists(rtl, owner_path):
            result.diagnostics.append(
                _diagnostic(
                    Severity.ERROR, UNRESOLVED_CODE,
                    f"SupplyNet '{net.name}': scope '{net.scope or '.'}' does not resolve to a block in the "
                    f"elaborated design (looked for '{owner_path}').",
                    net,
                )
            )
            result.reported_references.add((net.name, net.scope))
            continue
        is_internal = _is_internal_supply(net, power_intent, port_names)
        placement_id = _new_id()
        result.supply.supply_port_placements[placement_id] = SupplyPortPlacement(
            id=placement_id,
            owner_path=owner_path,
            name=net.name,
            color=QColor(colors.get(net.name, QColor("#9e9e9e"))),
            is_internal_supply=is_internal,
            # Only a boundary Supply Port carries authored states; an
            # Internal Supply's are derived live by tracing the switch
            # chain (see resolve_effective_states()), so seeding them here
            # would create a second, conflicting source of truth. When the
            # file DID author states for such a net, that is reported
            # below rather than silently discarded.
            states=[] if is_internal else _states_for_supply(power_intent, net.name, result.reinterpreted_state_types),
            # Supply Port only: an Internal Supply is not a boundary
            # port, so even a same-named `create_supply_port` upstream
            # does not give it a direction of its own.
            direction="" if is_internal else _net_direction(net.name, power_intent, port_names),
        )
        by_name[net.name] = placement_id
    return by_name


def build_supply_sets(power_intent: PowerIntentDesign, rtl: Design, result: ImportedPowerIntent) -> dict[str, str]:
    """SupplySets -> SupplySetPlacements, keyed back by IR name.

    One placement per IR supply set even when several domains reference
    it -- sharing is by id in this app's model, so the shared-set case
    (CHANGE 2's ancestor sharing) round-trips as one object rather than
    being duplicated per referencing domain."""
    by_name: dict[str, str] = {}
    for supply_set in power_intent.supply_sets.values():
        owner_paths = [resolve_owner_path(rtl, supply_set.scope)]
        if not supply_set.scope:
            owner_paths = _flat_supply_set_owner_paths(power_intent, rtl, supply_set.name) or owner_paths
        placement_id = _new_id()
        result.domain.supply_sets[placement_id] = SupplySetPlacement(
            id=placement_id,
            name=supply_set.name,
            power=supply_set.power_net,
            ground=supply_set.ground_net,
            n_well=supply_set.nwell_net,
            p_well=supply_set.pwell_net,
            owner_paths=owner_paths,
        )
        by_name[supply_set.name] = placement_id

    # CPF has no first-class Supply Set at all: a domain's rails arrive as
    # `update_power_domain -primary_power_net/-primary_ground_net`, which
    # the elaborator normalises into DomainSupplyConnections (see
    # cpf/verbs.py). Without synthesising a set from that pair, a CPF
    # domain would import with no supply -- which also means no colour,
    # since a domain's colour is derived from its primary set's power
    # rail. Named "<DOMAIN>_SS" so it is visibly generated rather than
    # pretending to be a name the file chose.
    #
    # Synthesised ONLY as a last resort. A UPF file commonly attaches
    # rails to a domain with `set_domain_supply_net` rather than
    # `create_power_domain -supply` -- the older, very widely used idiom
    # -- which lands in exactly the same DomainSupplyConnections. Such a
    # file has REAL, named supply sets AND per-domain connections, and
    # synthesising on the strength of the connections alone produced a
    # duplicate set per domain (`PD_TOP_SS` alongside the authored
    # `ss_aon`) which the domain then pointed at in preference to the set
    # the author actually wrote. Found by hand-authoring a
    # domains-first/set_domain_supply_net fixture -- see
    # tests/frontend/golden_simple10/README.md, iteration 1.
    #
    # So: an authored set naming the same rails wins, and a name is only
    # invented when nothing authored matches at all (the genuine CPF
    # case, where the format has no supply-set construct).
    for domain in power_intent.domains.values():
        if domain.primary_supply and domain.primary_supply in by_name:
            continue
        roles = {
            conn.role: conn.supply_net
            for conn in power_intent.domain_supply_connections
            if conn.domain == domain.name and conn.supply_net
        }
        power = roles.get("primary_power")
        ground = roles.get("primary_ground")
        if not power and not ground:
            continue
        authored = _authored_set_for_rails(power, ground, power_intent, by_name)
        if authored is not None:
            by_name[f"__synthesised__{domain.name}"] = authored
            continue
        owner_path = resolve_owner_path(rtl, domain.scope)
        placement_id = _new_id()
        result.domain.supply_sets[placement_id] = SupplySetPlacement(
            id=placement_id,
            name=f"{domain.name}_SS",
            power=power,
            ground=ground,
            owner_paths=[owner_path],
        )
        by_name[f"__synthesised__{domain.name}"] = placement_id
    return by_name


def _authored_set_for_rails(
    power: Optional[str],
    ground: Optional[str],
    power_intent: PowerIntentDesign,
    by_name: dict[str, str],
) -> Optional[str]:
    """An already-imported supply set carrying exactly these rails.

    Both rails must agree when the file named both -- a set matching only
    the power rail is a different set, and claiming it would attach the
    domain to the wrong ground. Returns the placement id, or None when
    nothing authored matches and a set genuinely has to be invented."""
    for supply_set in power_intent.supply_sets.values():
        if supply_set.name not in by_name:
            continue
        if power and supply_set.power_net != power:
            continue
        if ground and supply_set.ground_net != ground:
            continue
        return by_name[supply_set.name]
    return None


def _derived_domain_color(primary_supply_set_id: Optional[str], result: ImportedPowerIntent) -> QColor:
    """A Power Domain's colour, derived the same way the live diagram
    derives it: from its primary supply set's own power rail.

    Falls back to the neutral default only when there is genuinely
    nothing to derive from (no primary set, or its power rail names no
    imported supply) -- the same graceful-fallback behaviour
    resolve_domain_color() has."""
    if primary_supply_set_id is not None:
        supply_set = result.domain.supply_sets.get(primary_supply_set_id)
        if supply_set is not None:
            color = resolve_supply_set_color_from_placements(
                supply_set, result.supply.supply_port_placements.values()
            )
            if color is not None:
                return color
    return QColor(_UNDERIVABLE_DOMAIN_COLOR)


def _primary_supply_set_id(domain, supply_set_ids: dict[str, str]) -> Optional[str]:
    """The domain's primary Supply Set placement id.

    The authored name first (UPF), then the set synthesised from CPF's
    own per-domain rail connections -- see build_supply_sets()."""
    if domain.primary_supply and domain.primary_supply in supply_set_ids:
        return supply_set_ids[domain.primary_supply]
    return supply_set_ids.get(f"__synthesised__{domain.name}")


def build_domains(
    power_intent: PowerIntentDesign, rtl: Design, supply_set_ids: dict[str, str], result: ImportedPowerIntent
) -> dict[str, str]:
    """PowerDomains -> PowerDomainPlacements, keyed back by IR name.

    `elements` becomes owner_paths: each element is a block the domain
    covers, and a multi-element domain is exactly this app's own
    multi-hierarchy domain. An element that doesn't resolve is reported
    and skipped; a domain left with no resolvable element at all is not
    created, since a domain covering nothing has no meaning here."""
    by_name: dict[str, str] = {}
    for domain in power_intent.domains.values():
        elements = domain.elements or ["."]
        owner_paths: list[str] = []
        for element in elements:
            owner_path = resolve_owner_path(rtl, domain.scope, element)
            if not path_exists(rtl, owner_path):
                result.diagnostics.append(
                    _diagnostic(
                        Severity.ERROR, UNRESOLVED_CODE,
                        f"PowerDomain '{domain.name}': element '{element}' does not exist in the elaborated "
                        f"design (looked for '{owner_path}').",
                        domain,
                    )
                )
                result.reported_references.add((domain.name, element))
                continue
            if owner_path not in owner_paths:
                owner_paths.append(owner_path)
        if not owner_paths:
            result.diagnostics.append(
                _diagnostic(
                    Severity.ERROR, UNRESOLVED_CODE,
                    f"PowerDomain '{domain.name}': none of its elements resolved, so the domain was not imported.",
                    domain,
                )
            )
            continue
        primary_id = _primary_supply_set_id(domain, supply_set_ids)
        secondary_ids = [supply_set_ids[n] for n in domain.available_supplies if n in supply_set_ids]
        placement_id = _new_id()
        result.domain.domains[placement_id] = PowerDomainPlacement(
            id=placement_id,
            owner_path=owner_paths[0],
            owner_paths=owner_paths,
            name=domain.name,
            # DERIVED here, not left as a seed for a later refresh to fix.
            # A domain's colour comes from its primary supply set's power
            # rail, and `_refresh_domain_derived_colors()` -- the live
            # recompute -- only runs from the create/edit paths, never
            # after a bulk restore. So an import that left a placeholder
            # here produced EVERY domain in the same default green
            # regardless of its supply set, with nothing downstream to
            # correct it. Found by hand-authoring a real-world-style
            # fixture; see tests/frontend/golden_simple10/README.md, iteration 1.
            color=_derived_domain_color(primary_id, result),
            # The fixed 0.8 the derivation rule specifies, not
            # PowerDomainPlacement's own 0.5 default -- that default
            # predates the colour becoming derived at all (it was the
            # initial value of a user-facing transparency slider).
            transparency=DOMAIN_FIXED_TRANSPARENCY,
            primary_supply_set=primary_id,
            secondary_supply_sets=secondary_ids,
        )
        by_name[domain.name] = placement_id
    return by_name


def _strategy_owner_path(rtl: Design, scope: str, domain_placement: Optional[PowerDomainPlacement]) -> str:
    """Where a strategy badge attaches.

    A strategy's own authored NESTED scope wins when it resolves;
    otherwise it falls back to its domain's first owner path. UPF
    commonly declares a strategy at the same scope as its domain and
    relies on `-domain` for the association, so the fallback is the
    normal case, not an edge -- and a ROOT-scoped strategy (the flat form
    this app generates) says nothing about hierarchy at all, so it always
    takes its domain's, rather than piling every badge onto the top
    block."""
    own = resolve_owner_path(rtl, scope)
    if scope and path_exists(rtl, own):
        return own
    return domain_placement.owner_paths[0] if domain_placement else own


def _element_paths(rtl: Design, scope: str, elements: list[str]) -> list[str]:
    """Strategy elements are PORT names namespaced by their owning block
    ("<owner_path>/<port>") in this app's model -- see
    collect_isolation_elements_for_domain()'s own docstring. The IR holds
    them as authored, relative to the strategy's scope.

    Two spellings this app's own generator now writes are normalised:
    a hierarchical element relative to the scope (`u_sub/u_core/count`,
    the flat root-scoped form) is prefixed like a bare one -- it used to
    be kept as-is, losing the top instance -- and an all-bits `[*]`
    suffix is dropped, since an element key always names the whole
    signal (see power_intent_text_export._upf_element())."""
    base = resolve_owner_path(rtl, scope)
    top = f"{rtl_top_path(rtl)}/"
    paths = []
    for element in elements:
        element = element.removesuffix("[*]")
        paths.append(element if element.startswith(top) else f"{base}/{element}")
    return paths


# UPF `-applies_to {inputs|outputs|both}` -> the port directions it
# selects. `both` selects everything, which is why it maps to None
# (no filtering) rather than to the pair.
_APPLIES_TO_DIRECTIONS = {
    "inputs": (PortDirection.IN,),
    "outputs": (PortDirection.OUT,),
}


def _port_direction(rtl: Design, element_path: str) -> Optional[PortDirection]:
    """The direction of the port an `<owner_path>/<port>` element names,
    or None when it cannot be resolved (a bit-sliced reference, a name
    that is not a port of that block, a block not in this design)."""
    owner, _, port_name = element_path.rpartition("/")
    # Only a TRAILING bit-select is stripped: an arrayed interface signal
    # (`mst_port[0].awvalid`) carries an index in the middle of its name.
    bare = re.sub(r"\[[^\]]*\]$", "", port_name)
    for node in rtl.walk():
        if node.path != owner:
            continue
        # Interface signals included (shared/models/rtl_io.py).
        for port in module_io_ports(rtl, node.definition):
            if port.name in (port_name, bare):
                return port.direction
        return None
    return None


def _elements_matching_applies_to(
    rtl: Design, elements: list[str], applies_to: Optional[str]
) -> list[str]:
    """`set_isolation -applies_to` MATCHED against the rule's own
    elements, rather than carried as a separate field.

    `-applies_to outputs` alongside `-elements {a b c}` says the rule
    covers the OUTPUT ports among those -- the two describe one
    selection, not two independent facts. This GUI has no broad
    inputs/outputs/both mode of its own (its Isolation dialog picks
    individual ports, with a Direction column), so the flag is resolved
    INTO the element list here: the elements it selects are kept, and it
    stops being information that had nowhere to go.

    `both`, an unrecognised value, and any element whose direction cannot
    be resolved are all left alone -- filtering on a guess would silently
    drop a port the file explicitly listed, which is worse than carrying
    one it arguably did not select."""
    directions = _APPLIES_TO_DIRECTIONS.get((applies_to or "").lower())
    if directions is None or not elements:
        return elements
    kept = []
    for element in elements:
        direction = _port_direction(rtl, element)
        if direction is None or direction in directions:
            kept.append(element)
    return kept


def _supply_set_id_for(
    name: Optional[str], supply_set_ids: dict[str, str], result: ImportedPowerIntent
) -> Optional[str]:
    """The Supply Set placement for a name that may be a SET or a NET.

    UPF's `-isolation_supply_set` names a supply set, so the direct
    lookup wins. CPF has no supply-set concept and names the isolation
    cells' POWER RAIL instead (`update_isolation_rules
    -isolation_power_net VDD`), so a name that is not a set is matched
    against each set's own power rail -- the same "which set carries this
    rail" question the diagram itself answers everywhere else. No match
    leaves it unset rather than attaching an arbitrary set."""
    if not name:
        return None
    if name in supply_set_ids:
        return supply_set_ids[name]
    for placement in result.domain.supply_sets.values():
        if placement.power == name:
            return placement.id
    return None


def _all_retainable_elements(rtl: Design, domain: Optional[PowerDomainPlacement]) -> list[str]:
    """Every sequential element in a domain's own blocks, as the
    `"<path>/<output_signal>"` strings a Retention Strategy stores.

    Real UPF `set_retention` with no `-elements` means every retainable
    element in the domain. Importing that as an empty list was faithful
    to the FILE but wrong for this app: the retention overlay derives
    which blocks get a badge from the elements themselves, so a
    zero-element strategy existed in the data model and rendered nothing
    anywhere -- the same "one side of a feature built, the other
    silently incomplete" pattern already fixed once for Level Shifter
    and once for Power Switch. Found by hand-authoring a block-level
    fixture that omits -elements; see
    tests/frontend/golden_simple10/README.md, iteration 3.

    Filled in HERE rather than by teaching the overlay to fall back,
    because the GUI's own dialog requires at least one element and a
    test already pins "no elements left -> no badges anywhere" for the
    edit path. Expanding the flag's real meaning at import time leaves
    that contract untouched and produces exactly the strategy the
    dialog itself would have built.

    Mirrors collect_retention_elements()'s rule -- a domain's own blocks
    and their descendants, minus any descendant carved out by a
    more specific domain -- but reads the RTL directly, since that
    collector needs live Qt boxes that do not exist yet at import time.
    Descendants are included via the path prefix; the exclusion is left
    to the overlay's own `find_effective_domain_for_path()` cascade,
    which is what actually decides colouring and ownership."""
    if domain is None:
        return []
    elements: list[str] = []
    for node in rtl.walk():
        if not any(
            node.path == owner or node.path.startswith(f"{owner}/")
            for owner in domain.owner_paths
        ):
            continue
        for seq in node.definition.sequential_elements:
            elements.append(f"{node.path}/{seq.output_signal}")
    return elements


def _resolved_supply_set(
    raw: Optional[str],
    supply_set_ids: dict[str, str],
    construct,
    kind: str,
    role: str,
    result: ImportedPowerIntent,
    by_rail: bool = False,
) -> Optional[str]:
    """A strategy's supply-set reference, reporting a miss.

    A name that resolves to nothing used to import as a silent None: the
    strategy appeared correctly in every list, and its badge rendered in
    the default grey with nothing anywhere saying why. That breaks the
    "nothing is silently dropped" rule this module exists to uphold.

    The importer is the ONLY layer that can report these. The backend
    mapper classifies `isolation_supply`/`retention_supply` as
    NOT_ATTEMPTED (no independently verified target dict -- see
    docs/milestone3_mapping.md), which the validation layer then EXCLUDES
    from error counts by design. Found by hand-authoring a fixture of
    deliberately broken references; see
    tests/frontend/golden_simple10/README.md, iteration 7.

    `by_rail` additionally accepts a name that matches some set's POWER
    RAIL rather than a set name, which is how CPF states it."""
    if not raw:
        return None
    resolved = (
        _supply_set_id_for(raw, supply_set_ids, result) if by_rail
        else supply_set_ids.get(raw)
    )
    if resolved is None:
        result.diagnostics.append(
            _diagnostic(
                Severity.ERROR, UNRESOLVED_CODE,
                f"{kind} '{construct.name}': {role} '{raw}' is not a declared supply set, so the "
                f"strategy was imported without one -- its colour will fall back to the default grey.",
                construct,
            )
        )
        result.reported_references.add((construct.name, raw))
    return resolved


def _strategy_domain_id(strategy, domain_ids: dict[str, str]) -> Optional[str]:
    """Which Power Domain a strategy attaches to.

    UPF's `set_isolation`/`set_level_shifter` take `-domain` directly.
    CPF's `create_isolation_rule` has NO `-domain` flag at all -- it
    selects by `-from`/`-to` domain, which the IR records as
    `source`/`sink` (see IsolationStrategy's own docstring, which states
    this explicitly). Falling back to `source` is therefore how a CPF
    strategy names its domain, not a guess; `sink` is the last resort for
    a rule written only in the to-direction."""
    for name in (
        getattr(strategy, "domain", None),
        getattr(strategy, "source", None),
        getattr(strategy, "sink", None),
    ):
        if name and name in domain_ids:
            return domain_ids[name]
    return None


def build_strategies(
    power_intent: PowerIntentDesign,
    rtl: Design,
    domain_ids: dict[str, str],
    supply_set_ids: dict[str, str],
    supply_ids: dict[str, str],
    result: ImportedPowerIntent,
) -> None:
    """Isolation, Retention, Level Shifter and Power Switch placements.

    Each names a domain; one whose domain didn't import is skipped with
    an ERROR rather than materialized dangling -- the diagram's own
    invariants assume a strategy's domain exists."""
    for iso in power_intent.isolation_strategies.values():
        domain_id = _strategy_domain_id(iso, domain_ids)
        if domain_id is None:
            result.diagnostics.append(_missing_domain(iso, "IsolationStrategy"))
            continue
        # A rule with NO elements and a direction is this GUI's own
        # "Applies To All" Inputs/Outputs/Both. With elements, `-applies_to`
        # instead narrows that explicit list (see
        # _elements_matching_applies_to()).
        broad = (iso.applies_to or "").lower()
        applies_to = broad if not iso.elements and broad in ("inputs", "outputs", "both") else ISOLATION_APPLIES_TO_ELEMENTS
        placement_id = _new_id()
        result.isolation.strategies[placement_id] = IsolationStrategyPlacement(
            id=placement_id,
            name=iso.name,
            power_domain_id=domain_id,
            isolation_supply_set_id=_resolved_supply_set(
                iso.isolation_supply, supply_set_ids, iso, "IsolationStrategy",
                "isolation supply set", result, by_rail=True,
            ),
            # UPF `-source`/`-sink` name supply SETS. Mapped only when the
            # name is one: in CPF the same IR fields carry the `-from`/`-to`
            # DOMAIN (read as the domain link above), never a set.
            source_supply_set_id=supply_set_ids.get(iso.source) if iso.source else None,
            sink_supply_set_id=supply_set_ids.get(iso.sink) if iso.sink else None,
            applies_to=applies_to,
            # UPF writes 0/1/hold; this app's own vocabulary is
            # low/high/keep. A value outside both passes through as
            # authored rather than being coerced to a guess.
            clamp_value=_CLAMP_FROM_UPF.get((iso.clamp_value or "").lower(), iso.clamp_value),
            isolation_control=iso.isolation_signal,
            isolation_sense=iso.isolation_sense,
            location=iso.location,
            elements=_elements_matching_applies_to(
                rtl, _element_paths(rtl, iso.scope, iso.elements), iso.applies_to
            ),
            name_prefix=iso.name_prefix or "",
            owner_path=_strategy_owner_path(rtl, iso.scope, result.domain.domains.get(domain_id)),
        )

    for ret in power_intent.retention_strategies.values():
        domain_id = domain_ids.get(ret.domain or "")
        if domain_id is None:
            result.diagnostics.append(_missing_domain(ret, "RetentionStrategy"))
            continue
        placement_id = _new_id()
        domain_placement = result.domain.domains.get(domain_id)
        result.retention.strategies[placement_id] = RetentionStrategyPlacement(
            id=placement_id,
            name=ret.name,
            power_domain_id=domain_id,
            # A strategy that names no primary supply (`set_retention
            # -retention_supply_set` alone, the form this app generates)
            # runs on its domain's own primary -- which is what the
            # dialog's Primary Supply Set means.
            primary_supply_set_id=(
                _resolved_supply_set(
                    ret.primary_supply, supply_set_ids, ret, "RetentionStrategy", "primary supply set", result,
                )
                if ret.primary_supply
                else (domain_placement.primary_supply_set if domain_placement else None)
            ),
            retention_supply_set_id=_resolved_supply_set(
                ret.retention_supply, supply_set_ids, ret, "RetentionStrategy", "retention supply set", result,
            ),
            save_logic=_retention_logic(ret.save_signal),
            restore_logic=_retention_logic(ret.restore_signal),
            elements=(
                _element_paths(rtl, ret.scope, ret.elements) if ret.elements
                else _all_retainable_elements(rtl, result.domain.domains.get(domain_id))
            ),
            owner_path=_strategy_owner_path(rtl, ret.scope, result.domain.domains.get(domain_id)),
        )

    for ls in power_intent.level_shifter_strategies.values():
        domain_id = _strategy_domain_id(ls, domain_ids)
        if domain_id is None:
            result.diagnostics.append(_missing_domain(ls, "LevelShifterStrategy"))
            continue
        placement_id = _new_id()
        result.level_shifter.strategies[placement_id] = LevelShifterStrategyPlacement(
            id=placement_id,
            name=ls.name,
            power_domain_id=domain_id,
            # An element list with no `-applies_to` is the dialog's
            # "Elements" choice -- which the generator writes as
            # `-elements` alone ("elements" is not a UPF -applies_to value).
            applies_to=ls.applies_to or ("elements" if ls.elements else ls.applies_to),
            rule=ls.rule,
            location=ls.location,
            threshold=ls.threshold,
            cell_type=ls.cell_type,
            elements=_element_paths(rtl, ls.scope, ls.elements),
            name_prefix=ls.name_prefix or "",
            owner_path=_strategy_owner_path(rtl, ls.scope, result.domain.domains.get(domain_id)),
        )

    for switch in power_intent.switches.values():
        domain_id = domain_ids.get(switch.domain or "")
        if domain_id is None:
            result.diagnostics.append(_missing_domain(switch, "PowerSwitch"))
            continue
        # A switch attaches to the supply it drives -- its OUTPUT supply,
        # which is this app's own "Supply Out" anchor. Without that
        # target there is nothing to anchor the icon to, so the switch is
        # reported rather than created.
        target_id = supply_ids.get(switch.output_supply or "")
        if target_id is None:
            result.diagnostics.append(
                _diagnostic(
                    Severity.ERROR, UNRESOLVED_CODE,
                    f"PowerSwitch '{switch.name}': output supply "
                    f"'{switch.output_supply or '(none)'}' is not a declared supply net, so the switch "
                    "has nothing to attach to and was not imported.",
                    switch,
                )
            )
            result.reported_references.add((switch.name, switch.output_supply or ""))
            continue
        # The GUI's On/Off State hold the switch's own state EXPRESSION,
        # and that is what the generator writes as the expression half of
        # `-on_state {on {expr}}` -- the "on"/"off" names there are the
        # generator's own labels. So read the expression back, falling
        # back to the name for a hand-written file that only gave one.
        port_map = dict(switch.port_map or {})
        # ON/OFF State are authored against the REAL signal and written
        # out against the abstract control port, so reading them back
        # reverses that substitution -- otherwise a round trip silently
        # rewrites the user's own expression into UPF's vocabulary and
        # leaves it there. Only whole-identifier CTRL is replaced, so a
        # cell pin or signal merely CONTAINING "CTRL" is left alone.
        control_signal = switch.control_ports[0] if switch.control_ports else ""
        # The token to substitute BACK from is the switch's own declared
        # control PORT name -- the user's field value, not a fixed
        # constant, since that is what generation substituted toward.
        control_token = (
            port_map.get(ABSTRACT_CTRL)
            or (switch.control_port_pins[0] if switch.control_port_pins else "")
        )

        def _authored(expr: str) -> str:
            return substitute_abstract_control(expr, control_signal, control_token)

        on_state, off_state = "", ""
        if switch.states:
            first = switch.states[0]
            on_state = _authored(first.boolean_expr or first.name)
            if len(switch.states) > 1:
                second = switch.states[1]
                off_state = _authored(second.boolean_expr or second.name)
        placement_id = _new_id()
        owner_path = result.supply.supply_port_placements[target_id].owner_path
        result.power_switch.switches[placement_id] = PowerSwitchPlacement(
            id=placement_id,
            name=switch.name,
            power_domain_id=domain_id,
            target_ids=[target_id],
            owner_paths=[owner_path],
            # The GUI's three port fields are the switch cell's own PIN
            # names, and `map_power_switch -port_map` is where UPF states
            # them: it maps each ABSTRACT port (IN/OUT/CTRL) onto the real
            # cell pin implementing it. Read from there first.
            #
            # The fallbacks are for a file that has no -port_map -- then
            # the only pin-shaped names available are the abstract port
            # names the switch declared, and finally the nets themselves
            # for the unpaired spelling.
            # The DECLARED port name is primary now: generation writes the
            # user's own field values straight into `-input_supply_port
            # {NAME net}` and friends, so that is where they live.
            # `-port_map` is a secondary source, for a file that carries
            # one -- this app no longer emits any.
            input_supply_port=switch.input_supply_port or port_map.get(ABSTRACT_IN) or switch.input_supply or "",
            output_supply_port=switch.output_supply_port or port_map.get(ABSTRACT_OUT) or switch.output_supply or "",
            control_port=(
                (switch.control_port_pins[0] if switch.control_port_pins else "")
                or port_map.get(ABSTRACT_CTRL)
                or (switch.control_ports[0] if switch.control_ports else "")
            ),
            # "Control Signal": the NET half of `-control_port {CTRL
            # <net>}` -- the real RTL signal driving the control pin, as
            # distinct from the pin itself. This is the field the review
            # found missing entirely; before it existed, the net half was
            # classified NOT-MODELLED and dropped.
            control_signal=switch.control_ports[0] if switch.control_ports else "",
            # The acknowledge pair, resolved exactly like the control
            # pair: the cell PIN from -port_map (falling back to the
            # abstract port name a file without one declared), the real
            # SIGNAL from the net half of `-ack_port {ACK <net>}`.
            ack_port=(
                (switch.ack_port_pins[0] if switch.ack_port_pins else "")
                or port_map.get(ABSTRACT_ACK)
            ),
            ack_signal=switch.ack_ports[0] if switch.ack_ports else "",
            # None, not "", when the file maps no cell -- the same
            # "unset" a switch created in the dialog carries.
            cell_type=switch.cell_type or None,
            on_state=on_state,
            off_state=off_state,
        )
        # The Supply In connector. `-input_supply_port {pin net}` names
        # both the pin and the net feeding it, so the net half rebuilds
        # the connector the user originally drew -- without it the switch
        # would come back visually unconnected, and (because the pin name
        # is only representable when there IS a net to pair it with) the
        # pin name itself would then be lost on the NEXT export.
        source_id = supply_ids.get(switch.input_supply or "")
        if source_id is not None:
            result.supply.supply_connector_placements.append(
                (power_switch_connector_key(placement_id, target_id), source_id)
            )


def _missing_domain(construct, kind: str) -> Diagnostic:
    return _diagnostic(
        Severity.ERROR, UNRESOLVED_CODE,
        f"{kind} '{construct.name}': domain '{getattr(construct, 'domain', None) or '(none)'}' was not "
        "imported, so this strategy was skipped.",
        construct,
    )


def build_port_attributes(
    power_intent: PowerIntentDesign, rtl: Design, supply_set_ids: dict[str, str], result: ImportedPowerIntent
) -> None:
    """PortAttributes -> per-port-BIT Supply Set assignments.

    Driver and receiver collapse onto one assignment per key: this app
    stores a single Supply Set per key and derives which column it fills
    from the port's own direction (see port_attribute_dialog), so a port
    asserted with both roles keeps the driver -- the one an OUT/INOUT
    port displays.

    KEYED PER BIT, not per port. `assignments` is keyed by
    `port_attribute_row_keys()`: the bare name for a single-bit port, but
    `"<name>[i]"` per bit for a multi-bit one. A real file asserts the
    whole port at once (`-ports {d a}`), which was being stored under the
    bare name -- a key the editor never looks at for a 32-bit port, so
    the assignment existed in the state and was invisible in the GUI.
    Expanded here against the elaborated top-level ports, which is the
    only place the width is known. Found by hand-authoring a fixture that
    asserts attributes on this design's real multi-bit boundary ports --
    see tests/frontend/golden_simple10/README.md, iteration 3."""
    keys_by_port = {
        port.name: port_attribute_row_keys(port)
        # Interface signals included, matching the Port Attribute dialog.
        for port in module_io_ports(rtl, rtl.definitions[rtl.top])
    }
    for attribute in power_intent.port_attributes.values():
        supply_name = attribute.driver_supply or attribute.receiver_supply
        supply_set_id = supply_set_ids.get(supply_name or "")
        if supply_set_id is None:
            continue
        for port in attribute.ports:
            # An already-bit-indexed reference (`d[3]`) is used as given;
            # a whole-port one expands to every bit it covers. A name
            # that is not a top-level port at all falls back to itself,
            # so nothing is silently dropped -- the mapper's own DANGLING
            # sweep is what reports it.
            for key in keys_by_port.get(port, [port]):
                result.port_attribute.assignments[key] = supply_set_id


def build_connectors(
    power_intent: PowerIntentDesign, rtl: Design, supply_ids: dict[str, str], result: ImportedPowerIntent
) -> None:
    """`connect_supply_net -ports` -> drawn power nets.

    Two kinds of target are drawable, and the second was missing
    entirely until a hand-authored vendor fixture wired a macro up (see
    tests/frontend/golden_simple10/README.md, iteration 2):

      - another declared supply net, by name;
      - a MACRO PG PIN, spelled `<instance>/<pin>`. This is the form the
        app's own generator writes for every macro connection, so
        dropping it meant a generated file's macro hookup did not
        survive a reload either -- the round-trip suite had simply never
        included one.

    A macro PG pin is not in `supply_ids`: those placements are derived
    from the `.lib` definition at render time by
    `build_supply_port_overlay()`, not imported. But their ids are
    DETERMINISTIC (`pg:<macro path>:<pin>`), so a connector can name one
    before it exists and be matched up when the scene is built.

    Anything else is left alone -- notably the self-connection UPF uses
    to wire a boundary port to its own same-named net, which is not a
    diagram connector at all."""
    for connection in power_intent.domain_supply_connections:
        if not connection.ports:
            continue
        default_source_id = supply_ids.get(connection.supply_net)
        if default_source_id is None:
            continue
        for port in connection.ports:
            target_id = _connector_target_id(port, rtl, supply_ids)
            if target_id is None or target_id == default_source_id:
                continue
            # A macro pin is wired to the NEAREST supply of that name
            # above it -- the copy on its own subsystem, when the design
            # has one (see _materialize_shared_rails()) -- as in the app.
            source_id = default_source_id
            if target_id.startswith("pg:"):
                source_id = _nearest_supply_id(result, connection.supply_net, target_id.split(":", 2)[1]) or default_source_id
            pair = (source_id, target_id)
            if pair not in result.supply.supply_connector_placements:
                result.supply.supply_connector_placements.append(pair)


def _connector_target_id(port: str, rtl: Design, supply_ids: dict[str, str]) -> Optional[str]:
    """The diagram id a `connect_supply_net -ports` entry names, or None
    when it names nothing drawable."""
    bare = port.rsplit("/", 1)[-1]
    if "/" in port:
        instance, pin = port.rsplit("/", 1)
        path = resolve_owner_path(rtl, "", instance)
        if is_macro(rtl, path) and _macro_has_pg_pin(rtl, path, pin):
            return macro_pg_pin_id(path, pin)
    return supply_ids.get(bare)


def _macro_has_pg_pin(rtl: Design, path: str, pin: str) -> bool:
    """Whether the macro at `path` really declares `pin` as a PG pin.

    Checked rather than assumed: a `<instance>/<name>` entry naming a
    FUNCTIONAL pin of a macro is not a supply connection, and inventing
    a PG-pin id for it would produce a connector anchored to an icon
    that never materialises."""
    for node in rtl.walk():
        if node.path != path:
            continue
        return any(
            p.name == pin and p.direction in (PortDirection.PG, PortDirection.PG_INTERNAL)
            for p in node.definition.ports
        )
    return False


# --- hierarchical load_upf: flatten vs. attach (section 5) ------------------
#
# The SAME `load_upf -instance <x>` syntax means two different things,
# and which one is decided by what `x` actually IS in the elaborated RTL:
#
#   x is a SOFT (RTL) hierarchy -> a genuine hierarchical sub-power-intent
#       file. Its constructs belong to the design and are merged in under
#       that scope. This needs no work HERE: the elaborator already
#       sources the sub-file inline under the named scope, so its
#       constructs arrive in the same PowerIntentDesign already carrying
#       the right scope. Flattening is therefore a property of how the
#       elaborator reads the file, not a second pass -- which is also why
#       the spec's "do not build nested/lazy sub-file loading" is already
#       satisfied.
#
#   x is a MACRO -> vendor power intent for that macro, which this app
#       already models as an ATTACHMENT (see macro_power_intent_overlay).
#       It must NOT be flattened into the design's own constructs: a
#       macro is a black box, and its internal domains/supplies are not
#       part of the parent design's diagram.
#
# The elaborator cannot make this call -- it has no RTL to check the
# instance against, which is exactly why it accepts `-instance` as a
# plain scope alias and leaves the decision here.


def macro_attachments(power_intent: PowerIntentDesign, rtl: Design, entry_file: str) -> dict[str, str]:
    """Which loaded sub-files were attached to MACROS rather than
    flattened, as {macro hierarchy path: source file}.

    Detected from where constructs landed: any scope that resolves to a
    black-box instance was loaded onto a macro, and the file recorded is
    the one those constructs came from (their own `source_loc`), not the
    entry file. A soft-hierarchy scope produces nothing here, having
    already been merged in by the elaborator."""
    attachments: dict[str, str] = {}
    for collection in (
        power_intent.domains, power_intent.supply_ports, power_intent.supply_nets,
        power_intent.supply_sets, power_intent.switches, power_intent.isolation_strategies,
        power_intent.retention_strategies, power_intent.level_shifter_strategies,
    ):
        for construct in collection.values():
            scope = getattr(construct, "scope", "")
            if not scope:
                continue
            path = resolve_owner_path(rtl, scope)
            if not is_macro(rtl, path):
                continue
            loc = getattr(construct, "source_loc", None)
            source_file = getattr(loc, "file", None) or entry_file
            attachments.setdefault(path, source_file)
    return attachments


def scoped_to_macros(power_intent: PowerIntentDesign, rtl: Design) -> set[str]:
    """IR scopes that name a macro -- the constructs to LEAVE OUT of the
    design's own diagram, because they belong to an attached macro."""
    macro_scopes: set[str] = set()
    for collection in (
        power_intent.domains, power_intent.supply_nets, power_intent.supply_sets,
        power_intent.switches, power_intent.isolation_strategies,
        power_intent.retention_strategies, power_intent.level_shifter_strategies,
    ):
        for construct in collection.values():
            scope = getattr(construct, "scope", "")
            if scope and is_macro(rtl, resolve_owner_path(rtl, scope)):
                macro_scopes.add(scope)
    return macro_scopes


def _without_macro_scopes(power_intent: PowerIntentDesign, macro_scopes: set[str]) -> PowerIntentDesign:
    """A copy of the design with every macro-scoped construct removed.

    Filtered rather than skipped construct-by-construct downstream so
    each builder stays simple and no builder can forget the rule."""
    import dataclasses

    def keep(collection):
        return {k: v for k, v in collection.items() if getattr(v, "scope", "") not in macro_scopes}

    return dataclasses.replace(
        power_intent,
        domains=keep(power_intent.domains),
        supply_ports=keep(power_intent.supply_ports),
        supply_nets=keep(power_intent.supply_nets),
        supply_sets=keep(power_intent.supply_sets),
        switches=keep(power_intent.switches),
        isolation_strategies=keep(power_intent.isolation_strategies),
        retention_strategies=keep(power_intent.retention_strategies),
        level_shifter_strategies=keep(power_intent.level_shifter_strategies),
        power_states=keep(power_intent.power_states),
        power_state_groups=keep(power_intent.power_state_groups),
        port_attributes=keep(power_intent.port_attributes),
        domain_supply_connections=[
            c for c in power_intent.domain_supply_connections if c.scope not in macro_scopes
        ],
    )


# --- the entry point --------------------------------------------------------


def report_reinterpreted_state_types(result: ImportedPowerIntent) -> None:
    """Say which supply state TYPES were read as something else.

    The fallback in _rail_state() is deliberate and unchanged: an
    unrecognized token carrying a level is read as FULL_ON at that level,
    which is a reasonable best effort. What was wrong was that it
    happened SILENTLY -- a real reference file declares
    `power == {ON 0.60}`, and the state arrived as FULL_ON with nothing
    anywhere recording that the name had been substituted.

    One finding per DISTINCT token, not one per rail: the same
    non-standard name typically recurs across many supplies, and
    repeating it would read as many problems rather than one."""
    for token in sorted(result.reinterpreted_state_types):
        result.diagnostics.append(
            Diagnostic(
                severity=Severity.WARNING,
                code=UNRECOGNIZED_CODE,
                message=(
                    f"Supply state type '{token}' is not one of "
                    f"{'/'.join(SUPPLY_STATE_TYPES)}. It carries a voltage level, so it was "
                    f"interpreted as '{FULL_ON}' at that level as a best-effort fallback -- "
                    "check that this is what the file meant."
                ),
            )
        )


def unrecognized_syntax_diagnostics(power_intent: PowerIntentDesign) -> list[Diagnostic]:
    """One WARNING per construct the elaborator skipped as not recognized
    (a command, or a command carrying an option it does not know), naming
    the UPF line it came from. Everything else in the file was still read."""
    diagnostics = []
    for construct in power_intent.unrecognized:
        loc = construct.source_loc
        where = f"line {loc.line}" if loc is not None and loc.line is not None else "UPF"
        diagnostics.append(
            Diagnostic(
                severity=Severity.WARNING,
                code=UNRECOGNIZED_SYNTAX_CODE,
                message=(
                    f"{where}: `{construct.text}` was not recognized and was skipped -- "
                    f"{construct.reason}"
                ),
                file=loc.file if loc is not None else None,
                line=loc.line if loc is not None else None,
                source=DIAGNOSTIC_SOURCE,
            )
        )
    return diagnostics


def _generated_pst_prefixes(power_intent: PowerIntentDesign) -> dict[str, str]:
    """{pst name: prefix to strip} -- this app's legacy export prefixes
    every `add_pst_state` name with `<pst>_` (`PST_TURBO` in `PST`) so a
    mode can never share a name with a supply state. Stripped only when
    EVERY row of that table carries it, so a hand-written table that
    merely has some PST_-named rows keeps its names."""
    rows: dict[str, list[str]] = {}
    for ps in power_intent.power_states.values():
        if ps.shape is PowerStateShape.PST_ROW and ps.applies_to:
            rows.setdefault(ps.applies_to, []).append(ps.name)
    return {
        pst: f"{pst}_"
        for pst, names in rows.items()
        if names and all(name.startswith(f"{pst}_") and len(name) > len(pst) + 1 for name in names)
    }


def _materialize_shared_rails(rtl: Design, result: ImportedPowerIntent) -> None:
    """Rebuild the per-hierarchy copies of a supply used across hierarchies.

    Mirrors the app's own auto-link
    (PowerIntentDiagramView._auto_link_domains_supply_sets()): for every
    supply set a domain references -- its primary AND each secondary --
    every rail that is a boundary Supply Port on some OTHER hierarchy
    gets a same-named supply on each of the domain's own hierarchies that
    lacks one, wired to that port when one contains the other. One
    continuous rail, drawn where it is used. A flat UPF declares each net
    once, at the top, so these copies are not in the file; they follow
    from where the domains and supply sets live. Only a real port is a
    source: an internal net declared at the root of a flat file is
    placed by the importer, not authored there, so copying it would
    invent a second net."""
    root = rtl_top_path(rtl)
    placements = result.supply.supply_port_placements
    sets = result.domain.supply_sets
    for domain in result.domain.domains.values():
        # A macro's supplies are its own .lib PG pins; the app never
        # places a supply inside one.
        targets = [path for path in domain.owner_paths if not is_macro(rtl, path)]
        for set_id in (domain.primary_supply_set, *domain.secondary_supply_sets):
            supply_set = sets.get(set_id)
            if supply_set is None:
                continue
            for rail in (supply_set.power, supply_set.ground, supply_set.n_well, supply_set.p_well):
                if not rail:
                    continue
                source = next(
                    (p for p in placements.values()
                     if p.name == rail and p.owner_path not in targets
                     and not p.is_internal_supply and not p.is_virtual_supply and not p.fixed),
                    None,
                )
                if source is None:
                    continue
                for owner_path in targets:
                    if any(p.name == rail and p.owner_path == owner_path for p in placements.values()):
                        continue
                    placement_id = _new_id()
                    placements[placement_id] = SupplyPortPlacement(
                        id=placement_id,
                        owner_path=owner_path,
                        name=rail,
                        color=QColor(source.color),
                        is_internal_supply=owner_path != root,
                    )
                    nested = owner_path.startswith(f"{source.owner_path}/") or source.owner_path.startswith(f"{owner_path}/")
                    if nested:
                        result.supply.supply_connector_placements.append((source.id, placement_id))
        # A domain's colour follows its primary set's power rail ON ITS OWN
        # hierarchy, which may be one of the copies just made.
        domain.color = _derived_domain_color(domain.primary_supply_set, result)


def _nearest_supply_id(result: ImportedPowerIntent, name: str, path: str) -> Optional[str]:
    """The supply placement named `name` on the deepest hierarchy that
    contains `path` -- a macro pin's own subsystem's copy before the
    top-level port."""
    best = None
    for placement in result.supply.supply_port_placements.values():
        if placement.name != name:
            continue
        owner = placement.owner_path
        if path == owner or path.startswith(f"{owner}/"):
            if best is None or owner.count("/") > best.owner_path.count("/"):
                best = placement
    return best.id if best is not None else None


def _scope_rows_to_root_domain(rtl: Design, result: ImportedPowerIntent) -> None:
    """A PST row and a power-state-group state are design-wide: the file
    names no domain for them. They are scoped to the design's top-level
    domain -- the one covering the design root -- which is where a system
    mode belongs and what the Power State Table needs to be complete. Left
    unset when no domain covers the root."""
    root = rtl_top_path(rtl)
    root_domain = next(
        (d.id for d in result.domain.domains.values() if root in d.owner_paths),
        None,
    )
    if root_domain is None:
        return
    for row in result.power_state_table.rows.values():
        if row.power_domain_id is None:
            row.power_domain_id = root_domain


def import_power_intent(
    power_intent: PowerIntentDesign,
    rtl: Design,
    mapped: Optional[MappedDesign] = None,
    entry_file: str = "",
) -> ImportedPowerIntent:
    """Translate an elaborated power intent into diagram state.

    `rtl` is required and must already be elaborated (section 1): every
    scope, element and port reference resolves against it, and this
    function never infers hierarchy of its own.

    `mapped` is optional only so the translation can be tested without a
    full mapping run; the real caller always passes one, and without it
    the DANGLING-derived ERRORs are simply absent."""
    result = ImportedPowerIntent()

    # Section 5: split off anything scoped to a macro BEFORE building,
    # and record it as an attachment instead.
    macro_scopes = scoped_to_macros(power_intent, rtl)
    result.macro_power_intent.attachments.update(macro_attachments(power_intent, rtl, entry_file))
    design_only = _without_macro_scopes(power_intent, macro_scopes) if macro_scopes else power_intent

    result.diagnostics.extend(unrecognized_diagnostics(design_only))

    result.diagnostics.extend(unrecognized_syntax_diagnostics(power_intent))

    supply_ids = build_supplies(design_only, rtl, result)
    supply_set_ids = build_supply_sets(design_only, rtl, result)
    domain_ids = build_domains(design_only, rtl, supply_set_ids, result)
    _materialize_shared_rails(rtl, result)
    build_strategies(design_only, rtl, domain_ids, supply_set_ids, supply_ids, result)
    build_port_attributes(design_only, rtl, supply_set_ids, result)
    build_connectors(design_only, rtl, supply_ids, result)
    build_power_state_table(design_only, supply_ids, result)
    _scope_rows_to_root_domain(rtl, result)
    report_unrecoverable_internal_states(design_only, result)
    report_reinterpreted_state_types(result)

    # LAST, not first: the backend's DANGLING sweep is the catch-all for
    # references no builder looks at, and it can only skip the ones the
    # builders have already reported once they have actually run.
    result.diagnostics.extend(unresolved_diagnostics(mapped, result.reported_references))
    return result


# The set name admits "/" -- a root-scope group state names a nested
# set by its qualified path (see _find_supply_set_reference()).
_LOGIC_EXPR_TERM = re.compile(r"(?P<set>[\w./]+)\s*==\s*(?P<state>[\w.]+)")


def _build_rows_from_power_state_group(
    power_intent: PowerIntentDesign, supply_ids: dict[str, str], result: ImportedPowerIntent
) -> bool:
    """`add_power_state -group <g>` states -> Power State Table rows.

    The inverse of the two-tier generation, and the reason it can round
    trip at all. A group state's `-logic_expr` names SUPPLY SETS and the
    derived state each one is in (`SS_TOP == STATE_1`), so getting back
    to the per-NET values a table row stores takes two hops:

      1. resolve `SS_TOP == STATE_1` to that supply set's own STATE_1,
         whose `-supply_expr` gives one value per FUNCTION
         (power/ground/nwell/pwell);
      2. map each function to the NET the set names for it, and that net
         to its imported placement id.

    Returns whether it built anything, so the caller can fall through to
    the legacy-PST path for a file that uses that older shape instead."""
    group_states = [s for s in power_intent.power_states.values() if s.group]
    if not group_states:
        return False

    # (supply set name, derived state name) -> {function: rail key}
    derived: dict[tuple[str, str], dict[str, SupplyPortState]] = {}
    for state in power_intent.power_states.values():
        if not state.supply:
            continue
        derived[(state.supply, state.name)] = _supply_set_state_terms(state.expr or "")

    built = False
    for state in group_states:
        net_states: dict[str, Optional[str]] = {}
        for match in _LOGIC_EXPR_TERM.finditer(state.expr or ""):
            set_name, derived_name = match.group("set"), match.group("state")
            supply_set = _find_supply_set_reference(power_intent, set_name)
            # `derived` is keyed by the string Tier 1 authored, which for
            # a nested set is qualified too -- so the qualified reference
            # matches directly, and the bare tail is the fallback for a
            # file that mixes the two spellings.
            terms = derived.get((set_name, derived_name)) or derived.get((set_name.rpartition("/")[2], derived_name))
            if supply_set is None or not terms:
                continue
            for function, rail_state in terms.items():
                net_name = getattr(supply_set, _SUPPLY_FUNCTION_FIELDS[function], None)
                placement_id = supply_ids.get(net_name or "")
                if placement_id is not None:
                    net_states[placement_id] = state_key(rail_state)
        if not net_states:
            continue
        row_id = _new_id()
        result.power_state_table.rows[row_id] = PowerStateTableRow(
            id=row_id,
            # A group is design-wide, so its states name no single domain
            # -- the same reason a legacy PST row leaves this unset.
            power_domain_id=None,
            state_name=state.name,
            net_states=net_states,
        )
        built = True
    return built


def build_power_state_table(
    power_intent: PowerIntentDesign, supply_ids: dict[str, str], result: ImportedPowerIntent
) -> None:
    """A legacy PST -> Power State Table rows.

    UPF's `create_pst`/`add_pst_state` IS this app's Power State Table: a
    named system mode per row, one column per supply, each cell naming
    that supply's state in that mode. The exporter already writes the
    table out in exactly that form, and nothing read it back -- so a
    file's whole system-mode structure arrived as PowerState records that
    reached neither the table nor any supply, leaving only a
    "shape is not modelled" provenance warning that said nothing about
    the loss. Found by hand-authoring a legacy-PST fixture; see
    tests/frontend/golden_simple10/README.md, iteration 5.

    Read from `PowerState.supply_states`, the structured per-supply
    mapping, rather than by re-parsing the `expr` string this codebase
    synthesises for the same rows.

    `power_domain_id` stays None, faithfully: a PST has no domain concept
    at all (the exporter's own comment says so), and the table's own
    domain picker starts genuinely unselected.

    UPF only: CPF is no longer mapped (see load_power_intent_tab.py)."""
    # A POWER STATE GROUP's own states are the table's rows -- the Tier 2
    # shape this app now generates, and the one it has to read back.
    # Handled first: a file carrying a group is describing the same
    # system modes a legacy PST would, in the newer form.
    if _build_rows_from_power_state_group(power_intent, supply_ids, result):
        return

    # A legacy PST names each cell's state by NAME (`HV`, `RET`); the table
    # stores the state's key (`FULL_ON 0.8`). The supplies' own
    # add_port_state/add_supply_state lists give the translation.
    named = _named_state_keys(power_intent)
    prefixes = _generated_pst_prefixes(power_intent)
    for state in power_intent.power_states.values():
        if state.shape is PowerStateShape.PST_ROW and state.supply_states:
            net_states = {
                supply_ids[supply]: named.get((supply, value), value)
                for supply, value in state.supply_states.items()
                if supply in supply_ids
            }
            if not net_states:
                continue
            row_id = _new_id()
            result.power_state_table.rows[row_id] = PowerStateTableRow(
                id=row_id,
                power_domain_id=None,
                state_name=state.name.removeprefix(prefixes.get(state.applies_to, "")),
                net_states=net_states,
            )


def report_unrecoverable_internal_states(
    power_intent: PowerIntentDesign, result: ImportedPowerIntent
) -> None:
    """WARN where a net's authored power states are genuinely lost.

    An Internal Supply's states are DERIVED here, by tracing the switch
    chain feeding it (resolve_effective_states()), never authored -- so
    `build_supplies()` does not copy authored states onto one, which
    would create a second, conflicting source of truth.

    That is lossless exactly when something upstream can be traced. A
    net driven by an imported Power Switch gets its states back from the
    switch's own source plus a SWITCH-OFF entry, so the authored list is
    reproduced rather than dropped -- and warning there is a false
    positive, including on this app's own generated output, which writes
    an internal net's DERIVED states out as `add_power_state` (they are
    what a downstream UPF consumer needs).

    With no switch driving it, there is nothing to derive from and the
    states really are gone. That is the only case worth a warning.

    Runs as a post-pass rather than inside build_supplies() because it
    needs the switches, which are built afterwards."""
    switched_names = {
        result.supply.supply_port_placements[target_id].name
        for switch in result.power_switch.switches.values()
        for target_id in switch.target_ids
        if target_id in result.supply.supply_port_placements
    }
    port_names = {p.name for p in result.supply.supply_port_placements.values() if not p.is_internal_supply}
    for placement in result.supply.supply_port_placements.values():
        if not placement.is_internal_supply or placement.name in switched_names:
            continue
        if placement.name in port_names:
            continue  # a copy of a port (see _materialize_shared_rails()) -- the port carries the states
        authored = _states_for_supply(power_intent, placement.name, result.reinterpreted_state_types)
        if not authored:
            continue
        net = _find_by_name(power_intent.supply_nets, placement.name)
        result.diagnostics.append(
            _diagnostic(
                Severity.WARNING, UNRECOGNIZED_CODE,
                f"SupplyNet '{placement.name}': {len(authored)} authored power state(s) "
                f"({', '.join(state_label(s) for s in authored)}) were not imported. This net has no boundary "
                "supply port, so it is modelled as an Internal Supply, whose states are derived from "
                "the Power Switch chain feeding it -- and no switch drives this net, so there is "
                "nothing to derive them from.",
                net,
            )
        )
