import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event

from app.database import SessionLocal, engine
from app.main import app
from app.models import BlockedSlot, IncludedSlot, QueueEntry, QueueStatus, ShopStatus
from app.services.booking import (
    SLOT_OCCUPYING_STATUSES,
    SLOT_RELEASING_STATUSES,
    BookingRejected,
    ensure_bookable,
    get_or_create_shop_status,
)
from tests.conftest import TEST_TODAY

client = TestClient(app)

TOMORROW = "2026-12-02"
CONCURRENT_REQUESTS = 12


def book(slot, day=TOMORROW, phone="+41790000001", name="Test Customer"):
    return client.post(
        "/queue/checkin",
        json={"name": name, "phone": phone, "appointment_date": day, "appointment_time": slot},
    )


def availability(day=TOMORROW):
    response = client.get("/queue/available-times", params={"for_date": day})
    assert response.status_code == 200
    return {slot["time"]: slot for slot in response.json()}


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


def set_status_of_only_entry(status):
    with SessionLocal() as db:
        db.query(QueueEntry).one().status = status
        db.commit()


def book_concurrently(slot, day=TOMORROW):
    start_together = threading.Barrier(CONCURRENT_REQUESTS)

    def attempt(index):
        start_together.wait()
        return book(slot, day=day, phone=f"+4179{index:07d}", name=f"Caller {index}").status_code

    with ThreadPoolExecutor(CONCURRENT_REQUESTS) as pool:
        return list(pool.map(attempt, range(CONCURRENT_REQUESTS)))


def test_tests_never_use_the_development_database():
    assert "barbershop-tests-" in str(engine.url)


@pytest.mark.parametrize("slot", ["09:00:00", "09:30:00", "10:00:00", "10:30:00", "11:00:00", "11:30:00"])
def test_concurrent_bookings_for_one_slot_have_one_winner(slot):
    codes = book_concurrently(slot)

    assert codes.count(200) == 1
    assert codes.count(400) == CONCURRENT_REQUESTS - 1
    with SessionLocal() as db:
        assert db.query(QueueEntry).count() == 1


def test_concurrent_bookings_respect_a_larger_capacity():
    update_shop(capacity_per_slot=2)

    codes = book_concurrently("14:00:00")

    assert codes.count(200) == 2
    with SessionLocal() as db:
        positions = [row.position for row in db.query(QueueEntry).all()]
    assert sorted(positions) == [1, 2]


def test_second_booking_for_a_full_slot_is_refused():
    assert book("14:00:00").status_code == 200

    response = book("14:00:00", phone="+41790000002")

    assert response.status_code == 400
    assert response.json() == {"detail": "That time is fully booked"}
    assert availability()["14:00:00"]["available"] is False


def test_concurrent_first_requests_create_one_settings_row():
    start_together = threading.Barrier(CONCURRENT_REQUESTS)

    def first_request(_):
        with SessionLocal() as db:
            start_together.wait()
            return get_or_create_shop_status(db).id

    with ThreadPoolExecutor(CONCURRENT_REQUESTS) as pool:
        ids = set(pool.map(first_request, range(CONCURRENT_REQUESTS)))

    assert ids == {1}
    with SessionLocal() as db:
        assert db.query(ShopStatus).count() == 1


def test_admin_settings_use_the_same_single_row():
    assert client.get("/admin/shop-status").status_code == 200
    update_shop(capacity_per_slot=3)

    assert client.get("/admin/shop-status").json()["capacity_per_slot"] == 3
    with SessionLocal() as db:
        assert db.query(ShopStatus).count() == 1


def test_public_check_in_still_updates_the_customer_name():
    book("14:00:00", name="First Name")
    response = book("15:00:00", name="Updated Name")

    assert response.json()["customer"]["name"] == "Updated Name"


def test_slots_earlier_than_now_cannot_be_booked_today():
    response = book("09:30:00", day=TEST_TODAY)

    assert response.status_code == 400
    assert response.json() == {"detail": "That time has already passed"}


def test_slot_starting_exactly_now_cannot_be_booked():
    assert book("10:00:00", day=TEST_TODAY).status_code == 400


def test_later_slot_today_can_be_booked():
    assert book("10:30:00", day=TEST_TODAY).status_code == 200


def test_availability_marks_past_slots_unavailable_today_only():
    today_slots = availability(TEST_TODAY)
    assert today_slots["09:30:00"]["available"] is False
    assert today_slots["10:00:00"]["available"] is False
    assert today_slots["10:30:00"]["available"] is True
    assert availability(TOMORROW)["09:00:00"]["available"] is True


def test_past_date_is_refused():
    assert book("10:00:00", day="2026-11-30").status_code == 400
    response = client.get("/queue/available-times", params={"for_date": "2026-11-30"})
    assert response.status_code == 400
    assert response.json() == {"detail": "Cannot book a date in the past"}


def test_booking_closed_today_is_reflected_in_availability_and_refused():
    update_shop(booking_open=False)

    assert all(not slot["available"] for slot in availability(TEST_TODAY).values())
    assert book("15:00:00", day=TEST_TODAY).json() == {"detail": "Booking is currently closed for today"}
    assert book("15:00:00", day=TOMORROW).status_code == 200


