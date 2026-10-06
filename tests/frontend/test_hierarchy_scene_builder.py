"""shared/diagram/hierarchy_scene_builder.py -- the nested "block inside
block" (matrioska) layout, against mock RTL data only."""

from PySide6.QtWidgets import QGraphicsLineItem

from frontend.src.shared.diagram.hierarchy_scene_builder import (
    LEAF_HEIGHT,
    LEAF_WIDTH,
    PADDING,
    TITLE_HEIGHT,
    build_hierarchy_scene,
)
from frontend.src.shared.mock_backend.mock_rtl import build_mock_design
from frontend.src.shared.models.rtl import Definition, Design, Instance, SourceLanguage


def _single_child_chain_design() -> Design:
    """A three-level bottom-up single-child chain (bank -> wrap -> macro),
    the exact shape reported as over-sized in the real aura design
    (u_bank -> u_wrap -> u_macro): each level should tightly wrap its one
    child plus a uniform PADDING margin (and the title strip on top),
    never anything looser."""
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
    return Design(definitions={"macro": macro, "wrap": wrap, "bank": bank}, top="bank")


def test_child_boxes_are_nested_inside_their_parent_box(qtbot):
    _, boxes = build_hierarchy_scene(build_mock_design())

    assert boxes["top/u_core"].parentItem() is boxes["top"]
    assert boxes["top/u_dbg"].parentItem() is boxes["top"]
    assert boxes["top/u_ram"].parentItem() is boxes["top"]
    assert boxes["top"].parentItem() is None


def test_a_child_box_scene_rect_is_fully_contained_within_its_parent(qtbot):
    _, boxes = build_hierarchy_scene(build_mock_design())

    parent_rect = boxes["top"].sceneBoundingRect()
    child_rect = boxes["top/u_core"].sceneBoundingRect()
    assert parent_rect.contains(child_rect)


def test_sibling_boxes_do_not_overlap(qtbot):
    _, boxes = build_hierarchy_scene(build_mock_design())

    core_rect = boxes["top/u_core"].sceneBoundingRect()
    dbg_rect = boxes["top/u_dbg"].sceneBoundingRect()
    ram_rect = boxes["top/u_ram"].sceneBoundingRect()
    assert not core_rect.intersects(dbg_rect)
    assert not core_rect.intersects(ram_rect)
    assert not dbg_rect.intersects(ram_rect)


def test_no_connector_lines_are_drawn_between_levels(qtbot):
    scene, _ = build_hierarchy_scene(build_mock_design())
    assert [item for item in scene.items() if isinstance(item, QGraphicsLineItem)] == []


def test_rtl_boxes_are_white_with_a_normal_pen(qtbot):
    _, boxes = build_hierarchy_scene(build_mock_design())

    core_box = boxes["top/u_core"]
    assert core_box.brush().color().name() == "#ffffff"
    assert core_box.pen().widthF() < 2.0


def test_macro_boxes_are_grey_with_a_thicker_pen(qtbot):
    _, boxes = build_hierarchy_scene(build_mock_design())

    ram_box = boxes["top/u_ram"]
    assert ram_box.brush().color().name() != "#ffffff"
    assert ram_box.pen().widthF() > boxes["top/u_core"].pen().widthF()


def test_a_leaf_definition_produces_a_box_with_no_children(qtbot):
    _, boxes = build_hierarchy_scene(build_mock_design())
    assert boxes["top/u_ram"].childItems() == [] or all(
        not hasattr(item, "path") for item in boxes["top/u_ram"].childItems()
    )  # only its own label child, no nested instance boxes


def test_single_child_container_tightly_wraps_its_child_with_uniform_padding(qtbot):
    """Regression for the real aura design's u_bank -> u_wrap -> u_macro
    chain reported as over-sized: a single-child container's content must
    be exactly its child's size, not inflated by the skyline packer's
    width estimate (which, before the fix, doubled a lone child's width
    because the estimate was computed off unpadded sizes while packing
    used padded ones)."""
    _, boxes = build_hierarchy_scene(_single_child_chain_design())

    macro_box = boxes["bank_def/u_wrap/u_macro"]
    wrap_box = boxes["bank_def/u_wrap"]
    bank_box = boxes["bank_def"]

    assert (macro_box.rect().width(), macro_box.rect().height()) == (LEAF_WIDTH, LEAF_HEIGHT)
    assert (wrap_box.rect().width(), wrap_box.rect().height()) == (LEAF_WIDTH + PADDING * 2, LEAF_HEIGHT + TITLE_HEIGHT + PADDING * 2)

    wrap_content_width = LEAF_WIDTH + PADDING * 2
    wrap_content_height = LEAF_HEIGHT + TITLE_HEIGHT + PADDING * 2
    assert (bank_box.rect().width(), bank_box.rect().height()) == (
        wrap_content_width + PADDING * 2,
        wrap_content_height + TITLE_HEIGHT + PADDING * 2,
    )


