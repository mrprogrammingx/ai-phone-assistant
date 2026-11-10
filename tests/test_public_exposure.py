"""What an anonymous visitor can read, compared with what staff can read."""
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.services.booking import get_or_create_shop_status

REPO_ROOT = Path(__file__).resolve().parents[1]
TOMORROW = "2026-12-02"
NAME = "Zebulon Quartzwhistle"
NOTE = "Allergic to lavender oil"
PHONE = "+41795550142"
STAFF_EMAILS = "owner-private@example.com,second-private@example.com"

PUBLIC_SHOP_FIELDS = {"booking_open", "open_days", "open_time", "close_time", "slot_duration_minutes", "capacity_per_slot"}
PUBLIC_BOOKING_FIELDS = {"id", "queue_date", "appointment_time", "status", "position", "created_at"}

anonymous = TestClient(app)


@pytest.fixture
def staff():
    client = TestClient(app)
    assert client.post("/api/login", json={"username": "admin", "password": "test-password-123"}).status_code == 200
    return client


@pytest.fixture(autouse=True)
def shop_with_private_details():
    with SessionLocal() as db:
        get_or_create_shop_status(db).notification_emails = STAFF_EMAILS
        db.commit()
    booked = anonymous.post(
        "/queue/checkin",
        json={"name": NAME, "phone": PHONE, "note": NOTE, "appointment_date": TOMORROW, "appointment_time": "10:00"},
    )
    assert booked.status_code == 200


# --- shop status ------------------------------------------------------------------


def test_an_anonymous_visitor_sees_opening_hours_and_nothing_staff_only():
    response = anonymous.get("/admin/shop-status")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == PUBLIC_SHOP_FIELDS
    assert body["open_days"] == ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    assert (body["open_time"], body["close_time"]) == ("09:00:00", "18:00:00")
    assert (body["slot_duration_minutes"], body["capacity_per_slot"]) == (30, 1)
    assert body["booking_open"] is True
    assert "example.com" not in response.text and "notification" not in response.text


def test_a_fresh_anonymous_client_does_not_see_the_notification_emails():
    body = TestClient(app)  # a fresh anonymous client must not inherit anything
    assert "notification_emails" not in body.get("/admin/shop-status").json()


def test_logged_in_staff_get_the_full_settings(staff):
    body = staff.get("/admin/shop-status").json()

    assert set(body) == PUBLIC_SHOP_FIELDS | {"notification_emails"}
    assert body["notification_emails"] == STAFF_EMAILS.split(",")


def test_logging_out_hides_the_staff_fields_again(staff):
    assert "notification_emails" in staff.get("/admin/shop-status").json()

    staff.post("/api/logout")

    assert set(staff.get("/admin/shop-status").json()) == PUBLIC_SHOP_FIELDS


@pytest.mark.parametrize("cookie", ["", "is_admin=true", "phone_assistant_session=forged", "phone_assistant_session=eyJpc19hZG1pbiI6IHRydWV9"])
def test_a_forged_cookie_does_not_reveal_the_staff_fields(cookie):
    response = TestClient(app).get("/admin/shop-status", headers={"Cookie": cookie})

    assert response.status_code == 200
    assert set(response.json()) == PUBLIC_SHOP_FIELDS


def test_staff_settings_changes_still_return_the_full_settings(staff):
    response = staff.post("/admin/shop-status", json={"booking_open": True})

    assert set(response.json()) == PUBLIC_SHOP_FIELDS | {"notification_emails"}


def test_the_website_reads_only_public_fields_from_shop_status():
    """OpeningHours.jsx and the old templates must not depend on a staff-only field."""
    # Each file and the variable it keeps the shop status in.
    sources = {
        REPO_ROOT / "frontend" / "src" / "components" / "OpeningHours.jsx": "status",
        REPO_ROOT / "app" / "templates" / "base.html": "data",
        REPO_ROOT / "app" / "templates" / "checkin.html": "status",
    }
    for source, variable in sources.items():
        used = set(re.findall(rf"(?<![.\w]){variable}\.([a-z_]+)", source.read_text(encoding="utf-8")))
        used -= {"includes", "length", "map", "filter", "join", "slice"}
        assert used, source.name
        assert used <= PUBLIC_SHOP_FIELDS, (source.name, used - PUBLIC_SHOP_FIELDS)


# --- my bookings ------------------------------------------------------------------


@pytest.mark.parametrize("as_staff", [False, True], ids=["anonymous", "staff"])
def test_my_bookings_shows_when_and_never_who(as_staff, staff):
    client = staff if as_staff else anonymous

    response = client.get("/queue/my-bookings", params={"phone": PHONE})

    assert response.status_code == 200
    (entry,) = response.json()
    assert set(entry) == PUBLIC_BOOKING_FIELDS
    assert (entry["queue_date"], entry["appointment_time"], entry["status"]) == (TOMORROW, "10:00:00", "waiting")
    for private in ("Zebulon", "Quartzwhistle", "lavender", "customer", "note", "name", "5550142"):
        assert private not in response.text


def test_staff_still_see_the_name_and_note_on_the_staff_routes(staff):
    (entry,) = staff.get(f"/queue?for_date={TOMORROW}").json()

    assert entry["customer"]["name"] == NAME
    assert entry["note"] == NOTE
    assert anonymous.get(f"/queue?for_date={TOMORROW}").status_code == 401


def test_the_lookup_page_uses_only_what_my_bookings_returns():
    source = (REPO_ROOT / "frontend" / "src" / "sections" / "MyBookingsLookup.jsx").read_text(encoding="utf-8")
    template = (REPO_ROOT / "app" / "templates" / "checkin.html").read_text(encoding="utf-8")

    assert set(re.findall(r"\bentry\.([a-z_]+)", source)) <= PUBLIC_BOOKING_FIELDS
    assert set(re.findall(r"\bbooking\.([a-z_]+)", template)) - {"replace", "slice"} <= PUBLIC_BOOKING_FIELDS


@pytest.mark.parametrize(
    "path",
    ["/queue", "/queue/customers", "/queue/history/dates", f"/queue/history/{TOMORROW}", "/queue/1/position",
     f"/admin/blocked-slots?for_date={TOMORROW}", f"/admin/included-slots?for_date={TOMORROW}"],
)
def test_every_other_read_route_needs_a_login(path, staff):
    refused = anonymous.get(path)

    assert refused.status_code == 401
    assert refused.json() == {"detail": "Admin login required"}
    assert "Quartzwhistle" not in refused.text
    assert staff.get(path).status_code == 200


def test_public_pages_never_contain_customer_details():
    for path in ("/queue/available-times", f"/queue/available-times?for_date={TOMORROW}", "/admin/shop-status",
                 "/api/me", "/health", "/checkin"):
        response = anonymous.get(path)
        assert response.status_code == 200, path
        for private in ("Zebulon", "Quartzwhistle", "lavender", "5550142", "example.com"):
            assert private not in response.text, (path, private)
