"""Content-producing UPF verb handlers.

Confirmed identical across UPF 2.1/3.0/3.1 for construction, with one
exception: `_set_isolation`/`_set_level_shifter` call into
`version_gates.check_location_value` to reject `-location` values not yet
valid under the design's declared version (currently just `fanout`,
3.1-only -- see version_gates.py). Every other handler still has zero
version-conditional logic. Each handler rejects a second declaration of
the same name ("already exists") -- **confirmed correct UPF behavior, not
a gap**:
an earlier review round speculated this needed CPF-style by-name
"successive refinement" merging, but re-deriving the actual verified UPF
precedence example showed that rule resolves between *distinctly-named*
overlapping-scope strategies (needs real RTL port data -- Milestone 3's
job), not same-name redeclaration. See
docs/milestone2_powerintent_elaborator.md's "Accumulate-then-freeze" section
for the full correction and the retraction of the earlier framing.

Confidence calibration (see the doc's "confidence asymmetry" note): CPF's
command shapes used elsewhere in this codebase were verified directly
against Si2's freely-available primary spec. UPF's are not -- IEEE 1801 is
paywalled -- so the flag names below reflect consistent, widely-documented
industry usage (tool docs, tutorials), not a primary-source citation. Where
a real flag's exact shape isn't confidently known, the verb either omits
that flag (documented per-command below) rather than guessing at syntax.

Cross-reference validation (e.g. does `-domain PD_CORE` on an isolation
strategy actually name a declared domain?) is deliberately not done here --
same boundary as backend/validation/ being a separate, cross-cutting
concern in the repo layout, not folded into each elaborator.
"""

from __future__ import annotations

from typing import Optional

from backend.elaborator.power_intent.ir import (
    DomainSupplyConnection,
    PowerStateShape,
    PowerSwitchState,
    make_power_intent_id,
)
from backend.elaborator.power_intent.shared.builders import (
    _DomainBuilder,
    _ElaborationState,
    _IsolationBuilder,
    _LevelShifterBuilder,
    _PowerStateBuilder,
    _PowerStateGroupBuilder,
    _PortAttributeBuilder,
    _PowerStateGroupBuilder,
    _PowerSwitchBuilder,
    _RetentionBuilder,
    _SupplyNetBuilder,
    _SupplyPortBuilder,
    _SupplySetBuilder,
)
from backend.elaborator.power_intent.cpf.precedence import apply_update, find_by_name
from backend.elaborator.power_intent.shared.tcl_runtime import _one
from backend.elaborator.power_intent.upf.version_gates import check_location_value


def _create_power_domain(state: _ElaborationState, parsed: dict[str, list]) -> None:
    name = _one(parsed, "name")
    id_ = make_power_intent_id(state.current_scope, name)
    if id_ in state.domains:
        raise ValueError(f"power domain '{name}' already exists at scope '{state.current_scope}'")
    # `-supply` is a repeatable {role set} PAIR, not a bare value:
    # `-supply {primary SS_VDD} -supply {secondary SS_BKP}`. Reading it
    # with _one() produced the literal list ['primary', 'SS_VDD'] as the
    # "primary supply", which round-trip import exposed immediately.
    # Secondary roles land in `available_supplies`, the same place
    # `-available_supplies {..}` (the other spelling, used by this
    # project's own golden.upf) puts them -- both forms accepted.
    # `-include_scope` says "this domain IS the current scope", which is
    # the same statement `-elements {.}` makes -- the form this app
    # already models and generates. Normalised onto it here so both
    # spellings produce an equivalent domain rather than one of them
    # being rejected (audit B7).
    elements = _one(parsed, "elements", default=[])
    if _one(parsed, "include_scope", default=False) and not elements:
        elements = ["."]
    builder = _DomainBuilder(
        id=id_,
        name=name,
        scope=state.current_scope,
        elements=elements,
        exclude_elements=_one(parsed, "exclude_elements", default=[]),
        available_supplies=list(_one(parsed, "available_supplies", default=[])),
        source_loc=state.loc(),
    )
    for supply in parsed.get("supply", []):
        if isinstance(supply, str):
            # A bare `-supply SS` with no role -- treat as the primary,
            # which is the only sensible reading of an unroled supply.
            builder.primary_supply = supply
            continue
        if len(supply) != 2:
            raise ValueError(f"power domain '{name}': -supply expects {{role set}}, got {supply!r}")
        role, supply_set = supply
        if role == "primary":
            builder.primary_supply = supply_set
        elif role == "secondary":
            builder.available_supplies.append(supply_set)
        else:
            raise ValueError(f"power domain '{name}': -supply role '{role}' is not supported (supported: primary, secondary)")
    state.domains[id_] = builder


