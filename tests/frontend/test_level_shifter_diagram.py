"""Level Shifter -- diagram-only/mock-first, architecturally following
the ISOLATION model (explicit request, section 5 of the spec): one
shared entity, multiple diagram attachment points across a Power
Domain's own boundary blocks. See level_shifter_overlay.py's own module
docstring for the full reasoning, including the one real, flagged
deviation from Isolation's own model (color source: Level Shifter has
no Supply Set field, so its badge follows its own Power Domain's color
directly instead).

Covers: tool palette wiring, boundary-block validation (single- and
multi-hierarchy), create/edit/delete + cascade + undo, the multi-
hierarchy one-entity-several-icons model and its own group-highlight,
live colour-follow (from the owning Power Domain), cascade deletion via
Remove Power Domain, and right-click Edit/Info/Delete on the badge."""

import gc

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QDialog, QMessageBox

from frontend.src.power_intent_view.diagram_tool_palette import DiagramMode
from frontend.src.power_intent_view.level_shifter_dialog import LevelShifterDialog, LevelShifterInfoDialog
from frontend.src.power_intent_view.level_shifter_overlay import LevelShifterBadgeIconItem, LevelShifterStrategyPlacement
from frontend.src.power_intent_view.power_domain_overlay import PowerDomainPlacement
from frontend.src.power_intent_view.power_intent_tab import PowerIntentTab
from frontend.src.shared.mock_backend import build_mock_design
from frontend.src.shared.widgets import message_box


def _deselect_and_collect(tab: PowerIntentTab) -> None:
    if tab.diagram._view.scene() is not None:
        tab.diagram._view.scene().clearSelection()
    gc.collect()


