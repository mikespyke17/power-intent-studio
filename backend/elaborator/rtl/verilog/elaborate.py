"""Verilog/SystemVerilog front-end integration (Milestone 1a).

Uses pyslang (https://pypi.org/project/pyslang) to parse and elaborate
Verilog/SystemVerilog sources, then normalizes the elaborated AST into the
unified IR (backend/elaborator/rtl/ir).

Sequential-element extraction here is still deliberately narrow in one
sense -- no data-path/next-state modeling, ever (which value a register is
assigned *to* is never inspected, only which signals are assigned at all)
-- but, since a real personal SoC design surfaced it as a real gap, no
longer narrow about *shape*: one edge-triggered always block recognizes
its clock/reset structure (active-low/-high reset test, sync or async
based on whether the reset signal also appears in the sensitivity list --
not on sensitivity-list order, since a reset's own edge event is otherwise
indistinguishable from the clock's) once, from the top-level if/else (or a
bare resetless assignment, or nothing -- `always @(posedge clk) ...` with
no if/else at all), then walks the *entire* statement tree under that --
both branches, nested begin/end blocks, `case`/`unique case` arms -- to
collect every distinct signal actually assigned there. One always block
resetting/updating several unrelated registers together (extremely common,
idiomatic RTL -- `if (!rst_n) begin a<=0; b<=0; end else case(state) ...`)
now correctly produces one SequentialElement per register, all sharing
that block's clock/reset metadata, instead of the whole block silently
contributing nothing because it didn't match a single-assignment-per-branch
shape. Still not general flop/latch classification -- that's Milestone 1h's
overall job -- and still no set/preset distinction, no same-signal
multi-block-driver handling, no latch support beyond VHDL's existing basic
case; see docs/milestone1_rtl_elaborator.md's 1h tracked-gaps entries for
what's still deliberately deferred and why.
"""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass, field, replace

import pyslang
from pyslang import Diags
from pyslang.ast import (
    ArgumentDirection,
    BlockStatement,
    CaseStatement,
    Compilation,
    CompilationOptions,
    ConditionalStatement,
    ContinuousAssignSymbol,
    DefinitionKind,
    EdgeKind,
    ExpressionKind,
    ExpressionStatement,
    GenerateBlockArraySymbol,
    GenerateBlockSymbol,
    InstanceArraySymbol,
    InstanceSymbol,
    InterfacePortSymbol,
    ModportPortSymbol,
    ModportSymbol,
    NetSymbol,
    ParameterSymbol,
    ProceduralBlockSymbol,
    SignalEventControl,
    StatementList,
    TimedStatement,
    UnaryExpression,
    UnaryOperator,
    UninstantiatedDefSymbol,
    VariableSymbol,
)
from pyslang.syntax import SyntaxKind
from pyslang.syntax import SyntaxTree

from backend.elaborator.rtl.ir import (
    BitRange,
    ContinuousAssign,
    Definition,
    Design,
    Edge,
    Instance,
    InterfacePort,
    Modport,
    ModportPort,
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
    ArgumentDirection.In: PortDirection.IN,
    ArgumentDirection.Out: PortDirection.OUT,
    ArgumentDirection.InOut: PortDirection.INOUT,
    ArgumentDirection.Ref: PortDirection.INOUT,  # rare on module ports; no closer mapping exists
}

_EDGE_MAP = {
    EdgeKind.PosEdge: Edge.RISING,
    EdgeKind.NegEdge: Edge.FALLING,
}


