"""Diagram-only supply ports/domains (Add Power Supply/Create Power
Domain -- see supply_port_overlay.py/power_domain_overlay.py) surfaced
in the Power Intent tab's Properties panel, alongside whatever real
PowerIntentDesign was separately loaded via Load Power Intent. See
properties_panel.py's own module docstring for the full "why" and the
two-independent-data-sources design. Exercised against mock RTL/mock
PowerIntentDesign only.
"""

from PySide6.QtGui import QColor

from frontend.src.power_intent_view.power_domain_overlay import PowerDomainPlacement, SupplySetPlacement
from frontend.src.power_intent_view.power_intent_tab import PowerIntentTab
from frontend.src.power_intent_view.properties_panel import PowerIntentPropertiesPanel, _hierarchy_prefixed_name, _prefixed_or_dash
from frontend.src.power_intent_view.retention_overlay import RetentionDiagramState, RetentionStrategyPlacement
from frontend.src.power_intent_view.selection_controller import PowerIntentSelectionController
from frontend.src.shared.mock_backend import build_mock_design, build_mock_power_intent
from frontend.src.shared.widgets import message_box


def _rows(table):
    return [[table.item(r, c).text() if table.item(r, c) is not None else None for c in range(table.columnCount())] for r in range(table.rowCount())]


# --- _hierarchy_prefixed_name() / _prefixed_or_dash() -------------------------


def test_hierarchy_prefixed_name_strips_the_root_segment():
    assert _hierarchy_prefixed_name("VDD", "top/u_dut") == "u_dut/VDD"
    assert _hierarchy_prefixed_name("VDD", "top/u_dut/u_sub") == "u_dut/u_sub/VDD"


def test_hierarchy_prefixed_name_top_level_has_no_prefix():
    """Explicit request: "For the top level supply ports or supply nets
    no need hierarchy simply the name." """
    assert _hierarchy_prefixed_name("VDD", "top") == "VDD"


def test_prefixed_or_dash_uses_a_dash_for_an_unset_field():
    assert _prefixed_or_dash(None, "top/u_dut") == "-"
    assert _prefixed_or_dash("", "top/u_dut") == "-"
    assert _prefixed_or_dash("VDD", "top/u_dut") == "u_dut/VDD"


# --- refresh_diagram_overlay(): Supply Ports table -----------------------------


def test_refresh_diagram_overlay_adds_a_supply_port_row(qtbot):
    """Type/Name/Connected (explicit request, renamed from Kind/Name/
    Scope/Direction-Voltage -- see properties_panel.py's own
    _load_supply_ports() docstring): a plain, non-fixed supply port is
    "top level" in this table's own sense -- nothing upstream of it to
    reference -- so its own Connected column is always "-"."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view

    view.add_supply_port(view._boxes["top/u_core"], "VDD", QColor("#ff0000"))

    rows = _rows(tab.properties._tables["SupplyPort"])
    matching = [r for r in rows if r[0] == "Supply Port"]
    assert len(matching) == 1
    assert matching[0][1] == "u_core/VDD"  # hierarchy-prefixed name
    assert matching[0][2] == "-"  # Connected


def test_refresh_diagram_overlay_top_level_supply_port_has_no_prefix(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view

    view.add_supply_port(view._boxes["top"], "VDD_ROOT", QColor("#ff0000"))

    rows = _rows(tab.properties._tables["SupplyPort"])
    matching = [r for r in rows if r[0] == "Supply Port"]
    assert matching[0][1] == "VDD_ROOT"


def test_refresh_diagram_overlay_includes_pg_pins(qtbot):
    """"Macro PG" (renamed from "PG Pin", explicit request) -- not
    connected to any plain supply port here, so Connected reads
    "Floating" (see test_power_intent_properties_panel.py for the
    italic-font assertion)."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view

    view.add_supply_port(view._boxes["top/u_core"], "VDD", QColor("#ff0000"))  # triggers a rebuild -- PG pins materialize

    rows = _rows(tab.properties._tables["SupplyPort"])
    pg_rows = [r for r in rows if r[0] == "Macro PG"]
    assert len(pg_rows) == 1
    assert pg_rows[0][1] == "u_ram/VDD"
    # Column 2 is "Direction" now -- Supply Port only, so a macro PG pin
    # shows blank there; "Connected" moved to column 3.
    assert pg_rows[0][2] == "-"
    assert pg_rows[0][3] == "Floating"


