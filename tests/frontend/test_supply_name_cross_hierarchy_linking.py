"""Bug 9 (explicit request, highest-risk item of this batch): a Supply
Port/Internal Supply name (e.g. a shared Ground rail "VSS") can be
LEGITIMATELY reused across two UNRELATED Power Domains at different
hierarchy levels -- e.g. a top-level "VSS" and an inner block's own,
independent "VSS".

CHANGE 1 (explicit follow-up request): the global/local Supply Port/
Internal Supply name-uniqueness rule this file originally existed to
carve an exception INTO (_check_unique_name_globally(), item 11/12)
has been REMOVED entirely -- two same-named placements anywhere in the
design are now unconditionally allowed, with no exception logic
needed at all. Parts (a)/(b) below (the whole-design dropdown pool and
its optional auto-link/auto-connect convenience) are UNCHANGED by
CHANGE 1 -- only their surrounding PURPOSE changed, from "the only way
to avoid a rejected collision" to "an optional convenience."

Part (c) is RE-SCOPED, refined after a follow-up clarification (an
earlier pure-CONNECTIVITY draft of this rule broke item 8's own
"adopt an existing incoming connection as Supply In" feature, since
adding a switch onto an already-connected Internal Supply is the
NORMAL, expected topology, not a conflict). The actual rule is about
NAME CONTINUITY: a power net can legitimately stay one continuous,
same-named net across multiple Power Domains (exactly what CHANGE 1 +
the auto-link mechanism enable); a Power Switch DOES interrupt that
continuity (its output side isn't guaranteed to equal its input side),
so it's rejected specifically when the target's own name EQUALS its
upstream connection's name -- see
_power_switch_add_error()/_supply_in_name_conflict_owner()'s own
docstrings.

Four parts, all covered here:
  (a) The Power/Ground/N-Well/P-Well pickers in Create/Edit Power Domain
      (and Create/Edit Retention, which shares the same inline Supply
      Set flow) now offer every plain Supply Port/Internal Supply
      DESIGN-WIDE, excluding macro PG pins -- see
      supply_port_overlay.known_supply_names_globally()'s own docstring.
  (b) Selecting an EXISTING name from that pool that isn't already local
      to the hierarchy being composed on auto-creates a new Internal
      Supply there under the SAME name, and auto-connects it to the
      found source via a REAL power net -- reusing
      try_connect_supply_ports() verbatim (no special-casing) -- see
      PowerIntentDiagramView._auto_link_cross_hierarchy_supply_sets()'s
      own docstring.
  (c) A Power Switch can't attach to an Internal Supply whose own name
      EQUALS the name of whatever it's connected to (its own would-be
      Supply In) -- see
      _power_switch_add_error()/_supply_in_name_conflict_owner().
      Renaming to a genuinely different name lifts the rejection (a
      rename still propagates to every Supply Set that referenced the
      old name -- pre-existing _propagate_supply_port_rename(),
      unrelated to this bug but confirmed to compose correctly with
      it), and a rename PRESERVES the net (renaming never touches
      connector identity) -- item 8's own Supply-In-adoption still
      correctly re-routes that SAME connector through the new switch's
      own top anchor afterward. An Internal Supply with NO upstream
      connection, or one whose name already differs, is never
      restricted at all.
"""

import gc

from PySide6.QtGui import QColor

from frontend.src.power_intent_view.power_domain_overlay import SupplySetPlacement
from frontend.src.power_intent_view.power_intent_tab import PowerIntentTab
from frontend.src.power_intent_view.supply_port_overlay import known_supply_names_globally
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


def _vss(view, owner_path):
    return next(p for p in view._shared_state.supply_port_placements.values() if p.name == "VSS" and p.owner_path == owner_path)


# --- (a) whole-design dropdown scope, all 4 roles -------------------------------


