"""The webhook fallback. Every assumption about Suisse Line's payload is unverified."""
import copy
import hashlib
import hmac
import json
import logging
import os
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.assistant_main import app as assistant_app
from app.database import SessionLocal
from app.main import app as main_app
from app.models import BlockedSlot, Customer, QueueEntry
from app.services import booking

SAMPLE = json.loads((Path(__file__).parent / "fixtures" / "call_completed_sample.json").read_text(encoding="utf-8"))


def parse(payload):
    from app.services.call_requests import parse_call_completed

    return parse_call_completed(payload)


# Step 1: the parser


def test_parser_reads_the_assumed_sample():
    parsed = parse(SAMPLE)

    assert parsed.call_id == "call-sample-0001"
    assert parsed.caller_phone == "+41790000200"
    assert parsed.name == "Fictional Caller"
    assert parsed.phone == "+41790000201"
    assert parsed.service_id == "classic-cut"
    assert parsed.date == "2026-12-02"
    assert parsed.time == "14:00"
    assert parsed.is_call_completed is True


@pytest.mark.parametrize("location", ["extracted", "data"])
def test_parser_reads_details_from_either_nested_object(location):
    details = {"name": "A", "phone": "+41790000201", "service_id": "classic-cut", "date": "2026-12-02", "time": "14:00"}

    parsed = parse({"callId": "x", location: details})

    assert (parsed.name, parsed.time) == ("A", "14:00")


def test_parser_reads_details_nested_inside_data():
    parsed = parse({"data": {"callId": "x", "extracted": {"name": "A"}}})

    assert (parsed.call_id, parsed.name) == ("x", "A")


def test_parser_reads_details_from_the_top_level():
    parsed = parse({"call_id": "x", "name": "A", "phone": "+41790000201", "service_id": "s", "date": "d", "time": "t"})

    assert (parsed.call_id, parsed.name, parsed.service_id, parsed.date, parsed.time) == ("x", "A", "s", "d", "t")


def test_parser_prefers_nested_details_over_the_top_level():
    assert parse({"name": "Top", "extracted": {"name": "Nested"}}).name == "Nested"


def test_parser_ignores_unknown_fields():
    assert parse({"callId": "x", "somethingNew": {"deep": [1, 2]}}).call_id == "x"


def test_parser_never_reads_agent_name_as_the_caller():
    assert parse({"agentName": "Assistant"}).name is None


@pytest.mark.parametrize("payload", [{}, {"extracted": "not an object"}, {"data": None}, {"extracted": []}])
def test_parser_tolerates_missing_or_malformed_containers(payload):
    parsed = parse(payload)

    assert (parsed.call_id, parsed.name, parsed.phone, parsed.service_id, parsed.date, parsed.time) == (None,) * 6


@pytest.mark.parametrize("value", [123, None, ["x"], {"a": 1}, "", "   "])
def test_parser_treats_non_text_or_blank_values_as_missing(value):
    assert parse({"extracted": {"name": value}}).name is None


def test_parser_strips_surrounding_whitespace():
    assert parse({"extracted": {"name": "  Ann  "}}).name == "Ann"


def test_parser_does_not_copy_the_caller_number_into_the_details():
    parsed = parse({"phoneNumber": "+41790000200", "extracted": {"name": "A"}})

    assert parsed.caller_phone == "+41790000200"
    assert parsed.phone is None


def test_parser_treats_an_overlong_call_id_as_missing():
    assert parse({"callId": "x" * 201}).call_id is None


@pytest.mark.parametrize("event", ["call.completed", "call_completed", None])
def test_parser_accepts_completed_or_unnamed_events(event):
    payload = {} if event is None else {"event": event}
    assert parse(payload).is_call_completed is True


@pytest.mark.parametrize("event", ["call.started", "call.ended", "call.forwarded"])
def test_parser_flags_other_events(event):
    assert parse({"event": event}).is_call_completed is False


# Step 2: the signed route, booking and duplicates

SECRET = "test-webhook-secret"
URL = "/assistant/webhook/call-completed"
API_KEY = "test-assistant-key"
DAY = "2026-12-02"

client = TestClient(assistant_app)
website = TestClient(main_app)


@pytest.fixture(autouse=True)
def webhook_settings(monkeypatch):
    monkeypatch.setenv("SUISSELINE_WEBHOOK_SECRET", SECRET)
    monkeypatch.delenv("WEBHOOK_SIGNATURE_HEADER", raising=False)
    monkeypatch.setenv("ASSISTANT_API_KEY", API_KEY)


def sign(body):
    return hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


