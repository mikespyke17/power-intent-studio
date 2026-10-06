"""Permanent regression tests for the hand-authored UPF/CPF fixtures in
tests/frontend/golden_simple10/power_intent/.

WHY THESE EXIST, separately from the round-trip suite. The round-trip
suite (test_power_intent_round_trip.py) proves the importer reads back
the app's OWN canonical output. That is necessary and not sufficient: it
can only ever exercise the one syntactic shape the generator produces.
These fixtures were hand-authored the way a designer or vendor actually
writes power intent -- different command ordering, equivalent-but-
different flag spellings, naming that does not follow this app's own
conventions -- and every one of them caught at least one real defect the
round trip could not see. Each test below names the defect its fixture
caught, so a regression is traceable to the thing it broke.

See golden_simple10/README.md for the fixture index and the
full findings report.

LIKE THE ROUND-TRIP SUITE, this file reaches the real backend, for the
same reason: the question is what the app does with a file it did not
write, which mock data cannot answer. It goes through `backend.api` via
`frontend/src/backend_integration/` only, never into backend internals.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from frontend.src.power_intent_view.import_palette import are_visually_distinct
from frontend.src.power_intent_view.power_domain_overlay import (
    DOMAIN_FIXED_TRANSPARENCY,
    resolve_supply_set_color_from_placements,
)
from frontend.src.power_intent_view.power_intent_import import import_power_intent
from frontend.src.shared.models.validation import Severity

REPO = Path(__file__).parent.parent.parent

# The WHOLE example -- RTL, .lib and every hand-authored fixture -- lives
# here under tests/, so this suite is self-contained and always runs.
#
# It did not start that way, and the history is worth keeping: the design
# was originally a SIBLING of the repo
# (<repo>/../design_examples/simple10), which meant a checkout of this
# repo alone did not carry it and these tests SKIPPED rather than ran --
# silently running 62 fewer tests on CI or a fresh clone. Moved in on
# explicit request, which removes the skip entirely. See
# golden_simple10/README.md for the full note.
GOLDEN = Path(__file__).parent / "golden_simple10"
FIXTURES = GOLDEN / "power_intent"
DESIGN = GOLDEN / "design"

TOP = "sram"
MACRO = "sram/u_sram"
SUB = "sram/u_sub"


# --- the real pipeline ------------------------------------------------------


@pytest.fixture(scope="module")
def design():
    """(backend Design, adapted Design). Module-scoped: elaboration is the
    slow part and nothing here mutates it."""
    from backend.api import elaborate_rtl
    from frontend.src.backend_integration.backend_adapter import adapt_design

    backend_design = elaborate_rtl(
        [str(DESIGN / "sram.v"), str(DESIGN / "sub.sv")],
        [str(DESIGN / "sram.lib")],
        top=TOP,
    )
    return backend_design, adapt_design(backend_design)


def load(design, name: str, source_format: str = "upf"):
    from backend.api import elaborate_power_intent, run_mapping
    from frontend.src.backend_integration.backend_adapter import adapt_mapped_design, adapt_power_intent

    backend_design, rtl = design
    path = (FIXTURES / name).resolve()
    power_intent = elaborate_power_intent(str(path), source_format)
    adapted = adapt_power_intent(power_intent)
    mapped = adapt_mapped_design(run_mapping(power_intent, backend_design), adapted, rtl)
    return import_power_intent(adapted, rtl, mapped, str(path))


def sets_by_name(imported) -> dict:
    return {s.name: s for s in imported.domain.supply_sets.values()}


def domains_by_name(imported) -> dict:
    return {d.name: d for d in imported.domain.domains.values()}


def supplies_by_name(imported) -> dict:
    """Name -> the TOP-MOST placement of that name. A name can now occur
    on several hierarchies: a nested supply set that uses a top-level
    port gets a same-named copy on its own hierarchy, as in the app."""
    by_name: dict = {}
    for p in sorted(imported.supply.supply_port_placements.values(), key=lambda p: p.owner_path.count("/"), reverse=True):
        by_name[p.name] = p
    return by_name


def colour_of_set(imported, set_name: str):
    """A supply set's own derived colour, via the SAME match rule the
    diagram uses -- name equal AND owner path in the set's owner_paths."""
    return resolve_supply_set_color_from_placements(
        sets_by_name(imported)[set_name], imported.supply.supply_port_placements.values()
    )


