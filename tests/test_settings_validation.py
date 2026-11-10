"""Staff settings can never freeze booking.

Two layers are tested: the request schema refuses bad values, and the booking
service survives bad values that are already stored (an old database, a manual edit).

The stored-value cases run in a child process. A regression there is an endless
loop, and a child can be killed, where a looping thread would hold the database
lock and take the rest of the suite down with it.
"""
import json
import os
import subprocess
import sys
import time as clock
from concurrent.futures import ThreadPoolExecutor
from datetime import date, time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models import BlockedSlot, IncludedSlot
from app.services.booking import get_or_create_shop_status
from tests.conftest import TEST_TODAY

REPO_ROOT = Path(__file__).resolve().parents[1]
TOMORROW = "2026-12-02"  # a Wednesday
VALID = {
    "open_time": "09:00",
    "close_time": "18:00",
    "slot_duration_minutes": 30,
    "capacity_per_slot": 1,
}


@pytest.fixture
def admin():
    client = TestClient(app)
    response = client.post("/api/login", json={"username": "admin", "password": "test-password-123"})
    assert response.status_code == 200
    return client


def stored_settings() -> dict:
    with SessionLocal() as db:
        status = get_or_create_shop_status(db)
        return {
            "open_days": status.open_days,
            "open_time": status.open_time,
            "close_time": status.close_time,
            "slot_duration_minutes": status.slot_duration_minutes,
            "capacity_per_slot": status.capacity_per_slot,
        }


def assert_refused(response, expected_text: str, status_code: int = 422) -> None:
    assert response.status_code == status_code, response.text
    detail = response.json()["detail"]
    if isinstance(detail, str):
        messages = [detail]
    else:
        messages = [item["msg"] for item in detail]
    assert any(expected_text.lower() in message.lower() for message in messages), messages


# --- schedule settings: the request schema ---------------------------------------


@pytest.mark.parametrize("minutes", [0, -10, 1, 4, 241, 1000])
def test_slot_length_outside_5_to_240_is_refused_and_nothing_changes(admin, minutes):
    before = stored_settings()

    response = admin.post("/admin/schedule-settings", json={**VALID, "slot_duration_minutes": minutes})

    assert_refused(response, "slot length must be between 5 and 240 minutes")
    assert stored_settings() == before


@pytest.mark.parametrize("minutes", [5, 240])
def test_slot_length_at_the_limits_is_accepted(admin, minutes):
    response = admin.post("/admin/schedule-settings", json={**VALID, "slot_duration_minutes": minutes})

    assert response.status_code == 200, response.text
    assert stored_settings()["slot_duration_minutes"] == minutes


@pytest.mark.parametrize("capacity", [0, -5, 21])
def test_capacity_outside_1_to_20_is_refused_and_nothing_changes(admin, capacity):
    before = stored_settings()

    response = admin.post("/admin/schedule-settings", json={**VALID, "capacity_per_slot": capacity})

    assert_refused(response, "capacity must be between 1 and 20")
    assert stored_settings() == before


@pytest.mark.parametrize("capacity", [1, 20])
def test_capacity_at_the_limits_is_accepted(admin, capacity):
    response = admin.post("/admin/schedule-settings", json={**VALID, "capacity_per_slot": capacity})

    assert response.status_code == 200, response.text
    assert stored_settings()["capacity_per_slot"] == capacity


@pytest.mark.parametrize(
    "open_time, close_time, minutes, expected_text",
    [
        ("18:00", "09:00", 30, "opening time must be before closing time"),
        ("09:00", "09:00", 30, "opening time must be before closing time"),
        ("09:00", "09:20", 30, "at least one slot must fit"),
        ("09:00", "12:59", 240, "at least one slot must fit"),
    ],
    ids=["close-before-open", "equal", "window-shorter-than-slot", "window-one-minute-short"],
)
def test_opening_hours_that_hold_no_slot_are_refused(admin, open_time, close_time, minutes, expected_text):
    before = stored_settings()

    response = admin.post(
        "/admin/schedule-settings",
        json={**VALID, "open_time": open_time, "close_time": close_time, "slot_duration_minutes": minutes},
    )

    assert_refused(response, expected_text)
    assert stored_settings() == before


