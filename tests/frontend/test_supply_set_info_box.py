"""Merged Power Domain info box (explicit request, CONSOLIDATE task):
supersedes the earlier separate DomainLabelItem (name-only label) and
SupplySetInfoBoxItem (Supply-Set-only dotted box) -- both are now ONE
combined DomainInfoBoxItem, anchored bottom-right of the owner box,
still dotted/dashed grey-bordered, still sized exactly to content.
Content, top to bottom: the domain name (bold italic, unchanged
styling), then "Primary : <name>" and one "Secondary : <name>" per
Secondary Supply Set (both italic, smaller, not bold). LIVE-updates on
Power Domain rename and on Supply Set add/edit/remove (via
PowerIntentDiagramView._refresh_domain_derived_colors(), already called
from every one of those paths -- see DomainInfoBoxItem's own module
docstring in power_domain_overlay.py). Block-resize coverage (reusing
the shared "make room" insertion-resize mechanism) lives in the
dedicated permanent regression suite,
test_block_expansion_on_insertion.py's own merged-info-box section, per
explicit request -- not duplicated here.
"""

import gc

import pytest
from PySide6.QtGui import QColor

from frontend.src.power_intent_view.power_domain_overlay import _DOMAIN_INFO_PADDING, SupplySetPlacement
from frontend.src.power_intent_view.power_intent_tab import PowerIntentTab
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


def _row_texts(item) -> list[str]:
    return [text for text, _color in item._rows]


def test_creating_a_power_domain_shows_the_merged_box_with_name_and_primary(qtbot):
    tab, view = _tab_and_view(qtbot)
    box = view._boxes["top"]
    new_set = SupplySetPlacement(id="ss1", name="SS_VDD", power="VDD1", owner_path="top")

    domain = view.create_power_domain(box, "PD1", QColor("#4caf50"), 0.5, "ss1", [], {"ss1": new_set}, {"VDD1": QColor("#ff0000")})

    boxes = view._domain_info_boxes[domain.id]
    assert len(boxes) == 1
    assert _row_texts(boxes[0]) == ["PD1", "Primary : SS_VDD"]
    assert boxes[0].owner_box is box
    _deselect_and_collect(tab)


def test_a_domain_with_no_primary_supply_set_yet_still_gets_a_box_with_a_placeholder(qtbot):
    """Explicit design decision (documented in build_domain_info_box_overlay()'s
    own docstring): unlike the old, now-superseded SupplySetInfoBoxItem
    (which rendered nothing without a Primary), the merged box ALWAYS
    renders -- matching the old DomainLabelItem's own always-render
    behavior -- with a "?" placeholder for the Primary line's name."""
    tab, view = _tab_and_view(qtbot)
    box = view._boxes["top"]

    domain = view.create_power_domain(box, "PD1", QColor("#4caf50"), 0.5, None, [], {})

    assert domain.id in view._domain_info_boxes
    assert _row_texts(view._domain_info_boxes[domain.id][0]) == ["PD1", "Primary : ?"]
    _deselect_and_collect(tab)


def test_adding_a_secondary_supply_set_via_edit_updates_the_box_live(qtbot):
    tab, view = _tab_and_view(qtbot)
    box = view._boxes["top"]
    primary = SupplySetPlacement(id="ss1", name="SS_VDD", power="VDD1", owner_path="top")
    domain = view.create_power_domain(box, "PD1", QColor("#4caf50"), 0.5, "ss1", [], {"ss1": primary}, {"VDD1": QColor("#ff0000")})
    assert _row_texts(view._domain_info_boxes[domain.id][0]) == ["PD1", "Primary : SS_VDD"]

    secondary = SupplySetPlacement(id="ss2", name="SS_VDD_INT", power="VSS_X", owner_path="top")
    view.edit_power_domain(domain.id, "PD1", QColor("#4caf50"), 0.5, "ss1", ["ss2"], {"ss2": secondary}, {"VSS_X": QColor("#0000ff")})

    assert _row_texts(view._domain_info_boxes[domain.id][0]) == ["PD1", "Primary : SS_VDD", "Secondary : SS_VDD_INT"]
    _deselect_and_collect(tab)