def assert_domain_colour_derives(imported, domain_name: str, set_name: str) -> None:
    """The colour-derivation chain, checked as a chain: the domain's
    colour must equal its primary supply set's own power rail's colour,
    at the fixed transparency -- not merely be some colour that happens
    to differ from its neighbours'."""
    domain = domains_by_name(imported)[domain_name]
    expected = colour_of_set(imported, set_name)
    assert expected is not None, f"{set_name}'s power rail resolved to no placed supply"
    assert domain.color.name() == expected.name()
    assert domain.transparency == DOMAIN_FIXED_TRANSPARENCY
    assert sets_by_name(imported)[set_name].id == domain.primary_supply_set


# --- 01: designer style, domains first, set_domain_supply_net --------------


def test_01_authored_supply_sets_are_not_duplicated_by_synthesis(design):
    """CAUGHT: a duplicate set was synthesised per domain (PD_TOP_SS
    beside the authored ss_aon) and the domain pointed at the invented
    one. The CPF-motivated synthesis fired on DomainSupplyConnections
    alone, which the very common `set_domain_supply_net` idiom also
    produces in UPF."""
    imported = load(design, "01_designer_domains_first.upf")

    assert set(sets_by_name(imported)) == {"ss_aon", "ss_inv"}
    assert sets_by_name(imported)["ss_aon"].id == domains_by_name(imported)["PD_TOP"].primary_supply_set
    assert sets_by_name(imported)["ss_inv"].id == domains_by_name(imported)["PD_INV"].primary_supply_set


def test_01_domain_colours_derive_and_are_distinct(design):
    """CAUGHT: every imported domain came out the same default green. A
    domain's colour derives from its primary set's power rail, but the
    live recompute only runs from the create/edit paths -- never after a
    bulk restore -- so the import's placeholder was never corrected.
    Also CAUGHT: transparency was the 0.5 default, not the rule's 0.8."""
    imported = load(design, "01_designer_domains_first.upf")

    assert_domain_colour_derives(imported, "PD_TOP", "ss_aon")
    assert_domain_colour_derives(imported, "PD_INV", "ss_inv")
    assert are_visually_distinct(colour_of_set(imported, "ss_aon"), colour_of_set(imported, "ss_inv"))


def test_01_the_function_spelling_of_create_supply_set_is_read(design):
    """`-function {power VDD}` is the 1801 LRM's spelling; the app's own
    generator writes `-supply`. Both must work."""
    imported = load(design, "01_designer_domains_first.upf")

    assert (sets_by_name(imported)["ss_aon"].power, sets_by_name(imported)["ss_aon"].ground) == ("VDD", "VSS")


def test_01_a_net_with_no_boundary_port_becomes_an_internal_supply(design):
    imported = load(design, "01_designer_domains_first.upf")

    assert supplies_by_name(imported)["VDD_INV"].is_internal_supply is True
    assert supplies_by_name(imported)["VDD"].is_internal_supply is False


def test_01_authored_states_on_an_undriven_internal_net_are_reported(design):
    """Nothing silently dropped: VDD_INV has authored states and no
    Power Switch feeding it, so they genuinely cannot be re-derived."""
    imported = load(design, "01_designer_domains_first.upf")

    dropped = [d for d in imported.warnings if "VDD_INV" in d.message and "power state" in d.message]
    assert len(dropped) == 1
    assert "no switch drives this net" in dropped[0].message


# --- 02: vendor style, switch + isolation + retention + macro PG ----------


def test_02_macro_pg_connections_are_imported(design):
    """CAUGHT: `connect_supply_net -ports {u_sram/VDD}` was dropped
    entirely, so macro PG connectivity did not import at all. A macro PG
    placement is derived from the .lib when the scene is built, so it is
    not in the by-name supply dict -- but its id is deterministic."""
    imported = load(design, "02_vendor_dense_switch_iso_ret.upf")
    supplies = supplies_by_name(imported)

    pairs = set(imported.supply.supply_connector_placements)
    assert (supplies["VDDM_GATED"].id, f"pg:{MACRO}:VDD") in pairs
    assert (supplies["GND"].id, f"pg:{MACRO}:VSS") in pairs
    # The macro's INTERNAL PG pin too, not just its primary rails.
    assert (supplies["VDDC"].id, f"pg:{MACRO}:VDD_INT") in pairs


