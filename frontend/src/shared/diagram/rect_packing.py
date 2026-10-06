"""Pure-Python 2D rectangle packing -- no Qt dependency, so it's usable
and testable (tests/frontend/test_rect_packing.py) independent of any
GUI toolkit, before hierarchy_scene_builder.py wires it into the actual
QGraphicsView layer.

Algorithm: MaxRects, best-area-fit variant (Jylanki, "A Thousand Ways to
Pack the Bin"). Maintains a list of maximal free rectangles covering the
bin's remaining free space (rectangles that may overlap each other --
"maximal," not a disjoint partition, is what distinguishes MaxRects from
a guillotine-cut packer and is what lets a later, smaller item reuse
leftover space a guillotine cut would have discarded). Each placement:

  1. Score every free rectangle the item fits in by leftover area after
     placing it there (ties broken by shortest leftover side); place at
     the best-scoring one's top-left corner. No rotation -- a block's
     width/height are meaningful, fixed dimensions here, not
     interchangeable.
  2. Every free rectangle that intersects the newly placed item is
     removed and replaced by up to four new free rectangles covering
     what's left of it (the strips to its left/right/top/bottom of the
     placed item, each still spanning the *original* free rectangle's
     full height/width respectively) -- these deliberately can overlap
     other free rectangles now.
  3. Prune: drop any free rectangle fully contained within another --
     pure redundancy once the maximal set has grown.

No fixed bin size is given up front (the goal is minimizing the overall
bounding box, not fitting a predetermined bin) -- pack_rectangles()
wraps the fixed-bin placement above in a grow-and-retry loop: start from
a size estimate, and if any item fails to fit, restart placement from
scratch in a larger bin (free-rectangle state depends on bin size, so a
failed attempt can't be resumed, only restarted). Bin packing into a
minimal bounding box is NP-hard in general; this is a well-established
heuristic that produces good, not provably optimal, results.

Two further, unconditionally-beneficial refinements on top of that base
heuristic, both cheap relative to the placement work itself:

  - Shrink-back (_shrink_bin()): the grow loop stops at the *first* bin
    size that works, but growing geometrically (1.2x per step) can
    overshoot past the actual minimum. After a working size is found,
    binary-search each dimension back down independently (width first,
    then height against the now-tightened width) to the smallest size
    that still successfully packs everything -- strictly tightens the
    result, never loosens it.
  - Multi-order search (_ORDER_STRATEGIES): placement order affects the
    final bounding box (this is a heuristic algorithm, not an exact
    one), and "largest area first" is a good default but not always the
    best one for a given input. pack_rectangles() tries a handful of
    different orderings (by area, height, width, perimeter, each
    descending) and keeps whichever produces the smallest actual
    bounding box, not just the first one tried.

Both refinements only ever produce an equal-or-smaller result than the
single-order, no-shrink baseline -- there is no tradeoff/downside to
either, unlike size quantization (a separate, deliberately NOT-yet-wired
idea under evaluation elsewhere: rounding a block's *packing* size up to
a coarser grid can let MaxRects reuse free rectangles far more often
across near-identical-but-not-quite leaves, at the cost of the placed
rect no longer exactly matching the requested size).

Padding is handled by inflating each block's width/height by `padding`
before packing (never by inserting explicit gap rectangles), and
placing the *real* (un-inflated) rect at the inflated cell's top-left
corner. That single trick is sufficient to guarantee at least `padding`
separation between every pair of placed real rects, not just
cell-adjacent ones -- for any two non-overlapping inflated cells, they
must be separated along at least one axis (say C1 fully to the left of
C2: C1.right <= C2.left); the real rect inside C1 stops `padding` short
of C1's own right edge (since C1's width already carries the +padding
inflation) while the real rect inside C2 starts exactly at C2's left
edge (no inset on that side), so the gap between the two real rects
along that axis is (C2.left - C1.right) + padding, which is >= padding
since C2.left >= C1.right. The other three relative-position cases
(right-of, above, below) follow by the same argument. Confirmed directly
by tests/frontend/test_rect_packing.py, not just asserted here.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

_EPS = 1e-6
_GROW_FACTOR = 1.2
_SHRINK_TOLERANCE = 0.5  # px -- sub-pixel precision doesn't matter for a diagram
_SHRINK_MAX_ITERATIONS = 40  # safety cap; _SHRINK_TOLERANCE is what actually stops it in practice
_ORDER_STRATEGIES = ("area", "height", "width", "perimeter")


@dataclass(frozen=True)
class Block:
    id: str
    width: float
    height: float


@dataclass(frozen=True)
class Rect:
    x: float
    y: float
    w: float
    h: float


def pack_rectangles(blocks: list[Block], padding: float) -> dict[str, Rect]:
    """Places every block with no overlaps and at least `padding`
    separation between any two, minimizing (heuristically -- see module
    docstring) the bounding box of the whole layout. Returns
    block.id -> Rect for the block's own *real* (un-inflated) footprint.
    """
    if not blocks:
        return {}

    inflated = [(b.width + padding, b.height + padding) for b in blocks]

    best_order: list[int] | None = None
    best_placements: list[tuple[float, float]] | None = None
    best_area: float | None = None

    for strategy in _ORDER_STRATEGIES:
        order = _order_indices(inflated, strategy)
        ordered_sizes = [inflated[i] for i in order]
        placements = _pack_one_order(ordered_sizes)
        area = _bbox_area(ordered_sizes, placements)
        if best_area is None or area < best_area - _EPS:
            best_area = area
            best_order = order
            best_placements = placements

    result: dict[str, Rect] = {}
    for slot, (x, y) in enumerate(best_placements):
        block = blocks[best_order[slot]]
        result[block.id] = Rect(x=x, y=y, w=block.width, h=block.height)
    return result


def _order_indices(sizes: list[tuple[float, float]], strategy: str) -> list[int]:
    """Stable sort, descending -- ties keep the caller's own original
    order, so which of several equally-scored blocks is treated as
    "first" doesn't depend on dict/set iteration order anywhere
    upstream. "area": standard MaxRects default -- seating big items
    first leaves more, and more regularly-shaped, free space for smaller
    items to fill in later. The other three are tried alongside it
    because placement order is a heuristic choice, not a provably
    correct one -- see pack_rectangles()'s own docstring."""
    if strategy == "area":
        key = lambda i: sizes[i][0] * sizes[i][1]  # noqa: E731
    elif strategy == "height":
        key = lambda i: sizes[i][1]  # noqa: E731
    elif strategy == "width":
        key = lambda i: sizes[i][0]  # noqa: E731
    else:
        key = lambda i: sizes[i][0] + sizes[i][1]  # noqa: E731 -- "perimeter"
    return sorted(range(len(sizes)), key=key, reverse=True)


