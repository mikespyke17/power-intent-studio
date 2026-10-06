"""Version-gated UPF flag values.

Small, explicit table of {flag value -> minimum UPF version}, checked at
the point a verb handler would otherwise accept any string unconditionally.
Currently one entry: isolation/level-shifter `-location fanout`, confirmed
3.1-only against the DVCon whitepaper read directly during version
research -- UPF 3.0's isolation example there only shows `-location
parent`; 3.1 introduces the path-based `fanout` semantics specifically
(see docs/milestone2_powerintent_elaborator.md's version-research section).

Deliberately UPF-specific, not a shared per-format gate mechanism: there's
no evidence CPF has any comparable version-gated value, and extracting a
shared abstraction before a second real consumer exists would be
premature -- same reasoning that retargeted precedence.py at CPF's
create/update merging instead of building it speculatively for UPF.
"""

from __future__ import annotations

_VERSION_ORDER = ["2.1", "3.0", "3.1"]

_LOCATION_VALUE_MINIMUM_VERSION = {
    "fanout": "3.1",
}


def _version_at_least(version: str, minimum: str) -> bool:
    return _VERSION_ORDER.index(version) >= _VERSION_ORDER.index(minimum)


def check_location_value(source_version: str, location, command_name: str) -> None:
    """Raises if `location` is a version-gated value not yet valid under
    `source_version`. `location=None` (not specified) always passes."""
    if location is None:
        return
    minimum = _LOCATION_VALUE_MINIMUM_VERSION.get(location)
    if minimum is not None and not _version_at_least(source_version, minimum):
        raise ValueError(
            f"'{command_name}': -location '{location}' requires UPF {minimum}+ (this design declared '{source_version}')"
        )
