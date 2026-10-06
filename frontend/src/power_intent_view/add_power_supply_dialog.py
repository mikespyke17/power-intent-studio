"""'Add Power Supply' dialog: name + colour, triggered from the Power
Intent tab's Diagram right-click "Add Power Supply" action (see
power_intent_diagram_view.py's _extra_context_menu_actions()). Diagram-
only placement for now -- the resulting supply-port icon isn't wired
into the tab's PowerIntentDesign/Properties panel yet (see
supply_port_overlay.py's module docstring), so this dialog only ever
needs to hand back a name, a colour, and (for a plain Supply Port) a
Power States list, nothing construct-shaped.

Also the SAME dialog Create Power Domain's own inline Supply Port
creation flow uses (see create_power_domain_dialog.py's own
_SupplyNameCombo, "Add New...") -- one shared class, one shared States
list implementation, for both entry points.

Serves "Add/Edit Virtual Supply" too (`is_virtual_supply=True`,
explicit request) -- a Virtual Supply's Power States are AUTHORED
directly, exactly like a plain Supply Port's (never derived/inherited
the way Internal Supply's are), so it reuses the SAME `states_widget`
branch a plain Supply Port already gets (neither constructor parameter
changes which branch of the `if is_internal_supply:`/`else:` split at
the bottom of `__init__` runs) -- the only Virtual-Supply-specific
behavior is `accept()` additionally REJECTING an empty Power States
list (required, not optional, unlike a plain Supply Port's own
always-optional list).

Serves "Add/Edit Internal Supply" too (`is_internal_supply=True`) --
explicit request: an Internal Supply gets NO independent editable
Power States list (its own effective states are entirely DERIVED by
walking the connectivity chain back to a real source Supply Port -- see
power_switch_overlay.py's resolve_effective_states()/
trace_upstream_source()/describe_inherited_states()). Instead it shows
a read-only "Inherited Power States" preview, WITH per-line source
attribution (explicit follow-up request); the CALLER computes that
preview (via `inherited_state_lines`) before constructing this dialog --
this class stays a plain "hand back what the user typed" dialog with
zero connectivity-tracing knowledge of its own, same division of
responsibility already established for name/colour.

A single, FLAT layout (explicit revert of an earlier "General"/"Power
States" two-section QGroupBox reorganization -- that grouping is gone
again; Name, Colour, and the Power States table all sit together in
one continuous form, same as before that reorganization existed). What
DOES carry forward from that pass: the "Power States:" row label
itself, the "+"/"-" per-row icon buttons, centered cell alignment, and
the "equivalent Active states" validation (all live in
SupplyPortStatesWidget, untouched by this revert).

Bug fix, done AS PART OF this revert rather than left behind (explicit
request -- "this was the actual underlying problem the section split
was trying to work around"): SupplyPortStatesWidget's own table height
now tracks its row count exactly (no internal scrollbar), and emits
`rowsChanged` on every add/remove/replace -- connected here to
_sync_size_to_states_table(), which grows/shrinks the WINDOW itself (not
just the inner widget) to keep showing every row without clipping or
requiring a manual resize (the window stays freely resizable afterward,
same as any other dialog).

Bug fix, round two (explicit follow-up report -- the round-one fix
above turned out incomplete): _sync_size_to_states_table() computes the
dialog's own target height as an ABSOLUTE value (`_other_content_height`,
measured ONCE right after the layout is fully built, PLUS
states_widget.required_height() at call time) rather than an
incremental DELTA applied to self.height() -- confirmed empirically
(constructing the dialog, adding several rows, then inspecting actual
allocated widget geometries) that a delta-based resize() can silently
diverge from what the CONTENT needs: SupplyPortStatesWidget's own
row-buttons column used to include an addStretch(1) with no
counterbalancing constraint, which absorbed whatever EXTRA height
_sync_size_to_states_table() added to the window, growing this
widget's own ALLOCATED layout space well past what the table itself
actually needed (states_widget ended up ~339px tall for a 177px-tall
table) -- the window's OVERALL size looked plausible while the visible
CONTENT area was still effectively clipped/misallocated beneath it.
Fixed on two fronts: SupplyPortStatesWidget's own stretch is gone
(top-aligned instead, see its own docstring), AND this dialog now
computes its OWN absolute target size directly from
required_height() every time, rather than accumulating a delta that
could drift out of sync with whatever Qt's own nested layouts actually
did with the space. Also confirmed (unchanged from round one): QDialog's
own sizeHint() caches its FIRST-ever computed value and never correctly
re-derives it afterward, even after updateGeometry()/layout().invalidate()
calls at every level between the table and the dialog -- which is why
the "everything else" baseline below is captured ONCE, immediately
after construction (the one point sizeHint() is still reliably
accurate), rather than re-queried later.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QWidget,
)

from frontend.src.power_intent_view.supply_port_overlay import SupplyPortState
from frontend.src.power_intent_view.supply_port_states_widget import SupplyPortStatesWidget
from frontend.src.shared.widgets import message_box
from frontend.src.shared.widgets.target_hierarchy_label import APPLIES_TO_CAPTION, make_target_hierarchy_label

_DEFAULT_COLOR = QColor("#2196f3")

# UPF `create_supply_port -direction {in|out|inout}`. All three values
# verified against secondary UPF references before implementing (IEEE
# 1801 itself is paywalled -- the same source tier this project already
# qualifies every other UPF claim with); the sibling `create_logic_port`
# is independently documented with the identical three values in
# OpenROAD's own UPF command reference. Deliberately NOT extended beyond
# these three.
SUPPLY_PORT_DIRECTIONS = ("in", "out", "inout")
# Explicit request: a NEWLY created port opens on "in", so omitting the
# direction takes a deliberate act. Distinct from an IMPORTED port that
# carried no flag, which is "" -- see SupplyPortPlacement.direction.
DEFAULT_SUPPLY_PORT_DIRECTION = "in"
# What the empty choice reads as in the dropdown. A real selection
# meaning "omit -direction", not a placeholder for "nothing chosen".
DIRECTION_EMPTY_LABEL = "<empty>"

_NOT_CONNECTED_TEXT = "Inherited Power States: Not yet connected -- states will inherit once linked to a supply port."
_NO_INHERITED_STATES_TEXT = "Inherited Power States: No inherited states."
_INHERITED_STATES_HEADER = "Inherited Power States:"


class AddPowerSupplyDialog(QDialog):
    def __init__(
        self,
        parent: Optional[QWidget] = None,
        target_path: Optional[str] = None,
        is_internal_supply: bool = False,
        is_virtual_supply: bool = False,
        initial_states: Optional[list[SupplyPortState]] = None,
        inherited_state_lines: Optional[list[str]] = None,
        initial_direction: Optional[str] = None,
    ):
        super().__init__(parent)
        # Explicit request (Bug 5): every creation/edit dialog in this
        # app must be user-resizable, with an explicit, testable size
        # grip -- none of these dialogs ever set a fixed-size
        # constraint, so they were already technically resizable via
        # window-manager edge-dragging, but this makes the affordance
        # visible and gives isResizable() something concrete to assert
        # against in a test.
        self.setSizeGripEnabled(True)
        self.setWindowTitle("Add Power Supply")
        # Explicit bug report: the dialog was too narrow for the
        # embedded Power States table's own 3 columns (State Name/
        # Type/Voltage) to display without clipping. Bug fix (explicit
        # request, "nested Add New sized differently from standalone"):
        # this was previously gated on `not is_internal_supply`, so an
        # Internal Supply instance (no states table -- just the
        # "Inherited Power States" label) never got this width floor at
        # all and rendered noticeably narrower/more cramped than a
        # Supply Port instance -- both from the standalone "Add Internal
        # Supply" entry point AND every nested "Add New..." reached via
        # Create/Edit Power Domain's own Create Supply Set flow (see
        # _SupplyNameCombo._on_index_changed() -- same shared
        # AddPowerSupplyDialog class, no separate dialog exists). Now
        # unconditional, so every instance of this dialog -- Supply Port
        # or Internal Supply, standalone or nested -- gets the same
        # guaranteed minimum width.
        self.setMinimumWidth(420)
        self.color = QColor(_DEFAULT_COLOR)
        self._target_path = target_path
        self._is_internal_supply = is_internal_supply
        self._is_virtual_supply = is_virtual_supply

        self.name_edit = QLineEdit()
        # One example for all three kinds, because all three share this
        # dialog class and a rail is a rail whichever one it is.
        self.name_edit.setPlaceholderText("e.g. VDD")
        # Direction: SUPPLY PORT ONLY. Neither an Internal Supply nor a
        # Virtual Supply is a boundary port, so neither has a direction
        # to declare -- and because all three kinds share THIS dialog
        # class, the field has to be gated here rather than assumed
        # absent. Left as None for the other two so `direction` below
        # can tell "the user chose <empty>" from "this kind has no such
        # field at all" without consulting the flags again.
        self.direction_combo: Optional[QComboBox] = None
        if not is_internal_supply and not is_virtual_supply:
            self.direction_combo = QComboBox()
            # The empty entry is a real choice, not a placeholder: it is
            # how the user says "omit -direction from generation".
            self.direction_combo.addItem(DIRECTION_EMPTY_LABEL, "")
            for value in SUPPLY_PORT_DIRECTIONS:
                self.direction_combo.addItem(value, value)
            # Explicit request: default to "in", NOT <empty>. A newly
            # created port is an input supply far more often than not,
            # and omitting the direction has to be a deliberate act.
            # `initial_direction` is what EDIT pre-fills with, and it
            # must be honoured even when it is "" -- an imported port
            # with no `-direction` has to reopen on <empty>, not silently
            # acquire "in" the first time someone edits it. Hence the
            # `is None` check rather than a truthiness test.
            initial = (
                DEFAULT_SUPPLY_PORT_DIRECTION if initial_direction is None
                else initial_direction
            )
            index = self.direction_combo.findData(initial)
            # An unrecognised value (a file could carry anything) falls
            # back to <empty> rather than being silently coerced to "in".
            self.direction_combo.setCurrentIndex(index if index >= 0 else 0)
        self.color_button = QPushButton()
        self.color_button.clicked.connect(self._pick_color)
        self._refresh_color_button()

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        self._ok_button = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        self._ok_button.setEnabled(False)
        self.name_edit.textChanged.connect(self._on_name_changed)

        form = QFormLayout(self)
        if target_path is not None:
            form.addRow(APPLIES_TO_CAPTION, make_target_hierarchy_label([target_path]))
        form.addRow("Name:", self.name_edit)
        # Explicit request: BETWEEN Name and Colour.
        if self.direction_combo is not None:
            form.addRow("Direction:", self.direction_combo)
        form.addRow("Colour:", self.color_button)

        self.states_widget: Optional[SupplyPortStatesWidget] = None
        self.inherited_states_label: Optional[QLabel] = None
        self._other_content_height: Optional[int] = None
        if is_internal_supply:
            self.inherited_states_label = QLabel(self._inherited_states_text(inherited_state_lines))
            self.inherited_states_label.setWordWrap(True)
            form.addRow("Power States:", self.inherited_states_label)
        else:
            self.states_widget = SupplyPortStatesWidget()
            if initial_states:
                self.states_widget.states = list(initial_states)
            form.addRow("Power States:", self.states_widget)

        form.addRow(self.buttons)

        if self.states_widget is not None:
            # Bug fix (round two -- see this module's own docstring for
            # the full "why"): setFixedHeight(), not just relying on
            # sizeHint()/AlignTop, is what actually stops QFormLayout
            # from allocating this row MORE vertical space than the
            # table needs -- confirmed empirically that even with a
            # correct sizeHint() and FieldsStayAtSizeHint, the row's own
            # grid CELL still absorbed extra height whenever this widget's
            # own size policy was merely "Preferred" (not "Fixed"),
            # leaving harmless-looking but real wasted space below the
            # table. setFixedHeight() pins sizeHint()/minimumSizeHint()/
            # maximumSizeHint() all to the SAME value, leaving the layout
            # system no ambiguity to exploit.
            self.states_widget.setFixedHeight(self.states_widget.required_height())
            # The window itself must grow/shrink to keep every row
            # visible without clipping, not just the inner table --
            # measures a fixed baseline ONCE here (the layout is now
            # fully built, and this is the one point a fresh sizeHint()
            # query is still reliably accurate) rather than re-deriving
            # it later.
            self._other_content_height = self.sizeHint().height() - self.states_widget.height()
            self.states_widget.rowsChanged.connect(self._sync_size_to_states_table)

        if self.inherited_states_label is not None:
            # Bug fix (explicit report -- "same general dialog-sizing
            # bug family" as the states_widget fixes above): a
            # word-wrapped QLabel inside a QFormLayout is subject to the
            # SAME root cause already proven real for states_widget's
            # own height (see this module's own "round one"/"round two"
            # docstring writeups) -- QFormLayout's own two-pass
            # column-width resolution can settle this row's ALLOCATED
            # height from a PROVISIONAL field-column width that doesn't
            # match the width the label is actually given once every
            # column is finalized, letting the "Inherited Power States"
            # text overlap the row above/spill past the window on some
            # font/DPI/platform combination. Fixed the same way this
            # file already fixed the analogous states_widget bug:
            # adjustSize() forces one real layout pass immediately so
            # `label.width()` genuinely reflects its own final allocated
            # column width, then setFixedHeight() pins this row's own
            # height to exactly what THAT real width needs
            # (heightForWidth()) -- removing the ambiguity outright
            # instead of hoping Qt's own two-pass resolution lands on
            # the right answer. Not wired to a live "content changed"
            # signal the way states_widget is -- unlike the table's own
            # user-editable rows, this label's text is fixed for the
            # lifetime of one dialog instance (set once at construction
            # from `inherited_state_lines`, never mutated afterward by
            # any caller), so a single correct measurement here is
            # already sufficient for however long that text turns out
            # to be, placeholder or a full real state list alike.
            self.adjustSize()
            self.inherited_states_label.setFixedHeight(
                self.inherited_states_label.heightForWidth(self.inherited_states_label.width())
            )

        # Bug fix (explicit report, WIDTH variant of the same dialog-
        # sizing bug family): the flat setMinimumWidth(420) above is a
        # GUESS, not a guarantee -- it doesn't account for how wide the
        # label column itself ends up (which grows with the platform's
        # own font), so on a font/DPI where that column claims more of
        # the 420px than assumed, the Power States table's own columns
        # (and its "+" button, deliberately positioned OUTSIDE the table
        # itself -- see SupplyPortStatesWidget's own docstring) get
        # squeezed and clipped on the right. Deriving the floor from
        # THIS dialog's own freshly-built sizeHint() instead -- captured
        # once, right after every row (Power States/Inherited Power
        # States included, now pinned to its own real height above) is
        # in place, the same "one reliable point" precedent this file
        # already established for height -- guarantees the window can
        # never open narrower than what its own real content, on
        # whatever font/platform it's actually running on, needs.
        self.setMinimumWidth(max(420, self.sizeHint().width()))
        # Apply the fully-computed size immediately, rather than relying
        # on however a LATER show()/exec() call happens to trigger Qt's
        # own implicit layout-activation-then-resize-to-sizeHint()
        # behavior on a given platform -- the window's very first
        # rendered frame is already correctly sized.
        self.resize(self.sizeHint())

    def _sync_size_to_states_table(self) -> None:
        if self.states_widget is None or self._other_content_height is None:
            return
        self.states_widget.setFixedHeight(self.states_widget.required_height())
        target_height = self._other_content_height + self.states_widget.height()
        # Bug fix: resize() alone refused to SHRINK the window back down
        # after removing rows -- confirmed empirically that Qt's own
        # QLayout (SetDefaultConstraint, the default) re-applies
        # setMinimumSize() from its own layout-computed minimum on every
        # activation, and that computed minimum stayed stuck at the
        # LARGEST height this dialog ever reached rather than shrinking
        # back down (the same "stale, not re-derived" size-hint
        # unreliability behind every fix in this module, just showing up
        # as an explicit minimumSize() floor this time instead of a
        # sizeHint() cache). Clearing the height floor immediately
        # before every resize -- never the width floor, which stays the
        # deliberate 420px minimum set in __init__ -- lets resize() move
        # freely in EITHER direction; the window remains just as freely
        # user-resizable afterward either way.
        self.setMinimumHeight(0)
        self.resize(self.width(), target_height)

    @staticmethod
    def _inherited_states_text(inherited_state_lines: Optional[list[str]]) -> str:
        if inherited_state_lines is None:
            return _NOT_CONNECTED_TEXT
        if not inherited_state_lines:
            return _NO_INHERITED_STATES_TEXT
        return "\n".join([_INHERITED_STATES_HEADER, *inherited_state_lines])

    def _on_name_changed(self, text: str) -> None:
        self._ok_button.setEnabled(bool(text.strip()))

    def _refresh_color_button(self) -> None:
        self.color_button.setText(self.color.name())
        self.color_button.setStyleSheet(f"background-color: {self.color.name()}; color: #ffffff;")

    def _pick_color(self) -> None:
        chosen = QColorDialog.getColor(self.color, self, "Choose Power Supply Colour")
        if chosen.isValid():
            self.color = chosen
            self._refresh_color_button()

    def accept(self) -> None:
        if self.states_widget is not None:
            error = self.states_widget.validate()
            if error is not None:
                message_box.warning(self, "Invalid Power State", error)
                return
            # Explicit request: Power States are REQUIRED on a Virtual
            # Supply (not optional the way a plain Supply Port's own
            # list is) -- rejected here, at the same point every other
            # states-table validation already runs, rather than as a
            # separate check the caller would need to remember to make.
            if self._is_virtual_supply and not self.states_widget.states:
                message_box.warning(self, "Invalid Power State", "Virtual Supply requires at least one Power State.")
                return
        super().accept()

    @property
    def name(self) -> str:
        return self.name_edit.text().strip()

    @property
    def states(self) -> list[SupplyPortState]:
        return self.states_widget.states if self.states_widget is not None else []

    @property
    def direction(self) -> str:
        """The chosen `-direction` value, or "" for <empty>.

        Also "" for an Internal or Virtual Supply, which have no such
        field at all -- so a caller can read this unconditionally
        without re-deriving which kind of supply it is asking about."""
        if self.direction_combo is None:
            return ""
        return self.direction_combo.currentData()
