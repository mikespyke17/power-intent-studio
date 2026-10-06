"""Connectivity flattening (backend/elaborator/rtl/ir/flatten.py) -- traces
a signal's TRUE driver/receiver(s) across arbitrary hierarchy depth and
combinational pass-through chains. See flatten.py's own module docstring
for the full "why now" / milestone-boundary discussion.

Every case here was first verified empirically (ad-hoc scripts against
real elaborate() output and hand-built Design objects) before being
written as a permanent fixture+test -- two real algorithm bugs were found
and fixed that way (see the "real, confirmed bug" comments inside
flatten.py itself, at the two net_group()-sink-loop call sites this file's
own test_multi_hop_receiver_chain_resolves_to_true_destination and
test_fanout_resolves_every_distinct_sink regression-guard).
"""

from pathlib import Path

from backend.elaborator.rtl.ir.flatten import (
    DriverResult,
    Endpoint,
    ReceiverResult,
    TraceKind,
    flatten_design,
    trace_driver,
    trace_receivers,
)
from backend.elaborator.rtl.ir.schema import (
    ContinuousAssign,
    Definition,
    Design,
    Instance,
    Port,
    PortConnection,
    PortDirection,
    SequentialElement,
    SourceLanguage,
    make_definition_id,
)
from backend.elaborator.rtl.verilog import elaborate

GOLDEN = Path(__file__).parent / "golden_rtl"
PASSTHROUGH_DIR = GOLDEN / "verilog_flatten_passthrough_chain"
REAL_LOGIC_STOP_DIR = GOLDEN / "verilog_flatten_real_logic_stop"
LOOP_DIR = GOLDEN / "verilog_flatten_loop"
MULTI_DRIVER_DIR = GOLDEN / "verilog_flatten_multi_driver"
UNDRIVEN_FLOATING_DIR = GOLDEN / "verilog_flatten_undriven_floating"
FANOUT_DIR = GOLDEN / "verilog_flatten_fanout"


def _elaborate(dir_: Path, files: list[str]):
    return elaborate([str(dir_ / f) for f in files])


# --- multi-hop pass-through (3+ levels) ---------------------------------


def test_multi_hop_driver_chain_resolves_to_true_origin():
    design = _elaborate(PASSTHROUGH_DIR, ["leaf.v", "mid.v", "top.v"])

    for path, signal in [
        ("top", "y"),
        ("top/u_mid", "a"),
        ("top/u_mid", "y"),
        ("top/u_mid/u_leaf", "a"),
        ("top/u_mid/u_leaf", "y"),
    ]:
        result = trace_driver(design, path, signal)
        assert result == DriverResult(TraceKind.RESOLVED, endpoint=Endpoint("top", "a")), (path, signal)


def test_multi_hop_receiver_chain_resolves_to_true_destination():
    design = _elaborate(PASSTHROUGH_DIR, ["leaf.v", "mid.v", "top.v"])

    for path, signal in [
        ("top", "a"),
        ("top/u_mid", "a"),
        ("top/u_mid", "y"),
        ("top/u_mid/u_leaf", "a"),
        ("top/u_mid/u_leaf", "y"),
    ]:
        result = trace_receivers(design, path, signal)
        assert result == ReceiverResult(TraceKind.RESOLVED, endpoints=(Endpoint("top", "y"),)), (path, signal)


def test_child_instance_unconsumed_input_receiver_is_consistent_regardless_of_query_origin():
    """Real, confirmed bug (found validating the golden pass-through-vs-
    real-logic test case, tests/frontend/test_power_resolution_cache.py,
    against BOTH the outer and inner instance's own Port List): a child
    instance's own unconsumed input port (real logic downstream means
    nothing forwards it any further) is a legitimate terminal receiver
    -- but this used to resolve DIFFERENTLY depending on how the trace
    reached it: correctly via the parent's own boundary port (crossing
    into the child via net_group()'s own sink loop), but FLOATING when
    the exact same child endpoint was queried DIRECTLY (e.g. from that
    instance's own Port List tab). The underlying trace result for a
    given net must be identical regardless of which node's own Port
    List is asking."""
    leaf = Definition(
        definition_id="leaf",
        base_name="leaf",
        language=SourceLanguage.VERILOG,
        source_file="leaf.v",
        ports=[Port("in", PortDirection.IN), Port("out", PortDirection.OUT)],
        continuous_assigns=[ContinuousAssign(target="out", source=None)],  # real logic -- `in` forwards nowhere
    )
    top = Definition(
        definition_id="top",
        base_name="top",
        language=SourceLanguage.VERILOG,
        source_file="top.v",
        ports=[Port("in", PortDirection.IN), Port("out", PortDirection.OUT)],
        instances=[Instance("u_leaf", "leaf", [PortConnection("in", "in"), PortConnection("out", "out")])],
    )
    design = Design(definitions={"leaf": leaf, "top": top}, top="top")

    via_parent = trace_receivers(design, "top", "in")
    via_child_directly = trace_receivers(design, "top/u_leaf", "in")

    assert via_parent == ReceiverResult(TraceKind.RESOLVED, endpoints=(Endpoint("top/u_leaf", "in"),))
    assert via_child_directly == via_parent


