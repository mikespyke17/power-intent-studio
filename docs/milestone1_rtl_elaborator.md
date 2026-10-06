# Milestone 1 — RTL Elaborator: Engineering Spec

Status: scoped, not yet implemented. This doc is the detailed companion to the
summary in `CLAUDE.md`. Update it as design decisions are refined during
implementation — it should stay accurate, not just aspirational.

## Goal

Given a set of RTL sources (Verilog, SystemVerilog, and/or VHDL, possibly
mixed within one hierarchy) plus optional `.lib` black-box macro definitions,
produce a single language-agnostic **IR** (`backend/elaborator/rtl/ir/`) that
downstream milestones (UPF mapping, modification, front-end visualization)
can consume without ever needing to know which HDL a given block was written
in.

## In scope for Milestone 1

| Item | Scope |
|---|---|
| Verilog / SystemVerilog parsing & elaboration | Full: includes, macros, parameters, generate blocks |
| VHDL parsing & elaboration | Full: generics, generate statements, entity/architecture pairs |
| Mixed-language hierarchies | Resolve instantiation across language boundaries within the IR |
| `.lib` macro support | **Black-box only** — pin/interface info for hard IP with no RTL source |
| Module/entity I/O extraction | Direction, width, type, for every level of hierarchy |
| Connectivity | **Hierarchical is the primary representation.** Instance-to-instance port connections at each level. Full flattening is a **derived, on-demand** operation, not a separate elaboration pass. |
| Internal logic | **Flops and latches only**, identified from `always`/`process` blocks. Purpose: support retention-cell insertion in a later milestone (3b). |

## Explicitly out of scope for Milestone 1

- **FSM extraction.** Recognizing state machines from state-variable patterns
  and encoding style is a heuristic, non-deterministic problem — fundamentally
  different in kind from parsing/elaboration. If a real need for FSM awareness
  shows up later, it gets scoped as its own milestone with its own design
  discussion, not folded into "internal logic."
- **Standard-cell / synthesis-grade Liberty parsing.** No timing arcs, no
  power tables, no cell-mapping logic. `.lib` support here is strictly for
  giving black-box macros (SRAMs, analog IP, hard IP with no RTL) a
  pin/interface description.
- **Full flattened netlist as the native representation.** Flattening exists,
  but only as an on-demand query/derivation over the hierarchical IR — it is
  not what gets stored or what other milestones build against by default.

## Why these boundaries

- UPF references hierarchical instance paths (not flattened nets), so a
  hierarchical-primary IR is both cheaper to build and a better match for
  Milestone 3 (UPF-RTL mapping).
- Retention strategies need to know which regs are flops; isolation,
  power-domain, and level-shifter strategies operate at the port/hierarchy
  level and don't need any internal-logic awareness at all. Scoping "internal
  logic" down to flops/latches covers the actual downstream need without
  taking on FSM-extraction complexity that nothing yet requires.
- Hard macros (SRAMs, analog blocks, etc.) commonly ship with no RTL source at
  all — `.lib` is the only way the elaborator will ever see their interface.
  Treating `.lib` support as strictly black-box-macro scoped keeps this a
  small, well-bounded parser instead of a synthesis-grade Liberty reader.

## Suggested build order (within Milestone 1)

1. **1c — Unified IR schema** first, even before the parsers are wired up.
   Design the IR (hierarchy nodes, ports, instances, connections, sequential
   elements) against a couple of hand-written example designs so 1a/1b have a
   concrete target to normalize into. **Done.**
2. **1a — Verilog/SystemVerilog front-end** into the IR. **Done** (pyslang).
3. **1f/1g — I/O extraction + hierarchical connectivity** on top of 1a, tested
   end-to-end on Verilog/SV-only designs before VHDL is introduced. **Done**
   (hierarchical connectivity; on-demand flattening itself is still step 8).
4. **1b — VHDL front-end** into the same IR. **Done** (GHDL + Yosys).
5. **1d — Mixed-language instantiation resolution** once both front-ends
   independently produce valid IR. **Done**, single-direction (leaf)
   references only — an alternating Verilog↔VHDL chain is a tracked gap,
   not silently handled.
6. **1e — `.lib` black-box macro integration.** **Done** (`liberty-parser`).
7. **1h — Flop/latch identification.** **Done** — flops (both front-ends,
   async-reset, resetless, sync-reset) and basic (enable-only) latches; see
   the 1h section below for what's still explicitly deferred.
8. **On-demand flattening** as a derived operation, once the hierarchical IR
   is stable. Not started.

## Open decisions / tracked gaps (as of 1a)

Keep this section current — add an entry the moment a scope question comes up
that isn't resolved immediately, and update/remove it once resolved. The goal
is that anyone (human or Claude Code) picking this doc up mid-stream can see
what's settled vs. still open without having to reconstruct it from chat
history.

- **Parametrized RTL — RESOLVED.** `tests/backend/golden_rtl/verilog_parametrized/`
  (two instances of the same module, `WIDTH=8` and `WIDTH=16`) plus
  `test_parametrized_instances_produce_distinct_definitions` confirm the real
  `elaborate()` — not just the synthetic `make_definition_id` unit test —
  produces two distinct `Definition`s with different `definition_id`s,
  correctly-scaled port widths, and no incorrect dedup (3 `Definition`s total
  for 3 module bodies, not collapsed to 1).

- **SystemVerilog interfaces (`interface`/`modport`) — RESOLVED: implemented.**
  Originally deferred (see the superseded reasoning kept below this entry for
  history) until a real, complex personal SoC design (four interfaces —
  `axi_lite_if`, `apb_if`, `sram_if`, `pmu_if` — used pervasively as the
  *primary* connectivity mechanism across nearly every subsystem) made the
  deferral a hard blocker for that design's own "Load Design," not a
  hypothetical. Implemented after a real pyslang spike against that actual
  design (not assumed from docs): `InterfacePortSymbol.interfaceDef`/`.modport`
  give an interface port's type and modport constraint; `InstanceSymbol.body`
  for an interface instance elaborates into a real member list (ports, nets,
  `ModportSymbol` children) the same way a module body does, distinguished via
  `body.definition.definitionKind == DefinitionKind.Interface`; and a bound
  interface's *connection* is resolved via `PortConnection.ifaceConn` — a
  `(instance_symbol, modport_symbol)` pair — never via the plain-signal
  `.expression.getSymbolReference()` path, which interface-typed connections
  don't populate.
  New schema (`backend/elaborator/rtl/ir/schema.py`): `Definition.is_interface`
  (alongside the existing `is_black_box`, same pattern — an interface is "just
  another kind of Definition," not a parallel type), `Definition.modports`
  (`list[Modport]`, each a name + `list[ModportPort(name, direction)]` —
  reuses `PortDirection` rather than a new enum), and `Definition.interface_ports`
  (`list[InterfacePort(name, interface_name, modport)]`, deliberately *not* a
  `definition_id` — see `InterfacePort`'s own docstring: the concrete,
  parameter-resolved interface instance actually bound is only known
  per-instantiation via the owning `Instance.connections`, same division of
  labor `Port`/`PortConnection` already have for plain signals). `PortConnection`
  itself needed no schema change — `connected_to` (already a plain string)
  accommodates an interface-array-element connection like `"axi_slv[1]"` the
  same way it names any other endpoint.
  Verified end-to-end against the real triggering design once past a
  pre-existing, unrelated syntax bug in that design's own file (a variable
  named `dist`, an SV reserved keyword) — full elaboration success, 51
  `Definition`s, 74 hierarchy nodes, all four interfaces correctly recognized.
  Regression-tested with dedicated fixtures:
  `tests/backend/golden_rtl/verilog_interface/` (renamed from
  `verilog_interface_unsupported/` now that the behavior it exercises is
  supported, not rejected — `iface.sv`: a modport-constrained interface port
  and its connection resolve correctly; `dangling_iface.sv`: an unused
  interface instance elaborates to a real, if unreferenced, Definition,
  not an error) via `test_interface_instance_elaborates_as_an_is_interface_definition`,
  `test_interface_typed_port_and_its_connection_resolve_correctly`,
  `test_dangling_interface_instance_elaborates_to_an_unused_but_real_definition`
  in `test_verilog_elaborate.py`. **VHDL is explicitly untouched** — VHDL has
  no interface-equivalent construct, so this is genuinely Verilog/SV-only,
  not a cross-language asymmetry needing its own tracked-gap entry the way
  1d's alternating-chain restriction does.

  **Two more real gaps surfaced by the same real-design push, fixed alongside:**
  - **Arrayed instances (`some_if bus [N] (...)`, and equally an arrayed
    *module* instance) were silently invisible in the IR, not even an error.**
    pyslang reports these as one `InstanceArraySymbol` wrapping N real (but
    empty-`.name`) `InstanceSymbol` elements — a different type the existing
    `isinstance(member, InstanceSymbol)` body-member check never matched, so
    they were dropped with no diagnostic at all. Not interface-specific (the
    same gap silently drops any arrayed module instance, none of which this
    codebase's fixtures happened to exercise before), but discovered via the
    real design's own arrayed interfaces (`axi_mst[N_AXI_MST]`,
    `axi_slv[N_AXI_SLV]`, `pmu_ch[N_DOMAIN]`). Fixed by expanding each element
    into its own `Instance`, named positionally (`"buses[0]"`, `"buses[1]"`,
    ...) since pyslang's own per-element `.name` is empty — the same naming
    a bound-to-one-array-element `PortConnection.connected_to` uses (via
    `.arrayName`/`.arrayPath`), confirmed to actually match.
    `tests/backend/golden_rtl/verilog_interface_arrays/array_iface.sv` +
    `test_arrayed_interface_instance_expands_to_one_instance_per_element`,
    `test_arrayed_plain_module_instance_also_expands_correctly`,
    `test_interface_port_bound_to_one_array_element_names_the_connection_by_index`.
  - **A typedef'd port/net type crashed with a bare `AttributeError`.**
    `word_pkg::byte_t sig;` (a package `typedef logic [7:0] byte_t;`) hit
    `'TypeAliasType' object has no attribute 'range'` in both `_build_port`
    and `_build_net` — `.isPackedArray` is true on the alias type, but only
    `.canonicalType` (resolving through the alias to the real underlying
    type) actually has `.range`. Pre-existing, not introduced by interface
    work — just never reached before, since the interface guard used to raise
    before elaboration recursed deep enough into the real design to hit a
    typedef'd internal signal. Fixed with a shared `_bit_range(t)` helper
    resolving `.canonicalType` first, used by both `_build_port`/`_build_net`.
    `tests/backend/golden_rtl/verilog_typedef_signal/typedef_signal.sv` +
    `test_typedefd_port_and_net_resolve_bit_range_through_the_alias`.

