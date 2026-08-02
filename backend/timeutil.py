"""UTC time helpers.

SQLite (via SQLAlchemy's DateTime) does not persist tzinfo, so a freshly-created tz-aware
datetime and a DB-read naive one can't be compared/subtracted. To avoid that class of bug
we use **naive UTC** everywhere internally, and only attach the UTC marker when serializing
to ISO for the API/UI (so the browser parses it as UTC, not local time).
"""
from __future__ import annotations

from datetime import datetime, timezone


def now() -> datetime:
    """Naive UTC 'now'."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()
