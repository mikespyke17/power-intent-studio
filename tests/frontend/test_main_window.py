"""app/main_window.py -- end-to-end tab enablement and wiring, against
mock/fixture data only, driven through the real mocked-progress buttons
(not by calling internal _finish_* methods directly).

Tab order: 0 Load Design, 1 Load Power Intent, 2 Power Intent --
"Visualize Design" is hidden (explicit request, not deleted -- see
main_window.py's own docstring: `window.visualize_design_tab` still
exists and is still kept fed with data, just never added to
`window.tabs`, so it's untestable-via-the-tab-bar but still directly
reachable as an attribute for tests that want it).

Every test past the dedicated "welcome page"/"Create Project" ones below
calls `_create_project()` first -- explicit request: the real app now
always starts on `WelcomePage`, and `self.tabs` only becomes reachable
(and, critically, visible -- QWidget.isVisible() requires the *whole*
ancestor chain to be shown, not just the widget itself) once "File >
Create Project" succeeds."""

from PySide6.QtCore import QEventLoop
from PySide6.QtGui import QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog, QFileDialog, QInputDialog, QMessageBox

from frontend.src.app import main_window as main_window_module
from frontend.src.app.main_window import MainWindow
from frontend.src.app.welcome_page import WelcomePage
from frontend.src.hierarchy_view.load_design_tab import _make_item
from frontend.src.power_intent_view.supply_port_overlay import SupplyPortPlacement
from frontend.src.shared.widgets import message_box


def _run_until(signal, timeout_ms=2000):
    loop = QEventLoop()
    signal.connect(loop.quit)
    QTest.qWait(0)
    loop.exec()


def _ready_to_elaborate(load_design_tab) -> None:
    """Satisfies _start_elaborate()'s two pre-flight gates (a loaded
    file, a non-blank top module) -- see test_load_design_tab.py's own
    dedicated tests for those gates themselves."""
    load_design_tab.loaded_list.addItem(_make_item("rtl", "top.v"))
    load_design_tab.top_module_edit.setText("top")


def _create_project(window, monkeypatch, name="Test Project") -> None:
    """Drives "File > Create Project" through the same QAction a real
    click would trigger -- QInputDialog.getText() is mocked (a native
    modal can't be driven headlessly), matching the file's own "driven
    through the real... buttons, not internal methods" discipline as
    closely as a blocking native dialog allows."""
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: (name, True)))
    window.create_project_action.trigger()


def _continue_past_power_intent_findings(window, monkeypatch) -> None:
    """Clicks 'Load Power Intent's own Continue, answering the
    acknowledgment dialog it now shows.

    Continue is only ever enabled when the run produced a warning or
    error, and spec section 4 requires an explicit acknowledgment at
    exactly that point -- a real modal, which hangs a headless run, so
    it is answered by patching rather than executed."""
    from frontend.src.power_intent_view import load_power_intent_tab as load_pi_module

    monkeypatch.setattr(
        load_pi_module.message_box, "question",
        lambda *a, **k: QMessageBox.StandardButton.Yes,
    )
    window.load_power_intent_tab.continue_button.click()


# --- welcome page / Create Project -------------------------------------------


def test_window_opens_on_the_welcome_page_with_no_view_menu_yet(qtbot):
    """Explicit request: "View" isn't merely disabled before a project
    exists, it isn't in the menu bar at all."""
    window = MainWindow()
    qtbot.addWidget(window)

    assert isinstance(window.welcome_page, WelcomePage)
    assert window._central_stack.currentWidget() is window.welcome_page
    assert [a.text() for a in window.menuBar().actions()] == ["File"]
    assert window.create_project_action.isEnabled() is True
    assert window.windowIcon().isNull() is False
    assert window.windowTitle() == "Power Intent Studio"


def test_create_project_with_a_blank_name_or_cancel_does_nothing(qtbot, monkeypatch):
    window = MainWindow()
    qtbot.addWidget(window)

    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("", False)))  # Cancel
    window.create_project_action.trigger()
    assert window._central_stack.currentWidget() is window.welcome_page

    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("   ", True)))  # blank, OK'd
    window.create_project_action.trigger()
    assert window._central_stack.currentWidget() is window.welcome_page
    assert [a.text() for a in window.menuBar().actions()] == ["File"]
    assert window.create_project_action.isEnabled() is True


def test_create_project_switches_to_tabs_lands_on_load_design_and_enables_view(qtbot, monkeypatch):
    window = MainWindow()
    qtbot.addWidget(window)

    _create_project(window, monkeypatch, name="My SoC")

    assert window._central_stack.currentWidget() is window.tabs
    assert window.tabs.currentIndex() == 0  # Load Design
    assert [a.text() for a in window.menuBar().actions()] == ["File", "View", "Extract", "Guide"]
    # REVERSED (explicit request): "Create Project" stays reachable while
    # a project is open. It used to disable itself, matching this
    # window's never-go-back navigation; now creating over an active
    # project asks whether to save first -- see
    # test_create_project_over_an_active_project_* below.
    assert window.create_project_action.isEnabled() is True
    assert window.windowTitle() == "Power Intent Studio — My SoC"


# --- everything below assumes a project already exists -----------------------


def test_tabs_start_disabled_except_load_design(qtbot, monkeypatch):
    window = MainWindow()
    qtbot.addWidget(window)
    _create_project(window, monkeypatch)
    assert [window.tabs.isTabEnabled(i) for i in range(4)] == [True, False, False, False]


def test_visualize_design_is_hidden_but_still_constructed_and_kept_fed(qtbot, monkeypatch):
    """Explicit request: "get rid of" the tab but "keep the code in case
    I want to restore it in future" -- the tab object itself still
    exists and is still fed real data, just never added to window.tabs
    (so it's neither visible nor selectable, and "Visualize Design"
    doesn't appear anywhere in "View" either -- see
    test_view_menu_toggles_each_docks_visibility)."""
    window = MainWindow()
    qtbot.addWidget(window)
    _create_project(window, monkeypatch)

    assert window.tabs.indexOf(window.visualize_design_tab) == -1
    assert [window.tabs.tabText(i) for i in range(window.tabs.count())] == [
        "Load Design",
        "Load Power Intent",
        "Power Intent",
        "Power State Table",
    ]

    _ready_to_elaborate(window.load_design_tab)
    window.load_design_tab._start_elaborate()
    _run_until(window.load_design_tab.designLoaded)

    assert len(window.visualize_design_tab.tree._items_by_path) > 0  # still kept fed, per the explicit request


