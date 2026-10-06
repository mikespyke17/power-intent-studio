""""Extract Power Intent" (explicit request): serializes the CURRENT
Power Intent diagram's own mock overlay state -- SupplyPortDiagramState +
PowerDomainDiagramState + RetentionDiagramState + PowerSwitchDiagramState
(supply_port_overlay.py/power_domain_overlay.py/retention_overlay.py/
power_switch_overlay.py) -- into UPF or CPF command text.

Deliberately NOT the same thing as a real PowerIntentDesign export
(backend/export/ -- still genuinely unbuilt, its own future,
milestone-adjacent piece per CLAUDE.md/docs/product_workflow.md). This
reads only the diagram's own mock state, never a real PowerIntentDesign,
and covers 7 concepts -- power supplies, supply sets, power domains,
retention strategies, power switches, level shifters, and macro PG-pin
connections (retention and power switches were each added on a bug
report: fully built on the GUI side, visible in the diagram and
Properties, but this generator was never updated to consume their own
diagram state at all -- not a filtering/exception-swallowing bug, a
genuine "never wired in" gap each time, confirmed by reading this file
before making either change -- see generate_upf()/generate_cpf()'s own
"# Power Switches" section; Level Shifter's own "# Level Shifters"
section was added new, alongside the rest of that feature, not as a
retrofit). Pure frontend, zero backend import, matching every other
diagram-only Milestone 3 piece.

Best-effort convenience output, not a certified spec-compliant writer:
straightforward create_supply_port/create_supply_set/create_power_domain/
create_power_switch/connect_supply_net-family commands (UPF) or
create_power_nets/create_power_domain/create_power_switch_rule/
connect_supply_net-family commands (CPF). None of the content this
export ever touches has version-gated syntax across this project's own
supported ranges (UPF 2.1/3.0/3.1, CPF 1.1/2.0) -- `version` only
affects the file's own header comment, not command shapes.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

from frontend.src.power_intent_view.isolation_overlay import ISOLATION_APPLIES_TO_ELEMENTS, IsolationDiagramState
from frontend.src.power_intent_view.level_shifter_overlay import LevelShifterDiagramState
from frontend.src.power_intent_view.macro_power_intent_overlay import MacroPowerIntentDiagramState, attached_file_format, relative_or_absolute_path
from frontend.src.power_intent_view.port_attribute_overlay import PortAttributeDiagramState, port_attribute_row_keys
from frontend.src.power_intent_view.power_domain_overlay import PowerDomainDiagramState, PowerDomainPlacement, SupplySetPlacement
from frontend.src.power_intent_view.power_state_table import (
    PowerStateTableDiagramState,
    _deduplicated_supply_nets,
    ordered_supply_nets,
)
from frontend.src.power_intent_view.power_switch_overlay import (
    SWITCH_OFF_STATE_NAME,
    PowerSwitchDiagramState,
    power_switch_connector_key,
    resolve_effective_states,
    substitute_control_signal,
)
from frontend.src.power_intent_view.retention_overlay import RetentionDiagramState
from frontend.src.power_intent_view.supply_port_overlay import (
    LEVELLED_STATE_TYPES,
    OFF,
    state_key,
    UNDETERMINED,
    SupplyPortDiagramState,
    SupplyPortPlacement,
    SupplyPortState,
    connected_supply_port,
)
from frontend.src.shared.models.rtl import Design, Port, PortDirection
from frontend.src.shared.models.rtl_io import module_io_ports

SUPPORTED_UPF_VERSIONS = ["3.0", "3.1"]
SUPPORTED_CPF_VERSIONS = ["2.0", "2.1"]

# Constructs the Si2 spec's own delta records show arriving in CPF 2.1
# (front-matter "Enhancements and New Capabilities for CPF 2.1" p. 9, and
# the per-command change log pp. 318-320). Emitting one into a file that
# declares 2.0 makes the file contradict its own version line, which is
# what the CPF re-verification audit found -- see
# docs/cpf2_reverification_audit.html, finding C4.
_CPF_2_1_ONLY_LOCATIONS = {"self", "automatic"}


def _cpf_supports_2_1(version: str) -> bool:
    """Whether `version` may carry a 2.1-only construct.

    Compared numerically rather than by string equality: "2.1" and any
    later version qualify, "2.0" does not."""
    try:
        return tuple(int(part) for part in version.split(".")) >= (2, 1)
    except ValueError:
        return False

# Section 7 (explicit request): the header's own "Generator" line names
# THIS tool's own version, independent of the UPF/CPF format version
# being written -- this project has no other version constant anywhere
# (confirmed by grep before adding one), so this is the single source
# of truth for it.
_TOOL_VERSION = "1.1"
_UNKNOWN_DESIGN_NAME = "Unknown"


def _ascii_whitespace(text: str) -> str:
    """Every generated character of whitespace is ASCII (review finding
    1.7).

    Tcl does not reliably treat U+00A0 and friends as word separators, so
    a single non-breaking space anywhere in a command silently changes
    how it parses. Nothing in this module WRITES one -- the risk is
    user-typed data flowing through: a name, a Cell Type, or an ON/OFF
    State expression pasted out of a spec PDF or a word processor carries
    NBSP invisibly, and there is no way to see it in a dialog field.

    Applied once to the whole finished file rather than per template, so
    it cannot be forgotten at a new call site. Newlines and tabs are
    real, meaningful ASCII whitespace and are left alone; every other
    whitespace character, ASCII or not, collapses to a plain space, and a
    zero-width character (BOM, ZWSP, word joiner) is dropped outright --
    it is not a separator at all, so replacing it with a space would
    change the parse rather than preserve it."""
    zero_width = {"\ufeff", "\u200b", "\u200c", "\u200d", "\u2060"}
    out = []
    for ch in text:
        if ch in ("\n", "\t"):
            out.append(ch)
        elif ch in zero_width:
            continue
        elif ch.isspace() or ord(ch) == 0xA0:
            out.append(" ")
        else:
            out.append(ch)
    return "".join(out)


def _relative_path(owner_path: str) -> str:
    """Strips the design's own root instance segment -- "top/u_core" ->
    "u_core"; "top" (root level, no subpath) -> "" -- the same convention
    properties_panel.py's _hierarchy_prefixed_name() already uses for
    display, applied here to a bare instance path instead of a name."""
    parts = owner_path.split("/") if owner_path else []
    return "/".join(parts[1:])


def _multiline_braced_list(flag: str, values: list[str]) -> str:
    """`-flag {\\n    v1\\n    v2\\n}` -- one value per line inside the
    brace group (explicit request, readability only: valid Tcl exactly
    as-is -- a brace-grouped value may span multiple lines, each
    newline-separated token still parses as its own list element, same
    underlying UPF/CPF construct and semantics as the single-line
    `-flag {v1 v2}` form it replaces). Empty string (not a list
    fragment) for an empty `values` -- the caller's own existing
    `if values:` guard already skips appending anything in that case,
    matching the pre-existing single-line helpers' own behavior."""
    if not values:
        return ""
    body = "\n".join(f"    {v}" for v in values)
    return f"{flag} {{\n{body}\n}}"


def _domain_is_nested_in(inner: PowerDomainPlacement, outer: PowerDomainPlacement) -> bool:
    """True if ANY of `inner`'s own owner_paths falls inside ANY of
    `outer`'s own owner_paths' subtrees -- generalizes the pre-existing
    single-owner_path nesting check to a multi-hierarchy domain
    (explicit request): a multi-hierarchy domain's own membership is a
    set of independent subtree roots, not one, so "nested inside" has
    to be checked pairwise across both domains' own full membership."""
    return any(
        inner_path == outer_path or inner_path.startswith(f"{outer_path}/") for inner_path in inner.owner_paths for outer_path in outer.owner_paths
    )


def _direct_excluded_children(domain: PowerDomainPlacement, all_domains: list[PowerDomainPlacement]) -> list[PowerDomainPlacement]:
    """Domains nested inside `domain`'s own subtree(s), minus any already
    nested inside another, closer nested domain -- mirrors
    power_domain_overlay.py's own apply_domain_coloring() cascade
    ("deepest/most-specific domain always wins" on a subtree overlap).
    Same logic as properties_panel.py's own helper of the same name --
    duplicated, not shared, matching this codebase's own precedent for a
    small (~10 line) overlap between two otherwise-independent modules
    (see filter_button.py's own docstring for the same reasoning)."""
    descendants = [d for d in all_domains if d.id != domain.id and _domain_is_nested_in(d, domain)]
    return [d for d in descendants if not any(other.id != d.id and _domain_is_nested_in(d, other) for other in descendants)]


def _voltage_by_name(supply_state: SupplyPortDiagramState) -> dict[str, str]:
    """name -> voltage (e.g. "0.80V"), across both plain supply ports and
    macro PG pins -- a Supply Set's own power/ground/n_well/p_well fields
    are just names, and the name could refer to either kind of
    placement. Voltage is only ever known for a macro's own PG pin (see
    SupplyPortPlacement's own docstring) -- a plain user-placed supply
    port's voltage is always None."""
    voltages: dict[str, str] = {}
    for placement in list(supply_state.supply_port_placements.values()) + list(supply_state.pg_pin_placements.values()):
        if placement.voltage:
            voltages.setdefault(placement.name, placement.voltage)
    return voltages


def _macro_connections(supply_state: SupplyPortDiagramState) -> list[tuple[str, str]]:
    """(net_name, "<instance>/<pin_name>") pairs -- one per drawn
    connector between a plain (non-fixed) supply port and a macro's own
    (fixed) PG pin. A connector between two plain supply ports, or
    between two PG pins, isn't a "macro connection" and is skipped."""
    connections: list[tuple[str, str]] = []
    for a_id, b_id in supply_state.supply_connector_placements:
        a = supply_state.supply_port_placements.get(a_id) or supply_state.pg_pin_placements.get(a_id)
        b = supply_state.supply_port_placements.get(b_id) or supply_state.pg_pin_placements.get(b_id)
        if a is None or b is None:
            continue
        a_is_pg = a_id in supply_state.pg_pin_placements
        b_is_pg = b_id in supply_state.pg_pin_placements
        if a_is_pg == b_is_pg:
            continue  # not a supply-port <-> PG-pin pair
        pg, net = (a, b) if a_is_pg else (b, a)
        pin_ref = f"{_relative_path(pg.owner_path)}/{pg.name}".lstrip("/")
        connections.append((net.name, pin_ref))
    return connections


def _flag_line(text: str, indent: int = 4) -> str:
    """One flag, on its own indented line (4 spaces, matching every
    pre-existing multi-line command's own convention -- `indent=2` for
    `set_isolation` specifically, the one real exception: golden.upf's
    own hand-written block uses a 2-space flag indent there, confirmed
    by direct inspection, not guessed) -- the single-line counterpart to
    _braced_list_flag() below, both meant to be handed to
    _render_multiline_command() as one "chunk" each."""
    return f"{' ' * indent}{text}"


def _braced_list_flag(flag: str, values: list[str], indent: int = 4) -> str:
    """`-flag {\\n        v1\\n        v2\\n    }` (no embedded
    continuation backslashes -- _render_multiline_command() adds those
    uniformly across every chunk it's handed, so this stays pure
    content). `indent`-space indent for the flag line (matching every
    other flag under the same command -- `indent=2` for `set_isolation`,
    see _flag_line()'s own docstring), 8-space indent for each value,
    4-space indent for the closing brace REGARDLESS of `indent` --
    confirmed directly against golden.upf's own set_isolation block,
    whose closing `}` sits at 4 spaces even though its own `-elements {`
    opening line sits at 2 -- the same nesting golden.upf's own
    hand-written -exclude_elements/-elements blocks use elsewhere too."""
    lines = [f"{' ' * indent}{flag} {{"]
    lines += [f"        {v}" for v in values]
    lines.append("    }")
    return "\n".join(lines)


# Real UPF 1801 `-clamp_value` takes a literal 0/1/hold (or Z/X), never
# the "low"/"high"/"keep" labels this app's own IsolationStrategyPlacement
# stores (matching properties_panel.py's own _ISOLATION_CLAMP_LABELS,
# a DISPLAY label set, not a UPF literal) -- confirmed against golden.upf's
# own `-clamp_value 0` for what this app's own dialog would store as "low".
_CLAMP_VALUE_UPF = {"low": "0", "high": "1", "keep": "hold"}

# The `-applies_to` values IEEE 1801 defines for set_isolation /
# set_level_shifter. The dialogs' fourth "Applies To All" choice,
# "elements", is NOT one of them -- see the Level Shifter section.
_UPF_APPLIES_TO_DIRECTIONS = ("inputs", "outputs", "both")

# Characters Tcl treats specially in a bare word.
_TCL_SPECIAL = set('[]$\\{}";') | set(" \t\n")


def _tcl_word(value: str) -> str:
    """A user-typed value as ONE literal Tcl word.

    UPF and CPF are Tcl: written bare, `iso_en_w[2]` is COMMAND
    SUBSTITUTION -- every Tcl-based reader (an EDA tool, this app's own
    elaborator) tries to run a command named `2` and rejects the file
    ("invalid command name"). Braced, it is the literal signal name. A
    value with nothing special stays bare, so ordinary output (and the
    reference golden's `-isolation_signal iso_n`) is unchanged."""
    text = str(value)
    return f"{{{text}}}" if any(ch in _TCL_SPECIAL for ch in text) else text


# A Retention Save/Restore Signal as the dialog stores it -- a signal,
# optionally inverted (`enable`, `!enable`, `~enable`) -- versus what UPF
# `set_retention_control -save_signal/-restore_signal` takes: a
# `{signal sense}` PAIR (`{enable high}`, `{enable low}`), the form the
# reference golden uses. The reverse lives in
# power_intent_import.py's own _retention_logic().
_SIGNAL_NAME = r"[A-Za-z_][A-Za-z0-9_$/.\[\]:]*"
_INVERTED_SIGNAL = re.compile(rf"^[!~]\s*({_SIGNAL_NAME})$")
_PLAIN_SIGNAL = re.compile(rf"^({_SIGNAL_NAME})$")
_SIGNAL_WITH_SENSE = re.compile(rf"^{_SIGNAL_NAME}\s+(high|low|posedge|negedge)$")


def _retention_signal(logic: str) -> str:
    """`enable` -> `enable high`, `!enable`/`~enable` -> `enable low`. A
    value already in `<signal> <sense>` form passes through. Anything
    else (a compound expression, which UPF's save/restore signal cannot
    express) is emitted verbatim rather than guessed at."""
    text = logic.strip()
    while text.startswith("(") and text.endswith(")"):
        text = text[1:-1].strip()
    if match := _INVERTED_SIGNAL.match(text):
        return f"{match.group(1)} low"
    if match := _PLAIN_SIGNAL.match(text):
        return f"{match.group(1)} high"
    if _SIGNAL_WITH_SENSE.match(text):
        return text
    return logic.strip()


def _element_bare_name(element: str) -> str:
    """The bare port/pin name from an Isolation/Level Shifter "Elements"
    key. Bug fix (found implementing Isolation export): Level Shifter's
    own pre-existing section below used _relative_path() here, which
    only strips the design's own ROOT segment, leaving inner hierarchy
    intact -- correct for a genuine hierarchy INSTANCE path (Power
    Domain's own -elements, Retention's own sequential-element paths),
    but wrong here: collect_isolation_elements_for_domain()'s own
    "<owner_path>/<port_name>" namespacing (see that function's own
    docstring, reused by Level Shifter's own Elements table) means an
    element key is a PORT/PIN name prefixed by its FULL owner_path, not
    a hierarchy instance itself -- every leading segment must go, not
    just the first one. The pre-existing test covering this
    (test_cpf_level_shifter_emits_create_level_shifter_rule_with_pins_and_a_comment)
    used a single-level-owner_path fixture ("top/clk") specifically
    contrived to make _relative_path() happen to produce the right
    answer ("clk") by coincidence, masking that a real, multi-level
    owner_path element (e.g. "sram/u_sub/o_q[31:0]") would have been
    silently blanked to an empty string instead."""
    return element.rsplit("/", 1)[-1]


def _render_multiline_command(head: str, flag_chunks: list[str]) -> str:
    """UPF's real backslash-continuation style (matching golden.upf,
    e.g. `create_power_domain PD_TOP \\` / `    -elements {.} \\` / ...):
    every line gets a trailing ` \\` except the very last line of the
    very last chunk. A chunk may itself be multi-line (see
    _braced_list_flag() above) -- its own internal lines get exactly the
    same treatment, so a braced-list flag that isn't the command's last
    one still ends with `    } \\`, not a bare `    }`."""
    if not flag_chunks:
        return head
    all_lines = [head]
    for chunk in flag_chunks:
        all_lines.extend(chunk.split("\n"))
    last = len(all_lines) - 1
    return "\n".join(line + (" \\" if i < last else "") for i, line in enumerate(all_lines))


def _pad_name_column(names: list[str], min_gap: int = 1) -> dict[str, str]:
    """name -> name left-justified to (longest name + min_gap) -- the
    simple, uniform column-alignment rule golden.upf's own hand-formatted
    `create_supply_set`/`connect_supply_net` blocks follow (confirmed by
    measuring golden.upf's own column widths directly, not guessed --
    the "Connect external ports"/Supply Set blocks measure a 1-space
    gap, the "Macro Connections" block a 2-space gap; both are matched
    exactly via the caller's own min_gap rather than picking one value
    for both and drifting from golden on the other)."""
    width = max((len(n) for n in names), default=0) + min_gap
    return {n: n.ljust(width) for n in names}