def post_raw(body, signature=None, header="X-Signature"):
    headers = {} if signature is None else {header: signature}
    return client.post(URL, content=body, headers=headers)


def payload(**details):
    data = copy.deepcopy(SAMPLE)
    data["extracted"].update(details)
    return data


def deliver(body=None, call_id=None, **details):
    data = payload(**details) if body is None else body
    if call_id is not None:
        data["callId"] = call_id
    raw = json.dumps(data).encode()
    response = post_raw(raw, sign(raw))
    assert response.status_code == 200, response.text
    return response.json()


def without(field):
    data = copy.deepcopy(SAMPLE)
    del data["extracted"][field]
    return data


def stored():
    from app.models import CallRequest

    with SessionLocal() as db:
        return db.query(CallRequest).order_by(CallRequest.id).all()


def only_request():
    rows = stored()
    assert len(rows) == 1
    return rows[0]


def appointments():
    with SessionLocal() as db:
        return db.query(QueueEntry).count()


def web_book(slot="14:00", phone="+41780000001", day=DAY):
    return website.post(
        "/queue/checkin",
        json={"name": "Web Customer", "phone": phone, "appointment_date": day, "appointment_time": f"{slot}:00"},
    )


def live_book(slot="14:00", phone="+41790000300", service="classic-cut"):
    return client.post(
        "/assistant/bookings",
        headers={"X-API-Key": API_KEY},
        json={"name": "Live Caller", "phone": phone, "date": DAY, "time": slot, "service_id": service},
    ).json()


def test_valid_webhook_books_and_records_the_booking():
    assert deliver() == {"ok": True, "status": "booked"}

    row = only_request()
    with SessionLocal() as db:
        appointment = db.query(QueueEntry).one()
    assert row.call_id == "call-sample-0001"
    assert row.booking_id == appointment.id
    assert row.reason is None
    assert (row.name, row.phone, row.service_id) == ("Fictional Caller", "+41790000201", "classic-cut")
    assert (row.requested_date, row.requested_time) == (date(2026, 12, 2), time(14, 0))
    assert (appointment.service_id, appointment.duration_minutes) == ("classic-cut", 30)
    assert appointment.note == "Service: Classic Cut (30 min)"


def test_booking_goes_through_the_shared_booking_function(monkeypatch):
    calls = []
    original = booking.book_in_lock

    def spy(*args, **kwargs):
        calls.append(kwargs)
        return original(*args, **kwargs)

    monkeypatch.setattr(booking, "book_in_lock", spy)

    deliver()

    assert len(calls) == 1
    assert calls[0]["reuse_active_booking"] is True
    assert calls[0]["rename_existing_customer"] is False


def test_the_raw_payload_and_transcript_are_not_stored():
    deliver()

    row = only_request()
    values = " ".join(str(getattr(row, column.name)) for column in row.__table__.columns)
    assert "transcript" not in values.lower()
    assert "Fictional transcript" not in values


@pytest.mark.parametrize(
    "signature",
    [None, "", "deadbeef", "sha256=deadbeef", "not hex at all", "é"],
    ids=["missing", "blank", "wrong", "wrong-prefixed", "malformed", "non-ascii"],
)
def test_bad_signatures_are_refused(signature):
    raw = json.dumps(SAMPLE).encode()

    headers = {} if signature is None else {"X-Signature": signature.encode("latin-1")}
    assert client.post(URL, content=raw, headers=headers).status_code == 401
    assert stored() == []


@pytest.mark.parametrize("prefix", ["", "sha256="], ids=["bare", "prefixed"])
def test_both_signature_formats_are_accepted(prefix):
    raw = json.dumps(SAMPLE).encode()

    assert post_raw(raw, prefix + sign(raw)).status_code == 200


@pytest.mark.parametrize("value", [None, ""], ids=["unset", "blank"])
def test_everything_is_refused_without_a_secret(monkeypatch, value):
    raw = json.dumps(SAMPLE).encode()
    signature = hmac.new(b"", raw, hashlib.sha256).hexdigest()
    if value is None:
        monkeypatch.delenv("SUISSELINE_WEBHOOK_SECRET")
    else:
        monkeypatch.setenv("SUISSELINE_WEBHOOK_SECRET", value)

    assert post_raw(raw, signature).status_code == 401
    assert stored() == []


def test_signature_over_a_modified_body_is_refused():
    raw = json.dumps(SAMPLE).encode()
    signature = sign(raw)

    assert post_raw(raw.replace(b"14:00", b"15:00"), signature).status_code == 401
    assert stored() == []


def test_signature_in_the_wrong_header_is_refused():
    raw = json.dumps(SAMPLE).encode()

    assert post_raw(raw, sign(raw), header="X-Other").status_code == 401


