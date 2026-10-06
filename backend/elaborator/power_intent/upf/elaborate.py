"""UPF elaborator (Milestone 2a) -- public entry point and wiring.

Built on Python's bundled Tcl interpreter (`tkinter.Tcl()`), per the
Tcl-runner architecture spiked and confirmed in
docs/milestone2_powerintent_elaborator.md: UPF verbs are registered as
instrumented Tcl procs that record their arguments, letting the real
interpreter handle variables/control-flow/`load_upf`-as-`source` for free.

Split, after review, into `backend/elaborator/power_intent/`:
- `shared/tcl_runtime.py`: the generic Tcl command-registration layer (four
  gotcha-fix wrappers, including `install_relative_source` -- overrides
  Tcl's native `source` so relative paths resolve against the currently-
  sourcing file, not the process cwd; `load_upf` below only needs to push/
  pop the extra `-scope` it adds on top of plain sourcing now), confirmed
  format- and version-agnostic -- reused as-is by the CPF elaborator (2b).
- `shared/builders.py`: the mutable accumulate-then-freeze state, confirmed
  version-agnostic by construction (canonicalizes on UPF 3.1's shape
  regardless of declared source version) and format-agnostic by
  construction (no UPF- or CPF-specific fields; `source_format` is an
  explicit parameter to `_freeze_all`, not hardcoded).
- `ir/schema.py`: the canonical power-intent IR, a true sibling of `upf/`
  and (eventually) `cpf/` -- not nested inside either, mirroring
  `backend/elaborator/rtl/{verilog,vhdl,ir}/`.
- `upf/verbs.py`: the content-producing verb handlers, confirmed to have
  zero version-conditional logic for construction. Each handler's
  "already exists" rejection on a duplicate name is confirmed correct UPF
  behavior (not a merge gap) -- see verbs.py's own docstring and the doc's
  "Accumulate-then-freeze" section for the full correction.
- `upf/legacy_pst.py`: the 2.1-and-earlier create_pst/add_pst_state
  mechanism, isolated because it's the version-flavored one.

This module stays thin: version resolution (`upf_version` -- the one piece
of real per-format logic that doesn't belong in the generic runtime, since
2b's `set_cpf_version` will have its own different supported-version set),
`load_upf`, wiring everything together, and the public `elaborate()` entry
point.

Version handling implements the full algorithm from the doc's design
question 1, verified in review: auto-detected only (no caller-supplied
version parameter, no interactive prompt), unsupported versions rejected
immediately by name, a power-intent verb before any version declaration
raises immediately, conflicting per-file declarations raise naming both
files, and a whole-design (not per-file) backstop catches the fully
degenerate zero-power-intent case.
"""

from __future__ import annotations

import os

import tkinter
from typing import Optional

from backend.elaborator.power_intent.ir import PowerIntentDesign, SourceFormat
from backend.elaborator.power_intent.shared.builders import _ElaborationState, _freeze_all
from backend.elaborator.power_intent.shared.tcl_runtime import _one, _register, install_relative_source
from backend.elaborator.power_intent.upf.legacy_pst import _add_port_state, _add_pst_state, _create_pst
from backend.elaborator.power_intent.upf.verbs import (
    _map_power_switch,
    _set_level_shifter_cell,
    _set_port_attributes,
    _set_retention_control,
    _set_scope,
    _add_power_state,
    _associate_supply_set,
    _connect_supply_net,
    _create_power_domain,
    _create_power_state_group,
    _create_power_state_group,
    _create_power_switch,
    _create_supply_net,
    _create_supply_port,
    _create_supply_set,
    _set_domain_supply_net,
    _set_isolation,
    _set_level_shifter,
    _set_retention,
)

_SUPPORTED_VERSIONS = {"2.1", "3.0", "3.1"}


