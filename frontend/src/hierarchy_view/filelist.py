"""RTL filelist (`.f`) parsing.

A filelist is the standard way an RTL project names its sources: one
source path per line, plus `+define+`/`+incdir+` options and `-f`
references to further filelists. Every major simulator reads this
format, with minor variation at the edges.

WHAT THIS SUPPORTS, AND WHY THOSE FORMS. Verified against the Icarus
Verilog command-file format and against `dalance/verilog-filelist-parser`
(a reference implementation whose own test cases pin the exact shapes),
rather than written from memory:

    // a comment                  `//` and `#` both start a comment
    +define+NAME                  a bare define
    +define+NAME=VALUE            a value-carrying define
    +define+A=1+B+C=x+            several per line, `+`-separated,
                                  trailing `+` optional
    +incdir+dir1+dir2+            same `+`-separated, trailing-`+` shape
    -f other.f                    a nested filelist
    -F other.f                    likewise (see RELATIVE PATHS below)
    src/top.sv                    anything else is a source file

A define's VALUE may itself contain `=`: the split is at the FIRST one,
so `+define+W=a=b` defines `W` as `a=b`. That is the reference parser's
own documented behaviour, not an assumption.

An unrecognised option -- any other token starting with `-` or `+`,
e.g. `-y`, `-v`, `+libext+` -- is IGNORED rather than treated as a
source file or raised as an error. A real filelist routinely carries
tool-specific switches this tool has no use for, and refusing to read
the file because of one would be worse than skipping it. They are
collected in `ignored_options` so a caller can report them.

RELATIVE PATHS. Everything a filelist names -- sources, `+incdir+`
directories, nested filelists -- resolves against THE FILELIST'S OWN
DIRECTORY, so a filelist can be moved around with its tree intact.

    A REAL DIVERGENCE, FLAGGED RATHER THAN HIDDEN: in VCS, that is `-F`
    semantics. Its `-f` resolves relative to the directory the tool was
    INVOKED from. This app is a GUI with no meaningful invocation
    directory -- the process cwd is wherever the app happened to be
    started -- so `-f` under those semantics would resolve against
    something arbitrary. Both are therefore treated as `-F`. A filelist
    written for `-f`-with-absolute-paths is unaffected, since absolute
    paths are used as-is.

ENVIRONMENT VARIABLES. `$VAR`, `${VAR}` and `$(VAR)` are substituted,
matching the reference parser. An undefined variable expands to empty,
which is what the shell does and what a filelist author expects.

CYCLES. A filelist that reaches itself, directly or through a chain, is
reported as a `FilelistError` naming the cycle -- never followed into
infinite recursion.

NO QT, NO BACKEND. Pure functions over paths and text, so the parsing
rules can be tested exhaustively without a widget or an elaborator.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

_COMMENT_PREFIXES = ("//", "#")
_DEFINE_PREFIX = "+define+"
_INCDIR_PREFIX = "+incdir+"
_NESTED_FLAGS = ("-f", "-F", "-file")

# $VAR / ${VAR} / $(VAR) -- the three spellings the reference parser
# substitutes. Matched longest-form-first so `${VAR}` is not read as
# `$` followed by a literal `{VAR}`.
_ENV_VAR = re.compile(r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|\(([A-Za-z_][A-Za-z0-9_]*)\)|([A-Za-z_][A-Za-z0-9_]*))")


class FilelistError(Exception):
    """A filelist could not be read: missing, unreadable, or cyclic."""


@dataclass
class ParsedFilelist:
    """Everything one filelist (and its nested filelists) names.

    Paths are ABSOLUTE and in the order encountered -- a filelist's order
    is meaningful to some tools, so it is preserved rather than sorted.
    """

    files: list[str] = field(default_factory=list)
    include_dirs: list[str] = field(default_factory=list)
    # name -> value, where value is None for a bare `+define+NAME`.
    defines: dict[str, Optional[str]] = field(default_factory=dict)
    # Every filelist actually read, in order, starting with the entry
    # point -- what a caller shows as "loaded from".
    filelists: list[str] = field(default_factory=list)
    # Options recognised as options but not acted on, as (filelist, token).
    ignored_options: list[tuple[str, str]] = field(default_factory=list)


def substitute_env_vars(text: str) -> str:
    """`$VAR`, `${VAR}` and `$(VAR)` replaced by the environment's value,
    or by nothing when unset -- the shell's own behaviour."""
    def _replace(match: re.Match) -> str:
        name = match.group(1) or match.group(2) or match.group(3)
        return os.environ.get(name, "")

    return _ENV_VAR.sub(_replace, text)


def parse_defines_field(text: str) -> dict[str, Optional[str]]:
    """The "DEFINE:" field: a COMMA-separated list of defines.

        CPU, CORE=16, SRAM32x8   ->   {"CPU": None, "CORE": "16", "SRAM32x8": None}

    Whitespace around each entry is trimmed, so `CPU, CORE=16` and
    `CPU,CORE=16` are the same. An entry splits at its FIRST `=`, so a
    value may itself contain one -- consistent with how `+define+` is
    parsed, since the two sources are merged and should not disagree
    about what a define means. Empty entries (a trailing comma, a double
    comma) are skipped rather than producing a nameless define.
    """
    defines: dict[str, Optional[str]] = {}
    for entry in text.split(","):
        entry = entry.strip()
        if not entry:
            continue
        name, sep, value = entry.partition("=")
        name = name.strip()
        if not name:
            continue
        defines[name] = value.strip() if sep else None
    return defines


