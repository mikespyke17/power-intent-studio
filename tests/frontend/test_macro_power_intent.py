"""Attach an existing UPF or CPF power intent file to a specific macro
instance (explicit request) -- macro_power_intent_overlay.py,
power_intent_diagram_view.py's "Load Power Intent"/"Edit Power
Intent"/"Remove Power Intent" context-menu actions, and the "Load Upf"
UPF/CPF export section (power_intent_text_export.py). Diagram-only for
this pass, same "mock-first" scoping decision as every other Milestone 3
construct in this app.
"""

import gc
import os

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QFileDialog, QMenu

from frontend.src.app.main_window import MainWindow
from frontend.src.app.project_file import ProjectData, deserialize_project, serialize_project
from frontend.src.power_intent_view.isolation_overlay import IsolationDiagramState
from frontend.src.power_intent_view.level_shifter_overlay import LevelShifterDiagramState
from frontend.src.power_intent_view.macro_power_intent_overlay import (
    MacroPowerIntentBadgeItem,
    MacroPowerIntentDiagramState,
    attached_file_format,
    copy_macro_power_intent_diagram_state,
    macro_power_intent_badge_text,
    relative_or_absolute_path,
    restore_macro_power_intent_diagram_state,
)
from frontend.src.power_intent_view.port_attribute_overlay import PortAttributeDiagramState
from frontend.src.power_intent_view.power_domain_overlay import DomainInfoBoxItem, PowerDomainDiagramState, PowerDomainPlacement
from frontend.src.power_intent_view.power_intent_tab import PowerIntentTab
from frontend.src.power_intent_view.power_intent_text_export import generate_cpf, generate_upf, macro_power_intent_cpf_warnings
from frontend.src.power_intent_view.power_state_table import PowerStateTableDiagramState
from frontend.src.power_intent_view.power_switch_overlay import PowerSwitchDiagramState
from frontend.src.power_intent_view.retention_overlay import RetentionDiagramState
from frontend.src.power_intent_view.supply_port_overlay import SupplyPortDiagramState
from frontend.src.shared.mock_backend import build_mock_design
from frontend.src.shared.models.rtl import Definition, Design, Instance, PortConnection, SourceLanguage


def _two_macro_design() -> Design:
    """build_mock_design() only ever has ONE macro (top/u_ram) -- a
    hand-built fixture with two, for the multi-select-2-macros tests
    below (same "hand-built, since the standard mock only nests/varies
    so much" precedent already established elsewhere in this suite,
    e.g. the 3-level nested-block fixture noted for Supply Port hover
    tracking)."""
    macro = Definition(definition_id="ram_macro", base_name="RAM_MACRO", language=SourceLanguage.LIBERTY, is_black_box=True)
    core = Definition(definition_id="core", base_name="core", language=SourceLanguage.VERILOG)
    top = Definition(
        definition_id="top",
        base_name="top",
        language=SourceLanguage.VERILOG,
        instances=[
            Instance(name="u_ram1", definition_id="ram_macro", connections=[]),
            Instance(name="u_ram2", definition_id="ram_macro", connections=[]),
            Instance(name="u_core", definition_id="core", connections=[]),
        ],
    )
    return Design(definitions={"top": top, "ram_macro": macro, "core": core}, top="top")


def _make_tab(qtbot, design=None) -> PowerIntentTab:
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(design or build_mock_design())
    return tab


# --- MacroPowerIntentDiagramState / relative_or_absolute_path / attached_file_format ---


def test_attached_file_format_infers_from_extension():
    assert attached_file_format("/a/b/x.upf") == "upf"
    assert attached_file_format("/a/b/x.CPF") == "cpf"
    assert attached_file_format("/a/b/x.unknown") == "upf"  # same fallback as _infer_source_format()


def test_relative_or_absolute_path_prefers_relative():
    assert relative_or_absolute_path("/a/b/c/file.upf", "/a/b") == os.path.join("c", "file.upf")


def test_relative_or_absolute_path_falls_back_to_absolute_on_windows_cross_drive(monkeypatch):
    def _raise(*a, **k):
        raise ValueError("no relative path")

    monkeypatch.setattr(os.path, "relpath", _raise)
    assert relative_or_absolute_path("C:/a/file.upf", "D:/b") == "C:/a/file.upf"


