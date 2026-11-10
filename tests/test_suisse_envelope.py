"""The request envelope Suisse Line wraps around a tool's parameters, on the token routes.

The fixture copies the shape of a real capture from chat test mode, with fake values.
The parameters sit at tool.arguments, all as text. Next to them are the transcript, the
agent's prompt, the conversation, the caller's numbers and the call ids. None of that may
be read, logged, stored or echoed, and the caller's number never replaces the spoken one.
"""
import asyncio
import http.client
import json
import logging
import os
import socket
import subprocess
import sys
import time as wall_clock
from datetime import date, time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.assistant_main import app
from app.database import Base, SessionLocal, engine
from app.models import Customer, QueueEntry, WaitlistEntry
from app.services.envelope import NotToolArguments, tool_arguments
from app.services.waitlist import release_slot
from app.tool_bodies import MAX_TOOL_BODY_BYTES, ToolBodies, is_token_tool_path

KEY = "test-assistant-key"
TOKEN = "fake-path-token-for-tests-0123456789abcdef"
DAY = "2026-12-02"
PHONE = "+41791234567"
REPOSITORY = Path(__file__).resolve().parents[1]

# Everything here except tool.arguments must stay out of logs, responses and the database.
PRIVATE = {
    "transcript": "TRANSCRIPT-SENTINEL the caller said something private",
    "agentPrompt": "PROMPT-SENTINEL you are the demo assistant",
    "agentGreeting": "GREETING-SENTINEL hello and welcome",
    "conversation": "CONVERSATION-SENTINEL a turn of the conversation",
    "phoneNumber": "+41799990001",
    "toNumber": "+41449990002",
    "callId": "CALLID-SENTINEL-0000-0000",
    "toolId": "TOOLID-SENTINEL-0000-0000",
    "toolCallId": "TOOLCALLID-SENTINEL-0000-0000",
}
PRIVATE_FRAGMENTS = [*PRIVATE.values(), "799990001", "449990002", "SENTINEL"]

BOOKING = {"name": "Greta Fiction", "phone": PHONE, "date": DAY, "time": "10:00", "service_id": "classic-cut"}
WAITLIST = {
    "name": "Greta Fiction",
    "phone": PHONE,
    "service_id": "classic-cut",
    "date": DAY,
    "from": "10:00",
    "to": "12:00",
    "consent": "yes",
}
ARGUMENTS = {
    "info": {},
    "availability": {"date": DAY, "service_id": "classic-cut", "from": "10:00", "to": ""},
    "bookings": BOOKING,
    "bookings/find": {"phone": PHONE, "date": ""},
    "bookings/cancel": {"phone": PHONE, "date": DAY, "time": "10:00"},
    "waitlist": WAITLIST,
    "waitlist/accept": {"phone": PHONE, "date": DAY, "time": "10:00"},
}
TOOL_NAMES = {
    "info": "get_business_info",
    "availability": "check_availability",
    "bookings": "create_booking",
    "bookings/find": "find_booking",
    "bookings/cancel": "cancel_booking",
    "waitlist": "join_waitlist",
    "waitlist/accept": "accept_waitlist_offer",
}

client = TestClient(app)


@pytest.fixture(autouse=True)
def secrets_configured(monkeypatch):
    monkeypatch.setenv("ASSISTANT_API_KEY", KEY)
    monkeypatch.setenv("SUISSELINE_PATH_TOKEN", TOKEN)


