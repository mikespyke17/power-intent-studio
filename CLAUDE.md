# Project Context for Claude Code

This file is read automatically by Claude Code at the start of every session in
this repo. It defines the project's purpose, structure, and — most importantly —
the **milestone execution rules** below. Read this before starting or resuming
any work.

## What this project is

A tool that:
1. Elaborates RTL (Verilog, SystemVerilog, or VHDL — possibly mixed within the
   same hierarchy) plus associated `.lib` macros, into a unified internal
   hierarchy/netlist representation.
2. Elaborates a UPF (Unified Power Format) power intent file, if one is provided.
3. Maps the elaborated UPF onto the elaborated RTL hierarchy.
4. Allows modification of the loaded power intent (UPF) based on user input.
5. Optionally, lets the user drive UPF modifications through natural language,
   via an LLM API integration (opt-in feature).
6. Provides a GUI to visualize the RTL hierarchy, block connections, and power
   intent, and eventually to drive the whole workflow interactively.

See `docs/product_workflow.md` for the end-to-end user-facing flow that
ties these milestones together (load design → optionally load power
intent → visualize/build → modify → export). It's a product-level
narrative, not an engineering spec — it doesn't change any milestone's
scope, and the independence rule below still governs implementation.

Back end and front end are developed as two independent tracks and only meet
at the very end. See the rule below — it is the most important operating
principle for this repo.

---

## HARD RULE: Milestones are independent until the Final Milestone

**Back-end milestones and front-end milestones are NOT linked to each other.**
Do not write code, design abstractions, or make architectural decisions in one
track that assume the existence of, or reach into, the other track — unless
you are explicitly working the Final Milestone (integration).

Concretely:

- While working on **any Back-End milestone**, do not import from, call into,
  or shape data structures around `frontend/`. The back end must be usable and
  testable entirely on its own (e.g., via CLI or a test harness) with zero
  knowledge of how or whether a GUI exists.
- While working on **any Front-End milestone**, do not import from, call into,
  or shape components around `backend/`. The front end must be buildable and
  demoable against **mock/fixture data** (fake hierarchy JSON, fake power
  intent data) with zero dependency on the real back end being implemented or
  even running.
- The **only** place where back end and front end are allowed to know about
  each other is the **Final Milestone: Integration**, and the `backend/api/`
  layer that the Final Milestone consumes. Nothing outside `backend/api/`
  should be front-end-aware, and nothing in `frontend/` should reach past that
  API layer into back-end internals.
- Do not "get ahead" and start wiring things together early because it seems
  convenient. If a milestone seems to require touching the other track, stop
  and flag it — that's a signal the milestone boundary needs to be revisited,
  not a reason to quietly couple the two tracks.
- **One sanctioned exception now exists, flagged rather than quietly
  taken**: Front-End Milestone 3's "Load Power Intent" mapping feature
  (2026-09-12, `docs/load_power_intent_mapping.md`) genuinely cannot be
  built against mock data, because its whole job is to read a real
  UPF/CPF file — a mock elaborator would only ever prove the importer
  agrees with the mock. It goes through `backend.api` via
  `frontend/src/backend_integration/`, exactly like the app's own entry
  point already does, so the **seam** is unchanged; what changed is that
  a Milestone-3 feature now depends on it, and its round-trip test suite
  (`tests/frontend/test_power_intent_round_trip.py`) is the second file
  under `tests/frontend/` that reaches the backend at all. Everything
  else in that milestone remains diagram-only/mock-first. Treat this as
  the bar for any future exception: the milestone's own purpose has to
  make mock data *meaningless*, not merely inconvenient.
- Within the back end itself, treat each milestone as sequential and mostly
  self-contained too (e.g., don't reach ahead into "modification" logic while
  building the "mapping" milestone). Some natural data dependency exists
  back-to-back (elaborator output feeds the mapper, etc.) — that's fine and
  expected. What's *not* fine is skipping ahead or building speculative glue
  for a milestone that hasn't started yet.

**Why this matters:** each side has its own pace, its own risk areas (parser
correctness on the back end, usability/rendering on the front end), and its
own test strategy. Mixing them early creates hidden coupling that makes it
hard to tell which side broke when something fails, and makes both tracks
harder to test in isolation. Keep the seam clean until the Final Milestone.

---

## Milestone structure

### Back-End Milestones

