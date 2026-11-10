"""What the public booking form accepts: names, notes, phone numbers and dates.

The date horizon lives in the shared booking rule, so it is tested through the
website, the assistant API and the webhook alike.
"""
import hashlib
import hmac
import json
import re
from datetime import date, time, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.assistant_main import app as assistant_app
from app.database import SessionLocal
from app.main import app
from app.models import CallRequest, Customer, QueueEntry, WaitlistEntry
from app.services import booking
from tests.conftest import TEST_TODAY

REPO_ROOT = Path(__file__).resolve().parents[1]
client = TestClient(app)
assistant = TestClient(assistant_app)

API_KEY = "test-assistant-key"
WEBHOOK_SECRET = "test-webhook-secret"
TODAY = date.fromisoformat(TEST_TODAY)
TOMORROW = "2026-12-02"
PHONE = "+41791234567"
PHONE_MESSAGE = "Enter a valid phone number (at least 7 digits)"


@pytest.fixture(autouse=True)
def secrets(monkeypatch):
    monkeypatch.setenv("ASSISTANT_API_KEY", API_KEY)
    monkeypatch.setenv("SUISSELINE_WEBHOOK_SECRET", WEBHOOK_SECRET)


def day(offset: int) -> str:
    return (TODAY + timedelta(days=offset)).isoformat()


def checkin(**overrides):
    body = {
        "name": "Test Customer",
        "phone": PHONE,
        "appointment_date": TOMORROW,
        "appointment_time": "10:00",
    }
    return client.post("/queue/checkin", json={**body, **overrides})


def rows(model) -> int:
    with SessionLocal() as db:
        return db.query(model).count()


def customers() -> list[tuple[str, str]]:
    with SessionLocal() as db:
        return [(customer.name, customer.phone) for customer in db.query(Customer).order_by(Customer.id)]


def messages(response) -> list[str]:
    detail = response.json()["detail"]
    return [detail] if isinstance(detail, str) else [item["msg"] for item in detail]


def assert_nothing_booked() -> None:
    assert rows(QueueEntry) == 0
    assert rows(Customer) == 0


def admin_client() -> TestClient:
    admin = TestClient(app)
    assert admin.post("/api/login", json={"username": "admin", "password": "test-password-123"}).status_code == 200
    return admin


# --- names ----------------------------------------------------------------------

ACCEPTED_NAMES = {
    "one-character": ("A", "A"),
    "exactly-100": ("x" * 100, "x" * 100),
    "100-after-trimming": ("  " + "x" * 100 + "\n", "x" * 100),
    "trimmed": ("  Greta Fiction  ", "Greta Fiction"),
    "accents-and-punctuation": ("Zoë Müller-O'Neil", "Zoë Müller-O'Neil"),
    "chinese": ("李小龍", "李小龍"),
    "cyrillic": ("Иван Петров", "Иван Петров"),
    "emoji-with-text": ("Zoë 😀", "Zoë 😀"),
    "emoji-only": ("😀", "😀"),
    "joined-emoji": ("👨‍👩‍👧", "👨‍👩‍👧"),
    "html-is-stored-as-text": ("<b>Max</b> <script>alert(1)</script>", "<b>Max</b> <script>alert(1)</script>"),
    "inner-spaces-kept": ("Anna  Maria", "Anna  Maria"),
}

REFUSED_NAMES = {
    "empty": ("", "Enter your name"),
    "spaces": ("     ", "Enter your name"),
    "tab-and-newline-only": ("\t\n ", "Enter your name"),
    "invisible-only": ("​​", "Enter your name"),
    "101-characters": ("x" * 101, "at most 100 characters"),
    "5000-characters": ("x" * 5000, "at most 100 characters"),
    "newline": ("Anna\nMaria", "line breaks or control characters"),
    "carriage-return": ("Anna\r\nMaria", "line breaks or control characters"),
    "tab": ("Anna\tMaria", "line breaks or control characters"),
    "null-byte": ("Anna\x00", "line breaks or control characters"),
    "escape-sequence": ("Anna\x1b[31m", "line breaks or control characters"),
    "unicode-line-separator": ("Anna Maria", "line breaks or control characters"),
    "delete-character": ("Anna\x7f", "line breaks or control characters"),
}


