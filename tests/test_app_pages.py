"""Every screen renders without throwing.

WHY THIS FILE EXISTS

Three bugs reached the user's laptop because the only check on the app was that the server
answered on its port. It did. A Streamlit page renders after that, in a background thread,
and an exception there becomes a red box on the screen rather than a failed start. So the
app was "running" and every screen was broken.

The three:

  st.dataframe(height=None)      Streamlit used to read None as "size it yourself". It now
                                 rejects it. Four screens, one shared helper.
  days_since(NaT)                a work item with no updates yet has no last_update, and
                                 pandas reads that back as NaT, which the guard missed.
  use_container_width            deprecated, and already past the date it was to be removed.

streamlit.testing runs a page headlessly and collects whatever it raised, which is the
cheap version of opening five tabs and looking at them.

THESE TESTS NEED DATA, THE REST OF THE SUITE DOES NOT

The other 164 tests run on invented rows built in memory and touch no database at all,
which is a property worth keeping. So these skip themselves when the database is not there
yet. To run them:

    python scripts/create_tables.py
    python scripts/import_timeline.py --approve --by test
    python scripts/seed_demo.py
    pytest
"""

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
APP = os.path.join(PROJECT, "app")

# Streamlit puts the main script's folder on the import path when it runs an app, which is
# how `from lib import db, ui` resolves. AppTest does not, so a page tested on its own
# cannot find `lib`. Doing it here reproduces what a real run does.
if APP not in sys.path:
    sys.path.insert(0, APP)

pytest.importorskip("streamlit.testing.v1")

from streamlit.testing.v1 import AppTest  # noqa: E402

PAGES = [
    "Home.py",
    os.path.join("pages", "1_Work_items.py"),
    os.path.join("pages", "2_Submit_update.py"),
    os.path.join("pages", "3_Timeline.py"),
    os.path.join("pages", "4_Import_review.py"),
]


def _database_is_ready():
    """Is there a database with an approved import in it."""
    for p in (os.path.join(PROJECT, "conf"), os.path.join(PROJECT, "src"), APP):
        if p not in sys.path:
            sys.path.insert(0, p)
    try:
        import config
        if not os.path.exists(config.DUCKDB_PATH):
            return False
        from lib import db
        return db.table_count(config.t("core", "official_timeline")) > 0
    except Exception:
        return False


needs_data = pytest.mark.skipif(
    not _database_is_ready(),
    reason="no imported data yet: run create_tables, import_timeline --approve, seed_demo")


@needs_data
@pytest.mark.parametrize("page", PAGES, ids=lambda p: os.path.basename(p))
def test_the_page_renders_without_raising(page):
    at = AppTest.from_file(os.path.join(APP, page), default_timeout=120).run()
    assert not at.exception, (
        f"{page} raised: " + " | ".join(e.value.splitlines()[0] for e in at.exception))


@needs_data
def test_home_shows_the_headline_numbers():
    """Not just "did not crash". The Monday screen exists to answer five questions, and an
    empty screen answers none of them."""
    at = AppTest.from_file(os.path.join(APP, "Home.py"), default_timeout=120).run()
    assert not at.exception
    rendered = " ".join(str(m.value) for m in at.markdown)
    for label in ("timeline items", "active work items", "update coverage"):
        assert label in rendered, f"the Home screen is missing its {label!r} figure"


# ---------------------------------------------------------------- the empty deployment
#
# A fresh deployment has the code and the sample workbook and no database, because the
# database is not in the repo. Two things have to be right about that, and neither was.


def test_health_notices_that_the_tables_are_missing(tmp_path, monkeypatch):
    """health() used to check only that the database opened.

    Connecting to a DuckDB path that does not exist creates an empty database rather than
    refusing, so `SELECT 1` succeeded and the screen then died on the first real query with
    a raw catalog error instead of the message written for that case.
    """
    import config
    from lib import db

    # config reads TRACKER_DB when it is imported, and by now it has been, so the value is
    # patched directly. Clearing the cached connection makes db open the new path.
    monkeypatch.setattr(config, "DUCKDB_PATH", str(tmp_path / "empty.duckdb"))
    db._connection.clear()
    try:
        ok, detail = db.health()
        assert ok is False, "an empty database reported itself healthy"
        assert "not in it" in detail
        assert "create_tables" in detail
    finally:
        db._connection.clear()


def test_the_demo_bootstrap_is_off_unless_asked(monkeypatch):
    """On a laptop a missing database means a forgotten command, and being told is better
    than having it silently papered over. Only a hosted copy sets TRACKER_DEMO."""
    monkeypatch.delenv("TRACKER_DEMO", raising=False)
    from lib import bootstrap
    assert bootstrap.ensure() is False
