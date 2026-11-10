"""The read-only calendar feed: secrecy, RFC 5545 shape, privacy and no writes."""
import http.client
import importlib
import importlib.util
import itertools
import logging
import os
import re
import socket
import subprocess
import sys
import time as wall_clock
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select

from app.database import Base, SessionLocal, engine
from app.models import Customer, QueueEntry, QueueStatus, ShopStatus

REPOSITORY = Path(__file__).resolve().parents[1]
TOKEN = "calendar-test-token-abcdefghijklmnop"
URL = f"/calendar/{TOKEN}.ics"
TODAY = date(2026, 12, 1)
ACTIVE = [QueueStatus.waiting, QueueStatus.next, QueueStatus.in_progress, QueueStatus.done]

_numbers = itertools.count(1)


@pytest.fixture
def feed_app():
    return importlib.import_module("app.calendar_main").app


@pytest.fixture
def client(feed_app, monkeypatch):
    monkeypatch.setenv("CALENDAR_FEED_TOKEN", TOKEN)
    return TestClient(feed_app)


def add_appointment(
    day=TODAY,
    at=time(10, 0),
    status=QueueStatus.waiting,
    service_id=None,
    duration=None,
    name="Test Customer",
    phone=None,
    note=None,
    updated_at=None,
):
    number = next(_numbers)
    with SessionLocal() as db:
        customer = Customer(name=name, phone=phone or f"+4179{number:07d}")
        db.add(customer)
        db.flush()
        entry = QueueEntry(
            customer_id=customer.id,
            queue_date=day,
            position=number,
            status=status,
            note=note,
            appointment_time=at,
            service_id=service_id,
            duration_minutes=duration,
        )
        if updated_at is not None:
            entry.updated_at = updated_at
        db.add(entry)
        db.commit()
        return entry.id


def uid(entry_id):
    return f"queue-{entry_id}@phone-assistant.invalid"


def parse_calendar(body):
    """A deliberately strict reader: anything RFC 5545 forbids here is a test failure."""
    assert body.endswith(b"\r\n")
    logical = []
    for raw in body[:-2].split(b"\r\n"):
        assert b"\n" not in raw and b"\r" not in raw, raw
        assert len(raw) <= 75, raw
        text = raw.decode("utf-8")  # A fold must never split a multi-byte character.
        if text.startswith((" ", "\t")):
            assert logical, "a continuation line came first"
            logical[-1] += text[1:]
        else:
            logical.append(text)

    assert logical[0] == "BEGIN:VCALENDAR"
    assert logical[-1] == "END:VCALENDAR"
    header, events, current = {}, [], None
    for line in logical[1:-1]:
        name, separator, value = line.partition(":")
        assert separator and re.fullmatch(r"[A-Z0-9-]+", name), line
        if line == "BEGIN:VEVENT":
            assert current is None
            current = {}
        elif line == "END:VEVENT":
            assert current is not None
            events.append(current)
            current = None
        elif current is not None:
            assert name not in current, line
            current[name] = value
        else:
            assert name not in header, line
            header[name] = value
    assert current is None
    return header, events


def events_of(client):
    response = client.get(URL)
    assert response.status_code == 200
    return parse_calendar(response.content)[1]


# --- The token -----------------------------------------------------------------------


@pytest.mark.parametrize("configured", [None, "", "x" * 23])
def test_unset_or_short_token_fails_closed(feed_app, monkeypatch, configured):
    add_appointment()
    if configured is None:
        monkeypatch.delenv("CALENDAR_FEED_TOKEN", raising=False)
    else:
        monkeypatch.setenv("CALENDAR_FEED_TOKEN", configured)
    client = TestClient(feed_app)

    assert client.get(f"/calendar/{configured or 'anything'}.ics").status_code == 404
    assert client.get("/calendar/.ics").status_code == 404


def test_token_of_exactly_24_characters_is_accepted(feed_app, monkeypatch):
    monkeypatch.setenv("CALENDAR_FEED_TOKEN", "y" * 24)
    assert TestClient(feed_app).get(f"/calendar/{'y' * 24}.ics").status_code == 200


@pytest.mark.parametrize(
    "path",
    [
        f"/calendar/{TOKEN}x.ics",
        f"/calendar/{TOKEN[:-1]}.ics",
        "/calendar/wrong.ics",
        "/calendar/caf%C3%A9.ics",
        "/calendar/.ics",
        "/calendar/",
        f"/calendar/{TOKEN}",
        f"/calendar/{TOKEN}.ics/",
    ],
)
def test_wrong_or_missing_token_is_404(client, path):
    response = client.get(path, follow_redirects=False)
    assert response.status_code == 404


