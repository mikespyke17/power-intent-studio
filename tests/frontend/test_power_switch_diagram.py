"""Power Switch -- diagram rendering, hover+click placement, validation
enforcement, and delete/cascade (Stage 4 of the staged commit plan).
See power_switch_overlay.py's own module docstring for the resolved
architecture (bottom anchor = automatic/"Supply Out", top anchor =
manually-drawn Supply Net connector/"Supply In")."""

import gc

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMenu, QMessageBox

from frontend.src.power_intent_view.diagram_tool_palette import DiagramMode
from frontend.src.power_intent_view.power_domain_overlay import PowerDomainPlacement, SupplySetPlacement
from frontend.src.power_intent_view.power_intent_tab import PowerIntentTab
from frontend.src.power_intent_view.power_switch_overlay import (
    SWITCH_OFF_STATE_NAME,
    PowerSwitchIconItem,
    describe_inherited_states,
    find_switch_for_target,
    resolve_effective_states,
)
from frontend.src.power_intent_view.retention_overlay import RetentionStrategyPlacement
from frontend.src.power_intent_view.supply_port_overlay import SupplyPortState
from frontend.src.shared.mock_backend import build_mock_design
from frontend.src.shared.models.rtl import Definition, Design, Instance, SourceLanguage
from frontend.src.shared.widgets import message_box


def _viewport_pos(view, item):
    return view.mapFromScene(item.scene_center() if hasattr(item, "scene_center") else item.scenePos())


def _deselect_and_collect(tab: PowerIntentTab) -> None:
    if tab.diagram._view.scene() is not None:
        tab.diagram._view.scene().clearSelection()
    gc.collect()


