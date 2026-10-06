"""PERMANENT regression suite for QGraphicsScene item lifecycle --
specifically, callbacks an overlay item registers on a LONGER-LIVED
object (a supply-port icon's colour list, a hierarchy box's move/resize
lists) and must detach again when the item is discarded.

This class of defect is invisible in normal use and in every
functional test: the diagram looks correct, nothing raises, and the
stale entry only bites later -- when the still-live object fires its
callbacks and reaches an item that was removed from the scene, or when
the entries simply accumulate across a long session.

The health audit found two real instances by COUNTING the callback
lists rather than by reading the code, which is what these tests do:

- IsolationBadgeIconItem registered `sync_color` on its colour source
  and `_clear_isolation_icons()` never removed it. Retention's own
  equivalent did remove it -- and its code even carried a comment
  naming this exact hazard -- so the two were measurably asymmetric.
- SupplyPortIconItem registers on BOTH of its owner box's lists
  (on_resize for resnap_to_owner_edge, on_move for _on_owner_box_moved)
  but `delete_supply_port()` detached only the resize one.

Counting before/after is deliberate: asserting "the list is empty"
would break the moment some unrelated feature legitimately registers a
callback of its own, whereas "ends where it started" stays true.
"""

from PySide6.QtGui import QColor

from frontend.src.power_intent_view.power_domain_overlay import SupplySetPlacement
from frontend.src.power_intent_view.power_intent_tab import PowerIntentTab
from frontend.src.shared.mock_backend import build_mock_design


def _tab_and_view(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    return tab, tab.diagram._view


def _domain_with_supply(view, box):
    icon = view.add_supply_port(box, "VDD1", QColor("#ff0000"))
    domain = view.create_power_domain(
        box, "PD1", QColor("#4caf50"), 0.5, "ss1", [],
        {"ss1": SupplySetPlacement(id="ss1", name="SS", power="VDD1", owner_path="top")},
    )
    return icon, domain


# --- colour-source callbacks ------------------------------------------------


def test_deleting_an_isolation_detaches_its_colour_callback(qtbot):
    tab, view = _tab_and_view(qtbot)
    box = view._boxes["top"]
    icon, domain = _domain_with_supply(view, box)
    before = len(view._supply_port_icons[icon.key].on_recolor_callbacks)

    strategy = view.create_isolation(box, "ISO1", domain.id, "ss1", None, None, "low", "self", None, "high", ["top/clk"])
    assert len(view._supply_port_icons[icon.key].on_recolor_callbacks) == before + 1  # it really did register one

    view.delete_isolation(strategy.id)
    assert len(view._supply_port_icons[icon.key].on_recolor_callbacks) == before


def test_deleting_a_retention_detaches_its_colour_callback(qtbot):
    """The contrast case -- Retention was already correct. Kept so the
    two stay verifiably symmetric rather than one silently regressing."""
    tab, view = _tab_and_view(qtbot)
    box = view._boxes["top"]
    icon, domain = _domain_with_supply(view, box)
    before = len(view._supply_port_icons[icon.key].on_recolor_callbacks)

    strategy = view.create_retention_strategy(box, domain, "RET1", "ss1", "ss1", "s", "r", ["top/u_core/out1"], {})
    assert len(view._supply_port_icons[icon.key].on_recolor_callbacks) == before + 1

    view.delete_retention_strategy(strategy.id)
    assert len(view._supply_port_icons[icon.key].on_recolor_callbacks) == before


def test_repeated_isolation_create_delete_cycles_do_not_accumulate_callbacks(qtbot):
    """The accumulation half of the leak: _clear_isolation_icons() also
    runs on edit and on domain-colour refresh, neither of which rebuilds
    the supply icons, so stale entries piled up on a live icon across a
    session instead of being swept away by a rebuild."""
    tab, view = _tab_and_view(qtbot)
    box = view._boxes["top"]
    icon, domain = _domain_with_supply(view, box)
    before = len(view._supply_port_icons[icon.key].on_recolor_callbacks)

    for i in range(5):
        strategy = view.create_isolation(box, f"ISO{i}", domain.id, "ss1", None, None, "low", "self", None, "high", ["top/clk"])
        view.delete_isolation(strategy.id)

    assert len(view._supply_port_icons[icon.key].on_recolor_callbacks) == before


# --- owner-box callbacks ----------------------------------------------------


def test_deleting_a_supply_port_detaches_both_of_its_owner_box_callbacks(qtbot):
    tab, view = _tab_and_view(qtbot)
    box = view._boxes["top"]
    resize_before = len(box.on_resize_callbacks)
    move_before = len(box.on_move_callbacks)

    icon = view.add_supply_port(box, "VDD1", QColor("#ff0000"))
    live = view._supply_port_icons[icon.key]
    assert len(live.owner_box.on_resize_callbacks) == resize_before + 1
    assert len(live.owner_box.on_move_callbacks) == move_before + 1

    view.delete_supply_port(live)

    assert len(view._boxes["top"].on_resize_callbacks) == resize_before
    assert len(view._boxes["top"].on_move_callbacks) == move_before


def test_repeated_supply_port_add_delete_cycles_do_not_accumulate_callbacks(qtbot):
    tab, view = _tab_and_view(qtbot)
    box = view._boxes["top"]
    move_before = len(box.on_move_callbacks)

    for i in range(5):
        icon = view.add_supply_port(view._boxes["top"], f"V{i}", QColor("#ff0000"))
        view.delete_supply_port(view._supply_port_icons[icon.key])

    assert len(view._boxes["top"].on_move_callbacks) == move_before
