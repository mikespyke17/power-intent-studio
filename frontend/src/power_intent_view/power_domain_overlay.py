"""Power Domain creation on the Power Intent tab's Diagram -- right-click
a hierarchy box (never a macro -- see power_intent_diagram_view.py's own
"Create Power Domain" wiring, disabled there rather than here) for
"Create Power Domain". Diagram-only, same "mock-first" scoping decision
already established for supply ports (see supply_port_overlay.py's own
module docstring) -- nothing here is reflected into the tab's
PowerIntentDesign, no backend.modification.ops.add() call.

Unlike a supply port (its own icon, an independent QGraphicsItem), a
Power Domain has no glyph of its own at all: "once a power domain is
created, all below hierarchy must then have the same colour" (explicit
request) is implemented by *recolouring the hierarchy boxes
themselves* -- InstanceBoxItem.set_domain_color() -- not by drawing a
separate translucent region item the way diagram_overlay.py's own
MappedDesign-driven DomainRegionItem does. That's a real, deliberate
difference from the existing (unrelated) domain-visualization overlay:
this one is about directly recolouring `is_black_box=False` boxes in a
subtree (a macro's own grey never changes, ALWAYS, same unconditional
exception set_highlighted() already makes), not drawing a bounding
region behind them.

A PowerDomainPlacement's `owner_path` is the right-clicked instance --
"all below hierarchy" (apply_domain_coloring()) means every box whose
own path is `owner_path` itself or starts with f"{owner_path}/". Nested
domains are an explicit, supported case now: "a new domain's own
coloring excludes hierarchies which already have a power domain
defined" -- so when domain subtrees overlap (a domain rooted deeper in
the hierarchy than another), the *deeper* (more specific) one always
wins on the overlap, regardless of creation order, carving its own
subtree out of any ancestor domain's coloring. This is also why
"Create Power Domain" is disabled once a box's own path already owns a
domain (see power_intent_diagram_view.py's context-menu wiring) --
"Edit Power Domain" is the only way to change that box's own domain
after that, but a *descendant* box is still free to get its own,
nested domain.

SupplySetPlacement models a UPF supply_set's own 4 function handles
(Power/Ground/N-Well/P-Well, per the request) as plain name references
-- matching backend.elaborator.power_intent.ir.schema.SupplySet's own
`power_net`/`ground_net` fields being raw strings, not object
references, not a coincidence: a Supply Set doesn't own a physical
port, it just *names* one. "List of supply ports which have been
already created on the hierarchy" (explicit request) means the pool of
supply names offered when composing a Supply Set is scoped to the one
hierarchy box the domain is being created/edited on -- see
supply_port_overlay.py's own known_supply_names_for_owner() -- covering
both user-placed ports and macro PG pins anchored to that box, not
every supply name diagram-wide. A Supply Set object itself is still a
logical construct, reusable as any domain's primary/secondary
regardless of which box it was first composed on -- only the *name
picker* offered while creating one is box-scoped.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Optional

from PySide6.QtCore import QPointF, QRectF, QSizeF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPen
from PySide6.QtWidgets import QGraphicsItem

from frontend.src.power_intent_view.macro_power_intent_overlay import MacroPowerIntentBadgeItem
from frontend.src.power_intent_view.supply_port_overlay import INTERIOR_TOP_MARGIN_FOR_SUPPLY_PORT, SupplyPortIconItem
from frontend.src.shared.diagram.hierarchy_scene_builder import InstanceBoxItem

_LABEL_MARGIN = 6
_DEFAULT_DOMAIN_COLOR = QColor("#4caf50")  # same default create_power_domain_dialog.py's own color picker used to start with
# Explicit request, supersedes the earlier user-adjustable 0.5 default:
# a Power Domain's own transparency is now a FIXED value, not exposed
# in the dialog at all -- see resolve_domain_color()'s own docstring.
DOMAIN_FIXED_TRANSPARENCY = 0.8


@dataclass
class SupplySetPlacement:
    """One UPF-style supply_set -- a name plus up to 4 named function-
    handle references (Power/Ground/N-Well/P-Well), each just a raw
    supply name string (see module docstring), not a reference to any
    specific SupplyPortPlacement/icon.

    `owner_path` is the hierarchy box it was *composed on* (its own
    Power/Ground/N-Well/P-Well names are drawn from that box's own
    known_supply_names_for_owner() pool -- see module docstring) --
    recorded so the Properties panel can display this Supply Set's own
    name, and each of its 4 field values, prefixed with that hierarchy
    (explicit request: "Power, Ground, N-Well and P-Well also come with
    the hierarchy as prefix"), even though the Supply Set object itself
    remains reusable as any domain's primary/secondary regardless of
    that origin. Defaults to "" for pre-existing callers/tests that
    construct one without a hierarchy in mind.

    `owner_paths` (explicit request, multi-hierarchy Power Domain): the
    FULL list of hierarchies this Supply Set actually belongs to -- for
    a Supply Set composed under a multi-hierarchy Power Domain, this
    holds every one of that domain's own member hierarchies (the
    resulting Supply Port/Internal Supply is REPLICATED onto each one --
    see power_intent_diagram_view.py's own _add_missing_supply_ports())
    rather than picking just one arbitrarily. `owner_path` is kept as a
    backward-compatible single value -- the "primary"/first entry of
    `owner_paths` -- so every pre-existing single-hierarchy caller/test
    that only ever passes `owner_path=` keeps working completely
    unchanged (see __post_init__ below); real logic that needs to
    reason about the *whole* membership reads `owner_paths`, never bare
    `owner_path`, alone."""

    id: str
    name: str
    power: Optional[str] = None
    ground: Optional[str] = None
    n_well: Optional[str] = None
    p_well: Optional[str] = None
    owner_path: str = ""
    owner_paths: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.owner_paths:
            self.owner_path = self.owner_paths[0]
        else:
            self.owner_paths = [self.owner_path]


@dataclass
class PowerDomainPlacement:
    """Plain, Qt-item-independent record of one Power Domain -- see
    module docstring for how each of `owner_paths`' own subtrees get
    recoloured. `transparency` is a 0..1 fraction (0 = fully opaque, 1 =
    fully invisible); default 0.5, per the explicit request. Referenced
    Supply Sets are stored by id (SupplySetPlacement.id), not by value
    -- the same Supply Set can be a domain's primary or another
    domain's secondary.

    `owner_paths` (explicit request, multi-hierarchy Power Domain): a
    domain created from a multi-select now tracks EVERY selected
    hierarchy block as a full, independent member -- not just one. Same
    backward-compatible `owner_path`-as-primary-of-`owner_paths`
    relationship as SupplySetPlacement above (see its own docstring for
    the full "why"; __post_init__ here is identical)."""

    id: str
    owner_path: str
    name: str
    color: QColor
    transparency: float = 0.5
    primary_supply_set: Optional[str] = None
    secondary_supply_sets: list[str] = field(default_factory=list)
    owner_paths: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.owner_paths:
            self.owner_path = self.owner_paths[0]
        else:
            self.owner_paths = [self.owner_path]


@dataclass
class PowerDomainDiagramState:
    """Shared, Qt-independent Power Domain state for an entire Power
    Intent Diagram (every tab -- same cross-tab-sharing mechanism as
    SupplyPortDiagramState, see its own docstring for the full
    reasoning: a domain created while looking at a sub-module's own
    diagram tab must show up in the whole-hierarchy tab too). One
    instance is constructed once by PowerIntentDiagramView and handed to
    every _PowerIntentGraphicsView it creates."""

    domains: dict[str, PowerDomainPlacement] = field(default_factory=dict)
    supply_sets: dict[str, SupplySetPlacement] = field(default_factory=dict)


def copy_power_domain_diagram_state(state: PowerDomainDiagramState) -> PowerDomainDiagramState:
    """An independent snapshot of `state` -- same "why" as
    supply_port_overlay.py's own copy_supply_port_diagram_state():
    Ctrl+Z/Ctrl+Y undo/redo needs a genuinely separate copy, not a
    shallow one still sharing the same mutable placement objects with
    the live state."""
    return PowerDomainDiagramState(
        domains={k: replace(v, secondary_supply_sets=list(v.secondary_supply_sets), owner_paths=list(v.owner_paths)) for k, v in state.domains.items()},
        supply_sets={k: replace(v, owner_paths=list(v.owner_paths)) for k, v in state.supply_sets.items()},
    )


def restore_power_domain_diagram_state(state: PowerDomainDiagramState, snapshot: PowerDomainDiagramState) -> None:
    """Copies `snapshot`'s fields onto the *live* `state` object in
    place -- never replaces `state` itself, since it's shared by
    reference across every tab (see the class's own docstring)."""
    restored = copy_power_domain_diagram_state(snapshot)
    state.domains = restored.domains
    state.supply_sets = restored.supply_sets


def supply_sets_for_owner(
    state: PowerDomainDiagramState, owner_path: str, exclude_domain_id: Optional[str] = None
) -> dict[str, SupplySetPlacement]:
    """Supply Sets offered by Create/Edit Power Domain's own Primary/
    Secondary pickers -- explicit request/bug fix: the list used to be
    every Supply Set diagram-wide, including ones composed on a totally
    different hierarchy or already claimed by another domain entirely.
    Scoped to exactly two conditions: (1) `owner_path` exact match (same
    "composed on this one box" scoping known_supply_names_for_owner()
    already applies to the names pool beneath a Supply Set -- see this
    module's own docstring), not a subtree; (2) not already referenced
    as another domain's own primary/secondary. `exclude_domain_id` (the
    domain currently being edited, if any) doesn't count as "another"
    domain against its own already-chosen sets -- otherwise editing a
    domain would make its own current Primary/Secondary vanish from the
    list."""
    claimed_elsewhere = {
        sid
        for domain in state.domains.values()
        if domain.id != exclude_domain_id
        for sid in (domain.primary_supply_set, *domain.secondary_supply_sets)
        if sid
    }
    return {sid: s for sid, s in state.supply_sets.items() if owner_path in s.owner_paths and sid not in claimed_elsewhere}


def supply_sets_for_owners(
    state: PowerDomainDiagramState, owner_paths: list[str], exclude_domain_id: Optional[str] = None
) -> dict[str, SupplySetPlacement]:
    """Union of supply_sets_for_owner() across several hierarchy boxes
    at once (explicit request, multi-hierarchy Power Domain): the
    Primary/Secondary Supply Set picker for a domain spanning several
    member hierarchies must offer Supply Sets scoped to ANY of them."""
    result: dict[str, SupplySetPlacement] = {}
    for owner_path in owner_paths:
        result.update(supply_sets_for_owner(state, owner_path, exclude_domain_id=exclude_domain_id))
    return result


def supply_sets_referenced_by_domain(
    state: PowerDomainDiagramState, domain: PowerDomainPlacement
) -> dict[str, SupplySetPlacement]:
    """The domain's OWN Supply Sets -- its primary plus every secondary
    -- resolved straight out of `state.supply_sets`.

    Deliberately NOT supply_sets_for_owner()'s "offer" semantics, and
    the distinction is what this function exists for. That helper
    answers "which sets may this domain CLAIM", so it excludes any set
    already claimed by another domain -- correct for Create/Edit Power
    Domain's own picker, and wrong for anything that needs to RESOLVE
    the sets a domain already references. Ancestor Supply Set sharing
    makes the two disagree routinely: a nested domain legitimately
    references an ancestor domain's own primary as its secondary, at
    which point that ancestor's picker no longer offers its own primary
    -- so a caller indexing the offer by the domain's own ids raised
    KeyError and killed its dialog before it could open.

    A dangling id (the set was deleted) is skipped rather than raising:
    this returns what it can resolve."""
    ids = [domain.primary_supply_set, *domain.secondary_supply_sets]
    return {sid: state.supply_sets[sid] for sid in ids if sid and sid in state.supply_sets}


def _is_strict_ancestor(candidate_ancestor_path: str, path: str) -> bool:
    """True iff `candidate_ancestor_path` is a STRICT ancestor of
    `path` -- strictly ABOVE it on the path toward the root, never the
    same path (unlike is_nested_pair()'s own same-path-counts-too
    reading, needed for a genuinely different concern -- connector
    placement) and never a descendant or sibling."""
    return path.startswith(f"{candidate_ancestor_path}/")


def supply_sets_on_ancestor_hierarchies(supply_sets: dict[str, SupplySetPlacement], owner_path: str) -> dict[str, SupplySetPlacement]:
    """CHANGE 2 (explicit request): every Supply Set that already
    exists on a STRICT ANCESTOR hierarchy of `owner_path` -- offered by
    Create/Edit Power Domain's own "Supply Set" dropdown as a SHARING
    option (selecting one references the SAME SupplySetPlacement id,
    never a clone -- see create_power_domain_dialog.py's own module
    docstring for the full "why"). Deliberately UNSCOPED by
    `state.domains` (unlike supply_sets_for_owner()'s own "not already
    claimed elsewhere" exclusion) -- an already-claimed ancestor Supply
    Set is exactly what this dropdown exists to offer for sharing, not
    something to filter out. A Supply Set matches if ANY of its own
    (possibly several, multi-hierarchy-replicated) `owner_paths` is a
    strict ancestor of `owner_path` -- sibling and descendant
    hierarchies are excluded, matching a `path.startswith(f"{op}/")`
    check exactly once per candidate owner_path."""
    return {sid: s for sid, s in supply_sets.items() if any(_is_strict_ancestor(op, owner_path) for op in s.owner_paths)}


def supply_sets_on_ancestor_hierarchies_for_owners(supply_sets: dict[str, SupplySetPlacement], owner_paths: list[str]) -> dict[str, SupplySetPlacement]:
    """Union of supply_sets_on_ancestor_hierarchies() across several
    hierarchy boxes at once -- same "multi-hierarchy Power Domain"
    generalization supply_sets_for_owners() already establishes for
    the (now-removed) Primary/Secondary picker."""
    result: dict[str, SupplySetPlacement] = {}
    for owner_path in owner_paths:
        result.update(supply_sets_on_ancestor_hierarchies(supply_sets, owner_path))
    return result


def supply_set_name_used_by_another_domain(
    name: str,
    domains: dict[str, PowerDomainPlacement],
    supply_sets: dict[str, SupplySetPlacement],
    exclude_domain_id: Optional[str] = None,
) -> bool:
    """Fix 3C (explicit request): Supply Set names must be unique
    ACROSS Power Domains, not just within one -- a WIDER scope than
    supply_sets_for_owner()'s own per-hierarchy LIST scoping (which
    stays exactly as-is; only this NAME uniqueness check is global).
    True if `name` already belongs to a Supply Set referenced (as
    Primary or any Secondary) by some Power Domain other than
    `exclude_domain_id` (the domain currently being created/edited, if
    any -- its own already-chosen Supply Sets don't count as "another"
    domain against themselves, same precedent supply_sets_for_owner()'s
    own `exclude_domain_id` already establishes)."""
    for domain_id, domain in domains.items():
        if domain_id == exclude_domain_id:
            continue
        for sid in (domain.primary_supply_set, *domain.secondary_supply_sets):
            if sid is None:
                continue
            supply_set = supply_sets.get(sid)
            if supply_set is not None and supply_set.name == name:
                return True
    return False


def supply_set_with_duplicate_pin_combination(
    power: Optional[str],
    ground: Optional[str],
    n_well: Optional[str],
    p_well: Optional[str],
    supply_sets: dict[str, SupplySetPlacement],
    exclude_id: Optional[str] = None,
) -> Optional[SupplySetPlacement]:
    """Explicit request: a Supply Set's own full pin combination (Power
    AND Ground AND N-Well AND P-Well) must not exactly match another
    existing Supply Set's -- checked against whatever `supply_sets` the
    caller passes in, keyed directly (every SupplySetPlacement in that
    dict), not funneled through `domains` the way the name check is.

    Bug fix (explicit policy correction, superseding an earlier version
    of this docstring): this function is intentionally scope-agnostic --
    it only ever compares against the `supply_sets` dict it's handed,
    never anything design-wide on its own. Its ONLY real caller
    (create_power_domain_dialog.py's own `_prompt_create_and_assign_supply_set()`)
    now passes `_this_domains_supply_sets()` -- the domain currently
    being created/edited's own Primary/Secondary only, not every Supply
    Set in the design -- since this check's whole purpose is avoiding an
    ambiguous tie WITHIN one domain's own composition; two unrelated
    Power Domains reusing the same pin-naming convention (e.g. VDD/VSS)
    is completely normal and must NOT be rejected. A previous version of
    this function's own docstring described "GLOBAL scope" as
    deliberate -- that was the bug; scoping is entirely the caller's
    responsibility, not something this function should assume.

    PARTIAL overlap (sharing exactly one rail, e.g. two Supply Sets both
    using VSS as Ground but different Power) is explicitly legal and
    never flagged here -- only a full match across all 4 fields blocks.
    This makes a "both Power AND Ground shared" collision impossible to
    construct in the first place, so macro PG-pin-pair resolution
    (power_resolution_cache.py's own per-rail validation) never needs
    tie-breaking logic for it.

    Returns the CONFLICTING SupplySetPlacement itself (not just a bool)
    so the caller can name it in the error message, matching the
    explicit "name the conflicting Supply Set" requirement. `exclude_id`
    (same precedent as `exclude_domain_id` elsewhere) lets a future
    "Edit Supply Set" flow, if one is ever added, compare a Supply Set
    against every OTHER one without spuriously colliding with itself --
    no such edit flow exists yet (Supply Sets are creation-only today),
    so every current caller passes `exclude_id=None`."""
    for sid, existing in supply_sets.items():
        if sid == exclude_id:
            continue
        if existing.power == power and existing.ground == ground and existing.n_well == n_well and existing.p_well == p_well:
            return existing
    return None


def find_domain_for_owner(domains: dict[str, PowerDomainPlacement], owner_path: str) -> Optional[PowerDomainPlacement]:
    """The domain `owner_path` itself owns (not merely nested inside),
    if any -- drives both "Create Power Domain"'s disabled state and
    "Edit Power Domain"'s enabled state (see power_intent_diagram_view.py).
    For a multi-hierarchy domain (explicit request), `owner_path` matching
    ANY ONE of its own `owner_paths` counts as ownership -- editing from
    any single member hierarchy must reach the same shared domain
    entity, not just its "primary" one."""
    for domain in domains.values():
        if owner_path in domain.owner_paths:
            return domain
    return None


def find_effective_domain_for_path(domains: dict[str, PowerDomainPlacement], path: str) -> Optional[PowerDomainPlacement]:
    """The domain that actually *colours* `path` once cascading/nesting
    is accounted for -- i.e. the same "deepest owner wins" rule
    apply_domain_coloring() already applies when recolouring boxes,
    reused here as a pure lookup (no boxes/scene involved) for Retention's
    own validation (rule A: "target block must be mapped to a Power
    Domain") and its recursive, domain-aware Elements collection (see
    retention_overlay.py's collect_retention_elements() -- comparing this
    function's result at each candidate node against the target's own is
    the entire mechanism: a node whose own effective domain differs from
    the target's is excluded, and since this function is already
    "deepest owner wins," no separate subtree-stop bookkeeping is needed
    -- every candidate is simply asked independently).

    Unlike find_domain_for_owner() (exact owner_path match only), this
    also matches a path that is merely *inside* a domain's own subtree
    (owner_path itself, or any of its descendants) -- ancestor
    membership, not just direct ownership. For a multi-hierarchy domain,
    each of its own `owner_paths` is considered as an independent
    subtree root -- the "deepest wins" comparison is against whichever
    one of a domain's own members actually covers `path`, not the
    domain's arbitrary "primary" member."""
    best_domain: Optional[PowerDomainPlacement] = None
    best_depth = -1
    for domain in domains.values():
        for owner_path in domain.owner_paths:
            if path == owner_path or path.startswith(f"{owner_path}/"):
                depth = owner_path.count("/")
                if depth > best_depth:
                    best_depth = depth
                    best_domain = domain
    return best_domain


def resolve_supply_set_color_source(supply_set: SupplySetPlacement, supply_port_icons: dict[str, SupplyPortIconItem]) -> Optional[SupplyPortIconItem]:
    """A Supply Set has no glyph of its own in the diagram ("a Supply
    Set doesn't own a physical port, it just names one") -- same as
    Isolation/Retention, its own color is the color of whichever placed
    SupplyPortIconItem's own name matches this Supply Set's `.power`
    field, scoped to the Supply Set's OWN `owner_paths` (the exact same
    match rule resolve_isolation_color_source()/
    resolve_retention_color_source() already apply -- reused here
    verbatim on the SupplySetPlacement directly, rather than on a
    strategy that references one, since a Supply Set node has no
    strategy wrapping it). Lives here (not power_view_tree.py, its
    original home) since Power Domain's own color derivation
    (resolve_domain_color() below) needs it too, and this module is
    where SupplySetPlacement itself is defined."""
    if not supply_set.power:
        return None
    for icon in supply_port_icons.values():
        if icon.owner_box.path in supply_set.owner_paths and icon.name == supply_set.power:
            return icon
    return None


def resolve_supply_set_color_from_placements(supply_set: SupplySetPlacement, placements) -> Optional[QColor]:
    """`resolve_supply_set_color_source()`'s pre-view counterpart.

    Same match rule, stated once here and once above: a placed supply
    whose NAME equals this set's power rail AND whose owner path is in
    this set's own `owner_paths`. The difference is only what it matches
    against -- live `SupplyPortIconItem`s there, plain
    `SupplyPortPlacement`s here -- so it can run before any Qt item
    exists, which is exactly the situation the UPF/CPF import is in
    (see power_intent_import.build_domains()). Kept adjacent rather than
    factored into one generic helper because the two differ only in the
    attribute the owner path is read from, and a shared abstraction over
    that would be longer than either.

    `placements` is any iterable of objects with `.name`, `.owner_path`
    and `.color`."""
    if not supply_set.power:
        return None
    for placement in placements:
        if placement.owner_path in supply_set.owner_paths and placement.name == supply_set.power:
            return QColor(placement.color)
    return None


def resolve_domain_color(
    primary_supply_set_id: Optional[str],
    supply_sets: dict[str, SupplySetPlacement],
    supply_port_icons: dict[str, SupplyPortIconItem],
    fallback: Optional[QColor] = None,
) -> QColor:
    """A Power Domain's own color -- explicit request, superseding the
    earlier manual colour-picker design entirely: no longer user-chosen,
    LIVE-derived instead from the domain's own Primary Supply Set's Power
    rail, the exact same live-color-binding pattern already established
    for Isolation (follows its own Isolation Supply Set), Power Switch
    (follows Supply Out), and Retention (follows its own Retention Supply
    Set's Power rail) -- just applied to Power Domain's Primary Supply
    Set instead. Since PowerDomainPlacement is a plain, Qt-independent
    dataclass (no live Qt property mechanism to hook a paint()-time
    re-derivation into, unlike a real SupplyPortIconItem), "live" here
    means recomputed explicitly on every trigger that could change the
    result -- see power_intent_diagram_view.py's own
    _refresh_domain_derived_colors(), called from create_power_domain(),
    edit_power_domain(), edit_supply_port(), and edit_supply_set().

    Falls back to `fallback` (or _DEFAULT_DOMAIN_COLOR if that's also
    omitted) when there's no Primary Supply Set yet, or its own Power
    rail doesn't resolve to a live Supply Port icon (e.g. referencing a
    name with no matching port anywhere) -- same "graceful fallback, not
    a crash" precedent every other resolve_*_color_source() consumer
    already follows. `_refresh_domain_derived_colors()` passes the
    domain's own CURRENT `.color` as `fallback` -- so a domain with no
    Primary Supply Set (a real, valid, pre-existing state -- Primary is
    optional at the model level even though the dialog requires it)
    simply keeps whatever color it already had, rather than snapping to
    one fixed default the moment there's nothing to derive from."""
    if primary_supply_set_id is not None:
        supply_set = supply_sets.get(primary_supply_set_id)
        if supply_set is not None:
            icon = resolve_supply_set_color_source(supply_set, supply_port_icons)
            if icon is not None:
                return QColor(icon.color)
    return QColor(fallback) if fallback is not None else QColor(_DEFAULT_DOMAIN_COLOR)


def apply_domain_coloring(boxes: dict[str, InstanceBoxItem], domains: dict[str, PowerDomainPlacement]) -> None:
    """Recolours every box in `boxes` to reflect the current Power
    Domain assignments -- called once per rebuild (see
    power_intent_diagram_view.py's _rebuild()), after the boxes
    themselves already exist. Resets every box to "no domain" first
    (color=None), so a domain that was deleted -- or whose owner box is
    no longer in `boxes` at all, e.g. hidden -- doesn't leave a stale
    colour behind; then applies each domain's own colour to its own
    subtree(s), *shallowest owner_path first* so a more deeply-nested
    domain's own subtree always ends up carrying its own colour, never
    an ancestor domain's -- see module docstring's "excludes hierarchies
    which already have a power domain defined". A multi-hierarchy domain
    (explicit request) contributes one (owner_path, domain) pair per
    member hierarchy to this same shallowest-first ordering, rather than
    being treated as a single unit -- each member's own subtree is
    coloured independently, exactly like a single-hierarchy domain's
    subtree always was."""
    for box in boxes.values():
        box.set_domain_color(None)
    member_pairs = [(owner_path, domain) for domain in domains.values() for owner_path in domain.owner_paths]
    member_pairs.sort(key=lambda pair: pair[0].count("/"))
    for owner_path, domain in member_pairs:
        for path, box in boxes.items():
            if path == owner_path or path.startswith(f"{owner_path}/"):
                box.set_domain_color(domain.color, domain.transparency)


_DOMAIN_INFO_PADDING = 4.0


def _domain_name_font() -> QFont:
    """Bold italic, unchanged styling carried over from the
    now-superseded DomainLabelItem's own font -- only this box's own
    POSITION changed (bottom-right, same corner as before), not this
    line's own look."""
    font = QFont()
    font.setBold(True)
    font.setItalic(True)
    return font


def _supply_set_line_font() -> QFont:
    """Italic, NOT bold, deliberately SMALLER than the domain name
    line's own font -- a relative reduction (not a fixed point size) so
    this stays smaller than whatever the platform's own default font
    resolves to, rather than assuming a specific baseline size. Carried
    over unchanged from the now-superseded SupplySetInfoBoxItem's own
    font."""
    font = QFont()
    font.setItalic(True)
    font.setPointSizeF(font.pointSizeF() * 0.85)
    return font


_DOMAIN_INFO_LINE_GAP = 2.0


def _stacked_line_layout(rows_with_metrics: list[tuple[QFontMetricsF, str]]) -> tuple[float, list[float]]:
    """Bug fix (explicit report, follow-up to the earlier WIDTH-only
    fix): computes TOTAL height and each line's own BASELINE y
    (relative to the block's own top, y=0) from each line's own REAL
    boundingRect() vertical extent -- not the generic, per-FONT
    `ascent()`/`descent()`/`height()` the earlier version of this box
    used for every line uniformly. Two real, independently confirmed
    problems that formula had: (1) a font's own `.height()` reserves
    room for its OWN worst-case descender, regardless of whether THIS
    SPECIFIC text actually has one -- "PD_MEM"/"SS_VDD" etc. (no
    descenders at all) still consumed the domain-name font's own full
    descent allowance before the NEXT (smaller-font) line began,
    reading as unexplained empty space between them on a font/text
    combination where that allowance is generous; (2) exactly the
    reverse risk WIDTH already needed fixing for -- a font's own
    generic per-line `height()` is an approximation, not a guarantee,
    of what THIS text's own glyphs actually need, so a line whose real
    rendered extent exceeds that generic figure would spill past
    wherever the box's own border was sized from it. boundingRect()
    based stacking fixes both by construction: each line gets EXACTLY
    the room its own glyphs need (tight, no reason-less gap) with a
    fixed, deliberate `_DOMAIN_INFO_LINE_GAP` between lines for
    legibility (not zero -- adjacent lines touching edge-to-edge reads
    just as badly as an oversized gap), and the running total is
    always the true sum of what was actually measured, so it can never
    under-reserve.

    Shared by _content_size()/paint() (DomainInfoBoxItem, below) AND
    domain_info_box_required_height() -- the SAME single source of
    truth both already relied on domain_info_box_rows() to guarantee
    (identical line COUNT never drifting apart from the reservation
    formula); this closes the analogous risk for line HEIGHT too, now
    that height depends on each line's own TEXT, not just its count."""
    y = 0.0
    baselines: list[float] = []
    for index, (metrics, text) in enumerate(rows_with_metrics):
        rect = metrics.boundingRect(text)
        baseline = y - rect.top()
        baselines.append(baseline)
        y = baseline + rect.bottom()
        if index < len(rows_with_metrics) - 1:
            y += _DOMAIN_INFO_LINE_GAP
    return y, baselines


def domain_info_box_rows(
    domain: PowerDomainPlacement,
    supply_sets: dict[str, SupplySetPlacement],
    supply_port_icons: dict[str, SupplyPortIconItem],
) -> list[tuple[str, QColor]]:
    """The exact (text, color) pairs the merged Power Domain info box
    (DomainInfoBoxItem, below) renders, top to bottom -- explicit
    request, supersedes the now-separate DomainLabelItem/
    SupplySetInfoBoxItem overlays with ONE combined box:

    - The domain's own bare name, first -- same text the now-superseded
      DomainLabelItem alone rendered, always present (a domain always
      exists once created, regardless of whether it has a Primary
      Supply Set composed yet).
    - "Primary : <name>" next -- SAME color as the name line, since the
      domain's own derived `.color` IS the Primary Supply Set's own
      Power rail color already (resolve_domain_color()'s own
      docstring); "?" for a dangling/not-yet-composed Primary, same
      "not guessed at" convention the now-superseded
      supply_set_info_lines() already established, rather than
      omitting the line entirely.
    - One "Secondary : <name>" per secondary Supply Set, EACH resolved
      INDEPENDENTLY via resolve_domain_color() against its OWN id --
      explicit request: a Secondary's own line uses ITS OWN Supply
      Set's own Power rail color, NOT the domain's overall color,
      unlike the Primary line above. "Live" here means recomputed
      fresh on every rebuild -- the exact same "recompute at every
      trigger, no continuously-bound Qt signal" precedent
      resolve_domain_color()'s own docstring already establishes for
      the domain's own color itself, not a new mechanism.

    Shared by DomainInfoBoxItem's own sizing/painting AND
    power_intent_diagram_view.py's own _extra_interior_bottom() margin
    contribution (domain_info_box_required_height() below), so the two
    can never drift out of sync on how many lines this box needs."""
    rows: list[tuple[str, QColor]] = [(domain.name, QColor(domain.color))]
    primary_name = supply_sets[domain.primary_supply_set].name if domain.primary_supply_set in supply_sets else "?"
    rows.append((f"Primary : {primary_name}", QColor(domain.color)))
    for secondary_id in domain.secondary_supply_sets:
        secondary_name = supply_sets[secondary_id].name if secondary_id in supply_sets else "?"
        secondary_color = resolve_domain_color(secondary_id, supply_sets, supply_port_icons, fallback=domain.color)
        rows.append((f"Secondary : {secondary_name}", secondary_color))
    return rows


def domain_info_box_required_height(domain: PowerDomainPlacement, supply_sets: dict[str, SupplySetPlacement]) -> float:
    """The vertical room power_intent_diagram_view.py's own
    _extra_interior_bottom() must reserve, per member hierarchy, for
    this domain's own merged info box -- BOTTOM, not top, since this
    box anchors bottom-right (see DomainInfoBoxItem's own docstring for
    why).

    Bug fix (explicit report, follow-up to the WIDTH-only fix): this
    used to compute height from generic per-FONT `.height()` figures
    (name font once, line font times line count) entirely independent
    of domain_info_box_rows()/DomainInfoBoxItem's own actual TEXT --
    a second, separately-maintained height formula that could silently
    drift from whatever DomainInfoBoxItem itself actually renders (the
    exact "two formulas, one derived from the other's inputs but
    computed independently" risk domain_info_box_rows()'s own docstring
    already flagged for LINE COUNT). Now reuses domain_info_box_rows()
    directly (passing an empty `supply_port_icons` -- resolve_domain_color()'s
    own documented `fallback` parameter makes this safe; only each row's
    TEXT matters for height, never its resolved color) and
    _stacked_line_layout() -- the SAME boundingRect()-based stacking
    DomainInfoBoxItem._content_size() itself now uses -- so this
    reservation and the box's own rendered height can never diverge,
    by construction rather than by two hand-kept-in-sync formulas."""
    rows = domain_info_box_rows(domain, supply_sets, {})
    name_metrics = QFontMetricsF(_domain_name_font())
    line_metrics = QFontMetricsF(_supply_set_line_font())
    metrics_and_text = [(name_metrics, rows[0][0])] + [(line_metrics, text) for text, _color in rows[1:]]
    height, _baselines = _stacked_line_layout(metrics_and_text)
    return height + 2 * _DOMAIN_INFO_PADDING


class DomainInfoBoxItem(QGraphicsItem):
    """The merged Power Domain name + Supply Set list box (explicit
    request, supersedes the earlier separate DomainLabelItem --
    bold-italic name, bottom-right corner -- and SupplySetInfoBoxItem --
    dashed-border Supply Set list, top-right corner -- with ONE
    combined box). Anchored to the block's own BOTTOM-RIGHT corner --
    the SAME corner the now-superseded DomainLabelItem alone used to
    occupy; SupplySetInfoBoxItem's own former top-right corner is now
    free again.

    Content, top to bottom (see domain_info_box_rows()'s own docstring
    for the full "why" of each line's own text/color): the domain's own
    name (bold italic, unchanged styling from the now-superseded
    DomainLabelItem), "Primary : <name>" (italic, not bold, smaller
    font -- same domain color as the name line), then one
    "Secondary : <name>" per secondary Supply Set (same italic/smaller
    styling, each independently colored by its OWN Supply Set).

    Dashed grey border wraps the WHOLE merged box (every line
    together) -- the SAME border style the now-superseded
    SupplySetInfoBoxItem already used, just around the combined content
    instead of the Supply-Set-only lines alone.

    Room for this box is reserved via power_intent_diagram_view.py's
    own _extra_interior_bottom() (domain_info_box_required_height(),
    summed alongside the Ground-anchor routing-clearance reason that
    mechanism already covers -- same "sum, not overlap" convention
    _extra_interior_top() already established for its own multiple
    independent reasons), which GROWS the block's own height to fit it
    -- the same shared "make room on insertion" mechanism every other
    element type here already funnels through, not a bespoke one.

    Deliberately has NO connectivity of any kind (no handles, no
    `.connectors`, never appears in any connector-routing code path) --
    purely a read-only visual summary, same precedent
    SupplySetInfoBoxItem already established, carried over unchanged."""

    def __init__(
        self,
        domain: PowerDomainPlacement,
        owner_box: InstanceBoxItem,
        supply_sets: dict[str, SupplySetPlacement],
        supply_port_icons: dict[str, SupplyPortIconItem],
    ):
        super().__init__(owner_box)
        self.owner_box = owner_box
        self.domain_id = domain.id
        self._name_font = _domain_name_font()
        self._line_font = _supply_set_line_font()
        self._name_metrics = QFontMetricsF(self._name_font)
        self._line_metrics = QFontMetricsF(self._line_font)
        self._rows = domain_info_box_rows(domain, supply_sets, supply_port_icons)
        self.setZValue(1)  # above the box's own translucent domain fill, same as every other overlay item parented to it
        self._reposition()
        owner_box.on_resize_callbacks.append(self._reposition)

    def _line_metrics_and_text(self) -> list[tuple[QFontMetricsF, str]]:
        name_text, _name_color = self._rows[0]
        return [(self._name_metrics, name_text)] + [(self._line_metrics, text) for text, _color in self._rows[1:]]

    def _content_size(self) -> QSizeF:
        """Bug fix (explicit report): width was computed from
        horizontalAdvance() -- the CURSOR-ADVANCE distance a string
        occupies, not the actual visual extent of its rendered glyphs.
        For BOLD ITALIC (the name line) or ITALIC (every other line)
        text, the last character's own slant can visually overhang past
        its own advance width -- confirmed empirically (a live
        diagnostic measuring a real QFontMetricsF against this exact
        bold-italic/italic font pair found a line whose
        boundingRect().right() exceeded its own horizontalAdvance()),
        which is exactly what let text spill past the box's own dashed
        border on some fonts. boundingRect(text).right() -- the
        rightmost visual pixel reached from the SAME x=0 drawing origin
        drawText() itself uses -- is the correct measure of "how wide a
        box does this text actually need," not `.width()` alone (whose
        own x-origin can be offset from 0 too).

        Bug fix (explicit report, follow-up): HEIGHT used to be
        `name_metrics.height() + line_metrics.height() * (n - 1)` --
        each line reserving its own FONT's generic worst-case
        ascent+descent regardless of what THIS line's own text actually
        needed. A short, descender-less line (e.g. "PD_MEM") still
        consumed its font's full descent allowance before the next line
        began, reading as unexplained empty space; the same generic
        figure could, on a different font/text, just as easily fall
        short of a line's real extent instead. Now delegates to
        _stacked_line_layout() -- the same boundingRect()-driven,
        per-line-actual-extent stacking domain_info_box_required_height()
        also uses -- so this box is sized for exactly what it draws,
        nothing more, nothing less."""
        widths = [metrics.boundingRect(text).right() for metrics, text in self._line_metrics_and_text()]
        width = max(widths, default=0.0)
        height, _baselines = _stacked_line_layout(self._line_metrics_and_text())
        return QSizeF(width + 2 * _DOMAIN_INFO_PADDING, height + 2 * _DOMAIN_INFO_PADDING)

    def boundingRect(self) -> QRectF:
        size = self._content_size()
        return QRectF(0, 0, size.width(), size.height())

    def paint(self, painter, option, widget=None) -> None:
        rect = self.boundingRect()
        pen = QPen(QColor("#9e9e9e"))
        pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(rect)

        _height, baselines = _stacked_line_layout(self._line_metrics_and_text())

        name_text, name_color = self._rows[0]
        painter.setFont(self._name_font)
        painter.setPen(QColor(name_color))
        painter.drawText(QPointF(_DOMAIN_INFO_PADDING, _DOMAIN_INFO_PADDING + baselines[0]), name_text)

        painter.setFont(self._line_font)
        for index, (text, color) in enumerate(self._rows[1:], start=1):
            painter.setPen(QColor(color))
            painter.drawText(QPointF(_DOMAIN_INFO_PADDING, _DOMAIN_INFO_PADDING + baselines[index]), text)

    def _reposition(self) -> None:
        """Registered on the box's own on_resize_callbacks (same
        precedent the now-superseded DomainLabelItem/SupplySetInfoBoxItem
        both already established for their own corner-anchored
        positions) so a LATER resize of the SAME box -- e.g. the "make
        room" expansion this box's OWN presence just triggered, or an
        unrelated later insertion -- keeps this box correctly anchored
        to the bottom-right corner.

        Bug fix (explicit report): a Ground-anchored (edge=="bottom")
        Supply Port/Internal Supply/macro PG pin renders its own icon
        AND label in that exact same bottom-of-block band this box
        anchors into (_extra_interior_bottom()'s own docstring already
        SUMS both reasons' required height into the block's own growth,
        but that growth alone doesn't say WHO gets which portion of it
        -- both were independently anchoring flush against the same
        `rect.bottom()`, so they visually overlapped regardless of how
        tall the block grew). Fixed by shifting this box UP by
        INTERIOR_TOP_MARGIN_FOR_SUPPLY_PORT -- the EXACT same amount
        _extra_interior_bottom() already reserves for a ground anchor --
        whenever `owner_box` currently has one, so the two occupy
        distinct, non-overlapping bands: the ground anchor keeps the
        bottom-most strip, this box sits directly above it.

        Checked via `self.owner_box.childItems()` (the LIVE scene graph
        -- every materialized SupplyPortIconItem is a real Qt child of
        its own owner_box), never a `supply_port_icons` dict snapshot
        captured at THIS item's own construction time -- that snapshot
        would go stale the instant a new ground-anchored port is added
        via the ordinary add_supply_port()/add_internal_supply() flow
        afterward (which only triggers _reflow_layout(), never a full
        _rebuild() that would reconstruct this item fresh), reproducing
        the exact "direct insertion doesn't apply the fix, only undo+redo
        does" bug class this project has hit before -- undo+redo happens
        to mask a stale-snapshot bug by forcing a full _rebuild(), direct
        insertion would not."""
        rect = self.owner_box.rect()
        size = self._content_size()
        ground_anchor_reserved = INTERIOR_TOP_MARGIN_FOR_SUPPLY_PORT if self._owner_box_has_ground_anchor() else 0.0
        badge_reserved = self._owner_box_macro_power_intent_badge_height()
        self.setPos(
            rect.x() + rect.width() - size.width() - _LABEL_MARGIN,
            rect.y() + rect.height() - size.height() - _LABEL_MARGIN - ground_anchor_reserved - badge_reserved,
        )

    def _owner_box_has_ground_anchor(self) -> bool:
        return any(
            isinstance(child, SupplyPortIconItem) and child.placement.edge == "bottom" for child in self.owner_box.childItems()
        )

    def _owner_box_macro_power_intent_badge_height(self) -> float:
        """Generalizes the SAME live-scene-graph collision-avoidance
        check `_owner_box_has_ground_anchor()` above already establishes,
        to a SECOND bottom-right-corner occupant: a macro's own
        "[UPF]"/"[CPF]" power-intent-attachment badge
        (macro_power_intent_overlay.py), so the two never visually
        overlap when both happen to be present on the same block. Reads
        the badge's own actual rendered height (boundingRect()) rather
        than a second hand-kept-in-sync constant -- same "one formula,
        not two" discipline domain_info_box_required_height() already
        establishes for ITS OWN sizing. `+ _DOMAIN_INFO_PADDING` is a
        small breathing-room gap between the two boxes, same purpose
        `_DOMAIN_INFO_LINE_GAP` already serves between THIS box's own
        internal lines."""
        for child in self.owner_box.childItems():
            if isinstance(child, MacroPowerIntentBadgeItem):
                return child.boundingRect().height() + _DOMAIN_INFO_PADDING
        return 0.0


def build_domain_info_box_overlay(
    boxes: dict[str, InstanceBoxItem],
    domains: dict[str, PowerDomainPlacement],
    supply_sets: dict[str, SupplySetPlacement],
    supply_port_icons: dict[str, SupplyPortIconItem],
) -> dict[str, list[DomainInfoBoxItem]]:
    """Materializes one DomainInfoBoxItem (merged name + Supply Set
    list) per Power Domain PER MEMBER HIERARCHY -- explicit request,
    supersedes build_domain_label_overlay()/build_supply_set_info_box_overlay()
    entirely, same one-item-per-owner_path shape both already
    established, so a multi-hierarchy domain's box is automatically
    kept in sync across every one of its own member blocks: every
    rebuild reads the SAME domain.primary_supply_set/.secondary_supply_sets,
    so there is no separate per-member state that could drift. Unlike
    the now-superseded build_supply_set_info_box_overlay()'s own "no
    Primary -- no box at all" gate, this ALWAYS materializes a box for
    every domain (matching the now-superseded build_domain_label_overlay()'s
    own unconditional guarantee instead) -- domain_info_box_rows()'s own
    "?" fallback covers the no-Primary-yet case, so there's no longer a
    reason to hide the whole box over it."""
    boxes_by_domain: dict[str, list[DomainInfoBoxItem]] = {}
    for domain in domains.values():
        domain_boxes: list[DomainInfoBoxItem] = []
        for owner_path in domain.owner_paths:
            box = boxes.get(owner_path)
            if box is None:
                continue
            domain_boxes.append(DomainInfoBoxItem(domain, box, supply_sets, supply_port_icons))
        if domain_boxes:
            boxes_by_domain[domain.id] = domain_boxes
    return boxes_by_domain
