"""Green accent chrome styling (explicit request) -- PURE COSMETIC, QSS
ONLY. No functional code, widget logic, event handling, or layout is
touched anywhere for this: the single entry point is
`QApplication.setStyleSheet(build_stylesheet())` in main.py, layered on
top of the FIXED Fusion style + light palette that
`apply_fixed_appearance()` (bottom of this module) installs -- no longer
on the host's native style and colours, which made the app dark on a
dark-mode desktop.

THE ACCENT COLOUR IS SAMPLED FROM THE LOGO, NOT INVENTED.
`frontend/icons/power-intent-studio-logo.svg`'s dominant fill is
`#9FE1CB` (the rounded-rect body -- by far the most frequent non-white,
non-grey pixel when the SVG is rasterised; the only other colours are
white and the `#5F5E5A` grey arrow/ticks). `_sample_logo_green()`
below reproduces that sampling with QSvgRenderer + per-pixel HSL
inspection; `LOGO_GREEN` is the frozen result and
`test_theme.py::test_logo_green_constant_still_matches_the_asset`
fails if the asset ever drifts from it.

`#9FE1CB` is HSL(160deg, 52%, 75%) -- a pale mint. Every accent shade
below keeps that exact hue and derives lighter/darker/more-saturated
variants by HSL adjustment (`_shade()`), so the whole theme is one
colour family rooted in the logo:
  - ACCENT           darkened+saturated -> reads as a real accent
                     against the light UI (buttons, tab underline,
                     menu-bar underline, focus rings)
  - ACCENT_HOVER / ACCENT_PRESSED   button interaction states
  - TINT_TOOL_ACTIVE  active tool-palette button wash
  - TINT_FOCUS        dock-title wash (audit fix, category 03)
  - TINT_HEADER       defined, but still NOT applied -- QSS on
                     QHeaderView::section breaks the existing
                     bold-header feature (see the stylesheet's own
                     note). Deliberately left unused so the test that
                     asserts its absence stays meaningful.

EXPLICITLY UNTOUCHED (these carry real meaning -- see the request):
  - the fluorescent-yellow diagram/Properties selection highlight
    (`supply_port_overlay.HIGHLIGHT_COLOR`, painted on QGraphicsItems --
    QSS can't reach it, and nothing here styles `::item:selected`);
  - diagnostic severity presentation (the diagnostics tables use plain
    text labels, no custom colours at all -- confirmed);
  - every live Supply-Set-derived colour on Power Domain / Isolation /
    Retention / Power Switch / Virtual Supply icons and labels (painted
    on QGraphicsItems, not QSS).
"""

from __future__ import annotations

from pathlib import Path

_ICONS_DIR = Path(__file__).resolve().parents[2] / "icons"
LOGO_PATH = _ICONS_DIR / "power-intent-studio-logo.svg"

# Frozen result of _sample_logo_green() -- see module docstring.
LOGO_GREEN = "#9fe1cb"


def _sample_logo_green() -> str:
    """Rasterise the logo SVG and return the hex of its dominant green
    fill (most frequent pixel with hue in the green band and real
    saturation). Needs a live QApplication -- only ever called from a
    test, never at import time."""
    from collections import Counter

    from PySide6.QtCore import QByteArray
    from PySide6.QtGui import QColor, QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer

    renderer = QSvgRenderer(QByteArray(LOGO_PATH.read_bytes()))
    image = QImage(256, 256, QImage.Format.Format_ARGB32)
    image.fill(QColor("white"))
    painter = QPainter(image)
    renderer.render(painter)
    painter.end()

    greens: Counter[str] = Counter()
    for y in range(0, image.height(), 2):
        for x in range(0, image.width(), 2):
            color = image.pixelColor(x, y)
            if color.alpha() < 200:
                continue
            hue, sat, light, _ = color.getHslF()
            hue_deg = hue * 360 if hue >= 0 else -1.0
            if 90 <= hue_deg <= 175 and sat >= 0.25 and 0.2 <= light <= 0.9:
                greens[color.name()] += 1
    return greens.most_common(1)[0][0] if greens else LOGO_GREEN


