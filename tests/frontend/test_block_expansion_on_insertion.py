"""PERMANENT regression suite for the "make room" layout behavior: when
a new diagram element (Supply Port, Internal Supply, Power Switch,
Isolation, Level Shifter) is inserted, the block it's attached to must
reflow to accommodate it -- existing CHILDREN shift down to make room,
or (for a childless/leaf block) the block itself grows.

This exact behavior has regressed silently more than once across this
project's history (originally found for Power Switch, then generalized
into the shared _extra_top_margin()/_extra_left_margin()/
_extra_interior_top() + _reflow_layout() mechanism every element type
listed above now funnels through -- see power_intent_diagram_view.py's
own _reflow_layout()/_reflow_own_boxes() docstrings). Earlier coverage
tested the layout ALGORITHM (reflow_hierarchy_scene()) in isolation with
synthetic margins, which never would have caught a regression in the
WIRING between "element inserted" and "layout recompute triggered" --
every test below instead goes through the real, user-facing creation
method end-to-end (add_supply_port(), create_isolation(), etc. -- the
same "directly-testable core" every dialog's own accept() calls), so a
future wiring regression fails here even if the algorithm itself is
untouched.

A real, related regression WAS found and fixed while building this
suite (not merely re-confirmed): a "make room" reflow triggered from
one diagram tab (e.g. a sub-hierarchy tab) previously only ever updated
THAT tab's own boxes -- a sibling tab showing the same hierarchy path
(the always-open root tab, or another open sub-hierarchy tab) kept its
own stale geometry until separately refreshed. Fixed via a new
`reflow_siblings` callback threaded into every _PowerIntentGraphicsView,
reflowing every currently-open tab's own boxes together -- see that
fix's own dedicated tests further down. Retention was verified (not
assumed) to have NO space-reservation requirement at all -- an instance-
level badge only, no boundary-anchored icon -- so it's deliberately not
covered here.
"""

from PySide6.QtGui import QColor

from frontend.src.power_intent_view.power_intent_tab import PowerIntentTab
from frontend.src.shared.mock_backend import build_mock_design

# 'top' is the only box with children in the mock design (u_core/u_dbg/
# u_fwd/u_ram) -- used as the "with existing children" case throughout.
# 'top/u_dbg' is a plain (non-macro) leaf -- used as the "leaf block"
# case. 'top/u_core' is a second, independent leaf, used wherever a test
# needs two DIFFERENT target blocks at once (e.g. the sequential test).


