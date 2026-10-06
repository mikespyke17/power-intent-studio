"""Item 3 -- "make room" diagram layout: extends the SAME shared
hierarchy_scene_builder.py mechanism already built for Power Switch's
own "blocks don't move to fit the icon" fix (extra_top_margin) with two
siblings (extra_left_margin, extra_interior_top -- see that module's own
docstrings for the EXTERIOR-vs-INTERIOR distinction), reused (not
reimplemented per-feature) by Supply Port/Internal Supply and Isolation.

A) Supply Port/Internal Supply: a container box's own children shift
   DOWN to make room near the top; a LEAF box grows itself instead --
   both handled uniformly by extra_interior_top (see
   _extra_interior_top()'s own docstring, power_intent_diagram_view.py).
B) Isolation: an EXTERNALLY-placed one (Location=parent/fanout) shifts
   its boundary block(s) RIGHT (extra_left_margin); an INTERNALLY-placed
   one (Location=self) grows the block (extra_interior_top, shared
   with A)."""

from PySide6.QtGui import QColor

from frontend.src.power_intent_view.power_domain_overlay import PowerDomainPlacement, SupplySetPlacement, _LABEL_MARGIN, build_domain_info_box_overlay
from frontend.src.power_intent_view.power_intent_tab import PowerIntentTab
from frontend.src.power_intent_view.supply_port_overlay import INTERIOR_TOP_MARGIN_FOR_SUPPLY_PORT
from frontend.src.shared.mock_backend import build_mock_design
from frontend.src.shared.models.rtl import Definition, Design, Instance, SourceLanguage


def _stacked_siblings_design() -> Design:
    """Two leaf siblings stacked with zero extra slack -- same shape
    test_power_switch_diagram.py's own _stacked_sibling_design()
    establishes, reused here under a slightly different name since this
    is a genuinely separate test file."""
    leaf = Definition(definition_id="leaf", base_name="leaf_def", language=SourceLanguage.VERILOG)
    top = Definition(
        definition_id="top",
        base_name="top_def",
        language=SourceLanguage.VERILOG,
        instances=[Instance(name="u_upper", definition_id="leaf"), Instance(name="u_lower", definition_id="leaf")],
    )
    return Design(definitions={"leaf": leaf, "top": top}, top="top")


# --- (a) Supply Port/Internal Supply on a block WITH children -------------


