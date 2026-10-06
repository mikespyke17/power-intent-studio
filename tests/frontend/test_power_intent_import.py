"""power_intent_view/power_intent_import.py -- the translation layer
from an elaborated UPF/CPF into this app's own diagram state, plus the
'Load Power Intent' wiring around it (the acknowledgment dialog and the
dirty-flag re-load gate).

The round-trip suite -- generate every construct, re-load the generated
file, expect zero findings and equivalent state -- lives separately in
test_power_intent_round_trip.py; this file covers the paths a round trip
never reaches, which is what the spec's own regression list (a)-(g) asks
for.

Every dialog here is patched, never executed: message_box.question()
blocks on a modal that no one can click under the offscreen platform.
"""

from __future__ import annotations

from PySide6.QtCore import QEventLoop
from PySide6.QtWidgets import QMessageBox

from frontend.src.power_intent_view import load_power_intent_tab as tab_module
from frontend.src.power_intent_view.load_power_intent_tab import LoadPowerIntentTab
from frontend.src.power_intent_view.power_intent_import import (
    DIAGNOSTIC_SOURCE,
    UNRECOGNIZED_CODE,
    UNRESOLVED_CODE,
    ImportedPowerIntent,
    import_power_intent,
    is_macro,
    macro_attachments,
    resolve_owner_path,
    rtl_top_path,
    scoped_to_macros,
)
from frontend.src.shared.mock_backend import build_mock_design, build_mock_power_intent
from frontend.src.shared.models.mapping import MappedDesign
from frontend.src.shared.models.power_intent import (
    PowerDomain,
    PowerIntentDesign,
    SourceFormat,
    SourceLoc,
    SupplyNet,
    SupplySet,
)
from frontend.src.shared.models.validation import Severity


def _design(**kwargs) -> PowerIntentDesign:
    return PowerIntentDesign(source_format=SourceFormat.UPF, source_version="3.1", **kwargs)


def _empty_mapped(power_intent, rtl) -> MappedDesign:
    return MappedDesign(power_intent=power_intent, rtl=rtl)


# --- scope resolution against the RTL (section 1) ---------------------------


def test_the_rtl_top_path_comes_from_the_top_definitions_base_name():
    """`Design.top` is a definition ID carrying a parameter hash, not an
    instance path -- confusing the two is a documented past bug in this
    project, so the resolution is asserted directly."""
    rtl = build_mock_design()
    assert rtl_top_path(rtl) == rtl.definitions[rtl.top].base_name
    assert "::" not in rtl_top_path(rtl)


def test_the_root_scope_resolves_to_the_top_path_and_an_element_hangs_off_it():
    rtl = build_mock_design()
    top = rtl_top_path(rtl)
    assert resolve_owner_path(rtl, "") == top
    assert resolve_owner_path(rtl, "", ".") == top
    assert resolve_owner_path(rtl, "", "u_core") == f"{top}/u_core"
    assert resolve_owner_path(rtl, "u_core") == f"{top}/u_core"


# --- (a) an element/reference that does not exist -> ERROR ------------------


def test_a_domain_element_that_does_not_exist_is_an_error_and_the_domain_is_skipped():
    rtl = build_mock_design()
    power_intent = _design(
        domains={"d": PowerDomain(id="d", name="PD_GHOST", scope="", elements=["u_nope"])}
    )
    result = import_power_intent(power_intent, rtl)

    assert result.domain.domains == {}
    assert [d.code for d in result.errors] == [UNRESOLVED_CODE, UNRESOLVED_CODE]
    assert "u_nope" in result.errors[0].message
    assert "was not imported" in result.errors[1].message


