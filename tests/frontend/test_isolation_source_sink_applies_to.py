"""Isolation: `-source`/`-sink`, and "Applies To All".

CHANGE 1 (reference golden, design/single_clock_design): the dialog's
optional Source/Sink Supply Set reach the UPF as `-source`/`-sink`,
right after the `set_isolation` head line -- and only when set.

CHANGE 2 (explicit request): "Applies To All: Inputs | Outputs | Both |
Elements" below Isolation Sense, the same choice Level Shifter has. A
broad choice writes IEEE 1801 `-applies_to <dir>` and no `-elements`
(confirmed with the user over a literal `-applies_to_all`, which is not
an 1801 option); "Elements" enables the Elements section and writes
`-elements { ... }` as before.
"""

from __future__ import annotations

import pytest
from PySide6.QtGui import QColor

from frontend.src.power_intent_view.isolation_dialog import IsolationDialog
from frontend.src.power_intent_view.isolation_overlay import (
    IsolationDiagramState,
    IsolationStrategyPlacement,
    isolation_attachment_owner_paths,
    isolation_variants_on_block,
)
from frontend.src.power_intent_view.power_domain_overlay import (
    PowerDomainDiagramState,
    PowerDomainPlacement,
    SupplySetPlacement,
)
from frontend.src.power_intent_view.power_intent_tab import PowerIntentTab
from frontend.src.power_intent_view.power_intent_text_export import generate_upf
from frontend.src.power_intent_view.retention_overlay import RetentionDiagramState
from frontend.src.power_intent_view.supply_port_overlay import SupplyPortDiagramState
from frontend.src.shared.mock_backend import build_mock_design

_C = QColor("#ff0000")


def _domain_state():
    return PowerDomainDiagramState(
        supply_sets={
            "ss_top": SupplySetPlacement(id="ss_top", name="SS_TOP", power="VDD", ground="VSS", owner_path="top"),
            "ss_sub": SupplySetPlacement(id="ss_sub", name="SS_SUB", power="VDD_SUB", ground="VSS", owner_path="top/u_sub"),
        },
        domains={
            "pd_sub": PowerDomainPlacement(
                id="pd_sub", owner_path="top/u_sub", owner_paths=["top/u_sub"], name="PD_SUB",
                color=_C, primary_supply_set="ss_sub",
            ),
        },
    )


def _iso(**overrides):
    fields = dict(
        id="iso", name="ISO", power_domain_id="pd_sub", isolation_supply_set_id="ss_top",
        clamp_value="low", isolation_control="iso_n", isolation_sense="low", location="parent",
        elements=["top/u_sub/q"], owner_path="top/u_sub",
    )
    fields.update(overrides)
    return IsolationStrategyPlacement(**fields)


def _upf(iso) -> str:
    text = generate_upf(
        SupplyPortDiagramState(), _domain_state(), RetentionDiagramState(), "3.1",
        isolation_state=IsolationDiagramState(strategies={iso.id: iso}),
    )
    return text[text.index("set_isolation"):].split("\n\n")[0]


# --- CHANGE 1: -source / -sink ----------------------------------------------


def test_source_and_sink_follow_the_head_line_when_set():
    block = _upf(_iso(source_supply_set_id="ss_sub", sink_supply_set_id="ss_top"))
    assert block.startswith(
        "set_isolation ISO -domain PD_SUB \\\n"
        "  -source SS_SUB \\\n"
        "  -sink SS_TOP \\\n"
        "  -isolation_supply_set SS_TOP \\\n"
    )


@pytest.mark.parametrize(
    "source, sink, present",
    [(None, None, []), ("ss_sub", None, ["-source"]), (None, "ss_top", ["-sink"])],
)
def test_source_and_sink_are_each_emitted_only_when_set(source, sink, present):
    block = _upf(_iso(source_supply_set_id=source, sink_supply_set_id=sink))
    for flag in ("-source", "-sink"):
        assert (f"  {flag} " in block) == (flag in present)


def test_a_dangling_source_reference_emits_nothing_rather_than_none():
    assert "-source" not in _upf(_iso(source_supply_set_id="deleted"))


# --- CHANGE 2: Applies To All -- generation ----------------------------------


@pytest.mark.parametrize("direction", ["inputs", "outputs", "both"])
def test_a_broad_choice_writes_applies_to_and_no_elements(direction):
    block = _upf(_iso(applies_to=direction, elements=[]))
    assert f"  -applies_to {direction} \\\n  -location parent" in block
    assert "-elements" not in block
    assert "-applies_to_all" not in block


def test_elements_mode_writes_elements_and_no_applies_to():
    block = _upf(_iso(applies_to="elements"))
    assert "-elements {" in block
    assert "-applies_to" not in block


def test_a_strategy_from_before_the_field_existed_is_elements_mode():
    assert _iso().applies_to == "elements"


# --- CHANGE 2: the dialog ------------------------------------------------------


