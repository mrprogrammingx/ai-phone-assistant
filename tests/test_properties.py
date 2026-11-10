"""Property tests: rules that must hold for every input, not only the ones in a table.

They need Hypothesis, a development dependency (requirements-dev.txt). Without it this
file is skipped and the rest of the suite still runs.

Runs are derandomized, so the same inputs are tried every time and a failure in CI is a
failure on a laptop. To explore new inputs, run with HYPOTHESIS_PROFILE=explore.
"""
import os
import unicodedata
from datetime import date, datetime, time, timedelta, timezone
from unittest import mock

import pytest

hypothesis = pytest.importorskip("hypothesis")

from fastapi.testclient import TestClient
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, initialize, invariant, precondition, rule
from pydantic import ValidationError

from app import models  # noqa: F401
from app import timezone as app_timezone
from app.assistant_main import app as assistant_app
from app.database import Base, SessionLocal, engine
from app.main import app as main_app
from app.models import BlockedSlot, Customer, IncludedSlot, QueueEntry, QueueStatus, WaitlistEntry, WaitlistOffer
from app.phone import InvalidPhone, normalize_phone
from app.routers.assistant import CreateBookingRequest, JoinWaitlistRequest
from app.schemas import (
    MAX_NAME_LENGTH,
    MAX_NOTE_LENGTH,
    CheckInRequest,
    QueueNoteUpdate,
    clean_note,
    validate_name,
    validate_phone,
)
from app.services import booking
from app.services.call_requests import _normalized, parse_call_completed
from app.services.catalog import SERVICES
from app.services.waitlist import release_slot

# The autouse fixtures (clean database, frozen clock) are set up once per test, not once
# per example. Tests that write to the database reset it themselves at the start of
# each example, so that is safe here.
COMMON = dict(deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow])
settings.register_profile("suite", derandomize=True, database=None, print_blob=True, **COMMON)
settings.register_profile("explore", derandomize=False, **COMMON)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "suite"))

API_KEY = "test-assistant-key"
KEY = {"X-API-Key": API_KEY}
TODAY = date(2026, 12, 1)  # the frozen clock in tests/conftest.py
site = TestClient(main_app)
assistant = TestClient(assistant_app)


@pytest.fixture(autouse=True)
def assistant_key(monkeypatch):
    monkeypatch.setenv("ASSISTANT_API_KEY", API_KEY)


def fresh_database() -> None:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


def is_control(character: str) -> bool:
    return unicodedata.category(character) in ("Cc", "Zl", "Zp")


def is_invisible(character: str) -> bool:
    return unicodedata.category(character)[0] in ("C", "Z")


# --- names ------------------------------------------------------------------------

LINE_BREAKERS = st.characters(categories=["Cc", "Zl", "Zp"])
VISIBLE = st.characters(exclude_categories=["Cc", "Cs", "Cf", "Co", "Cn", "Zl", "Zp", "Zs"])
ANY_NAME = st.one_of(
    st.text(max_size=130),
    st.text(alphabet=st.one_of(VISIBLE, st.sampled_from(" \t\n\r ​  ")), max_size=130),
    st.text(alphabet=VISIBLE, min_size=95, max_size=106),
)


def accepted_name(typed: str) -> str | None:
    try:
        return validate_name(typed)
    except ValueError:
        return None


@given(ANY_NAME)
@settings(max_examples=400)
def test_an_accepted_name_is_always_trimmed_one_line_and_1_to_100_characters(typed):
    stored = accepted_name(typed)

    if stored is not None:
        assert stored == typed.strip()
        assert stored == stored.strip()
        assert 1 <= len(stored) <= MAX_NAME_LENGTH
        assert not any(is_control(character) for character in stored)
        assert "\n" not in stored and "\r" not in stored
        assert any(not is_invisible(character) for character in stored), "never spaces or invisible characters only"
        assert accepted_name(stored) == stored, "validating a stored name again changes nothing"


