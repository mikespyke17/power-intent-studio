"""A small text-search bar that hides QTableWidget rows not matching as
the user types -- a generic, uniform filter usable on any QTableWidget
shape, unlike the category-checkbox FilterButton
(hierarchy_view/properties_table_widget.py) which needs a per-table
category definition (Direction, Register-vs-Latch, ...) to mean
anything. The two are complementary, not a replacement for each other --
PortList/Sequential keep their own FilterButton *and* gain one of these.

Two explicit-request refinements on top of the original plain-substring
version:
  - An "Apply to" QComboBox next to the text box picks which column the
    filter checks -- every real column by (0-based) index, plus a
    default "All Columns" entry reproducing the original any-column
    behavior.
  - Matching is glob-style (`fnmatch`), not a bare substring: a pattern
    with no wildcard character (`*`/`?`/`[...]`) requires an *exact*
    (case-insensitive) match against the cell's own text -- "test" only
    matches a cell reading exactly "test", not "latest" -- while
    `*test*` matches any cell containing "test" anywhere (the old
    default behavior, now opt-in via an explicit wildcard). fnmatch
    already implements exactly this: with no wildcard characters present
    it degenerates to a plain equality check.
"""

from __future__ import annotations

import fnmatch
from typing import Optional

from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QTableWidget, QVBoxLayout, QWidget

_ALL_COLUMNS = None  # QComboBox userData sentinel for the "All Columns" entry


def wildcard_match(text: str, pattern: str) -> bool:
    """Case-insensitive glob match (`*`/`?`/`[...]`, degenerating to an
    exact match when `pattern` has no wildcard character) -- the one
    matching predicate every filter widget in this app shares, so
    wildcard semantics stay identical everywhere rather than being
    reimplemented per widget type. TableFilterBar (QTableWidget rows)
    and ListFilterBar (QListWidget items, e.g. the Retention Strategy
    dialog's own Elements list) both call this instead of embedding
    their own fnmatch call."""
    return fnmatch.fnmatchcase(text.lower(), pattern.lower())


def _filter_placeholder(example: Optional[str]) -> str:
    """Placeholder text for a filter box.

    The wildcard convention is the part every filter shares and must
    always be shown; the EXAMPLE is per-table, because only the caller
    knows what a realistic name looks like in its own contents. Appended
    rather than substituted, so adding an example never costs a user the
    explanation of what `*` and `?` do.
    """
    return f"e.g. {example}  (wildcards: * ?)" if example else "Filter (wildcards: * ?)…"


