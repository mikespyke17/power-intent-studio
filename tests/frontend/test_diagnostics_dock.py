"""diagnostics/diagnostics_dock.py -- filtering by phase, the
replace-not-accumulate semantics, and blank rendering for absent
file/line against mock/fixture data."""

from frontend.src.diagnostics.diagnostics_dock import DiagnosticsDock
from frontend.src.shared.models.diagnostics import Diagnostic
from frontend.src.shared.models.validation import Severity


def _diag(source: str, file=None, line=None) -> Diagnostic:
    return Diagnostic(severity=Severity.WARNING, code="X", message="m", file=file, line=line, source=source)


def test_visualize_design_tab_shares_load_design_diagnostics(qtbot):
    dock = DiagnosticsDock()
    dock.set_diagnostics("load_design", [_diag("load_design")])

    dock.show_for_tab("load_design")
    assert dock.model.rowCount() == 1
    dock.show_for_tab("visualize_design")
    assert dock.model.rowCount() == 1


def test_power_intent_tab_shows_both_load_and_mapping_diagnostics(qtbot):
    dock = DiagnosticsDock()
    dock.set_diagnostics("load_power_intent", [_diag("load_power_intent")])
    dock.set_diagnostics("mapping", [_diag("mapping"), _diag("mapping")])

    dock.show_for_tab("power_intent")
    assert dock.model.rowCount() == 3
    dock.show_for_tab("load_design")
    assert dock.model.rowCount() == 0


def test_re_running_a_phase_replaces_not_accumulates(qtbot):
    dock = DiagnosticsDock()
    dock.set_diagnostics("mapping", [_diag("mapping"), _diag("mapping")])
    dock.set_diagnostics("mapping", [_diag("mapping")])  # e.g. re-clicking Mapping after a fix

    dock.show_for_tab("power_intent")
    assert dock.model.rowCount() == 1


def test_blank_file_and_line_render_as_empty_string_not_none_or_zero(qtbot):
    dock = DiagnosticsDock()
    dock.set_diagnostics("mapping", [_diag("mapping", file=None, line=None)])
    dock.show_for_tab("power_intent")

    from PySide6.QtCore import Qt

    index_file = dock.model.index(0, 3)
    index_line = dock.model.index(0, 4)
    assert dock.model.data(index_file, Qt.ItemDataRole.DisplayRole) == ""
    assert dock.model.data(index_line, Qt.ItemDataRole.DisplayRole) == ""


def test_columns_auto_resize_to_content_not_a_fixed_default_width(qtbot):
    """Bug fix (explicit request): column widths must auto-fit content,
    reusing the same ResizeToContents convention already established
    for the Properties tables -- confirms the header's own resize mode
    is set (not left at Qt's default, fixed "Interactive" width) for
    every column."""
    from PySide6.QtWidgets import QHeaderView

    dock = DiagnosticsDock()
    header = dock.table.horizontalHeader()
    for column in range(dock.model.columnCount()):
        assert header.sectionResizeMode(column) == QHeaderView.ResizeMode.ResizeToContents


def test_a_long_flagged_error_message_is_not_truncated(qtbot):
    """Bug fix (explicit request): when an error is flagged in a row,
    the full cell content must be visible/readable, not truncated --
    the underlying model already always returns the full text (see
    DiagnosticsTableModel.data()); this confirms the VIEW actually
    widens the column to fit it, rather than clipping it at some
    smaller default width."""
    dock = DiagnosticsDock()
    long_message = "A very long error message " * 10
    dock.set_diagnostics(
        "load_design",
        [Diagnostic(source="load_design", severity=Severity.ERROR, code="E001", message=long_message, file="foo.sv", line=1)],
    )
    dock.show_for_tab("load_design")

    message_column_width = dock.table.columnWidth(2)  # "Message" column
    # A column genuinely fit to this content must be substantially
    # wider than Qt's own small interactive default (~100px) -- not
    # asserting an exact pixel value (font-metric-dependent), just that
    # real auto-fit actually happened.
    assert message_column_width > 400
