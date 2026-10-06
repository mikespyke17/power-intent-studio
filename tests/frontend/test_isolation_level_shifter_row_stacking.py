"""PERMANENT regression suite: Isolation and Level Shifter icons on the
SAME boundary block share ONE unified row-stacking system (explicit
request, extension of the Isolation-only self/parent-alignment work --
see isolation_overlay.build_isolation_overlay()'s own docstring for the
same rule scoped to one type).

Convention, confirmed against this codebase's own already-established
behavior rather than assumed: "by type then creation order" within each
side (inside/self vs outside/parent-fanout) -- Isolation is always built
FIRST in every code path (see power_intent_diagram_view.py's own
_isolation_badge_slot_counts_per_box()/_rebuild(), and the pre-existing
`initial_block_counts` mechanism this generalizes), so a Level Shifter
badge on either side continues Isolation's own sequence for that side,
never landing on an identical slot to a same-side Isolation badge unless
it's a genuine cross-type self+parent pair sharing that slot on purpose.

This suite also locks in the fix for the earlier-reported "Isolation
icon collides with Level Shifter icon" bug as a permanent regression
test, now exercised through the unified system rather than the older,
narrower `initial_block_counts` mechanism it replaced.
"""

import gc

from PySide6.QtGui import QColor

from frontend.src.power_intent_view.power_domain_overlay import PowerDomainPlacement, SupplySetPlacement
from frontend.src.power_intent_view.power_intent_tab import PowerIntentTab
from frontend.src.power_intent_view.supply_port_overlay import INTERIOR_TOP_MARGIN_FOR_SUPPLY_PORT
from frontend.src.shared.mock_backend import build_mock_design


def _deselect_and_collect(tab: PowerIntentTab) -> None:
    if tab.diagram._view.scene() is not None:
        tab.diagram._view.scene().clearSelection()
    gc.collect()


def _tab_with_single_hierarchy_domain(qtbot):
    """Same fixture shape as test_isolation_cell.py's own -- "top" is
    the domain's only boundary block, with a real top-edge Supply Port
    ("VDD1") already placed on it, so every test here also exercises
    the label-collision clearance for free."""
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


def test_two_isolation_and_one_level_shifter_get_distinct_rows_in_one_unified_stack(qtbot):
    """Explicit regression request: 2 Isolation + 1 Level Shifter, same
    block, various Locations -- all 3 get distinct rows, no overlaps."""
    tab, view, box, domain, port, supply_set = _tab_with_single_hierarchy_domain(qtbot)

    iso_self = view.create_isolation(box, "ISO_SELF", "d1", "ss1", None, None, "low", "self", "ctrl", "high", ["top/clk"])
    iso_parent = view.create_isolation(box, "ISO_PARENT", "d1", "ss1", None, None, "low", "parent", "ctrl", "high", ["top/sleep_n"])
    ls_self = view.create_level_shifter(box, "LS_SELF", "d1", "elements", "low_to_high", "self", None, None, ["top/out1"])

    positions = [
        view._isolation_icons[iso_self.id][0].pos(),
        view._isolation_icons[iso_parent.id][0].pos(),
        view._level_shifter_icons[ls_self.id][0].pos(),
    ]
    assert len(positions) == len(set((p.x(), p.y()) for p in positions))  # no two icons share the exact same spot
    _deselect_and_collect(tab)


def test_isolation_and_level_shifter_no_longer_collide_at_the_same_position(qtbot):
    """The earlier-reported bug this unification resolves as a side
    effect: an Isolation and a Level Shifter, same Location (so same
    inside/outside category), on the same block, must NOT render at
    the identical position (each independently starting its own count
    from 0 was the original bug -- see level_shifter_overlay.py's own
    build_level_shifter_overlay() docstring)."""
    tab, view, box, domain, port, supply_set = _tab_with_single_hierarchy_domain(qtbot)

    iso = view.create_isolation(box, "ISO1", "d1", "ss1", None, None, "low", "parent", "ctrl", "high", ["top/clk"])
    ls = view.create_level_shifter(box, "LS1", "d1", "elements", "low_to_high", "parent", None, None, ["top/sleep_n"])

    iso_icon = view._isolation_icons[iso.id][0]
    ls_icon = view._level_shifter_icons[ls.id][0]
    assert iso_icon.pos() != ls_icon.pos()
    assert iso_icon.pos().x() == ls_icon.pos().x()  # same category (outside) -- same X, stacked vertically
    assert iso_icon.pos().y() != ls_icon.pos().y()
    _deselect_and_collect(tab)


