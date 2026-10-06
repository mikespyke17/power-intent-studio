"""Bug 2 (explicit request): a Ground-type Supply Port/Internal Supply
(and a macro's own PG ground pin) renders at the BOTTOM of its owner
block, rotated 180 degrees, with its name label positioned INSIDE the
block boundary and horizontally centered -- not at the top, the
uniform default every supply icon used before this fix.

Two independent classification mechanisms (see supply_port_overlay.py's
own module-level docstrings, both added for this bug):
  - A macro PG pin's Ground-ness is derived from Port.related_ground_pin
    (the field a real FUNCTIONAL Liberty pin uses to name which PG pin
    grounds it) -- decided once, at build_supply_port_overlay() time,
    since a macro's own pin geometry is fully known upfront.
  - A plain Supply Port/Internal Supply's Ground-ness is derived from
    Supply Set usage: it renders at the bottom the moment its name is
    used as SOME Supply Set's own `.ground` field (mirroring
    power_intent_text_export.py's own power_names/ground_names split),
    live -- see PowerIntentDiagramView._reclassify_ground_supply_edges().

build_mock_design()'s own ram_macro fixture never populates
related_power_pin/related_ground_pin on its CE/DOUT pins (an existing
gap, not something this bug's own tests should silently rely on/paper
over) -- a small, dedicated macro fixture is built here instead, same
"hand-built fixture when the shared mock doesn't cover what's needed"
precedent already used elsewhere in this suite.
"""

from PySide6.QtGui import QColor

from frontend.src.power_intent_view.power_domain_overlay import SupplySetPlacement
from frontend.src.power_intent_view.power_intent_tab import PowerIntentTab
from frontend.src.power_intent_view.supply_port_overlay import _ROTATION_FOR_EDGE
from frontend.src.shared.mock_backend import build_mock_design
from frontend.src.shared.models.rtl import Design, Definition, Instance, Port, PortDirection, SourceLanguage


def _design_with_macro() -> Design:
    macro = Definition(
        definition_id="ram_macro",
        base_name="RAM_MACRO",
        language=SourceLanguage.LIBERTY,
        is_black_box=True,
        ports=[
            Port(name="VDD", direction=PortDirection.PG),
            Port(name="VSS", direction=PortDirection.PG),
            Port(name="CE", direction=PortDirection.IN, related_power_pin="VDD", related_ground_pin="VSS"),
            Port(name="DOUT", direction=PortDirection.OUT, related_power_pin="VDD", related_ground_pin="VSS"),
        ],
    )
    top = Definition(
        definition_id="top",
        base_name="top",
        language=SourceLanguage.VERILOG,
        ports=[Port(name="clk", direction=PortDirection.IN)],
        instances=[Instance(name="u_ram", definition_id="ram_macro", connections=[])],
    )
    return Design(top="top", definitions={"top": top, "ram_macro": macro})


def _tab(qtbot, design=None):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(design if design is not None else _design_with_macro())
    return tab


# --- macro PG ground pin -------------------------------------------------------


def test_macro_ground_pg_pin_renders_at_bottom_edge(qtbot):
    tab = _tab(qtbot)
    view = tab.diagram._view
    icon = view._supply_port_icons["pg:top/u_ram:VSS"]

    assert icon.placement.edge == "bottom"
    assert set(icon.handles) == {"bottom"}  # "top" is the interior handle for a bottom-anchored leaf icon -- dropped


def test_macro_ground_pg_pin_rotation_is_180_degrees(qtbot):
    tab = _tab(qtbot)
    view = tab.diagram._view
    icon = view._supply_port_icons["pg:top/u_ram:VSS"]

    assert _ROTATION_FOR_EDGE[icon.placement.edge] == 180.0


def test_macro_ground_pg_pin_label_is_centered_and_inside_the_block(qtbot):
    tab = _tab(qtbot)
    view = tab.diagram._view
    icon = view._supply_port_icons["pg:top/u_ram:VSS"]
    label_rect = icon.label.boundingRect()

    assert icon.label.pos().x() == -label_rect.width() / 2  # horizontally centered
    assert icon.label.pos().y() < 0  # above the icon (which sits ON the bottom boundary) -- i.e. inside the block


