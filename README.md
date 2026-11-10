# Demo Barbershop phone assistant

A phone assistant for a small, fictional barbershop, built on Suisse Line's AI voice agent for Suisse IT's Challenge A. Callers can ask about services and times, book, find or cancel an appointment, and join a waitlist. While the call is running, the agent calls seven tools on this repository's own booking system, which is the single source of truth for appointments, hours and services. The feature I added is the **Smart Cancellation Waitlist**: when a time is full, the caller can ask to be offered it if it frees up.

All names, numbers and shop details in this repository are fictional.

## How a call flows

```
 Caller ──phone──▶ Suisse Line AI agent
                        │  live tool call, POST, JSON envelope (parameters in tool.arguments)
                        ▼
                  cloudflared quick tunnel ── only port 8003 is public
                        │  https://<tunnel>/assistant/t/<token>/<tool>
                        ▼
 ┌─ app.assistant_main  :8003 ───────────────────────────────────────────┐
 │  wrong token → 404 · 256 KB cap · read tool.arguments only · 7 tools  │
 └───────────────────────────────┬───────────────────────────────────────┘
                                 │ one booking function, one lock
 ┌─ app.main  :8002 ─────────────┤      (website and staff dashboard, localhost only)
 │  booking site, staff login    │
 └───────────────────────────────┤
                                 ▼
                     SQLite database ── single source of truth
                                 ▲
 ┌─ app.calendar_main  :8004 ────┘      (read-only .ics feed, localhost only, optional)
```

The agent reads back service, date with weekday, time, name and phone, waits for a yes, and says an appointment is booked only after a tool answers `ok: true`. Staff see every booking in the dashboard at once. Design and decisions are in [docs/PLAN.md](docs/PLAN.md).

## The seven tools

Each tool is `POST /assistant/t/<token>/<route>` for Suisse Line, and the same route under `/assistant/<route>` with an `X-API-Key` header for scripts. What to type into the dashboard, field by field, is in [docs/dashboard-entry.md](docs/dashboard-entry.md).

| Tool | What it does | Route |
| --- | --- | --- |
| `get_business_info` | Today's date and weekday, opening hours, services with prices and durations | `info` |
| `check_availability` | Up to 5 start times on a date where the whole service fits | `availability` |
| `create_booking` | Books an appointment, safe to retry | `bookings` |
| `find_booking` | A caller's upcoming bookings, at most 3, without names | `bookings/find` |
| `cancel_booking` | Cancels by phone, date and time, safe to repeat | `bookings/cancel` |
| `join_waitlist` | Puts the caller on the waitlist, only with consent | `waitlist` |
| `accept_waitlist_offer` | Books an offered waitlist time when the caller rings back | `waitlist/accept` |

Every answer is HTTP 200 with `ok: true`, or `ok: false` with a `code` such as `slot_taken`, `unknown_service` or `consent_required`, so the agent can always say what happened.

## Smart Cancellation Waitlist

- **Consent first.** A caller joins only after saying yes to being contacted, and the consent time is stored. Only a clear yes counts.
- **Ordered offers.** When a time frees up, `release_slot` offers it to matching entries, oldest first. The whole service must fit, and each offer lasts 30 minutes, or until the slot starts if that is sooner.
- **An offer never reserves the slot.** Anyone can still book it on the website or by phone. Contacting the customer is a manual step, and nothing is sent automatically.
- **The first confirmed booking wins.** Accepting books inside the same lock as every other booking, so two callers can never both get the time. The other open offers for it are marked lost.
- **30-day retention.** Closed waitlist entries are deleted 30 days after they close. Names and numbers are never logged.

## Reliability and security

