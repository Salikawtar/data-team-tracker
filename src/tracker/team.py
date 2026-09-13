"""The Data Team's own side of the boundary.

`publish.py` writes the official record and is forbidden from touching anything a person
typed. This module is the mirror of that: it writes what people type and is forbidden from
touching the official record.

    import        ->  official_timeline, timeline_change_log, import_run
    application   ->  data_work_items, team_updates

The one link across the boundary is `data_work_items.timeline_item_id`, and it is a read.

Same shape as the rest of the project: this module builds and validates rows and returns
SQL, and never executes it. The caller does. That keeps the part where the mistakes happen
testable on its own.
"""

from datetime import datetime, date
from typing import Optional
import uuid


# ------------------------------------------------------------------ errors


class ValidationError(ValueError):
    """Raised with a message written for the person filling in the form, not for a log."""


def _fail(*messages):
    raise ValidationError("\n".join(m for m in messages if m))


# ------------------------------------------------------------------ helpers


def _clean(value, max_length=None, field=None):
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    if max_length and len(s) > max_length:
        _fail(f"{field} is {len(s)} characters, and the limit is {max_length}.")
    return s


def _required(value, field, max_length=None):
    v = _clean(value, max_length, field)
    if v is None:
        _fail(f"{field} is required.")
    return v


def _one_of(value, allowed, field):
    v = _clean(value)
    if v is None:
        _fail(f"{field} is required.")
    if v not in allowed:
        _fail(f"{field} is {v!r}, which is not an allowed value.",
              f"Choose one of: {', '.join(sorted(allowed))}")
    return v


def _as_date(value, field):
    if value is None or str(value).strip() == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    s = str(value).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%B %d, %Y", "%A, %B %d, %Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    _fail(f"{field} is {s!r}, which is not a date.",
          "Use YYYY-MM-DD, for example 2026-09-15.")


def _percent(value):
    if value is None or str(value).strip() == "":
        return None
    try:
        n = int(float(str(value).strip()))
    except ValueError:
        _fail(f"Percent complete is {value!r}, which is not a number.")
    if not 0 <= n <= 100:
        _fail(f"Percent complete is {n}, and it has to be between 0 and 100.")
    return n


# ------------------------------------------------------------------ the guard


def official_owned_fields(contract) -> set:
    return {f.name for f in contract.fields("official_timeline")}


def assert_no_official_fields(row: dict, table: str, contract):
    """The mirror of publish.assert_no_team_fields.

    The application writes what people type. If it ever wrote an official field it would be
    quietly overwriting the source record, and the next import would silently undo it, which
    is the worst of both.
    """
    allowed = {f.name for f in contract.fields(table)}
    official = official_owned_fields(contract)
    link = {"timeline_item_id"}          # the one crossing, and it is only ever a foreign key

    offences = []
    for col in row:
        if col not in allowed:
            offences.append(f"{col!r} is not a field of {table}")
        elif col in official and col not in link:
            offences.append(f"{col!r} is owned by the official record and must not be "
                            f"written by the application")
    if offences:
        raise AssertionError(
            f"This row would write outside the Data Team's own data:\n  "
            + "\n  ".join(sorted(set(offences)))
        )
    return True


# ------------------------------------------------------------------ work items


def new_work_item(contract, *, timeline_item_id: str, work_type: str,
                  data_owner_user_id: str, now: datetime,
                  planned_start_date=None, internal_target_date=None,
                  current_status: str = "Not Started", dependency=None,
                  required_deliverable=None) -> dict:
    """Build one row for data_work_items, validated against the dictionary."""
    tid = _required(timeline_item_id, "Timeline item")
    if len(tid) != 16 or any(c not in "0123456789abcdef" for c in tid):
        _fail(f"Timeline item {tid!r} does not look like a timeline_item_id.",
              "It should be 16 lowercase hexadecimal characters, for example c8f454a04d269131.")

    row = {
        "data_work_item_id": str(uuid.uuid4()),
        "timeline_item_id": tid,
        "work_type": _one_of(work_type, contract.allowed("work_type"), "Work type"),
        "data_owner_user_id": _required(data_owner_user_id, "Owner"),
        "planned_start_date": _as_date(planned_start_date, "Planned start"),
        "internal_target_date": _as_date(internal_target_date, "Internal target"),
        "current_status": _one_of(current_status, contract.allowed("team_status"), "Status"),
        "dependency": _clean(dependency, 500, "Dependency"),
        "required_deliverable": _clean(required_deliverable, 500, "Required deliverable"),
        "is_active": True,
        "created_at": now,
        "updated_at": now,
    }

    s, t = row["planned_start_date"], row["internal_target_date"]
    if s and t and t < s:
        _fail(f"The internal target ({t}) is before the planned start ({s}).")

    assert_no_official_fields(row, "data_work_items", contract)
    return row


def update_work_item_status(contract, existing: dict, *, current_status: str,
                            now: datetime) -> dict:
    """Change only the status and the timestamp. Everything else is left alone."""
    out = dict(existing)
    out["current_status"] = _one_of(current_status, contract.allowed("team_status"), "Status")
    out["updated_at"] = now
    assert_no_official_fields(out, "data_work_items", contract)
    return out


