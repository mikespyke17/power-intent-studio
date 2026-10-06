"""1e: Liberty (.lib) black-box macro integration.

Two layers: pure `.lib` parsing (elaborate(), no ghdl/yosys needed -- these
run unconditionally) and resolution against real RTL front-ends
(elaborate_with_black_boxes() / resolve_black_boxes()). The VHDL-side
resolution tests need ghdl/yosys the same way test_mixed_language.py does,
skipped (not failed) when unavailable; the Verilog-side ones don't and
always run.

See docs/milestone1_rtl_elaborator.md for the full 1e design writeup: the
liberty-parser choice, the bus-width extraction (bus_type -> type() group
preferred over counting enumerated pin sub-groups, to preserve true
bit_from/bit_to rather than just a width), and why is_black_box=True
Definitions are structurally distinct from 1d's transitional
UnresolvedInstance.
"""

import shutil
from pathlib import Path

import pytest

from backend.elaborator.lib import elaborate, elaborate_with_black_boxes, resolve_black_boxes
from backend.elaborator.lib.elaborate import _apply_lib_priority
from backend.elaborator.rtl import verilog
from backend.elaborator.rtl.ir import (
    BitRange,
    Definition,
    Instance,
    PortConnection,
    PortDirection,
    SourceLanguage,
    WellBiasPin,
    make_definition_id,
)

LIB_FIXTURE = Path(__file__).parent / "golden_lib" / "sram_macro.lib"
VOLTAGE_MAP_FIXTURE = Path(__file__).parent / "golden_lib" / "sram_macro_with_voltage_map.lib"
BAD_DIRECTION_FIXTURE = Path(__file__).parent / "golden_lib" / "unrecognized_direction.lib"
BAD_PG_TYPE_FIXTURE = Path(__file__).parent / "golden_lib" / "unrecognized_pg_type.lib"
QUOTED_NAMES_FIXTURE = Path(__file__).parent / "golden_lib" / "quoted_names.lib"
WELL_BIAS_FIXTURE = Path(__file__).parent / "golden_lib" / "sram_macro_with_well_bias_pins.lib"
VERILOG_BLACKBOX_DIR = Path(__file__).parent / "golden_lib" / "verilog_with_sram_blackbox"
VHDL_BLACKBOX_DIR = Path(__file__).parent / "golden_lib" / "vhdl_with_sram_blackbox"
LIB_PRIORITY_DIR = Path(__file__).parent / "golden_lib" / "verilog_sram_lib_priority"

requires_ghdl = pytest.mark.skipif(
    shutil.which("ghdl") is None or shutil.which("yosys") is None,
    reason="ghdl/yosys not on PATH -- see docs/milestone1_rtl_elaborator.md for setup (OSS CAD Suite or equivalent)",
)


def _sram_definition() -> Definition:
    definitions = elaborate([str(LIB_FIXTURE)])
    return next(d for d in definitions.values() if d.base_name == "SRAM_256x8")


# --- pure .lib parsing -------------------------------------------------


def test_scalar_pin_direction_and_shape():
    sram = _sram_definition()
    ports_by_name = {p.name: p for p in sram.ports}
    assert ports_by_name["clk"].direction == PortDirection.IN
    assert ports_by_name["clk"].bit_range is None
    assert ports_by_name["we"].direction == PortDirection.IN


def test_related_power_pin_parsed_for_scalar_and_bus_pins_none_when_absent():
    """Found via real `aura` design use (Port List's own Driver/Receiver
    Supply Set columns): a functional pin's own Liberty
    `related_power_pin` attribute names which pg_pin actually powers
    it -- not previously parsed at all. `related_ground_pin` (formerly
    deliberately not read -- Supply Set resolution used to be
    power-anchored only) was reopened after a real macro PG-pin-
    connectivity golden fixture needed to independently validate BOTH
    rails of a pin's own PG-pin pair -- see Port.related_ground_pin's
    own docstring in backend/elaborator/rtl/ir/schema.py."""
    definitions = elaborate([str(Path(__file__).parent / "golden_lib" / "sram_macro_with_related_power_pin.lib")])
    sram = next(d for d in definitions.values() if d.base_name == "SRAM_RPP")
    ports_by_name = {p.name: p for p in sram.ports}

    assert ports_by_name["CE"].related_power_pin == "VDD"
    assert ports_by_name["CE"].related_ground_pin == "VSS"
    assert ports_by_name["SPARE_IO"].related_power_pin == "VDD_IO"  # a DIFFERENT PG rail on the same cell
    assert ports_by_name["NO_RELATION"].related_power_pin is None  # never declared -- not guessed at
    assert ports_by_name["NO_RELATION"].related_ground_pin is None
    assert ports_by_name["dbus"].related_power_pin == "VDD"  # bus pins too
    # A pg_pin itself has no "related" power/ground pin of its own.
    assert ports_by_name["VDD"].related_power_pin is None
    assert ports_by_name["VDD"].related_ground_pin is None


