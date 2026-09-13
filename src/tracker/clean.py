"""The thirteen cleaning rules from sheet 10 of the data dictionary.

Two principles run through all of it.

**Cleaning is logged, never silent.** Every value changed produces a row in the cleaning
log naming the rule that changed it, what arrived and what it became. The import review
shows a count and the reviewer can open the detail. A pipeline that quietly corrects data
is a pipeline nobody can audit.

**Cleaning runs before the key is built.** Hashing an untrimmed value would give the same
row two different IDs, and it would present as deleted and recreated every single week.

Rules C01 to C13 are specified on sheet 10, each with a real example found in the source.
C09 is deliberately not automatic: it reports naming divergences from the SRS rather than
correcting them, because a French abbreviation is a translation decision, not dirt.
"""

from dataclasses import dataclass
from datetime import datetime, date
from typing import Optional
import csv
import os
import re

try:
    import config
except ImportError:
    config = None


DEFAULT_SENTINELS = {"N/A", "NA", "TBC", "TBD", "-", "--", ""}

# C05b. 26 real dates in the current export arrive as English text like this.
LONG_DATE_FORMATS = [
    "%A, %B %d, %Y",     # Monday, April 5, 2027
    "%A %B %d, %Y",
    "%B %d, %Y",         # April 5, 2027
    "%d %B %Y",
    "%Y-%m-%d",
]

DATE_MIN = date(2020, 1, 1)
DATE_MAX = date(2035, 12, 31)


@dataclass
class LogEntry:
    """One row of the cleaning log. Mirrors clean_cleaning_log."""
    source_row_number: int
    natural_key: Optional[str]
    field_name: str
    rule_id: str
    raw_value: Optional[str]
    cleaned_value: Optional[str]

    def as_row(self, import_id: str, cleaned_at: datetime):
        return {
            "import_id": import_id,
            "source_row_number": self.source_row_number,
            "natural_key": self.natural_key,
            "field_name": self.field_name,
            "rule_id": self.rule_id,
            "raw_value": self.raw_value,
            "cleaned_value": self.cleaned_value,
            "cleaned_at": cleaned_at,
        }


@dataclass
class Divergence:
    """Something reported, not corrected. See C09."""
    source_row_number: int
    field_name: str
    value: str
    expected: str
    note: str


# ------------------------------------------------------------------ helpers


def _s(v):
    return None if v is None else str(v)


def _sentinels():
    return {x.strip().upper() for x in (
        getattr(config, "SENTINELS", DEFAULT_SENTINELS) if config else DEFAULT_SENTINELS)}


def load_name_lookup(path: str = None) -> dict:
    """Known spelling variants, mapped to the canonical name. C07.

    Lives in conf/name_lookup.csv with a dated note per row, so a name is never corrected
    outside a versioned, reviewable file.
    """
    if path is None:
        path = getattr(config, "NAME_LOOKUP", None) if config else None
    if not path or not os.path.exists(path):
        return {}
    out = {}
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            variant = (row.get("variant") or "").strip()
            canonical = (row.get("canonical") or "").strip()
            if variant and canonical:
                out[variant.lower()] = canonical
    return out


def load_srs_instruments(path: str = None) -> dict:
    """The SRS instrument naming table. C09.

    Maps an English abbreviation to its French abbreviation, as defined in SRS section 3.1,
    which is authoritative for naming.
    """
    if path is None and config is not None:
        path = os.path.join(os.path.dirname(config.NAME_LOOKUP), "srs_instruments.csv")
    if not path or not os.path.exists(path):
        return {}
    out = {}
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            en = (row.get("abbreviation_en") or "").strip()
            fr = (row.get("abbreviation_fr") or "").strip()
            if en:
                out[en.upper()] = fr.upper()
    return out


# ------------------------------------------------------------------ rules


def c01_c02_text(value):
    """C01 trim, C02 collapse repeated internal spaces."""
    if value is None:
        return None, False
    s = str(value)
    out = re.sub(r"[ \t]+", " ", s).strip()
    return (out or None), (out != s)


def c10_strip_newlines(value):
    """C10. A leading newline on Multi-Sector Air Pollutants Regulations, 4 rows."""
    if value is None:
        return None, False
    s = str(value)
    out = s.strip("\r\n \t")
    out = re.sub(r"[ \t]+", " ", out)
    return (out or None), (out != s)


def c12_notes(value):
    """C12. Trim the ends only, keep internal line breaks. Notes carry the reason a date
    moved, so flattening them loses the most useful free text in the file."""
    if value is None:
        return None, False
    s = str(value)
    out = s.strip()
    return (out or None), (out != s)


def c04_sentinel(value):
    """C04. N/A, TBC, TBD, dashes and blanks all mean nothing is here."""
    if value is None:
        return None, False
    s = str(value).strip()
    if s.upper() in _sentinels():
        return None, True
    return (s or None), False


