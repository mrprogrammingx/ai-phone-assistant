# Demo Barbershop: phone assistant and booking backend

A booking backend and phone assistant API for a fictional barbershop. Customers book an
appointment on the website or by phone, and staff manage appointments and opening hours from a
lightweight dashboard. All shop details in this repository are fictional.

The `frontend/` folder holds the website and staff UI (React + Tailwind + Framer Motion), wired
to this same API. See [frontend/README.md](frontend/README.md).

## Stack

- **FastAPI** — backend/API
- **SQLAlchemy** — ORM, SQLite for dev, Postgres-ready (just change `DATABASE_URL`)
- **Jinja2** — minimal server-rendered check-in page and staff dashboard
- **Resend / SendGrid** — admin email notifications (pick one via `EMAIL_PROVIDER`)
- **Pydantic** — request/response schemas
- **React + Vite** (in `frontend/`) — the public-facing marketing/booking site, calling this
  API over HTTP (see `CORS_ORIGINS` below)

## Project structure

```
app/
  main.py            FastAPI app entrypoint
  models.py           SQLAlchemy models: Customer, QueueEntry, ShopStatus
  schemas.py           Pydantic schemas
  database.py          DB session/engine setup
  routers/
    queue.py           check-in, position, call-next, done, no-show
    admin.py            open/close, reset queue
  services/
    email.py            notify admin via Resend/SendGrid when a customer checks in
  templates/            Jinja2 check-in page + staff dashboard
tests/
```

## Setup

Create and activate a virtual environment, then install dependencies:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Copy the env file and fill in your values (SQLite/dry-run email works out of the box with no changes):

```bash
cp .env.example .env
```

## Run

```bash
uvicorn app.main:app --reload --port 8002
```

- Public site (React, booking included): http://localhost:8002/
- Simple no-JS check-in fallback: http://localhost:8002/checkin
- Staff admin panel (React): http://localhost:8002/admin/login
- Health check: http://localhost:8002/health
- API docs: http://localhost:8002/docs

