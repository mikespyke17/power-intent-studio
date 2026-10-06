"""Entry point. `python -m frontend.src.app.main` from the repo root.

Wires the real backend (see docs/backend_integration.md) -- this is the
app's single, standard entry point; `MainWindow`'s own bare constructor
still defaults to mock/fixture data (unused here) for the front-end test
suite and any backend-independent iteration."""

import ctypes
import sys

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from frontend.src.app.main_window import MainWindow
from frontend.src.app.theme import apply_fixed_appearance, build_stylesheet
from frontend.src.app.welcome_page import LOGO_PATH
from frontend.src.backend_integration.real_actions import BackendState, make_elaborate_action, make_load_power_intent_action, make_mapping_action


def set_windows_app_user_model_id() -> None:
    """Windows-only taskbar fix (explicit request): without an explicit
    AppUserModelID, the Windows taskbar shows the Python interpreter's
    own icon and GROUPS the running window under `python.exe`/
    `pythonw.exe` instead of showing it as its own distinct entry.
    Setting a unique id BEFORE `QApplication` is created fixes the
    grouping AND lets the taskbar pick up the window icon set below.

    Must be a harmless no-op on every other platform (guarded on
    `sys.platform`), and must never raise -- this is a cosmetic fix, not
    something worth crashing startup over.

    Scope note: this covers the `python power_intent_studio.py`
    (interpreted-script) launch, which is the only way this repo is run
    today -- there is no packaging/build script (no PyInstaller `.spec`,
    `setup.py`, `pyproject.toml`, `.desktop` file, or `.ico`). If a
    standalone `.exe` build is ever added, its build step should ALSO
    embed the icon directly (PyInstaller `--icon=<path>.ico`) -- that
    governs the File Explorer / pinned-taskbar icon independent of any
    running window, which this AppUserModelID call does not."""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("PowerIntentStudio.App.1")
    except (AttributeError, OSError):
        pass


def build_main_window() -> MainWindow:
    """Constructs the real, backend-wired MainWindow -- exactly the
    object graph `main()` shows. Split out from `main()` so tests can
    drive the same window `power_intent_studio.py` actually launches
    (real widget clicks, real backend.api calls) without the blocking
    `QApplication.exec()` -- a QApplication must already exist (the test
    `qtbot`/`qapp` fixture provides one), this function doesn't create
    one itself."""
    state = BackendState()
    return MainWindow(
        elaborate_action=make_elaborate_action(state),
        load_power_intent_action=make_load_power_intent_action(state),
        mapping_action=make_mapping_action(state),
    )


def main() -> None:
    set_windows_app_user_model_id()
    app = QApplication(sys.argv)
    # The same look on every machine (see theme.apply_fixed_appearance):
    # Fusion + a fixed light palette, never the host's own style or
    # dark mode. BEFORE any widget is built, so none is ever created
    # with the host's look.
    apply_fixed_appearance(app)
    # App-level window icon (explicit request, cross-platform taskbar/
    # dock fix): some window managers read the application-level icon,
    # others the window-level one -- MainWindow sets its own too, so
    # both are covered. The logo asset already exists in the icon folder
    # (also used for the welcome page and title bar).
    app.setWindowIcon(QIcon(str(LOGO_PATH)))
    window = build_main_window()
    # Green accent chrome styling (explicit request -- purely cosmetic,
    # QSS only, see theme.py). Applied app-wide AFTER MainWindow is
    # built ON PURPOSE: a QApplication stylesheet that is already active
    # while a table is constructed makes Qt drop the per-table
    # `horizontalHeader().setFont(QFont(bold))` those tables do (Qt
    # merges a partially-resolved QFont differently under a stylesheet)
    # -- applying the stylesheet after the widgets already exist leaves
    # that bold intact, and every table created later (dialogs) is fine
    # too. Verified in test_theme.py.
    app.setStyleSheet(build_stylesheet())
    window.resize(1200, 800)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
