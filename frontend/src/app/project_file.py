"""`.pis` project file: save/load the ENTIRE Power Intent Studio
project (explicit request).

FORMAT (confirmed with the user -- gzip-compressed JSON, NOT genuine
encryption): the logical payload is a plain JSON object; it is
gzip-compressed to binary before being written to disk. This makes the
file unreadable/uneditable in a plain text editor (it shows binary
garbage), and -- the actual point -- a text editor cannot re-save it
without destroying the gzip framing, so an accidental open-and-save in
an editor fails LOUDLY on the next load (`deserialize_project()` raises
`ProjectFileError`) rather than silently corrupting the data. This is
proportionate to the stated goal (corruption prevention), not
confidentiality.

OBJECT SHARING (CRITICAL -- CHANGE 2): a Supply Set can be a SINGLE
SHARED object referenced by multiple Power Domains. That sharing is
already represented in the live data model by ID: `PowerDomainDiagramState`
keeps one flat `supply_sets: dict[id -> SupplySetPlacement]`, and each
`PowerDomainPlacement` only ever holds supply-set ID STRINGS
(`primary_supply_set`, `secondary_supply_sets`). The generic codec
below serializes that structure verbatim -- one `SupplySetPlacement`
object under its id, ID references from the domains -- so a round-trip
naturally reconstructs the shared object as a single object, never
duplicated/un-shared. `test_project_file.py` verifies this explicitly.

CODEC: a small generic dataclass round-tripper (`_encode`/`_decode`)
rather than ~25 hand-written per-type serializers -- every state
dataclass here is plain data, so the only genuine special cases are
QColor (-> "#AARRGGBB"), Enum (-> class name + member name), and dicts
with non-str keys (`SupplyPortDiagramState.manual_connector_offsets` is
keyed by a `(str, str)` tuple). `_TYPE_REGISTRY` maps a dataclass/enum
name back to its class on decode. Every registered class is a plain
dataclass whose `__init__` accepts all its own fields by keyword, so
`cls(**decoded_fields)` reconstructs it exactly (the few classes with a
`__post_init__` that reconciles `owner_path`/`owner_paths` or
`target_id`/`target_ids` are fed BOTH, already mutually consistent, so
the reconciliation is a no-op)."""

from __future__ import annotations

import dataclasses
import enum
import gzip
import json
from typing import Any

from PySide6.QtGui import QColor

from frontend.src.power_intent_view.isolation_overlay import IsolationDiagramState, IsolationStrategyPlacement
from frontend.src.power_intent_view.level_shifter_overlay import LevelShifterDiagramState, LevelShifterStrategyPlacement
from frontend.src.power_intent_view.macro_power_intent_overlay import MacroPowerIntentDiagramState
from frontend.src.power_intent_view.port_attribute_overlay import PortAttributeDiagramState
from frontend.src.power_intent_view.power_domain_overlay import PowerDomainDiagramState, PowerDomainPlacement, SupplySetPlacement
from frontend.src.power_intent_view.power_state_table import PowerStateTableDiagramState, PowerStateTableRow
from frontend.src.power_intent_view.power_switch_overlay import PowerSwitchDiagramState, PowerSwitchPlacement
from frontend.src.power_intent_view.retention_overlay import RetentionDiagramState, RetentionStrategyPlacement
from frontend.src.power_intent_view.supply_port_overlay import SupplyPortDiagramState, SupplyPortPlacement, SupplyPortState
from frontend.src.shared.models.rtl import (
    ContinuousAssign,
    Definition,
    Design,
    Instance,
    Modport,
    ModportPort,
    Net,
    Port,
    PortConnection,
    PortDirection,
    SequentialElement,
    SourceLanguage,
    SourceLoc,
    WellBiasPin,
)
from frontend.src.shared.models.rtl import InterfacePort

_FORMAT_TAG = "power-intent-studio-project"
# Deliberately still 1 although the payload has since gained a section
# (`macro_power_intent_state`). Backward compatibility is handled: that
# key is optional on read, so files written before it existed still
# load. The FORWARD direction is not, and the audit (category 11)
# recorded the property rather than changing it: a file written by this
# build and opened by an older one passes that build's required-key
# check on the original ten keys and silently ignores the eleventh.
# Acceptable for a single-user tool -- bumping the version instead would
# hard-reject every already-saved project over one additive field.
_FORMAT_VERSION = 1