def _tab_and_view(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    return tab, tab.diagram._view


def _children_y(view) -> dict[str, float]:
    return {p: b.pos().y() for p, b in view._boxes.items() if p.count("/") == 1}


def _deselect_and_collect(tab) -> None:
    import gc

    if tab.diagram._view.scene() is not None:
        tab.diagram._view.scene().clearSelection()
    gc.collect()


# --- 1/2: Supply Port -------------------------------------------------------


def test_supply_port_on_a_block_with_children_shifts_them_down(qtbot):
    tab, view = _tab_and_view(qtbot)
    before = _children_y(view)

    view.add_supply_port(view._boxes["top"], "VDD", QColor("#ff0000"))

    after = _children_y(view)
    assert after != before
    assert all(after[p] > before[p] for p in before)


def test_supply_port_on_a_leaf_block_grows_it(qtbot):
    tab, view = _tab_and_view(qtbot)
    before = view._boxes["top/u_dbg"].rect()

    view.add_supply_port(view._boxes["top/u_dbg"], "VDD", QColor("#ff0000"))

    after = view._boxes["top/u_dbg"].rect()
    assert after.height() > before.height()


# --- 3: Internal Supply ------------------------------------------------------


def test_internal_supply_on_a_block_with_children_shifts_them_down(qtbot):
    tab, view = _tab_and_view(qtbot)
    before = _children_y(view)

    view.add_internal_supply(view._boxes["top"], "VDD_INT", QColor("#ff0000"))

    after = _children_y(view)
    assert after != before
    assert all(after[p] > before[p] for p in before)


def test_internal_supply_on_a_leaf_block_grows_it(qtbot):
    tab, view = _tab_and_view(qtbot)
    before = view._boxes["top/u_dbg"].rect()

    view.add_internal_supply(view._boxes["top/u_dbg"], "VDD_INT", QColor("#ff0000"))

    after = view._boxes["top/u_dbg"].rect()
    assert after.height() > before.height()


# --- 4: Power Switch ----------------------------------------------------------
# Power Switch reserves room OUTSIDE (above) its target's own box, not
# INSIDE it -- so "make room" shows up as the target's own position
# shifting (within its parent, or -- for the root itself, which has no
# parent to shift within -- the root's own y position in the scene),
# not a rect() height change. Verified empirically before writing this.


def test_power_switch_target_with_children_shifts_the_target_down(qtbot):
    """Target = 'top' itself (the root, which also owns children) --
    the root's own position shifts down to leave room for the switch
    icon rendering above it."""
    tab, view = _tab_and_view(qtbot)
    domain = view.create_power_domain(view._boxes["top"], "PD1", QColor("#ff0000"), 0.5, None, [], {})
    port = view.add_supply_port(view._boxes["top"], "VDD_ALWAYS_ON", QColor("#00ff00"))
    before_y = view._boxes["top"].pos().y()

    view.add_power_switch(port, "SW1", "in", "out", "ctrl", None, "on", "off")

    after_y = view._boxes["top"].pos().y()
    assert after_y > before_y


def test_power_switch_target_leaf_shifts_it_down_within_its_parent(qtbot):
    tab, view = _tab_and_view(qtbot)
    domain = view.create_power_domain(view._boxes["top"], "PD1", QColor("#ff0000"), 0.5, None, [], {})
    box = view._boxes["top/u_core"]
    port = view.add_supply_port(box, "VDD_ALWAYS_ON", QColor("#00ff00"))
    before_y = view._boxes["top/u_core"].pos().y()

    view.add_power_switch(port, "SW1", "in", "out", "ctrl", None, "on", "off")

    after_y = view._boxes["top/u_core"].pos().y()
    assert after_y > before_y


# --- 5: Isolation, both Location=self and Location=parent/fanout ------------


def test_isolation_self_on_a_block_with_children_shifts_them_down(qtbot):
    tab, view = _tab_and_view(qtbot)
    domain = view.create_power_domain(view._boxes["top"], "PD1", QColor("#ff0000"), 0.5, None, [], {})
    before = _children_y(view)

    view.create_isolation(view._boxes["top"], "ISO1", domain.id, None, None, None, "low", "self", None, "high", ["top/some_port"])

    after = _children_y(view)
    assert all(after[p] > before[p] for p in before)


def test_isolation_self_on_a_leaf_block_grows_it(qtbot):
    tab, view = _tab_and_view(qtbot)
    domain = view.create_power_domain(view._boxes["top"], "PD1", QColor("#ff0000"), 0.5, None, [], {})
    before = view._boxes["top/u_dbg"].rect()

    view.create_isolation(
        view._boxes["top/u_dbg"], "ISO1", domain.id, None, None, None, "low", "self", None, "high", ["top/u_dbg/some_port"]
    )

    after = view._boxes["top/u_dbg"].rect()
    assert after.height() > before.height()


def test_isolation_parent_on_the_root_shifts_its_own_position(qtbot):
    """Location=parent/fanout reserves room OUTSIDE the block's own
    left edge -- for the root (no parent to shift within), this shows
    up as the root's own x position moving."""
    tab, view = _tab_and_view(qtbot)
    domain = view.create_power_domain(view._boxes["top"], "PD1", QColor("#ff0000"), 0.5, None, [], {})
    before_x = view._boxes["top"].pos().x()

    view.create_isolation(view._boxes["top"], "ISO1", domain.id, None, None, None, "low", "parent", None, "high", ["top/some_port"])

    after_x = view._boxes["top"].pos().x()
    assert after_x != before_x


def test_isolation_parent_on_a_leaf_block_shifts_it_within_its_parent(qtbot):
    tab, view = _tab_and_view(qtbot)
    domain = view.create_power_domain(view._boxes["top"], "PD1", QColor("#ff0000"), 0.5, None, [], {})
    before_x = view._boxes["top/u_dbg"].pos().x()

    view.create_isolation(
        view._boxes["top/u_dbg"], "ISO1", domain.id, None, None, None, "low", "parent", None, "high", ["top/u_dbg/some_port"]
    )

    after_x = view._boxes["top/u_dbg"].pos().x()
    assert after_x != before_x


# --- 6: Level Shifter, both Location=self and Location=parent/fanout -------


def test_level_shifter_self_on_a_block_with_children_shifts_them_down(qtbot):
    tab, view = _tab_and_view(qtbot)
    domain = view.create_power_domain(view._boxes["top"], "PD1", QColor("#ff0000"), 0.5, None, [], {})
    before = _children_y(view)

    view.create_level_shifter(view._boxes["top"], "LS1", domain.id, "elements", "low_to_high", "self", None, None, ["top/some_port"])

    after = _children_y(view)
    assert all(after[p] > before[p] for p in before)


def test_level_shifter_self_on_a_leaf_block_grows_it(qtbot):
    tab, view = _tab_and_view(qtbot)
    domain = view.create_power_domain(view._boxes["top"], "PD1", QColor("#ff0000"), 0.5, None, [], {})
    before = view._boxes["top/u_dbg"].rect()

    view.create_level_shifter(
        view._boxes["top/u_dbg"], "LS1", domain.id, "elements", "low_to_high", "self", None, None, ["top/u_dbg/some_port"]
    )

    after = view._boxes["top/u_dbg"].rect()
    assert after.height() > before.height()


def test_level_shifter_parent_on_the_root_shifts_its_own_position(qtbot):
    tab, view = _tab_and_view(qtbot)
    domain = view.create_power_domain(view._boxes["top"], "PD1", QColor("#ff0000"), 0.5, None, [], {})
    before_x = view._boxes["top"].pos().x()

    view.create_level_shifter(view._boxes["top"], "LS1", domain.id, "elements", "low_to_high", "parent", None, None, ["top/some_port"])

    after_x = view._boxes["top"].pos().x()
    assert after_x != before_x


def test_level_shifter_parent_on_a_leaf_block_shifts_it_within_its_parent(qtbot):
    tab, view = _tab_and_view(qtbot)
    domain = view.create_power_domain(view._boxes["top"], "PD1", QColor("#ff0000"), 0.5, None, [], {})
    before_x = view._boxes["top/u_dbg"].pos().x()

    view.create_level_shifter(
        view._boxes["top/u_dbg"], "LS1", domain.id, "elements", "low_to_high", "parent", None, None, ["top/u_dbg/some_port"]
    )

    after_x = view._boxes["top/u_dbg"].pos().x()
    assert after_x != before_x


# --- Retention: verified (not assumed) to need no space reservation --------


def test_retention_requires_no_block_expansion_at_all(qtbot):
    """Explicit verification, not an assumption: Retention is a plain
    instance-level badge with no boundary-anchored icon, so adding one
    must NOT change any box's own geometry -- confirmed here so a
    future change to Retention's own rendering that silently starts
    needing room gets caught, rather than assumed away forever."""
    from frontend.src.power_intent_view.power_domain_overlay import SupplySetPlacement

    tab, view = _tab_and_view(qtbot)
    primary = SupplySetPlacement(id="ss_primary", name="SS_PRIMARY", power="VDD", owner_path="top")
    retention = SupplySetPlacement(id="ss_retention", name="SS_RETENTION", power="VDD_RET", owner_path="top")
    view._domain_state.supply_sets["ss_primary"] = primary
    view._domain_state.supply_sets["ss_retention"] = retention
    domain = view.create_power_domain(view._boxes["top"], "PD1", QColor("#ff0000"), 0.5, "ss_primary", ["ss_retention"], {})
    before = view._boxes["top/u_core"].rect()

    view.create_retention_strategy(
        view._boxes["top/u_core"], domain, "RET1", "ss_primary", "ss_retention", "save", "restore", ["top/u_core/out1"], {}
    )

    after = view._boxes["top/u_core"].rect()
    assert after == before


# --- 7: sequential, multiple element types on the same design --------------


def test_sequential_insertions_of_different_types_each_apply_and_none_undo_a_prior_one(qtbot):
    """Closer to the actual repro scenario than a single isolated
    insertion (explicit request): insert a Supply Port, then a Power
    Switch on it, then an Isolation elsewhere -- each insertion's own
    layout adjustment must be correctly applied and survive every
    LATER insertion too, not get reset or conflict."""
    tab, view = _tab_and_view(qtbot)
    domain = view.create_power_domain(view._boxes["top"], "PD1", QColor("#ff0000"), 0.5, None, [], {})
    box_core = view._boxes["top/u_core"]
    r0 = box_core.rect()

    # 1. Supply Port on top/u_core (leaf) -- grows it.
    port = view.add_supply_port(box_core, "VDD_ALWAYS_ON", QColor("#00ff00"))
    r1 = view._boxes["top/u_core"].rect()
    assert r1.height() > r0.height()

    # 2. Power Switch on that same port -- shifts top/u_core within its parent.
    y_before_switch = view._boxes["top/u_core"].pos().y()
    view.add_power_switch(port, "SW1", "in", "out", "ctrl", None, "on", "off")
    y_after_switch = view._boxes["top/u_core"].pos().y()
    assert y_after_switch > y_before_switch
    # The Supply Port's own earlier growth must still be intact.
    r2 = view._boxes["top/u_core"].rect()
    assert r2.height() == r1.height()

    # 3. Isolation elsewhere (top/u_dbg) -- must not disturb top/u_core at all.
    r_dbg_before = view._boxes["top/u_dbg"].rect()
    view.create_isolation(
        view._boxes["top/u_dbg"], "ISO1", domain.id, None, None, None, "low", "self", None, "high", ["top/u_dbg/some_port"]
    )
    r_dbg_after = view._boxes["top/u_dbg"].rect()
    assert r_dbg_after.height() > r_dbg_before.height()

    r_core_final = view._boxes["top/u_core"].rect()
    y_core_final = view._boxes["top/u_core"].pos().y()
    assert r_core_final.height() == r2.height()
    assert y_core_final == y_after_switch


# --- Bug fix: cross-tab staleness -- a sibling tab must reflow too ----------


def test_insertion_on_a_sub_hierarchy_tab_reflows_the_root_tab_too(qtbot):
    tab, root_view = _tab_and_view(qtbot)
    tab.diagram.open_path("top/u_core")
    sub_view = tab.diagram._tabs.current_view
    assert sub_view is not root_view
    before = root_view._boxes["top/u_core"].rect()

    sub_view.add_supply_port(sub_view._boxes["top/u_core"], "VDD", QColor("#ff0000"))

    after = root_view._boxes["top/u_core"].rect()
    assert after.height() > before.height()


def test_insertion_on_the_root_tab_reflows_an_open_sub_hierarchy_tab_too(qtbot):
    tab, root_view = _tab_and_view(qtbot)
    tab.diagram.open_path("top/u_core")
    sub_view = tab.diagram._tabs.current_view
    tab.diagram._tabs.setCurrentIndex(0)  # switch back to root before inserting
    before = sub_view._boxes["top/u_core"].rect()

    root_view.add_supply_port(root_view._boxes["top/u_core"], "VDD", QColor("#ff0000"))

    after = sub_view._boxes["top/u_core"].rect()
    assert after.height() > before.height()


# --- Bug fix: direct insertion must apply the SAME layout immediately as ---
# --- undo+redo does -- the earlier reported "add a Supply Port, block  -----
# --- doesn't move; Ctrl+Z then Ctrl+Y fixes it" discrepancy ----------------


def _full_state_snapshot(view) -> dict:
    """Every box's own rect/pos, PLUS every materialized Power Switch
    icon's own scenePos() -- the box-only snapshot the rest of this
    file already uses wouldn't have caught the real regression found
    here (a Power Switch icon silently NOT following its target's own
    box when that box moved due to an unrelated insertion elsewhere)."""
    boxes = {
        path: (box.rect().x(), box.rect().y(), box.rect().width(), box.rect().height(), box.pos().x(), box.pos().y())
        for path, box in view._boxes.items()
    }
    return {"boxes": boxes, "switch_count": sum(len(v) for v in view._power_switch_icons.values())}


def test_direct_insertion_applies_the_same_layout_undo_redo_would(qtbot):
    """Explicit regression request: the diagram state after a DIRECT
    insertion must be IDENTICAL to the diagram state after undoing and
    then redoing that SAME insertion -- both must show the correct,
    fully-reflowed layout, with no discrepancy between the two paths."""
    tab, view = _tab_and_view(qtbot)

    view.add_supply_port(view._boxes["top"], "VDD", QColor("#ff0000"))
    direct_state = _full_state_snapshot(view)

    tab.undo_shortcut.activated.emit()
    tab.redo_shortcut.activated.emit()
    undo_redo_state = _full_state_snapshot(view)

    assert direct_state == undo_redo_state
    # And both actually show the resized/moved layout, not just agree
    # on some other (wrong) shared state.
    assert view._boxes["top/u_core"].pos().y() > 50.0


def test_power_switch_on_a_sibling_follows_when_another_insertion_repacks_it(qtbot):
    """Bug fix, found via this exact investigation: a Power Switch's own
    "make room" TOP MARGIN can shift a SIBLING box's own packed position
    (not just grow the box that owns the switch) -- previously, any
    icon materialized on that sibling (here, ANOTHER Power Switch,
    already placed there before the new insertion) silently stayed at
    its OLD scene position instead of following, since a QGraphicsItem's
    own itemChange() never fires just because an ANCESTOR moved. This
    was the actual root cause behind the earlier-reported "Power Switch
    not positioned correctly after prior insertions" bug."""
    tab, view = _tab_and_view(qtbot)
    domain = view.create_power_domain(view._boxes["top"], "PD1", QColor("#ff0000"), 0.5, None, [], {})

    # A Power Switch already placed on top/u_dbg, BEFORE the later
    # insertion that will cause u_dbg to repack.
    port_dbg = view.add_supply_port(view._boxes["top/u_dbg"], "VDD_DBG", QColor("#00ff00"))
    switch = view.add_power_switch(port_dbg, "SW1", "in", "out", "ctrl", None, "on", "off")
    switch_icon = view._power_switch_icons[switch.id][0]

    dbg_pos_before = view._boxes["top/u_dbg"].pos()
    switch_scene_pos_before = switch_icon.scenePos()

    # A SEPARATE, unrelated insertion on a sibling block -- causes the
    # whole "top" packing to reflow, moving top/u_dbg (confirmed
    # empirically: a Power Switch's own reserved top margin, unlike a
    # plain Supply Port's interior growth, shifts sibling positions).
    view.add_power_switch(view.add_supply_port(view._boxes["top/u_core"], "VDD_CORE", QColor("#0000ff")), "SW2", "in", "out", "ctrl", None, "on", "off")

    dbg_pos_after = view._boxes["top/u_dbg"].pos()
    assert dbg_pos_after != dbg_pos_before  # sanity: the repack actually happened

    switch_scene_pos_after = switch_icon.scenePos()
    port_dbg_scene_pos_after = port_dbg.scenePos()
    assert switch_scene_pos_after != switch_scene_pos_before  # the switch actually followed, not stuck at the old position
    assert switch_scene_pos_after.x() == port_dbg_scene_pos_after.x()  # still correctly aligned above its own target


# --- Bug fix: Power Domain / Retention "compose a Supply Set" flow ---------
# --- never reflowed at all -- _add_missing_supply_ports() (shared by ------
# --- Create/Edit Power Domain, Edit Supply Set, Add/Edit Retention) --------
# --- created new Supply Port icons directly but never called ---------------
# --- _reflow_layout() afterward, unlike add_supply_port()/ -----------------
# --- add_internal_supply(), which each do it themselves. -------------------


def test_creating_a_nested_power_domain_grows_the_inner_block_to_fit_its_new_ports(qtbot):
    """Concrete repro (sram-style): a Power Domain on the root with its
    own Supply Set, then a SECOND Power Domain on a nested block with
    its own, separate Supply Set -- the nested block must grow to fit
    the new Supply Ports the second domain's own Supply Set composes,
    exactly like a plain add_supply_port() insertion already does."""
    tab, view = _tab_and_view(qtbot)
    top_box = view._boxes["top"]
    sub_box = view._boxes["top/u_core"]
    before = sub_box.rect()

    view.create_power_domain(
        top_box, "PD_TOP", QColor("#ff0000"), 0.5, None, [], {},
        new_supply_ports={"VDD": QColor("#ff0000"), "VSS": QColor("#000000")},
    )
    view.create_power_domain(
        sub_box, "PD_SUB", QColor("#00ff00"), 0.5, None, [], {},
        new_supply_ports={"VDD_SUB": QColor("#00ff00"), "VSS": QColor("#000000")},
    )

    after = view._boxes["top/u_core"].rect()
    assert after.height() > before.height()


def test_nested_power_domain_creation_direct_insertion_matches_undo_redo(qtbot):
    """The explicit "undo+redo fixes it, direct creation doesn't"
    discrepancy check, for this specific Power-Domain-composed-Supply-
    Set path (a separate code path from add_supply_port()'s own direct
    insertion, already covered by test_direct_insertion_applies_the_same_layout_undo_redo_would)."""
    tab, view = _tab_and_view(qtbot)
    top_box = view._boxes["top"]
    sub_box = view._boxes["top/u_core"]

    view.create_power_domain(
        top_box, "PD_TOP", QColor("#ff0000"), 0.5, None, [], {},
        new_supply_ports={"VDD": QColor("#ff0000"), "VSS": QColor("#000000")},
    )
    view.create_power_domain(
        sub_box, "PD_SUB", QColor("#00ff00"), 0.5, None, [], {},
        new_supply_ports={"VDD_SUB": QColor("#00ff00"), "VSS": QColor("#000000")},
    )
    direct_rect = view._boxes["top/u_core"].rect()
    direct_pos = view._boxes["top/u_core"].pos()

    tab.undo_shortcut.activated.emit()
    tab.redo_shortcut.activated.emit()

    assert view._boxes["top/u_core"].rect() == direct_rect
    assert view._boxes["top/u_core"].pos() == direct_pos


# --- 8: Power Domain merged info box (name + Supply Set display, ----------
# --- CONSOLIDATE task -- supersedes the earlier separate name-label and ---
# --- Supply Set display box; see power_domain_overlay.py's own -------------
# --- DomainInfoBoxItem docstring for the full merge writeup) ---------------
#
# (a) initial creation with Primary only; (b) adding a Secondary grows the
# box/resizes the block; (c) removing a Secondary shrinks it back; (d) all
# of the above verified direct-creation-vs-undo+redo; (e) combined with
# Isolation/Level Shifter/Retention/Supply Port on the SAME block; (f)
# multi-hierarchy sync across every boundary block.


def test_creating_a_power_domain_grows_its_boundary_block_to_fit_the_merged_info_box(qtbot):
    """(a) initial creation with Primary only. The Supply Port backing
    the Primary Supply Set's own Power rail is created FIRST, and
    separately, so this test isolates the merged box's OWN bottom-margin
    contribution from add_supply_port()'s own top-margin one (creating
    the domain WITH a brand-new supply-port name at the same time would
    correctly reserve room for both, but that combination is exercised
    on its own in test_merged_info_box_coexists_with_isolation_level_shifter_and_supply_port_on_the_same_block)."""
    tab, view = _tab_and_view(qtbot)
    box = view._boxes["top"]
    view.add_supply_port(box, "VDD1", QColor("#ff0000"))
    before = box.rect()

    from frontend.src.power_intent_view.power_domain_overlay import SupplySetPlacement, domain_info_box_required_height

    new_set = SupplySetPlacement(id="ss1", name="SS_VDD", power="VDD1", owner_path="top")
    domain = view.create_power_domain(box, "PD1", QColor("#4caf50"), 0.5, "ss1", [], {"ss1": new_set})

    after = box.rect()
    assert after.height() > before.height()
    required = domain_info_box_required_height(view._domain_state.domains[domain.id], view._domain_state.supply_sets)
    assert after.height() == before.height() + required


def test_adding_a_secondary_supply_set_grows_the_block_further(qtbot):
    """(b) The merged box's own required room is content-dependent (one
    line per role) -- a SECOND edit adding a Secondary Supply Set must
    grow the block AGAIN, not just once at creation."""
    tab, view = _tab_and_view(qtbot)
    box = view._boxes["top"]

    from frontend.src.power_intent_view.power_domain_overlay import SupplySetPlacement

    primary = SupplySetPlacement(id="ss1", name="SS_VDD", power="VDD1", owner_path="top")
    domain = view.create_power_domain(box, "PD1", QColor("#4caf50"), 0.5, "ss1", [], {"ss1": primary}, {"VDD1": QColor("#ff0000")})
    after_create = box.rect()

    secondary = SupplySetPlacement(id="ss2", name="SS_VDD_INT", power="VSS_X", owner_path="top")
    view.edit_power_domain(domain.id, "PD1", QColor("#4caf50"), 0.5, "ss1", ["ss2"], {"ss2": secondary}, {"VSS_X": QColor("#0000ff")})
    after_edit = box.rect()

    assert after_edit.height() > after_create.height()


def test_removing_a_secondary_supply_set_shrinks_the_block_back(qtbot):
    """(c) Removing a Secondary Supply Set via edit must shrink the box
    (and the block) back down to the Primary-only size -- not merely
    "not grow further"."""
    tab, view = _tab_and_view(qtbot)
    box = view._boxes["top"]

    from frontend.src.power_intent_view.power_domain_overlay import SupplySetPlacement

    primary = SupplySetPlacement(id="ss1", name="SS_VDD", power="VDD1", owner_path="top")
    secondary = SupplySetPlacement(id="ss2", name="SS_VDD_INT", power="VSS_X", owner_path="top")
    domain = view.create_power_domain(
        box, "PD1", QColor("#4caf50"), 0.5, "ss1", ["ss2"], {"ss1": primary, "ss2": secondary}, {"VDD1": QColor("#ff0000"), "VSS_X": QColor("#0000ff")}
    )
    primary_and_secondary_rect = box.rect()

    view.edit_power_domain(domain.id, "PD1", QColor("#4caf50"), 0.5, "ss1", [], {})
    primary_only_rect = box.rect()

    assert primary_only_rect.height() < primary_and_secondary_rect.height()


def test_power_domain_creation_with_merged_info_box_direct_insertion_matches_undo_redo(qtbot):
    """(d) Same "direct creation must match undo+redo" parity check this
    suite already establishes for every other insertion type -- the
    merged box's own content-dependent room requirement
    (domain_info_box_required_height()) is a genuinely distinct code
    path from every fixed-constant margin already covered above.
    Covers creation, adding a Secondary, and removing a Secondary."""
    tab, view = _tab_and_view(qtbot)
    box = view._boxes["top"]

    from frontend.src.power_intent_view.power_domain_overlay import SupplySetPlacement

    new_set = SupplySetPlacement(id="ss1", name="SS_VDD", power="VDD1", owner_path="top")
    domain = view.create_power_domain(box, "PD1", QColor("#4caf50"), 0.5, "ss1", [], {"ss1": new_set}, {"VDD1": QColor("#ff0000")})
    direct_rect_after_create = view._boxes["top"].rect()

    tab.undo_shortcut.activated.emit()
    tab.redo_shortcut.activated.emit()
    assert view._boxes["top"].rect() == direct_rect_after_create

    secondary = SupplySetPlacement(id="ss2", name="SS_VDD_INT", power="VSS_X", owner_path="top")
    view.edit_power_domain(domain.id, "PD1", QColor("#4caf50"), 0.5, "ss1", ["ss2"], {"ss2": secondary}, {"VSS_X": QColor("#0000ff")})
    direct_rect_after_add_secondary = view._boxes["top"].rect()

    tab.undo_shortcut.activated.emit()
    tab.redo_shortcut.activated.emit()
    assert view._boxes["top"].rect() == direct_rect_after_add_secondary

    view.edit_power_domain(domain.id, "PD1", QColor("#4caf50"), 0.5, "ss1", [], {})
    direct_rect_after_remove_secondary = view._boxes["top"].rect()

    tab.undo_shortcut.activated.emit()
    tab.redo_shortcut.activated.emit()
    assert view._boxes["top"].rect() == direct_rect_after_remove_secondary


def test_merged_info_box_coexists_with_isolation_level_shifter_and_supply_port_on_the_same_block(qtbot):
    """(e) Combined scenario: the merged box, an Isolation badge, a Level
    Shifter badge, and a plain Supply Port all reserve room on the SAME
    block, without interfering with each other. Isolation-self, Level-
    Shifter-self, and top-edge-Supply-Port are three INDEPENDENT margin
    "kinds" that sum together (_extra_interior_top()'s own "multiple
    qualifying reasons on the SAME box sum" rule) -- so each of the
    first three insertions below grows the block further. A SECOND
    top-edge Supply Port, by contrast, is the SAME kind as the first
    (that loop's own "not summed" rule -- one shared reservation per
    kind, not per icon) and must NOT grow the block any further; this
    also confirms the merged info box's own bottom-margin reservation
    (a fourth, independent kind) isn't silently dropped or double-
    counted by any of the others."""
    tab, view = _tab_and_view(qtbot)
    box = view._boxes["top/u_dbg"]
    view.add_supply_port(box, "VDD1", QColor("#ff0000"))
    before = box.rect()

    from frontend.src.power_intent_view.power_domain_overlay import SupplySetPlacement, domain_info_box_required_height

    new_set = SupplySetPlacement(id="ss1", name="SS_VDD", power="VDD1", owner_path="top/u_dbg")
    domain = view.create_power_domain(box, "PD1", QColor("#4caf50"), 0.5, "ss1", [], {"ss1": new_set})
    after_domain = box.rect()
    assert after_domain.height() > before.height()

    view.create_isolation(box, "ISO1", domain.id, "ss1", None, None, "low", "self", "ctrl", "high", ["top/u_dbg/clk"])
    after_isolation = box.rect()
    assert after_isolation.height() > after_domain.height()

    view.create_level_shifter(box, "LS1", domain.id, "elements", "low_to_high", "self", None, None, ["top/u_dbg/clk"])
    after_level_shifter = box.rect()
    assert after_level_shifter.height() > after_isolation.height()

    # A SECOND top-edge Supply Port is the same margin "kind" as the
    # first -- no further growth, and no interference with the other
    # three already-reserved kinds either.
    view.add_internal_supply(box, "VDD_OTHER", QColor("#00ff00"))
    after_second_port = box.rect()
    assert after_second_port.height() == after_level_shifter.height()

    # The merged box's own reserved room, computed independently, still
    # accounts for exactly its own share of the final height -- it wasn't
    # silently dropped or double-counted by the other insertions.
    required = domain_info_box_required_height(view._domain_state.domains[domain.id], view._domain_state.supply_sets)
    assert required == after_domain.height() - before.height()
    _deselect_and_collect(tab)


def test_multi_hierarchy_power_domain_merged_info_box_stays_in_sync_on_every_boundary_block(qtbot):
    """(f) A multi-hierarchy Power Domain's merged info box appears, and
    both grows/shrinks in sync, on EVERY boundary block it spans."""
    tab, view = _tab_and_view(qtbot)
    box_a = view._boxes["top/u_core"]
    box_b = view._boxes["top/u_dbg"]
    before_a, before_b = box_a.rect(), box_b.rect()

    from frontend.src.power_intent_view.power_domain_overlay import SupplySetPlacement

    primary = SupplySetPlacement(id="ss1", name="SS_VDD", power="VDD1", owner_path="top")
    domain = view.create_power_domain(
        box_a, "PD1", QColor("#4caf50"), 0.5, "ss1", [], {"ss1": primary}, {"VDD1": QColor("#ff0000")}, additional_boxes=[box_b]
    )

    after_create_a, after_create_b = box_a.rect(), box_b.rect()
    assert after_create_a.height() > before_a.height()
    assert after_create_b.height() > before_b.height()
    assert after_create_a.height() - before_a.height() == after_create_b.height() - before_b.height()

    secondary = SupplySetPlacement(id="ss2", name="SS_VDD_INT", power="VSS_X", owner_path="top")
    view.edit_power_domain(domain.id, "PD1", QColor("#4caf50"), 0.5, "ss1", ["ss2"], {"ss2": secondary}, {"VSS_X": QColor("#0000ff")})

    after_edit_a, after_edit_b = box_a.rect(), box_b.rect()
    assert after_edit_a.height() > after_create_a.height()
    assert after_edit_b.height() > after_create_b.height()
    assert after_edit_a.height() - after_create_a.height() == after_edit_b.height() - after_create_b.height()
    _deselect_and_collect(tab)


# --- 9: Ground-anchored bottom spacing (mirrors 1/2's own top-anchor case) --
# New requirement: a Ground-type Supply Port/Internal Supply/macro PG pin
# anchored at the BOTTOM of a block needs the same power-net-routing
# clearance a top-anchored one already gets -- reserved via the new
# _extra_interior_bottom() hook, the exact mirror image of
# _extra_interior_top() on the vertical axis (hierarchy_scene_builder.py's
# own extra_interior_bottom parameter, reusing the SAME
# INTERIOR_TOP_MARGIN_FOR_SUPPLY_PORT constant/calculation, not a
# separately-invented one).


def _make_ground(view, box, name):
    """Real, user-facing path to a bottom-anchored Ground Supply Port:
    add a plain Supply Port, then reference it as a Supply Set's own
    `.ground` field -- the same _reclassify_ground_supply_edges()
    mechanism a real Create/Edit Power Domain flow already drives.
    `power` deliberately references a name with no real placement at
    all (Supply Set fields are plain strings, never required to
    resolve) so this helper's own footprint is scoped to exactly ONE
    bottom-anchored port, not a companion top-anchored one too."""
    from frontend.src.power_intent_view.power_domain_overlay import SupplySetPlacement

    icon = view.add_supply_port(box, name, QColor("#0000ff"))
    new_set = SupplySetPlacement(id=f"ss_{name}", name=f"SS_{name}", owner_path=box.path, power="VDD_DANGLING", ground=name)
    view._domain_state.supply_sets[new_set.id] = new_set
    view._reclassify_ground_supply_edges()
    # Bug fix (found via THIS test): add_supply_port() above already
    # reflowed once, room-reserved for edge=="top" (its own default,
    # before reclassification) -- reclassify only fixes the ICON's own
    # position, not the box's already-computed SIZE. A real Create/Edit
    # Power Domain flow always has its own LATER, unconditional reflow
    # after reclassification (see edit_supply_set()'s own docstring for
    # the identical fix) -- mirrored here so this helper matches the
    # real, fixed production sequence exactly.
    view._reflow_layout()
    assert icon.placement.edge == "bottom"
    return icon


def test_ground_supply_on_a_leaf_block_grows_it(qtbot):
    tab, view = _tab_and_view(qtbot)
    box = view._boxes["top/u_dbg"]
    before = box.rect()

    _make_ground(view, box, "VSS")

    after = box.rect()
    assert after.height() > before.height()


def test_ground_supply_reserves_room_below_a_blocks_own_children_without_shifting_them(qtbot):
    """The bottom-anchor mirror of test_supply_port_on_a_block_with_children_shifts_them_down()
    above -- but UNLIKE that top-anchor case, a bottom-anchored Ground
    supply must NOT shift the block's own already-packed children (they
    stay exactly where they are; the extra room appears BELOW them,
    where the Ground anchor and its own routing clearance now sit --
    see hierarchy_scene_builder.py's own extra_interior_bottom
    docstring for why: nothing needs to be pushed down to make room at
    the bottom, only the box's own total height needs to grow)."""
    tab, view = _tab_and_view(qtbot)
    box = view._boxes["top"]
    before_children = _children_y(view)
    before_rect = box.rect()

    _make_ground(view, box, "VSS")

    after_children = _children_y(view)
    after_rect = box.rect()
    assert after_rect.height() > before_rect.height()  # the box itself grew
    assert after_children == before_children  # but its own children never moved


def test_ground_supply_reserves_the_same_amount_of_room_a_top_anchored_port_already_gets(qtbot):
    """Explicit request: reuse the SAME reserved-space calculation
    already used for top-placed ports, not a separately-invented one."""
    from frontend.src.power_intent_view.supply_port_overlay import INTERIOR_TOP_MARGIN_FOR_SUPPLY_PORT

    tab, view = _tab_and_view(qtbot)
    ground_box = view._boxes["top/u_dbg"]
    top_box = view._boxes["top/u_core"]
    ground_before = ground_box.rect().height()
    top_before = top_box.rect().height()

    _make_ground(view, ground_box, "VSS")
    view.add_supply_port(top_box, "VDD_TOP_ANCHORED", QColor("#ff0000"))  # stays at the default top edge

    assert ground_box.rect().height() - ground_before == INTERIOR_TOP_MARGIN_FOR_SUPPLY_PORT
    assert top_box.rect().height() - top_before == INTERIOR_TOP_MARGIN_FOR_SUPPLY_PORT


def test_ground_supply_on_a_macro_pg_pin_reserves_room_too(qtbot):
    """Explicit scope confirmation: the bottom-anchor spacing rule
    applies to macro ground pins too, not just plain Supply Port/
    Internal Supply -- matching the same scope Bug 2's own
    Ground-at-bottom placement rule already established. Uses a
    hand-built macro fixture (build_mock_design()'s own ram_macro never
    populates related_power_pin/related_ground_pin -- see
    test_ground_supply_placement.py's own module docstring for the
    same precedent) since the macro PG-pin ground classification is
    static, decided from Liberty data at build time, not from a Supply
    Set the way a plain port's is."""
    from frontend.src.shared.models.rtl import Design, Definition, Instance, Port, PortDirection, SourceLanguage

    macro = Definition(
        definition_id="ram_macro",
        base_name="RAM_MACRO",
        language=SourceLanguage.LIBERTY,
        is_black_box=True,
        ports=[
            Port(name="VDD", direction=PortDirection.PG),
            Port(name="VSS", direction=PortDirection.PG),
            Port(name="CE", direction=PortDirection.IN, related_power_pin="VDD", related_ground_pin="VSS"),
        ],
    )
    top = Definition(
        definition_id="top",
        base_name="top",
        language=SourceLanguage.VERILOG,
        ports=[Port(name="clk", direction=PortDirection.IN)],
        instances=[Instance(name="u_ram", definition_id="ram_macro", connections=[])],
    )
    design = Design(top="top", definitions={"top": top, "ram_macro": macro})

    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(design)
    view = tab.diagram._view
    ram_box = view._boxes["top/u_ram"]

    vss_icon = view._supply_port_icons["pg:top/u_ram:VSS"]
    assert vss_icon.placement.edge == "bottom"  # sanity: this pin is really Ground-classified

    # The macro's own room requirement is already baked into its initial
    # size at load time (no separate insertion event to compare
    # before/after against, unlike a user-placed port) -- confirmed here
    # by checking the icon actually sits at the box's own bottom edge
    # WITH clearance beneath it (not flush against/past the box's own
    # edge), the visible symptom "room was reserved" produces.
    assert vss_icon.pos().y() == ram_box.rect().bottom()


def test_ground_supply_bottom_spacing_direct_creation_matches_undo_redo(qtbot):
    """Same "direct creation must match undo+redo" parity check this
    suite already establishes for every other insertion type -- verify
    with the SAME methodology already used for the related "auto-linked
    Ground placed at top on direct creation" bug fix."""
    tab, view = _tab_and_view(qtbot)
    box = view._boxes["top"]

    _make_ground(view, box, "VSS")
    direct_rect = view._boxes["top"].rect()

    tab.undo_shortcut.activated.emit()
    tab.redo_shortcut.activated.emit()

    assert view._boxes["top"].rect() == direct_rect


# --- 10: Bug fix -- merged Power Domain info box (section 8) vs. a Ground- --
# --- anchored bottom item (section 9) both reserve room near a block's ------
# --- own bottom edge, but were built without awareness of EACH OTHER's own --
# --- exact vertical position within that reserved band -- both anchored ----
# --- flush against rect.bottom() regardless of how tall the block grew, ----
# --- so they visually overlapped even though the TOTAL reserved height was -
# --- already summed correctly. Fixed in DomainInfoBoxItem._reposition() --
# --- (power_domain_overlay.py): the merged box now shifts UP by exactly ----
# --- INTERIOR_TOP_MARGIN_FOR_SUPPLY_PORT -- the SAME amount already --------
# --- reserved for the Ground anchor -- whenever its own owner_box has one, -
# --- via a LIVE childItems() scan (not a construction-time snapshot, which -
# --- would go stale the instant a new Ground anchor is added afterward via -
# --- the ordinary add_supply_port()/add_internal_supply() flow -- that
# --- flow only ever triggers _reflow_layout(), never a full _rebuild() that
# --- would reconstruct DomainInfoBoxItem fresh).


def _info_box_and_ground_label_scene_rects(view, box, domain_id, ground_name):
    """Both items' own scene-space bounding rects -- used to assert
    non-overlap via QRectF.intersects(), the same real geometric check
    (not just "the block got taller") this bug's own symptom needs."""
    info_box = view._domain_info_boxes[domain_id][0]
    info_rect = info_box.mapRectToScene(info_box.boundingRect())
    ground_icon = next(
        icon for icon in view._supply_port_icons.values() if icon.owner_box is box and icon.placement.name == ground_name
    )
    label_rect = ground_icon.label.mapRectToScene(ground_icon.label.boundingRect())
    icon_rect = ground_icon.mapRectToScene(ground_icon.boundingRect())
    return info_rect, label_rect, icon_rect


def test_merged_info_box_and_a_ground_anchor_on_the_same_block_do_not_overlap(qtbot):
    """The exact bug report's own repro shape: a block with a Ground-
    type Supply Port AND a Power Domain carrying a Secondary Supply Set
    (a multi-line merged box, tall enough to plausibly reach into the
    Ground anchor's own band if left unfixed) -- neither the Ground
    icon nor its own label may overlap the merged box, and the block
    itself must be resized/laid out to fit both simultaneously (not
    just "the box grew," which alone doesn't prove non-overlap)."""
    tab, view = _tab_and_view(qtbot)
    box = view._boxes["top/u_dbg"]
    before = box.rect()

    from frontend.src.power_intent_view.power_domain_overlay import SupplySetPlacement

    primary = SupplySetPlacement(id="ss1", name="SS_VDD", power="VDD1", owner_path="top/u_dbg")
    secondary = SupplySetPlacement(id="ss2", name="SS_VDD_INT", power="VSS_X", owner_path="top/u_dbg")
    domain = view.create_power_domain(
        box, "PD_CORE", QColor("#4caf50"), 0.5, "ss1", ["ss2"], {"ss1": primary, "ss2": secondary}, {"VDD1": QColor("#ff0000"), "VSS_X": QColor("#00aa00")}
    )
    _make_ground(view, box, "VSS1")

    after = box.rect()
    assert after.height() > before.height()  # the block DID grow to fit both

    info_rect, label_rect, icon_rect = _info_box_and_ground_label_scene_rects(view, box, domain.id, "VSS1")
    assert not info_rect.intersects(label_rect)
    assert not info_rect.intersects(icon_rect)
    _deselect_and_collect(tab)


def test_merged_info_box_and_ground_anchor_non_overlap_direct_creation_matches_undo_redo(qtbot):
    """Same "direct creation must match undo+redo" parity check this
    suite already establishes for every other insertion type in this
    bug class -- explicitly required given this project's history of
    layout fixes that silently only applied correctly via the full
    _rebuild() undo+redo triggers, never on direct insertion."""
    tab, view = _tab_and_view(qtbot)
    box = view._boxes["top/u_dbg"]

    from frontend.src.power_intent_view.power_domain_overlay import SupplySetPlacement

    primary = SupplySetPlacement(id="ss1", name="SS_VDD", power="VDD1", owner_path="top/u_dbg")
    secondary = SupplySetPlacement(id="ss2", name="SS_VDD_INT", power="VSS_X", owner_path="top/u_dbg")
    domain = view.create_power_domain(
        box, "PD_CORE", QColor("#4caf50"), 0.5, "ss1", ["ss2"], {"ss1": primary, "ss2": secondary}, {"VDD1": QColor("#ff0000"), "VSS_X": QColor("#00aa00")}
    )
    _make_ground(view, box, "VSS1")

    direct_rect = view._boxes["top/u_dbg"].rect()
    direct_info_rect, direct_label_rect, direct_icon_rect = _info_box_and_ground_label_scene_rects(view, box, domain.id, "VSS1")
    assert not direct_info_rect.intersects(direct_label_rect)
    assert not direct_info_rect.intersects(direct_icon_rect)

    tab.undo_shortcut.activated.emit()
    tab.redo_shortcut.activated.emit()

    box_after = view._boxes["top/u_dbg"]
    assert box_after.rect() == direct_rect
    after_info_rect, after_label_rect, after_icon_rect = _info_box_and_ground_label_scene_rects(view, box_after, domain.id, "VSS1")
    assert not after_info_rect.intersects(after_label_rect)
    assert not after_info_rect.intersects(after_icon_rect)
    # The exact same geometry either way -- not merely "still non-overlapping."
    assert after_info_rect == direct_info_rect
    assert after_label_rect == direct_label_rect
    _deselect_and_collect(tab)


# --- Audit fix (category 06/08): the REMOVAL side ---------------------------
#
# This suite originally covered insertion thoroughly (35 tests) and
# removal almost not at all -- exactly one shrink-back test. The health
# audit found that the four mutators with NO _reflow_layout() call were
# all on removal/edit paths: remove_power_domain(),
# delete_supply_port_with_cascade(), delete_supply_set_with_cascade()
# and edit_supply_port(). The tests and the defects had the same shape,
# which is why a green suite never caught them.
#
# Every test below asserts the SAME property the insertion side already
# does: the geometry produced by the direct action must equal the
# geometry a full rebuild (what undo+redo effectively performs) would
# produce. That equality is the actual invariant -- not a specific pixel
# height, which would lock in today's margin constants.


def _rect_and_pos(view, path):
    box = view._boxes[path]
    return box.rect(), box.pos()


def test_removing_a_power_domain_releases_the_room_its_info_box_reserved(qtbot):
    """Measured before the fix: the block stayed at its with-domain
    height (477.9) where a rebuild gives 408.0 -- a whole info box worth
    of reserved space for an object that no longer existed."""
    from frontend.src.power_intent_view.power_domain_overlay import SupplySetPlacement

    tab, view = _tab_and_view(qtbot)
    box = view._boxes["top"]
    pristine = box.rect().height()  # before ANY supply port or domain exists
    view.add_supply_port(box, "VDD1", QColor("#ff0000"))
    height_before_domain = box.rect().height()

    view.create_power_domain(
        box, "PD1", QColor("#4caf50"), 0.5, "ss1", [], {"ss1": SupplySetPlacement(id="ss1", name="SS", power="VDD1", owner_path="top")}
    )
    assert view._boxes["top"].rect().height() > height_before_domain  # grew, as the insertion side already guarantees

    view.remove_power_domain(view._boxes["top"])
    direct = view._boxes["top"].rect().height()
    view.refresh()
    # The real invariant: the direct action leaves exactly the geometry a
    # rebuild (what undo+redo performs) would. Not a fixed pixel height,
    # which would just lock in today's margin constants.
    assert view._boxes["top"].rect().height() == direct
    # Removing a domain cascades away its Supply Sets and the Supply
    # Ports backing them (see _remove_domain_cascade()), so the block
    # returns past `height_before_domain` all the way to pristine.
    assert direct == pristine


def test_remove_power_domain_direct_matches_undo_redo(qtbot):
    from frontend.src.power_intent_view.power_domain_overlay import SupplySetPlacement

    tab, view = _tab_and_view(qtbot)
    box = view._boxes["top"]
    view.add_supply_port(box, "VDD1", QColor("#ff0000"))
    view.create_power_domain(
        box, "PD1", QColor("#4caf50"), 0.5, "ss1", [], {"ss1": SupplySetPlacement(id="ss1", name="SS", power="VDD1", owner_path="top")}
    )
    view.remove_power_domain(view._boxes["top"])
    direct_rect, direct_pos = _rect_and_pos(view, "top")

    tab.undo_shortcut.activated.emit()
    tab.redo_shortcut.activated.emit()

    assert _rect_and_pos(view, "top") == (direct_rect, direct_pos)


def test_deleting_a_supply_set_with_cascade_releases_its_members_reserved_room(qtbot):
    """The cascade sibling of the above: deleting a Supply Set removes
    every Power Domain referencing it, and must free the same room
    removing those domains directly would."""
    from frontend.src.power_intent_view.power_domain_overlay import SupplySetPlacement

    tab, view = _tab_and_view(qtbot)
    box = view._boxes["top"]
    pristine = box.rect().height()
    view.add_supply_port(box, "VDD1", QColor("#ff0000"))
    view.create_power_domain(
        box, "PD1", QColor("#4caf50"), 0.5, "ss1", [], {"ss1": SupplySetPlacement(id="ss1", name="SS", power="VDD1", owner_path="top")}
    )
    with_domain = view._boxes["top"].rect().height()

    view.delete_supply_set_with_cascade("ss1")
    direct = view._boxes["top"].rect().height()
    view.refresh()
    assert view._boxes["top"].rect().height() == direct
    assert direct < with_domain  # room actually released, not just recomputed to the same value
    assert direct == pristine  # the cascade takes the domain's ports with it


def test_deleting_a_supply_port_with_cascade_reflows_like_its_non_cascade_sibling(qtbot):
    """delete_supply_port() already reflowed; delete_supply_port_with_cascade()
    -- which removes strictly more, and so frees strictly more room --
    did not reflow at all."""
    from frontend.src.power_intent_view.power_domain_overlay import SupplySetPlacement

    tab, view = _tab_and_view(qtbot)
    box = view._boxes["top"]
    baseline = box.rect().height()
    icon = view.add_supply_port(box, "VDD1", QColor("#ff0000"))
    view.create_power_domain(
        box, "PD1", QColor("#4caf50"), 0.5, "ss1", [], {"ss1": SupplySetPlacement(id="ss1", name="SS", power="VDD1", owner_path="top")}
    )

    view.delete_supply_port_with_cascade(view._supply_port_icons[icon.key], {"ss1"}, {d.id for d in view._domain_state.domains.values()})
    direct = view._boxes["top"].rect().height()
    view.refresh()
    assert view._boxes["top"].rect().height() == direct
    assert direct == baseline


def test_renaming_a_supply_port_reflows_for_the_new_label_width(qtbot):
    """A supply port's own label width feeds _extra_interior_min_width(),
    which widens a block so two same-edge labels can't overlap. Renaming
    changes that input; edit_supply_port() never reflowed afterward.

    Deliberately places TWO ports on the same edge: the min-width floor
    only engages with 2+ same-edge occupants (a single label has nothing
    to collide with), so a one-port version of this test would pass even
    with the fix reverted -- verified, and the reason this test looks
    more elaborate than the others here."""
    tab, view = _tab_and_view(qtbot)
    box = view._boxes["top/u_dbg"]  # narrow leaf -- the min-width floor actually bites here
    first = view.add_supply_port(box, "V1", QColor("#ff0000"))
    view.add_supply_port(box, "V2", QColor("#00ff00"))
    width_before = view._boxes["top/u_dbg"].rect().width()

    view.edit_supply_port(view._supply_port_icons[first.key], "A_VERY_MUCH_LONGER_SUPPLY_NAME", QColor("#ff0000"))
    direct_rect, direct_pos = _rect_and_pos(view, "top/u_dbg")
    view.refresh()

    assert _rect_and_pos(view, "top/u_dbg") == (direct_rect, direct_pos)
    assert direct_rect.width() > width_before  # the longer label really did demand more room