@pytest.fixture
def dialog_parts(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    boxes = tab.diagram._view._boxes
    domains = {"d1": PowerDomainPlacement(id="d1", owner_path="top", name="PD_TOP", color=_C)}
    supply_sets = {"ss1": SupplySetPlacement(id="ss1", name="SS1", power="VDD", ground="VSS", owner_paths=["top"])}
    return boxes, domains, supply_sets


def _dialog(qtbot, dialog_parts, initial=None):
    boxes, domains, supply_sets = dialog_parts
    dialog = IsolationDialog(boxes, domains, supply_sets, lambda endpoints: ({}, {}), {}, initial=initial)
    qtbot.addWidget(dialog)
    return dialog


def _fill_required(dialog):
    dialog.name_edit.setText("ISO1")
    dialog.isolation_supply_combo.setCurrentIndex(dialog.isolation_supply_combo.findData("ss1"))
    dialog.clamp_group.selected = "low"
    dialog.isolation_control_edit.setText("iso_n")
    dialog.sense_group.selected = "low"


def test_a_new_dialog_starts_on_elements_with_the_section_enabled(qtbot, dialog_parts):
    dialog = _dialog(qtbot, dialog_parts)
    assert dialog.applies_to == "elements"
    assert dialog.elements_group_widget.isEnabled()


@pytest.mark.parametrize("direction", ["inputs", "outputs", "both"])
def test_a_broad_choice_disables_elements_and_submits_none(qtbot, dialog_parts, direction):
    dialog = _dialog(qtbot, dialog_parts)
    dialog._add_all_elements()
    assert dialog.elements  # checked while in Elements mode
    dialog.applies_to_group.selected = direction
    assert not dialog.elements_group_widget.isEnabled()
    assert dialog.elements == []  # still checked underneath, never submitted
    dialog.applies_to_group.selected = "elements"
    assert dialog.elements_group_widget.isEnabled()
    assert dialog.elements  # restored on switching back


def test_a_broad_choice_is_accepted_without_any_element(qtbot, dialog_parts, monkeypatch):
    from frontend.src.shared.widgets import message_box

    warnings = []
    monkeypatch.setattr(message_box, "warning", lambda *args, **kwargs: warnings.append(args[1]))
    dialog = _dialog(qtbot, dialog_parts)
    _fill_required(dialog)
    dialog.applies_to_group.selected = "outputs"
    dialog.accept()
    assert warnings == []
    assert dialog.result() == IsolationDialog.DialogCode.Accepted


def test_elements_mode_still_requires_an_element(qtbot, dialog_parts, monkeypatch):
    from frontend.src.shared.widgets import message_box

    warnings = []
    monkeypatch.setattr(message_box, "warning", lambda *args, **kwargs: warnings.append(args[1]))
    dialog = _dialog(qtbot, dialog_parts)
    _fill_required(dialog)
    dialog.accept()
    assert warnings == ["No Elements Selected"]


def test_edit_restores_a_broad_choice(qtbot, dialog_parts):
    initial = _iso(power_domain_id="d1", isolation_supply_set_id="ss1", applies_to="inputs", elements=[], owner_path="top")
    dialog = _dialog(qtbot, dialog_parts, initial=initial)
    assert dialog.applies_to == "inputs"
    assert not dialog.elements_group_widget.isEnabled()


# --- CHANGE 2: the diagram badge ------------------------------------------------


def test_a_broad_strategy_attaches_to_every_boundary_block_of_its_domain():
    """With no elements there is nothing to derive the blocks from -- the
    bug Level Shifter's Applies To All first shipped with (no badge)."""
    domains = {"pd": PowerDomainPlacement(id="pd", owner_path="top/a", owner_paths=["top/a", "top/b"], name="PD", color=_C)}
    broad = _iso(power_domain_id="pd", applies_to="outputs", elements=[])
    assert isolation_attachment_owner_paths(broad, domains) == {"top/a", "top/b"}
    # Element mode is unchanged: the blocks its elements belong to.
    assert isolation_attachment_owner_paths(_iso(elements=["top/a/x"]), domains) == {"top/a"}


@pytest.mark.parametrize("direction, variants", [("inputs", [False]), ("outputs", [True]), ("both", [False, True])])
def test_a_broad_strategys_badge_orientation_follows_its_direction(direction, variants):
    assert isolation_variants_on_block(_iso(applies_to=direction, elements=[]), None, "top/u_sub") == variants


def test_creating_a_broad_isolation_renders_a_badge(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top/u_core"]
    view._domain_state.domains["pd"] = PowerDomainPlacement(
        id="pd", owner_path="top/u_core", owner_paths=["top/u_core"], name="PD_CORE", color=_C)
    view._domain_state.supply_sets["ss"] = SupplySetPlacement(
        id="ss", name="SS", power="VDD", ground="VSS", owner_paths=["top"])
    placement = view.create_isolation(
        box, "ISO_ALL", "pd", "ss", None, None, "low", "parent", "iso_n", "low", [], applies_to="outputs",
    )
    badges = view._isolation_icons[placement.id]
    assert [icon.owner_box.path for icon in badges] == ["top/u_core"]