def test_copy_and_restore_are_independent_snapshots():
    state = MacroPowerIntentDiagramState(attachments={"top/u_ram": "/x.upf"})
    snapshot = copy_macro_power_intent_diagram_state(state)
    state.attachments["top/u_ram"] = "/y.upf"
    assert snapshot.attachments["top/u_ram"] == "/x.upf"

    restore_macro_power_intent_diagram_state(state, snapshot)
    assert state.attachments["top/u_ram"] == "/x.upf"


# --- validation --------------------------------------------------------


def test_validate_macro_power_intent_target_rejects_a_non_macro(qtbot):
    tab = _make_tab(qtbot)
    view = tab.diagram._view
    assert view._validate_macro_power_intent_target(view._boxes["top/u_core"]) == view._NOT_A_MACRO_POWER_INTENT_ERROR
    assert view._validate_macro_power_intent_target(view._boxes["top/u_ram"]) is None


# --- directly-testable core: attach/remove ------------------------------


def test_attach_macro_power_intent_stores_the_absolute_path_verbatim(qtbot):
    tab = _make_tab(qtbot)
    view = tab.diagram._view

    view.attach_macro_power_intent(["top/u_ram"], "/abs/path/to/macro.upf")

    assert view._macro_power_intent_state.attachments == {"top/u_ram": "/abs/path/to/macro.upf"}


def test_attach_macro_power_intent_applies_the_same_file_to_every_target(qtbot):
    tab = _make_tab(qtbot, _two_macro_design())
    view = tab.diagram._view

    view.attach_macro_power_intent(["top/u_ram1", "top/u_ram2"], "/shared.cpf")

    assert view._macro_power_intent_state.attachments == {
        "top/u_ram1": "/shared.cpf",
        "top/u_ram2": "/shared.cpf",
    }


def test_remove_macro_power_intent_deletes_the_attachment_with_no_confirmation(qtbot):
    tab = _make_tab(qtbot)
    view = tab.diagram._view
    view.attach_macro_power_intent(["top/u_ram"], "/x.upf")

    view.remove_macro_power_intent(["top/u_ram"])

    assert view._macro_power_intent_state.attachments == {}


def test_remove_macro_power_intent_on_a_path_with_no_attachment_is_a_safe_noop(qtbot):
    tab = _make_tab(qtbot)
    view = tab.diagram._view
    view.remove_macro_power_intent(["top/u_ram"])
    assert view._macro_power_intent_state.attachments == {}


def test_attach_and_remove_are_undoable(qtbot):
    tab = _make_tab(qtbot)
    view = tab.diagram._view

    view.attach_macro_power_intent(["top/u_ram"], "/x.upf")
    assert view._macro_power_intent_state.attachments == {"top/u_ram": "/x.upf"}

    view._undo_stack.undo()
    assert view._macro_power_intent_state.attachments == {}

    view._undo_stack.redo()
    assert view._macro_power_intent_state.attachments == {"top/u_ram": "/x.upf"}

    view.remove_macro_power_intent(["top/u_ram"])
    assert view._macro_power_intent_state.attachments == {}
    view._undo_stack.undo()
    assert view._macro_power_intent_state.attachments == {"top/u_ram": "/x.upf"}


def test_attaching_with_no_real_change_pushes_no_undo_step(qtbot):
    """Same "same value -> no-op undo step" discipline
    _commit_macro_power_intent_undo_step() documents -- attaching the
    exact same file a macro already has shouldn't grow the undo stack."""
    tab = _make_tab(qtbot)
    view = tab.diagram._view
    view.attach_macro_power_intent(["top/u_ram"], "/x.upf")
    count_before = view._undo_stack.count()

    view.attach_macro_power_intent(["top/u_ram"], "/x.upf")

    assert view._undo_stack.count() == count_before


# --- context menu: single target ----------------------------------------


def test_menu_offers_load_when_not_yet_attached_and_disables_edit_remove(qtbot):
    tab = _make_tab(qtbot)
    view = tab.diagram._view
    menu = QMenu()
    actions = dict(view._extra_context_menu_actions(menu, view._boxes["top/u_ram"]))
    load = next(a for a in actions if a.text() == "Load Power Intent")
    edit = next(a for a in actions if a.text() == "Edit Power Intent")
    remove = next(a for a in actions if a.text() == "Remove Power Intent")

    assert load.isEnabled() is True
    assert edit.isEnabled() is False
    assert remove.isEnabled() is False


