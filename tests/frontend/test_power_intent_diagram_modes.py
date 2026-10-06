"""power_intent_view/power_intent_diagram_view.py -- DiagramMode routing
(explicit request: a grouped tool palette whose modes the Diagram View
itself owns the actual mouse-event handling for). These focus on the
five modes each getting their own cursor (scoped to the viewport, not
the outer view widget), plus each mode's own canvas behavior: Pan
(click-drag-to-pan, moved here from being Regular Cursor's own default
behavior -- a follow-up correcting an earlier "OpenHandCursor mixup"),
Zoom Lens (rubber-band zoom in via left-click-drag, zoom out via
right-click-drag), Supply Port (hover+click placement directly on the
canvas -- an earlier drag-and-drop mechanism was explicitly removed as
unreliable), Supply Net (the connector-handle drag gesture, explicitly
gated on this mode, plus hover feedback), and the global Escape-key
reset."""

import gc

import pytest
from PySide6.QtCore import QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog, QGraphicsRectItem, QMessageBox

from frontend.src.power_intent_view.add_power_supply_dialog import AddPowerSupplyDialog
from frontend.src.power_intent_view.diagram_tool_palette import DiagramMode
from frontend.src.power_intent_view.power_intent_diagram_view import _BoxHighlightOverlay
from frontend.src.power_intent_view.power_intent_tab import PowerIntentTab
from frontend.src.power_intent_view.retention_dialog import RetentionStrategyDialog
from frontend.src.power_intent_view.supply_port_overlay import HIGHLIGHT_COLOR
from frontend.src.shared.mock_backend import build_mock_design
from frontend.src.shared.models.rtl import Definition, Design, Instance, SourceLanguage
from frontend.src.shared.widgets import message_box


def _deselect_and_collect(tab: PowerIntentTab) -> None:
    if tab.diagram._view.scene() is not None:
        tab.diagram._view.scene().clearSelection()
    gc.collect()


def _viewport_pos(view, item):
    return view.mapFromScene(item.scene_center() if hasattr(item, "scene_center") else item.scenePos())


def _own_region_point(view, box):
    """A viewport point inside `box`'s own "exclusive" region -- inside
    its rect, but above/outside any child box packed within it -- same
    convention test_hover_tracks_into_and_back_out_of_nested_blocks
    already established (its own top-edge-plus-a-few-pixels offset).
    Needed for Supply Port/Internal Supply hover tests once a box has
    children: a bare sceneBoundingRect().center() often lands inside a
    packed child instead of the box itself, which _box_at() (deepest-
    item-wins) would then resolve to."""
    rect = box.sceneBoundingRect()
    return view.mapFromScene(QPointF(rect.center().x(), rect.top() + 5))


def _make_view_actually_visible(tab: PowerIntentTab) -> None:
    """Real hover delivery (QTest.mouseMove with no button held, unlike
    mousePress/mouseRelease which reach mouseMoveEvent() via sendEvent()
    regardless) needs the target widget genuinely visible *and* the item
    being hovered actually within the current viewport bounds -- a real
    gotcha, found by a live repro: set_rtl() alone leaves
    PowerIntentDiagramView's own QStackedWidget showing the placeholder
    label (see its own docstring), not the real tabs/view at all, and
    without fit_view() the default scene rect can easily place a freshly
    added supply port's own handle outside the (in a test window) quite
    narrow viewport -- QTest.mouseMove() silently delivers nothing in
    either case, no error, just never invokes mouseMoveEvent()."""
    tab.diagram._stack.setCurrentWidget(tab.diagram._tabs)
    QTest.qWait(0)
    tab.diagram._view.fit_view()


def _fake_add_power_supply_exec(name: str, color: QColor, accept: bool = True):
    """Same stand-in-for-a-native-modal pattern already used throughout
    this codebase (see test_power_domain.py's own copy) -- AddPowerSupplyDialog.exec()
    can't be driven headlessly."""

    def fake_exec(self):
        if accept:
            self.name_edit.setText(name)
            self.color = QColor(color)
        return QDialog.DialogCode.Accepted if accept else QDialog.DialogCode.Rejected

    return fake_exec


# --- cursor per mode ------------------------------------------------------


def test_select_mode_uses_the_plain_arrow_cursor(qtbot):
    """Explicit request: "Regular Cursor mode -> normal arrow cursor
    (default)" -- supersedes this view's own earlier open-hand
    pan-affordance cursor for this mode."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    view = tab.diagram._view

    view.set_mode(DiagramMode.SELECT)

    assert view.viewport().cursor().shape() == Qt.CursorShape.ArrowCursor


def test_pan_mode_uses_the_stock_open_hand_cursor(qtbot):
    """Explicit request: Pan's own cursor is Qt.CursorShape.OpenHandCursor,
    "exactly as Regular Cursor mode used before this tool palette
    existed" -- a stock shape, not a custom pixmap cursor the way every
    other mode's own is."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    view = tab.diagram._view

    view.set_mode(DiagramMode.PAN)

    assert view.viewport().cursor().shape() == Qt.CursorShape.OpenHandCursor


def test_zoom_lens_mode_uses_a_custom_lens_cursor(qtbot):
    """Explicit request: the cursor itself becomes lens-shaped (a real
    QCursor built from the magnifying-glass glyph), not a stock shape."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    view = tab.diagram._view

    view.set_mode(DiagramMode.ZOOM_LENS)

    assert view.viewport().cursor().shape() == Qt.CursorShape.BitmapCursor
    assert not view.viewport().cursor().pixmap().isNull()


def test_supply_port_and_supply_net_modes_use_their_own_custom_cursors(qtbot):
    """Explicit request: each tool gets its own distinct cursor shape,
    not a shared crosshair."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    view = tab.diagram._view

    view.set_mode(DiagramMode.SUPPLY_PORT)
    assert view.viewport().cursor().shape() == Qt.CursorShape.BitmapCursor
    supply_port_pixmap = view.viewport().cursor().pixmap()
    assert not supply_port_pixmap.isNull()

    view.set_mode(DiagramMode.SUPPLY_NET)
    assert view.viewport().cursor().shape() == Qt.CursorShape.BitmapCursor
    supply_net_pixmap = view.viewport().cursor().pixmap()
    assert not supply_net_pixmap.isNull()
    assert supply_net_pixmap.toImage() != supply_port_pixmap.toImage()  # genuinely different glyphs


