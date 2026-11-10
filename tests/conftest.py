"""Shared test setup.

The environment must be set before anything imports the app: `app.database`
reads DATABASE_URL at import time, and this file is the first thing pytest loads.
python-dotenv never overrides variables that are already set, so a developer's
.env cannot redirect the tests to a real database or a real email provider.
"""
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

_test_dir = Path(tempfile.mkdtemp(prefix="barbershop-tests-"))

os.environ["DATABASE_URL"] = f"sqlite:///{_test_dir / 'test.db'}"
os.environ["APP_TIMEZONE"] = "Europe/Zurich"
os.environ["EMAIL_DRY_RUN"] = "true"
os.environ["ADMIN_USERNAME"] = "admin"
os.environ["ADMIN_PASSWORD"] = "test-password-123"

import pytest

from app import models  # noqa: F401  (registers the tables on Base.metadata)
from app import timezone as app_timezone
from app.database import Base, engine

# A Tuesday, 10:00 in Zurich: inside default opening hours, so slots remain today.
DEFAULT_INSTANT = datetime(2026, 12, 1, 9, 0, tzinfo=timezone.utc)
TEST_TODAY = "2026-12-01"


@pytest.fixture(autouse=True)
def clean_database():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield


@pytest.fixture(autouse=True)
def set_clock(monkeypatch):
    """Freezes the app's clock. Call the fixture with a UTC instant to move it."""
    current = {"instant": DEFAULT_INSTANT}
    monkeypatch.setattr(app_timezone, "_utc_now", lambda: current["instant"])

    def move_to(instant: datetime) -> None:
        current["instant"] = instant

    return move_to