def test_signature_header_name_is_configurable(monkeypatch):
    monkeypatch.setenv("WEBHOOK_SIGNATURE_HEADER", "X-Custom-Signature")
    raw = json.dumps(SAMPLE).encode()

    assert post_raw(raw, sign(raw), header="X-Custom-Signature").status_code == 200
    assert post_raw(raw, sign(raw), header="X-Signature").status_code == 401


def test_the_api_key_does_not_replace_a_signature():
    raw = json.dumps(SAMPLE).encode()
    assert client.post(URL, content=raw, headers={"X-API-Key": API_KEY}).status_code == 401


def test_body_at_the_limit_is_accepted():
    from app.routers.webhook import MAX_BODY_BYTES

    raw = b" " * (MAX_BODY_BYTES - 2) + b"{}"
    assert post_raw(raw, sign(raw)).status_code == 200


def test_body_over_the_limit_is_refused():
    from app.routers.webhook import MAX_BODY_BYTES

    raw = b" " * MAX_BODY_BYTES + b"{}"
    assert post_raw(raw, sign(raw)).status_code == 413
    assert stored() == []


def test_oversized_body_without_a_declared_length_is_refused():
    from app.routers.webhook import MAX_BODY_BYTES

    def chunks():
        yield b" " * MAX_BODY_BYTES
        yield b"{}"

    assert client.post(URL, content=chunks(), headers={"X-Signature": "x"}).status_code == 413


@pytest.mark.parametrize("raw", [b"{not json", b"", b"\xff\xfe", b"[" * 60000], ids=["broken", "empty", "bytes", "deep"])
def test_invalid_json_returns_400(raw):
    assert post_raw(raw, sign(raw)).status_code == 400
    assert stored() == []


def test_invalid_json_without_a_signature_is_401_not_400():
    assert post_raw(b"{not json").status_code == 401


def test_valid_json_of_the_wrong_kind_becomes_a_follow_up():
    raw = b"[1, 2]"
    response = post_raw(raw, sign(raw))

    assert response.json() == {"ok": True, "status": "follow_up"}
    assert only_request().reason == "incomplete"


@pytest.mark.parametrize("event", ["call.started", "call.ended"])
def test_other_events_are_ignored_and_do_not_use_up_the_call_id(event):
    assert deliver({**SAMPLE, "event": event}) == {"ok": True, "status": "ignored"}
    assert stored() == []

    assert deliver()["status"] == "booked"


@pytest.mark.parametrize("field", ["name", "phone", "service_id", "date", "time"])
def test_missing_detail_becomes_a_follow_up(field):
    assert deliver(without(field)) == {"ok": True, "status": "follow_up"}

    row = only_request()
    assert (row.status.value, row.reason, row.booking_id) == ("follow_up", "incomplete", None)
    assert appointments() == 0


def test_missing_call_id_becomes_a_follow_up_and_still_deduplicates():
    data = copy.deepcopy(SAMPLE)
    del data["callId"]

    assert deliver(data)["status"] == "follow_up"
    assert deliver(data)["status"] == "follow_up"

    assert only_request().reason == "incomplete"
    assert appointments() == 0


def test_caller_number_is_kept_for_staff_but_never_used_to_book():
    assert deliver(without("phone"))["status"] == "follow_up"

    row = only_request()
    assert row.phone == "+41790000200"
    assert appointments() == 0


@pytest.mark.parametrize(
    "details, reason",
    [
        ({"phone": "not a number"}, "invalid_phone"),
        ({"service_id": "moustache-wax"}, "unknown_service"),
        ({"date": "2026-11-30"}, "date_past"),
        ({"date": "tomorrow"}, "invalid_date"),
        ({"date": "2026-13-40"}, "invalid_date"),
        ({"time": "2pm"}, "invalid_time"),
        ({"time": "25:00"}, "invalid_time"),
        ({"time": "14:17"}, "invalid_time"),
    ],
    ids=["phone", "service", "past", "date-words", "date-range", "time-words", "time-range", "off-grid"],
)
def test_invalid_details_become_a_follow_up(details, reason):
    assert deliver(**details) == {"ok": True, "status": "follow_up"}

    row = only_request()
    assert (row.status.value, row.reason, row.booking_id) == ("follow_up", reason, None)
    assert appointments() == 0


def test_unknown_service_text_is_not_stored():
    deliver(service_id="moustache-wax")

    assert only_request().service_id is None


def test_a_taken_slot_becomes_a_follow_up():
    assert web_book().status_code == 200

    assert deliver() == {"ok": True, "status": "follow_up"}
    assert only_request().reason == "slot_taken"
    assert appointments() == 1


