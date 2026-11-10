"""Every route answers bad input with a readable error, and random use never breaks the rules.

The routes are read from the applications themselves, so a route added later is
covered without touching this file.
"""
import hashlib
import hmac
import json
import logging
import random
import re
import shutil
import subprocess
import time as clock
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.assistant_main import app as assistant_app
from app.database import SessionLocal, engine
from app.main import app as main_app
from app.models import (
    BlockedSlot,
    Customer,
    QueueEntry,
    QueueStatus,
    WaitlistEntry,
    WaitlistOffer,
    WaitlistOfferStatus,
)
from app.services import booking
from app.services.catalog import SERVICES
from app.services.waitlist import release_slot
from tests.conftest import TEST_TODAY

REPO_ROOT = Path(__file__).resolve().parents[1]
API_KEY = "test-assistant-key"
WEBHOOK_SECRET = "test-webhook-secret"
PATH_TOKEN = "fake-path-token-for-tests-0123456789abcdef"
TODAY = date.fromisoformat(TEST_TODAY)
OVERSIZED = b'{"name": "' + b"x" * (1024 * 1024) + b'"}'

# Never raise inside the test: a crash must come back as a 500 so it can be reported.
site = TestClient(main_app, raise_server_exceptions=False)
assistant = TestClient(assistant_app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def secrets(monkeypatch):
    monkeypatch.setenv("ASSISTANT_API_KEY", API_KEY)
    monkeypatch.setenv("SUISSELINE_WEBHOOK_SECRET", WEBHOOK_SECRET)
    monkeypatch.setenv("SUISSELINE_PATH_TOKEN", PATH_TOKEN)


@pytest.fixture
def staff():
    client = TestClient(main_app, raise_server_exceptions=False)
    assert client.post("/api/login", json={"username": "admin", "password": "test-password-123"}).status_code == 200
    return client


# --- what must never appear in a response or a log line ---------------------------

LEAKS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"traceback",
        r"sqlalchemy",
        r"sqlite3",
        r"psycopg",
        r"site-packages",
        re.escape(str(REPO_ROOT)),
        r"/Users/",
        r"/home/",
        r"/private/",
        r"/var/folders/",
        r"[A-Z]:\\\\",
        r'File "',
        r"\.py\b",
    )
]


def assert_no_leak(text_to_check: str, where: str) -> None:
    for pattern in LEAKS:
        found = pattern.search(text_to_check)
        assert found is None, f"{where} contains {found.group(0)!r}: {text_to_check[:300]}"


def logged(caplog) -> str:
    """What the application wrote, without pytest's own "file.py:line" prefix on each line."""
    return "\n".join(f"{record.getMessage()} {record.exc_text or ''}" for record in caplog.records)


def assert_readable_error(response, where: str) -> None:
    """`detail` is a sentence, or a list of objects that each carry a sentence in `msg`."""
    body = response.json()
    assert isinstance(body, dict) and "detail" in body, (where, body)
    detail = body["detail"]
    if isinstance(detail, str):
        assert detail.strip(), where
    else:
        assert isinstance(detail, list) and detail, (where, detail)
        for item in detail:
            assert isinstance(item, dict), (where, item)
            assert isinstance(item.get("msg"), str) and item["msg"].strip(), (where, item)


# --- the routes -------------------------------------------------------------------

