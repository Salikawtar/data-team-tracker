"""Comparison and publishing.

The two guarantees the charter names as success measures live here:

    no Data Team field is ever changed by an import
    importing the same file twice produces one import, not two
"""

import copy
import datetime
import pytest

from tracker import compare as CMP
from tracker import publish as PUB


@pytest.fixture
def first_import(clean_rows, contract, now):
    """The state after one approved import of the synthetic file."""
    rows, log, divs, errs = clean_rows
    comparison = CMP.compare(rows, [], contract)
    plan = PUB.plan_publish(comparison, contract, "imp_2026_08_27_001", now, current=[])
    return rows, comparison, plan


# ---------------------------------------------------------------- comparison


def test_an_empty_table_makes_everything_new(first_import):
    rows, comparison, _ = first_import
    assert comparison.counts["new"] == len(rows)
    assert comparison.counts["changed"] == 0


def test_the_same_rows_again_are_all_unchanged(clean_rows, contract, first_import):
    rows, _, plan = first_import
    again = CMP.compare(rows, plan.upserts, contract)
    assert again.counts["unchanged"] == len(rows)
    assert again.counts["changed"] == 0
    assert again.has_changes is False


def test_re_sorting_the_sheet_changes_nothing(clean_rows, contract, first_import):
    """The real sheet is sorted by Priority and priority is re-ranked between exports.
    Comparing by row position would report the whole file as changed."""
    rows, _, plan = first_import
    shuffled = list(reversed(rows))
    again = CMP.compare(shuffled, plan.upserts, contract)
    assert again.counts["unchanged"] == len(rows)


def test_a_changed_date_is_detected_and_named(clean_rows, contract, first_import):
    rows, _, plan = first_import
    week2 = copy.deepcopy(rows)
    week2[0]["production_launch_date"] = datetime.date(2026, 12, 10)
    c = CMP.compare(week2, plan.upserts, contract)
    assert c.counts["changed"] == 1
    changed = c.of("changed")[0]
    assert [ch.field for ch in changed.changes] == ["production_launch_date"]
    assert changed.changes[0].old == datetime.date(2026, 9, 3)
    assert changed.changes[0].new == datetime.date(2026, 12, 10)


def test_a_date_change_is_visible_as_schedule_movement(clean_rows, contract, first_import):
    rows, _, plan = first_import
    week2 = copy.deepcopy(rows)
    week2[0]["code_freeze_date"] = datetime.date(2026, 8, 1)
    c = CMP.compare(week2, plan.upserts, contract)
    assert len(c.date_changes()) == 1


def test_a_new_row_is_new(clean_rows, contract, first_import):
    from tracker import keys as K
    rows, _, plan = first_import
    extra = copy.deepcopy(rows[0])
    extra["implementation_need"] = "A need that did not exist last week"
    K.add_keys([extra])
    c = CMP.compare(rows + [extra], plan.upserts, contract)
    assert c.counts["new"] == 1


def test_a_disappeared_row_is_missing_not_deleted(clean_rows, contract, first_import):
    rows, _, plan = first_import
    c = CMP.compare(rows[:-1], plan.upserts, contract)
    assert c.counts["missing"] == 1
    assert c.of("missing")[0].previous is not None


def test_a_returning_row_is_restored(clean_rows, contract, first_import, now):
    """Without a fifth outcome a returning row looks unchanged and the week it vanished
    is lost."""
    rows, _, plan = first_import
    gone = CMP.compare(rows[:-1], plan.upserts, contract)
    after = PUB.plan_publish(gone, contract, "imp_002", now, current=plan.upserts)
    back = CMP.compare(rows, after.upserts, contract)
    assert back.counts["restored"] == 1


def test_comparing_without_keys_raises(clean_rows, contract):
    rows, _, _, _ = clean_rows
    stripped = [{k: v for k, v in r.items() if k != "timeline_item_id"} for r in rows]
    with pytest.raises(ValueError):
        CMP.compare(stripped, [], contract)


