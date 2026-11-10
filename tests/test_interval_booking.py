"""Appointments occupy a whole interval, not just a start time."""
import os
import sqlite3
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.assistant_main import app as assistant_app
from app.database import SessionLocal
from app.main import app as main_app
from app.models import BlockedSlot, Customer, IncludedSlot, QueueEntry
from app.services.booking import get_or_create_shop_status

API_KEY = "test-assistant-key"
HEADERS = {"X-API-Key": API_KEY}
DAY = "2026-12-02"
PHONE = "+41791234567"
OTHER_PHONE = "+41791234568"
CONCURRENT_REQUESTS = 12
REPOSITORY = Path(__file__).resolve().parents[1]

CLASSIC = "classic-cut"  # 30 minutes
FADE = "signature-fade"  # 45 minutes
COMBO = "cut-beard-combo"  # 60 minutes

web = TestClient(main_app)
assistant = TestClient(assistant_app)


@pytest.fixture(autouse=True)
def assistant_api_key(monkeypatch):
    monkeypatch.setenv("ASSISTANT_API_KEY", API_KEY)


def tool(path, **body):
    response = assistant.post(f"/assistant/{path}", json=body, headers=HEADERS)
    assert response.status_code == 200
    return response.json()


def phone_book(slot, service=CLASSIC, day=DAY, phone=PHONE):
    return tool("bookings", name="Test Customer", phone=phone, date=day, time=slot, service_id=service)


def web_book(slot, day=DAY, phone=OTHER_PHONE):
    return web.post(
        "/queue/checkin",
        json={"name": "Web Customer", "phone": phone, "appointment_date": day, "appointment_time": f"{slot}:00"},
    )


def phone_slots(service=CLASSIC, day=DAY, **window):
    return tool("availability", date=day, service_id=service, **window)["slots"]


def website_available(day=DAY):
    slots = web.get("/queue/available-times", params={"for_date": day}).json()
    return {slot["time"][:5]: slot["available"] for slot in slots}


def assert_refused(result, code):
    assert result["ok"] is False
    assert result["code"] == code


def update_shop(**values):
    with SessionLocal() as db:
        status = get_or_create_shop_status(db)
        for field, value in values.items():
            setattr(status, field, value)
        db.commit()


def add_row(row):
    with SessionLocal() as db:
        db.add(row)
        db.commit()


def include_slot(slot, day=DAY):
    add_row(IncludedSlot(included_date=date.fromisoformat(day), included_time=slot))


def run_together(attempts):
    start_together = threading.Barrier(len(attempts))

    def run(attempt):
        start_together.wait()
        return attempt()

    with ThreadPoolExecutor(len(attempts)) as pool:
        return list(pool.map(run, attempts))


# The three reported problems


def test_problem_included_slot_cannot_overlap_its_neighbours():
    include_slot(time(15, 15))

    assert web_book("15:00", phone="+41790000001").status_code == 200
    assert web_book("15:15", phone="+41790000002").status_code == 400
    assert web_book("15:30", phone="+41790000003").status_code == 200


def test_problem_booking_the_included_slot_first_blocks_both_neighbours():
    include_slot(time(15, 15))

    assert web_book("15:15", phone="+41790000001").status_code == 200
    assert web_book("15:00", phone="+41790000002").status_code == 400
    assert web_book("15:30", phone="+41790000003").status_code == 400


def test_problem_new_slot_length_cannot_overrun_an_existing_appointment():
    assert web_book("14:30").status_code == 200

    update_shop(slot_duration_minutes=45)

    assert website_available()["14:15"] is False
    assert web_book("14:15", phone="+41790000002").status_code == 400


@pytest.mark.parametrize("service, name", [(FADE, "Signature Fade"), (COMBO, "Cut + Beard Combo")])
def test_problem_featured_services_can_be_booked_by_phone(service, name):
    result = phone_book("10:00", service=service)

    assert result["ok"] is True
    assert result["booking"]["service"] == name
    assert service in {s["id"] for s in tool("info")["services"] if s["phone_bookable"]}


# Overlap


def test_sixty_minute_booking_blocks_its_whole_interval():
    assert phone_book("10:00", service=COMBO)["ok"] is True

    available = website_available()
    assert available["10:00"] is False
    assert available["10:30"] is False
    assert available["11:00"] is True
    assert_refused(phone_book("10:30", phone=OTHER_PHONE), "slot_taken")
    assert phone_book("11:00", phone=OTHER_PHONE)["ok"] is True


