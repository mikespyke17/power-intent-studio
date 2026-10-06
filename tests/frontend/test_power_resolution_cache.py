"""power_intent_view/power_resolution_cache.py -- the two-layer cache
behind PortList's "Receiver Supply Set"/"Driver Supply Set" columns, and
its end-to-end wiring through PowerIntentPropertiesPanel into
hierarchy_view/properties_table_widget.py's own PortList table.
"""

from pathlib import Path
from unittest.mock import patch

from PySide6.QtGui import QColor

from backend.api import elaborate_rtl
from frontend.src.hierarchy_view.properties_table_widget import ConnectivityCellDisplay
from frontend.src.power_intent_view import power_resolution_cache as prc
from frontend.src.power_intent_view.connectivity_flatten import Endpoint
from frontend.src.power_intent_view.port_attribute_overlay import PortAttributeDiagramState, port_attribute_row_keys
from frontend.src.power_intent_view.power_domain_overlay import PowerDomainDiagramState, PowerDomainPlacement, SupplySetPlacement
from frontend.src.power_intent_view.power_resolution_cache import PowerResolutionCache, build_structural_cache, resolve_all
from frontend.src.power_intent_view.properties_panel import PowerIntentPropertiesPanel
from frontend.src.power_intent_view.selection_controller import PowerIntentSelectionController
from frontend.src.power_intent_view.supply_port_overlay import SupplyPortDiagramState
from frontend.src.shared.models.rtl import ContinuousAssign, Definition, Design, Instance, Port, PortConnection, PortDirection, SourceLanguage
from frontend.src.backend_integration.backend_adapter import adapt_design


def _design_with_real_logic() -> Design:
    """top.a -(real logic in leaf)-> top.y -- leaf's own domain resolves
    top.y's driver; top itself resolves top.a's receiver back to leaf."""
    leaf = Definition(
        definition_id="leaf",
        base_name="leaf",
        language=SourceLanguage.VERILOG,
        ports=[Port("a", PortDirection.IN), Port("y", PortDirection.OUT)],
        continuous_assigns=[ContinuousAssign(target="y", source=None)],
    )
    top = Definition(
        definition_id="top",
        base_name="top",
        language=SourceLanguage.VERILOG,
        ports=[Port("a", PortDirection.IN), Port("y", PortDirection.OUT)],
        instances=[Instance("u_leaf", "leaf", [PortConnection("a", "a"), PortConnection("y", "y")])],
    )
    return Design(definitions={"leaf": leaf, "top": top}, top="top")


def _domain_state_for_leaf() -> PowerDomainDiagramState:
    state = PowerDomainDiagramState()
    state.supply_sets["ss1"] = SupplySetPlacement(id="ss1", name="VDD_CORE", power="VDD", owner_path="top")
    state.domains["d1"] = PowerDomainPlacement(id="d1", owner_path="top/u_leaf", name="PD_LEAF", color=QColor("#ff0000"), primary_supply_set="ss1")
    return state


# --- resolve_all()/PowerResolutionCache -- pure resolution logic -----------


def test_ordinary_port_resolves_to_its_domains_primary_supply_set():
    design = _design_with_real_logic()
    structural = build_structural_cache(design)
    domain_state = _domain_state_for_leaf()

    receiver_by_key, driver_by_key = resolve_all(structural, PowerResolutionCache(), None, domain_state, None)

    # top.y's TRUE driver is leaf's own `y` (real logic) -- leaf belongs to PD_LEAF -> VDD_CORE
    assert driver_by_key[("top", "y")] == prc.ResolvedSupplyDisplay("VDD_CORE")


def test_top_level_in_port_driver_with_no_port_attribute_shows_no_power_associated():
    """IN pin -- Driver Supply Set: explicit rule change, "No Power
    Associated" (italic) replaces the previous "From/To Top" fallback
    when no Port Attribute is mapped."""
    design = _design_with_real_logic()
    structural = build_structural_cache(design)

    receiver_by_key, driver_by_key = resolve_all(structural, PowerResolutionCache(), None, _domain_state_for_leaf(), None)

    assert driver_by_key[("top", "a")] == prc.ResolvedSupplyDisplay(prc.NO_PORT_ATTRIBUTE_ASSOCIATED_TEXT, italic=True)


def test_top_level_out_port_receiver_with_no_port_attribute_shows_no_power_associated():
    """OUT pin -- Receiver Supply Set: same rule, mirrored."""
    design = _design_with_real_logic()
    structural = build_structural_cache(design)

    receiver_by_key, driver_by_key = resolve_all(structural, PowerResolutionCache(), None, _domain_state_for_leaf(), None)

    assert receiver_by_key[("top", "y")] == prc.ResolvedSupplyDisplay(prc.NO_PORT_ATTRIBUTE_ASSOCIATED_TEXT, italic=True)


def test_top_level_port_with_port_attribute_shows_the_assigned_supply_set():
    """"External - " prefix (italic) ahead of the assigned Supply Set's
    own name -- distinguishes a MANUALLY-ASSERTED Port Attribute claim
    from a genuinely traced resolution (see EXTERNAL_PREFIX's own
    docstring, and the dedicated simple8-with-Port-Attributes golden
    test group below for the full multi-port picture)."""
    design = _design_with_real_logic()
    structural = build_structural_cache(design)
    domain_state = _domain_state_for_leaf()
    port_attrs = PortAttributeDiagramState(assignments={"a": "ss1"})

    receiver_by_key, driver_by_key = resolve_all(structural, PowerResolutionCache(), None, domain_state, port_attrs)

    assert driver_by_key[("top", "a")] == prc.ResolvedSupplyDisplay(
        f"{prc.EXTERNAL_PREFIX}VDD_CORE", italic_prefix_lens=(len(prc.EXTERNAL_PREFIX),)
    )


def test_cell_with_no_power_domain_is_italic():
    """Real logic genuinely exists here (NOT Undriven/Floating) -- it's
    just that no Power Domain has been assigned to this hierarchy at
    all, so there's no Supply Set to report. Nomenclature cleanup
    (explicit request): this used to be its own "No Power Domain" marker
    (NO_DOMAIN_TEXT); retired in favor of the shared NO_SUPPLY_SET_TEXT
    -- the two were never actually distinguishable to the user, both
    meaning "real logic exists here, but nothing to resolve a Supply Set
    from"."""
    design = _design_with_real_logic()
    structural = build_structural_cache(design)
    # No domain at all covers leaf -- top.y's driver (leaf's own `y`) has nowhere to resolve to.
    receiver_by_key, driver_by_key = resolve_all(structural, PowerResolutionCache(), None, PowerDomainDiagramState(), None)

    display = driver_by_key[("top", "y")]
    assert display.text == prc.NO_SUPPLY_SET_TEXT
    assert display.italic is True


def test_undriven_and_floating_are_italic():
    """`resolve_all()` only ever covers PORT-keyed endpoints (matching
    Layer 1's own port-only drivers/receivers dicts -- see flatten_design()'s
    own docstring for why internal nets are traced for warning-surfacing
    only, never added to those dicts) -- so the port itself, not a bare
    internal net, must be the one that's undriven/floating here."""
    from frontend.src.shared.models.rtl import Net

    top = Definition(
        definition_id="top",
        base_name="top",
        language=SourceLanguage.VERILOG,
        ports=[Port("a", PortDirection.IN), Port("y", PortDirection.OUT)],
        nets=[Net("undriven_sig"), Net("unused_sig")],
        continuous_assigns=[ContinuousAssign(target="y", source="undriven_sig"), ContinuousAssign(target="unused_sig", source="a")],
    )
    design = Design(definitions={"top": top}, top="top")
    structural = build_structural_cache(design)

    receiver_by_key, driver_by_key = resolve_all(structural, PowerResolutionCache(), None, PowerDomainDiagramState(), None)

    undriven = driver_by_key[("top", "y")]  # y's true driver (undriven_sig) is never driven
    assert undriven.text == prc.UNDRIVEN_TEXT and undriven.italic is True

    floating = receiver_by_key[("top", "a")]  # a's only receiver (unused_sig) is never consumed further
    assert floating.text == prc.FLOATING_TEXT and floating.italic is True


def test_multiple_sinks_resolving_to_the_same_supply_set_are_deduplicated():
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
        ],
    )
    design = Design(definitions={"sink": sink, "top": top}, top="top")
    structural = build_structural_cache(design)
    # Both sinks (top/u_s0, top/u_s1) belong to the SAME domain -> one deduplicated line.
    domain_state = PowerDomainDiagramState()
    domain_state.supply_sets["ss1"] = SupplySetPlacement(id="ss1", name="VDD_IO", power="VDD", owner_path="top")
    domain_state.domains["d1"] = PowerDomainPlacement(id="d1", owner_path="top", name="PD_TOP", color=QColor("#00ff00"), primary_supply_set="ss1")

    receiver_by_key, _ = resolve_all(structural, PowerResolutionCache(), None, domain_state, None)

    display = receiver_by_key[("top", "a")]
    assert display.text == "VDD_IO"  # deduplicated to one line, not "VDD_IO\nVDD_IO"


def test_combinational_loop_and_multiple_drivers_markers():
    # `y` is a real port (so it lands in resolve_all()'s own port-keyed
    # dict); the loop itself runs through an internal net `b`.
    top_loop = Definition(
        definition_id="top",
        base_name="top",
        language=SourceLanguage.VERILOG,
        ports=[Port("y", PortDirection.OUT)],
        continuous_assigns=[ContinuousAssign(target="y", source="b"), ContinuousAssign(target="b", source="y")],
    )
    design_loop = Design(definitions={"top": top_loop}, top="top")
    structural_loop = build_structural_cache(design_loop)
    receiver_by_key, driver_by_key = resolve_all(structural_loop, PowerResolutionCache(), None, PowerDomainDiagramState(), None)
    loop_display = driver_by_key[("top", "y")]
    assert loop_display.text == prc.LOOP_TEXT and loop_display.italic is True

    top_md = Definition(
        definition_id="top",
        base_name="top",
        language=SourceLanguage.VERILOG,
        ports=[Port("a", PortDirection.IN), Port("b", PortDirection.IN), Port("y", PortDirection.OUT)],
        continuous_assigns=[ContinuousAssign(target="y", source="a"), ContinuousAssign(target="y", source="b")],
    )
    design_md = Design(definitions={"top": top_md}, top="top")
    structural_md = build_structural_cache(design_md)
    _, driver_by_key_md = resolve_all(structural_md, PowerResolutionCache(), None, PowerDomainDiagramState(), None)
    md_display = driver_by_key_md[("top", "y")]
    assert md_display.text == prc.MULTIPLE_DRIVERS_TEXT and md_display.italic is True


# --- End-to-end through PowerIntentPropertiesPanel/PortList ----------------


def test_portlist_shows_receiver_and_driver_columns_end_to_end(qtbot):
    design = _design_with_real_logic()
    panel = PowerIntentPropertiesPanel(PowerIntentSelectionController())
    qtbot.addWidget(panel)
    panel.load_design(design)
    panel.refresh_diagram_overlay(SupplyPortDiagramState(), _domain_state_for_leaf(), None, None)

    panel.design_properties.apply_selection({"top/u_leaf"})
    table = panel.design_properties._port_panel.table
    headers = [table.horizontalHeaderItem(c).text() for c in range(table.columnCount())]
    assert headers == ["Name", "Dir", "Width", "Receiver Supply Set", "Driver Supply Set"]

    rows = {table.item(r, 0).text(): (table.item(r, 3).text(), table.item(r, 4).text()) for r in range(table.rowCount())}
    # leaf.y's own driver is leaf.y itself (real logic) -> PD_LEAF's primary supply set
    assert rows["y"][1] == "VDD_CORE"


def test_power_domain_edit_updates_portlist_without_rebuilding_structural_cache(qtbot):
    """Cache-invalidation contract: a Power Domain/Supply Set edit
    (refresh_diagram_overlay) must recompute the DISPLAYED text, but must
    NOT re-run the expensive Layer-1 structural trace -- confirmed here
    by spying on build_structural_cache() itself."""
    design = _design_with_real_logic()
    panel = PowerIntentPropertiesPanel(PowerIntentSelectionController())
    qtbot.addWidget(panel)
    panel.load_design(design)
    panel.design_properties.apply_selection({"top/u_leaf"})

    with patch("frontend.src.power_intent_view.properties_panel.build_structural_cache") as spy:
        panel.refresh_diagram_overlay(SupplyPortDiagramState(), _domain_state_for_leaf(), None, None)
        spy.assert_not_called()

    table = panel.design_properties._port_panel.table
    rows = {table.item(r, 0).text(): table.item(r, 4).text() for r in range(table.rowCount())}
    assert rows["y"] == "VDD_CORE"


def test_new_design_load_rebuilds_the_structural_cache():
    design_a = _design_with_real_logic()
    panel = PowerIntentPropertiesPanel(PowerIntentSelectionController())
    panel.load_design(design_a)
    first_cache = panel._structural_cache
    assert first_cache is not None

    design_b = _design_with_real_logic()
    panel.load_design(design_b)
    assert panel._structural_cache is not first_cache
    assert panel._structural_cache.design is design_b


def test_connectivity_cell_display_crosses_into_plain_hierarchy_view_type():
    """Sanity check on the milestone-boundary conversion itself: the
    (text, italic) shape survives the crossing from power_resolution_cache's
    own ResolvedSupplyDisplay into hierarchy_view's own ConnectivityCellDisplay
    unchanged."""
    resolved = prc.ResolvedSupplyDisplay("VDD_CORE", italic=False)
    cell = ConnectivityCellDisplay(resolved.text, resolved.italic)
    assert cell.text == "VDD_CORE"
    assert cell.italic is False


# --- Interface-exposed signal rows -- found and fixed via real aura design use ---
# (net_group() never matched interface-typed connections at all; see
# connectivity_flatten.py's own "Interface-aware tracing" section.)


def _bus_design() -> Design:
    from frontend.src.shared.models.rtl import InterfacePort, Modport, ModportPort, Net

    bus_if = Definition(
        definition_id="bus_if",
        base_name="bus_if",
        language=SourceLanguage.VERILOG,
        is_interface=True,
        ports=[Port("clk", PortDirection.IN)],
        nets=[Net("data")],
        modports=[Modport("mst", ports=[ModportPort("data", PortDirection.OUT)]), Modport("slv", ports=[ModportPort("data", PortDirection.IN)])],
    )
    leaf_mst = Definition(
        definition_id="leaf_mst",
        base_name="leaf_mst",
        language=SourceLanguage.VERILOG,
        ports=[Port("clk", PortDirection.IN)],
        interface_ports=[InterfacePort(name="b", interface_name="bus_if", modport="mst")],
    )
    leaf_slv = Definition(
        definition_id="leaf_slv",
        base_name="leaf_slv",
        language=SourceLanguage.VERILOG,
        ports=[Port("clk", PortDirection.IN)],
        interface_ports=[InterfacePort(name="b", interface_name="bus_if", modport="slv")],
    )
    top = Definition(
        definition_id="top",
        base_name="top",
        language=SourceLanguage.VERILOG,
        ports=[Port("clk", PortDirection.IN)],
        instances=[
            Instance("the_bus", "bus_if", [PortConnection("clk", "clk")]),
            Instance("u_mst", "leaf_mst", [PortConnection("clk", "clk"), PortConnection("b", "the_bus")]),
            Instance("u_slv", "leaf_slv", [PortConnection("clk", "clk"), PortConnection("b", "the_bus")]),
        ],
    )
    return Design(definitions={"bus_if": bus_if, "leaf_mst": leaf_mst, "leaf_slv": leaf_slv, "top": top}, top="top")


