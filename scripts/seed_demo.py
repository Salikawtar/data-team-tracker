"""Put some Data Team work into the tracker, so a fresh clone opens on a working screen.

    python scripts/seed_demo.py [--items 14] [--seed 3] [--reset]

WHY THIS EXISTS

An import fills the official tables, but the Data Team tables stay empty until a person
opens the app and types something. So a fresh clone would open on five zeroes, and a
tracker nobody has used does not demonstrate anything.

This writes invented work items and status updates against the imported timeline, so the
Home screen opens with blocked items, overdue updates and a coverage percentage instead.

IT USES THE SAME PATH THE APP USES

Every row goes through team.new_work_item and team.new_update, so it is validated against
the data dictionary and passes assert_no_official_fields exactly as a row typed into the
app would. Nothing here writes SQL of its own. If this script can create a row, so can a
person, and if the guards would reject a person's row they reject this one too.

The updates are dated deliberately: some fresh, some a fortnight old, so the staleness
list and the 90 percent coverage target both have something to show.
"""

import argparse
import datetime
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
for p in (os.path.join(PROJECT, "src"), os.path.join(PROJECT, "conf"),
          os.path.join(PROJECT, "app")):
    if p not in sys.path:
        sys.path.insert(0, p)

import config  # noqa: E402
from lib import db  # noqa: E402
from tracker.contract import load  # noqa: E402
from tracker import team as T  # noqa: E402


OWNERS = ["a.rahimi", "j.okonkwo", "m.tremblay", "s.nakamura", "d.osei"]

DELIVERABLES = [
    "Bilingual data dictionary signed off by the program",
    "Star schema and load script in the development environment",
    "Field mapping from the submission form to the reporting tables",
    "UAT evidence pack with row counts per test case",
    "Power BI model refreshing on schedule",
    "Production activation checklist completed",
]

PROGRESS = [
    "Mapped the submission form fields and sent the draft to the program for review.",
    "Loaded a first extract and reconciled row counts against the source.",
    "Walked the analyst through the review workflow. Two fields still unnamed.",
    "Wrote the validation queries and ran them against the UAT data.",
    "Refresh is running nightly. Waiting on a gateway account before go live.",
    "Drafted the activation checklist. Nothing to hand over yet.",
]

BLOCKERS = [
    "Waiting on the program to confirm which field carries the facility identifier.",
    "No access to the UAT environment yet. Request raised with IT delivery.",
    "The French labels have not come back from translation.",
]

NEXT = [
    "Follow up at Thursday's working group.",
    "Send the reconciliation to the business analyst.",
    "Book thirty minutes with the product owner.",
]


def main():
    ap = argparse.ArgumentParser(description="Seed invented Data Team work.")
    ap.add_argument("--items", type=int, default=14, help="how many work items")
    ap.add_argument("--seed", type=int, default=3)
    ap.add_argument("--reset", action="store_true",
                    help="clear the two team tables first")
    args = ap.parse_args()

    rng = random.Random(args.seed)
    now = datetime.datetime.now()
    contract = load(config.DICTIONARY)

    WORK = config.t("core", "data_work_items")
    UPDATES = config.t("core", "team_updates")

    timeline = db.query(
        f"SELECT timeline_item_id, instrument_id, production_launch_date "
        f"FROM {config.t('core', 'official_timeline')} "
        f"WHERE NOT is_missing ORDER BY production_launch_date NULLS LAST")
    if timeline.empty:
        raise SystemExit(
            "official_timeline is empty. Run scripts/import_timeline.py --approve first.")

    if args.reset:
        # team_updates first: it points at data_work_items.
        with db.transaction():
            db.execute(f"DELETE FROM {UPDATES}")
            db.execute(f"DELETE FROM {WORK}")
        print("Cleared the two team tables.")

    # contract.allowed returns a set, and a set has no stable order, so sort before
    # sampling. Otherwise the same --seed gives a different demo on every run.
    work_types = sorted(contract.allowed("work_type"))
    statuses = sorted(s for s in contract.allowed("team_status") if s != "Complete")

    picked = timeline.head(args.items * 2).sample(
        n=min(args.items, len(timeline)), random_state=args.seed)

    items, updates = [], []
    for _, row in picked.iterrows():
        launch = row["production_launch_date"]
        target = (launch - datetime.timedelta(days=rng.randint(20, 60))
                  if launch is not None and launch == launch else None)
        status = rng.choice(statuses)

        item = T.new_work_item(
            contract,
            timeline_item_id=row["timeline_item_id"],
            work_type=rng.choice(work_types),
            data_owner_user_id=rng.choice(OWNERS),
            now=now - datetime.timedelta(days=rng.randint(20, 90)),
            planned_start_date=(target - datetime.timedelta(days=30)) if target else None,
            internal_target_date=target,
            current_status=status,
            required_deliverable=rng.choice(DELIVERABLES),
        )
        items.append(item)

        # Some items get an update, some never do. The ones that never do are the point
        # of the "owing an update" list, so they have to exist.
        if rng.random() < 0.25:
            continue

        # Fresh for most, stale for a few, so coverage lands near but not at 90 percent.
        age = rng.choice([0, 1, 2, 3, 4, 6, 11, 16, 24])
        blocked = status == "Blocked"
        updates.append(T.new_update(
            contract,
            data_work_item_id=item["data_work_item_id"],
            submitted_by_user_id=item["data_owner_user_id"],
            status_at_update=status,
            percent_complete=rng.choice([10, 25, 40, 55, 70, 85, None]),
            progress_update=rng.choice(PROGRESS),
            blocker=rng.choice(BLOCKERS) if blocked else None,
            next_action=rng.choice(NEXT),
            expected_completion_date=target,
            now=now - datetime.timedelta(days=age),
        ))

    with db.transaction():
        db.insert_many(WORK, items)
        db.insert_many(UPDATES, updates)

    print(f"Wrote {len(items)} work items and {len(updates)} updates.")

    cov = db.query(T.sql_coverage(db.t))
    stale = db.query(T.sql_stale_items(db.t, days=7))
    blocked = db.query(T.sql_blocked(db.t))
    print(f"\n  active items      {int(cov['active_items'].iloc[0] or 0)}")
    print(f"  update coverage   {cov['percent_fresh'].iloc[0]}%  (charter target is 90)")
    print(f"  owing an update   {len(stale)}")
    print(f"  blocked           {len(blocked)}")
    print("\nNow run:  streamlit run app/Home.py")


if __name__ == "__main__":
    main()
