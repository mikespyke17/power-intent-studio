"""Green-accent chrome styling (app/theme.py) -- PURE COSMETIC.

Automated coverage is necessarily limited: QSS visual rendering can't
be asserted meaningfully in a headless test. These tests lock in the
things that CAN be checked -- the accent colour really is sampled from
the logo, the derived shades share its hue, the stylesheet targets the
right chrome selectors and NOT the protected semantic ones, and
applying it doesn't break widget construction. Actual on-screen
appearance (menu-bar underline, header wash, active-tab indicator,
primary-button fill, focus rings, active tool highlight; yellow
selection + diagnostics unchanged) still needs a manual visual pass on
real Windows + Linux -- noted in the PR.
"""

import colorsys

import pytest
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLineEdit, QTableWidget, QVBoxLayout

from frontend.src.app import theme


@pytest.fixture
def styled_app(qapp):
    """Applies the theme stylesheet for the duration of one test, then
    restores it -- the `qapp` fixture is session-scoped, so a leaked
    stylesheet would break the bold-header tests in other test files."""
    previous = qapp.styleSheet()
    qapp.setStyleSheet(theme.build_stylesheet())
    yield qapp
    qapp.setStyleSheet(previous)


def _hue(hex_color: str) -> float:
    c = QColor(hex_color)
    h, _l, _s = colorsys.rgb_to_hls(c.redF(), c.greenF(), c.blueF())
    return h * 360


# --- the accent colour is SAMPLED, not invented --------------------------


def test_logo_green_constant_still_matches_the_asset(qapp):
    """theme.LOGO_GREEN is the frozen result of _sample_logo_green() --
    this fails if the logo asset ever drifts from it (so the theme is
    never silently based on a stale colour)."""
    assert theme._sample_logo_green() == theme.LOGO_GREEN


def test_logo_green_is_actually_a_green():
    assert 90 <= _hue(theme.LOGO_GREEN) <= 180  # green band


# --- derived shades stay in the logo's colour family --------------------


def test_every_accent_shade_shares_the_logo_green_hue():
    base_hue = _hue(theme.LOGO_GREEN)
    for shade in (theme.ACCENT, theme.ACCENT_HOVER, theme.ACCENT_PRESSED, theme.TINT_TOOL_ACTIVE, theme.TINT_HEADER, theme.TINT_FOCUS):
        assert abs(_hue(shade) - base_hue) <= 4, f"{shade} drifted off the logo hue"


def test_accent_is_darker_than_the_pale_logo_green_so_it_reads_as_an_accent():
    assert QColor(theme.ACCENT).lightnessF() < QColor(theme.LOGO_GREEN).lightnessF()
    assert QColor(theme.ACCENT_PRESSED).lightnessF() < QColor(theme.ACCENT_HOVER).lightnessF() < QColor(theme.ACCENT).lightnessF()


def test_tints_are_lighter_than_the_logo_green_so_they_read_as_a_wash():
    assert QColor(theme.TINT_HEADER).lightnessF() > QColor(theme.LOGO_GREEN).lightnessF()


# --- the stylesheet targets the right chrome, not the protected colours -


def test_stylesheet_references_the_applied_accent_shades():
    qss = theme.build_stylesheet()
    for shade in (theme.ACCENT, theme.ACCENT_HOVER, theme.ACCENT_PRESSED, theme.TINT_TOOL_ACTIVE):
        assert shade in qss


def test_stylesheet_styles_the_expected_chrome_surfaces():
    qss = theme.build_stylesheet()
    assert "QMenuBar" in qss  # menu-bar underline
    assert "QTabBar::tab:selected" in qss  # active-tab indicator
    assert "QPushButton:default" in qss and 'QPushButton[accent="true"]' in qss  # primary buttons
    assert ":focus" in qss  # focus rings
    assert "QToolButton:checked" in qss  # active tool highlight


def test_table_headers_are_deliberately_left_unstyled():
    """QSS on QHeaderView::section clobbers the existing bold-header
    feature (see build_stylesheet()'s own note) -- so it's skipped, per
    the request's 'revert rather than adjust the test' rule. No
    `::section` RULE (a mention in a comment is fine) and the header
    tint colour is never emitted."""
    qss = theme.build_stylesheet()
    assert "QHeaderView::section {" not in qss
    assert theme.TINT_HEADER not in qss