def test_full_flow_enables_tabs_and_populates_diagram(qtbot, monkeypatch):
    window = MainWindow()
    qtbot.addWidget(window)
    _create_project(window, monkeypatch)

    _ready_to_elaborate(window.load_design_tab)
    window.load_design_tab._start_elaborate()
    _run_until(window.load_design_tab.designLoaded)
    assert window.tabs.isTabEnabled(1) is True  # Load Power Intent
    assert window.tabs.isTabEnabled(2) is False  # power intent not loaded yet
    assert window.tabs.currentIndex() == 0  # stays on Load Design -- no auto-navigate
    assert window.tabs.isTabEnabled(0) is True  # not disabled yet either
    assert window.load_design_tab.continue_button.isEnabled() is True

    window.load_design_tab.continue_button.click()
    assert window.tabs.currentIndex() == 1  # now jumps to Load Power Intent
    assert window.tabs.isTabEnabled(0) is False  # Load Design disabled, one-shot flow

    window.load_power_intent_tab.files_list.addItem("soc_top.upf")  # Mapping requires at least one loaded file
    window.load_power_intent_tab.mapping_button.click()
    _run_until(window.load_power_intent_tab.mappingCompleted)
    assert window.tabs.isTabEnabled(2) is True  # Power Intent tab
    assert window.tabs.isTabEnabled(3) is True  # Power State Table -- same condition as Power Intent
    # The mock pipeline's own diagnostics include warnings -- stays on
    # Load Power Intent (its own diagnostic dock now visible) rather
    # than auto-navigating away, same "see the warnings first" gate
    # LoadDesignTab's own Continue button already established.
    assert window.tabs.currentIndex() == 1
    assert window.load_power_intent_tab.diagnostics_dock.model.rowCount() > 0
    assert window.load_power_intent_tab.continue_button.isEnabled() is True

    _continue_past_power_intent_findings(window, monkeypatch)
    assert window.tabs.currentIndex() == 2  # now jumps to Power Intent
    # REVISED (spec section 7): Load Power Intent STAYS enabled so
    # another file can be loaded later -- a deliberate exception to this
    # window's otherwise one-shot navigation. What protects the diagram
    # edits a re-load would discard is the tab's own dirty-flag
    # confirmation, not disabling the tab.
    assert window.tabs.isTabEnabled(1) is True
    assert window.power_intent_tab.diagram._stack.currentWidget() is window.power_intent_tab.diagram._tabs
    assert len(window.power_intent_tab.diagram._index) > 0


def test_mapping_with_no_files_loaded_shows_an_error_and_does_not_proceed(qtbot, monkeypatch):
    window = MainWindow()
    qtbot.addWidget(window)
    _create_project(window, monkeypatch)
    _ready_to_elaborate(window.load_design_tab)
    window.load_design_tab._start_elaborate()
    _run_until(window.load_design_tab.designLoaded)
    window.load_design_tab.continue_button.click()
    assert window.load_power_intent_tab.files_list.count() == 0

    shown = []
    monkeypatch.setattr(message_box, "critical", lambda *args, **kwargs: shown.append(args))
    fired = []
    window.load_power_intent_tab.powerIntentLoaded.connect(lambda pi: fired.append(pi))

    window.load_power_intent_tab.mapping_button.click()

    assert shown  # the error dialog was shown
    assert fired == []  # nothing was loaded/mapped
    assert window.tabs.currentIndex() == 1  # no navigation happened
    assert window.tabs.isTabEnabled(1) is True  # tab stays enabled/usable


def test_create_new_power_intent_also_navigates_and_disables_the_tab(qtbot, monkeypatch):
    """"Create New" is a separate success path from "Mapping" but shares
    the same continueRequested signal -- confirms it drives the exact
    same navigate+disable behavior, without going through Mapping at
    all, and without any dialog (removed on explicit request)."""
    window = MainWindow()
    qtbot.addWidget(window)
    _create_project(window, monkeypatch)

    _ready_to_elaborate(window.load_design_tab)
    window.load_design_tab._start_elaborate()
    _run_until(window.load_design_tab.designLoaded)
    window.load_design_tab.continue_button.click()
    assert window.tabs.currentIndex() == 1

    fired = []
    window.load_power_intent_tab.powerIntentLoaded.connect(lambda pi: fired.append(pi))
    window.load_power_intent_tab.create_button.click()

    assert fired  # a PowerIntentDesign was created
    assert window.tabs.currentIndex() == 2  # Power Intent
    # Stays enabled (spec section 7) -- see the equivalent note in
    # test_full_flow_enables_tabs_and_populates_diagram.
    assert window.tabs.isTabEnabled(1) is True
    assert window.tabs.isTabEnabled(2) is True  # Power Intent tab enabled too
    assert window.tabs.isTabEnabled(3) is True  # Power State Table -- same condition as Power Intent


def test_create_new_also_reveals_the_power_intent_tabs_own_diagram(qtbot, monkeypatch):
    """Distinct from Mapping's own reaction: Create New never produces a
    MappedDesign (no mapping step runs at all), but the Power Intent
    tab's Diagram must still switch away from its "Run Mapping..."
    placeholder -- otherwise it would stay stuck there forever on this
    path, since no Mapping click is ever coming."""
    window = MainWindow()
    qtbot.addWidget(window)
    _create_project(window, monkeypatch)

    _ready_to_elaborate(window.load_design_tab)
    window.load_design_tab._start_elaborate()
    _run_until(window.load_design_tab.designLoaded)
    window.load_design_tab.continue_button.click()
    assert window.power_intent_tab.diagram._stack.currentWidget() is window.power_intent_tab.diagram._placeholder

    window.load_power_intent_tab.create_button.click()

    assert window.power_intent_tab.diagram._stack.currentWidget() is window.power_intent_tab.diagram._tabs


def test_each_tabs_own_diagnostics_dock_is_populated_independently(qtbot, monkeypatch):
    """Explicit request: no more single MainWindow-level dock switched
    by active tab -- "Load Design Diagnostic" and "Power Intent Mapping
    Diagnostic" are separate docks, each embedded in (and permanently
    scoped to) its own owning tab."""
    window = MainWindow()
    qtbot.addWidget(window)
    _create_project(window, monkeypatch)

    _ready_to_elaborate(window.load_design_tab)
    window.load_design_tab._start_elaborate()
    _run_until(window.load_design_tab.designLoaded)
    assert window.load_design_tab.diagnostics_dock.model.rowCount() == 2  # load_design's own 2 mock diagnostics
    assert window.load_power_intent_tab.diagnostics_dock.model.rowCount() == 0  # untouched so far

    window.tabs.setCurrentIndex(1)
    window.load_power_intent_tab.files_list.addItem("soc_top.upf")
    window.load_power_intent_tab.mapping_button.click()
    _run_until(window.load_power_intent_tab.mappingCompleted)

    assert window.load_power_intent_tab.diagnostics_dock.model.rowCount() > 0  # load diagnostics, plus mapping's own
    assert window.load_design_tab.diagnostics_dock.model.rowCount() == 2  # unaffected by the other tab's own dock


def test_load_design_diagnostic_dock_is_hidden_by_default_and_auto_shows_on_warnings(qtbot, monkeypatch):
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    _create_project(window, monkeypatch)
    assert window.load_design_tab.diagnostics_dock.isVisible() is False

    _ready_to_elaborate(window.load_design_tab)
    window.load_design_tab._start_elaborate()
    _run_until(window.load_design_tab.designLoaded)

    assert window.load_design_tab.diagnostics_dock.isVisible() is True  # mock diagnostics include warnings


