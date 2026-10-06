"""Canned mock RTL design -- fakes what backend/elaborator/rtl/'s
elaborate() would produce, in the frontend's own mirrored Design shape
(shared/models/rtl.py). No parsing, no real elaboration -- literal,
hand-built data, used by the mocked "Elaborate" button.

Shape chosen to give the power-intent overlay (power_intent_view/) real
variety to render against: a domain-eligible instance with several ports
(for pin-level isolation/level-shifter badges), an instance with no ports
worth marking (for the instance-level retention badge), and a top-level
port (for a switch control_port). `core` also carries one register and
one latch, so the Properties panel's Sequential tab has real demo
content. `u_ram` is a black-box macro instance (is_black_box=True, no
children -- see backend/elaborator/lib/elaborate.py) with PG/PG_INTERNAL
ports, so the PortList tab's PG filter category has something to show;
see PortDirection.PG's own docstring for why this is a mock-only
distinction the real backend can't produce yet.

`simple_bus_if` (an is_interface=True Definition with two modports) and
`u_bus` (an instance of it, referenced by `core`'s own `bus` interface
port with modport "consumer") give the Hierarchy Tree/Diagram/PortList
real interface content to exercise: interface *instances* like `u_bus`
are excluded from the Tree and Diagram entirely (interfaces are wiring
bundles, not blocks of logic -- see hierarchy_tree_widget.py/
hierarchy_scene_builder.py), while an interface-typed *port* like
`core.bus` surfaces as extra rows in the PortList tab instead, one per
signal the bound modport exposes.

`fwd` (a `core`-adjacent leaf, definition_id "fwd") carries a genuinely
unconstrained interface port ("bus", modport=None) with
`inferred_modport="producer"` set directly -- standing in for what the
real backend's forwarded-modport inference (see
InterfacePort.inferred_modport's own docstring) would compute for a
module that forwards an unconstrained interface port straight through to
a modport-qualified child; this mock hand-sets the *result* rather than
modeling the forwarding wiring itself, same precedent as `core.bus`
hand-setting a modport directly instead of modeling a real bind.
"""

from __future__ import annotations

from frontend.src.shared.models.rtl import (
    Definition,
    Design,
    Instance,
    InterfacePort,
    Modport,
    ModportPort,
    Net,
    Port,
    PortConnection,
    PortDirection,
    SequentialElement,
    SourceLanguage,
)


def build_mock_design() -> Design:
    simple_bus_if = Definition(
        definition_id="simple_bus_if",
        base_name="simple_bus_if",
        language=SourceLanguage.SYSTEMVERILOG,
        is_interface=True,
        ports=[Port(name="clk", direction=PortDirection.IN)],
        nets=[Net(name="data", width=8), Net(name="valid")],
        modports=[
            Modport(name="producer", ports=[ModportPort(name="data", direction=PortDirection.OUT), ModportPort(name="valid", direction=PortDirection.OUT)]),
            Modport(name="consumer", ports=[ModportPort(name="data", direction=PortDirection.IN), ModportPort(name="valid", direction=PortDirection.IN)]),
        ],
    )
    core = Definition(
        definition_id="core",
        base_name="core",
        language=SourceLanguage.VERILOG,
        ports=[
            Port(name="clk", direction=PortDirection.IN),
            Port(name="out1", direction=PortDirection.OUT),
            Port(name="out2", direction=PortDirection.OUT),
            Port(name="out3", direction=PortDirection.OUT),
        ],
        nets=[Net(name="internal_n1")],
        sequential_elements=[
            SequentialElement(output_signal="out1", is_latch=False),
            SequentialElement(output_signal="internal_n1", is_latch=True),
        ],
        interface_ports=[InterfacePort(name="bus", interface_name="simple_bus_if", modport="consumer")],
    )
    dbg = Definition(
        definition_id="dbg",
        base_name="dbg",
        language=SourceLanguage.VERILOG,
        ports=[Port(name="clk", direction=PortDirection.IN)],
    )
    fwd = Definition(
        definition_id="fwd",
        base_name="fwd",
        language=SourceLanguage.VERILOG,
        interface_ports=[InterfacePort(name="bus", interface_name="simple_bus_if", modport=None, inferred_modport="producer")],
    )
    ram_macro = Definition(
        definition_id="ram_macro",
        base_name="RAM_MACRO",
        language=SourceLanguage.LIBERTY,
        is_black_box=True,
        ports=[
            Port(name="VDD", direction=PortDirection.PG, voltage="0.80V"),
            Port(name="VSS", direction=PortDirection.PG_INTERNAL),
            Port(name="CE", direction=PortDirection.IN),
            Port(name="DOUT", direction=PortDirection.OUT),
        ],
    )
    top = Definition(
        definition_id="top",
        base_name="top",
        language=SourceLanguage.VERILOG,
        ports=[
            Port(name="clk", direction=PortDirection.IN),
            Port(name="sleep_n", direction=PortDirection.IN),
            Port(name="out1", direction=PortDirection.OUT),
            Port(name="out2", direction=PortDirection.OUT),
            Port(name="out3", direction=PortDirection.OUT),
        ],
        instances=[
            Instance(
                name="u_core",
                definition_id="core",
                connections=[
                    PortConnection(instance_port="clk", connected_to="clk"),
                    PortConnection(instance_port="out1", connected_to="out1"),
                    PortConnection(instance_port="out2", connected_to="out2"),
                    PortConnection(instance_port="out3", connected_to="out3"),
                    PortConnection(instance_port="bus", connected_to="u_bus"),
                ],
            ),
            Instance(name="u_dbg", definition_id="dbg", connections=[PortConnection(instance_port="clk", connected_to="clk")]),
            Instance(name="u_ram", definition_id="ram_macro", connections=[PortConnection(instance_port="CE", connected_to="clk")]),
            Instance(name="u_bus", definition_id="simple_bus_if", connections=[PortConnection(instance_port="clk", connected_to="clk")]),
            Instance(name="u_fwd", definition_id="fwd", connections=[]),
        ],
    )
    return Design(
        definitions={"top": top, "core": core, "dbg": dbg, "fwd": fwd, "ram_macro": ram_macro, "simple_bus_if": simple_bus_if}, top="top"
    )
