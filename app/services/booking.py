"""The one place that decides which slots can be booked, and books them."""
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import BlockedSlot, Customer, IncludedSlot, QueueEntry, QueueStatus, ShopStatus
from app.phone import InvalidPhone, normalize_phone  # noqa: F401  (imported from here by callers)
from app.schemas import AvailableSlotOut
from app.timezone import interval_is_unambiguous, now

WEEKDAY_CODES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

SHOP_STATUS_ID = 1

# Every QueueStatus must appear in exactly one of these tuples, and a test enforces
# it, so a new status cannot silently stop holding its slot.
#
# Only SLOT_OCCUPYING_STATUSES is used in SQL. Queries must never name `cancelled`,
# because a Postgres enum created before that status existed rejects the value until
# `ALTER TYPE ... ADD VALUE` has been run.
SLOT_OCCUPYING_STATUSES = (
    QueueStatus.waiting,
    QueueStatus.next,
    QueueStatus.in_progress,
    QueueStatus.done,
)
SLOT_RELEASING_STATUSES = (QueueStatus.no_show, QueueStatus.cancelled)

# Appointments in these statuses have not started, so they can still be cancelled.
NOT_STARTED_STATUSES = (QueueStatus.waiting, QueueStatus.next)

PAST_DATE_MESSAGE = "Cannot book a date in the past"

# How far ahead anyone can book, look up times or join the waitlist: the website, the
# phone assistant and the webhook alike. Day 90 from today is the last bookable day.
BOOKING_HORIZON_DAYS = 90
TOO_FAR_MESSAGE = f"We only take bookings up to {BOOKING_HORIZON_DAYS} days ahead"
INVALID_SCHEDULE_MESSAGE = "Booking is not available at the moment"

# A full day of the shortest slot staff can set (24 hours of 5 minutes). The slot
# generator never produces more, whatever is stored.
MAX_SLOTS_PER_DAY = 288

logger = logging.getLogger("phone_assistant.booking")
_warned_schedule_problems: set[str] = set()


