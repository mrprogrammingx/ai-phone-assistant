"""A cancelled appointment has given up its slot, so staff actions cannot revive it."""
import pytest
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models import QueueEntry, QueueStatus

DAY = "2026-12-02"
SLOT = "14:00:00"

public = TestClient(app)


@pytest.fixture
def staff():
    client = TestClient(app)
    response = client.post("/api/login", json={"username": "admin", "password": "test-password-123"})
    assert response.status_code == 200
    return client


def book(phone="+41790000001"):
    response = public.post(
        "/queue/checkin",
        json={"name": "Test Customer", "phone": phone, "appointment_date": DAY, "appointment_time": SLOT},
    )
    assert response.status_code == 200
    return response.json()["id"]


def cancel(entry_id):
    with SessionLocal() as db:
        db.get(QueueEntry, entry_id).status = QueueStatus.cancelled
        db.commit()


def status_of(entry_id):
    with SessionLocal() as db:
        return db.get(QueueEntry, entry_id).status


def slot_is_available():
    slots = public.get("/queue/available-times", params={"for_date": DAY}).json()
    return next(slot for slot in slots if slot["time"] == SLOT)["available"]


@pytest.mark.parametrize("action", ["done", "no-show"])
def test_cancelled_appointment_cannot_be_changed(staff, action):
    entry_id = book()
    cancel(entry_id)

    response = staff.post(f"/queue/{entry_id}/{action}")

    assert response.status_code == 409
    assert status_of(entry_id) == QueueStatus.cancelled
    assert slot_is_available() is True


def test_cancelled_appointment_cannot_retake_a_rebooked_slot(staff):
    cancelled_id = book()
    cancel(cancelled_id)
    rebooked_id = book(phone="+41790000002")

    assert staff.post(f"/queue/{cancelled_id}/done").status_code == 409

    with SessionLocal() as db:
        assert db.get(QueueEntry, rebooked_id).status == QueueStatus.waiting
        active = db.query(QueueEntry).filter(QueueEntry.status != QueueStatus.cancelled).count()
    assert active == 1


@pytest.mark.parametrize("action, expected", [("done", "done"), ("no-show", "no_show")])
def test_active_appointment_can_still_be_changed(staff, action, expected):
    entry_id = book()

    response = staff.post(f"/queue/{entry_id}/{action}")

    assert response.status_code == 200
    assert response.json()["status"] == expected
