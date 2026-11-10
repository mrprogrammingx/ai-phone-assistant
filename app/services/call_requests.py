"""Turns a completed call into a booking, or a follow-up for staff.

Used only when Suisse Line live tools are unavailable. The assistant then promises
nothing during the call, and this module acts on the webhook sent afterwards.
"""
import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import date, time, timedelta

from sqlalchemy import and_, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import CallRequest, CallRequestStatus
from app.services import booking
from app.services.booking import BookingRejected, InvalidPhone, normalize_phone
from app.services.catalog import SERVICES, staff_note
from app.services.email import ADMIN_EMAIL, send_email
from app.services.waitlist import RETENTION
from app.timezone import today, utc_now

logger = logging.getLogger("barbershop.assistant")

MAX_CALL_ID_LENGTH = 200
COMPLETED_EVENTS = {"call.completed", "call_completed"}
MAX_NAME_LENGTH = 100
SUGGESTIBLE_REASONS = {"slot_taken", "date_past", "incomplete"}
MAX_SUGGESTED_TIMES = 3
SUGGESTION_SEARCH_DAYS = 7


@dataclass(frozen=True)
class ParsedRequest:
    call_id: str | None
    is_call_completed: bool
    caller_phone: str | None
    name: str | None
    phone: str | None
    service_id: str | None
    date: str | None
    time: str | None


def _first_text(sources: list[dict], *keys: str) -> str | None:
    for source in sources:
        for key in keys:
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def parse_call_completed(payload: dict) -> ParsedRequest:
    """Reads a call.completed webhook. THE ONLY CODE THAT KNOWS THE PAYLOAD'S SHAPE.

    ASSUMED, NOT VERIFIED. No Suisse Line account exists. The public example shows
    `event`, `callId` and `phoneNumber`. The details object is invented:

        {
          "event": "call.completed",            optional, other events are ignored
          "callId": "...",                      or "call_id"
          "phoneNumber": "+41...",              optional, the number that called
          "extracted": {                        or "data", "data.extracted", or top level
            "name": "...", "phone": "...", "service_id": "...",
            "date": "YYYY-MM-DD", "time": "HH:MM"
          }
        }

    Change only this function when the real shape is known. Unknown fields are ignored,
    and a value that is missing or not text is treated as absent. The calling number
    is kept separate and is never used as the booking's phone number.
    """
    if not isinstance(payload, dict):
        payload = {}

    nested: list[dict] = []
    for container in ("extracted", "data"):
        value = payload.get(container)
        if isinstance(value, dict):
            inner = value.get("extracted")
            if isinstance(inner, dict):
                nested.append(inner)
            nested.append(value)
    sources = [*nested, payload]

    call_id = _first_text(sources, "callId", "call_id")
    if call_id is not None and len(call_id) > MAX_CALL_ID_LENGTH:
        call_id = None

    event = _first_text([payload], "event")
    return ParsedRequest(
        call_id=call_id,
        is_call_completed=event is None or event in COMPLETED_EVENTS,
        caller_phone=_first_text(sources, "phoneNumber", "caller_phone", "callerNumber"),
        name=_first_text(sources, "name"),
        phone=_first_text(sources, "phone"),
        service_id=_first_text(sources, "service_id"),
        date=_first_text(sources, "date"),
        time=_first_text(sources, "time"),
    )


@dataclass(frozen=True)
class RecordResult:
    request: CallRequest
    created: bool


def _normalized(phone: str | None) -> str | None:
    if phone is None:
        return None
    try:
        return normalize_phone(phone)
    except InvalidPhone:
        return None


def _parsed_date(value: str | None) -> date | None:
    if value is None or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _parsed_time(value: str | None) -> time | None:
    if value is None or not re.fullmatch(r"\d{2}:\d{2}", value):
        return None
    try:
        return time.fromisoformat(value)
    except ValueError:
        return None


def _delete_expired_requests(db: Session) -> None:
    """Deletes requests once they have been closed for the retention period. Runs under the lock.

    A booked request is closed when it arrives, and a follow-up when staff mark it handled.
    An open follow-up is never deleted by age. Appointments are untouched, because a
    request only points to one.
    """
    cutoff = utc_now() - RETENTION
    db.query(CallRequest).filter(
        or_(
            and_(CallRequest.status == CallRequestStatus.booked, CallRequest.received_at <= cutoff),
            and_(CallRequest.handled_at.isnot(None), CallRequest.handled_at <= cutoff),
        )
    ).delete(synchronize_session=False)


def delete_expired_requests(db: Session) -> None:
    """For the local command, so old data goes even if no webhook arrives."""
    booking.get_or_create_shop_status(db)
    try:
        booking.acquire_booking_lock(db)
        _delete_expired_requests(db)
        db.commit()
    except Exception:
        db.rollback()
        raise


def _find_existing(db: Session, call_id: str) -> CallRequest | None:
    return db.query(CallRequest).filter(CallRequest.call_id == call_id).first()


