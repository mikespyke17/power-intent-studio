"""power_intent_view/connectivity_flatten.py -- the frontend's own
genuinely-duplicated mirror of backend/elaborator/rtl/ir/flatten.py's
algorithm (see that module's own docstring for why it's a duplicate,
not an import). Hand-built mock Designs throughout, since the frontend
has no real elaborate() to drive -- same "directly-testable core against
hand-built IR" precedent already used elsewhere in this app (e.g.
test_power_intent_diagram_modes.py's own _three_level_nested_design()).
"""

from frontend.src.power_intent_view.connectivity_flatten import (
    DriverResult,
    Endpoint,
    ReceiverResult,
    TraceKind,
    flatten_design,
    trace_driver,
    trace_receivers,
)
from frontend.src.shared.models.rtl import (
    ContinuousAssign,
    Definition,
    Design,
    Instance,
    Port,
    PortConnection,
    PortDirection,
    SequentialElement,
    SourceLanguage,
)


def _passthrough_chain_design() -> Design:
    """top -> u_mid -> u_leaf, `a` (IN) pass-through wired straight
    through to `y` (OUT) at every level -- leaf is the only one with a
    real ContinuousAssign (source="a"); mid/top just forward via plain
    instance connections, exercising net_group()-mediated tracing."""
    leaf = Definition(
        definition_id="leaf",
        base_name="leaf",
        language=SourceLanguage.VERILOG,
        ports=[Port("a", PortDirection.IN), Port("y", PortDirection.OUT)],
        continuous_assigns=[ContinuousAssign(target="y", source="a")],
    )
    mid = Definition(
        definition_id="mid",
        base_name="mid",
        language=SourceLanguage.VERILOG,
        ports=[Port("a", PortDirection.IN), Port("y", PortDirection.OUT)],
        instances=[Instance("u_leaf", "leaf", [PortConnection("a", "a"), PortConnection("y", "y")])],
    )
    top = Definition(
        definition_id="top",
        base_name="top",
        language=SourceLanguage.VERILOG,
        ports=[Port("a", PortDirection.IN), Port("y", PortDirection.OUT)],
        instances=[Instance("u_mid", "mid", [PortConnection("a", "a"), PortConnection("y", "y")])],
    )
    return Design(definitions={"leaf": leaf, "mid": mid, "top": top}, top="top")


def test_multi_hop_driver_chain_resolves_to_true_origin():
    design = _passthrough_chain_design()
    for path, signal in [
        ("top", "y"),
        ("top/u_mid", "a"),
        ("top/u_mid", "y"),
        ("top/u_mid/u_leaf", "a"),
        ("top/u_mid/u_leaf", "y"),
    ]:
        assert trace_driver(design, path, signal) == DriverResult(TraceKind.RESOLVED, endpoint=Endpoint("top", "a")), (path, signal)


def test_multi_hop_receiver_chain_resolves_to_true_destination():
    design = _passthrough_chain_design()
    for path, signal in [
        ("top", "a"),
        ("top/u_mid", "a"),
        ("top/u_mid", "y"),
        ("top/u_mid/u_leaf", "a"),
        ("top/u_mid/u_leaf", "y"),
    ]:
        assert trace_receivers(design, path, signal) == ReceiverResult(TraceKind.RESOLVED, endpoints=(Endpoint("top", "y"),)), (path, signal)


