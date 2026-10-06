"""Content-producing CPF verb handlers.

All command shapes below are verified directly against the Si2 CPF 1.0
primary spec (freely available, unlike IEEE 1801) -- not secondary/tutorial
sources. Where a real option is implementation-detail/electrical-
characteristic in nature (library-cell binding, IR-drop limits, bias nets,
power-up timing), it's declared as a recognized option (so real files don't
fail) but deliberately not stored in the IR -- same "downstream analysis,
not structural power intent" carve-out already established for UPF, cited
explicitly per-option below rather than silently dropped.

`create_X`/`update_X` by-name merging (verified real for CPF, unlike the
earlier mistaken UPF framing -- see docs/milestone2_powerintent_elaborator.md)
uses `precedence.py`'s `find_by_name`/`apply_update`.

Multi-domain `-from`/`-to` (isolation/level-shifter rules) and wildcard
`-names` (update_X commands) are explicitly out of scope for this pass --
see `_single_domain` and `precedence.find_by_name` respectively. Not
guessed at; both fail loud rather than silently pick one match or drop
extra domains.
"""

from __future__ import annotations

from backend.elaborator.power_intent.cpf.precedence import apply_update, find_by_name
from backend.elaborator.power_intent.ir import DomainSupplyConnection, PowerStateShape, make_power_intent_id
from backend.elaborator.power_intent.shared.builders import (
    _DomainBuilder,
    _ElaborationState,
    _IsolationBuilder,
    _LevelShifterBuilder,
    _PowerStateBuilder,
    _PowerSwitchBuilder,
    _RetentionBuilder,
    _SupplyNetBuilder,
)
from backend.elaborator.power_intent.shared.tcl_runtime import _one


def _single_domain(values: list[str], flag_name: str, command_name: str):
    """`-from`/`-to` are documented as domain *lists* (pin filters), but
    IsolationStrategy.source/sink are single-ref fields (see their
    docstring). Only the common single-domain case is supported; a
    genuinely multi-domain list fails loud rather than silently dropping
    all but the first entry."""
    if not values:
        return None
    if len(values) > 1:
        raise ValueError(
            f"'{command_name}': multi-domain {flag_name} ({values}) is a tracked gap, not yet supported -- "
            "only a single domain is currently modeled (see IsolationStrategy's docstring)"
        )
    return values[0]


def _create_power_domain(state: _ElaborationState, parsed: dict[str, list]) -> None:
    name = _one(parsed, "name")
    id_ = make_power_intent_id(state.current_scope, name)
    if id_ in state.domains:
        raise ValueError(f"power domain '{name}' already exists at scope '{state.current_scope}'")
    state.domains[id_] = _DomainBuilder(
        id=id_,
        name=name,
        scope=state.current_scope,
        elements=_one(parsed, "instances", default=[]),
        boundary_ports=_one(parsed, "boundary_ports", default=[]),
        shutoff_condition=_one(parsed, "shutoff_condition"),
        default_restore_edge=_one(parsed, "default_restore_edge"),
        default_save_edge=_one(parsed, "default_save_edge"),
        power_up_state=_one(parsed, "power_up_states"),
        is_default=_one(parsed, "default", default=False),
        source_loc=state.loc(),
    )


