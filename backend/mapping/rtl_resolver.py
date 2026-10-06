"""RTL-hierarchy reference resolution (Milestone 3).

Resolves a power-intent reference string (an instance path, optionally
with a trailing port/pin/net leaf name) against the elaborated RTL Design.
Two-step: combine the construct's own authoring `scope` with the raw
string into a full, top-relative path, then walk the RTL hierarchy to the
longest matching instance-path prefix -- if the whole thing matches an
instance path, it's an INSTANCE reference (e.g. a PowerDomain's
`elements`); otherwise the last segment is checked as a port/net leaf name
on the matched instance's resolved Definition.

Case folding: VHDL identifiers are case-insensitive. Matching folds case
using the *resolved* leaf Definition's own language
(`normalize_identifier_case`, extracted from Milestone 1d's
mixed_language._normalize for this exact reuse) -- the referencing
power-intent author doesn't necessarily know or care about VHDL's case
folding, so matching happens on the target's terms, same rule 1d already
established and verified for cross-HDL instantiation.

Separator handling: UPF hierarchical paths are `/`-joined and support a
leading `/` for an absolute (top-relative) path -- both confirmed via
research during Milestone 3 planning. CPF paths are relative to the
current scope by default, with `.` as CPF's own default hierarchy
separator; `set_hierarchy_separator` (which could override that default)
is not registered by 2b's CPF elaborator at all, so it's an unrecognized
Tcl command there -- any CPF file that tried to override the separator
would already fail elaboration before ever reaching this resolver. Every
CPF design this resolver can actually see therefore uses the default `.`
separator; hardcoded below with this comment as the tracked reason it's
safe today, not a permanent assumption -- revisit if/when
`set_hierarchy_separator` is wired up in 2b.

Virtual/logic ports (CPF `set_design -ports`, UPF `create_logic_port`) are
not RTL, and 2a/2b don't populate any declared-virtual-signal-names data
yet (see the doc's Milestone 3 planning section) -- `virtual_names` is
accepted here as the forward-compatible slot for that data, currently
always empty in practice.
"""

from __future__ import annotations

from backend.elaborator.power_intent.ir import SourceFormat
from backend.elaborator.rtl.ir import Design, normalize_identifier_case
from backend.mapping.schema import ResolutionKind


def _separator(source_format: SourceFormat) -> str:
    return "/" if source_format == SourceFormat.UPF else "."


def _combine(raw: str, scope: str, sep: str) -> str:
    if raw == ".":
        # UPF's "." idiom for "the domain's own scope, the whole thing" --
        # confirmed real usage, not a guess (see
        # tests/backend/golden_upf/upf_basic/top.upf's
        # `create_power_domain PD_TOP -elements {.}`, asserted against in
        # test_upf_elaborate.py). Not a literal instance named ".": resolves
        # to the scope itself, with no trailing segment appended.
        return scope
    if raw.startswith(sep):
        # UPF absolute path -- already top-relative once the leading
        # separator is stripped. CPF has no documented absolute-path
        # syntax; a leading separator there is handled the same way
        # defensively rather than erroring, since drawing that line more
        # precisely isn't verified.
        return raw[len(sep):]
    if not scope:
        return raw
    return f"{scope}{sep}{raw}" if raw else scope


def resolve_rtl_reference(
    raw: str,
    scope: str,
    source_format: SourceFormat,
    rtl: Design,
    virtual_names: frozenset = frozenset(),
) -> tuple:
    """Returns (kind, resolved_path, resolved_definition_id, detail).

    Plain tuple, not ResolvedReference itself -- this is an internal
    resolution primitive; map_design.py is the only place that knows how
    to place a result into a ReferenceLocation.
    """
    if raw in virtual_names:
        return (ResolutionKind.VIRTUAL, None, None, None)

    sep = _separator(source_format)
    full_path = _combine(raw, scope, sep)
    segments = [s for s in full_path.split(sep) if s != ""]

    root_name = rtl.definitions[rtl.top].base_name
    nodes_by_path = {node.path: node for node in rtl.walk(separator=sep)}

    # Try the full path as an instance path first (e.g. PowerDomain.elements
    # naming instances directly, with no trailing port/pin/net).
    candidate = sep.join([root_name] + segments) if segments else root_name
    if candidate in nodes_by_path:
        node = nodes_by_path[candidate]
        return (ResolutionKind.INSTANCE, candidate, node.definition.definition_id, None)

    if not segments:
        return (ResolutionKind.DANGLING, None, None, f"'{raw}': empty path")

    # Otherwise, the last segment is a port/pin/net leaf name on the
    # instance path formed by everything before it.
    *prefix_segments, leaf = segments
    prefix_path = sep.join([root_name] + prefix_segments) if prefix_segments else root_name
    if prefix_path not in nodes_by_path:
        return (
            ResolutionKind.DANGLING,
            None,
            None,
            f"'{raw}': no instance at path '{prefix_path}' (resolved from scope '{scope}')",
        )
    definition = nodes_by_path[prefix_path].definition
    leaf_lang = definition.language
    folded_leaf = normalize_identifier_case(leaf, leaf_lang)

    if definition.is_black_box:
        # A black-box Definition (1e .lib macro, or an unresolved leaf of a
        # mixed-language chain) has no internal RTL -- only its declared
        # Ports are checkable; there are no nets/sub-instances to fall back
        # to.
        port_names = {normalize_identifier_case(p.name, leaf_lang) for p in definition.ports}
        if folded_leaf in port_names:
            return (ResolutionKind.BLACK_BOX_PORT, f"{prefix_path}{sep}{leaf}", definition.definition_id, None)
        return (
            ResolutionKind.UNRESOLVABLE_BLACK_BOX,
            None,
            definition.definition_id,
            f"'{raw}': '{leaf}' is not a declared port of black-box definition '{definition.base_name}' "
            "(black-box definitions have no internal nets/instances to check against)",
        )

    port_names = {normalize_identifier_case(p.name, leaf_lang) for p in definition.ports}
    if folded_leaf in port_names:
        return (ResolutionKind.PORT, f"{prefix_path}{sep}{leaf}", definition.definition_id, None)
    net_names = {normalize_identifier_case(n.name, leaf_lang) for n in definition.nets}
    if folded_leaf in net_names:
        return (ResolutionKind.NET, f"{prefix_path}{sep}{leaf}", definition.definition_id, None)
    return (
        ResolutionKind.DANGLING,
        None,
        None,
        f"'{raw}': '{leaf}' is not a port, net, or instance under '{prefix_path}'",
    )