- **SystemVerilog packages (`import pkg::*;`, `pkg::NAME`) — RESOLVED: support
  now, no code changes needed.** Confirmed against pyslang's actual elaborated
  symbol model, both through raw AST inspection and through the real
  `elaborate()` end-to-end: a module using `import pkg::*;` and unqualified
  `WIDTH`, and a separate module using explicit `pkg::WIDTH` with no import at
  all, both resolve port widths/ranges correctly with zero diagnostics —
  package-constant resolution goes through the *same* type-resolution path as
  local parameters, entirely inside pyslang's elaboration. No new code was
  needed in `elaborate.py`. Also confirmed package constants correctly do
  **not** appear in `InstanceBodySymbol.parameters` (and therefore not in
  `Definition.parameters`) — which is the semantically correct behavior, not a
  gap: a package constant is one global value shared by every instantiation,
  never a per-instance override, so it must not affect `definition_id`
  uniqueness the way a real parameter binding does.
  Regression-tested in `tests/backend/golden_rtl/verilog_packages/`
  (`pkg.sv` + `sub.sv` using `import pkg::*;` + `top.sv` using `pkg::WIDTH`
  directly) via `test_package_scoped_parameters_resolve_correctly`.

- **Multiple always blocks per module — reconfirmed as a known, tracked gap
  before closing 1a.** Not a blocker for 1f/1g (which only needs I/O
  extraction + connectivity, not sequential-element completeness), but
  explicitly flagged so 1a/1h aren't later assumed "fully" done without
  revisiting this. Current `_extract_sequential_element` recognizes at most
  one if/else inside one edge-triggered always block per module; a module
  with e.g. two separate always blocks driving two different registers would
  only have the first one recognized (or possibly neither, depending on
  iteration order — not yet characterized). Revisit as part of 1h.

