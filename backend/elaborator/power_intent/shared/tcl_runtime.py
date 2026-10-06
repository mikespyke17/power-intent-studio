"""Generic Tcl command-registration layer -- format- and version-agnostic.

Split out of elaborate.py to make that explicit: nothing here has ever
referenced a source_format or a source_version value, only the *presence*
of a resolved version (via `state.source_version is None`, for the
requires_version gate) -- confirmed by inspection before this split, not
just asserted. This is the piece 2b (CPF) is expected to reuse as-is.

Four hard requirements, from the Tcl interpreter spike and from two
post-audit findings, all implemented once, centrally, here -- never
per-handler:
1. List-valued Tcl arguments must be explicitly split (`interp.tk.splitlist`)
   -- they arrive as raw unsplit strings otherwise.
2. Exceptions raised inside a command handler must be re-signaled via
   `interp.tk.call("error", str(e))` -- a bare `raise` loses its message
   crossing back into Tcl.
3. Every option a registered command accepts must be declared explicitly
   (`options`); any flag not in that set is rejected at parse time, before
   the handler ever runs. Confirmed empirically as a real gap during the
   2a feature-completeness audit: without this, a handler that only reads
   the flags it knows about via `_one(parsed, key)` silently drops any
   other flag's data with no error at all (`create_power_domain PD_TOP
   -elements {.} -bogus_flag whatever` elaborated successfully, discarding
   `-bogus_flag` entirely) -- exactly the "silently produce incomplete
   data instead of failing loud" failure mode this project avoids
   everywhere else.
4. Relative paths passed to Tcl's `source` must resolve against the
   currently-sourcing file's directory, not the process cwd -- Tcl's
   default behavior otherwise. Confirmed empirically as a real gap during
   the 2b feature-completeness audit: CPF has no `load_upf`-equivalent
   wrapper verb, so multi-file CPF relies on raw `source` directly, which
   hit the exact bug UPF's `load_upf` handler had already fixed
   internally -- but only inside that one wrapper, not for `source` used
   directly. `install_relative_source` below fixes it generically, for
   both formats (and any future one) and both usages (through a wrapper
   verb or bare `source`), rather than re-fixing it per format.
"""

from __future__ import annotations

import os


class UnrecognizedSyntax(ValueError):
    """An option (or a stray argument) a command does not recognize.

    A ValueError, so a format that has not opted in still fails exactly
    as before; one that has (`state.skip_unrecognized`, UPF) records the
    command and skips it instead -- see _register()'s wrapper."""


def _is_group(arg: str) -> bool:
    """Whether a raw argument is a brace GROUP rather than a flag token.

    A real flag is one word (`-complete`, `-supply`); a brace group
    arrives as a single argument holding several (`-supply_expr {...}`).
    Whitespace is therefore the whole test, and a sound one."""
    return any(c.isspace() for c in arg.strip())


def _parse_args(
    command_name: str,
    raw_args: tuple,
    positional: list[str],
    options: set[str],
    list_options: set[str],
    flag_options: set[str],
    interp,
    optional_positional: "list[str] | None" = None,
    bare_or_valued: "set[str] | None" = None,
    name_then_group: "set[str] | None" = None,
) -> dict[str, list]:
    """Parses a flat Tcl arg tuple into {flag_name: [occurrence, ...]}.

    Every key maps to a list of occurrences (even flags that only ever
    appear once) so repeatable flags (e.g. create_supply_set's -function,
    which UPF allows multiple times in one call) don't need special-casing
    -- callers needing a single value just take occurrence [0].

    Gotcha-fix #1 (hard requirement): a flag declared in `list_options` has
    its value passed through `interp.tk.splitlist` here, once, centrally --
    never left to individual handlers to remember.

    Gotcha-fix #3 (hard requirement): a flag not in `options` raises
    immediately, naming the command and the unrecognized flag -- never
    silently parsed-and-ignored. `flag_options` (e.g. add_power_state's
    -complete) are valueless boolean switches: presence alone sets `True`,
    no following token is consumed as a value.

    `bare_or_valued` is for the handful of flags that occur BOTH ways in
    real files. `add_power_state`'s own `-supply` is the case that forced
    it: `add_power_state -supply SS_TOP ...` names its object by flag,
    while `add_power_state SS_TOP -supply ...` uses the same word as a
    bare MARKER meaning "the positional object is a supply set". Both are
    real. Declared value-taking only, the bare form swallowed the
    following `-state` and the whole parse collapsed. Disambiguated here
    by the next token: another `-flag` (or nothing) means the marker
    form, anything else is its value.

    `name_then_group` handles a flag written as a NAME followed by a
    separate brace group of that item's own sub-flags:
    `-state HV {-supply_expr {...}}`, as distinct from the same thing
    written as one group, `-state {HV -supply_expr {...}}`. Both forms
    are real and published. Tcl hands the first to Python as TWO
    arguments and the second as one, so only the split form reaches
    here needing repair -- the group arrives looking like an
    unrecognized flag and the command was rejected. The two are told
    apart safely: a single argument that starts with `-` AND contains
    whitespace can only be a brace group, never a real flag token.
    """
    args = list(raw_args)
    parsed: dict[str, list] = {}

    remaining_positional = list(positional)
    while remaining_positional and args and not args[0].startswith("-"):
        parsed[remaining_positional.pop(0)] = [args.pop(0)]
    if remaining_positional:
        raise ValueError(f"'{command_name}': missing required argument(s) {remaining_positional}")

    # Optional leading positionals, consumed only if actually present.
    for key in list(optional_positional or []):
        if args and not args[0].startswith("-"):
            parsed[key] = [args.pop(0)]

    while args:
        flag = args.pop(0)
        if not flag.startswith("-"):
            raise UnrecognizedSyntax(f"'{command_name}': expected an option starting with '-', got {flag!r}")
        key = flag[1:]
        if key not in options:
            raise UnrecognizedSyntax(f"'{command_name}': unrecognized option '-{key}' (recognized: {sorted(options)})")
        if key in flag_options:
            parsed.setdefault(key, []).append(True)
            continue
        if key in (bare_or_valued or set()) and (not args or args[0].startswith("-")):
            parsed.setdefault(key, []).append(True)
            continue
        if not args:
            raise ValueError(f"'{command_name}': option '{flag}' is missing its value")
        value = args.pop(0)
        if key in (name_then_group or set()) and args and args[0].startswith("-") and _is_group(args[0]):
            # Split form: re-join the name with its own group so the
            # handler sees exactly the single-group shape it already
            # understands.
            value = f"{value} {args.pop(0)}"
        if key in list_options:
            value = list(interp.tk.splitlist(value))
        parsed.setdefault(key, []).append(value)
    return parsed