def _split_plus_list(remainder: str) -> list[str]:
    """`A+B+C+` -> ["A", "B", "C"]. The trailing `+` is conventional and
    optional; empty segments it produces are dropped."""
    return [part for part in remainder.split("+") if part]


def parse_filelist(path: str) -> ParsedFilelist:
    """Read `path` and every filelist it references.

    Raises `FilelistError` if the filelist (or a nested one) cannot be
    read, or if the references form a cycle.
    """
    result = ParsedFilelist()
    _parse_into(Path(path).expanduser(), result, chain=[])
    return result


def _parse_into(path: Path, result: ParsedFilelist, chain: list[Path]) -> None:
    try:
        resolved = path.resolve()
    except OSError as exc:  # pragma: no cover -- resolve() rarely raises
        raise FilelistError(f"Cannot resolve filelist '{path}': {exc}") from exc

    # Cycle detection is on the RESOLVED path, so `a.f` -> `./a.f` and
    # `../dir/a.f` are recognised as the same file rather than as a new
    # one each time round.
    if resolved in chain:
        cycle = " -> ".join(str(p) for p in [*chain, resolved])
        raise FilelistError(f"Filelist cycle detected: {cycle}")

    try:
        text = resolved.read_text()
    except OSError as exc:
        raise FilelistError(f"Cannot read filelist '{resolved}': {exc}") from exc

    result.filelists.append(str(resolved))
    base = resolved.parent
    chain = [*chain, resolved]

    for raw_line in text.splitlines():
        line = substitute_env_vars(raw_line).strip()
        if not line or line.startswith(_COMMENT_PREFIXES):
            continue

        if line.startswith(_DEFINE_PREFIX):
            for entry in _split_plus_list(line[len(_DEFINE_PREFIX):]):
                name, sep, value = entry.partition("=")
                if name:
                    result.defines[name] = value if sep else None
            continue

        if line.startswith(_INCDIR_PREFIX):
            for entry in _split_plus_list(line[len(_INCDIR_PREFIX):]):
                directory = _absolute(entry, base)
                if directory not in result.include_dirs:
                    result.include_dirs.append(directory)
            continue

        nested = _nested_filelist_target(line)
        if nested is not None:
            if not nested:
                # `-f` with nothing after it: a malformed line, not a
                # source file called "-f".
                result.ignored_options.append((str(resolved), line))
                continue
            _parse_into(Path(_absolute(nested, base)), result, chain)
            continue

        if line.startswith(("-", "+")):
            # A tool-specific switch this app has no use for. Recorded,
            # not mistaken for a source file and not fatal.
            result.ignored_options.append((str(resolved), line))
            continue

        source = _absolute(line, base)
        if source not in result.files:
            result.files.append(source)


def _nested_filelist_target(line: str) -> Optional[str]:
    """The path a `-f`/`-F`/`-file` line points at, `""` if the flag was
    given with no argument, or None if this is not a nested-filelist
    line at all.

    Both the `-f path` and `-f=path` spellings are accepted; the bare
    flag glued to its path (`-fpath`) is deliberately NOT, since it
    cannot be told apart from an unrelated option starting with `-f`.
    """
    for flag in _NESTED_FLAGS:
        if line == flag:
            return ""
        for separator in (" ", "\t", "="):
            if line.startswith(flag + separator):
                return line[len(flag) + 1:].strip()
    return None


def _absolute(entry: str, base: Path) -> str:
    """`entry` resolved against the FILELIST'S own directory -- see this
    module's docstring on why that is the rule for both `-f` and `-F`.
    An already-absolute path is returned unchanged (beyond `~`
    expansion)."""
    candidate = Path(entry).expanduser()
    if candidate.is_absolute():
        return str(candidate)
    return str((base / candidate).resolve())


def merge_defines(
    filelist_defines: dict[str, Optional[str]],
    manual_defines: dict[str, Optional[str]],
) -> tuple[dict[str, Optional[str]], list[tuple[str, Optional[str], Optional[str]]]]:
    """Both define sources combined, plus the conflicts that were resolved.

    PRECEDENCE: the MANUALLY-ENTERED field wins (confirmed explicitly).
    It is the more immediate and explicit action, and it is the one the
    user can see and edit right there in the tab; a filelist is imported
    configuration. The two are COMBINED, never one instead of the other.

    The returned conflict list is `(name, filelist_value, manual_value)`
    for each name present in both with DIFFERENT values -- so an
    override is reported rather than applied silently. A name in both
    with the SAME value is not a conflict and is not reported.
    """
    merged = dict(filelist_defines)
    conflicts: list[tuple[str, Optional[str], Optional[str]]] = []
    for name, value in manual_defines.items():
        if name in filelist_defines and filelist_defines[name] != value:
            conflicts.append((name, filelist_defines[name], value))
        merged[name] = value
    return merged, conflicts


def format_define(name: str, value: Optional[str]) -> str:
    """One define as a `` `define `` directive line."""
    return f"`define {name}" if value is None else f"`define {name} {value}"
