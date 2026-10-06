"""QDockWidget showing the diagnostics table, filtered to whichever
"tab" (in the show_for_tab()/TAB_SOURCES sense) it's scoped to.
Filtering is by *phase*, not literally by tab: Visualize Design shares
Load Design's diagnostics (same phase, nothing new happens on
switching), and Power Intent shares Load Power Intent's + Mapping's --
see docs/frontend_milestone2_power_intent_visualization.md.

Originally a single dock at the MainWindow level, switched between
phases via show_for_tab() as the active top-level tab changed. Now
(explicit request) there are two separate *instances*, each embedded
directly in its own owning tab (LoadDesignTab's "Load Design
Diagnostic", LoadPowerIntentTab's "Power Intent Mapping Diagnostic") --
each calls show_for_tab() exactly once, at construction, to lock itself
to one fixed phase-set forever; the switching machinery itself didn't
need to change, just how many instances exist and who owns them.
Visualize Design/Power Intent get no diagnostics dock of their own at
all (explicit request) -- the "share Load Design's/Load Power Intent's"
entries above are unused by any current caller, kept only because
removing them would understate what this class is generically capable
of.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtWidgets import QDockWidget, QHeaderView, QTableView, QWidget

from frontend.src.diagnostics.diagnostics_model import DiagnosticsTableModel
from frontend.src.shared.models.diagnostics import Diagnostic
from frontend.src.shared.models.validation import Severity

TAB_SOURCES = {
    "load_design": {"load_design"},
    "visualize_design": {"load_design"},
    "load_power_intent": {"load_power_intent", "mapping", "power_intent_import"},
    "power_intent": {"load_power_intent", "mapping", "power_intent_import"},
    "power_state_table": {"power_state_table"},
}

_AUTO_SHOW_SEVERITIES = {Severity.ERROR, Severity.WARNING}


class DiagnosticsDock(QDockWidget):
    """`title` (explicit request: "Load Design Diagnostic"/"Power Intent
    Mapping Diagnostic" for the two real, embedded instances) defaults
    to "Diagnostics" for any other caller. `title` also becomes this
    dock's own `toggleViewAction()` text for free (Qt derives it from
    the dock's windowTitle) -- that action is what the View menu adds
    (see app/main_window.py), so no separate action/text needs to be
    maintained here.

    set_diagnostics() auto-*shows* (never auto-hides -- see its own
    docstring) this dock the moment any diagnostic in the batch just
    set is an ERROR or WARNING (not EXCLUDED) -- explicit request:
    "by default can be hidden but appears if elaboration/mapping have
    warnings or errors"."""

    def __init__(self, title: str = "Diagnostics", parent: Optional[QWidget] = None):
        super().__init__(title, parent)
        self.model = DiagnosticsTableModel(self)
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.horizontalHeader().setStretchLastSection(False)
        # Bug fix (explicit request): auto-size every column to its
        # content, same convention already established for the
        # Properties tables (properties_panel.py's own _make_table(),
        # hierarchy_view/properties_table_widget.py) -- ResizeToContents
        # is a persistent mode, not a one-shot call, so it keeps
        # re-fitting columns (including "Message") on every
        # set_diagnostics() reset for free, and a long/flagged-error
        # message is never visually clipped -- the model's own data()
        # already always returns the full, untruncated text; only the
        # view's own default fixed column width was hiding it.
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.setWidget(self.table)

    def set_diagnostics(self, source: str, diagnostics: list[Diagnostic]) -> None:
        self.model.set_diagnostics(source, diagnostics)
        if any(d.severity in _AUTO_SHOW_SEVERITIES for d in diagnostics):
            self.setVisible(True)

    def show_for_tab(self, tab_key: str) -> None:
        self.model.set_visible_sources(TAB_SOURCES.get(tab_key, set()))
