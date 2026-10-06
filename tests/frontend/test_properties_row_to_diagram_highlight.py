"""Properties-table-row-selection -> diagram highlight (explicit
request): the reverse direction of the diagram-click "group highlight"
mechanism power_intent_diagram_view.py's own _on_scene_selection_changed()
already establishes. See apply_construct_selection()'s own docstring
for the full "why" -- this exercises _resolve_diagram_only_box_group()/
_resolve_diagram_only_icon_group(), the two new resolvers that close
the gap `self._index` (real-backend-only) left for every diagram-only
entity (Add Power Supply/Create Power Domain/Retention/Isolation/Power
Switch/Power Switch Cell).

Deliberately calls `view.apply_construct_selection(kind, id)` directly
(the SAME method PowerIntentSelectionController.selectionChanged
already drives) rather than going through a real QTableWidget click --
this is the "directly-testable core" precedent every dialog/prompt in
this app already follows, and properties_panel.py's own row-selection
wiring (`_on_row_selected` -> `self._controller.selectionChanged.emit(...)`)
is already covered by test_selection_sync.py.
"""

from PySide6.QtGui import QColor

from frontend.src.power_intent_view.diagram_tool_palette import DiagramMode
from frontend.src.power_intent_view.power_domain_overlay import PowerDomainPlacement, SupplySetPlacement
from frontend.src.power_intent_view.power_intent_tab import PowerIntentTab
from frontend.src.shared.mock_backend import build_mock_design
from frontend.src.shared.models.rtl import Definition, Design, Instance, SequentialElement, SourceLanguage


def _three_sibling_leaf_design() -> Design:
    """top -> u_a/u_b/u_c, three SIBLING leaves -- same fixture shape
    test_power_intent_retention.py's own multi-block test already
    establishes, reused here (not imported, small/self-contained,
    same "duplication over a forced cross-file import" precedent this
    app already follows elsewhere)."""
    leaf_a = Definition(definition_id="leaf_a", base_name="leaf_a_def", language=SourceLanguage.SYSTEMVERILOG, sequential_elements=[SequentialElement(output_signal="regA", is_latch=False)])
    leaf_b = Definition(definition_id="leaf_b", base_name="leaf_b_def", language=SourceLanguage.SYSTEMVERILOG, sequential_elements=[SequentialElement(output_signal="regB", is_latch=False)])
    leaf_c = Definition(definition_id="leaf_c", base_name="leaf_c_def", language=SourceLanguage.SYSTEMVERILOG, sequential_elements=[SequentialElement(output_signal="regC", is_latch=False)])
    top = Definition(
        definition_id="top",
        base_name="top",
        language=SourceLanguage.SYSTEMVERILOG,
        instances=[Instance(name="u_a", definition_id="leaf_a"), Instance(name="u_b", definition_id="leaf_b"), Instance(name="u_c", definition_id="leaf_c")],
    )
    return Design(definitions={"top": top, "leaf_a": leaf_a, "leaf_b": leaf_b, "leaf_c": leaf_c}, top="top")


# --- (a) single-instance entity ---------------------------------------------