@given(ANY_NAME)
@settings(max_examples=400)
def test_a_name_is_refused_only_for_a_stated_reason(typed):
    trimmed = typed.strip()
    must_refuse = (
        not trimmed
        or all(is_invisible(character) for character in trimmed)
        or any(is_control(character) for character in trimmed)
        or len(trimmed) > MAX_NAME_LENGTH
    )

    assert (accepted_name(typed) is None) is must_refuse


@given(st.text(alphabet=VISIBLE, min_size=1, max_size=40), LINE_BREAKERS, st.text(alphabet=VISIBLE, min_size=1, max_size=40))
@settings(max_examples=300)
def test_a_name_with_any_line_break_or_control_character_inside_is_refused(before, breaker, after):
    assert accepted_name(before + breaker + after) is None


@given(st.text(alphabet=st.sampled_from(" \t\n\r\x0b\x0c   ​‍  　﻿"), max_size=30))
def test_a_name_of_only_spaces_or_invisible_characters_is_refused(typed):
    assert accepted_name(typed) is None


@given(ANY_NAME)
@settings(max_examples=300)
def test_the_website_and_the_assistant_accept_exactly_the_same_names(typed):
    def through(model, **fields):
        try:
            return model(**fields).name
        except ValidationError:
            return None

    website = through(CheckInRequest, name=typed, phone="+41791234567", appointment_date="2026-12-02",
                      appointment_time="10:00")
    booking_tool = through(CreateBookingRequest, name=typed, phone="+41791234567", date="2026-12-02", time="10:00",
                           service_id="classic-cut")
    waitlist_tool = through(JoinWaitlistRequest, name=typed, phone="+41791234567", service_id="classic-cut",
                            date="2026-12-02", **{"from": "10:00", "to": "11:00"})

    assert website == booking_tool == waitlist_tool == accepted_name(typed)


@given(ANY_NAME)
@settings(max_examples=40)
def test_no_request_ever_stores_a_name_that_breaks_the_rule(typed):
    fresh_database()

    response = site.post("/queue/checkin", json={"name": typed, "phone": "+41791234567",
                                                 "appointment_date": "2026-12-02", "appointment_time": "10:00"})

    assert response.status_code in (200, 422)
    with SessionLocal() as db:
        stored = [customer.name for customer in db.query(Customer)]
    assert stored == ([accepted_name(typed)] if response.status_code == 200 else [])
    assert (response.status_code == 200) is (accepted_name(typed) is not None)


# --- notes ------------------------------------------------------------------------

ANY_NOTE = st.one_of(
    st.text(max_size=600),
    st.text(alphabet=st.one_of(VISIBLE, LINE_BREAKERS, st.sampled_from(" \t\n\r")), max_size=600),
    st.text(alphabet=VISIBLE, min_size=495, max_size=506),
)


def kept_characters(text: str) -> list[str]:
    """What a note is made of once layout is ignored: everything but spacing and control characters."""
    return [character for character in text if not is_control(character) and not character.isspace()]


def cleaned(typed: str):
    try:
        return clean_note(typed)
    except ValueError:
        return ValueError


@given(ANY_NOTE)
@settings(max_examples=400)
def test_a_cleaned_note_is_short_trimmed_and_free_of_control_characters(typed):
    note = cleaned(typed)

    if note is ValueError:
        # Refused only for length, and only when the text really is too long once cleaned.
        assert len(kept_characters(typed)) + typed.count(" ") + typed.count("\n") + typed.count("\t") > MAX_NOTE_LENGTH
        return
    if note is None:
        assert kept_characters(typed) == []
        return
    assert 1 <= len(note) <= MAX_NOTE_LENGTH
    assert note == note.strip()
    assert "\r" not in note and "\t" not in note
    assert all(character == "\n" or not is_control(character) for character in note)
    assert kept_characters(note) == kept_characters(typed), "no visible character is lost or added"
    assert cleaned(note) == note, "cleaning a stored note again changes nothing"


