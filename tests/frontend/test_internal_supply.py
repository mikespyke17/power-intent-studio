"""'Internal Supply' -- a second, distinct entity type from Supply Port
(explicit request), mutually exclusive by hierarchy level: Supply Port
only on the design's own true top-level block, Internal Supply only on
an inner (non-top-level) block, both excluding macros. Shares
SupplyPortPlacement/the supply_port_placements dict/SupplyPortIconItem/
connector/rename-propagation machinery with Supply Port throughout --
see supply_port_overlay.py's own module docstring for the "why" and
SupplyPortPlacement.is_internal_supply's own docstring for how the two
kinds stay distinguished.

Level-rule enforcement is UI-layer only (hover gating, context-menu
enable state, the _prompt_* methods) -- explicit scope decision,
confirmed via AskUserQuestion: add_supply_port()/add_internal_supply()
(the "directly-testable core" functions) stay level-agnostic, only
macro-excluded, so the large pre-existing Supply Port test suite (which
places ports on arbitrary non-top-level boxes to test unrelated
concerns) needed no sweeping rewrite. This file's own core-level tests
reflect that scope precisely.
"""

import gc

import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap
from PySide6.QtWidgets import QMenu, QMessageBox, QStyleOptionGraphicsItem

from frontend.src.power_intent_view.add_power_supply_dialog import AddPowerSupplyDialog
from frontend.src.power_intent_view.diagram_tool_palette import DiagramMode
from frontend.src.power_intent_view.power_domain_overlay import PowerDomainPlacement, SupplySetPlacement
from frontend.src.power_intent_view.power_intent_tab import PowerIntentTab
from frontend.src.power_intent_view.retention_dialog import RetentionStrategyDialog
from frontend.src.power_intent_view.supply_port_overlay import is_nested_pair, known_supply_names_for_owner
from frontend.src.shared.mock_backend import build_mock_design


def _deselect_and_collect(tab: PowerIntentTab) -> None:
    if tab.diagram._view.scene() is not None:
        tab.diagram._view.scene().clearSelection()
    gc.collect()


def _render_icon_to_image(icon) -> QImage:
    """Same "directly-testable paint()" precedent test_power_intent_supply_ports.py's
    own _render_icon_to_image() already establishes."""
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.translate(32, 32)
    icon.paint(painter, QStyleOptionGraphicsItem())
    painter.end()
    return pixmap.toImage()


# --- add_internal_supply(): directly-testable core, level-agnostic ---------


def test_add_internal_supply_creates_an_icon_flagged_is_internal_supply(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top/u_core"]

    icon = view.add_internal_supply(box, "VDD_INT", QColor("#ff0000"))

    assert icon.placement.is_internal_supply is True
    assert icon.placement.name == "VDD_INT"
    assert view._shared_state.supply_port_placements[icon.key].is_internal_supply is True
    _deselect_and_collect(tab)


def test_add_internal_supply_core_does_not_reject_the_top_level_block(qtbot):
    """Explicit scope decision: the level rule is UI-layer only (hover/
    menu/_prompt_add_internal_supply()), not re-checked inside
    add_internal_supply() itself."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top"]

    icon = view.add_internal_supply(box, "VDD_INT", QColor("#ff0000"))

    assert icon.placement.is_internal_supply is True
    _deselect_and_collect(tab)


def test_add_internal_supply_on_a_macro_is_rejected(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    macro = view._boxes["top/u_ram"]

    with pytest.raises(ValueError, match="macro"):
        view.add_internal_supply(macro, "VDD_INT", QColor("#ff0000"))
    _deselect_and_collect(tab)


def test_add_internal_supply_rejects_a_name_with_an_internal_space(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top/u_core"]

    with pytest.raises(ValueError, match="spaces"):
        view.add_internal_supply(box, "VDD BAD", QColor("#ff0000"))
    _deselect_and_collect(tab)


def test_add_internal_supply_allows_a_duplicate_name_on_the_same_hierarchy(qtbot):
    """CHANGE 1 (explicit request): supersedes the earlier rejected-
    duplicate test -- the global/local Supply Port/Internal Supply
    name-uniqueness rule is removed entirely. Two Internal Supplies may
    now share a name freely, even on the SAME hierarchy block, with no
    error and no forced/implicit relationship between them (no power
    net auto-created just because the names match)."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top/u_core"]
    first = view.add_internal_supply(box, "VDD_INT", QColor("#ff0000"))

    second = view.add_internal_supply(box, "VDD_INT", QColor("#00ff00"))

    assert first.key != second.key
    assert len(first.connectors) == 0
    assert len(second.connectors) == 0
    _deselect_and_collect(tab)


