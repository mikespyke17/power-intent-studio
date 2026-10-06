# Front-End Milestone 2 — Power Intent Visualization: Engineering Spec

Status: done, visualization only (`frontend/src/power_intent_view/`,
`frontend/src/shared/diagram/`). This is the GUI's first per-milestone
engineering doc -- the front end was scaffolded (`frontend/src/
{hierarchy_view,power_intent_view,diff_view,chatbox,shared}/`) without a
concrete tech choice behind it until this pass; PySide6 was chosen for
its LGPL license, consistent with how GPL exposure has been tracked
elsewhere in this project (GHDL, liberty-parser).

Interactive editing (right-click add domain, click a port to add an
isolation rule, etc.) is explicitly **out of scope** for this pass --
deferred to a dedicated follow-up pass once this visualization layer is
reviewed and solid. Every properties table here is read-only.

## Hard constraint: mock/fixture data only -- for this milestone's own scope

Per `CLAUDE.md`'s milestone-independence rule, this visualization layer
itself must be buildable and demoable with zero dependency on the real
back end, and is: every widget in `frontend/src/power_intent_view/` and
`frontend/src/shared/diagram/` is built and tested entirely against mock
data, with zero backend import anywhere in that code (see "Mirrored data
layer" below for why that's a real, load-bearing decision, not an
oversight).

**Documentation note, added after this doc's own initial writing**: the
blanket "nothing in `frontend/` imports from `backend/`" statement is no
longer literally true of `frontend/src/` as a whole -- there is now one
sanctioned exception, `frontend/src/backend_integration/` (see
`docs/backend_integration.md`), which is the real-backend wiring the
app's own executable entry point (`power_intent_studio.py`) uses by
default. This milestone's own visualization code does not use it and
stays exactly as independent as described above; only the *app-wide*
claim needed updating. Checked *structurally*, not just by convention:
`test_no_backend_imports_outside_backend_integration`
(`tests/frontend/test_mock_backend.py`) parses every `.py` file under
`frontend/src/` EXCEPT that one directory and asserts no import
statement's module path starts with `backend`.

The "Elaborate"/"Load Power Intent"/"Mapping" buttons, when constructed
with no action parameters (`MainWindow()`'s own bare default -- what
this milestone's own test suite, and the front-end test suite generally,
still builds against), all run on a `QTimer`-driven fake progress bar (no
`QThread` -- there's no real work happening, so threading would add
complexity for zero benefit) and produce literal, hand-built canned data
from `shared/mock_backend/`, not derived from anything the buttons' own
inputs (file pickers, top-module field) actually contain.

## Mirrored data layer (`shared/models/`)

Every type mirrors a real backend dataclass shape field-for-field, as a
genuine duplicate -- importing the real backend type would make the
GUI's importability depend on the back end's (GHDL, pyslang, ...),
defeating the zero-dependency requirement. The goal is that
`backend/api/` (Final Milestone) has an already-exercised target shape to
produce, not a redesign of this module.

- `rtl.py` mirrors `backend/elaborator/rtl/ir/schema.py`, including a
  real `Design.walk()` (same traversal pattern, not just the same field
  shape).
- `power_intent.py` mirrors `backend/elaborator/power_intent/ir/schema.py`.
- `mapping.py` mirrors `backend/mapping/schema.py`.
- `validation.py` mirrors `backend/validation/schema.py`.
- `diagnostics.py` -- **not** a mirror of any single real backend type
  (see next section).

`rtl.SourceLoc` and `power_intent.SourceLoc` are two distinct types with
the same name, deliberately not both exported from `shared/models/
__init__.py` (a real name collision) -- callers needing one must import
from the specific submodule, which reinforces rather than hides that
they're genuinely different shapes.

## The diagnostics data model: a three-way asymmetry, not two

The dock's uniform table (Severity, Code, Message, Line, Column) can't
mirror any single real backend type, because none unifies RTL/UPF/CPF/
mapping diagnostics today. Checking all three real sources directly (not
assumed) surfaced a three-way split, not the two-way UPF-vs-RTL split
originally flagged:

| Source | Real backend shape | Line | Column |
|---|---|---|---|
| RTL elaboration | none -- `elaborate()` raises `ValueError`, no diagnostic list | present | sometimes present |
| UPF/CPF elaboration | none -- same | sometimes present | **never a field at all** |
| Mapping/validation | real: `ValidationReport.findings: list[Finding]` | **doesn't exist** -- `ResolvedReference` carries no `SourceLoc`, only `ReferenceLocation` (construct/field, not a file position) | **doesn't exist** |

`Diagnostic` (`shared/models/diagnostics.py`) is a deliberate
presentation/adapter type, not a mirror. Since Mapping is the one path
with a real structured type, the mocked Mapping action builds a mock
`ValidationReport` first, then runs it through a real
`finding_to_diagnostic()` adapter (`shared/mock_backend/mock_mapping.py`)
-- at the Final Milestone, only the *source* of the `ValidationReport`
changes, not the adapter. `code` has no backend equivalent anywhere (not
on `Finding`, not on `ResolutionKind`) -- a small UI-invented scheme
(`MAP-ERR-DANGLING`, `RTL-UNUSED-NET`, etc.), documented as invented, not
mirrored.