def test_a_window_of_exactly_one_slot_is_accepted(admin):
    response = admin.post(
        "/admin/schedule-settings", json={**VALID, "open_time": "09:00", "close_time": "09:30"}
    )

    assert response.status_code == 200, response.text
    slots = admin.get(f"/queue/available-times?for_date={TOMORROW}").json()
    assert [slot["time"] for slot in slots] == ["09:00:00"]


@pytest.mark.parametrize(
    "field, value",
    [
        ("slot_duration_minutes", "abc"),
        ("slot_duration_minutes", None),
        ("slot_duration_minutes", 7.5),
        ("slot_duration_minutes", True),
        ("capacity_per_slot", "many"),
        ("capacity_per_slot", None),
        ("capacity_per_slot", 1.5),
        ("capacity_per_slot", True),
        ("open_time", "09:00:30"),
        ("close_time", "18:00:00.5"),
        ("open_time", "09:00+01:00"),
        ("open_time", "25:00"),
        ("close_time", None),
    ],
)
def test_values_of_the_wrong_kind_are_refused_and_nothing_changes(admin, field, value):
    before = stored_settings()

    response = admin.post("/admin/schedule-settings", json={**VALID, field: value})

    assert response.status_code == 422, response.text
    assert all(isinstance(item["msg"], str) and item["msg"] for item in response.json()["detail"])
    assert stored_settings() == before


def test_a_refused_change_leaves_availability_working(admin):
    refused = admin.post("/admin/schedule-settings", json={**VALID, "slot_duration_minutes": 0})
    # Checked first: if the value were stored, the next request is the one that would hang.
    assert refused.status_code == 422

    started = clock.perf_counter()
    response = admin.get(f"/queue/available-times?for_date={TOMORROW}")

    assert response.status_code == 200
    assert len(response.json()) == 18
    assert clock.perf_counter() - started < 1


def test_a_booking_is_not_blocked_while_a_settings_change_is_refused(admin):
    public = TestClient(app)
    # Checked first: if the value were stored, the bookings below would hang.
    assert admin.post("/admin/schedule-settings", json={**VALID, "slot_duration_minutes": 0}).status_code == 422

    def refuse_settings(_index):
        return admin.post("/admin/schedule-settings", json={**VALID, "slot_duration_minutes": 0}).status_code

    def book(index):
        return public.post(
            "/queue/checkin",
            json={
                "name": "Test Customer",
                "phone": f"+4179000{index:04d}",
                "appointment_date": TOMORROW,
                "appointment_time": f"{9 + index:02d}:00",
            },
        ).status_code

    started = clock.perf_counter()
    with ThreadPoolExecutor(max_workers=8) as pool:
        refusals = [pool.submit(refuse_settings, index) for index in range(4)]
        bookings = [pool.submit(book, index) for index in range(4)]
        refusal_codes = [future.result(timeout=10) for future in refusals]
        booking_codes = [future.result(timeout=10) for future in bookings]

    assert refusal_codes == [422] * 4
    assert booking_codes == [200] * 4
    assert clock.perf_counter() - started < 1


# --- stored invalid values: the booking service must not hang ---------------------

