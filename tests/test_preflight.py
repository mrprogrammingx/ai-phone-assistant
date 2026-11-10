"""The activation-day preflight. Every value below is fake and exists only for these tests."""
import importlib.util
import json
import math
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest

REPOSITORY = Path(__file__).resolve().parents[1]
SCRIPT = REPOSITORY / "scripts" / "preflight.py"
SEED = REPOSITORY / "scripts" / "seed_demo.py"

API_KEY = "fake-assistant-value-0123456789abcdef"
ADMIN_PASSWORD = "fake-admin-value-0123456789abcdef"
SESSION_SECRET = "fake-session-value-0123456789abcdef"
WEBHOOK_SECRET = "fake-webhook-value-0123456789abcdef"


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def seed(database):
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{database}", "APP_TIMEZONE": "Europe/Zurich"}
    result = subprocess.run([sys.executable, str(SEED)], cwd=REPOSITORY, env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr


@pytest.fixture(scope="module")
def demo_database(tmp_path_factory):
    database = tmp_path_factory.mktemp("preflight") / "demo.db"
    seed(database)
    return database


@pytest.fixture(scope="module")
def assistant_port(demo_database):
    port = free_port()
    env = {
        **os.environ,
        "DATABASE_URL": f"sqlite:///{demo_database}",
        "APP_TIMEZONE": "Europe/Zurich",
        "EMAIL_DRY_RUN": "true",
        "ASSISTANT_API_KEY": API_KEY,
    }
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.assistant_main:app", "--host", "127.0.0.1", "--port", str(port), "--log-level", "critical"],
        cwd=REPOSITORY,
        env=env,
    )
    try:
        for _ in range(100):
            try:
                httpx.post(f"http://127.0.0.1:{port}/assistant/info", timeout=1)
                break
            except httpx.TransportError:
                time.sleep(0.1)
        yield port
    finally:
        server.terminate()
        server.wait()


@pytest.fixture(scope="module")
def tools_path(tmp_path_factory):
    folder = tmp_path_factory.mktemp("bin")
    tool = folder / "cloudflared"
    tool.write_text('#!/bin/sh\necho "cloudflared version 9.9.9 (fake)"\n')
    tool.chmod(0o755)
    return str(folder)


@pytest.fixture
def run(demo_database, assistant_port, tools_path):
    """Runs the command with a valid environment, with any values overridden."""

    def runner(*arguments, port=None, **overrides):
        env = {
            "PATH": tools_path,
            "ASSISTANT_API_KEY": API_KEY,
            "ADMIN_PASSWORD": ADMIN_PASSWORD,
            "SESSION_SECRET_KEY": SESSION_SECRET,
            "SUISSELINE_WEBHOOK_SECRET": "",
            "APP_TIMEZONE": "Europe/Zurich",
            "DATABASE_URL": f"sqlite:///{demo_database}",
            "EMAIL_DRY_RUN": "true",
            **overrides,
        }
        command = [sys.executable, str(SCRIPT), "--local-port", str(port or assistant_port), *arguments]
        return subprocess.run(command, cwd=REPOSITORY, env=env, capture_output=True, text=True, timeout=120)

    return runner


def lines_of(result):
    return result.stdout.splitlines()


def assert_not_ready(result, fragment):
    assert fragment in result.stdout, result.stdout
    assert result.returncode == 1
    assert lines_of(result)[-1] == "NOT READY"


def test_a_ready_environment_passes(run):
    result = run()

    assert result.returncode == 0, result.stdout + result.stderr
    assert lines_of(result)[-1] == "READY"
    assert "FAIL" not in result.stdout


