"""Bug 3 (explicit request, supersedes the earlier "append downward from
a fixed start" row-stacking spec): Isolation/Level Shifter badges on the
SAME block, in the SAME inside/outside sequence, are centered as ONE
GROUP around the block's own vertical midpoint -- 1 badge lands exactly
at the midpoint, 2 land symmetric above/below it, 3 land one-at-the-
midpoint plus one-above plus one-below, and this generalizes to N,
RE-CENTERING THE WHOLE GROUP whenever a badge is added or removed (not
appended downward from wherever the group used to start). See
isolation_overlay._centered_y()'s own docstring for the exact formula,
and power_intent_diagram_view.PowerIntentDiagramView._apply_group_centering()
for how it's kept live across every add/edit/delete path.

Also covers the overlap with Bug 2 (Ground Supply Port/Internal Supply
rendering at the bottom edge instead of the top) on the SAME fixture,
per explicit request that the two be tested together where they
interact -- both systems key off the SAME block but different edges
(Isolation/Level Shifter always anchor left; a Ground supply anchors
bottom), so the real thing to confirm is that they coexist without
interfering, not that they collide.
"""

import gc

from PySide6.QtGui import QColor

from frontend.src.power_intent_view.isolation_overlay import _centered_y
from frontend.src.power_intent_view.power_domain_overlay import PowerDomainPlacement, SupplySetPlacement
from frontend.src.power_intent_view.power_intent_tab import PowerIntentTab
from frontend.src.shared.mock_backend import build_mock_design


def _deselect_and_collect(tab: PowerIntentTab) -> None:
    if tab.diagram._view.scene() is not None:
        tab.diagram._view.scene().clearSelection()
    gc.collect()


def _tab_with_domain(qtbot):
    """Same shape as test_isolation_cell.py's own fixture, but with NO
    top-edge Supply Port -- extra_top_clearance stays 0, so the centered
    Y values below can be checked against a clean, easy-to-reason-about
    band (the block's own full height) rather than also needing to
    account for a label-collision clearance."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top"]
    domain = PowerDomainPlacement(id="d1", owner_path="top", name="PD_TOP", color=QColor("#ff0000"))
    view._domain_state.domains["d1"] = domain
    supply_set = SupplySetPlacement(id="ss1", name="SS1", power="VDD1", ground="VSS", owner_paths=["top"])
    view._domain_state.supply_sets["ss1"] = supply_set
    return tab, view, box, domain, supply_set


def _iso_y(view, placement):
    return view._isolation_icons[placement.id][0].pos().y()


def test_a_single_isolation_badge_lands_exactly_at_the_blocks_vertical_midpoint(qtbot):
    tab, view, box, domain, supply_set = _tab_with_domain(qtbot)

    iso = view.create_isolation(box, "ISO1", "d1", "ss1", None, None, "low", "self", "ctrl", "high", ["top/clk"])

    expected = _centered_y(box.rect(), 0.0, 0, 1)
    assert _iso_y(view, iso) == expected
    assert expected == box.rect().top() + box.rect().height() / 2  # the TRUE geometric midpoint, no clearance in play
    _deselect_and_collect(tab)


def test_two_isolation_badges_land_symmetric_above_and_below_the_midpoint(qtbot):
    tab, view, box, domain, supply_set = _tab_with_domain(qtbot)

    iso_a = view.create_isolation(box, "ISO_A", "d1", "ss1", None, None, "low", "self", "ctrl", "high", ["top/clk"])
    iso_b = view.create_isolation(box, "ISO_B", "d1", "ss1", None, None, "low", "self", "ctrl", "high", ["top/sleep_n"])

    midpoint = box.rect().top() + box.rect().height() / 2
    y_a, y_b = _iso_y(view, iso_a), _iso_y(view, iso_b)
    assert y_a < midpoint < y_b  # one strictly above, one strictly below
    assert (midpoint - y_a) == (y_b - midpoint)  # symmetric distances
    assert y_a == _centered_y(box.rect(), 0.0, 0, 2)
    assert y_b == _centered_y(box.rect(), 0.0, 1, 2)
    _deselect_and_collect(tab)


def test_three_isolation_badges_one_at_midpoint_one_above_one_below(qtbot):
    tab, view, box, domain, supply_set = _tab_with_domain(qtbot)

    iso_a = view.create_isolation(box, "ISO_A", "d1", "ss1", None, None, "low", "self", "ctrl", "high", ["top/clk"])
    iso_b = view.create_isolation(box, "ISO_B", "d1", "ss1", None, None, "low", "self", "ctrl", "high", ["top/sleep_n"])
    iso_c = view.create_isolation(box, "ISO_C", "d1", "ss1", None, None, "low", "self", "ctrl", "high", ["top/out1"])

    midpoint = box.rect().top() + box.rect().height() / 2
    y_a, y_b, y_c = _iso_y(view, iso_a), _iso_y(view, iso_b), _iso_y(view, iso_c)
    assert y_b == midpoint  # the SECOND-created badge (offset_index 1, the middle slot) sits exactly at the midpoint
    assert y_a < midpoint < y_c
    assert (midpoint - y_a) == (y_c - midpoint)
    _deselect_and_collect(tab)


def test_adding_a_fourth_badge_recenters_the_whole_existing_group_of_three(qtbot):
    """The defining behavior Bug 3 exists for: adding a 4th badge doesn't
    just APPEND below the 3rd -- it re-centers ALL FOUR as one new
    group, shifting the first three's own Y too."""
    tab, view, box, domain, supply_set = _tab_with_domain(qtbot)

    iso_a = view.create_isolation(box, "ISO_A", "d1", "ss1", None, None, "low", "self", "ctrl", "high", ["top/clk"])
    iso_b = view.create_isolation(box, "ISO_B", "d1", "ss1", None, None, "low", "self", "ctrl", "high", ["top/sleep_n"])
    iso_c = view.create_isolation(box, "ISO_C", "d1", "ss1", None, None, "low", "self", "ctrl", "high", ["top/out1"])
    y_a_before, y_b_before, y_c_before = _iso_y(view, iso_a), _iso_y(view, iso_b), _iso_y(view, iso_c)

    iso_d = view.create_isolation(box, "ISO_D", "d1", "ss1", None, None, "low", "self", "ctrl", "high", ["top/out2"])

    y_a_after, y_b_after, y_c_after, y_d_after = (_iso_y(view, p) for p in (iso_a, iso_b, iso_c, iso_d))
    # The first three all moved -- re-centered as part of a group of 4, not left where a group of 3 put them.
    assert y_a_after != y_a_before
    assert y_b_after != y_b_before
    assert y_c_after != y_c_before
    # And the new group of 4 is itself correctly centered: two above the midpoint, two below, evenly spaced.
    midpoint = box.rect().top() + box.rect().height() / 2
    ys = sorted([y_a_after, y_b_after, y_c_after, y_d_after])
    assert ys[0] < ys[1] < midpoint < ys[2] < ys[3]
    row = ys[1] - ys[0]
    assert row > 0
    assert ys[2] - ys[1] == row
    assert ys[3] - ys[2] == row
    assert (midpoint - ys[1]) == (ys[2] - midpoint) == row / 2
    _deselect_and_collect(tab)


