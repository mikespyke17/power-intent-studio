"""The supported-features list is EXHAUSTIVE against the backend IR
schema, and stays that way.

This is the test that makes section 3's deliverable a maintained list
rather than a snapshot that rots. Every field of every power-intent IR
dataclass must appear in SUPPORTED_FIELDS exactly once, classified
either MODELLED or UNMODELLED with a reason. So when the backend schema
gains a field later, this fails until someone decides what the GUI does
with it -- which is precisely the failure mode the list exists to
prevent: a construct quietly arriving in the IR that the importer then
drops without a trace.

Deliberately reads the real dataclasses via `dataclasses.fields()`
rather than a hand-kept copy of the schema. A hand-kept copy would have
the same rot problem the list itself is trying to avoid.

NOTE ON THE BOUNDARY: this test imports `backend.elaborator...ir.schema`,
which is a backend import from a TEST -- allowed, and distinct from the
structural rule that production `frontend/src/` code may only reach
`backend.api`. The rule is enforced over frontend/src/ only (see
test_mock_backend.py); tests/ has always been free to read both sides,
which is what lets this cross-check exist at all.
"""

import dataclasses

import pytest

import backend.elaborator.power_intent.ir.schema as schema
from frontend.src.power_intent_view.supported_features import (
    SUPPORTED_FIELDS,
    UNIVERSAL_FIELDS,
    Support,
    field_support,
    is_modelled,
    modelled_count,
    render_summary,
    unmodelled_count,
    unmodelled_fields,
)


def _ir_dataclasses() -> dict[str, type]:
    out = {}
    for name in dir(schema):
        cls = getattr(schema, name)
        if dataclasses.is_dataclass(cls) and name not in ("SourceLoc",):
            out[name] = cls
    return out


def _classifiable_fields(cls) -> list[str]:
    return [f.name for f in dataclasses.fields(cls) if f.name not in UNIVERSAL_FIELDS]


# --- exhaustiveness, both directions --------------------------------------


def test_every_ir_field_is_classified():
    """The core guarantee. A field added to the backend schema without a
    classification fails here, so it can never become an invisible gap."""
    missing = []
    for name, cls in _ir_dataclasses().items():
        for field in _classifiable_fields(cls):
            if field_support(name, field) is None:
                missing.append(f"{name}.{field}")
    assert missing == [], "unclassified IR fields -- decide MODELLED or UNMODELLED for each:\n  " + "\n  ".join(missing)


def test_the_list_contains_no_fields_that_left_the_schema():
    """The other direction: a stale entry for a field the backend has
    since renamed or removed is just as misleading as a missing one."""
    real = {(name, f) for name, cls in _ir_dataclasses().items() for f in _classifiable_fields(cls)}
    stale = [f"{e.construct}.{e.field}" for e in SUPPORTED_FIELDS if (e.construct, e.field) not in real]
    assert stale == [], "entries for fields that no longer exist in the IR:\n  " + "\n  ".join(stale)


def test_no_field_is_classified_twice():
    seen = {}
    for entry in SUPPORTED_FIELDS:
        key = (entry.construct, entry.field)
        assert key not in seen, f"{key} classified twice -- the two entries could disagree"
        seen[key] = entry


def test_every_ir_construct_is_represented():
    """Not just fields -- a whole construct type missing from the list
    would pass the field checks vacuously if it had no fields."""
    listed = {e.construct for e in SUPPORTED_FIELDS}
    assert set(_ir_dataclasses()) == listed


# --- each entry is actually informative -----------------------------------


def test_modelled_entries_say_where_the_value_lands():
    for entry in SUPPORTED_FIELDS:
        if entry.support is Support.MODELLED:
            assert entry.gui, f"{entry.construct}.{entry.field} is MODELLED but doesn't say into what"
            assert entry.reason is None


def test_unmodelled_entries_give_a_real_reason():
    """An UNMODELLED field is a scope decision, and the reason is what a
    future reader needs in order to revisit it. A bare "not supported"
    would make the list useless for that."""
    for entry in SUPPORTED_FIELDS:
        if entry.support is Support.UNMODELLED:
            assert entry.reason, f"{entry.construct}.{entry.field} is UNMODELLED with no reason"
            assert len(entry.reason) > 25, f"{entry.construct}.{entry.field}: reason is too thin to act on"
            assert entry.gui is None


# --- the lookup API the importer relies on --------------------------------


def test_is_modelled_matches_the_table():
    assert is_modelled("PowerDomain", "name") is True
    assert is_modelled("PowerDomain", "shutoff_condition") is False
    assert is_modelled("PowerStateGroup", "members") is False
    assert is_modelled("PowerDomain", "not_a_real_field") is False  # unknown is not modelled


def test_unmodelled_fields_groups_by_construct():
    names = {e.field for e in unmodelled_fields("IsolationStrategy")}
    assert "condition" in names and "isolation_target" in names
    assert "clamp_value" not in names  # that one IS modelled


def test_both_counts_are_nonzero_and_sum_to_the_table():
    assert modelled_count() > 0 and unmodelled_count() > 0
    assert modelled_count() + unmodelled_count() == len(SUPPORTED_FIELDS)


# --- the reviewable rendering ---------------------------------------------


def test_render_summary_lists_every_field_and_marks_each_one():
    text = render_summary()
    for entry in SUPPORTED_FIELDS:
        assert entry.field in text, f"{entry.construct}.{entry.field} missing from the rendering"
    assert text.count("[x]") == modelled_count()
    assert text.count("[ ]") == unmodelled_count()


@pytest.mark.parametrize("construct", sorted(_ir_dataclasses()))
def test_every_construct_appears_in_the_rendering(construct):
    assert construct in render_summary()


# --- known limitations (review items 4.1 / 4.2) ------------------------


def test_known_limitations_are_rendered_in_the_summary():
    """These are NOT per-field gaps, so nothing else in this module would
    surface them -- and an unrecorded known limitation is how one turns
    into a surprise. `scripts/show_supported_features.py` is what makes
    the list reviewable without reading Python, so the rendering is the
    part that has to keep working."""
    from frontend.src.power_intent_view.supported_features import KNOWN_LIMITATIONS

    summary = render_summary()
    assert "KNOWN LIMITATIONS" in summary
    assert KNOWN_LIMITATIONS
    for limitation in KNOWN_LIMITATIONS:
        assert limitation.title in summary
        assert limitation.detail in summary
        assert limitation.detail.strip(), f"{limitation.title} has no recorded reason"


def test_macro_retention_is_recorded_as_a_known_limitation():
    """4.2. Retention is RTL-sequential-element-based and is rejected on a
    macro, so there is no correct way to state that a macro retains its
    contents. Deliberately NOT built -- it is a separate architectural
    question -- but it must not be silently absent either."""
    from frontend.src.power_intent_view.supported_features import KNOWN_LIMITATIONS

    assert any("Macro" in l.title and "retention" in l.title.lower() for l in KNOWN_LIMITATIONS)