def close_work_item(contract, existing: dict, *, now: datetime) -> dict:
    """Deactivate rather than delete, so the update history stays readable."""
    out = dict(existing)
    out["is_active"] = False
    out["updated_at"] = now
    assert_no_official_fields(out, "data_work_items", contract)
    return out


# ------------------------------------------------------------------ updates


def new_update(contract, *, data_work_item_id: str, submitted_by_user_id: str,
               status_at_update: str, progress_update: str, now: datetime,
               percent_complete=None, blocker=None, next_action=None,
               support_required=None, expected_completion_date=None) -> dict:
    """Build one row for team_updates.

    Only two things are mandatory beyond the identifiers: the status, and a sentence about
    what actually happened. Everything else is optional on purpose. A long required form
    produces filler, and filler is worse than a gap because it looks like information.
    """
    row = {
        "update_id": str(uuid.uuid4()),
        "data_work_item_id": _required(data_work_item_id, "Work item"),
        "submitted_by_user_id": _required(submitted_by_user_id, "Submitted by"),
        "status_at_update": _one_of(status_at_update, contract.allowed("team_status"),
                                    "Status"),
        "percent_complete": _percent(percent_complete),
        "progress_update": _required(progress_update, "Progress update", 2000),
        "blocker": _clean(blocker, 1000, "Blocker"),
        "next_action": _clean(next_action, 1000, "Next action"),
        "support_required": _clean(support_required, 1000, "Support required"),
        "expected_completion_date": _as_date(expected_completion_date, "Expected completion"),
        "submitted_at": now,
    }

    if row["status_at_update"] == "Blocked" and not row["blocker"]:
        _fail("The status is Blocked but no blocker is described.",
              "Say what is blocking it. A blocked item with no reason cannot be helped.")

    assert_no_official_fields(row, "team_updates", contract)
    return row


def days_since(when, today: date) -> Optional[int]:
    """How many days ago, or None if there is no date.

    `when != when` is the check for a missing value that is not None. Pandas represents a
    null timestamp as NaT, and NaT is the only kind of object that is not equal to itself.
    It matters because a work item with no updates yet has no last_update, and reading that
    column back through pandas gives NaT rather than None. Checking for it without importing
    pandas keeps this module free of any framework, which is what lets the tests run without
    one.
    """
    if when is None or when != when:
        return None
    d = when.date() if isinstance(when, datetime) else when
    return (today - d).days


def is_stale(last_update, today: date, threshold_days: int = 7) -> bool:
    """No update in a week. The charter's target is 90 percent of active items fresher
    than this."""
    n = days_since(last_update, today)
    return n is None or n > threshold_days


# ------------------------------------------------------------------ queries
#
# SQL is returned rather than executed, so the screens stay thin and these can be read and
# tested without a database.


def sql_timeline_picker(t, limit: int = 500) -> str:
    """Timeline items a work item could be attached to, most urgent first."""
    return f"""
        SELECT timeline_item_id, instrument_id, instrument_short_name_en,
               fiscal_year, implementation_need, official_status,
               production_launch_date, is_missing, key_is_provisional
        FROM {t('core', 'official_timeline')}
        WHERE NOT is_missing
        ORDER BY production_launch_date NULLS LAST
        LIMIT {int(limit)}
    """


def sql_work_items(t, *, active_only: bool = True, owner: str = None,
                   instrument_id: str = None, status: str = None) -> str:
    where = ["1 = 1"]
    if active_only:
        where.append("w.is_active")
    if owner:
        where.append(f"w.data_owner_user_id = '{_esc(owner)}'")
    if instrument_id:
        where.append(f"o.instrument_id = '{_esc(instrument_id)}'")
    if status:
        where.append(f"w.current_status = '{_esc(status)}'")

    return f"""
        SELECT w.data_work_item_id, w.work_type, w.current_status,
               w.data_owner_user_id, w.internal_target_date,
               o.instrument_id, o.instrument_short_name_en, o.implementation_need,
               o.production_launch_date, o.official_status,
               u.last_update, u.updates
        FROM {t('core', 'data_work_items')} w
        JOIN {t('core', 'official_timeline')} o
          ON o.timeline_item_id = w.timeline_item_id
        LEFT JOIN (
            SELECT data_work_item_id, MAX(submitted_at) AS last_update,
                   COUNT(*) AS updates
            FROM {t('core', 'team_updates')} GROUP BY data_work_item_id
        ) u ON u.data_work_item_id = w.data_work_item_id
        WHERE {' AND '.join(where)}
        ORDER BY o.production_launch_date NULLS LAST, w.internal_target_date NULLS LAST
    """


def sql_updates_for(t, data_work_item_id: str, limit: int = 20) -> str:
    return f"""
        SELECT submitted_at, submitted_by_user_id, status_at_update, percent_complete,
               progress_update, blocker, next_action, support_required,
               expected_completion_date
        FROM {t('core', 'team_updates')}
        WHERE data_work_item_id = '{_esc(data_work_item_id)}'
        ORDER BY submitted_at DESC
        LIMIT {int(limit)}
    """


