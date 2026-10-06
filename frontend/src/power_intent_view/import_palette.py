"""Colour assignment for imported power intent (spec section 6).

An imported UPF/CPF file carries no diagram colours, so the import has
to invent them. The one hard constraint is that every Power Domain must
end up visually distinguishable from every other in the same design.

WHY THIS ASSIGNS SUPPLY COLOURS, NOT DOMAIN COLOURS. A Power Domain's
colour is not its own property in this app -- it is DERIVED live from its
Primary Supply Set's Power rail at a fixed transparency (see
power_domain_overlay.resolve_domain_color()). Colouring domains directly
would be overwritten on the next refresh. So distinctness has to be
engineered one level down, on the supply ports backing each domain's
primary supply set, which is what this module does.

THE FIXED PALETTE IS REUSED, NOT REPLACED. Section 6 asks to check for an
existing palette first: `diagram_overlay._DOMAIN_PALETTE` has six hues,
used by the read-only Milestone-2 overlay with `i % len(palette)` -- so
it repeats once exhausted, which is precisely the collision this import
must avoid. The six hues are reused here in their original order (a
design importing two domains gets the same familiar colours it always
had), and generation continues procedurally past six instead of wrapping.

BEYOND SIX, GOLDEN-ANGLE CANDIDATES WITH AN ACCEPTANCE TEST. Successive
hues are proposed at ~137.5 degree steps rather than an even 360/N: even
spacing needs N up front and re-colours everything when N changes, while
the golden angle keeps every prefix well-separated no matter where it
stops -- the property actually wanted when the domain count isn't known
until the file is parsed.

But proposing is not enough. Measured, a pure golden-angle walk collided
with the FIXED palette (the 8-domain case put two colours 35.7 RGB units
apart, and 20 domains got down to 28.1 -- both under the 40 this module
calls distinct), and adding lightness bands did not fix it either. So a
candidate is only ACCEPTED if it clears `_MINIMUM_DISTANCE` against every
colour already assigned, including the fixed six; otherwise the walk
advances and tries the next one. That makes the requirement hold by
construction rather than by choosing lucky constants, which is what the
distinctness test actually asserts.
"""

from __future__ import annotations

from typing import Iterable, Optional

from PySide6.QtGui import QColor

# The six hues the read-only overlay already uses, at full opacity -- the
# alpha there (60) is that overlay's own translucency, applied again by
# the domain fill, so carrying it here would double it.
FIXED_PALETTE: tuple[QColor, ...] = (
    QColor(66, 133, 244),
    QColor(219, 68, 55),
    QColor(244, 180, 0),
    QColor(15, 157, 88),
    QColor(171, 71, 188),
    QColor(0, 172, 193),
)

_GOLDEN_ANGLE_DEGREES = 137.508
_GENERATED_SATURATION = 0.62
_LIGHTNESS_BANDS = (0.52, 0.36, 0.68, 0.44, 0.60)
_MINIMUM_DISTANCE = 40
# A bounded search: if this many candidates in a row all collide, the
# colour space at this saturation is genuinely full, and the best
# available candidate is taken rather than spinning forever -- no colour
# is worse than a close one.
#
# MEASURED, not assumed: the guarantee holds exactly to 32 domains
# (closest pair 40.0). At 50 it degrades to 38.4, just under threshold,
# which is the saturation fallback doing its job rather than a bug.
# Real designs are nowhere near either number -- the largest fixture in
# this project has two -- so the limit is recorded rather than engineered
# around.
_MAX_CANDIDATES_PER_SLOT = 400

# A ground rail is conventionally dark and is NOT part of the
# distinctness requirement -- domains are distinguished by their POWER
# rail. Giving every ground the same near-black keeps imported diagrams
# readable and matches what a user would pick by hand.
GROUND_COLOR = QColor(38, 42, 48)

_sequence: list[QColor] = []


def _distance(first: QColor, second: QColor) -> float:
    dr = first.red() - second.red()
    dg = first.green() - second.green()
    db = first.blue() - second.blue()
    return (dr * dr + dg * dg + db * db) ** 0.5


def _candidate(step: int) -> QColor:
    hue = ((step + 1) * _GOLDEN_ANGLE_DEGREES) % 360.0
    lightness = _LIGHTNESS_BANDS[(step // len(_LIGHTNESS_BANDS)) % len(_LIGHTNESS_BANDS)]
    color = QColor()
    color.setHslF(hue / 360.0, _GENERATED_SATURATION, lightness)
    return color


def _extend_sequence_to(length: int) -> None:
    """Grows the shared colour sequence, accepting only candidates that
    clear the distance threshold against everything already in it.

    Deterministic by construction: the sequence is built from index 0 and
    only ever appended to, so a given index always resolves to the same
    colour and re-importing the same file twice produces the same
    diagram."""
    if not _sequence:
        _sequence.extend(QColor(c) for c in FIXED_PALETTE)
    step = 0
    while len(_sequence) < length:
        threshold = _MINIMUM_DISTANCE
        tries = 0
        while True:
            candidate = _candidate(step)
            step += 1
            tries += 1
            if all(_distance(candidate, existing) >= threshold for existing in _sequence):
                _sequence.append(candidate)
                break
            if tries >= _MAX_CANDIDATES_PER_SLOT:
                # Saturated -- take the best available rather than spin.
                best = max(
                    (_candidate(s) for s in range(step, step + _MAX_CANDIDATES_PER_SLOT)),
                    key=lambda c: min(_distance(c, existing) for existing in _sequence),
                )
                _sequence.append(best)
                break


def color_for_index(index: int) -> QColor:
    """The colour for the `index`-th distinct power supply -- the first
    six straight from FIXED_PALETTE, the rest generated and accepted per
    the module docstring."""
    _extend_sequence_to(index + 1)
    return QColor(_sequence[index])


def assign_supply_colors(
    power_supply_names: Iterable[str],
    ground_supply_names: Optional[Iterable[str]] = None,
) -> dict[str, QColor]:
    """A colour per supply name.

    `power_supply_names` is taken in priority order -- callers pass the
    supplies backing each domain's PRIMARY supply set first, so the
    domains that must be told apart get the earliest, most separated
    hues, and any remaining supplies take what follows.

    Ground rails all share GROUND_COLOR; see its own comment. A name that
    is both (a power rail for one set and a ground for another -- legal,
    if unusual) keeps its power colour, since that is the one a domain
    derives from."""
    colors: dict[str, QColor] = {}
    for name in ground_supply_names or ():
        if name:
            colors[name] = QColor(GROUND_COLOR)
    index = 0
    for name in power_supply_names:
        if not name:
            continue
        colors[name] = color_for_index(index)  # overwrites a ground assignment on purpose
        index += 1
    return colors


def are_visually_distinct(first: QColor, second: QColor, minimum_distance: int = 40) -> bool:
    """Whether two colours read as different at a glance.

    Plain RGB euclidean distance, deliberately: the requirement is "a
    human can tell these two domain fills apart", and a perceptual metric
    would be more precise than the question needs. `minimum_distance` is
    the threshold the import's own test asserts against."""
    dr = first.red() - second.red()
    dg = first.green() - second.green()
    db = first.blue() - second.blue()
    return (dr * dr + dg * dg + db * db) ** 0.5 >= minimum_distance
