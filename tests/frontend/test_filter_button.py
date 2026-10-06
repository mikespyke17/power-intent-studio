"""shared/widgets/filter_button.py -- the checkable-category popup menu
button, including the "All" sync option and the `label` override added
for Design Properties' "Visualize" rename (explicit request: rename
"Filter" to "Visualize" in Design Properties, add a default-on "All")."""

from frontend.src.shared.widgets.filter_button import FilterButton


def test_default_label_is_filter(qtbot):
    button = FilterButton(["Input", "Output"], lambda: None)
    qtbot.addWidget(button)

    assert button.text() == "Filter"


def test_label_can_be_overridden(qtbot):
    button = FilterButton(["Input", "Output"], lambda: None, label="Visualize")
    qtbot.addWidget(button)

    assert button.text() == "Visualize"


def test_without_include_all_every_category_starts_checked_and_no_all_action(qtbot):
    button = FilterButton(["Input", "Output"], lambda: None)
    qtbot.addWidget(button)

    assert button._all_action is None
    assert button.active_categories() == {"Input", "Output"}


def test_with_include_all_the_all_action_exists_and_starts_checked(qtbot):
    button = FilterButton(["Input", "Output"], lambda: None, include_all=True)
    qtbot.addWidget(button)

    assert button._all_action is not None
    assert button._all_action.isChecked() is True
    assert button.active_categories() == {"Input", "Output"}  # everything, by default


def test_unchecking_one_category_unchecks_all(qtbot):
    calls = []
    button = FilterButton(["Input", "Output"], lambda: calls.append(button.active_categories()), include_all=True)
    qtbot.addWidget(button)

    button._actions["Output"].setChecked(False)

    assert button._all_action.isChecked() is False
    assert button.active_categories() == {"Input"}
    assert calls[-1] == {"Input"}


def test_re_checking_every_category_by_hand_re_checks_all(qtbot):
    button = FilterButton(["Input", "Output"], lambda: None, include_all=True)
    qtbot.addWidget(button)
    button._actions["Output"].setChecked(False)
    assert button._all_action.isChecked() is False

    button._actions["Output"].setChecked(True)

    assert button._all_action.isChecked() is True


def test_checking_all_after_a_partial_selection_re_checks_every_category(qtbot):
    button = FilterButton(["Input", "Output", "Inout"], lambda: None, include_all=True)
    qtbot.addWidget(button)
    button._actions["Output"].setChecked(False)
    button._actions["Inout"].setChecked(False)
    assert button.active_categories() == {"Input"}

    button._all_action.setChecked(True)

    assert button.active_categories() == {"Input", "Output", "Inout"}