def test_macro_power_pg_pin_is_unaffected_stays_at_top(qtbot):
    """Contrast case: VDD has no functional pin naming it via
    related_ground_pin, so it keeps the pre-existing top-edge default."""
    tab = _tab(qtbot)
    view = tab.diagram._view
    icon = view._supply_port_icons["pg:top/u_ram:VDD"]

    assert icon.placement.edge == "top"
    assert icon.label.pos().y() > 0  # below the icon, i.e. still inside the block from the top side


def test_macro_pg_pin_with_no_related_ground_pin_reference_is_not_guessed_at(qtbot):
    """A PG pin no functional pin ever references via related_ground_pin
    keeps the "top" default rather than being guessed at from its own
    name -- matches this codebase's own "not guessed at" precedent."""
    macro = Definition(
        definition_id="mystery_macro",
        base_name="MYSTERY",
        language=SourceLanguage.LIBERTY,
        is_black_box=True,
        ports=[
            Port(name="VSS", direction=PortDirection.PG),  # a plausible ground NAME, but never referenced by any functional pin
            Port(name="EN", direction=PortDirection.IN),  # no related_power_pin/related_ground_pin at all
        ],
    )
    top = Definition(
        definition_id="top",
        base_name="top",
        language=SourceLanguage.VERILOG,
        ports=[Port(name="clk", direction=PortDirection.IN)],
        instances=[Instance(name="u_x", definition_id="mystery_macro", connections=[])],
    )
    design = Design(top="top", definitions={"top": top, "mystery_macro": macro})
    tab = _tab(qtbot, design)
    view = tab.diagram._view
    icon = view._supply_port_icons["pg:top/u_x:VSS"]

    assert icon.placement.edge == "top"


# --- plain Supply Port / Internal Supply ---------------------------------------


def test_plain_internal_supply_snaps_to_bottom_once_used_as_a_supply_sets_ground(qtbot):
    tab = _tab(qtbot)
    view = tab.diagram._view
    box = view._boxes["top"]
    power = view.add_supply_port(box, "VDD_TOP", QColor("#ff0000"))
    ground = view.add_internal_supply(box, "VSS_TOP", QColor("#0000ff"))
    assert ground.placement.edge == "top"  # default, before any Supply Set references it

    new_set = SupplySetPlacement(id="ss1", name="SS1", owner_path="top", power="VDD_TOP", ground="VSS_TOP")
    view.create_power_domain(box, "PD1", QColor("#4caf50"), 0.5, "ss1", [], {"ss1": new_set})

    assert power.placement.edge == "top"  # power role is unaffected
    assert ground.placement.edge == "bottom"
    label_rect = ground.label.boundingRect()
    assert ground.label.pos().x() == -label_rect.width() / 2
    assert ground.label.pos().y() < 0


def test_ground_type_internal_supply_gets_bottom_edge_rotation_and_centered_label(qtbot):
    """Explicit re-confirmation request: a Ground-type INTERNAL SUPPLY
    (not a plain Supply Port, and not a macro pin) must get the exact
    same bottom-edge/180-degree-rotation/centered-inside-label treatment.
    A dedicated test, distinct from
    test_plain_internal_supply_snaps_to_bottom_once_used_as_a_supply_sets_ground()
    above (which already covers edge+label, added for Bug 2 originally)
    -- this one additionally asserts `is_internal_supply` is really True
    (not merely anchored on a box that happens to look like one) and the
    rotation angle explicitly, matching
    test_macro_ground_pin_rotation_is_180_degrees()'s own shape for the
    macro-pin case."""
    tab = _tab(qtbot)
    view = tab.diagram._view
    box = view._boxes["top"]  # same fixture/box every other test in this module uses -- add_internal_supply()'s own is_internal_supply=True flag doesn't depend on box level, see that method's own docstring
    ground = view.add_internal_supply(box, "VSS_INT", QColor("#0000ff"))
    assert ground.placement.is_internal_supply is True

    new_set = SupplySetPlacement(id="ss1", name="SS1", owner_path="top", power="VDD_TOP", ground="VSS_INT")
    view._domain_state.supply_sets["ss1"] = new_set
    view._reclassify_ground_supply_edges()

    assert ground.placement.edge == "bottom"
    assert _ROTATION_FOR_EDGE[ground.placement.edge] == 180.0
    label_rect = ground.label.boundingRect()
    assert ground.label.pos().x() == -label_rect.width() / 2  # horizontally centered
    assert ground.label.pos().y() < 0  # above the icon -- i.e. inside the block, from the bottom side