def test_view_menu_toggles_each_docks_visibility(qtbot, monkeypatch):
    # Deliberately reads window.view_menu/.file_menu directly (kept as
    # persistent attributes -- see MainWindow._build_menu_bar()'s own
    # docstring) rather than rediscovering the "View" QMenu via
    # menu_bar.actions()[...].menu() -- a real PySide6/shiboken gotcha,
    # confirmed via a live repro: that specific rediscovery path raises
    # "Internal C++ object already deleted" on the exact same (`is`-
    # identical) QMenu object a direct attribute read has no trouble
    # with at all, immediately before or after.
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()

    # window.view_menu (a plain, persistent Python attribute -- see
    # _build_menu_bar()'s own docstring) and its own child actions are
    # fully built and inspectable even before a project exists and the
    # menu itself is ever inserted into the menu bar (see
    # test_window_opens_on_the_welcome_page_own docstring) -- only
    # *insertion* is gated on Create Project, not construction.
    view_menu = window.view_menu
    # Each group is a disabled header action (naming the owning tab --
    # not QMenu.addSection(), whose own title text turned out not to
    # render at all in this window's non-native menu style, confirmed
    # via a live screenshot -- see _add_view_menu_group()'s own
    # docstring) followed by that tab's own dock-toggle action(s),
    # separated from the next group by a real separator. The toggle
    # actions start matching this fresh window's own initial tab
    # enablement (only "Load Design" itself starts enabled) -- see the
    # dedicated test below for how they track tab enablement changing.
    non_separator_actions = [a for a in view_menu.actions() if not a.isSeparator()]
    assert [(a.text(), a.isEnabled()) for a in non_separator_actions] == [
        ("Load Design", False),
        ("Load Design Diagnostic", True),
        ("Load Power Intent", False),
        ("Power Intent Mapping Diagnostic", False),
        ("Power Intent", False),
        ("Hierarchy Tree", False),
        ("Properties", False),
    ]
    separator_count = sum(1 for a in view_menu.actions() if a.isSeparator())
    assert separator_count == 2  # between the 3 groups, none leading -- no "Visualize Design" group, it's hidden

    # "Load Design Diagnostic" is enabled from the start (tab 0 starts
    # enabled) -- toggling it is independent of the enablement check
    # above. Needs a project to actually exist first, though: the dock
    # lives inside self.tabs, and QWidget.isVisible() requires the
    # *whole* ancestor chain to be shown, not just the dock itself.
    _create_project(window, monkeypatch)
    load_design_diag_action = next(a for a in view_menu.actions() if a.text() == "Load Design Diagnostic")
    assert window.load_design_tab.diagnostics_dock.isVisible() is False
    load_design_diag_action.trigger()
    assert window.load_design_tab.diagnostics_dock.isVisible() is True


def test_selecting_a_dock_from_the_view_menu_raises_it_if_tabified(qtbot, monkeypatch):
    """Explicit request: choosing a dock that's off (tabified behind a
    sibling and not the currently-active tab in that group) from the
    View menu should bring it to the front, not just flip a visibility
    flag that leaves it invisibly buried behind whichever tab was
    already active. Uses Power Intent (not the now-hidden Visualize
    Design) -- same underlying _add_view_menu_group() mechanism, exercised
    against a tab that's actually reachable via window.tabs."""
    window = MainWindow()
    qtbot.addWidget(window)
    window.resize(1200, 800)
    window.show()
    _create_project(window, monkeypatch)
    window.tabs.setCurrentWidget(window.power_intent_tab)
    tab = window.power_intent_tab
    tab.show()
    tab.tabifyDockWidget(tab.tree_dock, tab.properties_dock)
    QTest.qWait(0)
    assert tab.properties_dock.visibleRegion().isEmpty() is False  # front tab after tabifying
    assert tab.tree_dock.visibleRegion().isEmpty() is True  # buried behind it

    # tree_dock is visible-by-default, so its own toggleViewAction() is
    # already checked=True even while buried behind properties_dock --
    # tabifying only changes which dock is the *front* tab, not either
    # one's own "shown" bookkeeping. setChecked(True) on an
    # already-checked action is a documented Qt no-op (no toggled signal
    # fires, so raise_() never would either) -- force a real transition
    # the same way a user re-selecting it from the View menu would: off,
    # then on. The same QAction object toggleViewAction() itself is --
    # also the one wired into "View" via _add_view_menu_group(), see
    # main_window.py's own module docstring.
    tab.tree_dock.toggleViewAction().setChecked(False)
    tab.tree_dock.toggleViewAction().setChecked(True)
    QTest.qWait(0)

    assert tab.tree_dock.visibleRegion().isEmpty() is False  # raised to the front
    assert tab.properties_dock.visibleRegion().isEmpty() is True  # pushed behind it instead


def _view_menu_action_states(window) -> dict[int, list[bool]]:
    return {index: [a.isEnabled() for a in actions] for index, actions in window._view_menu_actions_by_tab_index.items()}


def test_view_menu_actions_are_only_enabled_while_their_own_tab_is(qtbot, monkeypatch):
    """Explicit request: a View menu group's own dock-toggle action(s)
    should only be available once the corresponding tab is enabled --
    exercised through the full flow, checking after every tab-enablement
    change (both "enable" and the later one-shot "disable")."""
    window = MainWindow()
    qtbot.addWidget(window)
    _create_project(window, monkeypatch)

    assert _view_menu_action_states(window) == {0: [True], 1: [False], 2: [False, False]}

    _ready_to_elaborate(window.load_design_tab)
    window.load_design_tab._start_elaborate()
    _run_until(window.load_design_tab.designLoaded)
    assert _view_menu_action_states(window) == {0: [True], 1: [True], 2: [False, False]}  # Power Intent not loaded yet

    window.load_design_tab.continue_button.click()
    assert _view_menu_action_states(window) == {0: [False], 1: [True], 2: [False, False]}

    window.load_power_intent_tab.files_list.addItem("soc_top.upf")
    window.load_power_intent_tab.mapping_button.click()
    _run_until(window.load_power_intent_tab.mappingCompleted)
    assert _view_menu_action_states(window) == {0: [False], 1: [True], 2: [True, True]}

    _continue_past_power_intent_findings(window, monkeypatch)
    # Load Power Intent's own View group stays enabled -- the tab itself
    # now stays enabled after a successful mapping (spec section 7).
    assert _view_menu_action_states(window) == {0: [False], 1: [True], 2: [True, True]}


# --- Extract > Extract Power Intent ------------------------------------


def test_extract_menu_is_not_in_the_menu_bar_before_a_project_exists(qtbot):
    """Same deferred-insertion discipline as "View" -- nothing to extract
    before a project (and its Power Intent tab) exists."""
    window = MainWindow()
    qtbot.addWidget(window)

    assert [a.text() for a in window.menuBar().actions()] == ["File"]
    # "Extract Power Intent" is no longer under "File" at all (moved to
    # the new top-level "Extract" menu, explicit request).
    assert window.extract_power_intent_menu not in [a.menu() for a in window.file_menu.actions()]


def test_extract_menu_appears_after_create_project_with_extract_power_intent_and_extract_diagram(qtbot, monkeypatch):
    window = MainWindow()
    qtbot.addWidget(window)
    _create_project(window, monkeypatch)

    # "Extract" is now its own top-level menu (File | View | Extract | Guide).
    assert "Extract" in [a.text() for a in window.menuBar().actions()]
    assert window.extract_power_intent_menu in [a.menu() for a in window.extract_menu.actions()]
    assert "Extract Diagram" in [a.text() for a in window.extract_menu.actions()]
    # A separator sits between "Extract Power Intent" and "Extract Diagram".
    texts = [("sep" if a.isSeparator() else a.text()) for a in window.extract_menu.actions()]
    assert texts == ["Extract Power Intent", "sep", "Extract Diagram"]
    # UPF 2.1/CPF 1.1 removed as generation targets (explicit request) --
    # every other version stays.
    assert {a.text() for a in window.extract_upf_menu.actions()} == {"3.0", "3.1"}
    assert {a.text() for a in window.extract_cpf_menu.actions()} == {"2.0", "2.1"}
    assert "2.1" not in {a.text() for a in window.extract_upf_menu.actions()}
    assert "1.1" not in {a.text() for a in window.extract_cpf_menu.actions()}


