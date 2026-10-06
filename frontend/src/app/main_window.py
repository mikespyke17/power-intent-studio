"""Application shell: QTabWidget (Load Design / Load Power Intent /
Power Intent), a File/View menu bar, and nothing else at this level --
diagnostics and the Hierarchy Tree/Properties docks all now live
*inside* their own owning tab (each tab class is itself a QMainWindow --
see each one's own docstring), rather than one shared dock at this level
switched by whichever tab is active. This level only needs to know those
docks exist at all so the View menu can toggle them.

**"Visualize Design" is hidden, not deleted** (explicit request): `self.
visualize_design_tab` is still fully constructed and kept fed with data
(`_on_design_loaded()` still calls `.load(design)` on it) so it can be
restored later with minimal work, but it is deliberately never
`addTab()`'d into `self.tabs`, has no "View" menu group, and no tab-
enablement bookkeeping of its own anymore -- "Design Properties" (the
one part of it users still need day-to-day) moved to Power Intent
Properties' own first tab instead (see `power_intent_tab.py`'s and
`properties_panel.py`'s own docstrings). To restore: re-add the
`addTab()` call, the `visualize_design_docks` View-menu group, and shift
"Power Intent"'s own tab-index-keyed references (`_view_menu_actions_by_tab_index`,
`setTabEnabled()` calls) back up by one.

Tab order matches the intended workflow (load design -> optionally load
power intent -> visualize -- see docs/product_workflow.md): 'Load Power
Intent' sits right after 'Load Design', ahead of 'Power Intent'.

Tab enablement: Load Power Intent is enabled once 'Load Design'
succeeds; Power Intent is enabled once 'Load Power Intent' (or 'Create
New') succeeds -- "enabled once there's data to show," not "once every
downstream step has run."

Menu bar (restructured, explicit request): **File | View | Extract |
Guide**.
  - "File": "Create Project", a separator, then "Save Project" (visible
    from the start, ENABLED only while the "Power Intent" tab is -- the
    exact same tab-index-2 condition _sync_view_menu_enablement()
    already re-derives) and "Load Project" (available from the VERY
    start, welcome screen included -- there's no project yet so Save's
    gate can't apply).
  - "View": one _add_view_menu_group() per tab, each holding that tab's
    own dock(s) as a toggleViewAction() (QDockWidget's own built-in
    show/hide-and-stay-in-sync action).
  - "Extract" (new top-level menu): "Extract Power Intent > UPF/CPF >
    <version>" (moved here from "File"), a separator, then "Extract
    Diagram".
  - "Guide" (new top-level menu): an inert PLACEHOLDER only -- one
    disabled action so it still opens.
Explicitly *not* macOS's native, screen-top menu bar
(setNativeMenuBar(False), see _build_menu_bar()'s own comment) --
explicit request: the same in-window menu bar on every platform.

"View"/"Extract"/"Guide" aren't in the menu bar at all until a project
exists (explicit request -- stronger than merely disabled) -- built in
`_build_menu_bar()` but only inserted by `_on_create_project()` (or
`_apply_loaded_project()`, the "Load Project" equivalent tail).
Individually, a group's own dock-toggle action(s) are additionally only
enabled while its owning tab is (a separate, already-established rule)
-- `_view_menu_actions_by_tab_index` plus `_sync_view_menu_enablement()`,
called once at startup and again from every one of the 4 methods below
that actually calls setTabEnabled().

Selecting a dock's own toggle action from "View" also raise()s it
(explicit request, see `_add_view_menu_group()`'s own docstring) -- so
showing a dock that's tabified behind a sibling brings it to the front
as the active tab in that group, instead of leaving it invisibly
"shown" behind whatever tab was already active.

Launch flow (explicit request): the window opens on `WelcomePage` (an
EDA-tool-style landing screen -- logo + app name), not the tabs --
`self.tabs` is still fully constructed underneath (tab 0 already
enabled, ready to go) but isn't the visible page of `_central_stack`
until "File > Create Project" succeeds. `create_project_action` then
disables itself (one-shot, matching the rest of this window's own
already-established never-go-back navigation pattern -- there's no
"close project"/"new project" flow to reset the three tabs' internal
state back to empty).

A successful 'Elaborate' populates 'Load Power Intent' (and keeps the
hidden 'Visualize Design' fed too) and enables it right away, but the
window does NOT navigate anywhere automatically -- the user stays on
'Load Design' until they click its 'Continue' button
(LoadDesignTab.continueRequested), so they get a chance to check that
tab's own diagnostics dock for warnings first (Elaborate can succeed
*with* warnings, not just cleanly). Only on 'Continue' does the window
jump to 'Load Power Intent' (the next tab in the new order) and disable
'Load Design' outright (not just navigate away from it) -- a one-shot
flow from that point. There is deliberately no way back to 'Load
Design' from the GUI afterward (no "load a different design" action
exists yet); starting over currently means restarting the app.

Same one-shot pattern one step later: a successful 'Mapping' or 'Create'
on 'Load Power Intent' (LoadPowerIntentTab.continueRequested) jumps to
'Power Intent' (formerly 'Visualize Design', before it was hidden -- see
this module's own docstring above) and disables 'Load Power Intent' --
see that tab's own docstring for why both actions share this single
signal. 'Power Intent' itself is only enabled once 'Load Power Intent'
(or 'Create New') actually succeeds (`_on_power_intent_loaded()`, which
always runs before `continueRequested` -- see
`load_power_intent_tab.py`'s own signal-emission order), so by the time
this navigation fires, the tab it's jumping to is already enabled.

'Create New' additionally fires its own createdWithoutMapping signal
(Mapping never does), wired straight to
PowerIntentTab.show_diagram_without_mapping() -- 'Power Intent' tab's
own Diagram needs to become visible on this path too, but has no
MappedDesign to build the usual overlay from (Create New never runs a
mapping step at all), so it gets a different reaction from Mapping's own
mappingCompleted -> set_mapped_design(). See both tabs' own docstrings.

The three optional action parameters below are the real-backend wiring
hook (see docs/backend_integration.md) -- `frontend/src/app/main.py`'s
own `main()` (and so `power_intent_studio.py`, the app's single entry
point) passes all three by default. Left as `None` (the default here),
every action falls back to mock/fixture data -- unchanged, and still what
the rest of this file's own test suite (and the wider front-end test
suite) builds against, per CLAUDE.md's milestone-independence rule.
"""

