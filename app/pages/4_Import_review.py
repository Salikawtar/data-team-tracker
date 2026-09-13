"""Import review.

Upload the weekly export, see exactly what would change, and approve it here.

This is the one irreversible action in the tracker, so the screen is built around making
you look before you decide, not around making it quick:

  nothing is written until the comparison has been rendered
  the confirmation is typed, not clicked
  a file whose contents were already approved is refused
  the two Data Team tables are never touched, and that is checked before writing

Steps 0 to 4 are read only. You can upload and look as often as you like.
"""

import datetime
import hashlib
import io
import re

import pandas as pd
import streamlit as st

from lib import db, ui
from tracker import clean as C
from tracker import keys as K
from tracker import compare as CMP
from tracker import publish as PUB


ui.page("Import review", "The weekly export. Look first, then approve.")
ui.sidebar_footer()

t = db.t
c = db.contract()
cfg = db.cfg()
NOW = datetime.datetime.now()
me = ui.require_user()
if not me:
    st.stop()


def slug(header):
    s = re.sub(r"[^0-9a-zA-Z]+", "_", str(header).strip().lower()).strip("_")
    return re.sub(r"_+", "_", s)


# ------------------------------------------------------------------ history

with st.expander("Imports so far", expanded=False):
    runs = db.cached_query(f"""
        SELECT import_id, status, approved_by, approved_at, file_name,
               rows_read, rows_new, rows_changed, rows_missing,
               cleaning_actions, conflicts_reported
        FROM {t('core','import_run')}
        ORDER BY validated_at DESC LIMIT 20
    """)
    if not ui.table(runs):
        ui.empty("No imports yet. This will be the first.")

# ------------------------------------------------------------------ step 0

st.subheader("1. The file")

uploaded = st.file_uploader(
    "The weekly export, as .xlsx",
    type=["xlsx"],
    help="The workbook Trajce sends. It must contain the RSP Dates sheet.",
)

if not uploaded:
    ui.note("Nothing is read or written until you choose a file. Uploading is safe: the "
            "comparison runs and shows you what would change, and nothing is stored until "
            "you approve it further down.")
    st.stop()

raw_bytes = uploaded.getvalue()
FHASH = hashlib.sha256(raw_bytes).hexdigest()

prior = db.query(f"""
    SELECT import_id, approved_at, approved_by FROM {t('core','import_run')}
    WHERE file_hash = :h AND status = 'approved'
""", {"h": FHASH})

st.caption(f"{uploaded.name} · {len(raw_bytes)/1024:.1f} KB · hash {FHASH[:16]}")

already_imported = not prior.empty
if already_imported:
    p = prior.iloc[0]
    ui.note(f"This exact file was already approved as <b>{p['import_id']}</b> by "
            f"{p['approved_by']} on {p['approved_at']}. Re-approving it would change "
            f"nothing, because every row is keyed. Approval is disabled below.", warn=True)

# ------------------------------------------------------------------ gate

st.subheader("2. Does the file have the right shape")

import openpyxl

try:
    wb = openpyxl.load_workbook(io.BytesIO(raw_bytes), data_only=True)
except Exception as e:
    st.error(f"That file could not be opened as a workbook.\n\n{e}")
    st.stop()

if cfg.SOURCE_SHEET not in wb.sheetnames:
    st.error(f"There is no sheet called '{cfg.SOURCE_SHEET}' in this workbook. "
             f"It contains: {', '.join(wb.sheetnames)}.\n\n"
             f"This is not the official export.")
    st.stop()

ws = wb[cfg.SOURCE_SHEET]
headers = [cell.value for cell in ws[1]]
missing, unexpected = c.check_source_columns(headers)

g1, g2 = st.columns(2)
with g1:
    st.caption("Sheets in the file")
    st.code("\n".join(wb.sheetnames))
with g2:
    st.caption("Columns")
    st.write(f"{len([h for h in headers if h])} found, {len(c.source_columns())} expected")