def test_a_domain_with_one_bad_and_one_good_element_imports_the_good_one():
    """Partial resolution, not all-or-nothing: the spec asks for the
    unresolved reference to be reported, not for the whole construct to
    be discarded when part of it is usable."""
    rtl = build_mock_design()
    power_intent = _design(
        domains={"d": PowerDomain(id="d", name="PD_MIXED", scope="", elements=["u_nope", "u_core"])}
    )
    result = import_power_intent(power_intent, rtl)

    placement = next(iter(result.domain.domains.values()))
    assert placement.owner_paths == [f"{rtl_top_path(rtl)}/u_core"]
    assert len(result.errors) == 1


def test_a_supply_net_in_a_scope_that_does_not_exist_is_an_error():
    rtl = build_mock_design()
    power_intent = _design(supply_nets={"n": SupplyNet(id="n", name="VDD", scope="u_ghost")})
    result = import_power_intent(power_intent, rtl)

    assert result.supply.supply_port_placements == {}
    assert len(result.errors) == 1
    assert result.errors[0].code == UNRESOLVED_CODE
    assert "u_ghost" in result.errors[0].message


def test_the_backend_dangling_sweep_does_not_repeat_what_a_builder_reported():
    """Both the builders and the backend's own DANGLING classification
    see the same missing element. Reporting one fact twice reads as two
    problems, so the sweep filters itself against what the builders have
    already said in their own, more specific terms."""
    rtl = build_mock_design()
    power_intent = build_mock_power_intent()
    from frontend.src.shared.mock_backend import build_mock_mapped_design

    mapped = build_mock_mapped_design(power_intent, rtl)
    result = import_power_intent(power_intent, rtl, mapped)

    element_errors = [d for d in result.errors if "u_nonexistent" in d.message]
    assert len(element_errors) == 1
    assert ("PD_EMPTY", "u_nonexistent") in result.reported_references


# --- (b) a rule/flag this tool does not model -> WARNING --------------------


def test_an_unmodelled_flag_is_a_warning_naming_the_construct_field_and_reason():
    rtl = build_mock_design()
    power_intent = build_mock_power_intent()
    result = import_power_intent(power_intent, rtl)

    group_warnings = [d for d in result.warnings if "PowerStateGroup" in d.message]
    assert group_warnings, "create_power_state_group is not modelled and must be reported"
    warning = group_warnings[0]
    assert warning.code == UNRECOGNIZED_CODE
    assert warning.severity is Severity.WARNING
    assert "'members'" in " ".join(d.message for d in group_warnings)
    assert "is not modelled by this tool and was not imported" in warning.message
    # The reason is part of the message, not just the field name -- the
    # spec asks for nothing to be dropped "without a trace".
    assert warning.message.count("--") >= 1


def test_a_field_the_file_never_set_is_not_reported_as_unrecognized():
    """Only what the file actually AUTHORED is worth a warning. An
    unmodelled field left at its default says nothing about this file."""
    rtl = build_mock_design()
    power_intent = _design(supply_nets={"n": SupplyNet(id="n", name="VDD", scope="")})
    result = import_power_intent(power_intent, rtl)

    assert result.warnings == []


def test_unrecognized_is_a_warning_and_unresolved_is_an_error_never_conflated():
    """The two classifications the spec asks to keep distinct: a missing
    reference is an ERROR, an unmodelled rule is a WARNING, and each has
    its own code."""
    rtl = build_mock_design()
    power_intent = build_mock_power_intent()
    result = import_power_intent(power_intent, rtl)

    assert {d.code for d in result.errors} == {UNRESOLVED_CODE}
    assert {d.code for d in result.warnings} == {UNRECOGNIZED_CODE}
    assert {d.source for d in result.diagnostics} == {DIAGNOSTIC_SOURCE}


# --- (c) hierarchical: macro attachment vs. soft-hierarchy flattening -------


def test_a_scope_naming_a_soft_rtl_hierarchy_is_flattened_into_the_design():
    """The elaborator has already merged a `load_upf -instance <soft>`
    sub-file inline under that scope, so there is nothing to attach --
    the constructs simply belong to a nested block of this design."""
    rtl = build_mock_design()
    power_intent = _design(
        supply_nets={"n": SupplyNet(id="n", name="VDD_SUB", scope="u_core")}
    )
    result = import_power_intent(power_intent, rtl, entry_file="top.upf")

    assert result.macro_power_intent.attachments == {}
    placement = next(iter(result.supply.supply_port_placements.values()))
    assert placement.owner_path == f"{rtl_top_path(rtl)}/u_core"


