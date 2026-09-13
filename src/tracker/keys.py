"""The key the source file does not have.

Instrument ID is not unique in the official export. Eighty two rows resolve to twenty four
instruments, because one instrument carries several implementation needs across fiscal
years. RSP-2 alone appears thirteen times.

So the tracker generates its own key, the same way, every week, from three fields that
together are unique: Instrument ID, RDC Implementation Need and Fiscal Year.

Everything here is deterministic. The same row produces the same ID next week with no
lookup table and no manual mapping, which is what makes week over week comparison possible
at all.
"""

import hashlib
import re
from typing import Iterable

try:
    import config
except ImportError:
    config = None


DEFAULT_KEY_FIELDS = ["instrument_id", "implementation_need", "fiscal_year"]
DEFAULT_SEPARATOR = "||"
DEFAULT_LENGTH = 16

# A key field carrying this is a placeholder, not a value. One row in the current export,
# RSP-17 P2 Notice, has it as its fiscal year.
PROVISIONAL_MARKERS = {"tbd", "tbc", "n/a"}


def _cfg(name, default):
    return getattr(config, name, default) if config is not None else default


def normalize(value) -> str:
    """Trim, collapse repeated whitespace, fold to lower case.

    Cleaning has already run by the time this is called. Normalizing again here is
    deliberate belt and braces: if a cleaning rule is ever relaxed, the key stays stable
    rather than silently changing for every row in the file.
    """
    if value is None:
        return ""
    s = str(value).replace("\n", " ").replace("\t", " ")
    s = re.sub(r"\s+", " ", s).strip().lower()
    return s


def natural_key(row: dict, key_fields: Iterable[str] = None, separator: str = None) -> str:
    """The three key values joined. Readable, and used in the cleaning log."""
    key_fields = list(key_fields or _cfg("NATURAL_KEY", DEFAULT_KEY_FIELDS))
    separator = separator or _cfg("KEY_SEPARATOR", DEFAULT_SEPARATOR)

    missing = [f for f in key_fields if f not in row]
    if missing:
        raise KeyError(f"row is missing key field(s) {missing}. "
                       f"The key cannot be built from an incomplete row.")

    parts = [normalize(row[f]) for f in key_fields]

    blank = [f for f, p in zip(key_fields, parts) if not p]
    if blank:
        raise ValueError(
            f"key field(s) {blank} are empty. Every row must carry all three key values, "
            f"even if one of them is a placeholder such as TBD."
        )

    if any(separator in p for p in parts):
        raise ValueError(
            f"a key value contains the separator {separator!r}, so two different rows "
            f"could produce the same key. Change KEY_SEPARATOR in config."
        )

    return separator.join(parts)


def timeline_item_id(row: dict, key_fields: Iterable[str] = None,
                     separator: str = None, length: int = None) -> str:
    """SHA-256 of the normalized natural key, truncated."""
    length = length or _cfg("KEY_LENGTH", DEFAULT_LENGTH)
    nk = natural_key(row, key_fields, separator)
    return hashlib.sha256(nk.encode("utf-8")).hexdigest()[:length]


def is_provisional(row: dict, key_fields: Iterable[str] = None) -> bool:
    """True when a key field holds a placeholder rather than a real value.

    This row's ID will change once the placeholder is replaced, and it will then present
    as one delete plus one insert. That is correct behaviour, but it should be visible in
    the review rather than a surprise a month later.
    """
    key_fields = list(key_fields or _cfg("NATURAL_KEY", DEFAULT_KEY_FIELDS))
    return any(normalize(row.get(f)) in PROVISIONAL_MARKERS for f in key_fields)


def add_keys(rows, key_fields: Iterable[str] = None):
    """Stamp timeline_item_id and key_is_provisional onto every row, in place."""
    for r in rows:
        r["timeline_item_id"] = timeline_item_id(r, key_fields)
        r["key_is_provisional"] = is_provisional(r, key_fields)
    return rows


def find_duplicates(rows, key_fields: Iterable[str] = None):
    """Rows that produce the same ID.

    A collision is a data question for a person, not something to resolve automatically.
    Returns {timeline_item_id: [row, row, ...]} for every ID appearing more than once.
    """
    key_fields = list(key_fields or _cfg("NATURAL_KEY", DEFAULT_KEY_FIELDS))
    seen = {}
    for r in rows:
        tid = r.get("timeline_item_id") or timeline_item_id(r, key_fields)
        seen.setdefault(tid, []).append(r)
    return {k: v for k, v in seen.items() if len(v) > 1}


def check_unique(rows, key_fields: Iterable[str] = None):
    """Raise with a readable message if two rows collide.

    The message names both rows, because the person reading it has to go and look at the
    spreadsheet.
    """
    key_fields = list(key_fields or _cfg("NATURAL_KEY", DEFAULT_KEY_FIELDS))
    dups = find_duplicates(rows, key_fields)
    if not dups:
        return True

    lines = [f"{len(dups)} duplicate key(s) in this file. The import cannot continue."]
    for tid, group in list(dups.items())[:10]:
        lines.append(f"\n  {tid}")
        for r in group:
            where = r.get("source_row_number", "?")
            vals = " | ".join(str(r.get(f, "")) for f in key_fields)
            lines.append(f"    row {where}: {vals}")
    if len(dups) > 10:
        lines.append(f"\n  ... and {len(dups) - 10} more not shown")
    lines.append(
        "\nTwo rows share all three key values. Either the export has a genuine duplicate, "
        "or an implementation need was reworded to match another. A person has to decide "
        "which, so this is not resolved automatically."
    )
    raise ValueError("\n".join(lines))


def check_against_history(rows, known: dict, key_fields: Iterable[str] = None):
    """Guard against a hash collision with a key seen in an earlier import.

    `known` maps timeline_item_id to the natural key it was built from. Vanishingly
    unlikely with SHA-256, and cheap enough to rule out.
    """
    key_fields = list(key_fields or _cfg("NATURAL_KEY", DEFAULT_KEY_FIELDS))
    clashes = []
    for r in rows:
        tid = r.get("timeline_item_id") or timeline_item_id(r, key_fields)
        nk = natural_key(r, key_fields)
        if tid in known and known[tid] != nk:
            clashes.append((tid, known[tid], nk))
    if clashes:
        lines = ["hash collision against history, which should never happen:"]
        for tid, old, new in clashes:
            lines.append(f"  {tid}\n    was: {old}\n    now: {new}")
        raise ValueError("\n".join(lines))
    return True
