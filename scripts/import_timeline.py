"""The weekly import, all six steps.

    python scripts/import_timeline.py                      # newest workbook, report only
    python scripts/import_timeline.py --file sample.xlsx   # a specific workbook
    python scripts/import_timeline.py --approve --by kawtar

    | Step     |                                                                        |
    |----------|------------------------------------------------------------------------|
    | 0 gate   | file type, sheet, the 24 expected columns, and whether this file was    |
    |          | already imported                                                       |
    | 1 raw    | read exactly what arrived                                              |
    | 2 clean  | the thirteen rules, logging every change                               |
    | 3 key    | build the key, check uniqueness                                        |
    | 4 compare| against the current table, BY KEY, never by row position                |
    | 5 report | what would change, for a person to read                                |
    | 6 approve| nothing is written without --approve                                   |

Run it once to read the comparison, then run it again with --approve.
Re-running the same file is safe: every row is keyed, so writing is an upsert.

WHY IT IS SPLIT THIS WAY

Steps 0 to 5 are calls into src/tracker and touch no database at all. Everything that can
be got wrong (which rows are new, what changed, what the key is, what the cleaning did) is
worked out in plain Python first and printed for a person to read. Only step 6 writes, and
only after someone has passed --approve.

That is also what makes the risky half testable: the whole comparison can be checked
without a database anywhere near it.
"""

import argparse
import datetime
import glob
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
for p in (os.path.join(PROJECT, "src"), os.path.join(PROJECT, "conf"),
          os.path.join(PROJECT, "app")):
    if p not in sys.path:
        sys.path.insert(0, p)

import openpyxl  # noqa: E402

import config  # noqa: E402
from lib import db  # noqa: E402
from tracker.contract import load  # noqa: E402
from tracker import clean as C  # noqa: E402
from tracker import compare as CMP  # noqa: E402
from tracker import keys as K  # noqa: E402
from tracker import publish as PUB  # noqa: E402


def slug(header):
    s = re.sub(r"[^0-9a-zA-Z]+", "_", str(header).strip().lower()).strip("_")
    return re.sub(r"_+", "_", s)


def ordered(rows, columns):
    """One dict per row with exactly these keys, in this order.

    insert_many reads the column list off the first row, so every row has to agree, and a
    row that happens to be missing an optional field would otherwise shift every value.
    """
    return [{c: r.get(c) for c in columns} for r in rows]


def find_workbook(named):
    """A named file, else the newest workbook in landing/, else in sample_data/."""
    if named:
        for folder in (config.LANDING, config.SAMPLE_DATA, ""):
            candidate = os.path.join(folder, named) if folder else named
            if os.path.exists(candidate):
                return candidate
        raise SystemExit(f"No such workbook: {named}")

    for folder in (config.LANDING, config.SAMPLE_DATA):
        found = sorted(glob.glob(os.path.join(folder, "*.xlsx")))
        if found:
            return found[-1]

    raise SystemExit(
        f"No .xlsx found in {config.LANDING} or {config.SAMPLE_DATA}.\n"
        "Put the weekly workbook in landing/, or run scripts/make_sample_data.py first.")