@dataclass(frozen=True)
class UnresolvedInstance:
    """An instantiation pyslang could not resolve to a known module -- most
    commonly a reference to a module actually defined in a different HDL.

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


class ElaborationError(ValueError):
    """Raised for a real pyslang error diagnostic (syntax error, unknown
    module, etc.). Carries `.file`/`.line` from the *first* offending
    diagnostic's real source location, so a caller (e.g. the frontend's
    Diagnostic adapter) can populate structured file/line columns instead
    of only ever having a free-text message to show. Previously this was
    a bare ValueError naming only the diagnostic *code* (e.g.
    "DiagCode(ExpectedToken)") with no file, no line, and no description
    -- confirmed genuinely unhelpful against a real syntax bug before this
    fix, not a hypothetical complaint.
    """

    def __init__(self, message: str, file: str | None, line: int | None):
        super().__init__(message)
        self.file = file
        self.line = line


def _format_diagnostics(compilation: Compilation, diagnostics: list) -> tuple[str, str | None, int | None]:
    """Formats `diagnostics` using pyslang's own real diagnostic-reporting
    machinery (DiagnosticEngine + TextDiagnosticClient) -- the same
    mechanism a real pyslang-based CLI tool would use -- rather than
    hand-rolling message text from raw DiagCode names. Returns (message,
    file, line) where file/line come from the *first* diagnostic's real
    source location via the compilation's own SourceManager -- confirmed
    directly against a real syntax error (a missing comma in a port
    connection list) to include the file path, 1-based line/column, the
    actual source line, and a caret pointing at the exact offending token,
    not just a diagnostic code name.
    """
    engine = pyslang.DiagnosticEngine(compilation.sourceManager)
    client = pyslang.TextDiagnosticClient()
    engine.addClient(client)
    for d in diagnostics:
        engine.issue(d)
    message = client.getString()

    first_location = diagnostics[0].location
    file = compilation.sourceManager.getFileName(first_location) or None
    line = compilation.sourceManager.getLineNumber(first_location) or None
    return message, file, line


def _make_compilation(top: str | None) -> Compilation:
    """`top`, if given, is passed to pyslang as `CompilationOptions.topModules`
    -- not just used to filter the auto-detected top-level instances after
    the fact. This matters for a real, verified-not-assumed case: a module
    that legitimately *is* instantiated elsewhere in the given file set
    (e.g. compiling just a DUT submodule out of a larger loaded design)
    would never appear in pyslang's own auto-detected `topInstances` at
    all, so post-hoc filtering can never find it -- `topModules` tells
    pyslang to treat it as an elaboration root regardless of where else
    it's instantiated. Confirmed directly: with `topModules={name}` set,
    `getRoot().topInstances` contains exactly that instance (any other,
    otherwise-unreferenced top-level module in the file set is silently
    fine, not flagged as an error), and a `name` that doesn't exist at all
    raises pyslang's own `InvalidTopModule` diagnostic -- already caught by
    the existing error-diagnostics check below, no separate handling
    needed. `top is None` (auto-detect) is unaffected -- topModules is
    only set when a name is actually given.
    """
    compilation_options = CompilationOptions()
    if top is not None:
        compilation_options.topModules = {top}
    bag = pyslang.Bag()
    bag.compilationOptions = compilation_options
    return Compilation(bag)


def _add_sources(compilation, files: list[str], defines: dict[str, str | None] | None) -> None:
    """Add `files` to `compilation`, with `defines` in scope if any.

    WHY TWO PATHS. pyslang 11 exposes no constructible
    `PreprocessorOptions`, so there is no structured way to hand slang a
    `+define+` -- `Bag.preprocessorOptions` exists as a slot with no
    type behind it (verified against the installed build, not assumed).
    What does work is a generated header carrying real `` `define ``
    directives, parsed in the SAME compilation unit as the sources, so
    the macros are visible to them.

    `SyntaxTree.fromFiles([...])` is what makes one unit out of several
    files; the per-file `fromFile` loop makes one unit EACH, which is
    why a define in a separate file does not reach the others (confirmed
    empirically -- the wrong `ifdef` branch was taken).

    So: no defines, no change at all -- the original per-file loop, and
    every existing design elaborates exactly as before. With defines,
    one shared unit, which is also what `+define+` means: a define
    applies across the compile, not to one file.
    """
    if not defines:
        for f in files:
            compilation.addSyntaxTree(SyntaxTree.fromFile(f))
        return
    header = _write_defines_header(defines)
    compilation.addSyntaxTree(SyntaxTree.fromFiles([header, *files]))


def _write_defines_header(defines: dict[str, str | None]) -> str:
    """The defines as a real `.svh` on disk, since `fromFiles` takes
    paths. Written to a temp directory that outlives this call (slang
    reads lazily and reports diagnostics against the file later), and
    left for the OS to reap -- deleting it would risk a dangling source
    reference in a diagnostic, which is worse than a small temp file."""
    directory = tempfile.mkdtemp(prefix="pis_defines_")
    path = os.path.join(directory, "__command_line_defines.svh")
    with open(path, "w") as handle:
        for name, value in defines.items():
            handle.write(f"`define {name}\n" if value is None else f"`define {name} {value}\n")
    return path


def elaborate(
    files: list[str],
    top: str | None = None,
    defines: dict[str, str | None] | None = None,
    include_dirs: list[str] | None = None,
) -> Design:
    """Parse and elaborate the given Verilog/SystemVerilog files into a Design.

    `top`, if given, selects a specific module as the elaboration root --
    whether or not it's also separately instantiated elsewhere in `files`
    (see _make_compilation's docstring). Defaults to the original
    auto-detect-the-sole-top behavior.

    `defines` are preprocessor macros (name -> value, None for a bare
    define) and `include_dirs` are `` `include `` search paths -- both
    as a filelist's `+define+`/`+incdir+` supply them. Omitting both
    leaves elaboration byte-for-byte as it was.
    """
    compilation = _make_compilation(top)
    _install_include_dirs(include_dirs)
    _add_sources(compilation, files, defines)

    errors = [d for d in compilation.getAllDiagnostics() if d.isError()]
    if errors:
        message, file, line = _format_diagnostics(compilation, errors)
        raise ElaborationError(message, file, line)

    selected = _select_top_instance(compilation, top)

    definitions: dict[str, Definition] = {}
    unresolved: list[UnresolvedInstance] = []
    top_id = _elaborate_instance_body(selected.body, definitions, compilation, unresolved)
    if unresolved:
        # Shouldn't be reachable: the error-diagnostics check above already
        # guarantees no UnknownModule (or other) errors exist before we ever
        # start walking the tree. Asserting rather than silently accepting a
        # Design with instances missing from their owning Definition.
        raise AssertionError(f"unexpected unresolved instances in strict elaborate(): {unresolved}")
    return Design(definitions=definitions, top=top_id)


def _install_include_dirs(include_dirs: list[str] | None) -> None:
    """`+incdir+` directories, registered on pyslang's own default
    SourceManager -- which is what `SyntaxTree.fromFile`/`fromFiles`
    use when no manager is passed, so registering there is what makes
    `` `include `` resolve against them."""
    if not include_dirs:
        return
    manager = SyntaxTree.getDefaultSourceManager()
    for directory in include_dirs:
        manager.addUserDirectories(directory)


def elaborate_allowing_unresolved(
    files: list[str],
    top: str | None = None,
    defines: dict[str, str | None] | None = None,
    include_dirs: list[str] | None = None,
) -> tuple[Design, list[UnresolvedInstance]]:
    """Like elaborate(), but tolerates UnknownModule diagnostics instead of
    raising on them -- used by the mixed-language orchestrator (1d) to
    discover cross-HDL references. Any other error diagnostic still raises;
    this only relaxes the one specific, expected-in-a-mixed-design case.

    Instances referencing an unresolved module are omitted from their
    owning Definition's `instances` list in the returned Design (there is
    nothing valid to point `definition_id` at yet) and are reported in the
    second return value instead, for the caller to resolve and splice back in.
    """
    compilation = _make_compilation(top)
    _install_include_dirs(include_dirs)
    _add_sources(compilation, files, defines)

    other_errors = [d for d in compilation.getAllDiagnostics() if d.isError() and d.code != Diags.UnknownModule]
    if other_errors:
        message, file, line = _format_diagnostics(compilation, other_errors)
        raise ElaborationError(message, file, line)

    selected = _select_top_instance(compilation, top)

    definitions: dict[str, Definition] = {}
    unresolved: list[UnresolvedInstance] = []
    top_id = _elaborate_instance_body(selected.body, definitions, compilation, unresolved)
    return Design(definitions=definitions, top=top_id), unresolved


def _select_top_instance(compilation: Compilation, top: str | None):
    top_instances = compilation.getRoot().topInstances
    if top is None:
        if len(top_instances) != 1:
            names = [t.name for t in top_instances]
            raise ValueError(f"expected exactly one top-level instance, found {names}")
        return top_instances[0]

    matches = [t for t in top_instances if t.name == top]
    if len(matches) != 1:
        names = [t.name for t in top_instances]
        raise ValueError(f"top '{top}' not found among top-level instances: {names}")
    return matches[0]


def list_module_names(files: list[str]) -> set[str]:
    """Syntax-only (no Compilation, no elaboration) scan for top-level module
    declaration names -- cheap enough to use just for "is `top` defined in
    this file set" checks during mixed-language top-detection, without
    paying for (or risking failure of) a real elaboration pass. Reuses
    pyslang's own parser rather than a hand-rolled regex, for the same
    robustness-over-duplication reasons the VHDL entity scanner is a text
    scan of last resort, not a first choice, where a real parser is available.
    """
    names: set[str] = set()
    for f in files:
        tree = SyntaxTree.fromFile(f)

        def collect(node):
            if node.kind == SyntaxKind.ModuleDeclaration:
                names.add(node.header.name.value)

        tree.root.visit(collect)
    return names


def _collect_interface_instance_identities(scope) -> list[tuple[str, object]]:
    """Every LOCALLY-declared interface instance directly in `scope` (or,
    recursing, in the live content of a nested generate block) -- e.g.
    `sram_if bus (.clk(clk), ...);` declared directly inside a module
    body, as opposed to an interface-typed PORT (collected separately in
    _elaborate_instance_body via `interface_port_symbols`). See that
    function's own `interface_identities` comment for the full "why".

    Read-only, mirrors _walk_body_members()'s own generate-block descent
    (only the live branch of a `generate if`, every live iteration of a
    `generate for`) but doesn't build any IR -- collected once, upfront,
    the same "gather identities before the real per-member walk needs
    them" precedent `interface_port_symbols` (from body.portList) already
    establishes. An arrayed interface instance (`sram_if bus [N] (...);`)
    is walked the same way an arrayed *module* instance is elsewhere in
    this file, but a real design driving one *element* of an interface
    array via a per-element indexed assign hasn't been seen yet -- kept
    simple (whole-array elements skipped, not guessed at) rather than
    inventing indexed-name handling for an unconfirmed case.
    """
    identities: list[tuple[str, object]] = []
    for member in scope:
        if isinstance(member, InstanceSymbol):
            if member.body.definition.definitionKind == DefinitionKind.Interface:
                identities.append((member.name, member.body))
        elif isinstance(member, GenerateBlockSymbol):
            if not member.isUninstantiated:
                identities.extend(_collect_interface_instance_identities(member))
        elif isinstance(member, GenerateBlockArraySymbol):
            for entry in member.entries:
                if not entry.isUninstantiated:
                    identities.extend(_collect_interface_instance_identities(entry))
    return identities


def _elaborate_instance_body(
    body, definitions: dict[str, Definition], compilation: Compilation, unresolved: list[UnresolvedInstance]
) -> str:
    base_name = body.definition.name
    parameters = {p.name: str(p.value) for p in body.parameters}
    definition_id = make_definition_id(base_name, parameters)

    if definition_id in definitions:
        return definition_id  # already elaborated this (module, parameter-binding) pair

    source_file = compilation.sourceManager.getFileName(body.location)
    language = SourceLanguage.SYSTEMVERILOG if source_file.endswith(".sv") else SourceLanguage.VERILOG
    is_interface = body.definition.definitionKind == DefinitionKind.Interface

    ports: list[Port] = []
    interface_port_symbols: list[InterfacePortSymbol] = []
    for p in body.portList:
        if isinstance(p, InterfacePortSymbol):
            interface_port_symbols.append(p)
        else:
            ports.append(_build_port(p))
    port_names = {p.name for p in ports}

    # Every interface identity a plain-signal reference elsewhere in this
    # body could be rooted in -- both this Definition's own interface-typed
    # PORTS (`sram_if.mem bus` as a port) and every LOCALLY-declared
    # interface instance (`sram_if bus (.clk(clk), ...);` -- a real,
    # confirmed, separate case, found via real `aura` design use:
    # `mem_subsys.sv`'s own `bus`/`icache.sv`'s own `tag_bus`/`data_bus`
    # are declared exactly this way, then driven via plain
    # `assign bus.req = sel & req;`-style continuous assigns -- the same
    # "interface-member LHS" shape as a port-rooted reference, but with no
    # InterfacePortSymbol.connection to match identity against, since
    # there's no port here at all). See _resolve_connected_name()'s own
    # docstring for how each `(name, instance_body)` pair here is matched
    # against a real reference's own resolved symbol.
    interface_identities: list[tuple[str, object]] = [
        (p.name, p.connection[0].body) for p in interface_port_symbols if getattr(p.connection[0], "body", None) is not None
    ]
    interface_identities.extend(_collect_interface_instance_identities(body))

    instances: list[Instance] = []
    nets: list[Net] = []
    sequential_elements: list[SequentialElement] = []
    modports: list[Modport] = []
    continuous_assigns: list[ContinuousAssign] = []
    # Raw (bound interface instance symbol, modport name) pairs from every
    # interface-typed connection anywhere in this body (direct or nested in
    # a generate block) -- fed into _infer_forwarded_modport below, once
    # every child instance has actually been walked.
    iface_connections: list[tuple[object, str | None]] = []
    _walk_body_members(
        body,
        definitions,
        compilation,
        unresolved,
        definition_id,
        port_names,
        instances,
        nets,
        sequential_elements,
        modports,
        iface_connections,
        continuous_assigns,
        interface_identities,
    )
    interface_ports = [
        _build_interface_port(p, inferred_modport=_infer_forwarded_modport(p, iface_connections) if not p.modport else None)
        for p in interface_port_symbols
    ]

    # Insert before recursing would allow self-instantiation loops to explode;
    # not a real RTL case, so simple post-recursion insertion is fine here.
    definitions[definition_id] = Definition(
        definition_id=definition_id,
        base_name=base_name,
        language=language,
        source_file=source_file,
        parameters=parameters,
        is_interface=is_interface,
        ports=ports,
        nets=nets,
        instances=instances,
        sequential_elements=sequential_elements,
        interface_ports=interface_ports,
        modports=modports,
        continuous_assigns=continuous_assigns,
    )
    return definition_id


def _walk_body_members(
    scope,
    definitions: dict[str, Definition],
    compilation: Compilation,
    unresolved: list[UnresolvedInstance],
    definition_id: str,
    port_names: set[str],
    instances: list[Instance],
    nets: list[Net],
    sequential_elements: list[SequentialElement],
    modports: list[Modport],
    iface_connections: list[tuple[object, str | None]],
    continuous_assigns: list[ContinuousAssign],
    interface_identities: list[tuple[str, object]],
    name_prefix: str = "",
    genvar_values: dict[str, int] | None = None,
) -> None:
    """Walks one scope's members (a module/interface body, or -- recursing
    -- the *live* content of a generate block found inside one), mutating
    the accumulator lists in place. Split out from _elaborate_instance_body
    so the exact same dispatch applies uniformly whether a member is
    declared directly in a module body or nested inside `generate if`/
    `generate for` -- previously only the direct case was handled at all,
    so *everything* declared inside a generate block (instances, nets,
    sequential elements) was silently invisible to elaboration, not a
    partial miss. Found via a real design where a `generate if/else if`
    selecting between two hard-macro configurations meant the selected
    macro instance never appeared in the IR at all.

    `GenerateBlockSymbol` (`generate if`/`case`) contributes its content
    only when `not member.isUninstantiated` -- the *other* branches also
    appear as sibling GenerateBlockSymbols, but pyslang marks exactly the
    one branch actually taken as instantiated; the rest are skipped
    entirely, not walked as if empty. Its content is named
    `"{block_name}."`-prefixed, like a `generate for` entry's: the block
    is a real hierarchy level (`u_wrap/g_1024x32.u_macro`), named by its
    label or, unlabelled, by its implicit `genblk<N>`.

    `GenerateBlockArraySymbol` (`generate for`) contributes each live
    `.entries[i]` the same way, but instance names inside are prefixed
    with `"{array_name}[{constructIndex}]."` first -- unlike a single
    `generate if`, different loop iterations can genuinely declare an
    instance with the same literal name, so without disambiguation later
    iterations would silently collide with (and in this dict-keyed
    representation, overwrite) earlier ones.

    `interface_identities` -- every interface identity a plain-signal
    reference in this scope could be rooted in (this Definition's OWN
    interface ports AND every locally-declared interface instance;
    collected once in _elaborate_instance_body, before this function is
    first called, and threaded through every recursive call unchanged) --
    is passed down to _build_instance()/_build_continuous_assign() so a
    plain-signal connection/target rooted in one of them (e.g. `bus.clk`,
    `~bus.req`) can be correctly qualified. See _resolve_connected_name()'s
    own docstring for the full "why".

    Bug fix (found via real `generate`-block validation: a `for`-generate
    instantiating one macro per iteration, each iteration ALSO declaring
    its own local net + continuous assign feeding that macro, e.g.
    `logic bank_cen_b; assign bank_cen_b = cen_b | (selected_bank != idx);`
    inside `begin : g_bank`): unlike instances, which already get
    `name_prefix` applied to their own name (see the `GenerateBlockArraySymbol`
    docstring above), a NET/VARIABLE declared directly inside a `generate
    for` iteration was extracted under its own bare local name with NO
    prefix at all -- every iteration's own genuinely distinct local net
    (real SV: `g_bank[0].bank_cen_b` and `g_bank[1].bank_cen_b` are
    different signals) collapsed into multiple IDENTICALLY-named `Net`/
    `ContinuousAssign` entries in this one flat Definition, indistinguishable
    from one another. Worse than merely losing information: `flatten.py`'s
    own exact-string-match driver search then silently picked ONE of the
    (accidentally identical) assign targets for every replica's own query,
    reporting a single confidently-RESOLVED driver identical across every
    generate iteration -- a wrong answer, not a visible gap.

    Fixed by prefixing every net declared as a DIRECT member of this scope
    (`local_net_names`, collected once at the top -- membership in `scope`
    itself already proves local declaration, no further check needed) the
    same way an instance already is, and prefixing a continuous assign's
    own TARGET only when it resolves (after any constant-index suffix is
    stripped) to one of those same locally-declared names -- an assign
    targeting an OUTER-scope signal via a constant-indexed reference (e.g.
    `assign clk_dom[d] = ...;`, already correctly resolved to
    "clk_dom[<value>]" by `_resolve_connected_name()`) must NOT be
    prefixed, since that target genuinely lives in an ancestor scope, not
    this one -- prefixing it would fabricate a hierarchy path nothing
    actually occupies. `referenced_signals` (used only for pass-through-
    vs-real-logic classification, never for endpoint identity) is
    deliberately left unprefixed -- out of scope for this fix, see
    ContinuousAssign's own docstring for what that field is actually for.
    """
    members = list(scope)
    local_net_names = {m.name for m in members if isinstance(m, (NetSymbol, VariableSymbol)) and m.name not in port_names}
    genvar_values = genvar_values or {}
    for member in members:
        if isinstance(member, InstanceSymbol):
            instances.append(
                _prefix_local_connections(
                    _build_instance(
                        member,
                        definitions,
                        compilation,
                        unresolved,
                        interface_identities,
                        name=f"{name_prefix}{member.name}",
                        iface_connections_out=iface_connections,
                    ),
                    name_prefix,
                    local_net_names,
                )
            )
        elif isinstance(member, InstanceArraySymbol):
            # `some_if bus [N] (...)` -- pyslang expands this into N real
            # InstanceSymbol elements (each with its own empty .name; the
            # real identity is positional) under one InstanceArraySymbol
            # container, a different type the plain InstanceSymbol check
            # above doesn't match -- confirmed via a real design that hit
            # this (arrayed interface instances), not assumed. Not
            # interface-specific: an arrayed *module* instance would hit
            # this exact same path. Naming ("base[i]") matches how
            # PortConnection.connected_to below names a connection into
            # one specific array element.
            for i, element in enumerate(member.elements):
                instances.append(
                    _prefix_local_connections(
                        _build_instance(
                            element,
                            definitions,
                            compilation,
                            unresolved,
                            interface_identities,
                            name=f"{name_prefix}{member.name}[{i}]",
                            iface_connections_out=iface_connections,
                        ),
                        name_prefix,
                        local_net_names,
                    )
                )
        elif isinstance(member, GenerateBlockSymbol):
            if not member.isUninstantiated:
                # The block IS a level of the hierarchy (IEEE 1800 27.6),
                # exactly like a `generate for` entry: everything inside
                # `if (...) begin : g_1024x32` is `g_1024x32.<name>`, which
                # is how a UPF (and every EDA tool) has to address it --
                # `u_wrap/g_1024x32.u_macro`, not `u_wrap/u_macro`. It
                # used to recurse with the PARENT's prefix, silently
                # dropping the block name. `externalName` is the LRM name:
                # the label for a named block, the implicit `genblk<N>`
                # for an unnamed one (pyslang's own hierarchicalPath
                # agrees).
                _walk_body_members(
                    member,
                    definitions,
                    compilation,
                    unresolved,
                    definition_id,
                    port_names,
                    instances,
                    nets,
                    sequential_elements,
                    modports,
                    iface_connections,
                    continuous_assigns,
                    interface_identities,
                    f"{name_prefix}{member.externalName}.",
                    genvar_values,
                )
        elif isinstance(member, GenerateBlockArraySymbol):
            for entry in member.entries:
                if entry.isUninstantiated:
                    continue
                entry_prefix = f"{name_prefix}{member.name}[{entry.constructIndex}]."
                # Bug fix (found via real `generate`-block validation): a
                # black-box (.lib) instance's own connection to a bit-
                # indexed element of a wider signal, keyed off this SAME
                # loop's own genvar (e.g. `.Q(bank_q[bank_idx])`), can't
                # be constant-folded from `_extract_uninstantiated_connections()`'s
                # own raw-syntax-text view alone -- the genvar's NAME is
                # all the source text carries, not its per-iteration
                # VALUE. `entry`'s own direct members already include a
                # `ParameterSymbol` for every genvar live at this exact
                # iteration (pyslang represents a genvar as one -- `bank_idx
                # = 2`, already resolved to this iteration's own constant),
                # so collecting those here and merging them into whatever
                # was already visible from an ENCLOSING generate-for (a
                # nested loop can reference either its own genvar or an
                # outer one) gives `_extract_uninstantiated_connections()`
                # everything it needs to resolve the SAME element-select
                # pattern `_resolve_connected_name()` already handles for
                # an ordinary (non-black-box) instance.
                entry_genvar_values = dict(genvar_values)
                for m in entry:
                    if not isinstance(m, ParameterSymbol):
                        continue
                    value = _genvar_int_value(m)
                    if value is not None:
                        entry_genvar_values[m.name] = value
                _walk_body_members(
                    entry,
                    definitions,
                    compilation,
                    unresolved,
                    definition_id,
                    port_names,
                    instances,
                    nets,
                    sequential_elements,
                    modports,
                    iface_connections,
                    continuous_assigns,
                    interface_identities,
                    entry_prefix,
                    entry_genvar_values,
                )
        elif isinstance(member, ModportSymbol):
            modports.append(_build_modport(member))
        elif isinstance(member, UninstantiatedDefSymbol):
            # A cross-HDL (or genuinely undefined) reference pyslang couldn't
            # resolve. Recorded for the caller instead of being silently
            # dropped or crashing here -- only elaborate_allowing_unresolved()
            # can actually reach this branch; strict elaborate() already
            # raised on the UnknownModule diagnostic before recursing this far.
            if member.paramExpressions:
                # v1 mixed-language scope boundary, not a general limitation
                # of this extraction: a cross-language reference is only
                # resolved at its default configuration (no #(...) override
                # support yet), matching the "single-direction, unparametrized
                # leaf" cut agreed for 1d's first pass. Fail loud rather than
                # silently resolve against the wrong (default) generics.
                raise NotImplementedError(
                    f"instance '{member.name}' of unresolved module '{member.definitionName}' has a parameter "
                    "override (#(...)) -- cross-language references with non-default parameters/generics are "
                    "not yet supported by the mixed-language orchestrator"
                )
            unresolved.append(
                UnresolvedInstance(
                    owner_definition_id=definition_id,
                    name=f"{name_prefix}{member.name}",
                    referenced_definition_name=member.definitionName,
                    connections=_prefix_local_connections_dict(
                        _extract_uninstantiated_connections(member, genvar_values), name_prefix, local_net_names
                    ),
                )
            )
        elif isinstance(member, ProceduralBlockSymbol):
            sequential_elements.extend(_extract_sequential_elements(member, interface_identities))
        elif isinstance(member, ContinuousAssignSymbol):
            # `assign target = ...;` -- see ContinuousAssign's own
            # docstring for the full "why" (connectivity-flattening's
            # pass-through-vs-real-logic distinction). `_build_continuous_assign()`
            # returns None (nothing appended) for the rare LHS shape it
            # doesn't model (a concatenation/bit-select target, e.g.
            # `assign {a, b} = c;` or `assign y[3:0] = a;` -- not a bare
            # single-signal or interface-member target) -- a deliberate,
            # explicit scope cut, not a silent bug: such an assign is
            # unambiguously NOT a simple whole-signal pass-through anyway,
            # and IS still visible to a flattening trace via that signal's
            # OWN net entry/other drivers, just not represented as its own
            # ContinuousAssign record here. An interface-member target
            # (`assign bus.rdata = q_macro;`) IS modeled, via the same
            # _resolve_connected_name() qualification _build_instance()
            # uses -- see that function's own docstring.
            built = _build_continuous_assign(member, interface_identities)
            if built is not None:
                if name_prefix:
                    target_base = _strip_constant_index_suffix(built.target)
                    if target_base in local_net_names:
                        built = replace(built, target=f"{name_prefix}{built.target}")
                continuous_assigns.append(built)
        elif isinstance(member, (NetSymbol, VariableSymbol)) and member.name not in port_names:
            # Every port also shows up here as its own backing NetSymbol/VariableSymbol
            # (e.g. "clk" appears as both a PortSymbol and a NetSymbol) -- the
            # port_names filter keeps those out so they aren't double-counted
            # as internal nets. Prefixed with `name_prefix` -- see this
            # function's own docstring's "Bug fix" paragraph for the full
            # "why" (a net declared inside a `generate for` iteration is
            # genuinely distinct per iteration, and must be named that way).
            nets.append(_build_net(member, name_prefix))


