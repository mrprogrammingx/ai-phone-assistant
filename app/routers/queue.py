from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from fastapi.exceptions import RequestValidationError
from sqlalchemy.orm import Session

from app.auth import require_admin
from app.database import get_db
from app.models import Customer, QueueEntry, QueueStatus
from app.phone import InvalidPhone, normalize_phone
from app.schemas import PHONE_MESSAGE, CheckInRequest, QueueEntryOut, QueueNoteUpdate, AvailableSlotOut, typed_phone
from app.services.booking import (
    BookingRejected,
    book_slot,
    get_or_create_shop_status,
    revive_no_show,
    slot_availability,
)
from app.services.email import send_admin_checkin_notification
from app.timezone import today as get_today

# Public: check-in, availability, and a customer's own booking lookup.
router = APIRouter(prefix="/queue", tags=["queue"])

# Staff-only: viewing/managing the live queue and history.
admin_router = APIRouter(prefix="/queue", tags=["queue"], dependencies=[Depends(require_admin)])


@router.get("/available-times", response_model=list[AvailableSlotOut])
def get_available_times(for_date: date | None = None, db: Session = Depends(get_db)):
    try:
        return slot_availability(db, for_date or get_today())
    except BookingRejected as rejected:
        raise HTTPException(status_code=400, detail=rejected.message)


def _checkin_phone(db: Session, typed: str) -> str:
    """The number to book under: E.164, or the stored spelling of an earlier customer.

    A number that cannot be normalised is refused, unless a customer saved before
    numbers were normalised has exactly that string, so they can still book.
    """
    try:
        return normalize_phone(typed)
    except InvalidPhone:
        if db.query(Customer.id).filter(Customer.phone == typed).first() is not None:
            return typed
    # The same shape as a schema error, so the form shows it beside the phone field.
    raise RequestValidationError(
        [{"type": "value_error", "loc": ("body", "phone"), "msg": f"Value error, {PHONE_MESSAGE}"}]
    )


@router.post("/checkin", response_model=QueueEntryOut)
def check_in(payload: CheckInRequest, db: Session = Depends(get_db)):
    try:
        entry = book_slot(
            db,
            name=payload.name,
            phone=_checkin_phone(db, payload.phone),
            phone_as_typed=payload.phone,
            appointment_date=payload.appointment_date,
            appointment_time=payload.appointment_time,
            note=payload.note,
        )
    except BookingRejected as rejected:
        raise HTTPException(status_code=400, detail=rejected.message)

    customer = entry.customer
    shop_status = get_or_create_shop_status(db)
    recipients = [email for email in (shop_status.notification_emails or "").split(",") if email]
    send_admin_checkin_notification(
        customer.name,
        customer.phone,
        entry.position,
        str(entry.appointment_time),
        entry.note,
        recipients=recipients,
    )

    return entry


@admin_router.get("", response_model=list[QueueEntryOut])
def list_queue(for_date: date | None = None, db: Session = Depends(get_db)):
    target_date = for_date or get_today()
    return (
        db.query(QueueEntry)
        .filter(QueueEntry.queue_date == target_date)
        .filter(QueueEntry.status.in_([QueueStatus.waiting, QueueStatus.next, QueueStatus.in_progress]))
        .order_by(QueueEntry.appointment_time.asc(), QueueEntry.position.asc())
        .all()
    )


@router.get("/my-bookings", response_model=list[QueueEntryOut])
def get_my_bookings(phone: str, for_date: date | None = None, db: Session = Depends(get_db)):
    # Looks under the normalised number and under exactly what was typed, so a
    # customer stored before numbers were normalised is still found.
    try:
        typed = typed_phone(phone)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    spellings = {typed}
    try:
        spellings.add(normalize_phone(typed))
        is_valid = True
    except InvalidPhone:
        is_valid = False

    customer_ids = [row[0] for row in db.query(Customer.id).filter(Customer.phone.in_(spellings))]
    if not customer_ids:
        if not is_valid:
            raise HTTPException(status_code=400, detail=PHONE_MESSAGE)
        return []

    query = db.query(QueueEntry).filter(QueueEntry.customer_id.in_(customer_ids))
    if for_date is not None:
        query = query.filter(QueueEntry.queue_date == for_date)

    return query.order_by(QueueEntry.queue_date.desc(), QueueEntry.appointment_time.asc()).all()


