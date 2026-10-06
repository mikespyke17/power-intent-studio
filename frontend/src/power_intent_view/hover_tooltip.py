"""Hover-delay info tooltip for the Power Intent Diagram (explicit
request): pausing the cursor over a diagram element for 1 second shows
a small, custom-styled info box with a FIXED field list per element
type. The CRITICAL rule driving every `resolve_*_fields()` function
below: always return every listed field for that type, using "none"
(or, for a Supply Port/Internal Supply/Virtual Supply's Power States
field only, the existing "Not yet connected" message) as the VALUE
when a field has nothing to show -- never omit a field/line outright.
The field list itself never varies by instance; only the value does.

This module owns two things: the plain, directly-testable content-
assembly functions (`resolve_*_fields()`, each a pure function of
already-resolved diagram state -- no Qt event handling, no icon
objects, so they're testable with the exact same hand-built
placement/state fixtures every other diagram-only module here already
uses) and the tooltip WIDGET itself (`HoverInfoTooltip`). The hover-
delay TIMER/cancellation logic and the per-element-type hit-testing
that decides WHAT to resolve live in power_intent_diagram_view.py's own
`_PowerIntentGraphicsView` (`_update_hover_tooltip()`/
`_hover_tooltip_target_at()`/`_hover_tooltip_text_for()`) -- that class
already owns every other hover-highlight mechanism (Supply Port/
Internal Supply/Virtual Supply/Retention/Isolation/Level Shifter/Power
Switch placement-target hover) and every icon hit-test helper
(`_box_at()`/`_supply_port_icon_at()`/`_retention_badge_at()`/etc.)
this feature reuses rather than duplicating.

Multi-instance entities (Retention/Isolation/Level Shifter/Power
Switch spanning several boundary blocks in a multi-hierarchy Power
Domain): every field EXCEPT Power Switch's own Supply In/Supply Out
comes from the ONE shared placement record (RetentionStrategyPlacement/
IsolationStrategyPlacement/LevelShifterStrategyPlacement/
PowerSwitchPlacement), so hovering any one instance already shows
identical content for those fields, by construction -- no extra
"which instance" bookkeeping needed here at all. Power Switch's Supply
Out is ALSO always identical in practice (multi-hierarchy replication
matches targets by name -- see PowerSwitchPlacement's own module
docstring), but Supply In is a genuine PER-INSTANCE electrical
connection (a user can draw a different net, or none, onto each
hierarchy's own replica) -- confirmed with the user that this should
reflect the ACTUALLY-HOVERED instance's own real connection, not a
fixed canonical one, so `resolve_power_switch_fields()` takes
already-resolved `supply_in`/`supply_out` values as plain strings
rather than reaching into a dict of replicas itself.

Font: JetBrains Mono was requested but is NEITHER bundled in this
project (no .ttf/.otf resource anywhere, confirmed via `find`) NOR
installed as a system font on the dev machine (confirmed via a live
`QFontDatabase.families()` query returning no JetBrains-ish match) --
flagged back per the explicit "confirm first" instruction rather than
silently substituting. User-confirmed fallback: "Menlo, Consolas,
monospace" (Menlo confirmed present on this system via the same query;
Consolas/generic monospace cover Windows/Linux and any machine with
neither)."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QLabel, QWidget

from frontend.src.power_intent_view.isolation_overlay import IsolationStrategyPlacement
from frontend.src.power_intent_view.level_shifter_overlay import LevelShifterStrategyPlacement
from frontend.src.power_intent_view.power_domain_overlay import PowerDomainDiagramState, find_effective_domain_for_path
from frontend.src.power_intent_view.power_switch_overlay import PowerSwitchPlacement, resolve_effective_states
from frontend.src.power_intent_view.retention_overlay import RetentionStrategyPlacement
from frontend.src.power_intent_view.supply_port_overlay import state_label, SupplyPortDiagramState, SupplyPortPlacement
from frontend.src.shared.diagram.hierarchy_scene_builder import InstanceBoxItem

NONE_TEXT = "none"
NOT_YET_CONNECTED_TEXT = "Not yet connected"

_CLAMP_LABELS = {"low": "Low", "high": "High", "keep": "Keep"}
_RULE_LABELS = {"low_to_high": "Low to High", "high_to_low": "High to Low", "both": "Both"}


def _display_state(state) -> str:
    """One rail state for the tooltip.

    This used to be a deliberate 4-line duplicate of
    properties_panel.py's own `_format_supply_state()`, per this
    codebase's small-overlap-duplication precedent. Both now delegate to
    the shared `state_label()` instead: once states lost their authored
    name, the formatting rule became a property of the state VOCABULARY
    rather than of either display surface, so keeping two copies of it
    would mean two places to keep in step with that vocabulary."""
    return state_label(state)


def _domain_name(domain_state: PowerDomainDiagramState, domain_id: Optional[str]) -> Optional[str]:
    domain = domain_state.domains.get(domain_id) if domain_id else None
    return domain.name if domain is not None else None


def _supply_set_name(domain_state: PowerDomainDiagramState, supply_set_id: Optional[str]) -> Optional[str]:
    supply_set = domain_state.supply_sets.get(supply_set_id) if supply_set_id else None
    return supply_set.name if supply_set is not None else None


def render_tooltip_text(fields: list[tuple[str, str]]) -> str:
    """One "Label: Value" line per field, in the exact order given --
    shared by every element type below, so the CRITICAL "always show
    every field" rule only has to be honoured once, structurally, by
    each resolve_*_fields() always returning its own fixed-length list
    rather than by this renderer doing any filtering/omitting itself."""
    return "\n".join(f"{label}: {value}" for label, value in fields)


def resolve_hierarchy_block_fields(box: InstanceBoxItem, domain_state: PowerDomainDiagramState) -> list[tuple[str, str]]:
    """Instance name (+ RTL/Macro) / Power Domain / Supply Sets --
    exactly the spec's own field list, in order. "Power Domain" uses
    find_effective_domain_for_path() (cascading -- the domain that
    actually COLOURS this block, same as the diagram's own visual
    tinting), not find_domain_for_owner() (exact ownership only) -- a
    block nested inside a domain's own subtree without owning one
    itself still visually belongs to it, and the tooltip should agree
    with what's on screen. "Supply Sets" lists the domain's own Primary
    THEN every Secondary (in order), by name -- skipping any reference
    that doesn't resolve to a real SupplySetPlacement (a real, though
    rare, dangling-reference case) rather than showing a blank entry."""
    instance_name = box.path.rsplit("/", 1)[-1]
    kind = "Macro" if box.definition.is_black_box else "RTL"
    domain = find_effective_domain_for_path(domain_state.domains, box.path)
    if domain is None:
        domain_value = NONE_TEXT
        supply_sets_value = NONE_TEXT
    else:
        domain_value = domain.name
        names = []
        primary_name = _supply_set_name(domain_state, domain.primary_supply_set)
        if primary_name:
            names.append(primary_name)
        for supply_set_id in domain.secondary_supply_sets:
            name = _supply_set_name(domain_state, supply_set_id)
            if name:
                names.append(name)
        supply_sets_value = ", ".join(names) if names else NONE_TEXT
    return [
        ("Instance", f"{instance_name} ({kind})"),
        ("Power Domain", domain_value),
        ("Supply Sets", supply_sets_value),
    ]


def resolve_power_switch_fields(
    placement: PowerSwitchPlacement,
    domain_state: PowerDomainDiagramState,
    supply_in: Optional[str],
    supply_out: Optional[str],
) -> list[tuple[str, str]]:
    """Name / Power Domain / Supply In / Supply Out / Cell Type / ON
    State / OFF State -- exactly the spec's own field list, in order.
    `supply_in`/`supply_out` are already-resolved plain names (or
    None) -- see module docstring for why this function doesn't resolve
    them itself (the per-instance vs. shared-record split)."""
    return [
        ("Name", placement.name),
        ("Power Domain", _domain_name(domain_state, placement.power_domain_id) or NONE_TEXT),
        ("Supply In", supply_in or NONE_TEXT),
        ("Supply Out", supply_out or NONE_TEXT),
        ("Cell Type", placement.cell_type or NONE_TEXT),
        ("ON State", placement.on_state or NONE_TEXT),
        ("OFF State", placement.off_state or NONE_TEXT),
    ]


def resolve_isolation_fields(strategy: IsolationStrategyPlacement, domain_state: PowerDomainDiagramState) -> list[tuple[str, str]]:
    """Name / Power Domain / Isolation Supply Set / Clamp Value /
    Isolation Control -- exactly the spec's own field list, in order.
    Clamp Value is displayed via the SAME "low"/"high"/"keep" ->
    "Low"/"High"/"Keep" label mapping isolation_dialog.py's own Info
    dialog already uses (duplicated here, not imported -- see
    _display_state()'s own docstring for the same small-duplication
    precedent)."""
    return [
        ("Name", strategy.name),
        ("Power Domain", _domain_name(domain_state, strategy.power_domain_id) or NONE_TEXT),
        ("Isolation Supply Set", _supply_set_name(domain_state, strategy.isolation_supply_set_id) or NONE_TEXT),
        ("Clamp Value", _CLAMP_LABELS.get(strategy.clamp_value, strategy.clamp_value) or NONE_TEXT),
        ("Isolation Control", strategy.isolation_control or NONE_TEXT),
    ]


def resolve_level_shifter_fields(strategy: LevelShifterStrategyPlacement) -> list[tuple[str, str]]:
    """Name / Rule / Cell Type -- exactly the spec's own field list, in
    order. Deliberately NO Power Domain field (explicit request: "do
    not add it"), even though LevelShifterStrategyPlacement itself DOES
    carry a `power_domain_id` -- this tooltip's own field list is
    narrower than the data available, by design."""
    return [
        ("Name", strategy.name),
        ("Rule", _RULE_LABELS.get(strategy.rule, strategy.rule) or NONE_TEXT),
        ("Cell Type", strategy.cell_type or NONE_TEXT),
    ]


def resolve_retention_fields(strategy: RetentionStrategyPlacement, domain_state: PowerDomainDiagramState) -> list[tuple[str, str]]:
    """Name / Power Domain / Save Logic / Restore Logic -- exactly the
    spec's own field list, in order."""
    return [
        ("Name", strategy.name),
        ("Power Domain", _domain_name(domain_state, strategy.power_domain_id) or NONE_TEXT),
        ("Save Logic", strategy.save_logic or NONE_TEXT),
        ("Restore Logic", strategy.restore_logic or NONE_TEXT),
    ]


def resolve_supply_port_fields(
    placement: SupplyPortPlacement, supply_state: SupplyPortDiagramState, switches: dict
) -> list[tuple[str, str]]:
    """Type / Name / Power States -- exactly the spec's own field list,
    for Supply Port, Internal Supply, AND Virtual Supply alike (one
    shared field list, per the spec's own single combined bullet for
    all three kinds). Type is "Port"/"Internal"/"Virtual" (explicit
    follow-up request, added on top of the original Name/Power States
    pair) -- the three kinds are mutually exclusive flags on the same
    SupplyPortPlacement (is_internal_supply/is_virtual_supply), so
    exactly one of the three labels always applies. Power States is a
    single value listing every state ("NAME (X.XV)" each, comma-joined)
    -- an Internal Supply's own DERIVED effective list
    (resolve_effective_states(), the exact same function/semantics
    properties_panel.py's own "States" column already uses) when
    connected, or the existing "Not yet connected" message (spec's own
    explicit carve-out) when an Internal Supply resolves to genuinely
    nothing. A plain Supply Port/Virtual Supply with zero authored
    states (technically possible for a plain Supply Port -- unlike
    Virtual Supply, it has no "at least one state required" rule) is
    NOT the same "Not yet connected" case at all (nothing is ever
    "connected" about a plain Supply Port's own directly-authored
    states to begin with) -- falls back to the CRITICAL rule's own
    generic "none" instead."""
    if placement.is_internal_supply:
        type_value = "Internal"
    elif placement.is_virtual_supply:
        type_value = "Virtual"
    else:
        type_value = "Port"
    states = resolve_effective_states(placement, supply_state, switches)
    if states:
        states_value = ", ".join(_display_state(s) for s in states)
    elif placement.is_internal_supply:
        states_value = NOT_YET_CONNECTED_TEXT
    else:
        states_value = NONE_TEXT
    return [
        ("Type", type_value),
        ("Name", placement.name),
        ("Power States", states_value),
    ]


def resolve_pg_pin_fields(placement: SupplyPortPlacement) -> list[tuple[str, str]]:
    """Voltage / Type -- exactly the spec's own field list for a
    macro's own PG pin (explicit follow-up request, a NEW element type
    for this tooltip feature): `fixed=True`, exposed directly from the
    macro's own Liberty definition (build_supply_port_overlay()'s own
    PG-pin auto-derivation) -- genuinely distinct from a user-added
    Supply Port/Internal Supply/Virtual Supply, which get their own
    separate field list via resolve_supply_port_fields() above instead.
    `placement.voltage` is display-only, already-formatted data sourced
    straight from the macro's own Definition.ports (see
    SupplyPortPlacement.voltage's own docstring) -- "none" only when
    the Liberty data genuinely didn't specify one. `placement.is_internal`
    (PG_INTERNAL vs. PG) drives Type, same distinction the diagram's own
    icon rendering already keys off."""
    return [
        ("Voltage", placement.voltage or NONE_TEXT),
        ("Type", "Internal PG" if placement.is_internal else "PG"),
    ]


class HoverInfoTooltip(QLabel):
    """Custom-styled hover tooltip -- light yellow background, small/
    compact box (explicit request: "a lightweight 'tooltip' look, not a
    full dialog"). `Qt.WindowType.ToolTip` is the same top-level window
    type Qt's own native QToolTip uses -- frameless, always-on-top, and
    (combined with WA_ShowWithoutActivating) never steals keyboard
    focus or intercepts any mouse event, so it can never block/interfere
    with an active tool-mode interaction underneath it (drawing a net,
    placing an icon, etc.) regardless of DiagramMode. Parented to the
    owning `_PowerIntentGraphicsView` (not None) so its lifetime is tied
    to the view's own -- Qt's parent/child ownership destroys it
    automatically when the view itself is destroyed, the same
    "long-lived, view-owned, no manual cleanup needed" precedent every
    other persistent per-view child widget here already follows."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent, Qt.WindowType.ToolTip)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setWordWrap(False)
        self.setStyleSheet(
            "QLabel {"
            " background-color: #ffffcc;"
            " color: #242930;"
            " border: 1px solid #b8b840;"
            " border-radius: 3px;"
            " padding: 4px 6px;"
            " font-family: 'Menlo', 'Consolas', monospace;"
            " font-size: 11px;"
            "}"
        )

    def show_at(self, global_pos: QPoint, text: str) -> None:
        self.setText(text)
        self.adjustSize()
        self.move(global_pos.x() + 16, global_pos.y() + 16)
        self.show()