def test_02_the_switch_control_port_keeps_both_the_pin_and_the_net(design):
    """CAUGHT: `-control_port {sleep_n sleep_b}` imported the NET into a
    GUI field that means the PIN, silently relabelling it.

    Both halves now land in their own field. The "the net is reported as
    not carried" half of this test is RETIRED: the net had nowhere to go
    when it was written, and the "Switch Control" field is exactly the
    place the review said was missing."""
    imported = load(design, "02_vendor_dense_switch_iso_ret.upf")
    switch = next(iter(imported.power_switch.switches.values()))

    assert switch.control_port == "sleep_n"     # the cell's pin
    assert switch.control_signal == "sleep_b"   # the net driving it
    assert switch.input_supply_port == "vin"
    assert switch.output_supply_port == "vout"
    assert switch.cell_type == "HDRX4"  # from the separate map_power_switch
    # ...and nothing is reported as dropped any more, because nothing is.
    assert not any("control_ports" in d.message for d in imported.warnings)


def test_02_switch_on_off_read_the_expression_not_the_state_name(design):
    """The expression, not the state name -- and expressed in terms of
    the REAL SIGNAL, not the port.

    This fixture writes `-control_port {sleep_n sleep_b}` (port
    `sleep_n`, net `sleep_b`) and `-on_state {on_hi vin {sleep_n}}`,
    whose expression names the PORT. The GUI's ON State field holds what
    the user authors, which is the signal -- so import substitutes the
    port name back to it, and `sleep_b` is what belongs in the field.

    This changed when the fixed `CTRL` token was retired: the reverse
    substitution now targets the switch's own declared port name, which
    this file actually uses, where `CTRL` never appeared in it."""
    imported = load(design, "02_vendor_dense_switch_iso_ret.upf")
    switch = next(iter(imported.power_switch.switches.values()))

    assert (switch.control_port, switch.control_signal) == ("sleep_n", "sleep_b")
    assert (switch.on_state, switch.off_state) == ("sleep_b", "!sleep_b")


def test_02_isolation_and_retention_colours_derive_from_their_own_sets(design):
    """The colour chain for strategies, checked separately from whether
    the strategies exist: an Isolation follows its Isolation Supply Set,
    a Retention its Retention Supply Set's power rail."""
    imported = load(design, "02_vendor_dense_switch_iso_ret.upf")
    sets = sets_by_name(imported)

    iso = next(iter(imported.isolation.strategies.values()))
    assert iso.isolation_supply_set_id == sets["SS_CORE"].id
    assert colour_of_set(imported, "SS_CORE").name() == supplies_by_name(imported)["VDDC"].color.name()

    ret = next(iter(imported.retention.strategies.values()))
    assert ret.retention_supply_set_id == sets["SS_MEM"].id
    assert colour_of_set(imported, "SS_MEM").name() == supplies_by_name(imported)["VDDM"].color.name()


def test_02_clamp_value_is_translated_into_this_apps_vocabulary(design):
    """UPF writes 0/1/hold; this app's own vocabulary is low/high/keep."""
    imported = load(design, "02_vendor_dense_switch_iso_ret.upf")

    assert next(iter(imported.isolation.strategies.values())).clamp_value == "low"


def test_02_a_secondary_supply_set_is_imported(design):
    imported = load(design, "02_vendor_dense_switch_iso_ret.upf")

    domain = domains_by_name(imported)["PD_CORE"]
    assert [sets_by_name(imported)[n].id for n in ("SS_MEM",)] == domain.secondary_supply_sets


def test_02_every_supply_set_colour_is_distinct(design):
    imported = load(design, "02_vendor_dense_switch_iso_ret.upf")
    names = sorted(sets_by_name(imported))
    colours = {n: colour_of_set(imported, n) for n in names}

    for i, first in enumerate(names):
        for second in names[i + 1:]:
            assert are_visually_distinct(colours[first], colours[second]), f"{first} vs {second}"


def test_02_the_live_diagram_corrects_the_ground_edge_and_colours_pg_pins(qtbot, design):
    """The chain does not stop at the data model. Through the real tab:
    a Ground-role supply snaps to the bottom edge at scene-build time,
    and a macro PG pin takes the colour of whatever the imported
    connector wired it to."""
    from frontend.src.power_intent_view.power_intent_tab import PowerIntentTab

    _, rtl = design
    imported = load(design, "02_vendor_dense_switch_iso_ret.upf")
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(rtl)
    tab.apply_imported_power_intent(rtl, imported)

    live = {p.name: p for p in tab.diagram.shared_supply_state.supply_port_placements.values()}
    assert live["GND"].edge == "bottom"
    assert live["GND"].ground_auto_anchored is True
    assert live["VDDC"].edge == "top"

    pg = tab.diagram.shared_supply_state.pg_pin_placements
    assert pg[f"pg:{MACRO}:VDD"].color.name() == live["VDDM_GATED"].color.name()
    assert pg[f"pg:{MACRO}:VDD_INT"].color.name() == live["VDDC"].color.name()
    assert pg[f"pg:{MACRO}:VSS"].edge == "bottom"


