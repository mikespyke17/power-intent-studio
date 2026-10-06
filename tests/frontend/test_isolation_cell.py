"""Isolation Cell -- diagram-only/mock-first, architecturally following
the RETENTION model (one shared entity, multiple diagram attachment
points across a Power Domain's own boundary blocks), NOT Power
Switch's own independently-replicated-per-hierarchy one. See
isolation_overlay.py's own module docstring for the full reasoning.

Covers: tool palette wiring, boundary-block validation (single- and
multi-hierarchy), the dialog's own core validation/domain-driven
Elements table, create/edit/delete + cascade + undo, the multi-
hierarchy one-entity-several-icons model and its own group-highlight,
live colour-follow, Properties table wiring, and right-click Edit/
Info/Delete on the badge."""

import gc

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QDialog, QMenu, QMessageBox

from frontend.src.power_intent_view.diagram_tool_palette import DiagramMode
from frontend.src.power_intent_view.isolation_dialog import IsolationDialog, IsolationInfoDialog
from frontend.src.power_intent_view.isolation_overlay import IsolationBadgeIconItem, IsolationStrategyPlacement
from frontend.src.power_intent_view.power_domain_overlay import PowerDomainPlacement, SupplySetPlacement
from frontend.src.power_intent_view.power_intent_tab import PowerIntentTab
from frontend.src.shared.mock_backend import build_mock_design
from frontend.src.shared.widgets import message_box


def _deselect_and_collect(tab: PowerIntentTab) -> None:
    if tab.diagram._view.scene() is not None:
        tab.diagram._view.scene().clearSelection()
    gc.collect()


def _tab_with_single_hierarchy_domain(qtbot):
    """A ready-to-isolate fixture: mock design, a Power Domain rooted at
    "top" (a single-hierarchy domain -- "top" is its own, only,
    boundary block), a real Supply Port on "top" and a matching Supply
    Set referencing it as Power."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top"]
    domain = PowerDomainPlacement(id="d1", owner_path="top", name="PD_TOP", color=QColor("#ff0000"))
    view._domain_state.domains["d1"] = domain
    port = view.add_supply_port(box, "VDD1", QColor("#123456"))
    supply_set = SupplySetPlacement(id="ss1", name="SS1", power="VDD1", ground="VSS", owner_paths=["top"])
    view._domain_state.supply_sets["ss1"] = supply_set
    return tab, view, box, domain, port, supply_set


def _tab_with_multi_hierarchy_domain(qtbot):
    """A multi-hierarchy Power Domain spanning "top/u_core" and
    "top/u_dbg" -- both are its own boundary blocks."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    domain = PowerDomainPlacement(
        id="d1", owner_path="top/u_core", owner_paths=["top/u_core", "top/u_dbg"], name="PD_MULTI", color=QColor("#ff0000")
    )
    view._domain_state.domains["d1"] = domain
    return tab, view, domain


# --- tool palette -------------------------------------------------------