_CHILD = r"""
import json, logging, sys, time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.assistant_main import app as assistant_app
from app.database import SessionLocal, engine
from app.main import app as main_app
from app.services.booking import get_or_create_shop_status

stored = json.loads(sys.argv[1])
with SessionLocal() as db:
    get_or_create_shop_status(db)
# A raw UPDATE, as a manual edit or an old database would leave it.
with engine.begin() as connection:
    for column, value in stored.items():
        connection.execute(text(f"UPDATE shop_status SET {column} = :value"), {"value": value})

site = TestClient(main_app)
assistant = TestClient(assistant_app)
day = (date.today() + timedelta(days=2)).isoformat()
NAME = "Zebulon Quartzwhistle"
PHONE = "+41795550142"


def timed(call):
    started = time.perf_counter()
    response = call()
    return {"seconds": time.perf_counter() - started, "status": response.status_code, "body": response.json()}


def availability():
    return site.get(f"/queue/available-times?for_date={day}")


def checkin():
    return site.post("/queue/checkin", json={
        "name": NAME, "phone": PHONE, "appointment_date": day, "appointment_time": "10:00",
    })


def assistant_availability():
    return assistant.post("/assistant/availability", headers={"X-API-Key": "child-key"}, json={
        "date": day, "service_id": "classic-cut",
    })


def assistant_booking():
    return assistant.post("/assistant/bookings", headers={"X-API-Key": "child-key"}, json={
        "name": NAME, "phone": PHONE, "date": day, "time": "10:00", "service_id": "classic-cut",
    })


results = {
    "availability": timed(availability),
    "checkin": timed(checkin),
    "assistant_availability": timed(assistant_availability),
    "assistant_booking": timed(assistant_booking),
}
with ThreadPoolExecutor(max_workers=2) as pool:
    first = pool.submit(timed, checkin)
    second = pool.submit(timed, checkin)
    results["concurrent"] = [first.result(), second.result()]
results["availability_again"] = timed(availability)
print("RESULTS=" + json.dumps(results))
"""

STORED_INVALID = {
    "slot-zero": {"slot_duration_minutes": 0},
    "slot-negative": {"slot_duration_minutes": -10},
    "slot-one-minute": {"slot_duration_minutes": 1},
    "capacity-zero": {"capacity_per_slot": 0},
    "capacity-negative": {"capacity_per_slot": -5},
    "close-before-open": {"open_time": "18:00:00.000000", "close_time": "09:00:00.000000"},
    "close-equals-open": {"open_time": "09:00:00.000000", "close_time": "09:00:00.000000"},
}


