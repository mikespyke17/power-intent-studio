"""shared/widgets/target_hierarchy_label.py -- the single shared
"Applies to:" formatting helper used by Power Domain/Retention/
Isolation/Level Shifter/Power Switch dialogs alike. Capping at 10
hierarchies (explicit request) is implemented ONCE here, not
duplicated per dialog -- see this module's own docstring."""

from frontend.src.shared.widgets.target_hierarchy_label import make_target_hierarchy_label


def test_ten_or_fewer_hierarchies_show_in_full_with_no_truncation_indicator(qtbot):
    paths = [f"top/u_{i}" for i in range(10)]

    label = make_target_hierarchy_label(paths)
    qtbot.addWidget(label)

    assert label.text() == "\n".join(paths)
    assert "more" not in label.text()


def test_a_single_hierarchy_is_unaffected(qtbot):
    label = make_target_hierarchy_label(["top/u_core"])
    qtbot.addWidget(label)

    assert label.text() == "top/u_core"


def test_more_than_ten_hierarchies_caps_at_ten_plus_a_count_of_the_rest(qtbot):
    """(a): 15 member hierarchies -- exactly 10 listed, one per line,
    plus an explicit "... and 5 more" indicator naming the real
    remaining count."""
    paths = [f"top/u_{i}" for i in range(15)]

    label = make_target_hierarchy_label(paths)
    qtbot.addWidget(label)

    lines = label.text().split("\n")
    assert lines[:10] == paths[:10]
    assert lines[10] == "... and 5 more"
    assert len(lines) == 11


def test_exactly_eleven_hierarchies_reports_one_more(qtbot):
    paths = [f"top/u_{i}" for i in range(11)]

    label = make_target_hierarchy_label(paths)
    qtbot.addWidget(label)

    assert label.text().split("\n")[-1] == "... and 1 more"


def test_exactly_ten_hierarchies_is_the_boundary_no_cap_applied(qtbot):
    """(b): confirms the cap doesn't kick in unnecessarily right at the
    boundary count -- 10 is shown in full, not 9 plus an indicator."""
    paths = [f"top/u_{i}" for i in range(10)]

    label = make_target_hierarchy_label(paths)
    qtbot.addWidget(label)

    assert label.text() == "\n".join(paths)
    assert len(label.text().split("\n")) == 10