def _tab_with_single_hierarchy_domain(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top"]
    domain = PowerDomainPlacement(id="d1", owner_path="top", name="PD_TOP", color=QColor("#ff0000"))
    view._domain_state.domains["d1"] = domain
    return tab, view, box, domain


def _tab_with_multi_hierarchy_domain(qtbot):
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


def test_level_shifter_mode_sets_the_level_shifter_cursor(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view

    view.set_mode(DiagramMode.LEVEL_SHIFTER)

    from frontend.src.power_intent_view.diagram_tool_icons import level_shifter_cursor

    assert view.viewport().cursor().pixmap().toImage() == level_shifter_cursor().pixmap().toImage()


# --- boundary-block validation (same rule as Isolation) -----------------


def test_boundary_block_of_a_domain_is_a_valid_target(qtbot):
    tab, view, box, domain = _tab_with_single_hierarchy_domain(qtbot)
    assert view._validate_level_shifter_target(box) is None
    _deselect_and_collect(tab)


def test_block_with_no_power_domain_is_rejected(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    assert view._validate_level_shifter_target(view._boxes["top"]) is not None


def test_a_block_merely_nested_inside_a_domain_not_its_own_boundary_is_rejected(qtbot):
    """Same rule as Isolation (find_domain_for_owner(), exact boundary
    ownership) -- NOT Retention/Power Switch's own cascading "anywhere
    inside" rule."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view._domain_state.domains["d1"] = PowerDomainPlacement(id="d1", owner_path="top", name="PD_TOP", color=QColor("#ff0000"))
    assert view._validate_level_shifter_target(view._boxes["top/u_core"]) is not None


def test_add_level_shifter_context_menu_action_is_gated_by_the_same_validation(qtbot):
    tab, view, box, domain = _tab_with_single_hierarchy_domain(qtbot)
    from PySide6.QtWidgets import QMenu

    menu = QMenu()
    actions = view._extra_context_menu_actions(menu, box)
    add_action = next(a for a, _cb in actions if a.text() == "Add Level Shifter")
    assert add_action.isEnabled() is True
    _deselect_and_collect(tab)


# --- create/edit/delete + undo -------------------------------------------


def test_create_level_shifter_materializes_a_badge_on_its_boundary_block(qtbot):
    tab, view, box, domain = _tab_with_single_hierarchy_domain(qtbot)

    placement = view.create_level_shifter(box, "LS1", domain.id, "inputs", "low_to_high", "self", None, None, ["top/clk"])

    assert placement.name == "LS1"
    icons = view._level_shifter_icons[placement.id]
    assert len(icons) == 1
    assert isinstance(icons[0], LevelShifterBadgeIconItem)
    assert icons[0].owner_box is box
    _deselect_and_collect(tab)


def test_create_level_shifter_rejects_a_duplicate_name(qtbot):
    tab, view, box, domain = _tab_with_single_hierarchy_domain(qtbot)
    view.create_level_shifter(box, "LS1", domain.id, "inputs", "low_to_high", "self", None, None, ["top/clk"])

    try:
        view.create_level_shifter(box, "LS1", domain.id, "outputs", "high_to_low", "self", None, None, ["top/clk"])
        assert False, "expected ValueError"
    except ValueError:
        pass
    _deselect_and_collect(tab)


def test_edit_level_shifter_updates_the_placement_and_rebuilds_badges(qtbot):
    tab, view, box, domain = _tab_with_single_hierarchy_domain(qtbot)
    placement = view.create_level_shifter(box, "LS1", domain.id, "inputs", "low_to_high", "self", None, None, ["top/clk"])

    view.edit_level_shifter(placement.id, "LS1_RENAMED", domain.id, "outputs", "high_to_low", "parent", 0.3, None, ["top/clk"])

    updated = view._level_shifter_state.strategies[placement.id]
    assert updated.name == "LS1_RENAMED"
    assert updated.applies_to == "outputs"
    assert updated.rule == "high_to_low"
    assert updated.location == "parent"
    assert updated.threshold == 0.3
    _deselect_and_collect(tab)


def test_delete_level_shifter_removes_the_strategy_and_every_badge(qtbot):
    tab, view, box, domain = _tab_with_single_hierarchy_domain(qtbot)
    placement = view.create_level_shifter(box, "LS1", domain.id, "inputs", "low_to_high", "self", None, None, ["top/clk"])

    view.delete_level_shifter(placement.id)

    assert placement.id not in view._level_shifter_state.strategies
    assert view._level_shifter_icons.get(placement.id, []) == []
    _deselect_and_collect(tab)


def test_create_level_shifter_undo_redo(qtbot):
    tab, view, box, domain = _tab_with_single_hierarchy_domain(qtbot)

    placement = view.create_level_shifter(box, "LS1", domain.id, "inputs", "low_to_high", "self", None, None, ["top/clk"])
    assert placement.id in view._level_shifter_state.strategies

    view._undo_stack.undo()
    assert placement.id not in tab.diagram._view._level_shifter_state.strategies

    view._undo_stack.redo()
    assert placement.id in tab.diagram._view._level_shifter_state.strategies
    _deselect_and_collect(tab)


# --- multi-hierarchy: one entity, several attachment-point icons --------


def test_multi_hierarchy_domain_gets_a_badge_on_every_boundary_block_referenced_by_elements(qtbot):
    tab, view, domain = _tab_with_multi_hierarchy_domain(qtbot)

    placement = view.create_level_shifter(
        view._boxes["top/u_core"], "LS1", domain.id, "elements", "both", "self", None, None, ["top/u_core/clk", "top/u_dbg/clk"]
    )

    icons = view._level_shifter_icons[placement.id]
    assert {i.owner_box.path for i in icons} == {"top/u_core", "top/u_dbg"}
    _deselect_and_collect(tab)


def test_selecting_one_icon_group_highlights_every_sibling_of_the_same_strategy(qtbot):
    tab, view, domain = _tab_with_multi_hierarchy_domain(qtbot)
    placement = view.create_level_shifter(
        view._boxes["top/u_core"], "LS1", domain.id, "elements", "both", "self", None, None, ["top/u_core/clk", "top/u_dbg/clk"]
    )
    icons = view._level_shifter_icons[placement.id]

    view.apply_construct_selection("LevelShifterStrategy", placement.id)

    assert set(view._click_highlighted_icons) == set(icons)
    assert len(view._click_highlighted_icons) == 2
    _deselect_and_collect(tab)


def test_left_clicking_a_level_shifter_icon_highlights_it_yellow_fluo(qtbot):
    """Bug fix (explicit request): Level Shifter was the one diagram
    object type never wired into _on_scene_selection_changed()'s own
    dispatch at all -- unlike test_selecting_one_icon_group_highlights_every_sibling_of_the_same_strategy
    above (which drives the SEPARATE Properties-table/apply_construct_selection()
    path, already correct), this drives a REAL diagram click via Qt's
    own scene selection (icon.setSelected(True) ->
    _on_scene_selection_changed()), the path that was actually broken."""
    tab, view, box, domain = _tab_with_single_hierarchy_domain(qtbot)
    placement = view.create_level_shifter(box, "LS1", domain.id, "elements", "low_to_high", "self", None, None, ["top/clk"])
    icon = view._level_shifter_icons[placement.id][0]

    icon.setSelected(True)
    view._on_scene_selection_changed()

    assert icon._click_highlighted is True
    _deselect_and_collect(tab)


def test_clicking_one_icon_of_a_multi_attachment_level_shifter_highlights_every_sibling(qtbot):
    """Same real-diagram-click group-highlight guarantee Isolation's own
    test_clicking_one_icon_of_a_multi_attachment_isolation_highlights_every_sibling
    already establishes, now for Level Shifter."""
    tab, view, domain = _tab_with_multi_hierarchy_domain(qtbot)
    box_core = view._boxes["top/u_core"]
    placement = view.create_level_shifter(
        box_core, "LS_MULTI", domain.id, "elements", "both", "self", None, None, ["top/u_core/clk", "top/u_dbg/clk"]
    )
    icons = view._level_shifter_icons[placement.id]

    icons[0].setSelected(True)
    view._on_scene_selection_changed()

    assert all(icon._click_highlighted for icon in icons)
    assert len(icons) == 2
    _deselect_and_collect(tab)


# --- color follows the owning Power Domain (flagged deviation) ----------


def test_level_shifter_badge_color_matches_its_power_domains_color(qtbot):
    tab, view, box, domain = _tab_with_single_hierarchy_domain(qtbot)
    placement = view.create_level_shifter(box, "LS1", domain.id, "inputs", "low_to_high", "self", None, None, ["top/clk"])

    icon = view._level_shifter_icons[placement.id][0]
    assert icon.color == QColor("#ff0000")
    _deselect_and_collect(tab)


def test_level_shifter_badge_color_updates_live_when_the_domain_is_edited(qtbot):
    tab, view, box, domain = _tab_with_single_hierarchy_domain(qtbot)
    placement = view.create_level_shifter(box, "LS1", domain.id, "inputs", "low_to_high", "self", None, None, ["top/clk"])

    view.edit_power_domain(domain.id, domain.name, QColor("#00ff00"), 0.5, None, [], {})

    icon = view._level_shifter_icons[placement.id][0]
    assert icon.color == QColor("#00ff00")  # live -- no manual refresh needed
    _deselect_and_collect(tab)


# --- cascade deletion via Remove Power Domain -----------------------------


def test_removing_the_power_domain_cascades_away_its_level_shifters(qtbot):
    tab, view, box, domain = _tab_with_single_hierarchy_domain(qtbot)
    placement = view.create_level_shifter(box, "LS1", domain.id, "inputs", "low_to_high", "self", None, None, ["top/clk"])

    affected = view._cascade_preview_for_domain_removal(domain)
    assert [s.name for s in affected[4]] == ["LS1"]

    view.remove_power_domain(box)

    assert view._level_shifter_state.strategies == {}
    _deselect_and_collect(tab)


# --- right-click Edit/Info/Delete on the badge ---------------------------


def test_badge_context_menu_offers_edit_info_delete(qtbot):
    tab, view, box, domain = _tab_with_single_hierarchy_domain(qtbot)
    placement = view.create_level_shifter(box, "LS1", domain.id, "inputs", "low_to_high", "self", None, None, ["top/clk"])
    icon = view._level_shifter_icons[placement.id][0]

    from PySide6.QtWidgets import QMenu

    menu = QMenu()
    actions = view._level_shifter_context_menu_actions(menu, icon)

    assert [a.text() for a, _cb in actions] == ["Edit", "Info", "Delete"]
    _deselect_and_collect(tab)


def test_delete_action_prompts_confirmation_default_no(qtbot, monkeypatch):
    tab, view, box, domain = _tab_with_single_hierarchy_domain(qtbot)
    placement = view.create_level_shifter(box, "LS1", domain.id, "inputs", "low_to_high", "self", None, None, ["top/clk"])
    monkeypatch.setattr(message_box, "question", lambda *a, **k: QMessageBox.StandardButton.No)

    view._prompt_delete_level_shifter(placement.id)

    assert placement.id in view._level_shifter_state.strategies  # declined -- nothing removed
    _deselect_and_collect(tab)


def test_info_dialog_shows_the_strategys_own_current_values(qtbot):
    tab, view, box, domain = _tab_with_single_hierarchy_domain(qtbot)
    placement = view.create_level_shifter(box, "LS1", domain.id, "inputs", "low_to_high", "self", 0.4, None, ["top/clk"])
    icon = view._level_shifter_icons[placement.id][0]

    dialog = LevelShifterInfoDialog(placement, domain.name, "?", parent=None)
    qtbot.addWidget(dialog)

    assert dialog.windowTitle() == "Level Shifter: LS1"
    _deselect_and_collect(tab)


# --- Elements required (cannot be empty) ---------------------------------


def test_create_level_shifter_raises_nothing_special_but_dialog_rejects_empty_elements_when_applies_to_all_is_elements(qtbot, monkeypatch):
    """The directly-testable create_level_shifter() core doesn't itself
    enforce "Elements required" (same split every other placement type
    here follows -- validation lives in the dialog's own accept(), see
    LevelShifterDialog.accept()). QMessageBox.warning() must be
    monkeypatched here -- a real one is a blocking modal that hangs a
    headless test run indefinitely (confirmed empirically). Rework
    (explicit follow-up request): Elements is only required when
    Applies To All is specifically "Elements" -- see the dedicated
    test_level_shifter_applies_to_all.py for the full conditional-
    requirement coverage; this one keeps the "Elements" case here since
    it was already the established location for this test."""
    import frontend.src.power_intent_view.level_shifter_dialog as mod

    monkeypatch.setattr(mod.message_box, "warning", lambda *a, **k: None)
    tab, view, box, domain = _tab_with_single_hierarchy_domain(qtbot)
    dialog = LevelShifterDialog(view._boxes, domain, view._resolve_connectivity, {})
    qtbot.addWidget(dialog)
    dialog.name_edit.setText("LS1")
    dialog.applies_to_group.selected = "elements"
    dialog.rule_group.selected = "both"

    dialog.accept()

    assert dialog.result() != QDialog.DialogCode.Accepted  # no elements checked
    _deselect_and_collect(tab)


# --- bug: a broad ("Applies To All" != "Elements") strategy rendered NO icon
#
# Root cause (confirmed empirically): the "Applies To All" rework
# (section 3) made Elements optional -- empty and irrelevant whenever a
# broad Inputs/Outputs/Both choice is active -- but this module's own
# attachment-block derivation was never updated to match: it still
# unconditionally called owner_paths_for_elements(strategy.elements)
# (copied from Isolation's own always-non-empty-elements assumption
# when this module was first built). For a broad strategy, `elements`
# is genuinely `[]`, so that call returned an EMPTY set and no badge
# ever materialized anywhere -- the strategy itself was created
# successfully (Properties table / Power View both reflected it), but
# `_level_shifter_icons[strategy_id]` was silently `[]`. The exact same
# "one side of a feature built, the other silently incomplete" pattern
# already seen once for Power Switch and once for Retention-in-
# generation. Fixed via attachment_owner_paths()/
# variants_for_strategy_on_block() (level_shifter_overlay.py) -- see
# their own docstrings.


def test_broad_applies_to_all_renders_a_real_visible_icon_on_the_boundary_block(qtbot):
    tab, view, box, domain = _tab_with_single_hierarchy_domain(qtbot)

    placement = view.create_level_shifter(box, "LS1", domain.id, "inputs", "low_to_high", "self", None, None, [])

    icons = view._level_shifter_icons[placement.id]
    assert len(icons) == 1, "a broad (non-Elements) strategy must still render a badge on its own boundary block"
    icon = icons[0]
    assert icon.owner_box is box
    assert icon.scene() is view.scene()
    assert icon.isVisible() is True
    assert icon.boundingRect().width() > 0 and icon.boundingRect().height() > 0
    assert icon.zValue() > box.zValue()  # renders above its own owning block, not occluded by it
    _deselect_and_collect(tab)


def test_broad_outputs_renders_the_flipped_variant_only(qtbot):
    tab, view, box, domain = _tab_with_single_hierarchy_domain(qtbot)

    placement = view.create_level_shifter(box, "LS1", domain.id, "outputs", "high_to_low", "self", None, None, [])

    icons = view._level_shifter_icons[placement.id]
    assert len(icons) == 1
    assert icons[0].flipped is True


def test_broad_both_renders_both_variants(qtbot):
    tab, view, box, domain = _tab_with_single_hierarchy_domain(qtbot)

    placement = view.create_level_shifter(box, "LS1", domain.id, "both", "both", "self", None, None, [])

    icons = view._level_shifter_icons[placement.id]
    assert len(icons) == 2
    assert {i.flipped for i in icons} == {False, True}


def test_broad_applies_to_all_on_a_multi_hierarchy_domain_renders_on_every_boundary_block(qtbot):
    tab, view, domain = _tab_with_multi_hierarchy_domain(qtbot)

    placement = view.create_level_shifter(view._boxes["top/u_core"], "LS1", domain.id, "inputs", "low_to_high", "self", None, None, [])

    icons = view._level_shifter_icons[placement.id]
    assert {i.owner_box.path for i in icons} == {"top/u_core", "top/u_dbg"}
    for icon in icons:
        assert icon.scene() is view.scene()
        assert icon.isVisible() is True


def test_broad_applies_to_all_survives_a_full_rebuild(qtbot):
    """The inline (_materialize_level_shifter_badges) and wholesale
    (build_level_shifter_overlay) code paths must agree -- same "two
    independent code paths, both need updating" gap already flagged
    elsewhere in this codebase's own history."""
    tab, view, box, domain = _tab_with_single_hierarchy_domain(qtbot)
    placement = view.create_level_shifter(box, "LS1", domain.id, "both", "both", "self", None, None, [])
    assert len(view._level_shifter_icons[placement.id]) == 2

    view.refresh()  # forces a full _rebuild(), not the inline materializer

    icons_after = view._level_shifter_icons[placement.id]
    assert len(icons_after) == 2
    assert all(icon.owner_box.path == "top" and icon.scene() is view.scene() and icon.isVisible() for icon in icons_after)


def test_dialog_driven_add_level_shifter_with_a_broad_choice_produces_a_visible_icon(qtbot, monkeypatch):
    """End-to-end regression, closest to the real repro: goes through
    _prompt_add_level_shifter() (the SAME method the tool palette/
    right-click menu action actually calls), not just the bypass-the-
    dialog create_level_shifter() core."""
    import frontend.src.power_intent_view.level_shifter_dialog as mod
    from PySide6.QtWidgets import QDialog

    tab, view, box, domain = _tab_with_single_hierarchy_domain(qtbot)

    original_init = mod.LevelShifterDialog.__init__

    def patched_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        self.name_edit.setText("LS1")
        self.applies_to_group.selected = "inputs"
        self.rule_group.selected = "both"

    monkeypatch.setattr(mod.LevelShifterDialog, "__init__", patched_init)
    monkeypatch.setattr(mod.LevelShifterDialog, "exec", lambda self: QDialog.DialogCode.Accepted)

    view._prompt_add_level_shifter(box)

    assert len(view._level_shifter_state.strategies) == 1
    strategy_id = next(iter(view._level_shifter_state.strategies))
    icons = view._level_shifter_icons[strategy_id]
    assert len(icons) == 1
    assert icons[0].scene() is view.scene()
    assert icons[0].isVisible() is True
    _deselect_and_collect(tab)
