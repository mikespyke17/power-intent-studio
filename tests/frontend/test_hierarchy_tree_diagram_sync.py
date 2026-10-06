"""Hierarchy Tree <-> Diagram interplay on the Visualize Design tab:
bidirectional click-to-highlight sync, right-click Hide/Unhide (boxes
disappear from the diagram, not just dim), the "Clear" button, and the
Diagram's mouse-wheel zoom / click-drag pan / Fit View -- against mock
RTL data only."""

import gc

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QGuiApplication, QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QGraphicsRectItem

from frontend.src.hierarchy_view.hierarchy_diagram_view import HierarchyDiagramView
from frontend.src.hierarchy_view.hierarchy_tree_widget import HierarchyTreeWidget
from frontend.src.hierarchy_view.selection_controller import HierarchySelectionController
from frontend.src.hierarchy_view.visualize_design_tab import VisualizeDesignTab
from frontend.src.shared.mock_backend.mock_rtl import build_mock_design
from frontend.src.shared.models.rtl import Definition, Design, Instance, SourceLanguage


class _FakeAngleDelta:
    def __init__(self, y: int):
        self._y = y

    def y(self) -> int:
        return self._y


class _FakeWheelEvent:
    """Duck-typed stand-in for QWheelEvent -- exercises
    HierarchyDiagramView.wheelEvent()'s own direction-mapping logic
    without depending on QWheelEvent's constructor shape."""

    def __init__(self, delta_y: int):
        self._delta_y = delta_y

    def angleDelta(self) -> _FakeAngleDelta:
        return _FakeAngleDelta(self._delta_y)

    def accept(self) -> None:
        pass


def _mouse_event(pos: QPoint, event_type=QEvent.Type.MouseMove, buttons=Qt.MouseButton.LeftButton) -> QMouseEvent:
    """A real QMouseEvent -- HierarchyDiagramView's mouse*Event() methods
    call super().mouse*Event(event) in the non-panning path, and PySide6
    type-checks that argument strictly (a duck-typed fake raises
    TypeError there, confirmed directly)."""
    point = QPointF(pos)
    return QMouseEvent(event_type, point, point, buttons, buttons, Qt.KeyboardModifier.NoModifier)


def _wired(qtbot):
    controller = HierarchySelectionController()
    tree = HierarchyTreeWidget(controller)
    diagram = HierarchyDiagramView(controller)
    qtbot.addWidget(tree)
    qtbot.addWidget(diagram)
    design = build_mock_design()
    tree.load(design)
    diagram.load(design)
    return controller, tree, diagram


def _nested_wired(qtbot):
    """bank_def -> u_wrap -> u_macro, a 3-level nesting chain -- for
    confirming highlighting reaches a deeply-nested box, not just a
    top-level one (build_mock_design() only nests one level deep, not
    enough to exercise this)."""
    macro = Definition(definition_id="macro", base_name="macro_def", language=SourceLanguage.SYSTEMVERILOG, is_black_box=True)
    wrap = Definition(
        definition_id="wrap",
        base_name="wrap_def",
        language=SourceLanguage.SYSTEMVERILOG,
        instances=[Instance(name="u_macro", definition_id="macro")],
    )
    bank = Definition(
        definition_id="bank",
        base_name="bank_def",
        language=SourceLanguage.SYSTEMVERILOG,
        instances=[Instance(name="u_wrap", definition_id="wrap")],
    )
    design = Design(definitions={"macro": macro, "wrap": wrap, "bank": bank}, top="bank")

    controller = HierarchySelectionController()
    tree = HierarchyTreeWidget(controller)
    diagram = HierarchyDiagramView(controller)
    qtbot.addWidget(tree)
    qtbot.addWidget(diagram)
    tree.load(design)
    diagram.load(design)
    return controller, tree, diagram


def _deselect_and_collect(diagram: HierarchyDiagramView) -> None:
    """Call at the end of any test that leaves a box selected
    (setSelected(True), simulating a click). A selected QGraphicsItem
    whose scene gets torn down can segfault -- reproduced standalone: the
    item's C++ destructor fires selectionChanged() back into a
    HierarchyDiagramView Python object that's *simultaneously* being
    collected as part of the same reference cycle (controller <-> diagram
    <-> scene via the bound-method signal connections), which only
    Python's cyclic GC -- not plain refcounting -- can break, at a time
    of its own choosing. Deselecting removes the trigger; forcing gc.collect()
    here (rather than leaving pytest-qt's deferred close()/deleteLater()
    to eventually collect it, at which point this state may no longer be
    current) reproduces exactly the sequence confirmed safe standalone."""
    if diagram.scene() is not None:
        diagram.scene().clearSelection()
    gc.collect()


# --- interface instances are excluded from the Tree and Diagram entirely --
# An interface is a bundle of wiring with modport constraints, not a block
# of logic -- build_mock_design()'s "u_bus" (a simple_bus_if instance) must
# never appear as a Tree item or a Diagram box, even though it's a real
# child in top.instances and design.walk() itself is unfiltered.


def test_interface_instance_has_no_tree_item(qtbot):
    controller, tree, diagram = _wired(qtbot)
    assert "top/u_bus" not in tree._items_by_path


def test_interface_instance_has_no_diagram_box(qtbot):
    controller, tree, diagram = _wired(qtbot)
    assert "top/u_bus" not in diagram._boxes


def test_interface_instance_does_not_count_toward_its_parents_leaf_or_child_layout(qtbot):
    """top has 4 real children (u_core, u_dbg, u_ram, u_fwd) plus the
    excluded u_bus -- the diagram must lay out (and size) top based on the
    4 real ones only, never trying to size/place a box for u_bus."""
    controller, tree, diagram = _wired(qtbot)
    assert set(diagram._boxes) == {"top", "top/u_core", "top/u_dbg", "top/u_ram", "top/u_fwd"}


# --- bidirectional selection sync ---------------------------------------


def test_clicking_a_tree_item_highlights_the_matching_diagram_box(qtbot):
    controller, tree, diagram = _wired(qtbot)

    tree.setCurrentItem(tree._items_by_path["top/u_core"])
    tree._on_item_selected()  # setCurrentItem alone doesn't fire itemSelectionChanged headlessly

    box = diagram._boxes["top/u_core"]
    assert diagram._highlighted_paths == {"top/u_core"}
    assert box.pen().color().name() == "#ffff00"  # fluorescent yellow border -- see set_highlighted()
    # The pen-color assertion alone previously let a real bug through:
    # apply_selection() only changed the pen, never Qt's own isSelected()
    # state, so this direction never got the native "item is selected"
    # paint decoration diagram-click selection gets for free -- see
    # HierarchyDiagramView.apply_selection()'s own docstring.
    assert box.isSelected() is True
    _deselect_and_collect(diagram)


