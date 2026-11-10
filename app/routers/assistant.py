"""Tools for the phone assistant.

This router translates between the assistant and the booking service. It contains
no slot or booking rules. Business outcomes return HTTP 200 with `ok: false`,
because how Suisse Line's live tools handle other status codes is not documented.
"""
import base64
import datetime as dt
import json
import logging
import os
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import AfterValidator, BaseModel, Field, field_validator
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.access_log import redact_path
from app.auth import PATH_TOKEN_PREFIX, require_assistant_key, require_path_token
from app.database import get_db
from app.models import QueueEntry, ShopStatus
from app.schemas import CustomerName
from app.services.booking import (
    BookingNotFound,
    BookingRejected,
    InvalidPhone,
    book_slot_idempotent,
    cancel_booking,
    get_or_create_shop_status,
    normalize_phone,
    slot_availability,
    upcoming_bookings,
)
from app.services.catalog import CURRENCY, SERVICES, staff_note
from app.services.email import send_admin_checkin_notification
from app.services.waitlist import accept_offer, join_waitlist
from app.timezone import APP_TZ, now

logger = logging.getLogger("barbershop.assistant")

# The tools themselves, mounted twice at the bottom of this file: behind the header key,
# and behind a token in the path for a dashboard that cannot send headers.
tools = APIRouter()

router = APIRouter(
    prefix="/assistant",
    tags=["assistant"],
    dependencies=[Depends(require_assistant_key)],
)

token_router = APIRouter(
    prefix=PATH_TOKEN_PREFIX + "{token}",
    tags=["assistant"],
    dependencies=[Depends(require_path_token)],
)

MAX_OFFERED_SLOTS = 5


def _reject_time_zone(value: dt.time) -> dt.time:
    if value.tzinfo is not None:
        raise ValueError("times are shop-local and must not carry a time zone")
    return value


LocalTime = Annotated[dt.time, AfterValidator(_reject_time_zone)]
Name = CustomerName  # the same rule as the website: trimmed, 1 to 100 characters, one line
# Any text: `normalize_phone` alone decides what is a valid number, as it does on the website.
Phone = str


class _BlankOptionalsAreMissing(BaseModel):
    """Voice platforms may send an empty string for an optional field."""

    @field_validator("*", mode="before")
    @classmethod
    def _blank_to_none(cls, value):
        return None if value == "" else value


class AvailabilityRequest(_BlankOptionalsAreMissing):
    date: dt.date
    service_id: str
    from_time: LocalTime | None = Field(default=None, alias="from")
    to_time: LocalTime | None = Field(default=None, alias="to")


class CreateBookingRequest(_BlankOptionalsAreMissing):
    name: Name
    phone: Phone
    date: dt.date
    time: LocalTime
    service_id: str


class FindBookingRequest(_BlankOptionalsAreMissing):
    phone: Phone
    date: dt.date | None = None


class CancelBookingRequest(_BlankOptionalsAreMissing):
    phone: Phone
    date: dt.date
    time: LocalTime


class AcceptOfferRequest(_BlankOptionalsAreMissing):
    phone: Phone
    date: dt.date
    time: LocalTime


class JoinWaitlistRequest(_BlankOptionalsAreMissing):
    name: Name
    phone: Phone
    service_id: str
    date: dt.date
    from_time: LocalTime = Field(alias="from")
    to_time: LocalTime = Field(alias="to")
    consent: bool | None = None

    @field_validator("to_time")
    @classmethod
    def _range_is_in_order(cls, value, info):
        start = info.data.get("from_time")
        if start is not None and value < start:
            raise ValueError("the range must end after it starts")
        return value


def _ok(**fields) -> dict:
    return {"ok": True, **fields}


def _failure(request: Request, code: str, message: str) -> JSONResponse:
    logger.info("path=%s result=%s", redact_path(request.scope["path"]), code)
    return JSONResponse({"ok": False, "code": code, "message": message})