def test_retention_mode_uses_its_own_custom_cursor(qtbot):
    """Explicit request: Retention gets its own distinct cursor shape,
    built from the same already-provided retention-flop.svg asset the
    tool palette's own button icon uses -- not a shared crosshair, and
    not the same glyph as any other mode."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    view = tab.diagram._view

    view.set_mode(DiagramMode.SUPPLY_PORT)
    supply_port_pixmap = view.viewport().cursor().pixmap()

    view.set_mode(DiagramMode.RETENTION)

    assert view.viewport().cursor().shape() == Qt.CursorShape.BitmapCursor
    retention_pixmap = view.viewport().cursor().pixmap()
    assert not retention_pixmap.isNull()
    assert retention_pixmap.toImage() != supply_port_pixmap.toImage()


def test_switching_back_to_select_restores_the_arrow_cursor(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    view = tab.diagram._view
    view.set_mode(DiagramMode.ZOOM_LENS)

    view.set_mode(DiagramMode.SELECT)

    assert view.viewport().cursor().shape() == Qt.CursorShape.ArrowCursor


def test_custom_cursor_never_leaks_onto_the_outer_view_widget(qtbot):
    """Explicit request: the custom cursor must apply ONLY while inside
    the Diagram View's canvas area -- confirmed by checking the OUTER
    QGraphicsView widget's own cursor (as opposed to its viewport's)
    never picks up the per-tool override, since set_mode() only ever
    calls `self.viewport().setCursor(...)`, never `self.setCursor(...)`."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    view = tab.diagram._view

    view.set_mode(DiagramMode.ZOOM_LENS)

    assert view.cursor().shape() != Qt.CursorShape.BitmapCursor


# --- toolbar <-> diagram wiring (power_intent_tab.py) ----------------------


def test_tool_palette_mode_change_propagates_to_the_diagram(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)

    tab.tool_palette.zoom_lens_action.setChecked(True)

    assert tab.diagram._view.viewport().cursor().shape() == Qt.CursorShape.BitmapCursor


def test_mode_propagates_to_a_sub_hierarchy_tab_opened_after_the_change(qtbot):
    """A tab opened *after* a mode change must still start in that mode,
    not always reset to Select -- PowerIntentDiagramView's own
    view_factory applies the current mode to every freshly constructed
    view (see its own docstring)."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    tab.tool_palette.zoom_lens_action.setChecked(True)

    root_view = tab.diagram._tabs.current_view
    tab.diagram.open_path("top/u_core")

    new_view = tab.diagram._tabs.current_view
    assert new_view is not root_view  # a genuinely new view, not the root tab reused
    assert new_view.viewport().cursor().shape() == Qt.CursorShape.BitmapCursor


# --- Pan mode: click-drag pans the view; Regular Cursor no longer does -----
# Explicit request, a follow-up correcting an earlier "OpenHandCursor mixup"
# that had briefly left pan-on-drag bundled into Regular Cursor/Select's own
# default behavior: pan-on-drag now belongs exclusively to a dedicated Pan
# tool, gated via HierarchyDiagramView's own _pan_enabled() hook (see that
# class's own module for the mechanism -- the actual pan-drag machinery
# itself is untouched/reused, not reimplemented).


def test_pan_mode_click_drag_pans_the_view(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.resize(900, 700)
    tab.show()
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view.set_mode(DiagramMode.PAN)

    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(50, 50))
    QTest.mouseMove(view.viewport(), QPoint(80, 80))

    assert view._panning is True
    assert view.viewport().cursor().shape() == Qt.CursorShape.ClosedHandCursor  # redirected via _set_own_cursor()

    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(80, 80))
    assert view._panning is False
    assert view.viewport().cursor().shape() == Qt.CursorShape.OpenHandCursor
    _deselect_and_collect(tab)


def test_select_mode_click_drag_does_not_pan(qtbot):
    """Explicit request: "Regular Cursor mode goes back to being
    select/inspect only... no pan-on-drag" -- the exact same drag that
    pans in Pan mode above must NOT pan here."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.resize(900, 700)
    tab.show()
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view.set_mode(DiagramMode.SELECT)

    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(50, 50))
    QTest.mouseMove(view.viewport(), QPoint(80, 80))

    assert view._panning is False
    assert view._pan_start is None

    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(80, 80))
    assert view.viewport().cursor().shape() == Qt.CursorShape.ArrowCursor  # regression: used to reset to OpenHandCursor
    _deselect_and_collect(tab)


@pytest.mark.parametrize(
    "action_name,expected_shape",
    [
        ("select_action", Qt.CursorShape.ArrowCursor),
        ("zoom_lens_action", Qt.CursorShape.BitmapCursor),
        ("supply_port_action", Qt.CursorShape.BitmapCursor),
        ("supply_net_action", Qt.CursorShape.BitmapCursor),
        ("power_switch_action", Qt.CursorShape.BitmapCursor),
        ("retention_action", Qt.CursorShape.BitmapCursor),
    ],
)
def test_cursor_survives_a_click_release_outside_pan_mode(qtbot, action_name, expected_shape):
    """Real, confirmed bug: HierarchyDiagramView's own mouseReleaseEvent()
    used to reset the cursor to OpenHandCursor on *every* left-button
    release unconditionally, regardless of _pan_enabled() -- so clicking
    anywhere on the canvas in any mode other than Pan would stomp the
    correct per-tool cursor set_mode() had already applied. Now gated
    behind _pan_enabled(), same as the pan-arming check in
    mousePressEvent()."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.resize(900, 700)
    tab.show()
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    getattr(tab.tool_palette, action_name).setChecked(True)
    cursor_before = view.viewport().cursor()

    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(50, 50))
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(50, 50))

    assert view.viewport().cursor().shape() == expected_shape
    if expected_shape == Qt.CursorShape.BitmapCursor:
        assert view.viewport().cursor().pixmap().toImage() == cursor_before.pixmap().toImage()
    _deselect_and_collect(tab)


@pytest.mark.parametrize(
    "action_name",
    ["select_action", "pan_action", "zoom_lens_action", "supply_port_action", "supply_net_action", "power_switch_action", "retention_action"],
)
def test_drag_mode_is_always_no_drag(qtbot, action_name):
    """Explicit request: confirms Qt's own built-in ScrollHandDrag is
    never used (this codebase deliberately hand-rolls pan instead --
    see hierarchy_diagram_view.py's own module docstring for why:
    ScrollHandDrag would swallow every left-click and break box-click
    selection). True for all 6 modes, including Pan -- Pan's own
    click-drag-to-pan is the hand-rolled mousePressEvent()/
    mouseMoveEvent()/mouseReleaseEvent() gesture, gated via
    _pan_enabled(), not Qt's built-in drag mode."""
    from PySide6.QtWidgets import QGraphicsView

    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    getattr(tab.tool_palette, action_name).setChecked(True)

    assert view.dragMode() == QGraphicsView.DragMode.NoDrag
    _deselect_and_collect(tab)