@pytest.mark.parametrize("typed, stored", ACCEPTED_NAMES.values(), ids=ACCEPTED_NAMES.keys())
def test_accepted_names(typed, stored):
    response = checkin(name=typed)

    assert response.status_code == 200, response.text
    assert response.json()["customer"]["name"] == stored
    assert customers() == [(stored, PHONE)]


@pytest.mark.parametrize("typed, expected", REFUSED_NAMES.values(), ids=REFUSED_NAMES.keys())
def test_refused_names(typed, expected):
    response = checkin(name=typed)

    assert response.status_code == 422, response.text
    assert any(expected in message for message in messages(response)), messages(response)
    assert response.json()["detail"][0]["loc"] == ["body", "name"]
    assert_nothing_booked()


@pytest.mark.parametrize("value", [None, 123, 4.5, True, [], ["Anna"], {"first": "Anna"}])
def test_a_name_must_be_text(value):
    response = checkin(name=value)

    assert response.status_code == 422
    assert_nothing_booked()


def test_a_refused_name_is_not_echoed_back():
    response = checkin(name="Quartzwhistle\x00" + "y" * 200)

    assert response.status_code == 422
    assert "Quartzwhistle" not in response.text


@pytest.mark.parametrize("name", ["Anna\nMaria", "Anna\x00", "x" * 101, "​"])
@pytest.mark.parametrize("path", ["bookings", "waitlist"])
def test_the_assistant_refuses_the_same_names(path, name):
    body = {"name": name, "phone": PHONE, "date": TOMORROW, "service_id": "classic-cut"}
    if path == "bookings":
        body["time"] = "10:00"
    else:
        body.update({"from": "10:00", "to": "12:00", "consent": True})

    result = assistant.post(f"/assistant/{path}", json=body, headers={"X-API-Key": API_KEY}).json()

    assert result["ok"] is False
    assert result["code"] == "invalid_input"
    assert result["message"] == "Missing or invalid: name."
    assert_nothing_booked()
    assert rows(WaitlistEntry) == 0


# --- notes ----------------------------------------------------------------------

NOTES = {
    "missing": (None, None),
    "empty": ("", None),
    "spaces-only": ("   \n ", None),
    "control-characters-only": ("\x00\x07", None),
    "plain": ("Short on the sides", "Short on the sides"),
    "trimmed": ("  Short on the sides \n", "Short on the sides"),
    "exactly-500": ("n" * 500, "n" * 500),
    "500-after-cleaning": (" \x00" + "n" * 500 + "\x07 ", "n" * 500),
    "control-characters-removed": ("a\x00b\x07c\x1b[0m", "abc[0m"),
    "line-breaks-kept": ("first\nsecond", "first\nsecond"),
    "windows-line-breaks": ("first\r\nsecond\rthird", "first\nsecond\nthird"),
    "tab-becomes-a-space": ("a\tb", "a b"),
    "unicode-and-emoji": ("Bitte kurz ✂️ schön", "Bitte kurz ✂️ schön"),
    "html-is-stored-as-text": ("<img src=x onerror=alert(1)>", "<img src=x onerror=alert(1)>"),
}


@pytest.mark.parametrize("typed, stored", NOTES.values(), ids=NOTES.keys())
def test_accepted_notes(typed, stored):
    response = checkin(note=typed)

    assert response.status_code == 200, response.text
    assert response.json()["note"] == stored
    with SessionLocal() as db:
        assert db.query(QueueEntry).one().note == stored


@pytest.mark.parametrize("length", [501, 20_000])
def test_a_note_over_500_characters_is_refused(length):
    response = checkin(note="n" * length)

    assert response.status_code == 422, response.text
    assert messages(response) == ["Value error, Note must be at most 500 characters"]
    assert response.json()["detail"][0]["loc"] == ["body", "note"]
    assert_nothing_booked()


@pytest.mark.parametrize("value", [123, True, ["note"], {"text": "note"}])
def test_a_note_must_be_text(value):
    assert checkin(note=value).status_code == 422
    assert_nothing_booked()


@pytest.mark.parametrize("typed, stored", NOTES.values(), ids=NOTES.keys())
def test_staff_notes_follow_the_same_rules(typed, stored):
    entry_id = checkin().json()["id"]

    response = admin_client().post(f"/queue/{entry_id}/note", json={"note": typed})

    assert response.status_code == 200, response.text
    assert response.json()["note"] == stored


