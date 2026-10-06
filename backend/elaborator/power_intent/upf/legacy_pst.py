"""Legacy UPF Power State Table (PST) commands: create_pst/add_pst_state.

Isolated from verbs.py specifically because this is the version-flavored
(2.1-and-earlier, per the version research) mechanism `add_power_state`
superseded -- kept separate so it doesn't read as equally "current" next
to the native mechanism.

Version-gating investigated, resolved with a caveat (no IEEE 1801 primary
text access -- it's paywalled, unlike CPF's freely available spec):
multiple independent secondary sources converge on PST being marked
"legacy"/deprecated starting UPF 2.1 but never removed -- kept for backward
compatibility, including a mention that IEEE 1801's own Annex D catalogs
deprecated-but-still-valid commands. No source found suggesting outright
removal in 3.0/3.1. Conclusion: available under every supported version
with `PowerStateShape.PST_ROW` tagging is very likely already correct --
not gated here. If stronger (primary-source) confidence becomes available,
revisit.
"""

from __future__ import annotations

from backend.elaborator.power_intent.ir import PowerStateShape, make_power_intent_id
from backend.elaborator.power_intent.shared.builders import _ElaborationState, _PowerStateBuilder
from backend.elaborator.power_intent.shared.tcl_runtime import _one


def _create_pst(state: _ElaborationState, parsed: dict[str, list]) -> None:
    name = _one(parsed, "name")
    # Generic scratch bag, not a named field on the shared _ElaborationState
    # -- this bookkeeping (create_pst name -> its declared supply list, so
    # a later add_pst_state call can look it up) is specific to UPF's
    # legacy PST mechanism, which CPF has no equivalent of.
    state.scratch.setdefault("pst_supplies", {})[name] = _one(parsed, "supplies", default=[])


def _add_pst_state(state: _ElaborationState, parsed: dict[str, list]) -> None:
    """Legacy UPF 2.1 PST row -> canonical PowerState translation (see the
    doc's design question 2). Each row becomes one PowerState whose
    expression is the conjunction of "supply == value" terms for that
    row's columns -- mechanical and lossless, since a table is strictly
    less expressive than the canonical add_power_state model.
    """
    state_name = _one(parsed, "name")
    # `-pst` and `-pst_name` are the same flag under two spellings, and
    # BOTH occur in real files -- `-pst` is the 1801 LRM's own, which is
    # what a hand-written or migrated file uses. Both were already
    # REGISTERED as recognized options, but only `-pst_name` was ever
    # read, so `-pst` parsed cleanly and then failed with
    # "no create_pst named 'None' found" -- the "recognized but never
    # read by any handler" failure mode this project has hit before.
    # Found by hand-authoring a legacy-PST fixture; see
    # tests/frontend/golden_simple10/README.md, iteration 5.
    pst_name = _one(parsed, "pst_name") or _one(parsed, "pst")
    values = _one(parsed, "state", default=[])
    supplies = state.scratch.get("pst_supplies", {}).get(pst_name)
    if supplies is None:
        raise ValueError(f"add_pst_state: no create_pst named '{pst_name}' found")
    if len(supplies) != len(values):
        raise ValueError(
            f"add_pst_state '{state_name}': {len(values)} value(s) given for pst '{pst_name}', "
            f"which declares {len(supplies)} supply column(s)"
        )
    pairs = list(zip(supplies, values))
    expr = " && ".join(f"{supply} == {value}" for supply, value in pairs)
    id_ = make_power_intent_id(state.current_scope, f"{pst_name}.{state_name}")
    if id_ in state.power_states:
        raise ValueError(f"PST state '{state_name}' on '{pst_name}' already exists at scope '{state.current_scope}'")
    state.power_states[id_] = _PowerStateBuilder(
        id=id_,
        name=state_name,
        scope=state.current_scope,
        applies_to=pst_name,
        expr=expr,
        # The same information as `expr`, structured. A consumer wanting
        # the per-supply values back should read this rather than
        # re-parsing the string above -- see PowerState.supply_states.
        supply_states=dict(pairs),
        shape=PowerStateShape.PST_ROW,
        source_loc=state.loc(),
    )


def _add_port_state(state: _ElaborationState, parsed: dict[str, list]) -> None:
    """`add_port_state <port> -state {NAME value} ...` (and its net
    counterpart `add_supply_state`): the legacy per-supply state list a
    PST's rows refer to BY NAME. Each `-state` pair becomes one
    PORT_STATE PowerState on that supply -- `name` the state's own name
    (HV, RET, ...), `expr` its value: a voltage, or "off".

    Distinct from add_power_state's `{TYPE level}` per-net pair, where the
    first token is the state TYPE (FULL_ON, OFF): here it is a free name,
    which is exactly what add_pst_state's columns reference."""
    supply = _one(parsed, "object")
    for entry in parsed.get("state", []):
        if len(entry) != 2:
            raise ValueError(f"'{supply}': -state expects {{NAME value}}, got {entry!r}")
        state_name, value = entry
        id_ = make_power_intent_id(state.current_scope, f"{supply}.{state_name}")
        if id_ in state.power_states:
            raise ValueError(f"state '{state_name}' on '{supply}' already exists at scope '{state.current_scope}'")
        state.power_states[id_] = _PowerStateBuilder(
            id=id_,
            name=state_name,
            scope=state.current_scope,
            applies_to=supply,
            expr=value,
            shape=PowerStateShape.PORT_STATE,
            source_loc=state.loc(),
        )
