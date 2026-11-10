"""A read-only iCalendar (RFC 5545) feed of appointments, for staff calendars.

Privacy rule: the feed's URL may be shared by accident, so it carries the service name
and times only. The query selects no customer column and no note, so personal data
cannot reach the feed by mistake.
"""
from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from app.models import QueueEntry, ShopStatus
from app.services.booking import SHOP_STATUS_ID, SLOT_OCCUPYING_STATUSES
from app.services.catalog import SERVICES
from app.timezone import local_to_utc, today

SHOP_NAME = "Demo Barbershop"
DAYS_BEHIND = 1
DAYS_AHEAD = 60
MAX_EVENTS = 500
FALLBACK_SLOT_MINUTES = ShopStatus.slot_duration_minutes.default.arg
MAX_LINE_OCTETS = 75


def _escape(text: str) -> str:
    return (
        text.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
    )


def _fold(line: str) -> str:
    """Splits a line at 75 octets, never inside a multi-byte character."""
    pieces, current, octets = [], "", 0
    for character in line:
        size = len(character.encode("utf-8"))
        if octets + size > MAX_LINE_OCTETS:
            pieces.append(current)
            current, octets = " ", 1  # The leading space counts towards the limit.
        current += character
        octets += size
    pieces.append(current)
    return "\r\n".join(pieces)


def _utc_stamp(moment: datetime) -> str:
    return moment.strftime("%Y%m%dT%H%M%SZ")


def _event_lines(row, slot_minutes: int) -> list[str]:
    start = local_to_utc(row.queue_date, row.appointment_time)
    end = start + timedelta(minutes=row.duration_minutes or slot_minutes)
    # updated_at is stored as shop-local wall-clock time.
    changed = local_to_utc(row.updated_at.date(), row.updated_at.time()) if row.updated_at else start
    service = SERVICES.get(row.service_id) or {}
    return [
        "BEGIN:VEVENT",
        f"UID:queue-{row.id}@phone-assistant.invalid",
        f"DTSTAMP:{_utc_stamp(changed)}",
        f"LAST-MODIFIED:{_utc_stamp(changed)}",
        f"DTSTART:{_utc_stamp(start)}",
        f"DTEND:{_utc_stamp(end)}",
        f"SUMMARY:{_escape(service.get('name') or 'Appointment')}",
        "DESCRIPTION:",
        "END:VEVENT",
    ]


def _appointments(db: Session, first_day: date, last_day: date):
    return (
        db.query(
            QueueEntry.id,
            QueueEntry.queue_date,
            QueueEntry.appointment_time,
            QueueEntry.duration_minutes,
            QueueEntry.service_id,
            QueueEntry.updated_at,
        )
        .filter(QueueEntry.queue_date >= first_day, QueueEntry.queue_date <= last_day)
        .filter(QueueEntry.status.in_(SLOT_OCCUPYING_STATUSES))
        .order_by(QueueEntry.queue_date, QueueEntry.appointment_time, QueueEntry.id)
        .limit(MAX_EVENTS)
        .all()
    )


def build_feed(db: Session) -> str:
    current_day = today()
    rows = _appointments(db, current_day - timedelta(days=DAYS_BEHIND), current_day + timedelta(days=DAYS_AHEAD))
    # Read directly: the usual settings helper creates the row, and the feed never writes.
    stored_minutes = (
        db.query(ShopStatus.slot_duration_minutes).filter(ShopStatus.id == SHOP_STATUS_ID).scalar()
    )
    # A stored zero or negative length must not produce events that end before they start.
    slot_minutes = stored_minutes if stored_minutes and stored_minutes > 0 else FALLBACK_SLOT_MINUTES

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Phone Assistant//Calendar Feed//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_escape(SHOP_NAME)}",
    ]
    for row in rows:
        lines.extend(_event_lines(row, slot_minutes))
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"