def _create_supply_port(state: _ElaborationState, parsed: dict[str, list]) -> None:
    name = _one(parsed, "name")
    id_ = make_power_intent_id(state.current_scope, name)
    if id_ in state.supply_ports:
        raise ValueError(f"supply port '{name}' already exists at scope '{state.current_scope}'")
    state.supply_ports[id_] = _SupplyPortBuilder(
        id=id_,
        name=name,
        scope=state.current_scope,
        direction=_one(parsed, "direction"),
        source_loc=state.loc(),
    )


def _create_supply_net(state: _ElaborationState, parsed: dict[str, list]) -> None:
    """`create_supply_net <name> [-domain <d>] [-reuse]`.

    TWO scoping mechanisms reach this handler and they behave
    differently, which is what made `-reuse` misfire:

      - `set_scope <s>` changes `state.current_scope`, so a net
        redeclared under a different scope gets a DIFFERENT id and no
        collision arises at all.
      - `-domain <d>` does NOT change the scope. A file that scopes its
        nets by domain -- which is ordinary, idiomatic UPF -- leaves
        every net at the same scope, so the second
        `create_supply_net VDD_AON -domain PD_CPU -reuse` collided with
        the first and was rejected outright.

    `-reuse` means "this is the SAME net, continued", so a collision is
    exactly the expected condition under it, not an error: the existing
    record stands and this command only adds the new domain
    association. Without `-reuse`, a redeclaration is still an error.
    """
    name = _one(parsed, "name")
    reuse = bool(_one(parsed, "reuse", default=False))
    id_ = make_power_intent_id(state.current_scope, name)
    if id_ in state.supply_nets and not reuse:
        raise ValueError(f"supply net '{name}' already exists at scope '{state.current_scope}'")
    if id_ not in state.supply_nets:
        state.supply_nets[id_] = _SupplyNetBuilder(
            id=id_, name=name, scope=state.current_scope,
            # Recorded only on a net this command actually CREATES. The
            # first declaration of a rail is never itself a reuse, even
            # when later ones continue it.
            reuse=reuse,
            source_loc=state.loc(),
        )
    domain = _one(parsed, "domain")
    if domain is not None:
        # create_supply_net's -domain associates the net with a domain --
        # the same relationship set_domain_supply_net expresses, so both
        # synthesize the same DomainSupplyConnection shape rather than
        # silently dropping -domain because it wasn't set_domain_supply_net.
        state.domain_supply_connections.append(
            DomainSupplyConnection(
                domain=domain, supply_net=name, scope=state.current_scope, role=None, source_loc=state.loc()
            )
        )


def _connect_supply_net(state: _ElaborationState, parsed: dict[str, list]) -> None:
    """Both forms are supported now: `-domain` (net-to-domain) and
    `-ports` (net-to-port/pin).

    `-ports` was previously a deliberately-unregistered gap -- it doesn't
    fit the net-to-domain shape DomainSupplyConnection was built for, and
    the decision was to fail loud rather than guess at a schema. Round-trip
    import supplied the motivating usage that gap was waiting for: the GUI
    generates this form both for boundary port-to-net wiring and for every
    macro PG-pin connection, so a generated file could not be read back at
    all. Resolved by adding `ports` to DomainSupplyConnection, with
    `domain` empty for this shape -- `-ports` carries no domain, and
    inventing one would be exactly the guess the original gap avoided.
    """
    net = _one(parsed, "net")
    domain = _one(parsed, "domain")
    ports = _one(parsed, "ports", default=[])
    if domain is None and not ports:
        raise ValueError(
            f"connect_supply_net '{net}': called with no recognized data to record -- "
            "expected either -domain or -ports"
        )
    if domain is not None:
        state.domain_supply_connections.append(
            DomainSupplyConnection(domain=domain, supply_net=net, scope=state.current_scope, role=None, source_loc=state.loc())
        )
    if ports:
        state.domain_supply_connections.append(
            DomainSupplyConnection(
                domain="", supply_net=net, scope=state.current_scope, role=None,
                ports=list(ports), source_loc=state.loc(),
            )
        )


def _create_supply_set(state: _ElaborationState, parsed: dict[str, list]) -> None:
    name = _one(parsed, "name")
    id_ = make_power_intent_id(state.current_scope, name)
    if id_ in state.supply_sets:
        raise ValueError(f"supply set '{name}' already exists at scope '{state.current_scope}'")
    builder = _SupplySetBuilder(id=id_, name=name, scope=state.current_scope, source_loc=state.loc())
    # `-function` and `-supply` are the same {kind net} pair under two
    # spellings; golden.upf and the GUI generator both use `-supply`,
    # which the elaborator previously rejected outright.
    for function in list(parsed.get("function", [])) + list(parsed.get("supply", [])):
        if len(function) != 2:
            raise ValueError(f"supply set '{name}': -function/-supply expects {{kind net}}, got {function!r}")
        kind, net = function
        # nwell/pwell are modelled now too (this app's own
        # SupplySetPlacement has always had the fields). Before that an
        # otherwise-valid bias-aware file was rejected outright here.
        # Any OTHER function kind still fails loud rather than silently
        # dropping data the caller explicitly specified.
        field_for_kind = {
            "power": "power_net",
            "ground": "ground_net",
            "nwell": "nwell_net",
            "pwell": "pwell_net",
        }.get(kind)
        if field_for_kind is None:
            raise ValueError(
                f"supply set '{name}': -function/-supply kind '{kind}' is not supported "
                "(supported: power, ground, nwell, pwell)"
            )
        setattr(builder, field_for_kind, net)
    state.supply_sets[id_] = builder


