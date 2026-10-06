"""Supply Port "Direction" field -- UPF `create_supply_port -direction`.

SCOPE IS THE POINT OF HALF THESE TESTS: Direction applies to a plain
Supply Port only. An Internal Supply and a Virtual Supply are not
boundary ports and have no direction -- and because all three share ONE
dialog class (AddPowerSupplyDialog) and ONE placement dataclass
(SupplyPortPlacement), "it doesn't apply" has to be actively enforced
rather than assumed, in the dialog, the generator, the Properties table
and the importer alike.

CPF: there is NO equivalent, established by reading the Si2 CPF 1.0
specification directly rather than by assuming -- see
power_intent_text_export.supply_port_direction_cpf_warnings()'s own
docstring for the evidence. Generation therefore emits nothing and warns.
"""

from __future__ import annotations

import pytest
from PySide6.QtGui import QColor

from frontend.src.power_intent_view.add_power_supply_dialog import (
    DEFAULT_SUPPLY_PORT_DIRECTION,
    DIRECTION_EMPTY_LABEL,
    SUPPLY_PORT_DIRECTIONS,
    AddPowerSupplyDialog,
)
from frontend.src.power_intent_view.power_domain_overlay import PowerDomainDiagramState
from frontend.src.power_intent_view.power_intent_tab import PowerIntentTab
from frontend.src.power_intent_view.power_intent_text_export import (
    generate_cpf,
    generate_upf,
    supply_port_direction_cpf_warnings,
)
from frontend.src.power_intent_view.retention_overlay import RetentionDiagramState
from frontend.src.power_intent_view.supply_port_overlay import (
    SupplyPortDiagramState,
    SupplyPortPlacement,
    SupplyPortState,
)
from frontend.src.shared.mock_backend import build_mock_design

_COLOR = QColor("#ff0000")


def _port(name: str, direction: str = "", **kwargs) -> SupplyPortPlacement:
    return SupplyPortPlacement(
        id=name, owner_path="top", name=name, color=_COLOR, direction=direction, **kwargs
    )


# --- (a) the dialog -------------------------------------------------------


def test_the_dialog_shows_direction_between_name_and_colour(qtbot):
    """Explicit request: Name, then Direction, then Colour."""
    dialog = AddPowerSupplyDialog()
    qtbot.addWidget(dialog)
    form = dialog.layout()

    labels = [
        form.itemAt(row, form.ItemRole.LabelRole).widget().text()
        for row in range(form.rowCount())
        if form.itemAt(row, form.ItemRole.LabelRole) is not None
    ]
    assert "Name:" in labels and "Direction:" in labels and "Colour:" in labels
    assert labels.index("Name:") < labels.index("Direction:") < labels.index("Colour:")


def test_the_dialog_offers_exactly_empty_in_out_inout(qtbot):
    dialog = AddPowerSupplyDialog()
    qtbot.addWidget(dialog)
    combo = dialog.direction_combo

    assert [combo.itemText(i) for i in range(combo.count())] == [
        DIRECTION_EMPTY_LABEL, "in", "out", "inout",
    ]
    # The empty entry's DATA is "", which is what generation keys off.
    assert [combo.itemData(i) for i in range(combo.count())] == ["", "in", "out", "inout"]
    assert SUPPLY_PORT_DIRECTIONS == ("in", "out", "inout")


def test_the_dialog_defaults_to_in_not_empty(qtbot):
    """Explicit request: "in", so omitting the direction takes a
    deliberate act."""
    dialog = AddPowerSupplyDialog()
    qtbot.addWidget(dialog)

    assert DEFAULT_SUPPLY_PORT_DIRECTION == "in"
    assert dialog.direction_combo.currentData() == "in"
    assert dialog.direction == "in"


def test_choosing_empty_yields_an_empty_direction(qtbot):
    dialog = AddPowerSupplyDialog()
    qtbot.addWidget(dialog)
    dialog.direction_combo.setCurrentIndex(0)

    assert dialog.direction == ""


@pytest.mark.parametrize(
    "kwargs,label",
    [({"is_internal_supply": True}, "Internal Supply"), ({"is_virtual_supply": True}, "Virtual Supply")],
)
def test_direction_is_absent_from_the_internal_and_virtual_dialogs(qtbot, kwargs, label):
    """The scope rule, enforced where it would otherwise leak: all three
    kinds share this ONE dialog class."""
    dialog = AddPowerSupplyDialog(**kwargs)
    qtbot.addWidget(dialog)
    form = dialog.layout()

    assert dialog.direction_combo is None, f"Direction leaked into the {label} dialog"
    labels = [
        form.itemAt(row, form.ItemRole.LabelRole).widget().text()
        for row in range(form.rowCount())
        if form.itemAt(row, form.ItemRole.LabelRole) is not None
    ]
    assert "Direction:" not in labels
    # Readable unconditionally by a caller that does not branch on kind.
    assert dialog.direction == ""


def test_edit_prefills_the_existing_direction_including_empty(qtbot):
    """An imported port with no direction must reopen on <empty>, not
    silently acquire the "in" default the first time it is edited."""
    for existing in ("in", "out", "inout", ""):
        dialog = AddPowerSupplyDialog(initial_direction=existing)
        qtbot.addWidget(dialog)
        assert dialog.direction == existing