def test_dropdown_pool_is_whole_design_and_excludes_macro_pg_pins_for_all_4_roles(qtbot):
    tab, view = _tab_and_view(qtbot)
    top_box = view._boxes["top"]
    view.add_supply_port(top_box, "VDD_TOP", QColor("#ff0000"))
    view.add_supply_port(top_box, "VSS_TOP", QColor("#0000ff"))
    core_box = view._boxes["top/u_core"]
    view.add_internal_supply(core_box, "VDD_CORE_NW", QColor("#00ff00"))
    view.add_internal_supply(core_box, "VSS_CORE_PW", QColor("#000000"))

    pool = known_supply_names_globally(view._shared_state)

    # All 4 roles' worth of names, from DIFFERENT hierarchies, all present.
    assert {"VDD_TOP", "VSS_TOP", "VDD_CORE_NW", "VSS_CORE_PW"} <= set(pool)
    # Macro PG pins (VDD/VSS on top/u_ram, mock design) are excluded --
    # this pool is built ONLY from supply_port_placements, never
    # pg_pin_placements (see known_supply_names_globally()'s own
    # docstring), confirmed by construction, not just by absence of a
    # name collision here.
    assert view._shared_state.pg_pin_placements  # sanity: the mock design really does have macro PG pins to exclude
    assert set(view._shared_state.pg_pin_placements.keys()).isdisjoint(view._shared_state.supply_port_placements.keys())
    _deselect_and_collect(tab)


# --- (b) auto-link + auto-net ----------------------------------------------------


def test_selecting_an_existing_name_from_an_unrelated_hierarchy_auto_links_and_connects(qtbot):
    tab, view = _tab_and_view(qtbot)
    top_box = view._boxes["top"]
    core_box = view._boxes["top/u_core"]

    ss_top = SupplySetPlacement(id="ss_top", name="SS_TOP", power="VDD_TOP", ground="VSS", owner_path="top")
    view.create_power_domain(top_box, "PD_TOP", QColor("#4caf50"), 0.5, "ss_top", [], {"ss_top": ss_top}, {"VDD_TOP": QColor("#ff0000"), "VSS": QColor("#0000ff")})

    # Domain B, composed on an INNER hierarchy, reuses "VSS" -- selected from the
    # whole-design pool (a3), not typed as "Add New..." -- no explicit color given for it.
    ss_core = SupplySetPlacement(id="ss_core", name="SS_CORE", power="VDD_CORE", ground="VSS", owner_path="top/u_core")
    view.create_power_domain(core_box, "PD_CORE", QColor("#2196f3"), 0.5, "ss_core", [], {"ss_core": ss_core}, {"VDD_CORE": QColor("#00ff00")})

    vss_top = _vss(view, "top")
    vss_core = _vss(view, "top/u_core")
    assert vss_core.is_internal_supply  # composed on an inner hierarchy -- Internal Supply, not a plain Supply Port
    assert vss_core.color == vss_top.color  # inherits the source's own colour

    icon_top = view._supply_port_icons[vss_top.id]
    icon_core = view._supply_port_icons[vss_core.id]
    assert len(icon_top.connectors) == 1
    assert len(icon_core.connectors) == 1
    assert icon_top.connectors[0] is icon_core.connectors[0]  # the SAME real connector, an ordinary power net
    _deselect_and_collect(tab)


def test_auto_link_is_undoable(qtbot):
    """The auto-created net goes through try_connect_supply_ports()
    verbatim (explicit request: "no special-casing"), so it gets that
    method's own real undo/redo for free, same as a manually-drawn net."""
    tab, view = _tab_and_view(qtbot)
    top_box = view._boxes["top"]
    core_box = view._boxes["top/u_core"]
    ss_top = SupplySetPlacement(id="ss_top", name="SS_TOP", power="VDD_TOP", ground="VSS", owner_path="top")
    view.create_power_domain(top_box, "PD_TOP", QColor("#4caf50"), 0.5, "ss_top", [], {"ss_top": ss_top}, {"VDD_TOP": QColor("#ff0000"), "VSS": QColor("#0000ff")})
    ss_core = SupplySetPlacement(id="ss_core", name="SS_CORE", power="VDD_CORE", ground="VSS", owner_path="top/u_core")
    view.create_power_domain(core_box, "PD_CORE", QColor("#2196f3"), 0.5, "ss_core", [], {"ss_core": ss_core}, {"VDD_CORE": QColor("#00ff00")})
    assert len(_vss_connectors(view)) == 1

    tab.undo_shortcut.activated.emit()  # undoes the "Connect Power Supply" auto-link step

    assert len(_vss_connectors(view)) == 0
    _deselect_and_collect(tab)