def test_isolation_cell_mode_sets_the_isolation_cursor(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view

    view.set_mode(DiagramMode.ISOLATION_CELL)

    from frontend.src.power_intent_view.diagram_tool_icons import isolation_cell_cursor

    assert view.viewport().cursor().pixmap().toImage() == isolation_cell_cursor().pixmap().toImage()


# --- boundary-block validation (single- and multi-hierarchy) -----------


def test_boundary_block_of_a_single_hierarchy_domain_is_a_valid_target(qtbot):
    tab, view, box, domain, port, supply_set = _tab_with_single_hierarchy_domain(qtbot)
    assert view._validate_isolation_target(box) is None
    _deselect_and_collect(tab)


def test_a_block_merely_nested_inside_a_domain_is_not_a_valid_target(qtbot):
    """The core distinction from Retention: a block that BELONGS to the
    domain (nested inside its subtree) but is not itself the domain's
    own boundary/topmost block must be REJECTED -- Retention would
    accept this same block (find_effective_domain_for_path()'s own
    cascading rule), Isolation must not (find_domain_for_owner()'s own
    exact-ownership rule)."""
    tab, view, box, domain, port, supply_set = _tab_with_single_hierarchy_domain(qtbot)
    nested_box = view._boxes["top/u_core"]  # belongs to PD_TOP's subtree, but doesn't OWN it

    error = view._validate_isolation_target(nested_box)

    assert error == view._ISOLATION_NO_BOUNDARY_ERROR
    _deselect_and_collect(tab)


def test_a_block_with_no_power_domain_at_all_is_not_a_valid_target(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top"]  # no Power Domain ever assigned

    error = view._validate_isolation_target(box)

    assert error == view._ISOLATION_NO_BOUNDARY_ERROR
    _deselect_and_collect(tab)


def test_every_member_boundary_block_of_a_multi_hierarchy_domain_is_valid(qtbot):
    """As many valid boundary blocks as there are member hierarchies --
    explicit request."""
    tab, view, domain = _tab_with_multi_hierarchy_domain(qtbot)

    assert view._validate_isolation_target(view._boxes["top/u_core"]) is None
    assert view._validate_isolation_target(view._boxes["top/u_dbg"]) is None
    # "top" itself does NOT own this domain (it's the domain's own
    # ancestor, not one of its owner_paths) -- not a boundary block.
    assert view._validate_isolation_target(view._boxes["top"]) == view._ISOLATION_NO_BOUNDARY_ERROR
    _deselect_and_collect(tab)


def test_add_isolation_context_menu_action_is_enabled_only_for_a_valid_boundary_block(qtbot):
    tab, view, box, domain, port, supply_set = _tab_with_single_hierarchy_domain(qtbot)
    menu = QMenu()

    actions = view._extra_context_menu_actions(menu, box)
    add_isolation_action = next(a for a, _cb in actions if a.text() == "Add Isolation")
    assert add_isolation_action.isEnabled() is True

    menu2 = QMenu()
    actions2 = view._extra_context_menu_actions(menu2, view._boxes["top/u_core"])
    add_isolation_action2 = next(a for a, _cb in actions2 if a.text() == "Add Isolation")
    assert add_isolation_action2.isEnabled() is False
    _deselect_and_collect(tab)


def test_add_isolation_shows_the_validation_error_and_never_opens_the_dialog(qtbot, monkeypatch):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    opened = []
    monkeypatch.setattr(IsolationDialog, "exec", lambda self: opened.append(1) or QDialog.DialogCode.Rejected)
    warnings = []
    monkeypatch.setattr(message_box, "warning", lambda parent, title, text: warnings.append((title, text)))
    box = view._boxes["top"]  # no Power Domain -- rejected

    view._prompt_add_isolation(box)

    assert opened == []
    assert len(warnings) == 1
    assert warnings[0][1] == view._ISOLATION_NO_BOUNDARY_ERROR
    assert view._isolation_state.strategies == {}
    _deselect_and_collect(tab)


# --- create/edit/delete + cascade + undo --------------------------------


def test_create_isolation_materializes_a_badge_immediately(qtbot):
    """"top/clk", "top/sleep_n" are both IN -- a single-direction
    selection, so exactly one (normal, unflipped) icon materializes;
    see the dedicated direction-variant tests below for the
    mixed/all-output cases (section 5)."""
    tab, view, box, domain, port, supply_set = _tab_with_single_hierarchy_domain(qtbot)

    placement = view.create_isolation(box, "ISO1", "d1", "ss1", None, None, "low", "self", "iso_ctrl", "high", ["top/clk", "top/sleep_n"])

    assert placement.id in view._isolation_state.strategies
    assert placement.id in view._isolation_icons
    icons = view._isolation_icons[placement.id]
    assert len(icons) == 1
    assert isinstance(icons[0], IsolationBadgeIconItem)
    assert icons[0].owner_box is box
    assert icons[0].flipped is False
    _deselect_and_collect(tab)


def test_create_isolation_rejects_a_duplicate_name(qtbot):
    tab, view, box, domain, port, supply_set = _tab_with_single_hierarchy_domain(qtbot)
    view.create_isolation(box, "ISO1", "d1", "ss1", None, None, "low", "self", "ctrl", "high", ["top/clk"])

    try:
        view.create_isolation(box, "ISO1", "d1", "ss1", None, None, "low", "self", "ctrl", "high", ["top/out1"])
        assert False, "expected ValueError"
    except ValueError:
        pass
    _deselect_and_collect(tab)


def test_a_multi_hierarchy_isolation_materializes_one_icon_per_member_boundary_block(qtbot):
    """The core "one shared entity, several attachment points" model,
    per the Retention precedent this feature explicitly follows."""
    tab, view, domain = _tab_with_multi_hierarchy_domain(qtbot)
    box_core = view._boxes["top/u_core"]

    placement = view.create_isolation(
        box_core, "ISO_MULTI", "d1", "ss1", None, None, "low", "self", "ctrl", "high", ["top/u_core/clk", "top/u_dbg/clk"]
    )

    icons = view._isolation_icons[placement.id]
    assert len(icons) == 2
    assert sorted(i.owner_box.path for i in icons) == ["top/u_core", "top/u_dbg"]
    _deselect_and_collect(tab)


def test_clicking_one_icon_of_a_multi_attachment_isolation_highlights_every_sibling(qtbot):
    """Explicit request: reuse Retention's own group-highlight
    mechanism -- clicking any one icon highlights ALL of them together,
    fluorescent yellow."""
    tab, view, domain = _tab_with_multi_hierarchy_domain(qtbot)
    box_core = view._boxes["top/u_core"]
    placement = view.create_isolation(
        box_core, "ISO_MULTI", "d1", "ss1", None, None, "low", "self", "ctrl", "high", ["top/u_core/clk", "top/u_dbg/clk"]
    )
    icons = view._isolation_icons[placement.id]

    icons[0].setSelected(True)
    view._on_scene_selection_changed()

    assert all(icon._click_highlighted for icon in icons)
    _deselect_and_collect(tab)


def test_edit_isolation_can_change_which_blocks_it_spans(qtbot):
    tab, view, domain = _tab_with_multi_hierarchy_domain(qtbot)
    box_core = view._boxes["top/u_core"]
    placement = view.create_isolation(
        box_core, "ISO_MULTI", "d1", "ss1", None, None, "low", "self", "ctrl", "high", ["top/u_core/clk", "top/u_dbg/clk"]
    )

    view.edit_isolation(placement.id, "ISO_MULTI", "d1", "ss1", None, None, "high", "parent", "ctrl2", "low", ["top/u_core/clk"])

    icons = view._isolation_icons[placement.id]
    assert len(icons) == 1
    assert icons[0].owner_box.path == "top/u_core"
    updated = view._isolation_state.strategies[placement.id]
    assert updated.clamp_value == "high"
    assert updated.location == "parent"
    _deselect_and_collect(tab)


def test_delete_isolation_removes_every_icon_across_every_block(qtbot):
    tab, view, domain = _tab_with_multi_hierarchy_domain(qtbot)
    box_core = view._boxes["top/u_core"]
    placement = view.create_isolation(
        box_core, "ISO_MULTI", "d1", "ss1", None, None, "low", "self", "ctrl", "high", ["top/u_core/clk", "top/u_dbg/clk"]
    )

    view.delete_isolation(placement.id)

    assert placement.id not in view._isolation_state.strategies
    assert placement.id not in view._isolation_icons
    _deselect_and_collect(tab)


def test_prompt_delete_isolation_shows_a_confirmation_and_respects_cancel(qtbot, monkeypatch):
    tab, view, box, domain, port, supply_set = _tab_with_single_hierarchy_domain(qtbot)
    placement = view.create_isolation(box, "ISO1", "d1", "ss1", None, None, "low", "self", "ctrl", "high", ["top/clk"])
    monkeypatch.setattr(message_box, "question", lambda *a, **k: QMessageBox.StandardButton.No)

    view._prompt_delete_isolation(placement.id)

    assert placement.id in view._isolation_state.strategies
    _deselect_and_collect(tab)


def test_prompt_delete_isolation_confirmed_removes_it(qtbot, monkeypatch):
    tab, view, box, domain, port, supply_set = _tab_with_single_hierarchy_domain(qtbot)
    placement = view.create_isolation(box, "ISO1", "d1", "ss1", None, None, "low", "self", "ctrl", "high", ["top/clk"])
    monkeypatch.setattr(message_box, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)

    view._prompt_delete_isolation(placement.id)

    assert placement.id not in view._isolation_state.strategies
    _deselect_and_collect(tab)


def test_removing_the_power_domain_cascades_away_its_isolation_cells(qtbot):
    """An Isolation can only exist on an already-created Power Domain's
    own boundary -- the domain disappearing must sweep it away too,
    same reasoning already established for Retention/Power Switch."""
    tab, view, box, domain, port, supply_set = _tab_with_single_hierarchy_domain(qtbot)
    placement = view.create_isolation(box, "ISO1", "d1", "ss1", None, None, "low", "self", "ctrl", "high", ["top/clk"])

    view.remove_power_domain(box)

    assert "d1" not in view._domain_state.domains
    assert placement.id not in view._isolation_state.strategies
    assert placement.id not in view._isolation_icons
    _deselect_and_collect(tab)


def test_removing_the_power_domain_and_its_isolation_undo_together(qtbot):
    tab, view, box, domain, port, supply_set = _tab_with_single_hierarchy_domain(qtbot)
    placement = view.create_isolation(box, "ISO1", "d1", "ss1", None, None, "low", "self", "ctrl", "high", ["top/clk"])

    view.remove_power_domain(box)
    view._undo_stack.undo()

    assert "d1" in view._domain_state.domains
    assert placement.id in view._isolation_state.strategies
    assert placement.id in view._isolation_icons
    _deselect_and_collect(tab)


def test_create_isolation_is_a_single_undoable_step(qtbot):
    tab, view, box, domain, port, supply_set = _tab_with_single_hierarchy_domain(qtbot)

    placement = view.create_isolation(box, "ISO1", "d1", "ss1", None, None, "low", "self", "ctrl", "high", ["top/clk"])
    view._undo_stack.undo()

    assert placement.id not in view._isolation_state.strategies
    assert placement.id not in view._isolation_icons
    _deselect_and_collect(tab)


# --- live colour-follow ---------------------------------------------------


def test_isolation_icon_color_follows_the_isolation_supply_sets_power_port(qtbot):
    tab, view, box, domain, port, supply_set = _tab_with_single_hierarchy_domain(qtbot)

    placement = view.create_isolation(box, "ISO1", "d1", "ss1", None, None, "low", "self", "ctrl", "high", ["top/clk"])

    icon = view._isolation_icons[placement.id][0]
    assert icon.color == QColor("#123456")
    _deselect_and_collect(tab)


def test_isolation_icon_color_updates_live_when_the_source_port_is_recolored(qtbot):
    """Explicit request: same live-binding convention as Power Switch's
    own "colour follows Supply Out" rule -- updates automatically, no
    rebuild needed."""
    tab, view, box, domain, port, supply_set = _tab_with_single_hierarchy_domain(qtbot)
    placement = view.create_isolation(box, "ISO1", "d1", "ss1", None, None, "low", "self", "ctrl", "high", ["top/clk"])
    icon = view._isolation_icons[placement.id][0]

    port.recolor(QColor("#abcdef"))

    assert icon.color == QColor("#abcdef")
    _deselect_and_collect(tab)


def test_isolation_icon_color_follows_a_supply_port_on_a_different_hierarchy_than_the_target(qtbot):
    """Bug fix, explicit request: the Isolation Supply Set is very
    commonly composed on an ANCESTOR block (e.g. "top"), while the
    Isolation itself attaches to a nested boundary block (e.g.
    "top/u_core") -- a normal pattern, not an edge case. An earlier,
    broken version scoped the colour-source search to the BADGE's own
    attachment block instead of the Supply Set's own owner_paths,
    silently falling back to default grey whenever the two differed."""
    tab, view, domain = _tab_with_multi_hierarchy_domain(qtbot)
    top_box = view._boxes["top"]
    vdd = view.add_supply_port(top_box, "VDD1", QColor("#123456"))
    ss1 = SupplySetPlacement(id="ss1", name="SS1", power="VDD1", owner_paths=["top"])
    view._domain_state.supply_sets["ss1"] = ss1
    box_core = view._boxes["top/u_core"]

    placement = view.create_isolation(box_core, "ISO1", "d1", "ss1", None, None, "low", "self", "ctrl", "high", ["top/u_core/clk"])

    icon = view._isolation_icons[placement.id][0]
    assert icon.color == QColor("#123456")

    vdd.recolor(QColor("#abcdef"))

    assert icon.color == QColor("#abcdef")
    _deselect_and_collect(tab)


# --- Item 2: Location-dependent placement (inside vs. outside) -----------


def test_location_self_renders_inside_the_boundary_block(qtbot):
    tab, view, box, domain, port, supply_set = _tab_with_single_hierarchy_domain(qtbot)

    placement = view.create_isolation(box, "ISO1", "d1", "ss1", None, None, "low", "self", "ctrl", "high", ["top/clk", "top/sleep_n"])

    icon = view._isolation_icons[placement.id][0]
    assert icon.pos().x() > box.rect().left()  # inside -- to the RIGHT of the left edge
    assert icon.pos().y() >= box.rect().top()  # near the top-left interior corner
    _deselect_and_collect(tab)


def test_location_parent_and_fanout_render_outside_on_the_left_edge(qtbot):
    """Existing behavior, confirmed unaffected by the new Location
    dependency."""
    tab, view, box, domain, port, supply_set = _tab_with_single_hierarchy_domain(qtbot)

    parent_placement = view.create_isolation(box, "ISO_PARENT", "d1", "ss1", None, None, "low", "parent", "ctrl", "high", ["top/clk", "top/sleep_n"])
    fanout_placement = view.create_isolation(box, "ISO_FANOUT", "d1", "ss1", None, None, "low", "fanout", "ctrl", "high", ["top/clk", "top/sleep_n"])

    parent_icon = view._isolation_icons[parent_placement.id][0]
    fanout_icon = view._isolation_icons[fanout_placement.id][0]
    assert parent_icon.pos().x() < box.rect().left()  # outside -- to the LEFT of the left edge
    assert fanout_icon.pos().x() < box.rect().left()
    _deselect_and_collect(tab)


# --- Self/parent pair alignment (explicit request, "sram example" repro) ----


def test_self_and_parent_isolation_pair_align_on_the_same_horizontal_line(qtbot):
    """A self-placed and a parent-placed Isolation on the SAME boundary
    block must share the exact same Y (a mirrored pair straddling the
    block's own left edge), rather than stacking one above the other --
    even though `box` here already carries a top-edge Supply Port
    ("VDD1", via _tab_with_single_hierarchy_domain()'s own fixture),
    triggering the pre-existing label-collision clearance (see
    IsolationBadgeIconItem's own docstring) -- the pair must still align
    AT whatever height that clearance pushes them to, not just when
    there's no clearance in play.

    Bug 3 (explicit request) supersedes the exact Y this pair lands at
    -- previously "pushed down past VDD1's own label," now "centered
    within the space below that label" (see IsolationBadgeIconItem's
    own _centered_y() docstring) -- the SAME-Y alignment guarantee this
    test is actually about is completely unchanged and still asserted
    directly; only the shared expected-Y computation below was updated
    to the new formula."""
    tab, view, box, domain, port, supply_set = _tab_with_single_hierarchy_domain(qtbot)

    self_placement = view.create_isolation(box, "ISO_SELF", "d1", "ss1", None, None, "low", "self", "ctrl", "high", ["top/clk"])
    parent_placement = view.create_isolation(box, "ISO_PARENT", "d1", "ss1", None, None, "low", "parent", "ctrl", "high", ["top/clk"])

    self_icon = view._isolation_icons[self_placement.id][0]
    parent_icon = view._isolation_icons[parent_placement.id][0]
    assert self_icon.pos().y() == parent_icon.pos().y()  # same horizontal line
    assert self_icon.pos().x() > box.rect().left()  # self -- inside
    assert parent_icon.pos().x() < box.rect().left()  # parent -- outside

    from frontend.src.power_intent_view.supply_port_overlay import INTERIOR_TOP_MARGIN_FOR_SUPPLY_PORT
    from frontend.src.power_intent_view.isolation_overlay import _centered_y

    expected_y = _centered_y(box.rect(), INTERIOR_TOP_MARGIN_FOR_SUPPLY_PORT, 0, 1)
    assert self_icon.pos().y() == expected_y  # centered below VDD1's own label
    assert parent_icon.pos().y() == expected_y  # -- and the outside badge moved down WITH it, to stay aligned
    _deselect_and_collect(tab)


def test_a_second_self_badge_stacks_below_the_aligned_pair_without_overlapping_it(qtbot):
    """Explicit requirement: additional Isolation icons beyond the
    matched self+parent pair still use the existing anti-collision
    stacking, avoiding both each other and the aligned pair."""
    tab, view, box, domain, port, supply_set = _tab_with_single_hierarchy_domain(qtbot)

    self_placement = view.create_isolation(box, "ISO_SELF_1", "d1", "ss1", None, None, "low", "self", "ctrl", "high", ["top/clk"])
    parent_placement = view.create_isolation(box, "ISO_PARENT", "d1", "ss1", None, None, "low", "parent", "ctrl", "high", ["top/clk"])
    second_self_placement = view.create_isolation(box, "ISO_SELF_2", "d1", "ss1", None, None, "low", "self", "ctrl", "high", ["top/sleep_n"])

    self_icon = view._isolation_icons[self_placement.id][0]
    parent_icon = view._isolation_icons[parent_placement.id][0]
    second_self_icon = view._isolation_icons[second_self_placement.id][0]
    assert self_icon.pos().y() == parent_icon.pos().y()  # the pair is still aligned
    assert second_self_icon.pos().y() > self_icon.pos().y()  # the extra self badge stacks below it
    assert second_self_icon.pos() != parent_icon.pos()  # and doesn't land on the outside badge either
    _deselect_and_collect(tab)


def test_editing_isolations_location_recomputes_the_alignment(qtbot):
    """Explicit request: the pairing/alignment must recompute on Edit,
    not leave a stale position from before the Location change. Two
    self-placed Isolations start stacked (no parent counterpart to
    align with); editing the second one's own Location to "parent"
    must immediately re-align it with the first (now its only
    same-slot counterpart), not leave it at its old, stale self-stack
    position."""
    tab, view, box, domain, port, supply_set = _tab_with_single_hierarchy_domain(qtbot)
    placement_a = view.create_isolation(box, "ISO_A", "d1", "ss1", None, None, "low", "self", "ctrl", "high", ["top/clk"])
    placement_b = view.create_isolation(box, "ISO_B", "d1", "ss1", None, None, "low", "self", "ctrl", "high", ["top/sleep_n"])
    icon_a = view._isolation_icons[placement_a.id][0]
    icon_b_before = view._isolation_icons[placement_b.id][0]
    assert icon_b_before.pos().y() != icon_a.pos().y()  # stacked, not aligned -- both are "self"

    view.edit_isolation(placement_b.id, "ISO_B", "d1", "ss1", None, None, "low", "parent", "ctrl", "high", ["top/sleep_n"])

    icon_a_after = view._isolation_icons[placement_a.id][0]
    icon_b_after = view._isolation_icons[placement_b.id][0]
    assert icon_b_after.pos().x() < box.rect().left()  # now outside, matching its new Location
    assert icon_b_after.pos().y() == icon_a_after.pos().y()  # re-aligned with ISO_A, not stuck at its old stacked Y
    _deselect_and_collect(tab)


def test_location_self_placement_applies_independently_per_boundary_block(qtbot):
    """Multi-hierarchy scope, explicit request: the placement rule
    applies independently to EACH boundary block's own icon instance."""
    tab, view, domain = _tab_with_multi_hierarchy_domain(qtbot)
    box_core = view._boxes["top/u_core"]
    box_dbg = view._boxes["top/u_dbg"]

    placement = view.create_isolation(box_core, "ISO1", "d1", "ss1", None, None, "low", "self", "ctrl", "high", ["top/u_core/clk", "top/u_dbg/clk"])

    icons = view._isolation_icons[placement.id]
    core_icon = next(i for i in icons if i.owner_box is box_core)
    dbg_icon = next(i for i in icons if i.owner_box is box_dbg)
    assert core_icon.pos().x() > box_core.rect().left()
    assert dbg_icon.pos().x() > box_dbg.rect().left()
    _deselect_and_collect(tab)


# --- Item 5: direction-based icon variant, per boundary block ------------


def test_all_output_elements_render_a_single_flipped_icon(qtbot):
    tab, view, box, domain, port, supply_set = _tab_with_single_hierarchy_domain(qtbot)

    placement = view.create_isolation(box, "ISO_OUT", "d1", "ss1", None, None, "low", "parent", "ctrl", "high", ["top/out1", "top/out2"])

    icons = view._isolation_icons[placement.id]
    assert len(icons) == 1
    assert icons[0].flipped is True
    _deselect_and_collect(tab)


def test_mixed_direction_elements_render_two_adjacent_icons(qtbot):
    tab, view, box, domain, port, supply_set = _tab_with_single_hierarchy_domain(qtbot)

    placement = view.create_isolation(box, "ISO_MIX", "d1", "ss1", None, None, "low", "parent", "ctrl", "high", ["top/clk", "top/out1"])

    icons = view._isolation_icons[placement.id]
    assert len(icons) == 2
    assert sorted(i.flipped for i in icons) == [False, True]
    assert icons[0].pos() != icons[1].pos()  # adjacent, not overlapping
    _deselect_and_collect(tab)


def test_direction_variant_is_computed_independently_per_boundary_block(qtbot):
    """Multi-hierarchy, explicit request: block A all-input renders
    normal, block B all-output renders flipped, independently -- even
    though the rule as a whole is "mixed" globally."""
    tab, view, domain = _tab_with_multi_hierarchy_domain(qtbot)
    box_core = view._boxes["top/u_core"]  # will use "clk" (IN)
    box_dbg = view._boxes["top/u_dbg"]  # will use "clk" (IN) too -- dbg has no OUT port, use core's own out1 instead

    # top/u_core has clk(IN)/out1(OUT)/out2(OUT)/out3(OUT); top/u_dbg has only clk(IN).
    placement = view.create_isolation(box_core, "ISO_SPLIT", "d1", "ss1", None, None, "low", "parent", "ctrl", "high", ["top/u_core/out1", "top/u_dbg/clk"])

    icons = view._isolation_icons[placement.id]
    core_icon = next(i for i in icons if i.owner_box is box_core)
    dbg_icon = next(i for i in icons if i.owner_box is box_dbg)
    assert core_icon.flipped is True  # all-output locally
    assert dbg_icon.flipped is False  # all-input locally
    assert len(icons) == 2  # one icon per block, no mixed-variant doubling anywhere
    _deselect_and_collect(tab)


def test_editing_elements_updates_the_direction_variant_live(qtbot):
    """Item 6, explicit request: editing an Isolation's own elements
    updates the diagram's icon variant(s) immediately, no manual
    refresh needed."""
    tab, view, box, domain, port, supply_set = _tab_with_single_hierarchy_domain(qtbot)
    placement = view.create_isolation(box, "ISO1", "d1", "ss1", None, None, "low", "parent", "ctrl", "high", ["top/clk"])
    assert len(view._isolation_icons[placement.id]) == 1
    assert view._isolation_icons[placement.id][0].flipped is False

    view.edit_isolation(placement.id, "ISO1", "d1", "ss1", None, None, "low", "parent", "ctrl", "high", ["top/clk", "top/out1"])

    icons = view._isolation_icons[placement.id]
    assert len(icons) == 2
    assert sorted(i.flipped for i in icons) == [False, True]
    _deselect_and_collect(tab)


def test_group_highlight_covers_every_variant_of_a_mixed_direction_block(qtbot):
    """Item 7, explicit request: clicking ANY one icon -- regardless of
    variant -- highlights every icon of the same rule, including both
    the normal and flipped icons sharing a mixed-direction block."""
    tab, view, box, domain, port, supply_set = _tab_with_single_hierarchy_domain(qtbot)
    placement = view.create_isolation(box, "ISO_MIX", "d1", "ss1", None, None, "low", "parent", "ctrl", "high", ["top/clk", "top/out1"])
    icons = view._isolation_icons[placement.id]

    icons[1].setSelected(True)  # click the FLIPPED one specifically
    view._on_scene_selection_changed()

    assert all(icon._click_highlighted for icon in icons)
    _deselect_and_collect(tab)


def test_isolation_icon_color_falls_back_to_default_grey_when_unresolved(qtbot):
    tab, view, box, domain, port, supply_set = _tab_with_single_hierarchy_domain(qtbot)
    # a Supply Set whose own Power field matches no real placed port
    orphan_set = SupplySetPlacement(id="ss_orphan", name="SS_ORPHAN", power="NO_SUCH_PORT", owner_paths=["top"])
    view._domain_state.supply_sets["ss_orphan"] = orphan_set

    placement = view.create_isolation(box, "ISO1", "d1", "ss_orphan", None, None, "low", "self", "ctrl", "high", ["top/clk"])

    icon = view._isolation_icons[placement.id][0]
    assert icon.color == QColor("#9e9e9e")
    _deselect_and_collect(tab)


# --- Properties table -----------------------------------------------------


def test_properties_table_shows_a_diagram_only_isolation_row(qtbot):
    tab, view, box, domain, port, supply_set = _tab_with_single_hierarchy_domain(qtbot)
    view.create_isolation(box, "ISO1", "d1", "ss1", None, None, "low", "self", "iso_ctrl", "high", ["top/clk"])

    tab._refresh_diagram_overlay_properties()

    table = tab.properties._tables["IsolationStrategy"]
    rows = [table.item(r, 0).text() for r in range(table.rowCount())]
    assert "ISO1" in rows
    row = next(r for r in range(table.rowCount()) if table.item(r, 0).text() == "ISO1")
    assert table.item(row, 1).text() == "PD_TOP"  # Power Domain
    assert table.item(row, 2).text() == "SS1"  # Isolation Supply Set
    assert table.item(row, 5).text() == "Low"  # Clamp Value
    assert table.item(row, 6).text() == "self"  # Location
    assert table.item(row, 7).text() == "iso_ctrl"  # Isolation Control
    assert table.item(row, 8).text() == "High"  # Isolation Sense
    _deselect_and_collect(tab)


def test_properties_table_updates_live_on_edit_and_delete(qtbot):
    tab, view, box, domain, port, supply_set = _tab_with_single_hierarchy_domain(qtbot)
    placement = view.create_isolation(box, "ISO1", "d1", "ss1", None, None, "low", "self", "ctrl", "high", ["top/clk"])
    tab._refresh_diagram_overlay_properties()

    view.edit_isolation(placement.id, "ISO1_RENAMED", "d1", "ss1", None, None, "keep", "fanout", "ctrl2", "low", ["top/clk"])
    tab._refresh_diagram_overlay_properties()
    table = tab.properties._tables["IsolationStrategy"]
    names = [table.item(r, 0).text() for r in range(table.rowCount())]
    assert "ISO1_RENAMED" in names
    assert "ISO1" not in names

    view.delete_isolation(placement.id)
    tab._refresh_diagram_overlay_properties()
    names = [table.item(r, 0).text() for r in range(table.rowCount())]
    assert names == []
    _deselect_and_collect(tab)


def test_properties_table_elements_cell_expands_beyond_ten(qtbot):
    """Explicit request: up to 10 elements shown, "Show all" reveals
    the complete list inside a bounded-height, internally-scrollable
    QListWidget (not by growing the row/label unboundedly -- see
    _ElementsCellWidget's own docstring for the rendering bug this
    fixes)."""
    from frontend.src.power_intent_view.properties_panel import _ElementsCellWidget

    tab, view, box, domain, port, supply_set = _tab_with_single_hierarchy_domain(qtbot)
    elements = [f"top/e{i}" for i in range(15)]
    view.create_isolation(box, "ISO1", "d1", "ss1", None, None, "low", "self", "ctrl", "high", elements)
    tab._refresh_diagram_overlay_properties()

    table = tab.properties._tables["IsolationStrategy"]
    row = next(r for r in range(table.rowCount()) if table.item(r, 0).text() == "ISO1")
    from frontend.src.power_intent_view.properties_panel import _ISOLATION_ELEMENTS_COLUMN

    widget = table.cellWidget(row, _ISOLATION_ELEMENTS_COLUMN)
    assert isinstance(widget, _ElementsCellWidget)
    assert widget._show_all_button is not None
    assert len(widget._label.text().split("\n")) == 10
    assert widget._list_widget is None

    widget._show_all_button.click()

    assert widget._list_widget is not None
    assert widget._list_widget.count() == 15
    assert [widget._list_widget.item(i).text() for i in range(15)] == elements
    assert widget._list_widget.maximumHeight() == widget._EXPANDED_MAX_HEIGHT
    assert widget._show_all_button.isHidden()
    _deselect_and_collect(tab)


# --- right-click Edit/Info/Delete on the badge -----------------------------


def test_isolation_badge_context_menu_actions(qtbot, monkeypatch):
    tab, view, box, domain, port, supply_set = _tab_with_single_hierarchy_domain(qtbot)
    placement = view.create_isolation(box, "ISO1", "d1", "ss1", None, None, "low", "self", "ctrl", "high", ["top/clk"])
    icon = view._isolation_icons[placement.id][0]

    menu = QMenu()
    actions = view._isolation_context_menu_actions(menu, icon)

    assert [a.text() for a, _cb in actions] == ["Edit", "Info", "Delete"]
    _deselect_and_collect(tab)


def test_isolation_badge_at_finds_the_right_icon(qtbot):
    tab, view, box, domain, port, supply_set = _tab_with_single_hierarchy_domain(qtbot)
    placement = view.create_isolation(box, "ISO1", "d1", "ss1", None, None, "low", "self", "ctrl", "high", ["top/clk"])
    icon = view._isolation_icons[placement.id][0]

    found = view._isolation_badge_at(view.mapFromScene(icon.scenePos()))

    assert found is icon
    _deselect_and_collect(tab)


def test_prompt_show_isolation_info_opens_the_info_dialog(qtbot, monkeypatch):
    tab, view, box, domain, port, supply_set = _tab_with_single_hierarchy_domain(qtbot)
    placement = view.create_isolation(box, "ISO1", "d1", "ss1", None, None, "low", "self", "ctrl", "high", ["top/clk"])
    icon = view._isolation_icons[placement.id][0]
    opened = []
    monkeypatch.setattr(IsolationInfoDialog, "exec", lambda self: opened.append(1) or QDialog.DialogCode.Accepted)

    view._prompt_show_isolation_info(icon)

    assert opened == [1]
    _deselect_and_collect(tab)


def test_prompt_edit_isolation_reopens_prefilled_and_applies_changes(qtbot, monkeypatch):
    tab, view, box, domain, port, supply_set = _tab_with_single_hierarchy_domain(qtbot)
    placement = view.create_isolation(box, "ISO1", "d1", "ss1", None, None, "low", "self", "ctrl", "high", ["top/clk"])
    icon = view._isolation_icons[placement.id][0]

    def fake_exec(self):
        assert self.name == "ISO1"  # pre-filled
        assert self.power_domain_id == "d1"
        self.name_edit.setText("ISO1_EDITED")
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(IsolationDialog, "exec", fake_exec)

    view._prompt_edit_isolation(icon.strategy_id)

    assert view._isolation_state.strategies[placement.id].name == "ISO1_EDITED"
    _deselect_and_collect(tab)