_REGISTERED: tuple[type, ...] = (
    # RTL model
    SourceLoc,
    Port,
    Net,
    PortConnection,
    Instance,
    SequentialElement,
    ModportPort,
    Modport,
    InterfacePort,
    ContinuousAssign,
    WellBiasPin,
    Definition,
    Design,
    PortDirection,
    SourceLanguage,
    # diagram-only state
    SupplyPortState,
    SupplyPortPlacement,
    SupplyPortDiagramState,
    SupplySetPlacement,
    PowerDomainPlacement,
    PowerDomainDiagramState,
    RetentionStrategyPlacement,
    RetentionDiagramState,
    PortAttributeDiagramState,
    PowerSwitchPlacement,
    PowerSwitchDiagramState,
    IsolationStrategyPlacement,
    IsolationDiagramState,
    LevelShifterStrategyPlacement,
    LevelShifterDiagramState,
    PowerStateTableRow,
    PowerStateTableDiagramState,
    MacroPowerIntentDiagramState,
)
_TYPE_REGISTRY: dict[str, type] = {c.__name__: c for c in _REGISTERED}


class ProjectFileError(Exception):
    """Raised for any unreadable/corrupt/wrong-format `.pis` file -- a
    single clean exception type the UI layer can catch and turn into a
    user-facing 'could not load' message (never a raw crash, never a
    silent partial load)."""


# --- generic codec --------------------------------------------------------


def _encode(obj: Any) -> Any:
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, enum.Enum):
        return {"__enum__": type(obj).__name__, "member": obj.name}
    if isinstance(obj, QColor):
        return {"__qcolor__": obj.name(QColor.NameFormat.HexArgb)}
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {"__dc__": type(obj).__name__, "fields": {f.name: _encode(getattr(obj, f.name)) for f in dataclasses.fields(obj)}}
    if isinstance(obj, dict):
        return {"__map__": [[_encode(k), _encode(v)] for k, v in obj.items()]}
    if isinstance(obj, (list, tuple)):
        return {"__seq__": "tuple" if isinstance(obj, tuple) else "list", "items": [_encode(x) for x in obj]}
    raise ProjectFileError(f"cannot serialize value of type {type(obj).__name__}")


def _decode(data: Any) -> Any:
    if not isinstance(data, dict):
        return data
    if "__enum__" in data:
        cls = _TYPE_REGISTRY.get(data["__enum__"])
        if cls is None:
            raise ProjectFileError(f"unknown enum type {data['__enum__']!r}")
        return getattr(cls, data["member"])
    if "__qcolor__" in data:
        return QColor(data["__qcolor__"])
    if "__seq__" in data:
        items = [_decode(x) for x in data["items"]]
        return tuple(items) if data["__seq__"] == "tuple" else items
    if "__map__" in data:
        return {_decode(k): _decode(v) for k, v in data["__map__"]}
    if "__dc__" in data:
        cls = _TYPE_REGISTRY.get(data["__dc__"])
        if cls is None:
            raise ProjectFileError(f"unknown dataclass type {data['__dc__']!r}")
        return cls(**{k: _decode(v) for k, v in data["fields"].items()})
    raise ProjectFileError(f"unrecognized encoded object with keys {sorted(data)}")


# --- public API ---------------------------------------------------------


@dataclasses.dataclass
class ProjectData:
    """Everything needed to fully reconstruct a project on load."""

    project_name: str
    rtl_design: Design
    supply_state: SupplyPortDiagramState
    domain_state: PowerDomainDiagramState
    retention_state: RetentionDiagramState
    port_attribute_state: PortAttributeDiagramState
    power_switch_state: PowerSwitchDiagramState
    isolation_state: IsolationDiagramState
    level_shifter_state: LevelShifterDiagramState
    power_state_table_state: PowerStateTableDiagramState
    # Added after format version 1 shipped -- defaulted (not required in
    # `deserialize_project()` below) so an older .pis file with no
    # section for this still loads cleanly as "no attachments", rather
    # than bumping _FORMAT_VERSION and hard-breaking every already-saved
    # project file over one small, purely additive feature.
    macro_power_intent_state: MacroPowerIntentDiagramState = dataclasses.field(default_factory=MacroPowerIntentDiagramState)
    # The hierarchies hidden in the diagram (the Hierarchy Tree's Hide /
    # the diagram's right-click Hide) -- explicitly hidden paths only,
    # exactly as HierarchyTreeWidget holds them. Optional and defaulted,
    # same reasoning as `macro_power_intent_state`: a .pis saved before
    # this existed loads with nothing hidden.
    hidden_paths: list[str] = dataclasses.field(default_factory=list)


