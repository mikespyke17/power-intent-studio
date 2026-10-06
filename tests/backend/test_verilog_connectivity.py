"""1g: hierarchical connectivity, run through the real pyslang-driven elaborate().

Three concerns not exercised by the 1a-era fixtures (which only ever had a
single two-level, direct-port-passthrough design):
  - I/O extraction/connectivity holding up beyond depth 2.
  - Connectivity mediated through an internal net (not just a straight
    parent-port passthrough) -- this is also what exercises the new
    Definition.nets extraction.
  - Two instances sharing one (deduped) Definition must keep fully
    independent per-instance connections -- nothing about how one is wired
    should leak through the shared Definition object into the other.
"""

from pathlib import Path

from backend.elaborator.rtl.ir import BitRange, Net
from backend.elaborator.rtl.verilog import elaborate

THREE_LEVEL_DIR = Path(__file__).parent / "golden_rtl" / "verilog_three_level_hierarchy"
NET_FANOUT_DIR = Path(__file__).parent / "golden_rtl" / "verilog_internal_net_fanout"
NET_FANOUT_MULTI_DIR = Path(__file__).parent / "golden_rtl" / "verilog_net_fanout_multi"
SHARED_DEF_DIR = Path(__file__).parent / "golden_rtl" / "verilog_shared_definition_independent_connections"


def test_three_level_hierarchy_walk_and_connectivity():
    design = elaborate(
        [str(THREE_LEVEL_DIR / "leaf.v"), str(THREE_LEVEL_DIR / "mid.v"), str(THREE_LEVEL_DIR / "top.v")]
    )

    nodes = list(design.walk())
    assert [n.path for n in nodes] == ["top", "top/u_mid", "top/u_mid/u_leaf"]
    assert [n.definition.base_name for n in nodes] == ["top", "mid", "leaf"]

    top = design.definition(design.top)
    mid = design.definition(top.instances[0].definition_id)
    leaf = design.definition(mid.instances[0].definition_id)

    assert [p.name for p in top.ports] == ["clk", "rst_n", "data_in", "data_out"]
    assert [p.name for p in mid.ports] == ["clk", "rst_n", "d", "q"]
    assert [p.name for p in leaf.ports] == ["clk", "rst_n", "d", "q"]

    # connectivity chain: top -> mid -> leaf, each hop resolved independently
    top_conns = {c.instance_port: c.connected_to for c in top.instances[0].connections}
    assert top_conns == {"clk": "clk", "rst_n": "rst_n", "d": "data_in", "q": "data_out"}

    mid_conns = {c.instance_port: c.connected_to for c in mid.instances[0].connections}
    assert mid_conns == {"clk": "clk", "rst_n": "rst_n", "d": "d", "q": "q"}

    assert len(leaf.sequential_elements) == 1
    assert leaf.sequential_elements[0].output_signal == "q"


def test_internal_net_fanout_connectivity():
    """driver and receiver are wired together through an internal `link` net
    inside top, not through any of top's own ports (top has none). Both the
    Net extraction and the connectivity resolution through it are new as of
    1g -- 1a's fixtures never had a non-port internal signal.
    """
    design = elaborate([str(NET_FANOUT_DIR / "driver.v"), str(NET_FANOUT_DIR / "receiver.v"), str(NET_FANOUT_DIR / "top.v")])
    top = design.definition(design.top)

    assert top.ports == []
    assert top.nets == [Net(name="link", bit_range=BitRange(7, 0))]

    drv_inst, rcv_inst = top.instances
    assert drv_inst.name == "u_drv"
    assert rcv_inst.name == "u_rcv"
    assert [c.connected_to for c in drv_inst.connections] == ["link"]
    assert [c.connected_to for c in rcv_inst.connections] == ["link"]


def test_net_fanout_to_three_or_more_loads_is_not_assumed_binary():
    """The original net-fanout fixture only ever had 2 endpoints (1 driver, 1
    load) -- the same category of risk as the sensitivity-list bug, where the
    simplest fixture shape happened to hide a binary assumption. Here `link`
    has 4 endpoints (1 driver, 3 loads). Nothing in the current extraction
    path aggregates connections per net at all -- each Instance just records
    its own connection endpoint independently -- so there's no code path that
    *could* special-case "exactly 2", but this proves that empirically rather
    than by reading the implementation, and guards against a future
    per-net-aggregation change accidentally introducing that assumption.
    """
    design = elaborate(
        [
            str(NET_FANOUT_MULTI_DIR / "driver.v"),
            str(NET_FANOUT_MULTI_DIR / "receiver.v"),
            str(NET_FANOUT_MULTI_DIR / "top.v"),
        ]
    )
    top = design.definition(design.top)
    assert top.nets == [Net(name="link", bit_range=BitRange(7, 0))]

    link_endpoints = [
        (inst.name, c.instance_port) for inst in top.instances for c in inst.connections if c.connected_to == "link"
    ]
    assert len(top.instances) == 4
    assert len(link_endpoints) == 4
    assert ("u_drv", "out") in link_endpoints
    assert ("u_rcv0", "in") in link_endpoints
    assert ("u_rcv1", "in") in link_endpoints
    assert ("u_rcv2", "in") in link_endpoints


def test_shared_definition_keeps_instance_connections_independent():
    """top instantiates sub twice (u_sub_a, u_sub_b) with no parameter
    differences -- both must dedupe to the *same* Definition object, while
    each Instance's connections stay wired to its own distinct external
    signals. A bug that accidentally shared/reused connection data across
    instances of the same Definition would show up here as either identical
    connections on both instances, or the two Instance.connections lists
    literally being the same object.
    """
    design = elaborate([str(SHARED_DEF_DIR / "sub.v"), str(SHARED_DEF_DIR / "top.v")])
    top = design.definition(design.top)

    assert len(design.definitions) == 2  # top + one shared sub, not three
    inst_a, inst_b = top.instances
    assert inst_a.name == "u_sub_a"
    assert inst_b.name == "u_sub_b"

    # real dedup: both instances point at the identical Definition object
    def_a = design.definition(inst_a.definition_id)
    def_b = design.definition(inst_b.definition_id)
    assert inst_a.definition_id == inst_b.definition_id
    assert def_a is def_b

    # independent wiring per instance, not aliased
    conns_a = {c.instance_port: c.connected_to for c in inst_a.connections}
    conns_b = {c.instance_port: c.connected_to for c in inst_b.connections}
    assert conns_a == {"clk": "clk", "rst_n": "rst_n", "d": "data_in_a", "q": "data_out_a"}
    assert conns_b == {"clk": "clk", "rst_n": "rst_n", "d": "data_in_b", "q": "data_out_b"}
    assert conns_a != conns_b
    assert inst_a.connections is not inst_b.connections