def test_an_unrecognised_stored_direction_falls_back_to_empty(qtbot):
    """A file can carry anything. Falling back to <empty> keeps the
    value out of generation rather than coercing it to a wrong one."""
    dialog = AddPowerSupplyDialog(initial_direction="sideways")
    qtbot.addWidget(dialog)

    assert dialog.direction == ""


# --- (b) UPF generation ---------------------------------------------------


@pytest.mark.parametrize("direction", ["in", "out", "inout"])
def test_a_set_direction_generates_the_flag(direction):
    supply = SupplyPortDiagramState(supply_port_placements={"p": _port("VDD", direction)})
    text = generate_upf(supply, PowerDomainDiagramState(), RetentionDiagramState(), "3.1")

    assert f"create_supply_port VDD -direction {direction}" in text


def test_an_empty_direction_generates_no_flag():
    supply = SupplyPortDiagramState(supply_port_placements={"p": _port("VDD", "")})
    text = generate_upf(supply, PowerDomainDiagramState(), RetentionDiagramState(), "3.1")

    assert "create_supply_port VDD\n" in text
    assert "-direction" not in text


def test_the_verb_is_create_supply_port():
    """Verified before implementing, per explicit instruction: the real
    UPF verb is `create_supply_port`. `create_power_supply` -- the name
    in the original request, which the request itself then corrected --
    is not a UPF command and must appear nowhere."""
    supply = SupplyPortDiagramState(supply_port_placements={"p": _port("VDD", "in")})
    text = generate_upf(supply, PowerDomainDiagramState(), RetentionDiagramState(), "3.1")

    assert "create_supply_port" in text
    assert "create_power_supply" not in text


@pytest.mark.parametrize(
    "kwargs", [{"is_internal_supply": True}, {"is_virtual_supply": True}]
)
def test_an_internal_or_virtual_supply_never_generates_a_direction(kwargs):
    """Neither gets a `create_supply_port` at all, so a direction
    somehow set on one must not leak into generation."""
    supply = SupplyPortDiagramState(
        supply_port_placements={"p": _port("VDD_X", "in", **kwargs)}
    )
    text = generate_upf(supply, PowerDomainDiagramState(), RetentionDiagramState(), "3.1")

    assert "-direction" not in text


# --- (c) CPF: no equivalent, reported ------------------------------------


@pytest.mark.parametrize("direction", ["in", "out", "inout"])
def test_cpf_emits_no_direction_and_warns(direction):
    """CPF has no supply-PORT construct at all, and its only -direction
    flag is define_level_shifter_cell's {up|down|bidir} -- a different
    concept. Read out of the Si2 CPF 1.0 spec directly. So: nothing
    emitted, and the user told."""
    supply = SupplyPortDiagramState(supply_port_placements={"p": _port("VDD", direction)})
    text = generate_cpf(supply, PowerDomainDiagramState(), RetentionDiagramState(), "2.0")

    assert "-direction" not in text
    warnings = supply_port_direction_cpf_warnings(supply)
    assert len(warnings) == 1
    assert "VDD" in warnings[0] and direction in warnings[0]
    assert "no construct for a supply port's direction" in warnings[0]


def test_cpf_does_not_warn_when_no_direction_is_set():
    supply = SupplyPortDiagramState(supply_port_placements={"p": _port("VDD", "")})

    assert supply_port_direction_cpf_warnings(supply) == []


@pytest.mark.parametrize(
    "kwargs", [{"is_internal_supply": True}, {"is_virtual_supply": True}, {"fixed": True}]
)
def test_cpf_does_not_warn_for_kinds_that_have_no_direction(kwargs):
    """No false alarm for a kind the field never applied to -- including
    a macro PG pin, which is fixed by its Liberty definition."""
    supply = SupplyPortDiagramState(
        supply_port_placements={"p": _port("VDD_X", "in", **kwargs)}
    )

    assert supply_port_direction_cpf_warnings(supply) == []


def test_the_cpf_warning_reaches_the_export_action(qtbot, monkeypatch, tmp_path):
    """End to end through MainWindow: the diagnostic must actually be
    shown, not merely computable."""
    from frontend.src.app.main_window import MainWindow
    from frontend.src.shared.widgets import message_box

    window = MainWindow()
    qtbot.addWidget(window)
    window.power_intent_tab.set_rtl(build_mock_design())
    window.power_intent_tab.diagram.shared_supply_state.supply_port_placements["p"] = _port("VDD", "in")

    shown: list[tuple[str, str]] = []
    monkeypatch.setattr(message_box, "warning", lambda parent, title, text, *a, **k: shown.append((title, text)))
    # A REAL path: the diagnostic deliberately fires only after the file
    # is actually written, so a cancelled save must not warn -- returning
    # "" here would have tested nothing.
    out = tmp_path / "out.cpf"
    monkeypatch.setattr(
        "frontend.src.app.main_window.QFileDialog.getSaveFileName",
        staticmethod(lambda *a, **k: (str(out), "")),
    )
    window._on_extract_power_intent("CPF", "2.0")

    assert shown, "exporting to CPF with a Direction set must warn"
    assert "Not Expressible in CPF" in shown[0][0]
    assert "VDD" in shown[0][1]


