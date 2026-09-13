# Data Team Work Tracker

A small governed data product. It imports an official schedule that somebody else owns,
keeps a permanent record of every time that schedule moves, and lets a team track its own
work against it without the two ever being able to overwrite each other.

Runs on a laptop. One command to set up, one to run.

```bash
pip install -r requirements.txt
python scripts/make_sample_data.py          # invented workbook, same shape as the real one
python scripts/create_tables.py             # eight tables, generated from the data dictionary
python scripts/import_timeline.py --approve --by demo
python scripts/seed_demo.py                 # some Data Team work, so the screens are not empty
streamlit run app/Home.py
```

Everything in the repo runs on invented data. There is no real data here and none is needed.

---

## The problem it solves

A federal regulatory programme maintains an official onboarding timeline: which regulatory
instrument goes onto the digital platform, and when. It is exported weekly as an Excel
workbook and it is authoritative for the milestone dates.

It was not built to manage a data team's work, and four things follow from that.

**It records milestones, not work.** It says a launch is in March. It does not say who on
the data team owns the pipeline behind it, what they have to deliver, or what is blocking
them.

**Team progress lives in email.** So the answer to "where are we" depends on who you ask.

**When a date moves, the old value is gone.** The export is overwritten each week. Nobody
can answer "how many times has this launch slipped" because nothing recorded the slips.

**The workbook cannot be written to.** It belongs to another team. Anything the data team
adds has to live somewhere else and stay linked.

## The rule the whole design rests on

> An import may update official fields only. It must never overwrite the data team's
> owner, status, notes, blockers, or any other team-owned information.

Official and team information live in separate tables, linked and never merged. The import
writes one side, the app writes the other, and neither is allowed to reach across.

That is not a convention. It is two functions, `publish.assert_no_team_fields` and
`team.assert_no_official_fields`, which inspect every column of every row against the data
dictionary and refuse the write. Both are covered by tests, and the import calls them
before it opens a transaction.

## How it works

```
  official export (.xlsx)
          |
          v
     [ 0 gate ]      right sheet, right 24 columns, not already imported
     [ 1 raw  ]      stored exactly as it arrived, never corrected
     [ 2 clean]      13 rules, every change written to a cleaning log
     [ 3 key  ]      a key the source file does not have
     [ 4 compare]    against last week, BY KEY, never by row position
     [ 5 report]     what would change, for a person to read
     [ 6 approve]    nothing is written without it
          |
          v
  official_timeline  <--  timeline_change_log        the permanent record
          ^
          |  timeline_item_id
          |
  data_work_items  <--  team_updates                 what the team is doing
          ^
          |
       the app
```

**The key is generated, because the source has none.** Instrument ID is not unique: 82 rows
resolved to 24 instruments, because one instrument carries several implementation needs
across several fiscal years, and one ID appeared thirteen times. So the tracker builds its
own key from three fields together, hashed: instrument, implementation need, fiscal year.
Deterministic, so the same row produces the same ID next week with no lookup table.

**Comparison is by key, never by row position.** The export is sorted by priority and
priority is re-ranked between exports, so rows move every week without changing. Comparing
row 2 to row 2 would report the entire file as changed and hide the handful of things that
actually did.

**Cleaning is logged, never silent.** Every corrected value writes a row naming the rule
that changed it, what arrived, and what it became. The sample import produces around 700
of them. A pipeline that quietly fixes data is a pipeline nobody can audit.

**Nothing is hardcoded.** No column name, type, allowed value or cleaning rule appears
anywhere in the code. It all comes out of `conf/data_dictionary.xlsx`, including the table
definitions: `scripts/create_tables.py` contains no CREATE TABLE text, it generates all
eight from the dictionary. A rule written in two places is a rule that will disagree with
itself within a month.

## What is in here

```
conf/     data_dictionary.xlsx     the contract. Everything else reads this.
src/      tracker/                 all the logic: contract, clean, keys, compare, publish, team
app/      Home.py, pages/          the Streamlit screens
          lib/db.py                the only file that knows what the database is
scripts/  create_tables.py         dictionary -> DDL
          import_timeline.py       the weekly import, all six steps
          make_sample_data.py      an invented workbook shaped like the real export
          seed_demo.py             invented team work, through the same validated path the app uses
          anonymize.py             swap real staff names for invented ones before publishing
tests/                             170 tests, 164 of which need no database
docs/                              project charter and plan
```

## Design notes

A few decisions that are not obvious from reading the code, and the reasoning behind them.

**Two files know things, everything else asks them.** `conf/config.py` is the only place
that knows a table name or a path, and `app/lib/db.py` is the only place that knows what
the database is. Every screen calls functions in `db.py` and never opens a connection of
its own. That is why the project runs from any folder with no configuration, and why
swapping DuckDB for Postgres would mean rewriting one module.

**Upserting is not deleting and re-inserting.** The two produce identical table contents,
so they look interchangeable. They are not: `timeline_change_log` and `data_work_items`
both point at `official_timeline`, and deleting a row breaks those references, so the
second import of the same file fails outright. `ON CONFLICT DO UPDATE` fails too, because
DuckDB rewrites an update as a delete and insert when the table has an outgoing foreign key
of its own. What works is the plain two halves: update the rows whose key exists, insert
the ones whose key does not. That is `db.upsert`.

**One foreign key is deliberately not enforced**, for the reason just above. Which one and
why is at `SKIP_FK` in `scripts/create_tables.py`.

**The import runs in one transaction, and parents are written first.** The tempting order
is to write the `import_run` record last, so a crash leaves no record claiming success. The
transaction already guarantees that, and writing the parent last would only mean
`official_timeline.source_import_id` pointing at a row that does not exist yet.

**Date arithmetic is the easiest thing to get silently wrong.** `date_diff` reads unit,
start, end. Swapping the last two turns "days since last update" negative instead of
raising, so the sign is asserted in a test rather than eyeballed.

**A missing date is not None.** A work item with no updates yet has no `last_update`, and
reading that column back through pandas gives `NaT`, which is neither `None` nor a float.
That slipped past two separate guards and broke a screen. Both now check properly and both
are covered.

## Running the tests

```bash
pytest
```

170 tests. They cover the thirteen cleaning rules, the key builder, the comparison, and
both ownership guards. The charter's success measure says no team field is ever changed by
an import, *proven by an automated test* rather than by inspection. That test is
`tests/test_gate.py`.

164 of them need no database and no network. The other six render each screen headlessly
and fail if it throws; they skip themselves until there is imported data to render, so run
the four setup commands first if you want them.

That last file was added after three bugs shipped. The app had been checked by confirming
the server answered on its port, which it did: a Streamlit page renders after that, so an
exception becomes a red box on the screen rather than a failed start. Every screen was
broken and nothing said so.

## Notes on the data

Every name, instrument and date in this repo is invented. The instruments are
plausible-sounding and fictional; no real regulation is named. The people are invented.
`scripts/anonymize.py` is what keeps it that way, and it reads its name map from a
gitignored file rather than carrying one, for the obvious reason.

The sample workbook is deliberately messy: trailing spaces on statuses, "N/A" in a numeric
column, dates written out as English text, a placeholder in a key field, one name spelled
two ways. All of it mirrors the mess the real export was profiled and found to contain.
Clean sample data would make the import look trivial and leave the cleaning log empty,
which is the opposite of the point.
