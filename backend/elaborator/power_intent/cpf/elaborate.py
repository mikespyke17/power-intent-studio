"""CPF elaborator (Milestone 2b) -- public entry point and wiring.

Mirrors backend/elaborator/power_intent/upf/elaborate.py's shape exactly,
reusing shared/tcl_runtime.py and shared/builders.py as-is (confirmed
format-agnostic while building 2a, extended not duplicated here).

Version handling: `set_cpf_version [value]` is verified, directly against
the primary spec, to behave differently from UPF's `upf_version` in one
real way -- the spec states "If specified, this command must be the first
CPF command in a CPF file" and gives an explicit default ("Default: 1.0")
if never called at all. Since CPF 1.0 is explicitly out of scope here (see
the doc's tracked gaps), a CPF design that never calls set_cpf_version
still gets a clear error via the same "verb before any resolved version"
/ whole-design-backstop machinery shared/tcl_runtime.py and
shared/builders.py already provide -- it is never silently defaulted to
1.0, even though the CPF spec itself would do that. "Must be first command
in the file" is not separately enforced beyond the existing "no
power-intent verb before a resolved version" check plus the standard
conflicting-declaration check; a stricter literal-first-token rule was
judged not worth the extra fragility, same reasoning as UPF's own
version-declaration timing.

Hierarchical scope: CPF has no load_upf-equivalent file-sourcing verb.
Instead, `set_design`/`set_instance`/`end_design` form a flat sequence of
scope blocks -- verified against the primary spec's own worked example
(a single set_design/end_design pair around a flat design) and its text
("set_instance... specifies... a hierarchical instance in the top
design"). `set_instance` REPLACES the current scope (via
_ElaborationState.set_scope), it does not nest/push the way UPF's
load_upf -scope does; `end_design` resets to top-level scope, closing
exactly the one open set_design block. This was treated as a hypothesis
to verify, not a settled design, per explicit instruction -- confirmed
against a hand-written hierarchical CPF fixture (see
tests/backend/golden_cpf/cpf_hierarchical/) before being finalized here.

Multi-file CPF (if used) needs no special verb of its own: it's plain Tcl
`source`, which this module registers no wrapper for. **Correction: an
earlier version of this docstring claimed this was "confirmed by
inspection, not assumed" -- it was not actually tested, and the 2b
feature-completeness audit proved that claim wrong.** Tcl's raw `source`
resolves a relative path against the process cwd, the exact bug UPF's
`load_upf` handler had already fixed internally -- but CPF has no such
wrapper to fix it in, so a real multi-file CPF design failed outright
("no such file or directory") whenever the cwd differed from the sourcing
file's directory. Fixed generically in
`shared/tcl_runtime.py`'s `install_relative_source` (gotcha-fix #4),
which overrides `source` itself so both formats -- and any usage, wrapper
verb or bare `source` -- get correct resolution. Confirmed now with an
actual multi-file fixture run from an unrelated cwd, not by inspection.

set_instance is treated as always taking its positional hier_instance
(the "change scope" usage); the query-mode usage (bare `set_instance`
returning the current scope) has no elaboration-relevant effect and isn't
supported -- a deliberate, logged simplification, not a guess.
"""

from __future__ import annotations

import os

from backend.elaborator.power_intent.cpf.verbs import (
    _create_isolation_rule,
    _create_level_shifter_rule,
    _create_global_connection,
    _create_nets,
    _create_nominal_condition,
    _create_power_domain,
    _create_power_mode,
    _create_power_switch_rule,
    _create_state_retention_rule,
    _update_isolation_rules,
    _update_level_shifter_rules,
    _update_power_domain,
    _update_power_switch_rule,
)
from backend.elaborator.power_intent.ir import PowerIntentDesign, SourceFormat
from backend.elaborator.power_intent.shared.builders import _ElaborationState, _freeze_all
from backend.elaborator.power_intent.shared.tcl_runtime import _one, _register, install_relative_source

_SUPPORTED_VERSIONS = {"1.1", "2.0"}


def _handle_set_cpf_version(state: _ElaborationState, parsed: dict[str, list]) -> None:
    version = _one(parsed, "version")
    if version not in _SUPPORTED_VERSIONS:
        raise ValueError(
            f"CPF version '{version}' is not supported (supported: {sorted(_SUPPORTED_VERSIONS)}), "
            f"declared in '{state.current_file}'"
        )
    state.version_declared_by[state.current_file] = version
    if state.source_version is None:
        state.source_version = version
    elif state.source_version != version:
        raise ValueError(
            f"conflicting set_cpf_version declarations: '{state.current_file}' declares '{version}', but this "
            f"design's version was already resolved to '{state.source_version}' -- a single design must use "
            "one semantic version throughout"
        )


