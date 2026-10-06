"""frontend/src/backend_integration/ -- the real-backend wiring (see
docs/backend_integration.md) that `power_intent_studio.py`'s own entry
point (`frontend/src/app/main.py::main()`) uses by default. Exercises
real backend.api calls against the examples/soc_with_ram fixture through
the actual injected-action MainWindow object graph, driven by real widget
clicks -- not internal method calls, and not backend/api in isolation
(that's covered in tests/backend/test_api.py).

Formerly `test_validation_harness.py`, covering a separate
`power_intent_studio_validation_harness.py` prototype entry point -- that
script has been retired; its wiring is now the app's own single,
standard entry point, and this file's own tests were renamed/reworked to
match (see `test_the_real_entry_point_wires_real_backend_actions` and
`test_mainwindow_bare_constructor_still_defaults_to_mock_actions` below).
"""

import os
from pathlib import Path

from PySide6.QtWidgets import QMessageBox

from frontend.src.app.main import build_main_window
from frontend.src.app.main_window import MainWindow
from frontend.src.shared.models.validation import Severity
from frontend.src.hierarchy_view.load_design_tab import _make_item as _make_design_item
from frontend.src.backend_integration.real_actions import BackendState, make_elaborate_action, make_load_power_intent_action, make_mapping_action

# VENDORED under tests/, not an examples/ design. These tests used to
# point at examples/soc_with_ram and broke the day that example was
# curated away -- a test should not depend on assets maintained for
# users' benefit, which are free to change or disappear.
EXAMPLE_DIR = Path(__file__).parent / "golden_simple10"
DESIGN_DIR = EXAMPLE_DIR / "design"
RTL_FILES = [str(DESIGN_DIR / "sram.v"), str(DESIGN_DIR / "sub.sv")]
LIB_FILES = [str(DESIGN_DIR / "sram.lib")]
TOP = "sram"


def _make_harness_window():
    state = BackendState()
    window = MainWindow(
        elaborate_action=make_elaborate_action(state),
        load_power_intent_action=make_load_power_intent_action(state),
        mapping_action=make_mapping_action(state),
    )
    return window, state


def _stage_and_load(load_design_tab, rtl_files, lib_files):
    """Simulates picking files via Load RTL/LIB Files... (staging list),
    then clicking "Load" to transfer them into the "Loaded" list --
    exactly the real Design Files -> Loaded flow, not a shortcut around
    it.
    """
    for f in rtl_files:
        load_design_tab.staging_list.addItem(_make_design_item("rtl", f))
    for f in lib_files:
        load_design_tab.staging_list.addItem(_make_design_item("lib", f))
    load_design_tab.load_button.click()


def _continue_past_findings(window):
    """Clicks 'Load Power Intent's Continue past its acknowledgment
    dialog (spec section 4).

    Patched by direct attribute assignment rather than monkeypatch: this
    file's helpers take no fixtures, and the substitution is restored
    immediately after the click rather than at teardown."""
    from frontend.src.power_intent_view import load_power_intent_tab as load_pi_module

    original = load_pi_module.message_box.question
    load_pi_module.message_box.question = lambda *a, **k: QMessageBox.StandardButton.Yes
    try:
        window.load_power_intent_tab.continue_button.click()
    finally:
        load_pi_module.message_box.question = original


def test_real_actions_run_synchronously_not_via_the_mock_timer(qtbot):
    """A real click must produce a result immediately -- no QTimer delay,
    confirming the indeterminate-progress path (not the mock's discrete
    animation) is what actually ran.
    """
    window, _ = _make_harness_window()
    qtbot.addWidget(window)
    _stage_and_load(
        window.load_design_tab,
        RTL_FILES,
        LIB_FILES,
    )
    window.load_design_tab.top_module_edit.setText(TOP)

    fired = []
    window.load_design_tab.designLoaded.connect(lambda d: fired.append(d))
    window.load_design_tab.elaborate_button.click()

    assert fired  # already fired by the time click() returns -- proves synchronous, not timer-deferred


def test_load_button_transfers_staged_files_and_clears_staging(qtbot):
    window, _ = _make_harness_window()
    qtbot.addWidget(window)
    tab = window.load_design_tab
    _stage_and_load(tab, [RTL_FILES[0]], LIB_FILES)

    assert tab.staging_list.count() == 0
    assert tab.loaded_list.count() == 2
    assert tab._loaded_paths_by_kind("rtl") == [RTL_FILES[0]]
    assert tab._loaded_paths_by_kind("lib") == LIB_FILES