def test_later_booking_blocks_a_long_service_that_would_reach_it():
    assert phone_book("10:30")["ok"] is True

    assert_refused(phone_book("10:00", service=COMBO, phone=OTHER_PHONE), "slot_taken")
    assert phone_book("10:00", phone=OTHER_PHONE)["ok"] is True


def test_short_booking_ending_at_a_start_does_not_conflict():
    assert phone_book("10:00")["ok"] is True
    assert phone_book("10:30", service=COMBO, phone=OTHER_PHONE)["ok"] is True


def test_partial_slot_service_still_blocks_only_what_it_overlaps():
    assert phone_book("10:00", service=FADE)["ok"] is True  # runs to 10:45

    assert website_available()["10:30"] is False
    assert website_available()["11:00"] is True


def test_availability_offers_only_starts_where_the_whole_service_fits():
    assert phone_book("10:30")["ok"] is True

    assert phone_slots(COMBO, **{"from": "09:00", "to": "11:30"}) == ["09:00", "09:30", "11:00", "11:30"]
    assert phone_slots(CLASSIC, **{"from": "09:00", "to": "11:30"}) == ["09:00", "09:30", "10:00", "11:00", "11:30"]


def test_website_availability_uses_one_slot_length():
    assert website_available()["17:30"] is True
    assert phone_slots(COMBO, **{"from": "17:00", "to": "17:30"}) == ["17:00"]


# Capacity


def test_capacity_two_refuses_only_when_the_peak_is_reached():
    update_shop(capacity_per_slot=2)

    assert phone_book("10:00", service=COMBO, phone="+41790000001")["ok"] is True
    assert phone_book("10:30", phone="+41790000002")["ok"] is True
    assert_refused(phone_book("10:30", phone="+41790000003"), "slot_taken")
    assert phone_book("10:00", phone="+41790000004")["ok"] is True
    assert_refused(phone_book("10:00", service=COMBO, phone="+41790000005"), "slot_taken")


def test_capacity_counts_the_peak_not_the_number_of_overlaps():
    update_shop(capacity_per_slot=2)

    assert phone_book("10:00", phone="+41790000001")["ok"] is True
    assert phone_book("10:30", phone="+41790000002")["ok"] is True

    assert phone_book("10:00", service=COMBO, phone="+41790000003")["ok"] is True


# Rows without a duration


def test_row_without_duration_occupies_exactly_one_slot():
    assert web_book("10:00").status_code == 200
    with SessionLocal() as db:
        entry = db.query(QueueEntry).one()
        assert entry.duration_minutes is None
        assert entry.service_id is None

    assert_refused(phone_book("09:30", service=COMBO), "slot_taken")
    assert phone_book("10:30", service=COMBO)["ok"] is True


def test_row_without_duration_uses_the_current_slot_length():
    assert web_book("14:30").status_code == 200

    update_shop(slot_duration_minutes=45)

    # The row now reads as 14:30 to 15:15, so the 15:00 start must be unavailable.
    assert website_available()["15:00"] is False


# Opening hours


def test_interval_may_end_exactly_at_closing_time():
    assert phone_book("17:30")["ok"] is True


def test_interval_one_minute_past_closing_time_is_refused():
    update_shop(close_time=time(17, 59))

    assert_refused(phone_book("17:30"), "invalid_time")
    assert website_available()["17:30"] is False


def test_long_service_cannot_run_past_closing_time():
    assert_refused(phone_book("17:30", service=COMBO), "invalid_time")


def test_including_a_regular_slot_does_not_extend_it_past_closing_time():
    include_slot(time(17, 30))

    assert "17:30" not in phone_slots(COMBO, **{"from": "17:00"})
    assert_refused(phone_book("17:30", service=COMBO), "invalid_time")


def test_included_slot_may_run_past_closing_time():
    include_slot(time(17, 45))

    assert phone_book("17:45", service=COMBO)["ok"] is True


def test_included_slot_still_cannot_overlap_anything():
    include_slot(time(17, 45))
    assert phone_book("17:30")["ok"] is True

    assert_refused(phone_book("17:45", service=COMBO, phone=OTHER_PHONE), "slot_taken")


def test_included_slot_cannot_cross_midnight():
    include_slot(time(23, 30))

    assert_refused(phone_book("23:30", service=COMBO), "invalid_time")


def test_included_slot_may_end_exactly_at_midnight():
    include_slot(time(23, 30))

    assert phone_book("23:30")["ok"] is True


# Blocked slots


