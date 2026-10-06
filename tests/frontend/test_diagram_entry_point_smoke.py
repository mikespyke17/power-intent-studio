"""Every diagram entry point still opens what it claims to open.

WHY THIS FILE EXISTS. "Add Retention" stopped working entirely -- neither
the right-click action nor the tool-palette tool opened its dialog -- and
nothing caught it. Every Retention test in this repo calls
`create_retention_strategy()` or constructs `RetentionStrategyDialog`
directly, which is the right way to test what a feature DOES and
structurally cannot notice that the path from a click to that code is
broken. The failure was an exception raised inside dialog construction,
which Qt swallows at the signal/slot boundary: the action fires, the
exception is printed to a console nobody is reading, and the user sees
nothing happen at all.

So this file tests the SEAM rather than any one feature: for every
right-click action and every tool-palette mode, invoking it reaches its
handler and does not raise. It is deliberately broad and shallow -- it
does not check what each dialog contains (the per-feature suites do
that), only that the entry point is alive.

THE STATE IT RUNS AGAINST MATTERS. The bug needed ancestor Supply Set
sharing to be present -- a nested domain referencing an ancestor
domain's own primary as its secondary, which is a supported and ordinary
thing to build -- so the fixture below sets exactly that up. A fixture
with one domain and one unshared supply set reproduces nothing, which is
why the existing suites all passed.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QDialog, QMenu

from frontend.src.power_intent_view.diagram_tool_palette import DiagramMode
from frontend.src.power_intent_view.power_intent_tab import PowerIntentTab
from frontend.src.shared.mock_backend import build_mock_design

TOP = "top"
CORE = "top/u_core"


@pytest.fixture
def tab(qtbot):
    t = PowerIntentTab()
    qtbot.addWidget(t)
    t.set_rtl(build_mock_design())
    return t


def _shared_supply_set_state(view):
    """Two domains, the nested one SHARING the ancestor's own primary
    Supply Set as its secondary.

    This is the shape that broke Retention: from PD_TOP's point of view
    its own primary is now "claimed by another domain", so the helper
    that answers "which sets may this domain claim" stopped listing it,
    and the dialog indexing that answer by PD_TOP's own ids died."""
    from PySide6.QtGui import QColor

    from frontend.src.power_intent_view.power_domain_overlay import (
        PowerDomainPlacement,
        SupplySetPlacement,
    )

    red = QColor("#ff0000")
    view.add_supply_port(view._boxes[TOP], "VDD", red)
    view.add_supply_port(view._boxes[TOP], "VSS", QColor("#000080"))
    view.add_internal_supply(view._boxes[CORE], "VDD_CORE", red)

    domain_state = view._domain_state
    domain_state.supply_sets["ss_top"] = SupplySetPlacement(
        id="ss_top", name="SS_TOP", power="VDD", ground="VSS", owner_path=TOP)
    domain_state.supply_sets["ss_core"] = SupplySetPlacement(
        id="ss_core", name="SS_CORE", power="VDD_CORE", ground="VSS", owner_path=CORE)
    domain_state.domains["pd_top"] = PowerDomainPlacement(
        id="pd_top", owner_path=TOP, name="PD_TOP", color=red, primary_supply_set="ss_top")
    domain_state.domains["pd_core"] = PowerDomainPlacement(
        id="pd_core", owner_path=CORE, name="PD_CORE", color=red,
        primary_supply_set="ss_core",
        # THE TRIGGER: the ancestor's own primary, shared down.
        secondary_supply_sets=["ss_top"])
    view._rebuild()
    return domain_state


def _capture_dialogs(monkeypatch, module, *names):
    """Replace each named dialog class with one that records its own
    construction and returns Rejected from exec().

    exec() is a blocking modal that cannot run headlessly -- the hazard
    this repo has hit repeatedly -- and a dialog that never constructs
    is exactly the defect under test, so construction is what gets
    recorded."""
    opened: list[str] = []
    for name in names:
        real = getattr(module, name)

        class _Recording(real):
            _name = name

            def __init__(self, *a, **k):
                super().__init__(*a, **k)
                opened.append(type(self)._name)

            def exec(self):
                return QDialog.DialogCode.Rejected

        monkeypatch.setattr(module, name, _Recording)
    return opened


# --- the reported bug, both entry points ------------------------------------


