"""Multi-hop interface-PORT-forwarding trace fix
(power_intent_view/connectivity_flatten.py) -- mirrors
tests/backend/test_verilog_interface_forwarding_chain.py's own hand-built
shape, against this module's own mocked rtl.Design instead of a real
elaborate() call.

Real, confirmed gap found via real `aura` design use (`dsp_mem`'s own
`.bus_a(wr_bus)`, `wr_bus` ITSELF an interface port, forwarded another
level up to a genuinely local instance `mem_wr` in `dsp_mem`'s own
PARENT): `_trace_driver_interface_member()`/`_trace_receivers_interface_member()`
used to unconditionally descend into "parent_path/target" as if `target`
were a concrete child instance, fabricating a hierarchy path no real node
exists at whenever `target` is itself a forwarding PORT of the parent
scope rather than a local instance.
"""

from frontend.src.power_intent_view.connectivity_flatten import TraceKind, trace_driver
from frontend.src.shared.models.rtl import (
    ContinuousAssign,
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


def _bus_if() -> Definition:
    return Definition(
        definition_id="bus_if",
        base_name="bus_if",
        language=SourceLanguage.SYSTEMVERILOG,
        is_interface=True,
        ports=[Port("clk", PortDirection.IN), Port("rst_n", PortDirection.IN)],
        modports=[Modport("mem", ports=[ModportPort("clk", PortDirection.IN), ModportPort("rst_n", PortDirection.IN), ModportPort("req", PortDirection.IN)])],
    )


def _leaf() -> Definition:
    return Definition(definition_id="leaf", base_name="leaf", language=SourceLanguage.SYSTEMVERILOG, ports=[Port("sig", PortDirection.IN)])


def _mid() -> Definition:
    return Definition(
        definition_id="mid",
        base_name="mid",
        language=SourceLanguage.SYSTEMVERILOG,
        interface_ports=[InterfacePort(name="bus", interface_name="bus_if", modport="mem")],
        instances=[Instance("u_leaf", "leaf", [PortConnection("sig", "bus.req")])],
    )


def _outer() -> Definition:
    return Definition(
        definition_id="outer",
        base_name="outer",
        language=SourceLanguage.SYSTEMVERILOG,
        # `outer`'s own "bus" port PURELY forwards to `mid`'s own "bus"
        # port -- mirroring `dsp_mem`'s own `wr_bus`, a forwarding port,
        # not a concrete local instance.
        interface_ports=[InterfacePort(name="bus", interface_name="bus_if", modport="mem")],
        instances=[Instance("u_mid", "mid", [PortConnection("bus", "bus")])],
    )


def _top() -> Design:
    top = Definition(
        definition_id="top",
        base_name="top",
        language=SourceLanguage.SYSTEMVERILOG,
        ports=[Port("clk", PortDirection.IN), Port("rst_n", PortDirection.IN), Port("a", PortDirection.IN), Port("b", PortDirection.IN)],
        instances=[
            Instance("bus", "bus_if", [PortConnection("clk", "clk"), PortConnection("rst_n", "rst_n")]),
            Instance("u_outer", "outer", [PortConnection("bus", "bus")]),
        ],
        continuous_assigns=[ContinuousAssign(target="bus.req", source=None, referenced_signals=("a", "b"))],
    )
    return Design(definitions={"bus_if": _bus_if(), "leaf": _leaf(), "mid": _mid(), "outer": _outer(), "top": top}, top="top")


def test_driver_traces_through_both_forwarding_hops_to_the_real_local_instance():
    design = _top()
    leaf_path = "top/u_outer/u_mid/u_leaf"

    result = trace_driver(design, leaf_path, "sig")
    assert result.kind == TraceKind.RESOLVED
    assert result.endpoint.path == "top"
    assert result.endpoint.signal == "bus.req"