def _tab_with_domain_and_port(qtbot):
    """A ready-to-switch fixture: mock design, a Power Domain on "top",
    and a real Supply Port icon placed there (add_supply_port(), the
    same directly-testable core every other supply-port test already
    uses)."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top"]
    view._domain_state.domains["d1"] = PowerDomainPlacement(id="d1", owner_path="top", name="PD_TOP", color=QColor("#ff0000"))
    port_icon = view.add_supply_port(box, "VDD_ALWAYS_ON", QColor("#00ff00"))
    return tab, view, port_icon


# --- validation rule B: Power Domain must exist first -----------------------


def test_validate_power_switch_target_passes_when_domain_exists(qtbot):
    tab, view, port_icon = _tab_with_domain_and_port(qtbot)
    assert view._validate_power_switch_target(port_icon) is None
    _deselect_and_collect(tab)


def test_validate_power_switch_target_fails_with_no_domain(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top"]
    port_icon = view.add_supply_port(box, "VDD_ALWAYS_ON", QColor("#00ff00"))  # no Power Domain ever assigned

    error = view._validate_power_switch_target(port_icon)

    assert error == view._POWER_SWITCH_NO_DOMAIN_ERROR
    _deselect_and_collect(tab)


def test_add_power_switch_raises_when_no_power_domain(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top"]
    port_icon = view.add_supply_port(box, "VDD_ALWAYS_ON", QColor("#00ff00"))

    try:
        view.add_power_switch(port_icon, "SW1", "in", "out", "ctrl", None, "", "")
        assert False, "expected ValueError"
    except ValueError as exc:
        assert str(exc) == view._POWER_SWITCH_NO_DOMAIN_ERROR
    assert view._power_switch_state.switches == {}
    _deselect_and_collect(tab)


# --- validation rule A: max ONE Power Switch per Supply Port/Internal Supply -


def test_add_power_switch_succeeds_on_a_valid_target(qtbot):
    tab, view, port_icon = _tab_with_domain_and_port(qtbot)

    placement = view.add_power_switch(port_icon, "SW1", "in", "out", "ctrl", "cell1", "sleep_b", "~sleep_b")

    assert placement.name == "SW1"
    assert placement.target_id == port_icon.key
    assert placement.owner_path == "top"
    assert placement.power_domain_id == "d1"
    assert placement.input_supply_port == "in"
    assert placement.output_supply_port == "out"
    assert placement.control_port == "ctrl"
    assert placement.cell_type == "cell1"
    assert placement.on_state == "sleep_b"
    assert placement.off_state == "~sleep_b"
    assert view._power_switch_state.switches[placement.id] == placement
    _deselect_and_collect(tab)


def test_power_switch_aligns_with_its_target_after_prior_element_insertions(qtbot):
    """Bug fix, explicit request: after one or more PRIOR element
    insertions have already caused a block to grow/reposition (the
    "make room" mechanism -- here, an earlier Internal Supply growing
    top/u_dbg's own height), inserting a NEW Power Switch targeting a
    port on that SAME block must still render directly above the
    target's CURRENT (post-resize) anchor, not a stale pre-resize one.

    Root cause (confirmed empirically, not assumed): a Supply Port
    icon's own local edge-anchored position is computed ONCE at
    construction time from its owner box's rect() -- a LATER resize of
    that same box (from an unrelated, later insertion) left it stale
    until something explicitly re-derived it. SupplyPortIconItem now
    registers a resnap_to_owner_edge() callback on its owner box's
    on_resize_callbacks (hierarchy_scene_builder.py's InstanceBoxItem)
    specifically to keep this correct without cascading into an
    EXISTING net/switch recompute."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top/u_dbg"]
    view._domain_state.domains["d1"] = PowerDomainPlacement(id="d1", owner_path="top/u_dbg", name="PD", color=QColor("#ff0000"))

    # A real, prior "make room" trigger that grows box's own height.
    view.add_internal_supply(box, "VDD_EARLIER", QColor("#00ffff"))
    # The switch's own target, added afterward (also grows box further).
    port_icon = view.add_supply_port(box, "VDD_DBG", QColor("#123456"))

    placement = view.add_power_switch(port_icon, "SW1", "in", "out", "ctrl", None, "", "")

    switch_icon = view._power_switch_icons[placement.id][0]
    assert switch_icon.scenePos().x() == port_icon.scenePos().x()
    _deselect_and_collect(tab)


def test_add_power_switch_raises_a_second_time_on_the_same_target(qtbot):
    tab, view, port_icon = _tab_with_domain_and_port(qtbot)
    view.add_power_switch(port_icon, "SW1", "in", "out", "ctrl", None, "", "")

    try:
        view.add_power_switch(port_icon, "SW2", "in", "out", "ctrl", None, "", "")
        assert False, "expected ValueError"
    except ValueError as exc:
        assert str(exc) == view._POWER_SWITCH_ALREADY_EXISTS_ERROR
    assert len(view._power_switch_state.switches) == 1  # the second attempt never got added
    _deselect_and_collect(tab)


def test_find_switch_for_target_reflects_the_real_state_after_add(qtbot):
    tab, view, port_icon = _tab_with_domain_and_port(qtbot)
    placement = view.add_power_switch(port_icon, "SW1", "in", "out", "ctrl", None, "", "")

    found = find_switch_for_target(view._power_switch_state.switches, port_icon.key)

    assert found is not None
    assert found.id == placement.id
    _deselect_and_collect(tab)


# --- diagram rendering: icon positioning + auto bottom connector -----------


def test_power_switch_icon_is_materialized_in_the_scene(qtbot):
    """Deliberately a SCENE-LEVEL item (no QGraphicsItem parent) --
    same "must not be a nested descendant that a sibling box can cover"
    fix as SupplyPortConnectorItem's own unparented design -- see
    PowerSwitchIconItem's own docstring for the real bug class this
    avoids."""
    tab, view, port_icon = _tab_with_domain_and_port(qtbot)
    placement = view.add_power_switch(port_icon, "SW1", "in", "out", "ctrl", None, "", "")

    switch_icon = view._power_switch_icons[placement.id][0]

    assert isinstance(switch_icon, PowerSwitchIconItem)
    assert switch_icon.parentItem() is None
    assert switch_icon.scene() is view.scene()
    assert switch_icon in view.scene().items()
    assert switch_icon.isVisible() is True
    _deselect_and_collect(tab)


def test_power_switch_z_value_exceeds_every_hierarchy_box(qtbot):
    """Confirms the actual mechanism that keeps this icon from ever
    being covered by an unrelated sibling box: its own Z compared
    DIRECTLY against Z_INSTANCE_BOX at the shared (scene) context, not
    buried several ancestors deep where a high LOCAL Z would never even
    be consulted."""
    from frontend.src.shared.diagram.hierarchy_scene_builder import Z_INSTANCE_BOX

    tab, view, port_icon = _tab_with_domain_and_port(qtbot)
    placement = view.add_power_switch(port_icon, "SW1", "in", "out", "ctrl", None, "", "")
    switch_icon = view._power_switch_icons[placement.id][0]

    assert switch_icon.zValue() > Z_INSTANCE_BOX
    _deselect_and_collect(tab)


def test_power_switch_position_tracks_its_target_when_dragged(qtbot):
    """The whole reason update_position()/on_move_callbacks exist:
    without QGraphicsItem parenting doing this automatically anymore,
    dragging the target along its own box edge must still visibly carry
    the switch along with it."""
    tab, view, port_icon = _tab_with_domain_and_port(qtbot)
    placement = view.add_power_switch(port_icon, "SW1", "in", "out", "ctrl", None, "", "")
    switch_icon = view._power_switch_icons[placement.id][0]
    pos_before = switch_icon.scenePos()

    box = view._boxes["top"]
    port_icon.setPos(box.rect().right(), box.rect().center().y())  # drag to the right edge

    assert switch_icon.scenePos() != pos_before
    assert switch_icon.scenePos().y() < port_icon.scenePos().y()  # still "above" after the move
    _deselect_and_collect(tab)


def test_deleting_the_switch_unregisters_its_move_callback(qtbot):
    """Real, confirmed hazard this closes: a scene-level icon's own
    update_position() staying registered on its (still-live) target
    after the icon itself is removed would crash on the next target
    move, calling a method on an already-deleted C++ object -- same
    shiboken-object-lifetime class of bug this codebase has hit before."""
    tab, view, port_icon = _tab_with_domain_and_port(qtbot)
    placement = view.add_power_switch(port_icon, "SW1", "in", "out", "ctrl", None, "", "")
    switch_icon = view._power_switch_icons[placement.id][0]

    view.delete_power_switch(placement.id)

    assert switch_icon.update_position not in port_icon.on_move_callbacks
    box = view._boxes["top"]
    port_icon.setPos(box.rect().left(), box.rect().center().y())  # must not raise
    _deselect_and_collect(tab)


def test_power_switch_icon_is_rendered_above_its_target(qtbot):
    """"Rendered ABOVE the Supply Port/Internal Supply it's attached to"
    -- a smaller scene Y (Qt's own top-left-origin coordinate system) is
    "above" visually."""
    tab, view, port_icon = _tab_with_domain_and_port(qtbot)
    placement = view.add_power_switch(port_icon, "SW1", "in", "out", "ctrl", None, "", "")
    switch_icon = view._power_switch_icons[placement.id][0]

    assert switch_icon.scenePos().y() < port_icon.scenePos().y()
    assert switch_icon.scenePos().x() == port_icon.scenePos().x()
    _deselect_and_collect(tab)


def test_power_switch_icon_has_top_and_bottom_handles(qtbot):
    tab, view, port_icon = _tab_with_domain_and_port(qtbot)
    placement = view.add_power_switch(port_icon, "SW1", "in", "out", "ctrl", None, "", "")
    switch_icon = view._power_switch_icons[placement.id][0]

    assert set(switch_icon.handles.keys()) == {"top", "bottom"}
    _deselect_and_collect(tab)


def test_auto_bottom_connector_connects_switch_and_target(qtbot):
    tab, view, port_icon = _tab_with_domain_and_port(qtbot)
    placement = view.add_power_switch(port_icon, "SW1", "in", "out", "ctrl", None, "", "")
    switch_icon = view._power_switch_icons[placement.id][0]

    connector = view._power_switch_connectors[placement.id][0]

    assert {connector.port_a, connector.port_b} == {switch_icon, port_icon}
    assert connector in switch_icon.connectors
    assert connector in port_icon.connectors
    _deselect_and_collect(tab)


def test_auto_bottom_connector_survives_a_rebuild(qtbot):
    tab, view, port_icon = _tab_with_domain_and_port(qtbot)
    placement = view.add_power_switch(port_icon, "SW1", "in", "out", "ctrl", None, "", "")

    view._rebuild()

    assert placement.id in view._power_switch_icons
    assert placement.id in view._power_switch_connectors
    new_switch_icon = view._power_switch_icons[placement.id][0]
    new_target_icon = view._supply_port_icons[port_icon.key]
    connector = view._power_switch_connectors[placement.id][0]
    assert {connector.port_a, connector.port_b} == {new_switch_icon, new_target_icon}
    _deselect_and_collect(tab)


# --- edit ---------------------------------------------------------------


def test_connector_color_follows_a_recolored_target_supply_port(qtbot):
    """Bug fix, explicit request: the power-net connector line between
    a Power Switch's own bottom anchor and its target Supply Port/
    Internal Supply must update its OWN drawn colour live too, not
    just the switch icon -- an earlier version set the connector's pen
    once at construction and never refreshed it, even though the
    switch icon's own `.color` (a live property reading the target)
    always reported the new value correctly."""
    tab, view, port_icon = _tab_with_domain_and_port(qtbot)
    placement = view.add_power_switch(port_icon, "SW1", "in", "out", "ctrl", None, "", "")
    switch_icon = view._power_switch_icons[placement.id][0]
    connector = view._power_switch_connectors[placement.id][0]

    port_icon.recolor(QColor("#abcdef"))

    assert switch_icon.color == QColor("#abcdef")
    assert connector.pen().color() == QColor("#abcdef")
    _deselect_and_collect(tab)


def test_deleting_a_power_switch_unregisters_the_connectors_recolor_callback(qtbot):
    """Regression guard for the cleanup half of the fix above -- a
    dangling _sync_pen() callback left on the (still-live) target port
    after the switch/connector are gone would crash the next time that
    port recolors (same shiboken "C++ object already deleted" hazard
    PowerSwitchIconItem.sync_color's own cleanup already guards
    against)."""
    tab, view, port_icon = _tab_with_domain_and_port(qtbot)
    placement = view.add_power_switch(port_icon, "SW1", "in", "out", "ctrl", None, "", "")

    view.delete_power_switch(placement.id)

    port_icon.recolor(QColor("#112233"))  # must not raise
    _deselect_and_collect(tab)


def test_edit_power_switch_updates_fields_but_not_target_or_domain(qtbot):
    tab, view, port_icon = _tab_with_domain_and_port(qtbot)
    placement = view.add_power_switch(port_icon, "SW1", "in", "out", "ctrl", "cell1", "sleep_b", "~sleep_b")

    updated = view.edit_power_switch(placement.id, "SW1_RENAMED", "in2", "out2", "ctrl2", "cell2", "on_sig", "off_sig")

    assert updated.name == "SW1_RENAMED"
    assert updated.input_supply_port == "in2"
    assert updated.output_supply_port == "out2"
    assert updated.control_port == "ctrl2"
    assert updated.cell_type == "cell2"
    assert updated.on_state == "on_sig"
    assert updated.off_state == "off_sig"
    assert updated.target_id == placement.target_id
    assert updated.power_domain_id == placement.power_domain_id
    _deselect_and_collect(tab)


# --- delete + cascade -----------------------------------------------------


def test_delete_power_switch_removes_state_icon_and_connector(qtbot):
    tab, view, port_icon = _tab_with_domain_and_port(qtbot)
    placement = view.add_power_switch(port_icon, "SW1", "in", "out", "ctrl", None, "", "")

    view.delete_power_switch(placement.id)

    assert placement.id not in view._power_switch_state.switches
    assert placement.id not in view._power_switch_icons
    assert placement.id not in view._power_switch_connectors
    assert port_icon.connectors == []
    _deselect_and_collect(tab)


def test_deleting_the_target_supply_port_cascades_and_removes_the_switch(qtbot):
    """Real, deliberate cascade (not explicitly requested by the spec,
    but a necessary data-hygiene consequence of "attaches to a Supply
    Port/Internal Supply"): removing the target orphans any switch
    attached to it, so it's removed too -- see delete_supply_port()'s
    own docstring."""
    tab, view, port_icon = _tab_with_domain_and_port(qtbot)
    placement = view.add_power_switch(port_icon, "SW1", "in", "out", "ctrl", None, "", "")

    view.delete_supply_port(port_icon)

    assert placement.id not in view._power_switch_state.switches
    assert placement.id not in view._power_switch_icons
    assert placement.id not in view._power_switch_connectors
    _deselect_and_collect(tab)


def test_delete_supply_port_without_a_switch_is_unaffected(qtbot):
    """Confirms the cascade doesn't misfire for the ordinary, no-switch
    case -- the existing, pre-Power-Switch delete behavior is unchanged."""
    tab, view, port_icon = _tab_with_domain_and_port(qtbot)

    view.delete_supply_port(port_icon)

    assert port_icon.key not in view._shared_state.supply_port_placements
    _deselect_and_collect(tab)


# --- cascade delete + confirmation on a single Supply Port/Internal Supply --
# Explicit request: deleting a Supply Port/Internal Supply that a Supply Set
# depends on must show a confirmation listing every affected Supply Set,
# Power Domain, Retention Strategy, and Power Switch, then cascade-delete all
# of them together on confirm -- reusing the exact same _remove_domain_cascade()
# "Remove Power Domain" itself uses, not a separate reimplementation.


def _tab_with_full_cascade_fixture(qtbot):
    """VDD1 (a real Supply Port on "top") is the Primary supply of Supply
    Set SS1, which is Domain PD_TOP's own Primary -- PD_TOP owns
    Retention Strategy RET1 and Power Switch SW1. SW1 is attached to a
    SEPARATE port, VDD2 (not VDD1 itself) -- distinguishing this new
    domain-cascade path from the pre-existing direct-attachment cascade
    test_deleting_the_target_supply_port_cascades_and_removes_the_switch
    already covers above."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top"]
    vdd1 = view.add_supply_port(box, "VDD1", QColor("#ff0000"))
    vdd2 = view.add_supply_port(box, "VDD2", QColor("#0000ff"))
    ss1 = SupplySetPlacement(id="ss1", name="SS1", power="VDD1", ground="VSS", owner_paths=["top"])
    view._domain_state.supply_sets["ss1"] = ss1
    domain = PowerDomainPlacement(id="d1", owner_path="top", name="PD_TOP", color=QColor("#4caf50"), primary_supply_set="ss1")
    view._domain_state.domains["d1"] = domain
    retention = RetentionStrategyPlacement(
        id="ret1",
        name="RET1",
        power_domain_id="d1",
        primary_supply_set_id="ss1",
        retention_supply_set_id="ss1",
        save_logic="save",
        restore_logic="restore",
        elements=["top/u_core/out1"],
    )
    view._retention_state.strategies["ret1"] = retention
    switch = view.add_power_switch(vdd2, "SW1", "in", "out", "ctrl", None, "", "")
    return tab, view, vdd1, ss1, domain, retention, switch


def test_prompt_delete_supply_port_with_no_cascade_impact_deletes_immediately_without_asking(qtbot, monkeypatch):
    """The common case -- most Supply Ports are never assigned to a
    Supply Set at all -- must stay exactly as unobtrusive as before this
    feature: no confirmation dialog, immediate delete."""
    tab, view, port_icon = _tab_with_domain_and_port(qtbot)
    asked = []
    monkeypatch.setattr(message_box, "question", lambda *a, **k: asked.append(1) or QMessageBox.StandardButton.Yes)

    view._prompt_delete_supply_port(port_icon)

    assert asked == []
    assert port_icon.key not in view._shared_state.supply_port_placements
    _deselect_and_collect(tab)


def test_prompt_delete_supply_port_lists_every_affected_entity(qtbot, monkeypatch):
    tab, view, vdd1, ss1, domain, retention, switch = _tab_with_full_cascade_fixture(qtbot)
    questions = []
    monkeypatch.setattr(message_box, "question", lambda *args, **kwargs: questions.append(args) or QMessageBox.StandardButton.No)

    view._prompt_delete_supply_port(vdd1)

    assert len(questions) == 1
    text = questions[0][2]
    assert "SS1" in text
    assert "PD_TOP" in text
    assert "RET1" in text
    assert "SW1" in text
    _deselect_and_collect(tab)


def test_prompt_delete_supply_port_cancelling_leaves_everything_intact(qtbot, monkeypatch):
    tab, view, vdd1, ss1, domain, retention, switch = _tab_with_full_cascade_fixture(qtbot)
    monkeypatch.setattr(message_box, "question", lambda *a, **k: QMessageBox.StandardButton.No)

    view._prompt_delete_supply_port(vdd1)

    assert vdd1.key in view._shared_state.supply_port_placements
    assert "ss1" in view._domain_state.supply_sets
    assert "d1" in view._domain_state.domains
    assert "ret1" in view._retention_state.strategies
    assert switch.id in view._power_switch_state.switches
    _deselect_and_collect(tab)


def test_prompt_delete_supply_port_confirming_cascades_everything(qtbot, monkeypatch):
    tab, view, vdd1, ss1, domain, retention, switch = _tab_with_full_cascade_fixture(qtbot)
    monkeypatch.setattr(message_box, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)

    view._prompt_delete_supply_port(vdd1)

    assert vdd1.key not in view._shared_state.supply_port_placements
    assert "ss1" not in view._domain_state.supply_sets
    assert "d1" not in view._domain_state.domains
    assert "ret1" not in view._retention_state.strategies
    assert switch.id not in view._power_switch_state.switches
    _deselect_and_collect(tab)


def test_delete_supply_port_with_cascade_is_a_single_undoable_step(qtbot, monkeypatch):
    """A single Ctrl+Z must restore the port, the Supply Set, the
    domain, the retention strategy, AND the power switch together --
    see delete_supply_port_with_cascade()'s own docstring."""
    tab, view, vdd1, ss1, domain, retention, switch = _tab_with_full_cascade_fixture(qtbot)
    monkeypatch.setattr(message_box, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)

    view._prompt_delete_supply_port(vdd1)
    assert "d1" not in view._domain_state.domains

    view._undo_stack.undo()

    assert vdd1.key in view._shared_state.supply_port_placements
    assert "ss1" in view._domain_state.supply_sets
    assert "d1" in view._domain_state.domains
    assert "ret1" in view._retention_state.strategies
    assert switch.id in view._power_switch_state.switches
    _deselect_and_collect(tab)


def test_deleting_the_target_supply_port_cascade_is_undoable(qtbot):
    tab, view, port_icon = _tab_with_domain_and_port(qtbot)
    placement = view.add_power_switch(port_icon, "SW1", "in", "out", "ctrl", None, "", "")

    view.delete_supply_port(port_icon)
    assert placement.id not in view._power_switch_state.switches

    view._undo_stack.undo()

    assert placement.id in view._power_switch_state.switches
    assert port_icon.key in view._shared_state.supply_port_placements
    _deselect_and_collect(tab)


# --- undo/redo --------------------------------------------------------------


def test_add_power_switch_is_undoable(qtbot):
    tab, view, port_icon = _tab_with_domain_and_port(qtbot)
    placement = view.add_power_switch(port_icon, "SW1", "in", "out", "ctrl", None, "", "")

    view._undo_stack.undo()
    assert placement.id not in view._power_switch_state.switches

    view._undo_stack.redo()
    assert placement.id in view._power_switch_state.switches
    _deselect_and_collect(tab)


def test_delete_power_switch_is_undoable(qtbot):
    tab, view, port_icon = _tab_with_domain_and_port(qtbot)
    placement = view.add_power_switch(port_icon, "SW1", "in", "out", "ctrl", None, "", "")
    view.delete_power_switch(placement.id)

    view._undo_stack.undo()

    assert placement.id in view._power_switch_state.switches
    _deselect_and_collect(tab)


# --- hover+click placement (DiagramMode.POWER_SWITCH) -----------------------


def test_power_switch_mode_hover_highlights_a_valid_target(qtbot):
    tab, view, port_icon = _tab_with_domain_and_port(qtbot)
    view.set_mode(DiagramMode.POWER_SWITCH)

    view._update_power_switch_hover(view.mapFromScene(port_icon.scene_center() if hasattr(port_icon, "scene_center") else port_icon.scenePos()))

    assert view._power_switch_hover_icon is port_icon
    assert view._power_switch_highlight_overlay is not None
    _deselect_and_collect(tab)


def test_power_switch_mode_never_highlights_a_pg_pin(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view.set_mode(DiagramMode.POWER_SWITCH)
    pg_icon = next(icon for icon in view._supply_port_icons.values() if icon.fixed)

    valid = view._is_valid_power_switch_target(pg_icon)

    assert valid is False
    _deselect_and_collect(tab)


def test_clicking_an_already_switched_target_via_the_tool_shows_a_warning(qtbot, monkeypatch):
    import frontend.src.power_intent_view.power_intent_diagram_view as mod

    tab, view, port_icon = _tab_with_domain_and_port(qtbot)
    view.add_power_switch(port_icon, "SW1", "in", "out", "ctrl", None, "", "")
    warnings = []
    monkeypatch.setattr(mod.message_box, "warning", lambda *a, **k: warnings.append(a))

    view._prompt_add_power_switch(port_icon)

    assert warnings
    assert len(view._power_switch_state.switches) == 1  # no second switch was added
    _deselect_and_collect(tab)


# --- Supply In resolution (top-anchor connector) -----------------------------


def test_resolve_supply_in_name_is_none_when_nothing_is_connected(qtbot):
    tab, view, port_icon = _tab_with_domain_and_port(qtbot)
    placement = view.add_power_switch(port_icon, "SW1", "in", "out", "ctrl", None, "", "")
    switch_icon = view._power_switch_icons[placement.id][0]

    assert view._resolve_power_switch_supply_in_name(switch_icon) is None
    _deselect_and_collect(tab)


def test_resolve_supply_in_name_reflects_a_manually_drawn_top_anchor_connector(qtbot):
    tab, view, port_icon = _tab_with_domain_and_port(qtbot)
    box = view._boxes["top"]
    upstream_icon = view.add_supply_port(box, "VDD_UNSWITCHED", QColor("#0000ff"))
    placement = view.add_power_switch(port_icon, "SW1", "in", "out", "ctrl", None, "", "")
    switch_icon = view._power_switch_icons[placement.id][0]

    connected = view.try_connect_supply_ports(switch_icon, upstream_icon)

    assert connected is True
    assert view._resolve_power_switch_supply_in_name(switch_icon) == "VDD_UNSWITCHED"
    _deselect_and_collect(tab)


def test_connecting_a_switch_top_anchor_back_to_its_own_target_is_rejected(qtbot):
    """"Supply In and Supply Out must not have the same value" (original
    spec), re-expressed per the resolved architecture: enforced at
    connector-draw time, not as a dialog-submission check -- see
    _shorts_power_switch()'s own docstring."""
    tab, view, port_icon = _tab_with_domain_and_port(qtbot)
    placement = view.add_power_switch(port_icon, "SW1", "in", "out", "ctrl", None, "", "")
    switch_icon = view._power_switch_icons[placement.id][0]

    connected = view.try_connect_supply_ports(switch_icon, port_icon)

    assert connected is False
    assert view._resolve_power_switch_supply_in_name(switch_icon) is None
    _deselect_and_collect(tab)


def test_connecting_a_switch_top_anchor_to_a_different_supply_still_works(qtbot):
    """Confirms the anti-short rule is scoped to THIS switch's own
    target only -- a different Supply Port/Internal Supply remains a
    perfectly valid Supply In connection."""
    tab, view, port_icon = _tab_with_domain_and_port(qtbot)
    box = view._boxes["top"]
    other_icon = view.add_supply_port(box, "VDD_OTHER", QColor("#123456"))
    placement = view.add_power_switch(port_icon, "SW1", "in", "out", "ctrl", None, "", "")
    switch_icon = view._power_switch_icons[placement.id][0]

    connected = view.try_connect_supply_ports(switch_icon, other_icon)

    assert connected is True
    _deselect_and_collect(tab)


def test_real_mouse_drag_from_the_switch_top_handle_connects_and_updates_supply_in(qtbot):
    """End-to-end, real QTest mouse press/move/release (not the directly-
    testable try_connect_supply_ports() core) -- confirms the switch's
    own top ConnectorHandleItem is genuinely findable/clickable now that
    PowerSwitchIconItem is a scene-level item (see that class's own
    docstring for the real bug this rework fixes: an earlier, nested-
    child version could end up visually/hit-test-wise shadowed).
    Bug report's own explicit ask: re-confirm Supply In resolution once
    the switch is genuinely interactable on the canvas, via the real
    drag gesture, not just the underlying resolver called directly."""
    tab, view, port_icon = _tab_with_domain_and_port(qtbot)
    tab.resize(900, 700)
    box = view._boxes["top"]
    # A distinct `t` -- default add_supply_port() placement (edge="top",
    # t=0.5) would otherwise land exactly on top of `port_icon`'s own
    # default position, geometrically overlapping the two icons/handles
    # and making the drag target ambiguous (a real test-fixture pitfall,
    # not a product bug).
    upstream_icon = view.add_supply_port(box, "VDD_UNSWITCHED", QColor("#0000ff"), edge="top", t=0.85)
    placement = view.add_power_switch(port_icon, "SW1", "in", "out", "ctrl", None, "", "")
    switch_icon = view._power_switch_icons[placement.id][0]
    view.set_mode(DiagramMode.SUPPLY_NET)

    start = _viewport_pos(view, switch_icon.handles["top"])
    end = _viewport_pos(view, upstream_icon.handles["top"])
    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
    assert view._connector_drag_line is not None
    QTest.mouseMove(view.viewport(), end)
    assert view._connector_handle_at(end) is not None
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, end)

    assert view._resolve_power_switch_supply_in_name(switch_icon) == "VDD_UNSWITCHED"
    _deselect_and_collect(tab)


def test_real_mouse_drag_onto_an_internal_supplys_switch_inherits_the_sources_power_states(qtbot):
    """PERMANENT regression test (explicit, mandatory request -- this is
    the SECOND time this exact behavior was reported broken): an
    Internal Supply with a Power Switch attached must inherit its
    connected upstream source's own Power States (plus its own
    auto-generated "OFF" state) once the "Supply In"
    connector is drawn.

    Deliberately distinct from
    test_real_mouse_drag_from_the_switch_top_handle_connects_and_updates_supply_in
    above, which only ever attaches a switch to a plain Supply Port
    (`_tab_with_domain_and_port()`'s own `port_icon`) -- a plain Supply
    Port's own `resolve_effective_states()` is just its unchanged
    `.states` list, so that test never actually exercises the
    trace-through-a-switch inheritance path at all, real drag gesture or
    not. Every PRE-EXISTING inheritance test in
    test_internal_supply_inheritance.py also stops short of this: they
    all splice `supply_connector_placements` together BY HAND, bypassing
    try_connect_supply_ports()/the real SUPPLY_NET drag gesture entirely.
    This test is the first to combine all three: a real end-to-end
    QTest mouse press/move/release drag, an Internal Supply as the
    switch's own TARGET, and an assertion on the actual resolved
    effective states/inherited-states preview -- closing the exact
    coverage gap that let a regression here go undetected before."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.resize(900, 700)
    tab.show()
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view._domain_state.domains["d1"] = PowerDomainPlacement(id="d1", owner_path="top/u_core", name="PD_CORE", color=QColor("#ff0000"))

    source = view.add_supply_port(
        view._boxes["top"],
        "VDD",
        QColor("#ff0000"),
        states=[SupplyPortState("FULL_ON", "1.0")],
    )
    internal = view.add_internal_supply(view._boxes["top/u_core"], "Y", QColor("#00ff00"))
    placement = view.add_power_switch(internal, "SW_A", "in", "out", "ctrl", None, "on", "off")
    switch_icon = view._power_switch_icons[placement.id][0]
    view.set_mode(DiagramMode.SUPPLY_NET)
    view.fit_view()

    start = _viewport_pos(view, switch_icon.handles["top"])
    end = _viewport_pos(view, source.handles["bottom"])
    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
    assert view._connector_drag_line is not None
    QTest.mouseMove(view.viewport(), end)
    assert view._connector_handle_at(end) is not None
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, end)

    effective = resolve_effective_states(internal.placement, view._shared_state, view._power_switch_state.switches)
    assert SupplyPortState("FULL_ON", "1.0") in effective
    assert SupplyPortState("OFF", name=SWITCH_OFF_STATE_NAME) in effective
    assert len(effective) == 2

    inherited_lines = describe_inherited_states(internal.placement, view._shared_state, view._power_switch_state.switches)
    assert inherited_lines is not None
    assert any("FULL_ON" in line and "VDD" in line for line in inherited_lines)
    assert any("OFF" in line for line in inherited_lines)
    _deselect_and_collect(tab)


# --- context menu: Add/Edit/Delete Power Switch on the Supply Port/Internal -
# --- Supply icon's own right-click menu -------------------------------------


def test_add_power_switch_enabled_when_no_switch_exists_and_domain_present(qtbot):
    tab, view, port_icon = _tab_with_domain_and_port(qtbot)

    menu = QMenu()
    actions = view._supply_port_context_menu_actions(menu, port_icon)
    by_text = {a.text(): a for a, _cb in actions}

    assert by_text["Add Power Switch"].isEnabled() is True
    assert by_text["Edit Power Switch"].isEnabled() is False
    assert by_text["Delete Power Switch"].isEnabled() is False
    _deselect_and_collect(tab)


def test_add_power_switch_disabled_with_no_power_domain(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    port_icon = view.add_supply_port(view._boxes["top"], "VDD_ALWAYS_ON", QColor("#00ff00"))  # no domain assigned

    menu = QMenu()
    actions = view._supply_port_context_menu_actions(menu, port_icon)
    by_text = {a.text(): a for a, _cb in actions}

    assert by_text["Add Power Switch"].isEnabled() is False
    _deselect_and_collect(tab)


def test_add_disabled_edit_delete_enabled_once_a_switch_exists(qtbot):
    tab, view, port_icon = _tab_with_domain_and_port(qtbot)
    view.add_power_switch(port_icon, "SW1", "in", "out", "ctrl", None, "", "")

    menu = QMenu()
    actions = view._supply_port_context_menu_actions(menu, port_icon)
    by_text = {a.text(): a for a, _cb in actions}

    assert by_text["Add Power Switch"].isEnabled() is False
    assert by_text["Edit Power Switch"].isEnabled() is True
    assert by_text["Delete Power Switch"].isEnabled() is True
    _deselect_and_collect(tab)


def test_context_menu_delete_power_switch_callback_confirms_then_removes_it(qtbot, monkeypatch):
    """Explicit request: Power Switch deletion gets its own confirmation
    prompt (a deliberate exception to this diagram's otherwise-uniform
    "delete immediately" convention -- see _prompt_delete_power_switch()'s
    own docstring)."""
    import frontend.src.power_intent_view.power_intent_diagram_view as mod

    tab, view, port_icon = _tab_with_domain_and_port(qtbot)
    placement = view.add_power_switch(port_icon, "SW1", "in", "out", "ctrl", None, "", "")
    monkeypatch.setattr(mod.message_box, "question", lambda *a, **k: mod.QMessageBox.StandardButton.Yes)

    menu = QMenu()
    actions = view._supply_port_context_menu_actions(menu, port_icon)
    delete_switch_cb = next(cb for a, cb in actions if a.text() == "Delete Power Switch")
    delete_switch_cb()

    assert placement.id not in view._power_switch_state.switches
    _deselect_and_collect(tab)


def test_delete_power_switch_confirmation_defaults_to_no(qtbot, monkeypatch):
    import frontend.src.power_intent_view.power_intent_diagram_view as mod

    tab, view, port_icon = _tab_with_domain_and_port(qtbot)
    placement = view.add_power_switch(port_icon, "SW1", "in", "out", "ctrl", None, "", "")
    captured = {}

    def fake_question(self_, title, text, buttons, default):
        captured["default"] = default
        return default

    monkeypatch.setattr(mod.message_box, "question", fake_question)

    view._prompt_delete_power_switch(placement)

    assert captured["default"] == mod.QMessageBox.StandardButton.No
    assert placement.id in view._power_switch_state.switches  # declining leaves it in place
    _deselect_and_collect(tab)


def test_delete_power_switch_confirmation_declined_leaves_the_switch(qtbot, monkeypatch):
    import frontend.src.power_intent_view.power_intent_diagram_view as mod

    tab, view, port_icon = _tab_with_domain_and_port(qtbot)
    placement = view.add_power_switch(port_icon, "SW1", "in", "out", "ctrl", None, "", "")
    monkeypatch.setattr(mod.message_box, "question", lambda *a, **k: mod.QMessageBox.StandardButton.No)

    view._prompt_delete_power_switch(placement)

    assert placement.id in view._power_switch_state.switches
    _deselect_and_collect(tab)


def test_delete_power_switch_confirmation_accepted_removes_the_switch(qtbot, monkeypatch):
    import frontend.src.power_intent_view.power_intent_diagram_view as mod

    tab, view, port_icon = _tab_with_domain_and_port(qtbot)
    placement = view.add_power_switch(port_icon, "SW1", "in", "out", "ctrl", None, "", "")
    monkeypatch.setattr(mod.message_box, "question", lambda *a, **k: mod.QMessageBox.StandardButton.Yes)

    view._prompt_delete_power_switch(placement)

    assert placement.id not in view._power_switch_state.switches
    _deselect_and_collect(tab)


def test_context_menu_add_power_switch_callback_opens_the_flow(qtbot, monkeypatch):
    """Directly-testable dispatch, same "can't drive dialog.exec()
    headlessly" reasoning every other context-menu test here already
    follows -- monkeypatches the dialog to auto-accept with a name."""
    import frontend.src.power_intent_view.power_switch_dialog as dialog_mod

    tab, view, port_icon = _tab_with_domain_and_port(qtbot)

    def fake_exec(self):
        self.name_edit.setText("SW1")
        return dialog_mod.QDialog.DialogCode.Accepted

    monkeypatch.setattr(dialog_mod.PowerSwitchDialog, "exec", fake_exec)

    menu = QMenu()
    actions = view._supply_port_context_menu_actions(menu, port_icon)
    add_switch_cb = next(cb for a, cb in actions if a.text() == "Add Power Switch")
    add_switch_cb()

    assert len(view._power_switch_state.switches) == 1
    _deselect_and_collect(tab)


# --- selection highlight (Regular Cursor / DiagramMode.SELECT) --------------


def test_clicking_a_power_switch_icon_flags_it_click_highlighted(qtbot):
    """Explicit request: same click-selection highlight mechanism as
    Supply Port/Retention icons, reused as-is (set_click_highlighted()/
    HIGHLIGHT_COLOR), not reimplemented."""
    tab, view, port_icon = _tab_with_domain_and_port(qtbot)
    tab.resize(900, 700)
    tab.show()
    placement = view.add_power_switch(port_icon, "SW1", "in", "out", "ctrl", None, "", "")
    switch_icon = view._power_switch_icons[placement.id][0]
    view.set_mode(DiagramMode.SELECT)

    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, view.mapFromScene(switch_icon.scenePos()))

    assert switch_icon._click_highlighted is True
    _deselect_and_collect(tab)


def test_selecting_something_else_clears_the_power_switch_highlight(qtbot):
    tab, view, port_icon = _tab_with_domain_and_port(qtbot)
    tab.resize(900, 700)
    tab.show()
    placement = view.add_power_switch(port_icon, "SW1", "in", "out", "ctrl", None, "", "")
    switch_icon = view._power_switch_icons[placement.id][0]
    view.set_mode(DiagramMode.SELECT)
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, view.mapFromScene(switch_icon.scenePos()))
    assert switch_icon._click_highlighted is True

    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, view.mapFromScene(port_icon.scenePos()))

    assert switch_icon._click_highlighted is False
    _deselect_and_collect(tab)


# --- item 6: Power Switch color live-follows its Supply Out's color --------


def test_power_switch_color_matches_its_target_at_creation(qtbot):
    tab, view, port_icon = _tab_with_domain_and_port(qtbot)
    placement = view.add_power_switch(port_icon, "SW1", "in", "out", "ctrl", None, "", "")
    switch_icon = view._power_switch_icons[placement.id][0]

    assert switch_icon.color == port_icon.color
    _deselect_and_collect(tab)


def test_power_switch_color_live_follows_a_later_target_recolor(qtbot):
    """Explicit request: "a live binding/reference to the same color
    value, not a one-time copy taken at Power Switch creation time" --
    changing the target's own color AFTER the switch already exists
    must be reflected without re-adding anything."""
    tab, view, port_icon = _tab_with_domain_and_port(qtbot)
    placement = view.add_power_switch(port_icon, "SW1", "in", "out", "ctrl", None, "", "")
    switch_icon = view._power_switch_icons[placement.id][0]
    assert switch_icon.color != QColor("#ff00ff")

    port_icon.recolor(QColor("#ff00ff"))

    assert switch_icon.color == QColor("#ff00ff")
    _deselect_and_collect(tab)


def test_power_switch_handles_also_follow_a_target_recolor(qtbot):
    """The one place color ISN'T read live -- ConnectorHandleItem holds
    a static brush/pen set at construction -- must be explicitly pushed
    via sync_color()."""
    tab, view, port_icon = _tab_with_domain_and_port(qtbot)
    placement = view.add_power_switch(port_icon, "SW1", "in", "out", "ctrl", None, "", "")
    switch_icon = view._power_switch_icons[placement.id][0]

    port_icon.recolor(QColor("#ff00ff"))

    for handle in switch_icon.handles.values():
        assert handle.brush().color() == QColor("#ff00ff")
    _deselect_and_collect(tab)


def test_deleting_the_switch_unregisters_its_recolor_callback(qtbot):
    """Same lifecycle hazard as on_move_callbacks -- a stale sync_color()
    callback left on the target after the switch is deleted would crash
    on the next target recolor, calling a method on an already-deleted
    C++ object."""
    tab, view, port_icon = _tab_with_domain_and_port(qtbot)
    placement = view.add_power_switch(port_icon, "SW1", "in", "out", "ctrl", None, "", "")
    switch_icon = view._power_switch_icons[placement.id][0]

    view.delete_power_switch(placement.id)

    assert switch_icon.sync_color not in port_icon.on_recolor_callbacks
    port_icon.recolor(QColor("#123456"))  # must not raise
    _deselect_and_collect(tab)


# --- item 7: Power Switch name label rendered to the right of its icon -----


def test_power_switch_shows_a_name_label_to_the_right_of_the_icon(qtbot):
    tab, view, port_icon = _tab_with_domain_and_port(qtbot)
    placement = view.add_power_switch(port_icon, "SW1", "in", "out", "ctrl", None, "", "")
    switch_icon = view._power_switch_icons[placement.id][0]

    assert switch_icon.label.text() == "SW1"
    assert switch_icon.label.parentItem() is switch_icon
    assert switch_icon.label.pos().x() > 0  # to the RIGHT of the icon's own local origin
    _deselect_and_collect(tab)


def test_renaming_a_power_switch_updates_its_own_label(qtbot):
    tab, view, port_icon = _tab_with_domain_and_port(qtbot)
    placement = view.add_power_switch(port_icon, "SW1", "in", "out", "ctrl", None, "", "")
    switch_icon = view._power_switch_icons[placement.id][0]

    view.edit_power_switch(placement.id, "SW1_RENAMED", "in", "out", "ctrl", None, "", "")

    assert switch_icon.label.text() == "SW1_RENAMED"
    _deselect_and_collect(tab)


# --- item 5: containing block reserves layout room for the switch -----------


def test_extra_top_margin_is_empty_with_no_switches(qtbot):
    tab = PowerIntentTab()
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view

    assert view._extra_top_margin() == {}
    _deselect_and_collect(tab)


def test_extra_top_margin_reserves_room_above_a_switched_top_edge_target(qtbot):
    from frontend.src.power_intent_view.power_switch_overlay import TOP_MARGIN_FOR_SWITCH

    tab, view, port_icon = _tab_with_domain_and_port(qtbot)
    view.add_power_switch(port_icon, "SW1", "in", "out", "ctrl", None, "", "")

    assert view._extra_top_margin() == {"top": TOP_MARGIN_FOR_SWITCH}
    _deselect_and_collect(tab)


def test_extra_top_margin_ignores_a_non_top_edge_target(qtbot):
    """Known, explicit scope cut (see _extra_top_margin()'s own
    docstring): a switch's own fixed vertical offset only ever renders
    above a top-edge-anchored target -- a port dragged to a different
    edge doesn't get reserved layout room, since the switch would
    render somewhere else relative to the box entirely."""
    tab, view, port_icon = _tab_with_domain_and_port(qtbot)
    box = view._boxes["top"]
    port_icon.setPos(box.rect().right(), box.rect().center().y())  # drag to the right edge
    assert port_icon.placement.edge == "right"

    view.add_power_switch(port_icon, "SW1", "in", "out", "ctrl", None, "", "")

    assert view._extra_top_margin() == {}
    _deselect_and_collect(tab)


def test_two_switched_top_edge_ports_on_the_same_box_share_one_margin(qtbot):
    from frontend.src.power_intent_view.power_switch_overlay import TOP_MARGIN_FOR_SWITCH

    tab, view, port_icon = _tab_with_domain_and_port(qtbot)
    box = view._boxes["top"]
    other_icon = view.add_supply_port(box, "VDD_OTHER", QColor("#123456"), edge="top", t=0.8)
    view.add_power_switch(port_icon, "SW1", "in", "out", "ctrl", None, "", "")
    view.add_power_switch(other_icon, "SW2", "in", "out", "ctrl", None, "", "")

    assert view._extra_top_margin() == {"top": TOP_MARGIN_FOR_SWITCH}  # not doubled
    _deselect_and_collect(tab)


def test_the_containing_box_actually_grows_and_shifts_after_a_real_rebuild(qtbot):
    """End-to-end: confirms _extra_top_margin() is genuinely wired into
    the real _rebuild() -> build_hierarchy_scene() call, not just
    correct in isolation -- the box's own rendered position/size
    actually change once a switch exists.

    Item 3 (explicit request, "make room" layout): the box's own
    NATURAL size now also grows by INTERIOR_TOP_MARGIN_FOR_SUPPLY_PORT
    -- a genuinely different, ADDITIONAL reservation from the switch's
    own EXTERIOR TOP_MARGIN_FOR_SWITCH (a pure position shift, verified
    unaffected by this): every top-edge Supply Port/Internal Supply now
    reserves interior room on its own owner box regardless of whether
    it's switched (see _extra_interior_top()'s own docstring), so this
    box needs both a shifted position (still switch-only) AND a taller
    natural size (now also supply-port-general).

    This test's own injected domain (scaffolding, not itself under
    test) ALSO now reserves its own bottom margin for its merged info
    box (build_domain_info_box_overlay()'s own "always renders"
    guarantee, see power_domain_overlay.py) -- explicit combined-
    scenario coverage per the CONSOLIDATE task: the switch/supply-port
    interior-TOP growth and the domain box's own interior-BOTTOM growth
    must both apply, independently, with neither shadowing the other."""
    from frontend.src.power_intent_view.power_switch_overlay import TOP_MARGIN_FOR_SWITCH
    from frontend.src.power_intent_view.supply_port_overlay import INTERIOR_TOP_MARGIN_FOR_SUPPLY_PORT
    from frontend.src.power_intent_view.power_domain_overlay import domain_info_box_required_height

    tab = PowerIntentTab()
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box_before = view._boxes["top"]
    pos_before, height_before = box_before.pos(), box_before.rect().height()

    view._domain_state.domains["d1"] = PowerDomainPlacement(id="d1", owner_path="top", name="PD_TOP", color=QColor("#ff0000"))
    port_icon = view.add_supply_port(box_before, "VDD_ALWAYS_ON", QColor("#00ff00"))
    view.add_power_switch(port_icon, "SW1", "in", "out", "ctrl", None, "", "")
    view._rebuild()

    box_after = view._boxes["top"]
    domain_box_margin = domain_info_box_required_height(view._domain_state.domains["d1"], view._domain_state.supply_sets)
    assert box_after.pos().y() == pos_before.y() + TOP_MARGIN_FOR_SWITCH
    assert box_after.rect().height() == height_before + INTERIOR_TOP_MARGIN_FOR_SUPPLY_PORT + domain_box_margin
    _deselect_and_collect(tab)


# --- item 8: auto-adopt an existing power net connection as Supply In ------


def test_add_power_switch_auto_adopts_an_existing_incoming_connection(qtbot):
    """An Internal Supply already wired to an upstream Supply Port
    BEFORE the switch is added -- adding the switch must re-route that
    existing connector through the switch's own top anchor, not leave
    Supply In empty requiring a manual redraw."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top/u_core"]
    view._domain_state.domains["d1"] = PowerDomainPlacement(id="d1", owner_path="top/u_core", name="PD_CORE", color=QColor("#ff0000"))
    upstream_icon = view.add_supply_port(view._boxes["top"], "VDD_UNSWITCHED", QColor("#0000ff"))
    internal_icon = view.add_internal_supply(box, "VDD_INT_SW", QColor("#00aa00"))
    assert view.try_connect_supply_ports(upstream_icon, internal_icon) is True
    original_connector = internal_icon.connectors[0]

    placement = view.add_power_switch(internal_icon, "SW1", "in", "out", "ctrl", None, "", "")
    switch_icon = view._power_switch_icons[placement.id][0]

    # The pre-existing connector now terminates at the switch's own top
    # anchor instead of the target directly.
    assert original_connector not in internal_icon.connectors
    assert original_connector in switch_icon.connectors
    assert {original_connector.port_a, original_connector.port_b} == {switch_icon, upstream_icon}
    # Supply In reflects the adopted connection -- no manual redraw needed.
    assert view._resolve_power_switch_supply_in_name(switch_icon) == "VDD_UNSWITCHED"
    # Persisted data model reference updated too, not just the live item.
    assert (upstream_icon.key, switch_icon.key) in view._shared_state.supply_connector_placements
    assert (upstream_icon.key, internal_icon.key) not in view._shared_state.supply_connector_placements
    # The target's own bottom-anchor connection to the switch is still
    # separately, correctly established.
    assert view._power_switch_connectors[placement.id][0] in internal_icon.connectors
    _deselect_and_collect(tab)


