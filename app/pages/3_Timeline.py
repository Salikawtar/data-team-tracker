"""Timeline.

The official record, read only. Nothing on this page can change it. It changes once a week,
through the import, and only after a person has approved the comparison.

Useful for looking something up, and for seeing the history of a date that has moved.
"""

import streamlit as st

from lib import db, ui
from tracker import team as T


ui.page("Timeline", "The official record. Read only, always.")
ui.sidebar_footer()

t = db.t
c = db.contract()

ui.note("This page cannot change anything. Official dates come from the weekly export and "
        "are updated only by an approved import. If a date here is wrong, it is wrong in "
        "the source, and the fix belongs with whoever owns the timeline.")

# ------------------------------------------------------------------ filters

rows = db.cached_query(f"""
    SELECT timeline_item_id, instrument_id, instrument_short_name_en,
           instrument_short_name_fr, instrument_full_name, fiscal_year,
           implementation_need, program_scope, official_status, priority, complexity,
           release_number, user_stories_deadline, code_freeze_date, uat_start_date,
           uat_end_date, production_launch_date, target_blue_stamp_date,
           coming_into_force_date, lead_rsm_concierge, lead_business_analyst,
           product_owner, notes, is_missing, key_is_provisional,
           first_seen_date, last_seen_date
    FROM {t('core','official_timeline')}
    ORDER BY production_launch_date NULLS LAST
""")

if rows.empty:
    ui.empty("The timeline is empty.", "Run scripts/import_timeline.py first.")
    st.stop()

n_missing = int(rows["is_missing"].sum())
n_prov = int(rows["key_is_provisional"].sum())

ui.metrics([
    (len(rows), "rows"),
    (rows["instrument_id"].nunique(), "instruments"),
    (n_missing, "flagged missing", n_missing > 0),
    (n_prov, "provisional keys", n_prov > 0),
])

f1, f2, f3, f4 = st.columns(4)
with f1:
    instruments = ["all"] + sorted(rows["instrument_id"].dropna().unique().tolist())
    inst = st.selectbox("Instrument", instruments)
with f2:
    statuses = ["all"] + sorted(rows["official_status"].dropna().unique().tolist())
    status = st.selectbox("Status", statuses)
with f3:
    years = ["all"] + sorted(rows["fiscal_year"].dropna().unique().tolist())
    year = st.selectbox("Fiscal year", years)
with f4:
    include_missing = st.toggle("Include missing", value=False)

view = rows.copy()
if inst != "all":
    view = view[view["instrument_id"] == inst]
if status != "all":
    view = view[view["official_status"] == status]
if year != "all":
    view = view[view["fiscal_year"] == year]
if not include_missing:
    view = view[~view["is_missing"]]

search = st.text_input("Search the implementation need or the notes", "")
if search:
    s = search.lower()
    view = view[
        view["implementation_need"].astype(str).str.lower().str.contains(s)
        | view["notes"].astype(str).str.lower().str.contains(s)
        | view["instrument_full_name"].astype(str).str.lower().str.contains(s)
    ]

st.caption(f"{len(view)} of {len(rows)} rows")

COLUMNS = ["instrument_id", "instrument_short_name_en", "fiscal_year",
           "implementation_need", "official_status", "priority", "release_number",
           "user_stories_deadline", "code_freeze_date", "uat_start_date", "uat_end_date",
           "production_launch_date", "coming_into_force_date"]
ui.table(view[COLUMNS], height=430)

st.markdown("---")

# ------------------------------------------------------------------ one item

st.subheader("One item in full")

if view.empty:
    ui.empty("Nothing matches those filters.")
    st.stop()

view = view.copy()
view["label"] = (view["instrument_id"].astype(str) + "  ·  "
                 + view["fiscal_year"].astype(str) + "  ·  "
                 + view["implementation_need"].astype(str).str.slice(0, 70))
pick = st.selectbox("Item", options=view.index,
                    format_func=lambda i: view.loc[i, "label"])
item = view.loc[pick]

a, b = st.columns([3, 2])

with a:
    st.markdown(f"**{item['instrument_full_name'] or item['instrument_id']}**")
    st.caption(f"{item['instrument_short_name_en'] or ''} / "
               f"{item['instrument_short_name_fr'] or ''}  ·  "
               f"{item['instrument_id']}  ·  {item['fiscal_year']}")
    st.write(item["implementation_need"])
    if item["program_scope"]:
        st.caption(str(item["program_scope"])[:600])
    if item["notes"]:
        st.markdown("**Notes**")
        st.caption(str(item["notes"]))
        st.caption("Notes are where the reason a date moved is usually written.")

with b:
    st.markdown("**Dates**")
    for label, key in [("User stories", "user_stories_deadline"),
                       ("Code freeze", "code_freeze_date"),
                       ("UAT start", "uat_start_date"),
                       ("UAT end", "uat_end_date"),
                       ("Production launch", "production_launch_date"),
                       ("Blue stamp", "target_blue_stamp_date"),
                       ("Coming into force", "coming_into_force_date")]:
        st.caption(f"{label}: {item[key] if item[key] is not None else 'not set'}")

    st.markdown("**People**")
    for label, key in [("Concierge", "lead_rsm_concierge"),
                       ("Business analyst", "lead_business_analyst"),
                       ("Product owner", "product_owner")]:
        st.caption(f"{label}: {item[key] or 'not set'}")

if item["key_is_provisional"]:
    ui.note("This row's key is provisional, because one of its key fields is still TBD. "
            "When that is filled in the id changes, and the row will appear as one delete "
            "plus one insert in the next import. That is correct behaviour, not a fault.",
            warn=True)

if item["is_missing"]:
    ui.note(f"This row is flagged missing. It last appeared on {item['last_seen_date']}. "
            f"It is kept rather than deleted, because work items may point at it.", warn=True)

# ------------------------------------------------------------------ history

st.markdown("**Everything that has changed on this item**")

hist = db.cached_query(f"""
    SELECT changed_at, field_name, old_value, new_value, change_type, import_id
    FROM {t('core','timeline_change_log')}
    WHERE timeline_item_id = :tid
    ORDER BY changed_at DESC, field_name
""", {"tid": item["timeline_item_id"]})

if hist.empty or (len(hist) == 1 and hist.iloc[0]["change_type"] == "new"):
    ui.empty("Nothing has changed since this item first appeared.",
             "History builds up from the second approved import onward.")
else:
    ui.table(hist[hist["change_type"] != "new"])

st.markdown("**The Data Team's work on this item**")

work = db.cached_query(f"""
    SELECT w.work_type, w.current_status, w.data_owner_user_id, w.internal_target_date,
           w.required_deliverable, w.dependency, w.is_active
    FROM {t('core','data_work_items')} w
    WHERE w.timeline_item_id = :tid
""", {"tid": item["timeline_item_id"]})

if not ui.table(work):
    ui.empty("No Data Team work is attached to this item.",
             "If your team has something to do for it, add it on the Work items screen.")
