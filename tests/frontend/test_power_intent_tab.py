"""power_intent_view/power_intent_tab.py and power_intent_diagram_view.py
-- the pre-Mapping placeholder state and the post-Mapping overlay state,
against mock/fixture data only.
"""

import gc

from PySide6.QtWidgets import QDockWidget, QGroupBox

from frontend.src.power_intent_view.power_intent_tab import PowerIntentTab
from frontend.src.shared.mock_backend import build_mock_design, build_mock_mapped_design, build_mock_power_intent


def _deselect_and_collect(tab: PowerIntentTab) -> None:
    """Same segfault hazard, same fix, as hierarchy_view's own
    _deselect_and_collect() in test_hierarchy_tree_diagram_sync.py -- see
    that helper's own docstring. Call at the end of any test that leaves
    a diagram box genuinely selected."""
    if tab.diagram._view.scene() is not None:
        tab.diagram._view.scene().clearSelection()
    gc.collect()


def test_diagram_shows_placeholder_before_mapping_has_run(qtbot):
    """'Power Intent tab open, Mapping hasn't run yet' -- exercised for
    real, not just designed: after set_rtl() alone (the state right
    after Load Power Intent succeeds but before the Mapping button has
    ever been clicked), the diagram must show the bare-hierarchy-plus-
    placeholder widget, with zero power-intent overlay items -- not an
    empty overlay silently mixed into the graphics view.
    """
    tab = PowerIntentTab()
    rtl = build_mock_design()

    tab.set_rtl(rtl)

    assert tab.diagram._stack.currentWidget() is tab.diagram._placeholder
    assert tab.diagram._index == {}
    # The bare hierarchy itself is still real, not blank -- confirms this
    # is "no overlay yet," not "nothing rendered at all."
    assert tab.diagram._view.scene() is not None
    assert len(tab.diagram._view.scene().items()) > 0


def test_diagram_switches_to_full_overlay_once_mapping_completes(qtbot):
    tab = PowerIntentTab()
    rtl = build_mock_design()
    power_intent = build_mock_power_intent()
    mapped = build_mock_mapped_design(power_intent, rtl)

    tab.set_rtl(rtl)
    assert tab.diagram._stack.currentWidget() is tab.diagram._placeholder

    tab.set_power_intent(power_intent)
    tab.set_mapped_design(mapped)

    assert tab.diagram._stack.currentWidget() is tab.diagram._tabs
    assert len(tab.diagram._index) > 0
    assert ("PowerDomain", "::PD_CORE") in tab.diagram._index


def test_properties_panel_is_populated_independent_of_mapping_state(qtbot):
    """Properties come from Load Power Intent alone (set_power_intent()),
    not from Mapping -- the tab's two data sources (PowerIntentDesign for
    properties, MappedDesign for the diagram overlay) are genuinely
    independent inputs, confirmed by loading properties without ever
    calling set_mapped_design()."""
    tab = PowerIntentTab()
    power_intent = build_mock_power_intent()

    tab.set_power_intent(power_intent)

    assert tab.properties._tables["PowerDomain"].rowCount() == len(power_intent.domains)


# --- layout: central Diagram QGroupBox + Hierarchy Tree/Properties docks ---


def test_layout_is_a_diagram_group_box_plus_tree_and_properties_docks(qtbot):
    """Explicit request: Hierarchy Tree and Power Intent Properties are
    QDockWidgets now (closable/reopenable, visible by default), not
    QGroupBoxes inside a QSplitter -- Power Diagram stays a QGroupBox,
    now as this tab's own central widget (this tab is a QMainWindow)."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.show()

    assert isinstance(tab.centralWidget(), QGroupBox)
    assert tab.centralWidget().title() == "Power Diagram"

    assert isinstance(tab.tree_dock, QDockWidget)
    assert tab.tree_dock.windowTitle() == "Hierarchy Tree"
    assert tab.tree_dock.isVisible() is True
    assert isinstance(tab.properties_dock, QDockWidget)
    assert tab.properties_dock.windowTitle() == "Properties"  # renamed from "Power Intent Properties" (explicit request)
    assert tab.properties_dock.isVisible() is True


def test_docks_can_be_closed_and_reopened(qtbot):
    """"So user can close or open" -- explicit request."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.show()

    tab.properties_dock.close()
    assert tab.properties_dock.isVisible() is False
    assert tab.tree_dock.isVisible() is True  # the other dock is unaffected

    tab.properties_dock.toggleViewAction().trigger()  # the same action the View menu uses
    assert tab.properties_dock.isVisible() is True


