"""The data dictionary, loaded.

Nothing in this project hardcodes a column name, a data type, an allowed value or a
cleaning rule. It all comes from conf/data_dictionary.xlsx through this module.

The reason is not tidiness. It is that a rule written in two places is a rule that will
disagree with itself within a month, and the disagreement will be discovered by a wrong
number on a dashboard rather than by an error.

Usage:

    from tracker.contract import load
    c = load()
    c.fields("official_timeline")          # every field, in order
    c.source_columns()                     # the 24 exact headers of the RSP Dates sheet
    c.allowed("official_status")           # {"Complete", "In Progress", ...}
    c.rules()                              # the cleaning rules from sheet 10
"""

from dataclasses import dataclass, field as _field
from typing import Optional
import os
import re

try:
    import config
except ImportError:  # allows the module to be imported for tests without config on the path
    config = None


# Which dictionary sheet defines which table.
SHEETS = {
    "official_timeline": "04_official_timeline",
    "timeline_change_log": "05_timeline_change_log",
    "import_run": "06_import_run",
    "data_work_items": "07_data_work_items",
    "team_updates": "08_team_updates",
}

SOURCE_PROFILE_SHEET = "12_Source_Profile"
CONTROLLED_SHEET = "09_Controlled_Values"
RULES_SHEET = "10_Cleaning_Rules"

# Columns the pipeline generates rather than reads, and which therefore change on every
# import by design. They are excluded from change detection, because if they counted then
# every row would look changed every week.
GENERATED_FIELDS = {
    "row_hash", "first_seen_date", "last_seen_date", "is_missing",
    "source_import_id", "source_row_number", "timeline_item_id",
}


@dataclass(frozen=True)
class Field:
    name: str
    source_column: Optional[str]
    source_authority: str
    data_type: str
    nullable: bool
    key: str                       # "PK", "FK", "NK" or ""
    owner: str                     # "Source", "Team" or "System"
    written_by: str                # "Import" or "Application"
    controlled_list: Optional[str]
    validation_rule: str
    cleaning_applied: str
    example: str
    notes: str

    @property
    def base_type(self) -> str:
        """'string(16)' -> 'string'. Length is a validation rule, not a storage decision."""
        return re.sub(r"\(.*\)", "", self.data_type).strip().lower()

    @property
    def max_length(self) -> Optional[int]:
        m = re.search(r"\((\d+)\)", self.data_type or "")
        return int(m.group(1)) if m else None

    @property
    def is_generated(self) -> bool:
        return not self.source_column


@dataclass(frozen=True)
class Rule:
    id: str
    applies_to: str
    rule: str
    example: str
    becomes: str
    automatic: bool
    authority: str
    why: str


@dataclass
class Contract:
    path: str
    _fields: dict = _field(default_factory=dict)
    _source_columns: list = _field(default_factory=list)
    _controlled: dict = _field(default_factory=dict)
    _rules: list = _field(default_factory=list)

    # ---------------------------------------------------------------- access

    def fields(self, table: str):
        if table not in self._fields:
            raise KeyError(f"unknown table {table!r}, expected one of {sorted(self._fields)}")
        return self._fields[table]

    def field(self, table: str, name: str) -> Field:
        for f in self.fields(table):
            if f.name == name:
                return f
        raise KeyError(f"{table} has no field {name!r}")

    def field_names(self, table: str):
        return [f.name for f in self.fields(table)]

    def changeable_fields(self):
        """The official fields a change can meaningfully happen to.

        Everything on official_timeline except the columns the pipeline generates. This is
        also the allowed value list for timeline_change_log.field_name, derived rather than
        typed out, so the two can never disagree.
        """
        return [f.name for f in self.fields("official_timeline")
                if f.name not in GENERATED_FIELDS]

    def tables(self):
        return sorted(self._fields)

    def source_columns(self):
        """The exact headers of the RSP Dates sheet, in sheet order."""
        return list(self._source_columns)

    def imported_source_columns(self):
        """The source columns official_timeline actually reads. Fewer than the 24 above,
        and one of them feeds three fields."""
        seen, out = set(), []
        for f in self.fields("official_timeline"):
            if f.source_column and f.source_column not in seen:
                seen.add(f.source_column)
                out.append(f.source_column)
        return out

    def fields_for_source(self, source_column: str):
        """Every field fed by one source column. Instrument Short Name feeds three."""
        return [f for f in self.fields("official_timeline") if f.source_column == source_column]

    def allowed(self, list_name: str):
        if list_name not in self._controlled:
            raise KeyError(f"unknown controlled list {list_name!r}, "
                           f"expected one of {sorted(self._controlled)}")
        return set(self._controlled[list_name])

    def controlled_lists(self):
        return sorted(self._controlled)

    def rules(self, automatic_only: bool = False):
        return [r for r in self._rules if r.automatic] if automatic_only else list(self._rules)

    def rule(self, rule_id: str) -> Rule:
        for r in self._rules:
            if r.id == rule_id:
                return r
        raise KeyError(f"no cleaning rule {rule_id!r} in the dictionary")

    # ---------------------------------------------------------------- checks

    def check_source_columns(self, headers):
        """Compare the headers of an uploaded sheet against the contract.

        Returns (missing, unexpected). Both empty means the file matches. This is the
        import gate, and it is why the exact header strings live in the dictionary."""
        expected = [h.strip() for h in self._source_columns]
        actual = [str(h).strip() for h in headers if h is not None]
        missing = [h for h in expected if h not in actual]
        unexpected = [h for h in actual if h not in expected]
        return missing, unexpected

    def natural_key_fields(self):
        return [f.name for f in self.fields("official_timeline") if f.key == "NK"]

    def summary(self):
        lines = [f"data dictionary: {self.path}"]
        for t in self.tables():
            lines.append(f"  {t:<22} {len(self._fields[t]):>3} fields")
        lines.append(f"  {'source columns':<22} {len(self._source_columns):>3}")
        lines.append(f"  {'controlled lists':<22} {len(self._controlled):>3}")
        lines.append(f"  {'cleaning rules':<22} {len(self._rules):>3} "
                     f"({sum(1 for r in self._rules if r.automatic)} automatic)")
        return "\n".join(lines)


