"""'Add Power Supply' -- the Power Intent tab Diagram's right-click
placement of a user-colored supply-port icon, draggable along a box's
edges, connectable to another supply port via handles that relocate to
match the icon's current edge (top/bottom vs left/right) with a
direction-aware orthogonal connector route. Also: right-click Edit/
Delete on a user-placed port (not offered for a macro's PG pins), and
same-hierarchy name-uniqueness validation. Diagram-only for this first
pass (see supply_port_overlay.py's module docstring) -- exercised
against mock RTL only, no PowerIntentDesign/MappedDesign involved at
all.
"""

import gc

import pytest
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QMenu, QStyleOptionGraphicsItem

from frontend.src.power_intent_view.add_power_supply_dialog import AddPowerSupplyDialog
from frontend.src.power_intent_view.diagram_tool_palette import DiagramMode
from frontend.src.power_intent_view.power_domain_overlay import PowerDomainPlacement, SupplySetPlacement
from frontend.src.power_intent_view.power_intent_tab import PowerIntentTab
from frontend.src.power_intent_view.supply_port_overlay import (
    _ICON_SIZE,
    SupplyPortConnectorItem,
    connector_anchor_scene_pos,
    is_nested_pair,
)
from frontend.src.shared.mock_backend import build_mock_design


def _anchor(icon):
    """A connector's own real terminal point for `icon` -- its correct
    ANCHOR HANDLE, not its bare (and, for every icon type here, WRONG --
    see connector_anchor_scene_pos()'s own docstring) scenePos()."""
    return connector_anchor_scene_pos(icon)


def _deselect_and_collect(tab: PowerIntentTab) -> None:
    if tab.diagram._view.scene() is not None:
        tab.diagram._view.scene().clearSelection()
    gc.collect()


def _is_orthogonal(waypoints) -> bool:
    return all(a.x() == b.x() or a.y() == b.y() for a, b in zip(waypoints, waypoints[1:]))


# --- AddPowerSupplyDialog -------------------------------------------------


def test_dialog_ok_button_disabled_until_a_name_is_entered(qtbot):
    dialog = AddPowerSupplyDialog()
    qtbot.addWidget(dialog)
    assert dialog._ok_button.isEnabled() is False

    dialog.name_edit.setText("VDD")

    assert dialog._ok_button.isEnabled() is True

    dialog.name_edit.setText("   ")

    assert dialog._ok_button.isEnabled() is False


def test_dialog_exposes_name_and_default_color(qtbot):
    dialog = AddPowerSupplyDialog()
    qtbot.addWidget(dialog)
    dialog.name_edit.setText("  VDD  ")

    assert dialog.name == "VDD"  # stripped
    assert dialog.color.isValid()


# --- add_supply_port() ----------------------------------------------------