def c03_canonical_case(value, allowed):
    """C03. Case only, never map one value onto a different value."""
    if value is None:
        return None, False
    s = str(value).strip()
    if s in allowed:
        return s, False
    lowered = {a.lower(): a for a in allowed}
    hit = lowered.get(s.lower())
    if hit:
        return hit, True
    return s, False          # unknown value, reported by validation, not silently changed


def c05_date(value):
    """C05 datetime to date, C05b long form English dates.

    Returns (value, changed, rule_id, ok). ok is False when the text could not be parsed,
    which is a validation error rather than something to clean.
    """
    if value is None:
        return None, False, None, True

    if isinstance(value, datetime):
        return value.date(), True, "C05", True
    if isinstance(value, date):
        return value, False, None, True

    s = str(value).strip()
    if not s:
        return None, False, None, True
    if s.upper() in _sentinels():
        return None, True, "C04", True

    for fmt in LONG_DATE_FORMATS:
        try:
            d = datetime.strptime(s, fmt).date()
            return d, True, "C05b", True
        except ValueError:
            continue

    return None, False, None, False


def c06_priority(value):
    """C06. 50 rows carry an integer, 32 carry the string N/A."""
    if value is None:
        return None, False, True
    if isinstance(value, int):
        return value, False, True
    s = str(value).strip()
    if s.upper() in _sentinels():
        return None, True, True
    try:
        return int(float(s)), True, True
    except ValueError:
        return None, False, False


def c07_name(value, lookup):
    """C07. 'Morgan Ashbey' on the Picklists sheet against 'Morgan Ashby' in RSP Dates."""
    if value is None or not lookup:
        return value, False
    s = str(value).strip()
    hit = lookup.get(s.lower())
    if hit and hit != s:
        return hit, True
    return s, False


def c08_split_short_name(value):
    """C08 split on the first ' - ', C08b leave both parts null when there is no separator.

    'VOC2 - COV2' -> ('VOC2', 'COV2'). 'EPOD IDEA' -> (None, None), which is data rather
    than an error: some entries are project names, not bilingual abbreviations.
    """
    if value is None:
        return None, None, False
    s = str(value).strip()
    if " - " not in s:
        return None, None, False
    en, fr = s.split(" - ", 1)
    return en.strip() or None, fr.strip() or None, True


def c11_instrument_id(value, known_ids=None):
    """C11. Upper case and trim, then confirm the ID exists on the InstrumentID sheet."""
    if value is None:
        return None, False, False
    s = str(value).strip().upper()
    changed = s != str(value)
    ok = True if not known_ids else s in known_ids
    return s, changed, ok


def c09_report_naming(short_name_en, short_name_fr, srs, row_number):
    """C09. Report a divergence from the SRS, do not correct it.

    The export says 'MGBH - COM' on one row and 'MGBH - OMCG' on another. The SRS says
    OMCG. It contradicts itself, which is the clearest possible signal that a person should
    look, and the clearest possible reason not to rewrite it automatically.
    """
    if not srs or not short_name_en or not short_name_fr:
        return None
    expected = srs.get(str(short_name_en).strip().upper())
    if not expected:
        return None                       # instrument not covered by the SRS, not an error
    actual = str(short_name_fr).strip().upper()
    if actual == expected:
        return None
    return Divergence(
        source_row_number=row_number,
        field_name="instrument_short_name_fr",
        value=short_name_fr,
        expected=expected,
        note=(f"SRS 3.1 gives the French abbreviation for {short_name_en} as {expected}. "
              f"The export says {short_name_fr}. Reported, not corrected: a French "
              f"abbreviation is a translation decision."),
    )


# ------------------------------------------------------------------ the pass


TEXT_FIELDS_KEEPING_LINEBREAKS = {"notes"}
NAME_FIELDS = {"lead_rsm_concierge", "lead_business_analyst", "product_owner"}


