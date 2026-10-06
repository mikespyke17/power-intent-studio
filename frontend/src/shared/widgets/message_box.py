"""Drop-in replacements for QMessageBox.warning()/question()/critical()
(explicit request, two bugs sharing one root cause): every error/
validation/confirmation dialog in this app must be (a) user-resizable
and (b) have its message text mouse-selectable/copyable -- neither of
which QMessageBox's own static convenience methods support, since they
construct their own internal instance with no way for a caller to reach
in and set setSizeGripEnabled()/setTextInteractionFlags() before it's
shown (confirmed empirically: a plain QMessageBox is NOT resizable by
default, unlike this app's own custom QDialog subclasses, which never
set any fixed-size constraint and are already resizable without
needing this fix -- see each of those dialogs' own setSizeGripEnabled()
call, added for the same explicit request but reachable directly
there).

These wrappers construct a real QMessageBox instance with both
properties set, then exec() it -- the exact same call signature and
return value as the static methods they replace (confirmed: a
constructed instance's own exec() returns the identical
QMessageBox.StandardButton-comparable value the static method's own
internal implementation already returns), so every call site across
this package only needs `QMessageBox.warning(`/`.question(`/`.critical(`
swapped for `warning(`/`question(`/`critical(` from this module, no
other change -- including every existing `reply ==
QMessageBox.StandardButton.X` comparison downstream, which keeps
working unmodified."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox, QWidget

_SELECTABLE_TEXT_FLAGS = Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.TextSelectableByKeyboard


def _show(
    icon: QMessageBox.Icon,
    parent: Optional[QWidget],
    title: str,
    text: str,
    buttons: QMessageBox.StandardButton,
    default_button: QMessageBox.StandardButton,
) -> QMessageBox.StandardButton:
    box = QMessageBox(icon, title, text, buttons, parent)
    if default_button != QMessageBox.StandardButton.NoButton:
        box.setDefaultButton(default_button)
    box.setSizeGripEnabled(True)
    box.setTextInteractionFlags(_SELECTABLE_TEXT_FLAGS)
    return box.exec()


def warning(
    parent: Optional[QWidget],
    title: str,
    text: str,
    buttons: QMessageBox.StandardButton = QMessageBox.StandardButton.Ok,
    default_button: QMessageBox.StandardButton = QMessageBox.StandardButton.NoButton,
) -> QMessageBox.StandardButton:
    return _show(QMessageBox.Icon.Warning, parent, title, text, buttons, default_button)


def question(
    parent: Optional[QWidget],
    title: str,
    text: str,
    buttons: QMessageBox.StandardButton = QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
    default_button: QMessageBox.StandardButton = QMessageBox.StandardButton.NoButton,
) -> QMessageBox.StandardButton:
    return _show(QMessageBox.Icon.Question, parent, title, text, buttons, default_button)


def critical(
    parent: Optional[QWidget],
    title: str,
    text: str,
    buttons: QMessageBox.StandardButton = QMessageBox.StandardButton.Ok,
    default_button: QMessageBox.StandardButton = QMessageBox.StandardButton.NoButton,
) -> QMessageBox.StandardButton:
    return _show(QMessageBox.Icon.Critical, parent, title, text, buttons, default_button)