def _update_power_domain(state: _ElaborationState, parsed: dict[str, list]) -> None:
    name = _one(parsed, "name")
    # find_by_name still validates the name was actually create_power_domain'd
    # (raises clearly otherwise, tested) even though apply_update isn't
    # needed anymore -- every option this command sets now either goes to
    # domain_supply_connections (power/ground nets, below) or is a
    # deliberately-unstored implementation detail, so there's no PowerDomain
    # builder field left for update_power_domain to actually mutate.
    find_by_name(state.domains, name, "update_power_domain", "power domain")
    # -internal_power_net/-internal_ground_net are NOT stored as PowerDomain
    # fields -- unified onto DomainSupplyConnection with role="primary_power"/
    # "primary_ground", the same roles UPF's set_domain_supply_net
    # -primary_power_net/-primary_ground_net already produces. Confirmed
    # during the 2b audit that both describe the same real-world fact ("the
    # net driving this domain's functional logic"), not just a naming
    # coincidence -- unlike e.g. IsolationStrategy.location, which looks
    # like a cross-format match but verifiably isn't.
    #
    # CAVEAT, not fully resolved: the CPF spec states -internal_power_net is
    # "required when you use header cells," which hints it may specifically
    # mean the *post-switch, load-side* net rather than an unswitched
    # upstream supply. UPF's own -primary_power_net semantics are
    # secondary-source-tier only here (IEEE 1801 is paywalled, unlike CPF's
    # freely available spec), so whether UPF's "primary" and CPF's
    # "internal" mean the exact same point in the power network -- pre- or
    # post-switch -- has NOT been independently confirmed on the UPF side.
    # If you're relying on this field to reason about switch placement or
    # electrical topology (not just "which net logically belongs to this
    # domain"), verify against a real design with an actual power switch on
    # both formats before trusting the roles line up exactly.
    #
    # -primary_power_net/-primary_ground_net are ALSO accepted, onto the
    # same two roles. Found by round-tripping this app's own generated
    # CPF, which the elaborator rejected outright: the hand-written
    # reference examples/upf_example_1_sram/cpf/golden.cpf uses the
    # `-primary_*` spelling, and per the CAVEAT above the `-internal_*`
    # pair is the one with the narrower (possibly post-switch) meaning,
    # so accepting only `-internal_*` was the gap -- not a case of the
    # generator inventing a flag. Which spelling a file used is not
    # preserved: both land on the same role, matching how UPF's own
    # -primary_power_net already normalises here.
    for role, flags in (
        ("primary_power", ("internal_power_net", "primary_power_net")),
        ("primary_ground", ("internal_ground_net", "primary_ground_net")),
    ):
        for flag in flags:
            net = _one(parsed, flag)
            if net is not None:
                state.domain_supply_connections.append(
                    DomainSupplyConnection(
                        domain=name, supply_net=net, scope=state.current_scope, role=role, source_loc=state.loc()
                    )
                )
    # -min_power_up_time/-max_power_up_time/-pmos_bias_net/-nmos_bias_net/
    # -user_attributes/-rail_mapping/-library_set are implementation-detail
    # options (recognized in elaborate.py's options set so real files don't
    # fail) -- deliberately not stored, same carve-out as UPF's IR-drop-
    # limit options.


def _create_nets(state: _ElaborationState, parsed: dict[str, list]) -> None:
    """Shared by create_power_nets and create_ground_nets -- identical
    shape (modulo -external_shutoff_condition, power-only, handled by the
    caller not passing that option)."""
    names = _one(parsed, "nets", default=[])
    voltage = _one(parsed, "voltage")
    for name in names:
        id_ = make_power_intent_id(state.current_scope, name)
        if id_ in state.supply_nets:
            raise ValueError(f"supply net '{name}' already exists at scope '{state.current_scope}'")
        state.supply_nets[id_] = _SupplyNetBuilder(
            id=id_, name=name, scope=state.current_scope, voltage=voltage, source_loc=state.loc()
        )
    # -external_shutoff_condition/-internal/-user_attributes/
    # -peak_ir_drop_limit/-average_ir_drop_limit are electrical-
    # characteristic/downstream-analysis options, same carve-out as above.


def _create_power_switch_rule(state: _ElaborationState, parsed: dict[str, list]) -> None:
    name = _one(parsed, "name")
    id_ = make_power_intent_id(state.current_scope, name)
    if id_ in state.switches:
        raise ValueError(f"power switch rule '{name}' already exists at scope '{state.current_scope}'")
    state.switches[id_] = _PowerSwitchBuilder(
        id=id_,
        name=name,
        scope=state.current_scope,
        domain=_one(parsed, "domain"),
        # CPF's {-external_power_net | -external_ground_net} are mutually
        # exclusive per the spec; mapped onto input_supply (whichever was
        # given) rather than forcing CPF's power/ground distinction into
        # UPF-shaped input_supply/output_supply, which mean something
        # different (a switch's input vs. output side, not power vs.
        # ground). Not a clean equivalence -- same caveat style as
        # IsolationStrategy.location.
        input_supply=_one(parsed, "external_power_net") or _one(parsed, "external_ground_net"),
        source_loc=state.loc(),
    )


