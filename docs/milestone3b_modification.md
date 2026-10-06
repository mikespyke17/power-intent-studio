# Milestone 3b — Power Intent Modification: Engineering Spec

Status: done (`backend/modification/{errors,ops}.py`). In-memory
`PowerIntentDesign` modification only — `PowerIntentDesign` in, a new (or
updated) `PowerIntentDesign` out, nothing written to disk. Export is its
own future, milestone-adjacent piece with its own research pass
(`backend/export/`), not folded into 3b — see
`docs/product_workflow.md`'s Export section for the full reasoning behind
that split (this doc previously conflicted with `CLAUDE.md`'s repo-layout
comment on this point; both are now reconciled to agree).

## Locked-in decisions (approved before implementation)

- **Full rebuild, not mutate-in-place** ("Option A" from the pre-3b
  survey). Every operation rebuilds via `dataclasses.replace()` rather
  than mutating a `PowerIntentDesign`'s dict fields in place. Mutation
  ("Option B") was rejected as disqualifying: it would let an
  already-computed `MappedDesign.references` silently go stale while
  `.power_intent` kept looking current and live — the exact "looks fine,
  is actually wrong" bug class this project has repeatedly eliminated
  elsewhere (the unconsumed-flag silent-drop fix, the sync-reset
  misclassification, etc.).
- **Full re-run of `map_design()`+`validate()` after every edit, no
  incremental re-resolution for v1.** Staleness is structural under full
  rebuild — an old `MappedDesign` obviously points at an old
  `PowerIntentDesign` object, nothing to invalidate. `backend/modification/`
  never calls `map_design()`/`validate()` itself; that's the caller's job,
  same as the very first "load a power intent file" step.
- **Rename cascades automatically** across every wired raw-string
  reference (the `MappedDesign` reference graph already has this data).
  **Delete blocks** if anything still references the construct, surfacing
  exactly what's blocking via the real `ResolvedReference`/
  `ReferenceLocation` objects, not just a message string.
- **Scope changes block** (or require explicit re-confirmation of every
  relative-path field) rather than silently reinterpreting `elements`/
  other relative fields under the new scope.
- **One uniform update operation against the canonical IR, format-agnostic**
  — `backend/modification/` doesn't branch on `source_format`. Export's
  translation-back problem (CPF has real `update_X` semantics; UPF has
  none, re-declaring a name is a hard parse-time error) is real but
  deferred to `backend/export/`, not this module's concern.
- **`version_gates.py` is reused at edit time**, not just parse time — see
  its own section below.
