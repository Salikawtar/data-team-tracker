"""Talking to the tracker's tables.

Every screen and every script calls these functions and never builds a connection itself.

**This is the only file in the project that knows what the database is.** Every screen
and every script goes through the functions below, so swapping DuckDB for something else
means rewriting this module and nothing else. That is worth keeping true.

Two rules that matter more than they look.

**Values are passed as parameters, never formatted into the SQL.** A progress update is
free text typed by a person, and a person will eventually type an apostrophe. The SQL in
this project is written with `:name` markers, which most drivers understand. DuckDB spells
them `$name`, so `execute` translates in one place rather than at every call site.

**Writes go through `insert`, which takes a dict built by `team.py` or `publish.py`.** So
the validation and the ownership guard always run before anything reaches a table.

**Streamlit is optional here.** It is used only for caching, so a script or a test can
import this module and query the tables without it.

One thing to know about DuckDB: a database file has a single writer. If the Streamlit app
is open, an import script cannot write. That is not a bug to work around, it is the file
being protected, so the error below says so in words.
"""

from __future__ import annotations

import functools
import os
import re
import threading
from contextlib import contextmanager
from typing import Any, Optional

import pandas as pd


# ------------------------------------------------------------------ optional Streamlit
#
# This module is imported by the app, but also by the import script and by anything else
# that wants to read the tracker's tables. Streamlit is only used here for its two caching
# decorators, and caching is a speed optimisation, not behaviour. So when Streamlit is
# absent the decorators are replaced with plain ones and everything else works unchanged.
# Importing a web framework should not be the price of running a query.

try:
    import streamlit as st

    HAS_STREAMLIT = True
except ModuleNotFoundError:
    HAS_STREAMLIT = False

    def _plain_resource(*_args, **_kwargs):
        """Stands in for st.cache_resource: remembers one result per call signature."""
        def decorate(fn):
            store = {}

            @functools.wraps(fn)
            def wrapper(*args, **kwargs):
                key = (args, tuple(sorted(kwargs.items())))
                if key not in store:
                    store[key] = fn(*args, **kwargs)
                return store[key]

            wrapper.clear = store.clear
            return wrapper
        return decorate

    def _plain_data(*_args, **_kwargs):
        """Stands in for st.cache_data: no caching at all.

        Deliberate. Outside the app there is no session to keep a cache for, and a stale
        answer in a script is worse than a slow one.
        """
        def decorate(fn):
            @functools.wraps(fn)
            def wrapper(*args, **kwargs):
                return fn(*args, **kwargs)

            wrapper.clear = lambda: None
            return wrapper
        return decorate

    class _NoStreamlit:
        cache_resource = staticmethod(_plain_resource)
        cache_data = staticmethod(_plain_data)

    st = _NoStreamlit()


# ------------------------------------------------------------------ the connection

_LOCK = threading.Lock()

# Streamlit reruns the script on every widget click, sometimes on a different thread.
# A DuckDB connection is not thread safe, so every statement goes through one lock.
# At this size that costs nothing measurable.


@st.cache_resource(show_spinner=False)
def _connection():
    """One connection per process, opened on the database file named by config."""
    import duckdb

    path = cfg().DUCKDB_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        return duckdb.connect(path)
    except Exception as e:
        if "lock" in str(e).lower() or "being used" in str(e).lower():
            raise RuntimeError(
                f"The tracker database is open in another program.\n\n{path}\n\n"
                "DuckDB allows one writer at a time. Close the Streamlit app, or stop the "
                "import script, then try again."
            ) from None
        raise


# ------------------------------------------------------------------ parameters

# The project's SQL is written with :name markers. DuckDB wants $name. Translating here
# means no call site has to care.
# The lookbehind skips :: casts and the lookahead skips a bare colon inside text.
_MARKER = re.compile(r"(?<![:\w]):([A-Za-z_]\w*)")