# -------------------------------------------------------------------- loading


def _truthy(v) -> bool:
    return str(v).strip().lower() in {"y", "yes", "true", "1"}


def _clean_cell(v):
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def load(path: str = None) -> Contract:
    """Read the dictionary workbook and build the contract.

    Fails loudly on anything missing. A dictionary that is half loaded is worse than one
    that refuses to load, because the pipeline would run with a silently incomplete
    contract and the gap would surface as missing data weeks later.
    """
    import openpyxl

    if path is None:
        if config is None:
            raise RuntimeError("no path given and config could not be imported")
        path = config.DICTIONARY

    if not os.path.exists(path):
        raise FileNotFoundError(f"data dictionary not found at {path}")

    wb = openpyxl.load_workbook(path, data_only=True)

    required = set(SHEETS.values()) | {SOURCE_PROFILE_SHEET, CONTROLLED_SHEET, RULES_SHEET}
    missing_sheets = sorted(required - set(wb.sheetnames))
    if missing_sheets:
        raise ValueError(f"data dictionary is missing sheets: {missing_sheets}")

    def rows_of(sheet_name):
        ws = wb[sheet_name]
        header = [c.value for c in ws[1]]
        for row in ws.iter_rows(min_row=2, values_only=True):
            if not row or row[0] is None or str(row[0]).strip() == "":
                continue
            yield {h: v for h, v in zip(header, row)}

    # --- fields, one sheet per table
    fields = {}
    for table, sheet in SHEETS.items():
        out = []
        for r in rows_of(sheet):
            out.append(Field(
                name=str(r["field_name"]).strip(),
                source_column=_clean_cell(r.get("source_column")),
                source_authority=_clean_cell(r.get("source_authority")) or "",
                data_type=str(r["data_type"]).strip(),
                nullable=str(r.get("nullable", "")).strip().upper() != "N",
                key=(_clean_cell(r.get("key")) or "").upper(),
                owner=_clean_cell(r.get("owner")) or "",
                written_by=_clean_cell(r.get("written_by")) or "",
                controlled_list=_clean_cell(r.get("controlled_list")),
                validation_rule=_clean_cell(r.get("validation_rule")) or "",
                cleaning_applied=_clean_cell(r.get("cleaning_applied")) or "",
                example=_clean_cell(r.get("example_from_source")) or "",
                notes=_clean_cell(r.get("notes")) or "",
            ))
        if not out:
            raise ValueError(f"sheet {sheet} produced no fields")
        fields[table] = out

    # --- the exact source headers, from the profile sheet
    ws = wb[SOURCE_PROFILE_SHEET]
    source_columns = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        if row and row[1] and str(row[1]).strip():
            source_columns.append(str(row[1]).strip())
    if len(source_columns) != 24:
        raise ValueError(f"expected 24 source columns on {SOURCE_PROFILE_SHEET}, "
                         f"found {len(source_columns)}")

    # --- controlled value lists
    controlled = {}
    current = None
    for r in rows_of(CONTROLLED_SHEET):
        name = _clean_cell(r.get("list_name"))
        if name:
            current = name
            controlled.setdefault(current, [])
        value = _clean_cell(r.get("allowed_value"))
        if current and value:
            controlled[current].append(value)

    # --- cleaning rules
    rules = []
    for r in rows_of(RULES_SHEET):
        rules.append(Rule(
            id=str(r["rule_id"]).strip(),
            applies_to=_clean_cell(r.get("applies_to")) or "",
            rule=_clean_cell(r.get("rule")) or "",
            example=_clean_cell(r.get("real example found in the sources")) or "",
            becomes=_clean_cell(r.get("becomes")) or "",
            automatic=_truthy(r.get("automatic")),
            authority=_clean_cell(r.get("authority")) or "",
            why=_clean_cell(r.get("why")) or "",
        ))
    if not rules:
        raise ValueError(f"sheet {RULES_SHEET} produced no rules")

    c = Contract(path=path)
    c._fields = fields
    c._source_columns = source_columns
    c._controlled = controlled
    c._rules = rules

    # timeline_change_log.field_name is restricted to the official fields. That list is
    # derived from sheet 04 rather than typed onto sheet 09, because a typed copy would
    # drift the first time a field was added and nobody would notice.
    controlled["official_field"] = c.changeable_fields()

    # A key that is not marked in the dictionary is a contract that does not match the code.
    nk = c.natural_key_fields()
    if config is not None and nk != config.NATURAL_KEY:
        raise ValueError(
            f"the dictionary marks {nk} as the natural key but config.NATURAL_KEY is "
            f"{config.NATURAL_KEY}. These must agree, because the key rule depends on both."
        )

    return c
