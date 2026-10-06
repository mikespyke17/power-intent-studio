# Macro / Driver-Receiver Supply Set golden fixture index

Reference index for the family of golden fixtures built around the Port
List's "Receiver Supply Set"/"Driver Supply Set" columns
(`frontend/src/power_intent_view/power_resolution_cache.py`) and the
`.lib` macro PG-pin/well-pin parsing that feeds it
(`backend/elaborator/lib/elaborate.py`). Every fixture here originates
from a hand-authored source design under `../design_examples/simpleN/`
(outside this repo) and is copied verbatim into a matching directory
under `tests/backend/golden_rtl/` (or, for `.lib`-only unit tests,
`tests/backend/golden_lib/`).

Kept up to date so a new session can cross-check "is the repo's copy of
this fixture still identical to the source the case was specified
against" and "which tests actually exercise it" without re-deriving
either by hand.

## How to re-verify sync (source vs. repo copy)

```bash
for pair in \
  "simple:verilog_pass_through_vs_real_logic" \
  "simple1:verilog_parent_level_real_logic_after_child" \
  "simple2:verilog_fanout_and_floating_output" \
  "simple3:verilog_macro_unconnected_pg_pins" \
  "simple4:verilog_macro_pg_pin_connectivity" \
  "simple5:verilog_macro_well_bias_validation" \
  "simple6:verilog_macro_exposed_well_pin" \
  "simple7:verilog_macro_exposed_well_pin_nwell" \
  "simple8:verilog_macro_boundary_and_internal_fanout"
do
  src="${pair%%:*}"; dst="${pair##*:}"
  for f in ../design_examples/$src/*; do
    fname=$(basename "$f")
    diff "$f" "tests/backend/golden_rtl/$dst/$fname" >/dev/null \
      && echo "$dst/$fname: identical" \
      || echo "$dst/$fname: DIFFERS -- re-copy from design_examples/$src/"
  done
done
```

If a diff ever fires, first check whether the differing attribute is
actually parsed anywhere in `backend/` (`grep -rn "<attribute_name>"
backend/`) before re-copying — several Liberty attributes in these
fixtures (e.g. `isolation_enable_condition`) are present for narrative/
realism reasons only and are never read by the elaborator, so a stray
edit to one of them doesn't invalidate the fixture's own test
assertions, just its byte-for-byte parity with the source.

## Fixture index