# --- Supply Port / Internal Supply share one name namespace, freely --------
# CHANGE 1 (explicit request): the two entity types used to be rejected as
# duplicates of each other (_check_unique_name(), now removed) -- they may
# now share a name just as freely, both orders, cross-type exactly like
# same-type.


def test_supply_port_then_internal_supply_with_the_same_name_is_allowed(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top/u_core"]
    view.add_supply_port(box, "VDD_A", QColor("#ff0000"))

    icon = view.add_internal_supply(box, "VDD_A", QColor("#00ff00"))

    assert icon is not None
    _deselect_and_collect(tab)


def test_internal_supply_then_supply_port_with_the_same_name_is_allowed(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top/u_core"]
    view.add_internal_supply(box, "VDD_A", QColor("#ff0000"))

    icon = view.add_supply_port(box, "VDD_A", QColor("#00ff00"))

    assert icon is not None
    _deselect_and_collect(tab)


# --- icon rendering: distinct glyph from Supply Port -----------------------


def test_internal_supply_icon_renders_a_different_glyph_than_supply_port(qtbot):
    """Explicit request: same live-paint() QSvgRenderer approach as
    Supply Port, using the distinct internal-supply.svg asset when
    is_internal_supply is set."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    port = view.add_supply_port(view._boxes["top"], "VDD_PORT", QColor("#ff0000"))
    internal = view.add_internal_supply(view._boxes["top/u_core"], "VDD_INT", QColor("#ff0000"))

    port_image = _render_icon_to_image(port)
    internal_image = _render_icon_to_image(internal)

    assert port_image != internal_image
    _deselect_and_collect(tab)


# --- hover gating: level + macro, mirrors Supply Port's own -----------------


def test_internal_supply_hover_highlights_a_valid_inner_non_macro_block(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.resize(900, 700)
    tab.show()
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view.set_mode(DiagramMode.INTERNAL_SUPPLY)
    box = view._boxes["top/u_core"]
    pos = view.mapFromScene(box.sceneBoundingRect().center())

    view._update_internal_supply_hover(pos)

    assert view._internal_supply_hover_box is box
    _deselect_and_collect(tab)


def test_internal_supply_hover_does_not_highlight_the_top_level_block(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.resize(900, 700)
    tab.show()
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view.set_mode(DiagramMode.INTERNAL_SUPPLY)
    box = view._boxes["top"]
    pos = view.mapFromScene(QPointF(box.sceneBoundingRect().center().x(), box.sceneBoundingRect().top() + 5))

    view._update_internal_supply_hover(pos)

    assert view._internal_supply_hover_box is None
    _deselect_and_collect(tab)


def test_internal_supply_hover_does_not_highlight_a_macro(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.resize(900, 700)
    tab.show()
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view.set_mode(DiagramMode.INTERNAL_SUPPLY)
    macro = view._boxes["top/u_ram"]
    pos = view.mapFromScene(macro.sceneBoundingRect().center())

    view._update_internal_supply_hover(pos)

    assert view._internal_supply_hover_box is None
    _deselect_and_collect(tab)


def test_supply_port_and_internal_supply_hover_are_independent(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.resize(900, 700)
    tab.show()
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    top = view._boxes["top"]
    inner = view._boxes["top/u_core"]
    top_pos = view.mapFromScene(QPointF(top.sceneBoundingRect().center().x(), top.sceneBoundingRect().top() + 5))
    inner_pos = view.mapFromScene(inner.sceneBoundingRect().center())

    view.set_mode(DiagramMode.SUPPLY_PORT)
    view._update_supply_port_hover(top_pos)
    assert view._supply_port_hover_box is top
    assert view._internal_supply_hover_box is None

    view.set_mode(DiagramMode.INTERNAL_SUPPLY)
    assert view._supply_port_hover_box is None  # cleared on mode switch
    view._update_internal_supply_hover(inner_pos)
    assert view._internal_supply_hover_box is inner
    _deselect_and_collect(tab)


# --- click placement + right-click menu -------------------------------------


def test_click_on_a_highlighted_block_in_internal_supply_mode_places_it(qtbot, monkeypatch):
    from PySide6.QtTest import QTest

    def fake_exec(self):
        self.name_edit.setText("VDD_INT")
        self.color = QColor("#123456")
        return self.DialogCode.Accepted

    monkeypatch.setattr(AddPowerSupplyDialog, "exec", fake_exec)
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.resize(900, 700)
    tab.show()
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view.set_mode(DiagramMode.INTERNAL_SUPPLY)
    box = view._boxes["top/u_core"]
    pos = view.mapFromScene(box.sceneBoundingRect().center())

    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, pos)
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, pos)

    placed = [icon for icon in view._supply_port_icons.values() if icon.owner_box is box]
    assert len(placed) == 1
    assert placed[0].name == "VDD_INT"
    assert placed[0].placement.is_internal_supply is True
    _deselect_and_collect(tab)


def test_add_internal_supply_menu_item_disabled_on_the_top_level_block(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view

    menu = QMenu()
    actions = view._extra_context_menu_actions(menu, view._boxes["top"])
    add_internal_action = next(a for a, _cb in actions if a.text() == "Add Internal Supply")
    add_supply_action = next(a for a, _cb in actions if a.text() == "Add Power Supply")

    assert add_internal_action.isEnabled() is False
    assert add_supply_action.isEnabled() is True


def test_add_internal_supply_menu_item_enabled_on_an_inner_non_macro_block(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view

    menu = QMenu()
    actions = view._extra_context_menu_actions(menu, view._boxes["top/u_core"])
    add_internal_action = next(a for a, _cb in actions if a.text() == "Add Internal Supply")
    add_supply_action = next(a for a, _cb in actions if a.text() == "Add Power Supply")

    assert add_internal_action.isEnabled() is True
    assert add_supply_action.isEnabled() is False


def test_add_internal_supply_menu_item_disabled_on_a_macro(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view

    menu = QMenu()
    actions = view._extra_context_menu_actions(menu, view._boxes["top/u_ram"])
    add_internal_action = next(a for a, _cb in actions if a.text() == "Add Internal Supply")

    assert add_internal_action.isEnabled() is False


def test_right_click_add_internal_supply_is_wired(qtbot, monkeypatch):
    def fake_exec(self):
        self.name_edit.setText("VDD_INT")
        self.color = QColor("#123456")
        return self.DialogCode.Accepted

    monkeypatch.setattr(AddPowerSupplyDialog, "exec", fake_exec)
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top/u_core"]

    menu = QMenu()
    actions = view._extra_context_menu_actions(menu, box)
    add_internal = next(cb for a, cb in actions if a.text() == "Add Internal Supply")
    add_internal()

    placed = [icon for icon in view._supply_port_icons.values() if icon.owner_box is box]
    assert len(placed) == 1
    assert placed[0].placement.is_internal_supply is True


# --- Edit/Delete/rename propagation: same mechanism as Supply Port ---------


def test_internal_supply_can_be_edited_and_deleted_via_the_same_icon_menu(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top/u_core"]
    icon = view.add_internal_supply(box, "VDD_INT", QColor("#ff0000"))

    menu = QMenu()
    actions = view._supply_port_context_menu_actions(menu, icon)
    assert [a.text() for a, _cb in actions] == ["Edit", "Delete", "Add Power Switch", "Edit Power Switch", "Delete Power Switch"]

    delete_cb = next(cb for a, cb in actions if a.text() == "Delete")
    delete_cb()
    assert icon.key not in view._shared_state.supply_port_placements
    _deselect_and_collect(tab)


def test_renaming_an_internal_supply_propagates_to_the_supply_set_that_references_it(qtbot):
    """Same mechanism as Supply Port's own rename propagation (explicit
    request: extend the shared mechanism to a third entity type rather
    than building a separate implementation) --
    _propagate_supply_port_rename() is name+owner_path scoped and
    doesn't care about is_internal_supply at all."""
    from frontend.src.power_intent_view.power_domain_overlay import SupplySetPlacement

    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top/u_core"]
    icon = view.add_internal_supply(box, "VDD_OLD", QColor("#ff0000"))
    supply_set = SupplySetPlacement(id="s1", name="SS1", power="VDD_OLD", ground="VSS", owner_path="top/u_core")
    view._domain_state.supply_sets["s1"] = supply_set

    view.edit_supply_port(icon, "VDD_NEW", QColor("#ff0000"))

    updated_set = view._domain_state.supply_sets["s1"]
    assert updated_set.power == "VDD_NEW"
    assert updated_set.ground == "VSS"  # untouched
    assert icon.placement.is_internal_supply is True  # rename never touches the kind flag
    _deselect_and_collect(tab)


# --- name validation: same no-spaces rule as the other 4 entity types -------


def test_add_internal_supply_dialog_rejects_a_name_with_an_internal_space_end_to_end(qtbot, monkeypatch):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top/u_core"]
    warnings = []
    monkeypatch.setattr(
        "frontend.src.power_intent_view.power_intent_diagram_view.message_box.warning",
        lambda parent, title, text: warnings.append((title, text)),
    )
    calls = iter([("VDD BAD", QColor("#ff0000")), ("VDD_OK", QColor("#ff0000"))])

    def fake_exec(self):
        name, color = next(calls)
        self.name_edit.setText(name)
        self.color = color
        return self.DialogCode.Accepted

    monkeypatch.setattr(AddPowerSupplyDialog, "exec", fake_exec)

    view._prompt_add_internal_supply(box)

    assert len(warnings) == 1
    assert warnings[0][1] == "Name cannot contain spaces."
    placed = [icon for icon in view._supply_port_icons.values() if icon.owner_box is box]
    assert len(placed) == 1
    assert placed[0].name == "VDD_OK"
    _deselect_and_collect(tab)


# --- Properties panel: Internal Supply excluded from the Supply Ports tab --


def test_internal_supply_is_excluded_from_the_supply_ports_properties_tab(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view.add_supply_port(view._boxes["top"], "VDD_PORT", QColor("#ff0000"))
    view.add_internal_supply(view._boxes["top/u_core"], "VDD_INT", QColor("#00ff00"))

    names = [tab.properties._tables["SupplyPort"].item(r, 1).text() for r in range(tab.properties._tables["SupplyPort"].rowCount())]

    assert "VDD_PORT" in names
    assert "VDD_INT" not in names
    _deselect_and_collect(tab)


# --- Supply Net: Supply Port <-> Internal Supply cross-type connection -----


def test_is_nested_pair_accepts_a_top_level_and_inner_path_either_direction():
    assert is_nested_pair("top", "top/u_core") is True
    assert is_nested_pair("top/u_core", "top") is True


def test_supply_port_can_connect_to_an_internal_supply(qtbot):
    """Explicit request: confirms the existing connector logic already
    treats both entity types' anchor points uniformly -- no type-based
    restriction needed, since is_nested_pair() (the only real gating
    rule try_connect_supply_ports() applies) is satisfied by construction
    whenever one side is a top-level Supply Port and the other an inner
    Internal Supply (the two are mutually exclusive by level, so any
    such pair is automatically an ancestor/descendant one)."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    port = view.add_supply_port(view._boxes["top"], "VDD_PORT", QColor("#ff0000"))
    internal = view.add_internal_supply(view._boxes["top/u_core"], "VDD_INT", QColor("#ff0000"))

    connected = view.try_connect_supply_ports(port, internal)

    assert connected is True
    assert (port.key, internal.key) in view._shared_state.supply_connector_placements
    _deselect_and_collect(tab)


# --- Retention dialog: domain-scoped Supply Set list includes both types ---


def test_known_supply_names_for_owner_includes_internal_supply_names(qtbot):
    """The name pool Retention's own "Add new Supply Set..." flow draws
    from (known_supply_names_for_owner()) already covers both Supply
    Port and Internal Supply names for free, since both live in the same
    supply_port_placements dict -- see supply_port_overlay.py's own
    module docstring."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view.add_internal_supply(view._boxes["top/u_core"], "VDD_INT", QColor("#ff0000"))

    names = known_supply_names_for_owner(view._shared_state, "top/u_core")

    assert "VDD_INT" in names


def test_retention_dialog_supply_set_list_is_scoped_to_the_target_domain(qtbot):
    """Same domain-scoping principle as the Power Domain creation Supply
    Set list fix -- _prompt_add_retention() now passes
    supply_sets_for_owner(...) instead of the raw, unscoped
    self._domain_state.supply_sets dict."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    own_set = SupplySetPlacement(id="s_own", name="SS_OWN", power="VDD_OWN", owner_path="top/u_core")
    other_set = SupplySetPlacement(id="s_other", name="SS_OTHER", power="VDD_OTHER", owner_path="top/u_dbg")
    view._domain_state.supply_sets["s_own"] = own_set
    view._domain_state.supply_sets["s_other"] = other_set
    domain = PowerDomainPlacement(id="d1", owner_path="top/u_core", name="PD_CORE", color=QColor("#ff0000"), primary_supply_set="s_own")
    view._domain_state.domains["d1"] = domain

    captured = {}
    original_init = RetentionStrategyDialog.__init__

    def _capture_init(self, domain_arg, supply_sets, *args, **kwargs):
        captured["supply_sets"] = supply_sets
        original_init(self, domain_arg, supply_sets, *args, **kwargs)

    import pytest as _pytest

    from PySide6.QtWidgets import QDialog

    with _pytest.MonkeyPatch.context() as mp:
        mp.setattr(RetentionStrategyDialog, "__init__", _capture_init)
        mp.setattr(RetentionStrategyDialog, "exec", lambda self: QDialog.DialogCode.Rejected)
        view._prompt_add_retention(view._boxes["top/u_core"])

    assert {s.name for s in captured["supply_sets"].values()} == {"SS_OWN"}
