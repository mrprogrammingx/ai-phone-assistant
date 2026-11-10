"""The Smart Cancellation Waitlist.

An offer never reserves a slot. Every write here runs under the booking lock, so the
first confirmed booking wins whichever path it came through.
"""
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from sqlalchemy.orm import Session

from app.models import QueueEntry, WaitlistEntry, WaitlistEntryStatus, WaitlistOffer, WaitlistOfferStatus
from app.services import booking
from app.services.booking import BookingRejected
from app.services.catalog import SERVICES, staff_note
from app.timezone import local_to_utc, today, utc_now, utc_to_local

OFFER_LIFETIME = timedelta(minutes=30)
MAX_OPEN_OFFERS_PER_TIME = 3
RETENTION = timedelta(days=30)


@dataclass(frozen=True)
class JoinResult:
    entry: WaitlistEntry
    created: bool


@dataclass(frozen=True)
class OfferedContact:
    """Who staff should contact by hand. Printed by the release command, never logged."""

    name: str
    phone: str
    service_id: str
    expires_local: datetime


@dataclass(frozen=True)
class ReleaseResult:
    offers: list[OfferedContact]
    refused_reason: str | None = None


def join_waitlist(
    db: Session,
    *,
    name: str,
    phone: str,
    service_id: str,
    duration_minutes: int,
    requested_date: date,
    window_start: time,
    window_end: time,
    consent: bool | None,
) -> JoinResult:
    """Adds a caller to the waitlist. An identical active request returns the existing entry."""
    if consent is not True:
        raise BookingRejected("consent_required", "We need permission to contact the caller first.")
    out_of_range = booking.date_rejection(requested_date)
    if out_of_range is not None:
        raise out_of_range

    booking.get_or_create_shop_status(db)
    try:
        booking.acquire_booking_lock(db)
        _apply_retention(db)

        existing = (
            db.query(WaitlistEntry)
            .filter(WaitlistEntry.phone == phone)
            .filter(WaitlistEntry.service_id == service_id)
            .filter(WaitlistEntry.requested_date == requested_date)
            .filter(WaitlistEntry.window_start == window_start)
            .filter(WaitlistEntry.window_end == window_end)
            .filter(WaitlistEntry.status == WaitlistEntryStatus.active)
            .first()
        )
        if existing is not None:
            db.commit()
            return JoinResult(entry=existing, created=False)

        entry = WaitlistEntry(
            name=name,
            phone=phone,
            service_id=service_id,
            duration_minutes=duration_minutes,
            requested_date=requested_date,
            window_start=window_start,
            window_end=window_end,
            consent_at=utc_now(),
        )
        db.add(entry)
        db.commit()
    except Exception:
        db.rollback()
        raise

    return JoinResult(entry=entry, created=True)


def _open_offers(db: Session):
    return db.query(WaitlistOffer).filter(WaitlistOffer.status == WaitlistOfferStatus.open)


def release_slot(db: Session, slot_date: date, slot_time: time) -> ReleaseResult:
    """Offers a freed time to matching entries, oldest first. Nothing is sent or reserved."""
    shop_status = booking.get_or_create_shop_status(db)

    try:
        booking.acquire_booking_lock(db)
        _apply_retention(db)

        try:
            booking.ensure_bookable(
                db, shop_status, slot_date, slot_time, shop_status.slot_duration_minutes
            )
        except BookingRejected as rejected:
            db.commit()
            return ReleaseResult(offers=[], refused_reason=rejected.message)

        already_open = (
            _open_offers(db)
            .filter(WaitlistOffer.slot_date == slot_date, WaitlistOffer.slot_time == slot_time)
            .count()
        )
        candidates = (
            db.query(WaitlistEntry)
            .filter(WaitlistEntry.status == WaitlistEntryStatus.active)
            .filter(WaitlistEntry.requested_date == slot_date)
            .filter(WaitlistEntry.window_start <= slot_time, WaitlistEntry.window_end >= slot_time)
            .order_by(WaitlistEntry.created_at, WaitlistEntry.id)
            .all()
        )

        expires_at = min(utc_now() + OFFER_LIFETIME, local_to_utc(slot_date, slot_time))
        offered = []
        for entry in candidates:
            if already_open + len(offered) >= MAX_OPEN_OFFERS_PER_TIME:
                break
            if _open_offers(db).filter(WaitlistOffer.entry_id == entry.id).count():
                continue
            try:
                booking.ensure_bookable(db, shop_status, slot_date, slot_time, entry.duration_minutes)
            except BookingRejected:
                continue

            db.add(
                WaitlistOffer(
                    entry_id=entry.id, slot_date=slot_date, slot_time=slot_time, expires_at=expires_at
                )
            )
            db.flush()
            offered.append(
                OfferedContact(
                    name=entry.name,
                    phone=entry.phone,
                    service_id=entry.service_id,
                    expires_local=utc_to_local(expires_at),
                )
            )

        db.commit()
    except Exception:
        db.rollback()
        raise

    return ReleaseResult(offers=offered)


@dataclass(frozen=True)
class AcceptResult:
    booking: QueueEntry
    created: bool