def _handle_upf_version(state: _ElaborationState, parsed: dict[str, list]) -> None:
    version = _one(parsed, "version")
    if version not in _SUPPORTED_VERSIONS:
        raise ValueError(
            f"UPF version '{version}' is not supported (supported: {sorted(_SUPPORTED_VERSIONS)}), "
            f"declared in '{state.current_file}'"
        )
    state.version_declared_by[state.current_file] = version
    if state.source_version is None:
        state.source_version = version
    elif state.source_version != version:
        raise ValueError(
            f"conflicting upf_version declarations: '{state.current_file}' declares '{version}', but this "
            f"design's version was already resolved to '{state.source_version}' -- a single design must use "
            "one semantic version throughout"
        )


def _handle_load_upf(interp, state: _ElaborationState, parsed: dict[str, list]) -> None:
    """Sources a sub-file under a scope.

    `file` arrives either positionally (`load_upf sub.upf`, real UPF) or
    as a flag (`load_upf -file sub.upf`, what this app generates) -- see
    the registration for why both are accepted. `-instance` is likewise
    an accepted alias for `-scope`: both name the instance the loaded
    file applies to. The elaborator deliberately does NOT decide whether
    an instance means "flatten into the parent design" or "attach as a
    macro's vendor intent" -- that needs the elaborated RTL to know
    whether the instance is a macro, which this layer has no access to,
    so it is the importer's call (section 5).
    """
    path = _one(parsed, "file")
    scope = _one(parsed, "scope") or _one(parsed, "instance") or state.current_scope
    if path is None:
        raise ValueError("load_upf: a file is required, either positionally or via -file")
    # Relative-path resolution and file-stack push/pop are handled generically
    # by shared/tcl_runtime.py's install_relative_source override of `source`
    # itself (gotcha-fix #4) -- load_upf only needs to additionally push/pop
    # the -scope UPF adds on top of plain sourcing.
    state.push_scope(scope)
    try:
        interp.eval(f"source {{{path}}}")
    finally:
        state.pop_scope()