def test_bookkeeping_columns_do_not_count_as_changes(contract):
    """last_seen_date moves on every import by design. If it counted, every row would
    look changed every week."""
    excluded = CMP.official_fields(contract)
    for name in ("last_seen_date", "first_seen_date", "row_hash", "source_import_id"):
        assert name not in excluded


def test_row_hash_ignores_bookkeeping(contract, clean_rows):
    rows, _, _, _ = clean_rows
    fields = CMP.official_fields(contract)
    a = dict(rows[0], last_seen_date=datetime.date(2026, 1, 1))
    b = dict(rows[0], last_seen_date=datetime.date(2026, 9, 9))
    assert CMP.row_hash(a, fields) == CMP.row_hash(b, fields)


def test_the_headline_reads_like_a_sentence(clean_rows, contract, first_import):
    rows, comparison, _ = first_import
    assert "new" in comparison.headline()


# ---------------------------------------------------------------- publishing


def test_every_classified_row_is_accounted_for(first_import, contract):
    _, comparison, plan = first_import
    assert PUB.assert_idempotent(plan, comparison) is True


def test_no_key_is_written_twice(first_import):
    _, _, plan = first_import
    ids = [r["timeline_item_id"] for r in plan.upserts]
    assert len(ids) == len(set(ids))


def test_the_plan_matches_the_table_schema(first_import, contract):
    _, _, plan = first_import
    expected = {f.name for f in contract.fields("official_timeline")}
    assert set(plan.upserts[0].keys()) == expected


def test_no_team_field_is_ever_written(first_import, contract):
    _, _, plan = first_import
    assert PUB.assert_no_team_fields(plan, contract) is True


def test_the_guard_refuses_a_team_field(contract):
    """The charter says the protection rule is proven by a test rather than by
    inspection. This is that test."""
    plan = PUB.Plan(import_id="x")
    plan.upserts = [{"timeline_item_id": "a", "current_status": "In Progress"}]
    with pytest.raises(AssertionError):
        PUB.assert_no_team_fields(plan, contract)


def test_the_guard_refuses_a_duplicate_key(first_import, contract):
    _, comparison, plan = first_import
    plan.upserts.append(dict(plan.upserts[0]))
    with pytest.raises(AssertionError):
        PUB.assert_idempotent(plan, comparison)


def test_a_new_row_gets_a_change_log_entry(first_import):
    _, comparison, plan = first_import
    assert len(plan.changes) == comparison.counts["new"]
    assert all(c["change_type"] == "new" for c in plan.changes)


def test_an_unchanged_import_writes_no_change_log(clean_rows, contract, first_import, now):
    rows, _, plan = first_import
    again = CMP.compare(rows, plan.upserts, contract)
    plan2 = PUB.plan_publish(again, contract, "imp_002", now, current=plan.upserts)
    assert plan2.changes == []


def test_an_unchanged_import_still_writes_every_row(clean_rows, contract, first_import, now):
    """last_seen_date has to move even when nothing else did."""
    rows, _, plan = first_import
    again = CMP.compare(rows, plan.upserts, contract)
    plan2 = PUB.plan_publish(again, contract, "imp_002", now, current=plan.upserts)
    assert len(plan2.upserts) == len(rows)


def test_first_seen_date_survives_later_imports(clean_rows, contract, first_import, now):
    rows, _, plan = first_import
    later = datetime.datetime(2026, 9, 3, 9, 0)
    again = CMP.compare(rows, plan.upserts, contract)
    plan2 = PUB.plan_publish(again, contract, "imp_002", later, current=plan.upserts)
    by_id = {r["timeline_item_id"]: r for r in plan2.upserts}
    for original in plan.upserts:
        assert by_id[original["timeline_item_id"]]["first_seen_date"] == \
               original["first_seen_date"]


def test_last_seen_date_moves(clean_rows, contract, first_import, now):
    rows, _, plan = first_import
    later = datetime.datetime(2026, 9, 3, 9, 0)
    again = CMP.compare(rows, plan.upserts, contract)
    plan2 = PUB.plan_publish(again, contract, "imp_002", later, current=plan.upserts)
    assert all(r["last_seen_date"] == later.date() for r in plan2.upserts)