def test_menu_offers_edit_remove_once_attached_and_disables_load(qtbot):
    tab = _make_tab(qtbot)
    view = tab.diagram._view
    view.attach_macro_power_intent(["top/u_ram"], "/x.upf")
    menu = QMenu()
    actions = dict(view._extra_context_menu_actions(menu, view._boxes["top/u_ram"]))
    load = next(a for a in actions if a.text() == "Load Power Intent")
    edit = next(a for a in actions if a.text() == "Edit Power Intent")
    remove = next(a for a in actions if a.text() == "Remove Power Intent")

    assert load.isEnabled() is False
    assert edit.isEnabled() is True
    assert remove.isEnabled() is True


def test_menu_disables_all_three_on_a_non_macro_target(qtbot):
    tab = _make_tab(qtbot)
    view = tab.diagram._view
    menu = QMenu()
    actions = dict(view._extra_context_menu_actions(menu, view._boxes["top/u_core"]))
    load = next(a for a in actions if a.text() == "Load Power Intent")
    edit = next(a for a in actions if a.text() == "Edit Power Intent")
    remove = next(a for a in actions if a.text() == "Remove Power Intent")
    assert load.isEnabled() is False
    assert edit.isEnabled() is False
    assert remove.isEnabled() is False
    # No explanatory tooltip on a single-target disabled action -- same
    # convention every other single-target action in this menu already
    # follows (Add Supply/Add Retention/etc. are silently greyed out too;
    # only the two MULTI-select block-reasons get a tooltip, see
    # create_domain_action's own).


def test_clicking_load_power_intent_opens_a_file_picker_and_attaches_the_pick(qtbot, monkeypatch):
    tab = _make_tab(qtbot)
    view = tab.diagram._view
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: ("/picked.upf", "")))

    menu = QMenu()
    actions = dict(view._extra_context_menu_actions(menu, view._boxes["top/u_ram"]))
    load = next(a for a in actions if a.text() == "Load Power Intent")
    callback = actions[load]
    callback()

    assert view._macro_power_intent_state.attachments == {"top/u_ram": "/picked.upf"}


def test_cancelling_the_file_picker_leaves_the_attachment_unchanged(qtbot, monkeypatch):
    tab = _make_tab(qtbot)
    view = tab.diagram._view
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: ("", "")))

    menu = QMenu()
    actions = dict(view._extra_context_menu_actions(menu, view._boxes["top/u_ram"]))
    load = next(a for a in actions if a.text() == "Load Power Intent")
    actions[load]()

    assert view._macro_power_intent_state.attachments == {}


# --- context menu: multi-select -----------------------------------------


def _multi_select(view, paths) -> None:
    for path in paths:
        view._boxes[path].setSelected(True)


def _deselect_and_collect(view) -> None:
    """Same real, pre-existing Qt-teardown-ordering hazard
    test_multi_hierarchy_power_domain.py's own `_deselect_and_collect()`
    already documents: a selected InstanceBoxItem left selected when its
    owning widget is torn down at test end can fire a selection-changed
    signal against an already-deleted C++ object (a noisy but harmless
    shiboken RuntimeError at teardown) -- clearing selection before the
    test ends avoids it."""
    if view.scene() is not None:
        view.scene().clearSelection()
    gc.collect()


def test_multi_select_of_two_unattached_macros_offers_load_and_attaches_both(qtbot, monkeypatch):
    tab = _make_tab(qtbot, _two_macro_design())
    view = tab.diagram._view
    _multi_select(view, ["top/u_ram1", "top/u_ram2"])
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: ("/shared.upf", "")))

    menu = QMenu()
    actions = dict(view._extra_context_menu_actions(menu, view._boxes["top/u_ram1"]))
    load = next(a for a in actions if a.text() == "Load Power Intent")
    assert load.isEnabled() is True
    actions[load]()

    assert view._macro_power_intent_state.attachments == {
        "top/u_ram1": "/shared.upf",
        "top/u_ram2": "/shared.upf",
    }
    _deselect_and_collect(view)