# The switch-contributed OFF state as written in UPF. On screen it is
# SWITCH_OFF_STATE_NAME ("SWITCH-OFF"), but `-` is not valid in a UPF
# state name, so every writer below uses this spelling instead.
UPF_SWITCH_OFF_STATE_NAME = "SWITCH_OFF"


def _upf_state_name(name: str) -> str:
    """A state name as UPF writes it: the switch-contributed
    "SWITCH-OFF" becomes "SWITCH_OFF"; any other name is unchanged."""
    return UPF_SWITCH_OFF_STATE_NAME if name == SWITCH_OFF_STATE_NAME else name


def _upf_state_entry(state: SupplyPortState) -> tuple[str, str]:
    """(state name, state value) for one `-state {name value}` entry.
    "off" state_type always renders the literal value "off" -- UNLESS
    it's a synthesized switch-traversal state (resolve_effective_states()'s
    own `f"SWITCH-OFF ({sw.name})"` naming, meant for on-screen display,
    not UPF text) -- golden.upf's own convention for that case is a
    flat "SWITCH_OFF" name with a numeric "0.0" value, not "off"."""
    if state.state_type == "off":
        if state.name.startswith(SWITCH_OFF_STATE_NAME):
            return UPF_SWITCH_OFF_STATE_NAME, "0.0"
        return state.name, "off"
    return state.name, state.voltage or "0.0"


def _add_power_state_command(name: str, states: list[SupplyPortState]) -> str:
    flag_chunks = [_flag_line(f"-state {{{n} {v}}}") for n, v in (_upf_state_entry(s) for s in states)]
    return _render_multiline_command(f"add_power_state {name}", flag_chunks)


def _reorder_terminal_off_last(states: list[SupplyPortState]) -> list[SupplyPortState]:
    """A real terminal "OFF" state (state_type=="off", not one of
    resolve_effective_states()'s own synthesized "SWITCH-OFF (...)"
    entries) always sorts to the END of the list -- matches golden.upf's
    own VDD_SUB ordering (`ON 1.2`, `SWITCH_OFF 0.0`, `OFF off`): a
    synthesized switch-traversal state is a TRANSITIONAL state on the way
    to fully off, so it reads more naturally before the real terminal off
    than after it. Stable sort -- everything else (ON, synthesized
    switch-off entries) keeps its existing relative order."""
    return sorted(states, key=lambda s: s.state_type == "off" and not s.name.startswith("SWITCH-OFF"))


def _power_state_commands(supply_state: SupplyPortDiagramState, power_switch_state: Optional[PowerSwitchDiagramState]) -> list[str]:
    """One `add_power_state` command per UNIQUE supply NAME that has
    states to show -- a plain Supply Port's own user-authored `states`
    list unchanged, an Internal Supply's live-resolved EFFECTIVE states
    (resolve_effective_states() -- its own `states` field is always
    empty, see SupplyPortPlacement's own docstring; a real UPF
    `add_power_state` needs the actual inherited values, not nothing).

    Deduplicated by NAME (not by placement): the cross-hierarchy
    "reuse an existing supply name" mechanism can legitimately create
    TWO placements sharing one name -- a plain Supply Port at its
    original hierarchy plus an auto-linked Internal Supply elsewhere,
    both representing the same real net (see
    _auto_link_cross_hierarchy_supply_sets()'s own docstring) -- and real
    UPF has no "declare the same power state twice" construct. The named
    Supply Port's own literal states win when a name appears in both
    groups (its states are the real, user-authored source of truth; the
    Internal Supply's own resolved-effective copy of the same name
    would just be tracing straight back to it anyway).

    Named ports first (alphabetical), Internal Supplies after
    (alphabetical) -- matches golden.upf's own grouping for this section
    specifically (its Supply Nets section, by contrast, merges both
    kinds into one flat alphabetical list -- a deliberate, observed
    difference between the two sections, not an inconsistency)."""
    switches = power_switch_state.switches if power_switch_state is not None else {}
    named = sorted(
        (p for p in supply_state.supply_port_placements.values() if not p.is_internal_supply and p.states),
        key=lambda p: p.name,
    )
    internal = sorted((p for p in supply_state.supply_port_placements.values() if p.is_internal_supply), key=lambda p: p.name)
    seen_names: set[str] = set()
    commands = []
    for p in named:
        commands.append(_add_power_state_command(p.name, p.states))
        seen_names.add(p.name)
    for p in internal:
        if p.name in seen_names:
            continue
        effective = _reorder_terminal_off_last(resolve_effective_states(p, supply_state, switches))
        if effective:
            commands.append(_add_power_state_command(p.name, effective))
            seen_names.add(p.name)
    return commands


_HEADER_RULE = "#" * 80
_HEADER_LABEL_WIDTH = 15  # "Design"/"Format"/"Generated"/"Generator" all align on the same ":" column


def _header_field(label: str, value: str) -> str:
    return f"#  {label:<{_HEADER_LABEL_WIDTH}}: {value}"