def test_add_retention_right_click_opens_the_dialog(tab, monkeypatch):
    """Through the REAL context-menu action, not _prompt_add_retention()
    directly: the action's own enabled state and its connected lambda
    are both part of what broke."""
    import frontend.src.power_intent_view.power_intent_diagram_view as module

    view = tab.diagram._view
    _shared_supply_set_state(view)
    opened = _capture_dialogs(monkeypatch, module, "RetentionStrategyDialog")
    warned: list[tuple] = []
    monkeypatch.setattr(module.message_box, "warning", lambda *a, **k: warned.append(a))

    menu = QMenu()
    actions = dict(view._extra_context_menu_actions(menu, view._boxes[TOP]))
    add_retention = next(a for a in actions if a.text() == "Add Retention")
    assert add_retention.isEnabled(), "the action is offered but disabled"

    actions[add_retention]()

    assert opened == ["RetentionStrategyDialog"], f"dialog never opened; warnings={warned}"
    assert not warned


def test_add_retention_tool_palette_opens_the_dialog(tab, monkeypatch, qtbot):
    """Through a REAL mouse press in DiagramMode.RETENTION -- the other
    half of the report, and a genuinely separate code path from the
    context menu even though both funnel into one method."""
    import frontend.src.power_intent_view.power_intent_diagram_view as module

    view = tab.diagram._view
    _shared_supply_set_state(view)
    opened = _capture_dialogs(monkeypatch, module, "RetentionStrategyDialog")
    monkeypatch.setattr(module.message_box, "warning", lambda *a, **k: None)

    tab.tool_palette.retention_action.setChecked(True)  # drives modeChanged -> set_mode
    assert view._mode == DiagramMode.RETENTION

    view.fit_view()
    box = view._boxes[TOP]
    centre = view.mapFromScene(box.sceneBoundingRect().center())
    # A point inside `top` but outside its children, so the press lands
    # on the block the test means.
    target = QPoint(int(view.mapFromScene(box.sceneBoundingRect().topLeft()).x()) + 6,
                    int(view.mapFromScene(box.sceneBoundingRect().topLeft()).y()) + 6)
    hit = view._box_at(target) or view._box_at(centre)
    assert hit is not None
    from PySide6.QtTest import QTest
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     target if view._box_at(target) is not None else centre)

    assert opened == ["RetentionStrategyDialog"], "a real click in Retention mode opened nothing"


def test_the_retention_dialog_offers_the_domains_own_supply_sets(tab):
    """Opening is not enough -- the dialog has to be USABLE. Its two
    required Supply Set pickers must actually list the domain's own
    sets, including one shared with another domain."""
    from frontend.src.power_intent_view.power_domain_overlay import supply_sets_referenced_by_domain
    from frontend.src.power_intent_view.retention_dialog import RetentionStrategyDialog

    view = tab.diagram._view
    domain_state = _shared_supply_set_state(view)
    domain = domain_state.domains["pd_top"]

    dialog = RetentionStrategyDialog(
        domain,
        supply_sets_referenced_by_domain(domain_state, domain),
        set(),
        ["top/state_q"],
        {},
        {},
    )
    names = [dialog.primary_combo.itemText(i) for i in range(dialog.primary_combo.count())]
    assert "SS_TOP" in names, f"the domain's own primary is missing from its picker: {names}"


def test_a_dangling_supply_set_reference_still_opens_the_dialog(tab):
    """Defence in depth. The id that could not be resolved used to raise
    from inside a sort key during __init__; a reference left dangling by
    a deleted Supply Set must degrade to a missing picker entry, never
    to an action that silently does nothing."""
    from frontend.src.power_intent_view.retention_dialog import RetentionStrategyDialog

    view = tab.diagram._view
    domain_state = _shared_supply_set_state(view)
    domain = domain_state.domains["pd_top"]

    dialog = RetentionStrategyDialog(domain, {}, set(), ["top/state_q"], {}, {})
    assert dialog.primary_combo.count() >= 1  # the empty entry, and no crash


# --- the seam, for every entry point ----------------------------------------


_RIGHT_CLICK_ACTIONS = [
    "Inspect Source File", "Create Power Domain", "Edit Power Domain",
    "Remove Power Domain", "Add Power Supply", "Add Internal Supply",
    "Add Virtual Supply", "Add Retention", "Add Isolation", "Add Level Shifter",
    "Load Power Intent", "Edit Power Intent", "Remove Power Intent",
]