def test_extract_power_intent_writes_the_generated_text_to_the_chosen_path(qtbot, monkeypatch, tmp_path):
    window = MainWindow()
    qtbot.addWidget(window)
    _create_project(window, monkeypatch)
    diagram = window.power_intent_tab.diagram
    diagram.shared_supply_state.supply_port_placements["p1"] = SupplyPortPlacement(
        id="p1", owner_path="top", name="VDD", color=QColor("red")
    )

    out_file = tmp_path / "out.upf"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(out_file), "UPF Files (*.upf)")))
    window._on_extract_power_intent("UPF", "3.1")

    text = out_file.read_text()
    assert "create_supply_port VDD" in text
    # A boxed header banner (tool name, copyright, Design/Format/
    # Generated/Generator fields -- section 7, explicit request) is
    # always present at the top of every export.
    assert "Format         : UPF 3.1" in text
    assert "#  Copyright (c)" in text
    # No RTL design has been loaded in this test -- the header's own
    # "Design" field falls back to its documented placeholder rather
    # than fabricating a name.
    assert "Design         : Unknown" in text


def test_extract_power_intent_passes_every_state_family(qtbot, monkeypatch, tmp_path):
    """The real menu action must hand the generator EVERY state family
    it accepts.

    Four of them were silently missing for a long time -- Isolation,
    Port Attributes, the whole Power State Table, and the top-level
    ports the Port Attributes section needs to resolve a direction.
    Every test covering those sections called generate_upf() DIRECTLY
    and passed the state itself, so all of them passed while the app's
    own "Extract Power Intent" wrote a file with those sections absent
    entirely. Reported by using the app: "I don see power states for
    supply sets and top level power domain (PST)."

    This test exists specifically to close that gap class, so it drives
    the real action rather than the generator: a test that bypasses this
    call site cannot see a missing argument at it.
    """
    from frontend.src.power_intent_view.isolation_overlay import IsolationStrategyPlacement
    from frontend.src.power_intent_view.power_domain_overlay import (
        PowerDomainPlacement,
        SupplySetPlacement,
    )
    from frontend.src.power_intent_view.power_state_table import PowerStateTableRow
    from frontend.src.power_intent_view.supply_port_overlay import SupplyPortState
    from frontend.src.shared.mock_backend import build_mock_design

    window = MainWindow()
    qtbot.addWidget(window)
    _create_project(window, monkeypatch)
    window.power_intent_tab.set_rtl(build_mock_design())

    diagram = window.power_intent_tab.diagram
    red = QColor("red")
    diagram.shared_supply_state.supply_port_placements.update({
        "p1": SupplyPortPlacement(id="p1", owner_path="top", name="VDD", color=red,
                                  states=[SupplyPortState("FULL_ON", "1.2"), SupplyPortState("OFF")]),
        "p2": SupplyPortPlacement(id="p2", owner_path="top", name="VSS", color=red,
                                  states=[SupplyPortState("FULL_ON", "0.0")]),
    })
    diagram.shared_domain_state.supply_sets["ss"] = SupplySetPlacement(
        id="ss", name="SS_VDD", power="VDD", ground="VSS", owner_path="top"
    )
    diagram.shared_domain_state.domains["d"] = PowerDomainPlacement(
        id="d", owner_path="top", name="PD_TOP", color=red, primary_supply_set="ss"
    )
    diagram.shared_isolation_state.strategies["iso"] = IsolationStrategyPlacement(
        id="iso", name="ISO_A", power_domain_id="d", isolation_supply_set_id="ss",
        clamp_value="low", isolation_control="iso_en", isolation_sense="high",
        elements=["top/out1"],
    )
    diagram.shared_port_attribute_state.assignments["clk"] = "ss"
    window.power_state_table_tab.state.rows["r1"] = PowerStateTableRow(
        id="r1", power_domain_id="d", state_name="ON",
        net_states={"p1": "FULL_ON 1.2", "p2": "FULL_ON 0.0"},
    )

    out_file = tmp_path / "out.upf"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(out_file), "UPF Files (*.upf)")))
    window._on_extract_power_intent("UPF", "3.1")
    text = out_file.read_text()

    assert "add_power_state -supply SS_VDD" in text      # Tier 1, was missing
    assert "create_power_state_group" in text            # Tier 2 (the PST), was missing
    assert "set_isolation ISO_A" in text                 # was missing
    assert "set_port_attributes" in text                 # needs top_level_ports too, was missing


def test_extract_power_intent_header_names_the_loaded_designs_own_top_module(qtbot, monkeypatch, tmp_path):
    """section 7 (explicit request): the header's own "Design" field
    names whatever RTL design is actually loaded -- resolved from
    PowerIntentTab.properties.design_properties._design (set by
    set_rtl() -> load_design()), not fabricated."""
    from frontend.src.shared.mock_backend import build_mock_design

    window = MainWindow()
    qtbot.addWidget(window)
    _create_project(window, monkeypatch)
    window.power_intent_tab.set_rtl(build_mock_design())  # base_name "top", see build_mock_design()'s own fixture

    out_file = tmp_path / "out.upf"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(out_file), "UPF Files (*.upf)")))
    window._on_extract_power_intent("UPF", "3.1")

    text = out_file.read_text()
    assert "Design         : top" in text


def test_extract_power_intent_cancelled_dialog_writes_nothing(qtbot, monkeypatch, tmp_path):
    window = MainWindow()
    qtbot.addWidget(window)
    _create_project(window, monkeypatch)

    out_file = tmp_path / "should_not_exist.cpf"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: ("", "")))
    window._on_extract_power_intent("CPF", "2.0")

    assert out_file.exists() is False


# --- Small fix 2: default export filename -------------------------------


def test_extract_power_intent_default_filename_is_topmodule_power_intent(qtbot, monkeypatch, tmp_path):
    from frontend.src.shared.mock_backend import build_mock_design

    window = MainWindow()
    qtbot.addWidget(window)
    _create_project(window, monkeypatch)
    window.power_intent_tab.set_rtl(build_mock_design())  # top module base_name "top"

    captured = []

    def _fake_save(parent, title, default, filt):
        captured.append(default)
        return ("", "")

    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(_fake_save))
    window._on_extract_power_intent("UPF", "3.1")
    window._on_extract_power_intent("CPF", "2.0")
    assert captured == ["top_power_intent.upf", "top_power_intent.cpf"]


# --- Menu bar restructuring: File | View | Extract | Guide -------------


