"""The Smart Cancellation Waitlist. Offers never hold a slot: the first confirmed booking wins."""
import logging
import os
import socket
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
from app.models import (
    Customer,
    IncludedSlot,
    QueueEntry,
    QueueStatus,
    WaitlistEntry,
    WaitlistEntryStatus,
    WaitlistOffer,
    WaitlistOfferStatus,
)
from app.services import booking
from tests.conftest import TEST_TODAY

API_KEY = "test-assistant-key"
HEADERS = {"X-API-Key": API_KEY}
DAY = "2026-12-02"
PHONE = "+41791234567"

assistant = TestClient(assistant_app)


@pytest.fixture(autouse=True)
def assistant_api_key(monkeypatch):
    monkeypatch.setenv("ASSISTANT_API_KEY", API_KEY)


def tool(path, **body):
    response = assistant.post(f"/assistant/{path}", json=body, headers=HEADERS)
    assert response.status_code == 200
    return response.json()


def join(**overrides):
    body = {
        "name": "Wait Lister",
        "phone": PHONE,
        "service_id": "classic-cut",
        "date": DAY,
        "from": "14:00",
        "to": "16:00",
        "consent": True,
    }
    body.update(overrides)
    return tool("waitlist", **{key: value for key, value in body.items() if value is not ...})


def assert_refused(result, code):
    assert result["ok"] is False
    assert result["code"] == code
    assert result["message"]


def entry_count():
    with SessionLocal() as db:
        return db.query(WaitlistEntry).count()


# Step 1: joining


def test_join_stores_the_request_with_a_consent_timestamp():
    result = join()

    assert result["ok"] is True
    assert result["created"] is True
    assert set(result["entry"]) == {"id", "date", "from", "to"}
    assert (result["entry"]["date"], result["entry"]["from"], result["entry"]["to"]) == (DAY, "14:00", "16:00")
    with SessionLocal() as db:
        entry = db.query(WaitlistEntry).one()
        assert entry.status == WaitlistEntryStatus.active
        assert entry.phone == PHONE
        assert entry.service_id == "classic-cut"
        assert entry.duration_minutes == 30
        assert entry.consent_at is not None


def test_joining_creates_no_customer_row():
    join()
    with SessionLocal() as db:
        assert db.query(Customer).count() == 0


@pytest.mark.parametrize("consent", [False, ..., "", None], ids=["false", "missing", "blank", "null"])
def test_join_without_consent_is_refused_and_stores_nothing(consent):
    assert_refused(join(consent=consent), "consent_required")
    assert entry_count() == 0


def test_joining_twice_returns_the_existing_entry():
    first = join()
    second = join()

    assert second["ok"] is True
    assert second["created"] is False
    assert second["entry"] == first["entry"]
    assert entry_count() == 1


def test_retry_still_requires_consent():
    join()

    assert_refused(join(consent=False), "consent_required")
    assert entry_count() == 1


def test_differently_formatted_number_is_the_same_entry():
    join(phone="079 123 45 67")
    assert join(phone="+41 (0)79 123 45 67")["created"] is False
    assert entry_count() == 1


@pytest.mark.parametrize(
    "overrides",
    [{"date": "2026-12-03"}, {"service_id": "beard-sculpt"}, {"from": "15:00"}, {"to": "17:00"}],
    ids=["date", "service", "from", "to"],
)
def test_a_different_request_is_a_new_entry(overrides):
    join()
    assert join(**overrides)["created"] is True
    assert entry_count() == 2


def test_concurrent_identical_joins_store_one_entry():
    start_together = threading.Barrier(12)

    def attempt(_):
        start_together.wait()
        return join()

    with ThreadPoolExecutor(12) as pool:
        results = list(pool.map(attempt, range(12)))

    assert all(result["ok"] for result in results)
    assert sum(result["created"] for result in results) == 1
    assert entry_count() == 1


@pytest.mark.parametrize(
    "overrides, code",
    [
        ({"phone": "not a number"}, "invalid_phone"),
        ({"service_id": "moustache-wax"}, "unknown_service"),
        ({"date": "2026-11-30"}, "date_past"),
        ({"from": "16:00", "to": "14:00"}, "invalid_input"),
        ({"from": "late"}, "invalid_input"),
        ({"date": "tomorrow"}, "invalid_input"),
        ({"name": "   "}, "invalid_input"),
    ],
)
def test_join_failures_store_nothing(overrides, code):
    assert_refused(join(**overrides), code)
    assert entry_count() == 0