def test_multi_select_mixing_a_macro_and_a_non_macro_disables_all_three(qtbot):
    tab = _make_tab(qtbot, _two_macro_design())
    view = tab.diagram._view
    _multi_select(view, ["top/u_ram1", "top/u_core"])

    menu = QMenu()
    actions = dict(view._extra_context_menu_actions(menu, view._boxes["top/u_ram1"]))
    load = next(a for a in actions if a.text() == "Load Power Intent")
    edit = next(a for a in actions if a.text() == "Edit Power Intent")
    remove = next(a for a in actions if a.text() == "Remove Power Intent")
    assert (load.isEnabled(), edit.isEnabled(), remove.isEnabled()) == (False, False, False)
    assert load.toolTip() == view._NOT_A_MACRO_POWER_INTENT_ERROR
    _deselect_and_collect(view)


def test_multi_select_mixing_attached_and_unattached_macros_disables_all_three(qtbot):
    tab = _make_tab(qtbot, _two_macro_design())
    view = tab.diagram._view
    view.attach_macro_power_intent(["top/u_ram1"], "/only_one.upf")
    _multi_select(view, ["top/u_ram1", "top/u_ram2"])

    menu = QMenu()
    actions = dict(view._extra_context_menu_actions(menu, view._boxes["top/u_ram1"]))
    load = next(a for a in actions if a.text() == "Load Power Intent")
    edit = next(a for a in actions if a.text() == "Edit Power Intent")
    remove = next(a for a in actions if a.text() == "Remove Power Intent")
    assert (load.isEnabled(), edit.isEnabled(), remove.isEnabled()) == (False, False, False)
    assert load.toolTip() == view._MIXED_MACRO_POWER_INTENT_SELECTION_ERROR
    _deselect_and_collect(view)


# --- UPF/CPF generation --------------------------------------------------


def _empty_states():
    return dict(
        supply_state=SupplyPortDiagramState(),
        domain_state=PowerDomainDiagramState(),
        retention_state=RetentionDiagramState(),
        power_switch_state=PowerSwitchDiagramState(),
        level_shifter_state=LevelShifterDiagramState(),
    )


def test_upf_export_emits_load_upf_for_a_single_attached_macro(tmp_path):
    macro_state = MacroPowerIntentDiagramState(attachments={"top/u_ram": str(tmp_path / "sub" / "macro.upf")})
    text = generate_upf(**_empty_states(), version="3.1", macro_power_intent_state=macro_state, output_dir=str(tmp_path))

    assert "# Load Upf" in text
    assert f"load_upf -file {os.path.join('sub', 'macro.upf')} -instance u_ram" in text


def test_upf_export_falls_back_to_absolute_path_with_no_output_dir():
    macro_state = MacroPowerIntentDiagramState(attachments={"top/u_ram": "/abs/macro.upf"})
    text = generate_upf(**_empty_states(), version="3.1", macro_power_intent_state=macro_state)

    assert "load_upf -file /abs/macro.upf -instance u_ram" in text


def test_upf_export_emits_one_line_per_macro_sorted_by_instance_path(tmp_path):
    macro_state = MacroPowerIntentDiagramState(
        attachments={
            "top/u_ram2": str(tmp_path / "b.upf"),
            "top/u_ram1": str(tmp_path / "a.upf"),
        }
    )
    text = generate_upf(**_empty_states(), version="3.1", macro_power_intent_state=macro_state, output_dir=str(tmp_path))

    lines = [l for l in text.splitlines() if l.startswith("load_upf")]
    assert lines == [
        "load_upf -file a.upf -instance u_ram1",
        "load_upf -file b.upf -instance u_ram2",
    ]


def test_upf_export_omits_the_load_upf_section_with_no_attachments():
    text = generate_upf(**_empty_states(), version="3.1")
    assert "Load Upf" not in text


def test_cpf_export_never_emits_a_real_command_only_a_comment(tmp_path):
    macro_state = MacroPowerIntentDiagramState(attachments={"top/u_ram": str(tmp_path / "macro.upf")})
    text = generate_cpf(**_empty_states(), version="2.0", macro_power_intent_state=macro_state)

    assert "# Load Upf" in text
    # Never a REAL command -- every line mentioning load_upf/the macro's
    # own attachment is itself a '#' comment, not a bare command line.
    for line in text.splitlines():
        if "load_upf" in line or "u_ram" in line or "macro.upf" in line:
            assert line.strip().startswith("#"), line


