"""QAbstractTableModel over list[Diagnostic] for the diagnostics dock.
Columns: Severity, Code, Message, File, Line -- File/Line render as
blank, not "None" or "0", when absent (see Diagnostic's own docstring
for the real reasons a diagnostic can lack either)."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt

from frontend.src.shared.models.diagnostics import Diagnostic
from frontend.src.shared.models.validation import Severity

_HEADERS = ["Severity", "Code", "Message", "File", "Line"]

_SEVERITY_LABEL = {
    Severity.ERROR: "Error",
    Severity.WARNING: "Warning",
    Severity.EXCLUDED: "Excluded",
}


class DiagnosticsTableModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._all_diagnostics: list[Diagnostic] = []
        self._visible_sources: set[str] = set()

    def set_diagnostics(self, source: str, diagnostics: list[Diagnostic]) -> None:
        """Replaces every diagnostic previously recorded for `source`
        with `diagnostics` -- each mocked action (Elaborate, Load Power
        Intent, Mapping) re-runs from scratch, so its diagnostics should
        replace the prior run's, not accumulate across repeated clicks."""
        self.beginResetModel()
        self._all_diagnostics = [d for d in self._all_diagnostics if d.source != source] + diagnostics
        self.endResetModel()

    def set_visible_sources(self, sources: set[str]) -> None:
        self.beginResetModel()
        self._visible_sources = sources
        self.endResetModel()

    def _visible(self) -> list[Diagnostic]:
        return [d for d in self._all_diagnostics if d.source in self._visible_sources]

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return len(self._visible())

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return len(_HEADERS)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return _HEADERS[section]
        return None

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        diagnostic = self._visible()[index.row()]
        column = index.column()
        if column == 0:
            return _SEVERITY_LABEL[diagnostic.severity]
        if column == 1:
            return diagnostic.code
        if column == 2:
            return diagnostic.message
        if column == 3:
            return diagnostic.file or ""
        if column == 4:
            return "" if diagnostic.line is None else str(diagnostic.line)
        return None