def _shade(hex_color: str, lightness: float, saturation_mul: float = 1.0) -> str:
    """A variant of `hex_color` at the given HSL `lightness` (0..1),
    hue unchanged, saturation multiplied by `saturation_mul` (clamped).
    Pure math -- no Qt -- so theme constants resolve at import time."""
    r = int(hex_color[1:3], 16) / 255
    g = int(hex_color[3:5], 16) / 255
    b = int(hex_color[5:7], 16) / 255
    hi, lo = max(r, g, b), min(r, g, b)
    lum = (hi + lo) / 2
    if hi == lo:
        h = s = 0.0
    else:
        d = hi - lo
        s = d / (2 - hi - lo) if lum > 0.5 else d / (hi + lo)
        if hi == r:
            h = (g - b) / d + (6 if g < b else 0)
        elif hi == g:
            h = (b - r) / d + 2
        else:
            h = (r - g) / d + 4
        h /= 6
    s = max(0.0, min(1.0, s * saturation_mul))
    lightness = max(0.0, min(1.0, lightness))

    def _hue_to_rgb(p: float, q: float, t: float) -> float:
        if t < 0:
            t += 1
        if t > 1:
            t -= 1
        if t < 1 / 6:
            return p + (q - p) * 6 * t
        if t < 1 / 2:
            return q
        if t < 2 / 3:
            return p + (q - p) * (2 / 3 - t) * 6
        return p

    if s == 0:
        rr = gg = bb = lightness
    else:
        q = lightness * (1 + s) if lightness < 0.5 else lightness + s - lightness * s
        p = 2 * lightness - q
        rr = _hue_to_rgb(p, q, h + 1 / 3)
        gg = _hue_to_rgb(p, q, h)
        bb = _hue_to_rgb(p, q, h - 1 / 3)
    return "#{:02x}{:02x}{:02x}".format(round(rr * 255), round(gg * 255), round(bb * 255))


ACCENT = _shade(LOGO_GREEN, 0.43, 1.15)
ACCENT_HOVER = _shade(LOGO_GREEN, 0.38, 1.15)
ACCENT_PRESSED = _shade(LOGO_GREEN, 0.32, 1.15)
TINT_TOOL_ACTIVE = _shade(LOGO_GREEN, 0.86)
# Defined for completeness / a future pass -- NOT currently applied
# (see the module docstring and build_stylesheet()'s header note).
TINT_HEADER = _shade(LOGO_GREEN, 0.90)
TINT_FOCUS = _shade(LOGO_GREEN, 0.94)


# --- a FIXED appearance, independent of the host machine -------------------
#
# The app runs on every kind of machine (Linux servers, Windows, macOS),
# and used to inherit each host's native widget style AND its colours --
# so a server whose desktop was in dark mode painted the whole app dark,
# under an accent, a selection highlight and diagram colours that were
# all chosen for a light background. It now looks the same everywhere:
#
#   - the Fusion style, Qt's own, identical on every platform (no
#     native style anywhere -- macOS included, by decision);
#   - an explicit LIGHT palette, built from the fixed colours below
#     rather than taken from Fusion's own standard palette, which on
#     newer Qt versions can itself follow the desktop's dark mode;
#   - and, where Qt supports it, an explicit request for the LIGHT
#     colour scheme, so the platform is never asked to apply its own.
#
# Colours follow Fusion's classic light look. Highlight is Fusion's own
# blue, deliberately NOT the green accent: it is the standard list/table
# row-selection colour, and changing it is not part of this.
APP_STYLE = "Fusion"