def test_menu_bar_structure_matches_the_spec(qtbot, monkeypatch):
    window = MainWindow()
    qtbot.addWidget(window)
    _create_project(window, monkeypatch)

    assert [a.text() for a in window.menuBar().actions()] == ["File", "View", "Extract", "Guide"]

    file_items = [("sep" if a.isSeparator() else a.text()) for a in window.file_menu.actions()]
    assert file_items == ["Create Project", "sep", "Save Project", "Load Project"]

    extract_items = [("sep" if a.isSeparator() else a.text()) for a in window.extract_menu.actions()]
    assert extract_items == ["Extract Power Intent", "sep", "Extract Diagram"]
    assert {a.text() for a in window.extract_upf_menu.actions()} == {"3.0", "3.1"}
    assert {a.text() for a in window.extract_cpf_menu.actions()} == {"2.0", "2.1"}

    # "Guide" exists but is an inert placeholder.
    guide_actions = [a for a in window.guide_menu.actions() if not a.isSeparator()]
    assert all(not a.isEnabled() for a in guide_actions)


def test_file_menu_is_present_on_the_welcome_screen_with_load_project(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    assert [a.text() for a in window.menuBar().actions()] == ["File"]
    assert window.load_project_action.isEnabled() is True  # available from the very start
    assert window.save_project_action.isEnabled() is False  # nothing to save yet


# --- Save Project enablement ------------------------------------------


def _flow_to_power_intent(window, monkeypatch):
    _create_project(window, monkeypatch)
    _ready_to_elaborate(window.load_design_tab)
    window.load_design_tab._start_elaborate()
    _run_until(window.load_design_tab.designLoaded)
    window.load_design_tab.continue_button.click()
    window.load_power_intent_tab.files_list.addItem("soc_top.upf")
    window.load_power_intent_tab.mapping_button.click()
    _run_until(window.load_power_intent_tab.mappingCompleted)
    _continue_past_power_intent_findings(window, monkeypatch)


def test_e_save_project_is_disabled_until_the_power_intent_tab_is_enabled(qtbot, monkeypatch):
    window = MainWindow()
    qtbot.addWidget(window)
    _create_project(window, monkeypatch)
    assert window.save_project_action.isEnabled() is False

    _ready_to_elaborate(window.load_design_tab)
    window.load_design_tab._start_elaborate()
    _run_until(window.load_design_tab.designLoaded)
    assert window.save_project_action.isEnabled() is False  # Power Intent tab still disabled

    window.load_design_tab.continue_button.click()
    window.load_power_intent_tab.files_list.addItem("soc_top.upf")
    window.load_power_intent_tab.mapping_button.click()
    _run_until(window.load_power_intent_tab.mappingCompleted)
    assert window.save_project_action.isEnabled() is True  # Power Intent tab now enabled


# --- Save / Load round-trip through the real MainWindow ---------------


def test_a_save_then_load_round_trips_power_intent_and_power_state_table(qtbot, monkeypatch, tmp_path):
    from PySide6.QtGui import QColor as _QColor

    from frontend.src.power_intent_view.power_domain_overlay import PowerDomainPlacement, SupplySetPlacement

    window = MainWindow()
    qtbot.addWidget(window)
    _flow_to_power_intent(window, monkeypatch)

    diagram = window.power_intent_tab.diagram
    diagram.shared_supply_state.supply_port_placements["p1"] = SupplyPortPlacement(
        id="p1", owner_path="top", name="VDD", color=_QColor("#ff0000")
    )
    diagram.shared_domain_state.supply_sets["ss1"] = SupplySetPlacement(id="ss1", name="SS1", power="VDD", owner_path="top")
    diagram.shared_domain_state.domains["d1"] = PowerDomainPlacement(
        id="d1", owner_path="top/u_core", name="PD1", color=_QColor("#00ff00"), primary_supply_set="ss1"
    )
    window.power_state_table_tab._on_add_state()
    row_id = next(iter(window.power_state_table_tab.state.rows))
    window.power_state_table_tab.state.rows[row_id].state_name = "MYSTATE"

    pis = tmp_path / "proj.pis"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(pis), "")))
    assert window._on_save_project() is True
    assert pis.exists() and pis.read_bytes()[:2] == b"\x1f\x8b"

    fresh = MainWindow()
    qtbot.addWidget(fresh)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(pis), "")))
    fresh.load_project_action.trigger()

    assert fresh._central_stack.currentWidget() is fresh.tabs
    assert fresh.tabs.currentIndex() == 2  # jumps straight to a populated Power Intent
    fd = fresh.power_intent_tab.diagram
    assert fd.shared_supply_state.supply_port_placements["p1"].name == "VDD"
    # A subset, not the exact set: Mapping now also IMPORTS the loaded
    # power intent's own domains into the diagram (the Load Power Intent
    # mapping feature), so the mock fixture's PD_CORE is legitimately
    # present alongside the PD1 this test placed by hand. What is being
    # round-tripped is what the test put in, which is PD1.
    assert "PD1" in {d.name for d in fd.shared_domain_state.domains.values()}
    assert "ss1" in fd.shared_domain_state.supply_sets
    assert {r.state_name for r in fresh.power_state_table_tab.state.rows.values()} == {"MYSTATE"}


# --- (f) Load Project from the welcome screen -------------------------


def test_f_load_project_works_from_the_initial_welcome_screen(qtbot, monkeypatch, tmp_path):
    from frontend.src.app.project_file import ProjectData, serialize_project
    from frontend.src.power_intent_view.isolation_overlay import IsolationDiagramState
    from frontend.src.power_intent_view.level_shifter_overlay import LevelShifterDiagramState
    from frontend.src.power_intent_view.port_attribute_overlay import PortAttributeDiagramState
    from frontend.src.power_intent_view.power_domain_overlay import PowerDomainDiagramState
    from frontend.src.power_intent_view.power_state_table import PowerStateTableDiagramState
    from frontend.src.power_intent_view.power_switch_overlay import PowerSwitchDiagramState
    from frontend.src.power_intent_view.retention_overlay import RetentionDiagramState
    from frontend.src.power_intent_view.supply_port_overlay import SupplyPortDiagramState
    from frontend.src.shared.mock_backend import build_mock_design

    supply = SupplyPortDiagramState(supply_port_placements={
        "p1": SupplyPortPlacement(id="p1", owner_path="top", name="VDD", color=QColor("#123456")),
    })
    data = ProjectData(
        "Welcome Load", build_mock_design(), supply, PowerDomainDiagramState(), RetentionDiagramState(),
        PortAttributeDiagramState(), PowerSwitchDiagramState(), IsolationDiagramState(), LevelShifterDiagramState(),
        PowerStateTableDiagramState(),
    )
    pis = tmp_path / "welcome.pis"
    pis.write_bytes(serialize_project(data))

    window = MainWindow()
    qtbot.addWidget(window)
    assert window._central_stack.currentWidget() is window.welcome_page  # still on the welcome screen

    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(pis), "")))
    window.load_project_action.trigger()

    assert window._central_stack.currentWidget() is window.tabs
    assert [a.text() for a in window.menuBar().actions()] == ["File", "View", "Extract", "Guide"]
    assert window.power_intent_tab.diagram.shared_supply_state.supply_port_placements["p1"].name == "VDD"
    assert window.windowTitle().endswith("Welcome Load")


# --- (g) unsaved-changes prompt when loading over an active project ---


