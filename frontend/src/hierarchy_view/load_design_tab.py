"""'Load Design' tab.

Layout: a horizontal QSplitter holding two QGroupBoxes, each internally
laid out vertically.

- Left, "Design Files": "Load RTL Files..."/"Load LIB Files..." in the
  top-right corner (labels only -- the underlying attributes are still
  named browse_rtl_button/browse_lib_button, unchanged, since nothing
  about what they *do* changed), a staging QListWidget in the center
  (RTL and .lib picks share one list -- each item tracks its own kind as
  item data, shown as a "[RTL]"/"[LIB]" prefix so the mix stays
  readable), "Remove Selected" bottom-left, and an "Add" button
  bottom-right that transfers every staged item into the right pane's
  "Loaded" list (moves, not copies -- the staging list clears, matching
  "Remove Selected"'s job of discarding unwanted staged picks *before*
  committing them, not after). The file-picker dialogs are filtered to
  the expected extensions (RTL: .v/.sv/.vhd/.vhdl; LIB: .lib) -- picking
  an unrelated file type isn't possible through them.
- Right, "Loaded": its own "Remove Selected" (top-right, acts on the
  Loaded list -- a separate button/list from the left pane's, which acts
  on staging), the committed file list, a Top Module field, the
  "Elaborate" action + progress bar (reading from the *Loaded* list, not
  the staging one), and "Continue" at the bottom -- disabled until a
  successful elaborate.

"Elaborate" is gated by two pre-flight checks, each a QMessageBox popup
(not a diagnostics-dock entry -- these mean elaboration was never
attempted at all, not that it ran and found something) rather than a
silent no-op, and both apply in mock mode too, not just when a real
`elaborate_action` is wired in -- these are about whether there's
anything meaningful to elaborate at all, not about what the real backend
specifically needs: (1) the *Loaded* list must be non-empty -- "No
design has been loaded"; (2) Top Module must be non-blank -- "Please
provide a top module name." Real elaboration genuinely also supports
leaving top unspecified (pyslang auto-detects the sole top-level
instance -- see backend/api/'s elaborate_rtl), but the GUI doesn't
expose that path; the user must name a module explicitly, which also
covers the real, verified workflow of compiling one DUT submodule out of
a larger loaded design (see
backend/elaborator/rtl/verilog/elaborate.py's _make_compilation).

By default (`elaborate_action=None`), once past those two gates,
"Elaborate" returns mock_backend's canned Design/diagnostics regardless
of which specific files were actually loaded or named as top -- this is
the mocked boundary Front-End Milestones 1-2 were built against,
unchanged, per CLAUDE.md's milestone-independence rule. This default is
what the front-end test suite builds `LoadDesignTab`/`MainWindow`
against; `power_intent_studio.py` itself no longer uses it -- see below.

`elaborate_action`, when given, is the real-backend wiring hook (see
docs/backend_integration.md) -- a callable taking the real loaded file
paths (split back into rtl/lib lists by their tracked kind) and
top-module text, returning (Design or None on failure, diagnostics).
`frontend/src/app/main.py`'s own `main()` passes this by default, making
it `power_intent_studio.py`'s actual, standard behavior (an earlier
separate `power_intent_studio_validation_harness.py` prototype that used
to be the only caller has been retired -- its wiring is now here).
When it's provided, the progress bar switches to indeterminate mode (no
fake step-by-step animation -- there's no discrete progress to report
for a real, synchronous elaboration call).

A successful "Elaborate" enables "Continue" (and, in app/main_window.py,
the Visualize Design/Load Power Intent tabs) but does *not* navigate
anywhere by itself -- `designLoaded` fires immediately so downstream tabs
are populated and ready, but the user stays on "Load Design" until they
explicitly click "Continue" (`continueRequested`). This is deliberate:
Elaborate can succeed with *warnings* (not just cleanly), and the
diagnostics dock's "Load Design"/"Visualize Design" tabs share the same
diagnostics (see diagnostics/diagnostics_dock.py) -- forcing an immediate
jump away would rush past exactly the moment those warnings are visible.
Only on "Continue" does app/main_window.py switch to "Visualize Design"
and disable "Load Design" -- see MainWindow's own docstring for that
behavior; not this module's job to enforce.
"""

from __future__ import annotations

import inspect