def _translate(sql_text: str) -> str:
    return _MARKER.sub(r"$\1", sql_text)


# ------------------------------------------------------------------ one door for all of it


def execute(sql_text: str, params: Optional[dict] = None):
    """Run one statement and return (column names, rows)."""
    con = _connection()
    text = _translate(sql_text) if params else sql_text
    with _LOCK:
        cur = con.execute(text, params) if params else con.execute(text)
        names = [d[0] for d in cur.description] if cur.description else []
        rows = [list(r) for r in cur.fetchall()] if cur.description else []
    return names, rows


@contextmanager
def transaction():
    """Everything inside either lands or does not.

    An import touches six tables. Without this, a failure halfway through would leave
    official_timeline updated and no change log to explain it.
    """
    con = _connection()
    with _LOCK:
        con.execute("BEGIN TRANSACTION")
    try:
        yield
    except Exception:
        with _LOCK:
            con.execute("ROLLBACK")
        raise
    else:
        with _LOCK:
            con.execute("COMMIT")
    finally:
        cached_query.clear()


def health() -> tuple[bool, str]:
    """Used by the Home screen so a connection problem reads as a message rather than a
    stack trace."""
    try:
        execute("SELECT 1")
        return True, f"connected (duckdb: {os.path.basename(cfg().DUCKDB_PATH)})"
    except Exception as e:
        return False, str(e)[:400]


# ------------------------------------------------------------------ reading


def query(sql_text: str, params: Optional[dict] = None) -> pd.DataFrame:
    names, rows = execute(sql_text, params)
    return pd.DataFrame(rows, columns=names)


@st.cache_data(ttl=60, show_spinner=False)
def cached_query(sql_text: str, params: Optional[dict] = None) -> pd.DataFrame:
    """Sixty seconds is long enough to stop every widget click hitting the database, and
    short enough that a submitted update shows up almost immediately."""
    return query(sql_text, params)


def scalar(sql_text: str, params: Optional[dict] = None, default=None):
    df = query(sql_text, params)
    if df.empty or df.shape[1] == 0:
        return default
    value = df.iloc[0, 0]
    return default if pd.isna(value) else value


def table_count(table: str) -> int:
    return int(scalar(f"SELECT COUNT(*) FROM {table}", default=0))


# ------------------------------------------------------------------ writing


def insert(table: str, row: dict) -> None:
    """Insert one row. The dict comes from team.py, already validated and guarded."""
    cols = list(row.keys())
    names = ", ".join(cols)
    marks = ", ".join(f":{c}" for c in cols)
    execute(f"INSERT INTO {table} ({names}) VALUES ({marks})",
            {c: row[c] for c in cols})
    cached_query.clear()


MAX_PARAMS_PER_STATEMENT = 1000


