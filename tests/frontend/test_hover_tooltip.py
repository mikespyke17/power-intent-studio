"""Hover-delay info tooltip (explicit request) -- see hover_tooltip.py's
own module docstring for the full design and font-fallback story.
Letters (a)-(e) map to the originating spec's own enumerated test list.

Two testing strategies, deliberately combined: (a)'s own "shows a
tooltip with exactly the specified field set" is proven ONCE, fully
end-to-end through the REAL 1-second QTimer (a hierarchy block, the
simplest case) -- confirming the timer/widget mechanism genuinely
works, not just the content-assembly functions in isolation. Every
OTHER element type's own exact field set is verified by calling
`_hover_tooltip_text_for()` directly (the SAME function
`_show_hover_tooltip()` calls once the real timer fires) -- this keeps
the suite fast (no repeated real 1-second waits) while still exercising
the identical code path the timer would use."""

import gc

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtTest import QTest

from frontend.src.power_intent_view.diagram_tool_palette import DiagramMode
from frontend.src.power_intent_view.hover_tooltip import (
    NONE_TEXT,
    NOT_YET_CONNECTED_TEXT,
    render_tooltip_text,
    resolve_hierarchy_block_fields,
    resolve_isolation_fields,
    resolve_level_shifter_fields,
    resolve_pg_pin_fields,
    resolve_power_switch_fields,
    resolve_retention_fields,
    resolve_supply_port_fields,
)
from frontend.src.power_intent_view.isolation_overlay import IsolationStrategyPlacement
from frontend.src.power_intent_view.level_shifter_overlay import LevelShifterStrategyPlacement
from frontend.src.power_intent_view.power_domain_overlay import PowerDomainDiagramState, PowerDomainPlacement, SupplySetPlacement
from frontend.src.power_intent_view.power_intent_tab import PowerIntentTab
from frontend.src.power_intent_view.retention_overlay import RetentionStrategyPlacement
from frontend.src.power_intent_view.supply_port_overlay import SupplyPortDiagramState, SupplyPortPlacement, SupplyPortState
from frontend.src.shared.mock_backend import build_mock_design


def _deselect_and_collect(tab: PowerIntentTab) -> None:
    if tab.diagram._view.scene() is not None:
        tab.diagram._view.scene().clearSelection()
    gc.collect()


def _fire_hover_delay(view) -> None:
    """Deterministically elapse the 1-second hover delay.

    Audit fix (category 06): these tests used to `qtbot.wait(1100)`
    against a 1000 ms timer -- a 100 ms margin a loaded CI machine can
    plausibly miss, and roughly six seconds of the suite spent purely
    sleeping. Stopping the timer and emitting its own `timeout` runs the
    exact slot the real timer is connected to, through the same
    connection, so the wiring is still under test -- only the waiting is
    removed. The real 1-second delay itself is still verified once, by
    wall clock, in
    test_a_hierarchy_block_tooltip_appears_after_a_real_one_second_pause.
    """
    assert view._hover_tooltip_timer.isActive(), "no pending tooltip to elapse"
    view._hover_tooltip_timer.stop()
    view._hover_tooltip_timer.timeout.emit()


def _viewport_pos(view, item):
    return view.mapFromScene(item.scene_center() if hasattr(item, "scene_center") else item.scenePos())


# --- (a) real end-to-end timer, one representative type (hierarchy block) --


