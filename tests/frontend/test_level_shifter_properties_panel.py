"""Level Shifter Properties table: "Level Shifter" (renamed from "Level
Shifter Instances" on a later tab rename/reorder pass) -- merges real
(backend PowerIntentDesign) rows with diagram-only ones (see
properties_panel.py's own _load_level_shifters() docstring).

Rework (explicit request, REVERSES the earlier "Level Shifter Cell
Type dropdown referencing a reusable library" model): the "Level
Shifter Cell" secondary table/tab is GONE entirely -- there is no more
reusable library entity to show a secondary table of. Cell Type is
tracked directly on the instance (LevelShifterStrategyPlacement.cell_type,
free text) and shown via a plain "Cell Type" column (renamed from
"Level Shifter Cell Type")."""

from PySide6.QtGui import QColor, QFont

from frontend.src.power_intent_view.level_shifter_overlay import LevelShifterDiagramState, LevelShifterStrategyPlacement
from frontend.src.power_intent_view.power_domain_overlay import PowerDomainDiagramState, PowerDomainPlacement
from frontend.src.power_intent_view.power_intent_tab import PowerIntentTab
from frontend.src.power_intent_view.properties_panel import PowerIntentPropertiesPanel
from frontend.src.power_intent_view.selection_controller import PowerIntentSelectionController
from frontend.src.shared.mock_backend import build_mock_design


def _panel(qtbot):
    panel = PowerIntentPropertiesPanel(PowerIntentSelectionController())
    qtbot.addWidget(panel)
    return panel


def test_level_shifter_instances_tab_has_the_specified_columns(qtbot):
    panel = _panel(qtbot)
    table = panel._tables["LevelShifterStrategy"]
    headers = [table.horizontalHeaderItem(c).text() for c in range(table.columnCount())]
    assert headers == ["Name", "Power Domain", "Applies To All", "Rule", "Location", "Threshold", "Cell Type", "Elements"]


def test_level_shifter_instances_tab_title_is_renamed(qtbot):
    """"Level Shifter Instances" -> "Level Shifter" on a later tab
    rename/reorder pass (explicit request)."""
    panel = _panel(qtbot)
    index = next(i for i, kind in panel._kind_for_tab.items() if kind == "LevelShifterStrategy")
    assert panel._tabs.tabText(index) == "Level Shifter"


def test_level_shifter_cell_secondary_table_no_longer_exists(qtbot):
    panel = _panel(qtbot)
    assert not hasattr(panel, "_level_shifter_cells_table")


def test_diagram_only_level_shifter_row_merges_in_with_display_labels(qtbot):
    panel = _panel(qtbot)
    domain_state = PowerDomainDiagramState(domains={"d1": PowerDomainPlacement(id="d1", owner_path="top", name="PD1", color=QColor("#ff0000"))})
    level_shifter_state = LevelShifterDiagramState(
        strategies={
            "ls1": LevelShifterStrategyPlacement(
                id="ls1",
                name="LS1",
                power_domain_id="d1",
                applies_to="inputs",
                rule="low_to_high",
                location="parent",
                threshold=0.5,
                cell_type="LSCELL",
                elements=["top/clk"],
            )
        },
    )

    panel.refresh_diagram_overlay(
        supply_state=__import__("frontend.src.power_intent_view.supply_port_overlay", fromlist=["SupplyPortDiagramState"]).SupplyPortDiagramState(),
        domain_state=domain_state,
        level_shifter_state=level_shifter_state,
    )

    table = panel._tables["LevelShifterStrategy"]
    assert table.rowCount() == 1
    values = [table.item(0, c).text() if table.item(0, c) is not None else None for c in range(7)]
    assert values == ["LS1", "PD1", "Inputs", "Low to High", "parent", "0.5", "LSCELL"]


def test_missing_cell_type_shows_the_warning_and_is_italic(qtbot):
    """Section 1/6(b): an empty Cell Type still triggers the existing
    generation-time warning -- same "No Cell Type Assigned" mechanism
    Power Switch's own Cell Type column already establishes, now keyed
    off an empty string rather than an unassigned library reference."""
    panel = _panel(qtbot)
    domain_state = PowerDomainDiagramState(domains={"d1": PowerDomainPlacement(id="d1", owner_path="top", name="PD1", color=QColor("#ff0000"))})
    level_shifter_state = LevelShifterDiagramState(
        strategies={
            "ls1": LevelShifterStrategyPlacement(id="ls1", name="LS1", power_domain_id="d1", applies_to="inputs", rule="low_to_high", elements=[])
        },
    )

    panel.refresh_diagram_overlay(
        supply_state=__import__("frontend.src.power_intent_view.supply_port_overlay", fromlist=["SupplyPortDiagramState"]).SupplyPortDiagramState(),
        domain_state=domain_state,
        level_shifter_state=level_shifter_state,
    )

    table = panel._tables["LevelShifterStrategy"]
    item = table.item(0, 6)
    assert item.text() == "No Cell Type Assigned"
    assert QFont(item.font()).italic() is True


def test_real_backend_level_shifter_row_leaves_advisory_fields_blank(qtbot):
    """The real backend LevelShifterStrategy schema has no applies_to/
    rule/location/threshold/cell-type concept at all -- same
    "schema doesn't have it" precedent every other real-row blank
    column here already follows."""
    panel = _panel(qtbot)
    panel.load(build_mock_power_intent_with_level_shifter())

    table = panel._tables["LevelShifterStrategy"]
    row = next(r for r in range(table.rowCount()) if table.item(r, 0).text() == "LS_REAL")
    assert table.item(row, 2).text() == ""  # Applies To
    assert table.item(row, 3).text() == ""  # Rule
    assert table.item(row, 5).text() == ""  # Threshold
    assert table.item(row, 6).text() == ""  # Level Shifter Cell Type


def build_mock_power_intent_with_level_shifter():
    from dataclasses import replace as dataclasses_replace

    from frontend.src.shared.mock_backend import build_mock_power_intent
    from frontend.src.shared.models.power_intent import LevelShifterStrategy

    design = build_mock_power_intent()
    ls = LevelShifterStrategy(id="ls_real", name="LS_REAL", scope="u_core", domain="PD1", elements=["clk"])
    return dataclasses_replace(design, level_shifter_strategies={**design.level_shifter_strategies, "ls_real": ls})


def test_edit_remove_dispatch_reuses_the_diagram_views_own_methods(qtbot, monkeypatch):
    """Third entry point (same as every other construct kind) --
    Properties table Edit/Remove for Level Shifter dispatches to the
    diagram view's own edit_properties_row()/remove_properties_row(),
    not a parallel implementation. QMessageBox.question() is
    monkeypatched to auto-accept -- a real one is a blocking modal that
    hangs a headless test run indefinitely (confirmed empirically)."""
    import frontend.src.power_intent_view.power_intent_diagram_view as mod

    monkeypatch.setattr(mod.message_box, "question", lambda *a, **k: mod.QMessageBox.StandardButton.Yes)
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    box = view._boxes["top"]
    domain = view.create_power_domain(box, "PD1", QColor("#ff0000"), 0.5, None, [], {})
    placement = view.create_level_shifter(box, "LS1", domain.id, "inputs", "low_to_high", "self", None, None, ["top/clk"])

    assert view.can_edit_properties_row("LevelShifterStrategy", placement.id) is True
    assert view.can_remove_properties_row("LevelShifterStrategy", placement.id) is True

    view.remove_properties_row("LevelShifterStrategy", placement.id)  # goes through _prompt_delete_level_shifter's own (now auto-accepted) confirmation

    assert placement.id not in view._level_shifter_state.strategies