def test_single_child_container_centers_the_child_with_padding_margin_on_every_side(qtbot):
    _, boxes = build_hierarchy_scene(_single_child_chain_design())

    wrap_box = boxes["bank_def/u_wrap"]
    macro_box = boxes["bank_def/u_wrap/u_macro"]

    assert macro_box.pos().x() == PADDING
    assert macro_box.pos().y() == TITLE_HEIGHT + PADDING
    assert wrap_box.rect().width() - (macro_box.pos().x() + macro_box.rect().width()) == PADDING
    assert wrap_box.rect().height() - (macro_box.pos().y() + macro_box.rect().height()) == PADDING


# --- extra_top_margin (item 5: reserve room for a Power-Intent-only ---------
# --- overlay item, e.g. a Power Switch icon, without this module ever ------
# --- knowing what that item is) ---------------------------------------------


def test_extra_top_margin_defaults_to_no_reserved_space(qtbot):
    """Every existing caller (hierarchy_view's own Visualize Design tab)
    omits this parameter entirely -- confirms the default behaves
    identically to before this parameter existed."""
    scene_without, boxes_without = build_hierarchy_scene(_single_child_chain_design())
    scene_with_empty, boxes_with_empty = build_hierarchy_scene(_single_child_chain_design(), extra_top_margin={})

    root_without = boxes_without["bank_def"]
    root_with_empty = boxes_with_empty["bank_def"]
    assert root_without.pos() == root_with_empty.pos()
    assert root_without.rect() == root_with_empty.rect()


def test_extra_top_margin_on_the_root_shifts_it_down_without_resizing_it(qtbot):
    baseline_scene, baseline_boxes = build_hierarchy_scene(_single_child_chain_design())
    baseline_root = baseline_boxes["bank_def"]

    _, boxes = build_hierarchy_scene(_single_child_chain_design(), extra_top_margin={"bank_def": 34.0})
    root_box = boxes["bank_def"]

    # Same natural footprint -- margin reserves space AROUND the box, it
    # never inflates the box's own visible rect.
    assert root_box.rect() == baseline_root.rect()
    # Shifted down by exactly the margin, leaving that much genuinely
    # empty scene space above its own top border.
    assert root_box.pos().y() == baseline_root.pos().y() + 34.0
    assert root_box.pos().x() == baseline_root.pos().x()


def test_extra_top_margin_on_a_nested_child_grows_its_parent_to_still_contain_it(qtbot):
    baseline_scene, baseline_boxes = build_hierarchy_scene(_single_child_chain_design())
    baseline_bank = baseline_boxes["bank_def"]
    baseline_wrap = baseline_boxes["bank_def/u_wrap"]

    _, boxes = build_hierarchy_scene(_single_child_chain_design(), extra_top_margin={"bank_def/u_wrap": 34.0})
    bank_box = boxes["bank_def"]
    wrap_box = boxes["bank_def/u_wrap"]

    # The child's own visible rect is unaffected -- only its POSITION
    # within its parent shifts down.
    assert wrap_box.rect() == baseline_wrap.rect()
    assert wrap_box.pos().y() == baseline_wrap.pos().y() + 34.0
    # The parent recursively grew tall enough to still fully contain the
    # shifted child -- no separate "resize siblings" pass needed, this
    # falls out of the ordinary recursive packing/sizing.
    assert bank_box.rect().height() == baseline_bank.rect().height() + 34.0
    assert wrap_box.pos().y() + wrap_box.rect().height() <= bank_box.rect().height()


def test_extra_top_margin_for_an_unrelated_path_does_not_affect_this_box(qtbot):
    _, boxes = build_hierarchy_scene(_single_child_chain_design(), extra_top_margin={"some/unrelated/path": 34.0})
    baseline_scene, baseline_boxes = build_hierarchy_scene(_single_child_chain_design())

    assert boxes["bank_def"].rect() == baseline_boxes["bank_def"].rect()
    assert boxes["bank_def"].pos() == baseline_boxes["bank_def"].pos()
