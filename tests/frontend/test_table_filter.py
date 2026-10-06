"""shared/widgets/table_filter.py -- the generic text-search filter bar
used across Design Properties and Power Intent Properties (explicit
request: "in all tabs I would like to have a way to filter the
tables"), its "Apply to" column selector, and its glob-style matching
(explicit request: exact match by default, `*wildcards*` for substring)."""

from PySide6.QtWidgets import QTableWidget, QTableWidgetItem

from frontend.src.shared.widgets.table_filter import TableFilterBar, wrap_with_filter


def _table(rows: list[tuple[str, str]], headers: tuple[str, str] = ("Name", "Extra")) -> QTableWidget:
    table = QTableWidget(len(rows), 2)
    table.setHorizontalHeaderLabels(list(headers))
    for row, (a, b) in enumerate(rows):
        table.setItem(row, 0, QTableWidgetItem(a))
        table.setItem(row, 1, QTableWidgetItem(b))
    return table


def test_empty_filter_shows_every_row(qtbot):
    table = _table([("Alpha", "foo"), ("Beta", "bar")])
    filter_bar = TableFilterBar(table)
    qtbot.addWidget(filter_bar)

    assert [table.isRowHidden(r) for r in range(2)] == [False, False]


def test_plain_text_requires_an_exact_case_insensitive_match(qtbot):
    """Explicit request: a bare "test" must only match a cell reading
    exactly "test" -- not "latest" or "testing" -- so a query with no
    real match empties the table rather than falling back to substring
    matching."""
    table = _table([("Alpha", "foo"), ("Beta", "foobar")])
    filter_bar = TableFilterBar(table)
    qtbot.addWidget(filter_bar)

    filter_bar.setText("foo")

    assert table.isRowHidden(0) is False  # column 1 reads exactly "foo"
    assert table.isRowHidden(1) is True  # "foobar" != "foo"


def test_plain_text_with_no_exact_match_hides_every_row(qtbot):
    table = _table([("Alpha", "foobar"), ("Beta", "barfoo")])
    filter_bar = TableFilterBar(table)
    qtbot.addWidget(filter_bar)

    filter_bar.setText("foo")

    assert [table.isRowHidden(r) for r in range(2)] == [True, True]


def test_wildcard_star_matches_a_substring_anywhere(qtbot):
    table = _table([("Alpha", "foo"), ("Beta", "bar"), ("Gamma", "foobar")])
    filter_bar = TableFilterBar(table)
    qtbot.addWidget(filter_bar)

    filter_bar.setText("*foo*")

    assert table.isRowHidden(0) is False  # "foo" contains "foo"
    assert table.isRowHidden(1) is True  # no "foo" anywhere
    assert table.isRowHidden(2) is False  # "foobar" contains "foo"


def test_filter_matches_the_first_column_too(qtbot):
    table = _table([("Alpha", "foo"), ("Beta", "bar")])
    filter_bar = TableFilterBar(table)
    qtbot.addWidget(filter_bar)

    filter_bar.setText("alpha")

    assert table.isRowHidden(0) is False
    assert table.isRowHidden(1) is True


def test_clearing_the_filter_shows_every_row_again(qtbot):
    table = _table([("Alpha", "foo"), ("Beta", "bar")])
    filter_bar = TableFilterBar(table)
    qtbot.addWidget(filter_bar)
    filter_bar.setText("alpha")

    filter_bar.setText("")

    assert [table.isRowHidden(r) for r in range(2)] == [False, False]


def test_apply_to_defaults_to_all_columns(qtbot):
    table = _table([("Alpha", "foo"), ("Beta", "bar")])
    filter_bar = TableFilterBar(table)
    qtbot.addWidget(filter_bar)

    assert filter_bar._column_combo.currentText() == "All Columns"


def test_apply_to_a_specific_column_ignores_matches_in_other_columns(qtbot):
    """"Extra" reads "foo" on row 0 and "Alpha" is the row-0 Name -- with
    "Apply to" narrowed to "Name", searching "foo" must find nothing even
    though it's a real (exact) match in the *other* column."""
    table = _table([("Alpha", "foo"), ("foo", "bar")])
    filter_bar = TableFilterBar(table)
    qtbot.addWidget(filter_bar)

    filter_bar.set_column("Name")
    filter_bar.setText("foo")

    assert table.isRowHidden(0) is True  # "Alpha" != "foo"
    assert table.isRowHidden(1) is False  # Name column *is* "foo"


def test_apply_to_back_to_all_columns_restores_default_behavior(qtbot):
    table = _table([("Alpha", "foo"), ("foo", "bar")])
    filter_bar = TableFilterBar(table)
    qtbot.addWidget(filter_bar)
    filter_bar.set_column("Name")

    filter_bar.set_column(None)
    filter_bar.setText("foo")

    assert table.isRowHidden(0) is False  # "Extra" column matches
    assert table.isRowHidden(1) is False  # "Name" column matches


def test_reapply_reasserts_the_filter_after_rows_are_repopulated(qtbot):
    """Explicit reason this method exists: repopulating a table's rows
    (setRowCount()/setItem(), the way every properties panel refreshes
    itself) doesn't reliably preserve which rows a filter had hidden."""
    table = _table([("Alpha", "foo"), ("Beta", "bar"), ("Gamma", "foobar")])
    filter_bar = TableFilterBar(table)
    qtbot.addWidget(filter_bar)
    filter_bar.setText("Beta")
    assert table.isRowHidden(1) is False

    table.setRowCount(0)
    table.setRowCount(3)
    table.setItem(0, 0, QTableWidgetItem("Alpha"))
    table.setItem(1, 0, QTableWidgetItem("Beta"))
    table.setItem(2, 0, QTableWidgetItem("Gamma"))

    filter_bar.reapply()

    assert table.isRowHidden(0) is True
    assert table.isRowHidden(1) is False
    assert table.isRowHidden(2) is True


def test_sorting_the_table_re_asserts_the_filter(qtbot):
    """setRowHidden() is tracked by the view at a fixed row index --
    sorting reorders which content lives at which index, so without
    re-applying the filter afterward the hidden flags would end up
    attached to the wrong rows."""
    table = _table([("Charlie", "x"), ("Alpha", "x"), ("Bravo", "x")])
    table.setSortingEnabled(True)
    filter_bar = TableFilterBar(table)
    qtbot.addWidget(filter_bar)
    filter_bar.setText("Alpha")

    table.sortByColumn(0, table.horizontalHeader().sortIndicatorOrder())

    visible = [table.item(r, 0).text() for r in range(table.rowCount()) if not table.isRowHidden(r)]
    assert visible == ["Alpha"]


def test_wrap_with_filter_returns_a_container_holding_the_bar_and_table(qtbot):
    table = _table([("Alpha", "foo")])
    container, filter_bar = wrap_with_filter(table)
    qtbot.addWidget(container)

    assert filter_bar.parent() is container
    assert table.parent() is container
