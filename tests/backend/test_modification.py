"""Milestone 3b: backend/modification/, exercised against real elaborated
fixtures (the same UPF/RTL pairing used throughout test_mapping.py/
test_validation.py), not hand-built IR.
"""

from pathlib import Path

import pytest

from backend.elaborator.power_intent.cpf import elaborate as elaborate_cpf
from backend.elaborator.power_intent.ir import DomainSupplyConnection, IsolationStrategy, PowerDomain, make_power_intent_id
from backend.elaborator.power_intent.upf import elaborate as elaborate_upf
from backend.elaborator.rtl.verilog import elaborate as elaborate_verilog
from backend.mapping import map_design
from backend.modification import (
    ConstructAlreadyExists,
    ConstructNotFound,
    DeleteBlocked,
    IdentityFieldInUpdate,
    ScopeChangeBlocked,
    add,
    add_domain_supply_connection,
    change_scope,
    delete,
    remove_domain_supply_connections,
    rename,
    update,
)

RTL_DIR = Path(__file__).parent / "golden_rtl" / "mapping_smoke"
UPF_BASIC_DIR = Path(__file__).parent / "golden_upf" / "upf_basic"
SAME_NAME_DIR = Path(__file__).parent / "golden_upf" / "upf_same_name_different_scope"
V21_DIR = Path(__file__).parent / "golden_upf" / "upf_v21_for_modification"
CPF_BASIC_DIR = Path(__file__).parent / "golden_cpf" / "cpf_basic"


def _rtl():
    return elaborate_verilog([str(RTL_DIR / "top.v"), str(RTL_DIR / "core.v"), str(RTL_DIR / "dbg.v")])


def _upf_basic_mapped():
    power_intent = elaborate_upf(str(UPF_BASIC_DIR / "top.upf"))
    return power_intent, map_design(power_intent, _rtl())


def _cpf_basic_mapped():
    power_intent = elaborate_cpf(str(CPF_BASIC_DIR / "top.cpf"))
    return power_intent, map_design(power_intent, _rtl())


# --- update() structurally rejects identity fields ------------------------


def test_update_rejects_name_and_scope_but_allows_plain_fields():
    power_intent, mapped = _upf_basic_mapped()

    with pytest.raises(IdentityFieldInUpdate):
        update(power_intent, "PowerDomain", "::PD_TOP", {"name": "PD_RENAMED"})
    with pytest.raises(IdentityFieldInUpdate):
        update(power_intent, "PowerDomain", "::PD_TOP", {"scope": "u_core"})

    updated = update(power_intent, "PowerDomain", "::PD_TOP", {"primary_supply": "SS_CORE"})
    assert updated.domains["::PD_TOP"].primary_supply == "SS_CORE"
    assert power_intent.domains["::PD_TOP"].primary_supply is None  # original untouched -- full rebuild, not mutation


def test_update_nonexistent_raises_construct_not_found():
    power_intent, _ = _upf_basic_mapped()
    with pytest.raises(ConstructNotFound):
        update(power_intent, "PowerDomain", "::NOPE", {"primary_supply": "X"})


# --- rename() cascades correctly, verified against real cross-references --