def test_floating_a_dock_gives_it_normal_window_min_max_close_buttons(qtbot):
    """Explicit request: a detached/floating Properties or Hierarchy Tree
    dock must be maximizable/minimizable, not stuck with QDockWidget's
    own default Qt.Tool floating window (small title bar, no min/max
    buttons)."""
    from PySide6.QtCore import Qt

    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.show()

    tab.properties_dock.setFloating(True)

    flags = tab.properties_dock.windowFlags()
    # Qt.WindowType's low bits encode the window *type* as a small
    # sub-mask (Tool = Popup|Dialog, which already includes Window's own
    # bit) -- comparing against WindowType_Mask is the only correct way
    # to confirm the type is plain Window, not e.g. still Tool.
    assert (int(flags) & int(Qt.WindowType.WindowType_Mask)) == int(Qt.WindowType.Window)
    assert bool(flags & Qt.WindowType.WindowMinimizeButtonHint) is True
    assert bool(flags & Qt.WindowType.WindowMaximizeButtonHint) is True
    assert bool(flags & Qt.WindowType.WindowCloseButtonHint) is True


def test_floating_dock_re_docks_cleanly_afterward(qtbot):
    """The Qt.Window override above must only apply while actually
    floating -- re-docking must genuinely re-embed the dock into the
    main window's layout, not leave it stuck as its own top-level
    window (Qt.Window forces a widget to stay top-level regardless of
    its Qt parent)."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.show()

    tab.tree_dock.setFloating(True)
    assert tab.tree_dock.isFloating() is True

    tab.tree_dock.setFloating(False)

    assert tab.tree_dock.isFloating() is False
    assert tab.tree_dock.isVisible() is True
    assert tab.tree_dock.isWindow() is False


def test_both_docks_can_float_and_re_dock_independently(qtbot):
    """Docking/undocking one dock must not disturb the other."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.show()

    tab.tree_dock.setFloating(True)
    tab.properties_dock.setFloating(True)
    tab.tree_dock.setFloating(False)

    assert tab.tree_dock.isFloating() is False
    assert tab.properties_dock.isFloating() is True

    tab.properties_dock.setFloating(False)
    assert tab.properties_dock.isFloating() is False


# --- Hierarchy Tree / Diagram feature parity with Visualize Design -------


