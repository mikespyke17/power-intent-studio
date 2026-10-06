"""'Load Power Intent' tab.

Single QGroupBox, "Load Power Intent" (no more left/right split -- the
former "Create New" group was folded into this one on explicit request,
see below). "Load UPF Files..." (top-right corner) opens a
multi-file picker straight into `files_list` -- no separate
staging/commit step the way Load Design's RTL/LIB pane has one, since
there's no per-file "kind" to sort out here, just a flat list of power
intent files (entry point is the first one). "Remove Selected"
(bottom-left) removes whatever's selected in that list. Bottom-right
corner holds "Mapping" then "Create New", plain default-styled buttons
(an earlier light-blue/bold-red/oversized style was tried and explicitly
reverted).

"Mapping" is the *only* load-triggering action here: there is no
separate "load" trigger distinct from it. Clicking it performs the
load/elaborate step against `files_list` *and* the mapping step, back to
back, as a single action/single progress-bar run -- explicit product
decision, not an oversight: this tab's whole job is "get from picked
files to a mapped design," not "load, then separately decide to map." A
failed load (real power_intent is None) stops the pipeline there and
never attempts mapping. Clicking it with an empty `files_list` doesn't
attempt anything at all -- a critical() pops up explaining
there's nothing to map, rather than silently no-op'ing (the previous
behavior) or proceeding into a meaningless mock/real call.

UPF ONLY (explicit request): CPF is no longer a Load Power Intent
format -- many more teams use UPF, and mapping effort goes there. The
picker offers .upf only, and a .cpf entry file is refused with a
diagnostic rather than half-read. (CPF EXPORT is unaffected.)

"Create New" no longer opens the format/version picker dialog it used
to -- explicit request: go straight to 'Visualize Design' on click, with
format/version selection deferred to whenever a real "extract"/export
step exists (see backend/export/'s own status in CLAUDE.md -- that
piece doesn't exist yet, so this creates a fixed-default
PowerIntentDesign(UPF, "3.1") for now, UPF 3.1 being this project's own
already-established canonical target -- see
docs/milestone2_powerintent_elaborator.md). Deliberately *not* run
through Mapping (nothing to map yet against an empty power intent, and
Visualize Design has no power-intent awareness at all; see
hierarchy_view/visualize_design_tab.py's own module docstring chain).

continueRequested fires once, from whichever of Mapping/Create New
actually succeeds (never on failure/empty-list/cancel) --
app/main_window.py's own _on_load_power_intent_continue() is the only
listener, navigating to 'Visualize Design' and disabling this tab,
one-shot. A single shared signal for two different success paths (not
two separate ones) because both mean exactly the same thing to
main_window.py: "this tab's job is done, move on."

Mapping's own success is conditional on whether it produced any
warning/error diagnostic (Create New never has this question -- no
load/mapping step runs at all, so it always emits immediately, same as
before): a *clean* Mapping still auto-navigates right away, same fast
path as always. But an explicit request found and fixed a real, non-
obvious bug here: auto-navigating unconditionally meant this tab's own
"Power Intent Mapping Diagnostic" dock (see below) would auto-show for
a diagnostics-containing Mapping and then *immediately* become
inaccessible -- the tab it lives in gets disabled and switched away
from in the very same tick, so the user could never actually see it,
not even by reopening it from the View menu afterward (its whole
containing window is hidden once the tab is switched away). Mirrors
LoadDesignTab's own already-established "stay until Continue" gate for
the identical reason: when Mapping produces a warning/error,
`continue_button` (new, disabled until this moment) becomes the manual
trigger instead of continueRequested firing automatically.

createdWithoutMapping is Create New's own, separate signal (never fired
by Mapping) -- app/main_window.py wires it to
PowerIntentTab.show_diagram_without_mapping(), since Create New's
success needs a genuinely different reaction from Mapping's: Mapping's
mappingCompleted already makes the Power Intent tab's Diagram visible
(it has a real MappedDesign to build the overlay from), but Create New
never produces one at all (there's nothing to map between the RTL
design and a power intent that was just created empty) -- without this
signal, that tab's Diagram would stay stuck on its "Run Mapping..."
placeholder forever after a Create New, since no Mapping click is ever
coming on this path.

By default (`load_action=None`, `mapping_action=None`), the combined
Mapping action returns mock_backend's canned data regardless of which
files were picked -- the mocked boundary Front-End Milestones 1-2 were
built against, unchanged. This default is what the front-end test suite
builds against; `power_intent_studio.py` itself no longer uses it.

`load_action`/`mapping_action`, when given, are the real-backend wiring
hooks (see docs/backend_integration.md), passed by default from
`frontend/src/app/main.py`'s own `main()` -- making this
`power_intent_studio.py`'s actual, standard behavior (an earlier separate
`power_intent_studio_validation_harness.py` prototype that used to be
the only caller has been retired -- its wiring is now there).
`load_action` takes the real picked entry-point file and a format
(always "upf" -- see the UPF ONLY note above), returning (PowerIntentDesign
or None, diagnostics). `mapping_action` takes no arguments -- Mapping
needs the *real* backend Design/PowerIntentDesign from the Load steps,
not the adapted ones this tab holds, so the closure built in
`real_actions.py` carries that state itself rather than threading it
back through here. Both switch the progress bar to indeterminate mode
instead of the mock path's fake step animation, same reasoning as
LoadDesignTab.
"""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from frontend.src.diagnostics.diagnostics_dock import DiagnosticsDock
from frontend.src.power_intent_view.power_intent_import import (
    DIAGNOSTIC_SOURCE as IMPORT_DIAGNOSTIC_SOURCE,
    ImportedPowerIntent,
    import_power_intent,
)
from frontend.src.shared.widgets import message_box
from frontend.src.shared.mock_backend import (
    build_mock_mapped_design,
    build_mock_power_intent,
    build_mock_power_intent_diagnostics,
    build_mock_validation_report,
    finding_to_diagnostic,
)
from frontend.src.shared.models.diagnostics import Diagnostic
from frontend.src.shared.models.mapping import MappedDesign
from frontend.src.shared.models.power_intent import PowerIntentDesign, SourceFormat
from frontend.src.shared.models.rtl import Design
from frontend.src.shared.models.validation import Severity

