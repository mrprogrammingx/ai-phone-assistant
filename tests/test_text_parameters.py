"""The dashboard's Parameters are only String or Number, with no boolean and no required flag.

Consent therefore arrives as text. Phones and dates must be text, because a number has
lost a leading + or 0, and a date given as a number is silently read as a Unix timestamp.
"""
import re
from pathlib import Path

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.assistant_main import app
from app.database import SessionLocal
from app.models import QueueEntry, WaitlistEntry
from app.routers import assistant as assistant_router

KEY = "test-assistant-key"
TOKEN = "fake-path-token-for-tests-0123456789abcdef"
DAY = "2026-12-02"
PHONE = "+41791234567"
TIMESTAMP = 1796169600  # 2026-12-02 00:00 UTC, which lax parsing reads as that date

ROUTES = ["header", "token"]
BOOKING = {"name": "Greta Fiction", "phone": PHONE, "date": DAY, "time": "10:00", "service_id": "classic-cut"}
WAITLIST = {
    "name": "Greta Fiction",
    "phone": PHONE,
    "service_id": "classic-cut",
    "date": DAY,
    "from": "10:00",
    "to": "12:00",
}
BODIES = {
    "availability": {"date": DAY, "service_id": "classic-cut"},
    "bookings": BOOKING,
    "bookings/find": {"phone": PHONE, "date": DAY},
    "bookings/cancel": {"phone": PHONE, "date": DAY, "time": "10:00"},
    "waitlist": {**WAITLIST, "consent": "yes"},
    "waitlist/accept": {"phone": PHONE, "date": DAY, "time": "10:00"},
}
TAKE_A_PHONE = [tool for tool, body in BODIES.items() if "phone" in body]
TAKE_A_DATE = list(BODIES)
TAKE_A_TIME = [tool for tool, body in BODIES.items() if "time" in body]

client = TestClient(app)


@pytest.fixture(autouse=True)
def secrets_configured(monkeypatch):
    monkeypatch.setenv("ASSISTANT_API_KEY", KEY)
    monkeypatch.setenv("SUISSELINE_PATH_TOKEN", TOKEN)


