"""Regression tests for failures seen in practice.

The smoke test starts the three real applications on a temporary database and walks
docs/demo-runbook.md from start to finish. The rest check the preflight's three known
failures, that logs and the calendar feed carry no personal data, and that the
migration statements in README.md really run.
"""
import hashlib
import hmac
import importlib.util
import json
import logging
import os
import re
import socket
import sqlite3
import subprocess
import sys
import threading
import time as clock
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

REPO_ROOT = Path(__file__).resolve().parents[1]
SEED = REPO_ROOT / "scripts" / "seed_demo.py"
PREFLIGHT = REPO_ROOT / "scripts" / "preflight.py"

# Fake values, distinctive enough that finding one in a log is unmistakable.
API_KEY = "smoke-assistant-key-Vq7RkT2mXw9LpZ4c"
ADMIN_PASSWORD = "smoke-admin-password-Hn3JdY8sBv6QfG1e"
SESSION_SECRET = "smoke-session-secret-Tz5WcK9rMx2NpL7a"
CALENDAR_TOKEN = "smoke-calendar-token-Ub4XgE6yCj8RsD3k"
WEBHOOK_SECRET = "smoke-webhook-secret-Pm1FhA7tLq5ZvN9o"
SECRETS = [API_KEY, ADMIN_PASSWORD, SESSION_SECRET, CALENDAR_TOKEN, WEBHOOK_SECRET]


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def app_environment(database: Path) -> dict:
    return {
        **os.environ,
        "DATABASE_URL": f"sqlite:///{database}",
        "APP_TIMEZONE": "Europe/Zurich",
        "EMAIL_DRY_RUN": "true",
        "ASSISTANT_API_KEY": API_KEY,
        "ADMIN_PASSWORD": ADMIN_PASSWORD,
        "ADMIN_USERNAME": "admin",
        "SESSION_SECRET_KEY": SESSION_SECRET,
        "CALENDAR_FEED_TOKEN": CALENDAR_TOKEN,
        "SUISSELINE_WEBHOOK_SECRET": WEBHOOK_SECRET,
        "PYTHONUNBUFFERED": "1",
    }


def seed(database: Path) -> str:
    done = subprocess.run(
        [sys.executable, str(SEED)], cwd=REPO_ROOT, env=app_environment(database),
        capture_output=True, text=True, timeout=60,
    )
    assert done.returncode == 0, done.stderr
    return done.stdout


# --- the three applications, really running ---------------------------------------


class RunningApps:
    def __init__(self, folder: Path):
        self.database = folder / "smoke.db"
        self.folder = folder
        self.processes = []
        self.log_files = {}
        self.urls = {}

    def start(self, name: str, module: str, ready_path: str) -> None:
        port = free_port()
        log_path = self.folder / f"{name}.log"
        log_file = log_path.open("w")
        process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", f"{module}:app", "--host", "127.0.0.1", "--port", str(port)],
            cwd=REPO_ROOT, env=app_environment(self.database), stdout=log_file, stderr=subprocess.STDOUT,
        )
        self.processes.append((process, log_file))
        self.log_files[name] = log_path
        self.urls[name] = f"http://127.0.0.1:{port}"
        deadline = clock.monotonic() + 30
        while clock.monotonic() < deadline:
            if process.poll() is not None:
                break
            try:
                httpx.get(self.urls[name] + ready_path, timeout=1)
                return
            except httpx.TransportError:
                clock.sleep(0.1)
        self.stop()
        pytest.fail(f"the {name} app did not start:\n{log_path.read_text()[-2000:]}")

    def stop(self) -> None:
        for process, log_file in self.processes:
            process.terminate()
        for process, log_file in self.processes:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
            log_file.close()
        self.processes = []

    def logs(self) -> str:
        return "\n".join(path.read_text() for path in self.log_files.values())


@pytest.fixture(scope="module")
def apps(tmp_path_factory):
    running = RunningApps(tmp_path_factory.mktemp("smoke"))
    running.seed_output = seed(running.database)
    running.start("website", "app.main", "/health")
    running.start("assistant", "app.assistant_main", "/nothing-here")
    running.start("calendar", "app.calendar_main", "/nothing-here")
    yield running
    running.stop()


