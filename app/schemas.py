import re
from datetime import datetime, date, time
from typing import Annotated, Optional

from pydantic import AfterValidator, BaseModel, ConfigDict, EmailStr, field_validator, model_validator

from app.models import QueueStatus

WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

# The limits staff settings must stay within. The booking service has its own guard
# for values that are already stored, so these are not the only protection.
MIN_SLOT_MINUTES = 5
MAX_SLOT_MINUTES = 240
MIN_CAPACITY = 1
MAX_CAPACITY = 20


def _whole_local_minute(value: time) -> time:
    if value.tzinfo is not None:
        raise ValueError("Times are shop-local and must not carry a time zone")
    if value.second or value.microsecond:
        raise ValueError("Times must be whole minutes, such as 09:30")
    return value


MinuteTime = Annotated[time, AfterValidator(_whole_local_minute)]


def _whole_number(value, message: str):
    """Refuses true/false and fractions, which would otherwise be read as numbers."""
    if isinstance(value, bool) or (isinstance(value, float) and not value.is_integer()):
        raise ValueError(message)
    return value


PHONE_PATTERN = re.compile(r"^\+?[0-9\s\-()]{7,20}$")


def validate_phone(value: str) -> str:
    value = value.strip()
    digit_count = sum(char.isdigit() for char in value)
    if not PHONE_PATTERN.match(value) or digit_count < 7:
        raise ValueError("Enter a valid phone number (at least 7 digits)")
    return value


def validate_name(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("Enter your name")
    return value


class CheckInRequest(BaseModel):
    name: str
    phone: str
    appointment_date: date
    appointment_time: time
    note: Optional[str] = None

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value):
        return validate_name(value)

    @field_validator("phone")
    @classmethod
    def _validate_phone(cls, value):
        return validate_phone(value)


class CustomerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    phone: str


class CustomerListOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    phone: str
    created_at: datetime
    visit_count: int


class QueueEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    queue_date: date
    position: int
    status: QueueStatus
    note: Optional[str] = None
    appointment_time: time
    created_at: datetime
    customer: CustomerOut


class QueueStatusUpdate(BaseModel):
    status: QueueStatus


class QueueNoteUpdate(BaseModel):
    note: Optional[str] = None


class AvailableSlotOut(BaseModel):
    time: time
    capacity: int
    booked: int
    blocked: bool
    included: bool
    available: bool


class BlockedSlotRequest(BaseModel):
    date: date
    time: MinuteTime


class BlockedSlotOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    blocked_date: date
    blocked_time: time


class IncludedSlotRequest(BaseModel):
    date: date
    time: MinuteTime


class IncludedSlotOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    included_date: date
    included_time: time


class ShopStatusOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    booking_open: bool
    open_days: list[str]
    open_time: time
    close_time: time
    slot_duration_minutes: int
    capacity_per_slot: int
    notification_emails: list[str]

    @field_validator("open_days", mode="before")
    @classmethod
    def _split_open_days(cls, value):
        if isinstance(value, str):
            return [day for day in value.split(",") if day]
        return value

    @field_validator("notification_emails", mode="before")
    @classmethod
    def _split_notification_emails(cls, value):
        if isinstance(value, str):
            return [email for email in value.split(",") if email]
        if value is None:
            return []
        return value


class ShopStatusUpdate(BaseModel):
    booking_open: bool


class OpenDaysUpdate(BaseModel):
    open_days: list[str]

    @field_validator("open_days")
    @classmethod
    def _validate_days(cls, value):
        invalid = set(value) - set(WEEKDAYS)
        if invalid:
            # Never echoes what was sent: the list of valid codes says enough.
            raise ValueError(f"Open days must be chosen from: {', '.join(WEEKDAYS)}")
        if not value:
            raise ValueError("Choose at least one open day")
        # Stored once each and in week order, whatever order they arrived in.
        return [day for day in WEEKDAYS if day in value]


class ScheduleSettingsUpdate(BaseModel):
    open_time: MinuteTime
    close_time: MinuteTime
    slot_duration_minutes: int
    capacity_per_slot: int

    @field_validator("slot_duration_minutes", mode="before")
    @classmethod
    def _slot_length_is_a_whole_number(cls, value):
        return _whole_number(value, "Slot length must be a whole number of minutes")

    @field_validator("capacity_per_slot", mode="before")
    @classmethod
    def _capacity_is_a_whole_number(cls, value):
        return _whole_number(value, "Capacity must be a whole number")

    @field_validator("slot_duration_minutes")
    @classmethod
    def _slot_length_in_range(cls, value):
        if not MIN_SLOT_MINUTES <= value <= MAX_SLOT_MINUTES:
            raise ValueError(
                f"Slot length must be between {MIN_SLOT_MINUTES} and {MAX_SLOT_MINUTES} minutes"
            )
        return value

    @field_validator("capacity_per_slot")
    @classmethod
    def _capacity_in_range(cls, value):
        if not MIN_CAPACITY <= value <= MAX_CAPACITY:
            raise ValueError(f"Capacity must be between {MIN_CAPACITY} and {MAX_CAPACITY}")
        return value

    @model_validator(mode="after")
    def _a_slot_fits_in_the_day(self):
        opens = self.open_time.hour * 60 + self.open_time.minute
        closes = self.close_time.hour * 60 + self.close_time.minute
        if opens >= closes:
            raise ValueError("Opening time must be before closing time")
        if closes - opens < self.slot_duration_minutes:
            raise ValueError("At least one slot must fit between opening and closing time")
        return self


class NotificationEmailsUpdate(BaseModel):
    notification_emails: list[EmailStr]