Mock data deliberately exercises all three line/column combinations on
every run (`build_mock_rtl_diagnostics`, `build_mock_power_intent_diagnostics`,
`finding_to_diagnostic`), not just the all-populated case --
`test_rtl_diagnostics_have_a_mix_of_column_present_and_absent` and
`test_power_intent_diagnostics_never_have_a_column` in
`tests/frontend/test_mock_backend.py` check this directly.

## Shared diagram builder -- "not a separate rendering" as actual code reuse

`shared/diagram/hierarchy_scene_builder.py`'s `build_hierarchy_scene()`
is the *only* place base hierarchy layout happens (instance boxes + port
anchors, no power-intent awareness). Both the Visualize Design tab
(`hierarchy_view/hierarchy_diagram_view.py`) and the Power Intent tab
(`power_intent_view/power_intent_diagram_view.py`) call this same
function; the latter layers the power-intent overlay on top of the
returned scene. "The same diagram, not a separate rendering" means
literal code reuse, not two independently maintained layouts that happen
to look similar.

`InstanceBoxItem.port_anchor()`/`port_anchor_local()` give overlay
badges a stable, parent-relative attachment point -- the one piece of
the base hierarchy diagram's design this pass reached back into, a
natural data dependency (M2 visualizing on top of M1's layout), not
scope creep.

## Rendering per construct kind (`power_intent_view/diagram_overlay.py`)

- **Power domains → translucent regions.** `DomainRegionItem`: bounding
  rect is the union of every resolved member box's `sceneBoundingRect()`,
  padded, `Z=0` (behind instance boxes at `Z=1`, members stay clickable
  on top). Color from a small fixed palette, indexed deterministically by
  sorted domain id (stable across re-renders).
- **Power switches → icon + connectors.** `SwitchIconItem` at the
  domain's region top-center; supply nets/ports (no hierarchy position of
  their own) get a `SupplyRailItem` marker in a horizontal strip above
  the diagram, one per distinct supply construct actually referenced by
  a switch; `ConnectorLineItem` lines from icon to marker.
- **Isolation / level-shifter → pin-level badges.** `IsolationBadgeItem`
  (orange triangle) / `LevelShifterBadgeItem` (purple diamond),
  `setParentItem()`'d onto the owning instance box at that port's local
  anchor offset -- parent-relative, tracks the box automatically. Fanned
  out with a small offset if multiple badges land on the same pin.
- **Retention → instance-level badge.** `RetentionBadgeItem` (blue
  circle), parented to the instance box directly (fixed corner offset),
  not a port anchor.
- **Power states/PST → not drawn**, confirmed. Tabular only, in the
  properties panel.

### Empty-domain fallback, tested explicitly

A domain whose `-elements` resolve to zero real members (every reference
`DANGLING`, e.g. `mock_power_intent.py`'s own `PD_EMPTY`, included in the
canned scenario as real demo data, not just a test-only construction) gets
**no `DomainRegionItem` at all** -- not a zero-size or degenerate rect.
The domain still gets a selection-index entry, `[]`, so it stays fully
selectable from the properties panel; only the diagram side has nothing
to show. The same principle extends to `PowerSwitch`: a switch whose
domain has no renderable region has no boundary to sit at, so it gets no
icon either.

Tested directly in `tests/frontend/test_diagram_overlay.py` against a
minimal, purpose-built `MappedDesign` (not the full canned scenario, for
the clearest possible proof): `test_domain_with_zero_resolved_members_renders_no_region_item`
confirms zero `DomainRegionItem`s and zero stray items added to the
scene; `test_domain_with_zero_resolved_members_still_has_a_selection_index_entry`
confirms the `[]`-not-missing distinction; `test_domain_with_zero_resolved_members_leaves_no_switch_icon_either`
extends the same check to a switch.

## Properties panel: three constructs folded into their nearest relative tab

The originally-specified seven tabs (Power Domains, Supply Ports/Nets,
Switches, Isolation, Retention, Level Shifters, Power States) don't cover
`SupplySet`, `PowerStateGroup`, or `DomainSupplyConnection` -- three real
2c constructs, the same three found missing from `product_workflow.md`'s
CRUD list during Milestone 3b planning. Folded into their closest
relative tab rather than either dropped or given three new top-level tabs:
`SupplySet` rows appear in Supply Ports/Nets (a `Kind` column
distinguishes Port/Net/Set); `DomainSupplyConnection` as a secondary
table within Power Domains; `PowerStateGroup` within Power States.

## The `MappedDesign` dependency and the pre-Mapping placeholder

