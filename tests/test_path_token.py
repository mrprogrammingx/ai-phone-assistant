"""Token-in-the-path routes, for a dashboard that cannot send headers, and capture mode."""
import base64
import http.client
import importlib.util
import json
import logging
import os
import socket
import stat
import subprocess
import sys
import time as wall_clock
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app import auth
from app.assistant_main import app as assistant_app
from app.database import SessionLocal
from app.main import app as main_app
from app.models import QueueEntry

REPOSITORY = Path(__file__).resolve().parents[1]
TOKEN = "fake-path-token-for-tests-0123456789abcdef"
WRONG = "fake-path-token-for-tests-0123456789abcdeX"
KEY = "test-assistant-key"
TOMORROW = "2026-12-02"
PHONE = "+41791234567"
TOOLS = {
    "info": {},
    "availability": {"date": TOMORROW, "service_id": "classic-cut"},
    "bookings": {"name": "Greta Fiction", "phone": PHONE, "date": TOMORROW, "time": "10:00", "service_id": "classic-cut"},
    "bookings/find": {"phone": PHONE},
    "bookings/cancel": {"phone": PHONE, "date": TOMORROW, "time": "10:00"},
    "waitlist": {
        "name": "Greta Fiction",
        "phone": PHONE,
        "service_id": "classic-cut",
        "date": TOMORROW,
        "from": "10:00",
        "to": "12:00",
        "consent": True,
    },
    "waitlist/accept": {"phone": PHONE, "date": TOMORROW, "time": "10:00"},
}

client = TestClient(assistant_app)


@pytest.fixture(autouse=True)
def secrets_configured(monkeypatch):
    monkeypatch.setenv("ASSISTANT_API_KEY", KEY)
    monkeypatch.setenv("SUISSELINE_PATH_TOKEN", TOKEN)
    monkeypatch.delenv("SUISSELINE_CAPTURE_FILE", raising=False)


def url(path, token=TOKEN):
    return f"/assistant/t/{token}/{path}"


# --- The token -----------------------------------------------------------------------


@pytest.mark.parametrize("tool", TOOLS)
def test_every_tool_answers_with_the_right_token_and_no_key(tool):
    response = client.post(url(tool), json=TOOLS[tool])

    assert response.status_code == 200
    assert isinstance(response.json()["ok"], bool)


def test_a_booking_made_through_a_token_route_is_really_stored():
    assert client.post(url("bookings"), json=TOOLS["bookings"]).json()["ok"] is True
    with SessionLocal() as db:
        assert db.query(QueueEntry).count() == 1


def test_the_token_routes_are_exactly_the_header_routes():
    paths = {route.path for route in assistant_app.routes if isinstance(route, APIRoute)}
    header_tools = {path.removeprefix("/assistant/") for path in paths if path.startswith("/assistant/")}
    header_tools = {path for path in header_tools if not path.startswith(("t/", "webhook/"))}
    token_tools = {path.removeprefix("/assistant/t/{token}/") for path in paths if path.startswith("/assistant/t/")}

    assert header_tools == set(TOOLS)
    assert token_tools == set(TOOLS) | {"capture"}


@pytest.mark.parametrize("configured", [None, "", "x" * 31])
@pytest.mark.parametrize("tool", TOOLS)
def test_an_unset_or_short_token_closes_every_route(monkeypatch, tool, configured):
    if configured is None:
        monkeypatch.delenv("SUISSELINE_PATH_TOKEN")
    else:
        monkeypatch.setenv("SUISSELINE_PATH_TOKEN", configured)

    response = client.post(url(tool, token=configured or "anything"), json=TOOLS[tool])

    assert response.status_code == 404


def test_every_token_route_is_guarded_by_the_dependency():
    token_routes = [route for route in assistant_app.routes if isinstance(route, APIRoute) and "/t/{token}/" in route.path]

    assert len(token_routes) == 8
    for route in token_routes:
        assert auth.require_path_token in [dependency.call for dependency in route.dependant.dependencies], route.path


