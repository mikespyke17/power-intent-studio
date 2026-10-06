"""power_intent_view/properties_panel.py -- bold header rows and the
per-tab filter bar (explicit request: "in all tabs I would like to
have a way to filter the tables. Please use bold font for the first
row"), against mock/fixture data only."""

from PySide6.QtGui import QColor

from frontend.src.power_intent_view.power_intent_tab import PowerIntentTab
from frontend.src.power_intent_view.properties_panel import PowerIntentPropertiesPanel
from frontend.src.power_intent_view.selection_controller import PowerIntentSelectionController
from frontend.src.shared.mock_backend import build_mock_power_intent
from frontend.src.shared.mock_backend.mock_rtl import build_mock_design


def _panel(qtbot):
    panel = PowerIntentPropertiesPanel(PowerIntentSelectionController())
    qtbot.addWidget(panel)
    panel.load(build_mock_power_intent())
    return panel


def test_portlist_and_sequential_elements_are_the_first_two_tabs_with_a_default_controller_when_omitted(qtbot):
    """Explicit request: "Port List"/"Sequential" (renamed from
    "PortList"/"Sequential Elements" on a later tab rename/reorder pass)
    promoted as this panel's own first two top-level tabs (individually,
    not grouped under one "Design Properties" container tab).
    `hierarchy_controller` is optional -- every existing caller/test
    (like _panel() above) that never cared about hierarchy-node
    selection still works unmodified, getting a private, unshared
    HierarchySelectionController by default."""
    panel = _panel(qtbot)
    assert panel._tabs.tabText(0) == "Port List"
    assert panel._tabs.widget(0) is panel.design_properties._port_panel
    assert panel._tabs.tabText(1) == "Sequential"
    assert panel._tabs.widget(1) is panel.design_properties._sequential_panel


def test_tab_bar_does_not_expand_to_fill_the_available_width(qtbot):
    """Explicit request: tabs read flush-left/indented, not centered
    across the panel's own full width."""
    panel = _panel(qtbot)
    assert panel._tabs.tabBar().expanding() is False


def test_every_primary_tables_header_row_is_bold(qtbot):
    panel = _panel(qtbot)
    for kind, table in panel._tables.items():
        assert table.horizontalHeader().font().bold() is True, kind


def test_the_secondary_table_header_row_is_bold_too(qtbot):
    panel = _panel(qtbot)
    assert panel._domain_connections_table.horizontalHeader().font().bold() is True


def test_every_table_has_sorting_enabled(qtbot):
    """Explicit request: click-to-sort on every table."""
    panel = _panel(qtbot)
    for kind, table in panel._tables.items():
        assert table.isSortingEnabled() is True, kind
    assert panel._domain_connections_table.isSortingEnabled() is True


def test_every_table_auto_resizes_its_columns_to_content(qtbot):
    """Same ResizeToContents convention already established for PortList/
    Sequential Elements (hierarchy_view/properties_table_widget.py) --
    extended here to every construct-kind tab plus both secondary tables."""
    from PySide6.QtWidgets import QHeaderView

    panel = _panel(qtbot)
    for kind, table in panel._tables.items():
        header = table.horizontalHeader()
        for column in range(header.count()):
            assert header.sectionResizeMode(column) == QHeaderView.ResizeMode.ResizeToContents, kind
    header = panel._domain_connections_table.horizontalHeader()
    for column in range(header.count()):
        assert header.sectionResizeMode(column) == QHeaderView.ResizeMode.ResizeToContents


def test_apply_to_can_narrow_the_power_domains_filter_to_a_single_column(qtbot):
    """"Elements" (column 1) for the mock PowerDomain rows is "."
    (root-scope) never literally "PD_CORE" -- narrowing "Apply to" to
    "Elements" while searching "PD_CORE" must find nothing, even though
    the *Name* column (0) has an exact match, proving the column
    restriction is actually honored."""
    panel = _panel(qtbot)
    table = panel._tables["PowerDomain"]
    filter_bar = next(fb for fb in panel._filter_bars if fb._table is table)
    assert table.rowCount() > 1

    filter_bar.set_column("Elements")
    filter_bar.setText("PD_CORE")

    assert all(table.isRowHidden(r) for r in range(table.rowCount()))


