# Backend Integration — the App's Real Backend Wiring

**Status: this is now `power_intent_studio.py`'s own single, standard
entry point** — not the fully-designed Final Milestone integration
CLAUDE.md describes (see that file's "Final Milestone — Back-End /
Front-End Integration" section). Originally built ahead of the Final
Milestone, on explicit request, as a separate
`power_intent_studio_validation_harness.py` prototype so real personal
RTL/UPF/CPF/.lib files could be run through what's been built so far via
the GUI — not just through `backend/`'s own test suite and
`examples/soc_with_ram/run_example.py`. That prototype has since been
**consolidated into the app itself**: there is only one executable entry
point now (`power_intent_studio.py`), and it always uses the real
backend wiring described here.

**This still isn't a claim that the Final Milestone's own deeper design
work is done.** The scope cuts below (no progress reporting, no format
auto-detection, no mixed-language RTL, coarse UPF/CPF/mapping errors)
were explicit, approved trade-offs for what was originally a
personal-validation prototype, and they're unchanged by the
consolidation — only the entry point moved from "a separate script you
had to know to run" to "the app's own default behavior." When the
Final Milestone's own deeper integration design happens, it can still
supersede this module's shape; the scope cuts named here are not
decisions already made for that design.

## Run it

```
python3 power_intent_studio.py
```

The single entry point — no separate harness script exists anymore.
Real RTL/.lib elaboration (pyslang/GHDL), real UPF/CPF elaboration, real
mapping, and real validation all run by default. See
`examples/aura_top/` for a design to try it against — a SystemVerilog SoC
with a filelist, `.lib` SRAM macros, generated UPF and a saved `.pis`
project, described in `docs/example_aura_top.md` (the smaller
`examples/sram/` is bundled too) — or use your own RTL/.lib/UPF/CPF
files. (This used to point at `examples/soc_with_ram`, which was curated
away; the historical mentions of it further down this document describe
what was verified at the time and are left as written.)

**Mock/fixture data hasn't gone anywhere** — `MainWindow`'s own bare
constructor (`MainWindow()`, no action parameters) still defaults every
action to mock/fixture data, unchanged. That's what the rest of the
front-end test suite (Front-End Milestones 1–3, hundreds of tests) is
still built and tested against, per CLAUDE.md's milestone-independence
rule — this consolidation only changed which wiring the app's own
executable entry point uses, not the mock code path itself, which
remains fully intact and independently useful for fast,
backend-independent frontend iteration.

## Scope, explicitly

**In scope:**
- Real file pickers on "Load Design" (Browse RTL/LIB Files, filtered to
  the expected extensions; staged picks committed to "Loaded" via "Add";
  top module, required) and "Load Power Intent" (UPF/CPF, plus an
  explicit UPF/CPF format pick).
- "Elaborate" → real RTL(+`.lib`) elaboration
  (`backend.elaborator.lib.elaborate_with_black_boxes`). Enables
  "Continue" on success (not on failure); the app doesn't navigate away
  from "Load Design" until "Continue" is clicked, so warnings from a
  successful-but-not-clean elaborate stay visible in the dock first —
  see `CLAUDE.md`'s Front-End Milestone 1 status for the full "Load
  Design" tab layout; this doc only covers the real-backend wiring
  behind it, not the tab's general UI design.
- "Load Power Intent" → real UPF/CPF elaboration.
- "Mapping" → real `map_design()` + `validate()`.
- RTL elaboration errors surface with real `File`/`Line` in the dock
  (`backend.elaborator.rtl.verilog.elaborate.ElaborationError`, using
  pyslang's own diagnostic-formatting machinery) -- fixed after a real
  reported bug: a missing comma in a port connection list originally
  produced a message naming only a bare pyslang diagnostic code, no
  file, no line, no description. UPF/CPF elaboration and mapping/
  validation errors remain coarse (message only, no file/line) -- no
  equivalent structured-location exception type exists for those yet,
  see "Errors: RTL is structured, UPF/CPF/mapping are still coarse"
  below.

**Explicitly out of scope, confirmed before implementation:**
- Progress callbacks / real progress reporting. The progress bar switches
  to Qt's built-in indeterminate/marquee mode while a real (synchronous)
  call runs, instead of the mock path's fake step-by-step animation —
  there's no discrete progress to report for a real call, and building
  fake steps for it would misrepresent what's actually happening. No
  `QThread` either — there's no real work happening in the mock path to
  justify one, and for the real path, a personal validation session on a
  real design was judged not to need background threading. Consequence,
  named directly: a large real design could visibly freeze the UI during
  elaboration.
- Resolving `shared/models/`-vs-conversion generally for every future
  consumer. The adapter (below) does a pragmatic field-by-field copy for
  this app's needs, not a general translation framework.
- Anything from Front-End Milestone 3 (editing) that hasn't otherwise
  landed independently of this consolidation.
- Mixed-language RTL. `elaborate_with_black_boxes` handles a
  single-language RTL design only (see its own module docstring); this
  wiring doesn't attempt to compose that with Milestone 1d's
  cross-language resolution. See "Mixed-language RTL: the exact failure
  mode" below for what actually happens if you hit this.
- Power-intent format auto-detection. No UPF/CPF sniffer exists in the
  backend today, and building one is new production logic beyond "wire
  together what's already built" — the app asks you to pick UPF or CPF
  explicitly instead.

## Architecture

```
backend/api/                              -- thin, frontend-agnostic facade
  elaborate_rtl(rtl_files, lib_files, top) -> Design
  elaborate_power_intent(file, format)      -> PowerIntentDesign
  run_mapping(power_intent, rtl)             -> MappedDesign
  run_validation(mapped)                      -> ValidationReport

frontend/src/backend_integration/          -- the ONE place frontend/ imports backend/
  backend_adapter.py    -- real backend.api types -> shared/models/ shapes
  real_actions.py         -- injectable action callables + BackendState

frontend/src/app/main.py                   -- build_main_window() wires
                                               BackendState + the three
                                               real_actions.py callables
                                               into MainWindow by default

power_intent_studio.py                     -- the single entry point
```

`backend/api/` returns the *real* backend dataclasses and re-exports
their types (`Design`, `PowerIntentDesign`, `MappedDesign`,
`ValidationReport`) from its own `__init__.py`, so nothing downstream
needs to reach past it into `backend.elaborator`/`backend.mapping`/
`backend.validation` internals directly. It imports nothing from
`frontend/` — completely front-end-agnostic, matching
`CLAUDE.md`'s "nothing outside `backend/api/` should be front-end-aware."

`frontend/src/backend_integration/backend_adapter.py` is the one place
in the entire frontend that imports `backend/` — and only
`backend.api`, never internals. Enforced structurally, not just by
convention: `tests/frontend/test_mock_backend.py`'s
`test_no_backend_imports_outside_backend_integration` (updated from the
blanket rule Front-End Milestones 1-2 established) confirms every other
frontend file still has zero backend imports, and
`test_backend_integration_only_imports_backend_api_never_backend_internals`
confirms this directory's own imports never reach past `backend.api`.
`MainWindow` itself takes the three action callables as optional
constructor parameters (unchanged by the consolidation) — it has no
special knowledge of `backend_integration/` at all; `frontend/src/app/main.py`'s
`build_main_window()` is the one place that wiring is assembled and
handed in.

## The adapter is a pragmatic subset copy, not a full mirror

`shared/models/` was authored (Front-End Milestone 2) as a *trimmed*
mirror of the real schemas — only the fields the mock-data widgets were
ever built to read, not an exhaustive 1:1 copy. The adapter does a
mechanical field-by-field copy for whatever fields exist on both shapes
and silently drops the rest. Two losses worth naming directly, not just
"some fields": `IsolationStrategy.exclude_elements` and
`LevelShifterStrategy.exclude_elements` are real RTL-hierarchy fields
`backend/mapping/` genuinely resolves, but `shared/models/power_intent.py`'s
mirrors have no field to hold the result, so those specific resolved
references are computed by the real pipeline and then discarded before
reaching the diagram. Everything else the widgets already display
(domains, switches, isolation/retention/level-shifter `elements`, power
states, supply ports/nets/sets, domain-supply connections) round-trips
correctly — confirmed against `examples/soc_with_ram`'s real elaborated
output, not just asserted (see Testing below).

## The adapter's own surface has grown since this doc was last kept current

**Documentation gap closed 2026-08-31** — this doc's own last substantive
update was 2026-08-01, but `backend_adapter.py`/`shared/models/rtl.py`
kept growing afterward as real Milestone-1 fixes needed new fields
forwarded through to the frontend. None of this changed the adapter's own
architecture (still a pragmatic, field-by-field subset copy, still the one
sanctioned seam) — recorded here so the field list above doesn't read as
frozen as of August 1st. Real additions since, each following the same
"backend schema gains a field → adapter forwards it → frontend mirror gains
it" pattern already established:

- `WellBiasPin` metadata and `PortDirection.PG_WELL` (implicit vs. exposed
  well-bias `pg_pin`s — see `docs/macro_supply_set_golden_fixtures.md` for
  the full writeup of what this enables in the GUI).
- `Definition.source_file` — existed on the real backend schema already,
  but was never actually forwarded through the adapter until "Inspect
  Source File" (a diagram context-menu action, see `CLAUDE.md`'s
  Front-End Milestone 3 entry) needed it — the same "field exists on the
  real schema, adapter silently never copied it" bug class this doc's own
  `IsolationStrategy.exclude_elements`/`LevelShifterStrategy.exclude_elements`
  gap (above) already named directly.
- `ContinuousAssign.referenced_signals` and `SequentialElement.data_ref` —
  needed so the frontend's own independent `connectivity_flatten.py` (see
  next section) could be built and kept structurally in sync with the real
  backend tracer.

