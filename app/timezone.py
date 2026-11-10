import os
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

DEFAULT_TIMEZONE = "Europe/Zurich"


def configured_timezone() -> ZoneInfo:
    """The business's time zone, from APP_TIMEZONE. An invalid name fails at startup."""
    return ZoneInfo(os.getenv("APP_TIMEZONE") or DEFAULT_TIMEZONE)


APP_TZ = configured_timezone()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def now() -> datetime:
    return _utc_now().astimezone(APP_TZ)


def today() -> date:
    return now().date()


def utc_now() -> datetime:
    """The current UTC time without a zone, as stored in the waitlist tables."""
    return _utc_now().replace(tzinfo=None)


def local_to_utc(day: date, clock_time: time) -> datetime:
    """Converts a shop-local date and time to UTC without a zone, for storage."""
    local = datetime.combine(day, clock_time, tzinfo=APP_TZ)
    return local.astimezone(timezone.utc).replace(tzinfo=None)


def utc_to_local(moment: datetime) -> datetime:
    """Converts a stored UTC time to the shop's local time."""
    return moment.replace(tzinfo=timezone.utc).astimezone(APP_TZ)


def is_unambiguous_local(day: date, clock_time: time) -> bool:
    """False if this local time is skipped or repeated by a daylight-saving change."""
    local = datetime.combine(day, clock_time, tzinfo=APP_TZ)
    return local.replace(fold=0).utcoffset() == local.replace(fold=1).utcoffset()


def interval_is_unambiguous(start: datetime, minutes: int) -> bool:
    """False if any minute of this local interval is skipped or repeated by a clock change.

    The end is exclusive, so an interval may finish exactly when a clock change begins.
    """
    moments = (start + timedelta(minutes=offset) for offset in range(minutes))
    return all(is_unambiguous_local(moment.date(), moment.time()) for moment in moments)