def test_a_staff_note_over_500_characters_is_refused_and_the_old_note_stays():
    entry_id = checkin(note="keep me").json()["id"]

    response = admin_client().post(f"/queue/{entry_id}/note", json={"note": "n" * 501})

    assert response.status_code == 422
    with SessionLocal() as db:
        assert db.query(QueueEntry).one().note == "keep me"


# --- phone numbers ----------------------------------------------------------------

SAME_SWISS_NUMBER = [
    "+41791234567",
    "+41 79 123 45 67",
    "079 123 45 67",
    "0791234567",
    "079-123-45-67",
    "079/123.45.67",
    "(079) 123 45 67",
    "0041 79 123 45 67",
    "+41 (0)79 123 45 67",
    "41791234567",
    "  079 123 45 67  ",
]

OTHER_COUNTRIES = {
    "+33 6 12 34 56 78": "+33612345678",
    "0033 6 12 34 56 78": "+33612345678",
    "+1 (415) 555-0100": "+14155550100",
    "+49 30 901820": "+4930901820",
}

REFUSED_PHONES = [
    "",
    "   ",
    "abc",
    "555-0100",
    "12345678901234567890",
    "+12345678901234567890",
    "+41 79 123",
    "079 123 45 6",
    "079 123 45 678",
    "+0123456789",
    "++41791234567",
    "079 123 45 67 ext 5",
    "+41 79 123 45 67; DROP TABLE customers",
    "079\n1234567",
    "٠٧٩١٢٣٤٥٦٧",
    "9" * 5000,
]


def test_every_way_of_writing_one_number_is_one_customer():
    slots = [f"{9 + index // 2:02d}:{30 * (index % 2):02d}" for index in range(len(SAME_SWISS_NUMBER))]

    for typed, slot in zip(SAME_SWISS_NUMBER, slots):
        response = checkin(phone=typed, appointment_time=slot)
        assert response.status_code == 200, (typed, response.text)
        assert response.json()["customer"]["phone"] == PHONE

    assert customers() == [("Test Customer", PHONE)]
    assert rows(QueueEntry) == len(SAME_SWISS_NUMBER)


@pytest.mark.parametrize("typed", SAME_SWISS_NUMBER)
def test_my_bookings_finds_the_customer_however_the_number_is_typed(typed):
    assert checkin(phone="079 123 45 67").status_code == 200

    response = client.get("/queue/my-bookings", params={"phone": typed})

    assert response.status_code == 200, response.text
    assert [entry["appointment_time"] for entry in response.json()] == ["10:00:00"]


@pytest.mark.parametrize("typed, stored", OTHER_COUNTRIES.items())
def test_numbers_from_other_countries_are_stored_in_e164(typed, stored):
    response = checkin(phone=typed)

    assert response.status_code == 200, response.text
    assert customers() == [("Test Customer", stored)]
    assert len(client.get("/queue/my-bookings", params={"phone": stored}).json()) == 1


@pytest.mark.parametrize("typed", REFUSED_PHONES)
def test_refused_phone_numbers(typed):
    response = checkin(phone=typed)

    assert response.status_code == 422, response.text
    assert messages(response) == [f"Value error, {PHONE_MESSAGE}"]
    assert response.json()["detail"][0]["loc"] == ["body", "phone"]
    assert_nothing_booked()

    lookup = client.get("/queue/my-bookings", params={"phone": typed})
    assert lookup.status_code == 400
    assert lookup.json() == {"detail": PHONE_MESSAGE}


@pytest.mark.parametrize("value", [None, 791234567, 79.5, True, ["+41791234567"], {"number": PHONE}])
def test_a_phone_number_must_be_text(value):
    assert checkin(phone=value).status_code == 422
    assert_nothing_booked()


def test_a_refused_phone_number_is_not_echoed_back():
    response = checkin(phone="5550142 Quartzwhistle")

    assert response.status_code == 422
    assert "5550142" not in response.text and "Quartzwhistle" not in response.text