def test_a_scope_naming_a_macro_becomes_an_attachment_and_is_not_flattened():
    """The SAME `-instance` syntax means something different when the
    instance is a black box: its internal power intent is vendor data
    about a macro, not part of this design's own diagram."""
    rtl = build_mock_design()
    top = rtl_top_path(rtl)
    assert is_macro(rtl, f"{top}/u_ram"), "mock fixture must have a black-box macro to test with"

    power_intent = _design(
        supply_nets={
            "inside": SupplyNet(
                id="inside", name="VDD_RAM", scope="u_ram",
                source_loc=SourceLoc(file="/proj/ram_macro.upf", line=3),
            ),
            "outside": SupplyNet(id="outside", name="VDD", scope=""),
        }
    )
    result = import_power_intent(power_intent, rtl, entry_file="/proj/top.upf")

    # Attached, recorded against the file it came from -- not the entry file.
    assert result.macro_power_intent.attachments == {f"{top}/u_ram": "/proj/ram_macro.upf"}
    # And genuinely NOT flattened: only the design's own net was imported.
    assert [p.name for p in result.supply.supply_port_placements.values()] == ["VDD"]


def test_the_macro_vs_soft_decision_reads_the_elaborated_rtl_not_the_file():
    """Both scopes are spelled identically in the file; only the
    elaborated design says which is a black box."""
    rtl = build_mock_design()
    power_intent = _design(
        supply_nets={
            "a": SupplyNet(id="a", name="A", scope="u_ram"),
            "b": SupplyNet(id="b", name="B", scope="u_core"),
        }
    )
    assert scoped_to_macros(power_intent, rtl) == {"u_ram"}


def test_an_attachment_falls_back_to_the_entry_file_when_the_construct_has_no_source():
    rtl = build_mock_design()
    top = rtl_top_path(rtl)
    power_intent = _design(supply_nets={"n": SupplyNet(id="n", name="VDD_RAM", scope="u_ram")})

    assert macro_attachments(power_intent, rtl, "/proj/top.upf") == {f"{top}/u_ram": "/proj/top.upf"}


# --- (d) colour distinctness across imported domains -----------------------


def test_every_imported_domains_colour_source_is_visually_distinct():
    """A domain's colour is DERIVED from its primary supply set's power
    rail, so distinctness is a property of the supplies backing them --
    which is what the import actually assigns."""
    from frontend.src.power_intent_view.import_palette import are_visually_distinct

    rtl = build_mock_design()
    top = rtl_top_path(rtl)
    supply_names = [f"VDD_{i}" for i in range(8)]
    power_intent = _design(
        supply_nets={n: SupplyNet(id=n, name=n, scope="") for n in supply_names},
        supply_sets={
            f"SS_{i}": SupplySet(id=f"SS_{i}", name=f"SS_{i}", scope="", power_net=n)
            for i, n in enumerate(supply_names)
        },
        domains={
            f"D{i}": PowerDomain(id=f"D{i}", name=f"PD_{i}", scope="", elements=["."], primary_supply=f"SS_{i}")
            for i in range(8)
        },
    )
    result = import_power_intent(power_intent, rtl)
    assert len(result.domain.domains) == 8
    assert top  # resolution happened against the real design

    colors = [p.color for p in result.supply.supply_port_placements.values()]
    assert len(colors) == 8
    for i, first in enumerate(colors):
        for second in colors[i + 1:]:
            assert are_visually_distinct(first, second), f"{first.name()} vs {second.name()}"


# --- (e) the acknowledgment dialog (section 4) -----------------------------


