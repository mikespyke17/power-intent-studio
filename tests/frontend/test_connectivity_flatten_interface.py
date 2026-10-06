"""Interface-aware connectivity tracing (power_intent_view/connectivity_flatten.py) --
mirrors tests/backend/test_flatten_interface.py's own hand-built shapes
(a single bus_if connected mst<->slv, plus a modport-less crossbar array
port mirroring real aura's own fabric_subsys), against this module's own
mocked rtl.Design instead of a real elaborate() call.
"""

from frontend.src.power_intent_view.connectivity_flatten import DriverResult, Endpoint, ReceiverResult, TraceKind, flatten_design, trace_driver, trace_receivers
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
    SourceLanguage,
)


def _bus_if() -> Definition:
    return Definition(
        definition_id="bus_if",
        base_name="bus_if",
        language=SourceLanguage.SYSTEMVERILOG,
        is_interface=True,
        ports=[Port("clk", PortDirection.IN), Port("rst_n", PortDirection.IN)],
        nets=[Net("data")],
        modports=[
            Modport("mst", ports=[ModportPort("data", PortDirection.OUT)]),
            Modport("slv", ports=[ModportPort("data", PortDirection.IN)]),
        ],
    )


def _leaf_mst() -> Definition:
    return Definition(
        definition_id="leaf_mst",
        base_name="leaf_mst",
        language=SourceLanguage.SYSTEMVERILOG,
        ports=[Port("clk", PortDirection.IN), Port("rst_n", PortDirection.IN)],
        interface_ports=[InterfacePort(name="b", interface_name="bus_if", modport="mst")],
    )


def _leaf_slv() -> Definition:
    return Definition(
        definition_id="leaf_slv",
        base_name="leaf_slv",
        language=SourceLanguage.SYSTEMVERILOG,
        ports=[Port("clk", PortDirection.IN), Port("rst_n", PortDirection.IN)],
        interface_ports=[InterfacePort(name="b", interface_name="bus_if", modport="slv")],
    )


def _single_instance_design() -> Design:
    top = Definition(
        definition_id="top",
        base_name="top",
        language=SourceLanguage.SYSTEMVERILOG,
        ports=[Port("clk", PortDirection.IN), Port("rst_n", PortDirection.IN)],
        instances=[
            Instance("the_bus", "bus_if", [PortConnection("clk", "clk"), PortConnection("rst_n", "rst_n")]),
            Instance("u_mst", "leaf_mst", [PortConnection("clk", "clk"), PortConnection("rst_n", "rst_n"), PortConnection("b", "the_bus")]),
            Instance("u_slv", "leaf_slv", [PortConnection("clk", "clk"), PortConnection("rst_n", "rst_n"), PortConnection("b", "the_bus")]),
        ],
    )
    return Design(definitions={"bus_if": _bus_if(), "leaf_mst": _leaf_mst(), "leaf_slv": _leaf_slv(), "top": top}, top="top")


def _xbar() -> Definition:
    return Definition(
        definition_id="xbar",
        base_name="xbar",
        language=SourceLanguage.SYSTEMVERILOG,
        ports=[Port("clk", PortDirection.IN), Port("rst_n", PortDirection.IN)],
        interface_ports=[InterfacePort(name="mst_port", interface_name="bus_if", modport=None, array_size=2)],
    )


def _array_design() -> Design:
    top = Definition(
        definition_id="top_array",
        base_name="top_array",
        language=SourceLanguage.SYSTEMVERILOG,
        ports=[Port("clk", PortDirection.IN), Port("rst_n", PortDirection.IN)],
        instances=[
            Instance("arr_bus[0]", "bus_if", [PortConnection("clk", "clk"), PortConnection("rst_n", "rst_n")]),
            Instance("arr_bus[1]", "bus_if", [PortConnection("clk", "clk"), PortConnection("rst_n", "rst_n")]),
            Instance(
                "u_xbar",
                "xbar",
                [
                    PortConnection("clk", "clk"),
                    PortConnection("rst_n", "rst_n"),
                    PortConnection("mst_port[0]", "arr_bus[0]"),
                    PortConnection("mst_port[1]", "arr_bus[1]"),
                ],
            ),
            Instance("u_mst0", "leaf_mst", [PortConnection("clk", "clk"), PortConnection("rst_n", "rst_n"), PortConnection("b", "arr_bus[0]")]),
        ],
    )
    return Design(definitions={"bus_if": _bus_if(), "xbar": _xbar(), "leaf_mst": _leaf_mst(), "top_array": top}, top="top_array")


# --- (a) redirect into a bound interface instance -------------------------


def test_module_own_interface_port_signal_redirects_to_the_bound_instance():
    design = _single_instance_design()
    assert trace_driver(design, "top/u_mst", "b.data") == DriverResult(TraceKind.RESOLVED, endpoint=Endpoint("top/u_mst", "b.data"))
    assert trace_receivers(design, "top/u_mst", "b.data") == ReceiverResult(TraceKind.RESOLVED, endpoints=(Endpoint("top/u_slv", "b.data"),))


# --- (b) peer lookup across an interface instance's own siblings ---------


def test_interface_instance_own_internal_net_resolves_via_sibling_peers():
    design = _single_instance_design()
    assert trace_driver(design, "top/the_bus", "data") == DriverResult(TraceKind.RESOLVED, endpoint=Endpoint("top/u_mst", "b.data"))
    assert trace_receivers(design, "top/the_bus", "data") == ReceiverResult(TraceKind.RESOLVED, endpoints=(Endpoint("top/u_slv", "b.data"),))


def test_peer_with_no_resolvable_modport_is_excluded_not_guessed_at():
    design = _array_design()
    assert trace_driver(design, "top_array/arr_bus[0]", "data") == DriverResult(
        TraceKind.RESOLVED, endpoint=Endpoint("top_array/u_mst0", "b.data")
    )
    result = trace_driver(design, "top_array/arr_bus[1]", "data")
    assert result.kind == TraceKind.UNDRIVEN


# --- flatten_design(): per-index enumeration, no cross-index merging -----


def test_flatten_design_enumerates_each_array_index_separately_not_merged():
    design = _array_design()
    connectivity = flatten_design(design)

    ep0 = Endpoint("top_array/u_xbar", "mst_port[0].data")
    ep1 = Endpoint("top_array/u_xbar", "mst_port[1].data")
    assert connectivity.drivers[ep0].kind == TraceKind.RESOLVED
    assert connectivity.drivers[ep1].kind == TraceKind.UNDRIVEN
    assert Endpoint("top_array/u_xbar", "mst_port.data") not in connectivity.drivers


def test_flatten_design_end_to_end_on_the_single_instance_fixture():
    design = _single_instance_design()
    connectivity = flatten_design(design)

    assert connectivity.drivers[Endpoint("top/u_mst", "b.data")] == DriverResult(TraceKind.RESOLVED, endpoint=Endpoint("top/u_mst", "b.data"))
    assert connectivity.receivers[Endpoint("top/u_mst", "b.data")] == ReceiverResult(TraceKind.RESOLVED, endpoints=(Endpoint("top/u_slv", "b.data"),))
