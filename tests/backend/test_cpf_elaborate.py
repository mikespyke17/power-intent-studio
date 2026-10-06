"""2b: CPF elaborator, exercising the real Tcl interpreter (tkinter.Tcl()) --
not a mock of Tcl or of CPF command parsing.

Reuses backend/elaborator/power_intent/shared/{tcl_runtime,builders}.py
as-is (no duplication, confirmed while building 2a). See
docs/milestone2_powerintent_elaborator.md for the full design writeup: why CPF
has no load_upf-equivalent (set_design/set_instance/end_design form a flat
sequence of scope blocks instead, verified against the primary spec's own
worked example before finalizing), and the real create_X/update_X by-name
merging this module's tests exercise via precedence.py.
"""

from pathlib import Path

import pytest

from backend.elaborator.power_intent.cpf import elaborate
from backend.elaborator.power_intent.ir import PowerStateShape, SourceFormat

GOLDEN = Path(__file__).parent / "golden_cpf"


# --- full strategy coverage, single file -----------------------------------


def test_basic_cpf_covers_every_construct_category():
    """One file exercising every construct category: domains (create +
    update by-name merge), supplies, switches, isolation (create + update
    by-name merge), retention, level shifters, power modes."""
    design = elaborate(str(GOLDEN / "cpf_basic" / "top.cpf"))

    assert design.source_format == SourceFormat.CPF
    assert design.source_version == "2.0"

    domain = design.domains["::PD_TOP"]
    assert domain.is_default is True
    assert domain.elements == ["."]
    assert domain.boundary_ports == ["clk", "rst_n"]
    assert len(design.domains) == 1

    # update_power_domain's -internal_power_net/-internal_ground_net are
    # unified onto DomainSupplyConnection (role="primary_power"/
    # "primary_ground") rather than PowerDomain fields -- same roles UPF's
    # set_domain_supply_net produces, confirmed to be the same concept
    # during the 2b audit. This also confirms the by-name merge actually
    # ran (update_power_domain found PD_TOP by name and recorded these),
    # not just that create_power_domain's own fields are present.
    connections = {(c.domain, c.supply_net, c.role) for c in design.domain_supply_connections}
    assert ("PD_TOP", "VDD", "primary_power") in connections
    assert ("PD_TOP", "VSS", "primary_ground") in connections

    vdd = design.supply_nets["::VDD"]
    assert vdd.voltage == "1.2"
    vss = design.supply_nets["::VSS"]
    assert vss.voltage is None

    switch = design.switches["::sw1"]
    assert switch.domain == "PD_TOP"
    assert switch.input_supply == "VDD_EXT"

    iso = design.isolation_strategies["::ir1"]
    assert iso.source == "PD_TOP"
    assert iso.condition == "en"
    assert iso.clamp_value == "high"
    # -location only appears after update_isolation_rules -- same by-name
    # merge confirmation as the domain above.
    assert iso.location == "from"

    ls = design.level_shifter_strategies["::lsr1"]
    assert ls.sink == "PD_TOP"

    ret = design.retention_strategies["::sr1"]
    assert ret.domain == "PD_TOP"
    assert ret.restore_signal == "rst_n"

    mode = design.power_states["::PM1"]
    assert mode.expr == "PD_TOP@high"
    assert mode.is_default is True
    assert mode.shape == PowerStateShape.CPF_POWER_MODE
    assert mode.applies_to is None  # CPF power modes have no single applies_to referent, see schema docstring


# --- the marquee precedence.py case, isolated and unambiguous --------------


def test_create_then_update_power_domain_attributes_connections_by_name_not_duplicate():
    """create_power_domain followed by update_power_domain on the SAME
    name must resolve to the one domain create_power_domain produced (not
    a second, duplicate one), and update_power_domain's
    -internal_power_net/-internal_ground_net must be attributed to that
    exact domain by name in domain_supply_connections.

    Note this no longer demonstrates a *builder-field* merge the way it
    used to: since -internal_power_net/-internal_ground_net were unified
    onto DomainSupplyConnection (see schema.py's PowerDomain docstring),
    update_power_domain has no PowerDomain field left to mutate at all --
    every option it sets either routes to domain_supply_connections or is
    a deliberately-unstored implementation detail. `find_by_name` still
    validates the referenced domain exists (tested separately in
    test_update_on_nonexistent_name_raises_clearly). For the true
    same-builder-record merge case, see
    test_create_then_update_isolation_rule_merges_onto_the_same_record
    below, where update_isolation_rules's -location genuinely does mutate
    a field on the record create_isolation_rule produced.
    """
    design = elaborate(str(GOLDEN / "cpf_create_then_update_merge" / "top.cpf"))

    assert len(design.domains) == 1
    domain = design.domains["::PD_TOP"]
    assert domain.is_default is True
    assert domain.elements == ["."]

    connections = {(c.domain, c.supply_net, c.role) for c in design.domain_supply_connections}
    assert ("PD_TOP", "VDD", "primary_power") in connections
    assert ("PD_TOP", "VSS", "primary_ground") in connections


def test_create_then_update_isolation_rule_merges_onto_the_same_record():
    """The true builder-field-mutation case of the marquee precedence.py
    scenario, now that update_power_domain (above) no longer demonstrates
    it: update_isolation_rules's -location genuinely mutates a field on
    the exact same IsolationStrategy builder create_isolation_rule
    produced -- not a second record, not a silent no-op."""
    design = elaborate(str(GOLDEN / "cpf_create_then_update_isolation_merge" / "top.cpf"))

    assert len(design.isolation_strategies) == 1
    iso = design.isolation_strategies["::ir1"]
    # Fields only create_isolation_rule can set:
    assert iso.source == "PD1"
    assert iso.condition == "en"
    # Field only the subsequent update_isolation_rules can set -- if the
    # merge had failed silently, this would still be None here:
    assert iso.location == "from"


