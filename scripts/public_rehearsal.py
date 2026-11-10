#!/usr/bin/env python3
"""Public endpoint rehearsal. Uses the standard library only.

    python scripts/public_rehearsal.py probe https://example.trycloudflare.com KEY
    python scripts/public_rehearsal.py race http://127.0.0.1:8002 http://127.0.0.1:8003 KEY

`probe` checks what the tunnel exposes, runs one full flow on fictional data five
times and prints the latency of every call. `race` books one slot through the website
and the assistant at the same moment. The key is never printed. Exit code 0 means
every check passed.
"""
import json
import random
import statistics
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

PLACEHOLDER_KEY = "replace-with-a-long-random-value"
PROBE_RUNS = 5
RACE_ROUNDS = 20
RACE_REQUESTS_PER_APP = 6
SERVICE = "classic-cut"
# Some Cloudflare rules refuse the default Python client name.
USER_AGENT = "barbershop-rehearsal-probe/1.0"
MUST_NOT_BE_EXPOSED = [
    ("GET", "/docs"),
    ("GET", "/openapi.json"),
    ("GET", "/admin/shop-status"),
    ("GET", "/queue/my-bookings?phone=%2B41790000000"),
    ("POST", "/api/login"),
]


def call(base, method, path, body=None, key=None):
    """Returns (HTTP status, parsed JSON or None, milliseconds). Status 0 means no response."""
    headers = {"User-Agent": USER_AGENT, "Content-Type": "application/json"}
    if key is not None:
        headers["X-API-Key"] = key
    data = json.dumps(body).encode() if body is not None else (b"{}" if method == "POST" else None)
    request = urllib.request.Request(base.rstrip("/") + path, data=data, headers=headers, method=method)

    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            status, raw = response.status, response.read()
    except urllib.error.HTTPError as error:
        status, raw = error.code, error.read()
    except (urllib.error.URLError, TimeoutError):
        status, raw = 0, b""
    elapsed = (time.perf_counter() - started) * 1000

    try:
        return status, json.loads(raw), elapsed
    except ValueError:
        return status, None, elapsed


def is_local(url):
    return urllib.parse.urlparse(url).hostname in ("127.0.0.1", "localhost", "::1")


def fictional_phone(prefix="79"):
    return f"+41{prefix}{random.randrange(10**7):07d}"


class Probe:
    def __init__(self, base, key):
        self.base, self.key = base, key
        self.failures = []
        self.checks = 0
        self.latency = {}

    def request(self, label, method, path, body=None, key=None):
        status, payload, elapsed = call(self.base, method, path, body, key)
        self.latency.setdefault(label, []).append(elapsed)
        return status, payload

    def tool(self, label, path, body=None):
        return self.request(label, "POST", f"/assistant/{path}", body, self.key)

    def check(self, run, name, passed, status, payload):
        self.checks += 1
        if not passed:
            code = payload.get("code") if isinstance(payload, dict) else None
            self.failures.append(f"run {run}: {name}: status={status} code={code}")

    def exposure_checks(self, run):
        for method, path in MUST_NOT_BE_EXPOSED:
            label = f"{method} {path.split('?')[0]} (must be 404)"
            status, payload = self.request(label, method, path, key=self.key)
            self.check(run, label, status == 404, status, payload)

    def auth_checks(self, run):
        status, payload = self.request("info, no key (401)", "POST", "/assistant/info")
        self.check(run, "info with no key", status == 401, status, payload)
        status, payload = self.request("info, wrong key (401)", "POST", "/assistant/info", key="wrong-key")
        self.check(run, "info with a wrong key", status == 401, status, payload)
        status, payload = self.request("info, right key", "POST", "/assistant/info", key=self.key)
        passed = status == 200 and isinstance(payload, dict) and payload.get("ok") is True
        self.check(run, "info with the right key", passed, status, payload)
        return payload.get("today") if passed else None

    def flow(self, run, today):
        day = (date.fromisoformat(today) + timedelta(days=14 + run)).isoformat()
        phone = fictional_phone()

        status, payload = self.tool("availability", "availability", {"date": day, "service_id": SERVICE})
        slots = payload.get("slots") if isinstance(payload, dict) else None
        self.check(run, "availability returns slots", status == 200 and bool(slots), status, payload)
        if not slots:
            return

        booking = {"name": "Fictional Rehearsal", "phone": phone, "date": day, "time": slots[0], "service_id": SERVICE}
        status, created = self.tool("create booking", "bookings", booking)
        self.check(run, "create booking", status == 200 and _is(created, "ok") and _is(created, "created"), status, created)
        if not _is(created, "ok"):
            return
        booking_id = created["booking"]["id"]

        status, retried = self.tool("retry booking", "bookings", booking)
        same = _is(retried, "ok") and retried.get("created") is False and retried["booking"]["id"] == booking_id
        self.check(run, "retry returns created: false", status == 200 and same, status, retried)

        status, found = self.tool("find booking", "bookings/find", {"phone": phone})
        listed = _is(found, "ok") and booking_id in [item["id"] for item in found["bookings"]]
        self.check(run, "find lists the booking", status == 200 and listed, status, found)

        status, cancelled = self.tool(
            "cancel booking", "bookings/cancel", {"phone": phone, "date": day, "time": slots[0]}
        )
        passed = _is(cancelled, "cancelled") and cancelled.get("already_cancelled") is False
        self.check(run, "cancel booking", status == 200 and passed, status, cancelled)

        status, joined = self.tool(
            "join waitlist",
            "waitlist",
            {
                "name": "Fictional Rehearsal",
                "phone": phone,
                "service_id": SERVICE,
                "date": day,
                "from": "09:00",
                "to": "17:00",
                "consent": True,
            },
        )
        self.check(run, "join waitlist with consent", status == 200 and _is(joined, "created"), status, joined)

    def run(self):
        for run in range(1, PROBE_RUNS + 1):
            self.exposure_checks(run)
            today = self.auth_checks(run)
            if today is not None:
                self.flow(run, today)

    def report(self):
        width = max(len(label) for label in self.latency)
        print(f"{'call'.ljust(width)}  runs  median ms  min ms  max ms")
        for label, samples in self.latency.items():
            print(
                f"{label.ljust(width)}  {len(samples):>4}  {statistics.median(samples):>9.0f}"
                f"  {min(samples):>6.0f}  {max(samples):>6.0f}"
            )
        print()
        print(f"checks: {self.checks}, failed: {len(self.failures)}")
        for failure in self.failures:
            print(f"FAIL {failure}")
        print("PROBE PASSED" if not self.failures else "PROBE FAILED")
        return 0 if not self.failures else 1


