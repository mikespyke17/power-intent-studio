"""VHDL front-end integration (Milestone 1b).

Unlike 1a (an in-process Python AST via pyslang), this shells out to GHDL
(analysis) and Yosys+ghdl-yosys-plugin (elaboration -- generic resolution,
hierarchy, flop inference), then normalizes the resulting JSON netlist into
the unified IR. See docs/milestone1_rtl_elaborator.md for why: pyGHDL's own
Python bindings were evaluated first and rejected for whole-design
elaboration (generic resolution there is essentially unexposed, hierarchy
walking means hand-rolling entity/architecture binding resolution). The
GHDL+Yosys synthesis path does real elaboration itself and hands back a
public, stable JSON schema.

`ghdl`/`yosys` are located via `PATH`, overridable with the `GHDL`/`YOSYS`
env vars; `GHDL_PREFIX` (its VHDL library search path) is passed through
from the environment if set. End-user distribution/packaging for these two
native tools is an explicitly deferred, separate decision -- see the doc.

Two real, non-obvious pieces of complexity found while building this
against actual GHDL/Yosys output (not assumed):

1. Yosys's JSON carries no structured attributes recovering the original
   entity name or generic bindings for a parameterized module -- only a
   name-mangling convention (e.g. `counter_Brtl_8` for entity `counter`,
   architecture `rtl`, single generic resolved to `8`). Recovering a real
   `{generic_name: value}` dict means knowing the entity's declared generic
   *names* in order, which nothing in the Yosys/GHDL output exposes either.
   This module resolves that with a narrow, deliberately non-general text
   scan of the *already GHDL-validated* source (`ghdl -a` must have
   succeeded before this runs) for `entity ... is` / `generic (...)` /
   `architecture ... of ... is` -- lexical identifier extraction against
   known syntactic anchors, not a VHDL parser: no expressions, no types, no
   semantics. (pyGHDL could do this info more "properly", but was ruled
   out here specifically to avoid adding a second native, version-locked,
   awkward-to-install dependency on top of GHDL+Yosys -- see the doc.)
2. A flop's ARST pin is not always wired directly to the named reset
   signal: GHDL's synthesis frontend can insert a `$not` cell between an
   active-low source condition (`if rst_n = '0'`) and the `$adff`
   primitive's own (active-high-normalized) ARST pin. Reading
   `ARST_POLARITY` at face value without accounting for this gives the
   wrong answer. This module traces back through at most one `$not` cell;
   anything more complex is left unrecognized rather than guessed at.

Generics text-scan (`_scan_sources`/`_reconstruct_base_name_and_parameters`):
explicit scope, same "recognize known shapes, don't guess on the rest"
principle as the sequential-element extraction above.

Handles:
  - Any number of `entity ... is ... end [entity] [name];` blocks and
    `architecture ... of ... is` headers, across multiple files.
  - `generic (...)` clauses with comma-separated identifier groups across
    `;`-separated interface elements (e.g. `A, B : integer := 0; C : ...`),
    with paren-balance tracking so a generic's own type/default expression
    containing parens (`W : integer_vector(0 to 3) := (8,8,8,8)`) doesn't
    break clause-boundary detection.
  - `--` line comments and VHDL-2008 `/* */` block comments are stripped
    before scanning (respecting double-quoted string literals), so
    `-- ... "entity old_foo is" ...` isn't mistaken for a real declaration
    -- found as a real bug (not hypothetical) while writing this scope note.
  - Three distinct, verified Yosys module-naming shapes: the top entity is
    always bare regardless of generics; a nested (instantiated) entity with
    zero generics gets an unconditional `<entity>_b<arch>` suffix; a nested
    entity with any declared generic *always* gets resolved values appended
    (`<entity>_b<arch>_<values>`), even when every value is that generic's
    own default with no explicit generic map override at all. An earlier
    version of this logic conflated "unmangled" with "at default" and broke
    on exactly that last case -- see `vhdl_generic_at_default/` fixture.

Does not handle (fails loudly via ValueError, not silently):
  - Generic value *types* other than plain integers. Only ever verified
    against integer-valued generics (matching the positional
    underscore-joined suffix Yosys/GHDL emit for those); string, boolean,
    enumerated, real, physical/time, array, or record generic values are
    untested, and the split-by-`_` reconstruction could misparse a value
    that itself contains an underscore.
  - Multiple architectures for the *same* entity where more than one
    candidate validates against a mangled name with the same generic count
    -- the first one found wins, which is not guaranteed to be the one GHDL
    actually elaborated.
  - Generic clauses declared anywhere other than directly in an
    `entity ... is ... end;` block (e.g. inside a `package` as a deferred
    generic, VHDL-2008 generic packages) -- not scanned for at all.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from backend.elaborator.rtl.ir import (
    BitRange,
    ContinuousAssign,
    Definition,
    Design,
    Edge,
    Instance,
    Net,
    Port,
    PortConnection,
    PortDirection,
    ResetKind,
    SequentialElement,
    SourceLanguage,
    make_definition_id,
)

_DIRECTION_MAP = {
    "input": PortDirection.IN,
    "output": PortDirection.OUT,
    "inout": PortDirection.INOUT,
}

_SEQ_CELL_TYPES = {"$dff", "$adff", "$dlatch"}

_ENTITY_RE = re.compile(r"\bentity\s+(\w+)\s+is\b", re.IGNORECASE)
_ARCHITECTURE_RE = re.compile(r"\barchitecture\s+(\w+)\s+of\s+(\w+)\s+is\b", re.IGNORECASE)
_GENERIC_CLAUSE_RE = re.compile(r"\bgeneric\s*\(", re.IGNORECASE)
_ENTITY_END_RE = re.compile(r"\bend\b(\s+entity)?(\s+\w+)?\s*;", re.IGNORECASE)


@dataclass(frozen=True)
class UnresolvedInstance:
    """An instantiation GHDL/Yosys could not bind to a real entity -- most
    commonly a `component` reference to an entity actually defined in a
    different HDL, but could equally be a genuinely-undefined component
    (a bug, or a future `.lib` black-box reference -- 1e doesn't exist yet,
    so this module makes no attempt to guess which).

    Used only by backend.elaborator.rtl.mixed_language (1d); not part of
    elaborate()'s normal, strict-mode contract, and never surfaced through
    the public IR. `owner_definition_id` is the Definition this instance
    would belong to once resolved -- the caller is expected to splice a
    real Instance back into that Definition's `instances` list after
    resolving `referenced_definition_name` some other way.
    """

    owner_definition_id: str
    name: str
    referenced_definition_name: str
    connections: dict[str, str | None] = field(default_factory=dict)


def elaborate(files: list[str], top: str) -> Design:
    """Elaborate the given VHDL files, with `top` as the top-level entity name.

    Unlike backend.elaborator.rtl.verilog.elaborate, `top` must be given
    explicitly: nothing in the GHDL+Yosys pipeline auto-detects it the way
    pyslang's `topInstances` does, and VHDL analysis libraries have no
    inherent "top" concept until elaboration is requested for one.
    """
    design, unresolved = _elaborate(files, top)
    if unresolved:
        # Unlike Verilog, GHDL+Yosys does not fail (exit 0, just a
        # "not bound" warning) on an unresolved component -- it silently
        # synthesizes a blackbox stub instead. Strict elaborate() must not
        # let that pass through quietly, so it's turned into a clear error
        # here rather than a confusing failure elsewhere.
        names = sorted({u.referenced_definition_name for u in unresolved})
        raise ValueError(
            f"found {len(unresolved)} unbound/blackbox component reference(s) not resolvable within a "
            f"single-language elaboration: {names}. If this is a legitimate cross-language reference, use "
            "the mixed-language orchestrator (backend.elaborator.rtl.mixed_language), not elaborate() directly."
        )
    return design


def elaborate_allowing_unresolved(files: list[str], top: str) -> tuple[Design, list[UnresolvedInstance]]:
    """Like elaborate(), but tolerates unbound components instead of raising
    on them -- used by the mixed-language orchestrator (1d) to discover
    cross-HDL references.

    Instances referencing an unresolved component are omitted from their
    owning Definition's `instances` list in the returned Design (there is
    nothing valid to point `definition_id` at yet) and are reported in the
    second return value instead, for the caller to resolve and splice back in.
    """
    return _elaborate(files, top)


def _elaborate(files: list[str], top: str) -> tuple[Design, list[UnresolvedInstance]]:
    ghdl = os.environ.get("GHDL", "ghdl")
    yosys = os.environ.get("YOSYS", "yosys")
    ghdl_prefix = os.environ.get("GHDL_PREFIX")

    # `ghdl -a` writes analysis artifacts (work-obj08.cf, *.o) into whatever
    # directory it's run from -- run it in a scratch dir, not the caller's
    # cwd, so elaborate() has no working-directory side effects. File paths
    # must be absolute first, since they're resolved relative to that
    # scratch dir once cwd changes.
    absolute_files = [str(Path(f).resolve()) for f in files]
    with tempfile.TemporaryDirectory(prefix="vhdl_elaborate_") as workdir:
        _run_ghdl_analyze(ghdl, ghdl_prefix, absolute_files, workdir)
        netlist = _run_yosys_json(yosys, ghdl_prefix, top, workdir)

    entity_generics, entity_source_file, architectures = _scan_sources(files)
    return _build_design(netlist, entity_generics, entity_source_file, architectures, top)


def list_entity_names(files: list[str]) -> set[str]:
    """Cheap (no ghdl invocation) entity-name discovery for mixed-language
    top-detection -- reuses the same text scan as generics recovery, just
    returning the name set. Names are lowercase, matching VHDL's
    case-insensitivity and the rest of this module's convention.
    """
    entity_generics, _, _ = _scan_sources(files)
    return set(entity_generics)


def _run_ghdl_analyze(ghdl: str, ghdl_prefix: str | None, files: list[str], workdir: str) -> None:
    cmd = [ghdl, "-a", "--std=08"]
    if ghdl_prefix:
        cmd.append(f"-P{ghdl_prefix}")
    cmd.extend(files)
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=workdir)
    if result.returncode != 0:
        raise ValueError(f"ghdl -a failed:\n{result.stderr}")


def _run_yosys_json(yosys: str, ghdl_prefix: str | None, top: str, workdir: str) -> dict:
    ghdl_cmd = "ghdl --std=08"
    if ghdl_prefix:
        ghdl_cmd += f" -P{ghdl_prefix}"
    # --latches: without this, GHDL's synthesis frontend treats latch
    # inference as a hard error ("latch infered for net ... (use --latches)")
    # and aborts elaboration entirely -- confirmed empirically. That means
    # *any* VHDL design containing a latch, intentional or an accidental bug,
    # currently crashed the whole elaborate() call rather than just missing
    # the latch. Always passing this flag is what makes plain $dlatch
    # extraction (below) reachable at all.
    ghdl_cmd += " --latches"
    ghdl_cmd += f" {top}"
    cmd = [yosys, "-m", "ghdl", "-q", "-p", f"{ghdl_cmd}; write_json"]
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=workdir)
    if result.returncode != 0:
        raise ValueError(f"yosys failed:\n{result.stderr}")
    return json.loads(result.stdout)


# --- source-text scanning (generic names + entity/architecture linkage only) ---


def _scan_sources(files: list[str]) -> tuple[dict[str, list[str]], dict[str, str], list[tuple[str, str]]]:
    entity_generics: dict[str, list[str]] = {}
    entity_source_file: dict[str, str] = {}
    architectures: list[tuple[str, str]] = []

    for path in files:
        with open(path, encoding="utf-8") as f:
            text = _strip_comments(f.read())
        for m in _ENTITY_RE.finditer(text):
            name = m.group(1)
            body_end_match = _ENTITY_END_RE.search(text, m.end())
            body = text[m.end() : body_end_match.start()] if body_end_match else text[m.end() :]
            entity_generics[name.lower()] = _extract_generic_names(body)
            entity_source_file[name.lower()] = path
        for m in _ARCHITECTURE_RE.finditer(text):
            architectures.append((m.group(1).lower(), m.group(2).lower()))

    return entity_generics, entity_source_file, architectures


def _strip_comments(text: str) -> str:
    """Blank out VHDL comments (`-- ...` to end of line, and VHDL-2008
    `/* ... */` block comments) with spaces, preserving text length so
    downstream regex match offsets stay valid. Without this, a comment like
    `-- this used to be "entity old_foo is"` would be matched as a real
    entity declaration -- confirmed as a real bug, not a theoretical one,
    while testing this scanner. Respects double-quoted string literals (a
    `--`/`/*` inside a string is not a comment); character literals (`'x'`)
    aren't specially handled since a lone `-` or `/` inside one can't form
    a real two-character comment marker.
    """
    result = []
    i = 0
    n = len(text)
    in_string = False
    while i < n:
        ch = text[i]
        if in_string:
            result.append(ch)
            if ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            result.append(ch)
            i += 1
            continue
        if text[i : i + 2] == "--":
            while i < n and text[i] != "\n":
                result.append(" ")
                i += 1
            continue
        if text[i : i + 2] == "/*":
            result.append("  ")
            i += 2
            while i < n and text[i : i + 2] != "*/":
                result.append("\n" if text[i] == "\n" else " ")
                i += 1
            if i < n:
                result.append("  ")
                i += 2
            continue
        result.append(ch)
        i += 1
    return "".join(result)


def _extract_generic_names(entity_body: str) -> list[str]:
    gm = _GENERIC_CLAUSE_RE.search(entity_body)
    if gm is None:
        return []
    clause = _extract_balanced_parens(entity_body, gm.end() - 1)
    names: list[str] = []
    for group in _split_top_level(clause[1:-1], ";"):
        before_colon = group.split(":", 1)[0]
        names.extend(n.strip().lower() for n in before_colon.split(",") if n.strip())
    return names


def _extract_balanced_parens(text: str, open_paren_pos: int) -> str:
    depth = 0
    for i in range(open_paren_pos, len(text)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return text[open_paren_pos : i + 1]
    raise ValueError("unbalanced parentheses in generic clause")


def _split_top_level(text: str, sep: str) -> list[str]:
    depth = 0
    parts: list[str] = []
    current: list[str] = []
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == sep and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    parts.append("".join(current))
    return parts


# --- Yosys mangled-module-name reconstruction ---


def _reconstruct_base_name_and_parameters(
    module_name: str,
    entity_generics: dict[str, list[str]],
    architectures: list[tuple[str, str]],
) -> tuple[str, dict[str, str]]:
    # Verified empirically (not assumed) against three distinct shapes:
    #   1. The top-level entity (read directly via `ghdl <top>`, never
    #      instantiated) is always left completely bare -- "top" stays
    #      "top" even if it happens to declare generics.
    #   2. A *nested* (instantiated) entity with zero declared generics
    #      gets an unconditional "<entity>_b<architecture>" suffix, even
    #      though it has nothing to parametrize -- e.g. "sub" becomes
    #      "sub_brtl". This disambiguates architecture binding, not generics.
    #   3. A nested entity with one or more declared generics *always* gets
    #      the generic values appended too -- "<entity>_b<arch>_<values>" --
    #      even when every value used is that generic's own default (an
    #      instantiation with no generic map override at all still produces
    #      e.g. "dgsub_brtl_8", not bare "dgsub_brtl"). So unlike shape 2,
    #      a nested generic-bearing entity is never seen unmangled.
    lower = module_name.lower()

    if lower in entity_generics:
        return module_name, {}  # shape 1: the top entity, always bare

    for arch_name, entity_name in architectures:
        bare = f"{entity_name}_b{arch_name}"
        if lower == bare and not entity_generics.get(entity_name):
            return entity_name, {}  # shape 2: nested, zero generics

        prefix = f"{bare}_"
        if not lower.startswith(prefix):
            continue
        generics = entity_generics.get(entity_name, [])
        if not generics:
            continue
        values = lower[len(prefix) :].split("_")
        if len(values) == len(generics):
            return entity_name, dict(zip(generics, values))  # shape 3

    raise ValueError(
        f"could not map Yosys module '{module_name}' back to a known VHDL entity/generics "
        f"(known entities: {sorted(entity_generics)}, known architectures: {architectures})"
    )


# --- JSON netlist -> IR ---


def _is_blackbox(mod: dict) -> bool:
    """A module Yosys itself couldn't bind -- e.g. a VHDL `component`
    instantiated with no matching entity anywhere in the analyzed sources
    (confirmed empirically: GHDL/Yosys auto-synthesizes one of these,
    correctly ported, rather than failing elaboration). Detected via
    Yosys's own standard `blackbox` attribute, not inferred any other way.
    """
    return bool(mod.get("attributes", {}).get("blackbox"))


def _build_design(
    netlist: dict,
    entity_generics: dict[str, list[str]],
    entity_source_file: dict[str, str],
    architectures: list[tuple[str, str]],
    top: str,
) -> tuple[Design, list[UnresolvedInstance]]:
    modules = netlist["modules"]
    blackbox_modules = {modname for modname, mod in modules.items() if _is_blackbox(mod)}

    module_to_definition_id: dict[str, str] = {}
    module_to_base_and_params: dict[str, tuple[str, dict[str, str]]] = {}
    module_to_port_order: dict[str, list[str]] = {}
    for modname, mod in modules.items():
        if modname in blackbox_modules:
            continue  # unresolved reference, not a real Definition -- see _build_instances
        base_name, parameters = _reconstruct_base_name_and_parameters(modname, entity_generics, architectures)
        module_to_base_and_params[modname] = (base_name, parameters)
        module_to_definition_id[modname] = make_definition_id(base_name, parameters)
        module_to_port_order[modname] = list(mod.get("ports", {}).keys())

    definitions: dict[str, Definition] = {}
    unresolved: list[UnresolvedInstance] = []
    for modname, mod in modules.items():
        if modname in blackbox_modules:
            continue
        base_name, parameters = module_to_base_and_params[modname]
        ports = _build_ports(mod)
        port_names = {p.name for p in ports}
        name_by_bit = _build_bit_name_map(mod, port_names)
        definition_id = module_to_definition_id[modname]

        definitions[definition_id] = Definition(
            definition_id=definition_id,
            base_name=base_name,
            language=SourceLanguage.VHDL,
            source_file=entity_source_file.get(base_name.lower(), ""),
            parameters=parameters,
            ports=ports,
            nets=_build_nets(mod, port_names),
            instances=_build_instances(
                mod, module_to_definition_id, module_to_port_order, blackbox_modules, definition_id, name_by_bit, unresolved
            ),
            sequential_elements=_build_sequential_elements(mod, name_by_bit),
            continuous_assigns=_build_continuous_assigns(mod, name_by_bit),
        )

    if top not in module_to_definition_id:
        if top in blackbox_modules:
            raise ValueError(f"top entity '{top}' was never bound to a real definition (no matching entity found)")
        raise ValueError(f"top entity '{top}' not found among elaborated modules: {sorted(modules)}")
    return Design(definitions=definitions, top=module_to_definition_id[top]), unresolved


def _build_ports(mod: dict) -> list[Port]:
    ports = []
    for name, info in mod.get("ports", {}).items():
        width = len(info["bits"])
        bit_range = BitRange(width - 1, 0) if width > 1 else None
        ports.append(Port(name=name, direction=_DIRECTION_MAP[info["direction"]], bit_range=bit_range))
    return ports


def _build_nets(mod: dict, port_names: set[str]) -> list[Net]:
    nets = []
    for name, info in mod.get("netnames", {}).items():
        if info.get("hide_name") or name in port_names:
            continue
        width = len(info["bits"])
        bit_range = BitRange(width - 1, 0) if width > 1 else None
        nets.append(Net(name=name, bit_range=bit_range))
    return nets


def _build_bit_name_map(mod: dict, port_names: set[str]) -> dict[int, str]:
    """Map each netlist bit id to its best-known name: ports first, then
    non-hidden nets. Hidden/auto-generated names (Yosys `hide_name: 1`,
    e.g. the sink Yosys fabricates for a VHDL `q => open`) are deliberately
    excluded -- resolving a connection through one of these would fabricate
    a name for a signal that has none in the source.
    """
    del port_names  # ports are already authoritative in mod["ports"]; kept for signature symmetry with _build_nets
    name_by_bit: dict[int, str] = {}
    for name, info in mod.get("ports", {}).items():
        for bit in info["bits"]:
            name_by_bit[bit] = name
    for name, info in mod.get("netnames", {}).items():
        if info.get("hide_name"):
            continue
        for bit in info["bits"]:
            name_by_bit.setdefault(bit, name)
    return name_by_bit


def _resolve_bits_to_name(bits: list, name_by_bit: dict[int, str]) -> str | None:
    """A connection resolves to a name only if every bit maps to the SAME
    named signal -- partial/mixed/constant-tied connections are left
    unresolved rather than guessed at.
    """
    names = {name_by_bit.get(b) for b in bits}
    if len(names) == 1:
        return names.pop()
    return None


# The near-universal Yosys RTLIL output-pin name for a combinational/mux
# primitive ("Y") and for a sequential primitive ("Q") -- see
# _build_continuous_assigns()'s own docstring for the "recognize known
# shapes, don't guess on the rest" scope this deliberately follows,
# matching the same precedent _find_reset_mux()/_trace_through_one_inverter()
# already establish elsewhere in this file.
_CELL_OUTPUT_PIN_CANDIDATES = ("Y", "Q")


def _build_continuous_assigns(mod: dict, name_by_bit: dict[int, str]) -> list[ContinuousAssign]:
    """VHDL/Yosys equivalent of the Verilog front-end's ContinuousAssign
    extraction -- see ContinuousAssign's own docstring for the full
    "why" (connectivity-flattening's pass-through-vs-real-logic
    distinction). Two genuinely different netlist shapes both need to
    become a ContinuousAssign record here -- omitting EITHER would make
    flatten.py's own trace_driver() wrongly report UNDRIVEN for a
    signal that really is driven:

    (1) TRUE pass-through wiring (a plain VHDL `y <= a;` outside any
    process) -- confirmed via this project's own spike investigation
    into a real Yosys JSON netlist shape -- collapses to pure bit-id
    ALIASING, with NO cell at all between the two named signals;
    `source` is set to that other signal's own name. NOT independently
    re-verified against a live GHDL/yosys run in this environment (see
    this module's own "unverified" note at the top of this feature's
    own history) -- ghdl/yosys are not installed here.

    (2) A signal driven by ANY Yosys cell -- $dff/$adff/$dlatch (a real
    register; already recognized elsewhere in this file for
    _build_sequential_elements(), but that function's own job is
    metadata extraction, not connectivity -- flatten.py still needs an
    explicit fact that something real drives this signal here) or any
    combinational primitive ($not/$mux/$and/...) -- gets `source=None`:
    real logic drives it HERE, a stopping point for flatten.py's own
    trace, never walked through as if transparent.

    A named signal actually driven by a REAL user-instantiated
    submodule's own output port needs no entry here at all -- that's
    already fully captured by Instance.connections/flatten.py's own
    net_group(), exactly parallel to how the Verilog front-end never
    emits a ContinuousAssign for a plain submodule-port connection
    either."""
    all_named: dict[str, list] = {name: info["bits"] for name, info in mod.get("ports", {}).items()}
    for name, info in mod.get("netnames", {}).items():
        if not info.get("hide_name") and name not in all_named:
            all_named[name] = info["bits"]

    cells = mod.get("cells", {})
    assigns: list[ContinuousAssign] = []
    for target_name, target_bits in all_named.items():
        driving_cell = next(
            (cell for cell in cells.values() if any(cell.get("connections", {}).get(pin) == target_bits for pin in _CELL_OUTPUT_PIN_CANDIDATES)),
            None,
        )
        if driving_cell is not None:
            # `referenced_signals` (see ContinuousAssign's own docstring
            # for the full "why" -- mirrors the Verilog front-end's own
            # extraction): every one of this cell's OWN non-output pins,
            # resolved to a named signal where possible -- an unnamed
            # intermediate wire (e.g. feeding from yet another cell)
            # resolves to None and is simply omitted, not guessed at.
            referenced = tuple(
                dict.fromkeys(
                    name
                    for pin, bits in driving_cell.get("connections", {}).items()
                    if pin not in _CELL_OUTPUT_PIN_CANDIDATES
                    for name in [_resolve_bits_to_name(bits, name_by_bit)]
                    if name is not None
                )
            )
            assigns.append(ContinuousAssign(target=target_name, source=None, referenced_signals=referenced))
            continue
        source_name = _resolve_bits_to_name(target_bits, name_by_bit)
        if source_name is not None and source_name != target_name:
            assigns.append(ContinuousAssign(target=target_name, source=source_name, referenced_signals=(source_name,)))
    return assigns


def _build_instances(
    mod: dict,
    module_to_definition_id: dict[str, str],
    module_to_port_order: dict[str, list[str]],
    blackbox_modules: set[str],
    owner_definition_id: str,
    name_by_bit: dict[int, str],
    unresolved: list[UnresolvedInstance],
) -> list[Instance]:
    """Build each cell's connections in the *target* module's declared port
    order. Yosys's own `cell["connections"]` dict order does not preserve
    source port-declaration order (observed alphabetical in practice) --
    without this reordering, otherwise-identical instances could compare
    unequal on connection order alone, and the order wouldn't match the
    referenced Definition's own port order the way Verilog's does.
    """
    instances = []
    for cell_name, cell in mod.get("cells", {}).items():
        cell_type = cell["type"]
        raw_connections = cell.get("connections", {})

        if cell_type in blackbox_modules:
            # An unbound component -- would otherwise be silently dropped
            # the same way a Yosys primitive is (both simply aren't in
            # module_to_definition_id), which is correct for a primitive
            # but wrong here: this is a real instance the caller needs to
            # know about, not internal synthesis machinery to ignore.
            unresolved.append(
                UnresolvedInstance(
                    owner_definition_id=owner_definition_id,
                    name=cell_name,
                    referenced_definition_name=cell_type,
                    connections={
                        port_name: _resolve_bits_to_name(bits, name_by_bit) for port_name, bits in raw_connections.items()
                    },
                )
            )
            continue

        if cell_type not in module_to_definition_id:
            continue  # a Yosys primitive ($adff, $not, ...), not a user module instance

        port_order = module_to_port_order.get(cell_type, list(raw_connections.keys()))
        connections = [
            PortConnection(instance_port=port_name, connected_to=_resolve_bits_to_name(raw_connections[port_name], name_by_bit))
            for port_name in port_order
            if port_name in raw_connections
        ]
        instances.append(Instance(name=cell_name, definition_id=module_to_definition_id[cell_type], connections=connections))
    return instances


def _param_bool(param_value: str) -> bool:
    """Yosys JSON encodes single-bit parameters as a binary string, e.g. '...0001'."""
    return param_value[-1] == "1"


def _build_sequential_elements(mod: dict, name_by_bit: dict[int, str]) -> list[SequentialElement]:
    cells = mod.get("cells", {})
    elements = []
    for cell in cells.values():
        if cell["type"] not in _SEQ_CELL_TYPES:
            continue
        element = _build_dlatch(cell, name_by_bit) if cell["type"] == "$dlatch" else _build_dff(cell, cells, name_by_bit)
        if element is not None:
            elements.append(element)
    return elements


def _build_dff(cell: dict, cells: dict, name_by_bit: dict[int, str]) -> SequentialElement | None:
    conns = cell["connections"]
    params = cell["parameters"]

    output_signal = _resolve_bits_to_name(conns["Q"], name_by_bit)
    clock_ref = _resolve_bits_to_name(conns["CLK"], name_by_bit)
    if output_signal is None or clock_ref is None:
        return None  # can't identify this flop's own signals -- leave unrecognized, don't guess
    edge = Edge.RISING if _param_bool(params["CLK_POLARITY"]) else Edge.FALLING

    # `data_ref` (see SequentialElement's own docstring) -- for $adff,
    # ARST is a separate control pin from D, so D is always the real
    # data path directly; for a plain $dff, D may instead be fed by a
    # sync-reset mux (resolved below, once `mux` is known), in which
    # case the mux's own non-constant side is the real data.
    data_ref = _resolve_bits_to_name(conns["D"], name_by_bit)

    reset_ref = None
    reset_active_low = None
    reset_kind = ResetKind.NONE
    if cell["type"] == "$adff":
        reset_ref, reset_active_low = _resolve_control_signal(
            conns["ARST"], name_by_bit, cells, _param_bool(params["ARST_POLARITY"])
        )
        if reset_ref is not None:
            reset_kind = ResetKind.ASYNC
    elif cell["type"] == "$dff":
        # A plain $dff can still be a *synchronous*-reset register: GHDL
        # never emits a single $sdff-style primitive for this shape -- it
        # compiles `if rst_n = '0' then q <= 0; elsif rising_edge(clk) then
        # q <= d; end if;` down to a $mux (selecting between the reset value
        # and real data) feeding an otherwise-ordinary $dff. Confirmed
        # empirically that skipping this check doesn't just miss the reset
        # -- it actively misreports a real sync-reset register as
        # reset_kind=NONE, since the D input still resolves to *something*
        # (the mux's Y output happens to not be a named signal, so the old
        # code just silently treated the flop as resetless). That's a real
        # bug, not a coverage gap.
        mux = _find_reset_mux(conns["D"], cells)
        if mux is not None:
            reset_ref, reset_active_low = _resolve_control_signal(
                mux["select_bits"], name_by_bit, cells, mux["pin_trigger_high"]
            )
            if reset_ref is not None:
                reset_kind = ResetKind.SYNC
                data_ref = _resolve_bits_to_name(mux["data_bits"], name_by_bit)

    return SequentialElement(
        output_signal=output_signal,
        is_latch=False,
        clock_ref=clock_ref,
        edge=edge,
        reset_ref=reset_ref,
        reset_kind=reset_kind,
        reset_active_low=reset_active_low,
        data_ref=data_ref,
    )


def _build_dlatch(cell: dict, name_by_bit: dict[int, str]) -> SequentialElement | None:
    """Plain enable-only latch ($dlatch: EN/D/Q, no reset/set pin). A latch
    *with* a reset or set does not come out as a richer single cell type the
    way $adff does for async-reset flops -- confirmed empirically it
    compiles to $dlatch fed by its own $not/$mux/$or chain implementing the
    reset/set logic ahead of the latch's own D and EN pins. That's a
    genuinely more complex shape than the one-hop-inverter tracing used
    elsewhere in this file; deliberately not attempted here -- see
    docs/milestone1_rtl_elaborator.md for the tracked-gap entry. Only the
    simple enable-only case is recognized; anything else involving a
    $dlatch is left unrecognized rather than guessed at.
    """
    conns = cell["connections"]
    params = cell["parameters"]

    output_signal = _resolve_bits_to_name(conns["Q"], name_by_bit)
    enable_ref = _resolve_bits_to_name(conns["EN"], name_by_bit)
    if output_signal is None or enable_ref is None:
        return None

    return SequentialElement(
        output_signal=output_signal,
        is_latch=True,
        clock_ref=enable_ref,  # per the IR schema: doubles as the enable/gate signal for latches
        edge=None,
        reset_kind=ResetKind.NONE,
        data_ref=_resolve_bits_to_name(conns["D"], name_by_bit),
    )


def _trace_through_one_inverter(bits: list, name_by_bit: dict[int, str], cells: dict) -> tuple[str | None, bool]:
    """Resolve a bit-id list back to a named signal, tracing through at most
    one $not cell. Returns (name, was_inverted); (None, False) if
    unresolved. Shared by both the async-reset (ARST pin) and sync-reset
    (mux select) cases -- same one-hop-only principle either way.
    """
    direct = _resolve_bits_to_name(bits, name_by_bit)
    if direct is not None:
        return direct, False

    for cell in cells.values():
        if cell["type"] != "$not":
            continue
        if cell["connections"].get("Y") == bits:
            source_name = _resolve_bits_to_name(cell["connections"]["A"], name_by_bit)
            if source_name is not None:
                return source_name, True

    return None, False


def _resolve_control_signal(
    bits: list, name_by_bit: dict[int, str], cells: dict, pin_trigger_high: bool
) -> tuple[str | None, bool | None]:
    """Resolve a control pin (an ARST pin, or a sync-reset mux's select)
    back to a named signal and the reset's polarity *relative to that
    signal* -- accounting for a possible single inverter GHDL's synthesis
    frontend inserts between an active-low source condition
    (`if rst_n = '0'`) and the primitive's own polarity-flagged control
    input. `pin_trigger_high` is whichever raw value (1 for ARST_POLARITY,
    or "the constant sits on mux input B" for a sync mux) actually
    triggers the reset at the *pin*, before accounting for any inversion.
    """
    name, was_inverted = _trace_through_one_inverter(bits, name_by_bit, cells)
    if name is None:
        return None, None
    active_low = pin_trigger_high if was_inverted else not pin_trigger_high
    return name, active_low


def _find_reset_mux(d_bits: list, cells: dict) -> dict | None:
    """If a $dff's D input is fed entirely by a single $mux selecting
    between a compile-time constant (the reset value) and a real signal
    (the data path), return what's needed to resolve it as a sync reset.
    None for anything else -- a genuine 2:1 data mux unrelated to reset (two
    real signal inputs), a multi-bit select, or a chain of muxes are all
    left unrecognized rather than guessed at.
    """
    for cell in cells.values():
        if cell["type"] != "$mux":
            continue
        if cell["connections"].get("Y") != d_bits:
            continue
        a_bits = cell["connections"].get("A", [])
        b_bits = cell["connections"].get("B", [])
        select_bits = cell["connections"].get("S", [])
        if len(select_bits) != 1:
            continue  # not a simple single-condition mux
        a_is_const = bool(a_bits) and all(isinstance(b, str) for b in a_bits)
        b_is_const = bool(b_bits) and all(isinstance(b, str) for b in b_bits)
        if a_is_const == b_is_const:
            continue  # need exactly one constant side (the reset value) and one real signal (the data)
        # Yosys $mux semantics: Y = S ? B : A -- if B is the constant, the
        # reset fires when the raw select value is 1. `data_bits` (found
        # alongside SequentialElement.data_ref, same "why" as its own
        # docstring) is whichever side is NOT the constant -- the real
        # data path feeding this register in normal (non-reset)
        # operation.
        return {"select_bits": select_bits, "pin_trigger_high": b_is_const, "data_bits": a_bits if b_is_const else b_bits}
    return None
