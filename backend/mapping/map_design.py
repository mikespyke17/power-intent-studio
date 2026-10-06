"""Milestone 3 orchestrator.

`map_design()` walks every construct in a `PowerIntentDesign` and resolves
every field classified as an RTL-hierarchy or intra-power-intent
reference, producing a `MappedDesign`. The classification itself --
which field points where, and why -- is recorded field-by-field below and
in docs/milestone2_powerintent_elaborator.md's Milestone 3 planning
section; this docstring doesn't repeat it.

Only fields with a *verified* target are actually looked up here. A few
reference-shaped fields have a real, spec-confirmed reference on one side
but no confirmed target dict (e.g. `PowerDomain.primary_supply` could
plausibly be a SupplySet or a SupplyNet reference -- both exist as real
UPF/CPF concepts, but which one(s) `primary_supply` actually points at was
never independently verified the way input_supply/output_supply was this
round). Those are listed in `_NOT_YET_WIRED` below -- each still gets a
`ResolvedReference` with `kind=NOT_ATTEMPTED` (see `_not_attempted`/
`_not_attempted_list` below), not silence: an earlier version of this
module simply never visited these fields, which left them invisible to
any consumer with no way to tell "checked, fine" apart from "never looked
at" -- caught during review before backend/validation/ was designed
against that gap. Revisit once each field is checked the same way
save_signal/input_supply were checked this round.
"""

from __future__ import annotations

from backend.elaborator.power_intent.ir import PowerIntentDesign
from backend.elaborator.rtl.ir import Design
from backend.mapping.power_intent_resolver import resolve_power_intent_reference
from backend.mapping.rtl_resolver import resolve_rtl_reference
from backend.mapping.schema import MappedDesign, ReferenceLocation, ResolutionKind, ResolvedReference

_NOT_ATTEMPTED_DETAIL = (
    "no independently verified target dict for this field yet -- see "
    "docs/milestone3_mapping.md's 'not wired yet' section"
)

# Fields with a real reference shape but no independently verified target
# dict yet -- logged, not guessed at. Revisit once each is checked the same
# way save_signal/input_supply were checked this round.
_NOT_YET_WIRED = (
    "PowerDomain.primary_supply",
    "IsolationStrategy.isolation_supply",
    "RetentionStrategy.retention_supply",
    "PowerState.supply",
    "PowerState.applies_to",
    # PowerStateGroup.members: upf_basic's own fixture shows this naming a
    # SupplySet ("PSG_TOP -elements {SS_CORE}", asserted in
    # test_upf_elaborate.py), not a PowerState -- despite living right next
    # to PowerState in the schema. That one data point isn't the same as an
    # independently spec-verified target dict the way input_supply/
    # save_signal were checked this round, so left unwired rather than
    # generalizing from a single fixture's authoring choice.
    "PowerStateGroup.members",
)

# Fields deliberately excluded -- raw expressions, literal values, or the
# SupplyPort/SupplyNet-vs-RTL exclusion. See schema.py's field-level
# comments and docs/milestone2_powerintent_elaborator.md's Milestone 3
# planning section for the reasoning behind each.
_OUT_OF_SCOPE = (
    "PowerState.expr",
    "RetentionStrategy.save_signal",
    "RetentionStrategy.restore_signal",
    "IsolationStrategy.condition",
    "IsolationStrategy.clamp_value",
    "PowerDomain.shutoff_condition",
    "PowerDomain.power_up_state",
    "PowerDomain.default_restore_edge",
    "PowerDomain.default_save_edge",
    "PowerSwitchState.boolean_expr",
    "SupplyPort.name",
    "SupplyNet.name",
)


def _rtl_list_refs(values, scope, source_format, rtl, virtual_names, construct_kind, construct_id, field_name):
    refs = []
    for i, raw in enumerate(values):
        kind, path, def_id, detail = resolve_rtl_reference(raw, scope, source_format, rtl, virtual_names)
        refs.append(
            ResolvedReference(
                raw=raw,
                location=ReferenceLocation(construct_kind, construct_id, field_name, i),
                kind=kind,
                resolved_path=path,
                resolved_definition_id=def_id,
                detail=detail,
            )
        )
    return refs


def _not_attempted(raw, construct_kind, construct_id, field_name, index=None):
    if raw is None:
        return None
    return ResolvedReference(
        raw=raw,
        location=ReferenceLocation(construct_kind, construct_id, field_name, index),
        kind=ResolutionKind.NOT_ATTEMPTED,
        detail=_NOT_ATTEMPTED_DETAIL,
    )


def _not_attempted_list(values, construct_kind, construct_id, field_name):
    return [_not_attempted(raw, construct_kind, construct_id, field_name, i) for i, raw in enumerate(values)]


def _pi_ref(raw, scope, dict_name, power_intent, construct_kind, construct_id, field_name):
    if raw is None:
        return None
    kind, pid, detail = resolve_power_intent_reference(raw, scope, dict_name, power_intent)
    return ResolvedReference(
        raw=raw,
        location=ReferenceLocation(construct_kind, construct_id, field_name),
        kind=kind,
        resolved_power_intent_id=pid,
        detail=detail,
    )