def install_error_handlers(app: FastAPI) -> None:
    """Turns business outcomes into the `ok: false` response the assistant understands."""

    def _is_tool_path(request: Request) -> bool:
        path = request.url.path
        return path.startswith(router.prefix + "/") and not path.startswith(router.prefix + "/webhook/")

    def _refusal_without_key(request: Request) -> JSONResponse | None:
        """A body that cannot be read fails before the route's key check runs, so the
        key is checked here: without it the answer is the same 401 as for any other call."""
        if request.scope["path"].startswith(PATH_TOKEN_PREFIX):
            return None  # A token route has no key, and the app has already checked its token.
        try:
            require_assistant_key(request)
        except HTTPException as refused:
            return JSONResponse({"detail": refused.detail}, status_code=refused.status_code)
        return None

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, error: StarletteHTTPException):
        # FastAPI answers a body it cannot decode with a bare 400. For a tool that is
        # invalid input like any other, so it gets the tool's usual answer.
        if error.status_code == 400 and _is_tool_path(request):
            return _refusal_without_key(request) or _failure(
                request, "invalid_input", "The request must be a JSON object containing this tool's fields."
            )
        # Everything else keeps FastAPI's usual answer.
        return JSONResponse({"detail": error.detail}, status_code=error.status_code, headers=error.headers)

    @app.exception_handler(RequestValidationError)
    async def _invalid_input(request: Request, error: RequestValidationError):
        if _is_tool_path(request):
            refused = _refusal_without_key(request)
            if refused is not None:
                return refused
        # Names the fields only. The caller's values must not be echoed or logged.
        fields = sorted(
            {part for item in error.errors() for part in item["loc"][1:] if isinstance(part, str)}
        )
        message = (
            f"Missing or invalid: {', '.join(fields)}."
            if fields
            else "The request must be a JSON object containing this tool's fields."
        )
        return _failure(request, "invalid_input", message)

    @app.exception_handler(BookingRejected)
    async def _rejected(request: Request, rejected: BookingRejected):
        return _failure(request, rejected.code, rejected.message)

    @app.exception_handler(InvalidPhone)
    async def _invalid_phone(request: Request, _error: InvalidPhone):
        return _failure(request, "invalid_phone", "That phone number does not look valid.")

    @app.exception_handler(BookingNotFound)
    async def _not_found(request: Request, _error: BookingNotFound):
        return _failure(request, "not_found", "No upcoming booking matches those details.")


def _clock(value: dt.time) -> str:
    return value.strftime("%H:%M")


def _weekday(value: dt.date) -> str:
    return value.strftime("%A")


def _is_phone_bookable(service: dict, shop_status: ShopStatus) -> bool:
    """False only for a service longer than the whole opening day."""
    opens = dt.datetime.combine(dt.date.min, shop_status.open_time)
    closes = dt.datetime.combine(dt.date.min, shop_status.close_time)
    return dt.timedelta(minutes=service["duration_minutes"]) <= closes - opens


def _known_service(service_id: str) -> dict:
    service = SERVICES.get(service_id)
    if service is None:
        raise BookingRejected("unknown_service", "We do not offer that service.")
    return service


def _booking_out(entry: QueueEntry) -> dict:
    service = SERVICES.get(entry.service_id)
    return {
        "id": entry.id,
        "date": entry.queue_date.isoformat(),
        "weekday": _weekday(entry.queue_date),
        "time": _clock(entry.appointment_time),
        "service": service["name"] if service else None,
    }


def _notify_staff(name: str, phone: str, position: int, slot: str, note: str, recipients: list[str]) -> None:
    """Runs after the response is sent. A notification problem must never affect a booking."""
    try:
        send_admin_checkin_notification(name, phone, position, slot, note, recipients=recipients)
    except Exception:
        logger.warning("staff_notification=failed")


def _queue_staff_notification(
    background: BackgroundTasks, entry: QueueEntry, shop_status: ShopStatus
) -> None:
    recipients = [email for email in (shop_status.notification_emails or "").split(",") if email]
    background.add_task(
        _notify_staff,
        entry.customer.name,
        entry.customer.phone,
        entry.position,
        str(entry.appointment_time),
        entry.note,
        recipients,
    )


@tools.post("/info")
def get_business_info(db: Session = Depends(get_db)):
    shop_status = get_or_create_shop_status(db)
    current = now()
    return _ok(
        today=current.date().isoformat(),
        weekday=_weekday(current.date()),
        now_local=current.isoformat(timespec="minutes"),
        timezone=APP_TZ.key,
        open_days=[day for day in shop_status.open_days.split(",") if day],
        open_time=_clock(shop_status.open_time),
        close_time=_clock(shop_status.close_time),
        services=[
            {
                "id": service["id"],
                "name": service["name"],
                "price": service["price"],
                "currency": CURRENCY,
                "duration_minutes": service["duration_minutes"],
                "phone_bookable": _is_phone_bookable(service, shop_status),
            }
            for service in SERVICES.values()
        ],
    )


