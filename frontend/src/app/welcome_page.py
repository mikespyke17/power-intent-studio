"""Welcome page shown before a project exists -- explicit request: an
EDA-tool-style landing screen (logo + app name), shown as
`MainWindow`'s own central `QStackedWidget` page 0 until "File > Create
Project" succeeds and switches to page 1 (`self.tabs`) -- see
main_window.py's own `_on_create_project()`.

Deliberately styled distinct from the rest of the app (a dark
background, not the working UI's default light Fusion theme) -- common
for EDA tool splash/landing screens (Vivado, Quartus, ...); this page
is purely informational, no interactive controls of its own -- "Create
Project" (and, added later, "Load Project" -- available from the very
start, explicit request) only ever live in the File menu, per the
explicit request, not duplicated as buttons here too. The hint line
below just names both.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtSvgWidgets import QSvgWidget
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

LOGO_PATH = Path(__file__).resolve().parents[2] / "icons" / "power-intent-studio-logo.svg"

_LOGO_SIZE = 160
_BACKGROUND_COLOR = "#1e1f22"
_TITLE_COLOR = "#f2f2f2"
_SUBTITLE_COLOR = "#9fa0a5"
_HINT_COLOR = "#6f7075"


class WelcomePage(QWidget):
    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        # A real bug, found via a live screenshot: a plain QWidget's own
        # stylesheet background-color does NOT paint across the widget's
        # full area by default -- only WA_StyledBackground widgets self-
        # paint their own stylesheet background; without it, the colour
        # only showed up "leaking" into each child QLabel's own tight
        # bounding box (Qt's stylesheet cascade still hands the
        # inherited background-color down to children that don't
        # override it), leaving the true page background the default
        # light grey with disconnected dark bands behind each label
        # instead of one full dark page.
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"background-color: {_BACKGROUND_COLOR};")

        # QSvgWidget (not a rasterized QPixmap) -- re-renders the vector
        # source on every paint, so the logo stays crisp on any display
        # scale factor instead of blurring if this window is ever moved
        # to a higher-DPI screen.
        logo_widget = QSvgWidget(str(LOGO_PATH))
        logo_widget.setFixedSize(_LOGO_SIZE, _LOGO_SIZE)

        title_label = QLabel("Power Intent Studio")
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_label.setStyleSheet(f"color: {_TITLE_COLOR}; font-size: 32px; font-weight: 600;")

        subtitle_label = QLabel("RTL + UPF Elaboration and Power Intent Editing")
        subtitle_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle_label.setStyleSheet(f"color: {_SUBTITLE_COLOR}; font-size: 14px;")

        hint_label = QLabel("File → Create Project  ·  File → Load Project")
        hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint_label.setStyleSheet(f"color: {_HINT_COLOR}; font-size: 12px;")

        layout = QVBoxLayout(self)
        layout.addStretch(2)
        layout.addWidget(logo_widget, alignment=Qt.AlignmentFlag.AlignHCenter)
        layout.addSpacing(20)
        layout.addWidget(title_label)
        layout.addWidget(subtitle_label)
        layout.addSpacing(36)
        layout.addWidget(hint_label)
        layout.addStretch(3)