def test_blocked_slot_in_the_middle_of_an_interval_refuses_it():
    add_row(BlockedSlot(blocked_date=date(2026, 12, 2), blocked_time=time(10, 30)))

    assert_refused(phone_book("10:00", service=COMBO), "slot_taken")
    assert phone_book("10:00", phone=OTHER_PHONE)["ok"] is True
    assert "10:00" not in phone_slots(COMBO)


# Concurrency


@pytest.mark.parametrize("hour", [9, 10, 11, 12, 13, 14])
def test_concurrent_overlapping_bookings_have_one_winner(hour):
    def attempt(index):
        if index % 2:
            return lambda: phone_book(f"{hour:02d}:00", service=COMBO, phone=f"+4179{index:07d}")
        return lambda: phone_book(f"{hour:02d}:30", phone=f"+4179{index:07d}")

    results = run_together([attempt(index) for index in range(CONCURRENT_REQUESTS)])

    assert sum(result["ok"] for result in results) == 1
    assert all(result["code"] == "slot_taken" for result in results if not result["ok"])
    with SessionLocal() as db:
        assert db.query(QueueEntry).count() == 1


@pytest.mark.parametrize("hour", [9, 10, 11, 12, 13, 14])
def test_website_and_assistant_cannot_both_book_an_overlap(hour):
    def attempt(index):
        if index % 2:
            return lambda: phone_book(f"{hour:02d}:00", service=COMBO, phone=f"+4179{index:07d}")["ok"]
        return lambda: web_book(f"{hour:02d}:30", phone=f"+4178{index:07d}").status_code == 200

    results = run_together([attempt(index) for index in range(CONCURRENT_REQUESTS)])

    assert sum(results) == 1
    with SessionLocal() as db:
        assert db.query(QueueEntry).count() == 1


# Cancelling


def test_cancel_frees_the_full_interval_and_rebooking_works():
    assert phone_book("10:00", service=COMBO)["ok"] is True
    assert website_available()["10:30"] is False

    assert tool("bookings/cancel", phone=PHONE, date=DAY, time="10:00")["cancelled"] is True

    available = website_available()
    assert available["10:00"] is True
    assert available["10:30"] is True
    assert phone_book("10:00", service=COMBO)["created"] is True


# Daylight saving


# Each clock change is tested from a few weeks before it, so the date is inside the
# booking horizon and a refusal can only come from the daylight-saving rule.
CLOCK_CHANGES = [
    ("2027-03-28", datetime(2027, 3, 1, 9, 0, tzinfo=timezone.utc)),
    ("2027-10-31", datetime(2027, 10, 1, 9, 0, tzinfo=timezone.utc)),
]


@pytest.mark.parametrize("day, clock", CLOCK_CHANGES, ids=["skipped-hour", "repeated-hour"])
def test_interval_across_a_clock_change_is_refused(day, clock, set_clock):
    set_clock(clock)
    include_slot(time(1, 30), day=day)

    result = phone_book("01:30", service=COMBO, day=day)

    assert_refused(result, "invalid_time")
    assert result["message"] == "That time is not available"
    # The same slot is fine for an appointment that ends before the change, so the
    # refusal above is the clock rule and nothing else.
    assert phone_book("01:30", day=day)["ok"] is True


@pytest.mark.parametrize("day, clock", CLOCK_CHANGES, ids=["skipped-hour", "repeated-hour"])
def test_interval_ending_exactly_at_a_clock_change_is_allowed(day, clock, set_clock):
    set_clock(clock)
    include_slot(time(1, 30), day=day)

    assert phone_book("01:30", day=day)["ok"] is True


# Stored service


def test_booking_stores_service_and_duration():
    phone_book("10:00", service=COMBO)

    with SessionLocal() as db:
        entry = db.query(QueueEntry).one()
        assert entry.service_id == COMBO
        assert entry.duration_minutes == 60
        assert entry.note == "Service: Cut + Beard Combo (60 min)"


def test_changing_the_display_note_has_no_effect_on_find_booking():
    phone_book("10:00", service=COMBO)
    with SessionLocal() as db:
        db.query(QueueEntry).one().note = "Service: Hot Towel Shave (35 min)"
        db.commit()

    assert tool("bookings/find", phone=PHONE)["bookings"][0]["service"] == "Cut + Beard Combo"


