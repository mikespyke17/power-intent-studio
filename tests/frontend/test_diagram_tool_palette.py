"""power_intent_view/diagram_tool_palette.py -- the vertical tool
palette (explicit request, "like KiCad's icon toolbar"): Regular Cursor/
Pan/Zoom Lens, a separator, Supply Port/Internal Supply/Virtual Supply/
Supply Net/Power Switch/Isolation Cell/Level Shifter, a second
separator, Retention -- all eleven mutually exclusive. No group header
labels (explicit request, removed on a follow-up -- see the module's
own docstring)."""

from frontend.src.power_intent_view.diagram_tool_icons import _render_svg
from frontend.src.power_intent_view.diagram_tool_palette import _ICON_SIZE, DiagramMode, DiagramToolPalette


def _palette(qtbot) -> DiagramToolPalette:
    palette = DiagramToolPalette()
    qtbot.addWidget(palette)
    return palette


def test_orientation_is_vertical(qtbot):
    from PySide6.QtCore import Qt

    palette = _palette(qtbot)
    assert palette.orientation() == Qt.Orientation.Vertical


def test_regular_cursor_is_checked_by_default(qtbot):
    palette = _palette(qtbot)
    assert palette.select_action.isChecked() is True
    assert palette.pan_action.isChecked() is False
    assert palette.zoom_lens_action.isChecked() is False
    assert palette.supply_port_action.isChecked() is False
    assert palette.internal_supply_action.isChecked() is False
    assert palette.virtual_supply_action.isChecked() is False
    assert palette.supply_net_action.isChecked() is False
    assert palette.power_switch_action.isChecked() is False
    assert palette.isolation_cell_action.isChecked() is False
    assert palette.retention_action.isChecked() is False


def test_pan_sits_between_regular_cursor_and_zoom_lens(qtbot):
    """Explicit request: "Pan, positioned between Regular Cursor and
    Zoom Lens"."""
    palette = _palette(qtbot)
    actions = [a for a in palette.actions() if not a.isSeparator()]
    assert [a.text() for a in actions] == [
        "Regular Cursor",
        "Pan",
        "Zoom Lens",
        "Supply Port",
        "Internal Supply",
        "Virtual Supply",
        "Supply Net",
        "Power Switch",
        "Isolation Cell",
        "Level Shifter",
        "Retention",
    ]


def test_internal_supply_sits_directly_after_supply_port(qtbot):
    palette = _palette(qtbot)
    actions = [a for a in palette.actions() if not a.isSeparator()]
    names = [a.text() for a in actions]
    assert names.index("Internal Supply") == names.index("Supply Port") + 1


def test_virtual_supply_sits_directly_after_internal_supply(qtbot):
    palette = _palette(qtbot)
    actions = [a for a in palette.actions() if not a.isSeparator()]
    names = [a.text() for a in actions]
    assert names.index("Virtual Supply") == names.index("Internal Supply") + 1


def test_power_switch_sits_directly_after_supply_net(qtbot):
    palette = _palette(qtbot)
    actions = [a for a in palette.actions() if not a.isSeparator()]
    names = [a.text() for a in actions]
    assert names.index("Power Switch") == names.index("Supply Net") + 1


def test_isolation_cell_sits_directly_after_power_switch(qtbot):
    palette = _palette(qtbot)
    actions = [a for a in palette.actions() if not a.isSeparator()]
    names = [a.text() for a in actions]
    assert names.index("Isolation Cell") == names.index("Power Switch") + 1


def test_isolation_cell_has_the_correct_tooltip(qtbot):
    palette = _palette(qtbot)
    assert palette.isolation_cell_action.toolTip() == "Isolation Cell"


def test_pan_action_has_the_correct_tooltip(qtbot):
    palette = _palette(qtbot)
    assert palette.pan_action.toolTip() == "Pan"


def test_no_group_header_labels(qtbot):
    """Explicit request, removed on a follow-up -- separator alone is
    enough visual grouping."""
    from PySide6.QtWidgets import QLabel

    palette = _palette(qtbot)
    assert palette.findChildren(QLabel) == []


def test_power_switch_action_has_the_correct_tooltip(qtbot):
    palette = _palette(qtbot)
    assert palette.power_switch_action.toolTip() == "Power Switch"


def test_a_separator_sits_between_each_of_the_three_groups(qtbot):
    """Explicit follow-up request: separators reviewed/reset to exactly
    three points -- Zoom Lens|Supply Port, Supply Net|Power Switch,
    Power Switch|Isolation Cell (the old Level Shifter|Retention
    separator was removed, not one of the three specified points)."""
    palette = _palette(qtbot)
    actions = palette.actions()
    assert sum(1 for a in actions if a.isSeparator()) == 3

    def _is_separator_between(before_label: str, after_label: str) -> bool:
        before_index = next(i for i, a in enumerate(actions) if not a.isSeparator() and a.text() == before_label)
        after_index = next(i for i, a in enumerate(actions) if not a.isSeparator() and a.text() == after_label)
        assert after_index == before_index + 2  # exactly one separator action between them
        return actions[before_index + 1].isSeparator()

    assert _is_separator_between("Zoom Lens", "Supply Port") is True
    assert _is_separator_between("Supply Net", "Power Switch") is True
    assert _is_separator_between("Power Switch", "Isolation Cell") is True


