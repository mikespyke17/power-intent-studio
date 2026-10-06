"""shared/mock_backend/ -- pure data, no Qt dependency, but still no
backend/ import anywhere (checked structurally, not just by convention:
see test_no_backend_imports below). This mock-data infrastructure is the
front-end test suite's own standing dependency, independent of which
real backend wiring the app's entry point happens to use -- see
docs/backend_integration.md."""

import ast
from pathlib import Path

from frontend.src.shared.mock_backend import (
    build_mock_design,
    build_mock_mapped_design,
    build_mock_power_intent,
    build_mock_power_intent_diagnostics,
    build_mock_rtl_diagnostics,
    build_mock_validation_report,
    finding_to_diagnostic,
)
from frontend.src.shared.models.mapping import ResolutionKind
from frontend.src.shared.models.validation import Severity


def test_mock_design_is_a_real_tree_walkable_via_walk():
    design = build_mock_design()
    paths = [n.path for n in design.walk()]
    assert paths == ["top", "top/u_core", "top/u_dbg", "top/u_ram", "top/u_bus", "top/u_fwd"]


def test_mock_power_intent_includes_a_domain_with_no_real_rtl_target():
    power_intent = build_mock_power_intent()
    assert power_intent.domains["::PD_EMPTY"].elements == ["u_nonexistent"]


def test_mock_mapped_design_flags_the_empty_domain_as_dangling():
    rtl = build_mock_design()
    power_intent = build_mock_power_intent()
    mapped = build_mock_mapped_design(power_intent, rtl)

    empty_refs = [r for r in mapped.references if r.location.construct_id == "::PD_EMPTY"]
    assert len(empty_refs) == 1
    assert empty_refs[0].kind == ResolutionKind.DANGLING


def test_validation_report_deduplicates_not_attempted_by_field_not_instance():
    rtl = build_mock_design()
    power_intent = build_mock_power_intent()
    mapped = build_mock_mapped_design(power_intent, rtl)
    report = build_mock_validation_report(mapped)

    excluded = [f for f in report.findings if f.severity == Severity.EXCLUDED]
    applies_to_finding = next(f for f in excluded if f.summary.startswith("PowerState.applies_to"))
    assert applies_to_finding.count == 2  # both SS1.ON and SS1.OFF set applies_to


def test_finding_to_diagnostic_never_has_file_or_line():
    rtl = build_mock_design()
    power_intent = build_mock_power_intent()
    mapped = build_mock_mapped_design(power_intent, rtl)
    report = build_mock_validation_report(mapped)

    for finding in report.findings:
        diagnostic = finding_to_diagnostic(finding)
        assert diagnostic.file is None
        assert diagnostic.line is None
        assert diagnostic.source == "mapping"


def test_power_intent_diagnostics_always_have_a_file_but_sometimes_lack_a_line():
    diagnostics = build_mock_power_intent_diagnostics()
    assert all(d.file is not None for d in diagnostics)
    assert any(d.line is not None for d in diagnostics)
    assert any(d.line is None for d in diagnostics)


def test_rtl_diagnostics_always_have_both_file_and_line():
    diagnostics = build_mock_rtl_diagnostics()
    assert all(d.file is not None and d.line is not None for d in diagnostics)


def _backend_import_names(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            candidates = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            candidates = [node.module] if node.module else []
        else:
            continue
        names.extend(n for n in candidates if n is not None and (n == "backend" or n.startswith("backend.")))
    return names


def test_no_backend_imports_outside_backend_integration():
    """Structural enforcement of CLAUDE.md's milestone-independence rule,
    not just a convention: every .py file under frontend/src/ EXCEPT
    backend_integration/ (the one deliberate, documented exception -- see
    docs/backend_integration.md; formerly named validation_harness/, back
    when it wired a separate prototype entry point rather than the app's
    real, standard one) is parsed and checked for any import whose module
    path starts with 'backend'.

    Scope, recorded by the 2026-09-11 audit (category 01) so this test
    is not over-read: it checks IMPORT STATEMENTS, not runtime
    dependency. `app/main.py` now pulls in the whole backend
    transitively and passes, because the import it writes is a frontend
    one. That is correct by design -- backend_integration/ is the seam,
    and main.py going through it is the point -- but it means this test
    answers "who names backend directly", not "how much of the frontend
    runs without a backend installed".
    """
    frontend_src = Path(__file__).parent.parent.parent / "frontend" / "src"
    integration_dir = frontend_src / "backend_integration"
    offenders = []
    for path in frontend_src.rglob("*.py"):
        if integration_dir in path.parents:
            continue
        if _backend_import_names(path):
            offenders.append(str(path))
    assert offenders == []


def test_backend_integration_only_imports_backend_api_never_backend_internals():
    """The one sanctioned exception is narrower than "backend_integration/
    may import backend/" -- it may only reach backend.api (the seam),
    never backend internals (backend.elaborator/backend.mapping/
    backend.validation directly). Checked structurally, same discipline
    as the blanket rule above, not left to reviewer attention.
    """
    integration_dir = Path(__file__).parent.parent.parent / "frontend" / "src" / "backend_integration"
    offenders = {}
    for path in integration_dir.rglob("*.py"):
        bad = [n for n in _backend_import_names(path) if not (n == "backend.api" or n.startswith("backend.api."))]
        if bad:
            offenders[str(path)] = bad
    assert offenders == {}