def test_a_hierarchy_block_tooltip_appears_after_a_real_one_second_pause(qtbot):
    """THE deliberate wall-clock test -- the only one left in this file
    (audit fix, category 06). It proves the real QTimer really is a
    one-second delay and really fires on its own; every other test here
    elapses the delay deterministically via _fire_hover_delay(), which
    removes ~6 seconds of pure sleeping and the 100 ms timing margin
    without weakening what's covered. Same "keep one genuine end-to-end
    test, make the edge cases deterministic" precedent this codebase
    already established for QTest.mouseMove()-driven hover tests."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.resize(900, 700)
    tab.show()
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top/u_core"]
    primary = SupplySetPlacement(id="ss1", name="SS_PRIMARY", power="VDD", ground="VSS", owner_path="top")
    view._domain_state.supply_sets["ss1"] = primary
    view._domain_state.domains["d1"] = PowerDomainPlacement(
        id="d1", owner_path="top", name="PD_TOP", color=QColor("#ff0000"), primary_supply_set="ss1"
    )
    pos = view.mapFromScene(box.sceneBoundingRect().center())

    view._update_hover_tooltip(pos)
    assert view._hover_tooltip_widget.isVisible() is False  # not yet -- pending only

    qtbot.wait(1100)

    assert view._hover_tooltip_widget.isVisible() is True
    assert view._hover_tooltip_widget.text() == "Instance: u_core (RTL)\nPower Domain: PD_TOP\nSupply Sets: SS_PRIMARY"
    _deselect_and_collect(tab)


def test_a_hierarchy_block_tooltip_shows_macro_and_none_fields(qtbot):
    """Same real end-to-end timer path, but for a Macro block with no
    Power Domain at all -- confirms the CRITICAL "always show every
    field, 'none' for missing" rule holds through the real timer too,
    not just the pure resolve function."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.resize(900, 700)
    tab.show()
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top/u_ram"]  # the mock design's own macro
    pos = view.mapFromScene(box.sceneBoundingRect().center())

    view._update_hover_tooltip(pos)
    _fire_hover_delay(view)

    assert view._hover_tooltip_widget.isVisible() is True
    assert view._hover_tooltip_widget.text() == "Instance: u_ram (Macro)\nPower Domain: none\nSupply Sets: none"
    _deselect_and_collect(tab)


# --- (a) exact field sets for every other type, via _hover_tooltip_text_for --