def test_quoted_related_power_and_ground_pin_have_no_stray_embedded_quote_characters():
    """Real, confirmed bug found via a real macro PG-pin-connectivity
    golden fixture (sram.v/sram.lib, a real-elaborated design -- not
    caught by any test above, which all use UNQUOTED `related_power_pin
    : VDD;` syntax): `_build_scalar_port()`/`_build_bus_port()` read
    `related_power_pin`/`related_ground_pin` completely unwrapped -- for
    a QUOTED value (`related_power_pin : "VDD";`, confirmed common,
    valid Liberty syntax -- this exact fixture already declares it
    quoted on "clk", just never asserted on before), the field stayed a
    raw `EscapedString` object whose own `__str__`/f-string
    interpolation re-wraps it in literal quote characters (same bug
    class `test_quoted_name_value_has_no_stray_embedded_quote_characters`
    above already locks in for `Port.name`/`Definition.base_name` --
    `related_power_pin`/`related_ground_pin` were simply never given the
    same treatment when they were first added). Fixed via a new
    `_unwrap_optional_liberty_string()` helper, applied in both
    port-building functions."""
    definitions = elaborate([str(QUOTED_NAMES_FIXTURE)])
    sram = next(d for d in definitions.values() if d.base_name == "QUOTED_SRAM_256x8")
    clk = next(p for p in sram.ports if p.name == "clk")
    assert clk.related_power_pin == "VDD"
    assert clk.related_ground_pin == "VSS"
    assert type(clk.related_power_pin) is str
    assert type(clk.related_ground_pin) is str


# --- well-bias pg_pins (nwell/pwell, VPB/VNB-style) --------------------


def test_well_bias_pg_pins_parsed_as_metadata_not_ports():
    """Real macro PG-pin-connectivity golden fixture (sram.v/sram.lib
    with VPB/VNB well-bias pg_pins) reopened `nwell`/`pwell` as
    genuinely recognized pg_types -- previously deliberately unmapped
    (raised as "unrecognized"), since an IMPLICIT well-bias pin (has a
    related_bias_pin, e.g. VPB/VNB) is not a connectable port at all
    (see WellBiasPin's own docstring). VPB/VNB themselves must NOT
    appear in `ports` at all -- only VDD/VSS/VDD_NOBIAS (real
    primary_power/primary_ground pg_pins) and VPW (an EXPOSED well pin
    -- see test_exposed_well_pin_becomes_a_real_pg_well_port below) do."""
    definitions = elaborate([str(WELL_BIAS_FIXTURE)])
    sram = next(d for d in definitions.values() if d.base_name == "SRAM_WELL_BIAS")
    port_names = {p.name for p in sram.ports}

    assert "VPB" not in port_names
    assert "VNB" not in port_names
    assert port_names == {"VDD", "VSS", "CE", "VDD_NOBIAS", "VPW"}


def test_exposed_well_pin_becomes_a_real_pg_well_port():
    """VPW (pwell, NO related_bias_pin, its own voltage_name,
    `physical_connection: device_layer`) is a real, independently
    connectable macro pin -- unlike VNB (also pwell, but WITH a
    related_bias_pin, staying implicit metadata) -- gets its own
    PortDirection.PG_WELL, same "real connectable pin" treatment as
    PG/PG_INTERNAL. Must NOT also appear in well_bias_pins."""
    definitions = elaborate([str(WELL_BIAS_FIXTURE)])
    sram = next(d for d in definitions.values() if d.base_name == "SRAM_WELL_BIAS")
    vpw = next(p for p in sram.ports if p.name == "VPW")

    assert vpw.direction == PortDirection.PG_WELL
    assert not any(wb.related_bias_pin == "VPW" for wb in sram.well_bias_pins)
    assert len(sram.well_bias_pins) == 2  # unchanged -- only VPB/VNB, both implicit


