"""power_intent_view/load_power_intent_tab.py -- widget-level behavior
that doesn't need the full harness/MainWindow, focused on this tab's own
"Power Intent Mapping Diagnostic" dock (see load_design_tab.py's own
analogous dock and tests for the shared pattern)."""

from PySide6.QtCore import QEventLoop
from PySide6.QtWidgets import QMessageBox

from frontend.src.power_intent_view import load_power_intent_tab as tab_module
from frontend.src.power_intent_view.load_power_intent_tab import LoadPowerIntentTab
from frontend.src.shared.mock_backend import build_mock_design


def _ready_to_map(tab: LoadPowerIntentTab) -> None:
    tab.set_rtl(build_mock_design())
    tab.files_list.addItem("soc_top.upf")


def _accept_acknowledgment(monkeypatch, answer=QMessageBox.StandardButton.Yes) -> list:
    """Answers this tab's confirmation dialogs without executing them.

    A real message_box.question() blocks on a modal that never gets a
    click under the offscreen platform, hanging the whole run -- the same
    hazard already documented for AddPowerSupplyDialog.exec(). Returns
    the list of (title, text) pairs asked, so a test can assert the
    dialog appeared at all rather than just that it didn't block."""
    asked: list = []

    def fake_question(parent, title, text, *args, **kwargs):
        asked.append((title, text))
        return answer

    monkeypatch.setattr(tab_module.message_box, "question", fake_question)
    return asked


def test_diagnostics_dock_is_named_and_part_of_this_tab(qtbot):
    """Explicit request: a dedicated "Power Intent Mapping Diagnostic"
    QDockWidget, part of this tab (this tab is a QMainWindow so it can
    host one) -- covers both phases this tab runs (load, then mapping),
    not just the mapping step alone."""
    tab = LoadPowerIntentTab()
    qtbot.addWidget(tab)

    assert tab.diagnostics_dock.windowTitle() == "Power Intent Mapping Diagnostic"
    assert tab.diagnostics_dock.parent() is tab


def test_diagnostics_dock_is_hidden_by_default(qtbot):
    tab = LoadPowerIntentTab()
    qtbot.addWidget(tab)
    tab.show()

    assert tab.diagnostics_dock.isVisible() is False


def test_diagnostics_dock_auto_shows_when_mapping_produces_warnings(qtbot):
    """Explicit request: "by default can be hidden but appears if...
    mapping have warnings or errors" -- the mock pipeline's own load +
    mapping diagnostics include warnings (build_mock_power_intent_diagnostics()
    plus build_mock_validation_report()'s own findings)."""
    tab = LoadPowerIntentTab()
    qtbot.addWidget(tab)
    tab.show()
    _ready_to_map(tab)

    loop = QEventLoop()
    tab.mappingCompleted.connect(lambda mapped: loop.quit())
    tab._start_mapping()
    loop.exec()

    assert tab.diagnostics_dock.isVisible() is True
    assert tab.diagnostics_dock.model.rowCount() > 0


def test_diagnostics_dock_covers_load_mapping_and_import_diagnostics(qtbot):
    """Three phases now, not two: the import (translating the elaborated
    power intent into diagram state) runs after mapping and contributes
    its own findings under its own source key, so neither batch erases
    another's -- DiagnosticsTableModel replaces per source."""
    tab = LoadPowerIntentTab()
    qtbot.addWidget(tab)
    _ready_to_map(tab)

    loop = QEventLoop()
    tab.mappingCompleted.connect(lambda mapped: loop.quit())
    tab._start_mapping()
    loop.exec()

    sources = {d.source for d in tab.diagnostics_dock.model._visible()}
    assert sources == {"load_power_intent", "mapping", "power_intent_import"}


def test_create_new_does_not_touch_the_diagnostics_dock(qtbot):
    """"Create New" never runs load or mapping at all (see class
    docstring) -- its own diagnostics dock should stay untouched, empty
    and hidden."""
    tab = LoadPowerIntentTab()
    qtbot.addWidget(tab)
    tab.show()

    tab.create_button.click()

    assert tab.diagnostics_dock.isVisible() is False
    assert tab.diagnostics_dock.model.rowCount() == 0


# --- continue_button: stays put on warnings/errors, auto-navigates when clean --


def test_continue_button_disabled_by_default(qtbot):
    tab = LoadPowerIntentTab()
    qtbot.addWidget(tab)
    assert tab.continue_button.isEnabled() is False


def test_mapping_with_warnings_stays_put_and_enables_continue_button(qtbot, monkeypatch):
    """Real bug, found via live use: unconditionally auto-navigating on
    Mapping success disabled and switched away from this tab in the
    same tick a warning/error had just made its own diagnostic dock
    auto-show -- making it instantly inaccessible, not even reachable
    again via the View menu afterward. The mock pipeline's own
    diagnostics always include warnings, so continueRequested must NOT
    fire automatically here."""
    tab = LoadPowerIntentTab()
    qtbot.addWidget(tab)
    _ready_to_map(tab)
    fired = []
    tab.continueRequested.connect(lambda: fired.append(1))

    loop = QEventLoop()
    tab.mappingCompleted.connect(lambda mapped: loop.quit())
    tab._start_mapping()
    loop.exec()

    assert fired == []  # did not auto-navigate
    assert tab.continue_button.isEnabled() is True

    # Clicking Continue with findings present now asks for an explicit
    # acknowledgment first (spec section 4) -- a real modal, which hangs
    # a headless run, so it is patched rather than executed.
    _accept_acknowledgment(monkeypatch)
    tab.continue_button.click()
    assert fired == [1]


def test_a_clean_mapping_still_auto_navigates_immediately(qtbot):
    """Same fast path as always when there's nothing to see -- exercised
    via a real load_action/mapping_action pair (the validation harness's
    own hook shape) returning zero diagnostics, since the mock pipeline
    always has warnings baked in.

    The power intent is EMPTY rather than build_mock_power_intent()'s
    own: that fixture deliberately contains a dangling element and
    constructs this tool does not model, so the import phase (a real
    third phase now, whose findings count toward this gate exactly like
    load's and mapping's) legitimately reports them and the run is no
    longer clean. An empty design is the honest way to express "nothing
    to see" now that every phase is actually inspected."""
    from frontend.src.shared.models.mapping import MappedDesign
    from frontend.src.shared.models.power_intent import PowerIntentDesign, SourceFormat

    rtl = build_mock_design()
    power_intent = PowerIntentDesign(source_format=SourceFormat.UPF, source_version="3.1")
    mapped = MappedDesign(power_intent=power_intent, rtl=rtl)

    tab = LoadPowerIntentTab(load_action=lambda entry, fmt: (power_intent, []), mapping_action=lambda: (mapped, []))
    qtbot.addWidget(tab)
    _ready_to_map(tab)
    fired = []
    tab.continueRequested.connect(lambda: fired.append(1))

    # A real load_action/mapping_action pair runs *synchronously* (see
    # class docstring) -- mappingCompleted/continueRequested have both
    # already fired by the time _start_mapping() returns, unlike the
    # mock (timer-driven) path other tests in this file wait on via
    # QEventLoop. Waiting on a QEventLoop here would block forever on a
    # signal that already fired before the loop ever started running.
    tab._start_mapping()

    assert fired == [1]  # auto-navigated, no manual step needed
    assert tab.continue_button.isEnabled() is False