def test_tree_and_diagram_share_the_same_hierarchy_selection_controller(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    assert tab.tree._controller is tab._hierarchy_controller
    assert tab.diagram._view._controller is tab._hierarchy_controller


def test_selecting_a_tree_item_highlights_the_matching_diagram_box(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())

    tab.tree._items_by_path["top/u_core"].setSelected(True)
    tab.tree._on_item_selected()  # setSelected() alone doesn't fire itemSelectionChanged headlessly

    box = tab.diagram._view._boxes["top/u_core"]
    assert box.isSelected() is True
    _deselect_and_collect(tab)


def test_hiding_a_tree_item_removes_its_box_from_the_diagram(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())

    tab.tree._toggle_hidden("top/u_core")

    assert "top/u_core" not in tab.diagram._view._boxes


def test_clear_button_unhides_everything_via_the_tree(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    tab.tree._toggle_hidden("top/u_core")
    assert "top/u_core" not in tab.diagram._view._boxes

    tab.clear_hidden_button.click()

    assert "top/u_core" in tab.diagram._view._boxes


def test_undo_shortcut_reverses_the_trees_last_hide(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    tab.tree._toggle_hidden("top/u_core")
    assert "top/u_core" not in tab.diagram._view._boxes

    tab.undo_shortcut.activated.emit()

    assert "top/u_core" in tab.diagram._view._boxes


def test_redo_shortcut_reapplies_the_undone_hide(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    tab.tree._toggle_hidden("top/u_core")
    tab.undo_shortcut.activated.emit()

    tab.redo_shortcut.activated.emit()

    assert "top/u_core" not in tab.diagram._view._boxes


def test_fit_view_button_is_wired_to_the_diagram(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())

    tab.fit_view_button.click()  # must not raise


def test_copy_path_is_available_on_the_diagram_just_like_visualize_design(qtbot):
    """Confirms the inner view really is a HierarchyDiagramView (not a
    lookalike) -- _copy_path/_hide_targets are inherited, not
    reimplemented."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())

    box = tab.diagram._view._boxes["top/u_core"]
    assert tab.diagram._view._hide_targets(box) == {"top/u_core"}


# --- "Design Properties" (moved here from the now-hidden Visualize -----
# --- Design tab, as Power Intent Properties' own first tab) -------------


def test_portlist_and_sequential_elements_are_the_properties_panels_first_two_tabs(qtbot):
    """Promoted individually (explicit request), not grouped under one
    "Design Properties" container tab anymore. Labels "Port List"/
    "Sequential" (renamed from "PortList"/"Sequential Elements" on a
    later tab rename/reorder pass)."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)

    assert tab.properties._tabs.tabText(0) == "Port List"
    assert tab.properties._tabs.widget(0) is tab.properties.design_properties._port_panel
    assert tab.properties._tabs.tabText(1) == "Sequential"
    assert tab.properties._tabs.widget(1) is tab.properties.design_properties._sequential_panel


def test_set_rtl_feeds_design_properties(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)

    tab.set_rtl(build_mock_design())

    tab.properties.design_properties.apply_selection({"top/u_core"})
    assert tab.properties.design_properties._port_panel.table.rowCount() > 0


def test_design_properties_shares_this_tabs_own_hierarchy_controller(qtbot):
    """A Tree click drives Design Properties' own selection too -- the
    same shared HierarchySelectionController the Tree/Diagram pair
    already use, exactly like Visualize Design's own (now-hidden) copy
    used to."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())

    tab.tree._items_by_path["top/u_core"].setSelected(True)
    tab.tree._on_item_selected()

    assert tab.properties.design_properties._port_panel.table.rowCount() > 0
    _deselect_and_collect(tab)


# --- Diagram's Filter button: placeholder only, no behavior wired --------


def test_diagram_filter_button_offers_the_expected_placeholder_categories(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)

    assert set(tab.diagram_filter_button._actions) == {
        "Supply Ports",
        "Supply Nets",
        "Power Domains",
        "Power Switches",
        "Isolations",
        "Retention",
        "Level Shifters",
    }


def test_diagram_filter_button_toggling_does_not_change_anything(qtbot):
    """Explicit request: these are placeholders, no filtering action
    wired up -- toggling a category must not raise and must not affect
    the diagram in any way."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    rtl = build_mock_design()
    power_intent = build_mock_power_intent()
    mapped = build_mock_mapped_design(power_intent, rtl)
    tab.set_rtl(rtl)
    tab.set_power_intent(power_intent)
    tab.set_mapped_design(mapped)
    box_count_before = len(tab.diagram._view._boxes)

    tab.diagram_filter_button._actions["Power Domains"].setChecked(False)

    assert len(tab.diagram._view._boxes) == box_count_before
    assert tab.diagram._stack.currentWidget() is tab.diagram._tabs