def test_rename_cascades_across_every_wired_reference_kind():
    """PD_TOP is referenced from isolation/level-shifter/retention/switch/
    power-state domain fields AND from domain_supply_connections -- a real
    fixture exercising every kind of location rename must patch, not a
    constructed-to-pass scenario.
    """
    power_intent, mapped = _upf_basic_mapped()
    renamed = rename(power_intent, "PowerDomain", "::PD_TOP", {"name": "PD_CORE"}, mapped)

    assert "::PD_TOP" not in renamed.domains
    assert renamed.domains["::PD_CORE"].name == "PD_CORE"

    assert renamed.isolation_strategies["::ISO_CORE"].domain == "PD_CORE"
    assert renamed.isolation_strategies["::ISO_CORE"].source == "PD_CORE"
    assert renamed.isolation_strategies["::ISO_CORE"].sink == "PD_CORE"
    assert renamed.level_shifter_strategies["::LS_CORE"].domain == "PD_CORE"
    assert renamed.level_shifter_strategies["::LS_CORE"].source == "PD_CORE"
    assert renamed.level_shifter_strategies["::LS_CORE"].sink == "PD_CORE"
    assert renamed.retention_strategies["::RET_CORE"].domain == "PD_CORE"
    assert renamed.switches["::SW_CORE"].domain == "PD_CORE"
    assert renamed.power_states["::SS_CORE.ON"].domain == "PD_CORE"

    domain_conns = [c for c in renamed.domain_supply_connections if c.domain == "PD_CORE"]
    assert len(domain_conns) == 4  # every connection in upf_basic targets PD_TOP
    assert not any(c.domain == "PD_TOP" for c in renamed.domain_supply_connections)

    # original untouched
    assert power_intent.isolation_strategies["::ISO_CORE"].domain == "PD_TOP"


def test_rename_to_an_existing_name_raises_construct_already_exists():
    power_intent, mapped = _upf_basic_mapped()
    power_intent = add(
        power_intent,
        "PowerDomain",
        PowerDomain(id=make_power_intent_id("", "PD_OTHER"), name="PD_OTHER", scope=""),
    )
    mapped = map_design(power_intent, _rtl())
    with pytest.raises(ConstructAlreadyExists):
        rename(power_intent, "PowerDomain", "::PD_OTHER", {"name": "PD_TOP"}, mapped)


def test_rename_power_state_composite_identity_upf():
    """UPF PowerState ids are make_power_intent_id(scope, f"{applies_to}.
    {name}") -- renaming must move both the state's own dict key and, if
    applies_to changes too, follow the same composite rule. Confirms the
    format-dependent identity-fields logic isn't just a comment.
    """
    power_intent, mapped = _upf_basic_mapped()
    renamed = rename(power_intent, "PowerState", "::SS_CORE.ON", {"name": "ACTIVE"}, mapped)
    assert "::SS_CORE.ON" not in renamed.power_states
    assert "::SS_CORE.ACTIVE" in renamed.power_states
    assert renamed.power_states["::SS_CORE.ACTIVE"].name == "ACTIVE"
    assert renamed.power_states["::SS_CORE.ACTIVE"].applies_to == "SS_CORE"


def test_update_rejects_applies_to_for_upf_powerstate_but_allows_it_for_cpf():
    """The decisive check: _identity_fields() must actually branch on
    power_intent.source_format at call time, not just in a comment. UPF's
    add_power_state gives applies_to real identity weight (id embeds it);
    CPF's create_power_mode never sets applies_to at all (always None --
    confirmed by direct inspection of cpf_basic's elaborated PM1) and its
    id is plain name-only, so applies_to is an ordinary field there with
    no special meaning to protect. Both halves are exercised against real
    elaborated designs of each format, not one asserted and the other
    assumed to follow.
    """
    upf_power_intent, _ = _upf_basic_mapped()
    with pytest.raises(IdentityFieldInUpdate):
        update(upf_power_intent, "PowerState", "::SS_CORE.ON", {"applies_to": "SOMETHING_ELSE"})

    cpf_power_intent, _ = _cpf_basic_mapped()
    assert cpf_power_intent.power_states["::PM1"].applies_to is None  # sanity: CPF really never sets this
    updated = update(cpf_power_intent, "PowerState", "::PM1", {"applies_to": "SOMETHING_ELSE"})
    assert updated.power_states["::PM1"].applies_to == "SOMETHING_ELSE"
    assert "::PM1" in updated.power_states  # id unchanged -- applies_to carries no identity weight for CPF