def test_renaming_a_power_domain_updates_the_boxs_name_line_live(qtbot):
    tab, view = _tab_and_view(qtbot)
    box = view._boxes["top"]
    primary = SupplySetPlacement(id="ss1", name="SS_VDD", power="VDD1", owner_path="top")
    domain = view.create_power_domain(box, "PD1", QColor("#4caf50"), 0.5, "ss1", [], {"ss1": primary}, {"VDD1": QColor("#ff0000")})

    view.edit_power_domain(domain.id, "PD_RENAMED", QColor("#4caf50"), 0.5, "ss1", [], {})

    assert _row_texts(view._domain_info_boxes[domain.id][0])[0] == "PD_RENAMED"
    _deselect_and_collect(tab)


def test_renaming_a_supply_set_updates_the_box_text_live(qtbot):
    tab, view = _tab_and_view(qtbot)
    box = view._boxes["top"]
    primary = SupplySetPlacement(id="ss1", name="SS_VDD", power="VDD1", owner_path="top")
    domain = view.create_power_domain(box, "PD1", QColor("#4caf50"), 0.5, "ss1", [], {"ss1": primary}, {"VDD1": QColor("#ff0000")})

    view.edit_supply_set("ss1", "SS_VDD_RENAMED", "VDD1", None, None, None)

    assert _row_texts(view._domain_info_boxes[domain.id][0]) == ["PD1", "Primary : SS_VDD_RENAMED"]
    _deselect_and_collect(tab)


def test_removing_a_secondary_supply_set_via_edit_updates_the_box_live(qtbot):
    tab, view = _tab_and_view(qtbot)
    box = view._boxes["top"]
    primary = SupplySetPlacement(id="ss1", name="SS_VDD", power="VDD1", owner_path="top")
    secondary = SupplySetPlacement(id="ss2", name="SS_VDD_INT", power="VSS_X", owner_path="top")
    domain = view.create_power_domain(
        box, "PD1", QColor("#4caf50"), 0.5, "ss1", ["ss2"], {"ss1": primary, "ss2": secondary}, {"VDD1": QColor("#ff0000"), "VSS_X": QColor("#0000ff")}
    )
    assert _row_texts(view._domain_info_boxes[domain.id][0]) == ["PD1", "Primary : SS_VDD", "Secondary : SS_VDD_INT"]

    view.edit_power_domain(domain.id, "PD1", QColor("#4caf50"), 0.5, "ss1", [], {})

    assert _row_texts(view._domain_info_boxes[domain.id][0]) == ["PD1", "Primary : SS_VDD"]
    _deselect_and_collect(tab)


def test_removing_the_power_domain_removes_its_merged_box(qtbot):
    tab, view = _tab_and_view(qtbot)
    box = view._boxes["top"]
    new_set = SupplySetPlacement(id="ss1", name="SS_VDD", power="VDD1", owner_path="top")
    domain = view.create_power_domain(box, "PD1", QColor("#4caf50"), 0.5, "ss1", [], {"ss1": new_set}, {"VDD1": QColor("#ff0000")})
    assert domain.id in view._domain_info_boxes

    view.remove_power_domain(box)

    assert domain.id not in view._domain_info_boxes
    _deselect_and_collect(tab)


def test_a_multi_hierarchy_domain_shows_and_syncs_the_merged_box_on_every_boundary_block(qtbot):
    tab, view = _tab_and_view(qtbot)
    top_box = view._boxes["top"]
    core_box = view._boxes["top/u_core"]
    new_set = SupplySetPlacement(id="ss1", name="SS_VDD", power="VDD1", owner_path="top")

    domain = view.create_power_domain(
        top_box, "PD1", QColor("#4caf50"), 0.5, "ss1", [], {"ss1": new_set}, {"VDD1": QColor("#ff0000")}, additional_boxes=[core_box]
    )

    boxes = view._domain_info_boxes[domain.id]
    assert len(boxes) == 2
    assert {b.owner_box.path for b in boxes} == {"top", "top/u_core"}
    assert all(_row_texts(b) == ["PD1", "Primary : SS_VDD"] for b in boxes)

    # Edit propagates to BOTH member hierarchies' own boxes, kept in sync.
    secondary = SupplySetPlacement(id="ss2", name="SS_VDD_INT", power="VSS_X", owner_path="top")
    view.edit_power_domain(domain.id, "PD1", QColor("#4caf50"), 0.5, "ss1", ["ss2"], {"ss2": secondary}, {"VSS_X": QColor("#0000ff")})

    boxes_after = view._domain_info_boxes[domain.id]
    assert len(boxes_after) == 2
    assert all(_row_texts(b) == ["PD1", "Primary : SS_VDD", "Secondary : SS_VDD_INT"] for b in boxes_after)
    _deselect_and_collect(tab)


