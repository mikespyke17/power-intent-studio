"""Vertical tool palette for the Power Intent Diagram (explicit request,
"like KiCad's icon toolbar") -- docked to the left of the QGraphicsView,
inside the "Power Diagram" group box (see power_intent_tab.py's own
layout, which places this alongside `self.diagram`). A `QToolBar` in
`Qt.Orientation.Vertical`:

  Regular Cursor (select/default)
  Pan
  Zoom Lens (rubber-band zoom)
  -- addSeparator() --
  Supply Port
  Internal Supply
  Virtual Supply
  Supply Net
  -- addSeparator() --
  Power Switch
  -- addSeparator() --
  Isolation Cell
  Level Shifter
  Retention

Separator placement was explicitly reviewed and reset on a follow-up
request to exactly these THREE points (Zoom Lens|Supply Port, Supply
Net|Power Switch, Power Switch|Isolation Cell) -- an earlier version
had them at Zoom Lens|Supply Port and Level Shifter|Retention instead.
Tool ORDER itself is unchanged throughout this history -- only where
the two separators sit has moved.

"Internal Supply" (explicit request) sits directly after Supply Port --
a second distinct entity type from it (see supply_port_overlay.py's own
SupplyPortPlacement.is_internal_supply), placeable only on an inner
(non-top-level) hierarchy block, excluding macros -- the mirror image
of Supply Port's own top-level-only, macro-excluded rule (see
power_intent_diagram_view.py's own _is_top_level_box()/placement-gating
docstrings). A second addSeparator() was added at the same time
(originally ahead of Retention -- see the separator-placement note
above for where it's moved to since), matching this diagram's own
established "each distinct concept group gets its own visual
separation" convention (the same one already applied to the
right-click context menu's own separators).

"Virtual Supply" (explicit request) sits directly after Internal
Supply -- a THIRD, distinct supply type (see supply_port_overlay.py's
own SupplyPortPlacement.is_virtual_supply), placeable at ANY hierarchy
level (unlike Supply Port/Internal Supply, no level restriction at
all) and structurally restricted to connecting only to a macro's own
internal PG pins -- see power_intent_diagram_view.py's own
add_virtual_supply()/try_connect_supply_ports() for the full rule.

No group header labels (explicit request, removed on a follow-up --
an earlier version had "DIAGRAM VIEW"/"POWER INTENT" text headers
above each pair; the separator alone is enough visual grouping). Icon
size/padding were first shrunk (explicit request: "roughly half its
current footprint" vs. an earlier, larger-icon version), then grown
50% again on a later follow-up (14px -> 21px, padding scaled to
match) -- see `_ICON_SIZE`/the toolbar's own stylesheet below.

Retention (explicit request, added on a follow-up) reuses the same
icon asset the diagram overlay's own read-only, MappedDesign-driven
RetentionBadgeItem is conceptually about (diagram_overlay.py) --
`retention-flop.svg`, already sitting in frontend/icons/, no
placeholder needed. It is otherwise a genuinely separate, diagram-
only/mock-first construct (see power_intent_diagram_view.py's own
Retention placement handling and retention_overlay.py), the same
"mock-first" split already established between supply_port_overlay.py
and diagram_overlay.py.

Power Switch (explicit request, added on a follow-up): same hover+click
placement mechanism as Supply Port/Internal Supply above, but its own
TARGET is a Supply Port/Internal Supply icon, not a hierarchy block --
see power_switch_overlay.py/power_intent_diagram_view.py's own Power
Switch hover+click handling. Placed right after Supply Net in the
toolbar (both are Supply-Port-icon-adjacent concepts) -- own dedicated
group now, bracketed by its own pair of separators (see above) --
`power-switch.svg`, already sitting in frontend/icons/, no placeholder
needed.

Isolation Cell (explicit request, added on a follow-up, positioned
directly below Power Switch): architecturally follows the RETENTION
model (one shared entity, multiple diagram attachment points), NOT
Power Switch's own independently-replicated-per-hierarchy one -- see
isolation_overlay.py's own module docstring. Same hover+click
mechanism as Retention, TARGET is a hierarchy block (not an icon) --
`isolation-cell.svg`, already sitting in frontend/icons/, no
placeholder needed.

Level Shifter (explicit request, added on a follow-up, positioned
directly below Isolation Cell): also follows the Isolation model (see
level_shifter_overlay.py's own module docstring) -- same hover+click
mechanism, TARGET is a hierarchy block, same Power-Domain-boundary
validation rule as Isolation. `level-shifter.svg`, already sitting in
frontend/icons/, no placeholder needed.

All ten tool buttons are checkable QActions in one exclusive
QActionGroup -- exactly one mode active at a time, defaulting to Regular
Cursor. Deliberately decoupled from the Diagram View's own mouse-event
handling (explicit request): this widget only emits `modeChanged`,
never touches a QGraphicsView/QGraphicsScene itself -- see
power_intent_diagram_view.py's `_PowerIntentGraphicsView.set_mode()` for
what each mode actually *does* (cursor shape, mouse-event routing).

Pan (explicit request, added on a follow-up): click-drag-to-pan, the
gesture Regular Cursor itself used to perform by default before this
palette existed (an `OpenHandCursor`/`ClosedHandCursor` mixup this
follow-up corrects) -- see `hierarchy_diagram_view.py`'s own
`_pan_enabled()` hook for how the base class's pre-existing pan-drag
machinery is gated behind this mode specifically, rather than being
reimplemented here.

Icons are the real provided SVG assets (`diagram_tool_icons.py`,
rendered from `frontend/icons/`), not hand-drawn placeholders -- kept in
that dedicated module specifically so a future asset swap only touches
it, never this file.

Every button is a plain checkable QAction/QToolButton, Supply Port
included -- explicit request superseding an earlier drag-and-drop
placement mechanism ("REMOVE the drag-and-drop placement mechanism
entirely... it is not working reliably"): placement is now hover+click
directly on the Diagram View's own canvas while this mode is active
(see `_PowerIntentGraphicsView`'s own module docstring), so the Supply
Port button no longer needs to double as a `QDrag` source the way it
briefly did in an earlier pass.
"""

