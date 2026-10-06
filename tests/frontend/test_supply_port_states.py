"""Supply Port "States" (DVFS-style, explicit request): a top-level
Supply Port (never Internal Supply, never a macro PG pin) gets an
editable list of power states -- State Name/Type (Voltage/Off)/Voltage.
State *names* are never reserved/special-cased -- the Voltage-vs-Off
distinction comes entirely from the Type field (see SupplyPortState's
own docstring). Both AddPowerSupplyDialog's own standalone flow and
Create Power Domain's inline Supply Port creation flow share the exact
same SupplyPortStatesWidget, so testing it once here covers both entry
points.
"""

from dataclasses import replace

from PySide6.QtGui import QColor

from frontend.src.power_intent_view.add_power_supply_dialog import AddPowerSupplyDialog
from frontend.src.power_intent_view.create_power_domain_dialog import CreatePowerDomainDialog, CreateSupplySetDialog
from frontend.src.power_intent_view.power_intent_tab import PowerIntentTab
from frontend.src.power_intent_view.supply_port_overlay import SupplyPortState
from frontend.src.power_intent_view.supply_port_states_widget import COL_NAME, COL_TYPE, COL_VOLTAGE, SupplyPortStatesWidget
from frontend.src.shared.mock_backend import build_mock_design


# --- Bug fix: Power States section must never appear for an Internal Supply -


def test_power_states_section_never_appears_when_composing_a_supply_set_on_an_internal_block(qtbot, monkeypatch):
    """Explicit bug report: creating a Power Domain on a non-top-level
    hierarchy block means any supply name introduced via "Create Supply
    Set..." -> "Add New..." will be materialized as an Internal Supply
    (see power_intent_diagram_view.py's own _add_missing_supply_ports())
    -- the nested AddPowerSupplyDialog popped up along the way must
    never offer an editable Power States section in that case."""
    domain_dialog = CreatePowerDomainDialog({}, ["VDD"], {}, "top/u_core")
    qtbot.addWidget(domain_dialog)
    assert domain_dialog._is_internal_target is True

    supply_set_dialog = CreateSupplySetDialog(["VDD"], is_internal_supply=domain_dialog._is_internal_target)
    qtbot.addWidget(supply_set_dialog)

    captured = {}

    def fake_exec(self):
        captured["dialog"] = self
        self.name_edit.setText("VDD_NEW")
        return AddPowerSupplyDialog.DialogCode.Accepted

    monkeypatch.setattr(AddPowerSupplyDialog, "exec", fake_exec)
    supply_set_dialog.power_combo.setCurrentIndex(supply_set_dialog.power_combo.count() - 1)  # "Add New..."

    assert captured["dialog"].states_widget is None
    assert captured["dialog"].inherited_states_label is not None


def test_power_states_section_still_appears_when_composing_a_supply_set_on_a_top_level_block(qtbot, monkeypatch):
    domain_dialog = CreatePowerDomainDialog({}, ["VDD"], {}, "top")
    qtbot.addWidget(domain_dialog)
    assert domain_dialog._is_internal_target is False

    supply_set_dialog = CreateSupplySetDialog(["VDD"], is_internal_supply=domain_dialog._is_internal_target)
    qtbot.addWidget(supply_set_dialog)

    captured = {}

    def fake_exec(self):
        captured["dialog"] = self
        self.name_edit.setText("VDD_NEW")
        return AddPowerSupplyDialog.DialogCode.Accepted

    monkeypatch.setattr(AddPowerSupplyDialog, "exec", fake_exec)
    supply_set_dialog.power_combo.setCurrentIndex(supply_set_dialog.power_combo.count() - 1)  # "Add New..."

    assert captured["dialog"].states_widget is not None


# --- Bug fix: Power States entered via "Add New..." must actually persist ---