def envelope(arguments, route="bookings", **changes):
    """The captured shape, with fake values. `changes` replaces top-level keys."""
    body = {
        "event": "tool.invocation",
        "agentId": 1,
        "agentName": "Fictional Test Agent",
        "callId": PRIVATE["callId"],
        "timestamp": "2026-12-01T09:00:00.000Z",
        "mode": "chat-test",
        "phoneNumber": PRIVATE["phoneNumber"],
        "toNumber": PRIVATE["toNumber"],
        "startedAt": "2026-12-01T08:59:00.000Z",
        "answeredAt": None,
        "direction": "inbound",
        "status": "fake-status",
        "transcript": PRIVATE["transcript"],
        "agentPrompt": PRIVATE["agentPrompt"],
        "agentGreeting": PRIVATE["agentGreeting"],
        "tool": {
            "name": TOOL_NAMES[route],
            "id": PRIVATE["toolId"],
            "callId": PRIVATE["toolCallId"],
            "arguments": arguments,
            "conversation": [
                {"role": "assistant", "text": PRIVATE["agentGreeting"]},
                {"role": "user", "text": PRIVATE["conversation"]},
            ],
        },
    }
    body.update(changes)
    return body


def suisse_headers(tool):
    return {
        "x-suisse-agent-id": "1",
        "x-suisse-call-id": PRIVATE["callId"],
        "x-suisse-mode": "chat-test",
        "x-suisse-timestamp": "2026-12-01T09:00:00.000Z",
        "x-suisse-tool": TOOL_NAMES[tool],
        "x-suisse-tool-call-id": PRIVATE["toolCallId"],
    }


def token_url(tool, token=TOKEN):
    return f"/assistant/t/{token}/{tool}"


def post(tool, body, **kwargs):
    response = client.post(token_url(tool), json=body, headers=suisse_headers(tool), **kwargs)
    assert response.status_code == 200, response.text
    return response


def call(tool, arguments, **changes):
    return post(tool, envelope(arguments, tool, **changes)).json()


def post_raw(tool, content, headers=None):
    response = client.post(token_url(tool), content=content, headers={"content-type": "application/json", **(headers or {})})
    assert response.status_code == 200, response.text
    return response.json()


def stored():
    with SessionLocal() as db:
        return db.query(QueueEntry).count(), db.query(WaitlistEntry).count()


def stored_phones():
    with SessionLocal() as db:
        return {customer.phone for customer in db.query(Customer)} | {entry.phone for entry in db.query(WaitlistEntry)}


def database_text():
    with engine.connect() as connection:
        return "\n".join(
            repr(tuple(row)) for table in Base.metadata.sorted_tables for row in connection.execute(table.select())
        )


def reset_database():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


def offer_the_waitlist_ten_oclock():
    with SessionLocal() as db:
        assert len(release_slot(db, date.fromisoformat(DAY), time(10, 0)).offers) == 1


# --- The pure function ----------------------------------------------------------------


def test_the_arguments_object_is_returned_as_it_is():
    arguments = {"name": "Greta Fiction", "phone": PHONE}
    body = envelope(arguments)

    assert tool_arguments(body) is arguments


@pytest.mark.parametrize(
    "body",
    [{}, {"name": "Greta Fiction", "phone": PHONE}, {"arguments": {"phone": PHONE}}, [], ["tool"], "tool", 5, None],
    ids=repr,
)
def test_a_body_without_a_tool_key_is_flat_and_returned_as_it_is(body):
    assert tool_arguments(body) is body


@pytest.mark.parametrize(
    "tool",
    [None, "create_booking", 5, True, [], [{"arguments": {}}], {}, {"name": "create_booking"}, {"args": {}}],
    ids=repr,
)
def test_a_tool_key_without_an_arguments_object_is_refused(tool):
    with pytest.raises(NotToolArguments):
        tool_arguments({"tool": tool})


@pytest.mark.parametrize("arguments", [None, "name=Test", 5, 1.5, True, [], [{"phone": PHONE}]], ids=repr)
def test_arguments_that_are_not_an_object_are_refused(arguments):
    with pytest.raises(NotToolArguments):
        tool_arguments(envelope(arguments))


def test_nothing_from_the_envelope_is_added_to_the_arguments():
    body = envelope({"name": "Greta Fiction"})

    assert tool_arguments(body) == {"name": "Greta Fiction"}
    assert body["phoneNumber"] == PRIVATE["phoneNumber"]  # unchanged and unused