def test_live_api_booking_first_makes_the_webhook_a_follow_up():
    assert live_book()["ok"] is True

    assert deliver()["status"] == "follow_up"
    assert only_request().reason == "slot_taken"
    assert appointments() == 1


def test_same_caller_already_booked_links_the_existing_booking():
    first = live_book(phone="+41790000201")

    assert deliver()["status"] == "booked"
    assert only_request().booking_id == first["booking"]["id"]
    assert appointments() == 1


def test_a_long_service_uses_its_full_duration():
    assert deliver(service_id="cut-beard-combo", time="10:00")["status"] == "booked"

    with SessionLocal() as db:
        assert db.query(QueueEntry).one().duration_minutes == 60
    assert web_book("10:30").status_code == 400
    assert web_book("11:00").status_code == 200


def test_a_long_service_is_refused_if_part_of_it_is_taken():
    assert web_book("10:30").status_code == 200

    assert deliver(service_id="cut-beard-combo", time="10:00")["status"] == "follow_up"
    assert only_request().reason == "slot_taken"


def test_an_existing_customer_is_not_renamed():
    assert web_book("09:00", phone="+41790000201").status_code == 200

    assert deliver()["status"] == "booked"

    with SessionLocal() as db:
        assert db.query(Customer).one().name == "Web Customer"


def test_same_call_id_twice_keeps_the_original_outcome():
    first = deliver()
    second = deliver()

    assert first == second == {"ok": True, "status": "booked"}
    assert len(stored()) == 1
    assert appointments() == 1


def test_a_duplicate_never_reaches_the_booking_function(monkeypatch):
    deliver()
    calls = []
    original = booking.book_in_lock

    def spy(*args, **kwargs):
        calls.append(kwargs)
        return original(*args, **kwargs)

    monkeypatch.setattr(booking, "book_in_lock", spy)

    deliver()

    assert calls == []


def test_duplicate_follow_up_keeps_its_outcome_after_the_slot_frees_up():
    assert web_book().status_code == 200
    deliver()
    with SessionLocal() as db:
        db.query(QueueEntry).delete()
        db.commit()

    assert deliver() == {"ok": True, "status": "follow_up"}
    assert only_request().reason == "slot_taken"
    assert appointments() == 0


def test_redelivery_after_a_cancel_does_not_rebook():
    deliver()
    assert client.post(
        "/assistant/bookings/cancel",
        headers={"X-API-Key": API_KEY},
        json={"phone": "+41790000201", "date": DAY, "time": "14:00"},
    ).json()["cancelled"] is True

    assert deliver()["status"] == "booked"

    with SessionLocal() as db:
        assert db.query(QueueEntry).one().status.value == "cancelled"


@pytest.mark.parametrize("round_number", range(6))
def test_concurrent_duplicates_create_one_booking_and_one_row(round_number):
    raw = json.dumps({**SAMPLE, "callId": f"call-race-{round_number}"}).encode()
    start_together = threading.Barrier(12)

    def attempt(_):
        start_together.wait()
        return post_raw(raw, sign(raw))

    with ThreadPoolExecutor(12) as pool:
        responses = list(pool.map(attempt, range(12)))

    assert {response.status_code for response in responses} == {200}
    assert {response.json()["status"] for response in responses} == {"booked"}
    assert len(stored()) == 1
    assert appointments() == 1


@pytest.mark.parametrize("round_number", range(6))
def test_webhooks_for_different_calls_cannot_both_win_a_slot(round_number):
    def body(index):
        data = copy.deepcopy(SAMPLE)
        data["callId"] = f"call-{round_number}-{index}"
        data["extracted"]["phone"] = f"+4179{index:07d}"
        return json.dumps(data).encode()

    start_together = threading.Barrier(12)

    def attempt(index):
        raw = body(index)
        start_together.wait()
        return post_raw(raw, sign(raw)).json()["status"]

    with ThreadPoolExecutor(12) as pool:
        outcomes = list(pool.map(attempt, range(12)))

    assert outcomes.count("booked") == 1
    assert outcomes.count("follow_up") == 11
    assert appointments() == 1


def test_webhook_and_website_cannot_both_win_a_slot():
    raw = json.dumps(SAMPLE).encode()
    start_together = threading.Barrier(2)

    def through_webhook():
        start_together.wait()
        return post_raw(raw, sign(raw)).json()["status"] == "booked"

    def through_website():
        start_together.wait()
        return web_book().status_code == 200

    with ThreadPoolExecutor(2) as pool:
        wins = [future.result() for future in [pool.submit(through_webhook), pool.submit(through_website)]]

    assert sum(wins) == 1
    assert appointments() == 1


