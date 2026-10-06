"""hierarchy_view/properties_table_widget.py -- the tabbed Properties
panel (PortList/Sequential), against mock RTL data only."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDockWidget, QGroupBox, QHeaderView

from frontend.src.hierarchy_view.properties_table_widget import ConnectivityCellDisplay, HierarchyPropertiesTableWidget
from frontend.src.hierarchy_view.selection_controller import HierarchySelectionController
from frontend.src.hierarchy_view.visualize_design_tab import VisualizeDesignTab
from frontend.src.shared.mock_backend.mock_rtl import build_mock_design


def _table_rows(table):
    return [[table.item(r, c).text() if table.item(r, c) is not None else None for c in range(table.columnCount())] for r in range(table.rowCount())]


def _widget(qtbot):
    controller = HierarchySelectionController()
    widget = HierarchyPropertiesTableWidget(controller)
    qtbot.addWidget(widget)
    widget.load(build_mock_design())
    return widget


def test_tab_titles_are_portlist_sequential_elements(qtbot):
    """General was removed on explicit request -- only these two remain.
    "Sequential" was renamed "Sequential Elements" (explicit request),
    then back to "Sequential" (and "PortList" -> "Port List") on a
    later tab rename/reorder pass (explicit request)."""
    widget = _widget(qtbot)
    titles = [widget.tabText(i) for i in range(widget.count())]
    assert titles == ["Port List", "Sequential"]


def test_portlist_and_sequential_tables_resize_columns_to_content(qtbot):
    widget = _widget(qtbot)
    for table in (widget._port_panel.table, widget._sequential_panel.table):
        mode = table.horizontalHeader().sectionResizeMode(0)
        assert mode == QHeaderView.ResizeMode.ResizeToContents


def test_portlist_header_is_bold(qtbot):
    widget = _widget(qtbot)
    assert widget._port_panel.table.horizontalHeader().font().bold() is True


def test_portlist_and_sequential_tables_have_sorting_enabled(qtbot):
    """Explicit request: click-to-sort on every table."""
    widget = _widget(qtbot)
    assert widget._port_panel.table.isSortingEnabled() is True
    assert widget._sequential_panel.table.isSortingEnabled() is True


def test_portlist_and_sequential_filter_buttons_are_labeled_visualize(qtbot):
    """Explicit request: rename the "Filter" button to "Visualize" in
    Design Properties (functionality unchanged)."""
    widget = _widget(qtbot)
    assert widget._port_panel.filter_button.text() == "Visualize"
    assert widget._sequential_panel.filter_button.text() == "Visualize"


def test_portlist_table_has_word_wrap_enabled(qtbot):
    """Real, confirmed bug: a Receiver/Driver Supply Set cell can hold
    several newline-joined lines (see power_resolution_cache.py's own
    "\\n".join() in _resolve_receiver() -- e.g. a macro pin fanning out
    to both the design's own top-level boundary AND real internal RTL,
    golden fixture simple8) -- without word wrap, Qt's default item
    delegate elides the cell to a single, truncated line instead of
    rendering each embedded "\\n" as its own line. Checks the flag
    directly, not just the resolved string's own content (the DATA side
    was already correct before this fix; this test protects the
    RENDERING side specifically, so this exact bug class can't silently
    return)."""
    widget = _widget(qtbot)
    assert widget._port_panel.table.wordWrap() is True


def test_portlist_table_grows_row_height_for_a_multi_line_cell(qtbot):
    """The other half of the same fix: word wrap alone doesn't
    retroactively grow a row's height for wrapped content -- confirms
    resizeRowsToContents() is actually called after every
    set_connectivity()-driven repopulation, by feeding a genuinely
    multi-line Receiver cell and checking the row grew taller than an
    ordinary single-line row, not just that word wrap is turned on."""
    widget = _widget(qtbot)
    widget.apply_selection({"top"})
    multi_line = ConnectivityCellDisplay("External - No Port Attribute Associated\nSS_VDD_SUB", italic=False)
    single_line = ConnectivityCellDisplay("SS_VDD", italic=False)
    widget.set_connectivity(
        {("top", "clk"): multi_line, ("top", "sleep_n"): single_line},
        {},
    )

    table = widget._port_panel.table
    row_by_name = {table.item(r, 0).text(): r for r in range(table.rowCount())}
    multi_line_row = table.rowHeight(row_by_name["clk"])
    single_line_row = table.rowHeight(row_by_name["sleep_n"])
    assert multi_line_row > single_line_row


def test_portlist_and_sequential_filter_buttons_default_to_all(qtbot):
    """Explicit request: add an "All" option, default-selected."""
    widget = _widget(qtbot)
    assert widget._port_panel.filter_button._all_action.isChecked() is True
    assert widget._sequential_panel.filter_button._all_action.isChecked() is True


def test_portlist_dir_and_width_are_centered(qtbot):
    widget = _widget(qtbot)
    widget.apply_selection({"top/u_core"})
    table = widget._port_panel.table

    for row in range(table.rowCount()):
        assert int(table.item(row, 1).textAlignment()) & Qt.AlignmentFlag.AlignCenter
        assert int(table.item(row, 2).textAlignment()) & Qt.AlignmentFlag.AlignCenter


def test_portlist_excludes_pg_and_pg_internal_for_a_macro(qtbot):
    """Explicit request: a macro's own PG/PG_INTERNAL pins are excluded
    from PortList entirely -- they're already shown on the Power Intent
    tab's own Supply Ports tab (properties_panel.py). Only the macro's
    genuine functional ports (CE/DOUT) remain."""
    widget = _widget(qtbot)

    widget.apply_selection({"top/u_ram"})

    rows = _table_rows(widget._port_panel.table)
    names = {row[0] for row in rows}
    assert "VDD" not in names
    assert "VSS" not in names
    assert names == {"CE", "DOUT"}


def test_portlist_sorting_is_enabled(qtbot):
    widget = _widget(qtbot)
    assert widget._port_panel.table.isSortingEnabled() is True


def test_portlist_filter_defaults_to_showing_every_port(qtbot):
    widget = _widget(qtbot)
    widget.apply_selection({"top/u_core"})
    # clk, out1, out2, out3 (plain ports) + bus.data, bus.valid (the
    # interface-typed "bus" port's exposed signals, via its "consumer" modport)
    assert widget._port_panel.table.rowCount() == 6


def test_portlist_shows_interface_port_signals_named_localname_dot_signal(qtbot):
    widget = _widget(qtbot)
    widget.apply_selection({"top/u_core"})

    rows = _table_rows(widget._port_panel.table)
    names = {row[0] for row in rows}
    assert {"bus.data", "bus.valid"} <= names
    # Not the interface *type*'s base name -- the local port name ("bus"),
    # since a module could have two ports of the same interface type.
    assert "simple_bus_if.data" not in names


def test_portlist_interface_port_direction_comes_from_its_bound_modport(qtbot):
    """core.bus is bound to simple_bus_if's "consumer" modport, which
    declares both data and valid as inputs -- must reflect the modport's
    view, not some other (e.g. the interface's raw declared) direction."""
    widget = _widget(qtbot)
    widget.apply_selection({"top/u_core"})

    rows = _table_rows(widget._port_panel.table)
    by_name = {row[0]: row[1] for row in rows}
    assert by_name["bus.data"] == "IN"
    assert by_name["bus.valid"] == "IN"


def test_portlist_interface_port_width_resolves_from_the_interface_definition(qtbot):
    widget = _widget(qtbot)
    widget.apply_selection({"top/u_core"})

    rows = _table_rows(widget._port_panel.table)
    by_name = {row[0]: row[2] for row in rows}
    assert by_name["bus.data"] == "8"
    assert by_name["bus.valid"] == "1"


def test_portlist_unconstrained_interface_port_uses_inferred_modport_for_direction(qtbot):
    """fwd.bus has no modport at its own declaration (modport=None) but
    build_mock_design() sets inferred_modport="producer" directly,
    standing in for what the real backend's forwarded-modport inference
    would compute -- direction must come from that modport ("producer":
    data/valid both OUT), not fall back to the raw-INOUT case."""
    widget = _widget(qtbot)
    widget.apply_selection({"top/u_fwd"})

    rows = _table_rows(widget._port_panel.table)
    by_name = {row[0]: row[1] for row in rows}
    assert by_name["bus.data"] == "OUT"
    assert by_name["bus.valid"] == "OUT"


def test_portlist_filter_hides_a_deselected_category(qtbot):
    widget = _widget(qtbot)
    widget.apply_selection({"top/u_core"})

    widget._port_panel.filter_button._actions["Output"].setChecked(False)

    rows = _table_rows(widget._port_panel.table)
    names = [row[0] for row in rows]
    assert "clk" in names
    assert "out1" not in names and "out2" not in names and "out3" not in names


def test_portlist_has_its_own_text_filter_bar_alongside_the_category_filter(qtbot):
    """Explicit request: "a way to filter the tables" -- a free-text
    filter, complementary to (not a replacement for) the existing
    Direction category checkboxes."""
    widget = _widget(qtbot)
    widget.apply_selection({"top/u_core"})
    table = widget._port_panel.table

    widget._port_panel.table_filter.setText("clk")

    rows = range(table.rowCount())
    visible_names = [table.item(r, 0).text() for r in rows if not table.isRowHidden(r)]
    assert visible_names == ["clk"]


def test_portlist_text_filter_survives_a_reselection(qtbot):
    widget = _widget(qtbot)
    widget.apply_selection({"top/u_core"})
    widget._port_panel.table_filter.setText("nonexistent_xyz")
    table = widget._port_panel.table
    assert table.rowCount() > 0
    assert all(table.isRowHidden(r) for r in range(table.rowCount()))

    widget.apply_selection({"top/u_dbg"})  # re-triggers _refresh()

    assert table.rowCount() > 0
    assert all(table.isRowHidden(r) for r in range(table.rowCount()))


def test_sequential_tab_merges_register_and_latch_with_name_type(qtbot):
    """Explicit request: the Name cell shows "<scope>/<name>" -- e.g.
    "top/u_core/out1" -- consistent with how elements are displayed in
    the Retention Strategy dialog's own Elements list."""
    widget = _widget(qtbot)

    widget.apply_selection({"top/u_core"})

    rows = _table_rows(widget._sequential_panel.table)
    assert ["top/u_core/out1", "Register"] in rows
    assert ["top/u_core/internal_n1", "Latch"] in rows


def test_sequential_header_is_bold(qtbot):
    widget = _widget(qtbot)
    assert widget._sequential_panel.table.horizontalHeader().font().bold() is True


def test_sequential_filter_defaults_to_showing_both_register_and_latch(qtbot):
    widget = _widget(qtbot)
    widget.apply_selection({"top/u_core"})
    assert widget._sequential_panel.table.rowCount() == 2


def test_sequential_filter_can_show_registers_only(qtbot):
    widget = _widget(qtbot)
    widget.apply_selection({"top/u_core"})

    widget._sequential_panel.filter_button._actions["Latch"].setChecked(False)

    rows = _table_rows(widget._sequential_panel.table)
    assert rows == [["top/u_core/out1", "Register"]]


def test_sequential_path_label_shows_the_selected_nodes_scope(qtbot):
    widget = _widget(qtbot)
    widget.apply_selection({"top/u_core"})

    assert widget._sequential_panel.path_label.text() == "Path: <i>top/u_core</i>"


def test_sequential_has_its_own_text_filter_bar(qtbot):
    widget = _widget(qtbot)
    widget.apply_selection({"top/u_core"})
    table = widget._sequential_panel.table

    widget._sequential_panel.table_filter.setText("*internal_n1")  # wildcard -- the cell text is now scope-prefixed

    rows = range(table.rowCount())
    visible_names = [table.item(r, 0).text() for r in rows if not table.isRowHidden(r)]
    assert visible_names == ["top/u_core/internal_n1"]


def test_selecting_a_node_with_no_sequential_elements_clears_the_sequential_table(qtbot):
    widget = _widget(qtbot)
    widget.apply_selection({"top/u_dbg"})
    assert widget._sequential_panel.table.rowCount() == 0


def test_sequential_tab_is_recursive_over_the_whole_selected_subtree(qtbot):
    """Selecting 'top' (which owns no sequential elements itself) must
    still show u_core's register+latch, attributed to u_core's own path
    -- not just the selected node's immediate Definition."""
    widget = _widget(qtbot)

    widget.apply_selection({"top"})

    rows = _table_rows(widget._sequential_panel.table)
    assert ["top/u_core/out1", "Register"] in rows
    assert ["top/u_core/internal_n1", "Latch"] in rows


def test_sequential_tab_at_a_leaf_that_owns_the_elements_still_matches_direct_selection(qtbot):
    widget = _widget(qtbot)
    widget.apply_selection({"top"})
    from_top = _table_rows(widget._sequential_panel.table)

    widget.apply_selection({"top/u_core"})
    from_core = _table_rows(widget._sequential_panel.table)

    assert from_top == from_core  # u_core is top's only source of sequential elements


# --- multi-select union (Ctrl+click selects more than one node) --------


def test_portlist_path_label_shows_the_selected_nodes_scope(qtbot):
    """No per-row Path column anymore (explicit request) -- a single
    "Path: <scope>" label above the table names the selected node(s)
    instead, italic."""
    widget = _widget(qtbot)
    widget.apply_selection({"top/u_core"})

    assert widget._port_panel.path_label.text() == "Path: <i>top/u_core</i>"


def test_portlist_path_label_joins_every_selected_node_for_a_multi_selection(qtbot):
    widget = _widget(qtbot)
    widget.apply_selection({"top/u_core", "top/u_dbg"})

    assert widget._port_panel.path_label.text() == "Path: <i>top/u_core, top/u_dbg</i>"


def test_portlist_path_label_shows_a_dash_for_no_selection(qtbot):
    widget = _widget(qtbot)
    widget.apply_selection({"top/u_core"})

    widget.apply_selection(frozenset())

    assert widget._port_panel.path_label.text() == "Path: <i>-</i>"


def test_portlist_unions_ports_from_every_selected_node(qtbot):
    """top/u_core and top/u_dbg both happen to have a port literally
    named "clk" -- both rows must appear (no longer disambiguated by a
    per-row Path column, just present twice), not merged/deduplicated
    into one."""
    widget = _widget(qtbot)

    widget.apply_selection({"top/u_core", "top/u_dbg"})

    rows = _table_rows(widget._port_panel.table)
    names = [row[0] for row in rows]
    assert names.count("clk") == 2
    assert names.count("out1") == 1


def test_sequential_unions_elements_from_every_selected_node_without_duplicating_overlap(qtbot):
    """"top" and "top/u_core" selected together -- top/u_core is inside
    top's own subtree, so its elements must appear exactly once each,
    not twice just because two selected roots both cover it."""
    widget = _widget(qtbot)

    widget.apply_selection({"top", "top/u_core"})

    rows = _table_rows(widget._sequential_panel.table)
    assert rows.count(["top/u_core/out1", "Register"]) == 1
    assert rows.count(["top/u_core/internal_n1", "Latch"]) == 1


def test_empty_selection_clears_both_panels(qtbot):
    widget = _widget(qtbot)
    widget.apply_selection({"top/u_core"})
    assert widget._port_panel.table.rowCount() > 0

    widget.apply_selection(frozenset())

    assert widget._port_panel.table.rowCount() == 0
    assert widget._sequential_panel.table.rowCount() == 0


def test_visualize_design_tab_holds_a_diagram_group_box_plus_tree_and_properties_docks(qtbot):
    """Explicit request: Hierarchy Tree and Design Properties are
    QDockWidgets now (closable/reopenable, visible by default), not
    QGroupBoxes inside a QSplitter -- Design Diagram stays a QGroupBox,
    now as this tab's own central widget (this tab is a QMainWindow)."""
    tab = VisualizeDesignTab()
    qtbot.addWidget(tab)
    tab.show()

    assert isinstance(tab.centralWidget(), QGroupBox)
    assert tab.centralWidget().title() == "Design Diagram"

    assert isinstance(tab.tree_dock, QDockWidget)
    assert tab.tree_dock.windowTitle() == "Hierarchy Tree"
    assert tab.tree_dock.isVisible() is True
    assert isinstance(tab.properties_dock, QDockWidget)
    assert tab.properties_dock.windowTitle() == "Design Properties"
    assert tab.properties_dock.isVisible() is True


def test_visualize_design_docks_can_be_closed_and_reopened(qtbot):
    """"So user can close or open" -- explicit request."""
    tab = VisualizeDesignTab()
    qtbot.addWidget(tab)
    tab.show()

    tab.tree_dock.close()
    assert tab.tree_dock.isVisible() is False
    assert tab.properties_dock.isVisible() is True  # the other dock is unaffected

    tab.tree_dock.toggleViewAction().trigger()  # the same action the View menu uses
    assert tab.tree_dock.isVisible() is True
