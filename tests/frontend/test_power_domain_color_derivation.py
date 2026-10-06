"""Power Domain colour is no longer user-chosen (explicit request,
superseding the earlier manual colour-picker/transparency-spinbox
design): it's LIVE-derived from the domain's own Primary Supply Set's
Power rail colour, at a FIXED 80% transparency -- the same live-color-
binding pattern already established for Isolation/Power Switch/
Retention, reused here via power_domain_overlay.py's own
resolve_domain_color()/DOMAIN_FIXED_TRANSPARENCY. Must propagate to all
three rendering locations: the diagram's box fill, the domain name
label, and the Power View tree node colour.
"""

from PySide6.QtGui import QColor

from frontend.src.power_intent_view.create_power_domain_dialog import CreatePowerDomainDialog
from frontend.src.power_intent_view.power_domain_overlay import DOMAIN_FIXED_TRANSPARENCY, SupplySetPlacement
from frontend.src.power_intent_view.power_intent_tab import PowerIntentTab
from frontend.src.shared.mock_backend import build_mock_design


# --- (a): Create Power Domain dialog no longer shows a colour-picker field ---


def test_create_power_domain_dialog_has_no_color_picker_or_transparency_field(qtbot):
    dialog = CreatePowerDomainDialog({}, ["VDD"], {}, "top")
    qtbot.addWidget(dialog)

    assert not hasattr(dialog, "color_button")
    assert not hasattr(dialog, "transparency_spin")


def test_create_power_domain_dialog_shows_no_color_trace_at_all(qtbot):
    """Explicit follow-up bug fix: supersedes an earlier version that
    kept a read-only "Colour (from Primary Supply Set)" preview row --
    the dialog now shows NOTHING about color at all, not even a
    read-only note. The derivation itself still happens, entirely
    behind the scenes."""
    dialog = CreatePowerDomainDialog({}, ["VDD"], {}, "top")
    qtbot.addWidget(dialog)

    from PySide6.QtWidgets import QLabel

    assert not hasattr(dialog, "color_preview_label")
    assert not any("colour" in label.text().lower() for label in dialog.findChildren(QLabel))
    assert dialog.transparency == DOMAIN_FIXED_TRANSPARENCY


# --- (b): created domain's color matches its Primary Supply Set's color -----


def test_created_domains_color_matches_its_primary_supply_sets_color_at_fixed_transparency(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top/u_core"]
    view.add_supply_port(view._boxes["top"], "VDD", QColor("#ff0000"))
    new_set = SupplySetPlacement(id="s1", name="SS1", power="VDD", owner_path="top")

    domain = view.create_power_domain(box, "PD_CORE", QColor("#000000"), 0.5, "s1", [], {"s1": new_set})

    assert domain.color.name() == "#ff0000"
    assert domain.transparency == DOMAIN_FIXED_TRANSPARENCY
    # Diagram fill
    assert box.brush().color().name() == "#ff0000"
    assert box.brush().color().alpha() == round((1 - DOMAIN_FIXED_TRANSPARENCY) * 255)
    # Merged info box -- name line and Primary line both, same color
    info_box_items = view._domain_info_boxes[domain.id]
    assert any(color.name() == "#ff0000" for item in info_box_items for _text, color in item._rows)
    # Power View tree node
    tree_item = tab.power_view_tree.topLevelItem(0)
    assert tree_item.foreground(0).color().name() == "#ff0000"


# --- (c): changing the Primary Supply Set's color later live-updates all three --


def test_changing_the_source_supply_ports_color_live_updates_domain_color_everywhere(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top/u_core"]
    port = view.add_supply_port(view._boxes["top"], "VDD", QColor("#ff0000"))
    new_set = SupplySetPlacement(id="s1", name="SS1", power="VDD", owner_path="top")
    domain = view.create_power_domain(box, "PD_CORE", QColor("#000000"), 0.5, "s1", [], {"s1": new_set})
    assert domain.color.name() == "#ff0000"

    view.edit_supply_port(port, "VDD", QColor("#0000ff"))

    assert view._domain_state.domains[domain.id].color.name() == "#0000ff"
    assert box.brush().color().name() == "#0000ff"
    info_box_items = view._domain_info_boxes[domain.id]
    assert any(color.name() == "#0000ff" for item in info_box_items for _text, color in item._rows)
    tree_item = tab.power_view_tree.topLevelItem(0)
    assert tree_item.foreground(0).color().name() == "#0000ff"


def test_a_domain_with_no_primary_supply_set_keeps_its_own_fallback_color(qtbot):
    """Not one of the 3 explicitly-requested tests, but locks in the
    backward-compatible fallback behavior resolve_domain_color() relies
    on: a domain with no Primary Supply Set (a real, valid state) simply
    keeps whatever color it already had rather than snapping to one
    fixed default."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view

    domain = view.create_power_domain(view._boxes["top/u_core"], "PD_CORE", QColor("#4caf50"), 0.5, None, [], {})

    assert domain.color.name() == "#4caf50"