def test_pan_mode_stays_active_after_a_pan(qtbot):
    """Same "don't auto-switch back to Select" precedent Zoom Lens/Supply
    Port already follow."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.resize(900, 700)
    tab.show()
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view.set_mode(DiagramMode.PAN)

    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(50, 50))
    QTest.mouseMove(view.viewport(), QPoint(80, 80))
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(80, 80))

    assert view._mode == DiagramMode.PAN
    _deselect_and_collect(tab)


def test_switching_away_from_pan_mid_drag_aborts_it(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.resize(900, 700)
    tab.show()
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view.set_mode(DiagramMode.PAN)
    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(50, 50))
    QTest.mouseMove(view.viewport(), QPoint(80, 80))
    assert view._panning is True

    view.set_mode(DiagramMode.SELECT)

    assert view._panning is False
    assert view._pan_start is None
    _deselect_and_collect(tab)


def test_escape_cancels_an_in_progress_pan(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.resize(900, 700)
    tab.show()
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    tab.tool_palette.pan_action.setChecked(True)
    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(50, 50))
    QTest.mouseMove(view.viewport(), QPoint(80, 80))
    assert view._panning is True

    QTest.keyClick(view, Qt.Key.Key_Escape)

    assert view._panning is False
    assert view._pan_start is None
    assert view._mode == DiagramMode.SELECT
    assert tab.tool_palette.select_action.isChecked() is True
    _deselect_and_collect(tab)


# --- Zoom Lens: rubber-band zoom in (left-click-drag) -----------------------


def test_rubber_band_drag_zooms_into_the_selected_scene_rect(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.resize(900, 700)
    tab.show()
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view.set_mode(DiagramMode.ZOOM_LENS)
    scale_before = view.transform().m11()

    start = QPoint(20, 20)
    end = QPoint(120, 120)
    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
    assert view._rubber_band is not None
    QTest.mouseMove(view.viewport(), end)
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, end)

    assert view._rubber_band is None  # cleaned up
    assert view.transform().m11() > scale_before  # the view actually zoomed IN
    _deselect_and_collect(tab)


def test_rubber_band_stays_in_zoom_lens_mode_after_zooming(qtbot):
    """Explicit request: no auto-switch back to Regular Cursor."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.resize(900, 700)
    tab.show()
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view.set_mode(DiagramMode.ZOOM_LENS)

    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(20, 20))
    QTest.mouseMove(view.viewport(), QPoint(120, 120))
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(120, 120))

    assert view._mode == DiagramMode.ZOOM_LENS
    assert view.viewport().cursor().shape() == Qt.CursorShape.BitmapCursor
    _deselect_and_collect(tab)


def test_a_tiny_left_drag_below_the_click_threshold_does_not_zoom(qtbot):
    """Explicit request: a drag smaller than ~5px is basically a click,
    not a zoom -- a no-op instead of zooming to a tiny rect."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.resize(900, 700)
    tab.show()
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view.set_mode(DiagramMode.ZOOM_LENS)
    scale_before = view.transform().m11()

    start = QPoint(50, 50)
    end = QPoint(52, 51)  # well under the 5px threshold
    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
    QTest.mouseMove(view.viewport(), end)
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, end)

    assert view.transform().m11() == scale_before  # unchanged -- no zoom applied
    _deselect_and_collect(tab)


# --- Zoom Lens: rubber-band zoom out (right-click-drag) ---------------------


def test_right_click_drag_zooms_out(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.resize(900, 700)
    tab.show()
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view.set_mode(DiagramMode.ZOOM_LENS)
    # Zoom in first so there's real room to zoom back out.
    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(20, 20))
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(400, 400))
    scale_after_zoom_in = view.transform().m11()

    start = QPoint(50, 50)
    end = QPoint(400, 400)
    QTest.mousePress(view.viewport(), Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier, start)
    assert view._rubber_band is not None
    QTest.mouseMove(view.viewport(), end)
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier, end)

    assert view._rubber_band is None
    assert view.transform().m11() < scale_after_zoom_in  # the view actually zoomed OUT
    _deselect_and_collect(tab)


def test_right_click_drag_stays_in_zoom_lens_mode(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.resize(900, 700)
    tab.show()
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view.set_mode(DiagramMode.ZOOM_LENS)

    QTest.mousePress(view.viewport(), Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier, QPoint(50, 50))
    QTest.mouseMove(view.viewport(), QPoint(400, 400))
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier, QPoint(400, 400))

    assert view._mode == DiagramMode.ZOOM_LENS
    _deselect_and_collect(tab)


def test_a_tiny_right_drag_below_the_click_threshold_does_not_zoom_out(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.resize(900, 700)
    tab.show()
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view.set_mode(DiagramMode.ZOOM_LENS)
    scale_before = view.transform().m11()

    start = QPoint(50, 50)
    end = QPoint(52, 51)  # well under the 5px threshold
    QTest.mousePress(view.viewport(), Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier, start)
    QTest.mouseMove(view.viewport(), end)
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier, end)

    assert view.transform().m11() == scale_before
    _deselect_and_collect(tab)


def test_right_click_drag_does_not_open_the_context_menu(qtbot):
    """Explicit request: right-click-drag is repurposed for zoom-out in
    this mode -- the pre-existing Copy Path/Hide/Add Power Supply popup
    must not also appear."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.resize(900, 700)
    tab.show()
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view.set_mode(DiagramMode.ZOOM_LENS)

    # _on_context_menu() is the slot customContextMenuRequested drives --
    # calling it directly is how the base class's own tests already probe
    # this without needing a real, headlessly-undrivable menu.exec().
    box = view._boxes["top/u_core"]
    pos = _own_region_point(view, box)
    view._on_context_menu(pos)  # must be a silent no-op, not raise/pop anything
    _deselect_and_collect(tab)


def test_zoom_scale_is_clamped_to_the_max(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.resize(900, 700)
    tab.show()
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view.set_mode(DiagramMode.ZOOM_LENS)

    # A tiny rect zoomed in as far as it can go, repeated -- should clamp
    # rather than run away to an unbounded scale.
    for _ in range(6):
        QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(50, 50))
        QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(60, 60))

    assert view.transform().m11() <= view._MAX_ZOOM_SCALE + 1e-6
    _deselect_and_collect(tab)


def _three_level_nested_design() -> Design:
    """outer -> middle -> inner, a single-child chain three levels deep
    -- same shape/pattern as test_hierarchy_scene_builder.py's own
    _single_child_chain_design() (bank -> wrap -> macro), reused here
    since build_mock_design() only ever nests 2 levels (a flat "top"
    with several direct children, no grandchildren) -- not deep enough
    to exercise Bug A (Supply Port hover getting stuck on an outer
    block instead of tracking into a nested child underneath it)."""
    inner = Definition(definition_id="inner", base_name="inner_def", language=SourceLanguage.SYSTEMVERILOG)
    middle = Definition(
        definition_id="middle",
        base_name="middle_def",
        language=SourceLanguage.SYSTEMVERILOG,
        instances=[Instance(name="u_inner", definition_id="inner")],
    )
    outer = Definition(
        definition_id="outer",
        base_name="outer_def",
        language=SourceLanguage.SYSTEMVERILOG,
        instances=[Instance(name="u_middle", definition_id="middle")],
    )
    return Design(definitions={"inner": inner, "middle": middle, "outer": outer}, top="outer")


# --- Supply Port mode: hover+click placement on the canvas -----------------
# Explicit request, superseding an earlier drag-and-drop mechanism ("REMOVE
# the drag-and-drop placement mechanism entirely -- it is not working
# reliably"). Hover driven via _update_supply_port_hover() directly --
# same "directly-testable core" precedent _update_supply_net_hover() already
# established in this file (QTest.mouseMove()'s own "only fires if the
# *global* cursor position actually changed" dedup makes it unreliable
# across back-to-back tests under the offscreen platform); click driven via
# real QTest.mousePress()/mouseRelease() to also exercise mousePressEvent()'s
# own DiagramMode.SUPPLY_PORT branch end-to-end.