Domain regions need to know *which instance boxes* belong to a domain --
that requires resolving `PowerDomain.elements`' raw strings, which is
`backend/mapping/`'s job (mirrored as `MappedDesign`). The overlay
builder deliberately does not re-implement any resolution logic itself;
it only consumes already-resolved `ResolvedReference`s, the same
discipline `mock_mapping.py`'s own docstring states for why its
`MappedDesign` is hand-authored rather than computed by a fake resolver.

Consequence: the Power Intent tab is enabled once "Load Power Intent"
succeeds (there's a `PowerIntentDesign` to show properties for), but the
diagram's overlay specifically needs Mapping to have run at least once.
`PowerIntentDiagramView` is a `QStackedWidget` between a placeholder label
("Run Mapping to see power intent overlaid on the hierarchy.") and the
real `QGraphicsView` -- `set_hierarchy_only()` (called right after Load
Power Intent) shows the bare hierarchy with the placeholder visible;
`set_mapped_design()` (called after Mapping) swaps to the full overlay.

Tested explicitly in `tests/frontend/test_power_intent_tab.py`:
`test_diagram_shows_placeholder_before_mapping_has_run` confirms the
placeholder is showing, the overlay index is empty, and the *bare*
hierarchy is still real (not blank) after `set_rtl()` alone;
`test_diagram_switches_to_full_overlay_once_mapping_completes` confirms
the transition; `test_properties_panel_is_populated_independent_of_mapping_state`
confirms properties come from `PowerIntentDesign` alone and don't require
`MappedDesign` at all, proving the two data sources are genuinely
independent inputs to the tab, not one gating the other.

## Bidirectional selection sync

`PowerIntentSelectionController` (`power_intent_view/selection_controller.py`)
is a hub-and-spoke `QObject` with one signal, `selectionChanged(kind, id)`.
Exactly one thing may ever call `.emit()`: a widget's own user-interaction
handler (a diagram click via `QGraphicsScene.selectionChanged`, a
properties-table row click). `setSelection(kind, id)` on every subscriber
-- including the widget that originated the emit -- is a pure visual
update that never re-emits; this is what prevents the infinite-loop bug
a naive widget-to-widget wiring would have. `hierarchy_view/` has the
identical pattern (`HierarchySelectionController`) for the Visualize
Design tab's own tree/diagram/properties sync, carrying a hierarchy path
instead of a `(kind, id)` pair.

A construct can map to *multiple* diagram items (a domain's region and
all its member boxes; a strategy's several port badges); the diagram
widget keeps a `dict[(kind, id), list[item]]` index built at scene
construction time so `setSelection()` can highlight (or clear) every item
at once.

**Two real implementation bugs found and fixed while writing the sync
tests, not while writing the sync code itself** -- worth recording since
this is exactly the value of running the tests, not just designing
around the interaction:

1. `ConnectorLineItem` (a switch's line to a supply-rail marker) is a
   plain `QGraphicsLineItem`, and `setSelection()` calls `.set_highlighted()`
   on every item in a construct's index entry unconditionally -- a bare
   `QGraphicsLineItem` has no such method. Fixed by giving connector
   lines their own `set_highlighted()` (pen swap), matching the interface
   `SelectableItemMixin` items already have, without making them
   independently selectable (a connector line represents no construct of
   its own).
2. Selecting a `PowerDomain` highlights its region *and* every member
   `InstanceBoxItem` (per the approved design), but `InstanceBoxItem`
   (defined in the power-intent-unaware `shared/diagram/` module) had no
   `set_highlighted()` at all. Fixed by adding it there, duck-typed to
   match `SelectableItemMixin`'s interface rather than sharing it via
   inheritance -- `shared/diagram/` still has zero power-intent awareness.

`test_no_infinite_loop_between_diagram_and_properties` in
`tests/frontend/test_selection_sync.py` asserts the emission count
directly (exactly one per triggering click, not a cascade) rather than
just checking the end state, which wouldn't distinguish "worked in one
hop" from "looped and happened to converge."

## Scope boundary drawn, not yet revisited

Clicking a plain instance box (not a power-intent item) in the Power
Intent tab's diagram is inert -- not wired to cross-navigate back to the
Hierarchy Tree box in that same tab. That cross-concern was judged to be
creeping past "visualize power intent" into general diagram-interaction
scope; deferred, not silently dropped.

## Testing

`tests/frontend/` -- 29 tests, `pytest-qt` based, forced onto the
offscreen Qt platform plugin via `tests/frontend/conftest.py` (headless,
no real display needed). `test_diagram_overlay.py` (empty-domain
fallback, badge placement), `test_power_intent_tab.py` (pre/post-Mapping
placeholder state), `test_selection_sync.py` (bidirectional sync, the
no-loop guarantee), `test_mock_backend.py` (mock data correctness, the
structural no-backend-import check), `test_diagnostics_dock.py`
(phase-based filtering, replace-not-accumulate, blank-cell rendering),
`test_main_window.py` (full app wiring end to end, driven through the
real mocked-progress buttons rather than calling internal `_finish_*`
methods directly).