def test_plain_supply_never_referenced_by_any_supply_sets_ground_stays_at_top(qtbot):
    tab = _tab(qtbot)
    view = tab.diagram._view
    box = view._boxes["top"]
    unreferenced = view.add_internal_supply(box, "VSS_UNUSED", QColor("#0000ff"))

    new_set = SupplySetPlacement(id="ss1", name="SS1", owner_path="top", power="VDD_TOP", ground="VSS_TOP")
    view._domain_state.supply_sets["ss1"] = new_set
    view._reclassify_ground_supply_edges()

    assert unreferenced.placement.edge == "top"


def test_edit_supply_set_live_reclassifies_an_already_placed_supply(qtbot):
    """The live path: a supply is placed FIRST, and only later referenced
    as a Supply Set's Ground via edit_supply_set() (the real, public,
    undo-tracked API) -- not create_power_domain()'s own inline flow."""
    tab = _tab(qtbot)
    view = tab.diagram._view
    box = view._boxes["top"]
    power = view.add_supply_port(box, "VDD_TOP", QColor("#ff0000"))
    ground = view.add_supply_port(box, "VSS_TOP", QColor("#0000ff"))
    new_set = SupplySetPlacement(id="ss1", name="SS1", owner_path="top", power="VDD_TOP")
    view.create_power_domain(box, "PD1", QColor("#4caf50"), 0.5, "ss1", [], {"ss1": new_set})
    assert ground.placement.edge == "top"

    view.edit_supply_set("ss1", "SS1", "VDD_TOP", "VSS_TOP", None, None)

    assert ground.placement.edge == "bottom"


def test_reclassification_never_disturbs_a_supply_already_dragged_to_a_different_edge(qtbot):
    """Bug 2 only overrides the untouched "top" default -- a Ground
    Supply Port the user already manually dragged elsewhere (left/right,
    or explicitly back to top) is left exactly where the user put it."""
    tab = _tab(qtbot)
    view = tab.diagram._view
    box = view._boxes["top"]
    ground = view.add_internal_supply(box, "VSS_TOP", QColor("#0000ff"))
    rect = box.rect()
    ground.setPos(rect.x(), rect.y() + rect.height() / 2)  # drag to the LEFT edge
    assert ground.placement.edge == "left"

    new_set = SupplySetPlacement(id="ss1", name="SS1", owner_path="top", power="VDD_TOP", ground="VSS_TOP")
    view._domain_state.supply_sets["ss1"] = new_set
    view._reclassify_ground_supply_edges()

    assert ground.placement.edge == "left"  # untouched -- reclassification only ever moves a still-"top" placement


def test_ground_and_power_connectivity_between_two_bottom_anchored_icons_still_works(qtbot):
    """Confirms Bug 2 is purely visual: connector routing/handle
    resolution (already fully edge-generic before this fix -- see
    _exit_direction()/_handle_layout()/_INTERIOR_HANDLE_FOR_EDGE) is
    unaffected by a Ground icon now defaulting to the bottom edge."""
    tab = _tab(qtbot)
    view = tab.diagram._view
    box = view._boxes["top"]
    ground = view.add_internal_supply(box, "VSS_TOP", QColor("#0000ff"))
    new_set = SupplySetPlacement(id="ss1", name="SS1", owner_path="top", power="VDD_TOP", ground="VSS_TOP")
    view._domain_state.supply_sets["ss1"] = new_set
    view._reclassify_ground_supply_edges()
    assert ground.placement.edge == "bottom"
    macro_vss = view._supply_port_icons["pg:top/u_ram:VSS"]
    assert macro_vss.placement.edge == "bottom"

    connected = view.try_connect_supply_ports(ground, macro_vss)

    assert connected
    assert len(ground.connectors) == 1
    assert len(macro_vss.connectors) == 1


def test_a_bottom_anchored_macro_ground_pin_tracks_its_box_growing_taller(qtbot):
    """Bug fix (found via this bug): a FIXED (macro PG-pin) icon
    previously never re-derived its own position when its owner box
    resized (SupplyPortIconItem.resnap_to_owner_edge() early-returned
    unconditionally for `fixed` icons) -- harmless before this bug,
    since every fixed icon was always "top"-anchored and _edge_point()'s
    "top" formula never depends on box height. A bottom-anchored one
    (this bug's own new default for a Ground pin) DOES depend on height,
    so the same early return left it stale -- still at the box's OLD
    bottom edge, floating mid-block -- the moment the box grew taller
    for any reason."""
    tab = _tab(qtbot)
    view = tab.diagram._view
    box = view._boxes["top/u_ram"]
    icon = view._supply_port_icons["pg:top/u_ram:VSS"]
    assert icon.placement.edge == "bottom"

    from PySide6.QtCore import QRectF

    old_rect = box.rect()
    box.setRect(QRectF(old_rect.x(), old_rect.y(), old_rect.width(), old_rect.height() + 40))
    for callback in box.on_resize_callbacks:
        callback()

    assert icon.pos().y() == box.rect().bottom()


