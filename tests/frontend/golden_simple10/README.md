# simple10 — hand-authored UPF/CPF golden fixtures

Golden regression fixtures for the "Load Power Intent" mapping feature,
plus the findings report from the iterative exercise that produced them.

## What this is, and why it is separate from the round-trip suite

`tests/frontend/test_power_intent_round_trip.py` proves the importer can
read back **the app's own generated output**. That is necessary and not
sufficient: it can only ever exercise the one syntactic shape the
generator produces. If the mapper happened to work only on power intent
shaped like its own output, the round trip would still pass.

These fixtures were hand-authored the way a designer or vendor actually
writes power intent — different command ordering, equivalent-but-
different flag spellings, naming that ignores this app's own conventions,
a different syntactic style per fixture. **Every single one caught at
least one real defect the round trip could not see: 20 findings across
8 fixtures**, including one that made every imported Power Domain the
same colour and one that rejected an otherwise-valid file outright.

## Target design

`tests/frontend/golden_simple10/design/` — top module `sram`:

```
sram                     (top)
├── u_sram               FAKE_SRAM_256X32  .lib macro
│                        PG pins: VDD (primary_power), VSS (primary_ground),
│                                 VDD_INT (internal_power)
└── u_sub                sub  (soft hierarchy; one flop, o_q)
```

Boundary ports: `clk rstn cen_b wen_b sleep_b iso_en a[7:0] d[31:0]
q[31:0] power_good`.

The design lives **here, inside the test tree**, so this suite is
self-contained and always runs. It did not start that way — see
correction 3 below.

### Three corrections to earlier passes of this exercise

Recorded rather than quietly amended, because they change what some
findings below mean:

1. **Iterations 1–7 ran against the wrong copy of this design.** An
   in-repo search concluded `design_examples/simple10` did not exist and
   fell back to `examples/upf_example_1_sram/design/` — near-identical,
   but an older snapshot **without `u_sub`'s flop** and without `rstn`.
   Everything is re-pointed at the real tree now; all 62 tests pass
   against it unchanged, since no fixture depended on the difference.
   Re-pointing it also exposed a third-order version of the same
   mistake: `validate_fixture.py` names the design tree independently
   of the tests, only one was updated at first, and the harness
   silently kept elaborating the old snapshot -- making a retention
   `-elements` list look missing from generated output when the
   generator was fine. The two are now pinned to the same tree by
   `test_the_reporting_harness_points_at_the_same_design_as_these_tests`.

3. **The example was not part of the test run.** With the design outside
   the repo, `test_hand_authored_power_intent.py` skipped rather than
   failed when the sibling tree was absent — so a fresh clone or a CI
   job silently ran 62 fewer tests and reported green. The whole example
   (RTL, `.lib`, every fixture, both scripts) was moved in here on
   explicit request, which removes the skip entirely. Verified by
   renaming the sibling tree away and re-running: still 62 passed.

   The RTL was **copied**, not deleted from
   `<repo>/../design_examples/simple10` — those files are not under
   version control anywhere, so removing them would be irreversible.

   That turns out to be this project's established house rule rather than
   an improvised choice: `docs/macro_supply_set_golden_fixtures.md`
   documents the same source-outside-repo → copied-verbatim-into-tests
   convention for the whole `simpleN` fixture family, along with a
   re-verify-sync procedure for exactly the drift this creates. This
   family is indexed in that doc's table too. The copy here is the one
   the tests use.

2. **The design did not elaborate when first opened.** `sub.sv` declared
   `o_q` as `output wire` while assigning it procedurally inside
   `always_ff`, *and* drove it a second time with a leftover
   `assign o_q = ~i_q;` — so elaboration failed outright with
   "cannot assign to a net within a procedural context". Fixed to
   `output logic` with the stray assign removed (backup at `sub.sv.bak`),
   which is the only reading consistent with the `clk`/`rstn` plumbing
   threaded through both modules. That fix is what makes **finding 16
   demonstrable here at all** — see fixture 08.

## Layout