def _bit_range(t) -> BitRange | None:
    # .canonicalType resolves through any `typedef`-alias chain (e.g. a
    # package's `typedef logic [7:0] byte_t;`) to the real underlying
    # type -- a bare TypeAliasType has no .range of its own even when
    # isPackedArray is true, confirmed via a real design (a typedef'd
    # internal signal inside an interface, previously unreached because
    # elaboration bailed out on the interface guard before getting this
    # deep) crashing with AttributeError, not a hypothetical.
    canonical = t.canonicalType
    return BitRange(canonical.range.left, canonical.range.right) if canonical.isPackedArray else None


def _build_port(port_symbol) -> Port:
    return Port(
        name=port_symbol.name,
        direction=_DIRECTION_MAP[port_symbol.direction],
        bit_range=_bit_range(port_symbol.type),
    )


def _build_interface_port(port_symbol: InterfacePortSymbol, inferred_modport: str | None = None) -> InterfacePort:
    """`axi_lite_if.mst dbus` -- see InterfacePort's own docstring for why
    this carries the interface's *base name*, not a definition_id: the
    concrete, parameter-resolved interface instance actually bound here is
    only known per-instantiation (Instance.connections), same as a plain
    Port never carries its own connection either."""
    declared_range = port_symbol.declaredRange
    array_size = abs(declared_range[0].left - declared_range[0].right) + 1 if declared_range else None
    return InterfacePort(
        name=port_symbol.name,
        interface_name=port_symbol.interfaceDef.name,
        modport=port_symbol.modport or None,  # pyslang reports '' for a genuinely unconstrained interface port
        inferred_modport=inferred_modport,
        array_size=array_size,
    )


