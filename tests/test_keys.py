"""The key the source file does not have.

The property that matters most is determinism: the same row must produce the same ID next
week, or week over week comparison is impossible.
"""

import pytest

from tracker import keys as K


BASE = {"instrument_id": "RSP-2", "implementation_need": "Registration Submission",
        "fiscal_year": "26/27"}


def test_same_row_twice_gives_the_same_id():
    assert K.timeline_item_id(dict(BASE)) == K.timeline_item_id(dict(BASE))


def test_id_is_16_lowercase_hex():
    tid = K.timeline_item_id(BASE)
    assert len(tid) == 16
    assert all(c in "0123456789abcdef" for c in tid)


@pytest.mark.parametrize("variant", [
    {"instrument_id": "  RSP-2 "},                       # padding
    {"instrument_id": "rsp-2"},                          # case
    {"implementation_need": "Registration  Submission"},  # doubled space
    {"implementation_need": " Registration Submission "},
])
def test_normalization_makes_dirty_values_produce_the_same_id(variant):
    """This is why cleaning must run before the key is built. Without it the same row
    would hash differently and present as deleted and recreated every week."""
    dirty = dict(BASE, **variant)
    assert K.timeline_item_id(dirty) == K.timeline_item_id(BASE)


@pytest.mark.parametrize("field", ["instrument_id", "implementation_need", "fiscal_year"])
def test_changing_any_key_field_changes_the_id(field):
    other = dict(BASE, **{field: "something else"})
    assert K.timeline_item_id(other) != K.timeline_item_id(BASE)


def test_a_non_key_field_does_not_affect_the_id():
    assert K.timeline_item_id(dict(BASE, official_status="Complete")) == \
           K.timeline_item_id(BASE)


def test_instrument_id_alone_is_not_the_key():
    """82 real rows resolve to 24 instruments, which is the whole reason this module
    exists."""
    a = dict(BASE)
    b = dict(BASE, implementation_need="A different need")
    assert a["instrument_id"] == b["instrument_id"]
    assert K.timeline_item_id(a) != K.timeline_item_id(b)


def test_a_missing_key_field_raises():
    with pytest.raises(KeyError):
        K.timeline_item_id({"instrument_id": "RSP-1"})


def test_an_empty_key_field_raises():
    with pytest.raises(ValueError):
        K.timeline_item_id(dict(BASE, fiscal_year=""))


def test_a_separator_inside_a_value_raises():
    """Otherwise two different rows could produce the same joined string."""
    with pytest.raises(ValueError):
        K.timeline_item_id(dict(BASE, implementation_need="a||b"))


def test_tbd_in_a_key_field_is_provisional():
    assert K.is_provisional(dict(BASE, fiscal_year="TBD")) is True
    assert K.is_provisional(BASE) is False


def test_add_keys_stamps_both_columns(clean_rows):
    rows, _, _, _ = clean_rows
    for r in rows:
        assert len(r["timeline_item_id"]) == 16
        assert isinstance(r["key_is_provisional"], bool)


def test_the_synthetic_rows_are_unique(clean_rows):
    rows, _, _, _ = clean_rows
    assert K.check_unique(rows) is True


def test_a_duplicate_is_detected_and_both_rows_named(clean_rows):
    rows, _, _, _ = clean_rows
    dupe = dict(rows[0])
    dupe["source_row_number"] = 99
    with pytest.raises(ValueError) as e:
        K.check_unique(rows + [dupe])
    msg = str(e.value)
    assert "duplicate key" in msg
    assert "row 99" in msg, "the message must name the offending rows, a person has to look"


def test_a_collision_against_history_raises():
    tid = K.timeline_item_id(BASE)
    with pytest.raises(ValueError):
        K.check_against_history([dict(BASE)], {tid: "a||completely||different"})


def test_history_check_passes_when_the_key_matches():
    tid = K.timeline_item_id(BASE)
    nk = K.natural_key(BASE)
    assert K.check_against_history([dict(BASE)], {tid: nk}) is True