_MOCK_PROGRESS_STEPS = 10
_MOCK_PROGRESS_INTERVAL_MS = 80

_POWER_INTENT_FILE_FILTER = "UPF Files (*.upf)"

# Explicit request, section 2. Substance that must survive any rewording:
# support is a deliberately-scoped SUBSET, an external file may not map
# cleanly, and the Diagnostic section is where to check. The exact set of
# constructs this refers to is not prose -- see supported_features.py,
# which is the machine-readable source the importer classifies against.
COMPATIBILITY_NOTE = (
    "This tool supports a common, deliberately-scoped subset of UPF rules and flags -- not the full "
    "specification. Mapping an externally-authored file may not be 100% compatible; a command or option "
    "that is not recognized is skipped and reported with its UPF line. Review the Diagnostic section "
    "carefully after loading."
)

# Same severities DiagnosticsDock auto-shows on -- a clean Mapping (no
# diagnostic at either severity) still auto-navigates immediately; see
# _finish_success()'s own docstring for why this one is more than that.
_BLOCKING_SEVERITIES = {Severity.ERROR, Severity.WARNING}

_DEFAULT_CREATE_FORMAT = SourceFormat.UPF
_DEFAULT_CREATE_VERSION = "3.1"

LoadAction = Callable[[str, str], tuple[Optional[PowerIntentDesign], list[Diagnostic]]]
MappingAction = Callable[[], tuple[Optional[MappedDesign], list[Diagnostic]]]
# Section 7: whether the Power Intent tab's diagram has unsaved edits.
# A callable rather than a direct reference because this tab must not
# know about PowerIntentTab -- app/main_window.py owns both and wires
# them; omitted (None) means "never dirty", which is what every mock/test
# construction wants.
DirtyCheck = Callable[[], bool]

_RELOAD_TITLE = "Replace Imported Power Intent"
_RELOAD_MESSAGE = (
    "The Power Intent diagram has unsaved edits. Loading a power intent file replaces the current "
    "diagram state entirely -- those edits will be discarded.\n\nContinue?"
)

_ACKNOWLEDGE_TITLE = "Proceed With Findings"


# Load Power Intent maps UPF only -- see the module docstring.
SOURCE_FORMAT = "upf"
CPF_NOT_SUPPORTED_CODE = "CPF-NOT-SUPPORTED"


def _cpf_refusal(entry_file: str) -> Optional[Diagnostic]:
    """An ERROR for a .cpf entry file, or None. CPF is not mapped; the file
    is refused before anything reads it."""
    if not entry_file.lower().endswith(".cpf"):
        return None
    return Diagnostic(
        severity=Severity.ERROR,
        code=CPF_NOT_SUPPORTED_CODE,
        message=f"'{entry_file}' is a CPF file. Load Power Intent maps UPF only.",
        file=entry_file,
        source="load_power_intent",
    )


