"""The "Load Power Intent" tab mirrors "Load Design"'s layout structure
(explicit request), without giving up any control of its own.

Two halves, deliberately separate:

  - PARITY: both tabs are a QMainWindow whose central area holds a
    horizontal QSplitter of exactly two QGroupBoxes -- a staging pane
    whose picks are committed via an "Add" button, and a "Loaded" pane
    holding what the action button actually reads -- with a diagnostics
    dock in the same bottom position. Asserted structurally, by walking
    the real widget tree, not by comparing screenshots or hardcoding
    child counts.

  - NO FEATURE LOSS: this was a layout/organization change, so every
    pre-existing "Load Power Intent" control must still be present AND
    wired. A parity test alone would happily pass on a tab that had
    been simplified into matching, which is exactly what the request
    ruled out.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGroupBox, QSplitter

from frontend.src.diagnostics.diagnostics_dock import DiagnosticsDock
from frontend.src.hierarchy_view.load_design_tab import LoadDesignTab, _make_item
from frontend.src.power_intent_view.load_power_intent_tab import COMPATIBILITY_NOTE, LoadPowerIntentTab


def _splitter_of(tab) -> QSplitter:
    """The tab's own two-pane splitter, wherever it sits in the central
    widget -- Load Design makes it the central widget directly, Load
    Power Intent nests it under the compatibility note, and the parity
    being asserted is the PANE STRUCTURE, not that nesting depth."""
    central = tab.centralWidget()
    if isinstance(central, QSplitter):
        return central
    found = central.findChildren(QSplitter)
    assert found, "no two-pane splitter found in the central widget"
    return found[0]


def _pane_titles(tab) -> list[str]:
    splitter = _splitter_of(tab)
    return [splitter.widget(i).title() for i in range(splitter.count()) if isinstance(splitter.widget(i), QGroupBox)]


# --- layout parity ---------------------------------------------------------


def test_both_tabs_use_a_two_pane_horizontal_splitter(qtbot):
    design, power_intent = LoadDesignTab(), LoadPowerIntentTab()
    qtbot.addWidget(design)
    qtbot.addWidget(power_intent)

    for tab in (design, power_intent):
        splitter = _splitter_of(tab)
        assert splitter.orientation() == Qt.Orientation.Horizontal
        assert len(_pane_titles(tab)) == 2, f"{type(tab).__name__} is not two group-box panes"


def test_both_tabs_name_their_panes_the_same_way(qtbot):
    """Left pane names the files being picked, right pane is "Loaded"."""
    design, power_intent = LoadDesignTab(), LoadPowerIntentTab()
    qtbot.addWidget(design)
    qtbot.addWidget(power_intent)

    assert _pane_titles(design) == ["Design Files", "Loaded"]
    assert _pane_titles(power_intent) == ["Power Intent Files", "Loaded"]


def test_both_tabs_stage_picks_and_commit_them_with_an_add_button(qtbot):
    """The pattern Load Design established: a browse populates STAGING,
    "Add" moves everything into Loaded and clears staging."""
    design, power_intent = LoadDesignTab(), LoadPowerIntentTab()
    qtbot.addWidget(design)
    qtbot.addWidget(power_intent)

    # Load Design's staging items carry (kind, path) role data, so they
    # must be built by its own helper -- a bare string reaches _make_item
    # with kind=None and raises.
    design.staging_list.addItem(_make_item("rtl", "some.v"))
    design.load_button.click()
    assert design.staging_list.count() == 0 and design.loaded_list.count() == 1

    power_intent.staging_list.addItem("some.upf")
    power_intent.add_button.click()
    assert power_intent.staging_list.count() == 0 and power_intent.files_list.count() == 1


def test_remove_selected_acts_on_staging_and_loaded_independently_in_both_tabs(qtbot):
    design, power_intent = LoadDesignTab(), LoadPowerIntentTab()
    qtbot.addWidget(design)
    qtbot.addWidget(power_intent)

    # Load Power Intent: staging removal must not touch Loaded.
    power_intent.files_list.addItem("kept.upf")
    power_intent.staging_list.addItem("discarded.upf")
    power_intent.staging_list.selectAll()
    power_intent.remove_selected_button.click()
    assert power_intent.staging_list.count() == 0
    assert power_intent.files_list.count() == 1

    # ...and the loaded pane has its own separate button, as Load Design does.
    power_intent.files_list.selectAll()
    power_intent.remove_loaded_button.click()
    assert power_intent.files_list.count() == 0

    design.loaded_list.addItem(_make_item("rtl", "kept.v"))
    design.staging_list.addItem(_make_item("rtl", "discarded.v"))
    design.staging_list.selectAll()
    design.remove_selected_button.click()
    assert design.staging_list.count() == 0 and design.loaded_list.count() == 1


def test_both_tabs_put_their_diagnostics_dock_in_the_same_place(qtbot):
    design, power_intent = LoadDesignTab(), LoadPowerIntentTab()
    qtbot.addWidget(design)
    qtbot.addWidget(power_intent)

    for tab in (design, power_intent):
        assert isinstance(tab.diagnostics_dock, DiagnosticsDock)
        assert tab.dockWidgetArea(tab.diagnostics_dock) == Qt.DockWidgetArea.BottomDockWidgetArea
        assert tab.diagnostics_dock.isVisible() is False  # hidden until a warning/error appears


def test_both_tabs_gate_their_primary_action_behind_an_accented_button_and_a_continue(qtbot):
    design, power_intent = LoadDesignTab(), LoadPowerIntentTab()
    qtbot.addWidget(design)
    qtbot.addWidget(power_intent)

    assert design.elaborate_button.property("accent") is True
    assert power_intent.mapping_button.property("accent") is True
    for tab in (design, power_intent):
        assert tab.continue_button.property("accent") is True
        assert tab.continue_button.isEnabled() is False  # until the action succeeds


# --- no feature loss -------------------------------------------------------


def test_every_load_power_intent_specific_control_survived_the_rework(qtbot):
    """The request was explicit that matching the layout must not remove
    anything. These are the controls that existed before it."""
    tab = LoadPowerIntentTab()
    qtbot.addWidget(tab)

    assert tab.load_files_button.text() == "Load UPF Files..."  # UPF only now -- CPF is not mapped
    assert tab.mapping_button.text() == "Mapping"
    assert tab.create_button.text() == "Create New"
    assert tab.remove_selected_button.text() == "Remove Selected"
    assert tab.continue_button.text() == "Continue"
    assert tab.progress_bar is not None
    assert tab.progress_bar.isVisible() is False
    assert tab.files_list is not None


def test_the_surviving_controls_are_still_wired(qtbot, monkeypatch):
    """Present-but-disconnected would pass the test above and still be a
    regression, so each control is driven and its effect observed."""
    from PySide6.QtWidgets import QFileDialog

    from frontend.src.shared.widgets import message_box

    tab = LoadPowerIntentTab()
    qtbot.addWidget(tab)

    monkeypatch.setattr(QFileDialog, "getOpenFileNames", staticmethod(lambda *a, **k: (["picked.upf"], "")))
    tab.load_files_button.click()
    assert tab.staging_list.count() == 1  # browse -> staging

    tab.add_button.click()
    assert tab.files_list.count() == 1  # Add -> loaded

    # "Create New" produces a power intent design without any mapping run.
    created = []
    tab.powerIntentLoaded.connect(lambda d: created.append(d))
    tab.create_button.click()
    assert len(created) == 1

    # Mapping with an empty Loaded list is rejected, not silently ignored.
    tab.files_list.clear()
    shown = []
    monkeypatch.setattr(message_box, "critical", lambda *a, **k: shown.append(a))
    tab.mapping_button.click()
    assert shown


def test_the_compatibility_note_is_present_and_says_the_required_things(qtbot):
    """Section 2: wording may be refined, but the substance must be there
    -- scoped subset, may not be fully compatible, review diagnostics."""
    tab = LoadPowerIntentTab()
    qtbot.addWidget(tab)

    assert tab.compatibility_note.text() == COMPATIBILITY_NOTE
    assert tab.compatibility_note.wordWrap() is True
    lowered = COMPATIBILITY_NOTE.lower()
    assert "subset" in lowered
    assert "not the full specification" in lowered
    assert "diagnostic" in lowered


def test_the_compatibility_note_sits_above_the_panes(qtbot):
    """Informational content at the top, diagnostics at the bottom --
    the same relative placement Load Design uses for its own."""
    tab = LoadPowerIntentTab()
    qtbot.addWidget(tab)

    central = tab.centralWidget()
    layout = central.layout()
    assert layout.itemAt(0).widget() is tab.compatibility_note
    assert isinstance(layout.itemAt(1).widget(), QSplitter)
