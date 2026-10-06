"""Shared Name-field validation (power_intent_view/name_validation.py) --
explicit request: no spaces allowed in a Power Domain / Supply Set /
Retention Strategy / Supply Port name, via ONE shared utility reused by
all four. Per-dialog/per-entity wiring is tested alongside each
entity's own existing test file (test_power_domain.py for Power Domain/
Supply Set, test_power_intent_retention.py for Retention Strategy,
test_power_intent_supply_ports.py for Supply Port) -- this file covers
only the shared predicate itself.
"""

from frontend.src.power_intent_view.name_validation import NO_SPACES_ERROR, validate_no_spaces


def test_validate_no_spaces_accepts_a_plain_underscore_separated_name():
    assert validate_no_spaces("VSS_SS") is True


def test_validate_no_spaces_rejects_an_internal_space():
    assert validate_no_spaces("VSS SS") is False


def test_validate_no_spaces_rejects_multiple_internal_spaces():
    assert validate_no_spaces("VSS SS EXTRA") is False


def test_no_spaces_error_message_is_the_expected_text():
    assert NO_SPACES_ERROR == "Name cannot contain spaces."