def test_each_check_is_reported(run):
    output = run().stdout

    for fragment in (
        "PASS  ASSISTANT_API_KEY: set, 37 chars",
        "PASS  ADMIN_PASSWORD: set, 33 chars",
        "PASS  SESSION_SECRET_KEY: set, 35 chars",
        "PASS  APP_TIMEZONE",
        "PASS  DATABASE_URL",
        "PASS  Schema",
        "PASS  Demo data",
        "PASS  Seeded dates",
        "PASS  EMAIL_DRY_RUN",
        "SKIP  SUISSELINE_WEBHOOK_SECRET",
        "PASS  Local app: wrong key",
        "PASS  Local app: right key",
        "PASS  Local app: /docs",
        "PASS  Local app: /admin/shop-status",
        "SKIP  Public address",
        "PASS  cloudflared: cloudflared version 9.9.9",
    ):
        assert fragment in output, output


def test_the_tunnel_reminder_is_always_printed(run):
    output = run().stdout

    assert "over a minute" in output
    assert "address changes" in output
    assert "dashboard" in output


@pytest.mark.parametrize(
    "name",
    ["ASSISTANT_API_KEY", "ADMIN_PASSWORD", "SESSION_SECRET_KEY"],
)
@pytest.mark.parametrize(
    "value",
    ["", "short-value", "please-change-this-value-0123456789", "replace-with-a-long-random-value", "an-example-value-0123456789abcdef"],
    ids=["unset", "short", "change", "replace", "example"],
)
def test_weak_secrets_fail(run, name, value):
    result = run(**{name: value})

    assert_not_ready(result, f"FAIL  {name}")


def test_placeholder_words_are_rejected_in_any_case(run):
    assert_not_ready(run(ADMIN_PASSWORD="REPLACE-THIS-VALUE-0123456789ABCDEF"), "FAIL  ADMIN_PASSWORD")


def test_a_failed_secret_shows_the_fix(run):
    assert "secrets.token_urlsafe" in run(ADMIN_PASSWORD="").stdout


@pytest.mark.parametrize(
    "overrides",
    [
        {"ADMIN_PASSWORD": API_KEY},
        {"SESSION_SECRET_KEY": API_KEY},
        {"SESSION_SECRET_KEY": ADMIN_PASSWORD},
    ],
    ids=["admin-key", "session-key", "session-admin"],
)
def test_secrets_that_match_each_other_fail(run, overrides):
    assert_not_ready(run(**overrides), "FAIL  Secrets differ")


@pytest.mark.parametrize("value", ["", "Asia/Dubai", "europe/zurich"])
def test_wrong_time_zone_fails(run, value):
    assert_not_ready(run(APP_TIMEZONE=value), "FAIL  APP_TIMEZONE")


@pytest.mark.parametrize(
    "url",
    ["", "postgresql://user@localhost/demo", "sqlite://", "sqlite:///:memory:"],
    ids=["unset", "postgres", "bare-sqlite", "memory"],
)
def test_a_database_url_that_is_not_a_sqlite_file_fails(run, url):
    assert_not_ready(run(DATABASE_URL=url), "FAIL  DATABASE_URL")


@pytest.mark.parametrize("name", ["barbershop.db", "copy-of-BARBERSHOP.DB", "barbershop.db.bak"])
def test_barbershop_db_is_refused_even_when_it_exists(run, tmp_path, name):
    database = tmp_path / name
    seed(tmp_path / "source.db")
    shutil.copy(tmp_path / "source.db", database)

    assert_not_ready(run(DATABASE_URL=f"sqlite:///{database}"), "FAIL  DATABASE_URL")


def test_a_missing_database_file_fails(run, tmp_path):
    database = tmp_path / "missing.db"

    assert_not_ready(run(DATABASE_URL=f"sqlite:///{database}"), "FAIL  DATABASE_URL")
    assert not database.exists()


def test_an_empty_database_file_fails(run, tmp_path):
    database = tmp_path / "empty.db"
    database.touch()

    assert_not_ready(run(DATABASE_URL=f"sqlite:///{database}"), "FAIL  DATABASE_URL")


def test_later_database_checks_are_skipped_after_a_bad_url(run):
    output = run(DATABASE_URL="").stdout

    assert "SKIP  Schema" in output
    assert "SKIP  Demo data" in output


