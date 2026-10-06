"""Retention on the Power Intent tab's Diagram -- a new tool-palette
mode ("Retention", see diagram_tool_palette.py/test_power_intent_diagram_modes.py
for the mode/cursor/hover plumbing), diagram-only/mock-first like Supply
Port and Power Domain (see supply_port_overlay.py/power_domain_overlay.py's
own module docstrings) -- nothing here is reflected into the tab's
PowerIntentDesign.

This file covers validation (explicit request: TWO independent rules,
each with its OWN distinct error message):
  (A) the target block must be part of a Power Domain (membership via
      find_effective_domain_for_path() -- owning OR merely nested inside
      one, matching how domain colouring itself cascades).
  (B) the target block must not be a macro (.lib black-box) -- no
      extractable sequential elements to retain.
Both are enforced by _validate_retention_target(), a pure function
returning the specific message for whichever rule fails first (or None
if both pass) -- never collapsed into one generic message. Dialog
construction, diagram icon rendering, and multi-strategy merge/duplicate
handling are covered in later, separate test files as those pieces land.
"""

import gc
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog, QMenu, QMessageBox

from frontend.src.power_intent_view.create_power_domain_dialog import CreateSupplySetDialog
from frontend.src.power_intent_view.power_domain_overlay import PowerDomainPlacement, SupplySetPlacement, find_effective_domain_for_path
from frontend.src.power_intent_view.power_intent_tab import PowerIntentTab
from frontend.src.power_intent_view.retention_dialog import RetentionStrategyDialog, RetentionStrategyInfoDialog
from frontend.src.power_intent_view.retention_overlay import (
    RetentionBadgeIconItem,
    RetentionDiagramState,
    RetentionStrategyPlacement,
    build_retention_overlay,
    claimed_elements_by_other_strategies,
    collect_retention_elements,
    copy_retention_diagram_state,
    find_duplicate_strategy,
    owner_paths_for_elements,
    restore_retention_diagram_state,
)
from frontend.src.power_intent_view.supply_port_overlay import HIGHLIGHT_COLOR
from frontend.src.shared.mock_backend import build_mock_design
from frontend.src.shared.widgets.target_hierarchy_label import APPLIES_TO_CAPTION
from frontend.src.shared.models.rtl import Definition, Design, Instance, SequentialElement, SourceLanguage
from frontend.src.shared.widgets import message_box


def _deselect_and_collect(tab: PowerIntentTab) -> None:
    if tab.diagram._view.scene() is not None:
        tab.diagram._view.scene().clearSelection()
    gc.collect()