def _one(parsed: dict[str, list], key: str, default=None):
    occurrences = parsed.get(key)
    return occurrences[0] if occurrences else default


def _register(
    interp,
    state,
    name,
    handler,
    positional=(),
    options=(),
    list_options=(),
    flag_options=(),
    requires_version=True,
    optional_positional=(),
    bare_or_valued=(),
    name_then_group=(),
):
    positional = list(positional)
    optional_positional = list(optional_positional)
    # Positional arguments are always recognized -- declaring them again in
    # `options` would be redundant busywork for every call site.
    # Positional arguments are always recognized as flags too -- which is
    # what lets an `optional_positional` name be supplied either way.
    options = set(options) | set(positional) | set(optional_positional)
    list_options = set(list_options)
    flag_options = set(flag_options)
    bare_or_valued = set(bare_or_valued)
    name_then_group = set(name_then_group)
    options |= bare_or_valued

    def wrapped(*raw_args):
        try:
            if requires_version and state.source_version is None:
                # Deliberately format-agnostic wording -- this is shared
                # infrastructure (confirmed a real prior instance of
                # hardcoded UPF wording leaking in here; fixed while
                # building 2b, see docs/milestone2_powerintent_elaborator.md's
                # note on this recurring pattern).
                raise ValueError(
                    f"'{name}' called before any version declaration -- the design must declare its "
                    "version before any power-intent commands"
                )
            parsed = _parse_args(
                name, raw_args, positional, options, list_options, flag_options, interp,
                optional_positional=optional_positional, bare_or_valued=bare_or_valued,
                name_then_group=name_then_group,
            )
            handler(state, parsed)
            return ""
        except UnrecognizedSyntax as e:
            if not getattr(state, "skip_unrecognized", False):
                interp.tk.call("error", str(e))
            # Skipped WHOLE, never half-applied: a command whose meaning
            # includes an option this app does not understand would
            # otherwise be imported silently wrong.
            state.record_unrecognized(name, str(e))
            return ""
        except Exception as e:
            # Gotcha-fix #2 (hard requirement): a bare `raise` here loses its
            # message crossing back into Tcl -- confirmed empirically during
            # the spike. Routing through Tcl's own `error` command preserves
            # it losslessly, including special characters a hand-quoted
            # interp.eval(f"error ...") string would mangle.
            interp.tk.call("error", str(e))

    interp.createcommand(name, wrapped)


def install_relative_source(interp, state) -> None:
    """Overrides Tcl's native `source` so a relative path resolves against
    the currently-sourcing file's directory, not the process cwd.

    Gotcha-fix #4 (hard requirement). Renames the real `source` to
    `_tcl_native_source` and delegates to it after resolving the path and
    updating `state`'s file stack -- confirmed empirically to work
    correctly even when the process cwd is deliberately wrong. Call this
    once, early, in every format's `_setup_interpreter`, whether or not
    that format has its own wrapper verb (like UPF's `load_upf`) around
    `source` -- a wrapper verb's own `interp.eval("source ...")` call goes
    through this same override, and a design using bare `source` directly
    (CPF has no wrapper verb at all) is covered too.
    """
    interp.eval("rename source _tcl_native_source")

    def relative_source(*raw_args):
        if not raw_args:
            raise ValueError("'source': missing filename")
        path = raw_args[0]
        if not os.path.isabs(path):
            path = os.path.join(os.path.dirname(state.current_file), path)
        state.push_file(path)
        try:
            interp.eval(f"_tcl_native_source {{{path}}}")
        finally:
            state.pop_file()

    interp.createcommand("source", relative_source)
