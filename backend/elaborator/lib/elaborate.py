"""Liberty (.lib) black-box macro integration (Milestone 1e).

Scope, per docs/milestone1_rtl_elaborator.md: `.lib` files here are used
*only* to describe black-box hard macros (SRAMs, analog IP, hard IP
blocks) that have no RTL source, so the elaborator can pick up their
pin/interface info. This is explicitly not a standard-cell/synthesis
Liberty reader -- extraction only ever walks a cell's `pin`/`bus`/`type`/
`pg_pin` groups; timing arcs, power tables, and leakage tables are never
touched, so "no timing/power parsing" is a structural consequence of
what's read here, not a policy this module has to separately enforce.

CAVEAT, and it cannot be enforced by this module: Liberty syntax gives no
way to distinguish "a genuine black-box hard macro" from "an ordinary
standard-cell in a full synthesis library" -- both are just `cell` groups
with pin lists. Callers are responsible for only pointing this module at
`.lib` files that actually describe black-box macros. Pointing it at a
full standard-cell timing library will "work" in the sense of producing a
Definition per cell, but that is not what this module is for.

Built on the `liberty-parser` package (GPL-3.0-or-later -- logged, not
resolved, same treatment as GHDL's GPL-2.0-or-later dependency from
Milestone 1b; see the doc's distribution/licensing notes).

Structural distinction from Milestone 1d's UnresolvedInstance
(backend.elaborator.rtl.mixed_language): UnresolvedInstance is private,
per-front-end, and purely transitional -- it never touches
backend/elaborator/rtl/ir/schema.py and never appears in a Design handed
back to a caller. A black-box Definition (is_black_box=True) built here is
the opposite: a first-class, *permanent* member of Design.definitions,
exactly like tagging a Definition SourceLanguage.VHDL is permanent. It is
never "resolved away" -- it just has intentionally-empty internals (no
instances, no sequential_elements, no nets) forever, because a `.lib` cell
has no visible RTL body to elaborate.

v1 scope: resolves `.lib` references for a *single-language* RTL design
(all-Verilog/SV or all-VHDL). A design that needs both a cross-language
reference (1d) and a `.lib` black-box reference (1e) at the same time is a
known, deliberately deferred integration case -- same tracked-gap
treatment as 1d's alternating cross-language chain restriction -- not
silently mishandled. resolve_black_boxes() is shaped (a plain definitions
dict in/out, plus leftover UnresolvedInstances) to compose with
mixed_language.py's own merge step later without rework, but wiring that
composition together is follow-on work, not part of this pass.

resolve_black_boxes() below duplicates a small amount of splicing logic
also present in mixed_language._merge (build a PortConnection list per
resolved instance, dataclasses.replace the owner Definition). Deliberately
not factored into a shared module: the actual overlap is ~6 lines, while
the surrounding matching logic differs in a way that matters -- 1d matches
by elaborating the referenced name as its own leaf-language top and
comparing definition_ids, 1e matches by base_name against an
is_black_box-filtered pool (see resolve_black_boxes' own docstring for
why base_name and not definition_id). Forcing a shared abstraction around
that small a common core would cost more in indirection than it saves.

LIB PRIORITY, explicitly requested: when a module has *both* a real RTL
body (in `rtl_files`) and a matching black-box `.lib` cell (same
base_name, e.g. a memory-compiler macro shipped with a behavioral RTL
model for simulation alongside its .lib for synthesis/timing -- a real,
common pairing, not a hypothetical), the `.lib` version wins. This is a
different code path from resolve_black_boxes() above: that function only
ever sees instances pyslang couldn't bind to *any* real module at all
(UnresolvedInstance) -- a module with real RTL present is never
unresolved in the first place, so resolve_black_boxes() alone would never
even get a chance to override it. `_apply_lib_priority()` runs
separately, directly against the fully-elaborated real Design, rewriting
every Instance that points at a real-RTL Definition whose base_name
matches a black box to point at the black-box Definition instead, and
dropping the now-unreferenced real Definition. Runs unconditionally (a
no-op when `lib_files` is empty, or when no base_name actually collides)
so it never changes behavior for a caller that isn't using this feature.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from typing import Literal, Optional

from liberty.parser import parse_liberty
from liberty.types import EscapedString

from backend.elaborator.rtl import verilog, vhdl
from backend.elaborator.rtl.ir import (
    BitRange,
    Definition,
    Design,
    Instance,
    Port,
    PortConnection,
    PortDirection,
    SourceLanguage,
    WellBiasPin,
    make_definition_id,
)

_VERILOG_EXTENSIONS = (".v", ".sv")
_VHDL_EXTENSIONS = (".vhd", ".vhdl")

_DIRECTION_MAP = {
    "input": PortDirection.IN,
    "output": PortDirection.OUT,
    "inout": PortDirection.INOUT,
}
# Liberty's `internal` direction marks a pin that isn't part of the cell's
# external instantiation interface (e.g. an internal debug/test node) --
# skipped, not represented as a Port, for both scalar pins and buses. Any
# direction value other than input/output/inout/internal is unexpected for
# a black-box macro's interface and raises rather than being guessed at.
_SKIPPED_DIRECTIONS = {"internal"}

# `pg_pin` groups (a cell's power/ground pins, e.g. VDD/VSS) are a
# structurally separate group kind from `pin` -- no `direction` attribute
# at all, instead a `pg_type` classifying the pin. `primary_*`/`backup_*`
# are the cell's real external supply pins (what a design's power
# switches/supply nets actually connect to); `internal_*` marks a pin
# that only exists on an internal (post-switch/post-header) rail, never
# meant for external connection -- same "externally visible vs. not"
# distinction `internal` draws for ordinary `pin` direction, just spelled
# differently for pg_pin. Confirmed against real aura .lib macros
# (primary_power/primary_ground on VDD/VSS); internal_power/
# internal_ground aren't exercised by that design but are real,
# documented Liberty pg_type values. `deepnwell` (and any other
# unrecognized pg_type) is deliberately not mapped here -- "raise rather
# than guess," same treatment as an unrecognized ordinary pin direction.
#
# `nwell`/`pwell` (real macro PG-pin-connectivity golden fixture,
# sram.v/sram.lib with VPB/VNB well-bias pins) are deliberately handled
# OUTSIDE this map entirely, in _build_black_box_definition() below --
# not mapped to a PortDirection at all, since a well-bias pin is not a
# connectable port (see WellBiasPin's own docstring in
# backend/elaborator/rtl/ir/schema.py for the full "why").
_PG_TYPE_MAP = {
    "primary_power": PortDirection.PG,
    "primary_ground": PortDirection.PG,
    "backup_power": PortDirection.PG,
    "backup_ground": PortDirection.PG,
    "internal_power": PortDirection.PG_INTERNAL,
    "internal_ground": PortDirection.PG_INTERNAL,
}
_WELL_PG_TYPES = {"nwell", "pwell"}

RtlLanguage = Literal["verilog", "vhdl"]


def elaborate(files: list[str]) -> dict[str, Definition]:
    """Parse the given .lib files into black-box Definitions, keyed by definition_id.

    Each top-level `cell` group in each file becomes one permanent,
    is_black_box=True Definition with empty instances/sequential_elements/
    nets (see module docstring) and ports built from the cell's pin/bus
    groups.
    """
    definitions: dict[str, Definition] = {}
    for path in files:
        with open(path) as f:
            text = f.read()
        library = parse_liberty(text)
        bus_widths = _collect_bus_widths(library)
        voltage_map = _collect_voltage_map(library)
        for cell_group in library.get_groups("cell"):
            definition = _build_black_box_definition(cell_group, path, bus_widths, voltage_map)
            definitions[definition.definition_id] = definition
    return definitions


def resolve_black_boxes(
    definitions: dict[str, Definition],
    unresolved: list,
    lib_definitions: dict[str, Definition],
    owner_language: RtlLanguage,
) -> tuple[dict[str, Definition], list]:
    """Splice a real Instance in for each UnresolvedInstance that matches a
    black-box Definition; returns (patched_definitions, still_unresolved).
    `unresolved` is a list of UnresolvedInstance (from either
    backend.elaborator.rtl.verilog or .vhdl -- duck-typed here on
    owner_definition_id/name/referenced_definition_name/connections, since
    the two front-ends' dataclasses are structurally identical but distinct
    types). Leftovers in `still_unresolved` are the caller's problem: a
    genuinely undefined reference, or one that belongs to a different
    resolution source (e.g. 1d's cross-language lookup) entirely.

    Matches on base_name, not definition_id. make_definition_id() folds in
    `parameters`, and a black-box Definition always has parameters={} (see
    module docstring -- .lib cells have no generics/parameterized-variant
    concept). An UnresolvedInstance arising from an RTL instantiation that
    *does* carry a parameter/generic override would therefore never match
    by id even when it is genuinely the same macro; base_name is the only
    key the two sides actually share.
    """
    black_boxes = {d.base_name: d for d in lib_definitions.values() if d.is_black_box}
    if owner_language == "vhdl":
        black_boxes = {name.lower(): d for name, d in black_boxes.items()}

    def lookup_key(referenced_name: str) -> str:
        return referenced_name.lower() if owner_language == "vhdl" else referenced_name

    still_unresolved = []
    by_owner: dict[str, list[tuple]] = defaultdict(list)
    for u in unresolved:
        matched = black_boxes.get(lookup_key(u.referenced_definition_name))
        if matched is None:
            still_unresolved.append(u)
            continue
        _validate_connections(u, matched, owner_language)
        by_owner[u.owner_definition_id].append((u, matched))

    patched = dict(definitions)
    used_lib_definitions: dict[str, Definition] = {}
    for owner_id, items in by_owner.items():
        owner_def = patched[owner_id]
        new_instances = list(owner_def.instances)
        for u, matched in items:
            new_instances.append(
                Instance(
                    name=u.name,
                    definition_id=matched.definition_id,
                    connections=[
                        PortConnection(instance_port=port, connected_to=signal)
                        for port, signal in u.connections.items()
                    ],
                )
            )
            used_lib_definitions[matched.definition_id] = matched
        patched[owner_id] = replace(owner_def, instances=new_instances)

    patched.update(used_lib_definitions)
    return patched, still_unresolved


def _apply_lib_priority(
    definitions: dict[str, Definition],
    lib_definitions: dict[str, Definition],
    owner_language: RtlLanguage,
) -> dict[str, Definition]:
    """When a real RTL Definition and a black-box .lib Definition share a
    base_name, the .lib version wins -- see the module docstring's "LIB
    PRIORITY" section for why. Every Instance across `definitions` whose
    definition_id points at an overridden real Definition gets rewritten
    to point at the black-box Definition instead; the real Definition
    itself is dropped (nothing should still reference its id afterward).

    Connections are validated the same way resolve_black_boxes() validates
    them for a genuinely-unresolved instance (same error shape, same
    VHDL case-insensitivity) -- the instance's connections were built
    against the *real* RTL module's port names, which aren't guaranteed
    to be identical to the .lib cell's pin names, even though in practice
    they typically are for a real macro/.lib pairing describing the same
    cell.
    """
    black_boxes = {d.base_name: d for d in lib_definitions.values() if d.is_black_box}
    if owner_language == "vhdl":
        black_boxes = {name.lower(): d for name, d in black_boxes.items()}

    def lookup_key(name: str) -> str:
        return name.lower() if owner_language == "vhdl" else name

    override_map: dict[str, Definition] = {}  # real definition_id -> black-box Definition
    for def_id, definition in definitions.items():
        if definition.is_black_box:
            continue
        matched = black_boxes.get(lookup_key(definition.base_name))
        if matched is not None:
            override_map[def_id] = matched

    if not override_map:
        return definitions

    patched: dict[str, Definition] = {}
    used_lib_definitions: dict[str, Definition] = {}
    for def_id, definition in definitions.items():
        if def_id in override_map:
            matched = override_map[def_id]
            used_lib_definitions[matched.definition_id] = matched
            continue  # drop the real RTL Definition -- superseded, and about to be unreferenced
        new_instances = []
        changed = False
        for inst in definition.instances:
            matched = override_map.get(inst.definition_id)
            if matched is None:
                new_instances.append(inst)
                continue
            _validate_lib_priority_connections(inst, matched, owner_language)
            new_instances.append(replace(inst, definition_id=matched.definition_id))
            changed = True
        patched[def_id] = replace(definition, instances=new_instances) if changed else definition

    patched.update(used_lib_definitions)
    return patched


def _validate_lib_priority_connections(instance: Instance, resolved: Definition, owner_language: RtlLanguage) -> None:
    """Same guarantee as resolve_black_boxes()'s _validate_connections,
    adapted for a real (already-connected) Instance rather than an
    UnresolvedInstance's raw connection dict."""
    resolved_port_names = {p.name for p in resolved.ports}
    used_names = {c.instance_port for c in instance.connections}
    if owner_language == "vhdl":
        resolved_port_names = {n.lower() for n in resolved_port_names}
        used_names = {n.lower() for n in used_names}
    unknown = used_names - resolved_port_names
    if unknown:
        raise ValueError(
            f"instance '{instance.name}' connects port(s) {sorted(unknown)} that don't exist on black-box "
            f"macro '{resolved.base_name}' (real ports: {sorted(resolved_port_names)}) -- .lib takes priority "
            f"over the real RTL definition of the same name, but its pin names don't match the RTL's port names"
        )


def elaborate_with_black_boxes(
    rtl_files: list[str],
    lib_files: list[str],
    top: str | None = None,
    defines: dict[str, str | None] | None = None,
    include_dirs: list[str] | None = None,
) -> Design:
    """Elaborate a single-language RTL design (all-Verilog/SV or all-VHDL,
    detected by extension) and resolve every unresolved reference against
    the black-box macros in `lib_files`. Raises clearly if anything is
    left unresolved (not found among the given .lib files either). Also
    applies LIB priority (see module docstring): a module with both a
    real RTL body and a matching .lib cell uses the .lib one.

    Does not compose with mixed_language.py's cross-language resolution in
    this pass -- see the module docstring's v1 scope note.
    """
    language = _detect_rtl_language(rtl_files)
    lib_definitions = elaborate(lib_files)

    if language == "verilog":
        # Defines/incdirs are Verilog/SV-only: they are preprocessor
        # concepts, and VHDL has no preprocessor. Passing them to the
        # VHDL path would be inventing a feature that format does not
        # have -- the caller is told instead (see backend/api).
        owner_design, unresolved = verilog.elaborate_allowing_unresolved(
            rtl_files, top=top, defines=defines, include_dirs=include_dirs
        )
    else:
        if top is None:
            raise ValueError("top is required for VHDL (no auto-detection -- see backend.elaborator.rtl.vhdl.elaborate)")
        owner_design, unresolved = vhdl.elaborate_allowing_unresolved(rtl_files, top=top)

    definitions = _apply_lib_priority(owner_design.definitions, lib_definitions, language)

    if not unresolved:
        return Design(definitions=definitions, top=owner_design.top)

    patched_definitions, still_unresolved = resolve_black_boxes(definitions, unresolved, lib_definitions, language)
    if still_unresolved:
        names = sorted({u.referenced_definition_name for u in still_unresolved})
        raise ValueError(
            f"{len(still_unresolved)} unresolved reference(s) to {names}: not found among the black-box macros "
            f"in the given .lib file(s), and not a genuine {language} definition in the given RTL files either"
        )
    return Design(definitions=patched_definitions, top=owner_design.top)


def _detect_rtl_language(rtl_files: list[str]) -> RtlLanguage:
    is_verilog = [f for f in rtl_files if f.lower().endswith(_VERILOG_EXTENSIONS)]
    is_vhdl = [f for f in rtl_files if f.lower().endswith(_VHDL_EXTENSIONS)]
    if is_verilog and is_vhdl:
        raise ValueError(
            "elaborate_with_black_boxes handles a single-language RTL design only (all-Verilog/SV or all-VHDL) -- "
            "for a mixed-language design, resolve cross-language references via "
            "backend.elaborator.rtl.mixed_language.elaborate_mixed first (composing that with .lib resolution "
            "is deliberately deferred -- see this module's docstring)"
        )
    if is_verilog:
        return "verilog"
    if is_vhdl:
        return "vhdl"
    raise ValueError(f"no recognized RTL source files (expected .v/.sv/.vhd/.vhdl): {rtl_files}")


def _unwrap_liberty_string(value) -> str:
    """A Liberty group's own name/argument (`Group.args[0]`) -- e.g. the
    identifier given to `pin()`/`bus()`/`cell()`/`pg_pin()` -- comes back
    from liberty-parser as a plain `str` for a bare identifier
    (`pin(SLEEP_B)`) but as its own `EscapedString` wrapper for a QUOTED
    one (`pin("SLEEP_B")`). Both are genuinely valid Liberty syntax --
    quoting is in fact REQUIRED for a name containing special characters
    (e.g. `pin("Q[0]")`, confirmed against real Liberty usage: an
    unquoted `[0]` would otherwise parse as bus/bit-range syntax, not
    part of the identifier) -- but `EscapedString` is NOT a `str`
    subclass (see `liberty.types.EscapedString`: a wrapper with its own
    `__eq__`/`__hash__` -- which is why dict/set lookups against it
    already worked fine everywhere else in this module without this
    fix -- but its own `__str__` re-wraps the value in literal quote
    characters rather than returning it plain, so even `str(x)` alone
    would be wrong here, not just non-crashing). Every IR field this
    feeds (`Port.name`, `Definition.base_name`) is declared `str` --
    unwrapped once, here, at the single point every one of them is read
    from a Group's own `.args[0]`, rather than every downstream
    consumer (including ones as strict as PySide6's own C++ bindings,
    which is where this was actually caught -- see the bug report) having
    to guard against a non-str type."""
    return value.value if isinstance(value, EscapedString) else str(value)


def _unwrap_optional_liberty_string(value) -> Optional[str]:
    """Same unwrapping as _unwrap_liberty_string() above, but for an
    OPTIONAL simple attribute read via a Group's own `["attr_name"]`
    accessor (e.g. `related_power_pin`/`related_ground_pin`) rather than
    a required `.args[0]` group name -- `None` when the attribute was
    never declared at all (a real, valid state, not an error) must stay
    `None`, not become the literal string "None". Real, confirmed bug
    found via a real macro PG-pin-connectivity golden fixture
    (sram.v/sram.lib): `_build_scalar_port()`/`_build_bus_port()`
    previously read `related_power_pin`/`related_ground_pin` completely
    unwrapped -- for a QUOTED Liberty value (`related_power_pin :
    "VDD_INT";`, confirmed common/expected Liberty syntax, not unusual),
    the field stayed an `EscapedString` object instead of a plain `str`
    for its entire downstream lifetime. Every dict/set lookup against it
    happened to keep working (EscapedString's own __eq__/__hash__
    delegate to the underlying value -- see _unwrap_liberty_string()'s
    own docstring), which is exactly why this stayed invisible through
    every prior macro-pin test: none of them f-string-interpolated a
    REAL (LIB-elaborated, not hand-built-mock) related_power_pin value
    into a pg-pin id string until this fixture's own fully-connected
    case did (`f"pg:{path}:{related_power_pin}"` in
    power_resolution_cache.py) -- silently producing
    `pg:sram/u_sram:"VDD_INT"` (literal embedded quote characters) and
    permanently failing to match the clean, unquoted id every
    SupplyPortPlacement is actually keyed by."""
    return _unwrap_liberty_string(value) if value is not None else None


def _collect_bus_widths(library) -> dict[str, BitRange]:
    widths = {}
    for type_group in library.get_groups("type"):
        bit_from = type_group["bit_from"]
        bit_to = type_group["bit_to"]
        if bit_from is None or bit_to is None:
            continue
        widths[_unwrap_liberty_string(type_group.args[0])] = BitRange(left=bit_from, right=bit_to)
    return widths


def _build_black_box_definition(
    cell_group, source_file: str, bus_widths: dict[str, BitRange], voltage_map: dict[str, float]
) -> Definition:
    base_name = _unwrap_liberty_string(cell_group.args[0])
    ports: list[Port] = []
    for pin_group in cell_group.get_groups("pin"):
        port = _build_scalar_port(pin_group)
        if port is not None:
            ports.append(port)
    for bus_group in cell_group.get_groups("bus"):
        port = _build_bus_port(bus_group, bus_widths)
        if port is not None:
            ports.append(port)
    well_bias_pins: list[WellBiasPin] = []
    for pg_pin_group in cell_group.get_groups("pg_pin"):
        if pg_pin_group["pg_type"] in _WELL_PG_TYPES:
            well_bias_pin = _build_well_bias_pin(pg_pin_group)
            if well_bias_pin is not None:
                well_bias_pins.append(well_bias_pin)
            else:
                # No related_bias_pin declared -- an EXPOSED well pin
                # (e.g. VPW), not an implicit one: a real, independently
                # connectable macro pin, same treatment as any other
                # pg_pin (see PortDirection.PG_WELL's own docstring).
                ports.append(_build_exposed_well_port(pg_pin_group, voltage_map))
        else:
            ports.append(_build_pg_pin_port(pg_pin_group, voltage_map))

    return Definition(
        definition_id=make_definition_id(base_name, {}),
        base_name=base_name,
        language=SourceLanguage.LIBERTY,
        source_file=source_file,
        is_black_box=True,
        ports=ports,
        well_bias_pins=well_bias_pins,
    )


def _build_scalar_port(pin_group) -> Port | None:
    name = _unwrap_liberty_string(pin_group.args[0])
    direction = _resolve_direction(pin_group["direction"], name)
    if direction is None:
        return None
    return Port(
        name=name,
        direction=direction,
        related_power_pin=_unwrap_optional_liberty_string(pin_group["related_power_pin"]),
        related_ground_pin=_unwrap_optional_liberty_string(pin_group["related_ground_pin"]),
    )


def _build_bus_port(bus_group, bus_widths: dict[str, BitRange]) -> Port | None:
    name = _unwrap_liberty_string(bus_group.args[0])
    direction = _resolve_direction(bus_group["direction"], name)
    if direction is None:
        return None

    bus_type = bus_group["bus_type"]
    bit_range = bus_widths.get(bus_type) if bus_type is not None else None
    if bit_range is None:
        # No bus_type -> type() group to resolve against (or it didn't
        # carry bit_from/bit_to) -- fall back to counting enumerated pin
        # sub-groups. This only recovers width, not true declared
        # direction/ordering, same fallback style already used for VHDL
        # width-only cases elsewhere in Milestone 1.
        sub_pins = bus_group.get_groups("pin")
        if not sub_pins:
            raise ValueError(f"bus '{name}' has neither a resolvable bus_type nor enumerated pin sub-groups -- cannot determine its width")
        bit_range = BitRange(left=len(sub_pins) - 1, right=0)

    return Port(
        name=name,
        direction=direction,
        bit_range=bit_range,
        related_power_pin=_unwrap_optional_liberty_string(bus_group["related_power_pin"]),
        related_ground_pin=_unwrap_optional_liberty_string(bus_group["related_ground_pin"]),
    )


def _collect_voltage_map(library) -> dict[str, float]:
    """Library-level `voltage_map (RAIL_NAME, value);` entries (e.g.
    `voltage_map (VDD, 0.8);`) -- a repeatable *complex attribute*, not a
    `group` (no braces), so unlike `_collect_bus_widths()`'s `type()`
    groups this reads `library.attributes` directly rather than
    `get_groups()`. Confirmed against real aura .lib macros: each entry's
    `.value` is a `[name, value]` pair."""
    voltages: dict[str, float] = {}
    for attr in library.attributes:
        if attr.name == "voltage_map":
            rail_name, value = attr.value
            voltages[rail_name] = value
    return voltages


def _build_pg_pin_port(pg_pin_group, voltage_map: dict[str, float]) -> Port:
    name = _unwrap_liberty_string(pg_pin_group.args[0])
    pg_type = pg_pin_group["pg_type"]
    direction = _PG_TYPE_MAP.get(pg_type)
    if direction is None:
        raise ValueError(
            f"pg_pin '{name}' has unrecognized Liberty pg_type {pg_type!r} (expected one of {sorted(_PG_TYPE_MAP)})"
        )
    # A pg_pin's own `voltage_name` is a reference into the library's
    # separate voltage_map, not a literal number sitting on the pg_pin
    # group itself (confirmed against real aura .lib macros -- see
    # _collect_voltage_map()). None (not guessed at) when voltage_name is
    # absent, or present but has no matching voltage_map entry.
    voltage_name = pg_pin_group["voltage_name"]
    voltage = f"{voltage_map[voltage_name]:.2f}V" if voltage_name in voltage_map else None
    return Port(name=name, direction=direction, voltage=voltage)


def _build_well_bias_pin(pg_pin_group) -> WellBiasPin | None:
    """A `pg_pin` group whose own `pg_type` is "nwell"/"pwell" (e.g.
    VPB/VNB) -- see WellBiasPin's own docstring in
    backend/elaborator/rtl/ir/schema.py for why this is metadata, not a
    Port. `related_bias_pin` is a QUOTED Liberty string in real usage
    (`related_bias_pin : "VDD";`) -- MUST go through
    _unwrap_optional_liberty_string(), same quote-corruption bug class
    already fixed for related_power_pin/related_ground_pin. None (no
    WellBiasPin recorded at all, not guessed at) when related_bias_pin
    was never declared -- nothing to validate against."""
    pg_type = pg_pin_group["pg_type"]
    related_bias_pin = _unwrap_optional_liberty_string(pg_pin_group["related_bias_pin"])
    if related_bias_pin is None:
        return None
    return WellBiasPin(pg_type=pg_type, related_bias_pin=related_bias_pin)


def _build_exposed_well_port(pg_pin_group, voltage_map: dict[str, float]) -> Port:
    """An EXPOSED well-type pg_pin (nwell/pwell with NO related_bias_pin
    -- e.g. VPW, `physical_connection: device_layer`, its own
    voltage_name) -- a real, independently connectable macro pin (see
    PortDirection.PG_WELL's own docstring), unlike an implicit one
    (_build_well_bias_pin() above). Mirrors _build_pg_pin_port() for the
    voltage lookup, but never consults _PG_TYPE_MAP -- the caller
    already confirmed `pg_type` is "nwell"/"pwell" before routing here,
    and PG_WELL is a single PortDirection regardless of which of the
    two it is (nothing downstream needs that distinction the way
    _macro_well_bias_conflict() does for an IMPLICIT well's own
    n_well/p_well field selection)."""
    name = _unwrap_liberty_string(pg_pin_group.args[0])
    voltage_name = pg_pin_group["voltage_name"]
    voltage = f"{voltage_map[voltage_name]:.2f}V" if voltage_name in voltage_map else None
    return Port(name=name, direction=PortDirection.PG_WELL, voltage=voltage)


def _resolve_direction(raw, pin_or_bus_name: str) -> PortDirection | None:
    if raw in _SKIPPED_DIRECTIONS:
        return None
    if raw not in _DIRECTION_MAP:
        raise ValueError(f"'{pin_or_bus_name}' has unrecognized Liberty direction {raw!r} (expected input/output/inout/internal)")
    return _DIRECTION_MAP[raw]


def _validate_connections(unresolved, resolved: Definition, owner_language: RtlLanguage) -> None:
    """Cheap name-existence check, same guarantee as mixed_language.py's
    own _validate_connections (see module docstring for why this isn't
    shared code with it)."""
    resolved_port_names = {p.name for p in resolved.ports}
    used_names = set(unresolved.connections)
    if owner_language == "vhdl":
        resolved_port_names = {n.lower() for n in resolved_port_names}
        used_names = {n.lower() for n in used_names}
    unknown = used_names - resolved_port_names
    if unknown:
        raise ValueError(
            f"instance '{unresolved.name}' connects port(s) {sorted(unknown)} that don't exist on black-box "
            f"macro '{resolved.base_name}' (real ports: {sorted(resolved_port_names)})"
        )