def test_range_error_names_the_field_without_echoing_values():
    result = join(**{"from": "16:00", "to": "14:00"})

    assert "to" in result["message"]
    assert "16:00" not in result["message"]


def test_join_requires_the_api_key():
    response = assistant.post("/assistant/waitlist", json={})
    assert response.status_code == 401


def test_waitlist_is_not_on_the_main_app():
    response = TestClient(main_app).post("/assistant/waitlist", json={}, headers=HEADERS)
    assert response.status_code in (404, 405)


def test_join_logs_no_personal_data(caplog):
    caplog.set_level(logging.INFO, logger="barbershop.assistant")

    join(name="Very Private Name")
    join(name="Very Private Name", consent=False)

    assert "result=consent_required" in caplog.text
    assert "Very Private Name" not in caplog.text
    assert "791234567" not in caplog.text


# Step 2: releasing a slot

BANNER = "MANUAL CONTACT: nothing was sent. The slot is not reserved."
REPOSITORY = Path(__file__).resolve().parents[1]
web = TestClient(main_app)


def release(slot, day=DAY):
    from app.services.waitlist import release_slot

    with SessionLocal() as db:
        return release_slot(db, date.fromisoformat(day), time.fromisoformat(slot))


def web_book(slot, day=DAY, phone="+41780000001"):
    return web.post(
        "/queue/checkin",
        json={"name": "Web Customer", "phone": phone, "appointment_date": day, "appointment_time": f"{slot}:00"},
    )


def include_slot(slot, day=DAY):
    with SessionLocal() as db:
        db.add(IncludedSlot(included_date=date.fromisoformat(day), included_time=slot))
        db.commit()


def all_offers():
    with SessionLocal() as db:
        return [
            (offer.entry.phone, offer.slot_time.strftime("%H:%M"), offer.status)
            for offer in db.query(WaitlistOffer).order_by(WaitlistOffer.id).all()
        ]


def only_offer_expiry():
    with SessionLocal() as db:
        return db.query(WaitlistOffer).one().expires_at


def caller(number):
    return f"+4179000000{number}"


def test_release_offers_matching_entries_oldest_first_up_to_three():
    for number in range(4):
        join(phone=caller(number))

    result = release("14:30")

    assert [offer.phone for offer in result.offers] == [caller(0), caller(1), caller(2)]


def test_release_ignores_other_dates_and_ranges():
    join(phone=caller(0), date="2026-12-03")
    join(phone=caller(1), **{"from": "15:00", "to": "16:00"})

    assert release("14:30").offers == []


@pytest.mark.parametrize("window", [("14:30", "16:00"), ("14:00", "14:30")], ids=["starts-at", "ends-at"])
def test_range_means_the_start_time_and_includes_both_ends(window):
    join(**{"from": window[0], "to": window[1]})

    assert len(release("14:30").offers) == 1


def test_whole_service_must_fit_at_the_freed_time():
    join(phone=caller(0), service_id="cut-beard-combo", **{"from": "10:00", "to": "10:00"})
    join(phone=caller(1), **{"from": "10:00", "to": "10:00"})
    assert web_book("10:30").status_code == 200

    assert [offer.phone for offer in release("10:00").offers] == [caller(1)]


def test_release_offers_nothing_when_the_time_is_not_free():
    join()
    assert web_book("14:30").status_code == 200

    result = release("14:30")

    assert result.offers == []
    assert result.refused_reason
    assert all_offers() == []


def test_past_time_today_is_not_offered():
    join(date=TEST_TODAY, **{"from": "09:00", "to": "16:00"})

    assert release("09:30", day=TEST_TODAY).offers == []


def test_an_offer_does_not_reserve_the_slot():
    join()
    assert len(release("14:30").offers) == 1

    assert web_book("14:30").status_code == 200


def test_releasing_twice_does_not_duplicate_an_open_offer():
    join()
    release("14:30")

    assert release("14:30").offers == []
    assert len(all_offers()) == 1


def test_at_most_three_open_offers_per_time_across_runs():
    for number in range(5):
        join(phone=caller(number))
    release("14:30")

    assert release("14:30").offers == []
    assert len(all_offers()) == 3


