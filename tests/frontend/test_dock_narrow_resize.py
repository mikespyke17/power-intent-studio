"""A dock containing a tab widget must be draggable narrow.

THE BUG, AND WHY EVERY EXISTING TEST MISSED IT. A `QTabBar`'s minimum
width depends on whether the style lets it show scroll arrows:

    style            usesScrollButtons   tab-bar minimum (9 tabs)
    Fusion / Windows        True              133 px
    macOS                   False             524 px

macOS sets `SH_TabBar_PreferNoArrows`, so Qt refuses the bar its scroll
buttons and makes its MINIMUM the width of every tab laid end to end.
The Properties dock then could not be dragged narrower than ~554 px --
it simply stopped, with nothing to indicate why, and the diagram could
not be given the space.

It never reproduced under Fusion, which is what the offscreen test
platform uses by default and what the README screenshots were captured
with. So these tests set the macOS style EXPLICITLY. Without that they
pass against the fix and against its absence equally, which is the
failure mode that let this ship.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QStyleFactory, QTabWidget, QWidget

from frontend.src.power_intent_view.power_intent_tab import PowerIntentTab
from frontend.src.shared.mock_backend import build_mock_design
from frontend.src.shared.widgets.shrinkable_tabs import allow_narrow_resize

# Wide enough that the tab bar, not the page content, is the constraint.
_MANY_TABS = ["Port List", "Sequential", "Supply Port", "Supply Set", "Power Domain",
              "Switch", "Isolation", "Retention", "Level Shifter"]


@pytest.fixture
def mac_style(qapp):
    """The macOS style, restored afterwards -- it is the only one that
    exhibits this, and it is a process-wide setting.

    SKIPS where that style does not exist (it ships only with Qt on
    macOS -- on Linux/Windows `QStyleFactory.create("macOS")` is None,
    which is why these used to FAIL there rather than test anything).
    Note the app itself no longer runs with the macOS style anywhere: it
    forces Fusion on every platform (theme.apply_fixed_appearance()), so
    this bug cannot occur in the shipped app. allow_narrow_resize() is
    kept as a guard, and these still pin it wherever macOS is available."""
    if "macos" not in (key.lower() for key in QStyleFactory.keys()):
        pytest.skip("the macOS style is only available on macOS")
    previous = qapp.style().objectName()
    qapp.setStyle(QStyleFactory.create("macOS"))
    yield
    qapp.setStyle(QStyleFactory.create(previous))


def _tabs(qtbot, labels=_MANY_TABS):
    widget = QTabWidget()
    for label in labels:
        widget.addTab(QWidget(), label)
    qtbot.addWidget(widget)
    return widget


def test_the_macos_style_is_what_makes_a_tab_bar_refuse_to_shrink(qtbot, mac_style):
    """The bug itself, pinned -- so that if a future Qt changes this
    behaviour, the reason these fixes exist is still on record rather
    than looking like superstition."""
    bare = _tabs(qtbot)

    assert bare.tabBar().usesScrollButtons() is False
    # The minimum is a large fraction of the FULL width, not a small
    # scrollable one -- that is the whole problem.
    assert bare.tabBar().minimumSizeHint().width() > 400


def test_allow_narrow_resize_restores_the_scroll_buttons_and_the_small_minimum(qtbot, mac_style):
    before = _tabs(qtbot)
    floor_before = before.tabBar().minimumSizeHint().width()

    after = allow_narrow_resize(_tabs(qtbot))

    assert after.tabBar().usesScrollButtons() is True
    assert after.tabBar().minimumSizeHint().width() < 200
    # A real improvement, not a rounding difference.
    assert after.tabBar().minimumSizeHint().width() < floor_before / 2


def test_the_fix_is_a_no_op_on_a_style_that_was_already_fine(qtbot, qapp):
    """Fusion already gives the bar its scroll buttons. The helper must
    not make anything worse there -- it is applied unconditionally."""
    qapp.setStyle(QStyleFactory.create("Fusion"))
    # Held in real locals: a chained `_tabs(qtbot).tabBar()` lets the
    # temporary QTabWidget be freed before the bar is read, which
    # surfaces as "Internal C++ object already deleted".
    plain = _tabs(qtbot)
    fixed = allow_narrow_resize(_tabs(qtbot))
    assert fixed.tabBar().minimumSizeHint().width() <= plain.tabBar().minimumSizeHint().width()


def test_the_properties_dock_can_be_dragged_narrow(qtbot, mac_style, monkeypatch):
    """The reported symptom, end to end: the Properties dock stopped
    resizing and the diagram could not be given the room."""
    def narrowest(with_fix):
        # The baseline is built with the helper NEUTRALISED, not undone
        # afterwards: it also relaxes minimumWidth, and those survive a
        # later setUsesScrollButtons(False) -- so "undoing" it measured
        # the fixed widget twice and compared equal.
        if not with_fix:
            for module in ("properties_panel", "power_intent_tab"):
                monkeypatch.setattr(
                    f"frontend.src.power_intent_view.{module}.allow_narrow_resize",
                    lambda widget: widget,
                )
        tab = PowerIntentTab()
        qtbot.addWidget(tab)
        tab.set_rtl(build_mock_design())
        tab.resize(1680, 1000)
        tab.show()
        tab.resizeDocks([tab.properties_dock], [150], Qt.Orientation.Horizontal)
        return tab, tab.properties_dock.width(), tab.diagram.width()

    _, without, diagram_without = narrowest(with_fix=False)
    monkeypatch.undo()
    tab, with_, diagram_with = narrowest(with_fix=True)

    # A before/after rather than a magic number: the page content
    # genuinely needs SOME width, so neither reaches 150 -- what matters
    # is that the tab bar is no longer what stops it.
    assert with_ < without, f"dock floor unchanged: {without} -> {with_}"
    assert without - with_ > 100, f"only {without - with_}px narrower"
    # ...and the space actually goes to the diagram.
    assert diagram_with > diagram_without
    assert tab.properties._tabs.tabBar().usesScrollButtons() is True


def test_the_hierarchy_dock_can_be_dragged_narrow_too(qtbot, mac_style):
    """Fewer tabs, so a smaller floor, but the same class of bug -- and
    it is equally draggable."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    tab.resize(1680, 1000)
    tab.show()

    tab.resizeDocks([tab.tree_dock], [120], Qt.Orientation.Horizontal)

    assert tab.tree_dock.width() < 300
    assert tab.tree_view_tabs.tabBar().usesScrollButtons() is True


def test_every_tab_stays_reachable_once_the_bar_is_scrollable(qtbot, mac_style):
    """Shrinking must hide tabs behind arrows, never drop them: the
    point is that the hidden ones remain reachable."""
    tabs = allow_narrow_resize(_tabs(qtbot))
    tabs.resize(160, 40)
    qtbot.addWidget(tabs)

    assert tabs.count() == len(_MANY_TABS)
    for index, label in enumerate(_MANY_TABS):
        tabs.setCurrentIndex(index)
        assert tabs.tabText(index) == label
        assert tabs.currentIndex() == index  # selectable even while scrolled out of view
