import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.assistant_main import app as assistant_app
from app.database import SessionLocal
from app.main import app as main_app
from app.models import Customer, QueueEntry, QueueStatus
from app.routers import assistant as assistant_router
from app.services.booking import InvalidPhone, get_or_create_shop_status, normalize_phone
from tests.conftest import TEST_TODAY

API_KEY = "test-assistant-key"
HEADERS = {"X-API-Key": API_KEY}
TOMORROW = "2026-12-02"
PHONE = "+41791234567"
CONCURRENT_REQUESTS = 12

client = TestClient(assistant_app)


@pytest.fixture(autouse=True)
def assistant_api_key(monkeypatch):
    monkeypatch.setenv("ASSISTANT_API_KEY", API_KEY)


@pytest.fixture
def notifications(monkeypatch):
    sent = []
    monkeypatch.setattr(
        "app.routers.assistant.send_admin_checkin_notification",
        lambda *args, **kwargs: sent.append(args),
    )
    return sent


def tool(path, **body):
    response = client.post(f"/assistant/{path}", json=body, headers=HEADERS)
    assert response.status_code == 200
    return response.json()


def book(**overrides):
    body = {
        "name": "Test Customer",
        "phone": PHONE,
        "date": TOMORROW,
        "time": "14:00",
        "service_id": "classic-cut",
    }
    return tool("bookings", **{**body, **overrides})


def availability(**overrides):
    return tool("availability", **{"date": TOMORROW, "service_id": "classic-cut", **overrides})


def cancel(**overrides):
    return tool("bookings/cancel", **{"phone": PHONE, "date": TOMORROW, "time": "14:00", **overrides})


def assert_failed(result, code):
    assert result["ok"] is False
    assert result["code"] == code
    assert result["message"]


def update_shop(**values):
    with SessionLocal() as db:
        status = get_or_create_shop_status(db)
        for field, value in values.items():
            setattr(status, field, value)
        db.commit()


def booking_count():
    with SessionLocal() as db:
        return db.query(QueueEntry).count()


def set_status_of_only_entry(status):
    with SessionLocal() as db:
        db.query(QueueEntry).one().status = status
        db.commit()


# Access control


@pytest.mark.parametrize(
    "headers",
    [{}, {"X-API-Key": "wrong"}, {"Authorization": "Bearer wrong"}, {"Authorization": f"Basic {API_KEY}"}],
    ids=["missing", "wrong-key", "wrong-bearer", "wrong-scheme"],
)
def test_bad_credentials_are_refused(headers):
    assert client.post("/assistant/info", headers=headers).status_code == 401


def test_both_credential_styles_are_accepted():
    assert client.post("/assistant/info", headers=HEADERS).json()["ok"] is True
    bearer = {"Authorization": f"Bearer {API_KEY}"}
    assert client.post("/assistant/info", headers=bearer).json()["ok"] is True


@pytest.mark.parametrize("configured", [None, ""], ids=["unset", "blank"])
def test_everything_is_refused_when_no_key_is_configured(monkeypatch, configured):
    if configured is None:
        monkeypatch.delenv("ASSISTANT_API_KEY")
    else:
        monkeypatch.setenv("ASSISTANT_API_KEY", configured)

    for headers in ({}, {"X-API-Key": ""}, HEADERS):
        assert client.post("/assistant/info", headers=headers).status_code == 401


def test_credentials_are_checked_before_input():
    assert client.post("/assistant/bookings", json={}).status_code == 401


def test_non_ascii_key_is_refused_without_crashing():
    response = client.post("/assistant/info", headers={"X-API-Key": "clé".encode("latin-1")})
    assert response.status_code == 401


@pytest.mark.parametrize(
    "method, path",
    [
        ("get", "/docs"),
        ("get", "/redoc"),
        ("get", "/openapi.json"),
        ("get", "/health"),
        ("get", "/admin/shop-status"),
        ("post", "/admin/reset-queue"),
        ("get", "/queue/my-bookings?phone=%2B41791234567"),
        ("get", "/queue/available-times"),
        ("post", "/queue/checkin"),
        ("post", "/api/login"),
        ("get", "/"),
    ],
)
def test_nothing_else_is_exposed_on_the_assistant_app(method, path):
    assert getattr(client, method)(path, headers=HEADERS).status_code == 404