- **1f/1g — I/O extraction + hierarchical connectivity — RESOLVED for
  Verilog/SV.** Port extraction (1f) was already exercised across every prior
  fixture; three gaps specific to connectivity (1g) hadn't been tested yet and
  are now covered:
  - **Depth beyond 2 levels.** `verilog_three_level_hierarchy/` (top → mid →
    leaf) confirms `Design.walk()` and per-level port/connectivity extraction
    hold up recursively, not just for the one two-level shape every earlier
    fixture happened to share.
  - **Net-mediated connectivity.** Every prior fixture wired instance ports
    straight through to a parent port by the same name — never through a
    genuine internal signal. `Definition.nets` was defined in the 1c schema
    but never populated by `elaborate.py` until now. Added net extraction
    (`NetSymbol`/`VariableSymbol` members not already covered by a port) and
    `verilog_internal_net_fanout/` (two instances wired together through an
    internal `wire`, not any parent port) to prove both the extraction and
    the resulting connectivity resolve correctly. That first fixture only
    had 2 endpoints (1 driver, 1 load) though — same category of risk as the
    sensitivity-list bug, where the simplest fixture shape happened to hide a
    binary assumption. Added `verilog_net_fanout_multi/` (1 driver, 3 loads,
    4 endpoints total on one net) to check this empirically rather than by
    reasoning about the code. Nothing in the extraction path aggregates
    connections per net at all — each `Instance` independently records its
    own endpoint — so there's structurally no way a "net has exactly one
    driver and one load" assumption could exist yet, but this is now proven,
    not just argued, and guards against it being introduced later if/when
    per-net aggregation (e.g. for the deferred flattening operation) is added.
  - **Shared-Definition connection independence.** `test_ir_schema.py` already
    proved this with hand-built IR; `verilog_shared_definition_independent_connections/`
    proves it through the real elaborator: two instances of the same
    (unparametrized) module dedupe to one `Definition` object
    (`def_a is def_b`), while each `Instance.connections` stays wired to its
    own distinct external signals and the two connection lists are neither
    equal nor the same object.
  **On-demand flattening — RESOLVED, built 2026-08-14 onward (documentation
  gap closed 2026-08-31).** Built later than step 8's own originally
  envisioned position in the build order, and under real design-correctness
  pressure (found via a real personal SoC design, the same `aura` design
  already cited elsewhere in this doc) rather than as a clean, planned final
  step — this status entry itself went un-updated for roughly 2.5 weeks
  after the work landed, which is exactly the kind of drift this doc exists
  to prevent; recorded here now from the real commit history and current
  code, not written alongside the work.

  `backend/elaborator/rtl/ir/flatten.py` (1011 lines) is the real,
  end-to-end signal tracer this section previously described as deferred:
  `trace_driver()` walks a signal backward to its true origin, `trace_receivers()`
  walks forward to every true sink, through combinational pass-through
  chains (`ContinuousAssign.referenced_signals`, a new schema field), across
  arbitrary hierarchy depth, through SV interfaces/modports (own
  `_trace_driver_interface_member()`/`_trace_receivers_interface_member()`
  pair), and through sequential elements (`SequentialElement.data_ref` —
  clock/reset/set/data refs resolve as terminal receivers, not dead ends).
  `flatten_design()`/`net_group()` complete the public surface. Its own
  module docstring states plainly that it IS the 1g step, built now rather
  than in this doc's own originally envisioned order.

  27 dedicated backend tests exercise it directly against the real IR, no
  UI/mock involvement: `tests/backend/test_flatten.py` (19 tests — depth,
  multi-driver, combinational loops, macros, sequential elements) and
  `tests/backend/test_flatten_interface.py` (8 tests). A run of real-design
  bugs was found and fixed along the way, each with its own regression
  fixture under `tests/backend/golden_rtl/`: a flop's own D input wasn't
  recognized as a real receiver (`SequentialElement.data_ref` had the
  driver/receiver sense reversed); a flop wasn't recognized as driving its
  own output on the driver side; parent-level real-logic detection was
  missing entirely until `ContinuousAssign.referenced_signals` was added; a
  dotted interface-member signal's own local continuous-assign driver was
  checked before its interface-member ascension, misordering which one won
  (found 2026-08-16); a locally-declared interface instance had no identity
  to qualify member-connectivity queries against; a forwarding interface
  port two hierarchy levels from its real target was mishandled — fixed by
  re-qualifying at the same scope rather than fabricating a bogus child
  path; a constant-indexed vector reference (`clk_dom[4]`) failed to merge
  with the whole-vector `clk_dom` net-group membership it structurally
  belongs to.

  **A real, load-bearing gap this status update needs to be honest about**:
  none of the above is currently reachable from the product surface.
  `backend/api/__init__.py` — the ONLY sanctioned frontend seam per this
  project's own milestone-independence rule — re-exports exactly 4
  functions (`elaborate_rtl`, `elaborate_power_intent`, `run_mapping`,
  `run_validation`); `flatten_design()`/`trace_driver()`/`trace_receivers()`
  are not among them, confirmed by reading the file directly, and nothing
  under `frontend/` imports `backend.elaborator.rtl.ir.flatten` at all. The
  real backend engine described above is correct and fully tested, but
  dead code from the running application's own point of view — including
  through the validation harness (`docs/backend_integration.md`) against a
  real elaborated design.

  What the GUI actually runs on instead is
  `frontend/src/power_intent_view/connectivity_flatten.py` (755 lines) — a
  hand-duplicated, structurally identical copy of the same algorithm,
  operating on the frontend's own independent mirrored IR
  (`frontend/src/shared/models/rtl.Design`) rather than the real backend
  `Design`. This is the correct "duplicate, not import" shape per this
  project's own milestone-independence rule (confirmed: it imports nothing
  from `backend/`), and it is itself real, tested production code — 5
  dedicated test files under `tests/frontend/test_connectivity_flatten*.py`
  — not a toy or a stub. But it is a second, hand-kept-in-sync
  implementation of the identical tracer, not a consumer of the first one:
  every one of the real-design bug fixes listed above was ported into this
  frontend copy by hand, separately, in the same commit. This dual-engine
  situation — a correct, tested backend capability with no wiring to expose
  it, and a parallel frontend reimplementation actually serving the product
  — is a real architectural fact worth resolving deliberately (either by
  exposing `flatten_design()` through `backend/api/` once the validation
  harness needs it against real per-signal connectivity, or by explicitly
  deciding the frontend copy stays authoritative until the Final Milestone)
  rather than left as an accident of which half of the work happened to get
  built first.

  **Five real `generate`-block connectivity bugs found and fixed, 2026-09-01
  (SystemVerilog `generate` validation pass)** — a dedicated validation
  pass ran real, hand-authored SV `generate` examples (module-instance
  replication, generate-based selection, black-box macro replication,
  nested generate loops) through the real `elaborate_rtl()`/`flatten.py`
  pipeline and found five distinct, real bugs, all sharing one underlying
  pattern: a `generate for` loop driving a per-iteration bit/element-slice
  of a wider signal lost that slice's own identity somewhere between
  extraction and connectivity resolution. Fixed in
  `backend/elaborator/rtl/verilog/elaborate.py`/`backend/elaborator/rtl/ir/flatten.py`,
  27 tests confirmed unaffected, 10 new regression tests added
  (`tests/backend/test_verilog_generate_indexed_connectivity.py`, paired
  with `tests/backend/golden_rtl/verilog_generate_indexed_connectivity/`
  and `tests/backend/golden_lib/gen_macro.lib`):
  1. An indexed OUTPUT port connection from an ordinary instance
     (`.q(data_o[bit_idx])`) collapsed to the bare parent signal name —
     pyslang wraps an output connection's own expression in an
     `AssignmentExpression` that `_resolve_connected_name()` never
     unwrapped before walking its ElementSelect layers.
  2. A constant-indexed driver query never matched a WHOLE-vector
     continuous-assign target (`assign d_next = ...;`) — `flatten.py`'s
     own driver search did an exact string match with no "also try the
     whole-vector base name" fallback, the same shape `net_group()`
     already handled for connections but continuous-assign targets never
     got; a matched whole-vector assign that's itself a true pass-through
     needed the query's own index suffix re-attached to `.source` before
     recursing, the same "keep asking the same specific-bit question all
     the way down" requirement `net_group()`'s own docstring establishes.
  3. A net (and its own feeding continuous assign) declared LOCALLY
     inside a `generate for` iteration was extracted under its own bare
     name with no per-iteration prefix at all, unlike an instance's own
     name — every iteration's own genuinely distinct local net collapsed
     into identically-named IR entries, and `flatten.py`'s own exact-match
     driver search then silently resolved every iteration's own query to
     the SAME accidentally-identical target: a confidently wrong answer,
     not a visible gap.
  4. ANY indexed or range-sliced connection to a black-box (.lib) macro
     instance was dropped entirely (`connected_to=None`), unconditionally
     — `_extract_uninstantiated_connections()`'s own raw-syntax-text
     extraction only ever recognized a bare identifier. Now also resolves
     a literal-integer element-select, a range-select with literal bounds
     (collapsing to the bare base name, matching the ordinary-instance
     convention), and — the hard case — a genvar-element-select, via a
     newly-threaded `genvar_values` mapping built from each live
     `generate for` entry's own `ParameterSymbol` members (a genvar's own
     per-iteration resolved constant; not recoverable from raw syntax
     text alone, since the text only ever carries the genvar's NAME).
  5. A `generate for` loop directly containing an `always_ff` with NO
     submodule instantiation at all lost per-iteration bit identity in
     `SequentialElement.output_signal` the same way (1) did — a second,
     independent implementation of the same ElementSelect-auto-unwrap
     gap in `_collect_assignment_targets()`, fixed by reusing
     `_resolve_connected_name()` there instead of a raw
     `getSymbolReference()` call.

  **Known, deliberately out-of-scope-for-this-pass consequence**: fix #2
  above (`flatten.py`'s driver search) has a structurally identical,
  currently UNFIXED counterpart in `frontend/src/power_intent_view/connectivity_flatten.py`
  (confirmed by reading it — same `ca.target == signal` exact-match, no
  whole-vector fallback) — the two-engine situation described earlier in
  this section means this fix has NOT been ported there yet. Flagged
  here explicitly rather than silently left to drift further, matching
  this doc's own "if the doc and the code drift, note it" discipline —
  porting it is a separate, deliberate decision, not bundled into this
  backend-only validation/fix pass.



## Language construct scope: "synthesizable structural RTL subset," not "full language compliance"

The target for this milestone is **full support of the constructs that
produce hierarchy, ports, connectivity, and sequential elements** — not full
IEEE 1800 / IEEE 1076 compliance. Verification-only constructs (classes,
randomization/constraints, assertions, covergroups, DPI-C, UVM-adjacent
features) have no bearing on power intent and are explicitly **out of scope
regardless of how complete elaboration support for them might be** — treating
them as in-scope risks turning this milestone into an open-ended
verification-language compiler project instead of a bounded structural
elaborator.

### SystemVerilog / Verilog

| Construct | Status |
|---|---|
| Modules, ports, local parameters | Done (1a) |
| Parameterized instances (dedup via `definition_id`) | Done (1a) |
| Generate blocks | Elaborated automatically by pyslang's full-elaboration model — no separate handling needed, but not yet explicitly fixture-tested |
| Packages (`import pkg::*;`, `pkg::NAME` parameters/types) | **Resolved — supported, no code changes needed.** Confirmed against real pyslang elaboration: package constants resolve through the same type-resolution path as local parameters, and correctly don't leak into `Definition.parameters`. See entry above. |
| Multiple always blocks per module, different signals | **Not a gap — verified during the 1h review pass, not just assumed.** Two separate always blocks in one module, each driving a different register, are both correctly identified (each `ProceduralBlockSymbol` is walked independently). Previously untested; a dedicated always-block-per-signal check confirmed this directly. |
| Multiple always blocks jointly driving the *same* signal | **Known, tracked gap.** A genuinely different (and much rarer) shape than the row above — e.g. one block doing the synchronous update, another handling some other aspect of the same register. Deferred: see the 1h tracked-gaps entry below for why and what would trigger revisiting it. |
| Interfaces / modports | **Done — implemented, Verilog/SV only.** See the "RESOLVED: implemented" entry above. `Definition.is_interface`/`.modports`/`.interface_ports`; arrayed interface *and* module instances now elaborate too (a related, previously-silent gap fixed alongside). VHDL has no equivalent construct. |
| Classes, randomization, assertions, covergroups, DPI-C, UVM | **Explicitly out of scope, not a gap.** Irrelevant to hierarchy/connectivity/power-intent. Do not chase elaboration support for these. |

### VHDL

| Construct | Status |
|---|---|
| Entities/architectures, generics, generic maps | **Done (1b).** Direct entity instantiation, multi-level hierarchy, and generics (incl. two differently-parametrized instances deduping to distinct `Definition`s, and generics left at their default with no explicit generic map override) all fixture-tested against the real GHDL+Yosys pipeline. |
| Packages / package bodies (constants, types) | **Done (1b), no code changes needed.** `use work.pkg.all;` + an unqualified package constant sizing a port resolves through GHDL's own elaboration — same finding as SV packages. Fixture-tested. |
| Generate statements | **Spot-checked, works, not yet a permanent fixture.** A VHDL `for ... generate` loop instantiating 3 copies of the same entity correctly deduped to one `Definition` with 3 `Instance`s (Yosys/GHDL name them `gen_bits[0].u_leaf2` etc.). Verified interactively, not committed as a golden fixture — same "known-working, not yet regression-guarded" status Verilog's generate-block support has. |
| Processes — flop/latch identification | **Flops done (async-reset, resetless, and sync-reset — the last one fixed during a dedicated 1h review pass after an audit found it was silently misclassified, not just missing). Latches: basic (enable-only) support done; latch-with-reset is a further, tracked gap.** See the dedicated 1h entry below for the full audit and both fixes. |
| Multiple processes per architecture | **Not a gap — verified, not just assumed.** Unlike SV's "multiple always blocks" row above, this isn't a limitation here: extraction works over Yosys's already-elaborated cell list, not one `process`'s AST shape at a time, so a second process driving a different register is picked up automatically. Fixture-tested (`vhdl_multiple_processes/`). |
| Protected types, VHDL-2008 PSL assertions, files/access types | **Explicitly out of scope** — verification/simulation-oriented, not structural |

Action items:
1. ~~Research pyslang's package symbol model~~ — **done**, see the SV packages
   entry in "Open decisions / tracked gaps" above. (VHDL package support
   itself is still unimplemented — 1b hasn't started — this item was only
   about the SV research.)
2. When 1b begins, confirm this VHDL scope table (or amend it) *before*
   writing the parser integration, the same way the pyslang choice was
   reviewed before 1a's implementation started.

### VHDL front-end selection — spike findings (pre-1b)

Unlike pyslang for SV, no VHDL front-end offered a clean "pip install and get
an elaborated Python model" story. Four candidates researched:
`pyGHDL.dom`/`pyVHDLModel` (mature, pip-installable, but explicitly
**syntax-only** — no generic resolution, no cross-unit symbol resolution —
ruled out immediately, since using it would mean hand-rolling the exact
elaboration engine the milestone doc says to avoid building from scratch);
`pyGHDL.libghdl` (GHDL's low-level ctypes bindings, elaboration support
documented upstream as work-in-progress); GHDL `--synth` /
ghdl-yosys-plugin (more production-tested synthesis kernel, but both
GHDL's own docs and the plugin's README open with "this is experimental and
work in progress," and the output format itself is still unsettled
upstream); NVC (simulation/JIT-focused, no Python bindings or structured
introspection output).

**Spike performed:** installed Docker (via Homebrew `colima` + `docker` CLI,
not Docker Desktop, since Docker Desktop's setup needs GUI interaction not
available in this environment; the GHDL Homebrew *cask* was ruled out up
front — deprecated, disabled on macOS Gatekeeper 2026-09-01). Ran
`ghdl/ghdl:6.0.0-mcode-ubuntu-24.04`, installed the matching v6.0.0
`pyGHDL` source (the PyPI `pyghdl` package is stale — `1.0.dev0` from
2020 — current pyGHDL only ships inside the GHDL repo) plus `pyTooling`,
and hand-tested against a real VHDL file: an `entity counter` with a
`WIDTH` generic, instantiated twice via direct entity instantiation with
different generic map values (`WIDTH => 8`, `WIDTH => 16`).

**Result — evaluated against the three criteria set before the spike:**

1. *Resolve generics into concrete values?* **Partial.** Walking
   `Get_Generic_Map_Aspect_Chain` → `Get_Actual` → `Get_Value` did correctly
   return `8` and `16` for the two instances. But this only works because
   the actual happened to be a plain integer-literal node
   (`Iir_Kind` value 8). There is no evidence of a general constant-folding
   entry point exposed to Python — a generic map actual referencing another
   constant, a package value, or an arithmetic expression would need to be
   evaluated by hand-walking further AST nodes, not by calling one function
   the way `ParameterSymbol.value` just worked in pyslang regardless of
   source-expression complexity.
2. *Enumerate hierarchy through the ctypes API "reasonably directly"?*
   **Marginal.** `Get_Concurrent_Statement_Chain` does surface instantiation
   statements, but only within one architecture body at a time — there is no
   `topInstances`-equivalent that hands back an elaborated, already-descended
   design tree. Building a real hierarchy walk means writing the
   entity/architecture-binding resolution and recursive descent myself
   (and this test used direct entity instantiation specifically because
   it's simpler than component instantiation + separate configuration
   specifications, which real VHDL commonly uses and which wasn't tested at
   all).
3. *Stable/documented enough to build 1b on, vs. working around internals?*
   **No.** Every step past the initial file load required discovering
   accessor names via `dir()` introspection on live objects rather than
   documentation or examples — there is exactly one relevant usage example
   in GHDL's own test suite (`testsuite/pyunit/libghdl/Initialize.py`), and
   it only demonstrates single-file semantic analysis (entity/architecture
   name checks), not hierarchy or generics at all. `canon.py`, the module
   whose name most suggested elaboration-adjacent processing, turned out to
   expose only three boolean flags, no callable pass.

**Decision: pyGHDL fails the bar.** Per the pre-agreed fallback, next step is
evaluating the GHDL + Yosys (ghdl-yosys-plugin) pipeline — a separate,
scoped spike, not started yet. pyVHDLModel stays off the table regardless
of that outcome, as agreed before this spike started.

### GHDL + Yosys (ghdl-yosys-plugin) spike — functional result is strong; distribution result is not

**Setup:** downloaded the OSS CAD Suite `linux-x64` prebuilt bundle (`ghdl`,
`yosys`, and the `ghdl-yosys-plugin` all together — fastest path to a working
setup, avoids fighting a from-source plugin build) into the same Docker
container from the pyGHDL spike (`--platform linux/amd64`, since GHDL support
in this bundle is x64/darwin-arm64 only — see distribution finding below).
Test file: same shape as the pyGHDL spike — `entity counter` with a `WIDTH`
generic, instantiated twice (`WIDTH => 8`, `WIDTH => 16`) inside `entity top`.
Command: `yosys -m ghdl -p 'ghdl --std=08 top; write_json'` — deliberately
*not* running any `synth_*` pass, since those trigger flattening/tech-mapping;
this only reads the design into Yosys's RTLIL and dumps it, which is the
right point to check whether hierarchy survives at all.

**Result — evaluated against the same three functional criteria:**

1. *Resolve generics into concrete values?* **Yes, cleanly.** GHDL's
   synthesis frontend uniquifies parameterized instances by generic value
   itself, before the JSON is ever written — the two instances elaborate to
   two separate modules named `counter_Brtl_8` and `counter_Brtl_16`, each
   with correctly-scaled port widths (8 and 16) baked directly into the
   output. No hand-walking of AST nodes, no manual constant-folding — this is
   consuming an already-elaborated result, the same relationship pyslang had
   to `ParameterSymbol.value`.
2. *Enumerate hierarchy "reasonably directly"?* **Yes.** The JSON's
   top-level `modules` dict has one entry per elaborated module
   (`top`, `counter_Brtl_8`, `counter_Brtl_16`); `top`'s `cells` dict lists
   `u_counter_a`/`u_counter_b` with a `type` field naming the submodule and a
   `connections` map per port. No `synth_*`/flatten pass means this stays
   hierarchical, not a flat gate soup. This maps far more directly onto
   `Definition`/`Instance` than raw IIR node-chain walking did.
3. *Stable/documented enough to build on?* **Yes.** Yosys's JSON netlist
   format is a real, publicly documented, stable schema used broadly for
   interop across the open-silicon tool ecosystem — not something
   reverse-engineered via `dir()`. The two failures hit while getting the
   test file to analyze (a VHDL library-clause scoping mistake identical to
   the one caught in the SV/VHDL fixture work weeks ago, and a latch-inference
   error from a self-referential `q <= q` in the toy RTL) were both genuine
   VHDL-correctness bugs in the test file, not tooling confusion.

**Bonus finding, beyond what was asked:** the JSON cells aren't just wires —
GHDL's synthesis frontend already infers proper flop cells (`$adff` here,
Yosys's async-reset-D-flip-flop primitive, with `CLK`/`ARST`/`D`/`Q` ports).
If this path is chosen, sequential-element identification for VHDL (the
`processes` analog of 1h) may come substantially free from Yosys's generic
cell types instead of requiring hand-parsed `process` statement structure
the way SV's `always` blocks did. Not evaluated in depth — flagging it
because it changes the shape of future 1h-for-VHDL work, not because it was
part of this spike's scope.

**Criterion 4 — end-user distribution — is the actual blocker, and it's bad:**

- **OSS CAD Suite, the most complete prebuilt bundle, explicitly ships GHDL
  and the GHDL plugin for only two of five published platforms:
  `linux-x64` and `darwin-arm64`.** No Windows. No `linux-arm64`. No
  `darwin-x64` (Intel Mac). This is stated outright in the bundle's own
  README, not an inferred gap — `GHDL VHDL 2008/93/87 simulator (linux-x64
  and darwin-arm64 platforms only)`.
- **No pip-installable, cross-platform WebAssembly path exists for GHDL at
  all**, unlike Yosys itself. YoWASP — the project that does exactly this for
  Yosys and nextpnr (pip install, runs anywhere via WASM, no native toolchain
  needed) — is real and actively maintained (moved from GitHub to Codeberg,
  commits as recent as the day before this spike), but **there is no `ghdl`
  package in the YoWASP org at all**, on either host. Confirmed by listing
  every repo in the org, not by absence of a search hit.
  `yowasp-yosys` alone does not add VHDL support without a GHDL counterpart.
- **Where GHDL does exist prebuilt, it's large.** The OSS CAD Suite bundles
  that do include it run 500–740MB per platform (compressed) — GHDL and the
  plugin are a small fraction of that, but there's no slimmer official
  GHDL-only prebuilt bundle; the alternative is building GHDL from source
  per target platform ourselves, which is its own significant CI/maintenance
  commitment.
- **Windows has a separate, thinner path, not the same one.** GHDL and Yosys
  are each independently packaged for Windows via MSYS2 (`pacman -S
  mingw-w64-x86_64-ghdl`, confirmed live and updated the day before this
  spike), but this is a *third*, differently-shaped distribution channel
  from OSS CAD Suite (end users install MSYS2 and `pacman`-install packages
  themselves — not something we could silently bundle into a PyInstaller
  build) — and a quick check did not turn up a prepackaged
  `ghdl-yosys-plugin` bridge under an obvious name on MSYS2, meaning even
  this path may require building the plugin ourselves for Windows.
- **Licensing flag, not a legal conclusion:** GHDL is GPL-2.0-or-later.
  Bundling a GPL binary into a distributed tool (versus invoking a
  separately-installed one) raises real questions about how that interacts
  with the rest of the tool's distribution terms — worth actual legal review
  before committing to a bundling strategy, not something to wave away or
  resolve by assumption here.

**Net: three functional criteria pass clearly — better than pyGHDL by a wide
margin — but there is no platform-uniform path to shipping this to end users
today.** Any real ship strategy right now would mean either (a) three
different per-platform stories (OSS CAD Suite for macOS-ARM/Linux-x64,
something else for Windows, something else again for Linux-ARM/macOS-Intel),
each requiring us to bundle and maintain large GPL-licensed native binaries,
or (b) requiring end users to install GHDL+Yosys themselves as a
prerequisite. Neither is "pip install our tool and it works everywhere,"
which was the bar implied by "publicly distributable... not just a working
dev-time proof of concept."

### DECISION: proceed with GHDL+Yosys for 1b; distribution is deferred, not blocking

Given the functional result above, **1b is built on GHDL + Yosys
(ghdl-yosys-plugin), shelling out to `ghdl -a` + `yosys -m ghdl -p '... write_json'`
and parsing the resulting JSON netlist into the IR** — the same relationship
1a has to pyslang, except via subprocess + JSON rather than an in-process
Python AST.

**Explicitly deferred, not resolved, and not blocking 1b implementation
work:**
- Per-platform packaging/bundling strategy (OSS CAD Suite vs. MSYS2 vs.
  building GHDL ourselves per target).
- GPL-2.0-or-later licensing implications of bundling GHDL into a
  distributed tool (needs real legal review, not an engineering decision).
- Installer/dependency-declaration story for end users (today: `ghdl` and
  `yosys` with the ghdl plugin must be on `PATH`, or locatable via an env
  var — exact mechanism TBD when this is picked back up).

This mirrors how `.lib` black-box scope and UPF version selection were
handled earlier — a real product decision explicitly separated from the
technical/engineering track so it doesn't block forward progress, revisited
before this milestone (or the project) is considered ready to actually ship
to end users. Do not let 1b implementation quietly assume any particular
resolution of this (e.g., don't hardcode an OSS-CAD-Suite-specific path) —
`elaborate()` should locate `ghdl`/`yosys` the same simple way (`PATH`,
overridable via env var) regardless of how they got there.

### 1b implementation — RESOLVED for entities/architectures/generics/packages/processes(flops)

`backend/elaborator/rtl/vhdl/elaborate.py`: `ghdl -a` (analyze) then
`yosys -m ghdl -q -p "ghdl --std=08 <top>; write_json"` (elaborate + dump
JSON netlist, deliberately no `synth_*` pass so hierarchy isn't flattened),
parsed into the same IR 1a/1c defined. Requires an explicit `top` parameter
(unlike Verilog's `elaborate(files)`) — nothing in this pipeline
auto-detects it the way pyslang's `topInstances` does, and VHDL analysis
libraries have no inherent "top" concept until elaboration is requested for
one; this is a deliberate, documented API asymmetry between the two
front-ends, not an oversight.

Two real pieces of complexity found by testing against actual GHDL/Yosys
output, not assumed from reading either tool's docs:

- **Yosys's JSON carries no structured metadata recovering a parameterized
  module's original entity name or generic bindings** — `module.attributes`
  is empty on every module tested, and cell `parameters` on an instantiated
  user module are empty too (unlike a `$paramod`-style Verilog pattern).
  The *only* place resolved generics are encoded at all is a name-mangling
  convention (`sub_Brtl_8` for entity `sub`, architecture `rtl`, single
  generic resolved to `8` — positionally, in generic *declaration* order,
  confirmed by testing a two-generic entity: `fifo_Brtl_16_8` for
  `WIDTH=>16, DEPTH=>8`). Recovering a real `{generic_name: value}` dict
  means separately knowing each entity's declared generic *names*, which
  nothing in GHDL/Yosys's output exposes either.
  - **pyGHDL was prototyped for this and works** (`Get_Generic_Chain` on an
    entity gives declared names cleanly; `Get_Formal`/`Get_Actual` on a
    generic map association gives a direct name→value pair, no positional
    guessing needed) — but was **deliberately not used in production code**.
    Reason: it would add a *second* native, version-locked, awkward-to-install
    dependency (pyGHDL's own PyPI package is stale from 2020; the current
    bindings only ship inside the GHDL source tree and must be built/loaded
    against a matching `libghdl` — real friction independently verified
    while getting the pre-1b spike working) on top of GHDL+Yosys, worsening
    an already-bad distribution story for no correctness benefit — the
    *values* still come from GHDL/Yosys's own authoritative synthesis
    output either way.
  - **What's actually used instead**: a narrow, deliberately non-general
    text scan of the VHDL source (`_scan_sources` in `elaborate.py`) for
    `entity ... is` / `generic (...)` / `architecture ... of ... is` —
    lexical identifier extraction against known syntactic anchors, not a
    VHDL parser (no expressions, no types, no semantics). This only runs
    *after* `ghdl -a` has already succeeded, so by construction it only
    ever sees already-validated VHDL — meaningfully de-risking what would
    otherwise be a fragile approach. The mangled Yosys name is then
    reconstructed against every known `(entity, architecture)` pair with a
    strict validation check (segment count must match the entity's declared
    generic count); failure raises rather than guesses.
- **A flop's `ARST` pin is not always wired directly to the named reset
  signal.** For an active-low source condition (`if rst_n = '0'`), GHDL's
  synthesis frontend inserts a `$not` cell between `rst_n` and the `$adff`
  primitive's own ARST pin (which Yosys normalizes to describe with its own
  `ARST_POLARITY` flag). Reading that flag against the *pin* without
  accounting for the inverter gives the reset polarity backwards relative
  to the actual named signal. `_resolve_reset_signal` traces back through
  at most one `$not` cell; anything deeper is left unrecognized (`None`)
  rather than guessed at — same "don't guess on unrecognized shapes"
  principle the SV sequential-element extraction already used. Verified
  with a dedicated test asserting the traced-through result directly
  (`test_vhdl_flop_min_reset_polarity_traced_through_inverter`), not just
  equality with the golden IR, so a regression here fails specifically on
  the tracing logic rather than an opaque diff.

**One-line summary of the connection-order bug**: Yosys's per-cell
`connections` dict doesn't preserve source port-declaration order (came out
alphabetical), while `module.ports` does — fixed by reordering each
instance's connections to match the target `Definition`'s declared port
order, caught by the golden-IR equality test failing before the fix.

**Generics text-scan — explicit boundaries**, documented at the same rigor
as the flop/latch scope above (full detail in the `elaborate.py` module
docstring, summarized here): handles arbitrarily many `entity`/`architecture`
blocks across files, paren-balanced `generic (...)` clauses, and strips
`--`/`/* */` comments before scanning — the last of which was **found as a
real bug, not a hypothetical one**: a comment reading `-- ... "entity
old_foo is" ...` was matched as a genuine declaration before comment-stripping
was added, producing a phantom entity entry. Three distinct Yosys
module-naming shapes were verified empirically (not assumed) and are now
each handled explicitly: the top entity is always bare regardless of
generics; a nested entity with *zero* generics still gets an unconditional
`<entity>_b<arch>` suffix; a nested entity with *any* declared generic
always gets resolved values appended, even when every value is left at its
default with no explicit generic map override at all. That last shape broke
an earlier version of the reconstruction logic — see the
`vhdl_generic_at_default/` fixture and its test, added specifically because
this was caught by a full-suite run, not anticipated in advance. Explicitly
**not** handled (raises `ValueError` rather than guessing): non-integer
generic value types (only ever verified against integers), multiple
architectures for the same entity when more than one candidate validates
against the same mangled name, and generics declared anywhere other than
directly in an `entity` block.

**VHDL equivalent of the "multiple always blocks" gap: verified there isn't
one.** Sequential-element extraction here works over Yosys's
already-elaborated cell list (`$dff`/`$adff` primitives across the whole
module), not by walking one `process` statement's AST shape at a time the
way the SV side walks one `always` block at a time. A second, independent
`process` in the same architecture driving a different register is picked
up automatically — confirmed with a dedicated two-process fixture
(`vhdl_multiple_processes/`), not assumed from the implementation shape.
This is a genuine, structural difference between the two front-ends (see
the updated SV/VHDL comparison table below), not an oversight needing its
own tracked-gap entry.

**Test suite skips gracefully, not hard-fails, without the toolchain.**
Confirmed the pre-fix behavior really did hard-fail (`FileNotFoundError`)
when `ghdl`/`yosys` weren't on `PATH`, exactly as flagged as a risk. Fixed
with `pytestmark = pytest.mark.skipif(shutil.which(...) is None, ...)` in
`test_vhdl_elaborate.py` — verified both directions: 6 skipped without the
toolchain on `PATH`, all pass with it restored.

**Fixtures**: `vhdl_three_level_hierarchy/`, `vhdl_parametrized/`,
`vhdl_flop_no_reset/`, `vhdl_packages/` (all mirroring their Verilog/1a-1g
counterparts), `vhdl_multiple_processes/`, `vhdl_comment_with_entity_keyword/`,
`vhdl_generic_at_default/` (new findings from this review pass), plus
`vhdl_flop_min/` (already existed from 1c, now also the first real
parser-driven VHDL test, not just hand-built golden IR).

### 1d — mixed-language instantiation resolution — RESOLVED

**Design question, resolved before any code:** does mixed-language
resolution reduce to a specialized case of the `.lib` black-box mechanism
(1e)? Data-model yes, orchestration no. A not-yet-fully-elaborated
`Definition`, filled in externally and merged into one `Design`, is the
right representation for both — but a `.lib` blackbox is a leaf by
construction (no RTL source, ever, so the lookup terminates in one step),
while a foreign-language `Definition` can have its own sub-hierarchy that
recurses back into either language at any depth. Neither pyslang nor
GHDL+Yosys supports "pause elaboration, accept an externally-resolved
definition, resume" — both are closed-world, single-invocation tools — so
1d can't be built as a callback hook inside 1a/1b's `elaborate()` the way a
`.lib` lookup naturally could be once 1e exists. Approved design: partition
sources by language, discover unresolved references via each tool's own
diagnostics, resolve whichever side is a leaf first, splice its real
`Definition`s into the side that referenced it, merge. Explicitly scoped to
single-direction (leaf) references only for v1 — an alternating
Verilog→VHDL→Verilog chain is a known, tracked gap, not silently handled.

**Two pre-implementation spikes, both confirmed positively (details below),
plus one unplanned finding that changed the implementation:**

1. **pyslang gives a clean, structured discovery signal.** An unresolved
   `module` instantiation does not abort compilation. `getAllDiagnostics()`
   reports exactly one `DiagCode(UnknownModule)` per unresolved *instance
   site* (not deduplicated by name — verified with two instances of the
   same unknown module plus one of a different module → three distinct
   diagnostics), with the unresolved name directly in `diagnostic.args`
   (`['vhdl_entity']`, no text-parsing needed). Compilation still succeeds
   past the error: `topInstances` resolves normally, and every unresolved
   instantiation shows up in the tree as a dedicated `UninstantiatedDefSymbol`
   carrying `.name` (instance label), `.definitionName` (the unresolved
   module name), and — via `.syntax.connections`, since semantic connection
   binding is `Invalid` with no real port to check against — the full port
   map as raw syntax, walkable for formal-name → connected-signal-name pairs.
2. **GHDL tolerates an unbound VHDL `component` at both analysis and full
   elaboration**, better than the design anticipated. `ghdl -a` on a
   `component verilog_leaf` declared and instantiated with no matching
   entity anywhere succeeds (exit 0) — VHDL's component/configuration model
   genuinely defers binding to elaboration time, as the language spec
   suggests. The real surprise: the full `yosys -m ghdl -p "ghdl top;
   write_json"` pipeline *also* succeeds (exit 0, just a `-Wbinding`
   warning) — GHDL/Yosys auto-synthesizes a real blackbox module entry in
   the JSON, marked with Yosys's own standard `"blackbox": "...1"`
   attribute, with the exact port list (names/directions/widths) taken
   straight from the VHDL `component` declaration. No stub-synthesis is
   needed on this side at all.
3. **Unplanned finding: the existing (pre-1d) `vhdl.elaborate()` crashed on
   this, rather than silently dropping the reference.** Running it
   unmodified against the unbound-component fixture raised a confusing
   `ValueError` from `_reconstruct_base_name_and_parameters`, because that
   function tried to text-scan-reconstruct the blackbox module name against
   known VHDL entities and correctly failed (the name was never scanned —
   it doesn't exist in any VHDL source). Right instinct (fail loud), wrong
   place to catch it — this is what motivated the scoped `vhdl/elaborate.py`
   patch below rather than reusing the function as-is.

**Confirmed during design review, before writing `mixed_language.py`:**
(a) the VHDL-needs-Verilog direction still runs a *real* Verilog elaboration
and replaces the auto-generated blackbox placeholder with it during merge —
the blackbox finding only means stub-*synthesis* is unnecessary, not that
real elaboration is skipped; (b) the transitional 1d placeholder and the
future permanent 1e `.lib` black box are distinct concepts with distinct
lifetimes even though both can currently be *signaled* by the identical raw
`blackbox` JSON attribute — 1d's tracking lives entirely inside
`mixed_language.py`, never touches `ir/schema.py`, and never appears in the
final `Design`; resolution explicitly tries cross-language matching first
and never guesses an unmatched blackbox is a future `.lib` macro, since 1e
doesn't exist; (c) discovery is asymmetric by design, not a shared "did it
fail" check — Verilog's signal is error-severity diagnostics, VHDL's is an
explicit JSON attribute scan independent of exit status (which is always 0).

**What was actually built:**
- `backend/elaborator/rtl/verilog/elaborate.py`: `UnresolvedInstance`
  dataclass; `elaborate_allowing_unresolved(files, top=None)` (tolerates
  `UnknownModule` only, any other error diagnostic still raises);
  `list_module_names(files)` (syntax-only scan, no elaboration, reused for
  top-language detection); `_elaborate_instance_body` now recognizes
  `UninstantiatedDefSymbol` instead of silently dropping it. Also added an
  optional `top` parameter to plain `elaborate()` itself (previously
  auto-detect-only) — needed so a leaf reference can be elaborated by name
  even when unrelated other top-level modules exist in the same file set;
  backward compatible, existing callers are unaffected.
- `backend/elaborator/rtl/vhdl/elaborate.py`: matching `UnresolvedInstance`
  dataclass (independent, not shared with the Verilog one — same "small
  duplication over premature shared abstraction" call made throughout this
  milestone); `elaborate_allowing_unresolved(files, top)`; `list_entity_names`;
  `_build_design`/`_build_instances` now detect the `blackbox` attribute
  before attempting name-reconstruction and report it as an
  `UnresolvedInstance` instead of crashing or silently skipping it (a Yosys
  primitive like `$adff` and a blackbox reference are no longer treated the
  same way — one is internal synthesis machinery to ignore, the other is a
  real instance the caller needs to know about). Strict `elaborate()` now
  raises a clear, purpose-built error naming the unbound reference(s)
  instead of the confusing internal crash described above.
- `backend/elaborator/rtl/mixed_language.py` (new): `elaborate_mixed(files,
  top, top_language=None)`. Partitions by extension; detects top language
  via the two `list_*_names` scans (raises if the name exists in both,
  rather than guessing); runs the owning side in tolerant mode; for each
  distinct unresolved reference, elaborates the *leaf* side in **strict**
  mode as its own top (deliberately not tolerant — if the leaf itself has an
  unresolved reference, that's the out-of-scope alternating-chain case, and
  strict mode surfaces it as a clear error for free, no special detection
  logic needed); validates that every port name used at the instantiation
  site exists on the resolved definition (case-insensitively when the
  resolved side is VHDL, matching VHDL's own semantics, since the
  referencing source's port-map names were written without VHDL's case
  folding in mind) before splicing a real `Instance` into the owning
  `Definition` via `dataclasses.replace` (both are frozen). Parameter/generic
  overrides on a cross-language instantiation raise `NotImplementedError`
  rather than silently resolving against default generics — same v1 scope
  cut as the alternating-chain restriction.

**Verified end-to-end, both directions, with real ports/connectivity/
sequential-elements on both sides** (not just structural presence):
Verilog `top` instantiating a real Verilog leaf and a real VHDL leaf in the
same design (the VHDL leaf's `SequentialElement` comes through with
correctly-traced async-reset polarity, exercising the same inverter-tracing
logic 1b validated); the reverse direction, VHDL `top` with an unbound
`component` resolved against a real Verilog leaf (whose `SequentialElement`
comes through via the completely different always-block AST-walk code
path). Error paths verified directly, not assumed: no leaf files provided
→ clear `ValueError` naming the missing reference; a `#(...)` override on a
cross-language instantiation → clear `NotImplementedError`; the alternating
Verilog→VHDL→Verilog chain → clear `ValueError` naming both the outer
reference being resolved and the inner cause; a name defined in both
source sets → clear `ValueError` requiring `top_language`, resolved once
given explicitly.

**Fixtures**: `mixed_verilog_top/` (Verilog-owns-top, one native + one
cross-language leaf), `mixed_vhdl_top/` (VHDL-owns-top, unbound
`component` resolved against a Verilog leaf), `mixed_alternating_chain/`
(deliberately missing the innermost Verilog file, to prove the v1 scope
boundary rejects rather than mishandles it).

**Review finding, fixed before pushing:** the alternating-chain error message
was technically correct but genuinely misleading on first read. `_elaborate_leaf`
originally just prefixed the inner strict-`elaborate()` error text, which
includes "use the mixed-language orchestrator ... not elaborate() directly"
— sound advice for someone calling a front-end's `elaborate()` directly, but
actively wrong when it surfaces from *inside* the orchestrator's own leaf
resolution, which is exactly where this fires. Fixed by having
`_elaborate_leaf` construct its own top-level explanation (naming the leaf,
explaining this is either the deliberately out-of-scope alternating-chain
case or a genuinely undefined reference in the leaf's own source, and
pointing at this doc) with the inner tool error embedded as supporting detail
rather than the primary message.

### 1h — sequential-element identification — audited against original scope, not assumed done

Before starting 1e, 1h was explicitly re-checked against its original scope
text ("identify sequential elements — flip-flops and latches — from
always/process blocks... no FSM extraction") rather than assumed complete
just because flop extraction already existed as a side effect of 1a/1b. The
audit found one real correctness bug (not a coverage gap) and confirmed
several things either worked or didn't that had never actually been tested.

**Fixed — VHDL synchronous-reset flops were silently misclassified as
resetless, not just unrecognized.** GHDL never emits a single `$sdff`-style
primitive for a sync-reset register — it compiles
`if rst_n = '0' then q <= 0; elsif rising_edge(clk) then q <= d; end if;`
into a `$mux` (selecting between the reset value and real data) feeding an
otherwise-ordinary `$dff`. Before the fix, `_build_sequential_elements` only
ever looked at the `$dff`'s `D` input as a plain signal; since the mux's `Y`
output isn't a named signal, `_resolve_bits_to_name` returned `None` for it
in one place and the flop still got reported as `reset_kind=NONE` — a real
register with a real reset, silently described as having none. This is
exactly the "silently wrong" failure mode the async-reset inverter-tracing
work was designed to avoid, just in a spot that hadn't been exercised until
this audit built a sync-reset fixture and actually checked the output.

Fixed by generalizing the existing one-hop `$not`-tracing logic (previously
`_resolve_reset_signal`, now `_trace_through_one_inverter` +
`_resolve_control_signal`, shared by both call sites) and adding
`_find_reset_mux`: if a `$dff`'s `D` input is fed by exactly one `$mux` with
one constant input (the reset value) and one real-signal input (the data
path), resolve the mux's select line the same way the ARST pin was already
resolved. Verified directly, not just "no longer NONE": active-low and
active-high polarity both produce the correct `reset_active_low`, and a
genuine unrelated 2:1 data mux (two real signal inputs, no constant — a very
common RTL pattern) is confirmed **not** misidentified as a reset, which is
exactly the false-positive risk this fix could have introduced if the
"exactly one constant input" check were missing or wrong.

**Fixed — VHDL latch inference wasn't just unimplemented, it crashed
elaboration entirely.** By default, GHDL/Yosys treats latch inference as a
hard error (`error: latch infered for net "q" (use --latches)`) and aborts
— confirmed empirically. This meant any VHDL design containing a latch,
intentional or an accidental bug, previously failed `elaborate()` completely
(no ports, no hierarchy, nothing usable), not just "missed the latch."
Fixed by always passing `--latches`, and adding basic `$dlatch` (enable-only:
`EN`/`D`/`Q`, no reset/set pin) extraction — `is_latch=True`, `clock_ref` set
to the enable signal per the IR schema's own documented convention for
latches, `edge=None`. Confirmed a plain latch is now correctly identified,
*and* confirmed a latch-with-reset no longer crashes elaboration even though
that shape still isn't extracted (ports/hierarchy/connectivity all still
correctly present; only `sequential_elements` for that one register is
empty) — the "leave unrecognized, don't crash the world" principle used
everywhere else in 1a/1b, now actually true for VHDL latches too.

**Deferred, logged explicitly rather than silently dropped — does not block
1e:**
- **Latch with reset/set (VHDL).** Confirmed empirically it does *not*
  compile to a richer single cell type the way `$adff` does for async-reset
  flops — it's a `$not`/`$mux`/`$or` chain feeding a plain `$dlatch`'s `EN`
  and `D` pins, genuinely more complex than the one-hop tracing used
  everywhere else in this file. Revisit if/when a real VHDL design with a
  reset-and-latch pattern needs retention-cell insertion (1h's stated
  purpose) and the current "left unrecognized" behavior turns out to be
  insufficient rather than merely incomplete.
- **Verilog latches.** Not implemented at all (`is_latch` is hardcoded
  `False`; a real Verilog latch fixture confirmed zero `SequentialElement`s
  are produced, not a crash — unlike VHDL, pyslang never errors on a
  latch-shaped `always @(*)` block, so this is a pure omission, not a
  usability blocker the way the VHDL crash was). Revisit alongside VHDL
  latch-with-reset, ideally in the same pass so both languages reach parity
  together rather than drifting.
- **Multiple always blocks/processes jointly driving the *same* signal.**
  Distinct from (and much rarer than) "multiple blocks driving different
  signals," which is confirmed working for both languages. Revisit if a
  real design surfaces this pattern — deliberately not chased speculatively,
  since it's an unusual construct (typically implies mutually-exclusive
  drive conditions the extractor would need to reason about, not just walk
  independently).
- **`set_ref`/`set_active_low` (schema fields since 1c) are never
  populated by either front-end.** Any single-conditional-branch flop is
  always bucketed as a reset (`reset_ref`/`reset_kind`), even if the
  overridden value is semantically a preset-to-1 rather than a clear-to-0.
  Not explicitly named in 1h's original scope text (which only says
  "flops and latches," not "and distinguish set from reset"), and not
  actively wrong the way the sync-reset bug was — just an unused schema
  affordance. Revisit once Milestone 3b (retention-cell insertion, 1h's
  actual downstream consumer) defines whether set-vs-reset actually matters
  for retention strategy selection; no sense guessing at the distinction
  before the consumer says it's needed.

**Fixtures**: `vhdl_flop_sync_reset/`, `vhdl_flop_sync_reset_active_high/`,
`vhdl_data_mux_not_reset/` (the false-positive check), `vhdl_latch_min/`,
`vhdl_latch_with_reset_no_crash/` — all new, in
`tests/backend/test_vhdl_sequential_elements.py`.

**Post-1h fix found via real design use (Verilog/SV only) — one always_ff
block silently contributing zero registers, not just missing some.** A
real, complex personal SoC design (`aura`) had 46 real `always_ff` blocks
in its RTL; only 4 were producing a `SequentialElement`. Root cause:
`_extract_sequential_element` (as originally written) recognized *at
most one* assignment per if/else branch — `_assignment_target` required
the branch to be a bare `ExpressionStatement`, so the moment a branch was
a `begin...end` block with more than one assignment, or a `case`/`unique
case` statement, the whole block returned `None`. This is an extremely
common, idiomatic RTL shape — one `always_ff` resetting/updating several
unrelated registers together (`rv_core.sv`'s own reset branch alone
assigns 6 registers) — not a corner case; it accounted for the large
majority of the 42 missed blocks.

Fixed by generalizing from "find one assignment in a specific shape" to
"find the block's clock/reset structure once (from the top-level if/else,
same as before), then walk the *entire* statement tree under it — both
branches, nested begin/end blocks, `case` arms — collecting every
distinct assigned signal" (`_extract_sequential_elements`, now plural,
returns a `list[SequentialElement]`; new `_collect_assignment_targets`
does the walk). Every resulting `SequentialElement` for the same block
shares that block's clock/reset metadata. Still strictly "which signals
are assigned," never "to what value" — no data-path/next-state modeling
crept in; the fix is about statement-tree *coverage*, not about tracking
new information. Verified end-to-end against the real triggering design:
4 → 147 sequential elements recognized, once re-elaborated after this fix.
VHDL's flop/latch extraction (`ir/vhdl_sequential.py`, a completely
separate code path built on Yosys cell types, not statement-tree walking)
was not touched and had no equivalent bug — the two front-ends' sequential
extraction don't share implementation, only the target IR shape.