def call(route, tool, body):
    if route == "header":
        response = client.post(f"/assistant/{tool}", json=body, headers={"X-API-Key": KEY})
    else:
        response = client.post(f"/assistant/t/{TOKEN}/{tool}", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def stored():
    with SessionLocal() as db:
        return db.query(QueueEntry).count(), db.query(WaitlistEntry).count()


# --- Consent --------------------------------------------------------------------------


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize(
    "consent",
    ["yes", "YES", "Yes", " yes ", "\tyes\n", "true", "TRUE", "True", " true ", "ja", "JA", " Ja ", "oui", "OUI", "si", "SI", " Si ", True],
    ids=repr,
)
def test_a_clear_yes_joins_the_waitlist(route, consent):
    result = call(route, "waitlist", {**WAITLIST, "consent": consent})

    assert result["ok"] is True, result
    assert stored() == (0, 1)


MISSING = object()


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize(
    "consent",
    [
        MISSING, "", "   ", None,
        "no", "NO", " no ", "false", "False", False,
        "1", "0", "on", "off", "y", "Y", "t", "T", "n", "f",
        "maybe", "sure", "ok", "okay", "yes please", "yes.", "not yes", "nein", "non", "yep", "oui oui", "sí",
        1, 0, 1.0, 0.0, 2, [], ["yes"], {}, {"consent": "yes"},
    ],
    ids=lambda value: "missing" if value is MISSING else repr(value),
)
def test_anything_but_a_clear_yes_is_consent_required(route, consent):
    body = dict(WAITLIST) if consent is MISSING else {**WAITLIST, "consent": consent}

    result = call(route, "waitlist", body)

    assert (result["ok"], result["code"]) == (False, "consent_required"), result
    assert stored() == (0, 0)


# --- Phones ---------------------------------------------------------------------------


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("tool", TAKE_A_PHONE)
@pytest.mark.parametrize("phone", [41791234567, 791234567, 41791234567.0, True], ids=repr)
def test_a_phone_sent_as_a_number_is_invalid_phone(route, tool, phone):
    result = call(route, tool, {**BODIES[tool], "phone": phone})

    assert (result["ok"], result["code"]) == (False, "invalid_phone"), result
    assert stored() == (0, 0)


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("tool", TAKE_A_PHONE)
@pytest.mark.parametrize("phone", ["+41791234567", "079 123 45 67", "0041 79 123 45 67", "41791234567"])
def test_a_phone_sent_as_text_is_accepted(route, tool, phone):
    result = call(route, tool, {**BODIES[tool], "phone": phone})

    assert result.get("code") not in ("invalid_phone", "invalid_input"), result


# --- Dates and times ------------------------------------------------------------------


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("tool", TAKE_A_DATE)
@pytest.mark.parametrize(
    "date",
    [TIMESTAMP, float(TIMESTAMP), str(TIMESTAMP), "2026-12-02T00:00:00", "20261202", 20261202, True, "02.12.2026", "tomorrow", "2026-02-30"],
    ids=repr,
)
def test_a_date_that_is_not_yyyy_mm_dd_text_is_invalid_input(route, tool, date):
    result = call(route, tool, {**BODIES[tool], "date": date})

    assert (result["ok"], result["code"]) == (False, "invalid_input"), result
    assert "date" in result["message"]
    assert stored() == (0, 0)


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("tool", TAKE_A_DATE)
def test_a_date_sent_as_text_is_accepted(route, tool):
    result = call(route, tool, BODIES[tool])

    assert result.get("code") != "invalid_input", result


@pytest.mark.parametrize("route", ROUTES)
def test_the_optional_date_may_be_blank_or_absent(route):
    assert call(route, "bookings/find", {"phone": PHONE, "date": ""})["ok"] is True
    assert call(route, "bookings/find", {"phone": PHONE})["ok"] is True


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("tool", TAKE_A_TIME)
@pytest.mark.parametrize("value", [36000, 36000.0, "36000", "10", True], ids=repr)
def test_a_time_that_is_not_text_stays_refused(route, tool, value):
    result = call(route, tool, {**BODIES[tool], "time": value})

    assert (result["ok"], result["code"]) == (False, "invalid_input"), result


# --- docs/dashboard-entry.md ----------------------------------------------------------

ENTRY_FILE = Path(__file__).resolve().parents[1] / "docs" / "dashboard-entry.md"
MODELS = {
    "get_business_info": ("info", None),
    "check_availability": ("availability", assistant_router.AvailabilityRequest),
    "create_booking": ("bookings", assistant_router.CreateBookingRequest),
    "find_booking": ("bookings/find", assistant_router.FindBookingRequest),
    "cancel_booking": ("bookings/cancel", assistant_router.CancelBookingRequest),
    "join_waitlist": ("waitlist", assistant_router.JoinWaitlistRequest),
    "accept_waitlist_offer": ("waitlist/accept", assistant_router.AcceptOfferRequest),
}


def entry_sections():
    text = ENTRY_FILE.read_text(encoding="utf-8")
    parts = re.split(r"^## `(\w+)`$", text, flags=re.M)
    return dict(zip(parts[1::2], parts[2::2]))


def test_dashboard_entry_has_exactly_the_seven_tools():
    assert list(entry_sections()) == list(MODELS)


@pytest.mark.parametrize("tool", MODELS)
def test_dashboard_entry_matches_the_code(tool):
    path, model = MODELS[tool]
    section = entry_sections()[tool]

    assert f"https://YOUR-TUNNEL-ADDRESS/assistant/t/YOUR-TOKEN/{path}" in section
    assert f"/assistant/t/{{token}}/{path}" in {route.path for route in app.routes if isinstance(route, APIRoute)}
    rows = re.findall(r"^\| `(\w+)` \| (\w+) \| (.+) \|$", section, flags=re.M)
    expected = [] if model is None else [field.alias or name for name, field in model.model_fields.items()]
    assert sorted(name for name, _, _ in rows) == sorted(expected)
    assert {kind for _, kind, _ in rows} <= {"String"}
    for name, _, description in rows:
        assert description.startswith(("Required.", "Optional.")), name


def test_dashboard_entry_holds_only_the_placeholder():
    text = ENTRY_FILE.read_text(encoding="utf-8")
    tokens = set(re.findall(r"/assistant/t/([^/\s`]+)/", text))
    assert tokens == {"YOUR-TOKEN"}