def test_the_dependency_refuses_a_wrong_token_on_its_own():
    with pytest.raises(HTTPException) as refused:
        auth.require_path_token(WRONG)
    assert refused.value.status_code == 404


def test_a_token_of_exactly_32_characters_is_accepted(monkeypatch):
    monkeypatch.setenv("SUISSELINE_PATH_TOKEN", "y" * 32)
    assert client.post(url("info", token="y" * 32), json={}).status_code == 200


@pytest.mark.parametrize("tool", TOOLS)
def test_a_wrong_token_is_404_even_with_the_right_key(tool):
    response = client.post(url(tool, token=WRONG), json=TOOLS[tool], headers={"X-API-Key": KEY})
    assert response.status_code == 404


def test_a_wrong_token_books_nothing():
    client.post(url("bookings", token=WRONG), json=TOOLS["bookings"])
    with SessionLocal() as db:
        assert db.query(QueueEntry).count() == 0


def test_a_wrong_token_looks_exactly_like_an_unknown_path():
    wrong = client.post(url("info", token=WRONG), json={})
    unknown = client.post("/no-such-page", json={})
    assert wrong.status_code == unknown.status_code == 404
    assert wrong.content == unknown.content


@pytest.mark.parametrize(
    "method, path, options",
    [
        ("POST", url("info", token=WRONG), {"content": b"{not json", "headers": {"content-type": "application/json"}}),
        ("POST", url("bookings", token=WRONG), {"json": {"name": "only a name"}}),
        ("GET", url("info", token=WRONG), {}),
        ("PUT", url("bookings", token=WRONG), {"json": {}}),
        ("DELETE", url("bookings", token=WRONG), {}),
        ("POST", url("info", token=WRONG) + "/", {"json": {}}),
        ("POST", url("capture", token=WRONG), {"json": {}}),
        ("POST", "/assistant/t/caf%C3%A9/info", {"json": {}}),
    ],
)
def test_a_wrong_token_never_gets_anything_but_404(method, path, options):
    response = client.request(method, path, follow_redirects=False, **options)
    assert response.status_code == 404
    assert "location" not in response.headers


def test_the_token_is_compared_in_constant_time(monkeypatch):
    compared = []
    real = auth.secrets.compare_digest

    def spy(left, right):
        compared.append((left, right))
        return real(left, right)

    monkeypatch.setattr(auth.secrets, "compare_digest", spy)

    client.post(url("info"), json={})
    client.post(url("info", token=WRONG), json={})

    assert (TOKEN.encode(), TOKEN.encode()) in compared
    assert (WRONG.encode(), TOKEN.encode()) in compared


def test_unreadable_json_with_the_right_token_gets_the_tools_usual_answer():
    response = client.post(url("bookings"), content=b"{not json", headers={"content-type": "application/json"})

    assert response.status_code == 200
    assert response.json()["code"] == "invalid_input"


def test_missing_fields_with_the_right_token_get_the_tools_usual_answer():
    response = client.post(url("bookings"), json={"name": "Greta Fiction"})

    assert response.status_code == 200
    assert response.json()["code"] == "invalid_input"


# --- The header routes are unchanged --------------------------------------------------


@pytest.mark.parametrize("configured", [TOKEN, None])
def test_header_routes_work_whether_or_not_a_token_is_set(monkeypatch, configured):
    if configured is None:
        monkeypatch.delenv("SUISSELINE_PATH_TOKEN")

    assert client.post("/assistant/info", json={}, headers={"X-API-Key": KEY}).json()["ok"] is True
    assert client.post("/assistant/info", json={}).status_code == 401
    assert client.post("/assistant/info", json={}, headers={"X-API-Key": TOKEN}).status_code == 401


def test_the_routes_exist_only_on_the_assistant_app():
    assert not [route.path for route in main_app.routes if "/assistant/t/" in getattr(route, "path", "")]


# --- Logs ------------------------------------------------------------------------------