def test_wrong_token_looks_exactly_like_an_unknown_path(client):
    wrong = client.get("/calendar/wrong.ics")
    unknown = client.get("/no-such-page")
    assert wrong.status_code == unknown.status_code == 404
    assert wrong.content == unknown.content


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
def test_other_methods_do_not_reveal_the_route(client, method):
    assert client.request(method, URL).status_code == 404


# --- Shape of the calendar -----------------------------------------------------------


def test_valid_request_returns_a_well_formed_calendar(client):
    first = add_appointment(service_id="classic-cut", duration=30)
    second = add_appointment(at=time(11, 0), service_id="beard-sculpt", duration=20)

    response = client.get(URL)

    assert response.status_code == 200
    header, events = parse_calendar(response.content)
    assert header["VERSION"] == "2.0"
    assert header["PRODID"]
    assert header["CALSCALE"] == "GREGORIAN"
    assert header["METHOD"] == "PUBLISH"
    assert header["X-WR-CALNAME"] == "Demo Barbershop"
    assert [event["UID"] for event in events] == [uid(first), uid(second)]
    assert "VTIMEZONE" not in response.text
    assert b"\n" not in response.content.replace(b"\r\n", b"")


def test_empty_calendar_is_still_well_formed(client):
    assert events_of(client) == []


def test_response_headers(client):
    response = client.get(URL, headers={"Origin": "https://example.com"})
    assert response.headers["content-type"] == "text/calendar; charset=utf-8"
    assert response.headers["cache-control"] == "no-store"
    assert not [name for name in response.headers if name.lower().startswith("access-control-")]


def test_long_text_is_folded_at_75_octets_without_splitting_characters(client, monkeypatch):
    feed = importlib.import_module("app.services.calendar_feed")
    name = "Überlanger Haarschnitt mit Rasur " * 8
    monkeypatch.setitem(feed.SERVICES, "long-one", {"name": name})
    add_appointment(service_id="long-one")

    response = client.get(URL)

    assert max(len(line) for line in response.content.split(b"\r\n")) <= 75
    assert any(line.startswith(b" ") for line in response.content.split(b"\r\n"))
    assert parse_calendar(response.content)[1][0]["SUMMARY"] == name


def test_text_is_escaped_per_rfc_5545(client, monkeypatch):
    feed = importlib.import_module("app.services.calendar_feed")
    monkeypatch.setitem(feed.SERVICES, "odd-one", {"name": "Cut, Wash; Back\\slash\nLine"})
    add_appointment(service_id="odd-one")

    assert events_of(client)[0]["SUMMARY"] == "Cut\\, Wash\\; Back\\\\slash\\nLine"


# --- Which appointments appear -------------------------------------------------------


def test_only_active_statuses_appear(client):
    active = {add_appointment(status=status) for status in ACTIVE}
    add_appointment(status=QueueStatus.cancelled)
    add_appointment(status=QueueStatus.no_show)

    assert {event["UID"] for event in events_of(client)} == {uid(entry) for entry in active}


def test_a_cancelled_appointment_disappears_on_the_next_fetch(client):
    entry_id = add_appointment()
    assert len(events_of(client)) == 1

    with SessionLocal() as db:
        db.get(QueueEntry, entry_id).status = QueueStatus.cancelled
        db.commit()

    assert events_of(client) == []


def test_window_runs_from_yesterday_through_60_days_ahead(client):
    add_appointment(day=TODAY - timedelta(days=2))
    inside = {
        add_appointment(day=TODAY - timedelta(days=1)),
        add_appointment(day=TODAY),
        add_appointment(day=TODAY + timedelta(days=60)),
    }
    add_appointment(day=TODAY + timedelta(days=61))

    assert {event["UID"] for event in events_of(client)} == {uid(entry) for entry in inside}


def test_window_follows_the_shop_date_not_utc(client, set_clock):
    set_clock(datetime(2026, 12, 1, 23, 30, tzinfo=timezone.utc))  # 00:30 on 2 Dec in Zurich
    add_appointment(day=date(2026, 11, 30))
    inside = add_appointment(day=date(2026, 12, 1))

    assert [event["UID"] for event in events_of(client)] == [uid(inside)]