def _set_domain_supply_net(state: _ElaborationState, parsed: dict[str, list]) -> None:
    domain = _one(parsed, "domain")
    for role, flag in (("primary_power", "primary_power_net"), ("primary_ground", "primary_ground_net")):
        net = _one(parsed, flag)
        if net is not None:
            state.domain_supply_connections.append(
                DomainSupplyConnection(
                    domain=domain, supply_net=net, scope=state.current_scope, role=role, source_loc=state.loc()
                )
            )


def _create_power_switch(state: _ElaborationState, parsed: dict[str, list]) -> None:
    name = _one(parsed, "name")
    id_ = make_power_intent_id(state.current_scope, name)
    if id_ in state.switches:
        raise ValueError(f"power switch '{name}' already exists at scope '{state.current_scope}'")
    # -on_state/-off_state are now parsed. The shape was previously left
    # alone because "exact UPF argument grouping isn't confidently known";
    # this project's own golden.upf settles it with real content:
    #   -on_state  {on in {ctrl}}   -> name, input supply, boolean expr
    #   -off_state {off {!ctrl}}    -> name, boolean expr (no input supply)
    # So the first element is always the state name, the LAST is the
    # boolean expression, and an optional middle element names the input
    # supply. Only the name and expression are modelled (PowerSwitchState);
    # the middle element is already carried by `input_supply`.
    #
    # -input_supply_port/-output_supply_port are {role net} PAIRS in real
    # UPF (`{in VDD}`), not bare values -- _one() previously handed back
    # the literal ['in', 'VDD'] list as the supply name.
    state.switches[id_] = _PowerSwitchBuilder(
        id=id_,
        name=name,
        scope=state.current_scope,
        domain=_one(parsed, "domain"),
        input_supply=_supply_port_net(parsed, "input_supply_port"),
        output_supply=_supply_port_net(parsed, "output_supply_port"),
        input_supply_port=_supply_port_name(parsed, "input_supply_port"),
        output_supply_port=_supply_port_name(parsed, "output_supply_port"),
        control_ports=[_control_port_net(cp) for cp in parsed.get("control_port", [])],
        control_port_pins=[_control_port_pin(cp) for cp in parsed.get("control_port", [])],
        # `-ack_port {port net [{expr}]}` -- the same pin/net split as
        # -control_port, so it reuses the same two extractors. The
        # optional third element (a boolean function) has no IR field and
        # is deliberately not invented into one.
        ack_ports=[_control_port_net(ap) for ap in parsed.get("ack_port", [])],
        ack_port_pins=[_control_port_pin(ap) for ap in parsed.get("ack_port", [])],
        states=_switch_states(parsed, name),
        source_loc=state.loc(),
    )


def _supply_port_net(parsed: dict[str, list], flag: str) -> "str | None":
    """The net from a `{role net}` supply-port pair, or the bare value if
    a file used the unpaired spelling. Shared by input and output."""
    value = _one(parsed, flag)
    if value is None or isinstance(value, str):
        return value
    if len(value) == 2:
        return value[1]
    if len(value) == 1:
        return value[0]
    raise ValueError(f"-{flag} expects {{role net}} or a bare net name, got {value!r}")


def _supply_port_name(parsed: dict[str, list], flag: str) -> "str | None":
    """The PORT half of a `{port net}` supply-port pair. None for the
    unpaired spelling, where the single value is the net."""
    value = _one(parsed, flag)
    if value is None or isinstance(value, str):
        return None
    return value[0] if len(value) == 2 else None


def _control_port_net(value) -> str:
    """The NET half of `-control_port {ctrl pwr_en}`.

    A BARE `-control_port <x>` is genuinely ambiguous and is recorded as
    BOTH the pin and the net (see _control_port_pin). Two real,
    conflicting readings exist in this project already:
    tests/backend/golden_upf/upf_basic/top.upf writes
    `-control_port sleep_n` where `sleep_n` is a real RTL port, and the
    mapper resolves it as one -- while this app's own GENERATOR writes
    the bare form carrying its GUI's switch-cell PIN name. Recording
    both keeps each consumer correct; the import suppresses the
    "net was dropped" warning when the two are identical, since nothing
    distinct was ever given."""
    if isinstance(value, str):
        return value
    return value[-1] if value else ""


def _control_port_pin(value) -> str:
    """The PIN half of `-control_port {ctrl pwr_en}` -- the switch cell's
    own pin name, as opposed to the net driving it (see
    _control_port_net). A bare value is the pin: that is the spelling
    this app's own generator writes, since its GUI tracks a pin name with
    no net to pair it with."""
    if isinstance(value, str):
        return value
    return value[0] if value else ""