def test_remove_selected_only_affects_staging_not_loaded(qtbot):
    window, _ = _make_harness_window()
    qtbot.addWidget(window)
    tab = window.load_design_tab
    _stage_and_load(tab, [RTL_FILES[0]], [])  # one file already committed to "Loaded"

    tab.staging_list.addItem(_make_design_item("rtl", RTL_FILES[1]))
    tab.staging_list.selectAll()
    tab.remove_selected_button.click()

    assert tab.staging_list.count() == 0
    assert tab.loaded_list.count() == 1  # untouched


def test_leaving_top_module_blank_is_rejected_with_a_clear_error_popup(qtbot, monkeypatch):
    """Top Module is a required field for real elaboration -- the GUI
    doesn't offer pyslang's own auto-detect-when-unspecified path (see
    backend/api/elaborate_rtl), so an empty click must be rejected
    outright, immediately, with a clear error -- not silently attempted
    (which previously surfaced as a confusing "top '' not found" error
    from deep inside pyslang instead). Shown as a QMessageBox popup, not
    a diagnostics-dock entry -- see load_design_tab.py's own docstring
    for why."""
    from frontend.src.shared.widgets import message_box

    window, _ = _make_harness_window()
    qtbot.addWidget(window)
    _stage_and_load(
        window.load_design_tab,
        RTL_FILES,
        LIB_FILES,
    )
    assert window.load_design_tab.top_module_edit.text() == ""  # deliberately left blank

    shown = []
    monkeypatch.setattr(message_box, "critical", lambda *args, **kwargs: shown.append(args))
    results = []
    window.load_design_tab.designLoaded.connect(lambda d: results.append(d))
    window.load_design_tab.elaborate_button.click()

    assert results == []  # no elaboration attempted at all
    assert shown
    assert window.load_design_tab.diagnostics_dock.model.rowCount() == 0  # nothing routed through the dock


def test_explicit_top_selects_a_submodule_instantiated_elsewhere(qtbot):
    """The real workflow you flagged: loading a whole design but wanting
    to compile just a DUT submodule that's normally instantiated by
    something else in the same file set. Confirmed via backend/api's own
    tests too (tests/backend/test_verilog_elaborate.py) -- this checks it
    survives through the real GUI flow end to end.
    """
    window, _ = _make_harness_window()
    qtbot.addWidget(window)
    _stage_and_load(
        window.load_design_tab,
        RTL_FILES,
        LIB_FILES,
    )
    window.load_design_tab.top_module_edit.setText("sub")  # a submodule, not the design root

    results = []
    window.load_design_tab.designLoaded.connect(lambda d: results.append(d))
    window.load_design_tab.elaborate_button.click()

    assert len(results) == 1
    design = results[0]
    top_def = design.definitions[design.top]
    assert top_def.base_name == "sub"
    assert window.load_design_tab.diagnostics_dock.model.rowCount() == 0


