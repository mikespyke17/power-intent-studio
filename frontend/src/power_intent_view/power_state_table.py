"""Power State Table -- diagram-only/mock-first, same scoping decision
as every other Milestone 3 feature (see supply_port_overlay.py's own
module docstring): nothing here is reflected into a real PowerIntentDesign.

A ROW is one Power-Domain-scoped state definition: which Power Domain
it applies to, a free-text Name, and one selection per currently-defined
Supply Net (a plain Supply Port, Internal Supply, OR Virtual Supply --
see ordered_supply_nets() below), each selection being one of THAT
net's own already-established Power States (SupplyPortPlacement.states
for a Supply Port/Virtual Supply -- both AUTHORED directly -- or an
Internal Supply's live-resolved effective list -- the exact SAME
per-net "States" list already shown elsewhere, e.g.
properties_panel.py's own "Supply Port" tab).

Deliberately NOT integrated into `_PowerIntentGraphicsView`'s own
Ctrl+Z/Ctrl+Y undo stack (explicit scope decision -- the spec never
asked for undo here, and every other diagram-only construct's undo
integration lives entirely inside that one already-enormous class;
this stays a self-contained sibling instead, observing the SAME shared
SupplyPortDiagramState/PowerDomainDiagramState/PowerSwitchDiagramState
via PowerIntentDiagramView.diagramStateChanged rather than mutating
them). Row/cell edits themselves are not currently undoable -- a
documented, deliberate scope cut, not an oversight.

Live-update cascade (explicit request) is implemented via SNAPSHOT
DIFFING (see power_state_table_tab.py's own `_on_diagram_state_changed()`),
not by hooking into `power_intent_diagram_view.py`'s own mutation
methods directly -- comparing each net's own previously-known effective
state-NAME list against its current one on every `diagramStateChanged`
tick lets a single mechanism correctly distinguish a RENAME (same list
length, exactly one differing name at the same position -- treated as
"this specific name became that one") from a genuine REMOVAL (a name
present before that has no such positional replacement), and does so
uniformly for both a plain Supply Port's own directly-edited `.states`
AND an Internal Supply's fully-DERIVED effective list (a state renamed
on the real upstream source correctly cascades through to every
Internal Supply still tracing back to it, for free, since the snapshot
is of the RESOLVED list, not the raw stored one)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from frontend.src.power_intent_view.power_domain_overlay import PowerDomainDiagramState
from frontend.src.power_intent_view.power_switch_overlay import PowerSwitchDiagramState, resolve_effective_states
from frontend.src.power_intent_view.supply_port_overlay import (
    LEVELLED_STATE_TYPES,
    SupplyPortDiagramState,
    SupplyPortPlacement,
    state_key,
)
from frontend.src.shared.models.diagnostics import Diagnostic
from frontend.src.shared.models.validation import Severity

DIAGNOSTICS_SOURCE = "power_state_table"


@dataclass
class PowerStateTableRow:
    """`net_states` keys by the net's own SupplyPortPlacement.id (stable
    across a rename of the net itself -- see module docstring), values
    are the SELECTED per-net Power State's own NAME (states have no id
    of their own to reference more stably than that) or None when
    unselected/cleared."""

    id: str
    power_domain_id: Optional[str] = None
    state_name: str = ""
    net_states: dict[str, Optional[str]] = field(default_factory=dict)


@dataclass
class PowerStateTableDiagramState:
    rows: dict[str, PowerStateTableRow] = field(default_factory=dict)


def _deduplicated_supply_nets(supply_state: SupplyPortDiagramState) -> list[SupplyPortPlacement]:
    """One representative SupplyPortPlacement per distinct NET NAME, not
    one per placement id (bug fix -- CHANGE 2's ancestor Supply Set
    sharing mechanism, `_auto_link_cross_hierarchy_supply_sets()`,
    auto-instantiates a genuinely SEPARATE SupplyPortPlacement -- its
    own id -- for the SAME physical net on every Power Domain that
    shares it, connected back to the original via a real, ordinary
    power net under the identical name; that's the established "same
    name = same net" identity convention this whole module already
    relies on elsewhere, e.g. known_supply_names_globally()/cross-
    hierarchy linking). Without this, a shared net previously showed up
    as one duplicate column per referencing domain.

    Keeps first-seen ORDER (dict iteration = insertion order) stable by
    name, but prefers a genuine, non-Internal-Supply Supply Port as the
    representative when the name-group has one (the true, root-level
    source -- its own resolve_effective_states() is just its plain
    `.states`, never a derived trace), falling back to whichever
    placement was seen first otherwise. Every caller that needs "the
    net list" (ordered_supply_nets() below, net_state_snapshot()) goes
    through this ONE shared dedup so a row's `net_states` key (a
    representative's own `.id`) and a live snapshot's own key always
    agree on which placement id represents a given name."""
    by_name: dict[str, SupplyPortPlacement] = {}
    for placement in supply_state.supply_port_placements.values():
        existing = by_name.get(placement.name)
        if existing is None or (existing.is_internal_supply and not placement.is_internal_supply):
            by_name[placement.name] = placement
    return list(by_name.values())


def ordered_supply_nets(supply_state: SupplyPortDiagramState, domain_state: PowerDomainDiagramState) -> list[SupplyPortPlacement]:
    """Every DISTINCT Supply Net (a plain Supply Port, Internal Supply,
    OR Virtual Supply -- NOT a macro PG pin, which is fixed/derived from
    the .lib itself, not a user-authored net this table tracks states
    against; and never more than once per NAME -- see
    _deduplicated_supply_nets() above), in THREE groups, in this exact
    order: Power-role nets, then VIRTUAL SUPPLIES, then Ground-role nets
    (each group in creation order) -- explicit request, spec section 7:
    "Virtual Supply columns sit BETWEEN Power-type columns and Ground-
    type columns." Power/Ground classification is via whichever Supply
    Set(s) actually reference each name as `.power`/`.ground` (the same
    classification power_intent_text_export.py's own CPF generator
    already establishes for the identical "power vs ground" question) --
    a Virtual Supply is never referenced by any Supply Set at all (see
    supply_port_overlay.py's own known_supply_names*() exclusions), so
    it's carved out into its own middle group by `.is_virtual_supply`
    BEFORE that Power/Ground classification runs, rather than relying on
    it to fall out of the ground_names/power_names sets on its own. A
    non-Virtual-Supply name never referenced by any Supply Set's
    `.ground` field defaults into the Power group -- there's nothing to
    positively classify it as Ground, and Power is the more common/
    default role."""
    ground_names = {s.ground for s in domain_state.supply_sets.values() if s.ground}
    power_names = {s.power for s in domain_state.supply_sets.values() if s.power}
    nets = _deduplicated_supply_nets(supply_state)
    virtual_group = [n for n in nets if n.is_virtual_supply]
    non_virtual = [n for n in nets if not n.is_virtual_supply]
    power_group = [n for n in non_virtual if not (n.name in ground_names and n.name not in power_names)]
    ground_group = [n for n in non_virtual if n.name in ground_names and n.name not in power_names]
    return power_group + virtual_group + ground_group


def net_effective_state_keys(net: SupplyPortPlacement, supply_state: SupplyPortDiagramState, power_switch_state: Optional[PowerSwitchDiagramState]) -> list[str]:
    """The dropdown VALUES for one net's own column -- a plain Supply
    Port's or Virtual Supply's own authored `.states`, or an Internal
    Supply's live-resolved effective list (resolve_effective_states(),
    the exact same function/semantics properties_panel.py's own "Supply
    Port" tab already uses for its "States" column).

    Keys, not display text. A rail state has no name of its own anymore,
    and its DISPLAY (see `state_display()`) is the bare number for a
    levelled state -- so FULL_ON 1.2 and PARTIAL_ON 1.2 both read "1.2"
    while being legitimately different states. `state_key()` is the
    canonical identity that distinguishes them, and it is what a row
    stores; `net_state_display()` turns one back into what a cell
    shows."""
    switches = power_switch_state.switches if power_switch_state is not None else {}
    return [state_key(s) for s in resolve_effective_states(net, supply_state, switches)]


def net_state_display(key: Optional[str]) -> str:
    """A stored `net_states` value -> what its cell/dropdown shows
    (explicit request, section 4): the NUMBER alone for a levelled state,
    "OFF" for an off one, the literal "UNDETERMINED" otherwise.

    Parsed back from the canonical key rather than carried alongside it,
    so the stored value stays the single source of truth."""
    if not key:
        return ""
    head, _, level = key.partition(" ")
    return level if head in LEVELLED_STATE_TYPES and level else head


def net_state_snapshot(supply_state: SupplyPortDiagramState, power_switch_state: Optional[PowerSwitchDiagramState]) -> dict[str, list[str]]:
    """net id -> its own current effective state-KEY list, for the
    rename/removal cascade's own before/after diff -- see module
    docstring. Built from the SAME deduplicated-by-name representative
    set `ordered_supply_nets()` uses (bug fix -- see
    `_deduplicated_supply_nets()`'s own docstring), so a snapshot key
    always agrees with whichever placement id a row's own `net_states`
    dict is actually keyed by; a non-representative duplicate (a shared
    net's OTHER per-domain copy) never enters the snapshot at all, since
    no row ever references its id in the first place."""
    switches = power_switch_state.switches if power_switch_state is not None else {}
    return {net.id: [state_key(s) for s in resolve_effective_states(net, supply_state, switches)] for net in _deduplicated_supply_nets(supply_state)}


def add_row(state: PowerStateTableDiagramState, row_id: str) -> PowerStateTableRow:
    """`row_id` is supplied by the caller (uuid4().hex in production,
    a fixed value in tests) -- keeps this function itself
    deterministic/directly testable, same "no hidden randomness in the
    core" precedent every other diagram-only `add_*()` here follows at
    its own call site instead."""
    row = PowerStateTableRow(id=row_id)
    state.rows[row.id] = row
    return row


def remove_row(state: PowerStateTableDiagramState, row_id: str) -> None:
    state.rows.pop(row_id, None)


def remove_rows_for_domain(state: PowerStateTableDiagramState, domain_id: str) -> list[str]:
    """Cascade (explicit, confirmed decision): a row whose own Power
    Domain is deleted entirely is itself deleted, not left with an
    emptied cell -- a state definition with no domain to scope its own
    Name-uniqueness against has nothing left to validate. Returns the
    removed row ids, purely informational for a caller that wants to
    log/assert what happened."""
    removed = [r.id for r in state.rows.values() if r.power_domain_id == domain_id]
    for row_id in removed:
        state.rows.pop(row_id, None)
    return removed


def rename_net_state_references(state: PowerStateTableDiagramState, net_id: str, old_name: str, new_name: str) -> None:
    for row in state.rows.values():
        if row.net_states.get(net_id) == old_name:
            row.net_states[net_id] = new_name


def clear_net_state_references(state: PowerStateTableDiagramState, net_id: str, state_name: str) -> None:
    for row in state.rows.values():
        if row.net_states.get(net_id) == state_name:
            row.net_states[net_id] = None


def reconcile_net_state_renames_and_removals(
    state: PowerStateTableDiagramState, previous_snapshot: dict[str, list[str]], current_snapshot: dict[str, list[str]]
) -> None:
    """The core of the rename/removal cascade (see module docstring):
    for each net that existed in the PREVIOUS snapshot, compares its
    own state-name list then vs. now. Equal-length lists differing at
    exactly the same position(s) are treated as a rename per differing
    position (`old_names[i]` became `new_names[i]`); any old name with
    no such positional match (including every name on a net that's
    shrunk, grown, or disappeared entirely) is treated as removed --
    every row's own selection of it is cleared, never left dangling on
    a name that no longer exists."""
    for net_id, old_names in previous_snapshot.items():
        new_names = current_snapshot.get(net_id, [])
        if old_names == new_names:
            continue
        renamed_away: set[str] = set()
        if len(old_names) == len(new_names):
            for old_name, new_name in zip(old_names, new_names):
                if old_name != new_name:
                    rename_net_state_references(state, net_id, old_name, new_name)
                    renamed_away.add(old_name)
        removed_names = (set(old_names) - set(new_names)) - renamed_away
        for name in removed_names:
            clear_net_state_references(state, net_id, name)


def validate_power_state_table(
    state: PowerStateTableDiagramState,
    domain_state: PowerDomainDiagramState,
    supply_state: SupplyPortDiagramState,
) -> list[Diagnostic]:
    """"Check Power Intent" (explicit request) -- resets/replaces
    entirely on each call (the caller's own DiagnosticsDock.set_diagnostics()
    already does this per-source, see power_state_table_tab.py), never
    accumulates across runs. Net columns are recomputed fresh here (not
    read from any row's own stale state) so a row's empty-cell check
    always reflects the CURRENT net list, not whatever existed when
    that row was created.

    (b) duplicate State Name, (d) duplicate full net-selection
    configuration -- both scoped PER POWER DOMAIN (explicit, confirmed
    decision): two rows under different domains may legitimately share
    either. (c) any empty cell (Power Domain unselected, State Name
    blank, or any CURRENT net column unselected) -- checked
    independently of (b)/(d), a row can be flagged by more than one
    rule at once. A ZERO-ROW table (explicit request) is itself flagged
    -- an empty table is a real gap worth surfacing, not silently
    "nothing to check, no errors" -- and returned immediately, since
    every other rule below is vacuously satisfied by no rows anyway."""
    if not state.rows:
        return [
            Diagnostic(
                severity=Severity.ERROR,
                code="POWER-STATE-TABLE-EMPTY-TABLE",
                message="No Power States defined.",
                source=DIAGNOSTICS_SOURCE,
            )
        ]

    net_ids = [n.id for n in ordered_supply_nets(supply_state, domain_state)]
    diagnostics: list[Diagnostic] = []

    for row in state.rows.values():
        missing = []
        if not row.power_domain_id:
            missing.append("Power Domain")
        if not row.state_name.strip():
            missing.append("State Name")
        missing += [n.name for n in ordered_supply_nets(supply_state, domain_state) if not row.net_states.get(n.id)]
        if missing:
            diagnostics.append(
                Diagnostic(
                    severity=Severity.ERROR,
                    code="POWER-STATE-TABLE-EMPTY-CELL",
                    message=f"Row is missing a value for: {', '.join(missing)}.",
                    source=DIAGNOSTICS_SOURCE,
                )
            )

    rows_by_domain: dict[str, list[PowerStateTableRow]] = {}
    for row in state.rows.values():
        if row.power_domain_id:
            rows_by_domain.setdefault(row.power_domain_id, []).append(row)

    for domain_id, rows in rows_by_domain.items():
        domain = domain_state.domains.get(domain_id)
        domain_name = domain.name if domain is not None else domain_id

        seen_names: dict[str, PowerStateTableRow] = {}
        for row in rows:
            name = row.state_name.strip()
            if not name:
                continue
            if name in seen_names:
                diagnostics.append(
                    Diagnostic(
                        severity=Severity.ERROR,
                        code="POWER-STATE-TABLE-DUPLICATE-NAME",
                        message=f"Power Domain '{domain_name}': State Name '{name}' is used by more than one row.",
                        source=DIAGNOSTICS_SOURCE,
                    )
                )
            else:
                seen_names[name] = row

        seen_configs: dict[tuple, PowerStateTableRow] = {}
        for row in rows:
            config = tuple(row.net_states.get(nid) for nid in net_ids)
            if any(v is None for v in config):
                continue  # already flagged by the empty-cell check -- not also a meaningful duplicate
            if config in seen_configs:
                diagnostics.append(
                    Diagnostic(
                        severity=Severity.ERROR,
                        code="POWER-STATE-TABLE-DUPLICATE-CONFIGURATION",
                        message=(
                            f"Power Domain '{domain_name}': rows '{seen_configs[config].state_name}' and '{row.state_name}' "
                            "select the identical Power State on every net."
                        ),
                        source=DIAGNOSTICS_SOURCE,
                    )
                )
            else:
                seen_configs[config] = row

    return diagnostics
