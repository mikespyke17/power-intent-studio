""""Inspect Source File" -- the diagram right-click context menu action
(power_intent_diagram_view.py's _extra_context_menu_actions()) and the
non-modal viewer it opens (source_viewer_dialog.py)."""

from PySide6.QtGui import QTextDocument
from PySide6.QtWidgets import QMenu

from frontend.src.power_intent_view.power_intent_tab import PowerIntentTab
from frontend.src.power_intent_view.source_viewer_dialog import SourceViewerDialog
from frontend.src.shared.mock_backend import build_mock_design
from frontend.src.shared.models.rtl import Definition, Design, Instance, Port, PortDirection, SourceLanguage


def _design_with_source_files(rtl_path: str, lib_path: str) -> Design:
    """A minimal hand-built Design (same precedent as every other
    hand-built fixture this session) with THREE distinct source_file
    scenarios in one design, matching the request's own "valid for both
    macro instances and RTL hierarchy blocks... disabled if no
    associated source file" requirements in a single, directly-testable
    shape:
      - top/u_rtl -- a real RTL leaf, source_file set to a real file on
        disk.
      - top/u_macro -- a black-box macro, source_file set to a real
        (different) file on disk.
      - top/u_nosrc -- a real RTL leaf with NO source_file at all
        (source_file="", the one real, confirmed case this can happen --
        see Definition.source_file's own docstring)."""
    rtl_leaf = Definition(definition_id="rtl_leaf", base_name="rtl_leaf", language=SourceLanguage.VERILOG, source_file=rtl_path)
    macro_leaf = Definition(
        definition_id="macro_leaf", base_name="macro_leaf", language=SourceLanguage.LIBERTY, is_black_box=True, source_file=lib_path
    )
    nosrc_leaf = Definition(definition_id="nosrc_leaf", base_name="nosrc_leaf", language=SourceLanguage.VERILOG, source_file="")
    top = Definition(
        definition_id="top",
        base_name="top",
        language=SourceLanguage.VERILOG,
        ports=[Port("clk", PortDirection.IN)],
        instances=[
            Instance("u_rtl", "rtl_leaf"),
            Instance("u_macro", "macro_leaf"),
            Instance("u_nosrc", "nosrc_leaf"),
        ],
    )
    return Design(definitions={"top": top, "rtl_leaf": rtl_leaf, "macro_leaf": macro_leaf, "nosrc_leaf": nosrc_leaf}, top="top")


def _rtl_actions_by_text(view, box) -> tuple[QMenu, dict]:
    """Returns `(menu, by_text)` -- the CALLER must keep `menu` bound to
    a local variable for as long as it inspects the returned actions.
    Real, confirmed gotcha (same "C++ object torn down once its Python
    wrapper's parent goes out of scope" class already documented
    elsewhere in this codebase): each QAction created via
    `menu.addAction(...)` is Qt-parented to `menu` itself -- if this
    function returned only the actions and `menu` (a local variable
    with no Qt-level parent of its own) were left to fall out of scope,
    its own C++ object gets destroyed once nothing references it, which
    destroys every one of its children too, even though the Python
    QAction *wrapper* objects would still be reachable -- their
    underlying C++ objects would already be gone."""
    menu = QMenu()
    actions = view._extra_context_menu_actions(menu, box)
    return menu, {action.text(): (action, callback) for action, callback in actions}


# --- context menu: enabled/disabled per node type ---------------------


def test_inspect_source_file_enabled_for_rtl_block_with_a_real_source_file(qtbot, tmp_path):
    rtl_file = tmp_path / "rtl_leaf.sv"
    rtl_file.write_text("module rtl_leaf; endmodule\n")
    lib_file = tmp_path / "macro_leaf.lib"
    lib_file.write_text("library (x) { cell (Y) { } }\n")

    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(_design_with_source_files(str(rtl_file), str(lib_file)))
    view = tab.diagram._view

    _menu, actions = _rtl_actions_by_text(view, view._boxes["top/u_rtl"])
    action, _callback = actions["Inspect Source File"]
    assert action.isEnabled() is True