@given(ANY_NOTE)
@settings(max_examples=300)
def test_a_note_is_refused_exactly_when_it_is_over_500_characters_after_cleaning(typed):
    expected = typed.replace("\r\n", "\n").replace("\r", "\n").replace("\t", " ")
    expected = "".join(character for character in expected if character == "\n" or not is_control(character)).strip()

    assert (cleaned(typed) is ValueError) is (len(expected) > MAX_NOTE_LENGTH)


@given(st.text(alphabet=VISIBLE, min_size=1, max_size=40), LINE_BREAKERS, st.text(alphabet=VISIBLE, min_size=1, max_size=40))
@settings(max_examples=300)
def test_every_control_character_is_removed_from_a_note(before, breaker, after):
    note = cleaned(before + breaker + after)

    if breaker in "\n\r":
        assert note == f"{before}\n{after}"
    elif breaker == "\t":
        assert note == f"{before} {after}"
    else:
        assert note == before + after


@given(ANY_NOTE)
@settings(max_examples=200)
def test_customer_notes_and_staff_notes_follow_one_rule(typed):
    def through(model, **fields):
        try:
            return model(**fields).note
        except ValidationError:
            return ValueError

    customer = through(CheckInRequest, name="Test Customer", phone="+41791234567", appointment_date="2026-12-02",
                       appointment_time="10:00", note=typed)

    assert customer == through(QueueNoteUpdate, note=typed) == cleaned(typed)


# --- phone numbers ------------------------------------------------------------------

SEPARATOR = st.sampled_from(["", "", " ", "  ", "-", ".", "/", " - "])


@st.composite
def swiss_numbers(draw):
    """A valid Swiss mobile number, written one of the ways people write them."""
    digits = draw(st.text(alphabet="0123456789", min_size=7, max_size=7))
    area = draw(st.sampled_from(["76", "77", "78", "79"]))
    groups = [area, digits[:3], digits[3:5], digits[5:]]
    prefix = draw(st.sampled_from(["0", "+41", "0041", "+41 (0)", "41"]))
    separator = draw(SEPARATOR) if prefix != "41" else ""
    body = separator.join(groups)
    if draw(st.booleans()) and prefix == "0":
        body = f"({prefix}{area}){separator}{separator.join(groups[1:])}"
        prefix = ""
    padding = draw(st.sampled_from(["", " ", "  "]))
    return f"{padding}{prefix}{' ' if prefix.startswith('+') and separator else ''}{body}{padding}", f"+41{area}{digits}"


PHONE_LIKE = st.text(alphabet="0123456789+ ()-./\n\t\r\x00 ", max_size=45)
ANY_PHONE = st.one_of(
    swiss_numbers().map(lambda pair: pair[0]),
    PHONE_LIKE,
    st.text(max_size=45),
    st.from_regex(r"\+[0-9]{5,18}", fullmatch=True),
    st.from_regex(r"0[0-9 ]{6,14}", fullmatch=True),
    swiss_numbers().flatmap(lambda pair: st.tuples(st.just(pair[0]), st.sampled_from(["\n", "\t", "\x00", " x", "9"]),
                                                   st.integers(0, len(pair[0]))))
    .map(lambda parts: parts[0][:parts[2]] + parts[1] + parts[0][parts[2]:]),
)


def website_says(typed: str) -> str | None:
    """The number the website would store, through the same function the form's route uses."""
    try:
        return validate_phone(typed)
    except ValueError:
        return None


def assistant_says(typed: str) -> str | None:
    """The number the assistant would look up, read back from a real tool call."""
    response = assistant.post("/assistant/bookings/find", json={"phone": typed}, headers=KEY)
    assert response.status_code == 200
    result = response.json()
    if not result["ok"]:
        assert result["code"] in ("invalid_phone", "invalid_input")
        return None
    try:
        return normalize_phone(typed)
    except InvalidPhone:
        pytest.fail(f"the assistant accepted {typed!r}, which cannot be normalised")


