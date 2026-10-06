"""Every I/O list shows a block's SystemVerilog INTERFACE signals too.

THE BUG (found on the `aura_top` design, which connects its hierarchy
almost entirely through interfaces): the Isolation and Level Shifter
element pickers listed a block's I/O from `Definition.ports` alone. An
interface port lives in `Definition.interface_ports`, so every signal
reaching a block through one was missing -- `aura_top/u_cpu` offered 4
I/Os where Port List showed 34. The same `.ports`-only read also blanked
the Direction column and the badge orientation for such signals, and
fed the Port Attribute dialog and the UPF width lookup.

All of them now share shared/models/rtl_io.module_io_ports(), the
expansion Port List already used: `<iface_port>.<signal>` per signal of
the bound modport, `[i]`-indexed for an arrayed interface port.

Built on a small hand-made design rather than on aura_top itself, so it
runs without that out-of-repo tree.
"""

from __future__ import annotations

import pytest
from PySide6.QtGui import QColor

from frontend.src.power_intent_view.isolation_dialog import IsolationDialog
from frontend.src.power_intent_view.isolation_overlay import (
    IsolationDiagramState,
    IsolationStrategyPlacement,
    local_direction_composition,
)
from frontend.src.power_intent_view.level_shifter_dialog import LevelShifterDialog
from frontend.src.power_intent_view.power_domain_overlay import (
    PowerDomainDiagramState,
    PowerDomainPlacement,
    SupplySetPlacement,
)
from frontend.src.power_intent_view.power_intent_tab import PowerIntentTab
from frontend.src.power_intent_view.power_intent_text_export import generate_upf
from frontend.src.power_intent_view.retention_overlay import RetentionDiagramState
from frontend.src.power_intent_view.supply_port_overlay import SupplyPortDiagramState
from frontend.src.shared.models.rtl import (
    Definition,
    Design,
    Instance,
    InterfacePort,
    Modport,
    ModportPort,
    Port,
    PortConnection,
    PortDirection,
    SourceLanguage,
)
from frontend.src.shared.models.rtl_io import module_io_ports

_C = QColor("#ff0000")
SUB = "top/u_sub"


def _design() -> Design:
    """`top` instantiates `u_sub`, whose I/O is one plain port (`clk`),
    a bus interface port bound to modport `mst`, and an ARRAYED one
    (`ch[2]`) bound to modport `dom`."""
    bus_if = Definition(
        definition_id="bus_if", base_name="bus_if", language=SourceLanguage.VERILOG, is_interface=True,
        ports=[Port(name="addr", direction=PortDirection.INOUT, width=32),
               Port(name="valid", direction=PortDirection.INOUT),
               Port(name="rdata", direction=PortDirection.INOUT, width=32)],
        modports=[Modport(name="mst", ports=[
            ModportPort(name="addr", direction=PortDirection.OUT),
            ModportPort(name="valid", direction=PortDirection.OUT),
            ModportPort(name="rdata", direction=PortDirection.IN),
        ])],
    )
    pmu_if = Definition(
        definition_id="pmu_if", base_name="pmu_if", language=SourceLanguage.VERILOG, is_interface=True,
        ports=[Port(name="req", direction=PortDirection.INOUT)],
        modports=[Modport(name="dom", ports=[ModportPort(name="req", direction=PortDirection.IN)])],
    )
    sub = Definition(
        definition_id="sub", base_name="sub", language=SourceLanguage.VERILOG,
        ports=[Port(name="clk", direction=PortDirection.IN)],
        interface_ports=[
            InterfacePort(name="mem", interface_name="bus_if", modport="mst"),
            InterfacePort(name="ch", interface_name="pmu_if", modport="dom", array_size=2),
        ],
    )
    top = Definition(
        definition_id="top", base_name="top", language=SourceLanguage.VERILOG,
        ports=[Port(name="clk", direction=PortDirection.IN)],
        instances=[Instance(name="u_sub", definition_id="sub", connections=[PortConnection(instance_port="clk", connected_to="clk")])],
    )
    return Design(definitions={d.definition_id: d for d in (top, sub, bus_if, pmu_if)}, top="top")