def test_cpf_export_comment_covers_both_upf_and_cpf_attached_files(tmp_path):
    macro_state = MacroPowerIntentDiagramState(
        attachments={
            "top/u_ram1": str(tmp_path / "a.upf"),
            "top/u_ram2": str(tmp_path / "b.cpf"),
        }
    )
    text = generate_cpf(**_empty_states(), version="2.0", macro_power_intent_state=macro_state)
    assert "a.upf" in text and "(UPF)" in text
    assert "b.cpf" in text and "(CPF)" in text


def test_cpf_export_omits_the_section_with_no_attachments():
    text = generate_cpf(**_empty_states(), version="2.0")
    assert "Load Upf" not in text


def test_macro_power_intent_cpf_warnings_covers_every_attachment_regardless_of_source_format(tmp_path):
    """The gap is broader than the originally-named UPF-attached/CPF-
    target case -- confirmed by research (see power_intent_text_export.py's
    own comment): CPF has no in-language file-loading construct AT ALL,
    so a CPF-attached file hits the identical gap too."""
    macro_state = MacroPowerIntentDiagramState(
        attachments={
            "top/u_ram1": str(tmp_path / "a.upf"),
            "top/u_ram2": str(tmp_path / "b.cpf"),
        }
    )
    warnings = macro_power_intent_cpf_warnings(macro_state)
    assert len(warnings) == 2
    assert any("u_ram1" in w and "UPF" in w for w in warnings)
    assert any("u_ram2" in w and "CPF" in w for w in warnings)


def test_macro_power_intent_cpf_warnings_empty_with_no_attachments():
    assert macro_power_intent_cpf_warnings(MacroPowerIntentDiagramState()) == []
    assert macro_power_intent_cpf_warnings(None) == []


# --- project save/load ----------------------------------------------------


def _project_data(macro_power_intent_state=None) -> ProjectData:
    design = build_mock_design()
    return ProjectData(
        project_name="P",
        rtl_design=design,
        supply_state=SupplyPortDiagramState(),
        domain_state=PowerDomainDiagramState(),
        retention_state=RetentionDiagramState(),
        port_attribute_state=PortAttributeDiagramState(),
        power_switch_state=PowerSwitchDiagramState(),
        isolation_state=IsolationDiagramState(),
        level_shifter_state=LevelShifterDiagramState(),
        power_state_table_state=PowerStateTableDiagramState(),
        macro_power_intent_state=macro_power_intent_state or MacroPowerIntentDiagramState(),
    )


def test_project_round_trip_preserves_macro_power_intent_attachments():
    data = _project_data(MacroPowerIntentDiagramState(attachments={"top/u_ram": "/x.upf"}))
    blob = serialize_project(data)
    restored = deserialize_project(blob)
    assert restored.macro_power_intent_state.attachments == {"top/u_ram": "/x.upf"}


def test_loading_a_pre_existing_pis_file_with_no_macro_power_intent_section_defaults_to_empty():
    """A .pis file saved before this feature existed has no
    'macro_power_intent_state' key at all -- must still load cleanly as
    'no attachments', not raise (see ProjectData's own field comment)."""
    import json

    data = _project_data()
    blob = serialize_project(data)
    import gzip

    payload = json.loads(gzip.decompress(blob))
    del payload["macro_power_intent_state"]
    old_blob = gzip.compress(json.dumps(payload).encode("utf-8"))

    restored = deserialize_project(old_blob)
    assert restored.macro_power_intent_state.attachments == {}


def test_load_project_restores_attachments_into_the_live_diagram_state(qtbot):
    tab = _make_tab(qtbot)
    macro_state = MacroPowerIntentDiagramState(attachments={"top/u_ram": "/loaded.upf"})

    tab.load_project(
        build_mock_design(),
        SupplyPortDiagramState(),
        PowerDomainDiagramState(),
        RetentionDiagramState(),
        PortAttributeDiagramState(),
        PowerSwitchDiagramState(),
        IsolationDiagramState(),
        LevelShifterDiagramState(),
        macro_state,
    )

    assert tab.diagram.shared_macro_power_intent_state.attachments == {"top/u_ram": "/loaded.upf"}