def webhook_says(typed: str) -> str | None:
    """The number the webhook would book under, through its own parser."""
    return _normalized(parse_call_completed({"callId": "x", "extracted": {"phone": typed}}).phone)


@given(ANY_PHONE)
@settings(max_examples=500)
def test_the_website_the_assistant_and_the_webhook_agree_on_every_phone_number(typed):
    website, by_phone, webhook = website_says(typed), assistant_says(typed), webhook_says(typed)

    assert website == by_phone == webhook, (
        f"website {website!r}, assistant {by_phone!r}, webhook {webhook!r} for {typed!r}"
    )


@given(ANY_PHONE)
@settings(max_examples=500)
def test_the_public_lookup_agrees_with_the_check_in_form(typed):
    """With no customers stored, the lookup answers 200 for a valid number and 400 for any other."""
    fresh_database()

    lookup = site.get("/queue/my-bookings", params={"phone": typed})
    form = site.post("/queue/checkin", json={"name": "Test Customer", "phone": typed,
                                             "appointment_date": "2026-12-02", "appointment_time": "10:00"})

    valid = website_says(typed) is not None
    assert lookup.status_code == (200 if valid else 400)
    assert form.status_code == (200 if valid else 422)
    if valid:
        assert form.json()["customer"]["phone"] == website_says(typed)


@given(ANY_PHONE)
@settings(max_examples=500)
def test_normalising_twice_gives_the_same_number(typed):
    once = website_says(typed)

    if once is not None:
        assert website_says(once) == once
        assert normalize_phone(once) == once
        assert once.startswith("+") and once[1:].isascii() and once[1:].isdigit()
        assert 8 <= len(once) - 1 <= 15
        assert once[1] != "0"


@given(swiss_numbers())
@settings(max_examples=300)
def test_every_spelling_of_a_swiss_number_is_the_same_number(pair):
    typed, expected = pair

    assert website_says(typed) == expected
    assert webhook_says(typed) == expected


@given(ANY_PHONE)
@settings(max_examples=300)
def test_a_refused_phone_number_never_reaches_the_database(typed):
    assume(website_says(typed) is None)
    fresh_database()

    site.post("/queue/checkin", json={"name": "Test Customer", "phone": typed, "appointment_date": "2026-12-02",
                                      "appointment_time": "10:00"})
    assistant.post("/assistant/bookings", headers=KEY, json={"name": "Test Customer", "phone": typed,
                                                             "date": "2026-12-02", "time": "10:00",
                                                             "service_id": "classic-cut"})
    assistant.post("/assistant/waitlist", headers=KEY, json={"name": "Test Customer", "phone": typed,
                                                             "service_id": "classic-cut", "date": "2026-12-02",
                                                             "from": "10:00", "to": "11:00", "consent": True})

    with SessionLocal() as db:
        assert db.query(Customer).count() == db.query(QueueEntry).count() == db.query(WaitlistEntry).count() == 0


# --- dates ------------------------------------------------------------------------

ANY_DATE = st.one_of(
    st.dates(),
    st.integers(-400, 400).map(lambda offset: TODAY + timedelta(days=offset)),
    st.integers(85, 96).map(lambda offset: TODAY + timedelta(days=offset)),
)
SLOT = st.builds(time, st.integers(9, 17), st.sampled_from([0, 30]))


@given(ANY_DATE)
@settings(max_examples=400)
def test_a_date_is_in_range_exactly_from_today_to_ninety_days_ahead(day):
    rejection = booking.date_rejection(day)
    offset = (day - TODAY).days

    if offset < 0:
        assert rejection is not None and rejection.code == "date_past"
    elif offset > booking.BOOKING_HORIZON_DAYS:
        assert rejection is not None and rejection.code == "date_too_far"
    else:
        assert rejection is None


