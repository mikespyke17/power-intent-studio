"""Properties panel for the Visualize Design tab's "Properties" group box.

Two tabs, each showing a different slice of the *currently selected set*
of hierarchy nodes (via HierarchySelectionController -- selectionChanged
carries a frozenset[str] of paths, not a single one, since Ctrl+click
multi-select is symmetric across the Tree and Diagram; see
hierarchy_tree_widget.py's own docstring). With more than one node
selected, both tabs show the *union* across every selected node -- not
just the most-recently-clicked one.

**No per-row Path column anymore** (explicit request, removed from both
tables): a single "Path: <scope>" label (`_make_path_label()`/
`_set_path_label_text()`), the selected node path(s) in *italic*,
comma-joined for a multi-selection, sits between the filter bar and the
table instead. Accepted tradeoff of this change, worth naming
explicitly: two selected nodes whose own rows would otherwise be
identical except for *which* node they came from (e.g. both happening to
have a "clk" port, or -- Sequential -- a same-named register at two
different paths) are no longer disambiguated row-by-row; the label above
still names every contributing path, just not per-row anymore.
  - PortList: Name/Dir/Width, one row per port *of each selected node's
    own Definition* (not recursive into children -- unlike Sequential
    below), bold header row, Dir/Width centered, sortable by any column,
    with a bottom-right Filter button (Input/Output/Inout/PG checkboxes,
    all on by default). Also includes one row per signal
    exposed through each selected node's interface-typed ports
    (Definition.interface_ports) -- an interface is a bundle of wiring
    with modport constraints, not a block of logic, so it never gets its
    own Tree/Diagram entry (see
    hierarchy_tree_widget.py/hierarchy_scene_builder.py); its content
    surfaces here instead. Named "<port_name>.<signal_name>" (the
    *local* interface port's own name, e.g. "dbus", not the interface
    type's base name -- a module can have two ports of the same
    interface type, e.g. cpu_subsys.sv's `s0`/`s1`, both `axi_lite_if`,
    and only the local name disambiguates them). Direction comes from
    the *modport* actually bound at that port (the same signal is
    `output` from one modport and `input` from another), resolved by
    looking up the interface's own Definition (by base_name, among the
    whole Design -- see InterfacePort's own docstring for why there's no
    direct definition_id to follow) and finding the named modport there.
    A genuinely unconstrained interface port (no modport at declaration)
    first tries `InterfacePort.inferred_modport` -- a modport *derived* on
    the backend by tracing this exact port through pure pass-through
    wiring to a modport-qualified child instance inside the same module's
    own body (e.g. `axi_lite_if mem_port` immediately forwarded to
    `axi_lite_mux2 u_mux (.m(mem_port))`, whose own `m` port is declared
    `axi_lite_if.mst m` -- a real, common RTL idiom found in practice: a
    forwarding module leaves its own port unconstrained, but the actual
    consumer downstream is always modport-qualified). Only when no such
    inference was possible (no forwarding child found, or more than one
    child disagreeing on the modport -- genuinely ambiguous) does this
    fall back to the interface's own declared ports (their real direction)
    plus its internal nets, shown as INOUT since nothing constrains
    read/write access to them -- an explicit, flagged assumption, not
    modeled from real per-signal semantics.
  - "Sequential" (tab title -- originally "Sequential", renamed to
    "Sequential Elements" on an earlier explicit request, then back to
    "Sequential" on a later tab rename/reorder pass, explicit request):
    merges the previous separate Register/Latch tabs into one Name/Type
    table (Type is "Register" or "Latch"), bold header row, same
    Filter-button pattern (Register/Latch checkboxes).
    Recursive: shows every SequentialElement in the *entire* subtree of
    *every* selected node (each selected node's own Definition plus
    every descendant instance's) -- a node whose subtree overlaps
    another selected node's (e.g. both a parent and its own child are
    selected) never double-counts an element, since this still walks
    the whole Design exactly once and checks membership against the
    *set* of selected roots, not once per root (unrelated to, and
    unaffected by, the Path column's own removal above -- dedup was
    always a property of walking the whole Design once, never of having
    a Path column to compare against). **Name cells are scope-prefixed**
    ("<scope>/<name>", e.g. "top/u_core/out1" -- explicit follow-up
    request, consistent with how elements are already displayed in the
    Retention Strategy dialog's own Elements list): a partial reversal
    of the per-row Path removal above, but embedded into the existing
    single Name column rather than reintroducing a separate column --
    the "Path: <scope>" label above the table still names every
    contributing selected node too.

A third "General" tab (Module Name / Hier Path / Type, plain labels)
existed here previously and was removed on explicit request -- judged
not worth the space it took up.

Both tables auto-resize columns to their content
(QHeaderView.ResizeMode.ResizeToContents).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QFont, QFontMetrics
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from frontend.src.hierarchy_view.selection_controller import HierarchySelectionController
from frontend.src.shared.models.rtl import Design, HierarchyNode, PortDirection
from frontend.src.shared.models.rtl_io import module_io_ports
from frontend.src.shared.widgets.filter_button import FilterButton
from frontend.src.shared.widgets.table_filter import TableFilterBar

_DIR_DISPLAY = {
    PortDirection.IN: "IN",
    PortDirection.OUT: "OUT",
    PortDirection.INOUT: "INOUT",
    PortDirection.PG: "PG",
    PortDirection.PG_INTERNAL: "PG Internal",
}

_DIR_FILTER_GROUPS = {
    "Input": {PortDirection.IN},
    "Output": {PortDirection.OUT},
    "Inout": {PortDirection.INOUT},
    # No "PG" group (explicit request): a macro's own PG/PG_INTERNAL pins
    # are excluded from PortList entirely, not just filterable -- they're
    # already shown on the Supply Ports tab (properties_panel.py), and
    # showing them here too would also mean giving them a meaningless
    # "Driver/Receiver Supply Set" (they ARE the supply, not a signal
    # that traces to one). Since `allowed` (below) is built purely from
    # this dict's own values, omitting the group here is enough --  no
    # separate exclusion check is needed in _refresh().
}

_SEQ_FILTER_GROUPS = {"Register": False, "Latch": True}  # category -> SequentialElement.is_latch

_NOT_APPLICABLE = "-"


@dataclass(frozen=True)
class ConnectivityCellDisplay:
    """PortList's "Receiver Supply Set"/"Driver Supply Set" columns are
    power-intent-flavored data (a signal's traced driver/receiver cell,
    resolved to a Power Domain's own Primary Supply Set) -- but this
    module lives in hierarchy_view/ (Front-End Milestone 1) and must
    stay power-intent-agnostic, the same milestone-directory separation
    CLAUDE.md already draws between hierarchy_view/ and power_intent_view/.
    So the actual trace/resolution (power_intent_view/connectivity_flatten.py
    + power_resolution_cache.py) happens entirely OUTSIDE this module;
    what crosses in here is this plain (text, italic, italic_prefix_lens)
    shape only, keyed by (path, port_name) -- see
    _PortListPanel.set_connectivity().

    `italic_prefix_lens` is a per-LINE tuple (`text.split("\\n")`, one
    entry per line): when non-empty AND its length matches the actual
    number of lines, each entry styles the first N characters of THAT
    line in italic (e.g. "External - " ahead of a Port-Attribute-
    assigned Supply Set name) while the rest of that line renders in the
    table's normal font -- see power_resolution_cache.py's own
    ResolvedSupplyDisplay docstring for the full rationale (including
    why a multi-bit port's own per-bit-disagreeing assignments can now
    produce more than one such line), and _PartialItalicPrefixDelegate
    below for how it's actually painted (QTableWidgetItem's own QFont
    can only style a whole cell, so this one case needs a real
    per-substring, per-line paint path)."""

    text: str
    italic: bool = False
    italic_prefix_lens: tuple[int, ...] = ()


# Custom Qt.ItemDataRole carrying ConnectivityCellDisplay.italic_prefix_lens
# onto the QTableWidgetItem itself -- _PartialItalicPrefixDelegate below
# reads it back at paint time. Not a real column value, so a UserRole
# offset (never collides with a real Qt built-in role) rather than one of
# the DisplayRole/FontRole/etc. roles Qt's own default delegate already
# interprets on its own.
_ITALIC_PREFIX_LENS_ROLE = Qt.ItemDataRole.UserRole + 1


class _PartialItalicPrefixDelegate(QStyledItemDelegate):
    """Renders the first N characters of EACH line of a cell's text in
    italic while the rest of that line stays normal (N = the matching
    entry of _ITALIC_PREFIX_LENS_ROLE, one entry per "\\n"-split line),
    e.g. "External - " ahead of a Port-Attribute-assigned Supply Set
    name -- the one genuine per-substring styling need in this table
    (see ConnectivityCellDisplay's own docstring: every OTHER marker
    uses whole-cell italic, which QTableWidgetItem's own QFont already
    handles via the default delegate). Falls back to the default
    delegate's own paint() whenever no prefix tuple was recorded, or its
    length doesn't match the cell's actual line count (defensive -- see
    ConnectivityCellDisplay's own docstring)."""

    def paint(self, painter, option, index) -> None:
        prefix_lens = index.data(_ITALIC_PREFIX_LENS_ROLE)
        text = index.data(Qt.ItemDataRole.DisplayRole)
        lines = text.split("\n") if text else []
        if not prefix_lens or len(prefix_lens) != len(lines):
            super().paint(painter, option, index)
            return

        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        style = opt.widget.style() if opt.widget is not None else QApplication.style()
        # Let the real style draw everything (background, selection
        # highlight, focus rect) EXCEPT the text itself -- text is drawn
        # manually below, split across the two fonts, line by line.
        opt.text = ""
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)

        italic_font = QFont(opt.font)
        italic_font.setItalic(True)
        normal_font = QFont(opt.font)
        line_height = QFontMetrics(normal_font).height()

        margin = style.pixelMetric(QStyle.PixelMetric.PM_FocusFrameHMargin, opt, opt.widget) + 1
        content_rect = opt.rect.adjusted(margin, 0, -margin, 0)
        total_height = line_height * len(lines)
        top = content_rect.top() + max(0, (content_rect.height() - total_height) // 2)

        painter.save()
        painter.setClipRect(opt.rect)
        painter.setPen(opt.palette.color(opt.palette.currentColorGroup(), opt.palette.ColorRole.HighlightedText if opt.state & QStyle.StateFlag.State_Selected else opt.palette.ColorRole.Text))

        for line, prefix_len in zip(lines, prefix_lens):
            prefix, rest = line[:prefix_len], line[prefix_len:]
            line_rect = QRect(content_rect.left(), top, content_rect.width(), line_height)
            prefix_width = QFontMetrics(italic_font).horizontalAdvance(prefix)
            prefix_rect = QRect(line_rect.left(), line_rect.top(), prefix_width, line_rect.height())
            rest_rect = QRect(prefix_rect.right(), line_rect.top(), max(0, line_rect.right() - prefix_rect.right()), line_rect.height())

            painter.setFont(italic_font)
            painter.drawText(prefix_rect, int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), prefix)
            painter.setFont(normal_font)
            painter.drawText(rest_rect, int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), rest)
            top += line_height
        painter.restore()


