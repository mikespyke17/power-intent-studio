# Load Power Intent — mapping an existing UPF/CPF into the diagram

Design writeup for the "Load Power Intent" mapping feature: importing an
externally-authored (or previously-exported) UPF/CPF file and turning it
into real, editable Power Intent diagram state.

Built on branch `feature/load-power-intent-mapping`, 2026-09-12, in six
phases (one commit each): the supported-features list, the elaborator
extensions needed to read this app's own dialect, the colour assignment
and ERROR/WARNING classifiers, the GUI wiring, the UPF round-trip suite,
and the CPF round trip.

> **2026-10-06 — UPF only.** CPF mapping has been removed from Load Power
> Intent (a `.cpf` file is refused with a `CPF-NOT-SUPPORTED` error before
> the elaborator runs). The CPF material below is kept as history; CPF
> *generation* is unaffected. See "UPF -> design mapping on aura_top" at
> the end of this document for what changed.

This is explicitly **not** a from-scratch parser project. Parsing
(Milestone 2), reference resolution and validation (Milestone 3) already
existed. What did not exist is the step from the elaborated IR — a flat,
format-agnostic set of constructs — to the far richer GUI model, which
has concepts the IR has no notion of at all: Internal vs. Virtual
Supply, Supply Sets shared across hierarchies, diagram colours and
placement edges, authored vs. inherited power states. That translation
layer is the new work, and it lives in
`frontend/src/power_intent_view/power_intent_import.py`.

---

## Where the pieces live

| File | Role |
| --- | --- |
| `frontend/src/power_intent_view/supported_features.py` | The inspectable "what do we model" list. Drives the WARNING classification. |
| `scripts/show_supported_features.py` | Prints that list as a readable summary. |
| `frontend/src/power_intent_view/power_intent_import.py` | The translation layer: IR → the eight diagram-only state families. |
| `frontend/src/power_intent_view/import_palette.py` | Colour assignment (section 6). |
| `frontend/src/power_intent_view/load_power_intent_tab.py` | Layout parity with Load Design, the compatibility note, the import phase, the acknowledgment dialog, the dirty-flag gate. |
| `frontend/src/power_intent_view/power_intent_tab.py` | `apply_imported_power_intent()` and the dirty flag. |
| `tests/frontend/test_power_intent_round_trip.py` | Section 8, the feature's definition of done. |
| `tests/frontend/test_power_intent_import.py` | The non-round-trip regression paths. |
| `tests/frontend/test_load_tabs_layout_parity.py` | The layout-parity requirement. |

---

## Precondition: RTL first

Every scope, element and port reference resolves against the **already
elaborated** RTL design. The importer never infers hierarchy of its own.
This was already enforced by tab enablement — `main_window.py` enables
"Load Power Intent" only from `_on_design_loaded()` — and that was
confirmed rather than re-implemented.

`rtl_top_path()` reads the top instance path from
`rtl.definitions[rtl.top].base_name`, **never** from `rtl.top` itself,
which is a definition ID carrying a parameter hash. Confusing the two is
a bug this codebase has already hit once, in the connectivity resolver.

---

## The supported-features list is a deliverable, not an implementation detail

`supported_features.py` classifies **every field of every power-intent IR
construct** as MODELLED or UNMODELLED, and an UNMODELLED field carries a
one-line reason. 75 modelled, 25 unmodelled at the time of writing.

Two decisions worth recording:

- **Classified per IR FIELD, not per Tcl command.** A command is not the
  unit of support: `set_isolation` is modelled, but its `-source`/`-sink`
  domain filtering is not. Per-field is the granularity at which the
  answer is actually true, and it is also the granularity the importer
  can mechanically check itself against.
- **Zero backend imports.** The list names backend construct/field names
  as plain strings. Importing the backend schema to enumerate them would
  couple the front end to backend internals for a list whose whole
  purpose is to be human-reviewable.

