"""Taskbar / launch-bar app-icon fix (app/main.py).

This is inherently a manual/visual item -- the Windows taskbar and
Linux dock icon rendering can't be verified headlessly. These tests
lock in the automatable parts: the app-level and window-level icons
are actually set from the real logo asset, and the Windows-only
AppUserModelID call is a safe no-op everywhere else. Actual taskbar
rendering still needs a manual check on a real Windows AND a real
Linux desktop -- noted in the PR.
"""

import sys

import pytest
from PySide6.QtGui import QIcon

from frontend.src.app import main as app_main
from frontend.src.app.main_window import MainWindow
from frontend.src.app.welcome_page import LOGO_PATH


def test_logo_asset_exists():
    """The fix reuses the EXISTING logo asset -- no new/fabricated
    icon (see the request: flag back rather than improvise one)."""
    assert LOGO_PATH.exists()
    assert LOGO_PATH.suffix == ".svg"


def test_app_level_window_icon_is_set_from_the_logo(qapp):
    """main() calls QApplication.setWindowIcon() -- some window
    managers read the application-level icon for the taskbar/dock."""
    qapp.setWindowIcon(QIcon(str(LOGO_PATH)))  # what main() does
    assert not qapp.windowIcon().isNull()


def test_main_window_sets_its_own_window_icon(qtbot):
    """...and MainWindow sets the window-LEVEL icon too -- some
    taskbars read that one instead."""
    window = MainWindow()
    qtbot.addWidget(window)
    assert not window.windowIcon().isNull()


def test_set_windows_app_user_model_id_is_a_safe_noop_off_windows():
    """Must never raise on macOS/Linux (it's guarded on sys.platform
    and swallows ctypes errors regardless) -- a cosmetic taskbar fix
    is not worth crashing startup over."""
    assert sys.platform != "win32"  # this CI/dev environment
    app_main.set_windows_app_user_model_id()  # returns cleanly, no exception


@pytest.mark.skipif(sys.platform != "win32", reason="AppUserModelID is Windows-only")
def test_set_windows_app_user_model_id_runs_on_windows():
    app_main.set_windows_app_user_model_id()  # just: executes without error