| Path | What it holds |
| --- | --- |
| `design/` | The RTL and `.lib` every fixture resolves against. |
| `power_intent/` | The hand-authored fixtures. Permanent. |
| `validate_fixture.py` | Loads one fixture through the real pipeline and reports what the app produced, including the colour chain. |
| `regenerate.py` | Re-exports every fixture, which is how finding 20 was caught. |

`regenerate.py` writes into `generated/` beside the fixtures, which is
**gitignored** — it is derived output, rebuilt on demand, and nothing
asserts against it, so committing it would only add noise.

The permanent tests are
`tests/frontend/test_hand_authored_power_intent.py` (65 tests). Each one
names the defect its fixture caught, so a regression is traceable to the
thing it broke.

```
python3 tests/frontend/golden_simple10/validate_fixture.py \
    tests/frontend/golden_simple10/power_intent/02_vendor_dense_switch_iso_ret.upf
```

## Fixture index

| Fixture | Authoring style | Covers | Findings |
| --- | --- | --- | --- |
| `01_designer_domains_first.upf` | Domains declared before any supply exists; the older `set_domain_supply_net` idiom instead of `-supply`; `-function` spelling; lowercase set names | Power Domains (nested, `-exclude_elements`), supply ports/nets/sets, power states | 1, 2, 5, 11 |
| `02_vendor_dense_switch_iso_ret.upf` | Vendor template: dense one-liners, `;`-separated commands, section rules as comments | Power Switch (+`map_power_switch`), Isolation, Retention (+`set_retention_control`), macro PG hookup incl. the internal PG pin, secondary supply set | 8, 10, 11, 19 |
| `03_hier/sram_top.upf` + `inv_block.upf` + `sram_macro.upf` | Top integration file sourcing one block file per partition | **Hierarchical `load_upf`: flatten vs. macro attachment**, multi-hierarchy domain, Level Shifter (+cell), Port Attributes on multi-bit ports | 15, 16, 18 |
| `04_cpf_modes.cpf` | Real CPF idiom: `set_design` block, power/ground nets declared separately, rules named by `-from`, modes from nominal conditions | CPF end to end, power modes, default domain, boundary ports | **3**, 7, 14 |
| `05_legacy_pst_shared_set.upf` | Migrated from a 1801-2009 flow: legacy `create_pst`/`add_pst_state` | **Power State Table / system modes**, one supply set shared as primary + secondary | **12**, 13 |
| `06_wells_and_refinement.upf` | Bias-aware flow; strategy built up over two commands | N-Well/P-Well function handles, `-update` successive refinement | 6, 17 |
| `06_unsupported_function_kind.upf` | — | Negative: a function kind with genuinely nowhere to go must still fail loudly | (guards 6) |
| `07_dangling_refs.upf` | Syntactically valid, semantically wrong against this design | ERROR classification, partial resolution, no-crash guarantee | 4, 19 |
| `09_supply_port_directions.upf` | All four Direction states at once, including a port that declares none | `create_supply_port -direction {in\|out\|inout}` and the no-flag case | (covers the Direction feature) |
| `08_retention_all_elements.upf` | `set_retention` with the `-elements` flag deliberately omitted | Retention over a domain's own flops; **the only fixture that needs `u_sub`'s flop** | (demonstrates 16) |

---

# Findings report

Twenty findings. Seventeen were real defects and were fixed; three are
recorded as non-findings so they are not re-investigated.

## Colour derivation

The exercise checked the colour chain explicitly and separately from
whether the right objects exist, because that is precisely where a
discrepancy can hide while every entity looks correct. Four of the
findings are colour-chain bugs, and two of them are severe.

### 1. Every imported Power Domain came out the same default green ✅ fixed

*Fixture 01.* A domain's colour is not its own property — it derives from
its primary supply set's power rail. But the live recompute
(`_refresh_domain_derived_colors()`) runs only from the create/edit
paths, **never after a bulk restore**, so the import's placeholder seed
was never corrected and nothing downstream would ever fix it.

