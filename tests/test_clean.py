"""The thirteen cleaning rules.

Each test names the rule it covers and, where the rule exists because of something real,
uses the value that was actually found in the export.
"""

import datetime
import pytest

from tracker import clean as C


def _by_rule(log):
    out = {}
    for e in log:
        out.setdefault(e.rule_id, []).append(e)
    return out


# ---------------------------------------------------------------- unit level


def test_c01_trims():
    assert C.c01_c02_text("Not Initiated ") == ("Not Initiated", True)


def test_c02_collapses_repeated_spaces():
    assert C.c01_c02_text("VOC2  -  COV2") == ("VOC2 - COV2", True)


def test_c01_leaves_clean_text_alone():
    assert C.c01_c02_text("In Progress") == ("In Progress", False)


def test_c03_fixes_case_only():
    allowed = {"On Hold", "In Progress"}
    assert C.c03_canonical_case("On hold", allowed) == ("On Hold", True)


def test_c03_never_maps_onto_a_different_value():
    """An unknown value is a validation error, not something to guess at."""
    allowed = {"On Hold", "In Progress"}
    value, changed = C.c03_canonical_case("Paused", allowed)
    assert value == "Paused" and changed is False


@pytest.mark.parametrize("sentinel", ["N/A", "TBC", "TBD", "-", "", "  "])
def test_c04_maps_every_sentinel_to_null(sentinel):
    assert C.c04_sentinel(sentinel)[0] is None


def test_c04_covers_tbd_which_the_first_version_missed():
    """TBD appears 34 times in the real export and was absent from version 1.0."""
    assert C.c04_sentinel("TBD") == (None, True)


def test_c05_drops_the_time_from_a_datetime():
    v, changed, rule, ok = C.c05_date(datetime.datetime(2026, 5, 6, 0, 0))
    assert v == datetime.date(2026, 5, 6) and rule == "C05" and ok


def test_c05b_parses_long_form_english_dates():
    """26 real dates arrive this way. Without the rule they would all be rejected."""
    v, changed, rule, ok = C.c05_date("Monday, April 5, 2027")
    assert v == datetime.date(2027, 4, 5) and rule == "C05b" and ok


def test_an_unparseable_date_is_an_error_not_a_silent_null():
    v, changed, rule, ok = C.c05_date("sometime next spring")
    assert ok is False


def test_c06_parses_priority_and_nulls_the_sentinel():
    assert C.c06_priority(4) == (4, False, True)
    assert C.c06_priority("N/A") == (None, True, True)
    assert C.c06_priority("high")[2] is False


def test_c07_corrects_only_a_known_variant():
    lookup = {"morgan ashbey": "Morgan Ashby"}
    assert C.c07_name("Morgan Ashbey", lookup) == ("Morgan Ashby", True)
    assert C.c07_name("Peter Quinlan", lookup) == ("Peter Quinlan", False)


def test_c08_splits_the_bilingual_short_name():
    assert C.c08_split_short_name("VOC2 - COV2") == ("VOC2", "COV2", True)


def test_c08b_leaves_both_parts_null_when_there_is_no_separator():
    """EPOD IDEA is a project name, not a bilingual abbreviation. Data, not an error."""
    assert C.c08_split_short_name("EPOD IDEA") == (None, None, False)


def test_c10_strips_a_leading_newline():
    v, changed = C.c10_strip_newlines("\nMulti-Sector Air Pollutants Regulations (Part 1)")
    assert v == "Multi-Sector Air Pollutants Regulations (Part 1)" and changed


def test_c11_upper_cases_and_validates_the_instrument_id():
    assert C.c11_instrument_id(" rsp-2 ", {"RSP-2"}) == ("RSP-2", True, True)
    assert C.c11_instrument_id("RSP-99", {"RSP-2"})[2] is False


def test_c12_keeps_internal_line_breaks_in_notes():
    """Notes carry the reason a date moved. Flattening them loses the most useful free
    text in the file."""
    v, changed = C.c12_notes("  \nDark Release\nNeed to align  ")
    assert "\n" in v
    assert v.startswith("Dark Release")


def test_c09_reports_a_divergence_from_the_srs():
    d = C.c09_report_naming("MGBH", "COM", {"MGBH": "OMCG"}, 25)
    assert d is not None and d.expected == "OMCG" and d.source_row_number == 25


def test_c09_is_silent_when_the_instrument_is_not_in_the_srs():
    assert C.c09_report_naming("DASR", "RIM", {"MGBH": "OMCG"}, 3) is None


def test_c09_is_silent_when_they_agree():
    assert C.c09_report_naming("CER", "REP", {"CER": "REP"}, 1) is None


# ---------------------------------------------------------------- whole rows


def test_the_synthetic_file_produces_no_validation_errors(clean_rows):
    rows, log, divs, errs = clean_rows
    assert errs == [], f"unexpected errors: {errs[:5]}"


def test_every_row_survives(clean_rows, dirty_rows):
    rows, _, _, _ = clean_rows
    assert len(rows) == len(dirty_rows)


