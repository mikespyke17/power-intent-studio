"""Attach an existing UPF or CPF power intent file directly to a specific
MACRO INSTANCE (a Liberty `.lib` black-box block) -- explicit request.
Scoped PER MACRO INSTANCE (hierarchical instance `.path`), never per
Liberty cell/macro type: two instances of the same macro carry
independently different (or no) attached file, since the attachment key
is the instance's own path, not `definition.definition_id`.

Diagram-only for this pass, same "mock-first" scoping decision already
established for every other diagram-only construct in this app (see
supply_port_overlay.py's own module docstring) -- nothing here is
reflected into the tab's real PowerIntentDesign.

**Path storage: absolute, not relative** -- same convention every other
loaded-file reference in this app already follows (RTL/.lib files, the
Load Power Intent tab's own entry-point file: all QFileDialog-picked
absolute paths, stored and compared as-is, never normalized to relative
at storage time). Relative-when-possible is instead computed only at
UPF/CPF GENERATION time, relative to the *output* file's own directory
-- the one real, well-defined base a generated `load_upf -file <path>`
line is ever actually interpreted against (matching Tcl `source`-style
relative resolution -- see CLAUDE.md's Milestone 2a `load_upf`
relative-path fix, and `install_relative_source`). See
`relative_or_absolute_path()` below.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from PySide6.QtCore import QPointF, QRectF, QSizeF
from PySide6.QtGui import QColor, QFont, QFontMetricsF
from PySide6.QtWidgets import QGraphicsItem

from frontend.src.power_intent_view.supply_port_overlay import INTERIOR_TOP_MARGIN_FOR_SUPPLY_PORT, SupplyPortIconItem
from frontend.src.shared.diagram.hierarchy_scene_builder import InstanceBoxItem


@dataclass
class MacroPowerIntentDiagramState:
    """Shared, Qt-independent state for an entire Power Intent Diagram
    (every tab -- same cross-tab-sharing mechanism as every other
    diagram-only state here, e.g. PortAttributeDiagramState/
    RetentionDiagramState). One instance is constructed once by
    PowerIntentDiagramView and handed to every _PowerIntentGraphicsView
    it creates.

    `attachments` maps a macro instance's own hierarchy `.path` to the
    ABSOLUTE path of the UPF or CPF file attached to it. Absence from
    this dict means no file is attached -- there is no explicit "None"
    entry the way PortAttributeDiagramState distinguishes "unassigned"
    from "never visited"; removing an attachment just deletes its key."""

    attachments: dict[str, str] = field(default_factory=dict)


def copy_macro_power_intent_diagram_state(state: MacroPowerIntentDiagramState) -> MacroPowerIntentDiagramState:
    """An independent snapshot -- same "why" as every other diagram-only
    state's own copy_*_diagram_state(): Ctrl+Z/Ctrl+Y undo/redo needs a
    genuinely separate copy, not one still sharing the same mutable
    `attachments` dict object with the live state."""
    return MacroPowerIntentDiagramState(attachments=dict(state.attachments))


def restore_macro_power_intent_diagram_state(state: MacroPowerIntentDiagramState, snapshot: MacroPowerIntentDiagramState) -> None:
    """Copies `snapshot`'s fields onto the *live* `state` object in
    place -- never replaces `state` itself, since it's shared by
    reference across every tab (see the class's own docstring)."""
    restored = copy_macro_power_intent_diagram_state(snapshot)
    state.attachments = restored.attachments


def attached_file_format(file_path: str) -> str:
    """"upf" or "cpf", inferred from `file_path`'s own extension --
    same convention _infer_source_format() (load_power_intent_tab.py)
    already uses for the real "Load Power Intent" file picker, no new
    detection logic invented here. Defaults to "upf" for any other/
    unrecognized extension (matches that function's own fallback), since
    a file picker filtered to *.upf/*.cpf should never realistically
    produce anything else."""
    return "cpf" if file_path.lower().endswith(".cpf") else "upf"


def relative_or_absolute_path(file_path: str, output_dir: str) -> str:
    """Relative-when-possible (explicit request, leaning toward portable
    generated output): `file_path` expressed relative to `output_dir`
    (the directory the generated UPF/CPF file is itself being written
    into -- the one real, well-defined base a `load_upf -file <path>`
    line in that file is ever interpreted against). Falls back to the
    original absolute path when no relative path exists at all (e.g. a
    different drive on Windows) -- never raises."""
    try:
        return os.path.relpath(file_path, output_dir)
    except ValueError:
        return file_path


# --- diagram label (explicit follow-up request) -------------------------


_MACRO_POWER_INTENT_BADGE_PADDING = 3.0
_MACRO_POWER_INTENT_BADGE_MARGIN = 4.0  # same corner-inset convention DomainInfoBoxItem's own _LABEL_MARGIN uses


def _macro_power_intent_badge_font() -> QFont:
    """Plain, small -- deliberately NOT bold/italic like the domain-info
    box's own name/Supply-Set lines (power_domain_overlay.py), so this
    badge doesn't visually compete with that box when both are present
    on the same block (see MacroPowerIntentBadgeItem's own docstring for
    the collision-avoidance story)."""
    font = QFont()
    font.setPointSizeF(font.pointSizeF() * 0.85)
    return font


def macro_power_intent_badge_text(file_path: str) -> str:
    return f"[{attached_file_format(file_path).upper()}]"


def macro_power_intent_badge_required_height() -> float:
    """The vertical room power_intent_diagram_view.py's own
    _extra_interior_bottom() must reserve, per attached macro, for this
    badge -- BOTTOM, not top, since it anchors bottom-right (mirrors
    domain_info_box_required_height()'s own reasoning exactly). Unlike
    that function, this needs no per-instance recomputation: the badge's
    own text is always exactly "[UPF]" or "[CPF]" -- same length either
    way -- so one fixed figure (computed once, not a hand-kept-in-sync
    constant) covers every attachment."""
    metrics = QFontMetricsF(_macro_power_intent_badge_font())
    rect = metrics.boundingRect("[UPF]")
    return rect.height() + 2 * _MACRO_POWER_INTENT_BADGE_PADDING


def _owner_box_has_ground_anchor(owner_box: InstanceBoxItem) -> bool:
    """Same check DomainInfoBoxItem._owner_box_has_ground_anchor()
    (power_domain_overlay.py) already makes -- duplicated, not shared,
    per this project's own established small-overlap-duplication
    precedent (see e.g. resolve_retention_color_source()/
    resolve_supply_set_color_source(), each deliberately duplicated
    rather than factored out)."""
    return any(isinstance(child, SupplyPortIconItem) and child.placement.edge == "bottom" for child in owner_box.childItems())


class MacroPowerIntentBadgeItem(QGraphicsItem):
    """A small "[UPF]"/"[CPF]" label anchored to a macro instance's own
    BOTTOM-RIGHT corner, showing the FORMAT of whichever power intent
    file is CURRENTLY attached to it (see MacroPowerIntentDiagramState's
    own docstring) -- explicit follow-up request. Purely a live, read-
    only reflection of `attachments[owner_box.path]`; there is no
    independent state on this item itself worth snapshotting for undo --
    build_macro_power_intent_badge_overlay() below always rematerializes
    it fresh from the SAME diagram-only state Ctrl+Z/Ctrl+Y already
    restores, the same "no materialized-item state of its own" shape
    RetentionBadgeIconItem/IsolationBadgeIconItem already establish.

    Room for this badge is reserved via power_intent_diagram_view.py's
    own _extra_interior_bottom() (macro_power_intent_badge_required_height(),
    summed alongside every other independent reason that mechanism
    already covers -- Ground-anchor clearance, the merged Power Domain
    info box) -- the SAME shared "make room on insertion" mechanism
    every other element type here already funnels through, verified
    against test_block_expansion_on_insertion.py's own permanent
    regression suite.

    **Collision avoidance, reusing the EXISTING mechanism rather than a
    new one**: this shifts itself up by `INTERIOR_TOP_MARGIN_FOR_SUPPLY_PORT`
    whenever `owner_box` currently has a Ground-anchored (edge=="bottom")
    Supply Port/Internal Supply/macro PG pin -- the exact same live-
    scene-graph check (`_owner_box_has_ground_anchor()` above) and
    exact same reserved figure DomainInfoBoxItem's own `_reposition()`
    already uses for the identical reason. `DomainInfoBoxItem` was, in
    turn, given a matching check of its OWN for THIS item (see its own
    `_owner_box_macro_power_intent_badge_height()`) -- generalizing that
    same one collision-avoidance mechanism to a second occupant type,
    not building a separate one: this badge always occupies the BOTTOM-
    most band (right above any Ground anchor), and a domain-info box on
    the SAME block (only reachable today via direct diagram-state
    construction -- a macro can never itself OWN a Power Domain through
    the normal UI flow, see power_intent_diagram_view.py's own
    `_MACRO_IN_MULTI_SELECT_ERROR`/`create_domain_action` gating, but the
    layout math stays correct regardless) shifts up further still to
    clear it."""

    def __init__(self, owner_box: InstanceBoxItem, file_path: str):
        super().__init__(owner_box)
        self.owner_box = owner_box
        self._font = _macro_power_intent_badge_font()
        self._metrics = QFontMetricsF(self._font)
        self._text = macro_power_intent_badge_text(file_path)
        self.setZValue(1)  # above the box's own translucent domain fill, same as every other overlay item parented to it
        self._reposition()
        owner_box.on_resize_callbacks.append(self._reposition)

    def _content_size(self) -> QSizeF:
        rect = self._metrics.boundingRect(self._text)
        return QSizeF(rect.width() + 2 * _MACRO_POWER_INTENT_BADGE_PADDING, rect.height() + 2 * _MACRO_POWER_INTENT_BADGE_PADDING)

    def boundingRect(self) -> QRectF:
        size = self._content_size()
        return QRectF(0, 0, size.width(), size.height())

    def paint(self, painter, option, widget=None) -> None:
        text_rect = self._metrics.boundingRect(self._text)
        baseline_y = _MACRO_POWER_INTENT_BADGE_PADDING - text_rect.top()
        painter.setFont(self._font)
        painter.setPen(QColor("#5f5e5a"))  # same neutral grey the app's own SVG glyph set already uses (see diagram_tool_icons.py)
        painter.drawText(QPointF(_MACRO_POWER_INTENT_BADGE_PADDING, baseline_y), self._text)

    def _reposition(self) -> None:
        """Registered on the box's own on_resize_callbacks -- same
        precedent DomainInfoBoxItem's own `_reposition()` already
        establishes -- so a LATER resize of the SAME box (e.g. the "make
        room" expansion this badge's own presence just triggered, or an
        unrelated later insertion) keeps it correctly anchored."""
        rect = self.owner_box.rect()
        size = self._content_size()
        ground_anchor_reserved = INTERIOR_TOP_MARGIN_FOR_SUPPLY_PORT if _owner_box_has_ground_anchor(self.owner_box) else 0.0
        self.setPos(
            rect.x() + rect.width() - size.width() - _MACRO_POWER_INTENT_BADGE_MARGIN,
            rect.y() + rect.height() - size.height() - _MACRO_POWER_INTENT_BADGE_MARGIN - ground_anchor_reserved,
        )


def build_macro_power_intent_badge_overlay(boxes: dict[str, InstanceBoxItem], attachments: dict[str, str]) -> dict[str, MacroPowerIntentBadgeItem]:
    """Materializes one MacroPowerIntentBadgeItem per attached macro
    instance whose own box is currently visible/materialized (a hidden
    box's own attachment simply isn't rendered this pass -- same
    precedent build_supply_port_overlay() already establishes for a
    hidden owner). Keyed by instance path, mirroring every other
    per-path overlay dict in this module family."""
    badges: dict[str, MacroPowerIntentBadgeItem] = {}
    for path, file_path in attachments.items():
        box = boxes.get(path)
        if box is None:
            continue
        badges[path] = MacroPowerIntentBadgeItem(box, file_path)
    return badges
