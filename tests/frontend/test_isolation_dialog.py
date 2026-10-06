"""isolation_dialog.py -- Add/Edit Isolation dialog: field validation,
the domain-driven Elements table (reusing Feature 2's own Driver/
Receiver Supply Set computation, not recomputing it), the field-scoped
TableFilterBar reuse (explicitly NOT retention_dialog.py's own
ListFilterBar), and the 4 Elements buttons."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QDialog, QMessageBox

from frontend.src.power_intent_view.isolation_dialog import IsolationDialog
from frontend.src.power_intent_view.isolation_overlay import IsolationStrategyPlacement
from frontend.src.power_intent_view.power_domain_overlay import PowerDomainPlacement, SupplySetPlacement
from frontend.src.power_intent_view.power_intent_tab import PowerIntentTab
from frontend.src.shared.mock_backend import build_mock_design
from frontend.src.shared.models.rtl import Definition, Design, Instance, Port, PortConnection, PortDirection, SourceLanguage
from frontend.src.shared.widgets.table_filter import TableFilterBar
from frontend.src.shared.widgets import message_box


def _fixture(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    boxes = view._boxes
    domain = PowerDomainPlacement(id="d1", owner_path="top", name="PD_TOP", color=QColor("#ff0000"))
    domains = {"d1": domain}
    ss1 = SupplySetPlacement(id="ss1", name="SS1", power="VDD", ground="VSS", owner_paths=["top"])
    supply_sets = {"ss1": ss1}
    return tab, boxes, domains, supply_sets


def _fixture_with_multi_bit_sub(qtbot):
    """Mirrors the reported repro's own "u_sub" hierarchy -- a nested
    block with a real multi-bit port (an 8-bit "d"), the exact shape
    that exposed Bug 1 (per-bit element keys never matching
    PowerResolutionCache's own whole-port-level (path, signal) keys --
    see collect_isolation_elements_for_domain()'s own docstring)."""
    sub = Definition(
        definition_id="sub",
        base_name="sub",
        language=SourceLanguage.VERILOG,
        ports=[
            Port(name="clk", direction=PortDirection.IN),
            Port(name="d", direction=PortDirection.IN, width=8),
            Port(name="q", direction=PortDirection.OUT, width=8),
        ],
    )
    top = Definition(
        definition_id="top",
        base_name="top",
        language=SourceLanguage.VERILOG,
        ports=[Port(name="clk", direction=PortDirection.IN)],
        instances=[Instance(name="u_sub", definition_id="sub", connections=[PortConnection(instance_port="clk", connected_to="clk")])],
    )
    design = Design(definitions={"top": top, "sub": sub}, top="top")
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(design)
    view = tab.diagram._view
    boxes = view._boxes
    domain = PowerDomainPlacement(id="d1", owner_path="top/u_sub", name="PD_SUB", color=QColor("#ff0000"))
    domains = {"d1": domain}
    return tab, view, boxes, domains


def _accept_ready_dialog(dialog: IsolationDialog, name: str = "ISO1") -> None:
    dialog.name_edit.setText(name)
    dialog.isolation_supply_combo.setCurrentIndex(dialog.isolation_supply_combo.findData("ss1"))
    dialog.clamp_group.selected = "low"
    dialog.isolation_control_edit.setText("iso_ctrl")
    dialog.sense_group.selected = "high"
    if dialog.elements_table.rowCount() > 0:
        dialog.elements_table.item(0, 0).setCheckState(Qt.CheckState.Checked)


def test_elements_table_is_populated_from_the_boundary_blocks_own_ports(qtbot):
    tab, boxes, domains, supply_sets = _fixture(qtbot)
    dialog = IsolationDialog(boxes, domains, supply_sets, lambda endpoints: ({}, {}), {})
    qtbot.addWidget(dialog)

    names = {dialog.elements_table.item(r, 0).text() for r in range(dialog.elements_table.rowCount())}

    assert names == {"top/clk", "top/sleep_n", "top/out1", "top/out2", "top/out3"}


# --- Bug 1 regression: multi-bit ports must resolve Receiver/Driver -------


def test_multi_bit_port_elements_show_the_same_receiver_driver_as_port_list(qtbot):
    """Bug fix, explicit repro: a Power Domain on a nested "u_sub"
    hierarchy with a real multi-bit port -- every row in the Isolation
    dialog's own Elements list must show non-empty Receiver/Driver
    Supply values matching EXACTLY what tab.properties'
    resolve_port_connectivity() (the same source PortList itself reads
    from) reports for the identical (path, port_name) key. An earlier,
    broken version derived per-BIT element keys ("top/u_sub/d[0]")
    that never matched PowerResolutionCache's own whole-port-level
    keys, leaving these cells blank for any port wider than 1 bit."""
    tab, view, boxes, domains = _fixture_with_multi_bit_sub(qtbot)
    domain = domains["d1"]
    dialog = IsolationDialog(boxes, domains, {}, view._resolve_connectivity, {}, target_paths=domain.owner_paths, initial_domain_id=domain.id)
    qtbot.addWidget(dialog)
    receiver_by_key, driver_by_key = tab.properties.resolve_port_connectivity([])

    assert dialog.elements_table.rowCount() == 3  # clk, d, q -- one row per PORT, not per bit
    for row in range(dialog.elements_table.rowCount()):
        name = dialog.elements_table.item(row, 0).text()
        owner_path, port_name = name.rsplit("/", 1)
        expected_receiver = receiver_by_key[(owner_path, port_name)].text
        expected_driver = driver_by_key[(owner_path, port_name)].text
        assert dialog.elements_table.item(row, 2).text() == expected_receiver
        assert dialog.elements_table.item(row, 3).text() == expected_driver
        assert dialog.elements_table.item(row, 2).text() != ""
        assert dialog.elements_table.item(row, 3).text() != ""


def test_elements_table_reuses_the_injected_connectivity_resolver(qtbot):
    """Explicit request: reuse Feature 2's own Driver/Receiver Supply
    Set computation, don't recompute it independently -- the dialog
    just calls whatever resolver it's handed."""
    tab, boxes, domains, supply_sets = _fixture(qtbot)
    captured = {}

    def fake_resolver(endpoints):
        captured["endpoints"] = endpoints
        receiver = {("top", "clk"): type("R", (), {"text": "REC_SS"})()}
        driver = {("top", "clk"): type("R", (), {"text": "DRV_SS"})()}
        return receiver, driver

    dialog = IsolationDialog(boxes, domains, supply_sets, fake_resolver, {})
    qtbot.addWidget(dialog)

    assert ("top", "clk") in captured["endpoints"]
    row = next(r for r in range(dialog.elements_table.rowCount()) if dialog.elements_table.item(r, 0).text() == "top/clk")
    assert dialog.elements_table.item(row, 2).text() == "REC_SS"
    assert dialog.elements_table.item(row, 3).text() == "DRV_SS"


def test_changing_domain_re_derives_source_supply_pool_and_elements(qtbot):
    tab, boxes, domains, supply_sets = _fixture(qtbot)
    domain2 = PowerDomainPlacement(id="d2", owner_path="top/u_core", name="PD_CORE", color=QColor("#00ff00"))
    domains["d2"] = domain2
    ss2 = SupplySetPlacement(id="ss2", name="SS2", power="VDD_CORE", owner_paths=["top/u_core"])
    supply_sets["ss2"] = ss2
    dialog = IsolationDialog(boxes, domains, supply_sets, lambda endpoints: ({}, {}), {})
    qtbot.addWidget(dialog)
    dialog.domain_combo.setCurrentIndex(dialog.domain_combo.findData("d1"))
    assert dialog.source_supply_combo.findText("SS1") >= 0
    assert dialog.source_supply_combo.findText("SS2") < 0  # ss2 belongs to a different domain's own hierarchy

    dialog.domain_combo.setCurrentIndex(dialog.domain_combo.findData("d2"))

    assert dialog.source_supply_combo.findText("SS2") >= 0
    assert dialog.source_supply_combo.findText("SS1") < 0
    element_names = {dialog.elements_table.item(r, 0).text() for r in range(dialog.elements_table.rowCount())}
    assert element_names  # non-empty -- "top/u_core" has real ports of its own
    assert all(name.startswith("top/u_core/") for name in element_names)  # every element is namespaced to the NEW domain's own boundary block


def test_changing_domain_resets_checked_elements(qtbot):
    tab, boxes, domains, supply_sets = _fixture(qtbot)
    domain2 = PowerDomainPlacement(id="d2", owner_path="top/u_dbg", name="PD_DBG", color=QColor("#00ff00"))
    domains["d2"] = domain2
    dialog = IsolationDialog(boxes, domains, supply_sets, lambda endpoints: ({}, {}), {})
    qtbot.addWidget(dialog)
    # Domains sort alphabetically by name ("PD_DBG" < "PD_TOP"), so d2 is
    # actually the combo's own default -- select d1 explicitly first so
    # the switch below is a genuine, signal-firing change.
    dialog.domain_combo.setCurrentIndex(dialog.domain_combo.findData("d1"))
    dialog.elements_table.item(0, 0).setCheckState(Qt.CheckState.Checked)
    assert dialog.elements

    dialog.domain_combo.setCurrentIndex(dialog.domain_combo.findData("d2"))

    assert dialog.elements == []


def test_editing_pre_selects_the_strategys_own_domain_and_checked_elements(qtbot):
    tab, boxes, domains, supply_sets = _fixture(qtbot)
    initial = IsolationStrategyPlacement(
        id="i1",
        name="ISO1",
        power_domain_id="d1",
        isolation_supply_set_id="ss1",
        clamp_value="low",
        isolation_control="ctrl",
        isolation_sense="high",
        elements=["top/clk"],
    )
    dialog = IsolationDialog(boxes, domains, supply_sets, lambda endpoints: ({}, {}), {}, editing_strategy_id="i1", initial=initial)
    qtbot.addWidget(dialog)

    assert dialog.domain_combo.currentData() == "d1"
    assert dialog.isolation_supply_combo.currentData() == "ss1"
    assert dialog.clamp_group.selected == "low"
    assert dialog.isolation_sense == "high"
    assert dialog.elements == ["top/clk"]


# --- accept() validation ---------------------------------------------------


def test_accept_rejects_missing_clamp_value(qtbot, monkeypatch):
    tab, boxes, domains, supply_sets = _fixture(qtbot)
    calls = []
    monkeypatch.setattr(message_box, "warning", lambda parent, title, text: calls.append(title))
    dialog = IsolationDialog(boxes, domains, supply_sets, lambda endpoints: ({}, {}), {})
    qtbot.addWidget(dialog)
    _accept_ready_dialog(dialog)
    dialog.clamp_group.selected = None

    dialog.accept()

    assert dialog.result() == QDialog.DialogCode.Rejected
    assert calls[0] == "Missing Clamp Value"


def test_accept_rejects_missing_isolation_sense(qtbot, monkeypatch):
    tab, boxes, domains, supply_sets = _fixture(qtbot)
    calls = []
    monkeypatch.setattr(message_box, "warning", lambda parent, title, text: calls.append(title))
    dialog = IsolationDialog(boxes, domains, supply_sets, lambda endpoints: ({}, {}), {})
    qtbot.addWidget(dialog)
    _accept_ready_dialog(dialog)
    dialog.sense_group.selected = None

    dialog.accept()

    assert dialog.result() == QDialog.DialogCode.Rejected
    assert calls[0] == "Missing Isolation Sense"


def test_accept_rejects_no_elements_selected(qtbot, monkeypatch):
    tab, boxes, domains, supply_sets = _fixture(qtbot)
    calls = []
    monkeypatch.setattr(message_box, "warning", lambda parent, title, text: calls.append(title))
    dialog = IsolationDialog(boxes, domains, supply_sets, lambda endpoints: ({}, {}), {})
    qtbot.addWidget(dialog)
    dialog.name_edit.setText("ISO1")
    dialog.isolation_supply_combo.setCurrentIndex(dialog.isolation_supply_combo.findData("ss1"))
    dialog.clamp_group.selected = "low"
    dialog.isolation_control_edit.setText("ctrl")
    dialog.sense_group.selected = "high"
    # no element checked

    dialog.accept()

    assert dialog.result() == QDialog.DialogCode.Rejected
    assert calls[0] == "No Elements Selected"


def test_accept_rejects_a_duplicate_name(qtbot, monkeypatch):
    tab, boxes, domains, supply_sets = _fixture(qtbot)
    calls = []
    monkeypatch.setattr(message_box, "warning", lambda parent, title, text: calls.append(title))
    dialog = IsolationDialog(boxes, domains, supply_sets, lambda endpoints: ({}, {}), {"other_id": "ISO1"})
    qtbot.addWidget(dialog)
    _accept_ready_dialog(dialog, name="ISO1")

    dialog.accept()

    assert dialog.result() == QDialog.DialogCode.Rejected
    assert calls[0] == "Duplicate Isolation Name"


def test_accept_succeeds_with_every_required_field_set(qtbot):
    tab, boxes, domains, supply_sets = _fixture(qtbot)
    dialog = IsolationDialog(boxes, domains, supply_sets, lambda endpoints: ({}, {}), {})
    qtbot.addWidget(dialog)
    _accept_ready_dialog(dialog)

    dialog.accept()

    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.source_supply_set_id is None  # optional, left blank
    assert dialog.sink_supply_set_id is None  # optional, left blank


def test_source_and_sink_supply_sets_are_genuinely_optional(qtbot):
    tab, boxes, domains, supply_sets = _fixture(qtbot)
    dialog = IsolationDialog(boxes, domains, supply_sets, lambda endpoints: ({}, {}), {})
    qtbot.addWidget(dialog)
    _accept_ready_dialog(dialog)

    dialog.accept()

    assert dialog.result() == QDialog.DialogCode.Accepted


# --- Item 4: Direction column + Inputs/Outputs/Both filter ----------------


def test_elements_table_has_a_direction_column_reusing_port_list_data(qtbot):
    tab, boxes, domains, supply_sets = _fixture(qtbot)
    dialog = IsolationDialog(boxes, domains, supply_sets, lambda endpoints: ({}, {}), {})
    qtbot.addWidget(dialog)

    headers = [dialog.elements_table.horizontalHeaderItem(i).text() for i in range(dialog.elements_table.columnCount())]
    assert headers == ["Name", "Direction", "Receiver Supply", "Driver Supply Set"]
    directions = {dialog.elements_table.item(r, 0).text(): dialog.elements_table.item(r, 1).text() for r in range(dialog.elements_table.rowCount())}
    assert directions["top/clk"] == "IN"
    assert directions["top/out1"] == "OUT"


def test_direction_filter_defaults_to_both_and_shows_every_row(qtbot):
    tab, boxes, domains, supply_sets = _fixture(qtbot)
    dialog = IsolationDialog(boxes, domains, supply_sets, lambda endpoints: ({}, {}), {})
    qtbot.addWidget(dialog)

    assert dialog.direction_filter_group.selected == "both"
    assert all(not dialog.elements_table.isRowHidden(r) for r in range(dialog.elements_table.rowCount()))


def test_direction_filter_inputs_hides_output_rows(qtbot):
    tab, boxes, domains, supply_sets = _fixture(qtbot)
    dialog = IsolationDialog(boxes, domains, supply_sets, lambda endpoints: ({}, {}), {})
    qtbot.addWidget(dialog)

    dialog.direction_filter_group.selected = "in"

    for row in range(dialog.elements_table.rowCount()):
        direction = dialog.elements_table.item(row, 1).text()
        assert dialog.elements_table.isRowHidden(row) == (direction != "IN")


def test_direction_filter_outputs_hides_input_rows(qtbot):
    tab, boxes, domains, supply_sets = _fixture(qtbot)
    dialog = IsolationDialog(boxes, domains, supply_sets, lambda endpoints: ({}, {}), {})
    qtbot.addWidget(dialog)

    dialog.direction_filter_group.selected = "out"

    for row in range(dialog.elements_table.rowCount()):
        direction = dialog.elements_table.item(row, 1).text()
        assert dialog.elements_table.isRowHidden(row) == (direction != "OUT")


def test_direction_filter_only_affects_visibility_never_checked_state(qtbot):
    """Explicit request: the filter controls table VISIBILITY ONLY --
    checking rows under one filter, switching to another, then back,
    must never lose or alter which elements are actually included."""
    tab, boxes, domains, supply_sets = _fixture(qtbot)
    dialog = IsolationDialog(boxes, domains, supply_sets, lambda endpoints: ({}, {}), {})
    qtbot.addWidget(dialog)
    in_row = next(r for r in range(dialog.elements_table.rowCount()) if dialog.elements_table.item(r, 1).text() == "IN")
    out_row = next(r for r in range(dialog.elements_table.rowCount()) if dialog.elements_table.item(r, 1).text() == "OUT")

    # Filter to Inputs, check an input row.
    dialog.direction_filter_group.selected = "in"
    dialog.elements_table.item(in_row, 0).setCheckState(Qt.CheckState.Checked)
    # Filter to Outputs, check an output row too -- the INPUT row is now
    # hidden (out of view), but must remain checked underneath.
    dialog.direction_filter_group.selected = "out"
    dialog.elements_table.item(out_row, 0).setCheckState(Qt.CheckState.Checked)

    accumulated = set(dialog.elements)
    assert dialog.elements_table.item(in_row, 0).data(Qt.ItemDataRole.UserRole) in accumulated
    assert dialog.elements_table.item(out_row, 0).data(Qt.ItemDataRole.UserRole) in accumulated
    assert len(accumulated) == 2  # accumulated across the filter change, not reset/lost

    # Back to Both -- both rows visible again, both still checked.
    dialog.direction_filter_group.selected = "both"
    assert dialog.elements_table.item(in_row, 0).checkState() == Qt.CheckState.Checked
    assert dialog.elements_table.item(out_row, 0).checkState() == Qt.CheckState.Checked
    assert set(dialog.elements) == accumulated


def test_direction_filter_combines_with_the_text_filter_not_replaces_it(qtbot):
    tab, boxes, domains, supply_sets = _fixture(qtbot)
    dialog = IsolationDialog(boxes, domains, supply_sets, lambda endpoints: ({}, {}), {})
    qtbot.addWidget(dialog)

    dialog.direction_filter_group.selected = "in"  # top/clk, top/sleep_n
    dialog.elements_filter.set_column("Name")
    dialog.elements_filter.setText("*clk*")  # top/clk only

    visible = [dialog.elements_table.item(r, 0).text() for r in range(dialog.elements_table.rowCount()) if not dialog.elements_table.isRowHidden(r)]
    assert visible == ["top/clk"]


def test_direction_column_is_sortable(qtbot):
    tab, boxes, domains, supply_sets = _fixture(qtbot)
    dialog = IsolationDialog(boxes, domains, supply_sets, lambda endpoints: ({}, {}), {})
    qtbot.addWidget(dialog)

    dialog.elements_table.sortItems(1)  # click-to-sort by Direction, ascending

    directions = [dialog.elements_table.item(r, 1).text() for r in range(dialog.elements_table.rowCount())]
    assert directions == sorted(directions)


# --- Elements: field-scoped filter (TableFilterBar reuse) -----------------


def test_elements_table_filter_is_a_table_filter_bar_not_a_list_filter_bar(qtbot):
    """Explicit request: the NEW field-scoped filter capability is
    TableFilterBar (already field-scoped, "Apply to" column combo),
    reused as-is -- NOT retention_dialog.py's own ListFilterBar
    (wildcard-only, no column picker)."""
    tab, boxes, domains, supply_sets = _fixture(qtbot)
    dialog = IsolationDialog(boxes, domains, supply_sets, lambda endpoints: ({}, {}), {})
    qtbot.addWidget(dialog)

    assert isinstance(dialog.elements_filter, TableFilterBar)


def test_elements_table_filter_can_be_scoped_to_a_specific_column(qtbot):
    tab, boxes, domains, supply_sets = _fixture(qtbot)
    dialog = IsolationDialog(boxes, domains, supply_sets, lambda endpoints: ({}, {}), {})
    qtbot.addWidget(dialog)

    dialog.elements_filter.set_column("Name")
    dialog.elements_filter.setText("*clk*")  # wildcard -- a bare "clk" would require an EXACT cell match instead

    hidden = {dialog.elements_table.item(r, 0).text(): dialog.elements_table.isRowHidden(r) for r in range(dialog.elements_table.rowCount())}
    assert hidden["top/clk"] is False
    assert hidden["top/out1"] is True


# --- Elements: 4 buttons (Add/Clear Selected, Add/Clear All) --------------


def test_add_all_and_clear_all_elements(qtbot):
    tab, boxes, domains, supply_sets = _fixture(qtbot)
    dialog = IsolationDialog(boxes, domains, supply_sets, lambda endpoints: ({}, {}), {})
    qtbot.addWidget(dialog)

    dialog._add_all_elements()
    assert len(dialog.elements) == dialog.elements_table.rowCount()

    dialog._clear_all_elements()
    assert dialog.elements == []


def test_add_selected_and_clear_selected_only_affect_list_selected_rows(qtbot):
    tab, boxes, domains, supply_sets = _fixture(qtbot)
    dialog = IsolationDialog(boxes, domains, supply_sets, lambda endpoints: ({}, {}), {})
    qtbot.addWidget(dialog)
    dialog.elements_table.selectRow(0)

    dialog._add_selected_elements()

    assert len(dialog.elements) == 1

    dialog._clear_selected_elements()

    assert dialog.elements == []


def test_add_selected_button_disabled_with_nothing_list_selected(qtbot):
    tab, boxes, domains, supply_sets = _fixture(qtbot)
    dialog = IsolationDialog(boxes, domains, supply_sets, lambda endpoints: ({}, {}), {})
    qtbot.addWidget(dialog)

    assert dialog.add_selected_button.isEnabled() is False
    assert dialog.clear_selected_button.isEnabled() is False

    dialog.elements_table.selectRow(0)

    assert dialog.add_selected_button.isEnabled() is True
    assert dialog.clear_selected_button.isEnabled() is True


# --- separator reorganization (explicit request, section 3): NO field ------
# --- content changes -- only visual grouping via form_separator() rows -----
# --- Also section 4: "(Optional)" renders to the RIGHT of Source/Sink -------
# --- Supply Set, italic, never baked into the row's own caption label. ------


def _find_row_by_label_text(dialog, text):
    fields_form = dialog.layout().itemAt(0).layout()
    for row in range(fields_form.rowCount()):
        label_item = fields_form.itemAt(row, fields_form.ItemRole.LabelRole)
        if label_item is not None and label_item.widget() is not None and label_item.widget().text() == text:
            return row, fields_form
    raise AssertionError(f"no {text!r} row found in dialog")


def test_isolation_dialog_field_layout_and_separators_match_the_spec_exactly(qtbot):
    from PySide6.QtWidgets import QFrame

    from frontend.src.shared.widgets.target_hierarchy_label import APPLIES_TO_CAPTION

    tab, boxes, domains, supply_sets = _fixture(qtbot)
    dialog = IsolationDialog(boxes, domains, supply_sets, lambda endpoints: ({}, {}), {}, target_paths=["top"])
    qtbot.addWidget(dialog)

    fields_form = dialog.layout().itemAt(0).layout()
    kinds = []
    for row in range(fields_form.rowCount()):
        label_item = fields_form.itemAt(row, fields_form.ItemRole.LabelRole)
        field_item = fields_form.itemAt(row, fields_form.ItemRole.FieldRole)
        if label_item is None and field_item is not None and isinstance(field_item.widget(), QFrame):
            kinds.append("separator")
        else:
            kinds.append(label_item.widget().text())

    assert kinds == [
        APPLIES_TO_CAPTION,
        "separator",
        "Name:",
        "Power Domain:",
        # Prefix for the names of the inserted CELL instances -- distinct
        # from "Name", which names the strategy itself. Optional.
        "Name Prefix:",
        "Isolation Supply Set:",
        "Source Supply Set:",
        "Sink Supply Set:",
        "Clamp Value:",
        "Location:",
        # Renamed from "Isolation Control" -- label only; still the RTL
        # signal that controls isolation, and generation is unchanged.
        "Isolation Signal:",
        "Isolation Sense:",
        # Directly below Isolation Sense (explicit request): the same
        # Inputs | Outputs | Both | Elements choice Level Shifter has.
        "Applies To All:",
        "separator",
    ]


def test_optional_label_renders_to_the_right_of_source_and_sink_supply_set(qtbot):
    """Global styling rule (section 4): "(Optional)" renders to the
    RIGHT of the field it modifies, italic -- never baked into the
    row's own caption label (superseding the earlier "Source Supply Set
    (Optional)" caption-text convention)."""
    tab, boxes, domains, supply_sets = _fixture(qtbot)
    dialog = IsolationDialog(boxes, domains, supply_sets, lambda endpoints: ({}, {}), {})
    qtbot.addWidget(dialog)

    for caption, combo in (("Source Supply Set:", dialog.source_supply_combo), ("Sink Supply Set:", dialog.sink_supply_combo)):
        row, fields_form = _find_row_by_label_text(dialog, caption)
        assert caption == fields_form.itemAt(row, fields_form.ItemRole.LabelRole).widget().text()
        field_widget = fields_form.itemAt(row, fields_form.ItemRole.FieldRole).widget()
        layout = field_widget.layout()
        assert layout.itemAt(0).widget() is combo
        optional_label = layout.itemAt(1).widget()
        assert optional_label.text() == "(Optional)"
        assert optional_label.font().italic() is True


def test_isolation_dialog_applies_to_is_capped_at_ten_hierarchies(qtbot):
    """(c): the SAME shared make_target_hierarchy_label() capping
    behavior (see test_target_hierarchy_label.py for the full coverage)
    is wired into the Isolation dialog too, not just tested in
    isolation."""
    tab, boxes, domains, supply_sets = _fixture(qtbot)
    paths = [f"top/u_{i}" for i in range(15)]
    dialog = IsolationDialog(boxes, domains, supply_sets, lambda endpoints: ({}, {}), {}, target_paths=paths)
    qtbot.addWidget(dialog)

    _row, fields_form = _find_row_by_label_text(dialog, "Applies to:")
    applies_to_value = fields_form.itemAt(_row, fields_form.ItemRole.FieldRole).widget()
    lines = applies_to_value.text().split("\n")
    assert lines[:10] == paths[:10]
    assert lines[10] == "... and 5 more"


# --- "Name Prefix", and the "Isolation Control" -> "Isolation Signal"
# --- rename (label only) ------------------------------------------------


def test_name_prefix_is_optional_and_reaches_the_placement(qtbot):
    """It names the inserted CELL INSTANCES, not the strategy -- so it is
    optional, and empty is a perfectly ordinary state ("let the tool use
    its own default")."""
    tab, boxes, domains, supply_sets = _fixture(qtbot)
    dialog = IsolationDialog(boxes, domains, supply_sets, lambda endpoints: ({}, {}), {})
    qtbot.addWidget(dialog)

    assert dialog.name_prefix == ""
    dialog.name_prefix_edit.setText("  iso_lvf_  ")
    assert dialog.name_prefix == "iso_lvf_"  # stripped, like every other field here


def test_name_prefix_has_the_house_style_placeholder(qtbot):
    tab, boxes, domains, supply_sets = _fixture(qtbot)
    dialog = IsolationDialog(boxes, domains, supply_sets, lambda endpoints: ({}, {}), {})
    qtbot.addWidget(dialog)

    assert dialog.name_prefix_edit.placeholderText().startswith("e.g. iso_lvf_")


def test_name_prefix_prefills_from_an_existing_strategy(qtbot):
    tab, boxes, domains, supply_sets = _fixture(qtbot)
    initial = IsolationStrategyPlacement(
        id="i1", name="ISO1", power_domain_id="d1", isolation_supply_set_id="ss1",
        clamp_value="low", isolation_control="iso_en", isolation_sense="high",
        name_prefix="iso_lvf_",
    )
    dialog = IsolationDialog(
        boxes, domains, supply_sets, lambda endpoints: ({}, {}), {"i1": "ISO1"},
        editing_strategy_id="i1", initial=initial,
    )
    qtbot.addWidget(dialog)

    assert dialog.name_prefix_edit.text() == "iso_lvf_"


def test_the_isolation_signal_rename_is_label_only(qtbot):
    """The widget, its attribute name and the placement field it feeds
    are all unchanged -- only the label a user reads."""
    tab, boxes, domains, supply_sets = _fixture(qtbot)
    dialog = IsolationDialog(boxes, domains, supply_sets, lambda endpoints: ({}, {}), {})
    qtbot.addWidget(dialog)

    dialog.isolation_control_edit.setText("iso_en")
    assert dialog.isolation_control == "iso_en"