def test_assistant_tools_are_not_on_the_main_app():
    response = TestClient(main_app).post("/assistant/info", headers=HEADERS)
    assert response.status_code in (404, 405)


# Business information


def test_info_reports_shop_local_time_and_services():
    info = tool("info")

    assert info["today"] == TEST_TODAY
    assert info["weekday"] == "Tuesday"
    assert info["now_local"] == "2026-12-01T10:00+01:00"
    assert info["timezone"] == "Europe/Zurich"
    assert info["open_time"] == "09:00"
    assert info["close_time"] == "18:00"
    assert len(info["open_days"]) == 7

    services = {service["id"]: service for service in info["services"]}
    assert len(services) == 6
    assert services["classic-cut"] == {
        "id": "classic-cut",
        "name": "Classic Cut",
        "price": 35,
        "currency": "CHF",
        "duration_minutes": 30,
        "phone_bookable": True,
    }
    assert services["signature-fade"]["phone_bookable"] is True


def test_info_does_not_leak_staff_email():
    update_shop(notification_emails="owner@example.com")
    assert "owner@example.com" not in str(tool("info"))


# Availability


def test_availability_offers_at_most_five_slots():
    result = availability()

    assert result["ok"] is True
    assert result["weekday"] == "Wednesday"
    assert result["slots"] == ["09:00", "09:30", "10:00", "10:30", "11:00"]
    assert result["more"] is True


def test_availability_respects_a_time_range():
    result = availability(**{"from": "14:00", "to": "15:00"})
    assert result["slots"] == ["14:00", "14:30", "15:00"]
    assert result["more"] is False


def test_blank_optional_fields_are_ignored():
    assert availability(**{"from": "", "to": ""})["ok"] is True


def test_availability_omits_past_times_today():
    assert availability(date=TEST_TODAY)["slots"][0] == "10:30"


def test_availability_omits_booked_slots():
    book(time="09:00")
    assert availability()["slots"][0] == "09:30"


def test_availability_past_date():
    assert_failed(availability(date="2026-11-30"), "date_past")


def test_availability_closed_weekday():
    update_shop(open_days="mon,tue,thu,fri,sat,sun")
    assert_failed(availability(), "shop_closed")


def test_availability_closed_today():
    update_shop(booking_open=False)
    assert_failed(availability(date=TEST_TODAY), "shop_closed")
    assert availability()["ok"] is True


def test_availability_unknown_service():
    assert_failed(availability(service_id="moustache-wax"), "unknown_service")


def test_availability_service_longer_than_a_slot():
    result = availability(service_id="signature-fade", **{"from": "17:00"})
    assert result["slots"] == ["17:00"]


@pytest.mark.parametrize(
    "overrides",
    [{"date": "02.12.2026"}, {"date": "2026-13-40"}, {"from": "late"}, {"from": "14:00+01:00"}],
)
def test_availability_invalid_input(overrides):
    assert_failed(availability(**overrides), "invalid_input")


# Creating bookings


def test_booking_is_created_and_visible_to_staff_and_website():
    result = book()

    assert result["ok"] is True
    assert result["created"] is True
    booking = result["booking"]
    assert set(booking) == {"id", "date", "weekday", "time", "service"}
    assert (booking["date"], booking["weekday"]) == (TOMORROW, "Wednesday")
    assert (booking["time"], booking["service"]) == ("14:00", "Classic Cut")

    with SessionLocal() as db:
        entry = db.query(QueueEntry).one()
        assert entry.note == "Service: Classic Cut (30 min)"
        assert entry.status == QueueStatus.waiting

    website = TestClient(main_app).get("/queue/available-times", params={"for_date": TOMORROW}).json()
    assert next(slot for slot in website if slot["time"] == "14:00:00")["available"] is False


def test_retry_after_a_lost_response_returns_the_same_booking():
    first = book()
    retry = book()

    assert retry["created"] is False
    assert retry["booking"] == first["booking"]
    assert booking_count() == 1


def test_retry_with_the_number_formatted_differently_is_still_a_duplicate():
    book(phone="079 123 45 67")
    assert book(phone="+41 (0)79 123 45 67")["created"] is False
    assert booking_count() == 1
    with SessionLocal() as db:
        assert db.query(Customer).one().phone == PHONE


def test_retry_succeeds_even_after_the_slot_has_started(set_clock):
    book()
    set_clock(datetime(2026, 12, 2, 13, 30, tzinfo=timezone.utc))  # 14:30 in Zurich
    assert book()["created"] is False


