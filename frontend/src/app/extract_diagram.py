"""'Extract Diagram' (explicit request): export one or more open
diagram tabs to image files (PNG / JPEG / SVG).

Two entry points, both landing here:
  1. Menu bar (Extract > Extract Diagram) -- with more than one diagram
     tab open, `ExtractDiagramDialog` shows a multi-select list; with
     exactly one, that one is the implicit target.
  2. Right-click on empty canvas space inside a specific diagram -- that
     one diagram is the unambiguous target, so the dialog skips the
     list entirely.

Multiple selected diagrams in one operation -> ONE FILE PER DIAGRAM
(confirmed with the user), each named `<base>_<tab-name>.<ext>` (the
base filename field acts as a prefix when >1 is selected; with exactly
one it IS the filename). Tab names are sanitized for the filesystem
(``/`` -> ``_`` etc.).

Rendering goes through the QGraphicsScene, not a viewport grab, so the
output is the whole diagram at full fidelity regardless of the on-
screen zoom/scroll position: raster formats render `itemsBoundingRect()`
into a `QImage`; SVG renders the same rect into a `QSvgGenerator`."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QMarginsF, QRectF, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtSvg import QSvgGenerator
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

# (extension, Qt image-format handler or None for SVG)
IMAGE_FORMATS: dict[str, str] = {"PNG": "png", "JPEG": "jpg", "SVG": "svg"}
_RASTER_SCALE = 2  # supersample raster output a little so text/edges stay crisp
_RENDER_MARGIN = 12.0


def sanitize_filename_component(name: str) -> str:
    """A tab name ("top", "top/u_core", ...) -> a safe single filename
    component ("top", "top_u_core"). Collapses any run of characters
    that aren't alnum/dash/underscore/dot to a single underscore, and
    trims leading/trailing separators; falls back to "diagram" for a
    name that sanitizes to nothing."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_")
    return cleaned or "diagram"


def _content_rect(view) -> QRectF:
    scene = view.scene()
    rect = scene.itemsBoundingRect() if scene is not None else QRectF()
    if rect.isEmpty():
        rect = view.sceneRect()
    return rect.marginsAdded(QMarginsF(_RENDER_MARGIN, _RENDER_MARGIN, _RENDER_MARGIN, _RENDER_MARGIN))


def render_view_to_file(view, filepath: str, image_format: str) -> None:
    """Render `view`'s whole scene to `filepath` in `image_format`
    (one of IMAGE_FORMATS' keys). Raises OSError / RuntimeError-style
    failures to the caller; a QImage that fails to save raises
    RuntimeError so the caller never reports a silent success."""
    fmt = image_format.upper()
    if fmt not in IMAGE_FORMATS:
        raise ValueError(f"unsupported image format {image_format!r}")
    scene = view.scene()
    source = _content_rect(view)

    if fmt == "SVG":
        generator = QSvgGenerator()
        generator.setFileName(filepath)
        generator.setSize(source.size().toSize())
        generator.setViewBox(QRectF(0, 0, source.width(), source.height()))
        generator.setTitle("Power Intent Studio diagram export")
        painter = QPainter(generator)
        scene.render(painter, QRectF(0, 0, source.width(), source.height()), source)
        painter.end()
        if not Path(filepath).exists():
            raise RuntimeError(f"failed to write SVG to {filepath}")
        return

    width = max(1, round(source.width() * _RASTER_SCALE))
    height = max(1, round(source.height() * _RASTER_SCALE))
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    # JPEG has no alpha channel -- fill white so the diagram doesn't
    # composite onto black. PNG keeps transparency.
    image.fill(Qt.GlobalColor.white if fmt == "JPEG" else Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    scene.render(painter, QRectF(0, 0, width, height), source)
    painter.end()
    if not image.save(filepath, "JPEG" if fmt == "JPEG" else "PNG"):
        raise RuntimeError(f"failed to write {fmt} image to {filepath}")


def output_path_for(directory: str, base_filename: str, tab_name: str, image_format: str, single: bool) -> str:
    """The final file path for one exported diagram. With a single
    selection the base filename IS the name; with several, each file is
    `<base>_<sanitized-tab-name>.<ext>` so they don't collide."""
    ext = IMAGE_FORMATS[image_format.upper()]
    base = Path(base_filename).stem or "diagram"
    stem = base if single else f"{base}_{sanitize_filename_component(tab_name)}"
    return str(Path(directory) / f"{stem}.{ext}")


class ExtractDiagramDialog(QDialog):
    """Collects the export parameters in one dialog: which diagram(s)
    (a checkable list -- only shown when there's a real choice to make),
    a base filename, a destination directory, and the image format."""

    def __init__(self, diagram_names: list[str], allow_multi_select: bool, default_directory: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("Extract Diagram")
        self.setSizeGripEnabled(True)

        self._names = list(diagram_names)
        self._list: Optional[QListWidget] = None

        layout = QVBoxLayout(self)

        if allow_multi_select and len(diagram_names) > 1:
            layout.addWidget(QLabel("Diagrams to export:"))
            self._list = QListWidget()
            for name in diagram_names:
                item = QListWidgetItem(name)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Checked)
                self._list.addItem(item)
            layout.addWidget(self._list)

        form = QFormLayout()
        default_base = sanitize_filename_component(diagram_names[0]) if diagram_names else "diagram"
        self.filename_edit = QLineEdit(default_base)
        self.filename_edit.setPlaceholderText("e.g. power_diagram \u2014 one file per open diagram")
        form.addRow("Base filename:", self.filename_edit)

        dir_row = QHBoxLayout()
        self.directory_edit = QLineEdit(default_directory)
        self.directory_edit.setPlaceholderText("e.g. /home/user/exports")
        browse_button = QPushButton("Browse…")
        browse_button.clicked.connect(self._browse_directory)
        dir_row.addWidget(self.directory_edit, stretch=1)
        dir_row.addWidget(browse_button)
        dir_widget = QWidget()
        dir_widget.setLayout(dir_row)
        form.addRow("Directory:", dir_widget)

        self.format_combo = QComboBox()
        self.format_combo.addItems(list(IMAGE_FORMATS.keys()))
        form.addRow("Format:", self.format_combo)
        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _browse_directory(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Choose export directory", self.directory_edit.text())
        if chosen:
            self.directory_edit.setText(chosen)

    @property
    def selected_names(self) -> list[str]:
        if self._list is None:
            return list(self._names)
        return [self._list.item(i).text() for i in range(self._list.count()) if self._list.item(i).checkState() == Qt.CheckState.Checked]

    @property
    def base_filename(self) -> str:
        return self.filename_edit.text().strip()

    @property
    def directory(self) -> str:
        return self.directory_edit.text().strip()

    @property
    def image_format(self) -> str:
        return self.format_combo.currentText()