def test_floating_pg_pin_connected_cell_is_italic(qtbot):
    """Explicit request: "if an internal supply port is floating, fill
    with 'Floating' in Italic font"."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view.add_supply_port(view._boxes["top/u_core"], "VDD", QColor("#ff0000"))  # triggers a rebuild -- PG pins materialize

    table = tab.properties._tables["SupplyPort"]
    # Connected is column 3 now -- the table gained a "Direction" column
    # between Name and Connected.
    floating_row = next(r for r in range(table.rowCount()) if table.item(r, 3).text() == "Floating")
    assert table.item(floating_row, 3).font().italic() is True


def test_states_column_shows_only_the_placeholder_when_a_supply_port_has_no_states(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view.add_supply_port(view._boxes["top"], "VDD", QColor("#ff0000"))

    table = tab.properties._tables["SupplyPort"]
    row = next(r for r in range(table.rowCount()) if table.item(r, 1) is not None and table.item(r, 1).text() == "VDD")
    assert table.item(row, 3) is not None and table.item(row, 3).text() == "-"
    assert table.cellWidget(row, 3) is None


def test_states_column_shows_only_the_real_states_with_no_leftover_placeholder(qtbot):
    """Bug fix: a QTableWidgetItem set at a cell is not automatically
    cleared by a later setCellWidget() at the same cell -- across two
    rebuilds where the same row index switches from "no states" (a
    plain "-" item) to "has states" (a cell widget), the stale item
    used to be left behind underneath the new widget."""
    from frontend.src.power_intent_view.properties_panel import _ElementsCellWidget
    from frontend.src.power_intent_view.supply_port_overlay import SupplyPortState

    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    port = view.add_supply_port(view._boxes["top"], "VDD", QColor("#ff0000"))
    table = tab.properties._tables["SupplyPort"]
    row = next(r for r in range(table.rowCount()) if table.item(r, 1) is not None and table.item(r, 1).text() == "VDD")
    assert table.item(row, 4).text() == "-"  # sanity: placeholder present before (States is column 4 now)

    view.edit_supply_port(port, "VDD", QColor("#ff0000"), states=[SupplyPortState("FULL_ON", "1.0")])

    table = tab.properties._tables["SupplyPort"]
    row = next(r for r in range(table.rowCount()) if table.item(r, 1) is not None and table.item(r, 1).text() == "VDD")
    assert table.item(row, 4) is None  # no leftover placeholder item
    widget = table.cellWidget(row, 4)
    assert isinstance(widget, _ElementsCellWidget)
    assert widget._elements == ["FULL_ON (1.0V)"]


def test_every_table_has_its_own_filter_bar(qtbot):
    panel = _panel(qtbot)
    # 7 primary tabs' tables + 1 secondary table (Domain-Supply
    # Connections) -- see class docstring. The standalone "Power States"
    # tab (and its own "Power State Groups" secondary table) was removed
    # (explicit request, superseded by the new "Power State Table"
    # top-level tab). The "Power Switch Cells"/"Level Shifter Cells"
    # secondary tables are gone entirely too (rework, explicit request --
    # REVERSES the earlier "Cell Type dropdown referencing a reusable
    # library" model).
    assert len(panel._filter_bars) == 8
    filtered_tables = {fb._table for fb in panel._filter_bars}
    assert set(panel._tables.values()) <= filtered_tables
    assert panel._domain_connections_table in filtered_tables


def test_filtering_the_power_domains_table_hides_non_matching_rows(qtbot):
    panel = _panel(qtbot)
    table = panel._tables["PowerDomain"]
    filter_bar = next(fb for fb in panel._filter_bars if fb._table is table)
    assert table.rowCount() > 1

    filter_bar.setText("PD_CORE")

    visible_rows = [r for r in range(table.rowCount()) if not table.isRowHidden(r)]
    assert len(visible_rows) >= 1
    assert all("PD_CORE" in table.item(r, 0).text() for r in visible_rows)


def test_filter_survives_a_reload(qtbot):
    panel = _panel(qtbot)
    table = panel._tables["PowerDomain"]
    filter_bar = next(fb for fb in panel._filter_bars if fb._table is table)
    filter_bar.setText("nonexistent_xyz")
    assert all(table.isRowHidden(r) for r in range(table.rowCount()))

    panel.load(build_mock_power_intent())  # re-triggers _rebuild()

    assert table.rowCount() > 0
    assert all(table.isRowHidden(r) for r in range(table.rowCount()))


def test_setselection_still_switches_tabs_and_selects_the_row_with_filter_bars_present(qtbot):
    """Regression lock: setSelection() used to call
    QTabWidget.setCurrentWidget(table), which silently no-op'd once each
    table stopped being the tab page's own direct child (now wrapped
    with its own filter bar) -- switched to setCurrentIndex()."""
    panel = _panel(qtbot)
    table = panel._tables["PowerSwitch"]
    switch_id = next(iter(build_mock_power_intent().switches))

    panel.setSelection("PowerSwitch", switch_id)

    assert panel._kind_for_tab[panel._tabs.currentIndex()] == "PowerSwitch"
    selected_rows = table.selectionModel().selectedRows()
    assert len(selected_rows) == 1


# --- Bug fix: States column cell widget text stays black on row selection ---


def test_states_column_text_turns_white_when_its_row_is_selected(qtbot):
    """Bug fix: a cell WIDGET (setCellWidget(), which is what the States
    column always uses) sits entirely outside Qt's own item-painting
    system, so it never picked up the palette's HighlightedText colour
    a selected row's other (plain-item) columns get automatically."""
    from PySide6.QtGui import QPalette

    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    from frontend.src.power_intent_view.supply_port_overlay import SupplyPortState

    view.add_supply_port(view._boxes["top"], "VDD", QColor("#ff0000"), states=[SupplyPortState("FULL_ON", "1.0")])

    table = tab.properties._tables["SupplyPort"]
    row = next(r for r in range(table.rowCount()) if table.item(r, 1) is not None and table.item(r, 1).text() == "VDD")
    widget = table.cellWidget(row, 4)  # States is column 4 now
    highlighted = table.palette().color(QPalette.ColorRole.HighlightedText).name()

    table.selectRow(row)

    assert widget._label.styleSheet() == f"color: {highlighted};"