def test_load_project_with_no_macro_power_intent_argument_defaults_to_empty(qtbot):
    """Backward-compatible default (explicit request, param added after
    this method already had real callers) -- an old call site that
    doesn't pass the new argument at all still works."""
    tab = _make_tab(qtbot)
    tab.load_project(
        build_mock_design(),
        SupplyPortDiagramState(),
        PowerDomainDiagramState(),
        RetentionDiagramState(),
        PortAttributeDiagramState(),
        PowerSwitchDiagramState(),
        IsolationDiagramState(),
        LevelShifterDiagramState(),
    )
    assert tab.diagram.shared_macro_power_intent_state.attachments == {}


# --- end-to-end through MainWindow's real "Extract Power Intent" --------


def test_extract_power_intent_end_to_end_writes_load_upf_line(qtbot, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QInputDialog

    window = MainWindow()
    qtbot.addWidget(window)
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("Test Project", True)))
    window.create_project_action.trigger()
    window.power_intent_tab.set_rtl(build_mock_design())

    macro_file = tmp_path / "attached" / "macro.upf"
    window.power_intent_tab.diagram.shared_macro_power_intent_state.attachments["top/u_ram"] = str(macro_file)

    out_file = tmp_path / "out.upf"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(out_file), "UPF Files (*.upf)")))
    window._on_extract_power_intent("UPF", "3.1")

    text = out_file.read_text()
    assert f"load_upf -file {os.path.join('attached', 'macro.upf')} -instance u_ram" in text


def test_extract_power_intent_to_cpf_warns_about_unexpressible_attachments(qtbot, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QInputDialog

    from frontend.src.app import main_window as main_window_module

    window = MainWindow()
    qtbot.addWidget(window)
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("Test Project", True)))
    window.create_project_action.trigger()
    window.power_intent_tab.set_rtl(build_mock_design())
    window.power_intent_tab.diagram.shared_macro_power_intent_state.attachments["top/u_ram"] = str(tmp_path / "macro.upf")

    out_file = tmp_path / "out.cpf"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(out_file), "CPF Files (*.cpf)")))
    # message_box.warning() constructs a raw QMessageBox and calls
    # .exec() itself (see message_box.py's own _show()) rather than
    # going through QMessageBox's static convenience method -- a real
    # .exec() blocks headlessly waiting for a click that can never come
    # (same real, confirmed hazard already documented for
    # QMessageBox.warning() elsewhere in this suite), so the module-level
    # function main_window.py actually calls is mocked directly instead.
    warned = []
    monkeypatch.setattr(main_window_module.message_box, "warning", lambda *a, **k: warned.append(a))
    window._on_extract_power_intent("CPF", "2.0")

    assert out_file.exists()
    assert warned  # a generation-time diagnostic was shown


# --- diagram label: "[UPF]"/"[CPF]" badge at the macro's own bottom-right corner ---


def _badge(view, path: str) -> MacroPowerIntentBadgeItem:
    return view._macro_power_intent_badges[path]


def test_attaching_a_upf_file_shows_the_upf_badge(qtbot):
    tab = _make_tab(qtbot)
    view = tab.diagram._view

    view.attach_macro_power_intent(["top/u_ram"], "/x.upf")

    assert "top/u_ram" in view._macro_power_intent_badges
    badge = _badge(view, "top/u_ram")
    assert badge._text == "[UPF]"
    assert badge.owner_box is view._boxes["top/u_ram"]
    assert badge.parentItem() is view._boxes["top/u_ram"]  # a real child of the macro's own box


def test_attaching_a_cpf_file_shows_the_cpf_badge(qtbot):
    tab = _make_tab(qtbot)
    view = tab.diagram._view

    view.attach_macro_power_intent(["top/u_ram"], "/x.cpf")

    badge = _badge(view, "top/u_ram")
    assert badge._text == "[CPF]"


def test_macro_power_intent_badge_text_matches_the_generic_helper():
    assert macro_power_intent_badge_text("/x.upf") == "[UPF]"
    assert macro_power_intent_badge_text("/x.CPF") == "[CPF]"


def test_removing_the_attachment_removes_the_badge_entirely(qtbot):
    tab = _make_tab(qtbot)
    view = tab.diagram._view
    view.attach_macro_power_intent(["top/u_ram"], "/x.upf")
    box = view._boxes["top/u_ram"]
    badge = _badge(view, "top/u_ram")
    assert badge in box.childItems()

    view.remove_macro_power_intent(["top/u_ram"])

    assert "top/u_ram" not in view._macro_power_intent_badges
    assert badge not in box.childItems()
    assert badge.scene() is None