def _update_power_switch_rule(state: _ElaborationState, parsed: dict[str, list]) -> None:
    """The switch counterpart to _update_isolation_rules/
    _update_level_shifter_rules, which already existed.

    Logged as a deferred gap during 2b ("three `update_*` commands...
    names confirmed, shapes not yet read") and closed here: this app's own
    CPF writer emits it to carry a switch's Cell Type, so a generated
    file could not be read back at all -- the command simply did not
    exist, which surfaces as Tcl's own "invalid command name" rather than
    an unrecognized-flag error.

    Singular `-name`, matching CPF's own asymmetry: the isolation and
    level-shifter update commands are plural (`update_isolation_rules
    -names`), this one is not."""
    name = _one(parsed, "name")
    builder = find_by_name(state.switches, name, "update_power_switch_rule", "power switch rule")
    updates = {}
    cells = _one(parsed, "cells", default=[])
    if cells:
        # The WHOLE list (audit B5): `-cells` offers a tool a choice of
        # cells for one rule, and keeping only the first silently
        # narrowed the intent. cell_type is free text, so a
        # space-separated list survives and is re-emitted verbatim.
        updates["cell_type"] = " ".join(cells) if not isinstance(cells, str) else cells
    # CPF 2.x numbers this option (`-acknowledge_receiver_1`/`_2`,
    # Si2 CPF 2.1 p. 266); CPF 1.0 spelled it bare and took a PIN
    # rather than an expression. Both are read, newest first, so a file
    # written either way loads -- see this command's own registration
    # for why writing stays conservative.
    receiver = _one(parsed, "acknowledge_receiver_1")
    if receiver is None:
        receiver = _one(parsed, "acknowledge_receiver")
    if receiver is not None:
        # CPF's acknowledge_receiver is the DESIGN-side pin the switch
        # cell's acknowledge output drives -- the same thing UPF states
        # as the net half of `-ack_port {port net}`, so it normalises
        # onto the same field rather than a CPF-only one.
        updates["ack_ports"] = [receiver]
    apply_update(builder, updates)
    # -prefix/-library_set/-peak_ir_drop_limit and friends are
    # implementation-detail options, same carve-out as the isolation and
    # level-shifter update commands above.


def _create_global_connection(state: _ElaborationState, parsed: dict[str, list]) -> None:
    """CPF's own `create_global_connection` (Si2 CPF 2.1, p. 129).

    The counterpart of UPF's `connect_supply_net -ports`, and what this
    app's CPF writer emits for a macro's PG pins now that the audit
    established `connect_supply_net` is not a CPF command at all.

    Normalised onto the SAME `DomainSupplyConnection` records the UPF
    handler produces -- `-pins` and `-ports` both land in `ports`, since
    the IR draws no pin/port distinction here and a consumer should not
    have to learn one per source format. `-pg_type` selects pins by TYPE
    rather than by name, which this IR has nowhere to put; it is
    recorded as an unmodelled construct rather than silently dropped."""
    net = _one(parsed, "net")
    if net is None:
        raise ValueError("create_global_connection: -net is required")
    domain = _one(parsed, "domain")
    targets = list(_one(parsed, "pins", default=[]) or []) + list(_one(parsed, "ports", default=[]) or [])
    pg_type = _one(parsed, "pg_type")
    if not targets and pg_type is None and domain is None:
        raise ValueError(
            f"create_global_connection '{net}': called with no recognized data to record -- "
            "expected one of -pins, -ports or -pg_type"
        )
    if targets:
        state.domain_supply_connections.append(
            DomainSupplyConnection(
                domain=domain or "", supply_net=net, scope=state.current_scope,
                role=None, ports=targets, source_loc=state.loc(),
            )
        )
    elif domain is not None:
        state.domain_supply_connections.append(
            DomainSupplyConnection(
                domain=domain, supply_net=net, scope=state.current_scope,
                role=None, source_loc=state.loc(),
            )
        )