_EXPECTED_SUB_IO = {
    "clk": "IN",
    "mem.addr": "OUT", "mem.valid": "OUT", "mem.rdata": "IN",
    "ch[0].req": "IN", "ch[1].req": "IN",
}


@pytest.fixture
def view(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(_design())
    return tab.diagram._view


def _domains():
    return {"pd": PowerDomainPlacement(id="pd", owner_path=SUB, owner_paths=[SUB], name="PD_SUB", color=_C)}


def _rows(table) -> dict[str, str]:
    return {table.item(r, 0).text().rsplit("/", 1)[1]: table.item(r, 1).text() for r in range(table.rowCount())}


def test_the_shared_expansion_lists_plain_and_interface_io():
    design = _design()
    ports = module_io_ports(design, design.definitions["sub"])
    assert {p.name: p.direction.name for p in ports} == {
        "clk": "IN", "mem.addr": "OUT", "mem.valid": "OUT", "mem.rdata": "IN", "ch[0].req": "IN", "ch[1].req": "IN",
    }
    assert next(p for p in ports if p.name == "mem.addr").width == 32


def test_a_diagram_box_carries_the_full_io(view):
    assert {p.name for p in view._boxes[SUB].io_ports} == set(_EXPECTED_SUB_IO)


def test_the_isolation_dialog_lists_every_io_with_its_direction(qtbot, view):
    supply_sets = {"ss": SupplySetPlacement(id="ss", name="SS", power="VDD", ground="VSS", owner_paths=["top"])}
    dialog = IsolationDialog(view._boxes, _domains(), supply_sets, lambda e: ({}, {}), {}, initial_domain_id="pd")
    qtbot.addWidget(dialog)
    assert _rows(dialog.elements_table) == _EXPECTED_SUB_IO


def test_the_level_shifter_dialog_lists_every_io_with_its_direction(qtbot, view):
    dialog = LevelShifterDialog(view._boxes, _domains()["pd"], lambda e: ({}, {}), {})
    qtbot.addWidget(dialog)
    assert _rows(dialog.elements_table) == _EXPECTED_SUB_IO


def test_an_interface_only_selection_orients_its_badge(view):
    """Before the fix an interface signal resolved to NO direction, so a
    selection of only those could not say it was all-output."""
    iso = IsolationStrategyPlacement(
        id="i", name="ISO", power_domain_id="pd", isolation_supply_set_id="ss", clamp_value="low",
        isolation_control="iso_n", isolation_sense="low", elements=[f"{SUB}/mem.addr", f"{SUB}/mem.valid"],
    )
    assert local_direction_composition(iso, view._boxes[SUB], SUB) == (False, True)


def test_the_port_attribute_dialog_is_given_the_full_io(view, monkeypatch):
    from frontend.src.power_intent_view import power_intent_diagram_view as module

    seen = {}

    class _Dialog:
        def __init__(self, ports, *args, **kwargs):
            seen["ports"] = [p.name for p in ports]

        def exec(self):
            return 0

    monkeypatch.setattr(module, "PortAttributeDialog", _Dialog)
    view._prompt_add_or_edit_port_attributes(view._boxes[SUB])
    assert set(seen["ports"]) == set(_EXPECTED_SUB_IO)


def test_a_multi_bit_interface_signal_gets_star_in_the_upf():
    domain_state = PowerDomainDiagramState(
        supply_sets={"ss": SupplySetPlacement(id="ss", name="SS", power="VDD", ground="VSS", owner_paths=["top"])},
        domains=_domains(),
    )
    iso = IsolationStrategyPlacement(
        id="i", name="ISO", power_domain_id="pd", isolation_supply_set_id="ss", clamp_value="low",
        isolation_control="iso_n", isolation_sense="low",
        elements=[f"{SUB}/mem.addr", f"{SUB}/mem.valid", f"{SUB}/ch[1].req"],
    )
    text = generate_upf(
        SupplyPortDiagramState(), domain_state, RetentionDiagramState(), "3.1",
        isolation_state=IsolationDiagramState(strategies={"i": iso}), rtl_design=_design(),
    )
    assert "        u_sub/ch[1].req \\\n        u_sub/mem.addr[*] \\\n        u_sub/mem.valid \\\n" in text