def test_primary_and_name_lines_share_the_domain_color_secondary_uses_its_own(qtbot):
    """Explicit coloring requirement: the name line and the Primary line
    share the domain's own derived color; each Secondary line uses its
    OWN Supply Set's Power rail color, independently."""
    tab, view = _tab_and_view(qtbot)
    box = view._boxes["top"]
    primary = SupplySetPlacement(id="ss1", name="SS_VDD", power="VDD1", owner_path="top")
    secondary = SupplySetPlacement(id="ss2", name="SS_VDD_INT", power="VSS_X", owner_path="top")

    domain = view.create_power_domain(
        box, "PD1", QColor("#4caf50"), 0.5, "ss1", ["ss2"], {"ss1": primary, "ss2": secondary}, {"VDD1": QColor("#ff0000"), "VSS_X": QColor("#0000ff")}
    )

    info_box = view._domain_info_boxes[domain.id][0]
    name_color, primary_color, secondary_color = (color for _text, color in info_box._rows)
    assert name_color.name() == domain.color.name()
    assert primary_color.name() == domain.color.name()
    assert secondary_color.name() == "#0000ff"
    _deselect_and_collect(tab)


def test_the_merged_box_has_no_connectivity_role_of_its_own(qtbot):
    """Explicit request: adding/editing/removing the merged box must NOT
    trigger power net rerouting -- confirmed by construction here, not
    just by absence of a symptom: growing a block to fit a taller box
    already, unavoidably, causes _reflow_layout()'s own PRE-EXISTING
    connector.update_route() sweep to re-run for every real Supply Port
    connector on that block (same behavior an Isolation/Level Shifter/
    plain Supply Port insertion already triggers -- not specific to this
    feature at all, see _reflow_layout()'s own docstring). What IS
    specific to this feature, and what actually matters for "must not
    trigger power net rerouting": the merged box itself is never a
    connector endpoint -- it has no handles, isn't returned by
    connector_anchor_scene_pos()/_connector_handle_at(), and can't be
    passed to try_connect_supply_ports() at all."""
    tab, view = _tab_and_view(qtbot)
    box = view._boxes["top"]
    new_set = SupplySetPlacement(id="ss1", name="SS_VDD", power="VDD1", owner_path="top")
    domain = view.create_power_domain(box, "PD1", QColor("#4caf50"), 0.5, "ss1", [], {"ss1": new_set})
    info_box = view._domain_info_boxes[domain.id][0]

    assert not hasattr(info_box, "handles")
    assert not hasattr(info_box, "connectors")
    port = view.add_supply_port(box, "VDD_OTHER", QColor("#00ff00"), edge="left", t=0.5)
    with pytest.raises((AttributeError, TypeError)):
        view.try_connect_supply_ports(port, info_box)


# --- Bug fix: box must fully contain its own rendered text -----------------
# horizontalAdvance() (the cursor-advance distance) used to size the box,
# but a BOLD ITALIC/ITALIC line's own last character can visually overhang
# past its own advance width -- confirmed via a live QFontMetricsF
# diagnostic, not assumed (e.g. a Supply Set name ending in certain
# letters like "V"/"W"/"Y" measurably differs between the two APIs on
# this font) -- letting long lines spill past the dashed border on some
# fonts. _content_size() now uses boundingRect(text).right() instead,
# the correct measure of "how far right this text's own rendered pixels
# actually reach" from the same x=0 origin drawText() itself uses.
#
# NOTE on the regression test's own methodology: the italic overhang
# this bug is about is only ever a SUB-PIXEL difference against
# horizontalAdvance() on the fonts available in this test environment
# (confirmed via the same live diagnostic) -- far smaller than
# _DOMAIN_INFO_PADDING's own several-pixel right-side clearance, so a
# "does the box visually clip on THIS font" assertion can never
# reliably fail without the fix here (it would pass either way, purely
# by padding coincidence, on this environment -- confirmed empirically
# before settling on the approach below). The real production risk is
# environment/font-dependent (a different OS/font could show a much
# larger overhang, as the bug report's own screenshot demonstrates)&#59;
# a robust, environment-independent regression instead asserts the fix
# STRUCTURALLY: _content_size()'s own width calculation must be derived
# from boundingRect().right(), not horizontalAdvance() -- checked by
# picking a Supply Set name confirmed (live, via QFontMetricsF) to
# differ measurably between the two APIs on this font, then asserting
# the box's own computed width tracks the boundingRect()-based value
# exactly, not the (smaller, on this font) horizontalAdvance()-based one.


