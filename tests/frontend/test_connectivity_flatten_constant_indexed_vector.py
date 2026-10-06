"""Constant-indexed vector bit-select net_group() fix
(power_intent_view/connectivity_flatten.py) -- mirrors
tests/backend/test_verilog_constant_indexed_vector.py's own hand-built
shape, against this module's own mocked rtl.Design instead of a real
elaborate() call.

Real, confirmed gap found via real `aura` design use (`clk_rst_gen`'s own
per-domain `clk_dom[N_DOMAIN-1:0]` clock gating): a bit-indexed net_group
query ("clk_dom[0]") can never exact-string-match a WHOLE-vector
connection ("clk_dom", the consuming instantiation's own
`.clk_dom(clk_dom)` binding) -- net_group() now merges in whole-vector
members too, re-attaching the same index suffix.
"""

from frontend.src.power_intent_view.connectivity_flatten import TraceKind, trace_driver
from frontend.src.shared.models.rtl import ContinuousAssign, Definition, Design, Instance, Port, PortConnection, PortDirection, SourceLanguage


def _clk_gen() -> Definition:
    return Definition(
        definition_id="clk_gen",
        base_name="clk_gen",
        language=SourceLanguage.SYSTEMVERILOG,
        ports=[Port("clk_ref", PortDirection.IN), Port("en", PortDirection.IN), Port("clk_dom", PortDirection.OUT)],
        continuous_assigns=[
            ContinuousAssign(target="clk_dom[0]", source=None, referenced_signals=("clk_ref", "en")),
            ContinuousAssign(target="clk_dom[1]", source=None, referenced_signals=("clk_ref", "en")),
        ],
    )


def _consumer() -> Definition:
    return Definition(definition_id="consumer", base_name="consumer", language=SourceLanguage.SYSTEMVERILOG, ports=[Port("clk", PortDirection.IN)])


def _top() -> Design:
    top = Definition(
        definition_id="top",
        base_name="top",
        language=SourceLanguage.SYSTEMVERILOG,
        ports=[Port("clk_ref", PortDirection.IN), Port("en0", PortDirection.IN), Port("en1", PortDirection.IN)],
        instances=[
            Instance("u_gen", "clk_gen", [PortConnection("clk_ref", "clk_ref"), PortConnection("en", None), PortConnection("clk_dom", "clk_dom")]),
            Instance("u_cons_a", "consumer", [PortConnection("clk", "clk_dom[0]")]),
            Instance("u_cons_b", "consumer", [PortConnection("clk", "clk_dom[1]")]),
        ],
    )
    return Design(definitions={"clk_gen": _clk_gen(), "consumer": _consumer(), "top": top}, top="top")


def test_each_consumer_resolves_to_its_own_distinct_bit_not_conflated():
    design = _top()

    driver_a = trace_driver(design, "top/u_cons_a", "clk")
    driver_b = trace_driver(design, "top/u_cons_b", "clk")

    assert driver_a.kind == TraceKind.RESOLVED
    assert driver_a.endpoint.path == "top/u_gen"
    assert driver_a.endpoint.signal == "clk_dom[0]"

    assert driver_b.kind == TraceKind.RESOLVED
    assert driver_b.endpoint.path == "top/u_gen"
    assert driver_b.endpoint.signal == "clk_dom[1]"

    assert driver_a.endpoint != driver_b.endpoint