def test_full_real_pipeline_through_the_gui_produces_the_expected_overlay(qtbot):
    window, _ = _make_harness_window()
    qtbot.addWidget(window)

    _stage_and_load(
        window.load_design_tab,
        RTL_FILES,
        LIB_FILES,
    )
    window.load_design_tab.top_module_edit.setText(TOP)
    window.load_design_tab.elaborate_button.click()
    assert window.tabs.isTabEnabled(1) is True  # Load Power Intent
    assert window.power_intent_tab.diagram._stack.currentWidget() is window.power_intent_tab.diagram._placeholder

    window.load_power_intent_tab.files_list.addItems(
        [os.path.abspath(str(EXAMPLE_DIR / "power_intent" / "10_idiomatic_constructs.upf"))]
    )
    window.load_power_intent_tab.mapping_button.click()  # now performs load + mapping together; format inferred from the .upf extension
    assert window.tabs.isTabEnabled(2) is True
    assert window.power_intent_tab.diagram._stack.currentWidget() is window.power_intent_tab.diagram._tabs
    assert ("PowerDomain", "::PD_SUB") in window.power_intent_tab.diagram._index
    assert ("PowerSwitch", "::SW_SUB") in window.power_intent_tab.diagram._index
    assert ("IsolationStrategy", "::ISO_PERIPH") in window.power_intent_tab.diagram._index
    assert ("LevelShifterStrategy", "::LS_PERIPH") in window.power_intent_tab.diagram._index
    assert ("RetentionStrategy", "::RET_PERIPH") in window.power_intent_tab.diagram._index

    # The import phase (translating this real UPF into diagram state)
    # reports the flags this tool does not model -- `-source`/`-sink`
    # domain filtering on Isolation/Level Shifter, a supply port's
    # direction, a power state's `-domain`/`-complete`. All WARNINGs,
    # nothing unresolved, so the file maps cleanly but not silently: the
    # run therefore stays put behind Continue rather than navigating
    # away from a dock that just appeared.
    import_diagnostics = [
        d for d in window.load_power_intent_tab.diagnostics_dock.model._visible()
        if d.source == "power_intent_import"
    ]
    assert import_diagnostics
    assert all(d.severity is Severity.WARNING for d in import_diagnostics)
    # Still wherever it was (this test never clicked Load Design's own
    # Continue, so that is tab 0) -- the point is that Mapping did not
    # navigate on its own.
    assert window.tabs.currentIndex() != 2
    assert window.load_power_intent_tab.continue_button.isEnabled() is True

    # And it did produce real diagram state, not just diagnostics. The
    # placements come from this file's supply NETS, which is what a
    # diagram supply actually is here -- `create_supply_port VDD_IN` is
    # the boundary port feeding one, not a third placement of its own.
    supplies = window.power_intent_tab.diagram.shared_supply_state.supply_port_placements
    domains = window.power_intent_tab.diagram.shared_domain_state.domains
    assert {p.name for p in supplies.values()} == {"VDD_ALWAYS_ON", "VDD_SUB_SW", "VSS"}
    assert {d.name for d in domains.values()} == {"PD_TOP", "PD_SUB"}

    _continue_past_findings(window)
    assert window.tabs.currentIndex() == 2  # Mapping success navigates to Power Intent
    # Spec section 7: stays enabled so another file can be loaded later.
    assert window.tabs.isTabEnabled(1) is True


def test_mixed_language_rtl_surfaces_as_a_diagnostic_not_a_crash(qtbot):
    """The exact failure mode a user hits if they accidentally hand the
    harness a mixed Verilog+VHDL file set: a clear ERROR Diagnostic in
    the dock, tabs stay disabled, no exception propagates out of the
    click handler.
    """
    window, _ = _make_harness_window()
    qtbot.addWidget(window)

    backend_golden = Path(__file__).parent.parent / "backend" / "golden_rtl"
    _stage_and_load(
        window.load_design_tab,
        [str(backend_golden / "verilog_three_level_hierarchy" / "top.v"), str(backend_golden / "vhdl_flop_min" / "top.vhd")],
        [],
    )
    window.load_design_tab.top_module_edit.setText("top")

    fired = []
    window.load_design_tab.designLoaded.connect(lambda d: fired.append(d))
    window.load_design_tab.elaborate_button.click()  # must not raise

    assert fired == []
    assert window.tabs.isTabEnabled(1) is False
    assert window.tabs.isTabEnabled(2) is False
    assert window.load_design_tab.diagnostics_dock.model.rowCount() == 1
    diagnostic = window.load_design_tab.diagnostics_dock.model._visible()[0]
    assert diagnostic.code == "RTL-ELABORATE-ERROR"
    assert "single-language RTL design only" in diagnostic.message
    assert diagnostic.file is None
    assert diagnostic.line is None
    assert window.load_design_tab.continue_button.isEnabled() is False  # nothing to continue with


def test_real_pipeline_does_not_navigate_until_continue_is_clicked(qtbot):
    """The Continue gate applies to the real harness path too, not just
    the mock one (test_main_window.py covers mock mode's own version of
    this) -- a real successful Elaborate must not jump to Visualize
    Design or disable Load Design by itself."""
    window, _ = _make_harness_window()
    qtbot.addWidget(window)
    _stage_and_load(
        window.load_design_tab,
        RTL_FILES,
        LIB_FILES,
    )
    window.load_design_tab.top_module_edit.setText(TOP)
    window.load_design_tab.elaborate_button.click()

    assert window.tabs.currentIndex() == 0
    assert window.tabs.isTabEnabled(0) is True
    assert window.load_design_tab.continue_button.isEnabled() is True

    window.load_design_tab.continue_button.click()
    assert window.tabs.currentIndex() == 1
    assert window.tabs.isTabEnabled(0) is False