def test_add_power_switch_without_an_existing_connection_leaves_supply_in_empty(qtbot):
    """Confirms the "no prior connection" path is unaffected -- Supply
    In still starts empty/unresolved, exactly as already established,
    when there's nothing to adopt."""
    tab, view, port_icon = _tab_with_domain_and_port(qtbot)
    placement = view.add_power_switch(port_icon, "SW1", "in", "out", "ctrl", None, "", "")
    switch_icon = view._power_switch_icons[placement.id][0]

    assert view._resolve_power_switch_supply_in_name(switch_icon) is None
    _deselect_and_collect(tab)


def test_auto_adopted_connection_is_undoable(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top/u_core"]
    view._domain_state.domains["d1"] = PowerDomainPlacement(id="d1", owner_path="top/u_core", name="PD_CORE", color=QColor("#ff0000"))
    upstream_icon = view.add_supply_port(view._boxes["top"], "VDD_UNSWITCHED", QColor("#0000ff"))
    internal_icon = view.add_internal_supply(box, "VDD_INT_SW", QColor("#00aa00"))
    view.try_connect_supply_ports(upstream_icon, internal_icon)

    view.add_power_switch(internal_icon, "SW1", "in", "out", "ctrl", None, "", "")
    assert (upstream_icon.key, internal_icon.key) not in view._shared_state.supply_connector_placements

    view._undo_stack.undo()

    assert (upstream_icon.key, internal_icon.key) in view._shared_state.supply_connector_placements
    assert view._power_switch_state.switches == {}
    _deselect_and_collect(tab)


# --- items 9/10: multi-hierarchy Power Domain replication --------------------
# Mirrors the existing multi-hierarchy Supply Set/Retention replication
# pattern (test_multi_hierarchy_power_domain.py) -- ONE shared
# PowerSwitchPlacement whose target_ids/owner_paths span every member
# hierarchy's own same-named Internal Supply, one icon materialized per
# target, edit/delete/click-highlight all operate on the whole group.


def _multi_hierarchy_domain_with_internal_supplies(qtbot, member_paths: list[str], supply_name: str = "VDD_SW"):
    """A Power Domain spanning `member_paths` (2+ sibling hierarchies
    under "top", e.g. ["top/u_core", "top/u_dbg"]), each already carrying
    its own Internal Supply icon of the SAME name -- the "corresponding
    same-name target on every member hierarchy" precondition item 9's
    own replication logic (_corresponding_target_ids()) relies on."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view._domain_state.domains["d1"] = PowerDomainPlacement(
        id="d1", owner_path=member_paths[0], name="PD_MULTI", color=QColor("#ff0000"), owner_paths=list(member_paths)
    )
    icons = {}
    for path in member_paths:
        box = view._boxes[path]
        icons[path] = view.add_internal_supply(box, supply_name, QColor("#00aa00"))
    return tab, view, icons


def test_add_power_switch_on_a_multi_hierarchy_domain_replicates_to_every_member(qtbot):
    member_paths = ["top/u_core", "top/u_dbg"]
    tab, view, icons = _multi_hierarchy_domain_with_internal_supplies(qtbot, member_paths)

    placement = view.add_power_switch(icons["top/u_core"], "SW1", "in", "out", "ctrl", None, "", "")

    assert set(placement.target_ids) == {icons["top/u_core"].key, icons["top/u_dbg"].key}
    assert set(placement.owner_paths) == set(member_paths)
    switch_icons = view._power_switch_icons[placement.id]
    assert len(switch_icons) == 2
    assert {si.target_icon.key for si in switch_icons} == {icons["top/u_core"].key, icons["top/u_dbg"].key}
    # Each replicated instance got its own auto bottom connector, wired
    # to its OWN target -- not shared/cross-wired between member
    # hierarchies.
    connectors = view._power_switch_connectors[placement.id]
    assert len(connectors) == 2
    for connector in connectors:
        assert {connector.port_a, connector.port_b} & set(switch_icons)
    _deselect_and_collect(tab)


def test_add_power_switch_second_target_only_replicates_to_matching_names(qtbot):
    """A member hierarchy whose own Internal Supply has a DIFFERENT name
    is not a "corresponding" target -- silently skipped, not guessed
    at."""
    member_paths = ["top/u_core", "top/u_dbg"]
    tab, view, icons = _multi_hierarchy_domain_with_internal_supplies(qtbot, member_paths, supply_name="VDD_SW")
    # Give u_dbg an UNRELATED second Internal Supply, differently named.
    unrelated = view.add_internal_supply(view._boxes["top/u_dbg"], "VDD_OTHER", QColor("#123456"))

    placement = view.add_power_switch(icons["top/u_core"], "SW1", "in", "out", "ctrl", None, "", "")

    assert unrelated.key not in placement.target_ids
    _deselect_and_collect(tab)


def test_editing_a_replicated_power_switch_from_one_instance_updates_all(qtbot):
    member_paths = ["top/u_core", "top/u_dbg"]
    tab, view, icons = _multi_hierarchy_domain_with_internal_supplies(qtbot, member_paths)
    placement = view.add_power_switch(icons["top/u_core"], "SW1", "in", "out", "ctrl", None, "", "")

    updated = view.edit_power_switch(placement.id, "SW1_RENAMED", "in", "out", "ctrl", "cell1", "en", "~en")

    assert updated.name == "SW1_RENAMED"
    switch_icons = view._power_switch_icons[placement.id]
    assert len(switch_icons) == 2
    for switch_icon in switch_icons:
        assert switch_icon.placement.name == "SW1_RENAMED"
        assert switch_icon.label.text() == "SW1_RENAMED"
    assert view._power_switch_state.switches[placement.id].cell_type == "cell1"
    _deselect_and_collect(tab)


def test_deleting_a_replicated_power_switch_from_one_instance_removes_all(qtbot):
    member_paths = ["top/u_core", "top/u_dbg"]
    tab, view, icons = _multi_hierarchy_domain_with_internal_supplies(qtbot, member_paths)
    placement = view.add_power_switch(icons["top/u_core"], "SW1", "in", "out", "ctrl", None, "", "")
    switch_icons_before = list(view._power_switch_icons[placement.id])
    connectors_before = list(view._power_switch_connectors[placement.id])

    view.delete_power_switch(placement.id)

    assert placement.id not in view._power_switch_state.switches
    assert placement.id not in view._power_switch_icons
    assert placement.id not in view._power_switch_connectors
    for icon in switch_icons_before:
        assert icon.scene() is None
    for connector in connectors_before:
        assert connector.scene() is None
    # Both targets' own on_move/on_recolor callback lists were cleaned
    # up too -- confirmed no dangling callback survives deletion.
    assert icons["top/u_core"].on_move_callbacks == []
    assert icons["top/u_dbg"].on_move_callbacks == []
    assert icons["top/u_core"].on_recolor_callbacks == []
    assert icons["top/u_dbg"].on_recolor_callbacks == []
    _deselect_and_collect(tab)


def test_clicking_one_replicated_power_switch_instance_highlights_all_three(qtbot):
    """Explicit regression request: a Power Switch replicated across 3
    member hierarchies of a multi-hierarchy Power Domain -- clicking
    ANY one instance highlights all 3 simultaneously (same group-
    highlight pattern already established for Retention)."""
    member_paths = ["top/u_core", "top/u_dbg", "top/u_fwd"]
    tab, view, icons = _multi_hierarchy_domain_with_internal_supplies(qtbot, member_paths)
    placement = view.add_power_switch(icons["top/u_core"], "SW1", "in", "out", "ctrl", None, "", "")
    switch_icons = view._power_switch_icons[placement.id]
    assert len(switch_icons) == 3

    switch_icons[0].setSelected(True)
    view.scene().selectionChanged.emit()

    for switch_icon in switch_icons:
        assert switch_icon._click_highlighted is True
    assert set(view._click_highlighted_icons) == set(switch_icons)
    _deselect_and_collect(tab)


def test_deselecting_a_replicated_power_switch_clears_the_whole_group_highlight(qtbot):
    member_paths = ["top/u_core", "top/u_dbg", "top/u_fwd"]
    tab, view, icons = _multi_hierarchy_domain_with_internal_supplies(qtbot, member_paths)
    placement = view.add_power_switch(icons["top/u_core"], "SW1", "in", "out", "ctrl", None, "", "")
    switch_icons = view._power_switch_icons[placement.id]
    switch_icons[0].setSelected(True)
    view.scene().selectionChanged.emit()

    view.scene().clearSelection()
    view.scene().selectionChanged.emit()

    for switch_icon in switch_icons:
        assert switch_icon._click_highlighted is False
    assert view._click_highlighted_icons == []
    gc.collect()


def test_add_power_switch_on_a_single_hierarchy_domain_is_unaffected(qtbot):
    """Confirms the pre-existing single-hierarchy path is unchanged --
    exactly one target_id/icon, same as before items 9/10 existed."""
    tab, view, port_icon = _tab_with_domain_and_port(qtbot)

    placement = view.add_power_switch(port_icon, "SW1", "in", "out", "ctrl", None, "", "")

    assert placement.target_ids == [port_icon.key]
    assert len(view._power_switch_icons[placement.id]) == 1
    _deselect_and_collect(tab)


# --- item 3: "Applies to" for a multi-hierarchy Power Switch, driven -------
# --- through the real Add/Edit Power Switch entry points -------------------


def _applies_to_paths(dialog) -> list[str]:
    from PySide6.QtWidgets import QLabel

    labels = dialog.findChildren(QLabel)
    for i, label in enumerate(labels):
        if label.text() == "Applies to:":
            return labels[i + 1].text().split("\n")
    raise AssertionError("no 'Applies to:' row found in dialog")


def test_add_power_switch_dialog_lists_all_three_member_hierarchies(qtbot, monkeypatch):
    """Explicit regression request: a Power Switch on an Internal Supply
    belonging to a multi-hierarchy Power Domain spanning 3 hierarchies
    -- opening Add Power Switch shows "Applies to:" listing all 3
    paths on separate lines, matching the Retention dialog's own
    existing multi-line formatting exactly."""
    import frontend.src.power_intent_view.power_switch_dialog as dialog_mod

    member_paths = ["top/u_core", "top/u_dbg", "top/u_fwd"]
    tab, view, icons = _multi_hierarchy_domain_with_internal_supplies(qtbot, member_paths)
    captured = {}

    def fake_exec(self):
        captured["paths"] = _applies_to_paths(self)
        return dialog_mod.QDialog.DialogCode.Rejected

    monkeypatch.setattr(dialog_mod.PowerSwitchDialog, "exec", fake_exec)

    view._prompt_add_power_switch(icons["top/u_core"])

    assert captured["paths"] == member_paths
    _deselect_and_collect(tab)


def test_edit_power_switch_dialog_lists_all_three_member_hierarchies(qtbot, monkeypatch):
    """Same as the Add test above, but for Edit -- reads the already-
    replicated PowerSwitchPlacement's own owner_paths."""
    import frontend.src.power_intent_view.power_switch_dialog as dialog_mod

    member_paths = ["top/u_core", "top/u_dbg", "top/u_fwd"]
    tab, view, icons = _multi_hierarchy_domain_with_internal_supplies(qtbot, member_paths)
    placement = view.add_power_switch(icons["top/u_core"], "SW1", "in", "out", "ctrl", None, "", "")
    captured = {}

    def fake_exec(self):
        captured["paths"] = _applies_to_paths(self)
        return dialog_mod.QDialog.DialogCode.Rejected

    monkeypatch.setattr(dialog_mod.PowerSwitchDialog, "exec", fake_exec)

    view._prompt_edit_power_switch(icons["top/u_core"], placement)

    assert captured["paths"] == member_paths
    _deselect_and_collect(tab)


# --- bug fix: manually-drawn Supply In connector didn't survive a rebuild --
# root cause (found while investigating the multi-hierarchy replication bug
# below): PowerSwitchIconItem.key used to just BE placement.id, and
# _rebuild()'s connector-restoration loop ran BEFORE _power_switch_icons
# even existed -- so a manual top-anchor connector's own persisted pair
# could never be found there at all, on ANY switch, single-hierarchy
# included.


def test_manual_supply_in_connector_survives_a_rebuild(qtbot):
    tab, view, port_icon = _tab_with_domain_and_port(qtbot)
    upstream = view.add_supply_port(view._boxes["top"], "VDD_UNSWITCHED", QColor("#0000ff"), edge="top", t=0.85)
    placement = view.add_power_switch(port_icon, "SW1", "in", "out", "ctrl", None, "", "")
    switch_icon = view._power_switch_icons[placement.id][0]
    assert view.try_connect_supply_ports(switch_icon, upstream) is True
    assert len(switch_icon.connectors) == 2  # auto bottom + manual top

    view._rebuild()

    new_switch_icon = view._power_switch_icons[placement.id][0]
    assert len(new_switch_icon.connectors) == 2
    assert view._resolve_power_switch_supply_in_name(new_switch_icon) == "VDD_UNSWITCHED"
    _deselect_and_collect(tab)


# --- bug fix: _shorts_power_switch() latent multi-hierarchy gap ------------
# found investigating the same replication bug -- it used to compare
# against the SHARED record's own `placement.target_id` (always the
# "primary" replica's target), so a non-primary replica's own short-circuit
# check never actually fired.


def test_shorts_power_switch_correctly_checks_each_replicas_own_target(qtbot):
    member_paths = ["top/u_core", "top/u_dbg"]
    tab, view, icons = _multi_hierarchy_domain_with_internal_supplies(qtbot, member_paths)
    placement = view.add_power_switch(icons["top/u_core"], "SW1", "in", "out", "ctrl", None, "", "")
    switch_icons = {si.target_icon.owner_box.path: si for si in view._power_switch_icons[placement.id]}

    # u_dbg's own switch replica connected back to u_dbg's OWN target
    # (its own Supply Out) -- must be rejected as a short, exactly like
    # the primary (u_core) replica already correctly rejects this.
    connected = view.try_connect_supply_ports(switch_icons["top/u_dbg"], icons["top/u_dbg"])

    assert connected is False
    _deselect_and_collect(tab)


# --- Bug 2: manually-drawn Supply In connector must replicate across -------
# --- every OTHER member hierarchy's own Power Switch instance --------------


def test_manual_supply_in_replicates_via_a_shared_ancestor_upstream(qtbot):
    """Explicit regression request (a): the upstream is on a common
    ANCESTOR of every member hierarchy (e.g. a top-level unswitched
    supply) -- ONE single shared object, reused directly for every
    replica's own connection, not a separate per-hierarchy instance."""
    member_paths = ["top/u_core", "top/u_dbg", "top/u_fwd"]
    tab, view, icons = _multi_hierarchy_domain_with_internal_supplies(qtbot, member_paths)
    upstream = view.add_supply_port(view._boxes["top"], "VDD_UNSWITCHED", QColor("#0000ff"))
    placement = view.add_power_switch(icons["top/u_core"], "SW1", "in", "out", "ctrl", None, "", "")
    switch_icons = {si.target_icon.owner_box.path: si for si in view._power_switch_icons[placement.id]}

    connected = view.try_connect_supply_ports(switch_icons["top/u_core"], upstream)

    assert connected is True
    for path in member_paths:
        assert view._resolve_power_switch_supply_in_name(switch_icons[path]) == "VDD_UNSWITCHED"
        connector = next(c for c in switch_icons[path].connectors if c.port_a is upstream or c.port_b is upstream)
        assert connector.port_a is upstream or connector.port_b is upstream  # the SAME shared object, every time
    _deselect_and_collect(tab)


def test_manual_supply_in_replicates_via_a_per_hierarchy_replica_upstream(qtbot):
    """Explicit regression request (a), the other resolved case: the
    upstream is ITSELF a legitimate multi-hierarchy replica (same name,
    same domain membership) -- each OTHER member's own switch connects
    to ITS OWN local upstream instance, not literally u_core's."""
    member_paths = ["top/u_core", "top/u_dbg", "top/u_fwd"]
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view._domain_state.domains["d1"] = PowerDomainPlacement(
        id="d1", owner_path=member_paths[0], name="PD_MULTI", color=QColor("#ff0000"), owner_paths=list(member_paths)
    )
    targets = {path: view.add_internal_supply(view._boxes[path], "VDD_SW", QColor("#00aa00")) for path in member_paths}
    local_upstreams = {path: view.add_internal_supply(view._boxes[path], "VDD_LOCAL_UP", QColor("#ff8800")) for path in member_paths}
    placement = view.add_power_switch(targets["top/u_core"], "SW1", "in", "out", "ctrl", None, "", "")
    switch_icons = {si.target_icon.owner_box.path: si for si in view._power_switch_icons[placement.id]}

    connected = view.try_connect_supply_ports(switch_icons["top/u_core"], local_upstreams["top/u_core"])

    assert connected is True
    for path in member_paths:
        assert view._resolve_power_switch_supply_in_name(switch_icons[path]) == "VDD_LOCAL_UP"
        own_upstream = local_upstreams[path]
        connector = next((c for c in switch_icons[path].connectors if c.port_a is own_upstream or c.port_b is own_upstream), None)
        assert connector is not None, f"{path}'s own switch is not connected to ITS OWN local upstream instance"
    _deselect_and_collect(tab)


def test_manual_supply_in_does_not_replicate_when_no_corresponding_upstream_exists(qtbot):
    """Negative case: an upstream local to JUST u_core (no same-name
    replica anywhere else, not an ancestor of the other members either)
    -- the other 2 replicas get NOTHING fabricated, they simply stay
    unconnected."""
    member_paths = ["top/u_core", "top/u_dbg", "top/u_fwd"]
    tab, view, icons = _multi_hierarchy_domain_with_internal_supplies(qtbot, member_paths)
    local_only_upstream = view.add_internal_supply(view._boxes["top/u_core"], "VDD_ONLY_HERE", QColor("#ff8800"))
    placement = view.add_power_switch(icons["top/u_core"], "SW1", "in", "out", "ctrl", None, "", "")
    switch_icons = {si.target_icon.owner_box.path: si for si in view._power_switch_icons[placement.id]}

    connected = view.try_connect_supply_ports(switch_icons["top/u_core"], local_only_upstream)

    assert connected is True
    assert view._resolve_power_switch_supply_in_name(switch_icons["top/u_core"]) == "VDD_ONLY_HERE"
    assert view._resolve_power_switch_supply_in_name(switch_icons["top/u_dbg"]) is None
    assert view._resolve_power_switch_supply_in_name(switch_icons["top/u_fwd"]) is None
    _deselect_and_collect(tab)


def test_deleting_a_replicated_supply_in_connector_removes_it_from_every_member(qtbot):
    """Explicit regression request (b): deleting the connector from ONE
    replica removes the auto-created equivalent connections on the
    other 2 as well."""
    member_paths = ["top/u_core", "top/u_dbg", "top/u_fwd"]
    tab, view, icons = _multi_hierarchy_domain_with_internal_supplies(qtbot, member_paths)
    upstream = view.add_supply_port(view._boxes["top"], "VDD_UNSWITCHED", QColor("#0000ff"))
    placement = view.add_power_switch(icons["top/u_core"], "SW1", "in", "out", "ctrl", None, "", "")
    switch_icons = {si.target_icon.owner_box.path: si for si in view._power_switch_icons[placement.id]}
    view.try_connect_supply_ports(switch_icons["top/u_core"], upstream)
    for path in member_paths:
        assert view._resolve_power_switch_supply_in_name(switch_icons[path]) == "VDD_UNSWITCHED"
    connector = next(c for c in switch_icons["top/u_core"].connectors if c.port_a is upstream or c.port_b is upstream)

    view.delete_connector(connector)

    for path in member_paths:
        assert view._resolve_power_switch_supply_in_name(switch_icons[path]) is None
        # The auto bottom (Supply Out) connector is untouched -- only the
        # manually-drawn top (Supply In) connection is removed.
        assert len(switch_icons[path].connectors) == 1
    _deselect_and_collect(tab)


def test_deleting_a_replicated_supply_in_connector_is_a_single_undo_step(qtbot):
    member_paths = ["top/u_core", "top/u_dbg", "top/u_fwd"]
    tab, view, icons = _multi_hierarchy_domain_with_internal_supplies(qtbot, member_paths)
    upstream = view.add_supply_port(view._boxes["top"], "VDD_UNSWITCHED", QColor("#0000ff"))
    placement = view.add_power_switch(icons["top/u_core"], "SW1", "in", "out", "ctrl", None, "", "")
    switch_icons = {si.target_icon.owner_box.path: si for si in view._power_switch_icons[placement.id]}
    view.try_connect_supply_ports(switch_icons["top/u_core"], upstream)
    connector = next(c for c in switch_icons["top/u_core"].connectors if c.port_a is upstream or c.port_b is upstream)
    view.delete_connector(connector)

    view._undo_stack.undo()

    for path in member_paths:
        assert view._resolve_power_switch_supply_in_name(switch_icons[path]) == "VDD_UNSWITCHED"
    _deselect_and_collect(tab)


# --- bug fix: Power Switch icon must reserve room, not just render clipped -
# --- over/overlapping a sibling or ancestor block. Re-reported as STILL -----
# --- broken after an earlier fix attempt -- root cause investigated fresh: -
# --- _extra_top_margin() was already correctly IMPLEMENTED, but only ever
# --- got APPLIED on the NEXT full _rebuild() (hide/unhide, undo, ...), never
# --- on the switch's own initial placement -- add_power_switch() never
# --- triggered a layout recompute at all (a full _rebuild() would destroy
# --- the very icon it's about to hand back). Fixed via the new, NON-
# --- destructive reflow_hierarchy_scene().


def _stacked_sibling_design() -> Design:
    """Two leaf siblings, u_upper directly above u_lower with the
    layout's own default GAP between them and zero extra slack -- the
    exact shape needed to force a genuine overlap: a switch rendered
    above u_lower's own top edge lands squarely inside u_upper's own
    box unless the layout actually reserves room for it."""
    leaf = Definition(definition_id="leaf", base_name="leaf_def", language=SourceLanguage.VERILOG)
    top = Definition(
        definition_id="top",
        base_name="top_def",
        language=SourceLanguage.VERILOG,
        instances=[Instance(name="u_upper", definition_id="leaf"), Instance(name="u_lower", definition_id="leaf")],
    )
    return Design(definitions={"leaf": leaf, "top": top}, top="top")


def _switch_icon_scene_rect(switch_icon) -> QRectF:
    from frontend.src.power_intent_view.power_switch_overlay import _ICON_SIZE

    half = _ICON_SIZE / 2
    pos = switch_icon.scenePos()
    return QRectF(pos.x() - half, pos.y() - half, _ICON_SIZE, _ICON_SIZE)


def test_power_switch_icon_overlapping_a_sibling_block_is_reproduced_without_the_fix(qtbot, monkeypatch):
    """Confirms the repro FAILS against the current implementation with
    the fix disabled -- proves this test scenario actually exercises
    the reported bug, not a scenario that happened to have enough
    natural slack anyway."""
    import frontend.src.power_intent_view.power_intent_diagram_view as mod

    monkeypatch.setattr(mod._PowerIntentGraphicsView, "_reflow_layout", lambda self: None)
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(_stacked_sibling_design())
    view = tab.diagram._view
    lower_box = view._boxes["top_def/u_lower"]
    upper_box = view._boxes["top_def/u_upper"]
    view._domain_state.domains["d1"] = PowerDomainPlacement(id="d1", owner_path="top_def/u_lower", name="PD_LOWER", color=QColor("#ff0000"))
    icon = view.add_internal_supply(lower_box, "VDD_SW", QColor("#00aa00"))

    placement = view.add_power_switch(icon, "SW1", "in", "out", "ctrl", None, "", "")
    switch_icon = view._power_switch_icons[placement.id][0]

    assert upper_box.sceneBoundingRect().intersects(_switch_icon_scene_rect(switch_icon))
    _deselect_and_collect(tab)


def test_power_switch_icon_no_longer_overlaps_a_sibling_block(qtbot):
    """The real fix, exercised end-to-end: the SAME repro as above, with
    the fix active -- u_upper must have moved to make room, and the
    switch icon must not overlap ANY other box (sibling or otherwise)."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(_stacked_sibling_design())
    view = tab.diagram._view
    lower_box = view._boxes["top_def/u_lower"]
    upper_box = view._boxes["top_def/u_upper"]
    upper_box_pos_before = upper_box.pos()
    view._domain_state.domains["d1"] = PowerDomainPlacement(id="d1", owner_path="top_def/u_lower", name="PD_LOWER", color=QColor("#ff0000"))
    icon = view.add_internal_supply(lower_box, "VDD_SW", QColor("#00aa00"))

    placement = view.add_power_switch(icon, "SW1", "in", "out", "ctrl", None, "", "")
    switch_icon = view._power_switch_icons[placement.id][0]

    switch_rect = _switch_icon_scene_rect(switch_icon)
    for path, box in view._boxes.items():
        if path == "top_def/u_lower":
            continue  # the switch's own target -- expected to sit right at its own top edge, not a real "overlap"
        if path == "top_def":
            continue  # the ancestor CONTAINING the switch -- expected, not the "sibling/parent block" the bug is about
        assert not box.sceneBoundingRect().intersects(switch_rect), f"{path} still overlaps the switch icon"
    assert upper_box.pos() != upper_box_pos_before  # u_upper actually moved to make room
    _deselect_and_collect(tab)


def test_power_switch_room_reservation_survives_deleting_the_switch(qtbot):
    """The reflow also runs on delete -- the room reserved for a since-
    deleted switch's own icon is released, not left as permanent dead
    space (a natural completeness pairing for the room-reservation fix,
    not the originally-reported symptom itself).

    Item 3 (explicit request, "make room" layout): the checkpoint to
    compare back against after deleting the SWITCH is right after
    add_internal_supply() (not the very original, pre-everything
    position) -- the internal supply itself is never deleted here, and
    now ALSO reserves its own INTERIOR room independent of any switch
    (see _extra_interior_top()'s own docstring), so that part of the
    reservation is correctly still owed after the switch-only deletion,
    not a regression."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(_stacked_sibling_design())
    view = tab.diagram._view
    lower_box = view._boxes["top_def/u_lower"]
    upper_box = view._boxes["top_def/u_upper"]
    view._domain_state.domains["d1"] = PowerDomainPlacement(id="d1", owner_path="top_def/u_lower", name="PD_LOWER", color=QColor("#ff0000"))
    icon = view.add_internal_supply(lower_box, "VDD_SW", QColor("#00aa00"))
    upper_box_pos_before = upper_box.pos()  # AFTER the internal supply's own room is already reserved
    placement = view.add_power_switch(icon, "SW1", "in", "out", "ctrl", None, "", "")
    assert upper_box.pos() != upper_box_pos_before

    view.delete_power_switch(placement.id)

    assert upper_box.pos() == upper_box_pos_before
    _deselect_and_collect(tab)


# --- regression guard: Power Switch position must track its target's own --
# --- drag (block-internal repositioning) -- investigated a reported bug ---
# --- report of this NOT working: could not reproduce it against the ------
# --- current implementation across every scenario tried (single-hierarchy,
# --- multi-hierarchy, a real QTest-driven mouse drag, and a drag after a
# --- forced full rebuild) -- SupplyPortIconItem.itemChange() already calls
# --- every registered on_move_callbacks() entry on every real position
# --- change, and PowerSwitchIconItem.update_position() is already
# --- registered there at construction time (both pre-dating this
# --- session's own work). These tests lock the ALREADY-correct behavior in
# --- as a permanent regression guard either way.


def test_power_switch_tracks_its_target_when_dragged_within_its_block(qtbot):
    tab, view, port_icon = _tab_with_domain_and_port(qtbot)
    placement = view.add_power_switch(port_icon, "SW1", "in", "out", "ctrl", None, "", "")
    switch_icon = view._power_switch_icons[placement.id][0]
    connector = next(c for c in switch_icon.connectors if c.port_a is port_icon or c.port_b is port_icon)

    box = view._boxes["top"]
    port_icon.setPos(box.rect().right() - 5, box.rect().top())

    assert switch_icon.scenePos().x() == port_icon.scenePos().x()
    # The auto bottom connector's own route was re-derived too, not left
    # pointing at the target's old position.
    assert connector.path().boundingRect().right() >= port_icon.scenePos().x() - 1
    _deselect_and_collect(tab)


def test_power_switch_top_connector_stays_anchored_when_target_is_dragged(qtbot):
    """Bug fix, explicit report: "the power net is not very anchored to
    the top anchor of the power switch... moving an internal supply
    port... seems getting disconnecting from the power net." Root
    cause: PowerSwitchIconItem.update_position() moved the icon itself
    but never re-routed ITS OWN connectors -- the auto BOTTOM connector
    (Supply Out) happened to still look right after a drag purely by
    accident (it's also registered on the TARGET, whose own real
    itemChange() already re-routes it), but the manually-drawn TOP
    connector (Supply In, to an upstream) is registered only on the
    switch and the upstream -- neither of which moved -- so it was
    never re-routed at all. This test targets the TOP connector
    specifically, since the sibling test above (bottom connector only)
    passed even with the bug still present."""
    tab, view, port_icon = _tab_with_domain_and_port(qtbot)
    box = view._boxes["top"]
    upstream = view.add_supply_port(box, "VDD_UNSWITCHED", QColor("#0000ff"), edge="top", t=0.85)
    placement = view.add_power_switch(port_icon, "SW1", "in", "out", "ctrl", None, "", "")
    switch_icon = view._power_switch_icons[placement.id][0]
    view.try_connect_supply_ports(switch_icon, upstream)
    top_connector = next(c for c in switch_icon.connectors if c.port_a is upstream or c.port_b is upstream)
    rect_before = top_connector.path().boundingRect()

    port_icon.setPos(box.rect().right() - 5, box.rect().top())

    assert top_connector.path().boundingRect() != rect_before
    switch_handle_x = switch_icon.handles["top"].scenePos().x()
    connector_rect = top_connector.path().boundingRect()
    assert connector_rect.left() - 1 <= switch_handle_x <= connector_rect.right() + 1
    _deselect_and_collect(tab)


def test_dragging_one_member_hierarchys_target_does_not_move_other_replicas(qtbot):
    """Explicit "do not conflate with replication" requirement: Name/
    Color/manually-drawn connections ARE replicated across a multi-
    hierarchy Power Domain's own member instances, but POSITION is
    purely local/per-instance -- dragging ONE member's own Supply Port/
    Internal Supply (and its own attached Power Switch) must leave
    every OTHER member hierarchy's own instance and Power Switch
    completely untouched."""
    member_paths = ["top/u_core", "top/u_dbg", "top/u_fwd"]
    tab, view, icons = _multi_hierarchy_domain_with_internal_supplies(qtbot, member_paths)
    placement = view.add_power_switch(icons["top/u_core"], "SW1", "in", "out", "ctrl", None, "", "")
    switch_icons = {si.target_icon.owner_box.path: si for si in view._power_switch_icons[placement.id]}
    other_positions_before = {path: (icons[path].scenePos(), switch_icons[path].scenePos()) for path in ("top/u_dbg", "top/u_fwd")}

    core_box = view._boxes["top/u_core"]
    icons["top/u_core"].setPos(core_box.rect().right() - 5, core_box.rect().top())

    assert switch_icons["top/u_core"].scenePos().x() == icons["top/u_core"].scenePos().x()
    for path, (icon_pos_before, switch_pos_before) in other_positions_before.items():
        assert icons[path].scenePos() == icon_pos_before, f"{path}'s own Internal Supply moved -- position must not replicate"
        assert switch_icons[path].scenePos() == switch_pos_before, f"{path}'s own Power Switch moved -- position must not replicate"
    _deselect_and_collect(tab)


# --- Power Switch icon right-click menu (explicit request) --------------


def test_power_switch_icon_right_click_menu_shows_plain_edit_and_delete(qtbot):
    tab, view, port_icon = _tab_with_domain_and_port(qtbot)
    placement = view.add_power_switch(port_icon, "SW1", "in", "out", "ctrl", None, "on", "")
    switch_icon = view._power_switch_icons[placement.id][0]

    menu = QMenu()
    actions = view._power_switch_icon_context_menu_actions(menu, switch_icon)

    assert [a.text() for a, _cb in actions] == ["Edit", "Delete"]
    _deselect_and_collect(tab)


def test_power_switch_icon_right_click_edit_opens_the_existing_edit_dialog(qtbot, monkeypatch):
    from PySide6.QtWidgets import QDialog

    from frontend.src.power_intent_view.power_switch_dialog import PowerSwitchDialog

    tab, view, port_icon = _tab_with_domain_and_port(qtbot)
    placement = view.add_power_switch(port_icon, "SW1", "in", "out", "ctrl", None, "on", "")
    switch_icon = view._power_switch_icons[placement.id][0]

    captured = {}

    def fake_exec(self):
        captured["name"] = self.name  # the pre-filled existing name
        self.name_edit.setText("SW_RENAMED")
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(PowerSwitchDialog, "exec", fake_exec)
    menu = QMenu()
    actions = view._power_switch_icon_context_menu_actions(menu, switch_icon)
    edit_cb = next(cb for a, cb in actions if a.text() == "Edit")
    edit_cb()

    assert captured["name"] == "SW1"  # the SAME dialog, pre-filled with the existing switch
    assert view._power_switch_state.switches[placement.id].name == "SW_RENAMED"
    _deselect_and_collect(tab)


def test_power_switch_icon_right_click_delete_uses_the_existing_confirmation_flow(qtbot, monkeypatch):
    tab, view, port_icon = _tab_with_domain_and_port(qtbot)
    placement = view.add_power_switch(port_icon, "SW1", "in", "out", "ctrl", None, "on", "")
    switch_icon = view._power_switch_icons[placement.id][0]

    asked = []
    monkeypatch.setattr(message_box, "question", lambda *a, **k: asked.append(a) or QMessageBox.StandardButton.Yes)

    menu = QMenu()
    actions = view._power_switch_icon_context_menu_actions(menu, switch_icon)
    delete_cb = next(cb for a, cb in actions if a.text() == "Delete")
    delete_cb()

    assert len(asked) == 1  # the existing "Delete Power Switch '...'?" confirmation fired
    assert placement.id not in view._power_switch_state.switches
    _deselect_and_collect(tab)


# --- "Switch Control": the RTL net driving the control pin --------------
#
# ON/OFF State are authored against the REAL signal (what the user can
# check against the HDL) and rewritten to the switch's own CONTROL PORT
# name on export. That target used to be a fixed `CTRL` token; it is the
# user's own Control Port field value now, passed in explicitly so the
# `-control_port` declaration and the expression cannot disagree.

from frontend.src.power_intent_view.power_switch_overlay import (  # noqa: E402
    references_control_signal,
    substitute_abstract_control,
    substitute_control_signal,
)

PORT = "pg_ctrl"      # what the user typed into Control Port
SIGNAL = "pwr_switch"  # what they typed into Switch Control


def test_a_an_on_state_that_is_exactly_the_control_signal_becomes_the_port_name():
    """Case (a): the simple form, which nearly every switch uses."""
    assert substitute_control_signal(SIGNAL, SIGNAL, PORT) == PORT


def test_b_a_compound_expression_substitutes_only_the_control_signal():
    """Case (b): every other term is left exactly as authored -- the
    generator has no business reinterpreting a condition it knows
    nothing about."""
    assert substitute_control_signal(f"{SIGNAL} && some_other_cond", SIGNAL, PORT) == f"{PORT} && some_other_cond"
    assert substitute_control_signal(f"!{SIGNAL}", SIGNAL, PORT) == f"!{PORT}"


def test_c_an_expression_not_referencing_the_control_signal_is_emitted_as_authored():
    """Case (c): NOT blocked. The expression may reference a signal this
    app has no field for, and emitting it verbatim is the honest
    outcome; the dialog's inline hint is what raises it."""
    assert substitute_control_signal("sleep_n", SIGNAL, PORT) == "sleep_n"
    assert references_control_signal("sleep_n", SIGNAL) is False


def test_a_longer_signal_containing_the_control_signal_is_not_half_rewritten():
    """The trap a plain string replace falls into: `pwr` inside
    `pwr_switch` is a DIFFERENT net, and rewriting it would produce a
    name that resolves to nothing in a file that still parses."""
    assert substitute_control_signal("pwr_switch", "pwr", PORT) == "pwr_switch"
    assert references_control_signal("pwr_switch", "pwr") is False


def test_a_signal_name_with_hdl_punctuation_substitutes_whole():
    """`\b` flips meaning when the pattern itself starts or ends with a
    non-word character, and real HDL names carry `/` and `[]`."""
    assert substitute_control_signal("u_ctl/en & x", "u_ctl/en", PORT) == f"{PORT} & x"
    assert substitute_control_signal("ctl[0] | y", "ctl[0]", PORT) == f"{PORT} | y"


def test_the_substitution_reverses_exactly_for_import():
    for expression in (SIGNAL, f"!{SIGNAL} && test_mode", "sleep_n"):
        written = substitute_control_signal(expression, SIGNAL, PORT)
        assert substitute_abstract_control(written, SIGNAL, PORT) == expression


def test_a_name_merely_containing_the_port_name_is_not_rewritten_on_import():
    """`pg_ctrl_n` is its own name, not the control port."""
    assert substitute_abstract_control(f"pg_ctrl_n && {PORT}", SIGNAL, PORT) == f"pg_ctrl_n && {SIGNAL}"


def test_a_missing_port_name_leaves_the_expression_alone():
    """No target to substitute toward -- emitting the signal verbatim is
    better than substituting an empty string into the expression."""
    assert substitute_control_signal(SIGNAL, SIGNAL, "") == SIGNAL