def test_exact_repro_create_power_domain_on_inner_block_then_ground_supply_set_field(qtbot):
    """PERMANENT regression test, explicit request -- the EXACT reported
    repro, not a lower-level equivalent: Create a Power Domain on an
    INNER hierarchy block -> Create Supply Set -> a Ground rail with a
    brand-new name. This exercises the REAL end-to-end path
    (create_power_domain() -> _add_missing_supply_ports() materializing
    a fresh Internal Supply -> _reclassify_ground_supply_edges()), not
    a placement constructed directly and reclassified by hand the way
    test_plain_internal_supply_snaps_to_bottom_once_used_as_a_supply_sets_ground()
    (Bug 2's own original test, above) does. Uses build_mock_design()
    (not this module's own macro-only fixture) specifically because it
    has a real, non-macro inner hierarchy ("top/u_core") to create the
    domain on."""
    from frontend.src.power_intent_view.supply_port_overlay import SupplyPortIconItem
    from frontend.src.shared.mock_backend import build_mock_design

    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    inner_box = view._boxes["top/u_core"]

    new_set = SupplySetPlacement(id="ss1", name="SS_INNER", power="VDD_INNER", ground="VSS_INNER", owner_path="top/u_core")
    view.create_power_domain(
        inner_box, "PD_INNER", QColor("#4caf50"), 0.5, "ss1", [], {"ss1": new_set},
        new_supply_ports={"VDD_INNER": QColor("#ff0000"), "VSS_INNER": QColor("#0000ff")},
    )

    ground = next(p for p in view._shared_state.supply_port_placements.values() if p.name == "VSS_INNER")
    assert ground.is_internal_supply is True
    icon = view._supply_port_icons[ground.id]
    assert isinstance(icon, SupplyPortIconItem)

    assert ground.edge == "bottom"
    assert icon.pos().y() == inner_box.rect().bottom()  # rendered at the BOTTOM of its block
    assert _ROTATION_FOR_EDGE[ground.edge] == 180.0  # rotated 180 degrees
    label_rect = icon.label.boundingRect()
    assert icon.label.pos().x() == -label_rect.width() / 2  # horizontally centered
    assert icon.label.pos().y() < 0  # above the icon -- INSIDE the block boundary, not floating below it


# --- Audit fix (category 04): scoping and reversibility --------------------
#
# Two defects the health audit confirmed against running code:
#
# (1) classification matched the BARE NAME design-wide, so a Supply Set
#     on one hierarchy naming "VSS" as its ground re-anchored every
#     placement called "VSS" anywhere -- including ports on unrelated
#     hierarchies belonging to no Supply Set at all. That was only ever
#     safe while supply names were globally unique, a rule deliberately
#     removed later.
#
# (2) classification was ONE-WAY (top -> bottom only), with no way to
#     tell its own past decisions apart from a user's deliberate drag.
#     A supply that lost its Ground role stayed anchored at the bottom
#     permanently -- not repaired by undo/redo or by a full rebuild.


def test_ground_role_does_not_leak_onto_a_same_named_port_on_an_unrelated_hierarchy(qtbot):
    tab = _tab(qtbot, build_mock_design())
    view = tab.diagram._view
    in_set = view.add_supply_port(view._boxes["top/u_core"], "VSS", QColor("#000000"))
    unrelated = view.add_supply_port(view._boxes["top/u_dbg"], "VSS", QColor("#000000"))

    view.create_power_domain(
        view._boxes["top/u_core"], "PD_A", QColor("#4caf50"), 0.5, "ssA", [],
        {"ssA": SupplySetPlacement(id="ssA", name="SS_A", power="VDD_A", ground="VSS", owner_path="top/u_core")},
    )

    assert in_set.placement.edge == "bottom"  # genuinely in that Supply Set
    assert unrelated.placement.edge == "top"  # same name, unrelated hierarchy -- untouched


