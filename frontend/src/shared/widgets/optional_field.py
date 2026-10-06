"""Global styling rule (explicit request): wherever a field is marked
"(Optional)", the word must render to the RIGHT of the input widget it
modifies, in italic -- never below it, never as part of the row's own
caption label. `wrap_optional_field()` packages a field widget together
with this suffix into one composite widget suitable for
`QFormLayout.addRow(plain_caption, wrap_optional_field(field))` -- the
row's own caption stays plain text, with no "(Optional)" baked into it.
"""

from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget


def wrap_optional_field(field: QWidget) -> QWidget:
    label = QLabel("(Optional)")
    font = label.font()
    font.setItalic(True)
    label.setFont(font)

    container = QWidget()
    layout = QHBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.addWidget(field)
    layout.addWidget(label)
    return container