def _close_offer(offer: WaitlistOffer, status: WaitlistOfferStatus) -> None:
    offer.status = status
    offer.closed_at = utc_now()


def _expire_stale_offers(db: Session) -> None:
    for offer in _open_offers(db).filter(WaitlistOffer.expires_at <= utc_now()).all():
        _close_offer(offer, WaitlistOfferStatus.expired)
    db.flush()


def _apply_retention(db: Session) -> None:
    """Closes what has run out and deletes what is past retention. Runs under the lock.

    Done on every waitlist write, so no scheduler is needed.
    """
    _expire_stale_offers(db)

    current = utc_now()
    past_their_date = (
        db.query(WaitlistEntry)
        .filter(WaitlistEntry.status == WaitlistEntryStatus.active)
        .filter(WaitlistEntry.requested_date < today())
        .all()
    )
    for entry in past_their_date:
        entry.status = WaitlistEntryStatus.expired
        entry.closed_at = current
        for offer in entry.offers:
            if offer.status == WaitlistOfferStatus.open:
                _close_offer(offer, WaitlistOfferStatus.expired)
    db.flush()

    purge_ids = [
        row[0]
        for row in db.query(WaitlistEntry.id)
        .filter(WaitlistEntry.status != WaitlistEntryStatus.active)
        .filter(WaitlistEntry.closed_at <= current - RETENTION)
        .all()
    ]
    if purge_ids:
        db.query(WaitlistOffer).filter(WaitlistOffer.entry_id.in_(purge_ids)).delete(synchronize_session=False)
        db.query(WaitlistEntry).filter(WaitlistEntry.id.in_(purge_ids)).delete(synchronize_session=False)


def _offers_for(db: Session, phone: str, slot_date: date, slot_time: time) -> list[WaitlistOffer]:
    return (
        db.query(WaitlistOffer)
        .join(WaitlistEntry)
        .filter(WaitlistEntry.phone == phone)
        .filter(WaitlistOffer.slot_date == slot_date, WaitlistOffer.slot_time == slot_time)
        .order_by(WaitlistOffer.id.desc())
        .all()
    )


def _take_offer(db: Session, shop_status, offer: WaitlistOffer) -> AcceptResult:
    entry = offer.entry
    result = booking.book_in_lock(
        db,
        shop_status,
        name=entry.name,
        phone=entry.phone,
        appointment_date=offer.slot_date,
        appointment_time=offer.slot_time,
        note=staff_note(SERVICES[entry.service_id]),
        reuse_active_booking=True,
        rename_existing_customer=False,
        service_id=entry.service_id,
        duration_minutes=entry.duration_minutes,
    )

    _close_offer(offer, WaitlistOfferStatus.accepted)
    offer.queue_entry_id = result.entry.id
    entry.status = WaitlistEntryStatus.booked
    entry.closed_at = utc_now()
    db.flush()  # the session does not autoflush, and the query below must not see this offer

    others = _open_offers(db).filter(
        WaitlistOffer.slot_date == offer.slot_date, WaitlistOffer.slot_time == offer.slot_time
    )
    for other in others.all():
        _close_offer(other, WaitlistOfferStatus.lost)

    return AcceptResult(booking=result.entry, created=result.created)


def _refusal_for_booking(rejected: BookingRejected) -> BookingRejected:
    if rejected.code == "date_past":
        return BookingRejected("offer_expired", "That offer has expired.")
    return BookingRejected("slot_taken", "Someone else has taken that time.")


def _without_an_open_offer(offers: list[WaitlistOffer]) -> AcceptResult | BookingRejected:
    """What a caller gets when no offer is open: a retry result, or the reason."""
    for offer in offers:
        accepted = offer.status == WaitlistOfferStatus.accepted and offer.booking is not None
        if accepted and offer.booking.status in booking.SLOT_OCCUPYING_STATUSES:
            return AcceptResult(booking=offer.booking, created=False)

    newest = offers[0].status if offers else None
    if newest == WaitlistOfferStatus.lost:
        return BookingRejected("slot_taken", "Someone else has taken that time.")
    if newest == WaitlistOfferStatus.expired:
        return BookingRejected("offer_expired", "That offer has expired.")
    return BookingRejected("no_open_offer", "There is no open offer for that time.")


def accept_offer(db: Session, phone: str, slot_date: date, slot_time: time) -> AcceptResult:
    """Books an open offer under the lock. Only a success means the time is booked."""
    shop_status = booking.get_or_create_shop_status(db)

    try:
        booking.acquire_booking_lock(db)
        _apply_retention(db)

        offers = _offers_for(db, phone, slot_date, slot_time)
        open_offer = next((offer for offer in offers if offer.status == WaitlistOfferStatus.open), None)

        if open_offer is None:
            outcome = _without_an_open_offer(offers)
        else:
            try:
                outcome = _take_offer(db, shop_status, open_offer)
            except BookingRejected as rejected:
                _close_offer(open_offer, WaitlistOfferStatus.lost)
                outcome = _refusal_for_booking(rejected)

        # Committed even when refusing, so expired and lost offers are recorded.
        db.commit()
    except Exception:
        db.rollback()
        raise

    if isinstance(outcome, BookingRejected):
        raise outcome
    db.refresh(outcome.booking)
    return outcome