def test_every_action_has_a_real_icon(qtbot):
    palette = _palette(qtbot)
    for action in (
        palette.select_action,
        palette.pan_action,
        palette.zoom_lens_action,
        palette.supply_port_action,
        palette.internal_supply_action,
        palette.supply_net_action,
        palette.power_switch_action,
        palette.isolation_cell_action,
        palette.retention_action,
    ):
        assert action.icon().isNull() is False


def test_each_action_uses_the_correct_provided_icon_asset(qtbot):
    """Explicit request: "the current toolbar icons do not match the
    icon assets I provided... verify each button's icon assignment is
    correct" -- pixel-compares each action's own rendered icon against
    the exact diagram_tool_icons.py function/SVG asset it's supposed to
    use, so a future mix-up (wrong file wired to the wrong button) fails
    a test instead of only being caught by eyeballing the toolbar."""
    from frontend.src.power_intent_view.diagram_tool_icons import (
        cursor_icon,
        internal_supply_icon,
        isolation_cell_icon,
        pan_icon,
        power_switch_icon,
        retention_icon,
        supply_net_icon,
        supply_port_icon,
        zoom_lens_icon,
    )

    palette = _palette(qtbot)
    expected = {
        "select_action": cursor_icon,
        "pan_action": pan_icon,
        "zoom_lens_action": zoom_lens_icon,
        "supply_port_action": supply_port_icon,
        "internal_supply_action": internal_supply_icon,
        "supply_net_action": supply_net_icon,
        "power_switch_action": power_switch_icon,
        "isolation_cell_action": isolation_cell_icon,
        "retention_action": retention_icon,
    }
    for attr_name, icon_fn in expected.items():
        action = getattr(palette, attr_name)
        actual = action.icon().pixmap(_ICON_SIZE, _ICON_SIZE).toImage()
        want = icon_fn(_ICON_SIZE).pixmap(_ICON_SIZE, _ICON_SIZE).toImage()
        assert actual == want, f"{attr_name} is not showing the icon from {icon_fn.__name__}()"


def test_checking_an_action_unchecks_every_sibling(qtbot):
    palette = _palette(qtbot)

    palette.supply_net_action.setChecked(True)

    assert palette.select_action.isChecked() is False
    assert palette.pan_action.isChecked() is False
    assert palette.zoom_lens_action.isChecked() is False
    assert palette.supply_port_action.isChecked() is False
    assert palette.internal_supply_action.isChecked() is False
    assert palette.supply_net_action.isChecked() is True
    assert palette.power_switch_action.isChecked() is False
    assert palette.isolation_cell_action.isChecked() is False
    assert palette.retention_action.isChecked() is False


def test_checking_each_action_emits_the_matching_mode(qtbot):
    palette = _palette(qtbot)
    fired = []
    palette.modeChanged.connect(fired.append)

    palette.pan_action.setChecked(True)
    palette.zoom_lens_action.setChecked(True)
    palette.supply_port_action.setChecked(True)
    palette.internal_supply_action.setChecked(True)
    palette.supply_net_action.setChecked(True)
    palette.power_switch_action.setChecked(True)
    palette.isolation_cell_action.setChecked(True)
    palette.retention_action.setChecked(True)
    palette.select_action.setChecked(True)

    assert fired == [
        DiagramMode.PAN,
        DiagramMode.ZOOM_LENS,
        DiagramMode.SUPPLY_PORT,
        DiagramMode.INTERNAL_SUPPLY,
        DiagramMode.SUPPLY_NET,
        DiagramMode.POWER_SWITCH,
        DiagramMode.ISOLATION_CELL,
        DiagramMode.RETENTION,
        DiagramMode.SELECT,
    ]


def test_rechecking_the_already_active_action_does_not_re_emit(qtbot):
    """setChecked(True) on an already-checked exclusive action is a
    documented Qt no-op (no toggled signal) -- same precedent already
    established for View menu dock-toggle actions (see
    app/main_window.py's own _add_view_menu_group() docstring)."""
    palette = _palette(qtbot)
    fired = []
    palette.modeChanged.connect(fired.append)

    palette.select_action.setChecked(True)  # already checked -- no-op

    assert fired == []


# --- item 8: icon quality (supersample-then-downscale) -----------------------


def test_render_svg_returns_the_exact_requested_logical_size(qtbot):
    """Explicit request: fix pixelation without changing how every
    existing caller interprets the returned pixmap's own dimensions --
    the supersample-then-downscale fix must be entirely internal."""
    pixmap = _render_svg("cursor.svg", 24)

    assert pixmap.width() == 24
    assert pixmap.height() == 24


def test_render_svg_is_not_null_for_every_provided_asset(qtbot):
    for filename in (
        "cursor.svg",
        "pan.svg",
        "zoom-lens.svg",
        "supply-port.svg",
        "internal-supply.svg",
        "power-net.svg",
        "retention-flop.svg",
        "power-switch.svg",
    ):
        assert not _render_svg(filename, 28).isNull()