def test_portlist_resolves_interface_exposed_signal_rows_not_just_plain_ports(qtbot):
    design = _bus_design()
    panel = PowerIntentPropertiesPanel(PowerIntentSelectionController())
    qtbot.addWidget(panel)
    panel.load_design(design)

    domain_state = PowerDomainDiagramState()
    domain_state.supply_sets["ss1"] = SupplySetPlacement(id="ss1", name="VDD_BUS", power="VDD", owner_path="top")
    domain_state.domains["d1"] = PowerDomainPlacement(id="d1", owner_path="top/u_mst", name="PD_MST", color=QColor("#ff0000"), primary_supply_set="ss1")
    panel.refresh_diagram_overlay(SupplyPortDiagramState(), domain_state, None, None)

    panel.design_properties.apply_selection({"top/u_mst", "top/u_slv"})
    table = panel.design_properties._port_panel.table
    rows = [
        (table.item(r, 0).text(), table.item(r, 1).text(), table.item(r, 3).text(), table.item(r, 4).text()) for r in range(table.rowCount())
    ]
    iface_rows = [row for row in rows if row[0] == "b.data"]
    assert len(iface_rows) == 2  # one for u_mst (OUT), one for u_slv (IN)

    # u_mst's own b.data is OUT (it's the master) -- its driver is itself, PD_MST -> VDD_BUS.
    mst_row = next(row for row in iface_rows if row[1] == "OUT")
    assert mst_row[3] == "VDD_BUS"  # Driver Supply Set
    # u_slv has no assigned domain -- its own driver (u_mst) still resolves to VDD_BUS.
    slv_row = next(row for row in iface_rows if row[1] == "IN")
    assert slv_row[3] == "VDD_BUS"


# --- Macro pin Driver/Receiver direction-awareness -- found via real design use ---
# Real, confirmed bug (Port List's own Driver/Receiver Supply Set
# columns): every macro pin used to resolve to the SAME value in both
# columns (the macro's own Power Domain), regardless of pin direction --
# an INPUT pin's real external driver never showed. Fixed in three
# passes: (1) direction-aware is_black_box handling in
# connectivity_flatten.py, so the side facing the rest of the design is
# genuinely traced; (2) the side facing the macro itself resolved via
# Port.related_power_pin matched directly against a Supply Set's own
# Power field -- SUPERSEDED by (3): related_power_pin is only the raw
# PG-pin NAME (e.g. "VDD"), not itself a Supply Set -- the real answer
# requires following the diagram's own REAL power-net connectivity (a
# drawn connector from the macro's fixed pg-pin icon to a plain Supply
# Port/Internal Supply), then finding whichever Supply Set references
# THAT connected port's own name -- since a real design typically wires
# a macro's "VDD" pg-pin to a differently-named Supply Port (e.g.
# "VDD_CORE"), a bare name-coincidence match against the raw LIB string
# was silently wrong for exactly that (normal, expected) case.


def _pg_id(macro_path: str, pin_name: str) -> str:
    return f"pg:{macro_path}:{pin_name}"


def _wire_macro_pg_pin_to_supply_port(
    supply_state: SupplyPortDiagramState, macro_path: str, pin_name: str, port_name: str, port_owner_path: str
) -> str:
    """Registers a plain Supply Port `port_name` at `port_owner_path` and
    draws a power-net connector from the macro's own `pin_name` pg-pin
    icon to it -- mirrors what "Add Power Supply" + dragging a connector
    between the two icons actually produces in the real diagram. Returns
    the new Supply Port's own id (SupplySetPlacement.power/.ground store
    bare names, not ids)."""
    from frontend.src.power_intent_view.supply_port_overlay import SupplyPortPlacement

    pg_id = _pg_id(macro_path, pin_name)
    supply_state.pg_pin_placements[pg_id] = SupplyPortPlacement(id=pg_id, owner_path=macro_path, name=pin_name, color=QColor("#888888"), fixed=True)
    port_id = f"port:{port_name}"
    supply_state.supply_port_placements[port_id] = SupplyPortPlacement(id=port_id, owner_path=port_owner_path, name=port_name, color=QColor("#ff0000"))
    supply_state.supply_connector_placements.append((pg_id, port_id))
    return port_id


def _design_with_wired_macro(ce_related_power_pin="VDD", dout_related_power_pin="VDD") -> Design:
    macro = Definition(
        definition_id="ram",
        base_name="RAM",
        language=SourceLanguage.LIBERTY,
        is_black_box=True,
        ports=[
            Port("CE", PortDirection.IN, related_power_pin=ce_related_power_pin),
            Port("DOUT", PortDirection.OUT, related_power_pin=dout_related_power_pin),
            Port("VDD", PortDirection.PG),
        ],
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
            Instance("u_ram", "ram", [PortConnection("CE", "ce_net"), PortConnection("DOUT", "dout_net")]),
            Instance("u_snk0", "snk", [PortConnection("in_sig", "dout_net")]),
            Instance("u_snk1", "snk", [PortConnection("in_sig", "dout_net")]),
        ],
    )
    return Design(definitions={"ram": macro, "drv": driver, "snk": sink, "top": top}, top="top")


def test_portlist_macro_input_pin_receiver_resolves_via_power_net_not_raw_pg_name(qtbot):
    """CE's own related_power_pin is "VDD", but the ACTUAL Supply Port
    it's wired to via a drawn connector is named "VDD_CORE" (a
    deliberately DIFFERENT name -- the normal, expected authoring flow,
    and the exact case the raw-name-match version got wrong)."""
    design = _design_with_wired_macro()
    panel = PowerIntentPropertiesPanel(PowerIntentSelectionController())
    qtbot.addWidget(panel)
    panel.load_design(design)

    supply_state = SupplyPortDiagramState()
    _wire_macro_pg_pin_to_supply_port(supply_state, "top/u_ram", "VDD", "VDD_CORE", "top/u_ram")

    domain_state = PowerDomainDiagramState()
    domain_state.supply_sets["ss1"] = SupplySetPlacement(id="ss1", name="VDD_MACRO_SS", power="VDD_CORE", owner_path="top/u_ram")
    domain_state.supply_sets["ss2"] = SupplySetPlacement(id="ss2", name="VDD_DRV", power="VDD", owner_path="top/u_drv")
    domain_state.domains["d2"] = PowerDomainPlacement(id="d2", owner_path="top/u_drv", name="PD_DRV", color=QColor("#00ff00"), primary_supply_set="ss2")
    panel.refresh_diagram_overlay(supply_state, domain_state, None, None)

    panel.design_properties.apply_selection({"top/u_ram"})
    table = panel.design_properties._port_panel.table
    rows = {table.item(r, 0).text(): (table.item(r, 3).text(), table.item(r, 4).text()) for r in range(table.rowCount())}

    # CE is an INPUT: Receiver = power-net-resolved (VDD -> VDD_CORE port -> VDD_MACRO_SS); Driver = traced external driver.
    assert rows["CE"] == ("VDD_MACRO_SS", "VDD_DRV")


def test_portlist_macro_output_pin_driver_resolves_via_power_net_with_dedup(qtbot):
    design = _design_with_wired_macro()
    panel = PowerIntentPropertiesPanel(PowerIntentSelectionController())
    qtbot.addWidget(panel)
    panel.load_design(design)

    supply_state = SupplyPortDiagramState()
    _wire_macro_pg_pin_to_supply_port(supply_state, "top/u_ram", "VDD", "VDD_CORE", "top/u_ram")

    domain_state = PowerDomainDiagramState()
    domain_state.supply_sets["ss1"] = SupplySetPlacement(id="ss1", name="VDD_MACRO_SS", power="VDD_CORE", owner_path="top/u_ram")
    # Both sinks share the SAME domain -- Receiver column must dedupe to one line, not two.
    domain_state.supply_sets["ss2"] = SupplySetPlacement(id="ss2", name="VDD_IO", power="VDD", owner_path="top")
    domain_state.domains["d2"] = PowerDomainPlacement(id="d2", owner_path="top", name="PD_TOP", color=QColor("#0000ff"), primary_supply_set="ss2")
    panel.refresh_diagram_overlay(supply_state, domain_state, None, None)

    panel.design_properties.apply_selection({"top/u_ram"})
    table = panel.design_properties._port_panel.table
    rows = {table.item(r, 0).text(): (table.item(r, 3).text(), table.item(r, 4).text()) for r in range(table.rowCount())}

    # DOUT is an OUTPUT: Driver = power-net-resolved; Receiver = traced, deduplicated external receivers.
    assert rows["DOUT"] == ("VDD_IO", "VDD_MACRO_SS")


def test_portlist_macro_pin_with_no_connector_shows_macro_no_supply_set_italic(qtbot):
    """The macro's own pg-pin has no drawn power-net connector to
    anything yet -- "Macro - No Supply Set" (italic), a marker DISTINCT
    from the plain "Floating" a genuinely zero-sink signal gets AND from
    the bare "No Supply Set" the RTL-only case gets (resolved via the
    sram.v/sram.lib golden fixture below, which closed this as a
    previously-open question; renamed from an earlier "Macro - Floating"
    on a nomenclature-cleanup follow-up request, since that name
    collided with the unrelated plain "Floating" marker -- see
    MACRO_NO_SUPPLY_SET_TEXT's own module-level docstring in
    power_resolution_cache.py)."""
    design = _design_with_wired_macro()
    panel = PowerIntentPropertiesPanel(PowerIntentSelectionController())
    qtbot.addWidget(panel)
    panel.load_design(design)

    domain_state = PowerDomainDiagramState()
    domain_state.domains["d1"] = PowerDomainPlacement(id="d1", owner_path="top/u_ram", name="PD_RAM", color=QColor("#ff0000"), primary_supply_set=None)
    panel.refresh_diagram_overlay(SupplyPortDiagramState(), domain_state, None, None)

    panel.design_properties.apply_selection({"top/u_ram"})
    table = panel.design_properties._port_panel.table
    row_index = next(r for r in range(table.rowCount()) if table.item(r, 0).text() == "CE")
    receiver_item = table.item(row_index, 3)
    assert receiver_item.text() == prc.MACRO_NO_SUPPLY_SET_TEXT
    assert receiver_item.font().italic() is True


def test_portlist_macro_pin_connected_but_no_matching_supply_set_shows_macro_no_supply_set_italic(qtbot):
    """The pg-pin IS wired to a real Supply Port ("VDD_CORE"), but no
    Supply Set has actually been composed referencing that port yet --
    "Macro - No Supply Set" (italic). Nomenclature cleanup (explicit
    request): this used to assert the bare "No Supply Set" -- that
    literal string is now reserved exclusively for the RTL-only "real
    logic found, no Power Domain assigned" case; every non-resolving
    macro-pin branch (this one included) uses the macro-scoped marker
    instead, so "No Supply Set" can never appear for a macro pin."""
    design = _design_with_wired_macro()
    panel = PowerIntentPropertiesPanel(PowerIntentSelectionController())
    qtbot.addWidget(panel)
    panel.load_design(design)

    supply_state = SupplyPortDiagramState()
    _wire_macro_pg_pin_to_supply_port(supply_state, "top/u_ram", "VDD", "VDD_CORE", "top/u_ram")

    domain_state = PowerDomainDiagramState()
    domain_state.domains["d1"] = PowerDomainPlacement(id="d1", owner_path="top/u_ram", name="PD_RAM", color=QColor("#ff0000"), primary_supply_set=None)
    panel.refresh_diagram_overlay(supply_state, domain_state, None, None)

    panel.design_properties.apply_selection({"top/u_ram"})
    table = panel.design_properties._port_panel.table
    row_index = next(r for r in range(table.rowCount()) if table.item(r, 0).text() == "CE")
    receiver_item = table.item(row_index, 3)
    assert receiver_item.text() == prc.MACRO_NO_SUPPLY_SET_TEXT
    assert receiver_item.font().italic() is True


def test_portlist_macro_pin_with_no_related_power_pin_shows_macro_no_supply_set():
    """The LIB never declared related_power_pin at all for this pin --
    still "Macro - No Supply Set," not guessed at via the macro's own
    domain, and not the bare RTL-only "No Supply Set" (nomenclature
    cleanup, see the sibling tests above)."""
    design = _design_with_wired_macro(ce_related_power_pin=None)
    structural = build_structural_cache(design)
    domain_state = PowerDomainDiagramState()
    domain_state.domains["d1"] = PowerDomainPlacement(id="d1", owner_path="top/u_ram", name="PD_RAM", color=QColor("#ff0000"), primary_supply_set=None)

    receiver_by_key, _ = resolve_all(structural, PowerResolutionCache(), SupplyPortDiagramState(), domain_state, None)
    display = receiver_by_key[("top/u_ram", "CE")]
    assert display.text == prc.MACRO_NO_SUPPLY_SET_TEXT and display.italic is True


# --- GOLDEN TEST CASE: pass-through vs. real-logic detection, real elaboration ---
# The clearest concrete specification of correct pass-through-vs-real-logic
# behavior produced so far in this project (explicit request) -- run
# through the REAL backend elaborator (not a hand-built mock Design) and
# the REAL backend_integration adapter, exactly the path the actual GUI
# uses, to catch exactly the class of bug a hand-built fixture can't:
# fixing this golden case surfaced FOUR real, previously-undetected bugs
# that every earlier hand-built-Design test in this file was structurally
# incapable of catching:
#   1. `_resolve_ordinary_endpoint`'s own top-level-boundary check compared
#      `endpoint.path` (a hierarchy PATH string) against `design.top` (a
#      DEFINITION ID, which always carries a hash suffix from
#      make_definition_id() -- e.g. "top__42988143" -- even with zero
#      parameters). Every hand-built Design fixture in this file happened
#      to set `definition_id="top"` literally, so this comparison
#      accidentally worked in every OTHER test here -- never for a real
#      elaborated design. Fixed by checking the SAME structural "is this
#      the design's own root" fact the trace engine already relies on
#      (`node.instance is None`), further narrowed to require
#      `endpoint.signal` to actually be one of that root Definition's own
#      declared ports (an internal net like a flop's own `q_int`, sitting
#      at that exact same root path, is NOT a boundary port a Port
#      Attribute could ever apply to).
#   2. A flop's own clock/reset/set/data signal (consumed by an
#      `always_ff` block, not a continuous assign) was invisible to
#      trace_receivers() entirely -- ContinuousAssign-based tracing has
#      no visibility into procedural blocks at all. Fixed by treating a
#      SequentialElement's own `clock_ref`/`reset_ref`/`set_ref`/
#      `data_ref` as legitimate receivers, with that element's own
#      `output_signal` standing in for "the flop itself" (SequentialElement
#      has no hierarchy path of its own -- it's metadata on the SAME
#      Definition the clock/reset/data signal already lives in).
#   3. mock.SequentialElement (this trimmed frontend mirror) never even
#      carried clock_ref/reset_ref/set_ref/data_ref at all, and
#      backend_adapter.py's own _adapt_sequential_element() silently
#      dropped them even after being added -- the same "adapter forgot to
#      forward a new field" bug class as Port.related_power_pin/
#      InterfacePort.array_size/Definition.continuous_assigns before it.
#   4. `d`'s own Receiver column (the user's own originally-specified
#      expectation: SS_VDD, the flop that consumes it) required reversing
#      a standing, documented architectural decision -- SequentialElement
#      previously had NO way to record which signal feeds a flop's own
#      data input at all ("No data-path/next-state modeling... which
#      value a register is assigned to is never inspected"). Done only
#      after explicit sign-off (a separate request, not bundled silently
#      into this pass-through-detection fix): SequentialElement.data_ref
#      now captures the SAME narrow "is the RHS a bare, same-width
#      single-signal reference" test ContinuousAssign.source already
#      applies to a plain `assign`, scoped to a sequential element's own
#      "not currently resetting" branch. General next-state EXPRESSION
#      modeling (an operator, a case-selected mux between two different
#      registers) remains explicitly out of scope -- see
#      SequentialElement's own docstring and
#      tests/backend/test_sequential_data_ref.py for the exact boundary.


def _pass_through_vs_real_logic_design():
    golden = Path(__file__).parent.parent / "backend" / "golden_rtl" / "verilog_pass_through_vs_real_logic"
    real_design = elaborate_rtl([str(golden / "sub0.sv"), str(golden / "top.sv")], [], "top")
    return adapt_design(real_design)


