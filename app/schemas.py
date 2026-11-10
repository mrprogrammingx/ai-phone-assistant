import re
import unicodedata
from datetime import datetime, date, time
from typing import Annotated, Optional

from pydantic import AfterValidator, BaseModel, ConfigDict, EmailStr, field_validator, model_validator

from app.models import QueueStatus
from app.phone import InvalidPhone, normalize_phone

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


MAX_NAME_LENGTH = 100
MAX_NOTE_LENGTH = 500
MAX_TYPED_PHONE_LENGTH = 40
PHONE_MESSAGE = "Enter a valid phone number (at least 7 digits)"


def _is_control(character: str) -> bool:
    """Control characters and the Unicode line and paragraph separators."""
    return unicodedata.category(character) in ("Cc", "Zl", "Zp")


def _is_invisible(character: str) -> bool:
    """Spaces, separators and every control or format character, such as a zero-width space."""
    return unicodedata.category(character)[0] in ("C", "Z")


def typed_phone(value: str) -> str:
    """Tidies a number as typed. Whether it is a real number is decided by `normalize_phone`."""
    value = value.strip()
    if not value or len(value) > MAX_TYPED_PHONE_LENGTH or any(_is_control(char) for char in value):
        raise ValueError(PHONE_MESSAGE)
    return value


def validate_phone(value: str) -> str:
    """Returns the number in E.164, or raises ValueError with a message fit to show."""
    try:
        return normalize_phone(typed_phone(value))
    except InvalidPhone:
        raise ValueError(PHONE_MESSAGE) from None


def validate_name(value: str) -> str:
    value = value.strip()
    if not value or all(_is_invisible(character) for character in value):
        raise ValueError("Enter your name")
    if any(_is_control(character) for character in value):
        raise ValueError("Name cannot contain line breaks or control characters")
    if len(value) > MAX_NAME_LENGTH:
        raise ValueError(f"Name must be at most {MAX_NAME_LENGTH} characters")
    return value


def clean_note(value: Optional[str]) -> Optional[str]:
    """Removes control characters but keeps line breaks. A note with nothing left is no note."""
    if value is None:
        return None
    text = value.replace("\r\n", "\n").replace("\r", "\n").replace("\t", " ")
    text = "".join(char for char in text if char == "\n" or not _is_control(char)).strip()
    if len(text) > MAX_NOTE_LENGTH:
        raise ValueError(f"Note must be at most {MAX_NOTE_LENGTH} characters")
    return text or None


CustomerName = Annotated[str, AfterValidator(validate_name)]
Note = Annotated[Optional[str], AfterValidator(clean_note)]


class CheckInRequest(BaseModel):
    name: CustomerName
    # Kept as typed. The route normalises it, because a customer stored before numbers
    # were normalised can only be found by the exact string.
    phone: Annotated[str, AfterValidator(typed_phone)]
    appointment_date: date
    appointment_time: MinuteTime
    note: Note = None


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
    note: Note = None


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