from __future__ import annotations

from enum import Enum, auto
from typing import Optional

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import QToolBar, QWidget

from frontend.src.power_intent_view.diagram_tool_icons import (
    cursor_icon,
    internal_supply_icon,
    isolation_cell_icon,
    level_shifter_icon,
    pan_icon,
    power_switch_icon,
    retention_icon,
    supply_net_icon,
    supply_port_icon,
    virtual_supply_icon,
    zoom_lens_icon,
)

_ICON_SIZE = 21  # explicit request: 50% bigger than the previous 14px size


class DiagramMode(Enum):
    SELECT = auto()
    PAN = auto()
    ZOOM_LENS = auto()
    SUPPLY_PORT = auto()
    INTERNAL_SUPPLY = auto()
    VIRTUAL_SUPPLY = auto()
    SUPPLY_NET = auto()
    POWER_SWITCH = auto()
    ISOLATION_CELL = auto()
    LEVEL_SHIFTER = auto()
    RETENTION = auto()


class DiagramToolPalette(QToolBar):
    modeChanged = Signal(DiagramMode)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setOrientation(Qt.Orientation.Vertical)
        self.setMovable(False)
        self.setFloatable(False)
        self.setIconSize(QSize(_ICON_SIZE, _ICON_SIZE))
        # Padding scaled proportionally with _ICON_SIZE (2px at the
        # previous 14px size -> 3px at this one) -- QToolBar's own
        # default QToolButton padding is sized for a larger icon than
        # either of these and would look disproportionate otherwise.
        self.setStyleSheet("QToolButton { padding: 3px; }")

        self._action_group = QActionGroup(self)
        self._action_group.setExclusive(True)

        self.select_action = self._add_mode_action("Regular Cursor", cursor_icon(_ICON_SIZE), DiagramMode.SELECT)
        self.pan_action = self._add_mode_action("Pan", pan_icon(_ICON_SIZE), DiagramMode.PAN)
        self.zoom_lens_action = self._add_mode_action("Zoom Lens", zoom_lens_icon(_ICON_SIZE), DiagramMode.ZOOM_LENS)

        self.addSeparator()

        self.supply_port_action = self._add_mode_action("Supply Port", supply_port_icon(_ICON_SIZE), DiagramMode.SUPPLY_PORT)
        self.internal_supply_action = self._add_mode_action("Internal Supply", internal_supply_icon(_ICON_SIZE), DiagramMode.INTERNAL_SUPPLY)
        self.virtual_supply_action = self._add_mode_action("Virtual Supply", virtual_supply_icon(_ICON_SIZE), DiagramMode.VIRTUAL_SUPPLY)
        self.supply_net_action = self._add_mode_action("Supply Net", supply_net_icon(_ICON_SIZE), DiagramMode.SUPPLY_NET)

        self.addSeparator()

        self.power_switch_action = self._add_mode_action("Power Switch", power_switch_icon(_ICON_SIZE), DiagramMode.POWER_SWITCH)

        self.addSeparator()

        self.isolation_cell_action = self._add_mode_action("Isolation Cell", isolation_cell_icon(_ICON_SIZE), DiagramMode.ISOLATION_CELL)
        self.level_shifter_action = self._add_mode_action("Level Shifter", level_shifter_icon(_ICON_SIZE), DiagramMode.LEVEL_SHIFTER)
        self.retention_action = self._add_mode_action("Retention", retention_icon(_ICON_SIZE), DiagramMode.RETENTION)

        self.select_action.setChecked(True)  # default mode

    def _add_mode_action(self, label, icon, mode: DiagramMode) -> QAction:
        action = QAction(icon, label, self)
        action.setCheckable(True)
        action.setToolTip(label)
        # toggled(True), not triggered() -- fires exactly when this
        # action *becomes* the checked one (whether by user click or,
        # for tests, a direct setChecked(True)), never redundantly for
        # the sibling action losing checked state in the same exclusive
        # group (that one gets its own toggled(False), filtered out here).
        action.toggled.connect(lambda checked, m=mode: self.modeChanged.emit(m) if checked else None)
        # The mode carried ON the action, so "which button selects this
        # mode" is answerable without a second, hand-maintained mapping
        # that could drift out of step with the buttons themselves.
        action.setData(mode)
        self._action_group.addAction(action)
        self.addAction(action)
        return action

    def action_for_mode(self, mode: DiagramMode) -> Optional[QAction]:
        """The toolbar action that selects `mode`, or None if no button
        offers it -- which would make that mode unreachable from the
        palette entirely."""
        return next((a for a in self._action_group.actions() if a.data() == mode), None)
