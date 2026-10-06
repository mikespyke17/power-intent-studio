"""Mapping (RTL <-> power-intent resolution) view model.

Mirrors backend/mapping/schema.py field-for-field -- see rtl.py's module
docstring for why this is a duplicate, not an import.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from frontend.src.shared.models.power_intent import PowerIntentDesign
from frontend.src.shared.models.rtl import Design


class ResolutionKind(Enum):
    INSTANCE = "instance"
    PORT = "port"
    NET = "net"
    BLACK_BOX_PORT = "black_box_port"
    UNRESOLVABLE_BLACK_BOX = "unresolvable_black_box"
    VIRTUAL = "virtual"
    POWER_INTENT = "power_intent"
    DANGLING = "dangling"
    NOT_ATTEMPTED = "not_attempted"


@dataclass(frozen=True)
class ReferenceLocation:
    construct_kind: str
    construct_id: str
    field: str
    index: Optional[int] = None


@dataclass(frozen=True)
class ResolvedReference:
    raw: str
    location: ReferenceLocation
    kind: ResolutionKind
    resolved_path: Optional[str] = None
    resolved_definition_id: Optional[str] = None
    resolved_power_intent_id: Optional[str] = None
    detail: Optional[str] = None


@dataclass(frozen=True)
class MappedDesign:
    power_intent: PowerIntentDesign
    rtl: Design
    references: list[ResolvedReference] = field(default_factory=list)