def _header(format_name: str, version: str, design_name: Optional[str], extra: list[str] | None = None) -> list[str]:
    """Always present at the top of every generated file (explicit
    request, exact block/spacing specified): a boxed banner naming the
    tool, the copyright notice, and Design/Format/Generated/Generator
    fields -- a commented-out block, never live UPF/CPF syntax.
    `design_name` is the top module's own name (resolved by the caller
    from whatever RTL Design is currently loaded) -- `_UNKNOWN_DESIGN_NAME`
    when none has been loaded yet (this export doesn't require one)."""
    lines = [
        _HEADER_RULE,
        "#",
        "#  Power Intent Studio",
        "#  Unified Power Format (UPF/CPF) Generator",
        "#",
        f"#  Copyright (c) {datetime.now().year} Power Intent Studio. All rights reserved.",
        "#",
        "#  This file was automatically generated. Manual edits may be",
        "#  overwritten on the next generation pass.",
        "#",
        _header_field("Design", design_name or _UNKNOWN_DESIGN_NAME),
        _header_field("Format", f"{format_name} {version}"),
        _header_field("Generated", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        _header_field("Generator", f"Power Intent Studio v{_TOOL_VERSION}"),
        "#",
    ]
    if extra:
        lines += extra
    lines += [_HEADER_RULE, ""]
    return lines


def _section_header(title: str) -> list[str]:
    """Every SECTION's own heading (explicit request: match the file-level
    banner's own boxed `_HEADER_RULE` convention -- golden.upf/golden.cpf
    both wrap every section title the identical way) -- reuses the exact
    same 80-`#` rule the top-of-file banner already uses, rather than a
    second, differently-sized one."""
    return [_HEADER_RULE, f"# {title}", _HEADER_RULE]


def _join_blocks(blocks: list[list[str]]) -> list[str]:
    """Exactly ONE blank line between each distinct instance's block --
    never inside one, never leading, never trailing.

    THE separator rule for every multi-instance section in both formats
    (explicit request). One helper rather than a per-section `if i <
    len(...) - 1: append("")`, which is what those sections grew
    independently and is exactly how they drifted: some separated
    instances, some separated nothing, and two put the blank INSIDE an
    instance (between a switch and its own `map_power_switch`, and
    before a group's closing `set_scope .`).

    An empty block contributes nothing, so a skipped instance -- a
    dangling reference, an unset optional -- cannot leave a double blank
    or a stray one behind."""
    out: list[str] = []
    for block in blocks:
        if not block:
            continue
        if out:
            out.append("")
        out.extend(block)
    return out


# ---------------------------------------------------------------------------
# Isolation Rules
# ---------------------------------------------------------------------------


def _isolation_upf_lines(
    isolation_state: Optional[IsolationDiagramState],
    domain_names: dict[str, str],
    supply_sets: dict[str, SupplySetPlacement],
    widths: Optional[dict[str, int]] = None,
) -> list[str]:
    """Real UPF 1801 `set_isolation` syntax, verified directly against
    golden.upf's own hand-written block (this project's own backend UPF
    parser doesn't yet recognize `-isolation_signal`/`-isolation_sense`
    among its accepted `set_isolation` options -- a backend-side gap,
    same "match real syntax/golden files over an incomplete backend
    accept-list" precedent Power Switch's own -input_supply_port/
    -output_supply_port already established in this exact module).
    `-domain`/name share ONE head line (unlike every other multi-line
    command in this file, which keeps `-domain` as its own flag chunk)
    -- confirmed by direct inspection of golden.upf's own block, along
    with its own 2-space flag indent (see _flag_line()'s own docstring
    for why this differs from every other section here). `-source`/
    `-sink` ARE emitted now, when set: they used to be withheld for
    lack of a verified example, and the reference golden
    (design/single_clock_design) supplies one."""
    if not isolation_state or not isolation_state.strategies:
        return []
    lines = _section_header("Isolation Rules")
    # Root scope, like every other construct (see the FLAT, ROOT-SCOPED
    # note): the domain and supply set are named bare, the elements by
    # their root-relative path.
    blocks: list[list[str]] = []
    for iso in sorted(isolation_state.strategies.values(), key=lambda i: i.name):
        domain_name = domain_names.get(iso.power_domain_id)
        if domain_name is None:
            continue  # dangling reference -- the domain it was created against no longer exists
        head = f"set_isolation {iso.name} -domain {domain_name}"
        flag_chunks = []
        # `-source`/`-sink`, when the dialog's optional Source/Sink Supply
        # Set is set (reference golden: the reference golden carries
        # both, right after the head line). Absent -> no flag.
        for flag, set_id in (("-source", iso.source_supply_set_id), ("-sink", iso.sink_supply_set_id)):
            endpoint = supply_sets.get(set_id) if set_id else None
            if endpoint is not None:
                flag_chunks.append(_flag_line(f"{flag} {_upf_reference(endpoint)}", indent=2))
        supply_set = supply_sets.get(iso.isolation_supply_set_id)
        if supply_set is not None:
            flag_chunks.append(_flag_line(f"-isolation_supply_set {_upf_reference(supply_set)}", indent=2))
        # Every optional flag is GUARDED. These three used to be emitted
        # unconditionally, which wrote the literal text "None" for an
        # unset value -- and that parses cleanly, so it came back as the
        # STRING 'None' rather than as absent: silent corruption, not a
        # visible failure. A strategy created through the dialog always
        # has them set, which is why the round-trip suite never saw it;
        # an IMPORTED one legitimately may not. Found by re-exporting a
        # hand-authored fixture -- see
        # tests/frontend/golden_simple10/README.md, finding 20.
        if iso.clamp_value:
            flag_chunks.append(_flag_line(f"-clamp_value {_CLAMP_VALUE_UPF.get(iso.clamp_value, iso.clamp_value)}", indent=2))
        if iso.isolation_control:
            flag_chunks.append(_flag_line(f"-isolation_signal {_tcl_word(iso.isolation_control)}", indent=2))
        if iso.isolation_sense:
            flag_chunks.append(_flag_line(f"-isolation_sense {iso.isolation_sense}", indent=2))
        if iso.applies_to != ISOLATION_APPLIES_TO_ELEMENTS:
            # "Applies To All" Inputs/Outputs/Both: the whole domain
            # boundary in that direction, IEEE 1801 `-applies_to` --
            # never alongside `-elements`.
            flag_chunks.append(_flag_line(f"-applies_to {iso.applies_to}", indent=2))
        elif iso.elements:
            # Root-relative PATH, not the bare port name: the command is
            # emitted at the root, so `count` alone would name a port of
            # the top module rather than of u_sub/u_core. `[*]` on a
            # multi-bit port -- see _upf_element().
            element_paths = sorted(_upf_element(e, widths or {}) for e in iso.elements)
            flag_chunks.append(_braced_list_flag("-elements", element_paths, indent=2))
        if iso.location:
            flag_chunks.append(_flag_line(f"-location {iso.location}", indent=2))
        blocks.append([_render_multiline_command(head, flag_chunks)])
    lines += _join_blocks(blocks)
    lines.append("")
    return lines


def _isolation_cpf_lines(
    isolation_state: Optional[IsolationDiagramState],
    domain_names: dict[str, str],
    supply_sets: dict[str, SupplySetPlacement],
    version: str = "2.0",
    secondary_domain_for_supply_set: Optional[dict[str, str]] = None,
) -> list[str]:
    """Real CPF `create_isolation_rule`/`update_isolation_rules` syntax,
    verified against this project's OWN backend CPF parser
    (backend/elaborator/power_intent/cpf/verbs.py's own
    `_create_isolation_rule`/`_update_isolation_rules` registration:
    `-name`/`-from`/`-to`/`-pins`/`-isolation_condition`/`-isolation_output`
    for the create verb; `-names`/`-location` for update) AND golden.cpf's
    own hand-written block -- `-isolation_power_net`/`-isolation_ground_net`
    on `update_isolation_rules` are real 1801-family CPF flags (matching
    golden.cpf's own literal usage exactly, resolved from the isolation
    supply SET's own `.power`/`.ground` fields -- golden.cpf's own
    `VDD`/`VSS` there are SS_VDD's power/ground, not PD_SUB's own primary
    supply VDD_SUB/VSS, confirmed by direct inspection) that this
    project's OWN backend CPF parser doesn't accept yet either (logged
    in CLAUDE.md's own CPF section as a deferred `update_*` command -- a
    backend-side gap, not a reason to omit them here). `-to`/
    `-diode_supply` have no golden.cpf example to verify against and are
    intentionally not emitted, same "don't guess" precedent Retention/
    Level Shifter's own CPF sections already establish. Unlike
    `set_isolation`'s own UPF block, this one uses the SAME 4-space flag
    indent every other multi-line CPF/UPF command in this file already
    uses (confirmed by direct inspection of golden.cpf's own block --
    genuinely different from `set_isolation`'s 2-space UPF convention,
    not an inconsistency)."""
    if not isolation_state or not isolation_state.strategies:
        return []
    secondary_domain_for_supply_set = secondary_domain_for_supply_set or {}
    unsupported_locations: list[tuple[str, str]] = []
    unresolved_isolation_supplies: list[tuple[str, str]] = []
    lines = _section_header("Isolation Rules")
    iso_blocks: list[list[str]] = []
    for iso in sorted(isolation_state.strategies.values(), key=lambda i: i.name):
        domain_name = domain_names.get(iso.power_domain_id)
        if domain_name is None:
            continue  # dangling reference -- the domain it was created against no longer exists
        create_flags = [_flag_line(f"-from {domain_name}")]
        if iso.isolation_control:
            create_flags.append(_flag_line(f"-isolation_condition {{{iso.isolation_control}}}"))
        if iso.clamp_value:  # guarded -- see the UPF writer's own note
            create_flags.append(_flag_line(f"-isolation_output {iso.clamp_value}"))
        if iso.elements:
            element_names = sorted(_element_bare_name(e) for e in iso.elements)
            create_flags.append(_braced_list_flag("-pins", element_names))
        # The rule and its own `update_isolation_rules` refinement are
        # ONE instance, so no blank line between them -- there used to
        # be one, which read as a section break mid-rule.
        # `-isolation_power_net`/`-isolation_ground_net` are GONE (audit
        # finding C3). Both strings occur ZERO times in the CPF 1.0 spec
        # AND zero times in the CPF 2.1 spec -- they are not CPF syntax
        # in any version. They came from this project's own golden.cpf,
        # and the backend CPF parser was extended to accept them, which
        # encoded the same mistake.
        #
        # CPF states the isolation cell's own supply on the CREATE
        # command, as a DOMAIN rather than a net pair:
        # `create_isolation_rule ... [-secondary_domain power_domain]`
        # (Si2 CPF 2.1, p. 134). Only emitted when the isolation Supply
        # Set is genuinely some domain's own primary supply set -- that
        # is the one case where a domain name can be resolved without
        # guessing. Otherwise the supply is reported, not fabricated.
        ss = supply_sets.get(iso.isolation_supply_set_id)
        if ss is not None:
            secondary = secondary_domain_for_supply_set.get(iso.isolation_supply_set_id)
            if secondary is not None:
                create_flags.append(_flag_line(f"-secondary_domain {secondary}"))
            elif ss.power or ss.ground:
                unresolved_isolation_supplies.append((iso.name, ss.name))
        block = [_render_multiline_command(f"create_isolation_rule -name {iso.name}", create_flags)]

        update_flags = []
        if iso.location:
            # `-location self`/`automatic` are CPF 2.1 ADDITIONS (audit
            # finding C4): "Isolation Enhancements -- Added support for
            # location self and automatic" (Si2 CPF 2.1, p. 9). Emitting
            # one under `set_cpf_version 2.0` makes the file contradict
            # its own version line, so at 2.0 it is reported instead of
            # asserted. CPF 1.0 was narrower still -- `{from | to}` only.
            if iso.location in _CPF_2_1_ONLY_LOCATIONS and not _cpf_supports_2_1(version):
                unsupported_locations.append((iso.name, iso.location))
            else:
                update_flags.append(_flag_line(f"-location {iso.location}"))
        # `-prefix string`: "Specifies the prefix to be used when creating
        # the isolation logic. Default: CPF_ISO_" -- read directly out of
        # the Si2 CPF 1.0 primary spec. A real flag, so asserted rather
        # than commented. Note the ASYMMETRY this creates: CPF has a
        # verified home for an isolation name prefix and UPF's own
        # set_isolation does NOT (see _isolation_upf_lines()), which is
        # the reverse of this project's usual "UPF-only, no CPF
        # equivalent" shape.
        if iso.name_prefix:
            update_flags.append(_flag_line(f"-prefix {_tcl_word(iso.name_prefix)}"))
        if update_flags:  # nothing to refine -> no command at all
            block.append(_render_multiline_command(f"update_isolation_rules -names {iso.name}", update_flags))
        iso_blocks.append(block)
    lines += _join_blocks(iso_blocks)
    # What could not be asserted, said out loud rather than dropped --
    # the same "no clean equivalent, report it" convention every other
    # CPF gap in this module already follows.
    for name, location in unsupported_locations:
        lines.append(f"#   {name}: -location {location} needs CPF 2.1 (added there; this file declares {version}) -- omitted")
    for name, set_name in unresolved_isolation_supplies:
        lines.append(f"#   {name}: isolation supply '{set_name}' is not any power domain's own primary supply set,")
        lines.append("#   so it has no -secondary_domain to name here (CPF states this as a DOMAIN, not a net pair)")
    lines.append("")
    return lines


# ---------------------------------------------------------------------------
# Port Attributes
# ---------------------------------------------------------------------------


def _port_display_name(port: Port) -> str:
    return port.name if port.width <= 1 else f"{port.name}[{port.width - 1}:0]"


def _port_attribute_groups(
    top_level_ports: list[Port], assignments: dict[str, Optional[str]], supply_set_names: dict[str, str]
) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """(driver_ports_by_supply_set_name, receiver_ports_by_supply_set_name)
    -- one whole-PORT display name (see _port_display_name(), e.g.
    "a[7:0]", never per-bit "a[0]".."a[7]") per port ASSIGNED a Supply
    Set, grouped by that Supply Set's own name. A multi-bit port only
    contributes an entry when EVERY one of its own bits (see
    port_attribute_row_keys()) shares the IDENTICAL assignment -- a
    genuinely mixed per-bit assignment has no honest single "the whole
    port" representation, so it's skipped here rather than guessed at
    (real UPF has no "half this port" port attribute). IN contributes to
    the driver group, OUT to the receiver group, INOUT to both (matching
    `set_port_attributes -driver_supply`/`-receiver_supply`'s own real
    UPF meaning and this app's own Port Attribute dialog semantics for
    INOUT -- see port_attribute_dialog.py's own module docstring)."""
    driver: dict[str, list[str]] = {}
    receiver: dict[str, list[str]] = {}
    for port in top_level_ports:
        keys = port_attribute_row_keys(port)
        values = [assignments.get(k) for k in keys]
        if not values or values[0] is None or any(v != values[0] for v in values):
            continue
        ss_name = supply_set_names.get(values[0])
        if ss_name is None:
            continue
        display = _port_display_name(port)
        if port.direction in (PortDirection.IN, PortDirection.INOUT):
            driver.setdefault(ss_name, []).append(display)
        if port.direction in (PortDirection.OUT, PortDirection.INOUT):
            receiver.setdefault(ss_name, []).append(display)
    return driver, receiver


def _set_port_attributes_command(port_names: list[str], flag: str, ss_name: str) -> str:
    """`set_port_attributes -ports {\\n        p1\\n...\\n    } \\n    -<flag>
    <ss_name>` -- a THIRD distinct multi-line shape in this file (unlike
    every other multi-line command, `-ports {` is merged directly onto
    the command's own head line, at 0 indent, confirmed by direct
    inspection of golden.upf's own block -- not a generic
    _render_multiline_command()/_braced_list_flag() case)."""
    lines = ["set_port_attributes -ports {"]
    lines += [f"        {p}" for p in port_names]
    lines.append("    }")
    lines.append(f"    -{flag} {ss_name}")
    last = len(lines) - 1
    return "\n".join(line + (" \\" if i < last else "") for i, line in enumerate(lines))


def _port_attributes_upf_lines(
    port_attribute_state: Optional[PortAttributeDiagramState], top_level_ports: Optional[list[Port]], supply_set_names: dict[str, str]
) -> list[str]:
    if not port_attribute_state or not port_attribute_state.assignments or not top_level_ports:
        return []
    driver, receiver = _port_attribute_groups(top_level_ports, port_attribute_state.assignments, supply_set_names)
    if not driver and not receiver:
        return []
    lines = _section_header("Port Attributes")
    lines += _join_blocks(
        [[_set_port_attributes_command(port_names, "driver_supply", ss_name)] for ss_name, port_names in sorted(driver.items())]
        + [[_set_port_attributes_command(port_names, "receiver_supply", ss_name)] for ss_name, port_names in sorted(receiver.items())]
    )
    lines.append("")
    return lines


def _domain_shutoff_condition(domain, power_switch_state) -> Optional[str]:
    """The OFF State of the Power Switch that powers `domain`, or None.

    CPF states this on the domain rather than on the switch rule -- the
    switch's enable pin is then driven by its inversion (Si2 CPF 2.1,
    create_power_switch_rule, p. 173). Read from the switch's own Off
    State, expressed in terms of the real RTL control signal, since that
    is what CPF wants here (a Boolean function of design pins) rather
    than the abstract port name the UPF writer substitutes toward.

    None when the domain has no switch, or its switch has no Off State
    -- Off State is optional in the dialog, and a fabricated condition
    would assert a power-down behaviour the user never stated."""
    if power_switch_state is None:
        return None
    for switch in power_switch_state.switches.values():
        if switch.power_domain_id == domain.id and switch.off_state:
            return switch.off_state
    return None


def _boundary_ports_for_root_domain(
    domain: PowerDomainPlacement, port_attribute_state: Optional[PortAttributeDiagramState], top_level_ports: Optional[list[Port]], supply_set_names: dict[str, str]
) -> list[str]:
    """CPF has no separate "Port Attributes" section at all (explicit,
    confirmed via golden.cpf's own structure) -- every assigned port,
    regardless of driver/receiver direction, folds directly into the
    design's own ROOT-level domain's `-boundary_ports` flag instead.
    Only ever non-empty for the domain anchored at the design's own root
    (`_relative_path(owner_path) == ""` for at least one of its own
    owner_paths) -- Port Attribute assignments are scoped to the design's
    single top-level block only (see port_attribute_overlay.py's own
    module docstring), so no other domain could ever have boundary ports
    to report here anyway."""
    if not port_attribute_state or not port_attribute_state.assignments or not top_level_ports:
        return []
    if not any(_relative_path(p) == "" for p in domain.owner_paths):
        return []
    driver, receiver = _port_attribute_groups(top_level_ports, port_attribute_state.assignments, supply_set_names)
    ordered: list[str] = []
    seen: set[str] = set()
    for port_names in list(driver.values()) + list(receiver.values()):
        for name in port_names:
            if name not in seen:
                ordered.append(name)
                seen.add(name)
    return ordered


# ---------------------------------------------------------------------------
# Power State Table
# ---------------------------------------------------------------------------


def _valid_power_state_table_rows(power_state_table_state: Optional[PowerStateTableDiagramState], net_ids: list[str]) -> list:
    """Every row with a non-blank State Name and a value for EVERY
    current net column -- an incomplete row has no honest single-line
    PST/power-mode representation, so it's skipped here (matching
    "Check Power Intent"'s own empty-cell rule, applied at export time
    instead of as a UI diagnostic). Insertion order (creation order),
    NOT alphabetical -- matches golden.upf's own FULL_ON/RETENTION/IDLE
    ordering (the order the states were actually defined in), same
    "creation order reads more naturally" precedent Power Domains'
    own section already establishes."""
    if power_state_table_state is None:
        return []
    return [
        row
        for row in power_state_table_state.rows.values()
        if row.state_name.strip() and all(row.net_states.get(net_id) for net_id in net_ids)
    ]


# ---------------------------------------------------------------------------
# Power States -- the two-tier model
# ---------------------------------------------------------------------------
#
# REPLACES both the old per-net independent `add_power_state` generation
# and the old Power State Table -> legacy-PST translation. Neither
# survives: per-net states are no longer independent facts (they are the
# rails a Supply Set's own states are composed FROM), and a PST cannot
# express a supply-set-level model at all.
#
#   TIER 1, per Supply Set: walk every Power State Table row, read that
#   set's own four rails out of the row's per-net selections, and combine
#   them into one `-supply_expr`. Identical combinations across rows
#   collapse into ONE derived state, reused by every row that maps to it.
#   Named generically in order of first appearance -- STATE_1, STATE_2,
#   ... -- scoped per Supply Set, so STATE_1 means nothing across two
#   different sets.
#
#   TIER 2, one global group for the whole design: one
#   `create_power_state_group <TOP>_PST`, then one `-state` per table row
#   whose `-logic_expr` names each relevant Supply Set == its own Tier 1
#   state for that row.
#
# No `-complete` anywhere (explicit confirmation, reversing an earlier
# worked example that included it).

_SUPPLY_SET_FUNCTIONS = ("power", "ground", "nwell", "pwell")


def _row_rail_key(row, rail_name: Optional[str], nets_by_name: dict) -> Optional[str]:
    """What one Power State Table row selects for one Supply Set rail.

    A rail names a supply NET; the row stores its selection per net id.
    Returns the canonical state key (`FULL_ON 1.2`, `OFF`, ...), or None
    when the set has no such rail or the row left it unselected."""
    if not rail_name:
        return None
    net = nets_by_name.get(rail_name)
    if net is None:
        return None
    return row.net_states.get(net.id)


def _supply_expr(rail_keys: dict[str, str]) -> str:
    """`power == {FULL_ON 1.2} && ground == {FULL_ON 0.0}` -- one term per
    rail the Supply Set actually declares, in a fixed function order so
    two identical combinations always produce identical text (which is
    what makes deduplication by expression sound)."""
    terms = []
    for function in _SUPPLY_SET_FUNCTIONS:
        key = rail_keys.get(function)
        if key is None:
            continue
        terms.append(f"{function} == {{{key}}}")
    return " && ".join(terms)


def _simstate(rail_keys: dict[str, str]) -> str:
    """CORRUPT if ANY rail is OFF or UNDETERMINED, NORMAL otherwise."""
    unpowered = (OFF, UNDETERMINED)
    return "CORRUPT" if any(key.split(" ")[0] in unpowered for key in rail_keys.values()) else "NORMAL"


# HISTORICAL NOTE, kept because this decision reversed twice and the
# reasoning is easy to re-derive wrongly.
#
# A power switch's ports were once named by FIXED tokens
# (IN/OUT/CTRL/ACK) that `create_power_switch` declared, with
# `map_power_switch -port_map` bridging each to the user's own field
# value. That came out of an external review, whose own text was
# internally inconsistent on the point: one item wrote `-control_port
# {<Control Port value> <Control Signal value>}` -- the field AS the port
# name -- while its corrected example and its `-port_map` shape both used
# fixed tokens. The tokens were implemented because only they made
# `-port_map` mean anything.
#
# That is now retired by explicit decision: the four field values ARE the
# port names, emitted directly, and `-port_map` is not generated at all.
# So the reading the review's own `-control_port` formula implied is the
# one that stands, and the indirection it needed is gone with it.


# FLAT, ROOT-SCOPED UPF (reference golden, reverses review finding 1.4
# and 1.5).
#
# Every UPF construct -- supply nets, supply sets, power domains,
# retention, power switches, isolation, level shifters, port/PST states
# -- is declared at the design's ROOT scope. A construct that belongs to
# a nested hierarchy says so through ROOT-RELATIVE instance/element paths
# (`-elements {u_sub/u_core}`, `-elements {u_sub/u_core/count}`), never
# through a `set_scope <hierarchy>` wrapper.
#
# This replaces the earlier scope-wrapped shape (each nested construct
# inside its own `set_scope` ... `set_scope .`, with `-reuse` for a
# same-named net continued into a child scope and scope-qualified
# references such as `u_sub/SS_SUB` from the root). That shape was
# derived from an external review. The flat form is the reference
# golden's (tests/frontend/golden_upf_output/single_clock_design/
# main_top.fixed.upf), and it is the form this generator now produces.
#
# Flattening is sound for this app's own data model, which is why no
# name mangling is needed:
#   - Supply Set and Power Domain names are already unique design-wide.
#   - Two supply placements sharing a NAME are, by this app's own rule,
#     one continuous net (the cross-hierarchy auto-link and ancestor
#     Supply Set sharing both rely on it) -- so one root-level
#     `create_supply_net` per distinct name is exactly that net, and the
#     `-reuse` replicas have nothing left to express.
#
# So every reference is the construct's bare NAME.


def _upf_reference(placement) -> str:
    """How any UPF command names a supply net, supply set or switch: its
    bare name, since every one of them is declared at the root scope
    (see the FLAT, ROOT-SCOPED note above)."""
    return placement.name


def _signal_widths(rtl_design: Optional[Design]) -> dict[str, int]:
    """"<instance path>/<signal>" -> bit width, for every port and net of
    every instance in the design. A strategy element key is exactly that
    shape (a whole port for Isolation/Level Shifter, a flop's whole
    output signal for Retention), so this is what tells a multi-bit
    element from a scalar one. Empty when no design is loaded."""
    widths: dict[str, int] = {}
    if rtl_design is None:
        return widths
    for node in rtl_design.walk():
        # Interface-exposed signals included (`u_cpu/mem_port.awaddr`),
        # so a multi-bit one gets its `[*]` too.
        for port in module_io_ports(rtl_design, node.definition):
            widths[f"{node.path}/{port.name}"] = port.width
        for net in node.definition.nets:
            widths.setdefault(f"{node.path}/{net.name}", net.width)
    return widths


def _upf_element(element: str, widths: dict[str, int]) -> str:
    """A strategy element as written in `-elements`: root-relative, and
    -- for a MULTI-BIT signal -- suffixed `[*]` (the reference golden
    writes `u_data_subsystem/u_counter/count[*]`).

    An element key always names the WHOLE signal (one per port, one per
    flop output), so every bit is selected and `[*]` is always the right
    suffix. A key that already carries its own bit-select is left as
    authored, and a scalar -- or a signal whose width is unknown -- gets
    no suffix."""
    path = _relative_path(element)
    if "[" in element.rsplit("/", 1)[-1] or widths.get(element, 1) <= 1:
        return path
    return f"{path}[*]"


def _derive_supply_set_states(supply_state, domain_state, power_state_table_state):
    """Tier 1 for every Supply Set.

    Returns (states_by_set, row_state_by_set) where `states_by_set` maps a
    set name to its ordered [(STATE_N, supply_expr, simstate)] and
    `row_state_by_set` maps (set name, row id) -> the STATE_N that row
    resolved to. The second is what Tier 2 needs; deriving both in one
    pass is what guarantees they agree."""
    rows = list(power_state_table_state.rows.values()) if power_state_table_state else []
    nets_by_name = {
        net.name: net
        for net in _deduplicated_supply_nets(supply_state)
    } if rows else {}

    states_by_set: dict[str, list[tuple[str, str, str]]] = {}
    row_state_by_set: dict[tuple[str, str], str] = {}
    for supply_set in sorted(domain_state.supply_sets.values(), key=lambda s: s.name):
        # Used as the key for BOTH returned dicts so Tier 1's `-supply`
        # and Tier 2's `-logic_expr` cannot disagree about how the same
        # set is named.
        reference = _upf_reference(supply_set)
        rails = {
            "power": supply_set.power,
            "ground": supply_set.ground,
            "nwell": supply_set.n_well,
            "pwell": supply_set.p_well,
        }
        by_expr: dict[str, str] = {}
        derived: list[tuple[str, str, str]] = []
        for row in rows:
            rail_keys = {
                function: key
                for function, rail_name in rails.items()
                if (key := _row_rail_key(row, rail_name, nets_by_name)) is not None
            }
            # RELEVANCE turns on the POWER rail, not on any rail.
            #
            # The spec scopes a row's -logic_expr to "the Supply Sets that
            # domain's rows reference", which left one case genuinely
            # ambiguous: two sets routinely SHARE a ground rail, so a row
            # selecting only SS_A's rails also, incidentally, matches
            # SS_B's ground. Constraining SS_B on that basis asserts
            # something about a set the row says nothing about -- and
            # produces a thin `ground ==` only expression to do it.
            #
            # So a set participates only when the row selects its own
            # POWER rail, the rail that defines it (and the one a domain's
            # condition and colour both derive from). A set with no power
            # rail at all falls back to "any rail", so it is not silently
            # excluded outright.
            defining = "power" if rails.get("power") else None
            if not rail_keys or (defining and defining not in rail_keys):
                continue  # this row says nothing about this set
            expr = _supply_expr(rail_keys)
            name = by_expr.get(expr)
            if name is None:
                name = f"STATE_{len(derived) + 1}"
                by_expr[expr] = name
                derived.append((name, expr, _simstate(rail_keys)))
            row_state_by_set[(reference, row.id)] = name
        if derived:
            states_by_set[reference] = derived
    return states_by_set, row_state_by_set


def _supply_set_states_upf_lines(states_by_set) -> list[str]:
    """Tier 1's own generated text -- one `add_power_state -supply` per
    Supply Set. Deliberately NO `-complete`."""
    if not states_by_set:
        return []
    lines = _section_header("Supply Set Power States")
    instances: list[list[str]] = []
    for set_name, derived in states_by_set.items():
        blocks = []
        for name, expr, simstate in derived:
            blocks.append(
                f"    -state {{ {name} \\\n"
                f"        -supply_expr {{ {expr} }} \\\n"
                f"        -simstate {simstate} \\\n"
                f"    }}"
            )
        instances.append([f"add_power_state -supply {set_name} \\\n" + " \\\n".join(blocks)])
    lines += _join_blocks(instances)
    lines.append("")
    return lines


def _power_state_group_upf_lines(
    design_name, power_state_table_state, domain_state, row_state_by_set
) -> list[str]:
    """Tier 2: ONE global `create_power_state_group` for the whole
    design, covering every Power Domain (explicit confirmation -- not one
    group per domain).

    Each row's `-logic_expr` constrains only the Supply Sets that row
    actually says something about, leaving sets belonging to unrelated
    domains unconstrained. The GROUP stays single and global regardless
    of how many domains contribute rows."""
    rows = list(power_state_table_state.rows.values()) if power_state_table_state else []
    rows = [r for r in rows if r.state_name.strip()]
    if not rows or not row_state_by_set:
        return []
    group = f"{design_name or 'DESIGN'}_PST"
    lines = _section_header("Power State Group")
    lines.append(f"create_power_state_group {group}")
    lines.append("")
    blocks = []
    for row in rows:
        terms = [
            f"{set_name} == {state_name}"
            for (set_name, row_id), state_name in sorted(row_state_by_set.items())
            if row_id == row.id
        ]
        if not terms:
            continue
        joined = " && \\\n                ".join(terms)
        blocks.append(
            f"    -state {{ {row.state_name.strip()} \\\n"
            f"        -logic_expr {{ \\\n"
            f"                {joined} \\\n"
            f"        }} \\\n"
            f"    }}"
        )
    if not blocks:
        return []
    lines.append(f"add_power_state -group {group} \\\n" + " \\\n".join(blocks))
    lines.append("")
    return lines


# The command a legacy per-net state declaration uses, chosen by whether
# the net is PORT-BACKED.
#
# `add_port_state` is for an object that genuinely IS a port -- which
# here means a plain Supply Port, since those are the only nets this
# generator also emits a `create_supply_port` for. An Internal Supply and
# a Virtual Supply are each emitted as `create_supply_net` ALONE, with no
# port declaration anywhere, so neither is a port.
#
# UNVERIFIED, and flagged rather than presented as checked: `add_port_state`
# is confirmed (its shape, and its use on both a supply port and a power
# switch's own output port, is documented and appears throughout this
# project's own aura_top reference). `add_supply_state` could NOT be
# confirmed from any source checked -- it appears in no reference found,
# in no file in this repository, and one search explicitly reported the
# standard command as `add_power_state` instead. It is emitted here
# because it was explicitly specified; the constant is isolated so
# changing it is a one-line edit.
#
# Worth recording, because it is the one real-world data point: aura_top
# faces exactly this case -- VDD_CPU/VDD_DSP/VDD_PER are
# `create_supply_net` with no port -- and solves it WITHOUT any such
# command, by putting the driving power switch's own output port
# (`SW_CPU/vout`) in the PST instead, which is a port and takes
# `add_port_state`.
_PORT_STATE_COMMAND = "add_port_state"
_SUPPLY_STATE_COMMAND = "add_supply_state"


def _legacy_state_command(net) -> str:
    """`add_port_state` for a port-backed net, `add_supply_state`
    otherwise -- see _PORT_STATE_COMMAND's own note."""
    port_backed = not net.is_internal_supply and not net.is_virtual_supply
    return _PORT_STATE_COMMAND if port_backed else _SUPPLY_STATE_COMMAND


def _legacy_state_names(states) -> dict[str, str]:
    """One single-token NAME per state, keyed by its `state_key()`.

    Legacy UPF references a state by name and nothing else, so every
    state a table row can select needs one. An authored name is used
    as-is -- that is the whole point of reintroducing the field. A state
    that arrived without one (only ever from an IMPORT, since the dialog
    requires a name) gets its TYPE as the label, suffixed only where
    that would otherwise collide inside the same net.

    Deriving rather than skipping is deliberate: skipping left the state
    undeclared, which in turn dropped every table row selecting it --
    silently, and without the row being wrong. The derived label states
    nothing the state does not already say.
    """
    names: dict[str, str] = {}
    taken = {st.name for st in states if st.name}
    for st in states:
        key = state_key(st)
        if st.name:
            # UPF spelling: "SWITCH-OFF" -> "SWITCH_OFF" (`-` is not
            # valid in a UPF state name).
            names[key] = _upf_state_name(st.name)
            continue
        candidate, n = st.state_type, 1
        while candidate in taken:
            n += 1
            candidate = f"{st.state_type}_{n}"
        taken.add(candidate)
        names[key] = candidate
    return names


_PST_NAME = "PST"
# Prefix every `add_pst_state` name carries (reference golden, see
# design/single_clock_design/upf_fixed/main_top.fixed.upf; the reason
# confirmed by the user). A table row is typically named after the
# supply state it selects -- FULL_ON, OFF -- and FULL_ON is UPF's own
# predefined supply-state keyword; the prefix keeps a PST state from
# ever clashing with it (or with this file's `add_port_state` names). Export-only, like the rest of the
# legacy block (`add_port_state` is not registered in the elaborator, so
# a legacy file does not re-import), which is why the importer does not
# strip it: there, it would only rename a hand-written file's states.
PST_STATE_PREFIX = f"{_PST_NAME}_"


def _pst_state_name(state_name: str) -> str:
    """A table row's State Name as `add_pst_state` writes it -- prefixed
    with PST_STATE_PREFIX, once (a name the user already prefixed is not
    doubled)."""
    name = state_name.strip()
    return name if name.startswith(PST_STATE_PREFIX) else f"{PST_STATE_PREFIX}{name}"


def _power_state_table_upf_lines(power_state_table_state: Optional[PowerStateTableDiagramState], supply_state: SupplyPortDiagramState, domain_state: PowerDomainDiagramState, switches: Optional[dict] = None) -> list[str]:
    """Real legacy-PST UPF syntax (`create_pst`/`add_pst_state`), matching
    golden.upf's own block exactly -- genuinely NO Power Domain concept
    at all in this construct (unlike CPF's own `-domain_conditions`, see
    _power_state_table_cpf_lines()'s own docstring), so every valid row
    is included regardless of which single Power Domain this app's own
    per-row scoping happens to reference. Column order comes straight
    from ordered_supply_nets() -- this app's own natural Power-then-
    Ground, creation-order net ordering, NOT necessarily matching
    whatever literal order a Prompt/spec described (an accepted,
    expected difference, not a bug: forcing a specific literal order
    would mean hardcoding scenario-specific knowledge into an otherwise
    general-purpose exporter)."""
    nets = ordered_supply_nets(supply_state, domain_state)
    if not nets:
        return []
    rows = _valid_power_state_table_rows(power_state_table_state, [n.id for n in nets])
    if not rows:
        return []
    # EFFECTIVE states, not authored ones. An Internal Supply authors
    # none of its own -- its states are traced through the switch chain
    # back to the real source -- so reading `.states` directly left every
    # Internal Supply out of this section entirely while `add_pst_state`
    # rows still referenced names for it. Same resolver that already
    # powers the "Inherited Power States" preview and Tier 1's own
    # `-supply_expr`, so all three agree about what a net's states are.
    effective_by_net: dict[str, list] = {
        net.id: resolve_effective_states(net, supply_state, switches or {})
        for net in nets
    }
    # A net's per-state NAMES, keyed by the state_key() a table cell
    # stores. The table stores the physical identity ("FULL_ON 1.2") and
    # legacy UPF references the authored NAME ("ON"), so this is the
    # translation between them -- built per net, because a name is only
    # unique within its own supply.
    names_by_net: dict[str, dict[str, str]] = {
        net.id: _legacy_state_names(effective_by_net[net.id]) for net in nets
    }

    lines = _section_header("Port State")
    # Every state each net can be in, declared by NAME with its own
    # value, before the table references those names positionally. One
    # blank line between each net's own block -- these are distinct
    # instances, and they used to run together.
    state_blocks: list[list[str]] = []
    for net in nets:
        states = effective_by_net[net.id]
        if not states:
            continue
        names = names_by_net[net.id]
        state_flags = [
            _flag_line(f"-state {{{names[state_key(st)]} {st.voltage if st.state_type in LEVELLED_STATE_TYPES and st.voltage else 'off'}}}")
            for st in states
        ]
        command = _legacy_state_command(net)
        state_blocks.append([_render_multiline_command(f"{command} {_upf_reference(net)}", state_flags)])
    lines += _join_blocks(state_blocks)
    lines.append("")

    lines += _section_header("Power State Table")
    # Bare net names: every net is declared at the root scope, where
    # `create_pst` is emitted too (see the FLAT, ROOT-SCOPED note).
    supplies = " ".join(_upf_reference(n) for n in nets)
    lines.append(f"create_pst {_PST_NAME} -supplies {{{supplies}}}")
    lines.append("")
    name_padded = _pad_name_column([_pst_state_name(r.state_name) for r in rows])

    def _referenced(net, row) -> Optional[str]:
        """The NAME this row selects for this net, or None if the row
        selects a state this net no longer has.

        `add_pst_state`'s list is POSITIONAL -- one token per net, in the
        order `create_pst -supplies` declared -- so whatever goes here
        must be exactly ONE token. The physical key is TWO ("FULL_ON
        1.2"), which is why it cannot be the fallback: a single such
        entry silently shifts every later column by one. The name from
        `_legacy_state_names()` is always one token, so the only
        unresolvable case left is a STALE row -- see the filter below."""
        key = row.net_states[net.id]
        return names_by_net.get(net.id, {}).get(key)

    # A row is only expressible here when every net's selected state
    # still exists on that net. That is a stale row, not an unnamed
    # state: `_legacy_state_names()` gives every real state a name, so
    # nothing is dropped merely for having arrived unnamed from an
    # import.
    rows = [r for r in rows if all(_referenced(n, r) is not None for n in nets)]
    if not rows:
        return []

    col_width = [max(len(_referenced(n, r) or "") for r in rows) + 2 for n in nets]
    for r in rows:
        values = []
        for i, n in enumerate(nets):
            value = _referenced(n, r) or ""
            values.append(value.ljust(col_width[i]) if i < len(nets) - 1 else value)
        lines.append(f"add_pst_state {name_padded[_pst_state_name(r.state_name)]}-pst {_PST_NAME} -state {{{''.join(values)}}}")
    # Trailing blank line, like every other section here -- without it
    # this block runs straight into the next section's header banner.
    lines.append("")
    return lines


def _cpf_power_state_lines(
    supply_state, domain_state, power_state_table_state, row_state_by_set
) -> list[str]:
    """CPF's own two tiers, and how far they actually go.

    RESEARCHED against the Si2 CPF 1.0 specification directly (the
    freely-available primary source), not assumed. CPF is ALREADY
    structurally two-tier, which is why this needed reshaping rather than
    inventing:

      - `create_nominal_condition -name <s> -voltage <float>
        [-pmos_bias_voltage] [-nmos_bias_voltage]` -- a named operating
        condition. Note what it is NOT: a single VOLTAGE, with no
        ground/nwell/pwell composition. CPF has no supply-set construct
        at all, so a Tier 1 multi-rail `-supply_expr` has no equivalent
        and cannot be given one.
      - `create_power_mode -name <s> -domain_conditions <list>
        [-default]` -- per POWER DOMAIN, not per supply set.

    So the honest mapping is: a domain's condition in a row is the state
    of its PRIMARY supply set's POWER rail, which is exactly what a CPF
    nominal condition describes. Ground/nwell/pwell rails, and the
    supply-set grouping itself, are not expressible and are reported.

    Two real rules taken straight from the spec rather than guessed:

      - "A power domain is switched off if it is associated with a
        nominal condition whose voltage is 0." So OFF is a 0.0 V
        condition -- NOT a `-state off` flag, which CPF 1.0's own
        create_nominal_condition does not define.
      - "If you define any power mode, you must define one (and only
        one) power mode as the default mode." So the first row gets
        `-default`, and exactly one does.

    UNDETERMINED has no voltage and therefore no condition; a row using
    it omits that domain rather than inventing a level."""
    rows = [r for r in (power_state_table_state.rows.values() if power_state_table_state else []) if r.state_name.strip()]
    if not rows or not row_state_by_set:
        return []
    sets_by_id = domain_state.supply_sets
    nets_by_name = {net.name: net for net in _deduplicated_supply_nets(supply_state)}

    # TIER 1: one nominal condition per distinct voltage level needed.
    conditions: dict[str, str] = {}  # rail key -> condition name
    ordered: list[tuple[str, str]] = []  # (condition name, voltage)
    domain_conditions: dict[str, list[str]] = {}  # row id -> ["PD@COND", ...]
    for row in rows:
        terms = []
        for domain in sorted(domain_state.domains.values(), key=lambda d: d.name):
            supply_set = sets_by_id.get(domain.primary_supply_set)
            if supply_set is None or not supply_set.power:
                continue
            key = _row_rail_key(row, supply_set.power, nets_by_name)
            if key is None or key.split(" ")[0] == UNDETERMINED:
                continue
            head, _, level = key.partition(" ")
            voltage = "0.0" if head == OFF else level
            if not voltage:
                continue
            name = conditions.get(key)
            if name is None:
                name = f"COND_{len(ordered) + 1}"
                conditions[key] = name
                ordered.append((name, voltage))
            terms.append(f"{domain.name}@{name}")
        if terms:
            domain_conditions[row.id] = terms
    if not ordered:
        return []

    lines = _section_header("Nominal Conditions")
    padded = _pad_name_column([name for name, _ in ordered])
    lines += _join_blocks([
        [f"create_nominal_condition -name {padded[name]}-voltage {voltage}"]
        for name, voltage in ordered
    ])
    lines.append("")

    # TIER 2: one power mode per row, exactly one marked default.
    lines += _section_header("Power Modes")
    mode_blocks: list[list[str]] = []
    first = True
    for row in rows:
        terms = domain_conditions.get(row.id)
        if not terms:
            continue
        default = " -default" if first else ""
        first = False
        mode_blocks.append([
            f"create_power_mode -name {row.state_name.strip()} "
            f"-domain_conditions {{{' '.join(terms)}}}{default}"
        ])
    lines += _join_blocks(mode_blocks)
    # A note about the SECTION, not about the last mode -- kept with the
    # section it explains, after one blank line.
    lines.append("")
    lines.append(
        "#   A CPF nominal condition is a single VOLTAGE, and a power mode maps POWER"
    )
    lines.append(
        "#   DOMAINS to conditions -- so each domain's condition above is its PRIMARY"
    )
    lines.append(
        "#   supply set's POWER rail. CPF has no supply-set construct, so the"
    )
    lines.append(
        "#   ground/nwell/pwell rails and the supply-set grouping itself are not"
    )
    lines.append("#   expressible here.")
    lines.append("")
    return lines


def _power_state_table_cpf_lines(power_state_table_state: Optional[PowerStateTableDiagramState], supply_state: SupplyPortDiagramState, domain_state: PowerDomainDiagramState) -> list[str]:
    """CPF's real `create_power_mode -domain_conditions {DOMAIN@STATE ...}`
    needs a per-DOMAIN on/off condition per mode -- data this app's own
    PowerStateTableRow genuinely doesn't have (one row references exactly
    ONE Power Domain, for its own uniqueness-scoping only; its actual
    values are per-NET, not per-domain). Synthesizing `-domain_conditions`
    would mean guessing a domain-level ON/OFF condition from net-level
    state values with no honest, general mapping between the two --
    same "not confident enough to assert a fabricated flag, comment
    instead" precedent Retention/Power Switch/Level Shifter's own CPF
    sections already establish repeatedly in this file. `-name` is
    real/asserted; the full net-state mapping is surfaced in an
    informational comment instead."""
    nets = ordered_supply_nets(supply_state, domain_state)
    if not nets:
        return []
    rows = _valid_power_state_table_rows(power_state_table_state, [n.id for n in nets])
    if not rows:
        return []
    lines = _section_header("Power State Table")
    for r in rows:
        lines.append(f"create_power_mode -name {r.state_name}")
        mapping = " ".join(f"{n.name}={r.net_states[n.id]}" for n in nets)
        lines.append(f"#   {r.state_name}: {mapping}")
    lines.append("")
    return lines


# ---------------------------------------------------------------------------
# Power States (CPF)
# ---------------------------------------------------------------------------


def _power_states_cpf_lines(supply_state: SupplyPortDiagramState, power_switch_state: Optional[PowerSwitchDiagramState]) -> list[str]:
    """Real CPF `create_nominal_condition` syntax, matching golden.cpf's
    own block exactly -- a real, confirmed gap (generate_cpf() had NO
    Power States section at all before this, unlike generate_upf()'s own
    `add_power_state`, found while implementing the Power State Table
    directly alongside it). Unlike UPF's `add_power_state` (one command
    PER SUPPLY, every one of its own states listed together), CPF's own
    `create_nominal_condition` is a single, globally-reusable NAMED
    condition -- one command per UNIQUE STATE NAME across the WHOLE
    design, not per supply (confirmed against golden.cpf's own exactly-3-
    entries: ON/SWITCH_OFF/OFF, covering all 4 supplies' own distinct
    state names between them) -- assumes every occurrence of the same
    state NAME shares the same voltage, matching real UPF/CPF's own
    reusable-named-condition semantics. Reuses `_power_state_commands()`'s
    own named-then-internal supply gathering/dedup-by-name precedent, but
    deduplicates by STATE NAME across ALL supplies together (that
    function's own dedup is only ever by SUPPLY name)."""
    switches = power_switch_state.switches if power_switch_state is not None else {}
    named = sorted((p for p in supply_state.supply_port_placements.values() if not p.is_internal_supply and p.states), key=lambda p: p.name)
    internal = sorted((p for p in supply_state.supply_port_placements.values() if p.is_internal_supply), key=lambda p: p.name)
    seen_supply_names: set[str] = set()
    seen_state_names: set[str] = set()
    entries: list[tuple[str, str]] = []
    for p in named:
        seen_supply_names.add(p.name)
        for s in p.states:
            name, value = _upf_state_entry(s)
            if name not in seen_state_names:
                entries.append((name, value))
                seen_state_names.add(name)
    for p in internal:
        if p.name in seen_supply_names:
            continue
        for s in _reorder_terminal_off_last(resolve_effective_states(p, supply_state, switches)):
            name, value = _upf_state_entry(s)
            if name not in seen_state_names:
                entries.append((name, value))
                seen_state_names.add(name)
    if not entries:
        return []
    lines = _section_header("Power States")
    name_padded = _pad_name_column([name for name, _ in entries])
    for name, value in entries:
        flag = "-voltage 0.0 -state off" if value == "off" else f"-voltage {value}"
        lines.append(f"create_nominal_condition -name {name_padded[name]}{flag}")
    lines.append("")
    return lines


# --- Macro power-intent attachment (explicit request) --------------------
#
# UPF has a real construct for this: `load_upf -file <path> -instance
# <instance>` -- already used by this project's own backend UPF
# elaborator for hierarchical `load_upf` scoping (see CLAUDE.md's
# Milestone 2a). Emitted here as this generator's own final section, one
# line per macro instance with an attached file.
#
# CPF does NOT -- confirmed by direct research against the Si2 CPF 1.0
# primary specification (fetched and read directly, not assumed): there
# is no CPF command that lets one CPF file reference/load an external
# CPF OR UPF file for a specific hierarchical instance. CPF's own
# "hierarchical integration" (composing a block-level power-intent
# description into a top-level one) is a multi-file, EDA-TOOL-level flow
# -- separate CPF files given to the tool as distinct inputs, associated
# via set_instance/set_design, never a command inside the CPF language
# itself. This holds regardless of whether the attached file is UPF or
# CPF -- it is not only the cross-format (UPF-attached/CPF-target) case
# that's unexpressible, same-format (CPF-attached/CPF-target) has the
# identical gap, since the gap is "CPF has no file-loading command at
# all," not "CPF can't load a foreign format." Per this project's own
# "no clean equivalent -- report it, don't fabricate a workaround"
# convention (see e.g. CPF's own create_power_mode/-domain_conditions
# gap above), _macro_power_intent_cpf_comment_lines() below emits a
# comment naming every un-expressible attachment instead of a bogus
# command, and macro_power_intent_cpf_warnings() gives the app's own UI
# layer the same information as a generation-time diagnostic.


def _macro_power_intent_upf_lines(macro_power_intent_state: Optional[MacroPowerIntentDiagramState], output_dir: Optional[str]) -> list[str]:
    if not macro_power_intent_state or not macro_power_intent_state.attachments:
        return []
    lines = _section_header("Load Upf")
    blocks: list[list[str]] = []
    for instance_path in sorted(macro_power_intent_state.attachments):
        file_path = macro_power_intent_state.attachments[instance_path]
        resolved_path = relative_or_absolute_path(file_path, output_dir) if output_dir else file_path
        blocks.append([f"load_upf -file {resolved_path} -instance {_relative_path(instance_path)}"])
    lines += _join_blocks(blocks)
    lines.append("")
    return lines


def _macro_power_intent_cpf_comment_lines(macro_power_intent_state: Optional[MacroPowerIntentDiagramState]) -> list[str]:
    if not macro_power_intent_state or not macro_power_intent_state.attachments:
        return []
    lines = _section_header("Load Upf")
    lines.append("# CPF has no construct equivalent to UPF's load_upf -- confirmed via")
    lines.append("# direct research against the Si2 CPF specification (see this module's")
    lines.append("# own comment above _macro_power_intent_upf_lines()). The following")
    lines.append("# macro instance(s) have a power intent file attached in the diagram,")
    lines.append("# but it cannot be expressed here -- not emitted as a CPF command:")
    for instance_path in sorted(macro_power_intent_state.attachments):
        file_path = macro_power_intent_state.attachments[instance_path]
        lines.append(f"#   {_relative_path(instance_path)}: {file_path} ({attached_file_format(file_path).upper()})")
    lines.append("")
    return lines


def macro_power_intent_cpf_warnings(macro_power_intent_state: Optional[MacroPowerIntentDiagramState]) -> list[str]:
    """One human-readable warning per macro instance with an attached
    power intent file -- for the app's own generation-time diagnostic
    when exporting to CPF (explicit request). Every attachment triggers
    this, not just the UPF-attached/CPF-target combination originally
    asked about: CPF has no in-language file-loading construct at all
    (confirmed by research, not assumed -- see this module's own comment
    above _macro_power_intent_upf_lines()), so a CPF-attached file hits
    the identical gap when the export target is also CPF."""
    if not macro_power_intent_state:
        return []
    warnings = []
    for instance_path, file_path in sorted(macro_power_intent_state.attachments.items()):
        warnings.append(
            f"'{_relative_path(instance_path)}' has a power intent file attached "
            f"({attached_file_format(file_path).upper()}: {file_path}), but CPF has no "
            "construct to express a per-instance attached power intent file -- omitted "
            "from the generated CPF output (see the file's own 'Load Upf' section comment)."
        )
    return warnings


def isolation_name_prefix_upf_warnings(isolation_state: Optional[IsolationDiagramState]) -> list[str]:
    """An Isolation Name Prefix has no VERIFIED home in UPF.

    RESEARCHED, and reported rather than guessed. `-name_prefix` is
    confirmed on `set_level_shifter` and is emitted there. On
    `set_isolation` it could not be confirmed from any of five
    references checked: every published option list for that command
    omits it, including a real tool implementation's. The naming-prefix
    concept does exist for isolation, but in UPF 1.0's
    `create_isolation_logic -prefix` -- a command superseded by
    `set_isolation` in UPF 2.0+ and not one this app emits at all.

    Absence of evidence is not proof here: the same tool reference's
    `set_isolation` list also omits `-elements`, `-isolation_supply_set`,
    `-source` and `-sink`, which are unquestionably real and which this
    app already emits. So the honest state is UNCERTAIN, and this
    project's standing rule for that is to report, never to assert an
    unverified flag into a generated file.

    CPF, by contrast, has a verified home for it
    (`update_isolation_rules -prefix`), which is the reverse of the usual
    "UPF-only construct with no CPF equivalent" shape."""
    if not isolation_state:
        return []
    return [
        f"Isolation '{strategy.name}' has Name Prefix '{strategy.name_prefix}'. UPF's "
        "set_isolation has no confirmed -name_prefix option (it is verified on "
        "set_level_shifter, and emitted there), so the prefix is omitted from the generated "
        "UPF. It IS exported to CPF, as 'update_isolation_rules -prefix'."
        for strategy in sorted(isolation_state.strategies.values(), key=lambda s: s.name)
        if strategy.name_prefix
    ]


def power_switch_ack_cpf_warnings(power_switch_state: Optional[PowerSwitchDiagramState]) -> list[str]:
    """What an acknowledge pair loses on the way to CPF.

    The SIGNAL survives -- `update_power_switch_rule
    -acknowledge_receiver` is a real CPF construct and is emitted (see
    the CPF Power Switches section). The cell's own acknowledge PIN does
    not: CPF states it on `define_power_switch_cell -stage_1_output`/
    `-stage_2_output`, a library-cell definition command this app has no
    model for and does not generate. Reported through the same "Not
    Expressible in CPF" path the macro-attachment and supply-port-
    direction gaps already use, rather than silently dropped."""
    if not power_switch_state:
        return []
    warnings = []
    for switch in sorted(power_switch_state.switches.values(), key=lambda s: s.name):
        if not switch.ack_port:
            continue
        warnings.append(
            f"Power Switch '{switch.name}' has Ack Port '{switch.ack_port}'. CPF states a switch "
            "cell's acknowledge output pin on 'define_power_switch_cell -stage_1_output/"
            "-stage_2_output', a library-cell definition this tool does not generate, so the pin "
            "name is omitted from the generated CPF."
            + (
                f" The acknowledge SIGNAL '{switch.ack_signal}' IS exported, as "
                "'update_power_switch_rule -acknowledge_receiver'."
                if switch.ack_signal else ""
            )
        )
    return warnings


def supply_port_direction_cpf_warnings(supply_state: Optional[SupplyPortDiagramState]) -> list[str]:
    """One warning per Supply Port carrying a non-empty Direction, for
    the app's own generation-time diagnostic when exporting to CPF.

    CPF HAS NO EQUIVALENT, and this was researched rather than assumed --
    read directly out of the Si2 Common Power Format Specification v1.0
    (the freely-available primary source this project already uses for
    every other CPF question), not inferred from a vendor page:

      - CPF has no supply-PORT construct at all to hang a direction on.
        `create_supply_port`, `create_power_port`, `create_ground_port`
        and even the phrase "supply port" return ZERO hits in the whole
        specification. CPF declares supply NETS
        (`create_power_nets`/`create_ground_nets`) and names a design's
        or a domain's ports separately (`set_design -ports`,
        `create_power_domain -boundary_ports`) -- none of which carries a
        direction, because a port's direction comes from the RTL.
      - `-direction` DOES exist in CPF, on exactly one command:
        `define_level_shifter_cell -direction {up|down|bidir}`. That is
        voltage-translation direction, an entirely different concept, and
        emitting a supply port's in/out/inout through it would be
        fabrication.
      - The nearest ADJACENT concept is `create_power_nets -internal`,
        which distinguishes an internal net from a boundary-crossing one.
        That is not direction either -- and this app already models that
        distinction separately, as Internal Supply.

    So, per this project's standing "no clean equivalent -- report it,
    don't fabricate a workaround" convention (the same one already
    applied to Supply Sets, per-net Power States, `-available_supplies`
    and macro power-intent attachments), CPF generation emits nothing for
    Direction and the user is told, rather than the value being silently
    dropped or squeezed into an unrelated flag."""
    if not supply_state:
        return []
    warnings = []
    ports = [
        p for p in supply_state.supply_port_placements.values()
        if p.direction and not p.is_internal_supply and not p.is_virtual_supply and not p.fixed
    ]
    for p in sorted(ports, key=lambda p: (p.name, p.owner_path)):
        warnings.append(
            f"Supply Port '{p.name}' has Direction '{p.direction}', but CPF has no construct for a "
            "supply port's direction -- CPF has no supply-port construct at all, and its only "
            "-direction flag belongs to define_level_shifter_cell (up/down/bidir), a different "
            "concept. The direction is omitted from the generated CPF output."
        )
    return warnings


def generate_upf(
    supply_state: SupplyPortDiagramState,
    domain_state: PowerDomainDiagramState,
    retention_state: RetentionDiagramState,
    version: str,
    power_switch_state: Optional[PowerSwitchDiagramState] = None,
    level_shifter_state: Optional[LevelShifterDiagramState] = None,
    design_name: Optional[str] = None,
    isolation_state: Optional[IsolationDiagramState] = None,
    port_attribute_state: Optional[PortAttributeDiagramState] = None,
    top_level_ports: Optional[list[Port]] = None,
    power_state_table_state: Optional[PowerStateTableDiagramState] = None,
    # WHICH power-state representation this file carries. EXCLUSIVE:
    # True writes the UPF 2.x-era `add_port_state`/`create_pst`/
    # `add_pst_state` block and NO modern sections; False writes the
    # supply-set states and Power State Group and NO legacy block.
    #
    # This was `include_legacy_pst`, an ADDITIVE flag that put the legacy
    # table alongside the modern group. That reading is retired: the two
    # representations can no longer coexist in one generated file, and
    # the parameter is a straight either/or so no caller can ask for
    # both. Defaults to modern, so an export nobody changed is
    # byte-identical to before.
    legacy_power_states: bool = False,
    macro_power_intent_state: Optional[MacroPowerIntentDiagramState] = None,
    output_dir: Optional[str] = None,
    # The loaded RTL design, for signal WIDTHS only: a multi-bit strategy
    # element is written `<path>[*]` (see _upf_element()). Optional --
    # without it every element is written as a bare path, as before.
    rtl_design: Optional[Design] = None,
) -> str:
    lines = _header("UPF", version, design_name)
    widths = _signal_widths(rtl_design)
    # A real version DECLARATION, not just the header's comment field.
    # Round-trip import exposed the gap: the version appeared only inside
    # the commented banner, so a generated file never actually declared
    # it and re-reading failed with "called before any version
    # declaration" -- a generated file has to be self-describing.
    #
    # `upf_version`, NOT `set_upf_version` (review finding 1.1): the
    # latter is not real UPF syntax. This project's own golden.upf used
    # it, and this generator copied it from there, so the wrong spelling
    # was consistent throughout without ever being correct. The
    # elaborator accepts both, which is why nothing here failed.
    lines += [f"upf_version {version}", "set_scope .", ""]

    # Supply Port is a true boundary port (create_supply_port); Internal
    # Supply is an internal-only net -- create_supply_port declares a
    # boundary connection point, create_supply_net alone declares a net
    # with no such boundary crossing. Supply Nets is a single flat,
    # alphabetically-sorted list across BOTH kinds (matches golden.upf's
    # own structure exactly: VDD, VDD_BKP, VDD_SUB, VSS in one block --
    # deliberately NOT split into a separate "Internal Supplies"
    # sub-heading the way an earlier version of this generator did; the
    # boundary-vs-internal distinction is already fully visible from the
    # Supply Ports section above, which only ever lists named ports).
    # Virtual Supply is excluded from `named_ports` (no create_supply_port
    # -- it's never a real boundary port, see add_virtual_supply()'s own
    # docstring in power_intent_diagram_view.py) but still counted into
    # `all_supply_names` below via its own `virtual_supplies` list, so it
    # still gets a plain create_supply_net -- explicit request, spec
    # section 2: "emitted as a STANDARD SUPPLY NET with its own declared
    # power states... treated as first-class, not special-cased."
    named_ports = [p for p in supply_state.supply_port_placements.values() if not p.is_internal_supply and not p.is_virtual_supply]
    internal_supplies = [p for p in supply_state.supply_port_placements.values() if p.is_internal_supply]
    virtual_supplies = [p for p in supply_state.supply_port_placements.values() if p.is_virtual_supply]
    if named_ports:
        lines += _section_header("Supply Ports")
        # PACKED, not one blank line per declaration (refined rule): in
        # the three declaration sections a blank line marks a SCOPE
        # CHANGE, not an instance boundary. Supply ports are all at the
        # root, so this section never has one inside it.
        for p in sorted(named_ports, key=lambda p: p.name):
            # `-direction` only when one was chosen. "" is a real user
            # choice meaning "omit the flag", not a missing value, so
            # there is nothing to default here -- see
            # SupplyPortPlacement.direction.
            if p.direction:
                lines.append(f"create_supply_port {p.name} -direction {p.direction}")
            else:
                lines.append(f"create_supply_port {p.name}")
        lines.append("")

    # Deduplicated by NAME, not by placement: the cross-hierarchy "reuse
    # an existing supply name" mechanism can legitimately create a
    # second placement (an auto-linked Internal Supply elsewhere)
    # sharing a plain Supply Port's own name -- both represent the same
    # real net, and real UPF has no "declare the same net twice"
    # construct (see _power_state_commands()'s own docstring for the
    # identical dedup reasoning applied to add_power_state).
    # ONE root-level `create_supply_net` per distinct NAME (see the FLAT,
    # ROOT-SCOPED note): a same-named placement on another hierarchy --
    # an auto-linked or ancestor-shared replica -- is the same continuous
    # net, so it is not declared again (it used to be, as `-reuse` inside
    # its own `set_scope`).
    #
    # Order: the root hierarchy's nets first, then each nested
    # hierarchy's not-yet-declared ones, shallowest hierarchy first --
    # each group alphabetical. PACKED, no blank lines: there are no scope
    # transitions left to mark.
    nets_by_hierarchy: dict[str, set[str]] = {}
    for placement in named_ports + internal_supplies + virtual_supplies:
        nets_by_hierarchy.setdefault(_relative_path(placement.owner_path), set()).add(placement.name)
    if nets_by_hierarchy:
        lines += _section_header("Supply Nets")
        declared: set[str] = set()
        for hierarchy in sorted(nets_by_hierarchy, key=lambda h: (h != "", h.count("/"), h)):
            for name in sorted(nets_by_hierarchy[hierarchy] - declared):
                lines.append(f"create_supply_net {name}")
                declared.add(name)
        lines.append("")

    # "Connect external ports to internal nets" -- matches golden.upf's
    # own section: a create_supply_port and its same-named create_supply_net
    # are two independent declarations in real UPF, not automatically
    # wired together -- without this, a boundary port's own net is
    # technically undriven at the boundary. Internal Supplies never
    # appear here (create_supply_net alone, deliberately no boundary
    # port to connect).
    if named_ports:
        lines += _section_header("Connect external ports to internal nets")
        padded = _pad_name_column([p.name for p in named_ports])
        # PACKED (refined rule), like the two declaration sections above.
        for p in sorted(named_ports, key=lambda p: p.name):
            lines.append(f"connect_supply_net {padded[p.name]}-ports {{{p.name}}}")
        lines.append("")

    if domain_state.supply_sets:
        lines += _section_header("Supply Sets")
        # All at the root scope (see the FLAT, ROOT-SCOPED note) -- Supply
        # Set names are unique design-wide, so nothing collides. The root
        # hierarchy's sets first, then nested ones shallowest-first, each
        # group alphabetical.
        supply_sets_sorted = sorted(
            domain_state.supply_sets.values(),
            key=lambda s: (_relative_path(s.owner_path) != "", _relative_path(s.owner_path).count("/"), _relative_path(s.owner_path), s.name),
        )
        role_specs = [("power", lambda s: s.power), ("ground", lambda s: s.ground), ("nwell", lambda s: s.n_well), ("pwell", lambda s: s.p_well)]
        set_blocks: list[list[str]] = []
        for s in supply_sets_sorted:
            # One `-function` per line, backslash-continued, like every
            # other multi-line command here (reference golden; it used
            # to be one long column-aligned line per set).
            #
            # `-function`, NOT `-supply` (review finding 1.2).
            # create_supply_set takes -function; -supply is
            # create_power_domain's flag.
            functions = [
                _flag_line(f"-function {{{role} {value}}}")
                for role, getter in role_specs
                if (value := getter(s))
            ]
            set_blocks.append([_render_multiline_command(f"create_supply_set {s.name}", functions)])
        lines += _join_blocks(set_blocks)
        lines.append("")

    # Computed unconditionally (not just inside "if domain_state.domains:"
    # below) -- the Retention Strategies section further down needs it
    # too, and a design can have Supply Sets/Retention Strategies without
    # ever having reached "if domain_state.domains:" first.
    supply_set_names = {id_: s.name for id_, s in domain_state.supply_sets.items()}

    lines += _port_attributes_upf_lines(port_attribute_state, top_level_ports, supply_set_names)

    if domain_state.domains:
        # Every domain at the root scope (see the FLAT, ROOT-SCOPED
        # note), its hierarchy stated by root-relative `-elements`:
        # `-elements {.}` for the design root itself, the instance
        # path(s) otherwise.
        lines += _section_header("Power Domains")
        all_domains = list(domain_state.domains.values())
        # Creation order, NOT alphabetical -- matches golden.upf's own
        # PD_TOP-then-PD_SUB ordering (the order they were actually
        # created in, "outer domain first" reads more naturally than a
        # sort that happens to put a sub-domain first). dict insertion
        # order is exactly that, since domains are added to
        # domain_state.domains one create_power_domain() call at a time.
        domain_blocks: list[list[str]] = []
        for d in all_domains:
            flag_chunks = []
            elements = sorted({rel for owner_path in d.owner_paths if (rel := _relative_path(owner_path))})
            if elements:
                # One element per line inside the brace group, not all
                # concatenated onto one line.
                flag_chunks.append(_braced_list_flag("-elements", elements))
            else:
                # The domain IS the design root -- `-elements {.}`, real
                # UPF convention for "the whole current scope", matching
                # golden.upf's own PD_TOP.
                flag_chunks.append(_flag_line("-elements {.}"))
            excluded = _direct_excluded_children(d, all_domains)
            if excluded:
                excluded_paths = sorted({rel for c in excluded for owner_path in c.owner_paths if (rel := _relative_path(owner_path))})
                if excluded_paths:
                    flag_chunks.append(_braced_list_flag("-exclude_elements", excluded_paths))
            primary = domain_state.supply_sets.get(d.primary_supply_set) if d.primary_supply_set is not None else None
            if primary is not None:
                flag_chunks.append(_flag_line(f"-supply {{primary {_upf_reference(primary)}}}"))
            secondary_names = [
                _upf_reference(domain_state.supply_sets[i])
                for i in d.secondary_supply_sets if i in domain_state.supply_sets
            ]
            if secondary_names:
                # -available_supplies (not a repeated -supply {secondary
                # ...} flag) -- matches golden.upf's own real 1801 usage.
                # Spaced inside the braces, as the reference golden
                # writes it.
                flag_chunks.append(_flag_line(f"-available_supplies {{ {' '.join(secondary_names)} }}"))
            domain_blocks.append([_render_multiline_command(f"create_power_domain {d.name}", flag_chunks)])
        lines += _join_blocks(domain_blocks)
        lines.append("")

    if retention_state.strategies:
        # Root scope (see the FLAT, ROOT-SCOPED note): the domain and
        # supply set are named bare, the elements by root-relative path.
        lines += _section_header("Retention Strategies")
        domain_names = {id_: d.name for id_, d in domain_state.domains.items()}
        retention_blocks: list[list[str]] = []
        for r in sorted(retention_state.strategies.values(), key=lambda r: r.name):
            domain_name = domain_names.get(r.power_domain_id)
            if domain_name is None:
                continue  # dangling reference -- the domain it was created against no longer exists
            # BOTH commands in the same backslash-continued,
            # one-flag-per-line shape as create_power_domain /
            # set_isolation above (explicit request).
            #
            # Still TWO commands, deliberately: real UPF separates
            # `set_retention` (domain/supply/elements) from
            # `set_retention_control` (save/restore behaviour), and an
            # earlier proposal to fold the signals onto the first one was
            # dropped as wrong.
            flag_chunks = [_flag_line(f"-domain {domain_name}")]
            # `-retention_supply_set` ONLY (reference golden). The
            # primary supply is the domain's own, which `-domain` already
            # states; it used to be repeated as `-supply {primary ...}`
            # next to a `-supply {retention ...}`, which the tool did not
            # accept. `primary_supply_set_id` is therefore not generated.
            retention_set = domain_state.supply_sets.get(r.retention_supply_set_id)
            if retention_set is not None:
                flag_chunks.append(_flag_line(f"-retention_supply_set {_upf_reference(retention_set)}"))
            if r.elements:
                element_paths = sorted(_upf_element(e, widths) for e in r.elements)
                flag_chunks.append(_braced_list_flag("-elements", element_paths))
            control_chunks = [_flag_line(f"-domain {domain_name}")]
            if r.save_logic:
                control_chunks.append(_flag_line(f"-save_signal {{{_retention_signal(r.save_logic)}}}"))
            if r.restore_logic:
                control_chunks.append(_flag_line(f"-restore_signal {{{_retention_signal(r.restore_logic)}}}"))
            # One blank line between `set_retention` and its own
            # `set_retention_control`, as the reference golden
            # writes them -- and so, one between strategies too.
            retention_blocks.append([_render_multiline_command(f"set_retention {r.name}", flag_chunks)])
            retention_blocks.append([_render_multiline_command(f"set_retention_control {r.name}", control_chunks)])
        lines += _join_blocks(retention_blocks)
        lines.append("")

    if power_switch_state and power_switch_state.switches:
        # Item 1 (explicit request): Power Switch was fully built on the
        # GUI side (creation, dialog, Cell Pins, ON/OFF Logic, multi-
        # hierarchy replication) but never wired into this generator at
        # all -- confirmed by reading this file/its own caller
        # (main_window.py's _on_extract_power_intent()) before making
        # this change, the exact same "GUI-complete, generator never
        # updated" gap Retention's own bug report already established
        # once in this file (see module docstring) -- not assumed to be
        # already handled.
        #
        # Real UPF syntax verified against this project's OWN backend
        # UPF parser (backend/elaborator/power_intent/upf/elaborate.py's
        # own create_power_switch registration: positional `name`,
        # options domain/input_supply_port/output_supply_port/
        # control_port), not guessed at: `create_power_switch <name>
        # -domain <domain> -output_supply_port <name> [-input_supply_port
        # <name>] [-control_port <name>]`. Supply In/Out map onto
        # -input_supply_port/-output_supply_port using the RESOLVED
        # supply name (what -input_supply_port/-output_supply_port
        # actually reference in real UPF -- a supply port/net).
        #
        # Rework (explicit request, REVERSES the earlier "Cell Type
        # dropdown referencing a reusable PowerSwitchCellPlacement
        # library" model): PowerSwitchPlacement.input_supply_port/
        # output_supply_port/control_port are now plain, REQUIRED
        # per-instance fields -- input_supply_port/output_supply_port
        # double as the {role name} tag itself (matching golden.upf's
        # own `{in VDD}`/`{out VDD_SUB}` shape, previously hardcoded
        # "in"/"out" since no real per-instance role-name data existed
        # yet), `<net>` being the electrically resolved Supply In/Out
        # name. -control_port is always emitted now (was previously
        # gated on a Cell being assigned) -- these 3 fields are required
        # at dialog accept() time, so there's no longer an "absent"
        # case. `cell_type` (an OPTIONAL free-text library-cell name) is
        # emitted as a real `map_power_switch` command when set (bug fix,
        # explicit request -- see the per-instance block below; it was
        # previously an informational comment only), or simply omitted
        # (non-blocking) when unset -- ON/OFF State, which the backend's
        # own parser explicitly declines to confidently model
        # at all (see _create_power_switch()'s own comment on
        # -on_state/-off_state syntax not being confidently known), has
        # its own real -on_state/-off_state flags below instead.
        #
        # Root scope, like everything else (see the FLAT, ROOT-SCOPED
        # note): `-domain` and the input/output nets are all named bare.
        #
        # Multi-hierarchy replication: PowerSwitchPlacement.target_ids/
        # owner_paths are parallel lists, one entry per replicated member
        # hierarchy. Each replica used to get its own `set_scope` so the
        # same switch name resolved per hierarchy. At the root that would
        # declare one name twice -- and there is nothing left to tell the
        # replicas apart anyway: they share one domain, and their outputs
        # are same-named, i.e. (by this app's own rule) one continuous
        # net, declared once. So ONE `create_power_switch` per switch,
        # from its first resolvable target: a single switch rule covers
        # every block of its power domain (confirmed by the user).
        #
        # Real -on_state/-off_state flags (state names "on"/"off",
        # matching golden.upf's own convention) carry the actual boolean
        # expressions -- previously relegated to an info-only comment
        # entirely, a real functional gap fixed in an earlier pass (an
        # ON/OFF State typed into the dialog never reached the exported
        # UPF at all).
        lines += _section_header("Power Switches")
        domain_names = {id_: d.name for id_, d in domain_state.domains.items()}
        blocks: list[list[str]] = []
        for s in sorted(power_switch_state.switches.values(), key=lambda s: s.name):
            domain_name = domain_names.get(s.power_domain_id)
            if domain_name is None:
                continue  # dangling reference -- the domain it was created against no longer exists
            for target_id in s.target_ids:
                target = supply_state.supply_port_placements.get(target_id) or supply_state.pg_pin_placements.get(target_id)
                if target is None:
                    continue  # dangling reference -- the target this switch was attached to no longer exists
                block: list[str] = []
                connected = connected_supply_port(power_switch_connector_key(s.id, target_id), supply_state)
                # Flag order (domain, input, output, control, on_state,
                # off_state) matches golden.upf's own PSW_SUB exactly.
                #
                # The switch's own port names ARE the user's four field
                # values, emitted directly (explicit decision). The
                # fixed-abstract-token layer that briefly sat here --
                # IN/OUT/CTRL/ACK declared by create_power_switch and
                # bridged to the fields via `map_power_switch -port_map`
                # -- is retired, along with -port_map itself. This is the
                # shape this generator had before the external review
                # introduced the tokens, and it makes the four dialog
                # fields directly meaningful again with no bridging
                # construct.
                flag_chunks = [_flag_line(f"-domain {domain_name}")]
                if connected is not None:
                    flag_chunks.append(_flag_line(f"-input_supply_port {{{s.input_supply_port} {connected.name}}}"))
                flag_chunks.append(_flag_line(f"-output_supply_port {{{s.output_supply_port} {target.name}}}"))
                # `-control_port {CTRL <net>}` connects the abstract
                # control port to the RTL signal driving it. The bare
                # form is emitted when no Control Signal has been given:
                # the port is still declared, it just has nothing wired
                # to it yet.
                if s.control_signal:
                    flag_chunks.append(_flag_line(f"-control_port {{{s.control_port} {s.control_signal}}}"))
                else:
                    flag_chunks.append(_flag_line(f"-control_port {_tcl_word(s.control_port)}"))
                # `-ack_port {port net [{expr}]}` -- a real option on
                # create_power_switch, the same {abstract-port real-net}
                # shape as -control_port, with an optional third boolean
                # element this app has no field for and does not invent.
                # RESEARCHED: two independent references give this shape
                # for UPF 2.1+ alongside -output_supply_port; the
                # sibling -ack_delay {port delay} also exists and is NOT
                # emitted, since there is no delay field to emit from.
                #
                # Emitted only when BOTH halves exist: a port with no net
                # states a connection to nothing, and a net with no port
                # has no port to attach to.
                if s.ack_port and s.ack_signal:
                    flag_chunks.append(_flag_line(f"-ack_port {{{s.ack_port} {s.ack_signal}}}"))
                # ON/OFF State are authored in terms of the REAL RTL
                # signal and rewritten to the abstract control port here
                # -- see substitute_control_signal(). An expression that
                # never mentions the signal is emitted verbatim; the
                # dialog's own inline hint is what raises that, at a point
                # where the user can still act on it.
                # Substituted toward the switch's own CONTROL PORT name --
                # the user's field value, not a fixed token, since that
                # is what `-control_port` above now declares. The input
                # port element likewise names the user's own Input Supply
                # Port value.
                if s.on_state:
                    on_expr = substitute_control_signal(s.on_state, s.control_signal, s.control_port)
                    on_body = (
                        f"on {s.input_supply_port} {{{on_expr}}}" if connected is not None
                        else f"on {{{on_expr}}}"
                    )
                    flag_chunks.append(_flag_line(f"-on_state {{{on_body}}}"))
                if s.off_state:
                    off_expr = substitute_control_signal(s.off_state, s.control_signal, s.control_port)
                    flag_chunks.append(_flag_line(f"-off_state {{off {{{off_expr}}}}}"))
                block.append(_render_multiline_command(f"create_power_switch {s.name}", flag_chunks))
                # Cell Type -> `map_power_switch` (bug fix, explicit
                # request): the free-text `cell_type` is a library-cell
                # NAME to map this switch rule onto.
                #
                # The switch name is POSITIONAL (review finding 1.3):
                # `map_power_switch {PG} -lib_cells {CELL}`. This
                # RETRACTS an earlier instruction in this project that
                # specified `-switch_name_list`, since confirmed not to
                # be standard IEEE 1801-2018 syntax. One
                # map_power_switch per instance (a genuinely REPLICATED
                # switch emits one per replica scope, alongside its own
                # per-replica `create_power_switch`) rather than trying to
                # group same-cell rules -- simpler and always correct,
                # since cell_type is per-instance free text. Emitted ONLY
                # when set -- a missing Cell Type is non-blocking (nothing
                # generated for this instance), consistent with the
                # existing rule; the Properties table's own "No Cell Type
                # Assigned" marker (properties_panel.py) is the
                # "generation-time warning" that still fires, unaffected
                # by this.
                if s.cell_type:
                    # NO blank line between the create_power_switch
                    # block and its own map_power_switch: the two are
                    # one instance, and the separator marks the boundary
                    # between DISTINCT instances. This used to insert
                    # one here, which read as a section break in the
                    # middle of a single switch.
                    #
                    # `-domain` was missing entirely -- the same gap
                    # already found and fixed for map_level_shifter_cell
                    # (audit B10). VERIFIED rather than assumed: the
                    # documented shape is `map_power_switch switch_name
                    # -domain domain_name -lib_cells list`, so -domain
                    # goes immediately after the positional name, which
                    # also matches the three map_*_cell commands' own
                    # documented order (`name -domain d [-elements]
                    # [-lib_cells]`).
                    map_flags = [
                        _flag_line(f"-domain {domain_name}"),
                        _flag_line(f"-lib_cells {{{s.cell_type}}}"),
                    ]
                    # NO `-port_map`, ever (explicit decision). It
                    # existed to bridge a fixed abstract port name to the
                    # user's own field value; with the fields BEING the
                    # port names there is nothing left to bridge, and the
                    # whole indirection is retired rather than made
                    # conditional.
                    block.append(_render_multiline_command(f"map_power_switch {{{s.name}}}", map_flags))
                blocks.append(block)
                break  # one per switch -- see the replication note above
        lines += _join_blocks(blocks)
        lines.append("")

    domain_names = {id_: d.name for id_, d in domain_state.domains.items()}
    lines += _isolation_upf_lines(isolation_state, domain_names, domain_state.supply_sets, widths)

    if level_shifter_state and level_shifter_state.strategies:
        # Real UPF 1801 `set_level_shifter` syntax (well-documented,
        # standard LRM construct -- unlike Power Switch's own ON/OFF
        # State, there is no confidence gap here): `-domain`,
        # `-applies_to {inputs|outputs|both}`, `-rule
        # {low_to_high|high_to_low|both}`, `-location {self|parent|
        # fanout}`, `-threshold <value>` (only when set), `-elements`.
        # `set_level_shifter_cell` pairs a rule with a library cell name
        # -- see the per-strategy block below for the full "why" and the
        # verified IEEE 1801 syntax (bug fix, explicit request:
        # `strategy.cell_type` was previously emitted as a UPF 2.0-era
        # `map_level_shifter_cell -domain <d> -cell <c>` line).
        # Bug fix (found matching golden.upf's own block precisely, same
        # pass as Isolation's new section): this command shares
        # `set_isolation`'s own two distinctive shapes -- name+`-domain`
        # merged onto ONE head line, and a 2-space (not 4-space) flag
        # indent -- confirmed by direct inspection of golden.upf's own
        # `set_level_shifter LS_SUB_OUT -domain PD_SUB \` block, previously
        # rendered as one flat single line instead. Flag order
        # (`-applies_to`, `-location`, `-rule`) also matches golden.upf's
        # own literal order, which differs from this app's own internal
        # field order (`applies_to`, `rule`, `location`).
        # Root scope, same as Isolation above (see the FLAT, ROOT-SCOPED
        # note).
        lines += _section_header("Level Shifter Rules")
        ls_blocks: list[list[str]] = []
        for ls in sorted(level_shifter_state.strategies.values(), key=lambda s: s.name):
            domain_name = domain_names.get(ls.power_domain_id)
            if domain_name is None:
                continue  # dangling reference -- the domain it was created against no longer exists
            head = f"set_level_shifter {ls.name} -domain {domain_name}"
            # Guarded, same reason as set_isolation's own flags above: an
            # unset value used to be written as the literal text "None",
            # which parses cleanly and comes back as the STRING 'None'.
            flag_chunks = []
            # `-applies_to` only for a real IEEE 1801 direction. "elements"
            # is the dialog's own "use my element list" choice, NOT a UPF
            # value: it is expressed by `-elements` alone, and used to leak
            # out as an invalid `-applies_to elements`.
            if ls.applies_to in _UPF_APPLIES_TO_DIRECTIONS:
                flag_chunks.append(_flag_line(f"-applies_to {ls.applies_to}", indent=2))
            if ls.location:
                flag_chunks.append(_flag_line(f"-location {ls.location}", indent=2))
            if ls.elements:
                # Root-relative path, same reason as set_isolation's.
                element_paths = sorted(_upf_element(e, widths) for e in ls.elements)
                flag_chunks.append(_braced_list_flag("-elements", element_paths, indent=2))
            if ls.rule:
                flag_chunks.append(_flag_line(f"-rule {ls.rule}", indent=2))
            if ls.threshold is not None:
                flag_chunks.append(_flag_line(f"-threshold {ls.threshold}", indent=2))
            # `-name_prefix` is VERIFIED on set_level_shifter: it
            # appears in OpenROAD's own UPF command reference for
            # this command, in a list evidently derived from the LRM
            # (it carries obscure siblings like
            # -use_functional_equivalence / -applies_to_boundary /
            # -no_shift / -force_shift), corroborated independently.
            # Its own `-name_suffix` sibling is real too and is not
            # emitted -- there is no suffix field to emit from.
            if ls.name_prefix:
                flag_chunks.append(_flag_line(f"-name_prefix {_tcl_word(ls.name_prefix)}", indent=2))
            block = [_render_multiline_command(head, flag_chunks)]
            # Cell Type -> `map_level_shifter_cell <name> -domain <d>
            # -lib_cells {A B}` (audit B10). This SUPERSEDES
            # `set_level_shifter_cell -level_shifter <n> -cell_name <c>`,
            # which this generator wrote before: the map_* form is what
            # a real reference file uses, its signature is confirmed
            # (`map_level_shifter_cell level_shifter_name -domain
            # domain_name -lib_cells list [-elements list]`, with
            # -domain REQUIRED), and it takes a cell LIST where the
            # superseded form took one name.
            #
            # Positional name, `-domain`, brace-wrapped list -- exactly
            # the shape map_power_switch was corrected to earlier, so
            # the two cell-mapping commands now read alike. `cell_type`
            # is free text and may hold several space-separated names
            # (audit B5); it is emitted verbatim inside the braces.
            # Emitted ONLY when set -- a missing Cell Type is
            # non-blocking, same as Power Switch above.
            # Directly PAIRED with its own set_level_shifter, so no
            # blank line between the two: the separator marks the
            # boundary between distinct instances, and a rule plus
            # its own cell mapping is ONE instance.
            if ls.cell_type:
                block.append(
                    _render_multiline_command(
                        f"map_level_shifter_cell {ls.name}",
                        [_flag_line(f"-domain {domain_name}"), _flag_line(f"-lib_cells {{{ls.cell_type}}}")],
                    )
                )
            ls_blocks.append(block)
        lines += _join_blocks(ls_blocks)
        lines.append("")

    # TIER 1: per-Supply-Set derived states. This REPLACES the old
    # per-net independent `add_power_state` block -- a rail's states are
    # no longer an independent fact to emit, they are what a Supply Set's
    # own states are composed from. Derived once here and reused by
    # Tier 2, so the two cannot disagree about which STATE_N a row means.
    states_by_set, row_state_by_set = _derive_supply_set_states(
        supply_state, domain_state, power_state_table_state
    )
    # EXCLUSIVE: legacy replaces the modern sections entirely rather
    # than being appended to them. Tier 1 is still DERIVED either way --
    # CPF and Tier 2 both read `row_state_by_set` -- only its UPF output
    # is withheld.
    if not legacy_power_states:
        lines += _supply_set_states_upf_lines(states_by_set)
    else:
        lines += _power_state_table_upf_lines(
            power_state_table_state, supply_state, domain_state,
            power_switch_state.switches if power_switch_state else {},
        )

    connections = _macro_connections(supply_state)
    if connections:
        lines += _section_header("Macro Connections")
        # Primary PG pins (VDD/VSS-style, pg_type primary_power/
        # primary_ground) before internal ones (VDD_INT-style,
        # pg_type internal_power/internal_ground) -- matches golden.upf's
        # own ordering (VDD, VSS, then VDD_BKP -> VDD_INT last), not a
        # plain alphabetical sort (which would put VDD_BKP second).
        internal_pin_names = {p.name for p in supply_state.pg_pin_placements.values() if p.is_internal}
        connections = sorted(connections, key=lambda c: (c[1].rsplit("/", 1)[-1] in internal_pin_names, c[0]))
        padded = _pad_name_column([net_name for net_name, _ in connections], min_gap=2)
        # PACKED (refined rule). Named by that request's own title
        # ("macro port connections") rather than by its list of three
        # section headings, which instead named the boundary-connection
        # section -- a judgment call, and the one that keeps the two
        # `connect_supply_net` sections looking alike rather than
        # packing one and separating the other.
        for net_name, pin_ref in connections:
            lines.append(f"connect_supply_net {padded[net_name]}-ports {{{pin_ref}}}")
        lines.append("")

    # TIER 2: ONE global power state group for the whole design. This
    # REPLACES the old legacy-PST translation, which could not express a
    # supply-set-level model at all.
    if not legacy_power_states:
        lines += _power_state_group_upf_lines(
            design_name, power_state_table_state, domain_state, row_state_by_set
        )

    # "Load Upf" (explicit request): the generator's own final section --
    # see the comment block above _macro_power_intent_upf_lines() for why
    # this is a real UPF command (unlike its CPF counterpart below).
    lines += _macro_power_intent_upf_lines(macro_power_intent_state, output_dir)

    return _ascii_whitespace("\n".join(lines).rstrip("\n") + "\n")


def _internal_supply_names(supply_state: SupplyPortDiagramState) -> set[str]:
    """Names belonging to at least one Internal Supply placement and no
    plain Supply Port placement of the same name -- used to split CPF's
    otherwise-flat create_power_nets/create_ground_nets output into two
    labelled groups (CPF's own create_power_nets/create_ground_nets is a
    plain net declaration at either hierarchy level -- confirmed no
    equivalent boundary-vs-internal distinction exists there the way
    UPF's create_supply_port-vs-create_supply_net split has, so this
    export labels the two kinds via separate sections/comments instead
    of a fabricated flag). A name used as both a plain Supply Port AND
    an Internal Supply somewhere in the design (an unlikely edge case --
    the two are mutually exclusive by hierarchy level, but nothing stops
    the same name recurring under two different hierarchies) is treated
    as a Supply Port here, not guessed at further."""
    internal = {p.name for p in supply_state.supply_port_placements.values() if p.is_internal_supply}
    plain = {p.name for p in supply_state.supply_port_placements.values() if not p.is_internal_supply}
    return internal - plain


def generate_cpf(
    supply_state: SupplyPortDiagramState,
    domain_state: PowerDomainDiagramState,
    retention_state: RetentionDiagramState,
    version: str,
    power_switch_state: Optional[PowerSwitchDiagramState] = None,
    level_shifter_state: Optional[LevelShifterDiagramState] = None,
    design_name: Optional[str] = None,
    isolation_state: Optional[IsolationDiagramState] = None,
    port_attribute_state: Optional[PortAttributeDiagramState] = None,
    top_level_ports: Optional[list[Port]] = None,
    power_state_table_state: Optional[PowerStateTableDiagramState] = None,
    macro_power_intent_state: Optional[MacroPowerIntentDiagramState] = None,
) -> str:
    lines = _header(
        "CPF",
        version,
        design_name,
        extra=[
            "# Supply Sets have no direct CPF equivalent -- folded into each",
            "# domain's own primary_power_net/primary_ground_net below instead.",
        ],
    )
    # Same self-describing requirement as UPF's own set_upf_version --
    # see generate_upf()'s comment.
    lines += [f"set_cpf_version {version}"]
    # `set_hierarchy_separator /` -- REQUIRED here, not cosmetic. CPF's
    # DEFAULT hierarchy delimiter is a period (Si2 CPF 2.1 p. 44: "period
    # (.) default; slash (/) recommended; colon (:)"), and every
    # hierarchical reference this writer emits uses `/`
    # (`u_sram/VDD`, `u_sub/q_reg`). Without this line a conformant
    # reader parses those as single flat names rather than as paths.
    # Found by the CPF re-verification audit; present in CPF 1.0 too, so
    # this has always been wrong.
    lines += ["set_hierarchy_separator /"]
    # THE DESIGN WRAPPER (audit finding C2). "The power intent
    # represented by the power design is defined by the CPF commands
    # between this command and a matching end_design command"
    # (Si2 CPF 2.1, set_design, p. 201). Everything this writer emitted
    # used to sit outside any design at all. `end_design` is appended at
    # the very end of this function.
    lines += [f"set_design {design_name or 'DESIGN'}", ""]

    power_names: set[str] = set()
    ground_names: set[str] = set()
    for s in domain_state.supply_sets.values():
        if s.power:
            power_names.add(s.power)
        if s.ground:
            ground_names.add(s.ground)
    # Virtual Supply is never referenced by any Supply Set's power/ground
    # role (it can't be used as a Supply Set rail at all -- see spec
    # section 1), so it never naturally falls into power_names/ground_names
    # the way a normal supply does -- without this, it would silently land
    # in the "unclassified... CPF requires that classification to create a
    # net" comment block below instead of actually being emitted. CPF has
    # no type-agnostic net concept, so it gets its own labelled section
    # instead (mirroring the existing "Internal Supplies (power)" split
    # below) using create_power_nets -- the same "power" default
    # ordered_supply_nets() (power_state_table.py) already applies for
    # UPF's own unclassified-name case.
    virtual_names = {p.name for p in supply_state.supply_port_placements.values() if p.is_virtual_supply}
    voltages = _voltage_by_name(supply_state)
    internal_names = _internal_supply_names(supply_state)

    def _emit_net_section(title: str, names: set[str], command: str, internal: bool = False) -> None:
        if not names:
            return
        lines.extend(_section_header(title))
        # PACKED (refined rule), the CPF counterpart of UPF's own
        # Supply Ports/Supply Nets sections: these are net DECLARATIONS,
        # and CPF emits them all at one scope, so there is never a scope
        # transition inside one of these sections to mark.
        #
        # `-internal` (audit finding H5) is a MUST, not a nicety: "You
        # must specify this option if this power net is the primary
        # power net of an internal switchable power domain using header
        # switches" (Si2 CPF 2.1, create_power_nets, p. 171). An
        # Internal Supply driven by a Power Switch is exactly that, and
        # the section this writer already labels "Internal Supplies"
        # knew the fact without emitting it.
        for name in sorted(names):
            voltage_flag = f" -voltage {voltages[name].rstrip('Vv')}" if name in voltages else ""
            internal_flag = " -internal" if internal else ""
            lines.append(f"{command} -nets {{{name}}}{voltage_flag}{internal_flag}")
        lines.append("")

    _emit_net_section("Power Supplies (power)", power_names - internal_names, "create_power_nets")
    _emit_net_section("Internal Supplies (power)", power_names & internal_names, "create_power_nets", internal=True)
    _emit_net_section("Power Supplies (ground)", ground_names - internal_names, "create_ground_nets")
    _emit_net_section("Internal Supplies (ground)", ground_names & internal_names, "create_ground_nets", internal=True)
    _emit_net_section("Virtual Supplies", virtual_names, "create_power_nets")

    all_port_names = {p.name for p in supply_state.supply_port_placements.values()}
    unclassified = sorted(all_port_names - power_names - ground_names - virtual_names)
    if unclassified:
        lines.append("# Power supplies not used as any Supply Set's power/ground role")
        lines.append("# (CPF requires that classification to create a net) -- skipped:")
        for name in unclassified:
            lines.append(f"#   {name}")
        lines.append("")

    supply_set_names = {id_: s.name for id_, s in domain_state.supply_sets.items()}
    domain_names = {id_: d.name for id_, d in domain_state.domains.items()}

    if domain_state.domains:
        lines += _section_header("Power Domains")
        all_domains = list(domain_state.domains.values())
        domain_blocks: list[list[str]] = []
        for d in sorted(domain_state.domains.values(), key=lambda d: d.name):
            # `create_power_domain`, its own `update_power_domain`, and
            # its own exclusion note are ONE instance -- never separated
            # from each other, always separated from the next domain.
            block: list[str] = []
            elements = sorted({rel for owner_path in d.owner_paths if (rel := _relative_path(owner_path))})
            parts = [f"create_power_domain -name {d.name}"]
            if elements:
                # Item 3 (explicit request, extended to Power Domain's
                # own Elements list too): one element per line, same
                # _multiline_braced_list() helper generate_upf() uses.
                parts.append(_multiline_braced_list("-instances", elements))
            else:
                # `-default` (audit finding H1). A domain anchored at the
                # design root has no instance list of its own -- UPF says
                # `-elements {.}`, and CPF says `-default`: "All instances
                # of the current design scope that were not associated
                # with a specific power domain belong to the default
                # power domain" (Si2 CPF 2.1, p. 160). Emitted with
                # NEITHER before, so the domain selected nothing at all.
                parts.append("-default")
            # Port Attributes (explicit request): CPF has no separate
            # section for these at all -- every assigned port folds
            # directly into the design's own ROOT-level domain's
            # `-boundary_ports` flag instead (matches golden.cpf's own
            # `create_power_domain -name PD_TOP ... -boundary_ports
            # {...}` exactly -- see _boundary_ports_for_root_domain()'s
            # own docstring).
            boundary_ports = _boundary_ports_for_root_domain(d, port_attribute_state, top_level_ports, supply_set_names)
            if boundary_ports:
                parts.append(_multiline_braced_list("-boundary_ports", boundary_ports))
            excluded = _direct_excluded_children(d, all_domains)
            excluded_paths = sorted({rel for c in excluded for owner_path in c.owner_paths if (rel := _relative_path(owner_path))})
            if excluded_paths:
                # `-exclude_instances` (audit finding H2), which this
                # writer previously reported as having "no exact CPF
                # equivalent" and emitted as a comment. That was correct
                # against CPF 1.0, where the flag does not exist at all;
                # it was added afterwards and is real in 2.0 (Si2 CPF
                # 2.1, create_power_domain, p. 158).
                #
                # Emitted alongside `-default`, matching the hand-written
                # golden CPF 2.0. Worth knowing: the spec ties it to
                # `-instances` ("Excludes those instances already
                # selected through the -instances option"), so a strict
                # reader may ignore it on a `-default` domain -- harmless
                # either way, since a nested domain's own members are
                # already excluded from the default domain by definition.
                parts.append(_multiline_braced_list("-exclude_instances", excluded_paths))
            # `-shutoff_condition` (audit finding H4). The condition
            # under which this domain powers down is the single most
            # load-bearing fact the golden UPF carries about a switched
            # domain, and it reached the CPF only as a comment. CPF's own
            # worked example puts it here: "create_power_domain -name X
            # -shutoff_condition !ena ... create_power_switch_rule -name
            # ps1 -domain X -external_power_net VDD" (Si2 CPF 2.1,
            # p. 174), and "the inversion of the expression specified for
            # the shutoff condition of the power domain is used as the
            # driver for the enable pin of the power switch cell"
            # (p. 173).
            shutoff = _domain_shutoff_condition(d, power_switch_state)
            if shutoff is not None:
                parts.append(f"-shutoff_condition {{{shutoff}}}")
            block.append(" ".join(parts))
            ss = domain_state.supply_sets.get(d.primary_supply_set) if d.primary_supply_set is not None else None
            if ss is not None and (ss.power or ss.ground):
                update_parts = [f"update_power_domain -name {d.name}"]
                if ss.power:
                    update_parts.append(f"-primary_power_net {ss.power}")
                if ss.ground:
                    update_parts.append(f"-primary_ground_net {ss.ground}")
                block.append(" ".join(update_parts))
            domain_blocks.append(block)
        lines += _join_blocks(domain_blocks)
        lines.append("")

    if retention_state.strategies:
        lines += _section_header("Retention Strategies")
        domain_names = {id_: d.name for id_, d in domain_state.domains.items()}
        supply_set_names = {id_: s.name for id_, s in domain_state.supply_sets.items()}
        retention_blocks: list[list[str]] = []
        for r in sorted(retention_state.strategies.values(), key=lambda r: r.name):
            domain_name = domain_names.get(r.power_domain_id)
            if domain_name is None:
                continue  # dangling reference -- the domain it was created against no longer exists
            # The rule and its own explanatory comment are ONE instance.
            # `-name`, not a bare positional. CPF names every rule with
            # an explicit flag -- every other command this writer emits
            # already does (create_power_domain, create_power_switch_rule,
            # create_isolation_rule, create_level_shifter_rule), and so
            # does golden.cpf. This one line was the lone positional, and
            # a real CPF reader rejects it; found by round-tripping this
            # app's own generated CPF back through the real elaborator.
            # Same multi-line shape as the UPF writer above and as this
            # format's own create_isolation_rule block -- CPF's
            # retention rule is the structurally analogous construct
            # (a named rule, a domain, and an element list), so it
            # formats alike rather than staying a compact one-liner.
            retention_flags = [_flag_line(f"-domain {domain_name}")]
            if r.elements:
                # Item 3 (explicit request): one element per line, same
                # _multiline_braced_list() helper used throughout.
                # `-instances`, not `-pins`. A retention rule applies to
                # sequential ELEMENTS (registers), and CPF names those
                # with -instances; -pins belongs to create_isolation_rule,
                # which applies to ports. The two sections are adjacent
                # here and the wrong flag was carried across -- found by
                # round-tripping this app's own generated CPF, which the
                # real elaborator rejected.
                element_paths = sorted(_relative_path(e) for e in r.elements)
                retention_flags.append(_braced_list_flag("-instances", element_paths))
            block = [_render_multiline_command(f"create_state_retention_rule -name {r.name}", retention_flags)]
            # create_state_retention_rule's real -save_edge/-restore_edge
            # flags need a <signal, rise|fall> pair, and CPF has no direct
            # flag for a domain's own primary vs. retention supply role --
            # this app tracks save/restore as free-text logic (no separate
            # edge) and the two supply roles as separate references, so
            # (matching the Power Domains section's own precedent above:
            # "not confident enough to assert a fabricated flag") both are
            # surfaced as a comment rather than guessed syntax.
            primary_name = supply_set_names.get(r.primary_supply_set_id) or "?"
            retention_name = supply_set_names.get(r.retention_supply_set_id) or "?"
            block.append(f"#   {r.name}: save={r.save_logic!r} restore={r.restore_logic!r} primary_supply={primary_name} retention_supply={retention_name}")
            retention_blocks.append(block)
        lines += _join_blocks(retention_blocks)
        lines.append("")

    if power_switch_state and power_switch_state.switches:
        # Item 1 (explicit request): same "never wired in" gap as
        # generate_upf()'s own Power Switches section above -- see that
        # one's own docstring for the full "why".
        #
        # Real CPF syntax verified against this project's OWN backend
        # CPF parser (backend/elaborator/power_intent/cpf/elaborate.py's
        # own create_power_switch_rule registration: options name/
        # domain/external_power_net/external_ground_net, name is a FLAG
        # here -- unlike UPF's positional `create_power_switch <name>`),
        # not guessed at: `create_power_switch_rule -name <name> -domain
        # <domain> [-external_power_net <net> | -external_ground_net
        # <net>]`. The backend itself only ever captures ONE of
        # power/ground for the switch's own input side (its own comment:
        # "mutually exclusive per the spec... mapped onto input_supply"),
        # and never an output/control/cell-pin/on-off-logic equivalent
        # at all -- a real, structural CPF limitation compared to UPF's
        # own create_power_switch, not an oversight in this generator.
        # Cell Type is the ONE exception -- CPF's own
        # `update_power_switch_rule -cells` IS a real, spec-verified
        # equivalent for it (see the per-instance block below).
        # Supply In's resolved name is classified power-vs-ground via
        # the SAME power_names/ground_names sets the Power Supplies
        # section above already computes (a name absent from both is
        # unclassifiable -- omitted, not guessed, same "unclassified"
        # precedent that section already establishes); everything else
        # (output supply, all 3 Cell Pins, ON/OFF Logic) is surfaced in
        # an informational comment instead of a fabricated flag.
        #
        # Multi-hierarchy replication: same set_instance-per-replica
        # scoping generate_upf() uses (CPF's own real scope-switching
        # primitive, confirmed via this project's own CLAUDE.md 2b
        # research notes: set_instance REPLACES the current scope,
        # matching set_scope's role for this purpose), restored to
        # "set_instance ." once at the end of this section.
        lines += _section_header("Power Switches")
        domain_names = {id_: d.name for id_, d in domain_state.domains.items()}
        switch_blocks: list[list[str]] = []
        for s in sorted(power_switch_state.switches.values(), key=lambda s: s.name):
            domain_name = domain_names.get(s.power_domain_id)
            if domain_name is None:
                continue  # dangling reference -- the domain it was created against no longer exists
            for target_id, owner_path in zip(s.target_ids, s.owner_paths):
                target = supply_state.supply_port_placements.get(target_id) or supply_state.pg_pin_placements.get(target_id)
                if target is None:
                    continue  # dangling reference -- the target this switch was attached to no longer exists
                # `set_instance`, the rule, its own `update_*` lines and
                # its summary comment are ONE instance.
                # NO `set_instance` wrapper (audit finding M1/C2). `.`
                # is CPF's DEFAULT HIERARCHY SEPARATOR, not a "back to
                # top" token, so the `set_instance .` this writer used
                # to emit named an instance literally called `.`. CPF
                # scopes structurally instead -- everything here sits
                # inside the one `set_design`/`end_design` pair and
                # references instances by their root-relative path,
                # which is exactly what the hand-written golden CPF 2.0
                # does.
                block: list[str] = []
                parts = [f"create_power_switch_rule -name {s.name}", f"-domain {domain_name}"]
                connected = connected_supply_port(power_switch_connector_key(s.id, target_id), supply_state)
                if connected is not None:
                    if connected.name in power_names:
                        parts.append(f"-external_power_net {connected.name}")
                    elif connected.name in ground_names:
                        parts.append(f"-external_ground_net {connected.name}")
                block.append(" ".join(parts))
                # Cell Type -> `update_power_switch_rule -cells` (bug
                # fix, explicit request): CPF DOES have a real equivalent
                # -- verified against the Si2 CPF 1.0 primary spec
                # (`update_power_switch_rule -name <string> { ... |
                # -cells cell_list -library_set library_set | ... }...`;
                # the spec's own define_power_switch_cell text: "To use a
                # specific cell, use the update_power_switch_rule
                # command"). `-library_set` is deliberately omitted --
                # this app's diagram model has no library-set concept and
                # the spec allows `-cells` to resolve against cells
                # already defined via `define_power_switch_cell`; the
                # project's own backend CPF parser also accepts `-cells`
                # without `-library_set`. `-cells` is list-typed, so a
                # single value is brace-wrapped. Emitted ONLY when set --
                # a missing Cell Type is non-blocking, same as UPF.
                if s.cell_type:
                    block.append(f"update_power_switch_rule -name {s.name} -cells {{{s.cell_type}}}")
                # `-enable_condition_1` (audit finding H4). The switch's
                # ON State reached CPF only as a comment; this is its
                # real home, and it is what the numbered
                # `-acknowledge_receiver_1` below pairs with.
                if s.on_state:
                    block.append(f"update_power_switch_rule -name {s.name} -enable_condition_1 {{{s.on_state}}}")
                # ACK SIGNAL -> a REAL CPF construct, contrary to what
                # this project's other CPF switch gaps would suggest.
                # Verified against the Si2 CPF 1.0 primary spec directly:
                # `update_power_switch_rule -name <string> { ... |
                # -acknowledge_receiver pin | ... }...`, documented as
                # "an input pin in the design which must be connected to
                # an output pin of the power switch cell" -- which is
                # exactly what Ack Signal is (the design side), not what
                # Ack Port is (the cell side).
                #
                # One spec inconsistency, resolved in favour of the
                # syntax: the prose for define_power_switch_cell refers
                # to "the -acknowledge_receiver option of the
                # create_power_switch_rule command", but both syntax
                # blocks AND the quick reference put it on
                # update_power_switch_rule, and create_power_switch_rule's
                # own syntax lists only -name/-domain/-external_*_net.
                #
                # The ACK PORT (the cell's own pin) has no home here: CPF
                # states it on `define_power_switch_cell -stage_1_output`/
                # `-stage_2_output`, a LIBRARY CELL DEFINITION command
                # this app does not generate at all. Reported rather than
                # faked -- see power_switch_ack_cpf_warnings().
                if s.ack_signal:
                    # `-acknowledge_receiver_1`, NOT `-acknowledge_receiver`
                    # (audit finding H3). The unnumbered spelling is CPF
                    # 1.0's, where the option also took a PIN; 2.0/2.1
                    # number it and take an expression, paired with the
                    # correspondingly numbered `-enable_condition_x`:
                    # "If a switch rule has -acknowledge_receiver_2
                    # specified, it must also have
                    # -acknowledge_receiver_1" (Si2 CPF 2.1, p. 266).
                    block.append(f"update_power_switch_rule -name {s.name} -acknowledge_receiver_1 {{{s.ack_signal}}}")
                block.append(
                    f"#   {s.name}: output_supply={target.name} cell_pin_in={s.input_supply_port!r} cell_pin_out={s.output_supply_port!r} "
                    f"control_port={s.control_port!r} ack_port={s.ack_port!r} on_state={s.on_state!r} off_state={s.off_state!r}"
                )
                switch_blocks.append(block)
        lines += _join_blocks(switch_blocks)
        lines.append("")

    # Which domain, if any, a Supply Set is the PRIMARY of -- the only
    # honest way to turn this app's isolation Supply Set into CPF's own
    # `-secondary_domain`, which names a domain rather than a net pair.
    secondary_domain_for_supply_set = {
        d.primary_supply_set: d.name
        for d in domain_state.domains.values()
        if d.primary_supply_set
    }
    lines += _isolation_cpf_lines(
        isolation_state, domain_names, domain_state.supply_sets,
        version=version, secondary_domain_for_supply_set=secondary_domain_for_supply_set,
    )

    if level_shifter_state and level_shifter_state.strategies:
        # Real CPF `create_level_shifter_rule` syntax, verified against
        # this project's OWN backend CPF parser
        # (backend/elaborator/power_intent/cpf/verbs.py's own
        # `_create_level_shifter_rule`/registration): options are
        # `-name`/`-pins`/`-from`/`-to`/`-exclude` -- genuinely NO
        # `-domain` flag, and `-from`/`-to` are a DOMAIN PAIR (which
        # domain is low, which is high), not the single `power_domain_id`
        # this app's own LevelShifterStrategyPlacement tracks, so there
        # is no honest way to populate them here. Same "not confident
        # enough to assert a fabricated flag, comment instead" precedent
        # Retention Strategies' own CPF section above already
        # establishes: only `-name`/`-pins` (both real, well-documented,
        # and both have data this app actually tracks) are emitted;
        # Power Domain/Applies To/Rule/Location/Threshold are all
        # surfaced in an informational comment line instead.
        #
        # Cell Type is the ONE exception -- `update_level_shifter_rules
        # -names <rule> -cells {<cell>}` IS a real, spec-verified CPF
        # equivalent (Si2 CPF 1.0 primary spec: "-cells cell_list:
        # Specifies the names of the library cells to be used to bridge
        # the specified power domains"). Same `-library_set`-omitted
        # reasoning as Power Switch's own `update_power_switch_rule`
        # above. Emitted ONLY when set; unset is surfaced as a `#`
        # comment.
        lines += _section_header("Level Shifter Rules")
        domain_names = {id_: d.name for id_, d in domain_state.domains.items()}
        ls_blocks: list[list[str]] = []
        ls_unsupported_locations: list[tuple[str, str]] = []
        for ls in sorted(level_shifter_state.strategies.values(), key=lambda s: s.name):
            domain_name = domain_names.get(ls.power_domain_id) or "?"
            # The rule, its own `update_level_shifter_rules` refinement
            # and its summary comment are ONE instance.
            parts = [f"create_level_shifter_rule -name {ls.name}"]
            # `-from <domain>` and `-location` WERE being dropped -- the
            # Power Domain and Location were surfaced only in the comment
            # line below, so a generated rule came back attached to no
            # domain at all. Both are in this project's own hand-written
            # golden.cpf (`create_level_shifter_rule -name LS_SUB_OUT
            # -from PD_SUB` / `update_level_shifter_rules -names
            # LS_SUB_OUT -location parent`), and the adjacent Isolation
            # section already emits `-from` the same way, so this was an
            # inconsistency rather than a deliberate omission. The
            # `-from`/`-to` PAIR meaning (which domain is low, which is
            # high) is still not asserted -- only the single domain this
            # app actually tracks goes in `-from`, which is exactly what
            # golden does.
            if domain_name != "?":
                parts.append(f"-from {domain_name}")
            if ls.elements:
                element_paths = sorted(_element_bare_name(e) for e in ls.elements)
                parts.append(_multiline_braced_list("-pins", element_paths))
            block = [" ".join(parts)]
            update_parts = [f"update_level_shifter_rules -names {ls.name}"]
            if ls.location:
                # Same 2.1-only gate as the isolation writer: the CPF 2.1
                # change log lists BOTH commands together -- "Level
                # Shifting Enhancements -- Added support for location
                # self and automatic" (Si2 CPF 2.1, p. 9).
                if ls.location in _CPF_2_1_ONLY_LOCATIONS and not _cpf_supports_2_1(version):
                    ls_unsupported_locations.append((ls.name, ls.location))
                else:
                    update_parts.append(f"-location {ls.location}")
            if ls.cell_type:
                update_parts.append(f"-cells {{{ls.cell_type}}}")
            # `-prefix string`: "Specifies the prefix to be used when
            # creating this logic. Default: CPF_LS_" -- Si2 CPF 1.0
            # primary spec, same as the isolation counterpart above.
            if ls.name_prefix:
                update_parts.append(f"-prefix {_tcl_word(ls.name_prefix)}")
            if len(update_parts) > 1:
                block.append(" ".join(update_parts))
            # Applies To/Rule/Threshold genuinely have no CPF flag this
            # writer is confident in -- still a comment, same "not
            # confident enough to assert a fabricated flag" precedent.
            block.append(
                f"#   {ls.name}: applies_to={ls.applies_to!r} rule={ls.rule!r} threshold={ls.threshold!r}"
            )
            ls_blocks.append(block)
        lines += _join_blocks(ls_blocks)
        for name, location in ls_unsupported_locations:
            lines.append(f"#   {name}: -location {location} needs CPF 2.1 (added there; this file declares {version}) -- omitted")
        lines.append("")

    # The two-tier model, CPF-side. REPLACES both the old
    # per-unique-state-name nominal conditions and the old
    # `create_power_mode -name` only block (whose -domain_conditions had
    # to be a comment, because the per-domain data did not exist until
    # the supply-set derivation below produced it).
    states_by_set, row_state_by_set = _derive_supply_set_states(
        supply_state, domain_state, power_state_table_state
    )
    lines += _cpf_power_state_lines(
        supply_state, domain_state, power_state_table_state, row_state_by_set
    )

    connections = _macro_connections(supply_state)
    if connections:
        lines += _section_header("Macro Connections")
        # `create_global_connection`, NOT `connect_supply_net` (audit
        # finding C1, and the most clear-cut of them). `connect_supply_net`
        # is a UPF command: it occurs ZERO times in the CPF 1.0 spec and
        # zero times in the CPF 2.1 spec, and appears nowhere in CPF's
        # own command index. The CPF construct is
        # `create_global_connection -net net {-pins pin_list | -ports
        # port_list | -pg_type pg_type_string}` (Si2 CPF 2.1, p. 129) --
        # and `-pins` is the right half of that choice here, since a
        # macro PG pin is a pin, not a design port.
        #
        # PACKED (refined rule), same as the UPF section it mirrors.
        padded = _pad_name_column([net_name for net_name, _ in connections], min_gap=1)
        for net_name, pin_ref in connections:
            lines.append(f"create_global_connection -net {padded[net_name]}-pins {{{pin_ref}}}")
        lines.append("")



    # "Load Upf" section title kept identical to the UPF side (explicit
    # request's own naming) even though CPF's own content here is a
    # comment, not a command -- see the comment block above
    # _macro_power_intent_upf_lines() for why no real CPF command exists.
    lines += _macro_power_intent_cpf_comment_lines(macro_power_intent_state)

    # Closes the `set_design` opened at the top (audit finding C2).
    lines += ["end_design", ""]

    return _ascii_whitespace("\n".join(lines).rstrip("\n") + "\n")