def test_concurrent_retries_of_one_booking_create_one_row(notifications):
    start_together = threading.Barrier(CONCURRENT_REQUESTS)

    def attempt(_):
        start_together.wait()
        return book()

    with ThreadPoolExecutor(CONCURRENT_REQUESTS) as pool:
        results = list(pool.map(attempt, range(CONCURRENT_REQUESTS)))

    assert all(result["ok"] for result in results)
    assert sum(result["created"] for result in results) == 1
    assert len({result["booking"]["id"] for result in results}) == 1
    assert booking_count() == 1
    assert len(notifications) == 1


def test_concurrent_callers_have_one_winner():
    start_together = threading.Barrier(CONCURRENT_REQUESTS)

    def attempt(index):
        start_together.wait()
        return book(phone=f"+4179{index:07d}")

    with ThreadPoolExecutor(CONCURRENT_REQUESTS) as pool:
        results = list(pool.map(attempt, range(CONCURRENT_REQUESTS)))

    assert sum(result["ok"] for result in results) == 1
    assert sum(not result["ok"] and result["code"] == "slot_taken" for result in results) == 11
    assert booking_count() == 1


def test_second_caller_gets_slot_taken():
    book()
    assert_failed(book(phone="+41791234568"), "slot_taken")
    assert booking_count() == 1


def test_existing_customer_is_not_renamed():
    book(name="Original Name")
    book(name="Someone Else", time="15:00")
    with SessionLocal() as db:
        assert db.query(Customer).one().name == "Original Name"


@pytest.mark.parametrize(
    "overrides, code",
    [
        ({"time": "14:17"}, "invalid_time"),
        ({"date": TEST_TODAY, "time": "09:30"}, "invalid_time"),
        ({"date": "2026-11-30"}, "date_past"),
        ({"service_id": "moustache-wax"}, "unknown_service"),
        ({"service_id": "cut-beard-combo", "time": "17:30"}, "invalid_time"),
        ({"phone": "not a number"}, "invalid_phone"),
        ({"phone": "+4179123"}, "invalid_phone"),
        ({"name": "   "}, "invalid_input"),
        ({"date": "tomorrow"}, "invalid_input"),
        ({"time": "2pm"}, "invalid_input"),
    ],
)
def test_booking_failures(overrides, code):
    assert_failed(book(**overrides), code)
    assert booking_count() == 0


def test_booking_closed_today():
    update_shop(booking_open=False)
    assert_failed(book(date=TEST_TODAY), "booking_closed")


def test_missing_field_names_the_field_without_echoing_values():
    secret_name = "Very Private Name"
    result = tool("bookings", name=secret_name, phone=PHONE, date=TOMORROW, time="14:00")

    assert_failed(result, "invalid_input")
    assert "service_id" in result["message"]
    assert secret_name not in str(result)
    assert PHONE not in str(result)


@pytest.mark.parametrize(
    "request_body",
    [{}, {"content": b"{not json"}, {"json": []}, {"json": "hello"}],
    ids=["empty", "malformed", "array", "string"],
)
def test_unreadable_request_gets_a_readable_message(request_body):
    response = client.post("/assistant/bookings", headers=HEADERS, **request_body)

    assert response.status_code == 200
    assert_failed(response.json(), "invalid_input")
    assert "JSON object" in response.json()["message"]


# Finding bookings


def test_find_returns_upcoming_bookings_without_names():
    book(time="15:00", name="Private Name")
    book(time="14:00", name="Private Name")

    result = tool("bookings/find", phone="079 123 45 67")

    assert [booking["time"] for booking in result["bookings"]] == ["14:00", "15:00"]
    assert "Private Name" not in str(result)


def test_find_returns_at_most_three():
    for slot in ("10:00", "11:00", "12:00", "13:00"):
        book(time=slot)
    assert len(tool("bookings/find", phone=PHONE)["bookings"]) == 3


def test_find_can_filter_by_date():
    book(date=TOMORROW)
    book(date="2026-12-03")
    result = tool("bookings/find", phone=PHONE, date="2026-12-03")
    assert [booking["date"] for booking in result["bookings"]] == ["2026-12-03"]