def test_the_function_reads_only_tool_and_arguments():
    class Untouchable(dict):
        def __getitem__(self, key):
            if key not in ("tool", "arguments"):
                raise AssertionError(f"read {key}")
            return super().__getitem__(key)

        def get(self, key, default=None):
            if key not in ("tool", "arguments"):
                raise AssertionError(f"read {key}")
            return super().get(key, default)

    arguments = {"phone": PHONE}
    tool = Untouchable(name="x", callId="y", conversation=[], arguments=arguments)
    body = Untouchable(transcript="t", phoneNumber="+41799990001", tool=tool)

    assert tool_arguments(body) is arguments


# --- Which routes -----------------------------------------------------------------------


@pytest.mark.parametrize("tool", ARGUMENTS)
@pytest.mark.parametrize("token", [TOKEN, "any-token"])
def test_the_seven_token_tool_paths_are_adapted(tool, token):
    assert is_token_tool_path(token_url(tool, token)) is True


@pytest.mark.parametrize(
    "path",
    [
        "/assistant/info",
        "/assistant/bookings",
        "/assistant/waitlist/accept",
        f"/assistant/t/{TOKEN}/capture",
        f"/assistant/t/{TOKEN}",
        f"/assistant/t/{TOKEN}/",
        f"/assistant/t/{TOKEN}/info/",
        f"/assistant/t/{TOKEN}/bookings/find/x",
        f"/assistant/t/{TOKEN}/webhook/twilio",
        "/assistant/webhook/twilio",
        "/assistant/t/bookings",
        "/info",
    ],
)
def test_other_paths_are_left_alone(path):
    assert is_token_tool_path(path) is False


def test_the_header_routes_never_unwrap_an_envelope():
    response = client.post("/assistant/bookings", json=envelope(BOOKING), headers={"X-API-Key": KEY})

    assert (response.json()["ok"], response.json()["code"]) == (False, "invalid_input")
    assert stored() == (0, 0)


def test_capture_still_records_the_raw_envelope(monkeypatch, tmp_path):
    target = tmp_path / "capture.jsonl"
    monkeypatch.setenv("SUISSELINE_CAPTURE_FILE", str(target))
    body = envelope(BOOKING, transcript="x" * (MAX_TOOL_BODY_BYTES + 10))

    response = client.post(token_url("capture"), json=body)

    assert response.json() == {"ok": True, "message": "captured"}
    assert json.loads(json.loads(target.read_text(encoding="utf-8"))["body"]) == body


# --- All seven tools through the envelope ----------------------------------------------


def test_a_whole_call_works_through_the_envelope():
    info = call("info", {})
    assert info["ok"] is True and info["services"]

    available = call("availability", ARGUMENTS["availability"])
    assert available["ok"] is True and "10:00" in available["slots"]

    created = call("bookings", BOOKING)
    assert (created["ok"], created["created"]) == (True, True)

    found = call("bookings/find", ARGUMENTS["bookings/find"])
    assert [(b["date"], b["time"]) for b in found["bookings"]] == [(DAY, "10:00")]

    cancelled = call("bookings/cancel", ARGUMENTS["bookings/cancel"])
    assert (cancelled["ok"], cancelled["already_cancelled"]) == (True, False)

    joined = call("waitlist", WAITLIST)
    assert (joined["ok"], joined["created"]) == (True, True)

    offer_the_waitlist_ten_oclock()
    accepted = call("waitlist/accept", ARGUMENTS["waitlist/accept"])
    assert accepted["ok"] is True and accepted["booking"]["time"] == "10:00"

    assert stored_phones() == {PHONE}


@pytest.mark.parametrize("tool", ARGUMENTS)
def test_each_tool_answers_the_envelope_exactly_as_the_flat_body(tool):
    flat = post(tool, ARGUMENTS[tool]).json()
    reset_database()
    wrapped = call(tool, ARGUMENTS[tool])

    assert wrapped == flat
    assert wrapped.get("code") != "invalid_input", wrapped


def test_the_envelope_as_captured_with_null_numbers_works():
    result = call("bookings", BOOKING, phoneNumber=None, toNumber=None)

    assert result["ok"] is True