def _pass_through_vs_real_logic_domain_state() -> PowerDomainDiagramState:
    state = PowerDomainDiagramState()
    state.supply_sets["ss_cor"] = SupplySetPlacement(id="ss_cor", name="SS_VDD", power="VDD", owner_path="top")
    state.domains["pd_cor"] = PowerDomainPlacement(id="pd_cor", owner_path="top", name="PD_COR", color=QColor("#ff0000"), primary_supply_set="ss_cor")
    state.supply_sets["ss_sub"] = SupplySetPlacement(id="ss_sub", name="SS_VDD_SUB", power="VDD_SUB", owner_path="top/u_sub0")
    state.domains["pd_sub"] = PowerDomainPlacement(
        id="pd_sub", owner_path="top/u_sub0", name="PD_SUB", color=QColor("#00ff00"), primary_supply_set="ss_sub"
    )
    return state


def test_golden_pass_through_vs_real_logic_port_list_table(qtbot):
    design = _pass_through_vs_real_logic_design()
    panel = PowerIntentPropertiesPanel(PowerIntentSelectionController())
    qtbot.addWidget(panel)
    panel.load_design(design)
    panel.refresh_diagram_overlay(SupplyPortDiagramState(), _pass_through_vs_real_logic_domain_state(), None, None)

    NPA = prc.NO_PORT_ATTRIBUTE_ASSOCIATED_TEXT
    table = panel.design_properties._port_panel.table

    panel.design_properties.apply_selection({"top"})
    top_rows = {table.item(r, 0).text(): (table.item(r, 3).text(), table.item(r, 4).text()) for r in range(table.rowCount())}
    expected_top = {
        # name        (Receiver,     Driver)
        "clk":     ("SS_VDD",     NPA),
        "reset_n": ("SS_VDD",     NPA),
        "d":       ("SS_VDD",     NPA),
        "q":       (NPA,          "SS_VDD_SUB"),
        "i_sleep": ("SS_VDD_SUB", NPA),
        "o_sleep": (NPA,          "SS_VDD_SUB"),
        "i_iso":   (NPA,          NPA),
        "o_iso":   (NPA,          NPA),
    }
    for name, expected_cell in expected_top.items():
        assert top_rows[name] == expected_cell, f"top.{name}: expected {expected_cell}, got {top_rows[name]}"

    # Extended coverage (explicit request): the SAME underlying trace
    # result must be reported consistently regardless of which
    # instance's own Port List is being viewed -- sub0's own ports here
    # reflect the identical wiring already validated above from top's
    # own boundary, just viewed from the inner instance's perspective.
    # A real, confirmed bug was found and fixed getting this to hold:
    # the "a child instance's own unconsumed input port is itself a
    # legitimate receiver" fallback used to apply ONLY when reached via
    # a parent's own net_group() sink loop crossing into that child --
    # querying the SAME endpoint directly (as sub0's own Port List does)
    # fell through to a bare, inconsistent FLOATING instead. Fixed by
    # making that fallback an intrinsic property of the endpoint's own
    # resolution (both flatten.py and connectivity_flatten.py), not a
    # property of whichever call site happened to reach it.
    panel.design_properties.apply_selection({"top/u_sub0"})
    sub0_rows = {table.item(r, 0).text(): (table.item(r, 3).text(), table.item(r, 4).text()) for r in range(table.rowCount())}
    expected_sub0 = {
        # name        (Receiver,     Driver)
        "i_sleep": ("SS_VDD_SUB", NPA),
        "o_sleep": (NPA,          "SS_VDD_SUB"),
        "i_iso":   (NPA,          NPA),
        "o_iso":   (NPA,          NPA),
        # i_q/o_q (explicit follow-up request): completes sub0's own Port
        # List assertion. i_q is the one port in the whole fixture where
        # BOTH Driver and Receiver resolve to real, DIFFERENT Supply
        # Sets -- Receiver = SS_VDD_SUB (sub0's own inverter, `assign
        # o_q = ~i_q;`, real logic); Driver = SS_VDD (crosses back UP
        # through top's own wiring to `q_int`, the always_ff flop's own
        # output net in PD_COR) -- validating cross-hierarchy backward
        # tracing lands on real logic on BOTH ends of a signal
        # simultaneously, not just when one end is an unattributed
        # top-level boundary (every other row in this fixture). Getting
        # this to resolve (rather than i_q's own Driver reporting
        # Undriven) required a real, confirmed, separate fix: nothing
        # previously marked a flop as DRIVING its own output_signal on
        # the driver side (only the receiver side treated clock_ref/
        # reset_ref/set_ref/data_ref as legitimate receivers) -- the
        # backward trace from i_q correctly found `q_int` as the true
        # source (cross-hierarchy ascension itself was never the
        # problem), but `q_int` then reported itself UNDRIVEN with no
        # way to say "a real, identifiable flop drives this."
        "i_q": ("SS_VDD_SUB", "SS_VDD"),
        "o_q": (NPA,          "SS_VDD_SUB"),
    }
    for name, expected_cell in expected_sub0.items():
        assert sub0_rows[name] == expected_cell, f"sub0.{name}: expected {expected_cell}, got {sub0_rows[name]}"


def test_golden_pass_through_traces_clean_through_a_pure_wire_assign():
    """The specific, narrow claim the user's own spec calls "the critical
    distinguishing test": `assign o_iso = i_iso;` (sub0.sv) is a pure
    wire pass-through with NO operator on the RHS -- must be traced
    THROUGH, landing on the true origin/destination at the far side, not
    stopped at sub0's own boundary. Checked directly against the
    structural trace (bypassing Power-Domain resolution) so this test's
    own intent -- "does the pass-through/real-logic DETECTION itself
    work" -- can't be confused with a Power-Domain-configuration mistake
    in some other test."""
    design = _pass_through_vs_real_logic_design()
    structural = build_structural_cache(design)

    # i_iso's true receiver, after passing straight through sub0's own
    # `assign o_iso = i_iso;`, is top's OWN o_iso boundary port -- not
    # sub0's own o_iso (an intermediate hop the trace must not stop at).
    receiver = structural.connectivity.receivers[Endpoint("top", "i_iso")]
    assert receiver.kind.value == "resolved"
    assert receiver.endpoints == (Endpoint("top", "o_iso"),)


def test_golden_real_logic_inverter_stops_the_trace_not_walked_through():
    """The other half of the same distinguishing claim: `assign o_sleep =
    ~i_sleep;` (sub0.sv) IS real logic (an inverter), not a plain
    ContinuousAssign forward (its own `source` is None, since the RHS
    isn't a bare signal reference) -- i_sleep's own trace must stop at
    sub0's own `i_sleep` pin itself (the child instance's own
    unconsumed input is still a legitimate receiver -- the existing,
    separately-tested FLOATING fallback), NOT walked through the
    inverter out to top's own o_sleep boundary port the way the
    pure-wire i_iso case is."""
    design = _pass_through_vs_real_logic_design()
    structural = build_structural_cache(design)

    receiver = structural.connectivity.receivers[Endpoint("top", "i_sleep")]
    assert receiver.kind.value == "resolved"
    assert receiver.endpoints == (Endpoint("top/u_sub0", "i_sleep"),)


# --- GOLDEN TEST CASE: real RTL logic found, but its own hierarchy has no Power Domain ---
# Closes a gap surfaced by the nomenclature-cleanup pass (explicit
# request): NO_SUPPLY_SET_TEXT's own scenario -- real driving/receiving
# RTL logic genuinely found (NOT Undriven/Floating), but the hierarchy
# that logic lives in has no Power Domain assigned at all -- was
# previously only exercised against a hand-built mock Design
# (test_cell_with_no_power_domain_is_italic above), never against a real
# elaborated golden fixture. Reuses top.sv/sub0.sv (no new RTL files
# needed).
#
# A real, confirmed finding while building this test: the originally
# suggested setup ("remove sub0's own Power Domain, leave top's own in
# place") does NOT actually produce "No Supply Set" anywhere --
# find_effective_domain_for_path()'s own "deepest owner wins" rule
# treats a domain's owner_path as covering its ENTIRE subtree
# (ancestor membership, not just an exact path match -- see its own
# docstring in power_domain_overlay.py), so top's own root-level domain
# (PD_COR, owner_path="top") cascades DOWN to cover sub0 too whenever
# sub0 has no more specific domain of its own overriding it. Domains
# only ever cascade downward, never upward -- so the configuration that
# actually isolates this scenario is the INVERSE: only sub0 has its own
# Power Domain (PD_SUB); top's own root is left with none at all. Then
# any real logic living directly in top's own hierarchy (the flop
# driving q_int, fed by clk/reset_n/d) has no domain covering it, while
# sub0's own domain-covered ports are completely unaffected -- exactly
# the "No Supply Set here, real Supply Set names there, in the SAME
# design" contrast this test needs.


def _sub0_only_domain_state() -> PowerDomainDiagramState:
    state = PowerDomainDiagramState()
    state.supply_sets["ss_sub"] = SupplySetPlacement(id="ss_sub", name="SS_VDD_SUB", power="VDD_SUB", owner_path="top/u_sub0")
    state.domains["pd_sub"] = PowerDomainPlacement(
        id="pd_sub", owner_path="top/u_sub0", name="PD_SUB", color=QColor("#00ff00"), primary_supply_set="ss_sub"
    )
    return state


def test_golden_real_logic_with_no_power_domain_shows_no_supply_set(qtbot):
    design = _pass_through_vs_real_logic_design()
    panel = PowerIntentPropertiesPanel(PowerIntentSelectionController())
    qtbot.addWidget(panel)
    panel.load_design(design)
    panel.refresh_diagram_overlay(SupplyPortDiagramState(), _sub0_only_domain_state(), None, None)

    NPA = prc.NO_PORT_ATTRIBUTE_ASSOCIATED_TEXT
    NSS = prc.NO_SUPPLY_SET_TEXT
    table = panel.design_properties._port_panel.table

    panel.design_properties.apply_selection({"top"})
    top_rows = {table.item(r, 0).text(): (table.item(r, 3).text(), table.item(r, 4).text()) for r in range(table.rowCount())}
    expected_top = {
        # name        (Receiver,  Driver)
        # clk/reset_n/d's TRUE receiver is the flop itself, living
        # directly in top's own domain-less hierarchy -- "No Supply
        # Set", not Undriven/Floating (real logic genuinely exists).
        "clk":     (NSS, NPA),
        "reset_n": (NSS, NPA),
        "d":       (NSS, NPA),
        # q/o_sleep's TRUE driver is sub0's own real logic -- sub0 HAS
        # its own Power Domain, so these resolve normally, unaffected.
        "q":       (NPA, "SS_VDD_SUB"),
        "i_sleep": ("SS_VDD_SUB", NPA),
        "o_sleep": (NPA, "SS_VDD_SUB"),
        "i_iso":   (NPA, NPA),
        "o_iso":   (NPA, NPA),
    }
    for name, expected_cell in expected_top.items():
        assert top_rows[name] == expected_cell, f"top.{name}: expected {expected_cell}, got {top_rows[name]}"

    panel.design_properties.apply_selection({"top/u_sub0"})
    sub0_rows = {table.item(r, 0).text(): (table.item(r, 3).text(), table.item(r, 4).text()) for r in range(table.rowCount())}
    expected_sub0 = {
        # name        (Receiver,     Driver)
        "i_sleep": ("SS_VDD_SUB", NPA),
        "o_sleep": (NPA,          "SS_VDD_SUB"),
        "i_iso":   (NPA,          NPA),
        "o_iso":   (NPA,          NPA),
        "o_q":     (NPA,          "SS_VDD_SUB"),
        # i_q's Receiver is sub0's own real logic (has a domain) -- but
        # its Driver traces back UP to q_int, living in top's own
        # domain-less hierarchy -- "No Supply Set", confirming the two
        # sides of the SAME port can resolve completely independently.
        "i_q":     ("SS_VDD_SUB", NSS),
    }
    for name, expected_cell in expected_sub0.items():
        assert sub0_rows[name] == expected_cell, f"sub0.{name}: expected {expected_cell}, got {sub0_rows[name]}"


# --- GOLDEN TEST CASE: parent-level real logic after a sub-hierarchy output ---
# A deliberate variant of the pass-through-vs-real-logic fixture above,
# added alongside it (not replacing it) -- explicit request. Structurally
# identical to top.sv/sub0.sv EXCEPT for one change: top1 itself now has a
# SECOND inversion stage sitting directly in the PARENT module
# (`assign o_sleep = ~sleep_int;`), fed by an internal net `sleep_int`
# that comes from sub1's own `o_sleep` output -- rather than sub1's
# o_sleep connecting straight through to top1's o_sleep port the way
# sub0's did for top's.
#
# The point of this fixture: the original golden test only ever validated
# that real logic living in a CHILD hierarchy (sub0) is correctly
# identified as a stopping point. This one validates that real logic
# living in the PARENT hierarchy, positioned AFTER a child instance's own
# output pin, is ALSO correctly identified as a stopping point -- in BOTH
# directions:
#   - top1.o_sleep's own DRIVER must resolve to SS_VDD (top1's own
#     inverter, in PD_COR), NOT SS_VDD_SUB1 (sub1's domain) -- even though
#     top1.o_sleep's own NAME collides with a root-level boundary port,
#     which is otherwise the signal for "flows out of the design,
#     untraceable." Fixed by threading `is_driver_side` through
#     `_resolve_ordinary_endpoint()` (power_resolution_cache.py): the
#     top-level-boundary branch is now excluded specifically when a
#     real-logic continuous assign (source=None) targets this exact
#     signal at this exact path AND the query is driver-side -- Verilog
#     structurally forbids assigning to an INPUT port, so this collision
#     can only ever arise on the driver side, never the receiver side.
#   - sub1.o_sleep's own RECEIVER must resolve to SS_VDD (top1's own
#     inverter now genuinely CONSUMES it), NOT "Floating" the way an
#     unattributed top-level pass-through would. Fixed by adding
#     ContinuousAssign.referenced_signals (every signal referenced
#     anywhere in a real-logic RHS, not just the special bare-pass-through
#     `source` case) and extending trace_receivers() to recognize "this
#     signal feeds a real-logic assign" as a legitimate, terminal receiver
#     fact -- deliberately anchored at the signal's OWN identity (`here`),
#     never at the assign's own `target`, to avoid an unresolvable
#     collision with that identical Endpoint's separate "flows out of the
#     design" boundary meaning.
#
# Every other row is structurally unchanged from top.sv/sub0.sv and
# expected to produce identical results (flop-fed inputs resolve to
# SS_VDD; the q/i_q/o_q path crosses the hierarchy boundary through two
# pieces of real logic; i_iso/o_iso remain a pure feedthrough resolving to
# "External - No Port Attribute Associated" on both sides).


def _parent_level_real_logic_design():
    golden = Path(__file__).parent.parent / "backend" / "golden_rtl" / "verilog_parent_level_real_logic_after_child"
    real_design = elaborate_rtl([str(golden / "sub1.sv"), str(golden / "top1.sv")], [], "top1")
    return adapt_design(real_design)


def _parent_level_real_logic_domain_state() -> PowerDomainDiagramState:
    state = PowerDomainDiagramState()
    state.supply_sets["ss_cor"] = SupplySetPlacement(id="ss_cor", name="SS_VDD", power="VDD", owner_path="top1")
    state.domains["pd_cor"] = PowerDomainPlacement(id="pd_cor", owner_path="top1", name="PD_COR", color=QColor("#ff0000"), primary_supply_set="ss_cor")
    state.supply_sets["ss_sub1"] = SupplySetPlacement(id="ss_sub1", name="SS_VDD_SUB1", power="VDD_SUB1", owner_path="top1/u_sub1")
    state.domains["pd_sub1"] = PowerDomainPlacement(
        id="pd_sub1", owner_path="top1/u_sub1", name="PD_SUB1", color=QColor("#00ff00"), primary_supply_set="ss_sub1"
    )
    return state