def test_child_instance_unconsumed_input_receiver_is_consistent_regardless_of_query_origin():
    """Real, confirmed bug (found validating the golden pass-through-vs-
    real-logic test case against BOTH the outer and inner instance's own
    Port List): a child instance's own unconsumed input port (real logic
    downstream, e.g. `assign out = ~in;`, means nothing forwards `in`
    any further) is a legitimate terminal receiver -- but this used to
    resolve DIFFERENTLY depending on how the trace reached it: correctly
    via the parent's own boundary port (ascending in, crossing into the
    child via net_group()'s own sink loop), but FLOATING when the exact
    same child endpoint was queried DIRECTLY (e.g. from that instance's
    own Port List tab). The underlying trace result for a given net must
    be identical regardless of which node's own Port List is asking."""
    leaf = Definition(
        definition_id="leaf",
        base_name="leaf",
        language=SourceLanguage.VERILOG,
        ports=[Port("in", PortDirection.IN), Port("out", PortDirection.OUT)],
        continuous_assigns=[ContinuousAssign(target="out", source=None)],  # real logic -- `in` forwards nowhere
    )
    top = Definition(
        definition_id="top",
        base_name="top",
        language=SourceLanguage.VERILOG,
        ports=[Port("in", PortDirection.IN), Port("out", PortDirection.OUT)],
        instances=[Instance("u_leaf", "leaf", [PortConnection("in", "in"), PortConnection("out", "out")])],
    )
    design = Design(definitions={"leaf": leaf, "top": top}, top="top")

    via_parent = trace_receivers(design, "top", "in")
    via_child_directly = trace_receivers(design, "top/u_leaf", "in")

    assert via_parent == ReceiverResult(TraceKind.RESOLVED, endpoints=(Endpoint("top/u_leaf", "in"),))
    assert via_child_directly == via_parent


def test_real_logic_partway_through_chain_stops_the_trace_there():
    """mid's own `b` (source=None, real logic) must stop the driver
    trace right there -- not continue through it back to `a`."""
    leaf = Definition(
        definition_id="leaf",
        base_name="leaf",
        language=SourceLanguage.VERILOG,
        ports=[Port("a", PortDirection.IN), Port("y", PortDirection.OUT)],
        continuous_assigns=[ContinuousAssign(target="y", source="a")],
    )
    mid = Definition(
        definition_id="mid",
        base_name="mid",
        language=SourceLanguage.VERILOG,
        ports=[Port("a", PortDirection.IN), Port("y", PortDirection.OUT)],
        continuous_assigns=[ContinuousAssign(target="b", source=None)],
        instances=[Instance("u_leaf", "leaf", [PortConnection("a", "b"), PortConnection("y", "y")])],
    )
    top = Definition(
        definition_id="top",
        base_name="top",
        language=SourceLanguage.VERILOG,
        ports=[Port("a", PortDirection.IN), Port("y", PortDirection.OUT)],
        instances=[Instance("u_mid", "mid", [PortConnection("a", "a"), PortConnection("y", "y")])],
    )
    design = Design(definitions={"leaf": leaf, "mid": mid, "top": top}, top="top")

    result = trace_driver(design, "top", "y")
    assert result == DriverResult(TraceKind.RESOLVED, endpoint=Endpoint("top/u_mid", "b"))


def test_combinational_loop_is_detected_with_correct_cycle():
    top = Definition(
        definition_id="top",
        base_name="top",
        language=SourceLanguage.VERILOG,
        continuous_assigns=[ContinuousAssign(target="a", source="b"), ContinuousAssign(target="b", source="a")],
    )
    design = Design(definitions={"top": top}, top="top")

    result = trace_driver(design, "top", "a")
    assert result.kind == TraceKind.LOOP
    assert result.cycle is not None
    assert result.cycle[0] == result.cycle[-1] == Endpoint("top", "a")


def test_flatten_design_surfaces_loop_as_a_connectivity_warning_even_when_only_touching_internal_nets():
    """The loop above lives entirely on internal signals never declared
    as a Net or Port -- flatten_design() must still surface it (see
    connectivity_flatten.py's own flatten_design() docstring: ports+nets
    together are a complete scan, since every ContinuousAssign target/
    source is always one or the other)."""
    from frontend.src.shared.models.rtl import Net

    top = Definition(
        definition_id="top",
        base_name="top",
        language=SourceLanguage.VERILOG,
        nets=[Net("a"), Net("b")],
        continuous_assigns=[ContinuousAssign(target="a", source="b"), ContinuousAssign(target="b", source="a")],
    )
    design = Design(definitions={"top": top}, top="top")

    connectivity = flatten_design(design)
    loop_warnings = [w for w in connectivity.warnings if w.kind == TraceKind.LOOP]
    assert loop_warnings, connectivity.warnings