def _infer_forwarded_modport(port_symbol: InterfacePortSymbol, child_iface_connections: list[tuple[object, str | None]]) -> str | None:
    """See InterfacePort.inferred_modport's own docstring for the full
    reasoning. `port_symbol.connection` gives `(inst_sym, modport_sym)` --
    the same concrete interface instance identity pyslang resolves this
    port to, *even when there's no real parent yet* (an elaboration-root
    module's own unconstrained interface port still resolves to a
    synthesized default instance, confirmed via a live pyslang spike) --
    which is exactly the same identity a child instance's own interface
    connection resolves to when that child is wired straight to this same
    port via pure pass-through (`adapter u_adapter (.mem_port(mem_port))`).
    Matching on that shared identity is what lets this work without
    needing to know how *this* module itself gets instantiated.

    Only returns a modport when every matching child agrees -- two
    children forwarding the same port but requiring two different
    modports is a genuinely ambiguous case, not guessed at.
    """
    own_inst_sym, _own_modport_sym = port_symbol.connection
    if own_inst_sym is None:
        return None
    candidates = {modport_name for inst_sym, modport_name in child_iface_connections if inst_sym is own_inst_sym and modport_name is not None}
    return next(iter(candidates)) if len(candidates) == 1 else None


def _build_modport(modport_symbol: ModportSymbol) -> Modport:
    # Non-ModportPortSymbol members (e.g. ModportClockingSymbol) are
    # skipped, not raised on -- no design encountered so far uses modport
    # clocking blocks; extend this the same way other "recognized subset"
    # extractions elsewhere in this module do, if/when a real need shows up.
    ports = [ModportPort(name=p.name, direction=_DIRECTION_MAP[p.direction]) for p in modport_symbol if isinstance(p, ModportPortSymbol)]
    return Modport(name=modport_symbol.name, ports=ports)


def _build_net(symbol, name_prefix: str = "") -> Net:
    return Net(name=f"{name_prefix}{symbol.name}", bit_range=_bit_range(symbol.type))


def _strip_constant_index_suffix(name: str) -> str:
    """"bank_cen_b[3]" -> "bank_cen_b"; a name with no "[...]" suffix ->
    itself, unchanged. Local counterpart of flatten.py's own
    `_strip_constant_index()` (that one returns None for "nothing to
    strip" -- callers here always want a usable base name to check
    against `local_net_names` either way, indexed or not, so this one
    just returns the name itself in that case instead)."""
    if name.endswith("]") and "[" in name:
        return name[: name.index("[")]
    return name