def server_side_log(caplog):
    # httpx is this test's own client, not the server, and it logs every URL it requests.
    records = [record for record in caplog.records if not record.name.startswith(("httpx", "httpcore"))]
    return "\n".join(record.getMessage() for record in records)


def test_log_timing_and_failure_lines_never_carry_the_token(caplog):
    caplog.set_level(logging.DEBUG)

    client.post(url("info"), json={})
    client.post(url("availability"), json={"date": TOMORROW, "service_id": "no-such-service"})
    client.post(url("bookings"), json={"name": "Greta Fiction"})
    client.post(url("bookings"), content=b"{not json", headers={"content-type": "application/json"})
    client.post(url("no-such-tool"), json={})
    client.post(url("info", token=WRONG), json={})

    text = server_side_log(caplog)
    assert TOKEN not in text
    assert WRONG not in text
    assert "path=/assistant/t/[redacted]/info status=200" in text
    assert "path=/assistant/t/[redacted]/availability result=unknown_service" in text
    assert "path=/assistant/t/[redacted]/bookings result=invalid_input" in text


def test_the_configured_token_is_redacted_even_in_a_mistyped_path(caplog):
    caplog.set_level(logging.DEBUG)

    for path in (f"/assistant/T/{TOKEN}/info", f"/{TOKEN}", f"/assistant//t/{TOKEN}/info"):
        client.post(path, json={})

    assert TOKEN not in server_side_log(caplog)


@pytest.mark.parametrize("path", [f"/assistant/t/{TOKEN}/info", f"/assistant/t/{TOKEN}/info?a=1", f"/x/{TOKEN}"])
def test_the_access_log_filter_removes_the_token(path):
    record = logging.LogRecord(
        "uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d', ("127.0.0.1:1", "POST", path, "1.1", 200), None
    )
    for log_filter in logging.getLogger("uvicorn.access").filters:
        assert log_filter.filter(record)

    assert TOKEN not in record.getMessage()


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def post_status(port, path):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        connection.request("POST", path, body=b"{}", headers={"Content-Type": "application/json"})
        return connection.getresponse().status
    finally:
        connection.close()


def run_server(command, port, tmp_path, paths):
    """Runs a real server with its access log on and returns the statuses and everything logged."""
    log_file = tmp_path / "server.log"
    with log_file.open("wb") as sink:
        server = subprocess.Popen(
            command,
            cwd=REPOSITORY,
            env={**os.environ, "SUISSELINE_PATH_TOKEN": TOKEN, "ASSISTANT_API_KEY": KEY},
            stdout=sink,
            stderr=subprocess.STDOUT,
        )
        try:
            deadline = wall_clock.monotonic() + 30
            while True:
                try:
                    post_status(port, "/not-ready-yet")
                    break
                except OSError:
                    assert wall_clock.monotonic() < deadline, "the server did not start"
                    wall_clock.sleep(0.1)
            statuses = [post_status(port, path) for path in paths]
        finally:
            server.terminate()
            server.wait(timeout=15)
    return statuses, log_file.read_text(encoding="utf-8")


def test_a_real_server_never_writes_the_token_to_its_logs(tmp_path):
    port = free_port()
    command = [sys.executable, "-m", "uvicorn", "app.assistant_main:app", "--host", "127.0.0.1", "--port", str(port)]

    statuses, output = run_server(command, port, tmp_path, [url("info"), url("info", token=WRONG)])

    assert statuses == [200, 404]
    assert '"POST /assistant/t/[redacted]/info HTTP/1.1" 200' in output, output
    assert "path=/assistant/t/[redacted]/info status=200" in output, output
    assert TOKEN not in output, output
    assert WRONG not in output, output


def test_a_crash_on_a_token_route_keeps_the_token_out_of_the_traceback(tmp_path):
    port = free_port()
    program = (
        "import uvicorn\n"
        "import app.routers.assistant as assistant\n"
        "def fail(*_args):\n"
        "    raise RuntimeError('deliberate failure')\n"
        "assistant.get_or_create_shop_status = fail\n"
        f"uvicorn.run('app.assistant_main:app', host='127.0.0.1', port={port})\n"
    )

    statuses, output = run_server([sys.executable, "-c", program], port, tmp_path, [url("info")])

    assert statuses == [500]
    assert "deliberate failure" in output, output  # Proves the traceback really was captured.
    assert "Traceback" in output, output
    assert TOKEN not in output, output


