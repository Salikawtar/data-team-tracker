"""Build the demo database on first boot, for a hosted copy.

WHY THIS EXISTS

The database file is gitignored, which is right: it holds imported data and it changes on
every run. But that means a fresh deployment has the code and the sample workbook and no
tables at all, so the app would open on an error instead of on the tracker.

The four setup commands already build everything from scratch. This runs them once, in
process, when the tables are missing.

IT IS OPT IN, ON PURPOSE

Only runs when TRACKER_DEMO is set. On a laptop you run the four commands yourself and a
missing database means you forgot one, which is worth being told rather than having papered
over. On a hosted demo there is nobody to run anything, so the app builds its own.

Set TRACKER_DEMO=1 in the hosting platform's environment or secrets. Nothing else.

WHAT IT DOES NOT DO

It never touches an existing database. If official_timeline has rows, this returns
immediately. So redeploying does not wipe anything a viewer entered, until the host
restarts the container and takes the file with it, which is the nature of a free tier.
"""

import os
import sys


def _run(module_name, argv):
    """Call a script's main() with the arguments it would have been given.

    The scripts are written to be run from a terminal and parse sys.argv. Rather than
    duplicate what they do, this lends them an argv and calls them. Slightly blunt, and
    much better than a second copy of the same logic drifting away from the first.
    """
    import importlib

    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    scripts = os.path.join(here, "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)

    saved = sys.argv
    sys.argv = [module_name] + argv
    try:
        importlib.import_module(module_name).main()
    finally:
        sys.argv = saved


def needed() -> bool:
    """True when there is nothing to show."""
    from lib import db
    try:
        return db.table_count(db.t("core", "official_timeline")) == 0
    except Exception:
        return True          # no database file, or no tables in it


def build(seed_team_work: bool = True):
    """Create the tables, import the sample workbook, add invented team work."""
    import glob

    from lib import db
    cfg = db.cfg()

    if not glob.glob(os.path.join(cfg.SAMPLE_DATA, "*.xlsx")):
        _run("make_sample_data", [])

    _run("create_tables", [])
    _run("import_timeline", ["--approve", "--by", "demo"])
    if seed_team_work:
        _run("seed_demo", [])
    db.refresh()


def ensure():
    """Call this at the top of the Home screen. Does nothing unless asked and empty."""
    if not os.getenv("TRACKER_DEMO"):
        return False
    if not needed():
        return False
    build()
    return True
