"""PERMANENT regression suite: does an Internal Supply's own row in the
Properties table's Supply Port tab correctly reflect its connectivity-
derived state -- both its "Connected" column (who it's wired to) and
its "States" column (which Power States it inherits through that
wiring)? Both columns are really the SAME underlying concern applied to
two different fields (per explicit request, kept together in one
module rather than split across two): does this table's own row-
building code actually walk the live connectivity graph for an Internal
Supply, or does it use some stale/incomplete/hardcoded source instead?

Both bugs covered here were found via the SAME concrete repro: Create a
Power Domain on the TOP hierarchy with Supply Set SS_VDD (Power=VDD,
Ground=VSS), then create a SECOND Power Domain on an INNER hierarchy
with a Supply Set reusing the same Power/Ground names -- this triggers
PowerIntentDiagramView._auto_link_cross_hierarchy_supply_sets() (the
cross-hierarchy name-reuse fix), which auto-creates a matching Internal
Supply on the inner hierarchy AND a real, ordinary power net connecting
it to the top-level Supply Port of the same name.

Investigated independently, empirically, before fixing (explicit
request -- do not assume a shared root cause just because both concern
the same row):

  - "Connected" column: a REAL bug. properties_panel.py's own
    _load_supply_ports() hardcoded "-" for EVERY entry in
    `supply_state.supply_port_placements` (both plain Supply Port and
    Internal Supply alike), never consulting the live connector graph
    at all for this specific loop -- unlike the sibling PG-pin loop
    just below it, which already correctly calls
    connected_supply_port() (supply_port_overlay.py). Fixed by calling
    that SAME, already-generic resolver for Internal Supply rows only
    (a plain Supply Port keeps its existing "-", unchanged, by design).

  - "States" column: NOT a bug -- already correct. `states =
    resolve_effective_states(p, self._supply_state, switches)` was
    ALREADY called unconditionally for every supply_port_placements
    row, and that function's own trace_upstream_source() already walks
    ANY connector in `supply_connector_placements` generically (manual
    or auto-created, no distinction) to find the real source Supply
    Port's own states. Confirmed via a live diagnostic before writing
    the test below: the auto-linked Internal Supply's own States cell
    already showed the inherited states correctly, with zero code
    changes needed.
"""

import gc

from PySide6.QtGui import QColor

from frontend.src.power_intent_view.power_domain_overlay import SupplySetPlacement
from frontend.src.power_intent_view.power_intent_tab import PowerIntentTab
from frontend.src.power_intent_view.properties_panel import _ElementsCellWidget
from frontend.src.power_intent_view.supply_port_overlay import SupplyPortState
from frontend.src.shared.mock_backend import build_mock_design


def _deselect_and_collect(tab: PowerIntentTab) -> None:
    if tab.diagram._view.scene() is not None:
        tab.diagram._view.scene().clearSelection()
    gc.collect()