def test_isolation_self_and_level_shifter_parent_align_across_types(qtbot):
    """The alignment itself now spans types, not just same-type pairs:
    an Isolation (self) and a Level Shifter (parent) on the same block
    share the same row -- including the top-edge label clearance,
    which must apply to BOTH (see class docstrings' own "applied to
    EVERY badge on a qualifying block" note).

    Bug 3 (explicit request) supersedes the exact Y this pair lands at
    -- see test_isolation_cell.py's own
    test_self_and_parent_isolation_pair_align_on_the_same_horizontal_line
    for the identical supersession applied to a same-type pair; the
    cross-type SAME-Y alignment this test is actually about is
    completely unchanged and still asserted directly."""
    tab, view, box, domain, port, supply_set = _tab_with_single_hierarchy_domain(qtbot)

    iso = view.create_isolation(box, "ISO1", "d1", "ss1", None, None, "low", "self", "ctrl", "high", ["top/clk"])
    ls = view.create_level_shifter(box, "LS1", "d1", "elements", "low_to_high", "parent", None, None, ["top/sleep_n"])

    iso_icon = view._isolation_icons[iso.id][0]
    ls_icon = view._level_shifter_icons[ls.id][0]
    assert iso_icon.pos().y() == ls_icon.pos().y()
    assert iso_icon.pos().x() > box.rect().left()  # self -- inside
    assert ls_icon.pos().x() < box.rect().left()  # parent -- outside

    from frontend.src.power_intent_view.isolation_overlay import _centered_y

    expected_y = _centered_y(box.rect(), INTERIOR_TOP_MARGIN_FOR_SUPPLY_PORT, 0, 1)
    assert iso_icon.pos().y() == expected_y  # both cleared VDD1's own label together, centered
    _deselect_and_collect(tab)


def test_a_third_badge_of_either_type_stacks_below_a_cross_type_pair_without_overlap(qtbot):
    """Requirement: ANY count/mix of Isolation + Level Shifter, ANY
    combination of self/parent/fanout. A second self-side badge (here,
    a Level Shifter) stacks below the aligned Isolation+LevelShifter
    pair rather than colliding with it."""
    tab, view, box, domain, port, supply_set = _tab_with_single_hierarchy_domain(qtbot)

    iso_self = view.create_isolation(box, "ISO_SELF", "d1", "ss1", None, None, "low", "self", "ctrl", "high", ["top/clk"])
    ls_parent = view.create_level_shifter(box, "LS_PARENT", "d1", "elements", "low_to_high", "parent", None, None, ["top/sleep_n"])
    ls_self_2 = view.create_level_shifter(box, "LS_SELF_2", "d1", "elements", "low_to_high", "self", None, None, ["top/out1"])

    iso_icon = view._isolation_icons[iso_self.id][0]
    ls_parent_icon = view._level_shifter_icons[ls_parent.id][0]
    ls_self_2_icon = view._level_shifter_icons[ls_self_2.id][0]
    assert iso_icon.pos().y() == ls_parent_icon.pos().y()  # the cross-type pair is aligned
    assert ls_self_2_icon.pos().y() > iso_icon.pos().y()  # the extra self-side badge stacks below it
    assert ls_self_2_icon.pos() != ls_parent_icon.pos()
    _deselect_and_collect(tab)


def test_direct_creation_matches_a_full_rebuild_for_a_mixed_type_stack(qtbot):
    """Same "undo+redo fixes it, direct creation doesn't" guard already
    applied to Isolation alone, now for a mixed Isolation+Level Shifter
    stack -- both code paths must agree."""
    tab, view, box, domain, port, supply_set = _tab_with_single_hierarchy_domain(qtbot)
    iso_self = view.create_isolation(box, "ISO_SELF", "d1", "ss1", None, None, "low", "self", "ctrl", "high", ["top/clk"])
    iso_parent = view.create_isolation(box, "ISO_PARENT", "d1", "ss1", None, None, "low", "parent", "ctrl", "high", ["top/sleep_n"])
    ls_self = view.create_level_shifter(box, "LS_SELF", "d1", "elements", "low_to_high", "self", None, None, ["top/out1"])

    def snapshot():
        return {
            "iso_self": view._isolation_icons[iso_self.id][0].pos(),
            "iso_parent": view._isolation_icons[iso_parent.id][0].pos(),
            "ls_self": view._level_shifter_icons[ls_self.id][0].pos(),
        }

    direct = snapshot()
    tab.undo_shortcut.activated.emit()
    tab.redo_shortcut.activated.emit()
    after_rebuild = snapshot()

    assert direct == after_rebuild
    _deselect_and_collect(tab)


