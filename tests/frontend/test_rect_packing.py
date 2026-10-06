"""shared/diagram/rect_packing.py -- pure-Python MaxRects packing, no Qt
dependency at all (this file imports nothing from PySide6), so it's
testable and iterable on its own before hierarchy_scene_builder.py wires
it into the actual QGraphicsView layer."""

from __future__ import annotations

import random

from frontend.src.shared.diagram.rect_packing import Block, Rect, pack_rectangles


def _rects_overlap_within_padding(r1: Rect, r2: Rect, padding: float) -> bool:
    """True if r1/r2 come closer than `padding` on every axis -- i.e. a
    real violation, not just "touching at exactly padding."""
    separated = (
        r1.x + r1.w + padding <= r2.x + 1e-6
        or r2.x + r2.w + padding <= r1.x + 1e-6
        or r1.y + r1.h + padding <= r2.y + 1e-6
        or r2.y + r2.h + padding <= r1.y + 1e-6
    )
    return not separated


def _axis_gap(r1: Rect, r2: Rect) -> float | None:
    """Gap along whichever axis separates r1 and r2, but only when
    they're genuinely side-by-side (overlapping or touching along the
    *other* axis too) -- None for a diagonal (not directly adjacent)
    pair, which this doesn't attempt to characterize."""
    y_overlap = min(r1.y + r1.h, r2.y + r2.h) - max(r1.y, r2.y) > -1e-6
    x_overlap = min(r1.x + r1.w, r2.x + r2.w) - max(r1.x, r2.x) > -1e-6
    if y_overlap:
        gap = max(r1.x, r2.x) - min(r1.x + r1.w, r2.x + r2.w)
        if gap > -1e-6:
            return gap
    if x_overlap:
        gap = max(r1.y, r2.y) - min(r1.y + r1.h, r2.y + r2.h)
        if gap > -1e-6:
            return gap
    return None


def _bounding_box_area(rects: dict[str, Rect]) -> float:
    max_x = max(r.x + r.w for r in rects.values())
    max_y = max(r.y + r.h for r in rects.values())
    return max_x * max_y


def _wasted_area_fraction(blocks: list[Block], rects: dict[str, Rect]) -> float:
    block_area = sum(b.width * b.height for b in blocks)
    return 1 - (block_area / _bounding_box_area(rects))


def _random_blocks(n: int, seed: int, min_size: float = 20, max_size: float = 120) -> list[Block]:
    rng = random.Random(seed)
    return [Block(id=f"b{i}", width=rng.uniform(min_size, max_size), height=rng.uniform(min_size, max_size)) for i in range(n)]


# --- empty/trivial inputs -------------------------------------------------


def test_empty_input_returns_empty_dict():
    assert pack_rectangles([], padding=10) == {}


def test_single_block_is_placed_at_the_origin():
    result = pack_rectangles([Block("a", 50, 30)], padding=10)
    assert result == {"a": Rect(x=0, y=0, w=50, h=30)}


# --- requirement 1: no two placed rects overlap, including padding -------


def test_no_overlap_including_padding_for_a_small_fixed_case():
    blocks = [Block("a", 100, 60), Block("b", 40, 40), Block("c", 70, 20), Block("d", 30, 90)]
    padding = 12
    result = pack_rectangles(blocks, padding)

    ids = list(result)
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            assert not _rects_overlap_within_padding(result[ids[i]], result[ids[j]], padding), (ids[i], ids[j])


def test_no_overlap_including_padding_across_many_random_cases():
    padding = 8
    for seed in range(20):
        blocks = _random_blocks(n=12, seed=seed)
        result = pack_rectangles(blocks, padding)
        ids = list(result)
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                assert not _rects_overlap_within_padding(result[ids[i]], result[ids[j]], padding), (seed, ids[i], ids[j])


def test_every_placed_block_keeps_its_own_width_and_height():
    """The padding inflation used internally for placement must never
    leak into the returned rect's own size."""
    blocks = [Block("a", 55, 33), Block("b", 12, 88)]
    result = pack_rectangles(blocks, padding=15)
    assert (result["a"].w, result["a"].h) == (55, 33)
    assert (result["b"].w, result["b"].h) == (12, 88)


# --- requirement 2: exactly `padding` distance between neighbors ---------


def test_two_blocks_are_placed_exactly_padding_apart():
    padding = 10
    result = pack_rectangles([Block("a", 50, 50), Block("b", 50, 50)], padding)
    gap = _axis_gap(result["a"], result["b"])
    assert gap is not None
    assert abs(gap - padding) < 1e-6


def test_a_2x2_grid_of_equal_blocks_has_every_touching_pair_exactly_padding_apart():
    padding = 6
    blocks = [Block(f"b{i}", 40, 40) for i in range(4)]
    result = pack_rectangles(blocks, padding)

    ids = list(result)
    touching_gaps = []
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            gap = _axis_gap(result[ids[i]], result[ids[j]])
            if gap is not None:
                touching_gaps.append(gap)

    assert touching_gaps  # sanity: this layout must have *some* adjacent pairs
    for gap in touching_gaps:
        assert abs(gap - padding) < 1e-6


def test_the_tightest_gap_across_random_cases_is_exactly_padding_not_more():
    """No pair should ever be *closer* than padding (requirement 1,
    covered above) -- this confirms the packer also doesn't leave
    unnecessary extra slack: somewhere in a reasonably dense layout, two
    blocks must end up touching at exactly padding, not padding+something."""
    padding = 5
    for seed in range(10):
        blocks = _random_blocks(n=15, seed=seed, min_size=15, max_size=40)
        result = pack_rectangles(blocks, padding)
        ids = list(result)
        gaps = [g for i in range(len(ids)) for j in range(i + 1, len(ids)) if (g := _axis_gap(result[ids[i]], result[ids[j]])) is not None]
        assert gaps, seed
        assert abs(min(gaps) - padding) < 1e-6, (seed, min(gaps))


# --- requirement 3: wasted-area percentage --------------------------------


def test_wasted_area_percentage_is_reported_and_reasonably_small():
    """Not a claim of provable optimality (bin packing is NP-hard --
    see rect_packing.py's own docstring) -- a regression guard that the
    heuristic stays in a sane ballpark for a representative input, and a
    concrete demonstration of how to compute the metric the task asked
    to have reported."""
    blocks = _random_blocks(n=25, seed=42, min_size=20, max_size=100)
    result = pack_rectangles(blocks, padding=10)

    wasted_fraction = _wasted_area_fraction(blocks, result)
    print(f"wasted area: {wasted_fraction:.1%}")  # -- shown with pytest -s

    assert 0 <= wasted_fraction < 0.55


def test_wasted_area_percentage_for_uniform_blocks_that_tile_perfectly_is_small():
    """A grid of identical blocks (padding=0) should tile with near-zero
    waste -- confirms the metric itself, computed against a case whose
    ideal packing is obviously a perfect grid."""
    blocks = [Block(f"b{i}", 50, 50) for i in range(16)]
    result = pack_rectangles(blocks, padding=0)

    wasted_fraction = _wasted_area_fraction(blocks, result)
    assert wasted_fraction < 0.05