def _first_problem(parsed: ParsedRequest, phone, service, requested_date, requested_time) -> str | None:
    """The reason this request cannot be booked. Missing details are never guessed."""
    required = (parsed.call_id, parsed.name, parsed.phone, parsed.service_id, parsed.date, parsed.time)
    if any(value is None for value in required):
        return "incomplete"
    if phone is None:
        return "invalid_phone"
    if service is None:
        return "unknown_service"
    if requested_date is None:
        return "invalid_date"
    if requested_time is None:
        return "invalid_time"
    return None


def _add_suggested_times(db: Session, request: CallRequest, service: dict | None) -> None:
    """Notes the next free times for staff to offer. Reads only, and holds nothing."""
    if request.reason not in SUGGESTIBLE_REASONS or service is None or request.requested_date is None:
        return

    start = max(request.requested_date, today())
    for offset in range(SUGGESTION_SEARCH_DAYS + 1):
        day = start + timedelta(days=offset)
        slots = booking.slot_availability(db, day, duration_minutes=service["duration_minutes"])
        free = [slot.time.strftime("%H:%M") for slot in slots if slot.available][:MAX_SUGGESTED_TIMES]
        if free:
            request.suggested_times = f"{day.isoformat()} {', '.join(free)}"
            request.suggested_as_of = utc_now()
            return


def _handle_in_lock(db: Session, shop_status, parsed: ParsedRequest, call_id: str) -> CallRequest:
    phone = _normalized(parsed.phone)
    service = SERVICES.get(parsed.service_id)
    requested_date = _parsed_date(parsed.date)
    requested_time = _parsed_time(parsed.time)

    request = CallRequest(
        call_id=call_id,
        status=CallRequestStatus.follow_up,
        name=parsed.name[:MAX_NAME_LENGTH] if parsed.name else None,
        # The calling number helps staff ring back. It is never used to book.
        phone=phone or _normalized(parsed.caller_phone),
        service_id=service["id"] if service else None,
        requested_date=requested_date,
        requested_time=requested_time,
    )

    request.reason = _first_problem(parsed, phone, service, requested_date, requested_time)
    if request.reason is not None:
        _add_suggested_times(db, request, service)
        return request

    try:
        result = booking.book_in_lock(
            db,
            shop_status,
            name=request.name,
            phone=phone,
            appointment_date=requested_date,
            appointment_time=requested_time,
            note=staff_note(service),
            reuse_active_booking=True,
            rename_existing_customer=False,
            service_id=service["id"],
            duration_minutes=service["duration_minutes"],
        )
    except BookingRejected as rejected:
        request.reason = rejected.code
        _add_suggested_times(db, request, service)
        return request

    request.status = CallRequestStatus.booked
    request.booking_id = result.entry.id
    return request


def record_call(db: Session, parsed: ParsedRequest, raw_body: bytes) -> RecordResult:
    """Books the request or records a follow-up. A repeated delivery changes nothing.

    The booking and its record share one transaction under the booking lock, so a
    duplicate waits and then sees the original outcome. The unique call_id is the backstop.
    """
    call_id = parsed.call_id or f"body-{hashlib.sha256(raw_body).hexdigest()}"
    shop_status = booking.get_or_create_shop_status(db)

    try:
        booking.acquire_booking_lock(db)
        _delete_expired_requests(db)

        existing = _find_existing(db, call_id)
        if existing is not None:
            db.commit()
            return RecordResult(request=existing, created=False)

        request = _handle_in_lock(db, shop_status, parsed, call_id)
        db.add(request)
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = _find_existing(db, call_id)
        if existing is None:
            raise
        return RecordResult(request=existing, created=False)
    except Exception:
        db.rollback()
        raise

    return RecordResult(request=request, created=True)


def staff_recipients(db: Session) -> list[str]:
    configured = booking.get_or_create_shop_status(db).notification_emails or ""
    recipients = [email for email in configured.split(",") if email]
    return recipients or ([ADMIN_EMAIL] if ADMIN_EMAIL else [])


def notify_staff(
    request_id: int,
    status: str,
    reason: str | None,
    recipients: list[str],
    suggested_times: str | None = None,
) -> None:
    """Runs after the response is sent. A notification problem must never affect the outcome.

    The text carries no name or number, because dry-run mode writes email bodies to the log.
    Staff read the details with the local list_requests command.
    """
    if status == CallRequestStatus.booked.value:
        subject = "Phone request booked"
        body = f"Phone request {request_id} was booked."
    else:
        subject = "Phone request needs follow-up"
        body = f"Phone request {request_id} needs follow-up. Reason: {reason}."
        if suggested_times:
            body += f" Free when it arrived, and may be taken since: {suggested_times}."

    try:
        for recipient in recipients:
            send_email(recipient, subject, body)
    except Exception:
        logger.warning("staff_notification=failed")


def open_follow_ups(db: Session) -> list[CallRequest]:
    return (
        db.query(CallRequest)
        .filter(CallRequest.status == CallRequestStatus.follow_up, CallRequest.handled_at.is_(None))
        .order_by(CallRequest.received_at, CallRequest.id)
        .all()
    )


def mark_handled(db: Session, request_id: int) -> bool:
    """Marks an open follow-up as handled. Returns False if there is no such open request."""
    request = db.get(CallRequest, request_id)
    if request is None or request.status != CallRequestStatus.follow_up or request.handled_at is not None:
        return False
    request.handled_at = utc_now()
    db.commit()
    return True