class TableFilterBar(QWidget):
    """Filters `table`'s own rows live as the user types or changes the
    "Apply to" column. An empty filter shows every row."""

    def __init__(self, table: QTableWidget, parent: Optional[QWidget] = None, example: Optional[str] = None):
        super().__init__(parent)
        self._table = table

        self._line_edit = QLineEdit(self)
        self._line_edit.setPlaceholderText(_filter_placeholder(example))
        self._line_edit.textChanged.connect(self._apply)

        self._column_combo = QComboBox(self)
        self._column_combo.currentIndexChanged.connect(self._apply)
        self._populate_column_choices()

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._line_edit, 1)
        layout.addWidget(QLabel("Apply to:"))
        layout.addWidget(self._column_combo)

        # Sorting reorders which row a given cell's content lives in --
        # `setRowHidden()` is tracked by the view at a fixed row index, so
        # a sort performed after filtering can leave the hidden flags
        # applied to the wrong (now-different) rows unless the filter is
        # re-asserted afterward.
        table.horizontalHeader().sortIndicatorChanged.connect(lambda *_args: self._apply())

    def _populate_column_choices(self) -> None:
        previous = self._column_combo.currentData() if self._column_combo.count() else _ALL_COLUMNS
        self._column_combo.blockSignals(True)
        self._column_combo.clear()
        self._column_combo.addItem("All Columns", _ALL_COLUMNS)
        for col in range(self._table.columnCount()):
            header_item = self._table.horizontalHeaderItem(col)
            self._column_combo.addItem(header_item.text() if header_item is not None else str(col), col)
        restored = self._column_combo.findData(previous) if previous is not _ALL_COLUMNS else 0
        self._column_combo.setCurrentIndex(restored if restored >= 0 else 0)
        self._column_combo.blockSignals(False)

    def set_column(self, column_name: Optional[str]) -> None:
        """Selects "Apply to" by header text -- `None` for "All Columns".
        A name not found among the table's own headers is a silent no-op,
        matching QComboBox's own findText() semantics."""
        if column_name is None:
            self._column_combo.setCurrentIndex(0)
            return
        index = self._column_combo.findText(column_name)
        if index >= 0:
            self._column_combo.setCurrentIndex(index)

    def _cell_matches(self, row: int, col: int, pattern: str) -> bool:
        item = self._table.item(row, col)
        return item is not None and wildcard_match(item.text(), pattern)

    def _apply(self) -> None:
        pattern = self._line_edit.text().strip()
        column = self._column_combo.currentData()
        for row in range(self._table.rowCount()):
            if not pattern:
                self._table.setRowHidden(row, False)
                continue
            if column is _ALL_COLUMNS:
                match = any(self._cell_matches(row, col, pattern) for col in range(self._table.columnCount()))
            else:
                match = self._cell_matches(row, column, pattern)
            self._table.setRowHidden(row, not match)

    def reapply(self) -> None:
        """Re-applies the current filter text/column -- call after
        repopulating `table`'s own rows (setRowCount()/setItem()), which
        doesn't reliably preserve each row's own prior hidden state (rows
        beyond the old count start unhidden regardless; Qt's own
        retention for surviving row indices isn't guaranteed either), so
        a stale filter could otherwise silently stop applying to
        freshly-loaded rows. Also refreshes the "Apply to" choices, in
        case the table's own column count/headers ever change."""
        self._populate_column_choices()
        self._apply()

    # -- QLineEdit-shaped proxy, for callers/tests that just want to
    # drive the text box without reaching into _line_edit directly --
    def text(self) -> str:
        return self._line_edit.text()

    def setText(self, text: str) -> None:
        self._line_edit.setText(text)


class ListFilterBar(QWidget):
    """Same wildcard-matching text filter as TableFilterBar (explicit
    request: reuse the exact same matching logic, not reimplement it),
    adapted for a QListWidget -- no "Apply to column" picker, since a
    list item only ever has one line of text. First used by
    retention_dialog.py's own Elements list filter, kept here (not
    inlined there) so any future list-shaped filter in this app can
    reuse it the same way TableFilterBar already gets reused across
    every Properties table.

    Hides non-matching items via QListWidgetItem.setHidden() -- this
    never touches checkState(), so a filtered-out (hidden) item's own
    enabled/disabled checkbox state is completely unaffected by
    filtering, exactly as required."""

    def __init__(self, list_widget: QListWidget, parent: Optional[QWidget] = None, example: Optional[str] = None):
        super().__init__(parent)
        self._list_widget = list_widget

        self._line_edit = QLineEdit(self)
        self._line_edit.setPlaceholderText(_filter_placeholder(example))
        self._line_edit.textChanged.connect(self._apply)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._line_edit)

    def _apply(self) -> None:
        pattern = self._line_edit.text().strip()
        for i in range(self._list_widget.count()):
            item = self._list_widget.item(i)
            item.setHidden(bool(pattern) and not wildcard_match(item.text(), pattern))

    def reapply(self) -> None:
        """Re-applies the current filter text -- call after repopulating
        `list_widget`'s own items, same "doesn't reliably survive a
        repopulate on its own" precedent TableFilterBar.reapply() already
        documents."""
        self._apply()

    def text(self) -> str:
        return self._line_edit.text()

    def setText(self, text: str) -> None:
        self._line_edit.setText(text)


def wrap_with_filter(table: QTableWidget, example: Optional[str] = None) -> tuple[QWidget, TableFilterBar]:
    """A small container: the filter bar above `table`. Returns the
    container (add *this* to a layout/tab in `table`'s place) and the
    filter bar itself (call `.reapply()` on it after repopulating
    `table`'s rows).

    `example` is a realistic value for THIS table's own contents, e.g.
    "data_out*" -- see _filter_placeholder()."""
    filter_bar = TableFilterBar(table, example=example)
    container = QWidget()
    layout = QVBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.addWidget(filter_bar)
    layout.addWidget(table)
    return container, filter_bar