def test_an_out_of_date_schema_fails_with_the_commands(run, tmp_path):
    database = tmp_path / "old.db"
    seed(database)
    connection = sqlite3.connect(database)
    connection.execute("ALTER TABLE queue_entries DROP COLUMN duration_minutes")
    connection.commit()
    connection.close()

    result = run(DATABASE_URL=f"sqlite:///{database}")

    assert_not_ready(result, "FAIL  Schema")
    assert "ALTER TABLE" in result.stdout


def test_a_database_that_is_not_the_apps_fails_the_schema_check(run, tmp_path):
    database = tmp_path / "other.db"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE unrelated (id INTEGER PRIMARY KEY)")
    connection.commit()
    connection.close()

    assert_not_ready(run(DATABASE_URL=f"sqlite:///{database}"), "FAIL  Schema")


def seeded_copy(tmp_path, *statements):
    database = tmp_path / "copy.db"
    seed(database)
    connection = sqlite3.connect(database)
    for statement in statements:
        connection.execute(statement)
    connection.commit()
    connection.close()
    return database


def test_no_future_appointment_fails(run, tmp_path):
    database = seeded_copy(tmp_path, "DELETE FROM queue_entries")

    assert_not_ready(run(DATABASE_URL=f"sqlite:///{database}"), "FAIL  Demo data")


def test_no_open_waitlist_entry_fails(run, tmp_path):
    database = seeded_copy(tmp_path, "DELETE FROM waitlist_entries")

    assert_not_ready(run(DATABASE_URL=f"sqlite:///{database}"), "FAIL  Demo data")


def test_preflight_does_not_change_the_database(run, tmp_path):
    database = seeded_copy(tmp_path)
    before = database.read_bytes()

    run(DATABASE_URL=f"sqlite:///{database}")

    assert database.read_bytes() == before


def test_old_seeded_dates_warn_but_do_not_fail(run, tmp_path):
    old = (date.today() - timedelta(days=3)).isoformat()
    database = seeded_copy(tmp_path, f"UPDATE queue_entries SET queue_date = '{old}' WHERE id = 1")

    result = run(DATABASE_URL=f"sqlite:///{database}")

    assert "WARN  Seeded dates" in result.stdout
    assert "seed_demo.py" in result.stdout
    assert result.returncode == 0
    assert lines_of(result)[-1] == "READY"


@pytest.mark.parametrize("value", ["false", "", "0"])
def test_real_email_fails_by_default(run, value):
    assert_not_ready(run(EMAIL_DRY_RUN=value), "FAIL  EMAIL_DRY_RUN")


def test_real_email_can_be_allowed(run):
    result = run("--allow-real-email", EMAIL_DRY_RUN="false")

    assert "FAIL" not in result.stdout
    assert result.returncode == 0


def test_webhook_secret_is_skipped_by_default(run):
    assert "SKIP  SUISSELINE_WEBHOOK_SECRET" in run().stdout


def test_webhook_secret_is_required_in_fallback_mode(run):
    assert_not_ready(run("--fallback"), "FAIL  SUISSELINE_WEBHOOK_SECRET")


def test_webhook_secret_passes_in_fallback_mode(run):
    result = run("--fallback", SUISSELINE_WEBHOOK_SECRET=WEBHOOK_SECRET)

    assert "PASS  SUISSELINE_WEBHOOK_SECRET: set, 35 chars" in result.stdout
    assert result.returncode == 0


def test_nothing_listening_fails_with_the_start_command(run):
    result = run(port=free_port())

    assert_not_ready(result, "FAIL  Local app")
    assert "uvicorn app.assistant_main:app" in result.stdout


def test_the_wrong_configured_key_fails_the_right_key_check(run):
    assert_not_ready(run(ASSISTANT_API_KEY="fake-different-value-0123456789abcdef"), "FAIL  Local app: right key")