def test_golden_parent_level_real_logic_after_child_port_list_table(qtbot):
    design = _parent_level_real_logic_design()
    panel = PowerIntentPropertiesPanel(PowerIntentSelectionController())
    qtbot.addWidget(panel)
    panel.load_design(design)
    panel.refresh_diagram_overlay(SupplyPortDiagramState(), _parent_level_real_logic_domain_state(), None, None)

    NPA = prc.NO_PORT_ATTRIBUTE_ASSOCIATED_TEXT
    table = panel.design_properties._port_panel.table

    panel.design_properties.apply_selection({"top1"})
    top1_rows = {table.item(r, 0).text(): (table.item(r, 3).text(), table.item(r, 4).text()) for r in range(table.rowCount())}
    expected_top1 = {
        # name        (Receiver,     Driver)
        "clk":     ("SS_VDD",      NPA),
        "reset_n": ("SS_VDD",      NPA),
        "d":       ("SS_VDD",      NPA),
        "q":       (NPA,           "SS_VDD_SUB1"),
        "i_iso":   (NPA,           NPA),
        "o_iso":   (NPA,           NPA),
        "i_sleep": ("SS_VDD_SUB1", NPA),
        # The critical, deliberately-differing row: top1's own inverter
        # (`assign o_sleep = ~sleep_int;`), living in PD_COR, is the true
        # driver -- NOT sub1's domain, even though sub1's o_sleep is the
        # signal that ultimately feeds it.
        "o_sleep": (NPA,           "SS_VDD"),
    }
    for name, expected_cell in expected_top1.items():
        assert top1_rows[name] == expected_cell, f"top1.{name}: expected {expected_cell}, got {top1_rows[name]}"

    panel.design_properties.apply_selection({"top1/u_sub1"})
    sub1_rows = {table.item(r, 0).text(): (table.item(r, 3).text(), table.item(r, 4).text()) for r in range(table.rowCount())}
    expected_sub1 = {
        # name        (Receiver,     Driver)
        "i_q":     ("SS_VDD_SUB1", "SS_VDD"),
        "o_q":     (NPA,           "SS_VDD_SUB1"),
        "i_sleep": ("SS_VDD_SUB1", NPA),
        "i_iso":   (NPA,           NPA),
        "o_iso":   (NPA,           NPA),
        # The other critical, deliberately-differing row: sub1's own
        # o_sleep now feeds a REAL receiver (top1's own inverter) rather
        # than passing straight through to an unattributed top-level
        # boundary port -- must resolve to SS_VDD (top1's domain), not
        # "External - No Port Attribute Associated".
        "o_sleep": ("SS_VDD",      "SS_VDD_SUB1"),
    }
    for name, expected_cell in expected_sub1.items():
        assert sub1_rows[name] == expected_cell, f"sub1.{name}: expected {expected_cell}, got {sub1_rows[name]}"


# --- GOLDEN TEST CASE: fan-out to multiple receivers + a genuinely floating instance output ---
# A third variant, added alongside (not replacing) top.sv/sub0.sv and
# top1.sv/sub1.sv -- explicit request. Extends the same "sub-level real
# logic feeding a parent-level inverter" shape with two new, distinct
# conditions neither earlier fixture exercised:
#   1. top2.i_sleep FANS OUT across TWO SEPARATE CHILD INSTANCES
#      (sub21.i_sleep and sub22.i_sleep), each with its own real inverter
#      living in a genuinely DIFFERENT Power Domain -- must resolve to a
#      two-line, non-deduplicated cell ("SS_VDD_SUB12\nSS_VDD_SUB22").
#      The pre-existing dedup-by-display-text test only ever covered
#      multiple sinks collapsing onto the SAME Supply Set; this is the
#      first fixture covering genuinely different sinks fanning out
#      across different instances, not just different pins of one
#      instance.
#   2. sub22's own instantiation in top2 leaves `.o_sleep()` explicitly
#      UNCONNECTED -- no net at all, not even an internal one. This is
#      NOT the same as an unattributed top-level boundary port (every
#      other "External - No Port Attribute Associated" case in every earlier fixture): there
#      is no destination node whatsoever, so it must resolve to
#      "Floating" (the existing zero-sink LINT marker), while sub22's own
#      Driver side (its own real inverter) must still resolve normally to
#      SS_VDD_SUB22 -- confirming Driver-side resolution is completely
#      independent of whether a Receiver exists at all.


def _fanout_and_floating_design():
    golden = Path(__file__).parent.parent / "backend" / "golden_rtl" / "verilog_fanout_and_floating_output"
    real_design = elaborate_rtl([str(golden / "sub21.sv"), str(golden / "sub22.sv"), str(golden / "top2.sv")], [], "top2")
    return adapt_design(real_design)


def _fanout_and_floating_domain_state() -> PowerDomainDiagramState:
    state = PowerDomainDiagramState()
    state.supply_sets["ss_cor"] = SupplySetPlacement(id="ss_cor", name="SS_VDD", power="VDD", owner_path="top2")
    state.domains["pd_cor"] = PowerDomainPlacement(id="pd_cor", owner_path="top2", name="PD_COR", color=QColor("#ff0000"), primary_supply_set="ss_cor")
    state.supply_sets["ss_sub21"] = SupplySetPlacement(id="ss_sub21", name="SS_VDD_SUB12", power="VDD_SUB12", owner_path="top2/u_sub21")
    state.domains["pd_sub21"] = PowerDomainPlacement(
        id="pd_sub21", owner_path="top2/u_sub21", name="PD_SUB21", color=QColor("#00ff00"), primary_supply_set="ss_sub21"
    )
    state.supply_sets["ss_sub22"] = SupplySetPlacement(id="ss_sub22", name="SS_VDD_SUB22", power="VDD_SUB22", owner_path="top2/u_sub22")
    state.domains["pd_sub22"] = PowerDomainPlacement(
        id="pd_sub22", owner_path="top2/u_sub22", name="PD_SUB22", color=QColor("#0000ff"), primary_supply_set="ss_sub22"
    )
    return state


def test_golden_fanout_and_floating_output_port_list_table(qtbot):
    design = _fanout_and_floating_design()
    panel = PowerIntentPropertiesPanel(PowerIntentSelectionController())
    qtbot.addWidget(panel)
    panel.load_design(design)
    panel.refresh_diagram_overlay(SupplyPortDiagramState(), _fanout_and_floating_domain_state(), None, None)

    NPA = prc.NO_PORT_ATTRIBUTE_ASSOCIATED_TEXT
    FLOATING = prc.FLOATING_TEXT
    table = panel.design_properties._port_panel.table

    panel.design_properties.apply_selection({"top2"})
    top2_rows = {table.item(r, 0).text(): (table.item(r, 3).text(), table.item(r, 4).text()) for r in range(table.rowCount())}
    expected_top2 = {
        # name        (Receiver,                        Driver)
        "clk":     ("SS_VDD",                        NPA),
        "reset_n": ("SS_VDD",                        NPA),
        "d":       ("SS_VDD",                        NPA),
        "q":       (NPA,                             "SS_VDD_SUB12"),
        "i_iso":   (NPA,                             NPA),
        "o_iso":   (NPA,                             NPA),
        # The fan-out row: two separate child instances, two genuinely
        # different Supply Sets, no dedup.
        "i_sleep": ("SS_VDD_SUB12\nSS_VDD_SUB22",     NPA),
        "o_sleep": (NPA,                              "SS_VDD"),
    }
    for name, expected_cell in expected_top2.items():
        assert top2_rows[name] == expected_cell, f"top2.{name}: expected {expected_cell}, got {top2_rows[name]}"

    panel.design_properties.apply_selection({"top2/u_sub21"})
    sub21_rows = {table.item(r, 0).text(): (table.item(r, 3).text(), table.item(r, 4).text()) for r in range(table.rowCount())}
    expected_sub21 = {
        # name        (Receiver,     Driver)
        "i_q":     ("SS_VDD_SUB12", "SS_VDD"),
        "o_q":     (NPA,            "SS_VDD_SUB12"),
        "i_sleep": ("SS_VDD_SUB12", NPA),
        "o_sleep": ("SS_VDD",       "SS_VDD_SUB12"),
        "i_iso":   (NPA,            NPA),
        "o_iso":   (NPA,            NPA),
    }
    for name, expected_cell in expected_sub21.items():
        assert sub21_rows[name] == expected_cell, f"sub21.{name}: expected {expected_cell}, got {sub21_rows[name]}"

    panel.design_properties.apply_selection({"top2/u_sub22"})
    sub22_rows = {table.item(r, 0).text(): (table.item(r, 3).text(), table.item(r, 4).text()) for r in range(table.rowCount())}
    expected_sub22 = {
        # name        (Receiver,   Driver)
        "i_sleep": ("SS_VDD_SUB22", NPA),
        # The critical row: genuinely unconnected (`.o_sleep()`), not an
        # unattributed top-level boundary port -- must be "Floating", not
        # "External - No Port Attribute Associated". Driver side is unaffected (its own real
        # inverter still resolves normally), confirming the two sides of
        # the trace don't share failure state.
        "o_sleep": (FLOATING,       "SS_VDD_SUB22"),
    }
    for name, expected_cell in expected_sub22.items():
        assert sub22_rows[name] == expected_cell, f"sub22.{name}: expected {expected_cell}, got {sub22_rows[name]}"


# --- GOLDEN TEST CASE: macro with unconnected PG pins -- closes the "unconnected PG-Type fallback" open question ---
# A fourth variant, added alongside (not replacing) top.sv/sub0.sv,
# top1.sv/sub1.sv, and top2.sv/sub21.sv/sub22.sv -- explicit request.
# sram.v/sram.lib is a black-box (.lib) macro whose functional pins map
# to TWO different PG-Types (VDD -- SLEEP_B/ISO_EN/POWER_GOOD -- and the
# internal VDD_INT -- CLK/CEN_B/WEN_B/A/D/Q), but NEITHER pg-pin has a
# power-net connector drawn to any Supply Port/Internal Supply anywhere
# in the design.
#
# This resolves a previously-open question flagged in the earlier macro
# power-net resolution fix: what should display when a macro's own
# PG-Type has no power-net connection at all? Answer, confirmed by this
# fixture: "Macro - No Supply Set" (italic) -- a marker deliberately
# DISTINCT from the plain "Floating" a genuinely zero-sink signal gets,
# AND from the bare "No Supply Set" the RTL-only "real logic found, no
# Power Domain" case gets (see MACRO_NO_SUPPLY_SET_TEXT's own docstring
# in power_resolution_cache.py -- renamed from an earlier "Macro -
# Floating" on a nomenclature-cleanup follow-up request, since that name
# collided with the unrelated plain "Floating" marker), applied
# uniformly regardless of which specific PG pin (main VDD vs. internal
# VDD_INT) is involved -- confirmed here since q/power_good map to
# DIFFERENT PG pins (VDD_INT vs. VDD respectively) yet both produce the
# identical marker.


def _macro_unconnected_pg_pins_design():
    golden = Path(__file__).parent.parent / "backend" / "golden_rtl" / "verilog_macro_unconnected_pg_pins"
    real_design = elaborate_rtl([str(golden / "sram.v")], [str(golden / "sram.lib")], "sram")
    return adapt_design(real_design)


def _macro_unconnected_pg_pins_domain_state() -> PowerDomainDiagramState:
    state = PowerDomainDiagramState()
    state.supply_sets["ss_vdd"] = SupplySetPlacement(id="ss_vdd", name="SS_VDD", power="VDD", owner_path="sram")
    state.supply_sets["ss_vdd_int"] = SupplySetPlacement(id="ss_vdd_int", name="SS_VDD_INT", power="VDD_INT", owner_path="sram")
    state.domains["pd_ram"] = PowerDomainPlacement(
        id="pd_ram", owner_path="sram", name="PD_RAM", color=QColor("#ff0000"), primary_supply_set="ss_vdd", secondary_supply_sets=["ss_vdd_int"]
    )
    return state


def test_golden_macro_unconnected_pg_pins_port_list_table(qtbot):
    design = _macro_unconnected_pg_pins_design()
    panel = PowerIntentPropertiesPanel(PowerIntentSelectionController())
    qtbot.addWidget(panel)
    panel.load_design(design)
    panel.refresh_diagram_overlay(SupplyPortDiagramState(), _macro_unconnected_pg_pins_domain_state(), None, None)

    NPA = prc.NO_PORT_ATTRIBUTE_ASSOCIATED_TEXT
    MACRO_NO_SUPPLY_SET = prc.MACRO_NO_SUPPLY_SET_TEXT
    table = panel.design_properties._port_panel.table

    panel.design_properties.apply_selection({"sram"})
    sram_rows = {table.item(r, 0).text(): (table.item(r, 3).text(), table.item(r, 4).text()) for r in range(table.rowCount())}
    expected_sram = {
        # name          (Receiver,        Driver)
        "clk":        (MACRO_NO_SUPPLY_SET, NPA),
        "cen_b":      (MACRO_NO_SUPPLY_SET, NPA),
        "wen_b":      (MACRO_NO_SUPPLY_SET, NPA),
        "sleep_b":    (MACRO_NO_SUPPLY_SET, NPA),
        "iso_en":     (MACRO_NO_SUPPLY_SET, NPA),
        "a":          (MACRO_NO_SUPPLY_SET, NPA),
        "d":          (MACRO_NO_SUPPLY_SET, NPA),
        # q maps to VDD_INT; power_good maps to VDD -- different PG pins,
        # identical marker, since both are equally unconnected.
        "q":          (NPA,            MACRO_NO_SUPPLY_SET),
        "power_good": (NPA,            MACRO_NO_SUPPLY_SET),
    }
    for name, expected_cell in expected_sram.items():
        assert sram_rows[name] == expected_cell, f"sram.{name}: expected {expected_cell}, got {sram_rows[name]}"


def test_golden_macro_unconnected_pg_pins_port_list_table_before_any_power_domain_exists(qtbot):
    """The identical sram.v/sram.lib fixture and expected table as
    test_golden_macro_unconnected_pg_pins_port_list_table above, but with
    the ONLY difference being a completely fresh Power Intent tab -- NO
    Power Domain (PD_RAM), NO Supply Set (SS_VDD/SS_VDD_INT) created
    anywhere at all, just a bare SupplyPortDiagramState()/
    PowerDomainDiagramState() -- explicit follow-up request, closing the
    question of whether "Macro - No Supply Set"/"External - No Power
    Associated" have any unstated dependency on a Power Domain already
    existing.

    Both markers are structurally independent of Power Domain existence:
    "Macro - No Supply Set" comes from _macro_port_facing_itself() --
    checked, and short-circuited on, BEFORE _resolve_ordinary_endpoint()
    ever reaches the ordinary Power-Domain lookup -- and "External - No
    Power Associated" comes from the top-level Port Attribute check,
    which never consults domain_state.domains at all. This is exactly
    the state every design
    starts in immediately after "Load Design," before the user has
    created anything -- must produce the identical, well-defined table
    as the Power-Domain-present case, not a crash (e.g. code assuming
    `domain.primary_supply_set` is always reachable) or a blank/
    different result."""
    design = _macro_unconnected_pg_pins_design()
    panel = PowerIntentPropertiesPanel(PowerIntentSelectionController())
    qtbot.addWidget(panel)
    panel.load_design(design)
    panel.refresh_diagram_overlay(SupplyPortDiagramState(), PowerDomainDiagramState(), None, None)

    NPA = prc.NO_PORT_ATTRIBUTE_ASSOCIATED_TEXT
    MACRO_NO_SUPPLY_SET = prc.MACRO_NO_SUPPLY_SET_TEXT
    table = panel.design_properties._port_panel.table

    panel.design_properties.apply_selection({"sram"})
    sram_rows = {table.item(r, 0).text(): (table.item(r, 3).text(), table.item(r, 4).text()) for r in range(table.rowCount())}
    expected_sram = {
        # name          (Receiver,        Driver)
        "clk":        (MACRO_NO_SUPPLY_SET, NPA),
        "cen_b":      (MACRO_NO_SUPPLY_SET, NPA),
        "wen_b":      (MACRO_NO_SUPPLY_SET, NPA),
        "sleep_b":    (MACRO_NO_SUPPLY_SET, NPA),
        "iso_en":     (MACRO_NO_SUPPLY_SET, NPA),
        "a":          (MACRO_NO_SUPPLY_SET, NPA),
        "d":          (MACRO_NO_SUPPLY_SET, NPA),
        "q":          (NPA,            MACRO_NO_SUPPLY_SET),
        "power_good": (NPA,            MACRO_NO_SUPPLY_SET),
    }
    for name, expected_cell in expected_sram.items():
        assert sram_rows[name] == expected_cell, f"sram.{name}: expected {expected_cell}, got {sram_rows[name]}"