def _tab_with_cross_hierarchy_auto_link(qtbot, top_states=None):
    """The exact shared repro: Power Domain SS_VDD on "top" (Power=VDD,
    Ground=VSS), then a SECOND Power Domain on "top/u_core" reusing the
    same names -- auto-linking VDD and VSS as Internal Supplies there,
    each auto-connected to its top-level counterpart. `top_states`
    (optional), if given, is applied to the TOP-LEVEL VDD/VSS via a real
    edit_supply_port() call BEFORE the second domain is created, so the
    auto-linked Internal Supply has real states to inherit."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    top_box = view._boxes["top"]
    core_box = view._boxes["top/u_core"]

    ss_top = SupplySetPlacement(id="ss_top", name="SS_VDD", power="VDD", ground="VSS", owner_path="top")
    view.create_power_domain(top_box, "PD_TOP", QColor("#4caf50"), 0.5, "ss_top", [], {"ss_top": ss_top}, {"VDD": QColor("#ff0000"), "VSS": QColor("#0000ff")})

    if top_states is not None:
        vdd_top = next(p for p in view._shared_state.supply_port_placements.values() if p.name == "VDD" and p.owner_path == "top")
        vss_top = next(p for p in view._shared_state.supply_port_placements.values() if p.name == "VSS" and p.owner_path == "top")
        vdd_icon = view._supply_port_icons[vdd_top.id]
        vss_icon = view._supply_port_icons[vss_top.id]
        view.edit_supply_port(vdd_icon, "VDD", vdd_icon.color, states=top_states)
        view.edit_supply_port(vss_icon, "VSS", vss_icon.color, states=top_states)

    ss_core = SupplySetPlacement(id="ss_core", name="SS_VDD_INT", power="VDD", ground="VSS", owner_path="top/u_core")
    view.create_power_domain(core_box, "PD_CORE", QColor("#2196f3"), 0.5, "ss_core", [], {"ss_core": ss_core})

    return tab, view


def _row_for(table, name: str) -> int:
    return next(r for r in range(table.rowCount()) if table.item(r, 1) is not None and table.item(r, 1).text() == name)


# --- Bug 2: "Connected" column ---------------------------------------------------


def test_auto_linked_internal_supply_rows_show_their_connection_in_the_connected_column(qtbot):
    tab, view = _tab_with_cross_hierarchy_auto_link(qtbot)
    table = tab.properties._tables["SupplyPort"]

    vdd_row = _row_for(table, "u_core/VDD")
    vss_row = _row_for(table, "u_core/VSS")

    assert table.item(vdd_row, 0).text() == "Internal Supply"
    assert table.item(vdd_row, 3).text() == "VDD"  # Connected (column 3) -- the top-level Supply Port of the same name
    assert table.item(vss_row, 0).text() == "Internal Supply"
    assert table.item(vss_row, 3).text() == "VSS"
    _deselect_and_collect(tab)


def test_a_plain_supply_port_still_shows_the_dash_unchanged(qtbot):
    """The fix is scoped to Internal Supply rows only -- a plain,
    top-level Supply Port keeps its existing "-" ("nothing upstream of
    a supply declaration itself"), by design, unaffected."""
    tab, view = _tab_with_cross_hierarchy_auto_link(qtbot)
    table = tab.properties._tables["SupplyPort"]

    vdd_row = _row_for(table, "VDD")
    assert table.item(vdd_row, 0).text() == "Supply Port"
    assert table.item(vdd_row, 3).text() == "-"
    _deselect_and_collect(tab)


def test_an_unconnected_internal_supply_still_shows_the_dash(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view.add_internal_supply(view._boxes["top/u_core"], "VSS_ORPHAN", QColor("#0000ff"))

    table = tab.properties._tables["SupplyPort"]
    row = _row_for(table, "u_core/VSS_ORPHAN")
    assert table.item(row, 0).text() == "Internal Supply"
    assert table.item(row, 3).text() == "-"
    _deselect_and_collect(tab)


# --- Bug 3: "States" column -------------------------------------------------------


def test_auto_linked_internal_supply_rows_show_their_inherited_states(qtbot):
    states = [SupplyPortState("FULL_ON", "1.0"), SupplyPortState("OFF")]
    tab, view = _tab_with_cross_hierarchy_auto_link(qtbot, top_states=states)
    table = tab.properties._tables["SupplyPort"]

    vdd_row = _row_for(table, "u_core/VDD")
    vss_row = _row_for(table, "u_core/VSS")

    vdd_widget = table.cellWidget(vdd_row, 4)  # States
    vss_widget = table.cellWidget(vss_row, 4)
    assert isinstance(vdd_widget, _ElementsCellWidget)
    assert isinstance(vss_widget, _ElementsCellWidget)
    # The SOURCE's own OFF, user-authored and unnamed -- so no switch OFF
    # is added (the source already has one) and no name is shown.
    assert vdd_widget._elements == ["FULL_ON (1.0V)", "OFF"]
    assert vss_widget._elements == ["FULL_ON (1.0V)", "OFF"]
    _deselect_and_collect(tab)


def test_auto_linked_internal_supply_with_no_states_on_the_source_shows_the_placeholder(qtbot):
    """Contrast case: the source Supply Port has no states of its own
    at all -- the Internal Supply row must fall back to the same "-"
    placeholder every states-less row already uses, not a stale/leftover
    widget."""
    tab, view = _tab_with_cross_hierarchy_auto_link(qtbot)
    table = tab.properties._tables["SupplyPort"]

    row = _row_for(table, "u_core/VDD")
    assert table.cellWidget(row, 4) is None
    assert table.item(row, 4) is not None and table.item(row, 4).text() == "-"
    _deselect_and_collect(tab)


# --- Bug 3 follow-up (explicit report): a Power Switch ALSO present along --
# --- the chain -- investigated independently, empirically, per this ---------
# --- module's own established discipline (do not assume a shared root ------
# --- cause with the "Connected"/"States" bugs above just because both -------
# --- concern the same row). Two orderings covered, since the report itself -
# --- specifically asked to rule out an ordering-dependent gap: switch added
# --- BEFORE the connecting net is drawn (the reported repro's own order), --
# --- and the REVERSE (net first, then switch) -- both confirmed, via a live
# --- diagnostic before writing these, to already produce the correct
# --- inherited "ON" state PLUS the synthesized "OFF" state
# --- with the CURRENT code (trace_upstream_source()'s own single-hop-per-
# --- switch walk already generalizes correctly regardless of ordering,
# --- since the switch's own Supply-Out connector to its target is never
# --- persisted in supply_connector_placements at all -- only a real,
# --- manually-drawn/auto-linked net is -- so there is no possible
# --- ambiguity between a switch's Supply In and Supply Out sharing one
# --- connector-list entry). Kept as PERMANENT regression coverage either
# --- way, per the explicit request.


def test_states_column_correct_when_switch_is_added_before_the_connecting_net(qtbot):
    """Exact reported repro order: (1) Power Switch added on the
    Internal Supply FIRST, (2) a Power State added on the eventual
    source Supply Port SECOND, (3) the connecting net drawn LAST."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    top_box = view._boxes["top"]
    core_box = view._boxes["top/u_core"]

    vdd_icon = view.add_supply_port(top_box, "VDD", QColor("#ff0000"))
    view.create_power_domain(core_box, "PD_CORE", QColor("#4caf50"), 0.5, None, [], {})
    vdd_int_icon = view.add_internal_supply(core_box, "VDD_INT", QColor("#00aa00"))

    # (1) Power Switch first.
    switch = view.add_power_switch(vdd_int_icon, "PG", "in", "out", "ctrl", None, "on", "off")

    # (2) Power State on the source second.
    view.edit_supply_port(vdd_icon, "VDD", vdd_icon.color, states=[SupplyPortState("FULL_ON", 0.8)])

    # (3) Connecting net last, straight to the switch's own top (Supply In) anchor.
    switch_icon = view._power_switch_icons[switch.id][0]
    assert view.try_connect_supply_ports(vdd_icon, switch_icon) is True

    table = tab.properties._tables["SupplyPort"]
    row = _row_for(table, "u_core/VDD_INT")
    widget = table.cellWidget(row, 4)  # States
    assert isinstance(widget, _ElementsCellWidget), "expected a states widget, got the '-' placeholder instead"
    assert widget._elements == ["FULL_ON (0.8V)", "SWITCH-OFF [OFF]"]
    _deselect_and_collect(tab)


def test_states_column_correct_when_the_connecting_net_is_drawn_before_the_switch(qtbot):
    """Reverse order, to rule out an ordering-dependent gap specifically:
    (1) Power State added on the source FIRST, (2) the connecting net
    drawn to the (plain, not-yet-switched) Internal Supply SECOND, (3)
    the Power Switch added LAST -- item 8's own "adopt existing
    connection as Supply In" re-routes that same net onto the switch's
    own top anchor."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    top_box = view._boxes["top"]
    core_box = view._boxes["top/u_core"]

    vdd_icon = view.add_supply_port(top_box, "VDD", QColor("#ff0000"))
    view.create_power_domain(core_box, "PD_CORE", QColor("#4caf50"), 0.5, None, [], {})
    vdd_int_icon = view.add_internal_supply(core_box, "VDD_INT", QColor("#00aa00"))

    # (1) Power State on the source first.
    view.edit_supply_port(vdd_icon, "VDD", vdd_icon.color, states=[SupplyPortState("FULL_ON", 0.8)])

    # (2) Connecting net second, directly to the (still plain) Internal Supply.
    assert view.try_connect_supply_ports(vdd_icon, vdd_int_icon) is True

    # (3) Power Switch last -- adopts the existing connection as Supply In.
    switch = view.add_power_switch(vdd_int_icon, "PG", "in", "out", "ctrl", None, "on", "off")

    table = tab.properties._tables["SupplyPort"]
    row = _row_for(table, "u_core/VDD_INT")
    widget = table.cellWidget(row, 4)  # States
    assert isinstance(widget, _ElementsCellWidget)
    assert widget._elements == ["FULL_ON (0.8V)", "SWITCH-OFF [OFF]"]
    _deselect_and_collect(tab)