@admin_router.get("/history/dates", response_model=list[date])
def list_history_dates(db: Session = Depends(get_db)):
    rows = (
        db.query(QueueEntry.queue_date)
        .distinct()
        .order_by(QueueEntry.queue_date.desc())
        .all()
    )
    return [row[0] for row in rows]


@admin_router.get("/history/{queue_date}", response_model=list[QueueEntryOut])
def get_history_for_date(queue_date: date, db: Session = Depends(get_db)):
    return (
        db.query(QueueEntry)
        .filter(QueueEntry.queue_date == queue_date)
        .order_by(QueueEntry.appointment_time.asc(), QueueEntry.position.asc())
        .all()
    )


@admin_router.get("/{entry_id}/position", response_model=QueueEntryOut)
def get_position(entry_id: int, db: Session = Depends(get_db)):
    entry = db.query(QueueEntry).filter(QueueEntry.id == entry_id).first()
    if entry is None:
        raise HTTPException(status_code=404, detail="Queue entry not found")
    return entry


@admin_router.post("/{entry_id}/note", response_model=QueueEntryOut)
def set_note(entry_id: int, payload: QueueNoteUpdate, db: Session = Depends(get_db)):
    entry = db.query(QueueEntry).filter(QueueEntry.id == entry_id).first()
    if entry is None:
        raise HTTPException(status_code=404, detail="Queue entry not found")

    entry.note = payload.note
    db.commit()
    db.refresh(entry)
    return entry


@admin_router.post("/call-next", response_model=QueueEntryOut)
def call_next(db: Session = Depends(get_db)):
    today = get_today()

    in_progress = (
        db.query(QueueEntry)
        .filter(QueueEntry.queue_date == today, QueueEntry.status == QueueStatus.in_progress)
        .first()
    )
    if in_progress is not None:
        raise HTTPException(status_code=400, detail="A customer is already being served")

    entry = (
        db.query(QueueEntry)
        .filter(QueueEntry.queue_date == today)
        .filter(QueueEntry.status.in_([QueueStatus.waiting, QueueStatus.next]))
        .order_by(QueueEntry.appointment_time.asc(), QueueEntry.position.asc())
        .first()
    )
    if entry is None:
        raise HTTPException(status_code=404, detail="No customers waiting")

    entry.status = QueueStatus.in_progress
    db.commit()
    db.refresh(entry)

    _promote_upcoming_next(db, today)

    return entry


def _promote_upcoming_next(db: Session, today: date) -> None:
    upcoming = (
        db.query(QueueEntry)
        .filter(QueueEntry.queue_date == today, QueueEntry.status == QueueStatus.waiting)
        .order_by(QueueEntry.appointment_time.asc(), QueueEntry.position.asc())
        .first()
    )
    if upcoming is None:
        return

    upcoming.status = QueueStatus.next
    db.commit()


def _refuse_if_cancelled(entry: QueueEntry) -> None:
    """A cancelled appointment has given up its slot, which someone else may now hold."""
    if entry.status == QueueStatus.cancelled:
        raise HTTPException(status_code=409, detail="A cancelled appointment cannot be changed")


@admin_router.post("/{entry_id}/done", response_model=QueueEntryOut)
def mark_done(entry_id: int, db: Session = Depends(get_db)):
    entry = db.query(QueueEntry).filter(QueueEntry.id == entry_id).first()
    if entry is None:
        raise HTTPException(status_code=404, detail="Queue entry not found")

    _refuse_if_cancelled(entry)

    if entry.status == QueueStatus.no_show:
        try:
            return revive_no_show(db, entry, QueueStatus.done)
        except BookingRejected as rejected:
            raise HTTPException(status_code=409, detail=rejected.message)

    entry.status = QueueStatus.done
    db.commit()
    db.refresh(entry)
    return entry


@admin_router.post("/{entry_id}/no-show", response_model=QueueEntryOut)
def mark_no_show(entry_id: int, db: Session = Depends(get_db)):
    entry = db.query(QueueEntry).filter(QueueEntry.id == entry_id).first()
    if entry is None:
        raise HTTPException(status_code=404, detail="Queue entry not found")

    _refuse_if_cancelled(entry)
    entry.status = QueueStatus.no_show
    db.commit()
    db.refresh(entry)

    today = get_today()
    in_progress = (
        db.query(QueueEntry)
        .filter(QueueEntry.queue_date == today, QueueEntry.status == QueueStatus.in_progress)
        .first()
    )
    if in_progress is None:
        _promote_upcoming_next(db, today)

    return entry