# --- sequential elements: a flop is a terminal DRIVER of its own output ---
# Real, confirmed gap (found tracing a cross-hierarchy signal backward all
# the way up to a flop's own output net): a flop DRIVES its own
# output_signal -- that's the entire point of a flop -- but nothing marked
# it as a driving entity on the DRIVER side (only the receiver side
# treated clock_ref/reset_ref/set_ref/data_ref as legitimate receivers).
# Mirrors how a black-box macro's own output pin is already always its own
# terminal driver.


def test_sequential_element_output_signal_is_a_resolved_terminal_driver():
    top = Definition(
        definition_id="top",
        base_name="top",
        language=SourceLanguage.VERILOG,
        source_file="top.v",
        ports=[Port("clk", PortDirection.IN)],
        sequential_elements=[SequentialElement(output_signal="q", is_latch=False, clock_ref="clk")],
    )
    design = Design(definitions={"top": top}, top="top")

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
        source_file="sink.v",
        ports=[Port("in", PortDirection.IN)],
    )
    top = Definition(
        definition_id="top",
        base_name="top",
        language=SourceLanguage.VERILOG,
        source_file="top.v",
        ports=[Port("clk", PortDirection.IN)],
        sequential_elements=[SequentialElement(output_signal="q_int", is_latch=False, clock_ref="clk")],
        instances=[Instance("u_sink", "sink", [PortConnection("in", "q_int")])],
    )
    design = Design(definitions={"sink": sink, "top": top}, top="top")

    result = trace_driver(design, "top/u_sink", "in")
    assert result == DriverResult(TraceKind.RESOLVED, endpoint=Endpoint("top", "q_int"))


# --- real logic partway through a pass-through-shaped chain -------------


def test_real_logic_partway_through_chain_stops_the_trace_there():
    """mid's own `b = ~a` is real logic, not pass-through -- the driver
    trace from top.y must stop at mid's own `b`, not continue tracing
    through it back to `a` at mid or top."""
    design = _elaborate(REAL_LOGIC_STOP_DIR, ["leaf.v", "mid.v", "top.v"])

    result = trace_driver(design, "top", "y")
    assert result == DriverResult(TraceKind.RESOLVED, endpoint=Endpoint("top/u_mid", "b"))


# --- combinational loop --------------------------------------------------


def test_combinational_loop_is_detected_with_correct_cycle():
    design = _elaborate(LOOP_DIR, ["top.v"])

    result = trace_driver(design, "top", "a")
    assert result.kind == TraceKind.LOOP
    assert result.cycle is not None
    # the cycle must actually revisit the starting endpoint
    assert result.cycle[0] == result.cycle[-1] == Endpoint("top", "a")


def test_flatten_design_surfaces_loop_as_a_connectivity_warning():
    design = _elaborate(LOOP_DIR, ["top.v"])

    connectivity = flatten_design(design)
    loop_warnings = [w for w in connectivity.warnings if w.kind == TraceKind.LOOP]
    assert loop_warnings, connectivity.warnings


# --- multiple drivers ------------------------------------------------


def test_multiple_drivers_is_detected_with_both_candidates():
    design = _elaborate(MULTI_DRIVER_DIR, ["top.v"])

    result = trace_driver(design, "top", "y")
    assert result.kind == TraceKind.MULTIPLE_DRIVERS
    assert result.candidates is not None
    assert set(result.candidates) == {Endpoint("top", "a"), Endpoint("top", "b")}


def test_flatten_design_surfaces_multiple_drivers_as_a_connectivity_warning():
    design = _elaborate(MULTI_DRIVER_DIR, ["top.v"])

    connectivity = flatten_design(design)
    md_warnings = [w for w in connectivity.warnings if w.kind == TraceKind.MULTIPLE_DRIVERS]
    assert md_warnings
    assert md_warnings[0].endpoint == Endpoint("top", "y")


