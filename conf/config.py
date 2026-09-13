"""Configuration for the Data Team Work Tracker.

The only place in the project that knows a store, a table name or a path. If you find one
of those hardcoded anywhere else, that is a bug.

That rule is worth more than it looks. Because every table name comes from t() and every
path is worked out from this file's own location, the project runs from any folder on any
machine, and swapping the database means editing one module rather than hunting through
every screen.
"""

import os

# --------------------------------------------------------------------------
# Where tables live
# --------------------------------------------------------------------------

# "duckdb" writes to a single file inside this repo.
# A server database would set CATALOG and SCHEMA below and t() would qualify names with
# them. Nothing else in the project would change, which is the point of t().
STORE = "duckdb"

CATALOG = None
SCHEMA = None

# Every table this project owns carries this prefix, so it can share a database with
# something else without a name collision, and so ownership is obvious at a glance.
#
# Read it as "the Data Team's tracker of the RSM timeline", not "the RSM tracker". Those
# are two different things and the names must not blur them:
#
#   the RSM onboarding timeline   the source. Owned elsewhere. Read only. Arrives
#                                 as a weekly Excel export.
#   the Data Team Work Tracker    this product. Owned here. Imports that source
#                                 and adds the Data Team's own work on top.
NAMESPACE = "rsm_data_tracker_"

# The layer stays visible in the table name.
PREFIX = {
    "raw": "raw_",      # exactly what arrived, never corrected
    "clean": "clean_",  # typed, cleaned, keyed
    "core": "core_",    # the governed tables
    "mart": "vw_",      # views the dashboards read
}

# --------------------------------------------------------------------------
# Where files live
#
# Everything is relative to the repo root, worked out from this file's own location, so
# the project runs from any folder on any machine. No absolute path is written down
# anywhere, which is what makes `git clone` followed by four commands actually work.
# --------------------------------------------------------------------------

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

LANDING = os.path.join(PROJECT, "landing")                        # real workbooks, gitignored
SAMPLE_DATA = os.path.join(PROJECT, "sample_data")                # invented workbooks, committed
DICTIONARY = os.path.join(PROJECT, "conf", "data_dictionary.xlsx")  # the contract
NAME_LOOKUP = os.path.join(PROJECT, "conf", "name_lookup.csv")    # known spelling variants
SRS_INSTRUMENTS = os.path.join(PROJECT, "conf", "srs_instruments.csv")
STORE_DIR = os.path.join(PROJECT, "store")
BACKUP = os.path.join(STORE_DIR, "backup")                        # CSV copy after each import
DUCKDB_PATH = os.path.join(STORE_DIR, "tracker.duckdb")

# The database file can be moved without editing this file.
DUCKDB_PATH = os.environ.get("TRACKER_DB", DUCKDB_PATH)

# --------------------------------------------------------------------------
# The source workbook
# --------------------------------------------------------------------------

READER = "openpyxl"
SOURCE_SHEET = "RSP Dates"
INSTRUMENT_SHEET = "InstrumentID"
PICKLIST_SHEET = "Picklists"

# The three fields that form the natural key. Changing this list changes every
# ID in the system, so it lives here and nowhere else.
NATURAL_KEY = ["instrument_id", "implementation_need", "fiscal_year"]
KEY_SEPARATOR = "||"
KEY_LENGTH = 16

# Values that mean "nothing here". TBD was missed in the first version of the
# dictionary and appeared 34 times in the real export, including once in a key field.
SENTINELS = {"N/A", "NA", "N/A ", "TBC", "TBD", "-", ""}

# The database file is small, and a CSV copy is readable without any tooling.
BACKUP_AFTER_IMPORT = True

# Who is using the tracker. There is no sign-in on a local build, so this names the
# person the app records as the author of a work item or an update.
USER = os.environ.get("TRACKER_USER", "local.user")

# --------------------------------------------------------------------------
# Helpers. Use these, never string formatting at the call site.
# --------------------------------------------------------------------------


def t(layer: str, name: str) -> str:
    """Fully qualified table name.

    >>> t("core", "official_timeline")
    'rsm_data_tracker_core_official_timeline'
    """
    if layer not in PREFIX:
        raise ValueError(f"unknown layer {layer!r}, expected one of {sorted(PREFIX)}")
    base = f"{NAMESPACE}{PREFIX[layer]}{name}"
    if STORE == "duckdb":
        return base
    return f"{CATALOG}.{SCHEMA}.{base}"


def all_tables() -> dict:
    """Every table the project owns, by layer."""
    return {
        "raw": ["timeline_import"],
        "clean": ["timeline_import", "cleaning_log"],
        "core": [
            "official_timeline",
            "timeline_change_log",
            "import_run",
            "data_work_items",
            "team_updates",
        ],
        "mart": [],
    }


def describe() -> str:
    """One line for the top of every script, so a run says where it wrote."""
    if STORE == "duckdb":
        return f"store=duckdb  path={DUCKDB_PATH}  namespace={NAMESPACE}"
    return f"store={STORE}  {CATALOG}.{SCHEMA}  namespace={NAMESPACE}"