def test_find_excludes_cancelled_and_past_bookings(set_clock):
    book(time="14:00")
    book(time="15:00")
    cancel(time="15:00")
    assert len(tool("bookings/find", phone=PHONE)["bookings"]) == 1

    set_clock(datetime(2026, 12, 2, 13, 30, tzinfo=timezone.utc))  # 14:30 in Zurich
    assert tool("bookings/find", phone=PHONE)["bookings"] == []


def test_find_unknown_number_returns_nothing():
    assert tool("bookings/find", phone="+41790000000") == {"ok": True, "bookings": []}


def test_find_invalid_phone():
    assert_failed(tool("bookings/find", phone="abc"), "invalid_phone")


# Cancelling


def test_cancel_frees_the_slot():
    book()

    assert cancel() == {"ok": True, "cancelled": True, "already_cancelled": False}
    assert "14:00" in availability(**{"from": "14:00", "to": "14:00"})["slots"]
    with SessionLocal() as db:
        assert db.query(QueueEntry).one().status == QueueStatus.cancelled


def test_cancelling_twice_is_safe():
    book()
    cancel()
    assert cancel() == {"ok": True, "cancelled": True, "already_cancelled": True}


def test_caller_changes_mind_book_cancel_rebook():
    book()
    cancel()

    rebooked = book()

    assert rebooked["created"] is True
    assert len(tool("bookings/find", phone=PHONE)["bookings"]) == 1
    assert_failed(book(phone="+41791234568"), "slot_taken")


def test_cancel_only_affects_the_caller_booking():
    book()
    assert_failed(cancel(phone="+41791234568"), "not_found")
    assert_failed(cancel(time="15:00"), "not_found")
    assert tool("bookings/find", phone=PHONE)["bookings"] != []


@pytest.mark.parametrize("status", [QueueStatus.in_progress, QueueStatus.done, QueueStatus.no_show])
def test_appointments_already_under_way_cannot_be_cancelled(status):
    book()
    set_status_of_only_entry(status)
    assert_failed(cancel(), "not_found")


def test_past_appointment_cannot_be_cancelled(set_clock):
    book()
    set_clock(datetime(2026, 12, 2, 13, 30, tzinfo=timezone.utc))  # 14:30 in Zurich
    assert_failed(cancel(), "not_found")


def test_cancel_invalid_phone():
    assert_failed(cancel(phone="abc"), "invalid_phone")


# Notifications and logs


def test_staff_are_notified_once_per_booking(notifications):
    book()
    book()
    assert len(notifications) == 1


def test_notification_failure_does_not_affect_the_booking(monkeypatch):
    def fail(*_args, **_kwargs):
        raise RuntimeError("email provider is down")

    monkeypatch.setattr("app.routers.assistant.send_admin_checkin_notification", fail)

    assert book()["ok"] is True
    assert booking_count() == 1

    # Must return normally: an error here would escape into the background task.
    assistant_router._notify_staff("Name", PHONE, 1, "14:00:00", "Phone: Classic Cut", [])


def test_logs_contain_no_personal_data(caplog):
    caplog.set_level(logging.INFO, logger="barbershop.assistant")
    name = "Very Private Name"

    book(name=name)
    book(name=name, phone="+41791234568")  # slot_taken
    tool("bookings/find", phone=PHONE)
    cancel()
    book(name=name, phone="not a number")
    tool("bookings", name=name, phone=PHONE)  # invalid_input

    assert "result=slot_taken" in caplog.text
    assert name not in caplog.text
    for digits in ("791234567", "791234568", "not a number"):
        assert digits not in caplog.text


# Phone numbers


@pytest.mark.parametrize(
    "spoken, expected",
    [
        ("+41791234567", "+41791234567"),
        ("079 123 45 67", "+41791234567"),
        ("0041 79 123 45 67", "+41791234567"),
        ("41791234567", "+41791234567"),
        ("+41 (0)79 123 45 67", "+41791234567"),
        ("079-123.45/67", "+41791234567"),
        ("+351 91 234567", "+35191234567"),
    ],
)
def test_phone_numbers_are_normalised(spoken, expected):
    assert normalize_phone(spoken) == expected


@pytest.mark.parametrize("bad", ["", "abc", "12345", "+4179123456", "+417912345678", "+0123456789", "٠٧٩١٢٣٤٥٦٧"])
def test_invalid_phone_numbers_are_refused(bad):
    with pytest.raises(InvalidPhone):
        normalize_phone(bad)
