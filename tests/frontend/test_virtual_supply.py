"""'Virtual Supply' -- a THIRD, distinct supply type from Supply Port/
Internal Supply (explicit request, full spec in the branch that
introduced it). Shares SupplyPortPlacement/the supply_port_placements
dict/SupplyPortIconItem/connector machinery with the other two kinds
throughout (see supply_port_overlay.py's own module docstring), but is:

- placeable at ANY hierarchy level (no top-level-only/inner-only rule);
- restricted to connecting ONLY to a macro's own INTERNAL PG pin, in a
  ONE-DIRECTIONAL way (a regular Supply Port/Internal Supply remains
  freely able to connect to that same pin, exactly as before);
- never a valid Power Switch target;
- NOT scoped to any Power Domain, and NOT selectable as any Supply
  Set's Power/Ground/Nwell/Pwell rail;
- required (not optional) to carry at least one authored Power State,
  exactly like a top-level Supply Port's own (never inherited the way
  an Internal Supply's are);
- subject to its own narrow, EXPLICITLY-SCOPED name-uniqueness rule
  (against every other Virtual Supply AND every Supply Port/Internal
  Supply) -- this does NOT reintroduce the general Supply Port/Internal
  Supply uniqueness rule that CHANGE 1 deliberately removed; those two
  kinds may still freely share a name with each other.

Letters (a)-(l) in test names below map directly to the originating
spec's own enumerated test list."""

import gc

import pytest
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap
from PySide6.QtWidgets import QMenu, QStyleOptionGraphicsItem

from frontend.src.power_intent_view.add_power_supply_dialog import AddPowerSupplyDialog
from frontend.src.power_intent_view.diagram_tool_palette import DiagramMode
from frontend.src.power_intent_view.power_domain_overlay import PowerDomainDiagramState, PowerDomainPlacement, SupplySetPlacement
from frontend.src.power_intent_view.power_intent_tab import PowerIntentTab
from frontend.src.power_intent_view.power_intent_text_export import generate_cpf, generate_upf
from frontend.src.power_intent_view.power_state_table import ordered_supply_nets
from frontend.src.power_intent_view.power_switch_overlay import PowerSwitchDiagramState
from frontend.src.power_intent_view.retention_overlay import RetentionDiagramState
from frontend.src.power_intent_view.supply_port_overlay import SupplyPortDiagramState, SupplyPortPlacement, SupplyPortState, known_supply_names_for_owner
from frontend.src.shared.mock_backend import build_mock_design


def _deselect_and_collect(tab: PowerIntentTab) -> None:
    if tab.diagram._view.scene() is not None:
        tab.diagram._view.scene().clearSelection()
    gc.collect()


def _render_icon_to_image(icon) -> QImage:
    pixmap = QPixmap(64, 64)
    pixmap.fill(QColor("transparent"))
    painter = QPainter(pixmap)
    painter.translate(32, 32)
    icon.paint(painter, QStyleOptionGraphicsItem())
    painter.end()
    return pixmap.toImage()


def _states(*names: str) -> list[SupplyPortState]:
    """N distinct rail states.

    States carry no name anymore, so the arguments are read purely as "how
    many, and is one of them OFF" -- each levelled state gets its own
    voltage so they stay genuinely distinct (identical Type AND Voltage
    is a duplicate now, and would collapse)."""
    out = []
    for i, name in enumerate(names):
        if name.upper() == "OFF":
            out.append(SupplyPortState("OFF"))
        else:
            out.append(SupplyPortState("FULL_ON", f"{1.0 + i / 10:.1f}"))
    return out


# --- (a) creation via both entry points -------------------------------------