def _vss_connectors(view):
    """Distinct connector OBJECTS touching a "VSS"-named icon -- each
    connector is referenced from BOTH of its own endpoints' own
    `.connectors` lists, so a naive sum of list lengths double-counts
    every real connector."""
    connectors = set()
    for placement in view._shared_state.supply_port_placements.values():
        if placement.name != "VSS":
            continue
        icon = view._supply_port_icons.get(placement.id)
        if icon is not None:
            connectors.update(icon.connectors)
    return connectors


def test_sibling_unrelated_hierarchies_still_link_by_name_but_get_no_net(qtbot):
    """try_connect_supply_ports()'s own PRE-EXISTING is_nested_pair()
    rule is deliberately left unmodified (explicit "no special-casing"
    request) -- two SIBLING hierarchies (neither an ancestor of the
    other) still get the auto-created, same-named Internal Supply each,
    but no net, matching that rule's existing behavior for any other
    non-nested pair."""
    tab, view = _tab_and_view(qtbot)
    core_box = view._boxes["top/u_core"]
    dbg_box = view._boxes["top/u_dbg"]
    ss_core = SupplySetPlacement(id="ss_core", name="SS_CORE", power="VDD_CORE", ground="VSS", owner_path="top/u_core")
    view.create_power_domain(core_box, "PD_CORE", QColor("#4caf50"), 0.5, "ss_core", [], {"ss_core": ss_core}, {"VDD_CORE": QColor("#ff0000"), "VSS": QColor("#0000ff")})

    ss_dbg = SupplySetPlacement(id="ss_dbg", name="SS_DBG", power="VDD_DBG", ground="VSS", owner_path="top/u_dbg")
    view.create_power_domain(dbg_box, "PD_DBG", QColor("#2196f3"), 0.5, "ss_dbg", [], {"ss_dbg": ss_dbg}, {"VDD_DBG": QColor("#00ff00")})

    vss_core = _vss(view, "top/u_core")
    vss_dbg = _vss(view, "top/u_dbg")
    assert vss_core is not None and vss_dbg is not None
    assert len(view._supply_port_icons[vss_core.id].connectors) == 0
    assert len(view._supply_port_icons[vss_dbg.id].connectors) == 0
    _deselect_and_collect(tab)


# --- (c) Power Switch rejection + rename resolves it -----------------------------


def test_power_switch_is_rejected_on_a_name_matching_its_own_upstream_connection(qtbot):
    """CHANGE 1 regression (a): an Internal Supply whose own name
    EQUALS the name of whatever it's connected to (its own would-be
    Supply In) is rejected -- a Power Switch would break the name
    continuity that connection implies."""
    tab, view = _tab_and_view(qtbot)
    top_box = view._boxes["top"]
    core_box = view._boxes["top/u_core"]
    ss_top = SupplySetPlacement(id="ss_top", name="SS_TOP", power="VDD_TOP", ground="VSS", owner_path="top")
    view.create_power_domain(top_box, "PD_TOP", QColor("#4caf50"), 0.5, "ss_top", [], {"ss_top": ss_top}, {"VDD_TOP": QColor("#ff0000"), "VSS": QColor("#0000ff")})
    ss_core = SupplySetPlacement(id="ss_core", name="SS_CORE", power="VDD_CORE", ground="VSS", owner_path="top/u_core")
    view.create_power_domain(core_box, "PD_CORE", QColor("#2196f3"), 0.5, "ss_core", [], {"ss_core": ss_core}, {"VDD_CORE": QColor("#00ff00")})
    icon_core = view._supply_port_icons[_vss(view, "top/u_core").id]

    error = view._power_switch_add_error(icon_core)
    assert error is not None
    assert "VSS" in error
    assert "shares its name with its own upstream" in error.lower()
    assert "rename" in error.lower()

    import pytest

    with pytest.raises(ValueError, match="shares its name"):
        view.add_power_switch(icon_core, "SW1", "in", "out", "ctrl", None, "on", "off")
    _deselect_and_collect(tab)