def _bold_font() -> QFont:
    font = QFont()
    font.setBold(True)
    return font


def _make_path_label() -> QLabel:
    """"Path: <scope>" (explicit request), positioned between the filter
    bar and the table -- replaces the per-row Path column both tables
    used to carry (see _set_path_label_text() for what it shows for a
    multi-selection)."""
    label = QLabel()
    label.setTextFormat(Qt.TextFormat.RichText)
    return label


def _set_path_label_text(label: QLabel, paths) -> None:
    """`paths` in *italic* font, comma-joined when more than one node is
    selected (Ctrl+click multi-select) -- "-" (still italic) for no
    selection at all, matching this codebase's own established "-" for
    nothing" convention."""
    scope = ", ".join(sorted(paths)) if paths else "-"
    label.setText(f"Path: <i>{scope}</i>")


def _make_table(headers: list[str], bold_header: bool = False) -> QTableWidget:
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    if bold_header:
        table.horizontalHeader().setFont(_bold_font())
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    return table


def _centered_text_item(text: str) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
    return item


def _numeric_centered_item(value: int) -> QTableWidgetItem:
    """Sorts numerically, not lexicographically -- a plain string item
    would sort '10' before '2'."""
    item = QTableWidgetItem()
    item.setData(Qt.ItemDataRole.DisplayRole, value)
    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
    return item


