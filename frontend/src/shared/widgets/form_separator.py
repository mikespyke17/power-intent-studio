"""A thin horizontal rule for visually grouping a QFormLayout's own rows
into sections (explicit request: Power Switch/Level Shifter/Retention/
Isolation dialogs all group "Applies to" / core fields / Elements into
separate visual blocks). `form.addRow(form_separator())` -- QFormLayout's
own single-argument addRow() spans the widget across both columns.
"""

from __future__ import annotations

from PySide6.QtWidgets import QFrame


def form_separator() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setFrameShadow(QFrame.Shadow.Sunken)
    return line