# --- 03: hierarchical load_upf -- flatten vs. attach ----------------------


def test_03_a_soft_hierarchy_block_file_is_flattened_in(design):
    """The section-5 distinction, half one: `load_upf -instance u_sub`
    names a soft RTL block, so its constructs belong to this design and
    arrive carrying that scope."""
    imported = load(design, "03_hier/sram_top.upf")

    assert supplies_by_name(imported)["VDD_INV_LOCAL"].owner_path == SUB
    assert sets_by_name(imported)["SS_INV"].owner_paths == [SUB]
    assert next(iter(imported.retention.strategies.values())).owner_path == SUB


def test_03_a_macro_block_file_becomes_an_attachment_not_flattened(design):
    """Half two: the SAME syntax naming a black-box macro is vendor data
    about the macro's inside, and must not enter the design's diagram."""
    imported = load(design, "03_hier/sram_top.upf")

    assert list(imported.macro_power_intent.attachments) == [MACRO]
    assert imported.macro_power_intent.attachments[MACRO].endswith("sram_macro.upf")
    # The macro file declares PD_ARRAY / VDD_ARRAY / SS_ARRAY of its own.
    assert set(domains_by_name(imported)) == {"PD_ALL"}
    assert "VDD_ARRAY" not in supplies_by_name(imported)
    assert "SS_ARRAY" not in sets_by_name(imported)


def test_03_a_multi_hierarchy_domain_spans_every_element(design):
    imported = load(design, "03_hier/sram_top.upf")

    assert sorted(domains_by_name(imported)["PD_ALL"].owner_paths) == [MACRO, SUB]


def test_03_port_attributes_on_a_multi_bit_port_are_keyed_per_bit(design):
    """CAUGHT: a whole-port assertion was stored under the BARE port
    name, a key the editor never reads for a multi-bit port -- so the
    assignment existed in the state and was invisible in the GUI."""
    imported = load(design, "03_hier/sram_top.upf")
    assignments = imported.port_attribute.assignments
    set_id = sets_by_name(imported)["SS_IO"].id

    assert "d" not in assignments  # the bare name is NOT the key
    assert [assignments.get(f"d[{i}]") for i in range(32)] == [set_id] * 32
    assert [assignments.get(f"a[{i}]") for i in range(8)] == [set_id] * 8
    assert [assignments.get(f"q[{i}]") for i in range(32)] == [set_id] * 32


def test_03_a_level_shifter_carries_its_cell_and_applies_to(design):
    imported = load(design, "03_hier/sram_top.upf")
    ls = next(iter(imported.level_shifter.strategies.values()))

    assert (ls.name, ls.applies_to, ls.rule, ls.location) == ("LS_OUT", "outputs", "high_to_low", "self")
    assert (ls.threshold, ls.cell_type) == ("0.05", "LSHL_X2")


def test_03_the_whole_hierarchical_load_is_clean(design):
    """Clean except for ONE diagnostic, which is the point of it.

    This fixture writes `add_power_state VDD_IO -state {IO_ON 1.8}`. The
    importer reads an unrecognized level-bearing state type as FULL_ON at
    that level, which is a reasonable best effort -- but it used to do it
    SILENTLY, and the aura_top audit found the same thing happening to a
    real file's `ON` state (finding B9). It now says so, and this fixture
    is an existing, independent example of the case."""
    imported = load(design, "03_hier/sram_top.upf")

    messages = [d.message for d in imported.diagnostics]
    assert len(messages) == 1
    assert "IO_ON" in messages[0] and "FULL_ON" in messages[0]

    # Everything else about the load is still clean, and the state itself
    # still arrives -- the diagnostic reports the substitution, it does
    # not reject the file.
    assert imported.errors == []
    vdd_io = next(p for p in imported.supply.supply_port_placements.values() if p.name == "VDD_IO")
    assert [(s.state_type, s.voltage) for s in vdd_io.states] == [("FULL_ON", "1.8")]


# --- 05: legacy PST + a shared supply set --------------------------------