def _make_pis(tmp_path, name="Other") -> str:
    from frontend.src.app.project_file import ProjectData, serialize_project
    from frontend.src.power_intent_view.isolation_overlay import IsolationDiagramState
    from frontend.src.power_intent_view.level_shifter_overlay import LevelShifterDiagramState
    from frontend.src.power_intent_view.port_attribute_overlay import PortAttributeDiagramState
    from frontend.src.power_intent_view.power_domain_overlay import PowerDomainDiagramState
    from frontend.src.power_intent_view.power_state_table import PowerStateTableDiagramState
    from frontend.src.power_intent_view.power_switch_overlay import PowerSwitchDiagramState
    from frontend.src.power_intent_view.retention_overlay import RetentionDiagramState
    from frontend.src.power_intent_view.supply_port_overlay import SupplyPortDiagramState
    from frontend.src.shared.mock_backend import build_mock_design

    data = ProjectData(
        name, build_mock_design(), SupplyPortDiagramState(), PowerDomainDiagramState(), RetentionDiagramState(),
        PortAttributeDiagramState(), PowerSwitchDiagramState(), IsolationDiagramState(), LevelShifterDiagramState(),
        PowerStateTableDiagramState(),
    )
    path = tmp_path / f"{name}.pis"
    path.write_bytes(serialize_project(data))
    return str(path)


def test_g_load_over_active_project_yes_saves_first_then_loads(qtbot, monkeypatch, tmp_path):
    window = MainWindow()
    qtbot.addWidget(window)
    _flow_to_power_intent(window, monkeypatch)

    other = _make_pis(tmp_path, "TheOther")
    save_target = tmp_path / "saved_current.pis"
    monkeypatch.setattr(message_box, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(save_target), "")))
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (other, "")))

    window.load_project_action.trigger()

    assert save_target.exists()  # current project was saved first
    assert window.windowTitle().endswith("TheOther")  # then the new one loaded


def test_g_load_over_active_project_yes_then_cancel_save_aborts_the_whole_load(qtbot, monkeypatch, tmp_path):
    window = MainWindow()
    qtbot.addWidget(window)
    _flow_to_power_intent(window, monkeypatch)
    original_title = window.windowTitle()

    other = _make_pis(tmp_path, "ShouldNotLoad")
    monkeypatch.setattr(message_box, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: ("", "")))  # user cancels the Save dialog
    open_called = []
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: open_called.append(True) or (other, "")))

    window.load_project_action.trigger()

    assert open_called == []  # never even reached the "which file to load" step
    assert window.windowTitle() == original_title  # nothing changed


def test_g_load_over_active_project_no_discards_and_loads_directly(qtbot, monkeypatch, tmp_path):
    window = MainWindow()
    qtbot.addWidget(window)
    _flow_to_power_intent(window, monkeypatch)

    other = _make_pis(tmp_path, "DiscardAndGo")
    save_called = []
    monkeypatch.setattr(message_box, "question", lambda *a, **k: QMessageBox.StandardButton.No)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: save_called.append(True) or ("", "")))
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (other, "")))

    window.load_project_action.trigger()

    assert save_called == []  # "No" -> no save dialog at all
    assert window.windowTitle().endswith("DiscardAndGo")


def test_g_load_over_active_project_cancel_aborts(qtbot, monkeypatch, tmp_path):
    window = MainWindow()
    qtbot.addWidget(window)
    _flow_to_power_intent(window, monkeypatch)
    original_title = window.windowTitle()

    monkeypatch.setattr(message_box, "question", lambda *a, **k: QMessageBox.StandardButton.Cancel)
    open_called = []
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: open_called.append(True) or ("", "")))

    window.load_project_action.trigger()

    assert open_called == []
    assert window.windowTitle() == original_title


def test_load_project_shows_a_clear_error_for_a_corrupt_file(qtbot, monkeypatch, tmp_path):
    window = MainWindow()
    qtbot.addWidget(window)
    bad = tmp_path / "bad.pis"
    bad.write_bytes(b"not a real pis file at all")

    errors = []
    monkeypatch.setattr(main_window_module.message_box, "critical", lambda parent, title, text: errors.append((title, text)))
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(bad), "")))
    window.load_project_action.trigger()

    assert len(errors) == 1 and errors[0][0] == "Load Project Failed"
    assert window._central_stack.currentWidget() is window.welcome_page  # nothing loaded


# --- Audit fix (category 09): named tab indices --------------------------


def test_tab_constants_match_the_real_tab_order(qtbot):
    """The indices used to be bare integers at ~12 call sites, several
    with a comment explaining which number meant which tab. Reordering
    or restoring a tab meant auditing every one by hand -- already done
    once, when "Visualize Design" was hidden. This asserts the constants
    and the actual addTab() order can't drift apart."""
    window = MainWindow()
    qtbot.addWidget(window)

    assert [window.tabs.tabText(i) for i in range(window.tabs.count())] == list(MainWindow.TAB_ORDER)
    assert window.tabs.tabText(MainWindow.TAB_LOAD_DESIGN) == "Load Design"
    assert window.tabs.tabText(MainWindow.TAB_LOAD_POWER_INTENT) == "Load Power Intent"
    assert window.tabs.tabText(MainWindow.TAB_POWER_INTENT) == "Power Intent"
    assert window.tabs.tabText(MainWindow.TAB_POWER_STATE_TABLE) == "Power State Table"


def test_main_window_uses_no_bare_integer_tab_indices():
    """Structural guard: the whole point of the constants is that a
    future tab reorder is a one-line change, which only holds if nobody
    reintroduces a literal."""
    import ast
    from pathlib import Path

    source = Path(__file__).parent.parent.parent / "frontend" / "src" / "app" / "main_window.py"
    offenders = []
    for node in ast.walk(ast.parse(source.read_text(), filename=str(source))):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in ("setTabEnabled", "setCurrentIndex", "isTabEnabled"):
            continue
        if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, int):
            offenders.append(f"line {node.lineno}: {node.func.attr}({node.args[0].value}, ...)")
    assert offenders == [], "use the TAB_* constants:\n" + "\n".join(offenders)


# --- the "Extract Power Intent" options dialog (audit B8) ---------------


def _extract_fixture(window):
    """Enough state for both power-state tiers to have something to say."""
    from frontend.src.power_intent_view.power_domain_overlay import (
        PowerDomainPlacement,
        SupplySetPlacement,
    )
    from frontend.src.power_intent_view.power_state_table import PowerStateTableRow
    from frontend.src.power_intent_view.supply_port_overlay import SupplyPortState

    diagram = window.power_intent_tab.diagram
    red = QColor("red")
    diagram.shared_supply_state.supply_port_placements.update({
        "p1": SupplyPortPlacement(id="p1", owner_path="top", name="VDD", color=red,
                                  states=[SupplyPortState("FULL_ON", "1.2"), SupplyPortState("OFF")]),
        "p2": SupplyPortPlacement(id="p2", owner_path="top", name="VSS", color=red,
                                  states=[SupplyPortState("FULL_ON", "0.0")]),
    })
    diagram.shared_domain_state.supply_sets["ss"] = SupplySetPlacement(
        id="ss", name="SS", power="VDD", ground="VSS", owner_path="top")
    diagram.shared_domain_state.domains["d"] = PowerDomainPlacement(
        id="d", owner_path="top", name="PD", color=red, primary_supply_set="ss")
    window.power_state_table_tab.state.rows["r1"] = PowerStateTableRow(
        id="r1", power_domain_id="d", state_name="ON",
        net_states={"p1": "FULL_ON 1.2", "p2": "FULL_ON 0.0"})