def _ready(tab: LoadPowerIntentTab) -> None:
    tab.set_rtl(build_mock_design())
    tab.files_list.addItem("soc_top.upf")


def _run_mock_mapping(tab: LoadPowerIntentTab) -> None:
    loop = QEventLoop()
    tab.mappingCompleted.connect(lambda mapped: loop.quit())
    tab._start_mapping()
    loop.exec()


def _patch_question(monkeypatch, answer) -> list:
    asked: list = []

    def fake_question(parent, title, text, *args, **kwargs):
        asked.append((title, text))
        return answer

    monkeypatch.setattr(tab_module.message_box, "question", fake_question)
    return asked


def test_continuing_past_findings_asks_for_an_explicit_acknowledgment(qtbot, monkeypatch):
    tab = LoadPowerIntentTab()
    qtbot.addWidget(tab)
    _ready(tab)
    _run_mock_mapping(tab)
    assert tab.continue_button.isEnabled() is True

    asked = _patch_question(monkeypatch, QMessageBox.StandardButton.Yes)
    fired = []
    tab.continueRequested.connect(lambda: fired.append(1))
    tab.continue_button.click()

    assert len(asked) == 1
    title, text = asked[0]
    assert title == "Proceed With Findings"
    # Names what was actually found, not a generic "are you sure".
    assert "error" in text or "warning" in text
    assert "Diagnostic section" in text
    assert fired == [1]


def test_declining_the_acknowledgment_does_not_navigate(qtbot, monkeypatch):
    tab = LoadPowerIntentTab()
    qtbot.addWidget(tab)
    _ready(tab)
    _run_mock_mapping(tab)

    _patch_question(monkeypatch, QMessageBox.StandardButton.No)
    fired = []
    tab.continueRequested.connect(lambda: fired.append(1))
    tab.continue_button.click()

    assert fired == []
    # Still available -- declining is not a dead end.
    assert tab.continue_button.isEnabled() is True


def test_the_acknowledgment_counts_match_what_enabled_the_button(qtbot, monkeypatch):
    tab = LoadPowerIntentTab()
    qtbot.addWidget(tab)
    _ready(tab)
    _run_mock_mapping(tab)

    expected_errors = sum(1 for d in tab._last_diagnostics if d.severity is Severity.ERROR)
    expected_warnings = sum(1 for d in tab._last_diagnostics if d.severity is Severity.WARNING)
    assert expected_errors or expected_warnings  # the mock path always has findings

    asked = _patch_question(monkeypatch, QMessageBox.StandardButton.Yes)
    tab.continue_button.click()
    text = asked[0][1]
    if expected_errors:
        assert f"{expected_errors} error" in text
    if expected_warnings:
        assert f"{expected_warnings} warning" in text


# --- (f) the dirty-flag re-load gate (section 7) ---------------------------


def test_a_first_load_never_asks_about_discarding_anything(qtbot, monkeypatch):
    tab = LoadPowerIntentTab(dirty_check=lambda: True)
    qtbot.addWidget(tab)
    _ready(tab)
    asked = _patch_question(monkeypatch, QMessageBox.StandardButton.Yes)

    _run_mock_mapping(tab)
    assert asked == []  # nothing existed to lose


def test_a_reload_with_no_edits_does_not_ask_either(qtbot, monkeypatch):
    tab = LoadPowerIntentTab(dirty_check=lambda: False)
    qtbot.addWidget(tab)
    _ready(tab)
    _run_mock_mapping(tab)
    asked = _patch_question(monkeypatch, QMessageBox.StandardButton.Yes)

    _run_mock_mapping(tab)
    assert asked == []