def _setup_interpreter(state: _ElaborationState) -> tkinter.Tcl:
    interp = tkinter.Tcl()
    install_relative_source(interp, state)

    _register(interp, state, "upf_version", _handle_upf_version, positional=["version"], requires_version=False)
    # `set_upf_version` is the spelling this project's own golden.upf uses
    # (both exist in real UPF; `upf_version` is the older one). Same
    # handler -- a file may use either.
    _register(interp, state, "set_upf_version", _handle_upf_version, positional=["version"], requires_version=False)
    # `set_scope` moves the current scope in place -- the first line of
    # every file this app generates, and previously an unknown command.
    _register(interp, state, "set_scope", _set_scope, positional=["scope"], requires_version=False)
    _register(
        interp,
        state,
        "load_upf",
        lambda s, p: _handle_load_upf(interp, s, p),
        # `file` is deliberately NOT a required positional: real UPF has
        # it positional (`load_upf <file>`), but this app's own generator
        # emits the `-file <path>` flag form, and both spellings are in
        # circulation across tools. Accepting either means a generated
        # file can be re-read without the generator having to change a
        # form it already ships -- see _handle_load_upf for the
        # either/or resolution.
        # `-instance` is the spelling the GUI generates for a macro
        # attachment and real UPF uses for a hierarchical sub-file; both
        # name the instance the loaded file applies to. Accepted as an
        # alias here; deciding whether a given instance means "flatten"
        # or "attach to a macro" is the importer's job (section 5), not
        # the elaborator's -- it has no RTL to check the instance against.
        optional_positional=["file"],
        options={"file", "scope", "instance"},
        requires_version=False,
    )

    _register(
        interp,
        state,
        "create_power_domain",
        _create_power_domain,
        positional=["name"],
        # `-include_scope` means "this domain IS the current scope" --
        # the same thing `-elements {.}` says, which this app already
        # models and generates. A pure parser gap (audit B7); the handler
        # normalises it onto that existing representation.
        options={"elements", "exclude_elements", "supply", "available_supplies", "include_scope"},
        flag_options={"include_scope"},
        list_options={"elements", "exclude_elements", "supply", "available_supplies"},
    )
    _register(interp, state, "create_supply_port", _create_supply_port, positional=["name"],
        # `-domain` scopes the port to a domain's interface -- ordinary,
        # idiomatic UPF, and the form a real reference file uses
        # throughout. Previously rejected outright (audit B6).
        options={"direction", "domain"})
    # `-reuse` declares that this net CONTINUES an already-declared net
    # from an outer scope rather than creating a new, independent one
    # (review finding 1.5) -- a flag, no value.
    _register(
        interp, state, "create_supply_net", _create_supply_net,
        positional=["name"], options={"domain", "reuse"}, flag_options={"reuse"},
    )
    # -ports is recognized now: the tracked gap it used to represent was
    # closed once round-trip import supplied the motivating usage -- see
    # _connect_supply_net's own docstring.
    _register(
        interp,
        state,
        "connect_supply_net",
        _connect_supply_net,
        positional=["net"],
        options={"domain", "ports"},
        list_options={"ports"},
    )
    _register(
        interp,
        state,
        "create_supply_set",
        _create_supply_set,
        positional=["name"],
        options={"function", "supply"},
        list_options={"function", "supply"},
    )
    _register(
        interp,
        state,
        "set_domain_supply_net",
        _set_domain_supply_net,
        positional=["domain"],
        options={"primary_power_net", "primary_ground_net"},
    )
    # -control_port is repeatable (one control signal per occurrence), not
    # itself list-valued -- NOT in list_options. Confirmed by a real bug:
    # marking it as list_options caused splitlist to wrap each scalar
    # occurrence in its own 1-element list (control_ports ending up as
    # [['sleep_n']] instead of ['sleep_n']), conflating "repeatable flag"
    # with "flag whose single value is itself a Tcl list" -- they're
    # different things and need different handling.
    _register(
        interp,
        state,
        "create_power_switch",
        _create_power_switch,
        positional=["name"],
        # `-ack_port {port net [{expr}]}` and `-ack_delay {port delay}`
        # are real create_power_switch options (verified against two
        # independent references for UPF 2.1+, alongside
        # -output_supply_port). Both are pair/triple-valued, so both are
        # list_options.
        options={
            "domain", "supply_set", "input_supply_port", "output_supply_port",
            "control_port", "ack_port", "ack_delay", "on_state", "off_state",
        },
        # These are all {role net}/{name ... expr} PAIRS, so each single
        # occurrence's value is itself a Tcl list -- list_options, which
        # is a different thing from a repeatable flag (see the
        # -control_port note above for the bug that distinction caused).
        list_options={
            "input_supply_port", "output_supply_port", "control_port",
            "ack_port", "ack_delay", "on_state", "off_state",
        },
    )
    _register(
        interp,
        state,
        "set_isolation",
        _set_isolation,
        positional=["name"],
        options={
            "domain", "source", "sink", "elements", "location", "clamp_value",
            "isolation_supply", "isolation_supply_set", "isolation_signal", "isolation_sense",
            # `-name_prefix`/`-name_suffix`: the prefix for the inserted
            # isolation CELL instance names.
            #
            # SAME CAVEAT AS set_level_shifter's, and deliberately no
            # stronger: this is accepted on REAL-WORLD EVIDENCE, not on a
            # verified signature. No published option list for
            # set_isolation carries it (five were checked), while
            # set_level_shifter's is confirmed. What tipped it is that a
            # real hand-authored reference file uses it six times on this
            # exact command, and rejecting a flag a designer actually
            # writes is worse than accepting one whose LRM status is
            # unconfirmed -- accepting on READ asserts nothing into any
            # generated file.
            #
            # The deeper question stays open, and the generator still
            # does NOT emit it here (see
            # isolation_name_prefix_upf_warnings()): reading tolerantly
            # and writing conservatively are different decisions.
            "name_prefix", "name_suffix",
            "applies_to",
            # Real 1801: re-open this named strategy and refine it. See
            # verbs._refine_existing().
            "update",
        },
        list_options={"elements"},
        flag_options={"update"},
    )
    _register(
        interp,
        state,
        "set_retention",
        _set_retention,
        positional=["name"],
        options={
            "domain", "elements", "retention_supply", "retention_supply_set", "supply",
            "save_signal", "restore_signal", "primary_supply", "primary_supply_set",
            "update",  # see verbs._refine_existing()
        },
        list_options={"elements", "supply"},
        flag_options={"update"},
    )
    _register(
        interp,
        state,
        "set_level_shifter",
        _set_level_shifter,
        positional=["name"],
        options={
            "domain", "source", "sink", "elements", "location", "applies_to", "rule", "threshold",
            "input_supply", "output_supply",
            # `-name_prefix`/`-name_suffix` control how the inserted
            # level-shifter CELL instances are named. Verified real
            # options of this command; the prefix is modelled, the suffix
            # is recognised so a file carrying one still elaborates.
            "name_prefix", "name_suffix",
            "update",  # see verbs._refine_existing()
        },
        list_options={"elements"},
        flag_options={"update"},
    )
    _register(
        interp,
        state,
        # UPF 2.1+: binds a supply set to a domain's own supply-set
        # handle. See _associate_supply_set()'s own docstring.
        "associate_supply_set",
        _associate_supply_set,
        positional=["name"],
        options={"handle", "update"},
        flag_options={"update"},
    )
    _register(
        interp,
        state,
        "add_power_state",
        _add_power_state,
        # OPTIONAL: `add_power_state -supply SS ...` (UPF 2.1+) names its
        # object by flag and has no positional at all, while
        # `add_power_state VDD ...` (what this app generates) does. Both
        # are real -- see _add_power_state()'s own docstring.
        optional_positional=["object"],
        # -supply/-domain/-complete are UPF 2.1 additions, per the version
        # research ("Added -supply, -domain and -complete to
        # add_power_state"). -complete is a valueless boolean switch, not a
        # -flag value pair.
        options={"state", "logic_expr", "supply_expr", "group", "complete", "simstate"},
        # `-supply`/`-domain` occur BOTH as object-naming flags and as
        # bare markers qualifying the positional object (audit B2) --
        # see _parse_args()'s own `bare_or_valued` docstring.
        bare_or_valued={"supply", "domain"},
        # `-state NAME {flags}` (name outside the group) alongside
        # `-state {NAME flags}` (name inside). Both real (audit B3).
        name_then_group={"state"},
        # -state is BOTH repeatable and pair-valued: each occurrence is
        # its own {NAME value}. See _add_power_state's own docstring.
        list_options={"state"},
        flag_options={"complete"},
    )
    _register(
        interp,
        state,
        "create_power_state_group",
        _create_power_state_group,
        positional=["name"],
        options={"elements"},
        list_options={"elements"},
    )
    _register(
        interp, state, "create_pst", _create_pst, positional=["name"], options={"supplies"}, list_options={"supplies"}
    )
    # The legacy per-supply state lists a PST's rows name: `add_port_state`
    # for a port, `add_supply_state` for a net with no port. Both are what
    # this app's own legacy export writes.
    for verb in ("add_port_state", "add_supply_state"):
        _register(
            interp, state, verb, _add_port_state,
            positional=["object"], options={"state"}, list_options={"state"},
        )
    _register(
        interp,
        state,
        "add_pst_state",
        _add_pst_state,
        positional=["name"],
        # `-pst` is the spelling golden.upf uses; `-pst_name` the original.
        options={"pst_name", "pst", "state"},
        list_options={"state"},
    )

    _register(
        interp, state, "map_power_switch", _map_power_switch,
        # The switch name list is POSITIONAL -- `map_power_switch {PG}
        # -lib_cells {CELL}` -- confirmed by expert review, retracting an
        # earlier instruction in this project that specified
        # `-switch_name_list` as the standard spelling. It is registered
        # as OPTIONAL here rather than required so files written to the
        # older (wrong) spelling still elaborate; _map_power_switch()
        # rejects the case where neither form names a switch.
        optional_positional=["switch_name_list"],
        # `-domain` is how the three map_*_cell commands name the domain a
        # mapping belongs to (required on all three, confirmed against a
        # signature reference); a real file writes it on map_power_switch
        # too. Accepted rather than rejected (audit B4). Whether it is
        # strictly required HERE is one of the audit's own flagged-
        # uncertain items, so it stays optional and unreported.
        options={"lib_cells", "port_map", "domain"},
        list_options={"lib_cells", "switch_name_list", "port_map"},
    )
    _register(
        interp, state, "set_level_shifter_cell", _set_level_shifter_cell,
        options={"level_shifter", "cell_name"},
    )
    # The map_* spelling of the same thing -- positional strategy name,
    # `-domain`, `-lib_cells` LIST (audit B10). Registered alongside
    # rather than instead of set_level_shifter_cell: both are real, and a
    # file written to either must still elaborate.
    _register(
        interp, state, "map_level_shifter_cell", _set_level_shifter_cell,
        positional=["strategy"],
        options={"domain", "lib_cells", "elements"},
        list_options={"lib_cells", "elements"},
    )
    _register(
        interp, state, "set_retention_control", _set_retention_control,
        positional=["name"], options={"domain", "save_signal", "restore_signal"},
    )
    _register(
        interp, state, "set_port_attributes", _set_port_attributes,
        options={"ports", "driver_supply", "receiver_supply"},
        list_options={"ports"},
    )

    return interp


