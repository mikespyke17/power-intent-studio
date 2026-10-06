"""QGraphicsView wrapper for the Power Intent tab's Diagram group box.

Full feature parity with hierarchy_view/hierarchy_diagram_view.py's
HierarchyDiagramView (mouse-wheel zoom, click-drag pan, Fit View,
Ctrl+click multi-select, right-click Hide/Unhide + Copy Path, Ctrl+Z/
Ctrl+Y undo, light-blue cascading highlight) -- explicit request,
superseding this tab's earlier "read-only visualization... no
interactive editing" milestone note (see
docs/frontend_milestone2_power_intent_visualization.md). Achieved by
actually reusing HierarchyDiagramView (subclassed as
_PowerIntentGraphicsView below), not reimplementing any of it -- same
"shared base, not two independently maintained diagrams" principle the
module already established for the underlying hierarchy_scene_builder.py
layout code.

_PowerIntentGraphicsView additionally layers the power-intent overlay on
top (diagram_overlay.py) and adds a second, independent selection
concept: clicking a power-intent overlay item (a domain region, switch
icon, badge -- SelectableItemMixin) drives PowerIntentSelectionController
(construct_kind/construct_id, feeding the Properties panel), completely
separate from HierarchySelectionController (instance path, feeding the
Hierarchy Tree and driving Hide/Unhide/highlight on the underlying
hierarchy boxes). _on_scene_selection_changed() is overridden to route
to whichever controller the actually-clicked item belongs to, never
both, falling back to the base class's own hierarchy-box handling for
anything that isn't a power-intent overlay item.

Also adds "Add Power Supply" (see supply_port_overlay.py) via
HierarchyDiagramView's own _extra_context_menu_actions() hook -- a
right-click on a box offers it alongside Copy Path/Hide. Placing/dragging
a supply-port icon and drawing a connector between two icons' handles
both need genuinely new mouse handling: dragging the icon itself reuses
Qt's own ItemIsMovable machinery (routed straight to QGraphicsView's own
mousePressEvent, bypassing the base class's pan-vs-click bookkeeping
entirely for that gesture -- see mousePressEvent()'s own docstring for
why that bookkeeping would otherwise hijack the drag into a view-pan);
the connector-drag gesture (handle -> handle, spans two different
scene items) is hand-rolled the same way the base class already hand-
rolls pan-vs-click, just for a different gesture.

PowerIntentDiagramView (the public class power_intent_tab.py uses) is a
thin QStackedWidget wrapper around one of these, showing a placeholder
label until Mapping has actually run -- see set_mapped_design()'s
docstring for why the overlay can't be built from a PowerIntentDesign
alone. `_index` is exposed as a passthrough property to the inner
view's own index, for callers (tests included) that don't need to care
this wrapper exists at all.
"""

from __future__ import annotations

import os
from dataclasses import replace as dataclasses_replace
from typing import Callable, Optional
from uuid import uuid4

from PySide6.QtCore import QPoint, QPointF, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QMouseEvent, QPainterPath, QPen, QUndoCommand, QUndoStack
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QGraphicsPathItem,
    QGraphicsRectItem,
    QGraphicsView,
    QLabel,
    QMenu,
    QMessageBox,
    QRubberBand,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from frontend.src.hierarchy_view.hierarchy_diagram_view import HierarchyDiagramView
from frontend.src.hierarchy_view.selection_controller import HierarchySelectionController
from frontend.src.power_intent_view.add_power_supply_dialog import AddPowerSupplyDialog
from frontend.src.power_intent_view.create_power_domain_dialog import CreatePowerDomainDialog, CreateSupplySetDialog
from frontend.src.power_intent_view.diagram_overlay import SelectableItemMixin, build_power_intent_overlay
from frontend.src.power_intent_view.diagram_tool_icons import (
    internal_supply_cursor,
    isolation_cell_cursor,
    level_shifter_cursor,
    power_switch_cursor,
    retention_cursor,
    supply_net_cursor,
    supply_port_cursor,
    virtual_supply_cursor,
    zoom_lens_cursor,
)
from frontend.src.power_intent_view.diagram_tool_palette import DiagramMode
from frontend.src.power_intent_view.hover_tooltip import (
    HoverInfoTooltip,
    resolve_hierarchy_block_fields,
    resolve_isolation_fields,
    resolve_level_shifter_fields,
    resolve_pg_pin_fields,
    resolve_power_switch_fields,
    resolve_retention_fields,
    resolve_supply_port_fields,
    render_tooltip_text,
)
from frontend.src.power_intent_view.isolation_dialog import IsolationDialog, IsolationInfoDialog
from frontend.src.power_intent_view.isolation_overlay import (
    INTERIOR_TOP_MARGIN_FOR_ISOLATION,
    LEFT_MARGIN_FOR_ISOLATION,
    IsolationBadgeIconItem,
    IsolationDiagramState,
    IsolationStrategyPlacement,
    build_isolation_overlay,
    isolation_attachment_owner_paths,
    isolation_variants_on_block,
    copy_isolation_diagram_state,
    find_duplicate_isolation,
    resolve_isolation_color_source,
    restore_isolation_diagram_state,
)
from frontend.src.power_intent_view.level_shifter_dialog import LevelShifterDialog, LevelShifterInfoDialog
from frontend.src.power_intent_view.level_shifter_overlay import (
    INTERIOR_TOP_MARGIN_FOR_LEVEL_SHIFTER,
    LEFT_MARGIN_FOR_LEVEL_SHIFTER,
    LevelShifterBadgeIconItem,
    LevelShifterDiagramState,
    LevelShifterStrategyPlacement,
    attachment_owner_paths,
    build_level_shifter_overlay,
    copy_level_shifter_diagram_state,
    find_duplicate_level_shifter,
    resolve_level_shifter_color,
    restore_level_shifter_diagram_state,
    variants_for_strategy_on_block,
)
from frontend.src.power_intent_view.macro_power_intent_overlay import (
    MacroPowerIntentBadgeItem,
    MacroPowerIntentDiagramState,
    attached_file_format,
    build_macro_power_intent_badge_overlay,
    copy_macro_power_intent_diagram_state,
    macro_power_intent_badge_required_height,
    restore_macro_power_intent_diagram_state,
)
from frontend.src.power_intent_view.name_validation import NO_SPACES_ERROR, validate_no_spaces
from frontend.src.power_intent_view.port_attribute_dialog import PortAttributeDialog
from frontend.src.power_intent_view.port_attribute_overlay import (
    PortAttributeDiagramState,
    copy_port_attribute_diagram_state,
    restore_port_attribute_diagram_state,
)
from frontend.src.power_intent_view.power_domain_overlay import (
    DOMAIN_FIXED_TRANSPARENCY,
    DomainInfoBoxItem,
    PowerDomainDiagramState,
    PowerDomainPlacement,
    SupplySetPlacement,
    apply_domain_coloring,
    build_domain_info_box_overlay,
    copy_power_domain_diagram_state,
    domain_info_box_required_height,
    find_domain_for_owner,
    find_effective_domain_for_path,
    resolve_domain_color,
    restore_power_domain_diagram_state,
    supply_sets_for_owner,
    supply_sets_for_owners,
    supply_sets_referenced_by_domain,
)
from frontend.src.power_intent_view.power_switch_dialog import PowerSwitchDialog
from frontend.src.power_intent_view.power_switch_overlay import (
    PowerSwitchDiagramState,
    PowerSwitchIconItem,
    PowerSwitchPlacement,
    TOP_MARGIN_FOR_SWITCH,
    Z_POWER_SWITCH_CONNECTOR,
    build_power_switch_overlay,
    copy_power_switch_diagram_state,
    describe_inherited_states,
    find_switch_for_target,
    restore_power_switch_diagram_state,
)
from frontend.src.power_intent_view.retention_dialog import RetentionStrategyDialog, RetentionStrategyInfoDialog
from frontend.src.power_intent_view.retention_overlay import (
    RetentionBadgeIconItem,
    RetentionDiagramState,
    RetentionStrategyPlacement,
    build_retention_overlay,
    claimed_elements_by_other_strategies,
    collect_retention_elements_for_domain,
    copy_retention_diagram_state,
    find_duplicate_strategy,
    owner_paths_for_elements,
    resolve_retention_color_source,
    restore_retention_diagram_state,
)
from frontend.src.power_intent_view.selection_controller import PowerIntentSelectionController
from frontend.src.power_intent_view.source_viewer_dialog import SourceViewerDialog
from frontend.src.power_intent_view.supply_port_overlay import (
    HIGHLIGHT_COLOR,
    INTERIOR_TOP_MARGIN_FOR_SUPPLY_PORT,
    _CONNECTOR_LINE_WIDTH,
    _PG_PIN_DEFAULT_COLOR,
    ConnectorHandleItem,
    SupplyPortConnectorItem,
    SupplyPortDiagramState,
    SupplyPortIconItem,
    SupplyPortPlacement,
    SupplyPortState,
    Z_SUPPLY_PORT,
    Z_SUPPLY_PORT_CONNECTOR,
    _exit_direction,
    apply_ground_edge_classification,
    build_supply_port_overlay,
    collect_macro_obstacles,
    compute_connector_waypoints,
    connected_supply_port,
    copy_supply_port_diagram_state,
    is_nested_pair,
    known_supply_names_for_owner,
    known_supply_names_for_owners,
    known_supply_names_globally,
    path_from_waypoints,
    restore_supply_port_diagram_state,
    top_edge_supply_port_owners,
)
from frontend.src.shared.widgets import message_box
from frontend.src.shared.diagram.hierarchy_diagram_tabs import HierarchyDiagramTabs
from frontend.src.shared.diagram.hierarchy_scene_builder import InstanceBoxItem, reflow_hierarchy_scene
from frontend.src.shared.models.mapping import MappedDesign
from frontend.src.shared.models.rtl import Design, HierarchyNode


class _BoxHighlightOverlay(QGraphicsRectItem):
    """A temporary, non-destructive fluorescent-yellow highlight over a
    hierarchy box's own boundary -- explicit request: while
    DiagramMode.SUPPLY_PORT is active, whichever block is currently
    under the cursor gets this outline so it's clear where a left-click
    would place a port. Pen-only (no brush) so the box's own fill is
    never altered -- a separate item matching the box's own
    boundingRect(), parented to it (tracks automatically, removed the
    moment the cursor moves off it or a port is placed), not a mutation
    of the box's own appearance. Shares HIGHLIGHT_COLOR with
    ConnectorHandleItem.set_hover_highlight() (Supply Net's own hover/
    drop-target highlight) -- one constant, one place to tune later, per
    explicit request.

    A real, confirmed bug: this item's own default `shape()`
    (`QGraphicsRectItem`'s own, based on `rect()`) covers its *entire*
    rectangle for hit-testing purposes regardless of `NoBrush` -- Qt's
    own `qt_graphicsItem_shapeFromPath()` unions the stroked outline
    with the original (filled) path, so an unfilled pen-only rect item
    is, geometrically, exactly as hit-testable as a filled one. Once
    created on an *outer* box, this overlay -- a sibling of any nested
    child box under that same parent, at a far higher Z (`Z_SUPPLY_PORT
    + 1` vs. every `InstanceBoxItem`'s flat `Z_INSTANCE_BOX`) -- sits on
    top of and permanently shadows any nested child box for `itemAt()`/
    `items()` hit-testing across the *whole* outer box's own footprint,
    not just its drawn border. `setAcceptedMouseButtons(NoButton)` below
    only exempts it from *real* Qt event delivery (press/release), not
    from this more basic geometric hit-test query -- confirmed
    empirically to be the actual root cause of Supply Port hover
    getting stuck on an outer block and never tracking into a nested
    child underneath it. Overriding `shape()` to an empty path is the
    correct, minimal fix: this item is purely visual, so it should
    never be found by a hit test at all, matching what
    `setAcceptedMouseButtons(NoButton)` already only half-achieved.
    `boundingRect()`/painting are derived from `rect()`, not `shape()`,
    so this doesn't affect how the highlight actually renders."""

    def __init__(self, box: InstanceBoxItem):
        super().__init__(box.rect(), box)
        self.setPen(QPen(HIGHLIGHT_COLOR, 3))
        self.setBrush(Qt.BrushStyle.NoBrush)
        self.setZValue(Z_SUPPLY_PORT + 1)  # above the box's own PG-pin icons, purely cosmetic
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)  # visual only -- never steals a hit-test

    def shape(self) -> QPainterPath:
        """Empty -- see class docstring. Excludes this item from
        itemAt()/items() geometric hit-testing entirely, not just real
        mouse-event delivery (already handled by
        setAcceptedMouseButtons() above)."""
        return QPainterPath()


class _IconHighlightOverlay(QGraphicsRectItem):
    """The icon-shaped counterpart to _BoxHighlightOverlay above -- same
    temporary, non-destructive fluorescent-yellow outline, same
    HIGHLIGHT_COLOR/empty-shape()/NoButton precedents (see that class's
    own docstring for the full "why," including the real hit-test-
    shadowing bug its empty shape() fixes), just sized/positioned to an
    icon's own boundingRect() instead of a hierarchy box's rect() --
    used while DiagramMode.POWER_SWITCH is active, whichever Supply
    Port/Internal Supply icon is currently under the cursor gets this
    outline (see _update_power_switch_hover())."""

    def __init__(self, icon: SupplyPortIconItem):
        super().__init__(icon.boundingRect(), icon)
        self.setPen(QPen(HIGHLIGHT_COLOR, 3))
        self.setBrush(Qt.BrushStyle.NoBrush)
        self.setZValue(Z_SUPPLY_PORT + 1)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)

    def shape(self) -> QPainterPath:
        return QPainterPath()


class _DiagramStateCommand(QUndoCommand):
    """One Power Intent Diagram edit (add/edit/delete/move a supply
    port, connect/delete a connector, drag a segment, create/edit a
    Power Domain...) as a single undo step -- stores full before/after
    snapshots of one or more shared diagram-state objects (`entries`, a
    list of (shared_state, before, after, restore) tuples -- `restore`
    is whichever module-level restore_*_diagram_state() function
    matches that entry's own state object). Multiple entries exist for
    an edit that touches more than one shared state atomically -- e.g.
    Create Power Domain also auto-adds any missing supply ports (see
    create_power_domain()'s own docstring), so both
    SupplyPortDiagramState and PowerDomainDiagramState need to move
    together under a single Ctrl+Z, not two separate undo steps. Same
    "snapshot the whole thing" design as HierarchyTreeWidget's own
    _HideUnhideCommand, for the same reason: reversing some of these ops
    (e.g. a cascading PG-pin recolor triggered by deleting its connected
    supply port) isn't cleanly the literal inverse of the forward op, so
    a snapshot sidesteps needing a bespoke undo() per op type.

    Holds the shared state objects themselves plus a `refresh` callback,
    not a specific view -- a sub-hierarchy tab is closable, so a command
    created while editing inside one could otherwise end up holding a
    dangling reference to an already-destroyed view by the time the user
    presses Ctrl+Z. `refresh` is a closure over "whatever the currently
    active tab is" (see PowerIntentDiagramView), re-evaluated fresh on
    every call, so this is safe regardless of which tab (if any) is open
    by undo time.

    `_first_redo_done` starts True and skips exactly one redo() call --
    a real bug, found by running the actual feature, not just unit
    logic: QUndoStack.push() calls the just-pushed command's own redo()
    synchronously, on the assumption the action hasn't happened yet. But
    by construction here it *has* -- the caller (add_supply_port() etc.)
    already mutated the live state and already has real, live
    SupplyPortIconItems to work with (e.g. to return to whoever called
    it) *before* ever constructing this command. Rebuilding on that
    first, redundant redo() call destroyed those just-created items out
    from under the caller. This is Qt's own documented idiom for exactly
    this situation (a command that records an action already performed
    directly, rather than performing it itself) -- skip the first
    redo(), start actually applying from the second one on (a genuine
    user-triggered redo after an undo)."""

    def __init__(
        self,
        entries: list[tuple[object, object, object, Callable[[object, object], None]]],
        refresh: Callable[[], None],
        notify_changed: Callable[[], None],
        label: str,
    ):
        super().__init__(label)
        self._entries = entries
        self._refresh = refresh
        self._notify_changed = notify_changed
        self._first_redo_done = True

    def redo(self) -> None:
        if not self._first_redo_done:
            for shared_state, _before, after, restore in self._entries:
                restore(shared_state, after)
            self._refresh()
            self._notify_changed()
        self._first_redo_done = False

    def undo(self) -> None:
        for shared_state, before, _after, restore in self._entries:
            restore(shared_state, before)
        self._refresh()
        self._notify_changed()


class _PowerIntentGraphicsView(HierarchyDiagramView):
    # Explicit request: Escape resets DiagramMode back to SELECT and
    # updates the tool palette's own checked button -- this view cancels
    # whatever local interaction is in progress itself (see
    # keyPressEvent()/_cancel_active_interaction()), then just announces
    # that it happened; it never reaches into the toolbar's own QAction
    # directly. PowerIntentDiagramView re-emits this from whichever tab's
    # view it came from (see its own escapePressed), and power_intent_tab.py
    # checks tool_palette.select_action, which drives the mode back to
    # SELECT through the exact same modeChanged -> set_mode() chain a
    # normal toolbar click already uses -- so every open tab (not just
    # the one Escape was pressed in) resets uniformly.
    escapePressed = Signal()
    # Explicit request ("Extract Diagram" right-click entry point):
    # right-clicking EMPTY canvas space in a diagram emits this;
    # PowerIntentDiagramView re-broadcasts it tagged with this view's own
    # tab identity so the export targets exactly this one diagram, with
    # no multi-select prompt.
    extractDiagramRequested = Signal()

    def __init__(
        self,
        hierarchy_controller: HierarchySelectionController,
        power_intent_controller: PowerIntentSelectionController,
        shared_state: SupplyPortDiagramState,
        domain_state: PowerDomainDiagramState,
        retention_state: RetentionDiagramState,
        port_attribute_state: PortAttributeDiagramState,
        power_switch_state: PowerSwitchDiagramState,
        isolation_state: IsolationDiagramState,
        level_shifter_state: LevelShifterDiagramState,
        macro_power_intent_state: MacroPowerIntentDiagramState,
        undo_stack: QUndoStack,
        refresh_current: Callable[[], None],
        notify_changed: Callable[[], None],
        resolve_connectivity: Optional[Callable[[list[tuple[str, str]]], tuple[dict, dict]]] = None,
        reflow_siblings: Optional[Callable[[], None]] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(hierarchy_controller, parent)
        self._power_intent_controller = power_intent_controller
        self._index: dict[tuple[str, str], list] = {}
        self._highlighted_constructs: list = []
        # Persistent references for every currently-open, non-modal
        # "Inspect Source File" viewer (see _prompt_inspect_source_file())
        # -- required, not merely tidy: a locally-scoped QDialog with no
        # surviving Python reference is a real, confirmed crash class
        # already hit elsewhere in this codebase (a QMenu's own
        # underlying C++ object torn down once its Python wrapper went
        # out of scope -- see this module's own history). Several can be
        # open at once (inspecting two different files side by side is a
        # normal, expected use), so this is a list, not a single slot;
        # each dialog removes itself on close via its own `finished`
        # signal rather than accumulating forever.
        self._source_viewer_dialogs: list[SourceViewerDialog] = []
        # Qt-independent, shared with every other tab of the same Power
        # Intent Diagram (see SupplyPortDiagramState's own docstring) and
        # surviving a rebuild -- `_supply_port_icons`/`_supply_connectors`
        # below are the *materialized* items for this tab's own scene
        # only, always rebuilt from `_shared_state` in _rebuild().
        self._shared_state = shared_state
        # Same cross-tab-sharing/rebuild-survival story as `_shared_state`
        # above, for Power Domains -- see PowerDomainDiagramState's own
        # docstring. Applied to `self._boxes` (recolouring them, not a
        # separate glyph) in _rebuild(), via apply_domain_coloring().
        self._domain_state = domain_state
        # Same cross-tab-sharing/rebuild-survival story as `_shared_state`/
        # `_domain_state` above, for Retention Strategies -- see
        # RetentionDiagramState's own docstring (retention_overlay.py).
        self._retention_state = retention_state
        # Same cross-tab-sharing/rebuild-survival story again, for Port
        # Attributes -- see PortAttributeDiagramState's own docstring
        # (port_attribute_overlay.py). No materialized diagram item of
        # its own (Port Attribute has no glyph/icon -- Properties-table-
        # only, per its own dialog's docstring), so unlike
        # `_retention_icons`/`_domain_info_boxes` below there's nothing here
        # to rebuild in _rebuild().
        self._port_attribute_state = port_attribute_state
        # Same cross-tab-sharing/rebuild-survival story again, for Power
        # Switches -- see PowerSwitchDiagramState's own docstring
        # (power_switch_overlay.py). `_power_switch_icons`/
        # `_power_switch_connectors` below are this tab's own materialized
        # items, rebuilt from `_power_switch_state` in _rebuild(), same
        # "destroyed along with the scene, rematerialized fresh from plain
        # data" story as `_supply_port_icons`.
        self._power_switch_state = power_switch_state
        # Same cross-tab-sharing/rebuild-survival story again, for
        # Isolation Cells -- see IsolationDiagramState's own docstring
        # (isolation_overlay.py). Follows the Retention model (one
        # shared entity, several attachment-point icons), NOT Power
        # Switch's own independently-replicated one -- see that
        # module's own docstring for the full "why".
        self._isolation_state = isolation_state
        # Same cross-tab-sharing/rebuild-survival story again, for Level
        # Shifters -- see LevelShifterDiagramState's own docstring
        # (level_shifter_overlay.py). Follows the Isolation model too
        # (one shared entity, several attachment-point icons) -- see
        # that module's own docstring for the full "why", including the
        # one real, flagged deviation (color source).
        self._level_shifter_state = level_shifter_state
        # Same cross-tab-sharing/rebuild-survival story again, for
        # per-macro-instance power-intent-file attachments -- see
        # MacroPowerIntentDiagramState's own docstring
        # (macro_power_intent_overlay.py). No materialized diagram item
        # of its own (no badge/glyph requested for this feature -- see
        # that module's own docstring), same "Properties/menu-state-only,
        # nothing to rebuild in _rebuild()" shape as `_port_attribute_state`
        # above.
        self._macro_power_intent_state = macro_power_intent_state
        # Optional (defaults to a safe no-op returning empty resolution
        # dicts, matching "always show *something*, never crash" for
        # any pre-existing/test caller that never cared about this) --
        # threaded in by PowerIntentDiagramView, ultimately backed by
        # PowerIntentPropertiesPanel.resolve_port_connectivity(), the
        # SAME PowerResolutionCache-driven Driver/Receiver computation
        # the PortList tab already uses (explicit request: reuse, don't
        # recompute independently) -- see that method's own docstring.
        # Called only when opening the Isolation dialog, to populate its
        # own Elements table.
        self._resolve_connectivity = resolve_connectivity or (lambda endpoints: ({}, {}))
        # Materialized badge icons, keyed by strategy id -- see
        # RetentionBadgeIconItem's own docstring (one strategy can
        # contribute several icons, one per block it spans). Rebuilt
        # wholesale in _rebuild() (same "destroyed along with the scene,
        # rematerialized fresh from plain data" story as
        # _supply_port_icons), and updated incrementally by
        # create_retention_strategy() itself for the strategy it just
        # added -- same "materialize inline, don't wait for a rebuild"
        # precedent add_supply_port() already follows.
        self._retention_icons: dict[str, list[RetentionBadgeIconItem]] = {}
        # Same "materialized badge icons keyed by strategy id" shape as
        # `_retention_icons` above, for Isolation Cells.
        self._isolation_icons: dict[str, list[IsolationBadgeIconItem]] = {}
        # Same "materialized badge icons keyed by strategy id" shape
        # again, for Level Shifters.
        self._level_shifter_icons: dict[str, list[LevelShifterBadgeIconItem]] = {}
        # Merged Power Domain name + Supply Set list box (explicit
        # request, supersedes the earlier separate domain-name-label and
        # Supply-Set-display-box overlays), one PER MEMBER HIERARCHY,
        # keyed by domain id -- see build_domain_info_box_overlay()'s own
        # docstring (power_domain_overlay.py). Same "destroyed along with
        # the scene, rematerialized fresh from plain data" rebuild story
        # as `_retention_icons` above.
        self._domain_info_boxes: dict[str, list[DomainInfoBoxItem]] = {}
        # A macro's own "[UPF]"/"[CPF]" power-intent-attachment badge
        # (explicit follow-up request), one per attached macro instance,
        # keyed by instance path -- see MacroPowerIntentBadgeItem's own
        # docstring (macro_power_intent_overlay.py). Same "destroyed
        # along with the scene, rematerialized fresh from plain data"
        # rebuild story as `_domain_info_boxes` above; built BEFORE it in
        # _rebuild() so DomainInfoBoxItem's own initial _reposition() (its
        # __init__ calls it once immediately) already sees this badge as
        # a live sibling child when both are present on the same block.
        self._macro_power_intent_badges: dict[str, MacroPowerIntentBadgeItem] = {}
        # The SAME QUndoStack HierarchyTreeWidget's own Hide/Unhide
        # already pushes onto (see power_intent_tab.py) -- explicit
        # request: Ctrl+Z/Ctrl+Y for Power Intent edits, using the
        # already-wired shortcuts rather than a second, independent
        # stack that would leave "which one does Ctrl+Z affect" abiguous
        # whenever both kinds of edit happened in the same session.
        self._undo_stack = undo_stack
        self._refresh_current = refresh_current
        # Fired at the end of every _commit_*_undo_step() below (only
        # when something actually changed) -- a lightweight "the data
        # changed" notification distinct from `refresh_current`: it must
        # NOT touch this tab's own graphics scene, since the *first*
        # commit for a given edit runs right after the caller (e.g.
        # add_supply_port()) already built and is about to return a
        # live SupplyPortIconItem -- a scene rebuild here would destroy
        # it out from under the caller, the exact bug _DiagramStateCommand's
        # own "skip the first redo()" already exists to avoid for the
        # undo-stack side (see its docstring). Wired by PowerIntentDiagramView
        # to refresh the Properties panel's diagram-overlay tables, which
        # only ever read plain data (SupplyPortDiagramState/
        # PowerDomainDiagramState), never touch any Qt graphics item.
        self._notify_changed = notify_changed
        # Bug fix (recurring "block doesn't make room for a new element"
        # regression, found via empirical cross-tab testing): a "make
        # room" reflow triggered from THIS view (e.g. add_supply_port()
        # on a box open in a sub-hierarchy tab) previously only ever
        # touched `self._boxes` -- a SIBLING tab showing the SAME
        # hierarchy path (the always-open root tab, or another open
        # sub-hierarchy tab) kept its own, now-stale box geometry until
        # separately refreshed (e.g. by switching to it). Wired by
        # PowerIntentDiagramView to reflow EVERY currently-open tab's own
        # boxes, not just this one -- safe to call unconditionally
        # (including redundantly on `self`, already covered directly by
        # this same callback's own loop) since reflow_hierarchy_scene()
        # only ever mutates ALREADY-EXISTING InstanceBoxItems in place
        # (setRect/setPos), never destroys/recreates one, so this carries
        # none of `refresh_current`'s own "don't call this on the view
        # whose live item a caller is about to use" hazard. Defaults to a
        # private, unshared self-only reflow for any pre-existing direct-
        # construction caller/test that never cared about sibling tabs.
        self._reflow_siblings = reflow_siblings or self._reflow_own_boxes

        self._supply_port_icons: dict[str, SupplyPortIconItem] = {}
        self._supply_connectors: list[SupplyPortConnectorItem] = []
        # Materialized Power Switch icons, keyed by switch id -- a LIST
        # per switch (item 9: `target_ids`, plural -- one icon per
        # member hierarchy a multi-hierarchy switch spans, same shape
        # `_retention_icons` already uses for a Retention Strategy
        # spanning multiple blocks), plus the AUTOMATIC bottom-anchor
        # connectors (switch icon <-> its own target icon, one per
        # spanned target), keyed the same way -- kept deliberately OUT
        # of `_supply_connectors`/`_shared_state.supply_connector_placements`
        # (see power_switch_overlay.py's own module docstring): these
        # connectors are implicit/derived from PowerSwitchPlacement.target_ids,
        # never independently user-drawn/deleted/dragged the way a real
        # Supply Net connection is, so they must not be reachable via
        # the generic Supply Net right-click "Delete"/segment-drag
        # machinery.
        self._power_switch_icons: dict[str, list[PowerSwitchIconItem]] = {}
        self._power_switch_connectors: dict[str, list[SupplyPortConnectorItem]] = {}
        self._connector_drag_origin: Optional[ConnectorHandleItem] = None
        self._connector_drag_line: Optional[QGraphicsPathItem] = None
        # (connector, segment_index, "horizontal"/"vertical") while the
        # user is mid-drag on an existing connector's own middle segment
        # -- see mousePressEvent()/mouseMoveEvent()'s own handling below,
        # the per-segment-editing counterpart to _connector_drag_line's
        # "drawing a brand new connector" gesture.
        self._segment_drag: Optional[tuple[SupplyPortConnectorItem, int, str]] = None
        self._segment_drag_before: Optional[SupplyPortDiagramState] = None
        # Set at mousePressEvent time, right before handing a supply-port
        # icon (or its label, a child of the icon -- same press target)
        # drag off to Qt's own ItemIsMovable machinery -- committed as one
        # undo step on release, see mouseReleaseEvent()'s own handling.
        self._icon_drag_snapshot: Optional[SupplyPortDiagramState] = None
        # DiagramMode -- explicit request, "grouped tool palette" (see
        # diagram_tool_palette.py). Set via set_mode(), driven by
        # PowerIntentDiagramView.set_mode() -- see that class's own
        # docstring for how a mode change propagates to every open tab's
        # own view, including ones opened *after* the change.
        self._mode = DiagramMode.SELECT
        self._rubber_band: Optional[QRubberBand] = None
        self._rubber_band_origin: Optional[QPoint] = None
        # "in" (Zoom Lens left-click-drag) or "out" (right-click-drag) --
        # which gesture _finish_rubber_band() should apply, since both
        # share the exact same QRubberBand/origin/threshold machinery,
        # only the release-time math differs.
        self._rubber_band_kind: Optional[str] = None
        # Supply Port placement (explicit request, superseding an earlier
        # drag-and-drop mechanism: "REMOVE the drag-and-drop placement
        # mechanism entirely -- it is not working reliably") is now
        # hover+click directly on the canvas -- see
        # _update_supply_port_hover()/mousePressEvent()'s own
        # DiagramMode.SUPPLY_PORT branch below. `_supply_port_hover_box`
        # is which block (if any) the cursor is currently over while this
        # mode is active, for the fluorescent-yellow highlight overlay.
        self._supply_port_hover_box: Optional[InstanceBoxItem] = None
        self._box_highlight_overlay: Optional[_BoxHighlightOverlay] = None
        # DiagramMode.RETENTION hover feedback -- explicit request, same
        # hover+click placement mechanism as Supply Port (see
        # _update_retention_hover()), but its OWN dedicated pair of
        # fields rather than reusing `_supply_port_hover_box`/
        # `_box_highlight_overlay` directly -- same "each mode gets its
        # own hover state" precedent Supply Net's own `_hovered_handle`
        # already established, even though only one mode is ever active
        # at a time. `_BoxHighlightOverlay` itself is reused as-is (it
        # was never actually Supply-Port-specific in implementation).
        self._retention_hover_box: Optional[InstanceBoxItem] = None
        self._retention_highlight_overlay: Optional[_BoxHighlightOverlay] = None
        # DiagramMode.INTERNAL_SUPPLY hover feedback -- same hover+click
        # mechanism and "each mode gets its own dedicated hover state"
        # precedent as Retention above (see _update_internal_supply_hover()).
        self._internal_supply_hover_box: Optional[InstanceBoxItem] = None
        self._internal_supply_highlight_overlay: Optional[_BoxHighlightOverlay] = None
        # DiagramMode.VIRTUAL_SUPPLY hover feedback -- same hover+click
        # mechanism as Internal Supply above.
        self._virtual_supply_hover_box: Optional[InstanceBoxItem] = None
        self._virtual_supply_highlight_overlay: Optional[_BoxHighlightOverlay] = None
        # DiagramMode.POWER_SWITCH hover feedback -- same hover+click
        # mechanism as Retention/Internal Supply above ("each mode gets
        # its own dedicated hover state"), but the TARGET is a Supply
        # Port/Internal Supply ICON, not a hierarchy block -- see
        # _update_power_switch_hover(), which hit-tests via
        # _supply_port_icon_at() instead of _box_at(), and
        # _IconHighlightOverlay (this module, below _BoxHighlightOverlay)
        # for the icon-shaped equivalent of that class's own box outline.
        self._power_switch_hover_icon: Optional[SupplyPortIconItem] = None
        self._power_switch_highlight_overlay: Optional["_IconHighlightOverlay"] = None
        # DiagramMode.ISOLATION_CELL hover feedback -- same hover+click
        # mechanism/"each mode gets its own dedicated hover state"
        # precedent as Retention (TARGET is a hierarchy block too, not
        # an icon -- see _update_isolation_cell_hover()).
        self._isolation_cell_hover_box: Optional[InstanceBoxItem] = None
        self._isolation_cell_highlight_overlay: Optional[_BoxHighlightOverlay] = None
        # DiagramMode.LEVEL_SHIFTER hover feedback -- same hover+click
        # mechanism/"each mode gets its own dedicated hover state"
        # precedent as Isolation Cell above (TARGET is a hierarchy block
        # too -- see _update_level_shifter_hover()).
        self._level_shifter_hover_box: Optional[InstanceBoxItem] = None
        self._level_shifter_highlight_overlay: Optional[_BoxHighlightOverlay] = None
        # Hover-delay info tooltip (explicit request) -- UNLIKE every
        # hover-highlight state above, this one is NOT gated to any
        # single DiagramMode: it must work identically in every mode,
        # purely informational, never blocking/delaying an active tool
        # interaction (see hover_tooltip.py's own module docstring for
        # the full design). `_hover_tooltip_target` is whatever
        # icon/badge/box object is currently under the cursor (or being
        # timed towards showing, or already shown for) -- compared by
        # IDENTITY on every mouseMoveEvent so ANY movement (even within
        # the same element) restarts the pending delay, matching the
        # spec's own literal "pauses... (no movement)" wording; only
        # once the tooltip is ALREADY visible does staying on the exact
        # same target object avoid a hide/re-show flicker.
        self._hover_tooltip_widget = HoverInfoTooltip(self)
        self._hover_tooltip_timer = QTimer(self)
        self._hover_tooltip_timer.setSingleShot(True)
        self._hover_tooltip_timer.setInterval(1000)
        self._hover_tooltip_timer.timeout.connect(self._show_hover_tooltip)
        self._hover_tooltip_target = None
        self._hover_tooltip_global_pos: Optional[QPoint] = None
        # DiagramMode.SUPPLY_NET hover feedback (explicit request) --
        # which ConnectorHandleItem (if any) the cursor is currently
        # over while this mode is active; requires continuous
        # mouseMoveEvent delivery even with no button held, hence the
        # viewport mouse tracking below (off by default on a plain
        # QGraphicsView/QWidget). Also reused, during an active connector
        # drag, as the "currently a valid drop target" highlight -- see
        # _update_supply_net_hover()'s own docstring; the drag's own
        # SOURCE handle (_connector_drag_origin) is tracked and
        # highlighted independently so it stays lit for the whole drag
        # regardless of where this one points.
        self._hovered_handle: Optional[ConnectorHandleItem] = None
        # Bug 2/4 (explicit request): a single, uniform "click-selection"
        # highlight (fluorescent yellow, same HIGHLIGHT_COLOR every hover
        # highlight in this diagram already uses) spanning Supply Port
        # icons, Retention badges, AND hierarchy blocks -- only one thing
        # highlighted at a time. Hierarchy blocks reuse the EXISTING
        # HierarchySelectionController-driven pipeline (InstanceBoxItem.
        # set_highlighted(), re-skinned to border-only yellow -- see that
        # method's own docstring) unchanged, since that one also has to
        # keep driving the Tree/Properties panel; Supply Port/Retention
        # icons have no such external system to stay in sync with, so
        # they get this new, dedicated list instead, managed entirely by
        # _on_scene_selection_changed() below. Holds whichever icon
        # item(s) currently have set_click_highlighted(True) applied --
        # a single SupplyPortIconItem, or every RetentionBadgeIconItem
        # belonging to one Retention Strategy (bug 4: clicking ANY one of
        # a strategy's own (potentially several) icons highlights all of
        # them together).
        self._click_highlighted_icons: list = []
        self.viewport().setMouseTracking(True)
        power_intent_controller.selectionChanged.connect(self.apply_construct_selection)

    def set_mode(self, mode: DiagramMode) -> None:
        """Explicit request: the toolbar only emits mode-changed signals,
        this view owns everything about what a mode actually *does* --
        here, just the cursor shape (mousePressEvent/mouseMoveEvent/
        mouseReleaseEvent below read self._mode directly to route to the
        right gesture) and cancelling any in-progress Supply Net drag/
        highlight or Supply Port hover highlight left over from before
        the mode changed away from either one (switching tools mid-
        gesture, via the toolbar rather than Escape, is the same
        "abandon it" outcome as _cancel_active_interaction() -- see
        keyPressEvent()'s own docstring).

        Every mode gets its own real cursor, built from the same SVG
        assets the toolbar buttons themselves use (explicit request:
        "the mouse cursor itself must change shape to match that tool"),
        each one distinct per tool rather than a shared crosshair.
        `self.viewport().setCursor(...)` specifically (not
        `self.setCursor(...)`, the outer QGraphicsView widget) -- explicit
        request: the custom cursor must apply "ONLY while the cursor is
        inside the Diagram View's canvas area", and `viewport()` is that
        actual canvas widget (the outer QGraphicsView also includes its
        own thin frame margin around it); a sibling widget like the tool
        palette is a separate widget entirely and was never affected by
        either call, but scoping to viewport() is the precise, literal
        match for what was asked, not just "close enough."

        SELECT explicitly gets the plain system arrow (explicit request:
        "Regular Cursor mode -> normal arrow cursor (default)") --
        select/inspect only, no pan-on-drag. PAN (explicit request, a
        follow-up correcting an earlier pass that had briefly folded
        pan-on-drag into SELECT itself) gets the stock
        `Qt.CursorShape.OpenHandCursor` -- "exactly as Regular Cursor
        mode used before this tool palette existed" -- not a custom
        pixmap cursor the way every other mode's own is; see
        `_pan_enabled()`'s own docstring for how the actual click-drag
        gesture is gated to this mode specifically."""
        self._mode = mode
        if mode == DiagramMode.SELECT:
            self.viewport().setCursor(Qt.CursorShape.ArrowCursor)
        elif mode == DiagramMode.PAN:
            self.viewport().setCursor(Qt.CursorShape.OpenHandCursor)
        elif mode == DiagramMode.ZOOM_LENS:
            self.viewport().setCursor(zoom_lens_cursor())
        elif mode == DiagramMode.SUPPLY_PORT:
            self.viewport().setCursor(supply_port_cursor())
        elif mode == DiagramMode.INTERNAL_SUPPLY:
            self.viewport().setCursor(internal_supply_cursor())
        elif mode == DiagramMode.VIRTUAL_SUPPLY:
            self.viewport().setCursor(virtual_supply_cursor())
        elif mode == DiagramMode.RETENTION:
            self.viewport().setCursor(retention_cursor())
        elif mode == DiagramMode.POWER_SWITCH:
            self.viewport().setCursor(power_switch_cursor())
        elif mode == DiagramMode.ISOLATION_CELL:
            self.viewport().setCursor(isolation_cell_cursor())
        elif mode == DiagramMode.LEVEL_SHIFTER:
            self.viewport().setCursor(level_shifter_cursor())
        else:
            self.viewport().setCursor(supply_net_cursor())
        if mode != DiagramMode.SUPPLY_NET:
            # Highlights first, while `_connector_drag_origin` is still
            # set -- see _cancel_active_interaction()'s own comment on
            # this exact ordering bug.
            self._clear_supply_net_highlights()
            if self._connector_drag_line is not None:
                self.scene().removeItem(self._connector_drag_line)
                self._connector_drag_line = None
                self._connector_drag_origin = None
        if mode != DiagramMode.SUPPLY_PORT:
            self._clear_supply_port_hover_highlight()
        if mode != DiagramMode.INTERNAL_SUPPLY:
            self._clear_internal_supply_hover_highlight()
        if mode != DiagramMode.VIRTUAL_SUPPLY:
            self._clear_virtual_supply_hover_highlight()
        if mode != DiagramMode.RETENTION:
            self._clear_retention_hover_highlight()
        if mode != DiagramMode.POWER_SWITCH:
            self._clear_power_switch_hover_highlight()
        if mode != DiagramMode.ISOLATION_CELL:
            self._clear_isolation_cell_hover_highlight()
        if mode != DiagramMode.LEVEL_SHIFTER:
            self._clear_level_shifter_hover_highlight()
        if mode != DiagramMode.PAN:
            # Aborts an in-progress pan the same way switching away from
            # Supply Net/Supply Port mid-gesture already abandons those
            # (e.g. clicking a different toolbar button mid-drag) --
            # _pan_enabled() is only consulted at press time, so merely
            # changing self._mode above wouldn't otherwise stop a drag
            # already in progress.
            self._pan_start = None
            self._panning = False

    def _pan_enabled(self) -> bool:
        """Overrides HierarchyDiagramView's own (unconditionally True)
        default -- explicit request: pan-on-drag is now exclusively
        DiagramMode.PAN's own responsibility, not Regular Cursor/
        Select's default behavior anymore (that was itself a follow-up
        correction: an earlier pass had briefly left pan-on-drag bundled
        into SELECT, an "OpenHandCursor mixup"). Every other mode
        (Zoom Lens/Supply Port/Supply Net) already intercepts a plain
        left-press with its own dedicated branch in mousePressEvent()
        before the base class's own pan-vs-click bookkeeping ever runs,
        so this hook only ever actually matters for SELECT vs PAN."""
        return self._mode == DiagramMode.PAN

    def _set_own_cursor(self, shape: Qt.CursorShape) -> None:
        """Redirects the base class's own pan-cursor feedback (open
        hand at rest, closed hand while actively panning) to
        `self.viewport()` -- this class's own per-tool cursor scheme
        (see set_mode()) lives there exclusively; setting it on `self`
        instead (the base class's own default) would be shadowed by the
        viewport's already-set cursor and never actually show."""
        self.viewport().setCursor(shape)

    def _snapshot_state(self) -> SupplyPortDiagramState:
        return copy_supply_port_diagram_state(self._shared_state)

    def _commit_undo_step(self, label: str, before: SupplyPortDiagramState) -> None:
        """Pushes one _DiagramStateCommand for whatever changed between
        `before` and the shared state's current contents -- called
        *after* the actual mutation already happened. A no-op (nothing
        pushed) if the snapshot compares equal (dataclass field-by-field
        equality, including each SupplyPortPlacement's own) -- e.g. a
        plain click on an icon that doesn't actually drag it anywhere
        shouldn't create an empty undo step."""
        after = self._snapshot_state()
        if before == after:
            return
        entry = (self._shared_state, before, after, restore_supply_port_diagram_state)
        self._undo_stack.push(_DiagramStateCommand([entry], self._refresh_current, self._notify_changed, label))
        self._notify_changed()

    def _snapshot_domain_state(self) -> PowerDomainDiagramState:
        return copy_power_domain_diagram_state(self._domain_state)

    def _commit_combined_undo_step(
        self, label: str, supply_before: SupplyPortDiagramState, domain_before: PowerDomainDiagramState
    ) -> None:
        """Same as _commit_undo_step(), but
        for an edit that touched *both* shared states atomically (Create/
        Edit Power Domain -- see create_power_domain()'s own docstring
        on why supply ports and the domain itself must undo together).
        Each state that actually changed contributes its own entry;
        a state left untouched (e.g. no new supply ports were needed)
        is simply omitted rather than pushing a no-op entry for it."""
        entries = []
        supply_after = self._snapshot_state()
        if supply_before != supply_after:
            entries.append((self._shared_state, supply_before, supply_after, restore_supply_port_diagram_state))
        domain_after = self._snapshot_domain_state()
        if domain_before != domain_after:
            entries.append((self._domain_state, domain_before, domain_after, restore_power_domain_diagram_state))
        if not entries:
            return
        self._undo_stack.push(_DiagramStateCommand(entries, self._refresh_current, self._notify_changed, label))
        self._notify_changed()

    def _snapshot_power_switch_state(self) -> PowerSwitchDiagramState:
        return copy_power_switch_diagram_state(self._power_switch_state)

    def _commit_power_switch_undo_step(
        self, label: str, before: PowerSwitchDiagramState, supply_before: Optional[SupplyPortDiagramState] = None
    ) -> None:
        """Same simple, single-state shape as _commit_undo_step() for
        Edit/Delete -- a Power Switch's own dialog never introduces
        anything into SupplyPortDiagramState/PowerDomainDiagramState the
        way Retention's own "Add new Supply Set..." flow can (Supply
        In/Out are read-only displays, not dropdowns with an inline
        creation flow -- see power_switch_dialog.py's own module
        docstring). `supply_before` (optional, only ever passed by
        add_power_switch() -- item 8: adopting an existing connection as
        Supply In re-routes a real SupplyPortConnectorItem, mutating
        `_shared_state`) extends this to the same "each state that
        actually changed contributes its own entry" shape
        _commit_combined_undo_step() already establishes, so that
        re-route undoes atomically with the switch's own creation."""
        entries = []
        after = self._snapshot_power_switch_state()
        if before != after:
            entries.append((self._power_switch_state, before, after, restore_power_switch_diagram_state))
        if supply_before is not None:
            supply_after = self._snapshot_state()
            if supply_before != supply_after:
                entries.append((self._shared_state, supply_before, supply_after, restore_supply_port_diagram_state))
        if not entries:
            return
        self._undo_stack.push(_DiagramStateCommand(entries, self._refresh_current, self._notify_changed, label))
        self._notify_changed()

    def _snapshot_retention_state(self) -> RetentionDiagramState:
        return copy_retention_diagram_state(self._retention_state)

    def _snapshot_isolation_state(self) -> IsolationDiagramState:
        return copy_isolation_diagram_state(self._isolation_state)

    def _commit_isolation_undo_step(
        self, label: str, supply_before: SupplyPortDiagramState, isolation_before: IsolationDiagramState
    ) -> None:
        """Same shape as _commit_retention_undo_step() -- Isolation has
        no inline "Add new Supply Set..." flow of its own (explicit
        scope: its Supply Set dropdowns only ever offer EXISTING sets),
        so `supply_before` here never actually changes in practice, but
        is still threaded through for structural symmetry with every
        other placement type's own commit helper, and in case a future
        change ever needs it."""
        entries = []
        supply_after = self._snapshot_state()
        if supply_before != supply_after:
            entries.append((self._shared_state, supply_before, supply_after, restore_supply_port_diagram_state))
        isolation_after = self._snapshot_isolation_state()
        if isolation_before != isolation_after:
            entries.append((self._isolation_state, isolation_before, isolation_after, restore_isolation_diagram_state))
        if not entries:
            return
        self._undo_stack.push(_DiagramStateCommand(entries, self._refresh_current, self._notify_changed, label))
        self._notify_changed()

    def _snapshot_level_shifter_state(self) -> LevelShifterDiagramState:
        return copy_level_shifter_diagram_state(self._level_shifter_state)

    def _commit_level_shifter_undo_step(
        self, label: str, level_shifter_before: LevelShifterDiagramState
    ) -> None:
        """Same simple, single-state shape as _commit_isolation_undo_step()
        minus the supply-port half -- Level Shifter has no inline "Add
        new Supply Set..." flow of its own at all (it has no Supply Set
        reference in the first place, see level_shifter_overlay.py's own
        module docstring), so there is no second state that could ever
        change alongside this one."""
        after = self._snapshot_level_shifter_state()
        if level_shifter_before == after:
            return
        entry = (self._level_shifter_state, level_shifter_before, after, restore_level_shifter_diagram_state)
        self._undo_stack.push(_DiagramStateCommand([entry], self._refresh_current, self._notify_changed, label))
        self._notify_changed()

    def _commit_retention_undo_step(
        self, label: str, supply_before: SupplyPortDiagramState, retention_before: RetentionDiagramState
    ) -> None:
        """Same shape as _commit_combined_undo_step() (supply + domain),
        for an edit that touches both SupplyPortDiagramState (any supply
        ports newly introduced via the dialog's own "Add new Supply
        Set..." flow) and RetentionDiagramState atomically -- see
        create_retention_strategy()'s own docstring."""
        entries = []
        supply_after = self._snapshot_state()
        if supply_before != supply_after:
            entries.append((self._shared_state, supply_before, supply_after, restore_supply_port_diagram_state))
        retention_after = self._snapshot_retention_state()
        if retention_before != retention_after:
            entries.append((self._retention_state, retention_before, retention_after, restore_retention_diagram_state))
        if not entries:
            return
        self._undo_stack.push(_DiagramStateCommand(entries, self._refresh_current, self._notify_changed, label))
        self._notify_changed()

    def _snapshot_port_attribute_state(self) -> PortAttributeDiagramState:
        return copy_port_attribute_diagram_state(self._port_attribute_state)

    def _snapshot_macro_power_intent_state(self) -> MacroPowerIntentDiagramState:
        return copy_macro_power_intent_diagram_state(self._macro_power_intent_state)

    def _commit_macro_power_intent_undo_step(self, label: str, before: MacroPowerIntentDiagramState) -> None:
        """Single-state commit (unlike _commit_port_attribute_undo_step()
        above, this edit never touches SupplyPortDiagramState/
        PowerDomainDiagramState alongside it -- attaching a file to a
        macro never introduces a new Supply Set/supply-port name the way
        a Supply-Set-composing dialog can) -- still routed through the
        same _DiagramStateCommand `entries` list shape for consistency
        with every other undo step in this class, not a bespoke command
        type."""
        after = self._snapshot_macro_power_intent_state()
        if before == after:
            return
        entries = [(self._macro_power_intent_state, before, after, restore_macro_power_intent_diagram_state)]
        self._undo_stack.push(_DiagramStateCommand(entries, self._refresh_current, self._notify_changed, label))
        self._notify_changed()

    def _commit_port_attribute_undo_step(
        self,
        label: str,
        supply_before: SupplyPortDiagramState,
        domain_before: PowerDomainDiagramState,
        port_attribute_before: PortAttributeDiagramState,
    ) -> None:
        """Same "each state that actually changed contributes its own
        entry" shape as _commit_remove_domain_undo_step() above, for an
        edit that can touch all three at once: SupplyPortDiagramState
        (any brand-new supply-port *name* introduced via the dialog's
        own nested "Add New..." combo, materialized via
        _add_missing_supply_ports()), PowerDomainDiagramState (any
        Supply Set newly introduced via "Create Supply Set..."), and
        PortAttributeDiagramState (the assignments themselves) -- see
        set_port_attributes()'s own docstring."""
        entries = []
        supply_after = self._snapshot_state()
        if supply_before != supply_after:
            entries.append((self._shared_state, supply_before, supply_after, restore_supply_port_diagram_state))
        domain_after = self._snapshot_domain_state()
        if domain_before != domain_after:
            entries.append((self._domain_state, domain_before, domain_after, restore_power_domain_diagram_state))
        port_attribute_after = self._snapshot_port_attribute_state()
        if port_attribute_before != port_attribute_after:
            entries.append((self._port_attribute_state, port_attribute_before, port_attribute_after, restore_port_attribute_diagram_state))
        if not entries:
            return
        self._undo_stack.push(_DiagramStateCommand(entries, self._refresh_current, self._notify_changed, label))
        self._notify_changed()

    def _commit_remove_domain_undo_step(
        self,
        label: str,
        supply_before: SupplyPortDiagramState,
        domain_before: PowerDomainDiagramState,
        retention_before: RetentionDiagramState,
        power_switch_before: Optional[PowerSwitchDiagramState] = None,
        isolation_before: Optional[IsolationDiagramState] = None,
        level_shifter_before: Optional[LevelShifterDiagramState] = None,
    ) -> None:
        """Same "each state that actually changed contributes its own
        entry" shape as _commit_combined_undo_step()/
        _commit_retention_undo_step() above, extended to all six shared
        states at once -- "Remove Power Domain" (and, sharing this same
        helper, the Supply-Port-deletion cascade) cascades across its
        own Supply Sets/Ports, any Retention Strategy, any Power
        Switch, any Isolation Cell, and any Level Shifter that
        referenced the domain (see _remove_domain_cascade()'s own
        docstring), so a single Ctrl+Z must undo all six together, not
        six separate steps. `power_switch_before`/`isolation_before`/
        `level_shifter_before` are optional (default to None, meaning
        "not tracked by this caller") -- kept backward-compatible with
        any caller that genuinely never touches those states, same
        "optional extension" precedent _commit_power_switch_undo_step()'s
        own `supply_before` parameter already establishes."""
        entries = []
        supply_after = self._snapshot_state()
        if supply_before != supply_after:
            entries.append((self._shared_state, supply_before, supply_after, restore_supply_port_diagram_state))
        domain_after = self._snapshot_domain_state()
        if domain_before != domain_after:
            entries.append((self._domain_state, domain_before, domain_after, restore_power_domain_diagram_state))
        retention_after = self._snapshot_retention_state()
        if retention_before != retention_after:
            entries.append((self._retention_state, retention_before, retention_after, restore_retention_diagram_state))
        if power_switch_before is not None:
            power_switch_after = self._snapshot_power_switch_state()
            if power_switch_before != power_switch_after:
                entries.append((self._power_switch_state, power_switch_before, power_switch_after, restore_power_switch_diagram_state))
        if isolation_before is not None:
            isolation_after = self._snapshot_isolation_state()
            if isolation_before != isolation_after:
                entries.append((self._isolation_state, isolation_before, isolation_after, restore_isolation_diagram_state))
        if level_shifter_before is not None:
            level_shifter_after = self._snapshot_level_shifter_state()
            if level_shifter_before != level_shifter_after:
                entries.append((self._level_shifter_state, level_shifter_before, level_shifter_after, restore_level_shifter_diagram_state))
        if not entries:
            return
        self._undo_stack.push(_DiagramStateCommand(entries, self._refresh_current, self._notify_changed, label))
        self._notify_changed()

    def load_mapped(self, mapped: MappedDesign, root: Optional[HierarchyNode] = None) -> None:
        """Base hierarchy scene (via the inherited load()) plus the
        power-intent overlay on top -- see module docstring. `root`
        (explicit request: opening a sub-module in its own diagram tab)
        renders only that instance and its own descendants -- overlay
        items whose backing references fall outside `self._boxes` are
        already skipped gracefully by build_power_intent_overlay() itself
        (same "member path not found" tolerance it already needs for a
        DANGLING reference), so no changes were needed there."""
        self.load(mapped.rtl, root=root)
        self._index = build_power_intent_overlay(self.scene(), self._boxes, mapped)
        self._highlighted_constructs = []

    def apply_construct_selection(self, construct_kind: str, construct_id: str) -> None:
        """controller.selectionChanged handler for
        PowerIntentSelectionController -- the construct-kind/id
        counterpart to the base class's own apply_selection() (instance
        paths). Fires from BOTH directions: a real diagram click (via
        _on_scene_selection_changed()'s own SelectableItemMixin branch,
        legacy real-backend items only) and now also a Properties table
        row selection (explicit request) -- for the latter, `self._index`
        (built once from a real MappedDesign, see load_mapped()) is
        blind to everything the user created via the Diagram's own
        tools (Add Power Supply, Create Power Domain/Retention/
        Isolation/Power Switch/Power Switch Cell) -- confirmed by
        reading this method before extending it, the exact same
        "diagram-only state the Properties table already merges in
        alongside real rows" split properties_panel.py's own module
        docstring establishes. `_resolve_diagram_only_box_group()`/
        `_resolve_diagram_only_icon_group()` below close that gap by
        reusing the EXACT SAME per-kind group-resolution already built
        for the diagram-click direction (_supply_port_replica_group(),
        the _retention_icons/_isolation_icons/_power_switch_icons dicts
        keyed by shared id) rather than new lookup logic -- see each
        one's own docstring. Two independent highlight mechanisms
        applied together: InstanceBoxItem.set_highlighted() (merged
        into the SAME self._highlighted_constructs list/clear-then-
        apply pattern legacy items already use -- both expose the same
        method name) for box-based entities (Power Domain, Supply Set),
        and the icon system's own set_click_highlighted() (via
        _apply_icon_click_highlight(), the same helper the diagram-
        click direction already shares) for icon-based entities (Supply
        Port, Retention, Isolation, Power Switch, Power Switch Cell).
        Deliberately never touches Qt's own scene selection/setSelected()
        here -- only the visual highlight -- doing so would re-trigger
        _on_scene_selection_changed() and loop back into this same
        controller (see test_no_infinite_loop_between_diagram_and_properties)."""
        for item in self._highlighted_constructs:
            item.set_highlighted(False)
        box_group = self._resolve_diagram_only_box_group(construct_kind, construct_id)
        self._highlighted_constructs = self._index.get((construct_kind, construct_id), []) + box_group
        for item in self._highlighted_constructs:
            item.set_highlighted(True)
        self._apply_icon_click_highlight(self._resolve_diagram_only_icon_group(construct_kind, construct_id))

    def _resolve_diagram_only_box_group(self, construct_kind: str, construct_id: str) -> list[InstanceBoxItem]:
        """Every hierarchy box THIS diagram-only entity's own boundary
        actually sits on -- the box-based counterpart to
        _resolve_diagram_only_icon_group() below, for the two kinds
        that highlight as boxes rather than an icon (a Power Domain/
        Supply Set has no glyph of its own -- see power_domain_overlay.py's
        own module docstring)."""
        if construct_kind == "PowerDomain":
            domain = self._domain_state.domains.get(construct_id)
            if domain is None:
                return []
            return [self._boxes[p] for p in domain.owner_paths if p in self._boxes]
        if construct_kind == "SupplySet":
            # A Supply Set's own "natural diagram location" is simply
            # wherever it was composed (SupplySetPlacement.owner_paths) --
            # the same hierarchy scope its own Properties-table row is
            # already prefixed with (_hierarchy_prefixed_name()) --
            # rather than cross-referencing whichever Power Domain(s)
            # happen to reference it as Primary/Secondary right now.
            supply_set = self._domain_state.supply_sets.get(construct_id)
            if supply_set is None:
                return []
            return [self._boxes[p] for p in supply_set.owner_paths if p in self._boxes]
        return []

    def _resolve_diagram_only_icon_group(self, construct_kind: str, construct_id: str) -> list:
        """Every live icon representing THIS diagram-only entity --
        reuses the exact same per-kind group lookup
        _on_scene_selection_changed() already uses for the diagram-click
        direction (dict keyed by the shared logical id, or
        _supply_port_replica_group() for Supply Port/Internal Supply's
        own multi-hierarchy replication), just entered from a
        Properties-table (kind, id) pair instead of a clicked icon."""
        if construct_kind == "SupplyPort":
            icon = self._supply_port_icons.get(construct_id)
            return self._supply_port_replica_group(icon) if icon is not None else []
        if construct_kind == "PowerSwitch":
            existing = self._power_switch_state.switches.get(construct_id)
            return self._power_switch_icons.get(existing.id, []) if existing is not None else []
        if construct_kind == "IsolationStrategy":
            return self._isolation_icons.get(construct_id, [])
        if construct_kind == "LevelShifterStrategy":
            return self._level_shifter_icons.get(construct_id, [])
        if construct_kind == "RetentionStrategy":
            return self._retention_icons.get(construct_id, [])
        return []

    def _on_scene_selection_changed(self) -> None:
        """Extended for bug 2/4 (explicit request): besides the existing
        Milestone-2-construct/hierarchy-path handling, this now also
        applies/clears the Supply Port/Retention icon click-highlight --
        both icon types already have ItemIsSelectable set, and Qt's own
        native single-selection model already auto-deselects whatever
        was selected before (a box, a different icon) the moment a new
        item is clicked, so this slot firing IS the single, correct
        place to keep every kind of click-highlight in sync with
        whichever one thing is actually selected right now. Always still
        calls super()._on_scene_selection_changed() at the end (the
        existing hierarchy-block-path handling already correctly reads
        as "no boxes selected" whenever an icon -- not a box -- is what
        the scene now reports as selected, so nothing extra is needed
        there to clear a stale block highlight)."""
        scene = self.scene()
        selected = scene.selectedItems() if scene is not None else []
        for item in selected:
            if isinstance(item, SelectableItemMixin):
                self._clear_icon_click_highlight()
                self._power_intent_controller.selectionChanged.emit(item.construct_kind, item.construct_id)
                return
        supply_port = next((i for i in selected if isinstance(i, SupplyPortIconItem)), None)
        retention_badge = next((i for i in selected if isinstance(i, RetentionBadgeIconItem)), None)
        isolation_badge = next((i for i in selected if isinstance(i, IsolationBadgeIconItem)), None)
        level_shifter_badge = next((i for i in selected if isinstance(i, LevelShifterBadgeIconItem)), None)
        power_switch = next((i for i in selected if isinstance(i, PowerSwitchIconItem)), None)
        if supply_port is not None:
            # Bug fix: a Supply Port/Internal Supply belonging to a
            # multi-hierarchy Power Domain must group-highlight every
            # OTHER legitimate replica too, same "click any one
            # instance, highlight all of them together" mechanism
            # Retention/Power Switch already establish below -- this
            # branch previously only ever highlighted the single
            # clicked icon, never checking for a replication group at
            # all (see _supply_port_replica_group()'s own docstring).
            self._apply_icon_click_highlight(self._supply_port_replica_group(supply_port))
        elif retention_badge is not None:
            self._apply_icon_click_highlight(self._retention_icons.get(retention_badge.strategy_id, [retention_badge]))
        elif isolation_badge is not None:
            # Same "click any one spanned icon, highlight all of them
            # together" mechanism as Retention above -- explicit
            # request, per the Retention model an Isolation Cell shares.
            self._apply_icon_click_highlight(self._isolation_icons.get(isolation_badge.strategy_id, [isolation_badge]))
        elif level_shifter_badge is not None:
            # Bug fix (explicit request): Level Shifter was the one
            # remaining diagram object type never wired into this
            # dispatch at all -- same "click any one spanned icon,
            # highlight all of them together" mechanism Isolation
            # already establishes above, per the Isolation model Level
            # Shifter placement already follows.
            self._apply_icon_click_highlight(self._level_shifter_icons.get(level_shifter_badge.strategy_id, [level_shifter_badge]))
        elif power_switch is not None:
            # Item 10 (explicit request): same "click any one spanned
            # icon, highlight all of them together" mechanism Retention
            # already establishes above -- a multi-hierarchy switch
            # (item 9, target_ids) has one icon per member hierarchy it
            # spans, all sharing this same placement.id.
            self._apply_icon_click_highlight(self._power_switch_icons.get(power_switch.placement.id, [power_switch]))
        else:
            self._clear_icon_click_highlight()
        super()._on_scene_selection_changed()

    def _clear_icon_click_highlight(self) -> None:
        for item in self._click_highlighted_icons:
            item.set_click_highlighted(False)
        self._click_highlighted_icons = []

    def _apply_icon_click_highlight(self, items: list) -> None:
        self._clear_icon_click_highlight()
        for item in items:
            item.set_click_highlighted(True)
        self._click_highlighted_icons = list(items)

    def _extra_top_margin(self) -> dict:
        """Item 5 (explicit request): reserves TOP_MARGIN_FOR_SWITCH of
        extra room above a hierarchy box whenever it owns a Supply Port/
        Internal Supply that's both switched (has a Power Switch
        attached) AND top-edge-anchored -- the only edge a Power Switch
        actually renders above (see PowerSwitchIconItem.update_position()'s
        own fixed vertical offset); a port dragged to a different edge is
        a known, explicit gap, not silently mishandled (flagged here,
        not guessed at: the switch would still render correctly per its
        own position formula, just without reserved layout room on that
        side).

        Reads plain, already-persisted data
        (`_power_switch_state.switches`/`_shared_state.supply_port_placements`)
        rather than the currently-materialized `_supply_port_icons` --
        this runs from _rebuild() (via the base class's own hook call)
        BEFORE this pass's icons exist yet, so only the underlying state
        is available at this point regardless.

        Multiple switched top-edge ports on the SAME box still only need
        ONE shared margin (not summed) -- each switch renders
        independently above its own target's own X position along that
        edge, never stacked vertically with a sibling switch on the same
        box."""
        margins: dict = {}
        for switch in self._power_switch_state.switches.values():
            for target_id in switch.target_ids:  # item 9: one switch can span several targets
                target = self._shared_state.supply_port_placements.get(target_id)
                if target is None or target.edge != "top":
                    continue
                margins[target.owner_path] = TOP_MARGIN_FOR_SWITCH
        return margins

    def _extra_left_margin(self) -> dict:
        """Item 3 (explicit request), "make room" layout for an
        externally-placed Isolation (Location=parent/fanout): reserves
        LEFT_MARGIN_FOR_ISOLATION of room OUTSIDE a boundary block's own
        left edge, per block, so it shifts right within its own
        sibling packing rather than the badge overlapping whatever sits
        to its left. Same "reads plain, already-persisted data, runs
        BEFORE this pass's icons exist" precedent _extra_top_margin()
        already establishes. Several strategies/variants sharing the
        same block still only need ONE reserved margin (they stack
        vertically within it, not widen it -- same "not summed"
        precedent _extra_top_margin() already establishes for multiple
        switched ports on one box)."""
        margins: dict = {}
        for strategy in self._isolation_state.strategies.values():
            if strategy.location in ("self",):
                continue
            for owner_path in isolation_attachment_owner_paths(strategy, self._domain_state.domains):
                margins[owner_path] = LEFT_MARGIN_FOR_ISOLATION
        # Level Shifter (Isolation model, explicit request) -- same
        # externally-placed reservation, its own constant so a future
        # change to either badge's own footprint can't silently drift
        # the other out of sync. Bug fix ("Level Shifter icon not
        # rendered" repro): attachment_owner_paths(), NOT
        # owner_paths_for_elements(strategy.elements) directly -- a
        # broad (non-"elements") strategy has no elements at all to
        # derive attachment from, see that function's own docstring.
        for strategy in self._level_shifter_state.strategies.values():
            if strategy.location in ("self",):
                continue
            for owner_path in attachment_owner_paths(strategy, self._domain_state.domains):
                margins[owner_path] = LEFT_MARGIN_FOR_LEVEL_SHIFTER
        return margins

    def _extra_interior_top(self) -> dict:
        """Item 3 (explicit request), "make room" layout -- reserves
        room INSIDE a box's own top, summed from two independent
        reasons a box can need it (see hierarchy_scene_builder.py's own
        extra_interior_top docstring for what "interior" means: it
        pushes the box's own children down AND grows the box itself,
        rather than reserving space outside it):

        - Any top-edge Supply Port/Internal Supply on this box
          (INTERIOR_TOP_MARGIN_FOR_SUPPLY_PORT, supply_port_overlay.py)
          -- unlike _extra_top_margin() above (which only ever fires
          for a SWITCHED top-edge port, room for the switch icon
          rendering ABOVE it), this fires for EVERY top-edge Supply
          Port/Internal Supply regardless of whether it has a switch,
          since the icon itself always sits half INSIDE the box
          (explicit request: applies whether the box has children to
          push down, or is a leaf that just grows instead -- both
          handled uniformly by hierarchy_scene_builder.py's own
          extra_interior_top mechanism, see that module's docstring).
        - Any internally-placed Isolation (Location=self,
          INTERIOR_TOP_MARGIN_FOR_ISOLATION, isolation_overlay.py) on
          this box.

        Multiple qualifying reasons on the SAME box sum (not "take the
        max") -- each is an independent thing needing its own clearance
        near the box's own top, unlike same-KIND siblings (several
        switched ports, or several Isolation variants on one block),
        which still only ever need one shared reservation each (see
        each contributing loop's own "not summed" comment).

        KNOWN, EXPLICIT GAP (flagged, not silently mishandled, same
        "a differently-anchored port is a known gap" precedent
        _extra_top_margin() already establishes): this is the first
        margin kind that can change a box's own rect() SIZE (its
        height), not just its position within its own parent -- a
        top-edge icon's own position formula doesn't depend on box
        height at all (verified empirically), so it stays correct for
        free, but an icon anchored to the box's own BOTTOM/LEFT/RIGHT
        edge has no live position-recompute call the way a Power
        Switch icon does (on_move_callbacks) and could end up slightly
        off-centre relative to its own edge once the box's height
        grows for this reason. Not fixed in this pass."""
        margins: dict = {}
        supply_port_owners: set[str] = set()
        for placement in self._shared_state.supply_port_placements.values():
            if placement.edge == "top":
                supply_port_owners.add(placement.owner_path)
        for owner_path in supply_port_owners:
            margins[owner_path] = margins.get(owner_path, 0.0) + INTERIOR_TOP_MARGIN_FOR_SUPPLY_PORT
        isolation_self_owners: set[str] = set()
        for strategy in self._isolation_state.strategies.values():
            if strategy.location != "self":
                continue
            isolation_self_owners |= isolation_attachment_owner_paths(strategy, self._domain_state.domains)
        for owner_path in isolation_self_owners:
            margins[owner_path] = margins.get(owner_path, 0.0) + INTERIOR_TOP_MARGIN_FOR_ISOLATION
        level_shifter_self_owners: set[str] = set()
        for strategy in self._level_shifter_state.strategies.values():
            if strategy.location != "self":
                continue
            # Bug fix ("Level Shifter icon not rendered" repro): same
            # attachment_owner_paths() fix as _extra_left_margin() above.
            level_shifter_self_owners |= attachment_owner_paths(strategy, self._domain_state.domains)
        for owner_path in level_shifter_self_owners:
            margins[owner_path] = margins.get(owner_path, 0.0) + INTERIOR_TOP_MARGIN_FOR_LEVEL_SHIFTER
        return margins

    def _extra_interior_bottom(self) -> dict:
        """Explicit request: mirrors _extra_interior_top() above on the
        vertical axis -- a Ground-anchored (edge=="bottom") Supply Port/
        Internal Supply/macro PG pin needs the same clean power-net-
        routing clearance a top-anchored one already gets via
        INTERIOR_TOP_MARGIN_FOR_SUPPLY_PORT, just reserved below the
        block instead of above it -- reuses that SAME constant/
        calculation rather than inventing a separate one (its value is
        symmetric either way: room for the icon+label rendering half
        inside the block, on whichever edge it's actually anchored to).

        UNLIKE _extra_interior_top()'s own Supply Port loop (which only
        ever reads `supply_port_placements`, never `pg_pin_placements`
        -- a narrower, pre-existing scope specific to that function),
        this one explicitly covers macro PG pins too, matching the SAME
        scope Bug 2's own Ground-at-bottom placement rule already
        established (Supply Port, Internal Supply, AND macro ground
        pins alike -- see build_supply_port_overlay()'s own Ground-pin
        classification)."""
        margins: dict = {}
        bottom_owners: set[str] = set()
        for placement in self._shared_state.supply_port_placements.values():
            if placement.edge == "bottom":
                bottom_owners.add(placement.owner_path)
        for placement in self._shared_state.pg_pin_placements.values():
            if placement.edge == "bottom":
                bottom_owners.add(placement.owner_path)
        for owner_path in bottom_owners:
            margins[owner_path] = margins.get(owner_path, 0.0) + INTERIOR_TOP_MARGIN_FOR_SUPPLY_PORT
        # Merged Power Domain info box (explicit request, supersedes the
        # earlier separate domain-name-label/Supply-Set-display-box
        # overlays): reserves room for the domain's own combined box on
        # EVERY one of its member hierarchies -- content-dependent
        # (line-count), unlike the fixed-constant reason above, computed
        # via the SAME domain_info_box_required_height()
        # DomainInfoBoxItem itself sizes its own boundingRect() with, so
        # the box is never taller than the room actually reserved for
        # it. Summed (not "take the max") alongside the Ground-anchor
        # reason above, same "sum, not overlap" convention
        # _extra_interior_top() already established for ITS OWN multiple
        # independent reasons -- this box anchors bottom-RIGHT (see
        # DomainInfoBoxItem's own docstring), so it never visually
        # collides with a center-anchored Ground supply even though they
        # share the same reserved band.
        for domain in self._domain_state.domains.values():
            required = domain_info_box_required_height(domain, self._domain_state.supply_sets)
            for owner_path in domain.owner_paths:
                margins[owner_path] = margins.get(owner_path, 0.0) + required
        # Macro power-intent-attachment badge (explicit follow-up
        # request): reserves room for the "[UPF]"/"[CPF]" label on every
        # macro instance that currently has a file attached -- fixed-size
        # (see macro_power_intent_badge_required_height()'s own
        # docstring), summed alongside every other independent reason
        # above, same "sum, not overlap" convention.
        for owner_path in self._macro_power_intent_state.attachments:
            margins[owner_path] = margins.get(owner_path, 0.0) + macro_power_intent_badge_required_height()
        return margins

    _SUPPLY_LABEL_GAP = 8.0

    def _extra_interior_min_width(self) -> dict:
        """Bug fix (explicit report): a Supply Port/Internal Supply's own
        NAME label is horizontally centered on its icon's own `t`
        position along an edge -- two or more occupants on the SAME
        edge of the SAME box can end up close enough that their labels
        render on top of each other (see _edge_occupant_count()'s own
        docstring for the full root-cause writeup covering both
        _add_missing_supply_ports()'s batch-spread and
        _auto_link_cross_hierarchy_supply_sets()'s own hardcoded t --
        both fixed there to avoid landing on the exact same `t`, but
        pure fractional spacing within a box's own EXISTING width still
        isn't enough room for a narrow LEAF box (its own fixed
        LEAF_WIDTH has zero awareness of how many labels need to fit
        across it).

        Computed generically off REAL font metrics for each occupant's
        own name (QFontMetricsF against a bare default QFont -- the
        same font SupplyPortLabelItem itself renders with, since it
        never calls setFont()) -- summed side by side with
        `_SUPPLY_LABEL_GAP` between each pair, plus the same gap again
        as outer padding, so this box's own MINIMUM total width is
        exactly enough for every same-edge label to sit apart without
        touching. A box with 0 or 1 occupants on a given edge needs no
        floor at all -- nothing to collide with. Scoped to `edge in
        ("top", "bottom")` (the only two edges this app auto-assigns --
        "left"/"right" are user-drag-only and not this bug's own
        reported shape); a box with occupants on BOTH edges gets the
        wider of the two floors (`max`, not summed -- each edge's own
        labels render independently, never fighting for the SAME
        horizontal band the way two same-edge reasons would)."""
        metrics = QFontMetricsF(QFont())
        names_by_box_edge: dict[tuple[str, str], list[str]] = {}
        for placement in list(self._shared_state.supply_port_placements.values()) + list(self._shared_state.pg_pin_placements.values()):
            if placement.edge not in ("top", "bottom"):
                continue
            names_by_box_edge.setdefault((placement.owner_path, placement.edge), []).append(placement.name)
        margins: dict = {}
        for (owner_path, _edge), names in names_by_box_edge.items():
            if len(names) < 2:
                continue
            labels_width = sum(metrics.horizontalAdvance(name) for name in names) + self._SUPPLY_LABEL_GAP * (len(names) - 1)
            required = labels_width + self._SUPPLY_LABEL_GAP * 2
            margins[owner_path] = max(margins.get(owner_path, 0.0), required)
        return margins

    def _ground_supply_keys(self) -> set[tuple[str, str]]:
        """Bug 2 (explicit request): which placements currently carry a
        Ground role, because SOME Supply Set names them via `.ground`.

        Audit fix (category 04): returns `(owner_path, name)` PAIRS, not
        bare names. Matching on the bare name was only ever safe while
        Supply Port/Internal Supply names were globally unique -- once
        that rule was deliberately removed, a Supply Set on one
        hierarchy naming `VSS` as its ground silently re-anchored EVERY
        `VSS` in the design, including ports on unrelated hierarchies
        belonging to no Supply Set at all.

        Scoping rule: a placement qualifies when a Supply Set naming it
        as ground is composed on the placement's OWN hierarchy, or on an
        ANCESTOR of it. The ancestor half is required, not incidental --
        `_auto_link_cross_hierarchy_supply_sets()` materializes a local
        Internal Supply on each hierarchy that shares an ancestor's
        Supply Set WITHOUT extending that Supply Set's own `owner_paths`
        (verified against the live auto-link flow), so exact-path
        matching alone would have silently un-grounded every shared
        supply. Cascading downward mirrors how Power Domains already
        resolve (`find_effective_domain_for_path()`); there is
        deliberately no upward cascade, since a Supply Set composed deep
        in the hierarchy has no claim on a parent's own port.

        Kept as a set of plain tuples (not SupplySetPlacement objects)
        so supply_port_overlay.py still needs no import of
        power_domain_overlay.py -- that import would be circular."""
        keys: set[tuple[str, str]] = set()
        ground_scopes = [(owner_path, ss.ground) for ss in self._domain_state.supply_sets.values() if ss.ground for owner_path in ss.owner_paths]
        if not ground_scopes:
            return keys
        for placement in self._shared_state.supply_port_placements.values():
            for scope_path, ground_name in ground_scopes:
                if placement.name != ground_name:
                    continue
                if placement.owner_path == scope_path or placement.owner_path.startswith(f"{scope_path}/"):
                    keys.add((placement.owner_path, placement.name))
                    break
        return keys

    def _edge_occupant_count(self, box: InstanceBoxItem, edge: str) -> int:
        """Bug fix (explicit report): how many Supply Port/Internal
        Supply/macro PG-pin placements already occupy `edge` of `box`
        -- used by `_add_missing_supply_ports()`/
        `_auto_link_cross_hierarchy_supply_sets()` below to pick a NEW
        port's own `t` so it doesn't land on the exact same spot as one
        already there. Root cause of the reported bug: both of those
        methods computed `t` in isolation (a fixed `t=0.5` for every
        auto-linked port, or an i/(count+1) spread that only ever
        considered its OWN new batch) with no awareness of whatever
        ALREADY occupies that edge -- a Supply Set composed via
        `_add_missing_supply_ports()` and a shared ancestor Supply
        Set's own pin auto-linked moments later via
        `_auto_link_cross_hierarchy_supply_sets()` (see CHANGE 2's own
        auto-link extension) could both land at the identical t=0.5,
        rendering two icons -- and their own labels -- directly on top
        of each other."""
        return sum(
            1 for p in self._shared_state.supply_port_placements.values() if p.owner_path == box.path and p.edge == edge
        ) + sum(1 for p in self._shared_state.pg_pin_placements.values() if p.owner_path == box.path and p.edge == edge)

    def _next_edge_t(self, box: InstanceBoxItem, edge: str) -> float:
        """Bug fix (explicit report): the default `t` for a new Supply
        Port/Internal Supply -- auto-computed from how many placements
        already occupy `edge` of `box` (_edge_occupant_count()) so a
        caller that doesn't explicitly choose a `t` (e.g. a right-click
        "Add Power Supply" with no drag-and-drop position) doesn't land
        on the exact same spot as one already there. A lone/first
        occupant still gets the original centered t=0.5 default
        (existing_count=0 -> (0+1)/(0+2)=0.5) -- only a SECOND-or-later
        occupant on the same edge gets pushed to a genuinely different
        position."""
        existing_count = self._edge_occupant_count(box, edge)
        return (existing_count + 1) / (existing_count + 2)

    def _respace_edge_occupants(self, box: InstanceBoxItem, edge: str) -> None:
        """Bug fix (explicit report): _next_edge_t() alone (a box-width-
        AGNOSTIC fraction) doesn't guarantee two labels never overlap --
        confirmed empirically: a box already wider than
        _extra_interior_min_width()'s own computed floor (nothing left
        to grow) can still combine with a fractional-t placement that
        leaves too little ACTUAL pixel gap between two real label
        widths. This recomputes EVERY plain Supply Port/Internal
        Supply's own `t` on `edge` of `box` from real, measured label
        widths against the box's own CURRENT (already `_reflow_layout()`-
        grown) rect() -- laid out side by side, centered as a group,
        with `_SUPPLY_LABEL_GAP` between each pair -- so the box's
        already-reserved width (via the min-width floor above) and the
        actual POSITIONS chosen are always mutually consistent, by
        construction, not by two independently-tuned heuristics hoping
        to agree.

        Deliberately scoped to `supply_port_placements` only (never
        `pg_pin_placements`) -- a macro's own PG pin is `fixed`: its own
        edge/t are set once at construction and never move again (see
        SupplyPortIconItem.resnap_to_owner_edge()'s own docstring), a
        real, established invariant this method must not disturb. The
        reported bug is specifically about plain Supply Port/Internal
        Supply labels colliding with EACH OTHER, never with a PG pin,
        so this narrower scope covers it without touching that
        separate, stable system -- a PG pin sharing the same edge as
        several plain ports is a known, unhandled combination this
        method doesn't attempt to avoid.

        Calls `icon.resnap_to_owner_edge()` (not a bare `setPos()`) for
        each repositioned icon -- the exact same "recompute this icon's
        own position from its current placement.t, no connector-reroute
        cascade" primitive a box resize already uses, so a respacing
        composes correctly with everything that primitive already
        handles correctly (labels move with their own icon for free,
        being real Qt children of it)."""
        if edge not in ("top", "bottom"):
            return
        occupants = [p for p in self._shared_state.supply_port_placements.values() if p.owner_path == box.path and p.edge == edge]
        if len(occupants) < 2:
            return
        metrics = QFontMetricsF(QFont())
        widths = [metrics.horizontalAdvance(p.name) for p in occupants]
        total = sum(widths) + self._SUPPLY_LABEL_GAP * (len(occupants) - 1)
        box_width = box.rect().width()
        if box_width <= 0:
            return
        x = max(self._SUPPLY_LABEL_GAP, (box_width - total) / 2)
        for placement, label_width in zip(occupants, widths):
            center_x = x + label_width / 2
            placement.t = min(1.0, max(0.0, center_x / box_width))
            icon = self._supply_port_icons.get(placement.id)
            if icon is not None:
                icon.resnap_to_owner_edge()
            x += label_width + self._SUPPLY_LABEL_GAP

    def _reclassify_ground_supply_edges(self) -> None:
        """Bug 2 (explicit request): a plain Supply Port/Internal
        Supply's own Ground role, unlike a macro PG pin's (always known
        upfront from Liberty data, see build_supply_port_overlay()'s own
        docstring), can only become known LATER -- once some Supply Set
        actually names it via `.ground`. Called from every place a
        Supply Set's own `.ground` field can be set (create_power_domain(),
        edit_power_domain(), edit_supply_set()) so a freshly-ground-
        classified port snaps to the bottom edge immediately, rather than
        waiting for an unrelated full _rebuild() to happen to pass through
        build_supply_port_overlay()'s own OWN one-time reclassification.

        Audit fix (category 04): now called from DELETE paths too
        (`delete_supply_set_with_cascade()`, `remove_power_domain()`'s
        own cascade), not just the three set-a-ground-field paths, and
        resolves in BOTH directions -- a supply that LOSES its Ground
        role returns to the top edge instead of staying anchored at the
        bottom forever. The rule itself lives in
        supply_port_overlay.py's `resolve_ground_edge_change()` and is
        shared verbatim with build_supply_port_overlay()'s own
        rebuild-time pass, so the two can no longer disagree.

        A placement the user dragged themselves is never moved by this
        (dragging clears `ground_auto_anchored` -- see that field's own
        docstring). Falls back to mutating the placement directly (and
        clearing its label offset, so a later materialization recomputes
        it fresh) for a placement whose owner hierarchy is currently
        hidden -- not materialized as a live icon right now.

        Deliberately no early-out when there are zero ground keys: that
        is exactly the state reached by deleting the last Supply Set
        that named a ground, and it's precisely when the revert branch
        needs to run."""
        ground_keys = self._ground_supply_keys()
        for placement in self._shared_state.supply_port_placements.values():
            apply_ground_edge_classification(placement, ground_keys, self._supply_port_icons.get(placement.id))

    def _auto_link_cross_hierarchy_supply_sets(
        self, supply_set: SupplySetPlacement, target_owner_paths: Optional[list[str]] = None
    ) -> None:
        """Bug 9 (explicit request): a Supply Set's own Power/Ground/
        N-Well/P-Well field can now reference a name already used on a
        DIFFERENT, unrelated hierarchy (known_supply_names_globally()'s
        own docstring covers the dropdown-scope half of this bug --
        that whole-design pool is what makes such a selection possible
        in the first place). Selecting one -- rather than typing a
        brand-new name via "Add New...", which _add_missing_supply_ports()
        already materializes locally with an explicit chosen colour --
        must auto-create a matching Internal Supply on EVERY one of
        `target_owner_paths` that doesn't already have one, under the
        SAME name, and auto-connect it to the found source via a REAL,
        ordinary power net -- reusing try_connect_supply_ports()
        directly (explicit request: "no special-casing" -- undo/redo,
        multi-hierarchy replication, and anchor-point correctness all
        fall out of that shared method for free, exactly as a manually-
        drawn connector already gets them). try_connect_supply_ports()
        itself still enforces its own pre-existing is_nested_pair() rule
        unmodified -- a net only actually materializes between an
        ancestor and a descendant hierarchy (matching this bug's own
        "different hierarchy LEVELS" framing), never between two
        unrelated siblings; the Internal Supply is still created either
        way; a sibling case just ends up name-linked without a drawn net,
        the same silent-no-connector outcome try_connect_supply_ports()
        already has for any other non-nested pair.

        Called AFTER _add_missing_supply_ports() (so a brand-new "Add
        New..." name is already materialized locally and never mistaken
        for a cross-hierarchy reference) from every place a Supply Set's
        own 4 role fields can be set (create_power_domain(),
        edit_power_domain(), edit_supply_set()) -- mirrors
        _reclassify_ground_supply_edges()'s own placement/call-site shape
        exactly (Bug 2). Constructs the placement directly, same
        "materialize inline, no separate undo commit" precedent
        _add_missing_supply_ports() itself already follows.

        `target_owner_paths` (explicit request, ancestor Supply Set
        sharing extension to CHANGE 2 -- defaults to `supply_set.owner_paths`,
        preserving every pre-existing caller's behavior EXACTLY unchanged,
        since a native/newly-composed Supply Set's own `owner_paths`
        always already equals its owning domain's own `owner_paths` --
        see create_power_domain_dialog.py's own `_prompt_create_and_assign_supply_set()`):
        `_auto_link_domains_supply_sets()` below now passes the
        REFERENCING domain's own `owner_paths` explicitly instead,
        which DIFFERS from `supply_set.owner_paths` specifically for a
        SHARED ancestor Supply Set (CHANGE 2's own dropdown) -- that
        object's own `owner_paths` deliberately still points at the
        ORIGINAL (ancestor) hierarchy only (never mutated by sharing,
        see CreatePowerDomainDialog's own module docstring on why that
        matters for staying distinct from multi-hierarchy replication).
        Passing the new (sharing) domain's own `owner_paths` here is
        what actually instantiates real Internal Supplies for that
        Supply Set's own pins on the NEW hierarchy and auto-links each
        one to its EXISTING (ancestor-level) counterpart -- CHANGE 2's
        dialog-level spec never did this on its own, since the shared
        Supply Set's own pin names never appeared in the dialog's
        `new_supply_ports` dict (nothing "new" was typed for them)."""
        target_paths = target_owner_paths if target_owner_paths is not None else supply_set.owner_paths
        created_any = False
        touched_boxes: dict[str, InstanceBoxItem] = {}
        for name in (supply_set.power, supply_set.ground, supply_set.n_well, supply_set.p_well):
            if name is None:
                continue
            source = next(
                (p for p in self._shared_state.supply_port_placements.values() if p.name == name and p.owner_path not in target_paths),
                None,
            )
            if source is None:
                continue  # nothing to link to elsewhere -- either truly new (handled by _add_missing_supply_ports) or genuinely absent
            for owner_path in target_paths:
                already_local = any(
                    p.name == name and p.owner_path == owner_path for p in self._shared_state.supply_port_placements.values()
                )
                if already_local:
                    continue
                box = self._boxes.get(owner_path)
                if box is None:
                    continue
                # Bug fix (explicit report): computed FRESH per placement
                # (not a fixed t=0.5) via _next_edge_t() -- so an
                # auto-linked port never lands on the exact same spot as
                # whatever already occupies this box's own top edge
                # (e.g. a Supply Set composed moments earlier via
                # _add_missing_supply_ports(), the reported repro's own
                # "VDD_INT" then "VDD" collision), and two auto-linked
                # ports created within this SAME call (e.g. both VDD and
                # VSS for one owner_path) don't collide with EACH OTHER
                # either, since each iteration re-reads the count fresh
                # after the previous one was just added to the dict.
                edge = "top"
                new_placement = SupplyPortPlacement(
                    id=uuid4().hex,
                    owner_path=owner_path,
                    name=name,
                    color=QColor(source.color),
                    edge=edge,
                    t=self._next_edge_t(box, edge),
                    is_internal_supply=not self._is_top_level_box(box),
                )
                self._shared_state.supply_port_placements[new_placement.id] = new_placement
                new_icon = SupplyPortIconItem(box, new_placement)
                self._supply_port_icons[new_placement.id] = new_icon
                created_any = True
                touched_boxes[owner_path] = box
                source_icon = self._supply_port_icons.get(source.id)
                if source_icon is not None:
                    self.try_connect_supply_ports(source_icon, new_icon)
        if created_any:
            self._reflow_layout()  # item 3: make room for the new Internal Supply icon(s) -- same precedent _add_missing_supply_ports() already establishes
            for touched_box in touched_boxes.values():
                self._respace_edge_occupants(touched_box, "top")

    def _auto_link_domains_supply_sets(self, domain: PowerDomainPlacement) -> None:
        """Bug 9: runs _auto_link_cross_hierarchy_supply_sets() for
        EVERY Supply Set `domain` currently references (Primary AND
        every Secondary) -- create_power_domain()/edit_power_domain()
        each only ever hand a single freshly-composed Supply Set to
        edit_supply_set()'s own equivalent hook, but a domain can
        reference several at once (a pre-existing one picked from the
        dropdown, not just a brand-new inline one), each independently
        possibly needing a cross-hierarchy link. Idempotent for a
        Supply Set that needs no linking at all (already local
        everywhere it's referenced) -- safe to call unconditionally on
        every domain create/edit, not just when `new_supply_sets` was
        non-empty.

        Ancestor Supply Set sharing extension to CHANGE 2 (explicit
        request): passes `domain.owner_paths` explicitly as
        `target_owner_paths` -- identical to the old, implicit
        `supply_set.owner_paths` default for a NATIVE Supply Set (both
        are always the same list already), but for a SHARED one
        (`supply_set.owner_paths` still pointing at the ancestor's own
        hierarchy) this is what actually materializes+links its pins
        onto THIS domain's own (different) hierarchy too -- see
        `_auto_link_cross_hierarchy_supply_sets()`'s own updated
        docstring for the full "why"."""
        for supply_set_id in (domain.primary_supply_set, *domain.secondary_supply_sets):
            supply_set = self._domain_state.supply_sets.get(supply_set_id)
            if supply_set is not None:
                self._auto_link_cross_hierarchy_supply_sets(supply_set, target_owner_paths=domain.owner_paths)

    def _reflow_layout(self) -> None:
        """Item 3 (explicit request): generalizes what used to be
        _reflow_layout_for_power_switch() (same fix, same mechanism --
        see that method's own docstring for the full "why" a NEW
        placement needs an explicit, non-destructive reflow rather than
        waiting for the next full _rebuild()) into the ONE shared entry
        point for every "make room" layout change -- Power Switch
        (_extra_top_margin()), Supply Port/Internal Supply and
        internally-placed Isolation (_extra_interior_top()), and
        externally-placed Isolation (_extra_left_margin()) all reflow
        through this same call, per explicit request not to build three
        parallel layout mechanisms."""
        if self._design is None:
            return
        # Reflows every currently-open tab's own boxes (this one
        # included -- see `_reflow_siblings`'s own docstring for why
        # that's always safe), not just self._boxes.
        self._reflow_siblings()
        for icon in self._supply_port_icons.values():
            for callback in icon.on_move_callbacks:
                callback()
            for connector in icon.connectors:
                connector.update_route()

    def _reflow_own_boxes(self) -> None:
        """Reflows exactly THIS view's own `self._boxes` -- the non-
        recursive core `_reflow_siblings` (whether the private self-only
        default, or PowerIntentDiagramView's own multi-tab callback)
        actually calls per tab. Kept as its own method, separate from
        `_reflow_layout()` above, specifically so the multi-tab callback
        can invoke it on every OTHER view without looping back through
        that view's own `_reflow_layout()` -> `_reflow_siblings()` ->
        (every view again) -> ... chain."""
        if self._design is None:
            return
        reflow_hierarchy_scene(
            self._design,
            self._boxes,
            self._hidden_paths,
            root=self._root,
            extra_top_margin=self._extra_top_margin(),
            extra_left_margin=self._extra_left_margin(),
            extra_interior_top=self._extra_interior_top(),
            extra_interior_bottom=self._extra_interior_bottom(),
            extra_interior_min_width=self._extra_interior_min_width(),
        )

    def _rebuild(self) -> None:
        """Every supply-port icon/connector is parented into the scene
        _rebuild() is about to discard (icons parented to InstanceBoxItems,
        connectors added directly to the scene) -- Qt destroys them along
        with it. This does NOT mean the supply ports disappear, though:
        `_supply_port_placements`/`_pg_pin_placements`/
        `_supply_connector_placements` are plain data, untouched by any of
        that, and build_supply_port_overlay() rematerializes fresh icons
        from them against the new `self._boxes` right after -- so hiding
        some unrelated block (which is what actually triggers most
        rebuilds -- see HierarchyDiagramView._on_visibility_changed())
        does not wipe out supply ports anchored to boxes that are still
        visible. A placement whose own owner box just became hidden isn't
        materialized this pass (see build_supply_port_overlay()'s
        docstring) -- it comes back once unhidden.

        Bug fix (explicit report, found via the merged-info-box-vs-
        Ground-anchor overlap bug's own regression test): reclassifies
        Ground edges via `self._reclassify_ground_supply_edges()`
        BEFORE `super()._rebuild()` -- which is what actually computes
        `_extra_interior_top()`/`_extra_interior_bottom()` and builds
        the new geometry -- rather than only AFTER, via
        build_supply_port_overlay()'s own one-time reclassification
        below (line ~1596-ish). A placement's `.edge` can legitimately
        still read "top" at rebuild time despite already being a
        ground name (e.g. an undo/redo snapshot captured before some
        earlier reclassification pass ran) -- the OLD ordering computed
        this box's own required height using that STALE "top"
        classification (so `_extra_interior_bottom()` never counted its
        own Ground-anchor reservation at all, undercounting the box's
        true height by INTERIOR_TOP_MARGIN_FOR_SUPPLY_PORT), and only
        fixed the PLACEMENT itself afterward -- too late to affect the
        geometry this exact pass already froze in. Calling it first is
        safe before `self._boxes`/`self._supply_port_icons` are torn
        down: it operates purely on `self._shared_state.supply_port_placements`
        (mutating `.edge` directly), with its own OLD-icon branch
        (`icon.set_edge(...)`) touching only a doomed icon about to be
        discarded anyway -- either branch correctly mutates the SAME
        placement object `_extra_interior_top()`/`_extra_interior_bottom()`
        read moments later inside `super()._rebuild()`.
        build_supply_port_overlay()'s own later reclassification pass
        stays exactly as it was -- now simply a no-op every time (every
        placement it would touch is already correctly classified by
        then), the same "cheap, idempotent" contract it already
        documented for itself."""
        self._reclassify_ground_supply_edges()
        super()._rebuild()
        self._supply_connectors = []
        self._connector_drag_origin = None
        self._connector_drag_line = None
        self._segment_drag = None
        self._segment_drag_before = None
        self._icon_drag_snapshot = None
        self._supply_port_hover_box = None
        self._box_highlight_overlay = None  # scene item just got discarded along with everything else
        self._click_highlighted_icons = []  # ditto -- whatever they pointed at is gone; _on_scene_selection_changed() re-applies as needed
        self._retention_hover_box = None
        self._retention_highlight_overlay = None  # ditto
        self._isolation_cell_hover_box = None
        self._isolation_cell_highlight_overlay = None  # ditto
        self._level_shifter_hover_box = None
        self._level_shifter_highlight_overlay = None  # ditto
        self._internal_supply_hover_box = None
        self._internal_supply_highlight_overlay = None  # ditto
        self._virtual_supply_hover_box = None
        self._virtual_supply_highlight_overlay = None  # ditto
        self._power_switch_hover_icon = None
        self._power_switch_highlight_overlay = None  # ditto
        self._hovered_handle = None  # ditto -- the handle it pointed at no longer exists
        self._supply_port_icons = build_supply_port_overlay(
            self._boxes, self._shared_state.supply_port_placements, self._shared_state.pg_pin_placements, self._ground_supply_keys()
        )
        self._retention_icons = build_retention_overlay(self._boxes, self._retention_state.strategies, self._domain_state.supply_sets, self._supply_port_icons)
        self._isolation_icons = build_isolation_overlay(
            self._boxes, self._isolation_state.strategies, self._domain_state.supply_sets, self._supply_port_icons,
            domains=self._domain_state.domains,
        )
        inside_slots, outside_slots = self._isolation_badge_slot_counts_per_box()
        self._level_shifter_icons = build_level_shifter_overlay(
            self._boxes,
            self._level_shifter_state.strategies,
            self._domain_state.domains,
            self._supply_port_icons,
            initial_inside_slots=inside_slots,
            initial_outside_slots=outside_slots,
        )
        self._apply_group_centering()  # Bug 3: re-center the WHOLE Isolation+Level Shifter group now that the true combined total per block is known
        self._power_switch_icons = build_power_switch_overlay(self._supply_port_icons, self._power_switch_state.switches)
        self._power_switch_connectors = {}
        for switch_id, switch_icon_list in self._power_switch_icons.items():
            connectors_for_switch = []
            for switch_icon in switch_icon_list:
                # Scene-level item now (see PowerSwitchIconItem's own
                # docstring) -- unlike a parented child, it needs an
                # explicit addItem() to actually appear anywhere.
                self.scene().addItem(switch_icon)
                # switch_icon.target_icon (not placement.target_id/
                # target_ids[0]) -- item 9: a multi-target switch has
                # several icons, each already bound to its OWN specific
                # target at construction (build_power_switch_overlay()).
                target_icon = switch_icon.target_icon
                connector = SupplyPortConnectorItem(switch_icon, target_icon, port_a_handle_key="bottom")
                connector.setZValue(Z_POWER_SWITCH_CONNECTOR)
                self.scene().addItem(connector)
                switch_icon.connectors.append(connector)
                target_icon.connectors.append(connector)
                connectors_for_switch.append(connector)
            self._power_switch_connectors[switch_id] = connectors_for_switch
        # Bug fix: restoring manually-drawn connector pairs now runs
        # AFTER `_power_switch_icons` exists -- a persisted pair whose
        # own endpoint is a Power Switch's TOP anchor (Supply In)
        # previously never found a match here at all (this loop used to
        # run BEFORE any Power Switch icon existed this rebuild pass),
        # so a manually-drawn Supply In connector silently vanished from
        # the live diagram on every rebuild, even though the underlying
        # persisted pair -- and therefore _resolve_power_switch_supply_in_name()'s
        # own resolution -- stayed correct throughout. `switch_icon.key`
        # is per-replica-unique now (power_switch_connector_key()), so
        # this dict lookup resolves the exact right replica, not an
        # ambiguous shared one.
        switch_icon_by_key = {icon.key: icon for icons in self._power_switch_icons.values() for icon in icons}
        for key_a, key_b in self._shared_state.supply_connector_placements:
            icon_a = self._supply_port_icons.get(key_a) or switch_icon_by_key.get(key_a)
            icon_b = self._supply_port_icons.get(key_b) or switch_icon_by_key.get(key_b)
            if icon_a is not None and icon_b is not None:
                self._connect_icons(icon_a, icon_b)
        self._macro_power_intent_badges = build_macro_power_intent_badge_overlay(self._boxes, self._macro_power_intent_state.attachments)
        apply_domain_coloring(self._boxes, self._domain_state.domains)
        self._domain_info_boxes = build_domain_info_box_overlay(self._boxes, self._domain_state.domains, self._domain_state.supply_sets, self._supply_port_icons)

    def _on_context_menu(self, pos) -> None:
        """Right-clicking a supply-port icon (or its label/handles -- see
        _supply_port_icon_at()) shows its own Edit/Delete menu instead of
        the underlying hierarchy box's Copy Path/Hide/Add Power Supply
        one -- explicit request, "not applicable to Macro power supplies"
        (a fixed PG-pin icon falls straight through to the box's own menu
        below, same as if nothing were right-clicked at all). Checked
        before a connector, since an icon's own drawn pixmap can overlap
        a connector line right at the anchor -- the icon should win
        there. A no-op in DiagramMode.ZOOM_LENS -- explicit request:
        right-click-drag is repurposed for zoom-out in that mode (see
        mousePressEvent()'s own RightButton branch), and this slot is
        Qt's own customContextMenuRequested delivery (a separate path
        from raw mouse press/release events, fired on release regardless
        of what mousePressEvent already did with the event), so it needs
        its own explicit bypass rather than relying on event-consumption
        timing to suppress it."""
        if self._mode == DiagramMode.ZOOM_LENS:
            return
        # Power Switch icon (explicit request): right-clicking the switch
        # glyph itself shows plain "Edit"/"Delete" that funnel into the
        # EXACT SAME _prompt_edit_power_switch()/_prompt_delete_power_switch()
        # flow the target Supply Port's own "Edit Power Switch"/"Delete
        # Power Switch" menu entries already use -- just a second,
        # more-direct entry point. Checked FIRST: the switch icon draws
        # on top (higher Z, offset above its target), so a right-click
        # landing on the glyph is unambiguously about the switch; the
        # target Supply Port's own menu stays reachable by clicking the
        # port glyph itself (a different on-screen location).
        switch_icon = self._power_switch_icon_at(pos)
        if switch_icon is not None:
            menu = QMenu(self)
            actions = self._power_switch_icon_context_menu_actions(menu, switch_icon)
            chosen = menu.exec(self.viewport().mapToGlobal(pos))
            for action, callback in actions:
                if chosen is action:
                    callback()
                    break
            return
        icon = self._supply_port_icon_at(pos)
        if icon is not None and not icon.fixed:
            menu = QMenu(self)
            actions = self._supply_port_context_menu_actions(menu, icon)
            chosen = menu.exec(self.viewport().mapToGlobal(pos))
            for action, callback in actions:
                if chosen is action:
                    callback()
                    break
            return
        connector = self._connector_at(self.mapToScene(pos))
        if connector is not None:
            menu = QMenu(self)
            delete_action = menu.addAction("Delete")
            chosen = menu.exec(self.viewport().mapToGlobal(pos))
            if chosen is delete_action:
                self.delete_connector(connector)
            return
        retention_icon = self._retention_badge_at(pos)
        if retention_icon is not None:
            menu = QMenu(self)
            actions = self._retention_context_menu_actions(menu, retention_icon)
            chosen = menu.exec(self.viewport().mapToGlobal(pos))
            for action, callback in actions:
                if chosen is action:
                    callback()
                    break
            return
        isolation_icon = self._isolation_badge_at(pos)
        if isolation_icon is not None:
            menu = QMenu(self)
            actions = self._isolation_context_menu_actions(menu, isolation_icon)
            chosen = menu.exec(self.viewport().mapToGlobal(pos))
            for action, callback in actions:
                if chosen is action:
                    callback()
                    break
            return
        level_shifter_icon = self._level_shifter_badge_at(pos)
        if level_shifter_icon is not None:
            menu = QMenu(self)
            actions = self._level_shifter_context_menu_actions(menu, level_shifter_icon)
            chosen = menu.exec(self.viewport().mapToGlobal(pos))
            for action, callback in actions:
                if chosen is action:
                    callback()
                    break
            return
        # Empty canvas space (nothing else matched, and no hierarchy box
        # under the cursor): a bare "Extract Diagram" menu (explicit
        # request) that exports ONLY this one diagram -- see
        # extractDiagramRequested's own comment above.
        if self._box_at(pos) is None:
            menu = QMenu(self)
            extract_action = menu.addAction("Extract Diagram")
            chosen = menu.exec(self.viewport().mapToGlobal(pos))
            if chosen is extract_action:
                self.extractDiagramRequested.emit()
            return
        super()._on_context_menu(pos)

    def _connector_at(self, scene_pos: QPointF) -> Optional[SupplyPortConnectorItem]:
        """Which connector (its whole line, not just a draggable middle
        segment -- see SupplyPortConnectorItem.hit_test()) `scene_pos`
        is near enough to right-click "Delete", or None."""
        for connector in self._supply_connectors:
            if connector.hit_test(scene_pos):
                return connector
        return None

    def _power_switch_icon_context_menu_actions(self, menu: QMenu, switch_icon: PowerSwitchIconItem) -> list:
        """Split out from _on_context_menu() so it's directly testable
        (menu.exec() can't be driven headlessly) -- same pattern as
        _supply_port_context_menu_actions()/_retention_context_menu_actions()
        etc. Explicit request: PLAIN "Edit"/"Delete" labels (not "Edit
        Power Switch"/"Delete Power Switch"), each funneling into the
        EXACT SAME _prompt_edit_power_switch()/_prompt_delete_power_switch()
        flow the target Supply Port's own menu already uses -- resolved
        for THIS specific replica via switch_icon.target_icon (each
        replica's own upstream Supply In wiring is independent)."""
        edit_action = menu.addAction("Edit")
        delete_action = menu.addAction("Delete")
        return [
            (edit_action, lambda: self._prompt_edit_power_switch(switch_icon.target_icon, switch_icon.placement)),
            (delete_action, lambda: self._prompt_delete_power_switch(switch_icon.placement)),
        ]

    def _supply_port_context_menu_actions(self, menu: QMenu, icon: SupplyPortIconItem) -> list:
        """Split out from _on_context_menu() so it's directly testable
        without going through the (headlessly-undrivable) menu.exec() --
        same "build the actions, dispatch separately" split the base
        class's own _extra_context_menu_actions() hook already uses.

        Add/Edit/Delete Power Switch (explicit request) live HERE, on the
        Supply Port/Internal Supply icon's own right-click menu -- "only
        relevant when right-clicking a Supply Port or Internal Supply
        (not a hierarchy block)" is exactly this menu, structurally
        separate from _extra_context_menu_actions() (the hierarchy BOX's
        own menu, where "Add Power Supply"/"Add Internal Supply"/"Add
        Retention" live -- see _on_context_menu(): right-clicking an icon
        shows ONLY this menu, never falling through to the box's own).
        The spec's own "positioned above Add Retention, below Add
        Internal Supply" ordering describes THAT other, separate menu's
        content and can't apply literally here (this menu has no such
        siblings) -- read as the closest equivalent instead: placed after
        this menu's own pre-existing Edit/Delete, separated, the natural
        "next group" position within THIS menu.

        `icon.fixed` is never True here -- _on_context_menu() only ever
        calls this for a non-fixed icon (a PG pin gets no menu at all) --
        so target-TYPE validity needs no re-check; only the Power-Domain-
        membership rule (validation rule B) and the max-one-per-target
        rule (rule A) gate "Add Power Switch"."""
        edit_action = menu.addAction("Edit")
        delete_action = menu.addAction("Delete")
        menu.addSeparator()
        add_switch_action = menu.addAction("Add Power Switch")
        edit_switch_action = menu.addAction("Edit Power Switch")
        delete_switch_action = menu.addAction("Delete Power Switch")
        existing_switch = find_switch_for_target(self._power_switch_state.switches, icon.key)
        add_switch_action.setEnabled(existing_switch is None and self._validate_power_switch_target(icon) is None)
        edit_switch_action.setEnabled(existing_switch is not None)
        delete_switch_action.setEnabled(existing_switch is not None)
        return [
            (edit_action, lambda: self._prompt_edit_supply_port(icon)),
            (delete_action, lambda: self._prompt_delete_supply_port(icon)),
            (add_switch_action, lambda: self._prompt_add_power_switch(icon)),
            (edit_switch_action, lambda: self._prompt_edit_power_switch(icon, existing_switch) if existing_switch is not None else None),
            (delete_switch_action, lambda: self._prompt_delete_power_switch(existing_switch) if existing_switch is not None else None),
        ]

    def _power_domain_target_paths(self, box: InstanceBoxItem) -> list[str]:
        """Same "right-click on a member of the current multi-selection
        targets the whole selection" convention _hide_targets() already
        establishes -- explicit request/confirmed follow-up: multi-
        hierarchy Power Domain creation reuses the EXISTING Ctrl+click
        multi-select mechanism as-is, no new selection mechanism needed.
        Sorted for a deterministic, click-order-independent result."""
        if box.path in self._selected_paths and len(self._selected_paths) > 1:
            return sorted(self._selected_paths)
        return [box.path]

    _MACRO_IN_MULTI_SELECT_ERROR = "Create Power Domain is disabled: a macro (.lib) block is directly selected."
    _CONFLICTING_MULTI_SELECT_ERROR = "Create Power Domain is disabled: one or more selected blocks already belong to a Power Domain."

    def _multi_select_create_domain_block_reason(self, target_paths: list[str]) -> Optional[str]:
        """None if a multi-hierarchy Power Domain can be created from
        `target_paths` (2+ paths); otherwise the reason it's blocked.
        Two independent rules (explicit request, confirmed via
        AskUserQuestion):
        (1) a macro EXPLICITLY selected as one of the target paths
        (not merely swept in via a parent's own cascade -- a cascaded-
        only descendant is never actually IN `_selected_paths`/
        `target_paths` at all, only in the separate `_highlighted_paths`
        set -- see hierarchy_diagram_view.py's own module docstring)
        blocks outright.
        (2) any target path that already EXACTLY OWNS an existing
        domain (find_domain_for_owner(), the SAME rule the single-
        hierarchy case already uses for its own "Create" disabled
        state) blocks -- a path merely NESTED inside (not owning)
        another domain's own subtree is the already-supported nested-
        domain-carve-out case, not a conflict, so it does NOT block
        here."""
        for path in target_paths:
            candidate = self._boxes.get(path)
            if candidate is not None and candidate.definition.is_black_box:
                return self._MACRO_IN_MULTI_SELECT_ERROR
        for path in target_paths:
            if find_domain_for_owner(self._domain_state.domains, path) is not None:
                return self._CONFLICTING_MULTI_SELECT_ERROR
        return None

    def _prompt_create_multi_hierarchy_power_domain(self, target_paths: list[str]) -> None:
        boxes = [self._boxes[p] for p in target_paths if p in self._boxes]
        if not boxes:
            return
        dialog = CreatePowerDomainDialog(
            supply_sets_for_owners(self._domain_state, target_paths),
            known_supply_names_globally(self._shared_state),  # Bug 9: whole-design pool, not hierarchy-scoped -- see that function's own docstring
            self._existing_domain_names(),
            boxes[0].path,
            parent=self,
            owner_paths=target_paths,
            all_domains=self._domain_state.domains,
            all_supply_sets=self._domain_state.supply_sets,
            supply_port_icons=self._supply_port_icons,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.create_power_domain(
                boxes[0],
                dialog.name,
                dialog.color,
                dialog.transparency,
                dialog.primary_supply_set_id,
                dialog.secondary_supply_set_ids,
                dialog.new_supply_sets,
                dialog.new_supply_ports,
                additional_boxes=boxes[1:],
                new_supply_port_states=dialog.new_supply_port_states,
            )

    def _extra_context_menu_actions(self, menu: QMenu, box: InstanceBoxItem) -> list:
        """Explicit request: a separator between the base class's own
        Copy Path/Hide items (added just before this hook runs) and this
        hook's own additions, then Create/Edit/Remove Power Domain, then
        Add Power Supply/Add Internal Supply/Add Virtual Supply
        together, then Add Retention, then Add Isolation, then Add
        Level Shifter -- each of these groups in its own visually-
        separated section (explicit follow-up request added the
        separator between Add Isolation and Add Level Shifter
        specifically -- previously adjacent with none between them).

        Multi-select (2+ paths in the current selection, explicit
        request): ONLY "Create Power Domain" has a defined multi-target
        meaning anywhere in this feature -- every other action here is
        inherently single-target with no multi-hierarchy behaviour
        specified, so all of them are disabled uniformly (not guessed
        at) whenever the right-click targets a multi-selection, whether
        or not creating a domain from it is itself currently blocked
        (see _multi_select_create_domain_block_reason())."""
        # "Inspect Source File" (explicit request): positioned directly
        # below the base class's own "Hide" with NO separator between
        # them -- literally the first action added here, before this
        # hook's own separator immediately below. Valid for both an RTL
        # hierarchy block (its own .v/.sv/.vhd file) and a macro (its
        # own .lib file) alike -- both are just whichever file
        # `box.definition.source_file` already names, resolved once by
        # the SAME front-end elaborate() call that built this Definition
        # in the first place (see Definition.source_file's own docstring
        # in shared/models/rtl.py), never re-derived independently here.
        # Same "visible, greyed out -- not omitted" convention every
        # other level-gated action in this method already follows,
        # rather than hidden outright, for the one real, confirmed case
        # where a Definition's own source_file can be empty (VHDL, when
        # GHDL's own base_name -> source_file map doesn't cover an
        # entity -- see that same docstring).
        inspect_source_action = menu.addAction("Inspect Source File")
        menu.addSeparator()
        add_port_attribute_action = menu.addAction("Add Port Attribute")
        edit_port_attribute_action = menu.addAction("Edit Port Attribute")
        menu.addSeparator()
        create_domain_action = menu.addAction("Create Power Domain")
        edit_domain_action = menu.addAction("Edit Power Domain")
        remove_domain_action = menu.addAction("Remove Power Domain")
        menu.addSeparator()
        add_supply_action = menu.addAction("Add Power Supply")
        add_internal_supply_action = menu.addAction("Add Internal Supply")
        add_virtual_supply_action = menu.addAction("Add Virtual Supply")
        menu.addSeparator()
        add_retention_action = menu.addAction("Add Retention")
        menu.addSeparator()
        add_isolation_action = menu.addAction("Add Isolation")
        menu.addSeparator()
        add_level_shifter_action = menu.addAction("Add Level Shifter")
        menu.addSeparator()
        # "Load Power Intent"/"Edit Power Intent"/"Remove Power Intent"
        # (explicit request): attach an existing UPF/CPF file to a macro
        # instance. Edit/Remove-style split, mirroring Create/Edit/Remove
        # Power Domain exactly -- Load enabled with no file attached yet,
        # Edit/Remove enabled once one is.
        load_power_intent_action = menu.addAction("Load Power Intent")
        edit_power_intent_action = menu.addAction("Edit Power Intent")
        remove_power_intent_action = menu.addAction("Remove Power Intent")

        target_paths = self._power_domain_target_paths(box)
        if len(target_paths) > 1:
            block_reason = self._multi_select_create_domain_block_reason(target_paths)
            create_domain_action.setEnabled(block_reason is None)
            create_domain_action.setToolTip(block_reason or "")
            inspect_source_action.setEnabled(False)
            add_port_attribute_action.setEnabled(False)
            edit_port_attribute_action.setEnabled(False)
            edit_domain_action.setEnabled(False)
            remove_domain_action.setEnabled(False)
            add_supply_action.setEnabled(False)
            add_internal_supply_action.setEnabled(False)
            add_virtual_supply_action.setEnabled(False)
            add_retention_action.setEnabled(False)
            add_isolation_action.setEnabled(False)
            add_level_shifter_action.setEnabled(False)
            # "Load/Edit/Remove Power Intent" is the one OTHER action
            # (alongside "Create Power Domain" above) with a defined
            # multi-target meaning -- explicit request: attaching the
            # same file to every selected macro at once.
            macro_block_reason = self._macro_power_intent_multi_select_block_reason(target_paths)
            all_attached = macro_block_reason is None and all(p in self._macro_power_intent_state.attachments for p in target_paths)
            load_power_intent_action.setEnabled(macro_block_reason is None and not all_attached)
            edit_power_intent_action.setEnabled(macro_block_reason is None and all_attached)
            remove_power_intent_action.setEnabled(macro_block_reason is None and all_attached)
            for action in (load_power_intent_action, edit_power_intent_action, remove_power_intent_action):
                action.setToolTip(macro_block_reason or "")
            return [
                (inspect_source_action, None),
                (add_port_attribute_action, None),
                (edit_port_attribute_action, None),
                (create_domain_action, lambda: self._prompt_create_multi_hierarchy_power_domain(target_paths)),
                (edit_domain_action, None),
                (remove_domain_action, None),
                (add_supply_action, None),
                (add_internal_supply_action, None),
                (add_virtual_supply_action, None),
                (add_retention_action, None),
                (add_isolation_action, None),
                (add_level_shifter_action, None),
                # box itself (not one of the other selected paths) is
                # still the right single argument here -- both prompt
                # methods below resolve the FULL multi-selection
                # themselves via _macro_power_intent_target_paths(box),
                # the same mechanism `target_paths` above was already
                # computed from, so this is correct for 1 or 2+ targets
                # alike (same "one prompt method, resolves its own
                # targets" shape _prompt_inspect_source_file() etc. use).
                (load_power_intent_action, lambda: self._prompt_load_or_edit_macro_power_intent(box)),
                (edit_power_intent_action, lambda: self._prompt_load_or_edit_macro_power_intent(box)),
                (remove_power_intent_action, lambda: self._prompt_remove_macro_power_intent(box)),
            ]

        # "Inspect Source File" -- enabled whenever this Definition
        # actually has one, regardless of whether it's a macro (.lib) or
        # an RTL hierarchy block (.v/.sv/.vhd); the one real, confirmed
        # case where it's genuinely absent is a VHDL entity GHDL's own
        # base_name -> source_file map didn't cover (see
        # Definition.source_file's own docstring) -- disabled, not
        # hidden, same convention as every other action here.
        inspect_source_action.setEnabled(bool(box.definition.source_file))

        # "Add Port Attribute"/"Edit Port Attribute" -- top-level-only
        # (explicit request), same "visible, greyed out -- not omitted"
        # convention every other level-gated action here already
        # follows (Supply Port itself, e.g.), and excluding a macro for
        # the same reason Supply Port does (a macro's own pins are
        # already fixed by its Liberty definition -- see
        # _MACRO_SUPPLY_PORT_ERROR). Add/Edit split mirrors Create/Edit
        # Power Domain exactly: Add enabled with zero assignments made
        # yet anywhere in the design, Edit enabled once at least one
        # exists -- the SAME dialog serves both either way (see
        # _prompt_add_or_edit_port_attributes()'s own docstring).
        port_attribute_target_ok = self._is_top_level_box(box) and not box.definition.is_black_box
        has_port_attributes = bool(self._port_attribute_state.assignments)
        add_port_attribute_action.setEnabled(port_attribute_target_ok and not has_port_attributes)
        edit_port_attribute_action.setEnabled(port_attribute_target_ok and has_port_attributes)

        existing_domain = find_domain_for_owner(self._domain_state.domains, box.path)
        # Explicit request: "Create Power Domain" is disabled (visible,
        # greyed out -- not omitted) both for a macro (a macro's own
        # colour never changes -- see InstanceBoxItem.set_domain_color()'s
        # own unconditional exception, so rooting a domain AT a macro
        # would be a create action with no visible effect at all) and for
        # a box that already owns a domain -- "Edit Power Domain"/"Remove
        # Power Domain" are the only ways to change that one from here on;
        # a *descendant* box is still free to get its own, nested domain
        # (see power_domain_overlay.py's apply_domain_coloring() docstring).
        create_domain_action.setEnabled(not box.definition.is_black_box and existing_domain is None)
        edit_domain_action.setEnabled(existing_domain is not None)
        remove_domain_action.setEnabled(existing_domain is not None)
        # Explicit request: "Add Power Supply"/"Add Internal Supply" now
        # follow the SAME "visible, greyed out -- not omitted" convention
        # Edit/Remove Power Domain already establish above, using the
        # exact same _validate_supply_port_target()/
        # _validate_internal_supply_target() UI-layer checks the hover
        # highlight (_update_supply_port_hover()/_update_internal_supply_hover())
        # already uses -- one shared rule, three consumers (hover, this
        # menu, the _prompt_* methods' own click-time QMessageBox, which
        # stays in place regardless as defense-in-depth).
        add_supply_action.setEnabled(self._validate_supply_port_target(box) is None)
        add_internal_supply_action.setEnabled(self._validate_internal_supply_target(box) is None)
        add_virtual_supply_action.setEnabled(self._validate_virtual_supply_target(box) is None)
        # Explicit request: "Add Retention" follows the SAME convention
        # too, instead of always being clickable and only rejecting
        # after the fact via _prompt_add_retention()'s own QMessageBox
        # warning (that warning stays in place regardless -- defense-in-
        # depth, same two-layer precedent the two Add-Supply rules above
        # already establish). Mirrors _validate_retention_target()'s
        # exact two rules: a macro is never a valid target, and the
        # block must fall within a Power Domain's own effective coverage
        # -- find_effective_domain_for_path(), not find_domain_for_owner()
        # above, since a *descendant* of a domain-owning box (not just
        # the box that owns it directly) is still a valid Retention
        # target.
        effective_domain = find_effective_domain_for_path(self._domain_state.domains, box.path)
        add_retention_action.setEnabled(not box.definition.is_black_box and effective_domain is not None)
        # Explicit request: "Add Isolation" follows the SAME "visible,
        # greyed out -- not omitted" convention too, mirroring
        # _validate_isolation_target()'s own single rule exactly --
        # `box` must be a Power Domain's own BOUNDARY block
        # (find_domain_for_owner(), exact ownership), unlike Retention's
        # own cascading "anywhere inside the domain" rule above.
        add_isolation_action.setEnabled(self._validate_isolation_target(box) is None)
        # Explicit request: "Add Level Shifter" follows the SAME
        # convention, mirroring _validate_level_shifter_target()'s own
        # single rule exactly -- identical to Isolation's own rule
        # above (follows the Isolation model, section 5).
        add_level_shifter_action.setEnabled(self._validate_level_shifter_target(box) is None)
        # "Load Power Intent"/"Edit Power Intent"/"Remove Power Intent" --
        # single-target rule: `box` must be a macro (_NOT_A_MACRO_POWER_INTENT_ERROR
        # otherwise), same "visible, greyed out -- not omitted" convention
        # every other action here follows, with the same Load-vs-Edit/Remove
        # split "Add Port Attribute"/"Edit Port Attribute" already establishes.
        macro_target_ok = self._validate_macro_power_intent_target(box) is None
        already_attached = box.path in self._macro_power_intent_state.attachments
        load_power_intent_action.setEnabled(macro_target_ok and not already_attached)
        edit_power_intent_action.setEnabled(macro_target_ok and already_attached)
        remove_power_intent_action.setEnabled(macro_target_ok and already_attached)
        return [
            (inspect_source_action, lambda: self._prompt_inspect_source_file(box)),
            (add_port_attribute_action, lambda: self._prompt_add_or_edit_port_attributes(box)),
            (edit_port_attribute_action, lambda: self._prompt_add_or_edit_port_attributes(box)),
            (create_domain_action, lambda: self._prompt_create_power_domain(box)),
            (edit_domain_action, lambda: self._prompt_edit_power_domain(box)),
            (remove_domain_action, lambda: self._prompt_remove_power_domain(box)),
            (add_supply_action, lambda: self._prompt_add_power_supply(box)),
            (add_internal_supply_action, lambda: self._prompt_add_internal_supply(box)),
            (add_virtual_supply_action, lambda: self._prompt_add_virtual_supply(box)),
            (add_retention_action, lambda: self._prompt_add_retention(box)),
            (add_isolation_action, lambda: self._prompt_add_isolation(box)),
            (add_level_shifter_action, lambda: self._prompt_add_level_shifter(box)),
            (load_power_intent_action, lambda: self._prompt_load_or_edit_macro_power_intent(box)),
            (edit_power_intent_action, lambda: self._prompt_load_or_edit_macro_power_intent(box)),
            (remove_power_intent_action, lambda: self._prompt_remove_macro_power_intent(box)),
        ]

    def _prompt_inspect_source_file(self, box: InstanceBoxItem) -> None:
        """The directly-testable core of "Inspect Source File" -- opens
        a non-modal SourceViewerDialog (see that module's own docstring
        for why non-modal) against `box.definition.source_file`, keeping
        a persistent reference in `self._source_viewer_dialogs` for as
        long as the dialog stays open (required -- see that list's own
        docstring). A no-op, not a crash, when called with no source
        file at all (the menu action itself is already disabled in that
        case, but this stays safe against any other future caller)."""
        if not box.definition.source_file:
            return
        dialog = SourceViewerDialog(box.definition.source_file, parent=self)
        self._source_viewer_dialogs.append(dialog)
        dialog.finished.connect(lambda: self._source_viewer_dialogs.remove(dialog))
        dialog.show()

    _POWER_INTENT_FILE_FILTER = "Power Intent Files (*.upf *.cpf)"

    def _prompt_load_or_edit_macro_power_intent(self, box: InstanceBoxItem) -> None:
        """The single entry point behind BOTH "Load Power Intent" (no
        attachment yet) and "Edit Power Intent" (one already exists) --
        same dialog either way, same reasoning
        _prompt_add_or_edit_port_attributes() already documents for its
        own Add/Edit pair: there's nothing behaviourally different
        between the two beyond which menu label is enabled. Attaches the
        SAME picked file to every currently-selected macro target
        (explicit request), not just `box` itself."""
        target_paths = self._macro_power_intent_target_paths(box)
        existing = self._macro_power_intent_state.attachments.get(box.path)
        start_dir = os.path.dirname(existing) if existing else ""
        file_path, _ = QFileDialog.getOpenFileName(self, "Load Power Intent", start_dir, self._POWER_INTENT_FILE_FILTER)
        if not file_path:
            return
        self.attach_macro_power_intent(target_paths, file_path)

    def _prompt_remove_macro_power_intent(self, box: InstanceBoxItem) -> None:
        target_paths = self._macro_power_intent_target_paths(box)
        self.remove_macro_power_intent(target_paths)

    def attach_macro_power_intent(self, target_paths: list[str], file_path: str) -> None:
        """The directly-testable core of "Load Power Intent"/"Edit Power
        Intent" -- bypasses the file-picker dialog (same reasoning as
        create_power_domain()'s own docstring). Stores `file_path`
        VERBATIM (whatever the caller/file-picker handed in -- always
        absolute for a real QFileDialog pick, see
        macro_power_intent_overlay.py's own module docstring for why
        this is never normalized to relative at storage time) against
        every path in `target_paths` -- the SAME file for every currently
        -selected macro (explicit request), not one pick per target."""
        before = self._snapshot_macro_power_intent_state()
        for path in target_paths:
            self._macro_power_intent_state.attachments[path] = file_path
        # Rematerializes the "[UPF]"/"[CPF]" badge (a plain remove-then-
        # rebuild covers "replace an existing attachment with a
        # different-format file" too -- there's no in-place badge-text
        # mutation path, so a format swap always shows correctly) and
        # makes room for it -- same "mutate state, rebuild the one
        # affected overlay, then reflow" sequence create_power_domain()
        # already establishes for its own direct/first-call path (undo/
        # redo's own full _rebuild() already includes this badge, see
        # that method's own docstring, so this is only needed here).
        self._clear_macro_power_intent_badges()
        self._macro_power_intent_badges = build_macro_power_intent_badge_overlay(self._boxes, self._macro_power_intent_state.attachments)
        self._reflow_layout()
        self._commit_macro_power_intent_undo_step("Load Power Intent", before)

    def remove_macro_power_intent(self, target_paths: list[str]) -> None:
        """The directly-testable core of "Remove Power Intent". Matches
        this diagram's own majority "delete immediately, undo is the
        safety net" convention (no confirmation dialog) -- same as e.g.
        Retention/Isolation/Level Shifter removal, not Power Switch's own
        flagged, deliberate exception."""
        before = self._snapshot_macro_power_intent_state()
        for path in target_paths:
            self._macro_power_intent_state.attachments.pop(path, None)
        # Same rebuild-the-one-affected-overlay-then-reflow sequence as
        # attach_macro_power_intent() above -- the badge (and the room
        # reserved for it) must disappear immediately, not just on the
        # next unrelated rebuild.
        self._clear_macro_power_intent_badges()
        self._macro_power_intent_badges = build_macro_power_intent_badge_overlay(self._boxes, self._macro_power_intent_state.attachments)
        self._reflow_layout()
        self._commit_macro_power_intent_undo_step("Remove Power Intent", before)

    # Explicit request, reversing an earlier explicit request ("Supply
    # Port must be addable on ANY hierarchy block, leaf or not -- macros
    # are a completely valid and common place to attach a supply port"):
    # a macro's own pins are already fully fixed by its Liberty (.lib)
    # definition (see build_supply_port_overlay()'s own PG-pin
    # auto-derivation) -- a user-placed supply port has no real meaning
    # on one and is no longer allowed.
    _MACRO_SUPPLY_PORT_ERROR = "Power supplies cannot be added on a macro (.lib) block -- a macro's own pins are already fixed by its Liberty definition."
    # "Internal Supply" (explicit request): a second, distinct entity
    # type from Supply Port, mutually exclusive by hierarchy level --
    # Supply Port only on the design's true top-level block, Internal
    # Supply only on an inner (non-top-level) one, both excluding
    # macros. Confirmed via explicit follow-up: a macro is excluded from
    # Supply Port unconditionally, even in the (unusual) case where the
    # macro itself happens to be the top-level block -- not a special
    # case carved back out.
    _SUPPLY_PORT_NOT_TOP_LEVEL_ERROR = "Supply Port can only be added on the design's own top-level hierarchy block -- use Internal Supply on an inner block instead."
    _INTERNAL_SUPPLY_TOP_LEVEL_ERROR = "Internal Supply cannot be added on the design's own top-level hierarchy block -- use Supply Port there instead."
    _MACRO_INTERNAL_SUPPLY_ERROR = "Internal Supply cannot be added on a macro (.lib) block -- a macro's own pins are already fixed by its Liberty definition."
    # "Virtual Supply" (explicit request): a THIRD, distinct entity
    # type, placeable at ANY hierarchy level (unlike Supply Port/
    # Internal Supply, no level restriction at all) -- only a macro
    # target is ever rejected, same "a macro's own pins are already
    # fixed by its Liberty definition" reasoning every other supply-icon
    # type here already excludes macros for.
    _MACRO_VIRTUAL_SUPPLY_ERROR = "Virtual Supply cannot be added on a macro (.lib) block -- a macro's own pins are already fixed by its Liberty definition."
    _VIRTUAL_SUPPLY_NO_STATES_ERROR = "Virtual Supply requires at least one Power State."
    _VIRTUAL_SUPPLY_NAME_CONFLICT_ERROR_TEMPLATE = (
        "A Supply Port, Internal Supply, or Virtual Supply named '{name}' already exists -- Virtual Supply names must be unique "
        "across the whole design (unlike Supply Port/Internal Supply, which may still freely share a name with each other)."
    )
    _POWER_SWITCH_VIRTUAL_SUPPLY_ERROR = "A Power Switch cannot be added on a Virtual Supply -- it exists only to drive a macro's own internal PG pins."
    # "Load Power Intent" (explicit request): the inverse level rule from
    # every Add-Supply/Add-Retention/Add-Isolation action above -- those
    # all EXCLUDE a macro (its own pins are already fixed by its Liberty
    # definition); this one ONLY ever applies TO a macro (attaching a
    # power intent file to an ordinary RTL hierarchy block has no defined
    # meaning here -- the whole feature exists to describe a black-box
    # macro's own internal power intent, which the RTL/.lib elaborator
    # has no visibility into at all).
    _NOT_A_MACRO_POWER_INTENT_ERROR = "A Power Intent file can only be attached to a macro (.lib) block."
    _MIXED_MACRO_POWER_INTENT_SELECTION_ERROR = "Load/Edit/Remove Power Intent is disabled: the current multi-selection mixes macros that already have an attached file with ones that don't."

    @staticmethod
    def _is_top_level_box(box: InstanceBoxItem) -> bool:
        """True for the design's own true top-level block (a single-
        segment path, e.g. "top") -- deliberately NOT "whichever box is
        this sub-hierarchy tab's own rendering root": a box's own `.path`
        is always the absolute design path regardless of which node a
        tab happens to be rooted at (see build_hierarchy_scene()'s own
        `root` parameter docstring), and Supply Port/Internal Supply are
        real UPF/CPF-level concepts (a boundary port vs. an internal net)
        that only make sense relative to the true design root, not
        whichever subtree a given tab happens to be showing."""
        return "/" not in box.path

    def _validate_supply_port_target(self, box: InstanceBoxItem) -> Optional[str]:
        """UI-layer validation only (hover gating, context-menu enable
        state, _prompt_add_power_supply() below) -- deliberately NOT
        re-checked inside add_supply_port() itself for the level rule
        (explicit scope decision: the "directly-testable core" functions
        stay level-agnostic, only macro-excluded as before, so the large
        pre-existing test suite -- which places Supply Ports on arbitrary
        non-top-level boxes to test unrelated concerns -- needs no sweeping
        rewrite; every real user-facing entry point funnels through this
        check regardless, so the actual product behaviour is unaffected).
        Macro checked first, same "more fundamental rejection reason
        first" precedent _validate_retention_target() already
        establishes."""
        if box.definition.is_black_box:
            return self._MACRO_SUPPLY_PORT_ERROR
        if not self._is_top_level_box(box):
            return self._SUPPLY_PORT_NOT_TOP_LEVEL_ERROR
        return None

    def _macro_power_intent_target_paths(self, box: InstanceBoxItem) -> list[str]:
        """Same "right-click on a member of the current multi-selection
        targets the whole selection" convention _power_domain_target_paths()
        already establishes, reused as-is (explicit request: "reuse
        existing multi-select convention") -- no new selection mechanism
        for this feature."""
        return self._power_domain_target_paths(box)

    def _validate_macro_power_intent_target(self, box: InstanceBoxItem) -> Optional[str]:
        """UI-layer validation only (context-menu enable state,
        _prompt_load_or_edit_macro_power_intent()/_prompt_remove_macro_power_intent()
        below) -- single-target rule: `box` itself must be a macro. The
        multi-select "mixed attached/unattached" rule lives separately in
        _macro_power_intent_multi_select_block_reason() below, since it
        needs the whole target-path list, not just one box."""
        if not box.definition.is_black_box:
            return self._NOT_A_MACRO_POWER_INTENT_ERROR
        return None

    def _macro_power_intent_multi_select_block_reason(self, target_paths: list[str]) -> Optional[str]:
        """None if Load/Edit/Remove Power Intent can act uniformly across
        every path in `target_paths` (2+ paths); otherwise the reason
        it's blocked. Two independent rules: (1) any target that isn't a
        macro blocks outright (same reasoning as
        _validate_macro_power_intent_target() above, applied to every
        target); (2) a mix of already-attached and not-yet-attached
        macros blocks too -- "Load"/"Edit"/"Remove" each have a single,
        uniform meaning across the whole selection, and there's no
        defined behaviour for applying "Load" to one target while
        silently "Edit"-ing another."""
        for path in target_paths:
            candidate = self._boxes.get(path)
            if candidate is None or not candidate.definition.is_black_box:
                return self._NOT_A_MACRO_POWER_INTENT_ERROR
        attached_count = sum(1 for path in target_paths if path in self._macro_power_intent_state.attachments)
        if 0 < attached_count < len(target_paths):
            return self._MIXED_MACRO_POWER_INTENT_SELECTION_ERROR
        return None

    def _validate_internal_supply_target(self, box: InstanceBoxItem) -> Optional[str]:
        """Mirror image of _validate_supply_port_target() above -- same
        UI-layer-only scope decision."""
        if box.definition.is_black_box:
            return self._MACRO_INTERNAL_SUPPLY_ERROR
        if self._is_top_level_box(box):
            return self._INTERNAL_SUPPLY_TOP_LEVEL_ERROR
        return None

    def _validate_virtual_supply_target(self, box: InstanceBoxItem) -> Optional[str]:
        """Unlike _validate_supply_port_target()/_validate_internal_supply_target()
        above, no hierarchy-LEVEL rule at all (explicit request: "Can be
        added at ANY level of hierarchy... no top-level-only or
        inner-only restriction") -- only a macro target is ever
        rejected."""
        if box.definition.is_black_box:
            return self._MACRO_VIRTUAL_SUPPLY_ERROR
        return None

    def _prompt_add_power_supply(self, box: InstanceBoxItem) -> None:
        """Reuses this SAME dialog for both entry points (explicit
        request: "do NOT build a new one") -- the right-click context-
        menu action and the Supply Port tool's own hover+click placement
        (see mousePressEvent()'s own DiagramMode.SUPPLY_PORT branch)
        differ only in how the target box is chosen, never in how
        name/colour are collected; both place at the same "top, centered"
        default position (add_supply_port()'s own edge/t defaults).

        Checked *before* ever constructing the dialog (explicit request:
        an invalid target rejects every possible name, so looping the
        dialog back open the way a duplicate-name ValueError already does
        below would be pointless busywork for the user -- they'd have to
        hit Cancel regardless of what they type)."""
        error = self._validate_supply_port_target(box)
        if error is not None:
            message_box.warning(self, "Add Power Supply", error)
            return
        dialog = AddPowerSupplyDialog(self, target_path=box.path)
        while dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                self.add_supply_port(box, dialog.name, dialog.color, states=dialog.states, direction=dialog.direction)
                return
            except ValueError as exc:
                message_box.warning(self, "Add Power Supply", str(exc))
                # loop: dialog reopens with the name/colour the user already
                # typed still in place, so they only need to fix the name

    def _prompt_add_internal_supply(self, box: InstanceBoxItem) -> None:
        """Mirror image of _prompt_add_power_supply() above -- same
        "reuse the SAME dialog, do NOT build a new one" precedent
        (Internal Supply needs no fields beyond Name/Colour, same as
        Supply Port)."""
        error = self._validate_internal_supply_target(box)
        if error is not None:
            message_box.warning(self, "Add Internal Supply", error)
            return
        dialog = AddPowerSupplyDialog(self, target_path=box.path, is_internal_supply=True)
        dialog.setWindowTitle("Add Internal Supply")
        while dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                self.add_internal_supply(box, dialog.name, dialog.color)
                return
            except ValueError as exc:
                message_box.warning(self, "Add Internal Supply", str(exc))

    def _prompt_add_virtual_supply(self, box: InstanceBoxItem) -> None:
        """Mirror image of _prompt_add_power_supply()/_prompt_add_internal_supply()
        above -- same "reuse the SAME dialog, do NOT build a new one"
        precedent. Unlike Internal Supply, Virtual Supply DOES get the
        dialog's own real, editable Power States table (is_internal_supply
        left False -- see AddPowerSupplyDialog's own module docstring:
        the states-widget-vs-inherited-label branch there only ever
        keys off `is_internal_supply`, so Virtual Supply naturally falls
        into the "real states table" branch already used for a plain
        Supply Port) -- `is_virtual_supply=True` only changes accept()'s
        own validation (Power States become REQUIRED, not optional)."""
        error = self._validate_virtual_supply_target(box)
        if error is not None:
            message_box.warning(self, "Add Virtual Supply", error)
            return
        dialog = AddPowerSupplyDialog(self, target_path=box.path, is_virtual_supply=True)
        dialog.setWindowTitle("Add Virtual Supply")
        while dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                self.add_virtual_supply(box, dialog.name, dialog.color, states=dialog.states)
                return
            except ValueError as exc:
                message_box.warning(self, "Add Virtual Supply", str(exc))

    def _paths_share_a_multi_hierarchy_domain(self, path_a: str, path_b: str) -> bool:
        """True iff SOME real PowerDomainPlacement's own `owner_paths`
        lists BOTH `path_a` and `path_b` as members -- two hierarchies
        only count as legitimately sharing a replicated entity if a
        real, existing multi-hierarchy Power Domain record actually
        spans both of them, not merely because two ports on them happen
        to share a name. Still used by _corresponding_supply_port_replicas()
        (Name/Color edit propagation across multi-hierarchy replicas) --
        CHANGE 1 removed the global/local Supply Port/Internal Supply
        name-uniqueness rule this method used to ALSO support
        (_check_unique_name_globally(), now deleted), but that removal
        doesn't touch replica-propagation, a genuinely separate concern."""
        return any(path_a in domain.owner_paths and path_b in domain.owner_paths for domain in self._domain_state.domains.values())

    def add_supply_port(
        self,
        box: InstanceBoxItem,
        name: str,
        color: QColor,
        edge: str = "top",
        t: Optional[float] = None,
        states: Optional[list[SupplyPortState]] = None,
        direction: str = "",
    ) -> SupplyPortIconItem:
        """The directly-testable core of "Add Power Supply" -- bypasses
        the dialog (menu.exec()/dialog.exec() can't be driven headlessly,
        same reasoning as _copy_path()/_hide_targets() in the base class).
        Registers a persisted SupplyPortPlacement first (see _rebuild()'s
        docstring), then materializes the icon from it -- the same path a
        rebuild's own restoration takes, just for one fresh placement.
        `edge` (explicit request: Supply Port drag-and-drop placement
        snaps to the nearest edge of the dropped-on box -- see
        _nearest_edge_and_t()) defaults to the same "top" placement
        every pre-existing caller (the dialog-driven right-click flow,
        _add_missing_supply_ports()) already relied on implicitly. `t`
        (bug fix, explicit report -- was a fixed `t: float = 0.5`)
        defaults to None, auto-computed via _next_edge_t() when omitted
        so a SECOND-or-later same-edge occupant doesn't land on the
        exact same spot (and therefore the exact same rendered label
        position) as one already there -- an explicit `t` (e.g. a real
        drag-and-drop drop position) is still honored exactly as given.
        Also raises ValueError for a macro (is_black_box) target -- see
        _MACRO_SUPPLY_PORT_ERROR -- as a second, structural layer of the
        same rule _prompt_add_power_supply() already checks before ever
        opening the dialog; kept here too so this directly-testable core
        stays correct on its own for any other/future caller that
        bypasses the dialog-level check.

        CHANGE 1 (explicit request, supersedes the earlier global/local
        name-uniqueness rule): no name-collision check at all anymore --
        two Supply Ports/Internal Supplies may freely share a name,
        anywhere in the design, with no forced/implicit relationship
        between them. The auto-link dropdown (still offered by the
        Create/Edit Power Domain and Create/Edit Retention Supply-Set
        flows) remains exactly as implemented, but is now a purely
        OPTIONAL convenience for a user who wants to explicitly link a
        new entity to an existing one -- see
        _auto_link_cross_hierarchy_supply_sets()'s own updated
        docstring."""
        if box.definition.is_black_box:
            raise ValueError(self._MACRO_SUPPLY_PORT_ERROR)
        if not validate_no_spaces(name):
            raise ValueError(NO_SPACES_ERROR)
        auto_t = t is None
        if auto_t:
            t = self._next_edge_t(box, edge)
        before = self._snapshot_state()
        placement = SupplyPortPlacement(
            id=uuid4().hex, owner_path=box.path, name=name, color=QColor(color), edge=edge, t=t,
            states=list(states or []),
            # Supply Port only -- add_internal_supply()/add_virtual_supply()
            # never pass this, and their dialogs do not offer it.
            direction=direction,
        )
        self._shared_state.supply_port_placements[placement.id] = placement
        icon = SupplyPortIconItem(box, placement)
        self._supply_port_icons[placement.id] = icon
        self._reflow_layout()  # item 3: make room for a new top-edge Supply Port -- see _extra_interior_top()'s own docstring
        # Bug fix (explicit report): only re-space when `t` was auto-
        # computed -- an EXPLICIT `t` (e.g. a real drag-and-drop drop
        # position) is the caller's own deliberate placement and must
        # not be silently overridden by this collision-avoidance pass.
        if auto_t:
            self._respace_edge_occupants(box, edge)
        self._commit_undo_step("Add Power Supply", before)
        return icon

    def add_internal_supply(
        self, box: InstanceBoxItem, name: str, color: QColor, edge: str = "top", t: Optional[float] = None
    ) -> SupplyPortIconItem:
        """The directly-testable core of "Add Internal Supply" -- mirrors
        add_supply_port() above exactly (same reasoning throughout,
        including the same `t=None` auto-spacing bug fix), the one
        difference being `is_internal_supply=True` on the resulting
        placement. Still raises for a macro target (same "second,
        structural layer" precedent add_supply_port() already
        establishes for its own macro rule) -- but deliberately does NOT
        raise for a top-level target here (see _validate_internal_supply_target()'s
        own docstring on why the level rule is UI-layer-only)."""
        if box.definition.is_black_box:
            raise ValueError(self._MACRO_INTERNAL_SUPPLY_ERROR)
        if not validate_no_spaces(name):
            raise ValueError(NO_SPACES_ERROR)
        auto_t = t is None
        if auto_t:
            t = self._next_edge_t(box, edge)
        before = self._snapshot_state()
        placement = SupplyPortPlacement(id=uuid4().hex, owner_path=box.path, name=name, color=QColor(color), edge=edge, t=t, is_internal_supply=True)
        self._shared_state.supply_port_placements[placement.id] = placement
        icon = SupplyPortIconItem(box, placement)
        self._supply_port_icons[placement.id] = icon
        self._reflow_layout()  # item 3: make room for a new top-edge Internal Supply -- see _extra_interior_top()'s own docstring
        if auto_t:
            self._respace_edge_occupants(box, edge)
        self._commit_undo_step("Add Internal Supply", before)
        return icon

    def _virtual_supply_name_conflict(self, name: str, exclude_id: Optional[str] = None) -> bool:
        """Section 3 (explicit request): Virtual Supply's own Name
        uniqueness is checked ONLY at Virtual Supply creation/rename
        time, against the FULL set of every Supply Port/Internal
        Supply/Virtual Supply name in the design -- deliberately NOT a
        reintroduction of CHANGE 1's removed Supply-Port-vs-Internal-
        Supply uniqueness rule (those two still freely share names with
        each other; this check only ever fires because ONE side of the
        comparison is a Virtual Supply). `exclude_id` (used by
        edit_supply_port() below, on a rename) excludes the placement
        being renamed itself from the collision check -- keeping its
        own unchanged name is never a conflict with itself."""
        return any(p.id != exclude_id and p.name == name for p in self._shared_state.supply_port_placements.values())

    def add_virtual_supply(
        self,
        box: InstanceBoxItem,
        name: str,
        color: QColor,
        edge: str = "top",
        t: Optional[float] = None,
        states: Optional[list[SupplyPortState]] = None,
    ) -> SupplyPortIconItem:
        """The directly-testable core of "Add Virtual Supply" -- mirrors
        add_supply_port()/add_internal_supply() above, with THREE real
        differences: (1) no hierarchy-level rule at all, only the macro
        exclusion (see _validate_virtual_supply_target()'s own
        docstring -- deliberately NOT re-checked here either, same
        "directly-testable core stays level-agnostic" scope decision
        add_supply_port() already establishes, though moot here since
        there IS no level rule to skip); (2) Power States are REQUIRED,
        never empty (section 2, explicit request) -- checked here too,
        not just in the dialog's own accept(), so this core stays
        correct on its own for any caller that bypasses the dialog;
        (3) Name uniqueness IS checked (section 3, explicit request),
        against the full supply_port_placements set -- the one
        deliberate exception to CHANGE 1's "no name-collision check at
        all" rule, scoped specifically to Virtual Supply."""
        if box.definition.is_black_box:
            raise ValueError(self._MACRO_VIRTUAL_SUPPLY_ERROR)
        if not validate_no_spaces(name):
            raise ValueError(NO_SPACES_ERROR)
        if not states:
            raise ValueError(self._VIRTUAL_SUPPLY_NO_STATES_ERROR)
        if self._virtual_supply_name_conflict(name):
            raise ValueError(self._VIRTUAL_SUPPLY_NAME_CONFLICT_ERROR_TEMPLATE.format(name=name))
        auto_t = t is None
        if auto_t:
            t = self._next_edge_t(box, edge)
        before = self._snapshot_state()
        placement = SupplyPortPlacement(
            id=uuid4().hex, owner_path=box.path, name=name, color=QColor(color), edge=edge, t=t, is_virtual_supply=True, states=list(states)
        )
        self._shared_state.supply_port_placements[placement.id] = placement
        icon = SupplyPortIconItem(box, placement)
        self._supply_port_icons[placement.id] = icon
        self._reflow_layout()  # item 3: make room for a new top-edge Virtual Supply -- see _extra_interior_top()'s own docstring
        if auto_t:
            self._respace_edge_occupants(box, edge)
        self._commit_undo_step("Add Virtual Supply", before)
        return icon

    def _prompt_add_or_edit_port_attributes(self, box: InstanceBoxItem) -> None:
        """The SAME dialog serves both "Add Port Attribute" and "Edit
        Port Attribute" (see _extra_context_menu_actions()'s own Create/
        Edit-style enable/disable gating: Add enabled with zero
        assignments yet on this design, Edit enabled once at least one
        exists) -- it always shows the full current state of every
        port, so there's nothing behaviourally different between the
        two entry points beyond which one is enabled."""
        dialog = PortAttributeDialog(
            # Full I/O, interface signals included (see box.io_ports).
            box.io_ports,
            self._domain_state.supply_sets,
            known_supply_names_for_owner(self._shared_state, box.path),
            box.path,
            initial=self._port_attribute_state.assignments,
            all_domains=self._domain_state.domains,
            parent=self,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.set_port_attributes(box, dialog.assignments, dialog.new_supply_sets, dialog.new_supply_ports, dialog.new_supply_port_states)

    def set_port_attributes(
        self,
        box: InstanceBoxItem,
        assignments: dict[str, Optional[str]],
        new_supply_sets: dict[str, SupplySetPlacement],
        new_supply_ports: Optional[dict[str, QColor]] = None,
        new_supply_port_states: Optional[dict[str, list[SupplyPortState]]] = None,
    ) -> None:
        """The directly-testable core of "Add/Edit Port Attribute" --
        bypasses the dialog (same reasoning as create_power_domain()'s
        own docstring). Any Supply Set created via the dialog's own
        "Create Supply Set..." flow is committed to `_domain_state`
        first (same "always-persisted-before-referenced" ordering
        create_power_domain() already establishes), and any brand-new
        supply-port *name* introduced along the way is materialized on
        `box` (always the design's own top-level block -- see
        _prompt_add_or_edit_port_attributes()) via the same
        _add_missing_supply_ports() every other Supply-Set-composing
        dialog already funnels through. `new_supply_port_states` (bug
        fix, explicit report -- see _add_missing_supply_ports()'s own
        docstring) carries each new name's own Power States alongside
        its colour, the same way."""
        supply_before = self._snapshot_state()
        domain_before = self._snapshot_domain_state()
        port_attribute_before = self._snapshot_port_attribute_state()
        self._domain_state.supply_sets.update(new_supply_sets)
        self._add_missing_supply_ports(box, new_supply_ports or {}, new_supply_port_states)
        self._port_attribute_state.assignments.update(assignments)
        self._commit_port_attribute_undo_step("Set Port Attributes", supply_before, domain_before, port_attribute_before)

    def _existing_domain_names(self) -> dict[str, str]:
        return {domain.id: domain.name for domain in self._domain_state.domains.values()}

    def _prompt_create_power_domain(self, box: InstanceBoxItem) -> None:
        dialog = CreatePowerDomainDialog(
            supply_sets_for_owner(self._domain_state, box.path),
            known_supply_names_globally(self._shared_state),  # Bug 9: whole-design pool, not hierarchy-scoped -- see that function's own docstring
            self._existing_domain_names(),
            box.path,
            parent=self,
            all_domains=self._domain_state.domains,
            all_supply_sets=self._domain_state.supply_sets,
            supply_port_icons=self._supply_port_icons,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.create_power_domain(
                box,
                dialog.name,
                dialog.color,
                dialog.transparency,
                dialog.primary_supply_set_id,
                dialog.secondary_supply_set_ids,
                dialog.new_supply_sets,
                dialog.new_supply_ports,
                new_supply_port_states=dialog.new_supply_port_states,
            )

    def _prompt_edit_power_domain(self, box: InstanceBoxItem) -> None:
        domain = find_domain_for_owner(self._domain_state.domains, box.path)
        if domain is None:
            return
        dialog = CreatePowerDomainDialog(
            supply_sets_for_owner(self._domain_state, box.path, exclude_domain_id=domain.id),
            known_supply_names_globally(self._shared_state),  # Bug 9: whole-design pool, not hierarchy-scoped -- see that function's own docstring
            self._existing_domain_names(),
            box.path,
            editing_domain_id=domain.id,
            initial=domain,
            parent=self,
            all_domains=self._domain_state.domains,
            all_supply_sets=self._domain_state.supply_sets,
            supply_port_icons=self._supply_port_icons,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.edit_power_domain(
                domain.id,
                dialog.name,
                dialog.color,
                dialog.transparency,
                dialog.primary_supply_set_id,
                dialog.secondary_supply_set_ids,
                dialog.new_supply_sets,
                dialog.new_supply_ports,
                dialog.new_supply_port_states,
            )

    def _add_missing_supply_ports(
        self, box: InstanceBoxItem, new_supply_ports: dict[str, QColor], new_supply_port_states: Optional[dict[str, list[SupplyPortState]]] = None
    ) -> None:
        """"Add missing supply ports to the hierarchy" (explicit
        request): any supply-port name introduced fresh while composing
        a Supply Set for this domain (see CreateSupplySetDialog's own
        "Add New..."/colour-prompt flow) that isn't already anchored to
        `box` gets a real SupplyPortPlacement here, spread along the top
        edge the same even-spacing way build_supply_port_overlay() seeds
        a macro's own PG pins. Names that already have a placement on
        this exact box (e.g. reused across two Supply Sets in the same
        session) are skipped -- already there, nothing missing.

        Explicit request (Internal Supply): the kind materialized here
        follows `box`'s own hierarchy level -- a Supply Set composed on
        the design's true top-level block still creates plain Supply
        Ports (unchanged, existing behaviour), but one composed on an
        inner block now creates Internal Supply entries instead. `box`
        is always the domain's/strategy's own owner box at every one of
        this method's call sites (Create/Edit Power Domain, Create/Edit
        Retention), so branching on it here covers all of them
        uniformly with a single change.

        Bug fix (recurring "block doesn't make room for a new element"
        regression, this time traced to its actual root cause via a
        real trace rather than another guess): unlike add_supply_port()/
        add_internal_supply(), which each call self._reflow_layout()
        themselves right after creating their own single icon, this
        method never called it at all -- every port materialized here
        (Create/Edit Power Domain's own Supply-Set-composition flow,
        Edit Supply Set, Add/Edit Retention) silently skipped "make
        room" entirely on DIRECT creation. Confirmed empirically: a
        second, nested Power Domain creation (top-level Supply Set,
        then an inner block's own Supply Set) left the inner block's
        own rect() completely untouched, while Ctrl+Z/Ctrl+Y (a full
        _rebuild(), which always reflows fresh) produced the correct,
        larger rect -- the exact "undo+redo fixes it, direct creation
        doesn't" pattern already reported multiple times. Reflowing
        here, once per call (not per caller), is the same "one shared
        entry point, not parallel layout mechanisms" discipline
        _reflow_layout() itself is already built on.

        Bug fix (explicit report): `new_supply_port_states` (a brand-new,
        OPTIONAL parameter -- every pre-existing caller/test that only
        ever passed `new_supply_ports` keeps working unchanged) carries
        whatever Power States were entered in the "Add New..." dialog
        alongside each name -- previously dropped entirely, regardless
        of entry point, since `new_supply_ports` itself only ever
        carried name->colour (see _SupplyNameCombo's own docstring for
        where in the chain this was actually lost). Only applied for a
        plain Supply Port (`not is_internal`) -- an Internal Supply's
        own `states` field is always meant to stay empty (its effective
        states are derived, never user-authored -- see
        SupplyPortPlacement.states' own docstring), so a states list
        threaded down for one is simply never consulted here."""
        is_internal = not self._is_top_level_box(box)
        existing_names = {p.name for p in self._shared_state.supply_port_placements.values() if p.owner_path == box.path}
        missing = [(name, color) for name, color in new_supply_ports.items() if name not in existing_names]
        count = len(missing)
        # Bug fix (explicit report): offset by whatever ALREADY occupies
        # "top" on this box -- the old i/(count+1) formula only ever
        # spread THIS batch among itself, blind to a pre-existing
        # top-edge occupant (e.g. from an earlier, separate action on
        # the same box), landing a new port on the exact same t and
        # overlapping its label -- see _edge_occupant_count()'s own
        # docstring.
        existing_count = self._edge_occupant_count(box, "top")
        for i, (name, color) in enumerate(missing):
            states = list(new_supply_port_states.get(name, [])) if new_supply_port_states and not is_internal else []
            placement = SupplyPortPlacement(
                id=uuid4().hex,
                owner_path=box.path,
                name=name,
                color=QColor(color),
                edge="top",
                t=(existing_count + i + 1) / (existing_count + count + 1),
                is_internal_supply=is_internal,
                states=states,
            )
            self._shared_state.supply_port_placements[placement.id] = placement
            self._supply_port_icons[placement.id] = SupplyPortIconItem(box, placement)
        if missing:
            self._reflow_layout()
            self._respace_edge_occupants(box, "top")

    def create_power_domain(
        self,
        box: InstanceBoxItem,
        name: str,
        color: QColor,
        transparency: float,
        primary_supply_set_id: Optional[str],
        secondary_supply_set_ids: list[str],
        new_supply_sets: dict[str, SupplySetPlacement],
        new_supply_ports: Optional[dict[str, QColor]] = None,
        additional_boxes: Optional[list[InstanceBoxItem]] = None,
        new_supply_port_states: Optional[dict[str, list[SupplyPortState]]] = None,
    ) -> PowerDomainPlacement:
        """The directly-testable core of "Create Power Domain" -- bypasses
        the dialog (same reasoning as add_supply_port()'s own docstring).
        `new_supply_sets` (any Supply Set created via the dialog's own
        nested "Create New Supply Set..." flow, not yet persisted
        anywhere) is committed here first, so the domain's own primary/
        secondary references always resolve against already-persisted
        Supply Sets. `new_supply_ports` (name -> chosen colour, also from
        the dialog) drives _add_missing_supply_ports() -- touches
        `_shared_state` too, which is why this commits via
        _commit_combined_undo_step() rather than the domain-only helper:
        a single Ctrl+Z must undo both the domain and any supply ports
        it just materialized together. Not offered at all for a macro
        box -- see _extra_context_menu_actions()'s own disabled-action
        handling -- recolouring never touches a macro's fixed grey
        anyway (see InstanceBoxItem.set_domain_color()), so this doesn't
        need its own extra guard against being called on one directly.

        `additional_boxes` (explicit request, multi-hierarchy Power
        Domain; new, OPTIONAL keyword-only parameter -- every pre-
        existing single-hierarchy caller/test that only ever passes
        `box` alone keeps working completely unchanged) lists any
        FURTHER member hierarchy blocks beyond `box` itself -- the
        resulting domain's own `owner_paths` covers `box` plus every one
        of these, and `new_supply_ports` is replicated onto EVERY member
        box, not just `box` (explicit request: "each member hierarchy
        gets its own instance of the corresponding Supply Port/Internal
        Supply").

        `new_supply_port_states` (bug fix, explicit report -- see
        _add_missing_supply_ports()'s own docstring): the ONLY way to
        introduce a brand-new Supply Port from within this whole flow is
        via a Supply Set's own "Add New...", so this is the sole path
        that ever needed a fix for Power States entered there being
        silently dropped -- carries each new name's own states
        alongside its colour, replicated onto every member box the same
        way `new_supply_ports` already is."""
        member_boxes = [box, *(additional_boxes or [])]
        supply_before = self._snapshot_state()
        domain_before = self._snapshot_domain_state()
        self._domain_state.supply_sets.update(new_supply_sets)
        placement = PowerDomainPlacement(
            id=uuid4().hex,
            owner_path=box.path,
            owner_paths=[b.path for b in member_boxes],
            name=name,
            color=QColor(color),
            transparency=transparency,
            primary_supply_set=primary_supply_set_id,
            secondary_supply_sets=list(secondary_supply_set_ids),
        )
        self._domain_state.domains[placement.id] = placement
        for member_box in member_boxes:
            self._add_missing_supply_ports(member_box, new_supply_ports or {}, new_supply_port_states)
        self._reclassify_ground_supply_edges()  # Bug 2: new_supply_sets above may have just set a Ground role for the first time
        self._auto_link_domains_supply_sets(placement)  # Bug 9: a role may reference a name that only exists on a different, unrelated hierarchy
        # Bug fix (found via the SAME "direct creation wrong, undo+redo
        # fixes it" diagnostic signature already seen elsewhere in this
        # project): _auto_link_domains_supply_sets() above constructs its
        # OWN new SupplyPortPlacement(s) directly (edge="top" hardcoded,
        # same "create at top, reclassify after" pattern
        # _add_missing_supply_ports() already follows) -- AFTER the
        # reclassify call above already ran, so an auto-linked Ground
        # Internal Supply was never reclassified until the next full
        # _rebuild() (build_supply_port_overlay()'s own, separate
        # one-time reclassification only runs there). Re-running this
        # (cheap, idempotent -- a no-op for every placement already at
        # the right edge) catches whatever the auto-link step just
        # created.
        self._reclassify_ground_supply_edges()
        self._refresh_domain_derived_colors()
        # New Supply Set display box feature: unconditional (unlike
        # _add_missing_supply_ports()'s own conditional reflow above) --
        # a brand-new domain always needs room for its own info box,
        # regardless of whether any new supply PORT name was introduced.
        self._reflow_layout()
        self._commit_combined_undo_step("Create Power Domain", supply_before, domain_before)
        return placement

    def _refresh_domain_derived_colors(self) -> None:
        """Power Domain's own color/transparency are no longer user-
        chosen (explicit request, superseding the earlier manual
        colour-picker design) -- LIVE-derived from each domain's own
        Primary Supply Set's Power rail color at a FIXED transparency
        (resolve_domain_color()/DOMAIN_FIXED_TRANSPARENCY, same live-
        color-binding pattern already established for Isolation/Power
        Switch/Retention -- see power_domain_overlay.py's own
        resolve_domain_color() docstring for the full "why"/"how").

        Since PowerDomainPlacement is a plain, Qt-independent dataclass
        (no live Qt property mechanism the way a real SupplyPortIconItem
        has), "live" here means recomputed explicitly on every trigger
        that could change the result -- called from create_power_domain()/
        edit_power_domain() (a domain's own Primary Supply Set choice)
        AND from edit_supply_port()/edit_supply_set() (a Supply Port's own
        colour, or a Supply Set's own Power rail reference, changing
        underneath an already-created domain). Re-applies
        apply_domain_coloring()/build_domain_info_box_overlay() (diagram fill
        + name label -- the other two of the three rendering locations the
        request calls out, the third being the Power View tree, which
        re-derives colour fresh on every rebuild already -- see
        power_view_tree.py's own module docstring) and rebuilds Level
        Shifter icons (their own colour cascades from their owning
        domain's colour -- same pre-existing dependency edit_power_domain()
        already had to account for) so every downstream consumer picks up
        the refreshed colour in the same pass."""
        for domain in self._domain_state.domains.values():
            domain.color = resolve_domain_color(domain.primary_supply_set, self._domain_state.supply_sets, self._supply_port_icons, fallback=domain.color)
            domain.transparency = DOMAIN_FIXED_TRANSPARENCY
        apply_domain_coloring(self._boxes, self._domain_state.domains)
        self._clear_domain_info_boxes()
        self._domain_info_boxes = build_domain_info_box_overlay(self._boxes, self._domain_state.domains, self._domain_state.supply_sets, self._supply_port_icons)
        self._clear_level_shifter_icons()
        inside_slots, outside_slots = self._isolation_badge_slot_counts_per_box()
        self._level_shifter_icons = build_level_shifter_overlay(
            self._boxes,
            self._level_shifter_state.strategies,
            self._domain_state.domains,
            self._supply_port_icons,
            initial_inside_slots=inside_slots,
            initial_outside_slots=outside_slots,
        )
        self._apply_group_centering()  # Bug 3: re-center the WHOLE Isolation+Level Shifter group now that the true combined total per block is known

    def edit_power_domain(
        self,
        domain_id: str,
        name: str,
        color: QColor,
        transparency: float,
        primary_supply_set_id: Optional[str],
        secondary_supply_set_ids: list[str],
        new_supply_sets: dict[str, SupplySetPlacement],
        new_supply_ports: Optional[dict[str, QColor]] = None,
        new_supply_port_states: Optional[dict[str, list[SupplyPortState]]] = None,
    ) -> PowerDomainPlacement:
        """The directly-testable core of "Edit Power Domain" -- same
        bypass-the-dialog reasoning as create_power_domain(), and shares
        its own "supply ports + domain undo together" story. `owner_path`
        (and therefore the domain's `id`) never changes here -- editing
        only ever touches the fields the dialog actually exposes.

        CHANGE 2 (explicit request, ancestor Supply Set sharing): a
        Supply Set removed from THIS domain's own list may still be
        alive via another domain's own reference to the exact SAME
        SupplySetPlacement id (not a clone) -- `_supply_set_ids_referenced_elsewhere()`
        excludes those from what `_cascade_remove_orphaned_supply_ports()`
        treats as newly-orphaned, so the underlying, still-needed
        Supply Port(s) aren't deleted out from under the OTHER domain.
        `_prune_orphaned_supply_sets()` below (unchanged) already does
        its own equivalent, independent check for the Supply Set
        RECORD itself."""
        supply_before = self._snapshot_state()
        domain_before = self._snapshot_domain_state()
        self._domain_state.supply_sets.update(new_supply_sets)
        existing = self._domain_state.domains[domain_id]
        old_supply_set_ids = {sid for sid in (existing.primary_supply_set, *existing.secondary_supply_sets) if sid}
        new_supply_set_ids = {sid for sid in (primary_supply_set_id, *secondary_supply_set_ids) if sid}
        removed_supply_set_ids = old_supply_set_ids - new_supply_set_ids
        survives_elsewhere = self._supply_set_ids_referenced_elsewhere(domain_id, removed_supply_set_ids)
        self._cascade_remove_orphaned_supply_ports(existing, removed_supply_set_ids - survives_elsewhere, new_supply_set_ids)
        placement = dataclasses_replace(
            existing,
            name=name,
            color=QColor(color),
            transparency=transparency,
            primary_supply_set=primary_supply_set_id,
            secondary_supply_sets=list(secondary_supply_set_ids),
        )
        self._domain_state.domains[domain_id] = placement
        # Fix 4B (explicit request): a Supply Set removed from this
        # domain's own list must have its underlying record fully
        # deleted, not left behind as an orphaned row nobody can reach
        # from the Properties table anymore -- UNLESS it's still
        # referenced by some OTHER Power Domain (checked AFTER
        # `placement` above already reflects this domain's own new,
        # smaller list, so a set that's merely moved from Primary to
        # Secondary -- still referenced by this SAME domain -- is
        # correctly never treated as orphaned).
        self._prune_orphaned_supply_sets(removed_supply_set_ids)
        # Replicated across every one of the domain's own EXISTING member
        # hierarchies (explicit request) -- editing never changes which
        # hierarchies belong to the domain, only Create does (see
        # create_power_domain()'s own `additional_boxes`).
        for owner_path in placement.owner_paths:
            member_box = self._boxes.get(owner_path)
            if member_box is not None:
                self._add_missing_supply_ports(member_box, new_supply_ports or {}, new_supply_port_states)
        self._reclassify_ground_supply_edges()  # Bug 2: new_supply_sets above may have just set a Ground role for the first time
        self._auto_link_domains_supply_sets(placement)  # Bug 9: a role may reference a name that only exists on a different, unrelated hierarchy
        self._reclassify_ground_supply_edges()  # Bug fix: re-run -- the auto-link step above may have just created a fresh Ground placement of its own, see create_power_domain()'s own identical comment
        # Level Shifter's own badge color follows its owning Power
        # Domain's color directly (see level_shifter_overlay.py's own
        # module docstring on why -- it has no Supply Set of its own to
        # follow the way Isolation/Retention do) -- handled by
        # _refresh_domain_derived_colors() below alongside the domain's
        # own color/label/box-fill refresh.
        self._refresh_domain_derived_colors()
        # New Supply Set display box feature: unconditional, same
        # reasoning as create_power_domain()'s own -- the Primary/
        # Secondary selection (and therefore the info box's own line
        # count) may have just changed with no new supply PORT name
        # involved at all.
        self._reflow_layout()
        self._commit_combined_undo_step("Edit Power Domain", supply_before, domain_before)
        # "After the removal completes" (explicit request) -- fires once
        # the edit (including the cascade above) has already been
        # committed, not before.
        self._warn_about_retention_strategies_affected_by_removed_supply_sets(domain_id, removed_supply_set_ids)
        return placement

    def _cascade_remove_orphaned_supply_ports(
        self, domain: PowerDomainPlacement, removed_supply_set_ids: set[str], remaining_supply_set_ids: set[str]
    ) -> None:
        """Explicit request: when a Supply Set is removed from a Power
        Domain (via Edit Power Domain), any Supply Port it referenced is
        removed too -- UNLESS that same port name is still referenced by
        another Supply Set GROUP still in this domain (checked against
        `remaining_supply_set_ids`, i.e. the domain's own primary +
        secondary AFTER this edit). Scoped to Supply Ports/Supply Sets
        sharing this domain's own `owner_path` -- the same scope
        known_supply_names_for_owner() already draws a Supply Set's own
        field values from at compose time (see
        create_power_domain_dialog.py's own module docstring), so this
        can never reach into an unrelated hierarchy's own same-named
        port. Only removes the Supply PORTS a removed Supply Set
        referenced -- the Supply Set placement's own record is a
        separate concern, handled by `_prune_orphaned_supply_sets()`
        (Fix 4B, called separately by `edit_power_domain()` right after
        this method -- superseding an earlier version of this docstring
        that described the Supply Set object as always left alone;
        that's still true for a Supply Set still referenced by some
        OTHER Power Domain, just no longer true unconditionally)."""
        if not removed_supply_set_ids:
            return
        removed_names = {
            n
            for set_id in removed_supply_set_ids
            for n in self._supply_set_field_values(set_id)
        }
        if not removed_names:
            return
        still_referenced = {
            n
            for set_id in remaining_supply_set_ids
            for n in self._supply_set_field_values(set_id)
        }
        orphaned_names = removed_names - still_referenced
        if not orphaned_names:
            return
        for port_id, port_placement in list(self._shared_state.supply_port_placements.items()):
            if port_placement.owner_path in domain.owner_paths and port_placement.name in orphaned_names:
                self._remove_supply_port_placement(port_id)

    def _supply_set_ids_referenced_elsewhere(self, exclude_domain_id: str, candidate_ids: set[str]) -> set[str]:
        """CHANGE 2 (explicit request, ancestor Supply Set sharing): which
        of `candidate_ids` are still referenced (as Primary or any
        Secondary) by some Power Domain OTHER than `exclude_domain_id`
        -- checked by id, never by `self._domain_state.domains`'s own
        current membership/mutation order, so it stays correct
        regardless of whether the caller has already updated/popped
        `exclude_domain_id`'s own dict entry at call time (both
        edit_power_domain() and _remove_domain_cascade() need this
        BEFORE their own dict mutation completes).

        Used to keep a SHARED Supply Set's own underlying Supply
        Port(s) alive when it's removed from/deleted along with only
        ONE of the (2+) domains that reference it -- see
        edit_power_domain()'s and _remove_domain_cascade()'s own call
        sites for the full "why": without this, removing a shared
        Supply Set's association from domain A alone would treat its
        Power/Ground/N-Well/P-Well names as orphaned and delete the
        real Supply Port(s) domain B's own (still-alive) reference to
        the SAME Supply Set object still depends on."""
        if not candidate_ids:
            return set()
        referenced: set[str] = set()
        for other in self._domain_state.domains.values():
            if other.id == exclude_domain_id:
                continue
            referenced.update(sid for sid in (other.primary_supply_set, *other.secondary_supply_sets) if sid)
        return referenced & candidate_ids

    def _prune_orphaned_supply_sets(self, removed_supply_set_ids: set[str]) -> None:
        """Fix 4B (explicit request): "if a Supply Set is created and
        then later removed from its Power Domain's list... the Supply
        Set's underlying record must be fully deleted -- not just
        hidden... while still existing as an orphaned record." Deletes
        each of `removed_supply_set_ids` outright, UNLESS it's still
        referenced (as Primary or any Secondary) by some Power Domain
        anywhere in the design -- checked against `self._domain_state.domains`
        as it stands AT CALL TIME, so the caller must update the
        editing domain's own placement first (see edit_power_domain()'s
        own call site)."""
        if not removed_supply_set_ids:
            return
        still_referenced = {
            sid for domain in self._domain_state.domains.values() for sid in (domain.primary_supply_set, *domain.secondary_supply_sets) if sid
        }
        for set_id in removed_supply_set_ids:
            if set_id not in still_referenced:
                self._domain_state.supply_sets.pop(set_id, None)

    def _supply_set_field_values(self, supply_set_id: str) -> set[str]:
        supply_set = self._domain_state.supply_sets.get(supply_set_id)
        if supply_set is None:
            return set()
        return {n for n in (supply_set.power, supply_set.ground, supply_set.n_well, supply_set.p_well) if n}

    _RETENTION_REVIEW_TITLE = "Review Retention Strategies"

    def _warn_about_retention_strategies_affected_by_removed_supply_sets(self, domain_id: str, removed_supply_set_ids: set[str]) -> None:
        """Explicit request: a WARNING/review prompt, not an automatic
        edit -- every affected Retention Strategy is left exactly as it
        was; this only tells the user which ones may now need a look,
        since the Supply Set they reference (Primary or Retention) was
        just removed from this Power Domain. Uses message_box.warning()
        -- the same existing convention every other "something needs
        your attention" message in this diagram already uses (see e.g.
        _MACRO_SUPPLY_PORT_ERROR/_RETENTION_NO_DOMAIN_ERROR), rather than
        introducing a new dialog type for this one case."""
        if not removed_supply_set_ids:
            return
        affected = [
            s
            for s in self._retention_state.strategies.values()
            if s.power_domain_id == domain_id and (s.primary_supply_set_id in removed_supply_set_ids or s.retention_supply_set_id in removed_supply_set_ids)
        ]
        if not affected:
            return
        names = ", ".join(sorted(s.name for s in affected))
        message_box.warning(
            self,
            self._RETENTION_REVIEW_TITLE,
            f"The following Retention Strategies reference a Supply Set that was just removed from this Power Domain and may need review: {names}",
        )

    def _remove_domain_cascade(self, domain: PowerDomainPlacement) -> None:
        """Shared cascade core for fully removing ONE Power Domain --
        extracted (explicit request: "reuse the existing cascade-
        deletion logic already built for these relationships rather
        than reimplementing it") so both the standalone "Remove Power
        Domain" action (remove_power_domain(), below) and the Supply-
        Port-deletion cascade (delete_supply_port_with_cascade()) share
        ONE implementation of "what does it mean for this domain to be
        gone." Every Supply Set the domain referenced (primary +
        secondary) is deleted outright -- not just unlinked, unlike Edit
        Power Domain's own "Supply Set removed from the domain" cascade
        (see _cascade_remove_orphaned_supply_ports()'s own docstring:
        that one only ever unlinks, since the domain itself still exists
        afterward) -- and any Supply Port only those Supply Sets
        referenced is removed too, reusing that exact same cascade
        helper with `remaining_supply_set_ids` passed as empty (every
        Supply Set in this domain is being removed, so nothing "remains"
        to keep a port alive). Any Retention Strategy tied to this
        domain (`power_domain_id`) is deleted outright as well --
        confirmed via explicit follow-up: the whole domain disappearing
        makes it genuinely invalid, not merely "worth reviewing" the way
        Edit Power Domain's own partial removal is (see
        _warn_about_retention_strategies_affected_by_removed_supply_sets(),
        which stays a warning-only path for THAT case, untouched here).
        Any Power Switch tied to this domain (`power_domain_id`) is
        removed too (explicit request, new -- a genuine gap in this
        cascade's own earlier version, which never touched Power
        Switches at all), reusing _remove_power_switch_placement() the
        exact same way "Delete Power Switch" and delete_supply_port()'s
        own pre-existing directly-attached-switch cascade already do.
        Any Isolation Cell tied to this domain (`power_domain_id`) is
        removed too, same reasoning -- an Isolation can only ever exist
        on an already-created Power Domain's own boundary (see
        _validate_isolation_target()), so the domain disappearing makes
        it exactly as invalid as a Retention Strategy/Power Switch in
        the same situation. Any Level Shifter tied to this domain is
        removed too, same reasoning again (it follows the Isolation
        model -- see level_shifter_overlay.py's own module docstring).
        Mutates state in place; callers snapshot
        before/after and commit their own undo step -- kept OUT of this
        method since a caller may need to combine it with further
        mutations (e.g. deleting the supply port itself) into one atomic
        Ctrl+Z.

        CHANGE 2 (explicit request, ancestor Supply Set sharing): a
        Supply Set this domain references may now be SHARED with an
        ancestor/descendant domain's own Primary/Secondary (the exact
        same SupplySetPlacement id, not a clone -- see
        create_power_domain_dialog.py's own module docstring). Removing
        THIS domain must not delete a shared Supply Set -- or the real
        Supply Port(s) its own Power/Ground/N-Well/P-Well names still
        need -- out from under the OTHER domain still referencing it.
        `_supply_set_ids_referenced_elsewhere()` excludes `domain` by
        id (not by dict-mutation timing), so popping `domain` from
        `self._domain_state.domains` first (moved up from its old
        position at the very end of this method) isn't strictly load-
        bearing for that check -- but is still done first regardless,
        so `_prune_orphaned_supply_sets()`'s own separate, identical-
        shaped scan (which DOES key off current dict membership) is
        correct too."""
        domain_supply_set_ids = {sid for sid in (domain.primary_supply_set, *domain.secondary_supply_sets) if sid}
        self._domain_state.domains.pop(domain.id, None)
        survives_elsewhere = self._supply_set_ids_referenced_elsewhere(domain.id, domain_supply_set_ids)
        self._cascade_remove_orphaned_supply_ports(domain, domain_supply_set_ids - survives_elsewhere, set())
        self._prune_orphaned_supply_sets(domain_supply_set_ids)

        affected_strategy_ids = [s.id for s in self._retention_state.strategies.values() if s.power_domain_id == domain.id]
        if affected_strategy_ids:
            for strategy_id in affected_strategy_ids:
                self._retention_state.strategies.pop(strategy_id, None)
            self._clear_retention_icons()
            self._retention_icons = build_retention_overlay(self._boxes, self._retention_state.strategies, self._domain_state.supply_sets, self._supply_port_icons)

        affected_switch_ids = [sw.id for sw in self._power_switch_state.switches.values() if sw.power_domain_id == domain.id]
        for switch_id in affected_switch_ids:
            self._remove_power_switch_placement(switch_id)

        affected_isolation_ids = [s.id for s in self._isolation_state.strategies.values() if s.power_domain_id == domain.id]
        if affected_isolation_ids:
            for strategy_id in affected_isolation_ids:
                self._isolation_state.strategies.pop(strategy_id, None)
            self._clear_isolation_icons()
            self._isolation_icons = build_isolation_overlay(
                self._boxes, self._isolation_state.strategies, self._domain_state.supply_sets, self._supply_port_icons,
            domains=self._domain_state.domains,
            )

        affected_level_shifter_ids = [s.id for s in self._level_shifter_state.strategies.values() if s.power_domain_id == domain.id]
        for strategy_id in affected_level_shifter_ids:
            self._level_shifter_state.strategies.pop(strategy_id, None)
        # Bug fix (item 2, explicit request): rebuilt UNCONDITIONALLY,
        # not just `if affected_level_shifter_ids` -- Isolation's own
        # badge count on a box may have just changed above (a domain
        # removed elsewhere on the SAME box) even when no Level Shifter
        # of THIS domain was affected, and Level Shifter's own stacking
        # must stay seeded from Isolation's current (not stale) count.
        self._clear_level_shifter_icons()
        inside_slots, outside_slots = self._isolation_badge_slot_counts_per_box()
        self._level_shifter_icons = build_level_shifter_overlay(
            self._boxes,
            self._level_shifter_state.strategies,
            self._domain_state.domains,
            self._supply_port_icons,
            initial_inside_slots=inside_slots,
            initial_outside_slots=outside_slots,
        )
        self._apply_group_centering()  # Bug 3: re-center the WHOLE Isolation+Level Shifter group now that the true combined total per block is known

        # `domain` was already popped from self._domain_state.domains
        # up top (see this method's own CHANGE 2 docstring paragraph).
        apply_domain_coloring(self._boxes, self._domain_state.domains)
        self._clear_domain_info_boxes()
        self._domain_info_boxes = build_domain_info_box_overlay(self._boxes, self._domain_state.domains, self._domain_state.supply_sets, self._supply_port_icons)

    def remove_power_domain(self, box: InstanceBoxItem) -> None:
        """The directly-testable core of "Remove Power Domain" -- a
        no-op if `box` doesn't own one (defensive; the menu action is
        disabled in that case, see _extra_context_menu_actions()).
        Explicit request: cascades fully now (superseding an earlier
        version of this method that left the domain's own Supply
        Sets/Ports and any dependent Retention Strategy/Power Switch
        alone) -- see _remove_domain_cascade()'s own docstring for the
        full reasoning, now shared with delete_supply_port_with_cascade().
        All of it -- Supply Sets/Ports, the domain itself, any deleted
        Retention Strategies, any deleted Power Switches, and any
        deleted Isolation Cells -- lands in ONE combined undo step
        (_commit_remove_domain_undo_step()), not one step per entity
        kind. The domain's own merged info box (build_domain_info_box_overlay())
        is removed for free by the unconditional _clear_domain_info_boxes()
        + rebuild inside _remove_domain_cascade(), same as
        apply_domain_coloring() already unconditionally resets
        colouring every time."""
        domain = find_domain_for_owner(self._domain_state.domains, box.path)
        if domain is None:
            return
        supply_before = self._snapshot_state()
        domain_before = self._snapshot_domain_state()
        retention_before = self._snapshot_retention_state()
        power_switch_before = self._snapshot_power_switch_state()
        isolation_before = self._snapshot_isolation_state()
        level_shifter_before = self._snapshot_level_shifter_state()

        self._remove_domain_cascade(domain)

        # Audit fix (category 08): `_extra_interior_bottom()` reserves
        # per-domain height for the merged info box, so removing a
        # domain frees that room -- but nothing in this method or in
        # `_remove_domain_cascade()` reached `_reflow_layout()`, leaving
        # the block visibly oversized (measured: 477.9px retained where
        # a rebuild gives 408.0px) until something unrelated forced a
        # rebuild. The cascade can also strip a Ground role along with
        # the Supply Sets it removes, so the edge classifier runs too.
        self._reclassify_ground_supply_edges()
        self._reflow_layout()
        self._commit_remove_domain_undo_step(
            "Remove Power Domain", supply_before, domain_before, retention_before, power_switch_before, isolation_before, level_shifter_before
        )

    def _cascade_preview_for_domain_removal(
        self, domain: PowerDomainPlacement
    ) -> tuple[
        list[SupplySetPlacement], list[RetentionStrategyPlacement], list[PowerSwitchPlacement], list[IsolationStrategyPlacement], list[LevelShifterStrategyPlacement]
    ]:
        """Everything _remove_domain_cascade() would ACTUALLY remove for
        `domain`, computed WITHOUT mutating anything -- used to show a
        detailed confirmation before removal (explicit request, item 3:
        Power Domain removal previously deleted immediately with no
        confirmation at all -- this adds one, reusing the SAME "list
        every affected entity by name, grouped by kind" convention
        _prompt_delete_supply_port() already established, rather than
        inventing a different confirmation style). Mirrors
        _remove_domain_cascade()'s own traversal exactly (its own
        Supply Sets, Retention Strategies, Power Switches, and Isolation
        Cells, all keyed off `domain.id`) so this preview never drifts
        from what removal will really do."""
        domain_supply_set_ids = {sid for sid in (domain.primary_supply_set, *domain.secondary_supply_sets) if sid}
        affected_sets = [self._domain_state.supply_sets[sid] for sid in domain_supply_set_ids if sid in self._domain_state.supply_sets]
        affected_retention = [s for s in self._retention_state.strategies.values() if s.power_domain_id == domain.id]
        affected_switches = [sw for sw in self._power_switch_state.switches.values() if sw.power_domain_id == domain.id]
        affected_isolation = [s for s in self._isolation_state.strategies.values() if s.power_domain_id == domain.id]
        affected_level_shifter = [s for s in self._level_shifter_state.strategies.values() if s.power_domain_id == domain.id]
        return affected_sets, affected_retention, affected_switches, affected_isolation, affected_level_shifter

    @staticmethod
    def _cascade_impact_lines(
        header: str, affected_sets=(), affected_domains=(), affected_retention=(), affected_switches=(), affected_isolation=(), affected_level_shifter=()
    ) -> list[str]:
        """Shared "list every affected entity by name, grouped by kind"
        formatting -- the exact convention _prompt_delete_supply_port()
        already established, factored out here so Power Domain/Supply
        Set/Power Switch Cell removal all render an identical-looking
        confirmation instead of three subtly different ad hoc ones."""
        lines = [header, ""]
        if affected_sets:
            lines.append(f"Supply Sets: {', '.join(sorted(s.name for s in affected_sets))}")
        if affected_domains:
            lines.append(f"Power Domains: {', '.join(sorted(d.name for d in affected_domains))}")
        if affected_retention:
            lines.append(f"Retention Strategies: {', '.join(sorted(r.name for r in affected_retention))}")
        if affected_switches:
            lines.append(f"Power Switches: {', '.join(sorted(sw.name for sw in affected_switches))}")
        if affected_isolation:
            lines.append(f"Isolation Cells: {', '.join(sorted(s.name for s in affected_isolation))}")
        if affected_level_shifter:
            lines.append(f"Level Shifters: {', '.join(sorted(s.name for s in affected_level_shifter))}")
        return lines

    def _prompt_remove_power_domain(self, box: InstanceBoxItem) -> None:
        """The UI-facing entry point for "Remove Power Domain" -- explicit
        request, item 3: this used to remove immediately with no
        confirmation at all (unlike Supply Port's own cascade-aware
        prompt); now shows the SAME detailed Yes/No confirmation
        listing every affected entity, only when there IS cascade
        impact beyond the domain itself (a domain with no Supply Sets/
        Retention/Power Switches/Isolation attached still removes
        immediately, matching this diagram's own "no impact, no
        friction" convention)."""
        domain = find_domain_for_owner(self._domain_state.domains, box.path)
        if domain is None:
            return
        affected_sets, affected_retention, affected_switches, affected_isolation, affected_level_shifter = self._cascade_preview_for_domain_removal(domain)
        if not (affected_sets or affected_retention or affected_switches or affected_isolation or affected_level_shifter):
            self.remove_power_domain(box)
            return
        lines = self._cascade_impact_lines(
            f"Removing Power Domain '{domain.name}' will also remove:",
            affected_sets=affected_sets,
            affected_retention=affected_retention,
            affected_switches=affected_switches,
            affected_isolation=affected_isolation,
            affected_level_shifter=affected_level_shifter,
        )
        reply = message_box.question(
            self,
            "Remove Power Domain",
            "\n".join(lines),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.remove_power_domain(box)

    # --- Supply Set: Edit/Remove (explicit request, Properties table) -------
    # Genuinely NEW logic -- confirmed before writing any of this that no
    # standalone edit/delete for Supply Set existed anywhere in this
    # diagram: it was previously creatable only INLINE, via
    # CreateSupplySetDialog embedded in the Power Domain/Retention
    # dialogs' own "Add new Supply Set..." flow, with no Properties-
    # table-level entry point at all.

    def _prompt_edit_supply_set(self, supply_set_id: str) -> None:
        """IMPACT NOTIFICATION (explicit request, section 2 -- editing a
        Supply Set notifies about dependent Power Domains/Isolation/
        Retention/etc.): reuses _cascade_preview_for_supply_set_delete()
        directly -- exactly the same "which Power Domains reference
        this, and everything cascading from THOSE domains" trace the
        Remove path already computes, just shown as an informational
        message_box.warning() before opening the dialog instead of a
        Yes/No confirmation before deleting anything. Same "informational,
        not blocking" framing as Power Switch Cell's own edit
        notification -- the edit always proceeds regardless."""
        supply_set = self._domain_state.supply_sets.get(supply_set_id)
        if supply_set is None:
            return
        affected_domains, affected_retention, affected_switches, affected_isolation, affected_level_shifter = self._cascade_preview_for_supply_set_delete(supply_set_id)
        if affected_domains:
            lines = self._cascade_impact_lines(
                f"Editing Supply Set '{supply_set.name}' will affect:",
                affected_domains=affected_domains,
                affected_retention=affected_retention,
                affected_switches=affected_switches,
                affected_isolation=affected_isolation,
                affected_level_shifter=affected_level_shifter,
            )
            message_box.warning(self, "Edit Supply Set", "\n".join(lines))
        dialog = CreateSupplySetDialog(
            known_supply_names_globally(self._shared_state),  # Bug 9: whole-design pool, not hierarchy-scoped -- see that function's own docstring
            parent=self,
            editing_supply_set_id=supply_set_id,
            initial=supply_set,
            is_internal_supply="/" in supply_set.owner_path,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.edit_supply_set(
                supply_set_id, dialog.name, dialog.power, dialog.ground, dialog.n_well, dialog.p_well, dialog.new_port_colors, dialog.new_port_states
            )

    def edit_supply_set(
        self,
        supply_set_id: str,
        name: str,
        power: Optional[str],
        ground: Optional[str],
        n_well: Optional[str],
        p_well: Optional[str],
        new_supply_ports: Optional[dict[str, QColor]] = None,
        new_supply_port_states: Optional[dict[str, list[SupplyPortState]]] = None,
    ) -> SupplySetPlacement:
        """The directly-testable core of "Edit Supply Set" -- bypasses
        the dialog, same reasoning as every other "edit_X" core here.
        `new_supply_ports` (any brand-new supply name introduced via
        one of the dialog's own 4 "Add New..." combos) is materialized
        on EVERY one of this Supply Set's own owner_paths, same
        replication rule edit_power_domain()'s own new_supply_ports
        handling already establishes for a multi-hierarchy domain.
        `new_supply_port_states` (bug fix, explicit report -- see
        _add_missing_supply_ports()'s own docstring) carries each new
        name's own Power States alongside its colour, the same way."""
        supply_before = self._snapshot_state()
        domain_before = self._snapshot_domain_state()
        current = self._domain_state.supply_sets[supply_set_id]
        placement = dataclasses_replace(current, name=name, power=power, ground=ground, n_well=n_well, p_well=p_well)
        self._domain_state.supply_sets[supply_set_id] = placement
        for owner_path in current.owner_paths:
            owner_box = self._boxes.get(owner_path)
            if owner_box is not None:
                self._add_missing_supply_ports(owner_box, new_supply_ports or {}, new_supply_port_states)
        self._reclassify_ground_supply_edges()  # Bug 2: `ground` above may have just been set/repointed for the first time
        self._auto_link_cross_hierarchy_supply_sets(placement)  # Bug 9: a role may have just been re-pointed at a name that only exists on a different, unrelated hierarchy
        self._reclassify_ground_supply_edges()  # Bug fix: re-run -- the auto-link step above may have just created a fresh Ground placement of its own, see create_power_domain()'s own identical comment
        # A Power Domain's own color follows its Primary Supply Set's
        # Power rail -- refresh every domain's derived color so a Supply
        # Set edit (e.g. re-pointing `power` at a different port name)
        # propagates immediately.
        self._refresh_domain_derived_colors()
        # Bug fix: unconditional, same "must run AFTER reclassification,
        # not just after _add_missing_supply_ports()'s own conditional
        # one" precedent create_power_domain()/edit_power_domain() both
        # already establish for the Supply Set info box feature -- a
        # freshly-reclassified Ground placement changes which margin
        # dimension (_extra_interior_top() vs. the new
        # _extra_interior_bottom()) this box's own room requirement
        # should be computed from; without this, a box already resized
        # by _add_missing_supply_ports() BEFORE reclassification ran
        # stays stuck at its stale top-margin size (incorrectly pushing
        # its own children down) instead of the correct bottom-margin
        # one.
        self._reflow_layout()
        self._commit_combined_undo_step("Edit Supply Set", supply_before, domain_before)
        return placement

    def _cascade_preview_for_supply_set_delete(
        self, supply_set_id: str
    ) -> tuple[
        list[PowerDomainPlacement], list[RetentionStrategyPlacement], list[PowerSwitchPlacement], list[IsolationStrategyPlacement], list[LevelShifterStrategyPlacement]
    ]:
        """Every Power Domain that has `supply_set_id` as its own
        Primary OR Secondary is affected -- same "no different treatment
        for Primary vs. Secondary" rule _cascade_targets_for_supply_port_delete()
        already establishes for the analogous Supply-Port-triggered
        case, applied here directly from the Supply Set's own id.
        Retention/Power Switch/Isolation entries are exactly what
        _remove_domain_cascade() would remove for each affected domain
        (by `power_domain_id`), matching what removal will actually do,
        not a narrower direct-reference-only subset."""
        affected_domains = [
            d for d in self._domain_state.domains.values() if d.primary_supply_set == supply_set_id or supply_set_id in d.secondary_supply_sets
        ]
        affected_domain_ids = {d.id for d in affected_domains}
        affected_retention = [r for r in self._retention_state.strategies.values() if r.power_domain_id in affected_domain_ids]
        affected_switches = [sw for sw in self._power_switch_state.switches.values() if sw.power_domain_id in affected_domain_ids]
        affected_isolation = [s for s in self._isolation_state.strategies.values() if s.power_domain_id in affected_domain_ids]
        affected_level_shifter = [s for s in self._level_shifter_state.strategies.values() if s.power_domain_id in affected_domain_ids]
        return affected_domains, affected_retention, affected_switches, affected_isolation, affected_level_shifter

    def delete_supply_set_with_cascade(self, supply_set_id: str) -> None:
        """The directly-testable, cascade-aware core -- every affected
        Power Domain is removed via the exact same _remove_domain_cascade()
        every other cascade path here already reuses; the Supply Set
        itself is then popped directly too (defensive, same
        "no-op for anything the domain loop already removed" precedent
        delete_supply_port_with_cascade() already establishes for its
        own `affected_supply_set_ids` sweep -- covers the rare case of a
        Supply Set that isn't actually anyone's Primary/Secondary).

        CHANGE 2 (explicit request, ancestor Supply Set sharing): `id`
        might now be shared -- two or more affected domains all
        referencing the exact same SupplySetPlacement. Every affected
        domain is popped from `self._domain_state.domains` in one batch
        BEFORE any of their own `_remove_domain_cascade()` calls run --
        so each one's own `_supply_set_ids_referenced_elsewhere()` check
        correctly sees every SIBLING domain in this SAME bulk deletion
        as already gone too, not as a stale mid-loop survivor (which
        would otherwise wrongly preserve this Supply Set's own
        underlying Supply Port(s) on whichever affected domain happens
        to be processed first, since that domain's own check would see
        a not-yet-processed sibling still "referencing" it)."""
        supply_before = self._snapshot_state()
        domain_before = self._snapshot_domain_state()
        retention_before = self._snapshot_retention_state()
        power_switch_before = self._snapshot_power_switch_state()
        isolation_before = self._snapshot_isolation_state()
        level_shifter_before = self._snapshot_level_shifter_state()

        affected_domains, _, _, _, _ = self._cascade_preview_for_supply_set_delete(supply_set_id)
        for domain in affected_domains:
            self._domain_state.domains.pop(domain.id, None)
        for domain in affected_domains:
            self._remove_domain_cascade(domain)
        self._domain_state.supply_sets.pop(supply_set_id, None)

        # Audit fix (category 04/08): deleting a Supply Set can strip a
        # supply's Ground role, and freeing its members' reserved room
        # can shrink their blocks -- neither happened here before, so a
        # direct delete left both the edge and the geometry stale until
        # some unrelated rebuild (classically: undo+redo "fixed" it).
        # `edit_supply_set()` already did both; this is its delete-side
        # counterpart.
        self._reclassify_ground_supply_edges()
        self._reflow_layout()
        self._commit_remove_domain_undo_step(
            "Remove Supply Set", supply_before, domain_before, retention_before, power_switch_before, isolation_before, level_shifter_before
        )

    def _prompt_remove_supply_set(self, supply_set_id: str) -> None:
        supply_set = self._domain_state.supply_sets.get(supply_set_id)
        if supply_set is None:
            return
        affected_domains, affected_retention, affected_switches, affected_isolation, affected_level_shifter = self._cascade_preview_for_supply_set_delete(supply_set_id)
        if not affected_domains:
            self.delete_supply_set_with_cascade(supply_set_id)
            return
        lines = self._cascade_impact_lines(
            f"Removing Supply Set '{supply_set.name}' will also remove:",
            affected_domains=affected_domains,
            affected_retention=affected_retention,
            affected_switches=affected_switches,
            affected_isolation=affected_isolation,
            affected_level_shifter=affected_level_shifter,
        )
        reply = message_box.question(
            self,
            "Remove Supply Set",
            "\n".join(lines),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.delete_supply_set_with_cascade(supply_set_id)

    # --- Retention: validation -------------------------------------------
    # Two independent rules (explicit request: each with its OWN distinct
    # error message, never collapsed into one generic one), enforced
    # before the Retention Strategy dialog is ever opened -- same
    # "checked before ever constructing the dialog" precedent
    # _prompt_add_power_supply() already established for the macro
    # Supply Port rule (a target that fails validation fails it for
    # every possible field value, so looping a dialog back open would be
    # pointless busywork). Unlike that rule, though, this one is never
    # baked into a disabled menu/hover state -- the action is always
    # offered, and clicking it explains precisely why it's rejected when
    # it is.

    _RETENTION_NO_DOMAIN_ERROR = "Retention requires the target block to be part of a Power Domain -- create or assign one first."
    _RETENTION_MACRO_ERROR = "Retention cannot be added on a macro (.lib) block -- a macro has no extractable sequential elements to retain."

    def _validate_retention_target(self, box: InstanceBoxItem) -> Optional[str]:
        """Returns the specific error message for the FIRST rule `box`
        fails (macro exclusion checked first -- a macro can never be
        retained regardless of domain membership, so that's the more
        fundamental rejection reason), or None if both rules pass. Pure/
        side-effect-free -- the caller (_prompt_add_retention()) decides
        what to actually do with the result (show a QMessageBox, or
        proceed to the dialog)."""
        if box.definition.is_black_box:
            return self._RETENTION_MACRO_ERROR
        if find_effective_domain_for_path(self._domain_state.domains, box.path) is None:
            return self._RETENTION_NO_DOMAIN_ERROR
        return None

    # --- Retention: placement (dialog-driven) -----------------------------

    def _existing_retention_names(self) -> dict[str, str]:
        return {strategy.id: strategy.name for strategy in self._retention_state.strategies.values()}

    def _prompt_add_retention(self, box: InstanceBoxItem) -> None:
        """Reuses this SAME dialog for both entry points (same "do NOT
        build a new one" precedent every other placement tool here
        follows) -- the hover+click Retention tool and the right-click
        "Add Retention" context-menu action both funnel through this one
        method, differing only in how `box` is chosen.

        Checked *before* ever constructing the dialog (same reasoning as
        _prompt_add_power_supply()'s own macro check): both validation
        rules reject every possible field value for a target that fails
        them, so there is nothing a dialog could offer to fix."""
        error = self._validate_retention_target(box)
        if error is not None:
            message_box.warning(self, "Add Retention", error)
            return
        domain = find_effective_domain_for_path(self._domain_state.domains, box.path)
        available_elements = collect_retention_elements_for_domain(self._boxes, domain, self._domain_state.domains)
        claimed = claimed_elements_by_other_strategies(self._retention_state.strategies, domain.id, exclude_strategy_id=None)
        dialog = RetentionStrategyDialog(
            domain,
            # The domain's OWN sets, resolved directly -- NOT
            # supply_sets_for_owners()'s "which sets may this domain
            # claim" offer. This dialog only ever looks up the domain's
            # own primary/secondary, and that offer legitimately omits
            # them once another domain shares one (ancestor Supply Set
            # sharing), which raised KeyError mid-construction and left
            # the dialog silently never opening.
            supply_sets_referenced_by_domain(self._domain_state, domain),
            known_supply_names_globally(self._shared_state),  # Bug 9: same whole-design pool as Create/Edit Power Domain's own inline Supply Set flow
            available_elements,
            claimed,
            self._existing_retention_names(),
            parent=self,
            # Explicit request/Fix 2: "Applies to" lists EVERY hierarchy
            # belonging to the (possibly multi-hierarchy) Power Domain
            # this strategy is being added to -- not just `box`, the
            # single block that happened to trigger the action.
            target_paths=domain.owner_paths,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            duplicate = find_duplicate_strategy(
                self._retention_state.strategies,
                domain.id,
                dialog.primary_supply_set_id,
                dialog.retention_supply_set_id,
                dialog.save_logic,
                dialog.restore_logic,
                dialog.elements,
            )
            if duplicate is not None:
                if not self._confirm_merge_duplicate_retention(duplicate):
                    return  # explicit request: "do NOT silently duplicate" -- declining the merge cancels rather than creating a redundant copy anyway
                return  # confirmed: merge into `duplicate` -- it already represents this exact state, nothing new to create; its own Name stays as-is (explicit answer)
            self.create_retention_strategy(
                box,
                domain,
                dialog.name,
                dialog.primary_supply_set_id,
                dialog.retention_supply_set_id,
                dialog.save_logic,
                dialog.restore_logic,
                dialog.elements,
                dialog.new_supply_sets,
                dialog.new_supply_ports,
                dialog.new_supply_port_states,
            )

    def _confirm_merge_duplicate_retention(self, duplicate: RetentionStrategyPlacement) -> bool:
        """"Completely matches an existing strategy... show a merge-
        confirmation dialog naming the existing matching strategy"
        (explicit request) -- Yes/No, defaulting to Yes (merging is the
        non-duplicating, generally-preferred outcome). Returns whether
        the user confirmed the merge."""
        reply = message_box.question(
            self,
            "Duplicate Retention Strategy",
            f"This matches an existing Retention Strategy, '{duplicate.name}', exactly -- same Power Domain, Primary/Retention Supply Set, Save/Restore Logic, and Elements. Merge into it instead of creating a duplicate?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        return reply == QMessageBox.StandardButton.Yes

    def create_retention_strategy(
        self,
        box: InstanceBoxItem,
        domain: PowerDomainPlacement,
        name: str,
        primary_supply_set_id: str,
        retention_supply_set_id: str,
        save_logic: str,
        restore_logic: str,
        elements: list[str],
        new_supply_sets: dict[str, SupplySetPlacement],
        new_supply_ports: Optional[dict[str, QColor]] = None,
        new_supply_port_states: Optional[dict[str, list[SupplyPortState]]] = None,
    ) -> RetentionStrategyPlacement:
        """The directly-testable core of "Add Retention" -- bypasses the
        dialog (same reasoning as add_supply_port()'s own docstring).
        Raises ValueError on a name collision (defense-in-depth: the
        dialog already checks this against `existing_names` before ever
        calling accept(), but this core stays correct on its own for any
        other/future caller that bypasses it, same two-layer precedent
        _MACRO_SUPPLY_PORT_ERROR's own check follows in add_supply_port()).
        `new_supply_sets`/`new_supply_ports` (from the dialog's own
        inline "Add new Supply Set..." flow) are anchored to the Power
        Domain's own owner box(es), not necessarily `box` itself --
        matching where the dialog's own supply-name picker pool was
        drawn from (known_supply_names_for_owners(..., domain.owner_paths)).
        For a multi-hierarchy domain, a newly-introduced supply name is
        replicated onto EVERY member hierarchy (same "each member gets
        its own instance" rule create_power_domain()'s own
        `additional_boxes` handling already establishes), not just one."""
        for existing in self._retention_state.strategies.values():
            if existing.name == name:
                raise ValueError(f"A Retention Strategy named '{name}' already exists.")
        supply_before = self._snapshot_state()
        retention_before = self._snapshot_retention_state()
        self._domain_state.supply_sets.update(new_supply_sets)
        for owner_path in domain.owner_paths:
            domain_box = self._boxes.get(owner_path)
            if domain_box is not None:
                self._add_missing_supply_ports(domain_box, new_supply_ports or {}, new_supply_port_states)
        placement = RetentionStrategyPlacement(
            id=uuid4().hex,
            name=name,
            power_domain_id=domain.id,
            primary_supply_set_id=primary_supply_set_id,
            retention_supply_set_id=retention_supply_set_id,
            save_logic=save_logic,
            restore_logic=restore_logic,
            elements=list(elements),
            owner_path=box.path,
        )
        self._retention_state.strategies[placement.id] = placement
        self._materialize_retention_badges(placement)
        self._commit_retention_undo_step("Add Retention", supply_before, retention_before)
        return placement

    def _materialize_retention_badges(self, strategy: RetentionStrategyPlacement) -> list[RetentionBadgeIconItem]:
        """Materializes `strategy`'s own badge icon(s) inline, against
        whichever blocks it currently spans -- same "don't wait for a
        rebuild" reasoning add_supply_port() already documents (a rebuild
        right after this call, before the caller gets a chance to use the
        result, would destroy whatever was just created). `offset_index`
        per block accounts for badges ALREADY materialized there by other
        strategies (counted across every list in `self._retention_icons`,
        not just this one strategy's own, since several strategies can
        share a block -- see build_retention_overlay()'s own docstring on
        stacking order). `color_source` resolved once here too (bug fix,
        new colour feature: this targeted materializer is a genuinely
        separate code path from build_retention_overlay() -- confirmed
        empirically that a freshly-created strategy's own icon rendered
        default grey instead of its Retention Supply Set's own Power
        colour until this was added, same "two independent code paths,
        both need updating" gap _clear_retention_icons() itself already
        warns about)."""
        icons = []
        color_source = resolve_retention_color_source(strategy, self._domain_state.supply_sets, self._supply_port_icons)
        top_edge_owners = top_edge_supply_port_owners(self._supply_port_icons)
        for owner_path in sorted(owner_paths_for_elements(strategy.elements)):
            box = self._boxes.get(owner_path)
            if box is None:
                continue
            existing_count = sum(1 for badges in self._retention_icons.values() for icon in badges if icon.owner_box is box)
            # Bug fix (explicit request, "sram example" repro): same
            # clearance-below-a-top-edge-supply-port-label fix
            # build_retention_overlay() already establishes for the
            # wholesale-rebuild path -- see that function's own comment.
            extra_top_clearance = INTERIOR_TOP_MARGIN_FOR_SUPPLY_PORT if owner_path in top_edge_owners else 0.0
            icons.append(RetentionBadgeIconItem(strategy.id, box, existing_count, color_source, extra_top_clearance))
        self._retention_icons[strategy.id] = icons
        return icons

    def _clear_retention_icons(self) -> None:
        """Removes every currently-materialized Retention badge from the
        scene -- used by edit/delete (see below), which both recompute
        `self._retention_icons` wholesale afterward rather than trying to
        incrementally patch it: an edit can change WHICH blocks a
        strategy spans at all (elements added/removed), so a full
        recompute via build_retention_overlay() is simpler and safer than
        hand-rolled add/move/remove bookkeeping -- same "don't guess,
        recompute from the source of truth" precedent _rebuild() itself
        already follows for every diagram-only placement here."""
        for badges in self._retention_icons.values():
            for icon in badges:
                if icon._reposition in icon.owner_box.on_resize_callbacks:
                    icon.owner_box.on_resize_callbacks.remove(icon._reposition)
                # New colour feature: same dangling-callback hazard as
                # on_move_callbacks/on_resize_callbacks above -- a stale
                # sync_color() left on the (possibly still-live) source
                # icon after this badge is discarded would crash on the
                # next recolor.
                if icon._color_source is not None and icon.sync_color in icon._color_source.on_recolor_callbacks:
                    icon._color_source.on_recolor_callbacks.remove(icon.sync_color)
                if icon.scene() is not None:
                    icon.scene().removeItem(icon)
        self._retention_icons = {}

    def _clear_isolation_icons(self) -> None:
        """Same "remove every currently-materialized item, recompute
        wholesale afterward" lifecycle as _clear_retention_icons() above,
        for Isolation Cells.

        Audit fix (category 10): now also detaches the badge's own
        `sync_color` from its colour source, which it never did -- an
        IsolationBadgeIconItem registers one in __init__ exactly like a
        RetentionBadgeIconItem does, but only Retention's own clear
        method removed it again. Measured before the fix: the source
        supply icon's `on_recolor_callbacks` went 0 -> 1 on create and
        stayed at 1 after delete (Retention, correctly, returned to 0).
        Because this method also runs on edit and on domain-colour
        refresh -- paths that do NOT rebuild the supply icons -- the
        stale entries accumulated on long-lived icons across a session
        rather than being swept away by the next rebuild, and
        SupplyPortIconItem.recolor() invokes every entry it finds."""
        for badges in self._isolation_icons.values():
            for icon in badges:
                if icon._color_source is not None and icon.sync_color in icon._color_source.on_recolor_callbacks:
                    icon._color_source.on_recolor_callbacks.remove(icon.sync_color)
                if icon.scene() is not None:
                    icon.scene().removeItem(icon)
        self._isolation_icons = {}

    def _clear_level_shifter_icons(self) -> None:
        """Same "remove every currently-materialized item, recompute
        wholesale afterward" lifecycle as _clear_isolation_icons() above,
        for Level Shifters.

        Deliberately has NO colour-callback detachment, unlike Isolation
        and Retention: a LevelShifterBadgeIconItem takes its colour from
        its owning Power Domain's plain `.color` value and registers
        nothing on any `on_recolor_callbacks` list (verified -- it has no
        `_color_source`/`sync_color` at all), so there is nothing here to
        detach. **If live colour binding is ever added to Level Shifter**
        -- plausible, since Isolation and Retention both have it -- this
        method must gain the same detach loop they have, or it silently
        inherits the exact leak the category-10 audit found in Isolation.
        Recorded here rather than as dead code that references attributes
        which don't currently exist."""
        for badges in self._level_shifter_icons.values():
            for icon in badges:
                if icon.scene() is not None:
                    icon.scene().removeItem(icon)
        self._level_shifter_icons = {}

    def _isolation_badge_slot_counts_per_box(self) -> tuple[dict[str, int], dict[str, int]]:
        """Extension (explicit request): Isolation and Level Shifter now
        share ONE unified row-stacking system, not two independent ones
        -- confirmed against this codebase's own already-established
        convention (Isolation is always built FIRST in every code path
        here, Level Shifter's own stacking already continued past
        wherever Isolation left off, see this method's own prior single-
        count version) and generalized: "by type then creation order"
        within each side, self/parent-mirrored ACROSS types the exact
        same way a same-type self+parent pair already aligns (see
        isolation_overlay.build_isolation_overlay()'s own docstring) --
        a Level Shifter self badge continuing Isolation's own `inside`
        sequence can land on the identical slot as an Isolation parent
        badge continuing the `outside` sequence, sharing a Y exactly
        like two same-type badges already do.

        Returns (inside_counts, outside_counts) -- SPLIT by each
        existing IsolationBadgeIconItem's own `.location`, not one flat
        total -- computed fresh from whatever `self._isolation_icons`
        already contains and handed to build_level_shifter_overlay()'s
        own `initial_inside_slots`/`initial_outside_slots`."""
        inside_counts: dict[str, int] = {}
        outside_counts: dict[str, int] = {}
        for badges in self._isolation_icons.values():
            for icon in badges:
                counts = inside_counts if icon.location in ("self",) else outside_counts
                counts[icon.owner_box.path] = counts.get(icon.owner_box.path, 0) + 1
        return inside_counts, outside_counts

    def _combined_badge_slot_counts(self) -> dict[str, int]:
        """Bug 3 (explicit request, "center around the block's vertical
        midpoint as a group"): the TRUE total badge count per owner_path,
        combining Isolation AND Level Shifter -- the two share ONE
        unified row-stacking system (see
        _isolation_badge_slot_counts_per_box()'s own docstring just
        above), so centering either type alone would be wrong the
        moment the other shares the same block.

        Deliberately ONE combined total per owner_path, not two separate
        inside/outside ones -- `max(inside_count, outside_count)`, not
        each side's own count. A self+parent pair sharing the SAME slot
        index has always shared the SAME Y (see IsolationBadgeIconItem's
        own module docstring, "align self/parent on the same line" --
        pre-existing, still tested requirement, unrelated to this bug).
        Centering each side independently around its OWN total would
        silently break that alignment the moment the two sequences have
        different lengths (confirmed empirically: a lone parent badge
        paired with 2 self badges no longer shared slot 0's Y under a
        naive per-side total). Using the longer side's own count for
        BOTH sides keeps every same-slot-index pair aligned exactly as
        before, while the group as a whole still ends up genuinely
        centered on the block (the longer side spans symmetrically
        around the midpoint; the shorter side's badges land wherever
        their own slot's Y falls on the longer side's axis, same as
        always).

        Counts whichever icons are CURRENTLY materialized in
        `self._isolation_icons`/`self._level_shifter_icons` -- always
        called AFTER both are (re)built, see _apply_group_centering()."""
        inside_counts: dict[str, int] = {}
        outside_counts: dict[str, int] = {}
        for badges in (*self._isolation_icons.values(), *self._level_shifter_icons.values()):
            for icon in badges:
                counts = inside_counts if icon.location in ("self",) else outside_counts
                counts[icon.owner_box.path] = counts.get(icon.owner_box.path, 0) + 1
        paths = set(inside_counts) | set(outside_counts)
        return {path: max(inside_counts.get(path, 0), outside_counts.get(path, 0)) for path in paths}

    def _apply_group_centering(self) -> None:
        """Bug 3 (explicit request, revises the earlier "append downward
        from a fixed start" row-stacking spec): re-centers EVERY
        currently-materialized Isolation/Level Shifter badge around its
        own block's vertical midpoint, as ONE group per owner_path (see
        _combined_badge_slot_counts()'s own docstring for why inside and
        outside share one combined total, not two independent ones) --
        1 badge lands exactly at the midpoint, 2 land symmetric above/
        below it, 3 land one-at-the-midpoint plus one-above plus
        one-below, and so on for N (see
        IsolationBadgeIconItem.recenter_in_group()'s own docstring for
        the exact formula, shared verbatim by LevelShifterBadgeIconItem).
        The underlying "one row per element, unified stack across types"
        PRINCIPLE (offset_index/location bookkeeping) is completely
        unchanged -- only the Y each offset_index maps to.

        Must run AFTER both build_isolation_overlay() and
        build_level_shifter_overlay() have (re)built their own icons for
        this pass -- every call site pairing the two already funnels
        through here, right after Level Shifter's own build call (see
        that call's own trailing comment)."""
        totals = self._combined_badge_slot_counts()
        for badges in (*self._isolation_icons.values(), *self._level_shifter_icons.values()):
            for icon in badges:
                icon.recenter_in_group(totals.get(icon.owner_box.path, 1))

    def _clear_domain_info_boxes(self) -> None:
        """Same "remove every currently-materialized item, recompute
        wholesale afterward" lifecycle as _clear_retention_icons() above
        -- used by create/edit/remove Power Domain, which all recompute
        `self._domain_info_boxes` fresh via build_domain_info_box_overlay()
        right after calling this. Each domain now maps to a LIST of
        boxes (one per member hierarchy -- see
        build_domain_info_box_overlay()'s own docstring), so this clears
        every box in every one of those lists. Supersedes the earlier
        separate _clear_domain_labels()/_clear_supply_set_info_boxes()
        (explicit request -- one merged box, one clear method)."""
        for boxes in self._domain_info_boxes.values():
            for box_item in boxes:
                if box_item._reposition in box_item.owner_box.on_resize_callbacks:
                    box_item.owner_box.on_resize_callbacks.remove(box_item._reposition)
                if box_item.scene() is not None:
                    box_item.scene().removeItem(box_item)
        self._domain_info_boxes = {}

    def _clear_macro_power_intent_badges(self) -> None:
        """Same "remove every currently-materialized item, recompute
        wholesale afterward" lifecycle as _clear_domain_info_boxes()
        above -- used by attach_macro_power_intent()/
        remove_macro_power_intent(), which both recompute
        `self._macro_power_intent_badges` fresh via
        build_macro_power_intent_badge_overlay() right after calling
        this (so an edit/replace is just a remove-then-rematerialize,
        not an in-place mutation of an existing item's own text)."""
        for badge in self._macro_power_intent_badges.values():
            if badge._reposition in badge.owner_box.on_resize_callbacks:
                badge.owner_box.on_resize_callbacks.remove(badge._reposition)
            if badge.scene() is not None:
                badge.scene().removeItem(badge)
        self._macro_power_intent_badges = {}

    def _retention_context_menu_actions(self, menu: QMenu, icon: RetentionBadgeIconItem) -> list:
        """Split out from _on_context_menu() so it's directly testable
        without going through menu.exec() -- same "build the actions,
        dispatch separately" split _supply_port_context_menu_actions()
        already uses. Each badge belongs to exactly one strategy, so
        these three actions only ever act on `icon.strategy_id`, never
        on every strategy touching the same block."""
        edit_action = menu.addAction("Edit")
        info_action = menu.addAction("Info")
        delete_action = menu.addAction("Delete")
        return [
            (edit_action, lambda: self._prompt_edit_retention(icon.strategy_id)),
            (info_action, lambda: self._prompt_show_retention_info(icon)),
            (delete_action, lambda: self._prompt_delete_retention(icon.strategy_id)),
        ]

    def _prompt_edit_retention(self, strategy_id: str) -> None:
        """Reopens the SAME "Retention Strategy" dialog "Add Retention"
        uses, pre-filled with the strategy's own current values (same
        "do NOT build a new one" precedent every other placement tool
        here follows) -- the global-uniqueness Name check excludes this
        strategy's own current name (`editing_strategy_id`), same
        precedent CreatePowerDomainDialog's own `editing_domain_id`
        already establishes. Takes a bare `strategy_id` (not a live
        icon) -- the diagram's own right-click-on-badge entry point
        only ever needed `icon.strategy_id` anyway, so this is directly
        reusable by the Properties table's own "Edit" row action, a
        second entry point into identical functionality.

        Elements are recollected from `strategy.owner_path` (the same
        "target hierarchy" scope root Add Retention used originally) --
        unioned with the strategy's own already-enabled elements so an
        edit never silently drops one that's still genuinely enabled,
        even if something about the surrounding domain shifted since
        this strategy was first created."""
        strategy = self._retention_state.strategies[strategy_id]
        domain = self._domain_state.domains[strategy.power_domain_id]
        available_elements = sorted(set(collect_retention_elements_for_domain(self._boxes, domain, self._domain_state.domains)) | set(strategy.elements))
        claimed = claimed_elements_by_other_strategies(self._retention_state.strategies, domain.id, exclude_strategy_id=strategy.id)
        dialog = RetentionStrategyDialog(
            domain,
            # The domain's OWN sets, resolved directly -- NOT
            # supply_sets_for_owners()'s "which sets may this domain
            # claim" offer. This dialog only ever looks up the domain's
            # own primary/secondary, and that offer legitimately omits
            # them once another domain shares one (ancestor Supply Set
            # sharing), which raised KeyError mid-construction and left
            # the dialog silently never opening.
            supply_sets_referenced_by_domain(self._domain_state, domain),
            known_supply_names_globally(self._shared_state),  # Bug 9: same whole-design pool as Create/Edit Power Domain's own inline Supply Set flow
            available_elements,
            claimed,
            self._existing_retention_names(),
            editing_strategy_id=strategy.id,
            initial=strategy,
            parent=self,
            # Explicit request/Fix 2: same "every member of the Power
            # Domain" scope as _prompt_add_retention() above, not just
            # the badge's own single owning block.
            target_paths=domain.owner_paths,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            duplicate = find_duplicate_strategy(
                self._retention_state.strategies,
                domain.id,
                dialog.primary_supply_set_id,
                dialog.retention_supply_set_id,
                dialog.save_logic,
                dialog.restore_logic,
                dialog.elements,
                exclude_strategy_id=strategy.id,
            )
            if duplicate is not None:
                if not self._confirm_merge_duplicate_retention(duplicate):
                    return  # same "do NOT silently duplicate" rule as Add -- declining leaves the strategy exactly as it was before this edit
                # Confirmed: the edited strategy is now redundant with `duplicate` (which keeps its own Name, per the same explicit answer as
                # the Add path) -- dissolve this one rather than leaving two identical strategies side by side.
                self.delete_retention_strategy(strategy.id)
                return
            self.edit_retention_strategy(
                strategy.id,
                dialog.name,
                dialog.primary_supply_set_id,
                dialog.retention_supply_set_id,
                dialog.save_logic,
                dialog.restore_logic,
                dialog.elements,
                dialog.new_supply_sets,
                dialog.new_supply_ports,
                dialog.new_supply_port_states,
            )

    def edit_retention_strategy(
        self,
        strategy_id: str,
        name: str,
        primary_supply_set_id: str,
        retention_supply_set_id: str,
        save_logic: str,
        restore_logic: str,
        elements: list[str],
        new_supply_sets: dict[str, SupplySetPlacement],
        new_supply_ports: Optional[dict[str, QColor]] = None,
        new_supply_port_states: Optional[dict[str, list[SupplyPortState]]] = None,
    ) -> RetentionStrategyPlacement:
        """The directly-testable core of "Edit" -- same bypass-the-dialog
        reasoning as create_retention_strategy(). `power_domain_id`/
        `owner_path` never change here -- editing only ever touches the
        fields the dialog actually exposes, same precedent
        edit_power_domain() already follows for its own `owner_path`."""
        for existing in self._retention_state.strategies.values():
            if existing.id != strategy_id and existing.name == name:
                raise ValueError(f"A Retention Strategy named '{name}' already exists.")
        supply_before = self._snapshot_state()
        retention_before = self._snapshot_retention_state()
        self._domain_state.supply_sets.update(new_supply_sets)
        current = self._retention_state.strategies[strategy_id]
        domain = self._domain_state.domains.get(current.power_domain_id)
        if domain is not None:
            for owner_path in domain.owner_paths:
                domain_box = self._boxes.get(owner_path)
                if domain_box is not None:
                    self._add_missing_supply_ports(domain_box, new_supply_ports or {}, new_supply_port_states)
        placement = dataclasses_replace(
            current,
            name=name,
            primary_supply_set_id=primary_supply_set_id,
            retention_supply_set_id=retention_supply_set_id,
            save_logic=save_logic,
            restore_logic=restore_logic,
            elements=list(elements),
        )
        self._retention_state.strategies[strategy_id] = placement
        self._clear_retention_icons()
        self._retention_icons = build_retention_overlay(self._boxes, self._retention_state.strategies, self._domain_state.supply_sets, self._supply_port_icons)
        self._commit_retention_undo_step("Edit Retention", supply_before, retention_before)
        return placement

    def _prompt_show_retention_info(self, icon: RetentionBadgeIconItem) -> None:
        strategy = self._retention_state.strategies[icon.strategy_id]
        domain = self._domain_state.domains.get(strategy.power_domain_id)
        supply_sets = self._domain_state.supply_sets
        domain_name = domain.name if domain is not None else "?"
        primary_name = supply_sets[strategy.primary_supply_set_id].name if strategy.primary_supply_set_id in supply_sets else "?"
        retention_name = supply_sets[strategy.retention_supply_set_id].name if strategy.retention_supply_set_id in supply_sets else "?"
        dialog = RetentionStrategyInfoDialog(strategy, domain_name, primary_name, retention_name, parent=self)
        dialog.exec()

    def _prompt_delete_retention(self, strategy_id: str) -> None:
        """Bug fix (explicit request): Retention deletion previously
        proceeded with NO confirmation at all -- unlike every comparable
        entity type (Isolation/Level Shifter/Power Switch, see
        _prompt_delete_isolation()'s own docstring), which all confirm
        first. Same plain message_box.question()/default-No convention,
        no cascade listing (matching the existing pattern for this
        class of entity -- a Retention Strategy has no downstream
        entities that reference IT the way a Power Domain/Supply Set
        does)."""
        strategy = self._retention_state.strategies.get(strategy_id)
        if strategy is None:
            return
        reply = message_box.question(
            self,
            "Delete Retention Strategy",
            f"Delete Retention Strategy '{strategy.name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.delete_retention_strategy(strategy_id)

    def delete_retention_strategy(self, strategy_id: str) -> None:
        """The directly-testable core of "Delete" -- bypasses the
        confirmation prompt (same "prompt is UI-layer only, this stays
        the real core" precedent every other placement tool here
        follows, see delete_isolation()'s own docstring) -- removes the
        strategy entirely and its icon from every block it appeared on
        (see _clear_retention_icons()/build_retention_overlay())."""
        supply_before = self._snapshot_state()
        retention_before = self._snapshot_retention_state()
        self._retention_state.strategies.pop(strategy_id, None)
        self._clear_retention_icons()
        self._retention_icons = build_retention_overlay(self._boxes, self._retention_state.strategies, self._domain_state.supply_sets, self._supply_port_icons)
        self._commit_retention_undo_step("Delete Retention", supply_before, retention_before)

    # --- Isolation Cell: validation -----------------------------------------
    # Explicit request: Isolation can ONLY be added on a Power Domain's own
    # BOUNDARY block -- find_domain_for_owner() (exact ownership: `box.path`
    # is literally one of the domain's own `owner_paths`), NOT
    # find_effective_domain_for_path()'s own cascading "anywhere inside the
    # subtree" rule Retention uses. A domain's own `owner_paths` already ARE
    # its boundary/topmost blocks by construction (creating a domain anchors
    # it there and colouring cascades DOWN from it -- see
    # power_domain_overlay.py's own apply_domain_coloring() docstring), so no
    # separate "topmost block" resolution is needed -- this reuses the EXACT
    # SAME check "Edit Power Domain"/"Remove Power Domain" enablement already
    # uses (_extra_context_menu_actions()).

    _ISOLATION_NO_BOUNDARY_ERROR = (
        "Isolation can only be added on a Power Domain's own boundary block -- select the block a Power Domain was "
        "created on directly (or one of its boundary blocks, for a multi-hierarchy domain), not merely a block nested inside one."
    )

    def _validate_isolation_target(self, box: InstanceBoxItem) -> Optional[str]:
        """Pure/side-effect-free -- the caller (_prompt_add_isolation())
        decides what to actually do with the result (a QMessageBox, or
        proceed to the dialog), same "always offered, explained on
        click" convention Retention's own _validate_retention_target()
        already establishes."""
        if find_domain_for_owner(self._domain_state.domains, box.path) is None:
            return self._ISOLATION_NO_BOUNDARY_ERROR
        return None

    # --- Isolation Cell: placement (dialog-driven) --------------------------

    def _existing_isolation_names(self) -> dict[str, str]:
        return {strategy.id: strategy.name for strategy in self._isolation_state.strategies.values()}

    def _prompt_add_isolation(self, box: InstanceBoxItem) -> None:
        """Reuses this SAME dialog for both entry points (same "do NOT
        build a new one" precedent every other placement tool here
        follows) -- the hover+click Isolation Cell tool and the
        right-click "Add Isolation" context-menu action both funnel
        through this one method, differing only in how `box` is chosen.
        Unlike Retention (whose own dialog receives a single, fixed
        domain), IsolationDialog's own "Power Domain" field is itself a
        selectable dropdown over EVERY domain in the design --
        `initial_domain_id=domain.id` only pre-selects the one `box`
        itself owns, the user is free to change it."""
        error = self._validate_isolation_target(box)
        if error is not None:
            message_box.warning(self, "Add Isolation", error)
            return
        domain = find_domain_for_owner(self._domain_state.domains, box.path)
        dialog = IsolationDialog(
            self._boxes,
            self._domain_state.domains,
            self._domain_state.supply_sets,
            self._resolve_connectivity,
            self._existing_isolation_names(),
            parent=self,
            target_paths=domain.owner_paths,
            initial_domain_id=domain.id,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            duplicate = find_duplicate_isolation(
                self._isolation_state.strategies,
                dialog.power_domain_id,
                dialog.isolation_supply_set_id,
                dialog.source_supply_set_id,
                dialog.sink_supply_set_id,
                dialog.clamp_value,
                dialog.location,
                dialog.isolation_control,
                dialog.isolation_sense,
                dialog.elements,
                applies_to=dialog.applies_to,
            )
            if duplicate is not None:
                if not self._confirm_merge_duplicate_isolation(duplicate):
                    return  # same "do NOT silently duplicate" rule as Retention -- declining the merge cancels rather than creating a redundant copy anyway
                return  # confirmed: merge into `duplicate` -- it already represents this exact state, nothing new to create; its own Name stays as-is
            self.create_isolation(
                box,
                dialog.name,
                dialog.power_domain_id,
                dialog.isolation_supply_set_id,
                dialog.source_supply_set_id,
                dialog.sink_supply_set_id,
                dialog.clamp_value,
                dialog.location,
                dialog.isolation_control,
                dialog.isolation_sense,
                dialog.elements,
                name_prefix=dialog.name_prefix,
                applies_to=dialog.applies_to,
            )

    def _confirm_merge_duplicate_isolation(self, duplicate: IsolationStrategyPlacement) -> bool:
        """Same "merge-confirmation, Yes-default" convention as
        Retention's own _confirm_merge_duplicate_retention()."""
        reply = message_box.question(
            self,
            "Duplicate Isolation",
            f"This matches an existing Isolation, '{duplicate.name}', exactly -- same Power Domain, Isolation/Source/Sink Supply Set, "
            "Clamp Value, Location, Isolation Control/Sense, Applies To All, and Elements. Merge into it instead of creating a duplicate?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        return reply == QMessageBox.StandardButton.Yes

    def create_isolation(
        self,
        box: InstanceBoxItem,
        name: str,
        power_domain_id: str,
        isolation_supply_set_id: str,
        source_supply_set_id: Optional[str],
        sink_supply_set_id: Optional[str],
        clamp_value: str,
        location: str,
        isolation_control: str,
        isolation_sense: str,
        elements: list[str],
        # Prefix for the inserted CELL instances. Trailing and defaulted:
        # optional at the model level, and every existing positional
        # caller predates it.
        name_prefix: str = "",
        # "Applies To All": "inputs"/"outputs"/"both" (no elements) or
        # "elements". Trailing and defaulted for the same reason.
        applies_to: str = "elements",
    ) -> IsolationStrategyPlacement:
        """The directly-testable core of "Add Isolation" -- bypasses the
        dialog (same reasoning as create_retention_strategy()'s own
        docstring). Raises ValueError on a name collision (defense-in-
        depth: the dialog already checks this before ever calling
        accept(), but this core stays correct on its own for any other/
        future caller that bypasses it)."""
        for existing in self._isolation_state.strategies.values():
            if existing.name == name:
                raise ValueError(f"An Isolation named '{name}' already exists.")
        supply_before = self._snapshot_state()
        isolation_before = self._snapshot_isolation_state()
        placement = IsolationStrategyPlacement(
            id=uuid4().hex,
            name=name,
            power_domain_id=power_domain_id,
            isolation_supply_set_id=isolation_supply_set_id,
            source_supply_set_id=source_supply_set_id,
            sink_supply_set_id=sink_supply_set_id,
            clamp_value=clamp_value,
            location=location,
            isolation_control=isolation_control,
            isolation_sense=isolation_sense,
            elements=list(elements),
            name_prefix=name_prefix,
            owner_path=box.path,
            applies_to=applies_to,
        )
        self._isolation_state.strategies[placement.id] = placement
        self._materialize_isolation_badges(placement)
        # Bug 3: _reflow_layout() (which may GROW this block to make room
        # for the new badge) must run BEFORE _apply_group_centering() --
        # centering reads the block's CURRENT rect() height, and
        # positioning first against the pre-growth rect() would leave
        # every badge stale the moment the block actually grows (a real,
        # confirmed regression: direct creation silently diverged from
        # what a full rebuild produced for the exact same state, caught
        # by test_isolation_level_shifter_row_stacking.py's own
        # direct-vs-rebuild parity test).
        self._reflow_layout()  # item 3: make room for the new badge -- see _extra_left_margin()/_extra_interior_top()'s own docstrings
        self._apply_group_centering()  # Bug 3: the newly-added badge changes the group's own total -- every sibling on the same block(s) must re-center too
        self._commit_isolation_undo_step("Add Isolation", supply_before, isolation_before)
        return placement

    def _materialize_isolation_badges(self, strategy: IsolationStrategyPlacement) -> list[IsolationBadgeIconItem]:
        """Materializes `strategy`'s own badge icon(s) inline, against
        whichever blocks it currently spans -- same "don't wait for a
        rebuild" reasoning _materialize_retention_badges() already
        documents. Shares variants_for_block() with build_isolation_overlay()
        (isolation_overlay.py) -- ONE place decides "normal, flipped, or
        both" per block (item 5), so this inline path and a full rebuild
        never disagree on how many icons/which variant(s) a block gets.

        Unified row-stacking with Level Shifter (explicit request,
        extension of the self+parent-pair-shares-a-Y rule
        build_isolation_overlay() establishes for a full rebuild -- this
        inline path must agree with it, or a direct "Add Isolation"
        would render stacked while the very next undo+redo -- a full
        _rebuild() -- snapped it back into alignment, the exact
        "undo+redo fixes it, direct creation doesn't" pattern already
        found and fixed elsewhere this session): the stacking slot
        counts EXISTING badges of EITHER type (Isolation AND Level
        Shifter, confirmed against this codebase's own already-
        established "Isolation always builds first, Level Shifter
        stacks past it" convention, generalized so BOTH types now share
        one unified sequence per side) on the same block sharing THIS
        strategy's own inside-vs-outside category (via each existing
        IsolationBadgeIconItem/LevelShifterBadgeIconItem's own
        `.location`) -- a same-category badge, either type, still
        stacks past it, but an opposite-category one (of either type)
        is free to land on the identical slot."""
        icons = []
        color_source = resolve_isolation_color_source(strategy, self._domain_state.supply_sets, self._supply_port_icons)
        top_edge_owners = top_edge_supply_port_owners(self._supply_port_icons)
        is_inside = strategy.location in ("self",)
        for owner_path in sorted(isolation_attachment_owner_paths(strategy, self._domain_state.domains)):
            box = self._boxes.get(owner_path)
            if box is None:
                continue
            extra_top_clearance = INTERIOR_TOP_MARGIN_FOR_SUPPLY_PORT if owner_path in top_edge_owners else 0.0
            for flipped in isolation_variants_on_block(strategy, box, owner_path):
                # Both `self._isolation_icons` (every OTHER strategy's
                # already-materialized badges) AND `icons` (this SAME
                # call's own variants added so far, for a mixed-direction
                # block -- not yet in self._isolation_icons until this
                # method returns) must count towards the stacking index,
                # or a block's own two variants would land on the exact
                # same slot and overlap -- but ONLY the ones sharing this
                # strategy's own inside/outside category (see docstring),
                # and now `self._level_shifter_icons` too, same category
                # scoping (see docstring's own "unified row-stacking"
                # paragraph).
                existing_count = sum(
                    1 for badges in self._isolation_icons.values() for icon in badges
                    if icon.owner_box is box and (icon.location in ("self",)) == is_inside
                )
                existing_count += sum(
                    1 for badges in self._level_shifter_icons.values() for icon in badges
                    if icon.owner_box is box and (icon.location == "self") == is_inside
                )
                existing_count += sum(1 for icon in icons if icon.owner_box is box and (icon.location in ("self",)) == is_inside)
                icons.append(IsolationBadgeIconItem(strategy.id, box, existing_count, color_source, strategy.location, flipped, extra_top_clearance))
        self._isolation_icons[strategy.id] = icons
        return icons

    def _isolation_context_menu_actions(self, menu: QMenu, icon: IsolationBadgeIconItem) -> list:
        """Split out from _on_context_menu() so it's directly testable
        without going through menu.exec() -- same "build the actions,
        dispatch separately" split _retention_context_menu_actions()
        already uses."""
        edit_action = menu.addAction("Edit")
        info_action = menu.addAction("Info")
        delete_action = menu.addAction("Delete")
        return [
            (edit_action, lambda: self._prompt_edit_isolation(icon.strategy_id)),
            (info_action, lambda: self._prompt_show_isolation_info(icon)),
            (delete_action, lambda: self._prompt_delete_isolation(icon.strategy_id)),
        ]

    def _prompt_edit_isolation(self, strategy_id: str) -> None:
        """Reopens the SAME "Isolation" dialog "Add Isolation" uses,
        pre-filled with the strategy's own current values (same "do NOT
        build a new one" precedent every other placement tool here
        follows) -- the global-uniqueness Name check excludes this
        strategy's own current name (`editing_strategy_id`). Takes a
        bare `strategy_id` (not a live icon) -- the diagram's own
        right-click-on-badge entry point only ever needed `icon.
        strategy_id` anyway, so this is directly reusable by the
        Properties table's own "Edit" row action (edit_properties_row()
        below), a second entry point into identical functionality."""
        strategy = self._isolation_state.strategies[strategy_id]
        domain = self._domain_state.domains.get(strategy.power_domain_id)
        dialog = IsolationDialog(
            self._boxes,
            self._domain_state.domains,
            self._domain_state.supply_sets,
            self._resolve_connectivity,
            self._existing_isolation_names(),
            editing_strategy_id=strategy.id,
            initial=strategy,
            parent=self,
            target_paths=domain.owner_paths if domain is not None else None,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            duplicate = find_duplicate_isolation(
                self._isolation_state.strategies,
                dialog.power_domain_id,
                dialog.isolation_supply_set_id,
                dialog.source_supply_set_id,
                dialog.sink_supply_set_id,
                dialog.clamp_value,
                dialog.location,
                dialog.isolation_control,
                dialog.isolation_sense,
                dialog.elements,
                exclude_strategy_id=strategy.id,
                applies_to=dialog.applies_to,
            )
            if duplicate is not None:
                if not self._confirm_merge_duplicate_isolation(duplicate):
                    return  # same "do NOT silently duplicate" rule as Add -- declining leaves the strategy exactly as it was before this edit
                self.delete_isolation(strategy.id)
                return
            self.edit_isolation(
                strategy.id,
                dialog.name,
                dialog.power_domain_id,
                dialog.isolation_supply_set_id,
                dialog.source_supply_set_id,
                dialog.sink_supply_set_id,
                dialog.clamp_value,
                dialog.location,
                dialog.isolation_control,
                dialog.isolation_sense,
                dialog.elements,
                name_prefix=dialog.name_prefix,
                applies_to=dialog.applies_to,
            )

    def edit_isolation(
        self,
        strategy_id: str,
        name: str,
        power_domain_id: str,
        isolation_supply_set_id: str,
        source_supply_set_id: Optional[str],
        sink_supply_set_id: Optional[str],
        clamp_value: str,
        location: str,
        isolation_control: str,
        isolation_sense: str,
        elements: list[str],
        # Prefix for the inserted CELL instances. Trailing and defaulted:
        # optional at the model level, and every existing positional
        # caller predates it.
        name_prefix: str = "",
        # "Applies To All": "inputs"/"outputs"/"both" (no elements) or
        # "elements". Trailing and defaulted for the same reason.
        applies_to: str = "elements",
    ) -> IsolationStrategyPlacement:
        """The directly-testable core of "Edit" -- same bypass-the-
        dialog reasoning as edit_retention_strategy()."""
        for existing in self._isolation_state.strategies.values():
            if existing.id != strategy_id and existing.name == name:
                raise ValueError(f"An Isolation named '{name}' already exists.")
        supply_before = self._snapshot_state()
        isolation_before = self._snapshot_isolation_state()
        current = self._isolation_state.strategies[strategy_id]
        placement = dataclasses_replace(
            current,
            name=name,
            power_domain_id=power_domain_id,
            isolation_supply_set_id=isolation_supply_set_id,
            source_supply_set_id=source_supply_set_id,
            sink_supply_set_id=sink_supply_set_id,
            clamp_value=clamp_value,
            location=location,
            isolation_control=isolation_control,
            isolation_sense=isolation_sense,
            elements=list(elements),
            name_prefix=name_prefix,
            applies_to=applies_to,
        )
        self._isolation_state.strategies[strategy_id] = placement
        self._clear_isolation_icons()
        self._isolation_icons = build_isolation_overlay(
            self._boxes, self._isolation_state.strategies, self._domain_state.supply_sets, self._supply_port_icons,
            domains=self._domain_state.domains,
        )
        # Bug fix (item 2, explicit request): Isolation's own per-block
        # badge count may have just changed (elements added/removed,
        # location changed) -- Level Shifter's own stacking on any
        # shared block was seeded from Isolation's PREVIOUS count, so it
        # must be recomputed too, or a now-larger Isolation badge count
        # could land on top of an already-placed Level Shifter badge.
        self._clear_level_shifter_icons()
        inside_slots, outside_slots = self._isolation_badge_slot_counts_per_box()
        self._level_shifter_icons = build_level_shifter_overlay(
            self._boxes,
            self._level_shifter_state.strategies,
            self._domain_state.domains,
            self._supply_port_icons,
            initial_inside_slots=inside_slots,
            initial_outside_slots=outside_slots,
        )
        self._reflow_layout()  # item 3/6: room requirement may have changed (elements/location edited) -- live, no manual refresh -- Bug 3: must run BEFORE centering, see create_isolation()'s own comment
        self._apply_group_centering()  # Bug 3: re-center the WHOLE Isolation+Level Shifter group now that the true combined total per block is known
        self._commit_isolation_undo_step("Edit Isolation", supply_before, isolation_before)
        return placement

    def _prompt_show_isolation_info(self, icon: IsolationBadgeIconItem) -> None:
        strategy = self._isolation_state.strategies[icon.strategy_id]
        domain = self._domain_state.domains.get(strategy.power_domain_id)
        supply_sets = self._domain_state.supply_sets
        domain_name = domain.name if domain is not None else "?"
        isolation_name = supply_sets[strategy.isolation_supply_set_id].name if strategy.isolation_supply_set_id in supply_sets else "?"
        source_name = supply_sets[strategy.source_supply_set_id].name if strategy.source_supply_set_id in supply_sets else ""
        sink_name = supply_sets[strategy.sink_supply_set_id].name if strategy.sink_supply_set_id in supply_sets else ""
        dialog = IsolationInfoDialog(strategy, domain_name, isolation_name, source_name, sink_name, parent=self)
        dialog.exec()

    def _prompt_delete_isolation(self, strategy_id: str) -> None:
        """Explicit request: Isolation deletion gets its own confirmation
        prompt -- same message_box.question()/default-No convention
        _prompt_delete_power_switch() already establishes for a
        destructive-feeling confirmation in this diagram (Retention now
        confirms too, see _prompt_delete_retention() -- a later, separate
        explicit request fixing what used to be the one entity type here
        that deleted immediately with no prompt at all; Power Domain
        alone keeps its own richer, cascade-aware confirmation instead
        of this plain shape, since removing one can also remove
        Supply Sets/Retention/Power Switches/Isolation/Level Shifters
        that reference it)."""
        strategy = self._isolation_state.strategies.get(strategy_id)
        if strategy is None:
            return
        reply = message_box.question(
            self,
            "Delete Isolation",
            f"Delete Isolation '{strategy.name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.delete_isolation(strategy_id)

    def delete_isolation(self, strategy_id: str) -> None:
        """The directly-testable core of "Delete" -- bypasses the
        confirmation prompt (same "prompt is UI-layer only, this stays
        the real core" precedent every other placement tool here
        follows). Removes the strategy entirely and its icon from EVERY
        block it appeared on (see _clear_isolation_icons()/
        build_isolation_overlay()) -- per the Retention model, one
        shared entity, several attachment points, all removed together."""
        supply_before = self._snapshot_state()
        isolation_before = self._snapshot_isolation_state()
        self._isolation_state.strategies.pop(strategy_id, None)
        self._clear_isolation_icons()
        self._isolation_icons = build_isolation_overlay(
            self._boxes, self._isolation_state.strategies, self._domain_state.supply_sets, self._supply_port_icons,
            domains=self._domain_state.domains,
        )
        # Bug fix (item 2, explicit request): same "Level Shifter's own
        # stacking depends on Isolation's current count" reasoning
        # edit_isolation() already establishes.
        self._clear_level_shifter_icons()
        inside_slots, outside_slots = self._isolation_badge_slot_counts_per_box()
        self._level_shifter_icons = build_level_shifter_overlay(
            self._boxes,
            self._level_shifter_state.strategies,
            self._domain_state.domains,
            self._supply_port_icons,
            initial_inside_slots=inside_slots,
            initial_outside_slots=outside_slots,
        )
        self._reflow_layout()  # item 3: the room reserved for this Isolation's own badge(s) is no longer needed -- Bug 3: must run BEFORE centering, see create_isolation()'s own comment
        self._apply_group_centering()  # Bug 3: re-center the WHOLE Isolation+Level Shifter group now that the true combined total per block is known
        self._commit_isolation_undo_step("Delete Isolation", supply_before, isolation_before)

    # --- Level Shifter: validation -------------------------------------------
    # Same rule as Isolation (explicit request, section 1 -- follows the
    # Isolation model per section 5): find_domain_for_owner() (exact
    # boundary ownership), NOT find_effective_domain_for_path()'s own
    # cascading "anywhere inside" rule Retention/Power Switch use.

    _LEVEL_SHIFTER_NO_BOUNDARY_ERROR = (
        "Level Shifter can only be added on a Power Domain's own boundary block -- select the block a Power Domain was "
        "created on directly (or one of its boundary blocks, for a multi-hierarchy domain), not merely a block nested inside one."
    )

    def _validate_level_shifter_target(self, box: InstanceBoxItem) -> Optional[str]:
        """Pure/side-effect-free -- same "always offered, explained on
        click" convention _validate_isolation_target() already
        establishes."""
        if find_domain_for_owner(self._domain_state.domains, box.path) is None:
            return self._LEVEL_SHIFTER_NO_BOUNDARY_ERROR
        return None

    # --- Level Shifter: placement (dialog-driven) ----------------------------

    def _existing_level_shifter_names(self) -> dict[str, str]:
        return {strategy.id: strategy.name for strategy in self._level_shifter_state.strategies.values()}

    def _prompt_add_level_shifter(self, box: InstanceBoxItem) -> None:
        """Reuses this SAME dialog for both entry points (same "do NOT
        build a new one" precedent every other placement tool here
        follows) -- the hover+click Level Shifter tool and the
        right-click "Add Level Shifter" context-menu action both funnel
        through this one method, differing only in how `box` is chosen.
        Unlike Isolation, this dialog's own "Power Domain" field is
        FIXED, read-only context (explicit request: "same convention as
        Retention's Power Domain field") -- `domain` is resolved once
        here and handed to the dialog directly, never a user-selectable
        dropdown."""
        error = self._validate_level_shifter_target(box)
        if error is not None:
            message_box.warning(self, "Add Level Shifter", error)
            return
        domain = find_domain_for_owner(self._domain_state.domains, box.path)
        dialog = LevelShifterDialog(
            self._boxes,
            domain,
            self._resolve_connectivity,
            self._existing_level_shifter_names(),
            parent=self,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            duplicate = find_duplicate_level_shifter(
                self._level_shifter_state.strategies,
                dialog.power_domain_id,
                dialog.applies_to,
                dialog.rule,
                dialog.location,
                dialog.threshold,
                dialog.cell_type,
                dialog.elements,
            )
            if duplicate is not None:
                if not self._confirm_merge_duplicate_level_shifter(duplicate):
                    return  # same "do NOT silently duplicate" rule as Isolation -- declining the merge cancels rather than creating a redundant copy anyway
                return  # confirmed: merge into `duplicate` -- it already represents this exact state, nothing new to create
            self.create_level_shifter(
                box,
                dialog.name,
                dialog.power_domain_id,
                dialog.applies_to,
                dialog.rule,
                dialog.location,
                dialog.threshold,
                dialog.cell_type,
                dialog.elements,
                name_prefix=dialog.name_prefix,
            )

    def _confirm_merge_duplicate_level_shifter(self, duplicate: LevelShifterStrategyPlacement) -> bool:
        """Same "merge-confirmation, Yes-default" convention as
        Isolation's own _confirm_merge_duplicate_isolation()."""
        reply = message_box.question(
            self,
            "Duplicate Level Shifter",
            f"This matches an existing Level Shifter, '{duplicate.name}', exactly -- same Power Domain, Applies To, Rule, Location, "
            "Threshold, Level Shifter Cell Type, and Elements. Merge into it instead of creating a duplicate?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        return reply == QMessageBox.StandardButton.Yes

    def create_level_shifter(
        self,
        box: InstanceBoxItem,
        name: str,
        power_domain_id: str,
        applies_to: str,
        rule: str,
        location: str,
        threshold: Optional[float],
        cell_type: Optional[str],
        elements: list[str],
        name_prefix: str = "",
    ) -> LevelShifterStrategyPlacement:
        """The directly-testable core of "Add Level Shifter" -- bypasses
        the dialog (same reasoning as create_isolation()'s own
        docstring). Raises ValueError on a name collision (defense-in-
        depth: the dialog already checks this before ever calling
        accept(), but this core stays correct on its own for any other/
        future caller that bypasses it)."""
        for existing in self._level_shifter_state.strategies.values():
            if existing.name == name:
                raise ValueError(f"A Level Shifter named '{name}' already exists.")
        level_shifter_before = self._snapshot_level_shifter_state()
        placement = LevelShifterStrategyPlacement(
            id=uuid4().hex,
            name=name,
            power_domain_id=power_domain_id,
            applies_to=applies_to,
            rule=rule,
            location=location,
            threshold=threshold,
            cell_type=cell_type,
            elements=list(elements),
            name_prefix=name_prefix,
            owner_path=box.path,
        )
        self._level_shifter_state.strategies[placement.id] = placement
        self._materialize_level_shifter_badges(placement)
        self._reflow_layout()  # item 3: make room for the new badge -- see _extra_left_margin()/_extra_interior_top()'s own docstrings -- Bug 3: must run BEFORE centering, see create_isolation()'s own comment
        self._apply_group_centering()  # Bug 3: the newly-added badge changes the group's own total -- every sibling on the same block(s) must re-center too
        self._commit_level_shifter_undo_step("Add Level Shifter", level_shifter_before)
        return placement

    def _materialize_level_shifter_badges(self, strategy: LevelShifterStrategyPlacement) -> list[LevelShifterBadgeIconItem]:
        """Materializes `strategy`'s own badge icon(s) inline, against
        whichever blocks it currently spans -- same "don't wait for a
        rebuild" reasoning _materialize_isolation_badges() already
        documents.

        Unified row-stacking with Isolation (explicit request, extension
        -- same self+parent-pair-shares-a-Y rule build_level_shifter_overlay()
        establishes for a full rebuild, now spanning BOTH badge types --
        this inline path must agree with it, same "undo+redo fixes it,
        direct creation doesn't" reasoning _materialize_isolation_badges()
        already documents): `existing_count` counts EXISTING badges of
        EITHER type on the same block sharing THIS strategy's own
        inside-vs-outside category (via each existing IsolationBadgeIconItem/
        LevelShifterBadgeIconItem's own `.location`) -- a same-category
        badge, Isolation or Level Shifter alike, still stacks past it,
        but an opposite-category one (of either type) is free to land on
        the identical slot."""
        icons = []
        color = resolve_level_shifter_color(strategy, self._domain_state.domains)
        top_edge_owners = top_edge_supply_port_owners(self._supply_port_icons)
        is_inside = strategy.location == "self"
        # Bug fix (explicit request, "Level Shifter icon not rendered"
        # repro): NOT unconditionally owner_paths_for_elements()/
        # variants_for_block() anymore -- see level_shifter_overlay.py's
        # own attachment_owner_paths()/variants_for_strategy_on_block()
        # docstrings for the full "why" (a broad, non-"elements"
        # strategy has no elements at all to derive attachment/direction
        # from).
        for owner_path in sorted(attachment_owner_paths(strategy, self._domain_state.domains)):
            box = self._boxes.get(owner_path)
            if box is None:
                continue
            extra_top_clearance = INTERIOR_TOP_MARGIN_FOR_SUPPLY_PORT if owner_path in top_edge_owners else 0.0
            for flipped in variants_for_strategy_on_block(strategy, box, owner_path):
                existing_count = sum(
                    1 for badges in self._level_shifter_icons.values() for icon in badges
                    if icon.owner_box is box and (icon.location == "self") == is_inside
                )
                existing_count += sum(
                    1 for badges in self._isolation_icons.values() for icon in badges
                    if icon.owner_box is box and (icon.location in ("self",)) == is_inside
                )
                existing_count += sum(1 for icon in icons if icon.owner_box is box and (icon.location == "self") == is_inside)
                icons.append(LevelShifterBadgeIconItem(strategy.id, box, existing_count, color, strategy.location, flipped, extra_top_clearance))
        self._level_shifter_icons[strategy.id] = icons
        return icons

    def _level_shifter_context_menu_actions(self, menu: QMenu, icon: LevelShifterBadgeIconItem) -> list:
        """Split out from _on_context_menu() so it's directly testable
        without going through menu.exec() -- same "build the actions,
        dispatch separately" split _isolation_context_menu_actions()
        already uses."""
        edit_action = menu.addAction("Edit")
        info_action = menu.addAction("Info")
        delete_action = menu.addAction("Delete")
        return [
            (edit_action, lambda: self._prompt_edit_level_shifter(icon.strategy_id)),
            (info_action, lambda: self._prompt_show_level_shifter_info(icon)),
            (delete_action, lambda: self._prompt_delete_level_shifter(icon.strategy_id)),
        ]

    def _prompt_edit_level_shifter(self, strategy_id: str) -> None:
        """Reopens the SAME "Level Shifter" dialog "Add Level Shifter"
        uses, pre-filled with the strategy's own current values."""
        strategy = self._level_shifter_state.strategies[strategy_id]
        domain = self._domain_state.domains.get(strategy.power_domain_id)
        if domain is None:
            return
        dialog = LevelShifterDialog(
            self._boxes,
            domain,
            self._resolve_connectivity,
            self._existing_level_shifter_names(),
            editing_strategy_id=strategy.id,
            initial=strategy,
            parent=self,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            duplicate = find_duplicate_level_shifter(
                self._level_shifter_state.strategies,
                dialog.power_domain_id,
                dialog.applies_to,
                dialog.rule,
                dialog.location,
                dialog.threshold,
                dialog.cell_type,
                dialog.elements,
                exclude_strategy_id=strategy.id,
            )
            if duplicate is not None:
                if not self._confirm_merge_duplicate_level_shifter(duplicate):
                    return  # same "do NOT silently duplicate" rule as Add -- declining leaves the strategy exactly as it was before this edit
                self.delete_level_shifter(strategy.id)
                return
            self.edit_level_shifter(
                strategy.id,
                dialog.name,
                dialog.power_domain_id,
                dialog.applies_to,
                dialog.rule,
                dialog.location,
                dialog.threshold,
                dialog.cell_type,
                dialog.elements,
                name_prefix=dialog.name_prefix,
            )

    def edit_level_shifter(
        self,
        strategy_id: str,
        name: str,
        power_domain_id: str,
        applies_to: str,
        rule: str,
        location: str,
        threshold: Optional[float],
        cell_type: Optional[str],
        elements: list[str],
        name_prefix: str = "",
    ) -> LevelShifterStrategyPlacement:
        """The directly-testable core of "Edit" -- same bypass-the-
        dialog reasoning as edit_isolation()."""
        for existing in self._level_shifter_state.strategies.values():
            if existing.id != strategy_id and existing.name == name:
                raise ValueError(f"A Level Shifter named '{name}' already exists.")
        level_shifter_before = self._snapshot_level_shifter_state()
        current = self._level_shifter_state.strategies[strategy_id]
        placement = dataclasses_replace(
            current,
            name=name,
            power_domain_id=power_domain_id,
            applies_to=applies_to,
            rule=rule,
            location=location,
            threshold=threshold,
            cell_type=cell_type,
            elements=list(elements),
            name_prefix=name_prefix,
        )
        self._level_shifter_state.strategies[strategy_id] = placement
        self._clear_level_shifter_icons()
        inside_slots, outside_slots = self._isolation_badge_slot_counts_per_box()
        self._level_shifter_icons = build_level_shifter_overlay(
            self._boxes,
            self._level_shifter_state.strategies,
            self._domain_state.domains,
            self._supply_port_icons,
            initial_inside_slots=inside_slots,
            initial_outside_slots=outside_slots,
        )
        self._reflow_layout()  # room requirement may have changed (elements/location edited) -- live, no manual refresh -- Bug 3: must run BEFORE centering, see create_isolation()'s own comment
        self._apply_group_centering()  # Bug 3: re-center the WHOLE Isolation+Level Shifter group now that the true combined total per block is known
        self._commit_level_shifter_undo_step("Edit Level Shifter", level_shifter_before)
        return placement

    def _prompt_show_level_shifter_info(self, icon: LevelShifterBadgeIconItem) -> None:
        strategy = self._level_shifter_state.strategies[icon.strategy_id]
        domain = self._domain_state.domains.get(strategy.power_domain_id)
        domain_name = domain.name if domain is not None else "?"
        cell_name = strategy.cell_type or "?"
        dialog = LevelShifterInfoDialog(strategy, domain_name, cell_name, parent=self)
        dialog.exec()

    def _prompt_delete_level_shifter(self, strategy_id: str) -> None:
        """Explicit request (mirrors Isolation's own confirmation
        exactly): Level Shifter deletion gets its own Yes/No
        confirmation, default No."""
        strategy = self._level_shifter_state.strategies.get(strategy_id)
        if strategy is None:
            return
        reply = message_box.question(
            self,
            "Delete Level Shifter",
            f"Delete Level Shifter '{strategy.name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.delete_level_shifter(strategy_id)

    def delete_level_shifter(self, strategy_id: str) -> None:
        """The directly-testable core of "Delete" -- bypasses the
        confirmation prompt. Removes the strategy entirely and its icon
        from EVERY block it appeared on -- per the Isolation model, one
        shared entity, several attachment points, all removed together."""
        level_shifter_before = self._snapshot_level_shifter_state()
        self._level_shifter_state.strategies.pop(strategy_id, None)
        self._clear_level_shifter_icons()
        inside_slots, outside_slots = self._isolation_badge_slot_counts_per_box()
        self._level_shifter_icons = build_level_shifter_overlay(
            self._boxes,
            self._level_shifter_state.strategies,
            self._domain_state.domains,
            self._supply_port_icons,
            initial_inside_slots=inside_slots,
            initial_outside_slots=outside_slots,
        )
        self._reflow_layout()  # item 3: the room reserved for this Level Shifter's own badge(s) is no longer needed -- Bug 3: must run BEFORE centering, see create_isolation()'s own comment
        self._apply_group_centering()  # Bug 3: re-center the WHOLE Isolation+Level Shifter group now that the true combined total per block is known
        self._commit_level_shifter_undo_step("Delete Level Shifter", level_shifter_before)

    # --- Power Switch: placement (dialog-driven) --------------------------

    def _existing_power_switch_names(self) -> dict[str, str]:
        return {switch.id: switch.name for switch in self._power_switch_state.switches.values()}

    def _resolve_power_switch_supply_in_name(self, switch_icon: PowerSwitchIconItem) -> Optional[str]:
        """"Supply In" is never stored -- it's derived fresh from
        whatever is currently wired to the switch's own TOP handle via
        the diagram's own Supply Net connector system (see
        power_switch_overlay.py's own module docstring). Reuses
        connected_supply_port() exactly as-is (the same core macro
        PG-pin "Connected" resolution already relies on) -- `switch_icon.key`
        is a real, ordinary entry in `_shared_state.supply_connector_placements`
        the moment a top-anchor connector is drawn, so no Power-Switch-
        specific lookup is needed."""
        connected = connected_supply_port(switch_icon.key, self._shared_state)
        return connected.name if connected is not None else None

    def _corresponding_target_ids(self, icon: SupplyPortIconItem, domain: PowerDomainPlacement) -> list[str]:
        """Item 9 (explicit request, multi-hierarchy Power Domain
        replication): every OTHER member hierarchy of `domain`'s own
        Internal Supply/Supply Port sharing `icon`'s own name -- the
        "corresponding" target a multi-hierarchy switch automatically
        replicates onto, same "same name across member hierarchies ==
        the same logical entity" reading already established for
        multi-hierarchy Supply Set replication
        (_add_missing_supply_ports()). Found by (name, owner_path)
        match against the PERSISTED placements, not just currently-
        materialized icons -- a member hierarchy whose own box happens
        to be hidden right now still counts (its own icon simply isn't
        materialized THIS pass -- build_power_switch_overlay() already
        tolerates that, same "not materialized, not discarded"
        precedent every other diagram-only placement here follows). A
        member hierarchy with no matching-named port at all is silently
        skipped, not guessed at or fabricated."""
        ids = []
        for owner_path in domain.owner_paths:
            if owner_path == icon.placement.owner_path:
                continue
            match = next(
                (p for p in self._shared_state.supply_port_placements.values() if p.owner_path == owner_path and p.name == icon.name), None
            )
            if match is not None:
                ids.append(match.id)
        return ids

    def _power_switch_add_error(self, icon: SupplyPortIconItem) -> Optional[str]:
        """The FULL "can Add Power Switch actually succeed for `icon`"
        check, including its own multi-hierarchy replicas (item 9) --
        shared by _prompt_add_power_switch() (pre-dialog check, so a
        conflict on a REPLICA the user never directly clicked still
        stops the dialog from opening for nothing) and add_power_switch()
        itself (data-model-level re-check, never relies on UI-disabling
        alone). Rule A (max one per target) on `icon` itself is checked
        FIRST -- the same "more immediately actionable" priority
        _prompt_add_power_switch() always used, preserved here even
        though replication adds a second, later place rule A can also
        fail. A Virtual Supply is rejected before even THAT (explicit
        request: "A Power Switch can NEVER be added on a Virtual
        Supply") -- the single most fundamental rejection reason,
        checked first, same "more fundamental rejection reason first"
        precedent _validate_supply_port_target() already establishes."""
        if icon.placement.is_virtual_supply:
            return self._POWER_SWITCH_VIRTUAL_SUPPLY_ERROR
        if find_switch_for_target(self._power_switch_state.switches, icon.key) is not None:
            return self._POWER_SWITCH_ALREADY_EXISTS_ERROR
        name_conflict_with = self._supply_in_name_conflict_owner(icon)
        if name_conflict_with is not None:
            return self._POWER_SWITCH_NAME_CONTINUITY_ERROR_TEMPLATE.format(name=icon.placement.name, other_owner_path=name_conflict_with)
        domain = find_effective_domain_for_path(self._domain_state.domains, icon.placement.owner_path)
        if domain is None:
            return self._POWER_SWITCH_NO_DOMAIN_ERROR
        for target_id in self._corresponding_target_ids(icon, domain):
            if find_switch_for_target(self._power_switch_state.switches, target_id) is not None:
                return self._POWER_SWITCH_ALREADY_EXISTS_ERROR
        return None

    def _prompt_add_power_switch(self, icon: SupplyPortIconItem) -> None:
        """Reuses this SAME dialog for both entry points (same "do NOT
        build a new one" precedent every other placement tool here
        follows) -- the Power Switch tool's own hover+click placement and
        the right-click "Add Power Switch" context-menu action both
        funnel through this one method, differing only in how `icon` is
        chosen.

        Checked *before* ever constructing the dialog (same reasoning as
        _prompt_add_retention()): every rule _power_switch_add_error()
        checks rejects every possible field value for a target that
        fails it, so there is nothing a dialog could offer to fix."""
        error = self._power_switch_add_error(icon)
        if error is not None:
            message_box.warning(self, "Add Power Switch", error)
            return
        domain = find_effective_domain_for_path(self._domain_state.domains, icon.placement.owner_path)
        # Item 3 (explicit request): "Applies to" must list every
        # hierarchy this switch will actually attach to -- for a
        # multi-hierarchy Power Domain, that's `icon`'s own owner_path
        # PLUS every other member hierarchy's own "corresponding"
        # (same-name) target add_power_switch() itself will replicate
        # onto (_corresponding_target_ids()), computed here since the
        # switch doesn't exist yet to read its own owner_paths from.
        target_paths = [icon.owner_box.path] + [
            self._shared_state.supply_port_placements[tid].owner_path for tid in self._corresponding_target_ids(icon, domain)
        ]
        dialog = PowerSwitchDialog(target_paths, icon.name, domain, None, self._existing_power_switch_names(), parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                self.add_power_switch(
                    icon,
                    dialog.name,
                    dialog.input_supply_port,
                    dialog.output_supply_port,
                    dialog.control_port,
                    dialog.cell_type,
                    dialog.on_state,
                    dialog.off_state,
                    control_signal=dialog.control_signal,
                    ack_port=dialog.ack_port,
                    ack_signal=dialog.ack_signal,
                )
            except ValueError as exc:
                message_box.warning(self, "Add Power Switch", str(exc))

    def add_power_switch(
        self,
        icon: SupplyPortIconItem,
        name: str,
        input_supply_port: str,
        output_supply_port: str,
        control_port: str,
        cell_type: Optional[str],
        on_state: str,
        off_state: str,
        # "Control Signal" -- the RTL net driving the control pin. Last
        # and defaulted so every existing positional caller is unaffected,
        # and because it is genuinely optional at the model level: a
        # switch IMPORTED from a file that wrote a bare `-control_port
        # CTRL` has no net to record. The dialog itself requires one.
        control_signal: str = "",
        # The acknowledge pair, defaulted for the same reason
        # control_signal is: genuinely optional, and every existing
        # positional caller predates it.
        ack_port: str = "",
        ack_signal: str = "",
    ) -> PowerSwitchPlacement:
        """The directly-testable core of "Add Power Switch" -- bypasses
        the dialog (same reasoning as add_supply_port()). Re-checks
        every validation rule itself, INCLUDING item 9's own multi-
        hierarchy replication conflicts (never relies on UI-disabling
        alone, per the explicit requirement) so this stays correct on
        its own for any caller that bypasses _prompt_add_power_switch()'s
        own checks -- same "second, structural layer" precedent
        add_supply_port()'s own macro-target re-check already
        establishes.

        Item 9 (explicit request): when `icon` belongs to a multi-
        hierarchy Power Domain, this switch automatically spans every
        OTHER member hierarchy's own "corresponding" (same-name)
        Internal Supply too (_corresponding_target_ids()) -- ONE shared
        PowerSwitchPlacement, `target_ids` covering all of them, one
        icon/auto-connector materialized per target that currently has
        a live SupplyPortIconItem to attach to."""
        error = self._power_switch_add_error(icon)
        if error is not None:
            raise ValueError(error)
        domain = find_effective_domain_for_path(self._domain_state.domains, icon.placement.owner_path)
        if not validate_no_spaces(name):
            raise ValueError(NO_SPACES_ERROR)
        target_ids = [icon.key, *self._corresponding_target_ids(icon, domain)]
        owner_paths = [self._shared_state.supply_port_placements[tid].owner_path for tid in target_ids]
        power_switch_before = self._snapshot_power_switch_state()
        # Item 8 (explicit request): snapshotted BEFORE
        # _adopt_existing_connection_as_supply_in() below can mutate
        # `_shared_state.supply_connector_placements` -- committed
        # alongside power_switch_before so a single Ctrl+Z reverses every
        # re-route atomically with the switch's own creation.
        supply_before = self._snapshot_state()
        placement = PowerSwitchPlacement(
            id=uuid4().hex,
            name=name,
            power_domain_id=domain.id,
            target_ids=target_ids,
            owner_paths=owner_paths,
            input_supply_port=input_supply_port,
            output_supply_port=output_supply_port,
            control_port=control_port,
            control_signal=control_signal,
            ack_port=ack_port,
            ack_signal=ack_signal,
            cell_type=cell_type,
            on_state=on_state,
            off_state=off_state,
        )
        self._power_switch_state.switches[placement.id] = placement
        switch_icons: list[PowerSwitchIconItem] = []
        connectors_for_switch: list[SupplyPortConnectorItem] = []
        for target_id in target_ids:
            target_icon = self._supply_port_icons.get(target_id)
            if target_icon is None:
                continue  # e.g. that member hierarchy's own box is currently hidden -- materializes on unhide/rebuild instead
            switch_icon = PowerSwitchIconItem(target_icon, placement)
            self.scene().addItem(switch_icon)  # scene-level item now, see PowerSwitchIconItem's own docstring
            switch_icons.append(switch_icon)
            # Item 8: re-route any connector `target_icon` ALREADY had
            # (its own pre-existing "Supply In"-shaped connection)
            # through THIS icon's own top anchor instead -- BEFORE the
            # auto bottom connector below is created, so this only ever
            # sees connectors that predate this switch, never that one.
            # Runs independently per target -- each member hierarchy's
            # own upstream wiring (if any) is its own, unrelated to any
            # sibling target's.
            self._adopt_existing_connection_as_supply_in(target_icon, switch_icon)
            connector = SupplyPortConnectorItem(switch_icon, target_icon, port_a_handle_key="bottom")
            connector.setZValue(Z_POWER_SWITCH_CONNECTOR)
            self.scene().addItem(connector)
            switch_icon.connectors.append(connector)
            target_icon.connectors.append(connector)
            connectors_for_switch.append(connector)
        self._power_switch_icons[placement.id] = switch_icons
        self._power_switch_connectors[placement.id] = connectors_for_switch
        self._reflow_layout()  # item 3: make room for the new switch icon -- see _extra_top_margin()'s own docstring
        self._commit_power_switch_undo_step("Add Power Switch", power_switch_before, supply_before)
        return placement

    def _adopt_existing_connection_as_supply_in(self, icon: SupplyPortIconItem, switch_icon: PowerSwitchIconItem) -> None:
        """Item 8 (explicit request): if `icon` already had an incoming
        power-net connector BEFORE this switch was placed, that
        connector is re-routed to terminate at the switch's own TOP
        anchor instead -- the switch is "inserted into" the existing
        connection (its BOTTOM anchor already connects to `icon`
        separately, via the auto connector this method's own caller
        creates right after) -- rather than requiring the user to
        delete and manually redraw it. A no-op when `icon` has no
        pre-existing connector (Supply In starts empty, as already
        established). Takes the FIRST connector found if `icon`
        happens to have more than one (a rare, ambiguous edge case this
        app doesn't otherwise prevent -- not guessed at further).

        Updates THREE things together, atomically: the live
        SupplyPortConnectorItem's own endpoint reference (so its route
        recomputes from the switch's new position, not the target's),
        the persisted `supply_connector_placements` pair (by icon key,
        the same "keys survive a rebuild, objects don't" precedent
        try_connect_supply_ports() already establishes), and any
        persisted manual drag offset for that same pair (so a
        previously-dragged segment's own offset stays correctly
        associated with the re-pointed connection instead of silently
        reverting to auto-routed)."""
        if not icon.connectors:
            return
        connector = icon.connectors[0]
        other = connector.port_b if connector.port_a is icon else connector.port_a
        if connector.port_a is icon:
            connector.port_a = switch_icon
        else:
            connector.port_b = switch_icon
        icon.connectors.remove(connector)
        switch_icon.connectors.append(connector)
        connector.update_route()

        old_forward, old_backward = (icon.key, other.key), (other.key, icon.key)
        new_forward, new_backward = (switch_icon.key, other.key), (other.key, switch_icon.key)
        self._shared_state.supply_connector_placements = [
            new_forward if pair == old_forward else new_backward if pair == old_backward else pair
            for pair in self._shared_state.supply_connector_placements
        ]
        for old_pair, new_pair in ((old_forward, new_forward), (old_backward, new_backward)):
            offset = self._shared_state.manual_connector_offsets.pop(old_pair, None)
            if offset is not None:
                self._shared_state.manual_connector_offsets[new_pair] = offset

    def _prompt_edit_power_switch(self, icon: SupplyPortIconItem, existing: PowerSwitchPlacement) -> None:
        domain = self._domain_state.domains.get(existing.power_domain_id)
        if domain is None:
            # The domain this switch was created against was since removed
            # -- same "?" fallback RetentionStrategyInfoDialog's own
            # _prompt_show_retention_info() already uses for an analogous
            # dangling reference, rather than crashing on a missing key.
            domain = PowerDomainPlacement(id="", owner_path=icon.owner_box.path, name="?", color=QColor())
        # Item 9: `existing.id` maps to a LIST of icons (one per
        # replicated target) -- the dialog opened from THIS specific
        # right-click must resolve Supply In for `icon`'s OWN instance,
        # not an arbitrary one (each replica's own upstream wiring is
        # independent -- see _adopt_existing_connection_as_supply_in()'s
        # own docstring).
        switch_icon = next((si for si in self._power_switch_icons.get(existing.id, []) if si.target_icon is icon), None)
        supply_in_name = self._resolve_power_switch_supply_in_name(switch_icon) if switch_icon is not None else None
        # Item 3: `existing.owner_paths` is already the correct,
        # already-computed "every hierarchy this switch actually
        # attaches to" list (items 9/10) -- no re-derivation needed.
        dialog = PowerSwitchDialog(
            existing.owner_paths,
            icon.name,
            domain,
            supply_in_name,
            self._existing_power_switch_names(),
            editing_switch_id=existing.id,
            initial=existing,
            parent=self,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                self.edit_power_switch(
                    existing.id,
                    dialog.name,
                    dialog.input_supply_port,
                    dialog.output_supply_port,
                    dialog.control_port,
                    dialog.cell_type,
                    dialog.on_state,
                    dialog.off_state,
                    control_signal=dialog.control_signal,
                    ack_port=dialog.ack_port,
                    ack_signal=dialog.ack_signal,
                )
            except ValueError as exc:
                message_box.warning(self, "Edit Power Switch", str(exc))

    def edit_power_switch(
        self,
        switch_id: str,
        name: str,
        input_supply_port: str,
        output_supply_port: str,
        control_port: str,
        cell_type: Optional[str],
        on_state: str,
        off_state: str,
        # "Control Signal" -- the RTL net driving the control pin. Last
        # and defaulted so every existing positional caller is unaffected,
        # and because it is genuinely optional at the model level: a
        # switch IMPORTED from a file that wrote a bare `-control_port
        # CTRL` has no net to record. The dialog itself requires one.
        control_signal: str = "",
        # The acknowledge pair, defaulted for the same reason
        # control_signal is: genuinely optional, and every existing
        # positional caller predates it.
        ack_port: str = "",
        ack_signal: str = "",
    ) -> PowerSwitchPlacement:
        """The directly-testable core of "Edit" -- same bypass-the-dialog
        reasoning as edit_retention_strategy(). `target_ids`/`owner_paths`/
        `power_domain_id` never change here -- editing only ever touches
        the fields the dialog actually exposes, same precedent
        edit_retention_strategy() already follows for ITS own
        `power_domain_id`/`owner_path`. Item 9: "editing from any ONE
        replicated instance updates all of them" falls out for free here
        -- there is only ever ONE shared PowerSwitchPlacement regardless
        of how many targets it spans, so updating it updates every
        instance at once; the loop below just keeps every one of its
        OWN materialized icons (build_power_switch_overlay()'s own list)
        in sync with the SAME new placement object."""
        if not validate_no_spaces(name):
            raise ValueError(NO_SPACES_ERROR)
        power_switch_before = self._snapshot_power_switch_state()
        current = self._power_switch_state.switches[switch_id]
        placement = dataclasses_replace(
            current,
            name=name,
            input_supply_port=input_supply_port,
            output_supply_port=output_supply_port,
            control_port=control_port,
            control_signal=control_signal,
            ack_port=ack_port,
            ack_signal=ack_signal,
            cell_type=cell_type,
            on_state=on_state,
            off_state=off_state,
        )
        self._power_switch_state.switches[switch_id] = placement
        for switch_icon in self._power_switch_icons.get(switch_id, []):
            switch_icon.placement = placement
            switch_icon.set_name(name)  # keeps the diagram's own name label (item 7) in sync with a rename, on EVERY replicated instance
        self._commit_power_switch_undo_step("Edit Power Switch", power_switch_before)
        return placement

    def _prompt_delete_power_switch(self, switch: PowerSwitchPlacement) -> None:
        """Explicit request: Power Switch deletion gets its OWN
        confirmation prompt -- a deliberate exception to this diagram's
        otherwise-uniform "delete immediately, Ctrl+Z is the safety net"
        convention (Power Domain/Retention Strategy still delete without
        asking, unchanged; Supply Port/Internal Supply now ALSO asks,
        but only when deleting it has real cascade impact -- see
        _prompt_delete_supply_port()'s own docstring -- so this is no
        longer a Power-Switch-only exception, just the one case that
        unconditionally always asks). Yes/No, defaulting to
        No (the safer default for a destructive-feeling confirmation,
        unlike _confirm_merge_duplicate_retention()'s own Yes-default --
        that one offers the generally-preferred outcome; this one asks
        before removing something outright). Still fully undoable via
        Ctrl+Z regardless of the answer -- the prompt is an extra "are
        you sure," not a claim that this is somehow permanent."""
        reply = message_box.question(
            self,
            "Delete Power Switch",
            f"Delete Power Switch '{switch.name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.delete_power_switch(switch.id)

    def delete_power_switch(self, switch_id: str) -> None:
        """The directly-testable core of "Delete" -- bypasses the
        confirmation prompt (same "dialog/prompt is UI-layer only, this
        stays the real core" precedent every other placement tool here
        follows) -- removes the switch, its icon, and its own automatic
        bottom connector entirely (see _remove_power_switch_placement()).
        Also reflows the layout (_reflow_layout()) -- the room reserved
        for this switch's own icon is no longer needed, same "layout
        stays correct after ANY switch-state change, not just Add"
        completeness the room-reservation fix itself is built on."""
        power_switch_before = self._snapshot_power_switch_state()
        self._remove_power_switch_placement(switch_id)
        self._reflow_layout()
        self._commit_power_switch_undo_step("Delete Power Switch", power_switch_before)


    def _remove_power_switch_placement(self, switch_id: str) -> None:
        """Full removal of a Power Switch by id -- state, EVERY one of
        its own automatic bottom connectors, and EVERY one of its own
        live icons (item 9: a multi-hierarchy switch has one of each per
        target it spans -- "deleting it from any instance removes it
        from all," explicit request, falls out for free here since this
        always removes the WHOLE shared record regardless of which
        target triggered it). Shared by delete_power_switch() (the
        right-click "Delete" action) and delete_supply_port() (cascade:
        deleting a Supply Port/Internal Supply that has a switch
        attached must also remove the now-orphaned switch -- including
        every OTHER replicated instance, since removing even one of a
        multi-hierarchy switch's own targets invalidates the whole
        shared entity, same "cascades fully" precedent
        remove_power_domain() already establishes for its own dependent
        Retention Strategies) -- one place that knows how to fully
        remove a switch, rather than two independently-maintained copies
        of the same connector/icon/state cleanup. Deliberately does NOT
        commit its own undo step -- both callers commit it themselves,
        atomically alongside whatever else changed in the same
        operation."""
        for connector in self._power_switch_connectors.pop(switch_id, []):
            a, b = connector.port_a, connector.port_b
            if connector in a.connectors:
                a.connectors.remove(connector)
            if connector in b.connectors:
                b.connectors.remove(connector)
            # Same dangling-callback hazard as this loop's own icon
            # cleanup below (on_move_callbacks/on_recolor_callbacks) --
            # this connector's own _sync_pen() (supply_port_overlay.py)
            # is registered onto whichever endpoint(s) expose
            # on_recolor_callbacks (b/target_icon here, always; a/the
            # switch icon, never -- see that class's own docstring).
            for endpoint in (a, b):
                callbacks = getattr(endpoint, "on_recolor_callbacks", None)
                if callbacks is not None and connector._sync_pen in callbacks:
                    callbacks.remove(connector._sync_pen)
            if connector.scene() is not None:
                connector.scene().removeItem(connector)
        for icon in self._power_switch_icons.pop(switch_id, []):
            # Real, confirmed hazard (same shiboken class this codebase
            # has hit before): PowerSwitchIconItem is scene-level, not
            # parented to its target (see its own docstring) -- the
            # target's own on_move_callbacks/on_recolor_callbacks would
            # otherwise keep calling this icon's update_position()/
            # sync_color() after it's removed from the scene, on the
            # very next time the target moves/recolors, crashing on the
            # now-deleted C++ object.
            if icon.update_position in icon.target_icon.on_move_callbacks:
                icon.target_icon.on_move_callbacks.remove(icon.update_position)
            if icon.sync_color in icon.target_icon.on_recolor_callbacks:
                icon.target_icon.on_recolor_callbacks.remove(icon.sync_color)
            if icon.scene() is not None:
                icon.scene().removeItem(icon)
        self._power_switch_state.switches.pop(switch_id, None)

    def _prompt_edit_supply_port(self, icon: SupplyPortIconItem) -> None:
        is_internal_supply = icon.placement.is_internal_supply
        is_virtual_supply = icon.placement.is_virtual_supply
        inherited_state_lines = None
        if is_internal_supply:
            inherited_state_lines = describe_inherited_states(icon.placement, self._shared_state, self._power_switch_state.switches)
        dialog = AddPowerSupplyDialog(
            self,
            is_internal_supply=is_internal_supply,
            is_virtual_supply=is_virtual_supply,
            initial_states=list(icon.placement.states),
            inherited_state_lines=inherited_state_lines,
            initial_direction=icon.placement.direction,
        )
        if is_internal_supply:
            title = "Edit Internal Supply"
        elif is_virtual_supply:
            title = "Edit Virtual Supply"
        else:
            title = "Edit Power Supply"
        dialog.setWindowTitle(title)
        dialog.name_edit.setText(icon.name)
        dialog.color = QColor(icon.color)
        dialog._refresh_color_button()
        while dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                self.edit_supply_port(icon, dialog.name, dialog.color, states=dialog.states, direction=dialog.direction)
                return
            except ValueError as exc:
                message_box.warning(self, title, str(exc))

    def _corresponding_supply_port_replicas(self, icon: SupplyPortIconItem) -> list[SupplyPortPlacement]:
        """Bug fix (multi-hierarchy Supply Port/Internal Supply
        replicas going out of sync on edit): every OTHER
        SupplyPortPlacement that is a legitimate replica of `icon` --
        matched by `icon`'s own (pre-edit) name AND a REAL shared
        PowerDomainPlacement actually spanning both owner_paths
        (_paths_share_a_multi_hierarchy_domain(), the exact same
        "same name across member hierarchies == the same logical
        entity" trace item 9's own _corresponding_target_ids() (Power
        Switch) already established) -- never by matching name alone. These
        are replicas of ONE logical Supply Port/Internal Supply, not
        independent entities that merely share a name, so any field
        genuinely describing the entity itself (Name, Color) must stay
        in sync across every one of them; a field describing this ONE
        instance's own placement (edge/t position on its own box) is
        correctly excluded from this, and stays independent per
        replica."""
        return [
            p
            for p in self._shared_state.supply_port_placements.values()
            if p.id != icon.key and p.name == icon.placement.name and p.owner_path != icon.placement.owner_path
            and self._paths_share_a_multi_hierarchy_domain(icon.placement.owner_path, p.owner_path)
        ]

    def _supply_port_replica_group(self, icon: SupplyPortIconItem) -> list[SupplyPortIconItem]:
        """Bug fix: the group-highlight set for `icon` -- itself plus
        every OTHER legitimate multi-hierarchy replica
        (_corresponding_supply_port_replicas(), the SAME real-shared-
        PowerDomainPlacement trace already used for items 11/12's
        naming uniqueness and the Name/Color sync fix), resolved to
        their own LIVE icons. Used by _on_scene_selection_changed() so
        clicking any one instance highlights all of them together, the
        same "click any one spanned icon, highlight all of them
        together" mechanism Retention/Power Switch already establish
        -- this was the missing piece for Supply Port/Internal Supply:
        the underlying replica-tracing already existed (built for the
        Name/Color sync fix), the click handler just never asked it. A
        replica whose own hierarchy is currently hidden has no live
        icon to highlight -- silently skipped, same "not materialized,
        not discarded" precedent every other multi-hierarchy overlay
        here follows. A single-hierarchy (non-replicated) icon
        correctly gets a group of exactly one -- itself, unchanged
        from the prior behavior."""
        group = [icon]
        for replica in self._corresponding_supply_port_replicas(icon):
            replica_icon = self._supply_port_icons.get(replica.id)
            if replica_icon is not None:
                group.append(replica_icon)
        return group

    def edit_supply_port(
        self,
        icon: SupplyPortIconItem,
        name: str,
        color: QColor,
        states: Optional[list[SupplyPortState]] = None,
        direction: Optional[str] = None,
    ) -> None:
        """The directly-testable core of "Edit". Not offered for a fixed
        (macro PG-pin) icon at all -- see _on_context_menu().

        Bug fix: Name and Color are both propagated to every OTHER
        legitimate multi-hierarchy replica of `icon`
        (_corresponding_supply_port_replicas()) -- previously ONLY
        `icon`'s own placement/live icon were updated, silently
        leaving every sibling replica (on another member hierarchy of
        the same multi-hierarchy Power Domain) with the stale name/
        colour, going out of sync with the "same logical entity" it's
        meant to represent (confirmed empirically: this affected BOTH
        fields already, not Color alone). Each replica's own live icon
        (if currently materialized) is updated the same way `icon`
        itself is -- label text + recolor() -- so the diagram itself,
        not just the underlying placement, reflects the change on
        every member hierarchy. `states` (when given, i.e. not None)
        propagates the exact same way -- a Supply Port's own power
        states describe the logical entity itself, same as Name/Color,
        not this one instance's own placement. Always None/ignored for
        an Internal Supply (no independent states field to overwrite --
        see SupplyPortPlacement.states's own docstring).

        Renaming propagates to every Supply Set that references this
        port by name (see _propagate_supply_port_rename()) -- run for
        `icon`'s own owner_path AND every replica's own owner_path, so
        a multi-hierarchy Supply Set stays correctly in sync regardless
        of which one member hierarchy's own instance triggered the
        edit. Committed together with the port's own state (and every
        replica's) via _commit_combined_undo_step(), so a single
        Ctrl+Z undoes the whole propagated change atomically.

        Section 3 (explicit request): a Virtual Supply's own Name
        uniqueness IS re-checked here on rename (the one deliberate
        exception to CHANGE 1's "no name-collision check" rule) --
        `icon`'s own current id is excluded so keeping the name
        unchanged is never flagged as colliding with itself. Never
        checked for a plain Supply Port/Internal Supply rename (CHANGE
        1 stays fully in effect for those two)."""
        if not validate_no_spaces(name):
            raise ValueError(NO_SPACES_ERROR)
        if icon.placement.is_virtual_supply and self._virtual_supply_name_conflict(name, exclude_id=icon.key):
            raise ValueError(self._VIRTUAL_SUPPLY_NAME_CONFLICT_ERROR_TEMPLATE.format(name=name))
        if icon.placement.is_virtual_supply and states is not None and not states:
            raise ValueError(self._VIRTUAL_SUPPLY_NO_STATES_ERROR)
        before = self._snapshot_state()
        domain_before = self._snapshot_domain_state()
        old_name = icon.placement.name
        replicas = self._corresponding_supply_port_replicas(icon)
        owner_paths = {icon.owner_box.path, *(r.owner_path for r in replicas)}

        icon.placement.name = name
        icon.label.setText(name)
        icon.recolor(color)
        if states is not None and not icon.placement.is_internal_supply:
            icon.placement.states = list(states)
        # Direction describes the logical ENTITY, same as Name/Color/
        # states, so it propagates to every replica too -- and only ever
        # applies to a plain Supply Port (an Internal or Virtual Supply
        # has no such field; its dialog does not offer one, and
        # dialog.direction returns "" for it, which must not be written
        # over anything). `None` means "this caller is not editing
        # direction", distinct from "" meaning "the user chose <empty>".
        edits_direction = (
            direction is not None
            and not icon.placement.is_internal_supply
            and not icon.placement.is_virtual_supply
        )
        if edits_direction:
            icon.placement.direction = direction
        for replica in replicas:
            replica.name = name
            if states is not None and not replica.is_internal_supply:
                replica.states = [dataclasses_replace(s) for s in states]
            if edits_direction and not replica.is_internal_supply and not replica.is_virtual_supply:
                replica.direction = direction
            replica_icon = self._supply_port_icons.get(replica.id)
            if replica_icon is not None:
                replica_icon.label.setText(name)
                replica_icon.recolor(color)
            else:
                # Not currently materialized (its own hierarchy is
                # hidden right now) -- the placement itself still needs
                # its own colour updated so a later rebuild restores the
                # new colour, not the stale one (same "placement is the
                # source of truth, live icon is a materialization of
                # it" precedent recolor() itself already establishes).
                replica.color = QColor(color)

        if old_name != name:
            for owner_path in owner_paths:
                self._propagate_supply_port_rename(owner_path, old_name, name)
        # A Power Domain's own color follows its Primary Supply Set's
        # Power rail, which may itself name THIS port -- refresh every
        # domain's derived color so a Supply Port recolor (the exact
        # trigger this live binding exists for) propagates immediately.
        self._refresh_domain_derived_colors()
        if old_name != name:
            # Audit fix (category 08): a rename changes two layout
            # inputs and neither was re-resolved here. The label's own
            # rendered width feeds `_extra_interior_min_width()` (the
            # mechanism that exists precisely to stop adjacent labels
            # overlapping on a narrow leaf block), and the new name may
            # gain or lose a Ground role that `_propagate_supply_port_rename()`
            # just carried across the referencing Supply Sets. Gated on
            # an actual rename so a pure recolor stays as cheap as it
            # was.
            self._reclassify_ground_supply_edges()
            self._reflow_layout()
        self._commit_combined_undo_step("Edit Power Supply", before, domain_before)

    def _propagate_supply_port_rename(self, owner_path: str, old_name: str, new_name: str) -> None:
        """Explicit request: renaming a Supply Port must propagate to
        every Supply Set that references it by name
        (SupplySetPlacement.power/ground/n_well/p_well -- the one place
        in this diagram-only model that references a Supply Port by
        name rather than by id, see that class's own docstring). Scoped
        to Supply Sets whose own `owner_paths` includes `owner_path` --
        bug fix: previously checked only the singular backward-compat
        `owner_path` ("primary" member), which silently skipped a
        multi-hierarchy Supply Set entirely whenever the propagating
        edit originated on one of its own NON-primary member
        hierarchies (see SupplySetPlacement's own docstring: `.power`/
        `.ground`/etc. are ONE shared name string regardless of which
        member hierarchy, so any one member's own owner_path must be
        able to find and update it) -- same scope
        known_supply_names_for_owner() already draws a Supply Set's own
        field values from at compose time (see
        create_power_domain_dialog.py's own module docstring), so a
        same-named port on a hierarchy with no real domain-membership
        relationship is still never affected.

        Power Domain and Retention Strategy references are structurally
        unaffected by a port rename -- both resolve down to a Supply Set
        by id (PowerDomainPlacement.primary_supply_set/
        secondary_supply_sets, RetentionStrategyPlacement.
        primary_supply_set_id/retention_supply_set_id), never a raw port
        name, and every place that displays a Supply Set's own name (the
        Properties panel, the Retention dialog/Info view) resolves it
        fresh from the live dict at render time rather than caching a
        copy -- verified empirically by reading every such call site,
        not assumed; see this session's own regression tests for a Power
        Domain rename covering the same claim for THAT entity."""
        for supply_set in self._domain_state.supply_sets.values():
            if owner_path not in supply_set.owner_paths:
                continue
            if supply_set.power == old_name:
                supply_set.power = new_name
            if supply_set.ground == old_name:
                supply_set.ground = new_name
            if supply_set.n_well == old_name:
                supply_set.n_well = new_name
            if supply_set.p_well == old_name:
                supply_set.p_well = new_name

    def _cascade_targets_for_supply_port_delete(
        self, icon: SupplyPortIconItem
    ) -> tuple[
        list[SupplySetPlacement],
        list[PowerDomainPlacement],
        list[RetentionStrategyPlacement],
        list[PowerSwitchPlacement],
        list[IsolationStrategyPlacement],
        list[LevelShifterStrategyPlacement],
    ]:
        """Resolves everything that would be swept away if `icon` were
        deleted, beyond the port itself -- explicit request. Same
        owner_path-scoped name match _propagate_supply_port_rename()
        already uses to find which Supply Sets reference a given port
        (a name alone isn't unique diagram-wide, only within one
        owner_path's own scope -- see SupplySetPlacement's own
        docstring). Every Power Domain that has one of those affected
        Supply Sets as its own Primary OR Secondary is itself affected
        (confirmed via explicit follow-up: losing ANY Supply Set makes
        the domain a candidate for full removal here, uniformly --
        no different treatment for Primary vs. Secondary, unlike Edit
        Power Domain's own softer per-field handling) -- reused, not
        reinvented, for delete_supply_port_with_cascade()'s own removal
        below via the exact same _remove_domain_cascade() "Remove Power
        Domain" itself uses. Retention Strategies/Power Switches/
        Isolation Cells listed here are exactly the ones
        _remove_domain_cascade() would delete for each affected domain
        (by `power_domain_id`, not by directly re-checking their own
        Supply Set references) -- matching what will ACTUALLY happen
        once confirmed, not a narrower, direct-reference-only subset."""
        affected_sets = [
            s
            for s in self._domain_state.supply_sets.values()
            if icon.owner_box.path in s.owner_paths and icon.name in (s.power, s.ground, s.n_well, s.p_well)
        ]
        affected_set_ids = {s.id for s in affected_sets}
        affected_domains = [
            d
            for d in self._domain_state.domains.values()
            if d.primary_supply_set in affected_set_ids or any(sid in affected_set_ids for sid in d.secondary_supply_sets)
        ]
        affected_domain_ids = {d.id for d in affected_domains}
        affected_retention = [r for r in self._retention_state.strategies.values() if r.power_domain_id in affected_domain_ids]
        affected_switches = [sw for sw in self._power_switch_state.switches.values() if sw.power_domain_id in affected_domain_ids]
        affected_isolation = [s for s in self._isolation_state.strategies.values() if s.power_domain_id in affected_domain_ids]
        affected_level_shifter = [s for s in self._level_shifter_state.strategies.values() if s.power_domain_id in affected_domain_ids]
        return affected_sets, affected_domains, affected_retention, affected_switches, affected_isolation, affected_level_shifter

    def _prompt_delete_supply_port(self, icon: SupplyPortIconItem) -> None:
        """The UI-facing entry point for right-click "Delete" on a
        Supply Port/Internal Supply -- explicit request. When deleting
        `icon` would ALSO sweep away Supply Sets/Power Domains/Retention
        Strategies/Power Switches/Isolation Cells (see
        _cascade_targets_for_supply_port_delete()), shows a detailed
        Yes/No confirmation listing every affected entity by name,
        grouped by kind, before doing anything -- same
        message_box.question()/default-No convention
        _prompt_delete_power_switch() already establishes for a
        destructive-feeling confirmation in this diagram. When there's
        NO cascade impact (the common case -- most Supply Ports are
        never actually assigned to a Supply Set), deletes immediately
        with no prompt at all, unchanged from this diagram's own
        otherwise-uniform "delete immediately, Ctrl+Z is the safety net"
        convention -- an empty "nothing else will be affected, are you
        sure?" dialog would be pure friction, not a real warning."""
        affected_sets, affected_domains, affected_retention, affected_switches, affected_isolation, affected_level_shifter = self._cascade_targets_for_supply_port_delete(icon)
        if not affected_sets:
            self.delete_supply_port(icon)
            return
        lines = [f"Deleting '{icon.name}' will also remove:", "", f"Supply Sets: {', '.join(sorted(s.name for s in affected_sets))}"]
        if affected_domains:
            lines.append(f"Power Domains: {', '.join(sorted(d.name for d in affected_domains))}")
        if affected_retention:
            lines.append(f"Retention Strategies: {', '.join(sorted(r.name for r in affected_retention))}")
        if affected_switches:
            lines.append(f"Power Switches: {', '.join(sorted(sw.name for sw in affected_switches))}")
        if affected_isolation:
            lines.append(f"Isolation Cells: {', '.join(sorted(s.name for s in affected_isolation))}")
        if affected_level_shifter:
            lines.append(f"Level Shifters: {', '.join(sorted(s.name for s in affected_level_shifter))}")
        reply = message_box.question(
            self,
            "Delete Power Supply",
            "\n".join(lines),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.delete_supply_port_with_cascade(icon, {s.id for s in affected_sets}, {d.id for d in affected_domains})

    def delete_supply_port_with_cascade(self, icon: SupplyPortIconItem, affected_supply_set_ids: set[str], affected_domain_ids: set[str]) -> None:
        """The directly-testable, cascade-aware core invoked once
        _prompt_delete_supply_port() confirms there IS real impact
        beyond the port itself -- bypasses the confirmation prompt, same
        "prompt is UI-layer only, this stays the real core" precedent
        every other placement tool here follows.

        Every affected Power Domain is removed via the exact same
        _remove_domain_cascade() "Remove Power Domain" itself uses
        (its own Supply Sets/Ports, Retention Strategies, Power
        Switches, and Isolation Cells, all together) -- reused, not
        reimplemented, per explicit request. `affected_supply_set_ids`
        is then swept directly too (defensive: covers a Supply Set that
        references this port but, unusually, isn't actually anyone's
        Primary/Secondary -- e.g. one abandoned before this cascade ever
        ran -- so isn't touched by any domain's own cascade above; a
        no-op `.pop(..., None)` for every id the domain loop already
        removed). The port itself, and any switch attached to it
        DIRECTLY as its own Supply Out (a different relationship than
        "belongs to an affected domain" -- find_switch_for_target()),
        are removed last, reusing delete_supply_port()'s own two
        removal primitives directly rather than calling
        delete_supply_port() itself (which pushes its own separate undo
        step -- this cascade must land as ONE atomic Ctrl+Z instead, via
        _commit_remove_domain_undo_step() across all five shared
        states).

        CHANGE 2 (explicit request, ancestor Supply Set sharing): same
        "pop every affected domain as one batch FIRST" precedent
        delete_supply_set_with_cascade() establishes -- two of
        `affected_domain_ids` could share an UNRELATED Supply Set
        (not `icon`'s own) as their common Primary/Secondary; batching
        the pop first keeps each domain's own
        `_supply_set_ids_referenced_elsewhere()` check inside
        _remove_domain_cascade() from seeing a not-yet-processed
        sibling domain in this SAME bulk deletion as a stale survivor."""
        supply_before = self._snapshot_state()
        domain_before = self._snapshot_domain_state()
        retention_before = self._snapshot_retention_state()
        power_switch_before = self._snapshot_power_switch_state()
        isolation_before = self._snapshot_isolation_state()
        level_shifter_before = self._snapshot_level_shifter_state()

        affected_domains = [self._domain_state.domains[did] for did in affected_domain_ids if did in self._domain_state.domains]
        for domain in affected_domains:
            self._domain_state.domains.pop(domain.id, None)
        for domain in affected_domains:
            self._remove_domain_cascade(domain)
        for supply_set_id in affected_supply_set_ids:
            self._domain_state.supply_sets.pop(supply_set_id, None)

        direct_switch = find_switch_for_target(self._power_switch_state.switches, icon.key)
        if direct_switch is not None:
            self._remove_power_switch_placement(direct_switch.id)
        self._remove_supply_port_placement(icon.key)

        # Audit fix (category 08): the plain `delete_supply_port()`
        # sibling below already reflowed; this cascade path -- which
        # removes strictly MORE (the port, its Supply Sets, and any
        # dependent Power Domains) and therefore frees strictly more
        # reserved room -- reflowed not at all. Same delete-side Ground
        # reclassification as the Supply Set cascade above, for the
        # Supply Sets this path removes.
        self._reclassify_ground_supply_edges()
        self._reflow_layout()
        self._commit_remove_domain_undo_step(
            "Delete Power Supply", supply_before, domain_before, retention_before, power_switch_before, isolation_before, level_shifter_before
        )

    def delete_supply_port(self, icon: SupplyPortIconItem) -> None:
        """The directly-testable core of "Delete". Not offered for a fixed
        (macro PG-pin) icon at all -- see _on_context_menu().

        A Power Switch attached to this port (its own "Supply Out") is
        now orphaned the moment the port itself is gone -- cascaded away
        FIRST (find_switch_for_target()), via the exact same
        _remove_power_switch_placement() the right-click "Delete Power
        Switch" action uses, same "cascades fully" precedent
        remove_power_domain() already establishes for its own dependent
        Retention Strategies. Both states -- SupplyPortDiagramState AND
        (when a switch was actually cascaded away) PowerSwitchDiagramState
        -- are snapshotted before/after and committed TOGETHER, so a
        single Ctrl+Z restores both the port and its own switch
        atomically. Deliberately NOT plumbed into the shared
        _remove_supply_port_placement() helper itself -- see that
        method's own docstring for why (every OTHER caller of that
        helper would also need updating to snapshot/commit
        PowerSwitchDiagramState, a real ripple not requested by the
        Power Switch spec; this is the one, most direct deletion path
        that needed it)."""
        supply_before = self._snapshot_state()
        power_switch_before = self._snapshot_power_switch_state()
        switch = find_switch_for_target(self._power_switch_state.switches, icon.key)
        if switch is not None:
            self._remove_power_switch_placement(switch.id)
        self._remove_supply_port_placement(icon.key)
        self._reflow_layout()  # item 3: the room reserved for this port is no longer needed
        entries = []
        supply_after = self._snapshot_state()
        if supply_before != supply_after:
            entries.append((self._shared_state, supply_before, supply_after, restore_supply_port_diagram_state))
        power_switch_after = self._snapshot_power_switch_state()
        if power_switch_before != power_switch_after:
            entries.append((self._power_switch_state, power_switch_before, power_switch_after, restore_power_switch_diagram_state))
        if not entries:
            return
        self._undo_stack.push(_DiagramStateCommand(entries, self._refresh_current, self._notify_changed, "Delete Power Supply"))
        self._notify_changed()

    def _remove_supply_port_placement(self, port_id: str) -> None:
        """Full removal of a user-placed supply port by id -- state,
        connectors, and its live icon (if currently materialized). Shared
        by delete_supply_port() (the right-click "Delete" action) and
        _cascade_remove_orphaned_supply_ports() (Edit Power Domain's own
        cascade cleanup, item 1) -- one place that knows how to fully
        remove a supply port, rather than two independently-maintained
        copies of the same connector/icon/state cleanup. Never used for a
        PG pin -- those are derived from the macro's own Definition, not
        user-removable (see build_supply_port_overlay()'s own docstring);
        neither caller here ever passes one.

        Removes every connector attached to it first (a connector left
        pointing at a just-destroyed icon would crash the next
        update_route()/rebuild) -- via the live icon's own `.connectors`
        when materialized, or by scrubbing
        `supply_connector_placements` directly when it isn't (e.g. the
        owner box is currently hidden -- the cascade path can still run
        against a domain whose box isn't in `self._boxes` right now).

        Does NOT itself cascade-remove an attached Power Switch -- see
        delete_supply_port()'s own docstring for why that cascade lives
        at the ONE call site that needed it instead of here."""
        icon = self._supply_port_icons.get(port_id)
        if icon is not None:
            for connector in list(icon.connectors):
                self._remove_connector(connector)
            if icon.resnap_to_owner_edge in icon.owner_box.on_resize_callbacks:
                icon.owner_box.on_resize_callbacks.remove(icon.resnap_to_owner_edge)
            # Audit fix (category 10): a SupplyPortIconItem registers on
            # BOTH of its owner box's callback lists (on_resize for
            # resnap_to_owner_edge, on_move for _on_owner_box_moved) but
            # only the resize one was ever detached here. The owner box
            # is never destroyed by deleting a port, so the surviving
            # on_move entry kept firing on every later box move against
            # an icon already removed from the scene.
            if icon._on_owner_box_moved in icon.owner_box.on_move_callbacks:
                icon.owner_box.on_move_callbacks.remove(icon._on_owner_box_moved)
            if icon.scene() is not None:
                icon.scene().removeItem(icon)
            self._supply_port_icons.pop(port_id, None)
        else:
            self._shared_state.supply_connector_placements = [
                pair for pair in self._shared_state.supply_connector_placements if port_id not in pair
            ]
        self._shared_state.supply_port_placements.pop(port_id, None)

    def delete_connector(self, connector: SupplyPortConnectorItem) -> None:
        """The directly-testable core of the connector's own right-click
        "Delete" -- explicit request. Unlike delete_supply_port(), this
        one has no "not applicable to macros" restriction: a connector
        touching a PG pin is exactly as deletable as one between two
        user-placed supply ports (the pin itself isn't removed, just
        disconnected -- see _remove_connector()'s own PG-pin-recolor
        note).

        Bug fix, since generalized (explicit request): if `connector`
        touches a multi-hierarchy replication group -- Supply Port/
        Internal Supply or Power Switch alike -- every OTHER member
        hierarchy's own equivalent, auto-created connection is removed
        together with it -- see
        _remove_replicated_manual_connectors()'s own docstring. Read
        BEFORE the original connector itself is removed (still needs
        `connector.port_a`/`.port_b` intact), committed together in the
        SAME undo step, so a single Ctrl+Z restores the whole
        replicated change atomically."""
        before = self._snapshot_state()
        self._remove_replicated_manual_connectors(connector)
        self._remove_connector(connector)
        self._commit_undo_step("Delete Power Net", before)

    def _remove_connector(self, connector: SupplyPortConnectorItem) -> None:
        a, b = connector.port_a, connector.port_b
        if connector in a.connectors:
            a.connectors.remove(connector)
        if connector in b.connectors:
            b.connectors.remove(connector)
        # Same dangling-callback hazard PowerSwitchIconItem's own removal
        # cleanup already establishes the precedent for (see
        # _remove_power_switch_placement() below) -- this connector's own
        # _sync_pen() (supply_port_overlay.py) is registered onto
        # whichever endpoint(s) actually expose on_recolor_callbacks;
        # left registered, a LATER recolour of a still-live endpoint
        # would keep calling back into this now-scene-less/about-to-be-
        # deleted connector.
        for endpoint in (a, b):
            callbacks = getattr(endpoint, "on_recolor_callbacks", None)
            if callbacks is not None and connector._sync_pen in callbacks:
                callbacks.remove(connector._sync_pen)
        if connector.scene() is not None:
            connector.scene().removeItem(connector)
        if connector in self._supply_connectors:
            self._supply_connectors.remove(connector)
        for pair in ((a.key, b.key), (b.key, a.key)):
            if pair in self._shared_state.supply_connector_placements:
                self._shared_state.supply_connector_placements.remove(pair)
            self._shared_state.manual_connector_offsets.pop(pair, None)
        # A PG pin with no connections left has nothing to take its colour
        # from anymore -- back to "not connected to anything", matching
        # SupplyPortIconItem.recolor()'s own "colour = whatever it's wired
        # to" contract instead of leaving a stale colour behind.
        if a.fixed and not a.connectors:
            a.recolor(_PG_PIN_DEFAULT_COLOR)
        if b.fixed and not b.connectors:
            b.recolor(_PG_PIN_DEFAULT_COLOR)

    def _connector_handle_at(self, pos) -> Optional[ConnectorHandleItem]:
        """Deliberately self.items(pos) (every item under `pos`, already
        stacking-order-sorted top-to-bottom), not self.itemAt(pos) (only
        the single topmost) -- a real bug, found via a real QTest press/
        move/release drive of the actual gesture, not just unit-testing
        the extracted logic: a handle is nested several levels deep
        (icon -> box -> ... -> root box), while the in-flight
        _connector_drag_line is a bare top-level scene item. Per Qt's own
        documented stacking rule, "a child is always stacked on top of
        its own parent, regardless of Z value" -- Z only ranks true
        siblings -- so itemAt()'s comparison happens at the *top* level
        (drag line vs. the handle's root-box ancestor), where the line's
        Z_SUPPLY_PORT_CONNECTOR beats the root box's own low Z, hiding
        every deeply-nested handle underneath it from itemAt() entirely,
        regardless of the handle's own much higher *local* Z. Walking the
        full stack and picking the first real handle sidesteps that
        mismatch instead of trying to out-Z it (which isn't possible
        here -- the same Z that makes the line visible in front of a
        box's plain white fill is exactly what makes it incorrectly beat
        deeply-nested handles too)."""
        for item in self.items(pos):
            if isinstance(item, ConnectorHandleItem):
                return item
        return None

    def _retention_badge_at(self, pos) -> Optional[RetentionBadgeIconItem]:
        """Same self.items(pos) reasoning as _connector_handle_at()/
        _supply_port_icon_at() -- walks the full stack rather than
        trusting itemAt(), the same real hit-testing bug class already
        found and fixed for those. Each badge belongs to exactly one
        strategy (see RetentionBadgeIconItem's own docstring on the
        "multiple stacked icons" decision), so right-clicking a specific
        icon acts on that strategy alone -- no "walk up to the owning
        type" needed here (unlike _supply_port_icon_at(), a badge has no
        child items of its own to land on first)."""
        for item in self.items(pos):
            if isinstance(item, RetentionBadgeIconItem):
                return item
        return None

    def _isolation_badge_at(self, pos) -> Optional[IsolationBadgeIconItem]:
        """Same self.items(pos)/"one badge, one strategy" reasoning as
        _retention_badge_at() above."""
        for item in self.items(pos):
            if isinstance(item, IsolationBadgeIconItem):
                return item
        return None

    def _level_shifter_badge_at(self, pos) -> Optional[LevelShifterBadgeIconItem]:
        """Same self.items(pos)/"one badge, one strategy" reasoning as
        _isolation_badge_at() above."""
        for item in self.items(pos):
            if isinstance(item, LevelShifterBadgeIconItem):
                return item
        return None

    def _supply_port_icon_at(self, pos) -> Optional[SupplyPortIconItem]:
        """Same self.items(pos) reasoning as _connector_handle_at() --
        walks up from the first SupplyPortIconItem-or-descendant found
        (its label or a handle) to the owning icon, same "walk up to the
        type that matters" pattern as the base class's own _box_at().
        Used both to decide whether a left-press should hand off to Qt's
        own item-drag machinery (mousePressEvent()) and to find the icon
        a right-click should show the Edit/Delete menu for
        (_on_context_menu())."""
        for item in self.items(pos):
            candidate = item
            while candidate is not None and not isinstance(candidate, SupplyPortIconItem):
                candidate = candidate.parentItem()
            if candidate is not None:
                return candidate
        return None

    def _power_switch_icon_at(self, pos) -> Optional[PowerSwitchIconItem]:
        """Same self.items(pos)/"one badge, one strategy" reasoning as
        _retention_badge_at() -- a PowerSwitchIconItem has no children of
        its own to land on first (unlike SupplyPortIconItem's label/
        handles), so no "walk up" is needed either. Added for the
        hover-delay info tooltip (explicit request); also now used by
        _on_context_menu() for the switch icon's OWN plain "Edit"/
        "Delete" right-click menu (explicit follow-up request) -- a
        second entry point alongside the target Supply Port's own
        "Edit Power Switch"/"Delete Power Switch" menu entries
        (_supply_port_context_menu_actions()), both funneling into the
        identical _prompt_edit_power_switch()/_prompt_delete_power_switch()
        flow."""
        for item in self.items(pos):
            if isinstance(item, PowerSwitchIconItem):
                return item
        return None

    # --- Hover-delay info tooltip (explicit request) ------------------------
    # See hover_tooltip.py's own module docstring for the full design.
    # Deliberately mode-independent -- called unconditionally from the very
    # top of mouseMoveEvent(), the same "runs on every move regardless of
    # mode" precedent every hover-HIGHLIGHT update already follows there,
    # so it works identically in Regular Cursor/Pan/Zoom Lens/every
    # placement tool, and never gates/consumes the event itself (every
    # method below is a pure side-effect call with no `event.accept()`/
    # early return of its own).

    def _hover_tooltip_target_at(self, pos):
        """Which single element (if any) `pos` should show a tooltip
        for, in PRIORITY order -- an icon always wins over the hierarchy
        block underneath it (mirrors _on_context_menu()'s own "checked
        before... the box's own menu below" ordering). A FIXED (macro
        PG-pin) Supply Port icon is included too (explicit follow-up
        request -- originally excluded, since the spec's own field list
        used to only cover Supply Port/Internal Supply/Virtual Supply;
        _hover_tooltip_text_for() now branches on `.fixed` to pick the
        right field set for either case)."""
        icon = self._supply_port_icon_at(pos)
        if icon is not None:
            return icon
        switch_icon = self._power_switch_icon_at(pos)
        if switch_icon is not None:
            return switch_icon
        retention_icon = self._retention_badge_at(pos)
        if retention_icon is not None:
            return retention_icon
        isolation_icon = self._isolation_badge_at(pos)
        if isolation_icon is not None:
            return isolation_icon
        level_shifter_icon = self._level_shifter_badge_at(pos)
        if level_shifter_icon is not None:
            return level_shifter_icon
        box = self._box_at(pos)
        if box is not None:
            return box
        return None

    def _hover_tooltip_text_for(self, target) -> Optional[str]:
        """Resolves `target` (whatever _hover_tooltip_target_at() found)
        into its own fixed field list via hover_tooltip.py's own
        resolve_*_fields(), then renders it. Returns None for a strategy
        badge whose own underlying record has since been deleted out
        from under a still-live icon (a real, if narrow, timing case --
        e.g. the delay elapses in the same tick a Delete just ran) rather
        than crashing or showing stale data."""
        if isinstance(target, InstanceBoxItem):
            fields = resolve_hierarchy_block_fields(target, self._domain_state)
        elif isinstance(target, PowerSwitchIconItem):
            supply_in = self._resolve_power_switch_supply_in_name(target)
            supply_out = target.target_icon.name if target.target_icon is not None else None
            fields = resolve_power_switch_fields(target.placement, self._domain_state, supply_in, supply_out)
        elif isinstance(target, RetentionBadgeIconItem):
            strategy = self._retention_state.strategies.get(target.strategy_id)
            if strategy is None:
                return None
            fields = resolve_retention_fields(strategy, self._domain_state)
        elif isinstance(target, IsolationBadgeIconItem):
            strategy = self._isolation_state.strategies.get(target.strategy_id)
            if strategy is None:
                return None
            fields = resolve_isolation_fields(strategy, self._domain_state)
        elif isinstance(target, LevelShifterBadgeIconItem):
            strategy = self._level_shifter_state.strategies.get(target.strategy_id)
            if strategy is None:
                return None
            fields = resolve_level_shifter_fields(strategy)
        elif isinstance(target, SupplyPortIconItem):
            if target.fixed:
                fields = resolve_pg_pin_fields(target.placement)
            else:
                fields = resolve_supply_port_fields(target.placement, self._shared_state, self._power_switch_state.switches)
        else:
            return None
        return render_tooltip_text(fields)

    def _update_hover_tooltip(self, pos: QPoint) -> None:
        """Called on every mouseMoveEvent -- ANY movement, per the
        spec's own literal "pauses... (no movement)" wording, restarts
        the pending delay UNLESS the tooltip is already visible and the
        target hasn't changed (avoids a hide/re-show flicker while
        sitting still over a large element like a hierarchy block,
        where tiny sub-pixel deltas can still fire a move event)."""
        target = self._hover_tooltip_target_at(pos)
        if target is self._hover_tooltip_target and self._hover_tooltip_widget.isVisible():
            return
        self._hover_tooltip_widget.hide()
        self._hover_tooltip_timer.stop()
        self._hover_tooltip_target = target
        if target is not None:
            self._hover_tooltip_global_pos = self.viewport().mapToGlobal(pos)
            self._hover_tooltip_timer.start()

    def _show_hover_tooltip(self) -> None:
        if self._hover_tooltip_target is None or self._hover_tooltip_global_pos is None:
            return
        text = self._hover_tooltip_text_for(self._hover_tooltip_target)
        if text is None:
            return
        self._hover_tooltip_widget.show_at(self._hover_tooltip_global_pos, text)

    def _hide_hover_tooltip(self) -> None:
        """Shared by leaveEvent() (cursor leaves the viewport entirely)
        and tests -- immediate hide + cancel, no fade/delay either way."""
        self._hover_tooltip_widget.hide()
        self._hover_tooltip_timer.stop()
        self._hover_tooltip_target = None

    def leaveEvent(self, event) -> None:
        self._hide_hover_tooltip()
        super().leaveEvent(event)

    def _connector_segment_at(self, scene_pos: QPointF) -> Optional[tuple[SupplyPortConnectorItem, int]]:
        """Which connector (and which of its own *middle* segments --
        see SupplyPortConnectorItem.segment_at()) `scene_pos` is near
        enough to drag, or None. Restored, pre-existing capability --
        deliberately asks each connector's own geometric segment_at()
        directly (iterating self._supply_connectors) rather than going
        through Qt's itemAt()/items() at all -- a connector is a bare
        top-level scene item, exactly the shape that already produced
        one real stacking-order bug for handles (see
        _connector_handle_at()'s own docstring); asking the connector
        itself sidesteps that class of bug entirely instead of relying
        on Qt's stacking order to find the right one."""
        for connector in self._supply_connectors:
            index = connector.segment_at(scene_pos)
            if index is not None:
                return connector, index
        return None

    def _connect_icons(self, source: SupplyPortIconItem, target: SupplyPortIconItem) -> SupplyPortConnectorItem:
        """The low-level "wire two already-validated icons together" step,
        shared by try_connect_supply_ports() (a fresh user connection) and
        _rebuild() (restoring a previously-made one -- nesting was already
        valid then and owner paths don't change across a rebuild, so it's
        not re-validated here). A fixed (PG-pin) endpoint recolors to
        match its real supply-port partner -- see
        SupplyPortIconItem.recolor()'s own docstring. recolor() mutates
        the icon's own `.placement` in place, and that placement object
        *is* the one stored in `_pg_pin_placements` (see
        build_supply_port_overlay()'s docstring) -- so the new colour is
        already persisted for the next rebuild with no separate write-back
        needed here."""
        if target.fixed and not source.fixed:
            target.recolor(source.color)
        elif source.fixed and not target.fixed:
            source.recolor(target.color)

        connector = SupplyPortConnectorItem(source, target)
        manual_offset = self._shared_state.manual_connector_offsets.get((source.key, target.key))
        if manual_offset is not None:
            connector.apply_manual_offset(manual_offset)
        self.scene().addItem(connector)
        # Bug fix (found investigating "manual segment-drag not
        # working"): SupplyPortConnectorItem.__init__ computes its own
        # initial route BEFORE it's ever added to a scene (self.scene()
        # is None at that point) -- _collect_obstacles() correctly
        # returns [] then, but that also means compute_connector_waypoints()
        # never runs its own obstacle-avoidance pass, which is the ONLY
        # thing that synthesizes a genuine middle segment for a mixed
        # (horizontal-into-vertical or vice versa) 2-segment L-bend --
        # see compute_connector_waypoints()/_avoid_obstacles()'s own
        # docstrings. A freshly-drawn connector with a mixed-direction
        # pair was left with NO draggable middle segment at all until
        # some later, unrelated reflow happened to recompute its route
        # with the scene actually attached. Recomputing once more here,
        # now that the connector is genuinely in the scene, gives it its
        # real, final, draggable shape immediately.
        connector.update_route()
        source.connectors.append(connector)
        target.connectors.append(connector)
        self._supply_connectors.append(connector)
        return connector

    @staticmethod
    def _shorts_power_switch(a, b) -> bool:
        """True when `a`/`b` are a Power Switch's own TOP anchor and the
        exact Supply Port/Internal Supply it's already BOTTOM-anchored
        to ("Supply Out") -- connecting them would short the switch
        (explicit request, re-expressed per the resolved Supply In/Out
        architecture: with those no longer dropdowns, this rule is
        enforced HERE, at connector-draw time, instead of as a dialog-
        submission check -- see power_switch_dialog.py's own module
        docstring). Every OTHER pairing (a switch's top anchor to any
        DIFFERENT Supply Port/Internal Supply, or two ordinary icons
        neither of which is a switch) is unaffected.

        Bug fix: compares against `a.target_icon.key` (THIS specific
        replica instance's own bottom-anchored target), not
        `a.placement.target_id` (the shared record's own "primary"
        target only, items 9/10) -- the previous version silently
        failed to catch a short for every NON-primary replica of a
        multi-hierarchy switch, since `b.key` (a genuinely different
        hierarchy's own target) could never equal the primary's own
        target_id anyway."""
        if isinstance(a, PowerSwitchIconItem) and b.key == a.target_icon.key:
            return True
        if isinstance(b, PowerSwitchIconItem) and a.key == b.target_icon.key:
            return True
        return False

    def _rejects_virtual_supply_connection(self, a, b) -> bool:
        """Section 1 (explicit request), ONE-DIRECTIONAL: constrains
        what a VIRTUAL SUPPLY may connect to -- a macro's own INTERNAL
        PG pin, and NOTHING else (not a regular Supply Port, Internal
        Supply, Power Switch, or another Virtual Supply). Deliberately
        does NOT constrain what may connect TO a macro's internal PG
        pin -- a regular Supply Port/Internal Supply remains freely
        able to, exactly as before (see build_supply_port_overlay()'s
        own PG-pin auto-derivation and pg_pin_placements' own
        `is_internal` classification -- both entirely untouched by this
        check). Checked in BOTH orderings (`a` a Virtual Supply, or `b`
        one) since try_connect_supply_ports() itself is direction-
        agnostic about which of source/target the user actually dragged
        FROM. `getattr(..., "is_virtual_supply", False)` -- not a bare
        attribute access -- since one side can be a PowerSwitchIconItem
        (its own PowerSwitchPlacement has no such field at all, same
        "no edge concept just means no match" precedent
        _exit_direction()'s own getattr() fallback already establishes)."""
        for x, y in ((a, b), (b, a)):
            if getattr(getattr(x, "placement", None), "is_virtual_supply", False):
                y_is_macro_internal_pg_pin = y.key in self._shared_state.pg_pin_placements and getattr(
                    getattr(y, "placement", None), "is_internal", False
                )
                if not y_is_macro_internal_pg_pin:
                    return True
        return False

    def _existing_connection_between(self, source: SupplyPortIconItem, target: SupplyPortIconItem) -> bool:
        """Whether a power net ALREADY connects `source`/`target` --
        direction-agnostic (explicit request): A-to-B and B-to-A count as
        the same existing connection, so both orderings are checked.
        Operates purely on the LOGICAL pair (`supply_connector_placements`,
        icon keys) -- entirely independent of how either net's own visual
        route is drawn."""
        pair = (source.key, target.key)
        reverse_pair = (target.key, source.key)
        return pair in self._shared_state.supply_connector_placements or reverse_pair in self._shared_state.supply_connector_placements

    def try_connect_supply_ports(self, source: SupplyPortIconItem, target: SupplyPortIconItem) -> bool:
        """The directly-testable core of committing a connector -- see
        is_nested_pair()'s own docstring for the "inner supply port" rule.
        Returns whether a connector was actually created, so a caller
        (the mouse-release handler, or a test) can tell success from a
        rejected drop without inspecting scene state itself. Persists the
        connection (by icon key, not object identity -- icons don't
        survive a rebuild, keys do) so it's restored by _rebuild() too.

        Bug fix, since generalized (explicit request): if either side
        belongs to a multi-hierarchy replication group -- Supply Port/
        Internal Supply or Power Switch alike -- the equivalent
        connection is now also created on every OTHER member
        hierarchy's own correct counterpart -- see
        _replicate_manual_connector()'s own docstring. Committed
        together in the SAME undo step, so a single Ctrl+Z undoes the
        whole replicated change atomically.

        Virtual Supply (explicit request, section 1, ONE-DIRECTIONAL --
        see _rejects_virtual_supply_connection()'s own docstring): a
        Virtual Supply may connect ONLY to a macro's own internal PG
        pin, silently rejected otherwise -- what may connect TO a
        macro's internal PG pin is completely unaffected.

        Duplicate-net prevention (explicit request): unlike the other
        rejection reasons above (self-connect, non-nested pair, Virtual
        Supply target restriction, shorting a Power Switch -- all
        silent, matching this method's own pre-existing behavior,
        untouched here), an already-connected
        pair shows an explicit QMessageBox warning -- there's no dialog
        step in this drag-drop-driven flow to surface a ValueError
        through the way every other "Add X" flow does, so the warning is
        shown directly here instead, mirroring how a click-to-place
        tool's own inline validation (e.g. _prompt_add_power_supply())
        shows one before ever getting as far as committing anything."""
        if source is target or not is_nested_pair(source.owner_box.path, target.owner_box.path):
            return False
        if self._rejects_virtual_supply_connection(source, target):
            return False
        if self._shorts_power_switch(source, target):
            return False
        if self._existing_connection_between(source, target):
            message_box.warning(self, "Connect Power Supply", f"A Power Net already connects '{source.name}' and '{target.name}'.")
            return False
        before = self._snapshot_state()
        self._connect_icons(source, target)
        self._shared_state.supply_connector_placements.append((source.key, target.key))
        self._replicate_manual_connector(source, target)
        self._commit_undo_step("Connect Power Supply", before)
        return True

    def _replica_icons(self, icon) -> list:
        """Every OTHER live icon representing the SAME logical,
        replicated entity as `icon` -- generalizes across EVERY
        replicated element type this diagram has (explicit request:
        "not just Power Switch" -- Supply Port/Internal Supply, Power
        Switch, and any future replicated element type), reusing
        whichever "replication group" tracking that element type
        already has, never re-derived here per type. `icon` itself is
        never included. Empty for a non-replicated (single-hierarchy)
        icon -- the common case, and the reason every caller of this
        can cheaply early-out on "nothing to replicate" for an
        ordinary connection."""
        if isinstance(icon, PowerSwitchIconItem):
            return [i for i in self._power_switch_icons.get(icon.placement.id, []) if i is not icon]
        if isinstance(icon, SupplyPortIconItem):
            replicas = []
            for placement in self._corresponding_supply_port_replicas(icon):
                replica_icon = self._supply_port_icons.get(placement.id)
                if replica_icon is not None:
                    replicas.append(replica_icon)
            return replicas
        return []

    def _corresponding_replica_counterpart(self, icon, other_owner_path: str):
        """The correct counterpart of `icon` on `other_owner_path` --
        resolved via the SAME two mechanisms already established
        elsewhere in this file for exactly this ambiguity (flagged
        explicitly, not guessed at), tried in order:
          1. `icon` is already directly connectable to
             `other_owner_path` (is_nested_pair()) -- i.e. `icon` sits
             on a common ANCESTOR of every member hierarchy (one
             genuinely shared entity, e.g. a top-level unswitched
             supply) -- reuse the EXACT SAME `icon` object; there is no
             separate per-hierarchy instance of it to look for.
          2. Otherwise, `icon` has its own legitimate replica
             (_replica_icons()) anchored specifically to
             `other_owner_path` -- use THAT instead, mirroring how a
             Power Switch's own target itself already replicates by
             name (_corresponding_target_ids()).
        None when neither applies -- there is no sensible counterpart
        on that hierarchy, and this is the caller's own signal to skip
        rather than fabricate a nonsensical cross-hierarchy
        connection."""
        if is_nested_pair(icon.owner_box.path, other_owner_path):
            return icon
        for replica in self._replica_icons(icon):
            if replica.owner_box.path == other_owner_path:
                return replica
        return None

    def _replicate_manual_connector(self, a: SupplyPortIconItem, b: SupplyPortIconItem) -> None:
        """General mechanism (explicit request: "this should be built
        as ONE general mechanism... rather than implementing it once
        for Power Switch and separately for Supply Port/Internal
        Supply" -- supersedes an earlier, Power-Switch-only version of
        this same fix). Called right after a fresh connector between
        `a`/`b` is committed: if EITHER endpoint belongs to a
        replication group (_replica_icons() -- any element type), the
        equivalent connection is created on every OTHER member
        hierarchy either endpoint's own group spans, pairing each
        hierarchy's own correct counterpart for BOTH sides
        (_corresponding_replica_counterpart()) -- not just whichever
        side happened to be the "replicated" one, since a connection
        between two DIFFERENT replicated elements (e.g. a replicated
        Internal Supply directly to a replicated Power Switch) needs
        both sides resolved per hierarchy. A no-op when NEITHER side is
        replicated at all (an ordinary single-hierarchy connection --
        the common case). Skips a hierarchy where either side has no
        real counterpart (nothing to fabricate), a pairing that would
        short a Power Switch (_shorts_power_switch(), a no-op for a
        non-switch pairing), or a pairing that's already connected (no
        duplicate persisted pair/connector)."""
        other_paths = {r.owner_box.path for r in self._replica_icons(a)} | {r.owner_box.path for r in self._replica_icons(b)}
        for other_path in other_paths:
            other_a = self._corresponding_replica_counterpart(a, other_path)
            other_b = self._corresponding_replica_counterpart(b, other_path)
            if other_a is None or other_b is None or other_a is other_b:
                continue
            if self._shorts_power_switch(other_a, other_b):
                continue
            pair = (other_a.key, other_b.key)
            if pair in self._shared_state.supply_connector_placements or (other_b.key, other_a.key) in self._shared_state.supply_connector_placements:
                continue
            self._connect_icons(other_a, other_b)
            self._shared_state.supply_connector_placements.append(pair)

    def _remove_replicated_manual_connectors(self, connector: "SupplyPortConnectorItem") -> None:
        """Symmetric, equally general counterpart to
        _replicate_manual_connector() (explicit request): deleting a
        manually-drawn connector touching a replicated element must
        also remove the equivalent, auto-created connections on every
        OTHER member hierarchy. Re-derives "which other connections are
        equivalent" via the exact same
        _corresponding_replica_counterpart() trace used at creation
        time, rather than tracking a separate replication-group id --
        finds each OTHER hierarchy's own live connector between its own
        two correct counterparts (if one currently exists) and removes
        it via the same _remove_connector() core. A no-op when NEITHER
        side of `connector` is replicated, or when an other hierarchy's
        own equivalent was never actually connected in the first place
        (e.g. it was skipped at creation time -- nothing to remove
        there)."""
        a, b = connector.port_a, connector.port_b
        other_paths = {r.owner_box.path for r in self._replica_icons(a)} | {r.owner_box.path for r in self._replica_icons(b)}
        for other_path in other_paths:
            other_a = self._corresponding_replica_counterpart(a, other_path)
            other_b = self._corresponding_replica_counterpart(b, other_path)
            if other_a is None or other_b is None or other_a is other_b:
                continue
            replica_connector = next(
                (c for c in list(other_a.connectors) if c.port_a is other_b or c.port_b is other_b), None
            )
            if replica_connector is not None:
                self._remove_connector(replica_connector)

    # --- Zoom Lens (DiagramMode.ZOOM_LENS) -- rubber-band zoom ----------

    _RUBBER_BAND_CLICK_THRESHOLD_PX = 5  # explicit request: a drag this short is a click, not a zoom
    _MIN_ZOOM_SCALE = 0.05  # explicit request: clamp so zoom in/out can't run away to nothing/infinity
    _MAX_ZOOM_SCALE = 20.0

    def _start_rubber_band(self, pos: QPoint, kind: str) -> None:
        """`kind` is "in" (left-click-drag) or "out" (right-click-drag,
        explicit request) -- both share the exact same QRubberBand/
        origin/threshold machinery, only _finish_rubber_band()'s own
        math differs per kind."""
        self._rubber_band_origin = pos
        self._rubber_band_kind = kind
        self._rubber_band = QRubberBand(QRubberBand.Shape.Rectangle, self.viewport())
        self._rubber_band.setGeometry(QRect(pos, QSize()))
        self._rubber_band.show()

    def _update_rubber_band(self, pos: QPoint) -> None:
        self._rubber_band.setGeometry(QRect(self._rubber_band_origin, pos).normalized())

    def _finish_rubber_band(self, pos: QPoint) -> None:
        origin = self._rubber_band_origin
        rect = QRect(origin, pos).normalized()
        kind = self._rubber_band_kind
        self._rubber_band.hide()
        self._rubber_band.deleteLater()
        self._rubber_band = None
        self._rubber_band_origin = None
        self._rubber_band_kind = None
        if (pos - origin).manhattanLength() < self._RUBBER_BAND_CLICK_THRESHOLD_PX:
            return  # basically a click, not a drag -- no-op, explicit request (both kinds)
        if kind == "in":
            self._zoom_in_to_rect(rect)
        else:
            self._zoom_out_from_rect(rect)
        # Deliberately does NOT switch back to DiagramMode.SELECT --
        # explicit request: stays in Zoom Lens so the user can zoom again
        # (in or out) without re-selecting the tool. Nothing here calls
        # set_mode() at all, on either the view or the toolbar -- the
        # mode only ever changes in reaction to the toolbar's own
        # modeChanged signal.

    def _zoom_in_to_rect(self, rect: QRect) -> None:
        scene_rect = self.mapToScene(rect).boundingRect()
        if scene_rect.isEmpty():
            return
        self.fitInView(scene_rect, Qt.AspectRatioMode.KeepAspectRatio)
        self._clamp_zoom_scale()

    def _zoom_out_from_rect(self, rect: QRect) -> None:
        """Explicit request: the drawn rectangle is the fraction of the
        current view the view should shrink to fit inside -- a small
        rect zooms out a lot, a rect nearly the size of the viewport
        zooms out only slightly (the exact inverse of zoom-in's own
        "rect grows to fill the view" ratio). `view.scale()` alone zooms
        anchored at wherever the live cursor happens to be
        (AnchorUnderMouse, the base class's own default -- see its
        __init__), not necessarily this rect's own center; centerOn()
        right after is the direct Qt equivalent of "capture scene pos of
        the rect's center before/after scaling and translate to
        compensate" -- it repositions the view so that exact scene point
        ends up centered, regardless of where the anchor put it."""
        viewport_rect = self.viewport().rect()
        if rect.width() <= 0 or rect.height() <= 0:
            return
        scene_center = self.mapToScene(rect.center())
        scale_factor = min(viewport_rect.width() / rect.width(), viewport_rect.height() / rect.height())
        if scale_factor <= 0:
            return
        self.scale(1 / scale_factor, 1 / scale_factor)
        self._clamp_zoom_scale()
        self.centerOn(scene_center)

    def _clamp_zoom_scale(self) -> None:
        current = self.transform().m11()
        if current <= 0:
            return
        clamped = min(max(current, self._MIN_ZOOM_SCALE), self._MAX_ZOOM_SCALE)
        if clamped != current:
            factor = clamped / current
            self.scale(factor, factor)

    # --- Supply Port (DiagramMode.SUPPLY_PORT) -- hover+click placement --
    # explicit request, superseding an earlier drag-and-drop mechanism
    # ("REMOVE the drag-and-drop placement mechanism entirely -- it is
    # not working reliably and should not be kept as a placement
    # method"): as the cursor moves over a hierarchy block while this
    # mode is active, that block highlights fluorescent yellow
    # (_BoxHighlightOverlay, same helper/constant the old drag-and-drop
    # version already used -- reused as-is, only *when* it gets driven
    # changed, from dragMoveEvent() to mouseMoveEvent()); a left-click
    # while a block is highlighted adds a Supply Port there via the same
    # "Add Power Supply" dialog the right-click context-menu path already
    # uses (explicit request: "do NOT build a new one"). Mouse events on
    # a QGraphicsView are already correctly viewport-relative (every
    # other gesture in this class already relies on that), so this
    # mechanism doesn't inherit the coordinate-space pitfall the removed
    # drag-and-drop version had.

    def _update_supply_port_hover(self, pos: QPoint) -> None:
        """A no-op outside Supply Port mode (mirrors
        _update_supply_net_hover()'s own "only finds a new one" shape --
        set_mode() already clears any stale highlight the moment the
        mode changes away). Explicit request: an invalid target (wrong
        level, or a macro) never highlights at all, unlike Retention's
        own hover below -- see _validate_supply_port_target()'s own
        docstring for why the level rule lives here (UI layer) and not
        in add_supply_port() itself."""
        if self._mode != DiagramMode.SUPPLY_PORT:
            return
        box = self._box_at(pos)
        if box is not None and self._validate_supply_port_target(box) is not None:
            box = None
        if box is self._supply_port_hover_box:
            return
        self._clear_supply_port_hover_highlight()
        if box is not None:
            self._supply_port_hover_box = box
            self._box_highlight_overlay = _BoxHighlightOverlay(box)

    def _clear_supply_port_hover_highlight(self) -> None:
        if self._box_highlight_overlay is not None:
            if self._box_highlight_overlay.scene() is not None:
                self._box_highlight_overlay.scene().removeItem(self._box_highlight_overlay)
            self._box_highlight_overlay = None
        self._supply_port_hover_box = None

    # --- Internal Supply (DiagramMode.INTERNAL_SUPPLY) -- hover+click ----
    # Mirror image of Supply Port above -- same hover+click mechanism,
    # same "invalid target never highlights" gating (_validate_internal_supply_target()),
    # own dedicated pair of hover-state fields (same "each mode gets its
    # own hover state" precedent Retention already established).

    def _update_internal_supply_hover(self, pos: QPoint) -> None:
        if self._mode != DiagramMode.INTERNAL_SUPPLY:
            return
        box = self._box_at(pos)
        if box is not None and self._validate_internal_supply_target(box) is not None:
            box = None
        if box is self._internal_supply_hover_box:
            return
        self._clear_internal_supply_hover_highlight()
        if box is not None:
            self._internal_supply_hover_box = box
            self._internal_supply_highlight_overlay = _BoxHighlightOverlay(box)

    def _clear_internal_supply_hover_highlight(self) -> None:
        if self._internal_supply_highlight_overlay is not None:
            if self._internal_supply_highlight_overlay.scene() is not None:
                self._internal_supply_highlight_overlay.scene().removeItem(self._internal_supply_highlight_overlay)
            self._internal_supply_highlight_overlay = None
        self._internal_supply_hover_box = None

    # --- Virtual Supply (DiagramMode.VIRTUAL_SUPPLY) -- hover+click ------
    # Mirror image of Supply Port/Internal Supply above -- same hover+click
    # mechanism, same "invalid target never highlights" gating
    # (_validate_virtual_supply_target(), macro-only rule -- Virtual Supply
    # has no LEVEL restriction at all, unlike Supply Port/Internal Supply).

    def _update_virtual_supply_hover(self, pos: QPoint) -> None:
        if self._mode != DiagramMode.VIRTUAL_SUPPLY:
            return
        box = self._box_at(pos)
        if box is not None and self._validate_virtual_supply_target(box) is not None:
            box = None
        if box is self._virtual_supply_hover_box:
            return
        self._clear_virtual_supply_hover_highlight()
        if box is not None:
            self._virtual_supply_hover_box = box
            self._virtual_supply_highlight_overlay = _BoxHighlightOverlay(box)

    def _clear_virtual_supply_hover_highlight(self) -> None:
        if self._virtual_supply_highlight_overlay is not None:
            if self._virtual_supply_highlight_overlay.scene() is not None:
                self._virtual_supply_highlight_overlay.scene().removeItem(self._virtual_supply_highlight_overlay)
            self._virtual_supply_highlight_overlay = None
        self._virtual_supply_hover_box = None

    # --- Retention (DiagramMode.RETENTION) -- hover+click placement ------
    # Same hover+click mechanism as Supply Port above (explicit request:
    # "same pattern as Supply Port"), reusing _BoxHighlightOverlay as-is.
    # Placement itself is offered on ANY block (leaf or not, macro or
    # not) -- both validation rules (Power Domain membership, macro
    # exclusion -- see _validate_retention_target()) are enforced at
    # click/menu-invocation time with their own distinct error messages,
    # not by disabling the hover/menu entry itself, same precedent
    # Create Power Domain's own macro-disabled *menu item* deliberately
    # does NOT follow (that one greys out; this one always offers the
    # action and explains why it's rejected when clicked, matching how
    # the (since-reversed) macro Supply Port rule used to be gated too).

    def _update_retention_hover(self, pos: QPoint) -> None:
        if self._mode != DiagramMode.RETENTION:
            return
        box = self._box_at(pos)
        if box is self._retention_hover_box:
            return
        self._clear_retention_hover_highlight()
        if box is not None:
            self._retention_hover_box = box
            self._retention_highlight_overlay = _BoxHighlightOverlay(box)

    def _clear_retention_hover_highlight(self) -> None:
        if self._retention_highlight_overlay is not None:
            if self._retention_highlight_overlay.scene() is not None:
                self._retention_highlight_overlay.scene().removeItem(self._retention_highlight_overlay)
            self._retention_highlight_overlay = None
        self._retention_hover_box = None

    # --- Isolation Cell (DiagramMode.ISOLATION_CELL) -- hover+click placement
    # Same hover+click mechanism as Retention above (TARGET is a hierarchy
    # block too) -- but the ONLY valid targets are a Power Domain's own
    # boundary blocks (find_domain_for_owner() -- exact ownership, NOT
    # find_effective_domain_for_path()'s own cascading "anywhere inside"
    # rule Retention uses; see _validate_isolation_target()). Same
    # "always offered, explained on click" convention as Retention: hover
    # highlights ANY block, validity is enforced at click/menu time.

    def _update_isolation_cell_hover(self, pos: QPoint) -> None:
        if self._mode != DiagramMode.ISOLATION_CELL:
            return
        box = self._box_at(pos)
        if box is self._isolation_cell_hover_box:
            return
        self._clear_isolation_cell_hover_highlight()
        if box is not None:
            self._isolation_cell_hover_box = box
            self._isolation_cell_highlight_overlay = _BoxHighlightOverlay(box)

    def _clear_isolation_cell_hover_highlight(self) -> None:
        if self._isolation_cell_highlight_overlay is not None:
            if self._isolation_cell_highlight_overlay.scene() is not None:
                self._isolation_cell_highlight_overlay.scene().removeItem(self._isolation_cell_highlight_overlay)
            self._isolation_cell_highlight_overlay = None
        self._isolation_cell_hover_box = None

    # --- Level Shifter (DiagramMode.LEVEL_SHIFTER) -- hover+click placement -
    # Same hover+click mechanism/validation as Isolation Cell above (follows
    # the Isolation model, section 5).

    def _update_level_shifter_hover(self, pos: QPoint) -> None:
        if self._mode != DiagramMode.LEVEL_SHIFTER:
            return
        box = self._box_at(pos)
        if box is self._level_shifter_hover_box:
            return
        self._clear_level_shifter_hover_highlight()
        if box is not None:
            self._level_shifter_hover_box = box
            self._level_shifter_highlight_overlay = _BoxHighlightOverlay(box)

    def _clear_level_shifter_hover_highlight(self) -> None:
        if self._level_shifter_highlight_overlay is not None:
            if self._level_shifter_highlight_overlay.scene() is not None:
                self._level_shifter_highlight_overlay.scene().removeItem(self._level_shifter_highlight_overlay)
            self._level_shifter_highlight_overlay = None
        self._level_shifter_hover_box = None

    # --- Power Switch (DiagramMode.POWER_SWITCH) -- hover+click placement -
    # Same hover+click mechanism as Supply Port/Retention above, but the
    # TARGET is a Supply Port/Internal Supply ICON (see
    # _supply_port_icon_at()), not a hierarchy block -- hover reuses
    # _IconHighlightOverlay (the icon-shaped sibling of _BoxHighlightOverlay,
    # defined earlier in this module). A fixed (macro PG-pin) icon is
    # never a valid TARGET TYPE at all (same "wrong kind of target never
    # highlights" rule Supply Port's own _validate_supply_port_target()
    # already establishes for the wrong hierarchy LEVEL) -- excluded
    # here, at the UI layer, before hover even lights up. The Power-
    # Domain-membership rule, by contrast, follows Retention's own
    # precedent (_validate_retention_target()): a valid TARGET TYPE
    # always highlights and is always offered, with the specific reason
    # explained on click if it fails.

    _POWER_SWITCH_NO_DOMAIN_ERROR = (
        "Power Switch requires the target Supply Port/Internal Supply to belong to a Power Domain -- create or assign one first."
    )
    _POWER_SWITCH_ALREADY_EXISTS_ERROR = (
        "This Supply Port/Internal Supply already has a Power Switch attached -- use Edit Power Switch (right-click) to change it, "
        "or Delete Power Switch first."
    )
    # Bug 9, RE-SCOPED by CHANGE 1 (explicit request, refined after a
    # follow-up clarification -- an earlier pure-CONNECTIVITY draft of
    # this rule was rejected: it broke item 8's own pre-existing
    # "adopt an existing incoming connection as Supply In" feature,
    # since adding a switch onto an ALREADY-connected Internal Supply
    # is the NORMAL, expected topology, not a conflict). The actual
    # rule is about NAME CONTINUITY, not connection presence/count: a
    # power net can legitimately stay one continuous, same-named net
    # across multiple Power Domains (exactly what CHANGE 1 + the
    # auto-link mechanism enable) as long as nothing interrupts that
    # continuity. A Power Switch DOES interrupt it -- its output side
    # is not guaranteed to equal its input side (it can be OFF while
    # the source stays ON), so the two sides are electrically DIFFERENT
    # nodes and must not share a name that implies otherwise. So: a
    # Power Switch can't be added on an Internal Supply whose own name
    # is EQUAL to the name of whatever it's currently connected to (its
    # own would-be Supply In, per _adopt_existing_connection_as_supply_in()) --
    # that connection being adopted as Supply In is untouched either
    # way; only whether ADDING THE SWITCH ITSELF is allowed depends on
    # this name check. An Internal Supply with NO upstream connection at
    # all is never restricted (nothing to compare names against) -- this
    # is exactly what makes two same-named-but-UNCONNECTED placements
    # (CHANGE 1) fully independent, each free to get its own switch.
    # Cascaded switches need no special-casing: once a switch forces a
    # rename that breaks the name match, a SECOND switch further
    # downstream checks against the (already-renamed) immediate
    # upstream neighbor's name, not the original top-level source's.
    _POWER_SWITCH_NAME_CONTINUITY_ERROR_TEMPLATE = (
        "The Internal Supply '{name}' shares its name with its own upstream Supply In connection (hierarchy '{other_owner_path}') -- "
        "a Power Switch breaks that continuity (its output side is not guaranteed to equal its input side), so the two can no longer "
        "share a name implying one continuous net. Rename this Internal Supply to a unique name before adding a Power Switch."
    )

    def _supply_in_name_conflict_owner(self, icon: SupplyPortIconItem) -> Optional[str]:
        """The owner_path of `icon`'s own upstream connection (its
        would-be Supply In, per _adopt_existing_connection_as_supply_in())
        when that connection's name EQUALS `icon`'s own name, or None
        otherwise (no upstream connection at all, or one whose name
        already differs) -- the actual condition add_power_switch()
        rejects on, checked FRESH every time (not a stored/historical
        flag) so it resolves itself the instant either side is renamed
        to a genuinely different name. Reuses connected_supply_port()
        verbatim (the exact same core macro PG-pin "Connected"
        resolution and _resolve_power_switch_supply_in_name() already
        rely on) -- already excludes PG pins as a match (only a genuine
        SupplyPortPlacement counts), so a connection to a macro's own
        fixed PG pin alone can never trip this rule."""
        connected = connected_supply_port(icon.key, self._shared_state)
        if connected is not None and connected.name == icon.placement.name:
            return connected.owner_path
        return None

    def _is_valid_power_switch_target(self, icon: Optional[SupplyPortIconItem]) -> bool:
        """TARGET-TYPE validity only (never a macro PG pin, never a
        Virtual Supply -- explicit request: "A Power Switch can NEVER
        be added on a Virtual Supply") -- NOT the Power-Domain-
        membership rule, which is a separate, click-time-only check
        (_validate_power_switch_target() below) per this section's own
        docstring."""
        return icon is not None and not icon.fixed and not icon.placement.is_virtual_supply

    def _validate_power_switch_target(self, icon: SupplyPortIconItem) -> Optional[str]:
        """Rule B (Power Domain must exist first) -- reuses the EXACT
        same find_effective_domain_for_path() core Retention's own
        _validate_retention_target() already uses, scoped to the
        TARGET's own owner_path (a Power Switch's target is an icon, not
        a block directly -- see power_switch_overlay.py's own module
        docstring on why PowerSwitchPlacement.owner_path mirrors the
        target's own). Returns None when a Power Domain is found."""
        if find_effective_domain_for_path(self._domain_state.domains, icon.placement.owner_path) is None:
            return self._POWER_SWITCH_NO_DOMAIN_ERROR
        return None

    def _update_power_switch_hover(self, pos: QPoint) -> None:
        if self._mode != DiagramMode.POWER_SWITCH:
            return
        icon = self._supply_port_icon_at(pos)
        if not self._is_valid_power_switch_target(icon):
            icon = None
        if icon is self._power_switch_hover_icon:
            return
        self._clear_power_switch_hover_highlight()
        if icon is not None:
            self._power_switch_hover_icon = icon
            self._power_switch_highlight_overlay = _IconHighlightOverlay(icon)

    def _clear_power_switch_hover_highlight(self) -> None:
        if self._power_switch_highlight_overlay is not None:
            if self._power_switch_highlight_overlay.scene() is not None:
                self._power_switch_highlight_overlay.scene().removeItem(self._power_switch_highlight_overlay)
            self._power_switch_highlight_overlay = None
        self._power_switch_hover_icon = None

    # --- Supply Net (DiagramMode.SUPPLY_NET) -----------------------------
    # The actual click-drag-connect gesture (precise connector-handle hit
    # target, live preview line, commit on a valid drop) reuses
    # _connector_handle_at() -- see mousePressEvent()'s own SUPPLY_NET
    # branch below, now explicitly gated on this mode (a real, deliberate
    # behavioral change -- this gesture predates DiagramMode entirely and
    # used to fire regardless of the active tool; explicit request: "fix
    # that").

    def _update_supply_net_hover(self, pos: QPoint) -> None:
        """Highlights whichever connector handle (if any) is under the
        cursor while DiagramMode.SUPPLY_NET is active -- explicit
        request: "give some hover feedback... when the cursor is over a
        valid connector, so it's clear where a connection can start or
        land." Requires viewport mouse tracking (see __init__) to run on
        every move, not just while a button is held. Also reused, during
        an active connector drag, as the "currently a valid drop target"
        highlight -- the drag's own SOURCE handle
        (_connector_drag_origin) is deliberately never touched by this
        method (neither highlighted nor un-highlighted here) so it stays
        lit for the whole drag regardless of where the cursor wanders;
        `self._hovered_handle` only ever tracks the *other* (candidate
        target) handle. A no-op outside Supply Net mode (set_mode()
        already clears any stale highlight the moment the mode changes
        away, so this only needs to skip *finding a new one*)."""
        if self._mode != DiagramMode.SUPPLY_NET:
            return
        handle = self._connector_handle_at(pos)
        if handle is self._hovered_handle:
            return
        if self._hovered_handle is not None and self._hovered_handle is not self._connector_drag_origin:
            self._hovered_handle.set_hover_highlight(False)
        self._hovered_handle = handle
        if handle is not None and handle is not self._connector_drag_origin:
            handle.set_hover_highlight(True)

    def _clear_supply_net_highlights(self) -> None:
        if self._connector_drag_origin is not None:
            self._connector_drag_origin.set_hover_highlight(False)
        if self._hovered_handle is not None:
            self._hovered_handle.set_hover_highlight(False)
        self._hovered_handle = None

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.position().toPoint()
            if self._mode == DiagramMode.ZOOM_LENS:
                self._start_rubber_band(pos, "in")
                event.accept()
                return
            if self._mode == DiagramMode.SUPPLY_PORT:
                # Explicit request (hover+click, superseding the earlier
                # drag-and-drop mechanism): a left-click while hovering a
                # highlighted block adds a Supply Port there via the same
                # dialog the right-click "Add Power Supply" action uses;
                # clicking outside any block does nothing (no crash, no
                # placement). Always consumed either way -- this mode is
                # a dedicated placement tool, not a normal-selection one.
                box = self._box_at(pos)
                if box is not None:
                    self._prompt_add_power_supply(box)
                    # Deliberately stays in DiagramMode.SUPPLY_PORT --
                    # same "don't auto-switch back to Select" rule Zoom
                    # Lens already follows -- so more ports can be added
                    # by continuing to hover + click other blocks.
                event.accept()
                return
            if self._mode == DiagramMode.INTERNAL_SUPPLY:
                # Mirror image of Supply Port above.
                box = self._box_at(pos)
                if box is not None:
                    self._prompt_add_internal_supply(box)
                event.accept()
                return
            if self._mode == DiagramMode.VIRTUAL_SUPPLY:
                # Mirror image of Supply Port/Internal Supply above.
                box = self._box_at(pos)
                if box is not None:
                    self._prompt_add_virtual_supply(box)
                event.accept()
                return
            if self._mode == DiagramMode.RETENTION:
                # Same hover+click shape as Supply Port above: a click on
                # a highlighted block invokes the (validate then dialog)
                # flow; clicking empty canvas does nothing. Stays in
                # DiagramMode.RETENTION afterward -- same "don't
                # auto-switch back to Select" rule every placement tool
                # here already follows.
                box = self._box_at(pos)
                if box is not None:
                    self._prompt_add_retention(box)
                event.accept()
                return
            if self._mode == DiagramMode.ISOLATION_CELL:
                # Same hover+click shape as Retention above. Stays in
                # DiagramMode.ISOLATION_CELL afterward -- same "don't
                # auto-switch back to Select" rule every placement tool
                # here already follows.
                box = self._box_at(pos)
                if box is not None:
                    self._prompt_add_isolation(box)
                event.accept()
                return
            if self._mode == DiagramMode.LEVEL_SHIFTER:
                # Same hover+click shape as Isolation Cell above (follows
                # the Isolation model, section 5). Stays in
                # DiagramMode.LEVEL_SHIFTER afterward -- same "don't
                # auto-switch back to Select" rule every placement tool
                # here already follows.
                box = self._box_at(pos)
                if box is not None:
                    self._prompt_add_level_shifter(box)
                event.accept()
                return
            if self._mode == DiagramMode.POWER_SWITCH:
                # Same hover+click shape as Retention above, but the
                # TARGET is a Supply Port/Internal Supply icon (see
                # _supply_port_icon_at()) -- a fixed (PG-pin) icon or
                # empty canvas does nothing (never a valid target TYPE,
                # see this mode's own hover section). Stays in
                # DiagramMode.POWER_SWITCH afterward -- same "don't
                # auto-switch back to Select" rule every placement tool
                # here already follows.
                icon = self._supply_port_icon_at(pos)
                if self._is_valid_power_switch_target(icon):
                    self._prompt_add_power_switch(icon)
                event.accept()
                return
            if self._mode == DiagramMode.SUPPLY_NET:
                handle = self._connector_handle_at(pos)
                if handle is not None:
                    self._connector_drag_origin = handle
                    # Explicit request: the source anchor point stays
                    # highlighted for the duration of the drag.
                    handle.set_hover_highlight(True)
                    self._hovered_handle = None
                    start = handle.scene_center()
                    obstacles = collect_macro_obstacles(self._boxes, exclude=(handle.owner.owner_box,))
                    self._connector_drag_line = QGraphicsPathItem(
                        path_from_waypoints(compute_connector_waypoints(start, start, obstacles=obstacles))
                    )
                    self._connector_drag_line.setPen(QPen(handle.owner.color, _CONNECTOR_LINE_WIDTH, Qt.PenStyle.DashLine))
                    # Z_SUPPLY_PORT_CONNECTOR, matching the committed connector
                    # it previews -- a real bug, found via a real-QTest
                    # investigation: with no Z set at all, this item defaulted
                    # to Z=0, *below* every InstanceBoxItem (Z=1), rendering it
                    # invisibly behind the box's own fill for virtually the
                    # entire drag.
                    self._connector_drag_line.setZValue(Z_SUPPLY_PORT_CONNECTOR)
                    self.scene().addItem(self._connector_drag_line)
                    event.accept()
                    return
                # Falls through to normal SELECT-like behavior below --
                # e.g. dragging an existing icon/segment is still
                # allowed while Supply Net is the active tool, just not
                # starting from a bare connector-handle press.
            segment_hit = self._connector_segment_at(self.mapToScene(pos))
            if segment_hit is not None:
                connector, index = segment_hit
                self._segment_drag = (connector, index, connector.segment_orientation(index))
                self._segment_drag_before = self._snapshot_state()
                event.accept()
                return
            icon_under_press = self._supply_port_icon_at(pos)
            if icon_under_press is not None:
                # Let Qt's own ItemIsMovable machinery drag the icon --
                # calling QGraphicsView's own mousePressEvent directly
                # (not the base class's) skips HierarchyDiagramView's
                # _pan_start bookkeeping entirely for this gesture, so its
                # mouseMoveEvent pan-threshold check (gated on _pan_start
                # is not None) never engages and can't hijack the drag
                # into a view-pan once it crosses the pixel threshold --
                # a real conflict, not hypothetical: that bookkeeping was
                # written before any movable item existed in this scene.
                # The snapshot here covers both the icon itself and its
                # label (a child of the icon -- _supply_port_icon_at()
                # walks up to the owning icon from either), since both
                # are ItemIsMovable and both go through this exact path.
                self._icon_drag_snapshot = self._snapshot_state()
                QGraphicsView.mousePressEvent(self, event)
                return
        elif event.button() == Qt.MouseButton.RightButton and self._mode == DiagramMode.ZOOM_LENS:
            # Explicit request: right-click-drag zooms OUT while Zoom
            # Lens is active -- bypasses super() entirely (unlike a
            # normal right-click, which the base class routes to its own
            # customContextMenuRequested/_on_context_menu popup, already
            # short-circuited for this mode -- see _on_context_menu()'s
            # own docstring).
            self._start_rubber_band(event.position().toPoint(), "out")
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        # Hover feedback runs on *every* move (not gated behind any of
        # the early-return branches below) -- it's independent of
        # whether a drag happens to be in progress (including an active
        # Supply Net connector drag, where it now doubles as "candidate
        # drop target" highlighting -- see its own docstring), and each
        # is a no-op whenever the mode doesn't match its own.
        # _update_hover_tooltip() (explicit request, the hover-delay info
        # tooltip) is the one exception to "each is a no-op whenever the
        # mode doesn't match its own" above -- it's mode-INDEPENDENT by
        # design (works identically in every DiagramMode) and is purely
        # a passive side-effect call (a timer start/stop + a non-
        # activating QLabel show/hide), so it can never block or delay
        # anything below it in this method.
        self._update_hover_tooltip(event.position().toPoint())
        self._update_supply_net_hover(event.position().toPoint())
        self._update_supply_port_hover(event.position().toPoint())
        self._update_internal_supply_hover(event.position().toPoint())
        self._update_virtual_supply_hover(event.position().toPoint())
        self._update_retention_hover(event.position().toPoint())
        self._update_isolation_cell_hover(event.position().toPoint())
        self._update_level_shifter_hover(event.position().toPoint())
        self._update_power_switch_hover(event.position().toPoint())
        if self._rubber_band is not None:
            self._update_rubber_band(event.position().toPoint())
            event.accept()
            return
        if self._connector_drag_line is not None:
            scene_pos = self.mapToScene(event.position().toPoint())
            start = self._connector_drag_origin.scene_center()
            origin_direction = _exit_direction(self._connector_drag_origin.owner)
            obstacles = collect_macro_obstacles(self._boxes, exclude=(self._connector_drag_origin.owner.owner_box,))
            # The floating end has no real icon/edge yet to derive a
            # direction from -- reuse the origin's own, a reasonable
            # approximation for a live preview (it becomes exact the
            # moment it's actually dropped on a real target's handle,
            # via SupplyPortConnectorItem.update_route()'s own per-side
            # _exit_direction() calls).
            waypoints = compute_connector_waypoints(start, scene_pos, origin_direction, origin_direction, obstacles)
            self._connector_drag_line.setPath(path_from_waypoints(waypoints))
            event.accept()
            return
        if self._segment_drag is not None:
            connector, index, orientation = self._segment_drag
            scene_pos = self.mapToScene(event.position().toPoint())
            value = scene_pos.y() if orientation == "horizontal" else scene_pos.x()
            connector.set_manual_segment_position(index, value)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._rubber_band is not None:
            self._finish_rubber_band(event.position().toPoint())
            event.accept()
            return
        if self._connector_drag_line is not None:
            pos = event.position().toPoint()
            target = self._connector_handle_at(pos)
            self.scene().removeItem(self._connector_drag_line)
            self._connector_drag_line = None
            origin = self._connector_drag_origin
            self._connector_drag_origin = None
            connected = target is not None and target.owner is not origin.owner and self.try_connect_supply_ports(origin.owner, target.owner)
            # Clear all net-related highlights either way (explicit
            # request), whether the drop succeeded, landed on an invalid
            # target, or hit empty space.
            origin.set_hover_highlight(False)
            if not connected and self._hovered_handle is not None:
                self._hovered_handle.set_hover_highlight(False)
            self._hovered_handle = None
            event.accept()
            return
        if self._segment_drag is not None:
            connector, _index, _orientation = self._segment_drag
            if connector._manual_offset is not None:
                self._shared_state.manual_connector_offsets[(connector.port_a.key, connector.port_b.key)] = connector._manual_offset
            self._segment_drag = None
            before = self._segment_drag_before
            self._segment_drag_before = None
            if before is not None:
                self._commit_undo_step("Move Power Net Segment", before)
            event.accept()
            return
        if self._icon_drag_snapshot is not None:
            before = self._icon_drag_snapshot
            self._icon_drag_snapshot = None
            super().mouseReleaseEvent(event)  # let Qt finish its own drag/release handling first
            self._commit_undo_step("Move Power Supply", before)
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:
        """Explicit request (revised again, reverting the previous
        revision): Escape ALWAYS resets the tool back to Select/Regular
        Cursor, "regardless of active tool" -- mode-reset is
        unconditional, decoupled from whether there was actually
        anything to cancel. The two steps are genuinely independent:
        (1) `_cancel_active_interaction()` still cancels/clears whatever
        local interaction happens to be in progress (rubber band, Supply
        Net drag/hover highlight, Supply Port hover highlight, an active
        pan) *if* there is one, and still returns whether it found
        anything -- that bookkeeping is kept (e.g. for tests, or any
        future caller that cares), but (2) `escapePressed` now always
        fires either way, not just when step 1 returns True. The
        previous "only reset if something was cancelled" behavior (a
        prior explicit revision) had a real, reported side effect: since
        DiagramMode.SUPPLY_PORT's own hover highlight is armed by mere
        mouse movement (not a click-drag), it's "active" far more often
        than Zoom Lens/Pan/Supply Net's own drag-only state, making
        Escape *appear* to only work in Supply Port mode in practice.
        Still always swallows the key press either way (`event.accept()`
        unconditionally) so it never falls through to some other
        widget's own Escape handling.

        Follow-up explicit request: Escape now ALSO clears any current
        yellow-fluo selection highlight (hierarchy blocks, Supply Port/
        Internal Supply/Retention icons -- whatever is currently Qt-
        selected, including a multi-selection) -- a third, independent
        step alongside the two above. `scene().clearSelection()` is all
        that's needed: it fires `selectionChanged`, already wired to
        `_on_scene_selection_changed()` below, which is what actually
        drives every selectable item's own highlight off (hierarchy
        boxes via `_highlighted_paths`/set_highlighted(), Supply Port/
        Retention icons via `set_click_highlighted()`) -- no separate
        per-item-type reset needed here. Doesn't conflict with "right-
        click + context menu must preserve the highlight while open"
        (an earlier, separate requirement): while a real QMenu.exec()
        is running, Qt delivers Escape to that modal menu itself, not to
        this widget's own keyPressEvent() at all -- closing the menu is
        the only thing Escape does in that state, standard Qt behavior,
        with nothing further required here."""
        if event.key() == Qt.Key.Key_Escape:
            self._cancel_active_interaction()
            if self.scene() is not None:
                self.scene().clearSelection()
            self.escapePressed.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def _cancel_active_interaction(self) -> bool:
        """Cancels any in-progress rubber band / Supply Net drag / Pan
        drag / active highlight. Returns whether there actually was one
        to cancel -- no longer used to gate `escapePressed` (see
        keyPressEvent()'s own docstring for why that changed), kept as
        a return value since it's still meaningful bookkeeping (tests
        rely on it directly to confirm a specific interaction was really
        cleared, not just that /something/ happened to be)."""
        had_active_interaction = (
            self._rubber_band is not None
            or self._connector_drag_line is not None
            or self._hovered_handle is not None
            or self._box_highlight_overlay is not None
            or self._retention_highlight_overlay is not None
            or self._internal_supply_highlight_overlay is not None
            or self._virtual_supply_highlight_overlay is not None
            or self._panning
        )
        if self._rubber_band is not None:
            self._rubber_band.hide()
            self._rubber_band.deleteLater()
            self._rubber_band = None
            self._rubber_band_origin = None
            self._rubber_band_kind = None
        # Highlights first, while `_connector_drag_origin` is still set --
        # a real bug, found via a real test: clearing it below *before*
        # calling _clear_supply_net_highlights() left the source handle's
        # own highlight stuck on forever, since that helper's own "is it
        # set" check would already see None.
        self._clear_supply_net_highlights()
        if self._connector_drag_line is not None:
            self.scene().removeItem(self._connector_drag_line)
            self._connector_drag_line = None
            self._connector_drag_origin = None
        self._clear_supply_port_hover_highlight()
        self._clear_retention_hover_highlight()
        self._clear_internal_supply_hover_highlight()
        self._clear_virtual_supply_hover_highlight()
        # An in-progress Pan drag (inherited from HierarchyDiagramView --
        # see _pan_enabled()) is aborted the same way: the mode reset to
        # SELECT that follows (via escapePressed, once this returns True)
        # already restores the correct cursor through set_mode(), so no
        # explicit cursor reset is needed here.
        self._pan_start = None
        self._panning = False
        return had_active_interaction

    # --- Properties table Edit/Remove (explicit request) --------------------
    # ONE generic dispatch pair (edit_properties_row()/remove_properties_row()),
    # keyed by the exact same construct-kind strings properties_panel.py's
    # own `self._tables` dict already uses -- PowerIntentPropertiesPanel
    # itself has no diagram context (no live icons/boxes/dialogs), so it
    # only ever fires (kind, construct_id) via its own editRequested/
    # removeRequested signals; PowerIntentTab wires those straight to
    # these two methods (and can_edit_properties_row()/can_remove_properties_row()
    # to properties_panel.set_action_enablement_hooks()). Every branch
    # below reuses the EXACT SAME dialog/cascade-delete logic the
    # diagram's own right-click-on-icon flow already uses -- a second
    # entry point, never a parallel implementation, per explicit
    # request. A row with no live counterpart to resolve (e.g. a REAL
    # backend-only row, or a hidden hierarchy's own icon) is a silent
    # no-op -- can_edit_properties_row()/can_remove_properties_row()
    # below already keep the menu item disabled for exactly these cases,
    # so reaching here with one is defensive, not an expected path.

    def _properties_row_is_diagram_only(self, kind: str, construct_id: str) -> bool:
        """True only for a row backed by THIS diagram's own mock/
        diagram-only state (never a real, loaded PowerIntentDesign
        construct -- editing/removing those is out of scope for this
        diagram-only feature set entirely, same scoping every other
        diagram-only entity here already follows)."""
        if kind == "PowerDomain":
            return construct_id in self._domain_state.domains
        if kind == "SupplySet":
            return construct_id in self._domain_state.supply_sets
        if kind == "SupplyPort":
            # Deliberately checked against supply_port_placements ONLY --
            # a macro PG pin lives in the SEPARATE pg_pin_placements dict
            # (see supply_port_overlay.py's own module docstring) and is
            # never user-created, so it's correctly excluded here too,
            # via the same "which dict is this id actually in" signal
            # section 4 asked to confirm/plumb.
            return construct_id in self._shared_state.supply_port_placements
        if kind == "PowerSwitch":
            return construct_id in self._power_switch_state.switches
        if kind == "IsolationStrategy":
            return construct_id in self._isolation_state.strategies
        if kind == "LevelShifterStrategy":
            return construct_id in self._level_shifter_state.strategies
        if kind == "RetentionStrategy":
            return construct_id in self._retention_state.strategies
        return False

    def can_edit_properties_row(self, kind: str, construct_id: str) -> bool:
        """Section 4 (Supply Port/macro exclusion) generalizes for free
        here: a macro PG pin's own id is never in `supply_port_placements`
        (see _properties_row_is_diagram_only() above), so it's already
        correctly disabled without any macro-specific special-casing at
        this layer."""
        return self._properties_row_is_diagram_only(kind, construct_id)

    def can_remove_properties_row(self, kind: str, construct_id: str) -> bool:
        return self._properties_row_is_diagram_only(kind, construct_id)

    def edit_properties_row(self, kind: str, construct_id: str) -> None:
        if kind == "PowerDomain":
            domain = self._domain_state.domains.get(construct_id)
            box = self._boxes.get(domain.owner_path) if domain is not None else None
            if box is not None:
                self._prompt_edit_power_domain(box)
        elif kind == "SupplySet":
            self._prompt_edit_supply_set(construct_id)
        elif kind == "SupplyPort":
            icon = self._supply_port_icons.get(construct_id)
            if icon is not None and not icon.fixed:
                self._prompt_edit_supply_port(icon)
        elif kind == "PowerSwitch":
            existing = self._power_switch_state.switches.get(construct_id)
            # A multi-hierarchy switch has one icon per replicated
            # target -- the Properties table shows ONE row for the
            # whole shared record (see properties_panel.py's own
            # _load_switches() docstring), so there's no "which replica
            # was right-clicked" concept here the way the diagram's own
            # per-icon right-click has; `existing.target_id` (the
            # "primary"/first target, per PowerSwitchPlacement's own
            # __post_init__) is the reasonable default.
            icon = self._supply_port_icons.get(existing.target_id) if existing is not None else None
            if existing is not None and icon is not None:
                self._prompt_edit_power_switch(icon, existing)
        elif kind == "IsolationStrategy":
            self._prompt_edit_isolation(construct_id)
        elif kind == "LevelShifterStrategy":
            self._prompt_edit_level_shifter(construct_id)
        elif kind == "RetentionStrategy":
            self._prompt_edit_retention(construct_id)

    def remove_properties_row(self, kind: str, construct_id: str) -> None:
        if kind == "PowerDomain":
            domain = self._domain_state.domains.get(construct_id)
            box = self._boxes.get(domain.owner_path) if domain is not None else None
            if box is not None:
                self._prompt_remove_power_domain(box)
        elif kind == "SupplySet":
            self._prompt_remove_supply_set(construct_id)
        elif kind == "SupplyPort":
            icon = self._supply_port_icons.get(construct_id)
            if icon is not None and not icon.fixed:
                self._prompt_delete_supply_port(icon)
        elif kind == "PowerSwitch":
            existing = self._power_switch_state.switches.get(construct_id)
            if existing is not None:
                self._prompt_delete_power_switch(existing)
        elif kind == "IsolationStrategy":
            self._prompt_delete_isolation(construct_id)
        elif kind == "LevelShifterStrategy":
            self._prompt_delete_level_shifter(construct_id)
        elif kind == "RetentionStrategy":
            self._prompt_delete_retention(construct_id)


class PowerIntentDiagramView(QWidget):
    """`_tabs` (a HierarchyDiagramTabs) replaces what used to be a single
    `_view` -- explicit request: double-click a box (or "Visualize
    Diagram" on the Hierarchy Tree) opens that instance's own sub-
    hierarchy in a new closable tab, alongside the always-open whole-
    design one. `_shared_supply_state` is constructed once here and
    handed to every tab's view via `view_factory` (see
    SupplyPortDiagramState's own docstring) -- the entire mechanism
    behind a supply port added in a sub-module's tab showing up in the
    whole-hierarchy tab too. `current_view` (the currently active tab's
    own _PowerIntentGraphicsView) replaces the old flat `_view` for
    every caller/test that only ever cared about "whichever one is
    showing" -- see the property below.

    `undo_stack` (explicit request: Ctrl+Z/Ctrl+Y for Power Intent
    edits) is not owned here -- it's the SAME QUndoStack
    HierarchyTreeWidget's own Hide/Unhide already pushes onto, handed in
    by power_intent_tab.py (`self.tree.undo_stack`), so the tab's
    existing Ctrl+Z/Ctrl+Y shortcuts (already wired to that one stack)
    just start undoing/redoing supply-port edits too, uniformly with
    Hide/Unhide, rather than needing a second independent stack fighting
    over the same shortcut. Every tab's view is constructed with a
    `refresh_current` callback closing over `self._tabs.current_view` --
    re-evaluated fresh every time it's actually called (an undo could
    fire long after the tab that made the edit was closed), so there's
    no risk of it holding a reference to an already-destroyed view.

    `diagramStateChanged` (explicit request: the Properties panel should
    reflect diagram-only supply ports/domains too, which it can't do on
    its own since it's otherwise driven entirely by a loaded
    PowerIntentDesign -- see properties_panel.py's own docstring) is
    emitted by every tab's view via the `notify_changed` callback each
    one is constructed with, on every logical edit (add/edit/delete/
    connect/create/remove domain, and every undo/redo of one) --
    power_intent_tab.py connects it to refresh the Properties panel from
    `shared_supply_state`/`shared_domain_state` below. Deliberately a
    *different* callback from `refresh_current`: that one rebuilds a
    tab's own graphics scene, which -- for the very first commit of a
    given edit -- would destroy the live item(s) the caller (e.g.
    add_supply_port()) already built and is about to return (see
    _PowerIntentGraphicsView's own `_notify_changed` docstring).
    `diagramStateChanged` only ever triggers a plain-data table refresh,
    never touches a graphics item, so it's always safe to emit.

    `set_mode()` (explicit request: the grouped tool palette, see
    diagram_tool_palette.py) stores the current DiagramMode here --
    the one thing this wrapper's own `_tabs` doesn't already track per-
    view -- and propagates it to every currently-open tab's own view
    (`self._tabs.all_views()`). A tab opened *after* a mode change (a new
    sub-hierarchy tab) still needs to start in that same mode, not always
    reset to SELECT: `view_factory` applies `self._mode` to every freshly
    constructed view before handing it back to HierarchyDiagramTabs.

    `escapePressed` (explicit request: Escape resets tool state) is
    re-emitted from whichever tab's own view the key press actually
    landed in -- see _PowerIntentGraphicsView's own `escapePressed`
    docstring for why it's just re-broadcast here rather than this
    wrapper doing anything with it itself. power_intent_tab.py connects
    it to `tool_palette.select_action.setChecked(True)`, which drives
    the mode back to SELECT through the exact same modeChanged ->
    set_mode() chain below that a normal toolbar click already uses --
    so every open tab resets uniformly, not just the one Escape was
    pressed in.
    """

    diagramStateChanged = Signal()
    escapePressed = Signal()
    # Explicit request ("Extract Diagram" right-click): carries the inner
    # _PowerIntentGraphicsView the right-click happened in, so the export
    # can target exactly that one diagram tab.
    extractDiagramRequested = Signal(object)

    def __init__(
        self,
        hierarchy_controller: HierarchySelectionController,
        power_intent_controller: PowerIntentSelectionController,
        undo_stack: QUndoStack,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self._placeholder = QLabel("Run Mapping to see power intent overlaid on the hierarchy.")
        self._placeholder.setWordWrap(True)
        self._shared_supply_state = SupplyPortDiagramState()
        self._shared_domain_state = PowerDomainDiagramState()
        self._shared_retention_state = RetentionDiagramState()
        self._shared_port_attribute_state = PortAttributeDiagramState()
        self._shared_power_switch_state = PowerSwitchDiagramState()
        self._shared_isolation_state = IsolationDiagramState()
        self._shared_level_shifter_state = LevelShifterDiagramState()
        self._shared_macro_power_intent_state = MacroPowerIntentDiagramState()
        self._mode = DiagramMode.SELECT
        # Optional connectivity resolver, set post-construction via
        # set_connectivity_resolver() (power_intent_tab.py owns both
        # this widget and PowerIntentPropertiesPanel, constructed
        # AFTER this one -- see that method's own docstring for why
        # this is a mutable attribute read fresh by _make_view()'s own
        # closure below, rather than a constructor parameter). Defaults
        # to a safe no-op so any view constructed before the real
        # resolver is set (or a caller/test that never sets one at all)
        # still works, just without live Isolation Elements resolution.
        self._resolve_connectivity: Callable[[list[tuple[str, str]]], tuple[dict, dict]] = lambda endpoints: ({}, {})

        def _make_view() -> _PowerIntentGraphicsView:
            view = _PowerIntentGraphicsView(
                hierarchy_controller,
                power_intent_controller,
                self._shared_supply_state,
                self._shared_domain_state,
                self._shared_retention_state,
                self._shared_port_attribute_state,
                self._shared_power_switch_state,
                self._shared_isolation_state,
                self._shared_level_shifter_state,
                self._shared_macro_power_intent_state,
                undo_stack,
                self._refresh_current_view,
                self.diagramStateChanged.emit,
                resolve_connectivity=lambda endpoints: self._resolve_connectivity(endpoints),
                reflow_siblings=self._reflow_all_tabs_boxes,
            )
            view.set_mode(self._mode)
            view.escapePressed.connect(self.escapePressed.emit)
            view.extractDiagramRequested.connect(lambda v=view: self.extractDiagramRequested.emit(v))
            return view

        self._tabs = HierarchyDiagramTabs(_make_view)

        self._stack = QStackedWidget()
        self._stack.addWidget(self._placeholder)
        self._stack.addWidget(self._tabs)

        layout = QVBoxLayout(self)
        layout.addWidget(self._stack)

    def _reflow_all_tabs_boxes(self) -> None:
        """Bug fix (recurring "block doesn't make room for a new
        element" regression) -- see _PowerIntentGraphicsView's own
        `_reflow_siblings` docstring for the full "why". Reflows every
        currently-open tab's own boxes (`_reflow_own_boxes()`, a
        non-destructive in-place geometry update -- see that method's
        own docstring), including whichever tab actually triggered the
        insertion (redundant but harmless, since it's idempotent)."""
        for view in self._tabs.all_views():
            view._reflow_own_boxes()

    def _refresh_current_view(self) -> None:
        """Bug fix (undo/redo consistency): refreshes EVERY currently-
        open tab's own view, not just whichever one happens to be active
        -- a real, confirmed staleness bug otherwise (e.g. a Power Domain
        created while looking at a sub-hierarchy tab, then undone while
        the ROOT tab is active: the root tab was never colored by the
        create in the first place -- see create_power_domain()'s own
        apply_domain_coloring(self._boxes, ...), which only ever touches
        the CALLING view's own `_boxes` -- and undo's own refresh only
        rebuilt whichever tab was active at UNDO time, leaving the
        sub-hierarchy tab's own diagram permanently stuck showing the
        undone state). Every tab shares the same underlying diagram-only
        state objects (SupplyPortDiagramState/PowerDomainDiagramState/
        etc. -- see this class's own docstring), so rebuilding EVERY
        tab's own scene from that shared state is always safe and
        correct, not just the active one. This method is ONLY ever
        invoked by _DiagramStateCommand's own undo()/redo() (confirmed
        -- grepped every call site), never from a normal (non-undo/redo)
        edit path, so there's no risk of this destroying a live icon
        some caller is about to use (the exact hazard
        _DiagramStateCommand's own `_first_redo_done` skip already
        guards against for a NORMAL edit's own first, synchronous
        redo())."""
        for view in self._tabs.all_views():
            view.refresh()

    def set_mode(self, mode: DiagramMode) -> None:
        self._mode = mode
        for view in self._tabs.all_views():
            view.set_mode(mode)

    @property
    def _view(self) -> _PowerIntentGraphicsView:
        """The currently active tab's own view -- kept as `_view` (not
        just `current_view`) so every existing caller/test written
        before tabs existed, which only ever meant "whichever one is
        showing," keeps working unchanged."""
        return self._tabs.current_view

    @property
    def shared_supply_state(self) -> SupplyPortDiagramState:
        return self._shared_supply_state

    @property
    def shared_domain_state(self) -> PowerDomainDiagramState:
        return self._shared_domain_state

    @property
    def shared_port_attribute_state(self) -> PortAttributeDiagramState:
        return self._shared_port_attribute_state

    @property
    def shared_retention_state(self) -> RetentionDiagramState:
        return self._shared_retention_state

    @property
    def shared_power_switch_state(self) -> PowerSwitchDiagramState:
        return self._shared_power_switch_state

    @property
    def shared_isolation_state(self) -> IsolationDiagramState:
        return self._shared_isolation_state

    @property
    def shared_level_shifter_state(self) -> LevelShifterDiagramState:
        return self._shared_level_shifter_state

    @property
    def shared_macro_power_intent_state(self) -> MacroPowerIntentDiagramState:
        return self._shared_macro_power_intent_state

    def restore_all_shared_states(
        self,
        supply_state: SupplyPortDiagramState,
        domain_state: PowerDomainDiagramState,
        retention_state: RetentionDiagramState,
        port_attribute_state: PortAttributeDiagramState,
        power_switch_state: PowerSwitchDiagramState,
        isolation_state: IsolationDiagramState,
        level_shifter_state: LevelShifterDiagramState,
        macro_power_intent_state: Optional[MacroPowerIntentDiagramState] = None,
    ) -> None:
        """Bulk-load "Load Project" (explicit request): copies every one
        of the 8 diagram-only state families into THIS widget's own
        live, shared state objects IN PLACE (via each family's own
        restore_*_diagram_state(), the exact same primitive Ctrl+Z/
        Ctrl+Y undo already uses -- never reassigns the state objects
        themselves, which are shared by reference across every open
        tab), then rebuilds every open tab's own scene from the freshly-
        loaded state. The caller is responsible for having already
        loaded the matching RTL hierarchy (set_hierarchy_only()) and for
        switching the stack to reveal the diagram.

        `macro_power_intent_state` defaults to `None` (treated as "no
        attachments") -- added after this method already had real
        callers, so a caller that hasn't been updated to pass it yet
        (or a project file saved before this feature existed --  see
        project_file.py's own ProjectData field comment) still works."""
        restore_supply_port_diagram_state(self._shared_supply_state, supply_state)
        restore_power_domain_diagram_state(self._shared_domain_state, domain_state)
        restore_retention_diagram_state(self._shared_retention_state, retention_state)
        restore_port_attribute_diagram_state(self._shared_port_attribute_state, port_attribute_state)
        restore_power_switch_diagram_state(self._shared_power_switch_state, power_switch_state)
        restore_isolation_diagram_state(self._shared_isolation_state, isolation_state)
        restore_level_shifter_diagram_state(self._shared_level_shifter_state, level_shifter_state)
        restore_macro_power_intent_diagram_state(self._shared_macro_power_intent_state, macro_power_intent_state or MacroPowerIntentDiagramState())
        for view in self._tabs.all_views():
            view.refresh()
        self.diagramStateChanged.emit()

    def set_connectivity_resolver(self, resolver: Callable[[list[tuple[str, str]]], tuple[dict, dict]]) -> None:
        """Threaded through to every tab's own view (current and
        future -- see `_make_view()`'s own closure) as the Isolation
        dialog's own Driver/Receiver Supply Set data source. Ultimately
        backed by PowerIntentPropertiesPanel.resolve_port_connectivity()
        -- the SAME PowerResolutionCache-driven computation the PortList
        tab already uses (explicit request: reuse, don't recompute
        independently) -- wired by power_intent_tab.py right after
        constructing self.properties (which this widget, constructed
        first, has no reference to itself)."""
        self._resolve_connectivity = resolver

    @property
    def _index(self) -> dict[tuple[str, str], list]:
        return self._view._index

    @property
    def _highlighted(self) -> list:
        return self._view._highlighted_constructs

    def setSelection(self, construct_kind: str, construct_id: str) -> None:
        """Passthrough to the inner view's apply_construct_selection() --
        kept as the wrapper's own public-facing name (matching
        PowerIntentPropertiesPanel's own setSelection()) even though the
        inner QGraphicsView itself uses apply_construct_selection(), to
        avoid colliding with the name HierarchyDiagramView already uses
        for instance-path selection (apply_selection())."""
        self._view.apply_construct_selection(construct_kind, construct_id)

    def fit_view(self) -> None:
        self._tabs.fit_view()

    def all_diagram_views(self) -> list[tuple[str, "_PowerIntentGraphicsView"]]:
        """Every currently-open diagram tab as (tab-name, view) pairs --
        the always-open root tab plus every double-click drill-down tab
        (explicit request, "Extract Diagram"). Reads the QTabWidget's
        own pages directly (`self._tabs.widget(i)` / `.tabText(i)`), the
        same accurate-regardless-of-lazy-bookkeeping approach
        `all_views()` already uses."""
        return [(self._tabs.tabText(i), self._tabs.widget(i)) for i in range(self._tabs.count())]

    def open_path(self, path: str) -> None:
        """Passthrough to the tabs container's own open_path() -- wired
        from the Hierarchy Tree's "Visualize Diagram" context-menu action
        (see power_intent_tab.py)."""
        self._tabs.open_path(path)

    def set_hierarchy_only(self, rtl: Design) -> None:
        """Pre-Mapping state: base hierarchy only, no power-intent overlay
        at all -- see class docstring. Called once Load Power Intent
        succeeds but before Mapping has run. reset() closes every
        previously-open sub-hierarchy tab -- a fresh Load Design/Load
        Power Intent means any old tab's own root instance may not even
        exist anymore."""
        self._tabs.reset(rtl, populate=lambda view, root: view.load(rtl, root=root))
        self._stack.setCurrentWidget(self._placeholder)

    def set_mapped_design(self, mapped: MappedDesign) -> None:
        """Full overlay state, once Mapping has run."""
        self._tabs.reset(mapped.rtl, populate=lambda view, root: view.load_mapped(mapped, root=root))
        self._stack.setCurrentWidget(self._tabs)

    def show_bare_hierarchy(self) -> None:
        """'Create New' succeeded (load_power_intent_tab.py's own
        createdWithoutMapping, not mappingCompleted -- there is no
        MappedDesign here at all, since Create New never runs a mapping
        step). The bare hierarchy scene is already loaded into the root
        tab (set_hierarchy_only() ran earlier, right after Load Design --
        see PowerIntentTab.set_rtl()), so this only needs to switch the
        stack to reveal it -- unlike set_hierarchy_only()/
        set_mapped_design() above, it never touches any tab's scene
        itself."""
        self._stack.setCurrentWidget(self._tabs)