@given(st.datetimes(min_value=datetime(2000, 1, 1), max_value=datetime(2100, 1, 1)), st.integers(-200, 200))
@settings(max_examples=300)
def test_the_horizon_moves_with_the_clock(instant, offset):
    """Whatever the time is, the last bookable day is the shop's own today plus ninety."""
    with mock.patch.object(app_timezone, "_utc_now", lambda: instant.replace(tzinfo=timezone.utc)):
        shop_today = app_timezone.today()
        rejection = booking.date_rejection(shop_today + timedelta(days=offset))

    assert (rejection is None) is (0 <= offset <= 90)


@given(ANY_DATE, SLOT, st.sampled_from(sorted(SERVICES)), st.booleans())
@settings(max_examples=120)
def test_nothing_is_ever_bookable_outside_the_horizon(day, slot, service_id, staff_added_the_time):
    assume(not 0 <= (day - TODAY).days <= booking.BOOKING_HORIZON_DAYS)
    fresh_database()
    if staff_added_the_time:
        # Even a time that staff added by hand, stored directly, does not open a day out of range.
        with SessionLocal() as db:
            db.add(IncludedSlot(included_date=day, included_time=slot))
            db.commit()
    iso, clock = day.isoformat(), slot.strftime("%H:%M")
    expected = "date_past" if day < TODAY else "date_too_far"

    website = site.post("/queue/checkin", json={"name": "Test Customer", "phone": "+41791234567",
                                                "appointment_date": iso, "appointment_time": clock})
    times = site.get("/queue/available-times", params={"for_date": iso})
    booked = assistant.post("/assistant/bookings", headers=KEY, json={
        "name": "Test Customer", "phone": "+41791234567", "date": iso, "time": clock, "service_id": service_id}).json()
    offered = assistant.post("/assistant/availability", headers=KEY, json={"date": iso, "service_id": service_id}).json()
    waiting = assistant.post("/assistant/waitlist", headers=KEY, json={
        "name": "Test Customer", "phone": "+41791234567", "service_id": service_id, "date": iso, "from": clock,
        "to": clock, "consent": True}).json()

    assert website.status_code == 400 and times.status_code == 400
    assert [booked["code"], offered["code"], waiting["code"]] == [expected] * 3
    with SessionLocal() as db:
        shop = booking.get_or_create_shop_status(db)
        with pytest.raises(booking.BookingRejected) as refused:
            booking.ensure_bookable(db, shop, day, slot, SERVICES[service_id]["duration_minutes"])
        db.rollback()
        assert refused.value.code == expected
        assert release_slot(db, day, slot).offers == []
        assert db.query(QueueEntry).count() == db.query(WaitlistEntry).count() == db.query(WaitlistOffer).count() == 0


@given(st.integers(1, 90), SLOT)
@settings(max_examples=60)
def test_every_day_inside_the_horizon_is_bookable_on_the_slot_grid(offset, slot):
    fresh_database()
    day = (TODAY + timedelta(days=offset)).isoformat()

    response = site.post("/queue/checkin", json={"name": "Test Customer", "phone": "+41791234567",
                                                 "appointment_date": day, "appointment_time": slot.strftime("%H:%M")})

    # The only reason left to refuse is a clock change on that night, which never touches opening hours.
    assert response.status_code == 200, response.text


# --- booking, cancelling, reviving and the waitlist, in any order -------------------

DAYS = [TODAY + timedelta(days=offset) for offset in (1, 2)]
PHONES = [f"+4179000{index:04d}" for index in range(8)]
GRID = [time(hour, minute) for hour in range(9, 18) for minute in (0, 30)]
OFF_GRID = [time(8, 30), time(9, 15), time(12, 10), time(18, 0), time(19, 30)]
SERVICE_IDS = sorted(SERVICES)