Regression-tested with `tests/backend/golden_rtl/verilog_multi_register_block/`
(`multi_reg`: one always_ff resetting 3 registers together then a `case`
updating one per branch; `multi_reg_resetless`: the same shape with no
if/else at all) via `test_one_always_ff_resetting_several_registers_together_yields_one_element_each`
and `test_resetless_multi_assignment_block_also_yields_one_element_each`
in `test_verilog_elaborate.py`.

### 1e — `.lib` black-box macro integration — RESOLVED

Scope, unchanged from the original framing at the top of this doc: `.lib`
files describe only black-box hard macros (SRAMs, analog IP, hard IP
blocks) with no RTL source, so the elaborator can pick up their
pin/interface info. Not a standard-cell/synthesis Liberty reader — no
timing arcs, no power tables, no cell-mapping logic. This is a structural
consequence of what's read (extraction only ever walks a cell's
`pin`/`bus`/`type` groups), not a policy enforced separately.

**Parser choice: `liberty-parser`** (PyPI, Codeberg `tok/Sourcerer`),
picked over `eda-liberty-parser` (GitHub, MIT license, but 0 stars/forks
and ~4 months old at evaluation time, essentially unproven) on maturity
grounds — `liberty-parser` has a multi-year history and 10 stars/6 forks.
License is **GPL-3.0-or-later — logged, not resolved**, same treatment as
GHDL's GPL-2.0-or-later dependency from 1b: this is now the second GPL
dependency in the toolchain (different GPL version); both should be
covered by the same eventual distribution/licensing review, not two
separate ones.