from __future__ import annotations

from typing import Optional

from pathlib import Path

from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QDialog, QDockWidget, QFileDialog, QInputDialog, QMainWindow, QMenu, QMessageBox, QStackedWidget, QTabWidget

from frontend.src.app.extract_diagram import ExtractDiagramDialog, output_path_for, render_view_to_file
from frontend.src.app.project_file import ProjectData, ProjectFileError, deserialize_project, serialize_project
from frontend.src.app.welcome_page import LOGO_PATH, WelcomePage
from frontend.src.hierarchy_view.load_design_tab import ElaborateAction, LoadDesignTab
from frontend.src.shared.models.rtl_io import module_io_ports
from frontend.src.shared.widgets import message_box
from frontend.src.hierarchy_view.visualize_design_tab import VisualizeDesignTab
from frontend.src.power_intent_view.load_power_intent_tab import LoadAction, LoadPowerIntentTab, MappingAction
from frontend.src.power_intent_view.power_intent_tab import PowerIntentTab
from frontend.src.power_intent_view.extract_options_dialog import (
    ExtractOptionsDialog,
    should_ask as should_ask_extract_options,
)
from frontend.src.power_intent_view.power_intent_text_export import (
    SUPPORTED_CPF_VERSIONS,
    SUPPORTED_UPF_VERSIONS,
    generate_cpf,
    generate_upf,
    isolation_name_prefix_upf_warnings,
    macro_power_intent_cpf_warnings,
    power_switch_ack_cpf_warnings,
    supply_port_direction_cpf_warnings,
)
from frontend.src.power_intent_view.power_state_table_tab import PowerStateTableTab


def _add_view_menu_group(menu: QMenu, title: str, docks: list[QDockWidget]) -> None:
    """One "View" menu group: a disabled header action naming the owning
    tab, then that tab's own dock-toggle action(s). Not QMenu.addSection()
    -- a real bug, found via a live screenshot: addSection()'s own title
    text simply never rendered at all in the non-native menu style this
    window now always uses (see _build_menu_bar()'s own
    setNativeMenuBar(False) comment) -- only its separator line showed,
    leaving every group visually unlabeled. A plain disabled QAction's
    text always renders, in any style.

    Each dock's own toggleViewAction() also gets wired to raise() it on
    show (explicit request) -- toggleViewAction() alone only flips
    visibility, which leaves a just-shown dock buried behind whichever
    tab was already active if the user had dragged it into a tabbed
    group with a sibling dock; QDockWidget.raise_() is the Qt idiom for
    bringing a specific tab in such a group to the front."""
    if menu.actions():
        menu.addSeparator()
    header = menu.addAction(title)
    header.setEnabled(False)
    for dock in docks:
        action = dock.toggleViewAction()
        action.toggled.connect(lambda checked, dock=dock: dock.raise_() if checked else None)
        menu.addAction(action)


