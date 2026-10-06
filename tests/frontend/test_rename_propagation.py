"""Rename propagation for Supply Ports and Power Domains -- explicit
request: renaming either must update every place that references it,
with no orphaned/stale reference left anywhere (including the
Properties panel).

Verified empirically, not assumed, which entity type needed a fix:
- Power Domain: every reference (PowerDomainPlacement.primary_supply_set/
  secondary_supply_sets, RetentionStrategyPlacement.power_domain_id) is
  already an id, resolved to a display name fresh at render time (the
  Retention dialog's own domain_label, properties_panel.py's own
  _load_retention()/_load_domains()) -- structurally already safe, no
  code change needed. test_b below locks this in as a regression test.
- Supply Port: SupplySetPlacement.power/ground/n_well/p_well are the ONE
  place in this diagram-only model that reference a Supply Port by raw
  NAME, not id (see SupplySetPlacement's own docstring) -- a real,
  fixable gap, closed by
  _PowerIntentGraphicsView._propagate_supply_port_rename(), called from
  edit_supply_port(). test_a below covers it end to end.
"""

from PySide6.QtGui import QColor

from frontend.src.power_intent_view.power_domain_overlay import PowerDomainPlacement, SupplySetPlacement
from frontend.src.power_intent_view.power_intent_tab import PowerIntentTab
from frontend.src.power_intent_view.supply_port_overlay import SupplyPortIconItem, SupplyPortPlacement
from frontend.src.shared.mock_backend import build_mock_design


def _rows(table):
    return [[table.item(r, c).text() if table.item(r, c) is not None else None for c in range(table.columnCount())] for r in range(table.rowCount())]


# --- (a) Supply Port rename propagates to Power Domain (via Supply Set) and --
#     Retention (indirectly, through the same Supply Set) --------------------


