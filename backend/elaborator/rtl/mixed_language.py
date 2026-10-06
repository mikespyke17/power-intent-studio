"""Mixed-language instantiation resolution (Milestone 1d).

Orchestrates the single-language front-ends (verilog.elaborate, vhdl.elaborate)
to resolve cross-HDL instantiation: a Verilog module instantiating a VHDL
entity, or a VHDL entity instantiating a Verilog module. Neither front-end
was modified to *know* this module exists -- each only grew a small,
symmetric "tolerate exactly one kind of unresolved reference, report it
instead of crashing or silently dropping it" extension (see their own
docstrings), which they already needed to expose to any caller wanting this
information, not something specific to how this module happens to use it.
1a/1b's own `elaborate()` stays strict and behaviorally unchanged.

Design, confirmed against real pyslang/GHDL behavior before writing this
(see docs/milestone1_rtl_elaborator.md for the full spike writeup):

- Neither underlying tool supports "pause elaboration, accept an externally
  resolved definition, resume." Both do single-invocation, closed-world
  elaboration over a given file set. So this can't be built as a callback
  hook inside 1a/1b's elaborate() the way a `.lib` black-box lookup (1e,
  not implemented yet) naturally could be -- there is no single elaboration
  pass to hook into.
- Both tools already expose "here's a reference I couldn't resolve, and
  here's what I know about its interface" on their own: pyslang via
  UnknownModule diagnostics + UninstantiatedDefSymbol; GHDL/Yosys by
  auto-synthesizing a `blackbox`-attributed module stub for an unbound VHDL
  `component`. No custom discovery scanner was needed for either side.
- Discovery is NOT a uniform "did elaboration fail" check across languages:
  Verilog surfaces unresolved references as *error* diagnostics (tolerated
  via elaborate_allowing_unresolved). GHDL+Yosys elaboration succeeds
  (exit 0) regardless -- there is no failure signal at all -- so VHDL-side
  discovery explicitly scans the JSON for the `blackbox` attribute instead,
  independent of exit status.
- v1 scope: single-direction (leaf) cross-language references only. A
  design where both languages need something from the other (an alternating
  Verilog->VHDL->Verilog chain) is not handled -- the leaf side is
  elaborated in *strict* mode, so if the "leaf" itself turns out to have
  its own unresolved reference, that surfaces as a clear error rather than
  being silently mishandled or guessed at.
- Cross-language instantiations with a parameter/generic override are not
  yet supported -- fails loudly (from the language front-end's own
  UnresolvedInstance collection) rather than silently resolving against
  default generics. Only default-configuration references are resolved.
- The stub/placeholder concept here is purely transitional bookkeeping,
  local to this module (UnresolvedInstance from each front-end). It never
  touches backend/elaborator/rtl/ir/schema.py and never appears in the
  Design handed back to the caller -- unlike a future `.lib` black box
  (1e), which is permanent and will need to persist into the IR. Both can
  currently be *signaled* by GHDL/Yosys via the same raw `blackbox` JSON
  attribute, but this module never assumes an unresolved blackbox is a
  `.lib` macro: 1e doesn't exist, and guessing would be reaching into a
  milestone that hasn't started. Anything that doesn't resolve against the
  other language's real definitions fails loudly, naming what's missing.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Literal

from backend.elaborator.rtl import verilog, vhdl
from backend.elaborator.rtl.ir import Definition, Design, Instance, PortConnection, SourceLanguage, normalize_identifier_case

_VERILOG_EXTENSIONS = (".v", ".sv")
_VHDL_EXTENSIONS = (".vhd", ".vhdl")

TopLanguage = Literal["verilog", "vhdl"]


def elaborate_mixed(files: list[str], top: str, top_language: TopLanguage | None = None) -> Design:
    """Elaborate a mixed Verilog/VHDL design rooted at `top`.

    `top_language` disambiguates which front-end should own the top-level
    elaboration when `top`'s name happens to exist in both source sets
    (a real, if unusual, possibility -- see _detect_top_language); otherwise
    it's inferred from a cheap, non-elaborating scan of both file sets.
    """
    verilog_files, vhdl_files = _partition_files(files)
    language = top_language or _detect_top_language(top, verilog_files, vhdl_files)

    if language == "verilog":
        return _elaborate_verilog_owned(top, verilog_files, vhdl_files)
    if language == "vhdl":
        return _elaborate_vhdl_owned(top, verilog_files, vhdl_files)
    raise ValueError(f"top_language must be 'verilog' or 'vhdl', got {language!r}")


def _partition_files(files: list[str]) -> tuple[list[str], list[str]]:
    verilog_files = [f for f in files if f.lower().endswith(_VERILOG_EXTENSIONS)]
    vhdl_files = [f for f in files if f.lower().endswith(_VHDL_EXTENSIONS)]
    unknown = [f for f in files if f not in verilog_files and f not in vhdl_files]
    if unknown:
        raise ValueError(f"file(s) with unrecognized extension (expected .v/.sv/.vhd/.vhdl): {unknown}")
    return verilog_files, vhdl_files


def _detect_top_language(top: str, verilog_files: list[str], vhdl_files: list[str]) -> TopLanguage:
    in_verilog = top in (verilog.list_module_names(verilog_files) if verilog_files else set())
    in_vhdl = top.lower() in (vhdl.list_entity_names(vhdl_files) if vhdl_files else set())
    if in_verilog and in_vhdl:
        raise ValueError(f"'{top}' is defined in both the Verilog and VHDL source sets -- pass top_language explicitly")
    if in_verilog:
        return "verilog"
    if in_vhdl:
        return "vhdl"
    raise ValueError(f"top '{top}' not found as a module/entity name in either source set")


def _elaborate_verilog_owned(top: str, verilog_files: list[str], vhdl_files: list[str]) -> Design:
    owner_design, unresolved = verilog.elaborate_allowing_unresolved(verilog_files, top=top)
    if not unresolved:
        return owner_design
    return _merge(owner_design, unresolved, leaf_files=vhdl_files, leaf_language="vhdl")


def _elaborate_vhdl_owned(top: str, verilog_files: list[str], vhdl_files: list[str]) -> Design:
    owner_design, unresolved = vhdl.elaborate_allowing_unresolved(vhdl_files, top=top)
    if not unresolved:
        return owner_design
    return _merge(owner_design, unresolved, leaf_files=verilog_files, leaf_language="verilog")


def _merge(owner_design: Design, unresolved: list, leaf_files: list[str], leaf_language: TopLanguage) -> Design:
    if not leaf_files:
        names = sorted({u.referenced_definition_name for u in unresolved})
        raise ValueError(f"{len(unresolved)} unresolved reference(s) to {names}, but no {leaf_language} source files were given")

    referenced_names = sorted({u.referenced_definition_name for u in unresolved})
    leaf_definitions: dict[str, Definition] = {}
    resolved_id_by_name: dict[str, str] = {}
    for name in referenced_names:
        leaf_design = _elaborate_leaf(name, leaf_files, leaf_language)
        leaf_definitions.update(leaf_design.definitions)
        resolved_id_by_name[_normalize(name, leaf_language)] = leaf_design.top

    patched_definitions = dict(owner_design.definitions)
    by_owner: dict[str, list] = {}
    for u in unresolved:
        by_owner.setdefault(u.owner_definition_id, []).append(u)

    for owner_id, owner_unresolved in by_owner.items():
        owner_def = patched_definitions[owner_id]
        new_instances = list(owner_def.instances)
        for u in owner_unresolved:
            resolved_id = resolved_id_by_name[_normalize(u.referenced_definition_name, leaf_language)]
            resolved_def = leaf_definitions[resolved_id]
            _validate_connections(u, resolved_def, leaf_language)
            new_instances.append(
                Instance(
                    name=u.name,
                    definition_id=resolved_id,
                    connections=[
                        PortConnection(instance_port=port, connected_to=signal) for port, signal in u.connections.items()
                    ],
                )
            )
        patched_definitions[owner_id] = replace(owner_def, instances=new_instances)

    patched_definitions.update(leaf_definitions)
    return Design(definitions=patched_definitions, top=owner_design.top)


def _elaborate_leaf(name: str, leaf_files: list[str], leaf_language: TopLanguage) -> Design:
    """Elaborate the referenced definition as its own top, in *strict* mode.

    Deliberately not tolerant: if this leaf itself references something
    unresolved (an alternating cross-language chain), that's out of 1d's v1
    scope and should surface as a clear error here, not be silently
    mishandled or guessed at.
    """
    try:
        if leaf_language == "vhdl":
            return vhdl.elaborate(leaf_files, top=name)
        return verilog.elaborate(leaf_files, top=name)
    except ValueError as e:
        # Deliberately does not just prefix str(e): the underlying strict
        # elaborate() error (e.g. "found N unbound/blackbox component
        # reference(s)... use the mixed-language orchestrator") is written
        # for a caller invoking elaborate() *directly* -- that advice is
        # actively wrong here, since we already are the orchestrator.
        # Point at the real, actionable limitation instead. Deliberately
        # hedged rather than asserting "this is the alternating-chain case":
        # 1d has no way to distinguish that from a genuinely undefined
        # reference in the leaf's own source without attempting the exact
        # multi-hop resolution that's out of v1 scope.
        raise ValueError(
            f"leaf '{name}' ({leaf_language}) itself contains an unresolved reference and could not be "
            f"elaborated on its own: {e}\nThis is either a deliberately out-of-scope alternating cross-language "
            "chain (v1 only resolves single-direction/leaf references -- see "
            "docs/milestone1_rtl_elaborator.md) or a genuinely undefined reference in the leaf's own source; "
            "the mixed-language orchestrator does not attempt multi-hop resolution."
        ) from e


def _normalize(name: str, language: TopLanguage) -> str:
    # TopLanguage's string values ("verilog"/"vhdl") match SourceLanguage's
    # enum values exactly -- delegates to the shared, public case-folding
    # rule (also used by Milestone 3's RTL-hierarchy resolver) rather than
    # each caller re-implementing "fold if vhdl" on its own.
    return normalize_identifier_case(name, SourceLanguage(language))


def _validate_connections(unresolved, resolved: Definition, leaf_language: TopLanguage) -> None:
    """Light validation: every port name used at the instantiation site must
    exist on the resolved definition. Case-insensitive when the resolved
    definition is VHDL (matching VHDL's own semantics -- the referencing
    Verilog source's port-map names were written by a Verilog author who
    doesn't necessarily know or care about VHDL's case folding), case
    sensitive when it's Verilog. Does not check direction or width -- name
    existence is the cheap, unambiguous check; a fuller check belongs with
    UPF-RTL mapping's own validation pass (Milestone 3), not duplicated here.
    """
    leaf_lang = SourceLanguage(leaf_language)
    resolved_port_names = {normalize_identifier_case(p.name, leaf_lang) for p in resolved.ports}
    used_names = {normalize_identifier_case(n, leaf_lang) for n in unresolved.connections}
    unknown = used_names - resolved_port_names
    if unknown:
        raise ValueError(
            f"instance '{unresolved.name}' connects port(s) {sorted(unknown)} that don't exist on the resolved "
            f"definition '{resolved.base_name}' (real ports: {sorted(resolved_port_names)})"
        )