`tests/frontend/test_supported_features.py` asserts the list stays
complete against the frontend mirror (so a newly added field cannot go
unclassified) and that every UNMODELLED reason is a real explanation
rather than a restatement — that test caught five thin reasons ("same as
source", "see name") during the build, which were expanded rather than
the test weakened.

---

## Three outcomes, never two

Section 4 is explicit that "didn't resolve" and "resolved but we don't
model it" are different failures and must not be conflated.

| Outcome | Severity | Code | Source |
| --- | --- | --- | --- |
| Reference doesn't resolve against the RTL | ERROR | `POWER-INTENT-IMPORT-UNRESOLVED` | reuses the backend's `DANGLING` |
| Resolved, but this GUI doesn't model it | WARNING | `POWER-INTENT-IMPORT-UNRECOGNIZED` | **new**, driven by `supported_features.py` |
| Resolved and modelled | — | — | becomes diagram state |

The WARNING classification is deliberately separate from the backend's
existing `NOT_ATTEMPTED` and `UNRESOLVABLE_BLACK_BOX` warnings, which
mean different things (see `backend/mapping/schema.py`). It is added
alongside them, never folded in.

Nothing is silently dropped. A construct this GUI cannot represent still
produces a diagnostic naming the field and the reason — which is exactly
why `supported_features.py` records a reason per unmodelled field.

Import diagnostics go to the dock under their **own** source key,
`"power_intent_import"` (added to `DiagnosticsDock.TAB_SOURCES`).
`DiagnosticsTableModel` replaces per source, so sharing the load phase's
key would have made each batch silently erase the other's.

### Only what the file actually authored is reported

`_is_authored()` compares a field against its **dataclass default**, not
against emptiness. An earlier version compared to falsiness and produced
four false warnings, because an enum-typed field is never empty. A
field the file never set says nothing about that file and is not worth a
warning.

### One fact, reported once, by the layer that understands it

Two filters keep the diagnostics honest rather than merely numerous:

- `ImportedPowerIntent.reported_references` — a builder that already
  reported a missing reference in its own, more specific terms (which
  path it looked for, and what the import did about it) suppresses the
  generic backend sweep for the same `(construct, reference)` pair. The
  mock fixture's single missing element was surfacing as two errors.
- `_BUILDER_OWNED_FIELDS` — twelve `(construct, field)` pairs where the
  generic mapper resolves the reference as an RTL hierarchy path relative
  to the construct's own scope, which is **not what the reference
  means**. Each entry carries its reason. Every one was measured by
  round-tripping real generated output, not added as a precaution:

  - `set_isolation`/`set_level_shifter -elements` are ports **of the
    referenced domain**, resolved relative to that domain, not to the
    strategy's own scope. Confirmed against the hand-written
    `examples/upf_example_1_sram/upf/golden.upf`, where a root-scope
    `set_isolation -domain PD_SUB -elements {o_q[31:0]}` names a bare
    port of a domain one level down.
  - `set_retention -elements` are sequential elements (registers). The
    RTL resolver resolves only instances, ports and nets, so a register
    can never resolve through it — by construction, not by accident.
  - A switch's input/output supply is a supply **net**, looked up by
    name; its `-control_port` is a switch cell **pin** name in this
    app's dialect (the GUI has no net for it).
  - Every strategy's own domain link, which the builders resolve by name
    against the domains they actually imported — including CPF's `-from`,
    since `create_isolation_rule` has no `-domain` flag at all.

An **absent** reference is also not a broken one:
`connect_supply_net -ports` legitimately has no domain, and the mapper
resolves that empty string rather than skipping it.

---

## Hierarchical UPF/CPF: flatten, except onto a macro

The critical distinction. `load_upf -instance <x>` means two different
things depending on what `x` actually is, and only the elaborated RTL can
say which:

- **`x` is a soft (RTL) hierarchy** → the file's constructs belong to a
  nested block of *this* design and are flattened in. This needs no
  second pass at all: the elaborator already sources the sub-file inline
  under the named scope, so the constructs simply arrive carrying that
  scope, and `resolve_owner_path()` turns it into a hierarchy path like
  any other.
- **`x` is a macro (`.lib` black box)** → this is vendor power intent for
  that macro, which the app already models as an **attachment** (see
  `macro_power_intent_overlay.py`). It must not be flattened: a macro is
  a black box, and its internal domains and supplies are not part of the
  parent design's diagram.

`scoped_to_macros()` finds the scopes that resolve to a black-box
instance; `_without_macro_scopes()` filters them out **before** any
builder runs, so each builder stays simple and no builder can forget the
rule; `macro_attachments()` records each one against the file its own
constructs came from (their `source_loc`), not the entry file.

The elaborator cannot make this call — it has no RTL to check the
instance against, which is exactly why it accepts `-instance` as a plain
scope alias and leaves the decision here.

---

## Colour assignment

An imported file carries no colours, so the import has to invent them,
with one hard constraint: every Power Domain must end up visually
distinguishable from every other.

**This assigns SUPPLY colours, not domain colours.** A domain's colour is
not its own property in this app — it is derived live from its primary
supply set's power rail (`resolve_domain_color()`). Colouring domains
directly would be overwritten on the next refresh, so distinctness has to
be engineered one level down.

The existing six-hue palette (`diagram_overlay._DOMAIN_PALETTE`) is
**reused, not replaced** — section 6 asked to check for one first. That
palette wraps with `i % len(palette)`, which is precisely the collision
this import must avoid, so the six hues keep their original order (a
two-domain design gets the same familiar colours it always had) and
generation continues procedurally past six.

Beyond six, hues are proposed at the golden angle (~137.5°) rather than
an even 360/N: even spacing needs N up front and re-colours everything
when N changes, while the golden angle keeps every prefix well-separated
no matter where it stops.

**Proposing was not enough, and this was measured rather than assumed.** A
pure golden-angle walk collided with the fixed palette — the 8-domain case
put two colours 35.7 RGB units apart and 20 domains got down to 28.1,
both under the 40 this module calls distinct — and adding lightness bands
did not fix it either. A candidate is now only **accepted** if it clears
the threshold against every colour already assigned, including the fixed
six, so the requirement holds by construction. Verified exact to 32
domains (closest pair 40.0); at 50 it degrades to 38.4, which is the
saturation fallback doing its job. The largest fixture in this project
has two.

A ground rail is conventionally dark and is not part of the distinctness
requirement (domains are told apart by their power rail), so every ground
shares one near-black.

---

## Re-loading, and the dirty flag

"Load Power Intent" now **stays enabled** after a successful mapping so
another file can be loaded later — a deliberate exception to this
window's otherwise one-shot navigation, and the one place
`_on_load_power_intent_continue()` no longer disables its own tab.

What protects the edits a re-load would discard is a plain dirty flag on
`PowerIntentTab`, driven by the **same** `diagramStateChanged` signal the
Properties refreshes already use — "the diagram state changed" is exactly
the question, so there is no separate notion of dirtiness to keep in sync
with the real one. It is reset by whatever replaces the state wholesale
(a project load, an import), never by an edit.

`LoadPowerIntentTab` reads it through a `dirty_check` callable rather than
a reference to the tab, so it needs no knowledge of `PowerIntentTab`;
`main_window.py` wires the two.

Section 7 is explicit that a re-load is a **full replace**, not a merge,
so the confirmation is a single Yes/No defaulting to No. No diff, no
incremental merge logic. `apply_imported_power_intent()` delegates
straight to `load_project()`: "a complete set of diagram states arrives
from outside and replaces everything" is the identical operation, and an
import that restored the eight families itself would be a second place to
keep in sync with `restore_all_shared_states()`.

## The acknowledgment dialog

`continue_button` is only ever enabled when the run produced a warning or
error (the pre-existing "stay until Continue" gate). Reaching it therefore
means the user is choosing to proceed past findings. Section 4 asks for an
explicit acknowledgment at exactly that moment: a `message_box.question`
(the app's own confirmation convention, defaulting to No) naming the
actual error and warning counts, so the acknowledgment is about something
specific rather than a generic "are you sure".

The counts come from the batch `_finish_success()` was given, not read
back out of the dock — it is the same list that decided whether the
button was enabled at all, so the message can never disagree with the
gate.

A failure inside the translation is reported as a diagnostic, not raised:
mapping has already succeeded and been emitted by that point, so a
translation bug must not take the whole run down with it.

---

## Layout parity with Load Design

A pure layout/organisation change, no feature reduction. "Load Power
Intent" is now the same two-pane `QSplitter` Load Design uses — a staging
pane whose picks are committed via "Add", and a "Loaded" pane the action
button reads — with the compatibility note above the splitter (where Load
Design puts its own informational content) and the diagnostics dock at
the bottom (where Load Design puts its own).

Every pre-existing control is still present and wired: the UPF/CPF file
picker, "Remove Selected", "Mapping", "Create New", the progress bar,
"Continue", and the diagnostics dock.

`files_list` deliberately **keeps its name and its meaning** (the LOADED
list, what Mapping reads) rather than being renamed to match the new pane
it sits in: every existing caller and test that seeds it directly stays
correct, and it is the genuine analogue of `LoadDesignTab.loaded_list`.

`tests/frontend/test_load_tabs_layout_parity.py` asserts both halves —
that the two tabs share the same widget hierarchy, and that every
Load-Power-Intent-specific control is still there and connected.

---

## Section 8: round-trip fidelity

The feature's real definition of done. For every construct type this app
can generate, generating it and immediately re-loading the generated file
must produce zero errors, zero warnings, and equivalent internal state.

`tests/frontend/test_power_intent_round_trip.py` runs that against the
**real** elaborator. It is the only file under `tests/frontend/` besides
`test_real_backend_integration.py` that reaches the backend, and for the
same reason: the whole question is whether the elaborator reads what the
generator writes, so mock data cannot answer it. It reaches the backend
only through `backend.api` via `frontend/src/backend_integration/`, never
into backend internals. Tests are parametrized per construct so a
regression names the construct rather than failing one opaque assertion.

"Equivalent" is not "identical": placement ids are freshly generated,
colours are invented, and a domain's colour is derived later from its
supply set. Equivalence is asserted on the fields carrying the user's
actual intent — names, scopes, which supply set a domain points at, which
elements a strategy covers, a switch's pin names and states — normalised
by the suite's own `snapshot()`. A field that genuinely cannot survive is
asserted as a **named, explained exception**, never quietly excluded.

### UPF: lossless, with one named exception

Every construct round-trips. The single exception, asserted as its own
test: an **unconnected** switch input pin name. UPF writes the input pin
as `-input_supply_port {pin net}` — a pin paired with the net feeding it
— so a switch whose Supply In was never wired has no legal UPF
representation for the pin name alone. Connected, it round-trips exactly.

### What the round trip found

Ten real defects, none of which had a test before. This is the list
because "the suite passes" is much less informative than what it caught:

**UPF generator**

1. The version appeared only in the commented header banner, so a
   generated file never actually declared it and re-reading failed with
   "called before any version declaration".
2. Supply nets were deduped by name alone and all declared at the root. A
   UPF net belongs to its current scope, so a nested placement's net was
   silently relocated to the top level and the nesting could not be
   recovered. Now grouped by `(scope, name)` and wrapped in `set_scope`.
3. `create_supply_set` had the identical bug, found the same way.

**UPF importer**

4. The switch's Supply In connector was not rebuilt. Because the pin name
   is only representable when there is a net to pair it with, the name
   survived one trip and was lost on the next.

**The DANGLING sweep** — six fields where the generic mapper was
confidently wrong (see `_BUILDER_OWNED_FIELDS` above), plus empty
references treated as broken ones.

**CPF generator**

5. `create_state_retention_rule` used a bare positional where CPF names
   every rule with `-name` — the lone positional in the whole writer.
6. ...and `-pins` where a retention rule takes `-instances`. `-pins`
   belongs to the adjacent isolation section and had been carried across.
7. `create_level_shifter_rule` dropped `-from <domain>` and
   `update_level_shifter_rules` dropped `-location`, surfacing both only
   in a comment — so a generated rule came back attached to no domain.
   `golden.cpf` has both and the adjacent isolation section already
   emitted `-from`, so this was an inconsistency rather than the
   deliberate "no confident CPF equivalent" omission it resembled.

**CPF elaborator**

8. `update_power_switch_rule` and `create_nominal_condition` were not
   registered at all, so Tcl rejected the command **name** itself. Both
   appear in `golden.cpf`; both were logged as deferred 2b gaps.
9. `update_power_domain` accepted only
   `-internal_power_net`/`-internal_ground_net`, rejecting the
   `-primary_*` spelling `golden.cpf` uses. Per 2b's own recorded caveat
   the `-internal_*` pair is the narrower (possibly post-switch) concept,
   so accepting only it was the gap — not the generator inventing a flag.
10. `update_isolation_rules` recognized neither `-isolation_power_net`
    nor `-isolation_ground_net`, both in `golden.cpf`. And
    `set_instance .` stored the literal `"."` as a scope where UPF's own
    `set_scope` already normalised it to the root, so constructs written
    after it stopped matching by-name references from the real top scope.

### CPF: parses and imports cleanly, but cannot round-trip losslessly

This is a structural property of the format, not a gap to close, and the
tests state it explicitly rather than asserting parity. CPF has no
construct for:

| Concept | Consequence on import |
| --- | --- |
| Supply Set | Synthesised per domain from its own `-primary_power_net`/`-primary_ground_net` rails, named `<DOMAIN>_SS` so it reads as generated. Without this a CPF domain imports with no supply and therefore no colour. |
| A switch's output supply | A CPF switch never carries the one thing needed to place it → **WARNING**, not ERROR. Erroring would put an error on every valid CPF file containing a switch. |
| A retention primary-vs-retention supply role | Both come back unset. |
| An isolation control signal / sense | Both come back unset (CPF has `-isolation_condition`, which this GUI has no editor for). |
| A level shifter's rule / applies-to / threshold | Come back unset. |
| A file-loading command (`load_upf` equivalent) | Macro attachments cannot survive at all — researched directly against the Si2 CPF spec. |
| A per-supply power state | CPF's `create_nominal_condition` is a single globally-reusable named condition, not per-supply. |

A generated CPF file now elaborates whole and imports with **zero
errors** — only warnings, each naming one of the above.

---

## Backend changes this feature required

The generator writes a real, if scoped, UPF dialect that the app's own
elaborator could not read. Extending the elaborator was chosen over
narrowing the generator (confirmed explicitly before starting), because
the generated commands are correct UPF and the gap was in what had been
implemented so far.

**New UPF commands**: `set_upf_version`, `set_scope`, `map_power_switch`,
`set_level_shifter_cell`, `set_retention_control`, `set_port_attributes`.

**Three pre-existing silent parsing bugs**, all the same shape — a
`{role value}` PAIR flag read as a scalar:

- `create_power_domain -supply {primary SS}` yielded a supply named
  `['primary', 'SS_VDD']`.
- `create_power_switch -input_supply_port {in VDD}` and `-on_state
  {on in {ctrl}}` likewise.
- `add_power_state -state {ON 1.2}` is both **repeatable** *and*
  pair-valued, so a three-state supply collapsed into one state named
  `['ON', '1.2']`. The repeatable-vs-list-valued distinction is a
  documented past bug class in this project, and this was a third
  instance of it.

**New IR fields**, mirrored through builders, the frontend mirror, and
the supported-features list: `PortAttribute` (a new construct),
`DomainSupplyConnection.ports`, `PowerDomain.available_supplies`,
`IsolationStrategy.isolation_signal`/`.isolation_sense`/`.isolation_ground_net`,
`LevelShifterStrategy.applies_to`/`.rule`/`.threshold`/`.cell_type`,
`PowerSwitch.cell_type`/`.input_supply_port`/`.output_supply_port`,
`RetentionStrategy.primary_supply`.

**`optional_positional`** on `shared/tcl_runtime.py`'s `_register()`:
`load_upf` accepts both `load_upf <file>` and `load_upf -file <file>`, and
removing the required positional to support the flag form broke seven
existing tests using the positional one. A shared, centralized mechanism
rather than a per-handler special case, consistent with that module's own
four hard requirements.

**The frontend power-intent mirror is now field-for-field complete**, and
`adapt_power_intent()` is generic — one `_adapt_pi_construct()` walking
dataclass fields, replacing eleven hand-written per-construct adapters.
`tests/frontend/test_power_intent_mirror_completeness.py` asserts the
parity, so the "adapter forgot to forward a field" bug class this project
has hit repeatedly cannot recur silently for power intent. The **RTL**
adapters are deliberately left explicit — they do real shape translation,
not field copying.

---

## Headless-test hazards hit while building this

Both already documented elsewhere in this project, both hit again:

- A real `message_box.question()` blocks on a modal nothing can click
  under the offscreen platform, hanging the entire run with no error.
  Every test that clicks "Continue" — across three files — patches it.
- `pytest`'s `tmp_path` is under `/private/var` on macOS while
  `tempfile.mkdtemp()` returns the `/var` symlink, which changes the
  depth a generated relative `load_upf -file ../../..` path resolves
  from. Scratch harnesses need `.resolve()`; `tmp_path` is already fine.

---

## Follow-up: hand-authored fixture validation

Section 8's round trip proves the importer reads back the app's own
output. A separate, complementary exercise then hand-authored seven
fixtures the way a designer or vendor actually writes power intent --
different command ordering, equivalent-but-different flag spellings,
naming that ignores this app's conventions -- against
`examples/upf_example_1_sram/design/`.

**Every fixture caught at least one real defect: 20 findings, 17 fixed.**
The full report, fixture index and coverage matrix live in
`tests/frontend/golden_simple10/README.md`; the permanent tests are
`tests/frontend/test_hand_authored_power_intent.py`.

The headline conclusion for anyone extending this feature: a round trip
against your own output is necessary and **not sufficient**. Four of the
twenty findings were colour-derivation bugs that left every entity
correct — including one that made every imported Power Domain the same
colour, and one that made every CPF supply grey. Three more were whole
features that did not work at all (macro PG connectivity, the system-mode
table, multi-bit port attributes). Three were valid files rejected
outright. And one — an unset flag written as the literal text `None`,
which parses cleanly and returns as the string `'None'` — was only
reachable by re-EXPORTING an imported file, a path neither the round trip
nor an import-only harness covers.

Two IR fields were added for the import side as a result
(`PowerState.supply_states`, `SupplySet.nwell_net`/`pwell_net`,
`PowerSwitch.control_port_pins`), and `ImportedPowerIntent` gained a
ninth state family for the Power State Table, which is owned by its own
tab rather than the diagram.

---

## Follow-up: Supply Port "Direction"

A `Direction` field on the Add/Edit Supply Port dialog, mapping to UPF
`create_supply_port -direction`. Two syntax questions had to be settled
by research before any of it was written, both of which the request
explicitly refused to let be guessed at.

### UPF: the verb and the values

**Verb: `create_supply_port`.** The request's own correction was right —
`create_power_supply` is not a UPF command, and a test asserts it appears
nowhere in generated output.

**Values: `in`, `out`, `inout`.** Two independent secondary references
document `-direction {in|out|inout}` on `create_supply_port`, and the
sibling `create_logic_port` is documented with the identical three values
in OpenROAD's own UPF command reference. Secondary-source tier, which is
the same qualification this project already attaches to every other UPF
claim (IEEE 1801 is paywalled, unlike CPF's specification). Deliberately
not extended beyond those three.

Generation is exactly as specified: the flag appears only when a
direction is set, and `""` is a real user choice meaning "omit it",
not a missing value waiting for a default.

### CPF: no equivalent exists — established from the primary source

This is the answer section 4 asked for, and it was read out of the Si2
Common Power Format Specification v1.0 directly rather than inferred:

- **CPF has no supply-PORT construct at all** to hang a direction on.
  `create_supply_port`, `create_power_port`, `create_ground_port` and
  even the phrase "supply port" return **zero hits** in the entire
  specification. CPF declares supply NETS
  (`create_power_nets`/`create_ground_nets`, whose full option lists are
  `-nets`, `-voltage`, `-external_shutoff_condition | -internal`,
  `-user_attributes`, `-peak_ir_drop_limit`, `-average_ir_drop_limit`)
  and names a design's or a domain's ports separately (`set_design
  -ports`, `create_power_domain -boundary_ports`). None of those carries
  a direction, because a port's direction comes from the RTL.
- **`-direction` does exist in CPF — on exactly one command**, and it
  means something else: `define_level_shifter_cell -direction
  {up|down|bidir}` is voltage-translation direction. Emitting a supply
  port's in/out/inout through it would be fabrication.
- The nearest *adjacent* concept is `create_power_nets -internal`, which
  distinguishes an internal net from a boundary-crossing one. That is not
  direction either — and this app already models that distinction
  separately, as Internal Supply.

So, per this project's standing "no clean equivalent — report it, don't
fabricate a workaround" convention (already applied to Supply Sets,
per-net Power States, `-available_supplies`, and macro power-intent
attachments), CPF generation emits nothing for Direction and
`supply_port_direction_cpf_warnings()` tells the user, sharing the one
"Not Expressible in CPF" dialog the macro-attachment gap already uses.

### Scope enforcement

Direction is Supply Port only. Because all three supply kinds share one
dialog class and one placement dataclass, "does not apply" had to be
actively enforced in four places rather than assumed, and each has its
own test: the dialog does not construct the combo at all for an Internal
or Virtual Supply; generation emits no flag for them (neither gets a
`create_supply_port` in the first place); the Properties table shows
blank, matching how every other not-applicable column there is already
handled; and the importer never assigns a direction to an internal net
even when a same-named `create_supply_port` exists upstream.

### Import

`SupplyPort.direction` moves from UNMODELLED to MODELLED in
`supported_features.py`. Its old reason — "not modelled; placement edge
is derived from Ground role instead" — conflated two things: the
placement *edge* really is derived from the Ground role and still is, but
that was never what `-direction` means.

An imported port that declared no `-direction` gets `""`, **not** the
`"in"` a newly-created port defaults to. The two states mean different
things and only one generates a flag, so Edit pre-fills with `""` rather
than silently upgrading it on first edit. New golden fixture
`09_supply_port_directions.upf` covers all four values, with the
round-trip asserted on the generated *text* as well as the reloaded
state — a bug writing `-direction None` could otherwise round-trip
through this app while producing invalid UPF, which is exactly the shape
of finding 20.

---

## Follow-up: reviewing a real, idiomatic UPF file

`examples/soc_with_ram/upf/soc_top.upf` was rewritten by hand into
idiomatic UPF 3.1. It **did not elaborate at all** — it failed on its
first unsupported command, so nothing after that was reached. Six real
gaps, every one a construct a hand-written file uses and this app's own
generator never emits, which is precisely why the round-trip suite was
structurally incapable of finding them:

| Gap | Why it was missed |
| --- | --- |
| `associate_supply_set SS -handle PD.primary` not registered at all | The generator writes `create_power_domain -supply {primary SS}` instead |
| `add_power_state -supply SS` (object by flag, no positional) | The generator always writes the positional form |
| `-state {ON -supply_expr {...} -simstate NORMAL}` (nested flags) | The generator writes `{name value}` pairs |
| `set_isolation -applies_to` | Not generated |
| `set_level_shifter -input_supply`/`-output_supply` | Not generated |
| `create_power_switch -supply_set` | Not generated |

Plus one import-side gap the same file exposed: a net reaching the
boundary through `connect_supply_net <net> -ports {PORT}` under a
**different name** imported as an Internal Supply and silently lost the
port's `-direction`. Only the same-name case was recognised — again, the
only one the generator produces.

The pattern is worth stating plainly, because it will recur: **a round
trip against your own output cannot find anything about syntax you never
emit.** Both of this project's other fixture families exist for that
reason, and this one adds a third angle — a file written to be *good
UPF* rather than to exercise this app.

`associate_supply_set` also needed scope-aware resolution: a file sourced
under `load_upf -instance u_sram` can legitimately declare a `PD_TOP`
beside the parent design's own, and a bare-name lookup then fails as
ambiguous when the intended domain is obvious — it is the one in the
scope the command was written in.

## The four semantic mappings

Specified explicitly after the review above, each stating that one UPF
construct MEANS THE SAME THING as something this app already models.
`tests/frontend/test_upf_semantic_mappings.py` asserts them as
equivalences -- the two spellings must produce the same imported state --
rather than merely asserting the new one parses.

### 1. `associate_supply_set SS -handle PD.primary`

== `create_power_domain PD -supply {primary SS}`. Lands on
`PowerDomain.primary_supply`.

### 2. `associate_supply_set SS -handle PD.<anything else>`

== `create_power_domain PD -available_supplies {SS}`. Appends to
`PowerDomain.available_supplies`.

This **corrected** the first implementation, which rejected every
non-primary handle outright. That reasoning was half right: folding a
non-primary handle into `primary` would attach the wrong supply set. But
the alternative was never refusal — a secondary association belongs in
`available_supplies`, and rejecting it failed valid files. Appended
rather than assigned, since several handles can associate several sets
with one domain.

### 3. `add_power_state -supply <supply_set>` declares states on the SET

A set-level state's `-supply_expr` says what each of the set's own rails
is doing, and that is exactly equivalent to per-net states:

```
add_power_state -supply SS_VDD \
    -state {ON  -supply_expr {power == {FULL_ON 1.2} && ground == {FULL_ON 0.0}} -simstate NORMAL} \
    -state {OFF -supply_expr {power == OFF          && ground == {FULL_ON 0.0}} -simstate CORRUPT} \
    -complete
                          ==
add_power_state VDD -state {FULL_ON 1.2}
add_power_state VDD -state {OFF}
add_power_state VSS -state {FULL_ON 0.0}
```

So a set-level state is decomposed per rail. Two details the equivalence
settles, both easy to get backwards:

- **The rail state's NAME comes from inside the term, not from the
  set-level state.** `-state {ON -supply_expr {power == {FULL_ON 1.2}}}`
  declares a rail state called `FULL_ON`, not `ON`.
- **A rail named by several set-level states is not duplicated.** A set's
  ground is typically `FULL_ON` in every one of its states.

Keyed off the supply-set *function*, so it covers `nwell`/`pwell` too,
not just power/ground.

Without this, a file declaring its states the supply-set way — the
idiomatic UPF 3.x style — imported with **no states on any supply at
all**, silently: they existed in the IR, attached to a set name no supply
lookup would ever match.

Writing the equivalence as a test immediately caught a real asymmetry in
the *existing* per-net path: a bare `add_power_state VDD -state {OFF}`
(no level) was imported as a **voltage** state with no voltage, because
only the `-state {OFF off}` spelling this app generates was recognised.
Both now produce the same off state.

### 4. `set_isolation -applies_to` matches the rule's own elements

`-applies_to outputs` alongside `-elements {a b c}` describes one
selection, not two independent facts. So it is resolved **into** the
element list — the elements it selects are kept — rather than stored as a
field this GUI has nowhere to put. `IsolationStrategy.applies_to` is
MODELLED as a result.

`both`, an unrecognised value, and any element whose direction cannot be
resolved are all left alone: filtering on a guess would silently drop a
port the file explicitly listed, which is worse than keeping one it
arguably did not select.

---

# The power-states model redesign

A full replacement of how power states are modelled, displayed and
generated. The old model — a per-state authored NAME, a binary
Voltage/Off type, one distinctly-named `SWITCH-OFF (<switch>)` state per
Power Switch, per-net independent `add_power_state`, and a legacy-PST
translation — is gone. Accepted explicitly as a **breaking schema
change**: `.pis` files written under the old schema do not load, and no
migration was written.

## The state vocabulary

`SupplyPortState` is now `state_type` ∈ {`FULL_ON`, `PARTIAL_ON`, `OFF`,
`UNDETERMINED`} plus a voltage, **with no name**. Naming moved to the two
levels that actually need it: a Supply Set's derived states
(`STATE_1`, `STATE_2`, …) and the Power State Table's own row names.

That alignment is the point rather than a coincidence: UPF writes a rail
state as `{FULL_ON 1.2}`, and the leading token *is* this vocabulary. A
state maps to UPF with no translation, in both directions.

Voltage is **required** for `FULL_ON`/`PARTIAL_ON` and the cell is
**disabled** for `OFF`/`UNDETERMINED`. That retires the old "two
ambiguous no-voltage Active states" conflict check outright — the
ambiguity is unreachable once a level is mandatory. What remains is a
plain duplicate-row check.

### Two representations, deliberately

- `state_key()` — `"FULL_ON 1.2"`. The canonical identity, what a Power
  State Table row stores, and exactly the token UPF wants inside a
  `-supply_expr`.
- `state_display()` — `"1.2"`. What a cell shows.

They must differ: `FULL_ON 1.2` and `PARTIAL_ON 1.2` both display as
`"1.2"` while being genuinely different states, so display text cannot
identify a selection. The Power State Table's combos therefore carry the
key as item data and read it back with `currentData()`, never from the
visible text.

## Power Switch: one OFF, never more

A switch in an Internal Supply's chain now contributes a single plain
`OFF`, with no switch name in it. If the inherited states already contain
an `OFF`, none is added; **cascaded switches collapse to at most one OFF
in total**. Two switches in series can only produce one "unpowered"
condition, so the extra entries said nothing. The switch name survives
only in the Inherited Power States preview's provenance suffix, which is
what that preview is for.

## Virtual Supply as a Supply Set rail

The rule that a Virtual Supply "cannot be used as a rail within a Supply
Set" is **reversed**. It is selectable as Power/Ground/Nwell/Pwell like
any other supply, and participates in Tier 1 with no separate path.
Supply Set membership is a *logical* grouping, distinct from the physical
connection restriction — which is unchanged: it still only ever wires to
a macro's internal PG pins, still has no Power/Ground type of its own,
and still cannot host a Power Switch. Standalone use is unaffected; the
two modes are not exclusive.

## Generation: two tiers

**Tier 1, per Supply Set.** Walk every Power State Table row, read that
set's four rails out of the row's per-net selections, combine them into
one `-supply_expr`. Identical combinations across rows collapse into one
derived state, reused. `-simstate` is `CORRUPT` if any rail is `OFF` or
`UNDETERMINED`, else `NORMAL`. **No `-complete`** — confirmed, reversing
an earlier worked example that included it.

**Tier 2, one global group.** A single `create_power_state_group
<TOP>_PST` for the entire design covering every domain including nested
ones, then one `-state` per table row whose `-logic_expr` names each
relevant Supply Set == its own Tier 1 state.

Both tiers are derived in **one pass** (`_derive_supply_set_states()`),
which is what guarantees Tier 2 never disagrees with Tier 1 about which
`STATE_N` a row means.

### One ambiguity in the spec, resolved deliberately

The spec scopes a row's `-logic_expr` to "the Supply Sets that domain's
rows reference". Two sets routinely **share a ground rail**, so a row
selecting only `SS_A`'s rails also incidentally matches `SS_B`'s ground —
and constraining `SS_B` on that basis asserts something about a set the
row says nothing about, via a thin `ground ==`-only expression.

Relevance therefore turns on the **power rail**: a set participates only
when the row selects its own power rail, the rail that defines it and the
one a domain's condition and colour both derive from. A set with no power
rail at all falls back to "any rail", so it is never silently excluded.
Found by a test written from the spec's own wording, which is what made
the ambiguity visible.

## CPF: already two-tier, reshaped not invented

Researched against the Si2 CPF 1.0 specification directly. CPF's model is
structurally two-tier already:

- `create_nominal_condition -name <s> -voltage <float>` — Tier 1, but a
  single **voltage**, with no ground/nwell/pwell composition. CPF has no
  supply-set construct, so a multi-rail `-supply_expr` has no equivalent.
- `create_power_mode -name <s> -domain_conditions <list> [-default]` —
  Tier 2, per **power domain**, not per supply set.

So a domain's condition is its **primary supply set's power rail** in
that row. Two rules taken from the spec rather than guessed:

- *"A power domain is switched off if it is associated with a nominal
  condition whose voltage is 0."* → `OFF` is a 0.0 V condition, **not** a
  `-state off` flag, which CPF 1.0's `create_nominal_condition` does not
  define.
- *"you must define one (and only one) power mode as the default mode."*
  → the first row gets `-default`, and exactly one does.

The ground/nwell/pwell rails and the supply-set grouping itself are
reported as inexpressible rather than fabricated.

This also **closed a real gap**: `-domain_conditions` used to be emitted
as a comment because the per-domain data did not exist. The supply-set
derivation produces it, so CPF now carries real conditions.

## Round-tripping the new shape

Two elaborator additions were needed for the app to read its own output
back: `create_power_state_group` (the command did not exist — Tcl
rejected the name) and `add_power_state -group`. The importer then
rebuilds the Power State Table **from the group**, reversing both tiers:
a group state's `-logic_expr` names supply sets and their derived states,
each derived state's `-supply_expr` gives one value per function, and
each function maps to the net the set names for it.

One property is asserted as a **named exception** rather than hidden: a
rail state that no table row ever selects does not survive a round trip.
It is composed into no supply-set state, so it reaches the file at all —
which is the model working as designed, not a loss.

## UPF -> design mapping on aura_top (2026-10-06)

Use case: load `examples/aura_top`'s RTL + LIB, map the UPF in its `upf/`
directory, and get back exactly `aura_top.pis` (both UPFs were generated
from it). `tests/frontend/test_upf_mapping_aura_top.py` runs the real
pipeline and compares every diagram family by name.

- **Legacy UPF: 100%, zero diagnostics.**
- **Modern UPF: everything the format carries.** Two differences are
  asserted exactly rather than tolerated: per-supply state NAMES (a modern
  file has none) and the states and PST column of `VDD_HI`, which is in no
  supply set and so has no supply-set state to carry them.

What had to change in the mapping:

- `add_port_state` / `add_supply_state` registered (new
  `PowerStateShape.PORT_STATE`); without them a legacy file did not load.
- **Shared rails rebuilt** (`_materialize_shared_rails()`), mirroring the
  app's own auto-link: for each domain and each supply set it references
  (primary and secondaries), a rail that is a boundary Supply Port on
  another hierarchy gets a same-named Internal Supply on the domain's
  hierarchies, wired to the port. Only a real port is a source, and a
  macro hierarchy never gets one. Domain colours are re-derived afterwards,
  since a set may resolve only through a copy.
- Legacy PST rows: per-supply state names restored, the generator's
  `PST_` prefix stripped (only when every row of that PST carries it), and
  each row given the domain owning the design root.
- A macro PG pin connects to the nearest same-named supply; an empty
  switch cell type stays `None`; a supply set used only as a secondary
  takes the hierarchy of the domain using it.

**Unrecognised syntax** is skipped and reported instead of aborting the
load. The UPF elaborator overrides Tcl's `unknown` and turns an
unrecognised option into an `UnrecognizedConstruct` record (command,
quoted command text, reason, file and line from `info frame`); the
command is skipped WHOLE, since half-applying it would be a silent wrong
answer. The importer turns each into a `UPF-SYNTAX-NOT-RECOGNIZED`
WARNING: `line N: `<command>` was not recognized and was skipped -- <reason>`.
Tests: `tests/frontend/test_upf_unrecognized_syntax.py`.