def test_05_a_legacy_pst_becomes_power_state_table_rows(design):
    """CAUGHT: UPF's whole system-mode table reached nothing. A PST IS
    this app's Power State Table -- the exporter already writes one --
    but the import never populated it, leaving only a provenance warning
    that said nothing about the loss."""
    imported = load(design, "05_legacy_pst_shared_set.upf")
    supplies = supplies_by_name(imported)
    rows = {r.state_name: r for r in imported.power_state_table.rows.values()}

    assert set(rows) == {"MODE_RUN", "MODE_SUBOFF", "MODE_ALLOFF"}
    assert rows["MODE_SUBOFF"].net_states == {
        supplies["VDD_TOP"].id: "TOP_ON",
        supplies["VDD_SUB"].id: "SUB_OFF",
        supplies["VSSX"].id: "GND_ON",
    }
    # A PST has no domain concept of its own. A row the app writes is
    # scoped to the design-root domain (its whole-design rows live
    # there), and a row with no domain is an error under Check Power
    # Intent -- so a legacy PST row maps to the domain owning the root.
    root_domain = next(d.id for d in imported.domain.domains.values() if d.name == "PD_MAIN")
    assert all(r.power_domain_id == root_domain for r in rows.values())


def test_05_the_pst_flag_spelling_is_accepted(design):
    """CAUGHT: `-pst` (the 1801 LRM's own spelling) was REGISTERED as a
    recognized option but never read by the handler, so it parsed and
    then failed with "no create_pst named 'None' found"."""
    imported = load(design, "05_legacy_pst_shared_set.upf")

    assert len(imported.power_state_table.rows) == 3


def test_05_one_supply_set_shared_as_primary_and_secondary_is_one_object(design):
    """SSET_TOP is PD_MAIN's primary and PD_SUBBLK's secondary. Sharing
    is by id in this app's model, so it must import as ONE placement
    referenced twice, not duplicated per referencing domain."""
    imported = load(design, "05_legacy_pst_shared_set.upf")
    shared = sets_by_name(imported)["SSET_TOP"]
    domains = domains_by_name(imported)

    assert domains["PD_MAIN"].primary_supply_set == shared.id
    assert domains["PD_SUBBLK"].secondary_supply_sets == [shared.id]
    assert len([s for s in imported.domain.supply_sets.values() if s.name == "SSET_TOP"]) == 1


def test_05_a_switch_supply_in_connector_is_rebuilt(design):
    imported = load(design, "05_legacy_pst_shared_set.upf")
    supplies = supplies_by_name(imported)
    switch = next(iter(imported.power_switch.switches.values()))

    assert switch.target_ids == [supplies["VDD_SUB"].id]
    sources = [target for key, target in imported.supply.supply_connector_placements if key.startswith("switch:")]
    assert sources == [supplies["VDD_TOP"].id]


# --- 06: well rails + successive refinement -------------------------------


def test_06_nwell_and_pwell_function_handles_are_imported(design):
    """CAUGHT: `-function {nwell VNW}` did not merely lose the data -- the
    elaborator RAISED on the function kind, so an otherwise-valid
    bias-aware file was rejected outright, for rails this app's own
    SupplySetPlacement already models."""
    imported = load(design, "06_wells_and_refinement.upf")
    bias = sets_by_name(imported)["SS_BIAS"]

    assert (bias.power, bias.ground, bias.n_well, bias.p_well) == ("VDDA", "VSSA", "VNW", "VPW")


def test_06_an_unsupported_function_kind_still_fails_loudly(design):
    """The fix widened what is accepted; it did not stop rejecting what
    genuinely has nowhere to go."""
    from backend.api import elaborate_power_intent

    bad = FIXTURES / "06_unsupported_function_kind.upf"
    with pytest.raises(Exception) as excinfo:
        elaborate_power_intent(str(bad.resolve()), "upf")
    assert "bulk" in str(excinfo.value)
    assert "power, ground, nwell, pwell" in str(excinfo.value)


def test_06_update_refines_an_existing_strategy_instead_of_being_rejected(design):
    """CAUGHT: `-update` is real 1801 -- re-open a named strategy and
    refine it -- but was unrecognized, so a very ordinary two-step
    authoring style failed outright. The merge must keep the FIRST
    command's fields and add the second's."""
    imported = load(design, "06_wells_and_refinement.upf")
    strategies = list(imported.isolation.strategies.values())

    assert len(strategies) == 1  # refined, not duplicated
    iso = strategies[0]
    assert iso.elements == ["sram/power_good"]  # from the first command
    assert iso.clamp_value == "high"  # from the first command
    assert iso.isolation_control == "iso_en"  # from the -update
    assert iso.location == "self"  # from the -update
    assert iso.isolation_supply_set_id == sets_by_name(imported)["SS_BIAS"].id


def test_06_a_refined_file_is_clean(design):
    imported = load(design, "06_wells_and_refinement.upf")

    assert [d.message for d in imported.diagnostics] == []


