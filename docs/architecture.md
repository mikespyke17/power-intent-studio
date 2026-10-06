# Architecture Notes

Status: placeholder — fill in as design decisions are made.

See [`product_workflow.md`](./product_workflow.md) for the user-facing
counterpart to this doc: architecture explains *how* the system is built;
product_workflow explains *why*, from the user's perspective — what they
actually do, end to end.

## Back end

- RTL sources: Verilog, SystemVerilog, VHDL — including mixed-language
  hierarchies. All languages normalize into a single internal representation
  (IR) in `backend/elaborator/rtl/ir/` before anything downstream (mapping,
  modification, API) touches them.
- UPF version(s) in scope: TBD — decide explicitly before deep-diving into
  Milestone 2.
- `backend/api/` is the only boundary the front end is allowed to depend on.

## Front end

- Built and tested against mock/fixture data — still true for the whole
  test suite, per CLAUDE.md's milestone-independence rule.
- No dependency on real back-end internals anywhere except the one
  sanctioned seam below.

## Backend integration (ahead of the fully-designed Final Milestone)

- The app's own executable entry point (`power_intent_studio.py` →
  `frontend/src/app/main.py::build_main_window()`) now wires the real
  backend by default via `frontend/src/backend_integration/` — the one
  place frontend/ imports `backend/`, and only `backend.api`. See
  `docs/backend_integration.md` for the full scope, architecture, and
  known gaps (no progress reporting, no format auto-detection, no
  mixed-language RTL, coarse UPF/CPF/mapping errors).
- `MainWindow`'s own bare constructor still defaults every action to
  mock/fixture data — that default, and the mock infrastructure behind
  it (`shared/mock_backend/`), is unchanged and still what the rest of
  the front-end test suite builds against.
- This is a real step toward the Final Milestone, not the Final
  Milestone itself: `backend/api/`'s own shape is still explicitly
  disposable (see CLAUDE.md's Final Milestone section), and none of the
  scope cuts above are decisions already made for that design.

## Integration (Final Milestone)

- Its own deeper design (progress reporting, format auto-detection,
  mixed-language RTL, structured UPF/CPF/mapping errors, and whatever
  `backend/api/`'s real, considered shape turns out to be) is still not
  designed — see CLAUDE.md.
- End-to-end tests already exist for the entry-point-level integration
  above (`tests/frontend/test_real_backend_integration.py`); this
  milestone is about the parts still explicitly out of scope for that.
