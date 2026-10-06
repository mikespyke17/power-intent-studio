"""Power Domain creation on the Power Intent tab's Diagram: right-click a
hierarchy (never a macro) for "Create Power Domain" -- a dialog for name/
colour/transparency plus primary (required)/secondary Supply Set
selection, with inline "create new" for both a Supply Set and, within
it, a supply name. Once created, every non-macro box in the domain's
own subtree is recoloured to match, except a descendant hierarchy that
already owns its own (nested) domain -- that one keeps its own colour.
A box that already owns a domain offers "Edit Power Domain" instead of
"Create Power Domain". Diagram-only (see power_domain_overlay.py's own
module docstring) -- exercised against mock RTL only, no
PowerIntentDesign/MappedDesign involved.
"""

import gc

import pytest
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QDialog, QMenu, QMessageBox

from frontend.src.power_intent_view.add_power_supply_dialog import AddPowerSupplyDialog
from frontend.src.power_intent_view.create_power_domain_dialog import CreatePowerDomainDialog, CreateSupplySetDialog
from frontend.src.power_intent_view.power_domain_overlay import (
    PowerDomainDiagramState,
    PowerDomainPlacement,
    SupplySetPlacement,
    apply_domain_coloring,
    copy_power_domain_diagram_state,
    find_domain_for_owner,
    find_effective_domain_for_path,
    restore_power_domain_diagram_state,
    supply_set_name_used_by_another_domain,
    supply_set_with_duplicate_pin_combination,
    supply_sets_for_owner,
)
from frontend.src.power_intent_view.power_intent_tab import PowerIntentTab
from frontend.src.power_intent_view.supply_port_overlay import (
    known_supply_names,
    known_supply_names_for_owner,
    SupplyPortDiagramState,
    SupplyPortIconItem,
    SupplyPortPlacement,
)
from frontend.src.shared.mock_backend import build_mock_design
from frontend.src.shared.widgets import message_box


def _deselect_and_collect(tab: PowerIntentTab) -> None:
    if tab.diagram._view.scene() is not None:
        tab.diagram._view.scene().clearSelection()
    gc.collect()


def _warnings(monkeypatch):
    """Captures every QMessageBox.warning() call made during a test
    instead of blocking on a real modal -- accept()/CreateSupplySetDialog's
    own validation call these directly (not through a Qt signal), so patching the
    class attribute directly is safe here (no shiboken signature-
    introspection recursion risk -- see the "Add New..." tests below
    for the one gesture where that risk is real, and why direct-method-
    call sidesteps it there too)."""
    calls = []
    monkeypatch.setattr(
        "frontend.src.power_intent_view.create_power_domain_dialog.message_box.warning",
        lambda *args, **kwargs: calls.append(args[1:]) or None,
    )
    return calls


# --- multi-select: reuses the EXISTING Ctrl+click mechanism, confirmed -----
# empirically to already drive the yellow-fluo border highlight on every
# selected box simultaneously (HierarchySelectionController.selectionChanged
# already carries a frozenset[str], and InstanceBoxItem.set_highlighted()'s
# own pipeline was already generic over however many paths that set holds --
# see hierarchy_diagram_view.py's own module docstring). No new selection
# mechanism needed for multi-hierarchy Power Domain creation -- confirmed via
# explicit follow-up rather than assumed.


