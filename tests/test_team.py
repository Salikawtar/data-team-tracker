"""The Data Team's own side of the boundary.

The mirror of the import guard: the application writes what people type, and must never
write the official record.
"""

import datetime
import pytest

from tracker import team as T
from tracker.team import ValidationError


TID = "c8f454a04d269131"


@pytest.fixture
def t():
    """A table name function, so the SQL helpers can be checked without a database."""
    return lambda layer, name: f"cat.sch.rsm_data_tracker_{layer}_{name}"


@pytest.fixture
def work_item(contract, now):
    return T.new_work_item(contract, timeline_item_id=TID, work_type="Pipeline Development",
                           data_owner_user_id="k.salik", now=now)


# ------------------------------------------------------------------ work items


def test_a_work_item_has_every_dictionary_field(contract, work_item):
    expected = {f.name for f in contract.fields("data_work_items")}
    assert set(work_item.keys()) == expected


def test_a_new_work_item_starts_not_started_and_active(work_item):
    assert work_item["current_status"] == "Not Started"
    assert work_item["is_active"] is True


def test_the_id_is_unique(contract, now):
    a = T.new_work_item(contract, timeline_item_id=TID, work_type="UAT Validation",
                        data_owner_user_id="k.salik", now=now)
    b = T.new_work_item(contract, timeline_item_id=TID, work_type="UAT Validation",
                        data_owner_user_id="k.salik", now=now)
    assert a["data_work_item_id"] != b["data_work_item_id"]


def test_an_unknown_work_type_is_refused(contract, now):
    with pytest.raises(ValidationError) as e:
        T.new_work_item(contract, timeline_item_id=TID, work_type="Doing Some Stuff",
                        data_owner_user_id="k.salik", now=now)
    assert "not an allowed value" in str(e.value)


def test_the_refusal_lists_the_allowed_values(contract, now):
    """A message that says no without saying what would work is a bad message."""
    with pytest.raises(ValidationError) as e:
        T.new_work_item(contract, timeline_item_id=TID, work_type="nope",
                        data_owner_user_id="k.salik", now=now)
    assert "Pipeline Development" in str(e.value)


def test_an_owner_is_required(contract, now):
    with pytest.raises(ValidationError):
        T.new_work_item(contract, timeline_item_id=TID, work_type="UAT Validation",
                        data_owner_user_id="   ", now=now)


def test_a_malformed_timeline_id_is_refused(contract, now):
    with pytest.raises(ValidationError) as e:
        T.new_work_item(contract, timeline_item_id="RSP-2", work_type="UAT Validation",
                        data_owner_user_id="k.salik", now=now)
    assert "16 lowercase hexadecimal" in str(e.value)


def test_a_target_before_the_start_is_refused(contract, now):
    with pytest.raises(ValidationError) as e:
        T.new_work_item(contract, timeline_item_id=TID, work_type="UAT Validation",
                        data_owner_user_id="k.salik", now=now,
                        planned_start_date="2026-09-01", internal_target_date="2026-08-01")
    assert "before the planned start" in str(e.value)


def test_dates_are_parsed_from_text(contract, now):
    w = T.new_work_item(contract, timeline_item_id=TID, work_type="UAT Validation",
                        data_owner_user_id="k.salik", now=now,
                        internal_target_date="2026-09-15")
    assert w["internal_target_date"] == datetime.date(2026, 9, 15)


def test_an_unreadable_date_says_what_format_to_use(contract, now):
    with pytest.raises(ValidationError) as e:
        T.new_work_item(contract, timeline_item_id=TID, work_type="UAT Validation",
                        data_owner_user_id="k.salik", now=now,
                        internal_target_date="next tuesday")
    assert "YYYY-MM-DD" in str(e.value)


def test_closing_deactivates_rather_than_deletes(contract, work_item, now):
    closed = T.close_work_item(contract, work_item, now=now)
    assert closed["is_active"] is False
    assert closed["data_work_item_id"] == work_item["data_work_item_id"]