# --- GOLDEN TEST CASE: macro PG-pin connectivity -- fully connected, partial (power/ground-only), and crossed-rail conflict ---
# A fourth sram.v/sram.lib variant (verilog_macro_pg_pin_connectivity/,
# alongside the "fully unconnected"/"no Power Domain at all" ones above,
# not replacing them) -- explicit request. Adds a real VSS_INT
# (internal_ground, pg_function "VSS") pg_pin to the LIB, which the
# earlier fixture never had -- every internal-domain functional pin
# (CLK/CEN_B/WEN_B/A/D/Q) now declares BOTH related_power_pin="VDD_INT"
# AND related_ground_pin="VSS_INT", closing the gap that made
# related_ground_pin a deliberately-unparsed field until this fixture.
#
# A REAL, PREVIOUSLY-HIDDEN BUG found building this fixture's own
# baseline (Case 1, fully connected) -- not a new category, the exact
# bug class _unwrap_liberty_string()'s own docstring already warns
# about: `_build_scalar_port()`/`_build_bus_port()`
# (backend/elaborator/lib/elaborate.py) read
# `related_power_pin`/`related_ground_pin` completely unwrapped. For a
# QUOTED Liberty value (`related_power_pin : "VDD_INT";` -- confirmed
# common, valid Liberty syntax, not unusual), the field stayed a raw
# `EscapedString` object instead of a plain `str` for its entire
# downstream lifetime. Every EARLIER macro-pin test happened to never
# expose this: the hand-built-mock-Design tests
# (_design_with_wired_macro()) never touched real LIB parsing at all,
# and the one real-LIB-elaborated fixture (verilog_macro_unconnected_pg_pins)
# only ever exercised the "nothing connected" case, where a
# quote-corrupted pg-pin id string still correctly fails to match
# anything (the expected outcome either way). This fixture's own Case 1
# was the FIRST real end-to-end test to wire a connector to a REAL,
# LIB-elaborated macro pin and expect a RESOLVED (non-floating) result
# -- f-string-interpolating the corrupted EscapedString into
# `f"pg:{path}:{related_power_pin}"` produced literal embedded quote
# characters (`pg:sram/u_sram:"VDD_INT"`), permanently failing to match
# the clean, unquoted id every SupplyPortPlacement is actually keyed
# by. Fixed via a new `_unwrap_optional_liberty_string()` helper
# (None-safe -- the attribute is legitimately absent on many ports),
# applied to both fields in both port-building functions. See that
# helper's own docstring for the full writeup.
#
# CASE 4's own scope was explicitly flagged back rather than guessed at
# (per direct request): does a crossed-rail conflict apply only to the
# specific PG-pin-pair involved (matching Cases 2/3's own established
# precedent -- only pins referencing VDD_INT/VSS_INT are affected,
# sleep_b/iso_en/power_good stay at their normal SS_VDD value), or does
# it propagate macro-instance-wide to every functional pin regardless of
# which PG-pin pair each one individually references? Confirmed: PER-
# RAIL-PAIR -- matches the existing architecture exactly (resolution is
# purely per-pin/per-PG-pin-pair; no macro-instance-wide state exists
# anywhere in the data model), and needed zero further implementation
# changes -- the natural per-rail-pair implementation already produced
# this result.


def _macro_pg_pin_connectivity_design():
    golden = Path(__file__).parent.parent / "backend" / "golden_rtl" / "verilog_macro_pg_pin_connectivity"
    real_design = elaborate_rtl([str(golden / "sram.v")], [str(golden / "sram.lib")], "sram")
    return adapt_design(real_design)


def _macro_pg_pin_connectivity_domain_state() -> PowerDomainDiagramState:
    state = PowerDomainDiagramState()
    state.supply_sets["ss_vdd"] = SupplySetPlacement(id="ss_vdd", name="SS_VDD", power="VDD", ground="VSS", owner_path="sram")
    state.supply_sets["ss_vdd_sub"] = SupplySetPlacement(id="ss_vdd_sub", name="SS_VDD_SUB", power="VDD_SUB", ground="VSS_SUB", owner_path="sram")
    state.domains["pd_ram"] = PowerDomainPlacement(
        id="pd_ram", owner_path="sram", name="PD_RAM", color=QColor("#ff0000"), primary_supply_set="ss_vdd", secondary_supply_sets=["ss_vdd_sub"]
    )
    return state


def _wire_sram_pg_pin_connectivity(wiring: list[tuple[str, str]]) -> SupplyPortDiagramState:
    """`wiring` is a list of (pg_pin_name, port_name) pairs -- reuses the
    existing _wire_macro_pg_pin_to_supply_port() helper (defined above,
    for the "power-only" macro-pin tests) once per pair, on the SAME
    SupplyPortDiagramState, so multiple rails of the same macro instance
    end up wired together in one state, matching how a real diagram
    would accumulate connectors one drag at a time."""
    supply_state = SupplyPortDiagramState()
    for pg_pin_name, port_name in wiring:
        _wire_macro_pg_pin_to_supply_port(supply_state, "sram/u_sram", pg_pin_name, port_name, "sram")
    return supply_state


def _sram_pg_pin_connectivity_port_list_table(supply_state: SupplyPortDiagramState, qtbot) -> dict[str, tuple[str, str]]:
    design = _macro_pg_pin_connectivity_design()
    panel = PowerIntentPropertiesPanel(PowerIntentSelectionController())
    qtbot.addWidget(panel)
    panel.load_design(design)
    panel.refresh_diagram_overlay(supply_state, _macro_pg_pin_connectivity_domain_state(), None, None)

    table = panel.design_properties._port_panel.table
    panel.design_properties.apply_selection({"sram"})
    return {table.item(r, 0).text(): (table.item(r, 3).text(), table.item(r, 4).text()) for r in range(table.rowCount())}


def test_golden_macro_pg_pin_fully_connected_resolves_both_domains(qtbot):
    """CASE 1 -- baseline: VDD<-VDD, VSS<-VSS, VDD_INT<-VDD_SUB,
    VSS_INT<-VSS_SUB. Every pin resolves to whichever Supply Set its
    related_power_pin/related_ground_pin pair is fully and correctly
    wired to -- no Floating/Conflict markers anywhere."""
    supply_state = _wire_sram_pg_pin_connectivity(
        [("VDD", "VDD"), ("VSS", "VSS"), ("VDD_INT", "VDD_SUB"), ("VSS_INT", "VSS_SUB")]
    )
    rows = _sram_pg_pin_connectivity_port_list_table(supply_state, qtbot)

    NPA = prc.NO_PORT_ATTRIBUTE_ASSOCIATED_TEXT
    expected = {
        # name          (Receiver,     Driver)
        "clk":        ("SS_VDD_SUB", NPA),
        "cen_b":      ("SS_VDD_SUB", NPA),
        "wen_b":      ("SS_VDD_SUB", NPA),
        "sleep_b":    ("SS_VDD",     NPA),
        "iso_en":     ("SS_VDD",     NPA),
        "a":          ("SS_VDD_SUB", NPA),
        "d":          ("SS_VDD_SUB", NPA),
        "q":          (NPA,          "SS_VDD_SUB"),
        "power_good": (NPA,          "SS_VDD"),
    }
    for name, expected_cell in expected.items():
        assert rows[name] == expected_cell, f"sram.{name}: expected {expected_cell}, got {rows[name]}"


def test_golden_macro_pg_pin_ground_only_missing_shows_macro_ground_floating(qtbot):
    """CASE 2 -- VSS_INT left open (VDD_INT still wired): the related
    POWER pin IS connected -- only the related GROUND pin is left open.
    Must be "Macro - Ground Floating" (naming specifically which rail is
    missing), NOT the generic "Macro - No Supply Set". Only pins whose
    related_ground_pin is VSS_INT are affected -- sleep_b/iso_en/
    power_good (related_ground_pin=VSS, untouched) stay at their normal
    SS_VDD value."""
    supply_state = _wire_sram_pg_pin_connectivity([("VDD", "VDD"), ("VSS", "VSS"), ("VDD_INT", "VDD_SUB")])
    rows = _sram_pg_pin_connectivity_port_list_table(supply_state, qtbot)

    NPA = prc.NO_PORT_ATTRIBUTE_ASSOCIATED_TEXT
    GND_FLOATING = prc.MACRO_GROUND_FLOATING_TEXT
    expected = {
        # name          (Receiver,     Driver)
        "clk":        (GND_FLOATING, NPA),
        "cen_b":      (GND_FLOATING, NPA),
        "wen_b":      (GND_FLOATING, NPA),
        "sleep_b":    ("SS_VDD",     NPA),
        "iso_en":     ("SS_VDD",     NPA),
        "a":          (GND_FLOATING, NPA),
        "d":          (GND_FLOATING, NPA),
        "q":          (NPA,          GND_FLOATING),
        "power_good": (NPA,          "SS_VDD"),
    }
    for name, expected_cell in expected.items():
        assert rows[name] == expected_cell, f"sram.{name}: expected {expected_cell}, got {rows[name]}"


def test_golden_macro_pg_pin_power_only_missing_shows_macro_power_floating(qtbot):
    """CASE 3 -- mirror of Case 2: VDD_INT left open (VSS_INT still
    wired) -- "Macro - Power Floating", confirming the resolution logic
    independently checks BOTH rails and reports specifically which one
    is missing, rather than one generic "something's disconnected"
    result."""
    supply_state = _wire_sram_pg_pin_connectivity([("VDD", "VDD"), ("VSS", "VSS"), ("VSS_INT", "VSS_SUB")])
    rows = _sram_pg_pin_connectivity_port_list_table(supply_state, qtbot)

    NPA = prc.NO_PORT_ATTRIBUTE_ASSOCIATED_TEXT
    PWR_FLOATING = prc.MACRO_POWER_FLOATING_TEXT
    expected = {
        # name          (Receiver,     Driver)
        "clk":        (PWR_FLOATING, NPA),
        "cen_b":      (PWR_FLOATING, NPA),
        "wen_b":      (PWR_FLOATING, NPA),
        "sleep_b":    ("SS_VDD",     NPA),
        "iso_en":     ("SS_VDD",     NPA),
        "a":          (PWR_FLOATING, NPA),
        "d":          (PWR_FLOATING, NPA),
        "q":          (NPA,          PWR_FLOATING),
        "power_good": (NPA,          "SS_VDD"),
    }
    for name, expected_cell in expected.items():
        assert rows[name] == expected_cell, f"sram.{name}: expected {expected_cell}, got {rows[name]}"


def test_golden_macro_pg_pin_crossed_rails_shows_conflict_scoped_to_the_pg_pin_pair(qtbot):
    """CASE 4 -- both VDD_INT and VSS_INT ARE connected (nothing open),
    but wired to rails belonging to DIFFERENT Supply Sets (VDD_INT ->
    SS_VDD_SUB's own power net, VSS_INT -> SS_VDD's own ground net) -- a
    genuine cross-domain wiring mistake. "Conflict - Supply Set",
    distinct from both the Floating cases (a rail genuinely missing) and
    the fully-connected case (both rails correctly belonging to the same
    Supply Set).

    Scope explicitly confirmed (flagged back rather than guessed at,
    per direct request): PER-RAIL-PAIR, not macro-instance-wide -- only
    the 6 pins referencing the crossed VDD_INT/VSS_INT pair (clk, cen_b,
    wen_b, a, d, q) show the conflict; sleep_b/iso_en/power_good
    (referencing the correctly-paired, unaffected VDD/VSS rails) stay at
    their normal SS_VDD value, matching how Cases 2/3 above already
    scope their own Floating markers."""
    supply_state = _wire_sram_pg_pin_connectivity(
        [("VDD", "VDD"), ("VSS", "VSS"), ("VDD_INT", "VDD_SUB"), ("VSS_INT", "VSS")]
    )
    rows = _sram_pg_pin_connectivity_port_list_table(supply_state, qtbot)

    NPA = prc.NO_PORT_ATTRIBUTE_ASSOCIATED_TEXT
    CONFLICT = prc.CONFLICT_SUPPLY_SET_TEXT
    expected = {
        # name          (Receiver,  Driver)
        "clk":        (CONFLICT, NPA),
        "cen_b":      (CONFLICT, NPA),
        "wen_b":      (CONFLICT, NPA),
        "sleep_b":    ("SS_VDD", NPA),
        "iso_en":     ("SS_VDD", NPA),
        "a":          (CONFLICT, NPA),
        "d":          (CONFLICT, NPA),
        "q":          (NPA,      CONFLICT),
        "power_good": (NPA,      "SS_VDD"),
    }
    for name, expected_cell in expected.items():
        assert rows[name] == expected_cell, f"sram.{name}: expected {expected_cell}, got {rows[name]}"


# --- GOLDEN TEST CASES 5 & 6: legal single-rail sharing between Primary and Secondary ---
# Same sram.v/sram.lib fixture as Cases 1-4 above -- no new RTL/LIB files
# needed. Both cases confirm a real, previously-hidden bug found while
# building them: an earlier version of _resolve_macro_power_net_supply()
# resolved each rail (power, ground) to a SINGLE Supply Set
# independently, picking whichever candidate happened to be first in
# dict-iteration order -- correct when a rail is unique, but WRONG the
# moment a rail is legally shared by more than one Supply Set (partial
# overlap, explicitly legal per the new pin-combination-uniqueness
# validation's own "only a FULL match is disallowed" rule): the shared
# rail's own arbitrary first-match didn't reliably agree with the
# OTHER, uniquely-resolving rail's own answer, spuriously reporting
# "Conflict - Supply Set" for a perfectly legal, unambiguous
# configuration. Fixed by resolving the PAIR as a unit (see
# _resolve_macro_power_net_supply()'s own docstring for the full
# writeup) -- these two golden fixtures lock that fix in permanently.
#
# Case 4 (Conflict) is re-verified immediately above this comment block
# as an unaffected regression check: the pair-matching fix changes
# nothing about a GENUINE crossed-rail mismatch (no full-pair match
# exists at all), only the previously-mishandled legal-sharing case.


def _pg_pin_connectivity_domain_state(ss_vdd_kwargs: dict, ss_vdd_sub_kwargs: dict) -> PowerDomainDiagramState:
    state = PowerDomainDiagramState()
    state.supply_sets["ss_vdd"] = SupplySetPlacement(id="ss_vdd", name="SS_VDD", owner_path="sram", **ss_vdd_kwargs)
    state.supply_sets["ss_vdd_sub"] = SupplySetPlacement(id="ss_vdd_sub", name="SS_VDD_SUB", owner_path="sram", **ss_vdd_sub_kwargs)
    state.domains["pd_ram"] = PowerDomainPlacement(
        id="pd_ram", owner_path="sram", name="PD_RAM", color=QColor("#ff0000"), primary_supply_set="ss_vdd", secondary_supply_sets=["ss_vdd_sub"]
    )
    return state


def test_golden_macro_pg_pin_shared_ground_between_primary_and_secondary_resolves_cleanly(qtbot):
    """CASE 5 -- SS_VDD (Power=VDD, Ground=VSS) and SS_VDD_SUB
    (Power=VDD_SUB, Ground=VSS) legally SHARE Ground (VSS) -- only ONE
    rail shared, not both, so this remains creatable under the new
    pin-combination-uniqueness validation. NOT a conflict: VDD_INT's
    connection to VDD_SUB is uniquely SS_VDD_SUB's own power rail (not
    shared with SS_VDD), which alone is sufficient to disambiguate --
    VSS being shared doesn't create ambiguity since it's also correctly
    SS_VDD_SUB's own assigned ground."""
    design = _macro_pg_pin_connectivity_design()
    domain_state = _pg_pin_connectivity_domain_state(
        dict(power="VDD", ground="VSS"),
        dict(power="VDD_SUB", ground="VSS"),
    )
    panel = PowerIntentPropertiesPanel(PowerIntentSelectionController())
    qtbot.addWidget(panel)
    panel.load_design(design)
    supply_state = _wire_sram_pg_pin_connectivity([("VDD", "VDD"), ("VSS", "VSS"), ("VDD_INT", "VDD_SUB"), ("VSS_INT", "VSS")])
    panel.refresh_diagram_overlay(supply_state, domain_state, None, None)

    table = panel.design_properties._port_panel.table
    panel.design_properties.apply_selection({"sram"})
    rows = {table.item(r, 0).text(): (table.item(r, 3).text(), table.item(r, 4).text()) for r in range(table.rowCount())}

    NPA = prc.NO_PORT_ATTRIBUTE_ASSOCIATED_TEXT
    expected = {
        # name          (Receiver,     Driver)
        "clk":        ("SS_VDD_SUB", NPA),
        "cen_b":      ("SS_VDD_SUB", NPA),
        "wen_b":      ("SS_VDD_SUB", NPA),
        "sleep_b":    ("SS_VDD",     NPA),
        "iso_en":     ("SS_VDD",     NPA),
        "a":          ("SS_VDD_SUB", NPA),
        "d":          ("SS_VDD_SUB", NPA),
        "q":          (NPA,          "SS_VDD_SUB"),
        "power_good": (NPA,          "SS_VDD"),
    }
    for name, expected_cell in expected.items():
        assert rows[name] == expected_cell, f"sram.{name}: expected {expected_cell}, got {rows[name]}"