def test_the_website_and_the_assistant_share_one_customer():
    assert checkin(phone="079 123 45 67", appointment_time="10:00").status_code == 200

    result = assistant.post(
        "/assistant/bookings",
        headers={"X-API-Key": API_KEY},
        json={"name": "Someone Else", "phone": "+41 79 123 45 67", "date": TOMORROW, "time": "11:00",
              "service_id": "classic-cut"},
    ).json()

    assert result["ok"] is True
    assert customers() == [("Test Customer", PHONE)]


def stored_customer(phone: str, name: str = "Stored Before") -> None:
    """A customer saved before numbers were normalised."""
    with SessionLocal() as db:
        customer = Customer(name=name, phone=phone)
        db.add(customer)
        db.flush()
        db.add(
            QueueEntry(
                customer_id=customer.id,
                queue_date=date.fromisoformat(TOMORROW),
                position=1,
                appointment_time=time(16, 0),
            )
        )
        db.commit()


@pytest.mark.parametrize("stored_as", ["555-0100", "079 123 45 67", "(079) 123 45 67", "0791234567"])
def test_a_customer_stored_before_normalising_is_still_found(stored_as):
    stored_customer(stored_as)

    response = client.get("/queue/my-bookings", params={"phone": stored_as})

    assert response.status_code == 200, response.text
    assert [entry["appointment_time"] for entry in response.json()] == ["16:00:00"]


@pytest.mark.parametrize("stored_as", ["555-0100", "079 123 45 67", "(079) 123 45 67"])
def test_a_customer_stored_before_normalising_can_book_again_as_the_same_customer(stored_as):
    stored_customer(stored_as)

    response = checkin(phone=stored_as, name="Stored Before")

    assert response.status_code == 200, response.text
    assert customers() == [("Stored Before", stored_as)]
    assert len(client.get("/queue/my-bookings", params={"phone": stored_as}).json()) == 2


def test_the_normalised_customer_wins_when_both_spellings_are_stored():
    stored_customer("079 123 45 67", name="Old Spelling")
    assert checkin(phone=PHONE, name="New Spelling", appointment_time="11:00").status_code == 200

    response = checkin(phone="079 123 45 67", name="New Spelling", appointment_time="12:00")

    assert response.status_code == 200
    assert response.json()["customer"]["phone"] == PHONE
    assert customers() == [("Old Spelling", "079 123 45 67"), ("New Spelling", PHONE)]
    # The lookup shows the bookings of both stored spellings.
    found = client.get("/queue/my-bookings", params={"phone": "079 123 45 67"}).json()
    assert sorted(entry["appointment_time"] for entry in found) == ["11:00:00", "12:00:00", "16:00:00"]


def test_an_invalid_number_that_no_customer_has_is_refused():
    stored_customer("555-0100")

    assert client.get("/queue/my-bookings", params={"phone": "555-0199"}).status_code == 400
    assert checkin(phone="555-0199").status_code == 422


# --- dates ----------------------------------------------------------------------


def test_the_horizon_is_ninety_days():
    assert booking.BOOKING_HORIZON_DAYS == 90


@pytest.mark.parametrize("offset", [0, 1, 89, 90])
def test_dates_up_to_the_horizon_can_be_booked(offset):
    response = checkin(appointment_date=day(offset), appointment_time="15:00")

    assert response.status_code == 200, response.text
    assert client.get("/queue/available-times", params={"for_date": day(offset)}).status_code == 200


@pytest.mark.parametrize("target", [day(91), day(92), day(365), "2999-12-31", "9999-12-31"])
def test_dates_past_the_horizon_are_refused(target):
    response = checkin(appointment_date=target)

    assert response.status_code == 400, response.text
    assert response.json() == {"detail": "We only take bookings up to 90 days ahead"}
    assert_nothing_booked()

    slots = client.get("/queue/available-times", params={"for_date": target})
    assert slots.status_code == 400
    assert slots.json() == {"detail": "We only take bookings up to 90 days ahead"}


@pytest.mark.parametrize("target", [day(-1), "2020-01-01", "0001-01-01"])
def test_past_dates_are_refused(target):
    response = checkin(appointment_date=target)

    assert response.status_code == 400, response.text
    assert response.json() == {"detail": "Cannot book a date in the past"}
    assert_nothing_booked()