def test_the_lock_is_taken_before_checking_for_a_duplicate(monkeypatch):
    from app.services import call_requests

    events = []

    def watch(owner, name, label):
        original = getattr(owner, name)

        def recorder(*args, **kwargs):
            events.append(label)
            return original(*args, **kwargs)

        monkeypatch.setattr(owner, name, recorder)

    watch(booking, "acquire_booking_lock", "lock")
    watch(call_requests, "_find_existing", "duplicate-check")
    watch(booking, "_load_day", "read")

    deliver()

    assert events[0] == "lock"
    assert events.index("duplicate-check") < events.index("read")


def test_unique_column_handles_a_duplicate_that_passes_the_first_check(monkeypatch):
    from app.services import call_requests

    deliver()
    original = call_requests._find_existing
    calls = []

    def misses_once(db, call_id):
        calls.append(call_id)
        return None if len(calls) == 1 else original(db, call_id)

    monkeypatch.setattr(call_requests, "_find_existing", misses_once)

    assert deliver() == {"ok": True, "status": "booked"}
    assert len(stored()) == 1
    assert appointments() == 1


# Step 3: staff email, logs, exposure and the follow-up command


@pytest.fixture
def emails(monkeypatch):
    sent = []
    monkeypatch.setattr(
        "app.services.call_requests.send_email",
        lambda to, subject, body: sent.append((to, subject, body)) or True,
    )
    with SessionLocal() as db:
        booking.get_or_create_shop_status(db).notification_emails = "staff@example.invalid"
        db.commit()
    return sent


def test_staff_are_notified_of_a_booking(emails):
    deliver()

    assert len(emails) == 1
    assert emails[0][0] == "staff@example.invalid"
    assert "booked" in emails[0][1].lower()


def test_staff_are_notified_of_a_follow_up(emails):
    deliver(service_id="moustache-wax")

    assert len(emails) == 1
    assert "follow" in emails[0][1].lower()
    assert "unknown_service" in emails[0][2]


def test_a_duplicate_delivery_sends_no_second_email(emails):
    deliver()
    deliver()

    assert len(emails) == 1


def test_an_ignored_event_sends_no_email(emails):
    deliver({**SAMPLE, "event": "call.started"})

    assert emails == []


def test_the_email_contains_no_personal_data(emails):
    deliver()
    deliver(call_id="call-2", name="Very Private Name", phone="+41790000299", service_id="moustache-wax")

    text = " ".join(subject + body for _, subject, body in emails)
    assert "Very Private Name" not in text
    assert "Fictional Caller" not in text
    assert "79000" not in text


def test_admin_email_is_the_fallback_recipient(monkeypatch):
    sent = []
    monkeypatch.setattr("app.services.call_requests.send_email", lambda to, subject, body: sent.append(to) or True)
    monkeypatch.setattr("app.services.call_requests.ADMIN_EMAIL", "owner@example.invalid")

    deliver()

    assert sent == ["owner@example.invalid"]


def test_a_failing_email_does_not_change_the_response(monkeypatch, emails):
    def fail(*_args, **_kwargs):
        raise RuntimeError("email provider is down")

    monkeypatch.setattr("app.services.call_requests.send_email", fail)

    assert deliver() == {"ok": True, "status": "booked"}
    assert only_request().status.value == "booked"

    from app.services.call_requests import notify_staff

    notify_staff(1, "booked", None, ["staff@example.invalid"])  # must return normally


def test_logs_contain_the_call_id_but_no_personal_data(caplog):
    caplog.set_level(logging.DEBUG)
    with SessionLocal() as db:
        booking.get_or_create_shop_status(db).notification_emails = "staff@example.invalid"
        db.commit()

    deliver(name="Very Private Name", phone="+41790000299")
    deliver(call_id="call-2", name="Very Private Name", phone="+41790000298", time="14:17")

    assert "call_id=call-sample-0001" in caplog.text
    assert "status=booked" in caplog.text
    assert "status=follow_up" in caplog.text
    for secret in ("Very Private Name", "790000299", "790000298", "790000200", "Fictional transcript", SECRET):
        assert secret not in caplog.text


def test_an_awkward_call_id_cannot_inject_into_the_logs(caplog):
    caplog.set_level(logging.INFO, logger="barbershop.assistant")

    deliver({**SAMPLE, "callId": "abc\nfake log line"})

    assert "fake log line" not in caplog.text
    assert "call_id=unprintable" in caplog.text


