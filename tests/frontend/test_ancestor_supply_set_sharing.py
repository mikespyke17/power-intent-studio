"""CHANGE 2 (explicit request): Create/Edit Power Domain's own "Supply
Set" dropdown (superseding the earlier "New Supply Set" button, renamed
`supply_set_combo` -- see create_power_domain_dialog.py's own module
docstring) offers every Supply Set that already exists on a STRICT
ANCESTOR hierarchy of the current block, alongside "Add New...".
Selecting an ancestor Supply Set opens the SAME CreateSupplySetDialog,
pre-filled and LOCKED (every field but "Set as Primary" read-only) from
that Supply Set's own existing values; accepting it SHARES the exact
same SupplySetPlacement id as the new domain's own Primary/Secondary --
confirmed to be a genuinely SHARED reference, not a clone, since
PowerDomainPlacement.primary_supply_set/secondary_supply_sets already
reference a Supply Set purely by id (no new data-model support needed
for two different domains to reference the SAME id).

Six parts, matching the explicit request's own regression list:
  (a) ancestor dropdown offers the ancestor's own Supply Set; selecting
      it opens the dialog pre-filled and locked except "Set as Primary".
  (b) the dropdown excludes sibling-level and descendant-level Supply
      Sets, only true (strict) ancestors.
  (c) editing the shared Supply Set from either domain's context
      updates it everywhere (there's only ever ONE record).
  (d) removing the shared Supply Set's association from ONE domain
      leaves it fully intact, still associated with the OTHER.
  (e) removing it from BOTH domains actually deletes it, with full
      cascade cleanup.
  (f) doesn't interfere with the pre-existing multi-hierarchy Power
      Domain replication mechanism.

Plus explicit "test thoroughly against existing systems" coverage
(Properties table, Power View tree, UPF/CPF text export) -- a shared
Supply Set is never duplicated by any of the three, confirmed by
reading each one's own real code (properties_panel.py's own
_load_supply_sets()/_load_domains(), power_view_tree.py's own
_domain_children(), power_intent_text_export.py's own generate_upf()):
every one of them already resolves a Supply Set purely by id against
the SAME flat `domain_state.supply_sets` dict, so a shared (not
cloned) reference composes correctly with all three for free -- no
production code changes were needed in any of them for this task.

FEATURE EXTENSION (explicit follow-up request, builds on the above):
CHANGE 2's own dialog-level spec never specified what should actually
happen in the diagram/data model as a direct CONSEQUENCE of sharing --
this was the gap: a shared ancestor Supply Set's own pins (e.g. VDD,
VSS at "top") never got a real, materialized Internal Supply on the
NEW (sharing) hierarchy at all, since `new_supply_ports` (the dialog's
own "brand-new name" tracking) never sees them -- nothing was ever
typed for them, they're REUSED, not new. Fixed by generalizing
`_auto_link_cross_hierarchy_supply_sets()`'s own `target_owner_paths`
(power_intent_diagram_view.py) to the REFERENCING domain's own
`owner_paths` (via `_auto_link_domains_supply_sets()`) rather than
always trusting `supply_set.owner_paths` -- identical to the old
default for a NATIVE Supply Set (both lists are always the same
already), but for a SHARED one this is what actually instantiates a
real Internal Supply for each of its pins on the new hierarchy and
auto-links it to the EXISTING (ancestor-level) counterpart of the same
name -- the exact same auto-link/try_connect_supply_ports() mechanism
CHANGE 1's own optional dropdown and the original Bug 9 fix already
established, just triggered automatically here as a direct consequence
of sharing rather than a manual dropdown pick. A pin already local
(e.g. via the SAME new domain's OWN newly-composed Supply Set reusing
the identical name -- the pre-existing "shared rail within one domain"
rule) is correctly deduplicated, never re-created -- see the worked
example below.
"""

import gc

import pytest
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QDialog

from frontend.src.power_intent_view.create_power_domain_dialog import CreatePowerDomainDialog, CreateSupplySetDialog
from frontend.src.power_intent_view.power_domain_overlay import SupplySetPlacement, supply_sets_on_ancestor_hierarchies
from frontend.src.power_intent_view.power_intent_tab import PowerIntentTab
from frontend.src.power_intent_view.power_intent_text_export import generate_upf
from frontend.src.power_intent_view.power_view_tree import build_power_view_tree
from frontend.src.shared.mock_backend import build_mock_design


def _deselect_and_collect(tab: PowerIntentTab) -> None:
    if tab.diagram._view.scene() is not None:
        tab.diagram._view.scene().clearSelection()
    gc.collect()