def test_a_add_virtual_supply_core_creates_an_icon_with_name_colour_and_states(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top/u_core"]

    icon = view.add_virtual_supply(box, "VVIRT", QColor("#123456"), states=_states("ACTIVE"))

    assert icon.placement.is_virtual_supply is True
    assert icon.placement.name == "VVIRT"
    assert icon.placement.color == QColor("#123456")
    assert [(s.state_type, s.voltage) for s in icon.placement.states] == [("FULL_ON", "1.0")]
    _deselect_and_collect(tab)


def test_a_add_virtual_supply_works_at_any_hierarchy_level_top_and_inner_and_macro_excluded(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view

    top_icon = view.add_virtual_supply(view._boxes["top"], "V_TOP", QColor("#ff0000"), states=_states("ACTIVE"))
    inner_icon = view.add_virtual_supply(view._boxes["top/u_core"], "V_INNER", QColor("#00ff00"), states=_states("ACTIVE"))

    assert top_icon.placement.is_virtual_supply is True
    assert inner_icon.placement.is_virtual_supply is True
    _deselect_and_collect(tab)


def test_a_click_on_a_highlighted_block_in_virtual_supply_mode_places_it(qtbot, monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    def fake_exec(self):
        self.name_edit.setText("VVIRT")
        self.color = QColor("#123456")
        self.states_widget.states = _states("ACTIVE")
        return self.DialogCode.Accepted

    monkeypatch.setattr(AddPowerSupplyDialog, "exec", fake_exec)
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.resize(900, 700)
    tab.show()
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view.set_mode(DiagramMode.VIRTUAL_SUPPLY)
    box = view._boxes["top/u_core"]
    pos = view.mapFromScene(box.sceneBoundingRect().center())

    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, pos)
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, pos)

    placed = [icon for icon in view._supply_port_icons.values() if icon.owner_box is box and icon.placement.is_virtual_supply]
    assert len(placed) == 1
    assert placed[0].name == "VVIRT"
    _deselect_and_collect(tab)


def test_a_right_click_add_virtual_supply_is_wired(qtbot, monkeypatch):
    def fake_exec(self):
        self.name_edit.setText("VVIRT")
        self.color = QColor("#123456")
        self.states_widget.states = _states("ACTIVE")
        return self.DialogCode.Accepted

    monkeypatch.setattr(AddPowerSupplyDialog, "exec", fake_exec)
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top/u_core"]

    menu = QMenu()
    actions = view._extra_context_menu_actions(menu, box)
    labels = [a.text() for a, _cb in actions]
    assert "Add Virtual Supply" in labels
    assert labels.index("Add Virtual Supply") == labels.index("Add Internal Supply") + 1
    add_virtual = next(cb for a, cb in actions if a.text() == "Add Virtual Supply")
    add_virtual()

    placed = [icon for icon in view._supply_port_icons.values() if icon.owner_box is box and icon.placement.is_virtual_supply]
    assert len(placed) == 1
    _deselect_and_collect(tab)


def test_a_add_virtual_supply_menu_item_disabled_on_a_macro(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view

    menu = QMenu()
    actions = view._extra_context_menu_actions(menu, view._boxes["top/u_ram"])
    add_virtual_action = next(a for a, _cb in actions if a.text() == "Add Virtual Supply")

    assert add_virtual_action.isEnabled() is False


# --- (b) creation with no Power States rejected ------------------------------


def test_b_add_virtual_supply_core_rejects_no_states(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top/u_core"]

    with pytest.raises(ValueError, match="Power State"):
        view.add_virtual_supply(box, "VVIRT", QColor("#ff0000"), states=[])
    _deselect_and_collect(tab)


def test_b_dialog_accept_rejects_no_states_for_virtual_supply(qtbot, monkeypatch):
    monkeypatch.setattr(
        "frontend.src.power_intent_view.add_power_supply_dialog.message_box.warning",
        lambda parent, title, text: None,
    )
    dialog = AddPowerSupplyDialog(None, target_path="top/u_core", is_virtual_supply=True)
    qtbot.addWidget(dialog)
    dialog.name_edit.setText("VVIRT")

    accepted = []
    dialog.accepted.connect(lambda: accepted.append(True))
    dialog.accept()

    assert accepted == []  # rejected -- no states entered


# --- (c) name collision -------------------------------------------------------


def test_c_virtual_supply_name_conflicts_with_an_existing_supply_port(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view.add_supply_port(view._boxes["top"], "SHARED", QColor("#ff0000"))

    with pytest.raises(ValueError, match="SHARED"):
        view.add_virtual_supply(view._boxes["top/u_core"], "SHARED", QColor("#00ff00"), states=_states("ACTIVE"))
    _deselect_and_collect(tab)


def test_c_virtual_supply_name_conflicts_with_an_existing_internal_supply(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view.add_internal_supply(view._boxes["top/u_core"], "SHARED", QColor("#ff0000"))

    with pytest.raises(ValueError, match="SHARED"):
        view.add_virtual_supply(view._boxes["top/u_dbg"], "SHARED", QColor("#00ff00"), states=_states("ACTIVE"))
    _deselect_and_collect(tab)


def test_c_virtual_supply_name_conflicts_with_another_virtual_supply(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view.add_virtual_supply(view._boxes["top/u_core"], "SHARED", QColor("#ff0000"), states=_states("ACTIVE"))

    with pytest.raises(ValueError, match="SHARED"):
        view.add_virtual_supply(view._boxes["top/u_dbg"], "SHARED", QColor("#00ff00"), states=_states("ACTIVE"))
    _deselect_and_collect(tab)


def test_c_supply_port_and_internal_supply_can_still_freely_share_a_name(qtbot):
    """CHANGE 1 stays in force -- Virtual Supply's own narrow uniqueness
    rule (this file's own (c) tests above) is a deliberate, scoped
    exception, not a reintroduction of the general rule."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view.add_supply_port(view._boxes["top"], "VDD_A", QColor("#ff0000"))

    icon = view.add_internal_supply(view._boxes["top/u_core"], "VDD_A", QColor("#00ff00"))

    assert icon is not None
    _deselect_and_collect(tab)


def test_c_editing_a_virtual_supply_to_a_conflicting_name_is_rejected(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view.add_supply_port(view._boxes["top"], "TAKEN", QColor("#ff0000"))
    icon = view.add_virtual_supply(view._boxes["top/u_core"], "VVIRT", QColor("#00ff00"), states=_states("ACTIVE"))

    with pytest.raises(ValueError, match="TAKEN"):
        view.edit_supply_port(icon, "TAKEN", QColor("#00ff00"), states=_states("ACTIVE"))
    _deselect_and_collect(tab)


def test_c_editing_a_virtual_supply_to_have_no_states_is_rejected(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    icon = view.add_virtual_supply(view._boxes["top/u_core"], "VVIRT", QColor("#00ff00"), states=_states("ACTIVE"))

    with pytest.raises(ValueError, match="Power State"):
        view.edit_supply_port(icon, "VVIRT", QColor("#00ff00"), states=[])
    _deselect_and_collect(tab)


# --- (d) icon rendering: bottom anchor only, no top anchor -------------------


def test_d_virtual_supply_icon_has_only_a_bottom_handle(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    icon = view.add_virtual_supply(view._boxes["top/u_core"], "VVIRT", QColor("#ff0000"), states=_states("ACTIVE"))

    assert set(icon.handles) == {"bottom"}
    _deselect_and_collect(tab)


def test_d_virtual_supply_icon_renders_a_different_glyph_than_supply_port_and_internal_supply(qtbot):
    """virtual-supply's own silhouette (a diamond with a white "V") is
    genuinely distinct from both supply-port.svg's own pentagon AND
    internal-supply.svg's plain (glyph-less) diamond -- confirmed pixel-
    different against both. Bug fix history: an earlier version of this
    icon rendered PIXEL-IDENTICAL to Internal Supply once tinted, since
    the "V" was drawn fully opaque (not a transparent cutout) and
    render_tinted_svg() masks purely by alpha -- see
    test_d_virtual_supply_glyph_stays_white_while_fill_colour_changes()
    below for the fix (a genuine two-layer fill/glyph split) and its own
    dedicated regression coverage."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    port = view.add_supply_port(view._boxes["top"], "VDD_PORT", QColor("#ff0000"))
    internal = view.add_internal_supply(view._boxes["top/u_core"], "VDD_INT", QColor("#ff0000"))
    virtual = view.add_virtual_supply(view._boxes["top/u_core"], "VVIRT", QColor("#ff0000"), states=_states("ACTIVE"))

    port_image = _render_icon_to_image(port)
    internal_image = _render_icon_to_image(internal)
    virtual_image = _render_icon_to_image(virtual)

    assert virtual_image != port_image
    assert virtual_image != internal_image
    _deselect_and_collect(tab)


def test_d_virtual_supply_uses_its_own_dedicated_fill_and_glyph_svg_renderers(qtbot):
    """Confirms paint() genuinely uses two separate renderers for
    Virtual Supply (the tinted fill layer + the plain white glyph
    layer), each distinct from Internal Supply's own single renderer."""
    from frontend.src.power_intent_view.supply_port_overlay import (
        _internal_supply_svg_renderer,
        _virtual_supply_fill_svg_renderer,
        _virtual_supply_glyph_svg_renderer,
    )

    assert _virtual_supply_fill_svg_renderer() is not _internal_supply_svg_renderer()
    assert _virtual_supply_glyph_svg_renderer() is not _virtual_supply_fill_svg_renderer()


def test_d_virtual_supply_glyph_stays_white_while_fill_colour_changes(qtbot):
    """Regression test for the reported bug ("V" glyph getting
    overwritten by the fill colour): the fill (diamond) and the glyph
    ("V") are now two genuinely separate rendering layers (see paint()'s
    own is_virtual_supply branch -- a tinted render_tinted_svg() call for
    the fill, then a plain/untinted renderer.render() call for the
    glyph on top). Exercised through the real "Edit" path
    (edit_supply_port(), the same one the Edit Virtual Supply dialog's
    Colour field goes through), across several different colours --
    every time, the fill pixel must equal the chosen colour exactly and
    the glyph pixel must stay solid white.

    Sample offsets (from the rendered icon's own center) were located
    empirically by scanning the full rendered image for a solid-fill
    pixel clear of the glyph's own bounding area, and a solid-glyph
    pixel clear of the diamond's own edge antialiasing."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    icon = view.add_virtual_supply(view._boxes["top/u_core"], "VVIRT", QColor("#ff0000"), states=_states("ACTIVE"))

    fill_offset = (0, -12)  # top vertex of the diamond, clear of the "V"
    glyph_offset = (-4, -4)  # inside the "V" stroke itself

    for hex_color in ["#ff0000", "#00ff00", "#0000ff", "#ffaa00", "#123456"]:
        view.edit_supply_port(icon, icon.placement.name, QColor(hex_color), states=icon.placement.states)
        image = _render_icon_to_image(icon)
        center = image.width() // 2
        fill_pixel = image.pixelColor(center + fill_offset[0], center + fill_offset[1])
        glyph_pixel = image.pixelColor(center + glyph_offset[0], center + glyph_offset[1])

        expected = QColor(hex_color)
        # Small tolerance (not exact-match): the sample point sits near
        # the diamond's own top vertex, where antialiasing can shift a
        # channel by a rounding step of 1 against the fully transparent
        # background just outside it -- irrelevant to what this test is
        # actually confirming (that the fill genuinely follows the
        # chosen colour, not pixel-perfect antialiasing behaviour).
        assert (
            abs(fill_pixel.red() - expected.red()) <= 2 and abs(fill_pixel.green() - expected.green()) <= 2 and abs(fill_pixel.blue() - expected.blue()) <= 2
        ), f"fill did not follow colour {hex_color}: got {fill_pixel.name()}"
        assert glyph_pixel.red() > 240 and glyph_pixel.green() > 240 and glyph_pixel.blue() > 240, (
            f"glyph pixel was recoloured to {glyph_pixel.name()} instead of staying white for fill colour {hex_color}"
        )
    _deselect_and_collect(tab)


def test_d_virtual_supply_icon_colour_follows_the_dialog_colour(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    icon = view.add_virtual_supply(view._boxes["top/u_core"], "VVIRT", QColor("#00ffcc"), states=_states("ACTIVE"))

    assert icon.color == QColor("#00ffcc")
    _deselect_and_collect(tab)


# --- (e)/(f)/(g) connection restrictions --------------------------------------


def _macro_pg_icons(view):
    vdd = next(i for i in view._supply_port_icons.values() if i.owner_box.path == "top/u_ram" and i.name == "VDD")  # main PG, is_internal False
    vss = next(i for i in view._supply_port_icons.values() if i.owner_box.path == "top/u_ram" and i.name == "VSS")  # internal PG, is_internal True
    return vdd, vss


def test_e_virtual_supply_connects_to_a_macro_internal_pg_pin(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    _vdd, vss = _macro_pg_icons(view)
    virtual = view.add_virtual_supply(view._boxes["top"], "VVIRT", QColor("#ff0000"), states=_states("ACTIVE"))

    connected = view.try_connect_supply_ports(virtual, vss)

    assert connected is True
    assert (virtual.key, vss.key) in view._shared_state.supply_connector_placements
    _deselect_and_collect(tab)


def test_f_virtual_supply_rejected_connecting_to_a_macro_main_non_internal_pg_pin(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    vdd, _vss = _macro_pg_icons(view)
    virtual = view.add_virtual_supply(view._boxes["top"], "VVIRT", QColor("#ff0000"), states=_states("ACTIVE"))

    connected = view.try_connect_supply_ports(virtual, vdd)

    assert connected is False
    _deselect_and_collect(tab)


def test_f_virtual_supply_rejected_connecting_to_a_regular_supply_port(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    port = view.add_supply_port(view._boxes["top"], "VDD_PORT", QColor("#ff0000"))
    virtual = view.add_virtual_supply(view._boxes["top"], "VVIRT", QColor("#00ff00"), states=_states("ACTIVE"))

    connected = view.try_connect_supply_ports(virtual, port)

    assert connected is False
    _deselect_and_collect(tab)


def test_f_virtual_supply_rejected_connecting_to_an_internal_supply(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    internal = view.add_internal_supply(view._boxes["top/u_core"], "VDD_INT", QColor("#ff0000"))
    virtual = view.add_virtual_supply(view._boxes["top/u_core"], "VVIRT", QColor("#00ff00"), states=_states("ACTIVE"))

    connected = view.try_connect_supply_ports(virtual, internal)

    assert connected is False
    _deselect_and_collect(tab)


def test_f_virtual_supply_rejected_connecting_to_another_virtual_supply(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    virtual_a = view.add_virtual_supply(view._boxes["top"], "VA", QColor("#ff0000"), states=_states("ACTIVE"))
    virtual_b = view.add_virtual_supply(view._boxes["top/u_core"], "VB", QColor("#00ff00"), states=_states("ACTIVE"))

    connected = view.try_connect_supply_ports(virtual_a, virtual_b)

    assert connected is False
    _deselect_and_collect(tab)


def test_f_rejection_is_direction_agnostic(qtbot):
    """The one-directional RULE (only a Virtual Supply's own outgoing
    connections are restricted) is still checked regardless of which
    side of the call is `source` vs `target` -- try_connect_supply_ports()
    is itself direction-agnostic about which side the user dragged from."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    port = view.add_supply_port(view._boxes["top"], "VDD_PORT", QColor("#ff0000"))
    virtual = view.add_virtual_supply(view._boxes["top"], "VVIRT", QColor("#00ff00"), states=_states("ACTIVE"))

    assert view.try_connect_supply_ports(port, virtual) is False
    _deselect_and_collect(tab)


def test_g_regular_supply_port_still_connects_to_a_macro_internal_pg_pin(qtbot):
    """Confirms the ONE-DIRECTIONAL rule: unrelated to Virtual Supply at
    all, a plain Supply Port -> macro internal PG pin connection must
    remain completely unaffected."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    _vdd, vss = _macro_pg_icons(view)
    port = view.add_supply_port(view._boxes["top"], "VDD_PORT", QColor("#ff0000"))

    connected = view.try_connect_supply_ports(port, vss)

    assert connected is True
    _deselect_and_collect(tab)


def test_g_internal_supply_still_connects_to_a_macro_internal_pg_pin(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    _vdd, vss = _macro_pg_icons(view)
    internal = view.add_internal_supply(view._boxes["top"], "VDD_INT", QColor("#ff0000"))

    connected = view.try_connect_supply_ports(internal, vss)

    assert connected is True
    _deselect_and_collect(tab)


def test_g_macro_internal_pg_pin_still_renders_with_no_bottom_anchor_unaffected(qtbot):
    """Regression guard: the pre-existing "internal PG pin has no
    interior handle" rendering rule (test_power_intent_supply_ports.py's
    own test_macro_internal_pg_pin_renders_with_the_internal_supply_icon_and_no_bottom_anchor)
    must remain unbroken by _dropped_handle_key()'s own new
    is_virtual_supply branch."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    _vdd, vss = _macro_pg_icons(view)

    assert set(vss.handles) == {"top"}
    _deselect_and_collect(tab)


# --- (h) Power Switch never allowed on a Virtual Supply -----------------------


def test_h_add_power_switch_core_rejects_a_virtual_supply_target(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    virtual = view.add_virtual_supply(view._boxes["top/u_core"], "VVIRT", QColor("#ff0000"), states=_states("ACTIVE"))

    with pytest.raises(ValueError, match="Virtual Supply"):
        view.add_power_switch(virtual, "SW1", "VIN", "VOUT", "CTRL", None, "on", "off")
    _deselect_and_collect(tab)


def test_h_power_switch_add_error_flags_a_virtual_supply_before_anything_else(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    virtual = view.add_virtual_supply(view._boxes["top/u_core"], "VVIRT", QColor("#ff0000"), states=_states("ACTIVE"))

    error = view._power_switch_add_error(virtual)

    assert error is not None
    assert "Virtual Supply" in error
    _deselect_and_collect(tab)


def test_h_virtual_supply_is_never_a_valid_power_switch_hover_target(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    virtual = view.add_virtual_supply(view._boxes["top/u_core"], "VVIRT", QColor("#ff0000"), states=_states("ACTIVE"))

    assert view._is_valid_power_switch_target(virtual) is False
    _deselect_and_collect(tab)


# --- (i) never selectable as a Supply Set rail --------------------------------


def test_i_virtual_supply_is_offered_as_a_supply_set_rail_for_its_owner(qtbot):
    """RULE REVERSAL (section 3): a Virtual Supply used to be excluded
    from every Supply-Set-composing picker. It is now selectable as
    Power/Ground/Nwell/Pwell exactly like a Supply Port or an Internal
    Supply. Supply Set membership is a LOGICAL grouping -- separate from
    the physical connection restriction, which is unchanged (it still
    only ever wires to a macro's internal PG pins)."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view.add_virtual_supply(view._boxes["top/u_core"], "VVIRT", QColor("#ff0000"), states=_states("ACTIVE"))

    assert "VVIRT" in known_supply_names_for_owner(view._shared_state, "top/u_core")
    _deselect_and_collect(tab)


def test_i_virtual_supply_is_offered_design_wide_too(qtbot):
    from frontend.src.power_intent_view.supply_port_overlay import known_supply_names_globally

    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view.add_virtual_supply(view._boxes["top/u_core"], "VVIRT", QColor("#ff0000"), states=_states("ACTIVE"))

    assert "VVIRT" in known_supply_names_globally(view._shared_state)
    _deselect_and_collect(tab)


def test_i_a_virtual_supply_chosen_as_a_rail_participates_in_its_supply_sets_states(qtbot):
    """Section 3(b): once selected, it takes part in Tier 1 generation
    exactly like any other rail -- no separate or simpler path."""
    from frontend.src.power_intent_view.power_domain_overlay import PowerDomainPlacement, SupplySetPlacement
    from frontend.src.power_intent_view.power_state_table import PowerStateTableDiagramState, PowerStateTableRow

    supply_state = SupplyPortDiagramState(
        supply_port_placements={
            "vvirt": _port("vvirt", "VVIRT", states=_states("ACTIVE"), is_virtual_supply=True),
            "vss": _port("vss", "VSS", states=_states("GND")),
        }
    )
    domain_state = PowerDomainDiagramState(
        supply_sets={"ss": SupplySetPlacement(id="ss", name="SS_V", power="VVIRT", ground="VSS", owner_path="top", owner_paths=["top"])},
        domains={"d": PowerDomainPlacement(id="d", owner_path="top", owner_paths=["top"], name="PD", color=QColor("#ff0000"), primary_supply_set="ss")},
    )
    table = PowerStateTableDiagramState(rows={
        "r": PowerStateTableRow(id="r", power_domain_id="d", state_name="ON",
                                net_states={"vvirt": "FULL_ON 1.0", "vss": "FULL_ON 1.0"}),
    })

    text = generate_upf(supply_state, domain_state, RetentionDiagramState(), "3.1",
                        design_name="top", power_state_table_state=table)

    assert "add_power_state -supply SS_V" in text
    assert "power == {FULL_ON 1.0}" in text


# --- (j) Properties table: Kind column ----------------------------------------


def test_j_virtual_supply_appears_in_supply_ports_tab_with_kind_and_states(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view.add_virtual_supply(view._boxes["top/u_core"], "VVIRT", QColor("#ff0000"), states=_states("ACTIVE", "OFF"))

    table = tab.properties._tables["SupplyPort"]
    row = next(r for r in range(table.rowCount()) if "VVIRT" in table.item(r, 1).text())

    assert table.item(row, 0).text() == "Virtual Supply"
    _deselect_and_collect(tab)


def test_j_supply_port_and_internal_supply_kinds_still_correct_alongside_virtual(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view.add_supply_port(view._boxes["top"], "VDD_PORT", QColor("#ff0000"))
    view.add_internal_supply(view._boxes["top/u_core"], "VDD_INT", QColor("#00ff00"))
    view.add_virtual_supply(view._boxes["top/u_core"], "VVIRT", QColor("#0000ff"), states=_states("ACTIVE"))

    table = tab.properties._tables["SupplyPort"]
    kinds_by_name = {}
    for r in range(table.rowCount()):
        name_cell = table.item(r, 1).text()
        kinds_by_name[name_cell] = table.item(r, 0).text()

    assert any(k == "Supply Port" for name, k in kinds_by_name.items() if "VDD_PORT" in name)
    assert any(k == "Internal Supply" for name, k in kinds_by_name.items() if "VDD_INT" in name)
    assert any(k == "Virtual Supply" for name, k in kinds_by_name.items() if "VVIRT" in name)
    _deselect_and_collect(tab)


# --- (k) Power State Table column ordering ------------------------------------


def _port(id_, name, states=None, owner_path="top", **kwargs):
    return SupplyPortPlacement(id=id_, owner_path=owner_path, name=name, color=QColor("#ff0000"), states=states or [], **kwargs)


def test_k_virtual_supply_sits_between_power_and_ground_groups():
    supply_state = SupplyPortDiagramState(
        supply_port_placements={
            "vdd": _port("vdd", "VDD"),
            "vss": _port("vss", "VSS"),
            "vvirt": _port("vvirt", "VVIRT", states=_states("ACTIVE"), is_virtual_supply=True),
        }
    )
    domain_state = PowerDomainDiagramState(supply_sets={"ss1": SupplySetPlacement(id="ss1", name="SS1", power="VDD", ground="VSS")})

    nets = ordered_supply_nets(supply_state, domain_state)

    assert [n.name for n in nets] == ["VDD", "VVIRT", "VSS"]


def test_k_multiple_virtual_supplies_all_land_between_power_and_ground():
    supply_state = SupplyPortDiagramState(
        supply_port_placements={
            "vdd": _port("vdd", "VDD"),
            "va": _port("va", "VA", states=_states("ACTIVE"), is_virtual_supply=True),
            "vb": _port("vb", "VB", states=_states("ACTIVE"), is_virtual_supply=True),
            "vss": _port("vss", "VSS"),
        }
    )
    domain_state = PowerDomainDiagramState(supply_sets={"ss1": SupplySetPlacement(id="ss1", name="SS1", power="VDD", ground="VSS")})

    nets = ordered_supply_nets(supply_state, domain_state)

    assert [n.name for n in nets] == ["VDD", "VA", "VB", "VSS"]


def test_k_virtual_supply_with_no_supply_set_at_all_still_lands_in_its_own_group():
    supply_state = SupplyPortDiagramState(
        supply_port_placements={"vvirt": _port("vvirt", "VVIRT", states=_states("ACTIVE"), is_virtual_supply=True)}
    )
    domain_state = PowerDomainDiagramState()

    nets = ordered_supply_nets(supply_state, domain_state)

    assert [n.name for n in nets] == ["VVIRT"]


def test_k_virtual_supply_column_appears_end_to_end_with_its_own_states_as_dropdown_options(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view.add_supply_port(view._boxes["top"], "VDD", QColor("#ff0000"))
    view.add_virtual_supply(view._boxes["top/u_core"], "VVIRT", QColor("#00ff00"), states=_states("ACTIVE", "RETAIN"))

    nets = ordered_supply_nets(view._shared_state, view._domain_state)
    virtual_net = next(n for n in nets if n.name == "VVIRT")

    from frontend.src.power_intent_view.power_state_table import net_effective_state_keys

    option_names = net_effective_state_keys(virtual_net, view._shared_state, None)
    # Dropdown values are canonical KEYS now, not names.
    assert option_names == ["FULL_ON 1.0", "FULL_ON 1.1"]
    _deselect_and_collect(tab)


# --- (l) UPF/CPF generation ----------------------------------------------------


def test_l_upf_emits_virtual_supply_as_a_plain_supply_net_not_a_supply_port():
    supply_state = SupplyPortDiagramState(
        supply_port_placements={"vvirt": _port("vvirt", "VVIRT", states=_states("ACTIVE"), is_virtual_supply=True)}
    )

    text = generate_upf(supply_state, PowerDomainDiagramState(), RetentionDiagramState(), "3.1")

    assert "create_supply_net VVIRT" in text
    assert "create_supply_port VVIRT" not in text
    assert "connect_supply_net" not in text or "VVIRT" not in [
        line for line in text.splitlines() if line.startswith("connect_supply_net")
    ]


def test_l_a_standalone_virtual_supply_emits_no_power_state_of_its_own():
    """REDESIGNED: per-net `add_power_state` generation is gone entirely.
    A rail's states are no longer an independent fact to emit -- they are
    what a Supply Set's own derived states are composed FROM (Tier 1). A
    Virtual Supply belonging to no Supply Set therefore contributes no
    power state to the output, only its `create_supply_net`. Its
    standalone macro-PG-driving role is unchanged (section 3)."""
    supply_state = SupplyPortDiagramState(
        supply_port_placements={"vvirt": _port("vvirt", "VVIRT", states=_states("ACTIVE", "OFF"), is_virtual_supply=True)}
    )

    text = generate_upf(supply_state, PowerDomainDiagramState(), RetentionDiagramState(), "3.1")

    assert "create_supply_net VVIRT" in text
    assert "add_power_state" not in text


def test_l_cpf_emits_virtual_supply_as_a_power_net_with_states():
    supply_state = SupplyPortDiagramState(
        supply_port_placements={"vvirt": _port("vvirt", "VVIRT", states=_states("ACTIVE"), is_virtual_supply=True)}
    )

    text = generate_cpf(supply_state, PowerDomainDiagramState(), RetentionDiagramState(), "2.0")

    assert "create_power_nets -nets {VVIRT}" in text
    assert "VVIRT" not in "\n".join(line for line in text.splitlines() if line.startswith("#") and "unclassified" in line.lower())


def test_l_cpf_virtual_supply_never_lands_in_the_unclassified_comment_block():
    supply_state = SupplyPortDiagramState(
        supply_port_placements={"vvirt": _port("vvirt", "VVIRT", states=_states("ACTIVE"), is_virtual_supply=True)}
    )

    text = generate_cpf(supply_state, PowerDomainDiagramState(), RetentionDiagramState(), "2.0")

    assert "not used as any Supply Set's power/ground role" not in text
