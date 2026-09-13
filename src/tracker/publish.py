"""Steps 5 and 6: turn an approved comparison into exactly the rows to write.

The planning here is pure Python and touches no database. That is deliberate: it means the
publish logic can be tested on its own, which is where most of the risk lives. The import
script does the writing.

Two guarantees this module is responsible for.

**A Data Team field is never written.** Not by convention, but by a check that inspects
every column in the plan against the dictionary and refuses if any of them is Team owned.
The charter's success measure says this is proven by an automated test rather than by
inspection, and `assert_no_team_fields` is that test.

**Running the same file twice changes nothing.** Every row is keyed, so writing is an
upsert rather than an append, and `import_run.file_hash` lets the caller notice a repeat
before it even starts.
"""

from dataclasses import dataclass, field as _field
from datetime import datetime, date
import hashlib
import uuid
from typing import Any, Dict, List, Optional

from .compare import Comparison, official_fields, row_hash, _canon


# ---------------------------------------------------------------- identifiers


def make_import_id(when: datetime, sequence: int = 1) -> str:
    """Readable on purpose, so it can be quoted in a conversation."""
    return f"imp_{when:%Y_%m_%d}_{sequence:03d}"


def file_hash(path: str, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


# ---------------------------------------------------------------- the plan


@dataclass
class Plan:
    import_id: str
    upserts: List[dict] = _field(default_factory=list)      # official_timeline
    changes: List[dict] = _field(default_factory=list)      # timeline_change_log
    import_run: dict = _field(default_factory=dict)         # import_run, one row
    raw: List[dict] = _field(default_factory=list)          # raw_timeline_import
    clean: List[dict] = _field(default_factory=list)        # clean_timeline_import
    cleaning_log: List[dict] = _field(default_factory=list) # clean_cleaning_log

    def summary(self) -> str:
        return (f"import {self.import_id}: "
                f"{len(self.upserts)} rows to upsert, "
                f"{len(self.changes)} change log rows, "
                f"{len(self.raw)} raw rows, {len(self.cleaning_log)} cleaning entries")


def plan_publish(comparison: Comparison,
                 contract,
                 import_id: str,
                 now: datetime,
                 current: List[dict] = None) -> Plan:
    """Build every row that will be written, and nothing else.

    Nothing here touches a database. Hand the result to the import script to write, or to a
    test to inspect.
    """
    fields = official_fields(contract)
    current_by_key = {r["timeline_item_id"]: r for r in (current or [])
                      if r.get("timeline_item_id")}
    today = now.date()
    plan = Plan(import_id=import_id)

    def stamp(row: dict, prev: Optional[dict], is_missing: bool = False) -> dict:
        out = {f: row.get(f) for f in fields}
        out["timeline_item_id"] = row["timeline_item_id"]
        out["row_hash"] = row_hash(row, fields)
        out["first_seen_date"] = (prev or {}).get("first_seen_date") or today
        out["last_seen_date"] = today
        out["is_missing"] = is_missing
        out["source_import_id"] = import_id
        return out

    def change(tid, field_name, old, new, change_type):
        return {
            "change_id": str(uuid.uuid4()),
            "timeline_item_id": tid,
            "field_name": field_name,
            "old_value": _canon(old) or None,
            "new_value": _canon(new) or None,
            "change_type": change_type,
            "import_id": import_id,
            "changed_at": now,
        }

    # --- new
    for r in comparison.of("new"):
        plan.upserts.append(stamp(r.row, None))
        plan.changes.append(change(r.timeline_item_id, "*", None, None, "new"))

    # --- changed, one change row per field that moved
    for r in comparison.of("changed"):
        plan.upserts.append(stamp(r.row, r.previous))
        for ch in r.changes:
            plan.changes.append(
                change(r.timeline_item_id, ch.field, ch.old, ch.new, "changed"))

    # --- restored
    for r in comparison.of("restored"):
        plan.upserts.append(stamp(r.row, r.previous))
        plan.changes.append(change(r.timeline_item_id, "is_missing", True, False, "restored"))
        for ch in r.changes:
            plan.changes.append(
                change(r.timeline_item_id, ch.field, ch.old, ch.new, "changed"))

    # --- unchanged, last_seen_date moves and nothing else
    for r in comparison.of("unchanged"):
        plan.upserts.append(stamp(r.row, r.previous))

    # --- missing, flagged and kept. Work items point at these.
    for r in comparison.of("missing"):
        prev = r.previous
        out = {f: prev.get(f) for f in fields}
        out["timeline_item_id"] = r.timeline_item_id
        out["row_hash"] = prev.get("row_hash") or row_hash(prev, fields)
        out["first_seen_date"] = prev.get("first_seen_date") or today
        out["last_seen_date"] = prev.get("last_seen_date")   # it was not seen today
        out["is_missing"] = True
        out["source_import_id"] = import_id
        plan.upserts.append(out)
        plan.changes.append(change(r.timeline_item_id, "is_missing", False, True, "missing"))

    return plan


def add_staging(plan: Plan, raw_rows: List[dict], clean_rows: List[dict],
                cleaning_log, contract, now: datetime, slug) -> Plan:
    """Attach the raw, clean and cleaning log rows for this import."""
    for i, r in enumerate(raw_rows, start=2):
        out = {"import_id": plan.import_id, "source_row_number": i}
        for header, value in r.items():
            if header:
                out[slug(header)] = None if value is None else str(value)
        plan.raw.append(out)

    skip = {"first_seen_date", "last_seen_date", "is_missing", "source_import_id"}
    keep = [f.name for f in contract.fields("official_timeline") if f.name not in skip]
    for r in clean_rows:
        out = {"import_id": plan.import_id,
               "source_row_number": r.get("source_row_number")}
        for f in keep:
            out[f] = r.get(f)
        plan.clean.append(out)

    for e in cleaning_log:
        plan.cleaning_log.append(e.as_row(plan.import_id, now))

    return plan


def build_import_run(plan: Plan, comparison: Comparison, *, file_name: str,
                     file_ext: str, fhash: str, sheet_name: str, rows_read: int,
                     provisional: int, divergences: int, status: str,
                     validated_at: datetime, approved_by: str = None,
                     approved_at: datetime = None, error_detail: str = None,
                     source_file_path: str = "") -> dict:
    c = comparison.counts
    plan.import_run = {
        "import_id": plan.import_id,
        "file_name": file_name,
        "file_extension": file_ext,
        "file_hash": fhash,
        "sheet_name": sheet_name,
        "rows_read": rows_read,
        "rows_new": c["new"],
        "rows_changed": c["changed"] + c["restored"],
        "rows_unchanged": c["unchanged"],
        "rows_missing": c["missing"],
        "rows_provisional_key": provisional,
        "cleaning_actions": len(plan.cleaning_log),
        "conflicts_reported": divergences,
        "status": status,
        "validated_at": validated_at,
        "approved_by": approved_by,
        "approved_at": approved_at,
        "error_detail": error_detail,
        "source_file_path": source_file_path,
    }
    return plan.import_run


# ---------------------------------------------------------------- the guard


def team_owned_fields(contract) -> set:
    out = set()
    for table in ("data_work_items", "team_updates"):
        for f in contract.fields(table):
            if f.owner == "Team":
                out.add(f.name)
    return out


def assert_no_team_fields(plan: Plan, contract):
    """Refuse to write anything a person entered.

    The governing rule of the whole project is that an import may update official fields
    only. This checks it mechanically rather than trusting that the code above happens to
    be correct.
    """
    team = team_owned_fields(contract)
    official = set(official_fields(contract)) | {
        "timeline_item_id", "row_hash", "first_seen_date", "last_seen_date",
        "is_missing", "source_import_id",
    }

    offences = []
    for i, row in enumerate(plan.upserts):
        for col in row:
            if col in team and col not in official:
                offences.append(f"upsert row {i} would write the Team owned column {col!r}")
            if col not in official:
                offences.append(f"upsert row {i} contains {col!r}, which is not an "
                                f"official_timeline field")

    if offences:
        raise AssertionError(
            "This plan would write outside the official record, which the governing rule "
            "forbids:\n  " + "\n  ".join(sorted(set(offences))[:20])
        )
    return True


def assert_idempotent(plan: Plan, comparison: Comparison):
    """One row per key, and no key written twice."""
    ids = [r["timeline_item_id"] for r in plan.upserts]
    if len(ids) != len(set(ids)):
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        raise AssertionError(f"the plan writes the same key more than once: {dupes[:10]}")
    expected = len(comparison.results)
    if len(ids) != expected:
        raise AssertionError(
            f"the plan has {len(ids)} upserts but the comparison classified {expected} rows. "
            f"Every classified row must be accounted for, including the missing ones."
        )
    return True