def test_exposed_well_pin_generalizes_to_nwell_not_just_pwell():
    """Real macro PG-pin-connectivity golden fixture (simple7/sram.v/
    sram.lib) swaps roles: VNW (nwell, NO related_bias_pin) is EXPOSED
    this time, VPW (pwell, WITH related_bias_pin) is implicit -- the
    mirror of SRAM_WELL_BIAS above. Confirms the exposed-well dispatch
    in _build_black_box_definition() keys off `pg_type in
    _WELL_PG_TYPES` + presence/absence of related_bias_pin generically,
    never a hardcoded pin name or a pwell-only code path."""
    definitions = elaborate([str(WELL_BIAS_FIXTURE)])
    sram = next(d for d in definitions.values() if d.base_name == "SRAM_WELL_BIAS_NWELL_EXPOSED")
    vnw = next(p for p in sram.ports if p.name == "VNW")

    assert vnw.direction == PortDirection.PG_WELL
    assert not any(wb.related_bias_pin == "VNW" for wb in sram.well_bias_pins)
    assert sram.well_bias_pins == [WellBiasPin(pg_type="pwell", related_bias_pin="VSS")]  # VPW stays implicit


def test_well_bias_pg_pins_recorded_with_unwrapped_related_bias_pin():
    """`related_bias_pin` is a QUOTED Liberty string in this fixture
    (`related_bias_pin : "VDD";`) -- same quote-corruption bug class
    already fixed for related_power_pin/related_ground_pin; MUST go
    through _unwrap_optional_liberty_string(), not the raw EscapedString
    value."""
    definitions = elaborate([str(WELL_BIAS_FIXTURE)])
    sram = next(d for d in definitions.values() if d.base_name == "SRAM_WELL_BIAS")

    assert WellBiasPin(pg_type="nwell", related_bias_pin="VDD") in sram.well_bias_pins
    assert WellBiasPin(pg_type="pwell", related_bias_pin="VSS") in sram.well_bias_pins
    assert len(sram.well_bias_pins) == 2
    for wb in sram.well_bias_pins:
        assert type(wb.related_bias_pin) is str


def test_a_pg_pin_with_no_well_bias_association_has_none_recorded():
    """VDD_NOBIAS is a real, ordinary primary_power pg_pin with no
    nwell/pwell counterpart referencing it at all -- confirms
    well_bias_pins isn't guessed at or padded with speculative
    entries for every PG pin, only the ones the LIB actually
    declares."""
    definitions = elaborate([str(WELL_BIAS_FIXTURE)])
    sram = next(d for d in definitions.values() if d.base_name == "SRAM_WELL_BIAS")

    assert not any(wb.related_bias_pin == "VDD_NOBIAS" for wb in sram.well_bias_pins)


def test_bus_pin_width_resolved_via_bus_type_gives_true_declared_range():
    """Prefers bus_type -> type() group (bit_from/bit_to) over counting
    enumerated pin sub-groups, so the recovered BitRange matches the
    declared range exactly, not just a derived width."""
    sram = _sram_definition()
    addr = next(p for p in sram.ports if p.name == "addr")
    assert addr.direction == PortDirection.IN
    assert addr.bit_range == BitRange(left=7, right=0)
    assert addr.width == 8

    data = next(p for p in sram.ports if p.name == "data")
    assert data.direction == PortDirection.INOUT
    assert data.bit_range == BitRange(left=7, right=0)


def test_bus_without_bus_type_falls_back_to_counting_enumerated_pins():
    sram = _sram_definition()
    flags = next(p for p in sram.ports if p.name == "flags")
    assert flags.direction == PortDirection.OUT
    assert flags.bit_range == BitRange(left=3, right=0)


def test_internal_direction_pin_is_skipped_not_represented_as_a_port():
    sram = _sram_definition()
    assert "dbg" not in {p.name for p in sram.ports}