def _build_continuous_assign(symbol: ContinuousAssignSymbol, interface_identities: list[tuple[str, object]]) -> ContinuousAssign | None:
    """`source` is set only for TRUE pass-through wiring -- verified live
    against real pyslang output (not assumed): a bare `assign y = a;`
    gives `.assignment.right` as a `NamedValueExpression` (kind
    NamedValue); `~a`/`sel?a:b`/`a&b`/a bit-select all give a distinct
    ExpressionKind (UnaryOp/ConditionalOp/BinaryOp/RangeSelect/...),
    naturally excluded by the `== NamedValue` check below with no extra
    per-kind handling needed.

    A same-width signedness-only (or 2-state/4-state) reinterpretation
    still wraps the RHS in exactly one `ConversionExpression` even
    though nothing about the actual VALUE changes (confirmed live) --
    unwrapped once here so that case still counts as pass-through. A
    genuine width-changing conversion (confirmed live: e.g. a 4-bit
    signal assigned into an 8-bit target) ALSO produces a
    ConversionExpression, but with `.operand.type.bitWidth` different
    from the target's own width -- the explicit width-equality check
    below (comparing `left`'s width against the UNWRAPPED operand's
    own width, not the outer conversion's post-conversion width, which
    trivially always matches `left` by construction) is what tells
    these two apart, per the explicit "never trace through a width
    mismatch" requirement.

    Returns None (nothing to append) when the LHS itself isn't a bare
    single-signal reference, an interface-member reference, or a
    constant-indexed single-bit reference (e.g. `assign {a, b} = c;`,
    `assign y[3:0] = a;` -- a bit RANGE, not a single constant element --
    stay an explicit, logged scope cut, not a silent gap; see this
    function's own call site for why). An interface-member LHS (`assign
    bus.rdata = q_macro;`, ExpressionKind.HierarchicalValue -- real,
    confirmed gap found via real `aura` design use: this assign was
    previously silently dropped entirely, severing an internal net's only
    link out to its owning interface port, e.g. `q_macro`'s own forward
    trace dead-ending at FLOATING instead of reaching `bus.rdata` and
    beyond) is resolved via the same _resolve_connected_name()
    qualification _build_instance() uses for a plain port connection
    rooted in an interface member. A constant-indexed single-bit LHS
    (`assign clk_dom[d] = clk_ref & en_latch;` inside a `generate for`
    loop, ExpressionKind.ElementSelect, `d` a real per-iteration
    compile-time constant -- real, confirmed gap found via real `aura`
    design use, `clk_rst_gen`'s own per-domain clock gating, previously
    silently dropped entirely the same way an interface-member LHS was)
    is ALSO resolved via _resolve_connected_name(), which appends the
    resolved constant index as a "[N]" suffix -- see that function's own
    docstring for the full mechanism."""
    assign_expr = symbol.assignment
    left = assign_expr.left
    if left.kind not in (ExpressionKind.NamedValue, ExpressionKind.HierarchicalValue, ExpressionKind.ElementSelect):
        return None
    target = _resolve_connected_name(left, interface_identities)
    if target is None:
        return None
    referenced_signals = _collect_referenced_signals(assign_expr.right, interface_identities)

    right = assign_expr.right
    if right.kind == ExpressionKind.Conversion:
        right = right.operand
    # `assign rdata = bus.rdata;` -- an interface-member RHS -- is
    # ExpressionKind.HierarchicalValue, not NamedValue (real, confirmed
    # gap found via real `aura` design use, `sram_bank`'s own
    # `rdata`/`rvalid` pass-throughs from its local `bus` instance):
    # `right.symbol.name` alone would give the bare, unqualified "rdata"
    # (matching no real net anywhere) the same way an un-fixed LHS did,
    # so this goes through the same _qualified_symbol_name() qualification
    # the LHS side already uses.
    if right.kind in (ExpressionKind.NamedValue, ExpressionKind.HierarchicalValue) and right.type.bitWidth == left.type.bitWidth:
        source = _qualified_symbol_name(right.symbol, interface_identities)
        return ContinuousAssign(target=target, source=source, referenced_signals=referenced_signals)
    return ContinuousAssign(target=target, source=None, referenced_signals=referenced_signals)


def _collect_referenced_signals(expr, interface_identities: list[tuple[str, object]]) -> tuple[str, ...]:
    """Every distinct signal name referenced anywhere in `expr` -- see
    ContinuousAssign.referenced_signals's own docstring for the full
    "why" (a signal feeding INTO a real-logic RHS as one of possibly
    several operands, e.g. `sleep_int` in `assign o_sleep = ~sleep_int;`,
    was previously invisible to flatten.py's own trace_receivers() --
    only the special bare-single-signal pass-through case was ever
    captured at all). `Expression.visit()` recurses through the WHOLE
    expression tree, including through a ConversionExpression's own
    operand -- confirmed live, so this needs no manual unwrapping the
    way the pass-through check above does. Also collects HierarchicalValue
    (interface-member) references, qualified via _qualified_symbol_name()
    the same way a top-level LHS/RHS reference already is -- real,
    confirmed gap found via real `aura` design use: an interface member
    referenced mid-expression (not just as a whole top-level RHS) was
    previously invisible here entirely, same root cause as the LHS/RHS
    gaps this whole module's own `_resolve_connected_name()` fixes."""
    refs: list[str] = []
    seen: set[str] = set()

    def collect(e) -> None:
        kind = getattr(e, "kind", None)
        if kind not in (ExpressionKind.NamedValue, ExpressionKind.HierarchicalValue):
            return
        name = _qualified_symbol_name(e.symbol, interface_identities)
        if name not in seen:
            seen.add(name)
            refs.append(name)

    expr.visit(collect)
    return tuple(refs)


def _interface_connection_name(inst_sym) -> str | None:
    """Names the bound interface instance the same way an arrayed
    instance's own elements get named in _elaborate_instance_body's
    InstanceArraySymbol branch ("base[i]"), so a PortConnection.connected_to
    value always matches a real Instance.name in the parent Definition --
    confirmed via a real design where an interface port is bound straight
    to one element of an interface array (pyslang reports the bound
    instance's own .name as empty in that case; .arrayName/.arrayPath give
    the real identity instead)."""
    if inst_sym.name:
        return inst_sym.name
    if inst_sym.arrayName:
        return f"{inst_sym.arrayName}[{','.join(str(i) for i in inst_sym.arrayPath)}]"
    return None


def _local_interface_identity_name(raw_inst_sym, interface_identities: list[tuple[str, object]]) -> str | None:
    """See `_build_instance()`'s own call-site comment for the full "why"
    -- matches `raw_inst_sym.body` (an InstanceBody, same identity shape
    `_qualified_symbol_name()` already matches against for a modport
    member reference) against each candidate by IDENTITY, not name.
    Returns None (defer to the raw pyslang-resolved name) when
    `raw_inst_sym` has no `.body` (e.g. already an interface INSTANCE
    itself rather than a wrapping Instance symbol -- shouldn't normally
    happen here, but not assumed) or no candidate matches."""
    body = getattr(raw_inst_sym, "body", None)
    if body is None:
        return None
    for name, instance_body in interface_identities:
        if instance_body is body:
            return name
    return None


def _interface_connections_for_port(port_name: str, inst_sym, modport_sym) -> list[tuple[str, str | None, object, str | None]]:
    """Real, confirmed bug (found via real `aura` design use, a whole-
    ARRAY-to-whole-ARRAY interface port bind -- `axi_lite_if mst_port
    [N_M]` connected as `.mst_port(axi_mst)`, both sides full arrays):
    for this shape, pc.ifaceConn's own `inst_sym` is itself an
    InstanceArraySymbol, but a SYNTHESIZED one whose own .name/.arrayName
    echo the PORT's own identity ("mst_port"), not the real bound
    instance's ("axi_mst") -- confirmed live against real aura RTL.
    The real per-element bound instances (with correct .arrayName/
    .arrayPath, e.g. "axi_mst"/[0]) only show up one level deeper, in
    `.elements`.

    Returns one `(instance_port, connected_to, raw_inst_sym, modport_name)`
    tuple per array element for this whole-array case (instance_port
    disambiguated "port_name[i]", matching how an arrayed *instance's*
    own elements are already named elsewhere in this file) -- or exactly
    one tuple, unindexed, for every other (non-whole-array) shape.
    Never guesses at a single representative connection for the
    whole-array case -- every element is reported, so callers that
    need honest multiplicity (flatten.py's own interface-aware tracing)
    can see and merge across all of them, same "never silently pick
    one" discipline already established for MULTIPLE_DRIVERS."""
    if isinstance(inst_sym, InstanceArraySymbol):
        return [
            (f"{port_name}[{i}]", _interface_connection_name(element), element, modport_sym.name if modport_sym else None)
            for i, element in enumerate(inst_sym.elements)
        ]
    return [(port_name, _interface_connection_name(inst_sym), inst_sym, modport_sym.name if modport_sym else None)]


