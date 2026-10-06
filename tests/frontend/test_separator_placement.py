"""Explicit follow-up request -- (a) tool palette separators reviewed
to exactly three points, (b) a separator added between "Add Isolation"
and "Add Level Shifter" in the diagram's own right-click context menu.
Both are pure UI reorganization, no functional/data changes -- see
diagram_tool_palette.py's own module docstring and
power_intent_diagram_view.py's own _extra_context_menu_actions()
docstring for the full "why"."""

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QMenu

from frontend.src.power_intent_view.diagram_tool_palette import DiagramToolPalette
from frontend.src.power_intent_view.power_domain_overlay import PowerDomainPlacement
from frontend.src.power_intent_view.power_intent_tab import PowerIntentTab
from frontend.src.shared.mock_backend import build_mock_design


# --- (a) tool palette: separators at exactly the three specified points -----


def test_a_tool_palette_separators_at_exactly_the_three_specified_points(qtbot):
    palette = DiagramToolPalette()
    qtbot.addWidget(palette)
    actions = palette.actions()

    assert sum(1 for a in actions if a.isSeparator()) == 3

    labelled = [(i, a.text()) for i, a in enumerate(actions) if not a.isSeparator()]

    def _index_of(label: str) -> int:
        return next(i for i, text in labelled if text == label)

    # Point 1: between Zoom Lens and Supply Port.
    assert actions[_index_of("Zoom Lens") + 1].isSeparator()
    assert actions[_index_of("Zoom Lens") + 2].text() == "Supply Port"
    # Point 2: between Supply Net and Power Switch.
    assert actions[_index_of("Supply Net") + 1].isSeparator()
    assert actions[_index_of("Supply Net") + 2].text() == "Power Switch"
    # Point 3: between Power Switch and Isolation Cell.
    assert actions[_index_of("Power Switch") + 1].isSeparator()
    assert actions[_index_of("Power Switch") + 2].text() == "Isolation Cell"

    # Tool order itself is unchanged (explicit constraint: "without
    # otherwise reordering the existing tools").
    assert [text for _i, text in labelled] == [
        "Regular Cursor",
        "Pan",
        "Zoom Lens",
        "Supply Port",
        "Internal Supply",
        "Virtual Supply",
        "Supply Net",
        "Power Switch",
        "Isolation Cell",
        "Level Shifter",
        "Retention",
    ]


def test_a_no_separator_between_level_shifter_and_retention_anymore(qtbot):
    """The old separator (Level Shifter|Retention) is not one of the
    three specified points -- confirms it was removed, not just that
    three separators exist somewhere."""
    palette = DiagramToolPalette()
    qtbot.addWidget(palette)
    actions = palette.actions()
    level_shifter_index = next(i for i, a in enumerate(actions) if not a.isSeparator() and a.text() == "Level Shifter")

    assert actions[level_shifter_index + 1].isSeparator() is False
    assert actions[level_shifter_index + 1].text() == "Retention"


# --- (b) right-click menu: separator between Add Isolation/Add Level Shifter


def test_b_right_click_menu_has_a_separator_between_add_isolation_and_add_level_shifter(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view._domain_state.domains["d1"] = PowerDomainPlacement(id="d1", owner_path="top", name="PD_TOP", color=QColor("#ff0000"))

    menu = QMenu()
    view._extra_context_menu_actions(menu, view._boxes["top"])
    actions = menu.actions()
    isolation_index = next(i for i, a in enumerate(actions) if not a.isSeparator() and a.text() == "Add Isolation")

    assert actions[isolation_index + 1].isSeparator() is True
    assert actions[isolation_index + 2].text() == "Add Level Shifter"