class Shop(RuleBasedStateMachine):
    """Customers, callers and staff use both applications in any order Hypothesis can find.

    Opening hours and slot length stay fixed, and capacity is chosen once at the start,
    so the capacity rule must hold for every appointment at every moment, not only for
    the one just booked.
    """

    @initialize(capacity=st.integers(1, 3))
    def open_the_shop(self, capacity):
        fresh_database()
        self.staff = TestClient(main_app)
        assert self.staff.post("/api/login", json={"username": "admin", "password": "test-password-123"}).status_code == 200
        changed = self.staff.post("/admin/schedule-settings", json={
            "open_time": "09:00", "close_time": "18:00", "slot_duration_minutes": 30, "capacity_per_slot": capacity})
        assert changed.status_code == 200
        self.capacity = capacity
        self.booked = 0

    def tool(self, path, **body):
        response = assistant.post(f"/assistant/{path}", json=body, headers=KEY)
        assert response.status_code == 200, response.text
        return response.json()

    def entries(self):
        with SessionLocal() as db:
            return [
                (entry.id, entry.customer.phone, entry.queue_date, entry.appointment_time, entry.status)
                for entry in db.query(QueueEntry).order_by(QueueEntry.id)
            ]

    @rule(day=st.sampled_from(DAYS), slot=st.sampled_from(GRID + OFF_GRID), phone=st.sampled_from(PHONES))
    def book_on_the_website(self, day, slot, phone):
        response = site.post("/queue/checkin", json={
            "name": "Web Customer", "phone": phone, "appointment_date": day.isoformat(),
            "appointment_time": slot.strftime("%H:%M")})
        assert response.status_code in (200, 400), response.text
        self.booked += response.status_code == 200

    @rule(day=st.sampled_from(DAYS), slot=st.sampled_from(GRID + OFF_GRID), phone=st.sampled_from(PHONES),
          service=st.sampled_from(SERVICE_IDS))
    def book_by_phone(self, day, slot, phone, service):
        result = self.tool("bookings", name="Phone Caller", phone=phone, date=day.isoformat(),
                           time=slot.strftime("%H:%M"), service_id=service)
        if result["ok"]:
            self.booked += result["created"]
        else:
            assert result["code"] in ("slot_taken", "invalid_time")

    @precondition(lambda self: self.booked > 0)
    @rule(data=st.data())
    def cancel(self, data):
        _, phone, day, slot, status = data.draw(st.sampled_from(self.entries()))
        response = assistant.post("/assistant/bookings/cancel", headers=KEY, json={
            "phone": phone, "date": day.isoformat(), "time": slot.strftime("%H:%M")})
        result = response.json()
        if status in (QueueStatus.waiting, QueueStatus.next):
            assert result["ok"] is True and result["cancelled"] is True

    @precondition(lambda self: self.booked > 0)
    @rule(data=st.data())
    def mark_no_show(self, data):
        entry_id, *_ = data.draw(st.sampled_from(self.entries()))
        assert self.staff.post(f"/queue/{entry_id}/no-show").status_code in (200, 409)

    @precondition(lambda self: self.booked > 0)
    @rule(data=st.data())
    def revive_or_finish(self, data):
        """Marking a No Show as done takes its slot back, which must fail if the slot has been given away."""
        entry_id, *_ = data.draw(st.sampled_from(self.entries()))
        assert self.staff.post(f"/queue/{entry_id}/done").status_code in (200, 409)

    @rule(day=st.sampled_from(DAYS), start=st.sampled_from(GRID), length=st.integers(0, 4),
          phone=st.sampled_from(PHONES), service=st.sampled_from(SERVICE_IDS))
    def join_the_waitlist(self, day, start, length, phone, service):
        end = GRID[min(GRID.index(start) + length, len(GRID) - 1)]
        result = self.tool("waitlist", name="Wait Lister", phone=phone, service_id=service, date=day.isoformat(),
                           consent=True, **{"from": start.strftime("%H:%M"), "to": end.strftime("%H:%M")})
        assert result["ok"] is True

    @rule(day=st.sampled_from(DAYS), slot=st.sampled_from(GRID))
    def release_a_time(self, day, slot):
        with SessionLocal() as db:
            release_slot(db, day, slot)

    @rule(data=st.data())
    def accept_an_offer(self, data):
        with SessionLocal() as db:
            offers = [(offer.entry.phone, offer.slot_date, offer.slot_time) for offer in db.query(WaitlistOffer)]
        if not offers:
            return
        phone, day, slot = data.draw(st.sampled_from(offers))
        result = self.tool("waitlist/accept", phone=phone, date=day.isoformat(), time=slot.strftime("%H:%M"))
        if not result["ok"]:
            assert result["code"] in ("no_open_offer", "offer_expired", "slot_taken")

    @rule(day=st.sampled_from(DAYS), slot=st.sampled_from(GRID), block=st.booleans())
    def staff_block_or_unblock_a_time(self, day, slot, block):
        body = {"date": day.isoformat(), "time": slot.strftime("%H:%M")}
        response = self.staff.request("POST" if block else "DELETE", "/admin/blocked-slots", json=body)
        assert response.status_code in (200, 404)

    @rule(day=st.sampled_from(DAYS), slot=st.sampled_from(OFF_GRID))
    def staff_add_an_extra_time(self, day, slot):
        response = self.staff.post("/admin/included-slots", json={"date": day.isoformat(), "time": slot.strftime("%H:%M")})
        assert response.status_code == 200

    @invariant()
    def no_moment_has_more_appointments_than_capacity(self):
        if not hasattr(self, "capacity"):
            return
        with SessionLocal() as db:
            occupying = (
                db.query(QueueEntry).filter(QueueEntry.status.in_(booking.SLOT_OCCUPYING_STATUSES)).all()
            )
            intervals = [
                (datetime.combine(entry.queue_date, entry.appointment_time),
                 datetime.combine(entry.queue_date, entry.appointment_time)
                 + timedelta(minutes=entry.duration_minutes or 30))
                for entry in occupying
            ]
        # Occupancy can only rise at the moment an appointment starts.
        for start, _ in intervals:
            running = sum(begins <= start < ends for begins, ends in intervals)
            assert running <= self.capacity, f"{running} appointments at {start}, capacity {self.capacity}"

    @invariant()
    def the_records_stay_consistent(self):
        if not hasattr(self, "capacity"):
            return
        with SessionLocal() as db:
            phones = [customer.phone for customer in db.query(Customer)]
            assert len(phones) == len(set(phones))
            assert all(phone in PHONES for phone in phones)
            for entry in db.query(QueueEntry):
                assert entry.position >= 1 and entry.status is not None
                assert entry.appointment_time in GRID + OFF_GRID
            blocked = {(row.blocked_date, row.blocked_time) for row in db.query(BlockedSlot)}
            assert len(blocked) == db.query(BlockedSlot).count()
            accepted = [offer.queue_entry_id for offer in db.query(WaitlistOffer) if offer.queue_entry_id is not None]
            assert len(accepted) == len(set(accepted)), "one booking per accepted offer"

    @invariant()
    def what_is_offered_can_be_booked(self):
        """A time shown as available on the website is under capacity and not blocked."""
        if not hasattr(self, "capacity"):
            return
        response = site.get("/queue/available-times", params={"for_date": DAYS[0].isoformat()})
        assert response.status_code == 200
        for slot in response.json():
            assert 0 <= slot["booked"] and slot["capacity"] == self.capacity
            if slot["available"]:
                assert slot["booked"] < slot["capacity"] and not slot["blocked"]


Shop.TestCase.settings = settings(max_examples=60, stateful_step_count=30, **COMMON)
TestShopInAnyOrder = Shop.TestCase