def test_removing_via_undo_also_removes_the_badge(qtbot):
    """"...or if the attachment is cleared through any other means" --
    Ctrl+Z is the other real path a live attachment can vanish through,
    not just an explicit "Remove Power Intent" click."""
    tab = _make_tab(qtbot)
    view = tab.diagram._view
    view.attach_macro_power_intent(["top/u_ram"], "/x.upf")
    assert "top/u_ram" in view._macro_power_intent_badges

    view._undo_stack.undo()

    assert "top/u_ram" not in view._macro_power_intent_badges
    assert not any(isinstance(child, MacroPowerIntentBadgeItem) for child in view._boxes["top/u_ram"].childItems())


def test_replacing_a_upf_attachment_with_cpf_updates_the_badge_live(qtbot):
    tab = _make_tab(qtbot)
    view = tab.diagram._view
    view.attach_macro_power_intent(["top/u_ram"], "/x.upf")
    old_badge = _badge(view, "top/u_ram")
    assert old_badge._text == "[UPF]"

    view.attach_macro_power_intent(["top/u_ram"], "/y.cpf")  # "Edit Power Intent" -- same directly-testable core

    new_badge = _badge(view, "top/u_ram")
    assert new_badge._text == "[CPF]"
    # The stale UPF-format badge item is genuinely gone, not just
    # shadowed by a second one -- exactly one MacroPowerIntentBadgeItem
    # child remains on the box.
    box = view._boxes["top/u_ram"]
    badge_children = [c for c in box.childItems() if isinstance(c, MacroPowerIntentBadgeItem)]
    assert badge_children == [new_badge]
    assert old_badge.scene() is None


def test_the_block_grows_to_fit_the_badge_without_clipping(qtbot):
    """Same "make room" layout family every other insertion-resize test
    in this suite verifies (test_block_expansion_on_insertion.py) --
    the box's own rect must be tall enough that the badge's own
    bottom-right-anchored bounding rect stays fully inside it."""
    tab = _make_tab(qtbot)
    view = tab.diagram._view
    box = view._boxes["top/u_ram"]
    height_before = box.rect().height()

    view.attach_macro_power_intent(["top/u_ram"], "/x.upf")

    badge = _badge(view, "top/u_ram")
    assert box.rect().height() > height_before  # the block actually grew
    badge_rect_in_box = box.mapRectFromItem(badge, badge.boundingRect())
    assert box.rect().contains(badge_rect_in_box)


def test_a_macro_with_both_the_badge_and_a_domain_info_box_shows_both_without_overlap(qtbot):
    """Scenario (e): reuses the SAME bottom-right-corner collision-
    avoidance mechanism DomainInfoBoxItem/ground-anchor already
    established (generalized, not rebuilt -- see MacroPowerIntentBadgeItem's
    own docstring). A macro can never OWN a Power Domain through the
    normal UI flow (create_power_domain()'s own menu gating rejects it),
    but the underlying diagram state has no such restriction -- the
    layout math itself must stay correct regardless of how that state
    came to exist, so this constructs it directly."""
    tab = _make_tab(qtbot)
    view = tab.diagram._view
    view.attach_macro_power_intent(["top/u_ram"], "/x.upf")
    view._domain_state.domains["d1"] = PowerDomainPlacement(
        id="d1", owner_path="top/u_ram", name="PD_ON_A_MACRO", color=QColor("#22aa22"), transparency=0.4
    )
    view.refresh()  # rebuilds both overlays fresh against the now-combined state

    box = view._boxes["top/u_ram"]
    badge = next(c for c in box.childItems() if isinstance(c, MacroPowerIntentBadgeItem))
    domain_box = next(c for c in box.childItems() if isinstance(c, DomainInfoBoxItem))

    badge_rect = box.mapRectFromItem(badge, badge.boundingRect())
    domain_rect = box.mapRectFromItem(domain_box, domain_box.boundingRect())
    assert not badge_rect.intersects(domain_rect)
    # Both fully visible (within the now-expanded block), not just
    # non-overlapping somewhere off in space.
    assert box.rect().contains(badge_rect)
    assert box.rect().contains(domain_rect)