def test_unrecognized_direction_raises_clearly_rather_than_guessing():
    with pytest.raises(ValueError, match="tristate"):
        elaborate([str(BAD_DIRECTION_FIXTURE)])


def test_pg_pin_primary_power_and_ground_map_to_pg():
    sram = _sram_definition()
    ports_by_name = {p.name: p for p in sram.ports}
    assert ports_by_name["VDD"].direction == PortDirection.PG
    assert ports_by_name["VSS"].direction == PortDirection.PG


def test_pg_pin_internal_power_maps_to_pg_internal():
    sram = _sram_definition()
    ports_by_name = {p.name: p for p in sram.ports}
    assert ports_by_name["VDDI"].direction == PortDirection.PG_INTERNAL


def test_unrecognized_pg_type_raises_clearly_rather_than_guessing():
    """Uses "deepnwell" as the still-genuinely-unrecognized example --
    "nwell"/"pwell" themselves are no longer unrecognized (real macro
    PG-pin-connectivity golden fixture, sram.v/sram.lib with VPB/VNB
    well-bias pins): they're now parsed as WellBiasPin metadata, just
    never mapped to a Port/PortDirection the way this fixture's own
    generic "unrecognized pg_type raises" check still exercises for
    anything genuinely unhandled."""
    with pytest.raises(ValueError, match="deepnwell"):
        elaborate([str(BAD_PG_TYPE_FIXTURE)])


def test_pg_pin_voltage_is_none_when_the_library_has_no_voltage_map():
    """sram_macro.lib's pg_pins all carry a voltage_name, but the library
    itself declares no voltage_map at all -- must stay None, not guessed
    at from voltage_name alone."""
    sram = _sram_definition()
    ports_by_name = {p.name: p for p in sram.ports}
    assert ports_by_name["VDD"].voltage is None
    assert ports_by_name["VSS"].voltage is None


def test_pg_pin_voltage_resolves_through_the_librarys_own_voltage_map():
    """Found via real aura .lib use: a pg_pin's `voltage_name` is a
    reference into the library's own `voltage_map (name, value)` entries,
    not a literal number on the pg_pin group itself."""
    definitions = elaborate([str(VOLTAGE_MAP_FIXTURE)])
    sram = next(d for d in definitions.values() if d.base_name == "SRAM_256x8")
    ports_by_name = {p.name: p for p in sram.ports}
    assert ports_by_name["VDD"].voltage == "0.80V"
    assert ports_by_name["VSS"].voltage == "0.00V"


def test_pg_pin_voltage_name_with_no_matching_voltage_map_entry_stays_none():
    """VDDI's own voltage_name has no corresponding voltage_map entry in
    the fixture -- must not fabricate one."""
    definitions = elaborate([str(VOLTAGE_MAP_FIXTURE)])
    sram = next(d for d in definitions.values() if d.base_name == "SRAM_256x8")
    ports_by_name = {p.name: p for p in sram.ports}
    assert ports_by_name["VDDI"].voltage is None


# --- bug fix: quoted Liberty group names (EscapedString) --------------
# A pin()/bus()/cell()/pg_pin() group's own name/argument is valid
# Liberty syntax whether given as a bare identifier (pin(clk)) or a
# quoted string (pin("clk")) -- quoting is in fact REQUIRED for a name
# containing special characters, e.g. pin("q[0]") (an unquoted "[0]"
# would otherwise parse as bus/bit-range syntax). liberty-parser
# represents a quoted name as its own EscapedString wrapper, NOT a str
# subclass -- previously threaded straight through into Port.name/
# Definition.base_name (both declared `str`) unconverted. Dict/set
# lookups against it happened to keep working (EscapedString defines
# __eq__/__hash__ against a plain str), which is why this went
# unnoticed until a real design's macro reached a consumer requiring a
# true `str`, not just something that compares equal to one (found via
# real design use -- see the golden fixture's own docstring above it).


def test_quoted_pin_and_pg_pin_names_come_back_as_plain_str():
    definitions = elaborate([str(QUOTED_NAMES_FIXTURE)])
    sram = next(d for d in definitions.values() if d.base_name == "QUOTED_SRAM_256x8")
    for port in sram.ports:
        assert type(port.name) is str, (port.name, type(port.name))


