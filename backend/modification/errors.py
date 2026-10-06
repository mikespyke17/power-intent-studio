"""Errors raised by backend/modification/ operations.

Plain ValueError subclasses with a clear message, matching the style
already established in upf/verbs.py and cpf/verbs.py -- except
DeleteBlocked and ScopeChangeBlocked, which also carry structured
ResolvedReference data (not just a formatted string) so a caller can
programmatically show exactly what's blocking, per the approved design.
"""

from __future__ import annotations

from typing import Optional

from backend.mapping import ResolvedReference


class ModificationError(ValueError):
    """Base class for all backend/modification/ errors."""


class ConstructNotFound(ModificationError):
    def __init__(self, kind: str, id_: str):
        super().__init__(f"no {kind} construct with id '{id_}'")
        self.kind = kind
        self.id = id_


class ConstructAlreadyExists(ModificationError):
    def __init__(self, kind: str, id_: str):
        super().__init__(f"{kind} construct with id '{id_}' already exists")
        self.kind = kind
        self.id = id_


class IdentityFieldInUpdate(ModificationError):
    """update() structurally rejects identity-determining fields (name,
    and for a UPF-sourced PowerState, applies_to too) and scope -- these
    aren't plain field mutations: they change the construct's id (name/
    applies_to) or the meaning of every relative-path field it carries
    (scope). Callers must go through rename() or change_scope() instead.
    """

    def __init__(self, kind: str, field_name: str):
        super().__init__(
            f"'{field_name}' is an identity field for {kind} and cannot be changed via update() -- "
            "use rename() (name/applies_to) or change_scope() (scope) instead"
        )
        self.kind = kind
        self.field = field_name


class DeleteBlocked(ModificationError):
    def __init__(self, kind: str, id_: str, blocking_references: list[ResolvedReference]):
        locations = ", ".join(
            f"{r.location.construct_kind}::{r.location.construct_id}.{r.location.field}" for r in blocking_references
        )
        super().__init__(f"cannot delete {kind} '{id_}': still referenced by {locations}")
        self.kind = kind
        self.id = id_
        self.blocking_references = blocking_references


class ScopeChangeBlocked(ModificationError):
    """Two distinct reasons a scope change can be blocked, both surfaced
    structurally: `missing_fields`/`extra_fields` (the construct's own
    relative-path fields weren't fully re-confirmed) and
    `blocking_references` (some other construct references this one by
    name, and a scope change would break that reference's scope-exact
    lookup -- see rtl_resolver.py/power_intent_resolver.py's scope-match
    requirement). Either can be non-empty; both are reported if both
    apply.
    """

    def __init__(
        self,
        kind: str,
        id_: str,
        missing_fields: Optional[list[str]] = None,
        extra_fields: Optional[list[str]] = None,
        blocking_references: Optional[list[ResolvedReference]] = None,
    ):
        missing_fields = missing_fields or []
        extra_fields = extra_fields or []
        blocking_references = blocking_references or []
        parts = []
        if missing_fields:
            parts.append(f"relative fields not re-confirmed: {missing_fields}")
        if extra_fields:
            parts.append(f"unexpected fields given (not relative fields on this construct): {extra_fields}")
        if blocking_references:
            locations = ", ".join(
                f"{r.location.construct_kind}::{r.location.construct_id}.{r.location.field}" for r in blocking_references
            )
            parts.append(f"still referenced by other constructs (scope change would break these): {locations}")
        super().__init__(f"cannot change scope of {kind} '{id_}': " + "; ".join(parts))
        self.kind = kind
        self.id = id_
        self.missing_fields = missing_fields
        self.extra_fields = extra_fields
        self.blocking_references = blocking_references


class VersionGateViolation(ModificationError):
    pass