from typing import Callable, Optional

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from frontend.src.diagnostics.diagnostics_dock import DiagnosticsDock
from frontend.src.hierarchy_view.filelist import (
    FilelistError,
    merge_defines,
    parse_defines_field,
    parse_filelist,
)
from frontend.src.shared.widgets import message_box
from frontend.src.shared.mock_backend import build_mock_design, build_mock_rtl_diagnostics
from frontend.src.shared.models.diagnostics import Diagnostic
from frontend.src.shared.models.validation import Severity
from frontend.src.shared.models.rtl import Design

_MOCK_PROGRESS_STEPS = 10
_MOCK_PROGRESS_INTERVAL_MS = 80

_KIND_ROLE = Qt.ItemDataRole.UserRole
_PATH_ROLE = Qt.ItemDataRole.UserRole + 1

_RTL_FILE_FILTER = "RTL Files (*.v *.sv *.vhd *.vhdl)"
_LIB_FILE_FILTER = "Liberty Files (*.lib)"
_FILELIST_FILTER = "RTL Filelists (*.f *.F *.flist)"
_LIB_EXTENSIONS = (".lib",)

# (rtl_files, lib_files, top, defines, include_dirs) -> (design, diagnostics).
# `defines` maps a name to its value, or to None for a bare define;
# `include_dirs` are `+incdir+` search paths. Both are passed positionally
# so an existing 3-argument implementation keeps working -- see
# _call_elaborate_action() for the compatibility shim and why it exists.
ElaborateAction = Callable[..., tuple[Optional[Design], list[Diagnostic]]]


def _shown(value: Optional[str]) -> str:
    """A define's value as it reads in a message. A BARE define has no
    value at all, which is a different thing from an empty one, so the
    two do not render the same."""
    return "no value" if value is None else f"'{value}'"


def _call_elaborate_action(action, rtl_files, lib_files, top, defines, include_dirs):
    """Call `action`, passing defines/include dirs only if it takes them.

    Every pre-existing ElaborateAction -- the mock one, and every one a
    test supplies -- takes exactly three arguments. Widening the
    protocol outright would break all of them at once, for a feature
    most of them do not care about, so the two new arguments are passed
    only when the callable's own signature has room.

    Deliberately NOT a `try: ... except TypeError:` around the 5-argument
    call: that would also swallow a genuine TypeError raised from INSIDE
    the action and silently retry with fewer arguments, which presents
    as "Elaborate did nothing" with the real error gone."""
    try:
        signature = inspect.signature(action)
    except (TypeError, ValueError):  # a builtin or C callable -- no signature to read
        return action(rtl_files, lib_files, top)
    parameters = list(signature.parameters.values())
    takes_extra = len(parameters) >= 5 or any(
        parameter.kind in (inspect.Parameter.VAR_KEYWORD, inspect.Parameter.VAR_POSITIONAL)
        for parameter in parameters
    )
    if takes_extra:
        return action(rtl_files, lib_files, top, defines, include_dirs)
    return action(rtl_files, lib_files, top)


def _make_item(kind: str, path: str) -> QListWidgetItem:
    item = QListWidgetItem(f"[{kind.upper()}] {path}")
    item.setData(_KIND_ROLE, kind)
    item.setData(_PATH_ROLE, path)
    return item


