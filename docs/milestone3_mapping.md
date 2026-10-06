# Milestone 3 — Mapping UPF/CPF ↔ RTL: Engineering Spec

Status: first pass implemented (`backend/mapping/`). `backend/validation/`
(the cross-cutting rules layer that consumes this milestone's output) is a
separate milestone-adjacent piece, not yet started -- see "What this
doesn't do" below.

## Goal

Resolve every reference embedded in an elaborated `PowerIntentDesign`
(Milestone 2) against the elaborated RTL `Design` (Milestone 1), and
against the power-intent design's own declared constructs where a
reference isn't RTL-shaped at all (e.g. a strategy naming a domain). This
is the first point in the project where RTL and power-intent
representations actually meet -- everything upstream of this was
deliberately independent (see `CLAUDE.md`'s milestone-independence rule).

Full research context (the RTL-vs-intra-power-intent field classification
research, the `save_signal`/`restore_signal` UPF/CPF divergence, the
`input_supply`/`output_supply` classification, and the
SupplyPort/SupplyNet-vs-RTL exclusion) lives in
`docs/milestone2_powerintent_elaborator.md`'s "Milestone 3 planning"
section -- not repeated here. This doc covers the resolver architecture
and implementation itself.

## Architecture

`backend/mapping/` is pure resolution -- no rule-checking, no pass/fail
judgment. It answers "what does this reference point at, if anything,"
and hands the full, unfiltered answer set to whatever consumes it next
(`backend/validation/`, eventually the front end). This mirrors the
project's `elaborator/` vs `validation/` split already established for
2a/2b's own "recognized-but-not-cross-checked" boundary.

```
backend/mapping/
  schema.py               -- ResolutionKind, ReferenceLocation, ResolvedReference, MappedDesign
  rtl_resolver.py          -- resolve_rtl_reference(): power-intent path -> RTL Design
  power_intent_resolver.py -- resolve_power_intent_reference(): name -> PowerIntentDesign's own dicts
  map_design.py             -- orchestrator: walks every PowerIntentDesign construct/field,
                                calls the two resolvers above, returns one MappedDesign
```

### `ResolutionKind` — seven-way (RTL) + one (intra-power-intent) + one (not-attempted)

An RTL-hierarchy reference resolves to one of seven kinds:
`INSTANCE`, `PORT`, `NET`, `BLACK_BOX_PORT`, `UNRESOLVABLE_BLACK_BOX`,
`VIRTUAL`, `DANGLING`. An intra-power-intent reference resolves to either
`POWER_INTENT` or `DANGLING` (the same dangling kind, shared across both
resolvers -- a reference either points at something real or it doesn't,
regardless of which resolver looked).

`UNRESOLVABLE_BLACK_BOX` is deliberately distinct from plain `DANGLING`:
a black-box `Definition` (1e `.lib` macro, or an unresolved leaf of a 1d
mixed-language chain) has no internal RTL to check further -- only its
declared `Port`s are checkable. A leaf name that isn't one of those
declared ports might be a real pin the `.lib` file just didn't declare, or
might be a genuine typo; a consumer (validation, or eventually a human)
should be able to tell that apart from "this instance path doesn't exist
at all," which is what plain `DANGLING` means. Verified against real
black-box `Design` data (`tests/backend/golden_lib/verilog_with_sram_blackbox`),
not just asserted in a docstring -- see `test_mapping.py`.

### Two-step path resolution (`rtl_resolver.py`)

A power-intent reference string is combined with the owning construct's
own authoring `scope` (now present on every construct that needs it --
see the six-field schema fix below) into a full, top-relative path, then:

1. Try the whole thing as an **instance path** first (covers e.g.
   `PowerDomain.elements` naming instances directly, no trailing leaf).
2. If that doesn't match, split off the last segment as a **port/net leaf
   name** and re-resolve the remaining prefix as an instance path; check
   the leaf against that instance's resolved `Definition`'s ports (then
   nets).

Implemented via `Design.walk(separator=...)` (parameterized for exactly
this purpose -- see its docstring in
`backend/elaborator/rtl/ir/schema.py`), building a `path -> HierarchyNode`
map once per resolution call and doing an exact string lookup rather than
walking the tree node-by-node per reference.

**The `.` idiom**: UPF's `-elements {.}` means "the whole current scope,"
not a literal instance named `.` -- confirmed real usage, not a guess (see
`tests/backend/golden_upf/upf_basic/top.upf`'s
`create_power_domain PD_TOP -elements {.}`, already asserted against in
`test_upf_elaborate.py`). Handled as a special case in `_combine()`:
resolves to the construct's own `scope` directly, no trailing segment
appended.

**Separator**: UPF paths are `/`-joined with a leading `/` for an
absolute (top-relative) path. CPF paths default to `.`-joined;
`set_hierarchy_separator` (which could override that default) is not
registered by 2b's CPF elaborator at all, so any CPF file attempting to
use it fails elaboration before ever reaching this resolver -- meaning
every CPF design this resolver can actually see uses the default `.`.
Hardcoded on that basis (`_separator()` in `rtl_resolver.py`), with a
tracked note to revisit if/when `set_hierarchy_separator` is wired up in
2b.

**Case folding**: delegates to `normalize_identifier_case()`
(`backend/elaborator/rtl/ir/schema.py`), extracted this milestone from
Milestone 1d's `mixed_language._normalize` for exactly this reuse --
folds to lowercase when the *resolved* leaf `Definition`'s language is
VHDL, matching 1d's already-verified rule (the referencing author doesn't
necessarily know or care about VHDL's case folding, so matching happens
on the target's terms).

**Virtual signals**: `resolve_rtl_reference()` accepts a `virtual_names`
frozenset, checked before any RTL lookup. Always empty in practice today
-- 2a/2b don't yet populate declared-virtual-signal-names (CPF
`set_design -ports`, UPF `create_logic_port`, both logged as follow-up
items in `docs/milestone2_powerintent_elaborator.md`) -- but the resolver's
contract includes this input from day one so wiring that data up later
needs zero changes here.

### Intra-power-intent resolution (`power_intent_resolver.py`)

Simple by-name lookup, symmetric with construction: every construct's own
`id` is `make_power_intent_id(scope, name)` (built at elaboration time),
so resolving a reference is "rebuild the same id from the referencing
construct's own scope and the raw name, check dict membership" -- not a
parallel scheme.

## The six-field schema fix (prerequisite, done first)

Before the resolvers could use each construct's own authoring `scope`,
`scope: str` had to be added to `IsolationStrategy`, `LevelShifterStrategy`,
`RetentionStrategy`, `PowerSwitch`, `PowerState`, and
`DomainSupplyConnection` -- previously only `PowerDomain`/`SupplyPort`/
`SupplyNet`/`SupplySet`/`PowerStateGroup` carried it explicitly (`scope`
was otherwise only baked opaquely into `id`, which
`make_power_intent_id`'s own docstring says must never be parsed back
apart). Wired through `shared/builders.py`'s matching builder dataclasses
and every construction call site in `upf/verbs.py`, `upf/legacy_pst.py`,
and `cpf/verbs.py` (`scope=state.current_scope`). Verified zero-regression
against the full existing suite before starting the resolvers themselves.

## Field classification: what's wired, what's deliberately not

### Wired -- RTL-hierarchy

`PowerDomain.elements`/`.exclude_elements`/`.boundary_ports`,
`IsolationStrategy.elements`/`.exclude_elements`,
`LevelShifterStrategy.elements`/`.exclude_elements`,
`RetentionStrategy.elements`, `PowerSwitch.control_ports`.

### Wired -- intra-power-intent

`IsolationStrategy.domain`/`.source`/`.sink`,
`LevelShifterStrategy.domain`/`.source`/`.sink`, `RetentionStrategy.domain`,
`PowerSwitch.domain`, `PowerState.domain` (all -> `domains`);
`PowerSwitch.input_supply`/`.output_supply` (-> `supply_ports`, falling
back to `supply_nets` -- both are real, spec-confirmed candidate targets
for this specific field, verified this milestone, not a guess);
`DomainSupplyConnection.domain` (-> `domains`), `.supply_net` (->
`supply_nets`).

### Deliberately not wired yet — reported as NOT_ATTEMPTED, not silently absent (`map_design._NOT_YET_WIRED`)

`PowerDomain.primary_supply`, `IsolationStrategy.isolation_supply`,
`RetentionStrategy.retention_supply`, `PowerState.supply`,
`PowerState.applies_to` — each has a real reference shape but no
independently verified target dict (could plausibly be `supply_sets`,
`supply_nets`, or `domains`, and the docstrings for some of these already
flag the ambiguity). `PowerStateGroup.members` also landed here: the one
real data point available (`upf_basic`'s `PSG_TOP -elements {SS_CORE}`,
asserted in `test_upf_elaborate.py`) shows a `SupplySet` name despite
living next to `PowerState` in the schema -- a single fixture's authoring
choice isn't the same as an independently spec-verified target the way
`input_supply`/`save_signal` were checked this round, so it's left
unwired rather than generalized from one example.

**Correction from the first pass**: these six fields were initially just
skipped during `map_design()`'s traversal -- entirely absent from
`MappedDesign.references`, with no way for a consumer to tell "this
construct has no `retention_supply`" apart from "this field was never
checked at all." Caught during review before `backend/validation/` was
designed against that gap (a validation layer built on top of it would
have had no signal that these fields even existed to report on). Fixed:
`map_design()` now emits a real `ResolvedReference` with
`kind=ResolutionKind.NOT_ATTEMPTED` for every one of these fields that has
a non-`None` raw value, carrying the raw value and a `detail` pointing
back here -- no lookup is attempted, but the reference is visible.
`test_not_yet_wired_fields_are_reported_as_not_attempted_not_dropped` in
`test_mapping.py` verifies this against real `upf_basic` data (which
populates several of these fields for real) and also confirms a genuinely
absent field (`PowerState.OFF.supply`, never set in the fixture) produces
no fabricated entry.

None of this blocks Milestone 3's first pass from being useful: every
currently-wired field is real, verified reference data, and resolving the
rest is additive follow-up, not a redesign.

### Out of scope, not a gap (`map_design._OUT_OF_SCOPE`)

Raw expressions (`PowerState.expr`, `RetentionStrategy.save_signal`/
`.restore_signal` — see the CPF/UPF divergence finding in
`docs/milestone2_powerintent_elaborator.md`, `IsolationStrategy.condition`,
`PowerDomain.shutoff_condition`), literal values, not references
(`IsolationStrategy.clamp_value`, `PowerDomain.power_up_state`,
`.default_restore_edge`, `.default_save_edge`, `PowerSwitchState.boolean_expr`),
and the confirmed SupplyPort/SupplyNet-vs-RTL exclusion (`SupplyPort.name`,
`SupplyNet.name`) -- reasoning for each already lives in
`docs/milestone2_powerintent_elaborator.md` and `docs/product_workflow.md`'s
"not signoff-grade verification" section.

## What `backend/mapping/` doesn't do

Same non-goal as stated in `docs/product_workflow.md`: this is structural
reference resolution, not signoff-grade verification. It doesn't parse
expression strings looking for embedded references, doesn't check
direction/width compatibility, and doesn't judge whether a resolved
reference is a *good* one -- just whether it points at something real.
Judging that ("is this a real inconsistency worth surfacing") is
`backend/validation/`'s job, covered next.

## `backend/validation/` — turning resolution results into findings

Status: done (`backend/validation/{schema,validate}.py`). Consumes a
`MappedDesign` and produces a `ValidationReport` -- a **findings list, not
a pass/fail gate**: what severity blocks what (export? GUI save? nothing
at all?) is a decision for whatever consumes the report, not baked in
here, matching `docs/product_workflow.md`'s "not signoff-grade
verification" framing directly. `ValidationReport.has_errors` is offered
as a convenience property, not the primary interface.

### Severity mapping

| `ResolutionKind` | Finding? | Severity | Why |
|---|---|---|---|
| `INSTANCE`/`PORT`/`NET`/`BLACK_BOX_PORT`/`POWER_INTENT` | no | -- | Resolved successfully; a finding per success would be noise. |
| `DANGLING` | yes | `ERROR` | The core structural check this milestone exists for. |
| `UNRESOLVABLE_BLACK_BOX` | yes | `WARNING` | A real resolution failure, but not certain to be wrong the way `DANGLING` is -- might be a real pin the `.lib` file just didn't declare. |
| `VIRTUAL` | no | -- | A successful, expected classification (non-RTL by design), not a problem. |
| `NOT_ATTEMPTED` | yes | `EXCLUDED` | Not a design defect -- a coverage gap in the tool itself. Kept in its own severity tier so it can never look like "this passed" or get silently merged into error counts. |

### `NOT_ATTEMPTED` deduplication

`map_design()` emits one `NOT_ATTEMPTED` reference per instance (e.g. a
design with 50 power states produces 50 `PowerState.supply` references).
Reporting one `EXCLUDED` finding per reference would bury real
`ERROR`/`WARNING` findings under a pure tool-coverage concern.
`validate()` groups `NOT_ATTEMPTED` references by `(construct_kind,
field)` and emits exactly one finding per group, carrying the count (e.g.
*"PowerState.applies_to: 2 reference(s) not checked (...)"*) --
per-instance detail stays available via `MappedDesign.references` for
anyone who wants to drill in; the report itself stays scannable.

### Testing

`tests/backend/test_validation.py` runs the same real-fixture pipeline as
`test_mapping.py` (real `elaborate()` + `map_design()` + `validate()`, not
hand-built `ResolvedReference` lists for most cases) and checks each
severity tier against real data: `upf_basic`'s undeclared `VSS` net for
`ERROR`, `upf_partial_dangling` for "exactly one error for the one bad
list entry, not the whole field," a real `.lib` black-box unresolved-pin
case for `WARNING`, and `upf_basic`'s five real not-yet-wired-field
populations for `EXCLUDED` deduplication (including confirming
`PowerState.supply` and `.applies_to` get different counts, since the
fixture sets them asymmetrically across its two power states).

## Testing

`tests/backend/test_mapping.py` -- exercises the real elaborate() entry
points end to end (Verilog `elaborate()`, UPF `elaborate()`, CPF
`elaborate()`, `.lib` `elaborate_with_black_boxes()`), not hand-built IR
fixtures. `tests/backend/golden_rtl/mapping_smoke/` was authored
specifically to match `golden_upf/upf_basic`'s own instance/port
references, so the UPF-side tests exercise real cross-milestone
resolution rather than a scenario constructed to pass. Black-box
(`golden_lib/verilog_with_sram_blackbox`) and CPF-separator
(`golden_cpf/cpf_hierarchical`) paths are each checked against their own
real fixtures too. `golden_upf/upf_partial_dangling/` covers the common
real-world case of a single list field with a mix of resolving and
genuinely-dangling entries, confirming every entry survives at its
correct list index rather than only the first miss being reported or an
entry being silently dropped.