def test_golden_macro_pg_pin_shared_power_between_primary_and_secondary_resolves_cleanly(qtbot):
    """CASE 6 -- mirror of Case 5 with power/ground roles swapped:
    SS_VDD (Power=VDD, Ground=VSS) and SS_VDD_SUB (Power=VDD,
    Ground=VSS_SUB) legally SHARE Power (VDD). NOT a conflict:
    VSS_INT's connection to VSS_SUB is uniquely SS_VDD_SUB's own ground
    rail (not shared with SS_VDD), which alone disambiguates."""
    design = _macro_pg_pin_connectivity_design()
    domain_state = _pg_pin_connectivity_domain_state(
        dict(power="VDD", ground="VSS"),
        dict(power="VDD", ground="VSS_SUB"),
    )
    panel = PowerIntentPropertiesPanel(PowerIntentSelectionController())
    qtbot.addWidget(panel)
    panel.load_design(design)
    supply_state = _wire_sram_pg_pin_connectivity([("VDD", "VDD"), ("VSS", "VSS"), ("VDD_INT", "VDD"), ("VSS_INT", "VSS_SUB")])
    panel.refresh_diagram_overlay(supply_state, domain_state, None, None)

    table = panel.design_properties._port_panel.table
    panel.design_properties.apply_selection({"sram"})
    rows = {table.item(r, 0).text(): (table.item(r, 3).text(), table.item(r, 4).text()) for r in range(table.rowCount())}

    NPA = prc.NO_PORT_ATTRIBUTE_ASSOCIATED_TEXT
    expected = {
        # name          (Receiver,     Driver)
        "clk":        ("SS_VDD_SUB", NPA),
        "cen_b":      ("SS_VDD_SUB", NPA),
        "wen_b":      ("SS_VDD_SUB", NPA),
        "sleep_b":    ("SS_VDD",     NPA),
        "iso_en":     ("SS_VDD",     NPA),
        "a":          ("SS_VDD_SUB", NPA),
        "d":          ("SS_VDD_SUB", NPA),
        "q":          (NPA,          "SS_VDD_SUB"),
        "power_good": (NPA,          "SS_VDD"),
    }
    for name, expected_cell in expected.items():
        assert rows[name] == expected_cell, f"sram.{name}: expected {expected_cell}, got {rows[name]}"


# --- GOLDEN TEST CASES: NWELL/PWELL body-bias validation --------------------
# A NEW LIB fixture (simple5/sram.v/sram.lib, stored as its own
# verilog_macro_well_bias_validation/ golden fixture directory --
# genuinely a different LIB file, not a modification of the earlier
# sram.v/sram.lib fixtures, since it adds VPB/VNB well pins the earlier
# ones never had). Confirms a new, warning-only (never blocking)
# annotation: a resolved macro pin's own Supply Set name gets
# BIAS_CONFLICT_SUFFIX (" - Bias Conflict", italic) appended whenever
# the LIB's own well-bias pg_pin (pg_type nwell/pwell, related_bias_pin)
# implies a different Nwell/Pwell than what the resolved Supply Set
# actually declares -- a valid-but-unusual body-bias design choice
# (wells shorted to ground/power instead of following the expected
# rail), not an error.
#
# IMPORTANT SCOPING, confirmed by this fixture's own LIB: well pins
# (VPB/VNB) are associated ONLY with the MAIN PG domain (VDD/VSS) --
# there is NO well association for the INTERNAL domain (VDD_INT/
# VSS_INT) at all. Every case below confirms pins resolving through the
# internal domain (clk/cen_b/wen_b/a/d/q) are NEVER annotated,
# regardless of what SS_VDD_INT's own Nwell/Pwell happen to be set to
# -- the check only ever fires for a PG-pin domain the LIB actually
# ties a well pin to.


def _macro_well_bias_design():
    golden = Path(__file__).parent.parent / "backend" / "golden_rtl" / "verilog_macro_well_bias_validation"
    real_design = elaborate_rtl([str(golden / "sram.v")], [str(golden / "sram.lib")], "sram")
    return adapt_design(real_design)


def _macro_well_bias_domain_state(ss_vdd_kwargs: dict, ss_vdd_int_kwargs: dict) -> PowerDomainDiagramState:
    state = PowerDomainDiagramState()
    state.supply_sets["ss_vdd"] = SupplySetPlacement(id="ss_vdd", name="SS_VDD", owner_path="sram", **ss_vdd_kwargs)
    state.supply_sets["ss_vdd_int"] = SupplySetPlacement(id="ss_vdd_int", name="SS_VDD_INT", owner_path="sram", **ss_vdd_int_kwargs)
    state.domains["pd_ram"] = PowerDomainPlacement(
        id="pd_ram", owner_path="sram", name="PD_RAM", color=QColor("#ff0000"), primary_supply_set="ss_vdd", secondary_supply_sets=["ss_vdd_int"]
    )
    return state


def _macro_well_bias_port_list_table(domain_state: PowerDomainDiagramState, qtbot) -> dict[str, tuple[str, str]]:
    design = _macro_well_bias_design()
    panel = PowerIntentPropertiesPanel(PowerIntentSelectionController())
    qtbot.addWidget(panel)
    panel.load_design(design)
    supply_state = _wire_sram_pg_pin_connectivity([("VDD", "VDD"), ("VSS", "VSS"), ("VDD_INT", "VDD_INT"), ("VSS_INT", "VSS_INT")])
    panel.refresh_diagram_overlay(supply_state, domain_state, None, None)

    table = panel.design_properties._port_panel.table
    panel.design_properties.apply_selection({"sram"})
    return {table.item(r, 0).text(): (table.item(r, 3).text(), table.item(r, 4).text()) for r in range(table.rowCount())}


def test_golden_macro_well_bias_correct_bias_shows_no_warning(qtbot):
    """CASE 1 -- SS_VDD's own Nwell=VDD/Pwell=VSS matches the LIB's own
    VPB(bias=VDD)/VNB(bias=VSS) exactly -- no warning anywhere. SS_VDD_INT's
    own Nwell/Pwell are irrelevant and must not be checked at all (no
    well pin ties to the internal domain in this LIB)."""
    domain_state = _macro_well_bias_domain_state(
        dict(power="VDD", ground="VSS", n_well="VDD", p_well="VSS"),
        dict(power="VDD_INT", ground="VSS_INT", n_well="VDD_INT", p_well="VSS_INT"),
    )
    rows = _macro_well_bias_port_list_table(domain_state, qtbot)

    NPA = prc.NO_PORT_ATTRIBUTE_ASSOCIATED_TEXT
    expected = {
        # name          (Receiver,      Driver)
        "clk":        ("SS_VDD_INT", NPA),
        "cen_b":      ("SS_VDD_INT", NPA),
        "wen_b":      ("SS_VDD_INT", NPA),
        "sleep_b":    ("SS_VDD",     NPA),
        "iso_en":     ("SS_VDD",     NPA),
        "a":          ("SS_VDD_INT", NPA),
        "d":          ("SS_VDD_INT", NPA),
        "q":          (NPA,          "SS_VDD_INT"),
        "power_good": (NPA,          "SS_VDD"),
    }
    for name, expected_cell in expected.items():
        assert rows[name] == expected_cell, f"sram.{name}: expected {expected_cell}, got {rows[name]}"


def test_golden_macro_well_bias_body_bias_to_ground_warns(qtbot):
    """CASE 2 -- SS_VDD's own Nwell/Pwell are BOTH shorted to VSS (a
    valid, if unusual, body-bias-to-ground configuration): Nwell (VSS)
    mismatches the LIB-expected VDD -- alone sufficient to trigger
    "SS_VDD - Bias Conflict" on every pin resolving to SS_VDD (sleep_b/
    iso_en/power_good). SS_VDD_INT-resolved pins stay completely
    unannotated."""
    domain_state = _macro_well_bias_domain_state(
        dict(power="VDD", ground="VSS", n_well="VSS", p_well="VSS"),
        dict(power="VDD_INT", ground="VSS_INT", n_well="VSS_INT", p_well="VSS_INT"),
    )
    rows = _macro_well_bias_port_list_table(domain_state, qtbot)

    NPA = prc.NO_PORT_ATTRIBUTE_ASSOCIATED_TEXT
    BIAS = "SS_VDD" + prc.BIAS_CONFLICT_SUFFIX
    expected = {
        # name          (Receiver,      Driver)
        "clk":        ("SS_VDD_INT", NPA),
        "cen_b":      ("SS_VDD_INT", NPA),
        "wen_b":      ("SS_VDD_INT", NPA),
        "sleep_b":    (BIAS,         NPA),
        "iso_en":     (BIAS,         NPA),
        "a":          ("SS_VDD_INT", NPA),
        "d":          ("SS_VDD_INT", NPA),
        "q":          (NPA,          "SS_VDD_INT"),
        "power_good": (NPA,          BIAS),
    }
    for name, expected_cell in expected.items():
        assert rows[name] == expected_cell, f"sram.{name}: expected {expected_cell}, got {rows[name]}"


def test_golden_macro_well_bias_body_bias_to_power_warns(qtbot):
    """CASE 3 -- mirror of Case 2: SS_VDD's own Nwell/Pwell are BOTH
    shorted to VDD instead -- Pwell (VDD) mismatches the LIB-expected
    VSS this time, same annotation pattern."""
    domain_state = _macro_well_bias_domain_state(
        dict(power="VDD", ground="VSS", n_well="VDD", p_well="VDD"),
        dict(power="VDD_INT", ground="VSS_INT", n_well="VDD_INT", p_well="VDD_INT"),
    )
    rows = _macro_well_bias_port_list_table(domain_state, qtbot)

    NPA = prc.NO_PORT_ATTRIBUTE_ASSOCIATED_TEXT
    BIAS = "SS_VDD" + prc.BIAS_CONFLICT_SUFFIX
    expected = {
        # name          (Receiver,      Driver)
        "clk":        ("SS_VDD_INT", NPA),
        "cen_b":      ("SS_VDD_INT", NPA),
        "wen_b":      ("SS_VDD_INT", NPA),
        "sleep_b":    (BIAS,         NPA),
        "iso_en":     (BIAS,         NPA),
        "a":          ("SS_VDD_INT", NPA),
        "d":          ("SS_VDD_INT", NPA),
        "q":          (NPA,          "SS_VDD_INT"),
        "power_good": (NPA,          BIAS),
    }
    for name, expected_cell in expected.items():
        assert rows[name] == expected_cell, f"sram.{name}: expected {expected_cell}, got {rows[name]}"


def test_golden_macro_well_bias_no_wells_at_all_is_an_automatic_conflict(qtbot):
    """N-Well/P-Well are now optional on a Supply Set (explicit follow-up
    request). Confirmed with the user before implementing (flagged as a
    genuine open question rather than guessed): a Supply Set with NO
    wells set at all, resolving against a macro pin the LIB DOES
    declare a well-bias pin for, is an AUTOMATIC "Bias Conflict" -- the
    exact same annotation as an explicitly-mismatched well, not a
    separate "unconfigured" marker. This already falls out of
    _macro_well_bias_conflict()'s own plain `!=` comparison (None never
    equals a real net name) with zero code changes -- this test locks
    that specific, confirmed-intentional behavior in."""
    domain_state = _macro_well_bias_domain_state(
        dict(power="VDD", ground="VSS"),  # n_well/p_well omitted entirely -- None, not just unset-but-truthy
        dict(power="VDD_INT", ground="VSS_INT", n_well="VDD_INT", p_well="VSS_INT"),
    )
    rows = _macro_well_bias_port_list_table(domain_state, qtbot)

    NPA = prc.NO_PORT_ATTRIBUTE_ASSOCIATED_TEXT
    BIAS = "SS_VDD" + prc.BIAS_CONFLICT_SUFFIX
    expected = {
        # name          (Receiver,      Driver)
        "clk":        ("SS_VDD_INT", NPA),
        "cen_b":      ("SS_VDD_INT", NPA),
        "wen_b":      ("SS_VDD_INT", NPA),
        "sleep_b":    (BIAS,         NPA),
        "iso_en":     (BIAS,         NPA),
        "a":          ("SS_VDD_INT", NPA),
        "d":          ("SS_VDD_INT", NPA),
        "q":          (NPA,          "SS_VDD_INT"),
        "power_good": (NPA,          BIAS),
    }
    for name, expected_cell in expected.items():
        assert rows[name] == expected_cell, f"sram.{name}: expected {expected_cell}, got {rows[name]}"


def test_golden_macro_well_bias_conflict_cell_is_rendered_italic(qtbot):
    """The whole compound "SS_VDD - Bias Conflict" string is rendered
    italic (no per-substring styling mechanism exists -- see
    ResolvedSupplyDisplay's own docstring) -- confirmed against the
    actual QTableWidgetItem font, not just the resolved text."""
    domain_state = _macro_well_bias_domain_state(
        dict(power="VDD", ground="VSS", n_well="VSS", p_well="VSS"),
        dict(power="VDD_INT", ground="VSS_INT", n_well="VSS_INT", p_well="VSS_INT"),
    )
    design = _macro_well_bias_design()
    panel = PowerIntentPropertiesPanel(PowerIntentSelectionController())
    qtbot.addWidget(panel)
    panel.load_design(design)
    supply_state = _wire_sram_pg_pin_connectivity([("VDD", "VDD"), ("VSS", "VSS"), ("VDD_INT", "VDD_INT"), ("VSS_INT", "VSS_INT")])
    panel.refresh_diagram_overlay(supply_state, domain_state, None, None)

    table = panel.design_properties._port_panel.table
    panel.design_properties.apply_selection({"sram"})
    row_index = next(r for r in range(table.rowCount()) if table.item(r, 0).text() == "sleep_b")
    receiver_item = table.item(row_index, 3)
    assert receiver_item.text() == "SS_VDD" + prc.BIAS_CONFLICT_SUFFIX
    assert receiver_item.font().italic() is True


# --- GOLDEN TEST CASES: exposed well pin (VPW) as a real connectable pin + default-domain bias check ---
# A NEW LIB fixture (simple6/sram.v/sram.lib, its own
# verilog_macro_exposed_well_pin/ golden fixture directory, alongside
# simple5's implicit-only fixture, not replacing it) -- adds an EXPOSED
# pwell pin (VPW: pg_type pwell, NO related_bias_pin, its own
# voltage_name, physical_connection: device_layer) while VPB (nwell,
# related_bias_pin=VDD) stays implicit, unchanged from simple5.
#
# Confirms two things: (1) VPW becomes a real, independently
# connectable macro pin (PortDirection.PG_WELL) -- surfaced in the
# Supply Port Properties tab exactly like VDD/VSS/VDD_INT/VSS_INT, NOT
# folded into well_bias_pins; (2) the DEFAULT-domain bias-conflict rule
# for an exposed well pin (no related_bias_pin stated at all): expected
# to ride on the SAME Supply Set as the macro's own PRIMARY (non-
# internal) PG domain -- confirmed against the codebase (grounded in
# CreateSupplySetDialog's own N-Well/P-Well-must-match-Power-or-Ground
# validation) before implementing, per explicit request.