def test_multiple_drivers_is_detected_with_both_candidates():
    top = Definition(
        definition_id="top",
        base_name="top",
        language=SourceLanguage.VERILOG,
        ports=[Port("a", PortDirection.IN), Port("b", PortDirection.IN), Port("y", PortDirection.OUT)],
        continuous_assigns=[ContinuousAssign(target="y", source="a"), ContinuousAssign(target="y", source="b")],
    )
    design = Design(definitions={"top": top}, top="top")

    result = trace_driver(design, "top", "y")
    assert result.kind == TraceKind.MULTIPLE_DRIVERS
    assert set(result.candidates) == {Endpoint("top", "a"), Endpoint("top", "b")}


def test_undriven_internal_net_is_reported_undriven():
    from frontend.src.shared.models.rtl import Net

    top = Definition(
        definition_id="top",
        base_name="top",
        language=SourceLanguage.VERILOG,
        ports=[Port("y", PortDirection.OUT)],
        nets=[Net("undriven_sig")],
        continuous_assigns=[ContinuousAssign(target="y", source="undriven_sig")],
    )
    design = Design(definitions={"top": top}, top="top")

    result = trace_driver(design, "top", "undriven_sig")
    assert result.kind == TraceKind.UNDRIVEN


def test_unconsumed_internal_net_is_reported_floating():
    from frontend.src.shared.models.rtl import Net

    top = Definition(
        definition_id="top",
        base_name="top",
        language=SourceLanguage.VERILOG,
        ports=[Port("a", PortDirection.IN)],
        nets=[Net("unused_sig")],
        continuous_assigns=[ContinuousAssign(target="unused_sig", source="a")],
    )
    design = Design(definitions={"top": top}, top="top")

    result = trace_receivers(design, "top", "unused_sig")
    assert result.kind == TraceKind.FLOATING


def test_fanout_resolves_every_distinct_sink():
    sink = Definition(definition_id="sink", base_name="sink", language=SourceLanguage.VERILOG, ports=[Port("a", PortDirection.IN)])
    top = Definition(
        definition_id="top",
        base_name="top",
        language=SourceLanguage.VERILOG,
        ports=[Port("a", PortDirection.IN)],
        continuous_assigns=[ContinuousAssign(target="link", source="a")],
        instances=[
            Instance("u_s0", "sink", [PortConnection("a", "link")]),
            Instance("u_s1", "sink", [PortConnection("a", "link")]),
            Instance("u_s2", "sink", [PortConnection("a", "link")]),
        ],
    )
    design = Design(definitions={"sink": sink, "top": top}, top="top")

    result = trace_receivers(design, "top", "a")
    assert result.kind == TraceKind.RESOLVED
    assert set(result.endpoints) == {Endpoint("top/u_s0", "a"), Endpoint("top/u_s1", "a"), Endpoint("top/u_s2", "a")}


# --- sequential elements: clock/reset/set/data as legitimate receivers ---
# Found via a real design's own top-level `always_ff` (ContinuousAssign-
# based tracing has zero visibility into procedural blocks at all): a
# flop's own clock/reset/set/data signal is a real receiver -- the flop
# itself consumes it. `data_ref` (mirrored from the backend's own
# reversed "no data-path modeling" decision -- see SequentialElement's
# own docstring) is on the exact same footing as the other three here.


def _design_with_flop() -> Design:
    top = Definition(
        definition_id="top",
        base_name="top",
        language=SourceLanguage.VERILOG,
        ports=[Port("clk", PortDirection.IN), Port("rst_n", PortDirection.IN), Port("d", PortDirection.IN)],
        nets=[],
        sequential_elements=[
            SequentialElement(output_signal="q", is_latch=False, clock_ref="clk", reset_ref="rst_n", data_ref="d"),
        ],
    )
    return Design(definitions={"top": top}, top="top")


def test_sequential_element_clock_ref_resolves_to_the_flop_itself():
    design = _design_with_flop()
    result = trace_receivers(design, "top", "clk")
    assert result == ReceiverResult(TraceKind.RESOLVED, endpoints=(Endpoint("top", "q"),))


def test_sequential_element_reset_ref_resolves_to_the_flop_itself():
    design = _design_with_flop()
    result = trace_receivers(design, "top", "rst_n")
    assert result == ReceiverResult(TraceKind.RESOLVED, endpoints=(Endpoint("top", "q"),))