if missing:
    st.error("The file is missing columns the tracker needs, so nothing is loaded:\n\n"
             + "\n".join(f"- {h}" for h in missing))
    st.stop()

if unexpected:
    ui.note("Columns in the file that the tracker does not know about. They are ignored. "
            "If one of them matters, it has to be added to the data dictionary first."
            "<br><br>" + ", ".join(f"<code>{h}</code>" for h in unexpected))

st.success("Shape is right.")

# ------------------------------------------------------------------ clean

st.subheader("3. What had to be cleaned")

raw_rows = [dict(zip(headers, r)) for r in ws.iter_rows(min_row=2, values_only=True) if r[0]]

known_ids = set()
if cfg.INSTRUMENT_SHEET in wb.sheetnames:
    known_ids = {str(r[0]).strip().upper()
                 for r in wb[cfg.INSTRUMENT_SHEET].iter_rows(min_row=2, values_only=True)
                 if r[0]}

with st.spinner("Cleaning and keying..."):
    rows, log, divergences, errors = C.clean_rows(raw_rows, c, known_ids=known_ids or None)

if errors:
    st.error(f"{len(errors)} validation error(s). Nothing can be imported until these are "
             f"resolved, either in the source file or by extending the data dictionary.")
    st.write(errors[:25])
    if len(errors) > 25:
        st.caption(f"...and {len(errors) - 25} more")
    st.stop()

K.add_keys(rows)
try:
    K.check_unique(rows)
except ValueError as e:
    st.error(str(e))
    st.stop()

provisional = [r for r in rows if r["key_is_provisional"]]

by_rule = {}
for e in log:
    by_rule.setdefault(e.rule_id, []).append(e)

ui.metrics([
    (len(raw_rows), "rows read"),
    (len(log), "values cleaned"),
    (len({r["timeline_item_id"] for r in rows}), "unique keys"),
    (len(provisional), "provisional keys", len(provisional) > 0),
    (len(divergences), "naming divergences", len(divergences) > 0),
    (0, "errors"),
])

with st.expander(f"The {len(log)} values that were cleaned, by rule"):
    for rule_id in sorted(by_rule):
        entries = by_rule[rule_id]
        try:
            desc = c.rule(rule_id).rule
        except KeyError:
            desc = ""
        st.markdown(f"**{rule_id}** · {desc} · {len(entries)} change(s)")
        st.dataframe(pd.DataFrame(
            [{"row": e.source_row_number, "field": e.field_name,
              "was": e.raw_value, "became": e.cleaned_value} for e in entries[:30]]),
            width="stretch", hide_index=True)
        if len(entries) > 30:
            st.caption(f"...and {len(entries) - 30} more")

if divergences:
    ui.note("<b>Naming divergences from the SRS.</b> These are reported, never corrected. "
            "A French abbreviation is a translation decision, and the export disagreeing "
            "with the specification is something a person has to resolve.", warn=True)
    st.dataframe(pd.DataFrame(
        [{"row": d.source_row_number, "field": d.field_name,
          "export says": d.value, "SRS says": d.expected} for d in divergences]),
        width="stretch", hide_index=True)

if provisional:
    ui.note(f"{len(provisional)} row(s) have a provisional key, because a key field is still "
            f"TBD. Their ids will change when the real value arrives, and they will then "
            f"appear as one delete plus one insert. That is correct, but it should not be "
            f"a surprise later.")

# ------------------------------------------------------------------ compare

st.subheader("4. What would change")

current = db.query(f"SELECT * FROM {t('core','official_timeline')}").to_dict("records")
comparison = CMP.compare(rows, current, c)
counts = comparison.counts

st.markdown(f"### {comparison.headline()}")

ui.metrics([
    (counts["new"], "new"),
    (counts["changed"], "changed", counts["changed"] > 0),
    (counts["restored"], "restored"),
    (counts["missing"], "now missing", counts["missing"] > 0),
    (counts["unchanged"], "unchanged"),
])