def _tab_and_view(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    return tab, tab.diagram._view


def _rows(table):
    return [[table.item(r, c).text() if table.item(r, c) is not None else None for c in range(table.columnCount())] for r in range(table.rowCount())]


def _domain_dialog(view, owner_path: str, **kwargs) -> CreatePowerDomainDialog:
    return CreatePowerDomainDialog(
        {},
        [],
        view._existing_domain_names(),
        owner_path,
        all_domains=view._domain_state.domains,
        all_supply_sets=view._domain_state.supply_sets,
        supply_port_icons=view._supply_port_icons,
        **kwargs,
    )


def _accept_with_primary(monkeypatch, primary: bool) -> None:
    def fake_exec(self):
        self.set_as_primary_checkbox.setChecked(primary)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(CreateSupplySetDialog, "exec", fake_exec)


# --- (a) dropdown offers ancestor's Supply Set; dialog opens pre-filled/locked --


def test_ancestor_supply_set_is_offered_and_dialog_opens_pre_filled_and_locked(qtbot, monkeypatch):
    tab, view = _tab_and_view(qtbot)
    top_box = view._boxes["top"]
    core_box = view._boxes["top/u_core"]
    ss_top = SupplySetPlacement(id="ss_top", name="SS_VDD", power="VDD1", ground="VSS1", owner_path="top")
    domain_top = view.create_power_domain(top_box, "PD_TOP", QColor("#4caf50"), 0.5, "ss_top", [], {"ss_top": ss_top}, {"VDD1": QColor("#ff0000"), "VSS1": QColor("#0000ff")})

    dialog = _domain_dialog(view, "top/u_core")
    idx = dialog.supply_set_combo.findText("SS_VDD")
    assert idx > 0
    assert dialog.supply_set_combo.itemData(idx) == "ss_top"

    captured = {}

    def fake_exec(self):
        captured["name_readonly"] = self.name_edit.isReadOnly()
        captured["power_enabled"] = self.power_combo.isEnabled()
        captured["ground_enabled"] = self.ground_combo.isEnabled()
        captured["n_well_enabled"] = self.n_well_combo.isEnabled()
        captured["p_well_enabled"] = self.p_well_combo.isEnabled()
        captured["bias_checkbox_enabled"] = self.enable_bias_supply_checkbox.isEnabled()
        captured["set_as_primary_enabled"] = self.set_as_primary_checkbox.isEnabled()
        captured["power_text"] = self.power_combo.currentText()
        captured["ground_text"] = self.ground_combo.currentText()
        self.set_as_primary_checkbox.setChecked(True)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(CreateSupplySetDialog, "exec", fake_exec)
    dialog.supply_set_combo.setCurrentIndex(idx)

    assert captured["name_readonly"] is True
    assert captured["power_enabled"] is False
    assert captured["ground_enabled"] is False
    assert captured["n_well_enabled"] is False
    assert captured["p_well_enabled"] is False
    assert captured["bias_checkbox_enabled"] is False
    assert captured["set_as_primary_enabled"] is True  # the ONLY interactive element
    assert captured["power_text"] == "VDD1"
    assert captured["ground_text"] == "VSS1"

    # Accepting SHARES the id -- no clone, no new_supply_sets entry.
    assert dialog.primary_supply_set_id == "ss_top"
    assert dialog.new_supply_sets == {}
    domain_core = view.create_power_domain(core_box, "PD_CORE", dialog.color, dialog.transparency, dialog.primary_supply_set_id, dialog.secondary_supply_set_ids, dialog.new_supply_sets, dialog.new_supply_ports)

    assert domain_core.primary_supply_set == "ss_top"
    assert view._domain_state.domains[domain_top.id].primary_supply_set == "ss_top"
    assert len(view._domain_state.supply_sets) == 1  # shared, not duplicated
    _deselect_and_collect(tab)


# --- (b) dropdown excludes sibling/descendant Supply Sets, only true ancestors --


def test_dropdown_excludes_sibling_and_descendant_supply_sets(qtbot):
    tab, view = _tab_and_view(qtbot)
    core_box = view._boxes["top/u_core"]
    dbg_box = view._boxes["top/u_dbg"]

    # A Supply Set on a SIBLING hierarchy ("top/u_dbg") -- not an ancestor of "top/u_core".
    ss_sibling = SupplySetPlacement(id="ss_sibling", name="SS_SIBLING", power="VDD_DBG", owner_path="top/u_dbg")
    view.create_power_domain(dbg_box, "PD_DBG", QColor("#2196f3"), 0.5, "ss_sibling", [], {"ss_sibling": ss_sibling}, {"VDD_DBG": QColor("#00ff00")})

    dialog_core = _domain_dialog(view, "top/u_core")
    assert dialog_core.supply_set_combo.findText("SS_SIBLING") == -1

    # A Supply Set composed ON "top/u_core" itself (about to become a DESCENDANT-scope
    # reference relative to a still-deeper hierarchy) -- not an ancestor of a box below it.
    ss_core = SupplySetPlacement(id="ss_core", name="SS_CORE", power="VDD_CORE", owner_path="top/u_core")
    domain_core = view.create_power_domain(core_box, "PD_CORE", QColor("#4caf50"), 0.5, "ss_core", [], {"ss_core": ss_core}, {"VDD_CORE": QColor("#ff9800")})

    # Re-opening a dialog ON "top/u_core" itself must not offer its OWN Supply Set
    # (not a strict ancestor of itself).
    dialog_core_again = _domain_dialog(view, "top/u_core", editing_domain_id=domain_core.id)
    assert dialog_core_again.supply_set_combo.findText("SS_CORE") == -1
    _deselect_and_collect(tab)


def test_supply_sets_on_ancestor_hierarchies_pure_function(qtbot):
    """Direct, pure-function coverage of the underlying scoping rule
    (power_domain_overlay.supply_sets_on_ancestor_hierarchies()), not
    routed through the dialog -- confirms strict-ancestor-only, no
    sibling, no descendant, no same-path match."""
    ss_ancestor = SupplySetPlacement(id="a", name="A", owner_path="top")
    ss_sibling = SupplySetPlacement(id="b", name="B", owner_path="top/u_dbg")
    ss_same = SupplySetPlacement(id="c", name="C", owner_path="top/u_core")
    ss_descendant = SupplySetPlacement(id="d", name="D", owner_path="top/u_core/inner")
    supply_sets = {"a": ss_ancestor, "b": ss_sibling, "c": ss_same, "d": ss_descendant}

    result = supply_sets_on_ancestor_hierarchies(supply_sets, "top/u_core")

    assert set(result) == {"a"}


# --- (c) editing the shared Supply Set from either domain's context updates both --


def test_editing_the_shared_supply_set_updates_it_everywhere(qtbot, monkeypatch):
    tab, view = _tab_and_view(qtbot)
    top_box = view._boxes["top"]
    core_box = view._boxes["top/u_core"]
    ss_top = SupplySetPlacement(id="ss_top", name="SS_VDD", power="VDD1", owner_path="top")
    domain_top = view.create_power_domain(top_box, "PD_TOP", QColor("#4caf50"), 0.5, "ss_top", [], {"ss_top": ss_top}, {"VDD1": QColor("#ff0000")})
    _accept_with_primary(monkeypatch, True)
    dialog = _domain_dialog(view, "top/u_core")
    dialog.supply_set_combo.setCurrentIndex(dialog.supply_set_combo.findText("SS_VDD"))
    domain_core = view.create_power_domain(core_box, "PD_CORE", dialog.color, dialog.transparency, dialog.primary_supply_set_id, dialog.secondary_supply_set_ids, dialog.new_supply_sets)

    # There's only ONE record -- editing it (by id, e.g. via the Properties
    # table's own Edit action or a Power View tree node from EITHER domain)
    # is inherently visible from both, since both reference the SAME id.
    view.edit_supply_set("ss_top", "SS_VDD_RENAMED", "VDD1", None, None, None)

    assert view._domain_state.supply_sets["ss_top"].name == "SS_VDD_RENAMED"
    # Both domains still reference the SAME id -- and therefore see the update.
    assert view._domain_state.domains[domain_top.id].primary_supply_set == "ss_top"
    assert view._domain_state.domains[domain_core.id].primary_supply_set == "ss_top"
    rows = _rows(tab.properties._tables["SupplySet"])
    matching = [r for r in rows if r[0] == "SS_VDD_RENAMED"]
    assert len(matching) == 1  # exactly one row, not duplicated
    _deselect_and_collect(tab)


# --- (d) removing the association from ONE domain leaves it intact for the OTHER --


def test_removing_the_shared_supply_sets_association_from_one_domain_preserves_the_other(qtbot, monkeypatch):
    tab, view = _tab_and_view(qtbot)
    top_box = view._boxes["top"]
    core_box = view._boxes["top/u_core"]
    ss_top = SupplySetPlacement(id="ss_top", name="SS_VDD", power="VDD1", owner_path="top")
    domain_top = view.create_power_domain(top_box, "PD_TOP", QColor("#4caf50"), 0.5, "ss_top", [], {"ss_top": ss_top}, {"VDD1": QColor("#ff0000")})
    _accept_with_primary(monkeypatch, True)
    dialog = _domain_dialog(view, "top/u_core")
    dialog.supply_set_combo.setCurrentIndex(dialog.supply_set_combo.findText("SS_VDD"))
    domain_core = view.create_power_domain(core_box, "PD_CORE", dialog.color, dialog.transparency, dialog.primary_supply_set_id, dialog.secondary_supply_set_ids, dialog.new_supply_sets)
    assert "ss_top" in view._domain_state.supply_sets

    # Remove the association from domain_core's own list (edit, not delete).
    view.edit_power_domain(domain_core.id, "PD_CORE", dialog.color, dialog.transparency, None, [], {})

    assert view._domain_state.domains[domain_core.id].primary_supply_set is None
    # Still fully intact, still Primary for domain_top.
    assert "ss_top" in view._domain_state.supply_sets
    assert view._domain_state.domains[domain_top.id].primary_supply_set == "ss_top"
    # The underlying real Supply Port ("VDD1", physically on "top") is
    # still alive too -- domain_top's own Supply Set still needs it.
    assert any(p.name == "VDD1" and p.owner_path == "top" for p in view._shared_state.supply_port_placements.values())
    _deselect_and_collect(tab)


# --- (e) removing it from BOTH domains fully deletes it, with cascade cleanup ----


def test_removing_the_shared_supply_set_from_both_domains_fully_deletes_it(qtbot, monkeypatch):
    """Note (updated after the auto-link feature extension): selecting
    the ancestor's SS_VDD via the dropdown now ALSO auto-materializes+
    links its own "VDD1" pin as a real, INDEPENDENT Internal Supply on
    "top/u_core" (see the FEATURE EXTENSION section below) -- an
    ordinary auto-linked entity from that point on, tracked only by its
    own owner_path+name, with no back-reference to "ss_top". Removing
    "ss_top" itself (the SHARED SUPPLY SET RECORD) from both domains
    correctly deletes THAT record and cleans up the ORIGINAL "top"-level
    "VDD1" once domain_top no longer needs it -- but the separate,
    already-materialized auto-linked COPY on "top/u_core" persists,
    exactly as any other auto-linked/manually-placed port would once
    the Supply Set that originally motivated its creation is gone."""
    tab, view = _tab_and_view(qtbot)
    top_box = view._boxes["top"]
    core_box = view._boxes["top/u_core"]
    ss_top = SupplySetPlacement(id="ss_top", name="SS_VDD", power="VDD1", owner_path="top")
    domain_top = view.create_power_domain(top_box, "PD_TOP", QColor("#4caf50"), 0.5, "ss_top", [], {"ss_top": ss_top}, {"VDD1": QColor("#ff0000")})
    _accept_with_primary(monkeypatch, True)
    dialog = _domain_dialog(view, "top/u_core")
    dialog.supply_set_combo.setCurrentIndex(dialog.supply_set_combo.findText("SS_VDD"))
    domain_core = view.create_power_domain(core_box, "PD_CORE", dialog.color, dialog.transparency, dialog.primary_supply_set_id, dialog.secondary_supply_set_ids, dialog.new_supply_sets)
    assert any(p.name == "VDD1" and p.owner_path == "top/u_core" for p in view._shared_state.supply_port_placements.values())

    # Remove from domain_core first (survives, per (d) above).
    view.edit_power_domain(domain_core.id, "PD_CORE", dialog.color, dialog.transparency, None, [], {})
    assert "ss_top" in view._domain_state.supply_sets

    # Now remove from domain_top too -- the LAST reference.
    view.edit_power_domain(domain_top.id, "PD_TOP", QColor("#4caf50"), 0.5, None, [], {})

    assert "ss_top" not in view._domain_state.supply_sets  # fully deleted
    # The ORIGINAL top-level "VDD1" is cleaned up (domain_top no longer needs it).
    assert not any(p.name == "VDD1" and p.owner_path == "top" for p in view._shared_state.supply_port_placements.values())
    # The auto-linked COPY on "top/u_core" is a separate, independent entity -- persists.
    assert any(p.name == "VDD1" and p.owner_path == "top/u_core" for p in view._shared_state.supply_port_placements.values())
    _deselect_and_collect(tab)


def test_removing_the_power_domain_entirely_preserves_a_shared_supply_set(qtbot, monkeypatch):
    """Same (d)/(e) semantics, but via full "Remove Power Domain" (not
    Edit-remove-from-list) -- exercises _remove_domain_cascade()'s own
    CHANGE 2 fix directly. See the test above for why the auto-linked
    "top/u_core" copy of "VDD1" (materialized by the auto-link feature
    extension) persists as an independent entity even once "ss_top"
    itself is fully deleted."""
    tab, view = _tab_and_view(qtbot)
    top_box = view._boxes["top"]
    core_box = view._boxes["top/u_core"]
    ss_top = SupplySetPlacement(id="ss_top", name="SS_VDD", power="VDD1", owner_path="top")
    view.create_power_domain(top_box, "PD_TOP", QColor("#4caf50"), 0.5, "ss_top", [], {"ss_top": ss_top}, {"VDD1": QColor("#ff0000")})
    _accept_with_primary(monkeypatch, True)
    dialog = _domain_dialog(view, "top/u_core")
    dialog.supply_set_combo.setCurrentIndex(dialog.supply_set_combo.findText("SS_VDD"))
    domain_core = view.create_power_domain(core_box, "PD_CORE", dialog.color, dialog.transparency, dialog.primary_supply_set_id, dialog.secondary_supply_set_ids, dialog.new_supply_sets)

    # Remove domain_core entirely -- ss_top must survive (still domain_top's own Primary).
    view.remove_power_domain(core_box)

    assert "ss_top" in view._domain_state.supply_sets
    assert any(p.name == "VDD1" and p.owner_path == "top" for p in view._shared_state.supply_port_placements.values())

    # Now remove domain_top too -- the LAST reference -- full cleanup of the record.
    view.remove_power_domain(top_box)

    assert "ss_top" not in view._domain_state.supply_sets
    assert not any(p.name == "VDD1" and p.owner_path == "top" for p in view._shared_state.supply_port_placements.values())
    _deselect_and_collect(tab)


# --- (f) doesn't interfere with multi-hierarchy Power Domain replication --------


def test_ancestor_sharing_does_not_interfere_with_multi_hierarchy_replication(qtbot, monkeypatch):
    tab, view = _tab_and_view(qtbot)
    top_box = view._boxes["top"]
    core_box = view._boxes["top/u_core"]
    dbg_box = view._boxes["top/u_dbg"]

    # A genuinely SEPARATE multi-hierarchy Power Domain: ONE domain spanning
    # two member hierarchies, each auto-getting its OWN replicated Supply
    # Port under the same name -- unrelated to ancestor sharing.
    ss_multi = SupplySetPlacement(id="ss_multi", name="SS_MULTI", power="VDD_MULTI", owner_path="top/u_core")
    domain_multi = view.create_power_domain(
        core_box, "PD_MULTI", QColor("#9c27b0"), 0.5, "ss_multi", [], {"ss_multi": ss_multi}, {"VDD_MULTI": QColor("#ff9800")}, additional_boxes=[dbg_box]
    )
    replicated_names = {p.name for p in view._shared_state.supply_port_placements.values() if p.owner_path in ("top/u_core", "top/u_dbg")}
    assert "VDD_MULTI" in replicated_names
    multi_ports = [p for p in view._shared_state.supply_port_placements.values() if p.name == "VDD_MULTI"]
    assert {p.owner_path for p in multi_ports} == {"top/u_core", "top/u_dbg"}  # replicated onto BOTH member hierarchies
    assert len({p.id for p in multi_ports}) == 2  # two DISTINCT placements, not shared

    # ANCESTOR sharing, on top: a domain at "top" shares an EXISTING
    # ancestor... but "top" has no ancestor, so instead confirm the
    # reverse direction -- "top/u_core"'s own domain can share a Supply
    # Set from "top", while the multi-hierarchy replication above
    # remains completely untouched.
    ss_top = SupplySetPlacement(id="ss_top", name="SS_TOP", power="VDD_TOP", owner_path="top")
    view.create_power_domain(top_box, "PD_TOP", QColor("#4caf50"), 0.5, "ss_top", [], {"ss_top": ss_top}, {"VDD_TOP": QColor("#ff0000")})

    dialog = _domain_dialog(view, "top/u_core", editing_domain_id=domain_multi.id, initial=view._domain_state.domains[domain_multi.id])
    idx = dialog.supply_set_combo.findText("SS_TOP")
    assert idx > 0
    _accept_with_primary(monkeypatch, False)  # domain_multi already has a Primary (ss_multi)
    dialog.supply_set_combo.setCurrentIndex(idx)
    assert "ss_top" in dialog.secondary_supply_set_ids

    view.edit_power_domain(domain_multi.id, "PD_MULTI", dialog.color, dialog.transparency, dialog.primary_supply_set_id, dialog.secondary_supply_set_ids, dialog.new_supply_sets)

    # FEATURE EXTENSION (post-dates this test's own original assertion --
    # updated to match): the shared "ss_top" Supply Set's own "VDD_TOP"
    # pin IS now auto-materialized+linked, but onto EVERY one of
    # domain_multi's own `owner_paths` -- both "top/u_core" AND
    # "top/u_dbg" -- since domain_multi is ITSELF a multi-hierarchy
    # domain (see _auto_link_domains_supply_sets()'s own `target_owner_paths=domain.owner_paths`).
    # The Supply Set's own `owner_paths` (still just ["top"]) is
    # unmodified either way -- this composes correctly with, and is
    # genuinely distinct from, multi-hierarchy REPLICATION below.
    vdd_top_ports = [p for p in view._shared_state.supply_port_placements.values() if p.name == "VDD_TOP"]
    assert {p.owner_path for p in vdd_top_ports} == {"top", "top/u_core", "top/u_dbg"}
    assert len({p.id for p in vdd_top_ports}) == 3  # two DISTINCT auto-linked copies, plus the original
    top_source = next(p for p in vdd_top_ports if p.owner_path == "top")
    for owner_path in ("top/u_core", "top/u_dbg"):
        copy = next(p for p in vdd_top_ports if p.owner_path == owner_path)
        connector = next((a, b) for a, b in view._shared_state.supply_connector_placements if copy.id in (a, b))
        assert top_source.id in connector  # each copy auto-linked back to the real ancestor source

    # The PRE-EXISTING multi-hierarchy replication (a totally separate,
    # unrelated Supply Set) is completely untouched -- still exactly 2
    # distinct "VDD_MULTI" ports.
    multi_ports_after = [p for p in view._shared_state.supply_port_placements.values() if p.name == "VDD_MULTI"]
    assert len(multi_ports_after) == 2
    assert {p.owner_path for p in multi_ports_after} == {"top/u_core", "top/u_dbg"}
    _deselect_and_collect(tab)


# --- downstream systems: Power View tree + export never duplicate a shared set --


def test_power_view_tree_shows_the_shared_supply_set_under_both_domain_nodes(qtbot, monkeypatch):
    tab, view = _tab_and_view(qtbot)
    top_box = view._boxes["top"]
    core_box = view._boxes["top/u_core"]
    ss_top = SupplySetPlacement(id="ss_top", name="SS_VDD", power="VDD1", owner_path="top")
    view.create_power_domain(top_box, "PD_TOP", QColor("#4caf50"), 0.5, "ss_top", [], {"ss_top": ss_top}, {"VDD1": QColor("#ff0000")})
    _accept_with_primary(monkeypatch, True)
    dialog = _domain_dialog(view, "top/u_core")
    dialog.supply_set_combo.setCurrentIndex(dialog.supply_set_combo.findText("SS_VDD"))
    view.create_power_domain(core_box, "PD_CORE", dialog.color, dialog.transparency, dialog.primary_supply_set_id, dialog.secondary_supply_set_ids, dialog.new_supply_sets)

    nodes = build_power_view_tree(view._boxes, view._domain_state, view._retention_state, view._isolation_state, view._power_switch_state, view._supply_port_icons)

    def find_supply_set_children(node_list, domain_label):
        for node in node_list:
            if node.kind == "PowerDomain" and node.label == domain_label:
                return [c for c in node.children if c.kind == "SupplySet"]
            found = find_supply_set_children(node.children, domain_label)
            if found is not None:
                return found
        return None

    top_children = find_supply_set_children(nodes, "PD_TOP")
    core_children = find_supply_set_children(nodes, "PD_CORE")
    assert top_children is not None and len(top_children) == 1
    assert core_children is not None and len(core_children) == 1
    # Same underlying construct_id (the SAME shared record) under BOTH domain nodes.
    assert top_children[0].construct_id == core_children[0].construct_id == "ss_top"
    assert top_children[0].label == core_children[0].label == "SS_VDD"
    _deselect_and_collect(tab)


def test_upf_export_emits_the_shared_supply_set_exactly_once(qtbot, monkeypatch):
    tab, view = _tab_and_view(qtbot)
    top_box = view._boxes["top"]
    core_box = view._boxes["top/u_core"]
    ss_top = SupplySetPlacement(id="ss_top", name="SS_VDD", power="VDD1", owner_path="top")
    view.create_power_domain(top_box, "PD_TOP", QColor("#4caf50"), 0.5, "ss_top", [], {"ss_top": ss_top}, {"VDD1": QColor("#ff0000")})
    _accept_with_primary(monkeypatch, True)
    dialog = _domain_dialog(view, "top/u_core")
    dialog.supply_set_combo.setCurrentIndex(dialog.supply_set_combo.findText("SS_VDD"))
    view.create_power_domain(core_box, "PD_CORE", dialog.color, dialog.transparency, dialog.primary_supply_set_id, dialog.secondary_supply_set_ids, dialog.new_supply_sets)

    text = generate_upf(view._shared_state, view._domain_state, view._retention_state, "3.1")

    assert text.count("create_supply_set SS_VDD") == 1  # exactly once, not per-domain
    # Both PD_TOP and PD_CORE reference it as their own -primary_power_net-equivalent.
    assert text.count("SS_VDD") >= 3  # 1 create_supply_set + at least 2 create_power_domain references
    _deselect_and_collect(tab)


# --- FEATURE EXTENSION: sharing an ancestor's Supply Set instantiates real --
# --- Internal Supplies on the new hierarchy and auto-links reused pins -----


def _connected_names(view, placement_id: str) -> list[str]:
    names = []
    for a, b in view._shared_state.supply_connector_placements:
        other_id = b if a == placement_id else (a if b == placement_id else None)
        if other_id is None:
            continue
        other = view._shared_state.supply_port_placements.get(other_id)
        if other is not None:
            names.append(other.name)
    return names


def test_worked_example_exactly_3_internal_supplies_with_correct_auto_links(qtbot, monkeypatch):
    """The exact golden worked example: PD_TOP/SS_VDD (Power=VDD,
    Ground=VSS) at the top level; PD_SUB at "top/u_core" with
    Secondary=SS_VDD (shared, via the ancestor dropdown) and
    Primary=SS_VDD_INT (new: Power=VDD_INT, Ground=VSS -- reusing the
    SAME ground name as the shared Secondary, within this SAME new
    domain). Exactly 3 Internal Supplies must be created on
    "top/u_core" (VDD_INT, VDD, VSS -- VSS instantiated ONCE, not
    duplicated); VDD and VSS must each have a real auto-created power
    net to their own top-level counterpart; VDD_INT must have none."""
    tab, view = _tab_and_view(qtbot)
    top_box = view._boxes["top"]
    core_box = view._boxes["top/u_core"]
    ss_top = SupplySetPlacement(id="ss_top", name="SS_VDD", power="VDD", ground="VSS", owner_path="top")
    view.create_power_domain(top_box, "PD_TOP", QColor("#4caf50"), 0.5, "ss_top", [], {"ss_top": ss_top}, {"VDD": QColor("#ff0000"), "VSS": QColor("#0000ff")})

    # Select the ancestor's SS_VDD via the real dropdown -- becomes Secondary
    # (Primary is reserved for the brand-new SS_VDD_INT composed below).
    _accept_with_primary(monkeypatch, False)
    dialog = _domain_dialog(view, "top/u_core")
    dialog.supply_set_combo.setCurrentIndex(dialog.supply_set_combo.findText("SS_VDD"))
    assert dialog.secondary_supply_set_ids == ["ss_top"]
    assert dialog.primary_supply_set_id is None

    # Compose the brand-new SS_VDD_INT as Primary -- same "directly-testable
    # core" precedent this whole test suite already follows for dialog state
    # (bypassing the nested Create-Supply-Set/Add-Power-Supply dialog chain).
    ss_vdd_int = SupplySetPlacement(id="ss_vdd_int", name="SS_VDD_INT", power="VDD_INT", ground="VSS", owner_path="top/u_core")
    dialog.new_supply_sets[ss_vdd_int.id] = ss_vdd_int
    dialog.new_supply_ports["VDD_INT"] = QColor("#00aa00")
    dialog._primary_id = ss_vdd_int.id

    domain_sub = view.create_power_domain(
        core_box, "PD_SUB", dialog.color, dialog.transparency, dialog.primary_supply_set_id, dialog.secondary_supply_set_ids, dialog.new_supply_sets, dialog.new_supply_ports
    )

    assert domain_sub.primary_supply_set == "ss_vdd_int"
    assert domain_sub.secondary_supply_sets == ["ss_top"]

    sub_placements = {p.name: p for p in view._shared_state.supply_port_placements.values() if p.owner_path == "top/u_core"}
    assert set(sub_placements) == {"VDD_INT", "VDD", "VSS"}  # exactly 3, no duplicate VSS
    assert all(p.is_internal_supply for p in sub_placements.values())

    assert _connected_names(view, sub_placements["VDD"].id) == ["VDD"]
    assert _connected_names(view, sub_placements["VSS"].id) == ["VSS"]
    assert _connected_names(view, sub_placements["VDD_INT"].id) == []  # brand-new pin, nothing to link to

    # The connected "VDD"/"VSS" names resolve to the real TOP-level Supply Ports.
    top_vdd = next(p for p in view._shared_state.supply_port_placements.values() if p.name == "VDD" and p.owner_path == "top")
    top_vss = next(p for p in view._shared_state.supply_port_placements.values() if p.name == "VSS" and p.owner_path == "top")
    vdd_connector = next((a, b) for a, b in view._shared_state.supply_connector_placements if sub_placements["VDD"].id in (a, b))
    assert top_vdd.id in vdd_connector
    vss_connector = next((a, b) for a, b in view._shared_state.supply_connector_placements if sub_placements["VSS"].id in (a, b))
    assert top_vss.id in vss_connector
    _deselect_and_collect(tab)


def test_auto_linked_pin_from_shared_supply_set_rejects_a_power_switch_until_renamed(qtbot, monkeypatch):
    """Composes correctly with the existing name-continuity Power-
    Switch rule (CHANGE 1): the sub hierarchy's auto-linked "VDD"
    Internal Supply (same name as its own upstream "top"-level source,
    exactly as if the auto-link had been drawn manually) is correctly
    rejected -- and renaming it to a unique name resolves the
    rejection, exactly as it would for a manually-created auto-link."""
    tab, view = _tab_and_view(qtbot)
    top_box = view._boxes["top"]
    core_box = view._boxes["top/u_core"]
    ss_top = SupplySetPlacement(id="ss_top", name="SS_VDD", power="VDD", ground="VSS", owner_path="top")
    view.create_power_domain(top_box, "PD_TOP", QColor("#4caf50"), 0.5, "ss_top", [], {"ss_top": ss_top}, {"VDD": QColor("#ff0000"), "VSS": QColor("#0000ff")})

    _accept_with_primary(monkeypatch, True)
    dialog = _domain_dialog(view, "top/u_core")
    dialog.supply_set_combo.setCurrentIndex(dialog.supply_set_combo.findText("SS_VDD"))
    view.create_power_domain(core_box, "PD_SUB", dialog.color, dialog.transparency, dialog.primary_supply_set_id, dialog.secondary_supply_set_ids, dialog.new_supply_sets)

    vdd_sub = next(p for p in view._shared_state.supply_port_placements.values() if p.name == "VDD" and p.owner_path == "top/u_core")
    vdd_sub_icon = view._supply_port_icons[vdd_sub.id]

    error = view._power_switch_add_error(vdd_sub_icon)
    assert error is not None
    assert "shares its name with its own upstream" in error.lower()

    view.edit_supply_port(vdd_sub_icon, "VDD_SUB_UNIQUE", vdd_sub_icon.color)

    assert view._power_switch_add_error(vdd_sub_icon) is None
    switch = view.add_power_switch(vdd_sub_icon, "SW1", "in", "out", "ctrl", None, "on", "off")
    assert switch is not None
    _deselect_and_collect(tab)


def test_sharing_an_ancestor_supply_set_with_no_new_pins_still_auto_links_every_role(qtbot, monkeypatch):
    """General-rule coverage beyond the worked example's own Power/
    Ground-only pair: N-Well and P-Well pins belonging to the shared
    Supply Set are auto-linked too, and a domain that shares WITHOUT
    composing any brand-new Supply Set of its own still gets every one
    of the shared Supply Set's pins correctly materialized+linked."""
    tab, view = _tab_and_view(qtbot)
    top_box = view._boxes["top"]
    core_box = view._boxes["top/u_core"]
    ss_top = SupplySetPlacement(id="ss_top", name="SS_VDD", power="VDD", ground="VSS", n_well="VDD", p_well="VSS", owner_path="top")
    view.create_power_domain(
        top_box, "PD_TOP", QColor("#4caf50"), 0.5, "ss_top", [], {"ss_top": ss_top}, {"VDD": QColor("#ff0000"), "VSS": QColor("#0000ff")}
    )

    _accept_with_primary(monkeypatch, True)
    dialog = _domain_dialog(view, "top/u_core")
    dialog.supply_set_combo.setCurrentIndex(dialog.supply_set_combo.findText("SS_VDD"))
    view.create_power_domain(core_box, "PD_SUB", dialog.color, dialog.transparency, dialog.primary_supply_set_id, dialog.secondary_supply_set_ids, dialog.new_supply_sets)

    sub_placements = {p.name: p for p in view._shared_state.supply_port_placements.values() if p.owner_path == "top/u_core"}
    # N-Well/P-Well reuse the SAME names as Power/Ground here (a real,
    # legal design convention) -- so still exactly 2 distinct Internal
    # Supplies, both auto-linked.
    assert set(sub_placements) == {"VDD", "VSS"}
    assert _connected_names(view, sub_placements["VDD"].id) == ["VDD"]
    assert _connected_names(view, sub_placements["VSS"].id) == ["VSS"]
    _deselect_and_collect(tab)