def insert_many(table: str, rows: list, batch: int = None) -> int:
    """Insert many rows as a few multi row statements rather than one per row.

    An import writes roughly a thousand rows across the staging and core tables. One
    statement per row would take minutes and look like a hang.

    The batch size adapts to the width of the table rather than being a fixed row count.
    official_timeline has thirty columns, so a hundred rows would be three thousand
    parameters in one statement, which is asking for trouble.
    """
    if not rows:
        return 0
    cols = list(rows[0].keys())
    names = ", ".join(cols)
    if batch is None:
        batch = max(1, MAX_PARAMS_PER_STATEMENT // max(1, len(cols)))
    written = 0

    for start in range(0, len(rows), batch):
        chunk = rows[start:start + batch]
        marks, params = [], {}
        for i, row in enumerate(chunk):
            marks.append("(" + ", ".join(f":p{i}_{c}" for c in cols) + ")")
            for c in cols:
                params[f"p{i}_{c}"] = row.get(c)
        execute(f"INSERT INTO {table} ({names}) VALUES {', '.join(marks)}", params)
        written += len(chunk)

    cached_query.clear()
    return written


def upsert(table: str, rows: list, key_column: str, batch: int = None) -> int:
    """Insert rows, updating in place any whose key is already there.

    THE OBVIOUS VERSION OF THIS IS WRONG, so it is worth saying why.

    Deleting the keys and inserting them again produces the same table content, so it looks
    equivalent. It is not. official_timeline is pointed at by timeline_change_log and by
    data_work_items, and deleting a row breaks those references, so the second import of the
    same file fails outright rather than quietly orphaning a change log.

    What is wanted is the two halves of an upsert:

        rows whose key already exists   ->  UPDATE ... FROM _incoming
        rows whose key does not         ->  INSERT ... WHERE NOT EXISTS

    The incoming rows go into a temporary table first, which is also faster than one
    statement per row.

    DuckDB's own ON CONFLICT DO UPDATE would be shorter, but on a table with children it
    falls back to a delete and re-insert internally and trips the same foreign key. A plain
    UPDATE that leaves the key column alone does not.

    Safe to re-run: the same file produces the same keys, so the second run updates every
    row to the values it already has and inserts nothing. Call it inside transaction().
    """
    if not rows:
        return 0
    cols = list(rows[0].keys())
    names = ", ".join(cols)
    staging = "_incoming_upsert"

    execute(f"DROP TABLE IF EXISTS {staging}")
    execute(f"CREATE TEMP TABLE {staging} AS SELECT {names} FROM {table} WHERE false")
    insert_many(staging, rows, batch=batch)

    updates = ", ".join(f"{c} = s.{c}" for c in cols if c != key_column)
    if updates:
        execute(f"UPDATE {table} AS t SET {updates} FROM {staging} AS s "
                f"WHERE t.{key_column} = s.{key_column}")

    execute(f"INSERT INTO {table} ({names}) SELECT {names} FROM {staging} AS s "
            f"WHERE NOT EXISTS (SELECT 1 FROM {table} AS t "
            f"                  WHERE t.{key_column} = s.{key_column})")

    execute(f"DROP TABLE {staging}")
    cached_query.clear()
    return len(rows)


def delete_where_in(table: str, column: str, values: list, batch: int = 200) -> int:
    """Delete rows whose key is in a list.

    Used on tables nothing points at. For a table with children, use upsert: see the note
    there about why deleting and re-inserting is not the same thing."""
    if not values:
        return 0
    removed = 0
    for start in range(0, len(values), batch):
        chunk = values[start:start + batch]
        marks = ", ".join(f":v{i}" for i in range(len(chunk)))
        params = {f"v{i}": v for i, v in enumerate(chunk)}
        execute(f"DELETE FROM {table} WHERE {column} IN ({marks})", params)
        removed += len(chunk)
    cached_query.clear()
    return removed


def delete_where(table: str, column: str, value) -> None:
    """Delete every row matching one value. Used to re-run an import cleanly."""
    execute(f"DELETE FROM {table} WHERE {column} = :v", {"v": value})
    cached_query.clear()


def update_fields(table: str, key_column: str, key_value: str, fields: dict) -> None:
    """Update named columns on one row, by primary key."""
    sets = ", ".join(f"{c} = :{c}" for c in fields)
    params = dict(fields)
    params["_key"] = key_value
    execute(f"UPDATE {table} SET {sets} WHERE {key_column} = :_key", params)
    cached_query.clear()


def refresh():
    """Drop the query cache. Called after any write, and by the refresh button."""
    cached_query.clear()


# ------------------------------------------------------------------ the contract


@st.cache_resource(show_spinner=False)
def contract():
    """The data dictionary, loaded once. It is the contract: no column name, type,
    allowed value or cleaning rule is written anywhere else in the project."""
    from tracker.contract import load
    return load(cfg().DICTIONARY)


@st.cache_resource(show_spinner=False)
def cfg():
    import config
    return config


def t(layer: str, name: str) -> str:
    return cfg().t(layer, name)