def test_changing_status_leaves_everything_else_alone(contract, work_item, now):
    later = datetime.datetime(2026, 9, 1, 9, 0)
    changed = T.update_work_item_status(contract, work_item,
                                        current_status="Complete", now=later)
    assert changed["current_status"] == "Complete"
    assert changed["updated_at"] == later
    for k in ("data_work_item_id", "timeline_item_id", "work_type", "created_at"):
        assert changed[k] == work_item[k]


# ------------------------------------------------------------------ updates


def _update(contract, now, **over):
    args = dict(data_work_item_id="w1", submitted_by_user_id="k.salik",
                status_at_update="In Progress",
                progress_update="Extraction finished for tanks and racks.", now=now)
    args.update(over)
    return T.new_update(contract, **args)


def test_an_update_has_every_dictionary_field(contract, now):
    expected = {f.name for f in contract.fields("team_updates")}
    assert set(_update(contract, now).keys()) == expected


def test_progress_is_the_only_free_text_that_is_required(contract, now):
    u = _update(contract, now)
    assert u["progress_update"]
    for optional in ("blocker", "next_action", "support_required", "percent_complete",
                     "expected_completion_date"):
        assert u[optional] is None


def test_an_empty_progress_update_is_refused(contract, now):
    with pytest.raises(ValidationError) as e:
        _update(contract, now, progress_update="   ")
    assert "required" in str(e.value)


def test_blocked_without_a_blocker_is_refused(contract, now):
    """A blocked item with no reason cannot be helped by anyone."""
    with pytest.raises(ValidationError) as e:
        _update(contract, now, status_at_update="Blocked")
    assert "blocking" in str(e.value)


def test_blocked_with_a_blocker_is_accepted(contract, now):
    u = _update(contract, now, status_at_update="Blocked",
                blocker="Waiting on the UAT environment refresh")
    assert u["blocker"].startswith("Waiting")


def test_percent_must_be_between_0_and_100(contract, now):
    assert _update(contract, now, percent_complete=60)["percent_complete"] == 60
    with pytest.raises(ValidationError):
        _update(contract, now, percent_complete=140)
    with pytest.raises(ValidationError):
        _update(contract, now, percent_complete="most of it")


def test_percent_is_optional(contract, now):
    """Making it mandatory produces guessing rather than information."""
    assert _update(contract, now, percent_complete="")["percent_complete"] is None


def test_an_over_long_field_says_the_limit(contract, now):
    with pytest.raises(ValidationError) as e:
        _update(contract, now, blocker="x" * 1500)
    assert "1000" in str(e.value)


def test_an_unknown_status_is_refused(contract, now):
    with pytest.raises(ValidationError):
        _update(contract, now, status_at_update="Sort of going")


def test_the_status_vocabulary_is_the_team_one_not_the_official_one(contract, now):
    """Three status vocabularies exist in this project. Using the wrong one on a screen
    is how they get confused."""
    team = contract.allowed("team_status")
    official = contract.allowed("official_status")
    assert "Not Started" in team and "Not Started" not in official
    assert "Not Initiated" in official and "Not Initiated" not in team


# ------------------------------------------------------------------ the guard


def test_the_application_never_writes_an_official_field(contract, work_item):
    assert T.assert_no_official_fields(work_item, "data_work_items", contract) is True


def test_the_guard_refuses_an_official_field(contract, work_item):
    """The mirror of publish.assert_no_team_fields. If the application wrote an official
    field, the next import would silently undo it."""
    bad = dict(work_item)
    bad["official_status"] = "Complete"
    with pytest.raises(AssertionError) as e:
        T.assert_no_official_fields(bad, "data_work_items", contract)
    assert "official_status" in str(e.value)


def test_the_guard_refuses_an_unknown_column(contract, work_item):
    bad = dict(work_item, some_new_idea="x")
    with pytest.raises(AssertionError):
        T.assert_no_official_fields(bad, "data_work_items", contract)


def test_the_link_field_is_allowed(contract, work_item):
    """timeline_item_id is the one crossing, and it is only ever a foreign key."""
    assert work_item["timeline_item_id"] == TID
    assert T.assert_no_official_fields(work_item, "data_work_items", contract) is True