if not comparison.has_changes:
    ui.note("Nothing has changed since the last approved import. Approving would record the "
            "run and move every row's last seen date, and nothing else.")

tabs = st.tabs(["Changed", "New", "Now missing", "Restored"])

with tabs[0]:
    if counts["changed"]:
        st.caption("Every field that moved, with its old and new value. Dates are the ones "
                   "worth reading closely.")
        detail = []
        for r in comparison.of("changed"):
            for ch in r.changes:
                detail.append({
                    "instrument": r.row.get("instrument_id"),
                    "item": str(r.row.get("implementation_need"))[:50],
                    "field": ch.field,
                    "was": ch.old,
                    "becomes": ch.new,
                })
        st.dataframe(pd.DataFrame(detail), width="stretch", hide_index=True)

        st.caption("Summary by field")
        st.dataframe(pd.DataFrame(
            [{"field": f, "changes": n} for f, n in comparison.changes_by_field().items()]),
            width="stretch", hide_index=True)

        dates = comparison.date_changes()
        if dates:
            ui.note(f"<b>{len(dates)} date change(s).</b> This is the schedule moving, and "
                    f"it is the thing this tracker exists to make visible.")
    else:
        ui.empty("Nothing changed.")

with tabs[1]:
    if counts["new"]:
        st.dataframe(pd.DataFrame([{
            "id": r.timeline_item_id,
            "instrument": r.row.get("instrument_id"),
            "fiscal year": r.row.get("fiscal_year"),
            "item": str(r.row.get("implementation_need"))[:70],
            "launch": r.row.get("production_launch_date"),
        } for r in comparison.of("new")]), width="stretch", hide_index=True)
    else:
        ui.empty("No new items.")

with tabs[2]:
    if counts["missing"]:
        ui.note("These are flagged, never deleted. Data Team work items may point at them, "
                "and deleting one would orphan real work.")
        st.dataframe(pd.DataFrame([{
            "id": r.timeline_item_id,
            "instrument": r.previous.get("instrument_id"),
            "item": str(r.previous.get("implementation_need"))[:70],
            "last seen": r.previous.get("last_seen_date"),
        } for r in comparison.of("missing")]), width="stretch", hide_index=True)
    else:
        ui.empty("Nothing disappeared.")

with tabs[3]:
    if counts["restored"]:
        st.dataframe(pd.DataFrame([{
            "id": r.timeline_item_id,
            "instrument": r.row.get("instrument_id"),
            "item": str(r.row.get("implementation_need"))[:70],
        } for r in comparison.of("restored")]), width="stretch", hide_index=True)
    else:
        ui.empty("Nothing came back.")

# ------------------------------------------------------------------ plan

seq = int(db.scalar(
    f"SELECT COUNT(*) FROM {t('core','import_run')} WHERE import_id LIKE :p",
    {"p": f"imp_{NOW:%Y_%m_%d}_%"}, default=0) or 0) + 1
IMPORT_ID = PUB.make_import_id(NOW, seq)

plan = PUB.plan_publish(comparison, c, IMPORT_ID, NOW, current=current)
PUB.add_staging(plan, raw_rows, rows, log, c, NOW, slug)
PUB.build_import_run(
    plan, comparison,
    file_name=uploaded.name, file_ext="xlsx", fhash=FHASH,
    sheet_name=cfg.SOURCE_SHEET, rows_read=len(raw_rows),
    provisional=len(provisional), divergences=len(divergences),
    status="approved", validated_at=NOW, approved_by=me, approved_at=NOW,
    source_file_path=f"uploaded via the app by {me}",
)

guard_ok, guard_msg = True, ""
try:
    PUB.assert_no_team_fields(plan, c)
    PUB.assert_idempotent(plan, comparison)
except AssertionError as e:
    guard_ok, guard_msg = False, str(e)

# ------------------------------------------------------------------ approve

st.markdown("---")
st.subheader("5. Approve")

