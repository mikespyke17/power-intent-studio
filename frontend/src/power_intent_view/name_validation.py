"""Shared Name-field validation, reused by every dialog that collects a
Power Domain / Supply Set / Retention Strategy / Supply Port name --
explicit request: ONE shared utility rather than four separate inline
checks, so a future naming rule (if any gets added later) only needs to
change here.

Callers are expected to pass an already-trimmed name -- every dialog
here already strips leading/trailing whitespace in its own `name`
property (e.g. AddPowerSupplyDialog.name -> self.name_edit.text().strip()),
so incidental copy-paste whitespace at the edges never reaches this
check at all; only a genuine space WITHIN the name is ever rejected.
"""

NO_SPACES_ERROR = "Name cannot contain spaces."


def validate_no_spaces(name: str) -> bool:
    """True if `name` contains no space character, False otherwise."""
    return " " not in name
