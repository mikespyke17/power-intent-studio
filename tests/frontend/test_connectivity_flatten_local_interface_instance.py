"""Trace-priority fix for a LOCALLY-declared interface instance
(power_intent_view/connectivity_flatten.py) -- mirrors
tests/backend/test_verilog_interface_member_connections.py's own
hand-built shape (a bus_if.mem PORT one level down, a plain local bus_if
INSTANCE one level up, both driven through unary logic / plain
continuous assigns), against this module's own mocked rtl.Design instead
of a real elaborate() call.

Real, confirmed bug found via real `aura` design use: `trace_driver()`/
`trace_receivers()` checked "does this dotted signal belong to an
interface PORT" BEFORE checking "does a local continuous assign already
drive/receive this exact (possibly dotted) signal" -- permanently
dead-ending a locally-declared interface instance's own driven/received
member (`top_bank`'s own `bus`, driven via
`assign bus.req = sel & req_in;`) at UNDRIVEN/FLOATING. See
connectivity_flatten.py's own _trace_driver()/_trace_receivers() comments
for the full mechanism writeup.
"""

from frontend.src.power_intent_view.connectivity_flatten import TraceKind, trace_driver, trace_receivers
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
        modports=[
            Modport(
                "mem",
                ports=[
                    ModportPort("clk", PortDirection.IN),
                    ModportPort("rst_n", PortDirection.IN),
                    ModportPort("req", PortDirection.IN),
                    ModportPort("we", PortDirection.IN),
                    ModportPort("rdata", PortDirection.OUT),
                ],
            )
        ],
    )


def _leaf_macro() -> Definition:
    return Definition(
        definition_id="leaf_macro",
        base_name="leaf_macro",
        language=SourceLanguage.SYSTEMVERILOG,
        ports=[
            Port("clk", PortDirection.IN),
            Port("cen", PortDirection.IN),
            Port("wen", PortDirection.IN),
            Port("q", PortDirection.OUT),
        ],
    )


def _mid_wrap() -> Definition:
    return Definition(
        definition_id="mid_wrap",
        base_name="mid_wrap",
        language=SourceLanguage.SYSTEMVERILOG,
        interface_ports=[InterfacePort(name="bus", interface_name="bus_if", modport="mem")],
        instances=[
            Instance(
                "u_leaf",
                "leaf_macro",
                [
                    PortConnection("clk", "bus.clk"),
                    PortConnection("cen", "bus.req"),
                    PortConnection("wen", "bus.we"),
                    PortConnection("q", "q_int"),
                ],
            )
        ],
        continuous_assigns=[ContinuousAssign(target="bus.rdata", source="q_int", referenced_signals=("q_int",))],
    )


def _top_bank() -> Design:
    top = Definition(
        definition_id="top_bank",
        base_name="top_bank",
        language=SourceLanguage.SYSTEMVERILOG,
        ports=[
            Port("clk", PortDirection.IN),
            Port("rst_n", PortDirection.IN),
            Port("sel", PortDirection.IN),
            Port("req_in", PortDirection.IN),
            Port("we_in", PortDirection.IN),
            Port("rdata_out", PortDirection.OUT),
        ],
        instances=[
            Instance("bus", "bus_if", [PortConnection("clk", "clk"), PortConnection("rst_n", "rst_n")]),
            Instance("u_wrap", "mid_wrap", [PortConnection("bus", "bus")]),
        ],
        continuous_assigns=[
            ContinuousAssign(target="bus.req", source=None, referenced_signals=("sel", "req_in")),
            ContinuousAssign(target="bus.we", source="we_in", referenced_signals=("we_in",)),
            ContinuousAssign(target="rdata_out", source="bus.rdata", referenced_signals=("bus.rdata",)),
        ],
    )
    return Design(definitions={"bus_if": _bus_if(), "leaf_macro": _leaf_macro(), "mid_wrap": _mid_wrap(), "top_bank": top}, top="top_bank")


def test_driver_traces_through_unary_logic_and_a_local_interface_instance_to_the_real_origin():
    design = _top_bank()
    macro_path = "top_bank/u_wrap/u_leaf"

    # `bus.req` is driven by REAL logic (`sel & req_in`, source=None) --
    # terminal right there, matching the established "real logic is its
    # own terminal driver, never traced through" precedent.
    cen_driver = trace_driver(design, macro_path, "cen")
    assert cen_driver.kind == TraceKind.RESOLVED
    assert cen_driver.endpoint.path == "top_bank"
    assert cen_driver.endpoint.signal == "bus.req"

    # `bus.we` is a pure PASS-THROUGH of `we_in` -- the trace correctly
    # continues one more hop, all the way to the real top-level input
    # port itself (the design's own boundary).
    wen_driver = trace_driver(design, macro_path, "wen")
    assert wen_driver.kind == TraceKind.RESOLVED
    assert wen_driver.endpoint.path == "top_bank"
    assert wen_driver.endpoint.signal == "we_in"


def test_receiver_traces_from_the_macro_output_through_both_interface_hops_to_the_real_top_level_port():
    design = _top_bank()
    macro_path = "top_bank/u_wrap/u_leaf"

    q_receiver = trace_receivers(design, macro_path, "q")
    assert q_receiver.kind == TraceKind.RESOLVED
    assert len(q_receiver.endpoints) == 1
    assert q_receiver.endpoints[0].path == "top_bank"
    assert q_receiver.endpoints[0].signal == "rdata_out"