class _PortListPanel(QWidget):
    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._design: Optional[Design] = None
        self._nodes: list[HierarchyNode] = []
        # Populated only by a power-intent-aware caller (see
        # set_connectivity()'s own docstring) -- stay empty (every cell
        # renders "-") for a caller that never calls it at all, e.g. the
        # hidden VisualizeDesignTab's own copy of this same class, which
        # has no Power Domain/Supply Set context to resolve against.
        self._receiver_by_key: dict[tuple[str, str], ConnectivityCellDisplay] = {}
        self._driver_by_key: dict[tuple[str, str], ConnectivityCellDisplay] = {}
        self.table = _make_table(["Name", "Dir", "Width", "Receiver Supply Set", "Driver Supply Set"], bold_header=True)
        self.table.setSortingEnabled(True)
        # Real, confirmed bug (a Receiver/Driver Supply Set cell can hold
        # several newline-joined lines, e.g. a macro pin fanning out to
        # both the design's own top-level boundary AND real internal RTL
        # -- see power_resolution_cache.py's own "\n".join() in
        # _resolve_receiver()): word wrap on is what makes Qt's default
        # item delegate render each embedded "\n" as its own line instead
        # of eliding the cell to single-line height -- same convention
        # already established for the Power Domain/Supply Set tables in
        # power_intent_view/properties_panel.py (see that module's own
        # _make_table(), which sets this on every table it builds). Set
        # directly on this instance rather than inside the SHARED
        # _make_table() factory above -- _SequentialPanel below reuses
        # that same factory for a table that never holds multi-line
        # content, so scoping this to _PortListPanel's own table keeps
        # the change from touching unrelated tables.
        self.table.setWordWrap(True)
        # Receiver/Driver Supply Set columns (3/4) are the only cells
        # that ever need the "External - " partial-italic prefix -- see
        # _PartialItalicPrefixDelegate's own docstring. Kept as an
        # instance attribute so it isn't garbage-collected out from under
        # the table (Qt's own item-view delegate ownership is a bare
        # C++-side pointer, not a Python reference).
        self._connectivity_delegate = _PartialItalicPrefixDelegate(self.table)
        self.table.setItemDelegateForColumn(3, self._connectivity_delegate)
        self.table.setItemDelegateForColumn(4, self._connectivity_delegate)
        # "Visualize" (renamed from "Filter", explicit request) -- an
        # "All" entry (default) shows everything and stays in sync with
        # the individual category checkboxes below it; see FilterButton's
        # own docstring for the sync behavior.
        self.filter_button = FilterButton(list(_DIR_FILTER_GROUPS), self._refresh, include_all=True, label="Visualize")
        # Explicit request: a free-text filter on top of the existing
        # category checkboxes above -- the two are complementary (see
        # shared/widgets/table_filter.py's own module docstring), not a
        # replacement for either.
        self.table_filter = TableFilterBar(self.table)
        self.path_label = _make_path_label()

        filter_row = QHBoxLayout()
        filter_row.addStretch(1)
        filter_row.addWidget(self.filter_button)

        layout = QVBoxLayout(self)
        layout.addWidget(self.table_filter)
        layout.addWidget(self.path_label)
        layout.addWidget(self.table)
        layout.addLayout(filter_row)

    def load(self, design: Optional[Design], nodes: list[HierarchyNode]) -> None:
        self._design = design
        self._nodes = list(nodes)
        self._refresh()

    def set_connectivity(
        self,
        receiver_by_key: dict[tuple[str, str], ConnectivityCellDisplay],
        driver_by_key: dict[tuple[str, str], ConnectivityCellDisplay],
    ) -> None:
        """Wholesale-replaces the resolved Driver/Receiver Supply Set
        lookups (see ConnectivityCellDisplay's own docstring for why
        this crosses in as plain data) and re-renders immediately --
        called by power_intent_view/properties_panel.py every time its
        own structural or resolution cache changes (a design load, or
        any Power Domain/Supply Set/Port Attribute edit)."""
        self._receiver_by_key = receiver_by_key
        self._driver_by_key = driver_by_key
        self._refresh()

    def _connectivity_item(self, lookup: dict[tuple[str, str], ConnectivityCellDisplay], key: Optional[tuple[str, str]]) -> QTableWidgetItem:
        display = lookup.get(key) if key is not None else None
        item = QTableWidgetItem(display.text if display is not None else _NOT_APPLICABLE)
        if display is not None and display.italic:
            font = item.font()
            font.setItalic(True)
            item.setFont(font)
        if display is not None and display.italic_prefix_lens:
            item.setData(_ITALIC_PREFIX_LENS_ROLE, display.italic_prefix_lens)
        return item

    def _refresh(self) -> None:
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        active = self.filter_button.active_categories()
        allowed = {d for name in active for d in _DIR_FILTER_GROUPS[name]}
        # `key` is (path, port_name) for a real port row, or
        # (path, "iface_port[i].signal") for an interface-exposed signal
        # row -- both are looked up in the Driver/Receiver dicts below
        # (see ConnectivityCellDisplay's own docstring); a key with no
        # entry in either dict (e.g. no power-intent-aware caller has
        # ever called set_connectivity()) renders "-".
        rows: list[tuple[str, PortDirection, int, Optional[tuple[str, str]]]] = []
        for node in self._nodes:
            definition = node.definition
            # Plain ports AND interface-exposed signals -- the shared
            # expansion every other I/O list uses too (rtl_io).
            rows.extend(
                (p.name, p.direction, p.width, (node.path, p.name))
                for p in module_io_ports(self._design, definition)
                if p.direction in allowed
            )
        self.table.setRowCount(len(rows))
        for row, (name, direction, width, key) in enumerate(rows):
            self.table.setItem(row, 0, QTableWidgetItem(name))
            self.table.setItem(row, 1, _centered_text_item(_DIR_DISPLAY[direction]))
            self.table.setItem(row, 2, _numeric_centered_item(width))
            self.table.setItem(row, 3, self._connectivity_item(self._receiver_by_key, key))
            self.table.setItem(row, 4, self._connectivity_item(self._driver_by_key, key))
        self.table.setSortingEnabled(True)
        # Word wrap alone (see __init__'s own comment) isn't enough --
        # Qt doesn't retroactively grow a row's height for wrapped
        # content on its own; resizeRowsToContents() after every
        # repopulation is the other half, same "explicit resize after
        # every rebuild" convention power_intent_view/properties_panel.py
        # already established for its own multi-line cells.
        self.table.resizeRowsToContents()
        self.table_filter.reapply()
        _set_path_label_text(self.path_label, [node.path for node in self._nodes])