def run_child(tmp_path, stored: dict) -> tuple[dict, str]:
    environment = {
        **os.environ,
        "DATABASE_URL": f"sqlite:///{tmp_path / 'stored-invalid.db'}",
        "ASSISTANT_API_KEY": "child-key",
        "EMAIL_DRY_RUN": "true",
        "PYTHONPATH": str(REPO_ROOT),
    }
    try:
        child = subprocess.run(
            [sys.executable, "-c", _CHILD, json.dumps(stored)],
            cwd=REPO_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(f"the app hung with stored settings {stored}")
    assert child.returncode == 0, child.stderr[-2000:]
    line = next(line for line in child.stdout.splitlines() if line.startswith("RESULTS="))
    return json.loads(line.removeprefix("RESULTS=")), child.stderr


@pytest.mark.parametrize("stored", STORED_INVALID.values(), ids=STORED_INVALID.keys())
def test_stored_invalid_settings_close_the_day_instead_of_hanging(tmp_path, stored):
    results, log = run_child(tmp_path, stored)

    every_call = [
        results["availability"],
        results["checkin"],
        results["assistant_availability"],
        results["assistant_booking"],
        results["availability_again"],
        *results["concurrent"],
    ]
    assert all(call["seconds"] < 1 for call in every_call), every_call

    # The day is closed: nothing to offer, and booking is refused cleanly.
    assert results["availability"]["status"] == 200
    assert results["availability"]["body"] == []
    assert results["availability_again"]["body"] == []

    for call in [results["checkin"], *results["concurrent"]]:
        assert call["status"] == 400
        assert call["body"] == {"detail": "Booking is not available at the moment"}

    assert results["assistant_availability"]["body"]["ok"] is False
    assert results["assistant_availability"]["body"]["code"] == "shop_closed"
    assert results["assistant_booking"]["body"]["ok"] is False
    assert results["assistant_booking"]["body"]["code"] == "booking_closed"

    # One warning for the whole process, and it names no customer.
    warnings = [line for line in log.splitlines() if "invalid schedule settings" in line]
    assert len(warnings) == 1, log
    assert "Zebulon" not in log and "Quartzwhistle" not in log
    assert "5550142" not in log
    assert "Traceback" not in log


def test_valid_stored_settings_log_no_warning(tmp_path):
    results, log = run_child(tmp_path, {"slot_duration_minutes": 30})

    assert results["checkin"]["status"] == 200
    assert len(results["availability"]["body"]) == 18
    assert "invalid schedule settings" not in log


def test_the_slot_generator_is_capped():
    from app.services import booking

    with SessionLocal() as db:
        status = get_or_create_shop_status(db)
        status.open_time, status.close_time = time(0, 0), time(23, 59)
        status.slot_duration_minutes = 5
        assert len(booking._base_slots(status, date.fromisoformat(TOMORROW))) == 288
        assert 288 <= booking.MAX_SLOTS_PER_DAY

        status.slot_duration_minutes = 4
        assert booking._base_slots(status, date.fromisoformat(TOMORROW)) == set()
        db.rollback()


@pytest.mark.parametrize("minutes", [0, -10])
def test_the_calendar_feed_never_ends_an_event_before_it_starts(minutes):
    from sqlalchemy import text

    from app.database import engine
    from app.models import Customer, QueueEntry
    from app.services.calendar_feed import build_feed

    with SessionLocal() as db:
        get_or_create_shop_status(db)
        customer = Customer(name="Test Customer", phone="+41790000009")
        db.add(customer)
        db.flush()
        db.add(
            QueueEntry(
                customer_id=customer.id,
                queue_date=date.fromisoformat(TOMORROW),
                position=1,
                appointment_time=time(10, 0),
            )
        )
        db.commit()
    with engine.begin() as connection:
        connection.execute(text("UPDATE shop_status SET slot_duration_minutes = :m"), {"m": minutes})

    with SessionLocal() as db:
        feed = build_feed(db)

    assert "DTSTART:20261202T090000Z" in feed
    assert "DTEND:20261202T093000Z" in feed


# --- open days ------------------------------------------------------------------


def test_open_days_cannot_be_empty(admin):
    before = stored_settings()

    response = admin.post("/admin/open-days", json={"open_days": []})

    assert_refused(response, "choose at least one open day")
    assert stored_settings() == before


def test_open_days_are_stored_once_each_in_week_order(admin):
    response = admin.post("/admin/open-days", json={"open_days": ["sat", "mon", "sat", "tue", "mon"]})

    assert response.status_code == 200, response.text
    assert response.json()["open_days"] == ["mon", "tue", "sat"]
    assert stored_settings()["open_days"] == "mon,tue,sat"


@pytest.mark.parametrize("days", [["funday"], ["mon", ""], ["MON"], [1], "mon", None, ["mon"] * 500 + ["x"]])
def test_unknown_open_days_are_refused_and_nothing_changes(admin, days):
    before = stored_settings()

    response = admin.post("/admin/open-days", json={"open_days": days})

    assert response.status_code == 422, response.text
    assert stored_settings() == before


# --- blocked and included slots ---------------------------------------------------


def slot_rows(model) -> int:
    with SessionLocal() as db:
        return db.query(model).count()


@pytest.mark.parametrize("path, model", [("blocked-slots", BlockedSlot), ("included-slots", IncludedSlot)])
@pytest.mark.parametrize(
    "day, expected_text",
    [
        ("2026-11-30", "past"),
        ("0001-01-01", "past"),
        ("2027-12-03", "more than"),
        ("2999-01-01", "more than"),
    ],
)
def test_slots_cannot_be_changed_on_absurd_dates(admin, path, model, day, expected_text):
    response = admin.post(f"/admin/{path}", json={"date": day, "time": "10:00"})

    assert_refused(response, expected_text, status_code=400)
    assert slot_rows(model) == 0


@pytest.mark.parametrize("path, model", [("blocked-slots", BlockedSlot), ("included-slots", IncludedSlot)])
@pytest.mark.parametrize("slot", ["10:00:30", "10:00:00.25", "10:00+01:00", "24:00", "", None])
def test_slot_times_must_be_whole_local_minutes(admin, path, model, slot):
    response = admin.post(f"/admin/{path}", json={"date": TOMORROW, "time": slot})

    assert response.status_code == 422, response.text
    assert slot_rows(model) == 0


@pytest.mark.parametrize("slot", ["10:07", "08:30", "18:00", "23:59"])
def test_only_one_of_the_days_slots_can_be_blocked(admin, slot):
    response = admin.post("/admin/blocked-slots", json={"date": TOMORROW, "time": slot})

    assert_refused(response, "not one of that day's appointment times", status_code=400)
    assert slot_rows(BlockedSlot) == 0


def test_a_closed_day_has_no_slot_to_block(admin):
    admin.post("/admin/open-days", json={"open_days": ["mon"]})

    response = admin.post("/admin/blocked-slots", json={"date": TOMORROW, "time": "10:00"})

    assert_refused(response, "not one of that day's appointment times", status_code=400)


def test_blocking_twice_keeps_one_row(admin):
    first = admin.post("/admin/blocked-slots", json={"date": TOMORROW, "time": "10:00"})
    second = admin.post("/admin/blocked-slots", json={"date": TOMORROW, "time": "10:00:00"})

    assert first.status_code == second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert slot_rows(BlockedSlot) == 1


def test_an_added_time_can_be_blocked(admin):
    assert admin.post("/admin/included-slots", json={"date": TOMORROW, "time": "19:15"}).status_code == 200

    response = admin.post("/admin/blocked-slots", json={"date": TOMORROW, "time": "19:15"})

    assert response.status_code == 200, response.text


def test_including_twice_keeps_one_row(admin):
    first = admin.post("/admin/included-slots", json={"date": TEST_TODAY, "time": "19:15"})
    second = admin.post("/admin/included-slots", json={"date": TEST_TODAY, "time": "19:15"})

    assert first.status_code == second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert slot_rows(IncludedSlot) == 1


def test_added_times_per_day_are_capped(admin):
    from app.routers.admin import MAX_INCLUDED_SLOTS_PER_DAY

    for index in range(MAX_INCLUDED_SLOTS_PER_DAY):
        response = admin.post(
            "/admin/included-slots", json={"date": TOMORROW, "time": f"{18 + index // 60}:{index % 60:02d}"}
        )
        assert response.status_code == 200, response.text

    response = admin.post("/admin/included-slots", json={"date": TOMORROW, "time": "22:00"})

    assert_refused(response, "no more than", status_code=400)
    assert slot_rows(IncludedSlot) == MAX_INCLUDED_SLOTS_PER_DAY
    # The cap is per day, and the day stays quick to read.
    assert admin.post("/admin/included-slots", json={"date": TEST_TODAY, "time": "22:00"}).status_code == 200
    started = clock.perf_counter()
    assert admin.get(f"/queue/available-times?for_date={TOMORROW}").status_code == 200
    assert clock.perf_counter() - started < 1


@pytest.mark.parametrize(
    "path, model, date_column, time_column",
    [
        ("blocked-slots", BlockedSlot, "blocked_date", "blocked_time"),
        ("included-slots", IncludedSlot, "included_date", "included_time"),
    ],
)
def test_rows_stored_before_these_rules_can_still_be_removed(admin, path, model, date_column, time_column):
    with SessionLocal() as db:
        db.add(model(**{date_column: date(2999, 1, 1), time_column: time(10, 7)}))
        db.add(model(**{date_column: date(2020, 1, 1), time_column: time(10, 0)}))
        db.commit()

    far = admin.request("DELETE", f"/admin/{path}", json={"date": "2999-01-01", "time": "10:07"})
    old = admin.request("DELETE", f"/admin/{path}", json={"date": "2020-01-01", "time": "10:00"})

    assert far.status_code == old.status_code == 200
    assert slot_rows(model) == 0