def test_renaming_to_a_unique_name_resolves_the_conflict_and_preserves_supply_in_adoption(qtbot):
    """CHANGE 1 regression (b): renaming the Internal Supply to a name
    that no longer matches its upstream connection's own name resolves
    the conflict -- Power Switch is now allowed, dependent Supply Set
    references still update on rename (pre-existing, unrelated to
    CHANGE 1), the auto-created net itself is PRESERVED by the rename
    (same connector object, not deleted/rebuilt -- renaming only ever
    touches `.name`, never connector identity), and the existing
    Supply-In-adoption behavior (item 8) still correctly re-routes that
    SAME connector through the new switch's own top anchor."""
    tab, view = _tab_and_view(qtbot)
    top_box = view._boxes["top"]
    core_box = view._boxes["top/u_core"]
    ss_top = SupplySetPlacement(id="ss_top", name="SS_TOP", power="VDD_TOP", ground="VSS", owner_path="top")
    view.create_power_domain(top_box, "PD_TOP", QColor("#4caf50"), 0.5, "ss_top", [], {"ss_top": ss_top}, {"VDD_TOP": QColor("#ff0000"), "VSS": QColor("#0000ff")})
    ss_core = SupplySetPlacement(id="ss_core", name="SS_CORE", power="VDD_CORE", ground="VSS", owner_path="top/u_core")
    view.create_power_domain(core_box, "PD_CORE", QColor("#2196f3"), 0.5, "ss_core", [], {"ss_core": ss_core}, {"VDD_CORE": QColor("#00ff00")})
    icon_core = view._supply_port_icons[_vss(view, "top/u_core").id]
    connector_before = icon_core.connectors[0]
    assert view._power_switch_add_error(icon_core) is not None

    view.edit_supply_port(icon_core, "VSS_CORE_UNIQUE", icon_core.color)

    # Conflict resolved -- Power Switch now allowed.
    assert view._power_switch_add_error(icon_core) is None
    assert icon_core.connectors[0] is connector_before
    assert len(icon_core.connectors) == 1

    # Dependent reference updated: the Supply Set that named the OLD name now names the new one.
    assert view._domain_state.supply_sets["ss_core"].ground == "VSS_CORE_UNIQUE"
    # The unrelated top-level Supply Set (different owner_path) is untouched.
    assert view._domain_state.supply_sets["ss_top"].ground == "VSS"

    switch = view.add_power_switch(icon_core, "SW1", "in", "out", "ctrl", None, "on", "off")
    assert switch is not None
    # Item 8's own Supply-In-adoption still correctly re-routes the SAME
    # pre-existing connector through the new switch's own top anchor.
    switch_icon = view._power_switch_icons[switch.id][0]
    assert connector_before.port_a is switch_icon or connector_before.port_b is switch_icon
    _deselect_and_collect(tab)


def test_a_different_name_from_its_upstream_connection_is_never_restricted(qtbot):
    """CHANGE 1 regression (c): an Internal Supply whose name already
    differs from its upstream connection's own name -- e.g. the normal
    Supply-In-adoption topology (item 8) -- gets NO restriction at all,
    confirming the rule is genuinely orthogonal to whether a connection
    exists, only to whether the NAMES match."""
    tab, view = _tab_and_view(qtbot)
    top_box = view._boxes["top"]
    core_box = view._boxes["top/u_core"]
    upstream_icon = view.add_supply_port(top_box, "VDD_UNSWITCHED", QColor("#0000ff"))
    internal_icon = view.add_internal_supply(core_box, "VDD_INT_SW", QColor("#00aa00"))
    view.create_power_domain(core_box, "PD_CORE", QColor("#2196f3"), 0.5, None, [], {})
    assert view.try_connect_supply_ports(upstream_icon, internal_icon) is True

    assert view._power_switch_add_error(internal_icon) is None
    switch = view.add_power_switch(internal_icon, "SW1", "in", "out", "ctrl", None, "on", "off")
    assert switch is not None
    _deselect_and_collect(tab)