EXPECTED_PUBLIC_ROUTES = {
    "/assistant/info",
    "/assistant/availability",
    "/assistant/bookings",
    "/assistant/bookings/find",
    "/assistant/bookings/cancel",
    "/assistant/waitlist",
    "/assistant/waitlist/accept",
    "/assistant/webhook/call-completed",
    "/assistant/t/{token}/info",
    "/assistant/t/{token}/availability",
    "/assistant/t/{token}/bookings",
    "/assistant/t/{token}/bookings/find",
    "/assistant/t/{token}/bookings/cancel",
    "/assistant/t/{token}/waitlist",
    "/assistant/t/{token}/waitlist/accept",
    "/assistant/t/{token}/capture",
}


def api_paths(application):
    from fastapi.routing import APIRoute

    return {route.path for route in application.routes if isinstance(route, APIRoute)}


def test_the_public_app_gains_only_the_webhook_route():
    assert api_paths(assistant_app) == EXPECTED_PUBLIC_ROUTES


def test_the_website_app_does_not_have_the_webhook():
    assert URL not in api_paths(main_app)
    raw = json.dumps(SAMPLE).encode()
    assert website.post(URL, content=raw, headers={"X-Signature": sign(raw)}).status_code in (404, 405)


def run_command(*arguments):
    from app.scripts.list_requests import main

    return main(list(arguments))


BANNER_PREFIX = "MANUAL FOLLOW-UP"


def test_command_lists_open_follow_ups_under_the_banner(capsys):
    deliver(service_id="beard-sculpt", time="14:17")

    assert run_command() == 0

    output = capsys.readouterr().out
    lines = output.splitlines()
    assert lines[0].startswith(BANNER_PREFIX)
    for expected in ("Fictional Caller", "+41790000201", "Beard Sculpt", "2026-12-02", "invalid_time"):
        assert expected in output


def test_command_leaves_out_booked_requests(capsys):
    deliver()

    assert run_command() == 0

    assert "Fictional Caller" not in capsys.readouterr().out


def test_command_shows_a_dash_for_details_that_are_missing(capsys):
    deliver(without("name"))

    run_command()

    assert "- |" in capsys.readouterr().out


def test_marking_a_request_handled_removes_it_from_the_list(capsys):
    deliver(service_id="moustache-wax")
    row_id = only_request().id

    assert run_command("--handled", str(row_id)) == 0
    assert only_request().handled_at is not None
    assert only_request().status.value == "follow_up"
    capsys.readouterr()

    assert run_command() == 0
    assert "Fictional Caller" not in capsys.readouterr().out


def test_marking_twice_is_refused(capsys):
    deliver(service_id="moustache-wax")
    row_id = only_request().id
    run_command("--handled", str(row_id))

    assert run_command("--handled", str(row_id)) == 1


def test_a_booked_request_cannot_be_marked_handled(capsys):
    deliver()

    assert run_command("--handled", str(only_request().id)) == 1
    assert only_request().handled_at is None


def test_an_unknown_id_cannot_be_marked_handled(capsys):
    assert run_command("--handled", "999") == 1


@pytest.mark.parametrize("arguments", [["--handled"], ["--handled", "abc"], ["--nonsense"]])
def test_command_rejects_bad_arguments(arguments, capsys):
    assert run_command(*arguments) == 2


def test_command_never_logs_contact_details(caplog, capsys):
    caplog.set_level(logging.DEBUG)
    deliver(name="Very Private Name", service_id="moustache-wax")
    caplog.clear()

    run_command()

    assert "Very Private Name" not in caplog.text
    assert "790000201" not in caplog.text


def test_command_runs_as_a_module_against_database_url(tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "app.scripts.list_requests"],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "DATABASE_URL": f"sqlite:///{tmp_path / 'list.db'}"},
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[0].startswith(BANNER_PREFIX)


# Retention: rows are deleted 30 days after they were closed

RECEIVED = datetime(2026, 12, 1, 9, 0, tzinfo=timezone.utc)


def another_delivery():
    deliver({**SAMPLE, "callId": "call-later", "extracted": {"name": "Later"}})


def request_for(call_id):
    from app.models import CallRequest

    with SessionLocal() as db:
        return db.query(CallRequest).filter(CallRequest.call_id == call_id).one_or_none()


def queue_size():
    with SessionLocal() as db:
        return db.query(QueueEntry).count()


@pytest.mark.parametrize("days, kept", [(29, True), (30, False), (31, False)], ids=["29-days", "30-days", "31-days"])
def test_booked_request_is_deleted_thirty_days_after_it_arrives(set_clock, days, kept):
    deliver()
    set_clock(RECEIVED + timedelta(days=days))

    another_delivery()

    assert (request_for("call-sample-0001") is not None) is kept


