import time
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pytest
from fastapi.testclient import TestClient

from app import timezone as app_timezone
from app.main import app

client = TestClient(app)


def status_for(day):
    return client.get("/queue/available-times", params={"for_date": day}).status_code


def test_default_time_zone_is_zurich(monkeypatch):
    monkeypatch.delenv("APP_TIMEZONE")
    assert app_timezone.configured_timezone().key == "Europe/Zurich"


def test_blank_setting_uses_the_default(monkeypatch):
    monkeypatch.setenv("APP_TIMEZONE", "")
    assert app_timezone.configured_timezone().key == "Europe/Zurich"


def test_setting_selects_the_business_time_zone(monkeypatch):
    monkeypatch.setenv("APP_TIMEZONE", "Europe/Zurich")
    assert app_timezone.configured_timezone().key == "Europe/Zurich"


def test_invalid_time_zone_fails_loudly(monkeypatch):
    monkeypatch.setenv("APP_TIMEZONE", "Mars/Olympus")
    with pytest.raises(ZoneInfoNotFoundError):
        app_timezone.configured_timezone()


def test_late_evening_in_zurich_is_still_today(set_clock):
    set_clock(datetime(2026, 12, 1, 22, 30, tzinfo=timezone.utc))  # 23:30 in Zurich

    assert app_timezone.today() == date(2026, 12, 1)
    assert status_for("2026-12-01") == 200


def test_just_after_midnight_in_zurich_is_the_next_day(set_clock):
    set_clock(datetime(2026, 12, 1, 23, 30, tzinfo=timezone.utc))  # 00:30 in Zurich

    assert app_timezone.today() == date(2026, 12, 2)
    assert status_for("2026-12-01") == 400


def test_another_configured_zone_is_honoured(monkeypatch, set_clock):
    monkeypatch.setattr(app_timezone, "APP_TZ", ZoneInfo("Asia/Dubai"))
    set_clock(datetime(2026, 12, 1, 22, 30, tzinfo=timezone.utc))

    assert app_timezone.today() == date(2026, 12, 2)


@pytest.mark.parametrize(
    "instant, expected_offset",
    [
        (datetime(2027, 1, 15, 12, 0, tzinfo=timezone.utc), timedelta(hours=1)),
        (datetime(2027, 7, 15, 12, 0, tzinfo=timezone.utc), timedelta(hours=2)),
    ],
    ids=["winter", "summer"],
)
def test_zurich_offset_follows_daylight_saving(set_clock, instant, expected_offset):
    set_clock(instant)
    assert app_timezone.now().utcoffset() == expected_offset


@pytest.mark.parametrize("machine_zone", ["Asia/Dubai", "Pacific/Auckland", "America/Los_Angeles"])
def test_machine_time_zone_is_ignored(monkeypatch, machine_zone):
    monkeypatch.setenv("TZ", machine_zone)
    time.tzset()
    try:
        assert app_timezone.today() == date(2026, 12, 1)
        slots = client.get("/queue/available-times", params={"for_date": "2026-12-01"}).json()
        assert [slot["time"] for slot in slots if slot["available"]][0] == "10:30:00"
    finally:
        monkeypatch.undo()
        time.tzset()