def test_removing_a_badge_recenters_the_remaining_group(qtbot):
    tab, view, box, domain, supply_set = _tab_with_domain(qtbot)
    iso_a = view.create_isolation(box, "ISO_A", "d1", "ss1", None, None, "low", "self", "ctrl", "high", ["top/clk"])
    iso_b = view.create_isolation(box, "ISO_B", "d1", "ss1", None, None, "low", "self", "ctrl", "high", ["top/sleep_n"])
    iso_c = view.create_isolation(box, "ISO_C", "d1", "ss1", None, None, "low", "self", "ctrl", "high", ["top/out1"])

    view.delete_isolation(iso_c.id)

    midpoint = box.rect().top() + box.rect().height() / 2
    y_a, y_b = _iso_y(view, iso_a), _iso_y(view, iso_b)
    assert y_a < midpoint < y_b
    assert (midpoint - y_a) == (y_b - midpoint)  # back to the symmetric 2-badge shape, not left as a stale 3-badge slot
    _deselect_and_collect(tab)


# --- overlap with Bug 2 (Ground supply bottom-placement) -----------------------


def test_ground_internal_supply_and_a_centered_isolation_group_coexist_on_the_same_block(qtbot):
    """Bug 2's Ground placement (bottom edge) and Bug 3's centered
    Isolation/Level Shifter group (left edge) key off the same block but
    different edges -- confirms they don't interfere with each other's
    positioning when both are exercised together on one fixture."""
    tab, view, box, domain, supply_set = _tab_with_domain(qtbot)
    ground = view.add_internal_supply(box, "VSS", QColor("#0000ff"))
    view._domain_state.supply_sets["ss1"] = SupplySetPlacement(id="ss1", name="SS1", power="VDD1", ground="VSS", owner_paths=["top"])
    view._reclassify_ground_supply_edges()
    assert ground.placement.edge == "bottom"  # Bug 2 took effect

    iso_a = view.create_isolation(box, "ISO_A", "d1", "ss1", None, None, "low", "self", "ctrl", "high", ["top/clk"])
    iso_b = view.create_isolation(box, "ISO_B", "d1", "ss1", None, None, "low", "self", "ctrl", "high", ["top/sleep_n"])

    # Bug 3's own centering is unaffected by the Ground supply's presence.
    midpoint = box.rect().top() + box.rect().height() / 2
    y_a, y_b = _iso_y(view, iso_a), _iso_y(view, iso_b)
    assert y_a < midpoint < y_b
    assert (midpoint - y_a) == (y_b - midpoint)
    # And the Ground supply is still at the bottom, unaffected by the isolation badges (different edge entirely).
    assert ground.placement.edge == "bottom"
    assert ground.pos().y() == box.rect().bottom()
    _deselect_and_collect(tab)
