"""Home.

The screen to open on a Monday. Five questions, in order of urgency, and one ranked list
of what to actually do today.

Read only. Nothing on this page writes.
"""

import streamlit as st

from lib import db, ui
from tracker import team as T


ui.page("Data Team Work Tracker",
        "Official milestones from the weekly import, joined to the Data Team's own work.")
ui.sidebar_footer()

ok, detail = db.health()
if not ok:
    st.error(f"Cannot reach the tracker tables.\n\n{detail}")
    ui.note("The database file named in conf/config.py could not be opened. Run "
            "scripts/create_tables.py if it does not exist yet, and check that no other "
            "program has it open: one writer at a time.")
    st.stop()

t = db.t
c = db.contract()

# ------------------------------------------------------------------ headline

cov = db.cached_query(T.sql_coverage(t))
n_official = db.scalar(f"SELECT COUNT(*) FROM {t('core','official_timeline')} "
                       f"WHERE NOT is_missing", default=0)
blocked = db.cached_query(T.sql_blocked(t))
due = db.cached_query(T.sql_upcoming_deadlines(t, days=30))
stale = db.cached_query(T.sql_stale_items(t, days=7))

active = int(cov["active_items"].iloc[0] or 0) if not cov.empty else 0
percent = float(cov["percent_fresh"].iloc[0] or 0) if not cov.empty else 0.0

ui.metrics([
    (n_official, "timeline items"),
    (active, "active work items"),
    (len(blocked), "blocked", len(blocked) > 0),
    (len(due), "due in 30 days"),
    (len(stale), "need an update", len(stale) > 0),
    (f"{percent:g}%", "update coverage", percent < 90),
])

last = db.cached_query(f"""
    SELECT import_id, approved_at, approved_by, rows_new, rows_changed, rows_missing,
           rows_provisional_key, conflicts_reported
    FROM {t('core','import_run')}
    WHERE status = 'approved' ORDER BY approved_at DESC LIMIT 1
""")

if last.empty:
    ui.note("No approved import yet. Run scripts/import_timeline.py before using this screen.",
            warn=True)
else:
    r = last.iloc[0]
    ui.note(
        f"Last import <b>{r['import_id']}</b>, approved by {r['approved_by']} "
        f"{ui.days_ago(r['approved_at'])}. "
        f"{r['rows_new']} new, {r['rows_changed']} changed, {r['rows_missing']} missing, "
        f"{r['rows_provisional_key']} provisional keys, "
        f"{r['conflicts_reported']} naming divergences reported."
    )

if active == 0:
    ui.note("There are no work items yet, so the team sections below are empty. That is not "
            "a fault. Open <b>Work items</b> and add what your team owns.", warn=True)

st.markdown("---")

# ------------------------------------------------------------------ attention

st.subheader("What needs attention today")

rows = []
for _, r in blocked.iterrows():
    rows.append(("1 BLOCKED", f"{r['instrument_id']} {r['work_type']}",
                 str(r["blocker"] or "")[:90], r["data_owner_user_id"]))
for _, r in stale.iterrows():
    n = r.get("days_since")
    rows.append(("2 NO UPDATE", f"{r['instrument_id']} {r['work_type']}",
                 "never updated" if n is None or n != n else f"{int(n)} days since an update",
                 r["data_owner_user_id"]))
for _, r in db.cached_query(T.sql_upcoming_deadlines(t, days=14)).iterrows():
    rows.append(("3 DUE SOON",
                 f"{r['instrument_id']} {r['instrument_short_name_en'] or ''}",
                 f"launches in {int(r['days_to_launch'])} days", ""))

if rows:
    import pandas as pd
    df = pd.DataFrame(sorted(rows), columns=["what", "item", "detail", "owner"])
    df["what"] = df["what"].str.split(" ", n=1).str[1]
    ui.table(df)
else:
    ui.empty("Nothing needs attention.",
             "Either everything is genuinely fine, or the tracker is empty. "
             "The counts at the top tell you which.")

st.markdown("---")

# ------------------------------------------------------------------ tabs

tab1, tab2, tab3, tab4 = st.tabs(
    ["Official changes", "Blocked", "Due soon", "Owing an update"])

with tab1:
    st.caption("What moved in the most recent approved import. New rows are excluded, "
               "because on a first import everything is new and that tells you nothing.")
    changes = db.cached_query(T.sql_recent_official_changes(t))
    if not ui.table(changes):
        ui.empty("Nothing changed in the last import, or that import was the first one.",
                 "This fills in from the second approved import onward. It is the evidence "
                 "behind statements like 'the FPR launch has moved three times'.")

with tab2:
    st.caption("Only the most recent update per work item counts, so something blocked "
               "last month and fine now does not appear.")
    if not ui.table(blocked):
        ui.empty("Nothing is blocked.")

with tab3:
    st.caption("Timeline items launching in the next 30 days.")
    if not ui.table(due):
        ui.empty("Nothing launches in the next 30 days.")

with tab4:
    st.caption("Active work with no update in seven days. Items never updated are first, "
               "because those are the ones created and then forgotten.")
    if not ui.table(stale):
        ui.empty("Everything active has a recent update.")

st.markdown("---")

# ------------------------------------------------------------------ movement

st.subheader("Schedule movement")
st.caption("The question the change log exists to answer: is the official schedule moving, "
           "and which dates.")

movement = db.cached_query(f"""
    SELECT DATE_TRUNC('WEEK', changed_at) AS week, field_name, COUNT(*) AS changes
    FROM {t('core','timeline_change_log')}
    WHERE change_type = 'changed'
      AND (field_name LIKE '%_date' OR field_name LIKE '%_deadline')
    GROUP BY 1, 2 ORDER BY 1 DESC, 3 DESC
""")

if movement.empty:
    ui.empty("No date changes recorded yet.",
             "A change needs two versions to compare, so this stays empty until a second "
             "import has been approved.")
else:
    left, right = st.columns([2, 3])
    with left:
        ui.table(movement)
    with right:
        pivot = movement.pivot_table(index="week", columns="field_name",
                                     values="changes", fill_value=0)
        st.bar_chart(pivot)