@pytest.mark.parametrize("tool", ARGUMENTS)
def test_flat_bodies_still_work_on_the_token_routes(tool):
    result = post(tool, ARGUMENTS[tool]).json()

    assert result.get("code") != "invalid_input", result


# --- The caller's number never replaces the spoken one ---------------------------------


def test_the_spoken_phone_is_used_and_the_callers_number_is_ignored():
    assert call("bookings", BOOKING)["ok"] is True
    assert call("waitlist", WAITLIST)["ok"] is True

    assert stored_phones() == {PHONE}
    assert call("bookings/find", {"phone": PRIVATE["phoneNumber"]})["bookings"] == []


@pytest.mark.parametrize("tool", [tool for tool, arguments in ARGUMENTS.items() if "phone" in arguments])
def test_without_a_spoken_phone_the_callers_number_is_not_used(tool):
    arguments = {key: value for key, value in ARGUMENTS[tool].items() if key != "phone"}

    result = call(tool, arguments)

    assert (result["ok"], result["code"]) == (False, "invalid_input")
    assert "phone" in result["message"]
    assert stored() == (0, 0)


# --- Bad envelopes ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "tool_value",
    [None, "create_booking", 5, [], {}, {"name": "create_booking", "callId": PRIVATE["toolCallId"]}],
    ids=repr,
)
def test_a_tool_without_arguments_is_invalid_input(tool_value):
    response = post("bookings", envelope(BOOKING, tool=tool_value))
    result = response.json()

    assert (result["ok"], result["code"]) == (False, "invalid_input")
    assert_nothing_private_in(response.text)
    assert stored() == (0, 0)


@pytest.mark.parametrize("tool", ARGUMENTS)
@pytest.mark.parametrize(
    "arguments", [None, "name=Greta Fiction PROMPT-SENTINEL", 5, True, [], [BOOKING]], ids=repr
)
def test_arguments_that_are_not_an_object_are_invalid_input(tool, arguments):
    response = post(tool, envelope(arguments, tool))
    result = response.json()

    assert (result["ok"], result["code"]) == (False, "invalid_input")
    assert_nothing_private_in(response.text)
    assert stored() == (0, 0)


@pytest.mark.parametrize(
    "field, value",
    [
        ("date", 20261202),
        ("date", "Friday SENTINEL"),
        ("time", 1000),
        ("time", "ten SENTINEL"),
        ("name", ["SENTINEL"]),
        ("name", {"first": "SENTINEL"}),
        ("service_id", 5),
    ],
    ids=repr,
)
def test_wrong_types_in_the_arguments_are_invalid_input_without_echo(field, value):
    response = post("bookings", envelope({**BOOKING, field: value}))
    result = response.json()

    assert (result["ok"], result["code"]) == (False, "invalid_input")
    assert result["message"] == f"Missing or invalid: {field}."
    assert_nothing_private_in(response.text)
    assert stored() == (0, 0)


def test_a_phone_sent_as_a_number_through_the_envelope_is_invalid_phone():
    result = call("bookings", {**BOOKING, "phone": 41791234567})

    assert (result["ok"], result["code"]) == (False, "invalid_phone")
    assert stored() == (0, 0)


def test_deep_nesting_is_invalid_input_not_a_crash():
    nested = b"[" * 50_000 + b"]" * 50_000

    assert post_raw("bookings", b'{"tool": {"arguments": ' + nested + b"}}")["code"] == "invalid_input"
    assert post_raw("bookings", nested)["code"] == "invalid_input"


# --- Consent through the envelope -------------------------------------------------------


@pytest.mark.parametrize("consent", ["yes", "Yes", " YES ", "true"])
def test_a_clear_yes_through_the_envelope_joins_the_waitlist(consent):
    result = call("waitlist", {**WAITLIST, "consent": consent})

    assert result["ok"] is True, result
    assert stored() == (0, 1)


