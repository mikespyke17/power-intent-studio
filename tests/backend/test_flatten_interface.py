"""Interface-aware connectivity tracing (backend/elaborator/rtl/ir/flatten.py) --
found and root-caused via real `aura` design use (a real SoC that uses SV
interfaces as its PRIMARY connectivity mechanism, per this project's own
"SystemVerilog interfaces" history): net_group() only ever matched a
child instance's connection against `inst_defn.ports`, silently skipping
every interface-typed connection -- an interface-mediated signal was
completely invisible to trace_driver()/trace_receivers(). Fixed via two
new mechanisms (see flatten.py's own "Interface-aware tracing" section):
(a) redirecting a compound "iface_port.signal" reference into the bound
interface instance, (b) a peer lookup across an interface instance's own
parent-scope siblings, using each one's own modport-derived direction.

Also covers the separate, compounding whole-array-to-whole-array
connection-naming bug fixed in verilog/elaborate.py's own
_interface_connections_for_port() -- both bugs were found together
tracing real `aura` RTL (`fabric_subsys`'s own `axi_lite_if mst_port
[N_M]`, connected `.mst_port(axi_mst)`, both sides full arrays).

Fixtures here (`golden_rtl/verilog_flatten_interface/`) are a minimal
version of the exact real-aura shape: `bus_if` (a `data` net, `mst`/`slv`
modports) connected single-instance-to-single-instance (`top.sv`) and
whole-array-to-whole-array via a generic, modport-less crossbar port
(`top_array.sv`/`xbar.sv`, mirroring aura's own `fabric_subsys` -- "the
crossbar picks the modport" per that module's own source comment, so its
own `mst_port` is correctly NEVER offered as a driver/sink candidate).
"""

from pathlib import Path

from backend.elaborator.rtl.ir.flatten import DriverResult, Endpoint, ReceiverResult, TraceKind, flatten_design, trace_driver, trace_receivers
from backend.elaborator.rtl.verilog import elaborate

IFACE_DIR = Path(__file__).parent / "golden_rtl" / "verilog_flatten_interface"


def _elaborate(top: str, files: list[str]):
    return elaborate([str(IFACE_DIR / f) for f in files], top=top)


# --- whole-array connection naming (verilog/elaborate.py) ----------------


def test_whole_array_interface_bind_splits_into_per_index_connections():
    design = _elaborate("top_array", ["bus_if.sv", "xbar.sv", "leaf_mst.sv", "top_array.sv"])
    top = design.definition(design.top)
    xbar_inst = next(i for i in top.instances if i.name == "u_xbar")
    conns = {c.instance_port: c.connected_to for c in xbar_inst.connections}
    assert conns["mst_port[0]"] == "arr_bus[0]"
    assert conns["mst_port[1]"] == "arr_bus[1]"


def test_single_element_interface_bind_is_unaffected_by_the_array_fix():
    design = _elaborate("top", ["bus_if.sv", "leaf_mst.sv", "leaf_slv.sv", "top.sv"])
    top = design.definition(design.top)
    mst_inst = next(i for i in top.instances if i.name == "u_mst")
    conns = {c.instance_port: c.connected_to for c in mst_inst.connections}
    assert conns["b"] == "the_bus"


# --- (a) redirect into a bound interface instance -------------------------


def test_module_own_interface_port_signal_redirects_to_the_bound_instance():
    design = _elaborate("top", ["bus_if.sv", "leaf_mst.sv", "leaf_slv.sv", "top.sv"])

    # u_mst's own "b.data" IS the driver (its modport marks data OUT) --
    # redirecting into the_bus and back out via peer lookup correctly
    # lands back on itself, the same self-referential-but-correct shape
    # confirmed against real aura's own u_fabric.apb.paddr.
    assert trace_driver(design, "top/u_mst", "b.data") == DriverResult(TraceKind.RESOLVED, endpoint=Endpoint("top/u_mst", "b.data"))
    assert trace_receivers(design, "top/u_mst", "b.data") == ReceiverResult(TraceKind.RESOLVED, endpoints=(Endpoint("top/u_slv", "b.data"),))