class FakeServer:
    """A stand-in server that records what it receives."""

    def __init__(self, overrides=None):
        self.overrides = overrides or {}
        self.requests = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def _respond(self):
                outer.requests.append((self.path, dict(self.headers)))
                status, headers, body = outer.reply(self.path, self.headers.get("X-API-Key"))
                self.send_response(status)
                for name, value in headers.items():
                    self.send_header(name, value)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_GET = do_POST = _respond

            def log_message(self, *_args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def port(self):
        return self.server.server_address[1]

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}"

    def reply(self, path, key):
        if path in self.overrides:
            return self.overrides[path]
        if path == "/assistant/info":
            if key != API_KEY:
                return 401, {}, b"{}"
            return 200, {}, json.dumps({"ok": True}).encode()
        return 404, {}, b"{}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def fake_server():
    servers = []

    def make(overrides=None):
        server = FakeServer(overrides)
        servers.append(server)
        return server

    yield make
    for server in servers:
        server.close()


@pytest.mark.parametrize("path", ["/docs", "/admin/shop-status"])
def test_an_exposed_path_fails(run, fake_server, path):
    server = fake_server({path: (200, {}, b"{}")})

    assert_not_ready(run(port=server.port), f"FAIL  Local app: {path}")


def test_a_wrong_key_that_is_accepted_fails(run, fake_server):
    server = fake_server({"/assistant/info": (200, {}, json.dumps({"ok": True}).encode())})

    assert_not_ready(run(port=server.port), "FAIL  Local app: wrong key")


def test_an_info_response_without_ok_fails(run, fake_server):
    server = fake_server()
    server.reply = lambda path, key: (
        (200, {}, b'{"ok": false}') if path == "/assistant/info" and key == API_KEY else
        ((401, {}, b"{}") if path == "/assistant/info" else (404, {}, b"{}"))
    )

    assert_not_ready(run(port=server.port), "FAIL  Local app: right key")


@pytest.mark.parametrize(
    "url",
    [
        "http://example.invalid",
        "ftp://example.invalid",
        "example.invalid",
        "https://user:pass@example.invalid",
        "https://example.invalid/some/path",
        "https://",
    ],
    ids=["http", "ftp", "no-scheme", "credentials", "path", "no-host"],
)
def test_an_unacceptable_public_address_is_refused(run, fake_server, url):
    result = run("--public-url", url)

    assert_not_ready(result, "FAIL  Public address")


def test_a_public_http_address_is_never_contacted(run, fake_server):
    server = fake_server()

    result = run("--public-url", server.url)

    assert_not_ready(result, "FAIL  Public address")
    assert server.requests == []


def test_a_public_address_that_does_not_answer_fails(run):
    result = run("--public-url", f"https://127.0.0.1:{free_port()}")

    assert_not_ready(result, "FAIL  Public address")