class BookingRejected(Exception):
    """The request cannot be carried out. The message is safe to show to the customer."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class BookingNotFound(Exception):
    pass


def date_rejection(day: date) -> BookingRejected | None:
    """Why nothing can be booked on this date at all, or None if the date is in range."""
    current = now().date()
    if day < current:
        return BookingRejected("date_past", PAST_DATE_MESSAGE)
    if (day - current).days > BOOKING_HORIZON_DAYS:
        return BookingRejected("date_too_far", TOO_FAR_MESSAGE)
    return None


def get_or_create_shop_status(db: Session) -> ShopStatus:
    """Returns the single settings row, creating it on first use.

    The fixed primary key means concurrent first requests cannot create a second row.
    """
    status = db.query(ShopStatus).order_by(ShopStatus.id).first()
    if status is not None:
        return status

    db.add(ShopStatus(id=SHOP_STATUS_ID, booking_open=True))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()  # another request created it first
    return db.query(ShopStatus).order_by(ShopStatus.id).first()


@dataclass(frozen=True)
class _DaySchedule:
    queue_date: date
    shop_status: ShopStatus
    base_times: set[time]
    included_times: set[time]
    blocked_times: set[time]
    occupied: list[tuple[datetime, datetime]]

    @property
    def slot_times(self) -> list[time]:
        return sorted(self.base_times | self.included_times)


def _interval(day: date, start: time, minutes: int) -> tuple[datetime, datetime]:
    """The half-open local interval [start, start + minutes)."""
    begins = datetime.combine(day, start)
    return begins, begins + timedelta(minutes=minutes)


def _peak_occupancy(occupied: list[tuple[datetime, datetime]], start: datetime, end: datetime) -> int:
    """The most appointments running at any single moment inside [start, end)."""
    overlapping = [(begins, ends) for begins, ends in occupied if begins < end and ends > start]
    # Occupancy only rises when an appointment begins, so these moments cover every peak.
    moments = [start] + [begins for begins, _ in overlapping if begins > start]
    return max(sum(begins <= moment < ends for begins, ends in overlapping) for moment in moments)


def _minute_of_day(value: time) -> int:
    return value.hour * 60 + value.minute


def _schedule_problem(shop_status: ShopStatus) -> str | None:
    """Why the stored settings cannot produce a day of slots, or None if they can.

    The settings form refuses these values, but an old database or a manual edit can
    still hold them. Nothing here may loop, so every check is plain arithmetic.
    """
    minutes = shop_status.slot_duration_minutes
    capacity = shop_status.capacity_per_slot
    opens, closes = shop_status.open_time, shop_status.close_time

    if not isinstance(minutes, int) or minutes <= 0:
        return "slot length is not a positive number of minutes"
    if not isinstance(capacity, int) or capacity <= 0:
        return "capacity is not a positive number"
    if opens is None or closes is None or opens >= closes:
        return "opening time is not before closing time"
    slot_count = -(-(_minute_of_day(closes) - _minute_of_day(opens)) // minutes)
    if slot_count > MAX_SLOTS_PER_DAY:
        return f"the day would hold more than {MAX_SLOTS_PER_DAY} slots"
    return None


def _schedule_is_usable(shop_status: ShopStatus) -> bool:
    """False if the stored settings are invalid. Warns once per problem, never per request."""
    problem = _schedule_problem(shop_status)
    if problem is None:
        return True
    if problem not in _warned_schedule_problems:
        _warned_schedule_problems.add(problem)
        logger.warning(
            "invalid schedule settings: %s. Every day is treated as closed until staff "
            "save valid opening hours, slot length and capacity.",
            problem,
        )
    return False


def _base_slots(shop_status: ShopStatus, queue_date: date) -> set[time]:
    if not _schedule_is_usable(shop_status):
        return set()
    open_days = set(shop_status.open_days.split(",")) if shop_status.open_days else set()
    if WEEKDAY_CODES[queue_date.weekday()] not in open_days:
        return set()

    opens = datetime.combine(queue_date, shop_status.open_time)
    span = _minute_of_day(shop_status.close_time) - _minute_of_day(shop_status.open_time)
    minutes = shop_status.slot_duration_minutes
    # A counted loop: the number of steps is known and capped before it starts.
    slot_count = min(-(-span // minutes), MAX_SLOTS_PER_DAY)
    return {(opens + timedelta(minutes=index * minutes)).time() for index in range(slot_count)}


def _load_day(db: Session, shop_status: ShopStatus, queue_date: date) -> _DaySchedule:
    included = db.query(IncludedSlot.included_time).filter(IncludedSlot.included_date == queue_date)
    blocked = db.query(BlockedSlot.blocked_time).filter(BlockedSlot.blocked_date == queue_date)
    booked = (
        db.query(QueueEntry.appointment_time, QueueEntry.duration_minutes)
        .filter(QueueEntry.queue_date == queue_date)
        .filter(QueueEntry.status.in_(SLOT_OCCUPYING_STATUSES))
    )
    slot_minutes = shop_status.slot_duration_minutes
    return _DaySchedule(
        queue_date=queue_date,
        shop_status=shop_status,
        base_times=_base_slots(shop_status, queue_date),
        included_times={row[0] for row in included.all()},
        blocked_times={row[0] for row in blocked.all()},
        occupied=[
            _interval(queue_date, start, duration or slot_minutes) for start, duration in booked.all()
        ],
    )


def _overlaps_blocked_slot(day: _DaySchedule, start: datetime, end: datetime) -> bool:
    """A blocked slot occupies one slot length, so no appointment may run across it."""
    slot_minutes = day.shop_status.slot_duration_minutes
    blocked = (_interval(day.queue_date, slot, slot_minutes) for slot in day.blocked_times)
    return any(begins < end and ends > start for begins, ends in blocked)


def _is_at_capacity(day: _DaySchedule, start: datetime, end: datetime) -> bool:
    return _peak_occupancy(day.occupied, start, end) >= day.shop_status.capacity_per_slot


def _rejection(day: _DaySchedule, slot: time, minutes: int) -> BookingRejected | None:
    """Why an appointment of this length cannot start at this slot now, or None if it can.

    Starts stay on the slot grid. A start on an admin-included slot may run outside
    opening hours, because staff opened it deliberately, but it may not cross midnight.
    """
    current = now()
    is_today = day.queue_date == current.date()

    out_of_range = date_rejection(day.queue_date)
    if out_of_range is not None:
        return out_of_range
    if not _schedule_is_usable(day.shop_status):
        return BookingRejected("booking_closed", INVALID_SCHEDULE_MESSAGE)
    if is_today and not day.shop_status.booking_open:
        return BookingRejected("booking_closed", "Booking is currently closed for today")
    if slot not in day.base_times and slot not in day.included_times:
        return BookingRejected("invalid_time", "Invalid appointment time")
    if is_today and slot <= current.time():
        return BookingRejected("invalid_time", "That time has already passed")
    start, end = _interval(day.queue_date, slot, minutes)
    if not interval_is_unambiguous(start, minutes):
        return BookingRejected("invalid_time", "That time is not available")
    if end.date() > day.queue_date and end.time() != time.min:
        return BookingRejected("invalid_time", "That appointment would run past midnight")
    closing = datetime.combine(day.queue_date, day.shop_status.close_time)
    if slot in day.base_times and end > closing:
        return BookingRejected("invalid_time", "That appointment would run past closing time")
    if _overlaps_blocked_slot(day, start, end):
        return BookingRejected("slot_taken", "That time is not available")
    if _is_at_capacity(day, start, end):
        return BookingRejected("slot_taken", "That time is fully booked")
    return None


def slot_availability(
    db: Session, queue_date: date, duration_minutes: int | None = None
) -> list[AvailableSlotOut]:
    """Availability for an appointment of this length, by default one slot length."""
    out_of_range = date_rejection(queue_date)
    if out_of_range is not None:
        raise out_of_range

    shop_status = get_or_create_shop_status(db)
    if not _schedule_is_usable(shop_status):
        return []  # closed, including any times staff added for this day
    minutes = duration_minutes or shop_status.slot_duration_minutes
    day = _load_day(db, shop_status, queue_date)
    return [
        AvailableSlotOut(
            time=slot,
            capacity=shop_status.capacity_per_slot,
            booked=_peak_occupancy(day.occupied, *_interval(queue_date, slot, minutes)),
            blocked=slot in day.blocked_times,
            included=slot in day.included_times and slot not in day.base_times,
            available=_rejection(day, slot, minutes) is None,
        )
        for slot in day.slot_times
    ]


def day_slot_times(db: Session, queue_date: date) -> list[time]:
    """Every start time that exists on this day: the opening-hours grid plus added times."""
    return _load_day(db, get_or_create_shop_status(db), queue_date).slot_times


def acquire_booking_lock(db: Session) -> None:
    """Serialises every booking write until this transaction ends.

    The slot check and the insert must happen as one step, or concurrent requests
    all see a free slot. Updating the single shop_status row takes a row lock on
    Postgres and the database write lock on SQLite. It must be the transaction's
    first write, and slots must be read after it. Repeating it within one
    transaction is harmless.
    """
    db.query(ShopStatus).update({ShopStatus.updated_at: now()})


def _lock_and_load_day(db: Session, shop_status: ShopStatus, queue_date: date) -> _DaySchedule:
    """Takes the booking lock, then reads the day. Never commits."""
    acquire_booking_lock(db)
    return _load_day(db, shop_status, queue_date)


def ensure_bookable(
    db: Session, shop_status: ShopStatus, queue_date: date, slot: time, minutes: int
) -> None:
    """Raises BookingRejected unless this appointment could be booked right now.

    Holds the booking lock and never commits, so the caller decides when the
    transaction ends and the answer stays true until then.
    """
    day = _lock_and_load_day(db, shop_status, queue_date)
    rejection = _rejection(day, slot, minutes)
    if rejection is not None:
        raise rejection


@dataclass(frozen=True)
class BookingResult:
    entry: QueueEntry
    created: bool


def find_customer(db: Session, phone: str, phone_as_typed: str | None = None) -> Customer | None:
    """The customer with this normalised number, or else with exactly the typed string.

    Customers saved before numbers were normalised keep their stored spelling, so
    they are found only by typing it the same way again.
    """
    customer = db.query(Customer).filter(Customer.phone == phone).first()
    if customer is None and phone_as_typed and phone_as_typed != phone:
        customer = db.query(Customer).filter(Customer.phone == phone_as_typed).first()
    return customer


def _bookings_at(db: Session, phone: str, appointment_date: date, appointment_time: time):
    return (
        db.query(QueueEntry)
        .join(Customer)
        .filter(Customer.phone == phone)
        .filter(QueueEntry.queue_date == appointment_date)
        .filter(QueueEntry.appointment_time == appointment_time)
    )


def _is_upcoming(entry: QueueEntry, current: datetime) -> bool:
    return (entry.queue_date, entry.appointment_time) > (current.date(), current.time())


def book_slot(
    db: Session,
    *,
    name: str,
    phone: str,
    appointment_date: date,
    appointment_time: time,
    note: str | None = None,
    phone_as_typed: str | None = None,
) -> QueueEntry:
    """Books a slot for the public website, or raises BookingRejected.

    `phone` is the normalised number. `phone_as_typed` also finds a customer who was
    stored before numbers were normalised.
    """
    return _book(
        db,
        name=name,
        phone=phone,
        phone_as_typed=phone_as_typed,
        appointment_date=appointment_date,
        appointment_time=appointment_time,
        note=note,
        reuse_active_booking=False,
        rename_existing_customer=True,
    ).entry


def book_slot_idempotent(
    db: Session,
    *,
    name: str,
    phone: str,
    appointment_date: date,
    appointment_time: time,
    service_id: str,
    duration_minutes: int,
    note: str | None = None,
) -> BookingResult:
    """Books an appointment so that a retry cannot create a second one.

    The same phone, date and time returns the existing active booking. A caller's
    spoken name never overwrites the name already stored for a phone number.
    """
    return _book(
        db,
        name=name,
        phone=phone,
        appointment_date=appointment_date,
        appointment_time=appointment_time,
        note=note,
        reuse_active_booking=True,
        rename_existing_customer=False,
        service_id=service_id,
        duration_minutes=duration_minutes,
    )


def _book(db: Session, **details) -> BookingResult:
    shop_status = get_or_create_shop_status(db)

    try:
        result = book_in_lock(db, shop_status, **details)
        db.commit()
    except Exception:
        db.rollback()
        raise

    db.refresh(result.entry)
    return result


def book_in_lock(
    db: Session,
    shop_status: ShopStatus,
    *,
    name: str,
    phone: str,
    appointment_date: date,
    appointment_time: time,
    note: str | None,
    reuse_active_booking: bool,
    rename_existing_customer: bool,
    service_id: str | None = None,
    duration_minutes: int | None = None,
    phone_as_typed: str | None = None,
) -> BookingResult:
    """The only code that creates appointments. Holds the lock and never commits."""
    acquire_booking_lock(db)

    if reuse_active_booking:
        existing = (
            _bookings_at(db, phone, appointment_date, appointment_time)
            .filter(QueueEntry.status.in_(SLOT_OCCUPYING_STATUSES))
            .first()
        )
        if existing is not None:
            return BookingResult(entry=existing, created=False)

    minutes = duration_minutes or shop_status.slot_duration_minutes
    ensure_bookable(db, shop_status, appointment_date, appointment_time, minutes)

    customer = find_customer(db, phone, phone_as_typed)
    if customer is None:
        customer = Customer(name=name, phone=phone)
        db.add(customer)
    elif rename_existing_customer:
        customer.name = name
    db.flush()

    max_position = (
        db.query(func.max(QueueEntry.position))
        .filter(QueueEntry.queue_date == appointment_date)
        .scalar()
    )

    entry = QueueEntry(
        customer_id=customer.id,
        queue_date=appointment_date,
        position=(max_position or 0) + 1,
        status=QueueStatus.waiting,
        note=note,
        appointment_time=appointment_time,
        service_id=service_id,
        duration_minutes=duration_minutes,
    )
    db.add(entry)
    db.flush()
    return BookingResult(entry=entry, created=True)


def revive_no_show(db: Session, entry: QueueEntry, new_status: QueueStatus) -> QueueEntry:
    """Moves a No Show back to an occupying status, or raises BookingRejected.

    A No Show gives up its slot, so someone else may hold it by now. Only the overlap
    and capacity rule applies: a correction is normally made after the appointment
    time, which the rules for new bookings would refuse. The entry is not counted
    against itself, because a No Show is not an occupying status.
    """
    shop_status = get_or_create_shop_status(db)

    try:
        day = _lock_and_load_day(db, shop_status, entry.queue_date)
        db.refresh(entry)

        if entry.status == QueueStatus.no_show:
            minutes = entry.duration_minutes or shop_status.slot_duration_minutes
            start, end = _interval(entry.queue_date, entry.appointment_time, minutes)
            if _is_at_capacity(day, start, end):
                raise BookingRejected("slot_taken", "That time has since been booked by someone else")

        entry.status = new_status
        db.commit()
    except Exception:
        db.rollback()
        raise

    db.refresh(entry)
    return entry


def upcoming_bookings(
    db: Session, phone: str, on_date: date | None = None, limit: int = 3
) -> list[QueueEntry]:
    current = now()
    query = (
        db.query(QueueEntry)
        .join(Customer)
        .filter(Customer.phone == phone)
        .filter(QueueEntry.status.in_(NOT_STARTED_STATUSES))
        .filter(QueueEntry.queue_date >= current.date())
    )
    if on_date is not None:
        query = query.filter(QueueEntry.queue_date == on_date)

    entries = query.order_by(QueueEntry.queue_date.asc(), QueueEntry.appointment_time.asc()).all()
    return [entry for entry in entries if _is_upcoming(entry, current)][:limit]


def cancel_booking(db: Session, phone: str, appointment_date: date, appointment_time: time) -> bool:
    """Cancels an upcoming booking. Returns False if it was already cancelled.

    Statuses are compared in Python, so looking up a booking never sends `cancelled`
    to the database. Only writing the new status needs the Postgres enum value.
    """
    get_or_create_shop_status(db)
    current = now()

    try:
        acquire_booking_lock(db)

        entries = _bookings_at(db, phone, appointment_date, appointment_time).all()
        for entry in entries:
            if entry.status in NOT_STARTED_STATUSES and _is_upcoming(entry, current):
                entry.status = QueueStatus.cancelled
                db.commit()
                return True

        already_cancelled = any(entry.status == QueueStatus.cancelled for entry in entries)
        db.rollback()
    except Exception:
        db.rollback()
        raise

    if already_cancelled:
        return False
    raise BookingNotFound()