def _command_location(interp) -> tuple[Optional[int], str]:
    """(line, command text) of the UPF command being run -- the innermost
    `source`d frame, which is the line in the file being read (a nested
    `load_upf` included). (None, "") outside any sourced file."""
    level = int(interp.eval("info frame"))
    for frame_level in range(level, 0, -1):
        words = interp.tk.splitlist(interp.eval(f"info frame {frame_level}"))
        frame = dict(zip(words[0::2], words[1::2]))
        if frame.get("type") == "source" and "line" in frame:
            return int(frame["line"]), str(frame.get("cmd", ""))
    return None, ""


def _install_unrecognized_reporting(interp, state: _ElaborationState) -> None:
    """UPF does not fail on syntax it does not recognize: the command is
    skipped and recorded with its line (explicit request), and the rest of
    the file is still read. Two halves:

      - a command NAME Tcl does not know reaches Tcl's `unknown` handler,
        replaced here to record it;
      - an OPTION a known command does not recognize raises
        UnrecognizedSyntax, which _register()'s wrapper records once
        `skip_unrecognized` is on.

    Also gives every construct its line (`state.loc()`)."""
    state.skip_unrecognized = True
    state.line_lookup = lambda: _command_location(interp)

    def unknown(*words):
        state.record_unrecognized(str(words[0]) if words else "", "command not recognized")
        return ""

    interp.createcommand("_upf_unrecognized_command", unknown)
    interp.eval("rename unknown _tcl_unknown")
    interp.eval("proc unknown {args} { _upf_unrecognized_command {*}$args }")