def test_an_entry_has_at_most_one_open_offer_across_times():
    join()
    release("14:30")

    assert release("15:00").offers == []
    assert [(time_, status) for _, time_, status in all_offers()] == [("14:30", WaitlistOfferStatus.open)]


def test_offer_expires_after_thirty_minutes_by_default():
    join()
    release("14:30")

    assert only_offer_expiry() == datetime(2026, 12, 1, 9, 30)


def test_offer_expires_at_slot_start_when_that_is_sooner():
    join(date=TEST_TODAY, **{"from": "10:00", "to": "11:00"})
    include_slot(time(10, 15), day=TEST_TODAY)

    release("10:15", day=TEST_TODAY)

    assert only_offer_expiry() == datetime(2026, 12, 1, 9, 15)  # 10:15 in Zurich, UTC+1


def test_slot_start_is_converted_with_the_summer_offset(set_clock):
    set_clock(datetime(2027, 7, 15, 10, 0, tzinfo=timezone.utc))  # 12:00 in Zurich
    join(date="2027-07-15", **{"from": "12:00", "to": "13:00"})
    include_slot(time(12, 15), day="2027-07-15")

    release("12:15", day="2027-07-15")

    assert only_offer_expiry() == datetime(2027, 7, 15, 10, 15)  # 12:15 in Zurich, UTC+2


@pytest.mark.parametrize("day", ["2027-03-28", "2027-10-31"], ids=["skipped-hour", "repeated-hour"])
def test_no_offer_on_a_skipped_or_repeated_local_time(day, set_clock):
    set_clock(datetime(2027, 3, 1, 9, 0, tzinfo=timezone.utc))
    join(date=day, **{"from": "02:00", "to": "03:00"})
    include_slot(time(2, 30), day=day)

    result = release("02:30", day=day)

    assert result.offers == []
    assert all_offers() == []


def test_release_confirms_the_time_with_the_booking_function(monkeypatch):
    join()
    calls = []
    original = booking.ensure_bookable

    def spy(*args, **kwargs):
        calls.append(args)
        return original(*args, **kwargs)

    monkeypatch.setattr(booking, "ensure_bookable", spy)

    release("14:30")

    assert calls


def run_script(*args):
    from app.scripts.release_slot import main

    return main(list(args))


def test_command_output_starts_with_the_banner_and_lists_contacts(capsys):
    join(name="Wait Lister")

    assert run_script(DAY, "14:30") == 0

    output = capsys.readouterr().out
    assert output.splitlines()[0] == BANNER
    assert "Wait Lister" in output
    assert PHONE in output


def test_command_reports_when_nothing_was_offered(capsys):
    assert run_script(DAY, "14:30") == 0

    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == BANNER
    assert len(lines) > 1


def test_command_makes_no_network_calls(monkeypatch, capsys):
    join()

    def refuse(*_args, **_kwargs):
        raise AssertionError("the release command must not open a network connection")

    monkeypatch.setattr(socket.socket, "connect", refuse)

    assert run_script(DAY, "14:30") == 0


def test_command_never_logs_contact_details(caplog, capsys):
    caplog.set_level(logging.DEBUG)
    join(name="Very Private Name")

    run_script(DAY, "14:30")

    assert "Very Private Name" not in caplog.text
    assert "791234567" not in caplog.text


@pytest.mark.parametrize("arguments", [[], ["2026-12-02"], ["soon", "14:30"], ["2026-12-02", "later"]])
def test_command_rejects_bad_arguments(arguments, capsys):
    assert run_script(*arguments) == 2


