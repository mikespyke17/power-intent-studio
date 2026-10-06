"""Intra-power-intent reference resolution (Milestone 3).

Simple by-name lookup against the power-intent design's own declared
constructs (PowerDomain, SupplyPort, SupplyNet, SupplySet, PowerState,
etc.) -- no RTL involved. These values are plain names (e.g. "PD_CORE"),
not hierarchical paths.

Resolution is symmetric with construction: every candidate construct's own
`id` was built via `make_power_intent_id(scope, name)` at elaboration time
(see backend/elaborator/power_intent/ir/schema.py's module docstring), so
looking a reference up is "rebuild the same id from the referencing
construct's own scope and the raw name, then check membership" -- not a
separate parallel scheme.
"""

from __future__ import annotations

from backend.elaborator.power_intent.ir import PowerIntentDesign, make_power_intent_id
from backend.mapping.schema import ResolutionKind


def resolve_power_intent_reference(name: str, scope: str, dict_name: str, power_intent: PowerIntentDesign) -> tuple:
    """Returns (kind, resolved_power_intent_id, detail).

    `dict_name` is the attribute name on PowerIntentDesign to look up
    against (e.g. "domains", "supply_nets") -- the caller (map_design.py)
    decides which dict(s) a given field can point into.
    """
    id_ = make_power_intent_id(scope, name)
    target: dict = getattr(power_intent, dict_name)
    if id_ in target:
        return (ResolutionKind.POWER_INTENT, id_, None)
    return (ResolutionKind.DANGLING, None, f"'{name}': no {dict_name} construct declared at scope '{scope}'")