def test_ground_role_still_cascades_from_an_ancestors_shared_supply_set(qtbot):
    """The scoping fix must not break CHANGE 2's ancestor sharing:
    _auto_link_cross_hierarchy_supply_sets() materializes a local
    Internal Supply on the sharing hierarchy WITHOUT extending the
    Supply Set's own owner_paths, so exact-path matching alone would
    silently un-ground every shared supply."""
    tab = _tab(qtbot, build_mock_design())
    view = tab.diagram._view
    view.create_power_domain(
        view._boxes["top"], "PD_TOP", QColor("#4caf50"), 0.5, "ss1", [],
        {"ss1": SupplySetPlacement(id="ss1", name="SS", power="VDD", ground="VSS", owner_path="top")},
        new_supply_ports={"VDD": QColor("#ff0000"), "VSS": QColor("#000000")},
    )
    view.create_power_domain(view._boxes["top/u_core"], "PD_SUB", QColor("#0000ff"), 0.5, "ss1", [], {})

    shared_vss = [p for p in view._shared_state.supply_port_placements.values() if p.name == "VSS" and p.owner_path == "top/u_core"]
    assert shared_vss, "ancestor sharing should have auto-linked a local VSS"
    assert shared_vss[0].edge == "bottom"


def test_a_supply_that_loses_its_ground_role_returns_to_the_top_edge(qtbot):
    tab = _tab(qtbot, build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top"]
    view.add_supply_port(box, "VDD1", QColor("#ff0000"))
    vss = view.add_supply_port(box, "VSS", QColor("#000000"))
    view.add_supply_port(box, "VSS2", QColor("#111111"))
    view.create_power_domain(
        box, "PD1", QColor("#4caf50"), 0.5, "ss1", [],
        {"ss1": SupplySetPlacement(id="ss1", name="SS", power="VDD1", ground="VSS", owner_path="top")},
    )
    assert vss.placement.edge == "bottom"

    view.edit_supply_set("ss1", "SS", "VDD1", "VSS2", None, None, {}, {})

    assert vss.placement.edge == "top"  # reverted, not stranded at the bottom
    vss2 = next(p for p in view._shared_state.supply_port_placements.values() if p.name == "VSS2")
    assert vss2.edge == "bottom"  # and the new ground took its place


def test_the_ground_revert_survives_a_full_rebuild(qtbot):
    """The auto-anchor marker must persist across icon reconstruction --
    it previously did not, because building an icon fired a position
    change that looked like a user drag and cleared it, so the revert
    could only ever fire before the first rebuild."""
    tab = _tab(qtbot, build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top"]
    view.add_supply_port(box, "VDD1", QColor("#ff0000"))
    vss = view.add_supply_port(box, "VSS", QColor("#000000"))
    view.add_supply_port(box, "VSS2", QColor("#111111"))
    view.create_power_domain(
        box, "PD1", QColor("#4caf50"), 0.5, "ss1", [],
        {"ss1": SupplySetPlacement(id="ss1", name="SS", power="VDD1", ground="VSS", owner_path="top")},
    )
    view.refresh()
    assert vss.placement.ground_auto_anchored is True  # marker survived the rebuild

    view.edit_supply_set("ss1", "SS", "VDD1", "VSS2", None, None, {}, {})
    assert vss.placement.edge == "top"


def test_a_user_dragged_supply_is_never_reverted_by_the_classifier(qtbot):
    from PySide6.QtCore import QPointF

    tab = _tab(qtbot, build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top"]
    view.add_supply_port(box, "VDD1", QColor("#ff0000"))
    icon = view.add_supply_port(box, "VSS", QColor("#000000"))
    view.create_power_domain(
        box, "PD1", QColor("#4caf50"), 0.5, "ss1", [],
        {"ss1": SupplySetPlacement(id="ss1", name="SS", power="VDD1", ground="VSS", owner_path="top")},
    )
    live = view._supply_port_icons[icon.key]
    rect = box.rect()
    live.setPos(QPointF(rect.left(), rect.top() + rect.height() * 0.5))  # drag to the LEFT edge
    assert live.placement.edge == "left"
    assert live.placement.ground_auto_anchored is False  # a real drag clears the marker

    view.edit_supply_set("ss1", "SS", "VDD1", None, None, None, {}, {})  # ground role removed entirely

    assert live.placement.edge == "left"  # user's own placement respected
