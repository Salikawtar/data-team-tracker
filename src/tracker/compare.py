"""Step 4: what changed since the last approved import.

Matching is by key, never by row position. The official export is sorted by Priority and
priority is re-ranked between exports, so rows move every week without changing. Comparing
row 2 to row 2 would report the whole file as changed and hide the handful of things that
actually did.

Five outcomes for every row:

    new         the key has never been seen
    changed     the key exists and at least one official field differs
    unchanged   the key exists and every official field matches
    missing     the key was in the last import and is not in this one
    restored    the key was marked missing and has come back

`missing` never deletes anything. Data Team work items point at these rows, and deleting
one would orphan real work. The row is flagged and kept.
"""

from dataclasses import dataclass, field as _field
from datetime import date, datetime
import hashlib
from typing import Any, List, Optional


def official_fields(contract) -> List[str]:
    """The fields a change can meaningfully happen to.

    Defined once, in contract.py, because the same list is also the allowed value list for
    timeline_change_log.field_name. Two copies would disagree the first time a field was
    added.
    """
    return contract.changeable_fields()


def _canon(value: Any) -> str:
    """One text form per value, so a date and its string never look different."""
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value).strip()


def row_hash(row: dict, fields: List[str], length: int = 16) -> str:
    """One hash over every official field, so a changed row is found in one comparison
    rather than thirty."""
    joined = "\x1f".join(_canon(row.get(f)) for f in fields)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:length]


@dataclass
class FieldChange:
    field: str
    old: Any
    new: Any

    def __str__(self):
        return f"{self.field}: {_canon(self.old) or '(empty)'} -> {_canon(self.new) or '(empty)'}"


@dataclass
class RowResult:
    timeline_item_id: str
    status: str                      # new, changed, unchanged, missing, restored
    row: Optional[dict] = None       # the incoming row, absent when missing
    previous: Optional[dict] = None  # the stored row, absent when new
    changes: List[FieldChange] = _field(default_factory=list)


@dataclass
class Comparison:
    fields: List[str]
    results: List[RowResult] = _field(default_factory=list)

    def of(self, status: str):
        return [r for r in self.results if r.status == status]

    @property
    def counts(self):
        c = {"new": 0, "changed": 0, "unchanged": 0, "missing": 0, "restored": 0}
        for r in self.results:
            c[r.status] += 1
        return c

    @property
    def has_changes(self) -> bool:
        c = self.counts
        return bool(c["new"] or c["changed"] or c["missing"] or c["restored"])

    def changes_by_field(self):
        out = {}
        for r in self.of("changed"):
            for ch in r.changes:
                out[ch.field] = out.get(ch.field, 0) + 1
        return dict(sorted(out.items(), key=lambda kv: -kv[1]))

    def date_changes(self):
        """Schedule movement specifically, which is the question the change log exists for."""
        return [(r, ch) for r in self.of("changed") for ch in r.changes
                if ch.field.endswith("_date") or ch.field.endswith("_deadline")]

    # ---------------------------------------------------------------- reporting

    def headline(self) -> str:
        c = self.counts
        parts = []
        if c["new"]:
            parts.append(f"{c['new']} new")
        if c["changed"]:
            nd = len(self.date_changes())
            parts.append(f"{c['changed']} changed" + (f" ({nd} date changes)" if nd else ""))
        if c["restored"]:
            parts.append(f"{c['restored']} restored")
        if c["missing"]:
            parts.append(f"{c['missing']} now missing")
        parts.append(f"{c['unchanged']} unchanged")
        return ", ".join(parts)

    def report(self, limit: int = 40) -> str:
        c = self.counts
        L = ["=" * 74, "COMPARISON", "=" * 74, self.headline(), ""]

        if c["new"]:
            L.append(f"NEW ({c['new']})")
            for r in self.of("new")[:limit]:
                row = r.row
                L.append(f"  {r.timeline_item_id}  {row.get('instrument_id'):<8} "
                         f"{str(row.get('fiscal_year')):<7} "
                         f"{str(row.get('implementation_need'))[:48]}")
            if c["new"] > limit:
                L.append(f"  ... and {c['new'] - limit} more")
            L.append("")

        if c["changed"]:
            L.append(f"CHANGED ({c['changed']})")
            for r in self.of("changed")[:limit]:
                row = r.row
                L.append(f"  {r.timeline_item_id}  {row.get('instrument_id')} "
                         f"{str(row.get('instrument_short_name_en') or '')}")
                for ch in r.changes:
                    L.append(f"      {ch}")
            if c["changed"] > limit:
                L.append(f"  ... and {c['changed'] - limit} more")
            L.append("")
            L.append("  changed fields, most often first:")
            for f, n in self.changes_by_field().items():
                L.append(f"      {f:<28} {n:>3}")
            L.append("")

        if c["restored"]:
            L.append(f"RESTORED ({c['restored']})")
            for r in self.of("restored")[:limit]:
                L.append(f"  {r.timeline_item_id}  {r.row.get('instrument_id')} "
                         f"was marked missing, has reappeared")
            L.append("")

        if c["missing"]:
            L.append(f"NOW MISSING ({c['missing']})")
            L.append("  These are flagged, never deleted. Work items point at them.")
            for r in self.of("missing")[:limit]:
                prev = r.previous
                L.append(f"  {r.timeline_item_id}  {prev.get('instrument_id'):<8} "
                         f"{str(prev.get('implementation_need'))[:48]}")
            if c["missing"] > limit:
                L.append(f"  ... and {c['missing'] - limit} more")
            L.append("")

        if not self.has_changes:
            L.append("Nothing has changed since the last approved import.")
            L.append("")

        L.append("No Data Team field is affected by any import. Those live in separate tables")
        L.append("that this pipeline does not write to.")
        L.append("=" * 74)
        return "\n".join(L)


def compare(incoming: List[dict], current: List[dict], contract) -> Comparison:
    """Classify every row.

    `incoming` is the cleaned and keyed rows from this file.
    `current` is what is in official_timeline now, as dicts. Empty on the first import.
    """
    fields = official_fields(contract)

    by_key_current = {}
    for r in current:
        tid = r.get("timeline_item_id")
        if tid:
            by_key_current[tid] = r

    seen = set()
    results = []

    for row in incoming:
        tid = row.get("timeline_item_id")
        if not tid:
            raise ValueError("an incoming row has no timeline_item_id. "
                             "Run keys.add_keys before comparing.")
        if tid in seen:
            raise ValueError(f"duplicate key {tid} in the incoming rows. "
                             f"keys.check_unique should have caught this.")
        seen.add(tid)

        prev = by_key_current.get(tid)
        if prev is None:
            results.append(RowResult(tid, "new", row=row))
            continue

        changes = []
        for f in fields:
            old, new = prev.get(f), row.get(f)
            if _canon(old) != _canon(new):
                changes.append(FieldChange(f, old, new))

        was_missing = bool(prev.get("is_missing"))
        if was_missing:
            results.append(RowResult(tid, "restored", row=row, previous=prev, changes=changes))
        elif changes:
            results.append(RowResult(tid, "changed", row=row, previous=prev, changes=changes))
        else:
            results.append(RowResult(tid, "unchanged", row=row, previous=prev))

    for tid, prev in by_key_current.items():
        if tid not in seen and not prev.get("is_missing"):
            results.append(RowResult(tid, "missing", previous=prev))

    return Comparison(fields=fields, results=results)