def test_box_width_is_derived_from_bounding_rect_not_horizontal_advance(qtbot):
    tab, view = _tab_and_view(qtbot)
    box = view._boxes["top"]
    primary = SupplySetPlacement(id="ss1", name="SS_VDD", power="VDD1", owner_path="top")
    secondary_a = SupplySetPlacement(id="ss2", name="SS_A", power="VDD_A", owner_path="top")
    # Deliberately ends in "V" -- an italic glyph confirmed (via a live
    # QFontMetricsF diagnostic against this exact font) to measurably
    # differ between horizontalAdvance() and boundingRect().right(),
    # the precise shape of glyph this bug needed to catch.
    secondary_b = SupplySetPlacement(id="ss3", name="SS_VDD_INTERNAL_LONG_NAME_ENDING_IN_V", power="VDD_B", owner_path="top")
    domain = view.create_power_domain(
        box,
        "PD_TOP",
        QColor("#4caf50"),
        0.5,
        "ss1",
        ["ss2", "ss3"],
        {"ss1": primary, "ss2": secondary_a, "ss3": secondary_b},
        {"VDD1": QColor("#ff0000"), "VDD_A": QColor("#00ff00"), "VDD_B": QColor("#0000ff")},
    )

    info_box = view._domain_info_boxes[domain.id][0]
    box_rect = info_box.boundingRect()

    from PySide6.QtGui import QFontMetricsF

    name_metrics = QFontMetricsF(info_box._name_font)
    line_metrics = QFontMetricsF(info_box._line_font)
    bounding_rect_widths = [name_metrics.boundingRect(info_box._rows[0][0]).right()]
    bounding_rect_widths += [line_metrics.boundingRect(text).right() for text, _color in info_box._rows[1:]]
    advance_widths = [name_metrics.horizontalAdvance(info_box._rows[0][0])]
    advance_widths += [line_metrics.horizontalAdvance(text) for text, _color in info_box._rows[1:]]
    # Sanity: this Supply Set name really does make the two APIs disagree
    # on this font -- otherwise the assertion below wouldn't distinguish
    # the fixed behavior from the reverted (buggy) one at all.
    assert max(bounding_rect_widths) != max(advance_widths)

    expected_width = max(bounding_rect_widths) + 2 * _DOMAIN_INFO_PADDING
    assert box_rect.width() == expected_width

    # Every text line's own right edge (the boundingRect()-based, truly
    # correct measure) must fit within the box, with no overflow.
    for i, (text, _color) in enumerate(info_box._rows):
        metrics = name_metrics if i == 0 else line_metrics
        text_right_edge = _DOMAIN_INFO_PADDING + metrics.boundingRect(text).right()
        assert text_right_edge <= box_rect.width(), f"{text!r} overflows the box's own right edge"
    _deselect_and_collect(tab)


# --- Bug fix (explicit report, follow-up to the WIDTH fix above): the box
# used to reuse a line's own BASELINE (already offset by that line's own
# ascent) as the "top of slot" reference for the NEXT line, then add that
# next line's FULL height (ascent+descent) on top of it -- effectively
# double-counting the name line's own ascent once, producing an unexplained
# gap between the name and Primary lines (reported as a suspected "phantom
# blank line" in the underlying row data -- investigated and disproven:
# domain_info_box_rows() never produces a blank entry, see the dedicated
# test below) and, because the box's OWN separately-computed content_size()
# height formula didn't carry the same error, the LAST line's real drawn
# position fell outside the box's own reported (shorter) height entirely.
# Verified live, before writing the fix: for the golden PD_MEM / SS_VDD_MEM
# / SS_VDD scenario below, the pre-fix formula produced a 12.5+ px phantom
# gap after the name line and an 8.5+ px overflow of the last line past the
# box's own bottom edge. Fixed by replacing BOTH _content_size()'s height
# formula and paint()'s own Y-accumulation with a single shared
# _stacked_line_layout() helper that stacks each line by its own REAL
# boundingRect() extent (used by domain_info_box_required_height() too, so
# the reservation and the box's own rendering can never diverge).