def _macro_exposed_well_design():
    golden = Path(__file__).parent.parent / "backend" / "golden_rtl" / "verilog_macro_exposed_well_pin"
    real_design = elaborate_rtl([str(golden / "sram.v")], [str(golden / "sram.lib")], "sram")
    return adapt_design(real_design)


def _macro_exposed_well_domain_state() -> PowerDomainDiagramState:
    state = PowerDomainDiagramState()
    state.supply_sets["ss_vdd"] = SupplySetPlacement(id="ss_vdd", name="SS_VDD", power="VDD", ground="VSS", n_well="VDD", p_well="VSS", owner_path="sram")
    state.supply_sets["ss_vdd_int"] = SupplySetPlacement(
        id="ss_vdd_int", name="SS_VDD_INT", power="VDD_INT", ground="VSS", n_well="VDD_INT", p_well="VSS", owner_path="sram"
    )
    state.supply_sets["ss_vdd_mem"] = SupplySetPlacement(
        id="ss_vdd_mem", name="SS_VDD_MEM", power="VDD", ground="VSS", n_well="VDD", p_well="VPW", owner_path="sram"
    )
    state.domains["pd_top"] = PowerDomainPlacement(
        id="pd_top", owner_path="sram", name="PD_TOP", color=QColor("#ff0000"), primary_supply_set="ss_vdd", secondary_supply_sets=["ss_vdd_int", "ss_vdd_mem"]
    )
    return state


def _macro_exposed_well_port_list_table(vpw_target: str, qtbot) -> dict[str, tuple[str, str]]:
    design = _macro_exposed_well_design()
    panel = PowerIntentPropertiesPanel(PowerIntentSelectionController())
    qtbot.addWidget(panel)
    panel.load_design(design)
    supply_state = _wire_sram_pg_pin_connectivity(
        [("VDD", "VDD"), ("VSS", "VSS"), ("VPW", vpw_target), ("VDD_INT", "VDD_INT"), ("VSS_INT", "VSS")]
    )
    panel.refresh_diagram_overlay(supply_state, _macro_exposed_well_domain_state(), None, None)

    table = panel.design_properties._port_panel.table
    panel.design_properties.apply_selection({"sram"})
    return {table.item(r, 0).text(): (table.item(r, 3).text(), table.item(r, 4).text()) for r in range(table.rowCount())}


def test_golden_macro_exposed_well_pin_wired_to_primary_domain_shows_no_conflict(qtbot):
    """CASE 1 -- VPW wired to a Supply Port belonging to SS_VDD (the
    SAME Supply Set VDD/VSS themselves resolve to) -- no Bias Conflict
    anywhere, matching the earlier "clean" baseline exactly. Which
    SPECIFIC Supply Port within SS_VDD VPW connects to is a design
    choice this tool deliberately never second-guesses beyond domain-
    level consistency (explicit scope note)."""
    rows = _macro_exposed_well_port_list_table("VDD", qtbot)

    NPA = prc.NO_PORT_ATTRIBUTE_ASSOCIATED_TEXT
    expected = {
        # name          (Receiver,      Driver)
        "clk":        ("SS_VDD_INT", NPA),
        "cen_b":      ("SS_VDD_INT", NPA),
        "wen_b":      ("SS_VDD_INT", NPA),
        "sleep_b":    ("SS_VDD",     NPA),
        "iso_en":     ("SS_VDD",     NPA),
        "a":          ("SS_VDD_INT", NPA),
        "d":          ("SS_VDD_INT", NPA),
        "q":          (NPA,          "SS_VDD_INT"),
        "power_good": (NPA,          "SS_VDD"),
    }
    for name, expected_cell in expected.items():
        assert rows[name] == expected_cell, f"sram.{name}: expected {expected_cell}, got {rows[name]}"


def test_golden_macro_exposed_well_pin_wired_to_a_different_domain_warns(qtbot):
    """CASE 2 -- VPW wired to a Supply Port belonging to SS_VDD_INT
    instead (a DIFFERENT Supply Set than VDD/VSS's own SS_VDD) --
    triggers "SS_VDD - Bias Conflict" on every pin resolving to SS_VDD
    (sleep_b/iso_en/power_good). SS_VDD_INT-resolved pins (clk/cen_b/
    wen_b/a/d/q) remain completely unaffected -- the conflict is
    specifically about VPW's mismatch against the PRIMARY domain."""
    rows = _macro_exposed_well_port_list_table("VDD_INT", qtbot)

    NPA = prc.NO_PORT_ATTRIBUTE_ASSOCIATED_TEXT
    BIAS = "SS_VDD" + prc.BIAS_CONFLICT_SUFFIX
    expected = {
        # name          (Receiver,      Driver)
        "clk":        ("SS_VDD_INT", NPA),
        "cen_b":      ("SS_VDD_INT", NPA),
        "wen_b":      ("SS_VDD_INT", NPA),
        "sleep_b":    (BIAS,         NPA),
        "iso_en":     (BIAS,         NPA),
        "a":          ("SS_VDD_INT", NPA),
        "d":          ("SS_VDD_INT", NPA),
        "q":          (NPA,          "SS_VDD_INT"),
        "power_good": (NPA,          BIAS),
    }
    for name, expected_cell in expected.items():
        assert rows[name] == expected_cell, f"sram.{name}: expected {expected_cell}, got {rows[name]}"


def test_golden_macro_exposed_well_pin_shows_in_supply_port_tab(qtbot):
    """Explicit request: the Supply Port Properties tab must reflect
    VPW's own connection, exactly the same way it already shows VDD/
    VSS/VDD_INT/VSS_INT -- same "Macro PG" Type label (not "Macro PG -
    Internal", since PG_WELL isn't PG_INTERNAL), "Connected" showing
    whichever Supply Port the user wired it to."""
    design = _macro_exposed_well_design()
    panel = PowerIntentPropertiesPanel(PowerIntentSelectionController())
    qtbot.addWidget(panel)
    panel.load_design(design)
    supply_state = _wire_sram_pg_pin_connectivity(
        [("VDD", "VDD"), ("VSS", "VSS"), ("VPW", "VDD"), ("VDD_INT", "VDD_INT"), ("VSS_INT", "VSS")]
    )
    panel.refresh_diagram_overlay(supply_state, _macro_exposed_well_domain_state(), None, None)

    table = panel._tables["SupplyPort"]
    # Connected is column 3 -- the table gained "Direction" at column 2.
    rows = {table.item(r, 1).text(): (table.item(r, 0).text(), table.item(r, 3).text()) for r in range(table.rowCount())}
    assert rows["u_sram/VPW"] == ("Macro PG", "VDD")


def test_golden_macro_exposed_well_pin_simple5_fixture_still_passes_unaffected(qtbot):
    """Confirms the simple5 implicit-only (VPB/VNB, no exposed well
    pin) fixture is completely unaffected by this additive feature --
    reruns its own Case 1 baseline."""
    golden = Path(__file__).parent.parent / "backend" / "golden_rtl" / "verilog_macro_well_bias_validation"
    real_design = elaborate_rtl([str(golden / "sram.v")], [str(golden / "sram.lib")], "sram")
    design = adapt_design(real_design)
    domain_state = _macro_well_bias_domain_state(
        dict(power="VDD", ground="VSS", n_well="VDD", p_well="VSS"),
        dict(power="VDD_INT", ground="VSS_INT", n_well="VDD_INT", p_well="VSS_INT"),
    )
    panel = PowerIntentPropertiesPanel(PowerIntentSelectionController())
    qtbot.addWidget(panel)
    panel.load_design(design)
    supply_state = _wire_sram_pg_pin_connectivity([("VDD", "VDD"), ("VSS", "VSS"), ("VDD_INT", "VDD_INT"), ("VSS_INT", "VSS_INT")])
    panel.refresh_diagram_overlay(supply_state, domain_state, None, None)

    table = panel.design_properties._port_panel.table
    panel.design_properties.apply_selection({"sram"})
    rows = {table.item(r, 0).text(): (table.item(r, 3).text(), table.item(r, 4).text()) for r in range(table.rowCount())}

    NPA = prc.NO_PORT_ATTRIBUTE_ASSOCIATED_TEXT
    expected = {
        "clk":        ("SS_VDD_INT", NPA),
        "cen_b":      ("SS_VDD_INT", NPA),
        "wen_b":      ("SS_VDD_INT", NPA),
        "sleep_b":    ("SS_VDD",     NPA),
        "iso_en":     ("SS_VDD",     NPA),
        "a":          ("SS_VDD_INT", NPA),
        "d":          ("SS_VDD_INT", NPA),
        "q":          (NPA,          "SS_VDD_INT"),
        "power_good": (NPA,          "SS_VDD"),
    }
    for name, expected_cell in expected.items():
        assert rows[name] == expected_cell, f"sram.{name}: expected {expected_cell}, got {rows[name]}"


# --- GOLDEN TEST CASES: exposed NWELL pin (VNW) -- mirror of the exposed-PWELL (VPW) cases, roles swapped ---
# A NEW LIB fixture (simple7/sram.v/sram.lib, its own
# verilog_macro_exposed_well_pin_nwell/ golden fixture directory,
# alongside simple6's exposed-PWELL fixture, not replacing it) -- SWAPS
# which well pin is exposed vs. implicit: VNW (nwell) is now EXPOSED
# (no related_bias_pin, own voltage_name, physical_connection:
# device_layer), while VPW (pwell) is now IMPLICIT (related_bias_pin:
# "VSS"), the mirror image of simple6's own VPB(nwell,implicit)/
# VPW(pwell,exposed) split.
#
# PRIMARY PURPOSE (explicit request): confirms the exposed-well-pin
# implementation generalizes across BOTH well types (nwell and pwell),
# rather than having been implemented in a way that only worked for
# pwell specifically. Verified: zero code changes were needed to make
# these two cases pass -- _build_exposed_well_port()/
# _macro_exposed_well_bias_conflict() key off `pg_type in
# _WELL_PG_TYPES`/`PortDirection.PG_WELL` and presence/absence of
# `related_bias_pin` generically, never a hardcoded pin name or a
# nwell-vs-pwell-specific branch for the EXPOSED case (unlike the
# IMPLICIT check, _macro_well_bias_conflict(), which DOES branch on
# pg_type -- but only to pick which SupplySetPlacement field, n_well vs
# p_well, to compare against, still driven by the LIB's own pg_type,
# never a hardcoded pin name either).


def _macro_exposed_well_nwell_design():
    golden = Path(__file__).parent.parent / "backend" / "golden_rtl" / "verilog_macro_exposed_well_pin_nwell"
    real_design = elaborate_rtl([str(golden / "sram.v")], [str(golden / "sram.lib")], "sram")
    return adapt_design(real_design)


def _macro_exposed_well_nwell_domain_state() -> PowerDomainDiagramState:
    state = PowerDomainDiagramState()
    state.supply_sets["ss_vdd"] = SupplySetPlacement(id="ss_vdd", name="SS_VDD", power="VDD", ground="VSS", n_well="VNW", p_well="VSS", owner_path="sram")
    state.supply_sets["ss_vdd_int"] = SupplySetPlacement(
        id="ss_vdd_int", name="SS_VDD_INT", power="VDD_INT", ground="VSS", n_well="VNW", p_well="VSS", owner_path="sram"
    )
    state.domains["pd_top"] = PowerDomainPlacement(
        id="pd_top", owner_path="sram", name="PD_TOP", color=QColor("#ff0000"), primary_supply_set="ss_vdd", secondary_supply_sets=["ss_vdd_int"]
    )
    return state


def _macro_exposed_well_nwell_port_list_table(vnw_target: str, qtbot) -> dict[str, tuple[str, str]]:
    design = _macro_exposed_well_nwell_design()
    panel = PowerIntentPropertiesPanel(PowerIntentSelectionController())
    qtbot.addWidget(panel)
    panel.load_design(design)
    supply_state = _wire_sram_pg_pin_connectivity(
        [("VDD", "VDD"), ("VSS", "VSS"), ("VNW", vnw_target), ("VDD_INT", "VDD_INT"), ("VSS_INT", "VSS")]
    )
    panel.refresh_diagram_overlay(supply_state, _macro_exposed_well_nwell_domain_state(), None, None)

    table = panel.design_properties._port_panel.table
    panel.design_properties.apply_selection({"sram"})
    return {table.item(r, 0).text(): (table.item(r, 3).text(), table.item(r, 4).text()) for r in range(table.rowCount())}


def test_golden_macro_exposed_nwell_pin_wired_to_primary_domain_shows_no_conflict(qtbot):
    """CASE 1 -- VNW wired to a Supply Port belonging to SS_VDD (the
    SAME Supply Set VDD/VSS themselves resolve to) -- no Bias Conflict
    anywhere, exact mirror of the exposed-PWELL Case 1."""
    rows = _macro_exposed_well_nwell_port_list_table("VDD", qtbot)

    NPA = prc.NO_PORT_ATTRIBUTE_ASSOCIATED_TEXT
    expected = {
        # name          (Receiver,      Driver)
        "clk":        ("SS_VDD_INT", NPA),
        "cen_b":      ("SS_VDD_INT", NPA),
        "wen_b":      ("SS_VDD_INT", NPA),
        "sleep_b":    ("SS_VDD",     NPA),
        "iso_en":     ("SS_VDD",     NPA),
        "a":          ("SS_VDD_INT", NPA),
        "d":          ("SS_VDD_INT", NPA),
        "q":          (NPA,          "SS_VDD_INT"),
        "power_good": (NPA,          "SS_VDD"),
    }
    for name, expected_cell in expected.items():
        assert rows[name] == expected_cell, f"sram.{name}: expected {expected_cell}, got {rows[name]}"


def test_golden_macro_exposed_nwell_pin_wired_to_a_different_domain_warns(qtbot):
    """CASE 2 -- VNW wired to a Supply Port belonging to SS_VDD_INT
    instead -- triggers "SS_VDD - Bias Conflict" on every pin resolving
    to SS_VDD (sleep_b/iso_en/power_good); SS_VDD_INT-resolved pins
    remain unaffected. Exact mirror of the exposed-PWELL Case 2."""
    rows = _macro_exposed_well_nwell_port_list_table("VDD_INT", qtbot)

    NPA = prc.NO_PORT_ATTRIBUTE_ASSOCIATED_TEXT
    BIAS = "SS_VDD" + prc.BIAS_CONFLICT_SUFFIX
    expected = {
        # name          (Receiver,      Driver)
        "clk":        ("SS_VDD_INT", NPA),
        "cen_b":      ("SS_VDD_INT", NPA),
        "wen_b":      ("SS_VDD_INT", NPA),
        "sleep_b":    (BIAS,         NPA),
        "iso_en":     (BIAS,         NPA),
        "a":          ("SS_VDD_INT", NPA),
        "d":          ("SS_VDD_INT", NPA),
        "q":          (NPA,          "SS_VDD_INT"),
        "power_good": (NPA,          BIAS),
    }
    for name, expected_cell in expected.items():
        assert rows[name] == expected_cell, f"sram.{name}: expected {expected_cell}, got {rows[name]}"


def test_golden_macro_exposed_nwell_pin_shows_in_supply_port_tab(qtbot):
    """Supply Port Properties tab reflects VNW's own connection, same
    "Macro PG" Type label as any other PG pin -- exact mirror of the
    exposed-PWELL fixture's own equivalent test."""
    design = _macro_exposed_well_nwell_design()
    panel = PowerIntentPropertiesPanel(PowerIntentSelectionController())
    qtbot.addWidget(panel)
    panel.load_design(design)
    supply_state = _wire_sram_pg_pin_connectivity(
        [("VDD", "VDD"), ("VSS", "VSS"), ("VNW", "VDD"), ("VDD_INT", "VDD_INT"), ("VSS_INT", "VSS")]
    )
    panel.refresh_diagram_overlay(supply_state, _macro_exposed_well_nwell_domain_state(), None, None)

    table = panel._tables["SupplyPort"]
    # Connected is column 3 -- the table gained "Direction" at column 2.
    rows = {table.item(r, 1).text(): (table.item(r, 0).text(), table.item(r, 3).text()) for r in range(table.rowCount())}
    assert rows["u_sram/VNW"] == ("Macro PG", "VDD")