def test_hover_tracks_into_and_back_out_of_nested_blocks(qtbot):
    """Real, confirmed bug: once _BoxHighlightOverlay highlighted an
    *outer* block, hover got permanently stuck there and never tracked
    into a nested child underneath the cursor -- root cause was the
    overlay's own default shape() covering its whole rect (not just the
    drawn border) at a far higher Z than any InstanceBoxItem, so it
    shadowed nested siblings for itemAt() hit-testing entirely (see
    _BoxHighlightOverlay's own docstring). Drives 3 levels deep
    (outer -> middle -> inner) and back out, confirming the hover
    highlight always tracks to the DEEPEST block actually containing
    the cursor at each step, not whichever was highlighted first.

    Driven via DiagramMode.RETENTION (not Supply Port): this is a
    generic _BoxHighlightOverlay/hit-testing test, not one about Supply
    Port's own business rules -- Retention's own hover has no
    hierarchy-level restriction (Supply Port's own hover now only ever
    lights up the design's true top-level block, "outer_def" here, so
    it can no longer exercise the middle/inner tracking this test is
    actually about -- see _validate_supply_port_target())."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.resize(900, 700)
    tab.show()
    tab.set_rtl(_three_level_nested_design())
    view = tab.diagram._view
    view.set_mode(DiagramMode.RETENTION)
    outer = view._boxes["outer_def"]
    middle = view._boxes["outer_def/u_middle"]
    inner = view._boxes["outer_def/u_middle/u_inner"]

    outer_rect = outer.sceneBoundingRect()
    middle_rect = middle.sceneBoundingRect()
    inner_rect = inner.sceneBoundingRect()
    # Each point sits in that box's own "exclusive" region -- inside it,
    # but above/outside the next-deeper box's own rect -- except the
    # innermost point, which is simply its center.
    outer_only = view.mapFromScene(QPointF(outer_rect.center().x(), outer_rect.top() + 5))
    middle_only = view.mapFromScene(QPointF(middle_rect.center().x(), middle_rect.top() + 5))
    inner_point = view.mapFromScene(inner_rect.center())
    assert outer_rect.top() < middle_rect.top() < inner_rect.top()  # sanity-check the fixture's own nesting/packing

    view._update_retention_hover(outer_only)
    assert view._retention_hover_box is outer

    view._update_retention_hover(middle_only)
    assert view._retention_hover_box is middle  # not stuck on outer

    view._update_retention_hover(inner_point)
    assert view._retention_hover_box is inner  # tracks all the way into the deepest child

    view._update_retention_hover(middle_only)
    assert view._retention_hover_box is middle  # and back out again

    view._update_retention_hover(outer_only)
    assert view._retention_hover_box is outer
    _deselect_and_collect(tab)


def test_supply_port_on_a_macro_is_rejected_with_an_error(qtbot, monkeypatch):
    """Explicit request (reversing an earlier explicit request that
    Supply Port must work on any block, macros included -- see
    _MACRO_SUPPLY_PORT_ERROR's own docstring): a macro's own pins are
    already fixed by its Liberty (.lib) definition, so a user-placed
    supply port is no longer allowed on one at all -- clicking a macro
    in Supply Port mode must show an error and add nothing. The dialog
    itself must never even open (see _prompt_add_power_supply()'s own
    docstring on why looping it back open would be pointless for a
    target that rejects every possible name) -- monkeypatching
    AddPowerSupplyDialog.exec() to fail the test if called is a
    stronger check than just asserting on the resulting icon count.
    QMessageBox.warning() itself is also monkeypatched -- a real,
    unmocked call blocks forever waiting for a user click that can
    never come under a headless test run (confirmed live: an earlier,
    unmocked diagnostic script hung indefinitely on exactly this).

    Hover highlight superseded on a later follow-up (Internal Supply):
    an invalid target (macro, or -- new -- the wrong hierarchy level)
    now never highlights at all (see _validate_supply_port_target()),
    which this test's own fixture ("top/u_ram") happens to fail for
    both reasons at once (a macro, and not the design's own top-level
    block) -- either alone would already produce no highlight."""
    dialog_opened = []
    monkeypatch.setattr(AddPowerSupplyDialog, "exec", lambda self: dialog_opened.append(1) or QDialog.DialogCode.Rejected)
    warnings = []
    monkeypatch.setattr(message_box, "warning", lambda parent, title, text: warnings.append((title, text)))
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.resize(900, 700)
    tab.show()
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view.set_mode(DiagramMode.SUPPLY_PORT)
    macro = view._boxes["top/u_ram"]
    assert macro.definition.is_black_box
    count_before = len(view._supply_port_icons)
    pos = view.mapFromScene(macro.sceneBoundingRect().center())

    view._update_supply_port_hover(pos)
    assert view._supply_port_hover_box is None  # invalid target -- no highlight

    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, pos)
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, pos)

    assert dialog_opened == []  # never even prompted for a name/colour
    assert len(warnings) == 1
    assert len(view._supply_port_icons) == count_before  # nothing added
    _deselect_and_collect(tab)


def test_add_supply_port_raises_for_a_macro_even_bypassing_the_dialog(qtbot):
    """The directly-testable core (add_supply_port()) rejects a macro
    target on its own, structurally -- a second, independent layer of
    the same rule _prompt_add_power_supply() already enforces before
    ever opening the dialog, protecting any other/future caller that
    bypasses that dialog-level check."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    macro = view._boxes["top/u_ram"]

    with pytest.raises(ValueError, match="macro"):
        view.add_supply_port(macro, "SHOULD_NOT_WORK", QColor("#123456"))
    _deselect_and_collect(tab)


def test_supply_port_rejection_on_a_macro_still_works_when_shadowed_by_an_unparented_overlay_item(qtbot, monkeypatch):
    """Real, confirmed root cause (of a DIFFERENT, now-fixed bug, kept
    here since it's still a real risk this new macro-rejection rule
    could silently regress): power_intent_view's own Milestone-2
    overlay items (SwitchIconItem/SupplyRailItem in diagram_overlay.py)
    are bare top-level scene items with no parent at all
    (scene.addItem(...), never setParentItem()), at a Z-value above
    InstanceBoxItem's own -- a power switch icon sits at a domain's own
    *region* top edge, which can genuinely coincide with a member box
    underneath it, macros included (a macro is exactly the kind of
    instance a real design puts in a switched domain). The base class's
    own _box_at() used to give up entirely (return None) whenever the
    single topmost item at a point had no parent to walk up to, even
    though the macro's own box was directly underneath -- see
    hierarchy_diagram_view.py's own _box_at() docstring for the full
    root-cause writeup. Confirms the hit-testing fix and the newer
    macro-rejection rule compose correctly: the error is reached
    *because* the macro is correctly resolved despite the overlay in
    front, not skipped by accident due to a hit-test miss."""
    warnings = []
    monkeypatch.setattr(message_box, "warning", lambda parent, title, text: warnings.append((title, text)))
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.resize(900, 700)
    tab.show()
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    macro = view._boxes["top/u_ram"]
    rect = macro.sceneBoundingRect()
    unparented_overlay = QGraphicsRectItem(QRectF(rect.center().x() - 5, rect.center().y() - 5, 10, 10))
    unparented_overlay.setZValue(3)
    view.scene().addItem(unparented_overlay)
    view.set_mode(DiagramMode.SUPPLY_PORT)
    count_before = len(view._supply_port_icons)
    pos = view.mapFromScene(rect.center())

    # _box_at() checked directly (not the hover highlight, which -- on a
    # later follow-up, Internal Supply -- no longer lights up for an
    # invalid target at all, see _update_supply_port_hover()): confirms
    # resolution reaches the real macro despite the overlay in front,
    # not stuck at None because of it.
    assert view._box_at(pos) is macro

    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, pos)
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, pos)

    assert len(warnings) == 1  # correctly reached the macro, correctly rejected it
    assert len(view._supply_port_icons) == count_before
    _deselect_and_collect(tab)