class LoadDesignTab(QMainWindow):
    designLoaded = Signal(Design)
    diagnosticsReady = Signal(list)  # list[Diagnostic]
    continueRequested = Signal()
    filelistLoaded = Signal(object)  # ParsedFilelist

    def __init__(self, elaborate_action: Optional[ElaborateAction] = None, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._elaborate_action = elaborate_action
        # `+define+`/`+incdir+` accumulated from every filelist loaded so
        # far. Kept separate from the manual field rather than folded
        # into it, so the two sources stay distinguishable -- which is
        # what makes a conflict reportable at all.
        self._filelist_defines: dict[str, Optional[str]] = {}
        self._filelist_include_dirs: list[str] = []

        # --- Left: "Design Files" -------------------------------------
        self.staging_list = QListWidget()
        self.staging_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)

        self.browse_rtl_button = QPushButton("Load RTL Files...")
        self.browse_rtl_button.clicked.connect(self._browse_rtl_files)
        self.browse_lib_button = QPushButton("Load LIB Files...")
        # Third button in this same row (confirmed placement): a filelist
        # names RTL sources, so it belongs with the other two ways of
        # naming them, feeding the same staging list -- "Add" then moves
        # the staged files to "Loaded" exactly as before.
        self.browse_filelist_button = QPushButton("Load Filelist (.f)...")
        self.browse_filelist_button.clicked.connect(self._browse_filelist)
        self.browse_lib_button.clicked.connect(self._browse_lib_files)
        self.remove_selected_button = QPushButton("Remove Selected")
        self.remove_selected_button.clicked.connect(self._remove_selected)

        self.load_button = QPushButton("Add")
        self.load_button.clicked.connect(self._load_staged_files)

        design_files_box = QGroupBox("Design Files")
        design_files_layout = QVBoxLayout(design_files_box)
        top_buttons_row = QHBoxLayout()
        top_buttons_row.addStretch(1)
        top_buttons_row.addWidget(self.browse_rtl_button)
        top_buttons_row.addWidget(self.browse_lib_button)
        top_buttons_row.addWidget(self.browse_filelist_button)
        design_files_layout.addLayout(top_buttons_row)
        design_files_layout.addWidget(self.staging_list)
        bottom_row = QHBoxLayout()
        bottom_row.addWidget(self.remove_selected_button)
        bottom_row.addStretch(1)
        bottom_row.addWidget(self.load_button)
        design_files_layout.addLayout(bottom_row)

        # --- Right: "Loaded" ---------------------------------------------
        self.loaded_list = QListWidget()
        self.loaded_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.remove_loaded_button = QPushButton("Remove Selected")
        self.remove_loaded_button.clicked.connect(self._remove_loaded_selected)
        # "DEFINE:" sits directly ABOVE "Top Module" (explicit request),
        # in the "Loaded" pane rather than across the top of the tab --
        # which puts it with the other two things Elaborate reads, in the
        # order it reads them: what to define, then what to elaborate.
        #
        # A PLAIN TEXT field with COMMAS as the separator -- a confirmed,
        # deliberate exception to this app's usual add/remove-row list
        # widget for multi-value input. A define list is short, is
        # usually pasted from a build script, and reads better as one
        # line than as a table.
        self.defines_edit = QLineEdit()
        self.defines_edit.setPlaceholderText("e.g. CPU, CORE=16, SRAM32x8 \u2014 comma-separated; NAME or NAME=VALUE")
        self.top_module_edit = QLineEdit()
        self.top_module_edit.setPlaceholderText("e.g. soc_top \u2014 the module to elaborate as the design root")
        self.elaborate_button = QPushButton("Elaborate")
        # Audit fix (category 03): the accent style hook reached only
        # the Power State Table's two buttons, leaving the app's MOST
        # primary actions on the default look. `accent` is a cosmetic
        # property only -- no behaviour attached, see theme.py.
        self.elaborate_button.setProperty("accent", True)
        self.elaborate_button.clicked.connect(self._start_elaborate)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, _MOCK_PROGRESS_STEPS)
        self.progress_bar.setVisible(False)
        self.continue_button = QPushButton("Continue")
        self.continue_button.setProperty("accent", True)
        self.continue_button.setEnabled(False)
        self.continue_button.clicked.connect(self.continueRequested.emit)

        loaded_box = QGroupBox("Loaded")
        loaded_layout = QVBoxLayout(loaded_box)
        loaded_top_row = QHBoxLayout()
        loaded_top_row.addStretch(1)
        loaded_top_row.addWidget(self.remove_loaded_button)
        loaded_layout.addLayout(loaded_top_row)
        loaded_layout.addWidget(self.loaded_list)
        defines_row = QHBoxLayout()
        defines_row.addWidget(QLabel("DEFINE:"))
        defines_row.addWidget(self.defines_edit)
        loaded_layout.addLayout(defines_row)
        top_module_row = QHBoxLayout()
        top_module_row.addWidget(QLabel("Top Module"))
        top_module_row.addWidget(self.top_module_edit)
        loaded_layout.addLayout(top_module_row)
        loaded_layout.addWidget(self.elaborate_button)
        loaded_layout.addWidget(self.progress_bar)
        loaded_layout.addWidget(self.continue_button)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(design_files_box)
        splitter.addWidget(loaded_box)

        self.setCentralWidget(splitter)

        # Explicit request: a dedicated "Load Design Diagnostic" dock,
        # part of this tab (not the old single MainWindow-level dock
        # switched by active tab -- see DiagnosticsDock's own updated
        # docstring), hidden by default -- set_diagnostics() below
        # auto-shows it the moment Elaborate's own diagnostics include a
        # warning or error, never auto-hides it again after that.
        self.diagnostics_dock = DiagnosticsDock("Load Design Diagnostic")
        self.diagnostics_dock.show_for_tab("load_design")
        self.diagnostics_dock.setVisible(False)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.diagnostics_dock)

        self._progress_timer = QTimer(self)
        self._progress_timer.timeout.connect(self._on_progress_tick)
        self._progress_value = 0

    # --- Design Files (left) ------------------------------------------

    def _browse_rtl_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(self, "Select RTL files", "", _RTL_FILE_FILTER)
        for f in files:
            self.staging_list.addItem(_make_item("rtl", f))

    def _browse_lib_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(self, "Select .lib files", "", _LIB_FILE_FILTER)
        for f in files:
            self.staging_list.addItem(_make_item("lib", f))

    def _browse_filelist(self) -> None:
        """"Load Filelist (.f)...": expand a filelist and stage every RTL
        source it names, so it joins the same staging -> "Add" -> Loaded
        flow the other two buttons already feed.

        Its `+define+`/`+incdir+` are kept on this tab (see
        `filelist_defines`/`filelist_include_dirs`) and handed to
        elaboration, combined with the "DEFINE:" field -- that combining
        is what `merge_defines()` does, and the manual field wins a
        genuine conflict (confirmed).

        An unreadable or CYCLIC filelist is reported and changes nothing
        -- partially staging a filelist that turned out to be broken
        would leave the tab in a state the user did not ask for."""
        path, _ = QFileDialog.getOpenFileName(self, "Select RTL filelist", "", _FILELIST_FILTER)
        if not path:
            return
        try:
            parsed = parse_filelist(path)
        except FilelistError as exc:
            message_box.critical(self, "Load Filelist Failed", str(exc))
            return

        self._filelist_defines.update(parsed.defines)
        for directory in parsed.include_dirs:
            if directory not in self._filelist_include_dirs:
                self._filelist_include_dirs.append(directory)
        for source in parsed.files:
            # `.lib` can legitimately appear in a filelist; classified
            # the same way the two browse buttons classify by extension,
            # rather than assuming every entry is RTL.
            kind = "lib" if source.lower().endswith(_LIB_EXTENSIONS) else "rtl"
            self.staging_list.addItem(_make_item(kind, source))

        self.filelistLoaded.emit(parsed)

    def _define_conflict_diagnostics(self) -> list[Diagnostic]:
        """One non-blocking WARNING per define given BOTH ways with
        different values, naming both values and which one won.

        Non-blocking deliberately: an override is something the user
        should SEE, not a reason to refuse to elaborate -- setting a
        define in the field precisely to override a filelist is a
        legitimate, everyday thing to do."""
        return [
            Diagnostic(
                severity=Severity.WARNING,
                code="DEFINE-OVERRIDE",
                message=(
                    f"Define '{name}' is set in both the filelist ({_shown(filelist_value)}) "
                    f"and the DEFINE: field ({_shown(manual_value)}). The DEFINE: field wins."
                ),
            )
            for name, filelist_value, manual_value in self.define_conflicts()
        ]

    def current_defines(self) -> dict[str, Optional[str]]:
        """Filelist defines combined with the "DEFINE:" field, manual
        winning a conflict. The combining rule lives in
        `filelist.merge_defines()` so it is testable without a widget."""
        merged, _ = merge_defines(self._filelist_defines, parse_defines_field(self.defines_edit.text()))
        return merged

    def define_conflicts(self) -> list[tuple[str, Optional[str], Optional[str]]]:
        """`(name, filelist_value, manual_value)` for each define given
        BOTH ways with different values -- reported as a non-blocking
        diagnostic at Elaborate time, so an override is never silent."""
        _, conflicts = merge_defines(self._filelist_defines, parse_defines_field(self.defines_edit.text()))
        return conflicts

    def _remove_selected(self) -> None:
        for item in self.staging_list.selectedItems():
            self.staging_list.takeItem(self.staging_list.row(item))

    def _load_staged_files(self) -> None:
        while self.staging_list.count() > 0:
            item = self.staging_list.takeItem(0)
            self.loaded_list.addItem(_make_item(item.data(_KIND_ROLE), item.data(_PATH_ROLE)))

    # --- Loaded (right) -------------------------------------------------

    def _remove_loaded_selected(self) -> None:
        for item in self.loaded_list.selectedItems():
            self.loaded_list.takeItem(self.loaded_list.row(item))

    def _loaded_paths_by_kind(self, kind: str) -> list[str]:
        return [
            self.loaded_list.item(i).data(_PATH_ROLE)
            for i in range(self.loaded_list.count())
            if self.loaded_list.item(i).data(_KIND_ROLE) == kind
        ]

    def _start_elaborate(self) -> None:
        # Both gates are pre-flight UI validation, not elaboration
        # results -- shown as a popup, not routed through diagnosticsReady
        # (the diagnostics dock is for findings *from* an elaboration
        # attempt; these two mean one was never attempted at all).
        # Applied in both mock and real mode -- unlike some other
        # per-field checks in this codebase, these two are about whether
        # there's anything meaningful to click "Elaborate" on at all, not
        # about what the real backend specifically requires, so mock mode
        # (elaborate_action=None) doesn't get an exemption from them.
        if self.loaded_list.count() == 0:
            message_box.critical(self, "No Design Loaded", "No design has been loaded -- add RTL/LIB files and click 'Add' before elaborating.")
            return
        if not self.top_module_edit.text().strip():
            # Real elaboration genuinely also supports leaving top
            # unspecified (pyslang auto-detects the sole top-level
            # instance -- see backend/api/'s elaborate_rtl), but the GUI
            # doesn't offer that path -- the user must name a module
            # explicitly, which also covers the real, verified workflow of
            # compiling one DUT submodule out of a larger loaded design
            # (see backend/elaborator/rtl/verilog/elaborate.py's
            # _make_compilation).
            message_box.critical(self, "Top Module Required", "Please provide a top module name before elaborating.")
            return
        self.elaborate_button.setEnabled(False)
        self.progress_bar.setVisible(True)
        if self._elaborate_action is None:
            self._progress_value = 0
            self.progress_bar.setRange(0, _MOCK_PROGRESS_STEPS)
            self.progress_bar.setValue(0)
            self._progress_timer.start(_MOCK_PROGRESS_INTERVAL_MS)
        else:
            self.progress_bar.setRange(0, 0)  # indeterminate -- see class docstring
            self._finish_real_elaborate()

    def _on_progress_tick(self) -> None:
        self._progress_value += 1
        self.progress_bar.setValue(self._progress_value)
        if self._progress_value >= _MOCK_PROGRESS_STEPS:
            self._progress_timer.stop()
            self._finish_mock_elaborate()

    def _finish_mock_elaborate(self) -> None:
        self.elaborate_button.setEnabled(True)
        self.progress_bar.setVisible(False)
        design = build_mock_design()
        diagnostics: list[Diagnostic] = build_mock_rtl_diagnostics()
        self.diagnosticsReady.emit(diagnostics)
        self.diagnostics_dock.set_diagnostics("load_design", diagnostics)
        self.continue_button.setEnabled(True)
        self.designLoaded.emit(design)

    def _finish_real_elaborate(self) -> None:
        self.elaborate_button.setEnabled(True)
        self.progress_bar.setVisible(False)
        # QLineEdit.text() returns "" for an empty field, never None -- but
        # backend.api.elaborate_rtl (and pyslang's _select_top_instance
        # underneath it) specifically checks `top is None` to trigger
        # auto-detection of the sole top-level instance. An empty string
        # bypasses that check entirely and searches for a top literally
        # named "", which never matches -- confirmed to produce a
        # confusing "top '' not found" error instead of either successful
        # auto-detection or VHDL's clearer "top is required" message.
        # Normalized here, once, at the UI boundary, rather than every
        # elaborate_action implementation having to remember to do it.
        top = self.top_module_edit.text().strip() or None
        design, diagnostics = _call_elaborate_action(
            self._elaborate_action,
            self._loaded_paths_by_kind("rtl"),
            self._loaded_paths_by_kind("lib"),
            top,
            self.current_defines(),
            list(self._filelist_include_dirs),
        )
        # Reported BEFORE the elaborator's own diagnostics, and never
        # fatal: an override is a thing the user should see, not a
        # reason to refuse to build.
        diagnostics = self._define_conflict_diagnostics() + list(diagnostics)
        self.diagnosticsReady.emit(diagnostics)
        self.diagnostics_dock.set_diagnostics("load_design", diagnostics)
        if design is not None:
            # Only on success -- a failed real elaborate() (design is
            # None) must not enable "Continue"; there's nothing to move
            # forward with.
            self.continue_button.setEnabled(True)
            self.designLoaded.emit(design)