# --- undriven / floating -------------------------------------------------


def test_undriven_internal_net_is_reported_undriven():
    design = _elaborate(UNDRIVEN_FLOATING_DIR, ["top.v"])

    result = trace_driver(design, "top", "undriven_sig")
    assert result.kind == TraceKind.UNDRIVEN


def test_unconsumed_internal_net_is_reported_floating():
    design = _elaborate(UNDRIVEN_FLOATING_DIR, ["top.v"])

    result = trace_receivers(design, "top", "unused_sig")
    assert result.kind == TraceKind.FLOATING


# --- fan-out: multiple sinks ----------------------------------------------


def test_fanout_resolves_every_distinct_sink():
    design = _elaborate(FANOUT_DIR, ["sink.v", "top.v"])

    result = trace_receivers(design, "top", "a")
    assert result.kind == TraceKind.RESOLVED
    assert set(result.endpoints) == {
        Endpoint("top/u_s0", "a"),
        Endpoint("top/u_s1", "a"),
        Endpoint("top/u_s2", "a"),
    }


# --- macro (black-box) endpoints ------------------------------------------
# Verilog's own front-end never produces is_black_box=True Definitions --
# that's the separate .lib/Milestone-1e pipeline -- so this is a hand-built
# Design, the same "directly test the algorithm against a shape the real
# front-end can't itself produce" precedent already used elsewhere in this
# suite (golden_ir_fixtures.py).


def _hand_built_design_with_macro() -> Design:
    macro = Definition(
        definition_id=make_definition_id("SRAM", {}),
        base_name="SRAM",
        language=SourceLanguage.VERILOG,
        source_file="macro.lib",
        is_black_box=True,
        ports=[Port("a", PortDirection.IN), Port("y", PortDirection.OUT)],
    )
    top = Definition(
        definition_id=make_definition_id("top", {}),
        base_name="top",
        language=SourceLanguage.VERILOG,
        source_file="top.v",
        ports=[Port("a", PortDirection.IN), Port("y", PortDirection.OUT)],
        instances=[
            Instance(
                name="u_macro",
                definition_id=macro.definition_id,
                connections=[PortConnection("a", "a"), PortConnection("y", "y")],
            )
        ],
    )
    return Design(definitions={macro.definition_id: macro, top.definition_id: top}, top=top.definition_id)


def test_macro_is_a_resolved_driver_endpoint_not_traced_into():
    design = _hand_built_design_with_macro()

    result = trace_driver(design, "top", "y")
    assert result == DriverResult(TraceKind.RESOLVED, endpoint=Endpoint("top/u_macro", "y"))


def test_macro_is_a_resolved_receiver_endpoint_not_traced_into():
    design = _hand_built_design_with_macro()

    result = trace_receivers(design, "top", "a")
    assert result == ReceiverResult(TraceKind.RESOLVED, endpoints=(Endpoint("top/u_macro", "a"),))


# --- macro pin Driver/Receiver Supply Set direction-awareness ------------
# Real, confirmed bug (found via real design use, Port List's own
# Driver/Receiver Supply Set columns): trace_driver()/trace_receivers()
# used to short-circuit EVERY macro pin, in BOTH directions, to the
# macro's own endpoint -- meaning an INPUT pin's Driver column and
# Receiver column showed the identical value (the macro itself), instead
# of the Driver column tracing backward to the real external driver. The
# fix (see _trace_driver()/_trace_receivers()'s own is_black_box
# handling) is direction-aware: the side FACING THE MACRO ITSELF (input
# -> its own receiver role; output -> its own driver role) stays
# terminal; the side FACING THE REST OF THE DESIGN (input -> who drives
# it; output -> who receives it) is now genuinely integration-traced,
# through the exact same net_group()/ascension machinery (and its
# Undriven/MultipleDrivers/Loop/fan-out handling) already used for every
# non-macro signal.