# --- (b) peer lookup across an interface instance's own siblings ---------


def test_interface_instance_own_internal_net_resolves_via_sibling_peers():
    design = _elaborate("top", ["bus_if.sv", "leaf_mst.sv", "leaf_slv.sv", "top.sv"])

    assert trace_driver(design, "top/the_bus", "data") == DriverResult(TraceKind.RESOLVED, endpoint=Endpoint("top/u_mst", "b.data"))
    assert trace_receivers(design, "top/the_bus", "data") == ReceiverResult(TraceKind.RESOLVED, endpoints=(Endpoint("top/u_slv", "b.data"),))


def test_peer_with_no_resolvable_modport_is_excluded_not_guessed_at():
    """Mirrors real aura's own crossbar (`fabric_subsys`): `xbar`'s own
    `mst_port` has NO modport at all -- it must never be offered as a
    driver/sink candidate for `arr_bus`'s own internal signal."""
    design = _elaborate("top_array", ["bus_if.sv", "xbar.sv", "leaf_mst.sv", "top_array.sv"])

    # index 0 has a real, modport-qualified peer (u_mst0) -- resolves.
    assert trace_driver(design, "top_array/arr_bus[0]", "data") == DriverResult(
        TraceKind.RESOLVED, endpoint=Endpoint("top_array/u_mst0", "b.data")
    )
    # index 1 has only the crossbar's own unresolvable-modport binding -- UNDRIVEN, not guessed at.
    result = trace_driver(design, "top_array/arr_bus[1]", "data")
    assert result.kind == TraceKind.UNDRIVEN


def test_array_indexed_peer_reports_the_precise_element_not_the_bare_name():
    design = _elaborate("top_array", ["bus_if.sv", "xbar.sv", "leaf_mst.sv", "top_array.sv"])

    # From u_xbar's OWN perspective, "mst_port[0].data" is driven by
    # itself in this fixture? No -- xbar's own modport is unresolvable,
    # so u_xbar can never appear as a peer at all; only u_mst0 can.
    result = trace_driver(design, "top_array/u_xbar", "mst_port[0].data")
    assert result == DriverResult(TraceKind.RESOLVED, endpoint=Endpoint("top_array/u_mst0", "b.data"))


# --- flatten_design(): per-index enumeration, no cross-index merging -----


def test_flatten_design_enumerates_each_array_index_separately_not_merged():
    """Real, confirmed bug found via real aura design use: before
    per-index enumeration existed, a bare "mst_port.awaddr"-style trace
    silently MERGED index 0's genuinely resolved driver with index 1's
    genuinely undriven one into one misleading single-candidate
    MULTIPLE_DRIVERS result. Each index must be its own, independent
    Endpoint."""
    design = _elaborate("top_array", ["bus_if.sv", "xbar.sv", "leaf_mst.sv", "top_array.sv"])
    connectivity = flatten_design(design)

    ep0 = Endpoint("top_array/u_xbar", "mst_port[0].data")
    ep1 = Endpoint("top_array/u_xbar", "mst_port[1].data")
    assert ep0 in connectivity.drivers
    assert ep1 in connectivity.drivers
    assert connectivity.drivers[ep0].kind == TraceKind.RESOLVED
    assert connectivity.drivers[ep1].kind == TraceKind.UNDRIVEN
    # No bare, unindexed "mst_port.data" entry -- array-ness always expands.
    assert Endpoint("top_array/u_xbar", "mst_port.data") not in connectivity.drivers


def test_flatten_design_end_to_end_on_the_single_instance_fixture():
    design = _elaborate("top", ["bus_if.sv", "leaf_mst.sv", "leaf_slv.sv", "top.sv"])
    connectivity = flatten_design(design)

    assert connectivity.drivers[Endpoint("top/u_mst", "b.data")] == DriverResult(TraceKind.RESOLVED, endpoint=Endpoint("top/u_mst", "b.data"))
    assert connectivity.receivers[Endpoint("top/u_mst", "b.data")] == ReceiverResult(TraceKind.RESOLVED, endpoints=(Endpoint("top/u_slv", "b.data"),))