class Walk:
    """One pass through the runbook. Every answer must be a 2xx with the expected body."""

    def __init__(self, apps: RunningApps):
        self.apps = apps
        self.site = httpx.Client(base_url=apps.urls["website"], timeout=10)
        self.assistant = httpx.Client(
            base_url=apps.urls["assistant"], timeout=10, headers={"X-API-Key": API_KEY}
        )
        self.calendar = httpx.Client(base_url=apps.urls["calendar"], timeout=10)
        self.slowest = 0.0

    def ok(self, response: httpx.Response):
        self.slowest = max(self.slowest, response.elapsed.total_seconds())
        assert 200 <= response.status_code < 300, f"{response.request.method} {response.request.url.path} -> {response.status_code} {response.text[:300]}"
        return response

    def tool(self, path: str, **body) -> dict:
        result = self.ok(self.assistant.post(f"/assistant/{path}", json=body)).json()
        assert isinstance(result, dict) and isinstance(result.get("ok"), bool), result
        return result


@pytest.fixture(scope="module")
def walked(apps):
    """Walks the runbook once and returns everything later tests look at."""
    walk = Walk(apps)
    seen = {}

    # Start of day: the seed, and what the assistant knows.
    assert "Seeded fictional demo data:" in apps.seed_output
    info = walk.tool("info")
    assert info["ok"] is True and info["timezone"] == "Europe/Zurich"
    assert info["today"] == datetime.now(ZoneInfo("Europe/Zurich")).date().isoformat()
    tomorrow = (date.fromisoformat(info["today"]) + timedelta(days=1)).isoformat()
    seen["tomorrow"] = tomorrow
    assert {service["id"] for service in info["services"]} >= {"classic-cut", "cut-beard-combo"}

    # (a) Book the Cut + Beard Combo.
    combo = walk.tool("availability", date=tomorrow, service_id="cut-beard-combo")
    assert combo == {"ok": True, "date": tomorrow, "weekday": combo["weekday"],
                     "slots": ["12:00", "12:30", "13:00", "14:30", "15:00"], "more": True}
    erin = walk.tool("bookings", name="Erin Testcase", phone="079 000 01 05", date=tomorrow, time="12:00",
                     service_id="cut-beard-combo")
    assert erin["ok"] is True and erin["created"] is True
    assert erin["booking"] == {"id": erin["booking"]["id"], "date": tomorrow, "weekday": combo["weekday"],
                               "time": "12:00", "service": "Cut + Beard Combo"}
    # A retry of the same call creates nothing.
    again = walk.tool("bookings", name="Erin Testcase", phone="079 000 01 05", date=tomorrow, time="12:00",
                      service_id="cut-beard-combo")
    assert again["created"] is False and again["booking"]["id"] == erin["booking"]["id"]

    # (b) The full 10:00, then the waitlist.
    taken = walk.tool("bookings", name="Finn Madeup", phone="079 000 01 06", date=tomorrow, time="10:00",
                      service_id="classic-cut")
    assert taken["ok"] is False and taken["code"] == "slot_taken" and taken["message"]
    around_ten = walk.tool("availability", date=tomorrow, service_id="classic-cut", **{"from": "09:00", "to": "11:00"})
    assert around_ten["slots"] == ["09:30", "10:30"] and around_ten["more"] is False
    no_consent = walk.tool("waitlist", name="Finn Madeup", phone="079 000 01 06", service_id="classic-cut",
                           date=tomorrow, **{"from": "10:00", "to": "10:00"})
    assert no_consent["code"] == "consent_required"
    waiting = walk.tool("waitlist", name="Finn Madeup", phone="079 000 01 06", service_id="classic-cut",
                        date=tomorrow, consent=True, **{"from": "10:00", "to": "10:00"})
    assert waiting["ok"] is True and waiting["created"] is True
    assert waiting["entry"] == {"id": waiting["entry"]["id"], "date": tomorrow, "from": "10:00", "to": "10:00"}

    # (c) Free the 10:00: cancel Ben, then the local release command.
    found = walk.tool("bookings/find", phone="+41790000102")
    assert [(booking["date"], booking["time"]) for booking in found["bookings"]] == [(tomorrow, "10:00")]
    cancelled = walk.tool("bookings/cancel", phone="+41790000102", date=tomorrow, time="10:00")
    assert cancelled == {"ok": True, "cancelled": True, "already_cancelled": False}
    released = subprocess.run(
        [sys.executable, "-m", "app.scripts.release_slot", tomorrow, "10:00"],
        cwd=REPO_ROOT, env=app_environment(apps.database), capture_output=True, text=True, timeout=60,
    )
    assert released.returncode == 0, released.stderr
    lines = released.stdout.splitlines()
    assert lines[0] == "MANUAL CONTACT: nothing was sent. The slot is not reserved."
    assert len(lines) == 2 and lines[1].startswith("Finn Madeup | +41790000106 | Classic Cut | offer ends ")

    # (d) Accept the offer.
    accepted = walk.tool("waitlist/accept", phone="079 000 01 06", date=tomorrow, time="10:00")
    assert accepted["ok"] is True
    assert (accepted["booking"]["date"], accepted["booking"]["time"], accepted["booking"]["service"]) == (
        tomorrow, "10:00", "Classic Cut")

    # (e) What staff see, and what the public website shows.
    assert walk.ok(walk.site.get("/health")).json() == {"status": "ok"}
    assert walk.site.get(f"/queue?for_date={tomorrow}").status_code == 401
    login = walk.ok(walk.site.post("/api/login", json={"username": "admin", "password": ADMIN_PASSWORD}))
    assert login.json() == {"is_admin": True}
    dashboard = walk.ok(walk.site.get(f"/queue?for_date={tomorrow}")).json()
    seen["dashboard"] = [(entry["appointment_time"][:5], entry["customer"]["name"]) for entry in dashboard]
    public = httpx.Client(base_url=apps.urls["website"], timeout=10)
    seen["public_status"] = walk.ok(public.get("/admin/shop-status")).json()
    seen["staff_status"] = walk.ok(walk.site.get("/admin/shop-status")).json()
    seen["my_bookings"] = walk.ok(public.get("/queue/my-bookings", params={"phone": "079 000 01 06"})).json()
    slots = walk.ok(public.get(f"/queue/available-times?for_date={tomorrow}")).json()
    seen["website_slots"] = {slot["time"][:5]: slot["available"] for slot in slots}
    # A website booking in the same database, with a note.
    web = walk.ok(public.post("/queue/checkin", json={
        "name": "Greta Fiction", "phone": "079 000 01 08", "note": "Smoke note Xylograph",
        "appointment_date": tomorrow, "appointment_time": "16:00",
    })).json()
    assert web["customer"] == {"id": web["customer"]["id"], "name": "Greta Fiction", "phone": "+41790000108"}

    # A webhook delivery for a time that is taken becomes a follow-up, not an error.
    raw = json.dumps({"callId": "smoke-call-1", "event": "call.completed", "extracted": {
        "name": "Hugo Invented", "phone": "+41790000109", "service_id": "classic-cut", "date": tomorrow, "time": "09:00",
    }}).encode()
    signature = hmac.new(WEBHOOK_SECRET.encode(), raw, hashlib.sha256).hexdigest()
    hook = walk.ok(walk.assistant.post("/assistant/webhook/call-completed", content=raw,
                                       headers={"X-Signature": signature, "Content-Type": "application/json"}))
    assert hook.json() == {"ok": True, "status": "follow_up"}

    # The calendar feed.
    feed = walk.ok(walk.calendar.get(f"/calendar/{CALENDAR_TOKEN}.ics"))
    assert feed.headers["content-type"] == "text/calendar; charset=utf-8"
    seen["feed"] = feed.text
    assert walk.calendar.get("/calendar/wrong-token-of-a-plausible-length.ics").status_code == 404
    assert walk.calendar.get("/health").status_code == 404

    # Nothing but the assistant's own routes answers on the assistant app.
    for path in ("/docs", "/admin/shop-status", "/queue/my-bookings?phone=%2B41790000106", "/health"):
        assert walk.assistant.get(path).status_code == 404, path

    seen["slowest"] = walk.slowest
    clock.sleep(0.3)  # let the last log lines reach the files
    seen["logs"] = apps.logs()
    return seen


