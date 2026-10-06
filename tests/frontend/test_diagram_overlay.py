"""power_intent_view/diagram_overlay.py -- built against mock/fixture
data only, per CLAUDE.md's front-end independence rule (no backend/
import anywhere in this file or the modules under test).
"""

from frontend.src.power_intent_view.diagram_overlay import DomainRegionItem, build_power_intent_overlay
from frontend.src.shared.diagram.hierarchy_scene_builder import build_hierarchy_scene
from frontend.src.shared.mock_backend import build_mock_design, build_mock_mapped_design, build_mock_power_intent
from frontend.src.shared.models.mapping import MappedDesign, ReferenceLocation, ResolutionKind, ResolvedReference
from frontend.src.shared.models.power_intent import PowerDomain, PowerIntentDesign, SourceFormat


def _empty_domain_mapped_design():
    """A minimal, purpose-built scenario (not the full canned mock) --
    one domain whose sole -elements reference is DANGLING, i.e. resolves
    to zero real members. The clearest, most direct proof of the
    fallback, independent of whatever the canned mock scenario happens
    to contain.
    """
    rtl = build_mock_design()
    power_intent = PowerIntentDesign(
        source_format=SourceFormat.UPF,
        source_version="3.1",
        domains={"::PD_EMPTY": PowerDomain(id="::PD_EMPTY", name="PD_EMPTY", scope="", elements=["u_ghost"])},
    )
    references = [
        ResolvedReference(
            raw="u_ghost",
            location=ReferenceLocation("PowerDomain", "::PD_EMPTY", "elements"),
            kind=ResolutionKind.DANGLING,
            detail="'u_ghost': no instance at that path",
        )
    ]
    return MappedDesign(power_intent=power_intent, rtl=rtl, references=references), rtl


def test_domain_with_zero_resolved_members_renders_no_region_item(qtbot):
    mapped, rtl = _empty_domain_mapped_design()
    scene, boxes = build_hierarchy_scene(rtl)
    base_item_count = len(scene.items())

    index = build_power_intent_overlay(scene, boxes, mapped)

    # The fallback: no DomainRegionItem anywhere in the scene for this
    # domain -- not a zero-size or degenerate one, none at all.
    region_items = [item for item in scene.items() if isinstance(item, DomainRegionItem)]
    assert region_items == []
    # Nothing else got added to the scene on this domain's behalf either
    # (confirms the fallback short-circuits cleanly, not "skip the visible
    # region but still leave some other stray item behind").
    assert len(scene.items()) == base_item_count


def test_domain_with_zero_resolved_members_still_has_a_selection_index_entry(qtbot):
    """The domain must stay selectable from the properties panel even
    with nothing to show on the diagram -- registered with an empty
    list, not simply absent from the index (see
    build_power_intent_overlay()'s own docstring on this distinction).
    """
    mapped, rtl = _empty_domain_mapped_design()
    scene, boxes = build_hierarchy_scene(rtl)
    index = build_power_intent_overlay(scene, boxes, mapped)

    assert ("PowerDomain", "::PD_EMPTY") in index
    assert index[("PowerDomain", "::PD_EMPTY")] == []


def test_domain_with_zero_resolved_members_leaves_no_switch_icon_either(qtbot):
    """A PowerSwitch whose domain has no renderable region has no
    boundary to sit at -- same fallback principle applied to switches,
    not just domains."""
    from frontend.src.shared.models.power_intent import PowerSwitch

    mapped, rtl = _empty_domain_mapped_design()
    mapped.power_intent.switches["::SW_GHOST"] = PowerSwitch(id="::SW_GHOST", name="SW_GHOST", scope="", domain="PD_EMPTY")
    mapped.references.append(
        ResolvedReference(
            raw="PD_EMPTY",
            location=ReferenceLocation("PowerSwitch", "::SW_GHOST", "domain"),
            kind=ResolutionKind.POWER_INTENT,
            resolved_power_intent_id="::PD_EMPTY",
        )
    )
    scene, boxes = build_hierarchy_scene(rtl)
    index = build_power_intent_overlay(scene, boxes, mapped)

    assert index[("PowerSwitch", "::SW_GHOST")] == []


# --- general overlay correctness against the full canned mock scenario ---


def _full_mapped():
    rtl = build_mock_design()
    power_intent = build_mock_power_intent()
    return build_mock_mapped_design(power_intent, rtl), rtl


def test_populated_domain_renders_a_region_covering_its_member_box(qtbot):
    mapped, rtl = _full_mapped()
    scene, boxes = build_hierarchy_scene(rtl)
    index = build_power_intent_overlay(scene, boxes, mapped)

    region, *members = index[("PowerDomain", "::PD_CORE")]
    assert isinstance(region, DomainRegionItem)
    assert boxes["top/u_core"] in members
    # The region's bounding rect must actually contain the member box it
    # groups, not just exist independently of it.
    assert region.sceneBoundingRect().contains(boxes["top/u_core"].sceneBoundingRect())


def test_isolation_and_level_shifter_badges_land_on_different_pins(qtbot):
    mapped, rtl = _full_mapped()
    scene, boxes = build_hierarchy_scene(rtl)
    index = build_power_intent_overlay(scene, boxes, mapped)

    (iso_badge,) = index[("IsolationStrategy", "::ISO1")]
    (ls_badge,) = index[("LevelShifterStrategy", "::LS1")]
    assert iso_badge.parentItem() is boxes["top/u_core"]
    assert ls_badge.parentItem() is boxes["top/u_core"]
    # Badge geometry is baked into the polygon points themselves (passed
    # to the QPolygonF constructor), not via setPos() -- .pos() stays
    # (0, 0) for both regardless of pin, so comparing bounding rects
    # (which reflect the actual polygon geometry) is the real check.
    assert iso_badge.sceneBoundingRect().center() != ls_badge.sceneBoundingRect().center()


def test_retention_badge_is_instance_level_not_pin_level(qtbot):
    mapped, rtl = _full_mapped()
    scene, boxes = build_hierarchy_scene(rtl)
    index = build_power_intent_overlay(scene, boxes, mapped)

    (ret_badge,) = index[("RetentionStrategy", "::RET1")]
    assert ret_badge.parentItem() is boxes["top/u_core"]