@pytest.mark.parametrize("value", ["", "tomorrow", "2026-02-30", "02.12.2026", "2026-12-02T10:00:00", None, 20261202, []])
def test_a_date_must_be_a_calendar_date(value):
    assert checkin(appointment_date=value).status_code == 422
    assert_nothing_booked()


@pytest.mark.parametrize("value", ["10:00:30", "10:00+01:00", "25:00", "ten", None, 1000, []])
def test_a_time_must_be_a_whole_local_minute(value):
    assert checkin(appointment_time=value).status_code == 422
    assert_nothing_booked()


def tool(path, **body):
    response = assistant.post(f"/assistant/{path}", json=body, headers={"X-API-Key": API_KEY})
    assert response.status_code == 200
    return response.json()


TOO_FAR = {"ok": False, "code": "date_too_far", "message": "We only take bookings up to 90 days ahead"}


@pytest.mark.parametrize("target", [day(91), "2999-12-31"])
def test_the_assistant_refuses_dates_past_the_horizon(target):
    assert tool("availability", date=target, service_id="classic-cut") == TOO_FAR
    assert (
        tool("bookings", name="Test Customer", phone=PHONE, date=target, time="10:00", service_id="classic-cut")
        == TOO_FAR
    )
    assert (
        tool("waitlist", name="Test Customer", phone=PHONE, service_id="classic-cut", date=target,
             **{"from": "10:00", "to": "12:00"}, consent=True)
        == TOO_FAR
    )
    assert_nothing_booked()
    assert rows(WaitlistEntry) == 0


def test_the_assistant_accepts_the_last_day_of_the_horizon():
    target = day(90)

    assert tool("availability", date=target, service_id="classic-cut")["ok"] is True
    assert tool("bookings", name="Test Customer", phone=PHONE, date=target, time="10:00",
                service_id="classic-cut")["ok"] is True
    assert tool("waitlist", name="Test Customer", phone=PHONE, service_id="classic-cut", date=target,
                **{"from": "10:00", "to": "12:00"}, consent=True)["ok"] is True


def test_waitlist_consent_is_still_checked_before_the_date():
    result = tool("waitlist", name="Test Customer", phone=PHONE, service_id="classic-cut", date=day(91),
                  **{"from": "10:00", "to": "12:00"})

    assert result["code"] == "consent_required"


def webhook(call_id: str, **extracted) -> dict:
    details = {"name": "Test Customer", "phone": PHONE, "service_id": "classic-cut", "date": TOMORROW,
               "time": "14:00", **extracted}
    details = {field: value for field, value in details.items() if value is not None}
    raw = json.dumps({"callId": call_id, "event": "call.completed", "extracted": details}).encode()
    signature = hmac.new(WEBHOOK_SECRET.encode(), raw, hashlib.sha256).hexdigest()
    response = assistant.post(
        "/assistant/webhook/call-completed",
        content=raw,
        headers={"X-Signature": signature, "Content-Type": "application/json"},
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize("target", [day(91), "2999-12-31"])
def test_a_webhook_for_a_date_past_the_horizon_becomes_a_follow_up(target):
    assert webhook("call-far-1", date=target)["status"] == "follow_up"
    # A request with no time is incomplete, and looking for times to suggest must not fail.
    assert webhook("call-far-2", date=target, time=None)["status"] == "follow_up"

    with SessionLocal() as db:
        reasons = [request.reason for request in db.query(CallRequest).order_by(CallRequest.id)]
        suggestions = [request.suggested_times for request in db.query(CallRequest)]
    assert reasons == ["date_too_far", "incomplete"]
    assert suggestions == [None, None]
    assert_nothing_booked()


def test_times_are_never_suggested_past_the_horizon():
    # Nothing is free on the last bookable day, so the search would run into the horizon.
    last_day = day(90)
    with SessionLocal() as db:
        status = booking.get_or_create_shop_status(db)
        status.open_days = "sun"  # day(90) is a Monday
        db.commit()

    assert webhook("call-edge", date=last_day, time=None)["status"] == "follow_up"

    with SessionLocal() as db:
        assert db.query(CallRequest).one().suggested_times is None


# --- the website form keeps the same limits ---------------------------------------


def frontend_constant(name: str) -> int:
    source = (REPO_ROOT / "frontend" / "src" / "lib" / "bookingRules.js").read_text(encoding="utf-8")
    return int(re.search(rf"export const {name} = (\d+);", source).group(1))


def test_the_website_form_uses_the_backend_limits():
    from app import schemas

    assert frontend_constant("BOOKING_HORIZON_DAYS") == booking.BOOKING_HORIZON_DAYS
    assert frontend_constant("MAX_NAME_LENGTH") == schemas.MAX_NAME_LENGTH
    assert frontend_constant("MAX_NOTE_LENGTH") == schemas.MAX_NOTE_LENGTH


def test_the_rehearsal_script_stays_inside_the_horizon():
    import importlib.util

    spec = importlib.util.spec_from_file_location("public_rehearsal", REPO_ROOT / "scripts" / "public_rehearsal.py")
    rehearsal = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rehearsal)

    assert rehearsal.BOOKING_HORIZON_DAYS == booking.BOOKING_HORIZON_DAYS
    # The probe books 14 days plus the run number ahead; the race starts at least 30 days ahead.
    assert 14 + rehearsal.PROBE_RUNS <= booking.BOOKING_HORIZON_DAYS
    assert 30 < booking.BOOKING_HORIZON_DAYS - rehearsal.RACE_ROUNDS