def test_command_runs_as_a_module_against_database_url(tmp_path):
    database = tmp_path / "script.db"

    result = subprocess.run(
        [sys.executable, "-m", "app.scripts.release_slot", "2030-01-02", "10:00"],
        cwd=REPOSITORY,
        env={**os.environ, "DATABASE_URL": f"sqlite:///{database}"},
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[0] == BANNER
    assert database.exists()


# Step 3: accepting an offer

OTHER_PHONE = "+41791234568"


def accept(slot="14:30", day=DAY, phone=PHONE):
    return tool("waitlist/accept", phone=phone, date=day, time=slot)


def offer_to(phone=PHONE, slot="14:30", **join_overrides):
    join(phone=phone, **join_overrides)
    assert len(release(slot, day=join_overrides.get("date", DAY)).offers) == 1


def only_offer_status():
    with SessionLocal() as db:
        return db.query(WaitlistOffer).one().status


def only_entry_status():
    with SessionLocal() as db:
        return db.query(WaitlistEntry).one().status


def appointment_count():
    with SessionLocal() as db:
        return db.query(QueueEntry).count()


def set_appointment_status(appointment_id, status):
    with SessionLocal() as db:
        db.get(QueueEntry, appointment_id).status = status
        db.commit()


def test_accept_books_the_time_and_closes_the_offer():
    offer_to(service_id="cut-beard-combo", **{"from": "10:00", "to": "11:00"}, slot="10:00")

    result = accept(slot="10:00")

    assert result["ok"] is True
    assert set(result["booking"]) == {"id", "date", "weekday", "time", "service"}
    assert result["booking"]["service"] == "Cut + Beard Combo"
    assert only_offer_status() == WaitlistOfferStatus.accepted
    assert only_entry_status() == WaitlistEntryStatus.booked
    with SessionLocal() as db:
        appointment = db.query(QueueEntry).one()
        assert appointment.service_id == "cut-beard-combo"
        assert appointment.duration_minutes == 60
        assert appointment.note == "Service: Cut + Beard Combo (60 min)"
        assert db.query(WaitlistOffer).one().queue_entry_id == appointment.id


def test_accepted_long_service_holds_its_whole_interval():
    offer_to(service_id="cut-beard-combo", **{"from": "10:00", "to": "11:00"}, slot="10:00")
    accept(slot="10:00")

    assert web_book("10:30").status_code == 400
    assert web_book("11:00").status_code == 200


def test_accept_creates_the_customer():
    offer_to(name="New Customer")
    accept()

    with SessionLocal() as db:
        assert db.query(Customer).one().name == "New Customer"


def test_accept_reuses_a_customer_and_never_renames_them():
    assert web_book("09:00", phone=PHONE).status_code == 200
    offer_to(name="Someone Else")
    accept()

    with SessionLocal() as db:
        assert db.query(Customer).one().name == "Web Customer"


def test_accept_accepts_any_format_of_the_number():
    offer_to()
    assert accept(phone="079 123 45 67")["ok"] is True


def test_accept_uses_the_shared_booking_function(monkeypatch):
    offer_to()
    calls = []
    original = booking.book_in_lock

    def spy(*args, **kwargs):
        calls.append(kwargs)
        return original(*args, **kwargs)

    monkeypatch.setattr(booking, "book_in_lock", spy)

    accept()

    assert len(calls) == 1
    assert calls[0]["rename_existing_customer"] is False


@pytest.mark.parametrize(
    "arguments", [{"phone": OTHER_PHONE}, {"slot": "15:00"}, {"day": "2026-12-03"}], ids=["phone", "time", "date"]
)
def test_accept_without_a_matching_offer_is_refused(arguments):
    offer_to()

    assert_refused(accept(**arguments), "no_open_offer")
    assert only_offer_status() == WaitlistOfferStatus.open
    assert appointment_count() == 0


def test_accept_with_no_offers_at_all_is_refused():
    assert_refused(accept(), "no_open_offer")


def test_direct_booking_beats_an_offer():
    offer_to()
    assert web_book("14:30").status_code == 200

    assert_refused(accept(), "slot_taken")
    assert only_offer_status() == WaitlistOfferStatus.lost
    assert only_entry_status() == WaitlistEntryStatus.active
    assert appointment_count() == 1


def test_a_losing_entry_can_be_offered_another_time():
    offer_to()
    assert web_book("14:30").status_code == 200
    accept()

    assert len(release("15:00").offers) == 1


def test_booking_closed_after_the_offer_counts_as_taken():
    offer_to(date=TEST_TODAY, **{"from": "11:00", "to": "12:00"}, slot="11:00")
    with SessionLocal() as db:
        booking.get_or_create_shop_status(db).booking_open = False
        db.commit()

    assert_refused(accept(slot="11:00", day=TEST_TODAY), "slot_taken")
    assert only_offer_status() == WaitlistOfferStatus.lost


def test_winning_closes_the_other_offers_for_that_time():
    join(phone=PHONE)
    join(phone=OTHER_PHONE)
    assert len(release("14:30").offers) == 2

    assert accept(phone=PHONE)["ok"] is True

    with SessionLocal() as db:
        statuses = {offer.entry.phone: offer.status for offer in db.query(WaitlistOffer).all()}
    assert statuses == {PHONE: WaitlistOfferStatus.accepted, OTHER_PHONE: WaitlistOfferStatus.lost}

    assert_refused(accept(phone=OTHER_PHONE), "slot_taken")
    with SessionLocal() as db:
        entry = db.query(WaitlistEntry).filter(WaitlistEntry.phone == OTHER_PHONE).one()
        assert entry.status == WaitlistEntryStatus.active
    assert appointment_count() == 1


@pytest.mark.parametrize("hour", list(range(9, 18)))
def test_two_people_accepting_at_once_produce_one_booking(hour):
    slot = f"{hour:02d}:00"
    window = {"from": "09:00", "to": "18:00"}
    join(phone=PHONE, **window)
    join(phone=OTHER_PHONE, **window)
    assert len(release(slot).offers) == 2
    start_together = threading.Barrier(2)

    def attempt(phone):
        start_together.wait()
        return accept(slot=slot, phone=phone)

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(attempt, [PHONE, OTHER_PHONE]))

    assert sum(result["ok"] for result in results) == 1
    assert [result["code"] for result in results if not result["ok"]] == ["slot_taken"]
    assert appointment_count() == 1