@pytest.mark.parametrize("consent", ["1", "on", "no", "false", "", "y", "maybe", None, "MISSING"])
def test_anything_else_through_the_envelope_is_consent_required(consent):
    arguments = {key: value for key, value in WAITLIST.items() if key != "consent"}
    if consent != "MISSING":
        arguments["consent"] = consent

    result = call("waitlist", arguments)

    assert (result["ok"], result["code"]) == (False, "consent_required"), result
    assert stored() == (0, 0)


# --- Body size cap ----------------------------------------------------------------------


def body_of_size(size, arguments=BOOKING):
    base = json.dumps(envelope(arguments, transcript="")).encode()
    body = json.dumps(envelope(arguments, transcript="x" * (size - len(base)))).encode()
    assert len(body) == size
    return body


def test_a_long_transcript_just_under_the_cap_is_accepted():
    result = post_raw("bookings", body_of_size(MAX_TOOL_BODY_BYTES))

    assert result["ok"] is True, result
    assert stored() == (1, 0)


def test_a_long_transcript_just_over_the_cap_is_invalid_input():
    result = post_raw("bookings", body_of_size(MAX_TOOL_BODY_BYTES + 1))

    assert result == {"ok": False, "code": "invalid_input", "message": "The request is too large."}
    assert stored() == (0, 0)


def chunks_of(body, size=64 * 1024):
    for start in range(0, len(body), size):
        yield body[start : start + size]


def test_without_a_content_length_the_cap_is_checked_while_reading():
    under = post_raw("bookings", chunks_of(body_of_size(MAX_TOOL_BODY_BYTES)))
    over = post_raw("bookings", chunks_of(body_of_size(MAX_TOOL_BODY_BYTES + 1, {**BOOKING, "time": "11:00"})))

    assert under["ok"] is True, under
    assert over["code"] == "invalid_input" and over["message"] == "The request is too large."
    assert stored() == (1, 0)


def test_a_flat_body_over_the_cap_is_refused_too():
    body = json.dumps({**BOOKING, "padding": "x" * MAX_TOOL_BODY_BYTES}).encode()

    assert post_raw("bookings", body)["message"] == "The request is too large."
    assert stored() == (0, 0)


def test_the_header_routes_have_no_new_cap():
    body = {**BOOKING, "padding": "x" * (MAX_TOOL_BODY_BYTES + 1)}

    response = client.post("/assistant/bookings", json=body, headers={"X-API-Key": KEY})

    assert response.json()["ok"] is True


def run_middleware(headers, path=None):
    """Calls the middleware directly, with a body that must never be read."""
    reached = []
    sent = []

    async def inner(scope, receive, send):
        reached.append(scope["path"])

    async def receive():
        raise AssertionError("the body was read")

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "method": "POST",
        "path": path or token_url("bookings"),
        "headers": headers,
        "query_string": b"",
    }
    asyncio.run(ToolBodies(inner)(scope, receive, send))
    return reached, sent


@pytest.mark.parametrize("length", [str(MAX_TOOL_BODY_BYTES + 1).encode(), b"999999999999", b"12abc", b"-1", b" "])
def test_a_declared_length_over_the_cap_or_unreadable_is_refused_before_reading(length):
    reached, sent = run_middleware([(b"content-length", length)])

    assert reached == []
    assert sent[0]["status"] == 200
    assert json.loads(sent[1]["body"])["code"] == "invalid_input"


def test_the_cap_is_256_kb():
    assert MAX_TOOL_BODY_BYTES == 256 * 1024


def test_a_client_that_disconnects_mid_body_gets_no_answer_and_reaches_no_tool():
    reached = []
    sent = []
    messages = [{"type": "http.request", "body": b'{"tool": {', "more_body": True}, {"type": "http.disconnect"}]

    async def inner(scope, receive, send):
        reached.append(scope["path"])

    async def receive():
        return messages.pop(0)

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "method": "POST", "path": token_url("bookings"), "headers": [], "query_string": b""}
    asyncio.run(ToolBodies(inner)(scope, receive, send))

    assert (reached, sent) == ([], [])