def _switch_states(parsed: dict[str, list], switch_name: str) -> list:
    """Both -on_state and -off_state, in declaration order -- see
    _create_power_switch's own comment for the argument shape."""
    states = []
    for flag in ("on_state", "off_state"):
        for raw in parsed.get(flag, []):
            if isinstance(raw, str):
                states.append(PowerSwitchState(name=raw))
                continue
            if not raw:
                raise ValueError(f"power switch '{switch_name}': -{flag} expects at least a state name")
            state_name = raw[0]
            expr = raw[-1] if len(raw) > 1 else None
            states.append(PowerSwitchState(name=state_name, boolean_expr=expr))
    return states


def _refine_existing(state, builders: dict, name: str, parsed: dict[str, list], command: str, fields: dict):
    """Apply a `-update` re-issue of a strategy command onto the builder
    that already exists, instead of rejecting the duplicate name.

    `-update` is real IEEE 1801: it re-opens a named strategy so a later
    command can refine it, which is the successive-refinement methodology
    this elaborator's own accumulate-then-freeze design already exists
    for. It simply was not wired for UPF's three strategy commands -- a
    second `set_isolation ISO_A -update ...` was rejected as a duplicate
    name, so a very ordinary two-step authoring style failed outright.
    Found by hand-authoring a refinement fixture; see
    tests/frontend/golden_simple10/README.md, iteration 6.

    `fields` maps builder field name -> the value this command parsed.
    Only entries the command ACTUALLY gave are applied, so a refinement
    never blanks a field an earlier command set -- reusing CPF's own
    apply_update() rather than a second merge implementation.

    Returns True when it handled the command; False when there is nothing
    to refine, in which case the caller creates as usual (a `-update` on
    a name that does not exist yet is treated as a plain creation, which
    is what a file split across several included pieces relies on)."""
    id_ = make_power_intent_id(state.current_scope, name)
    if id_ not in builders:
        return False
    updates = {field: value for field, value in fields.items() if value not in (None, [], "")}
    apply_update(builders[id_], updates)
    return True


def _set_isolation(state: _ElaborationState, parsed: dict[str, list]) -> None:
    name = _one(parsed, "name")
    id_ = make_power_intent_id(state.current_scope, name)
    location = _one(parsed, "location")
    check_location_value(state.source_version, location, "set_isolation")
    supply = _one(parsed, "isolation_supply") or _one(parsed, "isolation_supply_set")
    if _one(parsed, "update", default=False):
        if _refine_existing(
            state, state.isolation_strategies, name, parsed, "set_isolation",
            {
                "domain": _one(parsed, "domain"),
                "source": _one(parsed, "source"),
                "sink": _one(parsed, "sink"),
                "elements": _one(parsed, "elements", default=[]),
                "location": location,
                "applies_to": _one(parsed, "applies_to"),
                "clamp_value": _one(parsed, "clamp_value"),
                "isolation_supply": supply,
                "isolation_signal": _one(parsed, "isolation_signal"),
                "isolation_sense": _one(parsed, "isolation_sense"),
                "name_prefix": _one(parsed, "name_prefix"),
            },
        ):
            return
    if id_ in state.isolation_strategies:
        raise ValueError(f"isolation strategy '{name}' already exists at scope '{state.current_scope}'")
    state.isolation_strategies[id_] = _IsolationBuilder(
        id=id_,
        name=name,
        scope=state.current_scope,
        domain=_one(parsed, "domain"),
        source=_one(parsed, "source"),
        sink=_one(parsed, "sink"),
        elements=_one(parsed, "elements", default=[]),
        location=location,
        applies_to=_one(parsed, "applies_to"),
        clamp_value=_one(parsed, "clamp_value"),
        # `-isolation_supply_set` is the UPF 2.1+ spelling (supply SETS
        # replaced bare supply nets) and is what golden.upf and the GUI
        # generator both emit; `-isolation_supply` is the older form.
        # Accept either, into the one field.
        isolation_supply=_one(parsed, "isolation_supply") or _one(parsed, "isolation_supply_set"),
        isolation_signal=_one(parsed, "isolation_signal"),
        isolation_sense=_one(parsed, "isolation_sense"),
        # `-name_suffix` is recognised at the registration but not stored:
        # there is no suffix field, exactly as for set_level_shifter.
        name_prefix=_one(parsed, "name_prefix"),
        source_loc=state.loc(),
    )