def test_rename_applies_to_only_valid_as_identity_field_for_upf_not_cpf():
    """Mirrors the update() check from the CPF side: rename()'s identity-
    field whitelist must also reject 'applies_to' for a CPF PowerState
    (since _identity_fields() returns ("name",) there), while a plain
    'name' rename still works normally through the non-composite id path.
    """
    cpf_power_intent, cpf_mapped = _cpf_basic_mapped()
    with pytest.raises(ValueError, match="not identity fields"):
        rename(cpf_power_intent, "PowerState", "::PM1", {"applies_to": "X"}, cpf_mapped)

    renamed = rename(cpf_power_intent, "PowerState", "::PM1", {"name": "PM1_RENAMED"}, cpf_mapped)
    assert "::PM1_RENAMED" in renamed.power_states
    assert "::PM1" not in renamed.power_states


# --- delete() blocks with structured references ----------------------------


def test_delete_blocked_carries_every_real_referencer():
    power_intent, mapped = _upf_basic_mapped()
    with pytest.raises(DeleteBlocked) as exc_info:
        delete(power_intent, "PowerDomain", "::PD_TOP", mapped)

    refs = exc_info.value.blocking_references
    kinds = {r.location.construct_kind for r in refs}
    assert kinds == {"IsolationStrategy", "LevelShifterStrategy", "RetentionStrategy", "PowerSwitch", "PowerState", "DomainSupplyConnection"}
    assert len(refs) == 3 + 3 + 1 + 1 + 1 + 4  # iso(domain/source/sink) + ls(domain/source/sink) + ret + switch + state + 4 connections


def test_delete_succeeds_when_unreferenced():
    power_intent, mapped = _upf_basic_mapped()
    # VDD_IN is declared but nothing in upf_basic references the port
    # itself (SW_CORE references the "VDD"/"VDD_CORE" *nets*, not this port).
    result = delete(power_intent, "SupplyPort", "::VDD_IN", mapped)
    assert "::VDD_IN" not in result.supply_ports
    assert "::VDD_IN" in power_intent.supply_ports  # original untouched


# --- cross-scope collision safety (same name, different scope) -----------


def test_same_named_constructs_at_different_scopes_dont_collide():
    rtl = _rtl()
    power_intent = elaborate_upf(str(SAME_NAME_DIR / "top.upf"))
    mapped = map_design(power_intent, rtl)

    assert set(power_intent.domains) == {"::PD1", "u_core::PD1"}
    # ISO1 (declared at scope u_core) references the u_core-scoped PD1,
    # not the top-level one -- confirmed via the resolver, not assumed.
    iso_domain_ref = next(r for r in mapped.references if r.location.construct_kind == "IsolationStrategy" and r.location.field == "domain")
    assert iso_domain_ref.resolved_power_intent_id == "u_core::PD1"

    # Deleting the TOP-LEVEL PD1 must not be blocked by ISO1's reference
    # to the DIFFERENT, u_core-scoped PD1.
    result = delete(power_intent, "PowerDomain", "::PD1", mapped)
    assert "::PD1" not in result.domains
    assert "u_core::PD1" in result.domains

    # Deleting the u_core-scoped PD1 IS blocked by ISO1.
    with pytest.raises(DeleteBlocked) as exc_info:
        delete(power_intent, "PowerDomain", "u_core::PD1", mapped)
    assert len(exc_info.value.blocking_references) == 1
    assert exc_info.value.blocking_references[0].location.construct_id == "u_core::ISO1"

    # Renaming the TOP-LEVEL PD1 must not touch ISO1 (zero referencers).
    renamed = rename(power_intent, "PowerDomain", "::PD1", {"name": "PD1_RENAMED"}, mapped)
    assert renamed.isolation_strategies["u_core::ISO1"].domain == "PD1"


# --- change_scope(): blocked by unconfirmed relative fields AND/OR refs ---


def test_change_scope_blocked_by_missing_relative_fields_and_references_together():
    power_intent, mapped = _upf_basic_mapped()
    with pytest.raises(ScopeChangeBlocked) as exc_info:
        change_scope(power_intent, "PowerDomain", "::PD_TOP", "u_core", {}, mapped)
    err = exc_info.value
    assert set(err.missing_fields) == {"elements", "exclude_elements", "boundary_ports"}
    assert len(err.blocking_references) > 0  # PD_TOP is referenced by plenty in upf_basic