def _handle_set_design(state: _ElaborationState, parsed: dict[str, list]) -> None:
    module = _one(parsed, "module")
    if state.scratch.get("cpf_design_open"):
        raise ValueError(f"'set_design {module}': a design block is already open -- missing end_design before this")
    state.scratch["cpf_design_open"] = True
    state.scratch["cpf_current_design"] = module


def _handle_end_design(state: _ElaborationState, parsed: dict[str, list]) -> None:
    if not state.scratch.get("cpf_design_open"):
        raise ValueError("'end_design' called with no open set_design block")
    state.scratch["cpf_design_open"] = False
    state.scratch["cpf_current_design"] = None
    state.set_scope("")


def _handle_set_hierarchy_separator(state: _ElaborationState, parsed: dict[str, list]) -> None:
    """`set_hierarchy_separator [character]` (Si2 CPF 2.1, p. 44).

    Accepted and recorded, not acted on. CPF's default delimiter is a
    period and `/` is the recommended alternative; every path this
    elaborator resolves is already written with `/`, so what matters is
    that a file declaring it loads rather than dying on an unknown
    command. Re-parsing against a genuinely different character would be
    a separate piece of work, and no file this project produces or reads
    needs it yet."""
    # Nothing to store: `_ElaborationState` has no separator field, and
    # introducing one would imply the rest of the elaborator consults it.
    # Accepting the command is the whole job here.
    _one(parsed, "character")


def _handle_set_instance(state: _ElaborationState, parsed: dict[str, list]) -> None:
    """`.` means the top scope, normalised to the empty-string scope this
    IR uses throughout -- exactly as UPF's own `set_scope` already did
    (see upf/verbs.py::_set_scope). Only that side had the
    normalisation: stored literally, `.` became a scope of its own, so a
    construct written after `set_instance .` got scope "." and no longer
    matched one declared before any set_instance at all -- visible as
    construct ids like ".::ISO_CPU", and as by-name references across the
    two failing to resolve. Found by round-tripping this app's own
    generated CPF, which emits `set_instance .` to return to the top."""
    hier_instance = _one(parsed, "hier_instance")
    state.set_scope("" if hier_instance in (".", None) else hier_instance)