def _set_retention(state: _ElaborationState, parsed: dict[str, list]) -> None:
    name = _one(parsed, "name")
    id_ = make_power_intent_id(state.current_scope, name)
    if _one(parsed, "update", default=False):
        if _refine_existing(
            state, state.retention_strategies, name, parsed, "set_retention",
            {
                "domain": _one(parsed, "domain"),
                "elements": _one(parsed, "elements", default=[]),
                "retention_supply": _one(parsed, "retention_supply") or _one(parsed, "retention_supply_set"),
                "primary_supply": _one(parsed, "primary_supply") or _one(parsed, "primary_supply_set"),
                "save_signal": _one(parsed, "save_signal"),
                "restore_signal": _one(parsed, "restore_signal"),
            },
        ):
            return
    if id_ in state.retention_strategies:
        raise ValueError(f"retention strategy '{name}' already exists at scope '{state.current_scope}'")
    builder = _RetentionBuilder(
        id=id_,
        name=name,
        scope=state.current_scope,
        domain=_one(parsed, "domain"),
        elements=_one(parsed, "elements", default=[]),
        # `-retention_supply`/`-retention_supply_set` are the single-value
        # spellings; `-supply` is the repeatable {role set} PAIR form the
        # generator emits, carrying primary AND retention together.
        retention_supply=_one(parsed, "retention_supply") or _one(parsed, "retention_supply_set"),
        save_signal=_one(parsed, "save_signal"),
        restore_signal=_one(parsed, "restore_signal"),
        source_loc=state.loc(),
    )
    for supply in parsed.get("supply", []):
        if isinstance(supply, str):
            builder.retention_supply = builder.retention_supply or supply
            continue
        if len(supply) != 2:
            raise ValueError(f"retention strategy '{name}': -supply expects {{role set}}, got {supply!r}")
        role, supply_set = supply
        if role == "retention":
            builder.retention_supply = supply_set
        elif role == "primary":
            builder.primary_supply = supply_set
        else:
            raise ValueError(
                f"retention strategy '{name}': -supply role '{role}' is not supported (supported: primary, retention)"
            )
    state.retention_strategies[id_] = builder


def _set_level_shifter(state: _ElaborationState, parsed: dict[str, list]) -> None:
    name = _one(parsed, "name")
    id_ = make_power_intent_id(state.current_scope, name)
    if _one(parsed, "update", default=False):
        if _refine_existing(
            state, state.level_shifter_strategies, name, parsed, "set_level_shifter",
            {
                "domain": _one(parsed, "domain"),
                "source": _one(parsed, "source"),
                "sink": _one(parsed, "sink"),
                "elements": _one(parsed, "elements", default=[]),
                "location": _one(parsed, "location"),
                "applies_to": _one(parsed, "applies_to"),
                "rule": _one(parsed, "rule"),
                "threshold": _one(parsed, "threshold"),
                "input_supply": _one(parsed, "input_supply"),
                "output_supply": _one(parsed, "output_supply"),
                "name_prefix": _one(parsed, "name_prefix"),
            },
        ):
            return
    if id_ in state.level_shifter_strategies:
        raise ValueError(f"level shifter strategy '{name}' already exists at scope '{state.current_scope}'")
    location = _one(parsed, "location")
    check_location_value(state.source_version, location, "set_level_shifter")
    state.level_shifter_strategies[id_] = _LevelShifterBuilder(
        id=id_,
        name=name,
        scope=state.current_scope,
        domain=_one(parsed, "domain"),
        source=_one(parsed, "source"),
        sink=_one(parsed, "sink"),
        elements=_one(parsed, "elements", default=[]),
        location=location,
        applies_to=_one(parsed, "applies_to"),
        rule=_one(parsed, "rule"),
        threshold=_one(parsed, "threshold"),
        input_supply=_one(parsed, "input_supply"),
        output_supply=_one(parsed, "output_supply"),
        name_prefix=_one(parsed, "name_prefix"),
        source_loc=state.loc(),
    )


_NESTED_STATE_EXPR_FLAGS = ("-supply_expr", "-logic_expr")
# Recognised inside a `-state {...}` block but not modelled: simulation
# behaviour has no IR field, matching this project's standing decision to
# leave simulation-only UPF constructs out of scope.
_NESTED_STATE_IGNORED_FLAGS = ("-simstate",)


def _nested_state_expr(raw: list, applies_to: str) -> Optional[str]:
    """The expression out of a UPF 3.x `-state {NAME -flag value ...}`.

    Returns the first -supply_expr/-logic_expr value found. An unknown
    nested flag is a hard error, not a silent skip -- the same
    "unrecognized flags must be rejected" rule the command level already
    enforces, applied one level down."""
    expr = None
    index = 1
    while index < len(raw):
        flag = str(raw[index])
        if not flag.startswith("-"):
            raise ValueError(
                f"add_power_state '{applies_to}': expected a flag inside -state, got {flag!r}"
            )
        if index + 1 >= len(raw):
            raise ValueError(f"add_power_state '{applies_to}': -state flag '{flag}' has no value")
        value = raw[index + 1]
        if flag in _NESTED_STATE_EXPR_FLAGS and expr is None:
            expr = value if isinstance(value, str) else " ".join(str(v) for v in value)
        elif flag not in _NESTED_STATE_EXPR_FLAGS and flag not in _NESTED_STATE_IGNORED_FLAGS:
            raise ValueError(
                f"add_power_state '{applies_to}': unrecognized -state flag '{flag}' "
                f"(recognized: {sorted(_NESTED_STATE_EXPR_FLAGS + _NESTED_STATE_IGNORED_FLAGS)})"
            )
        index += 2
    return expr