def test_stylesheet_never_touches_selection_or_severity_colours():
    """The yellow diagram/Properties selection highlight and the
    diagnostic severity presentation carry real meaning -- the theme
    must not compete with them. It styles no `::item:selected` state and
    contains no yellow/red anywhere."""
    qss = theme.build_stylesheet().lower()
    assert "::item:selected" not in qss
    assert "selection-background-color" not in qss
    assert "#ffff00" not in qss and "255, 255, 0" not in qss and "yellow" not in qss
    assert "#ff0000" not in qss and "red" not in qss


# --- applying the stylesheet doesn't break widgets ---------------------


def test_applying_the_stylesheet_keeps_widgets_functional(styled_app):
    dialog = QDialog()
    layout = QVBoxLayout(dialog)
    edit = QLineEdit("hello")
    table = QTableWidget(2, 3)
    table.setHorizontalHeaderLabels(["A", "B", "C"])
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
    layout.addWidget(edit)
    layout.addWidget(table)
    layout.addWidget(buttons)
    dialog.show()
    styled_app.processEvents()

    ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
    assert ok.isDefault() is True  # -> QPushButton:default styling applies to it
    assert table.columnCount() == 3 and table.horizontalHeaderItem(1).text() == "B"
    assert edit.text() == "hello"
    dialog.close()
    dialog.deleteLater()


def test_theme_applied_after_mainwindow_keeps_the_bold_header_feature_intact(qtbot):
    """The real main() applies the stylesheet AFTER building MainWindow
    (main.py) precisely so the per-table bold-header setFont() is
    untouched. This locks that ordering in: build the window, apply the
    theme, and the Properties-table headers are still bold -- exactly as
    without any theme."""
    from frontend.src.app.main_window import MainWindow

    from PySide6.QtWidgets import QApplication

    window = MainWindow()
    qtbot.addWidget(window)
    header = window.power_intent_tab.properties.design_properties._port_panel.table.horizontalHeader()
    assert header.font().bold() is True  # baseline, no theme yet

    previous = QApplication.instance().styleSheet()
    try:
        QApplication.instance().setStyleSheet(theme.build_stylesheet())
        qtbot.wait(1)
        assert header.font().bold() is True  # STILL bold after the theme
        # and a table built later (e.g. in a dialog) is fine too
        from frontend.src.power_intent_view.properties_panel import _make_table

        later = _make_table(["X", "Y"])
        qtbot.addWidget(later)
        assert later.horizontalHeader().font().bold() is True
    finally:
        QApplication.instance().setStyleSheet(previous)


def test_power_state_table_primary_buttons_carry_the_accent_style_hook(qtbot):
    from frontend.src.power_intent_view.power_intent_tab import PowerIntentTab
    from frontend.src.power_intent_view.power_state_table_tab import PowerStateTableTab

    pit = PowerIntentTab()
    qtbot.addWidget(pit)
    tab = PowerStateTableTab(pit.diagram)
    qtbot.addWidget(tab)

    assert tab.add_state_button.property("accent") is True
    assert tab.check_power_intent_button.property("accent") is True


# --- Audit fix (category 03): coverage gaps the first pass left -----------


def test_the_primary_action_buttons_carry_the_accent_hook(qtbot):
    """The hook originally reached only the Power State Table's two
    buttons, while Elaborate / Mapping / Continue -- the app's most
    primary actions, and not dialog defaults, so `QPushButton:default`
    never reached them either -- were left unstyled."""
    from frontend.src.hierarchy_view.load_design_tab import LoadDesignTab
    from frontend.src.power_intent_view.load_power_intent_tab import LoadPowerIntentTab

    design_tab = LoadDesignTab()
    qtbot.addWidget(design_tab)
    assert design_tab.elaborate_button.property("accent") is True
    assert design_tab.continue_button.property("accent") is True

    power_intent_tab = LoadPowerIntentTab()
    qtbot.addWidget(power_intent_tab)
    assert power_intent_tab.mapping_button.property("accent") is True
    assert power_intent_tab.continue_button.property("accent") is True


def test_stylesheet_covers_the_previously_unstyled_interior_widgets():
    """Progress bars, dock titles, dropdown menus, group-box titles,
    check/radio indicators and splitter handles all fell back to the
    stock platform look, so the accent stopped at the window chrome."""
    qss = theme.build_stylesheet()
    for selector in ("QProgressBar", "QDockWidget::title", "QMenu {", "QGroupBox::title", "QCheckBox::indicator:checked", "QSplitter::handle:hover"):
        assert selector in qss, f"{selector} still unstyled"