def test_a_power_switch_tooltip_exact_fields_including_none_for_off_state(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top"]
    view._domain_state.domains["d1"] = PowerDomainPlacement(id="d1", owner_path="top", name="PD_TOP", color=QColor("#ff0000"))
    target = view.add_supply_port(box, "VDD_SW", QColor("#00ff00"))
    upstream = view.add_supply_port(box, "VDD_UP", QColor("#ff00ff"))
    placement = view.add_power_switch(target, "SW1", "in_pin", "out_pin", "ctrl_pin", "HVT_SWITCH", "ON1", "")
    switch_icon = view._power_switch_icons[placement.id][0]
    view.try_connect_supply_ports(switch_icon, upstream)

    text = view._hover_tooltip_text_for(switch_icon)

    assert text == "Name: SW1\nPower Domain: PD_TOP\nSupply In: VDD_UP\nSupply Out: VDD_SW\nCell Type: HVT_SWITCH\nON State: ON1\nOFF State: none"
    _deselect_and_collect(tab)


def test_a_power_switch_tooltip_shows_none_for_supply_in_when_unconnected(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top"]
    view._domain_state.domains["d1"] = PowerDomainPlacement(id="d1", owner_path="top", name="PD_TOP", color=QColor("#ff0000"))
    target = view.add_supply_port(box, "VDD_SW", QColor("#00ff00"))
    placement = view.add_power_switch(target, "SW1", "in_pin", "out_pin", "ctrl_pin", None, "ON1", "")
    switch_icon = view._power_switch_icons[placement.id][0]

    text = view._hover_tooltip_text_for(switch_icon)

    assert text == "Name: SW1\nPower Domain: PD_TOP\nSupply In: none\nSupply Out: VDD_SW\nCell Type: none\nON State: ON1\nOFF State: none"
    _deselect_and_collect(tab)


def test_a_isolation_tooltip_exact_fields(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top"]
    view._domain_state.domains["d1"] = PowerDomainPlacement(id="d1", owner_path="top", name="PD_TOP", color=QColor("#ff0000"))
    view._domain_state.supply_sets["ss1"] = SupplySetPlacement(id="ss1", name="SS_ISO", power="VDD_ISO", owner_path="top")
    placement = view.create_isolation(box, "ISO1", "d1", "ss1", None, None, "low", "self", "iso_ctrl", "high", ["top/clk"])
    icon = view._isolation_icons[placement.id][0]

    text = view._hover_tooltip_text_for(icon)

    assert text == "Name: ISO1\nPower Domain: PD_TOP\nIsolation Supply Set: SS_ISO\nClamp Value: Low\nIsolation Control: iso_ctrl"
    _deselect_and_collect(tab)


def test_a_level_shifter_tooltip_exact_fields_and_no_power_domain_field(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top"]
    view._domain_state.domains["d1"] = PowerDomainPlacement(id="d1", owner_path="top", name="PD_TOP", color=QColor("#ff0000"))
    placement = view.create_level_shifter(box, "LS1", "d1", "inputs", "low_to_high", "self", None, "LS_CELL", ["top/clk"])
    icon = view._level_shifter_icons[placement.id][0]

    text = view._hover_tooltip_text_for(icon)

    assert text == "Name: LS1\nRule: Low to High\nCell Type: LS_CELL"
    assert "Power Domain" not in text  # explicit request: deliberately omitted for Level Shifter
    _deselect_and_collect(tab)


def test_a_level_shifter_tooltip_none_cell_type(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top"]
    view._domain_state.domains["d1"] = PowerDomainPlacement(id="d1", owner_path="top", name="PD_TOP", color=QColor("#ff0000"))
    placement = view.create_level_shifter(box, "LS1", "d1", "outputs", "high_to_low", "self", None, None, ["top/clk"])
    icon = view._level_shifter_icons[placement.id][0]

    text = view._hover_tooltip_text_for(icon)

    assert text == "Name: LS1\nRule: High to Low\nCell Type: none"
    _deselect_and_collect(tab)


def test_a_retention_tooltip_exact_fields(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top/u_core"]
    view._domain_state.supply_sets["ss_p"] = SupplySetPlacement(id="ss_p", name="SS_P", power="VDD", owner_path="top")
    view._domain_state.supply_sets["ss_r"] = SupplySetPlacement(id="ss_r", name="SS_R", power="VDD_RET", owner_path="top")
    view._domain_state.domains["d1"] = PowerDomainPlacement(id="d1", owner_path="top", name="PD_TOP", color=QColor("#ff0000"))
    placement = view.create_retention_strategy(box, view._domain_state.domains["d1"], "RET1", "ss_p", "ss_r", "save_it", "restore_it", ["top/u_core/out1"], {})
    icon = view._retention_icons[placement.id][0]

    text = view._hover_tooltip_text_for(icon)

    assert text == "Name: RET1\nPower Domain: PD_TOP\nSave Logic: save_it\nRestore Logic: restore_it"
    _deselect_and_collect(tab)


def test_a_supply_port_tooltip_exact_fields(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    from frontend.src.power_intent_view.supply_port_overlay import SupplyPortState

    icon = view.add_supply_port(view._boxes["top"], "VDD", QColor("#ff0000"), states=[SupplyPortState("FULL_ON", "1.0")])

    text = view._hover_tooltip_text_for(icon)

    assert text == "Type: Port\nName: VDD\nPower States: FULL_ON (1.0V)"
    _deselect_and_collect(tab)


def test_a_supply_port_tooltip_none_for_no_states(qtbot):
    """A plain Supply Port CAN have zero authored states (unlike Virtual
    Supply, which requires at least one) -- distinct from Internal
    Supply's own "Not yet connected" case, so this falls back to the
    CRITICAL rule's generic "none" instead."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    icon = view.add_supply_port(view._boxes["top"], "VDD", QColor("#ff0000"))

    text = view._hover_tooltip_text_for(icon)

    assert text == "Type: Port\nName: VDD\nPower States: none"
    _deselect_and_collect(tab)


def test_a_internal_supply_tooltip_shows_not_yet_connected(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    icon = view.add_internal_supply(view._boxes["top/u_core"], "VDD_INT", QColor("#ff0000"))

    text = view._hover_tooltip_text_for(icon)

    assert text == f"Type: Internal\nName: VDD_INT\nPower States: {NOT_YET_CONNECTED_TEXT}"
    _deselect_and_collect(tab)


def test_a_virtual_supply_tooltip_exact_fields(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    from frontend.src.power_intent_view.supply_port_overlay import SupplyPortState

    icon = view.add_virtual_supply(view._boxes["top/u_core"], "VVIRT", QColor("#ff0000"), states=[SupplyPortState("FULL_ON", "0.9")])

    text = view._hover_tooltip_text_for(icon)

    assert text == "Type: Virtual\nName: VVIRT\nPower States: FULL_ON (0.9V)"
    _deselect_and_collect(tab)


def test_a_macro_regular_pg_pin_tooltip_exact_fields(qtbot):
    """Explicit follow-up request (item 2): a macro's own PG pin (the
    mock design's own top/u_ram macro -- VDD, PortDirection.PG,
    voltage="0.80V") now gets its own tooltip too -- Voltage/Type, a
    DIFFERENT field list from a plain/Internal/Virtual Supply's own
    Type/Name/Power States (this is a fixed icon, no user-authored name
    to show as such -- see resolve_pg_pin_fields()'s own docstring)."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    pg_pin = next(i for i in view._supply_port_icons.values() if i.owner_box.path == "top/u_ram" and i.name == "VDD")
    assert pg_pin.fixed is True

    target = view._hover_tooltip_target_at(view.mapFromScene(pg_pin.scenePos()))
    text = view._hover_tooltip_text_for(target)

    assert target is pg_pin
    assert text == "Voltage: 0.80V\nType: PG"
    _deselect_and_collect(tab)


def test_a_macro_internal_pg_pin_tooltip_shows_none_voltage(qtbot):
    """Same macro's own VSS pin (PortDirection.PG_INTERNAL, no voltage
    specified in the mock Liberty data) -- confirms both the "Internal
    PG" Type value and the CRITICAL rule's "none" fallback for a
    genuinely-unset Voltage."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    pg_pin = next(i for i in view._supply_port_icons.values() if i.owner_box.path == "top/u_ram" and i.name == "VSS")
    assert pg_pin.fixed is True
    assert pg_pin.placement.is_internal is True

    target = view._hover_tooltip_target_at(view.mapFromScene(pg_pin.scenePos()))
    text = view._hover_tooltip_text_for(target)

    assert target is pg_pin
    assert text == "Voltage: none\nType: Internal PG"
    _deselect_and_collect(tab)


# --- (b) moving before the delay elapses shows no tooltip -------------------


def test_b_moving_away_before_the_delay_elapses_cancels_the_pending_tooltip(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.resize(900, 700)
    tab.show()
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top/u_core"]
    pos_on_box = view.mapFromScene(box.sceneBoundingRect().center())

    view._update_hover_tooltip(pos_on_box)
    assert view._hover_tooltip_timer.isActive() is True

    # Simulate real movement before the 1s delay elapses -- moving off
    # the element entirely (a position with nothing hoverable).
    from PySide6.QtCore import QPoint

    empty_pos = QPoint(2, 2)  # top-left corner of the viewport, outside any box
    view._update_hover_tooltip(empty_pos)

    assert view._hover_tooltip_timer.isActive() is False
    assert view._hover_tooltip_widget.isVisible() is False

    # Nothing pending to elapse -- the cancel already stopped the timer,
    # which _fire_hover_delay() would assert on. Just re-confirm.
    assert view._hover_tooltip_widget.isVisible() is False
    _deselect_and_collect(tab)


def test_b_moving_to_a_different_element_restarts_the_delay_not_carries_it_over(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.resize(900, 700)
    tab.show()
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box_a = view._boxes["top/u_core"]
    box_b = view._boxes["top/u_dbg"]
    pos_a = view.mapFromScene(box_a.sceneBoundingRect().center())
    pos_b = view.mapFromScene(box_b.sceneBoundingRect().center())

    view._update_hover_tooltip(pos_a)
    assert view._hover_tooltip_timer.isActive() is True
    first_target = view._hover_tooltip_target

    view._update_hover_tooltip(pos_b)  # moved to a different element

    # The delay RESTARTS rather than carrying over: the pending target is
    # now box_b, and the timer is freshly running (Qt restarts a
    # single-shot timer on start(), so remainingTime() is back at full).
    # Asserted structurally instead of by sleeping 700ms twice -- the old
    # form left only a 100ms margin and its own comment trailed off
    # mid-reasoning about what the arithmetic actually proved.
    assert view._hover_tooltip_target is not first_target
    assert view._hover_tooltip_timer.isActive() is True
    assert view._hover_tooltip_timer.remainingTime() > 900
    assert view._hover_tooltip_widget.isVisible() is False  # still nothing shown yet

    _fire_hover_delay(view)
    assert view._hover_tooltip_widget.isVisible() is True
    assert "u_dbg" in view._hover_tooltip_widget.text()
    _deselect_and_collect(tab)


# --- (c) tooltip disappears when the cursor leaves the element --------------


def test_c_tooltip_disappears_when_the_cursor_moves_off_the_element(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.resize(900, 700)
    tab.show()
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top/u_core"]
    pos_on_box = view.mapFromScene(box.sceneBoundingRect().center())

    view._update_hover_tooltip(pos_on_box)
    _fire_hover_delay(view)
    assert view._hover_tooltip_widget.isVisible() is True

    from PySide6.QtCore import QPoint

    view._update_hover_tooltip(QPoint(2, 2))  # moved off the element entirely

    assert view._hover_tooltip_widget.isVisible() is False
    _deselect_and_collect(tab)


def test_c_tooltip_disappears_on_viewport_leave_event(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.resize(900, 700)
    tab.show()
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top/u_core"]
    pos_on_box = view.mapFromScene(box.sceneBoundingRect().center())

    view._update_hover_tooltip(pos_on_box)
    _fire_hover_delay(view)
    assert view._hover_tooltip_widget.isVisible() is True

    view._hide_hover_tooltip()  # the same call leaveEvent() itself makes

    assert view._hover_tooltip_widget.isVisible() is False
    assert view._hover_tooltip_timer.isActive() is False
    _deselect_and_collect(tab)


# --- (d) multi-instance consistency -----------------------------------------


def test_d_multi_hierarchy_isolation_shows_identical_content_for_every_instance(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view._domain_state.domains["d1"] = PowerDomainPlacement(
        id="d1", owner_paths=["top/u_core", "top/u_dbg"], owner_path="top/u_core", name="PD_MULTI", color=QColor("#ff0000")
    )
    view._domain_state.supply_sets["ss1"] = SupplySetPlacement(id="ss1", name="SS_ISO", power="VDD_ISO", owner_path="top/u_core")
    placement = view.create_isolation(
        view._boxes["top/u_core"], "ISO1", "d1", "ss1", None, None, "high", "self", "iso_ctrl", "low",
        ["top/u_core/clk", "top/u_dbg/clk"],
    )
    icons = view._isolation_icons[placement.id]
    assert len(icons) == 2  # genuinely two distinct on-screen instances

    text_0 = view._hover_tooltip_text_for(icons[0])
    text_1 = view._hover_tooltip_text_for(icons[1])

    assert text_0 == text_1
    assert text_0 == "Name: ISO1\nPower Domain: PD_MULTI\nIsolation Supply Set: SS_ISO\nClamp Value: High\nIsolation Control: iso_ctrl"
    _deselect_and_collect(tab)


def test_d_power_switch_shared_fields_identical_across_instances_but_supply_in_reflects_the_hovered_one(qtbot):
    """Confirmed with the user: Name/Power Domain/Cell Type/ON-OFF State
    stay identical across every replica (one shared record); Supply In
    is a genuine per-instance electrical connection and reflects the
    ACTUALLY-HOVERED instance's own real connection, not a fixed
    canonical one -- this test deliberately gives the two replicas
    DIFFERENT Supply In connections to prove that.

    NOTE: try_connect_supply_ports() itself auto-REPLICATES a manual
    connection across every other member hierarchy of a multi-hierarchy
    switch's own domain (an existing, deliberate feature -- see that
    method's own docstring), so drawing a connection through the public
    API alone can't produce two genuinely diverging replicas here. This
    test instead appends a second, independent connector directly to
    `supply_connector_placements` (a plain list of (id, id) pairs) to
    construct the edge case by hand -- still a legitimate state the data
    model allows (e.g. a manually-drawn connector on one replica whose
    OTHER replica's own analogous connection never got created/adopted
    for some other reason), which is exactly the scenario this tooltip
    rule needs to handle correctly."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view._domain_state.domains["d1"] = PowerDomainPlacement(
        id="d1", owner_paths=["top/u_core", "top/u_dbg"], owner_path="top/u_core", name="PD_MULTI", color=QColor("#ff0000")
    )
    target_a = view.add_internal_supply(view._boxes["top/u_core"], "VDD_INT", QColor("#00ff00"))
    target_b = view.add_internal_supply(view._boxes["top/u_dbg"], "VDD_INT", QColor("#00ff00"))
    placement = view.add_power_switch(target_a, "SW1", "in_pin", "out_pin", "ctrl_pin", "CELL", "ON1", "OFF1")
    icons = view._power_switch_icons[placement.id]
    assert len(icons) == 2
    icon_a = next(i for i in icons if i.target_icon is target_a)
    icon_b = next(i for i in icons if i.target_icon is target_b)
    upstream_a = view.add_supply_port(view._boxes["top"], "VDD_A", QColor("#123456"))
    view._shared_state.supply_connector_placements.append((icon_a.key, upstream_a.key))
    # icon_b's own Supply In is deliberately left unconnected.

    text_a = view._hover_tooltip_text_for(icon_a)
    text_b = view._hover_tooltip_text_for(icon_b)

    for text in (text_a, text_b):
        assert "Name: SW1" in text
        assert "Power Domain: PD_MULTI" in text
        assert "Cell Type: CELL" in text
        assert "ON State: ON1" in text
        assert "OFF State: OFF1" in text
    assert "Supply In: VDD_A" in text_a
    assert "Supply In: none" in text_b
    _deselect_and_collect(tab)


# --- (e) no interference with an active tool-mode interaction ---------------


def test_e_pending_tooltip_does_not_block_drawing_a_power_net(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.resize(900, 700)
    tab.show()
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    outer = view.add_supply_port(view._boxes["top"], "VDD", QColor("#ff0000"))
    inner = view.add_supply_port(view._boxes["top/u_core"], "VDD_CORE", QColor("#00ff00"))
    view.set_mode(DiagramMode.SUPPLY_NET)
    start = _viewport_pos(view, outer.handles["bottom"])
    end = _viewport_pos(view, inner.handles["top"])

    # Arm a pending tooltip over the source handle first (mirrors a real
    # user pausing briefly before starting to drag).
    view._update_hover_tooltip(start)
    assert view._hover_tooltip_timer.isActive() is True

    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
    assert view._connector_drag_line is not None  # the drag started despite the pending tooltip
    QTest.mouseMove(view.viewport(), end)  # real mouseMoveEvent -- now also runs _update_hover_tooltip() unconditionally
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, end)

    assert len(view._supply_connectors) == 1  # the connection still completed
    assert view._connector_drag_line is None
    _deselect_and_collect(tab)


def test_e_tooltip_mechanism_active_in_every_tool_mode_without_crashing(qtbot):
    """Spot-check across every DiagramMode -- purely informational, must
    never raise/crash/misbehave regardless of which tool is active."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.resize(900, 700)
    tab.show()
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top/u_core"]
    pos = view.mapFromScene(box.sceneBoundingRect().center())

    for mode in DiagramMode:
        view.set_mode(mode)
        view._update_hover_tooltip(pos)  # must not raise in any mode
    _deselect_and_collect(tab)


# --- pure resolve_*_fields()/render_tooltip_text() unit tests ---------------
# Independent of any live QGraphicsView -- the exact CRITICAL "always show
# every field, 'none' (or 'Not yet connected') for missing" rule lives here,
# structurally, in each function's own fixed-length return list.


def test_render_tooltip_text_joins_label_value_pairs():
    assert render_tooltip_text([("Name", "SW1"), ("Power Domain", "PD1")]) == "Name: SW1\nPower Domain: PD1"


class _FakeBox:
    def __init__(self, path, is_black_box):
        self.path = path
        self.definition = type("D", (), {"is_black_box": is_black_box})()


def test_resolve_hierarchy_block_fields_no_domain():
    box = _FakeBox("top/u_core", is_black_box=False)
    domain_state = PowerDomainDiagramState()

    fields = resolve_hierarchy_block_fields(box, domain_state)

    assert fields == [("Instance", "u_core (RTL)"), ("Power Domain", NONE_TEXT), ("Supply Sets", NONE_TEXT)]


def test_resolve_hierarchy_block_fields_macro_with_domain_and_supply_sets():
    box = _FakeBox("top/u_ram", is_black_box=True)
    domain_state = PowerDomainDiagramState(
        supply_sets={
            "ss_p": SupplySetPlacement(id="ss_p", name="SS_PRIMARY", power="VDD", owner_path="top"),
            "ss_s": SupplySetPlacement(id="ss_s", name="SS_SECONDARY", power="VDD_RET", owner_path="top"),
        },
        domains={"d1": PowerDomainPlacement(id="d1", owner_path="top", name="PD_TOP", color=QColor("#ff0000"), primary_supply_set="ss_p", secondary_supply_sets=["ss_s"])},
    )

    fields = resolve_hierarchy_block_fields(box, domain_state)

    assert fields == [("Instance", "u_ram (Macro)"), ("Power Domain", "PD_TOP"), ("Supply Sets", "SS_PRIMARY, SS_SECONDARY")]


def test_resolve_power_switch_fields_all_none_when_unset():
    from frontend.src.power_intent_view.power_switch_overlay import PowerSwitchPlacement

    placement = PowerSwitchPlacement(id="p1", name="SW1", power_domain_id="", target_id="t1", owner_path="top", cell_type=None, on_state="", off_state="")
    domain_state = PowerDomainDiagramState()

    fields = resolve_power_switch_fields(placement, domain_state, supply_in=None, supply_out=None)

    assert fields == [
        ("Name", "SW1"),
        ("Power Domain", NONE_TEXT),
        ("Supply In", NONE_TEXT),
        ("Supply Out", NONE_TEXT),
        ("Cell Type", NONE_TEXT),
        ("ON State", NONE_TEXT),
        ("OFF State", NONE_TEXT),
    ]


def test_resolve_isolation_fields_exact():
    strategy = IsolationStrategyPlacement(
        id="i1", name="ISO1", power_domain_id="d1", isolation_supply_set_id="ss1",
        clamp_value="keep", isolation_control="ctrl", isolation_sense="high", owner_path="top",
    )
    domain_state = PowerDomainDiagramState(
        domains={"d1": PowerDomainPlacement(id="d1", owner_path="top", name="PD1", color=QColor("#ff0000"))},
        supply_sets={"ss1": SupplySetPlacement(id="ss1", name="SS1", power="VDD", owner_path="top")},
    )

    fields = resolve_isolation_fields(strategy, domain_state)

    assert fields == [
        ("Name", "ISO1"),
        ("Power Domain", "PD1"),
        ("Isolation Supply Set", "SS1"),
        ("Clamp Value", "Keep"),
        ("Isolation Control", "ctrl"),
    ]


def test_resolve_level_shifter_fields_has_no_power_domain_key():
    strategy = LevelShifterStrategyPlacement(id="l1", name="LS1", power_domain_id="d1", applies_to="both", rule="both", cell_type=None, owner_path="top")

    fields = resolve_level_shifter_fields(strategy)

    keys = [k for k, _v in fields]
    assert keys == ["Name", "Rule", "Cell Type"]
    assert ("Power Domain", "d1") not in fields


def test_resolve_retention_fields_exact():
    strategy = RetentionStrategyPlacement(
        id="r1", name="RET1", power_domain_id="d1", primary_supply_set_id="ss1", retention_supply_set_id="ss2",
        save_logic="save_it", restore_logic="restore_it", owner_path="top",
    )
    domain_state = PowerDomainDiagramState(domains={"d1": PowerDomainPlacement(id="d1", owner_path="top", name="PD1", color=QColor("#ff0000"))})

    fields = resolve_retention_fields(strategy, domain_state)

    assert fields == [("Name", "RET1"), ("Power Domain", "PD1"), ("Save Logic", "save_it"), ("Restore Logic", "restore_it")]


def test_resolve_supply_port_fields_multiple_states_comma_joined():
    placement = SupplyPortPlacement(
        id="p1", owner_path="top", name="VDD", color=QColor("#ff0000"),
        states=[SupplyPortState("FULL_ON", "1.0"), SupplyPortState("OFF")],
    )
    supply_state = SupplyPortDiagramState(supply_port_placements={"p1": placement})

    fields = resolve_supply_port_fields(placement, supply_state, switches={})

    assert fields == [("Type", "Port"), ("Name", "VDD"), ("Power States", "FULL_ON (1.0V), OFF")]


def test_resolve_supply_port_fields_internal_supply_not_yet_connected():
    placement = SupplyPortPlacement(id="p1", owner_path="top/u_core", name="VDD_INT", color=QColor("#ff0000"), is_internal_supply=True)
    supply_state = SupplyPortDiagramState(supply_port_placements={"p1": placement})

    fields = resolve_supply_port_fields(placement, supply_state, switches={})

    assert fields == [("Type", "Internal"), ("Name", "VDD_INT"), ("Power States", NOT_YET_CONNECTED_TEXT)]


def test_resolve_pg_pin_fields_with_voltage():
    placement = SupplyPortPlacement(id="pg1", owner_path="top/u_ram", name="VDD", color=QColor("#9e9e9e"), fixed=True, voltage="0.80V", is_internal=False)

    fields = resolve_pg_pin_fields(placement)

    assert fields == [("Voltage", "0.80V"), ("Type", "PG")]


def test_resolve_pg_pin_fields_internal_no_voltage():
    placement = SupplyPortPlacement(id="pg2", owner_path="top/u_ram", name="VSS", color=QColor("#9e9e9e"), fixed=True, is_internal=True)

    fields = resolve_pg_pin_fields(placement)

    assert fields == [("Voltage", NONE_TEXT), ("Type", "Internal PG")]