def test_a_missing_row_is_flagged_and_kept(clean_rows, contract, first_import, now):
    rows, _, plan = first_import
    c = CMP.compare(rows[:-1], plan.upserts, contract)
    plan2 = PUB.plan_publish(c, contract, "imp_002", now, current=plan.upserts)
    assert len(plan2.upserts) == len(rows), "the missing row must still be written"
    flagged = [r for r in plan2.upserts if r["is_missing"]]
    assert len(flagged) == 1


def test_a_missing_row_keeps_its_old_last_seen_date(clean_rows, contract, first_import):
    rows, _, plan = first_import
    later = datetime.datetime(2026, 9, 3, 9, 0)
    c = CMP.compare(rows[:-1], plan.upserts, contract)
    plan2 = PUB.plan_publish(c, contract, "imp_002", later, current=plan.upserts)
    flagged = [r for r in plan2.upserts if r["is_missing"]][0]
    assert flagged["last_seen_date"] != later.date(), \
        "it was not seen today, so last_seen_date must not move"


def test_a_changed_row_logs_one_entry_per_field(clean_rows, contract, first_import, now):
    rows, _, plan = first_import
    week2 = copy.deepcopy(rows)
    week2[0]["production_launch_date"] = datetime.date(2026, 12, 10)
    week2[0]["official_status"] = "Complete"
    c = CMP.compare(week2, plan.upserts, contract)
    plan2 = PUB.plan_publish(c, contract, "imp_002", now, current=plan.upserts)
    assert len(plan2.changes) == 2
    assert {ch["field_name"] for ch in plan2.changes} == \
           {"production_launch_date", "official_status"}


def test_a_change_log_entry_records_both_values(clean_rows, contract, first_import, now):
    rows, _, plan = first_import
    week2 = copy.deepcopy(rows)
    week2[0]["production_launch_date"] = datetime.date(2026, 12, 10)
    c = CMP.compare(week2, plan.upserts, contract)
    plan2 = PUB.plan_publish(c, contract, "imp_002", now, current=plan.upserts)
    entry = plan2.changes[0]
    assert entry["old_value"] == "2026-09-03"
    assert entry["new_value"] == "2026-12-10"
    assert entry["import_id"] == "imp_002"


def test_import_id_is_readable(now):
    assert PUB.make_import_id(now, 1) == "imp_2026_08_27_001"


def test_the_import_run_record_matches_the_dictionary(first_import, contract, now):
    _, comparison, plan = first_import
    PUB.build_import_run(plan, comparison, file_name="f.xlsx", file_ext="xlsx",
                         fhash="h", sheet_name="RSP Dates", rows_read=6,
                         provisional=1, divergences=1, status="approved",
                         validated_at=now, approved_by="k.salik", approved_at=now)
    expected = {f.name for f in contract.fields("import_run")}
    assert set(plan.import_run.keys()) == expected


def test_the_import_run_counts_match_the_comparison(first_import, contract, now):
    _, comparison, plan = first_import
    PUB.build_import_run(plan, comparison, file_name="f.xlsx", file_ext="xlsx",
                         fhash="h", sheet_name="RSP Dates", rows_read=6,
                         provisional=1, divergences=1, status="approved",
                         validated_at=now, approved_by="k.salik", approved_at=now)
    assert plan.import_run["rows_new"] == comparison.counts["new"]
    assert plan.import_run["approved_by"] == "k.salik"


def test_file_hash_is_stable(tmp_path):
    p = tmp_path / "a.bin"
    p.write_bytes(b"hello tracker")
    assert PUB.file_hash(str(p)) == PUB.file_hash(str(p))


def test_file_hash_changes_with_content(tmp_path):
    a, b = tmp_path / "a.bin", tmp_path / "b.bin"
    a.write_bytes(b"one")
    b.write_bytes(b"two")
    assert PUB.file_hash(str(a)) != PUB.file_hash(str(b))
