"""A website booking needs a real name, not an empty or space-only one."""
import pytest
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models import Customer, QueueEntry

client = TestClient(app)

DAY = "2026-12-02"
SLOT = "10:00:00"


def book(name):
    return client.post(
        "/queue/checkin",
        json={"name": name, "phone": "+41790000001", "appointment_date": DAY, "appointment_time": SLOT},
    )


@pytest.mark.parametrize("name", ["", " ", "     ", "\t", " \n "])
def test_blank_name_is_refused_and_books_nothing(name):
    response = book(name)

    assert response.status_code == 422
    assert response.json()["detail"][0]["msg"] == "Value error, Enter your name"
    with SessionLocal() as db:
        assert db.query(QueueEntry).count() == 0
        assert db.query(Customer).count() == 0
    slots = client.get("/queue/available-times", params={"for_date": DAY}).json()
    assert next(slot for slot in slots if slot["time"] == SLOT)["available"]


def test_surrounding_spaces_are_removed_from_a_real_name():
    response = book("  Greta Fiction  ")

    assert response.status_code == 200
    assert response.json()["customer"]["name"] == "Greta Fiction"
