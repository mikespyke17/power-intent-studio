"""The diagnostics dock's unified row type.

Deliberately NOT a mirror of any single real backend type -- nothing in
the backend unifies RTL/UPF/CPF/mapping diagnostics into one shape today.
RTL and UPF/CPF elaboration raise plain exceptions on failure (no
diagnostic list at all); only backend/validation/'s Finding is a real,
structured list. See docs/frontend_milestone2_power_intent_visualization.md
(this module's own design notes) for the file/line availability table
this shape was designed against -- updated when the dock switched from
Line/Column to File/Line columns (Column dropped entirely, not kept
alongside File):

  source          | file              | line
  ----------------|-------------------|------------------
  RTL elaboration | always present    | always present
  UPF/CPF elab.   | always present    | sometimes present
  mapping/valid.  | doesn't exist     | doesn't exist

`code` has no real backend equivalent whatsoever -- not on Finding, not
on ResolutionKind, nowhere. It's a UI-invented scheme (see
mock_backend/mock_mapping.py's finding_to_diagnostic() for the actual
codes used), documented here as invented, not mirrored.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from frontend.src.shared.models.validation import Severity


@dataclass(frozen=True)
class Diagnostic:
    severity: Severity
    code: str
    message: str
    file: Optional[str] = None
    line: Optional[int] = None
    source: str = ""  # "load_design" | "load_power_intent" | "mapping" -- drives dock filtering