This single server serves both the API and the built frontend — see
[Frontend](#frontend-react-marketing-site) below for the one build step required first.

## Admin login

The Staff admin panel (Dashboard, History, Customers, Settings — all React pages under
`/admin/*`, plus their APIs) requires an admin login at `/admin/login`. Set
`ADMIN_USERNAME` and `ADMIN_PASSWORD` in `.env`, and a random `SESSION_SECRET_KEY` (e.g.
`python -c "import secrets; print(secrets.token_hex(32))"`). Login is disabled while
`ADMIN_PASSWORD` is blank. The public site (browsing times, booking, and looking up "My
Bookings" by phone) stays fully public — no login required.

Auth is a signed session cookie (`app/auth.py`), checked by the frontend via `GET /api/me`
and set via `POST /api/login` / `POST /api/logout`. `AdminLayout`
([frontend/src/components/admin/AdminLayout.jsx](frontend/src/components/admin/AdminLayout.jsx))
redirects to `/admin/login` client-side whenever `/api/me` reports a logged-out session.

When deploying (e.g. on a VPS), set real values for `ADMIN_USERNAME`/`ADMIN_PASSWORD`/
`SESSION_SECRET_KEY` in the server's `.env` and serve the app over HTTPS (e.g. behind
nginx/Caddy with a TLS cert). Set `SESSION_HTTPS_ONLY=true` in that `.env` so the session
cookie is only ever sent over HTTPS. Then log in at `https://yourdomain.com/admin/login`
with your admin credentials, same as locally.

## Email notifications

Set `ADMIN_EMAIL` to the address that should be notified when a customer checks in. Set
`EMAIL_PROVIDER` to `resend` or `sendgrid` and supply the matching API key
(`RESEND_API_KEY` / `SENDGRID_API_KEY`) plus `EMAIL_FROM`. With `EMAIL_DRY_RUN=true`
(the default), emails are logged instead of sent — handy for local development. Customers
are not emailed; only the admin address is notified.

## Frontend (React marketing site)

`frontend/` is a Vite + React app — see [frontend/README.md](frontend/README.md) for details.
It calls this API for live availability and booking.

**Single-server mode (recommended):** build the frontend once, then this FastAPI app serves
the built files directly at `/` (any path not matched by an API/admin route falls through to
the React app, so client-side routes like `/booking` and `/gallery` work too):

```bash
cd frontend && npm install && npm run build && cd ..
uvicorn app.main:app --reload --port 8002
```

Re-run `npm run build` after frontend changes; `--reload` only watches Python files. Because
it's the same origin, no `CORS_ORIGINS` setup is needed for this mode.

**Separate dev servers (for active frontend development):** run `npm run dev` in `frontend/`
(defaults to http://localhost:5173) alongside `uvicorn` on 8002. Vite proxies `/queue/*`,
`/admin/*`, and `/api/*` calls to the backend, so the admin panel works the same way as in
single-server mode — no `CORS_ORIGINS` change needed for this either.

## Core features

1. **Appointment slots** — admin sets open/close time, slot length, and capacity per slot;
   customers see live availability and pick an open time at check-in.
2. **Customer check-in** — name and phone (both required), joins today's queue at the chosen slot.
3. **Queue position tracking** — auto-incrementing position, ordered by appointment time.
4. **Staff dashboard** — view current queue, call next, mark done/no-show, edit slot settings.
5. **Admin email notification** — sent to `ADMIN_EMAIL` whenever a customer checks in.
6. **Admin controls** — toggle shop open/closed, set shop hours text, reset today's queue.
7. **Queue history** — browse past days' queue entries by date.
8. **Customers page** — every customer who's ever checked in, with visit counts.
9. **Admin login** — Staff Dashboard, History, Customers, and Settings require login;
   check-in and booking lookup stay public.

## Tests

```bash
pytest
```

## Public rehearsal

Rehearses the public endpoint on a brand-new temporary database. It needs no Suisse Line
account. Only the assistant app is tunnelled. The website app stays on localhost.

Generate a key once, before starting, and use the same value in terminals 2 and 4. Do not put
it in a file. The `<paste-the-generated-key>` lines below are shell syntax errors on purpose, so
they cannot run until you replace them.

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

**Terminal 1: website app first.** Wait until it reports that it is running. Starting both apps
at the same moment on a new database can crash one of them.

```bash
rm -f /tmp/phone-assistant-rehearsal.db
export APP_TIMEZONE=Europe/Zurich DATABASE_URL=sqlite:////tmp/phone-assistant-rehearsal.db EMAIL_DRY_RUN=true
venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8002
```

**Terminal 2: assistant app second, same database.**

```bash
export APP_TIMEZONE=Europe/Zurich DATABASE_URL=sqlite:////tmp/phone-assistant-rehearsal.db EMAIL_DRY_RUN=true
export ASSISTANT_API_KEY=<paste-the-generated-key>
venv/bin/uvicorn app.assistant_main:app --host 127.0.0.1 --port 8003
```

**Terminal 3: tunnel, assistant app only.** Copy the `https://….trycloudflare.com` address it
prints. A quick tunnel's address changes every time it restarts.

```bash
cloudflared tunnel --url http://localhost:8003
```

**Terminal 4: checks.** The probe prints a latency table with the median of 5 runs. The race
books one slot through both apps at once for 20 rounds and expects exactly one success per round.

```bash
export ASSISTANT_API_KEY=<paste-the-generated-key>
venv/bin/python scripts/public_rehearsal.py probe https://YOUR-ADDRESS.trycloudflare.com "$ASSISTANT_API_KEY"
venv/bin/python scripts/public_rehearsal.py race http://127.0.0.1:8002 http://127.0.0.1:8003 "$ASSISTANT_API_KEY"
```

Stop the tunnel with Ctrl+C as soon as you finish.

## Switching to Postgres

Set `DATABASE_URL` in `.env`, e.g.:

```
DATABASE_URL=postgresql+psycopg2://user:password@localhost:5432/barbershop
```

You'll also need to add `psycopg2-binary` to `requirements.txt`.

**Upgrading an existing Postgres database:** appointment status is a native Postgres enum, and
`create_all` does not add new values to it. Before deploying any version that can cancel
appointments, run this once. New databases and SQLite don't need it.

```sql
ALTER TYPE queuestatus ADD VALUE IF NOT EXISTS 'cancelled';
```

**Upgrading any existing database:** appointments now record a service and a duration, and
`create_all` does not add columns to existing tables. Both apps refuse to start, with these
same instructions, until the columns exist. Back up the database first, then run this once.
New databases don't need it.

```sql
-- SQLite
ALTER TABLE queue_entries ADD COLUMN service_id VARCHAR;
ALTER TABLE queue_entries ADD COLUMN duration_minutes INTEGER;

-- Postgres
ALTER TABLE queue_entries ADD COLUMN IF NOT EXISTS service_id VARCHAR;
ALTER TABLE queue_entries ADD COLUMN IF NOT EXISTS duration_minutes INTEGER;
```

Existing rows keep empty values and count as one slot length.

**Upgrading a database that already has `call_requests`:** the table gained two columns after it
was first added. Both apps refuse to start until they exist. Back up the database first, then
run this once. A database without the table doesn't need it.

```sql
-- SQLite
ALTER TABLE call_requests ADD COLUMN suggested_times VARCHAR;
ALTER TABLE call_requests ADD COLUMN suggested_as_of DATETIME;

-- Postgres
ALTER TABLE call_requests ADD COLUMN IF NOT EXISTS suggested_times VARCHAR;
ALTER TABLE call_requests ADD COLUMN IF NOT EXISTS suggested_as_of TIMESTAMP;
```