# ------------------------------------------------------------------ staleness


def test_days_since_counts_correctly():
    today = datetime.date(2026, 9, 10)
    assert T.days_since(datetime.date(2026, 9, 3), today) == 7
    assert T.days_since(datetime.datetime(2026, 9, 3, 14, 0), today) == 7
    assert T.days_since(None, today) is None


def test_an_item_never_updated_is_stale():
    assert T.is_stale(None, datetime.date(2026, 9, 10)) is True


def test_seven_days_is_not_yet_stale():
    today = datetime.date(2026, 9, 10)
    assert T.is_stale(datetime.date(2026, 9, 3), today) is False
    assert T.is_stale(datetime.date(2026, 9, 2), today) is True


# ------------------------------------------------------------------ queries


def test_every_query_mentions_the_right_tables(t):
    assert "core_official_timeline" in T.sql_timeline_picker(t)
    assert "core_data_work_items" in T.sql_work_items(t)
    assert "core_team_updates" in T.sql_updates_for(t, "w1")
    assert "core_timeline_change_log" in T.sql_recent_official_changes(t)


def test_the_picker_hides_missing_rows(t):
    assert "NOT is_missing" in T.sql_timeline_picker(t)


def test_work_item_filters_are_applied(t):
    sql = T.sql_work_items(t, owner="k.salik", instrument_id="RSP-2", status="Blocked")
    assert "k.salik" in sql and "RSP-2" in sql and "Blocked" in sql


def test_an_apostrophe_in_a_filter_does_not_break_the_sql(t):
    sql = T.sql_work_items(t, owner="o'brien")
    assert "o''brien" in sql


def test_a_limit_that_is_not_a_number_is_refused(t):
    """A limit interpolated as text would be an injection point, so it is cast to int and
    anything that is not a number fails loudly rather than reaching the query."""
    with pytest.raises(ValueError):
        T.sql_updates_for(t, "w1", limit="5; DROP TABLE x")


def test_a_numeric_limit_still_works(t):
    assert "LIMIT 5" in T.sql_updates_for(t, "w1", limit="5")


def test_stale_query_excludes_completed_work(t):
    assert "Complete" in T.sql_stale_items(t)


def test_stale_query_includes_items_never_updated(t):
    assert "last_update IS NULL" in T.sql_stale_items(t)


def test_blocked_query_uses_only_the_latest_update(t):
    """An item blocked last month and fine now must not appear."""
    sql = T.sql_blocked(t)
    assert "latest" in sql and "MAX(submitted_at)" in sql


def test_coverage_query_measures_the_charter_target(t):
    sql = T.sql_coverage(t)
    assert "percent_fresh" in sql and "7 DAYS" in sql


# ---------------------------------------------------------------- missing dates
#
# These exist because of a bug the port introduced and the tests did not catch.
#
# days_since only checked for None. A work item that has never had an update has no
# last_update, and reading that column back through pandas gives NaT, which is neither None
# nor a float. The Submit update screen crashed on it. Nothing in the suite covered a
# missing date, because nothing had ever produced one.

def test_days_since_handles_none():
    assert T.days_since(None, datetime.date(2026, 9, 13)) is None


def test_days_since_handles_a_pandas_missing_timestamp():
    """NaT is not None and is not a float, so it slipped past the original guard."""
    import pandas as pd
    assert T.days_since(pd.NaT, datetime.date(2026, 9, 13)) is None


def test_days_since_handles_a_plain_nan():
    assert T.days_since(float("nan"), datetime.date(2026, 9, 13)) is None


def test_days_since_counts_forward_not_backward():
    """The sign matters. date_diff reads unit, start, end, and getting the last two the
    wrong way round gives a negative number of days rather than an error."""
    n = T.days_since(datetime.datetime(2026, 9, 6, 14, 0), datetime.date(2026, 9, 13))
    assert n == 7


def test_an_item_never_updated_is_stale():
    import pandas as pd
    assert T.is_stale(pd.NaT, datetime.date(2026, 9, 13)) is True
