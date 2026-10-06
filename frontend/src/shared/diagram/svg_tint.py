"""Tints a black-silhouette SVG render to an arbitrary colour at paint
time -- shared by power_intent_view's own SupplyPortIconItem (a user-
chosen colour per port, see supply_port_overlay.py) and
RetentionBadgeIconItem (a fixed highlight colour when click-selected,
see retention_overlay.py), so both go through one identical masking
implementation rather than two copies of the same composition-mode
trick.

Genuinely shared/generic (no power-intent awareness at all) -- lives
here, not in power_intent_view, for the same reason hierarchy_scene_builder.py/
rect_packing.py already do: this module has nothing power-intent-specific
about it, and a future non-power-intent diagram item that wants a
colour-swappable icon can reuse it too.

Renders into a SEPARATE, temporary QImage first, then composites that
onto the real painter -- applying QPainter.CompositionMode_SourceIn
directly on the live scene painter would punch a hole based on
whatever's already drawn behind the icon (a hierarchy box's own fill,
another item), not the icon's own silhouette. The temporary image's own
resolution is derived from the CURRENT painter's own transform (however
zoomed the view is right now, mapped through `target_rect` rather than
read off a single transform component, since a component like m11()
alone goes to ~0 under a 90-degree rotation and would otherwise size the
buffer down to nothing) -- rendered fresh on every single paint() call,
never cached, so a tinted/masked icon stays exactly as zoom-crisp as the
plain (untinted) live-SVG render this technique is layered on top of.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

_MIN_DEVICE_SIZE = 1
_MAX_DEVICE_SIZE = 2048  # sane ceiling -- guards against a degenerate transform producing an absurd allocation


def render_tinted_svg(painter: QPainter, renderer: QSvgRenderer, target_rect: QRectF, color: QColor) -> None:
    """Renders `renderer`'s own SVG (expected to be a solid black
    silhouette -- fill="none"/coloured strokes are NOT masked out by
    this technique, only true black fill is) into `target_rect` of
    `painter`, tinted to `color`. Caller is responsible for any
    painter.save()/rotate()/restore() around this call (e.g. Supply
    Port's own per-edge rotation) -- rotating the OUTER painter before
    calling this still rotates the final composited result correctly,
    since the tinted image is drawn back via `painter.drawImage()`,
    which is subject to whatever transform is active on `painter` at
    that point."""
    device_rect = painter.transform().mapRect(target_rect)
    device_size = max(_MIN_DEVICE_SIZE, min(_MAX_DEVICE_SIZE, round(max(device_rect.width(), device_rect.height()))))

    image = QImage(device_size, device_size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    tint_painter = QPainter(image)
    tint_painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(tint_painter, QRectF(0, 0, device_size, device_size))
    tint_painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    tint_painter.fillRect(0, 0, device_size, device_size, color)
    tint_painter.end()

    painter.drawImage(target_rect, image)
