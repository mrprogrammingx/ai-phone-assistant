import enum
from datetime import time

from sqlalchemy import (
    Column,
    Integer,
    String,
    Text,
    DateTime,
    Date,
    Time,
    Enum,
    ForeignKey,
    Boolean,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.database import Base
from app.timezone import now, today, utc_now


class QueueStatus(str, enum.Enum):
    waiting = "waiting"
    next = "next"
    in_progress = "in_progress"
    done = "done"
    no_show = "no_show"
    cancelled = "cancelled"


class WaitlistEntryStatus(str, enum.Enum):
    active = "active"
    booked = "booked"
    expired = "expired"


class WaitlistOfferStatus(str, enum.Enum):
    open = "open"
    accepted = "accepted"
    lost = "lost"
    expired = "expired"


class CallRequestStatus(str, enum.Enum):
    booked = "booked"
    follow_up = "follow_up"


class Customer(Base):
    __tablename__ = "customers"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    phone = Column(String, nullable=False, unique=True, index=True)
    created_at = Column(DateTime, default=now)

    queue_entries = relationship("QueueEntry", back_populates="customer")


class QueueEntry(Base):
    __tablename__ = "queue_entries"

    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False)
    queue_date = Column(Date, default=today, index=True)
    position = Column(Integer, nullable=False)
    status = Column(Enum(QueueStatus), default=QueueStatus.waiting, nullable=False)
    note = Column(Text, nullable=True)
    appointment_time = Column(Time, nullable=False)

    # Both are empty for website bookings and rows older than these columns. An
    # appointment with no duration occupies one slot length.
    service_id = Column(String, nullable=True)
    duration_minutes = Column(Integer, nullable=True)

    created_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now, onupdate=now)

    customer = relationship("Customer", back_populates="queue_entries")


class WaitlistEntry(Base):
    """A caller who agreed to be contacted if a matching time is freed.

    Deliberately not linked to Customer: joining the waitlist is not a booking. All
    timestamps on the waitlist tables are UTC.
    """

    __tablename__ = "waitlist_entries"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    phone = Column(String, nullable=False, index=True)
    service_id = Column(String, nullable=False)
    duration_minutes = Column(Integer, nullable=False)
    requested_date = Column(Date, nullable=False, index=True)
    window_start = Column(Time, nullable=False)
    window_end = Column(Time, nullable=False)
    status = Column(Enum(WaitlistEntryStatus), default=WaitlistEntryStatus.active, nullable=False)
    consent_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=utc_now, nullable=False)
    closed_at = Column(DateTime, nullable=True)

    offers = relationship("WaitlistOffer", back_populates="entry")


class WaitlistOffer(Base):
    """A time offered to one waitlist entry. An offer never reserves the slot."""

    __tablename__ = "waitlist_offers"

    id = Column(Integer, primary_key=True, index=True)
    entry_id = Column(Integer, ForeignKey("waitlist_entries.id"), nullable=False, index=True)
    slot_date = Column(Date, nullable=False, index=True)
    slot_time = Column(Time, nullable=False)
    status = Column(Enum(WaitlistOfferStatus), default=WaitlistOfferStatus.open, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    queue_entry_id = Column(Integer, ForeignKey("queue_entries.id"), nullable=True)
    created_at = Column(DateTime, default=utc_now, nullable=False)
    closed_at = Column(DateTime, nullable=True)

    entry = relationship("WaitlistEntry", back_populates="offers")
    booking = relationship("QueueEntry")


class CallRequest(Base):
    """A completed call received by webhook. The transcript and raw payload are never stored.

    Timestamps are UTC. `reason` is a short code and holds no personal data.
    """

    __tablename__ = "call_requests"

    id = Column(Integer, primary_key=True, index=True)
    call_id = Column(String, nullable=False, unique=True)
    received_at = Column(DateTime, default=utc_now, nullable=False)
    status = Column(Enum(CallRequestStatus), nullable=False)
    reason = Column(String, nullable=True)
    booking_id = Column(Integer, ForeignKey("queue_entries.id"), nullable=True)
    name = Column(String, nullable=True)
    phone = Column(String, nullable=True)
    service_id = Column(String, nullable=True)
    requested_date = Column(Date, nullable=True)
    requested_time = Column(Time, nullable=True)
    handled_at = Column(DateTime, nullable=True)
    # Times that were free when the request arrived. Nothing is held for the caller.
    suggested_times = Column(String, nullable=True)
    suggested_as_of = Column(DateTime, nullable=True)


class BlockedSlot(Base):
    """A specific date+time slot the admin has excluded from booking (e.g. a lunch break)."""

    __tablename__ = "blocked_slots"
    __table_args__ = (UniqueConstraint("blocked_date", "blocked_time", name="uq_blocked_slot"),)

    id = Column(Integer, primary_key=True, index=True)
    blocked_date = Column(Date, nullable=False, index=True)
    blocked_time = Column(Time, nullable=False)
    created_at = Column(DateTime, default=now)


class IncludedSlot(Base):
    """A specific date+time slot the admin has added beyond normal open/close hours."""

    __tablename__ = "included_slots"
    __table_args__ = (UniqueConstraint("included_date", "included_time", name="uq_included_slot"),)

    id = Column(Integer, primary_key=True, index=True)
    included_date = Column(Date, nullable=False, index=True)
    included_time = Column(Time, nullable=False)
    created_at = Column(DateTime, default=now)


class ShopStatus(Base):
    """Singleton-style row holding shop scheduling settings."""

    __tablename__ = "shop_status"

    id = Column(Integer, primary_key=True, index=True)

    # Manual same-day override: turns new bookings for *today* on/off.
    # Independent of open_days (the weekly schedule) and existing appointments.
    booking_open = Column(Boolean, default=True, nullable=False)

    # Comma-separated weekday codes the shop is open, e.g. "mon,tue,wed,thu,fri"
    open_days = Column(String, default="mon,tue,wed,thu,fri,sat,sun", nullable=False)

    open_time = Column(Time, default=time(9, 0), nullable=False)
    close_time = Column(Time, default=time(18, 0), nullable=False)
    slot_duration_minutes = Column(Integer, default=30, nullable=False)
    capacity_per_slot = Column(Integer, default=1, nullable=False)

    # Comma-separated email addresses notified on check-in. Falls back to
    # the ADMIN_EMAIL env var when empty.
    notification_emails = Column(Text, nullable=True)

    updated_at = Column(DateTime, default=now, onupdate=now)
