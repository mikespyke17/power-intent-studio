"""Bug fix: a power net connector must always terminate at an
element's own correct ANCHOR HANDLE (ConnectorHandleItem.scene_center()),
never at the element's own bare scenePos() -- which, for every icon
type here (SupplyPortIconItem, PowerSwitchIconItem), is the icon's own
geometric CENTER, not any point on its edge. A handle's own scenePos()
is ALSO not its true position (a real, earlier-fixed bug for the
connector-DRAG preview line -- see ConnectorHandleItem.scene_center()'s
own docstring); that earlier fix was never carried over to
SupplyPortConnectorItem's own COMMITTED route computation
(_compute_auto_route()/update_route()/follow_endpoint_move()), which is
what this suite locks in -- see supply_port_overlay.py's own
connector_anchor_scene_pos() docstring for the full "why" and the one
case (the AUTOMATIC Power Switch <-> target connector) that needs an
explicit handle override rather than each icon's own natural default.
"""

from PySide6.QtGui import QColor

from frontend.src.power_intent_view.power_intent_tab import PowerIntentTab
from frontend.src.power_intent_view.power_switch_overlay import power_switch_connector_key
from frontend.src.shared.mock_backend import build_mock_design


def _tab_and_view(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    return tab, tab.diagram._view


def test_supply_port_to_internal_supply_terminates_at_the_top_anchor_not_the_center(qtbot):
    tab, view = _tab_and_view(qtbot)
    source = view.add_supply_port(view._boxes["top"], "VDD", QColor("#ff0000"))
    internal = view.add_internal_supply(view._boxes["top/u_core"], "Y", QColor("#ff0000"))
    # The bug's own literal symptom: the icon's bare scenePos() (its
    # center) is a genuinely DIFFERENT point from its top handle.
    assert internal.scenePos() != internal.handles["top"].scene_center()

    assert view.try_connect_supply_ports(source, internal) is True

    connector = next(c for c in view._supply_connectors if internal in (c.port_a, c.port_b))
    assert connector.waypoints[-1] == internal.handles["top"].scene_center()
    assert connector.waypoints[-1] != internal.scenePos()


def test_automatic_power_switch_connector_terminates_at_the_targets_top_anchor(qtbot):
    tab, view = _tab_and_view(qtbot)
    domain = view.create_power_domain(view._boxes["top"], "PD1", QColor("#ff0000"), 0.5, None, [], {})
    target = view.add_internal_supply(view._boxes["top/u_core"], "Y", QColor("#ff0000"))

    switch = view.add_power_switch(target, "SW1", "in", "out", "ctrl", None, "on", "off")

    connector = view._power_switch_connectors[switch.id][0]
    assert connector.waypoints[-1] == target.handles["top"].scene_center()
    assert connector.waypoints[-1] != target.scenePos()


def test_automatic_power_switch_connector_starts_at_the_switchs_own_bottom_anchor(qtbot):
    """Confirms the fix is general (explicit request): the switch's OWN
    end of the connector must also resolve to its correct anchor (the
    "bottom"/Supply Out handle), not its own bare scenePos()."""
    tab, view = _tab_and_view(qtbot)
    domain = view.create_power_domain(view._boxes["top"], "PD1", QColor("#ff0000"), 0.5, None, [], {})
    target = view.add_internal_supply(view._boxes["top/u_core"], "Y", QColor("#ff0000"))

    switch = view.add_power_switch(target, "SW1", "in", "out", "ctrl", None, "on", "off")

    switch_icon = view._power_switch_icons[switch.id][0]
    connector = view._power_switch_connectors[switch.id][0]
    assert connector.waypoints[0] == switch_icon.handles["bottom"].scene_center()
    assert connector.waypoints[0] != switch_icon.scenePos()


def test_manual_power_switch_supply_in_connector_terminates_at_the_switchs_own_top_anchor(qtbot):
    """A manually-drawn connection TO a Power Switch (its own "Supply
    In") must land on the switch's own "top" handle, not its center --
    confirms the fix handles the switch as a source/target too, not
    just as the automatic connector's own "bottom" role."""
    tab, view = _tab_and_view(qtbot)
    domain = view.create_power_domain(view._boxes["top"], "PD1", QColor("#ff0000"), 0.5, None, [], {})
    source = view.add_supply_port(view._boxes["top"], "VDD", QColor("#ff0000"))
    target = view.add_internal_supply(view._boxes["top/u_core"], "Y", QColor("#ff0000"))
    switch = view.add_power_switch(target, "SW1", "in", "out", "ctrl", None, "on", "off")
    switch_icon = view._power_switch_icons[switch.id][0]

    assert view.try_connect_supply_ports(switch_icon, source) is True

    connector = next(c for c in view._supply_connectors if switch_icon in (c.port_a, c.port_b))
    assert connector.waypoints[0] == switch_icon.handles["top"].scene_center()
    assert connector.waypoints[0] != switch_icon.scenePos()


def test_connector_stays_anchored_correctly_after_a_settle_recompute(qtbot):
    """update_route() (not just the initial _compute_auto_route()) must
    also resolve the correct anchor -- exercised via a full net-route
    recompute pass, the same one a "make room" reflow triggers."""
    tab, view = _tab_and_view(qtbot)
    source = view.add_supply_port(view._boxes["top"], "VDD", QColor("#ff0000"))
    internal = view.add_internal_supply(view._boxes["top/u_core"], "Y", QColor("#ff0000"))
    view.try_connect_supply_ports(source, internal)
    connector = next(c for c in view._supply_connectors if internal in (c.port_a, c.port_b))

    connector.update_route()

    assert connector.waypoints[-1] == internal.handles["top"].scene_center()