def test_editing_a_level_shifters_location_recomputes_alignment_with_isolation(qtbot):
    """Explicit request: recomputes correctly if an icon's Location
    changes via Edit. Two self-placed Isolations occupy inside-slots 0
    and 1, and one parent-placed Isolation occupies outside-slot 0; a
    Level Shifter starting "self" lands alone at inside-slot 2 (no
    outside-slot-2 counterpart to align with yet). Editing its own
    Location to "parent" moves it to outside-slot 1 -- which now
    aligns it with the SECOND self-placed Isolation (inside-slot 1),
    not with wherever it used to sit."""
    tab, view, box, domain, port, supply_set = _tab_with_single_hierarchy_domain(qtbot)
    iso_self_1 = view.create_isolation(box, "ISO_SELF_1", "d1", "ss1", None, None, "low", "self", "ctrl", "high", ["top/clk"])
    iso_self_2 = view.create_isolation(box, "ISO_SELF_2", "d1", "ss1", None, None, "low", "self", "ctrl", "high", ["top/sleep_n"])
    iso_parent = view.create_isolation(box, "ISO_PARENT", "d1", "ss1", None, None, "low", "parent", "ctrl", "high", ["top/out1"])
    ls = view.create_level_shifter(box, "LS1", "d1", "elements", "low_to_high", "self", None, None, ["top/out2"])
    iso_self_2_icon = view._isolation_icons[iso_self_2.id][0]
    iso_parent_icon = view._isolation_icons[iso_parent.id][0]
    ls_icon_before = view._level_shifter_icons[ls.id][0]
    assert ls_icon_before.pos().y() not in (iso_self_2_icon.pos().y(), iso_parent_icon.pos().y())  # alone, unaligned

    view.edit_level_shifter(ls.id, "LS1", "d1", "elements", "low_to_high", "parent", None, None, ["top/out2"])

    ls_icon_after = view._level_shifter_icons[ls.id][0]
    assert ls_icon_after.pos().x() < box.rect().left()  # now outside, matching its new Location
    assert ls_icon_after.pos().y() == iso_self_2_icon.pos().y()  # re-aligned with ISO_SELF_2, not stuck at its old, stale position
    assert ls_icon_after.pos().y() != iso_parent_icon.pos().y()  # and NOT sharing ISO_PARENT's own slot (0), which is already taken
    _deselect_and_collect(tab)


def test_deleting_an_isolation_reflows_the_remaining_unified_stack(qtbot):
    """Explicit request: recomputes correctly if an Isolation/Level
    Shifter is added/removed later -- no stale gaps or overlaps left
    behind by a deletion."""
    tab, view, box, domain, port, supply_set = _tab_with_single_hierarchy_domain(qtbot)
    iso_1 = view.create_isolation(box, "ISO_1", "d1", "ss1", None, None, "low", "self", "ctrl", "high", ["top/clk"])
    iso_2 = view.create_isolation(box, "ISO_2", "d1", "ss1", None, None, "low", "self", "ctrl", "high", ["top/sleep_n"])
    ls = view.create_level_shifter(box, "LS1", "d1", "elements", "low_to_high", "self", None, None, ["top/out1"])
    ls_icon_before = view._level_shifter_icons[ls.id][0]
    y_before = ls_icon_before.pos().y()

    view.delete_isolation(iso_1.id)  # the FIRST self badge is gone -- ISO_2/LS1 should shift up, not leave a gap

    iso_2_icon_after = view._isolation_icons[iso_2.id][0]
    ls_icon_after = view._level_shifter_icons[ls.id][0]
    assert ls_icon_after.pos().y() < y_before  # reflowed up, no stale gap
    assert ls_icon_after.pos().y() != iso_2_icon_after.pos().y() or ls_icon_after.pos().x() != iso_2_icon_after.pos().x()  # still no overlap
    positions = [icon.pos() for icon in view._isolation_icons[iso_2.id]] + [icon.pos() for icon in view._level_shifter_icons[ls.id]]
    assert len(positions) == len(set((p.x(), p.y()) for p in positions))
    _deselect_and_collect(tab)