def test_multi_selecting_two_boxes_highlights_both_simultaneously(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box_a = view._boxes["top/u_core"]
    box_b = view._boxes["top/u_dbg"]

    # setSelected(True) on both, without clearing between -- the same end
    # state a real Ctrl+click-to-toggle gesture produces (Qt's own native
    # QGraphicsScene handling, confirmed already unmodified/unhandled here).
    box_a.setSelected(True)
    box_b.setSelected(True)

    assert box_a.isSelected() is True
    assert box_b.isSelected() is True
    assert box_a.pen().color().name() == "#ffff00"
    assert box_b.pen().color().name() == "#ffff00"
    assert {i.path for i in view.scene().selectedItems()} == {"top/u_core", "top/u_dbg"}
    _deselect_and_collect(tab)


def test_selecting_a_new_box_without_ctrl_replaces_the_previous_selection(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box_a = view._boxes["top/u_core"]
    box_b = view._boxes["top/u_dbg"]
    box_a.setSelected(True)
    box_b.setSelected(True)

    # A plain (non-additive) selection change -- Qt's own scene-level
    # clearSelection() + setSelected(True), the same shape a real plain
    # (no-Ctrl) click drives natively.
    view.scene().clearSelection()
    box_a.setSelected(True)

    assert box_a.isSelected() is True
    assert box_b.isSelected() is False
    assert box_b.pen().color().name() != "#ffff00"
    _deselect_and_collect(tab)


# --- data model --------------------------------------------------------------


def test_copy_power_domain_diagram_state_is_a_real_independent_copy():
    state = PowerDomainDiagramState()
    state.domains["d1"] = PowerDomainPlacement(id="d1", owner_path="top", name="PD1", color=QColor("#ff0000"), secondary_supply_sets=["s1"])
    state.supply_sets["s1"] = SupplySetPlacement(id="s1", name="SS1", power="VDD")

    snapshot = copy_power_domain_diagram_state(state)
    state.domains["d1"].name = "MUTATED"
    state.domains["d1"].secondary_supply_sets.append("s2")
    state.supply_sets["s1"].power = "MUTATED"

    assert snapshot.domains["d1"].name == "PD1"
    assert snapshot.domains["d1"].secondary_supply_sets == ["s1"]
    assert snapshot.supply_sets["s1"].power == "VDD"


def test_restore_power_domain_diagram_state_mutates_in_place_not_by_reference():
    state = PowerDomainDiagramState()
    state.domains["d1"] = PowerDomainPlacement(id="d1", owner_path="top", name="PD1", color=QColor("#ff0000"))
    snapshot = copy_power_domain_diagram_state(state)
    same_object = state

    state.domains.clear()
    restore_power_domain_diagram_state(state, snapshot)

    assert state is same_object  # never replaced, only mutated
    assert "d1" in state.domains


def test_known_supply_names_covers_both_user_and_pg_pin_names():
    state = SupplyPortDiagramState()
    state.supply_port_placements["a"] = SupplyPortPlacement(id="a", owner_path="top", name="VDD_USER", color=QColor("#ff0000"))
    state.pg_pin_placements["b"] = SupplyPortPlacement(id="b", owner_path="top/u_ram", name="VDD", color=QColor("#9e9e9e"), fixed=True)

    assert known_supply_names(state) == ["VDD", "VDD_USER"]  # sorted


def test_known_supply_names_for_owner_is_scoped_to_one_hierarchy():
    state = SupplyPortDiagramState()
    state.supply_port_placements["a"] = SupplyPortPlacement(id="a", owner_path="top", name="VDD_TOP", color=QColor("#ff0000"))
    state.supply_port_placements["b"] = SupplyPortPlacement(id="b", owner_path="top/u_core", name="VDD_CORE", color=QColor("#00ff00"))
    state.pg_pin_placements["c"] = SupplyPortPlacement(id="c", owner_path="top/u_ram", name="VDD", color=QColor("#9e9e9e"), fixed=True)

    assert known_supply_names_for_owner(state, "top") == ["VDD_TOP"]
    assert known_supply_names_for_owner(state, "top/u_core") == ["VDD_CORE"]
    assert known_supply_names_for_owner(state, "top/does_not_exist") == []


def test_find_domain_for_owner_matches_only_the_exact_owner_path():
    domains = {"d1": PowerDomainPlacement(id="d1", owner_path="top/u_core", name="PD_CORE", color=QColor("#ff0000"))}

    assert find_domain_for_owner(domains, "top/u_core") is domains["d1"]
    assert find_domain_for_owner(domains, "top") is None  # ancestor -- not the owner itself
    assert find_domain_for_owner(domains, "top/u_core/child") is None  # descendant -- not the owner itself


def test_find_effective_domain_for_path_matches_owner_and_every_descendant():
    domains = {"d1": PowerDomainPlacement(id="d1", owner_path="top/u_core", name="PD_CORE", color=QColor("#ff0000"))}

    assert find_effective_domain_for_path(domains, "top/u_core") is domains["d1"]
    assert find_effective_domain_for_path(domains, "top/u_core/child") is domains["d1"]
    assert find_effective_domain_for_path(domains, "top") is None  # ancestor -- not covered
    assert find_effective_domain_for_path(domains, "top/u_dbg") is None  # unrelated sibling


def test_find_effective_domain_for_path_picks_the_deepest_covering_domain():
    """Same "deepest owner wins" rule apply_domain_coloring() already
    applies when actually recolouring boxes -- this is the same rule as
    a pure lookup, order-independent (dict iteration order doesn't
    matter, same as apply_domain_coloring()'s own precedent)."""
    outer = PowerDomainPlacement(id="d1", owner_path="top", name="OUTER", color=QColor("#ff0000"))
    inner = PowerDomainPlacement(id="d2", owner_path="top/u_core", name="INNER", color=QColor("#0000ff"))

    assert find_effective_domain_for_path({"d1": outer, "d2": inner}, "top/u_core") is inner
    assert find_effective_domain_for_path({"d2": inner, "d1": outer}, "top/u_core") is inner  # order-independent
    assert find_effective_domain_for_path({"d1": outer, "d2": inner}, "top/u_dbg") is outer  # only covered by outer
    assert find_effective_domain_for_path({"d1": outer, "d2": inner}, "top") is outer


# --- InstanceBoxItem.set_domain_color() / apply_domain_coloring() -----------


def test_apply_domain_coloring_cascades_to_descendants_but_never_a_macro(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    domain = PowerDomainPlacement(id="d1", owner_path="top", name="PD_TOP", color=QColor("#ff0000"), transparency=0.3)

    apply_domain_coloring(view._boxes, {"d1": domain})

    for path in ("top", "top/u_core", "top/u_dbg"):
        assert view._boxes[path].brush().color().name() == "#ff0000"
        assert abs(view._boxes[path].brush().color().alpha() - round((1 - 0.3) * 255)) <= 1
    macro_box = view._boxes["top/u_ram"]
    assert macro_box.brush().color().name() == "#c8c8c8"  # untouched, macro grey
    _deselect_and_collect(tab)


def test_apply_domain_coloring_only_affects_the_domains_own_subtree(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    domain = PowerDomainPlacement(id="d1", owner_path="top/u_core", name="PD_CORE", color=QColor("#00ff00"))

    apply_domain_coloring(view._boxes, {"d1": domain})

    assert view._boxes["top/u_core"].brush().color().name() == "#00ff00"
    assert view._boxes["top"].brush().color().name() == "#ffffff"  # ancestor, not in the subtree -- untouched
    _deselect_and_collect(tab)


def test_apply_domain_coloring_resets_boxes_no_longer_covered_by_any_domain(qtbot):
    """Called once per rebuild -- must not leave a stale colour on a box
    whose domain was since deleted (or renamed away from it)."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    domain = PowerDomainPlacement(id="d1", owner_path="top/u_core", name="PD_CORE", color=QColor("#00ff00"))
    apply_domain_coloring(view._boxes, {"d1": domain})
    assert view._boxes["top/u_core"].brush().color().name() == "#00ff00"

    apply_domain_coloring(view._boxes, {})  # domain gone

    assert view._boxes["top/u_core"].brush().color().name() == "#ffffff"
    _deselect_and_collect(tab)


def test_a_nested_domain_excludes_its_own_subtree_from_the_ancestor_domains_coloring(qtbot):
    """Explicit request: a new domain's coloring "includes all the sub
    hierarchy and excludes hierarchies which already have a power
    domain defined" -- regardless of dict/creation order, the deeper
    (more specific) domain always wins on the overlap."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    outer = PowerDomainPlacement(id="d1", owner_path="top", name="OUTER", color=QColor("#ff0000"))
    inner = PowerDomainPlacement(id="d2", owner_path="top/u_core", name="INNER", color=QColor("#0000ff"))

    # Insertion order deliberately outer-then-inner AND inner-then-outer
    # both produce the same result -- the tie-break is by owner_path
    # depth, not dict iteration order.
    apply_domain_coloring(view._boxes, {"d1": outer, "d2": inner})
    assert view._boxes["top/u_core"].brush().color().name() == "#0000ff"  # inner (deeper) wins
    assert view._boxes["top/u_dbg"].brush().color().name() == "#ff0000"  # only covered by outer

    apply_domain_coloring(view._boxes, {"d2": inner, "d1": outer})
    assert view._boxes["top/u_core"].brush().color().name() == "#0000ff"  # still inner, order-independent
    assert view._boxes["top/u_dbg"].brush().color().name() == "#ff0000"
    _deselect_and_collect(tab)


def test_a_nested_domains_rendered_fill_does_not_visually_blend_with_its_ancestors(qtbot):
    """Bug fix regression: an inner Power Domain's own translucent fill
    used to visibly BLEED into the outer (ancestor) Power Domain's own
    colour -- e.g. a green inner block rendered as a green/blue BLEND,
    not pure green, because Qt paints a container's own full-rect fill
    UNDER its nested children, and two translucent fills stacked
    composite together. The underlying brush.color() assignment was
    always correct (see the sibling test above) -- this is a genuine
    RENDER-time check, sampling actual painted pixels, not just the
    assigned brush colour, to confirm InstanceBoxItem.paint() itself
    (hierarchy_scene_builder.py) no longer lets an ancestor's fill show
    through underneath a descendant's own fill."""
    from PySide6.QtCore import Qt, QRectF
    from PySide6.QtGui import QImage, QPainter

    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    outer = PowerDomainPlacement(id="d1", owner_path="top", name="OUTER", color=QColor("#0000ff"), transparency=0.9)
    inner = PowerDomainPlacement(id="d2", owner_path="top/u_core", name="INNER", color=QColor("#00ff00"), transparency=0.9)
    apply_domain_coloring(view._boxes, {"d1": outer, "d2": inner})

    outer_box = view._boxes["top"]
    inner_box = view._boxes["top/u_core"]
    scene_rect = outer_box.sceneBoundingRect()

    image = QImage(int(scene_rect.width()), int(scene_rect.height()), QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.white)
    painter = QPainter(image)
    view.scene().render(painter, QRectF(image.rect()), scene_rect)
    painter.end()

    def sample(scene_point) -> QColor:
        local = scene_point - scene_rect.topLeft()
        return image.pixelColor(int(local.x()), int(local.y()))

    # A point well inside the inner (green) box, away from its own label
    # text -- near its own top-left corner.
    inner_rect = inner_box.sceneBoundingRect()
    inner_point = inner_rect.topLeft() + (inner_rect.bottomRight() - inner_rect.topLeft()) * 0.1
    inner_pixel = sample(inner_point)

    # A point inside the outer (blue) box's own area, well outside the
    # inner box's own rect entirely.
    outer_point = scene_rect.topLeft() + (scene_rect.bottomRight() - scene_rect.topLeft()) * 0.05
    outer_pixel = sample(outer_point)

    # Pure green-over-white at 10% opacity: R and B channels drop
    # together, G stays essentially untouched -- the whole point of
    # this assertion is that the BLUE channel here must match a clean
    # green tint, not sit measurably lower (which is what a green-over-
    # blue-tinted-white blend would produce -- see this fix's own
    # commit for the exact blend-formula verification done before
    # writing this test).
    assert inner_pixel.green() > inner_pixel.red() + 10
    assert abs(inner_pixel.red() - inner_pixel.blue()) <= 3  # R and B are equal for a pure green tint over white/grey
    # Pure green-over-white @ alpha 0.1 lands the B channel at ~229 (0.9*255);
    # a green-over-blue-tinted-white BLEND (the bug) would instead measure
    # ~207 -- a wide, unambiguous margin between the two.
    assert inner_pixel.blue() > 220

    # Pure blue-over-white at 10% opacity: R and G channels drop
    # together, B stays essentially untouched.
    assert outer_pixel.blue() > outer_pixel.red() + 10
    assert abs(outer_pixel.red() - outer_pixel.green()) <= 3
    _deselect_and_collect(tab)


# --- context menu: Create disabled for macros/already-owned, Edit enabled ----


def test_create_power_domain_is_offered_and_enabled_for_a_non_macro_box(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top/u_core"]

    menu = QMenu()
    actions = view._extra_context_menu_actions(menu, box)

    create_action = next(a for a, _cb in actions if a.text() == "Create Power Domain")
    edit_action = next(a for a, _cb in actions if a.text() == "Edit Power Domain")
    assert create_action.isEnabled() is True
    assert edit_action.isEnabled() is False


def test_create_power_domain_is_disabled_for_a_macro_box(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top/u_ram"]  # is_black_box=True

    menu = QMenu()
    actions = view._extra_context_menu_actions(menu, box)

    create_action = next(a for a, _cb in actions if a.text() == "Create Power Domain")
    assert create_action.isEnabled() is False


def test_create_is_disabled_and_edit_and_remove_are_enabled_once_a_box_owns_a_domain(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view.create_power_domain(view._boxes["top/u_core"], "PD_CORE", QColor("#4caf50"), 0.5, None, [], {})

    menu = QMenu()
    actions = view._extra_context_menu_actions(menu, view._boxes["top/u_core"])
    create_action = next(a for a, _cb in actions if a.text() == "Create Power Domain")
    edit_action = next(a for a, _cb in actions if a.text() == "Edit Power Domain")
    remove_action = next(a for a, _cb in actions if a.text() == "Remove Power Domain")

    assert create_action.isEnabled() is False
    assert edit_action.isEnabled() is True
    assert remove_action.isEnabled() is True
    _deselect_and_collect(tab)


def test_remove_power_domain_is_disabled_when_the_box_owns_no_domain(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view

    menu = QMenu()
    actions = view._extra_context_menu_actions(menu, view._boxes["top/u_dbg"])
    remove_action = next(a for a, _cb in actions if a.text() == "Remove Power Domain")

    assert remove_action.isEnabled() is False


def test_context_menu_groups_supply_port_and_power_domain_actions_with_separators(qtbot):
    """Explicit request: group the diagram's own context-menu additions
    into their own sections rather than one flat run of actions."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view

    menu = QMenu()
    view._extra_context_menu_actions(menu, view._boxes["top/u_core"])

    kinds = [("separator" if a.isSeparator() else a.text()) for a in menu.actions()]
    assert kinds == [
        "Inspect Source File",  # explicit request: directly below the base class's own Hide, no separator
        "separator",
        "Add Port Attribute",
        "Edit Port Attribute",
        "separator",
        "Create Power Domain",
        "Edit Power Domain",
        "Remove Power Domain",
        "separator",
        "Add Power Supply",
        "Add Internal Supply",
        "Add Virtual Supply",
        "separator",
        "Add Retention",
        "separator",
        "Add Isolation",
        "separator",
        "Add Level Shifter",
        "separator",
        "Load Power Intent",
        "Edit Power Intent",
        "Remove Power Intent",
    ]


def test_a_descendant_of_a_domain_owning_box_can_still_create_its_own_nested_domain(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view.create_power_domain(view._boxes["top"], "PD_TOP", QColor("#ff0000"), 0.5, None, [], {})

    menu = QMenu()
    actions = view._extra_context_menu_actions(menu, view._boxes["top/u_core"])
    create_action = next(a for a, _cb in actions if a.text() == "Create Power Domain")

    assert create_action.isEnabled() is True  # not the owner itself -- still creatable
    _deselect_and_collect(tab)


# --- create_power_domain() core method ----------------------------------------


def test_create_power_domain_creates_a_placement_and_colors_its_subtree(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top/u_core"]

    placement = view.create_power_domain(box, "PD_CORE", QColor("#4caf50"), 0.5, None, [], {})

    assert placement.owner_path == "top/u_core"
    assert placement.id in view._domain_state.domains
    assert view._boxes["top/u_core"].brush().color().name() == "#4caf50"
    _deselect_and_collect(tab)


def test_create_power_domain_persists_new_supply_sets_from_the_dialog(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top/u_core"]
    new_set = SupplySetPlacement(id="s1", name="SS_NEW", power="VDD", ground="VSS")

    placement = view.create_power_domain(box, "PD_CORE", QColor("#4caf50"), 0.5, "s1", [], {"s1": new_set})

    assert "s1" in view._domain_state.supply_sets
    assert placement.primary_supply_set == "s1"
    _deselect_and_collect(tab)


def test_create_power_domain_transparency_is_always_fixed_at_80_percent(qtbot):
    """Explicit request, supersedes the earlier user-adjustable 50%
    default: transparency is no longer configurable at all -- every
    Power Domain gets the same fixed 80% regardless of what's passed
    in to create_power_domain() (kept as a parameter only for the huge
    pre-existing direct-call test surface; the real, authoritative
    value always comes from DOMAIN_FIXED_TRANSPARENCY)."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view

    placement = view.create_power_domain(view._boxes["top/u_core"], "PD_CORE", QColor("#4caf50"), 0.5, None, [], {})

    assert placement.transparency == 0.8
    assert view._boxes["top/u_core"].brush().color().alpha() == round((1 - 0.8) * 255)
    _deselect_and_collect(tab)


def test_create_power_domain_adds_missing_supply_ports_to_the_hierarchy(qtbot):
    """Explicit request: "add missing supply ports to the hierarchy" --
    a name introduced via a Supply Set's own "Add New..." (represented
    here directly as the `new_supply_ports` dict create_power_domain()
    receives) becomes a real, box-anchored SupplyPortPlacement."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top/u_core"]

    view.create_power_domain(box, "PD_CORE", QColor("#4caf50"), 0.5, None, [], {}, {"VDD_NEW": QColor("#123456")})

    added = [p for p in view._shared_state.supply_port_placements.values() if p.owner_path == "top/u_core" and p.name == "VDD_NEW"]
    assert len(added) == 1
    assert added[0].color == QColor("#123456")
    assert added[0].id in view._supply_port_icons
    _deselect_and_collect(tab)


def test_create_power_domain_does_not_duplicate_a_supply_port_that_already_exists(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top/u_core"]
    view.add_supply_port(box, "VDD", QColor("#ff0000"))

    view.create_power_domain(box, "PD_CORE", QColor("#4caf50"), 0.5, None, [], {}, {"VDD": QColor("#123456")})

    matching = [p for p in view._shared_state.supply_port_placements.values() if p.owner_path == "top/u_core" and p.name == "VDD"]
    assert len(matching) == 1  # not duplicated -- already existed
    assert matching[0].color == QColor("#ff0000")  # untouched, kept its original colour
    _deselect_and_collect(tab)


# --- bug fix/feature: bold-italic Power Domain name label on its owner box -----


def test_create_power_domain_adds_a_bold_italic_name_line_on_the_owner_box(qtbot):
    """The domain name is now the FIRST line of the merged
    DomainInfoBoxItem (explicit request, supersedes the earlier
    separate DomainLabelItem) -- same bold-italic styling, unchanged,
    just accessed via `._rows[0]`/`._name_font` instead of a bare
    QGraphicsSimpleTextItem's own `.text()`/`.font()`."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top/u_core"]

    domain = view.create_power_domain(box, "PD_CORE", QColor("#4caf50"), 0.5, None, [], {})

    info_box = view._domain_info_boxes[domain.id][0]
    assert info_box._rows[0][0] == "PD_CORE"
    assert info_box.parentItem() is box
    assert info_box._name_font.bold() is True
    assert info_box._name_font.italic() is True
    _deselect_and_collect(tab)


def test_domain_info_box_is_anchored_to_the_bottom_right_corner_of_its_owner_box(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top/u_core"]

    domain = view.create_power_domain(box, "PD_CORE", QColor("#4caf50"), 0.5, None, [], {})

    info_box = view._domain_info_boxes[domain.id][0]
    box_rect = box.rect()
    box_size = info_box._content_size()
    # Bottom-right corner, inset by a small margin on both axes -- the
    # box's own bottom-right corner should sit near the owner box's own.
    assert info_box.pos().x() == pytest.approx(box_rect.right() - box_size.width() - 6)
    assert info_box.pos().y() == pytest.approx(box_rect.bottom() - box_size.height() - 6)
    _deselect_and_collect(tab)


def test_renaming_a_power_domain_updates_its_own_name_line(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top/u_core"]
    domain = view.create_power_domain(box, "PD_CORE", QColor("#4caf50"), 0.5, None, [], {})
    assert view._domain_info_boxes[domain.id][0]._rows[0][0] == "PD_CORE"

    view.edit_power_domain(domain.id, "PD_CORE_RENAMED", QColor("#4caf50"), 0.5, None, [], {})

    assert view._domain_info_boxes[domain.id][0]._rows[0][0] == "PD_CORE_RENAMED"
    _deselect_and_collect(tab)


def test_a_rebuild_reflects_the_current_domain_info_boxes(qtbot):
    """Same "materialized items don't survive a rebuild, rematerialize
    from plain data" story as every other diagram-only overlay here --
    a Hide/Unhide-triggered rebuild must still show the merged box
    afterward, not lose it."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top/u_core"]
    domain = view.create_power_domain(box, "PD_CORE", QColor("#4caf50"), 0.5, None, [], {})

    tab.tree.hide_paths(["top/u_dbg"])  # unrelated hide -- triggers a real rebuild

    assert domain.id in view._domain_info_boxes
    assert view._domain_info_boxes[domain.id][0]._rows[0][0] == "PD_CORE"
    _deselect_and_collect(tab)


# --- edit_power_domain() core method --------------------------------------------


def test_edit_power_domain_updates_fields_and_recolors(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    placement = view.create_power_domain(view._boxes["top/u_core"], "PD_CORE", QColor("#4caf50"), 0.5, None, [], {})

    updated = view.edit_power_domain(placement.id, "PD_CORE_RENAMED", QColor("#0000ff"), 0.2, None, [], {})

    assert updated.id == placement.id
    assert updated.owner_path == "top/u_core"  # identity field -- unchanged
    assert updated.name == "PD_CORE_RENAMED"
    assert view._boxes["top/u_core"].brush().color().name() == "#0000ff"
    _deselect_and_collect(tab)


def test_edit_power_domain_does_not_disturb_an_unrelated_nested_domain(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    outer = view.create_power_domain(view._boxes["top"], "PD_TOP", QColor("#ff0000"), 0.5, None, [], {})
    view.create_power_domain(view._boxes["top/u_core"], "PD_CORE", QColor("#00ff00"), 0.5, None, [], {})

    view.edit_power_domain(outer.id, "PD_TOP", QColor("#0000ff"), 0.5, None, [], {})

    assert view._boxes["top/u_dbg"].brush().color().name() == "#0000ff"  # only covered by outer
    assert view._boxes["top/u_core"].brush().color().name() == "#00ff00"  # still the nested domain's own colour
    _deselect_and_collect(tab)


def test_edit_power_domain_adds_missing_supply_ports_too(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    placement = view.create_power_domain(view._boxes["top/u_core"], "PD_CORE", QColor("#4caf50"), 0.5, None, [], {})

    view.edit_power_domain(placement.id, "PD_CORE", QColor("#4caf50"), 0.5, None, [], {}, {"VSS_NEW": QColor("#654321")})

    added = [p for p in view._shared_state.supply_port_placements.values() if p.owner_path == "top/u_core" and p.name == "VSS_NEW"]
    assert len(added) == 1
    _deselect_and_collect(tab)


# --- remove_power_domain() core method ------------------------------------------


def test_remove_power_domain_removes_the_placement_and_resets_the_boxs_colour(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top/u_core"]
    view.create_power_domain(box, "PD_CORE", QColor("#4caf50"), 0.5, None, [], {})

    view.remove_power_domain(box)

    assert len(view._domain_state.domains) == 0
    assert view._boxes["top/u_core"].brush().color().name() == "#ffffff"
    _deselect_and_collect(tab)


def test_remove_power_domain_is_a_no_op_when_the_box_owns_no_domain(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    count_before = tab.tree.undo_stack.count()

    view.remove_power_domain(view._boxes["top/u_dbg"])

    assert tab.tree.undo_stack.count() == count_before  # nothing pushed
    _deselect_and_collect(tab)


def test_remove_power_domain_uncovers_a_previously_shadowed_ancestor_domain(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view.create_power_domain(view._boxes["top"], "PD_TOP", QColor("#ff0000"), 0.5, None, [], {})
    view.create_power_domain(view._boxes["top/u_core"], "PD_CORE", QColor("#00ff00"), 0.5, None, [], {})
    assert view._boxes["top/u_core"].brush().color().name() == "#00ff00"

    view.remove_power_domain(view._boxes["top/u_core"])

    assert view._boxes["top/u_core"].brush().color().name() == "#ff0000"  # falls back to the outer domain
    _deselect_and_collect(tab)


def test_ctrl_z_undoes_remove_power_domain(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top/u_core"]
    view.create_power_domain(box, "PD_CORE", QColor("#4caf50"), 0.5, None, [], {})
    view.remove_power_domain(box)
    assert len(view._domain_state.domains) == 0

    tab.undo_shortcut.activated.emit()

    assert len(view._domain_state.domains) == 1
    assert view._boxes["top/u_core"].brush().color().name() == "#4caf50"
    _deselect_and_collect(tab)


def test_remove_power_domain_leaves_an_unrelated_auto_added_port_untouched(qtbot):
    """Same fixture as before the cascade fix -- kept, renamed for
    precision: "VDD_NEW" here isn't tied to any Supply Set the domain
    actually references (no primary/secondary was ever set up), so
    there is genuinely nothing for the new cascade to remove -- this is
    NOT a claim that Remove Power Domain leaves supply state untouched
    in general anymore (see the cascade tests below for that)."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top/u_core"]
    view.create_power_domain(box, "PD_CORE", QColor("#4caf50"), 0.5, None, [], {}, {"VDD_NEW": QColor("#123456")})
    supply_sets_before = dict(view._domain_state.supply_sets)
    ports_before = dict(view._shared_state.supply_port_placements)

    view.remove_power_domain(box)

    assert view._domain_state.supply_sets == supply_sets_before
    assert view._shared_state.supply_port_placements == ports_before
    _deselect_and_collect(tab)


# --- bug fix: Remove Power Domain cascades to Supply Sets/Ports/Retention/label -


def _domain_with_everything(qtbot):
    """A Power Domain with a Primary + Secondary Supply Set (each
    referencing its own real Supply Port), plus a Retention Strategy
    tied to it -- the full "nothing should be left orphaned" fixture
    fix #4's own regression test needs."""
    tab, view, box = _domain_view(qtbot)
    primary_port = view.add_supply_port(box, "VDD_PRIMARY", QColor("#ff0000"))
    secondary_port = view.add_supply_port(box, "VDD_SECONDARY", QColor("#00ff00"))
    primary = SupplySetPlacement(id="ss_primary", name="SS_PRIMARY", power="VDD_PRIMARY", owner_path="top")
    secondary = SupplySetPlacement(id="ss_secondary", name="SS_SECONDARY", power="VDD_SECONDARY", owner_path="top")
    domain = view.create_power_domain(box, "PD_TOP", QColor("#4caf50"), 0.5, "ss_primary", ["ss_secondary"], {"ss_primary": primary, "ss_secondary": secondary})
    strategy = view.create_retention_strategy(box, domain, "RET1", "ss_primary", "ss_secondary", "save", "restore", [], {})
    return tab, view, box, domain, primary_port, secondary_port, strategy


def test_remove_power_domain_cascades_to_its_own_supply_sets(qtbot):
    tab, view, box, domain, primary_port, secondary_port, strategy = _domain_with_everything(qtbot)

    view.remove_power_domain(box)

    assert "ss_primary" not in view._domain_state.supply_sets
    assert "ss_secondary" not in view._domain_state.supply_sets
    _deselect_and_collect(tab)


def test_remove_power_domain_cascades_to_its_own_supply_ports(qtbot):
    tab, view, box, domain, primary_port, secondary_port, strategy = _domain_with_everything(qtbot)

    view.remove_power_domain(box)

    assert primary_port.key not in view._shared_state.supply_port_placements
    assert secondary_port.key not in view._shared_state.supply_port_placements
    _deselect_and_collect(tab)


def test_remove_power_domain_deletes_dependent_retention_strategies_outright(qtbot):
    """Explicit follow-up decision: a Retention Strategy tied to the
    deleted domain is removed outright, not merely flagged for review
    (unlike Edit Power Domain's own partial-removal warning path)."""
    tab, view, box, domain, primary_port, secondary_port, strategy = _domain_with_everything(qtbot)

    view.remove_power_domain(box)

    assert strategy.id not in view._retention_state.strategies
    assert view._retention_icons == {}
    _deselect_and_collect(tab)


def test_remove_power_domain_removes_the_domain_info_box(qtbot):
    tab, view, box, domain, primary_port, secondary_port, strategy = _domain_with_everything(qtbot)
    assert domain.id in view._domain_info_boxes

    view.remove_power_domain(box)

    assert domain.id not in view._domain_info_boxes


def test_remove_power_domain_updates_properties_tables_live(qtbot):
    """No stale rows for the cascaded-away entities -- the mock design's
    own macro (u_ram) still legitimately contributes 2 fixed "Macro PG"/
    "Macro PG - Internal" SupplyPort rows regardless (real elaborated
    RTL data, untouched by any diagram-only cascade), so this checks the
    specific rows disappear rather than asserting the whole table empties
    out."""
    tab, view, box, domain, primary_port, secondary_port, strategy = _domain_with_everything(qtbot)
    assert tab.properties._tables["SupplySet"].rowCount() >= 2
    port_names_before = {tab.properties._tables["SupplyPort"].item(r, 1).text() for r in range(tab.properties._tables["SupplyPort"].rowCount())}
    assert {"VDD_PRIMARY", "VDD_SECONDARY"} <= port_names_before
    assert tab.properties._tables["RetentionStrategy"].rowCount() >= 1

    view.remove_power_domain(box)

    assert tab.properties._tables["SupplySet"].rowCount() == 0
    port_names_after = {tab.properties._tables["SupplyPort"].item(r, 1).text() for r in range(tab.properties._tables["SupplyPort"].rowCount())}
    assert "VDD_PRIMARY" not in port_names_after
    assert "VDD_SECONDARY" not in port_names_after
    assert tab.properties._tables["RetentionStrategy"].rowCount() == 0
    _deselect_and_collect(tab)


def test_ctrl_z_undoes_the_full_remove_power_domain_cascade_in_one_step(qtbot):
    tab, view, box, domain, primary_port, secondary_port, strategy = _domain_with_everything(qtbot)

    view.remove_power_domain(box)
    assert len(view._domain_state.domains) == 0

    tab.undo_shortcut.activated.emit()  # a single Ctrl+Z, not one per cascaded entity

    assert len(view._domain_state.domains) == 1
    assert "ss_primary" in view._domain_state.supply_sets
    assert "ss_secondary" in view._domain_state.supply_sets
    assert primary_port.key in view._shared_state.supply_port_placements
    assert secondary_port.key in view._shared_state.supply_port_placements
    assert strategy.id in view._retention_state.strategies
    assert domain.id in view._domain_info_boxes
    _deselect_and_collect(tab)


# --- undo/redo -----------------------------------------------------------------


def test_ctrl_z_undoes_create_power_domain_and_ctrl_y_redoes_it(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view.create_power_domain(view._boxes["top/u_core"], "PD_CORE", QColor("#4caf50"), 0.5, None, [], {})
    assert len(view._domain_state.domains) == 1
    assert view._boxes["top/u_core"].brush().color().name() == "#4caf50"

    tab.undo_shortcut.activated.emit()
    assert len(view._domain_state.domains) == 0
    assert view._boxes["top/u_core"].brush().color().name() == "#ffffff"

    tab.redo_shortcut.activated.emit()
    assert len(view._domain_state.domains) == 1
    assert view._boxes["top/u_core"].brush().color().name() == "#4caf50"
    _deselect_and_collect(tab)


def test_ctrl_z_on_create_power_domain_also_undoes_its_auto_added_supply_ports(qtbot):
    """Explicit combined-undo-step requirement: a single Ctrl+Z must
    revert both the domain and any supply ports it materialized (see
    _commit_combined_undo_step()'s own docstring), not leave a stray
    supply port behind after the domain itself is gone."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    before_count = len(view._shared_state.supply_port_placements)
    view.create_power_domain(view._boxes["top/u_core"], "PD_CORE", QColor("#4caf50"), 0.5, None, [], {}, {"VDD_NEW": QColor("#123456")})
    assert len(view._shared_state.supply_port_placements) == before_count + 1

    tab.undo_shortcut.activated.emit()

    assert len(view._domain_state.domains) == 0
    assert len(view._shared_state.supply_port_placements) == before_count

    tab.redo_shortcut.activated.emit()
    assert len(view._domain_state.domains) == 1
    assert len(view._shared_state.supply_port_placements) == before_count + 1
    _deselect_and_collect(tab)


def test_create_power_domain_uses_the_same_undo_stack_as_hide_unhide(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    count_before = tab.tree.undo_stack.count()

    view.create_power_domain(view._boxes["top/u_core"], "PD_CORE", QColor("#4caf50"), 0.5, None, [], {})

    assert tab.tree.undo_stack.count() == count_before + 1
    _deselect_and_collect(tab)


def test_a_fresh_design_load_clears_power_domain_undo_history(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view.create_power_domain(view._boxes["top/u_core"], "PD_CORE", QColor("#4caf50"), 0.5, None, [], {})
    assert tab.tree.undo_stack.count() > 0

    tab.set_rtl(build_mock_design())

    assert tab.tree.undo_stack.count() == 0
    _deselect_and_collect(tab)


# --- rebuild / cross-tab persistence -------------------------------------------


def test_domain_coloring_survives_an_unrelated_rebuild(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view.create_power_domain(view._boxes["top/u_core"], "PD_CORE", QColor("#4caf50"), 0.5, None, [], {})

    tab.tree._toggle_hidden("top/u_dbg")  # unrelated rebuild -- repacks the whole diagram

    assert view._boxes["top/u_core"].brush().color().name() == "#4caf50"
    _deselect_and_collect(tab)


def test_domain_created_in_a_sub_hierarchy_tab_is_visible_from_the_root_tab(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    root_view = tab.diagram._tabs.current_view
    tab.diagram.open_path("top/u_core")
    sub_view = tab.diagram._tabs.current_view
    assert sub_view is not root_view

    sub_view.create_power_domain(sub_view._boxes["top/u_core"], "PD_SUB", QColor("#0000ff"), 0.5, None, [], {})

    tab.diagram._tabs.setCurrentIndex(0)  # switch back -- triggers refresh
    assert root_view._boxes["top/u_core"].brush().color().name() == "#0000ff"
    _deselect_and_collect(tab)


# --- CreateSupplySetDialog ------------------------------------------------------


def test_supply_set_dialog_ok_disabled_until_named(qtbot):
    dialog = CreateSupplySetDialog(["VDD", "VSS"])
    qtbot.addWidget(dialog)
    assert dialog._ok_button.isEnabled() is False

    dialog.name_edit.setText("SS_CORE")

    assert dialog._ok_button.isEnabled() is True


def test_supply_set_dialog_exposes_the_4_function_handles(qtbot):
    dialog = CreateSupplySetDialog(["VDD", "VSS"])
    qtbot.addWidget(dialog)
    dialog.name_edit.setText("SS_CORE")
    dialog.power_combo.setCurrentText("VDD")
    dialog.ground_combo.setCurrentText("VSS")

    assert dialog.name == "SS_CORE"
    assert dialog.power == "VDD"
    assert dialog.ground == "VSS"
    assert dialog.n_well is None
    assert dialog.p_well is None


# --- Fix 4A: merged Supply Set creation -- "Set as Primary" checkbox ------


def test_supply_set_dialog_set_as_primary_checkbox_enabled_when_primary_available(qtbot):
    dialog = CreateSupplySetDialog(["VDD", "VSS"], primary_available=True)
    qtbot.addWidget(dialog)

    assert dialog.set_as_primary_checkbox.isEnabled() is True
    assert dialog.set_as_primary_checkbox.isChecked() is False  # opt-in, not a default-on assumption


def test_supply_set_dialog_set_as_primary_checkbox_disabled_when_primary_not_available(qtbot):
    dialog = CreateSupplySetDialog(["VDD", "VSS"], primary_available=False)
    qtbot.addWidget(dialog)

    assert dialog.set_as_primary_checkbox.isEnabled() is False
    assert dialog.set_as_primary_checkbox.isChecked() is False


def test_supply_set_dialog_defaults_to_primary_available(qtbot):
    """Every pre-existing standalone construction (no `primary_available`
    given at all) keeps the checkbox usable, matching this dialog's own
    pre-Fix-4A behaviour of not being tied to any one Power Domain's
    own Primary state."""
    dialog = CreateSupplySetDialog(["VDD", "VSS"])
    qtbot.addWidget(dialog)

    assert dialog.set_as_primary_checkbox.isEnabled() is True


def test_supply_set_dialog_set_as_primary_property_tracks_the_checkbox(qtbot):
    dialog = CreateSupplySetDialog(["VDD", "VSS"], primary_available=True)
    qtbot.addWidget(dialog)
    assert dialog.set_as_primary is False

    dialog.set_as_primary_checkbox.setChecked(True)

    assert dialog.set_as_primary is True


def test_supply_set_dialog_set_as_primary_is_false_when_disabled_even_if_forced_checked(qtbot):
    """A disabled checkbox can't be checked via real UI interaction, but
    the `set_as_primary` property is defensively false regardless of
    the raw checkState() in case something ever forces it (defense-in-
    depth, same precedent _MACRO_SUPPLY_PORT_ERROR's own two-layer
    check already establishes elsewhere)."""
    dialog = CreateSupplySetDialog(["VDD", "VSS"], primary_available=False)
    qtbot.addWidget(dialog)

    dialog.set_as_primary_checkbox.setChecked(True)  # bypasses the disabled UI gate directly

    assert dialog.set_as_primary is False


def _fake_add_power_supply_exec(name: str, color: QColor, accept: bool = True):
    """A stand-in for AddPowerSupplyDialog.exec() (native modal, can't be
    driven headlessly) -- sets the fields a real user interaction would
    have left behind, then returns the QDialog result code exec() itself
    would return. Patched onto the class (not the instance -- _SupplyNameCombo
    constructs its own AddPowerSupplyDialog internally), same pattern
    already used for CreateSupplySetDialog.exec() in the
    CreatePowerDomainDialog tests below."""

    def fake_exec(self):
        if accept:
            self.name_edit.setText(name)
            self.color = QColor(color)
        return QDialog.DialogCode.Accepted if accept else QDialog.DialogCode.Rejected

    return fake_exec


def test_supply_set_dialog_add_new_pops_up_the_same_dialog_as_standalone_add_power_supply(qtbot, monkeypatch):
    """Explicit request: "pop up the same window that you would pop up
    when a power supply is created - so user can set name and colour as
    a standalone port creation" -- name and colour chosen together, in
    one AddPowerSupplyDialog, not a bare name prompt with the colour
    deferred to later."""
    monkeypatch.setattr(AddPowerSupplyDialog, "exec", _fake_add_power_supply_exec("VDD_NEW", QColor("#123456")))
    dialog = CreateSupplySetDialog(["VDD"])
    qtbot.addWidget(dialog)

    index = dialog.power_combo.findText("Add New...")
    dialog.power_combo._on_index_changed(index)  # direct call -- see module note on signal-driven monkeypatching

    assert dialog.power == "VDD_NEW"
    assert dialog.new_port_colors == {"VDD_NEW": QColor("#123456")}  # captured immediately, not deferred to accept()


def test_add_new_via_real_signal_emission_opens_the_dialog_exactly_once(qtbot, monkeypatch):
    """Real bug, found via live use: inserting the new item *at* the
    current index (where "Add New..." itself sat) shifted Qt's own
    current-index bookkeeping forward to keep pointing at "Add New...",
    and that shift itself re-emitted currentIndexChanged -- re-entering
    _on_index_changed and popping the same AddPowerSupplyDialog open a
    second time. Unlike the other "Add New..." tests, this one drives
    the gesture through the *real* signal (setCurrentIndex(), not a
    direct _on_index_changed() call) specifically to exercise that
    re-entrancy path."""
    call_count = []

    def fake_exec(self):
        call_count.append(1)
        self.name_edit.setText("VDD_NEW")
        self.color = QColor("#123456")
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(AddPowerSupplyDialog, "exec", fake_exec)
    dialog = CreateSupplySetDialog(["VDD"])
    qtbot.addWidget(dialog)

    index = dialog.power_combo.findText("Add New...")
    dialog.power_combo.setCurrentIndex(index)  # the real signal-driven gesture

    assert len(call_count) == 1
    assert dialog.power == "VDD_NEW"
    assert [dialog.power_combo.itemText(i) for i in range(dialog.power_combo.count())] == ["", "VDD", "VDD_NEW", "Add New..."]


def test_supply_set_dialog_cancelling_add_new_reverts_selection(qtbot, monkeypatch):
    monkeypatch.setattr(AddPowerSupplyDialog, "exec", _fake_add_power_supply_exec("", QColor(), accept=False))
    dialog = CreateSupplySetDialog(["VDD"])
    qtbot.addWidget(dialog)
    dialog.power_combo.setCurrentText("VDD")

    index = dialog.power_combo.findText("Add New...")
    dialog.power_combo._on_index_changed(index)

    assert dialog.power == "VDD"  # reverted, not left on "Add New..."
    assert dialog.new_port_colors == {}


def test_supply_set_dialog_a_new_name_is_offered_on_all_4_combos(qtbot, monkeypatch):
    """A name added via Power must also be selectable from N-Well/
    P-Well -- otherwise "N-Well must match either Power or Ground"
    could never be satisfied for a genuinely new name."""
    monkeypatch.setattr(AddPowerSupplyDialog, "exec", _fake_add_power_supply_exec("VDD_NEW", QColor("#123456")))
    dialog = CreateSupplySetDialog(["VSS"])
    qtbot.addWidget(dialog)

    index = dialog.power_combo.findText("Add New...")
    dialog.power_combo._on_index_changed(index)

    assert dialog.n_well_combo.findText("VDD_NEW") >= 0
    assert dialog.p_well_combo.findText("VDD_NEW") >= 0
    assert dialog.ground_combo.findText("VDD_NEW") >= 0


def test_supply_set_dialog_accept_rejects_missing_ground(qtbot, monkeypatch):
    """N-Well/P-Well are optional (explicit request) -- only Power/Ground
    are hard-required now, so a Ground-only omission reports just
    Ground, not N-Well/P-Well too (see the sibling "no wells at all is
    valid" test below for confirmation that leaving BOTH wells blank is
    not itself an error)."""
    calls = _warnings(monkeypatch)
    dialog = CreateSupplySetDialog(["VDD", "VSS"])
    qtbot.addWidget(dialog)
    dialog.name_edit.setText("SS1")
    dialog.power_combo.setCurrentText("VDD")
    # ground left blank; n_well/p_well correctly left blank too (optional)

    dialog.accept()

    assert dialog.result() == QDialog.DialogCode.Rejected
    assert len(calls) == 1
    assert calls[0][1] == "Ground must be populated."


def test_supply_set_dialog_accept_rejects_n_well_not_matching_power_or_ground(qtbot, monkeypatch):
    calls = _warnings(monkeypatch)
    dialog = CreateSupplySetDialog(["VDD", "VSS", "OTHER"])
    qtbot.addWidget(dialog)
    dialog.name_edit.setText("SS1")
    dialog.power_combo.setCurrentText("VDD")
    dialog.ground_combo.setCurrentText("VSS")
    dialog.n_well_combo.setCurrentText("OTHER")
    dialog.p_well_combo.setCurrentText("VSS")

    dialog.accept()

    assert dialog.result() == QDialog.DialogCode.Rejected
    assert calls[0][0] == "Invalid N-Well"


def test_supply_set_dialog_accept_rejects_p_well_not_matching_power_or_ground(qtbot, monkeypatch):
    calls = _warnings(monkeypatch)
    dialog = CreateSupplySetDialog(["VDD", "VSS", "OTHER"])
    qtbot.addWidget(dialog)
    dialog.name_edit.setText("SS1")
    dialog.power_combo.setCurrentText("VDD")
    dialog.ground_combo.setCurrentText("VSS")
    dialog.n_well_combo.setCurrentText("VDD")
    dialog.p_well_combo.setCurrentText("OTHER")

    dialog.accept()

    assert dialog.result() == QDialog.DialogCode.Rejected
    assert calls[0][0] == "Invalid P-Well"


def test_supply_set_dialog_accepts_when_n_well_and_p_well_are_mixed(qtbot, monkeypatch):
    """"They must have at least same value of one of those or mixed" --
    N-Well tied to Ground and P-Well tied to Power is unusual but valid,
    as long as each individually matches one of the two."""
    _warnings(monkeypatch)
    dialog = CreateSupplySetDialog(["VDD", "VSS"])
    qtbot.addWidget(dialog)
    dialog.name_edit.setText("SS1")
    dialog.power_combo.setCurrentText("VDD")
    dialog.ground_combo.setCurrentText("VSS")
    dialog.n_well_combo.setCurrentText("VSS")
    dialog.p_well_combo.setCurrentText("VDD")

    dialog.accept()

    assert dialog.result() == QDialog.DialogCode.Accepted


# --- N-Well/P-Well are optional (explicit follow-up request) --------------


def test_supply_set_dialog_n_well_and_p_well_start_disabled(qtbot):
    dialog = CreateSupplySetDialog(["VDD", "VSS"])
    qtbot.addWidget(dialog)

    assert dialog.n_well_combo.isEnabled() is False
    assert dialog.p_well_combo.isEnabled() is False


def test_supply_set_dialog_enable_bias_supply_checkbox_unlocks_both_well_fields(qtbot):
    dialog = CreateSupplySetDialog(["VDD", "VSS"])
    qtbot.addWidget(dialog)

    dialog.enable_bias_supply_checkbox.setChecked(True)

    assert dialog.n_well_combo.isEnabled() is True
    assert dialog.p_well_combo.isEnabled() is True


def test_supply_set_dialog_unchecking_enable_bias_supply_disables_the_fields_again(qtbot):
    """Explicit correction: a genuine toggle, not a one-shot unlock --
    unchecking re-disables both combos."""
    dialog = CreateSupplySetDialog(["VDD", "VSS"])
    qtbot.addWidget(dialog)
    dialog.enable_bias_supply_checkbox.setChecked(True)

    dialog.enable_bias_supply_checkbox.setChecked(False)

    assert dialog.n_well_combo.isEnabled() is False
    assert dialog.p_well_combo.isEnabled() is False


def test_supply_set_dialog_unchecking_enable_bias_supply_clears_populated_wells(qtbot):
    """Explicit request/correction: unchecking after populating both
    wells CLEARS them back to the "no well data" state, rather than
    disabling-while-preserving the previously entered values -- see
    the class's own docstring for why silently preserving would be a
    real correctness bug (a disabled combo's own selected_name() still
    reads whatever was last set, regardless of enabled state)."""
    dialog = CreateSupplySetDialog(["VDD", "VSS"])
    qtbot.addWidget(dialog)
    dialog.enable_bias_supply_checkbox.setChecked(True)
    dialog.n_well_combo.setCurrentText("VDD")
    dialog.p_well_combo.setCurrentText("VSS")

    dialog.enable_bias_supply_checkbox.setChecked(False)

    assert dialog.n_well is None
    assert dialog.p_well is None


def test_supply_set_dialog_accepts_with_no_wells_at_all(qtbot, monkeypatch):
    """(a): a Supply Set with no N-Well/P-Well at all is a fully valid,
    non-error state -- the wells stay disabled and blank, never
    unlocked at all."""
    _warnings(monkeypatch)
    dialog = CreateSupplySetDialog(["VDD", "VSS"])
    qtbot.addWidget(dialog)
    dialog.name_edit.setText("SS1")
    dialog.power_combo.setCurrentText("VDD")
    dialog.ground_combo.setCurrentText("VSS")

    dialog.accept()

    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.n_well is None
    assert dialog.p_well is None


def test_supply_set_dialog_rejects_only_n_well_populated(qtbot, monkeypatch):
    """(b): populating ONLY N-Well, leaving P-Well empty, is an invalid,
    inconsistent state -- rejected."""
    calls = _warnings(monkeypatch)
    dialog = CreateSupplySetDialog(["VDD", "VSS"])
    qtbot.addWidget(dialog)
    dialog.name_edit.setText("SS1")
    dialog.power_combo.setCurrentText("VDD")
    dialog.ground_combo.setCurrentText("VSS")
    dialog.enable_bias_supply_checkbox.setChecked(True)
    dialog.n_well_combo.setCurrentText("VDD")
    # p_well left blank

    dialog.accept()

    assert dialog.result() == QDialog.DialogCode.Rejected
    assert calls[0][0] == "Incomplete Well Pins"


def test_supply_set_dialog_rejects_only_p_well_populated(qtbot, monkeypatch):
    """(c): the mirror image of the above -- only P-Well populated."""
    calls = _warnings(monkeypatch)
    dialog = CreateSupplySetDialog(["VDD", "VSS"])
    qtbot.addWidget(dialog)
    dialog.name_edit.setText("SS1")
    dialog.power_combo.setCurrentText("VDD")
    dialog.ground_combo.setCurrentText("VSS")
    dialog.enable_bias_supply_checkbox.setChecked(True)
    dialog.p_well_combo.setCurrentText("VSS")
    # n_well left blank

    dialog.accept()

    assert dialog.result() == QDialog.DialogCode.Rejected
    assert calls[0][0] == "Incomplete Well Pins"


def test_supply_set_dialog_both_wells_populated_still_works(qtbot, monkeypatch):
    """(d): no regression -- a Supply Set with both N-Well and P-Well
    populated still works exactly as before."""
    _warnings(monkeypatch)
    dialog = CreateSupplySetDialog(["VDD", "VSS"])
    qtbot.addWidget(dialog)
    dialog.name_edit.setText("SS1")
    dialog.power_combo.setCurrentText("VDD")
    dialog.ground_combo.setCurrentText("VSS")
    dialog.enable_bias_supply_checkbox.setChecked(True)
    dialog.n_well_combo.setCurrentText("VDD")
    dialog.p_well_combo.setCurrentText("VSS")

    dialog.accept()

    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.n_well == "VDD"
    assert dialog.p_well == "VSS"


def test_supply_set_with_duplicate_pin_combination_treats_two_well_less_sets_as_duplicates(qtbot):
    """Downstream impact audit: supply_set_with_duplicate_pin_combination()
    compares all 4 fields with plain equality -- None == None is True in
    Python, so two Supply Sets sharing the same Power+Ground and BOTH
    having no wells at all are correctly still flagged as a duplicate on
    the fields that exist, exactly as expected -- confirmed here with a
    real regression test rather than left as an unverified assumption."""
    from frontend.src.power_intent_view.power_domain_overlay import supply_set_with_duplicate_pin_combination

    existing = SupplySetPlacement(id="ss1", name="SS1", power="VDD", ground="VSS", n_well=None, p_well=None)
    conflict = supply_set_with_duplicate_pin_combination("VDD", "VSS", None, None, {"ss1": existing})

    assert conflict is existing


def test_supply_set_dialog_accept_does_not_reprompt_for_a_colour_already_captured(qtbot, monkeypatch):
    """accept() no longer opens any colour picker itself -- new_port_colors
    is already fully populated by the time "Add New..." completes (see
    test_supply_set_dialog_add_new_pops_up_the_same_dialog_as_standalone_add_power_supply),
    so accepting a fully-filled-out form must not pop up any *further*
    dialog. Patching AddPowerSupplyDialog.exec() to raise proves accept()
    never tries to open one."""

    def exec_should_not_be_called(self):
        raise AssertionError("accept() should not open any further dialog")

    monkeypatch.setattr(AddPowerSupplyDialog, "exec", _fake_add_power_supply_exec("VDD_NEW", QColor("#123456")))
    dialog = CreateSupplySetDialog(["VSS"])
    qtbot.addWidget(dialog)
    dialog.name_edit.setText("SS1")
    index = dialog.power_combo.findText("Add New...")
    dialog.power_combo._on_index_changed(index)  # introduces VDD_NEW immediately -- direct call, see module note
    dialog.ground_combo.setCurrentText("VSS")
    dialog.n_well_combo.setCurrentText("VDD_NEW")
    dialog.p_well_combo.setCurrentText("VSS")
    monkeypatch.setattr(AddPowerSupplyDialog, "exec", exec_should_not_be_called)

    dialog.accept()

    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.new_port_colors == {"VDD_NEW": QColor("#123456")}


def test_supply_set_dialog_no_new_names_means_no_new_port_colors(qtbot):
    dialog = CreateSupplySetDialog(["VDD", "VSS"])
    qtbot.addWidget(dialog)
    dialog.name_edit.setText("SS1")
    dialog.power_combo.setCurrentText("VDD")
    dialog.ground_combo.setCurrentText("VSS")
    dialog.n_well_combo.setCurrentText("VDD")
    dialog.p_well_combo.setCurrentText("VSS")

    dialog.accept()

    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.new_port_colors == {}


# --- CreatePowerDomainDialog -----------------------------------------------------


def test_power_domain_dialog_ok_disabled_until_named(qtbot):
    dialog = CreatePowerDomainDialog({}, ["VDD"], {}, "top")
    qtbot.addWidget(dialog)
    assert dialog._ok_button.isEnabled() is False

    dialog.name_edit.setText("PD_CORE")

    assert dialog._ok_button.isEnabled() is True


def test_power_domain_dialog_is_fully_opaque(qtbot):
    """Explicit bug report: this dialog appeared translucent, unlike
    every other dialog in the app."""
    from PySide6.QtCore import Qt

    dialog = CreatePowerDomainDialog({}, ["VDD"], {}, "top")
    qtbot.addWidget(dialog)

    assert dialog.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground) is False
    assert dialog.autoFillBackground() is True
    assert dialog.windowOpacity() == 1.0


def test_power_domain_dialog_defaults(qtbot):
    dialog = CreatePowerDomainDialog({}, ["VDD"], {}, "top")
    qtbot.addWidget(dialog)

    assert dialog.transparency == 0.8  # explicit request, supersedes the earlier adjustable 50% default: always fixed at 80%
    assert dialog.primary_supply_set_id is None
    assert dialog.secondary_supply_set_ids == []


def test_power_domain_dialog_has_no_manual_color_or_transparency_controls(qtbot):
    """Explicit request, then a direct follow-up: the manual colour-
    picker/transparency-spinbox pair is removed entirely, and even the
    read-only colour PREVIEW an earlier pass added is gone too -- colour
    derivation happens entirely behind the scenes now, with zero visual
    trace in the dialog."""
    dialog = CreatePowerDomainDialog({}, ["VDD"], {}, "top")
    qtbot.addWidget(dialog)

    assert not hasattr(dialog, "color_button")
    assert not hasattr(dialog, "color_preview_label")
    assert not hasattr(dialog, "transparency_spin")
    assert dialog.transparency == 0.8


def test_power_domain_dialog_removing_the_primary_frees_the_slot_up_again(qtbot, monkeypatch):
    """Follow-up explicit request (Primary/Secondary picker fields
    removed entirely -- "Create Supply Set..." is now the ONLY way to
    add a Supply Set, decided per-Supply-Set at creation time):
    removing the Primary from `supply_set_list` frees the slot back up
    so a NEW Supply Set can be created and checked "Set as Primary"
    again."""
    monkeypatch.setattr("frontend.src.power_intent_view.create_power_domain_dialog.CreateSupplySetDialog.exec", _fake_create_supply_set_exec("SS1", set_as_primary=True))
    dialog = CreatePowerDomainDialog({}, ["VDD"], {}, "top")
    qtbot.addWidget(dialog)
    dialog._prompt_create_and_assign_supply_set()
    first_id = dialog.primary_supply_set_id
    assert first_id is not None
    assert dialog.supply_set_list.item(0).text() == "Primary: SS1"

    dialog.supply_set_list.setCurrentRow(0)
    dialog._remove_selected_supply_set()

    assert dialog.primary_supply_set_id is None
    assert dialog.supply_set_list.count() == 0

    monkeypatch.setattr("frontend.src.power_intent_view.create_power_domain_dialog.CreateSupplySetDialog.exec", _fake_create_supply_set_exec("SS2", set_as_primary=True))
    dialog._prompt_create_and_assign_supply_set()

    assert dialog.primary_supply_set_id is not None
    assert dialog.primary_supply_set_id != first_id


def _fake_create_supply_set_exec(name: str, set_as_primary: bool = False, power="VDD", ground="VSS", n_well="VDD", p_well="VSS"):
    """Stand-in for CreateSupplySetDialog.exec() -- same "patch the
    class, fill the fields a real user interaction would leave behind"
    precedent the pre-existing combo-driven tests already used, now
    also setting the Fix 4A "Set as Primary" checkbox."""

    def fake_exec(self):
        self.name_edit.setText(name)
        self.power_combo.setCurrentText(power)
        self.ground_combo.setCurrentText(ground)
        self.n_well_combo.setCurrentText(n_well)
        self.p_well_combo.setCurrentText(p_well)
        if set_as_primary and self.set_as_primary_checkbox.isEnabled():
            self.set_as_primary_checkbox.setChecked(True)
        return QDialog.DialogCode.Accepted

    return fake_exec


# --- Fix 4A: merged "Create Supply Set" flow (replaces the old combo-driven
# "Create New Supply Set..." entries on the Primary/Secondary pickers) ------


def test_supply_set_combo_replaces_the_old_combo_entries(qtbot):
    """Explicit request: "This replaces whatever separate Primary-
    supply-creation vs. Secondary-supply-creation UI currently exists"
    -- superseded by a further explicit follow-up request removing the
    "Primary Supply"/"Secondary Supply" picker fields ENTIRELY (not
    just their own "Create New Supply Set..." entries), and later
    CHANGE 2 (explicit request) turned the standalone
    `create_supply_set_button` itself into `supply_set_combo` -- the
    ONLY way to add a Supply Set at all (either an ancestor's EXISTING
    one or "Add New..."), and neither picker combo from before exists
    anymore."""
    dialog = CreatePowerDomainDialog({}, ["VDD", "VSS"], {}, "top")
    qtbot.addWidget(dialog)

    assert not hasattr(dialog, "primary_combo")
    assert not hasattr(dialog, "secondary_picker_combo")
    assert not hasattr(dialog, "primary_add_button")
    assert not hasattr(dialog, "add_secondary_button")
    assert not hasattr(dialog, "create_supply_set_button")
    assert dialog.supply_set_combo is not None


def test_creating_the_first_supply_set_offers_an_enabled_primary_checkbox(qtbot, monkeypatch):
    import frontend.src.power_intent_view.create_power_domain_dialog as mod

    captured = {}
    original_init = mod.CreateSupplySetDialog.__init__

    def spy_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        captured["primary_available"] = self.set_as_primary_checkbox.isEnabled()

    monkeypatch.setattr(mod.CreateSupplySetDialog, "__init__", spy_init)
    monkeypatch.setattr(mod.CreateSupplySetDialog, "exec", _fake_create_supply_set_exec("SS_NEW"))
    dialog = CreatePowerDomainDialog({}, ["VDD", "VSS"], {}, "top")
    qtbot.addWidget(dialog)

    dialog._prompt_create_and_assign_supply_set()

    assert captured["primary_available"] is True


def test_creating_a_supply_set_with_the_checkbox_checked_becomes_primary_immediately(qtbot, monkeypatch):
    import frontend.src.power_intent_view.create_power_domain_dialog as mod

    monkeypatch.setattr(mod.CreateSupplySetDialog, "exec", _fake_create_supply_set_exec("SS_NEW", set_as_primary=True))
    dialog = CreatePowerDomainDialog({}, ["VDD", "VSS"], {}, "top")
    qtbot.addWidget(dialog)

    dialog._prompt_create_and_assign_supply_set()

    assert len(dialog.new_supply_sets) == 1
    new_id = next(iter(dialog.new_supply_sets))
    assert dialog.new_supply_sets[new_id].name == "SS_NEW"
    # No separate "Add" click needed -- the checkbox alone determines it.
    assert dialog.primary_supply_set_id == new_id


def test_creating_a_supply_set_with_the_checkbox_unchecked_becomes_secondary(qtbot, monkeypatch):
    import frontend.src.power_intent_view.create_power_domain_dialog as mod

    monkeypatch.setattr(mod.CreateSupplySetDialog, "exec", _fake_create_supply_set_exec("SS_NEW", set_as_primary=False))
    dialog = CreatePowerDomainDialog({}, ["VDD", "VSS"], {}, "top")
    qtbot.addWidget(dialog)

    dialog._prompt_create_and_assign_supply_set()

    new_id = next(iter(dialog.new_supply_sets))
    assert dialog.primary_supply_set_id is None
    assert new_id in dialog.secondary_supply_set_ids


def test_creating_a_second_supply_set_has_a_disabled_checkbox_and_becomes_secondary(qtbot, monkeypatch):
    import frontend.src.power_intent_view.create_power_domain_dialog as mod

    monkeypatch.setattr(mod.CreateSupplySetDialog, "exec", _fake_create_supply_set_exec("SS_FIRST", set_as_primary=True))
    dialog = CreatePowerDomainDialog({}, ["VDD", "VSS"], {}, "top")
    qtbot.addWidget(dialog)
    dialog._prompt_create_and_assign_supply_set()  # SS_FIRST becomes Primary
    first_id = dialog.primary_supply_set_id

    captured = {}
    original_init = mod.CreateSupplySetDialog.__init__

    def spy_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        captured["primary_available"] = self.set_as_primary_checkbox.isEnabled()

    monkeypatch.setattr(mod.CreateSupplySetDialog, "__init__", spy_init)
    monkeypatch.setattr(mod.CreateSupplySetDialog, "exec", _fake_create_supply_set_exec("SS_SECOND", set_as_primary=True, power="VDD2", ground="VSS2"))
    dialog._prompt_create_and_assign_supply_set()

    assert captured["primary_available"] is False  # a Primary already exists
    second_id = next(sid for sid in dialog.new_supply_sets if sid != first_id)
    assert dialog.primary_supply_set_id == first_id  # unchanged
    assert second_id in dialog.secondary_supply_set_ids


def test_a_newly_created_supply_set_is_stamped_with_the_dialogs_own_owner_path(qtbot, monkeypatch):
    import frontend.src.power_intent_view.create_power_domain_dialog as mod

    monkeypatch.setattr(mod.CreateSupplySetDialog, "exec", _fake_create_supply_set_exec("SS_NEW"))
    dialog = CreatePowerDomainDialog({}, ["VDD", "VSS"], {}, "top/u_core")
    qtbot.addWidget(dialog)

    dialog._prompt_create_and_assign_supply_set()

    new_set = next(iter(dialog.new_supply_sets.values()))
    assert new_set.owner_path == "top/u_core"


# --- Fix 3C integration: cross-domain Supply Set name uniqueness, enforced
# at the point a NEW Supply Set is created via the merged flow above -------


def test_creating_a_supply_set_whose_name_collides_with_another_domain_is_rejected(qtbot, monkeypatch):
    import frontend.src.power_intent_view.create_power_domain_dialog as mod

    other_domain_supply_sets = {"s1": SupplySetPlacement(id="s1", name="SS_TAKEN", power="VDD", owner_path="top/u_dbg")}
    other_domains = {"d_other": PowerDomainPlacement(id="d_other", owner_path="top/u_dbg", name="PD_OTHER", color=QColor("#ff0000"), primary_supply_set="s1")}

    warned = []
    monkeypatch.setattr(message_box, "warning", lambda *args, **kwargs: warned.append(args))
    # First open: user (mistakenly) types the already-taken name; second
    # open (same retry-loop precedent AddPowerSupplyDialog already
    # established): a unique name.
    execs = [_fake_create_supply_set_exec("SS_TAKEN", set_as_primary=True), _fake_create_supply_set_exec("SS_UNIQUE", set_as_primary=True)]
    monkeypatch.setattr(mod.CreateSupplySetDialog, "exec", lambda self: execs.pop(0)(self))
    dialog = CreatePowerDomainDialog({}, ["VDD", "VSS"], {}, "top", all_domains=other_domains, all_supply_sets=other_domain_supply_sets)
    qtbot.addWidget(dialog)

    dialog._prompt_create_and_assign_supply_set()

    assert len(warned) == 1  # rejected once, then the retry succeeded
    assert len(dialog.new_supply_sets) == 1
    assert next(iter(dialog.new_supply_sets.values())).name == "SS_UNIQUE"


def test_creating_a_supply_set_with_a_unique_name_is_accepted_despite_other_domains_existing(qtbot, monkeypatch):
    import frontend.src.power_intent_view.create_power_domain_dialog as mod

    other_domain_supply_sets = {"s1": SupplySetPlacement(id="s1", name="SS_TAKEN", power="VDD", owner_path="top/u_dbg")}
    other_domains = {"d_other": PowerDomainPlacement(id="d_other", owner_path="top/u_dbg", name="PD_OTHER", color=QColor("#ff0000"), primary_supply_set="s1")}

    monkeypatch.setattr(mod.CreateSupplySetDialog, "exec", _fake_create_supply_set_exec("SS_UNIQUE", set_as_primary=True))
    dialog = CreatePowerDomainDialog({}, ["VDD", "VSS"], {}, "top", all_domains=other_domains, all_supply_sets=other_domain_supply_sets)
    qtbot.addWidget(dialog)

    dialog._prompt_create_and_assign_supply_set()

    assert len(dialog.new_supply_sets) == 1


def test_editing_a_domain_does_not_collide_with_its_own_already_chosen_supply_set_name(qtbot, monkeypatch):
    """The domain currently being edited must not block against its OWN
    already-referenced Supply Set names -- same `exclude_domain_id`
    precedent supply_sets_for_owner() already establishes."""
    import frontend.src.power_intent_view.create_power_domain_dialog as mod

    own_supply_sets = {"s1": SupplySetPlacement(id="s1", name="SS_OWN", power="VDD", owner_path="top")}
    own_domain = PowerDomainPlacement(id="d1", owner_path="top", name="PD1", color=QColor("#ff0000"), primary_supply_set="s1")
    all_domains = {"d1": own_domain}

    monkeypatch.setattr(mod.CreateSupplySetDialog, "exec", _fake_create_supply_set_exec("SS_OWN", set_as_primary=False))
    dialog = CreatePowerDomainDialog(
        own_supply_sets, ["VDD", "VSS"], {"d1": "PD1"}, "top", editing_domain_id="d1", initial=own_domain, all_domains=all_domains, all_supply_sets=own_supply_sets
    )
    qtbot.addWidget(dialog)

    dialog._prompt_create_and_assign_supply_set()

    assert len(dialog.new_supply_sets) == 1  # not rejected -- "SS_OWN" belongs to d1 itself


# --- Supply Set pin-combination uniqueness integration: enforced at the same
# creation point as the Fix 3C name check above, dialog-driven end-to-end --


def test_creating_a_supply_set_with_a_duplicate_pin_combination_in_the_same_domain_is_rejected(qtbot, monkeypatch):
    """Same Power Domain already has a Supply Set with Power=VDD/
    Ground=VSS -- a second one with an exact pin-combination match
    (different name) must be rejected, naming the conflicting Supply
    Set; retrying with a genuinely different combination succeeds."""
    import frontend.src.power_intent_view.create_power_domain_dialog as mod

    own_supply_sets = {"s1": SupplySetPlacement(id="s1", name="SS_FIRST", power="VDD", ground="VSS", n_well="VDD", p_well="VSS", owner_path="top")}
    own_domain = PowerDomainPlacement(id="d1", owner_path="top", name="PD1", color=QColor("#ff0000"), primary_supply_set="s1")
    all_domains = {"d1": own_domain}

    warned = []
    monkeypatch.setattr(message_box, "warning", lambda *args, **kwargs: warned.append(args))
    # First attempt: exact same Power/Ground/N-Well/P-Well as SS_FIRST
    # (the _fake_create_supply_set_exec defaults) -- rejected. Second
    # attempt: a genuinely different Power -- accepted.
    execs = [
        _fake_create_supply_set_exec("SS_DUPLICATE", set_as_primary=False),
        _fake_create_supply_set_exec("SS_UNIQUE", set_as_primary=False, power="VDD2", n_well="VDD2"),
    ]
    monkeypatch.setattr(mod.CreateSupplySetDialog, "exec", lambda self: execs.pop(0)(self))
    dialog = CreatePowerDomainDialog(
        own_supply_sets, ["VDD", "VSS", "VDD2"], {"d1": "PD1"}, "top", editing_domain_id="d1", initial=own_domain, all_domains=all_domains, all_supply_sets=own_supply_sets
    )
    qtbot.addWidget(dialog)

    dialog._prompt_create_and_assign_supply_set()

    assert len(warned) == 1  # rejected once, then the retry succeeded
    assert "SS_FIRST" in str(warned[0])  # names the conflicting Supply Set
    assert len(dialog.new_supply_sets) == 1
    assert next(iter(dialog.new_supply_sets.values())).name == "SS_UNIQUE"


def test_creating_a_supply_set_with_a_duplicate_pin_combination_in_a_different_domain_now_succeeds(qtbot, monkeypatch):
    """Bug fix (explicit policy correction) supersedes this test's own
    former assertion -- the pin-combination duplicate check used to be
    checked GLOBALLY (via `all_supply_sets`), incorrectly rejecting two
    Supply Sets in DIFFERENT, unrelated Power Domains that happen to
    reuse the same Power/Ground/N-Well/P-Well combination (e.g. VDD/VSS
    is completely normal, realistic naming reused independently by
    unrelated hierarchy scopes). The check is now scoped to ONLY the
    domain currently being created/edited's own Primary/Secondary
    (`_this_domains_supply_sets()`) -- a conflicting Supply Set that
    belongs to a genuinely different Power Domain no longer blocks
    anything at all. The narrower, SAME-domain case is still covered by
    test_creating_a_supply_set_with_a_duplicate_pin_combination_in_the_same_domain_is_rejected
    above, unchanged."""
    import frontend.src.power_intent_view.create_power_domain_dialog as mod

    other_domain_supply_sets = {"s1": SupplySetPlacement(id="s1", name="SS_ELSEWHERE", power="VDD", ground="VSS", n_well="VDD", p_well="VSS", owner_path="top/u_dbg")}
    other_domains = {"d_other": PowerDomainPlacement(id="d_other", owner_path="top/u_dbg", name="PD_OTHER", color=QColor("#ff0000"), primary_supply_set="s1")}

    warned = []
    monkeypatch.setattr(message_box, "warning", lambda *args, **kwargs: warned.append(args))
    monkeypatch.setattr(
        mod.CreateSupplySetDialog, "exec", _fake_create_supply_set_exec("SS_VDD_INT", set_as_primary=True, power="VDD", ground="VSS", n_well="VDD", p_well="VSS")
    )
    dialog = CreatePowerDomainDialog({}, ["VDD", "VSS"], {}, "top/u_core", all_domains=other_domains, all_supply_sets=other_domain_supply_sets)
    qtbot.addWidget(dialog)

    dialog._prompt_create_and_assign_supply_set()

    assert warned == []  # no combination-conflict warning at all -- different domains, exact same combination, now allowed
    assert len(dialog.new_supply_sets) == 1
    assert next(iter(dialog.new_supply_sets.values())).name == "SS_VDD_INT"


def test_creating_a_supply_set_with_a_duplicate_name_in_a_different_domain_is_still_rejected(qtbot, monkeypatch):
    """The SEPARATE, unchanged name-uniqueness rule (Fix 3C) still
    applies globally, regardless of pin combination -- narrowing the
    combination check's own scope (see the test above) doesn't touch
    this rule at all."""
    import frontend.src.power_intent_view.create_power_domain_dialog as mod

    other_domain_supply_sets = {"s1": SupplySetPlacement(id="s1", name="SS_SHARED_NAME", power="VDD", ground="VSS", owner_path="top/u_dbg")}
    other_domains = {"d_other": PowerDomainPlacement(id="d_other", owner_path="top/u_dbg", name="PD_OTHER", color=QColor("#ff0000"), primary_supply_set="s1")}

    warned = []
    monkeypatch.setattr(message_box, "warning", lambda *args, **kwargs: warned.append(args))
    execs = [
        _fake_create_supply_set_exec("SS_SHARED_NAME", set_as_primary=True, power="VDD2", ground="VSS2", n_well="", p_well=""),
        _fake_create_supply_set_exec("SS_UNIQUE_NAME", set_as_primary=True, power="VDD2", ground="VSS2", n_well="", p_well=""),
    ]
    monkeypatch.setattr(mod.CreateSupplySetDialog, "exec", lambda self: execs.pop(0)(self))
    dialog = CreatePowerDomainDialog({}, ["VDD2", "VSS2"], {}, "top/u_core", all_domains=other_domains, all_supply_sets=other_domain_supply_sets)
    qtbot.addWidget(dialog)

    dialog._prompt_create_and_assign_supply_set()

    assert len(warned) == 1
    assert "already used by another Power Domain" in str(warned[0])
    assert len(dialog.new_supply_sets) == 1
    assert next(iter(dialog.new_supply_sets.values())).name == "SS_UNIQUE_NAME"


def test_power_domain_dialog_secondary_supply_sets_add_and_remove(qtbot, monkeypatch):
    """Follow-up explicit request: with the Primary/Secondary picker
    fields removed entirely, "adding a Secondary" now means creating a
    fresh Supply Set via the merged flow with "Set as Primary" left
    unchecked (only possible once a Primary already exists -- see
    _prompt_create_and_assign_supply_set()'s own docstring).

    Bug fix: `available_supply_names` now includes every name actually
    referenced below ("VDD2"/"VDD3"/"VSS") -- previously only "VDD" was
    listed, so `power_combo.setCurrentText("VDD2")` (a non-editable
    combo, no matching item) silently no-opped to blank/None for BOTH
    SS_A and SS_B, making their real, resolved Power/Ground/N-Well/
    P-Well combinations accidentally IDENTICAL. This was always latent
    (a genuinely sloppy fixture, not a deliberate test of the duplicate-
    combination rule) but harmless before Bug 3's fix, since this test
    never populated `all_supply_sets` and the OLD, GLOBAL duplicate
    check had nothing to compare against; the NEW, correctly-scoped
    `_this_domains_supply_sets()` check (which pulls from THIS session's
    own already-created Supply Sets, not a caller-supplied snapshot)
    caught the real, accidental duplicate and rejected SS_B, and the
    monkeypatched one-shot `.exec()` fake looping back on rejection
    caused a genuine infinite loop -- found via a live hang, not
    assumed."""
    import frontend.src.power_intent_view.create_power_domain_dialog as mod

    dialog = CreatePowerDomainDialog({}, ["VDD", "VDD2", "VDD3", "VSS"], {}, "top")
    qtbot.addWidget(dialog)
    monkeypatch.setattr(mod.CreateSupplySetDialog, "exec", _fake_create_supply_set_exec("SS_PRIMARY", set_as_primary=True))
    dialog._prompt_create_and_assign_supply_set()
    monkeypatch.setattr(mod.CreateSupplySetDialog, "exec", _fake_create_supply_set_exec("SS_A", set_as_primary=False, power="VDD2"))
    dialog._prompt_create_and_assign_supply_set()
    monkeypatch.setattr(mod.CreateSupplySetDialog, "exec", _fake_create_supply_set_exec("SS_B", set_as_primary=False, power="VDD3"))
    dialog._prompt_create_and_assign_supply_set()

    assert [dialog.new_supply_sets[sid].name for sid in dialog.secondary_supply_set_ids] == ["SS_A", "SS_B"]
    items = [dialog.supply_set_list.item(i).text() for i in range(dialog.supply_set_list.count())]
    assert items == ["Primary: SS_PRIMARY", "Secondary: SS_A", "Secondary: SS_B"]

    dialog.supply_set_list.setCurrentRow(1)  # "Secondary: SS_A"
    dialog._remove_selected_supply_set()

    remaining_names = [dialog.new_supply_sets[sid].name for sid in dialog.secondary_supply_set_ids]
    assert remaining_names == ["SS_B"]
    assert dialog.supply_set_list.count() == 2


def test_power_domain_dialog_primary_supply_set_list_prefix_and_ordering(qtbot, monkeypatch):
    """"Add the Prefix 'Primary: '/'Secondary: '... Primary should
    always be on top" -- explicit request. Follow-up: exercised via the
    merged Supply Set creation flow now, not separate picker combos --
    created secondary first, primary second, list must still show
    Primary on top.

    Bug fix: SS_PRIMARY now gets its own distinct `power` ("VDD2", added
    to `available_supply_names`) -- previously both Supply Sets used
    every field's own default, resolving to the SAME real combination,
    same latent-fixture issue as
    test_power_domain_dialog_secondary_supply_sets_add_and_remove's own
    fix just above (see that test's own docstring for the full "why")."""
    import frontend.src.power_intent_view.create_power_domain_dialog as mod

    dialog = CreatePowerDomainDialog({}, ["VDD", "VDD2"], {}, "top")
    qtbot.addWidget(dialog)
    monkeypatch.setattr(mod.CreateSupplySetDialog, "exec", _fake_create_supply_set_exec("SS_SECONDARY", set_as_primary=False))
    dialog._prompt_create_and_assign_supply_set()
    monkeypatch.setattr(mod.CreateSupplySetDialog, "exec", _fake_create_supply_set_exec("SS_PRIMARY", set_as_primary=True, power="VDD2"))
    dialog._prompt_create_and_assign_supply_set()

    items = [dialog.supply_set_list.item(i).text() for i in range(dialog.supply_set_list.count())]
    assert items == ["Primary: SS_PRIMARY", "Secondary: SS_SECONDARY"]


# --- bug fix: unused/abandoned new entities never reach the diagram --------
# Explicit repro: start creating a Power Domain, create a Supply Set, and
# within it create a new Power named "VDD_X", then realize it's a mistake
# and create a replacement "VDD_Y" instead before that inner Supply Set
# dialog's own OK -- VDD_X must never appear anywhere once the outer Power
# Domain dialog itself is confirmed, even though it was briefly the
# current combo selection and "Add New..."'d into new_port_colors.


def _fake_create_supply_set_exec_with_abandoned_power_name(final_name: str, abandoned_name: str, ground: str = "VSS", set_as_primary: bool = True):
    """Simulates the exact repro's own inner Supply Set dialog session:
    Power's own "Add New..." is used TWICE (first `abandoned_name`, then
    `final_name` replacing it as the combo's current selection) before
    this inner dialog's OK -- new_port_colors accumulates BOTH names
    (see _register_new_supply()'s own "if name not in ..." check, which
    never prunes a superseded one), exactly like two real "Add New..."
    round trips through AddPowerSupplyDialog would, but without needing
    to drive that nested modal headlessly."""

    def fake_exec(self):
        self.name_edit.setText("SS1")
        self.new_port_colors[abandoned_name] = QColor("#111111")
        self.new_port_colors[final_name] = QColor("#222222")
        self.power_combo.offer_name(abandoned_name)
        self.power_combo.offer_name(final_name)
        self.power_combo.setCurrentText(final_name)  # the LAST one created -- abandoned_name is no longer selected anywhere
        self.ground_combo.setCurrentText(ground)
        if set_as_primary and self.set_as_primary_checkbox.isEnabled():
            self.set_as_primary_checkbox.setChecked(True)
        return QDialog.DialogCode.Accepted

    return fake_exec


def test_accept_discards_a_supply_name_abandoned_before_the_inner_dialogs_own_ok(qtbot, monkeypatch):
    """The exact reported repro, at the CreatePowerDomainDialog level:
    only VDD_Y (the final, actually-used name) and VSS (pre-existing)
    must survive accept() -- VDD_X (created then superseded before the
    inner Supply Set dialog's own OK) must not, even though it was
    briefly accumulated into new_port_colors/new_supply_ports."""
    import frontend.src.power_intent_view.create_power_domain_dialog as mod

    monkeypatch.setattr(mod.CreateSupplySetDialog, "exec", _fake_create_supply_set_exec_with_abandoned_power_name("VDD_Y", "VDD_X"))
    dialog = CreatePowerDomainDialog({}, ["VDD_X", "VDD_Y", "VSS"], {}, "top")
    qtbot.addWidget(dialog)
    dialog._prompt_create_and_assign_supply_set()
    assert dialog.new_supply_ports == {"VDD_X": QColor("#111111"), "VDD_Y": QColor("#222222")}  # both accumulated pre-accept, as the bug produced
    dialog.name_edit.setText("PD1")

    dialog.accept()

    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.new_supply_ports == {"VDD_Y": QColor("#222222")}  # VDD_X discarded
    assert len(dialog.new_supply_sets) == 1
    supply_set = next(iter(dialog.new_supply_sets.values()))
    assert supply_set.power == "VDD_Y"
    assert supply_set.ground == "VSS"


def test_accept_discards_an_entire_supply_set_removed_before_the_outer_ok(qtbot, monkeypatch):
    """A related edge case covered by the same fix: a Supply Set created
    then removed via "Remove Selected" before the outer OK -- unlike the
    abandoned-name case above, `_remove_selected_supply_set()` only ever
    frees the Primary/Secondary slot, it never prunes `new_supply_sets`
    itself -- must not leave its own supply names materialized either."""
    import frontend.src.power_intent_view.create_power_domain_dialog as mod

    dialog = CreatePowerDomainDialog({}, ["VDD1", "VDD2", "VSS"], {}, "top")
    qtbot.addWidget(dialog)
    monkeypatch.setattr(mod.CreateSupplySetDialog, "exec", _fake_create_supply_set_exec("SS_KEEP", set_as_primary=True, power="VDD1", n_well="VDD1"))
    dialog._prompt_create_and_assign_supply_set()
    monkeypatch.setattr(mod.CreateSupplySetDialog, "exec", _fake_create_supply_set_exec("SS_DOOMED", set_as_primary=False, power="VDD2", n_well="VDD2"))
    dialog._prompt_create_and_assign_supply_set()
    assert len(dialog.new_supply_sets) == 2
    dialog.supply_set_list.setCurrentRow(1)  # "Secondary: SS_DOOMED"
    dialog._remove_selected_supply_set()
    dialog.name_edit.setText("PD1")

    dialog.accept()

    assert dialog.result() == QDialog.DialogCode.Accepted
    assert len(dialog.new_supply_sets) == 1
    assert next(iter(dialog.new_supply_sets.values())).name == "SS_KEEP"


def test_power_domain_dialog_accept_rejects_missing_primary(qtbot, monkeypatch):
    calls = _warnings(monkeypatch)
    dialog = CreatePowerDomainDialog({}, ["VDD"], {}, "top")
    qtbot.addWidget(dialog)
    dialog.name_edit.setText("PD1")

    dialog.accept()

    assert dialog.result() == QDialog.DialogCode.Rejected
    assert calls[0][0] == "Missing Primary Supply Set"


def test_power_domain_dialog_accept_rejects_a_duplicate_domain_name(qtbot, monkeypatch):
    calls = _warnings(monkeypatch)
    existing = {"s1": SupplySetPlacement(id="s1", name="SS_A")}
    dialog = CreatePowerDomainDialog(existing, ["VDD"], {"other_domain": "PD_EXISTING"}, "top")
    qtbot.addWidget(dialog)
    dialog.name_edit.setText("PD_EXISTING")
    dialog._primary_id = "s1"  # a Primary is required to reach this check at all -- not the concern under test

    dialog.accept()

    assert dialog.result() == QDialog.DialogCode.Rejected
    assert calls[0][0] == "Duplicate Power Domain Name"


def test_power_domain_dialog_editing_excludes_its_own_name_from_the_uniqueness_check(qtbot):
    """Editing a domain without renaming it must not collide with its
    own pre-existing name."""
    existing = {"s1": SupplySetPlacement(id="s1", name="SS_A")}
    initial = PowerDomainPlacement(id="d1", owner_path="top", name="PD1", color=QColor("#ff0000"), primary_supply_set="s1")
    dialog = CreatePowerDomainDialog(existing, ["VDD"], {"d1": "PD1"}, "top", editing_domain_id="d1", initial=initial)

    dialog.accept()

    assert dialog.result() == QDialog.DialogCode.Accepted


def test_power_domain_dialog_prepopulates_from_initial_for_editing(qtbot):
    existing = {"s1": SupplySetPlacement(id="s1", name="SS_A"), "s2": SupplySetPlacement(id="s2", name="SS_B")}
    initial = PowerDomainPlacement(
        id="d1", owner_path="top", name="PD1", color=QColor("#123456"), transparency=0.25, primary_supply_set="s1", secondary_supply_sets=["s2"]
    )
    dialog = CreatePowerDomainDialog(existing, ["VDD"], {"d1": "PD1"}, "top", editing_domain_id="d1", initial=initial)
    qtbot.addWidget(dialog)

    assert dialog.name == "PD1"
    assert dialog.color == QColor("#123456")
    assert dialog.transparency == 0.8  # explicit request, supersedes the earlier adjustable value: always fixed at 80%, regardless of `initial.transparency`
    assert dialog.primary_supply_set_id == "s1"
    assert dialog.secondary_supply_set_ids == ["s2"]
    assert dialog.windowTitle() == "Edit Power Domain"


# --- name validation: no spaces allowed (Power Domain) ----------------------


def test_power_domain_dialog_rejects_a_name_with_an_internal_space(qtbot, monkeypatch):
    calls = _warnings(monkeypatch)
    dialog = CreatePowerDomainDialog({}, ["VDD"], {}, "top")
    qtbot.addWidget(dialog)
    dialog.name_edit.setText("PD BAD")

    dialog.accept()

    assert dialog.result() == QDialog.DialogCode.Rejected
    assert calls[0] == ("Invalid Name", "Name cannot contain spaces.")


def test_power_domain_dialog_accepts_a_name_with_only_leading_trailing_whitespace(qtbot):
    """Explicit request: surrounding whitespace is trimmed automatically
    (via `.name`'s own `.strip()`) before the space-check runs -- only
    an INTERNAL space is ever rejected."""
    existing = {"s1": SupplySetPlacement(id="s1", name="SS_A")}
    dialog = CreatePowerDomainDialog(existing, ["VDD"], {}, "top")
    qtbot.addWidget(dialog)
    dialog.name_edit.setText("  PD_OK  ")
    dialog._primary_id = "s1"  # a Primary is required to reach accept() at all -- not the concern under test

    dialog.accept()

    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.name == "PD_OK"


def test_power_domain_dialog_accepts_a_valid_underscore_separated_name(qtbot):
    existing = {"s1": SupplySetPlacement(id="s1", name="SS_A")}
    dialog = CreatePowerDomainDialog(existing, ["VDD"], {}, "top")
    qtbot.addWidget(dialog)
    dialog.name_edit.setText("PD_TOP_CORE")
    dialog._primary_id = "s1"  # a Primary is required to reach accept() at all -- not the concern under test

    dialog.accept()

    assert dialog.result() == QDialog.DialogCode.Accepted


# --- name validation: no spaces allowed (Supply Set) -------------------------


def _valid_supply_set_dialog(qtbot) -> CreateSupplySetDialog:
    dialog = CreateSupplySetDialog(["VDD", "VSS"])
    qtbot.addWidget(dialog)
    dialog.power_combo.setCurrentText("VDD")
    dialog.ground_combo.setCurrentText("VSS")
    dialog.n_well_combo.setCurrentText("VDD")
    dialog.p_well_combo.setCurrentText("VSS")
    return dialog


def test_supply_set_dialog_rejects_a_name_with_an_internal_space(qtbot, monkeypatch):
    calls = _warnings(monkeypatch)
    dialog = _valid_supply_set_dialog(qtbot)
    dialog.name_edit.setText("VSS SS")

    dialog.accept()

    assert dialog.result() == QDialog.DialogCode.Rejected
    assert calls[0] == ("Invalid Name", "Name cannot contain spaces.")


def test_supply_set_dialog_accepts_a_name_with_only_leading_trailing_whitespace(qtbot):
    dialog = _valid_supply_set_dialog(qtbot)
    dialog.name_edit.setText("  VSS_SS  ")

    dialog.accept()

    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.name == "VSS_SS"


def test_supply_set_dialog_accepts_a_valid_underscore_separated_name(qtbot):
    dialog = _valid_supply_set_dialog(qtbot)
    dialog.name_edit.setText("VSS_SS")

    dialog.accept()

    assert dialog.result() == QDialog.DialogCode.Accepted


# --- item 1: Edit Power Domain cascades Supply Set removal onto Supply Ports --


def _domain_view(qtbot, box_path: str = "top"):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes[box_path]
    return tab, view, box


def test_removing_a_supply_set_removes_its_own_exclusively_referenced_port(qtbot):
    tab, view, box = _domain_view(qtbot)
    port = view.add_supply_port(box, "VDD_ONLY", QColor("#ff0000"))
    supply_set = SupplySetPlacement(id="s1", name="SS1", power="VDD_ONLY", owner_path="top")
    domain = view.create_power_domain(box, "PD_TOP", QColor("#4caf50"), 0.5, "s1", [], {"s1": supply_set})
    assert port.key in view._shared_state.supply_port_placements

    view.edit_power_domain(domain.id, "PD_TOP", QColor("#4caf50"), 0.5, None, [], {})

    assert port.key not in view._shared_state.supply_port_placements
    assert view._supply_port_icons.get(port.key) is None
    _deselect_and_collect(tab)


def test_a_port_shared_across_two_supply_set_groups_survives_removing_only_one(qtbot):
    """Explicit test requirement: a port referenced by two Supply Set
    GROUPS within the same domain survives removal of one group, and is
    only removed once the LAST referencing group is also gone."""
    tab, view, box = _domain_view(qtbot)
    port = view.add_supply_port(box, "VDD_SHARED", QColor("#ff0000"))
    primary = SupplySetPlacement(id="s1", name="SS_PRIMARY", power="VDD_SHARED", owner_path="top")
    secondary = SupplySetPlacement(id="s2", name="SS_SECONDARY", power="VDD_SHARED", owner_path="top")
    domain = view.create_power_domain(box, "PD_TOP", QColor("#4caf50"), 0.5, "s1", ["s2"], {"s1": primary, "s2": secondary})

    # Remove only the secondary group -- primary still references the port.
    view.edit_power_domain(domain.id, "PD_TOP", QColor("#4caf50"), 0.5, "s1", [], {})
    assert port.key in view._shared_state.supply_port_placements  # still referenced by primary

    # Now remove the primary group too -- nothing left referencing it.
    view.edit_power_domain(domain.id, "PD_TOP", QColor("#4caf50"), 0.5, None, [], {})
    assert port.key not in view._shared_state.supply_port_placements
    _deselect_and_collect(tab)


def test_cascade_removal_is_scoped_to_the_domains_own_owner_path(qtbot):
    """A same-named port on a DIFFERENT hierarchy is never touched --
    the cascade only ever considers ports anchored to the domain's own
    owner_path, the same scope a Supply Set's own field values were
    drawn from at compose time."""
    tab, view, box = _domain_view(qtbot)
    view.add_supply_port(box, "VDD_SAME_NAME", QColor("#ff0000"))
    other_box = view._boxes["top/u_core"]
    # Constructed directly rather than via add_supply_port() -- items
    # 11/12's own new global naming-uniqueness rule would otherwise
    # (correctly) reject this exact same-name-on-an-unrelated-hierarchy
    # setup; this test's own concern is cascade-removal SCOPING, not
    # naming validation, so the collision is seeded directly, bypassing
    # that unrelated check.
    other_port = SupplyPortPlacement(id="other_port", owner_path=other_box.path, name="VDD_SAME_NAME", color=QColor("#00ff00"))
    view._shared_state.supply_port_placements[other_port.id] = other_port
    view._supply_port_icons[other_port.id] = SupplyPortIconItem(other_box, other_port)
    supply_set = SupplySetPlacement(id="s1", name="SS1", power="VDD_SAME_NAME", owner_path="top")
    domain = view.create_power_domain(box, "PD_TOP", QColor("#4caf50"), 0.5, "s1", [], {"s1": supply_set})

    view.edit_power_domain(domain.id, "PD_TOP", QColor("#4caf50"), 0.5, None, [], {})

    assert other_port.id in view._shared_state.supply_port_placements  # untouched -- different owner_path
    _deselect_and_collect(tab)


def test_cascade_removal_updates_the_properties_table_live(qtbot):
    """Item 1: "The Properties table... must update to reflect the
    removal -- no stale rows left behind." -- exercised through the same
    diagramStateChanged -> refresh_diagram_overlay() chain every other
    diagram edit already relies on."""
    tab, view, box = _domain_view(qtbot)
    view.add_supply_port(box, "VDD_ONLY", QColor("#ff0000"))
    supply_set = SupplySetPlacement(id="s1", name="SS1", power="VDD_ONLY", owner_path="top")
    domain = view.create_power_domain(box, "PD_TOP", QColor("#4caf50"), 0.5, "s1", [], {"s1": supply_set})
    rows_before = [tab.properties._tables["SupplyPort"].item(r, 1).text() for r in range(tab.properties._tables["SupplyPort"].rowCount())]
    assert "VDD_ONLY" in rows_before

    view.edit_power_domain(domain.id, "PD_TOP", QColor("#4caf50"), 0.5, None, [], {})

    rows_after = [tab.properties._tables["SupplyPort"].item(r, 1).text() for r in range(tab.properties._tables["SupplyPort"].rowCount())]
    assert "VDD_ONLY" not in rows_after
    _deselect_and_collect(tab)


def test_fix_4b_pruned_supply_set_row_disappears_from_the_properties_table(qtbot):
    """Fix 4B regression (b): removing a Supply Set from its Power
    Domain deletes both the underlying record AND the Properties
    table's own Supply Set row for it -- not merely hidden while the
    record still exists somewhere."""
    tab, view, box = _domain_view(qtbot)
    supply_set = SupplySetPlacement(id="s1", name="SS_PRUNED", power="VDD_ONLY", owner_path="top")
    domain = view.create_power_domain(box, "PD_TOP", QColor("#4caf50"), 0.5, "s1", [], {"s1": supply_set})
    names_before = [tab.properties._tables["SupplySet"].item(r, 0).text() for r in range(tab.properties._tables["SupplySet"].rowCount())]
    assert "SS_PRUNED" in names_before

    view.edit_power_domain(domain.id, "PD_TOP", QColor("#4caf50"), 0.5, None, [], {})

    names_after = [tab.properties._tables["SupplySet"].item(r, 0).text() for r in range(tab.properties._tables["SupplySet"].rowCount())]
    assert "SS_PRUNED" not in names_after
    _deselect_and_collect(tab)


def test_fix_4b_pruning_cascades_to_dependent_supply_ports_and_warns_about_retention(qtbot, monkeypatch):
    """Fix 4B regression (c): removing a Supply Set from its domain
    still cascades to the Supply Ports it referenced (pre-existing
    _cascade_remove_orphaned_supply_ports() logic, reused as-is) AND
    still warns about any Retention Strategy that referenced it
    (pre-existing _warn_about_retention_strategies_affected_by_removed_supply_sets(),
    also reused as-is) -- pruning the Supply Set's own record is a
    THIRD, independent cascade step alongside both of these, not a
    replacement for either."""
    warned = []
    monkeypatch.setattr(message_box, "warning", lambda *args, **kwargs: warned.append(args))
    tab, view, box = _domain_view(qtbot)
    port = view.add_supply_port(box, "VDD_ONLY", QColor("#ff0000"))
    primary = SupplySetPlacement(id="s1", name="SS_PRIMARY", power="VDD_ONLY", owner_path="top")
    domain = view.create_power_domain(box, "PD_TOP", QColor("#4caf50"), 0.5, "s1", [], {"s1": primary})
    strategy = view.create_retention_strategy(box, domain, "RET1", "s1", "s1", "save", "restore", ["top/out1"], {})

    view.edit_power_domain(domain.id, "PD_TOP", QColor("#4caf50"), 0.5, None, [], {})  # removes s1 entirely

    assert "s1" not in view._domain_state.supply_sets  # pruned
    assert port.key not in view._shared_state.supply_port_placements  # cascaded (pre-existing logic)
    assert len(warned) == 1  # Retention warning still fires (pre-existing logic)
    assert strategy.id in view._retention_state.strategies  # strategy itself untouched -- warning-only, same as before
    _deselect_and_collect(tab)


def test_removed_supply_set_is_deleted_when_orphaned(qtbot):
    """Fix 4B (explicit request, supersedes an earlier "unlink, not
    delete" behavior): once a Supply Set is removed from a Power
    Domain's own list and isn't referenced by ANY OTHER Power Domain,
    its underlying record is deleted entirely -- not left behind as an
    orphaned row nobody can reach from the Properties table anymore."""
    tab, view, box = _domain_view(qtbot)
    view.add_supply_port(box, "VDD_ONLY", QColor("#ff0000"))
    supply_set = SupplySetPlacement(id="s1", name="SS1", power="VDD_ONLY", owner_path="top")
    domain = view.create_power_domain(box, "PD_TOP", QColor("#4caf50"), 0.5, "s1", [], {"s1": supply_set})

    view.edit_power_domain(domain.id, "PD_TOP", QColor("#4caf50"), 0.5, None, [], {})

    assert "s1" not in view._domain_state.supply_sets
    _deselect_and_collect(tab)


def test_removed_supply_set_survives_if_still_referenced_by_another_domain(qtbot):
    """Contrast case: a Supply Set removed from ONE domain's list stays
    alive if some OTHER Power Domain still references it -- pruning
    only ever deletes a genuinely orphaned record."""
    tab, view, box = _domain_view(qtbot)
    other_box = view._boxes["top/u_core"]
    shared_set = SupplySetPlacement(id="s1", name="SS_SHARED", power="VDD_SHARED", owner_path="top")
    domain = view.create_power_domain(box, "PD_TOP", QColor("#4caf50"), 0.5, "s1", [], {"s1": shared_set})
    # A second domain also references the SAME Supply Set id (its own
    # SupplySetPlacement object, still tracked once in `supply_sets`).
    view._domain_state.domains["d2"] = PowerDomainPlacement(
        id="d2", owner_path="top/u_core", owner_paths=["top/u_core"], name="PD_CORE", color=QColor("#2196f3"), primary_supply_set="s1"
    )

    view.edit_power_domain(domain.id, "PD_TOP", QColor("#4caf50"), 0.5, None, [], {})  # removes s1 from PD_TOP only

    assert "s1" in view._domain_state.supply_sets  # d2 still references it
    _deselect_and_collect(tab)


def test_edit_power_domain_with_no_supply_set_change_removes_nothing(qtbot):
    tab, view, box = _domain_view(qtbot)
    port = view.add_supply_port(box, "VDD_ONLY", QColor("#ff0000"))
    supply_set = SupplySetPlacement(id="s1", name="SS1", power="VDD_ONLY", owner_path="top")
    domain = view.create_power_domain(box, "PD_TOP", QColor("#4caf50"), 0.5, "s1", [], {"s1": supply_set})

    view.edit_power_domain(domain.id, "PD_TOP_RENAMED", QColor("#4caf50"), 0.5, "s1", [], {})  # same Supply Set, just renamed the domain

    assert port.key in view._shared_state.supply_port_placements
    _deselect_and_collect(tab)


# --- bug fix: Supply Set picker scoped to the current hierarchy only -------


def test_supply_sets_for_owner_excludes_a_different_hierarchys_supply_sets():
    state = PowerDomainDiagramState(
        supply_sets={
            "s1": SupplySetPlacement(id="s1", name="SS_TOP", power="VDD", owner_path="top"),
            "s2": SupplySetPlacement(id="s2", name="SS_CORE", power="VDD_CORE", owner_path="top/u_core"),
        }
    )

    assert set(supply_sets_for_owner(state, "top")) == {"s1"}
    assert set(supply_sets_for_owner(state, "top/u_core")) == {"s2"}


def test_supply_sets_for_owner_excludes_a_supply_set_claimed_by_another_domain():
    state = PowerDomainDiagramState(
        supply_sets={
            "s1": SupplySetPlacement(id="s1", name="SS_A", power="VDD", owner_path="top"),
            "s2": SupplySetPlacement(id="s2", name="SS_B", power="VDD2", owner_path="top"),
        },
        domains={"d1": PowerDomainPlacement(id="d1", owner_path="top", name="PD1", color=QColor("#4caf50"), primary_supply_set="s1")},
    )

    available = supply_sets_for_owner(state, "top")

    assert set(available) == {"s2"}  # s1 already claimed by d1


def test_supply_sets_for_owner_still_offers_the_domain_being_edited_its_own_sets():
    state = PowerDomainDiagramState(
        supply_sets={"s1": SupplySetPlacement(id="s1", name="SS_A", power="VDD", owner_path="top")},
        domains={"d1": PowerDomainPlacement(id="d1", owner_path="top", name="PD1", color=QColor("#4caf50"), primary_supply_set="s1")},
    )

    available = supply_sets_for_owner(state, "top", exclude_domain_id="d1")

    assert set(available) == {"s1"}  # not excluded from its own domain's own edit session


# --- Fix 3C: Supply Set names must be unique ACROSS Power Domains ----------


def test_supply_set_name_used_by_another_domain_detects_a_cross_domain_collision():
    supply_sets = {
        "s1": SupplySetPlacement(id="s1", name="SS_SHARED_NAME", power="VDD", owner_path="top"),
    }
    domains = {
        "d1": PowerDomainPlacement(id="d1", owner_path="top", name="PD1", color=QColor("#4caf50"), primary_supply_set="s1"),
    }

    assert supply_set_name_used_by_another_domain("SS_SHARED_NAME", domains, supply_sets) is True
    assert supply_set_name_used_by_another_domain("SS_UNUSED", domains, supply_sets) is False


def test_supply_set_name_used_by_another_domain_checks_secondary_sets_too():
    supply_sets = {
        "s1": SupplySetPlacement(id="s1", name="SS_PRIMARY", power="VDD", owner_path="top"),
        "s2": SupplySetPlacement(id="s2", name="SS_SECONDARY", power="VDD2", owner_path="top"),
    }
    domains = {
        "d1": PowerDomainPlacement(id="d1", owner_path="top", name="PD1", color=QColor("#4caf50"), primary_supply_set="s1", secondary_supply_sets=["s2"]),
    }

    assert supply_set_name_used_by_another_domain("SS_SECONDARY", domains, supply_sets) is True


def test_supply_set_name_used_by_another_domain_excludes_its_own_domain():
    """Editing/creating within domain d1 itself must not collide with
    d1's own already-chosen Supply Set names -- same `exclude_domain_id`
    precedent supply_sets_for_owner() already establishes."""
    supply_sets = {
        "s1": SupplySetPlacement(id="s1", name="SS_A", power="VDD", owner_path="top"),
    }
    domains = {
        "d1": PowerDomainPlacement(id="d1", owner_path="top", name="PD1", color=QColor("#4caf50"), primary_supply_set="s1"),
    }

    assert supply_set_name_used_by_another_domain("SS_A", domains, supply_sets, exclude_domain_id="d1") is False
    assert supply_set_name_used_by_another_domain("SS_A", domains, supply_sets, exclude_domain_id="d2") is True


def test_supply_set_name_used_by_another_domain_ignores_a_domain_with_no_supply_sets():
    domains = {"d1": PowerDomainPlacement(id="d1", owner_path="top", name="PD1", color=QColor("#4caf50"))}

    assert supply_set_name_used_by_another_domain("SS_ANYTHING", domains, {}) is False


# --- Supply Set pin-combination uniqueness (explicit request) --------------
# A Supply Set's own full Power/Ground/N-Well/P-Well combination must not
# exactly duplicate another existing Supply Set's -- GLOBAL scope, checked
# directly against every SupplySetPlacement that exists (unlike the name
# check above, not funneled through `domains` first). Partial overlap
# (sharing exactly one rail) stays legal.


def test_supply_set_with_duplicate_pin_combination_detects_a_full_match():
    existing = SupplySetPlacement(id="s1", name="SS_EXISTING", power="VDD", ground="VSS", n_well="VDD", p_well="VSS", owner_path="top")
    supply_sets = {"s1": existing}

    conflict = supply_set_with_duplicate_pin_combination("VDD", "VSS", "VDD", "VSS", supply_sets)
    assert conflict is existing


def test_supply_set_with_duplicate_pin_combination_allows_a_single_shared_rail():
    """PARTIAL overlap is explicitly legal -- sharing exactly one rail
    (Ground here) with a different Power must NOT be flagged. Matches
    Case 5's own golden fixture setup (SS_VDD/SS_VDD_SUB sharing VSS as
    Ground)."""
    existing = SupplySetPlacement(id="s1", name="SS_VDD", power="VDD", ground="VSS", n_well="VDD", p_well="VSS", owner_path="sram")
    supply_sets = {"s1": existing}

    conflict = supply_set_with_duplicate_pin_combination("VDD_SUB", "VSS", "VDD_SUB", "VSS", supply_sets)
    assert conflict is None


def test_supply_set_with_duplicate_pin_combination_allows_a_shared_power_rail():
    """Mirror of the above -- sharing Power (not Ground) alone is also
    legal. Matches Case 6's own golden fixture setup (SS_VDD/SS_VDD_SUB
    sharing VDD as Power)."""
    existing = SupplySetPlacement(id="s1", name="SS_VDD", power="VDD", ground="VSS", n_well="VDD", p_well="VSS", owner_path="sram")
    supply_sets = {"s1": existing}

    conflict = supply_set_with_duplicate_pin_combination("VDD", "VSS_SUB", "VDD", "VSS_SUB", supply_sets)
    assert conflict is None


def test_supply_set_with_duplicate_pin_combination_requires_all_four_fields_to_match():
    """Power+Ground alone matching, but N-Well or P-Well differing, is
    NOT a full match -- every one of the 4 fields must agree."""
    existing = SupplySetPlacement(id="s1", name="SS_EXISTING", power="VDD", ground="VSS", n_well="VDD", p_well="VSS", owner_path="top")
    supply_sets = {"s1": existing}

    assert supply_set_with_duplicate_pin_combination("VDD", "VSS", "VSS", "VSS", supply_sets) is None  # n_well differs
    assert supply_set_with_duplicate_pin_combination("VDD", "VSS", "VDD", "VDD", supply_sets) is None  # p_well differs


def test_supply_set_with_duplicate_pin_combination_excludes_its_own_id():
    """Same `exclude_id` precedent as `exclude_domain_id` elsewhere --
    a Supply Set being edited in place (no such flow exists yet, but
    the parameter is there for one) must not collide with its own
    already-saved combination."""
    existing = SupplySetPlacement(id="s1", name="SS_EXISTING", power="VDD", ground="VSS", n_well="VDD", p_well="VSS", owner_path="top")
    supply_sets = {"s1": existing}

    assert supply_set_with_duplicate_pin_combination("VDD", "VSS", "VDD", "VSS", supply_sets, exclude_id="s1") is None
    assert supply_set_with_duplicate_pin_combination("VDD", "VSS", "VDD", "VSS", supply_sets, exclude_id="s2") is existing


def test_create_power_domain_dialog_only_offers_supply_sets_from_the_clicked_hierarchy(qtbot, monkeypatch):
    """End-to-end regression test (explicit request): Supply Sets under
    two different hierarchies exist; opening "Create Power Domain" for
    one of them must only offer that hierarchy's own Supply Sets."""
    tab, view, box = _domain_view(qtbot)
    other_box = view._boxes["top/u_core"]
    view._domain_state.supply_sets["s_top"] = SupplySetPlacement(id="s_top", name="SS_TOP", power="VDD", owner_path="top")
    view._domain_state.supply_sets["s_core"] = SupplySetPlacement(id="s_core", name="SS_CORE", power="VDD_CORE", owner_path="top/u_core")

    captured = {}
    original_init = CreatePowerDomainDialog.__init__

    def _capture_init(self, existing_supply_sets, *args, **kwargs):
        captured["existing_supply_sets"] = existing_supply_sets
        original_init(self, existing_supply_sets, *args, **kwargs)

    monkeypatch.setattr(CreatePowerDomainDialog, "__init__", _capture_init)
    monkeypatch.setattr(CreatePowerDomainDialog, "exec", lambda self: QDialog.DialogCode.Rejected)

    view._prompt_create_power_domain(box)
    assert {s.name for s in captured["existing_supply_sets"].values()} == {"SS_TOP"}

    view._prompt_create_power_domain(other_box)
    assert {s.name for s in captured["existing_supply_sets"].values()} == {"SS_CORE"}
    _deselect_and_collect(tab)


# --- item 2: warn about Retention strategies affected by a removed Supply Set -


def _retention_warnings(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "frontend.src.power_intent_view.power_intent_diagram_view.message_box.warning",
        lambda *args, **kwargs: calls.append(args[1:]) or None,
    )
    return calls


def test_removing_a_supply_set_warns_about_retention_strategies_that_reference_it(qtbot, monkeypatch):
    tab, view, box = _domain_view(qtbot)
    primary = SupplySetPlacement(id="s1", name="SS_PRIMARY", power="VDD", owner_path="top")
    retention_set = SupplySetPlacement(id="s2", name="SS_RETENTION", power="VDD_RET", owner_path="top")
    domain = view.create_power_domain(box, "PD_TOP", QColor("#4caf50"), 0.5, "s1", ["s2"], {"s1": primary, "s2": retention_set})
    view.create_retention_strategy(box, domain, "RET1", "s1", "s2", "save", "restore", [], {})
    warnings = _retention_warnings(monkeypatch)

    view.edit_power_domain(domain.id, "PD_TOP", QColor("#4caf50"), 0.5, "s1", [], {})  # drops the "s2" (retention) group

    assert len(warnings) == 1
    assert warnings[0][0] == view._RETENTION_REVIEW_TITLE
    assert "RET1" in warnings[0][1]
    _deselect_and_collect(tab)


def test_no_warning_when_no_retention_strategy_is_affected(qtbot, monkeypatch):
    tab, view, box = _domain_view(qtbot)
    primary = SupplySetPlacement(id="s1", name="SS_PRIMARY", power="VDD", owner_path="top")
    secondary = SupplySetPlacement(id="s2", name="SS_SECONDARY", power="VDD_ALT", owner_path="top")
    domain = view.create_power_domain(box, "PD_TOP", QColor("#4caf50"), 0.5, "s1", ["s2"], {"s1": primary, "s2": secondary})
    warnings = _retention_warnings(monkeypatch)

    view.edit_power_domain(domain.id, "PD_TOP", QColor("#4caf50"), 0.5, "s1", [], {})  # no Retention strategies exist at all

    assert warnings == []
    _deselect_and_collect(tab)


def test_no_warning_when_a_retention_strategy_references_a_supply_set_that_survives(qtbot, monkeypatch):
    tab, view, box = _domain_view(qtbot)
    primary = SupplySetPlacement(id="s1", name="SS_PRIMARY", power="VDD", owner_path="top")
    secondary = SupplySetPlacement(id="s2", name="SS_SECONDARY", power="VDD_ALT", owner_path="top")
    domain = view.create_power_domain(box, "PD_TOP", QColor("#4caf50"), 0.5, "s1", ["s2"], {"s1": primary, "s2": secondary})
    view.create_retention_strategy(box, domain, "RET1", "s1", "s1", "save", "restore", [], {})  # only ever references s1
    warnings = _retention_warnings(monkeypatch)

    view.edit_power_domain(domain.id, "PD_TOP", QColor("#4caf50"), 0.5, "s1", [], {})  # removes s2, which RET1 never used

    assert warnings == []
    _deselect_and_collect(tab)


def test_warning_does_not_modify_the_affected_retention_strategy(qtbot, monkeypatch):
    """Explicit request: "this is a WARNING/review prompt, not an
    automatic edit -- do not silently modify the Retention strategies"."""
    tab, view, box = _domain_view(qtbot)
    primary = SupplySetPlacement(id="s1", name="SS_PRIMARY", power="VDD", owner_path="top")
    retention_set = SupplySetPlacement(id="s2", name="SS_RETENTION", power="VDD_RET", owner_path="top")
    domain = view.create_power_domain(box, "PD_TOP", QColor("#4caf50"), 0.5, "s1", ["s2"], {"s1": primary, "s2": retention_set})
    strategy = view.create_retention_strategy(box, domain, "RET1", "s1", "s2", "save", "restore", [], {})
    _retention_warnings(monkeypatch)

    view.edit_power_domain(domain.id, "PD_TOP", QColor("#4caf50"), 0.5, "s1", [], {})

    unchanged = view._retention_state.strategies[strategy.id]
    assert unchanged.retention_supply_set_id == "s2"  # left exactly as it was
    assert unchanged.name == "RET1"
    _deselect_and_collect(tab)


# --- Bug fix: nested "Add New..." AddPowerSupplyDialog must match the standalone
# --- one exactly -- same class, same "Applies to" row (and therefore the same
# --- size), same title. Root-caused via a live diagnostic (not assumed): the
# --- nested dialog IS the same AddPowerSupplyDialog class (confirmed -- there
# --- was never a second, separately-maintained implementation), but
# --- _SupplyNameCombo._on_index_changed() never passed `target_path` at all
# --- (so the standalone-only "Applies to" row was simply missing, the real
# --- source of the reported height/sizing mismatch) and never updated the
# --- dialog's own window title away from the class's generic default. -------


def _capture_nested_add_power_supply_dialog(monkeypatch, outer_dialog):
    """Drives the REAL "Create Supply Set..." -> "Add New..." gesture on
    `outer_dialog` (a CreatePowerDomainDialog) and returns the nested
    AddPowerSupplyDialog instance it constructs, without ever letting
    either modal actually block (both `.exec()` calls are monkeypatched
    to capture `self` and return Rejected)."""
    import frontend.src.power_intent_view.create_power_domain_dialog as mod

    captured = {}

    def fake_supply_set_exec(self):
        captured["supply_set_dialog"] = self
        return QDialog.DialogCode.Rejected

    def fake_add_power_exec(self):
        captured["add_power_dialog"] = self
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(mod.CreateSupplySetDialog, "exec", fake_supply_set_exec)
    monkeypatch.setattr(mod.AddPowerSupplyDialog, "exec", fake_add_power_exec)

    outer_dialog._prompt_create_and_assign_supply_set()
    ss_dialog = captured["supply_set_dialog"]
    index = ss_dialog.power_combo.findText("Add New...")
    ss_dialog.power_combo.setCurrentIndex(index)  # the real signal-driven gesture
    return captured["add_power_dialog"]


def test_nested_add_new_dialog_is_the_same_class_with_the_same_add_state_button(qtbot, monkeypatch):
    standalone = AddPowerSupplyDialog(target_path="top", is_internal_supply=False)
    qtbot.addWidget(standalone)

    outer = CreatePowerDomainDialog({}, ["VDD1"], {}, "top")
    qtbot.addWidget(outer)
    nested = _capture_nested_add_power_supply_dialog(monkeypatch, outer)

    assert type(nested) is type(standalone) is AddPowerSupplyDialog
    assert nested.states_widget is not None
    assert nested.states_widget._add_button.text() == "+"  # the "+" Power State row button, present same as standalone


def test_nested_add_new_dialog_matches_the_standalones_size_exactly(qtbot, monkeypatch):
    """The actual reported symptom: the nested dialog never showed the
    "Applies to: <hierarchy>" row the standalone one does, making it
    genuinely shorter -- confirmed via a live diagnostic (519x143 vs.
    519x164) before this fix, matching exactly afterward."""
    standalone = AddPowerSupplyDialog(target_path="top", is_internal_supply=False)
    qtbot.addWidget(standalone)

    outer = CreatePowerDomainDialog({}, ["VDD1"], {}, "top")
    qtbot.addWidget(outer)
    nested = _capture_nested_add_power_supply_dialog(monkeypatch, outer)

    assert nested.sizeHint() == standalone.sizeHint()
    assert nested.layout().count() == standalone.layout().count()  # same number of form rows


def test_nested_add_new_dialog_shows_the_applies_to_row_for_the_domains_own_hierarchy(qtbot, monkeypatch):
    outer = CreatePowerDomainDialog({}, ["VDD1"], {}, "top/u_core")
    qtbot.addWidget(outer)
    nested = _capture_nested_add_power_supply_dialog(monkeypatch, outer)

    assert nested._target_path == "top/u_core"


def test_nested_add_new_dialog_title_matches_standalone_for_both_supply_port_and_internal_supply(qtbot, monkeypatch):
    top_outer = CreatePowerDomainDialog({}, ["VDD1"], {}, "top")
    qtbot.addWidget(top_outer)
    nested_supply_port = _capture_nested_add_power_supply_dialog(monkeypatch, top_outer)
    assert nested_supply_port.windowTitle() == "Add Power Supply"

    inner_outer = CreatePowerDomainDialog({}, ["VDD1"], {}, "top/u_core")
    qtbot.addWidget(inner_outer)
    nested_internal_supply = _capture_nested_add_power_supply_dialog(monkeypatch, inner_outer)
    assert nested_internal_supply.windowTitle() == "Add Internal Supply"


def test_nested_internal_supply_inherited_power_states_message_matches_standalone(qtbot, monkeypatch):
    """Bug 4: the "Inherited Power States" message itself was already
    correct and identical in both dialogs (confirmed via a live
    diagnostic before touching any code) -- this test locks that in as
    a permanent guarantee now that the surrounding dialog (title, size)
    is also unified."""
    standalone = AddPowerSupplyDialog(is_internal_supply=True)
    qtbot.addWidget(standalone)

    inner_outer = CreatePowerDomainDialog({}, ["VDD1"], {}, "top/u_core")
    qtbot.addWidget(inner_outer)
    nested = _capture_nested_add_power_supply_dialog(monkeypatch, inner_outer)

    assert nested.states_widget is None
    assert nested.inherited_states_label is not None
    assert nested.inherited_states_label.text() == standalone.inherited_states_label.text()


# --- item 1: field rename "Domain Name" -> "Name" + separator -------------


def test_create_power_domain_dialog_field_layout_and_separator(qtbot):
    """Rename ONLY (explicit request): the label text reads "Name:", not
    "Domain Name:" -- no change to the underlying field's validation/
    behavior/data. A separator sits right after "Applies to:", same
    convention already applied to Power Switch/Level Shifter/Retention/
    Isolation dialogs."""
    from PySide6.QtWidgets import QFrame

    from frontend.src.shared.widgets.target_hierarchy_label import APPLIES_TO_CAPTION

    dialog = CreatePowerDomainDialog({}, [], {}, "top")
    qtbot.addWidget(dialog)

    fields_form = dialog.layout()
    kinds = []
    for row in range(fields_form.rowCount()):
        label_item = fields_form.itemAt(row, fields_form.ItemRole.LabelRole)
        field_item = fields_form.itemAt(row, fields_form.ItemRole.FieldRole)
        if label_item is not None:
            kinds.append(label_item.widget().text())
        elif field_item is not None and isinstance(field_item.widget(), QFrame):
            kinds.append("separator")
        else:
            kinds.append("other")  # e.g. the trailing single-widget buttons row

    assert kinds[:5] == [APPLIES_TO_CAPTION, "separator", "Name:", "Supply Set:", "Supply Sets:"]
    assert "Domain Name:" not in kinds


# --- item 2: "Applies to" capped at 10 hierarchies -------------------------


def test_power_domain_dialog_applies_to_is_capped_at_ten_hierarchies(qtbot):
    """(c): the SAME shared make_target_hierarchy_label() capping
    behavior (see test_target_hierarchy_label.py for the full coverage)
    is wired into the Power Domain dialog too, not just tested in
    isolation."""
    paths = [f"top/u_{i}" for i in range(15)]
    dialog = CreatePowerDomainDialog({}, [], {}, "top", owner_paths=paths)
    qtbot.addWidget(dialog)

    fields_form = dialog.layout()
    applies_to_value = fields_form.itemAt(0, fields_form.ItemRole.FieldRole).widget()
    lines = applies_to_value.text().split("\n")
    assert lines[:10] == paths[:10]
    assert lines[10] == "... and 5 more"