def test_the_trailing_space_status_is_cleaned(clean_rows):
    rows, _, _, _ = clean_rows
    assert rows[1]["official_status"] == "Not Initiated"


def test_the_lower_case_status_is_cleaned(clean_rows):
    rows, _, _, _ = clean_rows
    assert rows[2]["official_status"] == "On Hold"


def test_the_long_form_date_became_a_real_date(clean_rows):
    rows, _, _, _ = clean_rows
    assert rows[2]["uat_start_date"] == datetime.date(2027, 4, 5)


def test_fiscal_year_tbd_is_kept_not_nulled(clean_rows):
    """C04b. Nulling a key field would break the key. Keeping it visible is the lesser
    problem, and the row is flagged provisional."""
    rows, _, _, _ = clean_rows
    assert rows[2]["fiscal_year"] == "TBD"
    assert rows[2]["key_is_provisional"] is True


def test_the_short_name_split_into_two_languages(clean_rows):
    rows, _, _, _ = clean_rows
    assert rows[1]["instrument_short_name_en"] == "VOC2"
    assert rows[1]["instrument_short_name_fr"] == "COV2"
    assert rows[1]["instrument_short_name_raw"] == "VOC2 - COV2"


def test_a_short_name_with_no_separator_keeps_the_raw_value(clean_rows):
    rows, _, _, _ = clean_rows
    assert rows[2]["instrument_short_name_raw"] == "EPOD IDEA"
    assert rows[2]["instrument_short_name_en"] is None


def test_the_name_variant_was_corrected(clean_rows):
    rows, _, _, _ = clean_rows
    assert rows[4]["lead_rsm_concierge"] == "Morgan Ashby"


def test_the_leading_newline_is_gone(clean_rows):
    rows, _, _, _ = clean_rows
    assert rows[3]["instrument_full_name"].startswith("Multi-Sector")


def test_sentinels_in_date_columns_became_null(clean_rows):
    rows, _, _, _ = clean_rows
    assert rows[3]["target_blue_stamp_date"] is None
    assert rows[3]["coming_into_force_date"] is None


def test_priority_sentinel_became_null(clean_rows):
    rows, _, _, _ = clean_rows
    assert rows[1]["priority"] is None


def test_the_mgbh_divergence_is_reported(clean_rows):
    rows, log, divs, errs = clean_rows
    assert len(divs) == 1
    assert divs[0].value == "COM" and divs[0].expected == "OMCG"


def test_the_divergence_was_not_corrected(clean_rows):
    """Reported, never rewritten. It is a disagreement between two documents."""
    rows, _, divs, _ = clean_rows
    assert rows[5]["instrument_short_name_fr"] == "COM"


def test_every_change_is_logged_with_a_rule_id(clean_rows):
    rows, log, _, _ = clean_rows
    assert log, "cleaning produced no log at all, which cannot be right"
    for e in log:
        assert e.rule_id
        assert e.field_name
        assert e.source_row_number >= 2, "row 1 is the header"


def test_the_rules_that_fired_are_the_ones_we_expect(clean_rows):
    rows, log, _, _ = clean_rows
    fired = set(_by_rule(log))
    for rule_id in ("C01", "C03", "C04", "C05", "C05b", "C06", "C07", "C08", "C08b", "C10"):
        assert rule_id in fired, f"{rule_id} never fired on data built to trigger it"


def test_a_log_entry_records_both_values(clean_rows):
    rows, log, _, _ = clean_rows
    status_fixes = [e for e in _by_rule(log).get("C03", [])
                    if e.field_name == "official_status"]
    assert status_fixes, "the lower case status should have been corrected by C03"
    assert status_fixes[0].raw_value == "On hold"
    assert status_fixes[0].cleaned_value == "On Hold"


def test_an_unknown_status_is_reported_as_an_error(contract, dirty_rows,
                                                   name_lookup, srs, known_ids):
    bad = dirty_rows[:1]
    bad[0]["Status"] = "Somewhere In Between"
    rows, log, divs, errs = C.clean_rows(bad, contract, name_lookup=name_lookup,
                                         srs=srs, known_ids=known_ids)
    assert any("official_status" in e for e in errs)


def test_an_unknown_instrument_id_is_reported(contract, dirty_rows,
                                              name_lookup, srs):
    rows, log, divs, errs = C.clean_rows(dirty_rows[:1], contract,
                                         name_lookup=name_lookup, srs=srs,
                                         known_ids={"RSP-99"})
    assert any("instrument_id" in e for e in errs)


def test_an_unparseable_date_in_a_row_is_reported(contract, dirty_rows,
                                                  name_lookup, srs, known_ids):
    bad = dirty_rows[:1]
    bad[0]["Code Freeze"] = "next spring sometime"
    rows, log, divs, errs = C.clean_rows(bad, contract, name_lookup=name_lookup,
                                         srs=srs, known_ids=known_ids)
    assert any("code_freeze_date" in e for e in errs)