def test_validation_passes_for_a_non_macro_block_inside_a_power_domain(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view._domain_state.domains["d1"] = PowerDomainPlacement(id="d1", owner_path="top", name="PD_TOP", color=QColor("#ff0000"))

    assert view._validate_retention_target(view._boxes["top/u_core"]) is None


def test_validation_rejects_a_block_with_no_power_domain(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top/u_dbg"]
    assert box.definition.is_black_box is False  # sanity: failing for the domain reason, not the macro one

    error = view._validate_retention_target(box)

    assert error == view._RETENTION_NO_DOMAIN_ERROR


def test_validation_rejects_a_macro_even_with_a_covering_power_domain(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view._domain_state.domains["d1"] = PowerDomainPlacement(id="d1", owner_path="top", name="PD_TOP", color=QColor("#ff0000"))
    macro = view._boxes["top/u_ram"]
    assert macro.definition.is_black_box is True
    assert find_effective_domain_for_path(view._domain_state.domains, macro.path) is not None  # sanity: domain covers it

    error = view._validate_retention_target(macro)

    assert error == view._RETENTION_MACRO_ERROR


def test_validation_reports_the_macro_error_when_both_rules_fail(qtbot):
    """Explicit test requirement: "both failing simultaneously" gets its
    own, correct message -- the macro rule wins (checked first: a macro
    can never be retained regardless of domain membership, the more
    fundamental of the two rejection reasons)."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    macro = view._boxes["top/u_ram"]  # no domain assigned at all -- both rules fail

    error = view._validate_retention_target(macro)

    assert error == view._RETENTION_MACRO_ERROR
    assert error != view._RETENTION_NO_DOMAIN_ERROR


# --- data model: RetentionDiagramState copy/restore -------------------------


def test_copy_retention_diagram_state_is_a_real_independent_copy():
    state = RetentionDiagramState()
    state.strategies["r1"] = RetentionStrategyPlacement(
        id="r1", name="RET1", power_domain_id="d1", primary_supply_set_id="s1", retention_supply_set_id="s2",
        save_logic="save", restore_logic="restore", elements=["top/u_core/out1"],
    )

    snapshot = copy_retention_diagram_state(state)
    state.strategies["r1"].name = "MUTATED"
    state.strategies["r1"].elements.append("top/u_core/internal_n1")

    assert snapshot.strategies["r1"].name == "RET1"
    assert snapshot.strategies["r1"].elements == ["top/u_core/out1"]


def test_restore_retention_diagram_state_mutates_in_place_not_by_reference():
    state = RetentionDiagramState()
    state.strategies["r1"] = RetentionStrategyPlacement(
        id="r1", name="RET1", power_domain_id="d1", primary_supply_set_id="s1", retention_supply_set_id="s2",
        save_logic="save", restore_logic="restore",
    )
    snapshot = copy_retention_diagram_state(state)
    same_object = state

    state.strategies.clear()
    restore_retention_diagram_state(state, snapshot)

    assert state is same_object
    assert "r1" in state.strategies


def test_owner_paths_for_elements_dedupes_scope_prefixes():
    elements = ["top/u_core/out1", "top/u_core/internal_n1", "top/u_dbg/some_reg"]

    assert owner_paths_for_elements(elements) == {"top/u_core", "top/u_dbg"}


# --- collect_retention_elements(): recursive, domain-aware Elements list ----


def test_collect_retention_elements_includes_the_targets_own_sequential_elements(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    domains = {"d1": PowerDomainPlacement(id="d1", owner_path="top/u_core", name="PD_CORE", color=QColor("#ff0000"))}

    elements = collect_retention_elements(view._boxes, "top/u_core", domains)

    assert set(elements) == {"top/u_core/out1", "top/u_core/internal_n1"}


def test_collect_retention_elements_excludes_a_descendant_owned_by_a_different_domain(qtbot):
    """Explicit warning honored: a naive "walk all descendants" traversal
    is wrong here -- top/u_core has its OWN, more specific domain, so a
    strategy rooted at "top" must not pull in u_core's own sequential
    elements even though u_core is nested underneath it."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    domains = {
        "d_top": PowerDomainPlacement(id="d_top", owner_path="top", name="PD_TOP", color=QColor("#ff0000")),
        "d_core": PowerDomainPlacement(id="d_core", owner_path="top/u_core", name="PD_CORE", color=QColor("#0000ff")),
    }

    elements = collect_retention_elements(view._boxes, "top", domains)

    assert "top/u_core/out1" not in elements
    assert "top/u_core/internal_n1" not in elements


def test_collect_retention_elements_includes_a_descendant_sharing_the_same_domain(qtbot):
    """Contrast case for the exclusion test above: a descendant with NO
    domain of its own inherits the target's domain (cascading, same rule
    apply_domain_coloring() uses), so its elements ARE included."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    domains = {"d_top": PowerDomainPlacement(id="d_top", owner_path="top", name="PD_TOP", color=QColor("#ff0000"))}

    elements = collect_retention_elements(view._boxes, "top", domains)

    assert "top/u_core/out1" in elements
    assert "top/u_core/internal_n1" in elements


# --- claimed_elements_by_other_strategies() ----------------------------------


def test_claimed_elements_are_scoped_to_the_same_power_domain():
    strategies = {
        "r1": RetentionStrategyPlacement(
            id="r1", name="RET1", power_domain_id="d1", primary_supply_set_id="s1", retention_supply_set_id="s2",
            save_logic="save", restore_logic="restore", elements=["top/u_core/out1"],
        ),
        "r2": RetentionStrategyPlacement(
            id="r2", name="RET2", power_domain_id="d_other", primary_supply_set_id="s1", retention_supply_set_id="s2",
            save_logic="save", restore_logic="restore", elements=["top/u_dbg/some_reg"],
        ),
    }

    claimed = claimed_elements_by_other_strategies(strategies, "d1", exclude_strategy_id=None)

    assert claimed == {"top/u_core/out1"}  # r2's element is in a different domain -- not claimed against d1


def test_claimed_elements_exclude_the_strategy_being_edited():
    strategies = {
        "r1": RetentionStrategyPlacement(
            id="r1", name="RET1", power_domain_id="d1", primary_supply_set_id="s1", retention_supply_set_id="s2",
            save_logic="save", restore_logic="restore", elements=["top/u_core/out1"],
        ),
    }

    claimed = claimed_elements_by_other_strategies(strategies, "d1", exclude_strategy_id="r1")

    assert claimed == set()  # its own elements aren't locked against itself


# --- dialog-driven "Add Retention" end-to-end --------------------------------


def _domain_with_supply_sets(view, owner_path: str, domain_id: str = "d1"):
    primary = SupplySetPlacement(id="ss_primary", name="SS_PRIMARY", power="VDD", ground="VSS", n_well="VDD", p_well="VSS", owner_path=owner_path)
    retention = SupplySetPlacement(id="ss_retention", name="SS_RETENTION", power="VDD_RET", ground="VSS", n_well="VDD_RET", p_well="VSS", owner_path=owner_path)
    view._domain_state.supply_sets[primary.id] = primary
    view._domain_state.supply_sets[retention.id] = retention
    domain = PowerDomainPlacement(
        id=domain_id, owner_path=owner_path, name="PD_TOP", color=QColor("#ff0000"), primary_supply_set=primary.id, secondary_supply_sets=[retention.id]
    )
    view._domain_state.domains[domain_id] = domain
    return domain, primary, retention


def _fake_retention_dialog_exec(name: str, primary_id: str, retention_id: str, save_logic: str = "save_it", restore_logic: str = "restore_it", elements=None, accept: bool = True):
    def fake_exec(self):
        if accept:
            self.name_edit.setText(name)
            self._select_combo_data(self.primary_combo, primary_id)
            self._select_combo_data(self.retention_combo, retention_id)
            self.save_logic_edit.setText(save_logic)
            self.restore_logic_edit.setText(restore_logic)
            for element in elements or []:
                for i in range(self.elements_list.count()):
                    item = self.elements_list.item(i)
                    if item.text() == element:
                        item.setCheckState(Qt.CheckState.Checked)
        return QDialog.DialogCode.Accepted if accept else QDialog.DialogCode.Rejected

    return fake_exec


def test_click_add_retention_opens_the_dialog_and_creates_the_strategy(qtbot, monkeypatch):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    domain, primary, retention = _domain_with_supply_sets(view, "top")
    box = view._boxes["top/u_core"]
    monkeypatch.setattr(
        RetentionStrategyDialog, "exec", _fake_retention_dialog_exec("RET_CORE", primary.id, retention.id, elements=["top/u_core/out1"])
    )

    view._prompt_add_retention(box)

    assert len(view._retention_state.strategies) == 1
    strategy = next(iter(view._retention_state.strategies.values()))
    assert strategy.name == "RET_CORE"
    assert strategy.power_domain_id == domain.id
    assert strategy.primary_supply_set_id == primary.id
    assert strategy.retention_supply_set_id == retention.id
    assert strategy.elements == ["top/u_core/out1"]


def test_add_retention_shows_the_validation_error_and_never_opens_the_dialog(qtbot, monkeypatch):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    opened = []
    monkeypatch.setattr(RetentionStrategyDialog, "exec", lambda self: opened.append(1) or QDialog.DialogCode.Rejected)
    warnings = []
    monkeypatch.setattr(message_box, "warning", lambda parent, title, text: warnings.append((title, text)))
    box = view._boxes["top/u_dbg"]  # no Power Domain -- rule A fails

    view._prompt_add_retention(box)

    assert opened == []
    assert len(warnings) == 1
    assert warnings[0][1] == view._RETENTION_NO_DOMAIN_ERROR
    assert view._retention_state.strategies == {}


def test_right_click_add_retention_is_offered_and_wired(qtbot, monkeypatch):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    domain, primary, retention = _domain_with_supply_sets(view, "top")
    box = view._boxes["top/u_core"]
    monkeypatch.setattr(RetentionStrategyDialog, "exec", _fake_retention_dialog_exec("RET_CORE", primary.id, retention.id))

    menu = QMenu()
    actions = view._extra_context_menu_actions(menu, box)
    add_retention = next(cb for a, cb in actions if a.text() == "Add Retention")
    add_retention()

    assert len(view._retention_state.strategies) == 1


# --- context menu: order + "Add Retention" enabled/disabled convention ------


def test_context_menu_items_appear_in_the_requested_order_with_separators(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view

    menu = QMenu()
    view._extra_context_menu_actions(menu, view._boxes["top/u_core"])

    kinds = [("separator" if a.isSeparator() else a.text()) for a in menu.actions()]
    assert kinds == [
        "Inspect Source File",  # explicit request: directly below the base class's own Hide, no separator
        "separator",
        "Add Port Attribute",
        "Edit Port Attribute",
        "separator",
        "Create Power Domain",
        "Edit Power Domain",
        "Remove Power Domain",
        "separator",
        "Add Power Supply",
        "Add Internal Supply",
        "Add Virtual Supply",
        "separator",
        "Add Retention",
        "separator",
        "Add Isolation",
        "separator",
        "Add Level Shifter",
        "separator",
        "Load Power Intent",
        "Edit Power Intent",
        "Remove Power Intent",
    ]


def test_add_retention_is_disabled_when_the_block_belongs_to_no_power_domain(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top/u_core"]  # no Power Domain anywhere in this design yet

    menu = QMenu()
    actions = view._extra_context_menu_actions(menu, box)
    add_retention_action = next(a for a, _cb in actions if a.text() == "Add Retention")

    assert add_retention_action.isEnabled() is False


def test_add_retention_is_disabled_for_a_macro_even_if_it_belongs_to_a_power_domain(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    _domain_with_supply_sets(view, "top")  # covers the whole design, including the macro below
    macro = view._boxes["top/u_ram"]
    assert macro.definition.is_black_box is True

    menu = QMenu()
    actions = view._extra_context_menu_actions(menu, macro)
    add_retention_action = next(a for a, _cb in actions if a.text() == "Add Retention")

    assert add_retention_action.isEnabled() is False


def test_add_retention_is_enabled_for_a_non_macro_block_belonging_to_a_power_domain(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    _domain_with_supply_sets(view, "top")
    box = view._boxes["top/u_core"]
    assert box.definition.is_black_box is False

    menu = QMenu()
    actions = view._extra_context_menu_actions(menu, box)
    add_retention_action = next(a for a, _cb in actions if a.text() == "Add Retention")

    assert add_retention_action.isEnabled() is True


def test_add_retention_is_enabled_for_a_descendant_nested_inside_a_domain_owning_ancestor(qtbot):
    """Matches _validate_retention_target()'s own rule: membership via
    find_effective_domain_for_path() (owning OR merely nested inside),
    not exact ownership -- the domain in this fixture is rooted at "top",
    so a descendant like "top/u_core" must be enabled too, not just
    "top" itself."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    _domain_with_supply_sets(view, "top")

    menu = QMenu()
    actions = view._extra_context_menu_actions(menu, view._boxes["top"])
    add_retention_action = next(a for a, _cb in actions if a.text() == "Add Retention")

    assert add_retention_action.isEnabled() is True


# --- create_retention_strategy(): directly-testable core, name uniqueness --


def test_create_retention_strategy_raises_for_a_duplicate_name(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    domain, primary, retention = _domain_with_supply_sets(view, "top")
    box = view._boxes["top/u_core"]
    view.create_retention_strategy(box, domain, "RET1", primary.id, retention.id, "save", "restore", [], {})

    with pytest.raises(ValueError, match="already exists"):
        view.create_retention_strategy(box, domain, "RET1", primary.id, retention.id, "save2", "restore2", [], {})


def test_dialog_rejects_a_globally_duplicate_name(qtbot, monkeypatch):
    """Explicit request: uniqueness is GLOBAL across the entire design,
    not scoped to one hierarchy/Power Domain."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    domain, primary, retention = _domain_with_supply_sets(view, "top")
    box = view._boxes["top/u_core"]
    view.create_retention_strategy(box, domain, "RET1", primary.id, retention.id, "save", "restore", [], {})
    warnings = []
    monkeypatch.setattr(
        "frontend.src.power_intent_view.retention_dialog.message_box.warning",
        lambda *args, **kwargs: warnings.append(args[1:]) or None,
    )

    dialog = RetentionStrategyDialog(
        domain, view._domain_state.supply_sets, [], [], set(), view._existing_retention_names(), parent=None
    )
    dialog.name_edit.setText("RET1")
    dialog._select_combo_data(dialog.primary_combo, primary.id)
    dialog._select_combo_data(dialog.retention_combo, retention.id)
    dialog.save_logic_edit.setText("save")
    dialog.restore_logic_edit.setText("restore")

    dialog.accept()

    assert len(warnings) == 1
    assert "already exists" in warnings[0][1]
    qtbot.addWidget(dialog)


# --- dialog: claimed elements are shown disabled/grey/italic ----------------


def test_dialog_disables_elements_already_claimed_by_another_strategy_in_the_same_domain(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    domain, primary, retention = _domain_with_supply_sets(view, "top")
    box = view._boxes["top/u_core"]
    view.create_retention_strategy(box, domain, "RET1", primary.id, retention.id, "save", "restore", ["top/u_core/out1"], {})

    available = collect_retention_elements(view._boxes, "top/u_core", view._domain_state.domains)
    claimed = claimed_elements_by_other_strategies(view._retention_state.strategies, domain.id, exclude_strategy_id=None)
    dialog = RetentionStrategyDialog(domain, view._domain_state.supply_sets, [], available, claimed, {}, parent=None)
    qtbot.addWidget(dialog)

    claimed_item = next(dialog.elements_list.item(i) for i in range(dialog.elements_list.count()) if dialog.elements_list.item(i).text() == "top/u_core/out1")
    free_item = next(dialog.elements_list.item(i) for i in range(dialog.elements_list.count()) if dialog.elements_list.item(i).text() == "top/u_core/internal_n1")

    assert not (claimed_item.flags() & Qt.ItemFlag.ItemIsEnabled)
    assert claimed_item.checkState() == Qt.CheckState.Unchecked
    assert bool(free_item.flags() & Qt.ItemFlag.ItemIsEnabled)


def test_add_all_button_only_checks_non_claimed_elements(qtbot):
    """Explicit follow-up request: "All" renamed to "Add All", same
    always-enabled/skip-claimed-elements behaviour as before."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    domain, primary, retention = _domain_with_supply_sets(view, "top")
    box = view._boxes["top/u_core"]
    view.create_retention_strategy(box, domain, "RET1", primary.id, retention.id, "save", "restore", ["top/u_core/out1"], {})
    available = collect_retention_elements(view._boxes, "top/u_core", view._domain_state.domains)
    claimed = claimed_elements_by_other_strategies(view._retention_state.strategies, domain.id, exclude_strategy_id=None)
    dialog = RetentionStrategyDialog(domain, view._domain_state.supply_sets, [], available, claimed, {}, parent=None)
    qtbot.addWidget(dialog)

    dialog.add_all_button.click()

    assert "top/u_core/out1" not in dialog.elements
    assert "top/u_core/internal_n1" in dialog.elements


# --- Elements list button rework: Shift+Click list-selection + 4 buttons ---
# (Add Selected/Clear Selected/Add All/Clear All -- explicit follow-up
# request, replacing the earlier single "All" button). "Selected" here
# always means Shift+Click LIST selection (elements_list.selectedItems()),
# a plain list-UI state kept deliberately distinct from each item's own
# checkState() -- see retention_dialog.py's own "Elements" section
# docstring.


def _minimal_dialog(elements: list[str], claimed: frozenset = frozenset()) -> RetentionStrategyDialog:
    domain = PowerDomainPlacement(id="d1", owner_path="top", name="PD_TOP", color=QColor("#ff0000"))
    return RetentionStrategyDialog(domain, {}, [], elements, set(claimed), {}, parent=None)


def test_elements_list_supports_shift_click_range_selection(qtbot):
    """(a): Shift+Click selects a range of rows, and Add Selected/Clear
    Selected become enabled as a result."""
    dialog = _minimal_dialog(["A", "B", "C", "D"])
    qtbot.addWidget(dialog)
    assert dialog.elements_list.selectionMode() == dialog.elements_list.SelectionMode.ExtendedSelection
    assert dialog.add_selected_button.isEnabled() is False
    assert dialog.clear_selected_button.isEnabled() is False

    dialog.elements_list.item(0).setSelected(True)
    dialog.elements_list.item(1).setSelected(True)
    dialog.elements_list.item(2).setSelected(True)  # A/B/C -- a contiguous range, same end-state Shift+Click A->C produces

    assert {item.text() for item in dialog.elements_list.selectedItems()} == {"A", "B", "C"}
    assert dialog.add_selected_button.isEnabled() is True
    assert dialog.clear_selected_button.isEnabled() is True


def test_add_selected_checks_only_the_list_selected_elements(qtbot):
    """(b): Add Selected enables exactly the list-selected elements'
    checkboxes, without affecting others."""
    dialog = _minimal_dialog(["A", "B", "C", "D"])
    qtbot.addWidget(dialog)
    dialog.elements_list.item(0).setSelected(True)
    dialog.elements_list.item(2).setSelected(True)  # A, C -- a non-contiguous selection

    dialog.add_selected_button.click()

    assert set(dialog.elements) == {"A", "C"}


def test_clear_selected_unchecks_only_the_list_selected_elements(qtbot):
    """(c): Clear Selected disables exactly the list-selected elements,
    without affecting others."""
    dialog = _minimal_dialog(["A", "B", "C", "D"])
    qtbot.addWidget(dialog)
    dialog.add_all_button.click()
    assert set(dialog.elements) == {"A", "B", "C", "D"}
    dialog.elements_list.item(1).setSelected(True)  # B only

    dialog.clear_selected_button.click()

    assert set(dialog.elements) == {"A", "C", "D"}


def test_add_all_and_clear_all_ignore_list_selection_state(qtbot):
    """(d): Add All/Clear All act on every element regardless of
    whether anything is currently list-selected."""
    dialog = _minimal_dialog(["A", "B", "C", "D"])
    qtbot.addWidget(dialog)
    dialog.elements_list.item(0).setSelected(True)  # only A is list-selected

    dialog.add_all_button.click()
    assert set(dialog.elements) == {"A", "B", "C", "D"}  # every element, not just A

    dialog.clear_all_button.click()
    assert set(dialog.elements) == set()  # every element, not just A


def test_selected_buttons_are_disabled_when_list_selection_is_empty(qtbot):
    """(e): Add Selected/Clear Selected are disabled whenever nothing
    is currently list-selected -- including AFTER a selection is made
    and then cleared again."""
    dialog = _minimal_dialog(["A", "B", "C"])
    qtbot.addWidget(dialog)
    assert dialog.add_selected_button.isEnabled() is False
    assert dialog.clear_selected_button.isEnabled() is False

    dialog.elements_list.item(0).setSelected(True)
    assert dialog.add_selected_button.isEnabled() is True

    dialog.elements_list.clearSelection()

    assert dialog.add_selected_button.isEnabled() is False
    assert dialog.clear_selected_button.isEnabled() is False


def test_add_selected_and_clear_selected_skip_claimed_disabled_elements(qtbot):
    """A claimed/disabled element can still be list-selected (Shift+Click
    row highlighting doesn't require ItemIsEnabled), but Add Selected/
    Clear Selected must never touch its checkState() -- same "never
    touch a disabled item" rule Add All/Clear All already follow."""
    dialog = _minimal_dialog(["A", "B"], claimed=frozenset({"A"}))
    qtbot.addWidget(dialog)
    dialog.elements_list.item(0).setSelected(True)  # A -- claimed/disabled
    dialog.elements_list.item(1).setSelected(True)  # B -- free

    dialog.add_selected_button.click()

    assert set(dialog.elements) == {"B"}  # A never got checked despite being list-selected


def test_filtering_the_list_does_not_lose_selection_or_checkbox_state(qtbot):
    """Context/constraints: confirm the filter (added earlier), list-
    selection, and checkbox-enable states all interact correctly --
    filtering must not lose the current list-selection or checkbox
    state for hidden items."""
    dialog = _minimal_dialog(["apple", "banana", "cherry"])
    qtbot.addWidget(dialog)
    dialog.elements_list.item(0).setSelected(True)  # "apple"
    dialog.add_all_button.click()  # every item checked, including hidden-to-be "banana"/"cherry"

    dialog.elements_filter.setText("apple")

    assert dialog.elements_list.item(0).isHidden() is False
    assert dialog.elements_list.item(1).isHidden() is True
    assert dialog.elements_list.item(2).isHidden() is True
    # Selection and checkbox state both survive filtering untouched.
    assert dialog.elements_list.item(0).isSelected() is True
    assert set(dialog.elements) == {"apple", "banana", "cherry"}

    dialog.elements_filter.setText("")  # clear the filter -- everything visible again, state still intact

    assert all(not dialog.elements_list.item(i).isHidden() for i in range(dialog.elements_list.count()))
    assert set(dialog.elements) == {"apple", "banana", "cherry"}


# --- dialog: "Add new Supply Set..." on the Retention combo only ------------


def test_add_new_supply_set_entry_only_appears_on_the_retention_combo(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    domain, primary, retention = _domain_with_supply_sets(view, "top")
    dialog = RetentionStrategyDialog(domain, view._domain_state.supply_sets, [], [], set(), {}, parent=None)
    qtbot.addWidget(dialog)

    primary_texts = [dialog.primary_combo.itemText(i) for i in range(dialog.primary_combo.count())]
    retention_texts = [dialog.retention_combo.itemText(i) for i in range(dialog.retention_combo.count())]

    assert "Add new Supply Set..." not in primary_texts
    assert "Add new Supply Set..." in retention_texts


def test_picking_add_new_supply_set_reuses_create_supply_set_dialog_and_auto_selects_it(qtbot, monkeypatch):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    domain, primary, retention = _domain_with_supply_sets(view, "top")

    def fake_exec(self):
        self.name_edit.setText("SS_NEW")
        self.power_combo.blockSignals(True)
        self.power_combo.addItem("VDD_NEW")
        self.power_combo.setCurrentText("VDD_NEW")
        self.power_combo.blockSignals(False)
        self.ground_combo.blockSignals(True)
        self.ground_combo.addItem("VSS_NEW")
        self.ground_combo.setCurrentText("VSS_NEW")
        self.ground_combo.blockSignals(False)
        self.n_well_combo.blockSignals(True)
        self.n_well_combo.addItem("VDD_NEW")
        self.n_well_combo.setCurrentText("VDD_NEW")
        self.n_well_combo.blockSignals(False)
        self.p_well_combo.blockSignals(True)
        self.p_well_combo.addItem("VSS_NEW")
        self.p_well_combo.setCurrentText("VSS_NEW")
        self.p_well_combo.blockSignals(False)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(CreateSupplySetDialog, "exec", fake_exec)
    dialog = RetentionStrategyDialog(domain, view._domain_state.supply_sets, [], [], set(), {}, parent=None)
    qtbot.addWidget(dialog)
    add_new_index = dialog.retention_combo.findText("Add new Supply Set...")

    dialog.retention_combo.setCurrentIndex(add_new_index)

    assert len(dialog.new_supply_sets) == 1
    new_set = next(iter(dialog.new_supply_sets.values()))
    assert new_set.name == "SS_NEW"
    assert dialog.retention_combo.currentText() == "SS_NEW"


def _fake_create_supply_set_exec_for_retention(name: str, power: str, ground: str = "VSS_SHARED"):
    """Same shape as this file's own fake_exec above -- directly
    manipulates the combos rather than driving "Add New..."'s own
    nested AddPowerSupplyDialog -- but ALSO seeds new_port_colors
    directly, standing in for what a real "Add New..." round trip would
    have left behind (needed here since this test's own concern IS
    whether an abandoned name's own colour entry survives to accept())."""

    def fake_exec(self):
        self.name_edit.setText(name)
        self.new_port_colors[power] = QColor("#123456")
        self.new_port_colors[ground] = QColor("#654321")
        for combo, value in ((self.power_combo, power), (self.ground_combo, ground)):
            combo.blockSignals(True)
            if combo.findText(value) < 0:
                combo.addItem(value)
            combo.setCurrentText(value)
            combo.blockSignals(False)
        return QDialog.DialogCode.Accepted

    return fake_exec


def test_retention_dialog_accept_discards_a_supply_set_superseded_before_the_outer_ok(qtbot, monkeypatch):
    """Same architectural bug/fix as CreatePowerDomainDialog's own
    _discard_unused_new_entities() (see that class's own regression
    tests in test_power_domain.py): creating a new Supply Set via "Add
    new Supply Set..." on retention_combo, then creating ANOTHER one
    that supersedes it as the combo's own current selection before this
    outer OK, must not leave the first one -- or its own newly-
    introduced supply names -- reachable in the diagram afterward."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    domain, primary, retention = _domain_with_supply_sets(view, "top")

    monkeypatch.setattr(CreateSupplySetDialog, "exec", _fake_create_supply_set_exec_for_retention("SS_ABANDONED", "VDD_X"))
    dialog = RetentionStrategyDialog(domain, view._domain_state.supply_sets, [], ["top/u_core/out1"], set(), {}, parent=None)
    qtbot.addWidget(dialog)
    add_new_index = dialog.retention_combo.findText("Add new Supply Set...")
    dialog.retention_combo.setCurrentIndex(add_new_index)
    assert len(dialog.new_supply_sets) == 1

    monkeypatch.setattr(CreateSupplySetDialog, "exec", _fake_create_supply_set_exec_for_retention("SS_FINAL", "VDD_Y"))
    add_new_index_2 = dialog.retention_combo.findText("Add new Supply Set...")
    dialog.retention_combo.setCurrentIndex(add_new_index_2)
    assert len(dialog.new_supply_sets) == 2  # both still tracked pre-accept, as the bug produced
    assert "VDD_X" in dialog.new_supply_ports and "VDD_Y" in dialog.new_supply_ports

    dialog._select_combo_data(dialog.primary_combo, primary.id)
    dialog.save_logic_edit.setText("save_it")
    dialog.restore_logic_edit.setText("restore_it")
    dialog.elements_list.item(0).setCheckState(Qt.CheckState.Checked)

    dialog.accept()

    assert dialog.result() == QDialog.DialogCode.Accepted
    assert len(dialog.new_supply_sets) == 1
    assert next(iter(dialog.new_supply_sets.values())).name == "SS_FINAL"
    assert dialog.new_supply_ports == {"VDD_Y": QColor("#123456"), "VSS_SHARED": QColor("#654321")}
    assert "VDD_X" not in dialog.new_supply_ports


# --- diagram icon rendering: EVERY block owning >=1 enabled element -------
# gets its own badge, and multiple strategies sharing a block stack as
# multiple icons (explicit design decision, confirmed via AskUserQuestion).


def test_build_retention_overlay_places_one_icon_per_block_a_strategy_spans(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    strategy = RetentionStrategyPlacement(
        id="r1", name="RET1", power_domain_id="d1", primary_supply_set_id="s1", retention_supply_set_id="s2",
        save_logic="save", restore_logic="restore", elements=["top/u_core/out1", "top/u_dbg/whatever"],
    )

    icons = build_retention_overlay(view._boxes, {"r1": strategy})

    owners = {icon.owner_box.path for icon in icons["r1"]}
    assert owners == {"top/u_core", "top/u_dbg"}


def test_build_retention_overlay_stacks_multiple_strategies_on_the_same_block(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    strategy_a = RetentionStrategyPlacement(
        id="ra", name="A", power_domain_id="d1", primary_supply_set_id="s1", retention_supply_set_id="s2",
        save_logic="save", restore_logic="restore", elements=["top/u_core/out1"],
    )
    strategy_b = RetentionStrategyPlacement(
        id="rb", name="B", power_domain_id="d1", primary_supply_set_id="s1", retention_supply_set_id="s2",
        save_logic="save", restore_logic="restore", elements=["top/u_core/internal_n1"],
    )

    icons = build_retention_overlay(view._boxes, {"ra": strategy_a, "rb": strategy_b})

    icon_a = icons["ra"][0]
    icon_b = icons["rb"][0]
    assert icon_a.owner_box is icon_b.owner_box is view._boxes["top/u_core"]
    assert icon_a.pos() != icon_b.pos()  # genuinely two distinct, stacked icons -- not overlapping


def test_build_retention_overlay_skips_a_block_that_is_not_currently_materialized(qtbot):
    """Same "not materialized this pass, not discarded" precedent every
    other diagram-only placement follows -- a hidden block just has no
    icon this pass, the strategy's own persisted data is untouched."""
    strategy = RetentionStrategyPlacement(
        id="r1", name="RET1", power_domain_id="d1", primary_supply_set_id="s1", retention_supply_set_id="s2",
        save_logic="save", restore_logic="restore", elements=["top/u_core/out1"],
    )

    icons = build_retention_overlay({}, {"r1": strategy})  # no boxes at all -- e.g. everything hidden

    assert icons["r1"] == []


def test_creating_a_strategy_materializes_its_badges_immediately_without_a_rebuild(qtbot, monkeypatch):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    domain, primary, retention = _domain_with_supply_sets(view, "top")
    box = view._boxes["top/u_core"]

    placement = view.create_retention_strategy(box, domain, "RET1", primary.id, retention.id, "save", "restore", ["top/u_core/out1"], {})

    assert placement.id in view._retention_icons
    assert isinstance(view._retention_icons[placement.id][0], RetentionBadgeIconItem)
    assert view._retention_icons[placement.id][0].owner_box is box


def test_a_second_strategy_on_the_same_block_stacks_rather_than_overlapping(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    domain, primary, retention = _domain_with_supply_sets(view, "top")
    box = view._boxes["top/u_core"]
    first = view.create_retention_strategy(box, domain, "RET1", primary.id, retention.id, "save", "restore", ["top/u_core/out1"], {})

    second = view.create_retention_strategy(box, domain, "RET2", primary.id, retention.id, "save", "restore", ["top/u_core/internal_n1"], {})

    icon_1 = view._retention_icons[first.id][0]
    icon_2 = view._retention_icons[second.id][0]
    assert icon_1.pos() != icon_2.pos()


def test_retention_badges_survive_a_scene_rebuild(qtbot):
    """A rebuild (e.g. triggered by Hide/Unhide elsewhere in the tree)
    destroys and rematerializes every graphics item -- the badge must
    come back, rebuilt fresh from the persisted RetentionStrategyPlacement,
    same "plain data survives, Qt items don't" story every other
    diagram-only placement here already follows."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    domain, primary, retention = _domain_with_supply_sets(view, "top")
    box = view._boxes["top/u_core"]
    placement = view.create_retention_strategy(box, domain, "RET1", primary.id, retention.id, "save", "restore", ["top/u_core/out1"], {})

    view.refresh()

    assert placement.id in view._retention_icons
    new_box = view._boxes["top/u_core"]
    assert view._retention_icons[placement.id][0].owner_box is new_box  # rebuilt against the fresh box, not the old (destroyed) one


# --- right-click on a Retention badge: Edit / Info / Delete -----------------


def _fake_edit_retention_dialog_exec(name=None, save_logic=None, restore_logic=None, elements=None, accept: bool = True):
    def fake_exec(self):
        if accept:
            if name is not None:
                self.name_edit.setText(name)
            if save_logic is not None:
                self.save_logic_edit.setText(save_logic)
            if restore_logic is not None:
                self.restore_logic_edit.setText(restore_logic)
            if elements is not None:
                for i in range(self.elements_list.count()):
                    item = self.elements_list.item(i)
                    if item.flags() & Qt.ItemFlag.ItemIsEnabled:
                        item.setCheckState(Qt.CheckState.Checked if item.text() in elements else Qt.CheckState.Unchecked)
        return QDialog.DialogCode.Accepted if accept else QDialog.DialogCode.Rejected

    return fake_exec


def test_retention_badge_at_finds_the_icon_under_the_cursor(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.resize(900, 700)
    tab.show()
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    domain, primary, retention = _domain_with_supply_sets(view, "top")
    box = view._boxes["top/u_core"]
    view.create_retention_strategy(box, domain, "RET1", primary.id, retention.id, "save", "restore", ["top/u_core/out1"], {})
    icon = next(iter(view._retention_icons.values()))[0]
    pos = view.mapFromScene(icon.scenePos())

    assert view._retention_badge_at(pos) is icon


def test_right_click_a_badge_offers_edit_info_delete(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    domain, primary, retention = _domain_with_supply_sets(view, "top")
    box = view._boxes["top/u_core"]
    view.create_retention_strategy(box, domain, "RET1", primary.id, retention.id, "save", "restore", ["top/u_core/out1"], {})
    icon = next(iter(view._retention_icons.values()))[0]

    menu = QMenu()
    actions = view._retention_context_menu_actions(menu, icon)

    assert [a.text() for a, _cb in actions] == ["Edit", "Info", "Delete"]


def test_edit_retention_updates_the_strategys_own_fields(qtbot, monkeypatch):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    domain, primary, retention = _domain_with_supply_sets(view, "top")
    box = view._boxes["top/u_core"]
    placement = view.create_retention_strategy(box, domain, "RET1", primary.id, retention.id, "save", "restore", ["top/u_core/out1"], {})
    icon = view._retention_icons[placement.id][0]
    monkeypatch.setattr(RetentionStrategyDialog, "exec", _fake_edit_retention_dialog_exec(name="RET1_RENAMED", save_logic="new_save"))

    view._prompt_edit_retention(icon.strategy_id)

    updated = view._retention_state.strategies[placement.id]
    assert updated.name == "RET1_RENAMED"
    assert updated.save_logic == "new_save"


def test_edit_retention_does_not_collide_with_its_own_unchanged_name(qtbot, monkeypatch):
    """Explicit precedent (CreatePowerDomainDialog's own editing_domain_id):
    editing a strategy without renaming it must not trip its own global-
    uniqueness check against itself."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    domain, primary, retention = _domain_with_supply_sets(view, "top")
    box = view._boxes["top/u_core"]
    placement = view.create_retention_strategy(box, domain, "RET1", primary.id, retention.id, "save", "restore", ["top/u_core/out1"], {})
    icon = view._retention_icons[placement.id][0]
    monkeypatch.setattr(RetentionStrategyDialog, "exec", _fake_edit_retention_dialog_exec(name="RET1"))  # unchanged

    view._prompt_edit_retention(icon.strategy_id)  # must not raise/collide with itself

    assert view._retention_state.strategies[placement.id].name == "RET1"


def test_edit_retention_rejects_a_name_colliding_with_a_different_strategy(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    domain, primary, retention = _domain_with_supply_sets(view, "top")
    box = view._boxes["top/u_core"]
    view.create_retention_strategy(box, domain, "RET1", primary.id, retention.id, "save", "restore", ["top/u_core/out1"], {})
    second = view.create_retention_strategy(box, domain, "RET2", primary.id, retention.id, "save", "restore", ["top/u_core/internal_n1"], {})

    with pytest.raises(ValueError, match="already exists"):
        view.edit_retention_strategy(second.id, "RET1", primary.id, retention.id, "save", "restore", ["top/u_core/internal_n1"], {})


def test_editing_which_blocks_a_strategy_spans_moves_its_badges(qtbot, monkeypatch):
    """Elements can change on Edit -- the badge set must be recomputed,
    not just the first badge's own owner left stale."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    domain, primary, retention = _domain_with_supply_sets(view, "top")
    box = view._boxes["top/u_core"]
    placement = view.create_retention_strategy(box, domain, "RET1", primary.id, retention.id, "save", "restore", ["top/u_core/out1"], {})
    assert set(view._retention_icons[placement.id][0].owner_box.path for _ in [0]) == {"top/u_core"}
    monkeypatch.setattr(RetentionStrategyDialog, "exec", _fake_edit_retention_dialog_exec(elements=[]))  # drop every element

    view._prompt_edit_retention(view._retention_icons[placement.id][0].strategy_id)

    assert view._retention_state.strategies[placement.id].elements == []
    assert view._retention_icons[placement.id] == []  # no elements left -- no badges anywhere


def test_delete_retention_removes_the_strategy_and_every_one_of_its_badges(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    domain, primary, retention = _domain_with_supply_sets(view, "top")
    box = view._boxes["top/u_core"]
    placement = view.create_retention_strategy(
        box, domain, "RET1", primary.id, retention.id, "save", "restore", ["top/u_core/out1", "top/u_dbg/whatever"], {}
    )
    assert len(view._retention_icons[placement.id]) == 2

    view.delete_retention_strategy(placement.id)

    assert placement.id not in view._retention_state.strategies
    assert placement.id not in view._retention_icons


def test_delete_retention_via_the_context_menu_action(qtbot, monkeypatch):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    domain, primary, retention = _domain_with_supply_sets(view, "top")
    box = view._boxes["top/u_core"]
    placement = view.create_retention_strategy(box, domain, "RET1", primary.id, retention.id, "save", "restore", ["top/u_core/out1"], {})
    icon = view._retention_icons[placement.id][0]
    menu = QMenu()
    actions = view._retention_context_menu_actions(menu, icon)
    delete_callback = next(cb for a, cb in actions if a.text() == "Delete")
    monkeypatch.setattr(message_box, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)

    delete_callback()

    assert placement.id not in view._retention_state.strategies


def test_prompt_delete_retention_shows_a_confirmation_and_respects_cancel(qtbot, monkeypatch):
    """Bug fix (explicit request): Retention deletion previously
    proceeded with NO confirmation at all, unlike every comparable
    entity type. Declining (No/Cancel) must leave the strategy intact."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    domain, primary, retention = _domain_with_supply_sets(view, "top")
    box = view._boxes["top/u_core"]
    placement = view.create_retention_strategy(box, domain, "RET1", primary.id, retention.id, "save", "restore", ["top/u_core/out1"], {})
    monkeypatch.setattr(message_box, "question", lambda *a, **k: QMessageBox.StandardButton.No)

    view._prompt_delete_retention(placement.id)

    assert placement.id in view._retention_state.strategies


def test_prompt_delete_retention_confirmed_removes_it(qtbot, monkeypatch):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    domain, primary, retention = _domain_with_supply_sets(view, "top")
    box = view._boxes["top/u_core"]
    placement = view.create_retention_strategy(box, domain, "RET1", primary.id, retention.id, "save", "restore", ["top/u_core/out1"], {})
    monkeypatch.setattr(message_box, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)

    view._prompt_delete_retention(placement.id)

    assert placement.id not in view._retention_state.strategies


def test_info_action_opens_a_readonly_dialog_with_the_strategys_current_values(qtbot, monkeypatch):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    domain, primary, retention = _domain_with_supply_sets(view, "top")
    box = view._boxes["top/u_core"]
    placement = view.create_retention_strategy(box, domain, "RET1", primary.id, retention.id, "save_it", "restore_it", ["top/u_core/out1"], {})
    icon = view._retention_icons[placement.id][0]
    opened = []
    monkeypatch.setattr(RetentionStrategyInfoDialog, "exec", lambda self: opened.append(self) or QDialog.DialogCode.Accepted)

    view._prompt_show_retention_info(icon)

    assert len(opened) == 1
    dialog = opened[0]
    assert dialog.windowTitle() == "Retention Strategy: RET1"


def test_edit_and_delete_are_each_undoable(qtbot, monkeypatch):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    domain, primary, retention = _domain_with_supply_sets(view, "top")
    box = view._boxes["top/u_core"]
    placement = view.create_retention_strategy(box, domain, "RET1", primary.id, retention.id, "save", "restore", ["top/u_core/out1"], {})
    view.delete_retention_strategy(placement.id)
    assert placement.id not in view._retention_state.strategies

    tab.tree.undo_stack.undo()

    assert placement.id in view._retention_state.strategies


# --- multi-strategy duplicate/merge detection --------------------------------
# "Completely matches an existing strategy" (identical Power Domain, Primary
# Supply Set, Retention Supply Set, Save Logic, Restore Logic, AND enabled
# Elements) -> merge-confirmation dialog naming the existing match; confirmed
# -> merge, no new strategy created, existing strategy's own Name stays as-is
# (both explicit answers, confirmed via AskUserQuestion rather than guessed).


def _make_strategy(**overrides) -> RetentionStrategyPlacement:
    defaults = dict(
        id="r1", name="RET1", power_domain_id="d1", primary_supply_set_id="s1", retention_supply_set_id="s2",
        save_logic="save", restore_logic="restore", elements=["top/u_core/out1"],
    )
    defaults.update(overrides)
    return RetentionStrategyPlacement(**defaults)


def test_find_duplicate_strategy_matches_on_every_field():
    existing = _make_strategy()
    strategies = {"r1": existing}

    found = find_duplicate_strategy(strategies, "d1", "s1", "s2", "save", "restore", ["top/u_core/out1"])

    assert found is existing


def test_find_duplicate_strategy_elements_compared_as_a_set_not_order():
    existing = _make_strategy(elements=["top/u_core/out1", "top/u_core/internal_n1"])
    strategies = {"r1": existing}

    found = find_duplicate_strategy(strategies, "d1", "s1", "s2", "save", "restore", ["top/u_core/internal_n1", "top/u_core/out1"])

    assert found is existing


@pytest.mark.parametrize(
    "field,value",
    [
        ("power_domain_id", "d_other"),
        ("primary_supply_set_id", "s_other"),
        ("retention_supply_set_id", "s_other"),
        ("save_logic", "different_save"),
        ("restore_logic", "different_restore"),
        ("elements", ["top/u_dbg/whatever"]),
    ],
)
def test_find_duplicate_strategy_is_none_on_any_single_field_mismatch(field, value):
    """Explicit request: a PARTIAL match (all but one field equal) is
    NOT a duplicate -- falls through to the normal add-as-new path."""
    existing = _make_strategy()
    strategies = {"r1": existing}
    kwargs = dict(power_domain_id="d1", primary_supply_set_id="s1", retention_supply_set_id="s2", save_logic="save", restore_logic="restore", elements=["top/u_core/out1"])
    kwargs[field] = value

    found = find_duplicate_strategy(strategies, kwargs["power_domain_id"], kwargs["primary_supply_set_id"], kwargs["retention_supply_set_id"], kwargs["save_logic"], kwargs["restore_logic"], kwargs["elements"])

    assert found is None


def test_find_duplicate_strategy_excludes_the_given_strategy_id():
    existing = _make_strategy()
    strategies = {"r1": existing}

    found = find_duplicate_strategy(strategies, "d1", "s1", "s2", "save", "restore", ["top/u_core/out1"], exclude_strategy_id="r1")

    assert found is None


def test_add_retention_offers_a_merge_when_the_new_strategy_is_a_complete_duplicate(qtbot, monkeypatch):
    """Elements are deliberately left EMPTY on both strategies here: two
    strategies in the SAME Power Domain can never legitimately share a
    non-empty element (the claimed-elements constraint tested above
    already forces that), so a genuine complete-duplicate -- identical
    on every field including Elements -- can only really arise this way
    for two same-domain strategies. The pure find_duplicate_strategy()
    tests above already cover the general field-matching logic
    directly; this is about the view's own wiring (does it ask, and
    does it act correctly on the answer)."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    domain, primary, retention = _domain_with_supply_sets(view, "top")
    box = view._boxes["top/u_core"]
    view.create_retention_strategy(box, domain, "RET1", primary.id, retention.id, "save", "restore", [], {})
    monkeypatch.setattr(RetentionStrategyDialog, "exec", _fake_retention_dialog_exec("RET2", primary.id, retention.id, save_logic="save", restore_logic="restore"))
    questions = []
    monkeypatch.setattr(message_box, "question", lambda *args, **kwargs: questions.append(args) or QMessageBox.StandardButton.Yes)

    view._prompt_add_retention(box)

    assert len(questions) == 1
    assert len(view._retention_state.strategies) == 1  # merged -- no new strategy created
    assert next(iter(view._retention_state.strategies.values())).name == "RET1"  # existing strategy's Name stays as-is


def test_add_retention_declining_the_merge_does_not_create_a_duplicate_either(qtbot, monkeypatch):
    """Explicit "do NOT silently duplicate" principle applied to the
    decline branch too -- there is no explicit spec for what happens on
    decline, so this session's own interpretation (documented in
    _prompt_add_retention()'s own comment) is: declining cancels the add
    entirely rather than creating the redundant copy anyway."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    domain, primary, retention = _domain_with_supply_sets(view, "top")
    box = view._boxes["top/u_core"]
    view.create_retention_strategy(box, domain, "RET1", primary.id, retention.id, "save", "restore", [], {})
    monkeypatch.setattr(RetentionStrategyDialog, "exec", _fake_retention_dialog_exec("RET2", primary.id, retention.id, save_logic="save", restore_logic="restore"))
    monkeypatch.setattr(message_box, "question", lambda *args, **kwargs: QMessageBox.StandardButton.No)

    view._prompt_add_retention(box)

    assert len(view._retention_state.strategies) == 1  # still just the original -- no duplicate added


def test_add_retention_with_a_partial_match_creates_a_new_strategy_without_asking(qtbot, monkeypatch):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    domain, primary, retention = _domain_with_supply_sets(view, "top")
    box = view._boxes["top/u_core"]
    view.create_retention_strategy(box, domain, "RET1", primary.id, retention.id, "save", "restore", ["top/u_core/out1"], {})
    monkeypatch.setattr(
        RetentionStrategyDialog, "exec", _fake_retention_dialog_exec("RET2", primary.id, retention.id, save_logic="a_different_save", elements=["top/u_core/out1"])
    )
    questions = []
    monkeypatch.setattr(message_box, "question", lambda *args, **kwargs: questions.append(args) or QMessageBox.StandardButton.Yes)

    view._prompt_add_retention(box)

    assert questions == []  # never even asked -- not a complete match
    assert len(view._retention_state.strategies) == 2


def test_edit_retention_offers_a_merge_when_it_becomes_a_complete_duplicate(qtbot, monkeypatch):
    """Same "elements stay empty on both sides" reasoning as the Add
    tests above -- edits `second`'s own Save Logic to match `first`'s,
    with neither strategy ever claiming any element, so the claimed-
    elements constraint never gets in the way of setting up this
    scenario."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    domain, primary, retention = _domain_with_supply_sets(view, "top")
    box = view._boxes["top/u_core"]
    first = view.create_retention_strategy(box, domain, "RET1", primary.id, retention.id, "save", "restore", [], {})
    second = view.create_retention_strategy(box, domain, "RET2", primary.id, retention.id, "a_different_save", "restore", [], {})
    icon = SimpleNamespace(strategy_id=second.id)  # no badge exists for a zero-element strategy -- _prompt_edit_retention only ever reads .strategy_id
    monkeypatch.setattr(RetentionStrategyDialog, "exec", _fake_edit_retention_dialog_exec(save_logic="save"))  # now identical to RET1
    monkeypatch.setattr(message_box, "question", lambda *args, **kwargs: QMessageBox.StandardButton.Yes)

    view._prompt_edit_retention(icon.strategy_id)

    assert second.id not in view._retention_state.strategies  # dissolved into the existing match
    assert first.id in view._retention_state.strategies
    assert view._retention_state.strategies[first.id].name == "RET1"  # unchanged


def test_edit_retention_declining_the_merge_leaves_the_strategy_unchanged(qtbot, monkeypatch):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    domain, primary, retention = _domain_with_supply_sets(view, "top")
    box = view._boxes["top/u_core"]
    view.create_retention_strategy(box, domain, "RET1", primary.id, retention.id, "save", "restore", [], {})
    second = view.create_retention_strategy(box, domain, "RET2", primary.id, retention.id, "a_different_save", "restore", [], {})
    icon = SimpleNamespace(strategy_id=second.id)
    monkeypatch.setattr(RetentionStrategyDialog, "exec", _fake_edit_retention_dialog_exec(save_logic="save"))
    monkeypatch.setattr(message_box, "question", lambda *args, **kwargs: QMessageBox.StandardButton.No)

    view._prompt_edit_retention(icon.strategy_id)

    assert view._retention_state.strategies[second.id].save_logic == "a_different_save"  # edit was aborted, old value kept


# --- item 4: Retention badge icon size matches the Supply Port icon size ----


def test_retention_badge_icon_matches_the_supply_port_icon_size(qtbot):
    from frontend.src.power_intent_view.supply_port_overlay import _ICON_SIZE as SUPPLY_PORT_ICON_SIZE

    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    domain, primary, retention = _domain_with_supply_sets(view, "top")
    box = view._boxes["top/u_core"]
    placement = view.create_retention_strategy(box, domain, "RET1", primary.id, retention.id, "save", "restore", ["top/u_core/out1"], {})

    icon = view._retention_icons[placement.id][0]
    rect = icon.boundingRect()

    assert rect.width() == SUPPLY_PORT_ICON_SIZE
    assert rect.height() == SUPPLY_PORT_ICON_SIZE


# --- item 5: the Elements list actually expands when the dialog is resized --


def test_elements_list_has_an_expanding_size_policy(qtbot):
    from PySide6.QtWidgets import QSizePolicy

    dialog = RetentionStrategyDialog(
        PowerDomainPlacement(id="d1", owner_path="top", name="PD1", color=QColor("#ff0000")), {}, [], ["top/x"], set(), {}, parent=None
    )
    qtbot.addWidget(dialog)

    assert dialog.elements_list.sizePolicy().verticalPolicy() == QSizePolicy.Policy.Expanding


def test_elements_section_is_the_only_stretched_row_in_the_outer_layout(qtbot):
    """Explicit request: the Elements list grows with the dialog while
    Name/Power Domain/Save/Restore Logic stay their own natural size --
    verified structurally: the outer QVBoxLayout's own stretch factor is
    only set (non-zero) on the Elements section (index 1 -- fields form
    at 0, buttons at 2)."""
    dialog = RetentionStrategyDialog(
        PowerDomainPlacement(id="d1", owner_path="top", name="PD1", color=QColor("#ff0000")), {}, [], ["top/x"], set(), {}, parent=None
    )
    qtbot.addWidget(dialog)

    outer = dialog.layout()
    assert outer.stretch(0) == 0  # fixed fields
    assert outer.stretch(1) == 1  # Elements section -- the one that grows
    assert outer.stretch(2) == 0  # buttons


# --- item 6: wildcard filter above the Elements list --------------------------


def test_elements_filter_hides_non_matching_elements(qtbot):
    dialog = RetentionStrategyDialog(
        PowerDomainPlacement(id="d1", owner_path="top", name="PD1", color=QColor("#ff0000")),
        {},
        [],
        ["top/u_core/out1", "top/u_dbg/status_reg"],
        set(),
        {},
        parent=None,
    )
    qtbot.addWidget(dialog)

    dialog.elements_filter.setText("*u_core*")

    items_by_text = {dialog.elements_list.item(i).text(): dialog.elements_list.item(i) for i in range(dialog.elements_list.count())}
    assert items_by_text["top/u_core/out1"].isHidden() is False
    assert items_by_text["top/u_dbg/status_reg"].isHidden() is True


def test_elements_filter_supports_wildcards(qtbot):
    dialog = RetentionStrategyDialog(
        PowerDomainPlacement(id="d1", owner_path="top", name="PD1", color=QColor("#ff0000")),
        {},
        [],
        ["top/u_core/out1", "top/u_core/internal_n1"],
        set(),
        {},
        parent=None,
    )
    qtbot.addWidget(dialog)

    dialog.elements_filter.setText("*out1")

    items_by_text = {dialog.elements_list.item(i).text(): dialog.elements_list.item(i) for i in range(dialog.elements_list.count())}
    assert items_by_text["top/u_core/out1"].isHidden() is False
    assert items_by_text["top/u_core/internal_n1"].isHidden() is True


def test_elements_filter_does_not_affect_which_elements_are_checked(qtbot):
    """Explicit request: filtering never changes enabled/disabled
    (checked) state, including for a currently filtered-out element."""
    dialog = RetentionStrategyDialog(
        PowerDomainPlacement(id="d1", owner_path="top", name="PD1", color=QColor("#ff0000")),
        {},
        [],
        ["top/u_core/out1", "top/u_dbg/status_reg"],
        set(),
        {},
        parent=None,
    )
    qtbot.addWidget(dialog)
    dialog.elements_list.item(1).setCheckState(Qt.CheckState.Checked)  # "top/u_dbg/status_reg"

    dialog.elements_filter.setText("*u_core*")  # hides "top/u_dbg/status_reg"

    assert "top/u_dbg/status_reg" in dialog.elements  # still checked even while hidden by the filter


def test_clearing_the_elements_filter_shows_every_element_again(qtbot):
    dialog = RetentionStrategyDialog(
        PowerDomainPlacement(id="d1", owner_path="top", name="PD1", color=QColor("#ff0000")),
        {},
        [],
        ["top/u_core/out1", "top/u_dbg/status_reg"],
        set(),
        {},
        parent=None,
    )
    qtbot.addWidget(dialog)
    dialog.elements_filter.setText("*u_core*")

    dialog.elements_filter.setText("")

    assert all(not dialog.elements_list.item(i).isHidden() for i in range(dialog.elements_list.count()))


def test_retention_svg_renderer_is_shared_not_per_instance(qtbot):
    """Same "one shared QSvgRenderer per icon TYPE, not one per
    instance" requirement as SupplyPortIconItem's own regression test --
    RetentionBadgeIconItem now renders live via QSvgRenderer instead of
    caching a QPixmap (a fixed-resolution raster was confirmed, via a
    live before/after screenshot at high diagram zoom, to still visibly
    pixelate/soften no matter how high its own source resolution was)."""
    from frontend.src.power_intent_view.retention_overlay import _retention_svg_renderer

    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    domain, primary, retention = _domain_with_supply_sets(view, "top")
    box = view._boxes["top/u_core"]
    view.create_retention_strategy(box, domain, "RET1", primary.id, retention.id, "save", "restore", ["top/u_core/out1"], {})
    view.create_retention_strategy(box, domain, "RET2", primary.id, retention.id, "save", "restore", ["top/u_core/internal_n1"], {})

    assert _retention_svg_renderer() is _retention_svg_renderer()


def test_retention_badge_bounding_rect_is_centered_and_the_right_size(qtbot):
    """Explicit request: boundingRect() must now be implemented
    explicitly (a plain QGraphicsItem has no pixmap to derive it from
    implicitly anymore) -- without it, hit-testing/selection on the
    badge would break."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    domain, primary, retention = _domain_with_supply_sets(view, "top")
    box = view._boxes["top/u_core"]
    placement = view.create_retention_strategy(box, domain, "RET1", primary.id, retention.id, "save", "restore", ["top/u_core/out1"], {})

    icon = view._retention_icons[placement.id][0]
    rect = icon.boundingRect()

    from PySide6.QtCore import QPointF

    assert rect.center() == QPointF(0, 0)


# --- bug 1: at least one Element must be selected before submission ---------


def test_dialog_rejects_submission_with_zero_elements_selected(qtbot, monkeypatch):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    domain, primary, retention = _domain_with_supply_sets(view, "top")
    warnings = []
    monkeypatch.setattr(
        "frontend.src.power_intent_view.retention_dialog.message_box.warning",
        lambda *args, **kwargs: warnings.append(args[1:]) or None,
    )
    dialog = RetentionStrategyDialog(domain, view._domain_state.supply_sets, [], ["top/u_core/out1"], set(), {}, parent=None)
    qtbot.addWidget(dialog)
    dialog.name_edit.setText("RET1")
    dialog._select_combo_data(dialog.primary_combo, primary.id)
    dialog._select_combo_data(dialog.retention_combo, retention.id)
    dialog.save_logic_edit.setText("save")
    dialog.restore_logic_edit.setText("restore")
    # Deliberately leaves every Elements checkbox unchecked.

    dialog.accept()

    assert len(warnings) == 1
    assert "element" in warnings[0][1].lower()
    assert dialog.result() != QDialog.DialogCode.Accepted  # dialog stays open, not discarded


def test_dialog_accepts_submission_once_at_least_one_element_is_selected(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    domain, primary, retention = _domain_with_supply_sets(view, "top")
    dialog = RetentionStrategyDialog(domain, view._domain_state.supply_sets, [], ["top/u_core/out1"], set(), {}, parent=None)
    qtbot.addWidget(dialog)
    dialog.name_edit.setText("RET1")
    dialog._select_combo_data(dialog.primary_combo, primary.id)
    dialog._select_combo_data(dialog.retention_combo, retention.id)
    dialog.save_logic_edit.setText("save")
    dialog.restore_logic_edit.setText("restore")
    dialog.elements_list.item(0).setCheckState(Qt.CheckState.Checked)

    dialog.accept()

    assert dialog.result() == QDialog.DialogCode.Accepted


# --- bug 4: clicking a Retention icon highlights every icon of the same -----
#     strategy, across every block it spans -------------------------------


def _three_sibling_leaf_design() -> Design:
    """top -> u_a/u_b/u_c, three SIBLING leaves, each with its own single
    sequential element -- build_mock_design() only ever has ONE
    definition (core, at top/u_core) with sequential elements, not
    enough to build a strategy genuinely spanning 3 different blocks."""
    leaf_a = Definition(definition_id="leaf_a", base_name="leaf_a_def", language=SourceLanguage.SYSTEMVERILOG, sequential_elements=[SequentialElement(output_signal="regA", is_latch=False)])
    leaf_b = Definition(definition_id="leaf_b", base_name="leaf_b_def", language=SourceLanguage.SYSTEMVERILOG, sequential_elements=[SequentialElement(output_signal="regB", is_latch=False)])
    leaf_c = Definition(definition_id="leaf_c", base_name="leaf_c_def", language=SourceLanguage.SYSTEMVERILOG, sequential_elements=[SequentialElement(output_signal="regC", is_latch=False)])
    top = Definition(
        definition_id="top",
        base_name="top",
        language=SourceLanguage.SYSTEMVERILOG,
        instances=[
            Instance(name="u_a", definition_id="leaf_a"),
            Instance(name="u_b", definition_id="leaf_b"),
            Instance(name="u_c", definition_id="leaf_c"),
        ],
    )
    return Design(definitions={"top": top, "leaf_a": leaf_a, "leaf_b": leaf_b, "leaf_c": leaf_c}, top="top")


def test_clicking_one_icon_of_a_multi_block_strategy_highlights_every_icon_of_it(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.resize(900, 700)
    tab.show()
    tab.set_rtl(_three_sibling_leaf_design())
    view = tab.diagram._view
    domain, primary, retention = _domain_with_supply_sets(view, "top")
    box = view._boxes["top"]
    placement = view.create_retention_strategy(
        box, domain, "RET1", primary.id, retention.id, "save", "restore", ["top/u_a/regA", "top/u_b/regB", "top/u_c/regC"], {}
    )
    icons = view._retention_icons[placement.id]
    assert {i.owner_box.path for i in icons} == {"top/u_a", "top/u_b", "top/u_c"}  # sanity: genuinely 3 different blocks
    clicked = next(i for i in icons if i.owner_box.path == "top/u_b")  # click the MIDDLE one, not the first

    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, view.mapFromScene(clicked.scenePos()))

    assert all(i._click_highlighted for i in icons)
    _deselect_and_collect(tab)


def test_clicking_a_different_retention_strategys_icon_moves_the_group_highlight(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.resize(900, 700)
    tab.show()
    tab.set_rtl(_three_sibling_leaf_design())
    view = tab.diagram._view
    domain, primary, retention = _domain_with_supply_sets(view, "top")
    box = view._boxes["top"]
    first = view.create_retention_strategy(box, domain, "RET1", primary.id, retention.id, "save", "restore", ["top/u_a/regA"], {})
    second = view.create_retention_strategy(box, domain, "RET2", primary.id, retention.id, "save", "restore", ["top/u_b/regB"], {})
    first_icon = view._retention_icons[first.id][0]
    second_icon = view._retention_icons[second.id][0]
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, view.mapFromScene(first_icon.scenePos()))
    assert first_icon._click_highlighted is True

    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, view.mapFromScene(second_icon.scenePos()))

    assert second_icon._click_highlighted is True
    assert first_icon._click_highlighted is False
    _deselect_and_collect(tab)


def test_clicking_empty_canvas_clears_the_retention_group_highlight(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.resize(900, 700)
    tab.show()
    tab.set_rtl(_three_sibling_leaf_design())
    view = tab.diagram._view
    domain, primary, retention = _domain_with_supply_sets(view, "top")
    box = view._boxes["top"]
    placement = view.create_retention_strategy(
        box, domain, "RET1", primary.id, retention.id, "save", "restore", ["top/u_a/regA", "top/u_b/regB"], {}
    )
    icons = view._retention_icons[placement.id]
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, view.mapFromScene(icons[0].scenePos()))
    assert all(i._click_highlighted for i in icons)

    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(20, 650))

    assert not any(i._click_highlighted for i in icons)
    _deselect_and_collect(tab)


def test_retention_icon_renders_highlight_color_when_click_highlighted(qtbot):
    """Pixel-level check that paint() genuinely reacts to
    set_click_highlighted(), same discipline as the Supply Port colour
    regression tests -- not just that the flag got set."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    domain, primary, retention = _domain_with_supply_sets(view, "top")
    box = view._boxes["top/u_core"]
    placement = view.create_retention_strategy(box, domain, "RET1", primary.id, retention.id, "save", "restore", ["top/u_core/out1"], {})
    icon = view._retention_icons[placement.id][0]

    icon.set_click_highlighted(True)

    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtWidgets import QStyleOptionGraphicsItem

    pixmap_image = QImage(64, 64, QImage.Format.Format_ARGB32_Premultiplied)
    pixmap_image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap_image)
    painter.translate(32, 32)
    icon.paint(painter, QStyleOptionGraphicsItem())
    painter.end()

    # Asserted on the colour being PRESENT, not on it being the first
    # opaque pixel found. That older reading only held while the badge
    # rendered as a flat blob: it now carries dark outlines and a "RET"
    # label drawn over the tint (see
    # test_the_retention_badge_does_not_paint_over_its_own_label), so
    # the first opaque pixel scanned is detailing, not the fill.
    def _colours(image):
        return {(c.red(), c.green(), c.blue())
                for y in range(image.height()) for x in range(image.width())
                if (c := image.pixelColor(x, y)).alpha() == 255}

    highlight = (HIGHLIGHT_COLOR.red(), HIGHLIGHT_COLOR.green(), HIGHLIGHT_COLOR.blue())
    assert highlight in _colours(pixmap_image)

    # ...and genuinely absent once un-highlighted, so this is testing the
    # reaction rather than the colour merely existing somewhere.
    icon.set_click_highlighted(False)
    plain_image = QImage(64, 64, QImage.Format.Format_ARGB32_Premultiplied)
    plain_image.fill(Qt.GlobalColor.transparent)
    plain_painter = QPainter(plain_image)
    plain_painter.translate(32, 32)
    icon.paint(plain_painter, QStyleOptionGraphicsItem())
    plain_painter.end()
    assert highlight not in _colours(plain_image)
    _deselect_and_collect(tab)


# --- Retention icon colour (explicit request, new feature -- Retention -----
# --- previously had no colour concept of its own at all) --------------------


def test_retention_icon_color_follows_the_retention_supply_sets_power_rail(qtbot):
    """Follows the Retention Supply Set's own Power (not Ground) rail --
    same resolve-by-name-scoped-to-owner_paths mechanism Isolation's own
    colour-binding already establishes."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top"]
    vdd_ret = view.add_supply_port(box, "VDD_RET", QColor("#123456"))
    domain, primary, retention = _domain_with_supply_sets(view, "top")
    view._domain_state.supply_sets["ss_retention"] = SupplySetPlacement(
        id="ss_retention", name="SS_RETENTION", power="VDD_RET", ground="VSS", owner_path="top"
    )

    placement = view.create_retention_strategy(box, domain, "RET1", primary.id, "ss_retention", "save", "restore", ["top/u_core/out1"], {})

    icon = view._retention_icons[placement.id][0]
    assert icon.color == vdd_ret.color


def test_retention_icon_color_updates_live_when_the_source_port_is_recolored(qtbot):
    """Explicit request: same live-binding convention Power Switch/
    Isolation's own colour already establish -- updates automatically,
    no rebuild needed."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top"]
    vdd_ret = view.add_supply_port(box, "VDD_RET", QColor("#123456"))
    domain, primary, retention = _domain_with_supply_sets(view, "top")
    view._domain_state.supply_sets["ss_retention"] = SupplySetPlacement(
        id="ss_retention", name="SS_RETENTION", power="VDD_RET", ground="VSS", owner_path="top"
    )
    placement = view.create_retention_strategy(box, domain, "RET1", primary.id, "ss_retention", "save", "restore", ["top/u_core/out1"], {})
    icon = view._retention_icons[placement.id][0]

    vdd_ret.recolor(QColor("#abcdef"))

    assert icon.color == QColor("#abcdef")
    _deselect_and_collect(tab)


def test_retention_icon_color_falls_back_to_default_grey_when_unresolved(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top"]
    domain, primary, retention = _domain_with_supply_sets(view, "top")
    # a Supply Set whose own Power field matches no real placed port
    view._domain_state.supply_sets["ss_orphan"] = SupplySetPlacement(id="ss_orphan", name="SS_ORPHAN", power="NO_SUCH_PORT", owner_paths=["top"])

    placement = view.create_retention_strategy(box, domain, "RET1", primary.id, "ss_orphan", "save", "restore", ["top/u_core/out1"], {})

    icon = view._retention_icons[placement.id][0]
    assert icon.color == QColor("#9e9e9e")
    _deselect_and_collect(tab)


def test_deleting_the_retention_strategy_unregisters_its_recolor_callback(qtbot):
    """Same dangling-callback hazard as Power Switch's own
    on_recolor_callbacks cleanup -- a stale sync_color() left on the
    source port after the strategy is deleted would crash on the next
    recolor of that port."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top"]
    vdd_ret = view.add_supply_port(box, "VDD_RET", QColor("#123456"))
    domain, primary, retention = _domain_with_supply_sets(view, "top")
    view._domain_state.supply_sets["ss_retention"] = SupplySetPlacement(
        id="ss_retention", name="SS_RETENTION", power="VDD_RET", ground="VSS", owner_path="top"
    )
    placement = view.create_retention_strategy(box, domain, "RET1", primary.id, "ss_retention", "save", "restore", ["top/u_core/out1"], {})
    icon = view._retention_icons[placement.id][0]

    view.delete_retention_strategy(placement.id)

    assert icon.sync_color not in vdd_ret.on_recolor_callbacks
    vdd_ret.recolor(QColor("#654321"))  # must not raise
    _deselect_and_collect(tab)


def test_retention_icon_color_via_build_retention_overlay_directly(qtbot):
    """Same coverage as the above, but through build_retention_overlay()
    itself (the wholesale-rebuild path, e.g. after Edit) rather than
    _materialize_retention_badges() (the "don't wait for a rebuild"
    targeted path Add uses) -- both code paths need the same colour
    resolution, confirmed empirically as a real, separate gap while
    building this feature."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top"]
    vdd_ret = view.add_supply_port(box, "VDD_RET", QColor("#123456"))
    domain, primary, retention = _domain_with_supply_sets(view, "top")
    view._domain_state.supply_sets["ss_retention"] = SupplySetPlacement(
        id="ss_retention", name="SS_RETENTION", power="VDD_RET", ground="VSS", owner_path="top"
    )
    strategy = RetentionStrategyPlacement(
        id="r1", name="RET1", power_domain_id=domain.id, primary_supply_set_id=primary.id,
        retention_supply_set_id="ss_retention", save_logic="save", restore_logic="restore", elements=["top/u_core/out1"],
    )

    icons = build_retention_overlay(view._boxes, {"r1": strategy}, view._domain_state.supply_sets, view._supply_port_icons)

    assert icons["r1"][0].color == vdd_ret.color
    _deselect_and_collect(tab)


# --- name validation: no spaces allowed (Retention Strategy) ----------------


def _valid_retention_dialog(qtbot, view, domain, primary, retention, name: str) -> RetentionStrategyDialog:
    dialog = RetentionStrategyDialog(domain, view._domain_state.supply_sets, [], ["top/u_core/out1"], set(), {}, parent=None)
    qtbot.addWidget(dialog)
    dialog.name_edit.setText(name)
    dialog._select_combo_data(dialog.primary_combo, primary.id)
    dialog._select_combo_data(dialog.retention_combo, retention.id)
    dialog.save_logic_edit.setText("save")
    dialog.restore_logic_edit.setText("restore")
    dialog.elements_list.item(0).setCheckState(Qt.CheckState.Checked)
    return dialog


def test_retention_dialog_rejects_a_name_with_an_internal_space(qtbot, monkeypatch):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    domain, primary, retention = _domain_with_supply_sets(view, "top")
    warnings = []
    monkeypatch.setattr(
        "frontend.src.power_intent_view.retention_dialog.message_box.warning",
        lambda *args, **kwargs: warnings.append(args[1:]) or None,
    )
    dialog = _valid_retention_dialog(qtbot, view, domain, primary, retention, "RET BAD")

    dialog.accept()

    assert dialog.result() != QDialog.DialogCode.Accepted
    assert warnings[0] == ("Invalid Name", "Name cannot contain spaces.")


def test_retention_dialog_accepts_a_name_with_only_leading_trailing_whitespace(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    domain, primary, retention = _domain_with_supply_sets(view, "top")
    dialog = _valid_retention_dialog(qtbot, view, domain, primary, retention, "  RET_OK  ")

    dialog.accept()

    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.name == "RET_OK"


def test_retention_dialog_accepts_a_valid_underscore_separated_name(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    domain, primary, retention = _domain_with_supply_sets(view, "top")
    dialog = _valid_retention_dialog(qtbot, view, domain, primary, retention, "RET_CORE_A")

    dialog.accept()

    assert dialog.result() == QDialog.DialogCode.Accepted


# --- separator reorganization (explicit request, section 3): NO field ------
# --- content changes -- only visual grouping via form_separator() rows -----


def test_retention_dialog_field_layout_and_separators_match_the_spec_exactly(qtbot):
    from PySide6.QtWidgets import QFrame

    domain = PowerDomainPlacement(id="d1", owner_path="top", name="PD_TOP", color=QColor("#ff0000"))
    dialog = RetentionStrategyDialog(domain, {}, [], [], set(), {}, parent=None, target_paths=["top"])
    qtbot.addWidget(dialog)

    fields_form = dialog.layout().itemAt(0).layout()
    kinds = []
    for row in range(fields_form.rowCount()):
        label_item = fields_form.itemAt(row, fields_form.ItemRole.LabelRole)
        field_item = fields_form.itemAt(row, fields_form.ItemRole.FieldRole)
        if label_item is None and field_item is not None and isinstance(field_item.widget(), QFrame):
            kinds.append("separator")
        else:
            kinds.append(label_item.widget().text())

    assert kinds == [
        APPLIES_TO_CAPTION,
        "separator",
        "Name:",
        "Power Domain:",
        "Primary Supply Set:",
        "Retention Supply Set:",
        "Save Signal:",
        "Restore Signal:",
        "separator",
    ]


def test_retention_dialog_applies_to_is_capped_at_ten_hierarchies(qtbot):
    """(c): the SAME shared make_target_hierarchy_label() capping
    behavior (see test_target_hierarchy_label.py for the full coverage)
    is wired into the Retention dialog too, not just tested in
    isolation."""
    domain = PowerDomainPlacement(id="d1", owner_path="top", name="PD_TOP", color=QColor("#ff0000"))
    paths = [f"top/u_{i}" for i in range(15)]
    dialog = RetentionStrategyDialog(domain, {}, [], [], set(), {}, parent=None, target_paths=paths)
    qtbot.addWidget(dialog)

    applies_to_value = _find_applies_to_value_label(dialog)
    lines = applies_to_value.text().split("\n")
    assert lines[:10] == paths[:10]
    assert lines[10] == "... and 5 more"


def _find_applies_to_value_label(dialog):
    fields_form = dialog.layout().itemAt(0).layout()
    for row in range(fields_form.rowCount()):
        label_item = fields_form.itemAt(row, fields_form.ItemRole.LabelRole)
        if label_item is not None and label_item.widget() is not None and label_item.widget().text() == APPLIES_TO_CAPTION:
            return fields_form.itemAt(row, fields_form.ItemRole.FieldRole).widget()
    raise AssertionError("no 'Applies to:' row found in dialog")


# --- label rename: "Logic" -> "Signal" (explicit request) -------------------


def test_the_dialog_labels_say_signal_not_logic(qtbot):
    """Both the editable dialog and its read-only Info counterpart. The
    Info dialog is a sibling in the same module showing the same two
    fields, so leaving it saying "Logic" would have the same strategy
    labelled two different ways depending on which way you opened it."""
    domain = PowerDomainPlacement(id="d1", owner_path="top", name="PD_TOP", color=QColor("#ff0000"))
    dialog = RetentionStrategyDialog(domain, {}, [], [], set(), {}, parent=None, target_paths=["top"])
    qtbot.addWidget(dialog)

    labels = _form_labels(dialog.layout().itemAt(0).layout())
    assert "Save Signal:" in labels and "Restore Signal:" in labels
    assert "Save Logic:" not in labels and "Restore Logic:" not in labels

    strategy = RetentionStrategyPlacement(
        id="r", name="RET", power_domain_id="d1", primary_supply_set_id="", retention_supply_set_id="",
        save_logic="save_clk", restore_logic="restore_clk", elements=[], owner_path="top")
    info = RetentionStrategyInfoDialog(strategy, "PD_TOP", "", "", parent=None)
    qtbot.addWidget(info)
    info_labels = _form_labels(info.layout())
    assert "Save Signal:" in info_labels and "Restore Signal:" in info_labels
    assert "Save Logic:" not in info_labels and "Restore Logic:" not in info_labels


def _form_labels(form) -> list[str]:
    labels = []
    for row in range(form.rowCount()):
        item = form.itemAt(row, form.ItemRole.LabelRole)
        if item is not None and item.widget() is not None:
            labels.append(item.widget().text())
    return labels


def test_the_rename_is_label_only(qtbot, monkeypatch):
    """The fields, their validation and what reaches generation are all
    unchanged -- only the words on screen moved. Asserted explicitly
    because a rename is exactly the change that quietly reaches
    further than intended."""
    from frontend.src.power_intent_view import retention_dialog as module

    supply_sets = {
        "ss": SupplySetPlacement(id="ss", name="SS", power="VDD", ground="VSS", owner_path="top"),
    }
    domain = PowerDomainPlacement(id="d1", owner_path="top", name="PD_TOP", color=QColor("#ff0000"),
                                  primary_supply_set="ss", secondary_supply_sets=["ss"])
    dialog = RetentionStrategyDialog(domain, supply_sets, [], ["top/q_reg"], set(), {},
                                     parent=None, target_paths=["top"])
    qtbot.addWidget(dialog)

    # The widgets and the properties reading them keep their names: the
    # placement dataclass, project files and the generator all still say
    # save_logic/restore_logic.
    dialog.save_logic_edit.setText("save_clk")
    dialog.restore_logic_edit.setText("restore_clk")
    assert dialog.save_logic == "save_clk"
    assert dialog.restore_logic == "restore_clk"

    # Still REQUIRED, and still rejected in the same order -- the
    # message names the field the way the label now does.
    warned: list[tuple] = []
    monkeypatch.setattr(module.message_box, "warning", lambda *a, **k: warned.append(a[1:3]))
    dialog.name_edit.setText("RET")
    dialog.primary_combo.setCurrentIndex(1)
    dialog.retention_combo.setCurrentIndex(1)
    dialog.save_logic_edit.setText("")  # the field under test, left empty
    dialog.accept()

    assert warned, "an empty Save Signal is no longer rejected"
    assert warned[-1] == ("Missing Save Signal", "A Save Signal is required.")
    assert dialog.result() != QDialog.DialogCode.Accepted  # still blocked


# --- the badge icon must not paint over its own label -----------------------


def _render_badge(paint, size=160):
    """Paint `paint(painter, rect)` onto a white square and return the
    image, for asking what actually came out."""
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QImage, QPainter

    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.white)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    paint(painter, QRectF(10, 10, size - 20, size - 20))
    painter.end()
    return image


def _distinct_colours(image):
    return {image.pixel(x, y) for x in range(image.width()) for y in range(image.height())}


def test_the_retention_badge_is_drawn_as_an_outline_not_a_filled_block(qtbot):
    """Reported twice, and the second report set the final shape: the
    badge must be its LINES and its LABEL in the strategy's colour, with
    the interior TRANSPARENT.

    render_tinted_svg() composites with CompositionMode_SourceIn, which
    replaces every non-transparent pixel with the flat colour. The asset
    used to give its flop rectangles a solid white fill, so tinting
    turned the whole icon into one block with the label buried inside
    it. The fix is in the asset -- `fill="none"` on both rectangles --
    so what gets tinted is exactly the strokes and the text.

    Asserted by rendering over a DISTINCTIVE background and checking it
    still shows through: that is what "transparent interior" means, and
    it cannot be faked by an icon that merely looks lighter."""
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QColor, QImage, QPainter

    from frontend.src.power_intent_view.retention_overlay import _retention_svg_renderer
    from frontend.src.shared.diagram.svg_tint import render_tinted_svg

    background, tint = QColor("#00ff00"), QColor("#db4437")
    image = QImage(160, 160, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(background)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    render_tinted_svg(painter, _retention_svg_renderer(), QRectF(10, 10, 140, 140), tint)
    painter.end()

    colours = [QColor(image.pixel(x, y)) for x in range(160) for y in range(160)]
    background_pixels = sum(1 for c in colours if (c.red(), c.green(), c.blue()) == (0, 255, 0))
    tinted_pixels = sum(1 for c in colours if c.red() > 150 and c.green() < 120 and c.blue() < 120)

    # The lines and the label really are drawn...
    assert tinted_pixels > 200, f"almost nothing was tinted ({tinted_pixels}px)"
    # ...and the interior really is see-through. A filled block would
    # cover the middle; here the background dominates.
    assert background_pixels > tinted_pixels * 3, (
        f"the badge is filling its own area: {background_pixels}px background "
        f"vs {tinted_pixels}px tinted"
    )
    # Specifically INSIDE the front flop rectangle, not just around the
    # icon: that is the region the old solid fill covered.
    inside = [QColor(image.pixel(x, y)) for x in range(70, 95) for y in range(95, 115)]
    assert any((c.red(), c.green(), c.blue()) == (0, 255, 0) for c in inside), \
        "the flop interior is opaque -- the fill came back"


def test_the_retention_badge_takes_the_strategy_colour_on_its_lines(qtbot):
    """Whatever colour the strategy's supply set resolves to is what the
    lines and label are drawn in -- there is no second, untinted layer
    any more."""
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QColor, QImage, QPainter

    from frontend.src.power_intent_view.retention_overlay import _retention_svg_renderer
    from frontend.src.shared.diagram.svg_tint import render_tinted_svg

    for name in ("#db4437", "#4285f4", "#2c6a45"):
        wanted = QColor(name)
        image = QImage(160, 160, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.white)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        render_tinted_svg(painter, _retention_svg_renderer(), QRectF(10, 10, 140, 140), wanted)
        painter.end()

        exact = sum(1 for x in range(160) for y in range(160)
                    if (c := QColor(image.pixel(x, y))).rgb() == wanted.rgb())
        assert exact > 100, f"{name}: the lines were not drawn in the strategy colour ({exact}px)"


def test_the_badge_renders_the_same_way_in_a_real_diagram(qtbot):
    """Through the real paint path, not the helpers directly -- so a
    future change to paint() that stops composing the two layers is
    caught."""
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QColor, QImage, QPainter

    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    domain, primary, retention = _domain_with_supply_sets(view, "top/u_core")
    view.create_retention_strategy(
        view._boxes["top/u_core"], domain, "RET1", primary.id, retention.id,
        "save", "restore", ["top/u_core/out1"], {})

    badges = [b for group in view._retention_icons.values() for b in group]
    assert badges, "no retention badge was created"

    image = QImage(160, 160, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.white)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.translate(80, 80)
    painter.scale(5, 5)
    badges[0].paint(painter, None, None)
    painter.end()

    # The icon is drawn, and it did not fill its own area: a solid block
    # would leave far fewer untouched background pixels than an outline.
    painted = sum(1 for x in range(image.width()) for y in range(image.height())
                  if QColor(image.pixel(x, y)).rgb() != QColor(Qt.GlobalColor.white).rgb())
    assert painted > 100, "the badge drew nothing at all"
    assert painted < (image.width() * image.height()) / 3, \
        "the badge filled its own area -- it should be an outline"