def test_cascaded_switches_check_against_the_immediate_upstream_neighbor_not_the_original_source(qtbot):
    """CHANGE 1 regression (d): Internal Supply A (renamed after Switch
    1 forces the rename) feeds Internal Supply B -- adding Switch 2 on
    B is evaluated against A's OWN (already-renamed) name, not the
    original top-level source's name. Works correctly whether or not
    B's own name happens to match A's."""
    tab, view = _tab_and_view(qtbot)
    top_box = view._boxes["top"]
    core_box = view._boxes["top/u_core"]
    view.create_power_domain(top_box, "PD_TOP", QColor("#4caf50"), 0.5, None, [], {})
    view.create_power_domain(core_box, "PD_CORE", QColor("#2196f3"), 0.5, None, [], {})

    source = view.add_supply_port(top_box, "VDD", QColor("#ff0000"))
    supply_a = view.add_internal_supply(core_box, "VDD", QColor("#ff0000"))  # same name as source, on purpose
    assert view.try_connect_supply_ports(source, supply_a) is True
    assert view._power_switch_add_error(supply_a) is not None  # blocked: name matches its upstream (source)

    view.edit_supply_port(supply_a, "VDD_A", supply_a.color)  # rename resolves it
    switch_1 = view.add_power_switch(supply_a, "SW1", "in", "out", "ctrl", None, "on", "off")
    assert switch_1 is not None

    # B, downstream of (renamed) A -- same box as A is a valid nested
    # pair too (is_nested_pair()'s own same-path case) -- initially
    # shares A's OWN name, so it's blocked against A, not the original
    # "VDD" source's own name.
    supply_b = view.add_internal_supply(core_box, "VDD_A", QColor("#ff0000"))
    assert view.try_connect_supply_ports(supply_a, supply_b) is True
    assert view._power_switch_add_error(supply_b) is not None

    view.edit_supply_port(supply_b, "VDD_B", supply_b.color)
    switch_2 = view.add_power_switch(supply_b, "SW2", "in", "out", "ctrl", None, "on", "off")
    assert switch_2 is not None
    _deselect_and_collect(tab)


def test_two_same_named_unconnected_internal_supplies_each_get_their_own_switch_freely(qtbot):
    """CHANGE 1 regression (e): two same-named, genuinely UNCONNECTED
    Internal Supplies -- no upstream connection to compare names
    against at all -- are each completely free to get their own Power
    Switch, with no restriction."""
    tab, view = _tab_and_view(qtbot)
    core_box = view._boxes["top/u_core"]
    dbg_box = view._boxes["top/u_dbg"]
    view.create_power_domain(core_box, "PD_CORE", QColor("#2196f3"), 0.5, None, [], {})
    view.create_power_domain(dbg_box, "PD_DBG", QColor("#9c27b0"), 0.5, None, [], {})

    icon_core = view.add_internal_supply(core_box, "VDD_SW", QColor("#00aa00"))
    icon_dbg = view.add_internal_supply(dbg_box, "VDD_SW", QColor("#0000ff"))
    assert len(icon_core.connectors) == 0
    assert len(icon_dbg.connectors) == 0

    assert view._power_switch_add_error(icon_core) is None
    assert view._power_switch_add_error(icon_dbg) is None
    switch_core = view.add_power_switch(icon_core, "SW_CORE", "in", "out", "ctrl", None, "on", "off")
    switch_dbg = view.add_power_switch(icon_dbg, "SW_DBG", "in", "out", "ctrl", None, "on", "off")
    assert switch_core is not None and switch_dbg is not None
    _deselect_and_collect(tab)


# --- Bug fix: auto-linked Ground placement wrong on DIRECT creation -------------
# Same "direct insertion vs. undo+redo" diagnostic signature already found once
# in this project (a shared helper -- _add_missing_supply_ports() -- that
# created new icon items directly but never called the required post-creation
# reflow step, unlike its sibling creation methods). Root-caused here (via a
# live diagnostic, not assumed) to a DIFFERENT instance of the same pattern:
# _auto_link_cross_hierarchy_supply_sets()/_auto_link_domains_supply_sets()
# construct their own new SupplyPortPlacement(s) directly (edge="top"
# hardcoded, same "create at top, reclassify after" shape
# _add_missing_supply_ports() already follows) -- but they ran AFTER
# _reclassify_ground_supply_edges() had already had its one and only chance to
# flip a fresh Ground placement to "bottom", so an auto-linked Ground Internal
# Supply stayed silently stuck at "top" until the next full _rebuild() (whose
# OWN, separate one-time reclassification inside build_supply_port_overlay()
# masked the bug on undo+redo). Fixed by re-running
# _reclassify_ground_supply_edges() again after the auto-link step, at all 3
# call sites (create_power_domain(), edit_power_domain(), edit_supply_set()).