Derived at import time now, via a new
`resolve_supply_set_color_from_placements()` — the pre-view counterpart
to the icon-based resolver, kept adjacent to it so the shared match rule
(name equal **and** owner path in the set's `owner_paths`) is stated in
one place.

### 2. Transparency was 0.5, not the rule's 0.8 ✅ fixed

*Fixture 01.* `PowerDomainPlacement`'s own default predates the colour
becoming derived at all — it was the initial value of a user-facing
slider that no longer exists.

### 3. Every CPF supply got the neutral grey — the worst finding here ✅ fixed

*Fixture 04.* No name was classified as a power rail for **any** CPF
file, so every supply fell back to grey and every domain came out
identical: the exact opposite of the one hard requirement. Role
classification read only supply *sets*, and CPF has no such construct —
it states the same thing per domain
(`update_power_domain -primary_power_net/-primary_ground_net`). Now read
from both sources. The connections cannot simply replace the sets, since
classification runs *before* a CPF set is synthesised.

### 4. A strategy naming a missing supply set imported silently ✅ fixed

*Fixture 07.* The strategy appeared correctly in every list, and its
badge rendered default grey with **nothing anywhere saying why** — the
colour was wrong and the reason was invisible. The importer is the only
layer that can report this: the backend mapper classifies
`isolation_supply`/`retention_supply` as `NOT_ATTEMPTED` (no
independently verified target dict — see `docs/milestone3_mapping.md`),
which the validation layer then *excludes* from error counts by design.

## Supply sets and domains

### 5. A duplicate supply set was synthesised per domain ✅ fixed

*Fixture 01.* `PD_TOP_SS` appeared beside the authored `ss_aon`, and the
domain pointed at the **invented** one. The CPF-motivated synthesis fired
on `DomainSupplyConnection`s alone — but the very common
`set_domain_supply_net` idiom produces those in UPF too, alongside real
named sets. An authored set matching the same rails now wins; a name is
invented only when nothing authored matches.

### 6. N-Well / P-Well function handles rejected the whole file ✅ fixed

*Fixture 06.* `-function {nwell VNW}` did not merely lose data — the
elaborator **raised** on the function kind, so an otherwise-valid
bias-aware file failed outright. It was right to fail loudly when the IR
had nowhere to put it; the actual gap was that the IR lacked fields this
app's own `SupplySetPlacement` has always had (`n_well`/`p_well`). Added
through IR → builders → mirror → features list → importer. An
unsupported kind still fails loudly, guarded by
`06_unsupported_function_kind.upf`.

### 7. A root-scope CPF net was classified as an Internal Supply ✅ fixed

*Fixture 04.* Invalid in this app's own model, where an Internal Supply
belongs on an inner block and never the design top. The boundary test was
UPF-specific — a net accompanied by `create_supply_port` — and CPF has no
such command. Scope is the only signal CPF gives, and the right one.

## Connectivity

### 8. Macro PG connections were dropped entirely ✅ fixed

*Fixture 02.* `connect_supply_net -ports {u_sram/VDD}` imported nothing,
so **macro PG connectivity did not work at all**. The target lookup
matched only bare names against declared nets; a macro PG placement is
derived from the `.lib` when the scene is built and so is not in that
dict. Its id is deterministic, though, so a connector can name one before
it exists — now via a shared `macro_pg_pin_id()` so the two sides cannot
drift. Covers the macro's internal PG pin too.

This is also the form the app's **own** generator writes, so a generated
macro hookup did not survive a reload either; the round-trip fixture
simply never contained one.

### 9. A switch's Supply In connector was not rebuilt ✅ fixed

Found by the round-trip suite rather than these fixtures, but in the same
family. Because the pin name is only representable when there *is* a net
to pair it with, it survived one trip and vanished on the next.

## Power Switch

### 10. `-control_port {pin net}` imported the net where the GUI means the pin ✅ fixed

*Fixture 02.* Silently relabelled. Both halves are now kept in the IR
(`control_ports` / `control_port_pins`), because **both readings
genuinely occur**: `tests/backend/golden_upf/upf_basic` writes the bare
form as a real RTL port and the mapper resolves it as one, while this
app's generator writes the bare form carrying a pin name. A bare value is
therefore recorded as both, and the "net was dropped" warning is
suppressed when the two are identical (`_REDUNDANT_WHEN_EQUAL_TO`).

## Power states

### 11. Authored states on an internal net were dropped with no trace ✅ fixed

*Fixture 02, refined by fixture 01.* Now warned — but **only when no
Power Switch drives the net**. With one, the states are re-derived from
the switch chain and the authored list is reproduced, so warning would be
a false positive, including on this app's own output (which exports an
internal net's derived states because a downstream consumer needs them).

### 12. UPF's entire system-mode table reached nothing ✅ fixed

*Fixture 05.* A legacy PST **is** this app's Power State Table — the
exporter already writes one out as `create_pst`/`add_pst_state` — but the
import never populated it. The modes arrived as `PowerState` records that
reached neither the table nor any supply, leaving only a "shape is not
modelled" provenance warning that said nothing about the loss.

Imported as real table rows now, through a **ninth** state family: the
Power State Table is owned by its own tab rather than the diagram, so it
is handed over separately via the same `load_state()` a project load
already uses. Read from a new structured `PowerState.supply_states`
rather than by re-parsing the `expr` string this codebase synthesises for
those rows — two places agreeing on a private format is the drift hazard.

### 13. `add_pst_state -pst` failed with "no create_pst named 'None' found" ✅ fixed

*Fixture 05.* Both `-pst` and `-pst_name` were **registered** as
recognized options but only `-pst_name` was ever read — the "recognized
but never read by any handler" failure mode this project has hit before.
`-pst` is the 1801 LRM's own spelling, so any hand-written or migrated
file hits it.

### 14. A CPF power mode is reported, not guessed at ✅ by design

*Fixture 04.* Its conditions are per **domain**; a table row is per
**net**. No honest general conversion exists, so it produces a warning
naming the conditions — the same asymmetry the exporter already documents
in the other direction.

## Strategies

### 15. Port attributes on a multi-bit port were invisible in the GUI ✅ fixed

*Fixture 03.* A whole-port assertion (`-ports {d a}`) was stored under the
**bare** port name — a key the editor never reads for a multi-bit port.
The assignment existed in the state and showed as 32 unassigned rows.
Expanded per bit against the elaborated top-level ports, the only place
the width is known.

### 16. `set_retention` with no `-elements` rendered nothing ✅ fixed

*Fixture 03.* UPF's omitted flag means every retainable element in the
domain; importing it as an empty list was faithful to the file but wrong
for this app, whose retention overlay derives which blocks get a badge
**from** the elements. Expanded at import time.

Fixed there rather than by teaching the overlay to fall back to the
domain: the dialog requires at least one element, and a test already pins
"no elements left → no badges anywhere" for the edit path.

**Verification history, since it changed:** this was originally *not
reproducible* — the copy of the design in use had zero sequential
elements, so a retention strategy legitimately had nothing to retain
(which the app already handles with its "(No Sequential Elements)"
Properties warning), and the expansion had to be verified against
`examples/soc_with_ram` instead. Once the real `simple10` was found and
its `always_ff` made to elaborate (see the corrections above), the case
became directly demonstrable here: fixture 08 omits `-elements`, the
import expands it to `sram/u_sub/o_q`, and the badge renders.

### 17. `-update` was unrecognized, so two-step authoring failed ✅ fixed

*Fixture 06.* `-update` is real 1801: re-open a named strategy and refine
it — the successive-refinement methodology this elaborator's own
accumulate-then-freeze design already exists for. It simply was not wired
for UPF's three strategy commands, so a second
`set_isolation ISO_A -update ...` was rejected as a duplicate name. Now
merges via CPF's existing `apply_update()` rather than a second merge
implementation, keeping the first command's fields and adding the
second's. A `-update` on a name that does not exist yet is treated as a
plain creation, which a file split across several included pieces relies
on.

## Export after import

### 20. An unset flag was written as the literal text `None` ✅ fixed

Found by **re-exporting** the fixtures (`regenerate.py`) rather than by
loading them. Three flags in the UPF `set_isolation` writer
(`-clamp_value`, `-isolation_sense`, `-location`) and two in the Level
Shifter one (`-applies_to`, `-rule`) were emitted unconditionally, so an
unset value became the literal string `None` — which **parses cleanly**
and comes back as the string `'None'`. Silent corruption, not a visible
failure. The CPF writer had the same shape in two more places.

Invisible to both other suites, and the clearest argument for this third
one: a strategy created through the dialog always has those fields set,
so the round trip never saw it, and the import stops before generation,
so `validate_fixture.py` never did. Only "load a real file, then export
what you got" reaches it.

Every regenerated fixture now re-elaborates cleanly, pinned by
`test_an_imported_fixture_re_exports_to_something_that_elaborates`.

## Non-findings, recorded so they are not re-investigated

### 18. `set_level_shifter_cell` with a positional name — correctly rejected

My own fixture first wrote `set_level_shifter_cell LS_OUT -cell_name ...`,
putting the rule name positionally by analogy with its siblings
(`set_isolation`/`set_retention`/`set_level_shifter` all take it that
way). `-level_shifter` is a flag per the 1801 reference. The elaborator
rejected it with a precise message naming the command and the offending
token — correct behaviour. **No tolerance for non-conformant syntax was
invented.**

### 19. Ground-edge placement and macro PG-pin colour — correct already

The importer leaves every supply at `edge="top"`, which looked wrong for
a ground rail. Verified empirically through the real `PowerIntentTab`
rather than from the docstring: the scene-build pass owns this, and a
Ground-role supply snaps to `edge="bottom"` with `ground_auto_anchored`
set, while macro PG pins take the colour of whatever the imported
connector wired them to. Both are now pinned by a test that drives the
real tab, so the correctness is no longer incidental.

---

## Coverage against the required surface

| Feature | Fixtures | Verified |
| --- | --- | --- |
| Power Domains — single | 01, 04, 06, 07 | ✅ |
| Power Domains — multi-hierarchy | 03 | ✅ |
| Supply Ports / Nets / Sets | all | ✅ |
| Primary + Secondary supply sets | 02, 05 | ✅ |
| A set shared across domains | 05 | ✅ |
| N-Well / P-Well | 06 | ✅ |
| Power Switches | 02, 05, 07 | ✅ |
| Isolation | 02, 04, 06, 07 | ✅ |
| Retention | 02, 03, 07, 08 | ✅ |
| Level Shifter | 03, 04 | ✅ |
| Macro PG pins + internal PG pin | 02 | ✅ |
| Macro power-intent attachment | 03 | ✅ |
| Hierarchical flatten vs. attach | 03 | ✅ |
| Power States (per supply) | 01, 02, 05 | ✅ |
| Power State Table / system modes | 05 (UPF), 04 (CPF, reported) | ✅ |
| Port Attributes | 03 | ✅ |
| Colour derivation chain | all (property test) | ✅ |
| ERROR classification | 07 | ✅ |
| WARNING classification | 02, 04 | ✅ |

## Known remaining gaps

Not defects found here, but visible from this work and worth recording:

- **A CPF power mode does not reach the Power State Table** (finding 14)
  — reported, not silently dropped, because the per-domain → per-net
  conversion has no honest general form.
- **`backend/mapping/` is still wrong about six reference fields** — it
  resolves them as RTL hierarchy paths relative to the construct's own
  scope, which is not what they mean. The importer filters them
  per-field (`_BUILDER_OWNED_FIELDS`), so anything reading
  `MappedDesign.references` directly will hit the same false `DANGLING`
  classifications. See `docs/milestone2_powerintent_elaborator.md`.
- **A supply port's `-direction` is not modelled**, so every fixture
  declaring one produces a warning. Correct per the supported-features
  list, but it is the most frequent warning in practice.
