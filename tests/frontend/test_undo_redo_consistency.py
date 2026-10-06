"""Undo (Ctrl+Z) / redo (Ctrl+Y) must consistently update every relevant
view -- the diagram (icons, positions, colors), the Properties tables,
and the Power View tree.

Bug fix: _PowerIntentGraphicsView.refresh() only rebuilds the ONE view
it's called on -- fine for a single-tab design, but PowerIntentDiagramView
can have several open tabs (the always-open root tab plus any opened
sub-hierarchy tabs), each a SEPARATE _PowerIntentGraphicsView sharing the
same underlying diagram-only state objects. Confirmed via a live repro:
creating a Power Domain from a sub-hierarchy tab, then undoing it while
the ROOT tab was active, left the (never-open-at-undo-time) sub-hierarchy
tab's own diagram permanently stuck showing the undone state -- because
PowerIntentDiagramView._refresh_current_view() (the callback
_DiagramStateCommand.undo()/redo() call) only ever refreshed
self._tabs.current_view, not every open tab. Fixed by refreshing every
tab returned by HierarchyDiagramTabs.all_views() instead -- safe to do
unconditionally here (confirmed this method is ONLY ever invoked by
_DiagramStateCommand's own undo()/redo(), never a normal edit's first,
synchronous commit, so there's no risk of destroying a live icon a
caller is still about to use, the hazard _first_redo_done already
guards against for normal edits).

Properties table / Power View tree were already correctly wired (both
are single, tab-independent widgets driven by the same
diagramStateChanged signal every commit AND every undo/redo already
fires) -- confirmed via direct investigation before writing this fix,
not assumed.
"""

from PySide6.QtGui import QColor

from frontend.src.power_intent_view.power_intent_tab import PowerIntentTab
from frontend.src.power_intent_view.supply_port_overlay import SupplyPortState
from frontend.src.shared.mock_backend import build_mock_design


def _undo(tab):
    tab.undo_shortcut.activated.emit()


def _redo(tab):
    tab.redo_shortcut.activated.emit()


# --- Power Domain: diagram + Properties + Power View tree all revert --------


def test_undo_redo_a_power_domain_updates_diagram_properties_and_power_view(qtbot):
    """A genuine undo/redo REBUILDS the whole scene (see refresh()'s own
    docstring/hierarchy_diagram_view.py's own _rebuild()) -- any
    previously-held InstanceBoxItem reference goes stale (its own C++
    object is deleted) across the call, same pre-existing lifecycle
    every tab-switch-triggered refresh() already has; re-fetching
    `view._boxes["top/u_core"]` fresh after each undo/redo is the
    correct pattern here, not a workaround."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view

    view.create_power_domain(view._boxes["top/u_core"], "PD1", QColor("#ff0000"), 0.5, None, [], {})
    assert view._boxes["top/u_core"].brush().color().name() == "#ff0000"
    assert tab.properties._tables["PowerDomain"].rowCount() == 1
    assert tab.power_view_tree.topLevelItemCount() == 1

    _undo(tab)
    assert view._boxes["top/u_core"].brush().color().name() == "#ffffff"
    assert tab.properties._tables["PowerDomain"].rowCount() == 0
    assert tab.power_view_tree.topLevelItemCount() == 0

    _redo(tab)
    assert view._boxes["top/u_core"].brush().color().name() == "#ff0000"
    assert tab.properties._tables["PowerDomain"].rowCount() == 1
    assert tab.power_view_tree.topLevelItemCount() == 1


def test_undo_redo_a_power_domain_stays_consistent_across_every_open_tab(qtbot):
    """The confirmed real bug: a domain created from a sub-hierarchy tab
    must be correctly reverted/reapplied on EVERY open tab, not just
    whichever one happens to be active when Ctrl+Z/Ctrl+Y fires."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    root_view = tab.diagram._tabs.current_view
    tab.diagram.open_path("top/u_core")
    sub_view = tab.diagram._tabs.current_view
    assert sub_view is not root_view

    sub_view.create_power_domain(sub_view._boxes["top/u_core"], "PD_SUB", QColor("#0000ff"), 0.5, None, [], {})
    tab.diagram._tabs.setCurrentIndex(0)  # switch to the ROOT tab before undoing

    _undo(tab)
    assert root_view._boxes["top/u_core"].brush().color().name() == "#ffffff"
    assert sub_view._boxes["top/u_core"].brush().color().name() == "#ffffff"

    _redo(tab)
    assert root_view._boxes["top/u_core"].brush().color().name() == "#0000ff"
    assert sub_view._boxes["top/u_core"].brush().color().name() == "#0000ff"


# --- Supply Port with States: States column reverts/reapplies correctly ----


def test_undo_redo_a_supply_port_with_states_updates_the_states_column(qtbot):
    from frontend.src.power_intent_view.properties_panel import _ElementsCellWidget

    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view

    view.add_supply_port(
        view._boxes["top"], "VDD", QColor("#ff0000"), states=[SupplyPortState("FULL_ON", "1.0")]
    )
    table = tab.properties._tables["SupplyPort"]
    row = next(r for r in range(table.rowCount()) if table.item(r, 1) is not None and table.item(r, 1).text() == "VDD")
    assert isinstance(table.cellWidget(row, 4), _ElementsCellWidget)  # States is column 4

    _undo(tab)
    table = tab.properties._tables["SupplyPort"]
    assert not any(table.item(r, 1) is not None and table.item(r, 1).text() == "VDD" for r in range(table.rowCount()))

    _redo(tab)
    table = tab.properties._tables["SupplyPort"]
    row = next(r for r in range(table.rowCount()) if table.item(r, 1) is not None and table.item(r, 1).text() == "VDD")
    widget = table.cellWidget(row, 4)
    assert isinstance(widget, _ElementsCellWidget)
    assert widget._elements == ["FULL_ON (1.0V)"]


# --- Isolation: deleting then undoing brings the icon back -------------------


def test_undo_redo_deleting_an_isolation_brings_the_icon_back(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top/u_core"]
    domain = view.create_power_domain(box, "PD1", QColor("#ff0000"), 0.5, None, [], {})
    iso = view.create_isolation(box, "ISO1", domain.id, None, None, None, "low", "self", None, "high", ["top/u_core"])
    assert iso.id in view._isolation_icons
    assert tab.properties._tables["IsolationStrategy"].rowCount() == 1

    view.delete_isolation(iso.id)
    assert iso.id not in view._isolation_icons
    assert tab.properties._tables["IsolationStrategy"].rowCount() == 0

    _undo(tab)
    assert iso.id in view._isolation_state.strategies
    assert iso.id in view._isolation_icons  # icon reappears in the diagram
    assert tab.properties._tables["IsolationStrategy"].rowCount() == 1
    assert tab.power_view_tree.topLevelItemCount() == 1

    _redo(tab)
    assert iso.id not in view._isolation_state.strategies
    assert iso.id not in view._isolation_icons
    assert tab.properties._tables["IsolationStrategy"].rowCount() == 0