def _extract_to(window, monkeypatch, tmp_path, fmt, version, accept_options, legacy):
    """Drive the REAL menu action, answering the options dialog."""
    from frontend.src.power_intent_view import extract_options_dialog as options_module

    seen = {}

    class _Stub(options_module.ExtractOptionsDialog):
        def exec(self):
            seen["shown"] = True
            self.legacy_radio.setChecked(legacy)
            return QDialog.DialogCode.Accepted if accept_options else QDialog.DialogCode.Rejected

    monkeypatch.setattr(main_window_module, "ExtractOptionsDialog", _Stub)
    out = tmp_path / f"out.{fmt.lower()}"
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(out), "")))
    window._on_extract_power_intent(fmt, version)
    return out, seen


def test_the_options_dialog_is_shown_for_upf(qtbot, monkeypatch, tmp_path):
    window = MainWindow()
    qtbot.addWidget(window)
    _create_project(window, monkeypatch)
    _extract_fixture(window)

    out, seen = _extract_to(window, monkeypatch, tmp_path, "UPF", "3.1", True, False)
    assert seen.get("shown") is True
    assert "create_pst" not in out.read_text()      # Modern is the default


def test_picking_legacy_replaces_the_modern_group_rather_than_adding_to_it(qtbot, monkeypatch, tmp_path):
    """Through the REAL menu action: the two representations are
    mutually exclusive, so picking Legacy removes the modern group
    entirely. The old additive behaviour is unreachable -- the dialog
    offers a radio pair, not a checkbox."""
    window = MainWindow()
    qtbot.addWidget(window)
    _create_project(window, monkeypatch)
    _extract_fixture(window)

    out, _ = _extract_to(window, monkeypatch, tmp_path, "UPF", "3.1", True, True)
    text = out.read_text()
    assert "create_pst" in text and "add_pst_state" in text
    assert "create_power_state_group" not in text


def test_the_dialog_offers_an_exclusive_pair_with_modern_defaulted(qtbot):
    """No UI path can ask for both: they share one exclusive
    QButtonGroup, so checking either unchecks the other."""
    from frontend.src.power_intent_view.extract_options_dialog import ExtractOptionsDialog

    dialog = ExtractOptionsDialog("3.1")
    qtbot.addWidget(dialog)

    assert dialog.modern_radio.isChecked() and not dialog.legacy_radio.isChecked()
    assert dialog.legacy_power_states is False
    assert dialog.power_state_group.exclusive() is True

    dialog.legacy_radio.setChecked(True)
    assert dialog.legacy_power_states is True
    assert not dialog.modern_radio.isChecked()  # exclusivity, not both


def test_each_option_is_a_described_card_not_a_bare_label(qtbot):
    """Each choice carries four things, and they must be REAL text, not
    a label standing in for the rest: a plain title, the underlying
    command names, a prose description, and a runnable-looking snippet
    of what that option actually writes.

    Asserted against the widgets rather than the constants, because the
    defect this guards against is a constant existing while nothing
    displays it -- which is exactly what a bare radio pair looks like
    from the outside."""
    from frontend.src.power_intent_view.extract_options_dialog import ExtractOptionsDialog

    dialog = ExtractOptionsDialog("3.1")
    qtbot.addWidget(dialog)

    cards = {
        "modern": (dialog.modern_card, dialog.modern_radio,
                   ["create_power_state_group", "add_power_state"]),
        "legacy": (dialog.legacy_card, dialog.legacy_radio,
                   ["create_pst", "add_pst_state", "add_port_state"]),
    }
    for name, (card, radio, commands) in cards.items():
        assert card.radio is radio, f"{name}: the radio must live in its own card"
        assert radio.text().strip(), f"{name}: no title"
        assert radio.font().bold(), f"{name}: the title must read as a title"
        # The command names are a SUBTITLE, never the title itself.
        assert radio.text() != card.commands_label.text()
        for command in commands:
            assert command in card.commands_label.text(), f"{name}: missing {command}"
        # A real description, not one clause.
        assert len(card.description_label.text().split()) >= 10, f"{name}: description too thin"
        assert card.description_label.wordWrap(), f"{name}: description would clip"
        # A real snippet: more than one line, and naming a command it claims to write.
        example = card.example_label.text()
        assert "\n" in example, f"{name}: the example is not a code snippet"
        assert any(command in example for command in commands), f"{name}: example names no command"
        assert "monospace" in card.example_label.styleSheet()

    # ...and each card's own radio still drives the generator choice.
    dialog.modern_radio.setChecked(True)
    assert dialog.legacy_power_states is False
    dialog.legacy_radio.setChecked(True)
    assert dialog.legacy_power_states is True


def test_the_described_options_drive_generation_as_their_descriptions_claim(
    qtbot, monkeypatch, tmp_path
):
    """The cards are only honest if the file matches what they say.
    Each option's own advertised commands must appear in the output it
    produces, and the other option's must not."""
    from frontend.src.power_intent_view.extract_options_dialog import ExtractOptionsDialog

    dialog = ExtractOptionsDialog("3.1")
    qtbot.addWidget(dialog)
    advertised = {
        False: dialog.modern_card.commands_label.text(),
        True: dialog.legacy_card.commands_label.text(),
    }

    window = MainWindow()
    qtbot.addWidget(window)
    _create_project(window, monkeypatch)
    _extract_fixture(window)

    for legacy, commands in advertised.items():
        # A real directory per run: a save path in a directory that does
        # not exist makes the export raise and pop a REAL modal, which
        # hangs headlessly -- the hazard this suite already knows well.
        out_dir = tmp_path / str(legacy)
        out_dir.mkdir()
        out, _ = _extract_to(window, monkeypatch, out_dir, "UPF", "3.1", True, legacy)
        text = out.read_text()
        for command in commands.split(" / "):
            assert command in text, f"legacy={legacy}: advertised {command} but did not write it"
        for command in advertised[not legacy].split(" / "):
            if command in commands.split(" / "):
                continue  # add_power_state is genuinely shared wording; skip
            assert command not in text, f"legacy={legacy}: wrote the other option's {command}"


def test_cancelling_the_options_dialog_writes_nothing(qtbot, monkeypatch, tmp_path):
    """Asked BEFORE the save dialog, so cancelling here must not have
    first made the user choose a filename -- nor leave a file behind."""
    window = MainWindow()
    qtbot.addWidget(window)
    _create_project(window, monkeypatch)
    _extract_fixture(window)

    out, seen = _extract_to(window, monkeypatch, tmp_path, "UPF", "3.1", False, True)
    assert seen.get("shown") is True
    assert not out.exists()


def test_the_options_dialog_never_appears_for_cpf(qtbot, monkeypatch, tmp_path):
    """CPF has no legacy-power-state-table concept, so there is nothing
    to ask. The dialog is absent rather than present-and-inert."""
    window = MainWindow()
    qtbot.addWidget(window)
    _create_project(window, monkeypatch)
    _extract_fixture(window)
    monkeypatch.setattr(main_window_module.message_box, "warning", lambda *a, **k: None)

    out, seen = _extract_to(window, monkeypatch, tmp_path, "CPF", "2.0", True, True)
    assert "shown" not in seen
    assert out.exists()
    assert "create_pst" not in out.read_text()


# --- Change 1: "Create Project" over an active project ----------------------
#
# The action used to disable itself once a project existed, matching this
# window's never-go-back navigation. It stays enabled now, and creating
# over an active project runs the SAME unsaved-changes flow "Load Project"
# already established. These mirror that flow's own three outcomes.