PREFIXES = ("/queue", "/admin", "/api", "/assistant")
PATH_VALUES = {
    "entry_id": ["abc", "-1", "0", "1.5", "99999999999999999999", "9223372036854775808", "%00", "1;DROP"],
    "queue_date": ["abc", "2026-02-30", "0000-00-00", "99999-01-01", "2026-12-01T10:00", "%00"],
}
BAD_QUERIES = [
    "",
    "?for_date=abc",
    "?for_date=2026-02-30",
    "?for_date=0001-01-01",
    "?for_date=9999-12-31",
    "?for_date=2026-12-02&for_date=abc",
    "?for_date=%00",
    "?phone=",
    "?phone=abc",
    "?phone=%00%0a",
    "?phone=" + "9" * 5000,
    "?phone=%2B41791234567&for_date=abc",
    "?phone[]=1&phone[]=2",
]
BAD_BODIES = {
    "no-body": {},
    "empty-object": {"json": {}},
    "null": {"json": None},
    "string": {"json": "text"},
    "number": {"json": 42},
    "true": {"json": True},
    "array": {"json": [1, "two", None]},
    "array-of-objects": {"json": [{"name": "x"}]},
    "nested-wrong-types": {"json": {"name": {"a": 1}, "phone": [1], "date": 5, "time": False,
                                    "appointment_date": [], "appointment_time": {}, "note": 7,
                                    "open_days": "mon", "open_time": 9, "close_time": None,
                                    "slot_duration_minutes": "x", "capacity_per_slot": [],
                                    "notification_emails": "nobody", "booking_open": "maybe",
                                    "username": 1, "password": None, "status": "flying",
                                    "service_id": 3, "from": [], "to": {}, "consent": "yes"}},
    "all-null": {"json": {field: None for field in (
        "name", "phone", "date", "time", "appointment_date", "appointment_time", "note", "open_days",
        "open_time", "close_time", "slot_duration_minutes", "capacity_per_slot", "notification_emails",
        "booking_open", "username", "password", "service_id", "from", "to", "consent")}},
    "not-json": {"content": b"{not json", "headers": {"Content-Type": "application/json"}},
    "not-utf8": {"content": b"\xff\xfe\x00", "headers": {"Content-Type": "application/json"}},
    "form-encoded": {"content": b"name=x&phone=y", "headers": {"Content-Type": "application/x-www-form-urlencoded"}},
    "plain-text": {"content": b"hello", "headers": {"Content-Type": "text/plain"}},
    "deeply-nested": {"content": b"[" * 5000 + b"]" * 5000, "headers": {"Content-Type": "application/json"}},
    "oversized": {"content": OVERSIZED, "headers": {"Content-Type": "application/json"}},
}


def api_routes(app):
    routes = []
    for route in app.routes:
        if isinstance(route, APIRoute) and route.path.startswith(PREFIXES):
            for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
                routes.append((method, route.path, route.body_field is not None))
    return sorted(routes)


def concrete_paths(path: str) -> list[str]:
    names = re.findall(r"{(\w+)}", path)
    if not names:
        return [path]
    assert all(name in PATH_VALUES for name in names), f"add bad values for the path parameters of {path}"
    return [path.replace("{" + names[0] + "}", value) for value in PATH_VALUES[names[0]]]


MAIN_ROUTES = api_routes(main_app)
ASSISTANT_ROUTES = api_routes(assistant_app)
TOOL_ROUTES = [route for route in ASSISTANT_ROUTES if "/webhook/" not in route[1] and "/t/{token}/" not in route[1]]
TOKEN_TOOL_ROUTES = [route for route in ASSISTANT_ROUTES if "/t/{token}/" in route[1] and not route[1].endswith("/capture")]
WEBHOOK_ROUTES = [route for route in ASSISTANT_ROUTES if "/webhook/" in route[1]]


def test_the_route_tables_cover_the_whole_api():
    main_paths = {path for _, path, _ in MAIN_ROUTES}
    assert {"/queue/checkin", "/queue/my-bookings", "/admin/schedule-settings", "/api/login"} <= main_paths
    assert len(MAIN_ROUTES) >= 27
    assert len(TOOL_ROUTES) == 7
    assert len(TOKEN_TOOL_ROUTES) == 7
    assert len(WEBHOOK_ROUTES) == 1


def send(client, method, path, kwargs):
    return client.request(method, path, **kwargs)


@pytest.mark.parametrize("method, path, takes_body", MAIN_ROUTES, ids=[f"{m} {p}" for m, p, _ in MAIN_ROUTES])
@pytest.mark.parametrize("as_staff", [False, True], ids=["anonymous", "staff"])
def test_website_routes_answer_bad_input_with_a_readable_4xx(method, path, takes_body, as_staff, staff, caplog):
    client = staff if as_staff else site
    caplog.set_level(logging.DEBUG)
    answers = []

    for concrete in concrete_paths(path):
        for name, kwargs in BAD_BODIES.items():
            answers.append((f"{method} {concrete} [{name}]", send(client, method, concrete, kwargs), True))
        if method == "GET":
            for query in BAD_QUERIES:
                answers.append((f"GET {concrete}{query[:40]}", send(client, "GET", concrete + query, {}), False))

    for where, response, sent_a_body in answers:
        assert response.status_code < 500, f"{where} -> {response.status_code} {response.text[:300]}"
        assert_no_leak(response.text, where)
        if response.status_code >= 400:
            assert_readable_error(response, where)
        # A route that reads a body, or has a path parameter, can never accept this input.
        if (takes_body and sent_a_body) or concrete_paths(path) != [path]:
            assert 400 <= response.status_code < 500, f"{where} -> {response.status_code}"
    assert_no_leak(logged(caplog), f"log of {method} {path}")


