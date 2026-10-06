"""CPF create_X/update_X by-name merging.

Retargeted here from an earlier review round's UPF-scoped proposal, after
that proposal was found to rest on a mistaken premise (see
docs/milestone2_powerintent_elaborator.md's "Accumulate-then-freeze" section --
UPF's own commands don't support by-name refinement the way CPF's
create_X/update_X pairs verifiably do). This is the real, CPF-specific
version of that concept.

Verified directly against the primary CPF spec: update_power_domain,
update_isolation_rules, and update_level_shifter_rules all follow the same
"append implementation information to an already-created, named construct"
pattern -- each only ever specifies the fields it means to add or change,
never a wholesale replace. No conflict-resolution logic is needed beyond
"look the existing builder up by name and set whatever fields were given" --
confirmed empirically for update_power_domain in the earlier CPF-spec
research (create_power_domain and update_power_domain set disjoint field
sets).

Wildcard name matching (CPF's spec notes "-names rule_list... can contain
wildcards") is explicitly NOT supported in this pass -- exact-name lookup
only, raising a clear error if the name isn't found. Deferred, not guessed
at, same discipline as UPF's -on_state/-off_state.
"""

from __future__ import annotations


def apply_update(builder, updates: dict[str, object]) -> None:
    """Mutates `builder` in place, setting only the fields present in
    `updates` -- callers build this dict from exactly the flags the update
    command was actually given, never a full field set."""
    for field_name, value in updates.items():
        setattr(builder, field_name, value)


def find_by_name(builders: dict[str, object], name: str, command_name: str, kind: str):
    """Exact-name lookup among already-created builders (keyed by id, not
    name) for an update_X command. Raises clearly if not found, or if the
    name is ambiguous (matches more than one scope) -- update_X commands
    verified so far take a single bare name, not a scope-qualified path."""
    matches = [b for b in builders.values() if b.name == name]
    if not matches:
        raise ValueError(f"'{command_name}': no {kind} named '{name}' was created (create_* must precede update_*)")
    if len(matches) > 1:
        raise ValueError(
            f"'{command_name}': '{name}' matches {len(matches)} {kind}s at different scopes -- "
            "exact by-name lookup is ambiguous here, not yet supported"
        )
    return matches[0]