def _active_project(window, monkeypatch, name="First"):
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: (name, True)))
    window.create_project_action.trigger()
    assert window._project_is_active()


def test_create_project_stays_available_while_a_project_is_open(qtbot, monkeypatch):
    window = MainWindow()
    qtbot.addWidget(window)
    _active_project(window, monkeypatch)

    assert window.create_project_action.isEnabled() is True
    assert window.create_project_action in window.file_menu.actions()


def test_create_project_over_an_active_project_asks_to_save_first(qtbot, monkeypatch, tmp_path):
    """"Yes" saves, THEN creates -- both, in that order."""
    window = MainWindow()
    qtbot.addWidget(window)
    _active_project(window, monkeypatch)

    asked = []
    monkeypatch.setattr(main_window_module.message_box, "question",
                        lambda *a, **k: asked.append(a[2]) or QMessageBox.StandardButton.Yes)
    pis = tmp_path / "saved.pis"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(pis), "")))
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("Second", True)))

    window.create_project_action.trigger()

    assert asked and "save the current project before creating a new one" in asked[0]
    assert pis.exists()                      # the save really happened
    assert window.project_name == "Second"   # ...and so did the create


def test_create_project_aborts_entirely_when_the_save_dialog_is_cancelled(qtbot, monkeypatch):
    """"Yes" then Cancel aborts the WHOLE operation -- the same
    Yes-then-Cancel rule Load Project established. The old project must
    survive, and the name prompt must never even appear."""
    window = MainWindow()
    qtbot.addWidget(window)
    _active_project(window, monkeypatch)

    monkeypatch.setattr(main_window_module.message_box, "question",
                        lambda *a, **k: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: ("", "")))  # cancelled
    prompted = []
    monkeypatch.setattr(QInputDialog, "getText",
                        staticmethod(lambda *a, **k: prompted.append(1) or ("Second", True)))

    window.create_project_action.trigger()

    assert prompted == []                   # never got as far as asking for a name
    assert window.project_name == "First"   # the original project is untouched


def test_create_project_with_no_discards_and_creates_directly(qtbot, monkeypatch):
    window = MainWindow()
    qtbot.addWidget(window)
    _active_project(window, monkeypatch)

    monkeypatch.setattr(main_window_module.message_box, "question",
                        lambda *a, **k: QMessageBox.StandardButton.No)
    saved = []
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: saved.append(1) or ("", "")))
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("Second", True)))

    window.create_project_action.trigger()

    assert saved == []                      # nothing was saved
    assert window.project_name == "Second"  # and the new project was created


def test_create_project_cancelled_at_the_prompt_changes_nothing(qtbot, monkeypatch):
    window = MainWindow()
    qtbot.addWidget(window)
    _active_project(window, monkeypatch)

    monkeypatch.setattr(main_window_module.message_box, "question",
                        lambda *a, **k: QMessageBox.StandardButton.Cancel)
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("Second", True)))

    window.create_project_action.trigger()

    assert window.project_name == "First"


def test_the_first_create_project_asks_nothing(qtbot, monkeypatch):
    """No active project means nothing to lose -- the prompt would be
    pure friction on the very first action a user takes."""
    window = MainWindow()
    qtbot.addWidget(window)

    asked = []
    monkeypatch.setattr(main_window_module.message_box, "question",
                        lambda *a, **k: asked.append(1) or QMessageBox.StandardButton.Cancel)
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("First", True)))

    window.create_project_action.trigger()

    assert asked == []
    assert window.project_name == "First"


# --- Hidden hierarchies survive Save / Load Project -------------------


def test_hidden_hierarchies_are_saved_and_restored_with_the_project(qtbot, monkeypatch, tmp_path):
    """Explicit request: a hierarchy hidden in the diagram stays hidden
    after Save Project -> Load Project -- in the Hierarchy Tree (greyed)
    AND in the diagram (no box) -- and the restore is not undoable."""
    window = MainWindow()
    qtbot.addWidget(window)
    _flow_to_power_intent(window, monkeypatch)
    window.power_intent_tab.tree.hide_paths(["top/u_dbg"])
    assert window.power_intent_tab.tree.hidden_paths() == ["top/u_dbg"]

    pis = tmp_path / "hidden.pis"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(pis), "")))
    assert window._on_save_project() is True

    fresh = MainWindow()
    qtbot.addWidget(fresh)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(pis), "")))
    fresh.load_project_action.trigger()

    tree = fresh.power_intent_tab.tree
    assert tree.hidden_paths() == ["top/u_dbg"]
    view = fresh.power_intent_tab.diagram._view
    assert "top/u_dbg" not in view._boxes  # gone from the diagram, not just greyed
    assert "top/u_core" in view._boxes      # everything else still drawn
    # Loading is not an edit: Ctrl+Z has nothing to undo.
    assert tree.undo_stack.count() == 0


def test_a_project_saved_before_hidden_paths_existed_loads_with_nothing_hidden():
    """Backward compatibility: an older .pis has no `hidden_paths`."""
    import gzip
    import json

    from frontend.src.app.project_file import ProjectData, deserialize_project, serialize_project
    from frontend.src.power_intent_view.isolation_overlay import IsolationDiagramState
    from frontend.src.power_intent_view.level_shifter_overlay import LevelShifterDiagramState
    from frontend.src.power_intent_view.port_attribute_overlay import PortAttributeDiagramState
    from frontend.src.power_intent_view.power_domain_overlay import PowerDomainDiagramState
    from frontend.src.power_intent_view.power_state_table import PowerStateTableDiagramState
    from frontend.src.power_intent_view.power_switch_overlay import PowerSwitchDiagramState
    from frontend.src.power_intent_view.retention_overlay import RetentionDiagramState
    from frontend.src.power_intent_view.supply_port_overlay import SupplyPortDiagramState
    from frontend.src.shared.mock_backend import build_mock_design

    data = ProjectData(
        "Old", build_mock_design(), SupplyPortDiagramState(), PowerDomainDiagramState(), RetentionDiagramState(),
        PortAttributeDiagramState(), PowerSwitchDiagramState(), IsolationDiagramState(), LevelShifterDiagramState(),
        PowerStateTableDiagramState(), hidden_paths=["top/u_dbg"],
    )
    payload = json.loads(gzip.decompress(serialize_project(data)))
    assert payload["hidden_paths"] == ["top/u_dbg"]
    del payload["hidden_paths"]
    old = deserialize_project(gzip.compress(json.dumps(payload).encode()))
    assert old.hidden_paths == []


def test_a_saved_hidden_path_missing_from_the_design_is_dropped(qtbot):
    """The RTL may have changed since the save: a path that no longer
    exists is dropped, not kept as an invisible entry."""
    from frontend.src.hierarchy_view.hierarchy_tree_widget import HierarchyTreeWidget
    from frontend.src.hierarchy_view.selection_controller import HierarchySelectionController
    from frontend.src.shared.mock_backend import build_mock_design

    tree = HierarchyTreeWidget(HierarchySelectionController())
    qtbot.addWidget(tree)
    tree.load(build_mock_design())
    tree.restore_hidden_paths(["top/u_dbg", "top/u_gone"])
    assert tree.hidden_paths() == ["top/u_dbg"]
