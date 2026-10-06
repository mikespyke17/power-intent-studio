"""PERMANENT regression suite for a batch of dialog usability fixes:

- Bug 4: the "Add New" Supply Port dialog reached via Create Power
  Domain -> Create Supply Set -> Add New must render at the SAME size
  as the standalone "Add Power Supply"/"Add Internal Supply" dialogs --
  root-caused to setMinimumWidth(420) being gated on `not
  is_internal_supply`, so every Internal Supply instance of this
  dialog (standalone OR nested -- it's the exact same AddPowerSupplyDialog
  class either way, confirmed via a direct read of _SupplyNameCombo's
  own "Add New..." handler) never got the width floor at all.
- Bug 5: every creation/edit dialog in this app must be user-resizable,
  with an explicit, testable setSizeGripEnabled(True) -- none of them
  ever set a fixed-size constraint, so they were already technically
  resizable via window-manager edge-dragging, but this locks in an
  explicit, assertable guarantee.
- Bug 6: every error/validation/confirmation message shown via
  QMessageBox.warning()/question()/critical() must have its text
  mouse-selectable/copyable -- QMessageBox's own static convenience
  methods can't support this (no way to reach into the instance they
  construct internally), so every call site across power_intent_view/
  was moved to frontend.src.shared.widgets.message_box's own
  warning()/question()/critical() wrappers, which construct a real
  QMessageBox instance with setTextInteractionFlags() set before
  exec()."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from frontend.src.shared.widgets import message_box
from frontend.src.power_intent_view.add_power_supply_dialog import AddPowerSupplyDialog
from frontend.src.power_intent_view.create_power_domain_dialog import CreatePowerDomainDialog, CreateSupplySetDialog
from frontend.src.power_intent_view.isolation_dialog import IsolationDialog, IsolationInfoDialog
from frontend.src.power_intent_view.level_shifter_dialog import LevelShifterDialog, LevelShifterInfoDialog
from frontend.src.power_intent_view.port_attribute_dialog import PortAttributeDialog
from frontend.src.power_intent_view.power_switch_dialog import PowerSwitchDialog
from frontend.src.power_intent_view.retention_dialog import RetentionStrategyDialog, RetentionStrategyInfoDialog
from frontend.src.power_intent_view.source_viewer_dialog import SourceViewerDialog


# --- Bug 4: nested "Add New" sized the same as standalone -------------------


def test_internal_supply_dialog_gets_the_same_minimum_width_as_supply_port(qtbot):
    """Root-cause regression: an Internal Supply instance of
    AddPowerSupplyDialog (standalone OR nested "Add New...") must get
    AT LEAST the same 420px floor a plain Supply Port instance already
    gets -- previously gated on `not is_internal_supply`, so it was
    silently skipped for every Internal Supply case.

    Follow-up fix (explicit report, "table clipped on the right / '+'
    button missing" on some font/platform): the floor is no longer a
    flat `== 420` on either instance -- it's now `max(420,
    self.sizeHint().width())`, computed fresh from each dialog's OWN
    real content (see AddPowerSupplyDialog's own __init__ docstring
    comment), so it can legitimately grow past 420 on a font/platform
    where the label column or the Power States table need more. The
    real, permanent guarantee this test locks in is the FLOOR itself
    (never below 420) -- not an exact number that was always really
    just an artifact of one specific font/platform's own measurements."""
    supply_port_dialog = AddPowerSupplyDialog(target_path="top", is_internal_supply=False)
    qtbot.addWidget(supply_port_dialog)
    internal_supply_dialog = AddPowerSupplyDialog(target_path="top/u_core", is_internal_supply=True)
    qtbot.addWidget(internal_supply_dialog)

    assert supply_port_dialog.minimumWidth() >= 420
    assert internal_supply_dialog.minimumWidth() >= 420


def test_nested_add_new_style_call_gets_the_same_width_as_the_standalone_dialog(qtbot):
    """The nested "Add New..." entry point (Create Power Domain ->
    Create Supply Set -> Add New) constructs AddPowerSupplyDialog with
    no `target_path` at all (see _SupplyNameCombo._on_index_changed())
    -- confirms that specific call shape still gets AT LEAST the same
    width floor, for both the Supply Port and Internal Supply case (see
    the sibling test above for why this is now a floor, not an exact
    `== 420`, since the follow-up width fix)."""
    nested_style_port = AddPowerSupplyDialog(is_internal_supply=False)
    qtbot.addWidget(nested_style_port)
    nested_style_internal = AddPowerSupplyDialog(is_internal_supply=True)
    qtbot.addWidget(nested_style_internal)

    assert nested_style_port.minimumWidth() >= 420
    assert nested_style_internal.minimumWidth() >= 420


# --- Bug 7: same dialog-sizing family -- Add Internal Supply's own
# "Inherited Power States" text overlapping "Colour" (a HEIGHT problem),
# and Add Power Supply's own Power States table/"+" button clipped on the
# right (a WIDTH problem). Both root-caused to the SAME general "Qt's own
# iterative layout resolution can't be trusted to size a QFormLayout row
# for variable/word-wrapped content" class already proven real for
# states_widget's own height (see AddPowerSupplyDialog's own module
# docstring, "round one"/"round two") -- see its __init__ for the two
# follow-up fixes: an explicit adjustSize()+heightForWidth() pin for the
# "Inherited Power States" label (mirroring the fix states_widget already
# had), and a minimum width derived from the dialog's own freshly-built
# sizeHint() (captured once, right after every row is in place) instead of
# a flat, unaccounted-for guess.


def test_add_internal_supply_via_the_nested_add_new_flow_does_not_overlap_colour(qtbot, monkeypatch):
    """Repro (explicit report): Create Power Domain -> Create Supply Set
    -> Power -> Add New, for an INNER hierarchy (an Internal Supply, no
    existing connection -- the placeholder "Not yet connected..." text).
    Drives the REAL nested "Add New..." gesture (same fidelity level
    test_power_domain.py's own Bug-4-adjacent "Add New" tests already
    use) rather than constructing AddPowerSupplyDialog directly, so this
    covers the actual construction call shape _SupplyNameCombo uses (no
    `inherited_state_lines` passed at all -- always the placeholder text
    for a brand-new, just-being-created Internal Supply)."""
    captured = {}

    def fake_exec(self):
        self.show()
        captured["dialog"] = self
        self.hide()
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(AddPowerSupplyDialog, "exec", fake_exec)
    combo_dialog = CreateSupplySetDialog([], is_internal_supply=True, target_path="top/u_core")
    qtbot.addWidget(combo_dialog)

    index = combo_dialog.power_combo.findText("Add New...")
    combo_dialog.power_combo._on_index_changed(index)  # direct call -- see test_power_domain.py's own note on signal-driven monkeypatching

    dialog = captured["dialog"]
    label = dialog.inherited_states_label
    assert label is not None
    assert label.text() == "Inherited Power States: Not yet connected -- states will inherit once linked to a supply port."

    # No overlap between the "Colour" row and the "Inherited Power
    # States" row.
    assert not label.geometry().intersects(dialog.color_button.geometry())
    # The label's own real, final allocated height must be enough to
    # show its own full text with no clipping -- computed independently
    # here via heightForWidth() against the label's own real (post-
    # layout) width, not by re-invoking the same fixed-height value the
    # fix itself already pinned (that would just test the fix against a
    # second copy of itself).
    assert label.height() >= label.heightForWidth(label.width())
    # The label must fit fully within the dialog's own client area, not
    # spill past its bottom edge.
    assert label.geometry().bottom() <= dialog.height()
    # Structural guard, verified live to genuinely FAIL against the
    # pre-fix code (label.minimumHeight()==0, maximumHeight()==16777215
    # -- i.e. NOT pinned) and PASS once the fix's adjustSize()+
    # heightForWidth() pin is in place: the two headless-sandbox
    # geometry checks above happen not to diverge on this environment's
    # own fallback font/style (the same "can't force a numeric
    # divergence in this sandbox" limitation already documented
    # elsewhere in this codebase for an analogous font-metrics issue) --
    # this assertion instead locks in that the FIX MECHANISM itself is
    # actually active, independent of whether this sandbox's own font
    # happens to already render correctly without it.
    assert label.minimumHeight() == label.maximumHeight(), "label height is not pinned -- the adjustSize()+heightForWidth() fix is not active"


def test_add_internal_supply_grows_to_fit_a_longer_real_inherited_states_message(qtbot):
    """Second case (explicit request): a hierarchy that DOES have
    inherited states -- a real, non-empty, multi-line state list (the
    same call shape power_intent_diagram_view.py's own
    _prompt_edit_supply_port() uses for a real, already-connected
    Internal Supply, via `inherited_state_lines`) -- confirms the dialog
    also grows correctly to fit a LONGER message, not just the short
    placeholder text covered by the sibling test above."""
    long_inherited_lines = [
        "ON = 0.8V (from VDD)",
        "OFF (from VDD)",
        "RET = 0.6V (from VDD)",
        "STANDBY = 0.4V (from VDD)",
    ]
    dialog = AddPowerSupplyDialog(target_path="top/u_core/u_mem_ctrl", is_internal_supply=True, inherited_state_lines=long_inherited_lines)
    qtbot.addWidget(dialog)
    dialog.show()

    label = dialog.inherited_states_label
    assert label is not None
    assert label.text() == "Inherited Power States:\n" + "\n".join(long_inherited_lines)

    assert not label.geometry().intersects(dialog.color_button.geometry())
    assert label.height() >= label.heightForWidth(label.width())
    assert label.geometry().bottom() <= dialog.height()

    # The dialog must be genuinely TALLER than the short-placeholder
    # case, not just coincidentally big enough -- confirms this is a
    # real, content-driven grow, not a fixed constant that happens not
    # to clip on this specific message.
    short_dialog = AddPowerSupplyDialog(target_path="top/u_core/u_mem_ctrl", is_internal_supply=True, inherited_state_lines=None)
    qtbot.addWidget(short_dialog)
    short_dialog.show()
    assert dialog.height() > short_dialog.height()


def test_add_power_supply_via_the_nested_add_new_flow_fully_shows_the_states_table(qtbot, monkeypatch):
    """Repro (explicit report): Create Power Domain -> Create Supply Set
    -> Power -> Add New, for the TOP-LEVEL hierarchy (a plain Supply
    Port). Confirms the full Power States table -- every column (State
    Name/Type/Voltage) plus the "+" button, which sits OUTSIDE the table
    itself (see SupplyPortStatesWidget's own docstring) -- renders fully
    within the dialog's own default width, with nothing clipped or
    pushed off to the right."""
    captured = {}

    def fake_exec(self):
        self.show()
        captured["dialog"] = self
        self.hide()
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(AddPowerSupplyDialog, "exec", fake_exec)
    combo_dialog = CreateSupplySetDialog([], is_internal_supply=False, target_path="top")
    qtbot.addWidget(combo_dialog)

    index = combo_dialog.power_combo.findText("Add New...")
    combo_dialog.power_combo._on_index_changed(index)

    dialog = captured["dialog"]
    states_widget = dialog.states_widget
    assert states_widget is not None
    # Add one real row so the table's own 3 columns and the "+"/"-"
    # button column all have real content/geometry to check, not just
    # an empty 0-row table.
    states_widget._add_row()

    table = states_widget.table
    add_button = states_widget._add_button

    # Every column, and the "+" button past the table's own right edge,
    # must be fully within the dialog's own width -- none of it pushed
    # outside/clipped.
    table_right_edge = table.mapTo(dialog, table.rect().topRight()).x()
    add_button_right_edge = add_button.mapTo(dialog, add_button.rect().topRight()).x()
    assert table_right_edge <= dialog.width()
    assert add_button_right_edge <= dialog.width()
    for col in range(table.columnCount()):
        assert table.columnWidth(col) > 0, f"column {col} has zero width -- clipped out of the table entirely"

    # Structural guard, verified live to genuinely FAIL against the
    # pre-fix code (minimumWidth() stuck at the flat 420 constant while
    # sizeHint() already wanted 543 on this sandbox's own font/style)
    # and PASS once the fix derives the floor from the dialog's own
    # freshly-built sizeHint() -- same "lock in the mechanism itself"
    # reasoning as the sibling height test above, since the geometry
    # checks alone happen not to diverge on this sandbox's own fallback
    # font even pre-fix.
    assert dialog.minimumWidth() >= dialog.sizeHint().width(), "minimumWidth() does not reflect the dialog's own real content -- the sizeHint()-derived floor fix is not active"


# --- Bug 5: every creation/edit dialog is user-resizable --------------------


def test_every_creation_edit_dialog_has_an_explicit_size_grip(qtbot):
    """Explicit request: a testable, assertable signal that each dialog
    is user-resizable, not just "happens to have no fixed-size
    constraint blocking it." Constructs one instance of every listed
    dialog class with minimal/empty arguments (each constructor
    signature verified directly against its own class definition) and
    asserts isSizeGripEnabled()."""
    domain = _domain_fixture()
    resolve_connectivity = lambda elements: ({}, {})  # noqa: E731 -- minimal stand-in, never actually called here
    dialogs = [
        AddPowerSupplyDialog(target_path="top"),
        CreateSupplySetDialog([]),
        CreatePowerDomainDialog({}, [], {}, "top"),
        IsolationDialog({}, {}, {}, resolve_connectivity, {}),
        IsolationInfoDialog(_isolation_info_fixture(), "Domain", "SS_ISO", "", ""),
        LevelShifterDialog({}, domain, resolve_connectivity, {}),
        LevelShifterInfoDialog(_level_shifter_info_fixture(), "Domain", "Cell Type"),
        PortAttributeDialog([], {}, [], "top"),
        PowerSwitchDialog(["top"], "top", domain, None, {}),
        RetentionStrategyDialog(domain, {}, [], [], set(), {}),
        RetentionStrategyInfoDialog(_retention_info_fixture(), "Domain", "SS1", "SS2"),
        _source_viewer_dialog_fixture(),
    ]
    for dialog in dialogs:
        qtbot.addWidget(dialog)
        assert dialog.isSizeGripEnabled() is True, f"{type(dialog).__name__} is not explicitly resizable"


def _domain_fixture():
    from frontend.src.power_intent_view.power_domain_overlay import PowerDomainPlacement

    return PowerDomainPlacement(id="d1", owner_path="top", name="PD1", color=QColor("#ff0000"))


def _isolation_info_fixture():
    from frontend.src.power_intent_view.isolation_overlay import IsolationStrategyPlacement

    return IsolationStrategyPlacement(
        id="i1", name="ISO1", power_domain_id="d1", isolation_supply_set_id="ss1", clamp_value="low",
        location="self", isolation_control="ctrl", isolation_sense="high", elements=["top/clk"], owner_path="top",
    )


def _level_shifter_info_fixture():
    from frontend.src.power_intent_view.level_shifter_overlay import LevelShifterStrategyPlacement

    return LevelShifterStrategyPlacement(
        id="l1", name="LS1", power_domain_id="d1", applies_to="elements", rule="low_to_high",
        location="self", elements=["top/clk"], owner_path="top",
    )


def _retention_info_fixture():
    from frontend.src.power_intent_view.retention_overlay import RetentionStrategyPlacement

    return RetentionStrategyPlacement(
        id="r1", name="RET1", power_domain_id="d1", primary_supply_set_id="ss1", retention_supply_set_id="ss2",
        save_logic="save", restore_logic="restore", elements=[], owner_path="top",
    )


def _source_viewer_dialog_fixture(tmp_path_factory=None):
    import tempfile

    with tempfile.NamedTemporaryFile(mode="w", suffix=".v", delete=False) as f:
        f.write("module top; endmodule\n")
        path = f.name
    return SourceViewerDialog(path)


# --- Bug 6: message text is mouse-selectable/copyable -----------------------


def test_message_box_warning_sets_selectable_text_interaction_flags(monkeypatch):
    captured = {}

    def fake_exec(self):
        captured["flags"] = self.textInteractionFlags()
        captured["size_grip"] = self.isSizeGripEnabled()
        return QMessageBox.StandardButton.Ok

    monkeypatch.setattr(QMessageBox, "exec", fake_exec)

    message_box.warning(None, "Title", "Some error text")

    assert captured["flags"] & Qt.TextInteractionFlag.TextSelectableByMouse
    assert captured["size_grip"] is True


def test_message_box_question_sets_selectable_text_interaction_flags(monkeypatch):
    captured = {}

    def fake_exec(self):
        captured["flags"] = self.textInteractionFlags()
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "exec", fake_exec)

    result = message_box.question(None, "Title", "Some question text")

    assert captured["flags"] & Qt.TextInteractionFlag.TextSelectableByMouse
    assert result == QMessageBox.StandardButton.Yes


def test_message_box_critical_sets_selectable_text_interaction_flags(monkeypatch):
    captured = {}

    def fake_exec(self):
        captured["flags"] = self.textInteractionFlags()
        return QMessageBox.StandardButton.Ok

    monkeypatch.setattr(QMessageBox, "exec", fake_exec)

    message_box.critical(None, "Title", "Some critical text")

    assert captured["flags"] & Qt.TextInteractionFlag.TextSelectableByMouse


def test_message_box_return_value_matches_the_default_button_semantics(monkeypatch):
    """The wrapper's return value must behave identically to the static
    QMessageBox.question()'s own -- every existing `reply ==
    QMessageBox.StandardButton.X` comparison across this app depends
    on this."""
    monkeypatch.setattr(QMessageBox, "exec", lambda self: QMessageBox.StandardButton.No)

    reply = message_box.question(None, "Title", "text", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)

    assert reply == QMessageBox.StandardButton.No


# --- Audit fix (category 03): the convention, enforced structurally -------


def test_no_production_code_calls_the_static_qmessagebox_methods():
    """Every error/validation/confirmation dialog must go through
    shared/widgets/message_box.py, whose whole reason to exist is that
    QMessageBox's own static convenience methods can't produce
    selectable/copyable text (see that module's docstring).

    The health audit found six call sites still using the static
    methods -- four in main_window.py (Extract Power Intent / Save
    Project / Load Project / Extract Diagram failures, i.e. exactly the
    messages a user most wants to copy, since they embed a raw OS error
    string) and two in load_design_tab.py. Checked structurally rather
    than by review, the same discipline the backend-import boundary
    already uses, because a convention that was applied once by hand
    drifted once already.
    """
    import ast
    from pathlib import Path

    frontend_src = Path(__file__).parent.parent.parent / "frontend" / "src"
    wrapper = frontend_src / "shared" / "widgets" / "message_box.py"
    offenders = []
    for path in frontend_src.rglob("*.py"):
        if path == wrapper:  # the wrappers themselves legitimately construct one
            continue
        for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr not in ("warning", "critical", "question", "information"):
                continue
            owner = node.func.value
            if isinstance(owner, ast.Name) and owner.id == "QMessageBox":
                offenders.append(f"{path.relative_to(frontend_src)}:{node.lineno} QMessageBox.{node.func.attr}()")
    assert offenders == [], "use shared/widgets/message_box.py instead:\n" + "\n".join(offenders)