class MainWindow(QMainWindow):

    # Tab indices -- see the addTab() block in __init__ for why these are
    # named rather than written inline at each call site.
    TAB_LOAD_DESIGN = 0
    TAB_LOAD_POWER_INTENT = 1
    TAB_POWER_INTENT = 2
    TAB_POWER_STATE_TABLE = 3
    TAB_ORDER = ("Load Design", "Load Power Intent", "Power Intent", "Power State Table")

    def __init__(
        self,
        elaborate_action: Optional[ElaborateAction] = None,
        load_power_intent_action: Optional[LoadAction] = None,
        mapping_action: Optional[MappingAction] = None,
    ):
        super().__init__()
        self.setWindowTitle("Power Intent Studio")
        self.setWindowIcon(QIcon(str(LOGO_PATH)))  # explicit request: logo in the title bar too

        self.load_design_tab = LoadDesignTab(elaborate_action=elaborate_action)
        # Constructed and kept fed with data (see _on_design_loaded()
        # below) but deliberately never added to self.tabs -- explicit
        # request, see this module's own docstring: hidden, not deleted.
        # Kept deliberately (explicit request: "keep the code in case I
        # want to restore it"), never addTab()'d -- see this module's own
        # docstring for how to restore it. Cost, measured by the audit
        # (category 05) so it is at least visible: this constructs a full
        # second hierarchy tree, diagram and properties-table widget set
        # on every MainWindow, and _on_design_loaded() keeps feeding it
        # on every elaborate, for something unreachable from the UI. If
        # that ever matters, construct it lazily on restore rather than
        # deleting it.
        self.visualize_design_tab = VisualizeDesignTab()
        # `dirty_check` is a lambda, not `self.power_intent_tab.is_dirty`
        # directly: that tab is constructed on the NEXT line, so binding
        # the method here would fail. Deferring the lookup also keeps
        # LoadPowerIntentTab free of any reference to the tab itself.
        self.load_power_intent_tab = LoadPowerIntentTab(
            load_action=load_power_intent_action,
            mapping_action=mapping_action,
            dirty_check=lambda: self.power_intent_tab.is_dirty(),
        )
        self.power_intent_tab = PowerIntentTab()
        # Enabled by the EXACT SAME condition as "Power Intent" itself
        # (see _on_power_intent_loaded() below) -- its own dynamic
        # columns are entirely driven by Power Domains/Supply Nets that
        # only exist once Power Intent has been loaded. Reads the SAME
        # shared diagram-only state PowerIntentTab's own diagram already
        # owns -- see power_state_table_tab.py's own module docstring.
        self.power_state_table_tab = PowerStateTableTab(self.power_intent_tab.diagram)

        self.tabs = QTabWidget()
        # Audit fix (category 09): these four indices used to appear as
        # bare integers at ~12 call sites, several carrying a comment
        # explaining which number meant which tab. Reordering or
        # restoring a tab meant auditing every one by hand -- which has
        # already been done once, when "Visualize Design" was hidden and
        # everything after it shifted down. The constants are defined
        # immediately above the addTab() calls they describe so the two
        # can't be read apart, and TAB_ORDER asserts they still agree.
        self.tabs.addTab(self.load_design_tab, "Load Design")
        self.tabs.addTab(self.load_power_intent_tab, "Load Power Intent")
        self.tabs.addTab(self.power_intent_tab, "Power Intent")
        self.tabs.addTab(self.power_state_table_tab, "Power State Table")

        for index in (self.TAB_LOAD_POWER_INTENT, self.TAB_POWER_INTENT, self.TAB_POWER_STATE_TABLE):
            self.tabs.setTabEnabled(index, False)

        # Explicit request: an EDA-tool-style welcome page (logo + app
        # name) is what the user actually sees on launch -- self.tabs
        # only becomes the visible page once "File > Create Project"
        # succeeds (see _on_create_project()), even though it's already
        # fully constructed (and tab 0 already enabled) by this point.
        self.welcome_page = WelcomePage()
        self._central_stack = QStackedWidget()
        self._central_stack.addWidget(self.welcome_page)
        self._central_stack.addWidget(self.tabs)
        self.setCentralWidget(self._central_stack)

        self._build_menu_bar()
        self._wire_signals()
        self._sync_view_menu_enablement()

    def _build_menu_bar(self) -> None:
        menu_bar = self.menuBar()
        # Explicit request: the menu bar must be embedded in the app
        # window itself, the same on every platform -- not macOS's
        # native, screen-top menu bar. Found via a real screenshot, back
        # when "File" was still empty: a top-level menu with zero
        # actions is *silently omitted* by macOS's native menu bar
        # integration (menus with zero actions never render at all
        # there), so "File" was invisible even though "View" (non-empty)
        # rendered fine, right next to "Python" (the process's own name,
        # standing in for the app's name -- another side effect of
        # native integration this avoids).
        menu_bar.setNativeMenuBar(False)
        # `self.file_menu`/`self.view_menu` (not local variables) --
        # keeps a permanent, directly-accessible Python reference to
        # each top-level QMenu. A real PySide6/shiboken gotcha, found via
        # a live repro: re-discovering a menu later via
        # `menu_bar.actions()[i].menu()` (instead of reading it directly)
        # intermittently raised "Internal C++ object already deleted" on
        # that *exact same* (`is`-identical) QMenu a direct attribute
        # read has no trouble with at all -- so callers (including this
        # module's own tests) should always read `.file_menu`/
        # `.view_menu` directly, never rediscover them that way.
        # Menu bar restructured (explicit request) to: File | View |
        # Extract | Guide. "File" is present from the very start (its
        # "Load Project" must work from the welcome screen -- there's no
        # project yet, so Save Project's "Power Intent tab enabled" gate
        # obviously can't apply there); "View"/"Extract"/"Guide" keep the
        # same deferred-insertion discipline "View" already had (built
        # here, inserted into the bar only by _on_create_project() --
        # nothing meaningful in any of them before a project exists).
        self._menu_bar = menu_bar
        self.file_menu = menu_bar.addMenu("File")
        self.create_project_action = self.file_menu.addAction("Create Project")
        self.create_project_action.triggered.connect(self._on_create_project)
        self.file_menu.addSeparator()
        # "Save Project" (explicit request): visible from the start but
        # ENABLED only while the "Power Intent" tab is -- the exact same
        # condition (tab index 2) _sync_view_menu_enablement() already
        # re-derives on every tab-enablement change.
        self.save_project_action = self.file_menu.addAction("Save Project")
        self.save_project_action.triggered.connect(self._on_save_project)
        self.save_project_action.setEnabled(False)
        # "Load Project" (explicit request): available from the very
        # start, INCLUDING the welcome screen -- always enabled.
        self.load_project_action = self.file_menu.addAction("Load Project")
        self.load_project_action.triggered.connect(self._on_load_project)

        # "Extract" (NEW top-level menu, explicit request): holds
        # "Extract Power Intent > UPF/CPF > <version>" (MOVED here from
        # "File") and, below a separator, "Extract Diagram".
        # Sub-QMenu objects stored as instance attributes, not locals --
        # the same real PySide6/shiboken gotcha `self.file_menu`/
        # `self.view_menu` already guard against (a QMenu with no
        # persistent Python reference can have its underlying C++ object
        # torn down even while still nominally reachable, e.g. via
        # parent_menu.actions()[i].menu()).
        self.extract_menu = QMenu("Extract", menu_bar)
        self.extract_power_intent_menu = QMenu("Extract Power Intent", self.extract_menu)
        self.extract_menu.addMenu(self.extract_power_intent_menu)
        self.extract_upf_menu = self.extract_power_intent_menu.addMenu("UPF")
        for version in SUPPORTED_UPF_VERSIONS:
            action = self.extract_upf_menu.addAction(version)
            action.triggered.connect(lambda checked=False, v=version: self._on_extract_power_intent("UPF", v))
        self.extract_cpf_menu = self.extract_power_intent_menu.addMenu("CPF")
        for version in SUPPORTED_CPF_VERSIONS:
            action = self.extract_cpf_menu.addAction(version)
            action.triggered.connect(lambda checked=False, v=version: self._on_extract_power_intent("CPF", v))
        self.extract_menu.addSeparator()
        self.extract_diagram_action = self.extract_menu.addAction("Extract Diagram")
        self.extract_diagram_action.triggered.connect(self._on_extract_diagram)

        # "Guide" (NEW top-level menu, explicit request): a PLACEHOLDER
        # only for now -- no real content/functionality yet, just needs
        # to exist as an inert menu for future use. One disabled action
        # so the menu still opens (and reads as deliberately empty)
        # rather than being an un-openable, easily-mistaken-for-broken
        # bare label.
        self.guide_menu = QMenu("Guide", menu_bar)
        _guide_placeholder = self.guide_menu.addAction("(no guide content yet)")
        _guide_placeholder.setEnabled(False)

        # Explicit request: "View" isn't just disabled before a project
        # exists, it isn't in the menu bar *at all* -- constructed here
        # (so every dock/action underneath it is ready) but only
        # actually inserted into the menu bar by _on_create_project(),
        # via QMenuBar.addMenu(QMenu) (the overload that takes an
        # already-built menu, as opposed to the addMenu(str) used for
        # "File" above, which builds a new one).
        self.view_menu = QMenu("View", menu_bar)
        view_menu = self.view_menu
        load_design_docks = [self.load_design_tab.diagnostics_dock]
        load_power_intent_docks = [self.load_power_intent_tab.diagnostics_dock]
        # No "Visualize Design" group -- hidden along with its own tab
        # (see this module's own docstring); its docks still exist
        # (self.visualize_design_tab.tree_dock/.properties_dock), just
        # unreachable from "View" until it's ever restored.
        power_intent_docks = [self.power_intent_tab.tree_dock, self.power_intent_tab.properties_dock]
        # No "Power State Table" group -- unlike every other tab here,
        # it has no QDockWidget of its own (its Diagnostic section is a
        # fixed, inline part of its own layout, not a closable/floatable
        # dock -- see power_state_table_tab.py's own module docstring),
        # so there's nothing for a View-menu group to toggle.
        _add_view_menu_group(view_menu, "Load Design", load_design_docks)
        _add_view_menu_group(view_menu, "Load Power Intent", load_power_intent_docks)
        _add_view_menu_group(view_menu, "Power Intent", power_intent_docks)

        # Explicit request: a group's own dock-toggle action(s) are only
        # enabled while its owning tab is -- keyed by tab index (matches
        # self.tabs' own add order above) so _sync_view_menu_enablement()
        # can re-derive each action's enabled state straight from
        # self.tabs.isTabEnabled() whenever tab enablement changes,
        # rather than duplicating that bookkeeping in two places.
        self._view_menu_actions_by_tab_index: dict[int, list[QAction]] = {
            self.TAB_LOAD_DESIGN: [dock.toggleViewAction() for dock in load_design_docks],
            self.TAB_LOAD_POWER_INTENT: [dock.toggleViewAction() for dock in load_power_intent_docks],
            self.TAB_POWER_INTENT: [dock.toggleViewAction() for dock in power_intent_docks],
        }

    def _project_is_active(self) -> bool:
        """True once the window has left the welcome screen for the tabs
        -- either via "Create Project" or "Load Project". Used to decide
        whether "Load Project" needs to prompt about the current project
        first, and whether the View/Extract/Guide menus are already in
        the bar."""
        return self._central_stack.currentWidget() is self.tabs

    def _reveal_project_ui(self, name: str) -> None:
        """Shared tail of "Create Project" and "Load Project": switch
        from WelcomePage to the tabs, insert View | Extract | Guide into
        the menu bar (in that order, after "File"), disable "Create
        Project" (one-shot -- see module docstring), and set the window
        title. Idempotent-ish: only inserts the menus / disables
        Create Project the first time (a subsequent "Load Project" over
        an already-active project just updates the title)."""
        self.project_name = name
        self.setWindowTitle(f"Power Intent Studio — {name}")
        if not self._project_is_active():
            self._central_stack.setCurrentWidget(self.tabs)
            self._menu_bar.addMenu(self.view_menu)
            self._menu_bar.addMenu(self.extract_menu)
            self._menu_bar.addMenu(self.guide_menu)
            # DELIBERATELY still enabled (explicit request): "Create
            # Project" stays reachable while a project is open, and
            # _on_create_project() handles the unsaved-changes prompt.
            # This used to be a one-shot action, matching this window's
            # never-go-back navigation; that restriction is lifted.

    def _on_create_project(self) -> None:
        """"File > Create Project": prompts for a project name, then
        (only on a genuine, non-blank OK) switches away from
        `WelcomePage` to the already-built `self.tabs`, lands on "Load
        Design" (already the current tab -- nothing else has run yet),
        inserts "View | Extract | Guide" into the menu bar for the first
        time (see `_build_menu_bar()`'s own comment on why they aren't
        there from the start), and reflects the project name in the
        window title. A blank name or Cancel leaves the window exactly
        as it was -- still on the welcome page, free to try again.

        AVAILABLE AT ALL TIMES (explicit request), not just on the
        welcome screen: creating a project over an active one first asks
        whether to save it, using the EXACT same flow "Load Project"
        already established -- "Yes" runs Save Project and a CANCELLED
        save aborts the whole Create, "No" discards, Cancel aborts.
        Sharing that flow rather than writing a second one is what keeps
        the two from drifting apart."""
        if self._project_is_active():
            reply = message_box.question(
                self,
                "Create Project",
                "Do you want to save the current project before creating a new one?",
                buttons=QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel,
                default_button=QMessageBox.StandardButton.Cancel,
            )
            if reply == QMessageBox.StandardButton.Cancel:
                return
            if reply == QMessageBox.StandardButton.Yes and not self._on_save_project():
                return  # user cancelled the Save dialog -> abort the whole Create
        name, ok = QInputDialog.getText(self, "Create Project", "Project Name:")
        name = name.strip()
        if not ok or not name:
            return
        self._reveal_project_ui(name)
        self.tabs.setCurrentIndex(self.TAB_LOAD_DESIGN)

    def _loaded_design_top_name(self) -> Optional[str]:
        """The top module's own name for the export header's "Design"
        field (section 7, explicit request) -- resolved from whatever
        RTL Design is currently loaded (design_properties._design is
        set by PowerIntentTab.set_rtl() -> properties.load_design(),
        see that method's own docstring), or None before a design has
        been loaded (generate_upf()/generate_cpf() fall back to their
        own "Unknown" placeholder in that case)."""
        design = self.power_intent_tab.properties.design_properties._design
        if design is None:
            return None
        return design.definitions[design.top].base_name

    def _on_extract_power_intent(self, format_name: str, version: str) -> None:
        """Reads the Power Intent diagram's own *current* mock overlay
        state directly off `PowerIntentDiagramView` (shared_supply_state/
        shared_domain_state/shared_retention_state/shared_power_switch_state
        are live references, always up to date -- no signal hookup
        needed), serializes it, and writes it wherever the user picks
        via a save dialog. A cancelled dialog (empty `path`) is a silent
        no-op."""
        diagram = self.power_intent_tab.diagram
        design_name = self._loaded_design_top_name()
        # Explicit request: the save dialog's default suggested filename
        # is always "<top_module_name>_power_intent" (e.g.
        # "sram_power_intent"), regardless of format or version -- falls
        # back to a bare "power_intent" when no design name is resolvable
        # yet (same "Unknown"-shaped fallback _loaded_design_top_name()
        # itself returns None for).
        base_name = f"{design_name}_power_intent" if design_name else "power_intent"
        if format_name == "UPF":
            extension, default_name, file_filter = ".upf", f"{base_name}.upf", "UPF Files (*.upf)"
        else:
            extension, default_name, file_filter = ".cpf", f"{base_name}.cpf", "CPF Files (*.cpf)"

        # Export options, between the version pick and the path pick.
        # Asked FIRST so cancelling here does not make the user choose a
        # filename for an export they then abandon. UPF only -- see
        # extract_options_dialog.should_ask().
        legacy_power_states = False
        if should_ask_extract_options(format_name):
            options = ExtractOptionsDialog(version, parent=self)
            if options.exec() != QDialog.DialogCode.Accepted:
                return
            legacy_power_states = options.legacy_power_states

        path, _ = QFileDialog.getSaveFileName(self, f"Extract Power Intent — {format_name} {version}", default_name, file_filter)
        if not path:
            return
        # Explicit request: the saved file is always .upf/.cpf -- a
        # native save dialog doesn't reliably enforce the filter's own
        # extension (e.g. a typed name with no extension, or the wrong
        # one, on some platforms), so this is enforced here regardless
        # of what the dialog itself returned.
        if not path.lower().endswith(extension):
            path += extension

        # Text generation now happens AFTER the save path is known (moved
        # from before it, explicit request): a macro's own attached power
        # intent file (see macro_power_intent_overlay.py) is emitted
        # relative-when-possible in UPF's "Load Upf" section, and the one
        # real, well-defined base for that is the OUTPUT file's own
        # directory -- there is no other meaningful "relative to what"
        # otherwise. Every other section's own content is unaffected by
        # this reordering (none of it ever depended on the save path).
        output_dir = str(Path(path).parent)
        # EVERY state family the generator accepts is passed here.
        #
        # Four of them were not, for a long time, and the consequence was
        # invisible from the tests: Isolation, Port Attributes and the
        # whole Power State Table were fully built on the GUI side and
        # fully supported by the generator, but the two never met -- the
        # app exported none of them. Every test that covered those
        # sections called generate_upf()/generate_cpf() DIRECTLY and
        # passed the state itself, so all of them passed while the real
        # "Extract Power Intent" action silently produced a file with
        # those sections missing entirely. Found only by using the app.
        #
        # `test_extract_power_intent_passes_every_state_family` drives
        # this through the real menu action for exactly that reason: a
        # test that bypasses this call site cannot see this class of gap.
        rtl_design = self.power_intent_tab.properties.design_properties._design
        shared = dict(
            isolation_state=diagram.shared_isolation_state,
            port_attribute_state=diagram.shared_port_attribute_state,
            # Full top-level I/O, SystemVerilog interface signals included
            # -- the same list the Port Attribute dialog edits.
            top_level_ports=module_io_ports(rtl_design, rtl_design.definitions[rtl_design.top]) if rtl_design is not None else None,
            power_state_table_state=self.power_state_table_tab.state,
            macro_power_intent_state=diagram.shared_macro_power_intent_state,
        )
        if format_name == "UPF":
            text = generate_upf(
                diagram.shared_supply_state,
                diagram.shared_domain_state,
                diagram.shared_retention_state,
                version,
                diagram.shared_power_switch_state,
                diagram.shared_level_shifter_state,
                design_name,
                output_dir=output_dir,
                legacy_power_states=legacy_power_states,
                rtl_design=rtl_design,
                **shared,
            )
        else:
            text = generate_cpf(
                diagram.shared_supply_state,
                diagram.shared_domain_state,
                diagram.shared_retention_state,
                version,
                diagram.shared_power_switch_state,
                diagram.shared_level_shifter_state,
                design_name,
                **shared,
            )
        try:
            with open(path, "w") as f:
                f.write(text)
        except OSError as e:
            message_box.critical(self, "Extract Power Intent Failed", str(e))
            return
        # Generation-time diagnostic (explicit request): CPF has no
        # construct to express a macro's own attached power intent file
        # (see power_intent_text_export.py's own research-backed comment
        # above _macro_power_intent_upf_lines()) -- the file is still
        # written (with an explanatory comment in its own "Load Upf"
        # section), but the user is told about the gap rather than it
        # silently vanishing from the output.
        if format_name == "CPF":
            # Every gap shares one dialog: a user exporting to CPF wants
            # the whole list of what could not be expressed, not one
            # message box per construct kind.
            warnings = macro_power_intent_cpf_warnings(diagram.shared_macro_power_intent_state)
            warnings += supply_port_direction_cpf_warnings(diagram.shared_supply_state)
            warnings += power_switch_ack_cpf_warnings(diagram.shared_power_switch_state)
            title = "Power Intent Not Expressible in CPF"
        else:
            # The one diagnostic that fires for UPF rather than CPF: an
            # Isolation Name Prefix has no VERIFIED UPF home, while CPF
            # does have one -- the reverse of every other gap here, so it
            # gets its own title rather than being filed under a
            # CPF-shaped heading.
            warnings = isolation_name_prefix_upf_warnings(diagram.shared_isolation_state)
            title = "Power Intent Not Expressible in UPF"
        if warnings:
            message_box.warning(self, title, "\n\n".join(warnings))

    # --- Save / Load Project (.pis -- explicit request) -------------------

    def _gather_project_data(self) -> ProjectData:
        """Every piece of live state a `.pis` file must round-trip -- the
        RTL hierarchy (needed to reconstruct the diagram at all), all 7
        diagram-only state families (read straight off the shared,
        always-up-to-date objects PowerIntentDiagramView owns), and the
        Power State Table's own rows."""
        diagram = self.power_intent_tab.diagram
        return ProjectData(
            project_name=getattr(self, "project_name", ""),
            rtl_design=self.power_intent_tab.properties.design_properties._design,
            supply_state=diagram.shared_supply_state,
            domain_state=diagram.shared_domain_state,
            retention_state=diagram.shared_retention_state,
            port_attribute_state=diagram.shared_port_attribute_state,
            power_switch_state=diagram.shared_power_switch_state,
            isolation_state=diagram.shared_isolation_state,
            level_shifter_state=diagram.shared_level_shifter_state,
            power_state_table_state=self.power_state_table_tab.state,
            macro_power_intent_state=diagram.shared_macro_power_intent_state,
            hidden_paths=self.power_intent_tab.tree.hidden_paths(),
        )

    def _on_save_project(self) -> bool:
        """"File > Save Project": standard save-as dialog, then serialize
        the whole project to a gzip-compressed `.pis` file (see
        project_file.py's own module docstring). Returns whether a file
        was actually written -- so "Load Project"'s own "save first?"
        prompt can ABORT the load when the user cancels this dialog
        (a "Yes" answer is never silently downgraded to an unsaved
        discard)."""
        default = f"{getattr(self, 'project_name', None) or 'project'}.pis"
        path, _ = QFileDialog.getSaveFileName(self, "Save Project", default, "Power Intent Studio Project (*.pis)")
        if not path:
            return False
        if not path.lower().endswith(".pis"):
            path += ".pis"
        try:
            blob = serialize_project(self._gather_project_data())
            with open(path, "wb") as f:
                f.write(blob)
        except (OSError, ProjectFileError) as exc:
            message_box.critical(self, "Save Project Failed", str(exc))
            return False
        return True

    def _on_load_project(self) -> None:
        """"File > Load Project": available from the very start (welcome
        screen included). If a project is already active, first asks
        whether to save it -- "Yes" runs Save Project (a cancelled save
        aborts the whole load), "No" discards it, Cancel aborts. Then a
        file picker filtered to `.pis` only; on success the project is
        fully reconstructed (both the Power Intent view and the Power
        State Table)."""
        if self._project_is_active():
            reply = message_box.question(
                self,
                "Load Project",
                "Do you want to save the current project before loading the new one?",
                buttons=QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel,
                default_button=QMessageBox.StandardButton.Cancel,
            )
            if reply == QMessageBox.StandardButton.Cancel:
                return
            if reply == QMessageBox.StandardButton.Yes and not self._on_save_project():
                return  # user cancelled the Save dialog -> abort the whole Load
        path, _ = QFileDialog.getOpenFileName(self, "Load Project", "", "Power Intent Studio Project (*.pis)")
        if not path:
            return
        try:
            with open(path, "rb") as f:
                blob = f.read()
            data = deserialize_project(blob)
        except (OSError, ProjectFileError) as exc:
            message_box.critical(self, "Load Project Failed", str(exc))
            return
        self._apply_loaded_project(data)

    def _apply_loaded_project(self, data: ProjectData) -> None:
        """Reconstruct a project from a decoded `.pis` payload: reveal
        the project UI (menus, tabs -- same tail as Create Project),
        rebuild the Power Intent tab from the restored RTL + diagram
        state, repopulate the Power State Table, then jump straight to a
        fully-populated "Power Intent" tab (a one-shot flow -- Load
        Design / Load Power Intent are disabled, exactly as they are once
        the normal flow reaches this point)."""
        self._reveal_project_ui(data.project_name or "Loaded Project")
        self.power_intent_tab.load_project(
            data.rtl_design,
            data.supply_state,
            data.domain_state,
            data.retention_state,
            data.port_attribute_state,
            data.power_switch_state,
            data.isolation_state,
            data.level_shifter_state,
            data.macro_power_intent_state,
            hidden_paths=data.hidden_paths,
        )
        self.power_state_table_tab.load_state(data.power_state_table_state)
        self.tabs.setTabEnabled(self.TAB_LOAD_DESIGN, False)
        self.tabs.setTabEnabled(self.TAB_LOAD_POWER_INTENT, False)
        self.tabs.setTabEnabled(self.TAB_POWER_INTENT, True)
        self.tabs.setTabEnabled(self.TAB_POWER_STATE_TABLE, True)
        self.tabs.setCurrentIndex(self.TAB_POWER_INTENT)
        self._sync_view_menu_enablement()

    def _on_extract_diagram(self) -> None:
        """"Extract > Extract Diagram" (menu-bar entry point): see
        _extract_diagrams()/ExtractDiagramDialog. With more than one
        diagram tab open, prompts which one(s) to export; with exactly
        one, that one is the implicit target."""
        self._extract_diagrams(preselected=None)

    def _extract_diagrams(self, preselected) -> None:
        """Shared by both entry points. `preselected` is None for the
        menu-bar path (offer every open diagram tab, multi-select) or a
        specific inner diagram view for the right-click-empty-canvas
        path (that one diagram only, no list). One file per exported
        diagram, named after its own tab (see extract_diagram.py)."""
        views = self.power_intent_tab.diagram.all_diagram_views()
        if not views:
            return
        if preselected is not None:
            pairs = [(name, view) for name, view in views if view is preselected] or [views[0]]
            allow_multi = False
        else:
            pairs = views
            allow_multi = True

        dialog = ExtractDiagramDialog([name for name, _ in pairs], allow_multi, str(Path.home()), parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        chosen = dialog.selected_names
        if not chosen or not dialog.directory or not dialog.base_filename:
            return
        view_by_name = {name: view for name, view in pairs}
        single = len(chosen) == 1
        failures = []
        for name in chosen:
            view = view_by_name.get(name)
            if view is None:
                continue
            out_path = output_path_for(dialog.directory, dialog.base_filename, name, dialog.image_format, single)
            try:
                render_view_to_file(view, out_path, dialog.image_format)
            except (OSError, ValueError, RuntimeError) as exc:
                failures.append(f"{name}: {exc}")
        if failures:
            message_box.critical(self, "Extract Diagram Failed", "\n".join(failures))

    def _wire_signals(self) -> None:
        self.load_design_tab.designLoaded.connect(self._on_design_loaded)
        self.load_design_tab.continueRequested.connect(self._on_load_design_continue)

        self.load_power_intent_tab.powerIntentLoaded.connect(self._on_power_intent_loaded)
        self.load_power_intent_tab.mappingCompleted.connect(self.power_intent_tab.set_mapped_design)
        self.load_power_intent_tab.createdWithoutMapping.connect(self.power_intent_tab.show_diagram_without_mapping)
        self.load_power_intent_tab.continueRequested.connect(self._on_load_power_intent_continue)
        # The imported diagram state replaces the Power Intent tab's own
        # wholesale (spec section 7: a full reload/replace, never a
        # merge). Wired AFTER mappingCompleted above on purpose -- Qt
        # delivers in connection order, so set_mapped_design() has
        # already run and the overlay this replaces is the current one.
        self.load_power_intent_tab.powerIntentImported.connect(self._on_power_intent_imported)

        # "Extract Diagram" right-click-on-empty-canvas entry point --
        # carries the specific inner diagram view it fired in.
        self.power_intent_tab.diagram.extractDiagramRequested.connect(self._extract_diagrams)

    def _sync_view_menu_enablement(self) -> None:
        """Explicit request: a View menu group's own dock-toggle
        action(s) are only enabled while its owning tab is -- called
        once at startup and again every time tab enablement actually
        changes (QTabWidget has no signal for that itself, so each of
        the 4 methods below calls this directly right after its own
        setTabEnabled() call, rather than this trying to poll for
        changes on its own).

        "Save Project" is synced here too (explicit request): enabled by
        the EXACT SAME condition gating the "Power Intent" tab (index 2)
        -- there is nothing meaningful to save before that tab exists."""
        for index, actions in self._view_menu_actions_by_tab_index.items():
            enabled = self.tabs.isTabEnabled(index)
            for action in actions:
                action.setEnabled(enabled)
        self.save_project_action.setEnabled(self.tabs.isTabEnabled(self.TAB_POWER_INTENT))

    def _on_design_loaded(self, design) -> None:
        self.visualize_design_tab.load(design)  # kept fed even though hidden -- see module docstring
        self.load_power_intent_tab.set_rtl(design)
        self.power_intent_tab.set_rtl(design)
        self.tabs.setTabEnabled(self.TAB_LOAD_POWER_INTENT, True)
        self._sync_view_menu_enablement()

    def _on_load_design_continue(self) -> None:
        self.tabs.setCurrentIndex(self.TAB_LOAD_POWER_INTENT)
        self.tabs.setTabEnabled(self.TAB_LOAD_DESIGN, False)
        self._sync_view_menu_enablement()

    def _on_power_intent_loaded(self, power_intent) -> None:
        self.power_intent_tab.set_power_intent(power_intent)
        self.tabs.setTabEnabled(self.TAB_POWER_INTENT, True)
        # "Power State Table" (explicit request): enabled by the EXACT
        # SAME condition as "Power Intent" itself.
        self.tabs.setTabEnabled(self.TAB_POWER_STATE_TABLE, True)
        self._sync_view_menu_enablement()

    def _on_power_intent_imported(self, imported) -> None:
        # The RTL Design is read back from where the Power Intent tab
        # already keeps it (same accessor `_on_save_project()` uses)
        # rather than cached a second time here -- there is exactly one
        # loaded design, and duplicating the reference is how the two
        # silently drift apart.
        design = self.power_intent_tab.properties.design_properties._design
        if design is not None:
            self.power_intent_tab.apply_imported_power_intent(design, imported)
        # The Power State Table is the one imported family the diagram
        # does not own (see ImportedPowerIntent.power_state_table), so it
        # goes to its own tab -- through the SAME load_state() a project
        # load already uses, rather than a second restore path.
        self.power_state_table_tab.load_state(imported.power_state_table)

    def _on_load_power_intent_continue(self) -> None:
        self.tabs.setCurrentIndex(self.TAB_POWER_INTENT)
        # Spec section 7: 'Load Power Intent' STAYS enabled after a
        # successful mapping so another file can be loaded later -- a
        # deliberate exception to this window's otherwise one-shot
        # navigation. The tab's own dirty-flag confirmation
        # (LoadPowerIntentTab's `dirty_check`) is what protects the edits
        # a re-load would discard.
        self._sync_view_menu_enablement()