def test_change_scope_blocked_purely_by_references_when_fields_are_confirmed():
    power_intent, mapped = _upf_basic_mapped()
    with pytest.raises(ScopeChangeBlocked) as exc_info:
        change_scope(
            power_intent,
            "PowerDomain",
            "::PD_TOP",
            "u_core",
            {"elements": ["."], "exclude_elements": ["u_dbg"], "boundary_ports": []},
            mapped,
        )
    err = exc_info.value
    assert err.missing_fields == []
    assert err.extra_fields == []
    assert len(err.blocking_references) > 0


def test_change_scope_succeeds_when_unreferenced_and_fields_confirmed():
    power_intent, mapped = _upf_basic_mapped()
    power_intent = add(
        power_intent,
        "PowerDomain",
        PowerDomain(id=make_power_intent_id("", "PD_ISOLATED"), name="PD_ISOLATED", scope=""),
    )
    mapped = map_design(power_intent, _rtl())  # re-run after the add, per the approved full-rerun model

    moved = change_scope(power_intent, "PowerDomain", "::PD_ISOLATED", "u_core", {"elements": [], "exclude_elements": [], "boundary_ports": []}, mapped)
    assert "::PD_ISOLATED" not in moved.domains
    assert "u_core::PD_ISOLATED" in moved.domains
    assert moved.domains["u_core::PD_ISOLATED"].scope == "u_core"


# --- add(): id-matches-fields guard, duplicate rejection -------------------


def test_add_rejects_construct_whose_id_doesnt_match_its_own_fields():
    power_intent, _ = _upf_basic_mapped()
    bad = PowerDomain(id="::WRONG_ID", name="PD_NEW", scope="")
    with pytest.raises(ValueError, match="doesn't match"):
        add(power_intent, "PowerDomain", bad)


def test_add_rejects_duplicate_id():
    power_intent, _ = _upf_basic_mapped()
    dup = PowerDomain(id="::PD_TOP", name="PD_TOP", scope="")
    with pytest.raises(ConstructAlreadyExists):
        add(power_intent, "PowerDomain", dup)


# --- version_gates.py reuse at edit time ------------------------------------


def test_update_reuses_version_gate_for_location():
    power_intent = elaborate_upf(str(V21_DIR / "top.upf"))
    with pytest.raises(ValueError, match="3.1"):
        update(power_intent, "IsolationStrategy", "::ISO1", {"location": "fanout"})

    # a non-gated value still passes through update() normally
    updated = update(power_intent, "IsolationStrategy", "::ISO1", {"location": "self"})
    assert updated.isolation_strategies["::ISO1"].location == "self"


def test_add_reuses_version_gate_for_location():
    power_intent = elaborate_upf(str(V21_DIR / "top.upf"))
    bad = IsolationStrategy(
        id=make_power_intent_id("", "ISO_NEW"), name="ISO_NEW", scope="", domain="PD1", location="fanout"
    )
    with pytest.raises(ValueError, match="3.1"):
        add(power_intent, "IsolationStrategy", bad)


# --- DomainSupplyConnection: add/remove, no id/name -------------------------


def test_domain_supply_connection_add_and_remove():
    power_intent, _ = _upf_basic_mapped()
    before = len(power_intent.domain_supply_connections)

    added = add_domain_supply_connection(
        power_intent, DomainSupplyConnection(domain="PD_TOP", supply_net="VDD_CORE", scope="", role="primary_power")
    )
    assert len(added.domain_supply_connections) == before + 1

    removed = remove_domain_supply_connections(added, domain="PD_TOP", supply_net="VDD_CORE", role="primary_power", scope="")
    # removes every exact (domain, supply_net, role, scope) match -- the
    # pre-existing roleless VDD_CORE connection (role=None) doesn't match
    # role="primary_power", so this only removes the one just added.
    remaining_matches = [
        c for c in removed.domain_supply_connections if c.domain == "PD_TOP" and c.supply_net == "VDD_CORE" and c.role == "primary_power"
    ]
    assert remaining_matches == []
    assert len(power_intent.domain_supply_connections) == before  # original untouched