def _create_isolation_rule(state: _ElaborationState, parsed: dict[str, list]) -> None:
    name = _one(parsed, "name")
    id_ = make_power_intent_id(state.current_scope, name)
    if id_ in state.isolation_strategies:
        raise ValueError(f"isolation rule '{name}' already exists at scope '{state.current_scope}'")
    state.isolation_strategies[id_] = _IsolationBuilder(
        id=id_,
        name=name,
        scope=state.current_scope,
        source=_single_domain(_one(parsed, "from", default=[]), "-from", "create_isolation_rule"),
        sink=_single_domain(_one(parsed, "to", default=[]), "-to", "create_isolation_rule"),
        elements=_one(parsed, "pins", default=[]),
        exclude_elements=_one(parsed, "exclude", default=[]),
        condition=_one(parsed, "isolation_condition"),
        isolation_target=_one(parsed, "isolation_target"),
        clamp_value=_one(parsed, "isolation_output"),
        source_loc=state.loc(),
    )


def _update_isolation_rules(state: _ElaborationState, parsed: dict[str, list]) -> None:
    names = _one(parsed, "names", default=[])
    location = _one(parsed, "location")
    # -isolation_power_net/-isolation_ground_net: the rails powering the
    # isolation cells. Both appear in this project's own golden.cpf, and
    # neither was recognized -- a generated CPF file could not be read
    # back. UPF's equivalent is one `-isolation_supply_set` naming a SET,
    # so the power net shares `isolation_supply` with it while the ground
    # net gets its own field; see IsolationStrategy's own comment for why
    # a consumer must check source_format before resolving either.
    power_net = _one(parsed, "isolation_power_net")
    ground_net = _one(parsed, "isolation_ground_net")
    prefix = _one(parsed, "prefix")
    for name in names:
        builder = find_by_name(state.isolation_strategies, name, "update_isolation_rules", "isolation rule")
        updates = {}
        if location is not None:
            updates["location"] = location
        if power_net is not None:
            updates["isolation_supply"] = power_net
        if ground_net is not None:
            updates["isolation_ground_net"] = ground_net
        # `-prefix` IS read now -- it was recognized but never stored,
        # the "recognized but never read" failure mode this project
        # already documents. The spec defines it as "the prefix to be
        # used when creating the isolation logic" (default CPF_ISO_),
        # which is exactly the GUI's own Name Prefix, not an
        # implementation detail.
        if prefix is not None:
            updates["name_prefix"] = prefix
        # -cells/-library_set/-combine_level_shifting/
        # -open_source_pins_only remain implementation-detail/
        # library-binding options, same carve-out as update_power_domain's.
        apply_update(builder, updates)


def _create_level_shifter_rule(state: _ElaborationState, parsed: dict[str, list]) -> None:
    name = _one(parsed, "name")
    id_ = make_power_intent_id(state.current_scope, name)
    if id_ in state.level_shifter_strategies:
        raise ValueError(f"level shifter rule '{name}' already exists at scope '{state.current_scope}'")
    state.level_shifter_strategies[id_] = _LevelShifterBuilder(
        id=id_,
        name=name,
        scope=state.current_scope,
        source=_single_domain(_one(parsed, "from", default=[]), "-from", "create_level_shifter_rule"),
        sink=_single_domain(_one(parsed, "to", default=[]), "-to", "create_level_shifter_rule"),
        elements=_one(parsed, "pins", default=[]),
        exclude_elements=_one(parsed, "exclude", default=[]),
        source_loc=state.loc(),
    )