def _is(payload, field):
    return isinstance(payload, dict) and payload.get(field) is True


def probe(base, key):
    if key == PLACEHOLDER_KEY and not is_local(base):
        print("Refusing: replace the placeholder key before using a public address.", file=sys.stderr)
        return 2
    runner = Probe(base, key)
    runner.run()
    return runner.report()


def race(website, assistant, key):
    if not is_local(website):
        print("Refusing: the website app must stay on localhost and is never tunnelled.", file=sys.stderr)
        return 2

    status, info, _ = call(assistant, "POST", "/assistant/info", key=key)
    if status != 200 or not _is(info, "ok"):
        print(f"Cannot read the assistant app: status={status}", file=sys.stderr)
        return 1

    first_day = date.fromisoformat(info["today"]) + timedelta(days=random.randrange(30, 300))
    total = RACE_REQUESTS_PER_APP * 2
    bad_rounds, other_statuses = [], {}

    for round_number in range(1, RACE_ROUNDS + 1):
        day = (first_day + timedelta(days=round_number)).isoformat()
        start_together = threading.Barrier(total)

        def through_website(_):
            start_together.wait()
            body = {
                "name": "Fictional Race",
                "phone": fictional_phone("78"),
                "appointment_date": day,
                "appointment_time": "10:00:00",
            }
            code, _payload, _elapsed = call(website, "POST", "/queue/checkin", body)
            return code

        def through_assistant(_):
            start_together.wait()
            body = {"name": "Fictional Race", "phone": fictional_phone(), "date": day, "time": "10:00", "service_id": SERVICE}
            code, payload, _elapsed = call(assistant, "POST", "/assistant/bookings", body, key)
            if code == 200:
                return 200 if _is(payload, "ok") else 400
            return code

        with ThreadPoolExecutor(total) as pool:
            website_codes = [pool.submit(through_website, i) for i in range(RACE_REQUESTS_PER_APP)]
            assistant_codes = [pool.submit(through_assistant, i) for i in range(RACE_REQUESTS_PER_APP)]
            codes = [future.result() for future in website_codes + assistant_codes]

        if codes.count(200) != 1:
            bad_rounds.append((round_number, codes.count(200)))
        for code in codes:
            if code not in (200, 400):
                other_statuses[code] = other_statuses.get(code, 0) + 1

    print(f"rounds: {RACE_ROUNDS}, requests per round: {total} ({RACE_REQUESTS_PER_APP} per app)")
    print(f"rounds without exactly one success: {len(bad_rounds)} {bad_rounds}")
    print(f"unexpected statuses: {other_statuses}")
    passed = not bad_rounds and not other_statuses
    print("RACE PASSED" if passed else "RACE FAILED")
    return 0 if passed else 1


def main(arguments):
    if len(arguments) == 3 and arguments[0] == "probe":
        return probe(arguments[1], arguments[2])
    if len(arguments) == 4 and arguments[0] == "race":
        return race(arguments[1], arguments[2], arguments[3])
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