# --- 07: deliberately broken references ----------------------------------


def test_07_a_domain_whose_elements_all_dangle_is_not_imported(design):
    imported = load(design, "07_dangling_refs.upf")

    assert "PD_GHOST" not in domains_by_name(imported)
    messages = [d.message for d in imported.errors]
    assert any("u_cache" in m and "does not exist" in m for m in messages)
    assert any("none of its elements resolved" in m for m in messages)


def test_07_a_partially_resolving_domain_keeps_what_resolved(design):
    """Partial resolution, not all-or-nothing: the bad element is
    reported and the usable one is still imported."""
    imported = load(design, "07_dangling_refs.upf")

    assert domains_by_name(imported)["PD_PARTIAL"].owner_paths == [SUB]
    assert any("u_nowhere" in d.message for d in imported.errors)


def test_07_a_strategy_naming_a_missing_supply_set_is_reported(design):
    """CAUGHT: this imported as a silent None -- the strategy appeared
    correctly in every list and its badge rendered default grey with
    nothing saying why. The importer is the only layer that can report
    it: the backend mapper classifies these fields as NOT_ATTEMPTED,
    which validation then excludes from error counts by design."""
    imported = load(design, "07_dangling_refs.upf")
    iso = next(iter(imported.isolation.strategies.values()))

    assert iso.isolation_supply_set_id is None
    reported = [d for d in imported.errors if "SS_MISSING" in d.message]
    assert len(reported) == 1
    assert "not a declared supply set" in reported[0].message


def test_07_a_strategy_whose_domain_is_missing_is_skipped_with_an_error(design):
    imported = load(design, "07_dangling_refs.upf")

    assert imported.retention.strategies == {}
    assert any("PD_NOT_THERE" in d.message for d in imported.errors)


def test_07_a_switch_with_no_declared_output_supply_is_skipped(design):
    imported = load(design, "07_dangling_refs.upf")

    assert imported.power_switch.switches == {}
    assert any("VDD_NOT_DECLARED" in d.message for d in imported.errors)


def test_07_every_finding_is_an_error_not_a_crash(design):
    """The whole point of this fixture: a file full of broken references
    loads, reports, and does not raise."""
    imported = load(design, "07_dangling_refs.upf")

    assert len(imported.errors) >= 5
    assert all(d.severity in (Severity.ERROR, Severity.WARNING) for d in imported.diagnostics)


# --- a property that must hold for EVERY fixture -------------------------


@pytest.mark.parametrize(
    "name,source_format",
    [
        ("01_designer_domains_first.upf", "upf"),
        ("02_vendor_dense_switch_iso_ret.upf", "upf"),
        ("03_hier/sram_top.upf", "upf"),
        ("05_legacy_pst_shared_set.upf", "upf"),
        ("06_wells_and_refinement.upf", "upf"),
        ("07_dangling_refs.upf", "upf"),
    ],
)
def test_every_domains_colour_is_derived_never_arbitrary(design, name, source_format):
    """The colour-derivation constraint, as a property over all fixtures
    rather than one assertion per fixture: a domain's colour must equal
    its primary supply set's own power rail's, and any two domains whose
    sets differ must be visually distinguishable. A domain with no
    resolvable primary set is allowed the neutral grey -- that is the
    documented fallback, not an arbitrary pick."""
    imported = load(design, name, source_format)
    domains = list(imported.domain.domains.values())

    resolved = []
    for domain in domains:
        supply_set = imported.domain.supply_sets.get(domain.primary_supply_set)
        colour = (
            resolve_supply_set_color_from_placements(
                supply_set, imported.supply.supply_port_placements.values()
            )
            if supply_set is not None else None
        )
        if colour is None:
            assert domain.color.name() == "#9e9e9e", f"{domain.name}: no source, so expected the grey fallback"
            continue
        assert domain.color.name() == colour.name(), f"{domain.name}: colour is not derived from {supply_set.name}"
        assert domain.transparency == DOMAIN_FIXED_TRANSPARENCY
        resolved.append((domain.name, supply_set.name, colour))

    for i, (first_domain, first_set, first_colour) in enumerate(resolved):
        for second_domain, second_set, second_colour in resolved[i + 1:]:
            if first_set == second_set:
                continue  # two domains sharing one set legitimately share its colour
            assert are_visually_distinct(first_colour, second_colour), (
                f"{first_domain} and {second_domain} are not distinguishable"
            )


# --- export AFTER import, the path neither other suite covers -------------