@pytest.mark.parametrize("days, kept", [(29, True), (31, False)], ids=["29-days", "31-days"])
def test_handled_follow_up_is_deleted_thirty_days_after_it_was_handled(set_clock, capsys, days, kept):
    deliver(service_id="moustache-wax")
    handled_on = RECEIVED + timedelta(days=10)
    set_clock(handled_on)
    assert run_command("--handled", str(only_request().id)) == 0

    set_clock(handled_on + timedelta(days=days))
    another_delivery()

    assert (request_for("call-sample-0001") is not None) is kept


@pytest.mark.parametrize("days", [31, 60, 365])
def test_open_follow_up_is_never_deleted_by_age(set_clock, days):
    deliver(service_id="moustache-wax")
    set_clock(RECEIVED + timedelta(days=days))

    another_delivery()

    assert request_for("call-sample-0001") is not None


def test_deleting_a_request_never_touches_its_booking(set_clock):
    deliver()
    assert queue_size() == 1
    set_clock(RECEIVED + timedelta(days=31))

    another_delivery()

    assert request_for("call-sample-0001") is None
    assert queue_size() == 1


def test_the_local_command_runs_the_cleanup_before_printing(set_clock, capsys):
    deliver()
    deliver(call_id="call-open", service_id="moustache-wax")
    set_clock(RECEIVED + timedelta(days=31))

    assert run_command() == 0

    assert request_for("call-sample-0001") is None
    assert "Fictional Caller" in capsys.readouterr().out  # the open follow-up is kept


def test_an_ignored_event_does_not_delete_anything(set_clock):
    deliver()
    set_clock(RECEIVED + timedelta(days=31))

    deliver({**SAMPLE, "callId": "other", "event": "call.started"})

    assert request_for("call-sample-0001") is not None


def test_retention_uses_the_waitlist_constant():
    from app.services import call_requests, waitlist

    assert call_requests.RETENTION is waitlist.RETENTION


def test_cleanup_runs_under_the_lock_before_the_duplicate_check(monkeypatch):
    from app.services import call_requests

    events = []

    def watch(owner, name, label):
        original = getattr(owner, name)

        def recorder(*args, **kwargs):
            events.append(label)
            return original(*args, **kwargs)

        monkeypatch.setattr(owner, name, recorder)

    watch(booking, "acquire_booking_lock", "lock")
    watch(call_requests, "_delete_expired_requests", "delete")
    watch(call_requests, "_find_existing", "duplicate-check")

    deliver()

    assert events[:3] == ["lock", "delete", "duplicate-check"]


# Suggested times on follow-ups

COMBO = "cut-beard-combo"
ALL_DAY = [time(hour, minute) for hour in range(9, 18) for minute in (0, 30)]


def block_day(day):
    with SessionLocal() as db:
        for slot in ALL_DAY:
            db.add(BlockedSlot(blocked_date=date.fromisoformat(day), blocked_time=slot))
        db.commit()


def suggestions():
    return only_request().suggested_times


def test_slot_taken_suggests_up_to_three_times_where_the_whole_service_fits():
    assert web_book("10:30").status_code == 200

    assert deliver(service_id=COMBO, time="10:00")["status"] == "follow_up"

    assert only_request().reason == "slot_taken"
    assert suggestions() == "2026-12-02 09:00, 09:30, 11:00"


def test_each_suggested_time_really_can_be_booked_for_that_service():
    assert web_book("10:30").status_code == 200
    deliver(service_id=COMBO, time="10:00")

    day, first = suggestions().split(", ")[0].split(" ")
    assert live_book(slot=first, phone="+41790000301", service=COMBO)["ok"] is True
    assert day == DAY


def test_suggestions_record_when_they_were_computed():
    assert web_book().status_code == 200
    deliver()

    assert only_request().suggested_as_of == datetime(2026, 12, 1, 9, 0)


def test_a_full_requested_day_falls_through_to_the_next_open_date():
    assert web_book().status_code == 200
    with SessionLocal() as db:
        for slot in ALL_DAY:
            if slot != time(14, 0):
                db.add(BlockedSlot(blocked_date=date(2026, 12, 2), blocked_time=slot))
        db.commit()

    deliver()

    assert suggestions() == "2026-12-03 09:00, 09:30, 10:00"


def test_suggestions_look_at_most_seven_days_ahead():
    assert web_book().status_code == 200
    for offset in range(3, 10):  # 2026-12-03 to 2026-12-09, leaving the 8th day open
        block_day(f"2026-12-{offset:02d}")
    with SessionLocal() as db:
        for slot in ALL_DAY:
            if slot != time(14, 0):
                db.add(BlockedSlot(blocked_date=date(2026, 12, 2), blocked_time=slot))
        db.commit()

    deliver()

    row = only_request()
    assert (row.status.value, row.reason) == ("follow_up", "slot_taken")
    assert (row.suggested_times, row.suggested_as_of) == (None, None)