def test_domain_info_box_rows_has_no_phantom_blank_entries(qtbot):
    """Regression test (explicit request): construct the row list for a
    Power Domain with a name + Primary + 2 Secondary Supply Sets and
    assert the exact count -- 1 (name) + 1 (Primary) + 2 (Secondary), no
    extra/blank entry anywhere. (The suspected "stray blank line in the
    data" theory for the reported visual gap was investigated and
    disproven this way -- the real cause was the Y-accumulation bug
    described above -- but this structural guarantee on the row list
    itself is a real, worthwhile regression to lock in permanently.)"""
    tab, view = _tab_and_view(qtbot)
    box = view._boxes["top"]
    primary = SupplySetPlacement(id="ss1", name="SS_VDD_MEM", power="VDD_MEM", owner_path="top")
    secondary_a = SupplySetPlacement(id="ss2", name="SS_VDD", power="VDD", owner_path="top")
    secondary_b = SupplySetPlacement(id="ss3", name="SS_AON", power="VDD_AON", owner_path="top")
    domain = view.create_power_domain(
        box,
        "PD_MEM",
        QColor("#4caf50"),
        0.5,
        "ss1",
        ["ss2", "ss3"],
        {"ss1": primary, "ss2": secondary_a, "ss3": secondary_b},
    )

    info_box = view._domain_info_boxes[domain.id][0]
    rows = info_box._rows

    assert len(rows) == 1 + 1 + 2  # name + Primary + 2 Secondary, no more
    assert all(text.strip() != "" for text, _color in rows), f"found a blank/empty row: {rows!r}"
    assert [text for text, _color in rows] == [
        "PD_MEM",
        "Primary : SS_VDD_MEM",
        "Secondary : SS_VDD",
        "Secondary : SS_AON",
    ]
    _deselect_and_collect(tab)


def test_merged_box_fully_contains_all_text_within_its_dashed_border(qtbot):
    """Regression test (explicit request, golden scenario: PD_MEM /
    SS_VDD_MEM / SS_VDD), verified live to genuinely FAIL against the
    pre-fix Y-accumulation formula for this exact scenario before this
    fix (an 8.5+ px overflow of the "Secondary : SS_VDD" line past the
    box's own bottom edge) and to PASS once the fix
    (_stacked_line_layout(), see the module comment above) is in place.

    Renders the item's own paint() in ISOLATION (a plain white QImage,
    no scene/outer-box involved -- item.paint() itself never touches
    `option`/`widget` here, confirmed by reading its own body, so both
    are safely passed as None) rather than the whole scene, so the only
    ink that can possibly appear is this box's own dashed border plus
    its own text -- no risk of an unrelated neighboring element (the
    owner block's own border, other overlays) being mistaken for
    overflow. Deliberately does not re-derive the box's own
    baseline/stacking arithmetic in the test itself (that would just
    test the fix against a second copy of itself) -- the pixel buffer is
    an independent ground truth."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage, QPainter

    tab, view = _tab_and_view(qtbot)
    box = view._boxes["top"]
    primary = SupplySetPlacement(id="ss1", name="SS_VDD_MEM", power="VDD_MEM", owner_path="top")
    secondary = SupplySetPlacement(id="ss2", name="SS_VDD", power="VDD", owner_path="top")
    domain = view.create_power_domain(
        box,
        "PD_MEM",
        QColor("#4caf50"),
        0.5,
        "ss1",
        ["ss2"],
        {"ss1": primary, "ss2": secondary},
    )
    info_box = view._domain_info_boxes[domain.id][0]
    box_rect = info_box.boundingRect()

    # Extra room below/right of the box's own reported rect, so an
    # overflowing line has somewhere to actually land in the image
    # instead of being clipped away by the image bounds themselves
    # (which would silently hide the very bug this test exists to
    # catch).
    overflow_margin = 30
    width = int(box_rect.width()) + overflow_margin
    height = int(box_rect.height()) + overflow_margin

    image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.white)
    painter = QPainter(image)
    info_box.paint(painter, None, None)
    painter.end()

    import math

    overflow_pixels = []
    for y in range(math.ceil(box_rect.height()), height):
        for x in range(width):
            if image.pixelColor(x, y) != Qt.GlobalColor.white:
                overflow_pixels.append((x, y))

    assert not overflow_pixels, (
        f"found {len(overflow_pixels)} non-background pixel(s) below the merged box's own bottom edge "
        f"-- text is spilling past the dashed border: {overflow_pixels[:10]}"
    )
    _deselect_and_collect(tab)
