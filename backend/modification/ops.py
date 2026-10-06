"""Milestone 3b: in-memory PowerIntentDesign modification.

PowerIntentDesign in, a new PowerIntentDesign out -- nothing written to
disk (export is its own future, milestone-adjacent piece, not this
module's job -- see docs/product_workflow.md's Export section). Every
operation rebuilds via dataclasses.replace() rather than mutating in
place (approved "Option A" over mutate-in-place -- mutation would let a
MappedDesign's already-computed `references` silently go stale while
`.power_intent` kept looking current, exactly the "looks fine, is
actually wrong" bug class this project has repeatedly eliminated
elsewhere). Callers are responsible for re-running map_design()/
validate() after an edit to get current results -- this module never
calls either itself, and doesn't depend on the RTL Design at all.

Generic dispatch over `kind` (a construct-kind string, using the exact
same values ReferenceLocation.construct_kind already uses -- "PowerDomain",
"IsolationStrategy", etc.) instead of ~40 near-identical per-type
functions, per the approved "one uniform operation, format-agnostic"
decision. DomainSupplyConnection is the one exception -- no name/id, so
it doesn't fit the dict-keyed shape and gets its own add/remove functions
below.

See docs/milestone3b_modification.md for the full design writeup,
including the inherited NOT_ATTEMPTED cascade/delete-blocking gap (its
own explicit section there, not a footnote).
"""

from __future__ import annotations

from dataclasses import replace as dc_replace
from typing import Optional

from backend.elaborator.power_intent.ir import DomainSupplyConnection, PowerIntentDesign, SourceFormat, make_power_intent_id
from backend.elaborator.power_intent.upf.version_gates import check_location_value
from backend.mapping import MappedDesign, ResolutionKind
from backend.modification.errors import (
    ConstructAlreadyExists,
    ConstructNotFound,
    DeleteBlocked,
    IdentityFieldInUpdate,
    ScopeChangeBlocked,
)

_DICT_ATTR = {
    "PowerDomain": "domains",
    "SupplyPort": "supply_ports",
    "SupplyNet": "supply_nets",
    "SupplySet": "supply_sets",
    "PowerSwitch": "switches",
    "IsolationStrategy": "isolation_strategies",
    "RetentionStrategy": "retention_strategies",
    "LevelShifterStrategy": "level_shifter_strategies",
    "PowerState": "power_states",
    "PowerStateGroup": "power_state_groups",
}

# Fields whose meaning is scope-relative (rtl_resolver._combine() treats
# scope + this field as one inseparable unit) -- mirrors mapping/map_design.py's
# own rtl_list() calls exactly. Deliberately duplicated rather than
# imported: map_design.py's calls are simple/static and importing would
# mean reaching into that module's internals for a small, stable list --
# kept in sync by hand, flagged here so a future map_design.py field
# addition doesn't silently drift out of sync with this table.
_RELATIVE_FIELDS = {
    "PowerDomain": ("elements", "exclude_elements", "boundary_ports"),
    "PowerSwitch": ("control_ports",),
    "IsolationStrategy": ("elements", "exclude_elements"),
    "LevelShifterStrategy": ("elements", "exclude_elements"),
    "RetentionStrategy": ("elements",),
    "SupplyPort": (),
    "SupplyNet": (),
    "SupplySet": (),
    "PowerState": (),
    "PowerStateGroup": (),
}

_LOCATION_GATED_KINDS = ("IsolationStrategy", "LevelShifterStrategy")


def _dict_attr(kind: str) -> str:
    if kind not in _DICT_ATTR:
        raise ValueError(f"'{kind}' is not a dict-keyed construct kind (DomainSupplyConnection has its own functions)")
    return _DICT_ATTR[kind]


def _identity_fields(kind: str, source_format: SourceFormat) -> tuple[str, ...]:
    """Which fields determine this construct's `id`. Format-dependent for
    PowerState only: UPF's add_power_state/legacy-PST ids are
    make_power_intent_id(scope, f"{applies_to}.{state_name}") (confirmed
    at upf/verbs.py's _add_power_state and upf/legacy_pst.py's
    _add_pst_state) -- both name and applies_to are identity-determining.
    CPF's create_power_mode ids are plain make_power_intent_id(scope,
    name) (applies_to is always None for CPF power modes -- no single-
    object referent, see PowerState.applies_to's own docstring). Every
    other construct kind is uniformly name-only across both formats.
    """
    if kind == "PowerState" and source_format == SourceFormat.UPF:
        return ("name", "applies_to")
    return ("name",)