def test_add_supply_port_creates_an_icon_anchored_to_the_top_edge(qtbot):
    """No more per-instance tint (explicit request: the supply-port.svg
    asset itself is now always rendered fully black) -- `icon.color`
    still exists and still drives the handles/connector colour, just no
    longer the icon's own drawn glyph."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    box = tab.diagram._view._boxes["top"]  # has children -- both handles expected, see the leaf-block tests below

    icon = tab.diagram._view.add_supply_port(box, "VDD", QColor("#ff0000"))

    assert icon in tab.diagram._view._supply_port_icons.values()
    assert icon.parentItem() is box
    assert icon.color == QColor("#ff0000")
    rect = box.rect()
    assert icon.pos() == QPointF(rect.x() + rect.width() / 2, rect.y())  # top edge, centered
    assert set(icon.handles) == {"top", "bottom"}
    assert not icon.boundingRect().isEmpty()
    _deselect_and_collect(tab)


def test_dragging_a_supply_port_clamps_to_the_owning_boxs_perimeter(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    box = tab.diagram._view._boxes["top/u_core"]
    icon = tab.diagram._view.add_supply_port(box, "VDD", QColor("#00ff00"))
    rect = box.rect()

    # Drag far outside the box, well past its right edge, roughly mid-height.
    icon.setPos(rect.right() + 500, rect.center().y())

    pos = icon.pos()
    assert pos.x() == rect.right()  # snapped onto the right edge
    assert rect.top() <= pos.y() <= rect.bottom()
    _deselect_and_collect(tab)


def test_context_menu_offers_add_power_supply(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    box = tab.diagram._view._boxes["top/u_core"]

    from PySide6.QtWidgets import QMenu

    menu = QMenu()
    actions = tab.diagram._view._extra_context_menu_actions(menu, box)

    labels = [action.text() for action, _callback in actions]
    assert "Add Power Supply" in labels


# --- connector drag --------------------------------------------------------


def test_is_nested_pair_true_for_ancestor_descendant_either_direction():
    assert is_nested_pair("top", "top/u_core") is True
    assert is_nested_pair("top/u_core", "top") is True


def test_is_nested_pair_false_for_siblings():
    assert is_nested_pair("top/u_core", "top/u_dbg") is False


def test_is_nested_pair_true_for_the_same_path():
    """Found via Power Switch (a switch's own top anchor commonly needs
    to connect to an upstream Supply Port on the exact SAME box as the
    switch's own target) -- a degenerate/reflexive case of "nested," not
    a loosening of the sibling-rejection rule above (still False)."""
    assert is_nested_pair("top", "top") is True


def test_try_connect_supply_ports_succeeds_for_two_icons_on_the_same_box(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    root_box = view._boxes["top"]
    a = view.add_supply_port(root_box, "A", QColor("#111111"))
    b = view.add_supply_port(root_box, "B", QColor("#222222"))

    created = view.try_connect_supply_ports(a, b)

    assert created is True
    assert len(view._supply_connectors) == 1
    _deselect_and_collect(tab)


def test_try_connect_supply_ports_succeeds_for_a_nested_pair(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    root_box = view._boxes["top"]
    child_box = view._boxes["top/u_core"]
    outer = view.add_supply_port(root_box, "VDD", QColor("#123456"))
    inner = view.add_supply_port(child_box, "VDD_CORE", QColor("#654321"))

    created = view.try_connect_supply_ports(outer, inner)

    assert created is True
    assert len(view._supply_connectors) == 1
    connector = view._supply_connectors[0]
    assert isinstance(connector, SupplyPortConnectorItem)
    assert connector in outer.connectors
    assert connector in inner.connectors
    assert connector.scene() is view.scene()
    _deselect_and_collect(tab)


def test_try_connect_supply_ports_rejects_siblings(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    a = view.add_supply_port(view._boxes["top/u_core"], "A", QColor("#111111"))
    b = view.add_supply_port(view._boxes["top/u_dbg"], "B", QColor("#222222"))

    created = view.try_connect_supply_ports(a, b)

    assert created is False
    assert view._supply_connectors == []
    _deselect_and_collect(tab)


def test_try_connect_supply_ports_rejects_connecting_to_itself(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    a = view.add_supply_port(view._boxes["top/u_core"], "A", QColor("#111111"))

    assert view.try_connect_supply_ports(a, a) is False
    _deselect_and_collect(tab)


# --- Duplicate power net prevention ----------------------------------------


def test_try_connect_supply_ports_rejects_a_duplicate_net_same_direction(qtbot, monkeypatch):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    a = view.add_supply_port(view._boxes["top"], "A", QColor("#111111"))
    b = view.add_supply_port(view._boxes["top/u_core"], "B", QColor("#222222"))
    assert view.try_connect_supply_ports(a, b) is True
    warnings = []
    monkeypatch.setattr(
        "frontend.src.power_intent_view.power_intent_diagram_view.message_box.warning",
        lambda parent, title, text: warnings.append((title, text)),
    )

    created = view.try_connect_supply_ports(a, b)

    assert created is False
    assert len(view._supply_connectors) == 1  # no second connector was drawn
    assert len(warnings) == 1
    assert "already" in warnings[0][1].lower()
    _deselect_and_collect(tab)


def test_try_connect_supply_ports_rejects_a_duplicate_net_reversed_direction(qtbot, monkeypatch):
    """Direction-agnostic (explicit request): B-to-A counts as the same
    existing connection as an already-made A-to-B one."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    a = view.add_supply_port(view._boxes["top"], "A", QColor("#111111"))
    b = view.add_supply_port(view._boxes["top/u_core"], "B", QColor("#222222"))
    assert view.try_connect_supply_ports(a, b) is True
    warnings = []
    monkeypatch.setattr(
        "frontend.src.power_intent_view.power_intent_diagram_view.message_box.warning",
        lambda parent, title, text: warnings.append((title, text)),
    )

    created = view.try_connect_supply_ports(b, a)  # reversed

    assert created is False
    assert len(view._supply_connectors) == 1
    assert len(warnings) == 1
    _deselect_and_collect(tab)


def test_try_connect_supply_ports_allows_a_different_pair_sharing_one_endpoint(qtbot, monkeypatch):
    """A net between a DIFFERENT pair is still allowed even when one
    endpoint is shared with an already-connected pair."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    a = view.add_supply_port(view._boxes["top"], "A", QColor("#111111"))
    b = view.add_supply_port(view._boxes["top/u_core"], "B", QColor("#222222"))
    c = view.add_supply_port(view._boxes["top/u_dbg"], "C", QColor("#333333"))
    assert view.try_connect_supply_ports(a, b) is True
    warnings = []
    monkeypatch.setattr(
        "frontend.src.power_intent_view.power_intent_diagram_view.message_box.warning",
        lambda parent, title, text: warnings.append((title, text)),
    )

    created = view.try_connect_supply_ports(a, c)  # shares endpoint `a`, but a genuinely different pair

    assert created is True
    assert len(view._supply_connectors) == 2
    assert warnings == []
    _deselect_and_collect(tab)


def test_existing_connection_between_is_direction_agnostic(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    a = view.add_supply_port(view._boxes["top"], "A", QColor("#111111"))
    b = view.add_supply_port(view._boxes["top/u_core"], "B", QColor("#222222"))
    view.try_connect_supply_ports(a, b)

    assert view._existing_connection_between(a, b) is True
    assert view._existing_connection_between(b, a) is True
    _deselect_and_collect(tab)


def test_moving_a_connected_icon_updates_the_connector_route(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    root_box = view._boxes["top"]
    child_box = view._boxes["top/u_core"]
    outer = view.add_supply_port(root_box, "VDD", QColor("#123456"))
    inner = view.add_supply_port(child_box, "VDD_CORE", QColor("#654321"))
    view.try_connect_supply_ports(outer, inner)
    connector = view._supply_connectors[0]
    rect_before = connector.path().boundingRect()

    inner.setPos(child_box.rect().left(), child_box.rect().center().y())

    rect_after = connector.path().boundingRect()
    assert rect_after != rect_before
    _deselect_and_collect(tab)


def test_connector_color_follows_either_endpoint_recoloring(qtbot):
    """Bug fix (general case -- same fix as Power Switch's own
    connector, see test_power_switch_diagram.py): a connector's own
    drawn pen must stay live-bound to `port_a.color`, refreshing
    whenever EITHER endpoint recolors (port_a directly, or port_b --
    since a PG pin's own recolor() is how a macro pin visually adopts
    the wire it's connected to; either direction must keep the drawn
    line itself in sync, not just the icon glyphs)."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    root_box = view._boxes["top"]
    child_box = view._boxes["top/u_core"]
    outer = view.add_supply_port(root_box, "VDD", QColor("#123456"))
    inner = view.add_supply_port(child_box, "VDD_CORE", QColor("#654321"))
    view.try_connect_supply_ports(outer, inner)
    connector = view._supply_connectors[0]

    outer.recolor(QColor("#00ff00"))

    assert connector.pen().color() == QColor("#00ff00")
    _deselect_and_collect(tab)


def test_delete_connector_unregisters_the_recolor_callback_from_both_endpoints(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    root_box = view._boxes["top"]
    child_box = view._boxes["top/u_core"]
    outer = view.add_supply_port(root_box, "VDD", QColor("#123456"))
    inner = view.add_supply_port(child_box, "VDD_CORE", QColor("#654321"))
    view.try_connect_supply_ports(outer, inner)
    connector = view._supply_connectors[0]

    view.delete_connector(connector)

    assert connector._sync_pen not in outer.on_recolor_callbacks
    assert connector._sync_pen not in inner.on_recolor_callbacks
    outer.recolor(QColor("#00ff00"))  # must not raise
    _deselect_and_collect(tab)


def test_connector_route_is_orthogonal_not_a_straight_diagonal(qtbot):
    """The path between two non-vertically-aligned icons must bend,
    not run as one straight diagonal segment -- every consecutive pair
    of waypoints must be axis-aligned."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    outer = view.add_supply_port(view._boxes["top"], "VDD", QColor("#123456"))
    inner = view.add_supply_port(view._boxes["top/u_core"], "VDD_CORE", QColor("#654321"))
    view.try_connect_supply_ports(outer, inner)
    connector = view._supply_connectors[0]

    assert len(connector.waypoints) >= 3  # a real bend exists, not a single straight segment
    assert _is_orthogonal(connector.waypoints)
    assert connector.waypoints[0] == _anchor(outer)
    assert connector.waypoints[-1] == _anchor(inner)
    _deselect_and_collect(tab)


# --- rebuild persistence ---------------------------------------------------


def test_supply_ports_survive_hiding_an_unrelated_block(qtbot):
    """Hiding top/u_dbg triggers a full _rebuild() of the hierarchy scene
    (every InstanceBoxItem, including top/u_core's, gets destroyed and
    recreated) -- a supply port anchored to top/u_core (untouched by the
    hide) must still be there afterward, not wiped out just because
    *some* rebuild happened."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view.add_supply_port(view._boxes["top/u_core"], "VDD", QColor("#ff00ff"))
    assert len([i for i in view._supply_port_icons.values() if not i.fixed]) == 1

    tab.tree._toggle_hidden("top/u_dbg")  # unrelated sibling -- triggers _rebuild()

    icons = [i for i in view._supply_port_icons.values() if not i.fixed]
    assert len(icons) == 1
    restored = icons[0]
    assert restored.name == "VDD"
    assert restored.color == QColor("#ff00ff")
    assert restored.owner_box is view._boxes["top/u_core"]  # a genuinely new box object, same path
    assert restored.parentItem() is view._boxes["top/u_core"]
    _deselect_and_collect(tab)


def test_a_dragged_position_survives_a_rebuild(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top/u_core"]
    icon = view.add_supply_port(box, "VDD", QColor("#ff00ff"))
    icon.setPos(box.rect().left(), box.rect().center().y())  # drag onto the left edge

    tab.tree._toggle_hidden("top/u_dbg")

    restored = [i for i in view._supply_port_icons.values() if not i.fixed][0]
    new_box = view._boxes["top/u_core"]
    assert restored.pos().x() == new_box.rect().left()  # still on the left edge, not reset to top-center


def test_a_supply_port_disappears_only_while_its_own_owner_is_hidden(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view.add_supply_port(view._boxes["top/u_core"], "VDD", QColor("#ff00ff"))

    tab.tree._toggle_hidden("top/u_core")  # hide the port's own owner this time

    assert "top/u_core" not in view._boxes
    assert [i for i in view._supply_port_icons.values() if not i.fixed] == []

    tab.tree._toggle_hidden("top/u_core")  # unhide again

    icons = [i for i in view._supply_port_icons.values() if not i.fixed]
    assert len(icons) == 1
    assert icons[0].name == "VDD"
    _deselect_and_collect(tab)


def test_a_connector_survives_a_rebuild(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    outer = view.add_supply_port(view._boxes["top"], "VDD", QColor("#123456"))
    inner = view.add_supply_port(view._boxes["top/u_core"], "VDD_CORE", QColor("#654321"))
    view.try_connect_supply_ports(outer, inner)

    tab.tree._toggle_hidden("top/u_dbg")

    assert len(view._supply_connectors) == 1
    connector = view._supply_connectors[0]
    assert connector.port_a.key == outer.key
    assert connector.port_b.key == inner.key
    _deselect_and_collect(tab)


# --- macro PG pins -----------------------------------------------------------


def test_macro_boxes_auto_get_a_fixed_icon_per_pg_and_pg_internal_pin(qtbot):
    """top/u_ram (ram_macro) has VDD (PortDirection.PG) and VSS
    (PortDirection.PG_INTERNAL) in the mock fixture -- both now surface
    (PG_INTERNAL support added on explicit request; VSS's own placement
    is tagged is_internal=True, see supply_port_overlay.py's own
    SupplyPortPlacement docstring)."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view

    pg_icons = {i.name: i for i in view._supply_port_icons.values() if i.owner_box.path == "top/u_ram"}

    assert set(pg_icons) == {"VDD", "VSS"}
    vdd_icon = pg_icons["VDD"]
    assert vdd_icon.fixed is True
    assert vdd_icon.color == QColor("#9e9e9e")  # default "unconnected" grey
    assert vdd_icon.placement.is_internal is False
    assert pg_icons["VSS"].placement.is_internal is True
    _deselect_and_collect(tab)


def test_macro_internal_pg_pin_renders_with_the_internal_supply_icon_and_no_bottom_anchor(qtbot):
    """Bug fix, explicit request: a macro's own INTERNAL PG pin
    (top/u_ram's VSS, PortDirection.PG_INTERNAL -> placement.is_internal
    is True) previously rendered with the plain Supply Port glyph, same
    as the main PG pin -- it must render with the Internal Supply glyph
    instead, matching a real, user-placed Internal Supply icon
    (test_internal_supply.py's own
    test_internal_supply_icon_renders_a_different_glyph_than_supply_port
    establishes what that glyph looks like). The bottom/interior anchor
    was already correctly absent before this fix (interior_key is keyed
    on placement.fixed alone, not is_internal) -- locked in here too so
    a future change can't silently regress it alongside the icon fix."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    vss_icon = next(i for i in view._supply_port_icons.values() if i.owner_box.path == "top/u_ram" and i.name == "VSS")

    assert vss_icon.placement.is_internal is True
    assert set(vss_icon.handles) == {"top"}  # no bottom/interior anchor

    internal_pin_image = _render_icon_to_image(vss_icon)
    vdd_icon = next(i for i in view._supply_port_icons.values() if i.owner_box.path == "top/u_ram" and i.name == "VDD")
    main_pin_image = _render_icon_to_image(vdd_icon)
    assert internal_pin_image.cacheKey() != main_pin_image.cacheKey()  # genuinely different glyph
    _deselect_and_collect(tab)


def test_macro_main_pg_pin_still_renders_with_the_supply_port_icon_and_no_bottom_anchor(qtbot):
    """Regression guard: the existing main-PG-pin case (top/u_ram's VDD,
    PortDirection.PG -> placement.is_internal is False) must remain
    unaffected by the internal-PG-pin icon fix above -- still the plain
    Supply Port glyph, still no bottom/interior anchor."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    vdd_icon = next(i for i in view._supply_port_icons.values() if i.owner_box.path == "top/u_ram" and i.name == "VDD")

    assert vdd_icon.placement.is_internal is False
    assert set(vdd_icon.handles) == {"top"}  # no bottom/interior anchor
    _deselect_and_collect(tab)


# --- leaf-block anchor removal (generalized beyond macros) ------------------


def test_supply_port_on_a_leaf_rtl_block_has_no_bottom_anchor(qtbot):
    """Generalization (explicit request): the "no interior/bottom
    anchor" rule that already applied to a macro's own fixed PG pins
    now also applies to a real, MOVABLE, user-placed Supply Port/
    Internal Supply anchored to a plain RTL block that simply has no
    child hierarchy blocks of its own -- top/u_core (definition "core")
    has no instances, so it's a leaf despite being ordinary RTL, not a
    macro."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top/u_core"]
    assert box.is_leaf is True
    assert box.definition.is_black_box is False  # genuinely not a macro

    icon = view.add_supply_port(box, "VDD_CORE", QColor("#ff0000"))

    assert set(icon.handles) == {"top"}
    _deselect_and_collect(tab)


def test_supply_port_on_a_macro_still_has_no_bottom_anchor(qtbot):
    """Regression guard: the pre-existing macro case must still behave
    identically after generalizing the underlying check from
    `placement.fixed` to `owner_box.is_leaf`."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top/u_ram"]
    assert box.is_leaf is True
    assert box.definition.is_black_box is True

    icon = next(i for i in view._supply_port_icons.values() if i.owner_box.path == "top/u_ram" and i.name == "VDD")

    assert set(icon.handles) == {"top"}
    _deselect_and_collect(tab)


def test_supply_port_on_a_block_with_children_still_has_a_bottom_anchor(qtbot):
    """Positive-case guard: a block that DOES have child hierarchy
    blocks (top itself -- u_core/u_dbg/u_ram/u_fwd) keeps BOTH handles,
    confirming the generalization doesn't over-fire."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top"]
    assert box.is_leaf is False

    icon = view.add_supply_port(box, "VDD_TOP", QColor("#00ff00"))

    assert set(icon.handles) == {"top", "bottom"}
    _deselect_and_collect(tab)


def test_leaf_block_icon_keeps_a_single_handle_across_edge_changes(qtbot):
    """_relayout_handles() -- reached whenever a movable icon is dragged
    to a different edge -- must keep excluding the interior handle for a
    leaf-block icon rather than letting it regrow a second one once the
    icon's own edge changes (a movable macro PG pin never exercises this
    path at all, since PG pins aren't draggable -- this is genuinely new
    coverage the macro-only version of this rule never needed)."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top/u_core"]
    icon = view.add_supply_port(box, "VDD_CORE", QColor("#ff0000"))
    assert set(icon.handles) == {"top"}

    icon.setPos(box.rect().right(), box.rect().center().y())
    assert icon.placement.edge == "right"
    assert set(icon.handles) == {"right"}

    icon.setPos(box.rect().center().x(), box.rect().bottom())
    assert icon.placement.edge == "bottom"
    assert set(icon.handles) == {"bottom"}
    _deselect_and_collect(tab)


def test_pg_pin_icon_sits_on_the_top_edge_with_its_label_inside_the_box(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top/u_ram"]
    icon = next(i for i in view._supply_port_icons.values() if i.owner_box.path == "top/u_ram" and i.name == "VDD")

    assert icon.pos().y() == box.rect().top()  # anchored on the top edge, not bottom

    label_scene_y = icon.mapToScene(icon.label.pos()).y()
    assert label_scene_y > icon.scenePos().y()  # label sits below the icon...
    assert box.rect().top() < icon.mapToItem(box, icon.label.pos()).y() < box.rect().bottom()  # ...i.e. inside the box
    _deselect_and_collect(tab)


def test_pg_pin_icon_is_not_movable(qtbot):
    """Reverted: PG pins were briefly made draggable around the macro
    boundary, then explicitly reverted back to fixed -- for both macro
    and non-macro/"soft" hierarchy instances, i.e. everywhere a PG pin
    can appear (in practice, only macros ever have PortDirection.PG
    ports at all, but the flag logic itself isn't macro-specific)."""
    from PySide6.QtWidgets import QGraphicsItem

    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    icon = next(i for i in view._supply_port_icons.values() if i.owner_box.path == "top/u_ram" and i.name == "VDD")

    assert bool(icon.flags() & QGraphicsItem.ItemIsMovable) is False
    _deselect_and_collect(tab)


# --- label drag --------------------------------------------------------------


def test_label_can_be_dragged_but_stays_clamped_near_the_icon(qtbot):
    from frontend.src.power_intent_view.supply_port_overlay import _LABEL_MAX_RADIUS

    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    icon = view.add_supply_port(view._boxes["top/u_core"], "VDD", QColor("#ff0000"))

    icon.label.setPos(10000, 10000)  # try to drag it far away

    pos = icon.label.pos()
    distance = (pos.x() ** 2 + pos.y() ** 2) ** 0.5
    assert distance <= _LABEL_MAX_RADIUS + 1e-6
    _deselect_and_collect(tab)


def test_label_position_survives_a_rebuild(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    icon = view.add_supply_port(view._boxes["top/u_core"], "VDD", QColor("#ff0000"))
    icon.label.setPos(-5, -20)  # drag it up, above the icon

    tab.tree._toggle_hidden("top/u_dbg")

    restored = next(i for i in view._supply_port_icons.values() if not i.fixed)
    assert (restored.label.pos().x(), restored.label.pos().y()) == (-5, -20)
    _deselect_and_collect(tab)


# --- icon rotation -------------------------------------------------------------


def _render_icon_to_image(icon) -> QImage:
    """Drives SupplyPortIconItem.paint() directly, off-scene, into a
    plain QPixmap -- the "directly-testable core" precedent this
    codebase already uses elsewhere for whatever can't be driven through
    a real (headlessly-undrivable) Qt event loop; here it's simpler to
    just call paint() than to spin up a full QGraphicsScene render pass.
    Translated so the icon's own local origin (its center, per
    boundingRect()) lands at the pixmap's own center."""
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.translate(32, 32)
    icon.paint(painter, QStyleOptionGraphicsItem())
    painter.end()
    return pixmap.toImage()


def test_icon_rotates_to_match_its_current_edge(qtbot):
    """Rotation is now applied live inside paint() (no more a baked-in
    QTransform on a cached pixmap) -- verified by actually driving
    paint() at each edge and confirming the rendered image really does
    differ per edge, not just that `_ROTATION_FOR_EDGE[edge]` differs
    (which would be true by construction and wouldn't actually exercise
    paint() at all)."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top/u_core"]
    icon = view.add_supply_port(box, "VDD", QColor("#ff0000"))
    assert icon.placement.edge == "top"
    top_image = _render_icon_to_image(icon)

    icon.setPos(box.rect().right(), box.rect().center().y())
    assert icon.placement.edge == "right"
    right_image = _render_icon_to_image(icon)

    icon.setPos(box.rect().center().x(), box.rect().bottom())
    assert icon.placement.edge == "bottom"
    bottom_image = _render_icon_to_image(icon)

    icon.setPos(box.rect().left(), box.rect().center().y())
    assert icon.placement.edge == "left"
    left_image = _render_icon_to_image(icon)

    # Each edge renders a genuinely different (rotated) image -- not the
    # same image reused regardless of orientation.
    images = [top_image, right_image, bottom_image, left_image]
    assert len({img.cacheKey() for img in images}) == 4
    _deselect_and_collect(tab)


# --- bug 5: Supply Port icon is coloured with the user's own chosen colour --


def _first_opaque_pixel_color(image: QImage):
    """Scans for the first fully-opaque pixel in a rendered icon image --
    not a fixed coordinate, so this stays correct regardless of exactly
    where the arrow glyph's own silhouette happens to fall within the
    render target."""
    for y in range(image.height()):
        for x in range(image.width()):
            pixel = image.pixelColor(x, y)
            if pixel.alpha() == 255:
                return pixel
    raise AssertionError("no fully-opaque pixel found in the rendered icon")


def test_two_supply_ports_with_different_colors_render_visibly_distinct_pixels(qtbot):
    """Explicit request: check actual pixel colour output, not just that
    a colour property was stored correctly -- render_tinted_svg()
    (shared/diagram/svg_tint.py) composites the port's own stored colour
    onto the black silhouette via QPainter.CompositionMode_SourceIn at
    paint time, not by tinting the SVG source file itself."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top/u_core"]
    red_icon = view.add_supply_port(box, "VDD_RED", QColor(255, 0, 0))
    blue_icon = view.add_supply_port(box, "VDD_BLUE", QColor(0, 0, 255))

    red_pixel = _first_opaque_pixel_color(_render_icon_to_image(red_icon))
    blue_pixel = _first_opaque_pixel_color(_render_icon_to_image(blue_icon))

    assert (red_pixel.red(), red_pixel.green(), red_pixel.blue()) == (255, 0, 0)
    assert (blue_pixel.red(), blue_pixel.green(), blue_pixel.blue()) == (0, 0, 255)
    _deselect_and_collect(tab)


def test_supply_port_icon_color_is_unaffected_by_rotation(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top/u_core"]
    icon = view.add_supply_port(box, "VDD", QColor(0, 255, 0))
    assert icon.placement.edge == "top"
    top_pixel = _first_opaque_pixel_color(_render_icon_to_image(icon))

    icon.setPos(box.rect().right(), box.rect().center().y())
    assert icon.placement.edge == "right"
    right_pixel = _first_opaque_pixel_color(_render_icon_to_image(icon))

    icon.setPos(box.rect().center().x(), box.rect().bottom())
    assert icon.placement.edge == "bottom"
    bottom_pixel = _first_opaque_pixel_color(_render_icon_to_image(icon))

    for pixel in (top_pixel, right_pixel, bottom_pixel):
        assert (pixel.red(), pixel.green(), pixel.blue()) == (0, 255, 0)
    _deselect_and_collect(tab)


def test_recolor_updates_the_icons_own_rendered_color_immediately(qtbot):
    """Explicit request: "If the user later changes a Supply Port's
    color... the icon on the diagram must update to reflect the new
    color immediately." recolor() is the directly-testable core used by
    both the Edit dialog and PG-pin-wiring auto-recolor -- see
    edit_supply_port()/SupplyPortConnectorItem's own callers."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    icon = view.add_supply_port(view._boxes["top/u_core"], "VDD", QColor(255, 0, 0))
    before_pixel = _first_opaque_pixel_color(_render_icon_to_image(icon))
    assert (before_pixel.red(), before_pixel.green(), before_pixel.blue()) == (255, 0, 0)

    icon.recolor(QColor(0, 255, 0))

    after_pixel = _first_opaque_pixel_color(_render_icon_to_image(icon))
    assert (after_pixel.red(), after_pixel.green(), after_pixel.blue()) == (0, 255, 0)
    _deselect_and_collect(tab)


def test_pg_pin_stays_on_the_top_edge_since_it_cannot_be_dragged(qtbot):
    """Follows directly from test_pg_pin_icon_is_not_movable: with no
    ItemIsMovable flag, nothing ever calls setPos() on a PG pin via user
    interaction, so it stays at its original top-edge placement/rotation
    indefinitely -- confirmed across a rebuild too, not just at
    creation."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    icon = next(i for i in view._supply_port_icons.values() if i.owner_box.path == "top/u_ram" and i.name == "VDD")
    assert icon.placement.edge == "top"

    tab.tree._toggle_hidden("top/u_dbg")  # unrelated rebuild

    restored = next(i for i in view._supply_port_icons.values() if i.owner_box.path == "top/u_ram" and i.name == "VDD")
    assert restored.placement.edge == "top"
    _deselect_and_collect(tab)


def test_connecting_a_supply_port_to_a_pg_pin_recolors_the_pin(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    # top/u_ram is a child of top -- a supply port on "top" is a valid
    # (nested) source for the macro's PG pin.
    supply = view.add_supply_port(view._boxes["top"], "VDD_1V0", QColor("#00aaff"))
    pg_pin = next(i for i in view._supply_port_icons.values() if i.owner_box.path == "top/u_ram" and i.name == "VDD")

    created = view.try_connect_supply_ports(supply, pg_pin)

    assert created is True
    assert pg_pin.color == QColor("#00aaff")
    _deselect_and_collect(tab)


def test_pg_pin_color_survives_a_rebuild_after_being_connected(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    supply = view.add_supply_port(view._boxes["top"], "VDD_1V0", QColor("#00aaff"))
    pg_pin = next(i for i in view._supply_port_icons.values() if i.owner_box.path == "top/u_ram" and i.name == "VDD")
    view.try_connect_supply_ports(supply, pg_pin)

    tab.tree._toggle_hidden("top/u_dbg")  # unrelated rebuild

    restored_pin = next(i for i in view._supply_port_icons.values() if i.owner_box.path == "top/u_ram" and i.name == "VDD")
    assert restored_pin.color == QColor("#00aaff")
    _deselect_and_collect(tab)


# --- handles relocate to match the icon's edge -------------------------------


def test_handles_relocate_to_left_right_when_icon_is_on_a_side_edge(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top"]  # has children -- both handles expected, see the leaf-block tests below
    icon = view.add_supply_port(box, "VDD", QColor("#ff0000"))
    assert set(icon.handles) == {"top", "bottom"}

    icon.setPos(box.rect().right(), box.rect().center().y())  # drag onto the right edge

    assert set(icon.handles) == {"left", "right"}
    half = _ICON_SIZE / 2
    assert icon.handles["left"].rect().center() == QPointF(-half, 0)
    assert icon.handles["right"].rect().center() == QPointF(half, 0)
    _deselect_and_collect(tab)


def test_handles_relocate_back_to_top_bottom_when_dragged_back_off_a_side_edge(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top"]  # has children -- both handles expected, see the leaf-block tests below
    icon = view.add_supply_port(box, "VDD", QColor("#ff0000"))
    icon.setPos(box.rect().right(), box.rect().center().y())
    assert set(icon.handles) == {"left", "right"}

    icon.setPos(box.rect().center().x(), box.rect().top())  # back onto the top edge

    assert set(icon.handles) == {"top", "bottom"}
    _deselect_and_collect(tab)


def test_same_handle_objects_are_reused_across_a_relayout(qtbot):
    """_relayout_handles() must reposition the existing 2 handle objects,
    not destroy/recreate them -- an in-progress connector drag holds a
    direct reference to one (power_intent_diagram_view.py's
    `_connector_drag_origin`), which would dangle if the handle were
    replaced instead of moved."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top/u_core"]
    icon = view.add_supply_port(box, "VDD", QColor("#ff0000"))
    original_handles = set(id(h) for h in icon.handles.values())

    icon.setPos(box.rect().right(), box.rect().center().y())

    new_handles = set(id(h) for h in icon.handles.values())
    assert new_handles == original_handles
    _deselect_and_collect(tab)


# --- a LEAF block's "interior" handle is dropped entirely --------------------
# Explicit request, generalized beyond the original macro-only case: the
# handle facing INTO a leaf block's own body (the icon sits exactly on the
# box's boundary line -- for a fixed macro PG pin, always anchored at
# edge="top", see build_supply_port_overlay(), so its own "bottom" handle
# points down into the macro's own interior) is never a valid connection
# point, since there's nothing "below" a leaf for it to ever connect to.
# Scoped to any LEAF-block icon now (InstanceBoxItem.is_leaf), fixed macro PG
# pins included -- a real, movable user-placed port on a block that DOES have
# child hierarchy blocks still gets both handles regardless of which edge
# it's anchored to (see test_supply_port_on_a_block_with_children_still_has_a_bottom_anchor
# above).


def test_macro_pg_pin_only_has_its_exterior_handle(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    macro = view._boxes["top/u_ram"]
    pg_pin = next(i for i in view._supply_port_icons.values() if i.owner_box is macro and i.name == "VDD")

    assert pg_pin.fixed is True
    assert pg_pin.placement.edge == "top"
    assert set(pg_pin.handles) == {"top"}  # "bottom" (facing into the macro) is dropped
    _deselect_and_collect(tab)


def test_user_placed_port_keeps_both_handles_regardless_of_edge(qtbot):
    """Contrast case: a real, movable user-placed port (fixed=False) on a
    block that DOES have child hierarchy blocks -- even anchored at the
    same "top" edge a PG pin always uses -- keeps both handles. The
    leaf-block restriction above must not leak onto a non-leaf block's
    own ordinary ports (see test_supply_port_on_a_leaf_rtl_block_has_no_bottom_anchor
    for the leaf-RTL-block contrast case)."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    icon = view.add_supply_port(view._boxes["top"], "VDD", QColor("#ff0000"))

    assert icon.fixed is False
    assert icon.placement.edge == "top"
    assert set(icon.handles) == {"top", "bottom"}
    _deselect_and_collect(tab)


def test_handle_hit_shape_is_much_larger_than_the_visible_dot(qtbot):
    """The bug report: the drawn dot alone is essentially unclickable once
    a real diagram is zoomed to fit. shape() (what itemAt() hit-tests
    against) must accept a point well outside the visible radius."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    icon = view.add_supply_port(view._boxes["top/u_core"], "VDD", QColor("#ff0000"))
    handle = icon.handles["top"]
    center = handle.rect().center()

    just_outside_the_dot = QPointF(center.x() + 6, center.y())  # dot radius is 4 -- this misses the drawn circle
    well_within_padded_hit_area = QPointF(center.x() + 10, center.y())

    assert handle.shape().contains(just_outside_the_dot) is True
    assert handle.shape().contains(well_within_padded_hit_area) is True
    _deselect_and_collect(tab)


# --- direction-aware connector routing ---------------------------------------


def test_connector_route_respects_each_endpoints_own_exit_direction(qtbot):
    """One vertical-exit endpoint (top edge) + one horizontal-exit
    endpoint (right edge) -- the route's own first segment must leave
    `outer` vertically and its own last segment must arrive at `inner`
    horizontally, matching each icon's own edge."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    root_box = view._boxes["top"]
    child_box = view._boxes["top/u_core"]
    outer = view.add_supply_port(root_box, "VDD", QColor("#123456"))  # stays on top edge
    inner = view.add_supply_port(child_box, "VDD_CORE", QColor("#654321"))
    inner.setPos(child_box.rect().right(), child_box.rect().center().y())  # right edge

    view.try_connect_supply_ports(outer, inner)

    connector = view._supply_connectors[0]
    assert connector.waypoints[1].x() == connector.waypoints[0].x()  # first leg is vertical (top edge)
    assert connector.waypoints[-1].y() == connector.waypoints[-2].y()  # last leg is horizontal (right edge)
    assert _is_orthogonal(connector.waypoints)
    _deselect_and_collect(tab)


def test_connector_route_updates_direction_when_an_endpoint_moves_to_a_side_edge(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    root_box = view._boxes["top"]
    child_box = view._boxes["top/u_core"]
    outer = view.add_supply_port(root_box, "VDD", QColor("#123456"))
    inner = view.add_supply_port(child_box, "VDD_CORE", QColor("#654321"))
    view.try_connect_supply_ports(outer, inner)
    connector = view._supply_connectors[0]
    assert connector.waypoints[-1].x() == connector.waypoints[-2].x()  # both still on top edges -- vertical arrival

    inner.setPos(child_box.rect().right(), child_box.rect().center().y())  # drag onto the right edge

    # itemChange() -> update_route() picked up the new direction --
    # the route now arrives horizontally instead.
    assert connector.waypoints[-1].y() == connector.waypoints[-2].y()
    assert _is_orthogonal(connector.waypoints)
    _deselect_and_collect(tab)


def test_connector_line_is_thicker_than_a_typical_thin_diagram_line(qtbot):
    from frontend.src.power_intent_view.supply_port_overlay import _CONNECTOR_LINE_WIDTH

    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    outer = view.add_supply_port(view._boxes["top"], "VDD", QColor("#123456"))
    inner = view.add_supply_port(view._boxes["top/u_core"], "VDD_CORE", QColor("#654321"))
    view.try_connect_supply_ports(outer, inner)

    connector = view._supply_connectors[0]

    assert connector.pen().widthF() == _CONNECTOR_LINE_WIDTH
    assert _CONNECTOR_LINE_WIDTH > 2  # thicker than the old default
    _deselect_and_collect(tab)


def test_drag_preview_line_is_also_thicker(qtbot):
    from frontend.src.power_intent_view.supply_port_overlay import _CONNECTOR_LINE_WIDTH

    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    icon = view.add_supply_port(view._boxes["top/u_core"], "VDD", QColor("#ff0000"))
    handle = icon.handles["top"]

    view._connector_drag_origin = handle
    from PySide6.QtWidgets import QGraphicsPathItem

    from frontend.src.power_intent_view.supply_port_overlay import orthogonal_connector_path

    start = handle.scenePos()
    view._connector_drag_line = QGraphicsPathItem(orthogonal_connector_path(start, start))
    from PySide6.QtGui import QPen

    view._connector_drag_line.setPen(QPen(handle.owner.color, _CONNECTOR_LINE_WIDTH))

    assert view._connector_drag_line.pen().widthF() == _CONNECTOR_LINE_WIDTH
    _deselect_and_collect(tab)


# --- right-click Edit/Delete ---------------------------------------------------


def test_context_menu_offers_edit_and_delete_for_a_user_placed_port(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    icon = view.add_supply_port(view._boxes["top/u_core"], "VDD", QColor("#ff0000"))

    menu = QMenu()
    actions = view._supply_port_context_menu_actions(menu, icon)

    assert [a.text() for a, _cb in actions] == ["Edit", "Delete", "Add Power Switch", "Edit Power Switch", "Delete Power Switch"]
    _deselect_and_collect(tab)


def test_supply_port_icon_at_finds_the_icon_via_its_label(qtbot):
    """The routing check in _on_context_menu() must resolve to the icon
    even if the right-click lands on its label, a separate child item."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.resize(800, 600)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    icon = view.add_supply_port(view._boxes["top/u_core"], "VDD", QColor("#ff0000"))
    label_scene_pos = icon.label.mapToScene(icon.label.boundingRect().center())
    viewport_point = view.mapFromScene(label_scene_pos)

    found = view._supply_port_icon_at(viewport_point)

    assert found is icon
    _deselect_and_collect(tab)


def test_edit_supply_port_dialog_preloads_its_existing_power_states(qtbot):
    """Bug fix report investigated -- confirmed, via a live diagnostic
    against the real dialog-driven Add flow, that _prompt_edit_supply_port()
    already threads `initial_states=list(icon.placement.states)` into
    AddPowerSupplyDialog correctly, and SupplyPortStatesWidget's own
    `.states` setter correctly populates the table from it -- this test
    locks that in as a permanent guarantee rather than leaving it
    unverified. (The original report's exact repro did not reproduce
    once isolated from an unrelated same-named macro PG pin collision in
    the reproduction script itself, not a defect in this code path.)"""
    from frontend.src.power_intent_view.supply_port_overlay import SupplyPortState

    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    states = [
        SupplyPortState("FULL_ON", "1.0V"),
        SupplyPortState("FULL_ON", "0.6V"),
    ]
    icon = view.add_supply_port(view._boxes["top"], "VDD", QColor("#ff0000"), states=states)

    captured = {}
    monkeypatch_target = AddPowerSupplyDialog.exec

    def fake_exec(self):
        captured["dialog"] = self
        return QDialog.DialogCode.Rejected

    AddPowerSupplyDialog.exec = fake_exec
    try:
        view._prompt_edit_supply_port(icon)
    finally:
        AddPowerSupplyDialog.exec = monkeypatch_target

    dialog = captured["dialog"]
    assert dialog.states_widget.table.rowCount() == 2
    assert dialog.states == states
    _deselect_and_collect(tab)


def test_pg_pin_context_menu_is_not_offered(qtbot):
    """"not applicable to Macro power supplies" -- explicit request.
    _on_context_menu()'s own gate (`icon is not None and not icon.fixed`)
    is what enforces this; confirmed here at the icon.fixed level since
    menu.exec() itself can't be driven headlessly."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    pg_pin = next(i for i in view._supply_port_icons.values() if i.owner_box.path == "top/u_ram" and i.name == "VDD")

    assert pg_pin.fixed is True  # -- the exact condition _on_context_menu() checks
    _deselect_and_collect(tab)


def test_edit_supply_port_renames_and_recolors(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    icon = view.add_supply_port(view._boxes["top/u_core"], "VDD", QColor("#ff0000"))

    view.edit_supply_port(icon, "VDD_NEW", QColor("#00ff00"))

    assert icon.name == "VDD_NEW"
    assert icon.color == QColor("#00ff00")
    assert icon.label.text() == "VDD_NEW"
    _deselect_and_collect(tab)


def test_delete_supply_port_removes_it_and_its_connectors(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    outer = view.add_supply_port(view._boxes["top"], "VDD", QColor("#123456"))
    inner = view.add_supply_port(view._boxes["top/u_core"], "VDD_CORE", QColor("#654321"))
    view.try_connect_supply_ports(outer, inner)
    assert len(view._supply_connectors) == 1

    view.delete_supply_port(inner)

    assert inner.key not in view._supply_port_icons
    assert inner.key not in view._shared_state.supply_port_placements
    assert view._supply_connectors == []
    assert view._shared_state.supply_connector_placements == []
    _deselect_and_collect(tab)


def test_deleting_a_supply_port_resets_its_pg_pin_partner_to_default_grey(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    supply = view.add_supply_port(view._boxes["top"], "VDD_1V0", QColor("#00aaff"))
    pg_pin = next(i for i in view._supply_port_icons.values() if i.owner_box.path == "top/u_ram" and i.name == "VDD")
    view.try_connect_supply_ports(supply, pg_pin)
    assert pg_pin.color == QColor("#00aaff")

    view.delete_supply_port(supply)

    assert pg_pin.color == QColor("#9e9e9e")
    _deselect_and_collect(tab)


def test_delete_does_not_disturb_an_unrelated_supply_port(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    keep = view.add_supply_port(view._boxes["top/u_dbg"], "KEEP", QColor("#111111"))
    doomed = view.add_supply_port(view._boxes["top/u_core"], "DOOMED", QColor("#222222"))

    view.delete_supply_port(doomed)

    assert keep.key in view._supply_port_icons
    assert keep.key in view._shared_state.supply_port_placements
    _deselect_and_collect(tab)


# --- CHANGE 1 (explicit request): name uniqueness removed entirely -----------
# The former same-hierarchy AND cross-hierarchy global uniqueness rules
# (_check_unique_name()/_check_unique_name_globally()) are both deleted --
# two Supply Ports/Internal Supplies may now share a name freely, anywhere
# in the design, with no error and no forced/implicit relationship.


def test_can_add_a_supply_port_with_a_name_already_used_on_the_same_box(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top/u_core"]
    first = view.add_supply_port(box, "VDD", QColor("#ff0000"))

    second = view.add_supply_port(box, "VDD", QColor("#00ff00"))

    assert first.key != second.key
    assert len([p for p in view._shared_state.supply_port_placements.values() if p.owner_path == box.path]) == 2
    _deselect_and_collect(tab)


def test_same_name_is_allowed_on_an_unrelated_different_box(qtbot):
    """CHANGE 1 (supersedes the earlier "new global rule rejects this"
    test): "top/u_core" and "top/u_dbg" share no domain and no
    connection at all -- the name collision is now completely
    unrestricted, no error at all."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    first = view.add_supply_port(view._boxes["top/u_core"], "VDD", QColor("#ff0000"))

    second = view.add_supply_port(view._boxes["top/u_dbg"], "VDD", QColor("#00ff00"))

    assert first.key != second.key
    assert len(first.connectors) == 0
    assert len(second.connectors) == 0
    _deselect_and_collect(tab)


def test_cannot_add_a_supply_port_colliding_with_a_pg_pins_name_on_the_same_macro(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    macro_box = view._boxes["top/u_ram"]  # already auto-has a PG pin named "VDD"

    try:
        view.add_supply_port(macro_box, "VDD", QColor("#00ff00"))
        assert False, "expected ValueError"
    except ValueError:
        pass
    _deselect_and_collect(tab)


def test_edit_allows_renaming_to_a_name_already_used_on_the_same_box(qtbot):
    """CHANGE 1 (supersedes the earlier "edit rejects this" test)."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top/u_core"]
    view.add_supply_port(box, "VDD", QColor("#ff0000"))
    other = view.add_supply_port(box, "VSS", QColor("#00ff00"))

    view.edit_supply_port(other, "VDD", QColor("#0000ff"))

    assert other.name == "VDD"
    assert other.color == QColor("#0000ff")
    _deselect_and_collect(tab)


def test_edit_allows_renaming_to_its_own_current_name(qtbot):
    """A no-op rename (same name, different colour) must not trip over
    the port's own existing placement in the uniqueness check."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    icon = view.add_supply_port(view._boxes["top/u_core"], "VDD", QColor("#ff0000"))

    view.edit_supply_port(icon, "VDD", QColor("#0000ff"))  # must not raise

    assert icon.name == "VDD"
    assert icon.color == QColor("#0000ff")
    _deselect_and_collect(tab)


# --- real mouse-event-driven connector drag -----------------------------------
#
# Every test above drives try_connect_supply_ports()/add_supply_port() etc.
# directly, bypassing mousePressEvent/mouseMoveEvent/mouseReleaseEvent
# entirely -- which is exactly how a real, reported bug slipped through
# undetected: ConnectorHandleItem.scenePos() silently returned the *icon's*
# position, not the handle's own drawn location (it's never moved via
# setPos(), only its rect() is offset -- see scene_center()'s own
# docstring), and the drag-preview line had no Z value at all, defaulting
# to 0 -- below every InstanceBoxItem (Z=1), rendering it invisibly behind
# a box's own white fill for virtually the whole drag. Once those were
# fixed, a *third*, deeper bug surfaced: itemAt() picks the topmost item by
# a stacking-order rule where "a child is always drawn on top of its own
# parent, Z only ranks true siblings" -- since the drag line is a bare
# top-level scene item but a handle is nested several levels deep (icon ->
# box -> ... -> root box), the comparison Qt actually makes is [drag line]
# vs. [the handle's root-box ancestor], not [drag line] vs. [handle] --
# the line's Z beat the box's own low Z at that top level, hiding every
# handle underneath the line regardless of the handle's own much higher
# *local* Z. These tests drive the real QGraphicsView mouse-event pipeline
# with real QTest events end-to-end specifically so this class of bug
# (invisible/unclickable purely because of real widget/scene mechanics,
# not any unit-level logic error) can't silently reappear.


def _viewport_pos(view, item) -> "QPointF":
    return view.mapFromScene(item.scene_center() if hasattr(item, "scene_center") else item.scenePos())


def test_real_mouse_drag_from_handle_to_handle_completes_a_connection(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.resize(900, 700)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    outer = view.add_supply_port(view._boxes["top"], "VDD", QColor("#ff0000"))
    inner = view.add_supply_port(view._boxes["top/u_core"], "VDD_CORE", QColor("#00ff00"))
    view.set_mode(DiagramMode.SUPPLY_NET)  # explicit request: the handle-drag gesture is now gated on this mode

    start = _viewport_pos(view, outer.handles["bottom"])
    end = _viewport_pos(view, inner.handles["top"])

    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
    assert view._connector_drag_line is not None  # the line actually started
    QTest.mouseMove(view.viewport(), end)
    assert view._connector_handle_at(end) is not None  # the target is findable mid-drag, not hidden by the line
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, end)

    assert len(view._supply_connectors) == 1
    assert view._connector_drag_line is None  # preview cleaned up
    _deselect_and_collect(tab)


def test_real_mouse_drag_onto_a_pg_pin_completes_and_recolors_it(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.resize(900, 700)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    outer = view.add_supply_port(view._boxes["top"], "VDD", QColor("#ff0000"))
    pg_pin = next(i for i in view._supply_port_icons.values() if i.owner_box.path == "top/u_ram" and i.name == "VDD")
    view.set_mode(DiagramMode.SUPPLY_NET)  # explicit request: the handle-drag gesture is now gated on this mode

    start = _viewport_pos(view, outer.handles["bottom"])
    end = _viewport_pos(view, pg_pin.handles["top"])

    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
    QTest.mouseMove(view.viewport(), end)
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, end)

    assert len(view._supply_connectors) == 1
    assert pg_pin.color == QColor("#ff0000")
    _deselect_and_collect(tab)


def test_drag_preview_line_is_not_hidden_behind_a_box(qtbot):
    """The Z-value bug specifically: the preview line must render above a
    box's own fill (Z_SUPPLY_PORT_CONNECTOR, not the unset default of 0,
    which sits below InstanceBoxItem's Z=1)."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.resize(900, 700)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    icon = view.add_supply_port(view._boxes["top/u_core"], "VDD", QColor("#ff0000"))
    view.set_mode(DiagramMode.SUPPLY_NET)  # explicit request: the handle-drag gesture is now gated on this mode
    start = _viewport_pos(view, icon.handles["top"])

    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)

    from frontend.src.power_intent_view.supply_port_overlay import Z_SUPPLY_PORT_CONNECTOR

    assert view._connector_drag_line.zValue() == Z_SUPPLY_PORT_CONNECTOR
    _deselect_and_collect(tab)


def test_connector_handle_scene_center_differs_from_the_buggy_scenepos(qtbot):
    """Regression lock for the scenePos() bug itself: a handle's own
    scenePos() equals its *icon's* scenePos() (it's never moved via
    setPos()) -- scene_center() must not make that same mistake."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    icon = view.add_supply_port(view._boxes["top/u_core"], "VDD", QColor("#ff0000"))
    handle = icon.handles["top"]

    assert handle.scenePos() == icon.scenePos()  # the (surprising) fact scene_center() has to work around
    assert handle.scene_center() != icon.scenePos()  # the actual, correct, drawn location
    _deselect_and_collect(tab)


def test_real_mouse_drag_that_misses_any_handle_on_release_creates_no_connector(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.resize(900, 700)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    outer = view.add_supply_port(view._boxes["top"], "VDD", QColor("#ff0000"))
    view.add_supply_port(view._boxes["top/u_core"], "VDD_CORE", QColor("#00ff00"))
    view.set_mode(DiagramMode.SUPPLY_NET)  # explicit request: the handle-drag gesture is now gated on this mode

    start = _viewport_pos(view, outer.handles["bottom"])
    empty_spot = view.viewport().rect().bottomRight()

    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
    QTest.mouseMove(view.viewport(), empty_spot)
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, empty_spot)

    assert view._supply_connectors == []
    assert view._connector_drag_line is None
    _deselect_and_collect(tab)


# --- obstacle-avoiding routing -------------------------------------------------


def test_middle_segment_detours_around_an_obstacle_in_its_way(qtbot):
    from PySide6.QtCore import QPointF, QRectF

    from frontend.src.power_intent_view.supply_port_overlay import _segment_intersects_rect, compute_connector_waypoints

    p1, p2 = QPointF(0, 0), QPointF(200, 0)
    obstacle = QRectF(50, -20, 100, 40)  # squarely across the naive mid_y=0 horizontal segment

    waypoints = compute_connector_waypoints(p1, p2, "vertical", "vertical", [obstacle])

    assert waypoints[0] == p1 and waypoints[-1] == p2  # endpoints never move
    for i in range(len(waypoints) - 1):
        assert _segment_intersects_rect(waypoints[i], waypoints[i + 1], obstacle) is False


def test_l_bend_synthesizes_a_middle_segment_to_route_an_obstacle_around(qtbot):
    """A 2-segment L-bend has no middle segment at all -- confirms one
    gets synthesized specifically so there's something to detour with,
    for a case not touching either endpoint's own approach."""
    from PySide6.QtCore import QPointF, QRectF

    from frontend.src.power_intent_view.supply_port_overlay import _segment_intersects_rect, compute_connector_waypoints

    p1, p2 = QPointF(0, 0), QPointF(200, 100)
    obstacle = QRectF(50, 30, 100, 20)

    waypoints = compute_connector_waypoints(p1, p2, "vertical", "horizontal", [obstacle])

    assert len(waypoints) > 3  # more than the plain 2-segment L-bend
    for i in range(len(waypoints) - 1):
        assert _segment_intersects_rect(waypoints[i], waypoints[i + 1], obstacle) is False


def test_no_obstacles_produces_the_same_route_as_before(qtbot):
    from PySide6.QtCore import QPointF

    from frontend.src.power_intent_view.supply_port_overlay import compute_connector_waypoints

    p1, p2 = QPointF(0, 0), QPointF(200, 0)
    assert compute_connector_waypoints(p1, p2, "vertical", "vertical", []) == [p1, QPointF(0, 0), QPointF(200, 0), p2]


def test_real_macro_box_is_treated_as_an_obstacle(qtbot):
    """Places two supply ports so a straight route between them would
    cross straight through the real top/u_ram macro box's own scene
    rect, then confirms the actual committed connector -- not just the
    pure geometry function -- avoids it."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    macro_rect = view._boxes["top/u_ram"].sceneBoundingRect()

    outer = view.add_supply_port(view._boxes["top"], "VDD", QColor("#ff0000"))
    inner = view.add_supply_port(view._boxes["top/u_core"], "VDD_CORE", QColor("#00ff00"))
    # Force both icons onto the macro's own horizontal span, well above
    # and below it, so the naive straight route would pass right through it.
    outer.setPos(macro_rect.center().x(), view._boxes["top"].rect().top())
    inner.setPos(macro_rect.center().x(), view._boxes["top/u_core"].rect().top())

    created = view.try_connect_supply_ports(outer, inner)

    assert created is True
    connector = view._supply_connectors[0]
    from frontend.src.power_intent_view.supply_port_overlay import _segment_intersects_rect

    for i in range(len(connector.waypoints) - 1):
        assert _segment_intersects_rect(connector.waypoints[i], connector.waypoints[i + 1], macro_rect) is False
    _deselect_and_collect(tab)


def test_endpoint_move_keeps_the_route_starting_and_ending_at_the_real_anchors(qtbot):
    """Moving an endpoint icon must always leave the connector starting
    and ending exactly at the real (new) anchor positions -- update_route()
    recomputes the whole route fresh on every real (non-live-drag) move."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    outer = view.add_supply_port(view._boxes["top"], "VDD", QColor("#123456"))
    inner = view.add_supply_port(view._boxes["top/u_core"], "VDD_CORE", QColor("#654321"))
    view.try_connect_supply_ports(outer, inner)
    connector = view._supply_connectors[0]

    box = view._boxes["top"]
    outer.setPos(box.rect().left(), box.rect().center().y())  # deliberately move the endpoint

    assert connector.waypoints[0] == _anchor(outer)
    assert connector.waypoints[-1] == _anchor(inner)
    assert _is_orthogonal(connector.waypoints)
    _deselect_and_collect(tab)


# --- right-click Delete on a connector ("power net") --------------------------


def test_connector_at_finds_the_whole_line_including_the_anchor_stubs(qtbot):
    """Unlike segment_at() (draggable middle segments only), the
    right-click "Delete" target is the whole line -- explicit request."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    outer = view.add_supply_port(view._boxes["top"], "VDD", QColor("#123456"))
    inner = view.add_supply_port(view._boxes["top/u_core"], "VDD_CORE", QColor("#654321"))
    view.try_connect_supply_ports(outer, inner)
    connector = view._supply_connectors[0]
    first_segment_midpoint = (connector.waypoints[0] + connector.waypoints[1]) / 2

    assert view._connector_at(first_segment_midpoint) is connector
    far_away = QPointF(connector.waypoints[0].x() + 5000, connector.waypoints[0].y() + 5000)
    assert view._connector_at(far_away) is None
    _deselect_and_collect(tab)


def test_delete_connector_removes_it(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    outer = view.add_supply_port(view._boxes["top"], "VDD", QColor("#123456"))
    inner = view.add_supply_port(view._boxes["top/u_core"], "VDD_CORE", QColor("#654321"))
    view.try_connect_supply_ports(outer, inner)
    connector = view._supply_connectors[0]

    view.delete_connector(connector)

    assert view._supply_connectors == []
    assert view._shared_state.supply_connector_placements == []
    _deselect_and_collect(tab)


def test_delete_connector_does_not_delete_either_supply_port(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    outer = view.add_supply_port(view._boxes["top"], "VDD", QColor("#123456"))
    inner = view.add_supply_port(view._boxes["top/u_core"], "VDD_CORE", QColor("#654321"))
    view.try_connect_supply_ports(outer, inner)
    connector = view._supply_connectors[0]

    view.delete_connector(connector)

    assert outer.key in view._shared_state.supply_port_placements
    assert inner.key in view._shared_state.supply_port_placements
    _deselect_and_collect(tab)


def test_deleting_a_pg_pin_connector_resets_the_pin_to_default_grey(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    supply = view.add_supply_port(view._boxes["top"], "VDD_1V0", QColor("#00aaff"))
    pg_pin = next(i for i in view._supply_port_icons.values() if i.owner_box.path == "top/u_ram" and i.name == "VDD")
    view.try_connect_supply_ports(supply, pg_pin)
    assert pg_pin.color == QColor("#00aaff")
    connector = view._supply_connectors[0]

    view.delete_connector(connector)

    assert pg_pin.color == QColor("#9e9e9e")
    _deselect_and_collect(tab)


# --- Ctrl+Z/Ctrl+Y undo/redo for Power Intent edits ---------------------------
#
# Every mutation shares the same real bug this section is here to guard
# against: QUndoStack.push() calls the just-pushed command's own redo()
# synchronously, on the assumption the action hasn't happened yet -- but
# here the mutation (add_supply_port() etc.) already happened directly,
# and already has a real, live SupplyPortIconItem to return. An earlier
# version's redo() unconditionally rebuilt the scene on that first,
# redundant call, destroying the just-created item out from under the
# caller before it was ever used. Found only by actually calling
# add_supply_port() and checking the *returned* object stayed alive, not
# by asserting on _shared_state alone (which looked correct either way).


def test_undo_redo_use_the_same_stack_as_hide_unhide(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())

    assert tab.undo_shortcut.parent() is tab
    # Ctrl+Z/Ctrl+Y are already wired to tree.undo_stack for Hide/Unhide;
    # explicit request: Power Intent edits must use the SAME shortcuts,
    # not a second independent stack.
    assert tab.diagram._view._undo_stack is tab.tree.undo_stack


def test_add_supply_port_returns_a_still_alive_icon_after_the_undo_push(qtbot):
    """The actual regression: QUndoStack.push()'s own synchronous redo()
    call must not destroy the icon add_supply_port() is about to
    return."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view

    icon = view.add_supply_port(view._boxes["top/u_core"], "VDD", QColor("#ff0000"))

    assert icon in view._supply_port_icons.values()  # would raise/assert False if the C++ object were deleted
    assert icon.name == "VDD"
    _deselect_and_collect(tab)


def test_ctrl_z_undoes_add_power_supply_and_ctrl_y_redoes_it(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view.add_supply_port(view._boxes["top/u_core"], "VDD", QColor("#ff0000"))
    assert len(view._shared_state.supply_port_placements) == 1

    tab.undo_shortcut.activated.emit()
    assert len(view._shared_state.supply_port_placements) == 0

    tab.redo_shortcut.activated.emit()
    assert len(view._shared_state.supply_port_placements) == 1
    _deselect_and_collect(tab)


def test_ctrl_z_undoes_edit_power_supply(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    icon = view.add_supply_port(view._boxes["top/u_core"], "VDD", QColor("#ff0000"))
    view.edit_supply_port(icon, "VDD_NEW", QColor("#00ff00"))
    assert icon.name == "VDD_NEW"

    tab.undo_shortcut.activated.emit()

    restored = next(iter(view._shared_state.supply_port_placements.values()))
    assert restored.name == "VDD"
    assert restored.color == QColor("#ff0000")
    _deselect_and_collect(tab)


def test_ctrl_z_undoes_delete_power_supply(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    icon = view.add_supply_port(view._boxes["top/u_core"], "VDD", QColor("#ff0000"))
    view.delete_supply_port(icon)
    assert len(view._shared_state.supply_port_placements) == 0

    tab.undo_shortcut.activated.emit()

    assert len(view._shared_state.supply_port_placements) == 1
    _deselect_and_collect(tab)


def test_ctrl_z_undoes_connect_supply_ports(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    outer = view.add_supply_port(view._boxes["top"], "VDD", QColor("#123456"))
    inner = view.add_supply_port(view._boxes["top/u_core"], "VDD_CORE", QColor("#654321"))
    view.try_connect_supply_ports(outer, inner)
    assert len(view._shared_state.supply_connector_placements) == 1

    tab.undo_shortcut.activated.emit()
    assert len(view._shared_state.supply_connector_placements) == 0

    tab.redo_shortcut.activated.emit()
    assert len(view._shared_state.supply_connector_placements) == 1
    _deselect_and_collect(tab)


def test_ctrl_z_undoes_delete_connector(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    outer = view.add_supply_port(view._boxes["top"], "VDD", QColor("#123456"))
    inner = view.add_supply_port(view._boxes["top/u_core"], "VDD_CORE", QColor("#654321"))
    view.try_connect_supply_ports(outer, inner)
    connector = view._supply_connectors[0]
    view.delete_connector(connector)
    assert len(view._shared_state.supply_connector_placements) == 0

    tab.undo_shortcut.activated.emit()

    assert len(view._shared_state.supply_connector_placements) == 1
    _deselect_and_collect(tab)


def test_a_plain_click_with_no_real_drag_does_not_push_an_empty_undo_step(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.resize(900, 700)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    icon = view.add_supply_port(view._boxes["top/u_core"], "VDD", QColor("#ff0000"))
    count_before = tab.tree.undo_stack.count()
    pos = view.mapFromScene(icon.scenePos())

    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, pos)
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, pos)

    assert tab.tree.undo_stack.count() == count_before  # no no-op undo step pushed
    _deselect_and_collect(tab)


def test_real_mouse_move_of_a_supply_port_is_undoable(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.resize(900, 700)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top/u_core"]
    icon = view.add_supply_port(box, "VDD", QColor("#ff0000"))
    start_viewport = view.mapFromScene(icon.scenePos())
    target_scene = box.mapToScene(box.rect().right(), box.rect().center().y())
    target_viewport = view.mapFromScene(target_scene)

    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start_viewport)
    QTest.mouseMove(view.viewport(), target_viewport)
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, target_viewport)
    moved = next(iter(view._shared_state.supply_port_placements.values()))
    assert moved.edge == "right"

    tab.undo_shortcut.activated.emit()

    reverted = next(iter(view._shared_state.supply_port_placements.values()))
    assert reverted.edge == "top"
    _deselect_and_collect(tab)


def test_a_fresh_design_load_clears_power_intent_undo_history(qtbot):
    """The same tree.undo_stack.clear() Hide/Unhide's own load() already
    triggers must also wipe Power Intent edit history -- a fresh design
    load shouldn't let Ctrl+Z reach into a prior design's edits."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view.add_supply_port(view._boxes["top/u_core"], "VDD", QColor("#ff0000"))
    assert tab.tree.undo_stack.count() > 0

    tab.set_rtl(build_mock_design())  # fresh load

    assert tab.tree.undo_stack.count() == 0
    _deselect_and_collect(tab)


def test_undo_still_works_after_closing_the_sub_hierarchy_tab_the_edit_was_made_in(qtbot):
    """The exact scenario _SupplyPortStateCommand's own refresh callback
    design exists for: editing inside a closable sub-hierarchy tab, then
    closing that tab, then undoing from elsewhere -- must not crash from
    holding a reference to an already-destroyed view."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    tab.diagram.open_path("top/u_core")
    sub_view = tab.diagram._tabs.current_view
    sub_view.add_supply_port(sub_view._boxes["top/u_core"], "VDD_SUB", QColor("#ff00ff"))
    assert len(tab.diagram._shared_supply_state.supply_port_placements) == 1

    tab.diagram._tabs.setCurrentIndex(0)
    tab.diagram._tabs.tabCloseRequested.emit(1)  # close the tab the edit was made in
    assert tab.diagram._tabs.count() == 1

    tab.undo_shortcut.activated.emit()  # must not raise

    assert len(tab.diagram._shared_supply_state.supply_port_placements) == 0
    root_view = tab.diagram._tabs.current_view
    if root_view.scene() is not None:
        root_view.scene().clearSelection()
    gc.collect()


# --- macro PG pin voltage label -----------------------------------------------


def test_pg_pin_with_a_known_voltage_shows_it_on_a_second_label_line(qtbot):
    """top/u_ram's VDD (PortDirection.PG) carries voltage="0.80V" in the
    mock fixture -- explicit request: "VDD (0.80V)", voltage on its own
    line."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    pg_pin = next(i for i in view._supply_port_icons.values() if i.owner_box.path == "top/u_ram" and i.name == "VDD")

    assert pg_pin.placement.voltage == "0.80V"
    assert pg_pin.label.text() == "VDD\n(0.80V)"
    _deselect_and_collect(tab)


def test_pg_pin_with_no_known_voltage_shows_just_its_name(qtbot):
    """top/u_ram's VSS is PG_INTERNAL, not PG -- never even surfaced as
    an icon at all (deferred, see supply_port_overlay.py's own
    docstring), so there's no risk of it showing a stray voltage line.
    This confirms the *general* no-voltage fallback using a PG pin
    that's actually surfaced but has no voltage data by construction."""
    from frontend.src.power_intent_view.supply_port_overlay import _label_text
    from frontend.src.power_intent_view.supply_port_overlay import SupplyPortPlacement
    from PySide6.QtGui import QColor as _QColor

    placement = SupplyPortPlacement(id="pg:top/u_x:VDDIO", owner_path="top/u_x", name="VDDIO", color=_QColor("#9e9e9e"), fixed=True, voltage=None)

    assert _label_text(placement) == "VDDIO"


def test_user_placed_supply_port_never_gets_a_voltage_line(qtbot):
    """No dialog field for it -- explicit request scope was macro PG
    pins specifically, not user-placed ports."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view

    icon = view.add_supply_port(view._boxes["top/u_core"], "VDD", QColor("#ff0000"))

    assert icon.placement.voltage is None
    assert icon.label.text() == "VDD"
    _deselect_and_collect(tab)


def test_pg_pin_voltage_survives_a_rebuild(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view

    tab.tree._toggle_hidden("top/u_dbg")  # unrelated rebuild

    pg_pin = next(i for i in view._supply_port_icons.values() if i.owner_box.path == "top/u_ram" and i.name == "VDD")
    assert pg_pin.label.text() == "VDD\n(0.80V)"
    _deselect_and_collect(tab)


# --- item 8: icon quality -----------------------------------------------------


def test_supply_port_svg_renderer_is_shared_not_per_instance(qtbot):
    """Explicit request: a single shared QSvgRenderer per icon TYPE, not
    one per icon instance -- lru_cache(maxsize=1) makes every call to
    _supply_port_svg_renderer() return the exact same object, whether
    zero, one, or many SupplyPortIconItems exist."""
    from frontend.src.power_intent_view.supply_port_overlay import _supply_port_svg_renderer

    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view.add_supply_port(view._boxes["top"], "VDD_A", QColor("#ff0000"))
    view.add_supply_port(view._boxes["top/u_core"], "VDD_B", QColor("#00ff00"))

    assert _supply_port_svg_renderer() is _supply_port_svg_renderer()
    _deselect_and_collect(tab)


def test_supply_port_icon_bounding_rect_is_centered_and_the_right_size(qtbot):
    """Explicit request: boundingRect() must now be implemented
    explicitly (a plain QGraphicsItem has no pixmap to derive it from
    implicitly anymore) -- without it, hit-testing/selection on the
    icon would break."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    icon = view.add_supply_port(view._boxes["top"], "VDD", QColor("#ff0000"))

    rect = icon.boundingRect()

    assert rect.width() == _ICON_SIZE
    assert rect.height() == _ICON_SIZE
    assert rect.center() == QPointF(0, 0)
    _deselect_and_collect(tab)


def test_diagram_view_enables_antialiasing_and_smooth_pixmap_transform(qtbot):
    """Explicit request: the complementary fix to pixelation caused by
    THIS view's own zoom transform scaling an already-rendered pixmap up
    further -- without SmoothPixmapTransform, Qt uses fast/blocky
    nearest-neighbor scaling for that."""
    from PySide6.QtGui import QPainter

    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    view = tab.diagram._view

    hints = view.renderHints()

    assert hints & QPainter.RenderHint.Antialiasing
    assert hints & QPainter.RenderHint.SmoothPixmapTransform


def test_supply_port_icon_renders_without_crashing_at_a_high_zoom_level(qtbot):
    """A live vector render (see SupplyPortIconItem.paint()) has no
    fixed-resolution ceiling to run out of the way a cached pixmap did
    -- exercised here by actually zooming the view in hard and
    confirming the icon still paints (no crash, still not empty)."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.resize(900, 700)
    tab.show()
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    icon = view.add_supply_port(view._boxes["top"], "VDD", QColor("#ff0000"))
    view.fit_view()

    view.scale(12, 12)
    QApplication.processEvents()

    assert not icon.boundingRect().isEmpty()
    _deselect_and_collect(tab)


# --- bug 2: click-selection highlight overrides the port's own colour -------


def test_supply_port_icon_renders_highlight_color_when_click_highlighted(qtbot):
    from frontend.src.power_intent_view.supply_port_overlay import HIGHLIGHT_COLOR

    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    icon = view.add_supply_port(view._boxes["top"], "VDD", QColor(255, 0, 0))
    before_pixel = _first_opaque_pixel_color(_render_icon_to_image(icon))
    assert (before_pixel.red(), before_pixel.green(), before_pixel.blue()) == (255, 0, 0)

    icon.set_click_highlighted(True)

    highlighted_pixel = _first_opaque_pixel_color(_render_icon_to_image(icon))
    assert (highlighted_pixel.red(), highlighted_pixel.green(), highlighted_pixel.blue()) == (
        HIGHLIGHT_COLOR.red(),
        HIGHLIGHT_COLOR.green(),
        HIGHLIGHT_COLOR.blue(),
    )

    icon.set_click_highlighted(False)

    after_pixel = _first_opaque_pixel_color(_render_icon_to_image(icon))
    assert (after_pixel.red(), after_pixel.green(), after_pixel.blue()) == (255, 0, 0)  # reverts to the port's real colour
    _deselect_and_collect(tab)


def test_clicking_a_supply_port_icon_flags_it_click_highlighted(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.resize(900, 700)
    tab.show()
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view.set_mode(DiagramMode.SELECT)
    icon = view.add_supply_port(view._boxes["top"], "VDD", QColor("#2196f3"))

    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, view.mapFromScene(icon.scenePos()))

    assert icon._click_highlighted is True
    _deselect_and_collect(tab)


# --- name validation: no spaces allowed (Supply Port) -------------------------


def test_add_supply_port_rejects_a_name_with_an_internal_space(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top/u_core"]

    with pytest.raises(ValueError, match="space"):
        view.add_supply_port(box, "VDD BAD", QColor("#ff0000"))

    assert view._shared_state.supply_port_placements == {}
    _deselect_and_collect(tab)


def test_add_supply_port_accepts_a_name_with_only_leading_trailing_whitespace(qtbot):
    """Explicit request: surrounding whitespace is trimmed automatically
    before the space-check runs. AddPowerSupplyDialog.name already
    strips via its own `.text().strip()`, so this exercises
    add_supply_port() with the already-trimmed value directly, the same
    shape the dialog would hand it."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top/u_core"]

    icon = view.add_supply_port(box, "VDD_OK", QColor("#ff0000"))

    assert icon.name == "VDD_OK"
    _deselect_and_collect(tab)


def test_add_supply_port_accepts_a_valid_underscore_separated_name(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top/u_core"]

    icon = view.add_supply_port(box, "VSS_SS", QColor("#ff0000"))

    assert icon.name == "VSS_SS"
    _deselect_and_collect(tab)


def test_edit_supply_port_rejects_a_name_with_an_internal_space(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    icon = view.add_supply_port(view._boxes["top/u_core"], "VDD", QColor("#ff0000"))

    with pytest.raises(ValueError, match="space"):
        view.edit_supply_port(icon, "VDD BAD", QColor("#ff0000"))

    assert icon.name == "VDD"  # unchanged
    _deselect_and_collect(tab)


def test_add_power_supply_dialog_blocks_ok_button_with_a_space_only_visually_via_the_error_dialog(qtbot, monkeypatch):
    """End-to-end: the right-click "Add Power Supply" flow (dialog ->
    add_supply_port()) surfaces the space-rejection via the same
    QMessageBox.warning()-then-reopen loop the name-uniqueness check
    already uses (_prompt_add_power_supply()'s own retry loop) -- this
    confirms the ValueError raised by add_supply_port() actually reaches
    the user, not just that the core method rejects it in isolation."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top"]
    warnings = []
    monkeypatch.setattr(
        "frontend.src.power_intent_view.power_intent_diagram_view.message_box.warning",
        lambda parent, title, text: warnings.append((title, text)),
    )
    calls = iter([("VDD BAD", QColor("#ff0000")), ("VDD_OK", QColor("#ff0000"))])

    def fake_exec(self):
        name, color = next(calls)
        self.name_edit.setText(name)
        self.color = color
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(AddPowerSupplyDialog, "exec", fake_exec)

    view._prompt_add_power_supply(box)

    assert len(warnings) == 1
    assert warnings[0][1] == "Name cannot contain spaces."
    placed = [icon for icon in view._supply_port_icons.values() if icon.owner_box is box]
    assert len(placed) == 1
    assert placed[0].name == "VDD_OK"  # the loop reopened the dialog and the second attempt succeeded
    _deselect_and_collect(tab)


# --- Manual per-segment drag (restored, pre-existing capability -----------
# ---   confirmed distinct from the retracted "Split Segment" feature) -----


def _connect_two_ports(view):
    outer = view.add_supply_port(view._boxes["top"], "VDD", QColor("#123456"))
    inner = view.add_supply_port(view._boxes["top/u_core"], "VDD_CORE", QColor("#654321"))
    view.try_connect_supply_ports(outer, inner)
    return outer, inner, view._supply_connectors[0]


def _connect_two_ports_with_multiple_bends(view):
    """Same as _connect_two_ports() but anchored on perpendicular,
    "wrong-facing" edges (left-exit into a bottom-entry) specifically
    to guarantee 2 draggable middle segments, one of each orientation
    -- needed by tests exercising segment-drag mechanics for BOTH
    orientations. A same-edge pair (e.g. top-to-top, matching how
    Power-Domain-composed Supply Ports are always placed -- edge="top"
    unconditionally, see _add_missing_supply_ports()) settles on a
    single-bend (3-segment, exactly 1 draggable middle segment, always
    HORIZONTAL for a top-to-top pair) path -- not enough to test a
    genuinely vertical drag against."""
    outer = view.add_supply_port(view._boxes["top"], "VDD", QColor("#123456"), edge="left", t=0.3)
    inner = view.add_supply_port(view._boxes["top/u_core"], "VDD_CORE", QColor("#654321"), edge="bottom", t=0.7)
    view.try_connect_supply_ports(outer, inner)
    return outer, inner, view._supply_connectors[0]


def test_dragging_the_horizontal_segment_of_a_same_edge_pair_right_moves_it_and_keeps_endpoints_fixed(qtbot):
    """Explicit regression request, EXACT repro shape: a Power-Domain-
    composed pair (both edge="top", matching create_power_domain()'s
    own _add_missing_supply_ports() placement -- see that method's own
    docstring) settles on exactly ONE draggable middle segment, and
    it's HORIZONTAL (the two VERTICAL stub segments touching each
    anchor are never draggable -- unchanged, by design, both before
    and after the auto-router revert). Confirms this one still drags
    correctly post-revert, with the true endpoint anchors staying put."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    top_box = view._boxes["top"]
    sub_box = view._boxes["top/u_core"]
    view.create_power_domain(
        top_box, "PD_TOP", QColor("#ff0000"), 0.5, None, [], {},
        new_supply_ports={"VDD": QColor("#ff0000"), "VSS": QColor("#000000")},
    )
    view.create_power_domain(
        sub_box, "PD_SUB", QColor("#00ff00"), 0.5, None, [], {},
        new_supply_ports={"VDD_SUB": QColor("#00ff00"), "VSS": QColor("#000000")},
    )
    vdd = next(i for i in view._supply_port_icons.values() if i.owner_box is top_box and i.name == "VDD")
    vdd_sub = next(i for i in view._supply_port_icons.values() if i.owner_box is sub_box and i.name == "VDD_SUB")
    view.try_connect_supply_ports(vdd, vdd_sub)
    connector = view._supply_connectors[0]
    assert len(connector.waypoints) == 4  # exactly one middle segment
    idx = connector.segment_at((connector.waypoints[1] + connector.waypoints[2]) / 2)
    assert idx == 1
    assert connector.segment_orientation(idx) == "horizontal"
    before_x_span = (connector.waypoints[1].x(), connector.waypoints[2].x())
    target_y = connector.waypoints[idx].y() + 40

    connector.set_manual_segment_position(idx, target_y)

    assert connector.waypoints[idx].y() == target_y
    assert connector.waypoints[idx + 1].y() == target_y
    assert (connector.waypoints[1].x(), connector.waypoints[2].x()) == before_x_span  # a horizontal segment only moves vertically
    assert connector.waypoints[0] == _anchor(vdd)  # true endpoint anchor unchanged
    assert connector.waypoints[-1] == _anchor(vdd_sub)  # true endpoint anchor unchanged
    _deselect_and_collect(tab)


def test_dragging_a_vertical_segment_of_a_mixed_edge_pair_right_moves_it_and_keeps_endpoints_fixed(qtbot):
    """Explicit regression request, the vertical-drag half: a mixed-
    edge pair (see _connect_two_ports_with_multiple_bends()) has a
    genuinely draggable VERTICAL middle segment too -- dragging it
    horizontally moves it, and the true endpoint anchors stay fixed."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    outer, inner, connector = _connect_two_ports_with_multiple_bends(view)
    vertical_index = next(
        i for i in range(1, len(connector.waypoints) - 2)
        if connector.segment_orientation(i) == "vertical" and connector.segment_at((connector.waypoints[i] + connector.waypoints[i + 1]) / 2) == i
    )
    before_y_span = (connector.waypoints[vertical_index].y(), connector.waypoints[vertical_index + 1].y())
    target_x = connector.waypoints[vertical_index].x() + 25

    connector.set_manual_segment_position(vertical_index, target_x)

    assert connector.waypoints[vertical_index].x() == target_x
    assert connector.waypoints[vertical_index + 1].x() == target_x
    assert (connector.waypoints[vertical_index].y(), connector.waypoints[vertical_index + 1].y()) == before_y_span  # only X moved
    assert connector.waypoints[0] == _anchor(outer)  # true endpoint anchor unchanged
    assert connector.waypoints[-1] == _anchor(inner)  # true endpoint anchor unchanged
    _deselect_and_collect(tab)


def test_segment_at_finds_a_middle_segment_with_generous_padding(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    outer, inner, connector = _connect_two_ports(view)
    a, b = connector.waypoints[1], connector.waypoints[2]
    midpoint = (a + b) / 2

    assert connector.segment_at(midpoint) == 1
    just_off_the_line = type(midpoint)(midpoint.x(), midpoint.y() + 5)  # within padding, not exactly on it
    assert connector.segment_at(just_off_the_line) == 1
    far_away = type(midpoint)(midpoint.x(), midpoint.y() + 500)
    assert connector.segment_at(far_away) is None
    _deselect_and_collect(tab)


def test_segment_at_never_returns_the_first_or_last_segment(qtbot):
    """Perpendicular-anchor guarantee: the first/last anchor-touching
    segments must never be offered as draggable at all."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    outer, inner, connector = _connect_two_ports(view)

    first_segment_midpoint = (connector.waypoints[0] + connector.waypoints[1]) / 2
    last_segment_midpoint = (connector.waypoints[-2] + connector.waypoints[-1]) / 2

    assert connector.segment_at(first_segment_midpoint) != 0
    assert connector.segment_at(last_segment_midpoint) != len(connector.waypoints) - 2
    _deselect_and_collect(tab)


def test_set_manual_segment_position_moves_only_along_its_own_axis(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    outer, inner, connector = _connect_two_ports(view)
    assert connector.segment_orientation(1) == "horizontal"
    original_x_span = (connector.waypoints[1].x(), connector.waypoints[2].x())

    connector.set_manual_segment_position(1, 999.0)

    assert connector.waypoints[1].y() == 999.0
    assert connector.waypoints[2].y() == 999.0
    assert (connector.waypoints[1].x(), connector.waypoints[2].x()) == original_x_span  # X untouched
    assert connector.waypoints[0] == _anchor(outer)  # endpoints never move
    assert connector.waypoints[-1] == _anchor(inner)
    _deselect_and_collect(tab)


def test_dragging_a_vertical_segment_moves_it_horizontally(qtbot):
    """The first stub is vertical (top-edge exit) -- but confirm a
    genuinely vertical MIDDLE segment (found via segment_orientation())
    only accepts an X change, matching the spec's own "vertical ->
    left/right only" rule.

    `before` is read from a FRESH _auto_waypoints() -- the exact same
    baseline set_manual_segment_position() itself recomputes internally
    (see its own docstring)."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    outer, inner, connector = _connect_two_ports_with_multiple_bends(view)
    auto_waypoints = connector._auto_waypoints()
    vertical_index = next(
        i for i in range(1, len(auto_waypoints) - 2)
        if connector.segment_orientation(i) == "vertical"
    )
    before = (auto_waypoints[vertical_index], auto_waypoints[vertical_index + 1])

    connector.set_manual_segment_position(vertical_index, before[0].x() + 25)

    assert connector.waypoints[vertical_index].x() == before[0].x() + 25
    assert connector.waypoints[vertical_index + 1].x() == before[1].x() + 25
    assert connector.waypoints[vertical_index].y() == before[0].y()  # Y untouched
    assert connector.waypoints[vertical_index + 1].y() == before[1].y()
    _deselect_and_collect(tab)


def test_real_mouse_drag_on_a_segment_moves_it(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.resize(900, 700)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    outer, inner, connector = _connect_two_ports(view)
    a, b = connector.waypoints[1], connector.waypoints[2]
    start_scene = (a + b) / 2
    target_scene = start_scene + QPointF(0, 40)
    start_viewport = view.mapFromScene(start_scene)
    target_viewport = view.mapFromScene(target_scene)

    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start_viewport)
    assert view._segment_drag is not None
    QTest.mouseMove(view.viewport(), target_viewport)
    assert connector.waypoints[1].y() == a.y() + 40
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, target_viewport)

    assert view._segment_drag is None
    _deselect_and_collect(tab)


def test_a_manual_segment_drag_survives_an_unrelated_recompute(qtbot):
    """A dragged segment's own offset is stored as a delta from the
    auto-route's own value (see SupplyPortConnectorItem's own class
    docstring), so an unrelated change elsewhere (here, adding a supply
    port on a totally different box, which runs the pre-existing "make
    room" reflow) reapplies it rather than discarding it."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    outer, inner, connector = _connect_two_ports(view)
    idx = connector.segment_at((connector.waypoints[1] + connector.waypoints[2]) / 2)
    connector.set_manual_segment_position(idx, connector.waypoints[idx].y() + 30)
    dragged_waypoints = list(connector.waypoints)
    assert connector._manual_offset is not None  # sanity: the drag really did record something

    view.add_supply_port(view._boxes["top/u_dbg"], "VDD_DBG", QColor("#00ff00"))  # unrelated -- triggers a recompute

    assert connector.waypoints == dragged_waypoints
    assert connector._manual_offset is not None  # the offset itself is still there, not silently dropped
    _deselect_and_collect(tab)


def test_a_manual_segment_drag_survives_an_unrelated_rebuild(qtbot):
    """A rebuild (e.g. Hide/Unhide elsewhere in the tree) recreates
    every connector from scratch -- the offset must survive that too,
    which requires it to be persisted in SupplyPortDiagramState (not
    just live on the soon-to-be-destroyed connector object). Persists
    explicitly here (mimicking what mouseReleaseEvent() does for a real
    drag) since this test drives set_manual_segment_position() directly
    rather than through real Qt mouse events."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    outer, inner, connector = _connect_two_ports(view)
    idx = connector.segment_at((connector.waypoints[1] + connector.waypoints[2]) / 2)
    connector.set_manual_segment_position(idx, connector.waypoints[idx].y() + 30)
    manual_offset = connector._manual_offset
    view._shared_state.manual_connector_offsets[(outer.key, inner.key)] = manual_offset

    tab.tree._toggle_hidden("top/u_dbg")  # unrelated -- but rebuilds the whole scene (fresh box layout)

    new_connector = view._supply_connectors[0]
    # A rebuild reflows box layout, so anchor positions can genuinely
    # shift -- the honest guarantee is "the offset itself was
    # reapplied," not "the absolute pixel waypoints are byte-identical."
    assert new_connector._manual_offset == manual_offset
    _deselect_and_collect(tab)


def test_a_net_with_no_manual_adjustments_still_fully_auto_recomputes(qtbot):
    """A net that was never manually touched keeps behaving exactly as
    already implemented, recomputing its route fully and automatically
    on every relevant trigger."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    outer, inner, connector = _connect_two_ports(view)
    assert connector._manual_offset is None

    view.add_supply_port(view._boxes["top/u_dbg"], "VDD_DBG", QColor("#00ff00"))  # triggers a recompute

    assert connector._manual_offset is None
    assert connector.waypoints[0] == _anchor(outer)
    assert connector.waypoints[-1] == _anchor(inner)
    _deselect_and_collect(tab)


# --- Straight-line routing does not avoid an ordinary hierarchy block ------
# --- (explicit request, confirming the revert of the auto-router: only ----
# --- a `.lib` macro is ever routed around, matching the behavior that -----
# --- existed before the obstacle-avoiding engine was introduced) ----------


def test_a_route_crossing_an_ordinary_block_is_not_routed_around_it(qtbot):
    """After reverting the automatic obstacle-avoiding router, a power
    net whose straight path happens to cross a THIRD, ordinary (non-
    macro) hierarchy block must NOT be routed around it -- only a
    `.lib` macro box is ever treated as an obstacle (see
    collect_macro_obstacles()), matching the behavior that existed
    before the auto-router was ever introduced."""
    from frontend.src.power_intent_view.supply_port_overlay import _segment_intersects_rect

    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    top_box = view._boxes["top"]

    # Both endpoints on "top" itself (top edge / bottom edge -- a
    # legitimate "same box" nested pair, see is_nested_pair()) so their
    # shared X is freely choosable across the design's full width,
    # independent of any child box's own edges.
    outer = view.add_supply_port(top_box, "VDD", QColor("#ff0000"), edge="top", t=0.5)
    inner = view.add_supply_port(top_box, "VDD2", QColor("#00ff00"), edge="bottom", t=0.5)

    # A real, ordinary (non-macro) child box, its rect fetched fresh
    # now that no further insertions will move it -- forcing both
    # endpoints onto its own horizontal center makes the straight
    # vertical route pass directly through it.
    obstacle_box = view._boxes["top/u_dbg"]
    assert not obstacle_box.definition.is_black_box  # sanity: a plain block, not a macro
    obstacle_rect = obstacle_box.sceneBoundingRect()
    target_x = obstacle_rect.center().x()
    outer.setPos(top_box.mapFromScene(target_x, top_box.sceneBoundingRect().top()))
    inner.setPos(top_box.mapFromScene(target_x, top_box.sceneBoundingRect().bottom()))

    created = view.try_connect_supply_ports(outer, inner)

    assert created is True
    connector = view._supply_connectors[0]
    crosses = any(
        _segment_intersects_rect(connector.waypoints[i], connector.waypoints[i + 1], obstacle_rect)
        for i in range(len(connector.waypoints) - 1)
    )
    assert crosses is True  # not detoured -- an ordinary block is not an obstacle
    _deselect_and_collect(tab)


# --- Bug fix: adjacent same-edge Supply Port/Internal Supply labels must ---
# --- never overlap -- _add_missing_supply_ports()'s own i/(count+1) spread -
# --- only ever considered ITS OWN new batch, and _auto_link_cross_hierarchy_
# --- supply_sets() hardcoded t=0.5 for every auto-linked port, regardless
# --- of what already occupies that same edge -- both now go through the
# --- shared _edge_occupant_count() helper instead, so a NEW port never
# --- lands on the exact same t as one already there.


def test_two_supply_ports_added_separately_on_the_same_edge_get_distinct_non_overlapping_labels(qtbot):
    """The general case: two entirely separate add_supply_port() calls
    (not routed through any Supply-Set-composition flow) on the SAME
    block/edge, both relying on the default t=0.5 -- must not collide."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top/u_core"]

    first = view.add_internal_supply(box, "VDD_INT", QColor("#00aa00"))
    second = view.add_internal_supply(box, "VDD", QColor("#ff0000"))

    assert first.placement.t != second.placement.t
    first_label_rect = first.label.mapRectToScene(first.label.boundingRect())
    second_label_rect = second.label.mapRectToScene(second.label.boundingRect())
    assert not first_label_rect.intersects(second_label_rect)
    _deselect_and_collect(tab)


def test_add_missing_supply_ports_offsets_around_a_pre_existing_top_edge_occupant(qtbot):
    """`_add_missing_supply_ports()`'s own i/(count+1) batch-spread must
    now also account for a port that already existed on the SAME edge
    BEFORE this call -- not just spread its own new batch blind to it."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top/u_core"]
    pre_existing = view.add_internal_supply(box, "VDD_INT", QColor("#00aa00"))
    assert pre_existing.placement.t == 0.5  # the lone-occupant default

    from frontend.src.power_intent_view.power_domain_overlay import SupplySetPlacement

    new_set = SupplySetPlacement(id="ss1", name="SS1", power="VDD", owner_path="top/u_core")
    view.create_power_domain(box, "PD1", QColor("#4caf50"), 0.5, "ss1", [], {"ss1": new_set}, {"VDD": QColor("#ff0000")})

    new_port = next(p for p in view._shared_state.supply_port_placements.values() if p.name == "VDD" and p.owner_path == box.path)
    assert new_port.t != pre_existing.placement.t  # not the same collision-prone t

    new_icon = view._supply_port_icons[new_port.id]
    pre_existing_label_rect = pre_existing.label.mapRectToScene(pre_existing.label.boundingRect())
    new_label_rect = new_icon.label.mapRectToScene(new_icon.label.boundingRect())
    assert not pre_existing_label_rect.intersects(new_label_rect)
    _deselect_and_collect(tab)


def test_auto_linked_port_offsets_around_a_pre_existing_top_edge_occupant(qtbot, monkeypatch):
    """The exact repro from the bug report: a Supply Set composed
    locally ("VDD_INT") followed by an ancestor-shared Supply Set's own
    pin ("VDD") auto-linking onto the SAME block/edge -- must not
    collide."""
    from PySide6.QtWidgets import QDialog

    from frontend.src.power_intent_view.create_power_domain_dialog import CreatePowerDomainDialog, CreateSupplySetDialog
    from frontend.src.power_intent_view.power_domain_overlay import SupplySetPlacement

    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    top_box = view._boxes["top"]
    core_box = view._boxes["top/u_core"]
    ss_top = SupplySetPlacement(id="ss_top", name="SS_VDD", power="VDD", ground="VSS", owner_path="top")
    view.create_power_domain(top_box, "PD_TOP", QColor("#4caf50"), 0.5, "ss_top", [], {"ss_top": ss_top}, {"VDD": QColor("#ff0000"), "VSS": QColor("#0000ff")})

    def fake_exec(self):
        self.set_as_primary_checkbox.setChecked(False)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(CreateSupplySetDialog, "exec", fake_exec)
    dialog = CreatePowerDomainDialog(
        {}, [], view._existing_domain_names(), "top/u_core",
        all_domains=view._domain_state.domains, all_supply_sets=view._domain_state.supply_sets, supply_port_icons=view._supply_port_icons,
    )
    dialog.supply_set_combo.setCurrentIndex(dialog.supply_set_combo.findText("SS_VDD"))
    ss_vdd_int = SupplySetPlacement(id="ss_vdd_int", name="SS_VDD_INT", power="VDD_INT", ground="VSS", owner_path="top/u_core")
    dialog.new_supply_sets[ss_vdd_int.id] = ss_vdd_int
    dialog.new_supply_ports["VDD_INT"] = QColor("#00aa00")
    dialog._primary_id = ss_vdd_int.id

    view.create_power_domain(
        core_box, "PD_SUB", dialog.color, dialog.transparency, dialog.primary_supply_set_id, dialog.secondary_supply_set_ids, dialog.new_supply_sets, dialog.new_supply_ports
    )

    sub_icons = [icon for icon in view._supply_port_icons.values() if icon.owner_box is core_box]
    assert len(sub_icons) == 3  # VDD_INT, VDD, VSS -- see the auto-link feature extension's own worked example
    t_values = {icon.placement.name: icon.placement.t for icon in sub_icons}
    assert len(set(t_values.values())) == len(t_values)  # every one distinct -- no exact-t collision
    for a in sub_icons:
        for b in sub_icons:
            if a is b:
                continue
            a_rect = a.label.mapRectToScene(a.label.boundingRect())
            b_rect = b.label.mapRectToScene(b.label.boundingRect())
            assert not a_rect.intersects(b_rect), f"{a.placement.name} label overlaps {b.placement.name} label"
    _deselect_and_collect(tab)


def test_edge_label_respacing_direct_creation_matches_undo_redo(qtbot):
    """Same "direct creation must match undo+redo" parity check this
    project's own "make room" layout family already establishes for
    every other insertion type -- _respace_edge_occupants() mutates
    EXISTING occupants' own `.t` (not just the new one's), so this
    specifically confirms that mutation is captured correctly in the
    commit's own undo snapshot, not lost/reverted incorrectly on
    Ctrl+Z/Ctrl+Y."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top/u_core"]

    view.add_internal_supply(box, "VDD_INT", QColor("#00aa00"))
    view.add_internal_supply(box, "VDD", QColor("#ff0000"))
    direct_ts = {p.name: p.t for p in view._shared_state.supply_port_placements.values() if p.owner_path == box.path}
    direct_rect = view._boxes["top/u_core"].rect()

    tab.undo_shortcut.activated.emit()
    tab.redo_shortcut.activated.emit()

    box_after = view._boxes["top/u_core"]
    after_ts = {p.name: p.t for p in view._shared_state.supply_port_placements.values() if p.owner_path == box_after.path}
    assert after_ts == direct_ts
    assert box_after.rect() == direct_rect
    sub_icons = [icon for icon in view._supply_port_icons.values() if icon.owner_box is box_after]
    for a in sub_icons:
        for b in sub_icons:
            if a is b:
                continue
            a_rect = a.label.mapRectToScene(a.label.boundingRect())
            b_rect = b.label.mapRectToScene(b.label.boundingRect())
            assert not a_rect.intersects(b_rect)
    _deselect_and_collect(tab)