def test_the_runbook_walks_from_seed_to_calendar_feed(walked):
    # Ben's 10:00 went to Finn, Erin has the 12:00 combo, and the seeded day is otherwise intact.
    assert walked["dashboard"] == [
        ("09:00", "Anna Example"), ("10:00", "Finn Madeup"), ("11:00", "Clara Demo"),
        ("12:00", "Erin Testcase"), ("14:00", "David Fictional"),
    ]
    # Finn now has tomorrow's 10:00 as well as the appointment the seed gave him.
    finns = {(entry["queue_date"], entry["appointment_time"]) for entry in walked["my_bookings"]}
    assert (walked["tomorrow"], "10:00:00") in finns and len(finns) == 2
    assert walked["website_slots"]["10:00"] is False and walked["website_slots"]["16:00"] is True
    assert walked["slowest"] < 1, "every request in the walk answers within a second"


def test_the_walk_shows_the_public_and_staff_shapes(walked):
    assert "notification_emails" not in walked["public_status"]
    assert "notification_emails" in walked["staff_status"]
    for entry in walked["my_bookings"]:
        assert "customer" not in entry and "note" not in entry


def test_the_calendar_feed_lists_the_appointments_and_nobody(walked):
    feed = walked["feed"]

    assert feed.startswith("BEGIN:VCALENDAR\r\n") and feed.endswith("END:VCALENDAR\r\n")
    # Seeded 6, minus Ben's cancelled one, plus Erin, Finn and the website booking.
    assert feed.count("BEGIN:VEVENT") == feed.count("END:VEVENT") == 8
    assert "SUMMARY:Cut + Beard Combo" in feed and "SUMMARY:Classic Cut" in feed
    assert_nobody_is_named(feed, "the calendar feed")