def _create_power_state_group(state: _ElaborationState, parsed: dict[str, list]) -> None:
    """`create_power_state_group <name>` -- UPF 3.0+'s system-level state
    grouping. Declares the group; `add_power_state -group <name>` then
    adds states to it, each naming other objects' states in its own
    `-logic_expr`.

    Registered because this app now GENERATES one (Tier 2 of the
    power-state model) and could not read its own output back -- the
    command did not exist, so Tcl rejected the name."""
    name = _one(parsed, "name")
    id_ = make_power_intent_id(state.current_scope, name)
    if id_ in state.power_state_groups:
        raise ValueError(f"power state group '{name}' already exists at scope '{state.current_scope}'")
    state.power_state_groups[id_] = _PowerStateGroupBuilder(
        id=id_, name=name, scope=state.current_scope, source_loc=state.loc()
    )


def _associate_supply_set(state: _ElaborationState, parsed: dict[str, list]) -> None:
    """`associate_supply_set <supply_set_ref> -handle <handle_ref>`.

    UPF 2.1+'s third way of saying what `create_power_domain -supply
    {primary SS}` and `set_domain_supply_net` already say: which supply
    set powers a domain. The handle is a dotted reference --
    `PD_TOP.primary` names domain PD_TOP's own `primary` supply-set
    handle -- so this binds SS_TOP to it.

    TWO mappings, by which handle is named:

      - `.primary` == `create_power_domain PD -supply {primary SS}`, so
        it lands on `PowerDomain.primary_supply` -- the SAME field both
        other spellings already use, so nothing downstream has to learn a
        third representation of one fact.
      - ANY OTHER handle (`.aon`, `.default_retention`, a design's own
        named handle, ...) == `create_power_domain PD -available_supplies
        {SS}`, so it appends to `PowerDomain.available_supplies`. A
        non-primary handle is an ADDITIONAL supply the domain can draw
        on, which is exactly what that list means.

    An earlier version rejected every non-primary handle outright, on the
    reasoning that folding one into `primary` would attach the wrong
    supply set. That much was right -- but the alternative was not
    rejection: `available_supplies` is where a secondary association
    belongs, and refusing it failed files that are perfectly valid.

    Added because a real, idiomatic UPF 3.1 file used the command and it
    did not exist at all -- Tcl rejected the command NAME, so the whole
    file failed before any flag was examined."""
    supply_set = _one(parsed, "name")
    handle = _one(parsed, "handle")
    if not handle or "." not in handle:
        raise ValueError(
            f"associate_supply_set '{supply_set}': -handle expects <domain>.<handle> "
            f"(e.g. PD_TOP.primary), got {handle!r}"
        )
    domain_name, _, handle_name = handle.rpartition(".")
    # Resolve in THIS command's own scope first, and only fall back to a
    # global by-name search when the current scope has no such domain.
    # A file sourced under `load_upf -instance u_sram` can legitimately
    # declare a `PD_TOP` of its own while the parent design has one too
    # -- resolving by bare name across all scopes then reports "matches 2
    # power domains" and fails, when the intended one is unambiguous:
    # it is the one in the scope the command itself was written in.
    scoped_id = make_power_intent_id(state.current_scope, domain_name)
    builder = state.domains.get(scoped_id)
    if builder is None:
        builder = find_by_name(state.domains, domain_name, "associate_supply_set", "power domain")
    if handle_name == "primary":
        apply_update(builder, {"primary_supply": supply_set})
    elif supply_set not in builder.available_supplies:
        # Appended, not assigned: several handles can associate several
        # supply sets with one domain, and each is an addition.
        builder.available_supplies = [*builder.available_supplies, supply_set]