def test_sequential_element_data_ref_resolves_to_the_flop_itself():
    design = _design_with_flop()
    result = trace_receivers(design, "top", "d")
    assert result == ReceiverResult(TraceKind.RESOLVED, endpoints=(Endpoint("top", "q"),))


def test_signal_not_referenced_by_any_sequential_element_stays_floating():
    top = Definition(
        definition_id="top",
        base_name="top",
        language=SourceLanguage.VERILOG,
        ports=[Port("clk", PortDirection.IN), Port("unrelated", PortDirection.IN)],
        sequential_elements=[SequentialElement(output_signal="q", is_latch=False, clock_ref="clk", data_ref="d")],
    )
    design = Design(definitions={"top": top}, top="top")
    result = trace_receivers(design, "top", "unrelated")
    assert result.kind == TraceKind.FLOATING


# --- sequential elements: a flop is a terminal DRIVER of its own output ---
# Real, confirmed gap (found tracing a cross-hierarchy signal backward all
# the way up to a flop's own output net, e.g. a receiving instance's own
# input port -> the flop that ultimately produces it): a flop DRIVES its
# own output_signal -- that's the entire point of a flop -- but nothing
# marked it as a driving entity on the DRIVER side (only the receiver side
# treated clock_ref/reset_ref/set_ref/data_ref as legitimate receivers).
# Mirrors how a black-box macro's own output pin is already always its own
# terminal driver.


def test_sequential_element_output_signal_is_a_resolved_terminal_driver():
    design = _design_with_flop()
    result = trace_driver(design, "top", "q")
    assert result == DriverResult(TraceKind.RESOLVED, endpoint=Endpoint("top", "q"))


def test_cross_hierarchy_backward_trace_lands_on_the_flop_not_undriven():
    """The specific shape that surfaced the gap: a child instance's own
    input, wired up through the parent's own connection to a net that's
    actually a flop's own output -- the backward trace must correctly
    ascend AND correctly recognize the flop as the true driver, not
    report UNDRIVEN despite having found the right net."""
    sink = Definition(
        definition_id="sink",
        base_name="sink",
        language=SourceLanguage.VERILOG,
        ports=[Port("in", PortDirection.IN)],
    )
    top = Definition(
        definition_id="top",
        base_name="top",
        language=SourceLanguage.VERILOG,
        ports=[Port("clk", PortDirection.IN)],
        sequential_elements=[SequentialElement(output_signal="q_int", is_latch=False, clock_ref="clk")],
        instances=[Instance("u_sink", "sink", [PortConnection("in", "q_int")])],
    )
    design = Design(definitions={"sink": sink, "top": top}, top="top")

    result = trace_driver(design, "top/u_sink", "in")
    assert result == DriverResult(TraceKind.RESOLVED, endpoint=Endpoint("top", "q_int"))


def _design_with_macro() -> Design:
    macro = Definition(
        definition_id="SRAM",
        base_name="SRAM",
        language=SourceLanguage.LIBERTY,
        is_black_box=True,
        ports=[Port("a", PortDirection.IN), Port("y", PortDirection.OUT)],
    )
    top = Definition(
        definition_id="top",
        base_name="top",
        language=SourceLanguage.VERILOG,
        ports=[Port("a", PortDirection.IN), Port("y", PortDirection.OUT)],
        instances=[Instance("u_macro", "SRAM", [PortConnection("a", "a"), PortConnection("y", "y")])],
    )
    return Design(definitions={"SRAM": macro, "top": top}, top="top")


def test_macro_is_a_resolved_driver_endpoint_not_traced_into():
    design = _design_with_macro()
    result = trace_driver(design, "top", "y")
    assert result == DriverResult(TraceKind.RESOLVED, endpoint=Endpoint("top/u_macro", "y"))


def test_macro_is_a_resolved_receiver_endpoint_not_traced_into():
    design = _design_with_macro()
    result = trace_receivers(design, "top", "a")
    assert result == ReceiverResult(TraceKind.RESOLVED, endpoints=(Endpoint("top/u_macro", "a"),))