def _resolve_connected_name(expr, interface_identities: list[tuple[str, object]]) -> str | None:
    """Resolves a plain (non-interface-typed) port-connection expression,
    or a continuous-assign LHS, to the name PortConnection.connected_to /
    ContinuousAssign.target should record. Fixes two real, confirmed gaps
    found via real `aura` design use, both stemming from the same root
    cause (neither had any handling for a `MemberAccessExpression`/
    hierarchical-value interface member -- only for a bare
    `NamedValueExpression`):
      1. `sram_1rw_wrap`'s own `bus` SV interface PORT is its PRIMARY
         connectivity mechanism -- CLK/CEN/WEN/BEN/A/D/RETN/PDN all route
         through it. Every one of CLK/CEN/WEN/BEN/A/D previously resolved
         wrong (CEN/WEN/BEN to `None` i.e. "unconnected"; CLK/D/A to a
         bare, unqualified member name matching no real net/port
         anywhere), dead-ending the Driver/Receiver Supply Set trace at
         UNDRIVEN/FLOATING for a macro that's actually fully wired.
      2. One level up, `mem_subsys.sv`'s own `bus` (and `icache.sv`'s own
         `tag_bus`/`data_bus`) are LOCALLY-declared interface instances
         (`sram_if bus (.clk(clk), ...);`), not ports -- driven via plain
         `assign bus.req = sel & req;`-style continuous assigns. The
         SAME "interface-member LHS" shape as (1), but with no
         `InterfacePortSymbol.connection` to match identity against,
         since there's no port here at all -- fixing only (1) still left
         the trace dead-ending one hop further up, at `bus.req` itself,
         for the exact same real design.

    Handles, confirmed live via a pyslang spike against this exact design:
      - a bare local reference (NamedValueExpression) -> that symbol's own
        name, same as before.
      - `~x` (UnaryExpression) -> the operand's own name. Polarity doesn't
        change WHICH signal this pin is connected to for supply-domain
        tracing purposes -- an inline inverter isn't a power-domain
        boundary (isolation/level-shifter cells are explicit UPF strategy
        constructs elsewhere in this tool, never inferred from RTL gates).
        Bare `expr.getSymbolReference()` returns None for a bare
        UnaryExpression (confirmed live) -- previously silently dropping
        the whole connection as "unconnected" with no unwrapping at all.
      - a bit RANGE-select of either of the above -> pyslang's own
        getSymbolReference() already unwraps these to the base symbol with
        no extra handling needed (confirmed live: `bus.addr[9:0]` resolves
        straight to the `addr` ModportPort symbol).
      - a single-bit ELEMENT-select with a COMPILE-TIME-CONSTANT index of
        a plain (non-interface) VECTOR signal (`clk_dom[DOM_CPU]`, a
        `localparam` index -- real, confirmed gap found via real `aura`
        design use, `aura_top`'s own per-domain `clk_dom[N_DOMAIN-1:0]`
        fanout) -> unlike RangeSelect, `getSymbolReference()` on an
        ElementSelect ALSO auto-unwraps straight to the bare base symbol
        (confirmed live), silently discarding the index entirely and
        returning a name ("clk_dom") that means something different (the
        WHOLE vector) than the specific bit actually connected here.
        Walked explicitly BEFORE calling getSymbolReference() at all,
        appending a "[N]" suffix per ElementSelect layer (multiple layers
        for a multi-dimensional constant-indexed array, though the real
        gap found is a single dimension) -- constructed via
        `_constant_element_select_suffix()`. A NON-constant index (a
        genuine per-instance runtime index, not a `generate for`/
        `localparam` constant) can't be named as one specific bit --
        returns None rather than guessing at one.
      - any of the above rooted in a member of one of `interface_identities`
        (a HierarchicalValueExpression resolving to a `SymbolKind.
        ModportPort`, e.g. `bus.clk`/`~bus.req`) -> qualified as
        "ifaceName.memberName" (e.g. "bus.clk") so the existing
        interface-aware tracing (_trace_driver_interface_member, both here
        in flatten.py and in the frontend's own separately-duplicated
        mirror) recognizes the "." and redirects into the actually-bound
        interface instance, instead of looking for a plain net named
        "clk"/"wdata"/"addr" that doesn't exist in this Definition's own
        scope. The owning identity is found by matching
        `sym.parentScope.containingInstance` -- confirmed live to be the
        interface's own InstanceBody (SymbolKind.InstanceBody), a
        DIFFERENT symbol than an Instance symbol (SymbolKind.Instance)
        itself, hence every `interface_identities` entry already carries
        the resolved `.body`, not the raw Instance -- against each
        candidate by IDENTITY, not name -- the same identity-matching
        precedent _infer_forwarded_modport() already established
        elsewhere in this file, since two differently-named identities
        could share one interface type.

    Returns None when nothing resolvable is found (a genuinely complex
    connection, or an interface member whose owning identity couldn't be
    found) -- never guessed at, matching this module's existing
    "None = unconnected/unresolvable" discipline.

    Bug fix (found via real `generate`-block validation, a `for`-generate
    instantiating one leaf module per iteration with each iteration's own
    OUTPUT port connected to one constant-indexed element of a wider
    PARENT vector, e.g. `.q(data_o[bit_idx])`): pyslang represents an
    OUTPUT port's own connection expression wrapped in an
    `AssignmentExpression` (the instance's internal value being assigned
    OUT to the connected parent-side expression) -- confirmed live, this
    wrapping happens for every output connection, indexed or not. The
    ElementSelect-unwrapping loop below never recognized this wrapper, so
    `base.getSymbolReference()` was called on the bare AssignmentExpression
    itself, whose own auto-unwrap silently discarded the index the same
    way a bare ElementSelect's auto-unwrap already does (per this
    docstring's own next paragraph) -- but through an extra layer this
    function never accounted for, so even the OTHERWISE-already-fixed
    "walk ElementSelect layers before calling getSymbolReference()" logic
    below never got a chance to run for an indexed OUTPUT connection.
    Peeling to `.left` (the actual connected expression being assigned
    into, e.g. the ElementSelect for `data_o[bit_idx]`) before the
    existing walk restores the same handling this function already gives
    every INPUT-side indexed connection. A non-indexed output connection
    is unaffected either way (`.left` is then a bare NamedValueExpression,
    resolving identically to before).
    """
    base = expr.operand if isinstance(expr, UnaryExpression) else expr
    if base is None:
        return None
    if base.kind == ExpressionKind.Assignment:
        base = base.left
    suffix = ""
    while base is not None and base.kind == ExpressionKind.ElementSelect:
        index = _constant_index(base.selector)
        if index is None:
            return None
        suffix = f"[{index}]{suffix}"
        base = base.value
    if base is None:
        return None
    sym = base.getSymbolReference()
    if sym is None:
        return None
    return _qualified_symbol_name(sym, interface_identities) + suffix


def _constant_index(selector) -> int | None:
    """Extracts a plain Python int from a compile-time-constant
    ElementSelect index expression -- confirmed live via a real `aura`
    design (`clk_dom[DOM_CPU]`, `DOM_CPU` a `localparam`): `selector.
    constant` is a pyslang `ConstantValue`; `.value` is the underlying
    `SVInt`; `int(...)` on THAT gives a real Python int -- neither of the
    two outer layers converts to `int` directly (confirmed live: `int()`
    on the bare `ConstantValue` raises `TypeError`). Returns None for a
    non-constant selector (`selector.constant` itself is None for a
    genuine runtime/per-instance index) -- never guessed at."""
    constant = selector.constant
    if constant is None:
        return None
    try:
        return int(constant.value)
    except (TypeError, ValueError):
        return None


def _genvar_int_value(param: ParameterSymbol) -> int | None:
    """Extracts a plain Python int from a `ParameterSymbol` found as a
    direct member of a LIVE `generate for` entry -- pyslang represents a
    genvar this way, already resolved to this exact iteration's own
    constant value (confirmed live: `bank_idx = 0`/`1`/`2`/`3`, one
    `ParameterSymbol` per entry, for a 4-iteration loop). `.value` is a
    `ConstantValue` (same as `_constant_index()`'s own `selector.constant`
    above, a DIFFERENT attribute that happens to resolve to the same
    pyslang type); `.value.value` reaches the underlying `SVInt`, same
    two-step unwrap `_constant_index()` already needs -- confirmed live,
    `int(param.value)` alone raises `TypeError`, matching that function's
    own already-documented finding for the analogous case. Returns None
    for anything that doesn't cleanly convert (never guessed at) -- a
    non-constant/non-integral parameter isn't usable as a genvar-style
    index substitution anyway."""
    try:
        return int(param.value.value)
    except (TypeError, ValueError, AttributeError):
        return None


def _qualified_symbol_name(sym, interface_identities: list[tuple[str, object]]) -> str:
    """Shared tail of `_resolve_connected_name()` -- also used directly by
    `_collect_referenced_signals()`, which visits raw symbol references
    mid-expression-tree rather than a single top-level connection/LHS
    expression, but needs the exact same qualification.

    A member accessed through a MODPORT-qualified reference (e.g. via a
    `sram_if.mem bus` port) resolves to a `SymbolKind.ModportPort` -- but
    a member accessed on a plain, non-modport-qualified interface
    instance (`sram_if bus (...);`, no `.modport` at all -- confirmed
    live via real `aura` design use, `sram_bank`'s own local `bus`)
    resolves straight to the interface's own raw `SymbolKind.Variable`
    instead. Both shapes carry the SAME `parentScope.containingInstance`
    identity (the interface's own InstanceBody) -- checked uniformly here
    regardless of symbol kind, rather than gating on ModportPort
    specifically, which silently missed the plain-Variable case and left
    `bus.req` resolving to a bare, unqualified "req" (matching no real
    net in the *enclosing* scope) instead of "bus.req"."""
    parent_scope = getattr(sym, "parentScope", None)
    containing_instance = getattr(parent_scope, "containingInstance", None) if parent_scope is not None else None
    for name, instance_body in interface_identities:
        if containing_instance is instance_body:
            return f"{name}.{sym.name}"
    return sym.name