def test_bad_input_changes_nothing(staff):
    before = booking_state()

    for method, path, _ in MAIN_ROUTES:
        if path in ("/api/logout", "/admin/reset-queue", "/queue/call-next"):
            continue  # these take no input, so a body cannot make them invalid
        for concrete in concrete_paths(path):
            for kwargs in BAD_BODIES.values():
                send(staff, method, concrete, kwargs)

    assert booking_state() == before


def booking_state():
    with SessionLocal() as db:
        status = booking.get_or_create_shop_status(db)
        return (
            db.query(Customer).count(),
            db.query(QueueEntry).count(),
            db.query(BlockedSlot).count(),
            status.open_days,
            status.open_time,
            status.close_time,
            status.slot_duration_minutes,
            status.capacity_per_slot,
            status.notification_emails,
            status.booking_open,
        )


@pytest.mark.parametrize("method, path, takes_body", TOKEN_TOOL_ROUTES, ids=[f"{m} {p}" for m, p, _ in TOKEN_TOOL_ROUTES])
def test_token_routes_answer_bad_input_in_the_same_contract(method, path, takes_body):
    for name, kwargs in BAD_BODIES.items():
        where = f"{method} {path} [{name}]"

        response = send(assistant, method, path.replace("{token}", PATH_TOKEN), kwargs)

        assert response.status_code == 200, f"{where} -> {response.status_code} {response.text[:300]}"
        assert_no_leak(response.text, where)
        result = response.json()
        if takes_body:
            assert result["ok"] is False, where
            assert result["code"] == "invalid_input", where
        else:
            assert result["ok"] is True, where

        refused = send(assistant, method, path.replace("{token}", "wrong-token"), kwargs)
        assert refused.status_code == 404, where
        assert refused.json() == {"detail": "Not Found"}, where


@pytest.mark.parametrize("method, path, takes_body", TOOL_ROUTES, ids=[f"{m} {p}" for m, p, _ in TOOL_ROUTES])
def test_assistant_tools_answer_bad_input_in_their_own_contract(method, path, takes_body, caplog):
    """The assistant's contract is HTTP 200 with ok false. It is deliberate, see docs/PLAN.md section 6."""
    caplog.set_level(logging.DEBUG)

    for name, kwargs in BAD_BODIES.items():
        where = f"{method} {path} [{name}]"
        headers = {**kwargs.get("headers", {}), "X-API-Key": API_KEY}

        response = send(assistant, method, path, {**kwargs, "headers": headers})

        assert response.status_code == 200, f"{where} -> {response.status_code} {response.text[:300]}"
        assert_no_leak(response.text, where)
        result = response.json()
        if takes_body:
            assert result["ok"] is False, where
            assert result["code"] == "invalid_input", where
            assert isinstance(result["message"], str) and result["message"].strip(), where
        else:
            assert result["ok"] is True, where  # /assistant/info reads no input

        refused = send(assistant, method, path, kwargs)
        assert refused.status_code == 401, where
        assert refused.json() == {"detail": "Invalid API key"}
    assert_no_leak(logged(caplog), f"log of {path}")
    with SessionLocal() as db:
        assert db.query(QueueEntry).count() == db.query(WaitlistEntry).count() == 0