def test_the_website_form_reads_phone_numbers_like_the_backend():
    """Runs the form's own normalizePhone in Node. Skipped where Node is not installed."""
    import shutil
    import subprocess

    from app.phone import InvalidPhone, normalize_phone

    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is not installed")
    samples = [*SAME_SWISS_NUMBER, *OTHER_COUNTRIES, *[phone for phone in REFUSED_PHONES if "\n" not in phone]]
    script = (
        "import { normalizePhone } from './frontend/src/lib/bookingRules.js';"
        "const samples = JSON.parse(process.argv[1]);"
        "console.log(JSON.stringify(samples.map(normalizePhone)));"
    )
    done = subprocess.run(
        [node, "--input-type=module", "-e", script, json.dumps(samples)],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=30,
    )
    assert done.returncode == 0, done.stderr

    def backend(sample):
        try:
            return normalize_phone(sample.strip())
        except InvalidPhone:
            return None

    assert json.loads(done.stdout) == [backend(sample) for sample in samples]


# --- what was typed is never sent back ---------------------------------------------

TYPED = "Quartzwhistle-5550142"

BAD_WEBSITE_REQUESTS = {
    "checkin-bad-name": ("POST", "/queue/checkin", {"name": TYPED + "\x00", "phone": PHONE,
                                                    "appointment_date": TOMORROW, "appointment_time": "10:00"}),
    "checkin-bad-phone": ("POST", "/queue/checkin", {"name": "Test Customer", "phone": TYPED,
                                                     "appointment_date": TOMORROW, "appointment_time": "10:00"}),
    "checkin-bad-date": ("POST", "/queue/checkin", {"name": TYPED, "phone": PHONE,
                                                    "appointment_date": TYPED, "appointment_time": "10:00"}),
    "checkin-long-note": ("POST", "/queue/checkin", {"name": "Test Customer", "phone": PHONE, "note": TYPED * 100,
                                                     "appointment_date": TOMORROW, "appointment_time": "10:00"}),
    "checkin-wrong-kind": ("POST", "/queue/checkin", [TYPED]),
    "login-wrong-kind": ("POST", "/api/login", {"username": [TYPED], "password": {"value": TYPED}}),
}


@pytest.mark.parametrize("method, path, body", BAD_WEBSITE_REQUESTS.values(), ids=BAD_WEBSITE_REQUESTS.keys())
def test_the_website_never_sends_back_what_was_typed(method, path, body):
    response = client.request(method, path, json=body)

    assert response.status_code == 422, response.text
    assert "Quartzwhistle" not in response.text and "5550142" not in response.text
    for item in response.json()["detail"]:
        assert set(item) == {"type", "loc", "msg"}
        assert isinstance(item["msg"], str) and item["msg"]


def test_the_website_does_not_send_back_invalid_json():
    response = client.post("/queue/checkin", content=b'{"name": "' + TYPED.encode() + b'", oops',
                           headers={"Content-Type": "application/json"})

    assert response.status_code == 422
    assert "Quartzwhistle" not in response.text


