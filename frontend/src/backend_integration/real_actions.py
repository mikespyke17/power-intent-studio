"""Builds the injectable action callables LoadDesignTab/LoadPowerIntentTab
accept, wiring real backend.api calls through backend_adapter.py into the
shapes those tabs' signals already expect. This is the real-backend
wiring `frontend/src/app/main.py`'s own `main()` uses by default (see
docs/backend_integration.md) -- the app's single, standard entry point,
not a separate prototype anymore. `MainWindow`'s own bare constructor
still defaults every action to `None` (mock/fixture data), which is what
the rest of the front-end test suite continues to build against, per
CLAUDE.md's milestone-independence rule -- this module is just the one
place that wiring is assembled for real use.

`BackendState` exists because Mapping needs the *real* backend.api.Design/
PowerIntentDesign from the two Load steps, not the adapted shared/models/
versions the tabs themselves hold -- the two Load actions stash the real
objects here; the mapping action reads them back. One BackendState is
constructed per app session by the entry point and closed over by all
three actions built here.
"""

from __future__ import annotations

from typing import Optional

import backend.api as api

from frontend.src.backend_integration.backend_adapter import (
    adapt_design,
    adapt_mapped_design,
    adapt_power_intent,
    adapt_validation_report,
    error_to_diagnostic,
)
from frontend.src.shared.mock_backend.mock_mapping import finding_to_diagnostic
from frontend.src.shared.models.diagnostics import Diagnostic
from frontend.src.shared.models.mapping import MappedDesign as MockMappedDesign
from frontend.src.shared.models.power_intent import PowerIntentDesign as MockPowerIntentDesign
from frontend.src.shared.models.rtl import Design as MockDesign


class BackendState:
    def __init__(self) -> None:
        self.real_rtl: Optional[api.Design] = None
        self.real_power_intent: Optional[api.PowerIntentDesign] = None
        self.adapted_rtl: Optional[MockDesign] = None
        self.adapted_power_intent: Optional[MockPowerIntentDesign] = None


def make_elaborate_action(state: BackendState):
    def elaborate_action(
        rtl_files: list[str],
        lib_files: list[str],
        top: str,
        defines: Optional[dict[str, Optional[str]]] = None,
        include_dirs: Optional[list[str]] = None,
    ) -> tuple[Optional[MockDesign], list[Diagnostic]]:
        if not rtl_files:
            return None, [error_to_diagnostic(ValueError("no RTL files given"), source="load_design", code="NO-RTL-FILES")]
        try:
            # `defines`/`include_dirs` come from the Load Design tab's
            # own "DEFINE:" field and any loaded filelist, already
            # combined (manual wins) -- see LoadDesignTab.current_defines().
            real_design = api.elaborate_rtl(
                rtl_files, lib_files, top or None, defines=defines, include_dirs=include_dirs
            )
        except Exception as exc:  # noqa: BLE001 -- coarse error surfacing is the accepted scope, see backend/api/'s docstring
            return None, [error_to_diagnostic(exc, source="load_design", code="RTL-ELABORATE-ERROR")]

        state.real_rtl = real_design
        state.adapted_rtl = adapt_design(real_design)
        return state.adapted_rtl, []

    return elaborate_action


def make_load_power_intent_action(state: BackendState):
    def load_action(file: str, source_format: str) -> tuple[Optional[MockPowerIntentDesign], list[Diagnostic]]:
        try:
            real_power_intent = api.elaborate_power_intent(file, source_format)  # type: ignore[arg-type]
        except Exception as exc:  # noqa: BLE001
            return None, [error_to_diagnostic(exc, source="load_power_intent", code="POWER-INTENT-ELABORATE-ERROR")]

        state.real_power_intent = real_power_intent
        state.adapted_power_intent = adapt_power_intent(real_power_intent)
        return state.adapted_power_intent, []

    return load_action


def make_mapping_action(state: BackendState):
    def mapping_action() -> tuple[Optional[MockMappedDesign], list[Diagnostic]]:
        if state.real_rtl is None or state.real_power_intent is None:
            return None, [error_to_diagnostic(RuntimeError("Load Design and Load Power Intent must both succeed before Mapping"), source="mapping", code="MAPPING-NOT-READY")]
        try:
            real_mapped = api.run_mapping(state.real_power_intent, state.real_rtl)
            real_report = api.run_validation(real_mapped)
        except Exception as exc:  # noqa: BLE001
            return None, [error_to_diagnostic(exc, source="mapping", code="MAPPING-ERROR")]

        adapted_mapped = adapt_mapped_design(real_mapped, state.adapted_power_intent, state.adapted_rtl)
        adapted_report = adapt_validation_report(real_report)
        diagnostics = [finding_to_diagnostic(f) for f in adapted_report.findings]
        return adapted_mapped, diagnostics

    return mapping_action