- **Atomic booking lock.** The slot check and the insert run as one step under a lock taken on the shop's single status row, so concurrent requests cannot double-book. Tested with 12 simultaneous bookings over repeated rounds, and with the website and the assistant booking at the same moment.
- **Idempotent booking.** A repeated booking for the same phone, date and time returns the existing booking with `created: false` instead of a second one. After a timeout, the agent calls `find_booking` before trying again.
- **90-day horizon.** Bookings, availability and the waitlist cover today and the next 90 days. A later date gets `date_too_far`.
- **One phone format.** One function stores every number in E.164, assuming Swiss national numbers, for the website, every tool and the webhook, so one person is one customer however they say their number.
- **Text-only parameters.** The dashboard only offers String or Number. Consent counts only for `yes`, `true`, `ja`, `oui` or `si`. A phone sent as a number is refused, because it has lost its leading `+` or `0`. Dates must be `YYYY-MM-DD` text, so a number is never read as a Unix timestamp.
- **Path-token auth with redaction.** The dashboard cannot send headers, so the token is in the URL path: at least 32 characters, compared in constant time, and anything wrong gets a plain 404, even for malformed requests. The token is redacted from the access log, the app's own log lines and tracebacks.
- **The envelope is read narrowly.** Suisse Line sends the transcript, agent prompt, conversation and caller numbers with every call. The app reads only `tool.arguments`, never logs, stores or echoes the rest, and never uses the caller's number in place of the spoken one.
- **HMAC webhook fallback.** If live tools were unavailable, `POST /assistant/webhook/call-completed` books after the call. It checks an HMAC-SHA256 signature over the raw body, fails closed without a secret, caps the body at 64 KB and ignores repeated deliveries. Its payload shape is an assumption.
- **Private logs and a private calendar feed.** Logs carry the redacted path, a result code and the duration, never names or numbers. Only the assistant app is tunnelled, and staff routes return 404 there. The calendar feed has its own token, shows only service names and times, and turns off the access log because its path holds the token.

## How it was tested

- **1720 automated tests pass** (`venv/bin/python -m pytest -q`, run on a fresh export of this repository). They use a temporary database and a frozen clock in Europe/Zurich, so they never touch the demo data or the Mac's time zone.
- **Property tests** (`tests/test_properties.py`, Hypothesis) generate names, phone spellings, dates and random sequences of bookings, cancellations, no-shows, waitlist offers and staff changes. They check rules that must hold for every input: the website, the assistant and the webhook agree on every phone number, nothing outside the 90-day horizon is ever bookable, and no moment ever has more appointments than the capacity. Runs are derandomized, so every run tries the same inputs.
- **Mutation checks.** During development, each safety rule was broken on purpose, one fault at a time, on a scratch copy, and the suite had to fail. Examples are 20 faults in the envelope adapter and 8 in the text-parameter rules, all caught. The mutation scripts are not part of this repository.
- **The platform's Chat test** found a real problem: the agent sent `service_id` "1" and skipped the read-back. The app refused the unknown service and booked nothing. The system prompt and the `service_id` descriptions now ask for the exact id text, for example `classic-cut`, and forbid booking before the read-back.

## Run it locally

Needs Python 3.12 and Node.js. Run from the repository root.

**Install** (the frontend build is needed for the website and staff dashboard on port 8002):

```bash
python3.12 -m venv venv
```

```bash
venv/bin/pip install -r requirements-dev.txt
```

```bash
cd frontend && npm ci && npm run build && cd ..
```

**Settings.** In a fresh checkout, this writes a `.env` with new random secrets. The apps read it, and git ignores it.

```bash
cat > .env <<EOF
APP_TIMEZONE=Europe/Zurich
DATABASE_URL=sqlite:///./demo.db
EMAIL_DRY_RUN=true
ADMIN_USERNAME=admin
ADMIN_PASSWORD=$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')
SESSION_SECRET_KEY=$(python3 -c 'import secrets; print(secrets.token_hex(32))')
ASSISTANT_API_KEY=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')
SUISSELINE_PATH_TOKEN=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')
CALENDAR_FEED_TOKEN=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')
EOF
```

**Seed fictional demo data.** It refuses to touch a database that already has data.

```bash
DATABASE_URL=sqlite:///./demo.db venv/bin/python scripts/seed_demo.py
```

**Start the three apps**, each in its own terminal. Start the website first and wait until it is running.

```bash
venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8002
```

```bash
venv/bin/uvicorn app.assistant_main:app --host 127.0.0.1 --port 8003
```

```bash
venv/bin/uvicorn app.calendar_main:app --host 127.0.0.1 --port 8004
```

**Try it** in a fourth terminal. The website is at http://127.0.0.1:8002 and the staff dashboard at http://127.0.0.1:8002/admin/login, with user `admin` and the `ADMIN_PASSWORD` from `.env`.

```bash
set -a; . ./.env; set +a
```

```bash
curl -s -X POST "http://127.0.0.1:8003/assistant/t/$SUISSELINE_PATH_TOKEN/info"
```