class LoadPowerIntentTab(QMainWindow):
    powerIntentLoaded = Signal(PowerIntentDesign)
    mappingCompleted = Signal(MappedDesign)
    # Two signals, not one -- this tab covers two distinct phases (load,
    # mapping), and the diagnostics dock needs to know which one a given
    # batch replaces (DiagnosticsTableModel.set_diagnostics() clears by
    # source; inferring the source from the batch itself breaks on an
    # empty batch, e.g. a mapping run with zero findings).
    loadDiagnosticsReady = Signal(list)  # list[Diagnostic]
    mappingDiagnosticsReady = Signal(list)  # list[Diagnostic]
    continueRequested = Signal()  # Mapping or Create New succeeded -- see module docstring
    createdWithoutMapping = Signal()  # Create New specifically -- see its own docstring below
    # The imported diagram state (an ImportedPowerIntent), emitted after
    # mapping succeeds. `object`, not a concrete type, because it is a
    # plain dataclass rather than a QObject -- Signal(ImportedPowerIntent)
    # would need a registered meta-type.
    powerIntentImported = Signal(object)

    def __init__(
        self,
        load_action: Optional[LoadAction] = None,
        mapping_action: Optional[MappingAction] = None,
        parent: Optional[QWidget] = None,
        dirty_check: Optional[DirtyCheck] = None,
    ):
        super().__init__(parent)
        self._rtl: Optional[Design] = None
        self._power_intent: Optional[PowerIntentDesign] = None
        self._load_action = load_action
        self._mapping_action = mapping_action
        self._dirty_check = dirty_check
        self._imported: Optional[ImportedPowerIntent] = None
        self._has_mapped_once = False
        self._last_diagnostics: list[Diagnostic] = []

        # --- Left: "Power Intent Files" (staging) ----------------------
        # Layout rework (explicit request): this tab now mirrors
        # LoadDesignTab's own two-pane structure exactly -- a staging
        # pane whose picks are committed via "Add", and a "Loaded" pane
        # holding what the action button actually reads. Nothing was
        # removed to achieve it; every pre-existing control is still
        # here, just positioned the way its Load Design counterpart is.
        #
        # `files_list` deliberately keeps its name and its meaning (the
        # LOADED list, what Mapping reads) rather than being renamed to
        # match the new pane it sits in -- every existing caller and test
        # that seeds it directly stays correct, and it is the genuine
        # analogue of LoadDesignTab's own `loaded_list`.
        self.staging_list = QListWidget()
        self.staging_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)

        self.load_files_button = QPushButton("Load UPF Files...")
        self.load_files_button.clicked.connect(self._add_files)

        self.remove_selected_button = QPushButton("Remove Selected")
        self.remove_selected_button.clicked.connect(self._remove_selected)

        self.add_button = QPushButton("Add")
        self.add_button.clicked.connect(self._add_staged_files)

        # --- Right: "Loaded" -------------------------------------------
        self.files_list = QListWidget()
        self.files_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)

        self.remove_loaded_button = QPushButton("Remove Selected")
        self.remove_loaded_button.clicked.connect(self._remove_loaded_selected)

        self.mapping_button = QPushButton("Mapping")
        # Audit fix (category 03): the accent style hook reached only
        # the Power State Table's two buttons, leaving the app's MOST
        # primary actions on the default look. `accent` is a cosmetic
        # property only -- no behaviour attached, see theme.py.
        self.mapping_button.setProperty("accent", True)
        self.mapping_button.clicked.connect(self._start_mapping)

        self.create_button = QPushButton("Create New")
        self.create_button.clicked.connect(self._new_power_intent)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, _MOCK_PROGRESS_STEPS)
        self.progress_bar.setVisible(False)

        # Disabled until a Mapping run that produced a warning/error
        # diagnostic completes -- see _finish_success()'s own docstring.
        # A clean Mapping (or Create New, which never has diagnostics at
        # all) never touches this at all -- continueRequested still
        # fires immediately for those, same fast path as always.
        self.continue_button = QPushButton("Continue")
        self.continue_button.setProperty("accent", True)
        self.continue_button.setEnabled(False)
        self.continue_button.clicked.connect(self._on_continue_clicked)

        # Compatibility note (explicit request): permanently visible, at
        # the top of the tab. This tool implements a deliberately-scoped
        # subset of UPF (see supported_features.py for the exact,
        # inspectable list), so an externally-authored file may use
        # constructs it doesn't model -- the note says so before the user
        # loads anything, rather than leaving it to be discovered in the
        # Diagnostic section afterwards.
        self.compatibility_note = QLabel(COMPATIBILITY_NOTE)
        self.compatibility_note.setWordWrap(True)
        self.compatibility_note.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        note_font = self.compatibility_note.font()
        note_font.setItalic(True)
        self.compatibility_note.setFont(note_font)

        files_box = QGroupBox("Power Intent Files")
        files_layout = QVBoxLayout(files_box)
        top_buttons_row = QHBoxLayout()
        top_buttons_row.addStretch(1)
        top_buttons_row.addWidget(self.load_files_button)
        files_layout.addLayout(top_buttons_row)
        files_layout.addWidget(self.staging_list)
        staging_bottom_row = QHBoxLayout()
        staging_bottom_row.addWidget(self.remove_selected_button)
        staging_bottom_row.addStretch(1)
        staging_bottom_row.addWidget(self.add_button)
        files_layout.addLayout(staging_bottom_row)

        loaded_box = QGroupBox("Loaded")
        loaded_layout = QVBoxLayout(loaded_box)
        loaded_top_row = QHBoxLayout()
        loaded_top_row.addStretch(1)
        loaded_top_row.addWidget(self.remove_loaded_button)
        loaded_layout.addLayout(loaded_top_row)
        loaded_layout.addWidget(QLabel("UPF files (entry point is the first file)"))
        loaded_layout.addWidget(self.files_list)
        action_row = QHBoxLayout()
        action_row.addWidget(self.mapping_button)
        action_row.addWidget(self.create_button)
        loaded_layout.addLayout(action_row)
        loaded_layout.addWidget(self.progress_bar)
        loaded_layout.addWidget(self.continue_button)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(files_box)
        splitter.addWidget(loaded_box)

        # The note sits ABOVE the splitter, mirroring where LoadDesignTab
        # puts its own informational content relative to its panes; the
        # Diagnostic dock sits at the bottom, exactly where LoadDesignTab
        # puts its own. Wrapped in a plain container because a QMainWindow
        # takes a single central widget.
        central = QWidget()
        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.addWidget(self.compatibility_note)
        central_layout.addWidget(splitter, stretch=1)
        self.setCentralWidget(central)

        # Explicit request: a dedicated "Power Intent Mapping Diagnostic"
        # dock, part of this tab -- covers both phases this tab runs
        # (load, then mapping -- see class docstring), same single-dock-
        # per-tab shape LoadDesignTab's own dock has. Hidden by default,
        # auto-shown by set_diagnostics() the moment either phase's own
        # diagnostics include a warning or error.
        self.diagnostics_dock = DiagnosticsDock("Power Intent Mapping Diagnostic")
        self.diagnostics_dock.show_for_tab("load_power_intent")
        self.diagnostics_dock.setVisible(False)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.diagnostics_dock)

        self._progress_timer = QTimer(self)
        self._progress_timer.timeout.connect(self._on_progress_tick)
        self._progress_value = 0

    def set_rtl(self, rtl: Design) -> None:
        self._rtl = rtl

    def _add_files(self) -> None:
        """Picks land in the STAGING list now, not straight into Loaded --
        the same Browse -> stage -> "Add" flow LoadDesignTab established."""
        files, _ = QFileDialog.getOpenFileNames(self, "Select UPF files", "", _POWER_INTENT_FILE_FILTER)
        self.staging_list.addItems(files)

    def _add_staged_files(self) -> None:
        """Commits every staged pick into "Loaded" and clears staging --
        a move, not a copy, matching LoadDesignTab's own "Add"."""
        while self.staging_list.count():
            self.files_list.addItem(self.staging_list.takeItem(0).text())

    def _remove_selected(self) -> None:
        """Acts on STAGING (discard an unwanted pick before committing it)
        -- the loaded pane has its own separate button, same split
        LoadDesignTab uses."""
        for item in self.staging_list.selectedItems():
            self.staging_list.takeItem(self.staging_list.row(item))

    def _remove_loaded_selected(self) -> None:
        for item in self.files_list.selectedItems():
            self.files_list.takeItem(self.files_list.row(item))

    def _start_mapping(self) -> None:
        if self.files_list.count() == 0:
            message_box.critical(self, "Power Intent Not Loaded", "No Power Intent files have been loaded -- there is nothing to map.")
            return
        # Section 7: a RE-load replaces the whole diagram state. Ask only
        # when there is actually something to lose -- a first load has no
        # prior state, and an un-edited one has nothing the user authored.
        # Deliberately a single yes/no, not a diff or a merge: section 7
        # is explicit that a full reload/replace is the behaviour, so the
        # only decision available is whether to go ahead.
        if self._has_mapped_once and self._dirty_check is not None and self._dirty_check():
            reply = message_box.question(
                self,
                _RELOAD_TITLE,
                _RELOAD_MESSAGE,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,  # discarding work is never the default
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        self.mapping_button.setEnabled(False)
        self.progress_bar.setVisible(True)
        if self._load_action is None and self._mapping_action is None:
            self._progress_value = 0
            self.progress_bar.setRange(0, _MOCK_PROGRESS_STEPS)
            self.progress_bar.setValue(0)
            self._progress_timer.start(_MOCK_PROGRESS_INTERVAL_MS)
        else:
            self.progress_bar.setRange(0, 0)  # indeterminate -- see class docstring
            self._finish_real_pipeline()

    def _on_progress_tick(self) -> None:
        self._progress_value += 1
        self.progress_bar.setValue(self._progress_value)
        if self._progress_value >= _MOCK_PROGRESS_STEPS:
            self._progress_timer.stop()
            self._finish_mock_pipeline()

    def _finish_mock_pipeline(self) -> None:
        self.mapping_button.setEnabled(True)
        self.progress_bar.setVisible(False)
        self._power_intent = build_mock_power_intent()
        load_diagnostics = build_mock_power_intent_diagnostics()
        self.loadDiagnosticsReady.emit(load_diagnostics)
        self.diagnostics_dock.set_diagnostics("load_power_intent", load_diagnostics)
        self.powerIntentLoaded.emit(self._power_intent)

        assert self._rtl is not None  # guaranteed by tab-enablement gating in app/main_window.py
        mapped = build_mock_mapped_design(self._power_intent, self._rtl)
        report = build_mock_validation_report(mapped)
        diagnostics: list[Diagnostic] = [finding_to_diagnostic(f) for f in report.findings]
        self.mappingDiagnosticsReady.emit(diagnostics)
        self.diagnostics_dock.set_diagnostics("mapping", diagnostics)
        self.mappingCompleted.emit(mapped)
        import_diagnostics = self._run_import(self._power_intent, mapped, "")
        self._finish_success(load_diagnostics + diagnostics + import_diagnostics)

    def _finish_real_pipeline(self) -> None:
        self.mapping_button.setEnabled(True)
        self.progress_bar.setVisible(False)
        entry_file = self.files_list.item(0).text()
        refusal = _cpf_refusal(entry_file)
        if refusal is not None:
            self.loadDiagnosticsReady.emit([refusal])
            self.diagnostics_dock.set_diagnostics("load_power_intent", [refusal])
            return
        power_intent, load_diagnostics = self._load_action(entry_file, SOURCE_FORMAT)
        self.loadDiagnosticsReady.emit(load_diagnostics)
        self.diagnostics_dock.set_diagnostics("load_power_intent", load_diagnostics)
        if power_intent is None:
            return  # load failed -- never attempt mapping against nothing
        self._power_intent = power_intent
        self.powerIntentLoaded.emit(power_intent)

        mapped, mapping_diagnostics = self._mapping_action()
        self.mappingDiagnosticsReady.emit(mapping_diagnostics)
        self.diagnostics_dock.set_diagnostics("mapping", mapping_diagnostics)
        if mapped is None:
            return
        self.mappingCompleted.emit(mapped)
        import_diagnostics = self._run_import(power_intent, mapped, entry_file)
        self._finish_success(load_diagnostics + mapping_diagnostics + import_diagnostics)

    def _run_import(
        self,
        power_intent: PowerIntentDesign,
        mapped: MappedDesign,
        entry_file: str,
    ) -> list[Diagnostic]:
        """Translate the elaborated+mapped power intent into diagram
        state and hand it on (the 'Load Power Intent' mapping feature).

        Runs as a third phase after load and mapping, on BOTH the mock
        and real paths -- the translation reads only this tab's own
        already-adapted frontend models, so there is no separate real-
        backend hook for it and the mock path exercises the identical
        code. Its findings go to the dock under their own source key
        (see power_intent_import.DIAGNOSTIC_SOURCE) so they sit
        alongside the load/mapping batches instead of replacing one.

        A failure here is reported, not raised: the mapping itself has
        already succeeded and been emitted, so a translation bug must
        not take the whole run down with it."""
        assert self._rtl is not None  # same tab-enablement guarantee mapping relies on
        try:
            imported = import_power_intent(power_intent, self._rtl, mapped, entry_file)
        except Exception as exc:  # noqa: BLE001 -- see docstring
            diagnostics = [
                Diagnostic(
                    severity=Severity.ERROR,
                    code="POWER-INTENT-IMPORT-FAILED",
                    message=f"Could not translate the loaded power intent into the diagram: {exc}",
                    source=IMPORT_DIAGNOSTIC_SOURCE,
                )
            ]
            self.diagnostics_dock.set_diagnostics(IMPORT_DIAGNOSTIC_SOURCE, diagnostics)
            return diagnostics
        self._imported = imported
        self.diagnostics_dock.set_diagnostics(IMPORT_DIAGNOSTIC_SOURCE, imported.diagnostics)
        self._has_mapped_once = True
        self.powerIntentImported.emit(imported)
        return list(imported.diagnostics)

    def _on_continue_clicked(self) -> None:
        """`continue_button` is only ever enabled when the run produced a
        warning or error (see _finish_success()), so reaching here means
        the user is choosing to proceed past findings they have had the
        chance to read in the dock.

        Section 4 asks for an explicit acknowledgment at exactly that
        moment. Reuses the app's own confirmation convention
        (message_box.question, defaulting to No) rather than a bespoke
        dialog, and names the counts so the acknowledgment is about
        something specific rather than a generic 'are you sure'."""
        counts = self._finding_counts()
        if counts:
            reply = message_box.question(
                self,
                _ACKNOWLEDGE_TITLE,
                f"The power intent was loaded with {counts}.\n\n"
                "Unrecognized rules and unresolved references are NOT present in the diagram, so what "
                "you see may be incomplete. Review the Diagnostic section before continuing.\n\n"
                "Proceed anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        self.continueRequested.emit()

    def _finding_counts(self) -> str:
        """"2 errors and 1 warning", or "" when the last run was clean.

        Counted from the batch `_finish_success()` was given rather than
        read back out of the dock -- the dock keeps its findings keyed by
        source for display and has no accessor, and this is the same list
        that decided whether `continue_button` was enabled at all, so the
        message can never disagree with the gate."""
        diagnostics = self._last_diagnostics
        errors = sum(1 for d in diagnostics if d.severity is Severity.ERROR)
        warnings = sum(1 for d in diagnostics if d.severity is Severity.WARNING)
        parts = []
        if errors:
            parts.append(f"{errors} error{'s' if errors != 1 else ''}")
        if warnings:
            parts.append(f"{warnings} warning{'s' if warnings != 1 else ''}")
        return " and ".join(parts)

    def _finish_success(self, all_diagnostics: list[Diagnostic]) -> None:
        """A clean Mapping (no warning/error in either phase's own
        diagnostics) auto-navigates immediately via continueRequested,
        same fast path as always. Otherwise -- a real bug, found via
        live use: auto-navigating unconditionally disabled and switched
        away from this tab in the very same tick a warning/error had
        just made "Power Intent Mapping Diagnostic" auto-show, making it
        instantly inaccessible (not even reachable again via the View
        menu afterward, since its whole containing tab was hidden) --
        stays put and enables `continue_button` instead, mirroring
        LoadDesignTab's own already-established "stay until Continue"
        gate for the identical reason."""
        self._last_diagnostics = list(all_diagnostics)
        if any(d.severity in _BLOCKING_SEVERITIES for d in all_diagnostics):
            self.continue_button.setEnabled(True)
        else:
            self.continueRequested.emit()

    def _new_power_intent(self) -> None:
        self._power_intent = PowerIntentDesign(source_format=_DEFAULT_CREATE_FORMAT, source_version=_DEFAULT_CREATE_VERSION)
        self.powerIntentLoaded.emit(self._power_intent)
        # Distinct from Mapping's own mappingCompleted: there is no
        # MappedDesign here at all (Create New never runs a mapping step
        # between the RTL design and this freshly-created, empty power
        # intent -- see module docstring), but the Power Intent tab's
        # Diagram must still become visible rather than staying stuck on
        # its "Run Mapping..." placeholder forever, since there's never
        # going to be a Mapping click on this path. See
        # app/main_window.py's own wiring and
        # PowerIntentDiagramView.show_bare_hierarchy()'s docstring.
        self.createdWithoutMapping.emit()
        self.continueRequested.emit()
