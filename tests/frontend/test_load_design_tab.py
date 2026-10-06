"""hierarchy_view/load_design_tab.py -- widget-level behavior that
doesn't need the full harness/MainWindow: button labels and the browse
dialogs' file-type filters (mocked, since a real QFileDialog can't run
headlessly and shouldn't be invoked just to check what filter string it
was given)."""

from PySide6.QtCore import QEventLoop
from PySide6.QtWidgets import QFileDialog, QMessageBox

from frontend.src.hierarchy_view.load_design_tab import LoadDesignTab, _make_item
from frontend.src.shared.widgets import message_box


def _ready_to_elaborate(tab: LoadDesignTab) -> None:
    """Satisfies both of _start_elaborate()'s pre-flight gates (a loaded
    file, a non-blank top module) so a test can reach real elaboration
    without hitting the QMessageBox popups -- see the dedicated tests for
    those two gates themselves."""
    tab.loaded_list.addItem(_make_item("rtl", "top.v"))
    tab.top_module_edit.setText("top")


def test_button_labels_are_add_and_elaborate_not_load(qtbot):
    tab = LoadDesignTab()
    qtbot.addWidget(tab)
    assert tab.load_button.text() == "Add"  # staging -> Loaded transfer button
    assert tab.elaborate_button.text() == "Elaborate"  # the actual elaboration action


def test_browse_rtl_files_only_offers_rtl_extensions(qtbot, monkeypatch):
    tab = LoadDesignTab()
    qtbot.addWidget(tab)
    captured = {}

    def fake_get_open_file_names(parent, caption, directory, filter):
        captured["filter"] = filter
        return [], ""

    monkeypatch.setattr(QFileDialog, "getOpenFileNames", staticmethod(fake_get_open_file_names))
    tab.browse_rtl_button.click()

    assert ".v" in captured["filter"]
    assert ".sv" in captured["filter"]
    assert ".vhd" in captured["filter"]
    assert ".vhdl" in captured["filter"]
    assert ".lib" not in captured["filter"]


def test_browse_lib_files_only_offers_lib_extension(qtbot, monkeypatch):
    tab = LoadDesignTab()
    qtbot.addWidget(tab)
    captured = {}

    def fake_get_open_file_names(parent, caption, directory, filter):
        captured["filter"] = filter
        return [], ""

    monkeypatch.setattr(QFileDialog, "getOpenFileNames", staticmethod(fake_get_open_file_names))
    tab.browse_lib_button.click()

    assert ".lib" in captured["filter"]
    assert ".v " not in captured["filter"] and not captured["filter"].strip().endswith(".v")


def test_remove_selected_on_loaded_list_is_separate_from_staging(qtbot):
    """The Loaded group box has its own "Remove Selected" button, acting
    on the Loaded list -- distinct from the Design Files group's button,
    which only ever acts on staging (confirmed by leaving an untouched
    staged item alongside the removed loaded one)."""
    tab = LoadDesignTab()
    qtbot.addWidget(tab)
    tab.loaded_list.addItem(_make_item("rtl", "top.v"))
    tab.loaded_list.addItem(_make_item("lib", "macros.lib"))
    tab.staging_list.addItem(_make_item("rtl", "still_staged.v"))

    tab.loaded_list.selectAll()
    tab.remove_loaded_button.click()

    assert tab.loaded_list.count() == 0
    assert tab.staging_list.count() == 1  # untouched


def test_continue_button_disabled_by_default_and_enabled_only_after_success(qtbot):
    tab = LoadDesignTab()
    qtbot.addWidget(tab)
    assert tab.continue_button.isEnabled() is False

    _ready_to_elaborate(tab)
    loop = QEventLoop()
    tab.designLoaded.connect(lambda d: loop.quit())
    tab._start_elaborate()
    loop.exec()

    assert tab.continue_button.isEnabled() is True


def test_elaborate_alone_never_emits_continue_requested(qtbot):
    """continueRequested is a distinct signal from designLoaded -- a
    successful Elaborate must never emit it by itself; only an explicit
    Continue click does."""
    tab = LoadDesignTab()
    qtbot.addWidget(tab)
    continue_events = []
    tab.continueRequested.connect(lambda: continue_events.append(True))

    _ready_to_elaborate(tab)
    loop = QEventLoop()
    tab.designLoaded.connect(lambda d: loop.quit())
    tab._start_elaborate()
    loop.exec()

    assert continue_events == []


# --- Elaborate's two pre-flight validation gates --------------------------


def test_elaborate_with_nothing_loaded_shows_an_error_and_does_not_proceed(qtbot, monkeypatch):
    tab = LoadDesignTab()
    qtbot.addWidget(tab)
    tab.top_module_edit.setText("top")  # only the Loaded list is empty
    assert tab.loaded_list.count() == 0

    shown = []
    monkeypatch.setattr(message_box, "critical", lambda *args, **kwargs: shown.append(args))
    fired = []
    tab.designLoaded.connect(lambda d: fired.append(d))

    tab._start_elaborate()

    assert shown
    assert fired == []


def test_elaborate_with_blank_top_module_shows_an_error_and_does_not_proceed(qtbot, monkeypatch):
    tab = LoadDesignTab()
    qtbot.addWidget(tab)
    tab.loaded_list.addItem(_make_item("rtl", "top.v"))  # only Top Module is blank
    assert tab.top_module_edit.text() == ""

    shown = []
    monkeypatch.setattr(message_box, "critical", lambda *args, **kwargs: shown.append(args))
    fired = []
    tab.designLoaded.connect(lambda d: fired.append(d))

    tab._start_elaborate()

    assert shown
    assert fired == []


def test_elaborate_checks_loaded_files_before_top_module(qtbot, monkeypatch):
    """Both fields empty -- only the "no design loaded" popup should
    fire, not both, confirming the loaded-files gate is checked first."""
    tab = LoadDesignTab()
    qtbot.addWidget(tab)

    titles = []
    monkeypatch.setattr(message_box, "critical", lambda parent, title, message: titles.append(title))

    tab._start_elaborate()

    assert titles == ["No Design Loaded"]


# --- "Load Design Diagnostic" dock -----------------------------------------


def test_diagnostics_dock_is_named_and_part_of_this_tab(qtbot):
    """Explicit request: a dedicated "Load Design Diagnostic" QDockWidget,
    part of this tab (this tab is a QMainWindow so it can host one)."""
    tab = LoadDesignTab()
    qtbot.addWidget(tab)

    assert tab.diagnostics_dock.windowTitle() == "Load Design Diagnostic"
    assert tab.diagnostics_dock.parent() is tab  # genuinely part of this tab, not some sibling/top-level dock


def test_diagnostics_dock_is_hidden_by_default(qtbot):
    tab = LoadDesignTab()
    qtbot.addWidget(tab)
    tab.show()

    assert tab.diagnostics_dock.isVisible() is False


def test_diagnostics_dock_auto_shows_when_elaborate_produces_warnings(qtbot):
    """Explicit request: "by default can be hidden but appears if
    elaboration have warnings or errors" -- the mock elaborate path's
    own diagnostics include warnings (see build_mock_rtl_diagnostics())."""
    tab = LoadDesignTab()
    qtbot.addWidget(tab)
    tab.show()
    _ready_to_elaborate(tab)

    loop = QEventLoop()
    tab.designLoaded.connect(lambda d: loop.quit())
    tab._start_elaborate()
    loop.exec()

    assert tab.diagnostics_dock.isVisible() is True
    assert tab.diagnostics_dock.model.rowCount() == 2