def _name_part(kind: str, construct, source_format: SourceFormat) -> str:
    if _identity_fields(kind, source_format) == ("name", "applies_to"):
        return f"{construct.applies_to}.{construct.name}"
    return construct.name


def _check_version_gate(power_intent: PowerIntentDesign, kind: str, location_value: Optional[str], op_label: str) -> None:
    if power_intent.source_format == SourceFormat.UPF and kind in _LOCATION_GATED_KINDS:
        check_location_value(power_intent.source_version, location_value, op_label)


def _referencing(mapped: MappedDesign, id_: str):
    return [r for r in mapped.references if r.kind == ResolutionKind.POWER_INTENT and r.resolved_power_intent_id == id_]


def add(power_intent: PowerIntentDesign, kind: str, construct) -> PowerIntentDesign:
    attr = _dict_attr(kind)
    expected_id = make_power_intent_id(construct.scope, _name_part(kind, construct, power_intent.source_format))
    if construct.id != expected_id:
        raise ValueError(
            f"{kind} construct's id '{construct.id}' doesn't match its own scope/name "
            f"(expected '{expected_id}') -- construct it with matching fields"
        )
    current = getattr(power_intent, attr)
    if construct.id in current:
        raise ConstructAlreadyExists(kind, construct.id)
    _check_version_gate(power_intent, kind, getattr(construct, "location", None), f"modification.add({kind})")
    new_dict = dict(current)
    new_dict[construct.id] = construct
    return dc_replace(power_intent, **{attr: new_dict})


def update(power_intent: PowerIntentDesign, kind: str, id_: str, changes: dict) -> PowerIntentDesign:
    attr = _dict_attr(kind)
    current_dict = getattr(power_intent, attr)
    if id_ not in current_dict:
        raise ConstructNotFound(kind, id_)
    construct = current_dict[id_]

    blocked = (set(_identity_fields(kind, power_intent.source_format)) | {"scope"}) & set(changes)
    if blocked:
        raise IdentityFieldInUpdate(kind, sorted(blocked)[0])

    location_value = changes.get("location", getattr(construct, "location", None))
    _check_version_gate(power_intent, kind, location_value, f"modification.update({kind})")

    new_construct = dc_replace(construct, **changes)
    new_dict = dict(current_dict)
    new_dict[id_] = new_construct
    return dc_replace(power_intent, **{attr: new_dict})


def rename(power_intent: PowerIntentDesign, kind: str, id_: str, identity_changes: dict, mapped: MappedDesign) -> PowerIntentDesign:
    attr = _dict_attr(kind)
    current_dict = getattr(power_intent, attr)
    if id_ not in current_dict:
        raise ConstructNotFound(kind, id_)
    construct = current_dict[id_]

    id_fields = set(_identity_fields(kind, power_intent.source_format))
    unknown = set(identity_changes) - id_fields
    if unknown:
        raise ValueError(f"rename({kind}): {sorted(unknown)} are not identity fields (identity fields: {sorted(id_fields)})")

    old_name = construct.name
    new_construct = dc_replace(construct, **identity_changes)
    new_id = make_power_intent_id(new_construct.scope, _name_part(kind, new_construct, power_intent.source_format))

    if new_id != id_ and new_id in current_dict:
        raise ConstructAlreadyExists(kind, new_id)

    new_construct = dc_replace(new_construct, id=new_id)
    new_dict = dict(current_dict)
    del new_dict[id_]
    new_dict[new_id] = new_construct
    power_intent = dc_replace(power_intent, **{attr: new_dict})

    if "name" in identity_changes and new_construct.name != old_name:
        power_intent = _cascade_rename(power_intent, id_, old_name, new_construct.name, mapped)

    return power_intent