def clean_row(raw: dict, contract, row_number: int,
              name_lookup=None, srs=None, known_ids=None):
    """Clean one source row into the official_timeline shape.

    `raw` is keyed by the exact source column headers, as read from the sheet.
    Returns (clean_row, log_entries, divergences, errors).
    """
    name_lookup = name_lookup or {}
    log, divergences, errors = [], [], []
    out = {"source_row_number": row_number}

    def note(field_name, rule_id, before, after):
        log.append(LogEntry(row_number, None, field_name, rule_id, _s(before), _s(after)))

    fields = contract.fields("official_timeline")

    # Instrument Short Name feeds three fields, so it is handled once up front.
    short_src = "Instrument Short Name"
    short_raw = raw.get(short_src)
    short_clean, ch = c01_c02_text(short_raw)
    if ch:
        note("instrument_short_name_raw", "C01", short_raw, short_clean)
    en, fr, split = c08_split_short_name(short_clean)
    out["instrument_short_name_raw"] = short_clean
    out["instrument_short_name_en"] = en
    out["instrument_short_name_fr"] = fr
    if split:
        note("instrument_short_name_en", "C08", short_clean, f"{en} | {fr}")
    elif short_clean:
        note("instrument_short_name_fr", "C08b", short_clean, "no separator, parts left null")

    d = c09_report_naming(en, fr, srs, row_number)
    if d:
        divergences.append(d)

    for f in fields:
        if f.name in ("instrument_short_name_raw", "instrument_short_name_en",
                      "instrument_short_name_fr"):
            continue
        if f.is_generated:
            continue                       # keys, hashes and publish state come later

        value = raw.get(f.source_column)
        before = value

        # --- text shaping
        if f.name in TEXT_FIELDS_KEEPING_LINEBREAKS:
            value, ch = c12_notes(value)
            if ch:
                note(f.name, "C12", before, value)
            v2, ch2 = c04_sentinel(value)
            if ch2:
                note(f.name, "C04", value, v2)
            value = v2
        elif f.name == "instrument_full_name":
            value, ch = c10_strip_newlines(value)
            if ch:
                note(f.name, "C10", before, value)

        # --- typed conversion
        if f.base_type == "date":
            v, ch, rule, ok = c05_date(value)
            if ch and rule:
                note(f.name, rule, before, v)
            if not ok:
                errors.append(f"row {row_number}: {f.name} could not be read as a date, "
                              f"value was {before!r}")
            elif v is not None and not (DATE_MIN <= v <= DATE_MAX):
                errors.append(f"row {row_number}: {f.name} is {v}, outside "
                              f"{DATE_MIN} to {DATE_MAX}")
            out[f.name] = v
            continue

        if f.name == "priority":
            v, ch, ok = c06_priority(value)
            if ch:
                note(f.name, "C06", before, v)
            if not ok:
                errors.append(f"row {row_number}: priority {before!r} is not a number")
            out[f.name] = v
            continue

        if f.name == "instrument_id":
            v, ch, ok = c11_instrument_id(value, known_ids)
            if ch:
                note(f.name, "C11", before, v)
            if not ok:
                errors.append(f"row {row_number}: instrument_id {v!r} is not on the "
                              f"InstrumentID sheet")
            out[f.name] = v
            continue

        # --- generic text
        v, ch = c01_c02_text(value)
        if ch:
            note(f.name, "C01", before, v)

        if f.name in NAME_FIELDS:
            v2, ch2 = c07_name(v, name_lookup)
            if ch2:
                note(f.name, "C07", v, v2)
            v = v2

        # fiscal_year is a key field, so C04b keeps TBD instead of nulling it
        if f.name == "fiscal_year":
            if v is not None:
                v = str(v).strip().upper()
            out[f.name] = v
            continue

        v2, ch2 = c04_sentinel(v)
        if ch2:
            note(f.name, "C04", v, v2)
        v = v2

        if f.controlled_list:
            try:
                allowed = contract.allowed(f.controlled_list)
            except KeyError:
                allowed = set()
            if allowed and v is not None:
                v3, ch3 = c03_canonical_case(v, allowed)
                if ch3:
                    note(f.name, "C03", v, v3)
                v = v3
                if v not in allowed:
                    errors.append(f"row {row_number}: {f.name} is {v!r}, which is not in "
                                  f"the {f.controlled_list} list")

        if not f.nullable and v is None:
            errors.append(f"row {row_number}: {f.name} is required but empty")

        if f.max_length and v is not None and len(str(v)) > f.max_length:
            errors.append(f"row {row_number}: {f.name} is longer than {f.max_length}")

        out[f.name] = v

    return out, log, divergences, errors


def clean_rows(raw_rows, contract, name_lookup=None, srs=None, known_ids=None,
               first_data_row: int = 2):
    """Clean every row. Returns (rows, log, divergences, errors).

    `first_data_row` is 2 because row 1 of the sheet is the header, and the log records the
    spreadsheet row number so a person can go and look at it.
    """
    name_lookup = name_lookup if name_lookup is not None else load_name_lookup()
    srs = srs if srs is not None else load_srs_instruments()

    rows, log, divs, errs = [], [], [], []
    for i, raw in enumerate(raw_rows):
        r, l, d, e = clean_row(raw, contract, first_data_row + i, name_lookup, srs, known_ids)
        rows.append(r)
        log.extend(l)
        divs.extend(d)
        errs.extend(e)
    return rows, log, divs, errs


def summarize(rows, log, divergences, errors) -> str:
    by_rule = {}
    for e in log:
        by_rule[e.rule_id] = by_rule.get(e.rule_id, 0) + 1

    lines = [f"{len(rows)} rows cleaned, {len(log)} values changed"]
    for rule_id in sorted(by_rule):
        lines.append(f"    {rule_id:<6} {by_rule[rule_id]:>4}")
    if divergences:
        lines.append(f"\n{len(divergences)} naming divergence(s) reported, not corrected:")
        for d in divergences[:10]:
            lines.append(f"    row {d.source_row_number}: {d.value}, SRS says {d.expected}")
    if errors:
        lines.append(f"\n{len(errors)} validation error(s):")
        for e in errors[:15]:
            lines.append(f"    {e}")
        if len(errors) > 15:
            lines.append(f"    ... and {len(errors) - 15} more")
    else:
        lines.append("\nNo validation errors.")
    return "\n".join(lines)
