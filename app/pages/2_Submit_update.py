"""Submit update.

The most important screen in the project, and the one most likely to fail. Not because of
the code, but because if people do not fill it in then every dashboard is empty and the
rest of the work was wasted.

So it is short. Two things are required: your status, and a sentence about what happened.
Everything else is optional. A long mandatory form produces filler, and filler is worse
than a gap because it looks like information.

Updates are append only. Submitting again adds to the history rather than replacing it.
"""

import datetime

import streamlit as st

from lib import db, ui
from tracker import team as T


ui.page("Submit update", "Two boxes are required. The rest only if you have something to say.")
ui.sidebar_footer()

t = db.t
c = db.contract()
NOW = datetime.datetime.now()
TODAY = NOW.date()
team_status = sorted(c.allowed("team_status"))

me = ui.require_user()
if not me:
    st.stop()

# ------------------------------------------------------------------ pick

mine = db.cached_query(T.sql_work_items(t, active_only=True, owner=me))
everything = db.cached_query(T.sql_work_items(t, active_only=True))

if everything.empty:
    ui.empty("There are no work items to update.",
             "Open Work items and add what your team owns.")
    st.stop()

if mine.empty:
    ui.note(f"Nothing is assigned to <b>{me}</b>. Showing everything that is open, so you "
            f"can still submit. If work should be yours, change its owner on the Work items "
            f"screen.", warn=True)
    pool = everything
else:
    pool = everything if st.toggle("Show everyone's work", value=False) else mine

pool = pool.copy()
pool["stale"] = pool["last_update"].apply(lambda v: T.is_stale(v, TODAY))
pool["label"] = pool.apply(
    lambda r: (("● " if r["stale"] else "○ ")
               + f"{r['instrument_id']}  ·  {r['work_type']}  ·  {r['current_status']}"
               + f"  ·  last update {ui.days_ago(r['last_update'], TODAY)}"),
    axis=1,
)
pool = pool.sort_values(["stale", "last_update"], ascending=[False, True])

n_stale = int(pool["stale"].sum())
ui.metrics([
    (len(pool), "items shown"),
    (n_stale, "need an update", n_stale > 0),
])
st.caption("A filled circle means no update in the last seven days.")

pick = st.selectbox("Work item", options=pool.index,
                    format_func=lambda i: pool.loc[i, "label"])
item = pool.loc[pick]

st.caption(f"{item['instrument_id']} {item['instrument_short_name_en'] or ''} · "
           f"{str(item['implementation_need'])[:90]}")
if item["production_launch_date"] is not None:
    st.caption(f"Official launch {item['production_launch_date']} · "
               f"your internal target {item['internal_target_date'] or 'not set'}")

# ------------------------------------------------------------------ last time

last = db.cached_query(T.sql_last_update(t, item["data_work_item_id"]))
if last.empty:
    ui.note("No updates on this item yet. This will be the first.")
else:
    p = last.iloc[0]
    with st.expander(f"What was said last time, {ui.days_ago(p['submitted_at'], TODAY)}",
                     expanded=True):
        st.markdown(f"**{p['status_at_update']}** by {p['submitted_by_user_id']}")
        st.write(p["progress_update"])
        for label, key in [("Blocker", "blocker"), ("Next action", "next_action"),
                           ("Support required", "support_required"),
                           ("Expected completion", "expected_completion_date")]:
            if p[key] is not None and str(p[key]).strip():
                st.caption(f"{label}: {p[key]}")
        st.caption("Say what has moved since then. No need to repeat any of the above.")

# ------------------------------------------------------------------ the form

st.markdown("---")

default_status = (item["current_status"] if item["current_status"] in team_status
                  else team_status[0])

with st.form("submit_update", clear_on_submit=True):
    a, b = st.columns([1, 2])
    with a:
        status = st.selectbox("Status", team_status,
                              index=team_status.index(default_status))
    with b:
        progress = st.text_area(
            "What happened", height=90,
            placeholder="Extraction script finished for tanks and racks.",
            help="One or two sentences. This is the only free text that is required.")

    blocker = st.text_input(
        "Blocker", placeholder="Waiting on the UAT environment refresh",
        help="Required if the status is Blocked. A blocked item with no reason cannot be "
             "helped by anyone.")

    with st.expander("Optional"):
        c1, c2 = st.columns(2)
        with c1:
            next_action = st.text_input("Next action",
                                        placeholder="Validate the FLM averages tab")
            support = st.text_input("Support needed",
                                    placeholder="A decision on the null handling rule")
        with c2:
            expected = st.date_input("Expected completion", value=None)
            percent = st.slider("Percent complete", 0, 100, 0,
                                help="Leave at zero if you would rather not guess. It is "
                                     "optional on purpose.")

    submitted = st.form_submit_button("Submit update", type="primary")

if submitted:
    try:
        row = T.new_update(
            c,
            data_work_item_id=item["data_work_item_id"],
            submitted_by_user_id=me,
            status_at_update=status,
            progress_update=progress,
            blocker=blocker or None,
            next_action=next_action or None,
            support_required=support or None,
            expected_completion_date=expected,
            percent_complete=percent if percent else None,
            now=NOW,
        )
    except T.ValidationError as e:
        st.error(str(e))
        st.stop()

    T.assert_no_official_fields(row, "team_updates", c)
    db.insert(t("core", "team_updates"), row)

    # Keep the work item's own status in step with what was just reported.
    if item["current_status"] != status:
        db.update_fields(t("core", "data_work_items"), "data_work_item_id",
                         item["data_work_item_id"],
                         {"current_status": status, "updated_at": NOW})
        st.info(f"Work item status moved from {item['current_status']} to {status}.")

    st.success("Submitted.")
    st.rerun()

# ------------------------------------------------------------------ coverage

st.markdown("---")

cov = db.cached_query(T.sql_coverage(t))
if not cov.empty:
    active = int(cov["active_items"].iloc[0] or 0)
    fresh = int(cov["fresh"].iloc[0] or 0)
    percent = float(cov["percent_fresh"].iloc[0] or 0)
    ui.metrics([
        (active, "active items"),
        (fresh, "updated this week"),
        (f"{percent:g}%", "coverage", percent < 90),
    ])
    st.caption("The charter's target is 90 percent of active items carrying an update less "
               "than seven days old.")

stale = db.cached_query(T.sql_stale_items(t, days=7))
if not stale.empty:
    with st.expander(f"{len(stale)} item(s) still owing an update"):
        ui.table(stale)

with st.expander("History for this item"):
    ui.table(db.cached_query(T.sql_updates_for(t, item["data_work_item_id"])))
