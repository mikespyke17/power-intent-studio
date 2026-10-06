"""Shared "Applies to: <hierarchy path(s)>" read-only field (explicit
request): Power Domain, Retention, Isolation, Level Shifter, and Power
Switch dialogs all show which hierarchy block(s)/target(s) the action
applies to, using ONE consistent visual treatment rather than several
separate implementations. Purely informational -- display-only, never
affects validation/submission logic in any of those dialogs.

Explicit follow-up request: when several hierarchies are listed, each
one must render on its OWN LINE, not comma-joined on a single line --

    Applies to:   <hierarchy_1>
                  <hierarchy_2>

Explicit follow-up request (cap at 10): a multi-hierarchy construct
can span far more than a few member hierarchies -- past
`_MAX_HIERARCHIES_SHOWN`, only the first 10 (in whatever order the
caller's own `paths` already come in -- this function doesn't re-sort)
are shown, followed by a single "... and N more" line naming the exact
remaining count, rather than either silently truncating with no
indication more exist, or letting the dialog grow unbounded for a
construct spanning dozens of hierarchies. A list of exactly 10 or
fewer is shown in full, with no indicator line at all -- the cap only
ever adds a line, never removes information that would otherwise fit.

`make_target_hierarchy_label()` returns only the VALUE half (the
newline-joined path list, possibly capped) -- callers add it as a
normal two-argument QFormLayout row,
`form.addRow("Applies to:", make_target_hierarchy_label(paths))`, so
the "Applies to:" caption lines up in the label column exactly like
every other field in these dialogs, and the value column stacks one
path (or the truncation line) per line beneath it."""

from __future__ import annotations

from PySide6.QtWidgets import QLabel

APPLIES_TO_CAPTION = "Applies to:"
_MAX_HIERARCHIES_SHOWN = 10


def _capped_lines(paths: list[str]) -> list[str]:
    if len(paths) <= _MAX_HIERARCHIES_SHOWN:
        return list(paths)
    shown = paths[:_MAX_HIERARCHIES_SHOWN]
    remaining = len(paths) - len(shown)
    return [*shown, f"... and {remaining} more"]


def make_target_hierarchy_label(paths: list[str]) -> QLabel:
    """`paths` -- one or more absolute instance paths (e.g. "top/u_core")
    -- shown one per line, e.g. "top/u_core\\ntop/u_dsp" for a multi-
    hierarchy Power Domain, or just "top/u_core" for a single-target
    action (Add Supply Port/Add Internal Supply, or Add Retention on a
    single-hierarchy Power Domain). Capped at `_MAX_HIERARCHIES_SHOWN`
    -- see module docstring."""
    label = QLabel("\n".join(_capped_lines(paths)))
    label.setStyleSheet("color: palette(mid);")
    return label
