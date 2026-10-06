""""Power Switch" dialog -- triggered from the Power Intent tab's
Diagram via the Power Switch tool's own hover+click placement (see
power_intent_diagram_view.py's DiagramMode.POWER_SWITCH handling) or the
right-click "Add Power Switch"/"Edit Power Switch" context-menu actions
on a Supply Port/Internal Supply icon, both funneling through the
identical (validate target then this dialog) flow, same "do NOT build a
new one" precedent every other placement tool here already follows.

Diagram-only for this pass, same scoping decision as Supply Ports/
Retention (see power_switch_overlay.py's own module docstring) -- this
dialog only ever hands back plain data, never touches shared state
itself; the view's own add_power_switch()/edit_power_switch() does
that.

Field layout (rework, explicit request -- REVERSES the earlier "Cell
Type dropdown referencing a reusable PowerSwitchCellPlacement library"
model back to per-instance free text, "expose port fields directly"):

  Applies to: <hierarchy path>              (read-only, unchanged)
  -------------------------------------------
  Name: <text>                               (unchanged)
  Power Domain: <label>                      (unchanged)
  -------------------------------------------
  Supply In: <label>                         (UNCHANGED -- see below)
  Supply Out: <label>                        (UNCHANGED -- see below)
  -------------------------------------------
  Input Supply Port: <text>                  (NEW -- required, no spaces)
  Output Supply Port: <text>                 (NEW -- required, no spaces)
  Control Port: <text>                       (NEW -- required, no spaces)
  Cell Type: <text>              (Optional)  (NEW -- free text, no
                                              longer a dropdown)
  -------------------------------------------
  ON State: <text>                           (unchanged, required)
  OFF State: <text>              (Optional)  (unchanged)

CONFIRMED unaffected by this rework: the Supply In/Supply Out
ELECTRICAL connection resolution (Supply Out auto-filled from the
target Supply Port/Internal Supply the switch is placed on; Supply In
from a manually-drawn/auto-adopted connector on the top anchor) --
`supply_in_label`/`supply_out_label` below are unchanged from before,
still read-only labels, not editable fields.

Input Supply Port / Output Supply Port / Control Port: plain,
single-line free-text inputs (same widget behavior as e.g. Retention's
Save/Restore Logic fields), but validated no-spaces at accept() time
(same convention as every other pin-name-like field in this app,
unlike Save/Restore Logic's own free-typed boolean expressions) -- and,
unlike Save/Restore Logic, REQUIRED: these are the switch's own real
pin-name data (what power_intent_text_export.py needs to emit real
`-input_supply_port {<role> <net>}`/`-output_supply_port {<role> <net>}`/
`-control_port <pin>` flags), so they gate the OK button exactly like
Name/ON State do.

Cell Type: plain free-text input, OPTIONAL -- a purely descriptive
label with no relationship to any pin-name field. Leaving it blank is
NOT a blocking error -- see properties_panel.py's own docstring for the
generation-time warning this triggers instead (same "no Cell Type
Assigned" warning as before, now keyed off an empty string rather than
an unselected/unassigned reference).

"(Optional)" on OFF State/Cell Type renders to the RIGHT of its input,
italic (global styling rule, shared/widgets/optional_field.py).

EDIT mode reopens this same dialog pre-filled with the existing Power
Switch's current values (Name/Input-Output-Control Port/Cell Type/
ON-OFF State from `initial`; Supply In/Out resolved fresh by the
caller, same as Add) -- same Edit convention already established for
Retention/Supply Port.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGridLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from frontend.src.power_intent_view.name_validation import NO_SPACES_ERROR, validate_no_spaces
from frontend.src.power_intent_view.power_domain_overlay import PowerDomainPlacement
from frontend.src.power_intent_view.power_switch_overlay import (
    ABSTRACT_ACK,
    ABSTRACT_CTRL,
    ABSTRACT_IN,
    ABSTRACT_OUT,
    PowerSwitchPlacement,
    references_control_signal,
)
from frontend.src.shared.widgets import message_box
from frontend.src.shared.widgets.form_separator import form_separator
from frontend.src.shared.widgets.optional_field import wrap_optional_field
from frontend.src.shared.widgets.target_hierarchy_label import APPLIES_TO_CAPTION, make_target_hierarchy_label

NOT_CONNECTED_TEXT = "Not Connected"


def _supply_display_label(name: Optional[str]) -> QLabel:
    label = QLabel(name if name else NOT_CONNECTED_TEXT)
    if not name:
        font = label.font()
        font.setItalic(True)
        label.setFont(font)
    return label


class PowerSwitchDialog(QDialog):
    """The same dialog class serves both Add and Edit (same precedent as
    RetentionStrategyDialog/CreatePowerDomainDialog): passing `initial`
    (an existing PowerSwitchPlacement) and `editing_switch_id`
    pre-populates every editable field and excludes that switch's own
    current name from the global uniqueness check at accept() time.

    `existing_names` (switch id -> name, every Power Switch in the
    ENTIRE design) backs the accept()-time duplicate-name QMessageBox --
    same "uniqueness is global" precedent RetentionStrategyDialog
    already established, not scoped to any one hierarchy/Power Domain."""

    def __init__(
        self,
        target_paths: list[str],
        target_name: str,
        domain: PowerDomainPlacement,
        supply_in_name: Optional[str],
        existing_names: dict[str, str],
        editing_switch_id: Optional[str] = None,
        initial: Optional[PowerSwitchPlacement] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.setSizeGripEnabled(True)  # explicit request (Bug 5): every dialog here must be user-resizable
        self.setWindowTitle("Edit Power Switch" if editing_switch_id is not None else "Add Power Switch")
        self._domain = domain
        self._existing_names = dict(existing_names)
        self._editing_switch_id = editing_switch_id

        self.name_edit = QLineEdit(initial.name if initial is not None else "")
        self.domain_label = QLabel(domain.name)  # read-only -- auto-filled from the target's own domain

        self.supply_in_label = _supply_display_label(supply_in_name)  # read-only -- reflects the diagram's own top-anchor connector
        self.supply_out_label = QLabel(target_name)  # read-only -- always the target itself, see class docstring

        self.input_supply_port_edit = QLineEdit(initial.input_supply_port if initial is not None else "")
        self.output_supply_port_edit = QLineEdit(initial.output_supply_port if initial is not None else "")
        self.control_port_edit = QLineEdit(initial.control_port if initial is not None else "")
        # The RTL signal that actually drives the control pin (review
        # finding 2.1) -- a different thing from the pin itself, and the
        # half real UPF needs to emit a connection at all.
        self.control_signal_edit = QLineEdit(initial.control_signal if initial is not None else "")
        # The acknowledge pair, mirroring the control pair: the switch's
        # own port name on the left, the real RTL signal it drives on the
        # right. Both OPTIONAL.
        self.ack_port_edit = QLineEdit(initial.ack_port if initial is not None else "")
        self.ack_signal_edit = QLineEdit(initial.ack_signal if initial is not None else "")
        self.cell_type_edit = QLineEdit((initial.cell_type or "") if initial is not None else "")

        self.on_state_edit = QLineEdit(initial.on_state if initial is not None else "")
        self.off_state_edit = QLineEdit(initial.off_state if initial is not None else "")

        # Placeholders naming the ABSTRACT UPF port each field implements
        # or references. The review found a real file whose ON State read
        # `ON` -- not the control port at all -- which suggested these
        # fields were being read as free labels rather than as what they
        # are. See _SWITCH_ABSTRACT_PORTS in power_intent_text_export.py.
        self.name_edit.setPlaceholderText("e.g. PSW_CPU")
        self.input_supply_port_edit.setPlaceholderText(f"e.g. {ABSTRACT_IN} \u2014 the switch's own input port name")
        self.output_supply_port_edit.setPlaceholderText(f"e.g. {ABSTRACT_OUT} \u2014 the switch's own output port name")
        self.control_port_edit.setPlaceholderText(f"e.g. {ABSTRACT_CTRL} \u2014 the switch's own control port name")
        self.cell_type_edit.setPlaceholderText("e.g. PSWITCH_X4 \u2014 the mapped library cell name")
        self.control_signal_edit.setPlaceholderText("e.g. pwr_en_cpu \u2014 the RTL signal driving the control port")
        self.ack_port_edit.setPlaceholderText(f"e.g. {ABSTRACT_ACK} \u2014 the switch's own acknowledge port name")
        self.ack_signal_edit.setPlaceholderText("e.g. pwr_ack_cpu \u2014 the RTL signal driven by the acknowledge port")
        self.on_state_edit.setPlaceholderText("e.g. pwr_en_cpu \u2014 boolean expression for when the switch is ON")
        self.off_state_edit.setPlaceholderText("e.g. !pwr_en_cpu \u2014 boolean expression for when the switch is OFF")
        # Supplementary tooltips, only where the example alone does not
        # convey that a FULL boolean expression is accepted rather than a
        # single signal name.
        for edit_widget in (self.on_state_edit, self.off_state_edit):
            edit_widget.setToolTip(
                "A boolean expression over the Control Signal. Operators and additional "
                "terms are allowed (e.g. pwr_en_cpu && !test_mode); the Control Signal is "
                f"rewritten to the abstract UPF port {ABSTRACT_CTRL} on export."
            )

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        self._ok_button = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        self._required_edits = (
            self.name_edit,
            self.input_supply_port_edit,
            self.output_supply_port_edit,
            self.control_port_edit,
            self.control_signal_edit,
            self.on_state_edit,
        )
        self._update_ok_enabled()
        for edit in self._required_edits:
            edit.textChanged.connect(self._update_ok_enabled)

        # Four PAIRED rows, two columns: the switch's own PORT NAME on
        # the left, the real thing it connects to on the right. (The left
        # column named a library CELL PIN while the abstract-token layer
        # existed; with that retired, these values are the port names
        # `create_power_switch` declares directly.) The
        # pairing is the point -- it is what makes "Control Port" (a cell
        # pin) and "Control Signal" (an RTL net) read as two halves of
        # one fact rather than as two unrelated fields, which is how they
        # were being misread. A real QGridLayout rather than four
        # independent horizontal boxes, so the right-hand column actually
        # lines up across all four rows.
        self.paired_grid = QGridLayout()
        self.paired_grid.setColumnStretch(1, 1)
        self.paired_grid.setColumnStretch(3, 1)
        for row, (left_label, left_field, right_label, right_field) in enumerate([
            ("Input Supply Port:", self.input_supply_port_edit, "Supply In:", self.supply_in_label),
            ("Output Supply Port:", self.output_supply_port_edit, "Supply Out:", self.supply_out_label),
            ("Control Port:", self.control_port_edit, "Control Signal:", self.control_signal_edit),
            # The only optional pair, so only this row's fields are
            # wrapped -- the marker means "leaving this empty is fine",
            # and putting it on a required field would be a lie.
            ("Ack Port:", wrap_optional_field(self.ack_port_edit),
             "Ack Signal:", wrap_optional_field(self.ack_signal_edit)),
        ]):
            self.paired_grid.addWidget(QLabel(left_label), row, 0)
            self.paired_grid.addWidget(left_field, row, 1)
            self.paired_grid.addWidget(QLabel(right_label), row, 2)
            self.paired_grid.addWidget(right_field, row, 3)

        fields_form = QFormLayout()
        fields_form.addRow(APPLIES_TO_CAPTION, make_target_hierarchy_label(target_paths))
        fields_form.addRow("Name:", self.name_edit)
        fields_form.addRow("Power Domain:", self.domain_label)
        fields_form.addRow(form_separator())
        fields_form.addRow("Cell Type:", wrap_optional_field(self.cell_type_edit))
        # Spanning row, no label of its own: the paired grid carries its
        # own labels in both columns.
        fields_form.addRow(self.paired_grid)
        fields_form.addRow(form_separator())
        fields_form.addRow("ON State:", self.on_state_edit)
        fields_form.addRow("OFF State:", wrap_optional_field(self.off_state_edit))

        self.state_hint = QLabel()
        self.state_hint.setWordWrap(True)
        for edit in (self.on_state_edit, self.off_state_edit, self.control_signal_edit):
            edit.textChanged.connect(self._update_state_hint)
        self._update_state_hint()

        outer = QVBoxLayout(self)
        outer.addLayout(fields_form)
        outer.addWidget(self.state_hint)
        outer.addWidget(self.buttons)

    # --- accept validation ----------------------------------------------

    def _update_ok_enabled(self) -> None:
        self._ok_button.setEnabled(all(bool(edit.text().strip()) for edit in self._required_edits))

    def accept(self) -> None:
        name = self.name
        if not validate_no_spaces(name):
            message_box.warning(self, "Invalid Name", NO_SPACES_ERROR)
            return
        for switch_id, existing_name in self._existing_names.items():
            if switch_id != self._editing_switch_id and existing_name == name:
                message_box.warning(self, "Duplicate Power Switch Name", f"A Power Switch named '{name}' already exists. Choose a different name.")
                return
        for value, label in (
            (self.input_supply_port, "Input Supply Port"),
            (self.output_supply_port, "Output Supply Port"),
            (self.control_port, "Control Port"),
            (self.control_signal, "Control Signal"),
        ):
            if not value:
                message_box.warning(self, f"Missing {label}", f"{label} is required.")
                return
            if not validate_no_spaces(value):
                message_box.warning(self, f"Invalid {label}", NO_SPACES_ERROR)
                return
        # The acknowledge pair is OPTIONAL, so an empty value is valid --
        # but a value that IS given still has to be a legal name.
        for value, label in ((self.ack_port, "Ack Port"), (self.ack_signal, "Ack Signal")):
            if value and not validate_no_spaces(value):
                message_box.warning(self, f"Invalid {label}", NO_SPACES_ERROR)
                return
        super().accept()

    # --- non-blocking ON/OFF State hint ---------------------------------

    def stray_state_expressions(self) -> list[str]:
        """Which ON/OFF State values never mention the Control Signal.

        Review finding 2.1: the reviewed file had `-on_state {on IN {ON}}`
        -- "ON" is not a signal or a port of anything, which suggests the
        field was being read as a free-text label rather than as a boolean
        expression. Checked against the REAL Control Signal value now that
        there is one, rather than against a guessed token.

        Informational only. An expression may legitimately reference a
        signal this dialog knows nothing about (an enable qualified by
        some other condition), so refusing it would be worse than saying
        so -- and unsubstituted terms are emitted verbatim, which is the
        honest thing to do with an expression the generator cannot
        interpret."""
        signal = self.control_signal
        if not signal:
            return []
        return [
            label for value, label in ((self.on_state, "ON State"), (self.off_state, "OFF State"))
            if value and not references_control_signal(value, signal)
        ]

    def _update_state_hint(self) -> None:
        """Live, inline, and deliberately NOT a QMessageBox at accept
        time: a modal there blocks headlessly forever (the same hazard
        already documented across this suite), and a warning that appears
        only once the user has committed is worse guidance than one
        visible while they type."""
        signal = self.control_signal or "the Control Signal"
        stray = self.stray_state_expressions()
        if stray:
            self.state_hint.setText(
                f"\u26a0 {' and '.join(stray)} does not reference '{signal}'. ON/OFF State are "
                f"boolean expressions over the Control Signal; it is rewritten to the "
                f"abstract UPF port {ABSTRACT_CTRL} on export."
            )
            self.state_hint.setStyleSheet("color: #b06000; font-style: italic;")
        else:
            self.state_hint.setText(
                f"ON/OFF State are boolean expressions over the Control Signal, rewritten "
                f"to the abstract UPF port {ABSTRACT_CTRL} on export."
            )
            self.state_hint.setStyleSheet("color: gray; font-style: italic;")

    # --- accepted values -------------------------------------------------

    @property
    def name(self) -> str:
        return self.name_edit.text().strip()

    @property
    def input_supply_port(self) -> str:
        return self.input_supply_port_edit.text().strip()

    @property
    def output_supply_port(self) -> str:
        return self.output_supply_port_edit.text().strip()

    @property
    def control_port(self) -> str:
        return self.control_port_edit.text().strip()

    @property
    def control_signal(self) -> str:
        return self.control_signal_edit.text().strip()

    @property
    def ack_port(self) -> str:
        return self.ack_port_edit.text().strip()

    @property
    def ack_signal(self) -> str:
        return self.ack_signal_edit.text().strip()

    @property
    def cell_type(self) -> Optional[str]:
        return self.cell_type_edit.text().strip() or None

    @property
    def on_state(self) -> str:
        return self.on_state_edit.text().strip()

    @property
    def off_state(self) -> str:
        return self.off_state_edit.text().strip()