def map_design(power_intent: PowerIntentDesign, rtl: Design, virtual_names: frozenset = frozenset()) -> MappedDesign:
    fmt = power_intent.source_format
    refs: list[ResolvedReference] = []

    def rtl_list(values, scope, kind, id_, field_name):
        refs.extend(_rtl_list_refs(values, scope, fmt, rtl, virtual_names, kind, id_, field_name))

    def pi(raw, scope, dict_name, kind, id_, field_name):
        r = _pi_ref(raw, scope, dict_name, power_intent, kind, id_, field_name)
        if r is not None:
            refs.append(r)

    def not_attempted(raw, kind, id_, field_name):
        r = _not_attempted(raw, kind, id_, field_name)
        if r is not None:
            refs.append(r)

    for d in power_intent.domains.values():
        rtl_list(d.elements, d.scope, "PowerDomain", d.id, "elements")
        rtl_list(d.exclude_elements, d.scope, "PowerDomain", d.id, "exclude_elements")
        rtl_list(d.boundary_ports, d.scope, "PowerDomain", d.id, "boundary_ports")
        not_attempted(d.primary_supply, "PowerDomain", d.id, "primary_supply")

    for s in power_intent.switches.values():
        rtl_list(s.control_ports, s.scope, "PowerSwitch", s.id, "control_ports")
        pi(s.domain, s.scope, "domains", "PowerSwitch", s.id, "domain")
        # input_supply/output_supply: confirmed intra-power-intent this
        # round (both UPF and CPF), but the target may be either a
        # SupplyPort or a SupplyNet depending on how the switch's source
        # field was declared -- try supply_ports first, fall back to
        # supply_nets, rather than picking one dict and missing the other
        # real case.
        for field_name, raw in (("input_supply", s.input_supply), ("output_supply", s.output_supply)):
            if raw is None:
                continue
            r = _pi_ref(raw, s.scope, "supply_ports", power_intent, "PowerSwitch", s.id, field_name)
            if r.kind == ResolutionKind.DANGLING:
                fallback = _pi_ref(raw, s.scope, "supply_nets", power_intent, "PowerSwitch", s.id, field_name)
                if fallback.kind == ResolutionKind.POWER_INTENT:
                    r = fallback
            refs.append(r)

    for iso in power_intent.isolation_strategies.values():
        rtl_list(iso.elements, iso.scope, "IsolationStrategy", iso.id, "elements")
        rtl_list(iso.exclude_elements, iso.scope, "IsolationStrategy", iso.id, "exclude_elements")
        pi(iso.domain, iso.scope, "domains", "IsolationStrategy", iso.id, "domain")
        pi(iso.source, iso.scope, "domains", "IsolationStrategy", iso.id, "source")
        pi(iso.sink, iso.scope, "domains", "IsolationStrategy", iso.id, "sink")
        not_attempted(iso.isolation_supply, "IsolationStrategy", iso.id, "isolation_supply")

    for ls in power_intent.level_shifter_strategies.values():
        rtl_list(ls.elements, ls.scope, "LevelShifterStrategy", ls.id, "elements")
        rtl_list(ls.exclude_elements, ls.scope, "LevelShifterStrategy", ls.id, "exclude_elements")
        pi(ls.domain, ls.scope, "domains", "LevelShifterStrategy", ls.id, "domain")
        pi(ls.source, ls.scope, "domains", "LevelShifterStrategy", ls.id, "source")
        pi(ls.sink, ls.scope, "domains", "LevelShifterStrategy", ls.id, "sink")

    for ret in power_intent.retention_strategies.values():
        rtl_list(ret.elements, ret.scope, "RetentionStrategy", ret.id, "elements")
        pi(ret.domain, ret.scope, "domains", "RetentionStrategy", ret.id, "domain")
        not_attempted(ret.retention_supply, "RetentionStrategy", ret.id, "retention_supply")
        # save_signal/restore_signal: see _OUT_OF_SCOPE.

    for ps in power_intent.power_states.values():
        pi(ps.domain, ps.scope, "domains", "PowerState", ps.id, "domain")
        not_attempted(ps.supply, "PowerState", ps.id, "supply")
        not_attempted(ps.applies_to, "PowerState", ps.id, "applies_to")
        # expr: see _OUT_OF_SCOPE.

    for grp in power_intent.power_state_groups.values():
        refs.extend(_not_attempted_list(grp.members, "PowerStateGroup", grp.id, "members"))

    for i, conn in enumerate(power_intent.domain_supply_connections):
        pi(conn.domain, conn.scope, "domains", "DomainSupplyConnection", f"[{i}]", "domain")
        pi(conn.supply_net, conn.scope, "supply_nets", "DomainSupplyConnection", f"[{i}]", "supply_net")

    return MappedDesign(power_intent=power_intent, rtl=rtl, references=refs)
