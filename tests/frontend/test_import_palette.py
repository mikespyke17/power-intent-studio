"""Colour assignment for imported power intent (spec section 6).

The hard constraint is that every Power Domain in an imported design
must be visually distinguishable from every other. Because a domain's
colour is DERIVED from its Primary Supply Set's power rail rather than
stored on the domain, distinctness has to be engineered on the supply
colours -- which is what these tests check.

Section 6 also asks specifically that the existing fixed palette be
EXTENDED procedurally rather than repeated once exhausted. The
read-only Milestone-2 overlay wraps with `i % len(palette)`, so a
seventh domain there gets the first domain's colour back; the test
below pins that this import does not.
"""

import pytest

from frontend.src.power_intent_view.diagram_overlay import _DOMAIN_PALETTE
from frontend.src.power_intent_view.import_palette import (
    FIXED_PALETTE,
    GROUND_COLOR,
    are_visually_distinct,
    assign_supply_colors,
    color_for_index,
)


def test_the_first_six_reuse_the_existing_fixed_palette():
    """Section 6 says check for an existing palette first. A design with
    a handful of domains should get the same familiar colours it always
    had, not a new scheme."""
    for index, existing in enumerate(_DOMAIN_PALETTE):
        assigned = color_for_index(index)
        assert (assigned.red(), assigned.green(), assigned.blue()) == (existing.red(), existing.green(), existing.blue())


def test_the_fixed_palette_is_carried_at_full_opacity():
    """The overlay's own alpha (60) is ITS translucency; the domain fill
    applies transparency again, so carrying it here would double it."""
    assert all(c.alpha() == 255 for c in FIXED_PALETTE)


@pytest.mark.parametrize("count", [2, 6, 7, 8, 12, 20, 32])
def test_every_pair_is_visually_distinct_well_past_the_fixed_palette(count):
    """The section-6 guarantee. 7 and 8 matter most: they are the first
    counts the old `i % len(palette)` wrap would have collided at."""
    colors = [color_for_index(i) for i in range(count)]
    collisions = [
        (i, j) for i in range(count) for j in range(i + 1, count) if not are_visually_distinct(colors[i], colors[j])
    ]
    assert collisions == [], f"{count} domains produced colliding colours at indices {collisions}"


def test_a_seventh_domain_does_not_reuse_the_first_colour():
    """Explicitly the behaviour section 6 calls out as wrong -- the
    read-only overlay wraps here, this must not."""
    assert color_for_index(6) != color_for_index(0)
    assert are_visually_distinct(color_for_index(6), color_for_index(0))


def test_assignment_is_deterministic_across_calls():
    """Re-importing the same file twice must produce the same diagram."""
    first = [color_for_index(i).name() for i in range(12)]
    second = [color_for_index(i).name() for i in range(12)]
    assert first == second


def test_power_supplies_get_distinct_colours_in_priority_order():
    colors = assign_supply_colors(["VDD_A", "VDD_B", "VDD_C"])
    assert len(colors) == 3
    assert are_visually_distinct(colors["VDD_A"], colors["VDD_B"])
    assert are_visually_distinct(colors["VDD_B"], colors["VDD_C"])
    # Priority order is honoured, so the domains needing separation most
    # get the earliest hues.
    assert colors["VDD_A"] == color_for_index(0)
    assert colors["VDD_B"] == color_for_index(1)


def test_ground_rails_all_share_one_dark_colour():
    """Grounds are not part of the distinctness requirement -- domains
    are told apart by their power rail -- and a shared dark ground is
    what a user would pick by hand."""
    colors = assign_supply_colors(["VDD"], ground_supply_names=["VSS", "VSS_AON"])
    assert colors["VSS"] == GROUND_COLOR
    assert colors["VSS_AON"] == GROUND_COLOR
    assert colors["VDD"] != GROUND_COLOR


def test_a_name_used_as_both_power_and_ground_keeps_its_power_colour():
    """Legal if unusual. The power colour wins because that is the one a
    domain derives its own colour from."""
    colors = assign_supply_colors(["SHARED"], ground_supply_names=["SHARED"])
    assert colors["SHARED"] == color_for_index(0)


def test_empty_names_are_ignored_rather_than_assigned():
    colors = assign_supply_colors(["VDD", "", None], ground_supply_names=["", None])
    assert list(colors) == ["VDD"]