def test_auto_linked_ground_internal_supply_is_correctly_placed_on_direct_creation(qtbot):
    """PERMANENT regression test, explicit request: DIRECT creation of
    an auto-linked Ground Internal Supply must show bottom-placement,
    180-degree rotation, and an inside-the-block label immediately --
    with NO undo/redo involved -- and the diagram state after undo+redo
    of that same creation must be IDENTICAL, mirroring the exact
    verification approach already used for the related block-resize-on-
    insertion bug fix."""
    from frontend.src.power_intent_view.supply_port_overlay import _ROTATION_FOR_EDGE

    tab, view = _tab_and_view(qtbot)
    top_box = view._boxes["top"]
    core_box = view._boxes["top/u_core"]

    ss_top = SupplySetPlacement(id="ss_top", name="SS_TOP", power="VDD_TOP", ground="VSS", owner_path="top")
    view.create_power_domain(top_box, "PD_TOP", QColor("#4caf50"), 0.5, "ss_top", [], {"ss_top": ss_top}, {"VDD_TOP": QColor("#ff0000"), "VSS": QColor("#0000ff")})
    ss_core = SupplySetPlacement(id="ss_core", name="SS_CORE", power="VDD_CORE", ground="VSS", owner_path="top/u_core")
    view.create_power_domain(core_box, "PD_CORE", QColor("#2196f3"), 0.5, "ss_core", [], {"ss_core": ss_core}, {"VDD_CORE": QColor("#00ff00")})

    def _snapshot():
        vss_core = next(p for p in view._shared_state.supply_port_placements.values() if p.name == "VSS" and p.owner_path == "top/u_core")
        icon = view._supply_port_icons[vss_core.id]
        return {
            "edge": vss_core.edge,
            "rotation": _ROTATION_FOR_EDGE[vss_core.edge],
            "icon_y": icon.pos().y(),
            "box_bottom": icon.owner_box.rect().bottom(),
            "label_x": icon.label.pos().x(),
            "label_y_is_negative": icon.label.pos().y() < 0,
        }

    direct = _snapshot()

    # Immediately, on direct creation -- no undo/redo involved.
    assert direct["edge"] == "bottom"
    assert direct["rotation"] == 180.0
    assert direct["icon_y"] == direct["box_bottom"]
    assert direct["label_y_is_negative"] is True

    tab.undo_shortcut.activated.emit()
    tab.redo_shortcut.activated.emit()
    after_undo_redo = _snapshot()

    assert after_undo_redo == direct  # IDENTICAL -- direct creation must never differ from undo+redo's own full rebuild
    _deselect_and_collect(tab)


# --- Audit note (category 04): this composition IS already covered ---------
#
# The health audit flagged the Power Switch name-continuity rule and the
# cross-hierarchy auto-link as a standing tension worth "a standing
# combination test matrix", since the two features are designed to
# produce exactly the state the other rejects, and the one real bug from
# that combination (0f72b37) was found by a user report rather than a
# test.
#
# On following it up, the matrix already exists and no new test was
# warranted -- recorded here so the next reader doesn't re-derive it:
#
#   - same-named auto-linked supply REJECTS a switch
#     -> test_power_switch_is_rejected_on_a_name_matching_its_own_upstream_connection (above)
#   - differently-named supply is UNAFFECTED by the rule
#     -> test_a_different_name_from_its_upstream_connection_is_never_restricted (above)
#   - cascaded switches check the immediate neighbour, not the origin
#     -> test_cascaded_switches_check_against_the_immediate_upstream_neighbor_not_the_original_source (above)
#   - all three together (ancestor sharing + auto-link + switch chain),
#     including the empty-states mirror copy that caused 0f72b37
#     -> tests/frontend/test_internal_supply_inheritance.py::
#        test_switch_chain_through_a_change2_shared_supply_net_still_inherits_the_real_sources_states
#
# Two tests written for this audit were deleted on discovering they
# restated the first and last of those. The finding stands as a
# fragility note; it does not stand as a coverage gap.