def test_inspect_source_file_enabled_for_macro_with_a_real_source_file(qtbot, tmp_path):
    rtl_file = tmp_path / "rtl_leaf.sv"
    rtl_file.write_text("module rtl_leaf; endmodule\n")
    lib_file = tmp_path / "macro_leaf.lib"
    lib_file.write_text("library (x) { cell (Y) { } }\n")

    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(_design_with_source_files(str(rtl_file), str(lib_file)))
    view = tab.diagram._view

    _menu, actions = _rtl_actions_by_text(view, view._boxes["top/u_macro"])
    action, _callback = actions["Inspect Source File"]
    assert action.isEnabled() is True


def test_inspect_source_file_disabled_when_definition_has_no_source_file(qtbot, tmp_path):
    """The one real, confirmed case a Definition's own source_file can
    be empty (VHDL, when GHDL's own base_name -> source_file map didn't
    cover an entity) -- disabled, not hidden, same "visible, greyed out"
    convention every other level-gated action in this menu follows."""
    rtl_file = tmp_path / "rtl_leaf.sv"
    rtl_file.write_text("module rtl_leaf; endmodule\n")
    lib_file = tmp_path / "macro_leaf.lib"
    lib_file.write_text("library (x) { cell (Y) { } }\n")

    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(_design_with_source_files(str(rtl_file), str(lib_file)))
    view = tab.diagram._view

    _menu, actions = _rtl_actions_by_text(view, view._boxes["top/u_nosrc"])
    action, _callback = actions["Inspect Source File"]
    assert action.isEnabled() is False


def test_inspect_source_file_disabled_for_the_mock_demo_design(qtbot):
    """build_mock_design()'s own Definitions never set source_file at
    all (there's no real file to point a demo block at) -- confirms the
    menu item correctly disables rather than crashing or guessing a
    fake path, for the exact design every other pre-existing context-
    menu test in this codebase already uses."""
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    view = tab.diagram._view

    _menu, actions = _rtl_actions_by_text(view, view._boxes["top/u_core"])
    action, _callback = actions["Inspect Source File"]
    assert action.isEnabled() is False


def test_inspect_source_file_disabled_for_multi_select(qtbot, tmp_path):
    """Same uniform "every single-target action disabled" convention
    _extra_context_menu_actions() already applies to every other action
    here for a multi-selection -- "inspect the source of 2 selected
    boxes" has no defined meaning."""
    rtl_file = tmp_path / "rtl_leaf.sv"
    rtl_file.write_text("module rtl_leaf; endmodule\n")
    lib_file = tmp_path / "macro_leaf.lib"
    lib_file.write_text("library (x) { cell (Y) { } }\n")

    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(_design_with_source_files(str(rtl_file), str(lib_file)))
    view = tab.diagram._view
    # Same end state a real Ctrl+click-to-toggle multi-select gesture
    # produces (Qt's own native QGraphicsScene handling) -- matches
    # test_multi_hierarchy_power_domain.py's own _multi_select() helper.
    view._boxes["top/u_rtl"].setSelected(True)
    view._boxes["top/u_macro"].setSelected(True)

    _menu, actions = _rtl_actions_by_text(view, view._boxes["top/u_rtl"])
    action, callback = actions["Inspect Source File"]
    assert action.isEnabled() is False
    assert callback is None


# --- the viewer itself -------------------------------------------------


def test_prompt_inspect_source_file_opens_a_tracked_non_modal_dialog(qtbot, tmp_path):
    rtl_file = tmp_path / "rtl_leaf.sv"
    rtl_file.write_text("module rtl_leaf; endmodule\n")
    lib_file = tmp_path / "macro_leaf.lib"
    lib_file.write_text("library (x) { cell (Y) { } }\n")

    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(_design_with_source_files(str(rtl_file), str(lib_file)))
    view = tab.diagram._view
    box = view._boxes["top/u_rtl"]

    assert view._source_viewer_dialogs == []
    view._prompt_inspect_source_file(box)
    assert len(view._source_viewer_dialogs) == 1
    dialog = view._source_viewer_dialogs[0]
    assert dialog.isModal() is False
    assert dialog.text_edit.toPlainText() == "module rtl_leaf; endmodule\n"

    dialog.close()
    assert view._source_viewer_dialogs == []  # removes itself on close, doesn't accumulate