def test_events_are_capped_at_500_keeping_the_earliest(client):
    with SessionLocal() as db:
        customer = Customer(name="Bulk", phone="+41790000000")
        db.add(customer)
        db.flush()
        entries = [
            QueueEntry(
                customer_id=customer.id,
                queue_date=TODAY,
                position=index + 1,
                status=QueueStatus.waiting,
                appointment_time=time(9 + index // 60, index % 60),
            )
            for index in range(505)
        ]
        db.add_all(entries)
        db.commit()
        ids = [entry.id for entry in entries]

    assert [event["UID"] for event in events_of(client)] == [uid(entry) for entry in ids[:500]]


# --- Times ---------------------------------------------------------------------------


def test_long_service_ends_after_its_duration(client):
    add_appointment(day=date(2026, 12, 2), service_id="cut-beard-combo", duration=60)
    event = events_of(client)[0]
    assert (event["DTSTART"], event["DTEND"]) == ("20261202T090000Z", "20261202T100000Z")


def test_appointment_without_duration_uses_the_slot_length(client):
    with SessionLocal() as db:
        db.add(ShopStatus(id=1, slot_duration_minutes=45))
        db.commit()
    add_appointment(day=date(2026, 12, 2))
    event = events_of(client)[0]
    assert (event["DTSTART"], event["DTEND"]) == ("20261202T090000Z", "20261202T094500Z")


def test_slot_length_falls_back_when_no_settings_row_exists(client):
    add_appointment(day=date(2026, 12, 2))
    assert events_of(client)[0]["DTEND"] == "20261202T093000Z"


@pytest.mark.parametrize(
    "day, at, minutes, start, end",
    [
        (date(2027, 3, 27), time(10, 0), 30, "20270327T090000Z", "20270327T093000Z"),
        (date(2027, 3, 28), time(10, 0), 30, "20270328T080000Z", "20270328T083000Z"),
        (date(2027, 3, 28), time(1, 30), 60, "20270328T003000Z", "20270328T013000Z"),
        (date(2027, 10, 30), time(10, 0), 30, "20271030T080000Z", "20271030T083000Z"),
        (date(2027, 10, 31), time(10, 0), 30, "20271031T090000Z", "20271031T093000Z"),
        (date(2027, 10, 31), time(1, 30), 120, "20271030T233000Z", "20271031T013000Z"),
    ],
)
def test_daylight_saving_days_match_zurich(client, set_clock, day, at, minutes, start, end):
    set_clock(datetime(day.year, day.month, day.day, 6, tzinfo=timezone.utc) - timedelta(days=3))
    add_appointment(day=day, at=at, duration=minutes)
    event = events_of(client)[0]
    assert (event["DTSTART"], event["DTEND"]) == (start, end)


def test_uid_is_stable_and_timestamps_come_from_updated_at(client):
    entry_id = add_appointment(updated_at=datetime(2026, 11, 20, 10, 15, 30))

    first, second = events_of(client)[0], events_of(client)[0]

    assert first["UID"] == second["UID"] == uid(entry_id)
    assert first["DTSTAMP"] == first["LAST-MODIFIED"] == "20261120T091530Z"


# --- Privacy -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "service_id, expected",
    [("signature-fade", "Signature Fade"), (None, "Appointment"), ("no-such-service", "Appointment")],
)
def test_summary_is_the_service_name_only(client, service_id, expected):
    add_appointment(service_id=service_id, name="Zyxwvut Qqqqqq", note="SECRETNOTE-7788")
    event = events_of(client)[0]
    assert event["SUMMARY"] == expected
    assert event["DESCRIPTION"] == ""


def test_no_personal_data_anywhere_in_the_response(client):
    name, phone, note = "Zyxwvut Qqqqqq", "+41790009999", "SECRETNOTE-7788 mail zyx@example.org"
    add_appointment(service_id="classic-cut", duration=30, name=name, phone=phone, note=note)

    response = client.get(URL)
    unfolded = response.content.replace(b"\r\n ", b"").decode("utf-8")

    for secret in (name, "Zyxwvut", "Qqqqqq", phone, "790009999", "SECRETNOTE", "zyx@example.org", TOKEN):
        assert secret not in response.text
        assert secret not in unfolded
    assert not [header for header in response.headers.values() if TOKEN in header or name in header]


# --- Logging -------------------------------------------------------------------------


def test_token_never_reaches_the_logs(client, caplog):
    caplog.set_level(logging.DEBUG)
    add_appointment()

    for path in (URL, f"/calendar/{TOKEN}", f"/calendar/{TOKEN}/", f"/calendar/{TOKEN}x.ics", f"/{TOKEN}"):
        client.get(path, follow_redirects=False)

    # httpx is this test's own client, not the server, and it logs every URL it requests.
    server_side = [record for record in caplog.records if not record.name.startswith(("httpx", "httpcore"))]
    text = "\n".join(record.getMessage() for record in server_side)
    assert TOKEN not in text
    assert re.search(r"path=\S+ status=200 duration_ms=\d+", text)
    assert re.search(r"path=\S+ status=404 duration_ms=\d+", text)