# --- GOLDEN TEST CASE: macro pin fanning out to BOTH the top-level boundary AND real internal RTL simultaneously ---
# A NEW fixture (simple8/sram.v/sub.sv/sram.lib, its own
# verilog_macro_boundary_and_internal_fanout/ golden fixture directory)
# -- sram instantiates BOTH the FAKE_SRAM_256X32 macro (u_sram) AND a
# real RTL module `sub` (u_sub): u_sram's own Q output feeds BOTH
# sram's own top-level `q` output port AND u_sub.i_q (real logic,
# `assign o_q = ~i_q;`); u_sub.o_q is left explicitly unconnected.
#
# A REAL, CONFIRMED BUG found building this fixture: the "standing
# rule" that a top-level port's own Receiver/Driver never reports
# internal fanout (only Port Attribute or its absence) did NOT
# actually exist as an intentional short-circuit anywhere in the code
# -- empirically, BEFORE this fixture's own fix, sram's own top-level
# `q` and the macro's own `Q` pin produced the IDENTICAL combined
# multi-line result ("External - No Port Attribute Associated\n
# SS_VDD_SUB") for their own Receiver cells, since both queries hit
# the exact same structural ReceiverResult and _resolve_receiver()
# combined ALL of its endpoints uniformly, with no special treatment
# for the top-level port's own self-referential "reached my own
# boundary" entry. Fixed via a real code change (not just a new golden
# fixture) in _resolve_receiver(): when the QUERIED endpoint itself is
# both a top-level port AND literally present in its own
# ReceiverResult.endpoints (the self-reference), short-circuit to
# Port-Attribute-only, bypassing the general multi-sink dedup/join
# loop entirely for THAT row -- a macro pin's own Receiver cell
# (querying the SAME structural result from the OTHER side) is
# unaffected and still shows the full fanout, since the macro pin
# itself is never a top-level port.
#
# A first implementation attempt (short-circuiting for EVERY top-level
# port's own Receiver query, regardless of self-reference) was
# empirically WRONG -- it broke 23 pre-existing tests, since an INPUT
# top-level port's own Receiver legitimately means "what does this
# feed inside the design" (the general multi-sink trace), not a
# boundary check. See _resolve_receiver()'s own docstring for the full
# writeup of both the bug and the fix.


def _macro_boundary_and_internal_fanout_design():
    golden = Path(__file__).parent.parent / "backend" / "golden_rtl" / "verilog_macro_boundary_and_internal_fanout"
    real_design = elaborate_rtl([str(golden / "sub.sv"), str(golden / "sram.v")], [str(golden / "sram.lib")], "sram")
    return adapt_design(real_design)


def _macro_boundary_and_internal_fanout_domain_state() -> PowerDomainDiagramState:
    state = PowerDomainDiagramState()
    state.supply_sets["ss_vdd"] = SupplySetPlacement(id="ss_vdd", name="SS_VDD", power="VDD", ground="VSS", owner_path="sram")
    state.supply_sets["ss_vdd_int"] = SupplySetPlacement(id="ss_vdd_int", name="SS_VDD_INT", power="VDD_INT", ground="VSS", owner_path="sram")
    state.domains["pd_sram"] = PowerDomainPlacement(
        id="pd_sram", owner_path="sram", name="PD_SRAM", color=QColor("#ff0000"), primary_supply_set="ss_vdd", secondary_supply_sets=["ss_vdd_int"]
    )
    state.supply_sets["ss_vdd_sub"] = SupplySetPlacement(id="ss_vdd_sub", name="SS_VDD_SUB", power="VDD_SUB", ground="VSS", owner_path="sram/u_sub")
    state.domains["pd_sub"] = PowerDomainPlacement(id="pd_sub", owner_path="sram/u_sub", name="PD_SUB", color=QColor("#00ff00"), primary_supply_set="ss_vdd_sub")
    return state


def _macro_boundary_and_internal_fanout_supply_state() -> SupplyPortDiagramState:
    supply_state = SupplyPortDiagramState()
    for pg_pin_name, port_name in [("VDD", "VDD"), ("VSS", "VSS"), ("VDD_INT", "VDD_INT")]:
        _wire_macro_pg_pin_to_supply_port(supply_state, "sram/u_sram", pg_pin_name, port_name, "sram")
    return supply_state


def _macro_boundary_and_internal_fanout_tables(qtbot):
    design = _macro_boundary_and_internal_fanout_design()
    panel = PowerIntentPropertiesPanel(PowerIntentSelectionController())
    qtbot.addWidget(panel)
    panel.load_design(design)
    panel.refresh_diagram_overlay(_macro_boundary_and_internal_fanout_supply_state(), _macro_boundary_and_internal_fanout_domain_state(), None, None)

    table = panel.design_properties._port_panel.table
    tables = {}
    for sel, label in [({"sram"}, "sram"), ({"sram/u_sram"}, "macro"), ({"sram/u_sub"}, "sub")]:
        panel.design_properties.apply_selection(sel)
        tables[label] = {table.item(r, 0).text(): (table.item(r, 3).text(), table.item(r, 4).text()) for r in range(table.rowCount())}
    return tables


def test_golden_macro_boundary_and_internal_fanout_sram_level(qtbot):
    """SRAM level: q's own Receiver shows ONLY the external-boundary
    side ("External - No Port Attribute Associated"), never the
    internal fanout to u_sub.i_q -- the standing top-level-port rule,
    confirmed (and fixed to actually hold) by this fixture."""
    tables = _macro_boundary_and_internal_fanout_tables(qtbot)
    rows = tables["sram"]

    NPA = prc.NO_PORT_ATTRIBUTE_ASSOCIATED_TEXT
    expected = {
        # name          (Receiver,     Driver)
        "clk":        ("SS_VDD_INT", NPA),
        "cen_b":      ("SS_VDD_INT", NPA),
        "wen_b":      ("SS_VDD_INT", NPA),
        "sleep_b":    ("SS_VDD",     NPA),
        "iso_en":     ("SS_VDD",     NPA),
        "a":          ("SS_VDD_INT", NPA),
        "d":          ("SS_VDD_INT", NPA),
        "q":          (NPA,          "SS_VDD_INT"),
        "power_good": (NPA,          "SS_VDD"),
    }
    for name, expected_cell in expected.items():
        assert rows[name] == expected_cell, f"sram.{name}: expected {expected_cell}, got {rows[name]}"


def test_golden_macro_boundary_and_internal_fanout_macro_level(qtbot):
    """MACRO level: Q's own Receiver shows the FULL fanout -- BOTH the
    external boundary AND u_sub.i_q's own real Supply Set (SS_VDD_SUB),
    deduplicated multi-line, since a macro pin is a genuine graph node,
    not a top-level-boundary-scoped endpoint. The SAME underlying
    structural result as sram's own `q` above, resolved completely
    differently because THIS query's own endpoint (u_sram.Q) is not
    itself a top-level port."""
    tables = _macro_boundary_and_internal_fanout_tables(qtbot)
    rows = tables["macro"]

    NPA = prc.NO_PORT_ATTRIBUTE_ASSOCIATED_TEXT
    Q_FANOUT = NPA + "\nSS_VDD_SUB"
    expected = {
        # name          (Receiver,     Driver)
        "CLK":        ("SS_VDD_INT", NPA),
        "CEN_B":      ("SS_VDD_INT", NPA),
        "WEN_B":      ("SS_VDD_INT", NPA),
        "SLEEP_B":    ("SS_VDD",     NPA),
        "ISO_EN":     ("SS_VDD",     NPA),
        "A":          ("SS_VDD_INT", NPA),
        "D":          ("SS_VDD_INT", NPA),
        "Q":          (Q_FANOUT,     "SS_VDD_INT"),
        "POWER_GOOD": (NPA,          "SS_VDD"),
    }
    for name, expected_cell in expected.items():
        assert rows[name] == expected_cell, f"sram/u_sram.{name}: expected {expected_cell}, got {rows[name]}"


def test_golden_macro_boundary_and_internal_fanout_sub_level(qtbot):
    """SUB level: i_q resolves to SS_VDD_SUB (Receiver, its own domain)
    and SS_VDD_INT (Driver, traced back through the macro's own Q pin);
    o_q's Receiver correctly resolves to "Floating" (genuinely
    unconnected, `.o_q()`), never confused with the top-level-boundary
    "External - No Port Attribute Associated" case -- a completely
    different, unrelated marker for a completely different situation."""
    tables = _macro_boundary_and_internal_fanout_tables(qtbot)
    rows = tables["sub"]

    expected = {
        # name  (Receiver,             Driver)
        "i_q": ("SS_VDD_SUB",       "SS_VDD_INT"),
        "o_q": (prc.FLOATING_TEXT,  "SS_VDD_SUB"),
    }
    for name, expected_cell in expected.items():
        assert rows[name] == expected_cell, f"sram/u_sub.{name}: expected {expected_cell}, got {rows[name]}"


# --- GOLDEN TEST CASE: same simple8 fixture, but EVERY sram-level top
# port now has a Port Attribute assigned (SS_VDD) ---
# New display rule (explicit request): a top-level port's boundary-
# facing cell, when a Port Attribute IS assigned, now reads
# "External - <SupplySetName>" ("External - " a PREFIX, italic; the
# Supply Set name itself normal) instead of the bare Supply Set name --
# distinguishing a MANUALLY-ASSERTED Port Attribute claim from a
# genuinely TRACED internal resolution (e.g. SS_VDD_INT on clk/cen_b/
# wen_b/a/d's own Receiver, still bare/unprefixed here since those come
# from real tracing, not a Port Attribute). The "no attribute assigned"
# case (NO_PORT_ATTRIBUTE_ASSOCIATED_TEXT, "External - No Port
# Attribute Associated") is unaffected -- see the sram-level test above,
# still green with no Port Attributes configured at all.


def _macro_boundary_and_internal_fanout_port_attribute_state(design: Design) -> PortAttributeDiagramState:
    """Uniformly assigns SS_VDD to every bit of every sram-level top
    port -- including the genuinely multi-bit ones this real fixture
    already has (a[7:0], d[31:0], q[31:0]) -- via the SAME
    port_attribute_row_keys() bit-wise scheme the real dialog now uses,
    not the port's own bare name (see that helper's own docstring:
    a multi-bit port's OLD bare-name key is simply never read anymore).
    Every bit agreeing on the same Supply Set reproduces the exact same
    single-line "External - SS_VDD" cell the pre-bit-wise version of
    this golden test already asserted -- see the dedicated
    bit-wise-disagreement test below for the genuinely NEW multi-line
    behavior."""
    state = PortAttributeDiagramState()
    top_definition = design.definitions[design.top]
    for port in top_definition.ports:
        for key in port_attribute_row_keys(port):
            state.assignments[key] = "ss_vdd"
    return state


def test_golden_macro_boundary_and_internal_fanout_sram_level_with_port_attributes(qtbot):
    """Same fixture/wiring as test_golden_macro_boundary_and_internal_fanout_sram_level
    above, but with every sram-level top port's own Port Attribute set
    to SS_VDD -- each port's boundary-facing cell now reads
    "External - SS_VDD" (prefix italic) instead of
    "External - No Port Attribute Associated"; every internally-traced
    cell (the OTHER side of each port) is completely unchanged."""
    design = _macro_boundary_and_internal_fanout_design()
    panel = PowerIntentPropertiesPanel(PowerIntentSelectionController())
    qtbot.addWidget(panel)
    panel.load_design(design)
    panel.refresh_diagram_overlay(
        _macro_boundary_and_internal_fanout_supply_state(),
        _macro_boundary_and_internal_fanout_domain_state(),
        None,
        _macro_boundary_and_internal_fanout_port_attribute_state(design),
    )

    table = panel.design_properties._port_panel.table
    panel.design_properties.apply_selection({"sram"})
    rows = {table.item(r, 0).text(): (table.item(r, 3).text(), table.item(r, 4).text()) for r in range(table.rowCount())}

    EXT_SS_VDD = f"{prc.EXTERNAL_PREFIX}SS_VDD"
    expected = {
        # name          (Receiver,     Driver)
        "clk":        ("SS_VDD_INT", EXT_SS_VDD),
        "cen_b":      ("SS_VDD_INT", EXT_SS_VDD),
        "wen_b":      ("SS_VDD_INT", EXT_SS_VDD),
        "sleep_b":    ("SS_VDD",     EXT_SS_VDD),
        "iso_en":     ("SS_VDD",     EXT_SS_VDD),
        "a":          ("SS_VDD_INT", EXT_SS_VDD),
        "d":          ("SS_VDD_INT", EXT_SS_VDD),
        "q":          (EXT_SS_VDD,   "SS_VDD_INT"),
        "power_good": (EXT_SS_VDD,   "SS_VDD"),
    }
    for name, expected_cell in expected.items():
        assert rows[name] == expected_cell, f"sram.{name}: expected {expected_cell}, got {rows[name]}"


def test_golden_macro_boundary_and_internal_fanout_port_attribute_prefix_is_italic_only_for_the_prefix(qtbot):
    """Directly confirms the underlying ResolvedSupplyDisplay for a
    Port-Attribute-assigned boundary cell carries italic_prefix_lens ==
    (len("External - "),) -- a single-line cell, one prefix-length entry
    -- with italic (whole-cell) left False -- the ConnectivityCellDisplay/
    QTableWidgetItem crossing is exercised by the golden test above;
    this pins the exact resolution-layer shape that
    _PartialItalicPrefixDelegate depends on to paint only the prefix in
    italic."""
    design = _macro_boundary_and_internal_fanout_design()
    structural = build_structural_cache(design)
    cache = PowerResolutionCache()
    domain_state = _macro_boundary_and_internal_fanout_domain_state()
    port_attribute_state = _macro_boundary_and_internal_fanout_port_attribute_state(design)

    display = cache.driver_display(Endpoint("sram", "clk"), structural, _macro_boundary_and_internal_fanout_supply_state(), domain_state, port_attribute_state)

    assert display.text == f"{prc.EXTERNAL_PREFIX}SS_VDD"
    assert display.italic is False
    assert display.italic_prefix_lens == (len(prc.EXTERNAL_PREFIX),)
    assert display.text[: display.italic_prefix_lens[0]] == prc.EXTERNAL_PREFIX


def test_golden_macro_boundary_and_internal_fanout_bit_wise_disagreement_shows_multi_line(qtbot):
    """The genuinely NEW bit-wise behavior: sram's own `a` port is real,
    8-bit (a[7:0], see sram.v) -- if its own bits DISAGREE on which
    Supply Set is assigned (via port_attribute_row_keys()'s own
    "a[0]".."a[7]" keys, not the single bare "a" key the pre-bit-wise
    editor used), the Driver cell must show ONE "External - <name>" line
    per DISTINCT assigned Supply Set, deduplicated, each independently
    italic-prefixed -- not silently collapse to just one bit's own
    value. Every bit still resolving to the SAME Supply Set (the golden
    test above) stays a single line -- this is the disagreement case."""
    design = _macro_boundary_and_internal_fanout_design()
    structural = build_structural_cache(design)
    cache = PowerResolutionCache()
    domain_state = _macro_boundary_and_internal_fanout_domain_state()
    port_attribute_state = PortAttributeDiagramState(
        assignments={"a[0]": "ss_vdd", "a[1]": "ss_vdd", "a[2]": "ss_vdd_int", "a[3]": "ss_vdd_int"}
    )

    display = cache.driver_display(Endpoint("sram", "a"), structural, _macro_boundary_and_internal_fanout_supply_state(), domain_state, port_attribute_state)

    EXT_SS_VDD = f"{prc.EXTERNAL_PREFIX}SS_VDD"
    EXT_SS_VDD_INT = f"{prc.EXTERNAL_PREFIX}SS_VDD_INT"
    assert display.text == f"{EXT_SS_VDD}\n{EXT_SS_VDD_INT}"
    assert display.italic_prefix_lens == (len(prc.EXTERNAL_PREFIX), len(prc.EXTERNAL_PREFIX))
    lines = display.text.split("\n")
    for line, prefix_len in zip(lines, display.italic_prefix_lens):
        assert line[:prefix_len] == prc.EXTERNAL_PREFIX
        assert line[prefix_len:] in ("SS_VDD", "SS_VDD_INT")