# --- (d) the Properties table --------------------------------------------


def test_the_properties_table_has_a_direction_column_showing_each_value(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    for name, direction in (("VDD_I", "in"), ("VDD_O", "out"), ("VDD_B", "inout"), ("VDD_N", "")):
        view.add_supply_port(view._boxes["top"], name, _COLOR, direction=direction)

    table = tab.properties._tables["SupplyPort"]
    headers = [table.horizontalHeaderItem(c).text() for c in range(table.columnCount())]
    assert headers == ["Type", "Name", "Direction", "Connected", "States"]

    by_name = {
        table.item(r, 1).text(): table.item(r, 2).text()
        for r in range(table.rowCount())
        if table.item(r, 1) is not None
    }
    assert by_name["VDD_I"] == "in"
    assert by_name["VDD_O"] == "out"
    assert by_name["VDD_B"] == "inout"
    assert by_name["VDD_N"] == "-"  # <empty> reads as the table's own blank marker


def test_internal_virtual_and_macro_pg_rows_show_no_direction(qtbot):
    """Blank, matching how every other not-applicable column in this
    same table is already handled (see _load_supply_ports's docstring on
    the real Port/Net rows' blank States)."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    view.add_internal_supply(view._boxes["top/u_core"], "VDD_INT", _COLOR)
    # A Virtual Supply requires at least one Power State (its own rule).
    view.add_virtual_supply(
        view._boxes["top/u_core"], "VDD_VIRT", _COLOR,
        states=[SupplyPortState("FULL_ON", "1.0")],
    )

    table = tab.properties._tables["SupplyPort"]
    rows = {
        table.item(r, 1).text(): (table.item(r, 0).text(), table.item(r, 2).text())
        for r in range(table.rowCount())
        if table.item(r, 1) is not None
    }
    assert rows["u_core/VDD_INT"] == ("Internal Supply", "-")
    assert rows["u_core/VDD_VIRT"] == ("Virtual Supply", "-")
    assert rows["u_ram/VDD"][1] == "-"  # a macro PG pin


# --- editing ---------------------------------------------------------------


def test_editing_a_supply_port_updates_its_direction(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    icon = view.add_supply_port(view._boxes["top"], "VDD", _COLOR, direction="in")

    view.edit_supply_port(icon, "VDD", _COLOR, direction="out")
    assert icon.placement.direction == "out"
    # <empty> is a real edit, not a no-op.
    view.edit_supply_port(icon, "VDD", _COLOR, direction="")
    assert icon.placement.direction == ""


def test_editing_without_passing_direction_leaves_it_alone(qtbot):
    """`None` means "this caller is not editing direction" -- distinct
    from "" meaning "the user chose <empty>". Keeps every pre-existing
    caller and test correct."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    icon = view.add_supply_port(view._boxes["top"], "VDD", _COLOR, direction="inout")

    view.edit_supply_port(icon, "VDD_RENAMED", _COLOR)
    assert icon.placement.direction == "inout"


def test_editing_an_internal_supply_never_writes_a_direction(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    icon = view.add_internal_supply(view._boxes["top/u_core"], "VDD_INT", _COLOR)

    view.edit_supply_port(icon, "VDD_INT", _COLOR, direction="in")
    assert icon.placement.direction == ""


# --- the project file ------------------------------------------------------


def test_direction_survives_a_project_save_and_load(tmp_path):
    """`.pis` uses a generic dataclass round-tripper, so a new field
    should need no codec change -- asserted rather than assumed."""
    from frontend.src.app.project_file import deserialize_project, serialize_project, ProjectData
    from frontend.src.power_intent_view.isolation_overlay import IsolationDiagramState
    from frontend.src.power_intent_view.level_shifter_overlay import LevelShifterDiagramState
    from frontend.src.power_intent_view.port_attribute_overlay import PortAttributeDiagramState
    from frontend.src.power_intent_view.power_state_table import PowerStateTableDiagramState
    from frontend.src.power_intent_view.power_switch_overlay import PowerSwitchDiagramState

    supply = SupplyPortDiagramState(
        supply_port_placements={"p": _port("VDD", "inout"), "q": _port("VSS", "")}
    )
    data = ProjectData(
        project_name="t",
        rtl_design=build_mock_design(),
        supply_state=supply,
        domain_state=PowerDomainDiagramState(),
        retention_state=RetentionDiagramState(),
        port_attribute_state=PortAttributeDiagramState(),
        power_switch_state=PowerSwitchDiagramState(),
        isolation_state=IsolationDiagramState(),
        level_shifter_state=LevelShifterDiagramState(),
        power_state_table_state=PowerStateTableDiagramState(),
    )
    restored = deserialize_project(serialize_project(data))

    by_name = {p.name: p.direction for p in restored.supply_state.supply_port_placements.values()}
    assert by_name == {"VDD": "inout", "VSS": ""}