def test_hover_over_a_box_shows_the_fluorescent_highlight(qtbot):
    """Uses "top" (the design's own true top-level block), not
    "top/u_core" -- Supply Port's own hover only ever highlights a
    valid target now, and "top/u_core" is no longer one (see
    _validate_supply_port_target())."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.resize(900, 700)
    tab.show()
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view.set_mode(DiagramMode.SUPPLY_PORT)
    box = view._boxes["top"]
    pos = _own_region_point(view, box)

    view._update_supply_port_hover(pos)

    assert view._supply_port_hover_box is box
    assert isinstance(view._box_highlight_overlay, _BoxHighlightOverlay)
    _deselect_and_collect(tab)


def test_hover_highlight_does_not_apply_outside_supply_port_mode(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.resize(900, 700)
    tab.show()
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view.set_mode(DiagramMode.SELECT)
    box = view._boxes["top/u_core"]
    pos = _own_region_point(view, box)

    view._update_supply_port_hover(pos)

    assert view._supply_port_hover_box is None
    assert view._box_highlight_overlay is None
    _deselect_and_collect(tab)


def test_hover_moving_to_a_different_box_moves_the_highlight(qtbot):
    """Driven via Internal Supply, not Supply Port: Supply Port now has
    only ONE possible valid target (the design's own single top-level
    block), so it can no longer exercise "moving between two different
    valid targets" at all -- Internal Supply's own hover uses the exact
    same _BoxHighlightOverlay/hover-tracking mechanism (see
    _update_internal_supply_hover()), just gated by the opposite level
    rule, so this still covers the same real code path."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.resize(900, 700)
    tab.show()
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view.set_mode(DiagramMode.INTERNAL_SUPPLY)
    box_a = view._boxes["top/u_core"]
    box_b = view._boxes["top/u_dbg"]

    view._update_internal_supply_hover(view.mapFromScene(box_a.sceneBoundingRect().center()))
    first_overlay = view._internal_supply_highlight_overlay
    assert view._internal_supply_hover_box is box_a

    view._update_internal_supply_hover(view.mapFromScene(box_b.sceneBoundingRect().center()))

    assert view._internal_supply_hover_box is box_b
    assert view._internal_supply_highlight_overlay is not first_overlay
    _deselect_and_collect(tab)


def test_hover_leaving_all_boxes_clears_the_highlight(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.resize(900, 700)
    tab.show()
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view.set_mode(DiagramMode.SUPPLY_PORT)
    box = view._boxes["top"]
    view._update_supply_port_hover(_own_region_point(view, box))
    assert view._box_highlight_overlay is not None

    view._update_supply_port_hover(QPoint(700, 650))  # well outside any block

    assert view._box_highlight_overlay is None
    assert view._supply_port_hover_box is None
    _deselect_and_collect(tab)


def test_leaving_supply_port_mode_clears_any_active_hover_highlight(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.resize(900, 700)
    tab.show()
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view.set_mode(DiagramMode.SUPPLY_PORT)
    box = view._boxes["top"]
    view._update_supply_port_hover(_own_region_point(view, box))
    assert view._box_highlight_overlay is not None

    view.set_mode(DiagramMode.SELECT)

    assert view._box_highlight_overlay is None
    assert view._supply_port_hover_box is None
    _deselect_and_collect(tab)


def test_click_on_a_highlighted_box_opens_the_add_power_supply_dialog_and_places_the_port(qtbot, monkeypatch):
    """Explicit request: left-click opens the SAME "Add Power Supply"
    dialog the right-click path uses (reused, not a new one)."""
    monkeypatch.setattr(AddPowerSupplyDialog, "exec", _fake_add_power_supply_exec("VDD_NEW", QColor("#123456")))
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.resize(900, 700)
    tab.show()
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view.set_mode(DiagramMode.SUPPLY_PORT)
    box = view._boxes["top"]
    pos = _own_region_point(view, box)
    view._update_supply_port_hover(pos)

    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, pos)
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, pos)

    placed = [icon for icon in view._supply_port_icons.values() if icon.owner_box is box]
    assert len(placed) == 1
    assert placed[0].name == "VDD_NEW"
    assert placed[0].color == QColor("#123456")
    _deselect_and_collect(tab)


def test_click_outside_any_box_does_not_open_the_dialog_or_crash(qtbot, monkeypatch):
    """Explicit request: "Clicking outside any hierarchy block (empty
    canvas) while in this mode does nothing (no port added, no crash)"."""
    opened = []
    monkeypatch.setattr(AddPowerSupplyDialog, "exec", lambda self: opened.append(1) or QDialog.DialogCode.Rejected)
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.resize(900, 700)
    tab.show()
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view.set_mode(DiagramMode.SUPPLY_PORT)
    count_before = len(view._supply_port_icons)

    pos = QPoint(700, 650)  # well outside any block
    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, pos)
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, pos)

    assert opened == []  # dialog never opened -- clicked outside any block
    assert len(view._supply_port_icons) == count_before
    _deselect_and_collect(tab)


def test_supply_port_mode_stays_active_after_a_successful_click(qtbot, monkeypatch):
    """Explicit request: stay in Supply Port mode so another port can be
    added right away by continuing to hover + click other blocks."""
    monkeypatch.setattr(AddPowerSupplyDialog, "exec", _fake_add_power_supply_exec("VDD_NEW", QColor("#123456")))
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.resize(900, 700)
    tab.show()
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view.set_mode(DiagramMode.SUPPLY_PORT)
    box = view._boxes["top"]
    pos = _own_region_point(view, box)

    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, pos)
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, pos)

    assert view._mode == DiagramMode.SUPPLY_PORT
    _deselect_and_collect(tab)


