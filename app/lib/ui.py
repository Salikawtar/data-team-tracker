"""Shared bits of the interface.

Small on purpose. The tracker is a working tool, so the styling exists to make state
readable at a glance, not to decorate.
"""

from datetime import date, datetime
from typing import Optional

import pandas as pd
import streamlit as st


ACCENT = "#16697A"

CSS = """
<style>
  .block-container { padding-top: 2.2rem; max-width: 1250px; }
  .metric-row { display: flex; gap: 14px; flex-wrap: wrap; margin: 4px 0 18px; }
  .metric {
      background: rgba(128,128,128,.06);
      border: 1px solid rgba(128,128,128,.22);
      border-radius: 6px; padding: 12px 16px; min-width: 132px;
  }
  .metric b { display:block; font-size: 26px; line-height: 1.15; font-variant-numeric: tabular-nums; }
  .metric span { font-size: 12px; opacity: .72; }
  .metric.alert { border-color: #B3402F; background: rgba(179,64,47,.09); }
  .metric.alert b { color: #B3402F; }
  .rule { border-left: 3px solid #16697A; background: rgba(22,105,122,.08);
          padding: 12px 16px; border-radius: 0 4px 4px 0; margin: 14px 0; font-size: 14px; }
  .warn { border-left-color: #B3402F; background: rgba(179,64,47,.09); }
  .muted { opacity: .7; font-size: 13.5px; }
</style>
"""


def page(title: str, subtitle: str = ""):
    st.set_page_config(page_title=f"{title} · Data Team Tracker", layout="wide")
    st.markdown(CSS, unsafe_allow_html=True)
    st.title(title)
    if subtitle:
        st.markdown(f"<p class='muted'>{subtitle}</p>", unsafe_allow_html=True)


def metrics(items):
    """items: list of (value, label) or (value, label, alert_bool)."""
    html = ["<div class='metric-row'>"]
    for it in items:
        value, label = it[0], it[1]
        alert = len(it) > 2 and it[2]
        html.append(f"<div class='metric{' alert' if alert else ''}'>"
                    f"<b>{value}</b><span>{label}</span></div>")
    html.append("</div>")
    st.markdown("".join(html), unsafe_allow_html=True)


def note(text: str, warn: bool = False):
    st.markdown(f"<div class='rule{' warn' if warn else ''}'>{text}</div>",
                unsafe_allow_html=True)


def empty(reason: str, what_to_do: str = ""):
    """An empty table should say why it is empty. Otherwise it reads as broken."""
    st.info(reason + (f"\n\n{what_to_do}" if what_to_do else ""))


def table(df: pd.DataFrame, height: Optional[int] = None):
    """One table style for the whole app.

    Two Streamlit details are handled here rather than at every call site.

    `height` is only passed when a caller asked for one. Streamlit used to read None as
    "size it yourself"; it now rejects None and wants a positive number of pixels, or the
    words 'stretch' or 'content'. Leaving the argument out entirely is the same as the old
    default, and it works on both.

    `width="stretch"` replaces `use_container_width=True`, which is deprecated and already
    past the date it was meant to be removed.
    """
    if df is None or df.empty:
        return False
    extra = {"height": height} if height is not None else {}
    st.dataframe(df, width="stretch", hide_index=True, **extra)
    return True


def days_ago(value, today: Optional[date] = None) -> str:
    """The same missing-value trap as tracker.team.days_since.

    The original guard only caught None and a float NaN. A null timestamp read out of
    DuckDB through pandas is NaT, which is neither. pd.isna covers all three, and this
    module already depends on pandas so it can just say so.
    """
    if value is None or pd.isna(value):
        return "never"
    today = today or date.today()
    d = value.date() if isinstance(value, (datetime, pd.Timestamp)) else value
    n = (today - d).days
    if n == 0:
        return "today"
    if n == 1:
        return "yesterday"
    return f"{n} days ago"


def who(user_email: Optional[str]) -> str:
    return (user_email or "").split("@")[0] or "unknown"


def current_user() -> str:
    """Who is signed in, when the app is hosted somewhere that knows.

    A hosted deployment puts the signed in user in a request header. Falling back to
    whatever account the server runs as would silently attribute one person's update to
    another, so when the header is absent the screens say so rather than guessing.
    """
    try:
        headers = st.context.headers
        for key in ("X-Forwarded-Email", "X-Forwarded-Preferred-Username", "X-Forwarded-User"):
            v = headers.get(key)
            if v:
                return who(v)
    except Exception:
        pass
    return ""


def single_user() -> str:
    """The name to use when the app cannot tell who is looking at it.

    That is the normal case on a laptop: there is no sign in, so everything the app does
    runs as whoever started it. `config.USER` is that person, and TRACKER_USER overrides
    it. TRACKER_SINGLE_USER is accepted as an older spelling of the same thing.

    The honest part is that this is the only answer the app can give, whoever is at the
    screen. `require_user` says so out loud rather than quietly stamping one name on
    someone else's work.
    """
    import os
    for source in (os.getenv("TRACKER_SINGLE_USER"), os.getenv("TRACKER_USER")):
        if source:
            return who(source)
    try:
        from lib import db
        return who(db.cfg().USER)
    except Exception:
        return ""


def require_user() -> str:
    u = current_user()
    if u:
        return u

    u = single_user()
    if u:
        note(f"This copy has no sign in, so it cannot tell who is looking at it. Updates "
             f"will be recorded as <b>{u}</b>. Change the name below if that is not you, "
             f"or set TRACKER_USER before starting the app.")
        typed = st.text_input("Recording updates as", value=u, key="_single_user")
        return who(typed) or u

    st.warning("The app could not tell who you are, so an update would be recorded "
               "against nobody. Type your user id below.")
    return who(st.text_input("Your user id", key="_manual_user"))


def sidebar_footer():
    with st.sidebar:
        st.markdown("---")
        st.markdown("<p class='muted'>Data Team Work Tracker<br>"
                    "Official data is read only here. It changes only through the weekly "
                    "import.</p>", unsafe_allow_html=True)
        if st.button("Refresh data", width="stretch"):
            from lib import db
            db.refresh()
            st.rerun()