def _add_power_state(state: _ElaborationState, parsed: dict[str, list]) -> None:
    """`-state` is REPEATABLE, and each occurrence is a
    `{NAME value}` pair: `add_power_state VDD -state {ON 1.2} -state {OFF off}`
    declares two states on one supply in a single command.

    The original handler read a single scalar `-state` and built one
    PowerState from it, so a real multi-state declaration (which is what
    golden.upf and the GUI generator both emit) collapsed to one state
    whose name was the literal list ['ON', '1.2']. Each pair now becomes
    its own PowerState, with the value carried in `expr` -- the same field
    a `-logic_expr`/`-supply_expr` form uses, since both answer "what
    condition/level does this state mean".
    """
    # The object can be named POSITIONALLY (`add_power_state VDD ...`,
    # the 2.0-era spelling this app's own generator writes) or by FLAG
    # (`add_power_state -supply SS_TOP ...` / `-domain PD_TOP ...`, the
    # 2.1+ spelling a real file uses). Both occur; neither is wrong. The
    # positional is optional so the flag form parses at all, and the flag
    # is the fallback for `applies_to` so every state still knows what it
    # belongs to.
    positional = _one(parsed, "object")
    supply = _one(parsed, "supply")
    domain = _one(parsed, "domain")
    # A BARE `-supply`/`-domain` marker parses to True, not a name -- it
    # QUALIFIES the positional object rather than naming one. Resolved to
    # that object here so `PowerState.supply`/`.domain` always hold a
    # name, never a bool: they are typed Optional[str], and a bool
    # leaking through silently broke every by-name lookup downstream
    # (the Power State Group reader keys on `.supply` directly).
    if supply is True:
        supply = positional
    if domain is True:
        domain = positional
    applies_to = positional or supply or domain or _one(parsed, "group")
    raw_states = parsed.get("state", [])
    if not raw_states:
        raise ValueError(f"add_power_state '{applies_to}': at least one -state is required")
    shared_expr = _one(parsed, "logic_expr") or _one(parsed, "supply_expr")
    for raw in raw_states:
        if isinstance(raw, str):
            state_name, value = raw, None
        elif len(raw) == 1:
            state_name, value = raw[0], None
        elif len(raw) == 2:
            state_name, value = raw
        elif len(raw) > 2 and str(raw[1]).startswith("-"):
            # The UPF 3.x shape: `-state {ON -supply_expr {...} -simstate
            # NORMAL}` -- a name followed by the state's OWN nested
            # flags, not a {name value} pair. Read the expression out of
            # whichever of -supply_expr/-logic_expr is present; -simstate
            # and any other nested flag is recognised and skipped rather
            # than making the whole command fail, since this IR has no
            # simulation-behaviour field (see PowerState's own docstring
            # on simulation-only constructs being deliberately out of
            # scope).
            state_name, value = raw[0], _nested_state_expr(raw, applies_to)
        else:
            raise ValueError(f"add_power_state '{applies_to}': -state expects {{name value}}, got {raw!r}")
        id_ = make_power_intent_id(state.current_scope, f"{applies_to}.{state_name}")
        if id_ in state.power_states:
            raise ValueError(
                f"power state '{state_name}' on '{applies_to}' already exists at scope '{state.current_scope}'"
            )
        state.power_states[id_] = _PowerStateBuilder(
            id=id_,
            name=state_name,
            scope=state.current_scope,
            applies_to=applies_to,
            expr=value if value is not None else shared_expr,
            shape=PowerStateShape.ADD_POWER_STATE,
            domain=domain,
            supply=supply,
            group=_one(parsed, "group"),
            complete=_one(parsed, "complete", default=False),
            source_loc=state.loc(),
        )


def _create_power_state_group(state: _ElaborationState, parsed: dict[str, list]) -> None:
    name = _one(parsed, "name")
    id_ = make_power_intent_id(state.current_scope, name)
    if id_ in state.power_state_groups:
        raise ValueError(f"power state group '{name}' already exists at scope '{state.current_scope}'")
    state.power_state_groups[id_] = _PowerStateGroupBuilder(
        id=id_,
        name=name,
        scope=state.current_scope,
        members=_one(parsed, "elements", default=[]),
        source_loc=state.loc(),
    )


# --- Commands added so the app's own generated output can be read back ----
#
# Every one of these appears in this project's own golden.upf and is
# emitted by the GUI's "Extract Power Intent", but none were recognized --
# a generated file failed on its very first line (`set_scope .`). They are
# real UPF commands, not invented convenience syntax; the elaborator was
# simply scoped to a narrower subset chosen when the goal was reading
# third-party files rather than its own output.


def _set_scope(state: _ElaborationState, parsed: dict[str, list]) -> None:
    """`set_scope <instance>` moves the current scope, in place. Unlike
    `load_upf -scope` (which nests, because file-sourcing has a real
    call/return to unwind) this is a plain reassignment -- exactly the
    shape _ElaborationState.set_scope() already existed for, added for
    CPF's set_instance.

    `.` means "the top scope", which is the empty-string scope this IR
    uses throughout; it is normalized rather than stored literally, so a
    construct declared after `set_scope .` gets the same scope as one
    declared before any set_scope at all."""
    scope = _one(parsed, "scope")
    state.set_scope("" if scope in (".", None) else scope)