def sql_last_update(t, data_work_item_id: str) -> str:
    return sql_updates_for(t, data_work_item_id, limit=1)


def sql_stale_items(t, days: int = 7) -> str:
    """Active work with no update in the last `days`, worst first. This is the list the
    lead looks at."""
    return f"""
        SELECT w.data_work_item_id, w.work_type, w.current_status, w.data_owner_user_id,
               o.instrument_id, o.instrument_short_name_en, o.implementation_need,
               u.last_update,
               -- date_diff reads unit, start, end. Reversing the last two by accident
               -- makes this column negative instead of failing, so test_team.py checks
               -- the sign.
               date_diff('day', CAST(u.last_update AS DATE), CURRENT_DATE) AS days_since
        FROM {t('core', 'data_work_items')} w
        JOIN {t('core', 'official_timeline')} o
          ON o.timeline_item_id = w.timeline_item_id
        LEFT JOIN (
            SELECT data_work_item_id, MAX(submitted_at) AS last_update
            FROM {t('core', 'team_updates')} GROUP BY data_work_item_id
        ) u ON u.data_work_item_id = w.data_work_item_id
        WHERE w.is_active
          AND w.current_status NOT IN ('Complete')
          AND (u.last_update IS NULL
               OR u.last_update < CURRENT_DATE() - INTERVAL {int(days)} DAYS)
        ORDER BY u.last_update NULLS FIRST
    """


def sql_blocked(t) -> str:
    return f"""
        WITH latest AS (
            SELECT data_work_item_id, MAX(submitted_at) AS last_update
            FROM {t('core', 'team_updates')} GROUP BY data_work_item_id
        )
        SELECT o.instrument_id, o.instrument_short_name_en, w.work_type,
               w.data_owner_user_id, u.blocker, u.support_required, u.submitted_at
        FROM {t('core', 'team_updates')} u
        JOIN latest l ON l.data_work_item_id = u.data_work_item_id
                     AND l.last_update = u.submitted_at
        JOIN {t('core', 'data_work_items')} w
          ON w.data_work_item_id = u.data_work_item_id
        JOIN {t('core', 'official_timeline')} o
          ON o.timeline_item_id = w.timeline_item_id
        WHERE u.status_at_update = 'Blocked' AND w.is_active
        ORDER BY u.submitted_at
    """


def sql_upcoming_deadlines(t, days: int = 30) -> str:
    return f"""
        SELECT instrument_id, instrument_short_name_en, implementation_need,
               official_status, release_number,
               user_stories_deadline, code_freeze_date, uat_start_date,
               production_launch_date, coming_into_force_date,
               date_diff('day', CURRENT_DATE, production_launch_date) AS days_to_launch
        FROM {t('core', 'official_timeline')}
        WHERE NOT is_missing
          AND production_launch_date BETWEEN CURRENT_DATE()
              AND CURRENT_DATE() + INTERVAL {int(days)} DAYS
        ORDER BY production_launch_date
    """


def sql_recent_official_changes(t, limit: int = 100) -> str:
    """What moved in the most recent approved import."""
    return f"""
        SELECT c.changed_at, o.instrument_id, o.instrument_short_name_en,
               o.implementation_need, c.field_name, c.old_value, c.new_value, c.change_type
        FROM {t('core', 'timeline_change_log')} c
        LEFT JOIN {t('core', 'official_timeline')} o
          ON o.timeline_item_id = c.timeline_item_id
        WHERE c.import_id = (
            SELECT import_id FROM {t('core', 'import_run')}
            WHERE status = 'approved' ORDER BY approved_at DESC LIMIT 1
        )
        AND c.change_type <> 'new'
        ORDER BY c.field_name
        LIMIT {int(limit)}
    """


def sql_coverage(t) -> str:
    """How many active items carry a fresh update. The charter's target is 90 percent."""
    return f"""
        SELECT COUNT(*) AS active_items,
               SUM(CASE WHEN u.last_update >= CURRENT_DATE() - INTERVAL 7 DAYS
                        THEN 1 ELSE 0 END) AS fresh,
               ROUND(100.0 * SUM(CASE WHEN u.last_update >= CURRENT_DATE() - INTERVAL 7 DAYS
                        THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0), 1) AS percent_fresh
        FROM {t('core', 'data_work_items')} w
        LEFT JOIN (
            SELECT data_work_item_id, MAX(submitted_at) AS last_update
            FROM {t('core', 'team_updates')} GROUP BY data_work_item_id
        ) u ON u.data_work_item_id = w.data_work_item_id
        WHERE w.is_active AND w.current_status <> 'Complete'
    """


def _esc(value: str) -> str:
    """Single quotes doubled. Values reaching these helpers come from dropdowns and from
    IDs the tracker generated, but a name with an apostrophe should not break a screen."""
    return str(value).replace("'", "''")
