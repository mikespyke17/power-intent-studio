"""Diagram tool palette icons/cursors -- explicit request, "the current
toolbar icons do not match the icon assets I provided... replace them
with the actual provided files": renders the real SVG assets already
sitting in `frontend/icons/` (`cursor.svg`, `pan.svg`, `zoom-lens.svg`,
`supply-port.svg`, `power-net.svg` -- the last is the "Supply Net"
concept's own asset, matching its glyph: a T-junction of two power-net
leads meeting at a supply point) instead of the earlier QPainter-drawn
placeholder glyphs this module used to build by hand. `supply-port.svg`
is the SAME file `supply_port_overlay.py` already renders for the real
diagram supply-port icons -- reusing it here for the toolbar button too
is the same concept, not a coincidence. `pan.svg` (a simple open-palm-
plus-fingers glyph, same minimalist stroke style/weight/colour as its
siblings) is new -- explicit request, no such asset existed yet for the
new dedicated Pan tool, so one was created matching the existing set.
`internal-supply.svg` (explicit request, "Internal Supply" tool, a
new distinct entity type from Supply Port -- see supply_port_overlay.py's
own SupplyPortPlacement.is_internal_supply) was likewise already
provided, no placeholder needed. Same for `power-switch.svg` ("Power
Switch" tool, power_switch_overlay.py) -- already sitting in
frontend/icons/, found and reused as-is per explicit instruction not
to fabricate a placeholder. Same again for `isolation-cell.svg`
("Isolation Cell" tool, isolation_overlay.py) -- already sitting in
frontend/icons/ alongside its own clamp-value variants
(isolation-cell-clamp-{low,high,keep}.svg, not used by this toolbar
button itself), found and reused as-is. Same again for
`level-shifter.svg` ("Level Shifter" tool, level_shifter_overlay.py) --
already sitting in frontend/icons/, found and reused as-is, no
placeholder needed. Same again for `virtual-supply.svg` ("Virtual
Supply" tool, supply_port_overlay.py) -- already sitting in
frontend/icons/, found and reused as-is, no placeholder needed.

Kept as its own dedicated module for the same reason as before: callers
(`diagram_tool_palette.py`/`power_intent_diagram_view.py`) only ever see
`QIcon`/`QCursor` return values, never how they're actually produced --
so a future asset swap only touches this file.

Every mode also gets a matching cursor (explicit request: the mouse
cursor itself changes shape per selected tool while inside the Diagram
View's canvas -- see `_PowerIntentGraphicsView.set_mode()`), built from
the SAME source SVG at a larger, more cursor-appropriate size, with the
hotspot placed at the glyph's own visual center so clicks land where
the user expects.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QCursor, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

_ICONS_DIR = Path(__file__).resolve().parents[2] / "icons"

_CURSOR_SVG = "cursor.svg"
_PAN_SVG = "pan.svg"
_ZOOM_LENS_SVG = "zoom-lens.svg"
_SUPPLY_PORT_SVG = "supply-port.svg"
_INTERNAL_SUPPLY_SVG = "internal-supply.svg"  # already-provided asset -- no placeholder needed
_VIRTUAL_SUPPLY_SVG = "virtual-supply.svg"  # already-provided asset -- no placeholder needed
_SUPPLY_NET_SVG = "power-net.svg"  # the "Supply Net" concept's own provided asset
_RETENTION_SVG = "retention-flop.svg"  # already-provided asset -- no placeholder needed
_POWER_SWITCH_SVG = "power-switch.svg"  # already-provided asset -- no placeholder needed
_ISOLATION_CELL_SVG = "isolation-cell.svg"  # already-provided asset -- no placeholder needed
_LEVEL_SHIFTER_SVG = "level-shifter.svg"  # already-provided asset -- no placeholder needed


# Explicit request ("icons... displaying as pixelated/low quality"):
# rendered at _RENDER_SUPERSAMPLE times the requested size, then smoothly
# downscaled to that exact size -- a real quality gap, found by reading
# this function: QSvgRenderer was already used correctly (never a raw
# QPixmap(path) load), but rasterizing straight to a small target size
# (14-32px) produces visibly jagged edges from a vector source that has
# far more detail available. Supersampling first and downscaling with
# Qt.TransformationMode.SmoothTransformation is the standard fix, and
# keeps the returned pixmap's own dimensions identical to `size` (no
# devicePixelRatio tagging) -- deliberately, so every existing caller
# that reads `.width()`/`.height()` for anchoring/centering math
# (supply_port_overlay.py/retention_overlay.py's own icon-positioning
# logic) needs zero changes. The complementary fix for pixelation from
# the DIAGRAM's own zoom (scaling an already-rendered pixmap item up
# further) is enabling QPainter.RenderHint.SmoothPixmapTransform on the
# QGraphicsView itself (see hierarchy_diagram_view.py's own __init__).
_RENDER_SUPERSAMPLE = 4


def _render_svg(filename: str, size: int) -> QPixmap:
    svg_bytes = QByteArray((_ICONS_DIR / filename).read_bytes())
    renderer = QSvgRenderer(svg_bytes)
    render_size = size * _RENDER_SUPERSAMPLE
    pixmap = QPixmap(render_size, render_size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter)
    painter.end()
    return pixmap.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)


def _svg_icon(filename: str, size: int) -> QIcon:
    return QIcon(_render_svg(filename, size))


def _svg_cursor(filename: str, size: int) -> QCursor:
    """Hotspot at the glyph's own geometric center -- every one of the
    4 provided SVGs is drawn within the same 24x24 viewBox roughly
    centered on (11-12, 11-12), close enough to the pixmap's own
    geometric center that a uniform `size // 2` hotspot is accurate for
    all of them without needing a per-glyph special case."""
    pixmap = _render_svg(filename, size)
    center = size // 2
    return QCursor(pixmap, center, center)


def cursor_icon(size: int = 24) -> QIcon:
    return _svg_icon(_CURSOR_SVG, size)


def pan_icon(size: int = 24) -> QIcon:
    return _svg_icon(_PAN_SVG, size)


# No pan_cursor() -- explicit request: Pan mode's own cursor is Qt's
# stock Qt.CursorShape.OpenHandCursor, "exactly as Regular Cursor mode
# used before this tool palette existed," not a custom pixmap cursor
# built from pan.svg the way every other mode's cursor is.


def zoom_lens_icon(size: int = 24) -> QIcon:
    return _svg_icon(_ZOOM_LENS_SVG, size)


def zoom_lens_cursor(size: int = 32) -> QCursor:
    return _svg_cursor(_ZOOM_LENS_SVG, size)


def supply_port_icon(size: int = 24) -> QIcon:
    return _svg_icon(_SUPPLY_PORT_SVG, size)


def supply_port_cursor(size: int = 32) -> QCursor:
    return _svg_cursor(_SUPPLY_PORT_SVG, size)


def internal_supply_icon(size: int = 24) -> QIcon:
    return _svg_icon(_INTERNAL_SUPPLY_SVG, size)


def internal_supply_cursor(size: int = 32) -> QCursor:
    return _svg_cursor(_INTERNAL_SUPPLY_SVG, size)


def virtual_supply_icon(size: int = 24) -> QIcon:
    return _svg_icon(_VIRTUAL_SUPPLY_SVG, size)


def virtual_supply_cursor(size: int = 32) -> QCursor:
    return _svg_cursor(_VIRTUAL_SUPPLY_SVG, size)


def supply_net_icon(size: int = 24) -> QIcon:
    return _svg_icon(_SUPPLY_NET_SVG, size)


def supply_net_cursor(size: int = 32) -> QCursor:
    return _svg_cursor(_SUPPLY_NET_SVG, size)


def retention_icon(size: int = 24) -> QIcon:
    return _svg_icon(_RETENTION_SVG, size)


def retention_cursor(size: int = 32) -> QCursor:
    return _svg_cursor(_RETENTION_SVG, size)


def power_switch_icon(size: int = 24) -> QIcon:
    return _svg_icon(_POWER_SWITCH_SVG, size)


def power_switch_cursor(size: int = 32) -> QCursor:
    return _svg_cursor(_POWER_SWITCH_SVG, size)


def isolation_cell_icon(size: int = 24) -> QIcon:
    return _svg_icon(_ISOLATION_CELL_SVG, size)


def isolation_cell_cursor(size: int = 32) -> QCursor:
    return _svg_cursor(_ISOLATION_CELL_SVG, size)


def level_shifter_icon(size: int = 24) -> QIcon:
    return _svg_icon(_LEVEL_SHIFTER_SVG, size)


def level_shifter_cursor(size: int = 32) -> QCursor:
    return _svg_cursor(_LEVEL_SHIFTER_SVG, size)
