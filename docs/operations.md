# Operations

Details for running and maintaining the apps. The quick start is in the [README](../README.md).

## Website and staff dashboard (port 8002)

`app.main` serves the API and the built React site from `frontend/dist`. Any path not matched by an API or admin route falls through to the React app, so client-side routes such as `/booking` and `/gallery` work.

- Public site, booking included: http://127.0.0.1:8002/
- Simple no-JS check-in fallback: http://127.0.0.1:8002/checkin
- Staff dashboard: http://127.0.0.1:8002/admin/login
- Health check: http://127.0.0.1:8002/health

Re-run `npm run build` in `frontend/` after frontend changes, because `--reload` only watches Python files. Because it is the same origin, this mode needs no `CORS_ORIGINS` setting.

**Separate dev servers, for frontend work.** Run `npm run dev` in `frontend/` (http://localhost:5173) alongside the app on 8002. Vite proxies `/queue/*`, `/admin/*` and `/api/*` to the backend. A direct load of `/admin/login` on the Vite dev server is blank, because the `/admin` proxy takes the page route. Reach it through the Staff Login link instead.

## Staff login

The staff pages (Dashboard, History, Customers, Settings, all under `/admin/*`, plus their APIs) need a login at `/admin/login`. Set `ADMIN_USERNAME` and `ADMIN_PASSWORD`, and a random `SESSION_SECRET_KEY`. Login is disabled while `ADMIN_PASSWORD` is blank. Browsing times, booking and looking up "My Bookings" by phone stay public.

Auth is a signed session cookie (`app/auth.py`), checked by the frontend through `GET /api/me` and set through `POST /api/login` and `POST /api/logout`.

When deploying, set real values for these three in the server's `.env`, serve the app over HTTPS behind a reverse proxy, and set `SESSION_HTTPS_ONLY=true` so the session cookie is only sent over HTTPS.

## Email notifications

Staff are emailed about new bookings. Set `EMAIL_PROVIDER` to `resend` or `sendgrid`, supply the matching key (`RESEND_API_KEY` or `SENDGRID_API_KEY`) and `EMAIL_FROM`, and either configure notification addresses in Settings or set `ADMIN_EMAIL` as the fallback. With `EMAIL_DRY_RUN=true`, emails are logged instead of sent. The dry-run log includes the customer's name and phone, so do not point a log collector at it. Customers are never emailed.

## Public rehearsal

Rehearses the public endpoint on a brand-new temporary database, with no Suisse Line account. Only the assistant app is tunnelled, and the website app stays on localhost. It uses the header-key routes.

Generate a key once and use the same value in terminals 2 and 4. Do not put it in a file. The `<paste-the-generated-key>` lines below are shell syntax errors on purpose, so they cannot run until you replace them.

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

**Terminal 1: website app first.** Wait until it reports that it is running. Starting both apps at the same moment on a new database can crash one of them.

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

**Terminal 3: tunnel, assistant app only.** Copy the `https://….trycloudflare.com` address it prints. A quick tunnel's address changes every time it restarts.

```bash
cloudflared tunnel --url http://localhost:8003
```

**Terminal 4: checks.** The probe prints a latency table with the median of 5 runs. The race books one slot through both apps at once for 20 rounds and expects exactly one success per round.

```bash
export ASSISTANT_API_KEY=<paste-the-generated-key>
venv/bin/python scripts/public_rehearsal.py probe https://YOUR-ADDRESS.trycloudflare.com "$ASSISTANT_API_KEY"
venv/bin/python scripts/public_rehearsal.py race http://127.0.0.1:8002 http://127.0.0.1:8003 "$ASSISTANT_API_KEY"
```

Stop the tunnel with Ctrl+C as soon as you finish.

## Calendar feed (optional, read-only, port 8004)

Staff can see appointments in a calendar app through an `.ics` subscription. It books nothing and changes nothing, and the booking app stays the only source of truth. A cancelled appointment disappears on the next fetch.

Set `CALENDAR_FEED_TOKEN` to at least 24 random characters, then start `venv/bin/uvicorn app.calendar_main:app --host 127.0.0.1 --port 8004`. Every request gets 404 until the token is set. The feed address is `http://127.0.0.1:8004/calendar/YOUR-TOKEN.ics`. It covers appointments from yesterday through the next 60 days, up to 500 events.

**Apple Calendar, on the same Mac.** File, New Calendar Subscription, then paste the address.

**Google Calendar.** Other calendars, From URL. Google fetches the address from its own servers, so it cannot reach `127.0.0.1`. It needs a public address for this one port, through a separate tunnel of its own (`cloudflared tunnel --url http://localhost:8004`). That is your choice to make: the feed is local by default, and the tunnel for the assistant on port 8003 must not be reused for it. Google refreshes subscribed feeds only every few hours.

**Privacy rule.** The feed carries only the service name and the times, never a customer name, phone number, note or email. The token is the only protection, and anyone holding the address can read the schedule. A tunnel provider and a calendar service both see the full address. To revoke access, change the token, restart the feed and subscribe again.

The feed is never mounted on the website app (8002) or the assistant app (8003). Its entry point turns off Uvicorn's access log, because that log prints the request path and the path holds the token. Keep this entry point if you start it another way, or pass `--no-access-log`.

## Waitlist and follow-up commands

Both are local only, use `DATABASE_URL` and open no network connection.

```bash
venv/bin/python -m app.scripts.release_slot YYYY-MM-DD HH:MM
```

Offers a freed time to matching waitlist entries and prints them under `MANUAL CONTACT: nothing was sent. The slot is not reserved.`

```bash
venv/bin/python -m app.scripts.list_requests
```

Lists follow-ups recorded by the webhook fallback.

## Switching to Postgres

Set `DATABASE_URL`, for example `postgresql+psycopg2://user:password@localhost:5432/barbershop`, and add `psycopg2-binary` to `requirements.txt`. The lock and the concurrency tests have only run on SQLite.

**Upgrading an older database:** the `ALTER` statements are in the [README](../README.md#upgrading-an-older-database), where the tests check that they run.
