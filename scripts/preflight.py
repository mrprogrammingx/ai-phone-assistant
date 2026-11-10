#!/usr/bin/env python3
"""Tells you whether you are ready to run a call session.

    venv/bin/python scripts/preflight.py
    venv/bin/python scripts/preflight.py --public-url https://YOUR-TUNNEL-ADDRESS
    venv/bin/python scripts/preflight.py --fallback

Exit code 0 means no check failed. Secret values are compared but never printed, and
the key is only ever sent to 127.0.0.1 and the one public address given. Nothing here
contacts Suisse Line.
"""
import argparse
import http.client
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY))

from dotenv import load_dotenv
from sqlalchemy import create_engine

from app.schema_check import DatabaseSchemaOutOfDate, require_current_schema

TIMEZONE = "Europe/Zurich"
SECRETS = ("ASSISTANT_API_KEY", "ADMIN_PASSWORD", "SESSION_SECRET_KEY")
PLACEHOLDER_WORDS = ("change", "replace", "example")
MINIMUM_SECRET_LENGTH = 24
WRONG_KEY = "preflight-deliberately-wrong-key"
MUST_BE_HIDDEN = ("/docs", "/admin/shop-status")
REQUEST_TIMEOUT_SECONDS = 20
SQLITE_PREFIX = "sqlite:///"

GENERATE = "Generate one: python3 -c \"import secrets; print(secrets.token_urlsafe(32))\""
RESEED = (
    "Make a fresh demo database: DATABASE_URL=sqlite:////tmp/phone-assistant-demo-new.db "
    "APP_TIMEZONE=Europe/Zurich venv/bin/python scripts/seed_demo.py, then point DATABASE_URL at that file."
)
START_APP = (
    "APP_TIMEZONE=Europe/Zurich DATABASE_URL=<your-demo-database-url> EMAIL_DRY_RUN=true "
    "ASSISTANT_API_KEY=<paste-the-generated-key> venv/bin/uvicorn app.assistant_main:app --host 127.0.0.1 --port {port}"
)
START_TUNNEL = "Start the tunnel and wait for it to answer: cloudflared tunnel --url http://localhost:8003"


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    """A redirect must never carry the key to a different host."""

    def redirect_request(self, *_args, **_kwargs):
        return None


# An empty proxy table stops proxy settings in the environment from receiving the key.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirects)


class Report:
    def __init__(self):
        self.failed = False

    def add(self, status: str, name: str, detail: str, fix: str | None = None) -> None:
        print(f"{status:<5} {name}: {detail}", flush=True)
        if status == "FAIL":
            self.failed = True
            if fix:
                print(f"      Fix: {fix}", flush=True)

    def passed(self, name: str, detail: str) -> None:
        self.add("PASS", name, detail)

    def failure(self, name: str, detail: str, fix: str | None = None) -> None:
        self.add("FAIL", name, detail, fix)

    def skipped(self, name: str, detail: str) -> None:
        self.add("SKIP", name, detail)