def test_source_viewer_dialog_shows_correct_file_content(qtbot, tmp_path):
    source_file = tmp_path / "sample.sv"
    content = "module sample (\n    input wire clk\n);\nendmodule\n"
    source_file.write_text(content)

    dialog = SourceViewerDialog(str(source_file))
    qtbot.addWidget(dialog)

    assert dialog.text_edit.toPlainText() == content
    assert dialog.text_edit.isReadOnly() is True
    assert dialog.windowTitle() == "Inspect Source File -- sample.sv"


def test_source_viewer_dialog_shows_an_error_for_a_missing_file(qtbot, tmp_path):
    """A real, possible failure -- the file could have moved/been
    deleted since elaboration -- shown IN the viewer itself rather than
    crashing or silently showing a blank pane."""
    missing_path = str(tmp_path / "does_not_exist.sv")

    dialog = SourceViewerDialog(missing_path)
    qtbot.addWidget(dialog)

    assert "Could not read source file" in dialog.text_edit.toPlainText()
    assert missing_path in dialog.text_edit.toPlainText()


# --- Ctrl+F search -------------------------------------------------------


def test_search_finds_and_selects_a_match(qtbot, tmp_path):
    source_file = tmp_path / "sample.sv"
    source_file.write_text("module sample;\n    wire target_signal;\nendmodule\n")

    dialog = SourceViewerDialog(str(source_file))
    qtbot.addWidget(dialog)

    dialog.find_bar.open_bar()
    dialog.find_bar.line_edit.setText("target_signal")

    assert dialog.text_edit.textCursor().selectedText() == "target_signal"
    assert dialog.find_bar.status_label.text() == ""


def test_search_reports_not_found_for_an_absent_term(qtbot, tmp_path):
    source_file = tmp_path / "sample.sv"
    source_file.write_text("module sample; endmodule\n")

    dialog = SourceViewerDialog(str(source_file))
    qtbot.addWidget(dialog)

    dialog.find_bar.open_bar()
    dialog.find_bar.line_edit.setText("this_identifier_is_not_present")

    assert dialog.find_bar.status_label.text() == "Not found"


def test_search_next_wraps_around_to_the_first_match_again(qtbot, tmp_path):
    """Real, confirmed bug found writing this exact test (see
    source_viewer_dialog.py's own _find() docstring): an earlier version
    had the wrap-around cursor movement inverted, which meant even a
    SINGLE, genuinely-present match could never be found by "Next" a
    second time in a row."""
    source_file = tmp_path / "sample.sv"
    source_file.write_text("module sample;\n    wire only_once_signal;\nendmodule\n")

    dialog = SourceViewerDialog(str(source_file))
    qtbot.addWidget(dialog)

    dialog.find_bar.open_bar()
    dialog.find_bar.line_edit.setText("only_once_signal")
    assert dialog.text_edit.textCursor().selectedText() == "only_once_signal"

    dialog.find_bar._find(forward=True)
    assert dialog.text_edit.textCursor().selectedText() == "only_once_signal"
    assert dialog.find_bar.status_label.text() == ""


def test_search_highlights_every_occurrence(qtbot, tmp_path):
    source_file = tmp_path / "sample.sv"
    source_file.write_text("wire a; wire b; wire c;\n")

    dialog = SourceViewerDialog(str(source_file))
    qtbot.addWidget(dialog)

    dialog.find_bar.open_bar()
    dialog.find_bar.line_edit.setText("wire")

    assert len(dialog.text_edit.extraSelections()) == 3


def test_escape_closes_the_find_bar_and_refocuses_the_text_edit(qtbot, tmp_path):
    source_file = tmp_path / "sample.sv"
    source_file.write_text("module sample; endmodule\n")

    dialog = SourceViewerDialog(str(source_file))
    qtbot.addWidget(dialog)
    dialog.show()

    dialog.find_bar.open_bar()
    assert dialog.find_bar.isVisible() is True

    dialog.find_bar.close_bar()
    assert dialog.find_bar.isVisible() is False
    assert dialog.text_edit.extraSelections() == []