def test_power_states_entered_via_the_nested_add_new_flow_persist_on_the_created_supply_port(qtbot, monkeypatch):
    """Repro (explicit report): Create Power Domain for the TOP level ->
    Create Supply Set -> Power -> Add New (naming it "VDD"), entering 2+
    Power States before clicking OK. Root cause: _SupplyNameCombo's own
    "Add New..." handler read `dialog.name`/`dialog.color` but never
    `dialog.states` at all, so CreateSupplySetDialog.new_port_colors
    (and everything downstream of it -- CreatePowerDomainDialog.
    new_supply_ports -> _add_missing_supply_ports() ->
    SupplyPortPlacement construction) never had any states to carry in
    the first place, regardless of what was actually typed into the
    dialog's own Power States table. Fixed by threading a parallel
    `states`/`new_port_states`/`new_supply_port_states` value alongside
    `color`/`new_port_colors`/`new_supply_ports` the whole way through
    (see _SupplyNameCombo/CreateSupplySetDialog/CreatePowerDomainDialog/
    _add_missing_supply_ports()'s own docstrings). Verified via the
    resulting SupplyPortPlacement object itself (`view.
    _shared_state.supply_port_placements`), not just the dialog's own
    pre-OK visual state."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    top_box = view._boxes["top"]

    domain_dialog = CreatePowerDomainDialog({}, [], {}, "top")
    qtbot.addWidget(domain_dialog)
    assert domain_dialog._is_internal_target is False

    supply_set_dialog = CreateSupplySetDialog([], is_internal_supply=domain_dialog._is_internal_target, target_path="top")
    qtbot.addWidget(supply_set_dialog)

    def fake_add_power_supply_exec(self):
        self.name_edit.setText("VDD")
        self.color = QColor("#ff0000")
        # Two columns now (Type, Voltage) and no name cell -- the Type
        # combo is column 0 and the Voltage edit column 1.
        self.states_widget._add_row()
        self.states_widget.table.cellWidget(0, COL_TYPE).setCurrentText("FULL_ON")
        self.states_widget.table.cellWidget(0, COL_VOLTAGE).setText("0.8")
        self.states_widget._add_row()
        self.states_widget.table.cellWidget(1, COL_TYPE).setCurrentText("OFF")
        return AddPowerSupplyDialog.DialogCode.Accepted

    monkeypatch.setattr(AddPowerSupplyDialog, "exec", fake_add_power_supply_exec)
    supply_set_dialog.power_combo.setCurrentIndex(supply_set_dialog.power_combo.count() - 1)  # "Add New..."

    assert supply_set_dialog.new_port_colors == {"VDD": QColor("#ff0000")}
    assert set(supply_set_dialog.new_port_states) == {"VDD"}
    entered_states = supply_set_dialog.new_port_states["VDD"]
    assert [(s.state_type, s.voltage) for s in entered_states] == [("FULL_ON", "0.8"), ("OFF", None)]

    # Feed straight into the view's own directly-testable core, the SAME
    # way CreatePowerDomainDialog's own "Create Supply Set..." flow
    # would (its new_supply_ports/new_supply_port_states are just
    # CreateSupplySetDialog.new_port_colors/new_port_states merged in --
    # see CreatePowerDomainDialog._prompt_create_and_assign_supply_set())
    # -- confirms the fix all the way through to the real,
    # persisted SupplyPortPlacement object, not just the dialog's own
    # pre-OK bookkeeping.
    view.create_power_domain(
        top_box,
        "PD1",
        QColor("#00ff00"),
        0.5,
        None,
        [],
        {},
        new_supply_ports=supply_set_dialog.new_port_colors,
        new_supply_port_states=supply_set_dialog.new_port_states,
    )

    port = next(p for p in view._shared_state.supply_port_placements.values() if p.name == "VDD")
    assert [(s.state_type, s.voltage) for s in port.states] == [("FULL_ON", "0.8"), ("OFF", None)]


def _widget_with_states(qtbot, states):
    """Every state gets a Name, which validate() now requires.

    Auto-filled here (S1, S2, …) for the many tests that predate the Name
    column and are about something else entirely -- a test that cares
    about naming passes its own names in and they are left alone."""
    widget = SupplyPortStatesWidget()
    qtbot.addWidget(widget)
    widget.states = [
        state if state.name else replace(state, name=f"S{i + 1}")
        for i, state in enumerate(states)
    ]
    return widget


# --- Bug fix: dialog/table width, Voltage header, center alignment ----------


def test_dialog_is_wide_enough_for_the_states_table(qtbot):
    dialog = AddPowerSupplyDialog()
    qtbot.addWidget(dialog)
    assert dialog.minimumWidth() >= 420


def test_voltage_column_header_includes_the_unit(qtbot):
    from frontend.src.power_intent_view.supply_port_states_widget import VOLTAGE_HEADER

    widget = SupplyPortStatesWidget()
    qtbot.addWidget(widget)
    # Two columns now -- "State Name" is gone, so Voltage is column 1.
    assert [widget.table.horizontalHeaderItem(c).text() for c in range(widget.table.columnCount())] == [
            "Name", "Type", VOLTAGE_HEADER,
        ]
    assert VOLTAGE_HEADER == "Voltage [V]"


def test_voltage_cell_is_center_aligned(qtbot):
    from PySide6.QtCore import Qt

    widget = SupplyPortStatesWidget()
    qtbot.addWidget(widget)
    widget.states = [SupplyPortState("FULL_ON", "1.0")]

    assert bool(widget.table.cellWidget(0, COL_VOLTAGE).alignment() & Qt.AlignmentFlag.AlignHCenter)


# --- Bug fix: Type=Off shows 0.0 as a display-only convenience --------------


def test_a_type_without_a_level_disables_its_voltage_cell(qtbot):
    """FULL_ON/PARTIAL_ON carry a level; OFF/UNDETERMINED do not, and
    their Voltage cell is DISABLED rather than merely left blank. The
    "0.0" text is display-only -- `.states` still reports voltage=None,
    so nothing downstream confuses "off" with "an active 0.0V supply"."""
    widget = SupplyPortStatesWidget()
    qtbot.addWidget(widget)
    for state_type in ("OFF", "UNDETERMINED"):
        widget.states = [SupplyPortState(state_type)]
        edit = widget.table.cellWidget(0, COL_VOLTAGE)
        assert edit.isEnabled() is False, state_type
        assert widget.states[0].voltage is None


def test_a_state_displays_as_its_type_in_the_properties_table():
    """States have no authored name anymore, so the TYPE is the label and
    a levelled state shows its voltage alongside it."""
    from frontend.src.power_intent_view.properties_panel import _format_supply_state

    assert _format_supply_state(SupplyPortState("FULL_ON", "1.2")) == "FULL_ON (1.2V)"
    assert _format_supply_state(SupplyPortState("OFF")) == "OFF"
    assert _format_supply_state(SupplyPortState("UNDETERMINED")) == "UNDETERMINED"


def test_a_state_displays_as_its_type_in_the_internal_supply_preview():
    from frontend.src.power_intent_view.power_switch_overlay import _display_state

    assert _display_state(SupplyPortState("FULL_ON", "1.2")) == "FULL_ON — 1.2V"
    assert _display_state(SupplyPortState("OFF")) == "OFF"


# --- SupplyPortStatesWidget: multi-state creation, mixed Voltage/Off --------


def test_multiple_mixed_voltage_and_off_states_round_trip(qtbot):
    widget = _widget_with_states(
        qtbot,
        [
            SupplyPortState("FULL_ON", "1.0"),
            SupplyPortState("FULL_ON", "0.6"),
            SupplyPortState("OFF"),
        ],
    )

    assert widget.validate() is None
    # The helper auto-names unnamed rows (validate() requires a Name), so
    # this compares the physical fields the test is actually about.
    assert [(x.state_type, x.voltage) for x in widget.states] == [
        ("FULL_ON", "1.0"), ("FULL_ON", "0.6"), ("OFF", None),
    ]


def test_full_on_and_partial_on_at_the_same_voltage_are_different_states():
    """They DISPLAY identically ("1.2") but are not the same state --
    which is exactly why a row stores the canonical key, not the display
    text. See `state_key()`."""
    from frontend.src.power_intent_view.supply_port_overlay import state_display, state_key

    full, partial = SupplyPortState("FULL_ON", "1.2"), SupplyPortState("PARTIAL_ON", "1.2")
    assert state_display(full) == state_display(partial) == "1.2"
    assert state_key(full) != state_key(partial)


# --- validation --------------------------------------------------------------


def test_a_duplicate_name_is_rejected(qtbot):
    """REPLACES the old duplicate-ROW check, retired with the Name
    column: two states may now legitimately share a Type, and a voltage
    is required, so the row identity that check guarded is no longer the
    constraint. NAME is."""
    for states in (
        [SupplyPortState("FULL_ON", "1.0", name="ON"), SupplyPortState("FULL_ON", "0.6", name="ON")],
        [SupplyPortState("OFF", name="DOWN"), SupplyPortState("UNDETERMINED", name="DOWN")],
    ):
        widget = _widget_with_states(qtbot, states)
        assert widget.validate() is not None, states


def test_two_states_sharing_a_type_at_different_voltages_are_accepted(qtbot):
    """The DVFS case, explicitly legal now: the Name disambiguates them,
    so no auto-generated suffix scheme is needed."""
    widget = _widget_with_states(qtbot, [
        SupplyPortState("FULL_ON", "1.2", name="ON"),
        SupplyPortState("FULL_ON", "0.6", name="LOW_POWER"),
    ])
    assert widget.validate() is None
    assert [(x.name, x.voltage) for x in widget.states] == [("ON", "1.2"), ("LOW_POWER", "0.6")]


def test_the_same_voltage_under_two_different_types_is_not_a_duplicate(qtbot):
    """FULL_ON 1.2 and PARTIAL_ON 1.2 are legitimately different states,
    even though both DISPLAY as "1.2"."""
    widget = _widget_with_states(
        qtbot, [SupplyPortState("FULL_ON", "1.2"), SupplyPortState("PARTIAL_ON", "1.2")]
    )
    assert widget.validate() is None


def test_a_lone_levelled_state_still_needs_its_voltage(qtbot):
    """Supersedes the old "a LONE unspecified-voltage state is allowed"
    rule: a level is now required whenever the Type carries one, so being
    unambiguous is no longer enough on its own."""
    widget = _widget_with_states(qtbot, [SupplyPortState("FULL_ON", None)])
    assert widget.validate() is not None


def test_off_type_state_needs_no_voltage(qtbot):
    widget = _widget_with_states(qtbot, [SupplyPortState("OFF")])
    assert widget.validate() is None


# --- state-name uniqueness is per-Supply-Port, not global ---------------------


def test_two_supply_ports_may_hold_identical_state_lists(qtbot):
    """Nothing about a state is scoped diagram-wide -- two ports can hold
    exactly the same list. (This replaced a name-uniqueness test; names
    are gone, so what it now guards is that the duplicate-ROW check stays
    scoped to ONE port's own list.)"""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view

    port_a = view.add_supply_port(view._boxes["top"], "VDD_A", QColor("#ff0000"), states=[SupplyPortState("FULL_ON", "1.0")])
    port_b = view.add_supply_port(
        view._boxes["top/u_core"], "VDD_B", QColor("#00ff00"), states=[SupplyPortState("FULL_ON", "1.0")]
    )

    assert port_a.placement.states == port_b.placement.states


# --- dialog wiring: both entry points share the same widget -----------------


def test_standalone_dialog_exposes_the_states_widget_for_a_plain_supply_port(qtbot):
    dialog = AddPowerSupplyDialog()
    qtbot.addWidget(dialog)
    assert dialog.states_widget is not None
    assert isinstance(dialog.states_widget, SupplyPortStatesWidget)


def test_internal_supply_dialog_shows_no_editable_states_widget(qtbot):
    dialog = AddPowerSupplyDialog(is_internal_supply=True)
    qtbot.addWidget(dialog)
    assert dialog.states_widget is None
    assert dialog.states == []


def test_internal_supply_dialog_shows_not_yet_connected_when_no_lines_given(qtbot):
    dialog = AddPowerSupplyDialog(is_internal_supply=True, inherited_state_lines=None)
    qtbot.addWidget(dialog)
    assert dialog.inherited_states_label.text() == (
        "Inherited Power States: Not yet connected -- states will inherit once linked to a supply port."
    )


def test_internal_supply_dialog_shows_inherited_states_with_source_attribution(qtbot):
    dialog = AddPowerSupplyDialog(is_internal_supply=True, inherited_state_lines=["FULL_ON — 1.0V (from VDD)"])
    qtbot.addWidget(dialog)
    assert dialog.inherited_states_label.text() == "Inherited Power States:\nFULL_ON — 1.0V (from VDD)"


def test_add_supply_port_persists_states_from_the_dialogs_widget(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view

    icon = view.add_supply_port(
        view._boxes["top"], "VDD", QColor("#ff0000"), states=[SupplyPortState("FULL_ON", "1.0")]
    )

    assert icon.placement.states == [SupplyPortState("FULL_ON", "1.0")]


def test_edit_supply_port_updates_its_states(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    icon = view.add_supply_port(view._boxes["top"], "VDD", QColor("#ff0000"))

    view.edit_supply_port(icon, "VDD", QColor("#ff0000"), states=[SupplyPortState("FULL_ON", "0.6")])

    assert icon.placement.states == [SupplyPortState("FULL_ON", "0.6")]


def test_internal_supply_placement_never_stores_independent_states(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view

    icon = view.add_internal_supply(view._boxes["top/u_core"], "VDD_INT", QColor("#ff0000"))

    assert icon.placement.states == []


# --- Revert: no "General"/"Power States" section split, flat layout --------


def test_supply_port_dialog_has_no_group_box_sections(qtbot):
    """Explicit revert request: the earlier "General"/"Power States"
    QGroupBox reorganization is undone -- a single flat layout again,
    same as before that reorganization ever existed."""
    from PySide6.QtWidgets import QGroupBox

    dialog = AddPowerSupplyDialog()
    qtbot.addWidget(dialog)
    assert dialog.findChildren(QGroupBox) == []


def test_internal_supply_dialog_has_no_group_box_sections(qtbot):
    from PySide6.QtWidgets import QGroupBox

    dialog = AddPowerSupplyDialog(is_internal_supply=True)
    qtbot.addWidget(dialog)
    assert dialog.findChildren(QGroupBox) == []


def test_power_states_row_label_still_reads_power_states(qtbot):
    """The "Power States:" row label itself carries forward unchanged --
    only the section/GroupBox grouping is reverted."""
    from PySide6.QtWidgets import QLabel

    dialog = AddPowerSupplyDialog()
    qtbot.addWidget(dialog)
    assert any(label.text() == "Power States:" for label in dialog.findChildren(QLabel))


# --- Bug fix: the dialog window itself resizes to fit growing/shrinking -----
# --- Power States content, with no clipping and no manual resize needed ----


def test_dialog_grows_to_fit_added_power_states_rows_without_clipping(qtbot):
    """Explicit regression test request: open the dialog, populate
    several Power States rows, confirm every row is fully contained
    within the dialog's own visible area (no clipping, no manual
    resize needed)."""
    dialog = AddPowerSupplyDialog()
    qtbot.addWidget(dialog)
    dialog.show()

    for _ in range(8):
        dialog.states_widget._add_row()

    table = dialog.states_widget.table
    table_bottom = table.mapTo(dialog, table.rect().bottomRight()).y()
    assert table_bottom <= dialog.height()
    assert table.rowCount() == 8
    assert table.height() >= table.rowCount() * 20  # every row genuinely present, not scrolled/clipped


def test_dialog_shrinks_back_when_power_states_rows_are_removed(qtbot):
    dialog = AddPowerSupplyDialog()
    qtbot.addWidget(dialog)
    dialog.show()
    for _ in range(6):
        dialog.states_widget._add_row()
    grown_height = dialog.height()

    for _ in range(4):
        dialog.states_widget._row_buttons_layout.itemAt(1).widget().click()

    assert dialog.height() < grown_height
    table = dialog.states_widget.table
    table_bottom = table.mapTo(dialog, table.rect().bottomRight()).y()
    assert table_bottom <= dialog.height()


def test_dialog_already_fits_a_pre_populated_states_list_at_construction(qtbot):
    """The initial-population case (e.g. Edit Power Supply on a port
    that already has several states) must render correctly sized from
    the start too, not just after an interactive add/remove."""
    states = [SupplyPortState("FULL_ON", "1.0") for i in range(8)]
    dialog = AddPowerSupplyDialog(initial_states=states)
    qtbot.addWidget(dialog)
    dialog.show()

    table = dialog.states_widget.table
    table_bottom = table.mapTo(dialog, table.rect().bottomRight()).y()
    assert table_bottom <= dialog.height()
    assert table.rowCount() == 8


# --- Item 2: "+"/"-" icon buttons replace the old Add/Remove button pair ----


def test_plus_button_adds_a_row(qtbot):
    widget = SupplyPortStatesWidget()
    qtbot.addWidget(widget)
    assert widget.table.rowCount() == 0

    widget._add_button.click()

    assert widget.table.rowCount() == 1


def test_minus_button_removes_only_its_own_row(qtbot):
    widget = SupplyPortStatesWidget()
    qtbot.addWidget(widget)
    widget.states = [
        SupplyPortState("FULL_ON", "1.0"),
        SupplyPortState("FULL_ON", "0.8"),
    ]

    # Layout: [spacer, minus-row-0, minus-row-1, plus-button]
    widget._row_buttons_layout.itemAt(1).widget().click()

    assert widget.table.rowCount() == 1
    assert widget.states == [SupplyPortState("FULL_ON", "0.8")]


def test_old_add_remove_buttons_no_longer_exist(qtbot):
    widget = SupplyPortStatesWidget()
    qtbot.addWidget(widget)
    assert not hasattr(widget, "_remove_button")


# --- Item 2: all three columns are center-aligned ----------------------------


def test_both_columns_are_center_aligned(qtbot):
    from PySide6.QtCore import Qt

    widget = SupplyPortStatesWidget()
    qtbot.addWidget(widget)
    widget.states = [SupplyPortState("FULL_ON", "1.0")]

    type_combo = widget.table.cellWidget(0, COL_TYPE)
    assert bool(type_combo.lineEdit().alignment() & Qt.AlignmentFlag.AlignHCenter)
    assert bool(widget.table.cellWidget(0, COL_VOLTAGE).alignment() & Qt.AlignmentFlag.AlignHCenter)


# --- Item 2: "equivalent Active states" conflict validation -----------------


def test_a_levelled_state_with_no_voltage_is_rejected(qtbot):
    """Voltage is MANDATORY when the Type carries one. This replaces the
    old "two ambiguous no-voltage Active states" conflict check outright:
    that ambiguity is unreachable once a level is required, so the check
    became dead code rather than a rule worth keeping."""
    for state_type in ("FULL_ON", "PARTIAL_ON"):
        widget = _widget_with_states(qtbot, [SupplyPortState(state_type, None)])
        error = widget.validate()
        assert error is not None and state_type in error


def test_one_voltage_with_value_plus_one_off_is_accepted(qtbot):
    widget = _widget_with_states(
        qtbot,
        [
            SupplyPortState("FULL_ON", "1.0"),
            SupplyPortState("OFF"),
        ],
    )
    assert widget.validate() is None


def test_two_levelled_states_at_different_voltages_are_accepted(qtbot):
    widget = _widget_with_states(
        qtbot, [SupplyPortState("FULL_ON", "1.0"), SupplyPortState("FULL_ON", "0.8")]
    )
    assert widget.validate() is None


# --- Item 3: Internal Supply "Inherited Power States" preview messaging -----


def test_internal_supply_preview_unconnected_exact_message(qtbot):
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    internal = view.add_internal_supply(view._boxes["top/u_core"], "Y", QColor("#ff0000"))

    from frontend.src.power_intent_view.power_switch_overlay import describe_inherited_states

    lines = describe_inherited_states(internal.placement, view._shared_state, view._power_switch_state.switches)
    dialog = AddPowerSupplyDialog(is_internal_supply=True, inherited_state_lines=lines)
    qtbot.addWidget(dialog)

    assert dialog.inherited_states_label.text() == (
        "Inherited Power States: Not yet connected -- states will inherit once linked to a supply port."
    )


def test_internal_supply_preview_connected_with_no_switch_shows_source_attribution(qtbot):
    from frontend.src.power_intent_view.power_switch_overlay import describe_inherited_states

    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    source = view.add_supply_port(
        view._boxes["top"], "VDD", QColor("#ff0000"), states=[SupplyPortState("FULL_ON", "1.0")]
    )
    internal = view.add_internal_supply(view._boxes["top/u_core"], "Y", QColor("#ff0000"))
    view._shared_state.supply_connector_placements.append((source.key, internal.key))

    lines = describe_inherited_states(internal.placement, view._shared_state, view._power_switch_state.switches)
    dialog = AddPowerSupplyDialog(is_internal_supply=True, inherited_state_lines=lines)
    qtbot.addWidget(dialog)

    assert dialog.inherited_states_label.text() == "Inherited Power States:\nFULL_ON — 1.0V (from VDD)"


def test_internal_supply_preview_connected_with_switch_shows_via_attribution(qtbot):
    from frontend.src.power_intent_view.power_switch_overlay import describe_inherited_states, power_switch_connector_key

    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view
    source = view.add_supply_port(
        view._boxes["top"], "VDD", QColor("#ff0000"), states=[SupplyPortState("FULL_ON", "1.0")]
    )
    view.create_power_domain(view._boxes["top/u_core"], "PD_CORE", QColor("#00ff00"), 0.5, None, [], {})
    internal = view.add_internal_supply(view._boxes["top/u_core"], "Y", QColor("#ff0000"))
    switch = view.add_power_switch(internal, "SW_A", "in", "out", "ctrl", None, "on", "off")
    view._shared_state.supply_connector_placements.append((power_switch_connector_key(switch.id, internal.key), source.key))

    lines = describe_inherited_states(internal.placement, view._shared_state, view._power_switch_state.switches)
    dialog = AddPowerSupplyDialog(is_internal_supply=True, inherited_state_lines=lines)
    qtbot.addWidget(dialog)

    assert dialog.inherited_states_label.text() == (
        # A single plain OFF, with the switch named only in the
        # provenance suffix -- the state itself carries no switch name
        # anymore (see resolve_effective_states()'s redesigned rule).
        "Inherited Power States:\nFULL_ON — 1.0V (from VDD)\nSWITCH-OFF: OFF (via SW_A)"
    )


# --- Bug fix (round two): dialog fully fits all content at its default size --


def test_dialog_default_size_fully_fits_several_power_states_rows_no_clipping(qtbot):
    """Explicit regression request: open the "Add Power Supply" dialog,
    populate several Power States rows, confirm ALL content (Name,
    Colour, and every row) renders fully within the dialog's own
    DEFAULT size -- no clipping, no manual resize needed. Also locks in
    the round-two fix: the Power States row's own ALLOCATED layout
    space must match the table's own actual height exactly (no wasted/
    misallocated gap the round-one delta-resize fix left behind)."""
    dialog = AddPowerSupplyDialog()
    qtbot.addWidget(dialog)
    dialog.show()

    for i in range(7):
        dialog.states_widget._add_row()

    # Every row genuinely present and rendered, not scrolled/clipped.
    assert dialog.states_widget.table.rowCount() == 7
    table_bottom = dialog.states_widget.table.mapTo(dialog, dialog.states_widget.table.rect().bottomRight()).y()
    assert table_bottom <= dialog.height()
    # No leftover wasted space between the table and the OK/Cancel row --
    # the states widget's own allocated space matches its content exactly.
    assert dialog.states_widget.height() == dialog.states_widget.table.height()
    # Name/Colour rows (built before Power States) are still fully above
    # the table, never overlapped/clipped by it.
    assert dialog.name_edit.mapTo(dialog, dialog.name_edit.rect().bottomRight()).y() <= dialog.states_widget.geometry().top()


def test_dialog_can_still_be_shrunk_back_down_after_growing(qtbot):
    """The window must remain freely resizable in EITHER direction, not
    just grow -- explicit requirement. Also locks in a real bug found
    while fixing this: resize() alone refused to shrink the window back
    down once it had grown, due to a stale Qt-managed minimumSize()
    floor."""
    dialog = AddPowerSupplyDialog()
    qtbot.addWidget(dialog)
    dialog.show()
    # Captured fresh, right after construction -- the width floor is no
    # longer a flat 420 constant (see AddPowerSupplyDialog's own
    # __init__ docstring comment: it's now max(420, sizeHint().width()),
    # derived from this dialog's own real content), so this test's own
    # baseline must be whatever THIS instance actually got, not a
    # hardcoded number.
    original_min_width = dialog.minimumWidth()
    for _ in range(6):
        dialog.states_widget._add_row()
    grown_height = dialog.height()

    for _ in range(4):
        dialog.states_widget._row_buttons_layout.itemAt(1).widget().click()

    assert dialog.height() < grown_height
    assert dialog.minimumWidth() == original_min_width  # the width floor survives a height-only shrink, untouched


# --- (a)-(c): the Name column's own rules -------------------------------


def test_a_two_states_of_the_same_type_at_different_voltages_succeed(qtbot):
    """The DVFS case the Name column exists to make expressible. No
    auto-generated suffix scheme is involved -- the user names them."""
    widget = _widget_with_states(qtbot, [
        SupplyPortState("FULL_ON", "1.2", name="ON"),
        SupplyPortState("FULL_ON", "0.6", name="LOW_POWER"),
    ])
    assert widget.validate() is None


def test_b_a_duplicate_name_within_one_supply_is_rejected(qtbot):
    widget = _widget_with_states(qtbot, [
        SupplyPortState("FULL_ON", "1.2", name="ON"),
        SupplyPortState("OFF", name="ON"),
    ])
    message = widget.validate()
    assert message is not None and "ON" in message


def test_b_a_name_is_required_and_cannot_contain_spaces(qtbot):
    """Built directly, NOT through _widget_with_states() -- that helper
    auto-names unnamed rows for the many tests that predate this column,
    which would defeat the empty-name half of this check."""
    for name in ("", "two words"):
        widget = SupplyPortStatesWidget()
        qtbot.addWidget(widget)
        widget.states = [SupplyPortState("OFF", name=name)]
        assert widget.validate() is not None, repr(name)


def test_c_the_same_name_on_two_different_supplies_is_accepted(qtbot):
    """Uniqueness is per supply. Two independent widgets, each with its
    own "ON", both validate -- there is no global registry to collide
    in."""
    for _ in range(2):
        widget = _widget_with_states(qtbot, [SupplyPortState("FULL_ON", "1.2", name="ON")])
        assert widget.validate() is None


def test_the_name_column_leads_and_round_trips(qtbot):
    widget = _widget_with_states(qtbot, [SupplyPortState("FULL_ON", "1.2", name="ON")])
    assert widget.table.horizontalHeaderItem(COL_NAME).text() == "Name"
    assert widget.table.cellWidget(0, COL_NAME).text() == "ON"
    assert widget.states[0].name == "ON"