def test_a_reload_with_edits_confirms_first_and_can_be_cancelled(qtbot, monkeypatch):
    tab = LoadPowerIntentTab(dirty_check=lambda: True)
    qtbot.addWidget(tab)
    _ready(tab)
    _run_mock_mapping(tab)

    asked = _patch_question(monkeypatch, QMessageBox.StandardButton.No)
    imported = []
    tab.powerIntentImported.connect(lambda i: imported.append(i))
    tab._start_mapping()  # declined -- returns before any work

    assert len(asked) == 1
    assert asked[0][0] == "Replace Imported Power Intent"
    assert "discarded" in asked[0][1]
    assert imported == []


def test_a_confirmed_reload_runs_the_whole_pipeline_again(qtbot, monkeypatch):
    tab = LoadPowerIntentTab(dirty_check=lambda: True)
    qtbot.addWidget(tab)
    _ready(tab)
    _run_mock_mapping(tab)

    _patch_question(monkeypatch, QMessageBox.StandardButton.Yes)
    imported = []
    tab.powerIntentImported.connect(lambda i: imported.append(i))
    _run_mock_mapping(tab)

    assert len(imported) == 1


# --- (g) the import is emitted, and a failure in it does not lose the run --


def test_the_imported_state_is_emitted_after_mapping_succeeds(qtbot):
    tab = LoadPowerIntentTab()
    qtbot.addWidget(tab)
    _ready(tab)
    order: list[str] = []
    tab.mappingCompleted.connect(lambda m: order.append("mapped"))
    tab.powerIntentImported.connect(lambda i: order.append("imported"))

    _run_mock_mapping(tab)

    assert order == ["mapped", "imported"]
    assert isinstance(tab._imported, ImportedPowerIntent)
    assert tab._imported.domain.domains  # real state, not an empty shell


def test_a_translation_failure_is_reported_as_a_diagnostic_not_raised(qtbot, monkeypatch):
    """Mapping has already succeeded and been emitted by this point, so
    a bug in the translation must not take the whole run down."""
    tab = LoadPowerIntentTab()
    qtbot.addWidget(tab)
    _ready(tab)

    def boom(*args, **kwargs):
        raise RuntimeError("synthetic translation failure")

    monkeypatch.setattr(tab_module, "import_power_intent", boom)
    _run_mock_mapping(tab)  # must not raise

    codes = [d.code for d in tab.diagnostics_dock.model._visible()]
    assert "POWER-INTENT-IMPORT-FAILED" in codes
    assert tab.continue_button.isEnabled() is True  # an ERROR gates navigation


def test_import_diagnostics_do_not_replace_the_load_or_mapping_batches(qtbot):
    """DiagnosticsTableModel replaces per SOURCE, so the import needs its
    own key or each batch silently erases another's."""
    tab = LoadPowerIntentTab()
    qtbot.addWidget(tab)
    _ready(tab)
    _run_mock_mapping(tab)

    sources = {d.source for d in tab.diagnostics_dock.model._visible()}
    assert sources == {"load_power_intent", "mapping", "power_intent_import"}


# --- the Power Intent tab's own side of it ---------------------------------


def test_applying_an_import_replaces_the_whole_diagram_state_and_clears_dirty(qtbot):
    from frontend.src.power_intent_view.power_intent_tab import PowerIntentTab
    from frontend.src.power_intent_view.supply_port_overlay import SupplyPortPlacement
    from PySide6.QtGui import QColor

    rtl = build_mock_design()
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(rtl)

    # A hand-placed supply, then an edit, marks the tab dirty.
    tab.diagram.shared_supply_state.supply_port_placements["hand"] = SupplyPortPlacement(
        id="hand", owner_path=rtl_top_path(rtl), name="HAND", color=QColor("#ff0000")
    )
    tab.diagram.diagramStateChanged.emit()
    assert tab.is_dirty() is True

    imported = import_power_intent(build_mock_power_intent(), rtl)
    tab.apply_imported_power_intent(rtl, imported)

    names = {p.name for p in tab.diagram.shared_supply_state.supply_port_placements.values()}
    assert "HAND" not in names  # replaced wholesale, not merged
    assert names  # and the imported supplies are there
    assert tab.is_dirty() is False  # a replace is not an edit