LIGHT_PALETTE = {
    "Window": "#efefef",
    "WindowText": "#000000",
    "Base": "#ffffff",
    "AlternateBase": "#f7f7f7",
    "ToolTipBase": "#ffffdc",
    "ToolTipText": "#000000",
    "PlaceholderText": "#808080",
    "Text": "#000000",
    "Button": "#efefef",
    "ButtonText": "#000000",
    "BrightText": "#ffffff",
    "Light": "#ffffff",
    "Midlight": "#cacaca",
    "Mid": "#b8b8b8",
    "Dark": "#9f9f9f",
    "Shadow": "#767676",
    "Highlight": "#308cc6",
    "HighlightedText": "#ffffff",
    "Link": "#0000ff",
    "LinkVisited": "#ff00ff",
}
# Greyed-out text for disabled widgets (e.g. Isolation's Elements section
# while Applies To All is not "Elements") -- still readable on Window.
LIGHT_PALETTE_DISABLED = {
    "WindowText": "#a0a0a0",
    "Text": "#a0a0a0",
    "ButtonText": "#a0a0a0",
    "Base": "#efefef",
    "Highlight": "#919191",
    "HighlightedText": "#ffffff",
}


def build_light_palette():
    """The fixed light QPalette (see the block comment above)."""
    from PySide6.QtGui import QColor, QPalette

    palette = QPalette()
    for role, color in LIGHT_PALETTE.items():
        palette.setColor(getattr(QPalette.ColorRole, role), QColor(color))
    for role, color in LIGHT_PALETTE_DISABLED.items():
        palette.setColor(QPalette.ColorGroup.Disabled, getattr(QPalette.ColorRole, role), QColor(color))
    return palette


def apply_fixed_appearance(app) -> None:
    """Make `app` look the same on every machine: Fusion style, a light
    colour scheme request, then the fixed light palette.

    Call it right after the QApplication is created and BEFORE any
    widget exists, so nothing is ever constructed with the host's look.
    (The accent stylesheet is still applied after MainWindow is built --
    see main.py.) The palette is set AFTER the style on purpose: setting
    a style can reset the application palette to that style's own."""
    from PySide6.QtCore import Qt

    app.setStyle(APP_STYLE)
    hints = app.styleHints()
    if hasattr(hints, "setColorScheme"):  # Qt 6.8+
        hints.setColorScheme(Qt.ColorScheme.Light)
    app.setPalette(build_light_palette())


