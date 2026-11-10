"""The demo seed fills only a new database, and only through the app's own functions."""
import os
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

REPOSITORY = Path(__file__).resolve().parents[1]
SCRIPT = REPOSITORY / "scripts" / "seed_demo.py"


def seed(database_url):
    env = {key: value for key, value in os.environ.items() if key != "DATABASE_URL"}
    env["APP_TIMEZONE"] = "Europe/Zurich"
    if database_url is not None:
        env["DATABASE_URL"] = database_url
    return subprocess.run([sys.executable, str(SCRIPT)], cwd=REPOSITORY, env=env, capture_output=True, text=True, timeout=60)


def rows(database, query):
    connection = sqlite3.connect(database)
    try:
        return connection.execute(query).fetchall()
    finally:
        connection.close()


def test_seed_creates_the_demo_shape(tmp_path):
    database = tmp_path / "demo.db"
    today = datetime.now(ZoneInfo("Europe/Zurich")).date().isoformat()

    result = seed(f"sqlite:///{database}")

    assert result.returncode == 0, result.stderr
    assert rows(database, "select count(*) from queue_entries") == [(6,)]
    assert rows(database, "select count(*) from customers") == [(6,)]
    assert rows(database, "select count(*) from waitlist_entries where status = 'active'") == [(1,)]
    assert rows(database, "select count(*) from queue_entries where queue_date <= '%s'" % today) == [(0,)]

    first_day = rows(database, "select min(queue_date) from queue_entries")[0][0]
    at_ten = rows(database, f"select count(*) from queue_entries where queue_date = '{first_day}' and appointment_time like '10:00%'")
    assert at_ten == [(1,)]
    assert rows(database, "select capacity_per_slot from shop_status") == [(1,)]


def test_seeded_waitlist_entry_differs_from_the_booked_ten_oclock(tmp_path):
    database = tmp_path / "demo.db"
    seed(f"sqlite:///{database}")

    assert rows(database, "select service_id, window_start from waitlist_entries") == [
        ("hot-towel-shave", "16:00:00.000000")
    ]


def test_seed_uses_only_invented_numbers_and_the_app_booking_fields(tmp_path):
    database = tmp_path / "demo.db"
    seed(f"sqlite:///{database}")

    phones = [phone for (phone,) in rows(database, "select phone from customers")]
    assert all(phone.startswith("+41790000") for phone in phones)
    assert rows(database, "select count(*) from queue_entries where service_id is null or duration_minutes is null or note is null") == [(0,)]


def test_seed_prints_only_the_summary(tmp_path):
    result = seed(f"sqlite:///{tmp_path / 'demo.db'}")

    lines = result.stdout.splitlines()
    assert lines[0] == "Seeded fictional demo data:"
    assert len(lines) == 8
    assert "Ben Sample" in result.stdout
    assert "+4179" not in result.stdout


def test_seed_does_not_write_directly_to_the_database():
    source = SCRIPT.read_text(encoding="utf-8").upper()
    assert "INSERT " not in source
    assert "DB.ADD(" not in source


def test_seed_allows_an_existing_empty_file(tmp_path):
    database = tmp_path / "demo.db"
    database.touch()

    assert seed(f"sqlite:///{database}").returncode == 0


def assert_refused(result):
    assert result.returncode == 2
    assert result.stdout == ""
    assert "Refusing to seed" in result.stderr


def test_seed_refuses_an_unset_url():
    assert_refused(seed(None))


def test_seed_refuses_a_database_that_already_has_data(tmp_path):
    database = tmp_path / "demo.db"
    assert seed(f"sqlite:///{database}").returncode == 0
    before = database.read_bytes()

    assert_refused(seed(f"sqlite:///{database}"))
    assert database.read_bytes() == before


@pytest.mark.parametrize("name", ["barbershop.db", "copy-of-BARBERSHOP.DB", "barbershop.db.backup"])
def test_seed_refuses_a_url_naming_barbershop_db(tmp_path, name):
    database = tmp_path / name

    assert_refused(seed(f"sqlite:///{database}"))
    assert not database.exists()


@pytest.mark.parametrize("url", ["postgresql://user@localhost/demo", "sqlite://", "sqlite:///:memory:"])
def test_seed_refuses_anything_but_a_sqlite_file(url):
    assert_refused(seed(url))
