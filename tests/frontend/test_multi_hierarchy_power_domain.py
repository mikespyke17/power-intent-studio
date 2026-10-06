"""Multi-hierarchy Power Domain -- explicit request: right-clicking
"Create Power Domain" on a Ctrl+click multi-selection of 2+ hierarchy
blocks creates ONE Power Domain spanning all of them, not one domain
per block. Reuses the EXISTING Ctrl+click multi-select mechanism as-is
(confirmed via explicit follow-up, no new selection mechanism needed --
see hierarchy_diagram_view.py's own module docstring for that
mechanism, already empirically confirmed to drive the yellow-fluo
border highlight on every selected box simultaneously).

PowerDomainPlacement/SupplySetPlacement both gained a new `owner_paths:
list[str]` field alongside the pre-existing `owner_path: str` (kept as
a backward-compatible "primary member" convenience -- __post_init__
keeps the two in sync: `owner_path == owner_paths[0]` always). Every
pre-existing single-hierarchy caller/test that only ever passes
`owner_path=`/positional `box` keeps working completely unchanged --
confirmed by the full pre-existing suite passing with zero further
changes once this file's own new functionality was added.
"""

import gc

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog, QLabel, QMenu

from frontend.src.power_intent_view.add_power_supply_dialog import AddPowerSupplyDialog
from frontend.src.power_intent_view.create_power_domain_dialog import CreatePowerDomainDialog
from frontend.src.power_intent_view.power_domain_overlay import PowerDomainPlacement, SupplySetPlacement
from frontend.src.power_intent_view.power_intent_tab import PowerIntentTab
from frontend.src.power_intent_view.retention_dialog import RetentionStrategyDialog
from frontend.src.power_intent_view.retention_overlay import collect_retention_elements_for_domain
from frontend.src.power_intent_view.supply_port_overlay import connected_supply_port
from frontend.src.shared.mock_backend import build_mock_design
from frontend.src.shared.models.rtl import Definition, Design, Instance, SequentialElement, SourceLanguage


def _applies_to_paths(dialog) -> list[str]:
    """The "Applies to:" field is now a separate two-column QFormLayout
    row (Fix 1: one hierarchy per line, not comma-joined on one line)
    -- "Applies to:" is the row's own caption QLabel, and the actual
    path list lives in a SIBLING QLabel whose text is newline-joined
    (see target_hierarchy_label.py's own docstring)."""
    labels = dialog.findChildren(QLabel)
    for i, label in enumerate(labels):
        if label.text() == "Applies to:":
            return labels[i + 1].text().split("\n")
    raise AssertionError("no 'Applies to:' row found in dialog")


def _deselect_and_collect(tab: PowerIntentTab) -> None:
    if tab.diagram._view.scene() is not None:
        tab.diagram._view.scene().clearSelection()
    gc.collect()


def _multi_select(view, paths: list[str]) -> None:
    """Same end state a real Ctrl+click-to-toggle multi-select gesture
    produces (Qt's own native QGraphicsScene handling) -- see
    test_power_domain.py's own test_multi_selecting_two_boxes_..."""
    for path in paths:
        view._boxes[path].setSelected(True)


# --- create_power_domain()'s own new `additional_boxes` parameter ----------


