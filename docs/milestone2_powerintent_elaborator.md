# Milestone 2 — Power Intent Elaborator (UPF + CPF): Engineering Spec

Status: version research and Tcl interpreter spike resolved; 2c IR schema
done; 2a (UPF elaborator) and 2b (CPF elaborator) both done and audited
against locked-in scope, built on the same shared infrastructure — see
each sub-section below for exact verb coverage and tracked gaps. One open
question before Milestone 3 (CPF's `domain_supply_connections` gap, see
2b's audit) — otherwise Milestone 2 is functionally closed. Companion to
the summary in `CLAUDE.md`. Update as design decisions are refined during
implementation — this should stay accurate, not aspirational.

## Goal

Given a power-intent file set (either UPF or CPF — never mixed within one
design, per explicit decision below) plus, once Milestone 3 exists, the
elaborated RTL hierarchy it will eventually be mapped onto, produce a single
format-agnostic IR representing the **full power intent**: domains, supply
ports/nets/sets, power switches, isolation strategies, retention strategies,
level-shifter strategies, power states/power state tables, and hierarchical
scope (including UPF's `load_upf`/CPF's equivalent hierarchical inclusion).

## Structure: mirrors Milestone 1's RTL split

- **2a — UPF elaborator. Status: done, audited against locked-in scope**
  (`backend/elaborator/power_intent/upf/`).
- **2b — CPF elaborator. Status: done, first pass**
  (`backend/elaborator/power_intent/cpf/`), built on the same
  `shared/{tcl_runtime,builders}.py` as 2a.
- **2c — Unified power-intent IR schema. Status: done**
  (`backend/elaborator/power_intent/ir/schema.py`, design discipline matched 1c —
  validated against hand-written UPF and CPF examples before real
  integration work started).

### Directory layout — restructured to mirror `rtl/{verilog,vhdl,ir}/`

Originally built as `backend/elaborator/upf/{elaborate,verbs,legacy_pst,
tcl_runtime,builders}.py` with `upf/ir/` nested inside it. Restructured,
mid-flight during the tcl_runtime.py/builders.py module split (cheapest
point to move them), to:

```
backend/elaborator/power_intent/
  ir/          # canonical schema.py -- true sibling of upf/ and cpf/, not nested inside either
  shared/      # tcl_runtime.py, builders.py -- confirmed format-agnostic, reused as-is by 2b
  upf/         # elaborate.py, verbs.py, legacy_pst.py -- 2a's own glue and verb handlers
  cpf/         # 2b, not started -- not created yet, per the standing "don't build ahead" rule
```

The original nesting (`upf/ir/`, `upf/tcl_runtime.py`) made a future CPF
elaborator look like it would import from UPF's own package, which is
exactly backwards given `ir/` and `shared/` are meant to be format-neutral.
Making `builders.py` a true `shared/` module also surfaced one real
correction needed to make it *actually* format-agnostic, not just
positioned as if it were: `_freeze_all` previously hardcoded
`source_format=SourceFormat.UPF` and `_ElaborationState` carried a
UPF-specific `_pst_supplies` field. Fixed: `_freeze_all` now takes
`source_format` as an explicit parameter, and legacy-PST bookkeeping moved
to a generic `state.scratch` dict any format's verb modules can use for
their own private multi-command bookkeeping, keeping the shared state
class itself free of format-specific fields.

All 64 existing tests passed unchanged both before and after the move
(pure reorganization, zero behavior change, confirmed rather than assumed).

### 2a — UPF elaborator implementation notes

Instrumented verbs, one pass covering every construct category from the
"full strategy coverage" decision: `upf_version`, `load_upf`,
`create_power_domain`, `create_supply_port`, `create_supply_net`,
`create_supply_set`, `set_domain_supply_net`, `create_power_switch`,
`set_isolation`, `set_retention`, `set_level_shifter`, `add_power_state`,
`create_power_state_group`, and legacy `create_pst`/`add_pst_state` (with
the PST-row-to-canonical-`PowerState` translation from design question 2
actually implemented and tested, not just designed).

**Two real bugs found by testing against actual fixtures, not just by
design review** — same pattern as every real bug Milestone 1 caught:

1. **`load_upf`'s relative path resolved against the wrong base.** Tcl's
   `source` resolves a relative path against the process's cwd, not the
   currently-sourcing file's own directory. A two-file `load_upf` fixture
   failed outright ("no such file or directory") until the handler was
   fixed to resolve relative paths against `os.path.dirname` of the file
   currently being sourced. Regression-tested by running the hierarchical
   fixture from an unrelated `cwd` (`monkeypatch.chdir(tmp_path)`).
2. **`-control_port`'s repeatability was conflated with list-splitting.**
   Registering it as a `list_options` flag meant a single scalar occurrence
   (`-control_port sleep_n`) got Tcl-list-split into `['sleep_n']` and then
   wrapped again by the repeat-collector, producing `control_ports =
   [['sleep_n']]` instead of `['sleep_n']`. "Repeatable flag, each
   occurrence a scalar" and "flag whose single value is itself a Tcl list"
   (e.g. `create_supply_set`'s `-function {power VDD}`, correctly
   list-valued) are different things needing different handling — fixed by
   not marking `-control_port` as list-valued, since repetition alone
   already produces the list.

**Deliberately not attempted this pass, logged not silently dropped:**
`-on_state`/`-off_state` argument parsing for `create_power_switch` — exact
UPF argument grouping isn't confidently known (see the confidence-asymmetry
note above); `PowerSwitchState` is modeled in `schema.py` but `states`
stays empty until a real UPF example confirms the shape. Cross-reference
validation (does `-domain PD_CORE` actually name a declared domain?) is
deliberately not done in the elaborator itself — same boundary as
`backend/validation/` being a separate, cross-cutting concern in the repo
layout.

