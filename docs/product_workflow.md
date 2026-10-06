# Product Workflow: End-to-End User-Facing Flow

Status: describes intended user-facing behavior once the Final Milestone
connects the back end and front end. Documentation only — this doc does
not change any milestone's scope or sequencing, and the milestone-
independence rule in `CLAUDE.md` still governs all actual implementation
work. See the closing section below.

## Goal

Capture, in one place, what a user of Power Intent Studio actually does,
end to end — tying together work that's scoped and built independently
across `CLAUDE.md`'s milestone structure. This is a product-level
narrative, not an engineering spec: `docs/milestone1_rtl_elaborator.md`
and `docs/milestone2_powerintent_elaborator.md` remain the source of truth for
their own sub-tasks' scope and implementation detail — this doc doesn't
duplicate or override either.

## The flow

### 1. Load the design

RTL — Verilog, SystemVerilog, VHDL, or a mixed-language hierarchy —
elaborated per Milestone 1, including optional `.lib` black-box macros
(1e) for hard IP with no RTL source.

This is a hard prerequisite for everything downstream, including building
power intent from scratch. Even with no existing UPF/CPF file at all, a
user attaching an isolation strategy to `u_core/out1` or a domain to
`u_core` needs those to be real instance paths/ports from an actually
elaborated design — power intent isn't authored in a vacuum.

### 2. Optionally load existing power intent

UPF or CPF — never mixed within one design (Milestone 2's locked-in
scoping decision). If a power-intent file set is loaded, it's cross-checked
against the elaborated design as a sanity check.

**That cross-check is Milestone 3's core job** (Mapping UPF/CPF ↔ RTL —
validating that referenced instance paths/ports actually exist in the
design, flagging inconsistencies), not new scope introduced by this doc.

### 3. Two paths from here

**Option 1 — power intent was loaded.** Visualize the design and its
power intent graphically (Front-End Milestones 1 and 2 — built and tested
against mock/fixture data until the Final Milestone, per the standing
independence rule; real data only flows in once the Final Milestone
connects `backend/api/`). From there, modify the loaded power intent.

**Option 2 — building power intent from scratch.** The user picks a
target format and version from what Milestone 2 actually supports today —
UPF 2.1/3.0/3.1 or CPF 1.1/2.0 (see
`docs/milestone2_powerintent_elaborator.md` for the version research behind
this list) — visualizes the empty-of-power-intent design, and builds
power intent interactively from there.

**Option 2 is not a separate code path.** "Building from scratch" is just
"the modification operations below, starting from an empty power-intent
IR" — there's no reason to special-case it as its own workflow once the
design is loaded and a target format/version is chosen.

### 4. Modification operations (Milestone 3b)

CRUD (add / update / remove) on:
- power domains
- power/supply ports
- power/supply nets
- supply sets
- power switches
- isolation strategies
- retention strategies
- level-shifter strategies
- power states / power state tables
- power state groups
- domain-supply connections

This list should track Milestone 2's actual IR schema
(`backend/elaborator/power_intent/ir/schema.py`) as that schema evolves —
it isn't its own independent spec. If the two drift, the schema is the
source of truth; update this list to match, not the other way around.
**Reconciled against the real schema during Milestone 3b planning**: the
original list predated 2c's finalized schema and was missing power
domains, supply sets, power state groups, and domain-supply connections
entirely. The original "port-to-net connections" entry didn't correspond
to any real schema construct -- there is no SupplyPort-to-SupplyNet
connection type -- and has been replaced with "domain-supply connections"
(`DomainSupplyConnection`, populated by `connect_supply_net`/
`set_domain_supply_net`), the actual connection concept 2c's schema
models.

### 5. Export

Download the resulting power intent in the target format and version.

## Two things worth calling out explicitly

### Export (IR → text) is new, unbuilt scope

Everything Milestone 2 has built so far (2a, 2b) goes one direction: text
→ IR (parsing an existing UPF or CPF file set into the canonical IR).
Export is the reverse: generating syntactically correct, version-aware
UPF/CPF text *from* the IR. It belongs in `backend/export/` (already
named in `CLAUDE.md`'s repo layout, not yet built).

This is very likely its own real translation problem, not a trivial
inverse of parsing. 2c's schema deliberately canonicalizes on UPF 3.1's
refined shape — normalizing UPF 2.1's legacy PST or CPF's differing
vocabulary *up* into that shape was the whole design (see
`docs/milestone2_powerintent_elaborator.md`'s design question 2). Serializing
*back down* to an older version's syntax — e.g., emitting UPF 2.1 from a
3.1-shaped `PowerState` — isn't automatically free just because the
up-normalization was designed carefully; it's a separate translation
direction that needs its own verification once real work on it starts.

This should get its own engineering spec doc, with the same
research-before-implementation discipline as `milestone1_rtl_elaborator.md`
and `milestone2_powerintent_elaborator.md`, once real work on export actually
begins — not be designed speculatively here.

**Confirmed during Milestone 3b planning, resolving a conflict with
`CLAUDE.md`'s repo layout comment** (which previously tagged `export/` as
Milestone 3b scope, contradicting this section): export is its own
future, milestone-adjacent piece with its own research pass, not folded
into 3b. Milestone 3b is in-memory `PowerIntentDesign` modification only
-- `PowerIntentDesign` in, a new (or updated) `PowerIntentDesign` out,
nothing written to disk. `CLAUDE.md` corrected to match this section
rather than the other way around, per this doc's own "if the two drift"
rule.

### Explicit non-goal: this is not signoff-grade verification

`backend/validation/` (cross-cutting, per `CLAUDE.md`'s repo layout)
catches simple, structural problems: dangling references (a strategy
naming a domain that doesn't exist), obvious inconsistencies, unhandled
domain crossings the loaded power intent doesn't account for.

**It is not attempting to replace or replicate formal, signoff-grade
power-aware verification** the way Cadence/Synopsys tools do (full
power-state-space exploration, electrical rule checking, simulation-based
low-power verification). Writing this down explicitly now, so
`backend/validation/` doesn't quietly grow into that territory later — it's
a sanity-check layer, not a verification product.

**Specifically out of scope, decided during Milestone 3 planning:**
expressions embedded in power-intent fields (e.g. `PowerState.expr` strings
like `"!pm_inst.pse_enable[0]"`) are raw text, not structured references.
Validation checks whether a *named* reference (an element path, a domain
name, a signal name given as its own field) resolves against the
elaborated design — it does not parse boolean/expression syntax to extract
and validate hierarchical references embedded *inside* an expression
string. That's real expression parsing with its own correctness surface,
well past "simple, structural" and into the signoff-verification territory
this section already rules out.

## This doc doesn't license early cross-wiring

Everything above describes the user-facing flow **once the Final Milestone
connects the back end and front end**. It does not change, and is not an
excuse to change, how any individual milestone gets built. `CLAUDE.md`'s
milestone-independence rule still governs all actual implementation
sequencing: back-end milestones don't reach into `frontend/`, front-end
milestones stay on mock data, and nothing outside the Final Milestone (and
`backend/api/`) is allowed to know about both sides at once. This doc is a
map of the destination, not a shortcut to it.