## `flatten_design()` is not exposed through `backend/api/` at all

A real, current architectural gap, not a stale-doc issue: `backend/api/__init__.py`
re-exports exactly the 4 functions listed under Architecture above
(`elaborate_rtl`, `elaborate_power_intent`, `run_mapping`, `run_validation`)
— confirmed by reading the file directly. `backend.elaborator.rtl.ir.flatten`'s
own `flatten_design()`/`trace_driver()`/`trace_receivers()` (the real,
tested, backend-Milestone-1g end-to-end signal tracer — see
`docs/milestone1_rtl_elaborator.md`'s own 1g entry for the full writeup) are
not among them, and this adapter never calls into them.

**Practical consequence**: the "Driver Supply Set"/"Receiver Supply Set"
columns the Port List tab shows, even when running the real app against a
real elaborated design, are computed entirely by
`frontend/src/power_intent_view/connectivity_flatten.py` — a
hand-duplicated, independently-tested reimplementation of the same
algorithm operating on the *adapted* `shared/models/rtl.Design`, not by
calling the real backend tracer this wiring otherwise goes out of its way
to use for everything else (elaboration, mapping, validation). This
wiring's own "run real backend code, not a mock, wherever possible"
premise has one real, unaddressed exception. Worth resolving deliberately
(re-export the flatten functions from `backend/api/` and have the adapter
call them, the same shape every other real-backend action here already
follows) rather than leaving it as an accident of build order — logged
here, not fixed as part of this documentation pass.

## Bridging real and adapted state across three separate UI actions

`Mapping` needs the *real* `Design`/`PowerIntentDesign` (`map_design()`
operates on real backend types), not the adapted `shared/models/`
versions the tabs display. `real_actions.BackendState` — two attributes,
`real_rtl`/`real_power_intent`, plus their already-adapted counterparts
so `Mapping` doesn't re-convert data `Load Design`/`Load Power Intent`
already converted — is constructed once per app session by
`build_main_window()` and closed over by all three injected actions.

## Mixed-language RTL: the exact failure mode, confirmed not assumed

Handing the app a mixed Verilog+VHDL file set does **not** produce a
confusing or unrelated-looking failure. `elaborate_with_black_boxes`'s
own `_detect_rtl_language` raises before any elaboration is attempted:

> `elaborate_with_black_boxes handles a single-language RTL design only
> (all-Verilog/SV or all-VHDL) -- for a mixed-language design, resolve
> cross-language references via backend.elaborator.rtl.mixed_language.elaborate_mixed
> first (composing that with .lib resolution is deliberately deferred --
> see this module's docstring)`

The `elaborate_action` built by `real_actions.py` catches this (like any
other `ValueError` from elaboration) and surfaces it as a single `ERROR`
`Diagnostic` (code `RTL-ELABORATE-ERROR`) in the dock — the app does not
crash, the tabs stay disabled (no partial/broken `Design` reaches
`designLoaded`), and the message names the real fix. Confirmed live
through the actual `MainWindow` object graph — a real button click with
one real `.v` file and one real `.vhd` file — not just reasoned about
from reading `_detect_rtl_language`'s source; see
`test_mixed_language_rtl_surfaces_as_a_diagnostic_not_a_crash` in
`tests/frontend/test_real_backend_integration.py`.

## `elaborate_rtl(lib_files=[])` degrades correctly, confirmed not assumed

For a design with zero black-box instances, `elaborate_rtl(rtl_files,
lib_files=[], top)` must produce a `Design` identical to calling
`backend.elaborator.rtl.verilog.elaborate()` directly — not just "no
crash," a real equality check between the two `Design` objects.
`tests/backend/test_api.py`'s
`test_elaborate_rtl_with_no_lib_files_matches_plain_elaborate_exactly`
confirms this directly, against `verilog_three_level_hierarchy` (no
black-box instances).

## Errors: RTL is structured, UPF/CPF/mapping are still coarse

Found via real personal use, not a hypothetical: a missing comma in a
port connection list produced a dock row whose message was just
`"pyslang reported 1 error diagnostic(s): ['DiagCode(ExpectedToken)']"`
— no file, no line, no description of what was actually wrong. The root
cause was in `backend/elaborator/rtl/verilog/elaborate.py` itself, not
the frontend: it discarded pyslang's own rich diagnostic data and kept
only the bare diagnostic *code name*.

Fixed at the source with `ElaborationError(ValueError)` — a new
exception type carrying real `.file`/`.line` attributes from the
*first* offending diagnostic's actual source location
(`compilation.sourceManager.getFileName()`/`.getLineNumber()`), plus a
human-readable `str()` built from pyslang's own `DiagnosticEngine` +
`TextDiagnosticClient` (the same machinery a real pyslang-based CLI tool
uses) — not hand-rolled message text. For the reported bug, this now
reads:

```
examples/soc_with_ram/rtl/soc_top.v:23:32: error: expected ','
        .mem_rdata (data_rdata)
                               ^
```

`backend/api/` re-exports `ElaborationError` (alongside the real
dataclass types) so `backend_adapter.py`'s `error_to_diagnostic()` can
check `isinstance(exc, real.ElaborationError)` precisely — not duck-typed
via `getattr`, which would also match any unrelated exception that
happened to expose `.file`/`.line` by coincidence — and populate the
dock's `File`/`Line` columns from real data when the exception is one,
falling back to the coarse message-only shape otherwise.

**Only RTL elaboration got this treatment.** UPF/CPF elaboration errors
(Tcl `ValueError`s) and mapping/validation findings (`ResolvedReference`
carries no `SourceLoc` at all, confirmed back in Milestone 3 planning)
have no equivalent structured-location exception type — extending this
same fix to those would mean building real location-extraction logic for
each, which wasn't part of what was reported or asked for here. Logged as
the natural next follow-up if the same complaint comes up for UPF/CPF,
not attempted speculatively now.

## Testing

`tests/backend/test_api.py` — `backend/api/` against real golden
fixtures, both confirmations above plus UPF/CPF elaboration and a real
`map_design()`+`validate()` round trip.

`tests/frontend/test_real_backend_integration.py` (formerly
`test_validation_harness.py`) — the real-backend wiring's frontend half,
driven through real widget clicks on the actual `MainWindow` object
graph (not internal method calls), against `examples/soc_with_ram`'s
real files: the real actions run synchronously (no mock-timer delay);
the full pipeline produces the exact expected diagram-overlay entries
(domain, switch, isolation, level-shifter, retention all present);
mixed-language RTL and a real RTL syntax error (`golden_rtl/
verilog_syntax_error/`, confirming the dock's `File`/`Line` columns are
actually populated, not just the message) both surface as the expected
`Diagnostic`, not a crash; `MainWindow`'s own bare constructor still
defaults to mock actions
(`test_mainwindow_bare_constructor_still_defaults_to_mock_actions`); and
`frontend/src/app/main.py::build_main_window()` — the exact object graph
`power_intent_studio.py` launches — wires all three real actions and
produces a genuine, correctly-elaborated real design end to end
(`test_the_real_entry_point_wires_real_backend_actions`).

`tests/backend/test_verilog_elaborate.py`'s
`test_syntax_error_produces_a_real_message_with_structured_file_and_line`
covers `ElaborationError` itself, independent of the frontend.
