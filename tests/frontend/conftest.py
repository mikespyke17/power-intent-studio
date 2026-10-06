"""Forces the offscreen Qt platform plugin before any PySide6 import
happens, so frontend tests run headless (CI, this sandbox) without a
real display. Must be set before QApplication is ever constructed --
os.environ is set here, at conftest collection time, ahead of any test
module's `from PySide6 import ...`.

The green-accent stylesheet (app/theme.py) is deliberately NOT applied
here: a QApplication stylesheet that is active while a table is
*constructed* makes Qt drop that table's own bold-header
`setFont(QFont(bold))`, so a session-wide fixture would spuriously
break the pre-existing "bold header row" tests. The real app sidesteps
this by applying the stylesheet AFTER MainWindow is built (main.py);
test_theme.py verifies both that ordering and that widgets built later
are fine.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


# --- the "Extract Power Intent" options dialog ---------------------------
#
# A modal `exec()` in a headless run waits forever for a click that can
# never come -- a hazard this suite has already been bitten by more than
# once. `_on_extract_power_intent()` gained such a dialog, and five
# pre-existing tests drive that action; without a guard, each one hangs
# the WHOLE run rather than failing.
#
# Auto-accepting it with its default (legacy PST off) preserves exactly
# what those tests were written to assert. The dialog's own behaviour is
# tested separately, in test_main_window.py, by replacing this stub with
# one that answers deliberately -- which works because the patch below
# targets the name `main_window` looks up at call time.

import pytest


@pytest.fixture(autouse=True)
def _auto_accept_extract_options(monkeypatch):
    try:
        from PySide6.QtWidgets import QDialog

        from frontend.src.app import main_window as main_window_module
        from frontend.src.power_intent_view.extract_options_dialog import ExtractOptionsDialog
    except ImportError:  # pragma: no cover -- non-Qt tests never reach here
        return

    class _AutoAccepted(ExtractOptionsDialog):
        def exec(self):  # noqa: D102 -- stands in for a real modal
            return QDialog.DialogCode.Accepted

    monkeypatch.setattr(main_window_module, "ExtractOptionsDialog", _AutoAccepted)