def test_changing_the_display_note_has_no_effect_on_the_interval():
    phone_book("10:00", service=COMBO)
    with SessionLocal() as db:
        db.query(QueueEntry).one().note = "Service: Beard Sculpt (20 min)"
        db.commit()

    assert_refused(phone_book("10:30", phone=OTHER_PHONE), "slot_taken")


def test_old_row_with_only_a_note_returns_no_service():
    with SessionLocal() as db:
        customer = Customer(name="Old Customer", phone=PHONE)
        db.add(customer)
        db.flush()
        db.add(
            QueueEntry(
                customer_id=customer.id,
                queue_date=date(2026, 12, 2),
                position=1,
                note="Phone: Classic Cut",
                appointment_time=time(10, 0),
            )
        )
        db.commit()

    assert tool("bookings/find", phone=PHONE)["bookings"][0]["service"] is None


# phone_bookable


def test_every_service_is_phone_bookable_on_a_normal_day():
    assert all(service["phone_bookable"] for service in tool("info")["services"])


def test_service_longer_than_the_opening_day_is_not_phone_bookable():
    update_shop(open_time=time(9, 0), close_time=time(9, 50))

    services = {service["id"]: service["phone_bookable"] for service in tool("info")["services"]}

    assert services[COMBO] is False
    assert services[FADE] is True
    assert services[CLASSIC] is True


def test_removed_failure_code_is_gone():
    assert "service_not_phone_bookable" not in str(phone_book("17:30", service=COMBO))


# Startup check


OLD_QUEUE_ENTRIES = """
CREATE TABLE queue_entries (
    id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL, queue_date DATE,
    position INTEGER NOT NULL, status VARCHAR(11) NOT NULL, note TEXT,
    appointment_time TIME NOT NULL, created_at DATETIME, updated_at DATETIME
)
"""
SQLITE_ALTERS = [
    "ALTER TABLE queue_entries ADD COLUMN service_id VARCHAR;",
    "ALTER TABLE queue_entries ADD COLUMN duration_minutes INTEGER;",
]
POSTGRES_ALTERS = [
    "ALTER TABLE queue_entries ADD COLUMN IF NOT EXISTS service_id VARCHAR;",
    "ALTER TABLE queue_entries ADD COLUMN IF NOT EXISTS duration_minutes INTEGER;",
]


def start_app(module, database):
    return subprocess.run(
        [sys.executable, "-c", f"import {module}"],
        cwd=REPOSITORY,
        env={**os.environ, "DATABASE_URL": f"sqlite:///{database}"},
        capture_output=True,
        text=True,
        timeout=60,
    )


def old_database(tmp_path, statements=()):
    path = tmp_path / "old.db"
    connection = sqlite3.connect(path)
    connection.execute(OLD_QUEUE_ENTRIES)
    for statement in statements:
        connection.execute(statement)
    connection.commit()
    connection.close()
    return path


@pytest.mark.parametrize("module", ["app.main", "app.assistant_main"])
def test_app_refuses_to_start_when_columns_are_missing(tmp_path, module):
    result = start_app(module, old_database(tmp_path))

    assert result.returncode != 0
    for statement in SQLITE_ALTERS + POSTGRES_ALTERS:
        assert statement in result.stderr


@pytest.mark.parametrize("module", ["app.main", "app.assistant_main"])
def test_app_refuses_to_start_when_one_column_is_missing(tmp_path, module):
    result = start_app(module, old_database(tmp_path, SQLITE_ALTERS[:1]))

    assert result.returncode != 0
    assert "duration_minutes" in result.stderr


@pytest.mark.parametrize("module", ["app.main", "app.assistant_main"])
def test_app_starts_after_the_documented_migration(tmp_path, module):
    result = start_app(module, old_database(tmp_path, SQLITE_ALTERS))

    assert result.returncode == 0, result.stderr


def test_app_starts_on_a_brand_new_database(tmp_path):
    assert start_app("app.main", tmp_path / "new.db").returncode == 0


# Startup check for call_requests

CALL_REQUESTS_WITHOUT_SUGGESTIONS = """
CREATE TABLE call_requests (
    id INTEGER PRIMARY KEY, call_id VARCHAR NOT NULL UNIQUE, received_at DATETIME NOT NULL,
    status VARCHAR(9) NOT NULL, reason VARCHAR, booking_id INTEGER, name VARCHAR, phone VARCHAR,
    service_id VARCHAR, requested_date DATE, requested_time TIME, handled_at DATETIME
)
"""
CALL_REQUEST_ALTERS = {
    "suggested_times": (
        "ALTER TABLE call_requests ADD COLUMN suggested_times VARCHAR;",
        "ALTER TABLE call_requests ADD COLUMN IF NOT EXISTS suggested_times VARCHAR;",
    ),
    "suggested_as_of": (
        "ALTER TABLE call_requests ADD COLUMN suggested_as_of DATETIME;",
        "ALTER TABLE call_requests ADD COLUMN IF NOT EXISTS suggested_as_of TIMESTAMP;",
    ),
}