@pytest.mark.parametrize("hour", list(range(9, 18)))
def test_accept_and_a_direct_booking_cannot_both_win(hour):
    slot = f"{hour:02d}:00"
    join(**{"from": "09:00", "to": "18:00"})
    assert len(release(slot).offers) == 1
    start_together = threading.Barrier(2)

    def take_offer():
        start_together.wait()
        return accept(slot=slot)["ok"]

    def book_directly():
        start_together.wait()
        return web_book(slot).status_code == 200

    with ThreadPoolExecutor(2) as pool:
        wins = [future.result() for future in [pool.submit(take_offer), pool.submit(book_directly)]]

    assert sum(wins) == 1
    assert appointment_count() == 1


def test_accepting_again_returns_the_same_booking():
    offer_to()
    first = accept()

    second = accept()

    assert second["ok"] is True
    assert second["booking"] == first["booking"]
    assert appointment_count() == 1


@pytest.mark.parametrize("status", [QueueStatus.next, QueueStatus.in_progress, QueueStatus.done])
def test_accepting_again_works_while_the_booking_is_active(status):
    offer_to()
    first = accept()
    set_appointment_status(first["booking"]["id"], status)

    assert accept()["booking"] == first["booking"]


@pytest.mark.parametrize("status", [QueueStatus.cancelled, QueueStatus.no_show])
def test_accepting_again_is_refused_once_the_booking_is_inactive(status):
    offer_to()
    first = accept()
    set_appointment_status(first["booking"]["id"], status)

    assert_refused(accept(), "no_open_offer")
    assert appointment_count() == 1


def test_accepting_after_the_assistant_cancelled_is_refused():
    offer_to()
    accept()
    tool("bookings/cancel", phone=PHONE, date=DAY, time="14:30")

    assert_refused(accept(), "no_open_offer")


def test_offer_is_open_until_its_last_moment(set_clock):
    offer_to()
    set_clock(datetime(2026, 12, 1, 9, 29, 59, tzinfo=timezone.utc))

    assert accept()["ok"] is True


def test_offer_expires_exactly_at_thirty_minutes(set_clock):
    offer_to()
    set_clock(datetime(2026, 12, 1, 9, 30, tzinfo=timezone.utc))

    assert_refused(accept(), "offer_expired")
    assert only_offer_status() == WaitlistOfferStatus.expired
    assert only_entry_status() == WaitlistEntryStatus.active
    assert appointment_count() == 0


def test_a_past_slot_start_counts_as_expired(set_clock):
    join(date=TEST_TODAY, **{"from": "10:00", "to": "11:00"})
    include_slot(time(10, 15), day=TEST_TODAY)
    assert len(release("10:15", day=TEST_TODAY).offers) == 1
    set_clock(datetime(2026, 12, 1, 9, 20, tzinfo=timezone.utc))  # 10:20 in Zurich

    assert_refused(accept(slot="10:15", day=TEST_TODAY), "offer_expired")