def _request(base_url: str, method: str, path: str, key: str | None = None):
    """Returns (HTTP status, parsed JSON or None, milliseconds). Status 0 means no response."""
    headers = {"User-Agent": "barbershop-preflight/1.0", "Content-Type": "application/json"}
    if key is not None:
        headers["X-API-Key"] = key
    data = b"{}" if method == "POST" else None
    request = urllib.request.Request(base_url + path, data=data, headers=headers, method=method)

    started = time.perf_counter()
    try:
        with _OPENER.open(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            status, raw = response.status, response.read()
    except urllib.error.HTTPError as error:
        status, raw = error.code, error.read()
    except (urllib.error.URLError, OSError, http.client.HTTPException):
        status, raw = 0, b""
    elapsed = (time.perf_counter() - started) * 1000

    try:
        return status, json.loads(raw), elapsed
    except ValueError:
        return status, None, elapsed


def check_secrets(report: Report) -> None:
    values = {name: os.environ.get(name, "") for name in SECRETS}
    for name, value in values.items():
        if not value:
            report.failure(name, "not set", f"Set {name}. {GENERATE}")
        elif any(word in value.lower() for word in PLACEHOLDER_WORDS):
            report.failure(name, "looks like a placeholder", f"Replace it. {GENERATE}")
        elif len(value) < MINIMUM_SECRET_LENGTH:
            report.failure(name, f"too short, {len(value)} chars", f"Use at least {MINIMUM_SECRET_LENGTH}. {GENERATE}")
        else:
            report.passed(name, f"set, {len(value)} chars")

    present = [value for value in values.values() if value]
    if len(set(present)) != len(present):
        report.failure("Secrets differ", "two of the secrets have the same value", f"Give each its own value. {GENERATE}")
    else:
        report.passed("Secrets differ", "no two are the same")


def check_timezone(report: Report) -> None:
    if os.environ.get("APP_TIMEZONE") == TIMEZONE:
        report.passed("APP_TIMEZONE", TIMEZONE)
    else:
        report.failure("APP_TIMEZONE", f"must be {TIMEZONE}", f"Set APP_TIMEZONE={TIMEZONE}")


def _database_file(report: Report) -> Path | None:
    """The demo database file if DATABASE_URL is acceptable. The URL itself is never printed."""
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        report.failure("DATABASE_URL", "not set", RESEED)
        return None
    if "barbershop.db" in url.lower():
        report.failure("DATABASE_URL", "names barbershop.db, which may hold real data", RESEED)
        return None
    if not url.startswith(SQLITE_PREFIX):
        report.failure("DATABASE_URL", "is not a SQLite file", RESEED)
        return None

    path = url[len(SQLITE_PREFIX):].split("?")[0]
    if not path or ":memory:" in path:
        report.failure("DATABASE_URL", "is not a SQLite file", RESEED)
        return None

    database = Path(path)
    if not database.is_file():
        report.failure("DATABASE_URL", "the file does not exist", RESEED)
        return None
    size = database.stat().st_size
    if size == 0:
        report.failure("DATABASE_URL", "the file is empty", RESEED)
        return None

    report.passed("DATABASE_URL", f"SQLite file, {size} bytes")
    return database


def _schema_is_current(report: Report) -> bool:
    engine = create_engine(os.environ["DATABASE_URL"])
    try:
        require_current_schema(engine)
    except DatabaseSchemaOutOfDate as outdated:
        report.failure("Schema", "the database is out of date", str(outdated))
        return False
    except Exception:
        report.failure("Schema", "this does not look like the app's database", RESEED)
        return False
    finally:
        engine.dispose()

    report.passed("Schema", "all required columns are present")
    return True


def _check_demo_data(report: Report, database: Path) -> None:
    now = datetime.now(ZoneInfo(TIMEZONE))
    today = now.date()
    uri = f"file:{urllib.parse.quote(str(database.resolve()))}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    try:
        future = connection.execute(
            "SELECT count(*) FROM queue_entries WHERE status IN ('waiting', 'next') "
            "AND queue_date || ' ' || appointment_time > ?",
            (now.strftime("%Y-%m-%d %H:%M:%S"),),
        ).fetchone()[0]
        waiting = connection.execute(
            "SELECT count(*) FROM waitlist_entries WHERE status = 'active' AND requested_date >= ?",
            (today.isoformat(),),
        ).fetchone()[0]
        earliest = connection.execute("SELECT min(queue_date) FROM queue_entries").fetchone()[0]
    except sqlite3.Error:
        report.failure("Demo data", "could not be read", RESEED)
        return
    finally:
        connection.close()

    if future < 1 or waiting < 1:
        report.failure("Demo data", f"{future} future appointments, {waiting} open waitlist entries", RESEED)
    else:
        report.passed("Demo data", f"{future} future appointments, {waiting} open waitlist entries")

    if earliest is not None and date.fromisoformat(earliest) < today - timedelta(days=1):
        report.add("WARN", "Seeded dates", f"the earliest appointment is more than a day old. {RESEED}")
    else:
        report.passed("Seeded dates", "still current")


def check_database(report: Report) -> None:
    database = _database_file(report)
    if database is None:
        report.skipped("Schema", "needs a usable DATABASE_URL")
        report.skipped("Demo data", "needs a usable DATABASE_URL")
        return
    if not _schema_is_current(report):
        report.skipped("Demo data", "needs a current schema")
        return
    _check_demo_data(report, database)


def check_email(report: Report, allow_real_email: bool) -> None:
    if allow_real_email:
        report.skipped("EMAIL_DRY_RUN", "--allow-real-email was given")
    elif os.environ.get("EMAIL_DRY_RUN", "true").lower() == "true":
        report.passed("EMAIL_DRY_RUN", "true, so no real email is sent")
    else:
        report.failure(
            "EMAIL_DRY_RUN", "real email would be sent", "Set EMAIL_DRY_RUN=true, or pass --allow-real-email"
        )


def check_webhook_secret(report: Report, fallback: bool) -> None:
    name = "SUISSELINE_WEBHOOK_SECRET"
    if not fallback:
        report.skipped(name, "only needed with --fallback")
        return
    value = os.environ.get(name, "")
    if value:
        report.passed(name, f"set, {len(value)} chars")
    else:
        report.failure(name, "not set", f"Set {name} to the signing secret. {GENERATE}")


def check_application(report: Report, label: str, base_url: str, key: str, start_fix: str) -> float | None:
    """Checks one running assistant app. Returns the info round trip in milliseconds if it worked."""
    status, _, _ = _request(base_url, "POST", "/assistant/info", WRONG_KEY)
    if status == 0:
        report.failure(label, "nothing is answering", start_fix)
        return None
    if status == 401:
        report.passed(f"{label}: wrong key", "401")
    else:
        report.failure(f"{label}: wrong key", f"expected 401, got {status}")

    elapsed = None
    if not key:
        report.failure(f"{label}: right key", "ASSISTANT_API_KEY is not set", GENERATE)
    else:
        status, payload, milliseconds = _request(base_url, "POST", "/assistant/info", key)
        if status == 200 and isinstance(payload, dict) and payload.get("ok") is True:
            report.passed(f"{label}: right key", "ok: true")
            elapsed = milliseconds
        else:
            report.failure(
                f"{label}: right key",
                f"expected ok: true, got status {status}",
                "Use the same ASSISTANT_API_KEY here and in the terminal running the app.",
            )

    for path in MUST_BE_HIDDEN:
        status, _, _ = _request(base_url, "GET", path)
        if status == 404:
            report.passed(f"{label}: {path}", "404")
        else:
            report.failure(f"{label}: {path}", f"expected 404, got {status}", "Tunnel only app.assistant_main, on its own port.")
    return elapsed


def public_address_problem(url: str) -> str | None:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https":
        return "the address must start with https://, so nothing was sent"
    if not parsed.hostname:
        return "the address has no host, so nothing was sent"
    if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ("", "/"):
        return "give only the scheme and host, so nothing was sent"
    return None


def check_public_address(report: Report, url: str | None, key: str) -> None:
    if url is None:
        report.skipped("Public address", "no --public-url given")
        return
    problem = public_address_problem(url)
    if problem:
        report.failure("Public address", problem, "Pass the https address that cloudflared printed.")
        return

    elapsed = check_application(report, "Public address", url.rstrip("/"), key, START_TUNNEL)
    if elapsed is not None:
        report.passed("Public address: latency", f"{elapsed:.0f} ms for one info call")


def print_tunnel_reminder() -> None:
    print(
        "NOTE  Tunnel: a quick tunnel can take over a minute to start answering, and its "
        "address changes on restart, so update the dashboard URL if it restarts.",
        flush=True,
    )


def check_cloudflared(report: Report) -> None:
    executable = shutil.which("cloudflared")
    if executable is None:
        report.failure("cloudflared", "not installed", "brew install cloudflared")
        return
    try:
        result = subprocess.run([executable, "--version"], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        report.failure("cloudflared", "could not be run", "brew reinstall cloudflared")
        return
    report.passed("cloudflared", (result.stdout.strip().splitlines() or ["installed"])[0])


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check that you are ready to run a call session.")
    parser.add_argument("--local-port", type=int, default=8003, help="port of the assistant app (default 8003)")
    parser.add_argument("--public-url", help="the tunnel's https address")
    parser.add_argument("--fallback", action="store_true", help="also require the webhook secret")
    parser.add_argument("--allow-real-email", action="store_true", help="allow EMAIL_DRY_RUN to be false")
    options = parser.parse_args(arguments)

    load_dotenv(REPOSITORY / ".env")
    key = os.environ.get("ASSISTANT_API_KEY", "")
    report = Report()

    check_secrets(report)
    check_timezone(report)
    check_database(report)
    check_email(report, options.allow_real_email)
    check_webhook_secret(report, options.fallback)
    check_application(
        report,
        "Local app",
        f"http://127.0.0.1:{options.local_port}",
        key,
        START_APP.format(port=options.local_port),
    )
    check_public_address(report, options.public_url, key)
    print_tunnel_reminder()
    check_cloudflared(report)

    print("NOT READY" if report.failed else "READY", flush=True)
    return 1 if report.failed else 0


if __name__ == "__main__":
    sys.exit(main())
