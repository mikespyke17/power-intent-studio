"""'Extract Diagram' (explicit request): export open diagram tabs to
PNG / JPEG / SVG. See extract_diagram.py's own module docstring."""

from pathlib import Path

from PySide6.QtGui import QImage

from frontend.src.app.extract_diagram import ExtractDiagramDialog, output_path_for, render_view_to_file, sanitize_filename_component
from frontend.src.power_intent_view.power_intent_tab import PowerIntentTab
from frontend.src.shared.mock_backend import build_mock_design


def _tab_with_diagram(qtbot) -> PowerIntentTab:
    tab = PowerIntentTab()
    qtbot.addWidget(tab)
    tab.set_rtl(build_mock_design())
    tab.diagram.show_bare_hierarchy()
    return tab


# --- (a) single diagram export in each format ---------------------------


def test_a_single_diagram_exports_a_valid_file_in_each_format(qtbot, tmp_path):
    tab = _tab_with_diagram(qtbot)
    name, view = tab.diagram.all_diagram_views()[0]

    for fmt, ext in (("PNG", ".png"), ("JPEG", ".jpg"), ("SVG", ".svg")):
        out = str(tmp_path / f"diagram{ext}")
        render_view_to_file(view, out, fmt)
        assert Path(out).exists() and Path(out).stat().st_size > 0
        if fmt != "SVG":
            image = QImage(out)
            assert not image.isNull() and image.width() > 0 and image.height() > 0
        else:
            assert "<svg" in Path(out).read_text()


# --- (b) menu-bar multi-select lists every open diagram tab -------------


def test_b_multi_select_dialog_lists_every_open_diagram_tab(qtbot, tmp_path):
    tab = _tab_with_diagram(qtbot)
    tab.diagram.open_path("top/u_core")
    tab.diagram.open_path("top/u_dbg")
    names = [n for n, _ in tab.diagram.all_diagram_views()]
    assert names == ["top", "top/u_core", "top/u_dbg"]

    dialog = ExtractDiagramDialog(names, allow_multi_select=True, default_directory=str(tmp_path))
    qtbot.addWidget(dialog)

    assert dialog._list is not None
    listed = [dialog._list.item(i).text() for i in range(dialog._list.count())]
    assert listed == names
    # all checked by default -> selected_names is the full list
    assert dialog.selected_names == names


# --- (c) exporting several selected diagrams -> one file per diagram ----


def test_c_multiple_selected_diagrams_produce_one_named_file_each(qtbot, tmp_path):
    tab = _tab_with_diagram(qtbot)
    tab.diagram.open_path("top/u_core")
    pairs = tab.diagram.all_diagram_views()
    view_by_name = {n: v for n, v in pairs}
    chosen = ["top", "top/u_core"]

    for name in chosen:
        out = output_path_for(str(tmp_path), "myproj", name, "PNG", single=False)
        render_view_to_file(view_by_name[name], out, "PNG")

    assert (tmp_path / "myproj_top.png").exists()
    assert (tmp_path / "myproj_top_u_core.png").exists()


def test_c_single_selection_uses_the_base_filename_verbatim(tmp_path):
    assert output_path_for(str(tmp_path), "just_this", "top", "SVG", single=True) == str(tmp_path / "just_this.svg")
    assert sanitize_filename_component("top/u_core") == "top_u_core"


# --- (d) right-click empty canvas exports only that diagram ------------


def test_d_right_click_empty_space_targets_only_that_one_diagram(qtbot):
    """The inner view emits extractDiagramRequested, re-broadcast by
    PowerIntentDiagramView tagged with that specific view -- so the
    export target is unambiguous even with other diagram tabs open."""
    tab = _tab_with_diagram(qtbot)
    tab.diagram.open_path("top/u_core")
    _root_name, root_view = tab.diagram.all_diagram_views()[0]
    sub_name, sub_view = tab.diagram.all_diagram_views()[1]

    captured = []
    tab.diagram.extractDiagramRequested.connect(lambda v: captured.append(v))

    sub_view.extractDiagramRequested.emit()

    assert captured == [sub_view]
    assert captured[0] is not root_view