# --- Capture mode ----------------------------------------------------------------------


def lines_of(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_capture_is_off_by_default(tmp_path):
    response = client.post(url("capture"), json={"a": 1})

    assert response.status_code == 404
    assert list(tmp_path.iterdir()) == []


def test_an_empty_capture_setting_keeps_it_off(monkeypatch):
    monkeypatch.setenv("SUISSELINE_CAPTURE_FILE", "")
    assert client.post(url("capture"), json={}).status_code == 404


def test_capture_records_the_body_content_type_and_header_names_only(monkeypatch, tmp_path):
    target = tmp_path / "capture.jsonl"
    monkeypatch.setenv("SUISSELINE_CAPTURE_FILE", str(target))
    body = b'{"transcript": "hello",  "extracted": {"phone": "+41790000123"}}'

    response = client.post(
        url("capture"),
        content=body,
        headers={"Content-Type": "application/json; charset=utf-8", "X-Secret-Header": "header-value-must-not-appear"},
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True, "message": "captured"}
    [line] = lines_of(target)
    assert set(line) == {"body", "content_type", "header_names"}
    assert line["body"] == body.decode()  # Byte for byte, spacing included.
    assert line["content_type"] == "application/json; charset=utf-8"
    assert "x-secret-header" in line["header_names"]
    raw = target.read_text(encoding="utf-8")
    assert "header-value-must-not-appear" not in raw
    assert TOKEN not in raw


def test_capture_creates_the_file_owner_only_and_appends_one_line_per_request(monkeypatch, tmp_path):
    target = tmp_path / "capture.jsonl"
    monkeypatch.setenv("SUISSELINE_CAPTURE_FILE", str(target))
    previous = os.umask(0)
    try:
        client.post(url("capture"), content=b"first")
        client.post(url("capture"), content=b"second\nwith a line break")
    finally:
        os.umask(previous)

    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert [line["body"] for line in lines_of(target)] == ["first", "second\nwith a line break"]


def test_capture_tightens_a_file_that_already_exists(monkeypatch, tmp_path):
    target = tmp_path / "capture.jsonl"
    target.write_text("")
    target.chmod(0o644)
    monkeypatch.setenv("SUISSELINE_CAPTURE_FILE", str(target))

    client.post(url("capture"), content=b"x")

    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_capture_keeps_a_body_that_is_not_text(monkeypatch, tmp_path):
    target = tmp_path / "capture.jsonl"
    monkeypatch.setenv("SUISSELINE_CAPTURE_FILE", str(target))
    body = bytes([0xff, 0xfe, 0x00, 0x41])

    assert client.post(url("capture"), content=body).status_code == 200

    [line] = lines_of(target)
    assert "body" not in line
    assert base64.b64decode(line["body_base64"]) == body


def test_capture_refuses_a_very_large_body(monkeypatch, tmp_path):
    target = tmp_path / "capture.jsonl"
    monkeypatch.setenv("SUISSELINE_CAPTURE_FILE", str(target))

    assert client.post(url("capture"), content=b"x" * (1024 * 1024 + 1)).status_code == 413
    assert not target.exists()


def test_capture_needs_the_right_token(monkeypatch, tmp_path):
    target = tmp_path / "capture.jsonl"
    monkeypatch.setenv("SUISSELINE_CAPTURE_FILE", str(target))

    assert client.post(url("capture", token=WRONG), content=b"x").status_code == 404
    assert not target.exists()


def test_capture_does_not_exist_on_the_header_routes(monkeypatch, tmp_path):
    target = tmp_path / "capture.jsonl"
    monkeypatch.setenv("SUISSELINE_CAPTURE_FILE", str(target))

    assert client.post("/assistant/capture", content=b"x", headers={"X-API-Key": KEY}).status_code == 404
    assert not target.exists()


# --- Preflight -------------------------------------------------------------------------


def load_preflight():
    spec = importlib.util.spec_from_file_location("preflight_for_path_token_tests", REPOSITORY / "scripts" / "preflight.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def path_token_report(capsys):
    preflight = load_preflight()
    report = preflight.Report()
    preflight.check_path_token(report)
    return report, capsys.readouterr().out


def test_preflight_passes_a_good_token_without_printing_it(capsys):
    report, output = path_token_report(capsys)

    assert not report.failed
    assert f"PASS  SUISSELINE_PATH_TOKEN: set, {len(TOKEN)} chars" in output
    assert TOKEN not in output


@pytest.mark.parametrize("value", [None, "", "z" * 31])
def test_preflight_fails_an_unset_or_short_token(monkeypatch, capsys, value):
    if value is None:
        monkeypatch.delenv("SUISSELINE_PATH_TOKEN")
    else:
        monkeypatch.setenv("SUISSELINE_PATH_TOKEN", value)

    report, output = path_token_report(capsys)

    assert report.failed
    assert "FAIL  SUISSELINE_PATH_TOKEN" in output
    if value:
        assert value not in output


@pytest.mark.parametrize(
    "other",
    ["ASSISTANT_API_KEY", "ADMIN_PASSWORD", "SESSION_SECRET_KEY", "SUISSELINE_WEBHOOK_SECRET", "CALENDAR_FEED_TOKEN"],
)
def test_preflight_fails_a_token_that_matches_another_secret(monkeypatch, capsys, other):
    monkeypatch.setenv(other, TOKEN)

    report, output = path_token_report(capsys)

    assert report.failed
    assert "FAIL  SUISSELINE_PATH_TOKEN differs" in output
    assert TOKEN not in output


def public_check(monkeypatch, capsys, responses):
    """Runs the public checks against canned answers: {path: (status, body)}."""
    preflight = load_preflight()
    seen = []

    def fake_request(_base, method, path, key=None):
        seen.append((method, path, key))
        status, body = responses(path)
        return status, body, 12.0

    monkeypatch.setattr(preflight, "_request", fake_request)
    monkeypatch.setattr(preflight, "check_application", lambda *_args, **_kwargs: 12.0)
    monkeypatch.setattr(preflight, "public_address_problem", lambda _url: None)
    report = preflight.Report()
    preflight.check_public_address(report, "https://example.invalid", KEY)
    return report, capsys.readouterr().out, seen


def healthy(path):
    return (200, {"ok": True}) if TOKEN in path else (404, {"detail": "Not Found"})


def test_preflight_public_checks_pass_on_a_healthy_tunnel(monkeypatch, capsys):
    report, output, seen = public_check(monkeypatch, capsys, healthy)

    assert not report.failed
    assert "PASS  Public address: token route" in output
    assert "PASS  Public address: wrong token: 404" in output
    assert TOKEN not in output
    assert ("POST", f"/assistant/t/{TOKEN}/info", None) in seen


def test_preflight_fails_when_the_token_route_does_not_answer(monkeypatch, capsys):
    report, output, _ = public_check(monkeypatch, capsys, lambda _path: (404, None))

    assert report.failed
    assert "FAIL  Public address: token route" in output
    assert TOKEN not in output


def test_preflight_fails_when_a_wrong_token_is_accepted(monkeypatch, capsys):
    report, output, _ = public_check(monkeypatch, capsys, lambda _path: (200, {"ok": True}))

    assert report.failed
    assert "FAIL  Public address: wrong token" in output
    assert TOKEN not in output


def test_preflight_skips_the_token_routes_without_a_usable_token(monkeypatch, capsys):
    monkeypatch.delenv("SUISSELINE_PATH_TOKEN")

    report, output, seen = public_check(monkeypatch, capsys, healthy)

    assert not report.failed
    assert "SKIP  Public address: token route" in output
    assert seen == []