def _setup_interpreter(state: _ElaborationState):
    import tkinter

    interp = tkinter.Tcl()
    install_relative_source(interp, state)

    _register(interp, state, "set_cpf_version", _handle_set_cpf_version, positional=["version"], requires_version=False)
    _register(interp, state, "set_design", _handle_set_design, positional=["module"], options={"ports"}, requires_version=False)
    _register(
        interp,
        state,
        "set_instance",
        _handle_set_instance,
        positional=["hier_instance"],
        options={"merge_default_domains", "port_mapping"},
        flag_options={"merge_default_domains"},
        requires_version=False,
    )
    _register(interp, state, "end_design", _handle_end_design, requires_version=False)
    # `set_hierarchy_separator [character]` (Si2 CPF 2.1, p. 44). CPF's
    # DEFAULT delimiter is a period, so any file using `/` paths must
    # declare it -- which this project's own generator now does.
    # Recorded rather than acted on: every path this elaborator resolves
    # is already handled with `/`, so accepting the declaration is what
    # matters, not re-parsing against a different character.
    _register(
        interp, state, "set_hierarchy_separator", _handle_set_hierarchy_separator,
        positional=["character"], optional_positional=["character"], requires_version=False,
    )

    _register(
        interp,
        state,
        "create_power_domain",
        _create_power_domain,
        options={"name", "default", "instances", "exclude_instances", "boundary_ports", "shutoff_condition", "default_restore_edge", "default_save_edge", "power_up_states"},
        list_options={"instances", "exclude_instances", "boundary_ports"},
        flag_options={"default"},
    )
    _register(
        interp,
        state,
        "update_power_domain",
        _update_power_domain,
        options={
            "name",
            "internal_power_net",
            "internal_ground_net",
            # The `-primary_*` spelling of the same two roles -- see
            # _update_power_domain()'s own comment for why both are
            # accepted and which one is the narrower concept.
            "primary_power_net",
            "primary_ground_net",
            "min_power_up_time",
            "max_power_up_time",
            "pmos_bias_net",
            "nmos_bias_net",
            "user_attributes",
            "rail_mapping",
            "library_set",
        },
        list_options={"user_attributes", "rail_mapping"},
    )
    _register(
        interp,
        state,
        "create_power_nets",
        _create_nets,
        options={"nets", "voltage", "external_shutoff_condition", "internal", "user_attributes", "peak_ir_drop_limit", "average_ir_drop_limit"},
        list_options={"nets", "user_attributes"},
        flag_options={"internal"},
    )
    _register(
        interp,
        state,
        "create_ground_nets",
        _create_nets,
        options={"nets", "voltage", "internal", "user_attributes", "peak_ir_drop_limit", "average_ir_drop_limit"},
        list_options={"nets", "user_attributes"},
        flag_options={"internal"},
    )
    # `create_global_connection -net net {-pins|-ports|-pg_type}
    # [-domain|-instances]` (Si2 CPF 2.1, p. 129) -- CPF's real construct
    # for connecting a supply net to pins/ports, and the counterpart of
    # UPF's `connect_supply_net -ports`. Normalised onto the SAME
    # DomainSupplyConnection records UPF's own form lands on, so a
    # consumer sees one representation regardless of source format.
    _register(
        interp,
        state,
        "create_global_connection",
        _create_global_connection,
        options={"net", "pins", "ports", "pg_type", "domain", "instances"},
        list_options={"pins", "ports", "instances"},
    )
    _register(
        interp,
        state,
        "create_power_switch_rule",
        _create_power_switch_rule,
        options={"name", "domain", "external_power_net", "external_ground_net"},
    )
    _register(
        interp,
        state,
        "create_isolation_rule",
        _create_isolation_rule,
        # `-secondary_domain power_domain` (Si2 CPF 2.1, p. 134) -- where
        # CPF states the isolation cell's own supply. NOT
        # `-isolation_power_net`/`-isolation_ground_net`, which occur
        # zero times in the CPF 1.0 AND 2.1 specs; see
        # _update_isolation_rules()'s own note.
        options={"name", "isolation_condition", "pins", "from", "to", "isolation_target", "isolation_output", "exclude", "secondary_domain", "no_condition", "isolation_control"},
        list_options={"pins", "from", "to", "exclude"},
    )
    _register(
        interp,
        state,
        "update_isolation_rules",
        _update_isolation_rules,
        options={
            "names", "location", "cells", "library_set", "prefix",
            "combine_level_shifting", "open_source_pins_only",
            # The isolation cells' own rails -- see
            # _update_isolation_rules()'s own comment.
            "isolation_power_net", "isolation_ground_net",
        },
        list_options={"names", "cells"},
        flag_options={"combine_level_shifting", "open_source_pins_only"},
    )
    _register(
        interp,
        state,
        "create_level_shifter_rule",
        _create_level_shifter_rule,
        options={"name", "pins", "from", "to", "exclude"},
        list_options={"pins", "from", "to", "exclude"},
    )
    _register(
        interp,
        state,
        "update_level_shifter_rules",
        _update_level_shifter_rules,
        options={"names", "location", "cells", "library_set", "prefix"},
        list_options={"names", "cells"},
    )
    _register(
        interp,
        state,
        # Singular `-name` here, unlike the two plural `-names` update
        # commands above -- CPF's own asymmetry, not a typo.
        "update_power_switch_rule",
        _update_power_switch_rule,
        # `-acknowledge_receiver_1`/`_2` are the CPF 2.x spelling (Si2
        # CPF 2.1, p. 266), paired with the correspondingly numbered
        # `-enable_condition_x`. The UNNUMBERED `-acknowledge_receiver`
        # is CPF 1.0's, where it took a pin rather than an expression --
        # still accepted on READ so a 1.0-era file keeps loading, the
        # same read-tolerantly/write-conservatively split this project
        # already applies to `set_isolation -name_prefix`.
        options={
            "name", "cells", "library_set", "prefix", "peak_ir_drop_limit",
            "acknowledge_receiver", "acknowledge_receiver_1", "acknowledge_receiver_2",
            "enable_condition_1", "enable_condition_2",
            "gate_bias_net", "average_ir_drop_limit",
        },
        list_options={"cells"},
    )
    _register(
        interp,
        state,
        "create_state_retention_rule",
        _create_state_retention_rule,
        options={"name", "domain", "instances", "restore_edge", "save_edge"},
        list_options={"instances"},
    )
    _register(
        interp,
        state,
        # A named voltage condition, not attached to any supply or domain
        # -- see _create_nominal_condition()'s own docstring.
        "create_nominal_condition",
        _create_nominal_condition,
        options={"name", "voltage", "state", "ground_voltage", "pmos_bias_voltage", "nmos_bias_voltage"},
    )
    _register(
        interp,
        state,
        "create_power_mode",
        _create_power_mode,
        options={"name", "domain_conditions", "default"},
        list_options={"domain_conditions"},
        flag_options={"default"},
    )

    return interp


def elaborate(top_file: str) -> PowerIntentDesign:
    """Elaborate a CPF design starting from `top_file`. Unlike UPF, there
    is no load_upf-equivalent verb -- if a real design spans multiple CPF
    files, that's plain Tcl `source`, which needs no special handling here.
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
    state.push_file(top_file)
    state.push_scope("")
    try:
        interp.eval(f"source {{{top_file}}}")
    finally:
        state.pop_scope()
        state.pop_file()
    if state.scratch.get("cpf_design_open"):
        raise ValueError(
            f"design rooted at '{top_file}' ends with an open set_design "
            f"'{state.scratch.get('cpf_current_design')}' block -- missing end_design"
        )
    return _freeze_all(state, SourceFormat.CPF)