def test_a_renaming_a_supply_port_updates_the_supply_set_that_references_it(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top"]
    port = view.add_supply_port(box, "VDD_OLD", QColor("#ff0000"))
    supply_set = SupplySetPlacement(id="s1", name="SS1", power="VDD_OLD", ground="VSS", n_well="VDD_OLD", owner_path="top")
    domain = view.create_power_domain(box, "PD_TOP", QColor("#4caf50"), 0.5, "s1", [], {"s1": supply_set})
    strategy = view.create_retention_strategy(box, domain, "RET1", "s1", "s1", "save", "restore", [], {})

    view.edit_supply_port(port, "VDD_NEW", QColor("#ff0000"))

    updated_set = view._domain_state.supply_sets["s1"]
    assert updated_set.power == "VDD_NEW"
    assert updated_set.n_well == "VDD_NEW"
    assert updated_set.ground == "VSS"  # untouched -- never referenced the renamed port
    assert "VDD_OLD" not in (updated_set.power, updated_set.ground, updated_set.n_well, updated_set.p_well)

    # The Retention strategy still references the SAME Supply Set by id
    # (never touched directly) -- and that Supply Set's own name/fields
    # are what actually get displayed everywhere, so the rename is
    # already visible through it with nothing further to propagate.
    assert view._retention_state.strategies[strategy.id].primary_supply_set_id == "s1"


def test_a_rename_is_visible_in_the_supply_set_properties_table_with_no_stale_row(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top"]
    port = view.add_supply_port(box, "VDD_OLD", QColor("#ff0000"))
    supply_set = SupplySetPlacement(id="s1", name="SS1", power="VDD_OLD", ground="VSS", owner_path="top")
    view.create_power_domain(box, "PD_TOP", QColor("#4caf50"), 0.5, "s1", [], {"s1": supply_set})

    view.edit_supply_port(port, "VDD_NEW", QColor("#ff0000"))

    rows = _rows(tab.properties._tables["SupplySet"])
    row = next(r for r in rows if r[0] == "SS1")
    assert row[1] == "VDD_NEW"  # Power column
    assert "VDD_OLD" not in row  # no stale reference left anywhere in the row


def test_a_rename_does_not_affect_a_same_named_port_on_a_different_hierarchy(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    port = view.add_supply_port(view._boxes["top"], "VDD_SAME", QColor("#ff0000"))
    # Constructed directly rather than via add_supply_port() -- items
    # 11/12's own new global naming-uniqueness rule would otherwise
    # (correctly) reject this exact same-name-on-an-unrelated-hierarchy
    # setup; this test's own concern is rename-propagation SCOPING, not
    # naming validation, so the collision is seeded directly, bypassing
    # that unrelated check.
    other_box = view._boxes["top/u_core"]
    other_port = SupplyPortPlacement(id="other_port", owner_path=other_box.path, name="VDD_SAME", color=QColor("#00ff00"))
    view._shared_state.supply_port_placements[other_port.id] = other_port
    view._supply_port_icons[other_port.id] = SupplyPortIconItem(other_box, other_port)
    unrelated_set = SupplySetPlacement(id="s2", name="SS_UNRELATED", power="VDD_SAME", owner_path="top/u_core")
    view._domain_state.supply_sets["s2"] = unrelated_set

    view.edit_supply_port(port, "VDD_RENAMED", QColor("#ff0000"))

    assert view._domain_state.supply_sets["s2"].power == "VDD_SAME"  # untouched -- different owner_path


def test_a_rename_is_a_single_undoable_step(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top"]
    port = view.add_supply_port(box, "VDD_OLD", QColor("#ff0000"))
    supply_set = SupplySetPlacement(id="s1", name="SS1", power="VDD_OLD", owner_path="top")
    view.create_power_domain(box, "PD_TOP", QColor("#4caf50"), 0.5, "s1", [], {"s1": supply_set})

    view.edit_supply_port(port, "VDD_NEW", QColor("#ff0000"))
    tab.tree.undo_stack.undo()

    assert view._shared_state.supply_port_placements[port.key].name == "VDD_OLD"
    assert view._domain_state.supply_sets["s1"].power == "VDD_OLD"  # rename AND propagation both undone together


# --- (b) Power Domain rename propagates to Retention (already ID-based) -----


def test_b_renaming_a_power_domain_updates_the_retention_dialogs_own_domain_field(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top"]
    port = view.add_supply_port(box, "VDD", QColor("#ff0000"))
    supply_set = SupplySetPlacement(id="s1", name="SS1", power="VDD", owner_path="top")
    domain = view.create_power_domain(box, "PD_OLD", QColor("#4caf50"), 0.5, "s1", [], {"s1": supply_set})
    strategy = view.create_retention_strategy(box, domain, "RET1", "s1", "s1", "save", "restore", [], {})

    view.edit_power_domain(domain.id, "PD_NEW", QColor("#4caf50"), 0.5, "s1", [], {})

    renamed_domain = view._domain_state.domains[domain.id]
    assert renamed_domain.name == "PD_NEW"
    # The strategy's own reference is by id, resolved fresh -- confirm
    # the live lookup a dialog/Info view would perform reflects the new
    # name, with no cached "PD_OLD" surviving anywhere.
    resolved = view._domain_state.domains[view._retention_state.strategies[strategy.id].power_domain_id]
    assert resolved.name == "PD_NEW"
    assert resolved.name != "PD_OLD"


def test_b_renaming_a_power_domain_updates_the_retention_properties_tab(qtbot):
    """Explicit requirement: confirm the Retention strategy's displayed
    Power Domain reflects the new name in the Properties table's own
    Retention tab, with no stale reference left."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top"]
    view.add_supply_port(box, "VDD", QColor("#ff0000"))
    supply_set = SupplySetPlacement(id="s1", name="SS1", power="VDD", owner_path="top")
    domain = view.create_power_domain(box, "PD_OLD", QColor("#4caf50"), 0.5, "s1", [], {"s1": supply_set})
    view.create_retention_strategy(box, domain, "RET1", "s1", "s1", "save", "restore", [], {})

    view.edit_power_domain(domain.id, "PD_NEW", QColor("#4caf50"), 0.5, "s1", [], {})

    rows = _rows(tab.properties._tables["RetentionStrategy"])
    row = next(r for r in rows if r[0] == "RET1")
    assert row[2] == "PD_NEW"  # Domain column
    assert "PD_OLD" not in row


def test_b_renaming_a_power_domain_updates_the_power_domain_tab_itself(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top"]
    domain = view.create_power_domain(box, "PD_OLD", QColor("#4caf50"), 0.5, None, [], {})

    view.edit_power_domain(domain.id, "PD_NEW", QColor("#4caf50"), 0.5, None, [], {})

    names = {r[0] for r in _rows(tab.properties._tables["PowerDomain"])}
    assert "PD_NEW" in names
    assert "PD_OLD" not in names