**`version_gates.py`: `-location fanout` requires UPF 3.1+.** Built after
the module-split review, per the restructure plan's `version_gates.py`
piece. A small, explicit `{value: minimum_version}` table (currently one
entry), checked from `_set_isolation`/`_set_level_shifter` at the point
`-location` would otherwise be accepted unconditionally. `fanout`'s
3.1-only status is grounded in the DVCon whitepaper read directly during
version research (UPF 3.0's isolation example there only shows `-location
parent`; 3.1 introduces the path-based `fanout` semantics specifically).
Deliberately UPF-specific, not a shared per-format mechanism -- no
evidence CPF has a comparable version-gated value, and extracting a shared
abstraction before a second real consumer exists would be premature, same
reasoning as `precedence.py`'s retargeting above. Confirmed both
directions, not just the rejection: `fanout` under a 2.1-declared design
and under a 3.0-declared design both raise naming the required version;
`fanout` under 3.1 (the existing `upf_basic` fixture) still passes; a
non-gated value (`parent`) under 2.1 still passes, proving the gate
doesn't over-reject.

**Second real consumer, decided during Milestone 3b planning:**
`check_location_value` will be called from `backend/modification/` at
edit time too, not just from `_set_isolation`/`_set_level_shifter` at
parse time -- so a design's declared version stays enforced consistently
whether a value arrives via a real UPF file or via an in-memory edit. This
doesn't change the "premature to extract a shared per-format abstraction"
conclusion above (CPF still has no comparable version-gated value); it's
still UPF-only, just imported by a second module instead of one.

### Feature-completeness audit (before starting 2b) — real gaps found, same discipline as the 1h audit

Before starting CPF (2b), 2a was explicitly re-checked against the
locked-in scope ("full strategy coverage... hierarchical scope... two Tcl
gotchas as centralized wrappers... the version gate") rather than assumed
complete because the current round of fixes had landed. Five real, un-logged
gaps were found. Fixed, in the order triaged:

**Fixed — systemic: unrecognized/extra flags on an otherwise-valid command
were silently dropped, not rejected.** Confirmed empirically:
`create_power_domain PD_TOP -elements {.} -bogus_flag whatever` elaborated
successfully, discarding `-bogus_flag`'s data with no error at all. Every
handler only read the specific flags it knew about via
`_one(parsed, key)`; nothing checked for leftover, unconsumed keys. This
is the "silently produce incomplete data instead of failing loud" failure
mode the project avoids everywhere else (1e's `_resolve_direction`
raising on an unrecognized value, CPF's `-function` kind validation) --
but here it was systemic across all 14 registered commands, not a one-off.
**Fixed as a third hard requirement in `tcl_runtime.py`'s `_parse_args`**
(alongside the two Tcl-spike gotchas): every `_register(...)` call now
declares its full recognized `options` set; any flag outside it raises
immediately, naming the command and the flag, before the handler ever
runs. Also added `flag_options` support (valueless boolean switches, e.g.
`add_power_state`'s `-complete`), needed once that flag was wired.
Confirmed behavior-preserving first: all 68 then-existing tests still
passed after adding strict enforcement with option sets matching only the
already-implemented flags, *before* any new feature was added -- proving
nothing already built had been silently relying on an unenforced flag.

**Built now (all explicitly in-scope per "full strategy coverage from day
one"), surfaced by re-checking scope, not by the strict-parser run itself
(which found nothing new beyond what the audit already listed, precisely
because nothing already-built had relied on silently-dropped data):**

- **`connect_supply_net`** -- only the `-domain` form (functionally
  identical to `create_supply_net -domain`/`set_domain_supply_net`,
  synthesizing the same `DomainSupplyConnection`). The `-ports`/`-pins`
  form (wiring a net directly to logic ports) doesn't fit
  `DomainSupplyConnection`'s shape at all -- it's a net-to-port
  connection, not a net-to-domain one, and Milestone 2 has no construct
  for that yet. Deliberately **not** added to the command's recognized
  `options`, so a real file using `-ports` fails loud with "unrecognized
  option" rather than silently losing the connection -- tracked here as
  an explicit gap, to be resolved once a real design's usage motivates a
  schema decision, not guessed at now. A `connect_supply_net` call with
  neither form recognized (no `-domain`) also raises explicitly, since a
  no-op call is almost always a sign of unsupported syntax, not
  legitimate content-free usage.
- **`elements` added to `IsolationStrategy`/`LevelShifterStrategy`**
  (schema + builders + `-elements` wiring in `set_isolation`/
  `set_level_shifter`). `RetentionStrategy` already had this; isolation
  and level-shifter strategies didn't, even though real UPF commonly
  targets specific ports directly (`-elements`), not just domain-level
  `-source`/`-sink`. This was a structural schema gap, not just an
  unwired flag -- there was nowhere to put the data even once parsed.
- **`exclude_elements` added to `PowerDomain`** (schema + builder +
  `-exclude_elements` wiring in `create_power_domain`, list-valued like
  `-elements`).
- **`add_power_state`'s `-domain`/`-supply`/`-complete`** added to
  `PowerState` (`domain: Optional[str]`, `supply: Optional[str]`,
  `complete: bool`). The triage asked for `-domain`/`-complete`
  specifically; `-supply` was added alongside them since it's named in the
  exact same verified research sentence ("Added -supply, -domain and
  -complete to add_power_state") -- leaving it out arbitrarily while
  fixing the other two from the same finding would have quietly
  recreated the same kind of gap this audit exists to catch.

**Investigated before building or dropping, per instruction --
`sim_assertion_control`/`sim_replay_control`/`sim_corruption_control`
(UPF 3.1): explicitly excluded, documented, not silently unregistered.**
Two independent pieces of evidence converge: (1) the DVCon whitepaper read
directly during version research describes all three as simulator-behavior
directives (controlling assertion activity, initial-block replay, and
corruption semantics *during simulation*), not power-intent structure; (2)
a further search specifically confirms these are "classified as
simulation-only UPF commands that do not affect implementation, power
intent structure, or implementation-only enforcement," and that real UPF
tooling can exclude them wholesale via `load_upf`'s `-implementation`
option -- i.e. the standard itself treats them as a distinct,
non-structural category. This matches the doc's own pre-existing
"likely-legitimate exclusions... downstream analysis details, not
structural power intent" carve-out exactly. **Decision: not modeled in
2c's IR, not registered as commands.** A file using one gets a clear
"invalid command name" error (fail loud), not a silent no-op. Revisit only
if a future milestone needs simulation-behavior directives specifically
(none currently do).

`-on_state`/`-off_state` on power switches remains exactly as previously
logged (deliberately deferred, unchanged by this audit).

**Module layout**: `backend/elaborator/power_intent/upf/{elaborate,verbs,
legacy_pst,version_gates}.py`, built on `backend/elaborator/power_intent/
shared/{tcl_runtime,builders}.py` — see the directory-layout section above.

**Tests**: `tests/backend/test_upf_elaborate.py` (71 total), fixtures under
`tests/backend/golden_upf/` — full-coverage single-file (now also covering
`connect_supply_net`, isolation/level-shifter `-elements`,
`-exclude_elements`, and `add_power_state`'s `-domain`/`-supply`/
`-complete`), hierarchical `load_upf` (including the relative-path
regression test), legacy PST translation, one fixture per version-handling
branch (missing declaration, unsupported version, conflicting per-file
declarations, verb before any declaration reached through a sourced
helper, the whole-design backstop, and a helper file that must never fail
just for existing), the location-value version gate (both directions), the
unrecognized-option rejection, and `connect_supply_net`'s two rejection
paths (`-ports` unrecognized, no recognized data at all).

### 2b — CPF elaborator implementation notes

Built on `backend/elaborator/power_intent/shared/{tcl_runtime,builders}.py`
**as-is, extended not duplicated** -- confirmed while proposing 2b's scope
that both modules were genuinely format-agnostic (no UPF-specific
branches), and that held throughout the build with one exception, fixed
immediately on discovery: `tcl_runtime.py`'s "verb before any version"
error message hardcoded `'upf_version'` in its wording. Generalized to
format-neutral phrasing before writing a single CPF verb, not after.

**Two real design questions, both resolved by verifying against the
primary spec before finalizing, per explicit instruction not to treat
either as settled from the proposal alone:**

1. **CPF has no `load_upf`-equivalent file-sourcing verb.** Multi-file CPF
   (if used) is plain Tcl `source`, needing no CPF-specific command at all.
   Hierarchical scope instead works through `set_design`/`set_instance`/
   `end_design` -- a **flat sequence of independent scope blocks**, not a
   nested push/pop stack. Verified against the primary spec's own worked
   example (a single `set_design top` ... `end_design` block wrapping an
   entire flat multi-domain design) and its text (`set_instance` "specifies
   the name of a hierarchical instance **in the top design**" -- not
   relative to whatever the current scope happens to be). Built as a
   hypothesis first: a hand-written two-block fixture
   (`tests/backend/golden_cpf/cpf_hierarchical/top.cpf`, one top-level
   domain plus one domain under `set_instance pm_inst`) confirmed
   `set_instance` **replaces** the current scope (`_ElaborationState.
   set_scope`, a new method distinct from `push_scope`/`pop_scope`) rather
   than nesting one, and that `end_design` resets to top-level scope,
   closing exactly the one block it opened -- not popping to a "parent"
   that doesn't structurally exist here.
2. **The `connect_supply_net -ports`-equivalent question, resolved, not
   deferred a second time.** CPF has no construct shaped like UPF's
   `-ports` form. The closest analogs -- `create_power_domain
   -boundary_ports` (ports↔domain) and `update_power_domain
   -internal_power_net`/`-internal_ground_net` (net↔domain's internal
   fabric) -- are both a different shape (no direct net↔specific-port
   bind). CPF genuinely doesn't need UPF's deferred construct. But
   re-verifying these two commands surfaced a real, separate finding: see
   the recurring-pattern note below.

**By-name merging (`create_X`/`update_X`) is real for CPF** -- confirmed
directly in the `cpf_basic` fixture: `update_power_domain` and
`update_isolation_rules` both mutate the exact builder their matching
`create_*` call produced (asserted by checking the merged fields appear on
the *same* single domain/isolation-rule record, not a second one).
`precedence.py` (retargeted from its mistaken UPF-scoped proposal --see
above) implements this as `find_by_name` (exact match only; wildcard
`-names`, which the spec allows, is an explicit, logged gap, not
attempted) + `apply_update` (plain `setattr` per given field, no conflict
resolution needed -- confirmed empirically that CPF's update commands only
ever add fields their paired create command didn't set).

**Verb scope built**: `set_cpf_version`, `set_design`, `set_instance`,
`end_design`, `create_power_domain`/`update_power_domain`,
`create_power_nets`/`create_ground_nets`, `create_power_switch_rule`,
`create_isolation_rule`/`update_isolation_rules`,
`create_level_shifter_rule`/`update_level_shifter_rules`,
`create_state_retention_rule`, `create_power_mode` -- all shapes verified
directly against the Si2 CPF 1.0 primary spec, not guessed.

**Deferred, explicitly logged (verify before implementing, not before this
audit's discipline):** `update_power_switch_rule`,
`update_state_retention_rules`, `update_power_mode`, `create_bias_net` --
command names confirmed to exist, exact argument shapes not yet read.
Multi-domain `-from`/`-to` on isolation/level-shifter rules and wildcard
`-names` on update commands both fail loud (tested) rather than silently
picking one match or dropping data. Implementation-detail/library-binding
options (`-cells`, `-library_set`, `-prefix`, bias nets, power-up timing,
`-user_attributes`, IR-drop limits) are recognized (so real files don't
fail) but deliberately not stored, same "downstream analysis, not
structural power intent" carve-out already established for UPF.

**Tests**: `tests/backend/test_cpf_elaborate.py` (14 tests, see the audit
below for the additions past the initial 10), fixtures under
`tests/backend/golden_cpf/` -- full-coverage single file (including both
by-name merges, plus a dedicated isolated test for the marquee
create-then-update case specifically), the hierarchical scope-replacement
fixture, version handling (including the "CPF spec defaults to 1.0, must
not silently apply that default" case, which has no UPF analog),
unclosed/re-opened `set_design` blocks, unrecognized-option rejection,
update-on-nonexistent-name, and the multi-domain tracked-gap fail-loud
check.

### 2b feature-completeness audit (before closing Milestone 2) — one real bug, several real coverage gaps

Same discipline as 2a's own audit and 1h's before it: re-checked against
the locked-in scope rather than assumed complete because 2a's lessons had
already been applied going in. They had -- unconsumed-flag rejection was
built in from the first command, not retrofitted -- but that didn't mean
2b was gap-free; it meant the *known* gap class from 2a's audit didn't
recur. A different, real bug and several real coverage gaps still turned
up.

**Fixed — a real bug, not assumed away: multi-file CPF was broken.** This
doc's own 2b implementation notes previously claimed multi-file CPF "needs
no special handling... confirmed by inspection, not assumed" -- that
claim was itself never actually tested, and the audit disproved it. CPF
has no `load_upf`-equivalent wrapper verb, so multi-file CPF relies on raw
Tcl `source` directly, which resolves a relative path against the process
cwd -- the exact bug class UPF's `load_upf` handler had already fixed, but
only inside that one wrapper. A real two-file CPF fixture failed outright
("no such file or directory") the first time it was actually run from an
unrelated cwd. **Fixed generically**, not just for CPF: `shared/
tcl_runtime.py` gained a fourth hard requirement, `install_relative_source`,
which overrides Tcl's native `source` itself (rename to
`_tcl_native_source`, delegate after resolving the path and updating the
file stack) so both formats -- and both usages, through a wrapper verb or
bare `source` -- get correct resolution uniformly. UPF's `load_upf`
handler was simplified to drop its now-redundant manual path resolution,
keeping only the `-scope` push/pop that's genuinely UPF-specific.
Regression-tested for both formats by running from a deliberately wrong
cwd (`monkeypatch.chdir`), matching the existing UPF regression test's
own style.

**Built, closing real coverage gaps found by re-checking against verified
syntax rather than assuming the happy path was representative:**
multi-name `create_power_nets`/`create_ground_nets` (`-nets {A B C}`, not
just one name per call), isolation/level-shifter `-pins`/`-exclude`
(exercising `elements`/`exclude_elements`, previously only tested via the
domain-level `-elements`/`-exclude_elements` on UPF), the
retention-by-`-instances` form (not just `-domain` -- the specific case
that motivated relaxing `RetentionStrategy.domain` to `Optional` while
building 2b, now actually exercised rather than just theoretically
supported), and a power switch's `-external_ground_net` path (only
`-external_power_net` had been exercised before).

**RESOLVED (was left open above): CPF now populates `domain_supply_connections`
too.** Re-examined precisely, not just triaged: `update_power_domain`'s
`-internal_power_net`/`-internal_ground_net` ("the main power/ground net
for all functional gates in the specified power domain") and UPF's
`set_domain_supply_net -primary_power_net`/`-primary_ground_net` describe
the exact same real-world fact -- the net driving a domain's functional
logic -- not a naming coincidence the way `IsolationStrategy.location`
turned out to be. **Unified**: `PowerDomain.internal_power_net`/
`internal_ground_net` (added just above, in the audit that first surfaced
this) were removed again, and `update_power_domain` now appends
`DomainSupplyConnection(domain, net, role="primary_power"/"primary_ground")`
instead -- the same roles UPF's `set_domain_supply_net` already produces,
so a format-agnostic Milestone 3 query for "what powers domain X" doesn't
need to branch on `source_format`.

**Caveat, not fully resolved, documented directly in `cpf/verbs.py`'s
`_update_power_domain`:** the CPF spec notes `-internal_power_net` is
"required when you use header cells," hinting it may specifically mean the
post-switch, load-side net rather than an unswitched upstream supply.
UPF's own `-primary_power_net` semantics are secondary-source-tier only
here (IEEE 1801 paywalled), so whether the two formats' "primary" nets
mean the exact same point in the power network -- pre- or post-switch --
hasn't been independently confirmed on the UPF side. Verify against a real
design with an actual power switch, on both formats, before relying on
this for anything beyond "which net logically belongs to this domain."

`SupplySet` remains the genuine "CPF has no equivalent" case; `PowerDomain
.boundary_ports` remains CPF-only too (no UPF flag found for it, unlike
the internal-net fields) -- neither of those needed to change.

**Confirmed unconsumed-flag rejection and version handling hold for CPF
too**, not just assumed to inherit correctness from 2a's tests of the
same shared machinery: the whole-design backstop (a CPF design that
issues zero power-intent commands and never resolves a version) is now
explicitly tested for CPF, not just UPF.

#### Recurring pattern: researched-but-unwired schema fields (second occurrence)

This is the **second** time fields verified against primary-source research
were read, described in this doc, and then not actually added to the
schema until a later audit caught the gap -- first with 2a's `PowerDomain`
fields (shutoff_condition/power_up_state/etc., described in the
"Accumulate-then-freeze" section during schema design, not added until the
2a feature-completeness audit), now again with `PowerDomain.boundary_ports`/
`internal_power_net`/`internal_ground_net` (read from the same
`create_power_domain`/`update_power_domain` spec text, same session even,
still not added until 2b's own build surfaced the gap while investigating
the `connect_supply_net -ports` question). Of those three,
`internal_power_net`/`internal_ground_net` didn't stay as added -- a closer
semantic comparison against UPF's `set_domain_supply_net` (prompted by the
`domain_supply_connections` open question directly above) found they were
the same concept as UPF's existing fields, not a genuine CPF-only pair, and
they were removed again in favor of unifying onto
`DomainSupplyConnection`. `boundary_ports` stayed, having no UPF match.
Building 2b also surfaced
several more of the same shape found proactively this time rather than
after the fact (`IsolationStrategy.condition`/`exclude_elements`,
`LevelShifterStrategy.exclude_elements`, `PowerState.is_default`,
`domain` relaxed to `Optional` on three strategy types since CPF's own
commands don't universally require it) -- but proactive catches during the
same build don't fully explain away the pattern; they're still fixes to
gaps that existed, not evidence the pattern won't recur a third time.

**Worth adopting**: a periodic habit of cross-checking the schema against
this doc's own command-syntax research (grep field names mentioned in
verified-syntax notes against `schema.py`'s actual fields), rather than
relying solely on catching gaps during whatever the next scheduled audit
happens to be. Not implemented as an automated check in this pass --
logged here as a process note, not a blocking item.

#### Recurring pattern: shared-code genericity claims only get validated by a second real consumer

A distinct lesson from the one above -- this one about *validating* shared
infrastructure, not about *syncing research into schema fields*. Two real
bugs found in `shared/` so far, both the same shape:

1. During the directory restructure (before 2b existed): `_freeze_all` had
   `source_format=SourceFormat.UPF` hardcoded, and `_ElaborationState`
   carried a UPF-only `_pst_supplies` field, despite both living in a
   module already labeled "confirmed format-agnostic."
2. While building 2b: `tcl_runtime.py`'s "verb before any version" error
   hardcoded the string `'upf_version'` in its wording -- caught only once
   a real CPF file actually hit that path and the message read wrong.

**Neither bug was reachable, or even meaningfully checkable, with only
UPF as a consumer.** A single consumer's tests can confirm shared code
*works*, but not that it's actually *generic* -- there's nothing to
contrast against, so a format-specific assumption baked into "shared"
code has no way to surface until a second, independent consumer exercises
the same path differently. This is a structural property of the
situation, not a carelessness problem to fix by "being more careful" --
the fix is procedural: **explicitly re-audit `shared/` for
format-specific leakage every time a new consumer is added**, rather than
trusting that passing tests for the first consumer already proved
genericity. Done once, deliberately, for 2b (grepped `shared/tcl_runtime.py`
and `shared/builders.py` for any UPF/CPF-flavored string or assumption
beyond docstrings/comments -- none found beyond the one already fixed).
The same audit will be worth repeating if/when a third power-intent format
or a second RTL-side consumer of some currently-single-consumer "shared"
module ever shows up.

### Recurring pattern: recognized-but-unwired options (a fourth, distinct class)

Different failure mode from fix #1 (a flag not in `options` at all,
rejected at parse time): here the flag *is* declared recognized, so it
parses without error, but no handler code ever reads its value -- silently
discarded despite passing validation. Checked mechanically (an AST-based
script cross-referencing every `_register(...)` call's declared options
against what its handler function actually reads via `_one`/`.get`/
subscript), not by re-reading the code by eye. Two categories came back:

- **False positives from the check itself**: several fields
  (`set_domain_supply_net`'s `-primary_power_net`/`-primary_ground_net`,
  `update_power_domain`'s `-internal_power_net`/`-internal_ground_net`)
  flagged as "unread" only because they're accessed through a loop
  variable (`for role, flag in (...): _one(parsed, flag)`), which a purely
  static string-literal check can't see. Confirmed actually wired at
  runtime by the existing passing tests for both.
- **Already-documented, intentional carve-outs**: `update_power_domain`'s
  bias-net/power-up-timing/library-binding options,
  `create_power_nets`/`create_ground_nets`'s IR-drop-limit/`-internal`
  options, and both `update_isolation_rules`/`update_level_shifter_rules`'s
  library-binding options -- all explicitly commented in `cpf/verbs.py` as
  the same "downstream analysis, not structural power intent" carve-out
  already established for UPF. Recognized-but-unread by design, not a gap.
- **Real, previously unflagged**: `set_instance`'s `-merge_default_domains`
  and `-port_mapping` are recognized options with no handler logic at all
  -- found by this audit, not previously identified. Alongside the
  already-known `set_design -ports` gap, these are logged as explicit
  follow-up items below.

**Direct conclusion: narrow, not systemic.** The mechanical check covered
every registered command in both `upf/elaborate.py` and `cpf/elaborate.py`
and found exactly one additional real instance beyond the one already
known (`set_instance`, above) -- not a broader pattern across 2a/2b. The
UPF side came back completely clean (its one flagged hit was the loop-
variable false positive). Worth having run once, given fix #1 doesn't
cover this failure mode at all; not worth a recurring dedicated audit the
way the `shared/` genericity check is, since there's no structural reason
(like "only validated by a second consumer") for this class to keep
recurring the way that one does -- it's a per-handler completeness check,
now done.

### Follow-up items for CPF, logged not built (surfaced during Milestone 3 planning)

- **`set_design -ports`** (declares CPF virtual/logic ports -- control
  signals with no RTL backing, e.g. isolation/retention control ports).
  Recognized as an option, value never read or stored anywhere.
- **`set_instance -merge_default_domains`/`-port_mapping`**. Recognized,
  never read. `-port_mapping` in particular maps a parent-scope's virtual
  ports to real drivers -- relevant to the same virtual-port question as
  `set_design -ports` above.
- **UPF's `create_logic_port`/`create_logic_net`** (UPF's own mechanism for
  the same concept `set_design -ports` covers on the CPF side -- confirmed
  during Milestone 3 planning research that this is not a CPF-only
  concept). Not implemented in 2a at all -- no command registered.
- **UPF's `set_scope`** (navigable current-scope command --
  `.`/`..`/absolute-`/`-path -- distinct from `load_upf -scope`). Not
  implemented in 2a at all.

**Decision: none of these block Milestone 3 from starting.** Virtual-
signal classification (one of seven kinds a Milestone 3 reference can
resolve to -- see the design writeup once M3 has its own doc) is additive:
the other six kinds (instance, port, net, black-box-port,
unresolvable-black-box, dangling) are fully exercisable against existing
fixtures, none of which declare virtual signals. Blocking M3's *start* on
finishing two separate, already-closed-milestone sub-features would be
inconsistent with how every other cross-milestone dependency in this
project has been handled -- natural data dependencies (elaborator output
feeding the next stage) are fine and expected; blocking on non-critical-
path completeness isn't, same reasoning as why `-on_state`/`-off_state` or
`connect_supply_net -ports` never blocked anything downstream.

Mitigation for the integration risk: M3's resolver takes a
declared-virtual-signal-names input as part of its contract from day one
(sourced from `PowerIntentDesign`, currently always empty since nothing
populates it yet) rather than hardcoding "no virtual signals exist."  When
`create_logic_port`/`set_design -ports` wiring eventually lands, real data
starts flowing into an already-designed slot -- zero changes needed to
M3's own resolution logic. M3's own test suite will carry an explicit,
logged note that VIRTUAL classification is designed but not exercisable
against real fixtures until that 2a/2b work lands -- an acknowledged gap,
not a silently-untested path.

### Milestone 3 planning: three flagged reference-classification uncertainties, resolved

Before starting M3's resolvers, three specific fields were flagged as
ambiguous between "resolves against RTL" and "resolves against the
power-intent design's own declared constructs" (intra-power-intent). Each
was required to be checked against real spec text/syntax, not assumed --
below is what each check actually found.

#### `RetentionStrategy.save_signal`/`.restore_signal` — format-divergent, do NOT assume UPF matches CPF

**CPF** (primary-source-confirmed, from the Si2 CPF 1.0 spec):
`create_state_retention_rule -save_edge`/`-restore_edge` take an
`expression` -- "a function of pins" -- the same free-text-boolean shape as
`PowerState.expr`. Out of scope for resolution, same non-goal as the
existing `PowerState.expr` carve-out above.

**UPF** (secondary-source-confirmed, IEEE 1801 paywalled): the real command
is `set_retention_control`, and its syntax is
`-save_signal {net_name <high|low|posedge|negedge>}` -- e.g.
`-save_signal {aon_wrapper/pmu/ret_en high}`. This is a bare hierarchical
signal name plus a level/edge keyword, **not** a boolean expression. UPF's
shape genuinely diverges from CPF's here -- confirmed independently rather
than assumed to match, per instruction. The `net_name` portion is, in
principle, RTL-hierarchy-resolvable the same way an isolation strategy's
`source`/`sink` domain elements are.

**New gap found while verifying this, not previously known:** 2a's
`_set_retention` (`upf/verbs.py`) registers `save_signal`/`restore_signal`
as plain (non-list) options
(`upf/elaborate.py`'s `set_retention` registration, `options={...,
"save_signal", "restore_signal"}`, deliberately **not** in
`list_options`). Tcl's `{...}` brace-grouping still delivers
`-save_signal {aon_wrapper/pmu/ret_en high}` as a single string argument,
so `_one(parsed, "save_signal")` currently stores the whole two-token blob
`"aon_wrapper/pmu/ret_en high"` verbatim -- it is never split into the
signal-name and level/edge parts. This was never verified against real UPF
`set_retention_control` syntax before now (2a's own docstring never claimed
a specific save_signal/restore_signal shape), so this isn't a regression --
it's a previously-unverified gap surfaced by this round's research.

**Decision:** `save_signal`/`restore_signal` are excluded from M3's first
resolver pass for *both* formats -- CPF's because it's a genuine
expression (real exclusion), UPF's because the elaborator-side capture
isn't yet clean enough to reliably split off just the signal-name portion
without the resolver doing its own ad hoc string-splitting to compensate
for an elaborator gap (root-cause fix belongs in 2a, not papered over
downstream). Logged as a CPF-parity follow-up item alongside the ones
above: fixing 2a to mark `save_signal`/`restore_signal` as list-valued
(mirroring how `-function`'s `{kind net}` pairs are already handled) and
storing the signal-name/descriptor as two distinct pieces is a real, scoped
2a task, not bundled into this pass.

#### `PowerSwitch.input_supply`/`.output_supply` — confirmed intra-power-intent, both formats

**CPF** (primary-source-confirmed): `create_power_switch_rule
-external_power_net`/`-external_ground_net` must reference a net already
declared via `create_power_nets`/`create_ground_nets` -- explicit spec
text: "Even if this net exists in the RTL or the netlist, it still must be
declared through this command if the net is referenced in other CPF
commands."

**UPF** (secondary-source-confirmed): `create_power_switch
-input_supply_port`/`-output_supply_port` must reference a supply
port/net already declared via `create_supply_port`/`create_supply_net` in
the same UPF file.

Both formats independently land on the same classification -- not assumed
from the CPF finding, checked separately, and they agree: these fields
resolve against `PowerIntentDesign.supply_ports`/`.supply_nets`/
`.supply_sets`, never against RTL. Included in M3's first resolver pass as
an intra-power-intent lookup.

#### `SupplyPort`/`SupplyNet` names vs. RTL — confirmed exclusion, logged not built

Supply ports/nets are frequently things the power-intent file *adds*
precisely because the RTL doesn't model them (e.g. a `VDD`/`VSS` supply
net with no corresponding RTL net, or a supply port synthesized for a
domain that has no dedicated RTL port). Checking `SupplyPort.name`/
`SupplyNet.name` against RTL ports/nets would spuriously fail on normal,
correctly-authored designs, not catch real bugs. Excluded from RTL-
hierarchy resolution entirely -- not a deferred gap, a permanent scope
decision for this construct.

## Critical scoping decisions (locked in)

### 1. No mixed UPF/CPF within one design

A single power-intent design is entirely UPF or entirely CPF — never both in
the same file set. This is a real simplification versus the RTL mixed-language
problem (1d): there is **no cross-format merge/splice orchestration needed**,
only a format detector (by extension/content) that picks a single elaborator
to run. Much closer to the language-detection gate used for 1e's `.lib`
resolution than to 1d's genuine dual-front-end reconciliation.

### 2. Full strategy coverage is non-negotiable — do not apply the RTL narrowing philosophy here

For RTL, narrowing internal-logic extraction to flops/latches (1h) was correct
*because RTL's behavior isn't the content the tool cares about* — it only
matters insofar as it feeds retention decisions. For UPF/CPF, the opposite is
true: **the strategies themselves are the actual content this tool exists to
build and edit.** There is no equivalent of "FSM extraction" or "SV classes"
to defer here. In scope from day one, both formats:

- Power domains
- Supply ports, supply nets, supply sets
- Power switches
- Isolation strategies
- Retention strategies
- Level-shifter strategies
- Power states / power state tables (PST)
- Domain-supply connections (`connect_supply_net`, `set_domain_supply_net`
  and CPF equivalents)
- Hierarchical scope / hierarchical power-intent assembly (UPF `load_upf`,
  CPF equivalent)

Likely-legitimate exclusions (to be confirmed once real parsing begins, same
as identifying testbench-only SV constructs in 1a) are strictly **downstream
analysis details, not structural power intent** — e.g. detailed electrical
characteristics (resistance values) on supply networks used for IR-drop
analysis. Do not assume this list is complete; confirm against real UPF/CPF
content as 2a/2b proceed.

### 3. UPF/CPF version support — RESOLVED

**Target: UPF 2.1, 3.0, 3.1 (IEEE 1801-2013 / -2015 / -2018). CPF 1.1, 2.0.**

#### UPF version ↔ IEEE 1801 mapping (confirmed)

| UPF version | IEEE standard |
|---|---|
| 1.0 | Accellera (pre-IEEE) |
| 2.0 | 1801-2009 |
| 2.1 | 1801-2013 (+ 1801a-2014 amendment, sometimes called "2.2") |
| 3.0 | 1801-2015 |
| 3.1 | 1801-2018 |
| 4.0 (not targeted, see tracked gaps) | 1801-2024 |

#### Real semantic deltas found (not just additive features)

- **1.0 → 2.0**: 2.0 is the first IEEE version; reoriented around IP
  development/refinement (successive-refinement methodology) rather than
  1.0's direct "power-supply-net-driven" style. This is also where UPF/CPF
  convergence starts — 2.0's command shapes were chosen to map more easily
  onto CPF concepts than 1.0's did.
- **2.0 → 2.1**: **12 commands and 26 options from 1.0 declared
  legacy/deprecated.** Power State Tables (`create_pst`/`add_port_state`/
  `add_pst_state`) marked legacy in favor of `add_power_state`. Clarified
  that supply-set handles are *local* — power states no longer transfer
  across supply-set associations (2.0 had no real semantics here, a genuine
  gap, not just missing polish). Added `-supply`/`-domain`/`-complete` to
  `add_power_state`. New simstate `CORRUPT_STATE_ON_ACTIVITY`. Introduced
  `available_supplies`.
- **2.1 → 3.0**: Power states overhauled again — **removed the predefined
  `DEFAULT_NORMAL`/`DEFAULT_CORRUPT` states, added new predefined `ON`/`OFF`,
  explicitly flagged backward-incompatible** in the source material. Added
  `UNDEFINED`/`ERROR` states and the definitive/refined power-state model.
  New `create_power_state_group` command for system-level composition of
  per-IP power states (2.1 had no way to express this at all).
- **3.0 → 3.1**: New simulation-control commands (`sim_assertion_control`,
  `sim_replay_control`, `sim_corruption_control`), no 3.0 equivalent. Formal
  **precedence rules** defined for the first time (which of two overlapping
  `set_isolation`/retention/level-shifter/repeater commands wins — 3.0 and
  earlier had no defined resolution). Isolation semantics changed from
  **port-based to path-based** application (`-location fanout` didn't exist
  before; a 3.0-style `-location parent` isolation could silently
  "collateral damage" unrelated fanout paths that 3.1 now net-splits around,
  or errors instead of guessing). `boundary_supplies` added, `available_supplies`
  (from 2.1) clarified.
- **Cross-cutting fact, directly shapes the elaborator's architecture** (see
  design question 1 below): `upf_version` is declared **per file**, controls
  **syntax** of subsequent commands in that file, and can appear multiple
  times across a design's file set — but **semantics are whole-design, not
  per-file/namespaced**. A design cannot coherently mix semantic versions.

Sources: [UPF 1.0/2.0/2.1/3.0/3.1: The Big Q (DVCon proceedings, Madhur
Bhargava, Mentor/Siemens)](https://dvcon-proceedings.org/wp-content/uploads/upf-1-0-upf-2-0-upf-2-1-upf-3-0-and-now-upf-3-1-the-big-q-which-is-the-right-standard-for-my-design.pdf)
(fetched and read directly — the semantic-delta bullets above come from this
deck), [IEEE 1801-2013: A bold step towards power format convergence (EDN)](https://www.edn.com/ieee-1801-2013-a-bold-step-towards-power-format-convergence/).

#### CPF version deltas (grounded against the primary Si2 CPF 1.0 spec — fetched and read directly, 108pp)

CPF uses `create_X`/`update_X` verb pairs (declare, then incrementally
refine) plus `define_X_cell` for library-cell binding:
`create_power_domain`, `create_isolation_rule`/`update_isolation_rules`,
`define_isolation_cell`, `create_state_retention_rule`,
`create_power_switch_rule`, `create_level_shifter_rule`,
`create_power_mode`/`update_power_mode`, `create_mode_transition`,
`set_cpf_version`. CPF's "power mode" is the direct terminology equivalent
of UPF's "power state" (same concept, confirmed by command shape, not just
naming similarity).

- **CPF 1.0** (ratified Mar 2007, primary spec read directly): the commands
  above — power domains, isolation/retention/switch/level-shifter rules with
  cell binding, power modes/mode transitions, nominal conditions, operating
  corners, analysis views. **No macro-modeling support at all** — no way to
  represent a black-box hard-IP power interface.
  Source: [Si2 CPF Specification v1.0](https://x-power-format.sourceforge.net/si2_cpf_v1.0_01-02-2007.pdf).
- **1.0 → 1.1** (Si2, Sep 2008): macro modeling added
  (`set_macro_model`/`end_macro_model`, packaging a hard macro's/hardened-IP's
  power interface — SRAMs, analog IP, third-party blocks), derived/base
  power-domain hierarchy, expanded hierarchical IP-reuse flow, expanded
  power-domain operating states (reverse/forward-biased states added to the
  on/off/shutoff set). Source (secondary, via search — not fetched directly,
  lower confidence than the CPF 1.0 findings; verify against primary CPF 1.1
  spec when 2b starts): [Si2 release Common Power Format v1.1](https://www.ciol.com/si2-release-common-power-format-v11/).
- **1.1 → 2.0** (Si2, Feb 2011): voltage-regulator and analog-port support
  added. **Si2 then contributed the entire CPF 2.0 standard to IEEE in Oct
  2011** — direct lineage into UPF 2.1/1801-2013, which is described in
  sources as "significantly closing the gap" between UPF and CPF via the
  successive-refinement methodology and macro-modeling concepts CPF had
  already established. This means CPF's macro-model concept and UPF's
  post-2.1 refinement model aren't just superficially similar — one was a
  direct design input to the other, which is useful for 2c (see design
  question 2). Source (secondary, via search):
  [A CPF User Perspective on IEEE 1801 (UPF) "Methodology Convergence" (Cadence blog)](https://community.cadence.com/cadence_blogs_8/b/ii/archive/2013/05/13/a-cpf-user-perspective-on-ieee-1801-upf-methodology-convergence),
  [Common Power Format (Wikipedia)](https://en.wikipedia.org/wiki/Common_Power_Format).

#### Decision and reasoning

**Target UPF 2.1, 3.0, 3.1.** **Target CPF 1.1, 2.0.**

**Tracked gaps, logged not blocking:**

- **UPF 1.0 — deferred.** Its constructs are declared legacy as of 2.1;
  building parsing support for behavior the standard itself has since
  retracted isn't a good use of v1 scope. Revisit only if a real legacy UPF
  1.0-only design shows up that can't be bumped to 2.1+.
- **UPF 2.0 — deferred.** Lacks the supply-set-handle-local semantics 2.1
  fixed — a genuine gap, not just missing polish (2.0 "does not restrict the
  transfer of power states in the supply set associations," per the
  research above). Targeting 2.1 instead means we never have to implement
  the known-wrong 2.0 behavior. Revisit only if a real 2.0-only design shows
  up and needs the pre-2.1 (looser, arguably buggy) semantics specifically.
- **UPF 4.0 / IEEE 1801-2024 — deferred.** Exists, reworks state-retention
  modeling and introduces a "virtual supplies" concept, but is very new
  relative to the others researched here. Revisit once 3.1 support is solid
  and there's a concrete design that needs 4.0-only constructs.
- **CPF 1.0 — deferred.** No macro-modeling support at all, so it can't
  represent a black-box hard-IP power interface — a real gap given RTL's own
  1e already needs the equivalent capability on the RTL side. 1.1 is a
  strictly better v1 target for the same implementation cost. Revisit only
  if a real CPF 1.0-only design shows up.

## Architecture: UPF and CPF are both Tcl-based command languages

This is the single most important architectural fact for this milestone, and
it changes the front-end approach compared to RTL entirely. UPF files are not
a fixed-grammar language — they are literally Tcl, with UPF verbs
(`create_power_domain`, `create_supply_net`, `create_isolation_strategy`,
etc.) implemented as domain-specific Tcl procs. A real UPF file can contain
Tcl variables, conditionals, loops, sourced sub-files, and procs. CPF is
built on the same paradigm. This means:

- **Do not hand-roll a parser/grammar for UPF or CPF.** That would mean
  reimplementing Tcl's own semantics (variables, control flow, `source`) —
  exactly the "from-scratch work" principle that ruled out pyVHDLModel in 1b.
- **The sound approach: use a real Tcl interpreter** (Python ships one via
  `tkinter.Tcl()`, no GUI required) and register the power-intent command
  verbs as instrumented procs that record their arguments into the IR, letting
  the real interpreter handle variables/control-flow/sourcing for free.
- **This handles hierarchical `load_upf` naturally** — it becomes "source
  another file with the current design-scope context set," which Tcl already
  does.
- **UPF and CPF likely share the same underlying Tcl-interpreter-runner
  infrastructure**, differing only in which command verbs are instrumented and
  how they map onto the shared 2c schema — analogous to how both RTL
  front-ends (1a pyslang, 1b GHDL+Yosys) independently normalize into one
  `ir/schema.py`.

### Tcl interpreter architecture spike — RESOLVED

Built a two-file UPF-shaped fixture (`main.upf` sourcing `sub_domain.tcl`,
mirroring `load_upf`) and ran it through `tkinter.Tcl()` (Tcl 8.6.17),
hands-on, not from docs:

- **(a) Custom procs for arbitrary command names — confirmed.**
  `interp.createcommand(name, handler)` registers a Python callback per
  UPF/CPF verb; args arrive as a plain Python tuple of strings, with all Tcl
  variable/loop expansion already resolved before the callback sees them
  (verified `$TOP_DOMAIN_NAME` and a `for` loop producing `PWR_0`/`PWR_1`/
  `PWR_2` args, both fully substituted).
- **(b) Nested sourcing with shared state — confirmed, and is the right
  model for `load_upf`.** A `load_upf` proc that just calls
  `interp.eval("source {path}")` on the *same* interpreter gives the sourced
  file full access to already-registered commands and already-set
  variables — verified a variable set in the parent file (`ENABLE_ISO`) was
  correctly visible inside an `if` in the sourced sub-file.
- **(c) Variables/conditionals — confirmed, work via Tcl's own semantics.**
  A `for` loop and an `if {$ENABLE_ISO}` conditional in the fixture both
  evaluated correctly with zero custom logic on our side.

**Two additional findings, both real gotchas, found beyond the three
originally-scoped spike points:**

1. **List-valued arguments arrive as raw, unsplit Tcl-list strings, not
   Python lists.** `-elements {inst_a inst_b {inst_c/sub}}` comes through as
   the single string `'inst_a inst_b {inst_c/sub}'`, not
   `['inst_a', 'inst_b', 'inst_c/sub']`. Every command handler that takes a
   list-valued option (`-elements`, `-instances`, `-domain_conditions`,
   etc. — most of them) needs `interp.tk.splitlist(...)` applied. Missed
   silently, this is a silent-wrong-answer bug: an unsplit
   `"inst_a inst_b"` string looks like one bogus element name instead of
   two real ones, not a crash.
2. **A raw Python exception raised inside a `createcommand` callback loses
   its message crossing back into Tcl.** `tkinter.TclError` propagates
   structurally (confirmed an unregistered command also raises cleanly, so
   "fail loud on an error" still works at the control-flow level), but
   `str(e)` on the caught error comes back **empty** — only Tcl's own
   `errorInfo` (which command line failed) survives, not *why*. Fix,
   confirmed to preserve the full message losslessly including special
   characters (braces/quotes/`$`, which a hand-quoted
   `interp.eval(f"error ...")` string would mangle): catch the Python
   exception inside the handler and re-signal it via
   `interp.tk.call("error", str(e))` instead of a bare `raise`.

**Hard requirement, not a suggestion:** both gotchas above must be handled
by **shared, centralized wrappers in the command-registration layer**,
applied automatically to every registered UPF/CPF command — never something
each individual command handler has to remember to do itself. Concretely:
the function that does `interp.createcommand(name, handler)` for each verb
wraps `handler` once, centrally, in (a) a Tcl-list-splitting normalizer for
declared list-valued options and (b) the exception-to-`error`-call
translator above, before registration. A single handler that forgets either
wrapper becomes a silent-wrong-answer bug (mis-split element list) or a
useless blank error message — exactly the failure mode this project's
"verify empirically, fail loud" discipline exists to prevent, and not
something to leave to per-handler discipline.

## 2c design questions — RESOLVED (schema itself proposed in chat, pending sign-off)

Answered before proposing 2c's schema, same checkpoint discipline as
pyslang/GHDL/liberty-parser.

### 1. Version-conflict detection: `upf_version` is declared per file, but semantics are whole-design

Design: the file-sourcing driver (the piece that walks the top-level file(s)
and follows `load_upf`/CPF's equivalent recursively) tracks, for every file
it actually sources, which version (if any) that file declared — via its own
source-stack bookkeeping around each `interp.eval("source ...")` call, not
via Tcl's `info script` (more robust, since we already control every
sourcing call site).

- The **top-level file must declare a version explicitly** — missing
  declaration is a clear error, not a guessed default.
- A sourced sub-file that **doesn't** redeclare inherits the
  currently-active design version. This matches real UPF authoring
  convention (only the top file usually declares it) and the "no per-file
  namespace" fact from the research above.
- A sub-file that **does** redeclare a version that **differs** from the
  currently-active one is the real conflict — raise a clear error naming
  both files and both versions, rather than silently picking the top's, the
  newest, or the last-seen. This is the literal case the research flagged
  ("It is not possible to follow multiple semantics for a same design").

Applied symmetrically to CPF's `set_cpf_version`, as shared, format-agnostic
infrastructure alongside the Tcl-runner (not duplicated per format) — but
flagged as an assumption pending verification: research confirmed
`set_cpf_version` exists as a command, not that its per-file-redeclaration
semantics exactly mirror UPF's. Verify against real CPF file sets once 2b
starts; the detection design above degrades safely either way (worst case,
CPF designs simply never trigger the conflict branch because they never
redeclare per sub-file).

#### Full algorithm, confirmed complete (closes a gap the original design left implicit)

The version-command handler (`upf_version` / `set_cpf_version`) is a single,
format-scoped choke point every declaration passes through, for every file
sourced (top-level or nested), in this order:

1. **Unsupported version check, first.** Is the declared string in the
   format's supported set (`{2.1, 3.0, 3.1}` for UPF, `{1.1, 2.0}` for CPF)?
   If not, raise immediately, naming the exact declared string — no
   distinction between "recognized but deferred" (1.0/2.0/CPF-1.0) and
   "genuinely unrecognized garbage": both are simply not in the supported
   set, both get the same clear, specific error rather than being silently
   processed under the wrong version's semantics or guessed at.
2. **No active version yet for this design?** This declaration resolves it
   — becomes `PowerIntentDesign.source_version`.
3. **Active version already resolved, this declaration matches it?** No-op
   (redundant but harmless re-declaration).
4. **Active version already resolved, this declaration differs?** The
   conflict case above — hard error naming both files and both versions.

Two more pieces needed to make "top-level file must declare a version"
actually fire at a useful point instead of vaguely "eventually":

- Any instrumented power-intent verb (`create_power_domain`, etc.) invoked
  before a version has been resolved for the design raises "must declare a
  version before any power-intent commands," immediately, at that command —
  not deferred to end-of-file. Plain Tcl bookkeeping (variable sets, control
  flow) before the declaration is harmless and allowed; `upf_version` being
  conventionally-first in real files is not the same as it being
  syntactically forced to be the literal first token, so this checks for
  the thing that actually matters (no power intent recorded under an
  undefined version) rather than a stricter, more fragile literal-position
  rule.
- Backstop at end-of-elaboration, **scoped to the whole design, not
  per-file.** Evaluated exactly once, after the entire file-sourcing tree
  (top-level file plus every recursively sourced file) has finished, against
  the single design-wide `source_version` slot — never per individual
  sourced file. A sourced helper/utility file that issues zero power-intent
  commands and never redeclares a version never fails on its own; nothing
  requires any individual sourced file to declare anything or issue any
  command. The backstop fires only if, after everything has been sourced,
  `source_version` was never resolved by *anyone* at all — which can only
  happen if zero power-intent commands were issued anywhere in the whole
  file set (the previous bullet's immediate check would already have caught
  the first real command otherwise). The true degenerate case: a top-level
  file that only sources pure-Tcl utility files (variables/procs, no
  UPF/CPF verbs) and never declares a version itself — not silently allowed
  to produce a design with an undefined version, but also never a reason to
  fail a legitimate helper file in isolation.

No caller-supplied version parameter anywhere (`elaborate(files)`, not
`elaborate(files, version=...)`) and no interactive "pick your version"
prompt — both would be redundant with, and could contradict, what the file
itself declares. `PowerIntentDesign.source_version` is a plain, non-optional
`str` set once during the freeze step (see the accumulate-then-freeze
section above) — visible on every result, never buried in a log.

### 2. Canonical semantic shape: normalize UPF and CPF up into one model, same as RTL normalizes Verilog/VHDL

**2c targets UPF 3.1's refined power-state/isolation model as the one
canonical shape.** Reasoning:

- 3.1 is the newest targeted version and is a *clarification* layer on
  3.0's model (precedence rules, path-based isolation), not a different
  model — a 3.0 file's constructs normalize into 3.1's shape near-trivially,
  since nothing 3.1 adds invalidates what a 3.0-only file means.
- 2.1 needs a real, one-directional translation: 2.1's legacy PST
  (`create_pst`/`add_pst_state`, tabular) becomes canonical `PowerState`
  entries — each table row becomes one `PowerState` whose expression is the
  conjunction of "supply == value" terms for that row's columns
  (mechanical, lossless: a table is strictly less expressive than the 3.1
  model, so nothing is lost translating up). Tagged with a provenance
  marker (`shape=PST_ROW`) so a legacy-tabular origin isn't erased from
  diagnostics even though the canonical shape has no table concept.
- CPF normalizes into the same canonical shape via a vocabulary mapping,
  not a parallel schema: CPF `power_mode`/`-domain_conditions` →
  `PowerState` (a named state defined by a boolean condition over domain
  states — structurally the same thing as `add_power_state -domain
  -logic_expr`, confirmed by command shape, not just naming). CPF
  `isolation_rule -from/-to` → `IsolationStrategy.source/sink` (matching
  UPF's `-source`/`-sink`). CPF `state_retention_rule` → `RetentionStrategy`.
  CPF `power_switch_rule` → `PowerSwitch`. CPF `level_shifter_rule` →
  `LevelShifterStrategy`. CPF `create_power_domain -instances` →
  `PowerDomain.elements` (matching UPF's `-elements`). This mapping is not
  a coincidence — research found CPF 2.0's methodologies were directly
  contributed into IEEE 1801-2013 (UPF 2.1), so the concepts were never
  independent to begin with.
- Every construct keeps a `source_loc` (file, and a `shape` tag on
  `PowerState` specifically, since a single UPF 2.1 file can legitimately
  mix legacy PST rows and native `add_power_state` calls) for provenance —
  but `source_format` and the design's resolved version live once on the
  top-level `PowerIntentDesign`, not repeated per construct, since "no
  mixed UPF/CPF within one design" (decision #1) and the version-conflict
  design above (one resolved version per design) both already guarantee
  those are whole-design facts, not per-construct ones.

## 2c schema — RESOLVED, implemented in `backend/elaborator/power_intent/ir/schema.py`

Approved and written. Three points were resolved during review, after the
initial proposal, verified against the primary CPF spec rather than left as
chat-only decisions:

### CPF 1.1's derived/base power-domain hierarchy — tracked gap, not a schema field

**Explicitly out of scope for 2c.** No field for it exists on `PowerDomain`.
Reasoning: the freely-available Si2 spec this doc's CPF research is grounded
in is CPF **1.0** — "derived"/"base" domain is a **1.1** addition known only
from secondary sources (a feature-list mention, no structural detail). CPF
1.0's spec text was checked directly (`grep -i "derived\|base.*domain"`) and
contains zero mentions. Inventing a schema field for a concept whose shape
can't be verified would be exactly the "guess on an unrecognized shape"
failure mode this project avoids everywhere else. Revisit once either a real
CPF 1.1 design or the actual CPF 1.1 spec text is available — until then,
this is logged here as the tracked gap, not decided only in chat.

### Accumulate-then-freeze: how Tcl-driven, multi-command construction meets a frozen schema

Every type in `schema.py` is frozen, but CPF's `create_X`/`update_X` pairs
build one named construct up across multiple commands arriving in file
order — confirmed concretely against CPF's real `create_power_domain`
(sets `-instances`, `-boundary_ports`, `-shutoff_condition`, save/restore
edges, `-power_up_states`) vs. `update_power_domain` (adds a *disjoint*
field set: `-internal_power_net`, bias nets, `-rail_mapping`, power-up
timing) keyed back to the same domain by name. No single command produces
a complete record.

Plan, now implemented for 2a in `backend/elaborator/power_intent/shared/
builders.py`: **mutable, internal-only builder dataclasses** — plain
`@dataclass`, not frozen — mirroring the public schema field-for-field, one
per named construct, keyed by `id` in ordinary dicts. `create_X` handlers
instantiate a new builder (error on duplicate name — CPF's spec is explicit
names must be unique). CPF's `update_X` handlers (2b, not started yet) will
look up the existing builder by name and mutate fields in place — no real
conflict logic needed there, update only ever adds fields create didn't
set, verified above.

**CORRECTED, retracted from the original version of this section:** this
section originally claimed UPF's own commands needed the same by-name
refinement as CPF's create/update pair — "a second `set_isolation` call
targeting the same strategy name needs actual precedence resolution."
**That's wrong**, found after 2a was actually built and the underlying
precedence example was re-derived directly rather than trusted from an
earlier summary of it. The one verified UPF 3.1 precedence example is:

```
set_isolation iso1 –domain pd –source pd1 –sink pd2
set_isolation iso2 –domain pd –source pd1
```
"the command with both -source and -sink has higher precedence than the
command with only one of -source or -sink" — this is about **two
differently-named** strategies (`iso1`, `iso2`) whose scopes happen to
overlap (both touch ports sourced from `pd1`), **not the same name called
twice.** No evidence was found that UPF's own commands (`set_isolation`,
`create_power_domain`, etc.) support by-name refinement the way CPF's
`create_isolation_rule`/`update_isolation_rules -names X` verifiably does.

**Conclusion: 2a's "already exists" rejection on a duplicate construct
name is correct UPF behavior, not a bug.** Reusing a strategy name for a
second, materially different declaration is a real authoring error worth
catching, not something to silently merge. `verbs.py` and `CLAUDE.md` have
been corrected to match — neither should claim this is an open gap.

**Tracked for Milestone 3, not 2a:** the actual source+sink-vs-source-only
precedence rule resolves *which of several distinctly-named strategies
with overlapping scope* applies to a given port. That requires knowing
real port/instance data to determine which strategies' scopes actually
overlap on a given port — data Milestone 2 structurally doesn't have
("Milestone 2 does not require RTL to be elaborated first"). This is
UPF↔RTL mapping's job, not the power-intent elaborator's — logged here so
it isn't lost before Milestone 3 starts. The other six of the seven
precedence categories the 3.1 research found (power domains, retention
strategies, name affixes, supply connections, attributes, simstates) are
unverified at the concrete-rule level and should be re-examined against
Milestone 3's actual needs when it starts, not assumed to belong to 2a
either.

A module named `precedence.py`, proposed in an earlier review round as a
home for UPF same-name merge logic, is **retargeted**: it will hold CPF's
verified create_X/update_X-by-name merging instead, once 2b starts. Not
built now — there is no verified 2a-scoped work for it.

After the whole file set (all `source`/`load_upf` calls) is walked, a
single `_freeze_all` pass converts every builder to its frozen counterpart
in one shot and assembles `PowerIntentDesign`. The builder layer is
entirely internal to the `upf`/(future `cpf`) elaborator packages, never
exported — `schema.py` itself stays 100%
frozen, exactly as originally proposed.

### `IsolationStrategy` — corrected after verifying against the primary CPF spec (the proposal's original mapping was wrong)

Re-fetched and re-read the CPF 1.0 spec directly rather than trusting the
earlier secondary-source summary. Two real corrections, both now reflected
in `schema.py`'s field comments, not just here:

- `-location {from|to}` is **not** on `create_isolation_rule` — it's on the
  separate `update_isolation_rules` command, and means "which domain's
  instance-list the isolation cell is counted under for implementation"
  (default `to`). This is a different axis than UPF's
  `-location {self|other|parent|fanout}` (topological placement relative to
  the domain boundary). `IsolationStrategy.location` keeps the field name
  as a raw passthrough for both, but the schema now documents explicitly
  that this is **not a verified semantic equivalence** — cross-format
  comparison of this field isn't safe without further validation-layer
  work.
- `create_isolation_rule`'s real `-from`/`-to` are **domain lists used as
  pin filters** ("output pins in `-from` domains," "input pins in `-to`
  domains," combinable), not a strict single-source/single-sink pair.
  `IsolationStrategy.source`/`.sink` still cover the common single-domain-
  each case cleanly; multi-domain CPF isolation rules are a tracked gap
  (revisit once real CPF content shows how common they are), not a
  speculative list-typed redesign. A distinct `-isolation_target {from|to}`
  (default `from`, controls *when* the rule triggers — driver domain off vs.
  load domain off) was also found and added as
  `IsolationStrategy.isolation_target`; no UPF equivalent has been verified
  yet.

**Confidence asymmetry worth remembering for 2a/2b:** CPF's shapes above are
now genuinely primary-source-verified (Si2's spec is freely downloadable).
UPF's are not — IEEE 1801 is paywalled, so UPF field shapes in this schema
rest on secondary/tutorial sources only. Don't treat UPF fields as
equivalently verified just because they sit next to CPF fields that are.

## Extended 2026-09-12 by the frontend's "Load Power Intent" mapping feature

Both elaborators were extended substantially by work that started on the
front end. The full writeup lives in
`docs/load_power_intent_mapping.md`; recorded here so this doc does not
silently describe a smaller elaborator than the one that exists.

The trigger: round-tripping the app's OWN generated UPF and CPF back
through these elaborators. Both failed at the very first construct. That
is the single most effective test either elaborator has had — it found
**thirteen** real defects across the two formats and the generator, none
of which any existing test covered.

**Extending the elaborator was chosen over narrowing the generator**,
confirmed explicitly before starting: the generated commands are correct
UPF/CPF, and the gap was in what had been implemented so far.

### UPF (2a)

Newly implemented, several of which this doc previously listed as
out-of-scope or not-yet-built: `set_upf_version`, `set_scope`,
`map_power_switch`, `set_level_shifter_cell`, `set_retention_control`,
`set_port_attributes`, `connect_supply_net -ports`, and
`-on_state`/`-off_state` switch state parsing.

Three **silent** parsing bugs, all one shape — a `{role value}` PAIR flag
read as a scalar, so the parse succeeded and produced nonsense:

- `create_power_domain -supply {primary SS_VDD}` produced a supply
  literally named `['primary', 'SS_VDD']`.
- `create_power_switch -input_supply_port {in VDD}` and
  `-on_state {on in {ctrl}}` likewise.
- `add_power_state -state {ON 1.2}` is both **repeatable** *and*
  pair-valued, so a three-state supply collapsed into a single state
  named `['ON', '1.2']`. This is a third instance of the
  repeatable-vs-list-valued conflation this doc already records as a bug
  class — worth treating as a standing hazard, not a one-off.

New in `shared/tcl_runtime.py`: `optional_positional` on `_register()`,
so `load_upf` accepts both `load_upf <file>` and `load_upf -file <file>`.
Removing the required positional to support the flag form broke seven
existing tests; a shared, centralized mechanism was the right fix,
consistent with that module's own four hard requirements.

### CPF (2b)

- `update_power_switch_rule` and `create_nominal_condition` were **not
  registered at all**, so Tcl rejected the command *name* rather than a
  flag. Both appear in this project's own
  `examples/upf_example_1_sram/cpf/golden.cpf`. Two of the three deferred
  `update_*` commands this doc logged.
- `update_power_domain` accepted only
  `-internal_power_net`/`-internal_ground_net` and rejected the
  `-primary_*` spelling golden.cpf uses. Per 2b's own recorded caveat,
  `-internal_*` is the NARROWER (possibly post-switch) concept, so
  accepting only it was the gap — not the generator inventing a flag.
  Both spellings now land on the same `primary_power`/`primary_ground`
  roles; which one a file used is not preserved.
- `update_isolation_rules` recognized neither `-isolation_power_net` nor
  `-isolation_ground_net`, also both in golden.cpf. The power net shares
  `isolation_supply` with UPF's supply-SET name; the ground net got its
  own new `IsolationStrategy.isolation_ground_net` rather than being
  dropped. **Consequence for consumers**: `isolation_supply` now holds a
  different KIND of name depending on `source_format` — a set name for
  UPF, a net name for CPF — so check the format before deciding which
  dict to resolve it against.
- `set_instance .` stored the literal `"."` as a scope, where 2a's own
  `set_scope` already normalised it to the root. Constructs written after
  it therefore got scope `"."` and stopped matching by-name references
  from the real top scope, visible as construct ids like `".::ISO_CPU"`.
  A real asymmetry between the two front-ends for the same convention.

### New IR fields (2c)

Mirrored through `shared/builders.py`, the frontend mirror, and the
frontend's own supported-features list: `PortAttribute` (a new
construct), `DomainSupplyConnection.ports`,
`PowerDomain.available_supplies`,
`IsolationStrategy.isolation_signal`/`.isolation_sense`/`.isolation_ground_net`,
`LevelShifterStrategy.applies_to`/`.rule`/`.threshold`/`.cell_type`,
`PowerSwitch.cell_type`/`.input_supply_port`/`.output_supply_port`,
`RetentionStrategy.primary_supply`.

### A standing finding about `backend/mapping/`

Not fixed, deliberately, and recorded because it will come up again. The
mapper resolves six reference fields as RTL hierarchy paths relative to
the construct's own scope, which is not what those references mean:
`set_isolation`/`set_level_shifter -elements` are ports of the referenced
DOMAIN (confirmed against golden.upf, where a root-scope `set_isolation
-domain PD_SUB -elements {o_q[31:0]}` names a bare port of a domain one
level down); `set_retention -elements` are registers, which the RTL
resolver structurally cannot resolve since it resolves only instances,
ports and nets; a switch's supplies are supply nets resolved by name, and
its `-control_port` is a cell pin name. All six produced confident false
`DANGLING` classifications. The frontend importer filters them
per-field (`_BUILDER_OWNED_FIELDS`) because the layer that understands a
reference should be the one reporting it — but the mapper itself is still
wrong about these, and a future consumer reading `MappedDesign.references`
directly will hit the same thing.

## Notes for whoever picks this up

- Same review-checkpoint discipline as Milestone 1: the Tcl-runner
  architecture is spiked and confirmed (above); the 2c schema is written
  (`backend/elaborator/power_intent/ir/schema.py`) and awaiting final sign-off before
  2a/2b's real Tcl integration code is written.
- Same "verify empirically, don't assume" standard throughout — Milestone 1's
  real bugs (sensitivity-list misattribution, VHDL sync-reset
  misclassification, binary-fanout assumption) were all caught by actually
  testing edge cases, not by design review alone.
- Log every version-support and scope decision here as it's made, the same
  way Milestone 1's doc tracked FSM extraction, interfaces, packages,
  distribution, and latch handling — don't let decisions live only in chat
  history.