PEOPLE = [
    "Anna", "Example", "Ben", "Sample", "Clara", "David", "Fictional", "Erin", "Testcase", "Finn", "Madeup",
    "Flora", "Placeholder", "Greta", "Fiction", "Hugo", "Invented", "Xylograph", "Smoke note",
]
PHONE_DIGITS = re.compile(r"7900001\d\d|79 000 01|0790000")


def assert_nobody_is_named(text_to_check: str, where: str) -> None:
    for word in PEOPLE:
        assert not re.search(rf"\b{re.escape(word)}\b", text_to_check), f"{where} contains {word!r}"
    found = PHONE_DIGITS.search(text_to_check)
    assert found is None, f"{where} contains a phone number: {found.group(0)!r}"
    for secret in SECRETS:
        assert secret not in text_to_check, f"{where} contains a secret"
        assert secret[-12:] not in text_to_check, f"{where} contains part of a secret"


def test_the_running_apps_log_no_names_phones_or_tokens(walked):
    logs = walked["logs"]

    # The logs were really captured: each app wrote its own lines.
    assert "path=/assistant/bookings status=200" in logs
    assert "path=/calendar/[redacted] status=200" in logs
    assert "/queue/checkin" in logs and "/queue/my-bookings" in logs
    assert "result=slot_taken" in logs
    for line in logs.splitlines():
        assert_nobody_is_named(line, f"the server log line {line[:160]!r}")
    assert "Traceback" not in logs and "sqlalchemy" not in logs.lower()
    assert "phone=" not in logs, "a query string with a phone number reached the access log"
    assert "/queue/my-bookings?[redacted]" in logs


# --- logs written inside the apps, at every level ---------------------------------