def test_the_seventh_day_ahead_is_still_searched():
    assert web_book().status_code == 200
    with SessionLocal() as db:
        for slot in ALL_DAY:
            if slot != time(14, 0):
                db.add(BlockedSlot(blocked_date=date(2026, 12, 2), blocked_time=slot))
        db.commit()
    for offset in range(3, 9):  # 2026-12-03 to 2026-12-08
        block_day(f"2026-12-{offset:02d}")

    deliver()

    assert suggestions().startswith("2026-12-09 ")


def test_incomplete_request_with_service_and_date_gets_suggestions():
    deliver(without("name"))

    row = only_request()
    assert row.reason == "incomplete"
    assert row.suggested_times == "2026-12-02 09:00, 09:30, 10:00"


@pytest.mark.parametrize("field", ["service_id", "date"])
def test_incomplete_request_without_service_or_date_gets_none(field):
    assert deliver(without(field))["status"] == "follow_up"

    row = only_request()
    assert (row.reason, row.suggested_times, row.suggested_as_of) == ("incomplete", None, None)


@pytest.mark.parametrize(
    "details, reason",
    [
        ({"service_id": "moustache-wax"}, "unknown_service"),
        ({"phone": "not a number"}, "invalid_phone"),
        ({"date": "tomorrow"}, "invalid_date"),
        ({"time": "14:17"}, "invalid_time"),
    ],
)
def test_other_reasons_get_no_suggestions_and_no_error(details, reason):
    assert deliver(**details)["status"] == "follow_up"

    row = only_request()
    assert (row.reason, row.suggested_times) == (reason, None)


def test_date_past_suggests_from_today_and_never_a_past_time():
    deliver(date="2026-11-30")

    row = only_request()
    assert row.reason == "date_past"
    assert row.suggested_times == "2026-12-01 10:30, 11:00, 11:30"


def test_past_times_today_are_never_suggested():
    assert web_book("15:00", day="2026-12-01").status_code == 200

    deliver(date="2026-12-01", time="15:00")

    assert suggestions() == "2026-12-01 10:30, 11:00, 11:30"


def test_a_booked_request_has_no_suggestions():
    deliver()

    assert (only_request().suggested_times, only_request().suggested_as_of) == (None, None)


def test_suggestions_never_create_or_change_a_booking():
    assert web_book().status_code == 200
    with SessionLocal() as db:
        before = [(row.id, row.status, row.appointment_time) for row in db.query(QueueEntry).all()]
        customers = db.query(Customer).count()

    deliver()

    with SessionLocal() as db:
        assert [(row.id, row.status, row.appointment_time) for row in db.query(QueueEntry).all()] == before
        assert db.query(Customer).count() == customers


def test_a_suggested_time_stays_bookable_by_anyone():
    assert web_book().status_code == 200
    deliver()
    first = suggestions().split(" ")[1].rstrip(",")

    assert web_book(first, phone="+41780000002").status_code == 200


def test_suggestions_are_computed_with_the_shared_function_under_the_lock(monkeypatch):
    assert web_book().status_code == 200
    events = []

    def watch(name, label):
        original = getattr(booking, name)

        def recorder(*args, **kwargs):
            events.append((label, kwargs.get("duration_minutes")))
            return original(*args, **kwargs)

        monkeypatch.setattr(booking, name, recorder)

    watch("acquire_booking_lock", "lock")
    watch("slot_availability", "availability")

    deliver(service_id=COMBO, time="14:00")

    assert events[0][0] == "lock"
    assert ("availability", 60) in events


def test_command_prints_suggestions_with_the_warning(capsys):
    assert web_book().status_code == 200
    deliver()

    run_command()

    output = capsys.readouterr().out
    assert "as of 2026-12-01 10:00, may be taken since" in output
    assert "2026-12-02 09:00, 09:30, 10:00" in output
    assert "reserved" not in output.lower().replace("not reserved", "")


def test_staff_email_includes_suggestions_and_no_personal_data(emails):
    assert web_book().status_code == 200

    deliver(name="Very Private Name", phone="+41790000299")

    subject, body = emails[0][1], emails[0][2]
    assert "2026-12-02 09:00, 09:30, 10:00" in body
    assert "may be taken" in body
    assert "reserved" not in body.lower()
    assert "Very Private Name" not in subject + body
    assert "790000299" not in subject + body