def test_the_webhook_answers_bad_input_with_a_readable_4xx(caplog):
    caplog.set_level(logging.DEBUG)
    (method, path, _), = WEBHOOK_ROUTES

    for name, kwargs in BAD_BODIES.items():
        raw = kwargs.get("content", json.dumps(kwargs.get("json")).encode() if "json" in kwargs else b"")
        signature = hmac.new(WEBHOOK_SECRET.encode(), raw, hashlib.sha256).hexdigest()

        unsigned = assistant.request(method, path, content=raw)
        signed = assistant.request(method, path, content=raw, headers={"X-Signature": signature})

        assert unsigned.status_code in (401, 413), name
        assert_readable_error(unsigned, f"unsigned webhook [{name}]")
        assert signed.status_code < 500, f"signed webhook [{name}] -> {signed.status_code} {signed.text[:300]}"
        if signed.status_code >= 400:
            assert_readable_error(signed, f"signed webhook [{name}]")
        else:
            assert signed.json()["status"] in ("follow_up", "ignored"), name
        assert_no_leak(unsigned.text + signed.text, f"webhook [{name}]")
    assert assistant.request(method, path, content=OVERSIZED).status_code == 413
    assert_no_leak(logged(caplog), "webhook log")
    with SessionLocal() as db:
        assert db.query(QueueEntry).count() == 0


@pytest.mark.parametrize("path", ["/nope", "/assistant/nope", "/queue/checkin", "/admin/shop-status", "/docs",
                                  "/openapi.json", "/assistant/bookings/1", "/calendar/x.ics"])
def test_unknown_paths_on_the_assistant_app_are_plain_404s(path):
    response = assistant.get(path, headers={"X-API-Key": API_KEY})

    assert response.status_code in (404, 405)
    assert_readable_error(response, path)
    assert_no_leak(response.text, path)


def test_a_database_failure_is_a_plain_500_without_details(monkeypatch, caplog):
    """A crash still answers, and the answer says nothing about the code or the database."""

    def broken(*_args, **_kwargs):
        raise RuntimeError("sqlite3.OperationalError at /Users/someone/app/services/booking.py")

    monkeypatch.setattr("app.routers.queue.slot_availability", broken)

    response = site.get("/queue/available-times")

    assert response.status_code == 500
    assert_no_leak(response.text, "500 body")
    assert "Quartzwhistle" not in response.text


# --- the website's error helper ---------------------------------------------------


def collected_error_bodies(staff) -> list[tuple[int, object]]:
    bodies = []
    for client in (site, staff):
        for method, path, _ in MAIN_ROUTES:
            for concrete in concrete_paths(path)[:2]:
                for kwargs in BAD_BODIES.values():
                    response = send(client, method, concrete, kwargs)
                    if response.status_code >= 400:
                        bodies.append((response.status_code, response.json().get("detail")))
    # Shapes no route sends today. The helper must survive them all the same.
    bodies += [
        (500, None), (502, {"error": "x"}), (400, []), (400, [{}]), (400, [{"msg": 5}]), (400, [None, "x"]),
        (400, [{"msg": ""}]), (400, ""), (400, 0), (400, {"msg": "inside an object"}), (422, [[{"msg": "x"}]]),
    ]
    return bodies