def _hand_built_design_with_wired_macro() -> Design:
    """top -> u_drv (real logic, drives ce_net) -> u_macro (SRAM, CE=IN
    from ce_net, DOUT=OUT to dout_net) -> fans out to TWO sinks
    (u_snk0/u_snk1), both consuming dout_net -- exercises the fix's
    driver-side ascension AND receiver-side multi-sink fan-out/dedup in
    one fixture."""
    macro = Definition(
        definition_id=make_definition_id("SRAM", {}),
        base_name="SRAM",
        language=SourceLanguage.VERILOG,
        source_file="macro.lib",
        is_black_box=True,
        ports=[Port("CE", PortDirection.IN), Port("DOUT", PortDirection.OUT), Port("VDD", PortDirection.PG)],
    )
    driver = Definition(
        definition_id=make_definition_id("drv", {}),
        base_name="drv",
        language=SourceLanguage.VERILOG,
        source_file="drv.v",
        ports=[Port("out_sig", PortDirection.OUT)],
        continuous_assigns=[ContinuousAssign(target="out_sig", source=None)],
    )
    sink = Definition(
        definition_id=make_definition_id("snk", {}),
        base_name="snk",
        language=SourceLanguage.VERILOG,
        source_file="snk.v",
        ports=[Port("in_sig", PortDirection.IN)],
    )
    top = Definition(
        definition_id=make_definition_id("top", {}),
        base_name="top",
        language=SourceLanguage.VERILOG,
        source_file="top.v",
        instances=[
            Instance("u_drv", driver.definition_id, [PortConnection("out_sig", "ce_net")]),
            Instance("u_macro", macro.definition_id, [PortConnection("CE", "ce_net"), PortConnection("DOUT", "dout_net")]),
            Instance("u_snk0", sink.definition_id, [PortConnection("in_sig", "dout_net")]),
            Instance("u_snk1", sink.definition_id, [PortConnection("in_sig", "dout_net")]),
        ],
    )
    return Design(
        definitions={d.definition_id: d for d in (macro, driver, sink, top)},
        top=top.definition_id,
    )


def test_macro_input_pin_receiver_is_the_macro_itself_but_driver_is_traced_externally():
    design = _hand_built_design_with_wired_macro()

    # Facing the macro (it's the one receiving CE) -- stays terminal.
    assert trace_receivers(design, "top/u_macro", "CE") == ReceiverResult(TraceKind.RESOLVED, endpoints=(Endpoint("top/u_macro", "CE"),))
    # Facing the rest of the design -- traced back to the real driver, not the macro itself.
    assert trace_driver(design, "top/u_macro", "CE") == DriverResult(TraceKind.RESOLVED, endpoint=Endpoint("top/u_drv", "out_sig"))


def test_macro_output_pin_driver_is_the_macro_itself_but_receivers_are_traced_externally():
    design = _hand_built_design_with_wired_macro()

    # Facing the macro (it's the one driving DOUT) -- stays terminal.
    assert trace_driver(design, "top/u_macro", "DOUT") == DriverResult(TraceKind.RESOLVED, endpoint=Endpoint("top/u_macro", "DOUT"))
    # Facing the rest of the design -- traced forward to BOTH real sinks, deduplicated fan-out.
    result = trace_receivers(design, "top/u_macro", "DOUT")
    assert result.kind == TraceKind.RESOLVED
    assert set(result.endpoints) == {Endpoint("top/u_snk0", "in_sig"), Endpoint("top/u_snk1", "in_sig")}


def test_macro_pg_pin_is_unaffected_stays_terminal_both_directions():
    """A PG pin has no PortDirection.IN/OUT identity (it's PG) -- must
    fall through neither branch's "!= IN"/"!= OUT" exemption and stay
    exactly as before: terminal in both directions."""
    design = _hand_built_design_with_wired_macro()

    assert trace_driver(design, "top/u_macro", "VDD") == DriverResult(TraceKind.RESOLVED, endpoint=Endpoint("top/u_macro", "VDD"))
    assert trace_receivers(design, "top/u_macro", "VDD") == ReceiverResult(TraceKind.RESOLVED, endpoints=(Endpoint("top/u_macro", "VDD"),))


# --- flatten_design(): the full batched pass ------------------------------


def test_flatten_design_covers_every_port_of_every_node_with_no_warnings_on_a_clean_design():
    design = _elaborate(PASSTHROUGH_DIR, ["leaf.v", "mid.v", "top.v"])

    connectivity = flatten_design(design)
    expected_endpoints = {
        Endpoint("top", "a"),
        Endpoint("top", "y"),
        Endpoint("top/u_mid", "a"),
        Endpoint("top/u_mid", "y"),
        Endpoint("top/u_mid/u_leaf", "a"),
        Endpoint("top/u_mid/u_leaf", "y"),
    }
    assert set(connectivity.drivers.keys()) == expected_endpoints
    assert set(connectivity.receivers.keys()) == expected_endpoints
    assert connectivity.warnings == ()
    assert all(dr.kind == TraceKind.RESOLVED for dr in connectivity.drivers.values())
    assert all(rr.kind == TraceKind.RESOLVED for rr in connectivity.receivers.values())