def test_right_click_add_power_supply_still_works_unchanged(qtbot, monkeypatch):
    """Explicit request: "Right-click 'Add Power Supply' context menu
    path remains unchanged as the second way to add a port" -- exercises
    it end-to-end via the same directly-testable helper the base class's
    own context-menu tests already use, unaffected by the Supply Port
    mode rework above (this path is mode-independent)."""
    monkeypatch.setattr(AddPowerSupplyDialog, "exec", _fake_add_power_supply_exec("VDD_NEW", QColor("#123456")))
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top"]

    view._prompt_add_power_supply(box)

    placed = [icon for icon in view._supply_port_icons.values() if icon.owner_box is box]
    assert len(placed) == 1
    assert placed[0].name == "VDD_NEW"
    _deselect_and_collect(tab)


# --- Retention mode: hover+click placement mechanism (validation/dialog/
# icon rendering land in later, separate commits -- this covers only the
# tool-palette/mode/hover-highlight plumbing, same "same pattern as
# Supply Port" mechanism reused as-is).


def test_retention_hover_tracks_into_and_back_out_of_nested_blocks(qtbot):
    """Same nested-block hit-testing fix Supply Port's own hover already
    relies on (see test_hover_tracks_into_and_back_out_of_nested_blocks)
    -- exercised here too since Retention's own hover is a genuinely
    separate pair of fields (_retention_hover_box/
    _retention_highlight_overlay), not a reuse of Supply Port's."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.resize(900, 700)
    tab.show()
    tab.set_rtl(_three_level_nested_design())
    view = tab.diagram._view
    view.set_mode(DiagramMode.RETENTION)
    outer = view._boxes["outer_def"]
    middle = view._boxes["outer_def/u_middle"]
    inner = view._boxes["outer_def/u_middle/u_inner"]

    outer_rect = outer.sceneBoundingRect()
    middle_rect = middle.sceneBoundingRect()
    inner_rect = inner.sceneBoundingRect()
    outer_only = view.mapFromScene(QPointF(outer_rect.center().x(), outer_rect.top() + 5))
    middle_only = view.mapFromScene(QPointF(middle_rect.center().x(), middle_rect.top() + 5))
    inner_point = view.mapFromScene(inner_rect.center())

    view._update_retention_hover(outer_only)
    assert view._retention_hover_box is outer

    view._update_retention_hover(middle_only)
    assert view._retention_hover_box is middle

    view._update_retention_hover(inner_point)
    assert view._retention_hover_box is inner
    _deselect_and_collect(tab)


def test_retention_hover_over_a_box_shows_the_fluorescent_highlight(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.resize(900, 700)
    tab.show()
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view.set_mode(DiagramMode.RETENTION)
    box = view._boxes["top/u_core"]
    pos = _own_region_point(view, box)

    view._update_retention_hover(pos)

    assert view._retention_hover_box is box
    assert isinstance(view._retention_highlight_overlay, _BoxHighlightOverlay)
    _deselect_and_collect(tab)


def test_retention_hover_highlight_does_not_apply_outside_retention_mode(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.resize(900, 700)
    tab.show()
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view.set_mode(DiagramMode.SELECT)
    box = view._boxes["top/u_core"]
    pos = _own_region_point(view, box)

    view._update_retention_hover(pos)

    assert view._retention_hover_box is None
    assert view._retention_highlight_overlay is None
    _deselect_and_collect(tab)


def test_retention_hover_moving_to_a_different_box_moves_the_highlight(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.resize(900, 700)
    tab.show()
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view.set_mode(DiagramMode.RETENTION)
    box_a = view._boxes["top/u_core"]
    box_b = view._boxes["top/u_ram"]

    view._update_retention_hover(view.mapFromScene(box_a.sceneBoundingRect().center()))
    first_overlay = view._retention_highlight_overlay
    assert view._retention_hover_box is box_a

    view._update_retention_hover(view.mapFromScene(box_b.sceneBoundingRect().center()))

    assert view._retention_hover_box is box_b
    assert view._retention_highlight_overlay is not first_overlay
    _deselect_and_collect(tab)


def test_retention_hover_leaving_all_boxes_clears_the_highlight(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.resize(900, 700)
    tab.show()
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view.set_mode(DiagramMode.RETENTION)
    box = view._boxes["top/u_core"]
    view._update_retention_hover(_own_region_point(view, box))
    assert view._retention_highlight_overlay is not None

    view._update_retention_hover(QPoint(700, 650))  # well outside any block

    assert view._retention_highlight_overlay is None
    assert view._retention_hover_box is None
    _deselect_and_collect(tab)


def test_leaving_retention_mode_clears_any_active_hover_highlight(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.resize(900, 700)
    tab.show()
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view.set_mode(DiagramMode.RETENTION)
    box = view._boxes["top/u_core"]
    view._update_retention_hover(_own_region_point(view, box))
    assert view._retention_highlight_overlay is not None

    view.set_mode(DiagramMode.SELECT)

    assert view._retention_highlight_overlay is None
    assert view._retention_hover_box is None
    _deselect_and_collect(tab)


def test_retention_and_supply_port_hover_are_independent(qtbot):
    """Confirms the two modes' own hover state genuinely never collide --
    each mode's own fields are only ever populated while that specific
    mode is active (set_mode() clears the other away). Uses "top" (not
    "top/u_core") -- Supply Port's own hover needs a valid target to
    exercise at all, see _validate_supply_port_target()."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.resize(900, 700)
    tab.show()
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top"]
    pos = _own_region_point(view, box)

    view.set_mode(DiagramMode.SUPPLY_PORT)
    view._update_supply_port_hover(pos)
    assert view._supply_port_hover_box is box
    assert view._retention_hover_box is None

    view.set_mode(DiagramMode.RETENTION)
    assert view._supply_port_hover_box is None  # cleared on mode switch
    view._update_retention_hover(pos)
    assert view._retention_hover_box is box
    _deselect_and_collect(tab)


# --- Supply Net mode: click-drag-connect, now explicitly mode-gated -------


def test_supply_net_mode_handle_drag_still_connects_two_ports(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.resize(900, 700)
    tab.show()
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    outer = view.add_supply_port(view._boxes["top"], "VDD", QColor("#ff0000"))
    inner = view.add_supply_port(view._boxes["top/u_core"], "VDD_CORE", QColor("#00ff00"))
    view.set_mode(DiagramMode.SUPPLY_NET)

    start = _viewport_pos(view, outer.handles["bottom"])
    end = _viewport_pos(view, inner.handles["top"])
    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
    assert view._connector_drag_line is not None
    QTest.mouseMove(view.viewport(), end)
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, end)

    assert len(view._supply_connectors) == 1
    assert view._mode == DiagramMode.SUPPLY_NET  # stays active, explicit request
    _deselect_and_collect(tab)