# --- macro pin Driver/Receiver direction-awareness ------------------------
# Ported from the real bug fixed in the backend module (found via real
# design use, Port List's own Driver/Receiver Supply Set columns): the
# side FACING THE MACRO ITSELF (input -> its own receiver role; output ->
# its own driver role) stays terminal; the side FACING THE REST OF THE
# DESIGN (input -> who drives it; output -> who receives it) is now
# genuinely integration-traced.


def _design_with_wired_macro() -> Design:
    macro = Definition(
        definition_id="SRAM",
        base_name="SRAM",
        language=SourceLanguage.LIBERTY,
        is_black_box=True,
        ports=[Port("CE", PortDirection.IN), Port("DOUT", PortDirection.OUT), Port("VDD", PortDirection.PG)],
    )
    driver = Definition(
        definition_id="drv",
        base_name="drv",
        language=SourceLanguage.VERILOG,
        ports=[Port("out_sig", PortDirection.OUT)],
        continuous_assigns=[ContinuousAssign(target="out_sig", source=None)],
    )
    sink = Definition(
        definition_id="snk",
        base_name="snk",
        language=SourceLanguage.VERILOG,
        ports=[Port("in_sig", PortDirection.IN)],
    )
    top = Definition(
        definition_id="top",
        base_name="top",
        language=SourceLanguage.VERILOG,
        instances=[
            Instance("u_drv", "drv", [PortConnection("out_sig", "ce_net")]),
            Instance("u_macro", "SRAM", [PortConnection("CE", "ce_net"), PortConnection("DOUT", "dout_net")]),
            Instance("u_snk0", "snk", [PortConnection("in_sig", "dout_net")]),
            Instance("u_snk1", "snk", [PortConnection("in_sig", "dout_net")]),
        ],
    )
    return Design(definitions={"SRAM": macro, "drv": driver, "snk": sink, "top": top}, top="top")


def test_macro_input_pin_receiver_is_the_macro_itself_but_driver_is_traced_externally():
    design = _design_with_wired_macro()

    assert trace_receivers(design, "top/u_macro", "CE") == ReceiverResult(TraceKind.RESOLVED, endpoints=(Endpoint("top/u_macro", "CE"),))
    assert trace_driver(design, "top/u_macro", "CE") == DriverResult(TraceKind.RESOLVED, endpoint=Endpoint("top/u_drv", "out_sig"))


def test_macro_output_pin_driver_is_the_macro_itself_but_receivers_are_traced_externally():
    design = _design_with_wired_macro()

    assert trace_driver(design, "top/u_macro", "DOUT") == DriverResult(TraceKind.RESOLVED, endpoint=Endpoint("top/u_macro", "DOUT"))
    result = trace_receivers(design, "top/u_macro", "DOUT")
    assert result.kind == TraceKind.RESOLVED
    assert set(result.endpoints) == {Endpoint("top/u_snk0", "in_sig"), Endpoint("top/u_snk1", "in_sig")}


def test_macro_pg_pin_is_unaffected_stays_terminal_both_directions():
    design = _design_with_wired_macro()

    assert trace_driver(design, "top/u_macro", "VDD") == DriverResult(TraceKind.RESOLVED, endpoint=Endpoint("top/u_macro", "VDD"))
    assert trace_receivers(design, "top/u_macro", "VDD") == ReceiverResult(TraceKind.RESOLVED, endpoints=(Endpoint("top/u_macro", "VDD"),))


def test_flatten_design_covers_every_port_of_every_node_with_no_warnings_on_a_clean_design():
    design = _passthrough_chain_design()
    connectivity = flatten_design(design)
    expected = {
        Endpoint("top", "a"),
        Endpoint("top", "y"),
        Endpoint("top/u_mid", "a"),
        Endpoint("top/u_mid", "y"),
        Endpoint("top/u_mid/u_leaf", "a"),
        Endpoint("top/u_mid/u_leaf", "y"),
    }
    assert set(connectivity.drivers.keys()) == expected
    assert set(connectivity.receivers.keys()) == expected
    assert connectivity.warnings == ()