def test_a_declared_length_on_other_paths_is_not_checked():
    reached, _ = run_middleware([(b"content-length", b"999999999999")], path="/assistant/bookings")

    assert reached == ["/assistant/bookings"]


def test_a_real_server_refuses_a_huge_declared_length_without_waiting_for_the_body(tmp_path):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    log_file = tmp_path / "server.log"
    with log_file.open("wb") as sink:
        server = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.assistant_main:app", "--host", "127.0.0.1", "--port", str(port)],
            cwd=REPOSITORY,
            env={**os.environ, "SUISSELINE_PATH_TOKEN": TOKEN, "ASSISTANT_API_KEY": KEY},
            stdout=sink,
            stderr=subprocess.STDOUT,
        )
        try:
            deadline = wall_clock.monotonic() + 30
            while True:
                try:
                    socket.create_connection(("127.0.0.1", port), timeout=1).close()
                    break
                except OSError:
                    assert wall_clock.monotonic() < deadline, "the server did not start"
                    wall_clock.sleep(0.1)
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            connection.putrequest("POST", token_url("bookings"))
            connection.putheader("Content-Type", "application/json")
            connection.putheader("Content-Length", str(100 * 1024 * 1024))
            connection.endheaders(b'{"tool": {"arguments": {')
            response = connection.getresponse()
            status, answer = response.status, json.loads(response.read())
            connection.close()
        finally:
            server.terminate()
            server.wait(timeout=15)
    output = log_file.read_text(encoding="utf-8")

    assert status == 200
    assert answer == {"ok": False, "code": "invalid_input", "message": "The request is too large."}
    assert TOKEN not in output, output


# --- Nothing private leaves the envelope ------------------------------------------------


def assert_nothing_private_in(text):
    for fragment in PRIVATE_FRAGMENTS:
        assert fragment not in text, fragment


def test_nothing_from_the_envelope_reaches_logs_responses_or_the_database(caplog):
    caplog.set_level(logging.DEBUG)
    responses = []
    for tool in ARGUMENTS:  # The booking is cancelled, which frees ten o'clock for the offer.
        if tool == "waitlist/accept":
            offer_the_waitlist_ten_oclock()
        responses.append(post(tool, envelope(ARGUMENTS[tool], tool)).text)
    responses.append(post("bookings", envelope({**BOOKING, "time": "11:00"})).text)
    responses.append(post("bookings", envelope({**BOOKING, "time": "1000"})).text)
    responses.append(post("bookings", envelope(None)).text)
    responses.append(post("bookings", envelope({**BOOKING, "phone": "not a phone"})).text)
    responses.append(json.dumps(post_raw("bookings", body_of_size(MAX_TOOL_BODY_BYTES + 1))))
    log = "\n".join(
        record.getMessage() for record in caplog.records if not record.name.startswith(("httpx", "httpcore"))
    )

    assert stored() == (3, 1)  # cancelled, accepted and eleven o'clock; one waitlist entry
    for text in [*responses, log, database_text()]:
        assert_nothing_private_in(text)


def test_the_token_stays_redacted_on_adapted_routes_and_refusals(caplog):
    caplog.set_level(logging.DEBUG)

    post("bookings", envelope(BOOKING))
    post("bookings", envelope(None))
    post_raw("bookings", body_of_size(MAX_TOOL_BODY_BYTES + 1))
    log = "\n".join(
        record.getMessage() for record in caplog.records if not record.name.startswith(("httpx", "httpcore"))
    )

    assert TOKEN not in log
    assert "path=/assistant/t/[redacted]/bookings status=200" in log
    assert log.count("path=/assistant/t/[redacted]/bookings result=invalid_input") == 2


def test_a_wrong_token_is_still_404_before_any_size_or_envelope_check():
    big = body_of_size(MAX_TOOL_BODY_BYTES + 1)

    assert client.post(token_url("bookings", "wrong-token-" + "x" * 30), content=big).status_code == 404
    assert client.post(token_url("bookings", "wrong-token-" + "x" * 30), json=envelope(None)).status_code == 404