def test_closed_weekday_has_no_slots():
    update_shop(open_days="mon,tue,thu,fri,sat,sun")  # 2026-12-02 is a Wednesday

    assert availability(TOMORROW) == {}
    assert book("10:00:00").json() == {"detail": "Invalid appointment time"}


def test_time_off_the_slot_grid_is_refused():
    assert book("10:17:00").json() == {"detail": "Invalid appointment time"}


def test_blocked_slot_is_unavailable_and_refused():
    add_row(BlockedSlot(blocked_date=date(2026, 12, 2), blocked_time=time(13, 0)))

    assert availability()["13:00:00"]["available"] is False
    assert book("13:00:00").json() == {"detail": "That time is not available"}


@pytest.mark.parametrize("status", [QueueStatus.cancelled, QueueStatus.no_show])
def test_released_statuses_free_the_slot(status):
    assert book("16:00:00").status_code == 200

    set_status_of_only_entry(status)

    assert availability()["16:00:00"]["available"] is True
    assert book("16:00:00", phone="+41790000002").status_code == 200


def test_completed_appointment_keeps_its_slot():
    assert book("16:00:00").status_code == 200

    set_status_of_only_entry(QueueStatus.done)

    assert availability()["16:00:00"]["available"] is False


def test_cancelled_status_is_stored_and_returned():
    assert book("16:00:00").status_code == 200

    set_status_of_only_entry(QueueStatus.cancelled)

    response = client.get("/queue/my-bookings", params={"phone": "+41790000001"})
    assert response.json()[0]["status"] == "cancelled"


@pytest.mark.parametrize("status", list(QueueStatus))
def test_every_status_either_occupies_or_releases_its_slot(status):
    assert (status in SLOT_OCCUPYING_STATUSES) != (status in SLOT_RELEASING_STATUSES)


def test_availability_and_booking_never_send_the_cancelled_status_to_the_database():
    """A Postgres enum that predates `cancelled` would reject the value and fail the query."""
    sent = []

    def capture(_connection, _cursor, statement, parameters, _context, _executemany):
        sent.append(f"{statement} {parameters}")

    event.listen(engine, "before_cursor_execute", capture)
    try:
        availability()
        assert book("14:00:00").status_code == 200
    finally:
        event.remove(engine, "before_cursor_execute", capture)

    everything_sent = " ".join(sent)
    assert "waiting" in everything_sent  # proves the capture saw the status filter
    assert "cancelled" not in everything_sent


# Each clock change is tested from a few weeks before it, so the date is inside the
# booking horizon and a refusal can only come from the daylight-saving rule.
CLOCK_CHANGES = [
    ("2027-03-28", datetime(2027, 3, 1, 9, 0, tzinfo=timezone.utc)),
    ("2027-10-31", datetime(2027, 10, 1, 9, 0, tzinfo=timezone.utc)),
]


def rejection_code(day, slot, minutes=30):
    """The booking rule's own answer. The website shows only the message."""
    with SessionLocal() as db:
        status = get_or_create_shop_status(db)
        try:
            ensure_bookable(db, status, date.fromisoformat(day), slot, minutes)
        except BookingRejected as rejected:
            return rejected.code
        finally:
            db.rollback()
    return None


@pytest.mark.parametrize("day, clock", CLOCK_CHANGES, ids=["skipped-in-spring", "repeated-in-autumn"])
def test_local_times_broken_by_daylight_saving_are_refused(day, clock, set_clock):
    set_clock(clock)
    add_row(IncludedSlot(included_date=date.fromisoformat(day), included_time=time(2, 30)))

    assert availability(day)["02:30:00"]["available"] is False
    assert rejection_code(day, time(2, 30)) == "invalid_time"
    response = book("02:30:00", day=day)
    assert response.status_code == 400
    assert response.json() == {"detail": "That time is not available"}


@pytest.mark.parametrize("day, clock", CLOCK_CHANGES, ids=["spring", "autumn"])
def test_normal_hours_work_on_daylight_saving_days(day, clock, set_clock):
    set_clock(clock)

    assert rejection_code(day, time(9, 0)) is None
    assert availability(day)["09:00:00"]["available"] is True
    assert book("09:00:00", day=day).status_code == 200


def test_public_response_shapes_are_unchanged():
    slot = availability()["14:00:00"]
    assert set(slot) == {"time", "capacity", "booked", "blocked", "included", "available"}

    entry = book("14:00:00").json()
    assert set(entry) == {
        "id", "queue_date", "position", "status", "note", "appointment_time", "created_at", "customer",
    }
    assert set(entry["customer"]) == {"id", "name", "phone"}


def test_rejected_booking_leaves_the_shop_row_unlocked_for_the_next_request():
    assert book("10:17:00").status_code == 400
    assert book("14:00:00").status_code == 200
    with SessionLocal() as db:
        assert db.query(ShopStatus).count() == 1
