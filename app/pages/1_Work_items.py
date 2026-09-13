"""Work items.

The Data Team's own jobs, each attached to one official timeline item.

The official file says when things are due. It says nothing about what this team has to do.
A work item is that missing half. Without it the tracker is just a copy of the spreadsheet.

Writes to data_work_items only. Every row passes team.assert_no_official_fields before it
reaches a table, which is the mirror of the guard the import runs.
"""

import datetime

import streamlit as st

from lib import db, ui
from tracker import team as T


ui.page("Work items", "What the Data Team owns, and who owns it.")
ui.sidebar_footer()

t = db.t
c = db.contract()
me = ui.current_user()
NOW = datetime.datetime.now()

work_types = sorted(c.allowed("work_type"))
team_status = sorted(c.allowed("team_status"))

# ------------------------------------------------------------------ existing

st.subheader("Current work")

f1, f2, f3 = st.columns([2, 2, 3])
with f1:
    only_mine = st.toggle("Only mine", value=False, disabled=not me)
with f2:
    status_filter = st.selectbox("Status", ["all"] + team_status)
with f3:
    show_closed = st.toggle("Include closed", value=False)

items = db.cached_query(T.sql_work_items(
    t,
    active_only=not show_closed,
    owner=me if only_mine and me else None,
    status=None if status_filter == "all" else status_filter,
))

if items.empty:
    ui.empty("No work items yet.",
             "Add one below. Pick the timeline item your team has work for, say what kind "
             "of work it is, and who owns it.")
else:
    st.caption(f"{len(items)} item(s)")
    ui.table(items)

st.markdown("---")

# ------------------------------------------------------------------ create

st.subheader("Add a work item")

picker = db.cached_query(T.sql_timeline_picker(t, limit=500))

if picker.empty:
    ui.empty("No timeline items to attach work to.",
             "Run scripts/import_timeline.py first.")
    st.stop()

picker["label"] = (
    picker["instrument_id"].astype(str) + "  ·  "
    + picker["instrument_short_name_en"].fillna("").astype(str) + "  ·  "
    + picker["fiscal_year"].astype(str) + "  ·  "
    + picker["implementation_need"].astype(str).str.slice(0, 60)
)

with st.form("create_work_item", clear_on_submit=False):
    choice = st.selectbox(
        "Timeline item",
        options=picker.index,
        format_func=lambda i: picker.loc[i, "label"],
        help="Sorted by launch date, soonest first.",
    )

    a, b = st.columns(2)
    with a:
        work_type = st.selectbox("Work type", work_types)
        owner = st.text_input("Owner", value=me,
                              help="Whoever is accountable for this piece of work.")
    with b:
        target = st.date_input("Internal target date", value=None,
                               help="Your own deadline, normally earlier than the official "
                                    "launch. Leave blank if you do not have one yet.")
        deliverable = st.text_input("Required deliverable",
                                    placeholder="Star schema documentation")

    dependency = st.text_input("Dependency",
                               placeholder="Waiting on the VOC2 schema sign off")

    row = picker.loc[choice]
    st.caption(f"Official launch for this item: "
               f"{row['production_launch_date'] or 'not set'} · "
               f"status {row['official_status']}")
    if row.get("key_is_provisional"):
        st.warning("This timeline item has a provisional key, because one of its key fields "
                   "is still TBD. Its id will change when that is filled in, and this work "
                   "item would then point at nothing. Safe to add, worth knowing.")

    submitted = st.form_submit_button("Create work item", type="primary")

if submitted:
    try:
        new = T.new_work_item(
            c,
            timeline_item_id=row["timeline_item_id"],
            work_type=work_type,
            data_owner_user_id=owner,
            internal_target_date=target,
            required_deliverable=deliverable or None,
            dependency=dependency or None,
            now=NOW,
        )
    except T.ValidationError as e:
        st.error(str(e))
        st.stop()

    T.assert_no_official_fields(new, "data_work_items", c)
    db.insert(t("core", "data_work_items"), new)

    st.success(f"Created. {work_type} for {row['instrument_id']}, owned by {owner}.")
    st.caption("Next: open Submit update and put the first update against it.")
    st.rerun()

st.markdown("---")

# ------------------------------------------------------------------ change

st.subheader("Change a work item")

if items.empty:
    ui.empty("Nothing to change yet.")
    st.stop()

items["label"] = (
    items["instrument_id"].astype(str) + "  ·  "
    + items["work_type"].astype(str) + "  ·  "
    + items["data_owner_user_id"].astype(str) + "  ·  "
    + items["current_status"].astype(str)
)

pick = st.selectbox("Work item", options=items.index,
                    format_func=lambda i: items.loc[i, "label"])
chosen = items.loc[pick]

col1, col2 = st.columns([3, 2])

with col1:
    new_status = st.selectbox(
        "Set status to", team_status,
        index=team_status.index(chosen["current_status"])
        if chosen["current_status"] in team_status else 0,
    )
    if st.button("Update status"):
        db.update_fields(
            t("core", "data_work_items"), "data_work_item_id",
            chosen["data_work_item_id"],
            {"current_status": new_status, "updated_at": NOW},
        )
        st.success(f"Status set to {new_status}.")
        st.rerun()

with col2:
    st.caption("Closing hides an item from the active list. It is not deleted, and its "
               "update history stays.")
    if st.button("Close this work item"):
        db.update_fields(
            t("core", "data_work_items"), "data_work_item_id",
            chosen["data_work_item_id"],
            {"is_active": False, "updated_at": NOW},
        )
        st.success("Closed.")
        st.rerun()

with st.expander("Update history for this item"):
    hist = db.cached_query(T.sql_updates_for(t, chosen["data_work_item_id"]))
    if not ui.table(hist):
        ui.empty("No updates submitted against this item yet.")