def test_real_rtl_syntax_error_populates_file_and_line_in_the_dock(qtbot):
    """Regression test for a real, reported bug: a missing comma in a
    port connection list previously showed up in the dock with the
    File/Line columns blank and a message naming only a bare pyslang
    diagnostic code -- not helpful for finding the actual bug. Confirmed
    end to end through the real GUI flow, not just backend.api in
    isolation (tests/backend/test_verilog_elaborate.py covers that).
    """
    backend_golden = Path(__file__).parent.parent / "backend" / "golden_rtl"
    window, _ = _make_harness_window()
    qtbot.addWidget(window)
    _stage_and_load(window.load_design_tab, [str(backend_golden / "verilog_syntax_error" / "top.v")], [])
    window.load_design_tab.top_module_edit.setText("top")

    fired = []
    window.load_design_tab.designLoaded.connect(lambda d: fired.append(d))
    window.load_design_tab.elaborate_button.click()

    assert fired == []
    assert window.load_design_tab.diagnostics_dock.model.rowCount() == 1
    diagnostic = window.load_design_tab.diagnostics_dock.model._visible()[0]
    assert diagnostic.file is not None and diagnostic.file.endswith("verilog_syntax_error/top.v")
    assert diagnostic.line == 16
    assert "expected ','" in diagnostic.message


def test_mainwindow_bare_constructor_still_defaults_to_mock_actions(qtbot):
    """MainWindow's own bare constructor (no actions injected) must keep
    defaulting to mock/fixture data -- this is what the rest of the
    front-end test suite (hundreds of tests, per CLAUDE.md's Front-End
    Milestone 1-3 history) builds against, and it must stay true even
    though `power_intent_studio.py` itself no longer uses this default
    (see test_the_real_entry_point_wires_real_backend_actions below)."""
    window = MainWindow()
    qtbot.addWidget(window)
    assert window.load_design_tab._elaborate_action is None
    assert window.load_power_intent_tab._load_action is None
    assert window.load_power_intent_tab._mapping_action is None


def test_the_real_entry_point_wires_real_backend_actions(qtbot):
    """The consolidation regression test: `frontend/src/app/main.py`'s
    own `build_main_window()` -- exactly what `main()` (and so
    `power_intent_studio.py`) constructs before showing the window --
    must wire all three real backend actions, not fall back to mock
    mode. Driven end to end through real widget clicks against the same
    real example design as the rest of this file, confirming a genuine,
    correctly-elaborated result comes back through the app's actual
    entry point, not just through a hand-assembled BackendState/MainWindow
    pairing.
    """
    window = build_main_window()
    qtbot.addWidget(window)
    assert window.load_design_tab._elaborate_action is not None
    assert window.load_power_intent_tab._load_action is not None
    assert window.load_power_intent_tab._mapping_action is not None

    _stage_and_load(
        window.load_design_tab,
        RTL_FILES,
        LIB_FILES,
    )
    window.load_design_tab.top_module_edit.setText(TOP)

    results = []
    window.load_design_tab.designLoaded.connect(lambda d: results.append(d))
    window.load_design_tab.elaborate_button.click()

    assert len(results) == 1
    design = results[0]
    top_def = design.definitions[design.top]
    assert top_def.base_name == TOP
    assert window.load_design_tab.diagnostics_dock.model.rowCount() == 0