class _SequentialPanel(QWidget):
    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._design: Optional[Design] = None
        self._paths: frozenset[str] = frozenset()
        self.table = _make_table(["Name", "Type"], bold_header=True)
        self.table.setSortingEnabled(True)
        self.filter_button = FilterButton(list(_SEQ_FILTER_GROUPS), self._refresh, include_all=True, label="Visualize")
        # Explicit request: a free-text filter on top of the existing
        # category checkboxes above -- see _PortListPanel's own comment.
        self.table_filter = TableFilterBar(self.table)
        self.path_label = _make_path_label()

        filter_row = QHBoxLayout()
        filter_row.addStretch(1)
        filter_row.addWidget(self.filter_button)

        layout = QVBoxLayout(self)
        layout.addWidget(self.table_filter)
        layout.addWidget(self.path_label)
        layout.addWidget(self.table)
        layout.addLayout(filter_row)

    def load(self, design: Optional[Design], paths: frozenset[str]) -> None:
        self._design = design
        self._paths = frozenset(paths)
        self._refresh()

    def _refresh(self) -> None:
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        if self._design is None or not self._paths:
            self.table.setSortingEnabled(True)
            self.table_filter.reapply()
            _set_path_label_text(self.path_label, self._paths)
            return
        active = self.filter_button.active_categories()
        allowed_is_latch = {_SEQ_FILTER_GROUPS[name] for name in active}

        rows = []
        for node in self._design.walk():
            if not any(node.path == root or node.path.startswith(f"{root}/") for root in self._paths):
                continue  # outside every selected node's own subtree
            for element in node.definition.sequential_elements:
                if element.is_latch not in allowed_is_latch:
                    continue
                # Explicit request: show "<scope>/<name>" instead of the
                # bare element name -- consistent with how elements are
                # already displayed in the Retention Strategy dialog's
                # own Elements list (retention_dialog.py). This embeds
                # the scope into the existing single "Name" column
                # rather than reintroducing a separate per-row Path
                # column (explicitly removed earlier -- see this class's
                # own module docstring); the "Path: <scope>" label above
                # the table still shows the full selected-node list too.
                rows.append((f"{node.path}/{element.output_signal}", "Latch" if element.is_latch else "Register"))

        self.table.setRowCount(len(rows))
        for row, (name, kind) in enumerate(rows):
            self.table.setItem(row, 0, QTableWidgetItem(name))
            self.table.setItem(row, 1, QTableWidgetItem(kind))
        self.table.setSortingEnabled(True)
        self.table_filter.reapply()
        _set_path_label_text(self.path_label, self._paths)