# --- hierarchical scope: set_design/set_instance/end_design ----------------


def test_set_instance_replaces_scope_not_nests_it():
    """The hypothesis flagged for verification before finalizing: CPF's
    set_instance replaces the current scope (relative to top), it does not
    push a nested scope the way UPF's load_upf -scope does. Confirmed here
    against a hand-written two-block fixture, not assumed."""
    design = elaborate(str(GOLDEN / "cpf_hierarchical" / "top.cpf"))

    top = design.domains["::PD_TOP"]
    assert top.scope == ""

    pm = design.domains["pm_inst::PD_PM"]
    assert pm.scope == "pm_inst"


# --- version handling --------------------------------------------------


def test_missing_version_declaration_does_not_silently_default_to_1_0():
    """The CPF spec itself defines a default ('Default: 1.0') if
    set_cpf_version is never called -- but 1.0 is explicitly out of scope
    here, so this must still raise, not silently elaborate under 1.0."""
    with pytest.raises(Exception, match="version declaration"):
        elaborate(str(GOLDEN / "cpf_version_missing" / "top.cpf"))


def test_cpf_1_0_explicitly_declared_is_rejected():
    with pytest.raises(Exception, match="1.0"):
        elaborate(str(GOLDEN / "cpf_version_1_0_rejected" / "top.cpf"))


def test_conflicting_set_cpf_version_declarations_raise():
    with pytest.raises(Exception, match="conflicting set_cpf_version"):
        elaborate(str(GOLDEN / "cpf_version_conflict" / "top.cpf"))


# --- set_design/end_design structural checks --------------------------


def test_missing_end_design_raises():
    with pytest.raises(Exception, match="missing end_design"):
        elaborate(str(GOLDEN / "cpf_missing_end_design" / "top.cpf"))


def test_nested_set_design_without_end_design_raises():
    with pytest.raises(Exception, match="already open"):
        import tempfile
        import os

        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "top.cpf")
            with open(path, "w") as f:
                f.write("set_cpf_version 2.0\nset_design top\nset_design other\n")
            elaborate(path)


# --- unconsumed-flag rejection, from the start (not retrofitted) ----------


def test_unrecognized_option_rejected():
    with pytest.raises(Exception, match="unrecognized option"):
        elaborate(str(GOLDEN / "cpf_unrecognized_option_rejected" / "top.cpf"))


# --- create_X/update_X by-name merge (precedence.py) -----------------------


def test_update_on_nonexistent_name_raises_clearly():
    with pytest.raises(Exception, match="no power domain named 'NOPE' was created"):
        elaborate(str(GOLDEN / "cpf_update_nonexistent_rejected" / "top.cpf"))


# --- tracked gaps, confirmed to fail loud rather than silently mishandle --


def test_multi_domain_from_is_a_tracked_gap_not_silently_dropped():
    with pytest.raises(Exception, match="multi-domain"):
        elaborate(str(GOLDEN / "cpf_multi_domain_from_rejected" / "top.cpf"))


# --- feature-completeness audit follow-ups ---------------------------------


def test_multi_file_cpf_via_plain_tcl_source_resolves_relative_to_sourcing_file(monkeypatch, tmp_path):
    """A real bug found by this audit, not assumed away: CPF has no
    load_upf-equivalent wrapper verb, so multi-file CPF relies on raw Tcl
    `source`, which -- unpatched -- resolves relative paths against the
    process cwd and fails outright when that differs from the sourcing
    file's directory. Fixed generically in
    shared/tcl_runtime.py's install_relative_source. Run from a deliberately
    unrelated cwd here, same regression-proof style as the UPF load_upf fix.
    """
    monkeypatch.chdir(tmp_path)
    design = elaborate(str(GOLDEN / "cpf_multi_file" / "top.cpf"))
    assert "::PD_TOP" in design.domains
    assert "::PD_SUB" in design.domains


def test_degenerate_whole_design_backstop_fires_for_cpf_too():
    """Confirmed for UPF during 2a's own audit; never explicitly proven for
    CPF until now, even though it uses the same shared backstop machinery."""
    with pytest.raises(Exception, match="never declared a version"):
        elaborate(str(GOLDEN / "cpf_degenerate_backstop" / "top.cpf"))


def test_full_option_coverage_multi_name_nets_pins_exclude_and_instances_form():
    """Closes several real coverage gaps the audit found: create_power_nets
    with multiple names in one -nets list, isolation/level-shifter -pins
    and -exclude (elements/exclude_elements), the retention-by-instances
    form (not just -domain, exercising the Optional relaxation made while
    building 2b), and a power switch's -external_ground_net path (only
    -external_power_net had been exercised before).
    """
    design = elaborate(str(GOLDEN / "cpf_full_option_coverage" / "top.cpf"))

    assert set(design.supply_nets.keys()) == {"::VDD1", "::VDD2", "::VDD3"}
    for net in design.supply_nets.values():
        assert net.voltage == "1.0"

    switch = design.switches["::sw_gnd"]
    assert switch.input_supply == "VSS_EXT"

    iso = design.isolation_strategies["::ir_pins"]
    assert iso.elements == ["out1", "out2", "out3"]
    assert iso.exclude_elements == ["out3"]

    ls = design.level_shifter_strategies["::lsr_pins"]
    assert ls.elements == ["out4", "out5"]
    assert ls.exclude_elements == ["out5"]

    ret = design.retention_strategies["::sr_inst"]
    assert ret.domain is None
    assert ret.elements == ["u_a", "u_b"]