**Bus width extraction** prefers resolving a bus's `bus_type` against the
library's own `type()` groups (`bit_from`/`bit_to`, confirmed via a real
parse to come back as Python `int`s, e.g. 8/7/0 for an 8-bit bus) over
simply counting enumerated `pin()` sub-groups. This matters because it
recovers the true declared range (`BitRange(left=7, right=0)`), not just a
derived width — the same range-preservation principle 1c established for
Verilog/VHDL ports. Counting sub-groups is used only as a fallback when a
bus has no `bus_type` at all (or the referenced `type()` group doesn't
carry `bit_from`/`bit_to`), producing `BitRange(width-1, 0)` — same
fallback style already used for VHDL's own width-only cases.

**Direction mapping**: Liberty `input`/`output`/`inout` map to
`PortDirection` normally. `internal` (a pin/bus not part of the macro's
external instantiation interface, e.g. a debug node) is skipped — not
represented as a `Port` at all, for both scalar pins and buses uniformly.
Any other direction value raises clearly rather than being guessed at
(confirmed via a fixture with a deliberately bogus `direction : tristate;`
value).

**CAVEAT, not enforceable in code**: Liberty syntax gives no way to
distinguish "a genuine black-box hard macro" from "an ordinary
standard-cell in a full synthesis library" — both are just `cell` groups
with pin lists. `backend/elaborator/lib/elaborate.py`'s module docstring
states this explicitly: callers are responsible for only pointing it at
`.lib` files that actually describe black-box macros. Pointing it at a
full standard-cell timing library will "work" in the sense of producing a
Definition per cell, but that isn't what this module is for.

