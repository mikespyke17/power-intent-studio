"""A module's FULL I/O list: its plain ports plus every signal its
SystemVerilog interface ports expose.

An interface port (`axi_lite_if.mst m`) is not in `Definition.ports` --
it lives in `Definition.interface_ports` -- so anything that lists a
module's I/O from `.ports` alone silently drops every signal reaching
the module through an interface. That is exactly how the Isolation and
Level Shifter element pickers, their badges and the Port Attribute
dialog came to show only part of a module's I/O on a design using
interfaces (`aura_top`), while Port List showed it all. This is the one
expansion every one of them now shares.

Expansion rules -- unchanged from Port List's own, which is where they
came from (hierarchy_view/properties_table_widget.py's module docstring
has the full reasoning):

  - one entry per signal, named `<iface_port>.<signal>`, the same key
    the connectivity tracer / PowerResolutionCache already use, so a
    Driver/Receiver lookup works on it directly;
  - direction from the modport bound at the port, else the inferred
    modport, else (genuinely unconstrained) the interface's own port
    direction, with its internal nets INOUT;
  - an ARRAYED interface port expands per index:
    `mst_port[0].awvalid`, `mst_port[1].awvalid`, ...;
  - a dangling interface/modport reference yields nothing, not a guess.

Each entry is a plain `Port`, so every caller that already handles
`Definition.ports` (direction filters, width, per-bit row keys) handles
interface signals with no special case.
"""

from __future__ import annotations

from typing import Optional

from frontend.src.shared.models.rtl import Definition, Design, InterfacePort, Port, PortDirection


def find_interface_definition(design: Design, interface_name: str) -> Optional[Definition]:
    for candidate in design.definitions.values():
        if candidate.is_interface and candidate.base_name == interface_name:
            return candidate
    return None


def _signal_width(interface_def: Definition, signal_name: str) -> int:
    for port in interface_def.ports:
        if port.name == signal_name:
            return port.width
    for net in interface_def.nets:
        if net.name == signal_name:
            return net.width
    return 1


def interface_signal_ports(design: Design, iface_port: InterfacePort) -> list[Port]:
    """One `Port` per signal `iface_port` exposes (see module docstring)."""
    interface_def = find_interface_definition(design, iface_port.interface_name)
    if interface_def is None:
        return []
    effective_modport = iface_port.modport or iface_port.inferred_modport
    if effective_modport is not None:
        modport = next((m for m in interface_def.modports if m.name == effective_modport), None)
        if modport is None:
            return []
        signals = [(p.name, p.direction) for p in modport.ports]
    else:
        signals = [(p.name, p.direction) for p in interface_def.ports] + [
            (n.name, PortDirection.INOUT) for n in interface_def.nets
        ]
    port_names = (
        [f"{iface_port.name}[{i}]" for i in range(iface_port.array_size)] if iface_port.array_size else [iface_port.name]
    )
    return [
        Port(name=f"{port_name}.{name}", direction=direction, width=_signal_width(interface_def, name))
        for port_name in port_names
        for name, direction in signals
    ]


def module_io_ports(design: Optional[Design], definition: Definition) -> list[Port]:
    """`definition.ports` followed by every interface-exposed signal.
    Without a design (nothing to resolve an interface against) it is
    just `definition.ports`."""
    ports = list(definition.ports)
    if design is not None:
        for iface_port in definition.interface_ports:
            ports.extend(interface_signal_ports(design, iface_port))
    return ports