- **Milestone 1 — RTL Elaborator (incl. LIB macro integration)**
  Parse and elaborate RTL. Input can be Verilog, SystemVerilog, or VHDL,
  including mixed-language hierarchies. Normalize everything into a single
  language-agnostic internal representation (IR) before anything downstream
  touches it. Includes `.lib` (Liberty) macro integration.

  This is a summary — `docs/milestone1_rtl_elaborator.md` is the detailed
  companion doc (design decisions, tool evaluations, spike findings, open
  items) and is the source of truth if the two ever disagree.

  Sub-tracks:
  - **1a. Verilog / SystemVerilog front-end integration** — parse + elaborate
    (resolve includes, `` `define``/macros, parameters, generate blocks,
    packages) using an existing front-end rather than a from-scratch parser.
    **Status: done**, built on [pyslang](https://pypi.org/project/pyslang)
    (Python bindings for the `slang` SystemVerilog compiler).
    **Two real post-shipping fixes found via the validation harness**
    (`docs/backend_integration.md`), not during 1a's own build: explicit
    `top` now selects *any* named module as elaboration root via
    pyslang's `CompilationOptions.topModules`, not just pyslang's own
    auto-detected roots (previously broke the real "compile a DUT
    submodule out of a larger loaded design" workflow); elaboration
    errors now carry real `.file`/`.line` and a proper message via a new
    `ElaborationError`, instead of a bare `ValueError` naming only a
    diagnostic code. See `docs/milestone1_rtl_elaborator.md`'s "Post-1a
    fixes" section for the full writeup.
    **SystemVerilog interfaces/modports (`interface`/`modport`), originally
    deferred, are now implemented** — reopened after a real, complex
    personal SoC design used them pervasively as its primary connectivity
    mechanism, making the deferral a hard "Load Design" blocker rather than
    a hypothetical. New `Definition.is_interface`/`.modports`/
    `.interface_ports` schema (Verilog/SV only — VHDL has no equivalent
    construct). Found and fixed two more real, related gaps along the way:
    arrayed instances (`some_if bus [N] (...)`, and equally an arrayed
    *module* instance) were previously silently absent from the IR
    entirely (pyslang reports them as a distinct `InstanceArraySymbol`
    type the existing code never matched); a typedef'd port/net type
    (`pkg::byte_t sig;`) crashed with a bare `AttributeError` (needed
    `.canonicalType`, not `.type`, to reach `.range`). See
    `docs/milestone1_rtl_elaborator.md`'s "SystemVerilog interfaces —
    RESOLVED: implemented" entry for the full writeup.
    **`generate if`/`generate for` content was entirely invisible to
    elaboration** (found while investigating why LIB priority, 1e, had no
    effect on a real design): any instance, net, or sequential element
    declared inside a generate block -- not just some of them -- was
    silently absent from the IR, since nothing handled
    `GenerateBlockSymbol`/`GenerateBlockArraySymbol` as member kinds at
    all. Fixed via a new recursive `_walk_body_members()`, descending into
    only the *live* (`not isUninstantiated`) branch of a `generate if`
    and every live iteration of a `generate for` (instance names
    disambiguated with an `"array[index]."` prefix, since different
    iterations can declare identically-named instances). Verified against
    the real triggering design: previously-invisible SRAM macro instances
    now appear correctly, and LIB priority can now see and override them
    as designed. See `docs/milestone1_rtl_elaborator.md`'s "generate
    blocks were entirely invisible" entry for the full writeup.
    **An unconstrained interface port's internal signals were always shown
    as INOUT in the front end's Design Properties, even when real (found
    via real `aura` design use — `cpu_subsys.sv`'s own `axi_lite_if
    mem_port`, forwarded unmodified to a child instance's modport-qualified
    port, e.g. `axi_lite_mux2 u_mux (.m(mem_port))` with `m` declared
    `axi_lite_if.mst m`).** `InterfacePort.modport` being `None` at the
    port's own declaration is accurate SV — but the tool had no way to
    recover the *effective* direction the RTL author clearly intended
    (`mem_port`'s own comment: "AXI4-Lite master into the fabric"). Fixed
    with a new `InterfacePort.inferred_modport`, computed by matching
    `InterfacePortSymbol.connection`'s `(bound interface instance, modport)`
    identity against every child instance's own interface connections
    inside the same module body — confirmed via a live pyslang spike that
    this identity match holds even when the module itself is the
    elaboration root (pyslang synthesizes a default binding), so inference
    doesn't depend on how/whether the module is instantiated elsewhere.
    Only resolves when exactly one distinct modport is found among
    matching children — two children forwarding to conflicting modports
    stays unresolved, not guessed at. Verified end-to-end against real
    `aura` RTL (read-only): `cpu_subsys.mem_port` → inferred `mst`,
    `mem_subsys.bus` → inferred `slv`. The front end
    (`hierarchy_view/properties_table_widget.py`) now tries
    `modport or inferred_modport` before falling back to the raw-INOUT
    case, which remains for the genuinely unresolvable/ambiguous case.
    **Two further interface-connectivity fixes** (documentation gap closed
    2026-08-31, built 2026-08-16, found via the same real-design validation
    pressure as 1g's flattening work above): a dotted interface-member
    signal's own local continuous-assign driver was checked before its
    interface-member ascension, misordering which one won; a locally-declared
    interface instance had no identity to qualify member-connectivity
    queries against. Separately, a forwarding interface port two hierarchy
    levels from its real target was mishandled — fixed by re-qualifying at
    the same scope instead of fabricating a bogus child path — alongside a
    constant-indexed vector connectivity fix (`clk_dom[4]` now correctly
    merges with the whole-vector `clk_dom` net group). All four live in
    `backend/elaborator/rtl/ir/flatten.py`, mirrored in the frontend's own
    `connectivity_flatten.py` (see 1g above). Tests:
    `tests/backend/test_verilog_interface_member_connections.py` (5 tests) +
    3 new golden-fixture directories under `tests/backend/golden_rtl/`.
  - **1b. VHDL front-end integration** — same, for entities/architectures,
    generics, generate statements, packages.
    **Status: done**, built on GHDL + Yosys (`ghdl-yosys-plugin`): shells out
    to elaborate VHDL into a JSON netlist and normalizes that into the IR.
    pyGHDL's own Python bindings were evaluated and rejected for this
    (whole-design elaboration/generic-resolution is essentially unexposed
    there). End-user distribution/packaging for GHDL+Yosys is an explicitly
    **deferred, unresolved** decision — see the doc.
  - **1c. Unified IR + hierarchy builder** — the language-agnostic hierarchy
    representation both front-ends normalize into. This is the seam that
    keeps `mapping/`, `modification/`, and the frontend HDL-agnostic.
    **Status: done** (`backend/elaborator/rtl/ir/`).
  - **1d. Mixed-language instantiation resolution** — resolving instantiation
    across language boundaries (a VHDL entity instantiating a Verilog module
    or vice versa) within the unified IR.
    **Status: done**, single-direction (leaf) references only — an
    alternating Verilog↔VHDL↔Verilog chain is a tracked gap, not silently
    handled. Orchestrates 1a/1b's front-ends (`backend/elaborator/rtl/mixed_language.py`)
    rather than being a third parser; neither front-end was modified to know
    this module exists beyond a small, symmetric "report what couldn't be
    resolved instead of crashing or silently dropping it" extension each
    already needed to expose.
  - **1e. LIB macro integration (black-box scope)** — `.lib` files are used
    **only** to describe black-box hard macros (e.g. SRAMs, analog IP, hard IP
    blocks) that have no RTL source, so the elaborator can pick up their
    pin/interface info. This is explicitly **not** a standard-cell/synthesis
    Liberty reader — no timing/power-table parsing, no cell-mapping logic.
    **Status: done**, built on
    [liberty-parser](https://pypi.org/project/liberty-parser)
    (GPL-3.0-or-later — logged, not resolved, same treatment as GHDL's
    GPL-2.0-or-later dependency from 1b). `Definition.is_black_box` is now
    actually set (permanently, unlike 1d's transitional `UnresolvedInstance`
    mechanism, which never appears in a final `Design`); resolution matches
    RTL references to `.lib` cells by `base_name`, not `definition_id`, since
    a black-box `Definition`'s id is always computed with empty parameters.
    v1 scope is single-language RTL (Verilog-only or VHDL-only) + `.lib`
    resolution — composing this with 1d's cross-language resolution for a
    design needing both at once is a deliberately deferred, tracked gap, not
    silently unsupported. **LIB priority** (added on explicit request): when
    a module has *both* a real RTL body and a matching `.lib` black-box
    cell — a real, common pairing (a memory-compiler macro shipped with a
    simulation RTL model alongside its `.lib`) — the `.lib` version now
    wins. A genuinely separate mechanism from `resolve_black_boxes()`
    above: that function only ever sees instances with *no* real RTL at
    all, so a module with real RTL present was never offered to it in the
    first place. **`pg_pin` (power/ground pin) parsing** (added on
    explicit request, found via real `aura` design use through the front
    end's PortList view): a cell's `pg_pin` groups (e.g. `VDD`/`VSS`) were
    never read at all — only `pin`/`bus`. New `PortDirection.PG`/
    `PG_INTERNAL` values (previously mock-frontend-only) now come from a
    real `.lib` cell's `pg_type` attribute (`primary_power`/
    `primary_ground`/`backup_power`/`backup_ground` → `PG`,
    `internal_power`/`internal_ground` → `PG_INTERNAL`); any other
    `pg_type` (e.g. a well/bulk pin) raises rather than being guessed at.
    See the doc's 1e section for the full writeup.
    **`pg_pin` voltage resolution** (explicit request; found via real
    `aura` design use, this time through the Power Intent diagram's own
    macro PG-pin labels, which already had display logic for a known
    voltage but never actually received one from the real backend —
    `frontend/src/shared/models/rtl.py`'s own `Port.voltage` docstring
    already flagged this precisely as a tracked gap before this fix): a
    `pg_pin`'s own `voltage_name` attribute (e.g. `voltage_name : VDD;`)
    is a *reference* into the library's separate, repeatable
    `voltage_map (RAIL_NAME, value);` complex attributes (e.g.
    `voltage_map (VDD, 0.8);`) — not a literal number sitting on the
    pg_pin group itself, confirmed against real aura `.lib` macros. New
    `Port.voltage: Optional[str]` on the real IR schema (mirroring the
    frontend's own already-existing field, previously mock/diagram-only),
    populated via a new `_collect_voltage_map()` reading
    `library.attributes` directly (a *complex* attribute, repeatable —
    unlike `_collect_bus_widths()`'s `type()` *groups*, so it can't reuse
    that same `get_groups()` path). None (not guessed at) when a
    voltage_name has no matching voltage_map entry.
    **Quoted Liberty names fixed** (documentation gap closed 2026-08-31,
    built 2026-08-12): `liberty-parser` returns a quoted group name/arg
    (cell/pin/pg_pin/bus names, and later `related_power_pin`/
    `related_ground_pin`/`related_bias_pin`) as `EscapedString`, not `str`,
    and its own `__str__` re-wraps the value in literal quote characters —
    silently corrupting every quoted name in a real `.lib` file. Fixed via
    `_unwrap_liberty_string()`/`_unwrap_optional_liberty_string()`
    (`backend/elaborator/lib/elaborate.py`), applied at every name site.
    `tests/backend/test_lib_elaborate.py` (6 dedicated tests) +
    `tests/backend/golden_lib/quoted_names.lib`. **Well-bias pg_pin
    support** (implicit `nwell`/`pwell` pins with a `related_bias_pin`, e.g.
    VPB/VNB — non-connectable metadata; exposed pins with no
    `related_bias_pin`, e.g. VPW/VNW — real, connectable macro pins) and the
    resulting non-blocking "- Bias Conflict" warning are already documented
    accurately and in detail in `docs/macro_supply_set_golden_fixtures.md`
    (kept current through 2026-08-16) — not repeated here.
  - **1f. I/O extraction** — module/entity ports (direction, width, type) for
    every level of the hierarchy. **Status: done**, alongside 1a/1b.
  - **1g. Connectivity (hierarchical, with on-demand flattening)** —
    hierarchical instance-to-instance port connectivity is the **primary**
    representation (matches how UPF references instance paths). Full
    flattening (tracing a signal end-to-end across the whole hierarchy) is a
    **derived, on-demand operation** built on top of the hierarchical IR, not
    a separate parse/pass.
    **Status: done, both halves** (documentation gap closed 2026-08-31 —
    this was built starting 2026-08-14, under real design-correctness
    pressure rather than in this entry's own originally envisioned build
    order, and this status line went un-updated for ~2.5 weeks). Full
    end-to-end signal flattening — `trace_driver()`/`trace_receivers()` in
    `backend/elaborator/rtl/ir/flatten.py` — walks a signal back to its true
    origin or forward to every true sink, through combinational pass-through
    chains, arbitrary hierarchy depth, SV interfaces/modports, and
    sequential elements. 27 dedicated backend tests
    (`tests/backend/test_flatten.py`, `test_flatten_interface.py`).
    **Real, load-bearing gap, distinct from the above**: `backend/api/`
    does not currently re-export `flatten_design()`/`trace_driver()`/
    `trace_receivers()` at all — confirmed by reading `backend/api/__init__.py`
    directly (4 functions only: `elaborate_rtl`, `elaborate_power_intent`,
    `run_mapping`, `run_validation`). The real backend engine is therefore
    currently unreachable from the GUI, including the validation harness
    against real elaborated designs. The live product surface instead runs
    on `frontend/src/power_intent_view/connectivity_flatten.py`, a
    hand-duplicated, structurally identical copy of the same algorithm
    operating on the frontend's own mirrored `shared/models/rtl.Design` IR
    (imports zero backend code — the correct "duplicate, not import"
    pattern) — itself real, tested production code
    (`tests/frontend/test_connectivity_flatten*.py`, 5 files), just a
    second implementation of the same tracer that must be kept in sync by
    hand on every future fix (this has already happened 5 times since
    2026-08-14). See `docs/milestone1_rtl_elaborator.md`'s own 1g entry and
    `docs/backend_integration.md` for the fuller writeup of this two-engine
    situation and why it hasn't been reconciled yet.
  - **1h. Internal logic — flops/latches only** — identify sequential
    elements (flip-flops and latches) from `always`/`process` blocks, scoped
    specifically to support retention-cell insertion later (Milestone 3b).
    **No FSM extraction** — FSM detection is a heuristic, non-deterministic
    problem and is explicitly out of scope for this milestone. If a future
    need for FSM awareness arises, it should be scoped as its own milestone,
    not folded in here.
    **Status: flops done for both front-ends** (async-reset, resetless, and
    sync-reset — the last fixed after an explicit audit found VHDL sync
    reset was being silently misreported as no-reset, not just missing).
    **Latches: basic VHDL support done** (also fixed a real bug — GHDL
    previously crashed elaboration entirely on any latch, not just missed
    it); Verilog latches, latch-with-reset (either language), same-signal
    multi-block drivers, and set/preset distinction are explicitly deferred
    — see the doc's tracked-gaps entry for why each one is deferred and what
    would trigger revisiting it.
    **Post-1h fix found via real design use (Verilog/SV only):** one
    `always_ff` resetting/updating several unrelated registers together
    (idiomatic, common RTL — a real design had 46 `always_ff` blocks but
    only 4 were recognized before this fix) used to contribute *zero*
    `SequentialElement`s, not just miss some — the extractor only ever
    recognized a single assignment per if/else branch. Fixed by walking the
    whole statement tree under a block's clock/reset structure (both
    branches, nested blocks, `case` arms) instead of pattern-matching one
    shape; verified 4 → 147 sequential elements recognized on the real
    triggering design. See `docs/milestone1_rtl_elaborator.md`'s "Post-1h
    fix found via real design use" entry for the full writeup.

- **Milestone 2 — Power Intent Elaborator: UPF + CPF (if provided)**
  Parse and elaborate a power-intent file set into its own internal
  representation. Independent of Milestone 1 — does not require RTL to be
  elaborated first. See `docs/milestone2_powerintent_elaborator.md` for the full
  spec. Summary of locked-in scope decisions:
  - **UPF and CPF are separate formats, never mixed within one design** — a
    single design is entirely UPF or entirely CPF. No cross-format merge
    orchestration needed (unlike RTL's mixed-language problem, 1d) — just a
    format detector picking a single elaborator.
  - **Full strategy coverage is required from day one, for both formats** —
    power domains, supply ports/nets/sets, power switches, isolation
    strategies, retention strategies, level-shifter strategies, power
    states/PST, domain-supply connections, hierarchical scope (`load_upf` and
    the CPF equivalent). Unlike RTL's flop/latch narrowing (1h), there is no
    equivalent "not the content we care about" carve-out here — the
    strategies themselves are the actual power intent this tool exists to
    build and edit.
  - **Version support — RESOLVED via research.** Target **UPF 2.1, 3.0, 3.1**
    (IEEE 1801-2013/-2015/-2018) and **CPF 1.1, 2.0** — real semantic deltas
    exist between versions (e.g. UPF 2.1 fixed a genuine supply-set-handle
    semantics gap in 2.0; UPF 3.0 backward-incompatibly redefined predefined
    power states). UPF 1.0/2.0, UPF 4.0 (1801-2024), and CPF 1.0 are logged
    as tracked gaps, not silently unsupported — see the doc for reasoning
    per version.
  - **Architecture: both UPF and CPF are Tcl-based command languages —
    spiked and confirmed.** Do not hand-roll a parser — use a real Tcl
    interpreter (Python's bundled `tkinter.Tcl()`) with instrumented procs
    recording power-intent commands into the IR, mirroring why pyslang/GHDL
    were chosen over hand-rolled parsers for RTL. This also handles
    hierarchical `load_upf` for free (it's just `source` with shared
    interpreter state). Confirmed hands-on: custom command registration,
    shared-state nested sourcing, and Tcl variables/conditionals all work as
    expected. Two gotchas found and required to be handled by **shared,
    centralized wrappers in the command-registration layer** (never
    per-handler): (1) list-valued arguments arrive as raw unsplit Tcl-list
    strings, not Python lists — must be split centrally; (2) a Python
    exception raised inside a registered command loses its message crossing
    back into Tcl unless explicitly re-signaled via `interp.tk.call("error",
    str(e))`. See the doc for the full spike writeup.

  Sub-tracks:
  - **2a. UPF elaborator.** **Status: done, audited against the locked-in
    scope before starting 2b** (`backend/elaborator/power_intent/upf/`,
    built on the shared Tcl-runner in `backend/elaborator/power_intent/
    shared/`, canonical IR in `backend/elaborator/power_intent/ir/` —
    module layout mirrors `rtl/{verilog,vhdl,ir}/`, true siblings not
    nested). Covers every construct category (domains, supplies, switches,
    isolation, retention, level-shifters, power states incl. legacy-PST
    translation, domain-supply connections incl. `connect_supply_net`,
    `load_upf` hierarchy), the full version-handling algorithm, and the
    `-location fanout`-requires-3.1+ version gate. 71 tests.
    Real bugs/gaps found and fixed by testing and by the pre-2b audit, not
    assumed away — `load_upf` relative-path resolution, a repeatable-vs-
    list-valued-flag conflation, and (highest priority) a systemic gap
    where unrecognized flags on any command were silently dropped instead
    of rejected, now a third hard requirement in `tcl_runtime.py`. A prior
    round's "successive refinement is a bug" diagnosis was investigated and
    retracted — see the doc's "Accumulate-then-freeze" section.
    Deliberately out of scope, explicitly logged: UPF 3.1's three `sim_*`
    simulation-control commands (confirmed non-structural,
    simulation-only — see the doc). `create_logic_port`/`create_logic_net`
    (UPF's own virtual/logic-signal declaration mechanism — confirmed not
    CPF-only) remain unbuilt, surfaced during Milestone 3 planning.
    **Extended substantially 2026-09-12 by the frontend's own "Load Power
    Intent" mapping feature** (see that section under Front-End Milestone
    3, and `docs/load_power_intent_mapping.md`), which round-tripped this
    app's own generated UPF back through this elaborator and found it
    could not read it at all — starting at line 1. Extending the
    elaborator was chosen over narrowing the generator (confirmed
    explicitly before starting): the generated commands are correct UPF,
    and the gap was in what had been implemented. Closed there:
    `set_upf_version`, `set_scope` (both previously listed above as not
    built), `map_power_switch`, `set_level_shifter_cell`,
    `set_retention_control`, `set_port_attributes`, plus
    `connect_supply_net -ports` and `-on_state`/`-off_state` switch state
    parsing (also previously logged as out of scope above). **Three
    pre-existing SILENT parsing bugs found in the same pass**, all the
    same shape — a `{role value}` PAIR flag read as a scalar:
    `create_power_domain -supply {primary SS}` yielded a supply literally
    named `['primary', 'SS_VDD']`; `create_power_switch
    -input_supply_port {in VDD}` and `-on_state {on in {ctrl}}` likewise;
    and `add_power_state -state {ON 1.2}` is both **repeatable** *and*
    pair-valued, so a three-state supply collapsed into one state named
    `['ON', '1.2']` — a third instance of the repeatable-vs-list-valued
    conflation this file already documents as a bug class. Also new:
    `optional_positional` on `shared/tcl_runtime.py`'s `_register()`, so
    `load_upf` accepts both `load_upf <file>` and `load_upf -file <file>`
    (removing the required positional to support the flag form broke
    seven existing tests) — a shared, centralized mechanism, consistent
    with that module's own four hard requirements, not a per-handler
    special case.
  - **2b. CPF elaborator.** **Status: done, audited against locked-in
    scope** (`backend/elaborator/power_intent/cpf/`), reusing 2a's
    `shared/{tcl_runtime,builders}.py` as-is. Full command shapes verified
    directly against the Si2 CPF 1.0 primary spec. Two design questions
    resolved by verification, not assumption: CPF has no `load_upf`
    equivalent (`set_design`/`set_instance`/`end_design` form a flat
    sequence of scope-replacing blocks, not a nested stack — confirmed
    against a hand-written hierarchical fixture before finalizing); CPF
    doesn't need UPF's deferred `connect_supply_net -ports` construct,
    though investigating it surfaced real `PowerDomain` fields
    (`boundary_ports`, `internal_power_net`, `internal_ground_net`)
    researched but never added to the schema — the second such occurrence.
    Real `create_X`/`update_X` by-name merging in `precedence.py`
    (retargeted from an earlier mistaken UPF-scoped proposal), including
    dedicated isolated tests for the create→update-merges-onto-the-same-
    record case. **Pre-Milestone-3 audit found and fixed one real bug**
    (multi-file CPF was broken — raw Tcl `source` resolves relative paths
    against process cwd, no `load_upf`-style wrapper existed to fix it for
    CPF the way it had been fixed for UPF; fixed generically for both
    formats via a fourth `shared/tcl_runtime.py` hard requirement,
    `install_relative_source`, overriding `source` itself) and closed
    several real coverage gaps (multi-name `-nets`, isolation/level-shifter
    `-pins`/`-exclude`, retention's `-instances` form, a switch's
    `-external_ground_net` path). **Domain-supply-connection question
    resolved**: `internal_power_net`/`internal_ground_net` turned out to be
    the same concept as UPF's `set_domain_supply_net -primary_power_net`/
    `-primary_ground_net` (not a naming coincidence, unlike
    `IsolationStrategy.location`) — removed from `PowerDomain` again,
    unified onto `DomainSupplyConnection` with the same `"primary_power"`/
    `"primary_ground"` roles UPF already uses. Caveat, documented in
    `cpf/verbs.py`: CPF's spec hints this may specifically be the
    post-switch net (required "when you use header cells"), and UPF's own
    semantics here are secondary-source-tier only — verify against a real
    power-switch design on both formats before trusting full equivalence.
    86 tests total across UPF+CPF. Deferred, explicitly logged:
    `create_bias_net` (name confirmed, shape not yet read), wildcard
    `-names` matching, multi-domain `-from`/`-to`.
    Also surfaced during Milestone 3 planning (a distinct failure mode from
    the unrecognized-flag fix — these flags *are* recognized, just never
    read by any handler): `set_design -ports` and `set_instance
    -merge_default_domains`/`-port_mapping`, both relevant to virtual-port
    classification. Neither these nor 2a's own remaining
    `create_logic_port`/`create_logic_net` block Milestone 3 from
    starting — see the doc's Milestone 3 planning notes.
    **Six real gaps closed 2026-09-12 by the frontend's own "Load Power
    Intent" mapping feature** (see `docs/load_power_intent_mapping.md`),
    all found by round-tripping this app's own generated CPF back through
    this elaborator, which failed at the FIRST construct. Three were in
    this elaborator: `update_power_switch_rule` and
    `create_nominal_condition` were not registered at all — so Tcl
    rejected the command *name*, not a flag — even though both appear in
    this project's own `examples/upf_example_1_sram/cpf/golden.cpf` (two
    of the "three `update_*` commands" deferred above; the third,
    `update_isolation_rules`, already existed and gained the two flags
    below); `update_power_domain` accepted only
    `-internal_power_net`/`-internal_ground_net` and rejected the
    `-primary_*` spelling golden.cpf uses — per this bullet's own caveat
    above, `-internal_*` is the NARROWER (possibly post-switch) concept,
    so accepting only it was the gap, not the generator inventing a flag;
    `update_isolation_rules` recognized neither `-isolation_power_net`
    nor `-isolation_ground_net`, also both in golden.cpf (the power net
    shares `isolation_supply` with UPF's supply-SET name, the ground net
    got a new `IsolationStrategy.isolation_ground_net` rather than being
    dropped — a consumer must check `source_format` before deciding which
    dict to resolve `isolation_supply` against). Plus one real
    inconsistency with 2a: `set_instance .` stored the literal `"."` as a
    scope, where UPF's own `set_scope` already normalised it to the root
    — so constructs written after it got scope `"."` and stopped matching
    by-name references from the real top scope, visible as construct ids
    like `".::ISO_CPU"`.
  - **2c. Unified power-intent IR schema** — design first, same discipline as
    1c, validated against hand-written UPF *and* CPF examples before either
    front-end's real Tcl integration starts. Canonicalizes on UPF 3.1's
    refined power-state/isolation model; UPF 2.1's legacy PST and CPF's
    differing vocabulary (`power_mode`, `isolation_rule`, etc.) normalize up
    into it, the same way RTL's IR normalizes Verilog and VHDL regardless of
    source language. **Status: done.**

- **Milestone 3 — Mapping UPF ↔ RTL**
  Map the elaborated UPF (Milestone 2) onto the elaborated RTL hierarchy
  (Milestone 1). This is the first point where RTL and UPF representations
  meet. Includes validation/consistency checks (e.g., UPF references to RTL
  paths that don't exist).

  See `docs/milestone3_mapping.md` for the full design writeup.
  **Status: resolvers done** (`backend/mapping/`) — `ResolvedReference`/
  `MappedDesign`, a two-resolver split (RTL-hierarchy path resolution via
  `Design.walk()`; simple by-name lookup for references that point at the
  power-intent design's own constructs, not RTL). Seven-way
  `ResolutionKind` for RTL references (instance/port/net/black-box-port/
  unresolvable-black-box/virtual/dangling), verified against real
  elaborated fixtures including a black-box (.lib) case and partial-list
  resolution (some entries resolve, others genuinely dangling, in the same
  field), not just hand-built IR. A handful of reference-shaped fields
  (`PowerDomain.primary_supply`, the three `*_supply` fields, `PowerState.
  supply`/`.applies_to`, `PowerStateGroup.members`) have no independently
  verified target dict yet — an eighth `ResolutionKind.NOT_ATTEMPTED`
  (added after an initial version silently skipped these fields entirely,
  caught during review) makes that an explicit, visible fact in
  `MappedDesign.references` rather than an invisible gap — see the doc.
  **`backend/validation/` — status: done** (`backend/validation/
  {schema,validate}.py`). Turns a `MappedDesign` into a `ValidationReport`
  (findings list, not a pass/fail gate — matches `docs/product_workflow.md`'s
  "not signoff-grade verification" framing). Severity mapping: `DANGLING`
  → ERROR, `UNRESOLVABLE_BLACK_BOX` → WARNING, `NOT_ATTEMPTED` →
  EXCLUDED (its own tier, never merged into error counts), everything
  else → no finding. `NOT_ATTEMPTED` findings are deduplicated by
  `(construct_kind, field)` with a count, not emitted one per instance —
  see the doc's `backend/validation/` section.

- **Milestone 3b — Power Intent Modification**
  Apply user-driven modifications to the loaded/mapped power intent.
  In-memory `PowerIntentDesign` modification only -- `PowerIntentDesign`
  in, a new (or updated) `PowerIntentDesign` out. Serializing back to a
  valid file is explicitly **not** this milestone's scope -- corrected
  during Milestone 3b planning after a conflict with
  `docs/product_workflow.md`'s own Export section was caught; export is
  its own future, milestone-adjacent piece with its own research pass
  (`backend/export/`), not folded into 3b. See
  `docs/product_workflow.md` for the full reasoning.

  See `docs/milestone3b_modification.md` for the full design writeup.
  **Status: done** (`backend/modification/{errors,ops}.py`). Generic
  add/update/rename/delete/change_scope dispatch over a construct-kind
  string, full-rebuild via `dataclasses.replace()` (never mutates a
  `PowerIntentDesign` in place — mutation would let an already-computed
  `MappedDesign` silently go stale). `update()` structurally rejects
  identity fields (name, scope, and for a UPF `PowerState`, `applies_to`
  too — its id is format-dependent, confirmed against the real verb
  handlers, not assumed). `rename` cascades and `delete`/`change_scope`
  block using `MappedDesign`'s `POWER_INTENT`-kind references, verified
  cross-scope-collision-safe against a real two-domains-same-name fixture.
  `version_gates.py` reused at edit time, not just parse time.
  **Known, explicitly logged gap**: rename/delete/change_scope are blind
  to the same six `NOT_ATTEMPTED` fields Milestone 3's mapping doc already
  tracks (`primary_supply`, `isolation_supply`, `retention_supply`,
  `PowerState.supply`/`.applies_to`, `PowerStateGroup.members`) — see the
  doc's dedicated section, not a footnote.

- **Milestone 4 — LLM-API Integration (optional, user-enabled)**
  Integrate an LLM API into the Python back end to let users drive UPF
  modifications via natural language. Off by default; enabled explicitly
  through the API. LLM output must be validated before being applied — no
  blind writes to the power intent data.

### Front-End (GUI) Milestones

- **Milestone 1 — Hierarchy Visualization + Block Connections**
  Visualize design hierarchy and block-to-block connections using **mock/fixture
  data only**. No dependency on the real back end.

  Built as a native PySide6 desktop app (chosen for its LGPL license,
  consistent with how GPL exposure has been tracked elsewhere in this
  project — GHDL, liberty-parser), not a web frontend — `frontend/src/`
  is still Python modules. **Status: done**, delivered together with
  Milestone 2 in the same pass — see Milestone 2's doc pointer below for
  the shared module structure (`app/`, `shared/models/`,
  `shared/mock_backend/`, `shared/diagram/`, `diagnostics/`) both
  milestones sit on.

  "Load Design" tab, refined after real personal use through the
  validation harness (`docs/backend_integration.md`): a `QSplitter` with
  "Design Files" (Browse RTL/LIB Files, filtered to expected extensions
  only; a staging list; its own "Remove Selected"; "Add" transfers
  staged picks into "Loaded") and "Loaded" (its own separate "Remove
  Selected"; Top Module field, required for real elaboration; "Elaborate";
  "Continue", disabled until a successful elaborate). Elaborating does
  **not** auto-navigate — the app stays on "Load Design" until "Continue"
  is clicked, so warnings from a successful-but-not-clean elaborate stay
  visible in the diagnostics dock before moving on; only "Continue" jumps
  to "Visualize Design" and disables "Load Design" (a one-shot flow from
  that point — no way back without restarting). Diagnostics dock columns
  are Severity/Code/Message/**File**/Line (not Column) — `File` was
  added and `Column` dropped, matching what RTL/UPF/CPF `SourceLoc` shapes
  actually carry (see `shared/models/diagnostics.py`'s own docstring for
  the exact availability table). **The diagnostics dock itself was later
  restructured under Milestone 3** — see that section's own "App shell"
  paragraph — from one always-visible `MainWindow`-level dock switched by
  active tab into a dedicated dock embedded in this tab itself ("Load
  Design Diagnostic"), hidden until a warning/error actually appears; the
  columns/content described above are unchanged, only where the dock
  lives.

- **Milestone 2 — Hierarchy + Power Intent Visualization**
  Extend the hierarchy view to also show power intent (domains, rules, etc.),
  still against mock/fixture data.

  See `docs/frontend_milestone2_power_intent_visualization.md` for the
  full design writeup. **Status: done, visualization only** — power
  domains as translucent regions, switches as icons with supply
  connectors, isolation/level-shifter as pin-level badges, retention as
  instance-level badges, power states/PST deliberately not drawn
  (tabular-only). Interactive editing was explicitly deferred to its own
  follow-up pass here — **picked up under Milestone 3 below**, but as a
  genuinely separate, diagram-only overlay system (`power_intent_view/
  supply_port_overlay.py`/`power_domain_overlay.py`) layered on top of
  this one, not an extension of *this* MappedDesign-driven visualization
  itself (which stays exactly as built here, read-only). Overlaid on the
  *same* hierarchy diagram as Milestone 1 via a shared scene builder
  (`shared/diagram/hierarchy_scene_builder.py`), not a separate
  rendering. Requires a
  mocked `MappedDesign` (not just a `PowerIntentDesign`) to render the
  overlay at all — the diagram shows a bare-hierarchy-plus-placeholder
  state until the mocked Mapping step has run once. `docs/product_workflow.md`'s
  CRUD list gap (missing `SupplySet`/`PowerStateGroup`/`DomainSupplyConnection`,
  found during Milestone 3b planning) surfaced again here in the
  properties panel — resolved the same way, folded into the closest
  relative tab.

- **Milestone 3 — GUI Usability / Interactivity**
  Make the GUI actually usable end-to-end as an interactive tool (editing,
  navigation, diff views, etc.), still decoupled from the real back end.
  Includes the **chatbox UI** — the front-end counterpart to the optional
  LLM-API integration (Back-End Milestone 4). The chatbox lets the user type
  natural-language requests for UPF modification and see responses/results
  in-line. Build and test it against **mocked chatbox responses** (canned or
  scripted replies), same as the rest of the front end — it does not talk to
  the real LLM/back-end API until the Final Milestone.

  **Status: substantially done for Hierarchy Tree/Diagram interactivity
  and the Power Intent diagram's own interactive editing; diff views and
  the chatbox UI not started.** Everything below is diagram-only/mock-
  first, same discipline as Milestone 2 — confirmed via explicit
  AskUserQuestion scope decisions at each major addition, never wired to
  `backend/modification/ops.py` or a real `PowerIntentDesign`.

  **Hierarchy Tree/Diagram** (Visualize Design and Power Intent tabs,
  which now share full feature parity): Ctrl+click multi-select,
  Hide/Unhide with Copy Path and Ctrl+Z/Ctrl+Y undo, mouse-wheel zoom,
  click-drag pan, MaxRects bin-packing hierarchy layout (replaced an
  earlier skyline packer), and multi-tab diagrams
  (`shared/diagram/hierarchy_diagram_tabs.py`) — double-clicking a
  hierarchy box, or "Visualize Diagram" from the Tree's own right-click
  menu, opens that instance's own sub-hierarchy in a new closable tab
  alongside the always-open, non-closable whole-design one.

  **Power Intent diagram interactive editing**
  (`power_intent_view/{supply_port_overlay,power_domain_overlay,
  create_power_domain_dialog,power_intent_diagram_view,properties_panel}.py`):
  - *Add/Edit/Delete Power Supply* — an icon anchored to a hierarchy
    box's own boundary (draggable along all 4 edges, rotating per edge),
    its own draggable name label, connectable to nested supply ports via
    colour-matched, obstacle-avoiding orthogonal connectors (routes
    around macro boxes, not through them) with per-segment dragging and
    right-click Delete. A macro's own PG pins are auto-derived as fixed
    (undraggable, name/position locked) supply ports, colour following
    whatever they're connected to, voltage shown on the label when known.
  - *Create/Edit/Remove Power Domain* — name/colour/transparency
    (default 50%), a required Primary and optional Secondary Supply
    Set(s) (inline "create new" for both a Supply Set and, within it, a
    supply name — reusing the exact same standalone "Add Power Supply"
    dialog rather than a bespoke name+colour prompt), N-Well/P-Well
    required to match either Power or Ground. Domain colouring cascades
    down a box's own subtree, excluding any nested box that already owns
    its own (more specific) domain — order-independent, resolved by
    owner-path depth, not creation order. Domain names are unique
    diagram-wide. A macro's own box colour never changes, unconditionally.
  - *Undo/redo* — Ctrl+Z/Ctrl+Y for every edit above, sharing the exact
    same `QUndoStack` `HierarchyTreeWidget`'s own Hide/Unhide already
    pushes onto, so both kinds of edit undo/redo uniformly.
  - *Properties panel* — "Supply Ports/Nets" split into dedicated
    "Supply Ports" and "Supply Sets" tabs (the latter gaining real
    Power/Ground/N-Well/P-Well columns instead of one squeezed-together
    string); both, plus "Power Domains", now merge the diagram-only mock
    state in alongside real `PowerIntentDesign` rows, with names
    prefixed by their owning hierarchy (root-level entries shown
    unprefixed).

  **App shell** (`app/main_window.py`): a File/View menu bar (File
  holds exactly one action, "Create Project"; View grouped one
  disabled-header-plus-actions section per tab, each holding that tab's
  own dock(s) as a `toggleViewAction()`, gated on that tab's own
  `isTabEnabled()`) — explicitly the same in-window menu bar on every
  platform, not macOS's native screen-top one. **"View" itself isn't
  inserted into the menu bar at all until a project exists** (explicit
  request, tightened from an earlier merely-disabled version — the whole
  top-level menu is absent, not just greyed out); `QMenuBar.addMenu(view_menu)`
  runs once, inside `_on_create_project()`, the first time a project is
  created. Each dock's own `toggleViewAction()` is also wired to
  `dock.raise_()` on a real hidden→shown transition (explicit request: a
  dock buried behind a sibling in the same tabified group must come to
  the front when re-selected from "View" — Qt's `setChecked(True)` is a
  documented no-op when already checked, so this only fires on a genuine
  off→on transition, not a single click on an already-visible-but-buried
  dock's entry). Each of the four tabs is now a `QMainWindow` (not a
  `QWidget`) so it can host its own docks: Load Design/Load Power Intent
  each get one dedicated diagnostics dock ("Load Design
  Diagnostic"/"Power Intent Mapping Diagnostic"), hidden by default and
  auto-shown the moment that tab's own action produces a warning/error —
  replacing the single, always-visible `MainWindow`-level dock switched by
  active tab that used to exist. A Mapping run that produces a
  warning/error now also stays on "Load Power Intent" (a new
  `continue_button`, mirroring "Load Design"'s own already-established
  gate) instead of auto-navigating away in the same tick the dock just
  appeared, which used to make it instantly inaccessible. Visualize
  Design/Power Intent get no diagnostics dock at all; instead their own
  Hierarchy Tree and Properties panel became QDockWidgets (visible by
  default, closable/reopenable) instead of QGroupBoxes inside a
  QSplitter — Diagram stays a QGroupBox as each tab's own central widget.

  **Properties panel table filtering** (explicit request — "in all tabs I
  would like to have a way to filter the tables"; a new
  `shared/widgets/table_filter.py`, `TableFilterBar`/`wrap_with_filter()`):
  every table in both Design Properties (PortList/Sequential) and Power
  Intent Properties (all 8 primary construct-kind tabs plus the two
  secondary tables, Domain-Supply Connections and Power State Groups) now
  has its own free-text filter bar above it — complementary to, not a
  replacement for, PortList/Sequential's pre-existing category
  `FilterButton` checkboxes. `.reapply()` re-asserts the current filter
  after a table repopulates (`setRowCount()`/`setItem()` doesn't reliably
  preserve per-row hidden state on its own). Every table's header row is
  bold (explicit request), via `QHeaderView.setFont()`. Wrapping each
  table in its own filter-bar container broke
  `PowerIntentPropertiesPanel.setSelection()`'s `QTabWidget.setCurrentWidget(table)`
  (silently no-ops once `table` stops being the tab page's own direct
  child) — fixed by switching to `setCurrentIndex()` via a reverse lookup
  on the existing `kind -> tab index` mapping.
  **Refined on a follow-up explicit request**: matching is glob-style
  (`fnmatch.fnmatchcase`), not a bare substring — a pattern with no
  wildcard character requires an *exact* (case-insensitive) match against
  a cell's own text ("test" only matches a cell reading exactly "test",
  not "latest"), while `*test*` reproduces the original any-substring
  behavior; fnmatch already implements exactly this split (no wildcard
  chars degenerates to plain equality) with no extra branching needed. An
  "Apply to" `QComboBox` next to the text box (`TableFilterBar` is now a
  composite `QWidget`, not a bare `QLineEdit`, with `text()`/`setText()`
  proxy methods so existing direct-QLineEdit-style call sites needed no
  changes) restricts matching to one specific column, in place of the
  default "All Columns". Every table also gained real click-to-sort
  (`QTableWidget.setSortingEnabled(True)`, toggled off/on around each
  table's own repopulation, the same guard PortList's table already used
  to stop mid-populate re-sorts from scattering rows into the wrong
  index). Sorting reorders which row a cell's content lives at, but
  `setRowHidden()` is tracked by the view at a fixed row index — without
  re-asserting the filter afterward, hidden flags would end up attached
  to the wrong rows post-sort; fixed by having `TableFilterBar` itself
  listen for `QHeaderView.sortIndicatorChanged` and re-apply. Separately,
  Design Properties' own PortList/Sequential `FilterButton`s were renamed
  "Filter" → "Visualize" (explicit request, functionality unchanged) and
  gained a default-checked "All" entry, kept bidirectionally in sync with
  the individual category checkboxes (checking "All" checks every
  category; unchecking any one unchecks "All"; manually re-checking every
  category re-checks "All") — a new `FilterButton(include_all=..., label=...)`
  pair of parameters, opt-in so `power_intent_view`'s own pre-existing
  Diagram category filter (a different, non-table feature) is untouched.

  **Launch flow / branding** (explicit request, `app/welcome_page.py` +
  `frontend/icons/power-intent-studio-logo.svg`): the window opens on an
  EDA-tool-style `WelcomePage` (logo + app name, styled dark — distinct
  from the rest of the app's own default light theme, common for EDA
  tool splash/landing screens), the central `QStackedWidget`'s page 0 —
  `self.tabs` (page 1, already fully constructed) only becomes the
  visible page once "File > Create Project" succeeds (prompts for a
  project name; a blank name or Cancel leaves the window untouched).
  Landing always lands on "Load Design" (tab 0, already enabled). "View"
  isn't in the menu bar at all until that point either (see the App shell
  paragraph above — nothing meaningful to show/hide without a project
  yet), on top of its own already-established per-group tab-enablement
  gating. The same logo
  also sets the window/title-bar icon. `create_project_action` disables
  itself afterward — one-shot, matching this window's own established
  never-go-back navigation pattern (no "close project" flow exists to
  reset the four tabs' internal state back to empty).

  A handful of real, non-obvious bugs found via live use (screenshots
  and manual QTest-driven repros, not just unit tests) along the way:
  Qt's own "a child always stacks on top of its parent" rule hiding a
  deeply-nested handle behind a bare top-level drag line despite a much
  higher local Z value (fixed by walking the full `items(pos)` stack
  instead of trusting `itemAt(pos)`); `QUndoStack.push()`'s documented
  synchronous first `redo()` destroying a just-created item the caller
  was about to return; a shiboken signature-introspection recursion bug
  triggered specifically by monkeypatching a PySide static method and
  then invoking it from inside a live Qt signal/slot dispatch; a `QMenu`
  returned by `QMenuBar.addMenu()` having its underlying C++ object torn
  down once its Python wrapper went out of scope, despite Qt's own C++
  parent-child ownership; macOS's native menu bar silently omitting a
  top-level menu with zero actions (`File`); and `QMenu.addSection()`'s
  own title text simply not rendering at all once the native menu bar
  was disabled — replaced with a plain disabled `QAction` instead, which
  renders reliably in any style.

  **"Extract Power Intent" text export** (explicit request,
  `power_intent_view/power_intent_text_export.py` + `app/main_window.py`'s
  "File > Extract Power Intent > UPF/CPF > &lt;version&gt;"): serializes
  the Power Intent diagram's own *mock overlay* state (`SupplyPortDiagramState`
  + `PowerDomainDiagramState` — power supplies, supply sets, power
  domains, and macro PG-pin connections, the 4 concepts explicitly
  scoped for this pass) into UPF or CPF command text, saved wherever the
  user picks via `QFileDialog.getSaveFileName`. **Deliberately not the
  same thing as a real `PowerIntentDesign` export** — `backend/export/`
  remains genuinely unbuilt, its own future, milestone-adjacent piece
  per `docs/product_workflow.md`'s Export section; this reads only the
  diagram's own mock state, zero backend import, same as every other
  diagram-only piece of this milestone. Best-effort convenience output,
  not a certified spec-compliant writer — the file itself says so in its
  own header comment. UPF gets `create_supply_port`/`create_supply_net`
  per placed supply, `create_supply_set` (power/ground/nwell/pwell
  function handles), `create_power_domain` (`-elements`, stripped of the
  design's own root segment; omitted for a root-level domain), and
  `connect_supply_net -ports` for each connector drawn between a plain
  supply port and a macro's own (fixed) PG pin — a connector between two
  plain ports or two PG pins isn't a "macro connection" and is skipped.
  CPF has no first-class Supply Set concept, so one gets folded directly
  into each referencing domain's own `update_power_domain
  -primary_power_net`/`-primary_ground_net` instead; a supply's
  power-vs-ground role (needed for CPF's separate `create_power_nets`/
  `create_ground_nets`) is derived from which Supply Set field
  (`.power`/`.ground`) actually names it — a placed supply never used by
  any Supply Set can't be classified this way, so CPF output lists it in
  a comment rather than guessing which command it belongs under.
  Voltage (known only for a macro's own PG pin) is included on CPF's
  `create_power_nets`/`create_ground_nets` when known. Menu items follow
  the same deferred-insertion pattern as "View" (not in the menu bar
  until a project exists) and the same "store the QMenu as a persistent
  instance attribute" discipline the shiboken gotcha above already
  established — confirmed to matter again live here (a menu rediscovered
  via `.menu()` in this feature's own first test attempt raised the same
  "Internal C++ object already deleted"). Saved file extension is
  force-appended (`.upf`/`.cpf`) if the save dialog's own return value
  didn't already end with it — a native dialog doesn't reliably enforce
  the selected filter's extension on every platform.
  **Follow-up refinements** (explicit requests): UPF's `create_power_domain`
  gained `-exclude_elements` (well-documented in the 1801 LRM) and
  `-supply {secondary <name>}` per secondary Supply Set, both computed/
  read the same way the Properties panel's own Power Domains tab now
  surfaces them (see that tab's own entry below) — a domain nested inside
  another's subtree is excluded via `_direct_excluded_children()`,
  duplicated (not shared) between this module and `properties_panel.py`,
  same small-overlap-duplication precedent as `filter_button.py`'s own
  docstring. CPF has no exclude-instances flag this writer is confident
  enough in to assert, so exclusion there is a comment, not a guess.

  **Power Intent Properties, follow-up refinements** (explicit requests,
  `power_intent_view/properties_panel.py`): **no dedicated "Scope" column
  anywhere anymore** (Power Domains/Supply Ports/Supply Sets) — every
  table there is instead always ordered "top scope to deeper one" by
  default (`_depth()`, real per-column click-to-sort still overrides
  until the next rebuild). **Supply Ports**: "Kind"/"Direction-Voltage"
  renamed "Type"/"Connected"; Type values renamed "Supply"/"PG Pin" →
  "Supply Port"/"Macro PG", plus a new "Macro PG - Internal" for a
  macro's own PG_INTERNAL pins (previously not materialized as icons at
  all — supply_port_overlay.py's own `build_supply_port_overlay()` only
  ever read `PortDirection.PG`, never `PG_INTERNAL`; now reads both,
  tagging `SupplyPortPlacement.is_internal`). "Connected" is "-" for a
  real Port/Net row or a plain (non-fixed) diagram supply port (nothing
  "upstream" of a supply declaration itself), the wired plain port's own
  name for a macro PG pin connected to one via a drawn connector, or
  "Floating" (italic) otherwise. **Power Domains**: "Elements" now shows
  the domain's own scope (root-relative; "." at the design root) instead
  of a real domain's genuine `-elements` list; "Exclude Elements" for a
  diagram-only domain is now *computed* (`_direct_excluded_children()`,
  mirrors `power_domain_overlay.py`'s own `apply_domain_coloring()`
  cascade) rather than always blank — a real domain's own already-
  authored `exclude_elements` is left as-is, not merged with anything
  computed (different, RTL-instance-shaped field, not safely comparable
  to a computed scope-shaped one); new "Secondary Supplies" column,
  resolved from `PowerDomainPlacement.secondary_supply_sets` the same
  way "Primary Supply" already was.

  **Design Properties, follow-up refinements** (explicit requests,
  `hierarchy_view/properties_table_widget.py`): no per-row Path column
  anymore on either PortList or Sequential — a single "Path: &lt;scope&gt;"
  label (selected node path(s), italic, comma-joined for a multi-
  selection) sits between the filter bar and the table instead. Accepted
  tradeoff, named explicitly in the module's own docstring: two selected
  nodes whose own rows would otherwise be identical except for which
  node they came from (e.g. both having a "clk" port) are no longer
  disambiguated row-by-row. "Sequential" (tab title) renamed "Sequential
  Elements". The macro PG-pin label's own 2-line text (name + voltage)
  is now genuinely centered, line over line — `SupplyPortLabelItem`
  overrides `paint()` to draw each line by its own measured width rather
  than relying on `QGraphicsSimpleTextItem`'s own default multi-line
  paint (flush-left for every line, so a short "VDD" over a longer
  "(0.80V)" read as left-justified, not centered, before this fix).

  **"Visualize Design" hidden; "Design Properties" moved into Power
  Intent Properties** (explicit request — "get rid of the Visualize
  Design tab but keep the code in case I want to restore it in
  future"): `main_window.py` still fully constructs `self.
  visualize_design_tab` and still keeps it fed with data
  (`_on_design_loaded()` still calls `.load(design)` on it) — it's
  simply never `addTab()`'d into `self.tabs`, so it's unreachable from
  the UI (no tab, no "View" menu group for it either) without being
  deleted. Tab order is now Load Design/Load Power Intent/Power Intent
  (indices 0/1/2); every tab-index-keyed reference in this file
  (`_view_menu_actions_by_tab_index`, every `setTabEnabled()`/
  `setCurrentIndex()` call) was renumbered accordingly — "Mapping"/
  "Create New" succeeding now navigates straight to "Power Intent"
  (index 2) instead of the old "Visualize Design" stopover. The one
  part of Visualize Design users still needed day-to-day — "Design
  Properties" (PortList/Sequential Elements) — moved to Power Intent
  Properties' own first tab instead (`PowerIntentPropertiesPanel.
  design_properties`, a second, independent `HierarchyPropertiesTableWidget`
  instance, *not* the one still sitting unused inside the hidden
  `VisualizeDesignTab`), bound to `PowerIntentTab`'s own pre-existing
  `HierarchySelectionController` (the same one its Hierarchy Tree/
  Diagram already share) via a new optional `hierarchy_controller`
  constructor parameter — defaults to a private, unshared controller
  when omitted, so every pre-existing caller/test needed no changes.
  Fed via a new `PowerIntentPropertiesPanel.load_design()`, called from
  `PowerIntentTab.set_rtl()`, independent of `load()`/
  `refresh_diagram_overlay()` (power-intent-shaped data, not RTL-shaped).
  To restore Visualize Design later: re-add its `addTab()` call and
  View-menu group, and shift "Power Intent"'s own tab-index references
  back up by one (Design Properties can stay where it is, or move back
  — both copies of `HierarchyPropertiesTableWidget` keep working
  independently either way).

  **Follow-up refinements to the above** (explicit requests):
  "PortList"/"Sequential Elements" are now promoted *individually* as
  Power Intent Properties' own first two top-level tabs, not grouped
  under one "Design Properties" container tab anymore — `self.
  design_properties` (still a `HierarchyPropertiesTableWidget` instance)
  is never itself shown; only its own `._port_panel`/`._sequential_panel`
  are `addTab()`'d directly, reparented away from `design_properties`'s
  own (display-less) shell, which stays alive purely so `load_design()`
  and the two panels' own internal wiring keep running through it. The
  dock itself is retitled "Properties" (from "Power Intent Properties" —
  shorter, and the tab it lives in already says "Power Intent"). The
  QTabWidget's own tab bar no longer stretches to fill the panel's full
  width (`tabBar().setExpanding(False)`) — tabs read flush-left/indented
  instead of appearing centered across a wide panel.

  **Diagram tool palette** (explicit request, "like KiCad's icon
  toolbar" — `power_intent_view/diagram_tool_palette.py`): a vertical
  `QToolBar` docked to the left of the Power Diagram's own
  QGraphicsView (inside the "Power Diagram" group box itself, alongside
  `self.diagram` — not a separate QMainWindow-level dock the way
  Hierarchy Tree/Properties are): Regular Cursor/Select, Zoom Lens, a
  real `addSeparator()`, Supply Port, Supply Net. **No group header
  labels** — an earlier version had "DIAGRAM VIEW"/"POWER INTENT" text
  above each pair; removed on a direct follow-up request, the separator
  alone is enough grouping. All four are checkable `QAction`s in one
  exclusive `QActionGroup`, defaulting to Regular Cursor.

  **Icons — placeholder glyphs, not the real SVG assets** (revised on a
  direct follow-up request from an initial version reusing the existing
  `cursor.svg`/`zoom-lens.svg`/`supply-port.svg`/`power-net.svg` files):
  `diagram_tool_icons.py` draws each glyph with plain `QPainter`
  primitives onto a transparent `QPixmap` (arrow polygon, magnifying
  glass, circle-with-stub, diagonal line) instead, kept in this one
  dedicated module specifically so real artwork can replace them later
  without touching `diagram_tool_palette.py`/`power_intent_diagram_view.py`
  at all — every function returns a `QIcon` (or, for Zoom Lens's own
  cursor below, a `QCursor`), never a raw pixmap a caller would need to
  know how to wrap. **Palette footprint halved** (explicit request, "the
  palette is currently too large/bulky... roughly HALF its current
  footprint"): `_ICON_SIZE` dropped and `setStyleSheet("QToolButton {
  padding: 2px; }")` added — `QToolBar`'s own default button padding was
  sized for the earlier, larger icons and left disproportionate empty
  space around these smaller ones.

  Deliberately decoupled (explicit request): the palette only emits
  `modeChanged(DiagramMode)`; `_PowerIntentGraphicsView.set_mode()`
  (`power_intent_diagram_view.py`) owns everything about what a mode
  actually *does* — cursor shape and mouse-event routing, checked at the
  top of `mousePressEvent`/`mouseMoveEvent`/`mouseReleaseEvent` before
  any of the pre-existing (Select-mode-only) handle/segment/icon-drag
  logic runs, so Select's own behavior is completely unchanged.

  - **Zoom Lens — custom cursor, rubber-band zoom in *and* out**
    (zoom-out and the custom cursor both added on a direct follow-up
    request): the cursor itself becomes lens-shaped while this mode is
    active (`diagram_tool_icons.zoom_lens_cursor()` — the same
    magnifying-glass glyph rendered larger, as a real `QCursor` built
    from a pixmap, hotspot at the lens's own optical center) instead of
    a stock shape. Hand-rolled rubber-band zoom (`QRubberBand`), since
    this codebase has no `QGraphicsView.RubberBandDrag` usage anywhere
    to reuse (a real, confirmed gap — grepped, zero hits) and that
    built-in mode would conflict with the same hand-rolled pan-vs-click
    machinery that already rules out `ScrollHandDrag`.
    **Zoom in**: left-click-drag; release maps the final rect to scene
    coordinates and calls `fitInView(..., KeepAspectRatio)`.
    **Zoom out**: right-click-drag (there is deliberately no
    click-to-zoom at any fixed percentage) — the drawn rectangle is
    read as the *fraction* of the current viewport the view should
    shrink to fit inside (a small rect zooms out a lot, a rect nearly
    the size of the viewport zooms out only slightly — the literal
    inverse of zoom-in's own ratio): `scale_factor = viewport_size /
    rect_size`, `view.scale(1/scale_factor, 1/scale_factor)`, then
    `view.centerOn(scene_pos_of_rect_center)` — the direct Qt
    equivalent of "capture the rect center's scene position before/after
    scaling and translate to compensate" (`view.scale()` alone zooms
    anchored at the live cursor position, `AnchorUnderMouse`, not
    necessarily this rect's own center). A genuinely separate code path
    from the base class's own mouse-wheel zoom (`HierarchyDiagramView.
    wheelEvent()`/`_fit_scale()`) — that one floors zoom-out at "the
    whole design fits the viewport" with no ceiling on zoom-in at all;
    rubber-band zoom instead clamps to an explicit, separate
    `_MIN_ZOOM_SCALE`/`_MAX_ZOOM_SCALE` (0.05–20) so neither gesture can
    run away to nothing/infinity — deliberately not unified with the
    wheel-zoom mechanism, since the two gestures have genuinely
    different intents. Both directions ignore a drag under ~5px (a
    click, not a drag) and never call `set_mode()` on success — stays in
    Zoom Lens either way (explicit request: no auto-switch back to
    Select). Right-click-drag also needed a new
    `DiagramMode.ZOOM_LENS` bypass added to `_on_context_menu()` itself
    — that slot is Qt's own `customContextMenuRequested` delivery (a
    separate path from raw mouse press/release, fired on release
    regardless of what `mousePressEvent` already did with the event), so
    the pre-existing Copy Path/Hide/Add Power Supply popup needed its own
    explicit suppression rather than relying on event-consumption timing.

  - **Supply Port — real drag-and-drop from the palette button itself**
    (a full rewrite, on a direct follow-up request, of an earlier
    press-anywhere-on-canvas version — see that version's own prior
    writeup, now superseded below): clicking the canvas in this mode,
    on a block or empty space alike, does **nothing** — placement is
    drag-and-drop from the toolbar button only. `diagram_tool_palette.
    _DraggableToolButton` (bound to the Supply Port `QAction` via
    `setDefaultAction()` + `addWidget()` rather than a plain
    `addAction()`, the standard Qt idiom for a custom-behaving button
    that still participates in an exclusive `QActionGroup`) starts a
    real `QDrag` once a press moves past `QApplication.
    startDragDistance()`, carrying `SUPPLY_PORT_MIME_TYPE` as a marker
    (no real payload needed — the source is unambiguous). The receiving
    end (`_PowerIntentGraphicsView`, `self.setAcceptDrops(True)`) is a
    normal `dragEnterEvent`/`dragMoveEvent`/`dragLeaveEvent`/`dropEvent`
    override quartet: on each drag-move, whichever hierarchy block is
    under the cursor gets a temporary, non-destructive fluorescent-
    yellow outline (`_BoxHighlightOverlay`, a bare pen-only
    `QGraphicsRectItem` matching the box's own `boundingRect()`,
    parented to it — the box's own fill is never touched), cleared and
    reapplied as the hovered block changes, cleared outright on
    `dragLeaveEvent`. On drop, the target block/edge is resolved the
    same "nearest edge" way as before (`_nearest_edge_and_t()`), and
    the drop opens the exact same `AddPowerSupplyDialog` the right-click
    "Add Power Supply" action already uses (explicit request: "reuse
    the dialog class — do NOT build a new one") — `_prompt_add_power_supply()`
    gained optional `edge`/`t` parameters (defaulting to the
    right-click path's own prior "top, centered" behavior) so both entry
    points share one dialog-driven call, rather than the drop
    auto-naming a port with no dialog the way the superseded version
    did. Dropped outside any block: rejected, no crash, no placement.
    Stays in Supply Port mode after a successful drop (explicit request,
    same "don't auto-switch back to Select" rule Zoom Lens already
    follows). The right-click "Add Power Supply" menu action is
    unchanged, still a separate, parallel entry point to the same
    `add_supply_port()`.

  - **Supply Net — precise handle-to-handle, now explicitly mode-gated,
    plus hover/drop-target feedback**: "a small hit-target at the port's
    connection point, not the whole port icon" is the pre-existing
    `_connector_handle_at()` gesture — but a direct follow-up request
    overturned an earlier deliberate choice to leave that gesture
    mode-*independent* ("currently it seems to work regardless of active
    mode; fix that"): starting a connector drag now explicitly checks
    `self._mode == DiagramMode.SUPPLY_NET` in `mousePressEvent` first,
    falling through to normal Select-mode behavior otherwise (segment
    drag, icon drag). While a drag is in progress, the **source** anchor
    stays highlighted for the whole gesture regardless of where the
    cursor wanders (`handle.set_hover_highlight(True)` at press time,
    never touched again until the drag ends) — a real ordering bug found
    by a real test while wiring Escape (below): both `set_mode()`'s own
    away-from-Supply-Net cleanup and `_cancel_active_interaction()`
    originally nulled `self._connector_drag_origin` *before* calling the
    shared highlight-clearing helper, so that helper's own "is a drag in
    progress" check already saw `None` and silently skipped clearing the
    source's own highlight, leaving it stuck yellow forever; fixed by
    clearing highlights first, then nulling the drag state. A *second*,
    independent handle (`_hovered_handle`, reused from the pre-existing
    idle-hover feature) tracks whichever *other* handle is currently a
    valid drop target during the drag — `_update_supply_net_hover()` now
    runs on every `mouseMoveEvent` unconditionally (previously gated to
    only run when no drag was active at all) but explicitly skips ever
    highlighting/un-highlighting the origin handle itself, so the two
    highlight lifetimes never fight each other. Both share one
    mechanism (`ConnectorHandleItem.set_hover_highlight()`) and one
    color constant, `HIGHLIGHT_COLOR = QColor(255, 255, 0)`
    (`supply_port_overlay.py`) — explicitly the *same* constant the
    Supply Port drag's own `_BoxHighlightOverlay` uses, "one place to
    tune later." Requires `self.viewport().setMouseTracking(True)`
    (pre-existing) for continuous hover delivery. `set_mode()` cancels
    any live connector drag and clears every highlight the moment the
    mode changes away from Supply Net (switching tools mid-drag is
    treated the same as an abandon); `_rebuild()` still clears the
    reference outright post-rebuild.

  - **Escape — universal cancel-and-reset** (explicit request, "standard
    CAD/graphics-editor convention"): a new `_PowerIntentGraphicsView.
    keyPressEvent()` (the base `QGraphicsView` already gets
    `Qt.FocusPolicy.StrongFocus` for free, no extra wiring needed) on
    `Qt.Key.Key_Escape` cancels whatever local interaction is in
    progress — discards an active rubber band, an in-progress Supply Net
    drag line, and every yellow highlight (`_cancel_active_interaction()`,
    sharing the same highlight-clearing helper `set_mode()` uses) — then
    emits a new `escapePressed` signal rather than calling `self.
    set_mode(SELECT)` directly. That signal is deliberately just
    re-broadcast up through `PowerIntentDiagramView.escapePressed`
    (from whichever tab's view it fired in) to `power_intent_tab.py`,
    which checks `tool_palette.select_action.setChecked(True)` — driving
    the mode back to Select through the *exact same* `modeChanged ->
    set_mode()` chain a normal toolbar click already uses, so every open
    tab resets uniformly (not just the one Escape was pressed in), the
    toolbar's own checked button stays in sync for free, and there is no
    second, parallel "reset everything" code path to keep consistent
    with the first.

  - **Cross-tab propagation**: `PowerIntentDiagramView.set_mode()`
    stores the mode and applies it to every currently-open diagram tab
    (`HierarchyDiagramTabs.all_views()`, a small, view-type-agnostic
    accessor added there); a tab opened *after* a mode change (a new
    sub-hierarchy tab) still starts in that same mode via the
    `view_factory` closure applying it at construction time. A real bug
    surfaced building this: `all_views()`'s first version read
    `_views_by_path`, which only starts tracking the always-open root
    tab once `reset()` first runs (i.e. once a design is actually
    loaded) — a mode change made *before* that point would silently
    never reach the root tab at all. Fixed by reading the QTabWidget's
    own actual pages (`self.widget(i)`) instead, accurate regardless of
    `_views_by_path`'s own lazy-population timing.
  - **A real, confirmed `QTest.mouseMove()` flakiness class, found
    writing this feature's own hover tests**: `QTest.mouseMove()` only
    delivers a real event if the *global* screen position actually
    changed since the last synthetic move — two back-to-back tests each
    moving to the same *local* viewport coordinate landed on the same
    *global* position under the offscreen test platform (each test's
    window defaults to the same screen origin), so the second test's
    move silently delivered nothing, no error. Hover-specific tests now
    drive `_update_supply_net_hover()` directly instead (the
    "directly-testable core" precedent this whole module already
    established for `add_supply_port()`/`try_connect_supply_ports()`,
    there because `dialog.exec()`/`menu.exec()` can't be driven
    headlessly at all) — one real end-to-end `QTest.mouseMove()`-driven
    test remains, to confirm real events do funnel into that method
    correctly, but every edge case is covered by the direct-call tests
    instead of fighting this dedup behavior.
  - **A real segfault, found writing the Supply Port drag-and-drop
    tests**: constructing a `QDropEvent`/`QDragMoveEvent` directly (to
    drive `dragEnterEvent()`/`dragMoveEvent()`/`dropEvent()` headlessly,
    the same "directly-testable core" reasoning as `QTest.mouseMove()`'s
    own workaround above, since a real `QDrag.exec()` is a blocking,
    OS-level modal loop that can't be driven in a test at all) crashed
    the interpreter the moment `event.mimeData()` was read back inside
    the method under test. Root cause: `QDropEvent` stores its
    `QMimeData` as a raw, non-owning C++ pointer — with nothing keeping
    the Python `QMimeData` object alive past the test helper returning,
    CPython's own refcounting freed it immediately (not a GC-cycle
    dependency, so the crash was non-deterministic across runs), leaving
    a dangling pointer. Fixed test-side only, by keeping every
    constructed `QMimeData` alive for the rest of the test session via a
    module-level list — a real caller (Qt's own drag manager) already
    keeps its own `QMimeData` alive for the whole drag, so this is
    purely a test-construction hazard, not a product bug.

  **Post-tool-palette bug-fix pass** (4 reported bugs investigated;
  empirically verified before touching any code, not fixed from a
  description alone):
  - **Supply Port drag-and-drop hit-tested the wrong (outer/ancestor)
    hierarchy box instead of a nested one — REAL bug, fixed.** Root
    cause was *not* z-ordering (an initial hypothesis, disproven: child
    `InstanceBoxItem`s are already real Qt child items of their parent
    via `setParentItem()` — see `hierarchy_scene_builder.py`'s own
    `_place()` — so Qt's own "a child always stacks above its parent
    regardless of Z" rule already picks the innermost box correctly,
    same precedent already documented for `_connector_handle_at()`).
    The real cause: `dragMoveEvent()`/`dropEvent()` fed raw
    `event.position()` straight into `_box_at()`/`mapToScene()`, both of
    which expect *viewport*-relative coordinates — but Qt delivers
    real drag-and-drop events to a `QGraphicsView` with position
    relative to the **view widget itself**, not its viewport (confirmed
    empirically: unlike ordinary mouse events, which
    `QAbstractScrollArea` forwards from the viewport pre-translated —
    every other mouse-driven gesture in this class already relies on
    that — a synthetic drag event sent directly to `self.viewport()`
    never reached this class's own `dragMoveEvent()` at all, ruling out
    an equivalent forwarding path for drag-and-drop). `self.
    setAcceptDrops(True)` is called on the view itself, not its
    viewport, confirming real native drags target the view directly.
    The resulting offset (`self.viewport().pos()`, the view's own frame
    margin — small at 1:1 zoom, but scaling with the *inverse* zoom
    factor in scene space) reliably mis-hit-tests onto an ancestor box
    once a design is fit-to-view at any real zoom-out, matching the
    reported symptom exactly. Fixed via a new
    `_viewport_pos_from_drag_event()` helper subtracting `self.
    viewport().pos()` before every hit test. Regression tests
    deliberately move `view.viewport()` by a large, controllable amount
    (`view.viewport().move(30, 30)`, found to be a more reliable way to
    force a big offset under the offscreen test platform than
    `QFrame.setLineWidth()`, which didn't reliably reposition
    `viewport()` without a real show/layout cycle) and pick a hit point
    near a box's edge (not its center) — verified live, by temporarily
    reverting the fix, that both new tests genuinely fail without it
    before confirming they pass with it. Every pre-existing Supply Port
    drag-and-drop test was also updated to build its synthetic event via
    the same real view-to-widget offset (previously feeding an
    already-viewport-relative position straight in, unrealistically
    bypassing the exact translation that hid this bug).
  - **Escape always forced the tool back to Regular Cursor, even with
    nothing in progress — accepted as a deliberate UX change, not a
    "bug fix" in the traditional sense.** The original behavior
    (`_cancel_active_interaction()` unconditionally followed by
    `escapePressed.emit()`) was itself an explicit requirement ("On Esc,
    regardless of current mode... switch DiagramMode back to SELECT...
    a universal cancel-and-go-back-to-default key"), not an accident —
    flagged here for the record since this revision directly supersedes
    it, not because it was ever unspecified. Now:
    `_cancel_active_interaction()` returns whether it actually cancelled
    something real (a live rubber band, an in-progress Supply Net drag,
    or an active highlight), and `keyPressEvent()` only emits
    `escapePressed` (and thus only resets the toolbar/mode) when that's
    `True` — Escape with nothing in progress is now a no-op. Still
    always swallows the key press (`event.accept()`) either way.
  - **Toolbar icon-to-action mapping — verified correct, no bug.**
    Pixel-compared each of the four `QAction`'s actual rendered icon
    against the exact `diagram_tool_icons.py` function it's supposed to
    use (`select_action` ↔ `cursor_icon()`, etc.) — all four matched
    exactly. The glyphs themselves are still deliberately simple
    placeholders (see this section's own earlier "Icons — placeholder
    glyphs" entry) — any "doesn't look enough like a real icon"
    complaint is an aesthetic asset-replacement task, not a code defect.
  - **Escape allegedly clearing the current hierarchy selection —
    NOT reproduced.** Traced every one of the exactly two call sites
    that ever emit `HierarchySelectionController.selectionChanged`
    (`hierarchy_tree_widget.py`'s `_on_item_selected()` and
    `hierarchy_diagram_view.py`'s `_on_scene_selection_changed()`,
    both real-interaction-driven, neither reachable from the Escape/
    mode-reset code path) and empirically drove five realistic
    scenarios via `qtbot`/`QTest` (a plain selection, a selection
    surviving mid-Zoom-Lens-rubber-band, mid-Supply-Net-drag, a
    multi-selection, and a tree-driven selection) — the hierarchy
    selection survived Escape in every one. Left unfixed: no
    reproducible mechanism was found: fixing a described-but-unverified
    symptom risks masking whatever the real trigger actually is (a
    different sequence of actions, a different build, or a
    misattribution to Escape of something else entirely). Needs a
    precise repro (exact click/keypress sequence, and which commit) to
    investigate further.

  **Follow-up pass: real icon assets, cursor-per-tool, Supply Port
  reworked to hover+click** (explicit requests):
  - **Toolbar icons — reverted from QPainter-drawn placeholders back to
    the real provided SVG assets.** `diagram_tool_icons.py` now renders
    `frontend/icons/cursor.svg`/`zoom-lens.svg`/`supply-port.svg` (the
    same file `supply_port_overlay.py` already uses for the diagram's
    own supply-port icons) via `QSvgRenderer`, plus `power-net.svg` for
    Supply Net (the file's own name predates this toolbar — it's the
    provided "Supply Net" concept asset, matching the earlier "initial
    version reusing... power-net.svg" note from the palette's first
    pass). Verified pixel-for-pixel (new
    `test_each_action_uses_the_correct_provided_icon_asset`) that every
    action shows exactly the icon its own name implies, not just "some
    real icon."
  - **Cursor now changes shape per selected tool, scoped to the
    Diagram View's own canvas only.** `set_mode()` calls
    `self.viewport().setCursor(...)`, never `self.setCursor(...)` on the
    outer `QGraphicsView` widget -- explicit request: the custom cursor
    must apply "ONLY while the cursor is inside the Diagram View's
    canvas area," and `viewport()` is that literal widget. Each mode's
    cursor is built from the same SVG the toolbar button itself uses,
    rendered larger (`diagram_tool_icons.py`'s `zoom_lens_cursor()`/
    `supply_port_cursor()`/`supply_net_cursor()`, hotspot at the
    pixmap's own geometric center) -- Supply Port and Supply Net each
    now get their own distinct cursor, no longer a shared crosshair.
    **SELECT now gets the plain system arrow** (explicit request:
    "Regular Cursor mode -> normal arrow cursor (default)") --
    flagged, not silently assumed correct: this supersedes this view's
    own earlier `OpenHandCursor` pan-affordance cursor for Select mode,
    which predates the tool palette entirely and was documented
    multiple times as a deliberate choice; the new spec's own symmetric
    per-mode table read as an intentional, explicit override rather
    than an unaware placeholder, so it was implemented as literally
    specified rather than blocked on a question -- worth confirming
    this was actually intended, since the open-hand affordance is gone.
  - **Supply Port placement reworked from drag-and-drop to hover+click**
    (explicit request: "REMOVE the drag-and-drop placement mechanism
    entirely -- it is not working reliably and should not be kept as a
    placement method" -- superseding the whole drag-and-drop pass from
    the immediately preceding update, including its own dedicated
    coordinate-offset bug fix, which is now moot: mouse events on a
    `QGraphicsView` are already correctly viewport-relative, the same
    safe path every other gesture in this class already relies on, so
    the removed mechanism's whole bug class can't recur here).
    `_update_supply_port_hover()` (called from `mouseMoveEvent`,
    mirroring `_update_supply_net_hover()`'s own "no-op outside its own
    mode" shape) drives the exact same `_BoxHighlightOverlay`/
    `HIGHLIGHT_COLOR` the removed drag-and-drop version already used --
    only *what drives it* changed. A left-click in `mousePressEvent`'s
    own `DiagramMode.SUPPLY_PORT` branch calls `_box_at(pos)` and, if a
    block is under the cursor, opens the exact same `AddPowerSupplyDialog`
    the right-click "Add Power Supply" context-menu action already uses
    (`_prompt_add_power_supply()`, now simplified back to a single
    default "top, centered" placement -- edge-snapping-to-drop-position
    doesn't apply to a plain click the way it did to a drop position, so
    the `edge`/`t` pass-through parameters and the now-dead
    `_nearest_edge_and_t()` helper were removed rather than kept as
    unused flexibility). Clicking outside any block is a no-op (no
    crash, no placement, dialog never opened). Stays in Supply Port
    mode after a successful placement, same "don't auto-switch back to
    Select" precedent Zoom Lens already established. The tool palette's
    own Supply Port button is a plain checkable `QAction` again, same as
    the other three -- `_DraggableToolButton`/`SUPPLY_PORT_MIME_TYPE`
    (both specific to the removed `QDrag`-based mechanism) were deleted
    outright, not left as dead code. A real gap found writing this
    pass's own tests: `set_mode()` cleared the Supply Net highlight when
    leaving that mode, but never cleared the Supply Port hover highlight
    when leaving *that* mode -- switching tools mid-hover (e.g. Supply
    Port -> Select without clicking) left a stale yellow-highlighted box
    on screen; fixed by adding the equivalent `mode != DiagramMode.
    SUPPLY_PORT` clear alongside the pre-existing Supply Net one.

  **Dedicated Pan tool added** (explicit follow-up, resolving the "worth
  confirming this was actually intended" flag two entries up: it
  wasn't quite -- Select losing the open-hand pan affordance was correct,
  but that affordance needed a new home, not just disappearing).
  `DiagramMode` gains `PAN`, positioned between `SELECT` and `ZOOM_LENS`
  both in the enum and the toolbar itself; a new `pan.svg` (simple
  open-palm-plus-fingers glyph, matching the existing set's stroke
  weight/colour) since no such asset existed yet. Pan's own cursor is
  Qt's stock `Qt.CursorShape.OpenHandCursor` -- deliberately *not* a
  custom pixmap cursor built from `pan.svg` the way every other mode's
  own is (explicit request: "exactly as Regular Cursor mode used before
  this tool palette existed").

  The actual click-drag-pans gesture is `HierarchyDiagramView`'s own
  pre-existing pan machinery (`_pan_start`/`_panning`, unchanged) --
  moved/rewired, not reimplemented, via two new small overridable hooks
  on the *base* class rather than touching its own behavior:
  `_pan_enabled()` (default `True` -- this class's own single-mode
  world) and `_set_own_cursor()` (default `self.setCursor(...)`, the
  base class's own pan-cursor feedback target). `_PowerIntentGraphicsView`
  overrides both: `_pan_enabled()` returns `self._mode ==
  DiagramMode.PAN` (so `SELECT` no longer arms the pan-vs-click
  bookkeeping at all -- "select/inspect only, no pan-on-drag" -- while
  every other mode's own dedicated mousePressEvent() branch already
  intercepts a plain left-press before the base class's fallback logic
  would ever run, so this hook only ever actually matters for Select vs
  Pan); `_set_own_cursor()` redirects to `self.viewport().setCursor(...)`
  -- without this second hook, the base class's own closed-hand-while-
  panning/open-hand-at-rest feedback would set a cursor shadowed by the
  viewport's own per-tool cursor and silently never show, now that
  cursor display moved to viewport-scoping in the entry above. This
  two-hook split keeps `HierarchyDiagramView`'s own standalone behavior
  (still used directly, with no `DiagramMode` concept at all, by the
  hidden-but-kept `VisualizeDesignTab`) completely untouched --
  confirmed via its own pre-existing pan tests, unmodified and still
  passing. `set_mode()`/`_cancel_active_interaction()` both also abort
  an in-progress pan the same way they already abort an in-progress
  Supply Net drag or Supply Port hover when switching away mid-gesture
  (toolbar click or Escape respectively) -- `_pan_enabled()` is only
  consulted at press time, so merely changing `self._mode` wouldn't
  otherwise stop a drag already under way.

  **Escape reverted to always resetting to Regular Cursor, regardless
  of active tool** (explicit bug report, undoing the immediately prior
  revision's own conditional gating). The prior pass had made
  `escapePressed` only fire when `_cancel_active_interaction()`
  actually found something to cancel — reported (and confirmed) as
  making Escape "appear to only work when Supply Port is selected":
  `DiagramMode.SUPPLY_PORT`'s own hover highlight is armed by mere
  mouse movement (see `_update_supply_port_hover()`), so it's "active"
  far more often in practice than Zoom Lens/Pan/Supply Net's own
  drag-only state, which is only ever non-empty mid-gesture. Fixed by
  decoupling the two steps `keyPressEvent()` always performed
  together: `_cancel_active_interaction()` still cancels/clears
  whatever's actually in progress *if* anything is, and still returns
  whether it found something (kept as real bookkeeping, e.g. tests
  assert it directly to confirm a specific interaction was cleared —
  not repurposed for gating anymore), but `escapePressed` now always
  emits regardless of that return value, restoring "regardless of
  current mode... switch back to SELECT" unconditionally. New
  parametrized test (`test_escape_always_resets_to_select_even_with_
  nothing_in_progress`, one case per mode) locks this in for all 5
  modes with zero in-progress interaction in each.

  **Bug 1 (Escape allegedly clearing the current hierarchy selection)
  re-verified after this fix, per explicit request — still NOT
  reproducible as an Escape-caused bug**, though a related, genuinely
  real (but unrelated to Escape) selection-loss scenario was found and
  precisely isolated during the re-check: a plain left-click landing on
  empty canvas — in any mode whose `mousePressEvent()` falls through to
  the base class's own native `QGraphicsView` click handling (Select
  and Pan specifically, since every other mode's own dedicated branch
  intercepts the press first) — deselects the current selection
  immediately at press time, via standard, pre-existing `QGraphicsScene`
  click-to-deselect semantics that predate every change in this section
  and have nothing to do with Escape. Confirmed by isolating each step:
  starting a Pan drag from an arbitrary empty-canvas point loses the
  selection at the `mousePress` itself (verified with no Escape
  involved at all, and reproduced identically in plain Select mode,
  untouched by any of this session's changes); starting the *same* drag
  from *within* the already-selected box instead, the selection
  survives press, drag, and a subsequent Escape intact. Re-ran the
  original 5-scenario empirical suite (simple selection, mid-Zoom-Lens-
  rubber-band, mid-Pan-drag, mid-Supply-Net-drag, tree-driven selection,
  multi-selection) plus every no-active-interaction case across all 5
  modes — selection survived Escape in all of them once the drags
  themselves started from real content rather than empty background.

  **Two more reported bugs, both empirically confirmed and root-caused
  before fixing (per explicit request — neither of the user's own
  hypothesized causes turned out to be exactly right):**
  - **Supply Port hover highlight stuck on an outer block, never
    tracking into a nested child underneath the cursor.** Not a
    stacking-order/hit-testing-call-site issue as initially suspected
    (`_box_at()`'s own `itemAt()` + walk-up-via-`parentItem()` is
    correct, and child boxes already are real Qt children of their
    parent, confirmed in an earlier pass). The actual cause:
    `_BoxHighlightOverlay`'s own *default* `shape()` (`QGraphicsRectItem`'s
    own, from `rect()`) covers its *entire* rectangle for hit-testing,
    not just its drawn pen-only border — Qt's own
    `qt_graphicsItem_shapeFromPath()` unions the stroked outline with
    the original filled path regardless of brush, so an unfilled
    pen-only rect is exactly as hit-testable as a filled one. Once
    created on an *outer* box, this overlay — a sibling of any nested
    child box under that same parent, at `Z_SUPPLY_PORT + 1` vs. every
    `InstanceBoxItem`'s flat `Z_INSTANCE_BOX` — sits on top of and
    permanently shadows any nested child for `itemAt()`/`items()`
    across the *whole* outer box's own footprint. The pre-existing
    `setAcceptedMouseButtons(NoButton)` only ever exempted it from real
    Qt event delivery, never from this more basic geometric hit-test
    query — its own docstring already claimed "never steals a hit-test,"
    which was only half-true. Fixed by overriding `shape()` to return
    an empty `QPainterPath` — this item is purely visual, so it should
    never be found by a hit test at all; `boundingRect()`/painting are
    derived from `rect()`, not `shape()`, so the highlight itself still
    renders identically. Verified live before touching source: a
    monkeypatched empty `shape()` fixed the repro; reverting it
    afterward confirmed the new regression test
    (`test_hover_tracks_into_and_back_out_of_nested_blocks`, a hand-
    built 3-level `outer -> middle -> inner` `Design` fixture since
    `build_mock_design()` only ever nests 2 levels) genuinely fails
    without the fix.
  - **Regular Cursor showed an open-hand cursor after any click on the
    canvas.** Not a leftover `QGraphicsView.setDragMode(ScrollHandDrag)`
    as hypothesized — confirmed empirically (`view.dragMode()` is
    `NoDrag`) and by exhaustive grep: `setDragMode()` is never called
    anywhere in this codebase at all, deliberately (see
    `hierarchy_diagram_view.py`'s own module docstring: the built-in
    mode would intercept every left-click and break box-click
    selection, so pan is entirely hand-rolled instead). The actual
    cause: `HierarchyDiagramView.mouseReleaseEvent()`'s own cursor
    feedback — `self._set_own_cursor(Qt.CursorShape.OpenHandCursor)` —
    ran unconditionally on *every* left-button release, regardless of
    `_pan_enabled()`, unlike `mousePressEvent()`'s own `_pan_start`
    arming (already correctly gated when Pan was split out as its own
    mode). A real gap from that same earlier pass: only one of the two
    `_set_own_cursor()` call sites got the new gate. Fixed by adding
    the same `if self._pan_enabled():` guard to the release-time call —
    for the base class's own standalone use (pan always enabled there),
    behavior is unchanged; for `_PowerIntentGraphicsView`, a release
    outside Pan mode no longer stomps whatever cursor `set_mode()` had
    actually set. New regression tests: `test_cursor_survives_a_click_
    release_outside_pan_mode` (parametrized over Select/Zoom Lens/
    Supply Port/Supply Net) and `test_drag_mode_is_always_no_drag`
    (parametrized over all 5 modes, locking in that `ScrollHandDrag`
    is never used, matching the deliberate hand-rolled design even
    though it wasn't the actual bug here). Verified live: reintroducing
    the unconditional call made all of these genuinely fail before the
    fix.

  **Bug 3: Supply Port could not be added on a macro (leaf block) —
  root-caused in the SHARED `_box_at()` itself, not anything Supply-
  Port-specific.** Extensive direct testing against `build_mock_
  design()`'s own macro (`top/u_ram`, black-box, leaf, already carrying
  auto-derived VDD/VSS PG pins) found hover and click working correctly
  in every scenario tried directly — bare macro, macro with a
  `MappedDesign` loaded, clicking directly on an existing PG-pin icon,
  real `QTest.mouseMove()` + `fit_view()`. A research pass into
  `diagram_overlay.py` (the Milestone-2 real-`MappedDesign` overlay,
  distinct from the diagram-only user-placed supply ports) found the
  actual mechanism: unlike the 3 badge classes (isolation/level-
  shifter/retention, all correctly parented to their own owning
  `InstanceBoxItem`), `SwitchIconItem` and `SupplyRailItem` are bare
  top-level scene items (`scene.addItem(...)`, never `setParentItem()`)
  at a Z-value above `InstanceBoxItem`'s own — and a power switch icon
  is positioned at a domain's own *region* top edge (the union of every
  member box + padding), which can genuinely coincide with a member
  box underneath it, macros very much included (a macro is exactly the
  kind of instance a real design puts in a switched domain — the mock
  data just happens to never do this, which is why direct testing
  against it alone couldn't surface the bug). The real defect: the
  shared `_box_at()` (`hierarchy_diagram_view.py`, used by Supply Port
  hover/click but also right-click Hide/Copy Path, double-click sub-
  hierarchy navigation, and icon-drag detection — every one of these
  was equally exposed, not just Supply Port) used `self.itemAt(pos)`
  (only the single topmost item) and walked `parentItem()` from it
  looking for an `InstanceBoxItem` — when that topmost item had *no*
  parent to walk up to (exactly `SwitchIconItem`'s own shape), the walk
  hit `None` immediately and the method returned `None` — "no box
  here" — even though a real box existed directly underneath. Fixed by
  switching to `self.items(pos)` (the *whole* stacking-order-sorted
  list) and walking each candidate's own ancestry in turn, returning
  the first that resolves to a box — the exact same "walk the whole
  stack, not just the top" pattern `power_intent_diagram_view.py`'s own
  `_connector_handle_at()`/`_supply_port_icon_at()` already established
  for the analogous pitfall with *parented* deep items; this is the
  unparented-sibling counterpart. Verified live (reintroducing the old
  single-`itemAt()` version made the new regression tests fail, restoring
  the fix made them pass): a generic base-class test
  (`test_box_at_finds_the_box_underneath_an_unparented_overlay_item`,
  a bare `QGraphicsRectItem` at the same "no parent, higher Z" shape as
  the real overlay items, avoiding a dependency on the whole Milestone-2
  domain/switch construction pipeline) plus the equivalent Supply-Port-
  specific end-to-end test. (The `_box_at()` fix itself remains fully
  valid and unchanged — it's used far beyond Supply Port, e.g. right-
  click Hide/Copy Path and double-click sub-hierarchy navigation are
  equally protected by it. Only the *outcome* of successfully resolving
  a macro's own box changed in a later, explicit follow-up immediately
  below — attempting to add a supply port there is now deliberately
  rejected, not silently mishandled.)

  **Macro (.lib) supply ports — explicit reversal, superseding Bug 3's
  own "must be addable on ANY block, macros included" premise.** A
  macro's own pins are already fully fixed by its Liberty definition
  (see `build_supply_port_overlay()`'s own PG-pin auto-derivation) — a
  user-placed supply port has no real meaning on one and is no longer
  allowed at all. Enforced at two layers, mirroring the existing
  duplicate-name (`_check_unique_name()`) precedent exactly:
  `add_supply_port()` itself raises `ValueError` for a
  `box.definition.is_black_box` target (`_MACRO_SUPPLY_PORT_ERROR`, a
  class constant shared by both layers) — the directly-testable core
  staying correct on its own regardless of caller; `_prompt_add_power_supply()`
  (the single method both the right-click "Add Power Supply" action and
  the Supply Port tool's own hover+click funnel through) checks this
  *before* ever constructing `AddPowerSupplyDialog` at all, rather than
  opening it and then rejecting via the existing `except ValueError:
  QMessageBox.warning(...)` retry-loop the duplicate-name case already
  uses — a macro rejects *every* possible name, so looping the dialog
  back open would just be pointless busywork the user would have to
  Cancel out of regardless of what they typed. Hover highlighting itself
  is unaffected (still shows the block is hovered) — only the click/
  assignment moment is rejected, matching the request's own framing
  ("nel caso in cui viene assegnato, fai uscire una schermata di
  errore"). Every test from Bug 3's own "macros must work" pass was
  rewritten for the opposite outcome, including the unparented-overlay
  scenario (now confirms the error is reached *because* `_box_at()`
  correctly resolves the shadowed macro, not skipped by a hit-test
  miss — the two fixes compose correctly, not by accident). **A real,
  confirmed gotcha found writing these tests**: an earlier, unmocked
  diagnostic script that let a real `QMessageBox.warning()` actually
  execute (rather than monkeypatching it, the same "blocking modal
  can't run headlessly" reasoning `AddPowerSupplyDialog.exec()` already
  needed) hung the whole test process indefinitely, waiting for a user
  click that can never come — every test exercising this path now
  monkeypatches `QMessageBox.warning` explicitly, not just the dialog.

  **A related, explicit follow-up in the same pass**: a macro PG-pin
  icon's own "interior" connector handle — the one facing INTO the
  macro's own body (the icon sits exactly on the box's boundary line,
  always anchored at `edge="top"`, so its own "bottom" handle points
  down into the macro's interior) — is dropped entirely, since nothing
  can ever be wired to a point inside a black-box macro's own body.
  `_INTERIOR_HANDLE_FOR_EDGE` (`supply_port_overlay.py`) maps each edge
  to its own opposite/interior key generically (not hardcoded to
  "bottom" specifically, in case a PG pin's own anchor edge ever
  differs in the future, even though it's always "top" today);
  `SupplyPortIconItem.__init__` skips constructing that one handle when
  `placement.fixed` is true. Scoped specifically to fixed (PG-pin)
  icons — a real, movable user-placed port still gets both handles
  regardless of which edge it's anchored to, confirmed by a dedicated
  contrast test. Verified connector routing itself doesn't depend on
  which/how-many handle objects exist at all (`_auto_waypoints()` uses
  the icon's own `scenePos()`/`_exit_direction()`, never a specific
  handle's position), so this is safe with no routing-side changes
  needed.

  **Documentation gap closed 2026-08-31**: this doc's own tracking of Milestone 3
  work stopped after commit `2b5d9f7` (2026-08-09) while real feature work
  continued for ~22 more days — the sections below (Retention Strategy through
  the dialog/export/power-net items at the end of this bullet) were all built,
  tested, and shipped during that gap and are being reconciled into this doc
  now, after the fact, from the real commit history and current code — not
  written alongside the work the way every earlier entry in this file was.

  **Retention Strategy** (`retention_overlay.py`, `retention_dialog.py`):
  diagram-only interactive placement of Retention on a Power Domain, same
  "mock-first, own directly-testable core" precedent every other placement
  tool here follows. `RetentionStrategyPlacement` (id, name, power_domain_id,
  primary/retention Supply Set ids, save/restore logic strings, `elements`
  — flat `"<scope>/<name>"` strings, enabled-only) with cross-tab undo/redo
  via `RetentionDiagramState`/`copy_`/`restore_retention_diagram_state()`.
  Deliberately a SEPARATE class from the pre-existing, read-only Milestone-2
  `diagram_overlay.py::IsolationBadgeItem`-style badges (`docs/frontend_milestone2_power_intent_visualization.md`'s
  own "retention as instance-level badges" line describes that older,
  `MappedDesign`-driven visualization only — this is new, editable, Milestone-3
  scope layered on top, not a replacement).
  - **Placement validation**: rejected on a macro (`.lib`) block, or on a
    block that isn't part of any Power Domain — checked before the dialog
    ever opens, same "nothing a dialog could fix" precedent as Supply Port's
    own macro check.
  - **Element collection** (`collect_retention_elements[_for_domain]()`):
    recursive, domain-boundary-aware — a nested sub-hierarchy belonging to a
    *different, more specific* Power Domain is excluded, resolved by
    re-querying `find_effective_domain_for_path()` per node rather than
    manual subtree bookkeeping. Multi-hierarchy domains merge/dedupe across
    every member.
  - **Cross-strategy element claiming**: an element enabled by one strategy
    can't also be enabled by another *in the same Power Domain* — shown
    disabled/italic in the dialog rather than hidden, with wildcard filter
    and Shift/Ctrl-click range-select over the Elements list (distinct from
    each element's own checkbox state) plus Add/Clear Selected and Add/Clear
    All buttons (superseding an earlier single "All" button).
  - **Accept-time validation**: no-spaces name, name unique *design-wide*
    (not per-hierarchy — an explicit, deliberate difference from Supply
    Port's own per-owner-path scoping), Primary/Retention Supply Set and
    Save/Restore Logic all required, **at least one Element required**.
  - **Duplicate/merge detection**: a strategy is a complete duplicate only
    when Power Domain, Primary Supply Set, Retention Supply Set, Save Logic,
    Restore Logic, AND the Elements set all match — a confirm-or-cancel
    QMessageBox offers to merge into the existing one rather than silently
    creating a redundant copy; a partial match creates a new strategy without
    asking.
  - **Badge rendering** (`RetentionBadgeIconItem`): live SVG via a shared
    `lru_cache`d `QSvgRenderer` (same pixelation-avoidance precedent as
    Supply Port), anchored to the block's own top-right corner, stacked when
    multiple strategies share a block. Colour follows the Retention Supply
    Set's own Power rail live (`resolve_retention_color_source()`), falling
    back to grey when unresolved; clicking any one icon of a multi-block
    strategy group-highlights every icon of it.
  - **Context menu**: Edit / **Info** (a new pattern — no prior "Info" action
    existed on this diagram before Retention) / Delete, the last now
    confirming via QMessageBox (previously deleted with no confirmation at
    all).
  - **Cascade warning**: removing a Supply Set still referenced as a
    strategy's own Primary or Retention supply shows a read-only warning —
    never auto-edits the strategy.
  - **Properties tab** ("Retention"): merges real + diagram-only rows;
    "Primary Supply"/"Retention Supply" columns (blank for a real row — the
    real backend schema has no `primary_supply` field) sit before "Elements",
    which uses the same shared, scrollable `_ElementsCellWidget` Isolation's
    own Elements column uses.
  - **UPF/CPF export bug, found and fixed**: `generate_upf()`/`generate_cpf()`
    never accepted or referenced `RetentionDiagramState` at all — Retention
    strategies were silently absent from every exported file despite the
    feature working correctly end-to-end in the GUI. Fixed: UPF emits
    `set_retention`/`set_retention_control`; CPF emits
    `create_state_retention_rule` (save/restore + supply roles folded into a
    comment — CPF has no native flag for primary-vs-retention role, not
    fabricated). A strategy with a dangling Power Domain reference is
    skipped, not crashed.
  - **Tests**: `tests/frontend/test_power_intent_retention.py` (validation,
    element collection/claiming, duplicate detection, dialog mechanics,
    context menu, cascade warning), `tests/frontend/test_multi_hierarchy_power_domain.py`
    (multi-hierarchy element merging), `tests/frontend/test_power_intent_properties_diagram_overlay.py`
    (Properties tab), `tests/frontend/test_power_intent_text_export.py`
    (UPF/CPF generation), `tests/frontend/test_properties_table_edit_remove.py`
    (Edit/Remove row actions). One flagged gap: the cascade-warning path
    (`_warn_about_retention_strategies_affected_by_removed_supply_sets()`) has
    no test whose name explicitly targets it — likely exercised indirectly
    via the broader Supply Set cascade suites, not confirmed directly.

  **Internal Supply** (`supply_port_overlay.py`): a second, distinct
  *concept*, but confirmed the SAME underlying dataclass as Supply Port —
  `SupplyPortPlacement.is_internal_supply: bool` (deliberately separate from
  the pre-existing `is_internal` flag, which is about a macro's own
  PG_INTERNAL pin sub-classification, not this). Mutually exclusive by
  hierarchy level, enforced UI-layer only (hover gating, context-menu enable
  state): Supply Port only on the design's true top-level block
  (`"/" not in box.path`), Internal Supply only on an inner, non-macro block.
  The "directly-testable core" functions (`add_supply_port()`/
  `add_internal_supply()`) stay level-agnostic by design, so the large
  pre-existing Supply Port test suite (which places ports on arbitrary
  blocks for unrelated tests) didn't need rewriting. Own SVG glyph, own
  `DiagramMode.INTERNAL_SUPPLY` tool/cursor/hover state, own UPF/CPF
  construct (`create_supply_net` alone — no `create_supply_port`, a real
  boundary-vs-internal distinction; CPF gets a separate labelled section).
  No dedicated Properties tab (explicit scope decision — excluded from
  "Supply Ports" entirely). A macro's own PG_INTERNAL pin renders with the
  same "internal" glyph via an OR of both flags at the one paint() call site
  (bug fix, `8198532`) — the two flags themselves stay unmerged.
  **Effective-state inheritance** (`trace_upstream_source()`/
  `resolve_effective_states()`/`describe_inherited_states()`): an Internal
  Supply has no states of its own — its effective states are derived live by
  walking the connectivity chain back to the ultimate source Supply Port,
  picking up one additive `"SWITCH-OFF (<name>)"` state per Power Switch
  traversed (cascaded switches each contribute their own). The "Add/Edit
  Internal Supply" dialog shows this as a read-only preview with per-line
  source attribution.
  **Naming-uniqueness saga — added, then fully reverted**: `6275bed`
  (2026-08-17) added global cross-hierarchy name uniqueness for Supply
  Port/Internal Supply; `ed977ab` (2026-08-30) removed it entirely — "two
  placements may now freely share a name with no forced relationship." A
  Power Switch's own restriction was re-scoped in the same commit from
  "blocked if names match" to a **name-continuity** rule instead: a switch
  can't be added on a target whose name equals its own upstream connection's
  name (breaking continuity between differently-named nodes would be wrong;
  two same-named nodes imply one continuous net, so a switch there is
  rejected). **Current code has no cross-hierarchy name-uniqueness check at
  all** — don't document `6275bed`'s rule as live behavior.
  **Tests**: `tests/frontend/test_internal_supply.py` (level rule, icon,
  tool mode, Properties exclusion), `tests/frontend/test_internal_supply_inheritance.py`
  (state inheritance through switch chains), `tests/frontend/test_internal_supply_properties_connectivity.py`,
  `tests/frontend/test_supply_name_cross_hierarchy_linking.py::test_power_switch_is_rejected_on_a_name_matching_its_own_upstream_connection`
  (the current name-continuity rule), `tests/frontend/test_power_intent_supply_ports.py:1260`
  (explicit comment block confirming the uniqueness-removal, both the former
  same-hierarchy and cross-hierarchy checks deleted).

  **Power Switch** (`power_switch_overlay.py`, `power_switch_dialog.py`): a
  new construct with a genuinely different connectivity model from
  Isolation/Retention (no Supply Set reference at all) — attaches to exactly
  one Supply Port/Internal Supply icon via two fixed anchors: **Supply
  Out** (bottom, automatic, 1:1 with the target, never independently
  drawn/deleted) and **Supply In** (top, manually drawn via the existing
  Supply Net connector-drag gesture, resolved fresh each time, no stored id).
  No control-signal field lives on the switch instance itself — "control" is
  only a pin *name* on the separate Cell entity (below).
  - **Validation**: max one switch per target (`find_switch_for_target()`,
    checks the plural `target_ids`); target must belong to a Power Domain; a
    fixed macro PG pin is never a valid target; the name-continuity rule
    above (added later, `ed977ab`).
  - **Pin names + Cell Type — REVERTED to per-instance free text**
    (`eceb7de`, 2026-09-02; documentation corrected 2026-09-11 by the
    codebase health audit, which found this section still describing the
    superseded model as current). `input_supply_port`/`output_supply_port`/
    `control_port` are plain, required, no-spaces fields directly on
    `PowerSwitchPlacement`; `cell_type` is a separate, OPTIONAL free-text
    label. **There is no `PowerSwitchCellPlacement`, no Cell library
    entity, no `power_switch_cell_id`, and no "Power Switch Cells"
    Properties table** — an intermediate design (`6a77d5f`) introduced all
    of that and `eceb7de` removed it again along with
    `CreatePowerSwitchCellDialog` and every cascade/edit/remove path, with
    no disabled remnants left behind (verified by grep during the audit).
    That design itself had superseded an even earlier free-typed "Cell
    Pins" text field (`2ec6eaf`/`bc1c29e`). An instance with no Cell Type
    assigned is not blocked — shown via an italic "No Cell Type
    Assigned" warning in the Properties table, same convention as
    Combinational Loop/Multiple Drivers markers. `on_state`/`off_state`
    (renamed from `on_logic`/`off_logic`): On State required, Off State
    optional. Cascade-delete on removing a Cell lists every affected switch
    in a confirmation dialog.
  - **Icon rendering — a real, deliberate special case**: `PowerSwitchIconItem`
    has NO `QGraphicsItem` parent at all (unlike every other icon type here,
    which parents to its owning box) — added scene-level and repositioned
    manually via `update_position()` registered on the target's
    `on_move_callbacks`. Fixed this way (`931413f`) after Qt's own "a child
    stacks above its parent" rule turned out to only resolve ordering within
    a shared ancestor context, letting the icon (which routinely pokes
    outside its target's own box) get hidden underneath an unrelated sibling
    box inserted later — the same bug class already found for
    `_BoxHighlightOverlay`'s hit-test shadowing and `diagram_overlay.py`'s
    own unparented `SwitchIconItem`/`SupplyRailItem`.
  - **Multi-hierarchy replication** (items 9/10): one shared
    `PowerSwitchPlacement` spans every member target
    (`target_ids`/`owner_paths`, mirrors Power Domain/Retention's own shape,
    not a separate replication-group scheme); editing/deleting from any one
    target updates/removes the whole record; clicking any replica
    group-highlights all of them. A per-replica connector key
    (`power_switch_connector_key(placement_id, target_id)`) was needed after
    a bug where every replica shared the bare `placement.id`, breaking
    per-replica manual Supply-In connectors.
  - **Auto-adopt existing connection** (item 8): if the target already had
    an incoming connector before the switch was placed, that connector
    re-routes to the switch's own top anchor atomically with switch
    creation (one undo step). Later generalized (`eac3182`) from a
    Power-Switch-specific mechanism into `_replica_icons()`/
    `_corresponding_replica_counterpart()`, shared with Supply Port
    replication.
  - **Layout room reservation** (item 5): a generic, milestone-agnostic
    `extra_top_margin` parameter on the shared layout engine
    (`hierarchy_scene_builder.py`), consuming the one shared
    `TOP_MARGIN_FOR_SWITCH` constant so the icon's own position and the
    layout's own reservation can't silently drift apart.
  - **Live colour-follow + name label** (items 6/7): `.color` is a live
    property reading the target's own colour fresh every paint, synced via
    the target's `on_recolor_callbacks` hook; the connector itself was later
    extended to follow the same live colour.
  - **Selection highlight**: click in Regular Cursor mode highlights
    fluorescent yellow, same convention as every other placement type.
  - **Delete confirmation**: a deliberate, scoped exception to this
    diagram's otherwise-uniform "delete immediately, undo is the safety net"
    convention — Yes/No, defaults to No.
  - **UPF/CPF export**: UPF emits real `create_power_switch -domain
    -output_supply_port [-input_supply_port] [-control_port]`; CPF's own
    backend has no output/control/cell-pin/on-off-state equivalent at all —
    a real, permanent, by-design CPF limitation (documented in the
    generator's own comments), not an oversight.
  - **Properties tab**: a single "Switch" tab (Name/Power Domain/Supply
    In/Supply Out/Cell Type/On State/Off State). The earlier split into
    "Power Switch Instances" plus a "Power Switch Cells" secondary table
    went away with the Cell entity itself (`eceb7de`) — there is no
    secondary table here anymore.
  - **Tests**: `tests/frontend/test_power_switch_diagram.py`,
    `test_power_switch_dialog.py`, `test_power_intent_properties_diagram_overlay.py`,
    `test_multi_hierarchy_power_domain.py` (replication),
    `test_properties_table_edit_remove.py`, `test_power_intent_text_export.py`.

  **Isolation Cell** — interactive editing added on top of the pre-existing,
  read-only Milestone-2 Isolation visualization (`diagram_overlay.py`'s own
  `IsolationBadgeItem`, still separate and unchanged). **Naming note,
  confirmed by direct inspection, worth stating plainly**: there is **no
  `IsolationCellPlacement`** anywhere in the codebase (nor, since `eceb7de`,
  a `PowerSwitchCellPlacement`/`LevelShifterCellPlacement` — all three
  "Cell" names are feature labels, not entities) — "Isolation Cell" is
  simply this tool/feature's own name for placing an `IsolationStrategyPlacement`
  directly; it is NOT a third instance of the "reusable library entity
  referenced by id" pattern the other two Cells share. Hover+click placement
  (`DiagramMode.ISOLATION_CELL`) or right-click "Add Isolation" on any
  Power-Domain boundary block; Add/Edit dialog with an Elements table gaining
  a Direction column (Input/Output/Both filter, reusing Port List data);
  colour-live badge rendering; cascade-delete with the Power Domain.
  - **Placement model, changed significantly across three commits**:
    `140a271` shipped a simple "one badge per boundary block" model;
    `98a2272` reworked it to place inside vs. outside the block based on
    `location` (self vs. parent/fanout), rendering one or two
    direction-flipped icon variants based on the elements' own IN/OUT
    composition, via a shared, generalized layout-reflow mechanism
    (`extra_left_margin`/`extra_interior_top`) also reused by Supply
    Port/Internal Supply; `facdd09` then made a self+parent pair on the same
    block share one Y (aligned) instead of stacking, before being folded
    into the fully cross-type unification below.
  - **No UPF/CPF export support at all** — confirmed via direct grep,
    unlike Level Shifter and Power Switch, which both export. Not flagged
    as a scope cut anywhere in the commit history; a real, silent gap.
  - **Tests**: `tests/frontend/test_isolation_cell.py` (37 tests),
    `test_isolation_dialog.py` (24 tests, incl. the Direction column/filter),
    `test_diagram_layout_expansion.py` (the shared layout-reflow mechanism).

  **Level Shifter** — an entirely new construct, built following the same
  architectural pattern as Isolation/Power Switch
  (`level_shifter_overlay.py`, `level_shifter_dialog.py`): tool mode,
  Add/Edit/Delete dialog, diagram badges (same inside/outside +
  direction-flip placement model as Isolation Cell above), a Power View tree
  node, and UPF/CPF export. **Cell Type is per-instance free text**, not a
  library entity: an intermediate design added `LevelShifterCellPlacement`
  and `CreateLevelShifterCellDialog`, and `eceb7de` removed both together
  with Power Switch's own equivalent (documentation corrected 2026-09-11 by
  the codebase health audit, which found this passage still describing the
  superseded model as current).
  - **Applies-To-All rework, and a same-day follow-up bug fix**: a broad
    "Applies To All" strategy legitimately has an empty `elements` list —
    the diagram-rendering side wasn't updated to match at first, so no badge
    rendered for one at all (`3b2df0d`, same day as the feature itself,
    explicitly self-flagged in the commit message as "the same 'one side of
    a feature built, the other silently incomplete' pattern already seen
    once for Power Switch and once for Retention-in-generation"). Fixed via
    `attachment_owner_paths()`/`variants_for_strategy_on_block()`.
  - **UPF/CPF export**: `set_level_shifter`/`map_level_shifter_cell` (UPF),
    `create_level_shifter_rule` (CPF) — real 1801/CPF syntax; cell-pin
    resolution deliberately skipped since the Cell entity carries no pin
    fields (comment in the generator, not a silent gap).
  - **Tests**: `tests/frontend/test_level_shifter_cell.py`,
    `test_level_shifter_diagram.py` (27 tests), `test_level_shifter_applies_to_all.py`
    (10 tests), `test_level_shifter_properties_panel.py`.

  **Isolation + Level Shifter — unified row-stacking system** (`c71642c`,
  generalizing `facdd09`'s Isolation-only alignment fix above): badges of
  BOTH types on the same block now share one combined row-stacking
  sequence — split into independent inside/outside sub-sequences, ordered
  "by type then creation order" (Isolation always builds first, an
  established codebase convention) — via a single shared
  `_isolation_badge_slot_counts_per_box()` (renamed from the earlier
  Isolation-only `_isolation_badge_counts_per_box()`), replacing a flat
  "Level Shifter starts counting past Isolation's total" workaround that had
  a real collision bug. Both `IsolationBadgeIconItem`/`LevelShifterBadgeIconItem`
  store their own `.location` so cross-type slot computation can tell inside
  from outside. **Tests**: `tests/frontend/test_isolation_level_shifter_row_stacking.py`
  (7 tests).

  **Power Domain multi-hierarchy** (`9f576a6` and follow-ups): Ctrl+click
  multi-select → "Create Power Domain" now creates ONE domain spanning every
  selected block, via `owner_paths: list[str]` on both `PowerDomainPlacement`
  and `SupplySetPlacement` (`owner_path` kept as `owner_paths[0]` for
  backward compatibility). Dialog polish landed alongside: "Applies to"
  shown one path per line, Supply Set names made globally unique, Primary/
  Secondary Supply Set creation merged into one dialog with a "Set as
  Primary" checkbox (replacing two separate combo-driven flows), Esc also
  clears the selection highlight, right-click no longer silently clears a
  multi-selection (a real `QGraphicsScene` default-behavior bug).
  Editing a replicated Supply Port/Internal Supply's name or colour from any
  one member hierarchy now propagates to every other replica (`53672f9`).
  A separate, real Qt paint-order bug (`362723a`) was also fixed here: a
  nested domain's translucent fill visibly blended with its ancestor's own
  (two alpha layers compositing) — confirmed via a genuine pixel-sampling
  test (`test_a_nested_domains_rendered_fill_does_not_visually_blend_with_its_ancestors`),
  not just a brush-colour assertion. `9892aaa` bundled three more fixes: replica
  group-highlight for Supply Port/Internal Supply (Retention/Power Switch
  already had it), the shared spacing constants doubling
  (`PADDING`/`GAP`: 14→28, 18→36 — not independently regression-tested at
  those exact values, though the mechanism is), and a Power Switch icon not
  reserving layout room until some later, unrelated rebuild.
  **Tests**: `tests/frontend/test_multi_hierarchy_power_domain.py` (37
  tests), `tests/frontend/test_power_domain.py`.

  **Ground-anchored Supply Ports/Internal Supplies/macro PG pins**
  (`cc797ce`, `eec933a`, `492d66d`): a supply whose role is Ground (derived
  live from a Supply Set's own `.ground` usage for a plain supply, or
  statically from `Port.related_ground_pin` for a macro pin) now renders at
  the block's own BOTTOM edge, rotated 180°, label centered inside the
  block — the vertical mirror of the pre-existing top-anchor convention.
  `_reclassify_ground_supply_edges()` runs live on every Supply-Set-touching
  edit so a name only just assigned a Ground role reclassifies immediately,
  not just on the next full rebuild. Layout support is a genuine mirror
  pair: `extra_interior_bottom`/`_extra_interior_bottom()` alongside the
  pre-existing `extra_interior_top`/`_extra_interior_top()`, reusing the
  same `INTERIOR_TOP_MARGIN_FOR_SUPPLY_PORT` constant rather than a second
  one. A macro's own fixed PG-pin icon previously never tracked its own box
  resizing at all — fixed as part of the same pass. **Tests**:
  `tests/frontend/test_ground_supply_placement.py` (13 tests).

  **Cross-hierarchy Supply name linking** (`cc797ce`, generalized further
  in my own most recent session's work — see "Ancestor Supply Set sharing"
  below): a Supply Set's Power/Ground/N-Well/P-Well pickers offer every
  Supply Port/Internal Supply name design-wide (excluding macro PG pins),
  not just the current hierarchy's own. Picking a name that already exists
  on a different, unrelated hierarchy auto-creates a linked Internal Supply
  there and auto-connects it via a real power net (the same connection path
  a manual net uses, so undo/redo and replication behave identically).
  Adding a Power Switch to a supply whose name is shared elsewhere is
  rejected (see the name-continuity rule under Power Switch above); renaming
  to a unique name resolves it and preserves the auto-created net. **Tests**:
  `tests/frontend/test_supply_name_cross_hierarchy_linking.py` (10 tests).

  **Supply Port Power States (DVFS-style)** (`a83705e`, `6421d7c`,
  `297dd2e`): a plain Supply Port gains an editable States list
  (Name/Type=Voltage-or-Off/Voltage), shared by the standalone "Add Power
  Supply" dialog and Create Power Domain's own inline "Add New..." flow via
  `SupplyPortStatesWidget` — a `QTableWidget` with per-row Type
  combo/Voltage line-edit cells, a column of "−" row-remove buttons, and a
  single "+" button outside the table. An Internal Supply never gets this
  section — it has no states of its own (see the inheritance mechanism
  under Internal Supply above); suppressed via an `is_internal_supply` flag
  threaded down from every "Add New..." entry point. A Type=Off state shows
  a display-only "0.0" in its Voltage cell (the underlying stored value
  stays `None`). Two "equivalent Active states" reverts along the way,
  documented as real dead ends rather than silently dropped: an earlier
  "General"/"Power States" two-section dialog layout was reverted back to
  flat after the section split turned out to be masking, not fixing, a real
  `QDialog.sizeHint()` caching bug (it caches its first-ever computed value
  and never re-derives it, so a later `adjustSize()` becomes a silent no-op)
  — the sizing bug itself is what actually got fixed, via an explicit
  computed target height/width rather than trusting Qt's own layout
  resolution (see the dialog-sizing entry below). **Power Domain's own
  colour is no longer user-chosen** (`a83705e`) — it's now derived live from
  the domain's Primary Supply Set colour at a fixed 80% transparency,
  propagated to the diagram fill, name label, and Power View tree node; the
  dialog's own read-only colour preview was later removed entirely
  (`6421d7c`) since the derivation already happens invisibly. **Tests**:
  `tests/frontend/test_supply_port_states.py` (41 tests).

  **Port Attribute dialog — replaced with a bit-wise table editor**
  (`c4a7520`, `frontend/src/power_intent_view/port_attribute_dialog.py`):
  a `QTableWidget` (Port Name/Direction/Receiver Supply Set/Driver Supply
  Set), one row per BIT of a multi-bit top-level port (`port_attribute_overlay.py::port_attribute_row_keys()`
  ungroups it; a single-bit port keeps one bare-name row) — replacing an
  earlier one-combo-per-whole-port form. Direction locks which column is
  editable (IN locks Receiver, OUT locks Driver — a locked combo
  structurally never contributes to submitted assignments, not merely
  visually disabled); INOUT keeps both editable and mirrored to the same
  value. Gained its own filter bar (`5111b5a`, the same shared
  `TableFilterBar` every Properties table already uses — the table shipped
  without one at first). A top-level port's boundary-facing Driver/Receiver
  Properties cell shows `"External - <name>"` (only the "External - "
  prefix italic — needed a new `QStyledItemDelegate`,
  `_PartialItalicPrefixDelegate`, since a plain item's font can only style a
  whole cell) when the value comes from a manually-asserted Port Attribute,
  vs. no prefix for a genuinely-traced Supply Set (`f86a593`). **Tests**:
  `tests/frontend/test_port_attribute.py` (21 tests),
  `tests/frontend/test_power_resolution_cache.py` (the "External -" prefix
  rendering, golden fixture-backed).

  **Properties table Edit/Remove for every entity kind, and diagram
  highlight on row selection** (`232f556`, `bfb47d6`): every Properties
  table row gets a generic right-click Edit/Remove context menu, dispatching
  by construct kind to the SAME dialogs/cascade-delete logic the diagram's
  own right-click-on-icon flow already uses — shipped covering 7 kinds
  (PowerDomain, SupplySet, SupplyPort, PowerSwitch, PowerSwitchCell,
  IsolationStrategy, RetentionStrategy), later grown to 9 as Level Shifter
  landed and reused the same generic mechanism by design. Power Domain
  removal gained a real cascade-impact confirmation it lacked before; Supply
  Set and Power Switch Cell got genuinely new edit+cascade-delete logic that
  didn't exist at all before this. Macro PG pins are excluded (intrinsic to
  the Liberty definition, not user-created). Selecting a Properties row also
  now highlights the corresponding diagram object(s) — resolves both real
  (legacy `MappedDesign` index) and diagram-only entities, deliberately
  never touching Qt's own `setSelected()` (would loop back into the scene's
  own selection-changed handler). **Tests**:
  `tests/frontend/test_properties_table_edit_remove.py` (34 tests),
  `tests/frontend/test_properties_row_to_diagram_highlight.py` (13 tests).

  **"Power View" tree tab** (`7c83815`, `5caca5c`,
  `frontend/src/power_intent_view/power_view_tree.py`): a genuinely separate
  `QTreeWidget` subclass (not a mode/toggle on the pre-existing hierarchy
  tree) — the Hierarchy Tree dock's own widget became a `QTabWidget` with
  "Design View" (the pre-existing RTL-hierarchy tree, now just relabeled)
  and "Power View" (new — organized by Power Domain, not raw RTL hierarchy:
  child ordering Supply Set → Power Switch → Isolation → Retention → nested
  domain → Macro), sharing the same `HierarchySelectionController` for
  macro-node selection sync but otherwise fully separate. Nodes colour-bind
  live to their own source (Power Domain from its Primary Supply Set, Supply
  Set/Retention from their own Power rail — `resolve_supply_set_color_source()`/
  `resolve_retention_color_source()`, each deliberately duplicated rather
  than shared, per this codebase's own small-overlap-duplication precedent).
  A second, auto-sized tree column shows each node's kind in italic grey
  (`_TYPE_LABELS`). Reuses the Properties table's own Edit/Remove dispatch
  on right-click. One real bug found and fixed here: `PowerViewTree`'s own
  `setSelection()` silently shadowed `QAbstractItemView`'s built-in virtual
  method of the same name — a real click set `currentItem()` but never
  actually selected the row, so nothing highlighted; renamed to
  `apply_selection()` (the same shadowing-bug class already fixed once
  before for `HierarchyTreeWidget`). **Tests**:
  `tests/frontend/test_power_view_tree.py` (32 tests).

  **"Inspect Source File"** (`17fcee7`,
  `frontend/src/power_intent_view/source_viewer_dialog.py`,
  `source_syntax_highlighters.py`): a non-modal source viewer (RTL or
  Liberty) reachable from a right-click on a hierarchy block or macro, with
  Ctrl+F search and syntax highlighting (Pygments for Verilog/SV/VHDL, a
  hand-written regex highlighter for Liberty — new `Pygments` dependency in
  `requirements.txt`). Also fixed a real, narrower gap while
  building it: the backend schema's own `Definition.source_file` field
  existed already but was never forwarded through
  `frontend/src/shared/models/rtl.py` or `backend_integration/backend_adapter.py`
  — the exact "adapter forgot to forward a field" bug class this project has
  hit before. **Tests**: `tests/frontend/test_source_viewer.py` (13 tests).

  **Merged Power Domain info box, and its own follow-up fixes** (my own
  most recent work, `ed977ab` through `5ccdc25`): consolidated the
  previously-separate Power Domain name label and Supply Set display box
  (itself only just added, `cc797ce`) into one combined `DomainInfoBoxItem`
  (`power_domain_overlay.py`), anchored bottom-right, showing the domain
  name plus one "Primary : <name>"/"Secondary : <name>" line per referenced
  Supply Set. Several real, non-obvious layout bugs found and fixed along
  the way, each with its own regression test: width computed from
  `horizontalAdvance()` (the cursor-advance distance) instead of
  `boundingRect(text).right()`, letting bold-italic/italic text spill past
  the box's own dashed border on some fonts; a baseline/top double-counting
  bug in the box's own Y-accumulation arithmetic (paint()/`_content_size()`
  reused a line's own baseline — already offset by its ascent — as the
  "top" reference for the next line, adding a full line's height again on
  top) producing both a phantom gap after the name line and an
  under-height box, fixed via a single shared `_stacked_line_layout()`
  helper (`power_domain_overlay.py`) that both the box's own rendering and
  `domain_info_box_required_height()` now delegate to, so the two can never
  drift apart again. **Ancestor Supply Set sharing** (CHANGE 2, `ed977ab`):
  a Power Domain's own "Supply Set" control can select an EXISTING Supply
  Set from an ancestor hierarchy as a shared reference (never cloned) —
  generalized in a follow-up (`d7f4262`) to actually materialize real
  Internal Supplies and auto-link them on the new hierarchy via
  `_auto_link_cross_hierarchy_supply_sets(target_owner_paths=...)`. **Add
  Power Supply/Add Internal Supply dialog sizing** (`9fb0347`): the same
  "don't trust Qt's own iterative `QFormLayout` sizeHint resolution" lesson
  already learned once for the Power States table (see above) was extended
  to the "Inherited Power States" label (an `adjustSize()`+`heightForWidth()`
  pin, mirroring the table's own fix) and to the dialog's own overall width
  floor (derived from the dialog's real `sizeHint()` instead of a flat,
  under-measured 420px guess) — both bugs shared this one root cause across
  three different owning dialogs (Create Power Domain, Retention, Port
  Attribute), confirmed by reading, not assumed. **Properties table tab
  rename/reorder + Retention "no sequential elements" warning + a real
  states-persistence bug fix** (`5ccdc25`): tabs renamed and reordered to
  Port List, Sequential, Supply Port, Supply Set, Power Domain, Switch,
  Isolation, Retention, Level Shifter, Power States (the "Power Switch
  Cells"/"Level Shifter Cells" secondary labels, not separate tabs, renamed
  to "Switch Cell"/"Level Shifter Cell" in place — both of those secondary
  tables were REMOVED outright a few commits later by `eceb7de`, along with
  the Cell entities themselves; "Power States" was likewise removed when
  the Power State Table became its own tab). Adding Retention to a
  hierarchy with zero sequential elements now shows a non-blocking italic
  "(No Sequential Elements)" warning on the Properties-table row — reusing
  the exact "No Cell Type Assigned" convention, not a new mechanism.
  Root-caused and fixed a real bug shared across every "Add New..." nested
  Supply Set flow (Create Power Domain, Retention, Port Attribute): Power
  States entered in the popped-up dialog were silently dropped before ever
  reaching the created Supply Port, since the whole `new_supply_ports`
  plumbing only ever carried name→colour, never states — fixed by threading
  a parallel states value the entire way through. **Tests**:
  `tests/frontend/test_supply_set_info_box.py` (13 tests),
  `tests/frontend/test_block_expansion_on_insertion.py` (35 tests),
  `tests/frontend/test_dialog_resizing_and_messages.py` (10 tests),
  `tests/frontend/test_power_intent_properties_panel.py`,
  `tests/frontend/test_power_intent_properties_diagram_overlay.py`.

  **Dialog usability conventions, established as a running thread across
  many of the features above** (tracked centrally in
  `tests/frontend/test_dialog_resizing_and_messages.py`, not duplicated per
  feature): every creation/edit dialog is user-resizable with an explicit,
  testable `setSizeGripEnabled(True)`; a nested "Add New..." nested dialog
  (reached via any Supply-Set-composing flow) renders at the SAME size as
  its standalone counterpart — both bugs traced to a missing `target_path`/
  width-floor gate that silently differed by entry point; every
  error/validation/confirmation message shown via `message_box.py`'s
  `warning()`/`question()`/`critical()` wrappers has its text
  mouse-selectable/copyable (`QMessageBox`'s own static convenience methods
  can't support this, so every call site was moved off them).

  **Error message standardization** (`49c94de`): every user-facing message
  wrapping an identifier placeholder uses single quotes consistently; entity
  type names are capitalized consistently (Power Domain, Retention
  Strategy, Power State, Power Net); Supply Port/Internal Supply uniqueness
  errors name the actual entity type instead of a generic "power supply".

  **Power-net routing: automatic obstacle-avoidance added, then fully
  reverted** — worth documenting precisely since the current code shows no
  trace of it. `e1c5df4` (2026-08-20) added segment splitting with
  KiCad-style dragging; reverted same-day (`f7079d7`). `b15e354`
  (2026-08-21) added a full A*-pathfinding `net_router.py` (corridor
  spacing, crossed-obstacle fallback rendering, recompute-on-layout-change,
  pinned manual-segment edits) that stuck around for three days across
  several unrelated feature commits touching connector code alongside it.
  `812edf6` (2026-08-24) reverted it completely — `net_router.py` deleted,
  every recompute mechanism removed — restoring power-net routing to a
  simple orthogonal elbow that avoids only `.lib` macro boxes, with a
  single, translation-invariant manual segment offset per connector, which
  is the mechanism still in place today. **Confirmed on disk**: no
  `net_router.py` file exists, and no reference to it anywhere in
  `frontend/`/`tests/`.

  **Three changes: trimmed export targets, a column rename, and the new
  "Power State Table" tab** (explicit request, on branch
  `feature/power-state-table`): (1) UPF 2.1 and CPF 1.1 removed as
  "Extract Power Intent" generation targets (`power_intent_text_export.py`'s
  `SUPPORTED_UPF_VERSIONS`/`SUPPORTED_CPF_VERSIONS`, now `["3.0", "3.1"]`/
  `["2.0"]`) — every other version stays; `main_window.py`'s own menu
  construction already builds from these lists, so no menu-building code
  needed to change. (2) The Properties table's "Switch" and "Retention"
  tabs' own "Domain" column header renamed to "Power Domain" (label
  only — Isolation/Level Shifter already said "Power Domain"; the real
  `domain_connections_table`'s own "Domain" column and the (now-removed,
  see below) "Power States" tab's own "Domain" column were out of scope
  and left alone).
  (3) **New top-level "Power State Table" tab**
  (`power_intent_view/power_state_table.py` — the directly-testable
  row/cascade data model, `power_state_table_tab.py` — the QMainWindow
  tab itself; wired into `main_window.py` as tab index 3, enabled by the
  literal same `_on_power_intent_loaded()` call that enables "Power
  Intent"), diagram-only/mock-first like every other Milestone 3
  construct here — replaces the standalone real-`PowerIntentDesign`
  "Power States" tab that used to live in `properties_panel.py`
  (removed entirely, including its own "Power State Groups" secondary
  table — neither construct is shown anywhere in this panel anymore;
  the per-Supply-Port/Internal-Supply "States" column elsewhere is a
  different, untouched data source and remains this new tab's own
  read-only input). A row is one Power-Domain-scoped state definition:
  a Power Domain picker (starts genuinely unselected — no default, a
  reversal of an earlier "default to topmost domain" instruction caught
  before implementation), a free-text State Name, and one dropdown per
  currently-defined Supply Net (a plain Supply Port or Internal Supply,
  excluding macro PG pins), each offering that net's own already-
  established Power States (`SupplyPortPlacement.states`, or an Internal
  Supply's live `resolve_effective_states()`). Net columns are ordered
  Power-role nets first, then Ground-role nets, classified via whichever
  Supply Set(s) reference each name as `.power`/`.ground` — a name never
  referenced as ground defaults into the Power group. Table starts at
  zero rows; "Add State" appends a fully empty one; each row's own
  trailing "-" button deletes just that row. **Two confirmed decisions**
  (asked via AskUserQuestion before implementation, not assumed): a
  deleted Power Domain deletes every row scoped to it outright (not an
  emptied cell); the "identical per-net configuration across every
  dynamic column" duplicate check is scoped PER POWER DOMAIN, same as
  the State Name uniqueness check (different domains may share either).
  "Check Power Intent" runs `validate_power_state_table()` fresh each
  time (never accumulates — reuses `DiagnosticsTableModel`'s own
  existing replace-per-source semantics for free) and flags: any empty
  cell, a duplicate State Name within one domain, and a duplicate full
  net-selection configuration within one domain (an already-incomplete
  row is excluded from the configuration check, to avoid every
  incomplete row spuriously "matching" every other one on shared
  `None`s). **Live-update cascade** is implemented via snapshot-diffing
  on `PowerIntentDiagramView.diagramStateChanged` (`net_state_snapshot()`
  before/after each tick, `reconcile_net_state_renames_and_removals()`)
  rather than hooking into the diagram's own mutation methods directly —
  a same-length before/after state-name list differing at one position
  is a rename (cell updates to the new name); a name vanishing with no
  positional match is a removal (cell clears, row survives); a Power
  Domain vanishing from the shared domain dict cascades to
  `remove_rows_for_domain()`; a Power Domain rename needs no cascade
  code at all, since rows reference `power_domain_id`, not a name, and
  the dropdown always repopulates with current names on rebuild. The
  Diagnostic section reuses `DiagnosticsTableModel` (the same
  replace-not-accumulate-per-source model `DiagnosticsDock` is built on,
  plus the same `TAB_SOURCES`-style `"power_state_table"` filtering key)
  but embedded inline in this tab's own central layout (table, then
  Diagnostic, then the Add State/Check Power Intent button row,
  top-to-bottom per spec) rather than as a separate `QDockWidget` — this
  tab has no closable/floatable dock of its own, so it gets no "View"
  menu group either, unlike every other tab here. All dynamic net-column
  cells share one globally-computed uniform width (`_uniform_net_column_width()`,
  the longest single entry across every net's own name AND every net's
  own Power State names combined, not computed per-column) — Power
  Domain/State Name keep normal `ResizeToContents` auto-sizing.

  **Four follow-up bug fixes/changes** (explicit request): (1) a
  reported Power Switch inheritance REGRESSION was first investigated
  with a vague repro — bisected against every commit the report itself
  suspected and reproduced across 6 independent layers, all correct;
  not reproducible with that first description (closed at the time via
  a permanent test,
  `test_real_mouse_drag_onto_an_internal_supplys_switch_inherits_the_sources_power_states`
  in `tests/frontend/test_power_switch_diagram.py`, combining a real
  `QTest` drag gesture with an Internal Supply switch target for the
  first time — a real, standing coverage gap on its own, even before
  the real bug below was found). **A follow-up, EXACT golden repro
  then found and fixed the real bug**: PD_TOP/SS_VDD (VDD=Power with
  ON 1.2V + OFF states) at the top level; PD_SUB at a nested hierarchy
  sharing SS_VDD as its own Secondary (CHANGE 2's ancestor-sharing) and
  composing a new SS_VDD_INT (VDD_INT=Power) as its own Primary; a
  Power Switch on VDD_INT; a power net drawn from the switch's own
  Supply In anchor to VDD at the top level. VDD_INT's resulting
  effective states showed ONLY "SWITCH-OFF (SW_A)", missing the
  inherited "ON"/"OFF". Root cause: CHANGE 2's own
  `_auto_link_cross_hierarchy_supply_sets()` gives the real, root-level
  "VDD" Supply Port a SECOND, unrelated connector of its own — an
  auto-linked "mirror" copy of the identical net, materialized as a
  separate, empty-states Internal Supply on the sharing hierarchy.
  `trace_upstream_source()`'s own hop-by-hop walk reached the REAL
  "VDD" via the switch chain correctly, but (having no notion of "a
  resolved, non-Internal-Supply placement is already a TERMINAL
  source") kept walking one more hop, wandering onto that empty-states
  mirror copy instead of stopping — so the trace's own final `resolved`
  pointed at the mirror, silently dropping the real source's own
  inherited states. Fixed by making `trace_upstream_source()` stop the
  moment it resolves a genuine, non-Internal-Supply Supply Port (a
  plain Supply Port's own `.states` are always terminal, user-authored
  data — the same top-level distinction `resolve_effective_states()`
  already makes, now also enforced at every intermediate hop, not just
  the outermost check). New PERMANENT regression test,
  `test_switch_chain_through_a_change2_shared_supply_net_still_inherits_the_real_sources_states`
  (`tests/frontend/test_internal_supply_inheritance.py`), reproduces
  this exact scenario via the real production methods
  (`create_power_domain()`/`_auto_link_cross_hierarchy_supply_sets()`/
  `add_power_switch()`/`try_connect_supply_ports()`, not hand-spliced
  connector state) — confirmed to genuinely fail without the fix
  (`[SWITCH-OFF (SW_A)]` only) before confirming it passes with it.
  (2) **Real bug, fixed**: the Power State Table showed a SEPARATE,
  duplicate column per Power Domain referencing a net shared via
  CHANGE 2's own ancestor-sharing mechanism
  (`_auto_link_cross_hierarchy_supply_sets()` auto-instantiates a
  genuinely separate `SupplyPortPlacement` — its own id — under the
  SAME name for every domain that shares an ancestor's Supply Set,
  connected back via a real power net) — `ordered_supply_nets()`
  listed every dict value with no dedup at all. Fixed via a new
  `_deduplicated_supply_nets()` (one representative placement per
  distinct NAME, preferring a genuine non-Internal-Supply source over
  an auto-linked copy when one exists, stable first-seen order),
  shared by both `ordered_supply_nets()` and `net_state_snapshot()` so
  a row's `net_states` key and a live snapshot's own key always agree
  on which placement id represents a given name. Regression test
  reproduces CHANGE 2's own worked example
  (`test_a_supply_net_shared_across_two_power_domains_appears_as_one_column_not_two`,
  verified to genuinely fail without the fix — `['VDD', 'VDD', 'VSS',
  'VSS']`). (3) "Add State" moved from alongside "Check Power Intent"
  to its own row directly below the table, above the Diagnostic
  section — "Check Power Intent" itself unchanged, still below
  Diagnostic. (4) Clicking "Check Power Intent" against a zero-row
  table now emits a `POWER-STATE-TABLE-EMPTY-TABLE` ERROR diagnostic
  ("No power states defined.") instead of silently reporting no
  findings — returned immediately from `validate_power_state_table()`
  before any other rule runs (every other rule is vacuously satisfied
  by zero rows anyway), and clears itself on a later run once at least
  one valid row exists, via the same replace-not-accumulate
  `DiagnosticsTableModel.set_diagnostics()` semantics every other
  finding here already relies on.

  **Isolation, Port Attributes, and the Power State Table wired into
  UPF/CPF export** (explicit request, "FIX IT" — closing all three real
  gaps confirmed above): `power_intent_text_export.py`'s `generate_upf()`/
  `generate_cpf()` both gain 4 new optional parameters —
  `isolation_state`, `port_attribute_state`, `top_level_ports` (the
  loaded RTL Design's own top-module `Port` list — needed to resolve a
  Port Attribute assignment's direction/bit-width, data this module
  never had access to before), `power_state_table_state` — all
  backward-compatible (default `None`, every pre-existing caller/test
  unaffected).
  - **Isolation**: UPF `set_isolation` (verified against golden.upf's
    own hand-written block, including its two distinctive shapes vs.
    every other multi-line command here — name+`-domain` merged onto
    ONE head line, and a 2-space, not 4-space, flag indent); CPF
    `create_isolation_rule`/`update_isolation_rules`, with
    `-isolation_power_net`/`-isolation_ground_net` resolved from the
    ISOLATION SUPPLY SET's own power/ground fields, not the domain's own
    primary supply (confirmed against golden.cpf directly — a real,
    non-obvious distinction). `-clamp_value` needs real UPF's literal
    0/1/hold, translated from this app's own "low"/"high"/"keep"
    internal labels (`_CLAMP_VALUE_UPF`); CPF's own `-isolation_output`
    takes "low"/"high" directly, no translation needed — a genuine,
    confirmed asymmetry between the two formats, not an inconsistency.
  - **Real, adjacent bug found and fixed while implementing Isolation's
    own Elements handling**: Level Shifter's ALREADY-SHIPPED UPF/CPF
    export used `_relative_path()` (strips only the design's own ROOT
    path segment) on its own Elements list — correct for a genuine
    hierarchy INSTANCE path (Power Domain's own `-elements`, Retention's
    own sequential-element paths), but WRONG here: Isolation/Level
    Shifter's own "Elements" are actually bare PORT names namespaced by
    their FULL owner_path (`"<owner_path>/<port_name>"`, per
    `collect_isolation_elements_for_domain()`'s own docstring) — every
    leading segment needs stripping, not just the first one. The
    pre-existing test covering this used a single-level-owner_path
    fixture ("top/clk") specifically contrived to make the wrong
    function produce the right answer by coincidence, masking that a
    real, multi-level element (e.g. "sram/u_sub/o_q[31:0]") would have
    been silently blanked to an empty string. Fixed via a new
    `_element_bare_name()` (`element.rsplit("/", 1)[-1]`), used by both
    Level Shifter and the new Isolation section; the masking test fixture
    was corrected to assert the honest result.
  - **A second, adjacent formatting bug found the same way**: Level
    Shifter's own `set_level_shifter` UPF command was ALSO already
    shipped, but rendered as one flat single line — golden.upf's own
    block shows the SAME two distinctive shapes as `set_isolation`
    (merged head line, 2-space indent), plus a different real flag order
    (`-applies_to`/`-location`/`-rule`/`-threshold`, not this app's own
    internal field order). Fixed to match exactly; section title also
    corrected from "Level Shifters" to "Level Shifter Rules" (golden's
    own literal title, in both UPF and CPF).
  - **Port Attributes**: UPF gets its own standalone `set_port_attributes`
    section (two commands, split by driver/receiver — an assigned port's
    direction decides which; INOUT contributes to both), positioned
    between Supply Sets and Power Domains, matching golden.upf's own
    section order exactly. A multi-bit port collapses back to ONE
    whole-port range entry (e.g. "a[7:0]") when EVERY one of its own
    bits shares the identical assignment — never per-bit — falling back
    to being skipped entirely for a genuinely mixed per-bit assignment
    (no honest single-line UPF representation for "half this port").
    CPF has NO separate section for this at all (confirmed against
    golden.cpf's own structure) — every assigned port folds directly
    into the design's own ROOT-level domain's `-boundary_ports` flag on
    the SAME `create_power_domain` line instead, which is now built
    conditionally multi-line (only when boundary ports exist — every
    pre-existing domain-without-port-attributes call site keeps its own
    original flat single-line form unchanged).
  - **Power State Table**: UPF gets a real, fully honest `create_pst`/
    `add_pst_state` legacy-PST block (this construct has NO Power Domain
    concept at all, so every valid row is included regardless of which
    single domain this app's own per-row scoping happens to reference)
    — column order comes from `ordered_supply_nets()`'s own natural
    Power-then-Ground/creation-order, an accepted, expected difference
    from any specific literal order a user might have described when
    building the table (forcing a literal order would mean hardcoding
    scenario-specific knowledge into an otherwise general-purpose
    exporter). CPF's own real `create_power_mode` needs a per-DOMAIN
    `-domain_conditions {PD_TOP@ON PD_SUB@OFF}` condition — data this
    app's own `PowerStateTableRow` genuinely doesn't have (one row
    references exactly one Power Domain, for its own uniqueness-scoping
    only; its actual values are per-NET) — synthesizing that flag would
    mean guessing a domain-level condition from net-level values with no
    honest general mapping, so (same "not confident enough to assert a
    fabricated flag, comment instead" precedent Retention/Power
    Switch/Level Shifter's own CPF sections already establish
    repeatedly in this file) only `-name` is asserted; the full
    net-state mapping is surfaced in an informational comment instead.
    A row missing a value for any currently-defined net is skipped
    entirely from both formats (matching "Check Power Intent"'s own
    empty-cell rule, applied at export time).
  - **A fourth, adjacent gap found and closed alongside Power State
    Table** (not one of the three originally named, but the same "no
    CPF equivalent existed at all" shape): `generate_cpf()` had NO
    "Power States" section whatsoever — `generate_upf()`'s own
    `add_power_state` (one command per SUPPLY) has no direct CPF
    equivalent; real CPF `create_nominal_condition` is instead a single,
    globally-reusable NAMED condition, so the new CPF section emits
    exactly one per UNIQUE STATE NAME across the whole design (not per
    supply) — verified against golden.cpf's own exactly-3-entries
    (ON/SWITCH_OFF/OFF) covering all 4 supplies' distinct state names
    between them.
  - Section order in both formats now matches golden exactly: Supply
    Ports/Nets/Connect/Supply Sets/**Port Attributes**/Power
    Domains/Power Switches/**Isolation Rules**/Level Shifter
    Rules/Power States/Macro Connections/**Power State Table** (UPF);
    the CPF equivalent, with Port Attributes folded into Power Domains
    as described above. `examples/upf_example_1_sram/` (Prompt.txt,
    golden.upf, golden.cpf, run_example.py) updated to build and export
    all three new constructs end-to-end against the real elaborated
    `sram`/`sub` design — confirmed the isolation section now matches
    golden almost exactly, port attributes matches exactly, and the
    remaining diffs are the already-accepted header banner, a few
    pre-existing unrelated gaps (Power Switch's own `-control_port`
    lacking a resolved net-name suffix; PST/domain iteration order), and
    cosmetic-only column-width choices for the new PST value block.
  - 12 new regression tests in `tests/frontend/test_power_intent_text_export.py`
    covering both formats of all three new sections, the multi-bit
    collapse/mixed-assignment-skip rule, the root-domain-only
    `-boundary_ports` scoping, the incomplete-row skip rule, and the
    new CPF Power States section's per-name dedup.

  **Attach a power intent file to a macro instance** (explicit request,
  `power_intent_view/macro_power_intent_overlay.py` + the "Load Power
  Intent"/"Edit Power Intent"/"Remove Power Intent" context-menu actions
  in `power_intent_diagram_view.py`): lets an existing UPF or CPF file be
  attached directly to a specific MACRO INSTANCE -- scoped per hierarchy
  `.path` (`MacroPowerIntentDiagramState.attachments: dict[path, file]`),
  never per Liberty cell/type, so two instances of the same macro can
  carry independently different (or no) attached file. Same diagram-only,
  mock-first scoping as every other Milestone 3 construct here.
  - **Selection/menu**: reuses the existing left-click yellow-fluo
    highlight and Ctrl+click multi-select mechanisms as-is (no new
    selection code) -- right-click on a macro (or on one of several
    selected) offers "Load Power Intent" when none is attached yet, or
    "Edit"/"Remove Power Intent" once one is (same Add/Edit split
    convention as Create/Edit Power Domain). A non-macro target, or a
    multi-selection mixing macro/non-macro or attached/unattached
    macros, disables all three (visible, greyed out, with a tooltip on
    the multi-select case -- single-target actions elsewhere in this
    menu are silently greyed out with no tooltip, so this matches that
    existing convention exactly for the single-target case too).
  - **Confirmed via explicit follow-up rather than assumed**: path
    storage is ABSOLUTE (same convention as every other loaded-file
    reference in this app -- RTL/.lib files, the Load Power Intent tab's
    own entry file -- never normalized at storage time); relative-when-
    possible is instead computed only at UPF/CPF GENERATION time,
    relative to the *output* file's own directory (the one real,
    well-defined base a generated `load_upf -file <path>` line is ever
    interpreted against) -- `power_intent_text_export.py`'s
    `_on_extract_power_intent()` call site was reordered to pick the save
    path BEFORE generating text, specifically so this base is known.
    Attaching/removing follows this diagram's own majority "delete
    immediately, undo is the safety net" convention -- no confirmation
    dialog, same as Retention/Isolation/Level Shifter removal.
  - **UPF generation**: a new, final "Load Upf" section, one
    `load_upf -file <path> -instance <hierarchy_instance>` line per
    attached macro, sorted by instance path.
  - **CPF generation -- researched, not guessed at (explicit
    requirement)**: fetched and read the Si2 CPF 1.0 primary specification
    directly. Finding: CPF has **no construct equivalent to UPF's
    `load_upf`** at all -- no in-language command lets one CPF file
    reference/load an external CPF *or* UPF file for a specific
    hierarchical instance. CPF's own "hierarchical integration" is a
    multi-file, EDA-TOOL-level flow (separate block-level CPF files given
    to the tool as distinct inputs, associated via
    `set_instance`/`set_design`), never a command inside the CPF language
    itself -- consistent with this project's own already-documented
    Milestone 2b finding ("CPF has no `load_upf` equivalent"). This gap
    is broader than the originally-asked-about cross-format case (a UPF
    file attached to a macro, exported to CPF): a **CPF**-attached file
    hits the identical wall when the export target is also CPF, since the
    real gap is "CPF has no file-loading command at all," not "CPF can't
    load a foreign format." Per this project's own "no clean equivalent --
    report it, don't fabricate a workaround" convention (same precedent as
    CPF's own `create_power_mode`/`-domain_conditions` gap), every
    attachment is instead emitted as an explanatory `#` comment in the
    generated CPF's own "Load Upf" section (never a bogus command), and
    `macro_power_intent_cpf_warnings()` surfaces the identical information
    as a generation-time `QMessageBox` diagnostic in the app's own UI,
    covering every attachment regardless of source format.
  - **Project save/load** (`.pis`): `MacroPowerIntentDiagramState` is an
    8th diagram-only state family, wired into `project_file.py`'s generic
    codec, `restore_all_shared_states()`, and `PowerIntentTab.load_project()`
    -- added as an optional field/parameter everywhere (not bumping
    `_FORMAT_VERSION`), so a `.pis` file saved before this feature existed
    still loads cleanly as "no attachments" rather than being rejected.
  - **Diagram label** (explicit follow-up request): a small "[UPF]"/
    "[CPF]" badge (`MacroPowerIntentBadgeItem`) anchored to the macro's
    own BOTTOM-RIGHT corner, reflecting `attached_file_format()` of
    whichever file is CURRENTLY attached -- live, not a one-time stamp:
    `attach_macro_power_intent()`/`remove_macro_power_intent()` always
    remove-then-rematerialize the badge (never an in-place text mutation),
    so replacing a UPF attachment with a CPF one via "Edit Power Intent"
    updates it immediately, and "Remove Power Intent" (or an undo back
    past an attach) removes it outright. Room is reserved via the SAME
    `_extra_interior_bottom()` "make room" mechanism every other bottom-
    anchored element here already funnels through (fixed-size, since the
    text is always exactly 5 characters either way) -- verified against
    `test_block_expansion_on_insertion.py`'s own permanent regression
    suite. **Collision avoidance generalizes, not duplicates, the
    EXISTING mechanism**: `DomainInfoBoxItem` (power_domain_overlay.py)
    already shifted itself up to clear a Ground-anchored supply via a
    live `owner_box.childItems()` check -- it now makes the identical
    check for a `MacroPowerIntentBadgeItem` sibling too, so the two never
    overlap on a block that happens to carry both (only reachable today
    via direct diagram-state construction, since a macro can never OWN a
    Power Domain through the normal UI flow -- confirmed against
    `create_power_domain()`'s own menu gating -- but the layout math
    stays correct regardless of how that state came to exist).
  - **Tests**: `tests/frontend/test_macro_power_intent.py` (path/format
    inference, validation, attach/remove + undo/redo, single- and
    multi-select menu gating, UPF/CPF generation including the CPF
    warning surfacing, project save/load round-trip and backward
    compatibility, one real end-to-end test through `MainWindow`'s actual
    "Extract Power Intent" action, and the diagram label's own UPF/CPF
    display, live format-swap update, removal-via-explicit-action and
    removal-via-undo, block-growth-without-clipping, and no-overlap-with-
    a-co-located-domain-info-box cases).

  **Features reconciled 2026-09-11 by the codebase health audit.** All six
  shipped with no documentation at all — the audit measured nine of the
  fifteen commits since the previous doc sweep (`fba043a`) as touching no
  doc file, and `scripts/check_doc_drift.sh` was reporting OK throughout
  because it compared timestamps rather than coverage (since rewritten —
  see that script's own header). Recorded now, after the fact, from the
  real commit history and current code:

  - **Virtual Supply** (`a071115`, `01d9f21`) — a THIRD supply type
    alongside Supply Port and Internal Supply, sharing their dataclass/
    icon/connector machinery via a third flag
    (`SupplyPortPlacement.is_virtual_supply`). Placeable at ANY hierarchy
    level (no level restriction, unlike the other two); exists only to
    drive a macro's own INTERNAL PG pins, so connection is one-directional
    and it is never a Power Domain member or a Supply Set rail (excluded
    from `known_supply_names_*()`). Carries its own AUTHORED Power States
    like a plain Supply Port, and unlike those two it keeps a deliberate
    design-wide NAME-UNIQUENESS check — the one intentional exception left
    after that rule was removed generally (`ed977ab`). A Power Switch on
    one is rejected. `01d9f21` fixed the icon's "V" glyph being recoloured
    along with the fill, by splitting the SVG into fill and glyph layers.
    Exports as a standard `create_supply_net` with its own power states.
    **Tests**: `tests/frontend/test_virtual_supply.py`.
  - **Hover-delay info tooltip** (`71f57f9`, `8240399`,
    `power_intent_view/hover_tooltip.py`) — a 1-second-delay tooltip on
    diagram elements, with per-element-type field lists, in a monospace
    face (Menlo/Consolas; JetBrains Mono confirmed absent before choosing).
    `8240399` added the Type field for Supply Port/Internal/Virtual Supply
    and macro PG-pin tooltips. **Tests**:
    `tests/frontend/test_hover_tooltip.py`.
  - **Save / Load Project** (`caff800`, `app/project_file.py`) — the whole
    project to a `.pis` file: gzip-compressed JSON, chosen over real
    encryption deliberately (the goal is corruption prevention — a text
    editor cannot re-save it without destroying the gzip framing, so an
    accidental save fails LOUDLY on next load rather than corrupting
    silently). A small generic dataclass round-tripper rather than ~25
    hand-written serializers; Supply Set sharing survives the round trip
    because the live model already references by id. **Tests**:
    `tests/frontend/test_project_file.py`.
  - **Extract Diagram** (`caff800`, `app/extract_diagram.py`) — renders
    open diagram tabs to PNG/JPEG/SVG, one file per diagram.
    **Tests**: `tests/frontend/test_extract_diagram.py`.
  - **Menu bar restructure** (`caff800`) — File | View | Extract | Guide,
    with "Extract Power Intent"/"Extract Diagram" moved out of File into
    their own top-level menu.
  - **App icon + green-accent theme** (`8a427b5`, `app/theme.py`) — a
    QApplication-level window icon plus a Windows-only
    `SetCurrentProcessExplicitAppUserModelID` call so the taskbar stops
    grouping the window under `python.exe`. The theme's accent is SAMPLED
    from the logo asset (`#9fe1cb`), not invented, with every shade derived
    by HSL adjustment from it. Deliberately does not touch the yellow
    selection highlight, severity presentation, or any Supply-Set-derived
    diagram colour. Table headers are deliberately left unstyled: any
    `QHeaderView::section` rule makes Qt drop the per-table bold-header
    font. **Tests**: `tests/frontend/test_theme.py`,
    `tests/frontend/test_app_icon.py`.
    **Fixed appearance, independent of the host (2026-10-04, explicit
    request)**: the app used to inherit the host's native style AND
    colours, so a Linux server in dark mode (GTK `Adwaita-dark`, passed
    to Qt by its gtk3 platform-theme plugin) painted the whole app dark
    under colours chosen for a light background. Because the app must
    run on every kind of machine, `main()` now calls
    `theme.apply_fixed_appearance(app)` right after creating the
    QApplication and BEFORE any widget: the Fusion style on EVERY
    platform (macOS included -- the native Mac look is given up for
    consistency, by decision), a light colour-scheme request where Qt
    supports it, and an explicit light palette (`LIGHT_PALETTE` /
    `LIGHT_PALETTE_DISABLED`) -- the palette is what actually does it:
    reproduced on the server's real theme, the style there was ALREADY
    Fusion and the window was still `#2d3234` until the palette was
    set. Highlight stays Fusion's blue, not the accent. The welcome
    page is dark on purpose and unaffected. `test_dock_narrow_resize.py`
    now SKIPS where the macOS style is unavailable (it used to fail on
    Linux, where `QStyleFactory.create("macOS")` is None); the shipped
    app can no longer hit that macOS-only bug, and
    `allow_narrow_resize()` stays as a guard. Tests in `test_theme.py`
    simulate a dark host and pin that `main()` applies the look before
    the window is built (verified to fail with the call moved after).

  **"Load Power Intent" mapping — importing an existing UPF/CPF into the
  diagram** (explicit two-part request, branch
  `feature/load-power-intent-mapping`, 2026-09-12). See
  `docs/load_power_intent_mapping.md` for the full writeup; that doc is
  the source of truth if the two ever disagree. **This is the first
  Milestone-3 feature that is NOT diagram-only/mock-first** -- it
  necessarily runs the real elaborator, since its whole job is to read a
  real file. It reaches the backend only through `backend.api` via
  `frontend/src/backend_integration/`, exactly like the app's own entry
  point already does, so the milestone seam is unchanged; what changed is
  that a Milestone-3 feature now depends on it.
  - **Layout parity first** (part 1): "Load Power Intent" is now the same
    two-pane `QSplitter` as "Load Design" (staging pane + "Add", then a
    "Loaded" pane the action button reads), with the compatibility note
    above the splitter and the diagnostics dock at the bottom -- the
    positions Load Design uses for its own informational/diagnostic
    areas. A pure layout change: every pre-existing control is still
    present and wired. `files_list` deliberately keeps its name and
    meaning (the LOADED list) rather than being renamed to match its new
    pane, so every existing caller and test that seeds it stays correct.
  - **`supported_features.py` -- the inspectable "what do we model"
    list**, classified per IR FIELD rather than per Tcl command (a
    command is not the unit of support: `set_isolation` is modelled, its
    `-source`/`-sink` filtering is not). 75 modelled / 25 unmodelled,
    each unmodelled one carrying a real reason. Zero backend imports.
    `scripts/show_supported_features.py` prints it.
  - **Three outcomes, never two**: a reference that doesn't resolve is an
    ERROR (reusing the backend's own `DANGLING`); a construct or field
    this GUI doesn't model is a WARNING under a NEW code
    (`POWER-INTENT-IMPORT-UNRECOGNIZED`), deliberately separate from the
    backend's `NOT_ATTEMPTED`/`UNRESOLVABLE_BLACK_BOX` rather than folded
    into them. Nothing is dropped without a trace. Import diagnostics get
    their own dock source key -- `DiagnosticsTableModel` replaces per
    source, so sharing the load phase's key would have made each batch
    erase the other's.
  - **`_BUILDER_OWNED_FIELDS` -- twelve fields where the generic mapper
    is confidently wrong for this dialect**, each with its reason, every
    one measured rather than assumed: isolation/level-shifter
    `-elements` are ports of the referenced DOMAIN (confirmed against
    golden.upf, where a root-scope `set_isolation -domain PD_SUB
    -elements {o_q[31:0]}` names a port one level down), retention
    `-elements` are registers the RTL resolver structurally cannot
    resolve, a switch's supplies are nets and its `-control_port` a cell
    pin name, and every strategy's domain link is resolved by name by the
    builders themselves.
  - **Hierarchical: flatten, EXCEPT onto a macro.** The same
    `load_upf -instance <x>` means two things, and only the elaborated
    RTL says which: a soft hierarchy is flattened (no second pass needed
    -- the elaborator already sources the sub-file inline under that
    scope), a `.lib` macro becomes an ATTACHMENT via the existing
    `macro_power_intent_overlay` feature. Filtered out before any builder
    runs, so no builder can forget the rule.
  - **Colour: assigns SUPPLY colours, not domain colours** -- a domain's
    colour is derived live from its primary supply set's power rail, so
    colouring domains directly would be overwritten on the next refresh.
    Reuses the existing six-hue palette in its original order and
    continues past six at the golden angle. **Measured, not assumed**: a
    pure golden-angle walk collided with the fixed palette (35.7 units
    apart at 8 domains, 28.1 at 20, against a threshold of 40), and
    lightness bands didn't fix it -- a candidate is now only ACCEPTED if
    it clears the threshold against everything already assigned. Exact to
    32 domains; 38.4 at 50, recorded rather than engineered around.
  - **Re-loadable, gated by a plain dirty flag.** "Load Power Intent"
    now STAYS enabled after a successful mapping -- a deliberate
    exception to this window's one-shot navigation, and the one place
    `_on_load_power_intent_continue()` no longer disables its own tab. The
    dirty flag is driven by the SAME `diagramStateChanged` signal the
    Properties refreshes already use, so there's no second notion of
    dirtiness to keep in sync; read through a `dirty_check` callable so
    `LoadPowerIntentTab` needs no reference to `PowerIntentTab`. A
    re-load is a full replace (section 7 is explicit: no merge logic), so
    the confirmation is one Yes/No defaulting to No, and
    `apply_imported_power_intent()` delegates straight to `load_project()`
    rather than being a second restore path.
  - **Acknowledgment dialog** naming the actual error/warning counts,
    shown when Continue is clicked past findings, counted from the same
    batch that enabled the button so the message can't disagree with the
    gate.
  - **Section 8 round-trip suite is the definition of done**
    (`tests/frontend/test_power_intent_round_trip.py`): generate every
    construct, re-load the generated file, expect zero errors, zero
    warnings, equivalent state. **UPF is lossless** with one named,
    asserted exception (an UNCONNECTED switch input pin name -- UPF writes
    `-input_supply_port {pin net}`, so there is no legal UPF carrying the
    pin without a net). **CPF cannot round-trip losslessly and the tests
    say so explicitly** rather than asserting parity: the format has no
    construct for a Supply Set, a switch's output supply, a retention
    supply role, an isolation control signal, a level shifter's
    rule/threshold, or a file-loading command. A CPF switch is therefore
    a WARNING, not an ERROR -- the format's limit is not a broken file.
    A generated CPF now elaborates whole and imports with zero errors.
  - **The round trip found ten real defects, none of which had a test**
    -- three UPF generator bugs (the version only ever appeared in the
    comment banner; supply nets AND supply sets were deduped by name
    alone and emitted at the root, silently relocating a nested one), one
    importer bug (the switch's Supply In connector wasn't rebuilt, so the
    pin name survived one trip and vanished on the next), three CPF
    generator bugs and three CPF elaborator gaps. All enumerated in the
    doc.
  - **Tests**: `tests/frontend/test_power_intent_round_trip.py` (24),
    `test_power_intent_import.py` (25), `test_supported_features.py`
    (24), `test_import_palette.py` (15),
    `test_load_tabs_layout_parity.py` (10),
    `test_power_intent_mirror_completeness.py` (5), plus 12 appended to
    `tests/backend/test_upf_elaborate.py` and a new
    `tests/backend/golden_upf/upf_generated_dialect/` fixture.

  **Hand-authored fixture validation** (explicit request, an iterative
  exercise deliberately COMPLEMENTARY to the round-trip suite above; full
  report and fixture index in `tests/frontend/golden_simple10/README.md`). The
  round-trip suite only proves the importer reads back the app's OWN
  canonical output -- it can only ever exercise the one syntactic shape
  the generator produces. Eight fixtures were hand-authored against a small `sram` design with one
  `.lib` macro -- now vendored at `tests/frontend/golden_simple10/` -- the
  way a designer or vendor actually writes power intent: different command
  ordering, equivalent-but-different flag spellings, naming that ignores
  this app's own conventions, a different syntactic style per fixture.
  **Every fixture caught at least one real defect: 20 findings, 17 of them
  real and fixed.** Promoted to permanent goldens with
  `tests/frontend/test_hand_authored_power_intent.py` (62 tests, each
  naming the defect its fixture caught).
  - **The two worst were colour-derivation bugs, the class the request
    asked to check separately from entity mapping** -- and both were
    invisible in the entity mapping: EVERY imported Power Domain came out
    the same default green (the live colour recompute runs only from the
    create/edit paths, never after a bulk restore, so the import's
    placeholder was never corrected -- fixed by deriving at import time
    via a new `resolve_supply_set_color_from_placements()`, the pre-view
    counterpart to the icon-based resolver); and EVERY CPF supply got the
    neutral grey, so all CPF domains were identical (role classification
    read only supply SETS, and CPF has no such construct -- it states the
    same thing per domain). Also: transparency was
    `PowerDomainPlacement`'s own 0.5 default rather than the rule's 0.8,
    and a strategy naming a nonexistent supply set imported as a silent
    `None`, rendering default grey with nothing saying why (the importer
    is the ONLY layer that can report those two fields -- the mapper
    classifies them `NOT_ATTEMPTED`, which validation then excludes from
    error counts by design).
  - **Whole features that did not work at all**: macro PG connectivity
    (`connect_supply_net -ports {u_sram/VDD}` was dropped entirely -- also
    the form the app's own generator writes, so a generated macro hookup
    did not survive reload either; the round-trip fixture simply never
    contained one); UPF's entire system-mode table (a legacy PST IS this
    app's Power State Table and the exporter already writes one, but
    nothing read it back -- now imported via a NINTH state family, since
    the table is owned by its own tab rather than the diagram); and
    multi-bit Port Attributes (stored under the bare port name, a key the
    editor never reads for width > 1, so the assignment existed in the
    state and was invisible in the GUI).
  - **Files rejected outright that should load**: `-function {nwell VNW}`
    RAISED on the function kind (right to fail loudly when the IR had
    nowhere to put it -- the real gap was the IR lacking fields
    `SupplySetPlacement` has always had); `-update`, real 1801
    successive refinement, was unrecognized on all three UPF strategy
    commands; and `add_pst_state -pst` (the LRM's own spelling) was
    REGISTERED as recognized but never read by the handler -- the
    "recognized but never read" failure mode this file already documents.
  - **A duplicate supply set was synthesised per domain**, and the domain
    pointed at the INVENTED one: the CPF-motivated synthesis fired on
    `DomainSupplyConnection`s alone, which the very common
    `set_domain_supply_net` idiom also produces in UPF alongside real
    named sets.
  - **`-control_port {pin net}` imported the NET into a GUI field that
    means the PIN.** Both halves are kept now, because both readings
    genuinely occur in this repo: `golden_upf/upf_basic` writes the bare
    form as a real RTL port and the mapper resolves it as one, while the
    generator writes the bare form carrying a pin name.
  - **Re-EXPORTING the fixtures found one more, and is the clearest
    argument for this third suite**: three flags in the UPF isolation
    writer and two in the Level Shifter one were emitted unconditionally,
    so an unset value became the literal text `None` -- which PARSES
    CLEANLY and returns as the string `'None'`. Silent corruption.
    Invisible to the round trip (a dialog-created strategy always has
    those fields set) and to the import-only harness (which stops before
    generation).
  - **The design used for iterations 1-7 was the WRONG COPY**, corrected
    afterwards on explicit request. The real `simple10` is a SIBLING of
    the repo (`<repo>/../design_examples/simple10`), so an in-repo search
    concluded it did not exist and fell back to
    `examples/upf_example_1_sram/design/` -- near-identical, but an older
    snapshot without `u_sub`'s flop or `rstn`. The real tree also did not
    ELABORATE when opened: `sub.sv` declared `o_q` as `output wire` while
    assigning it procedurally inside `always_ff` AND drove it a second
    time with a leftover continuous assign, so elaboration failed with
    "cannot assign to a net within a procedural context" (the tool
    reported it correctly, with file and line -- Milestone 1a's own
    post-shipping fix). Fixed to `output logic` with the stray assign
    removed, the only reading consistent with the `clk`/`rstn` plumbing
    threaded through both modules. Everything is re-pointed at the real
    tree; all tests pass against it unchanged, and because the design
    lives outside the repo the suite SKIPS rather than fails when that
    sibling tree is absent (accepted deliberately over vendoring a second
    copy of the RTL). This is what makes the Retention non-finding below
    demonstrable at all -- new fixture 08 omits `-elements`, the import
    expands it to `sram/u_sub/o_q`, and the badge renders. Re-pointing
    also exposed a third-order version of the same mistake, now pinned by
    a test: the reporting harness names the design tree independently of
    the tests, only one was updated at first, and the harness silently
    kept elaborating the old snapshot -- which made a retention
    `-elements` list look missing from generated output when the
    generator was in fact correct.
  - **Three non-findings are recorded too**, so they are not
    re-investigated: `set_level_shifter_cell` with a positional rule name
    was correctly REJECTED (`-level_shifter` is a flag per the 1801
    reference -- no tolerance for non-conformant syntax was invented);
    ground-edge placement and macro PG-pin colour turned out already
    correct, owned by the scene-build pass, verified empirically through
    the real `PowerIntentTab` rather than from a docstring and now pinned
    by a test; and a Retention strategy rendering no badge was NOT
    reproducible on the copy of the design then in use, which had zero
    sequential elements, so there was genuinely nothing to retain (the
    omitted-flag expansion was still missing and was fixed, verified at
    the time against `examples/soc_with_ram` and now directly on the real
    simple10 -- see the wrong-copy entry above).

  **Supply Port "Direction" field** (explicit request). A `Direction`
  dropdown on the Add/Edit Supply Port dialog, between Name and Colour,
  offering `<empty>`/`in`/`out`/`inout` and defaulting to **`in`** --
  omitting the direction is a deliberate act, not the default. Maps to
  UPF `create_supply_port -direction`.
  - **Both syntax questions were RESEARCHED before implementing, per
    explicit instruction.** UPF verb: `create_supply_port` (the
    request's own correction was right -- `create_power_supply` is not a
    UPF command, and a test asserts it appears nowhere in generated
    output). Values `in`/`out`/`inout`: two independent secondary
    references document `-direction {in|out|inout}` on this command, and
    the sibling `create_logic_port` carries the identical three values in
    OpenROAD's own UPF reference. Secondary-source tier, the same
    qualification this file already attaches to every UPF claim.
  - **CPF HAS NO EQUIVALENT -- established from the PRIMARY source**, not
    assumed: read straight out of the freely-available Si2 CPF 1.0
    specification. CPF has no supply-PORT construct at all to hang a
    direction on (`create_supply_port`/`create_power_port`/
    `create_ground_port`, and even the phrase "supply port", return ZERO
    hits in the whole spec); it declares supply NETS
    (`create_power_nets`/`create_ground_nets`, whose complete option
    lists carry no direction) and names ports separately via
    `set_design -ports`/`create_power_domain -boundary_ports`, because a
    port's direction comes from the RTL. `-direction` DOES exist in CPF
    on exactly one command -- `define_level_shifter_cell
    {up|down|bidir}`, voltage-translation direction, a different concept
    that it would be fabrication to reuse. The nearest adjacent concept,
    `create_power_nets -internal`, is the internal-vs-boundary
    distinction this app already models as Internal Supply, not
    direction. So per the standing "no clean equivalent -- report it,
    don't fabricate a workaround" convention (Supply Sets, per-net Power
    States, `-available_supplies`, macro attachments),
    `supply_port_direction_cpf_warnings()` surfaces it in the SAME "Not
    Expressible in CPF" dialog the macro-attachment gap already uses.
  - **Scope enforced in four places, each with its own test**, because
    all three supply kinds share ONE dialog class and ONE placement
    dataclass, so "does not apply" cannot be assumed: the dialog does not
    construct the combo at all for an Internal/Virtual Supply;
    generation emits no flag for either (neither gets a
    `create_supply_port` in the first place); the Properties table shows
    blank, matching how every other not-applicable column there is
    already handled; and the importer never assigns a direction to an
    internal net even when a same-named `create_supply_port` exists.
  - **Import**: `SupplyPort.direction` moves UNMODELLED -> MODELLED in
    `supported_features.py` (its old reason conflated two things -- the
    placement EDGE is derived from the Ground role and still is, but that
    was never what `-direction` means). A port that declared no
    `-direction` imports as `""`, NOT the `"in"` a newly-created port
    defaults to: the two states mean different things and only one
    generates a flag, so Edit pre-fills `""` rather than silently
    upgrading it on first edit.
  - **Properties table** gains a "Direction" column between Name and
    Connected. That shifted Connected/States by one, which broke 13
    tests across five files reading the old positions -- the indices are
    now named constants (`_SUPPLY_PORT_CONNECTED_COLUMN`/
    `_SUPPLY_PORT_STATES_COLUMN`) so the next insertion cannot silently
    shift them again.
  - **Tests**: `tests/frontend/test_supply_port_direction.py` (29),
    plus new golden fixture
    `tests/frontend/golden_simple10/power_intent/09_supply_port_directions.upf`
    and 3 round-trip tests covering all four values -- asserted on the
    generated TEXT as well as the reloaded state, since a bug writing
    `-direction None` could otherwise round-trip through this app while
    producing invalid UPF (exactly the shape of the literal-`None` bug
    the hand-authored exercise caught as its finding 20).

  **Real-UPF review of `examples/soc_with_ram/upf/soc_top.upf`**
  (explicit request -- the example was rewritten by hand into idiomatic
  UPF 3.1 because the original "was completely broken and not well
  implemented"). The rewritten file did not elaborate AT ALL: it failed
  on its first unsupported command, so nothing after it was even
  reached. Six real gaps, each a construct a hand-written file uses and
  this app's own generator never emits -- which is exactly why the
  round-trip suite could not have found any of them:
  - **`associate_supply_set SS -handle PD.primary` did not exist** --
    Tcl rejected the command NAME. UPF 2.1+'s third way of stating which
    supply set powers a domain; normalised onto the SAME
    `PowerDomain.primary_supply` the other two spellings
    (`create_power_domain -supply {primary SS}`,
    `set_domain_supply_net`) already land on, so nothing downstream
    learns a third representation. Only the `primary` handle is mapped;
    `.default_isolation`/`.default_retention` are rejected rather than
    silently swallowed into `primary`, which would attach the wrong set.
    Resolves the domain in its OWN scope first, falling back to a global
    by-name search -- a file sourced under `load_upf -instance u_sram`
    can legitimately declare a `PD_TOP` beside the parent design's, and
    a bare-name search then fails as ambiguous when the intended one is
    obvious.
  - **`add_power_state` could not take its object by FLAG**
    (`-supply SS_TOP` / `-domain PD_TOP`, the 2.1+ spelling) -- the
    positional was required, because the only shape ever tested was the
    one this app generates. Now optional, with the flag as fallback.
  - **...nor a UPF 3.x nested `-state`**: `-state {ON -supply_expr {...}
    -simstate NORMAL}` is a name followed by the state's own flags, not
    the `{name value}` pair the generator writes. The expression is read
    out of `-supply_expr`/`-logic_expr`; `-simstate` is recognised and
    skipped (no IR field -- simulation-only constructs are deliberately
    out of scope), and an UNKNOWN nested flag is a hard error, applying
    the "unrecognized flags must be rejected" rule one level down.
  - **`set_isolation -applies_to`, `set_level_shifter -input_supply`/
    `-output_supply`, `create_power_switch -supply_set`** were all
    rejected outright. Now parsed; the three with no GUI equivalent are
    captured in the IR and REPORTED as unmodelled rather than failing
    the file.
  - **A net reaching the boundary through `connect_supply_net <net>
    -ports {PORT}` with a DIFFERENT name imported as an Internal
    Supply** -- and silently lost the port's `-direction`, since that
    lookup was by name too. Only the same-name case was recognised,
    which is the only one this app's generator produces. Perfectly
    ordinary UPF, and arguably the more natural style; `VDD_IN` feeding
    `VDD_ALWAYS_ON` now correctly imports as a boundary Supply Port
    carrying `direction="in"`, while the switched `VDD_PERIPH_SW`
    correctly stays internal.
  **Four semantic mappings then specified explicitly** (follow-up
  request), each stating that one construct MEANS THE SAME THING as
  something this app already models --
  `tests/frontend/test_upf_semantic_mappings.py` asserts them as
  EQUIVALENCES (both spellings must produce the same imported state),
  not merely that the new one parses:
  1. `-handle PD.primary` == `create_power_domain -supply {primary SS}`.
     Already correct.
  2. `-handle PD.<anything else>` == `-available_supplies {SS}`. This
     CORRECTED the first implementation, which rejected every
     non-primary handle: half right (folding one into `primary` would
     attach the wrong set) but the alternative was never refusal --
     a secondary association belongs in `available_supplies`.
  3. `add_power_state -supply <set>` declares states on the SET, and its
     `-supply_expr` is exactly equivalent to per-net states, so it is
     DECOMPOSED per rail. The rail state's NAME comes from inside the
     term (`{power == {FULL_ON 1.2}}` declares `FULL_ON`, not the
     set-level `ON`), a rail named by several set-level states is not
     duplicated, and it keys off the supply-set FUNCTION so nwell/pwell
     work too. Without it, a file using the idiomatic UPF 3.x style
     imported with NO states on any supply at all, silently. Writing the
     equivalence as a test immediately caught a real asymmetry in the
     EXISTING per-net path: a bare `-state {OFF}` was imported as a
     VOLTAGE state with no voltage, because only the `-state {OFF off}`
     spelling this app generates was recognised.
  4. `set_isolation -applies_to` MATCHES the rule's own elements rather
     than being a separate broad mode -- resolved INTO the element list,
     so `applies_to` becomes MODELLED. `both`, an unknown value, and an
     unresolvable element are all left alone: filtering on a guess would
     silently drop a port the file explicitly listed.
  Pinned by
  `test_the_example_upfs_idiomatic_constructs_all_elaborate_and_map`
  (`tests/frontend/test_real_backend_integration.py`), which asserts
  each construct individually so a regression names the one that broke.

  **MAJOR REDESIGN: the power-states model** (explicit request; full
  writeup in `docs/load_power_intent_mapping.md`). Replaces how power
  states are modelled, displayed and generated. An accepted BREAKING
  schema change -- `.pis` files written under the old schema do not
  load, and no migration was written (confirmed, not assumed).
  - **The vocabulary**: `SupplyPortState` is now `state_type` in
    {FULL_ON, PARTIAL_ON, OFF, UNDETERMINED} plus a voltage, with NO
    NAME. Naming moved to the two levels that need it -- a Supply Set's
    derived states (STATE_1/STATE_2/...) and the Power State Table's own
    row names. The alignment is the point: UPF writes a rail state as
    `{FULL_ON 1.2}` and that leading token IS this vocabulary, so a
    state maps to UPF with no translation either way. Voltage is
    REQUIRED for the two levelled types and its cell is DISABLED for the
    other two -- which retires the old "two ambiguous no-voltage Active
    states" check outright (unreachable once a level is mandatory),
    leaving a plain duplicate-row check.
  - **TWO representations, deliberately**: `state_key()` (`"FULL_ON 1.2"`,
    the canonical identity a table row stores, and exactly the token UPF
    wants) vs. `state_display()` (`"1.2"`, what a cell shows). They MUST
    differ -- FULL_ON 1.2 and PARTIAL_ON 1.2 both display as "1.2" while
    being different states -- so the table's combos carry the key as item
    data and read `currentData()`, never the visible text.
  - **Power Switch: ONE OFF, never more.** Retracts the earlier
    distinctly-named "SWITCH-OFF (<switch>)" state. A switch in the chain
    adds a plain OFF; a source that already has one gets no second; and
    CASCADED switches collapse to at most one OFF in total (two switches
    in series can only produce one "unpowered" condition). The switch
    name survives only in the Inherited Power States preview's own
    provenance suffix.
  - **Virtual Supply is now selectable as a Supply Set rail** (rule
    reversal), participating in Tier 1 with no separate path. Supply Set
    membership is a LOGICAL grouping, distinct from the physical
    connection restriction, which is unchanged.
  - **Generation, two tiers, derived in ONE pass** so they cannot
    disagree about which STATE_N a row means: Tier 1 derives each Supply
    Set's own states by reading its four rails out of every Power State
    Table row, deduplicating identical combinations; Tier 2 emits ONE
    global `create_power_state_group <TOP>_PST` for the whole design
    (not one per domain), with one `-state` per row. `-simstate` is
    CORRUPT if any rail is OFF/UNDETERMINED. **No `-complete`** --
    confirmed, reversing an earlier worked example. Both the old per-net
    `add_power_state` and the old legacy-PST translation are GONE.
  - **One spec ambiguity resolved deliberately, not guessed**: a row's
    `-logic_expr` was scoped to "the Supply Sets that domain's rows
    reference", but two sets routinely SHARE a ground rail -- so a row
    selecting only SS_A's rails incidentally matched SS_B's ground, and
    constraining SS_B on that basis asserts something the row never said,
    through a thin `ground ==`-only expression. Relevance therefore turns
    on the POWER rail (the rail that defines a set, and the one a
    domain's condition and colour both derive from); a set with no power
    rail falls back to "any rail" so it is never silently excluded.
    Surfaced by a test written from the spec's own wording.
  - **CPF was ALREADY two-tier** -- researched against the Si2 CPF 1.0
    spec directly, so it needed reshaping rather than inventing:
    `create_nominal_condition` is Tier 1 but a single VOLTAGE (no
    multi-rail composition -- CPF has no supply-set construct at all),
    and `create_power_mode -domain_conditions` is Tier 2, per DOMAIN. So
    a domain's condition is its PRIMARY supply set's POWER rail. Two
    rules taken from the spec rather than guessed: OFF is a 0.0V
    condition, NOT a `-state off` flag (CPF 1.0 defines no such option --
    "a power domain is switched off if it is associated with a nominal
    condition whose voltage is 0"), and exactly ONE mode carries
    `-default` ("one, and only one"). This also CLOSED A REAL GAP:
    `-domain_conditions` used to be emitted as a comment because the
    per-domain data did not exist -- the supply-set derivation produces
    it.
  - **Round-tripping the new shape** needed two elaborator additions:
    `create_power_state_group` did not exist at all (Tcl rejected the
    command NAME) and `add_power_state -group` was unrecognized. The
    importer then rebuilds the Power State Table FROM the group,
    reversing both tiers. One property is asserted as a NAMED exception
    rather than hidden: a rail state no table row ever selects does not
    survive a round trip -- it is composed into no supply-set state, so
    it never reaches the file. That is the model working as designed.
  - **Tests**: `tests/frontend/test_power_states_model.py` (18, one
    section per lettered item of the request), plus ~100 constructor
    sites and every affected expectation updated across 15 existing
    files.

  **Two follow-up bugs, both found by USING the app rather than by any
  test** ("I try to use the app and write the UPF. I don see power states
  for supply sets and top level power domain (PST)"):
  - **The real "Extract Power Intent" action never passed four of the
    state families the generator accepts** -- `power_state_table_state`
    (which alone makes BOTH tiers produce nothing, exactly the reported
    symptom), `isolation_state`, `port_attribute_state`, and
    `top_level_ports`. Each of those sections was fully built on the GUI
    side AND fully supported by `power_intent_text_export.py`; the two
    simply never met at `main_window.py`'s own
    `_on_extract_power_intent()` call site. Invisible to the whole test
    suite because EVERY test covering those sections called
    `generate_upf()`/`generate_cpf()` directly and passed the state
    itself -- the exact "one side of a feature built, the other silently
    incomplete" pattern this file already records for Power Switch,
    Retention-in-generation and Level-Shifter-applies-to-all, but at the
    CALLER rather than inside the generator. New regression test
    `test_extract_power_intent_passes_every_state_family`
    (`tests/frontend/test_main_window.py`) drives the REAL action for
    exactly that reason and is confirmed to fail without the fix; the
    call site now builds one shared kwargs dict so UPF and CPF cannot
    drift apart again.
  - **A nested Supply Set was referenced by its BARE name from both
    root-scope power-state tiers.** A Supply Set composed on a nested
    hierarchy is DECLARED inside a `set_scope <hierarchy>` block (the
    Supply Sets section groups by scope), but Tier 1
    (`add_power_state -supply`) and Tier 2 (the group's `-logic_expr`)
    are both emitted at the root scope by design -- so `SS_INT`
    resolves to nothing there and the state silently applies to no set.
    Both now qualify the reference as `<hierarchy>/<name>`
    (`_qualified_supply_set_name()`, used as the key for BOTH dicts
    `_derive_supply_set_states()` returns, so the two tiers cannot
    disagree about how one set is named). **Checked before implementing,
    per the request**: a MULTI-HIERARCHY Supply Set gets exactly ONE
    qualified reference, not one per hierarchy -- the Supply Sets
    section groups by `owner_path` (singular, `owner_paths[0]`), so a
    spanning set is created exactly once, and a reference per
    `owner_paths` entry would name sets that were never created.
    Symmetric on import: `_find_supply_set_reference()`
    (`power_intent_import.py`) matches a qualified reference on
    (scope, name) -- strictly better than the bare-name lookup used
    elsewhere there, since two same-named sets in different scopes stay
    distinct -- and falls back to the tail for an unqualified file;
    `_LOGIC_EXPR_TERM` now admits `/`. **Adjacent, NOT fixed, reported
    instead** (outside the reported scope, same defect shape): several
    OTHER root-scope commands still name a nested Supply Set bare --
    `create_power_domain -supply {primary ...}`/`-available_supplies`,
    `set_retention -supply {...}`, `set_isolation`'s own supply flags,
    and the Port Attributes section. The round trip does not catch
    these because the importer's bare-name lookup resolves them anyway;
    a real tool would not.
  - **Tests**: 3 in `tests/frontend/test_power_states_model.py` (both
    tiers qualified, root-level sets left bare, the multi-hierarchy
    once-only rule), 1 in `tests/frontend/test_power_intent_round_trip.py`
    (the existing fixture already had a nested `SS_CPU`, now asserted on
    the TEXT as well as the round trip -- the importer resolving a bare
    name anyway is what let this go unnoticed), 1 in
    `tests/frontend/test_main_window.py`.

  **UPF generator correctness review (external expert review of
  app-generated output).** Findings sorted into pure generator fixes and
  real app data-model gaps, because a generator fix cannot emit data the
  app never collected.

  - **1.1 `set_upf_version` -> `upf_version`**; **1.2 `create_supply_set`
    takes `-function`, not `-supply`** (`-supply` is
    `create_power_domain`'s flag); **1.3 `map_power_switch` names the
    switch POSITIONALLY** (`map_power_switch {PG} -lib_cells {CELL}`) --
    this RETRACTS an earlier instruction in this project specifying
    `-switch_name_list`, confirmed not to be standard IEEE 1801-2018.
    Each was wrong AND self-consistent, which is exactly why nothing
    caught them: the elaborator accepts the wrong spelling as an
    alternative, and this project's own `golden.upf` used it -- which is
    where the generator copied it from. `golden.upf` and
    `tests/backend/golden_upf/upf_generated_dialect/` corrected too.
  - **1.4 Scope ordering.** A construct belonging to a nested hierarchy
    is now emitted inside `set_scope <path>` ... `set_scope .`,
    extending the pattern Supply Nets/Supply Sets already used to Power
    Domains, Power Switches, Retention, Isolation and Level Shifter.
    Emitted at the root, each named a domain, supply set or net only
    visible from inside that scope. A Power Switch had been scope-wrapped
    only when REPLICATED, on the reasoning that "`-domain` alone already
    names everything unambiguously" -- true of the domain, not of its
    input/output nets. A construct spanning SEVERAL hierarchies has no
    single scope to sit in, so it stays at the root and qualifies its
    references (`_supply_set_reference()`), which also closed the
    adjacent bare-nested-name gap reported one commit earlier.
  - **1.5 `-reuse`.** A net continued from an outer scope is
    `create_supply_net VSS -reuse`, not a second bare declaration --
    which declares a new, independent, FLOATING net. This app produces
    such nets constantly: cross-hierarchy auto-link and ancestor Supply
    Set sharing both materialise a same-named replica precisely to
    represent one continuous rail, so the bare form contradicted both
    features outright.
  - **1.6 `-off_state` -- NOT a bug, verified rather than assumed.**
    Traced dialog -> placement -> generator: a populated OFF State always
    reaches the output. The reviewed design simply had none (the field is
    optional, unlike ON State). Pinned by two tests, including that an
    unset one emits no flag rather than an empty one.
  - **1.7 ASCII whitespace.** Nothing here WRITES a U+00A0; the risk is
    user-typed data (a name, Cell Type, or ON/OFF expression pasted out
    of a spec) carrying one invisibly, where Tcl stops treating it as a
    word separator. Sanitized once on the finished file rather than per
    template, so a new call site cannot miss it.
  - **2.1/2.3 Power Switch: "Switch Control", and the contradiction it
    resolved.** The review was internally inconsistent on the switch's
    own port naming: 2.1's inline formula wrote `-control_port {<Control
    Port value> <Control Signal value>}`, using the user's pin-name field
    as the ABSTRACT port name, while 1.4's corrected example and 2.3's
    `-port_map` shape both use fixed `IN`/`OUT`/`CTRL`. Only the latter
    is self-consistent -- with the pin name already serving as the
    abstract name, every `-port_map` entry maps a name to itself and says
    nothing. Flagged, and confirmed by the user in a follow-up. So:
    `ABSTRACT_IN`/`_OUT`/`_CTRL` (`power_switch_overlay.py`, homed there
    rather than in the generator because the dialog needs them to say
    what its own fields mean) are what `create_power_switch` declares;
    the dialog's three port fields are the real LIBRARY CELL PIN names
    and reach UPF through `map_power_switch -port_map`; and a NEW
    required "Switch Control" field (`PowerSwitchPlacement.
    control_signal`) carries the RTL net, emitted as `-control_port
    {CTRL <net>}`. Positioned with the ON/OFF State fields, not the pin
    names, because those are expressions over it.
  - **ON/OFF State substitution.** Authored against the REAL signal (what
    the user can check against the HDL) and rewritten to `CTRL` on
    export, reversed on import (`substitute_control_signal()` /
    `substitute_abstract_control()`). Whole-identifier only, via explicit
    lookarounds rather than `\b` -- whose meaning flips when the pattern
    starts or ends with a non-word character, and a real HDL name carries
    `/` and `[]`. The trap a plain `str.replace` falls into is real: with
    signal `pwr_en`, the expression `pwr_en_cpu` would become `CTRL_cpu`
    -- a name resolving to nothing, in a file that still parses. An
    expression that never mentions the signal is emitted VERBATIM, not
    blocked; the dialog's own live inline hint raises it, at a point
    where the user can still act on it.
  - **The hint is inline, never a modal** -- a `QMessageBox` at accept
    time hung the whole test process (found live, the same headless
    hazard this file already documents), and a warning shown only after
    the user has committed is worse guidance than one visible while they
    type.
  - **A named round-trip exception RETIRED**: "an unconnected switch
    input pin name cannot survive" was a consequence of writing pin names
    only inside `-input_supply_port {pin net}`, which needs a net to pair
    with. `-port_map` states them independently, so the pin name now
    survives with the connector still absent. `PowerSwitch.control_ports`
    and `PowerSwitch.port_map` both moved UNMODELLED -> MODELLED.
  - **Three findings ruled OUT OF SCOPE by the user, recorded so they
    are not re-investigated.** All three asked this app to judge whether
    the power intent is CORRECT, where its job is to express faithfully
    whatever the user states -- the same line `docs/product_workflow.md`
    already draws with "not signoff-grade verification":
      - *Undriven supply net diagnostic* -- an undriven net is the
        EXPECTED, intentional shape of a Virtual Supply, which by design
        has no upstream driver (nothing can connect to its top; it has
        only a bottom anchor). A generic "flag any undriven net" check
        would fire on correct designs.
      - *PARTIAL_ON misuse warning* -- choosing PARTIAL_ON where FULL_ON
        is semantically right is a power-intent modelling decision, not a
        syntax or generation bug.
      - *Cross-domain isolation diagnostic* -- a domain feeding signals
        into a powered domain while itself off, with no isolation
        strategy, is a real power-intent bug, but it is the USER's to fix
        by authoring the crossings properly. Not an app defect, and not
        something this app detects.

  - **Golden fixture for the review's own scenario**
    (`tests/frontend/golden_upf_output/expert_review_scenario.upf`,
    `test_generated_upf_golden.py`). Every other UPF test here asserts a
    FRAGMENT, which catches a construct disappearing and misses
    everything about the file as a whole -- section order, scope
    nesting, an opened-and-never-closed `set_scope`, a flag emitted
    twice. Several review findings were exactly that shape. This pins
    the whole output for the review's scenario (`sram`/`u_sub`/`u_sram`,
    VDD/VSS/VDD_SUB/VDD_INT, switch PG), plus one named assertion per
    finding so a red build says WHICH thing regressed rather than "the
    golden changed". Built from DIAGRAM STATE, not by importing a file:
    the generator is what is under test, and feeding it an imported file
    would make an importer bug look like a generator bug. The header's
    own generated-at timestamp is blanked on both sides rather than
    dropped (its presence and position are still compared), with a
    second test guarding that the normalisation still matches something
    -- otherwise a reworded banner would silently make the golden fail
    on the clock. Regenerate with `UPDATE_GOLDEN=1`, and read the diff.
    Verified separately that the golden ELABORATES through the real
    backend with scopes, `-reuse` and `-port_map` all recovered.
  - **4.1 `-simstate` placement -- RESEARCHED, deliberately unchanged**;
    **4.2 macro retention -- documented as a known limitation.** Both now
    live in a new `KNOWN_LIMITATIONS` list in `supported_features.py`,
    rendered by `scripts/show_supported_features.py` alongside the
    per-field table. They needed a new home because that table answers
    "does the GUI have anywhere to put this IR FIELD", and neither of
    these is shaped like that -- one is a concept modelled differently,
    the other an open design question with no field to hang a reason on.
    On 4.1 specifically: IEEE 1801 puts `-simstate` on SUPPLY SET power
    states (it defines how logic powered by that set behaves in
    simulation); a power domain's own states carry logic expressions over
    those and have no simstate of their own -- confirmed against
    Marschner's DVCon paper "Unleashing the Full Power of UPF Power
    States", whose every example writes `add_power_state PD.primary
    -supply -state {... -simstate ...}` and never puts a simstate on a
    domain state. A domain's `.primary` handle IS a supply set, so that
    spelling and this app's `add_power_state <set> -supply` name the SAME
    object once the set is associated as primary. Emitting both would
    declare states twice on one object, which 1801 reads as refinement
    (`-update`), not as two independent declarations. The reviewer's
    report that some flows do not propagate between the spellings is
    flow-specific and secondary-source; per the review's own instruction,
    default generation was NOT changed on that alone.

  **Placeholder hints on every fillable field** (explicit request,
  `tests/frontend/test_field_placeholders.py`). Qt placeholder text --
  grey, inside the field, gone on first keystroke -- in one house style:
  `"e.g. <realistic example>"`, with an em-dash clarification only where
  the example alone does not convey the field's purpose. The Power
  Switch dialog is the reference and is the one place the WORDING itself
  is pinned; every other dialog is required to match the SHAPE. 23 fields
  across 8 dialogs, plus the two table-cell fields (a Power State's
  Voltage, a Power State Table row's State Name) and the fillable fields
  outside any dialog (Load Design's Top Module, Extract Diagram's
  filename/directory).
  - **Written as a SWEEP, not a per-field list**: every dialog is
    constructed and `findChildren(QLineEdit)` is walked, so the field
    added NEXT by someone who has not read the convention is what fails.
    A per-field list would pass forever while new fields arrive bare. The
    QLineEdit Qt builds inside an editable `QComboBox` is excluded -- a
    dropdown wearing a text box, whose own options are the hint.
  - **Guarded against passing vacuously**: both sweeps are "no offenders"
    assertions, which an empty list satisfies, so a separate test asserts
    the sweep still finds a floor of fields at all. Without it a Qt
    parenting change or a lazily-built form would turn both green while
    checking nothing.
  - **A filter box is a deliberate exception to the style** -- it takes a
    PATTERN, not a value. `TableFilterBar`/`ListFilterBar`/
    `wrap_with_filter()` gained an optional `example=`, APPENDED to the
    shared wildcard explanation rather than replacing it
    (`_filter_placeholder()`): only the caller knows what a realistic
    name looks like in its own table, but no user should lose the
    explanation of what `*` and `?` do just because their table gained an
    example. Isolation/Level Shifter get `data_out*` (both list port
    names, via the shared `build_elements_picker()`), Retention
    `*/state_reg*` (sequential-element paths), Port Attributes
    `data_in*`.
  - **Constructor arguments reused, not re-derived**: the sweep builds
    each dialog with the same minimal arguments
    `test_dialog_resizing_and_messages.py` had already verified against
    each class definition, so the two sweeps cannot drift into
    disagreeing about how a dialog is built.
  - A supplementary TOOLTIP is added only where the placeholder
    under-sells the field -- ON/OFF State, whose example is a bare signal
    name while the field accepts a full boolean expression. One further
    test asserts the obvious-but-worth-pinning property that a
    placeholder is a HINT, never content: a field showing one is still
    empty and does not satisfy a required-field gate.
  - **`QInputDialog.getText` has no placeholder API**, so "File > Create
    Project"'s own project-name prompt is not covered. Left as is rather
    than rebuilt as a custom dialog for a hint.

  **Power Switch dialog restructured into paired rows, plus a new
  acknowledge pair** (explicit request).
  - **Layout**: Applies to / Name / Power Domain as one group (the
    separator that used to split "Applies to" from "Name" is gone), then
    Cell Type, then FOUR PAIRED ROWS in a real `QGridLayout`
    (`paired_grid`) -- library cell PIN on the left, the real thing it
    connects to on the right: Input Supply Port|Supply In, Output Supply
    Port|Supply Out, Control Port|Control Signal, Ack Port|Ack Signal --
    then ON/OFF State. A grid rather than four independent horizontal
    boxes, so the right-hand column actually lines up across all four
    rows; inserted into the existing `QFormLayout` as a single spanning
    row, so the separator mechanism and the structural layout test keep
    working. **"Switch Control" is renamed back to "Control Signal"** and
    moved out of the ON/OFF State group onto its partner's row -- the
    pairing is what makes a cell pin and an RTL net read as two halves of
    one fact rather than two unrelated fields, which is how they were
    being misread in the first place. Supply In/Supply Out MOVED only;
    still auto-resolved read-only labels.
  - **CONFIRMED, not assumed**: Name and Power Domain both survive. Power
    Domain stays a READ-ONLY label, NOT the `<dropdown>` the request's
    own layout sketch showed -- a switch's domain is derived from its
    target's domain (and validation requires the target to have one), so
    making it selectable would be a real semantic change rather than a
    layout one. Flagged rather than silently either way.
  - **`-ack_port` -- RESEARCHED before implementing.** It is an OPTION on
    `create_power_switch`, not a separate command, with the SAME
    `{abstract-port real-net}` shape as `-control_port` plus an optional
    third boolean element: `-ack_port {port_name net_name
    [{boolean_function}]}`. Two independent references give this form for
    UPF 2.1+ alongside `-output_supply_port`. The sibling `-ack_delay
    {port delay}` is equally real and deliberately NOT emitted -- there
    is no delay field to emit from. So it DID follow the control pair's
    pattern, and is implemented analogously.
  - **"ACK" is this app's own convention, not verified syntax** -- the
    one thing the request asked to confirm rather than assume, and the
    answer is that there is nothing to look up: a switch port's name in
    `create_power_switch` is a USER-CHOSEN identifier, not a reserved
    keyword (published examples use Vin/Vout/Cin/Aout). ACK was picked to
    sit alongside this app's existing IN/OUT/CTRL, which are its own
    convention for exactly the same reason. `ABSTRACT_ACK` lives beside
    them in `power_switch_overlay.py`.
  - **CPF HAS an equivalent -- contrary to expectation, and read out of
    the Si2 CPF 1.0 PRIMARY spec directly**: `update_power_switch_rule
    -name <rule> -acknowledge_receiver <pin>`, documented as "an input
    pin in the design which must be connected to an output pin of the
    power switch cell". That is the DESIGN side, so it carries Ack
    SIGNAL, and it is emitted as a real construct rather than a comment.
    One spec inconsistency resolved in favour of the syntax: the
    `define_power_switch_cell` prose attributes the flag to
    `create_power_switch_rule`, but both syntax blocks and the quick
    reference put it on `update_power_switch_rule`, whose own syntax
    lists only `-name`/`-domain`/`-external_*_net`. The cell-side ack PIN
    genuinely has no home -- CPF states it on `define_power_switch_cell
    -stage_1_output`/`-stage_2_output`, a library-cell definition this
    app does not generate -- so `power_switch_ack_cpf_warnings()` reports
    it through the same "Not Expressible in CPF" dialog the
    macro-attachment and supply-port-direction gaps already use, and the
    message names what DOES survive so the reader is not left thinking
    the whole feature was dropped.
  - **Both halves OPTIONAL**: an empty pair emits no acknowledge
    construct at all (not an empty one), and `-ack_port` needs BOTH
    halves -- a port with no net states a connection to nothing, a net
    with no port has no port to attach to. A pin alone still reaches
    `-port_map`, which is about the cell rather than the connection.
    Optional does not mean unvalidated: a value that IS given still has
    to pass the no-spaces check.
  - **Backend**: `-ack_port`/`-ack_delay` registered on
    `create_power_switch` (list_options -- pair/triple-valued),
    `-acknowledge_receiver` on CPF's `update_power_switch_rule`,
    normalising onto the SAME `PowerSwitch.ack_ports` field UPF's net
    half lands on rather than a CPF-only one. New `ack_ports`/
    `ack_port_pins` IR fields, mirrored frontend-side, both MODELLED.
  - **Control Signal's generation is unchanged** by the rename/move
    (substituted into `-control_port` and into CTRL within ON/OFF State),
    asserted explicitly rather than trusted -- a rename is exactly the
    change that quietly reaches further than intended. Verified the
    whole-file golden is byte-identical apart from its timestamp for a
    switch with no ack pair.
  - **Tests**: 8 in `tests/frontend/test_power_switch_dialog.py` (paired
    rows, two-column placement, Supply In/Out still read-only in their
    new position, Name/Power Domain survival, optionality, validation,
    round trip), 6 in `test_power_intent_text_export.py` (both formats,
    the both-halves rule, the CPF diagnostic, the no-regression check),
    plus the ack pair added to the round-trip fixture and asserted on the
    TEXT as well as the reloaded state.

  **Isolation/Level Shifter: "Isolation Signal" rename + new "Name
  Prefix"** (explicit request).
  - **Label-only rename**: "Isolation Control" -> "Isolation Signal" in
    both the edit dialog and the read-only Info dialog. The widget, its
    attribute (`isolation_control_edit`), the placement field and both
    generated flags (`-isolation_signal` / CPF `-isolation_condition`)
    are untouched.
  - **NON-FINDING, recorded so it is not re-investigated**: Level Shifter
    has no equivalent field to rename. It has no control signal at all --
    voltage translation is continuous, not gated -- so the request's "if
    it has its own equivalent under a different name" branch does not
    apply. Pinned by a test.
  - **New "Name Prefix" field** on both dialogs, directly below Power
    Domain, optional. It names the inserted CELL INSTANCES (one strategy
    covers many ports, so a tool needs distinguishable per-port instance
    names), which is a different thing from "Name", which names the
    strategy. Deliberately NOT part of either duplicate-detection check
    -- a naming prefix is not a semantic distinction, and including it
    would let two otherwise-identical strategies both be created.
  - **RESEARCHED, and the result is an asymmetry the request did not
    anticipate.** `set_level_shifter -name_prefix` is VERIFIED (it
    appears in OpenROAD's own UPF command reference for that command, in
    a list evidently LRM-derived -- it carries obscure siblings like
    `-use_functional_equivalence`/`-applies_to_boundary`/`-no_shift`/
    `-force_shift` -- corroborated independently); its real `-name_suffix`
    sibling is not emitted, since there is no suffix field.
    `set_isolation -name_prefix` could NOT be verified from any of five
    references, all of which omit it. The naming-prefix concept DOES
    exist for isolation, but as UPF 1.0's `create_isolation_logic
    -prefix` -- superseded by `set_isolation` in 2.0+ and not a command
    this app emits. Absence is weak evidence (the same tool reference's
    `set_isolation` list also omits `-elements`/`-isolation_supply_set`/
    `-source`/`-sink`, all real and all already emitted here), so the
    state is genuinely UNCERTAIN -- and the standing rule for uncertain
    is report, never assert. A fabricated flag would parse cleanly in
    this project's own elaborator and be silently wrong in a real tool,
    the exact failure mode the literal-`None` bug already demonstrated.
  - **CPF expresses this MORE completely than UPF** -- the reverse of
    every other gap in this module, and read out of the Si2 CPF 1.0
    PRIMARY spec: `update_isolation_rules -prefix` ("the prefix to be
    used when creating the isolation logic", default `CPF_ISO_`) and
    `update_level_shifter_rules -prefix` (default `CPF_LS_`). Both are
    emitted as real constructs. So
    `isolation_name_prefix_upf_warnings()` is the first diagnostic here
    that fires for UPF rather than CPF, and it gets its own dialog title
    ("Not Expressible in UPF") rather than being filed under a CPF
    heading.
  - **Both CPF commands already ACCEPTED `-prefix` and never read it** --
    the "recognized but never read" failure mode this file already
    documents, found while wiring import. Both handlers now store it.
  - **Tests**: 7 in `test_power_intent_text_export.py` (the verified UPF
    flag, the deliberately-absent one, the omit-when-empty rule, both CPF
    flags, the UPF diagnostic, and the rename not touching generation),
    4 in `test_isolation_dialog.py`, 3 in `test_level_shifter_dialog.py`
    (including the Level-Shifter-has-no-control-signal non-finding).

  **aura_top audit, phase 4: the 10 category-(b) fixes** (report:
  `docs/aura_top_upf_audit.html`; fixture:
  `tests/frontend/test_aura_top_reference.py`, 18 tests).
  - **B1 `-reuse` rejected as a duplicate** -- the one that blocked the
    whole file, and a defect in this project's own `-reuse` work from
    two commits earlier. TWO scoping mechanisms reach
    `_create_supply_net` and only one was accounted for: `set_scope`
    changes `current_scope` so no collision arises, while `-domain` does
    NOT, leaving every net at the same scope. `-reuse` means "the SAME
    net, continued", so a collision is its expected condition, not an
    error: the existing record stands and the command only adds the new
    domain association.
  - **B9 silent state-type reinterpretation, now reported.** An
    unrecognized level-bearing token is still read as FULL_ON -- the
    behaviour is deliberately unchanged, since whether `ON` is legal UPF
    is one of the audit's open questions -- but
    `report_reinterpreted_state_types()` now says so, once per DISTINCT
    token rather than once per rail. It immediately fired on an existing
    hand-authored fixture's own `IO_ON`, which had been silently
    substituted all along.
  - **B2/B3 `add_power_state` shapes.** Two new shared-runtime
    mechanisms rather than command-specific hacks: `bare_or_valued` for
    a flag that occurs BOTH as `-supply SS` and as a bare marker after a
    positional object (disambiguated by whether the next token is
    another flag), and `name_then_group` for `-state NAME {flags}`
    alongside `-state {NAME flags}` (Tcl hands the first to Python as
    two arguments and the second as one; a single argument starting with
    `-` AND containing whitespace can only be a brace group, never a
    flag token). A flaw in B2's own first cut, caught by the fixture:
    the bare marker parsed to `True` and leaked a bool into
    `PowerState.supply`, typed `Optional[str]` -- silently breaking
    every by-name lookup downstream. A bare marker now resolves to the
    positional object it qualifies.
  - **B4/B6/B7 rejected flags**: `map_power_switch -domain`,
    `create_supply_port -domain`, `create_power_domain -include_scope`.
    The last normalises onto the `-elements {.}` this app already
    models, so both spellings produce an equivalent domain.
  - **B5 `-lib_cells {A B}` truncated to its first entry**, in both the
    UPF and CPF handlers. Real rules routinely offer two or three cells;
    `cell_type` is free text, so the whole space-separated list survives
    and is re-emitted verbatim.
  - **B8 dead legacy-PST generator.** `_power_state_table_upf_lines()`
    was written, verified, then left unreachable by the power-states
    redesign. Wired back in behind `generate_upf(include_legacy_pst=...)`,
    default off so existing output is byte-identical. **UI placement
    deliberately NOT decided** -- the reference emits both the legacy
    table and the modern group together, so this is a user option, and
    guessing a menu design was explicitly out of scope. `MainWindow`
    passes an explicit value, so adding a control later is a one-liner.
  - **B10 level-shifter cell mapping** moves from
    `set_level_shifter_cell -level_shifter X -cell_name A` to
    `map_level_shifter_cell X -domain PD -lib_cells {A B}` -- the
    confirmed signature, and the same shape `map_power_switch` was
    corrected to earlier. The elaborator accepts BOTH spellings, since
    files already written the old way must still load.
  - **Two findings about the REFERENCE FILE, not the app**, reported
    rather than worked around. Six `-isolation_signal iso_en_w[2]`
    values are UNBRACED, which in Tcl is command substitution -- it runs
    a command called `2` -- while every other bit-select in the file is
    correctly braced; any real UPF reader fails there identically. And
    `{FULL_ON $DVDD_NOM}` inside braces is NOT variable-substituted by
    Tcl, so the literal `$DVDD_NOM` reaches the IR; that one is Tcl
    behaving correctly and is pinned because it looks like a bug and is
    not one.
  - **The flagged-uncertain items stay flagged**, and a test says so:
    `set_isolation -name_prefix` is still rejected by the elaborator
    even though this file uses it six times. Accepting it would have
    resolved an open question phase 4 was told not to touch. The
    asymmetry is pinned too -- the same flag on `set_level_shifter` IS
    accepted, because there it is verified.

  **aura_top audit, follow-up: the two open decisions resolved.**
  - **B8 got a UI**, and the shape of the flow forced a judgment call
    worth recording: the request described "the export dialog, alongside
    the existing format/version pickers", and no such dialog exists --
    format and version are MENU items, then a native save dialog. A new
    `extract_options_dialog.py` sits between the two, rather than a
    checkable menu item (which would sit apart from the action it
    modifies and silently persist between exports). It is asked BEFORE
    the save dialog, so cancelling does not first make the user pick a
    filename for an export they then abandon.
    **UPF only, confirmed rather than assumed**: CPF expresses system
    modes as `create_power_mode`, which this app already emits
    unconditionally, and has nothing corresponding to
    `create_pst`/`add_pst_state` -- so `should_ask()` returns False and
    the dialog never appears, rather than appearing with a control that
    cannot affect the output -- enforced at the API, not just in the UI:
    `generate_cpf()` does not accept the parameter at all. Default OFF,
    pinned by a test comparing against the no-argument call: an export
    nobody opted into is byte-identical to what this app produced before
    the option existed.
    **Adding the modal immediately hung the whole test run** -- five
    pre-existing tests drive `_on_extract_power_intent()`, and a modal
    `exec()` headless waits forever for a click that never comes, the
    hazard this file already records more than once. Guarded by an
    AUTOUSE fixture in `tests/frontend/conftest.py` that auto-accepts it
    with its default, so a future test cannot be bitten either; the
    dialog's own behaviour is tested by replacing that stub with one
    that answers deliberately. Wiring the generator back in also
    exposed a missing trailing blank line in
    `_power_state_table_upf_lines()` -- unreachable until now, so the
    block ran straight into the next section's header banner.
  - **`set_isolation -name_prefix` accepted**, aligning it with
    `set_level_shifter`'s. Deliberately on REAL-WORLD EVIDENCE, not a
    verified signature -- the caveat is unchanged and recorded in both
    the registration comment and the test: no published option list for
    `set_isolation` carries it, and what tipped it is that a real
    hand-authored file uses it six times on that exact command.
    Accepting on READ asserts nothing into any generated file, so the
    generator still does not emit it and
    `isolation_name_prefix_upf_warnings()` still reports the gap.
    Reading tolerantly and writing conservatively stay separate
    decisions.
  - **A CORRECTION to phase 4's own summary.** That summary called
    `-name_prefix` "the one item blocking the file from loading
    unmodified". That was wrong, and this follow-up was scoped on it:
    accepting the flag removed one blocker of TEN. The unmodified file
    still does not elaborate, and the new
    `test_capstone_exactly_these_blockers_remain` pins the complete,
    ordered list so the claim cannot drift again -- NINE category-(c)
    constructs (`set_design_top`, `add_state_transition`,
    `add_port_state`, `map_isolation_cell`, `map_retention_cell`,
    `set_related_supply_net`, `-retention_condition`,
    `-assert_r_mutex`, `-force_shift`) plus the file's own Tcl defect
    (six unbraced `-isolation_signal` bit-selects). The capstone asserts
    the whole list, so it fails both if something new starts blocking
    AND if something on it quietly stops.

  **Three fixes from a fresh `sram_power_intent.upf` export.**
  - **`map_power_switch` was missing `-domain`** -- the same gap already
    found and fixed for `map_level_shifter_cell` (audit B10), and missed
    there because the two commands were corrected in different passes.
    VERIFIED rather than assumed, since the argument ORDER was
    explicitly in question: the documented shape is `map_power_switch
    switch_name -domain domain_name -lib_cells list`, so `-domain`
    follows the positional name -- which also matches the three
    `map_*_cell` commands' own documented order. Each replica of a
    multi-hierarchy switch carries its own domain.
  - **The legacy PST named a nested net BARE.** `create_pst PST
    -supplies {VDD VDD_SUB VSS}` -- while every other reference to that
    same nested construct in the SAME file was correctly qualified
    (Tier 1's `-supply u_sub/SS_VDD_SUB`, Tier 2's own `-logic_expr`
    terms). Fixed by REUSING `_supply_set_reference()` rather than
    re-deriving scope: that helper needs only `.owner_path` and
    `.name`, which a supply-port placement carries just as a supply-set
    placement does, so there is one implementation of the spelling rule
    instead of two that can drift apart. Its name and docstring were
    broadened to say so.
  - **`-port_map` is now conditional, decided PER SWITCH INSTANCE.**
    `port_map_needed()` (`power_switch_overlay.py`) is True when any pin
    field is set and differs from the abstract token it implements;
    when every field IS its token the mapping maps each name to itself,
    which is exactly the automatic matching UPF already does. Comparison
    is EXACT, case included -- UPF identifiers are case-sensitive, so a
    pin genuinely called `in` differs from `IN` and does need mapping.
    Omitting it when pins genuinely differ is not cosmetic: UPF then
    fails on unmatched pins.
  - **The confirmation is per-instance, not a blanket toggle.** The
    export dialog built for B8 gained one checkbox PER POWER SWITCH,
    each labelled with that switch's own name and computed verdict, each
    independently overridable; `generate_upf(port_map_overrides=...)`
    takes a `{switch id: bool}` dict, and a switch absent from it falls
    back to the computed default. Keyed by ID, not name, because a name
    is editable. The whole dict is passed even for untouched rows: once
    OK is pressed the default IS a confirmed choice, and passing only
    the toggled ones would leave the rest to be recomputed elsewhere.
    Rows are scrolled, since nothing bounds how many switches a design
    has. **A real bug found writing its own test**: the group box was
    built unconditionally and simply left out of the layout when a
    design had no switches -- which ORPHANS it, since the QScrollArea
    holding it was then the only owner, went out of scope, and took the
    group box with it, leaving a wrapper that raises "Internal C++
    object already deleted" on any access. Now built only when there is
    something to ask.
  - **CHECKED, not assumed**: `map_level_shifter_cell` has no
    `-port_map` to make conditional -- its documented signature is
    `level_shifter_name -domain domain_name -lib_cells list [-elements
    list]`. (`map_isolation_cell`/`map_retention_cell` do carry a
    per-port form, but it is `{-port port_name net_name}` reached via
    `-lib_model_name`, a different option -- and this app generates
    neither command, since neither Isolation nor Retention has a
    cell_type field.) Pinned by a test.

  **`-port_map` RETIRED; the four field values ARE the switch's port
  names** (explicit decision, and a reversal worth recording precisely
  because this point has now flipped twice).
  - `create_power_switch` emits the user's own Input/Output/Control/Ack
    Port values directly as the port name in each pair --
    `-input_supply_port {VDDH VDD}`, `-control_port {SLEEP sleep_b}`,
    `-ack_port {ACKO pwr_ack}`. The fixed `IN`/`OUT`/`CTRL`/`ACK` tokens
    are gone, and `map_power_switch` emits **no `-port_map` under any
    circumstances**. This also RETRACTS the conditional-per-instance
    `-port_map` work from the immediately preceding commit, which is
    removed rather than left disabled (its dialog rows,
    `port_map_needed()`, `_switch_port_map()` and the
    `port_map_overrides` parameter are all deleted).
  - **This restores the shape this generator had before the external
    review.** The review's own text was internally inconsistent here --
    one item wrote `-control_port {<Control Port value> <Control Signal
    value>}` (the field AS the port name) while its corrected example
    and its `-port_map` shape both used fixed tokens. The tokens were
    implemented because only they made `-port_map` mean anything; with
    `-port_map` gone, the reading that formula implied is the one that
    stands. A historical note in `power_intent_text_export.py` records
    this so it is not re-derived wrongly a third time.
  - **The ON/OFF State substitution now targets the user's own Control
    Port value**, passed in explicitly (`substitute_control_signal(expr,
    signal, token)`) rather than read from a module constant -- which is
    what makes the `-control_port` declaration and the expression agree
    by construction. Its inverse takes the same token, so import
    reverses exactly what export did.
  - **A named round-trip exception is RESTORED.** "An unconnected
    switch's input port name cannot survive" was retired when
    `-port_map` stated names independently of any net; with `-port_map`
    gone it is back, for its original reason -- UPF writes
    `-input_supply_port {name net}`, so with no net there is no legal
    place for the name.
  - **A hand-authored fixture's expectation changed, correctly.**
    `golden_simple10`'s vendor fixture writes `-control_port {sleep_n
    sleep_b}` and `-on_state {on_hi vin {sleep_n}}` -- an expression
    naming the PORT. The reverse substitution now targets the declared
    port name, which this file actually uses (where `CTRL` never
    appeared), so the ON State field correctly comes back as the SIGNAL
    `sleep_b`, which is what the user authors and edits.
  - **Placeholder DESCRIPTIONS corrected, labels deliberately not.** The
    three port placeholders read "the library cell's … pin name", which
    is no longer true; they now read "the switch's own … port name".
    `PowerSwitch.port_map` moves MODELLED -> UNMODELLED (read back if a
    file carries one, never generated). The FIELD LABELS themselves
    ("Input Supply Port", "Control Port", …) are unchanged pending an
    explicit decision -- see the rename proposal raised alongside this
    change rather than applied silently.

  **Per-supply state NAMES reintroduced; legacy generation uses them,
  modern Tier 1 deliberately does not.** The Power States table (Supply
  Port and Virtual Supply share one widget) goes back to Name | Type |
  Voltage. Name is required, no spaces, and unique only WITHIN one
  supply -- two supplies may each have an "ON".
  - **Several states may now share a TYPE.** Two FULL_ON at different
    voltages is an ordinary DVFS case and is explicitly legal, because
    the name disambiguates them -- which resolves the collision question
    that had been heading toward an auto-generated suffix scheme.
    Validation follows: the old duplicate-(Type, Voltage) ROW check is
    retired and replaced by a duplicate-NAME check.
  - **THE BOUNDARY, which is the whole point**: legacy
    `add_port_state`/`add_pst_state` declare and reference the name, and
    the Power State Table's own cells display it; **modern Tier 1 never
    reads it** and keeps deriving STATE_1/STATE_2/... A combined
    supply-set state spans several rails, so borrowing the power rail's
    name would collide whenever two combinations shared a power value
    and differed in ground/nwell/pwell. Tier 2 is untouched. Asserted
    explicitly by its own test, because "accidentally started using
    Names in Tier 1" is the exact regression this risks.
  - **A real hazard, caught by the tests and fixed by field ORDER**:
    `name` was first in the dataclass at first, matching the column
    order. `SupplyPortState("FULL_ON", "1.2")` is written positionally
    in ~100 places, and every one silently misassigned -- the type
    became the name, the voltage became the type, with no error
    anywhere. `name` is now the LAST field, defaulted; the widget's
    column order is set by COL_NAME/COL_TYPE/COL_VOLTAGE and is
    independent of it.
  - **Display split, deliberately**: `state_display()` (what a Power
    State Table cell shows) returns the NAME, while the cell's stored
    DATA stays `state_key()` -- so generation keeps deriving
    `-supply_expr` from the real Type/Voltage. Both are asserted in one
    test so the split cannot quietly close. `state_label()` keeps its
    old unnamed form exactly ("FULL_ON (1.0V)") and prefixes the name
    when there is one, so an imported design's tooltips are unchanged.
  - **The switch-contributed OFF is named `SWITCH-OFF`** (confirmed),
    chosen to avoid colliding with a plausible user "OFF". If a source
    genuinely has its own "SWITCH-OFF", the per-supply duplicate-name
    check catches it rather than two identical names being produced
    silently -- and a source that already has ANY OFF gets no
    contributed one at all, so the ordinary case cannot collide.
    Internal Supply's inherited preview now leads each line with the
    name.
  - **Legacy output** gains its own `# Port State` section
    (`add_port_state <net> -state {<Name> <value>}`, one command per
    net) ahead of `# Power State Table`, and `add_pst_state` now
    references BARE NAMES instead of the inline `FULL_ON 1.2` values it
    used to emit -- a change to existing output, not just an addition.
    The scoped-reference fix holds: a nested net is `u_sub/VDD_SUB`.
  - **KNOWN, and flagged rather than silently accepted**: legacy output
    is EXPORT-ONLY. `add_port_state` is one of the audit's category-(c)
    constructs and is still not registered in the elaborator, so a
    generated legacy file cannot be re-imported by this app. Registering
    it would reclassify an audit item and needs its own decision; the
    legacy block is opt-in and off by default, so nothing round-trips
    through it today.

  **The legacy-PST trigger becomes an EXCLUSIVE Legacy/Modern radio.**
  `include_legacy_pst` -- an ADDITIVE checkbox that put the legacy table
  alongside the modern Power State Group -- is replaced by
  `legacy_power_states`, a straight either/or. The two representations
  can no longer coexist in one generated file.
  - **Exclusivity is enforced in two independent places, deliberately.**
    In the UI, the two radios share one `QButtonGroup` with
    `setExclusive(True)`, so no interaction can check both. In the
    generator, the parameter is a single boolean: there is simply no
    value it can take that means "both", which is the property the
    change exists to create. A test asserts the co-occurrence property
    directly for BOTH values rather than testing each branch alone.
  - **Modern is the default**, so an export nobody changed stays
    byte-identical to what this app produced before the choice existed
    -- pinned by comparing against the no-argument call.
  - **Section headers follow the choice**, as already built: Legacy
    writes `# Port State` / `# Power State Table`, Modern writes
    `# Supply Set Power States` / `# Power State Group`.
  - **CPF is structurally unaffected**, not merely unaffected by
    convention: `generate_cpf()` does not accept the parameter at all,
    and always emits its own `create_power_mode` form.
  - **Tier 1 is still DERIVED under Legacy**, only its UPF output
    withheld -- CPF and Tier 2 both read `row_state_by_set`, so
    suppressing the derivation rather than the emission would have
    broken CPF as a side effect.
  - One test had to change meaning rather than expectations: the legacy
    scoped-reference check used to compare `create_pst`'s spelling
    against Tier 1's in the SAME file, which exclusivity forbids. It now
    cross-checks against the modern export of the same design.

  **Legacy `# Port State` used the wrong command for a net with no port,
  and the Legacy/Modern dialog rebuilt as described option cards**
  (explicit request, two parts in one pass).
  - **`add_port_state` is only for a net that genuinely IS a port.** An
    Internal Supply and a Virtual Supply are each emitted as
    `create_supply_net` ALONE -- no `create_supply_port` anywhere -- so
    naming one in `add_port_state` states a port that was never
    declared. Both now use `add_supply_state`, split by
    `_legacy_state_command()` off the same two flags the rest of the
    generator already keys on. **`add_supply_state` COULD NOT BE
    VERIFIED** against any reference, and that is recorded at its own
    constant rather than buried: two independent searches found nothing,
    one explicitly said the standard command is `add_power_state`, and
    the canonical legacy-PST material lists only `add_port_state`/
    `create_pst`/`add_pst_state`. Implemented as specified, isolated in
    one constant so a correction is a one-line change. Worth knowing:
    `examples/aura_top/upf/aura_top.upf` faces exactly this case
    (VDD_CPU/VDD_DSP/VDD_PER are `create_supply_net` with no port) and
    solves it a THIRD way -- by putting the driving switch's own output
    port (`SW_CPU/vout`) in the PST instead.
  - **A second, deeper half of the same bug**: the section read each
    net's AUTHORED `.states`, and an Internal Supply authors none (its
    states are traced through the switch chain back to the real source),
    so every Internal Supply was missing from this section ENTIRELY
    while `add_pst_state` rows still referenced names for it. Now
    resolved through `resolve_effective_states()` -- the same resolver
    behind the "Inherited Power States" preview and Tier 1's own
    `-supply_expr`, so all three agree about what a net's states are.
  - **THE BROADENING CHECK was asked for explicitly and is asserted, not
    reasoned about**: `# Port State` was the only place legacy
    generation named a command per net. The other two constructs carry
    no port assumption by construction -- `create_pst -supplies` takes
    supply NETS, `add_pst_state` references state NAMES -- and a test
    pins that both list the non-port-backed nets on equal terms while
    `create_supply_port` is emitted for neither.
  - **An unnamed state is now DECLARED under a derived name, not
    skipped.** The first cut skipped it (nothing for `add_pst_state` to
    reference) and dropped every row selecting it -- silently, and
    without the row being wrong. `_legacy_state_names()` keeps an
    authored name as-is and otherwise uses the state's TYPE, suffixed
    only where that would collide within the same net. One token either
    way, which is the property the positional list depends on -- the
    physical key ("FULL_ON 1.2") is two, which is what made it
    unusable as the fallback in the first place. Only a STALE row (one
    selecting a state the net no longer has) is dropped now.
  - **The export dialog is two described CARDS, not a bare radio pair.**
    Each option carries a plain-language TITLE, the underlying command
    names as a monospace SUBTITLE (what a reader searches the generated
    file for -- kept visible, just not AS the title), a two-sentence
    description, and a real snippet of the output, grouped in a `QFrame`
    so it is obvious at a glance which text belongs to which choice.
    Colours come from `app/theme.py`'s sampled-from-the-logo palette
    rather than being invented here. Sized from the computed content
    (the recurring `QDialog.sizeHint()` caching lesson), and the snippet
    is mouse-selectable. The test asserts against the WIDGETS, not the
    constants -- the defect it guards is a constant existing while
    nothing displays it, which is exactly what a bare radio pair looks
    like from outside -- and a second test checks each card's advertised
    commands actually appear in the file that option produces.
  - **A real headless hazard hit again while writing these tests**: a
    save path in a directory that does not exist makes the export raise
    and pop a REAL modal, hanging the run. Named in the test rather than
    worked around silently.
  - **Tests**: 6 in `tests/frontend/test_power_states_model.py` (the
    per-net command split, the alignment property asserted by COUNTING
    tokens, the broadening check, the derived-name fallback), 2 in
    `tests/frontend/test_main_window.py` (the cards, and that they drive
    generation as they claim).

  **One blank line between distinct instances, everywhere** (explicit
  request, both formats). Each distinct INSTANCE's block is separated
  from the next by exactly one blank line; the separator never appears
  INSIDE one — not between a multi-line command's own continuation
  lines, and not between a construct and its own directly paired second
  command.
  - **The premise needed correcting, and it is recorded rather than
    quietly worked around.** The request named three sections as already
    correct. Only ONE was: "Supply Set Power States" genuinely separated
    its `add_power_state -supply` instances. **Supply Sets** and **Port
    State** did not — measured, not assumed, by generating a design with
    2+ of every construct and reading the output. So those two were
    fixes, not confirmations.
  - **Two sections had the blank INSIDE an instance** and this change
    REVERSES both: `create_power_switch`/`map_power_switch` (an earlier
    explicit request put a blank there) and `set_level_shifter`/
    `map_level_shifter_cell`. Also removed: the stray blank Isolation and
    Level Shifter emitted before a group's closing `set_scope .`, and
    CPF's own blank between `create_isolation_rule` and its
    `update_isolation_rules`.
  - **Two shared helpers replace per-section separator logic**
    (`_join_blocks()` / `_scope_group()`). Every section previously grew
    its own `if i < len(...) - 1: append("")`, which is exactly how they
    drifted: some separated instances, some separated nothing, and the
    scope-grouped ones separated only WITHIN a group, never between
    groups — so a design with one construct per hierarchy (the common
    case) got no separation at all. Sections converted: Supply Ports,
    Supply Nets, Connect-external-ports, Supply Sets, Port Attributes,
    Power Domains, Retention, Power Switches, Isolation, Level Shifter,
    Supply Set Power States, legacy Port State, Macro Connections, Load
    Upf — and the CPF counterparts, plus Nominal Conditions and Power
    Modes.
  - **What is deliberately NOT separated, flagged rather than assumed**:
    rows WITHIN one construct. `create_pst` plus its own `add_pst_state`
    rows is one table (and those rows are column-aligned precisely to be
    read as one), `create_power_state_group` plus its single
    `add_power_state -group` is one group, and CPF's own Load-Upf
    explanation is one comment paragraph. Separating those would break
    the alignment that exists to make them readable.
  - **Tests**: `tests/frontend/test_generation_blank_line_separation.py`
    (33) — a kitchen-sink design with 2+ of every construct across two
    hierarchies, asserting by COUNTING blocks per section rather than
    matching a snippet, which is what catches both a MISSING separator
    (two instances read as one block) and a DOUBLED one. Plus the
    inverse: paired commands share a block, a continuation line never
    runs into a blank, a scope wrapper hugs its own instances, and no
    two blank lines ever appear in a row anywhere in either format.
    Verified by reverting the helper: 23 of 33 fail without it. One
    named assertion added to `test_generated_upf_golden.py` so a red
    build says which property broke, and the golden regenerated — its
    diff is exactly this change and nothing else.

  **CRITICAL: "Add Retention" opened nothing at all, through either
  entry point** (reported against a real saved project, `../sram.pis`;
  reproduced there before any code was touched).
  - **ROOT CAUSE, traced rather than guessed: a `KeyError` raised INSIDE
    `RetentionStrategyDialog.__init__`.** Qt swallows an exception at
    the signal/slot boundary — the action fires, the traceback goes to a
    console nobody is reading, and the user sees nothing happen. Both
    entry points funnel through one method, which is why both were dead.
  - **The contradiction**: `_prompt_add_retention()` passed
    `supply_sets_for_owners(..., exclude_domain_id=domain.id)` and the
    dialog then INDEXED that dict by the domain's own primary/secondary
    ids. Those two disagree by design — that helper answers "which sets
    may this domain CLAIM", so it excludes any set already claimed by
    ANOTHER domain. **Ancestor Supply Set sharing (CHANGE 2) makes them
    disagree routinely**: `sram.pis` has PD_SUB referencing PD_TOP's own
    primary SS_VDD as its secondary, so PD_TOP's own primary was
    "claimed elsewhere", absent from the dict, and looking it up killed
    the dialog. NOT a menu-index/separator problem, and not any of the
    recent dialog restructuring — checked, then ruled out by the actual
    traceback.
  - **A latent defect, then made reachable.** `git log -L` on the call
    site: it originally passed `self._domain_state.supply_sets` (every
    set — no KeyError possible) and `f400c45` narrowed it to the
    claim-scoped helper. That created the contradiction; ancestor
    sharing turned it into an everyday crash. **`isolation_dialog.py`
    already documents this exact trap** in its own
    `_refresh_source_supply_combo()` — that this helper's exclusion is
    "specific to Create/Edit Power Domain's own picker semantics" and
    reusing it elsewhere "would silently hide legitimate choices".
    Isolation avoided it; Retention did not.
  - **Fixed in two layers.** New
    `supply_sets_referenced_by_domain(state, domain)`
    (`power_domain_overlay.py`) resolves a domain's OWN sets directly
    out of `state.supply_sets` — used by both Retention call sites (add
    and edit). And `_refresh_supply_set_combos()` now filters its pool
    to ids it can actually resolve, so a genuinely dangling reference
    degrades to a missing picker entry instead of a dead menu item.
  - **The broader gap this exposed, and the durable fix**: every
    Retention test in the repo calls `create_retention_strategy()` or
    constructs the dialog directly. That is the right way to test what a
    feature DOES and structurally cannot notice that the path from a
    click to that code is broken. New
    `tests/frontend/test_diagram_entry_point_smoke.py` (21) tests the
    SEAM instead: every right-click action and every tool-palette mode,
    invoked for real, must reach its handler without raising — plus
    "every mode has a button, and that button selects that mode"
    (`DiagramToolPalette.action_for_mode()`, new, reading the mode off
    the action rather than a second hand-maintained mapping). Its
    fixture deliberately builds the shared-Supply-Set shape, since a
    fixture with one unshared set reproduces nothing — which is exactly
    why every existing suite passed. Verified by reverting the fix: the
    three Retention tests fail.
  - **Item 2, the "no sequential elements" warning: ALREADY IMPLEMENTED,
    confirmed rather than assumed** — `properties_panel.py`'s own
    `_hierarchy_has_no_sequential_elements()`, an italic "(No Sequential
    Elements)" suffix on the Properties row, non-blocking, matching the
    "No Cell Type Assigned" convention the request named. Verified live
    against `sram.pis` (subtree-aware: `sram` reports False because its
    child `u_sub` has one). **Worth knowing, and new**: the dialog
    requires at least one element, so a target with no sequential
    elements anywhere in its domain cannot be accepted at all — the
    warning is only reachable through the UI for a MULTI-HIERARCHY
    domain, where the invoked block has none and a sibling member
    supplies the elements. Every pre-existing test of this warning
    bypasses the dialog; the two new end-to-end ones go through the real
    action and the real dialog, positive and negative.

  **REFINED: the declaration sections pack, and a blank line there
  marks a SCOPE CHANGE** (explicit follow-up; refines, does not retract,
  the rule above). In "# Supply Ports", "# Supply Nets", "# Connect
  external ports to internal nets" and "# Macro Connections",
  declarations sharing a scope run together with no separation; a single
  blank line appears only before a `set_scope <hierarchy>` block and
  after its closing `set_scope .` when more follows.
  - **One judgment call, flagged rather than taken silently**: the
    request's TITLE named "macro port connections" while its list of
    section headings named the boundary-connection section instead, and
    its "leave everything else alone" list did not mention Macro
    Connections either way. Included it — that is the reading that makes
    the title true, and packing one `connect_supply_net` section while
    separating the identically-shaped other would look like a bug.
  - **CPF's structural counterparts follow**: the `create_power_nets`/
    `create_ground_nets` declaration sections and CPF's own Macro
    Connections. CPF emits these at one scope, so none of them ever
    carries a transition to mark.
  - **Scoped deliberately**: Supply Sets is also scope-grouped and is
    NOT on this list — it keeps one blank line per instance, as the
    earlier message established. Everything else (Isolation, Level
    Shifter, Retention, Power Switches with their paired mapping line,
    Power Domains, Load Upf, the power-state sections) is unchanged.
  - **Tests**: 6 added to
    `tests/frontend/test_generation_blank_line_separation.py` (37 total)
    — the packed sections asserted to contain no blank line at all, the
    Supply Nets shape asserted LINE FOR LINE against the specified
    example, two nets sharing one nested scope asserted to pack (the
    property the refinement is actually about, which a one-net scope
    group cannot show), the CPF counterparts, and one test asserting
    every OTHER section still separates its instances — because the real
    risk in a change like this is applying it too widely, which no
    individual section's own test would catch. The golden was
    regenerated; its diff is exactly the removed blanks inside those
    four sections, with the scope-transition blanks intact.

  **Retention generation reformatted to the multi-line command shape**
  (explicit request; formatting only, no data-model or validation
  change).
  - **The two commands STAY SEPARATE.** Real UPF treats `set_retention`
    (domain/supply/elements) and `set_retention_control` (save/restore
    behaviour) as genuinely distinct commands; an earlier proposal to
    fold the signals onto the first was dropped as wrong. Pinned by its
    own test, which also asserts no `-save_signal`/`-restore_signal`
    ever appears inside the `set_retention` command.
  - Both commands now render via `_render_multiline_command()` +
    `_flag_line()` — backslash-continued, one flag per line — and the
    element list via `_braced_list_flag()` rather than
    `_multiline_braced_list()`, so the brace group's own lines get
    continued correctly by the same mechanism every other multi-line
    command here already uses. Previously they were compact one-liners
    whose only line break was the element list, which read as a
    different construct from every other strategy in the file.
  - **CPF's `create_state_retention_rule` got the same treatment** — the
    structurally analogous construct (a named rule, a domain, an element
    list), and this now matches that format's own
    `create_isolation_rule` block. Its elements stay ROOT-relative,
    unchanged: CPF has no `set_scope` wrapper around this rule.
  - **Tests**: 5 added to
    `tests/frontend/test_power_intent_text_export.py`, plus 5 existing
    assertions updated. The spec's own worked example (RET_CPU / PD_SUB
    / SS_VDD_SUB / SS_VDD / `o_q`) is pinned as ONE literal block rather
    than as fragments — the defects this guards are shapes (a stray
    unmatched brace, a backslash in the wrong place, an indent
    disagreeing with the line above), and every one of those survives a
    set of `assert "-domain PD_SUB" in text` checks. Plus: the shape
    holds for EVERY strategy in a file, for multi-hierarchy replication,
    and for a multi-element list; braces balance across the whole
    generated file; and no continuation line ever runs into a blank.

  **Retention dialog: "Save/Restore Logic" -> "Save/Restore Signal"**
  (explicit request, label only). Renamed in the editable dialog, in its
  own validation messages (the same field, named the same way -- text
  only, every check and its ordering untouched), and in the read-only
  `RetentionStrategyInfoDialog`, a sibling in the same module showing
  the same two fields: leaving that one would have labelled one strategy
  two different ways depending on which way it was opened. The dataclass
  fields stay `save_logic`/`restore_logic` -- renaming those would reach
  into placements, `.pis` project files and generation, which this is
  not. **NOT renamed, reported instead** (different surfaces, outside
  the stated scope): the diagram's hover tooltip
  (`hover_tooltip.py`) and the duplicate-merge confirmation in
  `power_intent_diagram_view.py` both still say "Logic". **Tests**: 2 in
  `tests/frontend/test_power_intent_retention.py` (both dialogs' labels,
  and that the rename is label-only -- the properties still read, the
  field is still required, and the message names it the new way), plus
  the existing label-order assertion updated.
  **Follow-up**: the Retention Properties table gained "Save Signal" and
  "Restore Signal" columns, between the two supplies and Elements,
  matching the dialog's own field order. **Unlike "Primary Supply"**,
  which stays blank for a real row because the backend schema genuinely
  has no such field, these two ARE real fields on the backend's own
  `RetentionStrategy` (`save_signal`/`restore_signal` -- which is also
  where the rename's wording comes from), so a real row fills them in
  rather than being blanked. Elements moved from column 5 to 7; its
  index is now the named `_RETENTION_ELEMENTS_COLUMN`, following the
  precedent `_SUPPLY_PORT_CONNECTED_COLUMN`/`_SUPPLY_PORT_STATES_COLUMN`
  already set after an insertion silently shifted them once before --
  the two tests reading the old literal now read the constant. 4 tests
  added (header order, a diagram-only row, a real backend row, and a row
  with no signals showing blanks rather than the string "None").

  **CPF generation corrected against the real Si2 specification** (audit:
  `docs/cpf2_reverification_audit.html`; the spec PDFs are the official
  Si2 CPF 2.1 and CPF 1.0). This changed almost every line of CPF the
  generator emits, because the writer had only ever been checked against
  CPF 1.0 and against this project's own `golden.cpf` — which turned out
  to carry some of the same mistakes.
  - **C1 — `connect_supply_net` is a UPF command.** Zero occurrences in
    the CPF 1.0 spec, zero in 2.1, absent from CPF's own command index.
    The whole "Macro Connections" section was written in the wrong
    language. Now `create_global_connection -net <net> -pins {...}`
    (2.1, p. 129), with a matching backend handler normalising onto the
    same `DomainSupplyConnection` records UPF's own form produces.
  - **C2 — nothing was inside a design.** Now wrapped in
    `set_design <top>` … `end_design` (2.1, p. 201).
  - **C3 — `-isolation_power_net`/`-isolation_ground_net` are not CPF in
    ANY version.** Zero occurrences in 1.0 and in 2.1. They came from
    this project's own `golden.cpf`, and the backend parser had been
    extended to accept them, encoding the same mistake. CPF states the
    isolation cell's supply as a DOMAIN: `create_isolation_rule
    -secondary_domain` (2.1, p. 134), emitted only when the isolation
    Supply Set is genuinely some domain's own primary — otherwise
    reported, never guessed.
  - **C4 — `-location self` is a CPF 2.1 ADDITION** (2.1, p. 9; 1.0
    allows only `{from|to}`), and the file declared 2.0. **CPF 2.1 is
    now a supported export target**; at 2.0 the flag is withheld and the
    omission reported. Same gate on `update_level_shifter_rules`, which
    the 2.1 change log lists alongside it.
  - **H1/H2** — a root domain now carries `-default` (it previously
    selected nothing at all) and a real `-exclude_instances`. **H2 is a
    genuine "resolved by 2.0"**: the flag does not exist in 1.0, so the
    old comment was correct when it was written.
  - **H3 — `-acknowledge_receiver_1`, not `-acknowledge_receiver`.** The
    unnumbered spelling is 1.0's, where it also took a PIN rather than
    an expression. The elaborator still READS both — reading tolerantly
    and writing conservatively stay separate decisions.
  - **H4 — the switch's control reached CPF only as a comment.** Now
    `create_power_domain -shutoff_condition` plus
    `update_power_switch_rule -enable_condition_1`, which is what CPF's
    own worked example does (2.1, p. 174).
  - **H5 — `-internal` is a MUST** for a switched internal supply (2.1,
    p. 171), and the section was already labelled "Internal Supplies"
    without emitting it.
  - **M1 + a finding the audit itself missed** — `set_instance .` named
    an instance literally called `.`, since `.` is CPF's DEFAULT
    hierarchy separator (2.1, p. 44); it is gone, scope being structural
    now. And the writer emits `/`-separated paths while never declaring
    `set_hierarchy_separator /`, so a conformant reader parsed
    `u_sram/VDD` as one flat name. Both fixed; the second was found only
    by reading the hand-written `golden_cpf20.cpf`, which sets it on
    line 12.
  - **A real ordering bug in this pass's own first cut**, caught before
    commit: `-secondary_domain` was appended to `create_flags` *after*
    the command had already been rendered, so it silently never
    appeared.
  - **Backend**: `set_hierarchy_separator` and `create_global_connection`
    registered (neither existed), plus `-exclude_instances`,
    `-secondary_domain`, `-acknowledge_receiver_1/_2`, `-gate_bias_net`.
  - **Tests**: 9 new in `tests/frontend/test_power_intent_text_export.py`,
    one per finding, naming it. ~20 existing CPF assertions updated from
    the old syntax — each is a place the suite had been pinning output
    that was wrong. The CPF round trip passes through the real
    elaborator with the corrected syntax.

  **Create Project always available; RTL filelist (`.f`) support; a
  manual DEFINE: field** (explicit request, three changes).
  - **"Create Project" is no longer one-shot.** It stays enabled while a
    project is open, and creating over an active one runs the EXACT flow
    "Load Project" already established -- Yes saves then creates, Yes
    then a CANCELLED save aborts the whole Create, No discards, Cancel
    aborts. Sharing that flow rather than writing a second one is what
    keeps them from drifting. This REVERSES the previous deliberate
    "matches this window's never-go-back navigation" rule, and the test
    asserting `isEnabled() is False` was updated to say so.
  - **`frontend/src/hierarchy_view/filelist.py`** -- filelist parsing as
    pure functions over text and paths, no Qt. Syntax **verified, not
    remembered**: against the Icarus Verilog command-file format and
    `dalance/verilog-filelist-parser`, whose own test cases pin the
    exact shapes -- `+define+A=1+B+C=x+` (several per line, `+`-
    separated, trailing `+` optional, value split at the FIRST `=` so a
    value may contain one), `+incdir+d1+d2+`, `-f`/`-F`/`-file`, `//`
    and `#` comments, `$VAR`/`${VAR}`/`$(VAR)` substitution. An
    unrecognised `-`/`+` option is RECORDED and skipped, never mistaken
    for a source file and never fatal -- a real filelist carries
    tool-specific switches this app has no use for.
  - **A real vendor divergence, flagged rather than hidden**: in VCS,
    `-F` resolves paths against the filelist's own directory and `-f`
    against the directory the tool was INVOKED from. This app is a GUI
    with no meaningful invocation directory, so both are treated as
    `-F`. Absolute paths are unaffected.
  - **Cycle detection on the RESOLVED path**, so `./a.f` and `a.f` are
    one file: a self- or mutually-referencing filelist is a
    `FilelistError` naming the cycle, and a DIAMOND (the same filelist
    reached down two branches) is legal, not a false positive.
  - **"Load Filelist (.f)..." is a third button in the Design Files
    browse row** (confirmed placement), feeding the SAME staging list,
    so "Add" -> Loaded works unchanged. A broken filelist reports and
    stages NOTHING -- partial staging would leave the tab in a state the
    user never asked for.
  - **"DEFINE:" is a plain comma-separated text field, directly above
    "Top Module"** in the "Loaded" pane -- moved there on a follow-up
    request from an initial position across the top of the tab. It now
    sits with the other two things Elaborate reads, in the order it
    reads them: what to define, then what to elaborate. (Confirmed
    widget shape -- a deliberate exception to this
    app's usual add/remove-row list). `CPU, CORE=16, SRAM32x8`, trimmed,
    split at the first `=`. A bare define and an empty-valued one stay
    distinguishable (`CPU` vs `CPU=`).
  - **PRECEDENCE, confirmed: the manual field wins**, and the two
    sources COMBINE rather than replace. A genuine conflict emits a
    non-blocking `DEFINE-OVERRIDE` WARNING naming both values -- never
    silent, never fatal, since overriding a filelist from the field is
    an everyday action.
  - **Defines genuinely reach elaboration**, threaded
    `LoadDesignTab` -> `ElaborateAction` -> `backend_integration` ->
    `backend.api.elaborate_rtl` -> the Verilog elaborator. **pyslang 11
    exposes no constructible `PreprocessorOptions`** (verified against
    the installed build -- `Bag.preprocessorOptions` is a slot with no
    type behind it), so defines are delivered as a generated header
    parsed in the SAME compilation unit via `SyntaxTree.fromFiles`. The
    per-file `fromFile` loop makes one unit EACH, which is why a define
    in a separate file does not reach the others -- confirmed
    empirically, the wrong `ifdef` branch was taken. **With no defines
    the original per-file loop runs unchanged**, so every existing
    design elaborates exactly as before. Verilog/SV only: VHDL has no
    preprocessor, and the arguments are ignored there rather than faked.
  - **`_call_elaborate_action()`** passes the two new arguments only to
    a callable whose signature has room, so every pre-existing
    3-argument action keeps working. Deliberately NOT a
    `try/except TypeError`, which would also swallow a genuine
    TypeError raised INSIDE the action and silently retry -- presenting
    as "Elaborate did nothing" with the real error gone. A test pins
    that.
  - **Tests**: `tests/frontend/test_filelist_and_defines.py` (40) --
    the four filelist cases the request named, plus the comma-field
    parsing and the precedence rule. Four reach the REAL backend and say
    why: proving a define CHANGES which `ifdef` branch compiles cannot
    be done against a mock, which would only agree with itself. The
    relative-path test moves the cwd elsewhere and puts a DECOY file of
    the same name there, so resolving the wrong way succeeds with the
    wrong file rather than failing loudly. 6 added to
    `test_main_window.py` for Create Project. Verified by reverting:
    breaking filelist-relative resolution fails 2, removing the cycle
    guard fails with `RecursionError`.

  **UPF generation aligned to a hand-fixed reference golden —
  2026-10-04.** The `single_clock_design` project (`main_top.pis`,
  vendored with the reference `main_top.fixed.upf` under
  `tests/frontend/golden_upf_output/single_clock_design/`) was exported
  and its UPF hand-fixed by the user into the reference form.
  Regenerating from the same project now reproduces that file exactly
  (`tests/frontend/test_reference_upf_golden.py`, 20 tests). **This
  reference outranks every earlier UPF golden here, which came from
  reviews and spec reading.** Do not describe it, in any doc, commit
  message or comment, as vendor- or EDA-tool-validated (explicit user
  instruction).
  - **FLAT, ROOT-SCOPED — REVERSES review findings 1.4 and 1.5.** Every
    UPF construct is declared at the root; a nested domain states its
    hierarchy as root-relative `-elements`; strategies' elements are
    root-relative paths; every reference is a bare name (one helper,
    `_upf_reference()`, replacing `_supply_set_reference()`/
    `_qualified_supply_set_name()`/`_scope_relative()`/
    `_construct_scope()`/`_scope_group()`). One `create_supply_net` per
    distinct name — sound because same-named placements are, by this
    app's own rule, one continuous net, so the `-reuse` replicas had
    nothing to say. Supply Set/Power Domain names were already unique
    design-wide, so nothing needed mangling. See the "FLAT, ROOT-SCOPED"
    note in `power_intent_text_export.py`.
  - **Replicated power switch: ONE root statement — SUPERSEDES an
    earlier explicit request** that each replica be generated
    separately in its own `set_scope`. At the root that would declare
    one name N times, and the replicas share a domain and a same-named
    output net. **CONFIRMED by the user (2026-10-04)**: a single power
    switch rule covers every block belonging to the same power domain.
  - **Retention**: `-retention_supply_set` only (no `-supply {primary
    …}` — the domain states it), a blank line between `set_retention`
    and `set_retention_control` (REVERSES the "one block" rule), and
    `{signal high|low}` save/restore pairs via `_retention_signal()`
    (`enable`→`enable high`, `!enable`/`~enable`→`enable low`; a
    compound expression passes verbatim rather than being guessed at).
  - **Formatting**: `create_supply_set` one `-function` per continued
    line; `-available_supplies { SS }` spaced; legacy `add_pst_state`
    names prefixed `PST_STATE_PREFIX` — **CONFIRMED by the user**: needed
    so a PST state never clashes with the `FULL_ON` keyword (export-only — not stripped on
    import, since legacy files do not re-import and stripping would only
    rename hand-written files' states).
  - **Multi-bit elements: `<path>[*]`** (follow-up golden revision): a
    multi-bit Retention/Isolation/Level Shifter element is written
    `u_data_subsystem/u_counter/count[*]`. An element key always names
    the WHOLE signal (one per port, one per flop output), so every bit
    is selected and `[*]` always applies; widths come from the new
    optional `generate_upf(rtl_design=...)` (`_signal_widths()`/
    `_upf_element()`), which `MainWindow` passes. Scalars, unknown
    widths and an already bit-selected key get no suffix. CPF unchanged.
    The importer drops `[*]`, and -- a REAL latent bug found here --
    no longer keeps a hierarchical element (`u_sub/o_q`) without its
    top-instance prefix; the round trip now compares FULL element paths
    (it compared only the last segment, which is what hid that bug).
  - **`set_isolation -source/-sink`** (follow-up golden revision): the
    dialog's optional Source/Sink Supply Set are emitted right after the
    head line, each only when set -- reversing the old "no verified
    example, intentionally NOT emitted" stance. Read back into
    `source/sink_supply_set_id` only when the name IS a supply set (CPF's
    `-from/-to` domain stays the domain link); `supported_features`
    moves both UNMODELLED -> MODELLED.
  - **Isolation "Applies To All: Inputs | Outputs | Both | Elements"**
    (explicit request), below Isolation Sense -- the same choice and
    labels as Level Shifter. New `IsolationStrategyPlacement.applies_to`,
    default `"elements"` so older projects/strategies are unchanged; the
    dialog starts on Elements and greys out the Elements section
    otherwise (`elements` returns [] then). UPF: a broad choice writes
    IEEE 1801 `-applies_to <dir>` and NO `-elements` -- **the user
    confirmed `-applies_to` over the literal `-applies_to_all` first
    asked for, which is not an 1801 option**. Badges: a broad strategy
    has no elements to derive blocks from, so it attaches to every
    boundary block of its domain (`isolation_attachment_owner_paths()`/
    `isolation_variants_on_block()`, mirroring Level Shifter's fix for
    the identical "no badge" bug). Duplicate detection, Info dialog and
    a new Properties "Applies To All" column (Elements moves to
    `_ISOLATION_ELEMENTS_COLUMN` = 10) follow. Import: no elements + a
    direction -> the broad choice; with elements, `-applies_to` still
    narrows the list as before. CPF: unchanged -- a rule with no
    `-pins` already covers the whole domain crossing; the direction
    itself has no CPF form. Tests:
    `tests/frontend/test_isolation_source_sink_applies_to.py`, plus
    round-trip coverage of both.
  - **NOT a generator bug, recorded so it is not re-investigated**: the
    golden's `-isolation_signal iso_n` (export said `iso_en`) was a DATA
    fix — the vendored `.pis` already carries `iso_n`.
  - **Importer, to keep the round trip lossless with no scope in the
    file**: a root-declared Supply Set takes the owner paths of the
    (shallowest) domain it is PRIMARY of; a root-declared Internal Supply
    takes the hierarchy of the switch domain driving it or the supply
    set using it as a rail (`_flat_supply_set_owner_paths()`/
    `_flat_internal_supply_owner_path()`); a retention strategy with no
    primary takes its domain's; `{signal high|low}` is reversed
    (`_retention_logic()`); and a ROOT-scoped strategy's badge attaches
    to its domain's block instead of piling onto the top
    (`_strategy_owner_path()`). A nested `set_scope` in a file still wins.
  - **Tests updated rather than deleted**, each pinning the new shape
    with its rationale: `test_power_intent_text_export.py`,
    `test_generation_blank_line_separation.py` (Supply Nets joins the
    packed sections), `test_power_states_model.py`,
    `test_power_intent_round_trip.py`, `test_generated_upf_golden.py`
    (golden regenerated — its diff is exactly this change).

  **Interface signals missing from every I/O picker except Port List
  (bug, found on `aura_top`, 2026-10-04).** The Isolation and Level
  Shifter element pickers (`isolation_overlay.collect_isolation_elements_
  for_domain()`), their Direction column (`port_direction_display()`),
  the badge orientation (`local_direction_composition()`), the Port
  Attribute dialog and the export's width lookup all listed a block's I/O
  from `Definition.ports` alone -- but a SystemVerilog interface port
  lives in `Definition.interface_ports`, so on an interface-heavy design
  most I/O vanished (`aura_top/u_cpu`: 4 offered vs 34 in Port List).
  Port List alone expanded them, in a private helper (with a second copy
  of the interface lookup in `connectivity_flatten.py`). Fixed by moving
  that expansion to ONE shared helper, `shared/models/rtl_io.py`
  (`module_io_ports()` -> plain ports + one `Port` per interface signal,
  `<iface>.<signal>`, `[i]`-indexed when arrayed, direction from the
  bound/inferred modport) and pointing every I/O list at it: Port List
  now uses it too; `InstanceBoxItem.io_ports` is computed by the scene
  builder (resolving an interface needs the Design, which a box does not
  hold) and read via `isolation_overlay.box_io_ports()`; the Port
  Attribute dialog, `MainWindow`'s `top_level_ports`, the importer's
  port-attribute keys and `-applies_to` direction lookup (which also now
  strips only a TRAILING bit-select, not the `[0]` inside
  `mst_port[0].awvalid`), and `_signal_widths()` (so a multi-bit
  interface signal gets `[*]`). Verified on `aura_top.pis` through the
  real dialogs: every domain's picker now equals Port List, all
  directions filled. `connectivity_flatten.py`'s own copy of the lookup
  is left as is (it is the connectivity tracer, not an I/O list).
  Tests: `tests/frontend/test_interface_io_in_dialogs.py` (7; 6 fail
  without the fix).

  **`SWITCH-OFF` -> `SWITCH_OFF` in UPF (bug, found on `aura_top`).**
  The switch-contributed OFF state is named `SWITCH_OFF_STATE_NAME`
  ("SWITCH-OFF") on screen, and the legacy writer copied state names
  verbatim, so `add_supply_state`/`add_pst_state` carried a `-`, which
  is not valid in a UPF state name. Fixed at the writer only
  (`_upf_state_name()` / `UPF_SWITCH_OFF_STATE_NAME` in
  `power_intent_text_export.py`, also used by the older
  `_upf_state_entry()`); the on-screen name is deliberately unchanged.
  Modern UPF and CPF never contained it (verified byte-identical on
  aura_top). Test: `test_power_states_model.py::test_the_switch_added_off_state_is_written_switch_underscore_off_in_upf`.

  **`if`/`case` generate blocks missing from instance names (bug, found
  on `aura_top`).** `_walk_body_members()` (backend Verilog elaborator)
  prefixed a `generate for` entry's content (`g_bank[1].u_bank`) but
  recursed into a `GenerateBlockSymbol` (`if`/`case`) with the PARENT's
  prefix, so `sram_1rw_wrap`'s `if (...) begin : g_1024x32 ... u_macro`
  elaborated as `u_wrap/u_macro` instead of `u_wrap/g_1024x32.u_macro`
  -- the IEEE 1800 27.6 hierarchical name every EDA tool and UPF uses
  (`u_mem/g_bank[1].u_bank/u_wrap/g_1024x32.u_macro/VDD`). Fixed by
  prefixing with `member.externalName` + "." -- the label, or the
  implicit `genblk<N>` for an unnamed block, matching pyslang's own
  `hierarchicalPath` -- for instances, nets and sequential elements
  alike; `genvar_values` is now passed down too (it was dropped for an
  `if` nested in a `for`). One test pinned the old bare `u_leaf` and was
  updated; `test_every_kind_of_generate_block_is_a_level_of_the_instance_name`
  covers named/unnamed `if`, `case`, and `if`-in-`for`. **Not changed,
  noted**: (1) `.pis` projects store the ELABORATED design, so one saved
  before this fix keeps the old names until re-elaborated; (2)
  `_collect_interface_instance_identities()` still names an interface
  INSTANCE declared inside a generate block without the block prefix --
  a pre-existing gap shared with `for` generates, not hit by aura_top;
  (3) VHDL generates come from GHDL/Yosys naming and could not be
  checked on this server (neither tool installed).

  **Hidden hierarchies saved with the project (explicit request).** A
  `.pis` used to drop the Hierarchy Tree's Hide state, so every hidden
  block came back on Load Project. `ProjectData.hidden_paths` (the
  tree's explicitly hidden paths, sorted) is a new OPTIONAL section --
  same backward-compatible pattern as `macro_power_intent_state`, no
  `_FORMAT_VERSION` bump: an older file loads with nothing hidden.
  `HierarchyTreeWidget.hidden_paths()` feeds Save;
  `restore_hidden_paths()` is applied LAST in `PowerIntentTab.load_project()`
  (the tree's `visibilityChanged` then rebuilds the diagram without
  them), is deliberately NOT pushed onto the undo stack (Ctrl+Z must not
  unhide what the file says is hidden), and drops a path the loaded
  design no longer has. A Load Power Intent re-import still starts with
  nothing hidden, unchanged. Tests: three in
  `tests/frontend/test_main_window.py` (real Save -> fresh window Load,
  backward compatibility, stale path).

  **`set_level_shifter -applies_to elements` (bug, user report).** The
  Level Shifter writer emitted `-applies_to <value>` for ANY Applies To
  All value, so the dialog's "Elements" choice leaked out as
  `-applies_to elements` -- not valid IEEE 1801 (`inputs|outputs|both`
  only). Now gated on `_UPF_APPLIES_TO_DIRECTIONS`; "Elements" is
  expressed by `-elements` alone, as Isolation already did. Import:
  a level shifter with elements and no `-applies_to` reads back as
  "elements" (it would otherwise come back as None). Tests: a
  parametrized generation test in `test_power_intent_text_export.py`
  and a round trip in `test_power_intent_round_trip.py`.

  **Unbraced Tcl-special values made generated UPF unreadable (bug,
  found preparing v1.1).** `set_isolation -isolation_signal iso_en_w[2]`
  was written bare; Tcl -- every UPF reader, this app's elaborator and
  any EDA tool -- treats `[2]` as command substitution and rejects the
  file (`invalid command name "2"`). The bundled `examples/aura_top` UPF
  could not be loaded back at all. `_tcl_word()` braces a user-typed
  value only when it contains a Tcl-special character (`[ ] $ \ { } " ;`
  or whitespace), so plain values -- and the reference golden's
  `-isolation_signal iso_n` -- are unchanged. Applied to the isolation
  signal, a bare `-control_port`, and the UPF/CPF `-name_prefix`/`-prefix`
  values (CPF conditions were already braced). Tests: two in
  `test_power_intent_round_trip.py` (braced + reads back; plain stays bare).

  **Release 1.1.0 preparation (2026-10-06).** `_TOOL_VERSION` = "1.1"
  (written into every generated file's header); the two whole-file UPF
  golden tests now blank the header's Generator version like the
  timestamp, and `test_the_header_carries_the_current_app_version` pins
  it instead -- so the reference golden file itself is never edited for a
  version bump. User-facing docs are rebuilt around `examples/aura_top`
  (README quickstart/screenshots, new `docs/example_aura_top.md`,
  screenshots in `docs/images/aura_top/`, captured off-screen from the
  real MainWindow with the fixed light appearance); the dated engineering
  history in the milestone docs and this file is deliberately left
  referring to the examples it was written against. `.gitignore` now
  un-ignores `examples/**/*.pis` so example projects ship. The bundled
  aura_top UPFs were regenerated from its `.pis` (only the header and the
  braced isolation signals changed), after fixing a `LS_PMU_CTRL_` name
  typo in the `.pis` that disagreed with the committed UPF.

  **UPF -> design mapping fixed on aura_top; CPF mapping removed
  (2026-10-06, explicit request).** Mapping `examples/aura_top`'s own
  generated UPF back onto its RTL + LIB must reproduce `aura_top.pis`:
  the legacy file now does, 100% with zero diagnostics; the modern file
  differs only by what its format cannot carry (asserted exactly).
  Unrecognised UPF commands/options are skipped whole and reported as a
  `UPF-SYNTAX-NOT-RECOGNIZED` WARNING quoting the command and naming its
  line. Load Power Intent is UPF only; a `.cpf` is refused up front (CPF
  generation unchanged). Full writeup at the end of
  `docs/load_power_intent_mapping.md`; tests
  `tests/frontend/test_upf_mapping_aura_top.py` and
  `test_upf_unrecognized_syntax.py`.

### Final Milestone — Back-End / Front-End Integration

The only milestone where the two tracks are connected at their own,
fully-designed integration layer. Its own deeper design work — progress
reporting, power-intent format auto-detection, mixed-language RTL
support composed with `.lib` resolution, structured UPF/CPF/mapping
errors (RTL elaboration already has these, see below), and whatever
`backend/api/`'s real, considered shape turns out to be — is still not
done. Treat `backend/api/`'s current shape as disposable, not a starting
point to preserve, when that design work happens.

**The app's own executable entry point has already been consolidated
onto the real backend, ahead of this milestone's own deeper design —
see `docs/backend_integration.md` for the full writeup.** What used to
be a separate, minimal, explicitly-scoped first-pass prototype
(`power_intent_studio_validation_harness.py`, distinct from the normal,
mock-only `power_intent_studio.py`) has been retired as a separate
script: `power_intent_studio.py` is now the single entry point, and its
`main()` (`frontend/src/app/main.py::build_main_window()`) wires the
real backend by default via `frontend/src/backend_integration/`
(renamed from `validation_harness/` to match — it's the app's real,
standard wiring now, not a prototype's). `MainWindow`'s own bare
constructor still defaults every action to mock/fixture data, unchanged
— that's still what the rest of the front-end test suite is built and
tested against, per the milestone-independence rule above; only the
app's own executable entry point stopped using that default.

This consolidation did **not** resolve this milestone's own remaining
scope cuts, which are unchanged from the original prototype: no progress
reporting, no format auto-detection, no mixed-language RTL, and a
pragmatic subset-only `shared/models/` adapter are still explicit,
approved scope cuts, not decisions carried forward as this milestone's
final design. Errors are no longer uniformly coarse: RTL elaboration
failures now carry real `.file`/`.line` (a real bug, found via personal
use, fixed at the source — see Milestone 1a's status above) — UPF/CPF
and mapping/validation errors remain message-only, still a deliberate
scope cut, not fixed alongside RTL. End-to-end tests already exist at
the entry-point-integration level
(`tests/frontend/test_real_backend_integration.py`); "end-to-end tests
across the full stack," in the fuller sense this milestone's own design
will eventually need, are still introduced only there for now.

---

## Repo layout

```
backend/
  elaborator/
    rtl/
      verilog/    # Verilog & SystemVerilog front-end adapters
      vhdl/       # VHDL front-end adapter
      ir/         # unified hierarchy/netlist IR + builder (Milestone 1c)
    power_intent/ # UPF + CPF parser/elaborator (Milestone 2), mirrors rtl/'s split
      upf/        # UPF front-end (2a)
      cpf/        # CPF front-end (2b, not started)
      ir/         # unified power-intent IR + builder (Milestone 2c)
      shared/     # Tcl-runner + accumulate-then-freeze infra, shared by upf/ and cpf/
    lib/          # Liberty (.lib) macro handling (Milestone 1e)
  mapping/        # UPF-RTL mapping engine (Milestone 3)
  modification/   # power intent modification logic (Milestone 3b) -- in-memory
                  # PowerIntentDesign -> PowerIntentDesign only, nothing written
                  # to disk (see docs/product_workflow.md)
  export/         # EMPTY (unstarted). UPF/CPF serialization / write-back -- its own future,
                  # milestone-adjacent piece with its own research pass, NOT
                  # part of Milestone 3b (corrected; see
                  # docs/product_workflow.md's Export section)
  llm_integration/# optional LLM-assisted UPF editing (Milestone 4) -- EMPTY (unstarted)
  validation/     # consistency checks, diagnostics (cross-cutting)
  api/            # THE ONLY seam frontend is allowed to depend on -- the app's real,
                  # standard entry point now uses this by default (docs/backend_integration.md),
                  # but its own shape is still NOT the fully-designed Final Milestone
                  # shape -- see that section above

frontend/
  src/
    backend_integration/ # the ONE place frontend/ imports backend/ (backend.api only) --
                         # real-backend wiring for the app's actual entry point
                         # (power_intent_studio.py); see docs/backend_integration.md.
                         # Formerly validation_harness/, when it wired a separate
                         # prototype script instead of the app's own default. Not
                         # part of Milestones 1-3's own mock-data-only scope.
    hierarchy_view/     # Front-End Milestone 1
    power_intent_view/  # Front-End Milestone 2
    diff_view/          # EMPTY (unstarted) -- supports Milestone 2/3
    chatbox/            # EMPTY (unstarted). Front-End Milestone 3 — UI for LLM-driven UPF edits
                         # (mocked responses until Final Milestone; pairs with
                         # backend/llm_integration/ from Back-End Milestone 4)
    shared/

tests/
  backend/
    golden_rtl/   # golden Verilog/SV/VHDL fixtures
    golden_upf/   # golden UPF fixtures
  frontend/       # tests against mock/fixture data, not the real backend

docs/
scripts/
.github/
```

## Notes for whoever (human or Claude Code) picks this up

- If you're not sure which milestone a task belongs to, ask before starting —
  don't guess and let it drift into "integration work in disguise."
- Golden test fixtures (`tests/backend/golden_rtl`, `tests/backend/golden_upf`)
  are the regression safety net for the multi-HDL / multi-UPF-version
  complexity. Add to them as edge cases are discovered.
- UPF has multiple standard versions (1.0 / 2.0 / 2.1 / 3.0 / 3.1). Confirm
  which version(s) are in scope before deep-diving into Milestone 2 parsing
  details — this should be decided explicitly, not assumed.
- **Run `scripts/check_doc_drift.sh` at the start of a session** (or before
  ending one). The practice of updating this file/`docs/` in the same pass
  as the code change lapsed silently for ~22 days/21 commits before a
  dedicated 2026-08-31 audit caught and reconciled it. It only warns
  (never blocks); if it fires, skim the commits it prints and fold
  anything doc-worthy into `CLAUDE.md`/`docs/` before moving on, the same
  way every earlier entry in this file was written alongside its own code.

  **The lapse then recurred anyway, and the script did not catch it** —
  the 2026-09-11 codebase health audit found nine of the fifteen commits
  since the last doc sweep had touched no documentation (including a whole
  new supply type, Save/Load Project, Extract Diagram, and a Cell-Type
  revert that left this file describing classes the code no longer had),
  while the check reported OK. Root cause: it compared the LATEST
  doc-touching commit's timestamp against the LATEST code-touching one, so
  **any single doc-touching commit reset it**. It has been rewritten to
  count code commits that touched no docs since the last doc sweep —
  measuring coverage, not recency. Verified against the historical range:
  the new form reports 7 undocumented commits where the old one reported
  OK.