def build_stylesheet() -> str:
    """The app-wide QSS: menu-bar underline, active-tab indicator,
    primary buttons, focus rings, active tool button, and (audit fix,
    category 03) the interior widgets the first pass left on the stock
    platform look -- progress bars, dock titles, dropdown menus, group-
    box titles, check/radio indicators and splitter handles.

    (Table-header wash is still skipped -- see the NOTE block below.)
    Nothing here styles the diagram/Properties `::item:selected` state
    (that would collide with the yellow selection highlight), any
    QGraphicsView content, or any severity/diagnostic presentation --
    dropdown menus get a border only, for the same reason."""
    return f"""
/* Menu bar: a thin accent underline, NOT a solid fill. */
QMenuBar {{
    border-bottom: 2px solid {ACCENT};
}}

/* NOTE -- table headers are deliberately NOT styled here.
   Applying ANY `QHeaderView::section` rule makes Qt re-resolve the
   header font and silently drop the per-table
   `horizontalHeader().setFont(bold)` that the Properties/Power-State
   tables already do (a deliberate, tested "bold header row" feature) --
   `header().font().bold()` then reports False and 4 existing tests
   fail. Per the styling request's own rule ("revert rather than adjust
   the test"), the accent wash on header rows is skipped. Restoring the
   tint AND the bold would need the header font set via code, not QSS --
   a functional change that's out of scope for this cosmetic pass.
   Flagged for the reviewer. */

/* Active tab: an accent underline indicator on the SELECTED tab only.
   Inactive tabs and the rest of the bar are untouched. */
QTabBar::tab:selected {{
    border-bottom: 2px solid {ACCENT};
}}

/* Primary / affirmative buttons.
   - `:default` catches the accept button Qt marks as default in a
     QDialogButtonBox (OK / Save / Apply).
   - `[accent="true"]` is an explicit opt-in hook for the handful of
     standalone primary buttons that aren't dialog defaults (Add State,
     Check Power Intent) -- a cosmetic property only, set in the tab's
     own __init__, no behaviour attached. */
QPushButton:default,
QPushButton[accent="true"] {{
    background-color: {ACCENT};
    color: #ffffff;
    border: 1px solid {ACCENT_HOVER};
    border-radius: 3px;
    padding: 4px 14px;
}}
QPushButton:default:hover,
QPushButton[accent="true"]:hover {{
    background-color: {ACCENT_HOVER};
}}
QPushButton:default:pressed,
QPushButton[accent="true"]:pressed {{
    background-color: {ACCENT_PRESSED};
}}
QPushButton:default:disabled,
QPushButton[accent="true"]:disabled {{
    background-color: #c9c9c9;
    color: #f2f2f2;
    border-color: #bfbfbf;
}}

/* Focus indicator: accent-green border on the actively-focused input,
   dropdown, or table -- replacing the default OS focus ring colour. */
QLineEdit:focus,
QPlainTextEdit:focus,
QTextEdit:focus,
QSpinBox:focus,
QDoubleSpinBox:focus,
QComboBox:focus {{
    border: 1px solid {ACCENT};
}}
QTableView:focus,
QTableWidget:focus,
QTreeView:focus,
QTreeWidget:focus,
QListView:focus,
QListWidget:focus {{
    border: 1px solid {ACCENT};
}}

/* Tool palette: the currently ACTIVE (checked) tool button gets an
   accent tint instead of the default OS selection colour. */
QToolBar QToolButton:checked {{
    background-color: {TINT_TOOL_ACTIVE};
    border: 1px solid {ACCENT};
    border-radius: 3px;
}}

/* --- Audit fix (category 03): widget classes the first pass missed ---
   The theme reached the window chrome (menu bar, tab bar, buttons,
   focus rings, tool palette) and stopped there, leaving several
   prominent interior widgets on the stock platform look. Each rule
   below is the same restrained treatment already used above -- an
   accent border or a tint, never a solid fill -- and none of them
   reach the model-view selection state, QGraphicsView content, or any
   severity colour, for the same reasons the header block documents.
   (The selection pseudo-state is not even NAMED here: the test that
   guards it matches the bare pattern anywhere in this stylesheet,
   comments included.) */

/* Progress bar: the most visible widget on both Load tabs, previously
   fully unstyled. Chunk only -- the groove keeps the platform look. */
QProgressBar {{
    border: 1px solid {ACCENT};
    border-radius: 3px;
    text-align: center;
}}
QProgressBar::chunk {{
    background-color: {ACCENT};
    border-radius: 2px;
}}

/* Dock titles: a quiet tint with an accent underline, so a floating or
   tabbed dock reads as part of the same family as the tab bar. */
QDockWidget::title {{
    background: {TINT_FOCUS};
    border-bottom: 1px solid {ACCENT};
    padding: 4px 6px;
}}

/* Dropdown menus: only the menu BAR was styled before, so every
   dropdown it opened fell back to the stock palette. Border only --
   the obvious addition, a highlighted-item tint, is deliberately NOT
   made: `test_stylesheet_never_touches_selection_or_severity_colours`
   guards that selection pseudo-state as a bare pattern anywhere in
   this stylesheet, and honouring the guard is worth more than a hover
   tint on a dropdown. */
QMenu {{
    border: 1px solid {ACCENT};
}}

/* Group boxes: the Diagram/Design Files/Loaded containers. Title only. */
QGroupBox::title {{
    color: {ACCENT_PRESSED};
    font-weight: 600;
}}

/* Checked check boxes and radio buttons (the Visualize/category
   filters, dialog toggles) -- indicator only, label text untouched. */
QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
    border: 1px solid {ACCENT};
    background-color: {ACCENT};
    border-radius: 2px;
}}

/* Splitter handle: a hairline that picks up the accent on hover, so the
   draggable seam between panes is discoverable. */
QSplitter::handle:hover {{
    background-color: {TINT_TOOL_ACTIVE};
}}
""".strip()