def test_no_logger_writes_names_phones_notes_or_tokens(caplog, monkeypatch):
    """The same check in-process, where DEBUG and INFO lines that a server hides are visible."""
    from app.assistant_main import app as assistant_app
    from app.calendar_main import app as calendar_app
    from app.database import SessionLocal
    from app.main import app as main_app
    from app.services import email
    from app.services.booking import get_or_create_shop_status
    from app.services.waitlist import release_slot

    monkeypatch.setenv("ASSISTANT_API_KEY", API_KEY)
    monkeypatch.setenv("CALENDAR_FEED_TOKEN", CALENDAR_TOKEN)
    monkeypatch.setenv("SUISSELINE_WEBHOOK_SECRET", WEBHOOK_SECRET)
    monkeypatch.setattr(email, "EMAIL_DRY_RUN", True)
    with SessionLocal() as db:
        get_or_create_shop_status(db).notification_emails = "private-owner@example.com"
        db.commit()
    caplog.set_level(logging.DEBUG)
    name, phone, note, day = "Zebulon Quartzwhistle", "+41795550142", "Allergic to lavender", "2026-12-02"
    site, assistant = TestClient(main_app), TestClient(assistant_app)
    key = {"X-API-Key": API_KEY}

    assert site.post("/queue/checkin", json={"name": name, "phone": "079 555 01 42", "note": note,
                                             "appointment_date": day, "appointment_time": "10:00"}).status_code == 200
    assert site.post("/queue/checkin", json={"name": name + "\n", "phone": "5550142", "appointment_date": day,
                                             "appointment_time": "10:00"}).status_code == 422
    assert site.get("/queue/my-bookings", params={"phone": phone}).status_code == 200
    assert site.post("/api/login", json={"username": "admin", "password": "wrong-" + ADMIN_PASSWORD}).status_code == 401
    for path, body in [
        ("bookings", {"name": name, "phone": phone, "date": day, "time": "11:00", "service_id": "classic-cut"}),
        ("bookings", {"name": name, "phone": "+41795550143", "date": day, "time": "10:00", "service_id": "classic-cut"}),
        ("bookings", {"name": name, "phone": "5550142", "date": day, "time": "12:00", "service_id": "classic-cut"}),
        ("bookings/find", {"phone": phone}),
        ("waitlist", {"name": name, "phone": phone, "service_id": "classic-cut", "date": day, "from": "14:00",
                      "to": "15:00", "consent": True}),
        ("bookings/cancel", {"phone": phone, "date": day, "time": "11:00"}),
    ]:
        assert assistant.post(f"/assistant/{path}", json=body, headers=key).status_code == 200
    with SessionLocal() as db:
        assert len(release_slot(db, date.fromisoformat(day), datetime.strptime("14:00", "%H:%M").time()).offers) == 1
    assert assistant.post("/assistant/waitlist/accept", json={"phone": phone, "date": day, "time": "14:00"},
                          headers=key).json()["ok"] is True
    assert assistant.post("/assistant/info", headers={"X-API-Key": "wrong-" + API_KEY}).status_code == 401
    raw = json.dumps({"callId": "log-call", "extracted": {"name": name, "phone": "+41795550144", "service_id": "classic-cut",
                                                         "date": day, "time": "10:00"}}).encode()
    signature = hmac.new(WEBHOOK_SECRET.encode(), raw, hashlib.sha256).hexdigest()
    assert assistant.post("/assistant/webhook/call-completed", content=raw,
                          headers={"X-Signature": signature}).json()["status"] == "follow_up"
    with TestClient(calendar_app) as calendar:
        feed = calendar.get(f"/calendar/{CALENDAR_TOKEN}.ics")
        assert calendar.get(f"/calendar/{CALENDAR_TOKEN[:-1]}x.ics").status_code == 404
    assert feed.status_code == 200 and feed.text.count("BEGIN:VEVENT") == 2

    # httpx is this test's own client, logging the requests it sends. It is not the app.
    written = "\n".join(
        f"{record.name} {record.getMessage()} {record.exc_text or ''}"
        for record in caplog.records
        if record.name != "httpx"
    )
    assert "barbershop.email" in written, "the email dry run must have logged something"
    assert "path=/assistant/bookings result=slot_taken" in written
    for private in ("Zebulon", "Quartzwhistle", "555014", "555 01 4", "lavender", "private-owner", *SECRETS):
        assert private not in written, f"a log line contains {private!r}"
        assert private not in feed.text, f"the calendar feed contains {private!r}"