def database_with_old_call_requests(tmp_path, statements=()):
    path = tmp_path / "old_calls.db"
    connection = sqlite3.connect(path)
    connection.execute(CALL_REQUESTS_WITHOUT_SUGGESTIONS)
    for statement in statements:
        connection.execute(statement)
    connection.commit()
    connection.close()
    return path


def refusal_message(result):
    assert result.returncode != 0
    return result.stderr[result.stderr.index("DatabaseSchemaOutOfDate:"):]


def columns_of(path, table):
    connection = sqlite3.connect(path)
    try:
        return {row[1] for row in connection.execute(f"pragma table_info({table})")}
    finally:
        connection.close()


@pytest.mark.parametrize("module", ["app.main", "app.assistant_main"])
def test_app_refuses_to_start_when_call_requests_lacks_both_columns(tmp_path, module):
    message = refusal_message(start_app(module, database_with_old_call_requests(tmp_path)))

    for column, statements in CALL_REQUEST_ALTERS.items():
        assert column in message
        for statement in statements:
            assert statement in message


@pytest.mark.parametrize(
    "present, missing",
    [("suggested_times", "suggested_as_of"), ("suggested_as_of", "suggested_times")],
)
@pytest.mark.parametrize("module", ["app.main", "app.assistant_main"])
def test_message_names_only_the_call_requests_column_that_is_missing(tmp_path, module, present, missing):
    database = database_with_old_call_requests(tmp_path, CALL_REQUEST_ALTERS[present][:1])

    message = refusal_message(start_app(module, database))

    assert missing in message
    assert present not in message


def test_the_local_commands_also_refuse_old_call_requests(tmp_path):
    database = database_with_old_call_requests(tmp_path)

    result = subprocess.run(
        [sys.executable, "-m", "app.scripts.list_requests"],
        cwd=REPOSITORY,
        env={**os.environ, "DATABASE_URL": f"sqlite:///{database}"},
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert "suggested_times" in refusal_message(result)


@pytest.mark.parametrize("module", ["app.main", "app.assistant_main"])
def test_database_without_call_requests_starts_and_creates_it(tmp_path, module):
    database = tmp_path / "no_calls.db"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE unrelated (id INTEGER PRIMARY KEY)")
    connection.commit()
    connection.close()

    result = start_app(module, database)

    assert result.returncode == 0, result.stderr
    assert {"suggested_times", "suggested_as_of"} <= columns_of(database, "call_requests")


def documented_call_request_alters():
    readme = (REPOSITORY / "README.md").read_text(encoding="utf-8")
    return [
        line.strip()
        for line in readme.splitlines()
        if line.startswith("ALTER TABLE call_requests ADD COLUMN ") and "IF NOT EXISTS" not in line
    ]


def test_readme_documents_both_commands_for_each_database():
    readme = (REPOSITORY / "README.md").read_text(encoding="utf-8")

    for statements in CALL_REQUEST_ALTERS.values():
        for statement in statements:
            assert statement in readme


@pytest.mark.parametrize("module", ["app.main", "app.assistant_main"])
def test_documented_call_requests_migration_lets_the_app_start(tmp_path, module):
    statements = documented_call_request_alters()
    assert len(statements) == 2
    database = database_with_old_call_requests(tmp_path, statements)

    result = start_app(module, database)

    assert result.returncode == 0, result.stderr


def test_fresh_database_has_both_call_requests_columns(tmp_path):
    database = tmp_path / "fresh.db"

    assert start_app("app.main", database).returncode == 0

    assert {"suggested_times", "suggested_as_of"} <= columns_of(database, "call_requests")


def test_queue_entries_message_is_reported_first_and_unchanged(tmp_path):
    database = old_database(tmp_path)
    connection = sqlite3.connect(database)
    connection.execute(CALL_REQUESTS_WITHOUT_SUGGESTIONS)
    connection.commit()
    connection.close()

    message = refusal_message(start_app("app.main", database))

    for statement in SQLITE_ALTERS + POSTGRES_ALTERS:
        assert statement in message
    assert "call_requests" not in message