def test_the_new_rules_still_respect_the_protected_patterns(styled_app):
    """The additions must not smuggle in the two things the theme is
    forbidden to touch -- already asserted generally above, re-asserted
    here so a future addition to this block is checked deliberately
    rather than by luck."""
    qss = theme.build_stylesheet().lower()
    assert "::item:selected" not in qss  # not even mentioned in a comment
    assert theme.TINT_HEADER not in qss  # table headers stay unstyled


# --- a FIXED appearance, independent of the host machine -----------------
#
# The app used to inherit the host's native style AND colours, so a
# server whose desktop was in dark mode painted it dark. These pin that
# it now always runs with Fusion and the fixed light palette, whatever
# the host looks like -- simulated below by giving the app a dark palette
# first, which is what a dark-mode desktop hands Qt.


@pytest.fixture
def restore_appearance(qapp):
    """`qapp` is session-scoped and style/palette are process-wide, so
    whatever a test installs is put back afterwards."""
    style, palette = qapp.style().name(), qapp.palette()
    yield qapp
    qapp.setStyle(style)
    qapp.setPalette(palette)


def _dark_host_palette():
    from PySide6.QtGui import QPalette

    dark = QPalette()
    for role in (QPalette.ColorRole.Window, QPalette.ColorRole.Base, QPalette.ColorRole.Button):
        dark.setColor(role, QColor("#202020"))
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText):
        dark.setColor(role, QColor("#e0e0e0"))
    return dark


def _lightness(color: QColor) -> float:
    return color.lightnessF()


def test_a_dark_host_does_not_make_the_app_dark(restore_appearance):
    from PySide6.QtGui import QPalette

    app = restore_appearance
    app.setPalette(_dark_host_palette())
    theme.apply_fixed_appearance(app)

    palette = app.palette()
    assert app.style().name().lower() == "fusion"
    for role, expected in (("Window", "#efefef"), ("Base", "#ffffff"), ("Text", "#000000"), ("WindowText", "#000000")):
        assert palette.color(getattr(QPalette.ColorRole, role)).name() == expected, role


def test_the_light_palette_has_dark_text_on_light_backgrounds():
    from PySide6.QtGui import QPalette

    palette = theme.build_light_palette()
    for text, background in (("WindowText", "Window"), ("Text", "Base"), ("ButtonText", "Button"), ("ToolTipText", "ToolTipBase")):
        fg = palette.color(getattr(QPalette.ColorRole, text))
        bg = palette.color(getattr(QPalette.ColorRole, background))
        assert _lightness(bg) - _lightness(fg) > 0.6, (text, background)
    # Disabled text is visibly greyed, but still readable on the window.
    disabled = palette.color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text)
    enabled = palette.color(QPalette.ColorGroup.Active, QPalette.ColorRole.Text)
    assert disabled != enabled
    assert _lightness(palette.color(QPalette.ColorRole.Window)) - _lightness(disabled) > 0.2


def test_the_selection_highlight_is_not_the_green_accent():
    """Highlight is the standard list/table row selection -- this change
    is about the host's colours, not about recolouring selection."""
    assert theme.LIGHT_PALETTE["Highlight"] not in {theme.ACCENT, theme.ACCENT_HOVER, theme.LOGO_GREEN}


def test_main_applies_the_fixed_appearance_before_any_widget_is_built(restore_appearance, monkeypatch):
    """The ORDER matters: a widget built before the style and palette are
    set is created with the host's look. Drives the real main() with the
    QApplication and the window stubbed out, and records what the app
    looked like at the moment the window was being built."""
    from PySide6.QtGui import QPalette

    from frontend.src.app import main as main_module

    app = restore_appearance
    app.setPalette(_dark_host_palette())
    previous_stylesheet = app.styleSheet()

    class _App:
        """The session's real QApplication (a second one cannot be
        created), minus the blocking event loop."""

        def __init__(self, argv):
            pass

        def __getattr__(self, name):
            return getattr(app, name)

        def exec(self):
            return 0

    seen = {}

    class _Window:
        def resize(self, *args):
            pass

        def show(self):
            pass

    def _build_main_window():
        seen["style"] = app.style().name().lower()
        seen["window"] = app.palette().color(QPalette.ColorRole.Window).name()
        return _Window()

    monkeypatch.setattr(main_module, "QApplication", _App)
    monkeypatch.setattr(main_module, "build_main_window", _build_main_window)
    try:
        with pytest.raises(SystemExit):
            main_module.main()
    finally:
        app.setStyleSheet(previous_stylesheet)

    assert seen == {"style": "fusion", "window": "#efefef"}