@pytest.mark.parametrize(
    "mode",
    [DiagramMode.SELECT, DiagramMode.PAN, DiagramMode.ZOOM_LENS, DiagramMode.SUPPLY_PORT, DiagramMode.RETENTION],
)
def test_handle_drag_outside_supply_net_mode_does_not_start_a_connector(qtbot, monkeypatch, mode):
    """Bug 3 (explicit request): "currently it seems to work regardless
    of active mode; fix that" -- re-verified empirically (via a live
    QTest press/move/release drive, not just reading the code) across
    every one of the 5 non-Supply-Net modes, not just Select, since an
    incomplete fix could plausibly have only gated one of the two
    handlers (hover-highlight vs. click-to-start-drag) and not the
    other. Also monkeypatches the dialogs/QMessageBox that Supply Port/
    Retention mode's own OWN click handling would otherwise open for a
    press landing on a box edge -- same "never let an unmocked modal
    block a headless test forever" precaution this codebase already
    established elsewhere."""
    monkeypatch.setattr(AddPowerSupplyDialog, "exec", lambda self: QDialog.DialogCode.Rejected)
    monkeypatch.setattr(RetentionStrategyDialog, "exec", lambda self: QDialog.DialogCode.Rejected)
    monkeypatch.setattr(message_box, "warning", lambda *args, **kwargs: None)
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.resize(900, 700)
    tab.show()
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    outer = view.add_supply_port(view._boxes["top"], "VDD", QColor("#ff0000"))
    inner = view.add_supply_port(view._boxes["top/u_core"], "VDD_CORE", QColor("#00ff00"))
    view.set_mode(mode)

    start = _viewport_pos(view, outer.handles["bottom"])
    end = _viewport_pos(view, inner.handles["top"])
    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
    assert view._connector_drag_line is None  # the press fell through to that mode's own normal behavior

    QTest.mouseMove(view.viewport(), end)
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, end)

    assert view._connector_drag_line is None
    assert len(view._supply_connectors) == 0  # no net was drawn, whatever else the gesture did in this mode
    _deselect_and_collect(tab)


def test_supply_net_mode_hover_highlights_a_handle_under_the_cursor(qtbot):
    """Explicit request: hover feedback on a valid connector while in
    Supply Net mode."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.resize(900, 700)
    tab.show()
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    outer = view.add_supply_port(view._boxes["top"], "VDD", QColor("#ff0000"))
    _make_view_actually_visible(tab)
    view.set_mode(DiagramMode.SUPPLY_NET)
    handle_pos = _viewport_pos(view, outer.handles["bottom"])

    QTest.mouseMove(view.viewport(), handle_pos)

    assert view._hovered_handle is outer.handles["bottom"]
    _deselect_and_collect(tab)


def test_hover_highlight_clears_when_the_cursor_moves_off(qtbot):
    """Drives _update_supply_net_hover() directly rather than through
    QTest.mouseMove() -- same "directly-testable core" precedent this
    whole module already follows for add_supply_port()/
    try_connect_supply_ports() (menu.exec()/dialog.exec() can't be
    driven headlessly there; here the issue is QTest.mouseMove()'s own
    "only fires if the *global* cursor position actually changed since
    the last synthetic move" dedup, which made this genuinely flaky
    across tests -- confirmed live: two back-to-back tests moving to the
    same *local* viewport coordinate landed on the same *global* screen
    position under the offscreen platform, so the second one silently
    delivered nothing.)"""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.resize(900, 700)
    tab.show()
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    outer = view.add_supply_port(view._boxes["top"], "VDD", QColor("#ff0000"))
    view.set_mode(DiagramMode.SUPPLY_NET)
    handle_pos = _viewport_pos(view, outer.handles["bottom"])
    view._update_supply_net_hover(handle_pos)
    assert view._hovered_handle is not None

    view._update_supply_net_hover(QPoint(5, 5))

    assert view._hovered_handle is None
    _deselect_and_collect(tab)


def test_hover_highlight_does_not_apply_outside_supply_net_mode(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.resize(900, 700)
    tab.show()
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    outer = view.add_supply_port(view._boxes["top"], "VDD", QColor("#ff0000"))
    view.set_mode(DiagramMode.SELECT)

    view._update_supply_net_hover(_viewport_pos(view, outer.handles["bottom"]))

    assert view._hovered_handle is None
    _deselect_and_collect(tab)


def test_leaving_supply_net_mode_clears_any_active_hover_highlight(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.resize(900, 700)
    tab.show()
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    outer = view.add_supply_port(view._boxes["top"], "VDD", QColor("#ff0000"))
    view.set_mode(DiagramMode.SUPPLY_NET)
    view._update_supply_net_hover(_viewport_pos(view, outer.handles["bottom"]))
    assert view._hovered_handle is not None

    view.set_mode(DiagramMode.SELECT)

    assert view._hovered_handle is None
    _deselect_and_collect(tab)


def test_source_handle_stays_highlighted_for_the_whole_drag(qtbot):
    """Explicit request: the SOURCE anchor point stays highlighted yellow
    for the duration of the drag, even as the cursor wanders off it and
    over empty space (not just while hovering some other handle)."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.resize(900, 700)
    tab.show()
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    outer = view.add_supply_port(view._boxes["top"], "VDD", QColor("#ff0000"))
    view.add_supply_port(view._boxes["top/u_core"], "VDD_CORE", QColor("#00ff00"))
    view.set_mode(DiagramMode.SUPPLY_NET)
    start = _viewport_pos(view, outer.handles["bottom"])

    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
    origin = view._connector_drag_origin
    assert origin is outer.handles["bottom"]
    assert origin.pen().color() == HIGHLIGHT_COLOR  # highlighted the moment the drag starts

    # Cursor wanders to empty space, nowhere near any handle.
    view._update_supply_net_hover(QPoint(5, 5))

    assert view._hovered_handle is None  # no *other* handle is the current target
    assert origin.pen().color() == HIGHLIGHT_COLOR  # source stays highlighted regardless
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(5, 5))
    _deselect_and_collect(tab)


# --- Escape: global tool-state reset ---------------------------------------


def test_escape_cancels_an_in_progress_rubber_band_and_resets_to_select(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.resize(900, 700)
    tab.show()
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    tab.tool_palette.zoom_lens_action.setChecked(True)
    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(20, 20))
    QTest.mouseMove(view.viewport(), QPoint(120, 120))
    assert view._rubber_band is not None

    QTest.keyClick(view, Qt.Key.Key_Escape)

    assert view._rubber_band is None
    assert view._mode == DiagramMode.SELECT
    assert tab.tool_palette.select_action.isChecked() is True
    assert view.viewport().cursor().shape() == Qt.CursorShape.ArrowCursor
    _deselect_and_collect(tab)