def _update_level_shifter_rules(state: _ElaborationState, parsed: dict[str, list]) -> None:
    names = _one(parsed, "names", default=[])
    location = _one(parsed, "location")
    prefix = _one(parsed, "prefix")
    for name in names:
        builder = find_by_name(state.level_shifter_strategies, name, "update_level_shifter_rules", "level shifter rule")
        updates = {}
        if location is not None:
            updates["location"] = location
        # `-prefix` IS read now, same as the isolation counterpart above:
        # "the prefix to be used when creating this logic" (default
        # CPF_LS_) is the GUI's own Name Prefix.
        if prefix is not None:
            updates["name_prefix"] = prefix
        # -cells/-library_set remain implementation-detail options, same
        # carve-out as above.
        apply_update(builder, updates)


def _create_state_retention_rule(state: _ElaborationState, parsed: dict[str, list]) -> None:
    name = _one(parsed, "name")
    id_ = make_power_intent_id(state.current_scope, name)
    if id_ in state.retention_strategies:
        raise ValueError(f"state retention rule '{name}' already exists at scope '{state.current_scope}'")
    state.retention_strategies[id_] = _RetentionBuilder(
        id=id_,
        name=name,
        scope=state.current_scope,
        domain=_one(parsed, "domain"),
        elements=_one(parsed, "instances", default=[]),
        restore_signal=_one(parsed, "restore_edge"),
        save_signal=_one(parsed, "save_edge"),
        source_loc=state.loc(),
    )


def _create_nominal_condition(state: _ElaborationState, parsed: dict[str, list]) -> None:
    """CPF's named, globally-reusable voltage condition.

    Another 2b gap closed for the same reason as
    _update_power_switch_rule: this app's own CPF writer emits one per
    distinct state name, and the command did not exist, so a generated
    file failed at Tcl level ("invalid command name") before any flag was
    even looked at. Present in this project's own golden.cpf, three of
    them.

    Recorded as a PowerState with no referent. A nominal condition is
    deliberately NOT attached to any supply or domain -- it is a named
    voltage that `create_power_mode -domain_conditions {PD@COND}` then
    refers to -- so `applies_to` stays None, exactly as
    _create_power_mode's own already does, and the voltage lands in
    `expr` as the raw authored text rather than being parsed into a
    number this IR has no numeric field for.

    `-state off` is carried into the expression too, since "0.0 at state
    off" and "0.0 while on" are different facts and dropping the state
    would merge them."""
    name = _one(parsed, "name")
    id_ = make_power_intent_id(state.current_scope, name)
    if id_ in state.power_states:
        raise ValueError(f"nominal condition '{name}' already exists at scope '{state.current_scope}'")
    parts = []
    voltage = _one(parsed, "voltage")
    if voltage is not None:
        parts.append(str(voltage))
    condition_state = _one(parsed, "state")
    if condition_state is not None:
        parts.append(str(condition_state))
    state.power_states[id_] = _PowerStateBuilder(
        id=id_,
        name=name,
        scope=state.current_scope,
        applies_to=None,
        expr=" ".join(parts) if parts else None,
        shape=PowerStateShape.CPF_POWER_MODE,
        source_loc=state.loc(),
    )


def _create_power_mode(state: _ElaborationState, parsed: dict[str, list]) -> None:
    name = _one(parsed, "name")
    id_ = make_power_intent_id(state.current_scope, name)
    if id_ in state.power_states:
        raise ValueError(f"power mode '{name}' already exists at scope '{state.current_scope}'")
    domain_conditions = _one(parsed, "domain_conditions", default=[])
    state.power_states[id_] = _PowerStateBuilder(
        id=id_,
        name=name,
        scope=state.current_scope,
        applies_to=None,
        expr=" ".join(domain_conditions) if domain_conditions else None,
        shape=PowerStateShape.CPF_POWER_MODE,
        is_default=_one(parsed, "default", default=False),
        source_loc=state.loc(),
    )
