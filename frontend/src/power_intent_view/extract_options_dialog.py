"""Export options for "Extract Power Intent".

WHY A DIALOG AT ALL. Format and version are picked from the menu
(Extract > Extract Power Intent > UPF > 3.1), and the save path from a
native file dialog -- there was no place in that flow for an option that
is neither. Rather than bolt a checkable item onto the menu, where it
would sit apart from the action it modifies and silently persist between
exports, the options live in one small dialog shown between the two.

UPF ONLY. The single question here -- which power-state representation
to write -- is a UPF-era distinction with no CPF equivalent: CPF
expresses system modes as `create_power_mode`, which this app always
emits, and has nothing corresponding to `create_pst`/`add_pst_state`. So
for a CPF export there is nothing to ask, and `should_ask()` says so:
the dialog never appears rather than appearing with a control that
cannot affect the output.

EXCLUSIVE, not additive. This was a checkbox that ADDED the legacy table
alongside the modern group, on the reading that a file may carry both.
It is a radio pair now: one representation or the other, never both in
one file.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QLabel,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from frontend.src.app import theme

# Each option as the user meets it: a real TITLE, the underlying command
# names as a subtitle (they are what a reader will search the generated
# file for, so they stay visible -- just not AS the title), a
# plain-language description, and a real snippet of the output. A bare
# radio with a command name on it asks the user to already know the
# answer.
POWER_STATE_HINT = (
    "Which power-state representation this file carries. The two are mutually exclusive, "
    "and CPF is unaffected either way."
)

MODERN_TITLE = "Power State Group (Modern)"
MODERN_COMMANDS = "create_power_state_group / add_power_state"
MODERN_DESCRIPTION = (
    "Modern UPF construct. States are expressed as boolean conditions across Supply Sets, "
    "allowing richer composition than the legacy table."
)
MODERN_EXAMPLE = (
    "add_power_state -group TOP_PST \\\n"
    "    -state { ON -logic_expr {\n"
    "        SS_VDD == STATE_1 && ... } }"
)

LEGACY_TITLE = "Legacy Power State Table (PST)"
LEGACY_COMMANDS = "create_pst / add_pst_state / add_port_state"
LEGACY_DESCRIPTION = (
    "Classic, widely-supported UPF construct. Named system states reference simple "
    "per-supply voltage levels."
)
LEGACY_EXAMPLE = (
    "add_pst_state ON -pst PST \\\n"
    "    -state {FULL_ON FULL_ON FULL_ON}"
)

# The same stack hover_tooltip.py settled on after confirming JetBrains
# Mono is neither bundled nor installed here -- reused rather than
# re-chosen, so every monospace surface in the app matches.
_MONO_STACK = "'Menlo', 'Consolas', monospace"

# Spacing/margins in the same register as this app's other dialogs,
# named rather than sprinkled as literals.
_MARGIN, _SPACING = 14, 10
_CARD_MARGIN, _CARD_SPACING, _EXAMPLE_PADDING = 12, 6, 8
_MIN_WIDTH = 480

# Drawn from the app's own sampled-from-the-logo palette rather than
# invented here -- see app/theme.py.
CARD_BORDER = theme.TINT_HEADER
CARD_BACKGROUND = theme.TINT_FOCUS
EXAMPLE_BACKGROUND = "#ffffff"
EXAMPLE_COLOUR = "#2b3138"
COMMANDS_COLOUR = theme.ACCENT_PRESSED


def should_ask(format_name: str) -> bool:
    """Whether this dialog has anything to ask for `format_name`.

    Only UPF does. CPF has no legacy-power-state-table concept, so
    showing the dialog there would offer a control that could not affect
    the output."""
    return format_name == "UPF"


class ExtractOptionsDialog(QDialog):
    """The options shown between picking a UPF version and picking a path."""

    def __init__(self, version: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setSizeGripEnabled(True)  # every dialog here is user-resizable
        self.setWindowTitle(f"Extract Power Intent — UPF {version}")

        self.modern_radio = QRadioButton(MODERN_TITLE)
        self.legacy_radio = QRadioButton(LEGACY_TITLE)
        # MODERN by default: an export nobody changed stays
        # byte-identical to what this app produced before the choice
        # existed. A QButtonGroup rather than bare radios, so the
        # exclusivity is enforced by the widget rather than by the two
        # buttons happening to share a parent.
        self.modern_radio.setChecked(True)
        self.power_state_group = QButtonGroup(self)
        self.power_state_group.setExclusive(True)
        self.power_state_group.addButton(self.modern_radio)
        self.power_state_group.addButton(self.legacy_radio)

        intro = QLabel(POWER_STATE_HINT)
        intro.setWordWrap(True)
        intro.setStyleSheet("color: gray; font-style: italic;")

        self.modern_card = self._build_card(
            self.modern_radio, MODERN_COMMANDS, MODERN_DESCRIPTION, MODERN_EXAMPLE
        )
        self.legacy_card = self._build_card(
            self.legacy_radio, LEGACY_COMMANDS, LEGACY_DESCRIPTION, LEGACY_EXAMPLE
        )

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(_MARGIN, _MARGIN, _MARGIN, _MARGIN)
        layout.setSpacing(_SPACING)
        layout.addWidget(intro)
        layout.addWidget(self.modern_card)
        layout.addWidget(self.legacy_card)
        layout.addStretch(1)
        layout.addWidget(self.buttons)

        # Sized from the CONTENT rather than left to Qt's own iterative
        # resolution -- the repeated lesson in this codebase is that a
        # QDialog caches its first computed sizeHint and never re-derives
        # it, so a snippet or a wrapped description can end up clipped.
        self.setMinimumWidth(max(_MIN_WIDTH, self.sizeHint().width()))
        self.adjustSize()

    def _build_card(self, radio, commands: str, description: str, example: str) -> QFrame:
        """One option, grouped: radio + title, command names, prose, and
        a real snippet of what it generates.

        A QFrame per option rather than four loose rows, so the grouping
        is visible -- which is the point of the redesign: it must be
        obvious at a glance which text belongs to which choice."""
        card = QFrame()
        card.setFrameShape(QFrame.Shape.StyledPanel)
        card.setStyleSheet(
            f"QFrame {{ border: 1px solid {CARD_BORDER}; border-radius: 4px;"
            f" background: {CARD_BACKGROUND}; }}"
        )
        title_font = radio.font()
        title_font.setBold(True)
        radio.setFont(title_font)

        commands_label = QLabel(commands)
        commands_label.setStyleSheet(
            f"color: {COMMANDS_COLOUR}; font-family: {_MONO_STACK}; border: none;"
        )

        description_label = QLabel(description)
        description_label.setWordWrap(True)
        description_label.setStyleSheet("border: none;")

        example_label = QLabel(example)
        example_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        example_label.setStyleSheet(
            f"font-family: {_MONO_STACK}; background: {EXAMPLE_BACKGROUND};"
            f" color: {EXAMPLE_COLOUR}; border: 1px solid {CARD_BORDER};"
            f" border-radius: 3px; padding: {_EXAMPLE_PADDING}px;"
        )

        inner = QVBoxLayout(card)
        inner.setContentsMargins(_CARD_MARGIN, _CARD_MARGIN, _CARD_MARGIN, _CARD_MARGIN)
        inner.setSpacing(_CARD_SPACING)
        inner.addWidget(radio)
        inner.addWidget(commands_label)
        inner.addWidget(description_label)
        inner.addWidget(example_label)
        # Kept reachable for tests and for any later restyling, so the
        # card's parts are not anonymous children to be found by index.
        card.radio = radio
        card.commands_label = commands_label
        card.description_label = description_label
        card.example_label = example_label
        return card

    @property
    def legacy_power_states(self) -> bool:
        """True for the legacy representation, False for the modern one.

        One boolean rather than a mode enum precisely because the choice
        is binary and exclusive -- there is no third state to represent,
        and a generator parameter that could be "both" is what this
        change exists to remove."""
        return self.legacy_radio.isChecked()