# --- New feature: Escape deselects a selected Properties table row ----------


def test_escape_deselects_a_selected_row(qtbot):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QKeyEvent

    panel = _panel(qtbot)
    table = panel._tables["PowerSwitch"]
    table.selectRow(0)
    assert table.selectionModel().hasSelection()

    event = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
    table.keyPressEvent(event)

    assert not table.selectionModel().hasSelection()


def test_escape_with_no_selection_is_a_no_op(qtbot):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QKeyEvent

    panel = _panel(qtbot)
    table = panel._tables["PowerSwitch"]
    assert not table.selectionModel().hasSelection()

    event = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
    table.keyPressEvent(event)  # must not raise

    assert not table.selectionModel().hasSelection()


# --- Explicit request: tab rename + reorder --------------------------------


def test_tab_labels_and_order_match_the_specified_sequence(qtbot):
    """Explicit request: exact tab label text AND left-to-right order.
    "Switch Cell"/"Level Shifter Cell" are NOT separate tabs (they're
    secondary, in-tab section labels sharing "Switch"'s/"Level
    Shifter"'s own tab -- see properties_panel.py's own secondary_tables
    wiring), so they're checked separately below, not as entries in this
    top-level tab sequence. "Power States" (the final tab here) was
    REMOVED (explicit request, superseded by the new top-level "Power
    State Table" tab -- see main_window.py/power_state_table_tab.py)."""
    panel = _panel(qtbot)
    titles = [panel._tabs.tabText(i) for i in range(panel._tabs.count())]
    assert titles == [
        "Port List",
        "Sequential",
        "Supply Port",
        "Supply Set",
        "Power Domain",
        "Switch",
        "Isolation",
        "Retention",
        "Level Shifter",
    ]


def test_switch_and_level_shifter_tabs_have_no_secondary_cell_table(qtbot):
    """Rework (explicit request, REVERSES the earlier "Cell Type
    dropdown referencing a reusable library" model): the "Switch
    Cell"/"Level Shifter Cell" secondary tables/labels are gone
    entirely -- "Switch"/"Level Shifter" are plain, single-table tabs
    again."""
    panel = _panel(qtbot)
    switch_index = next(i for i, kind in panel._kind_for_tab.items() if kind == "PowerSwitch")
    level_shifter_index = next(i for i, kind in panel._kind_for_tab.items() if kind == "LevelShifterStrategy")

    from PySide6.QtWidgets import QLabel

    switch_tab_labels = [w.text() for w in panel._tabs.widget(switch_index).findChildren(QLabel)]
    level_shifter_tab_labels = [w.text() for w in panel._tabs.widget(level_shifter_index).findChildren(QLabel)]
    assert "Switch Cell" not in switch_tab_labels
    assert "Level Shifter Cell" not in level_shifter_tab_labels
    assert not hasattr(panel, "_power_switch_cells_table")
    assert not hasattr(panel, "_level_shifter_cells_table")