def _cascade_rename(power_intent: PowerIntentDesign, old_id: str, old_name: str, new_name: str, mapped: MappedDesign) -> PowerIntentDesign:
    """Rewrites every raw-string reference to `old_name` at every location
    mapped.references recorded as resolving to `old_id`. `ref.raw ==
    old_name` is guaranteed by construction for every such reference, not
    just usually true: power_intent_resolver.py only produces a
    POWER_INTENT match when make_power_intent_id(referencing_scope,
    ref.raw) exactly equals the target's own id
    (make_power_intent_id(target.scope, target.name)) -- which forces
    ref.raw == target.name whenever scopes match, which they must for the
    id strings to be equal at all. Verified directly in
    test_modification.py, not just asserted here.
    """
    for ref in _referencing(mapped, old_id):
        loc = ref.location
        if loc.construct_kind == "DomainSupplyConnection":
            index = int(loc.construct_id[1:-1])  # "[3]" -> 3, map_design.py's own convention for this list-not-dict construct
            connections = list(power_intent.domain_supply_connections)
            owner = connections[index]
            assert getattr(owner, loc.field) == old_name
            connections[index] = dc_replace(owner, **{loc.field: new_name})
            power_intent = dc_replace(power_intent, domain_supply_connections=connections)
            continue

        target_attr = _dict_attr(loc.construct_kind)
        target_dict = dict(getattr(power_intent, target_attr))
        owner = target_dict[loc.construct_id]
        if loc.index is None:
            assert getattr(owner, loc.field) == old_name
            new_owner = dc_replace(owner, **{loc.field: new_name})
        else:
            values = list(getattr(owner, loc.field))
            assert values[loc.index] == old_name
            values[loc.index] = new_name
            new_owner = dc_replace(owner, **{loc.field: values})
        target_dict[loc.construct_id] = new_owner
        power_intent = dc_replace(power_intent, **{target_attr: target_dict})

    return power_intent


def delete(power_intent: PowerIntentDesign, kind: str, id_: str, mapped: MappedDesign) -> PowerIntentDesign:
    attr = _dict_attr(kind)
    current_dict = getattr(power_intent, attr)
    if id_ not in current_dict:
        raise ConstructNotFound(kind, id_)

    blocking = _referencing(mapped, id_)
    if blocking:
        raise DeleteBlocked(kind, id_, blocking)

    new_dict = dict(current_dict)
    del new_dict[id_]
    return dc_replace(power_intent, **{attr: new_dict})


def change_scope(
    power_intent: PowerIntentDesign,
    kind: str,
    id_: str,
    new_scope: str,
    confirmed_relative_fields: dict,
    mapped: MappedDesign,
) -> PowerIntentDesign:
    """Blocks for two independent reasons (see ScopeChangeBlocked): the
    construct's own relative fields weren't fully re-confirmed, or some
    other construct still references this one by name (a scope change
    would silently break that reference -- power_intent_resolver.py
    requires the referencing construct's own scope to exactly match the
    target's scope for its lookup to hit, so moving the target's scope
    invalidates every existing reference to it regardless of what its own
    relative fields say). Both conditions are checked and reported
    together, not short-circuited on the first one found.
    """
    attr = _dict_attr(kind)
    current_dict = getattr(power_intent, attr)
    if id_ not in current_dict:
        raise ConstructNotFound(kind, id_)
    construct = current_dict[id_]

    expected_fields = set(_RELATIVE_FIELDS.get(kind, ()))
    given_fields = set(confirmed_relative_fields)
    missing = sorted(expected_fields - given_fields)
    extra = sorted(given_fields - expected_fields)
    blocking = _referencing(mapped, id_)

    if missing or extra or blocking:
        raise ScopeChangeBlocked(kind, id_, missing_fields=missing, extra_fields=extra, blocking_references=blocking)

    new_construct = dc_replace(construct, scope=new_scope, **confirmed_relative_fields)
    new_id = make_power_intent_id(new_scope, _name_part(kind, new_construct, power_intent.source_format))
    if new_id != id_ and new_id in current_dict:
        raise ConstructAlreadyExists(kind, new_id)
    new_construct = dc_replace(new_construct, id=new_id)

    new_dict = dict(current_dict)
    del new_dict[id_]
    new_dict[new_id] = new_construct
    return dc_replace(power_intent, **{attr: new_dict})


def add_domain_supply_connection(power_intent: PowerIntentDesign, connection: DomainSupplyConnection) -> PowerIntentDesign:
    return dc_replace(power_intent, domain_supply_connections=[*power_intent.domain_supply_connections, connection])


def remove_domain_supply_connections(
    power_intent: PowerIntentDesign, domain: str, supply_net: str, role: Optional[str], scope: str
) -> PowerIntentDesign:
    """Removes every connection matching the full (domain, supply_net,
    role, scope) tuple -- there's no id/name to target one exactly, and
    nothing today prevents duplicate appends, so removing every exact
    match is the simplest correct semantics rather than requiring callers
    to track list indices."""
    remaining = [
        c
        for c in power_intent.domain_supply_connections
        if not (c.domain == domain and c.supply_net == supply_net and c.role == role and c.scope == scope)
    ]
    return dc_replace(power_intent, domain_supply_connections=remaining)