def test_selecting_a_supply_port_row_highlights_that_one_diagram_icon(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    port = view.add_supply_port(view._boxes["top"], "VDD1", QColor("#123456"))

    view.apply_construct_selection("SupplyPort", port.key)

    assert view._click_highlighted_icons == [port]
    assert port._click_highlighted is True


def test_selecting_a_power_domain_row_highlights_its_own_boundary_box(qtbot):
    """Power Domain has no glyph of its own (box recoloring only) --
    highlights via InstanceBoxItem.set_highlighted(), same mechanism/
    list _highlighted_constructs already uses for the legacy
    real-backend case."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view._domain_state.domains["d1"] = PowerDomainPlacement(id="d1", owner_path="top/u_core", name="PD1", color=QColor("#ff0000"))

    view.apply_construct_selection("PowerDomain", "d1")

    assert view._highlighted_constructs == [view._boxes["top/u_core"]]


def test_selecting_a_supply_set_row_highlights_the_box_it_was_composed_on(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view._domain_state.supply_sets["ss1"] = SupplySetPlacement(id="ss1", name="SS1", power="VDD", owner_paths=["top/u_dbg"])

    view.apply_construct_selection("SupplySet", "ss1")

    assert view._highlighted_constructs == [view._boxes["top/u_dbg"]]


def test_selecting_an_isolation_row_with_one_attachment_highlights_that_icon(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top"]
    view._domain_state.domains["d1"] = PowerDomainPlacement(id="d1", owner_path="top", name="PD1", color=QColor("#ff0000"))
    view._domain_state.supply_sets["ss1"] = SupplySetPlacement(id="ss1", name="SS1", power="VDD", owner_paths=["top"])
    placement = view.create_isolation(box, "ISO1", "d1", "ss1", None, None, "low", "self", "ctrl", "high", ["top/clk"])

    view.apply_construct_selection("IsolationStrategy", placement.id)

    assert set(view._click_highlighted_icons) == set(view._isolation_icons[placement.id])
    assert len(view._click_highlighted_icons) == 1


# --- (b) multi-hierarchy / multi-block entities ------------------------------


def test_selecting_a_multi_block_retention_row_highlights_all_3_diagram_instances(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(_three_sibling_leaf_design())
    view = tab.diagram._view
    primary = SupplySetPlacement(id="ss_primary", name="SS_PRIMARY", power="VDD", owner_path="top")
    retention = SupplySetPlacement(id="ss_retention", name="SS_RETENTION", power="VDD_RET", owner_path="top")
    view._domain_state.supply_sets[primary.id] = primary
    view._domain_state.supply_sets[retention.id] = retention
    domain = PowerDomainPlacement(id="d1", owner_path="top", name="PD_TOP", color=QColor("#ff0000"), primary_supply_set=primary.id, secondary_supply_sets=[retention.id])
    view._domain_state.domains["d1"] = domain
    placement = view.create_retention_strategy(
        view._boxes["top"], domain, "RET1", primary.id, retention.id, "save", "restore", ["top/u_a/regA", "top/u_b/regB", "top/u_c/regC"], {}
    )
    icons = view._retention_icons[placement.id]
    assert {i.owner_box.path for i in icons} == {"top/u_a", "top/u_b", "top/u_c"}  # sanity: genuinely 3 different blocks

    view.apply_construct_selection("RetentionStrategy", placement.id)

    assert set(view._click_highlighted_icons) == set(icons)
    assert len(view._click_highlighted_icons) == 3
    assert all(i._click_highlighted for i in icons)


def test_selecting_a_multi_hierarchy_power_domain_row_highlights_every_member_box(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    domain = PowerDomainPlacement(
        id="d1", owner_path="top/u_core", name="PD1", color=QColor("#ff0000"), owner_paths=["top/u_core", "top/u_dbg", "top/u_fwd"]
    )
    view._domain_state.domains["d1"] = domain

    view.apply_construct_selection("PowerDomain", "d1")

    assert set(view._highlighted_constructs) == {view._boxes["top/u_core"], view._boxes["top/u_dbg"], view._boxes["top/u_fwd"]}


def test_selecting_a_replicated_power_switch_row_highlights_every_replica(qtbot):
    """A Power Switch replicated across a multi-hierarchy Power Domain
    (items 9/10) still has ONE Properties table row -- selecting it
    must highlight every one of its replicated icons."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    member_paths = ["top/u_core", "top/u_dbg"]
    domain = PowerDomainPlacement(id="d1", owner_path="top/u_core", name="PD_MULTI", color=QColor("#ff0000"), owner_paths=member_paths)
    view._domain_state.domains["d1"] = domain
    icons = {p: view.add_internal_supply(view._boxes[p], "VDD_SW", QColor("#123456")) for p in member_paths}
    placement = view.add_power_switch(icons["top/u_core"], "SW1", "in", "out", "ctrl", None, "", "")
    switch_icons = view._power_switch_icons[placement.id]
    assert len(switch_icons) == 2  # sanity: genuinely replicated

    view.apply_construct_selection("PowerSwitch", placement.id)

    assert set(view._click_highlighted_icons) == set(switch_icons)


# --- (c) selecting a different row clears the previous highlight ------------


def test_selecting_a_different_row_clears_the_previous_highlight(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    port1 = view.add_supply_port(view._boxes["top"], "VDD1", QColor("#123456"))
    port2 = view.add_supply_port(view._boxes["top/u_core"], "VDD2", QColor("#654321"))
    view.apply_construct_selection("SupplyPort", port1.key)
    assert port1._click_highlighted is True

    view.apply_construct_selection("SupplyPort", port2.key)

    assert port1._click_highlighted is False
    assert port2._click_highlighted is True
    assert view._click_highlighted_icons == [port2]


def test_selecting_a_different_row_clears_a_previous_box_highlight_too(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view._domain_state.domains["d1"] = PowerDomainPlacement(id="d1", owner_path="top/u_core", name="PD1", color=QColor("#ff0000"))
    view._domain_state.domains["d2"] = PowerDomainPlacement(id="d2", owner_path="top/u_dbg", name="PD2", color=QColor("#00ff00"))
    view.apply_construct_selection("PowerDomain", "d1")
    assert view._highlighted_constructs == [view._boxes["top/u_core"]]

    view.apply_construct_selection("PowerDomain", "d2")

    assert view._highlighted_constructs == [view._boxes["top/u_dbg"]]


def test_selecting_an_unknown_construct_clears_highlight_without_error(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    port = view.add_supply_port(view._boxes["top"], "VDD1", QColor("#123456"))
    view.apply_construct_selection("SupplyPort", port.key)
    assert port._click_highlighted is True

    view.apply_construct_selection("SupplyPort", "nonexistent-id")  # must not raise

    assert port._click_highlighted is False
    assert view._click_highlighted_icons == []


# --- (d) applies regardless of active diagram tool mode ----------------------


def test_highlight_applies_regardless_of_active_tool_mode(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    port = view.add_supply_port(view._boxes["top"], "VDD1", QColor("#123456"))
    view.set_mode(DiagramMode.POWER_SWITCH)

    view.apply_construct_selection("SupplyPort", port.key)

    assert port._click_highlighted is True
    assert view._mode == DiagramMode.POWER_SWITCH  # selecting a properties row never changes the active tool


def test_highlight_applies_in_isolation_mode_too(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view._domain_state.domains["d1"] = PowerDomainPlacement(id="d1", owner_path="top/u_core", name="PD1", color=QColor("#ff0000"))
    view.set_mode(DiagramMode.ISOLATION_CELL)

    view.apply_construct_selection("PowerDomain", "d1")

    assert view._highlighted_constructs == [view._boxes["top/u_core"]]
    assert view._mode == DiagramMode.ISOLATION_CELL
