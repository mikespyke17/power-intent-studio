"""Validation report view model.

Mirrors backend/validation/schema.py field-for-field -- see rtl.py's
module docstring for why this is a duplicate, not an import.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from frontend.src.shared.models.mapping import ResolvedReference


class Severity(Enum):
    ERROR = "error"
    WARNING = "warning"
    EXCLUDED = "excluded"


@dataclass(frozen=True)
class Finding:
    severity: Severity
    summary: str
    reference: Optional[ResolvedReference] = None
    count: int = 1


@dataclass(frozen=True)
class ValidationReport:
    findings: list[Finding]

    @property
    def has_errors(self) -> bool:
        return any(f.severity == Severity.ERROR for f in self.findings)
