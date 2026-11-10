from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import require_admin
from app.database import get_db
from app.models import QueueEntry, QueueStatus, BlockedSlot, IncludedSlot
from app.services.booking import BOOKING_HORIZON_DAYS, day_slot_times
from app.services.booking import get_or_create_shop_status as _get_or_create_shop_status
from app.schemas import (
    ShopStatusOut,
    ShopStatusUpdate,
    OpenDaysUpdate,
    ScheduleSettingsUpdate,
    BlockedSlotRequest,
    BlockedSlotOut,
    IncludedSlotRequest,
    IncludedSlotOut,
    NotificationEmailsUpdate,
)
from app.timezone import today

# Times staff may add to one day beyond opening hours.
MAX_INCLUDED_SLOTS_PER_DAY = 48


def _require_changeable_date(day: date) -> None:
    """Applies only to new rows. Rows already stored can always be listed and removed."""
    current = today()
    if day < current:
        raise HTTPException(status_code=400, detail="That date is in the past")
    # The same horizon as customer bookings. The staff page cannot show later days,
    # and nothing can be booked on them, so there is nothing to block or add there.
    if (day - current).days > BOOKING_HORIZON_DAYS:
        raise HTTPException(
            status_code=400,
            detail=f"That date is more than {BOOKING_HORIZON_DAYS} days ahead",
        )


# Public: read-only shop status is needed on every page (banner) and by the
# public check-in page, so it is not behind admin auth.
public_router = APIRouter(prefix="/admin", tags=["admin"])

# Everything else under /admin is staff-only configuration.
router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])


@public_router.get("/shop-status", response_model=ShopStatusOut)
def get_shop_status(db: Session = Depends(get_db)):
    return _get_or_create_shop_status(db)


@router.post("/shop-status", response_model=ShopStatusOut)
def set_shop_status(payload: ShopStatusUpdate, db: Session = Depends(get_db)):
    status = _get_or_create_shop_status(db)
    status.booking_open = payload.booking_open
    db.commit()
    db.refresh(status)
    return status


@router.post("/open-days", response_model=ShopStatusOut)
def set_open_days(payload: OpenDaysUpdate, db: Session = Depends(get_db)):
    status = _get_or_create_shop_status(db)
    status.open_days = ",".join(payload.open_days)
    db.commit()
    db.refresh(status)
    return status


@router.post("/schedule-settings", response_model=ShopStatusOut)
def set_schedule_settings(payload: ScheduleSettingsUpdate, db: Session = Depends(get_db)):
    status = _get_or_create_shop_status(db)
    status.open_time = payload.open_time
    status.close_time = payload.close_time
    status.slot_duration_minutes = payload.slot_duration_minutes
    status.capacity_per_slot = payload.capacity_per_slot
    db.commit()
    db.refresh(status)
    return status


@router.post("/notification-emails", response_model=ShopStatusOut)
def set_notification_emails(payload: NotificationEmailsUpdate, db: Session = Depends(get_db)):
    status = _get_or_create_shop_status(db)
    status.notification_emails = ",".join(payload.notification_emails)
    db.commit()
    db.refresh(status)
    return status


@router.get("/blocked-slots", response_model=list[BlockedSlotOut])
def list_blocked_slots(for_date: date, db: Session = Depends(get_db)):
    return (
        db.query(BlockedSlot)
        .filter(BlockedSlot.blocked_date == for_date)
        .order_by(BlockedSlot.blocked_time.asc())
        .all()
    )


@router.post("/blocked-slots", response_model=BlockedSlotOut)
def block_slot(payload: BlockedSlotRequest, db: Session = Depends(get_db)):
    existing = (
        db.query(BlockedSlot)
        .filter(BlockedSlot.blocked_date == payload.date, BlockedSlot.blocked_time == payload.time)
        .first()
    )
    if existing is not None:
        return existing

    _require_changeable_date(payload.date)
    if payload.time not in day_slot_times(db, payload.date):
        raise HTTPException(
            status_code=400, detail="That time is not one of that day's appointment times"
        )

    blocked = BlockedSlot(blocked_date=payload.date, blocked_time=payload.time)
    db.add(blocked)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Slot already blocked")
    db.refresh(blocked)
    return blocked


@router.delete("/blocked-slots")
def unblock_slot(payload: BlockedSlotRequest, db: Session = Depends(get_db)):
    blocked = (
        db.query(BlockedSlot)
        .filter(BlockedSlot.blocked_date == payload.date, BlockedSlot.blocked_time == payload.time)
        .first()
    )
    if blocked is None:
        raise HTTPException(status_code=404, detail="Slot is not blocked")

    db.delete(blocked)
    db.commit()
    return {"unblocked": True}


@router.get("/included-slots", response_model=list[IncludedSlotOut])
def list_included_slots(for_date: date, db: Session = Depends(get_db)):
    return (
        db.query(IncludedSlot)
        .filter(IncludedSlot.included_date == for_date)
        .order_by(IncludedSlot.included_time.asc())
        .all()
    )


@router.post("/included-slots", response_model=IncludedSlotOut)
def include_slot(payload: IncludedSlotRequest, db: Session = Depends(get_db)):
    existing = (
        db.query(IncludedSlot)
        .filter(IncludedSlot.included_date == payload.date, IncludedSlot.included_time == payload.time)
        .first()
    )
    if existing is not None:
        return existing

    _require_changeable_date(payload.date)
    already_added = db.query(IncludedSlot).filter(IncludedSlot.included_date == payload.date).count()
    if already_added >= MAX_INCLUDED_SLOTS_PER_DAY:
        raise HTTPException(
            status_code=400,
            detail=f"No more than {MAX_INCLUDED_SLOTS_PER_DAY} extra times can be added to one day",
        )

    included = IncludedSlot(included_date=payload.date, included_time=payload.time)
    db.add(included)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Slot already included")
    db.refresh(included)
    return included


@router.delete("/included-slots")
def exclude_slot(payload: IncludedSlotRequest, db: Session = Depends(get_db)):
    included = (
        db.query(IncludedSlot)
        .filter(IncludedSlot.included_date == payload.date, IncludedSlot.included_time == payload.time)
        .first()
    )
    if included is None:
        raise HTTPException(status_code=404, detail="Slot is not included")

    db.delete(included)
    db.commit()
    return {"excluded": True}


@router.post("/reset-queue")
def reset_queue(db: Session = Depends(get_db)):
    """Marks all of today's active queue entries as done, effectively resetting the queue."""
    entries = (
        db.query(QueueEntry)
        .filter(QueueEntry.queue_date == today())
        .filter(QueueEntry.status.in_([QueueStatus.waiting, QueueStatus.next, QueueStatus.in_progress]))
        .all()
    )
    for entry in entries:
        entry.status = QueueStatus.done
    db.commit()
    return {"reset_count": len(entries)}