def test_uvicorn_access_log_is_off_for_this_app(feed_app):
    access_log = logging.getLogger("uvicorn.access")
    assert access_log.disabled
    assert not access_log.hasHandlers()


def test_a_real_server_started_as_documented_never_logs_the_token(tmp_path):
    add_appointment()
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    env = {**os.environ, "CALENDAR_FEED_TOKEN": TOKEN}
    log_file = tmp_path / "server.log"

    def status_of(path):
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        try:
            connection.request("GET", path)
            return connection.getresponse().status
        finally:
            connection.close()

    with log_file.open("wb") as sink:
        server = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.calendar_main:app", "--host", "127.0.0.1", "--port", str(port)],
            cwd=REPOSITORY,
            env=env,
            stdout=sink,
            stderr=subprocess.STDOUT,
        )
        try:
            deadline = wall_clock.monotonic() + 30
            while True:
                try:
                    status_of("/not-ready-yet")
                    break
                except OSError:
                    assert wall_clock.monotonic() < deadline, "the server did not start"
                    wall_clock.sleep(0.1)
            assert status_of(URL) == 200
            assert status_of(f"/calendar/{TOKEN}x.ics") == 404
        finally:
            server.terminate()
            server.wait(timeout=15)

    output = log_file.read_text(encoding="utf-8")
    assert "status=200" in output, output  # Proves this output is really the server's log.
    assert TOKEN not in output, output


# --- Isolation -----------------------------------------------------------------------


def test_only_the_feed_route_is_mounted(feed_app):
    assert [route.path for route in feed_app.routes] == ["/calendar/{token}.ics"]


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_docs_are_disabled(client, path):
    assert client.get(path).status_code == 404


@pytest.mark.parametrize("module", ["app.assistant_main", "app.main"])
def test_feed_does_not_exist_on_the_other_apps(module, monkeypatch):
    monkeypatch.setenv("CALENDAR_FEED_TOKEN", TOKEN)
    add_appointment()
    other = importlib.import_module(module).app

    assert TestClient(other).get(URL).status_code == 404


def test_startup_refuses_an_out_of_date_database(feed_app, monkeypatch):
    module = importlib.import_module("app.calendar_main")

    def refuse(_engine):
        raise RuntimeError("out of date")

    monkeypatch.setattr(module, "require_current_schema", refuse)
    with pytest.raises(RuntimeError, match="out of date"):
        with TestClient(feed_app):
            pass


# --- Read-only -----------------------------------------------------------------------


def row_counts():
    with SessionLocal() as db:
        return {table.name: db.scalar(select(func.count()).select_from(table)) for table in Base.metadata.sorted_tables}


def test_a_request_changes_no_rows_and_issues_only_selects(client):
    add_appointment(service_id="classic-cut", duration=30)
    add_appointment(status=QueueStatus.cancelled)
    statements = []

    def record(_connection, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement)

    before = row_counts()
    event.listen(engine, "before_cursor_execute", record)
    try:
        assert client.get(URL).status_code == 200
    finally:
        event.remove(engine, "before_cursor_execute", record)

    assert row_counts() == before
    assert before["shop_status"] == 0  # The feed must not create the settings row.
    assert statements
    assert all(statement.lstrip().upper().startswith("SELECT") for statement in statements), statements


# --- Preflight -----------------------------------------------------------------------


def load_preflight():
    spec = importlib.util.spec_from_file_location("preflight_for_calendar_tests", REPOSITORY / "scripts" / "preflight.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "value, expected",
    [
        (None, "SKIP  CALENDAR_FEED_TOKEN"),
        ("short", "SKIP  CALENDAR_FEED_TOKEN"),
        (TOKEN, f"PASS  CALENDAR_FEED_TOKEN: set, {len(TOKEN)} chars"),
    ],
)
def test_preflight_reports_the_token_without_printing_it(monkeypatch, capsys, value, expected):
    if value is None:
        monkeypatch.delenv("CALENDAR_FEED_TOKEN", raising=False)
    else:
        monkeypatch.setenv("CALENDAR_FEED_TOKEN", value)
    preflight = load_preflight()
    report = preflight.Report()

    preflight.check_calendar_token(report)

    output = capsys.readouterr().out
    assert expected in output
    assert not report.failed
    if value:
        assert value not in output