- **`docs/product_workflow.md`'s CRUD list reconciled** against the real
  2c schema (added power domains, supply sets, power state groups,
  domain-supply connections; replaced the never-real "port-to-net
  connections" entry with `DomainSupplyConnection`, the actual construct).

## Architecture

Generic dispatch over a construct-kind string (the exact same values
`ReferenceLocation.construct_kind` already uses — "PowerDomain",
"IsolationStrategy", etc.), not ~40 near-identical per-type functions:

```python
add(power_intent, kind, construct) -> PowerIntentDesign
update(power_intent, kind, id, changes: dict) -> PowerIntentDesign
rename(power_intent, kind, id, identity_changes: dict, mapped: MappedDesign) -> PowerIntentDesign
delete(power_intent, kind, id, mapped: MappedDesign) -> PowerIntentDesign
change_scope(power_intent, kind, id, new_scope, confirmed_relative_fields: dict, mapped: MappedDesign) -> PowerIntentDesign
```

Every dict-keyed construct type (`PowerDomain`, `SupplyPort`, `SupplyNet`,
`SupplySet`, `PowerSwitch`, `IsolationStrategy`, `RetentionStrategy`,
`LevelShifterStrategy`, `PowerState`, `PowerStateGroup`) shares this shape.
`DomainSupplyConnection` is the one exception — no `name`/`id`, so it gets
its own `add_domain_supply_connection()`/`remove_domain_supply_connections()`
(remove-by-exact-tuple-match, since there's no stable identity to target
one connection precisely and nothing prevents duplicate appends today).

**Dependencies**: `PowerIntentDesign` (M2) for the data being edited, plus
`MappedDesign`/`ResolvedReference`/`ResolutionKind` (M3) for `rename`/
`delete`/`change_scope`'s cascade-and-block discovery — but only the
`POWER_INTENT`-kind slice of `mapped.references`. No dependency on the RTL
`Design` directly, and this module never calls `map_design()`/`validate()`
itself.

## `update()` structurally rejects identity fields — verified, not conventional

`update()` computes the blocked-key set as `identity_fields(kind,
source_format) | {"scope"}` and intersects it against the caller's
`changes` dict *before* touching anything, raising `IdentityFieldInUpdate`
if the intersection is non-empty. This is an enforced precondition, not a
documented convention a caller could bypass —
`test_update_rejects_name_and_scope_but_allows_plain_fields` calls
`update()` with `{"name": ...}` and `{"scope": ...}` directly and asserts
both raise, alongside a plain field change on the same construct
succeeding.

## A real, non-obvious finding: `PowerState`'s identity is format-dependent

Grounding "what determines a construct's id" against the actual verb
handlers (not assumed from the schema alone) surfaced a genuine
irregularity: UPF's `add_power_state`/legacy-PST paths build the id as
`make_power_intent_id(scope, f"{applies_to}.{state_name}")` (confirmed at
`upf/verbs.py`'s `_add_power_state` and `upf/legacy_pst.py`'s
`_add_pst_state`) — both `name` and `applies_to` are identity-determining.
CPF's `create_power_mode` uses plain `make_power_intent_id(scope, name)`
(`applies_to` is always `None` for a CPF power mode — no single-object
referent, see `PowerState.applies_to`'s own docstring). So `PowerState`'s
identity-field set is keyed off `power_intent.source_format`, not uniform
the way every other construct kind is.
`test_rename_power_state_composite_identity_upf` exercises this for real
against `upf_basic`'s `SS_CORE.ON` state.

**Three follow-up questions this raised, each answered concretely below,
not just documented as a known asymmetry:**

1. **Does `update()` actually branch on format, or is one format silently
   wrong?** It branches for real: `_identity_fields(kind,
   power_intent.source_format)` takes `source_format` as a parameter and
   is called with the real value from whichever `PowerIntentDesign` is
   passed in — verified empirically (not just re-read as code) by calling
   it directly against both a real elaborated `upf_basic` design and a
   real elaborated `cpf_basic` design: `_identity_fields("PowerState",
   SourceFormat.UPF)` returns `("name", "applies_to")`;
   `_identity_fields("PowerState", SourceFormat.CPF)` returns `("name",)`.
2. **Is both halves tested, not just one asserted and the other assumed
   to follow?** `test_update_rejects_applies_to_for_upf_powerstate_but_allows_it_for_cpf`
   proves both directions against real elaborated designs of each format
   in one test: `update()` on a UPF `PowerState` with an `applies_to`
   change raises `IdentityFieldInUpdate`; the identical kind of change on
   a CPF `PowerState` (`cpf_basic`'s `PM1`, confirmed by direct inspection
   to have `applies_to=None`, never set by any CPF verb handler) succeeds
   as an ordinary field update, with the id staying unchanged.
   `test_rename_applies_to_only_valid_as_identity_field_for_upf_not_cpf`
   proves the mirror case on `rename()`'s side: `applies_to` is rejected
   as an unknown identity field for a CPF `PowerState`, while a plain
   `name` rename on the same CPF construct still works normally through
   the non-composite id path.
3. **Does `PowerState` need its own special-cased functions, the way
   `DomainSupplyConnection` got separate `add_domain_supply_connection`/
   `remove_domain_supply_connections`?** No — and the reason is
   structural, not incidental: `DomainSupplyConnection` needed fully
   separate functions because it has no `name`/`id` at all, so it doesn't
   fit the `dict[id] -> construct` shape the generic functions assume.
   `PowerState` *does* fit that shape — it's still `dict[id] ->
   PowerState`, just with an id computed differently per format. Every
   generic function that touches identity (`add`, `update`, `rename`,
   `change_scope`) already routes through `_identity_fields()`/
   `_name_part()` rather than hardcoding `construct.name` directly, so
   the format-awareness lives in those two small shared helpers, not
   scattered per-function. Audited line by line to confirm no function
   bypasses them; `delete()` needs no such handling since it only ever
   looks a construct up by its already-computed `id`, never recomputes
   one.

## A second real finding: scope changes can silently break *other*
constructs' references too, not just the construct's own relative fields

The original pre-3b survey scoped `change_scope`'s risk to the
construct's *own* relative-path fields (`elements`, etc. — decision #5).
Building `change_scope` against the real resolver surfaced a second,
independent failure mode: `power_intent_resolver.py`'s lookup requires the
*referencing* construct's own `scope` to exactly match the *target's*
`scope` (both feed `make_power_intent_id`, and dict lookup needs an exact
key match). So moving a construct's scope doesn't just reinterpret its
own relative fields — it can silently invalidate every *other*
construct's reference to it by name, since those referencers' scopes
won't match the new one.

`change_scope()` was extended beyond the original proposal to account for
this: it now also runs the same blocking-reference lookup `delete()` uses,
and blocks if *anything* currently references the construct — independent
of whether its own relative fields were re-confirmed. Both conditions are
checked and reported together in `ScopeChangeBlocked` (`missing_fields`/
`extra_fields` and `blocking_references` are separate attributes, not
short-circuited on whichever is found first) —
`test_change_scope_blocked_by_missing_relative_fields_and_references_together`
and `test_change_scope_blocked_purely_by_references_when_fields_are_confirmed`
verify each condition can fire independently of the other.

## Cross-scope collision safety — confirmed, not just asserted

Since `resolved_power_intent_id` is always the *full* `make_power_intent_id
(scope, name)` string, two identically-named constructs at different
scopes produce different ids and cannot be conflated by a straight
string-equality filter. `test_same_named_constructs_at_different_scopes_dont_collide`
proves this directly against a real fixture
(`golden_upf/upf_same_name_different_scope/`, two `PD1` domains — one at
top scope, one loaded at `-scope u_core`, with a real `set_isolation
-domain PD1` reference inside the `u_core` scope): deleting the top-level
`PD1` is not blocked by the `u_core`-scoped isolation strategy's
reference, deleting the `u_core`-scoped `PD1` is, and renaming the
top-level `PD1` leaves the `u_core`-scoped isolation strategy's `domain`
field untouched.

## `version_gates.py` reuse at edit time

`add()`/`update()` call the exact same `check_location_value()` 2a calls
at parse time — not a reimplementation — whenever `power_intent.
source_format == UPF` and the construct kind is `IsolationStrategy`/
`LevelShifterStrategy` and `location` is among the resulting field values.
This is `version_gates.py`'s second real consumer (logged in
`docs/milestone2_powerintent_elaborator.md`); the "UPF-only, not worth a
shared per-format abstraction" conclusion there is unchanged.
`test_update_reuses_version_gate_for_location`/
`test_add_reuses_version_gate_for_location` confirm a design that
declared UPF 2.1 rejects `-location fanout` through `update()`/`add()` the
same way parsing a real file would.

## Inherited gap: rename-cascade and delete-block are blind to `NOT_ATTEMPTED` fields

**This is the single most important caveat for anyone trusting `rename`/
`delete`/`change_scope`'s safety on this module — called out here as its
own section, not folded into a footnote.**

`rename`, `delete`, and `change_scope` all discover what references a
construct by filtering `mapped.references` to `kind ==
ResolutionKind.POWER_INTENT`. But six fields never reach `POWER_INTENT`
at all — `PowerDomain.primary_supply`, `IsolationStrategy.
isolation_supply`, `RetentionStrategy.retention_supply`, `PowerState.
supply`/`.applies_to`, and `PowerStateGroup.members` are tagged
`NOT_ATTEMPTED` by `map_design()` (see `docs/milestone3_mapping.md`)
because their target dict was never independently verified, the same
"logged, not guessed at" discipline as `input_supply`/`save_signal`.

**Concretely**: if a `RetentionStrategy.retention_supply` field names a
`SupplySet` by raw string, and that `SupplySet` is deleted, `delete()`
will **not** block the delete — the reference is invisible to the
blocking-lookup mechanism, exactly because it was never resolved in the
first place. The same `SupplySet` being renamed will **not** have that
reference cascaded either; it silently goes stale, indistinguishable from
a genuinely dangling reference the next time `validate()` runs (it would
surface as a fresh `DANGLING` `ERROR` finding at that point — not silent
forever, just silent *at the moment of the edit that caused it*, which is
exactly when a user would most want to know).

**No fix attempted here.** This isn't new scope for 3b to silently absorb
— it's the same open item from `docs/milestone3_mapping.md`'s "Deliberately
not wired yet" section, now shown to have a second real consequence
(mapping resolution *and* modification safety both depend on the same
unresolved target-dict question). Resolving it requires the same
independent-verification pass `input_supply`/`save_signal` already got for
each of the five (now six, counting `PowerStateGroup.members` separately)
fields — tracked there, not duplicated as a second open item here.

## Testing

`tests/backend/test_modification.py` — 18 tests, all against real
elaborated fixtures (`golden_upf/upf_basic` + `golden_rtl/mapping_smoke`,
`golden_cpf/cpf_basic`, plus two new fixtures purpose-built for this
milestone: `golden_upf/upf_same_name_different_scope/` for the
cross-scope collision test, `golden_upf/upf_v21_for_modification/` for
the version-gate-reuse tests). Covers: identity-field rejection, full
cross-reference-kind rename cascade (isolation/level-shifter/retention/
switch/power-state/domain-supply-connection, all in one real design),
rename-to-existing-name rejection, `PowerState`'s composite identity for
UPF and its *absence* of composite identity for CPF (both directions,
against both formats' real elaborated output, on both `update()`'s
rejection and `rename()`'s whitelist), delete-blocked carrying every real
referencer with correct counts, delete succeeding when genuinely
unreferenced, cross-scope collision safety, both `change_scope` blocking
conditions independently, `add()`'s id-matches-fields guard, and
`version_gates.py` reuse at both `add()` and `update()` time.