def elaborate(top_file: str) -> PowerIntentDesign:
    """Elaborate a UPF design starting from `top_file`.

    Unlike the RTL elaborators, only a single entry-point file is needed:
    `load_upf` (unlike Verilog `include or a flat file list) fully handles
    multi-file inclusion within Tcl itself via `source`, so there is no
    separate file-list-plus-top-name contract to satisfy.
    """
    # ABSOLUTE before anything touches it. `install_relative_source`'s
    # own `source` override resolves a RELATIVE path against
    # `dirname(state.current_file)` -- and for this outermost call
    # `current_file` IS this same file, so a relative `top_file` was
    # joined to its own directory and DOUBLED
    # ("a/b/top.upf" -> "a/b/a/b/top.upf"). An absolute path
    # short-circuits the override's `isabs` check, which is why this
    # only ever bit a relative one. Found by the v1.0 clean-clone smoke
    # test; unreachable from the GUI, where QFileDialog always returns
    # an absolute path, and reachable from any programmatic caller.
    top_file = os.path.abspath(os.path.expanduser(top_file))
    state = _ElaborationState(top_file=top_file)
    interp = _setup_interpreter(state)
    _install_unrecognized_reporting(interp, state)
    # Redundant with the push the `source` override also does for this
    # same outermost call, and kept so `state.current_file` is always
    # populated before the override needs to read it.
    state.push_file(top_file)
    state.push_scope("")
    try:
        interp.eval(f"source {{{top_file}}}")
    finally:
        state.pop_scope()
        state.pop_file()
    return _freeze_all(state, SourceFormat.UPF)
