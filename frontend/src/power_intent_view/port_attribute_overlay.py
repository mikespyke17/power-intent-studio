"""Port Attribute -- explicit request: a per-top-level-port assignment
of a Supply Set, editable via "Add Port Attribute"/"Edit Port Attribute"
(top-level-hierarchy-only, see power_intent_diagram_view.py's own
_is_top_level_box() gating). Diagram-only for this pass, same "mock-
first" scoping decision already established for supply ports/power
domains/retention (see supply_port_overlay.py's own module docstring)
-- nothing here is reflected into the tab's PowerIntentDesign.

Scoped to the design's single top-level block only -- there is only
ever one, so no owner_path/owner_paths field is needed here (unlike
every other diagram-only construct in this app, which is scoped to a
potentially-any hierarchy block).

**Bit-wise ungrouping** (explicit follow-up request, replacing the
original one-combo-per-whole-port editor with a table letting each BIT
of a multi-bit port carry its own, independent assignment):
`port_attribute_row_keys()` below is the single, shared source of truth
for turning a `Port` into the assignment key(s) it contributes -- used
by both `port_attribute_dialog.py` (building/reading the table's own
rows) and `power_resolution_cache.py` (resolving a top-level port's own
PortList boundary cell back from those keys). A genuinely single-bit
port (`width <= 1`) keeps its own bare name as the ONE key it has
always used (e.g. "clk") -- no "[0]" suffix, and no behavior change for
every existing single-bit assignment already saved under the old
one-key-per-port scheme. A multi-bit port (`width > 1`, e.g. `a[7:0]`)
now contributes one "<name>[i]" key per bit, 0-indexed ascending (e.g.
"a[0]".."a[7]") -- the same simple ascending convention properties_table_widget.py's
own arrayed-interface-port rows already use, not a MSB/LSB-aware range
index (no real fixture needs that distinction, and Port carries no
declared bit-range direction to derive it from anyway).

Deliberate, NOT-guessed-at scope cut: this is a clean-break migration,
not a backward-compatible one -- any OLD assignment stored under a
multi-bit port's own bare name (from before this rework existed) is
simply never read or written by the new per-bit scheme again. Nothing
in this codebase has ever shipped multi-bit-port Port Attribute data
(the feature itself is only a few commits old), so there is nothing
real to migrate."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from frontend.src.shared.models.rtl import Port


@dataclass
class PortAttributeDiagramState:
    """Shared, Qt-independent Port Attribute state for an entire Power
    Intent Diagram (every tab -- same cross-tab-sharing mechanism as
    SupplyPortDiagramState/PowerDomainDiagramState/RetentionDiagramState,
    see their own docstrings for the full reasoning). One instance is
    constructed once by PowerIntentDiagramView and handed to every
    _PowerIntentGraphicsView it creates.

    `assignments` maps a top-level port's own assignment key (see
    `port_attribute_row_keys()` above -- the bare port name for a
    single-bit port, or one "<name>[i]" key per bit for a multi-bit
    one) to the Supply Set id assigned to it, or None for an explicitly-
    unassigned key -- an empty selection is a valid, saved value
    (explicit request), not the same as "never visited"."""

    assignments: dict[str, Optional[str]] = field(default_factory=dict)


def port_attribute_row_keys(port: Port) -> list[str]:
    """One assignment key per row `port` contributes to the Port
    Attribute editor/table -- see this module's own docstring for the
    full "why" (bit-wise ungrouping, single-bit-port back-compat,
    0-indexed ascending convention, no migration of old data)."""
    if port.width <= 1:
        return [port.name]
    return [f"{port.name}[{i}]" for i in range(port.width)]


def copy_port_attribute_diagram_state(state: PortAttributeDiagramState) -> PortAttributeDiagramState:
    """An independent snapshot -- same "why" as every other diagram-only
    state's own copy_*_diagram_state(): Ctrl+Z/Ctrl+Y undo/redo needs a
    genuinely separate copy, not one still sharing the same mutable
    `assignments` dict object with the live state."""
    return PortAttributeDiagramState(assignments=dict(state.assignments))


def restore_port_attribute_diagram_state(state: PortAttributeDiagramState, snapshot: PortAttributeDiagramState) -> None:
    """Copies `snapshot`'s fields onto the *live* `state` object in
    place -- never replaces `state` itself, since it's shared by
    reference across every tab (see the class's own docstring)."""
    restored = copy_port_attribute_diagram_state(snapshot)
    state.assignments = restored.assignments
