"""Mapping IR (Milestone 3).

Resolves references embedded in an elaborated `PowerIntentDesign`
(Milestone 2) against an elaborated RTL `Design` (Milestone 1) and against
the power-intent design's own declared constructs. This module holds only
the result types -- pure data, no resolution logic (that's
rtl_resolver.py/power_intent_resolver.py/map_design.py).

A single power-intent field can resolve to one of seven kinds
(`ResolutionKind`): six real RTL-hierarchy outcomes (instance/port/net/
black-box-port/unresolvable-black-box/dangling) plus one non-RTL outcome
(virtual -- a declared logic/virtual port with no RTL backing, e.g. CPF's
`set_design -ports`). An eighth kind (`POWER_INTENT`) covers references
that were never meant to resolve against RTL at all -- a strategy naming a
domain, a switch naming a supply -- see
docs/milestone2_powerintent_elaborator.md's Milestone 3 planning section
for the full RTL-vs-intra-power-intent field classification this schema
implements.

A ninth kind (`NOT_ATTEMPTED`) is not a resolution outcome at all -- it
marks a real, reference-shaped field whose target dict isn't independently
verified yet (see map_design.py's `_NOT_YET_WIRED`). Emitted deliberately,
carrying the raw value with no lookup performed, so these fields are
visible in `MappedDesign.references` as an explicit "not checked" instead
of silently missing the way an un-visited field would be -- a consumer
(e.g. backend/validation/) can report this as a coverage gap rather than
have no way to distinguish "checked, fine" from "never looked at."
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from backend.elaborator.power_intent.ir import PowerIntentDesign
from backend.elaborator.rtl.ir import Design


class ResolutionKind(Enum):
    INSTANCE = "instance"
    PORT = "port"
    NET = "net"
    BLACK_BOX_PORT = "black_box_port"
    UNRESOLVABLE_BLACK_BOX = "unresolvable_black_box"
    VIRTUAL = "virtual"
    POWER_INTENT = "power_intent"
    DANGLING = "dangling"
    NOT_ATTEMPTED = "not_attempted"  # real reference-shaped field, no verified target dict yet -- see map_design._NOT_YET_WIRED


@dataclass(frozen=True)
class ReferenceLocation:
    """Where a raw reference string came from in the power-intent design --
    not where it resolved to (that's on ResolvedReference itself)."""

    construct_kind: str  # schema type name, e.g. "PowerDomain", "IsolationStrategy"
    construct_id: str  # the owning construct's power-intent id (make_power_intent_id)
    field: str  # e.g. "elements", "domain"
    index: Optional[int] = None  # position within a list field; None for scalar fields


@dataclass(frozen=True)
class ResolvedReference:
    raw: str  # the reference string exactly as authored
    location: ReferenceLocation
    kind: ResolutionKind
    # RTL-hierarchy kinds (INSTANCE/PORT/NET/BLACK_BOX_PORT/UNRESOLVABLE_BLACK_BOX):
    resolved_path: Optional[str] = None  # matched RTL hierarchy path, RTL Design's own separator
    resolved_definition_id: Optional[str] = None
    # POWER_INTENT kind:
    resolved_power_intent_id: Optional[str] = None
    # DANGLING/UNRESOLVABLE_BLACK_BOX: human-readable reason
    detail: Optional[str] = None


@dataclass(frozen=True)
class MappedDesign:
    power_intent: PowerIntentDesign
    rtl: Design
    references: list[ResolvedReference] = field(default_factory=list)