def main():
    ap = argparse.ArgumentParser(description="Import the official RSM timeline export.")
    ap.add_argument("--file", help="a workbook name, default is the newest one found")
    ap.add_argument("--approve", action="store_true", help="actually write. Off by default.")
    ap.add_argument("--by", dest="approved_by",
                    help="who is approving. Required with --approve.")
    ap.add_argument("--force", action="store_true",
                    help="allow a file whose hash was already imported")
    args = ap.parse_args()

    if args.approve and not args.approved_by:
        raise SystemExit(
            "--approve needs --by NAME. An approval needs a name against it, because the "
            "import_run record keeps who accepted the change.")

    NOW = datetime.datetime.now()
    print(config.describe())
    print("run at:", NOW.isoformat(timespec="seconds"))

    contract = load(config.DICTIONARY)
    print(contract.summary())

    # ------------------------------------------------------------ step 0: the gate

    path = find_workbook(args.file)
    print(f"\nfile: {os.path.basename(path)} ({os.path.getsize(path) / 1024:.1f} KB)")

    FHASH = PUB.file_hash(path)
    print("hash:", FHASH[:16], "...")

    wb = openpyxl.load_workbook(path, data_only=True)
    if config.SOURCE_SHEET not in wb.sheetnames:
        raise SystemExit(
            f"Sheet {config.SOURCE_SHEET!r} not found. This is not the official export.\n"
            f"The workbook contains: {', '.join(wb.sheetnames)}")

    ws = wb[config.SOURCE_SHEET]
    headers = [c.value for c in ws[1]]
    missing, unexpected = contract.check_source_columns(headers)
    if missing:
        for h in missing:
            print("  MISSING COLUMN:", h)
        raise SystemExit("Gate failed. Expected columns are missing, so nothing is loaded.")
    for h in unexpected:
        print("  not in the contract, ignored:", h)
    print("Gate passed.")

    IMPORT_RUN = config.t("core", "import_run")
    prior = db.query(
        f"SELECT import_id, approved_at FROM {IMPORT_RUN} "
        f"WHERE file_hash = :h AND status = 'approved'", {"h": FHASH})
    if not prior.empty and not args.force:
        p = prior.iloc[0]
        print(f"\nThis exact file was already imported and approved as {p['import_id']} "
              f"on {p['approved_at']}.")
        print("Re-running is harmless, every row is keyed, but nothing will change.")
        print("Pass --force if you meant to do this.")
        if args.approve:
            raise SystemExit("Refusing to approve the same file twice. See above.")

    # ------------------------------------------------------ steps 1 to 3: raw, clean, key

    raw_rows = [dict(zip(headers, r))
                for r in ws.iter_rows(min_row=2, values_only=True) if r[0]]
    known_ids = {str(r[0]).strip().upper()
                 for r in wb[config.INSTRUMENT_SHEET].iter_rows(min_row=2, values_only=True)
                 if r[0]}
    print(f"\n{len(raw_rows)} data rows, {len(known_ids)} known instrument ids\n")

    rows, log, divergences, errors = C.clean_rows(raw_rows, contract, known_ids=known_ids)
    print(C.summarize(rows, log, divergences, errors))

    if errors:
        raise SystemExit(
            f"\n{len(errors)} validation error(s). Nothing is loaded. Either the data needs "
            f"fixing upstream, or the dictionary is wrong and should be extended.")

    K.add_keys(rows)
    K.check_unique(rows)
    provisional = [r for r in rows if r["key_is_provisional"]]
    print(f"\n{len(rows)} rows, {len({r['timeline_item_id'] for r in rows})} distinct keys, "
          f"{len(provisional)} provisional")

    # ------------------------------------------------------------ step 4: compare

    OFFICIAL = config.t("core", "official_timeline")
    names, values = db.execute(f"SELECT * FROM {OFFICIAL}")
    current = [dict(zip(names, v)) for v in values]
    print(f"\n{len(current)} rows currently in official_timeline\n")

    comparison = CMP.compare(rows, current, contract)
    print(comparison.headline())

    # ------------------------------------------------------------ step 5: the report

    print()
    print(comparison.report())

    # ------------------------------------------------------------ build the plan

    seq = int(db.scalar(
        f"SELECT COUNT(*) FROM {IMPORT_RUN} WHERE import_id LIKE :p",
        {"p": f"imp_{NOW:%Y_%m_%d}_%"}, default=0)) + 1
    IMPORT_ID = PUB.make_import_id(NOW, seq)
    print("\nimport id:", IMPORT_ID)

    plan = PUB.plan_publish(comparison, contract, IMPORT_ID, NOW, current=current)
    PUB.add_staging(plan, raw_rows, rows, log, contract, NOW, slug)
    PUB.build_import_run(
        plan, comparison,
        file_name=os.path.basename(path), file_ext="xlsx", fhash=FHASH,
        sheet_name=config.SOURCE_SHEET, rows_read=len(raw_rows),
        provisional=len(provisional), divergences=len(divergences),
        status="approved" if args.approve else "validated",
        validated_at=NOW,
        approved_by=args.approved_by if args.approve else None,
        approved_at=NOW if args.approve else None,
        source_file_path=path,
    )
    print(plan.summary())

    # The two guarantees, checked rather than assumed.
    PUB.assert_no_team_fields(plan, contract)
    PUB.assert_idempotent(plan, comparison)
    print("\nGuards passed: no Team owned column is written, and no key is written twice.")

    # ------------------------------------------------------ step 6: write, only if approved

    if not args.approve:
        print("\nNothing was written.")
        print("Read the comparison above. If it is right, run again with")
        print(f"  python scripts/import_timeline.py --file {os.path.basename(path)} "
              f"--approve --by your.name")
        return

    print(f"\nApproved by {args.approved_by}. Writing.\n")

    ot_cols = [f.name for f in contract.fields("official_timeline")]
    cl_cols = [f.name for f in contract.fields("timeline_change_log")]
    ir_cols = [f.name for f in contract.fields("import_run")]
    raw_cols = ["import_id", "source_row_number"] + [slug(h) for h in contract.source_columns()]
    skip = {"first_seen_date", "last_seen_date", "is_missing", "source_import_id"}
    clean_cols = ["import_id", "source_row_number"] + [c for c in ot_cols if c not in skip]
    log_cols = ["import_id", "source_row_number", "natural_key", "field_name", "rule_id",
                "raw_value", "cleaned_value", "cleaned_at"]

    def replace_import(table, rows_, columns):
        """Delete anything already recorded for this import, then insert. Makes a re-run of
        the same import_id produce one set of rows rather than two."""
        db.delete_where(table, "import_id", IMPORT_ID)
        return db.insert_many(table, ordered(rows_, columns))

    # One transaction. Either the whole import lands or none of it does, so a failure
    # halfway cannot leave official_timeline updated with no change log to explain it.
    #
    # PARENTS FIRST. The tempting order is to write import_run last, so that a failure
    # partway through leaves no record claiming the import succeeded. That instinct is
    # right and the transaction already delivers it: if anything below fails, none of it
    # lands. Writing the parent last would only mean official_timeline.source_import_id
    # pointing at a row that does not exist yet, which the foreign key refuses outright.
    with db.transaction():
        # --- import_run first, because everything else points at it
        db.delete_where(IMPORT_RUN, "import_id", IMPORT_ID)
        db.insert_many(IMPORT_RUN, ordered([plan.import_run], ir_cols))
        print(f"  import_run                1 row")

        # --- official_timeline, upsert by key. See db.upsert for why it is not a
        #     delete followed by an insert.
        n = db.upsert(OFFICIAL, ordered(plan.upserts, ot_cols), "timeline_item_id")
        print(f"  official_timeline     {n:>5} rows upserted")

        n = replace_import(config.t("core", "timeline_change_log"), plan.changes, cl_cols)
        print(f"  timeline_change_log   {n:>5} rows")

        n = replace_import(config.t("raw", "timeline_import"), plan.raw, raw_cols)
        print(f"  raw_timeline_import   {n:>5} rows")

        n = replace_import(config.t("clean", "timeline_import"), plan.clean, clean_cols)
        print(f"  clean_timeline_import {n:>5} rows")

        n = replace_import(config.t("clean", "cleaning_log"), plan.cleaning_log, log_cols)
        print(f"  cleaning_log          {n:>5} rows")

    print("\nWritten.")

    # ------------------------------------------------------------ verify

    print("\nRow counts after the import:\n")
    for layer, name in [("core", "official_timeline"), ("core", "timeline_change_log"),
                        ("core", "import_run"), ("core", "data_work_items"),
                        ("core", "team_updates"), ("raw", "timeline_import"),
                        ("clean", "timeline_import"), ("clean", "cleaning_log")]:
        owner = "team" if name in ("data_work_items", "team_updates") else ""
        print(f"  {layer + '.' + name:<30} {db.table_count(config.t(layer, name)):>6}  {owner}")
    print("\n  The two team tables must stay at whatever the team put there. An import")
    print("  that changed them would be a governance failure, not a bug.")

    print(f"\n  flagged missing    "
          f"{db.scalar(f'SELECT COUNT(*) FROM {OFFICIAL} WHERE is_missing', default=0):>8}")
    print(f"  provisional keys   "
          f"{db.scalar(f'SELECT COUNT(*) FROM {OFFICIAL} WHERE key_is_provisional', default=0):>8}")

    moved = db.query(
        f"SELECT field_name, COUNT(*) AS n FROM {config.t('core', 'timeline_change_log')} "
        f"WHERE import_id = :i AND change_type = 'changed' "
        f"GROUP BY field_name ORDER BY n DESC", {"i": IMPORT_ID})
    print("\nOfficial date changes recorded this run:")
    print("  none" if moved.empty else moved.to_string(index=False))

    # ------------------------------------------------------------ backup

    if getattr(config, "BACKUP_AFTER_IMPORT", False):
        dest = os.path.join(config.BACKUP, NOW.strftime("%Y-%m-%d"))
        os.makedirs(dest, exist_ok=True)
        for name in ["official_timeline", "timeline_change_log", "import_run",
                     "data_work_items", "team_updates"]:
            pdf = db.query(f"SELECT * FROM {config.t('core', name)}")
            pdf.to_csv(os.path.join(dest, f"{name}.csv"), index=False)
            print(f"  {name:<24} {len(pdf):>6} rows -> {name}.csv")
        print(f"\nBackup written to {dest}")

    # ------------------------------------------------------------ divergences

    if divergences:
        print("\nThese were reported, not corrected. They are a disagreement between two "
              "documents, so a person decides, not the pipeline.\n")
        for d in divergences:
            print(f"  row {d.source_row_number}: {d.value!r}, SRS 3.1 says {d.expected!r}")
        print("\nRaise them with whoever owns the timeline. Do not edit the export yourself.")


if __name__ == "__main__":
    main()