def test_the_example_upfs_idiomatic_constructs_all_elaborate_and_map(qtbot):
    """A hand-written, idiomatic UPF 3.1 file, and every construct it gained had to be
    implemented -- the file failed at its FIRST unsupported command
    before that, so none of the rest was even reached. Pinned here
    because these are the constructs a hand-written file actually uses,
    and the round-trip suite cannot cover them: it only exercises what
    this app itself generates.

    What this asserts, construct by construct:
      - `associate_supply_set SS -handle PD.primary` binds a supply set
        to a domain (UPF 2.1+'s third spelling of that fact) and lands on
        the SAME primary_supply field the other two spellings do;
      - a net reaching the boundary through
        `connect_supply_net <net> -ports {PORT}` with a DIFFERENT name is
        a boundary Supply Port carrying that port's own `-direction`, not
        an Internal Supply;
      - a switched net with no boundary port stays an Internal Supply;
      - `set_isolation -applies_to`, `set_level_shifter -input_supply`/
        `-output_supply` and `create_power_switch -supply_set` parse
        rather than failing the file. `-applies_to` is then RESOLVED INTO
        the element list (it describes the same selection those elements
        do); the two level-shifter supplies genuinely have nowhere to go
        and are REPORTED;
      - `add_power_state -supply SS -state {ON -supply_expr {...}
        -simstate NORMAL}` -- the object named by flag instead of
        positionally, and the state carrying its own nested flags rather
        than being a {name value} pair.
    """
    from backend.api import elaborate_power_intent, elaborate_rtl, run_mapping
    from frontend.src.backend_integration.backend_adapter import (
        adapt_design,
        adapt_mapped_design,
        adapt_power_intent,
    )
    from frontend.src.power_intent_view.power_intent_import import import_power_intent

    idiomatic = str(EXAMPLE_DIR / "power_intent" / "10_idiomatic_constructs.upf")
    backend_design = elaborate_rtl(RTL_FILES, LIB_FILES, top=TOP)
    rtl = adapt_design(backend_design)
    power_intent = elaborate_power_intent(idiomatic, "upf")
    adapted = adapt_power_intent(power_intent)
    imported = import_power_intent(
        adapted, rtl,
        adapt_mapped_design(run_mapping(power_intent, backend_design), adapted, rtl),
        idiomatic,
    )

    # associate_supply_set drove both domain bindings, so both domains
    # resolve a primary set -- and therefore distinct derived colours.
    sets = {s.id: s for s in imported.domain.supply_sets.values()}
    domains = {d.name: d for d in imported.domain.domains.values()}
    assert sets[domains["PD_TOP"].primary_supply_set].name == "SS_TOP"
    assert sets[domains["PD_SUB"].primary_supply_set].name == "SS_SUB"
    assert domains["PD_TOP"].color.name() != domains["PD_SUB"].color.name()

    # Top-most placement per name: a nested supply set using a top-level
    # port also gets a same-named copy on its own hierarchy.
    supplies = {
        p.name: p
        for p in sorted(imported.supply.supply_port_placements.values(), key=lambda p: p.owner_path.count("/"), reverse=True)
    }
    # Boundary through a DIFFERENTLY-NAMED port (VDD_IN -> VDD_ALWAYS_ON).
    assert supplies["VDD_ALWAYS_ON"].is_internal_supply is False
    assert supplies["VDD_ALWAYS_ON"].direction == "in"
    assert supplies["VSS"].is_internal_supply is False
    assert supplies["VSS"].direction == "in"
    # Switched, with no boundary port of its own.
    assert supplies["VDD_SUB_SW"].is_internal_supply is True
    assert supplies["VDD_SUB_SW"].direction == ""

    switch = next(iter(imported.power_switch.switches.values()))
    assert (switch.name, switch.input_supply_port, switch.output_supply_port) == ("SW_SUB", "VIN", "VOUT")
    assert switch.control_port == "EN"  # the PIN half of {EN sub_sleep_n}

    iso = next(iter(imported.isolation.strategies.values()))
    assert iso.clamp_value == "low"  # UPF's 0 -> this app's own vocabulary
    assert sorted(e.rsplit("/", 1)[-1] for e in iso.elements) == ["addr_out", "data_out", "valid_out"]

    # Nothing unresolved: every reference in the file resolves against
    # the real RTL. The warnings are the honest "this GUI has nowhere to
    # put that" set, which is what they are for.
    assert [d.message for d in imported.errors] == []
    unmodelled = {d.message.split("'")[3] for d in imported.warnings if "is not modelled" in d.message}
    assert {"input_supply", "output_supply"} <= unmodelled
    # `-applies_to outputs` is matched against the rule's own elements
    # rather than reported as unmodelled -- all three listed ports ARE
    # outputs, so all three survive.
    assert "applies_to" not in unmodelled

    # And the supply-set power states reached the rails they describe:
    # `-supply_expr {power == {FULL_ON 1.0} && ground == {FULL_ON 0.0}}`
    # on SS_TOP is the same statement as per-net states on its own rails.
    assert [(s.state_type, s.voltage) for s in supplies["VDD_ALWAYS_ON"].states] == [("FULL_ON", "1.0")]
    assert [(s.state_type, s.voltage) for s in supplies["VSS"].states] == [("FULL_ON", "0.0")]