def test_the_frontend_helper_turns_every_error_into_a_sentence(staff):
    """Runs frontend/src/lib/errorMessage.js itself in Node. Skipped where Node is not installed."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is not installed")
    bodies = collected_error_bodies(staff)
    assert len(bodies) > 300
    assert any(isinstance(detail, str) for _, detail in bodies)
    assert any(isinstance(detail, list) for _, detail in bodies)

    script = (
        "import { errorMessage, fieldErrors } from './frontend/src/lib/errorMessage.js';"
        "let input = ''; process.stdin.on('data', (chunk) => { input += chunk; });"
        "process.stdin.on('end', () => { const cases = JSON.parse(input);"
        "console.log(JSON.stringify(cases.map(([status, detail]) =>"
        " [errorMessage(detail, status), Object.values(fieldErrors(detail))]))); });"
    )
    done = subprocess.run(
        [node, "--input-type=module", "-e", script],
        input=json.dumps(bodies), cwd=REPO_ROOT, capture_output=True, text=True, timeout=30,
    )
    assert done.returncode == 0, done.stderr

    for (status, detail), (message, field_messages) in zip(bodies, json.loads(done.stdout)):
        for shown in [message, *field_messages]:
            assert isinstance(shown, str) and shown.strip(), (status, detail)
            assert "[object" not in shown and "undefined" not in shown and "null" not in shown, (detail, shown)
            assert not shown.startswith(("{", "[")), (detail, shown)
            assert "Value error" not in shown, (detail, shown)


# --- random sequences -------------------------------------------------------------

DAYS = [TODAY + timedelta(days=offset) for offset in (0, 1, 2, 3)]
PHONES = [f"+4179000{index:04d}" for index in range(12)]
SERVICE_IDS = sorted(SERVICES)
SLOTS = [f"{hour:02d}:{minute:02d}" for hour in range(8, 20) for minute in (0, 15, 30, 45)]


class Shop:
    """Drives both applications the way customers, callers and staff would, in a random order."""

    def __init__(self, seed: int, staff: TestClient):
        self.random = random.Random(seed)
        self.staff = staff
        self.slowest = 0.0
        self.log: list[str] = []

    def timed(self, label, call):
        started = clock.perf_counter()
        response = call()
        elapsed = clock.perf_counter() - started
        self.slowest = max(self.slowest, elapsed)
        self.log.append(f"{label} -> {response.status_code}")
        assert elapsed < 1, f"{label} took {elapsed:.2f}s"
        assert response.status_code < 500, f"{label} -> {response.status_code} {response.text[:300]}"
        assert_no_leak(response.text, label)
        return response

    def tool(self, path, **body):
        response = self.timed(
            f"assistant {path}",
            lambda: assistant.post(f"/assistant/{path}", json=body, headers={"X-API-Key": API_KEY}),
        )
        assert response.status_code == 200
        result = response.json()
        assert isinstance(result["ok"], bool)
        if not result["ok"]:
            assert result["code"] and result["message"]
        return result

    def pick(self):
        return self.random.choice(DAYS), self.random.choice(SLOTS), self.random.choice(PHONES)

    # each action returns the booked entry id if it created a booking, else None

    def book_on_the_website(self):
        day, slot, phone = self.pick()
        response = self.timed("checkin", lambda: site.post("/queue/checkin", json={
            "name": "Random Customer", "phone": phone, "appointment_date": day.isoformat(),
            "appointment_time": slot, "note": self.random.choice([None, "", "a note"]),
        }))
        assert response.status_code in (200, 400), response.text
        return response.json()["id"] if response.status_code == 200 else None

    def book_by_phone(self):
        day, slot, phone = self.pick()
        result = self.tool("bookings", name="Random Caller", phone=phone, date=day.isoformat(), time=slot,
                           service_id=self.random.choice(SERVICE_IDS))
        return result["booking"]["id"] if result["ok"] and result["created"] else None

    def cancel(self):
        with SessionLocal() as db:
            entries = db.query(QueueEntry).all()
            if not entries:
                return None
            entry = self.random.choice(entries)
            details = (entry.customer.phone, entry.queue_date.isoformat(), entry.appointment_time.strftime("%H:%M"))
        self.tool("bookings/cancel", phone=details[0], date=details[1], time=details[2])
        return None

    def mark_no_show(self):
        entry_id = self.any_entry_id()
        if entry_id is not None:
            self.timed("no-show", lambda: self.staff.post(f"/queue/{entry_id}/no-show"))
        return None

    def revive(self):
        """Marks an entry done. For a No Show that takes its slot back, if it is still free."""
        with SessionLocal() as db:
            entries = db.query(QueueEntry).all()
            if not entries:
                return None
            entry = self.random.choice(entries)
            entry_id, was_no_show = entry.id, entry.status == QueueStatus.no_show
        response = self.timed("done", lambda: self.staff.post(f"/queue/{entry_id}/done"))
        assert response.status_code in (200, 409), response.text
        return entry_id if was_no_show and response.status_code == 200 else None

    def join_waitlist(self):
        day, slot, phone = self.pick()
        end = self.random.choice([candidate for candidate in SLOTS if candidate >= slot])
        self.tool("waitlist", name="Random Waiter", phone=phone, service_id=self.random.choice(SERVICE_IDS),
                  date=day.isoformat(), consent=self.random.choice([True, True, True, None]),
                  **{"from": slot, "to": end})
        return None

    def release(self):
        day, slot, _ = self.pick()
        started = clock.perf_counter()
        with SessionLocal() as db:
            release_slot(db, day, time.fromisoformat(slot))
        assert clock.perf_counter() - started < 1
        self.log.append("release")
        return None

    def accept(self):
        with SessionLocal() as db:
            offers = db.query(WaitlistOffer).all()
            if not offers:
                return None
            offer = self.random.choice(offers)
            details = (offer.entry.phone, offer.slot_date.isoformat(), offer.slot_time.strftime("%H:%M"))
            was_open = offer.status == WaitlistOfferStatus.open
        result = self.tool("waitlist/accept", phone=details[0], date=details[1], time=details[2])
        return result["booking"]["id"] if result["ok"] and was_open else None

    def change_settings(self):
        choice = self.random.randrange(6)
        if choice == 0:
            # Mostly a normal working day, sometimes values that must be refused.
            body = {
                "open_time": self.random.choice(["08:00", "09:00", "09:00", "10:00", "19:00"]),
                "close_time": self.random.choice(["17:00", "18:00", "18:00", "20:00", "08:00"]),
                "slot_duration_minutes": self.random.choice([0, -5, 1, 15, 30, 30, 45, 60, 1000]),
                "capacity_per_slot": self.random.choice([0, -1, 1, 1, 2, 3, 21]),
            }
            response = self.timed("schedule-settings", lambda: self.staff.post("/admin/schedule-settings", json=body))
        elif choice == 1:
            # Usually most of the week, sometimes nothing or nonsense, which must be refused.
            days = self.random.choice([
                ["mon", "tue", "wed", "thu", "fri", "sat", "sun"], ["tue", "wed", "thu", "fri", "sat"],
                ["sun", "tue", "wed", "thu"], [], ["funday"], ["wed", "wed", "tue", "thu", "fri"],
            ])
            response = self.timed("open-days", lambda: self.staff.post("/admin/open-days", json={"open_days": days}))
        elif choice == 2:
            flag = self.random.choice([True, True, True, False])
            response = self.timed("shop-status", lambda: self.staff.post("/admin/shop-status", json={"booking_open": flag}))
        else:
            day, slot, _ = self.pick()
            body = {"date": day.isoformat(), "time": slot}
            route = self.random.choice(["blocked-slots", "included-slots"])
            method = self.random.choice(["POST", "POST", "DELETE"])
            response = self.timed(f"{method} {route}", lambda: self.staff.request(method, f"/admin/{route}", json=body))
        assert response.status_code in (200, 400, 404, 422), response.text
        return None

    def look(self):
        day, _, phone = self.pick()
        self.timed("available-times", lambda: site.get(f"/queue/available-times?for_date={day}"))
        self.timed("my-bookings", lambda: site.get("/queue/my-bookings", params={"phone": phone}))
        self.tool("availability", date=day.isoformat(), service_id=self.random.choice(SERVICE_IDS))
        self.tool("bookings/find", phone=phone)
        return None

    def any_entry_id(self):
        with SessionLocal() as db:
            ids = [row[0] for row in db.query(QueueEntry.id)]
        return self.random.choice(ids) if ids else None

    def step(self):
        actions = [
            (self.book_on_the_website, 5), (self.book_by_phone, 5), (self.cancel, 2), (self.mark_no_show, 2),
            (self.revive, 2), (self.join_waitlist, 3), (self.release, 2), (self.accept, 2),
            (self.change_settings, 2), (self.look, 1),
        ]
        action = self.random.choices([a for a, _ in actions], weights=[w for _, w in actions])[0]
        return action()


def assert_within_capacity(entry_id: int) -> None:
    """The appointment just created or revived never has more than `capacity` running at once."""
    with SessionLocal() as db:
        status = booking.get_or_create_shop_status(db)
        slot_minutes, capacity = status.slot_duration_minutes, status.capacity_per_slot
        entry = db.get(QueueEntry, entry_id)
        occupying = (
            db.query(QueueEntry)
            .filter(QueueEntry.queue_date == entry.queue_date)
            .filter(QueueEntry.status.in_(booking.SLOT_OCCUPYING_STATUSES))
            .all()
        )
        intervals = [
            (datetime.combine(row.queue_date, row.appointment_time),
             datetime.combine(row.queue_date, row.appointment_time)
             + timedelta(minutes=row.duration_minutes or slot_minutes))
            for row in occupying
        ]
        start = datetime.combine(entry.queue_date, entry.appointment_time)
        end = start + timedelta(minutes=entry.duration_minutes or slot_minutes)
        blocked = [row.blocked_time for row in db.query(BlockedSlot).filter(BlockedSlot.blocked_date == entry.queue_date)]

    assert entry.status in booking.SLOT_OCCUPYING_STATUSES
    minute = start
    while minute < end:
        running = sum(begins <= minute < ends for begins, ends in intervals)
        assert running <= capacity, f"{running} appointments at {minute}, capacity {capacity}"
        minute += timedelta(minutes=1)
    assert 1 <= capacity <= 20 and 5 <= slot_minutes <= 240
    return blocked


def assert_database_is_sound() -> None:
    with engine.connect() as connection:
        assert connection.execute(text("PRAGMA integrity_check")).scalar() == "ok"
        assert connection.execute(text("PRAGMA foreign_key_check")).fetchall() == []
        duplicates = {
            "customers.phone": "SELECT phone FROM customers GROUP BY phone HAVING COUNT(*) > 1",
            "blocked slot": "SELECT 1 FROM blocked_slots GROUP BY blocked_date, blocked_time HAVING COUNT(*) > 1",
            "included slot": "SELECT 1 FROM included_slots GROUP BY included_date, included_time HAVING COUNT(*) > 1",
            "shop_status rows": "SELECT 1 FROM shop_status GROUP BY 1 HAVING COUNT(*) > 1",
            "accepted offer per booking": (
                "SELECT queue_entry_id FROM waitlist_offers WHERE queue_entry_id IS NOT NULL "
                "GROUP BY queue_entry_id HAVING COUNT(*) > 1"
            ),
        }
        for name, query in duplicates.items():
            assert connection.execute(text(query)).fetchall() == [], f"duplicate {name}"
        nulls = {
            "queue_entries": "customer_id IS NULL OR position IS NULL OR status IS NULL OR appointment_time IS NULL",
            "customers": "name IS NULL OR phone IS NULL OR TRIM(name) = '' OR TRIM(phone) = ''",
            "waitlist_entries": "name IS NULL OR phone IS NULL OR status IS NULL OR consent_at IS NULL",
            "waitlist_offers": "entry_id IS NULL OR status IS NULL OR expires_at IS NULL",
        }
        for table, condition in nulls.items():
            assert connection.execute(text(f"SELECT COUNT(*) FROM {table} WHERE {condition}")).scalar() == 0, table
        assert connection.execute(text("SELECT COUNT(*) FROM queue_entries WHERE position < 1")).scalar() == 0
        assert connection.execute(
            text("SELECT COUNT(*) FROM queue_entries WHERE duration_minutes IS NOT NULL AND duration_minutes < 1")
        ).scalar() == 0
        assert connection.execute(
            text("SELECT COUNT(*) FROM shop_status WHERE slot_duration_minutes < 5 OR capacity_per_slot < 1 "
                 "OR open_time >= close_time OR open_days = ''")
        ).scalar() == 0
        # Every customer's phone is in E.164, and an accepted offer always points at its booking.
        assert connection.execute(text("SELECT COUNT(*) FROM customers WHERE phone NOT LIKE '+%'")).scalar() == 0
        assert connection.execute(
            text("SELECT COUNT(*) FROM waitlist_offers WHERE status = 'accepted' AND queue_entry_id IS NULL")
        ).scalar() == 0


def assert_counts_are_sane() -> None:
    for day in DAYS:
        response = site.get(f"/queue/available-times?for_date={day}")
        assert response.status_code == 200
        slots = response.json()
        assert len(slots) <= booking.MAX_SLOTS_PER_DAY + 48
        for slot in slots:
            assert slot["booked"] >= 0 and slot["capacity"] >= 1
            if slot["available"]:
                assert slot["booked"] < slot["capacity"] and not slot["blocked"]


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5, 20261201])
def test_random_use_never_breaks_the_booking_rules(seed, staff, caplog):
    caplog.set_level(logging.DEBUG)
    shop = Shop(seed, staff)
    created = 0

    for step in range(200):
        try:
            entry_id = shop.step()
            if entry_id is not None:
                created += 1
                assert_within_capacity(entry_id)
            assert_database_is_sound()
            if step % 10 == 0:
                assert_counts_are_sane()
        except AssertionError as failure:
            raise AssertionError(f"seed {seed}, step {step}: {failure}\nlast steps: {shop.log[-8:]}") from failure

    assert_counts_are_sane()
    print(f"seed {seed}: {created} bookings created, slowest request {shop.slowest * 1000:.0f} ms")
    assert created >= 10, "the sequence must really book appointments"
    assert_no_leak(logged(caplog), f"log of seed {seed}")