def test_adding_a_supply_port_to_a_container_pushes_its_children_down_without_overlap(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    top_box = view._boxes["top"]  # has children: u_core, u_dbg, u_ram, u_fwd
    child_positions_before = {path: box.pos() for path, box in view._boxes.items() if path != "top" and path.startswith("top/")}

    view.add_supply_port(top_box, "VDD1", QColor("#123456"))

    # Every child shifted DOWN by the reserved amount -- not left in place
    # (which would mean the port just silently overlapped them instead).
    for path, pos_before in child_positions_before.items():
        box = view._boxes[path]
        assert box.pos().y() == pos_before.y() + INTERIOR_TOP_MARGIN_FOR_SUPPLY_PORT
        assert box.pos().x() == pos_before.x()  # only the Y axis moves for this mechanism
    # No two boxes overlap after the shift.
    boxes = list(view._boxes.values())
    for i, a in enumerate(boxes):
        for b in boxes[i + 1 :]:
            if a.parentItem() is not b.parentItem():
                continue  # only compare boxes that are actual siblings
            assert not a.sceneBoundingRect().intersects(b.sceneBoundingRect())


# --- (b) Supply Port/Internal Supply on a LEAF block ------------------------


def test_adding_a_supply_port_to_a_leaf_grows_the_block_itself(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    leaf_box = view._boxes["top/u_dbg"]  # a real leaf -- no children
    height_before = leaf_box.rect().height()

    view.add_supply_port(leaf_box, "VDD1", QColor("#123456"))

    assert leaf_box.rect().height() == height_before + INTERIOR_TOP_MARGIN_FOR_SUPPLY_PORT
    _assert_no_sibling_overlap(view, "top/u_dbg")


def test_adding_an_internal_supply_to_a_leaf_grows_the_block_itself(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    leaf_box = view._boxes["top/u_dbg"]
    height_before = leaf_box.rect().height()

    view.add_internal_supply(leaf_box, "VDD1", QColor("#123456"))

    assert leaf_box.rect().height() == height_before + INTERIOR_TOP_MARGIN_FOR_SUPPLY_PORT


def test_leaf_block_instance_name_label_recenters_after_growing(qtbot):
    """Bug fix, explicit request: a leaf block's own instance-name label
    is centered ONCE, at construction time, from whatever rect() the
    box had back then (hierarchy_scene_builder.py's own InstanceBoxItem).
    Inserting a new element that grows the SAME leaf block (the "make
    room" mechanism exercised above) used to leave the label glued to
    its OLD position, no longer centered in the now-taller box."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    leaf_box = view._boxes["top/u_dbg"]
    label = leaf_box._label

    view.add_supply_port(leaf_box, "VDD1", QColor("#123456"))

    rect = leaf_box.rect()
    label_rect = label.boundingRect()
    assert label.pos().x() == rect.center().x() - label_rect.width() / 2
    assert label.pos().y() == rect.center().y() - label_rect.height() / 2


def test_power_domain_name_label_repositions_after_growing(qtbot):
    """Bug fix, explicit request -- same root cause/fix shape as the
    instance-name label above and the earlier Power Switch misplacement
    fix: the Power Domain's own merged name+supply-set info box
    (power_domain_overlay.py's own DomainInfoBoxItem, superseding the
    old standalone DomainLabelItem) is anchored to the bottom-right
    corner of its box ONCE, at construction time -- inserting a new
    element that grows the SAME block used to leave the box glued to
    its OLD position, no longer flush with the now-larger box's own
    bottom-right corner."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    leaf_box = view._boxes["top/u_dbg"]
    view._domain_state.domains["d1"] = PowerDomainPlacement(id="d1", owner_path="top/u_dbg", name="PD1", color=QColor("#ff0000"))
    view._domain_info_boxes = build_domain_info_box_overlay(
        view._boxes, view._domain_state.domains, view._domain_state.supply_sets, view._supply_port_icons
    )
    info_box = view._domain_info_boxes["d1"][0]

    view.add_supply_port(leaf_box, "VDD1", QColor("#123456"))

    rect = leaf_box.rect()
    size = info_box._content_size()
    assert info_box.pos().x() == rect.x() + rect.width() - size.width() - _LABEL_MARGIN
    assert info_box.pos().y() == rect.y() + rect.height() - size.height() - _LABEL_MARGIN


def _assert_no_sibling_overlap(view, changed_path: str) -> None:
    changed_box = view._boxes[changed_path]
    for path, box in view._boxes.items():
        if path == changed_path or box.parentItem() is not changed_box.parentItem():
            continue
        assert not box.sceneBoundingRect().intersects(changed_box.sceneBoundingRect()), f"{path} overlaps {changed_path}"


# --- (c) externally-placed Isolation shifts its boundary block(s) right ----


def test_externally_placed_isolation_shifts_its_block_right_without_overlap(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(_stacked_siblings_design())
    view = tab.diagram._view
    lower_box = view._boxes["top_def/u_lower"]
    upper_box = view._boxes["top_def/u_upper"]
    upper_pos_before = upper_box.pos()
    domain = PowerDomainPlacement(id="d1", owner_path="top_def/u_lower", name="PD_LOWER", color=QColor("#ff0000"))
    view._domain_state.domains["d1"] = domain
    ss1 = SupplySetPlacement(id="ss1", name="SS1", power="VDD", owner_paths=["top_def/u_lower"])
    view._domain_state.supply_sets["ss1"] = ss1

    view.create_isolation(lower_box, "ISO1", "d1", "ss1", None, None, "low", "parent", "ctrl", "high", ["top_def/u_lower/clk"])

    # A sibling packed alongside u_lower must not have been forced to
    # overlap it once u_lower's own reported footprint grew to the left.
    for path, box in view._boxes.items():
        if path in ("top_def/u_lower", "top_def"):
            continue
        assert not box.sceneBoundingRect().intersects(lower_box.sceneBoundingRect()), f"{path} overlaps the widened u_lower"
    _deselect(tab)


# --- (d) internally-placed Isolation grows the block ------------------------


def test_internally_placed_isolation_grows_the_block_without_clipping(qtbot):
    """A domain occupying this same block ALSO reserves its own bottom
    margin for its merged info box (build_domain_info_box_overlay()'s
    own "always renders, even with no Primary yet" guarantee, see
    power_domain_overlay.py) -- this test's own injected domain
    (scaffolding for create_isolation()'s domain_id requirement, not
    itself under test) unavoidably contributes that margin too. Explicit
    combined-scenario coverage per the CONSOLIDATE task: Isolation's own
    interior-TOP growth and the domain box's own interior-BOTTOM growth
    must both apply, independently, with neither shadowing the other."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(_stacked_siblings_design())
    view = tab.diagram._view
    lower_box = view._boxes["top_def/u_lower"]
    height_before = lower_box.rect().height()
    domain = PowerDomainPlacement(id="d1", owner_path="top_def/u_lower", name="PD_LOWER", color=QColor("#ff0000"))
    view._domain_state.domains["d1"] = domain
    ss1 = SupplySetPlacement(id="ss1", name="SS1", power="VDD", owner_paths=["top_def/u_lower"])
    view._domain_state.supply_sets["ss1"] = ss1

    placement = view.create_isolation(lower_box, "ISO1", "d1", "ss1", None, None, "low", "self", "ctrl", "high", ["top_def/u_lower/clk"])

    from frontend.src.power_intent_view.isolation_overlay import INTERIOR_TOP_MARGIN_FOR_ISOLATION
    from frontend.src.power_intent_view.power_domain_overlay import domain_info_box_required_height

    domain_box_margin = domain_info_box_required_height(view._domain_state.domains["d1"], view._domain_state.supply_sets)
    assert lower_box.rect().height() == height_before + INTERIOR_TOP_MARGIN_FOR_ISOLATION + domain_box_margin
    # The badge itself must land INSIDE the (now-taller) block's own bounds, not clipped past its edge.
    icon = view._isolation_icons[placement.id][0]
    icon_rect_in_box = icon.mapRectToItem(lower_box, icon.boundingRect())
    assert lower_box.rect().contains(icon_rect_in_box)
    _deselect(tab)


def test_room_reservation_is_released_after_deleting_the_isolation(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(_stacked_siblings_design())
    view = tab.diagram._view
    lower_box = view._boxes["top_def/u_lower"]
    height_before = lower_box.rect().height()
    domain = PowerDomainPlacement(id="d1", owner_path="top_def/u_lower", name="PD_LOWER", color=QColor("#ff0000"))
    view._domain_state.domains["d1"] = domain
    ss1 = SupplySetPlacement(id="ss1", name="SS1", power="VDD", owner_paths=["top_def/u_lower"])
    view._domain_state.supply_sets["ss1"] = ss1
    placement = view.create_isolation(lower_box, "ISO1", "d1", "ss1", None, None, "low", "self", "ctrl", "high", ["top_def/u_lower/clk"])
    assert lower_box.rect().height() != height_before

    view.delete_isolation(placement.id)

    # Only Isolation's OWN reserved margin is released -- the domain's
    # own merged info box (still present; the domain itself was never
    # removed) keeps reserving its own bottom margin regardless.
    from frontend.src.power_intent_view.power_domain_overlay import domain_info_box_required_height

    domain_box_margin = domain_info_box_required_height(view._domain_state.domains["d1"], view._domain_state.supply_sets)
    assert lower_box.rect().height() == height_before + domain_box_margin
    _deselect(tab)


def _deselect(tab) -> None:
    if tab.diagram._view.scene() is not None:
        tab.diagram._view.scene().clearSelection()