| `design_examples/` source | Repo golden fixture | Test file(s) | What it validates |
|---|---|---|---|
| `simple/` (`top.sv`/`sub0.sv`) | `tests/backend/golden_rtl/verilog_pass_through_vs_real_logic/` | `tests/frontend/test_power_resolution_cache.py` | Original pass-through-vs-real-logic golden case: pure wire assigns traced through, real logic (inverters) stopped at; cross-hierarchy driver/receiver resolution; real RTL logic with no Power Domain assigned resolves to `"No Supply Set"`. |
| `simple1/` (`top1.sv`/`sub1.sv`) | `tests/backend/golden_rtl/verilog_parent_level_real_logic_after_child/` | `tests/frontend/test_power_resolution_cache.py` | Real logic living in the PARENT hierarchy (not just a child) is correctly identified as a driver/receiver stopping point, in both trace directions. |
| `simple2/` (`top2.sv`/`sub21.sv`/`sub22.sv`) | `tests/backend/golden_rtl/verilog_fanout_and_floating_output/` | `tests/frontend/test_power_resolution_cache.py` | Fan-out to multiple receivers across *different* instances (not just different pins of one instance); a genuinely unconnected instance output resolves to `"Floating"`, distinct from an unattributed top-level boundary. |
| `simple3/` (`sram.v`/`sram.lib`) | `tests/backend/golden_rtl/verilog_macro_unconnected_pg_pins/` | `tests/frontend/test_power_resolution_cache.py` | A macro with NO power net connected to either PG pin (VDD/VSS, VDD_INT/VSS_INT) resolves to `"Macro - No Supply Set"` — with and without any Power Domain existing in the design at all. |
| `simple4/` (`sram.v`/`sram.lib`, adds `VSS_INT`) | `tests/backend/golden_rtl/verilog_macro_pg_pin_connectivity/` | `tests/frontend/test_power_resolution_cache.py` | Per-rail (power/ground independently) macro PG-pin connectivity: fully connected, power-only missing (`"Macro - Power Floating"`), ground-only missing (`"Macro - Ground Floating"`), crossed rails between two Supply Sets (`"Conflict - Supply Set"`, scoped per-PG-pin-pair not macro-instance-wide), and two Supply Sets *legally* sharing exactly one rail (no false conflict). |
| `simple5/` (`sram.v`/`sram.lib`, adds `VPB`/`VNB` well-bias pins) | `tests/backend/golden_rtl/verilog_macro_well_bias_validation/` | `tests/frontend/test_power_resolution_cache.py`, `tests/backend/test_lib_elaborate.py` (via `tests/backend/golden_lib/sram_macro_with_well_bias_pins.lib`) | IMPLICIT well-bias pg_pins (`pg_type` nwell/pwell, WITH `related_bias_pin`) are metadata only (`WellBiasPin`), never a connectable port; a resolved Supply Set whose own Nwell/Pwell doesn't match what the LIB implies gets `" - Bias Conflict"` appended (italic, warning-only, never blocking); scoped only to the PG domain the LIB actually ties a well pin to. |
| `simple6/` (`sram.v`/`sram.lib`, adds EXPOSED `VPW`, `VPB` stays implicit) | `tests/backend/golden_rtl/verilog_macro_exposed_well_pin/` | `tests/frontend/test_power_resolution_cache.py`, `tests/backend/test_lib_elaborate.py` (via the same `sram_macro_with_well_bias_pins.lib`, extended with `VPW`) | An EXPOSED well pin (nwell/pwell, NO `related_bias_pin`, own `voltage_name`, `physical_connection: device_layer`) becomes a real, independently connectable macro pin (`PortDirection.PG_WELL`), surfaced in the Supply Port Properties tab like any other PG pin; DEFAULT-domain bias check (no `related_bias_pin` to compare against) flags a conflict when it's wired to a Supply Set other than the macro's own primary (non-internal) PG domain. |
| `simple7/` (`sram.v`/`sram.lib`, mirrors `simple6` with `VNW`/`VPW` roles swapped: `VNW`/nwell now EXPOSED, `VPW`/pwell now implicit) | `tests/backend/golden_rtl/verilog_macro_exposed_well_pin_nwell/` | `tests/frontend/test_power_resolution_cache.py`, `tests/backend/test_lib_elaborate.py` (via the same `sram_macro_with_well_bias_pins.lib`, extended with a second `SRAM_WELL_BIAS_NWELL_EXPOSED` cell) | Confirms the exposed-well-pin implementation genuinely generalizes across BOTH well types (nwell and pwell) — verified needing ZERO code changes to pass, since the dispatch keys off `pg_type in _WELL_PG_TYPES` + presence/absence of `related_bias_pin`, never a hardcoded pin name or a pwell-only path. |
| `simple8/` (`sram.v`/`sub.sv`/`sram.lib` — sram instantiates BOTH the macro AND a real RTL module `sub`; `sram.lib` has no `VSS_INT` pg_pin at all, `SS_VDD_INT` shares `Ground=VSS` with `SS_VDD`) | `tests/backend/golden_rtl/verilog_macro_boundary_and_internal_fanout/` | `tests/frontend/test_power_resolution_cache.py` | A macro pin (`Q`) simultaneously driving BOTH the design's own top-level boundary port (sram's `q`) AND real internal RTL (`sub`'s inverter) in a different Power Domain. **Found and fixed a real bug**: the "top-level port's own Receiver never reports internal fanout" rule did not actually exist as a code path before this fixture — SRAM-level `q` and MACRO-level `Q` previously produced the *identical* combined multi-line result. Fixed in `_resolve_receiver()` — see that function's own docstring, including a documented first-attempt-was-wrong writeup (an unconditional top-level-port short-circuit broke 23 other tests before being narrowed to "queried endpoint is both top-level AND self-referential in its own `ReceiverResult.endpoints`"). Also confirms `sub`'s own genuinely-unconnected `o_q` resolves to `"Floating"`, never confused with the boundary marker. |


| `simple10/` (`sram.v`/`sub.sv`/`sram.lib` — `sram` instantiates the macro plus a real RTL module `sub` carrying one flop) | `tests/frontend/golden_simple10/design/` (**not** `golden_rtl/` — see the note below) | `tests/frontend/test_hand_authored_power_intent.py` | The hand-authored UPF/CPF import fixture family: 8 power-intent files written the way a designer or vendor would, which caught 20 findings the app's own round-trip suite structurally could not see. Full index and findings report in `tests/frontend/golden_simple10/README.md`. Unlike every row above, the design and its power intent live TOGETHER in one self-contained directory under `tests/frontend/`, because that suite needs the RTL, the `.lib` AND the fixtures resolved against each other — splitting them across `golden_rtl/` and a separate power-intent tree would have made the example unreadable. It also does not depend on the source tree being present at all: a deliberate change, made after the suite was found to SKIP rather than run whenever `../design_examples/simple10/` was absent. |
## Non-`golden_rtl` supporting fixtures

| File | Used by | Purpose |
|---|---|---|
| `tests/backend/golden_lib/sram_macro_with_well_bias_pins.lib` | `tests/backend/test_lib_elaborate.py` | Hand-authored (not copied from `design_examples/`) — direct backend unit tests for `WellBiasPin` parsing, quoted `related_bias_pin` unwrapping, and the exposed-well-pin `PortDirection.PG_WELL` path, independent of the full `elaborate_rtl` pipeline. |
| `tests/backend/golden_lib/unrecognized_pg_type.lib` | `tests/backend/test_lib_elaborate.py::test_unrecognized_pg_type_raises_clearly_rather_than_guessing` | Uses `pg_type: deepnwell` (updated from `nwell` once `nwell`/`pwell` became recognized, real pg_types) as the still-genuinely-unrecognized example. |

## Known, deliberately-inert source drift

`isolation_enable_condition` differs between some `design_examples/simpleN/sram.lib` revisions (e.g. `"re"` vs. `"ISO_EN"`) — never parsed anywhere in `backend/`, confirmed via `grep -rn "isolation_enable_condition" backend/` returning nothing. A diff on this attribute alone does not indicate a stale/broken fixture.