**Schema changes**: `Definition.is_black_box` (reserved since 1c, unused
until now) is finally set `True` for every `.lib`-derived Definition. New
`SourceLanguage.LIBERTY` enum value — a `.lib` blackbox wasn't written in
any HDL, so tagging it VERILOG/VHDL would be dishonest; `language` is
provenance metadata and needed an honest value. A black-box `Definition`
always has empty `instances`/`sequential_elements`/`nets` (a `.lib` cell
has no visible RTL body) and empty `parameters` (no generics/parameterized-
variant concept in Liberty), with `definition_id` computed the same
deterministic way as every other Definition (`make_definition_id(base_name,
{})`).

**Structural distinction from 1d's `UnresolvedInstance`** (the thing this
step was explicitly required to get right before writing code):
`UnresolvedInstance` is private, per-front-end, and purely transitional —
it never touches `schema.py` and never appears in a `Design` handed back
to a caller (see 1d's own section above). A black-box `Definition`
(`is_black_box=True`) is the opposite: a first-class, *permanent* member
of `Design.definitions`, exactly like tagging a Definition
`SourceLanguage.VHDL` is permanent. It is never "resolved away" — it just
has intentionally-empty internals forever.

**Orchestration** (`backend/elaborator/lib/elaborate.py`):
- `elaborate(files) -> dict[str, Definition]` — parses `.lib` files into
  black-box Definitions, keyed by `definition_id`.
- `resolve_black_boxes(definitions, unresolved, lib_definitions,
  owner_language) -> (patched_definitions, still_unresolved)` — matches
  each `UnresolvedInstance` (from either front-end; duck-typed on
  `owner_definition_id`/`name`/`referenced_definition_name`/`connections`,
  since the two front-ends' dataclasses are structurally identical but
  distinct types) to a black-box Definition and splices in a real
  `Instance`. **Matches on `base_name`, not `definition_id`** —
  `make_definition_id()` folds in `parameters`, and a black-box
  Definition's parameters are always `{}`, so an id-based comparison would
  silently fail to match any RTL instantiation carrying a real
  parameter/generic override even though it's genuinely the same macro.
  Confirmed directly in `test_resolve_black_boxes_matches_by_base_name_not_definition_id`:
  computes a hypothetical parameter-carrying id for the same reference,
  asserts it differs from the real black-box Definition's id, and asserts
  resolution still succeeds. VHDL-side matching folds case (component names
  come back from GHDL/Yosys lower-cased), same as 1d's own cross-language
  matching; confirmed against a fixture where the `.lib` cell name is
  mixed-case (`SRAM_256x8`) and the VHDL component reference is not.
  Leftovers in `still_unresolved` are the caller's problem — a genuinely
  undefined reference, or one that belongs to a different resolution
  source entirely.
- `elaborate_with_black_boxes(rtl_files, lib_files, top=None) -> Design` —
  convenience entry point: elaborates a *single-language* RTL design
  (all-Verilog/SV or all-VHDL, detected by extension) tolerantly, resolves
  every unresolved reference against the given `.lib` files, and raises
  clearly (naming what's missing) if anything is left over.

**v1 scope boundary, same tracked-gap treatment as 1d's alternating-chain
restriction**: resolves `.lib` references for a single-language RTL design
only. A design needing *both* a cross-language reference (1d) and a `.lib`
black-box reference (1e) at once is not handled by this pass — not
silently mishandled, just not wired up yet. `resolve_black_boxes()` is
deliberately shaped (a plain `definitions` dict in/out, plus leftover
`UnresolvedInstance`s — the same shapes `mixed_language.py` already
produces/consumes) so that composing the two later doesn't require
rework; wiring that composition together is follow-on work once a real
need for it shows up.

**Not factored into a shared module with `mixed_language.py`**, despite
both doing "splice a resolved Instance into its owner Definition":
inspected the actual overlap and it's ~6 lines (build a `PortConnection`
list, `dataclasses.replace` the owner) versus meaningfully different
matching logic around it — 1d matches by elaborating the referenced name
as its own leaf-language top and comparing `definition_id`s, 1e matches by
`base_name` against an `is_black_box`-filtered pool. Forcing a shared
abstraction around that small a common core would cost more in indirection
than it saves; noted directly in `elaborate.py`'s module docstring so the
decision isn't silent.

**Fixtures**: `tests/backend/golden_lib/sram_macro.lib` (scalar pins, a
bus resolved via `bus_type`, a bus falling back to enumerated-pin
counting, an `internal`-direction pin), `unrecognized_direction.lib` (the
fail-loud case), `verilog_with_sram_blackbox/` and
`vhdl_with_sram_blackbox/` (end-to-end `elaborate_with_black_boxes`, the
latter exercising VHDL case-insensitive matching against a real
ghdl/yosys run) — all in `tests/backend/test_lib_elaborate.py`.

**LIB priority — added on explicit request, real design use.** A module
can have *both* a real RTL body (in `rtl_files`) and a matching `.lib`
black-box cell (same `base_name`) at once — a real, common pairing, not a
hypothetical: a memory-compiler macro is often shipped with a behavioral
RTL model for simulation *and* a `.lib` for synthesis/timing, describing
the same physical cell. Confirmed against a real design (`aura`)'s own
SRAM macros, whose RTL file even says so directly in a comment:
*"Synthesis treats these as black boxes; the behavioural bodies exist for
simulation only."* Requirement: the `.lib` version must win.

This is a genuinely different code path from `resolve_black_boxes()`
above, not an extension of it — that function only ever sees instances
pyslang couldn't bind to *any* real module at all (`UnresolvedInstance`).
A module with real RTL present is never unresolved in the first place
(pyslang resolves it normally), so `resolve_black_boxes()` never even got
a chance to consider overriding it — confirmed directly against `aura`:
before this fix, `elaborate_with_black_boxes` returned zero black-box
Definitions for a design whose RTL file list plainly included matching
`.lib` files, because there were zero unresolved references to trigger
`resolve_black_boxes()` at all.

Fixed with a new, separate function, `_apply_lib_priority(definitions,
lib_definitions, owner_language)`, run unconditionally (a no-op when
`lib_files` is empty or no `base_name` collides, so it never changes
behavior for a caller not using this feature) directly against the fully
real-elaborated `Design.definitions` — *before* `resolve_black_boxes()`
runs, so both mechanisms compose cleanly (LIB priority for "both exist,"
`resolve_black_boxes()` for "only `.lib` exists"). For every real
Definition whose `base_name` matches a black-box `.lib` cell (matching
convention unchanged from `resolve_black_boxes()`: by `base_name`, VHDL
case-folded), every `Instance` across the whole `Design` pointing at that
real Definition gets rewritten to point at the black-box Definition
instead, and the now-unreferenced real Definition is dropped. Connections
are validated the same way `resolve_black_boxes()` validates a spliced
`UnresolvedInstance`'s connections (same error shape) — the instance's
connections were built against the *real* RTL module's port names, which
aren't guaranteed to be identical to the `.lib` cell's pin names, even
though for `aura`'s own SRAM macros they were confirmed to match exactly
(foundry-style `CLK`/`CEN`/`WEN`/... on both sides).

**Fixtures**: `tests/backend/golden_lib/verilog_sram_lib_priority/` (a
real RTL body *and* a `.lib` cell for the same `SRAM_256x8`, port names
matching) plus in-memory `Definition`/`Instance` fixtures for the no-op
and connection-mismatch cases, in `test_lib_elaborate.py`.

**`pg_pin` (power/ground pin) parsing — added on explicit request, real
design use.** Found via the front end's PortList view against the real
`aura` design: a real macro's power/ground pins (`VDD`/`VSS`) never
appeared. Root cause: `_build_black_box_definition()` only ever called
`cell_group.get_groups("pin")`/`get_groups("bus")`, never
`get_groups("pg_pin")` — a structurally separate Liberty group kind (no
`direction` attribute at all; classified instead by a `pg_type`
attribute), confirmed via a real read of `aura`'s own `.lib` files
(`pg_pin (VDD) { voltage_name: VDD; pg_type: primary_power; }`, same
shape for `VSS`/`primary_ground`).

New `PortDirection.PG`/`PortDirection.PG_INTERNAL` values (previously
only a mock-only frontend concept, see `frontend/src/shared/models/rtl.py`'s
`PortDirection` — that docstring is updated to point at this real
support). `_build_pg_pin_port()` maps `pg_type` to one of the two:
`primary_power`/`primary_ground`/`backup_power`/`backup_ground` (the
cell's real external supply pins) → `PG`; `internal_power`/
`internal_ground` (a pin that only exists on an internal, post-switch
rail, never meant for external connection) → `PG_INTERNAL` — same
"externally visible vs. not" distinction ordinary `pin`'s `internal`
direction already draws, just spelled differently for `pg_pin`. Any other
`pg_type` (e.g. `pwell`/`nwell`/`deepnwell` — bulk/well connections, not
power/ground supply pins) raises clearly rather than being guessed at,
same "fail loud on the unrecognized" policy as ordinary pin direction.

Only `primary_power`/`primary_ground` are exercised by `aura`'s own
macros; `internal_power`/`internal_ground` is a real, spec-documented
Liberty value covered by a dedicated fixture/test even though no
personally-owned design exercises it yet.

**Fixtures**: `tests/backend/golden_lib/sram_macro.lib` extended with
`pg_pin (VDD)`/`pg_pin (VSS)` (`primary_power`/`primary_ground`) and
`pg_pin (VDDI)` (`internal_power`); new `unrecognized_pg_type.lib` (the
fail-loud case, a `pg_type : nwell;` pin) — all in `test_lib_elaborate.py`.

**Quoted Liberty names silently corrupted — RESOLVED, documentation gap
closed 2026-08-31, built 2026-08-12.** `liberty-parser` returns a quoted
group name or argument (e.g. `cell ("MY_CELL")` instead of the more
typical bare `cell (MY_CELL)`) as its own `EscapedString` type, not a
plain `str` — and `EscapedString.__str__` re-wraps the value in literal
quote characters on every stringification, rather than stripping them.
Every downstream name site that assumed a plain string (cell/pin/bus/
`pg_pin` names) silently carried the corrupted, still-quoted value all
the way into the IR. Found via a real crash on a real `.lib` file using
quoted names — not from a spec read. Fixed via two shared, centralized
helpers — `_unwrap_liberty_string()`/`_unwrap_optional_liberty_string()`
(`backend/elaborator/lib/elaborate.py`) — applied at every name
extraction site rather than patched ad hoc per call site (same
"centralize the fix, don't scatter it" discipline this project's own Tcl
runtime layer already established for UPF/CPF parsing). Extended once
more, on a direct follow-up, to `related_power_pin`/`related_ground_pin`/
`related_bias_pin` after the identical corruption class was found there
too. **Tests**: `test_quoted_pin_and_pg_pin_names_come_back_as_plain_str`,
`test_quoted_cell_name_becomes_a_plain_str_base_name`,
`test_quoted_name_value_has_no_stray_embedded_quote_characters`,
`test_quoted_bracketed_pin_name_is_preserved_exactly`,
`test_quoted_bus_type_group_name_resolves_the_bus_width`,
`test_quoted_related_power_and_ground_pin_have_no_stray_embedded_quote_characters`
(`test_lib_elaborate.py`); `tests/backend/golden_lib/quoted_names.lib`.

## Post-1a fixes found via the validation harness (see docs/backend_integration.md)

Two real bugs in already-shipped `backend/elaborator/rtl/verilog/elaborate.py`,
found through real personal use against real files (not discovered during
1a's own build/audit passes), fixed and covered by new regression tests.

**Explicit `top` only matched pyslang's auto-detected root instances, not
any named module.** `elaborate(files, top="cpu_core")` failed with a
confusing "not found among top-level instances" error whenever
`cpu_core` was legitimately instantiated by something else in the same
file set — exactly the real "compile just a DUT submodule out of a
larger loaded design" workflow. Root cause: `_select_top_instance` only
ever filtered `compilation.getRoot().topInstances` (pyslang's own
no-parent-instantiation auto-detection) by name, even when a name was
given explicitly. Fixed by passing `top` to pyslang itself via
`CompilationOptions.topModules` (a real pyslang mechanism, confirmed
directly, not assumed) at `Compilation` construction time -- pyslang
then treats the named module as an elaboration root regardless of where
else it's instantiated, and any other now-unreferenced top-level module
in the file set is silently fine, not flagged as an error. A `top` name
that doesn't exist at all still fails clearly, via pyslang's own
`InvalidTopModule` diagnostic. `top is None` (auto-detect) is
unaffected. See `_make_compilation()`'s docstring for the full
verification writeup; `test_explicit_top_selects_a_submodule_instantiated_elsewhere_in_the_file_set`/
`test_explicit_top_naming_a_nonexistent_module_raises_a_clear_error` in
`test_verilog_elaborate.py`. VHDL's `elaborate()` never had the
equivalent bug -- `top` was already mandatory there (no auto-detect
corner case), and GHDL's own `-e <entity>` elaboration model already
supports naming any analyzed entity regardless of where else it's
instantiated, natively.

**Error diagnostics discarded pyslang's own diagnostic data, keeping
only the bare code name.** A real syntax bug (a missing comma in a port
connection list) surfaced as
`"pyslang reported 1 error diagnostic(s): ['DiagCode(ExpectedToken)')]"`
-- no file, no line, no description. Fixed with a new
`ElaborationError(ValueError)`, raised instead of a bare `ValueError`
from both `elaborate()` and `elaborate_allowing_unresolved()`: it
carries real `.file`/`.line` attributes from the *first* offending
diagnostic's actual source location
(`compilation.sourceManager.getFileName()`/`.getLineNumber()`), and its
message is built from pyslang's own `DiagnosticEngine` +
`TextDiagnosticClient` -- the same diagnostic-formatting machinery a
real pyslang-based CLI tool uses -- not hand-rolled text. For the
reported bug this now reads `soc_top.v:23:32: error: expected ','` with
the actual source line and a caret at the offending token, not just a
diagnostic code. `backend/api/` (the validation harness's facade)
re-exports `ElaborationError` so the frontend can check `isinstance`
precisely when deciding whether a caught exception carries real
file/line data to show. See `test_syntax_error_produces_a_real_message_with_structured_file_and_line`
in `test_verilog_elaborate.py`, and `tests/backend/golden_rtl/verilog_syntax_error/`
for the dedicated fixture. Deliberately scoped to RTL/Verilog only --
UPF/CPF elaboration errors and mapping/validation findings have no
equivalent structured-location exception type yet; see
docs/backend_integration.md's "Errors: RTL is structured, UPF/CPF/mapping
are still coarse" section for why that wasn't extended further in this
pass.

## Post-1a fix found via real design use (Verilog/SV only) — generate blocks were entirely invisible

Surfaced while investigating why LIB priority (see 1e's own section
above) appeared to have no effect on a real design: a `generate if/else
if` selecting between two hard-macro configurations meant the selected
macro instance never appeared in the elaborated IR at all, so LIB
priority never got a chance to see (let alone override) it. Root cause
was one level deeper than LIB priority itself, though: `_elaborate_
instance_body`'s member-walking loop had no handling whatsoever for
`GenerateBlockSymbol` (`generate if`/`case`) or `GenerateBlockArraySymbol`
(`generate for`) as member kinds. Not a partial miss -- *everything*
declared inside a generate block (instances, nets, sequential elements)
was silently absent from the IR, for any design using conditional or
looped generate constructs at all, confirmed via a real design (`aura`)
where this affected memory-macro selection specifically but is not
generate-content-specific in any way.

Confirmed directly via pyslang, not assumed: iterating a module body's
members yields one `GenerateBlockSymbol` *per branch* of an if/else-if/
else generate (all three, for a two-branch if/else -- pyslang doesn't
omit the untaken ones), each carrying its own `isUninstantiated` --
`True` for every branch except the one actually selected at elaboration
time, `False` for the live one. A `generate for` loop comes back as a
single `GenerateBlockArraySymbol` whose `.entries` is one
`GenerateBlockSymbol` per iteration, each with its own `constructIndex`
and (again) `isUninstantiated`.

Fixed by extracting the per-member dispatch that used to live directly
in `_elaborate_instance_body`'s loop into a new, recursive
`_walk_body_members()`, called once for the module/interface body itself
and then recursively for the content of any *live*
(`not isUninstantiated`) generate block found within it -- uniformly, so
arbitrarily nested generate constructs (a `generate if` inside a
`generate for`, etc.) are handled the same way without special-casing
depth. `generate for` needs one more piece `generate if` doesn't: since
different loop iterations can genuinely declare an
instance with the same literal name (`u_inst` inside `begin : g_tap ...
end`, once per iteration), instance names found inside a
`GenerateBlockArraySymbol` entry are prefixed with
`"{array_name}[{constructIndex}]."` before reaching `_build_instance` --
without this, later iterations would silently collide with (and, in
this dict-keyed IR, overwrite) earlier ones instead of each being a
distinct `Instance`.

Verified end-to-end against the real triggering design: without this
fix, `aura`'s three real SRAM macro modules never appeared in the
elaborated `Design` at all (only their wrapper modules did, each with an
empty `instances` list); with it, all three appear correctly, LIB
priority (1e) can now see and override them as designed, and
`aura`'s total elaborated definition count and sequential-element count
both increased (definitions: 51 -> 66; sequential elements: 147 -> 188)
purely from previously-invisible generate-block content becoming visible.

**Fixtures**: `tests/backend/golden_rtl/verilog_generate_blocks/generate_blocks.sv`
(`gen_if_select`: a two-branch `generate if/else` selecting between
`leaf_a`/`leaf_b`; `gen_for_array`: a 3-iteration `generate for`, each
iteration declaring an instance named identically to the others) via
`test_generate_if_else_only_the_live_branch_contributes_its_instance`
and `test_generate_for_produces_one_instance_per_iteration_with_disambiguated_names`
in `test_verilog_elaborate.py`.

## Testing

- Golden fixtures live in `tests/backend/golden_rtl/`. Add fixtures per
  sub-task as it's implemented: pure Verilog, pure SV, pure VHDL, mixed
  Verilog+VHDL, a design with a black-box `.lib` macro instance, and a design
  with a mix of flops and latches (including at least one intentionally
  incomplete `if`/`case` to confirm latch detection, not just flop detection).
- Since FSM extraction is out of scope, no fixture should assume the IR
  exposes FSM state — a test that would only pass by inferring FSM structure
  is a sign of scope creep back into Milestone 1.