```bash
TOMORROW=$(venv/bin/python -c "import datetime, zoneinfo; print(datetime.datetime.now(zoneinfo.ZoneInfo('Europe/Zurich')).date() + datetime.timedelta(days=1))")
```

```bash
curl -s -X POST "http://127.0.0.1:8003/assistant/t/$SUISSELINE_PATH_TOKEN/availability" -H 'Content-Type: application/json' -d "{\"event\":\"tool.invocation\",\"tool\":{\"name\":\"check_availability\",\"arguments\":{\"date\":\"$TOMORROW\",\"service_id\":\"classic-cut\",\"from\":\"09:00\",\"to\":\"11:00\"}}}"
```

```bash
curl -s "http://127.0.0.1:8004/calendar/$CALENDAR_FEED_TOKEN.ics" | head -3
```

```bash
venv/bin/python scripts/preflight.py
```

With the seeded data, the availability call answers `09:30` and `10:30`. The preflight checks the settings, the database and the running assistant app without printing any secret. Its last line is `READY` once `cloudflared` is installed too.

**Run the tests:**

```bash
venv/bin/python -m pytest -q
```

To reach the assistant from Suisse Line, tunnel port 8003 only with `cloudflared tunnel --url http://localhost:8003`, and check it with `venv/bin/python scripts/preflight.py --public-url https://YOUR-TUNNEL-ADDRESS`. The demo script is in [docs/demo-runbook.md](docs/demo-runbook.md). The agent's prompt and tool definitions are in [docs/assistant-config.md](docs/assistant-config.md). Email, staff login, the calendar feed, Postgres and upgrades are covered in [docs/operations.md](docs/operations.md).

## Honest limits

- **Verified in Suisse Line's Chat test, not on a real phone call.** The tools, the envelope and the prompt were exercised through the platform's Chat test mode. A real phone call has not been verified in this repository, including what the caller-number fields hold.
- **The tunnel address changes.** A cloudflared quick tunnel gets a new address on every restart, and all seven Webhook URLs in the dashboard must then be updated.
- **Trial account limits.** The Suisse Line trial is documented as 7 days and 60 minutes, and every test call counts.
- **SQLite only.** The lock and all concurrency tests ran on SQLite. Postgres is untested.
- **Identity is by phone number.** Cancelling needs phone, date and time. That is knowledge, not proof of identity.
- **Manual waitlist contact.** Releasing a time is a local command, and a person contacts the customer. Nothing is sent automatically.
- **Not built:** rescheduling, and automatic waitlist matching when an appointment is cancelled.

## Upgrading an older database

Only needed if data from an older version of this app is brought across. A new database needs none of this. The tests run these statements against an old-shaped database.

**Upgrading an existing Postgres database:** appointment status is a native Postgres enum, and `create_all` does not add new values to it. Before deploying any version that can cancel appointments, run this once. New databases and SQLite don't need it.

```sql
ALTER TYPE queuestatus ADD VALUE IF NOT EXISTS 'cancelled';
```

**Upgrading any existing database:** appointments record a service and a duration, and `create_all` does not add columns to existing tables. Both apps refuse to start, with these same instructions, until the columns exist. Back up the database first, then run this once. New databases don't need it.

```sql
-- SQLite
ALTER TABLE queue_entries ADD COLUMN service_id VARCHAR;
ALTER TABLE queue_entries ADD COLUMN duration_minutes INTEGER;

-- Postgres
ALTER TABLE queue_entries ADD COLUMN IF NOT EXISTS service_id VARCHAR;
ALTER TABLE queue_entries ADD COLUMN IF NOT EXISTS duration_minutes INTEGER;
```

Existing rows keep empty values and count as one slot length.

**Upgrading a database that already has `call_requests`:** the table gained two columns after it was first added. Both apps refuse to start until they exist. Back up the database first, then run this once. A database without the table doesn't need it.

```sql
-- SQLite
ALTER TABLE call_requests ADD COLUMN suggested_times VARCHAR;
ALTER TABLE call_requests ADD COLUMN suggested_as_of DATETIME;

-- Postgres
ALTER TABLE call_requests ADD COLUMN IF NOT EXISTS suggested_times VARCHAR;
ALTER TABLE call_requests ADD COLUMN IF NOT EXISTS suggested_as_of TIMESTAMP;
```