def test_create_power_domain_with_additional_boxes_sets_owner_paths(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box_a = view._boxes["top/u_core"]
    box_b = view._boxes["top/u_dbg"]

    domain = view.create_power_domain(box_a, "PD_MULTI", QColor("#4caf50"), 0.5, None, [], {}, additional_boxes=[box_b])

    assert domain.owner_paths == ["top/u_core", "top/u_dbg"]
    assert domain.owner_path == "top/u_core"  # backward-compatible "primary" member
    _deselect_and_collect(tab)


def test_create_power_domain_without_additional_boxes_is_unchanged(qtbot):
    """Every pre-existing single-hierarchy call site (no `additional_boxes`
    given at all) keeps working exactly as before."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top/u_core"]

    domain = view.create_power_domain(box, "PD_SINGLE", QColor("#4caf50"), 0.5, None, [], {})

    assert domain.owner_paths == ["top/u_core"]
    assert domain.owner_path == "top/u_core"
    _deselect_and_collect(tab)


def test_multi_hierarchy_domain_recolours_every_member_box(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box_a = view._boxes["top/u_core"]
    box_b = view._boxes["top/u_dbg"]

    view.create_power_domain(box_a, "PD_MULTI", QColor("#4caf50"), 0.5, None, [], {}, additional_boxes=[box_b])

    assert box_a.brush().color().name() == "#4caf50"
    assert box_b.brush().color().name() == "#4caf50"
    _deselect_and_collect(tab)


def test_multi_hierarchy_domain_gets_a_label_on_every_member_box(qtbot):
    """Explicit request: the diagram's merged name+supply-set info box
    (DomainInfoBoxItem, superseding the old standalone DomainLabelItem)
    renders on EVERY member hierarchy block, not just a single
    "topmost" one."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box_a = view._boxes["top/u_core"]
    box_b = view._boxes["top/u_dbg"]

    domain = view.create_power_domain(box_a, "PD_MULTI", QColor("#4caf50"), 0.5, None, [], {}, additional_boxes=[box_b])

    info_boxes = view._domain_info_boxes[domain.id]
    assert len(info_boxes) == 2
    assert {item.parentItem() for item in info_boxes} == {box_a, box_b}
    assert all(item._rows[0][0] == "PD_MULTI" for item in info_boxes)
    _deselect_and_collect(tab)


# --- context menu: multi-select wiring --------------------------------------


def test_right_click_create_power_domain_on_a_multi_selection_creates_one_domain(qtbot, monkeypatch):
    def fake_exec(self):
        self.name_edit.setText("PD_MULTI")
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(CreatePowerDomainDialog, "exec", fake_exec)
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    _multi_select(view, ["top/u_core", "top/u_dbg"])

    menu = QMenu()
    actions = view._extra_context_menu_actions(menu, view._boxes["top/u_core"])
    create_cb = next(cb for a, cb in actions if a.text() == "Create Power Domain")
    create_cb()

    assert len(view._domain_state.domains) == 1
    domain = next(iter(view._domain_state.domains.values()))
    assert set(domain.owner_paths) == {"top/u_core", "top/u_dbg"}
    _deselect_and_collect(tab)


def test_multi_select_context_menu_disables_everything_except_create_power_domain(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    _multi_select(view, ["top/u_core", "top/u_dbg"])

    menu = QMenu()
    actions = view._extra_context_menu_actions(menu, view._boxes["top/u_core"])
    by_text = {a.text(): a for a, _cb in actions}

    assert by_text["Create Power Domain"].isEnabled() is True
    assert by_text["Edit Power Domain"].isEnabled() is False
    assert by_text["Remove Power Domain"].isEnabled() is False
    assert by_text["Add Power Supply"].isEnabled() is False
    assert by_text["Add Internal Supply"].isEnabled() is False
    assert by_text["Add Retention"].isEnabled() is False
    _deselect_and_collect(tab)


def test_real_right_click_on_a_multi_selection_preserves_it_before_create_power_domain(qtbot):
    """End-to-end regression test (explicit request): a genuine right-
    click (not the multi-select bookkeeping alone) must not clear the
    yellow highlight before "Create Power Domain" ever gets a chance to
    target it -- _PowerIntentGraphicsView shares HierarchyDiagramView's
    own mousePressEvent() fix (see that class's own docstring) via
    inheritance, so the same real-QGraphicsScene bug this fix addresses
    would otherwise silently break multi-hierarchy Power Domain
    creation triggered via a real right-click."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    _multi_select(view, ["top/u_core", "top/u_dbg"])
    box_a = view._boxes["top/u_core"]
    box_b = view._boxes["top/u_dbg"]
    assert box_a.pen().color().name() == "#ffff00"
    assert box_b.pen().color().name() == "#ffff00"

    pos = view.mapFromScene(box_a.sceneBoundingRect().center())
    QTest.mousePress(view.viewport(), Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier, pos)
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier, pos)

    assert view._selected_paths == {"top/u_core", "top/u_dbg"}
    assert box_a.pen().color().name() == "#ffff00"
    assert box_b.pen().color().name() == "#ffff00"

    menu = QMenu()
    actions = view._extra_context_menu_actions(menu, box_a)
    by_text = {a.text(): a for a, _cb in actions}
    assert by_text["Create Power Domain"].isEnabled() is True  # still targets both boxes, not silently dropped to 1
    _deselect_and_collect(tab)


def test_single_selection_context_menu_is_unaffected(qtbot):
    """No regression to the pre-existing single-box menu behaviour."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view

    menu = QMenu()
    actions = view._extra_context_menu_actions(menu, view._boxes["top"])
    by_text = {a.text(): a for a, _cb in actions}

    assert by_text["Create Power Domain"].isEnabled() is True
    _deselect_and_collect(tab)


# --- answer #2: macro in multi-select -- two different cases ---------------


def test_macro_included_only_via_cascade_does_not_block_create_power_domain(qtbot):
    """Selecting a parent block containing a macro descendant -> the
    macro is swept into `_highlighted_paths` via cascade, but never
    into `_selected_paths` itself (see hierarchy_diagram_view.py's own
    module docstring on that distinction) -- Create Power Domain must
    succeed normally."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    # "top" is the macro's own ancestor -- selecting it alone is a
    # single-selection (len(target_paths) == 1), the ordinary case.
    view._boxes["top"].setSelected(True)

    menu = QMenu()
    actions = view._extra_context_menu_actions(menu, view._boxes["top"])
    create_action = next(a for a, _cb in actions if a.text() == "Create Power Domain")

    assert create_action.isEnabled() is True
    _deselect_and_collect(tab)


def test_macro_selected_standalone_alongside_another_block_blocks_create_power_domain(qtbot):
    """A macro block explicitly shift/ctrl-clicked as one of the
    multi-selected targets (not merely cascade-included) must block
    Create Power Domain outright."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    _multi_select(view, ["top/u_ram", "top/u_core"])  # top/u_ram is the macro

    menu = QMenu()
    actions = view._extra_context_menu_actions(menu, view._boxes["top/u_ram"])
    create_action = next(a for a, _cb in actions if a.text() == "Create Power Domain")

    assert create_action.isEnabled() is False
    assert "macro" in create_action.toolTip().lower()
    _deselect_and_collect(tab)


# --- answer #3: a selected block already exactly owning a domain blocks ----


def test_selected_block_already_owning_a_domain_blocks_create_power_domain(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view.create_power_domain(view._boxes["top/u_core"], "PD_EXISTING", QColor("#ff0000"), 0.5, None, [], {})
    _multi_select(view, ["top/u_core", "top/u_dbg"])

    menu = QMenu()
    actions = view._extra_context_menu_actions(menu, view._boxes["top/u_core"])
    create_action = next(a for a, _cb in actions if a.text() == "Create Power Domain")

    assert create_action.isEnabled() is False
    assert "already belong" in create_action.toolTip().lower()
    _deselect_and_collect(tab)


def test_a_block_merely_nested_inside_another_domain_does_not_block(qtbot):
    """A selected block that's merely NESTED inside (not exactly
    owning) another domain's own subtree is the already-supported
    nested-domain-carve-out case, not a conflict."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view.create_power_domain(view._boxes["top"], "PD_TOP", QColor("#ff0000"), 0.5, None, [], {})
    _multi_select(view, ["top/u_core", "top/u_dbg"])  # both nested inside "top", neither OWNS a domain itself

    menu = QMenu()
    actions = view._extra_context_menu_actions(menu, view._boxes["top/u_core"])
    create_action = next(a for a, _cb in actions if a.text() == "Create Power Domain")

    assert create_action.isEnabled() is True
    _deselect_and_collect(tab)


# --- answer #5: editing from any single member hierarchy updates the whole -


def test_editing_from_any_single_member_hierarchy_updates_the_whole_domain(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box_a = view._boxes["top/u_core"]
    box_b = view._boxes["top/u_dbg"]
    box_c = view._boxes["top/u_fwd"]
    domain = view.create_power_domain(box_a, "PD_MULTI", QColor("#4caf50"), 0.5, None, [], {}, additional_boxes=[box_b, box_c])

    # Right-click just ONE member (not the original multi-selection) and
    # edit -- same directly-testable-core precedent every other dialog-
    # bypassing test here already follows.
    view.edit_power_domain(domain.id, "PD_RENAMED", QColor("#4caf50"), 0.5, None, [], {})

    updated = view._domain_state.domains[domain.id]
    assert updated.name == "PD_RENAMED"
    assert set(updated.owner_paths) == {"top/u_core", "top/u_dbg", "top/u_fwd"}
    info_boxes = view._domain_info_boxes[domain.id]
    assert len(info_boxes) == 3
    assert all(item._rows[0][0] == "PD_RENAMED" for item in info_boxes)
    _deselect_and_collect(tab)


def test_edit_power_domain_reached_via_a_single_member_hierarchys_own_right_click(qtbot, monkeypatch):
    """End-to-end: right-clicking just ONE member block (not the full
    original multi-selection) offers "Edit Power Domain" (enabled, via
    find_domain_for_owner()'s own now-membership-aware check), and
    editing through it updates the domain shared by all 3 members."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box_a = view._boxes["top/u_core"]
    box_b = view._boxes["top/u_dbg"]
    box_c = view._boxes["top/u_fwd"]
    domain = view.create_power_domain(box_a, "PD_MULTI", QColor("#4caf50"), 0.5, None, [], {}, additional_boxes=[box_b, box_c])

    def fake_exec(self):
        self.name_edit.setText("PD_RENAMED")
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(CreatePowerDomainDialog, "exec", fake_exec)

    menu = QMenu()
    # No multi-selection active -- right-clicking box_c alone (a single
    # member, not the original 3-way selection) still reaches the SAME
    # shared domain entity.
    actions = view._extra_context_menu_actions(menu, box_c)
    edit_action = next(a for a, _cb in actions if a.text() == "Edit Power Domain")
    assert edit_action.isEnabled() is True
    edit_cb = next(cb for a, cb in actions if a.text() == "Edit Power Domain")
    edit_cb()

    updated = view._domain_state.domains[domain.id]
    assert updated.name == "PD_RENAMED"
    assert set(updated.owner_paths) == {"top/u_core", "top/u_dbg", "top/u_fwd"}
    _deselect_and_collect(tab)


# --- cascade delete across every member hierarchy ---------------------------


def test_removing_a_multi_hierarchy_domain_recolours_every_member_box(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box_a = view._boxes["top/u_core"]
    box_b = view._boxes["top/u_dbg"]
    view.create_power_domain(box_a, "PD_MULTI", QColor("#4caf50"), 0.5, None, [], {}, additional_boxes=[box_b])

    view.remove_power_domain(box_a)  # removed via just ONE of its own member boxes

    assert box_a.brush().color().name() == "#ffffff"
    assert box_b.brush().color().name() == "#ffffff"
    assert view._domain_info_boxes == {}
    _deselect_and_collect(tab)


def test_removing_a_multi_hierarchy_domain_cascades_supply_sets_and_ports_from_every_member(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box_a = view._boxes["top/u_core"]
    box_b = view._boxes["top/u_dbg"]
    supply_set = SupplySetPlacement(id="s1", name="SS1", power="VDD_PRIMARY", owner_paths=["top/u_core", "top/u_dbg"])
    # Domain (spanning both members) created FIRST -- items 11/12's new
    # global Supply Port/Internal Supply naming-uniqueness rule allows a
    # same-named port on two DIFFERENT hierarchies only when a real
    # PowerDomainPlacement already spans both of them, so the two
    # same-named "VDD_PRIMARY" ports below must be added AFTER the
    # domain exists, not before.
    domain = view.create_power_domain(box_a, "PD_MULTI", QColor("#4caf50"), 0.5, "s1", [], {"s1": supply_set}, additional_boxes=[box_b])
    port_a = view.add_internal_supply(box_a, "VDD_PRIMARY", QColor("#ff0000"))
    port_b = view.add_internal_supply(box_b, "VDD_PRIMARY", QColor("#ff0000"))

    view.remove_power_domain(box_b)  # removed via the OTHER member box, not box_a

    assert domain.id not in view._domain_state.domains
    assert "s1" not in view._domain_state.supply_sets
    assert port_a.key not in view._shared_state.supply_port_placements
    assert port_b.key not in view._shared_state.supply_port_placements
    _deselect_and_collect(tab)


# --- Supply Set / Supply Port replication across every member hierarchy ----


def test_creating_a_multi_hierarchy_domain_replicates_the_supply_set_port_on_every_member(qtbot, monkeypatch):
    """Explicit request/answer #4: adding a Supply Set to a multi-
    hierarchy domain instantiates the resulting Supply Port/Internal
    Supply on EVERY member hierarchy, not just one."""

    def fake_exec(self):
        self.name_edit.setText("PD_MULTI")
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(CreatePowerDomainDialog, "exec", fake_exec)
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box_a = view._boxes["top/u_core"]
    box_b = view._boxes["top/u_dbg"]

    domain = view.create_power_domain(
        box_a, "PD_MULTI", QColor("#4caf50"), 0.5, None, [], {}, new_supply_ports={"VDD_SHARED": QColor("#123456")}, additional_boxes=[box_b]
    )

    ports_a = [p for p in view._shared_state.supply_port_placements.values() if p.owner_path == "top/u_core" and p.name == "VDD_SHARED"]
    ports_b = [p for p in view._shared_state.supply_port_placements.values() if p.owner_path == "top/u_dbg" and p.name == "VDD_SHARED"]
    assert len(ports_a) == 1
    assert len(ports_b) == 1
    # Both are Internal Supply -- neither "top/u_core" nor "top/u_dbg" is
    # the design's own true top-level block.
    assert ports_a[0].is_internal_supply is True
    assert ports_b[0].is_internal_supply is True
    _deselect_and_collect(tab)


def test_supply_sets_for_owners_is_the_union_across_member_hierarchies(qtbot):
    from frontend.src.power_intent_view.power_domain_overlay import supply_sets_for_owners

    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view._domain_state.supply_sets["s1"] = SupplySetPlacement(id="s1", name="SS_CORE", power="VDD", owner_path="top/u_core")
    view._domain_state.supply_sets["s2"] = SupplySetPlacement(id="s2", name="SS_DBG", power="VDD2", owner_path="top/u_dbg")
    view._domain_state.supply_sets["s3"] = SupplySetPlacement(id="s3", name="SS_ELSEWHERE", power="VDD3", owner_path="top/u_fwd")

    result = supply_sets_for_owners(view._domain_state, ["top/u_core", "top/u_dbg"])

    assert set(result) == {"s1", "s2"}
    _deselect_and_collect(tab)


# --- stage (f): dialogs show target hierarchy/hierarchies (display-only) ---


def test_create_power_domain_dialog_shows_a_single_target_hierarchy(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view

    dialog = CreatePowerDomainDialog({}, [], {}, "top/u_core", parent=view)

    assert _applies_to_paths(dialog) == ["top/u_core"]


def test_create_power_domain_dialog_shows_every_target_hierarchy_for_a_multi_selection(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view

    dialog = CreatePowerDomainDialog({}, [], {}, "top/u_core", parent=view, owner_paths=["top/u_core", "top/u_dbg"])

    assert _applies_to_paths(dialog) == ["top/u_core", "top/u_dbg"]


def test_add_retention_dialog_shows_the_triggering_hierarchy_block(qtbot, monkeypatch):
    captured: dict = {}
    original_init = RetentionStrategyDialog.__init__

    def spy_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        captured["dialog"] = self

    monkeypatch.setattr(RetentionStrategyDialog, "__init__", spy_init)
    monkeypatch.setattr(RetentionStrategyDialog, "exec", lambda self: QDialog.DialogCode.Rejected)

    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top/u_core"]
    view.create_power_domain(box, "PD_CORE", QColor("#4caf50"), 0.5, None, [], {})

    view._prompt_add_retention(box)

    assert _applies_to_paths(captured["dialog"]) == ["top/u_core"]
    _deselect_and_collect(tab)


def test_edit_retention_dialog_shows_the_badges_own_owner_block(qtbot, monkeypatch):
    """The block whose badge was right-clicked (icon.owner_box), not
    necessarily the strategy's own original owner_path -- both happen
    to coincide here since there's only one badge, but the code path
    exercised is icon.owner_box.path specifically (see
    _prompt_edit_retention()'s own docstring)."""
    captured: dict = {}
    original_init = RetentionStrategyDialog.__init__

    def spy_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        captured["dialog"] = self

    monkeypatch.setattr(RetentionStrategyDialog, "__init__", spy_init)
    monkeypatch.setattr(RetentionStrategyDialog, "exec", lambda self: QDialog.DialogCode.Rejected)

    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top/u_core"]
    domain = view.create_power_domain(box, "PD_CORE", QColor("#4caf50"), 0.5, None, [], {})
    strategy = view.create_retention_strategy(
        box, domain, "RET1", "", "", "save", "restore", ["top/u_core/out1"], {}
    )
    icon = view._retention_icons[strategy.id][0]

    view._prompt_edit_retention(icon.strategy_id)

    assert _applies_to_paths(captured["dialog"]) == ["top/u_core"]
    _deselect_and_collect(tab)


def test_add_power_supply_dialog_shows_the_target_top_level_block(qtbot, monkeypatch):
    monkeypatch.setattr(AddPowerSupplyDialog, "exec", lambda self: QDialog.DialogCode.Rejected)
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view

    dialog = AddPowerSupplyDialog(view, target_path="top")

    assert _applies_to_paths(dialog) == ["top"]


def test_add_internal_supply_dialog_shows_the_target_inner_block(qtbot, monkeypatch):
    captured: dict = {}
    original_init = AddPowerSupplyDialog.__init__

    def spy_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        captured["dialog"] = self

    monkeypatch.setattr(AddPowerSupplyDialog, "__init__", spy_init)
    monkeypatch.setattr(AddPowerSupplyDialog, "exec", lambda self: QDialog.DialogCode.Rejected)

    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top/u_core"]

    view._prompt_add_internal_supply(box)

    assert _applies_to_paths(captured["dialog"]) == ["top/u_core"]
    _deselect_and_collect(tab)


# --- data model: __post_init__ backward-compat invariant --------------------


def _two_sibling_design_with_sequential_elements() -> Design:
    """top -> {u_left, u_right}, EACH with its OWN sequential elements
    -- build_mock_design() only ever has sequential elements on a
    single box (top/u_core), which isn't enough to prove a genuine
    cross-hierarchy MERGE (as opposed to just "one hierarchy's own
    elements, unaffected by an empty second member"). Same hand-built-
    Design fixture pattern test_power_intent_diagram_modes.py's own
    _three_level_nested_design() already establishes."""
    left = Definition(
        definition_id="left",
        base_name="left_def",
        language=SourceLanguage.SYSTEMVERILOG,
        sequential_elements=[SequentialElement(output_signal="q_left", is_latch=False)],
    )
    right = Definition(
        definition_id="right",
        base_name="right_def",
        language=SourceLanguage.SYSTEMVERILOG,
        sequential_elements=[SequentialElement(output_signal="q_right", is_latch=False)],
    )
    top = Definition(
        definition_id="top",
        base_name="top_def",
        language=SourceLanguage.SYSTEMVERILOG,
        instances=[Instance(name="u_left", definition_id="left"), Instance(name="u_right", definition_id="right")],
    )
    return Design(definitions={"left": left, "right": right, "top": top}, top="top")


# --- answer #4 (stage d): Retention elements aggregated across every member


def test_collect_retention_elements_for_domain_merges_elements_from_every_member_hierarchy(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(_two_sibling_design_with_sequential_elements())
    view = tab.diagram._view
    domain = PowerDomainPlacement(id="d1", owner_path="top_def/u_left", name="PD_MULTI", color=QColor("#ff0000"), owner_paths=["top_def/u_left", "top_def/u_right"])

    elements = collect_retention_elements_for_domain(view._boxes, domain, {"d1": domain})

    assert set(elements) == {"top_def/u_left/q_left", "top_def/u_right/q_right"}
    _deselect_and_collect(tab)


def test_collect_retention_elements_for_domain_is_a_noop_merge_for_a_single_hierarchy_domain(qtbot):
    """For a single-hierarchy domain (`owner_paths` has exactly one
    entry, the common/pre-existing case), this is exactly equivalent to
    calling collect_retention_elements() directly -- no behaviour
    change for every pre-existing single-hierarchy caller."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    domain = PowerDomainPlacement(id="d1", owner_path="top/u_core", name="PD_CORE", color=QColor("#ff0000"))

    elements = collect_retention_elements_for_domain(view._boxes, domain, {"d1": domain})

    assert set(elements) == {"top/u_core/out1", "top/u_core/internal_n1"}
    _deselect_and_collect(tab)


def test_add_retention_on_a_multi_hierarchy_domain_offers_elements_from_every_member(qtbot, monkeypatch):
    """End-to-end: right-clicking "Add Retention" on ANY single member
    of a multi-hierarchy Power Domain offers sequential elements from
    EVERY member hierarchy in the dialog's own Elements list, not just
    the one that was right-clicked."""
    captured: dict = {}
    original_init = RetentionStrategyDialog.__init__

    def spy_init(self, domain, supply_sets, available_supply_names, available_elements, *args, **kwargs):
        captured["available_elements"] = list(available_elements)
        original_init(self, domain, supply_sets, available_supply_names, available_elements, *args, **kwargs)

    monkeypatch.setattr(RetentionStrategyDialog, "__init__", spy_init)
    monkeypatch.setattr(RetentionStrategyDialog, "exec", lambda self: QDialog.DialogCode.Rejected)

    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(_two_sibling_design_with_sequential_elements())
    view = tab.diagram._view
    box_left = view._boxes["top_def/u_left"]
    box_right = view._boxes["top_def/u_right"]
    view.create_power_domain(box_left, "PD_MULTI", QColor("#4caf50"), 0.5, None, [], {}, additional_boxes=[box_right])

    # Right-click just the RIGHT member -- the LEFT member's own element
    # must still be offered.
    view._prompt_add_retention(box_right)

    assert set(captured["available_elements"]) == {"top_def/u_left/q_left", "top_def/u_right/q_right"}
    _deselect_and_collect(tab)


def test_power_domain_placement_owner_path_and_owner_paths_stay_in_sync():
    single = PowerDomainPlacement(id="d1", owner_path="top/u_core", name="PD1", color=QColor("#ff0000"))
    assert single.owner_paths == ["top/u_core"]

    multi = PowerDomainPlacement(id="d2", owner_path="top/u_core", name="PD2", color=QColor("#ff0000"), owner_paths=["top/u_core", "top/u_dbg"])
    assert multi.owner_path == "top/u_core"
    assert multi.owner_paths == ["top/u_core", "top/u_dbg"]


def test_supply_set_placement_owner_path_and_owner_paths_stay_in_sync():
    single = SupplySetPlacement(id="s1", name="SS1", owner_path="top/u_core")
    assert single.owner_paths == ["top/u_core"]

    multi = SupplySetPlacement(id="s2", name="SS2", owner_paths=["top/u_core", "top/u_dbg"])
    assert multi.owner_path == "top/u_core"
    assert multi.owner_paths == ["top/u_core", "top/u_dbg"]


# --- CHANGE 1 (explicit request, supersedes items 11/12's own global/local -
# --- uniqueness rule entirely): Supply Port/Internal Supply names are no --
# --- longer unique at all, anywhere in the design -- items 11/12's own -----
# --- `_check_unique_name_globally()`/`_check_unique_name()` are both -------
# --- deleted. Kept as regression coverage that the removal itself sticks. --


def test_unrelated_hierarchies_freely_share_a_name_with_no_error_and_independent_power_switches(qtbot):
    """CHANGE 1 regression (a): two Supply Ports with the identical name
    in unrelated Power Domains, with NO connection between them -- no
    error at creation, and each independently gets its own Power
    Switch with no restriction at all (they are not electrically
    connected, so _power_switch_add_error() finds nothing to reject)."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box_a = view._boxes["top/u_core"]
    box_b = view._boxes["top/u_dbg"]
    domain_a = view.create_power_domain(box_a, "PD_A", QColor("#4caf50"), 0.5, None, [], {})
    domain_b = view.create_power_domain(box_b, "PD_B", QColor("#2196f3"), 0.5, None, [], {})
    assert domain_a.id != domain_b.id

    port_a = view.add_internal_supply(box_a, "VDD_SW", QColor("#00aa00"))
    port_b = view.add_internal_supply(box_b, "VDD_SW", QColor("#0000ff"))

    assert port_a.key != port_b.key
    assert len(port_a.connectors) == 0
    assert len(port_b.connectors) == 0
    assert view._power_switch_add_error(port_a) is None
    assert view._power_switch_add_error(port_b) is None

    switch_a = view.add_power_switch(port_a, "SW_A", "in", "out", "ctrl", None, "on", "off")
    switch_b = view.add_power_switch(port_b, "SW_B", "in", "out", "ctrl", None, "on", "off")
    assert switch_a is not None and switch_b is not None
    assert switch_a.id != switch_b.id
    _deselect_and_collect(tab)


def test_same_named_placements_explicitly_connected_via_auto_link_trigger_the_power_switch_restriction(qtbot):
    """CHANGE 1 regression (b): two same-named entities, connected
    afterward via the auto-link dropdown mechanism (still implemented
    exactly as before -- see _auto_link_cross_hierarchy_supply_sets()),
    correctly trigger the existing Power-Switch restriction: the
    restriction fires because the connected pair's NAMES match (a
    Power Switch would break that name continuity), regardless of how
    the connection itself came to exist (auto-linked here, manually
    drawn elsewhere -- see test_supply_name_cross_hierarchy_linking.py's
    own coverage of the underlying rule)."""
    from frontend.src.power_intent_view.power_domain_overlay import SupplySetPlacement

    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    top_box = view._boxes["top"]
    core_box = view._boxes["top/u_core"]
    ss_top = SupplySetPlacement(id="ss_top", name="SS_TOP", power="VDD_SHARED", owner_path="top")
    view.create_power_domain(top_box, "PD_TOP", QColor("#4caf50"), 0.5, "ss_top", [], {"ss_top": ss_top}, {"VDD_SHARED": QColor("#ff0000")})

    # Selecting the SAME existing name from the whole-design dropdown pool
    # (not "Add New...") auto-links and auto-connects -- see
    # test_supply_name_cross_hierarchy_linking.py's own part (b) coverage.
    ss_core = SupplySetPlacement(id="ss_core", name="SS_CORE", power="VDD_SHARED", owner_path="top/u_core")
    view.create_power_domain(core_box, "PD_CORE", QColor("#2196f3"), 0.5, "ss_core", [], {"ss_core": ss_core})

    port_top = next(p for p in view._shared_state.supply_port_placements.values() if p.name == "VDD_SHARED" and p.owner_path == "top")
    port_core = next(p for p in view._shared_state.supply_port_placements.values() if p.name == "VDD_SHARED" and p.owner_path == "top/u_core")
    icon_top = view._supply_port_icons[port_top.id]
    icon_core = view._supply_port_icons[port_core.id]
    assert len(icon_top.connectors) == 1  # the auto-created net
    assert len(icon_core.connectors) == 1

    error = view._power_switch_add_error(icon_core)
    assert error is not None
    assert "shares its name with its own upstream" in error.lower()
    import pytest

    with pytest.raises(ValueError, match="shares its name"):
        view.add_power_switch(icon_core, "SW1", "in", "out", "ctrl", None, "on", "off")
    _deselect_and_collect(tab)


def test_legitimate_multi_hierarchy_replicas_are_allowed(qtbot):
    """Regression 2/3: the SAME name on two DIFFERENT hierarchies is
    allowed once a real multi-hierarchy Power Domain actually spans
    both of them -- traced via the domain's own real id/owner_paths,
    not by the two ports merely sharing a name. Covers BOTH the
    already-established replication mechanism (_add_missing_supply_ports(),
    which bypasses this check by construction) AND a user manually
    adding a second same-named port to another member hierarchy
    afterward -- the case this rule's own exception is actually for."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box_a = view._boxes["top/u_core"]
    box_b = view._boxes["top/u_dbg"]
    view.create_power_domain(box_a, "PD_MULTI", QColor("#4caf50"), 0.5, None, [], {}, additional_boxes=[box_b])

    port_a = view.add_internal_supply(box_a, "VDD_SW", QColor("#00aa00"))
    port_b = view.add_internal_supply(box_b, "VDD_SW", QColor("#00aa00"))  # must not raise -- legitimate replica

    assert port_a.name == port_b.name == "VDD_SW"
    assert port_a.key != port_b.key
    _deselect_and_collect(tab)


def test_same_hierarchy_duplicate_names_are_also_freely_allowed_now(qtbot):
    """CHANGE 1 regression (supersedes the old "narrower same-hierarchy
    rule still fires" test): the SAME box, SAME name -- previously
    rejected by the narrower same-hierarchy rule (_check_unique_name(),
    now deleted) -- is now allowed too. Also re-confirms a macro's own
    PG-pin name reused on a DIFFERENT hierarchy stays allowed (already
    true pre-CHANGE-1, since a macro can never be a plain Supply Port's
    own target -- see _MACRO_SUPPLY_PORT_ERROR -- so this was never a
    same-hierarchy case to begin with; kept here as a sanity check that
    removing the two uniqueness methods didn't disturb it). SCOPE
    reminder: this removal is specific to Supply Port/Internal Supply
    naming; every OTHER uniqueness rule (Power Domain, Supply Set,
    Retention, Isolation, Level Shifter, Power Switch, Power Switch
    Cell, Level Shifter Cell names) is unaffected -- see those
    entities' own existing test coverage, unchanged by this task."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box_a = view._boxes["top/u_core"]
    box_b = view._boxes["top/u_dbg"]
    view.create_power_domain(box_a, "PD_MULTI", QColor("#4caf50"), 0.5, None, [], {}, additional_boxes=[box_b])
    first = view.add_internal_supply(box_a, "VDD_SW", QColor("#00aa00"))

    second = view.add_internal_supply(box_a, "VDD_SW", QColor("#123456"))  # same box, same name -- no longer rejected
    assert first.key != second.key

    # Now even a macro's own PG-pin name, on the SAME hierarchy as the
    # macro itself, is no longer a collision either.
    ram_box = view._boxes["top/u_ram"]
    pg_pin_name = next(p.name for p in view._shared_state.pg_pin_placements.values() if p.owner_path == "top/u_ram")
    port = view.add_supply_port(view._boxes["top"], pg_pin_name, QColor("#654321"))
    assert port.name == pg_pin_name
    _deselect_and_collect(tab)


# --- bug fix: editing a Supply Port/Internal Supply's Name/Color must ------
# --- propagate to every OTHER legitimate replica on a multi-hierarchy ------
# --- Power Domain, not just the one instance edited directly ---------------
# `_corresponding_supply_port_replicas()` traces replicas via a REAL shared
# PowerDomainPlacement (same items-11/12 discipline), never by name alone.


def _three_hierarchy_replicated_internal_supply(qtbot, name: str = "VDD_SW"):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    member_paths = ["top/u_core", "top/u_dbg", "top/u_fwd"]
    boxes = [view._boxes[p] for p in member_paths]
    view.create_power_domain(boxes[0], "PD_MULTI", QColor("#4caf50"), 0.5, None, [], {}, additional_boxes=boxes[1:])
    icons = {path: view.add_internal_supply(view._boxes[path], name, QColor("#00aa00")) for path in member_paths}
    return tab, view, icons, member_paths


def test_editing_color_on_one_replica_propagates_to_every_other_replica(qtbot):
    """The reported bug, reproduced and fixed: a Power Domain spanning
    3 hierarchies, each with its own replicated Internal Supply --
    changing the COLOR from just ONE member's own instance must update
    the underlying placement AND the live diagram icon on the other 2
    as well, not just the one edited directly."""
    tab, view, icons, member_paths = _three_hierarchy_replicated_internal_supply(qtbot)
    new_color = QColor("#123456")

    view.edit_supply_port(icons["top/u_core"], "VDD_SW", new_color)

    for path in member_paths:
        icon = icons[path]
        assert icon.placement.color.name() == new_color.name(), f"{path}'s own placement color did not propagate"
        assert icon.color.name() == new_color.name(), f"{path}'s own LIVE icon rendering color did not propagate"
    _deselect_and_collect(tab)


def test_editing_name_on_one_replica_propagates_to_every_other_replica(qtbot):
    """Same class of bug as Color above, confirmed to affect Name too
    (the investigation's own premise -- "Name already propagates" --
    turned out to be incorrect; neither field propagated before this
    fix)."""
    tab, view, icons, member_paths = _three_hierarchy_replicated_internal_supply(qtbot)

    view.edit_supply_port(icons["top/u_core"], "VDD_SW_RENAMED", QColor("#00aa00"))

    for path in member_paths:
        icon = icons[path]
        assert icon.placement.name == "VDD_SW_RENAMED", f"{path}'s own placement name did not propagate"
        assert icon.label.text() == "VDD_SW_RENAMED", f"{path}'s own live icon label did not propagate"
    _deselect_and_collect(tab)


def test_editing_from_a_non_primary_member_still_propagates_to_all(qtbot):
    """The propagation must not be one-directional/anchored to whichever
    hierarchy happens to be the domain's own "primary" (owner_paths[0])
    member -- editing from "top/u_fwd" (the LAST member, not the
    primary "top/u_core") must still reach every other replica,
    including the primary one."""
    tab, view, icons, member_paths = _three_hierarchy_replicated_internal_supply(qtbot)
    new_color = QColor("#abcdef")

    view.edit_supply_port(icons["top/u_fwd"], "VDD_SW", new_color)

    for path in member_paths:
        assert icons[path].placement.color.name() == new_color.name()
        assert icons[path].color.name() == new_color.name()
    _deselect_and_collect(tab)


def test_rename_propagates_to_a_multi_hierarchy_supply_set_regardless_of_which_member_edited(qtbot):
    """Bug fix, _propagate_supply_port_rename()'s own scoping: a
    multi-hierarchy Supply Set's shared `.power` field previously only
    got updated when the rename originated on the Supply Set's own
    "primary" owner_path -- editing from a NON-primary member hierarchy
    used to silently skip updating it, leaving `.power` referencing the
    now-stale name. Now checked against the Supply Set's own full
    `owner_paths`, so any member hierarchy's own edit finds it."""
    tab, view, icons, member_paths = _three_hierarchy_replicated_internal_supply(qtbot)
    supply_set = SupplySetPlacement(id="s1", name="SS1", power="VDD_SW", owner_paths=list(member_paths))
    view._domain_state.supply_sets["s1"] = supply_set

    # Edited from "top/u_dbg" -- NOT the Supply Set's own primary
    # owner_path ("top/u_core", owner_paths[0]).
    view.edit_supply_port(icons["top/u_dbg"], "VDD_SW_RENAMED", QColor("#00aa00"))

    assert view._domain_state.supply_sets["s1"].power == "VDD_SW_RENAMED"
    _deselect_and_collect(tab)


def test_editing_a_single_hierarchy_supply_port_does_not_touch_unrelated_ports(qtbot):
    """Confirms the fix is scoped correctly -- a Supply Port with no
    real multi-hierarchy domain membership relationship to another
    same-named port on an unrelated hierarchy must NOT have its edit
    bleed over (regression guard for _paths_share_a_multi_hierarchy_domain()
    actually gating this, not a blanket same-name propagation)."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view._domain_state.domains["d1"] = PowerDomainPlacement(id="d1", owner_path="top/u_core", name="PD_CORE", color=QColor("#ff0000"))
    view._domain_state.domains["d2"] = PowerDomainPlacement(id="d2", owner_path="top/u_dbg", name="PD_DBG", color=QColor("#0000ff"))
    icon_core = view.add_internal_supply(view._boxes["top/u_core"], "VDD_SW", QColor("#00aa00"))
    icon_dbg = view.add_internal_supply(view._boxes["top/u_dbg"], "VDD_SW_OTHER", QColor("#00aa00"))

    view.edit_supply_port(icon_core, "VDD_SW_RENAMED", QColor("#123456"))

    assert icon_dbg.placement.name == "VDD_SW_OTHER"  # untouched -- unrelated domain, unrelated port
    assert icon_dbg.placement.color.name() == "#00aa00"
    _deselect_and_collect(tab)


# --- bug fix: clicking a Supply Port/Internal Supply replica must --------
# --- group-highlight every OTHER legitimate replica too, same "click any -
# --- one instance, highlight all of them together" mechanism Retention/ --
# --- Power Switch already establish. Root cause: _on_scene_selection_    -
# --- changed() never asked _corresponding_supply_port_replicas() at all -
# --- for a Supply Port -- the underlying replica-tracing already existed
# --- (built for the Name/Color sync fix), the click handler just never
# --- used it. Drives the REAL click-handling code path
# --- (icon.setSelected(True) + the real selectionChanged signal), not a
# --- mock/stub -- same discipline the equivalent Power Switch test
# --- already uses.


def test_clicking_one_replicated_internal_supply_instance_highlights_all_three(qtbot):
    tab, view, icons, member_paths = _three_hierarchy_replicated_internal_supply(qtbot)

    icons["top/u_core"].setSelected(True)
    view.scene().selectionChanged.emit()

    for path in member_paths:
        assert icons[path]._click_highlighted is True
    assert set(view._click_highlighted_icons) == {icons[p] for p in member_paths}
    _deselect_and_collect(tab)


def test_deselecting_a_replicated_internal_supply_clears_the_whole_group_highlight(qtbot):
    tab, view, icons, member_paths = _three_hierarchy_replicated_internal_supply(qtbot)
    icons["top/u_core"].setSelected(True)
    view.scene().selectionChanged.emit()

    view.scene().clearSelection()
    view.scene().selectionChanged.emit()

    for path in member_paths:
        assert icons[path]._click_highlighted is False
    assert view._click_highlighted_icons == []
    gc.collect()


def test_clicking_a_single_hierarchy_supply_port_highlights_only_itself(qtbot):
    """Regression guard: an ordinary, non-replicated Supply Port must
    still behave exactly as before this fix -- a group of one."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view._domain_state.domains["d1"] = PowerDomainPlacement(id="d1", owner_path="top", name="PD_TOP", color=QColor("#ff0000"))
    port = view.add_supply_port(view._boxes["top"], "VDD_ALWAYS_ON", QColor("#00ff00"))

    port.setSelected(True)
    view.scene().selectionChanged.emit()

    assert port._click_highlighted is True
    assert view._click_highlighted_icons == [port]
    _deselect_and_collect(tab)


# --- generalized manual power net replication -------------------------------
# Explicit request: the earlier Power-Switch-only version of this fix
# (_replicate_power_switch_supply_in()/_remove_replicated_power_switch_connectors())
# was generalized into ONE mechanism (_replicate_manual_connector()/
# _remove_replicated_manual_connectors(), power_intent_diagram_view.py) keyed
# off _replica_icons() -- works for ANY replicated element type, not just
# Power Switch. These tests cover the Internal Supply element type directly
# (no Power Switch involved at all) -- test_power_switch_diagram.py's own
# test_manual_supply_in_replicates_via_a_shared_ancestor_upstream/
# test_deleting_a_replicated_supply_in_connector_removes_it_from_every_member
# (and their per-hierarchy-replica-upstream siblings) already cover the
# Power Switch element type -- together these confirm the mechanism is
# genuinely general, not accidentally type-specific to either one.


def test_manual_connector_on_one_internal_supply_replica_replicates_to_all_three(qtbot):
    """(a): a manual power net connector drawn directly on ONE Internal
    Supply replica -- no Power Switch anywhere in this scenario --
    connects to a single shared ancestor upstream and must replicate
    to the other 2 member hierarchies' own Internal Supply instances."""
    tab, view, icons, member_paths = _three_hierarchy_replicated_internal_supply(qtbot)
    upstream = view.add_supply_port(view._boxes["top"], "VDD_UNSWITCHED", QColor("#0000ff"))

    connected = view.try_connect_supply_ports(icons["top/u_core"], upstream)

    assert connected is True
    for path in member_paths:
        icon = icons[path]
        connector = next((c for c in icon.connectors if c.port_a is upstream or c.port_b is upstream), None)
        assert connector is not None, f"{path}'s own Internal Supply did not get the replicated connection"
    _deselect_and_collect(tab)


def test_manual_connector_on_internal_supply_replicates_via_a_per_hierarchy_replica_upstream(qtbot):
    """(a), the other resolved case: the upstream is ITSELF a legitimate
    multi-hierarchy replica -- each OTHER member's own Internal Supply
    connects to ITS OWN local upstream instance, not literally
    u_core's."""
    tab, view, icons, member_paths = _three_hierarchy_replicated_internal_supply(qtbot, name="VDD_SW")
    view = view  # noqa: keep name from helper explicit
    local_upstreams = {path: view.add_internal_supply(view._boxes[path], "VDD_LOCAL_UP", QColor("#ff8800")) for path in member_paths}

    connected = view.try_connect_supply_ports(icons["top/u_core"], local_upstreams["top/u_core"])

    assert connected is True
    for path in member_paths:
        icon = icons[path]
        own_upstream = local_upstreams[path]
        connector = next((c for c in icon.connectors if c.port_a is own_upstream or c.port_b is own_upstream), None)
        assert connector is not None, f"{path}'s own Internal Supply is not connected to ITS OWN local upstream instance"
    _deselect_and_collect(tab)


def test_deleting_a_replicated_internal_supply_connector_removes_it_from_all_three(qtbot):
    """(b): deleting the connector from ONE Internal Supply replica
    removes the auto-created equivalent connections on the other 2 as
    well."""
    tab, view, icons, member_paths = _three_hierarchy_replicated_internal_supply(qtbot)
    upstream = view.add_supply_port(view._boxes["top"], "VDD_UNSWITCHED", QColor("#0000ff"))
    view.try_connect_supply_ports(icons["top/u_core"], upstream)
    for path in member_paths:
        assert any(c.port_a is upstream or c.port_b is upstream for c in icons[path].connectors)
    connector = next(c for c in icons["top/u_core"].connectors if c.port_a is upstream or c.port_b is upstream)

    view.delete_connector(connector)

    for path in member_paths:
        assert not any(c.port_a is upstream or c.port_b is upstream for c in icons[path].connectors)
    _deselect_and_collect(tab)


def test_deleting_a_replicated_internal_supply_connector_is_a_single_undo_step(qtbot):
    """Checks via connected_supply_port() (persisted-state resolution),
    not the live `.connectors` list -- undo restores state through a
    rebuild, which materializes BRAND NEW icon objects; the `icons`
    dict captured before undo is stale afterward (same "icons don't
    survive a rebuild, keys do" precedent try_connect_supply_ports()
    itself is already built on), but each icon's own `.key` string is
    still meaningful for a fresh lookup against the CURRENT state."""
    tab, view, icons, member_paths = _three_hierarchy_replicated_internal_supply(qtbot)
    upstream = view.add_supply_port(view._boxes["top"], "VDD_UNSWITCHED", QColor("#0000ff"))
    view.try_connect_supply_ports(icons["top/u_core"], upstream)
    connector = next(c for c in icons["top/u_core"].connectors if c.port_a is upstream or c.port_b is upstream)
    keys_by_path = {path: icons[path].key for path in member_paths}
    view.delete_connector(connector)

    view._undo_stack.undo()

    for path in member_paths:
        connected = connected_supply_port(keys_by_path[path], view._shared_state)
        assert connected is not None and connected.name == "VDD_UNSWITCHED"
    _deselect_and_collect(tab)


def test_manual_connector_on_a_single_hierarchy_internal_supply_does_not_replicate(qtbot):
    """Negative case, confirms the general mechanism correctly no-ops
    for the ordinary (non-replicated) case -- a single-hierarchy
    Internal Supply connected to an upstream must not spuriously touch
    any unrelated icon."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view._domain_state.domains["d1"] = PowerDomainPlacement(id="d1", owner_path="top/u_core", name="PD_CORE", color=QColor("#ff0000"))
    upstream = view.add_supply_port(view._boxes["top"], "VDD_UNSWITCHED", QColor("#0000ff"))
    internal = view.add_internal_supply(view._boxes["top/u_core"], "VDD_SW", QColor("#00aa00"))
    unrelated = view.add_internal_supply(view._boxes["top/u_dbg"], "VDD_SW_OTHER", QColor("#00aa00"))  # no domain relationship to "internal" at all

    connected = view.try_connect_supply_ports(internal, upstream)

    assert connected is True
    assert not any(c.port_a is upstream or c.port_b is upstream for c in unrelated.connectors)
    _deselect_and_collect(tab)
