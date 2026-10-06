""""Power View" tab (explicit request, Power Intent tab's Hierarchy Tree
dock split into "Design View"/"Power View" -- see power_view_tree.py's
own module docstring): a tree organized by Power Domain instead of raw
RTL hierarchy.

Three layers of coverage, mirroring how the closely-related reverse-
mapping feature (test_properties_row_to_diagram_highlight.py) and the
Properties-table Edit/Remove feature (test_properties_table_edit_remove.py)
are each tested:
- Pure data (`build_power_view_tree()`): ordering, domain-nesting
  containment, bare-name/no-scope domain labels, transparent pass-through
  hierarchy blocks, one node per multi-hierarchy domain.
- Color resolution: Power Domain/Power Switch/Isolation/Retention node
  colors, reusing the SAME live-binding sources the diagram's own icons
  already follow.
- The Qt widget (`PowerViewTree`): rebuild populates the tree; selection
  sync (both directions); right-click Edit/Remove dispatch; live update
  on a full PowerIntentTab via `diagramStateChanged`.
"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog

from frontend.src.power_intent_view.power_domain_overlay import PowerDomainPlacement, SupplySetPlacement
from frontend.src.power_intent_view.power_intent_tab import PowerIntentTab
from frontend.src.power_intent_view.power_view_tree import PowerViewTree, build_power_view_tree
from frontend.src.power_intent_view.selection_controller import PowerIntentSelectionController
from frontend.src.shared.mock_backend import build_mock_design
from frontend.src.shared.models.rtl import Definition, Design, Instance, SourceLanguage

_DEFAULT_COLOR = QColor("#9e9e9e")


def _passthrough_macro_design() -> Design:
    """top -> u_mid -> u_macro -- "u_mid" itself has no power content of
    its own (no domain, not a macro), so it must be a transparent pass-
    through: a Power Domain rooted at "top" should show "u_macro" as a
    direct Macro child, with no node at all for "u_mid"."""
    macro = Definition(definition_id="macro", base_name="macro", language=SourceLanguage.SYSTEMVERILOG, is_black_box=True)
    mid = Definition(definition_id="mid", base_name="mid", language=SourceLanguage.VERILOG, instances=[Instance(name="u_macro", definition_id="macro")])
    top = Definition(definition_id="top", base_name="top", language=SourceLanguage.VERILOG, instances=[Instance(name="u_mid", definition_id="mid")])
    return Design(definitions={"top": top, "mid": mid, "macro": macro}, top="top")


# --- (a) pure data: build_power_view_tree() ----------------------------------


def test_domain_node_shows_bare_name_without_scope(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view._domain_state.domains["d1"] = PowerDomainPlacement(id="d1", owner_path="top/u_core", name="PD_CORE", color=QColor("#ff0000"))

    nodes = build_power_view_tree(view._boxes, view._domain_state, view._retention_state, view._isolation_state, view._power_switch_state, view._supply_port_icons)

    assert len(nodes) == 1
    assert nodes[0].kind == "PowerDomain"
    assert nodes[0].label == "PD_CORE"  # bare name, no "top/u_core" prefix


def test_multi_hierarchy_domain_is_a_single_node(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view._domain_state.domains["d1"] = PowerDomainPlacement(
        id="d1", owner_path="top/u_core", name="PD_MULTI", color=QColor("#ff0000"), owner_paths=["top/u_core", "top/u_dbg", "top/u_fwd"]
    )

    nodes = build_power_view_tree(view._boxes, view._domain_state, view._retention_state, view._isolation_state, view._power_switch_state, view._supply_port_icons)

    assert len(nodes) == 1  # one node regardless of spanning 3 physical hierarchies


def test_transparent_passthrough_block_is_not_its_own_node(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(_passthrough_macro_design())
    view = tab.diagram._view
    view._domain_state.domains["d1"] = PowerDomainPlacement(id="d1", owner_path="top", name="PD_TOP", color=QColor("#ff0000"))

    nodes = build_power_view_tree(view._boxes, view._domain_state, view._retention_state, view._isolation_state, view._power_switch_state, view._supply_port_icons)

    domain_node = nodes[0]
    assert [c.kind for c in domain_node.children] == ["Macro"]
    assert domain_node.children[0].label == "u_macro"
    assert domain_node.children[0].construct_id == "top/u_mid/u_macro"


def test_nested_domain_is_a_child_of_its_containing_domain_and_sibling_macro_stays_at_top(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    top_domain = PowerDomainPlacement(id="d_top", owner_path="top", name="PD_TOP", color=QColor("#ff0000"))
    sub_domain = PowerDomainPlacement(id="d_sub", owner_path="top/u_core", name="PD_SUB", color=QColor("#0000ff"))
    view._domain_state.domains["d_top"] = top_domain
    view._domain_state.domains["d_sub"] = sub_domain

    nodes = build_power_view_tree(view._boxes, view._domain_state, view._retention_state, view._isolation_state, view._power_switch_state, view._supply_port_icons)

    assert len(nodes) == 1  # PD_SUB is nested -- not a second root
    top_node = nodes[0]
    assert top_node.label == "PD_TOP"
    child_kinds = [c.kind for c in top_node.children]
    assert child_kinds.count("PowerDomain") == 1
    sub_node = next(c for c in top_node.children if c.kind == "PowerDomain")
    assert sub_node.label == "PD_SUB"
    # "top/u_ram" is build_mock_design()'s own macro, a SIBLING of
    # u_core -- it belongs directly to PD_TOP, not nested under PD_SUB.
    macro_children = [c for c in top_node.children if c.kind == "Macro"]
    assert {m.construct_id for m in macro_children} == {"top/u_ram"}
    assert not any(c.kind == "Macro" for c in sub_node.children if c.construct_id == "top/u_ram")


def test_child_ordering_is_supply_set_then_switch_then_isolation_then_retention_then_domain_then_macro(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top"]
    primary = SupplySetPlacement(id="ss1", name="SS1", power="VDD", ground="VSS", owner_paths=["top"])
    view._domain_state.supply_sets["ss1"] = primary
    domain = PowerDomainPlacement(id="d1", owner_path="top", name="PD_TOP", color=QColor("#4caf50"), primary_supply_set="ss1")
    view._domain_state.domains["d1"] = domain
    sub_domain = PowerDomainPlacement(id="d2", owner_path="top/u_core", name="PD_SUB", color=QColor("#00ff00"))
    view._domain_state.domains["d2"] = sub_domain
    view.create_retention_strategy(box, domain, "RET1", "ss1", "ss1", "save", "restore", ["top/u_dbg/clk"], {})
    switch_target = view.add_supply_port(box, "VDD_SW", QColor("#123456"))
    view.add_power_switch(switch_target, "SW1", "in", "out", "ctrl", None, "sleep_b", "")
    view.create_isolation(box, "ISO1", "d1", "ss1", None, None, "low", "self", "ctrl", "high", ["top/clk"])

    nodes = build_power_view_tree(view._boxes, view._domain_state, view._retention_state, view._isolation_state, view._power_switch_state, view._supply_port_icons)

    top_node = nodes[0]
    kinds = [c.kind for c in top_node.children]
    # Supply Set(s) -> Power Switch(es) -> Isolation(s) -> Retention(s) -> nested domain(s) -> Macro(s)
    assert kinds.index("SupplySet") < kinds.index("PowerSwitch") < kinds.index("IsolationStrategy") < kinds.index("RetentionStrategy") < kinds.index("PowerDomain")
    assert kinds[-1] == "Macro"  # "top/u_ram" always last


# --- (b) color resolution ------------------------------------------------


def test_power_domain_node_color_matches_the_domains_own_fill_color(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view._domain_state.domains["d1"] = PowerDomainPlacement(id="d1", owner_path="top", name="PD1", color=QColor("#123456"))

    nodes = build_power_view_tree(view._boxes, view._domain_state, view._retention_state, view._isolation_state, view._power_switch_state, view._supply_port_icons)

    assert nodes[0].color == QColor("#123456")


def test_supply_set_node_color_follows_its_own_power_rail(qtbot):
    """Bug fix: Supply Set nodes were never given a color at all (see
    resolve_supply_set_color_source()'s own docstring) -- confirmed
    empirically before fixing by checking _build_domain_node()'s own
    Supply Set loop passed no `color=` argument. A Supply Set has no
    diagram glyph of its own, so its node's color is the color of the
    real Supply Port icon its own `.power` field names, same match rule
    Isolation/Retention already use."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top"]
    vdd = view.add_supply_port(box, "VDD_SS", QColor("#00ff00"))
    view._domain_state.supply_sets["ss1"] = SupplySetPlacement(id="ss1", name="SS1", power="VDD_SS", owner_paths=["top"])
    view._domain_state.domains["d1"] = PowerDomainPlacement(id="d1", owner_path="top", name="PD1", color=QColor("#ff0000"), primary_supply_set="ss1")

    nodes = build_power_view_tree(view._boxes, view._domain_state, view._retention_state, view._isolation_state, view._power_switch_state, view._supply_port_icons)

    ss_node = next(c for c in nodes[0].children if c.kind == "SupplySet")
    assert ss_node.color == vdd.color == QColor("#00ff00")