def test_staff_routes_do_not_send_back_what_was_typed():
    response = admin_client().post(
        "/admin/schedule-settings",
        json={"open_time": TYPED, "close_time": "18:00", "slot_duration_minutes": TYPED, "capacity_per_slot": 1},
    )

    assert response.status_code == 422
    assert "Quartzwhistle" not in response.text


BAD_ASSISTANT_REQUESTS = {
    "bookings-bad-name": ("bookings", {"name": TYPED + "\x00", "phone": PHONE, "date": TOMORROW, "time": "10:00",
                                       "service_id": "classic-cut"}),
    "bookings-bad-phone": ("bookings", {"name": "Test Customer", "phone": TYPED, "date": TOMORROW, "time": "10:00",
                                        "service_id": "classic-cut"}),
    "bookings-bad-date": ("bookings", {"name": TYPED, "phone": PHONE, "date": TYPED, "time": TYPED,
                                       "service_id": "classic-cut"}),
    "bookings-unknown-service": ("bookings", {"name": "Test Customer", "phone": PHONE, "date": TOMORROW,
                                              "time": "10:00", "service_id": TYPED}),
    "bookings-wrong-kind": ("bookings", [TYPED]),
    "availability-bad-date": ("availability", {"date": TYPED, "service_id": "classic-cut"}),
    "find-bad-phone": ("bookings/find", {"phone": TYPED}),
    "cancel-bad-time": ("bookings/cancel", {"phone": TYPED, "date": TOMORROW, "time": TYPED}),
    "waitlist-bad-range": ("waitlist", {"name": TYPED, "phone": PHONE, "service_id": "classic-cut", "date": TOMORROW,
                                        "from": TYPED, "to": "12:00", "consent": True}),
    "accept-bad-phone": ("waitlist/accept", {"phone": TYPED, "date": TOMORROW, "time": "10:00"}),
}


@pytest.mark.parametrize("path, body", BAD_ASSISTANT_REQUESTS.values(), ids=BAD_ASSISTANT_REQUESTS.keys())
def test_the_assistant_never_sends_back_or_logs_what_was_typed(path, body, caplog):
    import logging

    with caplog.at_level(logging.DEBUG):
        response = assistant.post(f"/assistant/{path}", json=body, headers={"X-API-Key": API_KEY})

    assert response.status_code == 200, response.text
    result = response.json()
    assert result["ok"] is False
    assert set(result) == {"ok", "code", "message"}
    assert "Quartzwhistle" not in response.text and "5550142" not in response.text
    assert "Quartzwhistle" not in caplog.text and "5550142" not in caplog.text


def test_the_assistant_does_not_send_back_invalid_json():
    response = assistant.post(
        "/assistant/bookings",
        content=b'{"name": "' + TYPED.encode() + b'", oops',
        headers={"X-API-Key": API_KEY, "Content-Type": "application/json"},
    )

    assert response.status_code == 200
    assert response.json()["code"] == "invalid_input"
    assert "Quartzwhistle" not in response.text


def test_the_old_checkin_page_has_no_phone_pattern_of_its_own():
    page = client.get("/checkin")

    assert page.status_code == 200
    assert "pattern=" not in page.text
    assert 'type="tel"' in page.text


@pytest.mark.parametrize("typed", ["079\n1234567", "001000000\r0", "079\t123 45 67", "079 123 45 67", "0791234567\x00"])
def test_a_control_character_inside_a_number_is_refused_everywhere(typed):
    """Found by the property tests: the assistant and the webhook used to accept these."""
    from app.services.call_requests import _normalized, parse_call_completed

    assert checkin(phone=typed).status_code == 422
    assert tool("bookings/find", phone=typed)["code"] == "invalid_phone"
    assert tool("bookings", name="Test Customer", phone=typed, date=TOMORROW, time="10:00",
                service_id="classic-cut")["code"] == "invalid_phone"
    assert _normalized(parse_call_completed({"extracted": {"phone": typed}}).phone) is None
    assert_nothing_booked()


def test_space_around_a_number_is_ignored_everywhere():
    typed = "\n  079 123 45 67 \t"

    assert checkin(phone=typed).json()["customer"]["phone"] == PHONE
    assert tool("bookings/find", phone=typed)["ok"] is True