def test_an_expired_entry_can_receive_a_new_offer(set_clock):
    offer_to()
    set_clock(datetime(2026, 12, 1, 9, 31, tzinfo=timezone.utc))
    accept()

    assert len(release("15:00").offers) == 1


def test_staff_are_notified_once_for_an_accepted_offer(monkeypatch):
    sent = []
    monkeypatch.setattr(
        "app.routers.assistant.send_admin_checkin_notification",
        lambda *args, **kwargs: sent.append(args),
    )
    offer_to()
    accept()
    accept()

    assert len(sent) == 1


def test_accept_requires_the_api_key():
    assert assistant.post("/assistant/waitlist/accept", json={}).status_code == 401


@pytest.mark.parametrize(
    "body, code",
    [
        ({"phone": "abc", "date": DAY, "time": "14:30"}, "invalid_phone"),
        ({"phone": PHONE, "date": "soon", "time": "14:30"}, "invalid_input"),
        ({"phone": PHONE, "date": DAY}, "invalid_input"),
    ],
)
def test_accept_invalid_input(body, code):
    assert_refused(tool("waitlist/accept", **body), code)


def test_accept_logs_no_personal_data(caplog):
    caplog.set_level(logging.INFO, logger="barbershop.assistant")
    offer_to(name="Very Private Name")
    accept()
    accept(phone=OTHER_PHONE)

    assert "result=no_open_offer" in caplog.text
    assert "Very Private Name" not in caplog.text
    assert "791234567" not in caplog.text


# Step 4: retention and lock order

def later(day_offset, hour=9, minute=0):
    return datetime(2026, 12, 1, hour, minute, tzinfo=timezone.utc) + timedelta(days=day_offset)


def run_a_waitlist_write():
    # Thirty days after whatever the clock now says, so the date is always within the
    # booking horizon. A refused join would never reach the retention code under test.
    from app.timezone import today

    result = join(phone="+41790009999", date=(today() + timedelta(days=30)).isoformat())
    assert result["ok"] is True


def entry_for(phone=PHONE):
    with SessionLocal() as db:
        return db.query(WaitlistEntry).filter(WaitlistEntry.phone == phone).one_or_none()


def table_counts():
    with SessionLocal() as db:
        return db.query(WaitlistEntry).filter(WaitlistEntry.phone == PHONE).count(), db.query(WaitlistOffer).count()


def test_entry_is_still_active_on_its_own_date(set_clock):
    join()
    set_clock(datetime(2026, 12, 2, 22, 30, tzinfo=timezone.utc))  # 23:30 on the day, in Zurich

    run_a_waitlist_write()

    assert entry_for().status == WaitlistEntryStatus.active


def test_active_entry_expires_once_its_date_has_passed(set_clock):
    join()
    moment = datetime(2026, 12, 2, 23, 30, tzinfo=timezone.utc)  # 00:30 the next day, in Zurich
    set_clock(moment)

    run_a_waitlist_write()

    entry = entry_for()
    assert entry.status == WaitlistEntryStatus.expired
    assert entry.closed_at == moment.replace(tzinfo=None)


def test_open_offer_expires_with_its_entry(set_clock):
    offer_to()
    set_clock(datetime(2026, 12, 3, 9, 0, tzinfo=timezone.utc))

    run_a_waitlist_write()

    assert only_offer_status() == WaitlistOfferStatus.expired


def closed_entry_with_offer():
    offer_to()
    assert accept()["ok"] is True
    return entry_for().closed_at


@pytest.mark.parametrize("days, kept", [(29, True), (30, False), (31, False)], ids=["29-days", "30-days", "31-days"])
def test_closed_rows_are_kept_for_thirty_days(set_clock, days, kept):
    closed_entry_with_offer()
    set_clock(later(days))

    run_a_waitlist_write()

    assert table_counts() == ((1, 1) if kept else (0, 0))


def test_purging_a_closed_entry_keeps_the_appointment(set_clock):
    closed_entry_with_offer()
    set_clock(later(31))

    run_a_waitlist_write()

    assert appointment_count() == 1