def _prefix_local_connections(instance: Instance, name_prefix: str, local_net_names: set[str]) -> Instance:
    """Companion to `_walk_body_members()`'s own net/continuous-assign
    prefixing (see that function's own "Bug fix" docstring paragraph) --
    an instance's own connection to a net LOCALLY declared in the SAME
    `generate for` iteration (e.g. `.CEN_B(bank_cen_b)`, `bank_cen_b`
    declared right there in `begin : g_bank`) still needs prefixing too,
    the exact same way the net's own declaration and any continuous
    assign targeting it already are -- otherwise the now-correctly-
    disambiguated net (`g_bank[2].bank_cen_b`) and the instance's own,
    still-bare connection to it (`bank_cen_b`) would simply stop matching
    each other at all (net_group()'s exact-string match), turning the
    prior silent-wrong-answer bug into a silent-UNDRIVEN one instead --
    still wrong, just differently so. A no-op when `name_prefix` is empty
    (the ordinary, non-generate-for case) or when a connection's own
    (index-stripped) base name isn't one of `local_net_names` -- an
    outer-scope reference must never be prefixed, see the caller's own
    docstring for why."""
    if not name_prefix:
        return instance
    new_connections = []
    changed = False
    for conn in instance.connections:
        if conn.connected_to is not None and _strip_constant_index_suffix(conn.connected_to) in local_net_names:
            new_connections.append(replace(conn, connected_to=f"{name_prefix}{conn.connected_to}"))
            changed = True
        else:
            new_connections.append(conn)
    return replace(instance, connections=new_connections) if changed else instance


def _build_instance(
    instance_symbol: InstanceSymbol,
    definitions: dict[str, Definition],
    compilation: Compilation,
    unresolved: list[UnresolvedInstance],
    interface_identities: list[tuple[str, object]],
    name: str | None = None,
    iface_connections_out: list[tuple[object, str | None]] | None = None,
) -> Instance:
    """`name` overrides instance_symbol.name -- needed for an element
    pulled out of an InstanceArraySymbol, whose own .name is empty (see
    _elaborate_instance_body's InstanceArraySymbol branch).

    `iface_connections_out`, when given, collects this instance's own raw
    `(bound interface instance symbol, modport name or None)` pairs for
    every interface-typed port connection -- fed back to the *enclosing*
    Definition's own forwarded-modport inference (see
    _infer_forwarded_modport); unrelated to `connections` below, which
    stores names for the IR, not raw pyslang symbols."""
    child_definition_id = _elaborate_instance_body(instance_symbol.body, definitions, compilation, unresolved)
    connections = []
    for pc in instance_symbol.portConnections:
        if isinstance(pc.port, InterfacePortSymbol):
            inst_sym, modport_sym = pc.ifaceConn
            for instance_port, connected_to, raw_inst_sym, modport_name in _interface_connections_for_port(pc.port.name, inst_sym, modport_sym):
                if iface_connections_out is not None:
                    iface_connections_out.append((raw_inst_sym, modport_name))
                # Real, confirmed multi-hop gap found via real `aura`
                # design use (`dsp_mem`'s own `.bus_a(wr_bus)`, where
                # `wr_bus` is ITSELF an interface PORT of `dsp_mem`, not a
                # concrete local instance -- `wr_bus` is bound, one level
                # further up, to a genuinely local instance `mem_wr` in
                # `dsp_mem`'s own PARENT, `dsp_subsys`): pyslang's own
                # `pc.ifaceConn` resolves straight through ANY number of
                # such interface-PORT-to-interface-PORT forwarding hops to
                # the ULTIMATE bound instance's identity ("mem_wr") --
                # confirmed live, `pc.expression.getSymbolReference()`
                # gives the exact same fully-resolved identity, no
                # "shallow" one-hop view exists anywhere in pyslang's own
                # API. Naively using that name as `connected_to` produces
                # a value ("mem_wr") that names something NOT reachable
                # from THIS Definition's own local scope at all (`dsp_mem`
                # has no local instance/port called "mem_wr") -- silently
                # fabricating a hierarchy path no real node exists at
                # downstream, once flatten.py tries to ascend one level at
                # a time using it. Fixed by preferring, when available,
                # the name `interface_identities` (this Definition's OWN
                # ports + local interface instances, see
                # _resolve_connected_name()'s own docstring) already knows
                # this SAME resolved identity by -- "wr_bus" here, dsp_mem's
                # own port, letting the *existing*, already-correct
                # one-hop-at-a-time ascension continue from there. Falls
                # back to the raw pyslang name when no local identity
                # matches (the ordinary, already-correct direct-bind case,
                # and the whole-array-bind case, whose own elements aren't
                # collected into `interface_identities` -- see
                # _collect_interface_instance_identities()'s own docstring)
                # -- never guessed at beyond that.
                local_name = _local_interface_identity_name(raw_inst_sym, interface_identities)
                connections.append(PortConnection(instance_port=instance_port, connected_to=local_name or connected_to))
        else:
            connected_to = _resolve_connected_name(pc.expression, interface_identities) if pc.expression is not None else None
            connections.append(PortConnection(instance_port=pc.port.name, connected_to=connected_to))
    return Instance(name=name if name is not None else instance_symbol.name, definition_id=child_definition_id, connections=connections)


def _extract_uninstantiated_connections(member: UninstantiatedDefSymbol, genvar_values: dict[str, int]) -> dict[str, str | None]:
    """Port map for an instance pyslang couldn't bind to a real module --
    the shape a black-box (.lib) macro instance always takes at THIS
    extraction stage (real .lib resolution happens later, in
    backend/elaborator/lib/elaborate.py -- this module has no concept of
    "black box" at all, only "pyslang couldn't bind this to a real
    module").

    Semantic expression resolution (`.expr.getSymbolReference()`) doesn't
    work here -- with no real port to type-check against, pyslang leaves the
    connection expression `Invalid`. The raw syntax is still there, though:
    walk `.syntax.connections` (NamedPortConnectionSyntax nodes) directly
    and read the connected signal off as plain text.

    Bug fix (found via real `generate`-block validation, a `for`-generate
    instantiating one black-box macro per iteration with an
    element-select or range-select connection, e.g. `.A(addr_i[7:0])`,
    `.Q(bank_q[bank_idx])`, `.POWER_GOOD(power_good_o[bank_idx])`):
    previously ONLY a bare identifier was ever accepted (`expr_text.
    isidentifier()`) -- ANY indexed/sliced connection, however simple,
    was silently dropped to None, unconditionally, regardless of whether
    the index was even a compile-time constant. Real, common RTL for a
    macro replicated over `BANKS`/`LANES`/etc., each wired to its own
    slice of a wider parent signal, was therefore losing EVERY such
    connection outright -- not ambiguously merged (the class of gap
    `_resolve_connected_name()` already fixes for an ORDINARY, non-black-
    box instance elsewhere in this module), genuinely absent from the IR.

    Now additionally recognizes, via `_resolve_uninstantiated_connection_text()`:
      - `identifier[N]` (a literal integer index) -> kept as-is, indexed.
      - `identifier[genvar]` where `genvar` is a name found in
        `genvar_values` (see `_walk_body_members()`'s own
        `GenerateBlockArraySymbol` handling for how that's built --
        pyslang represents a genvar as a `ParameterSymbol` already
        resolved to ITS OWN constant value for this exact generate
        iteration) -> the genvar substituted with its resolved value,
        same final shape as the literal case. The genvar's NAME alone is
        all the raw syntax text ever carries -- there is no way to
        recover its per-iteration VALUE from text alone, which is
        exactly why this needs `genvar_values` threaded in from the
        live elaboration context rather than being handled here in
        isolation.
      - `identifier[a:b]` (a range-select with literal bounds) -> collapses
        to the bare `identifier`, matching `_resolve_connected_name()`'s
        own already-established convention for the ordinary-instance
        case (range info is discarded, not modeled as a suffix).
      - anything else (a non-constant index, an arithmetic expression, a
        concatenation, ...) -> still `None`, same "don't guess" discipline
        as before -- this is a widened recognized subset, not a general
        expression evaluator.
    """
    connections: dict[str, str | None] = {}
    syntax = member.syntax
    if syntax is None:
        return dict.fromkeys(member.portNames)
    for c in syntax.connections:
        if c.kind != SyntaxKind.NamedPortConnection:
            continue
        expr_text = str(c.expr).strip() if c.expr is not None else ""
        connections[str(c.name)] = _resolve_uninstantiated_connection_text(expr_text, genvar_values)
    return connections


_ELEMENT_SELECT_RE = re.compile(r"^([A-Za-z_]\w*)\[(\w+)\]$")
_RANGE_SELECT_RE = re.compile(r"^([A-Za-z_]\w*)\[\d+:\d+\]$")