def test_apply_selection_unblocks_scene_signals_even_if_setSelected_raises(qtbot, monkeypatch):
    """Exception-safety of apply_selection()'s blockSignals(True)/(False)
    guard: if something between the two calls raises, the scene must not
    be left permanently signal-blocked -- that would silently swallow
    every future real diagram click, since _on_scene_selection_changed()
    is wired to scene.selectionChanged, which a permanently-blocked scene
    never fires again. Confirms the guard is a real try/finally (blockSignals(False)
    still runs, and the exception still propagates rather than being
    swallowed), not a bare pair of `if scene is not None:
    scene.blockSignals(...)` calls that a raise in between could skip the
    second half of."""
    controller, tree, diagram = _wired(qtbot)
    scene = diagram.scene()

    calls = []
    real_block_signals = scene.blockSignals

    def spy_block_signals(blocked):
        calls.append(blocked)
        return real_block_signals(blocked)

    monkeypatch.setattr(scene, "blockSignals", spy_block_signals)

    def _raise_setSelected(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(diagram._boxes["top/u_core"], "setSelected", _raise_setSelected)

    with pytest.raises(RuntimeError, match="boom"):
        diagram.apply_selection({"top/u_core"})

    assert calls == [True, False]  # blockSignals(False) still ran despite the raise
    assert scene.signalsBlocked() is False  # and the scene really is unblocked, not just "called"


def test_clicking_a_diagram_box_highlights_the_matching_tree_item(qtbot):
    controller, tree, diagram = _wired(qtbot)

    diagram._boxes["top/u_core"].setSelected(True)  # simulates a real box click

    assert tree.currentItem() is tree._items_by_path["top/u_core"]
    _deselect_and_collect(diagram)


def test_selection_sync_is_symmetric_for_a_different_path(qtbot):
    """Same check, different node, confirming it's not a one-path fluke."""
    controller, tree, diagram = _wired(qtbot)

    diagram._boxes["top/u_dbg"].setSelected(True)

    assert tree.currentItem() is tree._items_by_path["top/u_dbg"]
    _deselect_and_collect(diagram)


# --- Ctrl+click multi-select, symmetric across Tree and Diagram --------
# Qt's own native selection machinery (QAbstractItemView's ExtendedSelection
# mode; QGraphicsScene's default click handling) already implements the
# actual Ctrl+click toggle -- confirmed directly, nothing hand-rolled here
# (see hierarchy_tree_widget.py/hierarchy_diagram_view.py's own
# docstrings). These tests drive the *outcome* of a Ctrl+click directly
# (multiple items/boxes ending up selected at once), same convention the
# single-select tests above already use for "simulates a real click."


def test_multi_selecting_two_tree_items_highlights_both_diagram_boxes(qtbot):
    controller, tree, diagram = _wired(qtbot)

    tree._items_by_path["top/u_core"].setSelected(True)
    tree._items_by_path["top/u_dbg"].setSelected(True)
    tree._on_item_selected()  # setSelected() alone doesn't fire itemSelectionChanged headlessly

    assert diagram._highlighted_paths == {"top/u_core", "top/u_dbg"}
    assert diagram._boxes["top/u_core"].isSelected() is True
    assert diagram._boxes["top/u_dbg"].isSelected() is True
    assert diagram._boxes["top/u_ram"].isSelected() is False
    _deselect_and_collect(diagram)


def test_multi_selecting_two_diagram_boxes_selects_both_tree_items(qtbot):
    controller, tree, diagram = _wired(qtbot)

    diagram._boxes["top/u_core"].setSelected(True)
    diagram._boxes["top/u_dbg"].setSelected(True)  # no clearSelection() in between -- both stay selected

    assert tree._items_by_path["top/u_core"].isSelected() is True
    assert tree._items_by_path["top/u_dbg"].isSelected() is True
    assert tree._items_by_path["top/u_ram"].isSelected() is False
    _deselect_and_collect(diagram)


def test_deselecting_everything_in_the_tree_clears_the_diagram_highlight(qtbot):
    controller, tree, diagram = _wired(qtbot)
    tree._items_by_path["top/u_core"].setSelected(True)
    tree._on_item_selected()
    assert diagram._highlighted_paths == {"top/u_core"}

    tree.clearSelection()
    tree._on_item_selected()

    assert diagram._highlighted_paths == frozenset()
    assert diagram._boxes["top/u_core"].isSelected() is False
    _deselect_and_collect(diagram)


def test_deselecting_everything_in_the_diagram_clears_the_tree_selection(qtbot):
    controller, tree, diagram = _wired(qtbot)
    diagram._boxes["top/u_core"].setSelected(True)
    assert tree._items_by_path["top/u_core"].isSelected() is True

    diagram.scene().clearSelection()

    assert tree._items_by_path["top/u_core"].isSelected() is False
    _deselect_and_collect(diagram)


# --- selection highlight color, including deeply-nested boxes ----------


def test_selected_box_border_turns_fluorescent_yellow(qtbot):
    controller, tree, diagram = _wired(qtbot)

    tree._items_by_path["top/u_core"].setSelected(True)
    tree._on_item_selected()

    assert diagram._boxes["top/u_core"].pen().color().name() == "#ffff00"
    _deselect_and_collect(diagram)


def test_a_deeply_nested_box_highlights_fluorescent_yellow_when_selected(qtbot):
    """"bank_def/u_wrap" (an RTL container, 1 level deep) must highlight
    exactly the same way a top-level box does -- the path->box lookup
    driving set_highlighted()/setSelected() is a flat map, not
    depth-limited. Uses u_wrap rather than the innermost u_macro
    specifically because a macro is a deliberate exception to
    highlighting altogether -- see the "macro color never changes"
    section below."""
    controller, tree, diagram = _nested_wired(qtbot)

    tree._items_by_path["bank_def/u_wrap"].setSelected(True)
    tree._on_item_selected()

    inner_box = diagram._boxes["bank_def/u_wrap"]
    outer_box = diagram._boxes["bank_def"]
    assert inner_box.isSelected() is True
    assert inner_box.pen().color().name() == "#ffff00"
    # selecting the inner box doesn't cascade a highlight up to its
    # unselected ancestor
    assert outer_box.isSelected() is False
    assert outer_box.pen().color().name() != "#ffff00"
    _deselect_and_collect(diagram)


def test_selecting_a_container_cascades_the_highlight_down_to_its_nested_child(qtbot):
    """Selecting "bank_def" (the outermost container) must also color
    its nested RTL child (bank_def/u_wrap), so the selection visually
    reads as "this whole assembly" -- but must NOT mark that child as
    actually selected (isSelected()/controller-level selection stays
    exact, only the visual highlight cascades -- see
    HierarchyDiagramView's own docstring for why conflating the two
    would corrupt the Properties panel's union). The nested macro is
    covered separately below -- it's excluded from this cascade too."""
    controller, tree, diagram = _nested_wired(qtbot)

    tree._items_by_path["bank_def"].setSelected(True)
    tree._on_item_selected()

    bank_box = diagram._boxes["bank_def"]
    wrap_box = diagram._boxes["bank_def/u_wrap"]

    assert bank_box.isSelected() is True
    assert bank_box.pen().color().name() == "#ffff00"
    assert wrap_box.pen().color().name() == "#ffff00"

    # cascaded, not actually selected -- Qt's own state and the
    # controller-level selection both stay exact
    assert wrap_box.isSelected() is False
    assert diagram._selected_paths == {"bank_def"}
    _deselect_and_collect(diagram)


def test_deselecting_a_container_un_highlights_its_cascaded_child(qtbot):
    controller, tree, diagram = _nested_wired(qtbot)
    tree._items_by_path["bank_def"].setSelected(True)
    tree._on_item_selected()
    wrap_box = diagram._boxes["bank_def/u_wrap"]
    assert wrap_box.pen().color().name() == "#ffff00"

    tree.clearSelection()
    tree._on_item_selected()

    assert wrap_box.pen().color().name() != "#ffff00"
    assert diagram._highlighted_paths == frozenset()
    _deselect_and_collect(diagram)


# --- macro color never changes, selected directly or indirectly --------


def test_macro_stays_grey_when_directly_selected(qtbot):
    controller, tree, diagram = _nested_wired(qtbot)

    tree._items_by_path["bank_def/u_wrap/u_macro"].setSelected(True)
    tree._on_item_selected()

    macro_box = diagram._boxes["bank_def/u_wrap/u_macro"]
    assert macro_box.isSelected() is True  # still genuinely selected...
    assert macro_box.pen().color().name() == "#202020"  # ...but its color never changed
    assert macro_box.brush().color().name() == "#c8c8c8"
    _deselect_and_collect(diagram)


def test_macro_stays_grey_when_only_cascade_highlighted_via_a_selected_ancestor(qtbot):
    controller, tree, diagram = _nested_wired(qtbot)

    tree._items_by_path["bank_def"].setSelected(True)
    tree._on_item_selected()

    macro_box = diagram._boxes["bank_def/u_wrap/u_macro"]
    assert macro_box.isSelected() is False
    assert macro_box.pen().color().name() == "#202020"
    assert macro_box.brush().color().name() == "#c8c8c8"
    _deselect_and_collect(diagram)


# --- Hide/Unhide: boxes disappear from the diagram, tree items grey out --


def test_hide_removes_the_box_from_the_diagram_entirely(qtbot):
    controller, tree, diagram = _wired(qtbot)

    tree._toggle_hidden("top/u_core")

    assert "top/u_core" not in diagram._boxes


def test_hide_greys_the_tree_item_text_but_keeps_it_in_the_tree(qtbot):
    controller, tree, diagram = _wired(qtbot)

    tree._toggle_hidden("top/u_core")

    item = tree._items_by_path["top/u_core"]
    assert item.foreground(0).color().name() == "#a0a0a0"
    assert item.parent() is tree._items_by_path["top"]  # still present in the tree, just greyed


def test_hide_cascades_to_downstream_descendants(qtbot):
    """Hiding a parent removes its whole subtree from the diagram and
    greys the whole subtree's tree text, even though only the parent was
    explicitly toggled.

    Bug fix follow-up ("Macros can never be hidden," explicit request,
    supersedes this test's own former "diagram._boxes == {}" assertion):
    build_mock_design()'s own "top" hierarchy contains a real Macro
    ("u_ram") -- per that new requirement, "top" and "top/u_ram" must
    still render in the DIAGRAM even though "top" was hidden, while
    "top/u_core"/"top/u_dbg" (ordinary, non-Macro content) are still
    genuinely removed. The TREE's own greying is unaffected either way
    -- it reflects the user's own "this is marked hidden" intent, a
    separate concern from the diagram's own macro-visibility exemption
    (see hierarchy_scene_builder.py's own module docstring)."""
    controller, tree, diagram = _wired(qtbot)
    # give u_core a child of its own indirectly isn't possible in the mock
    # (it's a leaf), so hide "top" itself to prove cascading removal.
    tree._toggle_hidden("top")

    assert "top/u_core" not in diagram._boxes
    assert "top/u_dbg" not in diagram._boxes
    assert "top" in diagram._boxes  # still renders -- it's the Macro's own ancestor
    assert "top/u_ram" in diagram._boxes  # the Macro itself, exempted from hiding
    for path in ("top", "top/u_core", "top/u_dbg", "top/u_ram"):
        assert tree._items_by_path[path].foreground(0).color().name() == "#a0a0a0"


def test_the_hidden_instances_parent_box_shrinks_to_fit_remaining_children(qtbot):
    controller, tree, diagram = _wired(qtbot)
    before = diagram._boxes["top"].rect().width()

    tree._toggle_hidden("top/u_core")

    after = diagram._boxes["top"].rect().width()
    assert after < before


def test_unhide_restores_the_box_and_normal_tree_text(qtbot):
    controller, tree, diagram = _wired(qtbot)

    tree._toggle_hidden("top/u_core")
    tree._toggle_hidden("top/u_core")  # Hide then Unhide

    assert "top/u_core" in diagram._boxes
    assert tree._items_by_path["top/u_core"].foreground(0).color().name() == "#000000"


def test_unhiding_a_container_also_purges_a_redundantly_hidden_nested_entry(qtbot):
    """"top" is hidden (which already covers "top/u_core" as a
    descendant), then "top/u_core" is *also* explicitly hidden on its
    own -- a real, reachable scenario since tree rows stay clickable
    even while greyed. Un-hiding "top" alone must bring the whole
    assembly back, not leave "top/u_core" stuck hidden because it has
    its own, now-orphaned entry."""
    controller, tree, diagram = _wired(qtbot)
    tree._toggle_hidden("top")
    tree._toggle_hidden("top/u_core")
    assert tree._hidden_paths == {"top", "top/u_core"}

    tree._toggle_hidden("top")  # unhide "top"

    assert tree._hidden_paths == set()
    assert "top/u_core" in diagram._boxes
    assert tree._items_by_path["top/u_core"].foreground(0).color().name() == "#000000"


def test_unhiding_an_upper_block_purges_a_redundantly_hidden_inner_block(qtbot):
    """Same scenario, but with genuine multi-level nesting (bank_def ->
    u_wrap -> u_macro) so "upper"/"inner" are literal, not just
    top-level-vs-descendant in a flat mock design."""
    controller, tree, diagram = _nested_wired(qtbot)
    tree._toggle_hidden("bank_def")
    tree._toggle_hidden("bank_def/u_wrap")
    assert tree._hidden_paths == {"bank_def", "bank_def/u_wrap"}

    tree._toggle_hidden("bank_def")  # unhide the upper block

    assert tree._hidden_paths == set()
    assert "bank_def/u_wrap" in diagram._boxes
    assert "bank_def/u_wrap/u_macro" in diagram._boxes


# --- Macros can never be hidden (explicit request, new feature) -----------
# Whatever hierarchy a Macro sits inside -- at ANY nesting depth -- must stay
# visible in the diagram even when that hierarchy (or an ancestor of it) is
# explicitly hidden; the surrounding NON-Macro structure still collapses
# normally. Implemented in hierarchy_scene_builder.py's own
# _visible_children()/_definition_ids_containing_macro() -- a hidden-but-
# macro-containing child is still included (so its own box, and therefore
# the chain down to the Macro, still renders), with a `macro_only` flag
# propagated to its own further recursion so every OTHER (non-Macro)
# descendant within that same hidden subtree keeps collapsing as before.


def _wired_with_macro_and_sibling(qtbot):
    """top -> u_mid -> {u_macro (a real .lib macro), u_sibling (an
    ordinary leaf)} -- lets a single test prove BOTH halves of the
    requirement at once on the SAME hide action: the Macro stays
    visible, its own non-Macro sibling does not."""
    macro = Definition(definition_id="macro", base_name="macro_def", language=SourceLanguage.SYSTEMVERILOG, is_black_box=True)
    sibling = Definition(definition_id="sibling", base_name="sibling_def", language=SourceLanguage.SYSTEMVERILOG)
    mid = Definition(
        definition_id="mid",
        base_name="mid_def",
        language=SourceLanguage.SYSTEMVERILOG,
        instances=[Instance(name="u_macro", definition_id="macro"), Instance(name="u_sibling", definition_id="sibling")],
    )
    top = Definition(
        definition_id="top", base_name="top_def", language=SourceLanguage.SYSTEMVERILOG, instances=[Instance(name="u_mid", definition_id="mid")]
    )
    design = Design(definitions={"top": top, "mid": mid, "macro": macro, "sibling": sibling}, top="top")

    controller = HierarchySelectionController()
    tree = HierarchyTreeWidget(controller)
    diagram = HierarchyDiagramView(controller)
    qtbot.addWidget(tree)
    qtbot.addWidget(diagram)
    tree.load(design)
    diagram.load(design)
    return controller, tree, diagram


def test_hiding_the_macros_own_ancestor_keeps_the_macro_visible_but_hides_its_sibling(qtbot):
    controller, tree, diagram = _wired_with_macro_and_sibling(qtbot)
    assert "top_def/u_mid/u_macro" in diagram._boxes  # sanity, before hiding anything
    assert "top_def/u_mid/u_sibling" in diagram._boxes

    tree._toggle_hidden("top_def/u_mid")

    assert "top_def/u_mid" in diagram._boxes  # the ancestor itself still renders, as a container
    assert "top_def/u_mid/u_macro" in diagram._boxes  # the Macro stays visible
    assert "top_def/u_mid/u_sibling" not in diagram._boxes  # its non-Macro sibling is still correctly hidden


def test_hiding_the_top_level_root_still_keeps_a_deeply_nested_macro_visible(qtbot):
    """Same requirement, but hiding from the very TOP of the design --
    "at any nesting depth" -- the Macro is 2 levels below the hidden
    path here, not a direct child of it."""
    controller, tree, diagram = _wired_with_macro_and_sibling(qtbot)

    tree._toggle_hidden("top_def")

    assert "top_def" in diagram._boxes
    assert "top_def/u_mid" in diagram._boxes
    assert "top_def/u_mid/u_macro" in diagram._boxes
    assert "top_def/u_mid/u_sibling" not in diagram._boxes


def test_a_macro_several_levels_deep_stays_visible_when_hidden_from_an_upper_ancestor(qtbot):
    """"At any nesting depth" -- a 4-level chain (top -> mid -> wrap ->
    macro), hidden from the very top, still shows the Macro at the
    bottom of the chain."""
    macro = Definition(definition_id="macro", base_name="macro_def", language=SourceLanguage.SYSTEMVERILOG, is_black_box=True)
    wrap = Definition(
        definition_id="wrap", base_name="wrap_def", language=SourceLanguage.SYSTEMVERILOG, instances=[Instance(name="u_macro", definition_id="macro")]
    )
    mid = Definition(
        definition_id="mid", base_name="mid_def", language=SourceLanguage.SYSTEMVERILOG, instances=[Instance(name="u_wrap", definition_id="wrap")]
    )
    top = Definition(
        definition_id="top", base_name="top_def", language=SourceLanguage.SYSTEMVERILOG, instances=[Instance(name="u_mid", definition_id="mid")]
    )
    design = Design(definitions={"top": top, "mid": mid, "wrap": wrap, "macro": macro}, top="top")
    controller = HierarchySelectionController()
    tree = HierarchyTreeWidget(controller)
    diagram = HierarchyDiagramView(controller)
    qtbot.addWidget(tree)
    qtbot.addWidget(diagram)
    tree.load(design)
    diagram.load(design)

    tree._toggle_hidden("top_def")

    assert "top_def/u_mid/u_wrap/u_macro" in diagram._boxes


def test_a_macro_cannot_be_hidden_even_via_a_direct_hide_action_targeting_it(qtbot):
    """The Macro's own path, hidden directly (not via an ancestor) --
    must stay visible too, per "a Macro itself must never be hidden"."""
    controller, tree, diagram = _wired_with_macro_and_sibling(qtbot)

    tree._toggle_hidden("top_def/u_mid/u_macro")

    assert "top_def/u_mid/u_macro" in diagram._boxes


def test_unhiding_restores_the_previously_hidden_macro_sibling(qtbot):
    """Confirms this feature composes correctly with the pre-existing
    unhide mechanism -- unhiding the ancestor brings the non-Macro
    sibling back too, exactly as it would have without any Macro
    involved at all."""
    controller, tree, diagram = _wired_with_macro_and_sibling(qtbot)
    tree._toggle_hidden("top_def/u_mid")
    assert "top_def/u_mid/u_sibling" not in diagram._boxes

    tree._toggle_hidden("top_def/u_mid")  # unhide

    assert "top_def/u_mid/u_sibling" in diagram._boxes
    assert "top_def/u_mid/u_macro" in diagram._boxes


# --- Copy Path ------------------------------------------------------------


def test_copy_path_copies_the_full_hierarchy_path_from_the_tree(qtbot):
    controller, tree, diagram = _wired(qtbot)

    tree._copy_path("top/u_core")

    assert QGuiApplication.clipboard().text() == "top/u_core"


def test_copy_path_copies_the_full_hierarchy_path_from_the_diagram(qtbot):
    controller, tree, diagram = _wired(qtbot)

    diagram._copy_path("top/u_core")

    assert QGuiApplication.clipboard().text() == "top/u_core"


def test_copy_path_is_only_offered_for_a_single_tree_target(qtbot):
    controller, tree, diagram = _wired(qtbot)

    single = tree._hide_targets(tree._items_by_path["top/u_core"])
    assert len(single) == 1

    tree._items_by_path["top/u_core"].setSelected(True)
    tree._items_by_path["top/u_dbg"].setSelected(True)
    multi = tree._hide_targets(tree._items_by_path["top/u_core"])
    assert len(multi) == 2
    _deselect_and_collect(diagram)


def test_copy_path_is_only_offered_for_a_single_diagram_target(qtbot):
    controller, tree, diagram = _wired(qtbot)

    single = diagram._hide_targets(diagram._boxes["top/u_core"])
    assert len(single) == 1

    diagram._boxes["top/u_core"].setSelected(True)
    diagram._boxes["top/u_dbg"].setSelected(True)
    multi = diagram._hide_targets(diagram._boxes["top/u_core"])
    assert len(multi) == 2
    _deselect_and_collect(diagram)


def test_copy_path_action_is_disabled_not_omitted_for_a_multi_selection(qtbot):
    """Explicit request: "Copy Path" now follows the SAME "visible,
    greyed out -- not omitted" convention every other multi-select-
    incompatible action in this diagram already establishes, instead of
    disappearing from the menu outright. _build_context_menu() is the
    extracted, directly-testable half of _on_context_menu() (whose own
    menu.exec() is a blocking modal call that can't be driven
    headlessly)."""
    controller, tree, diagram = _wired(qtbot)
    diagram._boxes["top/u_core"].setSelected(True)
    diagram._boxes["top/u_dbg"].setSelected(True)

    _menu, copy_action, _hide_action, _extra, _targets = diagram._build_context_menu(diagram._boxes["top/u_core"])

    assert copy_action.text() == "Copy Path"
    assert copy_action.isEnabled() is False
    _deselect_and_collect(diagram)


def test_copy_path_action_stays_enabled_for_a_single_target(qtbot):
    controller, tree, diagram = _wired(qtbot)

    _menu, copy_action, _hide_action, _extra, _targets = diagram._build_context_menu(diagram._boxes["top/u_core"])

    assert copy_action.isEnabled() is True
    _deselect_and_collect(diagram)


# --- right-click must not clear/modify the current selection --------------
# Real, confirmed bug: Qt's own default QGraphicsScene mouse handling
# clears the ENTIRE scene selection on ANY button press, right-click
# included -- even when the press lands on an item that's already part of
# a multi-selection (reproduced against a bare QGraphicsScene/QGraphicsView
# with zero custom code involved before this fix). mousePressEvent() now
# skips scene-forwarding entirely for a right-click -- see its own
# docstring for why this doesn't affect the context menu itself (a
# genuinely separate mechanism, customContextMenuRequested/contextMenuEvent,
# not wired through mousePressEvent at all).


def test_right_click_on_a_multi_selection_does_not_clear_the_yellow_highlight(qtbot):
    """Explicit regression test: multi-select 2+ blocks (yellow
    highlighted), right-click, confirm the highlight survives the
    press itself, then confirm it ALSO survives a full "open the
    context menu, dismiss via Escape, nothing chosen" round trip."""
    controller, tree, diagram = _wired(qtbot)
    box_a = diagram._boxes["top/u_core"]
    box_b = diagram._boxes["top/u_dbg"]
    box_a.setSelected(True)
    box_b.setSelected(True)
    assert box_a.pen().color().name() == "#ffff00"
    assert box_b.pen().color().name() == "#ffff00"

    pos = diagram.mapFromScene(box_a.sceneBoundingRect().center())
    QTest.mousePress(diagram.viewport(), Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier, pos)
    QTest.mouseRelease(diagram.viewport(), Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier, pos)

    # Survives the real right-click press/release itself.
    assert diagram._selected_paths == {"top/u_core", "top/u_dbg"}
    assert box_a.pen().color().name() == "#ffff00"
    assert box_b.pen().color().name() == "#ffff00"

    # Survives a full "menu opened, dismissed via Escape, nothing chosen"
    # round trip too -- menu.exec() itself can't be driven headlessly
    # (see this module's own note above _build_context_menu()'s tests),
    # so this drives the same extracted, directly-testable half every
    # other context-menu test here already uses; Escape dismissing a
    # real QMenu.exec() returns None, exactly like a rejected/cancelled
    # dialog, with zero action chosen -- the dispatch loop downstream in
    # _on_context_menu() is a no-op for a None result, so nothing further
    # needs to be simulated here to prove the highlight is untouched.
    diagram._build_context_menu(box_a)

    assert diagram._selected_paths == {"top/u_core", "top/u_dbg"}
    assert box_a.pen().color().name() == "#ffff00"
    assert box_b.pen().color().name() == "#ffff00"
    _deselect_and_collect(diagram)


def test_right_click_on_an_unselected_block_does_not_change_the_selection_either(qtbot):
    """Explicit request: "regardless of whether the right-click lands
    on an already-selected item or an unselected one" -- right-clicking
    a DIFFERENT, unselected block while something else is selected must
    not select the clicked block or deselect the existing selection."""
    controller, tree, diagram = _wired(qtbot)
    box_a = diagram._boxes["top/u_core"]
    box_unselected = diagram._boxes["top/u_dbg"]
    box_a.setSelected(True)

    pos = diagram.mapFromScene(box_unselected.sceneBoundingRect().center())
    QTest.mousePress(diagram.viewport(), Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier, pos)
    QTest.mouseRelease(diagram.viewport(), Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier, pos)

    assert diagram._selected_paths == {"top/u_core"}
    assert box_a.pen().color().name() == "#ffff00"
    assert box_unselected.pen().color().name() != "#ffff00"
    _deselect_and_collect(diagram)


# --- Undo/Redo (Ctrl+Z/Ctrl+Y) for Hide/Unhide -----------------------------


def test_undo_reverses_a_hide(qtbot):
    controller, tree, diagram = _wired(qtbot)
    tree._toggle_hidden("top/u_core")
    assert "top/u_core" not in diagram._boxes

    tree.undo_stack.undo()

    assert "top/u_core" in diagram._boxes
    assert tree._hidden_paths == set()


def test_redo_reapplies_an_undone_hide(qtbot):
    controller, tree, diagram = _wired(qtbot)
    tree._toggle_hidden("top/u_core")
    tree.undo_stack.undo()

    tree.undo_stack.redo()

    assert "top/u_core" not in diagram._boxes
    assert tree._hidden_paths == {"top/u_core"}


def test_undo_reverses_a_multi_row_hide_as_a_single_step(qtbot):
    controller, tree, diagram = _wired(qtbot)
    tree._apply_hide_action(["top/u_core", "top/u_dbg"], hide=True)
    assert tree._hidden_paths == {"top/u_core", "top/u_dbg"}

    tree.undo_stack.undo()  # one undo, not one per path

    assert tree._hidden_paths == set()
    assert "top/u_core" in diagram._boxes
    assert "top/u_dbg" in diagram._boxes


def test_undo_reverses_a_cascaded_unhide_including_the_purged_nested_entry(qtbot):
    controller, tree, diagram = _wired(qtbot)
    tree._toggle_hidden("top")
    tree._toggle_hidden("top/u_core")
    tree._toggle_hidden("top")  # unhide -- purges the nested "top/u_core" entry too
    assert tree._hidden_paths == set()

    tree.undo_stack.undo()  # must restore both entries in one step

    assert tree._hidden_paths == {"top", "top/u_core"}


def test_visualize_design_tab_ctrl_z_undoes_the_last_hide(qtbot):
    tab = VisualizeDesignTab()
    qtbot.addWidget(tab)
    tab.load(build_mock_design())
    tab.tree._toggle_hidden("top/u_core")
    assert "top/u_core" not in tab.diagram.current_view._boxes

    tab.undo_shortcut.activated.emit()

    assert "top/u_core" in tab.diagram.current_view._boxes


def test_visualize_design_tab_ctrl_y_redoes_the_last_undone_hide(qtbot):
    tab = VisualizeDesignTab()
    qtbot.addWidget(tab)
    tab.load(build_mock_design())
    tab.tree._toggle_hidden("top/u_core")
    tab.undo_shortcut.activated.emit()
    assert "top/u_core" in tab.diagram.current_view._boxes

    tab.redo_shortcut.activated.emit()

    assert "top/u_core" not in tab.diagram.current_view._boxes


def test_loading_a_new_design_clears_the_undo_stack(qtbot):
    controller, tree, diagram = _wired(qtbot)
    tree._toggle_hidden("top/u_core")
    assert tree.undo_stack.count() > 0

    tree.load(build_mock_design())

    assert tree.undo_stack.count() == 0


def test_hide_does_not_affect_a_sibling_outside_the_hidden_subtree(qtbot):
    controller, tree, diagram = _wired(qtbot)

    tree._toggle_hidden("top/u_core")

    assert "top/u_dbg" in diagram._boxes
    assert tree._items_by_path["top/u_dbg"].foreground(0).color().name() == "#000000"


def test_context_menu_action_label_toggles_between_hide_and_unhide(qtbot):
    controller, tree, diagram = _wired(qtbot)

    assert "top/u_core" not in tree._hidden_paths
    tree._toggle_hidden("top/u_core")
    assert "top/u_core" in tree._hidden_paths
    tree._toggle_hidden("top/u_core")
    assert "top/u_core" not in tree._hidden_paths


# --- Hide/Unhide is multi-select-aware (Ctrl+click, then right-click) ----


def test_hide_targets_the_whole_multi_selection_when_right_clicking_inside_it(qtbot):
    controller, tree, diagram = _wired(qtbot)
    tree._items_by_path["top/u_core"].setSelected(True)
    tree._items_by_path["top/u_dbg"].setSelected(True)

    targets = tree._hide_targets(tree._items_by_path["top/u_core"])

    assert set(targets) == {"top/u_core", "top/u_dbg"}
    _deselect_and_collect(diagram)


def test_hide_targets_only_the_right_clicked_row_when_outside_the_selection(qtbot):
    controller, tree, diagram = _wired(qtbot)
    tree._items_by_path["top/u_core"].setSelected(True)
    tree._items_by_path["top/u_dbg"].setSelected(True)

    targets = tree._hide_targets(tree._items_by_path["top/u_ram"])  # not part of the selection

    assert targets == ["top/u_ram"]
    _deselect_and_collect(diagram)


def test_hiding_a_multi_selection_hides_every_selected_box_in_one_signal(qtbot):
    """The actual end-to-end effect a real "Hide" click on a
    multi-selection produces -- _apply_hide_action() is what
    _on_context_menu() calls after resolving _hide_targets()."""
    controller, tree, diagram = _wired(qtbot)
    tree._items_by_path["top/u_core"].setSelected(True)
    tree._items_by_path["top/u_dbg"].setSelected(True)
    targets = tree._hide_targets(tree._items_by_path["top/u_core"])
    events = []
    controller.visibilityChanged.connect(lambda paths: events.append(paths))

    tree._apply_hide_action(targets, hide=True)

    assert "top/u_core" not in diagram._boxes
    assert "top/u_dbg" not in diagram._boxes
    assert len(events) == 1  # one visibilityChanged emit, not one per path


def test_unhide_a_multi_selection_only_when_every_targeted_row_is_hidden(qtbot):
    """Mixed state (one hidden, one not): _on_context_menu()'s "Unhide"
    label only applies when *every* targeted row is already hidden --
    this is the same all(...) check that method uses, run directly
    against _hide_targets()'s real output."""
    controller, tree, diagram = _wired(qtbot)
    tree._toggle_hidden("top/u_core")  # only this one is hidden so far
    tree._items_by_path["top/u_core"].setSelected(True)
    tree._items_by_path["top/u_dbg"].setSelected(True)

    targets = tree._hide_targets(tree._items_by_path["top/u_core"])
    all_hidden = all(p in tree._hidden_paths for p in targets)

    assert all_hidden is False  # top/u_dbg isn't hidden, so the label must read "Hide", not "Unhide"
    _deselect_and_collect(diagram)


# --- Hide from the Diagram's own right-click menu -------------------------
# _on_context_menu() itself calls QMenu.exec(), a blocking modal call that
# can't be driven headlessly -- same reasoning as the Tree's own context
# menu above (tested via _toggle_hidden directly, never via a simulated
# right-click). _box_at() is the extracted, directly-testable box-lookup
# half; _hide_targets() is the extracted, directly-testable multi-select-
# aware targeting half; controller.hideRequested is the extracted,
# directly-testable "menu selection happened" half.


def test_box_at_resolves_a_viewport_position_to_its_box(qtbot):
    controller, tree, diagram = _wired(qtbot)
    box = diagram._boxes["top/u_core"]
    viewport_pos = diagram.mapFromScene(box.sceneBoundingRect().center())

    assert diagram._box_at(viewport_pos) is box


def test_box_at_walks_up_from_a_child_item_like_the_label_to_its_box(qtbot):
    controller, tree, diagram = _wired(qtbot)
    box = diagram._boxes["top/u_core"]
    label = box.childItems()[0]  # the instance-name QGraphicsSimpleTextItem, parented to the box
    viewport_pos = diagram.mapFromScene(label.mapToScene(label.boundingRect().center()))

    assert diagram._box_at(viewport_pos) is box


def test_box_at_returns_none_for_empty_canvas(qtbot):
    controller, tree, diagram = _wired(qtbot)
    far_outside = diagram.mapFromScene(QPointF(1_000_000, 1_000_000))
    assert diagram._box_at(far_outside) is None


def test_box_at_finds_the_box_underneath_an_unparented_overlay_item(qtbot):
    """Real, confirmed bug (reported against Supply Port on a macro,
    root-caused here in the shared _box_at() itself): power_intent_view's
    own Milestone-2 overlay items (SwitchIconItem/SupplyRailItem in
    diagram_overlay.py) are bare top-level scene items with no parent at
    all (scene.addItem(...), never setParentItem()), at a Z-value above
    InstanceBoxItem's own -- a power switch icon is positioned at a
    domain's own *region* top edge, which can genuinely coincide with a
    member box underneath it (macros included, since macros are exactly
    the kind of instance real designs put in a switched domain).
    _box_at()'s old single-`itemAt()`-based walk gave up entirely
    (returned None -- "no box here") whenever the topmost item at a
    point had no parent to walk up to, even though a real box existed
    directly underneath. Reproduced generically here with a bare
    QGraphicsRectItem (same "no parent, higher Z" shape as the real
    overlay items) rather than depending on the whole Milestone-2
    domain/switch construction pipeline."""
    controller, tree, diagram = _wired(qtbot)
    box = diagram._boxes["top/u_ram"]  # build_mock_design()'s own macro (is_black_box, a leaf)
    rect = box.sceneBoundingRect()

    unparented_overlay = QGraphicsRectItem(QRectF(rect.center().x() - 5, rect.center().y() - 5, 10, 10))
    unparented_overlay.setZValue(3)  # above InstanceBoxItem's own Z_INSTANCE_BOX=1, same as SwitchIconItem
    diagram.scene().addItem(unparented_overlay)

    viewport_pos = diagram.mapFromScene(rect.center())
    assert diagram._box_at(viewport_pos) is box


def test_hide_requested_from_the_diagram_removes_the_box_and_greys_the_tree(qtbot):
    """Simulates choosing 'Hide' from the Diagram's own context menu --
    the part downstream of QMenu.exec(), which real user interaction
    would trigger via _on_context_menu()."""
    controller, tree, diagram = _wired(qtbot)

    controller.hideRequested.emit({"top/u_core"})

    assert "top/u_core" not in diagram._boxes
    assert tree._items_by_path["top/u_core"].foreground(0).color().name() == "#a0a0a0"


def test_hide_requested_for_an_already_hidden_path_does_not_re_emit(qtbot):
    controller, tree, diagram = _wired(qtbot)
    controller.hideRequested.emit({"top/u_core"})
    events = []
    controller.visibilityChanged.connect(lambda _paths: events.append(True))

    controller.hideRequested.emit({"top/u_core"})

    assert events == []


def test_hide_requested_with_multiple_paths_hides_all_of_them_in_one_emit(qtbot):
    controller, tree, diagram = _wired(qtbot)

    controller.hideRequested.emit({"top/u_core", "top/u_dbg"})

    assert "top/u_core" not in diagram._boxes
    assert "top/u_dbg" not in diagram._boxes
    assert tree._items_by_path["top/u_core"].foreground(0).color().name() == "#a0a0a0"
    assert tree._items_by_path["top/u_dbg"].foreground(0).color().name() == "#a0a0a0"


def test_diagram_context_menu_targets_the_whole_multi_selection_when_right_clicking_inside_it(qtbot):
    """Right-clicking a box that's part of the current multi-selection
    must target every selected box, not just the one under the cursor --
    _hide_targets() is the extracted, directly-testable half of
    _on_context_menu() (whose own QMenu.exec() can't be driven
    headlessly)."""
    controller, tree, diagram = _wired(qtbot)
    diagram._boxes["top/u_core"].setSelected(True)
    diagram._boxes["top/u_dbg"].setSelected(True)
    assert diagram._selected_paths == {"top/u_core", "top/u_dbg"}

    assert diagram._hide_targets(diagram._boxes["top/u_core"]) == {"top/u_core", "top/u_dbg"}
    _deselect_and_collect(diagram)


def test_diagram_context_menu_targets_only_the_right_clicked_box_when_outside_the_selection(qtbot):
    controller, tree, diagram = _wired(qtbot)
    diagram._boxes["top/u_core"].setSelected(True)
    diagram._boxes["top/u_dbg"].setSelected(True)

    ram_box = diagram._boxes["top/u_ram"]  # right-clicked, but not part of the selection
    assert diagram._hide_targets(ram_box) == {"top/u_ram"}
    _deselect_and_collect(diagram)


# --- Clear button ---------------------------------------------------------


def test_clear_hidden_unhides_everything_at_once(qtbot):
    controller, tree, diagram = _wired(qtbot)
    tree._toggle_hidden("top/u_core")
    tree._toggle_hidden("top/u_dbg")

    tree.clear_hidden()

    assert tree._hidden_paths == set()
    assert "top/u_core" in diagram._boxes
    assert "top/u_dbg" in diagram._boxes
    for path in ("top", "top/u_core", "top/u_dbg", "top/u_ram"):
        assert tree._items_by_path[path].foreground(0).color().name() == "#000000"


def test_clear_hidden_button_is_present_and_wired(qtbot):
    tab = VisualizeDesignTab()
    qtbot.addWidget(tab)
    tab.load(build_mock_design())

    tab.tree._toggle_hidden("top/u_core")
    assert "top/u_core" not in tab.diagram.current_view._boxes

    tab.clear_hidden_button.click()

    assert "top/u_core" in tab.diagram.current_view._boxes


def test_clear_hidden_is_a_noop_when_nothing_is_hidden(qtbot):
    controller, tree, diagram = _wired(qtbot)
    events = []
    controller.visibilityChanged.connect(lambda _paths: events.append(True))

    tree.clear_hidden()

    assert events == []


# --- Fit View / Zoom --------------------------------------------------


def test_fit_view_button_exists_and_calls_diagram_fit_view(qtbot):
    tab = VisualizeDesignTab()
    qtbot.addWidget(tab)
    tab.load(build_mock_design())

    assert tab.fit_view_button.text() == "Fit View"
    tab.fit_view_button.click()  # must not raise


def test_fit_view_with_no_scene_does_not_raise(qtbot):
    controller = HierarchySelectionController()
    diagram = HierarchyDiagramView(controller)
    qtbot.addWidget(diagram)
    diagram.fit_view()  # no .load() called yet -- no scene


def test_no_zoom_buttons_exist_zoom_is_mouse_wheel_only(qtbot):
    tab = VisualizeDesignTab()
    qtbot.addWidget(tab)
    assert not hasattr(tab, "zoom_in_button")
    assert not hasattr(tab, "zoom_out_button")


def _standalone_diagram(qtbot):
    """A standalone, non-layout-managed HierarchyDiagramView -- unlike
    one embedded in VisualizeDesignTab, resize() here reliably takes
    effect without needing the window shown, which matters for the zoom
    tests below (they depend on real viewport dimensions)."""
    controller = HierarchySelectionController()
    diagram = HierarchyDiagramView(controller)
    qtbot.addWidget(diagram)
    diagram.load(build_mock_design())
    diagram.resize(800, 600)
    qtbot.wait(0)  # let the resize event actually reach the QGraphicsView's internal viewport widget
    return diagram


def test_mouse_wheel_zooms_in_and_out(qtbot):
    diagram = _standalone_diagram(qtbot)
    # Zoom in several steps first so the later zoom-out has room to move
    # without immediately hitting the "whole design already fits" floor
    # (this mock design is small enough to already fit an 800x600
    # viewport near the default scale -- see the dedicated floor tests
    # below for that case specifically).
    for _ in range(6):
        diagram.wheelEvent(_FakeWheelEvent(delta_y=120))
    zoomed_in = diagram.transform().m11()

    diagram.wheelEvent(_FakeWheelEvent(delta_y=-120))
    assert diagram.transform().m11() < zoomed_in


def test_zoom_out_stops_exactly_once_the_whole_design_fits_the_view(qtbot):
    diagram = _standalone_diagram(qtbot)
    for _ in range(4):
        diagram.wheelEvent(_FakeWheelEvent(delta_y=120))
    min_scale = diagram._fit_scale()

    for _ in range(20):  # far more zoom-out ticks than needed to reach the floor
        diagram.wheelEvent(_FakeWheelEvent(delta_y=-120))

    assert diagram.transform().m11() == pytest.approx(min_scale)


def test_zoom_out_never_increases_scale_even_when_already_at_the_floor(qtbot):
    """The regression this guards against: a design that's already
    smaller than the viewport at the default scale has a fit-scale
    *above* 1.0 -- a naive clamp could misread "below the floor" as "snap
    up to it," turning a zoom-out gesture into a zoom-in. Confirmed this
    mock design/viewport combination actually exercises that floor-above-
    default-scale case, not assumed."""
    diagram = _standalone_diagram(qtbot)
    assert diagram._fit_scale() > 1.0

    before = diagram.transform().m11()
    diagram.wheelEvent(_FakeWheelEvent(delta_y=-120))
    assert diagram.transform().m11() == before


def test_diagram_shows_open_hand_cursor_at_rest(qtbot):
    controller = HierarchySelectionController()
    diagram = HierarchyDiagramView(controller)
    qtbot.addWidget(diagram)
    assert diagram.cursor().shape() == Qt.CursorShape.OpenHandCursor


def test_dragging_past_the_threshold_pans_and_shows_closed_hand_cursor(qtbot):
    controller, tree, diagram = _wired(qtbot)

    diagram.mousePressEvent(_mouse_event(QPoint(50, 50), QEvent.Type.MouseButtonPress))
    diagram.mouseMoveEvent(_mouse_event(QPoint(80, 80), QEvent.Type.MouseMove))

    assert diagram._panning is True
    assert diagram.cursor().shape() == Qt.CursorShape.ClosedHandCursor

    diagram.mouseReleaseEvent(_mouse_event(QPoint(80, 80), QEvent.Type.MouseButtonRelease))
    assert diagram._panning is False
    assert diagram.cursor().shape() == Qt.CursorShape.OpenHandCursor


def test_a_press_release_without_real_movement_never_starts_panning(qtbot):
    """A tiny sub-threshold jitter (well within a real click) must not be
    treated as a drag -- otherwise clicking a box to select it would be
    swallowed as an accidental pan."""
    controller, tree, diagram = _wired(qtbot)

    diagram.mousePressEvent(_mouse_event(QPoint(50, 50), QEvent.Type.MouseButtonPress))
    diagram.mouseMoveEvent(_mouse_event(QPoint(51, 51), QEvent.Type.MouseMove))

    assert diagram._panning is False


def test_hierarchy_tree_columns_auto_resize_to_content(qtbot):
    """Both "Instance"/"Module" columns must auto-fit their content
    (same ResizeToContents convention already established for Properties'
    own tables), not stay at whatever fixed/interactive width Qt defaults
    a QTreeWidget's header to."""
    from PySide6.QtWidgets import QHeaderView

    controller, tree, diagram = _wired(qtbot)

    header = tree.header()
    for column in range(header.count()):
        assert header.sectionResizeMode(column) == QHeaderView.ResizeMode.ResizeToContents