def load_preflight():
    spec = importlib.util.spec_from_file_location("preflight_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_application_checks_return_the_round_trip_time(fake_server, capsys):
    preflight = load_preflight()
    report = preflight.Report()

    elapsed = preflight.check_application(report, "Public address", fake_server().url, API_KEY, "start it")

    assert isinstance(elapsed, float)
    assert not report.failed
    assert API_KEY not in capsys.readouterr().out


def test_public_checks_print_the_latency(fake_server, capsys, monkeypatch):
    preflight = load_preflight()
    monkeypatch.setattr(preflight, "public_address_problem", lambda url: None)
    report = preflight.Report()

    preflight.check_public_address(report, fake_server().url, API_KEY)

    output = capsys.readouterr().out
    assert "Public address: latency" in output
    assert " ms" in output


def test_a_redirect_never_carries_the_key_to_another_host(fake_server, capsys):
    preflight = load_preflight()
    other = fake_server()
    first = fake_server({"/assistant/info": (302, {"Location": f"{other.url}/assistant/info"}, b"")})
    report = preflight.Report()

    preflight.check_application(report, "Public address", first.url, API_KEY, "start it")

    assert other.requests == []
    assert report.failed


@pytest.mark.parametrize("variable", ["HTTP_PROXY", "http_proxy", "ALL_PROXY"])
def test_a_proxy_setting_never_receives_the_key(fake_server, capsys, monkeypatch, variable):
    proxy = fake_server()
    target = fake_server()
    monkeypatch.setenv(variable, proxy.url)
    preflight = load_preflight()
    report = preflight.Report()

    preflight.check_application(report, "Public address", target.url, API_KEY, "start it")

    assert proxy.requests == []
    assert target.requests


def test_cloudflared_missing_fails_with_the_install_command(run, tmp_path):
    result = run(PATH=str(tmp_path))

    assert_not_ready(result, "FAIL  cloudflared")
    assert "brew install cloudflared" in result.stdout


def test_no_secret_value_is_ever_printed(run, tmp_path):
    secrets = {
        "ASSISTANT_API_KEY": "Qx7-short-assistant",
        "ADMIN_PASSWORD": "Qx7-replace-admin-value-0123456789",
        "SESSION_SECRET_KEY": "Qx7-replace-admin-value-0123456789",
        "SUISSELINE_WEBHOOK_SECRET": "Qx7-webhook-value-0123456789abcdef",
    }
    database_url = "postgresql://demo-user:Qx7-database-password@localhost/demo"

    result = run("--fallback", DATABASE_URL=database_url, **secrets)
    passing = run("--fallback", SUISSELINE_WEBHOOK_SECRET=WEBHOOK_SECRET)

    combined = result.stdout + result.stderr + passing.stdout + passing.stderr
    assert result.returncode == 1
    for value in [*secrets.values(), API_KEY, ADMIN_PASSWORD, SESSION_SECRET, WEBHOOK_SECRET, "Qx7-database-password", database_url]:
        assert value not in combined


def test_the_key_and_public_address_are_not_printed_on_success(fake_server, capsys, monkeypatch):
    preflight = load_preflight()
    monkeypatch.setattr(preflight, "public_address_problem", lambda url: None)
    server = fake_server()

    preflight.check_public_address(preflight.Report(), server.url, API_KEY)

    output = capsys.readouterr().out
    assert API_KEY not in output
    assert server.url not in output


def test_values_are_read_from_a_dotenv_file_and_stay_private(tmp_path, demo_database, assistant_port, tools_path):
    (tmp_path / "scripts").mkdir()
    copied = tmp_path / "scripts" / "preflight.py"
    shutil.copy(SCRIPT, copied)
    (tmp_path / ".env").write_text(
        f"ASSISTANT_API_KEY={API_KEY}\nADMIN_PASSWORD={ADMIN_PASSWORD}\nSESSION_SECRET_KEY={SESSION_SECRET}\n"
        f"APP_TIMEZONE=Europe/Zurich\nDATABASE_URL=sqlite:///{demo_database}\nEMAIL_DRY_RUN=true\n"
    )

    result = subprocess.run(
        [sys.executable, str(copied), "--local-port", str(assistant_port)],
        cwd=tmp_path,
        env={"PATH": tools_path, "PYTHONPATH": str(REPOSITORY)},
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS  ASSISTANT_API_KEY: set, 37 chars" in result.stdout
    assert API_KEY not in result.stdout + result.stderr


def test_no_document_tells_anyone_to_generate_a_short_secret():
    sources = [REPOSITORY / "README.md", *(REPOSITORY / "docs").rglob("*"), *(REPOSITORY / "scripts").rglob("*.py")]
    found = []
    for source in sources:
        if source.is_file():
            found += re.findall(r"token_(urlsafe|hex)\((\d*)\)", source.read_text(encoding="utf-8"))

    assert found
    for kind, size in found:
        size_in_bytes = int(size or 32)
        length = size_in_bytes * 2 if kind == "hex" else math.ceil(size_in_bytes * 4 / 3)
        assert length >= 24, f"token_{kind}({size}) produces only {length} characters"
