"""Bidirectional selection sync between the power-intent diagram and
properties panel -- against mock/fixture data only, no backend/ import.
"""

import gc

from PySide6.QtGui import QUndoStack

from frontend.src.hierarchy_view.selection_controller import HierarchySelectionController
from frontend.src.power_intent_view.power_intent_diagram_view import PowerIntentDiagramView
from frontend.src.power_intent_view.properties_panel import PowerIntentPropertiesPanel
from frontend.src.power_intent_view.selection_controller import PowerIntentSelectionController
from frontend.src.shared.mock_backend import build_mock_design, build_mock_mapped_design, build_mock_power_intent


def _wired(qtbot):
    controller = PowerIntentSelectionController()
    diagram = PowerIntentDiagramView(HierarchySelectionController(), controller, QUndoStack())
    qtbot.addWidget(diagram)
    properties = PowerIntentPropertiesPanel(controller)
    rtl = build_mock_design()
    power_intent = build_mock_power_intent()
    mapped = build_mock_mapped_design(power_intent, rtl)
    properties.load(power_intent)
    diagram.set_mapped_design(mapped)
    return controller, diagram, properties


def _deselect_and_collect(diagram: PowerIntentDiagramView) -> None:
    """Same segfault hazard, same fix, as
    hierarchy_view/hierarchy_diagram_view.py's own _deselect_and_collect()
    helper in test_hierarchy_tree_diagram_sync.py -- the power-intent
    diagram's inner view now subclasses HierarchyDiagramView, so it
    inherits the same "selected item + scene teardown mid-GC-cycle" risk.
    Call at the end of any test that leaves an item selected
    (setSelected(True))."""
    if diagram._view.scene() is not None:
        diagram._view.scene().clearSelection()
    gc.collect()


def test_selecting_a_properties_row_highlights_the_diagram_item(qtbot):
    controller, diagram, properties = _wired(qtbot)

    controller.selectionChanged.emit("IsolationStrategy", "::ISO1")  # simulates the row-click event properties itself would emit

    assert diagram._highlighted == diagram._index[("IsolationStrategy", "::ISO1")]


def test_selecting_a_diagram_item_switches_properties_to_the_right_tab_and_row(qtbot):
    controller, diagram, properties = _wired(qtbot)
    switch_icon = diagram._index[("PowerSwitch", "::SW_CORE")][0]

    switch_icon.setSelected(True)

    assert properties._kind_for_tab[properties._tabs.currentIndex()] == "PowerSwitch"
    selected_rows = properties._tables["PowerSwitch"].selectionModel().selectedRows()
    assert len(selected_rows) == 1
    _deselect_and_collect(diagram)


def test_selecting_a_domain_highlights_both_region_and_member_boxes(qtbot):
    controller, diagram, properties = _wired(qtbot)

    controller.selectionChanged.emit("PowerDomain", "::PD_CORE")

    region, *members = diagram._index[("PowerDomain", "::PD_CORE")]
    assert diagram._highlighted[0] is region
    assert len(members) >= 1
    assert all(m in diagram._highlighted for m in members)


def test_no_infinite_loop_between_diagram_and_properties(qtbot):
    """The hub-and-spoke design's whole point: setSelection() must never
    cause a re-emit. Directly counts controller emissions across one
    round trip -- exactly one for the triggering action, zero more.
    """
    controller, diagram, properties = _wired(qtbot)
    emissions = []
    controller.selectionChanged.connect(lambda k, i: emissions.append((k, i)))

    properties.setSelection("RetentionStrategy", "::RET1")
    assert emissions == []  # setSelection() itself never emits

    switch_icon = diagram._index[("PowerSwitch", "::SW_CORE")][0]
    switch_icon.setSelected(True)
    assert emissions == [("PowerSwitch", "::SW_CORE")]  # exactly one emission, not a cascade
    _deselect_and_collect(diagram)


def test_selecting_an_unknown_construct_clears_highlight_without_error(qtbot):
    controller, diagram, properties = _wired(qtbot)
    controller.selectionChanged.emit("PowerDomain", "::PD_CORE")
    assert diagram._highlighted != []

    diagram.setSelection("PowerDomain", "::DOES_NOT_EXIST")

    assert diagram._highlighted == []