def test_quoted_cell_name_becomes_a_plain_str_base_name():
    definitions = elaborate([str(QUOTED_NAMES_FIXTURE)])
    sram = next(d for d in definitions.values() if d.base_name == "QUOTED_SRAM_256x8")
    assert type(sram.base_name) is str


def test_quoted_name_value_has_no_stray_embedded_quote_characters():
    """A naive str(EscapedString(...)) would re-wrap the value in
    literal quote characters (see EscapedString.__str__) -- the fix
    must unwrap to the real value, not just coerce the type."""
    definitions = elaborate([str(QUOTED_NAMES_FIXTURE)])
    sram = next(d for d in definitions.values() if d.base_name == "QUOTED_SRAM_256x8")
    names = {p.name for p in sram.ports}
    assert "clk" in names
    assert '"clk"' not in names
    assert "QUOTED_SRAM_256x8" == sram.base_name


def test_quoted_bracketed_pin_name_is_preserved_exactly():
    """pin("q[0]") -- quoting is REQUIRED here, since an unquoted
    "[0]" would otherwise parse as bus/bit-range syntax rather than
    part of the identifier."""
    definitions = elaborate([str(QUOTED_NAMES_FIXTURE)])
    sram = next(d for d in definitions.values() if d.base_name == "QUOTED_SRAM_256x8")
    names = {p.name for p in sram.ports}
    assert "q[0]" in names


def test_quoted_bus_type_group_name_resolves_the_bus_width():
    """The `type (bus8)` group name is unquoted in this fixture, but
    the bus width lookup it feeds (_collect_bus_widths()'s own dict,
    keyed by the SAME _unwrap_liberty_string() helper) must still
    resolve correctly for a quoted bus() referencing it."""
    definitions = elaborate([str(QUOTED_NAMES_FIXTURE)])
    sram = next(d for d in definitions.values() if d.base_name == "QUOTED_SRAM_256x8")
    bus_a = next(p for p in sram.ports if p.name == "a")
    assert bus_a.bit_range == BitRange(left=7, right=0)


def test_black_box_definition_shape_is_permanent_and_empty_of_internals():
    sram = _sram_definition()
    assert sram.is_black_box is True
    assert sram.language == SourceLanguage.LIBERTY
    assert sram.parameters == {}
    assert sram.instances == []
    assert sram.sequential_elements == []
    assert sram.nets == []
    # id computed the same deterministic way as every other Definition, with
    # empty parameters (a .lib cell has no generics/parameterized-variant
    # concept) -- not a special-cased id scheme.
    assert sram.definition_id == make_definition_id("SRAM_256x8", {})


# --- resolve_black_boxes: matching semantics ----------------------------


def test_resolve_black_boxes_matches_by_base_name_not_definition_id():
    """The confirmation this module exists to satisfy: a naive
    definition_id comparison would only match an UnresolvedInstance when
    its own (hypothetical) parameters happen to be empty, since a
    black-box Definition's id is always computed with parameters={}.
    Prove resolution succeeds by base_name even though a hypothetical
    parameter-carrying id for the very same reference would not match.
    """
    lib_definitions = elaborate([str(LIB_FIXTURE)])
    macro_def = next(d for d in lib_definitions.values() if d.base_name == "SRAM_256x8")

    hypothetical_parameterized_id = make_definition_id("SRAM_256x8", {"DEPTH": "256"})
    assert hypothetical_parameterized_id != macro_def.definition_id  # sanity: ids really do differ

    owner = Definition(
        definition_id="owner__test",
        base_name="owner",
        language=SourceLanguage.VERILOG,
        source_file="owner.v",
    )
    unresolved = verilog.UnresolvedInstance(
        owner_definition_id=owner.definition_id,
        name="u_sram",
        referenced_definition_name="SRAM_256x8",
        connections={"clk": "clk", "we": "we"},
    )

    patched, still_unresolved = resolve_black_boxes({owner.definition_id: owner}, [unresolved], lib_definitions, "verilog")

    assert still_unresolved == []
    spliced = patched[owner.definition_id].instances[0]
    assert spliced.definition_id == macro_def.definition_id
    assert spliced.definition_id != hypothetical_parameterized_id
    assert patched[macro_def.definition_id].is_black_box is True


