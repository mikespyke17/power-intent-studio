"""backend/api/ -- the only seam CLAUDE.md's milestone-independence rule
allows the front end to depend on.

**Status: the real backend wiring `power_intent_studio.py` uses by
default, not the fully-designed Final Milestone integration.** Built
ahead of a fully-designed Final Milestone, on explicit request, so real
RTL/UPF/CPF/.lib files could be run through what's been built so far via
the GUI rather than only through `backend/`'s own test suite and example
scripts -- since promoted from a separate validation-harness prototype
into the app's single, standard entry point (see
docs/backend_integration.md), but its own scope is unchanged. This
module is intentionally minimal:

- No progress callbacks/incremental reporting -- callers get a result or
  an exception, nothing in between. The GUI shows an indeterminate
  progress bar while a call runs, not real progress.
- No format auto-detection for power intent -- `elaborate_power_intent`
  takes an explicit `source_format`, since no UPF/CPF sniffer exists in
  the backend today and building one is new production logic beyond
  "wire together what's already built."
- No mixed-language RTL support -- `elaborate_rtl` is exactly
  `backend.elaborator.lib.elaborate_with_black_boxes`, which handles a
  single-language RTL design only (see that module's own v1-scope note).
  A mixed Verilog+VHDL file set raises a clear, explicit error from
  `_detect_rtl_language` before any elaboration is attempted -- it is not
  silently mishandled or left to fail in a confusing way; see
  docs/backend_integration.md for the exact message and how it surfaces.
- Every function here returns/consumes the *real* backend dataclasses
  (`Design`, `PowerIntentDesign`, `MappedDesign`, `ValidationReport`,
  re-exported below). Converting those into the front end's own mirrored
  `shared/models/` shapes is the front end's job
  (`frontend/src/backend_integration/backend_adapter.py`) -- this module
  stays completely front-end-agnostic, importing nothing from
  `frontend/`.
- `elaborate_rtl`'s failures are the one exception to "coarse, message-
  only" errors: a real syntax bug (a missing comma in a port connection
  list) surfaced a genuine problem -- the original error only named a
  bare pyslang diagnostic code, no file, no line, no description. Fixed
  at the source (`backend/elaborator/rtl/verilog/elaborate.py`'s
  `ElaborationError`, re-exported below): real `.file`/`.line` from
  pyslang's own diagnostic location, plus a human-readable message using
  pyslang's own diagnostic-formatting machinery. UPF/CPF elaboration and
  mapping/validation errors remain coarse -- no equivalent structured-
  location exception type exists for those yet.

When the real Final Milestone integration is designed, it supersedes
this module -- this is a stepping stone, not a draft of it. Do not treat
its shape (or its scope cuts above) as decisions already made for that
design.
"""

from __future__ import annotations

from typing import Literal, Optional

from backend.elaborator.lib import elaborate_with_black_boxes
from backend.elaborator.power_intent.cpf import elaborate as _elaborate_cpf
from backend.elaborator.power_intent.ir import PowerIntentDesign
from backend.elaborator.power_intent.upf import elaborate as _elaborate_upf
from backend.elaborator.rtl.ir import Design
from backend.elaborator.rtl.verilog.elaborate import ElaborationError
from backend.mapping import MappedDesign, map_design
from backend.validation import ValidationReport, validate

__all__ = [
    "Design",
    "ElaborationError",
    "MappedDesign",
    "PowerIntentDesign",
    "ValidationReport",
    "elaborate_power_intent",
    "elaborate_rtl",
    "run_mapping",
    "run_validation",
]


def elaborate_rtl(
    rtl_files: list[str],
    lib_files: list[str],
    top: Optional[str] = None,
    defines: Optional[dict[str, Optional[str]]] = None,
    include_dirs: Optional[list[str]] = None,
) -> Design:
    """Elaborates `rtl_files` (single-language: all-Verilog/SV or all-VHDL
    -- see module docstring), resolving any unbound instances against the
    black-box macros declared in `lib_files` (may be empty). Raises
    ValueError on any real elaboration failure -- a bad RTL file, an
    unresolved reference not found in `lib_files` either, or a mixed
    Verilog+VHDL `rtl_files` list.

    `defines` (name -> value, None for a bare define) and `include_dirs`
    carry an RTL filelist's own `+define+`/`+incdir+`, and the Load
    Design tab's "DEFINE:" field. Both are Verilog/SV-only -- they are
    preprocessor concepts, and VHDL has no preprocessor -- so they are
    ignored for a VHDL design rather than faked into one.
    """
    return elaborate_with_black_boxes(
        rtl_files, lib_files, top=top, defines=defines, include_dirs=include_dirs
    )


def elaborate_power_intent(file: str, source_format: Literal["upf", "cpf"]) -> PowerIntentDesign:
    """Elaborates a single power-intent entry-point file. `source_format`
    must be given explicitly (see module docstring) -- pass the wrong one
    and the Tcl interpreter will fail on the first command it doesn't
    recognize for that format, surfacing as a ValueError same as any other
    real parse error.
    """
    if source_format == "upf":
        return _elaborate_upf(file)
    return _elaborate_cpf(file)


def run_mapping(power_intent: PowerIntentDesign, rtl: Design) -> MappedDesign:
    return map_design(power_intent, rtl)


def run_validation(mapped: MappedDesign) -> ValidationReport:
    return validate(mapped)
