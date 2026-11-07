"""Time handling.

The platform stores timestamps as **naive UTC** (the PostgreSQL columns are
plain ``DateTime``, not ``timezone=True``), and presents them in the
organization's timezone. This module is the single place that knows that.

``utcnow()`` exists because ``datetime.utcnow()`` is deprecated from Python
3.12: it returns a naive value while claiming to be UTC, which is exactly the
ambiguity that causes off-by-hours bugs. This helper returns the same naive UTC
value, so swapping it in changes no behaviour — but the intent is now explicit
and there is one place to change if the schema ever moves to aware datetimes.

For anything user-facing, convert at the boundary with :func:`in_timezone`.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

try:  # Python 3.9+
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - very old interpreters
    ZoneInfo = None  # type: ignore

#: Default display timezone for this product (all operators are in Kenya).
DEFAULT_TIMEZONE = 'Africa/Nairobi'


def utcnow() -> datetime:
    """Current UTC time as a naive datetime (the storage convention)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def utcfromtimestamp(timestamp: float) -> datetime:
    """Naive UTC datetime from a POSIX timestamp."""
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).replace(tzinfo=None)


def in_timezone(value: Optional[datetime], tz: str = DEFAULT_TIMEZONE) -> Optional[datetime]:
    """Convert a stored (naive UTC) datetime to ``tz`` for display.

    Naive values are *assumed* to be UTC, which is what the database holds.
    """
    if value is None:
        return None
    if ZoneInfo is None:  # pragma: no cover
        return value
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(ZoneInfo(tz))


def start_of_day_utc(tz: str = DEFAULT_TIMEZONE) -> datetime:
    """Naive UTC instant corresponding to 00:00 today in ``tz``."""
    if ZoneInfo is None:  # pragma: no cover
        return utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    local_now = datetime.now(ZoneInfo(tz))
    local_midnight = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    return local_midnight.astimezone(timezone.utc).replace(tzinfo=None)


def days_from_now(days: int) -> datetime:
    """Naive UTC datetime ``days`` in the future (or past, if negative)."""
    return utcnow() + timedelta(days=days)