@pytest.mark.parametrize("days, kept", [(29, True), (31, False)], ids=["29-days", "31-days"])
def test_date_expired_entries_follow_the_same_retention(set_clock, days, kept):
    join()
    closed_on = later(2)
    set_clock(closed_on)
    run_a_waitlist_write()

    set_clock(closed_on + timedelta(days=days))
    run_a_waitlist_write()

    assert (entry_for() is not None) is kept


def test_active_entries_are_never_purged(set_clock):
    join(date="2027-03-01")
    set_clock(later(45))

    run_a_waitlist_write()

    assert entry_for().status == WaitlistEntryStatus.active


@pytest.mark.parametrize("write", ["release", "accept"])
def test_retention_also_runs_on_release_and_accept(set_clock, write):
    join()
    set_clock(later(3))

    if write == "release":
        release("10:00", day="2026-12-20")
    else:
        accept(day="2026-12-20")

    assert entry_for().status == WaitlistEntryStatus.expired


def test_release_recovers_an_expired_offer_for_a_new_time(set_clock):
    offer_to()
    set_clock(later(0, minute=31))

    assert len(release("15:00").offers) == 1
    assert sorted(status for _, _, status in all_offers()) == [
        WaitlistOfferStatus.expired,
        WaitlistOfferStatus.open,
    ]


def record_calls(monkeypatch, *targets):
    events = []

    def watch(owner, name, label):
        original = getattr(owner, name)

        def recorder(*args, **kwargs):
            events.append(label)
            return original(*args, **kwargs)

        monkeypatch.setattr(owner, name, recorder)

    for owner, name, label in targets:
        watch(owner, name, label)
    return events


def test_revive_takes_the_lock_before_reading_the_schedule(monkeypatch):
    from app.services.booking import revive_no_show

    assert web_book("14:00").status_code == 200
    with SessionLocal() as db:
        entry = db.query(QueueEntry).one()
        entry.status = QueueStatus.no_show
        db.commit()
    events = record_calls(monkeypatch, (booking, "acquire_booking_lock", "lock"), (booking, "_load_day", "read"))

    with SessionLocal() as db:
        revive_no_show(db, db.query(QueueEntry).one(), QueueStatus.done)

    assert events[0] == "lock"
    assert "read" in events


def test_accept_takes_the_lock_before_reading_offers(monkeypatch):
    from app.services import waitlist

    offer_to()
    events = record_calls(
        monkeypatch,
        (booking, "acquire_booking_lock", "lock"),
        (waitlist, "_apply_retention", "retention"),
        (waitlist, "_offers_for", "offers"),
        (booking, "_load_day", "read"),
    )

    assert accept()["ok"] is True

    assert events[0] == "lock"
    assert events.index("retention") < events.index("offers") < events.index("read")


def test_release_takes_the_lock_before_retention_and_reads(monkeypatch):
    from app.services import waitlist

    join()
    events = record_calls(
        monkeypatch,
        (booking, "acquire_booking_lock", "lock"),
        (waitlist, "_apply_retention", "retention"),
        (booking, "_load_day", "read"),
    )

    release("14:30")

    assert events[:2] == ["lock", "retention"]
    assert "read" in events


def test_join_takes_the_lock_before_retention(monkeypatch):
    from app.services import waitlist

    events = record_calls(
        monkeypatch,
        (booking, "acquire_booking_lock", "lock"),
        (waitlist, "_apply_retention", "retention"),
    )

    join()

    assert events == ["lock", "retention"]


def test_retried_booking_takes_the_lock_before_looking_for_a_duplicate(monkeypatch):
    events = record_calls(
        monkeypatch,
        (booking, "acquire_booking_lock", "lock"),
        (booking, "_bookings_at", "duplicate-check"),
    )

    assert tool("bookings", name="A", phone=PHONE, date=DAY, time="14:00", service_id="classic-cut")["ok"]

    assert events[:2] == ["lock", "duplicate-check"]


def test_release_ignores_a_range_that_ended_before_the_time():
    join(**{"from": "13:00", "to": "14:00"})

    assert release("14:30").offers == []


def test_accept_returns_a_booking_the_caller_already_made_for_that_time():
    offer_to()
    direct = tool("bookings", name="A", phone=PHONE, date=DAY, time="14:30", service_id="classic-cut")

    result = accept()

    assert result["booking"] == direct["booking"]
    assert appointment_count() == 1
    assert only_offer_status() == WaitlistOfferStatus.accepted