def serialize_project(data: ProjectData) -> bytes:
    """`ProjectData` -> JSON -> gzip bytes, ready to write to a `.pis`
    file. Deterministic key order so an unchanged project round-trips to
    byte-identical output (helps tests and diffs)."""
    payload = {
        "format": _FORMAT_TAG,
        "version": _FORMAT_VERSION,
        "project_name": data.project_name,
        "rtl_design": _encode(data.rtl_design),
        "supply_state": _encode(data.supply_state),
        "domain_state": _encode(data.domain_state),
        "retention_state": _encode(data.retention_state),
        "port_attribute_state": _encode(data.port_attribute_state),
        "power_switch_state": _encode(data.power_switch_state),
        "isolation_state": _encode(data.isolation_state),
        "level_shifter_state": _encode(data.level_shifter_state),
        "power_state_table_state": _encode(data.power_state_table_state),
        "macro_power_intent_state": _encode(data.macro_power_intent_state),
        "hidden_paths": sorted(data.hidden_paths),
    }
    raw = json.dumps(payload, sort_keys=True).encode("utf-8")
    return gzip.compress(raw)


def deserialize_project(blob: bytes) -> ProjectData:
    """gzip bytes -> `ProjectData`. Raises `ProjectFileError` for
    anything that isn't a valid, complete `.pis` payload -- a truncated/
    corrupted file (bad gzip framing or bad JSON), a wrong `format`
    tag, an unknown `version`, or a missing top-level section -- so the
    caller can surface one clear 'could not load' error instead of
    crashing or half-loading garbage."""
    try:
        raw = gzip.decompress(blob)
    except (OSError, EOFError, gzip.BadGzipFile) as exc:
        raise ProjectFileError(f"not a valid .pis file (bad gzip data): {exc}") from exc
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ProjectFileError(f"not a valid .pis file (bad JSON payload): {exc}") from exc
    if not isinstance(payload, dict) or payload.get("format") != _FORMAT_TAG:
        raise ProjectFileError("not a Power Intent Studio project file")
    if payload.get("version") != _FORMAT_VERSION:
        raise ProjectFileError(f"unsupported .pis version {payload.get('version')!r} (this build reads version {_FORMAT_VERSION})")
    required = (
        "project_name",
        "rtl_design",
        "supply_state",
        "domain_state",
        "retention_state",
        "port_attribute_state",
        "power_switch_state",
        "isolation_state",
        "level_shifter_state",
        "power_state_table_state",
    )
    missing = [k for k in required if k not in payload]
    if missing:
        raise ProjectFileError(f"incomplete .pis file (missing sections: {', '.join(missing)})")
    # Audit fix (category 11): presence was checked, but not that the
    # value behind the key is actually there. A payload whose
    # `rtl_design` was null passed every gate above and then failed deep
    # inside load_project() with `AttributeError: 'NoneType' object has
    # no attribute 'walk'` -- which is neither OSError nor
    # ProjectFileError, so it escaped the caller's handler entirely and
    # surfaced as an uncaught crash rather than this module's own
    # promised "one clear could-not-load message". Checked for every
    # required section, not just rtl_design: none of them are legitimately
    # null, and a null anywhere would fail just as opaquely downstream.
    empty = [k for k in required if payload[k] is None]
    if empty:
        raise ProjectFileError(f"incomplete .pis file (empty sections: {', '.join(empty)})")
    try:
        return ProjectData(
            project_name=payload["project_name"],
            rtl_design=_decode(payload["rtl_design"]),
            supply_state=_decode(payload["supply_state"]),
            domain_state=_decode(payload["domain_state"]),
            retention_state=_decode(payload["retention_state"]),
            port_attribute_state=_decode(payload["port_attribute_state"]),
            power_switch_state=_decode(payload["power_switch_state"]),
            isolation_state=_decode(payload["isolation_state"]),
            level_shifter_state=_decode(payload["level_shifter_state"]),
            power_state_table_state=_decode(payload["power_state_table_state"]),
            # Not in `required` above -- see ProjectData's own field
            # comment: absent (a pre-existing .pis file) means "no
            # attachments", not an error.
            macro_power_intent_state=_decode(payload["macro_power_intent_state"]) if "macro_power_intent_state" in payload else MacroPowerIntentDiagramState(),
            # Optional too: absent means "nothing hidden".
            hidden_paths=[str(p) for p in payload.get("hidden_paths") or []],
        )
    except ProjectFileError:
        raise
    except (TypeError, KeyError, AttributeError) as exc:
        raise ProjectFileError(f"could not reconstruct project from .pis file: {exc}") from exc