def test_supply_port_table_merges_real_and_diagram_only_rows(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    power_intent = build_mock_power_intent()
    tab.set_power_intent(power_intent)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view

    view.add_supply_port(view._boxes["top/u_core"], "VDD_DIAGRAM", QColor("#ff0000"))

    rows = _rows(tab.properties._tables["SupplyPort"])
    kinds = {r[0] for r in rows}
    assert "Port" in kinds  # real, from power_intent.supply_ports
    assert "Net" in kinds  # real, from power_intent.supply_nets
    assert "Supply Port" in kinds  # diagram-only


def test_pg_internal_pin_shows_as_macro_pg_internal_type(qtbot):
    """ram_macro's own VSS pin is PortDirection.PG_INTERNAL in the mock
    fixture -- PG_INTERNAL support added on explicit request (previously
    these pins didn't materialize as icons at all)."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view.add_supply_port(view._boxes["top/u_core"], "VDD", QColor("#ff0000"))  # triggers a rebuild -- PG pins materialize

    rows = _rows(tab.properties._tables["SupplyPort"])
    vss_row = next(r for r in rows if r[1] == "u_ram/VSS")
    assert vss_row[0] == "Macro PG - Internal"


def test_supply_port_connected_column_shows_the_master_when_wired(qtbot):
    """Explicit request: a macro PG pin's own "Connected" column names
    the plain supply port it's wired to via a drawn connector."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    net_icon = view.add_supply_port(view._boxes["top"], "VDD", QColor("#ff0000"))
    pg_icon = next(i for i in view._supply_port_icons.values() if i.owner_box.path == "top/u_ram" and i.name == "VDD")

    assert view.try_connect_supply_ports(net_icon, pg_icon) is True

    rows = _rows(tab.properties._tables["SupplyPort"])
    pg_row = next(r for r in rows if r[0] == "Macro PG" and r[1] == "u_ram/VDD")
    assert pg_row[3] == "VDD"  # Connected is column 3 now


# --- refresh_diagram_overlay(): Supply Sets table ------------------------------


def test_refresh_diagram_overlay_adds_a_supply_set_row_with_bare_fields(qtbot):
    """Fix 3B (explicit request): the Supply Set tab shows bare names
    only -- no hierarchy-scope prefix, on any column."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    new_set = SupplySetPlacement(id="s1", name="SS_VDD", power="VDD", ground="VSS", n_well="VDD", p_well="VSS", owner_path="top/u_core")

    view.create_power_domain(view._boxes["top/u_core"], "PD_CORE", QColor("#4caf50"), 0.5, "s1", [], {"s1": new_set})

    rows = _rows(tab.properties._tables["SupplySet"])
    matching = [r for r in rows if r[0] == "SS_VDD"]
    assert len(matching) == 1
    assert matching[0] == ["SS_VDD", "VDD", "VSS", "VDD", "VSS"]


def test_supply_set_table_shows_a_dash_for_unset_well_fields(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    new_set = SupplySetPlacement(id="s1", name="SS_MINIMAL", power="VDD", ground="VSS", owner_path="top")

    view.create_power_domain(view._boxes["top"], "PD_TOP", QColor("#4caf50"), 0.5, "s1", [], {"s1": new_set})

    rows = _rows(tab.properties._tables["SupplySet"])
    matching = [r for r in rows if r[0] == "SS_MINIMAL"]
    assert matching[0] == ["SS_MINIMAL", "VDD", "VSS", "-", "-"]


def test_supply_set_table_shows_a_dash_for_a_real_sets_n_well_and_p_well(qtbot):
    """The real backend SupplySet schema has no N-Well/P-Well fields at
    all (only power_net/ground_net) -- confirmed against
    frontend/src/shared/models/power_intent.py's own SupplySet."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_power_intent(build_mock_power_intent())

    rows = _rows(tab.properties._tables["SupplySet"])
    assert len(rows) == 1
    assert rows[0][3] == "-"  # N-Well
    assert rows[0][4] == "-"  # P-Well


# --- refresh_diagram_overlay(): Power Domains table ----------------------------


def test_refresh_diagram_overlay_adds_a_domain_row(qtbot):
    """Name/Elements/Exclude Elements/Primary Supply/Secondary Supplies
    (explicit request, no more dedicated Scope column -- see
    properties_panel.py's own _load_domains() docstring): scope now
    folds into "Elements" instead, root-relative ("u_core", not the raw
    "top/u_core" owner_path). Fix 3A (follow-up explicit request): the
    Name column itself is now the BARE domain name, no hierarchy-scope
    prefix (domain names are already unique diagram-wide, so a prefix
    was never load-bearing)."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view

    view.create_power_domain(view._boxes["top/u_core"], "PD_CORE", QColor("#4caf50"), 0.5, None, [], {})

    rows = _rows(tab.properties._tables["PowerDomain"])
    matching = [r for r in rows if r[0] == "PD_CORE"]
    assert len(matching) == 1
    assert matching[0][1] == "u_core"  # Elements


def test_domain_row_shows_the_bare_primary_supply_name(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    new_set = SupplySetPlacement(id="s1", name="SS_VDD", power="VDD", ground="VSS", owner_path="top/u_core")

    view.create_power_domain(view._boxes["top/u_core"], "PD_CORE", QColor("#4caf50"), 0.5, "s1", [], {"s1": new_set})

    rows = _rows(tab.properties._tables["PowerDomain"])
    matching = [r for r in rows if r[0] == "PD_CORE"]
    assert matching[0][3] == "SS_VDD"  # Primary Supply column, bare name


def test_domain_table_merges_real_and_diagram_only_rows(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    power_intent = build_mock_power_intent()
    tab.set_power_intent(power_intent)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view

    view.create_power_domain(view._boxes["top/u_core"], "PD_DIAGRAM", QColor("#4caf50"), 0.5, None, [], {})

    rows = _rows(tab.properties._tables["PowerDomain"])
    names = {r[0] for r in rows}
    assert "PD_CORE" in names  # real
    assert "PD_DIAGRAM" in names  # diagram-only -- also bare, no prefix


def test_domain_row_lists_its_secondary_supplies(qtbot):
    """Explicit request: a new "Secondary Supplies" column, bare names,
    one per line (multi-line cell) when there's more than one."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    primary = SupplySetPlacement(id="s1", name="SS_PRIMARY", power="VDD", owner_path="top/u_core")
    secondary = SupplySetPlacement(id="s2", name="SS_SECONDARY", power="VDD_ALT", owner_path="top/u_core")

    view.create_power_domain(view._boxes["top/u_core"], "PD_CORE", QColor("#4caf50"), 0.5, "s1", ["s2"], {"s1": primary, "s2": secondary})

    rows = _rows(tab.properties._tables["PowerDomain"])
    matching = next(r for r in rows if r[0] == "PD_CORE")
    assert matching[4] == "SS_SECONDARY"  # Secondary Supplies, bare name


def test_domain_row_lists_multiple_secondary_supplies_one_per_line(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    primary = SupplySetPlacement(id="s1", name="SS_PRIMARY", power="VDD", owner_path="top/u_core")
    sec_a = SupplySetPlacement(id="s2", name="SS_SEC_A", power="VDD_A", owner_path="top/u_core")
    sec_b = SupplySetPlacement(id="s3", name="SS_SEC_B", power="VDD_B", owner_path="top/u_core")

    view.create_power_domain(
        view._boxes["top/u_core"], "PD_CORE", QColor("#4caf50"), 0.5, "s1", ["s2", "s3"], {"s1": primary, "s2": sec_a, "s3": sec_b}
    )

    rows = _rows(tab.properties._tables["PowerDomain"])
    matching = next(r for r in rows if r[0] == "PD_CORE")
    assert matching[4] == "SS_SEC_A\nSS_SEC_B"


def test_domain_excludes_a_nested_domain_directly_beneath_it(qtbot):
    """Explicit request: "when creating a power domain for a
    sub-hierarchy of a hierarchy for which a power domain is created,
    this sub-hierarchy should be excluded" -- mirrors
    power_domain_overlay.py's own apply_domain_coloring() cascade."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view

    view.create_power_domain(view._boxes["top"], "PD_TOP", QColor("#4caf50"), 0.5, None, [], {})
    view.create_power_domain(view._boxes["top/u_core"], "PD_CORE", QColor("#2196f3"), 0.5, None, [], {})

    rows = _rows(tab.properties._tables["PowerDomain"])
    top_row = next(r for r in rows if r[0] == "PD_TOP")
    assert top_row[2] == "u_core"  # Exclude Elements
    core_row = next(r for r in rows if r[0] == "PD_CORE")
    assert core_row[2] == ""  # nothing nested beneath PD_CORE itself


def test_several_excluded_hierarchies_are_listed_one_per_line(qtbot):
    """Explicit request: Exclude Elements shows one entry per line in the
    cell, not one comma-separated line."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view

    view.create_power_domain(view._boxes["top"], "PD_TOP", QColor("#4caf50"), 0.5, None, [], {})
    view.create_power_domain(view._boxes["top/u_core"], "PD_CORE", QColor("#2196f3"), 0.5, None, [], {})
    view.create_power_domain(view._boxes["top/u_dbg"], "PD_DBG", QColor("#ff9800"), 0.5, None, [], {})

    rows = _rows(tab.properties._tables["PowerDomain"])
    top_row = next(r for r in rows if r[0] == "PD_TOP")
    assert top_row[2] == "u_core\nu_dbg"
    assert "," not in top_row[2]


# --- either data source may be missing -----------------------------------------


def test_refresh_diagram_overlay_works_before_load_power_intent_ever_ran(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view

    view.add_supply_port(view._boxes["top"], "VDD", QColor("#ff0000"))  # no set_power_intent() call at all

    rows = _rows(tab.properties._tables["SupplyPort"])
    assert any(r[0] == "Supply Port" for r in rows)


def test_load_alone_shows_only_real_rows_no_diagram_state_yet():
    """properties.load() called directly, refresh_diagram_overlay()
    never called -- must show real data only, no crash from a missing
    diagram state."""
    panel = PowerIntentPropertiesPanel(PowerIntentSelectionController())
    power_intent = build_mock_power_intent()

    panel.load(power_intent)

    assert panel._tables["SupplyPort"].rowCount() == len(power_intent.supply_ports) + len(power_intent.supply_nets)
    assert panel._tables["SupplySet"].rowCount() == len(power_intent.supply_sets)
    assert panel._tables["PowerDomain"].rowCount() == len(power_intent.domains)


# --- PowerIntentDiagramView.diagramStateChanged --------------------------------


def test_diagram_state_changed_fires_on_a_direct_mutation(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    fired = []
    tab.diagram.diagramStateChanged.connect(lambda: fired.append(1))

    view.add_supply_port(view._boxes["top/u_core"], "VDD", QColor("#ff0000"))

    assert len(fired) == 1


def test_diagram_state_changed_fires_on_undo_and_redo(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view.add_supply_port(view._boxes["top/u_core"], "VDD", QColor("#ff0000"))
    fired = []
    tab.diagram.diagramStateChanged.connect(lambda: fired.append(1))

    tab.undo_shortcut.activated.emit()
    assert len(fired) == 1

    tab.redo_shortcut.activated.emit()
    assert len(fired) == 2


def test_diagram_state_changed_does_not_fire_on_a_true_no_op(qtbot):
    """add_supply_port() raising ValueError (invalid name -- CHANGE 1
    removed the duplicate-name rejection this test originally used, so
    it now exercises the still-enforced "no spaces" rejection instead)
    never reaches the commit step at all -- no notification for a
    rejected action."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view.add_supply_port(view._boxes["top/u_core"], "VDD", QColor("#ff0000"))
    fired = []
    tab.diagram.diagramStateChanged.connect(lambda: fired.append(1))

    try:
        view.add_supply_port(view._boxes["top/u_core"], "VDD BAD", QColor("#00ff00"))  # space in name -- raises
    except ValueError:
        pass

    assert fired == []


def test_diagram_state_changed_created_in_a_sub_hierarchy_tab_still_refreshes_properties(qtbot):
    """A domain created while looking at a sub-module's own diagram tab
    (see HierarchyDiagramTabs) still reaches the Properties panel, since
    every tab's view shares the same notify_changed callback."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    tab.diagram.open_path("top/u_core")
    sub_view = tab.diagram._tabs.current_view

    sub_view.create_power_domain(sub_view._boxes["top/u_core"], "PD_SUB", QColor("#4caf50"), 0.5, None, [], {})

    rows = _rows(tab.properties._tables["PowerDomain"])
    assert any(r[0] == "PD_SUB" for r in rows)


# --- multi-hierarchy Power Domain: one row, multi-line Elements cell --------


def test_multi_hierarchy_domain_row_lists_every_member_hierarchy_on_its_own_line(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box_core = view._boxes["top/u_core"]
    box_dbg = view._boxes["top/u_dbg"]

    view.create_power_domain(box_core, "PD_MULTI", QColor("#4caf50"), 0.5, None, [], {}, additional_boxes=[box_dbg])

    rows = _rows(tab.properties._tables["PowerDomain"])
    # Still ONE row for the whole domain, not one row per member. Fix
    # 3A: Name is now the bare domain name, no hierarchy-scope prefix.
    matching = [r for r in rows if r[0] == "PD_MULTI"]
    assert len(matching) == 1
    assert matching[0][1] == "u_core\nu_dbg"  # Elements, multi-line


def test_multi_hierarchy_domain_exclude_elements_lists_only_the_nested_member(qtbot):
    """A domain nested under ONE of a multi-hierarchy domain's own
    members is excluded from that member's own coverage -- the other,
    unrelated member is untouched."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box_core = view._boxes["top/u_core"]
    box_dbg = view._boxes["top/u_dbg"]

    view.create_power_domain(box_core, "PD_MULTI", QColor("#4caf50"), 0.5, None, [], {}, additional_boxes=[box_dbg])
    view.create_power_domain(view._boxes["top"], "PD_TOP", QColor("#2196f3"), 0.5, None, [], {})

    rows = _rows(tab.properties._tables["PowerDomain"])
    top_row = next(r for r in rows if r[0] == "PD_TOP")
    assert top_row[2] == "u_core\nu_dbg"  # Exclude Elements: both members of PD_MULTI, one per line


# --- multi-hierarchy Supply Set: one row, multi-line per-member cells -------


def test_multi_hierarchy_supply_set_produces_exactly_one_bare_row(qtbot):
    """Fix 3B (explicit request): a Supply Set shared by a multi-
    hierarchy Power Domain is REPLICATED across every member hierarchy
    on the DIAGRAM, but the Properties table shows exactly ONE row for
    it -- bare name, no hierarchy-scope prefix, no per-member lines."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    shared = SupplySetPlacement(id="s1", name="SS_SHARED", power="VDD", ground="VSS", owner_paths=["top/u_core", "top/u_dbg"])
    view._domain_state.supply_sets["s1"] = shared
    tab.properties.refresh_diagram_overlay(view._shared_state, view._domain_state)

    rows = _rows(tab.properties._tables["SupplySet"])
    matching = [r for r in rows if r[0] == "SS_SHARED"]
    assert len(matching) == 1
    assert matching[0] == ["SS_SHARED", "VDD", "VSS", "-", "-"]


def test_single_hierarchy_supply_set_row_is_unaffected(qtbot):
    """No behaviour change for the pre-existing, common single-
    hierarchy case."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    single = SupplySetPlacement(id="s1", name="SS_SOLO", power="VDD", owner_path="top/u_core")
    view._domain_state.supply_sets["s1"] = single
    tab.properties.refresh_diagram_overlay(view._shared_state, view._domain_state)

    rows = _rows(tab.properties._tables["SupplySet"])
    matching = next(r for r in rows if r[0] == "SS_SOLO")
    assert matching[1] == "VDD"


# --- items 9+10: Retention tab is populated from the diagram-only state ------
# ["Name", "Scope", "Domain", "Elements", "Primary Supply", "Retention Supply"]


def _domain_with_supply_sets(view, owner_path: str, domain_id: str = "d1"):
    primary = SupplySetPlacement(id="ss_primary", name="SS_PRIMARY", power="VDD", owner_path=owner_path)
    retention = SupplySetPlacement(id="ss_retention", name="SS_RETENTION", power="VDD_RET", owner_path=owner_path)
    view._domain_state.supply_sets[primary.id] = primary
    view._domain_state.supply_sets[retention.id] = retention
    domain = PowerDomainPlacement(
        id=domain_id, owner_path=owner_path, name="PD_TOP", color=QColor("#ff0000"), primary_supply_set=primary.id, secondary_supply_sets=[retention.id]
    )
    view._domain_state.domains[domain_id] = domain
    return domain, primary, retention


def test_retention_tab_is_populated_from_the_diagram_only_state(qtbot):
    """"Elements" (column 5) is now a setCellWidget() cell
    (_ElementsCellWidget, shared with Isolation's own Properties-table
    Elements column -- see properties_panel.py's own docstring), not a
    plain item -- checked separately from the rest of the row, which
    _rows() (plain-item-only) still covers correctly."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    domain, primary, retention = _domain_with_supply_sets(view, "top")

    view.create_retention_strategy(view._boxes["top/u_core"], domain, "RET1", primary.id, retention.id, "save_it", "restore_it", ["top/u_core/out1"], {})

    table = tab.properties._tables["RetentionStrategy"]
    rows = _rows(table)
    matching_row = next(r for r in range(table.rowCount()) if rows[r][0] == "RET1")
    assert rows[matching_row][:7] == [
        "RET1", "u_core", "PD_TOP", "SS_PRIMARY", "SS_RETENTION", "save_it", "restore_it"]
    from frontend.src.power_intent_view.properties_panel import (
        _RETENTION_ELEMENTS_COLUMN,
        _ElementsCellWidget,
    )

    widget = table.cellWidget(matching_row, _RETENTION_ELEMENTS_COLUMN)
    assert isinstance(widget, _ElementsCellWidget)
    assert widget._elements == ["top/u_core/out1"]


def test_retention_tab_elements_cell_uses_the_same_shared_scrollable_widget_as_isolation(qtbot):
    """Item 4, explicit request: Retention's own Properties-table
    Elements cell now gets the SAME max-10 + "Show all" + scrollable-
    expansion treatment as Isolation's -- the exact same
    _ElementsCellWidget class, not a second, separate implementation.
    Scoped ONLY to this read-only Properties-table summary cell --
    retention_dialog.py's own Add/Edit Elements list (filter, buttons,
    behavior) is untouched, per the explicit constraint."""
    from frontend.src.power_intent_view.properties_panel import (
        _RETENTION_ELEMENTS_COLUMN,
        _ElementsCellWidget,
    )

    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    domain, primary, retention = _domain_with_supply_sets(view, "top")
    elements = [f"top/u_core/e{i}" for i in range(15)]
    view.create_retention_strategy(view._boxes["top/u_core"], domain, "RET_LONG", primary.id, retention.id, "save", "restore", elements, {})

    table = tab.properties._tables["RetentionStrategy"]
    rows = _rows(table)
    matching_row = next(r for r in range(table.rowCount()) if rows[r][0] == "RET_LONG")
    widget = table.cellWidget(matching_row, _RETENTION_ELEMENTS_COLUMN)
    assert isinstance(widget, _ElementsCellWidget)
    assert widget._show_all_button is not None
    assert len(widget._label.text().split("\n")) == 10
    assert widget._list_widget is None

    widget._show_all_button.click()

    assert widget._list_widget is not None
    assert widget._list_widget.count() == 15
    assert [widget._list_widget.item(i).text() for i in range(15)] == elements
    assert widget._list_widget.maximumHeight() == widget._EXPANDED_MAX_HEIGHT


def test_retention_tab_primary_supply_column_sits_before_retention_supply(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    headers = [
        tab.properties._tables["RetentionStrategy"].horizontalHeaderItem(c).text()
        for c in range(tab.properties._tables["RetentionStrategy"].columnCount())
    ]
    assert headers.index("Primary Supply") < headers.index("Retention Supply")


def test_retention_tab_supply_columns_sit_before_elements(qtbot):
    """Explicit request: "Primary Supply"/"Retention Supply" grouped
    together ahead of the "Elements" summary column, not after it."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    headers = [
        tab.properties._tables["RetentionStrategy"].horizontalHeaderItem(c).text()
        for c in range(tab.properties._tables["RetentionStrategy"].columnCount())
    ]
    assert headers.index("Primary Supply") < headers.index("Elements")
    assert headers.index("Retention Supply") < headers.index("Elements")


def test_retention_tab_updates_live_on_edit(qtbot, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    domain, primary, retention = _domain_with_supply_sets(view, "top")
    placement = view.create_retention_strategy(view._boxes["top/u_core"], domain, "RET1", primary.id, retention.id, "save", "restore", [], {})
    monkeypatch.setattr(message_box, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)

    view.edit_retention_strategy(placement.id, "RET1_RENAMED", primary.id, retention.id, "save", "restore", [], {})

    rows = _rows(tab.properties._tables["RetentionStrategy"])
    assert any(r[0] == "RET1_RENAMED" for r in rows)
    assert not any(r[0] == "RET1" for r in rows)


def test_retention_tab_updates_live_on_delete(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    domain, primary, retention = _domain_with_supply_sets(view, "top")
    placement = view.create_retention_strategy(view._boxes["top/u_core"], domain, "RET1", primary.id, retention.id, "save", "restore", [], {})
    assert any(r[0] == "RET1" for r in _rows(tab.properties._tables["RetentionStrategy"]))

    view.delete_retention_strategy(placement.id)

    assert not any(r[0] == "RET1" for r in _rows(tab.properties._tables["RetentionStrategy"]))


# --- New feature (explicit request): non-blocking warning when Retention is
# --- added to a hierarchy with no sequential elements -----------------------


def test_retention_on_a_hierarchy_with_no_sequential_elements_creates_successfully_with_a_warning(qtbot):
    """"top/u_dbg" in the mock design has no SequentialElements at all
    (unlike "top/u_core", which does -- see the sibling test below).
    Creation must still succeed with no blocking (mirrors the
    non-blocking "No Cell Type Assigned" convention for a Power
    Switch's own missing Cell Type -- see properties_panel.py's own
    _load_switches() docstring), and the Properties table's own Name
    cell for this row must carry the warning, italicized."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    domain, primary, retention = _domain_with_supply_sets(view, "top/u_dbg", domain_id="d_dbg")

    placement = view.create_retention_strategy(view._boxes["top/u_dbg"], domain, "RET_DBG", primary.id, retention.id, "save", "restore", [], {})

    assert placement is not None  # creation succeeded, not blocked

    table = tab.properties._tables["RetentionStrategy"]
    rows = _rows(table)
    matching_row = next(r for r in range(table.rowCount()) if rows[r][0].startswith("RET_DBG"))
    assert rows[matching_row][0] == "RET_DBG (No Sequential Elements)"
    assert table.item(matching_row, 0).font().italic() is True


def test_retention_on_a_hierarchy_with_sequential_elements_does_not_warn(qtbot):
    """"top/u_core" in the mock design DOES have SequentialElements --
    the sibling test above's exact negative case."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    domain, primary, retention = _domain_with_supply_sets(view, "top/u_core", domain_id="d_core")

    view.create_retention_strategy(view._boxes["top/u_core"], domain, "RET_CORE", primary.id, retention.id, "save", "restore", [], {})

    table = tab.properties._tables["RetentionStrategy"]
    rows = _rows(table)
    matching_row = next(r for r in range(table.rowCount()) if rows[r][0].startswith("RET_CORE"))
    assert rows[matching_row][0] == "RET_CORE"
    assert table.item(matching_row, 0).font().italic() is False


def test_retention_tab_merges_real_and_diagram_only_rows(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    power_intent = build_mock_power_intent()
    tab.set_power_intent(power_intent)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    domain, primary, retention = _domain_with_supply_sets(view, "top")
    view.create_retention_strategy(view._boxes["top/u_core"], domain, "RET_DIAGRAM", primary.id, retention.id, "save", "restore", [], {})

    names = {r[0] for r in _rows(tab.properties._tables["RetentionStrategy"])}
    assert "RET_DIAGRAM" in names
    assert len(power_intent.retention_strategies) > 0
    real_names = {s.name for s in power_intent.retention_strategies.values()}
    assert real_names <= names


def test_retention_tab_real_row_has_no_primary_supply(qtbot):
    """The real backend RetentionStrategy schema has no primary_supply
    field at all -- confirmed against shared/models/power_intent.py."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_power_intent(build_mock_power_intent())

    rows = _rows(tab.properties._tables["RetentionStrategy"])
    assert len(rows) >= 1
    assert rows[0][3] == ""  # Primary Supply


# --- Power Switch: instance table shows Input/Output/Control Port + Cell --
# --- Type directly (rework, explicit request -- REVERSES the earlier ------
# --- "Cell Type dropdown referencing a reusable library" model; there is --
# --- no more secondary "Power Switch Cells" table at all) -----------------


def test_power_switch_instances_table_shows_ports_cell_type_and_on_off_state(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view._domain_state.domains["d1"] = PowerDomainPlacement(id="d1", owner_path="top", name="PD_TOP", color=QColor("#ff0000"))
    port_icon = view.add_supply_port(view._boxes["top"], "VDD_ALWAYS_ON", QColor("#00ff00"))

    view.add_power_switch(
        port_icon, "SW1", "VDDG", "VDD1", "CTRL1", "SWCELL", "sleep_b", "~sleep_b",
        control_signal="pwr_en", ack_port="ACKO", ack_signal="pwr_ack",
    )

    table = tab.properties._tables["PowerSwitch"]
    headers = [table.horizontalHeaderItem(c).text() for c in range(table.columnCount())]
    assert "Cell Pins" not in headers
    assert headers == [
        "Name",
        "Power Domain",
        "Supply In",
        "Supply Out",
        "Input Supply Port",
        "Output Supply Port",
        "Control Port",
        "Cell Type",
        # The RTL net driving the control pin -- distinct from "Control
        # Port", which is the cell's own pin name. (Renamed from "Switch
        # Control" when the two were paired on one row.)
        "Control Signal",
        # The acknowledge pair, mirroring the control pair: the cell's
        # own pin, then the RTL net it drives.
        "Ack Port",
        "Ack Signal",
        "On State",
        "Off State",
    ]
    rows = _rows(table)
    matching_row = next(r for r in range(table.rowCount()) if rows[r][0] == "SW1")
    assert rows[matching_row] == [
        "SW1", "PD_TOP", "Not Connected", "VDD_ALWAYS_ON", "VDDG", "VDD1", "CTRL1", "SWCELL",
        "pwr_en", "ACKO", "pwr_ack", "sleep_b", "~sleep_b",
    ]


def test_power_switch_instance_with_no_cell_type_still_appears_with_a_warning(qtbot):
    """An instance with an empty Cell Type is NOT blocked from
    appearing in the table -- it shows a "No Cell Type Assigned"
    warning in italics instead (the closest existing "Elaborate-time
    warning" convention this app has -- power_resolution_cache.py's own
    Combinational Loop/Multiple Drivers/Floating markers -- reused
    here, see properties_panel.py's own _load_switches() docstring)."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view._domain_state.domains["d1"] = PowerDomainPlacement(id="d1", owner_path="top", name="PD_TOP", color=QColor("#ff0000"))
    port_icon = view.add_supply_port(view._boxes["top"], "VDD_ALWAYS_ON", QColor("#00ff00"))

    view.add_power_switch(port_icon, "SW1", "in", "out", "ctrl", None, "sleep_b", "")

    table = tab.properties._tables["PowerSwitch"]
    rows = _rows(table)
    matching_row = next(r for r in range(table.rowCount()) if rows[r][0] == "SW1")
    assert rows[matching_row][7] == "No Cell Type Assigned"
    item = table.item(matching_row, 7)
    assert item.font().italic() is True


def test_switch_and_retention_tabs_rename_domain_column_to_power_domain(qtbot):
    """Rename only (explicit request): the underlying data is unaffected
    -- only the column label changes from "Domain" to "Power Domain"
    on the "Switch" and "Retention" tabs specifically (Isolation/Level
    Shifter already said "Power Domain")."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)

    switch_headers = [
        tab.properties._tables["PowerSwitch"].horizontalHeaderItem(c).text()
        for c in range(tab.properties._tables["PowerSwitch"].columnCount())
    ]
    retention_headers = [
        tab.properties._tables["RetentionStrategy"].horizontalHeaderItem(c).text()
        for c in range(tab.properties._tables["RetentionStrategy"].columnCount())
    ]

    assert "Power Domain" in switch_headers
    assert "Domain" not in switch_headers
    assert "Power Domain" in retention_headers
    assert "Domain" not in retention_headers


# --- Save/Restore Signal columns (explicit request) -------------------------


def test_the_retention_tab_has_save_and_restore_signal_columns_before_elements(qtbot):
    """Positioned to match the dialog's own field order: the two
    supplies, then the two signals, then Elements."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    table = tab.properties._tables["RetentionStrategy"]
    headers = [table.horizontalHeaderItem(c).text() for c in range(table.columnCount())]
    assert headers == [
        "Name", "Scope", "Power Domain", "Primary Supply", "Retention Supply",
        "Save Signal", "Restore Signal", "Elements",
    ]


def test_a_diagram_only_retention_row_shows_its_own_save_and_restore_signals(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    domain, primary, retention = _domain_with_supply_sets(view, "top")
    view.create_retention_strategy(view._boxes["top/u_core"], domain, "RET1", primary.id,
                                   retention.id, "save_clk", "restore_clk", ["top/u_core/out1"], {})

    table = tab.properties._tables["RetentionStrategy"]
    rows = _rows(table)
    row = next(r for r in range(table.rowCount()) if rows[r][0] == "RET1")
    assert rows[row][5] == "save_clk"
    assert rows[row][6] == "restore_clk"


def test_a_real_backend_retention_row_fills_the_signal_columns_too(qtbot):
    """Unlike "Primary Supply", which stays blank for a real row because
    the backend schema genuinely has no such field, these two ARE real
    fields on `RetentionStrategy` -- so a real row fills them in rather
    than being blanked."""
    from frontend.src.shared.models.power_intent import PowerIntentDesign, RetentionStrategy

    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    design = PowerIntentDesign(
        source_format="UPF", source_version="3.1",
        retention_strategies={
            "r": RetentionStrategy(id="r", name="RET_REAL", scope="top", domain="PD",
                                   retention_supply="SS", save_signal="s_sig",
                                   restore_signal="r_sig", elements=["top/q"]),
        },
    )
    tab.properties.load(design)

    table = tab.properties._tables["RetentionStrategy"]
    rows = _rows(table)
    row = next(r for r in range(table.rowCount()) if rows[r][0] == "RET_REAL")
    assert rows[row][5] == "s_sig"
    assert rows[row][6] == "r_sig"
    assert rows[row][3] == ""  # Primary Supply: still blank, no backend field


def test_a_retention_row_with_no_signals_leaves_the_columns_empty(qtbot):
    """A real row that carried none -- blank, not the string "None"."""
    from frontend.src.shared.models.power_intent import PowerIntentDesign, RetentionStrategy

    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    design = PowerIntentDesign(
        source_format="UPF", source_version="3.1",
        retention_strategies={
            "r": RetentionStrategy(id="r", name="RET_BARE", scope="top", domain="PD"),
        },
    )
    tab.properties.load(design)

    table = tab.properties._tables["RetentionStrategy"]
    rows = _rows(table)
    row = next(r for r in range(table.rowCount()) if rows[r][0] == "RET_BARE")
    assert rows[row][5] == "" and rows[row][6] == ""