def _resolve_uninstantiated_connection_text(expr_text: str, genvar_values: dict[str, int]) -> str | None:
    """The actual recognizer for `_extract_uninstantiated_connections()`
    above -- split out standalone so each recognized shape (and the
    "give up" fallback) is independently testable. See that function's
    own docstring for the full "why" of each case."""
    if expr_text.isidentifier():
        return expr_text
    element_match = _ELEMENT_SELECT_RE.match(expr_text)
    if element_match:
        base, index_text = element_match.group(1), element_match.group(2)
        if index_text.isdigit():
            return f"{base}[{index_text}]"
        if index_text in genvar_values:
            return f"{base}[{genvar_values[index_text]}]"
        return None
    range_match = _RANGE_SELECT_RE.match(expr_text)
    if range_match:
        return range_match.group(1)
    return None


def _prefix_local_connections_dict(connections: dict[str, str | None], name_prefix: str, local_net_names: set[str]) -> dict[str, str | None]:
    """The `UnresolvedInstance.connections` (a plain dict, unlike a real
    Instance's own `list[PortConnection]`) counterpart to
    `_prefix_local_connections()` above -- same reasoning, see that
    function's own docstring."""
    if not name_prefix:
        return connections
    return {
        port: (f"{name_prefix}{connected_to}" if connected_to is not None and _strip_constant_index_suffix(connected_to) in local_net_names else connected_to)
        for port, connected_to in connections.items()
    }


def _extract_sequential_elements(proc: ProceduralBlockSymbol, interface_identities: list[tuple[str, object]]) -> list[SequentialElement]:
    stmt = proc.body
    if not isinstance(stmt, TimedStatement):
        return []

    events = []
    stmt.timing.visit(lambda n: events.append(n) if isinstance(n, SignalEventControl) else None)
    edge_events = [e for e in events if e.edge in _EDGE_MAP]
    if not edge_events:
        return []  # no edge in the sensitivity list -- combinational or unsupported shape

    inner = stmt.stmt
    if isinstance(inner, BlockStatement):
        inner = inner.body

    reset_ref: str | None = None
    reset_active_low: bool | None = None
    if isinstance(inner, ConditionalStatement):
        reset_ref, reset_active_low = _parse_reset_condition(inner.conditions[0].expr)
    # else: either a bare resetless clocked block (`always @(posedge clk) ...`
    # with no if/else at all) or some other top-level shape -- either way,
    # _collect_assignment_targets below still finds whatever it can walk to.

    output_signals = _collect_assignment_targets(inner, interface_identities)
    if not output_signals:
        return []  # no recognizable assignment anywhere in this block

    # `data_ref` -- see SequentialElement's own docstring for the full
    # "why" -- is scoped to ONLY the "not currently resetting" branch of
    # the block's own top-level if/else (the reset branch's own
    # constant/override assignment, e.g. `q_int <= '0;`, is never real
    # data-path information); the whole body for a resetless block,
    # matching how reset_ref itself is already scoped to the SAME
    # top-level conditional above.
    data_scope = inner.ifFalse if isinstance(inner, ConditionalStatement) and inner.ifFalse is not None else inner
    data_refs = _collect_assignment_sources(data_scope)

    # The clock is whichever edge signal isn't the reset -- can't just take the
    # first sensitivity-list entry, since an async reset's own edge (e.g.
    # `negedge rst_n`) is indistinguishable from a clock edge by shape alone.
    clock_events = [e for e in edge_events if e.expr.getSymbolReference().name != reset_ref]
    if not clock_events:
        return []  # couldn't isolate a clock edge distinct from the reset signal
    clock_ref = clock_events[0].expr.getSymbolReference().name
    edge = _EDGE_MAP[clock_events[0].edge]

    reset_kind = ResetKind.NONE
    if reset_ref is not None:
        reset_in_sensitivity = any(e.expr.getSymbolReference().name == reset_ref for e in edge_events)
        reset_kind = ResetKind.ASYNC if reset_in_sensitivity else ResetKind.SYNC

    return [
        SequentialElement(
            output_signal=output_signal,
            is_latch=False,
            clock_ref=clock_ref,
            edge=edge,
            reset_ref=reset_ref,
            reset_kind=reset_kind,
            reset_active_low=reset_active_low,
            data_ref=data_refs.get(output_signal),
        )
        for output_signal in output_signals
    ]


def _collect_assignment_targets(stmt, interface_identities: list[tuple[str, object]]) -> list[str]:
    """Walks the full statement tree under a clocked block's top-level
    if/else (both branches, nested begin/end blocks, `case`/`unique case`
    arms) and returns every distinct signal assigned via a top-level
    ExpressionStatement, in first-seen order -- not just a single
    top-level assignment. This is *only* "which signals does this block
    drive" -- which value each is assigned to, and under which specific
    condition, is never inspected (no data-path/next-state modeling, per
    this module's own docstring). A statement shape not recognized here
    (e.g. a nested timing control, a function call with side effects) is
    silently not walked further, same "leave unrecognized shapes alone
    rather than guess" philosophy as the rest of this extraction.

    Bug fix (found via real `generate`-block validation, a `for`-generate
    directly containing an `always_ff` per iteration with no submodule
    instantiation at all, e.g. `q_o[bit_idx] <= d_i[bit_idx];`): a bare
    `s.expr.left.getSymbolReference()` auto-unwraps a constant-indexed
    ElementSelect target straight to its base symbol, silently discarding
    the index -- exactly the same gap `_resolve_connected_name()` already
    fixes for a continuous-assign LHS/port connection (see its own
    docstring). Reused here instead of a second, duplicate unwrapping
    implementation -- every generated flop's own `output_signal` used to
    collapse to the identical bare base name (e.g. `q_o`) regardless of
    which bit it actually drove, making them indistinguishable from one
    another.
    """
    seen: dict[str, None] = {}  # insertion-ordered de-dup; a dict, not a set, so order stays stable/testable

    def visit(s) -> None:
        if isinstance(s, ExpressionStatement):
            if s.expr.kind == ExpressionKind.Assignment:
                name = _resolve_connected_name(s.expr.left, interface_identities)
                if name is not None:
                    seen.setdefault(name, None)
        elif isinstance(s, BlockStatement):
            visit(s.body)
        elif isinstance(s, StatementList):
            for sub in s.list:
                visit(sub)
        elif isinstance(s, ConditionalStatement):
            visit(s.ifTrue)
            if s.ifFalse is not None:
                visit(s.ifFalse)
        elif isinstance(s, CaseStatement):
            for item in s.items:
                visit(item.stmt)
            if s.defaultCase is not None:
                visit(s.defaultCase)

    visit(stmt)
    return list(seen)


def _collect_assignment_sources(stmt) -> dict[str, str | None]:
    """Like _collect_assignment_targets() above, but resolves each
    target's own `data_ref` too -- see SequentialElement.data_ref's own
    docstring for the full "why"/scope. For each target, EVERY
    assignment site found here (across nested blocks/case arms within
    the SAME scope the caller already narrowed to "not resetting") must
    independently qualify as a bare, same-width single-signal RHS
    reference (mirroring _build_continuous_assign()'s exact pass-through
    test -- unwrap one same-width ConversionExpression, require a
    NamedValueExpression) AND agree on the SAME source signal, or that
    target's own entry is left out of the returned dict entirely (never
    a guessed-at value) -- a case statement selecting between two
    DIFFERENT bare registers (real next-state logic, not a pass-through)
    is exactly the shape this must NOT collapse into a single answer
    for."""
    sources: dict[str, str | None] = {}
    disqualified: set[str] = set()

    def bare_source_or_none(assign_expr) -> str | None:
        left = assign_expr.left
        right = assign_expr.right
        if right.kind == ExpressionKind.Conversion:
            right = right.operand
        if right.kind == ExpressionKind.NamedValue and right.type.bitWidth == left.type.bitWidth:
            return right.symbol.name
        return None

    def visit(s) -> None:
        if isinstance(s, ExpressionStatement):
            if s.expr.kind == ExpressionKind.Assignment:
                ref = s.expr.left.getSymbolReference()
                if ref is None or ref.name in disqualified:
                    return
                source = bare_source_or_none(s.expr)
                if source is None or (ref.name in sources and sources[ref.name] != source):
                    disqualified.add(ref.name)
                    sources.pop(ref.name, None)
                    return
                sources[ref.name] = source
        elif isinstance(s, BlockStatement):
            visit(s.body)
        elif isinstance(s, StatementList):
            for sub in s.list:
                visit(sub)
        elif isinstance(s, ConditionalStatement):
            visit(s.ifTrue)
            if s.ifFalse is not None:
                visit(s.ifFalse)
        elif isinstance(s, CaseStatement):
            for item in s.items:
                visit(item.stmt)
            if s.defaultCase is not None:
                visit(s.defaultCase)

    visit(stmt)
    return sources


def _parse_reset_condition(expr) -> tuple[str | None, bool | None]:
    if isinstance(expr, UnaryExpression) and expr.op == UnaryOperator.LogicalNot:
        ref = expr.operand.getSymbolReference()
        return (ref.name, True) if ref else (None, None)
    ref = expr.getSymbolReference()
    return (ref.name, False) if ref else (None, None)