def test_resolve_black_boxes_leaves_non_matching_reference_in_still_unresolved():
    lib_definitions = elaborate([str(LIB_FIXTURE)])
    owner = Definition(definition_id="owner__test", base_name="owner", language=SourceLanguage.VERILOG, source_file="owner.v")
    unresolved = verilog.UnresolvedInstance(
        owner_definition_id=owner.definition_id, name="u_other", referenced_definition_name="NOT_IN_ANY_LIB", connections={}
    )

    patched, still_unresolved = resolve_black_boxes({owner.definition_id: owner}, [unresolved], lib_definitions, "verilog")

    assert still_unresolved == [unresolved]
    assert patched[owner.definition_id].instances == []  # untouched, nothing spliced


def test_resolve_black_boxes_rejects_connection_to_nonexistent_port():
    lib_definitions = elaborate([str(LIB_FIXTURE)])
    owner = Definition(definition_id="owner__test", base_name="owner", language=SourceLanguage.VERILOG, source_file="owner.v")
    unresolved = verilog.UnresolvedInstance(
        owner_definition_id=owner.definition_id,
        name="u_sram",
        referenced_definition_name="SRAM_256x8",
        connections={"not_a_real_pin": "x"},
    )

    with pytest.raises(ValueError, match="not_a_real_pin"):
        resolve_black_boxes({owner.definition_id: owner}, [unresolved], lib_definitions, "verilog")


# --- end-to-end: elaborate_with_black_boxes -----------------------------


def test_elaborate_with_black_boxes_verilog_end_to_end():
    design = elaborate_with_black_boxes(
        rtl_files=[str(VERILOG_BLACKBOX_DIR / "top.v")],
        lib_files=[str(VERILOG_BLACKBOX_DIR / "sram.lib")],
        top="top",
    )
    top = design.definition(design.top)
    assert [i.name for i in top.instances] == ["u_sram"]

    sram_inst = top.instances[0]
    sram_def = design.definition(sram_inst.definition_id)
    assert sram_def.is_black_box is True
    assert sram_def.base_name == "SRAM_256x8"

    conns = {c.instance_port: c.connected_to for c in sram_inst.connections}
    assert conns == {"clk": "clk", "we": "we", "addr": "addr", "data": "data"}

    # No trace of the transitional UnresolvedInstance mechanism survives
    # into the final Design -- only real Instance/Definition objects.
    for definition in design.definitions.values():
        assert all(isinstance(i.definition_id, str) for i in definition.instances)


def test_elaborate_with_black_boxes_raises_when_nothing_matches():
    """top.v references SRAM_256x8, but no .lib file is given to define it
    -- must fail loudly, naming the reference, not silently drop it."""
    with pytest.raises(ValueError, match="SRAM_256x8"):
        elaborate_with_black_boxes(
            rtl_files=[str(VERILOG_BLACKBOX_DIR / "top.v")],
            lib_files=[],
            top="top",
        )


@requires_ghdl
def test_elaborate_with_black_boxes_vhdl_case_insensitive_match():
    """VHDL's component name comes back from GHDL/Yosys lower-cased; the
    .lib cell is declared 'SRAM_256x8' (mixed case) -- this only resolves
    if matching folds case for the VHDL side, same as mixed_language.py's
    own cross-language matching does.
    """
    design = elaborate_with_black_boxes(
        rtl_files=[str(VHDL_BLACKBOX_DIR / "top.vhd")],
        lib_files=[str(VHDL_BLACKBOX_DIR / "sram.lib")],
        top="top",
    )
    top = design.definition(design.top)
    assert [i.name for i in top.instances] == ["u_sram"]
    sram_def = design.definition(top.instances[0].definition_id)
    assert sram_def.is_black_box is True
    assert sram_def.base_name == "SRAM_256x8"


# --- LIB priority: a module with both a real RTL body and a matching --
# --- .lib cell uses the .lib one --------------------------------------