def _regenerate(imported, rtl, source_format: str) -> str:
    from frontend.src.power_intent_view.power_intent_text_export import generate_cpf, generate_upf

    common = dict(
        design_name=TOP,
        isolation_state=imported.isolation,
        port_attribute_state=imported.port_attribute,
        top_level_ports=rtl.definitions[rtl.top].ports,
        power_state_table_state=imported.power_state_table,
        macro_power_intent_state=imported.macro_power_intent,
    )
    writer = generate_cpf if source_format == "cpf" else generate_upf
    version = "2.0" if source_format == "cpf" else "3.1"
    return writer(
        imported.supply, imported.domain, imported.retention, version,
        imported.power_switch, imported.level_shifter, **common,
    )


@pytest.mark.parametrize(
    "name,source_format",
    [
        ("01_designer_domains_first.upf", "upf"),
        ("02_vendor_dense_switch_iso_ret.upf", "upf"),
        ("05_legacy_pst_shared_set.upf", "upf"),
        ("06_wells_and_refinement.upf", "upf"),
        ("07_dangling_refs.upf", "upf"),
    ],
)
def test_re_exporting_an_imported_fixture_never_writes_a_literal_none(design, name, source_format):
    """CAUGHT (finding 20): three flags in the UPF isolation writer and
    two in the Level Shifter one were emitted unconditionally, so an
    unset value became the literal text "None" -- which PARSES CLEANLY
    and comes back as the string 'None'. Silent corruption, not a
    visible failure.

    Invisible to both other suites: a strategy created through the dialog
    always has those fields set (so the round trip never saw it), and the
    import stops before generation (so validate_fixture never did).

    `03_hier` is excluded only because its generated `load_upf` line
    points at an absolute path outside any tmp dir; its own attachment
    round trip is covered by test_03_* above."""
    _, rtl = design
    imported = load(design, name, source_format)
    text = _regenerate(imported, rtl, source_format)

    offenders = [
        line for line in text.splitlines()
        if not line.lstrip().startswith("#") and "None" in line
    ]
    assert offenders == []


@pytest.mark.parametrize(
    "name,source_format",
    [
        ("01_designer_domains_first.upf", "upf"),
        ("02_vendor_dense_switch_iso_ret.upf", "upf"),
        ("05_legacy_pst_shared_set.upf", "upf"),
        ("06_wells_and_refinement.upf", "upf"),
        ("07_dangling_refs.upf", "upf"),
    ],
)
def test_an_imported_fixture_re_exports_to_something_that_elaborates(design, name, source_format, tmp_path):
    """The stronger form of the same guarantee: what the app writes from
    an imported state must be readable, not merely free of the one token
    that broke it."""
    from backend.api import elaborate_power_intent

    _, rtl = design
    imported = load(design, name, source_format)
    out = tmp_path / f"regenerated.{source_format}"
    out.write_text(_regenerate(imported, rtl, source_format))

    reloaded = elaborate_power_intent(str(out), source_format)
    assert reloaded.domains or reloaded.supply_nets  # it really did carry content


# --- 08: retention with the -elements flag omitted ------------------------


def test_08_an_omitted_elements_flag_expands_to_the_domains_own_flops(design):
    """Finding 16, now demonstrable ON THIS DESIGN. UPF's omitted
    `-elements` means every retainable element in the domain; importing
    it as an empty list was faithful to the file but wrong for this app,
    whose retention overlay derives which blocks get a badge FROM the
    elements -- so the strategy existed and rendered nothing.

    This could not be shown on simple10 at first: the design had no
    sequential element anywhere, so a retention strategy there
    legitimately had nothing to retain. It gained a real flop
    (u_sub/o_q, clocked on clk, reset by rstn) and the case is now
    exercised directly rather than against a second design."""
    imported = load(design, "08_retention_all_elements.upf")
    ret = next(iter(imported.retention.strategies.values()))

    assert ret.name == "RET_EVERYTHING"
    assert ret.elements == [f"{SUB}/o_q"]
    assert ret.retention_supply_set_id == sets_by_name(imported)["SS_KEEP"].id
    assert (ret.save_logic, ret.restore_logic) == ("clk posedge", "clk negedge")
    assert [d.message for d in imported.diagnostics] == []


def test_08_the_expanded_strategy_actually_renders_a_badge(qtbot, design):
    """The half that was broken: the data model was fine either way, the
    BADGE was what went missing."""
    from collections import Counter

    from frontend.src.power_intent_view.power_intent_tab import PowerIntentTab

    _, rtl = design
    imported = load(design, "08_retention_all_elements.upf")
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(rtl)
    tab.apply_imported_power_intent(rtl, imported)

    counts = Counter(type(i).__name__ for i in tab.diagram._view.scene().items())
    assert counts["RetentionBadgeIconItem"] == 1