@pytest.mark.parametrize("path", [TOP, CORE, "top/u_ram"])
def test_every_right_click_action_is_present_and_reaches_a_handler(tab, monkeypatch, path):
    """Broad and shallow: every action exists, and invoking an ENABLED
    one does not raise. What each one then shows is the per-feature
    suites' business -- this is about the path from click to handler
    staying alive while everything around it changes."""
    import frontend.src.power_intent_view.power_intent_diagram_view as module

    view = tab.diagram._view
    _shared_supply_set_state(view)
    _capture_dialogs(
        monkeypatch, module,
        "RetentionStrategyDialog", "IsolationDialog", "LevelShifterDialog",
        "CreatePowerDomainDialog", "AddPowerSupplyDialog", "PortAttributeDialog",
        "SourceViewerDialog",
    )
    monkeypatch.setattr(module.message_box, "warning", lambda *a, **k: None)
    monkeypatch.setattr(module.message_box, "question", lambda *a, **k: False)
    monkeypatch.setattr(module, "QFileDialog", type("_F", (), {
        "getOpenFileName": staticmethod(lambda *a, **k: ("", ""))}))

    menu = QMenu()
    actions = dict(view._extra_context_menu_actions(menu, view._boxes[path]))
    labels = [a.text() for a in actions]
    for expected in _RIGHT_CLICK_ACTIONS:
        assert expected in labels, f"{path}: '{expected}' is gone from the context menu"

    for action, handler in actions.items():
        if not action.isEnabled():
            continue
        handler()  # must not raise


@pytest.mark.parametrize("mode", list(DiagramMode))
def test_every_tool_palette_mode_survives_a_click_on_a_block(tab, monkeypatch, mode):
    """Each tool's own click handler, driven through a real mouse press
    on a real block. A tool whose handler raises looks, from the
    outside, exactly like a tool that does nothing."""
    import frontend.src.power_intent_view.power_intent_diagram_view as module
    from PySide6.QtTest import QTest

    view = tab.diagram._view
    _shared_supply_set_state(view)
    _capture_dialogs(
        monkeypatch, module,
        "RetentionStrategyDialog", "IsolationDialog", "LevelShifterDialog",
        "AddPowerSupplyDialog", "PowerSwitchDialog",
    )
    monkeypatch.setattr(module.message_box, "warning", lambda *a, **k: None)

    view.set_mode(mode)
    view.fit_view()
    box = view._boxes[CORE]
    point = view.mapFromScene(box.sceneBoundingRect().center())
    QTest.mouseMove(view.viewport(), point)
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point)

    # Select/Pan leave a real scene selection behind, which Qt clears
    # again during teardown -- re-entering the selection handler against
    # a controller that is already gone, and printing a traceback after
    # the run has passed. Dropped here, while everything is still alive,
    # so a traceback in this file's output means something.
    view.scene().clearSelection()


def test_the_tool_palette_exposes_an_action_for_every_mode(tab):
    """A mode with no button is unreachable; a button wired to the wrong
    mode is worse. Both are pinned here rather than assumed."""
    palette = tab.tool_palette
    for mode in DiagramMode:
        action = palette.action_for_mode(mode)
        assert action is not None, f"{mode} has no toolbar action"
        action.setChecked(True)
        assert tab.diagram._view._mode == mode, f"{action.text()!r} does not select {mode}"


# --- the "no sequential elements" warning, through the REAL dialog ----------


def _multi_hierarchy_domain(view):
    """One Power Domain spanning `u_dbg` (ZERO sequential elements in
    its subtree) and `u_core` (two).

    This shape is what makes the warning reachable through the real
    dialog at all -- see the test below."""
    from PySide6.QtGui import QColor

    from frontend.src.power_intent_view.power_domain_overlay import (
        PowerDomainPlacement,
        SupplySetPlacement,
    )

    red = QColor("#ff0000")
    view.add_supply_port(view._boxes[TOP], "VDD", red)
    view.add_supply_port(view._boxes[TOP], "VSS", QColor("#000080"))
    state = view._domain_state
    state.supply_sets["ss"] = SupplySetPlacement(
        id="ss", name="SS", power="VDD", ground="VSS",
        owner_path="top/u_dbg", owner_paths=["top/u_dbg", CORE])
    state.supply_sets["ss_ret"] = SupplySetPlacement(
        id="ss_ret", name="SS_RET", power="VDD", ground="VSS",
        owner_path="top/u_dbg", owner_paths=["top/u_dbg", CORE])
    state.domains["pd"] = PowerDomainPlacement(
        id="pd", owner_path="top/u_dbg", name="PD_MULTI", color=red,
        owner_paths=["top/u_dbg", CORE],
        primary_supply_set="ss", secondary_supply_sets=["ss_ret"])
    view._rebuild()
    return state