def _supply_set_item(tab: PowerIntentTab):
    domain_item = tab.power_view_tree.topLevelItem(0)
    return next(
        domain_item.child(i)
        for i in range(domain_item.childCount())
        if tab.power_view_tree._kind_and_id_by_item[domain_item.child(i)][0] == "SupplySet"
    )


def test_supply_set_node_color_updates_live_on_the_full_tab_without_manual_refresh(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top"]
    vdd = view.add_supply_port(box, "VDD_SS", QColor("#00ff00"))
    ss = SupplySetPlacement(id="ss1", name="SS1", power="VDD_SS", owner_paths=["top"])
    view.create_power_domain(box, "PD1", QColor("#ff0000"), 0.5, "ss1", [], {"ss1": ss})
    assert _supply_set_item(tab).foreground(0).color() == QColor("#00ff00")

    view.edit_supply_port(vdd, "VDD_SS", QColor("#abcdef"))

    assert _supply_set_item(tab).foreground(0).color() == QColor("#abcdef")  # live, no manual refresh


def test_power_switch_node_color_follows_supply_out(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top"]
    view._domain_state.domains["d1"] = PowerDomainPlacement(id="d1", owner_path="top", name="PD1", color=QColor("#ff0000"))
    target = view.add_supply_port(box, "VDD_SW", QColor("#00ff00"))
    view.add_power_switch(target, "SW1", "in", "out", "ctrl", None, "sleep_b", "")

    nodes = build_power_view_tree(view._boxes, view._domain_state, view._retention_state, view._isolation_state, view._power_switch_state, view._supply_port_icons)

    switch_node = next(c for c in nodes[0].children if c.kind == "PowerSwitch")
    assert switch_node.color == target.color == QColor("#00ff00")

    target.recolor(QColor("#abcdef"))
    nodes = build_power_view_tree(view._boxes, view._domain_state, view._retention_state, view._isolation_state, view._power_switch_state, view._supply_port_icons)
    switch_node = next(c for c in nodes[0].children if c.kind == "PowerSwitch")
    assert switch_node.color == QColor("#abcdef")  # live -- resolved fresh on every rebuild


def test_isolation_node_color_follows_the_isolation_supply_sets_power_rail(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top"]
    vdd = view.add_supply_port(box, "VDD_ISO", QColor("#00ff00"))
    view._domain_state.domains["d1"] = PowerDomainPlacement(id="d1", owner_path="top", name="PD1", color=QColor("#ff0000"))
    view._domain_state.supply_sets["ss1"] = SupplySetPlacement(id="ss1", name="SS1", power="VDD_ISO", owner_paths=["top"])
    view.create_isolation(box, "ISO1", "d1", "ss1", None, None, "low", "self", "ctrl", "high", ["top/clk"])

    nodes = build_power_view_tree(view._boxes, view._domain_state, view._retention_state, view._isolation_state, view._power_switch_state, view._supply_port_icons)

    iso_node = next(c for c in nodes[0].children if c.kind == "IsolationStrategy")
    assert iso_node.color == vdd.color == QColor("#00ff00")


def test_retention_node_color_follows_the_retention_supply_sets_power_rail(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top"]
    vdd_ret = view.add_supply_port(box, "VDD_RET", QColor("#00ff00"))
    primary = SupplySetPlacement(id="ss_primary", name="SS_PRIMARY", power="VDD_ISO", owner_paths=["top"])
    retention_ss = SupplySetPlacement(id="ss_retention", name="SS_RETENTION", power="VDD_RET", owner_paths=["top"])
    view._domain_state.supply_sets["ss_primary"] = primary
    view._domain_state.supply_sets["ss_retention"] = retention_ss
    domain = PowerDomainPlacement(id="d1", owner_path="top", name="PD1", color=QColor("#ff0000"), primary_supply_set="ss_primary", secondary_supply_sets=["ss_retention"])
    view._domain_state.domains["d1"] = domain
    view.create_retention_strategy(box, domain, "RET1", "ss_primary", "ss_retention", "save", "restore", ["top/u_dbg/clk"], {})

    nodes = build_power_view_tree(view._boxes, view._domain_state, view._retention_state, view._isolation_state, view._power_switch_state, view._supply_port_icons)

    ret_node = next(c for c in nodes[0].children if c.kind == "RetentionStrategy")
    assert ret_node.color == vdd_ret.color == QColor("#00ff00")


def test_unresolved_color_falls_back_to_default_grey(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top"]
    view._domain_state.domains["d1"] = PowerDomainPlacement(id="d1", owner_path="top", name="PD1", color=QColor("#ff0000"))
    view._domain_state.supply_sets["ss1"] = SupplySetPlacement(id="ss1", name="SS1", power="NO_SUCH_PORT", owner_paths=["top"])
    view.create_isolation(box, "ISO1", "d1", "ss1", None, None, "low", "self", "ctrl", "high", ["top/clk"])

    nodes = build_power_view_tree(view._boxes, view._domain_state, view._retention_state, view._isolation_state, view._power_switch_state, view._supply_port_icons)

    iso_node = next(c for c in nodes[0].children if c.kind == "IsolationStrategy")
    assert iso_node.color == _DEFAULT_COLOR


# --- (c) the Qt widget: rebuild, selection sync, Edit/Remove ----------------


def test_rebuild_sets_row_foreground_to_the_resolved_color(qtbot):
    tree = PowerViewTree(PowerIntentSelectionController())
    qtbot.addWidget(tree)
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view._domain_state.domains["d1"] = PowerDomainPlacement(id="d1", owner_path="top", name="PD1", color=QColor("#123456"))
    nodes = build_power_view_tree(view._boxes, view._domain_state, view._retention_state, view._isolation_state, view._power_switch_state, view._supply_port_icons)

    tree.rebuild(nodes)

    assert tree.topLevelItemCount() == 1
    item = tree.topLevelItem(0)
    assert item.text(0) == "PD1"
    assert item.foreground(0).color() == QColor("#123456")


def test_selecting_a_construct_node_emits_on_the_power_intent_controller(qtbot):
    controller = PowerIntentSelectionController()
    tree = PowerViewTree(controller)
    qtbot.addWidget(tree)
    node_kind_id = []
    controller.selectionChanged.connect(lambda kind, cid: node_kind_id.append((kind, cid)))
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view._domain_state.domains["d1"] = PowerDomainPlacement(id="d1", owner_path="top", name="PD1", color=QColor("#123456"))
    nodes = build_power_view_tree(view._boxes, view._domain_state, view._retention_state, view._isolation_state, view._power_switch_state, view._supply_port_icons)
    tree.rebuild(nodes)

    tree.setCurrentItem(tree.topLevelItem(0))

    assert node_kind_id == [("PowerDomain", "d1")]


def test_selecting_a_macro_node_emits_on_the_hierarchy_controller_instead(qtbot):
    from frontend.src.hierarchy_view.selection_controller import HierarchySelectionController

    power_controller = PowerIntentSelectionController()
    hierarchy_controller = HierarchySelectionController()
    tree = PowerViewTree(power_controller, hierarchy_controller)
    qtbot.addWidget(tree)
    power_emissions = []
    hierarchy_emissions = []
    power_controller.selectionChanged.connect(lambda kind, cid: power_emissions.append((kind, cid)))
    hierarchy_controller.selectionChanged.connect(lambda paths: hierarchy_emissions.append(paths))
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(_passthrough_macro_design())
    view = tab.diagram._view
    view._domain_state.domains["d1"] = PowerDomainPlacement(id="d1", owner_path="top", name="PD_TOP", color=QColor("#ff0000"))
    nodes = build_power_view_tree(view._boxes, view._domain_state, view._retention_state, view._isolation_state, view._power_switch_state, view._supply_port_icons)
    tree.rebuild(nodes)
    macro_item = tree.topLevelItem(0).child(0)
    assert macro_item.text(0) == "u_macro"

    tree.setCurrentItem(macro_item)

    assert power_emissions == []
    assert hierarchy_emissions == [frozenset({"top/u_mid/u_macro"})]


def test_apply_selection_from_another_widget_selects_the_matching_item_without_reemitting(qtbot):
    controller = PowerIntentSelectionController()
    tree = PowerViewTree(controller)
    qtbot.addWidget(tree)
    emissions = []
    controller.selectionChanged.connect(lambda kind, cid: emissions.append((kind, cid)))
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view._domain_state.domains["d1"] = PowerDomainPlacement(id="d1", owner_path="top", name="PD1", color=QColor("#123456"))
    nodes = build_power_view_tree(view._boxes, view._domain_state, view._retention_state, view._isolation_state, view._power_switch_state, view._supply_port_icons)
    tree.rebuild(nodes)

    tree.apply_selection("PowerDomain", "d1")

    assert tree.currentItem() is tree.topLevelItem(0)
    assert emissions == []  # apply_selection() is a pure visual update, never re-emits


def test_right_click_edit_and_remove_dispatch_with_the_correct_kind_and_id(qtbot):
    tree = PowerViewTree(PowerIntentSelectionController())
    qtbot.addWidget(tree)
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view._domain_state.domains["d1"] = PowerDomainPlacement(id="d1", owner_path="top", name="PD1", color=QColor("#123456"))
    nodes = build_power_view_tree(view._boxes, view._domain_state, view._retention_state, view._isolation_state, view._power_switch_state, view._supply_port_icons)
    tree.rebuild(nodes)
    item = tree.topLevelItem(0)

    kind, construct_id, menu, actions = tree._row_context_menu_actions(item)
    assert kind == "PowerDomain"
    assert construct_id == "d1"
    edit_captured = []
    remove_captured = []
    tree.editRequested.connect(lambda k, cid: edit_captured.append((k, cid)))
    tree.removeRequested.connect(lambda k, cid: remove_captured.append((k, cid)))

    tree._dispatch_chosen_row_action(kind, construct_id, "edit")
    tree._dispatch_chosen_row_action(kind, construct_id, "remove")

    assert edit_captured == [("PowerDomain", "d1")]
    assert remove_captured == [("PowerDomain", "d1")]


def test_macro_node_has_no_context_menu_actions(qtbot):
    tree = PowerViewTree(PowerIntentSelectionController())
    qtbot.addWidget(tree)
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(_passthrough_macro_design())
    view = tab.diagram._view
    view._domain_state.domains["d1"] = PowerDomainPlacement(id="d1", owner_path="top", name="PD_TOP", color=QColor("#ff0000"))
    nodes = build_power_view_tree(view._boxes, view._domain_state, view._retention_state, view._isolation_state, view._power_switch_state, view._supply_port_icons)
    tree.rebuild(nodes)
    macro_item = tree.topLevelItem(0).child(0)

    kind, construct_id, menu, actions = tree._row_context_menu_actions(macro_item)

    assert menu is None
    assert actions == []


def test_context_menu_action_enablement_hooks_are_consulted(qtbot):
    tree = PowerViewTree(PowerIntentSelectionController())
    qtbot.addWidget(tree)
    tree.set_action_enablement_hooks(lambda kind, cid: False, lambda kind, cid: False)
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view._domain_state.domains["d1"] = PowerDomainPlacement(id="d1", owner_path="top", name="PD1", color=QColor("#123456"))
    nodes = build_power_view_tree(view._boxes, view._domain_state, view._retention_state, view._isolation_state, view._power_switch_state, view._supply_port_icons)
    tree.rebuild(nodes)
    item = tree.topLevelItem(0)

    _kind, _cid, menu, actions = tree._row_context_menu_actions(item)

    assert all(not action.isEnabled() for action, _ak in actions)


# --- (d) full-tab integration: tab split + live updates ---------------------


def test_power_intent_tab_has_design_view_and_power_view_tabs(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)

    assert tab.tree_view_tabs.count() == 2
    assert tab.tree_view_tabs.tabText(0) == "Design View"
    assert tab.tree_view_tabs.tabText(1) == "Power View"
    assert tab.tree_view_tabs.widget(1) is tab.power_view_tree


def test_power_view_updates_live_when_a_power_domain_is_created(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    assert tab.power_view_tree.topLevelItemCount() == 0
    box = tab.diagram._view._boxes["top"]

    tab.diagram._view.create_power_domain(box, "PD1", QColor("#123456"), 0.5, None, [], {})

    assert tab.power_view_tree.topLevelItemCount() == 1
    assert tab.power_view_tree.topLevelItem(0).text(0) == "PD1"


def test_power_view_updates_live_when_a_power_domain_is_deleted(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    box = tab.diagram._view._boxes["top"]
    tab.diagram._view.create_power_domain(box, "PD1", QColor("#123456"), 0.5, None, [], {})
    assert tab.power_view_tree.topLevelItemCount() == 1

    tab.diagram._view.remove_power_domain(box)

    assert tab.power_view_tree.topLevelItemCount() == 0


def test_power_view_reflects_a_live_color_change_without_manual_refresh(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    box = tab.diagram._view._boxes["top"]
    placement = tab.diagram._view.create_power_domain(box, "PD1", QColor("#123456"), 0.5, None, [], {})
    assert tab.power_view_tree.topLevelItem(0).foreground(0).color() == QColor("#123456")

    tab.diagram._view.edit_power_domain(placement.id, "PD1", QColor("#00ff00"), 0.5, None, [], {})

    assert tab.power_view_tree.topLevelItem(0).foreground(0).color() == QColor("#00ff00")


def test_selecting_a_power_view_node_highlights_the_diagram_via_the_full_tab(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    box = tab.diagram._view._boxes["top"]
    tab.diagram._view.create_power_domain(box, "PD1", QColor("#123456"), 0.5, None, [], {})
    item = tab.power_view_tree.topLevelItem(0)

    tab.power_view_tree.setCurrentItem(item)

    assert tab.diagram._view._highlighted_constructs == [box]


# --- (e) italic type-identifier labels ---------------------------------------


def test_each_element_type_renders_its_correct_italic_type_label(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top"]
    primary = SupplySetPlacement(id="ss1", name="SS1", power="VDD", ground="VSS", owner_paths=["top"])
    view._domain_state.supply_sets["ss1"] = primary
    domain = PowerDomainPlacement(id="d1", owner_path="top", name="PD_TOP", color=QColor("#4caf50"), primary_supply_set="ss1")
    view._domain_state.domains["d1"] = domain
    view._domain_state.domains["d2"] = PowerDomainPlacement(id="d2", owner_path="top/u_core", name="PD_SUB", color=QColor("#00ff00"))
    view.create_retention_strategy(box, domain, "RET1", "ss1", "ss1", "save", "restore", ["top/u_dbg/clk"], {})
    switch_target = view.add_supply_port(box, "VDD_SW", QColor("#123456"))
    view.add_power_switch(switch_target, "SW1", "in", "out", "ctrl", None, "sleep_b", "")
    view.create_isolation(box, "ISO1", "d1", "ss1", None, None, "low", "self", "ctrl", "high", ["top/clk"])

    nodes = build_power_view_tree(view._boxes, view._domain_state, view._retention_state, view._isolation_state, view._power_switch_state, view._supply_port_icons)
    tab.power_view_tree.rebuild(nodes)

    expected = {
        "PowerDomain": "(Power Domain)",
        "SupplySet": "(Supply Set)",
        "PowerSwitch": "(Power Switch)",
        "IsolationStrategy": "(Isolation)",
        "RetentionStrategy": "(Retention)",
        "Macro": "(Macro)",
    }
    seen_kinds = set()
    for item, (kind, _construct_id) in tab.power_view_tree._kind_and_id_by_item.items():
        seen_kinds.add(kind)
        assert item.text(1) == expected[kind], f"{kind} label mismatch"
        assert item.font(1).italic() is True, f"{kind} label must be italic"
        assert item.foreground(1).color() == QColor("#9e9e9e"), f"{kind} label must be neutral grey"
    assert seen_kinds == set(expected)  # sanity: all 6 kinds actually present in this fixture


def test_type_label_color_is_independent_of_the_elements_own_color(qtbot):
    """Explicit request: the type-label styling is independent of the
    color-binding on the element's own name -- a Power Switch's own name
    (column 0) follows Supply Out's color, but its type label (column 1)
    stays neutral grey regardless."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top"]
    view._domain_state.domains["d1"] = PowerDomainPlacement(id="d1", owner_path="top", name="PD1", color=QColor("#ff0000"))
    target = view.add_supply_port(box, "VDD_SW", QColor("#00ff00"))
    view.add_power_switch(target, "SW1", "in", "out", "ctrl", None, "sleep_b", "")

    nodes = build_power_view_tree(view._boxes, view._domain_state, view._retention_state, view._isolation_state, view._power_switch_state, view._supply_port_icons)
    tab.power_view_tree.rebuild(nodes)

    switch_item = next(
        item for item, (kind, _cid) in tab.power_view_tree._kind_and_id_by_item.items() if kind == "PowerSwitch"
    )
    assert switch_item.foreground(0).color() == QColor("#00ff00")  # name follows Supply Out
    assert switch_item.foreground(1).color() == QColor("#9e9e9e")  # label stays neutral


# --- Level Shifter node type --------------------------------------------


def test_level_shifter_node_appears_after_isolation_and_before_retention(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top"]
    domain = PowerDomainPlacement(id="d1", owner_path="top", name="PD_TOP", color=QColor("#4caf50"))
    view._domain_state.domains["d1"] = domain
    view.create_isolation(box, "ISO1", "d1", "", None, None, "low", "self", "ctrl", "high", ["top/clk"])
    view.create_level_shifter(box, "LS1", "d1", "inputs", "low_to_high", "self", None, None, ["top/clk"])
    view.create_retention_strategy(box, domain, "RET1", "", "", "save", "restore", ["top/u_dbg/clk"], {})

    nodes = build_power_view_tree(
        view._boxes, view._domain_state, view._retention_state, view._isolation_state, view._power_switch_state, view._supply_port_icons, view._level_shifter_state
    )

    kinds = [c.kind for c in nodes[0].children]
    assert kinds.index("IsolationStrategy") < kinds.index("LevelShifterStrategy") < kinds.index("RetentionStrategy")


def test_level_shifter_node_color_matches_its_power_domains_color(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top"]
    view._domain_state.domains["d1"] = PowerDomainPlacement(id="d1", owner_path="top", name="PD1", color=QColor("#123456"))
    view.create_level_shifter(box, "LS1", "d1", "inputs", "low_to_high", "self", None, None, ["top/clk"])

    nodes = build_power_view_tree(
        view._boxes, view._domain_state, view._retention_state, view._isolation_state, view._power_switch_state, view._supply_port_icons, view._level_shifter_state
    )

    ls_node = next(c for c in nodes[0].children if c.kind == "LevelShifterStrategy")
    assert ls_node.color == QColor("#123456")


def test_level_shifter_type_label_is_italic_and_neutral_grey(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top"]
    view._domain_state.domains["d1"] = PowerDomainPlacement(id="d1", owner_path="top", name="PD1", color=QColor("#123456"))
    view.create_level_shifter(box, "LS1", "d1", "inputs", "low_to_high", "self", None, None, ["top/clk"])
    nodes = build_power_view_tree(
        view._boxes, view._domain_state, view._retention_state, view._isolation_state, view._power_switch_state, view._supply_port_icons, view._level_shifter_state
    )
    tab.power_view_tree.rebuild(nodes)

    ls_item = next(item for item, (kind, _cid) in tab.power_view_tree._kind_and_id_by_item.items() if kind == "LevelShifterStrategy")

    assert ls_item.text(1) == "(Level Shifter)"
    assert ls_item.font(1).italic() is True
    assert ls_item.foreground(1).color() == QColor("#9e9e9e")


def test_level_shifter_omitted_when_state_not_provided(qtbot):
    """Backward-compatible default (None) -- a caller/test that predates
    Level Shifter and never passes this argument keeps working
    unchanged, simply never showing Level Shifter nodes."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view._domain_state.domains["d1"] = PowerDomainPlacement(id="d1", owner_path="top", name="PD1", color=QColor("#123456"))

    nodes = build_power_view_tree(view._boxes, view._domain_state, view._retention_state, view._isolation_state, view._power_switch_state, view._supply_port_icons)

    assert [c.kind for c in nodes[0].children] == ["Macro"]  # build_mock_design()'s own "top/u_ram" macro -- no Level Shifter node


def test_power_view_updates_live_when_a_level_shifter_is_created_on_the_full_tab(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top"]
    view.create_power_domain(box, "PD1", QColor("#123456"), 0.5, None, [], {})
    domain_id = next(iter(view._domain_state.domains))
    children_before = tab.power_view_tree.topLevelItem(0).childCount()

    view.create_level_shifter(box, "LS1", domain_id, "inputs", "low_to_high", "self", None, None, ["top/clk"])

    domain_item = tab.power_view_tree.topLevelItem(0)
    child_kinds = [tab.power_view_tree._kind_and_id_by_item[domain_item.child(i)][0] for i in range(domain_item.childCount())]
    assert "LevelShifterStrategy" in child_kinds


# --- regression: a REAL click must select the row, not just currentItem() --
#
# Bug (regression, explicit request): PowerViewTree used to define its own
# `setSelection(construct_kind, construct_id)` -- QTreeWidget (via
# QAbstractItemView) already has a built-in virtual `setSelection(QRect,
# QItemSelectionModel.SelectionFlags)`, used INTERNALLY by Qt's own mouse
# click handling to perform a real click's selection. The same-named
# override silently shadowed it: a real click set `currentItem()` but
# never actually SELECTED the row (`selectedItems()` stayed empty), so
# `_on_item_selected()` never fired and nothing ever got highlighted --
# the exact same shadowing bug class already fixed once before in this
# codebase for HierarchyTreeWidget.apply_selection() (see that method's
# own docstring). Every test above that drives selection via
# `setCurrentItem()`/`apply_selection()` directly -- the "directly-
# testable core" precedent this app uses everywhere else -- CANNOT catch
# this class of bug at all, since it bypasses Qt's own click machinery
# entirely; only a real QTest.mouseClick() exercises the actual shadowed
# method. Fixed by renaming to `apply_selection()`, matching the
# established sibling precedent exactly.


def _click_item(tree, item) -> None:
    tree.show()
    QTest.qWaitForWindowExposed(tree)
    rect = tree.visualItemRect(item)
    QTest.mouseClick(tree.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())


def test_a_real_click_selects_every_entity_types_power_view_node_and_highlights_the_diagram(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top"]

    domain = view.create_power_domain(box, "PD1", QColor("#ff0000"), 0.5, None, [], {})
    ss = SupplySetPlacement(id="ss1", name="SS1", power="VDD", owner_paths=["top"])
    view._domain_state.supply_sets["ss1"] = ss
    view.edit_power_domain(domain.id, domain.name, domain.color, domain.transparency, "ss1", [], {})
    target = view.add_supply_port(box, "VDD_SW", QColor("#00ff00"))
    switch = view.add_power_switch(target, "SW1", "in", "out", "ctrl", None, "sleep_b", "")
    iso = view.create_isolation(box, "ISO1", domain.id, "ss1", None, None, "low", "self", "ctrl", "high", ["top/clk"])
    ret = view.create_retention_strategy(box, domain, "RET1", "ss1", "ss1", "save", "restore", ["top/u_dbg/clk"], {})
    ls = view.create_level_shifter(box, "LS1", domain.id, "inputs", "low_to_high", "self", None, None, ["top/clk"])

    tree = tab.power_view_tree
    tree.resize(500, 400)

    for kind, cid in [
        ("PowerDomain", domain.id),
        ("SupplySet", "ss1"),
        ("PowerSwitch", switch.id),
        ("IsolationStrategy", iso.id),
        ("RetentionStrategy", ret.id),
        ("LevelShifterStrategy", ls.id),
    ]:
        item = tree._item_by_kind_and_id.get((kind, cid))
        assert item is not None, f"no Power View node found for {kind}"
        view._highlighted_constructs = []
        view._click_highlighted_icons = []

        _click_item(tree, item)

        assert item.isSelected() is True, f"a real click did not select the {kind} row"
        assert (len(view._highlighted_constructs) + len(view._click_highlighted_icons)) > 0, f"{kind} row selection did not highlight its diagram counterpart"


def test_properties_table_row_selection_still_highlights_the_diagram_too(qtbot):
    """Sanity check (explicit request): confirms the SHARED reverse-
    mapping mechanism itself (apply_construct_selection()) is intact --
    the bug was specific to PowerViewTree's own shadowed method name,
    not this shared mechanism."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top"]
    domain = view.create_power_domain(box, "PD1", QColor("#ff0000"), 0.5, None, [], {})
    tab._refresh_diagram_overlay_properties()

    table = tab.properties._tables["PowerDomain"]
    table.resize(400, 300)
    table.show()
    QTest.qWaitForWindowExposed(table)
    rect = table.visualRect(table.model().index(0, 0))
    view._highlighted_constructs = []

    QTest.mouseClick(table.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())

    assert len(view._highlighted_constructs) > 0
