"""A No Show gives up its slot, so reviving it must not double-book that slot."""
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.assistant_main import app as assistant_app
from app.database import SessionLocal
from app.main import app
from app.models import QueueEntry, QueueStatus
from app.services.booking import get_or_create_shop_status

API_KEY = "test-assistant-key"
DAY = "2026-12-02"

web = TestClient(app)
assistant = TestClient(assistant_app)


@pytest.fixture(autouse=True)
def assistant_api_key(monkeypatch):
    monkeypatch.setenv("ASSISTANT_API_KEY", API_KEY)


@pytest.fixture
def staff():
    client = TestClient(app)
    response = client.post("/api/login", json={"username": "admin", "password": "test-password-123"})
    assert response.status_code == 200
    return client


def web_book(slot, phone="+41790000001", day=DAY):
    return web.post(
        "/queue/checkin",
        json={"name": "Web Customer", "phone": phone, "appointment_date": day, "appointment_time": f"{slot}:00"},
    )


def phone_book(slot, phone="+41790000001", service="classic-cut"):
    response = assistant.post(
        "/assistant/bookings",
        headers={"X-API-Key": API_KEY},
        json={"name": "Phone Customer", "phone": phone, "date": DAY, "time": slot, "service_id": service},
    )
    assert response.json()["ok"] is True
    return response.json()["booking"]["id"]


def no_show(staff, entry_id):
    assert staff.post(f"/queue/{entry_id}/no-show").status_code == 200


def status_of(entry_id):
    with SessionLocal() as db:
        return db.get(QueueEntry, entry_id).status


def set_capacity(capacity):
    with SessionLocal() as db:
        get_or_create_shop_status(db).capacity_per_slot = capacity
        db.commit()


def assert_revival_refused(staff, entry_id):
    response = staff.post(f"/queue/{entry_id}/done")

    assert response.status_code == 409
    assert "booked" in response.json()["detail"]
    assert status_of(entry_id) == QueueStatus.no_show


def test_no_show_can_be_corrected_when_the_slot_is_free(staff):
    entry_id = web_book("14:00").json()["id"]
    no_show(staff, entry_id)

    response = staff.post(f"/queue/{entry_id}/done")

    assert response.status_code == 200
    assert response.json()["status"] == "done"


def test_correction_is_allowed_after_the_appointment_time_has_passed(staff, set_clock):
    set_clock(datetime(2026, 12, 2, 7, 0, tzinfo=timezone.utc))  # 08:00 in Zurich
    entry_id = web_book("09:00").json()["id"]
    no_show(staff, entry_id)

    set_clock(datetime(2026, 12, 2, 9, 0, tzinfo=timezone.utc))  # 10:00 in Zurich

    assert staff.post(f"/queue/{entry_id}/done").status_code == 200


def test_revival_is_refused_when_the_website_rebooked_the_time(staff):
    entry_id = web_book("14:00").json()["id"]
    no_show(staff, entry_id)
    rebooked_id = web_book("14:00", phone="+41790000002").json()["id"]

    assert_revival_refused(staff, entry_id)
    assert status_of(rebooked_id) == QueueStatus.waiting


def test_revival_is_refused_when_the_assistant_rebooked_the_time(staff):
    entry_id = web_book("14:00").json()["id"]
    no_show(staff, entry_id)
    rebooked_id = phone_book("14:00", phone="+41790000002")

    assert_revival_refused(staff, entry_id)
    assert status_of(rebooked_id) == QueueStatus.waiting


def test_long_service_is_refused_if_any_part_of_its_interval_is_taken(staff):
    entry_id = phone_book("10:00", service="cut-beard-combo")
    no_show(staff, entry_id)
    assert web_book("10:30", phone="+41790000002").status_code == 200

    assert_revival_refused(staff, entry_id)


def test_long_service_can_be_revived_when_only_time_after_it_is_taken(staff):
    entry_id = phone_book("10:00", service="cut-beard-combo")
    no_show(staff, entry_id)
    assert web_book("11:00", phone="+41790000002").status_code == 200

    assert staff.post(f"/queue/{entry_id}/done").status_code == 200


def test_capacity_two_allows_revival_below_the_peak(staff):
    set_capacity(2)
    entry_id = web_book("14:00").json()["id"]
    no_show(staff, entry_id)
    assert web_book("14:00", phone="+41790000002").status_code == 200

    assert staff.post(f"/queue/{entry_id}/done").status_code == 200


def test_capacity_two_refuses_revival_at_the_peak(staff):
    set_capacity(2)
    entry_id = web_book("14:00").json()["id"]
    no_show(staff, entry_id)
    assert web_book("14:00", phone="+41790000002").status_code == 200
    assert web_book("14:00", phone="+41790000003").status_code == 200

    assert_revival_refused(staff, entry_id)


@pytest.mark.parametrize("hour", list(range(9, 18)))
def test_revival_and_new_booking_cannot_both_win(staff, hour):
    slot = f"{hour:02d}:00"
    entry_id = web_book(slot).json()["id"]
    no_show(staff, entry_id)
    start_together = threading.Barrier(2)

    def revive():
        start_together.wait()
        return staff.post(f"/queue/{entry_id}/done").status_code

    def book():
        start_together.wait()
        return web_book(slot, phone="+41790000002").status_code

    with ThreadPoolExecutor(2) as pool:
        codes = [future.result() for future in [pool.submit(revive), pool.submit(book)]]

    assert codes.count(200) == 1
    with SessionLocal() as db:
        occupying = (
            db.query(QueueEntry).filter(QueueEntry.status.in_([QueueStatus.done, QueueStatus.waiting])).count()
        )
    assert occupying == 1


def test_cancelled_appointment_is_still_refused(staff):
    entry_id = web_book("14:00").json()["id"]
    with SessionLocal() as db:
        db.get(QueueEntry, entry_id).status = QueueStatus.cancelled
        db.commit()

    assert staff.post(f"/queue/{entry_id}/done").status_code == 409
    assert status_of(entry_id) == QueueStatus.cancelled


def test_active_appointment_moves_to_done_without_a_slot_check(staff):
    entry_id = web_book("14:00").json()["id"]

    assert staff.post(f"/queue/{entry_id}/done").status_code == 200
    assert status_of(entry_id) == QueueStatus.done