class HierarchyPropertiesTableWidget(QTabWidget):
    def __init__(self, controller: HierarchySelectionController, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._design: Optional[Design] = None

        self._port_panel = _PortListPanel()
        self._sequential_panel = _SequentialPanel()

        # Explicit request (tab rename/reorder pass, Power Intent
        # Properties): renamed for consistency with
        # PowerIntentPropertiesPanel's own copies of these two panels
        # (properties_panel.py's own addTab() calls) -- these labels are
        # only actually visible if the hidden-but-kept VisualizeDesignTab
        # is ever restored (see main_window.py's own docstring), but
        # keeping both copies in sync avoids exactly the kind of
        # standalone-vs-nested-dialog drift this app has already hit and
        # fixed multiple times elsewhere.
        self.addTab(self._port_panel, "Port List")
        self.addTab(self._sequential_panel, "Sequential")

        controller.selectionChanged.connect(self.apply_selection)

    def load(self, design: Design) -> None:
        self._design = design
        self._port_panel.load(None, [])
        self._sequential_panel.load(None, frozenset())
        # A fresh design's Endpoints are entirely different from
        # whatever the previous design's own connectivity dicts were
        # keyed by -- clear rather than carry stale entries forward. A
        # power-intent-aware caller (PowerIntentPropertiesPanel) always
        # follows this with its own set_connectivity() call once it has
        # rebuilt its structural cache for the new design; a caller that
        # never calls set_connectivity() at all (VisualizeDesignTab) just
        # keeps showing "-" in both columns, which is correct for it.
        self._port_panel.set_connectivity({}, {})

    def set_connectivity(
        self,
        receiver_by_key: dict[tuple[str, str], ConnectivityCellDisplay],
        driver_by_key: dict[tuple[str, str], ConnectivityCellDisplay],
    ) -> None:
        self._port_panel.set_connectivity(receiver_by_key, driver_by_key)

    def apply_selection(self, paths) -> None:
        if self._design is None:
            return
        paths = frozenset(paths)
        selected_nodes = [n for n in self._design.walk() if n.path in paths]
        self._port_panel.load(self._design, selected_nodes)
        self._sequential_panel.load(self._design, paths)