def test_lib_priority_overrides_a_real_rtl_definition_of_the_same_name():
    """sram_rtl.v gives SRAM_256x8 a real, elaboratable RTL body (with its
    own internal net, `dummy_marker`) -- previously that meant it was
    never even offered to resolve_black_boxes(), since pyslang resolves
    it as a genuine module, never leaving it 'unresolved.' LIB priority
    is a separate mechanism specifically for this case: real RTL body
    *and* a matching .lib cell both present, .lib wins."""
    design = elaborate_with_black_boxes(
        rtl_files=[str(LIB_PRIORITY_DIR / "top.v"), str(LIB_PRIORITY_DIR / "sram_rtl.v")],
        lib_files=[str(LIB_PRIORITY_DIR / "sram.lib")],
        top="top",
    )

    top = design.definition(design.top)
    (sram_inst,) = top.instances
    sram_def = design.definition(sram_inst.definition_id)

    assert sram_def.is_black_box is True
    assert sram_def.language == SourceLanguage.LIBERTY
    assert sram_def.nets == []  # the black box's empty internals, not the real RTL body's dummy_marker net

    # The real RTL Definition for SRAM_256x8 is gone entirely -- nothing
    # in the final Design should still reference or contain it.
    assert all(not (d.base_name == "SRAM_256x8" and not d.is_black_box) for d in design.definitions.values())

    conns = {c.instance_port: c.connected_to for c in sram_inst.connections}
    assert conns == {"clk": "clk", "we": "we", "addr": "addr", "data": "data"}


def test_lib_priority_is_a_noop_with_no_lib_files():
    """Confirms the new code path never changes behavior for a caller not
    using this feature -- same guarantee test_api.py's
    test_elaborate_rtl_with_no_lib_files_matches_plain_elaborate_exactly
    already covers for the zero-black-box-instances case; this is the
    lib_files=[] case specifically."""
    design = elaborate_with_black_boxes(
        rtl_files=[str(LIB_PRIORITY_DIR / "top.v"), str(LIB_PRIORITY_DIR / "sram_rtl.v")],
        lib_files=[],
        top="top",
    )

    top = design.definition(design.top)
    sram_def = design.definition(top.instances[0].definition_id)
    assert sram_def.is_black_box is False
    assert {n.name for n in sram_def.nets} == {"dummy_marker"}


def test_lib_priority_is_a_noop_when_no_base_name_collides():
    """A .lib file that doesn't describe any module actually present in
    the RTL must not touch anything -- confirms _apply_lib_priority()
    only overrides real matches, not every real RTL Definition."""
    lib_definitions = elaborate([str(LIB_FIXTURE)])  # defines SRAM_256x8, unrelated to "owner"/"leaf" below
    owner = Definition(definition_id="owner__test", base_name="owner", language=SourceLanguage.VERILOG, source_file="owner.v")
    leaf = Definition(definition_id="leaf__test", base_name="leaf", language=SourceLanguage.VERILOG, source_file="leaf.v")
    definitions = {owner.definition_id: owner, leaf.definition_id: leaf}

    patched = _apply_lib_priority(definitions, lib_definitions, "verilog")

    assert patched == definitions


def test_lib_priority_rejects_a_connection_the_lib_cell_has_no_matching_pin_for():
    """Same guarantee as resolve_black_boxes()'s own connection check,
    applied to the LIB-priority path: an instance's real connections were
    built against the RTL module's own port names, which aren't
    guaranteed to match the .lib cell's pin names even when both describe
    'the same' macro."""
    lib_definitions = elaborate([str(LIB_FIXTURE)])  # SRAM_256x8: clk, we, addr, data (no "extra_pin")
    real_sram = Definition(
        definition_id=make_definition_id("SRAM_256x8", {}),
        base_name="SRAM_256x8",
        language=SourceLanguage.VERILOG,
        source_file="sram_rtl.v",
    )
    owner = Definition(
        definition_id="owner__test",
        base_name="owner",
        language=SourceLanguage.VERILOG,
        source_file="owner.v",
        instances=[
            Instance(
                name="u_sram",
                definition_id=real_sram.definition_id,
                connections=[PortConnection(instance_port="extra_pin", connected_to="x")],
            )
        ],
    )
    definitions = {owner.definition_id: owner, real_sram.definition_id: real_sram}

    with pytest.raises(ValueError, match="extra_pin"):
        _apply_lib_priority(definitions, lib_definitions, "verilog")