def _bbox_area(sizes: list[tuple[float, float]], placements: list[tuple[float, float]]) -> float:
    max_x = max(x + w for (x, y), (w, h) in zip(placements, sizes))
    max_y = max(y + h for (x, y), (w, h) in zip(placements, sizes))
    return max_x * max_y


def _pack_one_order(sizes: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Grows a bin until `sizes` (in this exact order) all fit, then
    shrinks that bin back down as far as it'll go (see module
    docstring), and returns the resulting placements."""
    total_area = sum(w * h for w, h in sizes)
    max_w = max(w for w, h in sizes)
    max_h = max(h for w, h in sizes)
    bin_w = max(max_w, math.sqrt(total_area))
    bin_h = max(max_h, math.sqrt(total_area))

    while _try_pack(sizes, bin_w, bin_h) is None:
        # Grow whichever dimension is currently smaller, keeping the
        # bin roughly square -- and retry from scratch (see module
        # docstring: a failed attempt's free-rectangle state can't be
        # reused for a different bin size).
        if bin_w <= bin_h:
            bin_w *= _GROW_FACTOR
        else:
            bin_h *= _GROW_FACTOR

    bin_w, bin_h = _shrink_bin(sizes, bin_w, bin_h, max_w, max_h)
    return _try_pack(sizes, bin_w, bin_h)


def _shrink_bin(sizes: list[tuple[float, float]], bin_w: float, bin_h: float, min_w: float, min_h: float) -> tuple[float, float]:
    """Binary-searches width down first (holding height at its current,
    known-working value), then height down (holding the now-tightened
    width) -- a working (bin_w, bin_h) only ever gets tighter here, never
    looser. `min_w`/`min_h` (the single largest item's own dimensions)
    are safe lower bounds to search from: no bin narrower/shorter than
    its single largest item could ever work, so they never need to be
    tested themselves."""
    lo, hi = min_w, bin_w
    for _ in range(_SHRINK_MAX_ITERATIONS):
        if hi - lo < _SHRINK_TOLERANCE:
            break
        mid = (lo + hi) / 2
        if _try_pack(sizes, mid, bin_h) is not None:
            hi = mid
        else:
            lo = mid
    bin_w = hi

    lo, hi = min_h, bin_h
    for _ in range(_SHRINK_MAX_ITERATIONS):
        if hi - lo < _SHRINK_TOLERANCE:
            break
        mid = (lo + hi) / 2
        if _try_pack(sizes, bin_w, mid) is not None:
            hi = mid
        else:
            lo = mid
    bin_h = hi

    return bin_w, bin_h


def _try_pack(sizes: list[tuple[float, float]], bin_w: float, bin_h: float) -> list[tuple[float, float]] | None:
    """Attempts to place every size (in the given order) into a
    bin_w x bin_h bin. None if any single item doesn't fit anywhere."""
    free_rects: list[tuple[float, float, float, float]] = [(0.0, 0.0, bin_w, bin_h)]
    placements: list[tuple[float, float]] = []

    for w, h in sizes:
        best = _find_best_area_fit(free_rects, w, h)
        if best is None:
            return None
        bx, by = best
        placements.append((bx, by))
        _place_and_split(free_rects, bx, by, w, h)

    return placements


def _find_best_area_fit(free_rects: list[tuple[float, float, float, float]], w: float, h: float) -> tuple[float, float] | None:
    best_leftover_area: float | None = None
    best_short_side: float | None = None
    best_pos: tuple[float, float] | None = None

    for fx, fy, fw, fh in free_rects:
        if fw + _EPS < w or fh + _EPS < h:
            continue
        leftover_area = fw * fh - w * h
        short_side = min(fw - w, fh - h)
        better = (
            best_leftover_area is None
            or leftover_area < best_leftover_area - _EPS
            or (abs(leftover_area - best_leftover_area) <= _EPS and short_side < best_short_side - _EPS)
        )
        if better:
            best_leftover_area = leftover_area
            best_short_side = short_side
            best_pos = (fx, fy)

    return best_pos


def _place_and_split(free_rects: list[tuple[float, float, float, float]], bx: float, by: float, w: float, h: float) -> None:
    px1, py1, px2, py2 = bx, by, bx + w, by + h

    new_free_rects: list[tuple[float, float, float, float]] = []
    remaining: list[tuple[float, float, float, float]] = []
    for fx, fy, fw, fh in free_rects:
        fx2, fy2 = fx + fw, fy + fh
        if not _intersects(fx, fy, fx2, fy2, px1, py1, px2, py2):
            remaining.append((fx, fy, fw, fh))
            continue
        if px1 > fx + _EPS:  # left leftover strip
            new_free_rects.append((fx, fy, px1 - fx, fh))
        if px2 < fx2 - _EPS:  # right leftover strip
            new_free_rects.append((px2, fy, fx2 - px2, fh))
        if py1 > fy + _EPS:  # top leftover strip
            new_free_rects.append((fx, fy, fw, py1 - fy))
        if py2 < fy2 - _EPS:  # bottom leftover strip
            new_free_rects.append((fx, py2, fw, fy2 - py2))

    remaining.extend(r for r in new_free_rects if r[2] > _EPS and r[3] > _EPS)
    free_rects[:] = _prune(remaining)


def _intersects(ax1: float, ay1: float, ax2: float, ay2: float, bx1: float, by1: float, bx2: float, by2: float) -> bool:
    return ax1 < bx2 - _EPS and ax2 > bx1 + _EPS and ay1 < by2 - _EPS and ay2 > by1 + _EPS


def _contains(outer: tuple[float, float, float, float], inner: tuple[float, float, float, float]) -> bool:
    ox, oy, ow, oh = outer
    ix, iy, iw, ih = inner
    return ix >= ox - _EPS and iy >= oy - _EPS and ix + iw <= ox + ow + _EPS and iy + ih <= oy + oh + _EPS


def _prune(free_rects: list[tuple[float, float, float, float]]) -> list[tuple[float, float, float, float]]:
    """Drops every free rectangle fully contained within another --
    MaxRects deliberately allows the working set to hold overlapping
    rects (see module docstring), but a rect wholly inside another
    contributes nothing a later placement couldn't already find via the
    rect containing it."""
    kept: list[tuple[float, float, float, float]] = []
    for i, r in enumerate(free_rects):
        contained = False
        for j, other in enumerate(free_rects):
            if i == j:
                continue
            if _contains(other, r) and (not _contains(r, other) or j < i):
                contained = True
                break
        if not contained:
            kept.append(r)
    return kept
