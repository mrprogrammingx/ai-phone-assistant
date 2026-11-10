from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import TEST_TODAY as TODAY

client = TestClient(app)


def _first_available_slot():
    res = client.get(f"/queue/available-times?for_date={TODAY}")
    slots = res.json()
    return next(s["time"] for s in slots if s["available"])


def _login_as_admin():
    admin_client = TestClient(app)
    response = admin_client.post(
        "/api/login",
        json={"username": "admin", "password": "test-password-123"},
    )
    assert response.status_code == 200
    return admin_client


def test_checkin_and_position():
    response = client.post(
        "/queue/checkin",
        json={
            "name": "Alice",
            "phone": "+41790000100",
            "appointment_date": TODAY,
            "appointment_time": _first_available_slot(),
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["position"] >= 1
    assert data["status"] == "waiting"
    assert data["customer"]["name"] == "Alice"


def test_call_next_requires_admin():
    response = client.post("/queue/call-next")
    assert response.status_code == 401


def test_call_next_as_admin():
    client.post(
        "/queue/checkin",
        json={
            "name": "Bob",
            "phone": "+41790000101",
            "appointment_date": TODAY,
            "appointment_time": _first_available_slot(),
        },
    )
    admin_client = _login_as_admin()
    response = admin_client.post("/queue/call-next")
    assert response.status_code in (200, 400)


def test_login_rejects_bad_credentials():
    response = client.post(
        "/api/login",
        json={"username": "admin", "password": "wrong"},
    )
    assert response.status_code == 401


def test_me_reports_logged_out_by_default():
    response = client.get("/api/me")
    assert response.status_code == 200
    assert response.json() == {"is_admin": False}


def test_me_reports_logged_in_after_login():
    admin_client = _login_as_admin()
    response = admin_client.get("/api/me")
    assert response.json() == {"is_admin": True}