if not guard_ok:
    st.error("The guards refused this plan, so approval is not offered.\n\n" + guard_msg)
    st.stop()

st.caption(f"Import id {IMPORT_ID} · approving as {me}")

ui.note(
    f"Approving writes <b>{len(plan.upserts)}</b> rows to the official timeline, "
    f"<b>{len(plan.changes)}</b> to the change log, "
    f"<b>{len(plan.cleaning_log)}</b> to the cleaning log, and one import record."
    f"<br><br>"
    f"It does not touch <code>data_work_items</code> or <code>team_updates</code>. "
    f"That has just been checked, not assumed."
)

if already_imported:
    st.info("Approval is disabled because this exact file was already approved. "
            "Upload a newer export.")
    st.stop()

confirm = st.text_input(
    "Type APPROVE to confirm",
    placeholder="APPROVE",
    help="Typed rather than clicked, because this is the one action in the tracker that "
         "cannot be undone with a click.",
)

go = st.button("Approve and write", type="primary", disabled=(confirm.strip() != "APPROVE"))

if go:
    prog = st.progress(0.0, "Writing")
    try:
        ids = [r["timeline_item_id"] for r in plan.upserts]

        # Replace by key, so re-running cannot double up.
        db.delete_where_in(t("core", "official_timeline"), "timeline_item_id", ids)
        prog.progress(0.15, "official timeline")
        db.insert_many(t("core", "official_timeline"), plan.upserts)
        prog.progress(0.45, "change log")

        db.delete_where_in(t("core", "timeline_change_log"), "import_id", [IMPORT_ID])
        db.insert_many(t("core", "timeline_change_log"), plan.changes)
        prog.progress(0.60, "raw copy")

        db.delete_where_in(t("raw", "timeline_import"), "import_id", [IMPORT_ID])
        db.insert_many(t("raw", "timeline_import"), plan.raw)
        prog.progress(0.75, "clean copy")

        db.delete_where_in(t("clean", "timeline_import"), "import_id", [IMPORT_ID])
        db.insert_many(t("clean", "timeline_import"), plan.clean)
        prog.progress(0.88, "cleaning log")

        db.delete_where_in(t("clean", "cleaning_log"), "import_id", [IMPORT_ID])
        db.insert_many(t("clean", "cleaning_log"), plan.cleaning_log)
        prog.progress(0.96, "import record")

        # Written last, so a failure above leaves no record of success.
        db.delete_where_in(t("core", "import_run"), "import_id", [IMPORT_ID])
        db.insert(t("core", "import_run"), plan.import_run)
        prog.progress(1.0, "done")

    except Exception as e:
        st.error(f"The write failed part way through.\n\n{e}\n\n"
                 f"Nothing was written: the whole import runs in one transaction, so a "
                 f"failure part way rolls all of it back. Fix whatever the message above "
                 f"describes and run it again. scripts/import_timeline.py does the same "
                 f"job from the terminal and prints more detail if you need it.")
        st.stop()

    db.refresh()
    st.success(f"Approved and written as {IMPORT_ID}.")
    st.balloons()

    after = db.query(f"""
        SELECT COUNT(*) AS timeline,
               SUM(CASE WHEN is_missing THEN 1 ELSE 0 END) AS flagged_missing
        FROM {t('core','official_timeline')}
    """)
    team = db.query(f"""
        SELECT (SELECT COUNT(*) FROM {t('core','data_work_items')}) AS work_items,
               (SELECT COUNT(*) FROM {t('core','team_updates')}) AS updates
    """)
    ui.metrics([
        (int(after["timeline"].iloc[0]), "timeline rows"),
        (int(after["flagged_missing"].iloc[0] or 0), "flagged missing"),
        (int(team["work_items"].iloc[0]), "work items, untouched"),
        (int(team["updates"].iloc[0]), "updates, untouched"),
    ])

    if divergences:
        ui.note("Do not forget the naming divergences above. Take them to whoever owns the "
                "timeline. Do not edit the export yourself.", warn=True)
