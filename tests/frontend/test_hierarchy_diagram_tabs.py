"""Multi-tab Diagram support (Visualize Design and Power Intent both use
HierarchyDiagramTabs): double-click a box (or "Visualize Diagram" from
the Hierarchy Tree) opens that instance's own sub-hierarchy in a new,
closable tab, alongside the always-open whole-design one; switching to
an already-open path's tab reuses it rather than duplicating; Power
Intent's own supply-port state is shared across every tab so a change
made in a sub-module's tab is visible from the whole-hierarchy tab too.
Against mock RTL/PowerIntentDesign only.
"""

import gc

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor

from frontend.src.hierarchy_view.hierarchy_diagram_view import HierarchyDiagramView
from frontend.src.hierarchy_view.visualize_design_tab import VisualizeDesignTab
from frontend.src.power_intent_view.power_intent_tab import PowerIntentTab
from frontend.src.shared.diagram.hierarchy_diagram_tabs import HierarchyDiagramTabs
from frontend.src.shared.mock_backend import build_mock_design
from frontend.src.shared.mock_backend.mock_mapping import build_mock_mapped_design
from frontend.src.shared.mock_backend.mock_power_intent import build_mock_power_intent


def _deselect_and_collect(tabs: HierarchyDiagramTabs) -> None:
    for i in range(tabs.count()):
        view = tabs.widget(i)
        if view is not None and view.scene() is not None:
            view.scene().clearSelection()
    gc.collect()


# --- HierarchyDiagramTabs, driven directly via VisualizeDesignTab ----------


def test_root_tab_exists_and_is_never_closable(qtbot):
    tab = VisualizeDesignTab()
    qtbot.addWidget(tab)

    assert tab.diagram.count() == 1
    assert tab.diagram.tabBar().tabButton(0, tab.diagram.tabBar().ButtonPosition.RightSide) is None


def test_load_sets_the_root_tabs_text_and_tooltip_to_the_top_level_path(qtbot):
    tab = VisualizeDesignTab()
    qtbot.addWidget(tab)

    tab.load(build_mock_design())

    assert tab.diagram.tabText(0) == "top"
    assert tab.diagram.tabToolTip(0) == "top"
    _deselect_and_collect(tab.diagram)


def test_double_click_a_box_opens_a_new_closable_tab_rooted_there(qtbot):
    tab = VisualizeDesignTab()
    qtbot.addWidget(tab)
    tab.load(build_mock_design())
    root_view = tab.diagram.current_view

    root_view.openSubHierarchyRequested.emit("top/u_core")  # double-click's own extracted signal

    assert tab.diagram.count() == 2
    assert tab.diagram.currentIndex() == 1
    assert tab.diagram.tabText(1) == "top/u_core"
    assert tab.diagram.tabToolTip(1) == "top/u_core"
    assert tab.diagram.tabBar().tabButton(1, tab.diagram.tabBar().ButtonPosition.RightSide) is not None
    new_view = tab.diagram.current_view
    assert new_view is not root_view
    assert set(new_view._boxes) == {"top/u_core"}  # rooted there, not the whole design
    _deselect_and_collect(tab.diagram)


def test_opening_an_already_open_path_switches_to_it_instead_of_duplicating(qtbot):
    tab = VisualizeDesignTab()
    qtbot.addWidget(tab)
    tab.load(build_mock_design())
    tab.diagram.open_path("top/u_core")
    assert tab.diagram.count() == 2
    tab.diagram.setCurrentIndex(0)

    tab.diagram.open_path("top/u_core")

    assert tab.diagram.count() == 2  # no duplicate tab
    assert tab.diagram.currentIndex() == 1
    _deselect_and_collect(tab.diagram)


def test_opening_the_root_path_again_just_stays_on_the_root_tab(qtbot):
    tab = VisualizeDesignTab()
    qtbot.addWidget(tab)
    tab.load(build_mock_design())

    tab.diagram.open_path("top")

    assert tab.diagram.count() == 1
    assert tab.diagram.currentIndex() == 0
    _deselect_and_collect(tab.diagram)


def test_opening_an_unknown_path_is_a_harmless_no_op(qtbot):
    tab = VisualizeDesignTab()
    qtbot.addWidget(tab)
    tab.load(build_mock_design())

    tab.diagram.open_path("top/does_not_exist")  # must not raise

    assert tab.diagram.count() == 1
    _deselect_and_collect(tab.diagram)


def test_closing_a_sub_hierarchy_tab_removes_it(qtbot):
    tab = VisualizeDesignTab()
    qtbot.addWidget(tab)
    tab.load(build_mock_design())
    tab.diagram.open_path("top/u_core")
    assert tab.diagram.count() == 2

    tab.diagram.tabCloseRequested.emit(1)

    assert tab.diagram.count() == 1
    _deselect_and_collect(tab.diagram)

    # Reopening after close must work fine (not silently blocked by stale bookkeeping)
    tab.diagram.open_path("top/u_core")
    assert tab.diagram.count() == 2
    _deselect_and_collect(tab.diagram)