def _map_power_switch(state: _ElaborationState, parsed: dict[str, list]) -> None:
    """`map_power_switch {SW} -lib_cells {CELL} [-port_map {...}]` names
    the library cell implementing an already-declared switch -- a second
    command refining a construct declared earlier, the
    accumulate-then-freeze shape the builders exist for. Only the first
    cell is modelled; the GUI has one free-text Cell Type field per
    switch.

    `-port_map` maps each abstract UPF switch port (IN/OUT/CTRL) onto the
    real library cell pin implementing it. It arrives as a flat list of
    alternating names, which is how UPF writes it."""
    cells = _one(parsed, "lib_cells", default=[])
    targets = _one(parsed, "switch_name_list", default=[])
    if not cells:
        raise ValueError("map_power_switch: -lib_cells is required")
    if not targets:
        raise ValueError("map_power_switch: a switch name is required")
    if isinstance(targets, str):
        targets = [targets]
    # The WHOLE list, not just its first entry (audit B5). `-lib_cells
    # {HDR_X16 HDR_X8}` offers a tool a choice of cells for one rule, and
    # real files routinely give two or three. Keeping only the first
    # silently narrowed the intent. `cell_type` is free text, so a
    # space-separated list round-trips through it unchanged and is
    # re-emitted verbatim inside `-lib_cells {...}`.
    cell = cells if isinstance(cells, str) else " ".join(cells)
    for target in targets:
        id_ = make_power_intent_id(state.current_scope, target)
        builder = state.switches.get(id_)
        if builder is None:
            raise ValueError(f"map_power_switch '{target}': no power switch of that name at scope '{state.current_scope}'")
        builder.cell_type = cell
        port_map = _one(parsed, "port_map", default=None)
        if port_map:
            tokens = [port_map] if isinstance(port_map, str) else list(port_map)
            builder.port_map = {
                abstract: real for abstract, real in zip(tokens[::2], tokens[1::2])
            }


def _set_level_shifter_cell(state: _ElaborationState, parsed: dict[str, list]) -> None:
    """Binds a level shifter strategy to its library cell(s).

    TWO command spellings reach this handler, and both are real:

      - `map_level_shifter_cell <strategy> -domain <d> -lib_cells {A B}`
        -- the form a real reference file uses and the one this app now
        generates (audit B10). Positional name, `-domain`, a cell LIST;
        the same shape as `map_power_switch`.
      - `set_level_shifter_cell -level_shifter <strategy> -cell_name <c>`
        -- what this generator wrote before B10, kept so files already
        written in that spelling still elaborate.

    `-domain` is accepted and not otherwise used: the strategy already
    names its own domain, and re-deriving it here would only create a
    second place for the two to disagree."""
    strategy_name = _one(parsed, "level_shifter") or _one(parsed, "strategy")
    # `-lib_cells` is a LIST; the whole list is kept, not its first entry
    # (audit B5). `cell_type` is free text, so a space-separated list
    # survives and is re-emitted verbatim.
    cells = _one(parsed, "lib_cells", default=None)
    if cells is not None:
        cell = cells if isinstance(cells, str) else " ".join(cells)
    else:
        cell = _one(parsed, "cell_name")
    id_ = make_power_intent_id(state.current_scope, strategy_name)
    builder = state.level_shifter_strategies.get(id_)
    if builder is None:
        raise ValueError(
            f"level shifter cell mapping '{strategy_name}': no level shifter strategy of that name "
            f"at scope '{state.current_scope}'"
        )
    builder.cell_type = cell


def _set_retention_control(state: _ElaborationState, parsed: dict[str, list]) -> None:
    """`set_retention_control <strategy> -domain <d> -save_signal <s>
    -restore_signal <r>` -- refines an already-declared retention strategy
    with its save/restore signals, which real UPF separates from
    `set_retention` itself. Both fields already exist on the strategy, so
    this fills them in rather than adding anything new."""
    strategy_name = _one(parsed, "name")
    id_ = make_power_intent_id(state.current_scope, strategy_name)
    builder = state.retention_strategies.get(id_)
    if builder is None:
        raise ValueError(
            f"set_retention_control '{strategy_name}': no retention strategy of that name "
            f"at scope '{state.current_scope}'"
        )
    save = _one(parsed, "save_signal")
    restore = _one(parsed, "restore_signal")
    if save is not None:
        builder.save_signal = save
    if restore is not None:
        builder.restore_signal = restore


def _set_port_attributes(state: _ElaborationState, parsed: dict[str, list]) -> None:
    """`set_port_attributes -ports {a b} -driver_supply <set>` /
    `-receiver_supply <set>` asserts the boundary-facing supply for design
    ports. One PortAttribute per command occurrence -- a file legitimately
    issues several (the GUI emits one per supply role), and merging them
    by ports would lose which command said what.

    Keyed by occurrence index rather than by name because the command has
    no name of its own; `id` stays opaque, per make_power_intent_id's own
    contract."""
    ports = _one(parsed, "ports", default=[])
    if not ports:
        raise ValueError("set_port_attributes: -ports is required")
    index = len(state.port_attributes)
    id_ = make_power_intent_id(state.current_scope, f"port_attributes[{index}]")
    state.port_attributes[id_] = _PortAttributeBuilder(
        id=id_,
        scope=state.current_scope,
        ports=list(ports),
        driver_supply=_one(parsed, "driver_supply"),
        receiver_supply=_one(parsed, "receiver_supply"),
        source_loc=state.loc(),
    )