def _accept_retention_dialog(monkeypatch, module, name):
    """Fill the real dialog in and accept it, instead of returning
    Rejected -- so the whole path (validate, dialog, accept, create)
    runs."""
    real = module.RetentionStrategyDialog

    class _Accepting(real):
        def exec(self):
            self.name_edit.setText(name)
            self.primary_combo.setCurrentIndex(1)
            self.retention_combo.setCurrentIndex(1)
            self.save_logic_edit.setText("save_clk")
            self.restore_logic_edit.setText("restore_clk")
            for row in range(self.elements_list.count()):
                item = self.elements_list.item(row)
                if item.flags() & Qt.ItemFlag.ItemIsEnabled:
                    item.setCheckState(Qt.CheckState.Checked)
            self.accept()
            return QDialog.DialogCode.Accepted

    monkeypatch.setattr(module, "RetentionStrategyDialog", _Accepting)


def test_retention_created_through_the_dialog_warns_when_the_target_has_no_sequential_elements(
    tab, monkeypatch
):
    """Item 2, end to end. Every existing test of this warning calls
    `create_retention_strategy()` directly; this one goes through the
    real "Add Retention" action and the real dialog.

    WORTH KNOWING: the dialog requires at least one element, so a target
    with no sequential elements ANYWHERE in its domain simply cannot be
    accepted. The warning is therefore only reachable through the UI for
    a MULTI-HIERARCHY domain -- the block the action was invoked on has
    none, while a sibling member supplies the elements. That is exactly
    the case built here."""
    import frontend.src.power_intent_view.power_intent_diagram_view as module

    view = tab.diagram._view
    _multi_hierarchy_domain(view)
    _accept_retention_dialog(monkeypatch, module, "RET_DBG")
    warned: list[tuple] = []
    monkeypatch.setattr(module.message_box, "warning", lambda *a, **k: warned.append(a[1:]))

    menu = QMenu()
    actions = dict(view._extra_context_menu_actions(menu, view._boxes["top/u_dbg"]))
    add_retention = next(a for a in actions if a.text() == "Add Retention")
    actions[add_retention]()

    assert not warned, f"creation was blocked: {warned}"
    created = [s for s in view._retention_state.strategies.values() if s.name == "RET_DBG"]
    assert created, "the strategy was not created"
    assert created[0].owner_path == "top/u_dbg"

    tab.properties.refresh_diagram_overlay(view._shared_state, view._domain_state, view._retention_state)
    table = tab.properties._tables["RetentionStrategy"]
    row = next(r for r in range(table.rowCount()) if table.item(r, 0).text().startswith("RET_DBG"))
    assert table.item(row, 0).text() == "RET_DBG (No Sequential Elements)"
    assert table.item(row, 0).font().italic() is True  # non-blocking, advisory


def test_retention_on_a_target_with_sequential_elements_is_not_warned_about(tab, monkeypatch):
    """The negative case, through the same path: `u_core` has two
    sequential elements, so the identical flow must produce no marker."""
    import frontend.src.power_intent_view.power_intent_diagram_view as module

    view = tab.diagram._view
    _multi_hierarchy_domain(view)
    _accept_retention_dialog(monkeypatch, module, "RET_CORE")
    monkeypatch.setattr(module.message_box, "warning", lambda *a, **k: None)

    menu = QMenu()
    actions = dict(view._extra_context_menu_actions(menu, view._boxes[CORE]))
    actions[next(a for a in actions if a.text() == "Add Retention")]()

    tab.properties.refresh_diagram_overlay(view._shared_state, view._domain_state, view._retention_state)
    table = tab.properties._tables["RetentionStrategy"]
    row = next(r for r in range(table.rowCount()) if table.item(r, 0).text().startswith("RET_CORE"))
    assert table.item(row, 0).text() == "RET_CORE"
    assert table.item(row, 0).font().italic() is False