def test_closing_the_root_tab_is_a_no_op(qtbot):
    tab = VisualizeDesignTab()
    qtbot.addWidget(tab)
    tab.load(build_mock_design())

    tab.diagram.tabCloseRequested.emit(0)

    assert tab.diagram.count() == 1
    _deselect_and_collect(tab.diagram)


def test_reloading_the_design_closes_every_sub_hierarchy_tab(qtbot):
    tab = VisualizeDesignTab()
    qtbot.addWidget(tab)
    tab.load(build_mock_design())
    tab.diagram.open_path("top/u_core")
    assert tab.diagram.count() == 2

    tab.load(build_mock_design())  # fresh load, e.g. a different design entirely

    assert tab.diagram.count() == 1
    _deselect_and_collect(tab.diagram)


def test_visualize_diagram_tree_action_opens_the_same_tab_as_double_click(qtbot):
    tab = VisualizeDesignTab()
    qtbot.addWidget(tab)
    tab.load(build_mock_design())

    tab.tree.visualizeDiagramRequested.emit("top/u_core")

    assert tab.diagram.count() == 2
    assert tab.diagram.tabText(1) == "top/u_core"
    _deselect_and_collect(tab.diagram)


def test_design_properties_still_updates_from_selection_in_a_sub_hierarchy_tab(qtbot):
    """"Design Properties... should always work" -- explicit request.
    Selecting a box while a sub-hierarchy tab is active must still reach
    the Properties panel, same as it always did against the root tab."""
    tab = VisualizeDesignTab()
    qtbot.addWidget(tab)
    tab.load(build_mock_design())
    tab.diagram.open_path("top/u_core")
    view = tab.diagram.current_view

    box = view._boxes["top/u_core"]
    box.setSelected(True)
    view._on_scene_selection_changed()  # setSelected() alone doesn't fire selectionChanged headlessly

    assert tab.properties._port_panel.table.rowCount() > 0  # populated for the selected instance
    _deselect_and_collect(tab.diagram)


# --- cross-tab sync (Power Intent's own shared supply-port state) ----------


def _power_intent_mapped(tab: PowerIntentTab):
    rtl = build_mock_design()
    power_intent = build_mock_power_intent()
    mapped = build_mock_mapped_design(power_intent, rtl)
    tab.set_rtl(rtl)
    tab.set_power_intent(power_intent)
    tab.set_mapped_design(mapped)
    return mapped


def test_power_intent_sub_hierarchy_tab_gets_the_full_overlay_too(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    _power_intent_mapped(tab)

    tab.diagram.open_path("top/u_core")

    view = tab.diagram._view  # current tab, via the back-compat property
    assert set(view._boxes) == {"top/u_core"}
    _deselect_and_collect(tab.diagram._tabs)


def test_supply_port_added_in_a_sub_tab_is_visible_from_the_root_tab(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    _power_intent_mapped(tab)
    root_view = tab.diagram._tabs.current_view
    tab.diagram.open_path("top/u_core")
    sub_view = tab.diagram._tabs.current_view
    assert sub_view is not root_view

    sub_view.add_supply_port(sub_view._boxes["top/u_core"], "VDD_SUB", QColor("#ff00ff"))

    tab.diagram._tabs.setCurrentIndex(0)  # switch back to the root tab -- triggers its own refresh()
    root_icons = {i.name for i in root_view._supply_port_icons.values() if not i.fixed}
    assert "VDD_SUB" in root_icons
    _deselect_and_collect(tab.diagram._tabs)


def test_supply_port_added_in_the_root_tab_is_visible_in_a_newly_opened_sub_tab(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    _power_intent_mapped(tab)
    root_view = tab.diagram._tabs.current_view
    root_view.add_supply_port(root_view._boxes["top/u_core"], "VDD_ROOT", QColor("#00ffff"))

    tab.diagram.open_path("top/u_core")

    sub_view = tab.diagram._tabs.current_view
    sub_icons = {i.name for i in sub_view._supply_port_icons.values() if not i.fixed}
    assert "VDD_ROOT" in sub_icons
    _deselect_and_collect(tab.diagram._tabs)


def test_hide_in_one_tab_propagates_live_to_a_background_tab(qtbot):
    """Hide/Unhide propagates through the one shared HierarchySelectionController
    every tab's view is built against -- must reach a tab that is NOT
    currently active too, not just the visible one."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    _power_intent_mapped(tab)
    root_view = tab.diagram._tabs.current_view
    tab.diagram.open_path("top/u_core")  # now the active tab

    tab.tree._toggle_hidden("top/u_dbg")  # root_view is currently in the background

    assert "top/u_dbg" not in root_view._boxes
    _deselect_and_collect(tab.diagram._tabs)