def test_a_real_email_failure_is_logged_without_the_address_or_the_message(caplog, monkeypatch):
    from app.services import email

    def fail(*_args, **_kwargs):
        raise httpx.ConnectError("could not reach the provider for private-owner@example.com")

    monkeypatch.setattr(email, "EMAIL_DRY_RUN", False)
    monkeypatch.setattr(email, "EMAIL_PROVIDER", "resend")
    monkeypatch.setattr(email, "RESEND_API_KEY", "fake-provider-key-Kd8SnV3w")
    monkeypatch.setattr(email.httpx, "post", fail)
    caplog.set_level(logging.DEBUG)

    sent = email.send_admin_checkin_notification(
        "Zebulon Quartzwhistle", "+41795550142", 1, "10:00:00", "Allergic to lavender",
        recipients=["private-owner@example.com"],
    )

    written = "\n".join(f"{record.getMessage()} {record.exc_text or ''}" for record in caplog.records)
    assert sent is False
    assert "email" in written.lower()
    for private in ("Zebulon", "Quartzwhistle", "5550142", "lavender", "private-owner", "fake-provider-key"):
        assert private not in written


# --- the preflight's three known failures -----------------------------------------


@pytest.fixture(scope="module")
def preflight():
    spec = importlib.util.spec_from_file_location("preflight_regression", PREFLIGHT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_check(preflight, capsys, check, *arguments):
    report = preflight.Report()
    check(report, *arguments)
    return report, capsys.readouterr().out


@pytest.mark.parametrize(
    "url",
    [
        "sqlite:///./barbershop.db",
        "sqlite:///barbershop.db",
        "sqlite:////Users/someone/Projects/my-app/barbershop.db",
        "sqlite:////tmp/BarberShop.DB",
        "sqlite:////tmp/barbershop.db?mode=ro",
        "sqlite:////tmp/old-barbershop.db.backup",
    ],
)
def test_failure_1_a_database_url_naming_barbershop_db(preflight, capsys, monkeypatch, tmp_path, url):
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.chdir(tmp_path)

    report, printed = run_check(preflight, capsys, preflight.check_database)

    assert report.failed
    assert "FAIL  DATABASE_URL: names barbershop.db, which may hold real data" in printed
    assert "seed_demo.py" in printed, "the fix must say how to make a demo database"
    assert "SKIP  Schema" in printed and "SKIP  Demo data" in printed
    assert url not in printed and "/Users/someone" not in printed
    assert not (tmp_path / "barbershop.db").exists(), "the file must never be opened or created"

    # The seed script refuses the same names, so the fix cannot recreate the problem.
    seed_spec = importlib.util.spec_from_file_location("seed_regression", SEED)
    seed_module = importlib.util.module_from_spec(seed_spec)
    seed_spec.loader.exec_module(seed_module)
    assert seed_module.refusal() == "DATABASE_URL names barbershop.db, which may hold real data."


def test_failure_1_the_app_default_is_the_dangerous_name(preflight, capsys, monkeypatch):
    """With DATABASE_URL unset the apps fall back to ./barbershop.db, so unset must fail too."""
    monkeypatch.delenv("DATABASE_URL", raising=False)

    report, printed = run_check(preflight, capsys, preflight.check_database)

    assert report.failed and "FAIL  DATABASE_URL: not set" in printed
    source = (REPO_ROOT / "app" / "database.py").read_text(encoding="utf-8")
    assert 'os.getenv("DATABASE_URL", "sqlite:///./barbershop.db")' in source


class StubbornServer:
    """Stands in for a tunnel that is not ready: it answers badly, or not at all."""

    def __init__(self, mode: str):
        outer_mode = mode

        class Handler(BaseHTTPRequestHandler):
            def _respond(self):
                if outer_mode == "silent":
                    clock.sleep(5)
                    return
                status = {"bad-gateway": 502, "cloudflare-1033": 530, "html-200": 200}[outer_mode]
                body = b"<html>Cloudflare Tunnel error</html>"
                self.send_response(status)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_GET = do_POST = _respond

            def log_message(self, *_args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.mark.parametrize("mode", ["closed-port", "silent", "bad-gateway", "cloudflare-1033", "html-200"])
def test_failure_2_a_public_address_that_is_not_answering(preflight, capsys, monkeypatch, mode):
    monkeypatch.setattr(preflight, "REQUEST_TIMEOUT_SECONDS", 1)
    server = None if mode == "closed-port" else StubbornServer(mode)
    url = f"http://127.0.0.1:{free_port()}" if server is None else server.url
    started = clock.perf_counter()

    try:
        report, printed = run_check(
            preflight, capsys, preflight.check_application, "Public address", url, API_KEY, preflight.START_TUNNEL
        )
    finally:
        if server is not None:
            server.close()

    assert report.failed, printed
    assert "FAIL  Public address" in printed
    assert "PASS  Public address: right key" not in printed
    if mode in ("closed-port", "silent"):
        assert "nothing is answering" in printed
        assert "cloudflared tunnel --url http://localhost:8003" in printed, "the fix must say how to start the tunnel"
    assert clock.perf_counter() - started < 10, "the check must give up, not wait for the tunnel"
    assert API_KEY not in printed


def test_failure_2_through_the_command_line(preflight, capsys, monkeypatch):
    """The whole command: an https address with nothing behind it ends in NOT READY."""
    monkeypatch.setattr(preflight, "REQUEST_TIMEOUT_SECONDS", 1)

    report, printed = run_check(
        preflight, capsys, preflight.check_public_address, f"https://127.0.0.1:{free_port()}", API_KEY
    )

    assert report.failed
    assert "FAIL  Public address: nothing is answering" in printed
    assert "PASS  Public address: latency" not in printed


def shifted_copy(tmp_path: Path, days_back: int) -> Path:
    """A seeded database as it looks `days_back` days after it was seeded."""
    database = tmp_path / f"seeded-{days_back}-days-ago.db"
    seed(database)
    connection = sqlite3.connect(database)
    connection.execute("UPDATE queue_entries SET queue_date = date(queue_date, ?)", (f"-{days_back} days",))
    connection.execute("UPDATE waitlist_entries SET requested_date = date(requested_date, ?)", (f"-{days_back} days",))
    connection.commit()
    connection.close()
    return database


@pytest.mark.parametrize(
    "days_back, fails, warns",
    [(0, False, False), (1, False, False), (2, True, False), (3, True, True), (30, True, True)],
    ids=["fresh", "one-day-old", "two-days-old", "three-days-old", "a-month-old"],
)
def test_failure_3_a_stale_seed(preflight, capsys, monkeypatch, tmp_path, days_back, fails, warns):
    """The seed puts its appointments on days +1 to +3 and its waitlist entry on day +1.

    Two days later the waitlist entry is in the past, so the demo cannot be shown and the
    preflight fails. A day after that the earliest appointment is more than a day old and
    the preflight also names the cause. Both point at the seed script.
    """
    database = shifted_copy(tmp_path, days_back)
    before = database.read_bytes()
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database}")

    report, printed = run_check(preflight, capsys, preflight.check_database)

    assert report.failed is fails, printed
    if fails:
        assert "FAIL  Demo data:" in printed and "0 open waitlist entries" in printed
        assert "Fix:" in printed and "seed_demo.py" in printed
    else:
        assert "PASS  Demo data" in printed
    if warns:
        assert "WARN  Seeded dates: the earliest appointment is more than a day old." in printed
    else:
        assert "PASS  Seeded dates: still current" in printed
    assert database.read_bytes() == before, "the preflight only reads"


# --- the migration statements in README.md ----------------------------------------


def readme_statements() -> list[str]:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    blocks = re.findall(r"```sql\n(.*?)```", readme, flags=re.DOTALL)
    return [line.strip() for block in blocks for line in block.splitlines() if line.strip().upper().startswith("ALTER")]


def old_database(path: Path):
    """Today's tables, minus the columns the README's statements add."""
    from app import models  # noqa: F401
    from app.database import Base

    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(bind=engine)
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO customers (name, phone) VALUES ('Old Row', '+41790000001')"))
        connection.execute(text(
            "INSERT INTO queue_entries (customer_id, queue_date, position, status, appointment_time) "
            "VALUES (1, '2026-12-02', 1, 'waiting', '10:00:00.000000')"
        ))
        for table, column in [("queue_entries", "service_id"), ("queue_entries", "duration_minutes"),
                              ("call_requests", "suggested_times"), ("call_requests", "suggested_as_of")]:
            connection.execute(text(f"ALTER TABLE {table} DROP COLUMN {column}"))
    return engine


def test_the_readme_lists_the_statements(tmp_path):
    statements = readme_statements()

    assert len(statements) == 9
    assert sum("IF NOT EXISTS" not in statement and "ALTER TABLE" in statement for statement in statements) == 4
    assert all(statement.endswith(";") for statement in statements)


def test_every_sqlite_migration_statement_in_the_readme_runs(tmp_path):
    from app.schema_check import DatabaseSchemaOutOfDate, require_current_schema

    engine = old_database(tmp_path / "old.db")
    with pytest.raises(DatabaseSchemaOutOfDate) as outdated:
        require_current_schema(engine)
    sqlite_statements = [s for s in readme_statements() if "ALTER TABLE" in s and "IF NOT EXISTS" not in s]
    # The app prints the same statements when it refuses to start.
    for statement in sqlite_statements[:2]:
        assert statement in str(outdated.value)

    with engine.begin() as connection:
        for statement in sqlite_statements:
            connection.execute(text(statement))

    require_current_schema(engine)
    with engine.connect() as connection:
        row = connection.execute(text("SELECT service_id, duration_minutes FROM queue_entries")).one()
        assert tuple(row) == (None, None), "rows that existed before keep working, with empty new columns"
        assert connection.execute(text("SELECT suggested_times, suggested_as_of FROM call_requests")).fetchall() == []
    # Running them a second time fails loudly instead of corrupting anything.
    with pytest.raises(Exception), engine.begin() as connection:
        connection.execute(text(sqlite_statements[0]))
    engine.dispose()


def test_an_app_starts_on_a_migrated_database_and_refuses_an_old_one(tmp_path):
    database = tmp_path / "old.db"
    old_database(database).dispose()
    script = "import app.main"
    environment = {**app_environment(database), "PYTHONPATH": str(REPO_ROOT)}

    refused = subprocess.run([sys.executable, "-c", script], cwd=REPO_ROOT, env=environment,
                             capture_output=True, text=True, timeout=60)
    assert refused.returncode != 0
    assert "ALTER TABLE queue_entries ADD COLUMN service_id VARCHAR;" in refused.stderr

    connection = sqlite3.connect(database)
    for statement in readme_statements():
        if "ALTER TABLE" in statement and "IF NOT EXISTS" not in statement:
            connection.execute(statement)
    connection.commit()
    connection.close()
    started = subprocess.run([sys.executable, "-c", script], cwd=REPO_ROOT, env=environment,
                             capture_output=True, text=True, timeout=60)
    assert started.returncode == 0, started.stderr


def test_every_postgres_statement_has_the_same_effect_as_its_sqlite_twin(tmp_path):
    """Postgres itself is not available here. Each Postgres statement is run on SQLite
    with only its Postgres-only words removed, and must add the same column."""
    from app.schema_check import require_current_schema

    postgres = [s for s in readme_statements() if "IF NOT EXISTS" in s and "ALTER TABLE" in s]
    sqlite_twins = [s for s in readme_statements() if "ALTER TABLE" in s and "IF NOT EXISTS" not in s]
    assert len(postgres) == len(sqlite_twins) == 4

    def target(statement):
        return re.search(r"ALTER TABLE (\w+) ADD COLUMN (?:IF NOT EXISTS )?(\w+)", statement).groups()

    assert [target(s) for s in postgres] == [target(s) for s in sqlite_twins]
    engine = old_database(tmp_path / "old.db")
    with engine.begin() as connection:
        for statement in postgres:
            connection.execute(text(statement.replace("IF NOT EXISTS ", "")))
    require_current_schema(engine)
    engine.dispose()

    enum_statements = [s for s in readme_statements() if s.startswith("ALTER TYPE")]
    assert enum_statements == ["ALTER TYPE queuestatus ADD VALUE IF NOT EXISTS 'cancelled';"]
    from app.models import QueueStatus

    assert QueueStatus.cancelled.value == "cancelled"
    assert QueueStatus.__name__.lower() == "queuestatus"