@tools.post("/availability")
def check_availability(request: AvailabilityRequest, db: Session = Depends(get_db)):
    shop_status = get_or_create_shop_status(db)
    service = _known_service(request.service_id)

    slots = slot_availability(db, request.date, duration_minutes=service["duration_minutes"])
    closed_today = request.date == now().date() and not shop_status.booking_open
    if not slots or closed_today:
        raise BookingRejected("shop_closed", "The shop is not taking bookings on that day.")

    matching = [
        slot.time
        for slot in slots
        if slot.available
        and (request.from_time is None or slot.time >= request.from_time)
        and (request.to_time is None or slot.time <= request.to_time)
    ]
    return _ok(
        date=request.date.isoformat(),
        weekday=_weekday(request.date),
        slots=[_clock(slot) for slot in matching[:MAX_OFFERED_SLOTS]],
        more=len(matching) > MAX_OFFERED_SLOTS,
    )


@tools.post("/bookings")
def create_booking(
    request: CreateBookingRequest,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
):
    shop_status = get_or_create_shop_status(db)
    service = _known_service(request.service_id)
    phone = normalize_phone(request.phone)

    result = book_slot_idempotent(
        db,
        name=request.name,
        phone=phone,
        appointment_date=request.date,
        appointment_time=request.time,
        service_id=service["id"],
        duration_minutes=service["duration_minutes"],
        note=staff_note(service),
    )

    if result.created:
        _queue_staff_notification(background, result.entry, shop_status)
    return _ok(created=result.created, booking=_booking_out(result.entry))


@tools.post("/bookings/find")
def find_booking(request: FindBookingRequest, db: Session = Depends(get_db)):
    entries = upcoming_bookings(db, normalize_phone(request.phone), on_date=request.date)
    return _ok(bookings=[_booking_out(entry) for entry in entries])


@tools.post("/bookings/cancel")
def cancel(request: CancelBookingRequest, db: Session = Depends(get_db)):
    newly_cancelled = cancel_booking(db, normalize_phone(request.phone), request.date, request.time)
    return _ok(cancelled=True, already_cancelled=not newly_cancelled)


@tools.post("/waitlist")
def join(request: JoinWaitlistRequest, db: Session = Depends(get_db)):
    service = _known_service(request.service_id)
    result = join_waitlist(
        db,
        name=request.name,
        phone=normalize_phone(request.phone),
        service_id=service["id"],
        duration_minutes=service["duration_minutes"],
        requested_date=request.date,
        window_start=request.from_time,
        window_end=request.to_time,
        consent=request.consent,
    )
    entry = result.entry
    return _ok(
        created=result.created,
        entry={
            "id": entry.id,
            "date": entry.requested_date.isoformat(),
            "from": _clock(entry.window_start),
            "to": _clock(entry.window_end),
        },
    )


@tools.post("/waitlist/accept")
def accept(
    request: AcceptOfferRequest,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
):
    shop_status = get_or_create_shop_status(db)
    result = accept_offer(db, normalize_phone(request.phone), request.date, request.time)
    if result.created:
        _queue_staff_notification(background, result.booking, shop_status)
    return _ok(booking=_booking_out(result.booking))


MAX_CAPTURE_BYTES = 1024 * 1024


# ==========================================================================================
# REHEARSAL ONLY. DO NOT LEAVE THIS ON.
#
# This exists to learn what Suisse Line really sends. It writes the raw request body to
# disk, and that body is expected to hold call transcripts and phone numbers. It is off
# unless SUISSELINE_CAPTURE_FILE is set. Unset it after the rehearsal and delete the file.
# Header values are never recorded, only their names.
# ==========================================================================================
@token_router.post("/capture")
async def capture(request: Request):
    target = os.getenv("SUISSELINE_CAPTURE_FILE")
    if not target:
        raise HTTPException(status_code=404, detail="Not Found")

    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_CAPTURE_BYTES:
            raise HTTPException(status_code=413, detail="Body too large")

    record = {"content_type": request.headers.get("content-type"), "header_names": list(request.headers.keys())}
    try:
        record["body"] = bytes(body).decode("utf-8")
    except UnicodeDecodeError:
        record["body_base64"] = base64.b64encode(bytes(body)).decode("ascii")

    descriptor = os.open(target, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    with os.fdopen(descriptor, "a", encoding="utf-8") as capture_file:
        os.fchmod(capture_file.fileno(), 0o600)  # In case the file already existed.
        capture_file.write(json.dumps(record) + "\n")
    return _ok(message="captured")


# Last, because including a router copies the routes it holds at that moment.
router.include_router(tools)
token_router.include_router(tools)