def test_08_the_designs_flop_is_visible_to_elaboration_at_all(design):
    """Guards the premise the two tests above rest on -- and the RTL fix
    that made it true. sub.sv declared `o_q` as `output wire` while
    assigning it procedurally inside `always_ff`, AND drove it a second
    time with a leftover continuous assign, so the whole design failed
    to elaborate ("cannot assign to a net within a procedural context").
    If that regresses, these tests should say why rather than just
    reporting a missing badge."""
    _, rtl = design
    by_path = {n.path: n for n in rtl.walk()}

    flops = by_path[SUB].definition.sequential_elements
    assert [s.output_signal for s in flops] == ["o_q"]
    assert flops[0].is_latch is False
    assert (flops[0].clock_ref, flops[0].reset_ref) == ("clk", "rstn")


def test_the_reporting_harness_points_at_the_same_design_as_these_tests():
    """Guards a mistake this exercise actually made. `validate_fixture.py`
    (the exploratory reporting tool) and this file each name the design
    tree independently. When only one was re-pointed at the real
    simple10, the harness silently kept elaborating an older snapshot
    WITHOUT u_sub's flop -- so its regenerated output was missing a
    retention `-elements` list and briefly looked like a generator bug
    that did not exist. Both now resolve the same in-tests location, but
    they still do so separately, so the guard is still worth having.

    Cheap to assert, and the failure mode is a wrong conclusion rather
    than a broken test, which is the kind worth pinning."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "validate_fixture", GOLDEN / "validate_fixture.py"
    )
    harness = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(harness)

    assert harness.DESIGN_DIR.resolve() == DESIGN.resolve()
    assert harness.TOP == TOP


# --- 09: Supply Port Direction -------------------------------------------


def test_09_each_direction_value_imports_including_the_absent_one(design):
    """Section 6 of the Direction spec. The no-flag case carries the
    real risk: an imported port that declared no `-direction` must come
    back as "" -- NOT as the "in" a newly-created port defaults to in
    this app. The two states mean different things and only one of them
    generates a flag."""
    imported = load(design, "09_supply_port_directions.upf")
    by_name = {p.name: p for p in imported.supply.supply_port_placements.values()}

    assert by_name["VDD_I"].direction == "in"
    assert by_name["VDD_O"].direction == "out"
    assert by_name["VDD_B"].direction == "inout"
    assert by_name["VSS_N"].direction == ""
    assert [d.message for d in imported.diagnostics] == []


def test_09_direction_round_trips_through_generation(design, tmp_path):
    """Section 6's round-trip requirement, all four values: generate,
    reload, expect the same four directions back and nothing reported."""
    from backend.api import elaborate_power_intent, run_mapping
    from frontend.src.backend_integration.backend_adapter import adapt_mapped_design, adapt_power_intent

    backend_design, rtl = design
    imported = load(design, "09_supply_port_directions.upf")
    out = tmp_path / "regenerated.upf"
    out.write_text(_regenerate(imported, rtl, "upf"))

    power_intent = elaborate_power_intent(str(out), "upf")
    adapted = adapt_power_intent(power_intent)
    mapped = adapt_mapped_design(run_mapping(power_intent, backend_design), adapted, rtl)
    again = import_power_intent(adapted, rtl, mapped, str(out))

    before = {p.name: p.direction for p in imported.supply.supply_port_placements.values()}
    after = {p.name: p.direction for p in again.supply.supply_port_placements.values()}
    assert after == before == {"VDD_I": "in", "VDD_O": "out", "VDD_B": "inout", "VSS_N": ""}
    assert [d.message for d in again.errors] == []


def test_09_the_generated_text_omits_the_flag_only_for_the_empty_one(design, tmp_path):
    """Asserted on the TEXT, not just the reloaded state: a bug that
    wrote `-direction ` or `-direction None` could still round-trip
    through this app while producing invalid UPF -- the exact shape of
    the literal-"None" bug finding 20 already caught elsewhere."""
    _, rtl = design
    imported = load(design, "09_supply_port_directions.upf")
    text = _regenerate(imported, rtl, "upf")

    assert "create_supply_port VDD_I -direction in" in text
    assert "create_supply_port VDD_O -direction out" in text
    assert "create_supply_port VDD_B -direction inout" in text
    assert "create_supply_port VSS_N\n" in text
    assert "-direction None" not in text
    assert "-direction \n" not in text