def test_escape_cancels_an_in_progress_supply_net_drag(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.resize(900, 700)
    tab.show()
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    outer = view.add_supply_port(view._boxes["top"], "VDD", QColor("#ff0000"))
    view.add_supply_port(view._boxes["top/u_core"], "VDD_CORE", QColor("#00ff00"))
    tab.tool_palette.supply_net_action.setChecked(True)
    start = _viewport_pos(view, outer.handles["bottom"])
    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
    assert view._connector_drag_line is not None
    origin = view._connector_drag_origin

    QTest.keyClick(view, Qt.Key.Key_Escape)

    assert view._connector_drag_line is None
    assert view._connector_drag_origin is None
    assert view._supply_connectors == []  # no connection was committed
    assert origin.pen().color() != HIGHLIGHT_COLOR  # highlight cleared, not left stuck on
    assert view._mode == DiagramMode.SELECT
    assert tab.tool_palette.select_action.isChecked() is True
    _deselect_and_collect(tab)


@pytest.mark.parametrize(
    "action_name",
    [
        "select_action",
        "pan_action",
        "zoom_lens_action",
        "supply_port_action",
        "internal_supply_action",
        "supply_net_action",
        "retention_action",
    ],
)
def test_escape_always_resets_to_select_even_with_nothing_in_progress(qtbot, action_name):
    """Explicit request (reverting a previous revision that had gated
    this on `_cancel_active_interaction()`'s own return value -- a real,
    reported bug: DiagramMode.SUPPLY_PORT's own hover highlight is armed
    by mere mouse movement, not a click-drag, so it was "active" far
    more often than Zoom Lens/Pan/Supply Net's own drag-only state,
    making Escape *appear* to only work in Supply Port mode in
    practice): Escape ALWAYS resets to Regular Cursor, "regardless of
    active tool," with zero in-progress interaction in any of the modes
    here -- mode-reset is unconditional, decoupled from whether there
    was actually anything to cancel."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.resize(900, 700)
    tab.show()
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    getattr(tab.tool_palette, action_name).setChecked(True)

    QTest.keyClick(view, Qt.Key.Key_Escape)

    assert view._mode == DiagramMode.SELECT
    assert tab.tool_palette.select_action.isChecked() is True
    assert view.viewport().cursor().shape() == Qt.CursorShape.ArrowCursor
    _deselect_and_collect(tab)


def test_escape_clears_the_yellow_highlighted_selection_and_resets_the_tool(qtbot):
    """Follow-up explicit request: Escape now ALSO clears any current
    yellow-fluo selection highlight (hierarchy blocks, Supply Port/
    Retention icons, etc. -- including a multi-selection from Ctrl+
    Click), as a THIRD action alongside the pre-existing cancel-in-
    progress-interaction and reset-tool-to-Select behaviors -- both of
    which must still happen in the same keypress."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.resize(900, 700)
    tab.show()
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box_a = view._boxes["top/u_core"]
    box_b = view._boxes["top/u_dbg"]
    box_a.setSelected(True)
    box_b.setSelected(True)
    assert box_a.pen().color().name() == "#ffff00"
    assert box_b.pen().color().name() == "#ffff00"
    tab.tool_palette.retention_action.setChecked(True)  # a non-Select tool active too, to confirm the reset still happens together

    QTest.keyClick(view, Qt.Key.Key_Escape)

    assert view.scene().selectedItems() == []
    assert box_a.pen().color().name() != "#ffff00"
    assert box_b.pen().color().name() != "#ffff00"
    assert view._mode == DiagramMode.SELECT
    assert tab.tool_palette.select_action.isChecked() is True
    _deselect_and_collect(tab)


def test_cancel_active_interaction_returns_false_with_nothing_in_progress(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view

    assert view._cancel_active_interaction() is False
    _deselect_and_collect(tab)


def test_cancel_active_interaction_returns_true_for_a_live_rubber_band(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.resize(900, 700)
    tab.show()
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view.set_mode(DiagramMode.ZOOM_LENS)
    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(20, 20))
    assert view._rubber_band is not None

    assert view._cancel_active_interaction() is True

    assert view._rubber_band is None
    _deselect_and_collect(tab)


def test_escape_resets_every_open_tab_not_just_the_active_one(qtbot):
    """Cross-tab propagation -- pressing Escape in one tab's own view
    resets every open tab's mode/cursor uniformly (via escapePressed ->
    PowerIntentDiagramView -> tool_palette.select_action, the same chain
    a normal toolbar click already uses), not just the one it was
    pressed in. No active interaction needed in either tab -- mode-reset
    on Escape is unconditional (see keyPressEvent()'s own docstring)."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.resize(900, 700)
    tab.show()
    tab.set_rtl(build_mock_design())
    tab.tool_palette.zoom_lens_action.setChecked(True)
    root_view = tab.diagram._tabs.current_view
    tab.diagram.open_path("top/u_core")
    sub_view = tab.diagram._tabs.current_view
    assert sub_view is not root_view

    QTest.keyClick(sub_view, Qt.Key.Key_Escape)

    assert root_view.viewport().cursor().shape() == Qt.CursorShape.ArrowCursor
    assert sub_view.viewport().cursor().shape() == Qt.CursorShape.ArrowCursor
    _deselect_and_collect(tab)


# --- bug 2: Regular Cursor mode click-selection highlight (fluorescent yellow) --


def test_clicking_a_hierarchy_block_highlights_only_its_border_not_its_fill(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.resize(900, 700)
    tab.show()
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view.set_mode(DiagramMode.SELECT)
    box = view._boxes["top/u_core"]
    fill_before = box.brush().color().name()
    pos = _own_region_point(view, box)

    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, pos)

    assert box.pen().color() == HIGHLIGHT_COLOR
    assert box.brush().color().name() == fill_before  # explicit request: no fill change at all
    _deselect_and_collect(tab)


def test_clicking_empty_canvas_clears_the_block_highlight(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.resize(900, 700)
    tab.show()
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view.set_mode(DiagramMode.SELECT)
    box = view._boxes["top/u_core"]
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, _own_region_point(view, box))
    assert box.pen().color() == HIGHLIGHT_COLOR

    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(20, 650))  # well outside any block

    assert box.pen().color() != HIGHLIGHT_COLOR
    _deselect_and_collect(tab)


def test_clicking_a_different_block_moves_the_highlight(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.resize(900, 700)
    tab.show()
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view.set_mode(DiagramMode.SELECT)
    box_a = view._boxes["top/u_core"]
    box_b = view._boxes["top/u_dbg"]
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, view.mapFromScene(box_a.sceneBoundingRect().center()))
    assert box_a.pen().color() == HIGHLIGHT_COLOR

    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, view.mapFromScene(box_b.sceneBoundingRect().center()))

    assert box_b.pen().color() == HIGHLIGHT_COLOR
    assert box_a.pen().color() != HIGHLIGHT_COLOR
    _deselect_and_collect(tab)


def test_clicking_a_supply_port_icon_clears_a_previously_highlighted_block(qtbot):
    """Explicit request: "only one thing highlighted at a time" spans
    every object type, not just within one type."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.resize(900, 700)
    tab.show()
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view.set_mode(DiagramMode.SELECT)
    box = view._boxes["top/u_core"]
    icon = view.add_supply_port(view._boxes["top"], "VDD", QColor("#2196f3"))
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, _own_region_point(view, box))
    assert box.pen().color() == HIGHLIGHT_COLOR

    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, view.mapFromScene(icon.scenePos()))

    assert box.pen().color() != HIGHLIGHT_COLOR
    assert icon._click_highlighted is True
    _deselect_and_collect(tab)
