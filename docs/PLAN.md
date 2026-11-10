# Suisse Line Challenge A: Research and Plan (revision 2)

Revised 2026-11-09 · @Amir · Supersedes the first plan of the same date.

## 0. What changed since revision 1

| Change | Why |
|---|---|
| **New standalone repository** (`phone-assistant`). | Decision by Amir. The code began as a copy of an earlier booking app. That repository was never changed and nothing was pushed. |
| **Rebrand.** The site, staff UI and backend names now use the fictional Demo Barbershop, and the default time zone is Europe/Zurich. | Nothing inherited from the starting code may identify a real business. The look, copy, photos and contact details were all replaced. |
| Phase 1 and Phase 2 are **done and committed** (`d43fe88`, `212272a`). | Reviewed: 150 tests pass, development database untouched by the suite. |
| **New Phase 2b: interval booking** before the waitlist. | The booking rule counts bookings at an exact start time and stores no service or duration. Reproduced: an admin-added 15:15 slot accepted bookings at 15:00, 15:15 and 15:30 for one chair. |
| "Only services that fit one slot are bookable by phone" is **withdrawn**. | Signature Fade and Cut + Beard Combo, both featured, would have been refused. Phase 2b makes every service bookable if its whole interval fits. |
| Service is stored in **columns** (`service_id`, `duration_minutes`), not in the note. | The `Phone: Classic Cut` note was a workaround. A display-only note stays for staff. |
| Currency is **CHF** (`shared/services.json`). | The site's prices had no currency. Demo choice. |
| **English only.** | Decision by Amir. |
| Estimate grows from 28 h to about **35 to 37 h**. | Phase 2b adds 7 to 9 h. Judgement, not measured. |

## 1. Executive summary

**Recommendation (unchanged):** connect Suisse Line live tools to the small, API-key-protected `/assistant/*` API in this repository. This repository is the only booking system. Nothing about Suisse Line has been tested, because no account exists.

**The open gate (unchanged).** Live tools are documented as a feature. Nothing public says who configures them, or which methods, parameters, authentication or timeouts they support. If they cannot be configured in the trial, the fallback is booking after the call through the `call.completed` webhook.

**Where the build stands**
- Foundation repaired (Phase 1): lock against double booking, `APP_TIMEZONE`, past and closed slots, `cancelled` status, test isolation.
- Assistant API built (Phase 2): seven of the eight tools exist (`info`, `availability`, `bookings`, `find`, `cancel`, `waitlist`, `waitlist/accept`). `reschedule` is not built yet. Reviewed live on a scratch database.
- Built, committed `faf771f` (Phase 2b): interval booking and service durations.
- Fixed and committed: cancelled appointments cannot be changed (item 14, `194e0e4`), and a no-show whose slot was taken cannot be revived (item 18, `1067b09`). 229 tests pass.
- Built, committed `48d0b1b` (Phase 3): the Smart Cancellation Waitlist, with `join_waitlist`, `accept_waitlist_offer` and the release command. 349 tests pass.
- Rehearsed through a real tunnel, committed `6af4bf3` (Phase 4): only `/assistant/*` responded, and the probe and two-process race passed.
- Built, committed `212a98f`: the webhook fallback, for use only if live tools are unavailable. Its payload shape is an assumption.
- Not started: trial activation (5), calls (6, 7).

**Interview date: not recorded.** Do not activate the trial until section 10 is ticked. The documented 7 days should include the interview unless a recording is used.

## 2. Suisse Line feasibility

Nothing here is Verified. Sources were read on 2026-11-09. Statuses are unchanged from revision 1.

| Capability | Status | Consequence |
|---|---|---|
| Incoming calls, test number | Documented | Use the test number. Never forward a real business number. |
| Custom assistant (prompt, greeting, four voices) | Documented | Prompt size limit Unknown. Keep it short, put data behind tools. |
| English support | Documented, quality Unknown | Test R1. German only if R1 fails. |
| External API call during a call (live tools) | Documented as a feature, setup Unknown | **The main gate.** Confirm in the dashboard before configuring. |
| Structured tool arguments | Unknown | Contract stays flat. |
| Response changes what the assistant says | Unknown | Proved by R2. |
| Auth, timeouts, retries for tools | Unknown | Accept `X-API-Key` or Bearer. Booking stays safe under retries. |
| Caller number or call ID sent to tools | Unknown | The assistant asks for the number and reads it back. |
| Webhooks | Documented | Basis of the fallback. Header and retry policy Unknown. |
| Calendar integration | Documented, set up by Suisse Line | Not used. |
| Native appointment creation | Not available, per the Suisse Line blog | Booking goes through our API. |
| Trial | 7 days, 60 minutes, every Starter and Business function | Calls and browser tests count. Outbound calls and SMS are not available to rely on. |

Billing: per second from answer, ringing free, calls under 15 seconds free, forwarded talk time counts. Whether this applies to the trial is Unknown.

**Questions to email support@suisse-line.ch (copy info@suisse-it.ch).** Send before activation. They are unchanged: (1) can a trial user configure live tools alone; (2) URL, methods, custom headers, typed parameters; (3) caller number and call ID, and browser test calls; (4) timeout, retries, maximum response size; (5) how responses and errors reach the assistant; (6) webhook events, signature header, retry policy; (7) custom extracted webhook fields; (8) does the assistant ever check free slots; (9) does my own application count as "any other" booking system; (10) when the 7 days start and whether minutes can be extended; (11) calling the Swiss test number from abroad; (12) English as starting language and recommended voices.

**Dashboard checklist after activation (about 15 minutes, no calls):** trial end date and minutes; test number country; system prompt, greeting, language, voice and character limit; live tools locked or on request; the new-tool form fields; variables for caller number, call ID and date; a tool test button; webhook events and signature settings; the in-dashboard API documentation; calendar screen without connecting anything.

## 3. Repository state and audit

The audit covered the code as it stood when this repository began. The table shows what is still open.

| # | Defect found at audit | Status |
|---|---|---|
| 1 | Check then insert with no lock (30 of 30 rounds double-booked) | **Fixed.** One atomic write on `shop_status` serialises booking. Tested on SQLite only. |
| 2 | Time zone hard-coded | **Fixed.** `APP_TIMEZONE`, default Europe/Zurich. |
| 3 | Past times today bookable | **Fixed.** |
| 4 | Identical retry returns "fully booked" | **Fixed** on the assistant path (`created: false`). |
| 5 | Email sent inside the request | **Fixed** on the assistant path (background task). |
| 6 | Services of 45, 35 and 60 minutes against a 30-minute slot | **Fixed in Phase 2b, committed `faf771f`.** |
| 7 | Phone not normalised | **Fixed** on the assistant path (E.164, Swiss default). Public check-in unchanged. |
| 8 | Check-in renames an existing customer | **Fixed** on the assistant path. Public check-in still renames, by decision. |
| 9 | `booking_open=false` not reflected in availability | **Fixed.** |
| 10 | Public endpoints expose names by phone and staff emails | **Mitigated.** The tunnel serves only the assistant app. The staff app is never tunnelled. |
| 11 | Tests wrote into the development database | **Fixed.** |
| 12 | `create_all` only, no migrations | **Still true.** Drives the migration steps below. |
| 13 (new) | Exact-start counting allows overlapping appointments | **Fixed in Phase 2b, committed `faf771f`.** |
| 14 (new) | A cancelled entry can be changed to Done in the history page, retaking its slot | **Fixed, committed `194e0e4`.** The `done` and `no-show` routes return 409, and the history page shows a plain label. |
| 15 (new) | With `EMAIL_DRY_RUN=true`, `email.py` logs customer name and phone at INFO | **Open, accepted.** Do not point a log collector at dry-run output. |
| 16 (new) | Simultaneous first start of the website and assistant apps on a brand-new database crashed one in 12 tries (reported, not re-run) | **Open.** Phase 4 starts the website first. |
| 17 (new) | On the Vite dev server a direct load of `/admin/login` is blank, because the `/admin` proxy takes page routes. README is wrong about it | **Open, predates the work.** Reach it through the Staff Login link. |
| 18 (new) | A No Show could be switched to Done after someone else booked its slot, double-booking it | **Fixed, committed `1067b09`.** Revival runs the overlap and capacity check inside the lock and returns 409 if the time was taken. The page shows staff no message when refused. Tested on SQLite only. |
| 19 (new) | If an accepted time can no longer be booked because the shop closed it for today or an admin blocked it, the assistant is told someone else took it | **Open, known.** Safe: no booking is made and the entry stays active. Only the message is misleading. |

**Replacement decision.** The booking core was evaluated for replacement. Verdict: keep the `queue_entries` table, the staff dashboard, website, login, email, lock and retry safety. Replace only the booking rule (Phase 2b). A full appointments rewrite is justified only by per-barber schedules, database-enforced non-overlap at volume, or production Postgres with schema changes. All three are out of scope.

**Voice Cloner.** No role in a telephony assistant, because Suisse Line supplies the voices. Do not use it.

## 4. Integration decision

Choose option 1: narrow `/assistant/*` endpoints inside this repository. Option 4 (webhook after the call) is the fallback. The choice depends on dashboard checklist steps 4 and 5.

| Option | Verdict |
|---|---|
| 1. Live tool calls this app directly | **Chosen.** One hop, one codebase, real confirmation during the call. Setup Unknown. |
| 2. Separate integration service | Rejected. An extra process and pressure to duplicate logic. A second entry point in one repository gives the same isolation. |
| 3. Native calendar | Rejected. Team-assisted setup, capture only. |
| 4. Webhook after the call | **Fallback, built but untested against Suisse Line.** The assistant never claims a booking. `call.completed` feeds a signature-checked endpoint that calls the same booking function, or marks the request for staff. |

**Webhook fallback (built, committed `212a98f`).** `POST /assistant/webhook/call-completed`, on the public app only, is authenticated by HMAC-SHA256 over the raw body and fails closed without `SUISSELINE_WEBHOOK_SECRET`. It books through the same inner booking function, inside the lock and in one transaction with its record, or records a follow-up with a reason code. Staff list follow-ups with `python -m app.scripts.list_requests`. **Unverified:** the payload shape, the header name, the signature format, the retry behaviour and Postgres. `call_requests` rows are deleted 30 days after they close, as section 5 describes. In this mode a complete request is booked, but the caller hears only that the team will confirm and nothing is sent to them, so staff must phone back.

**Fallback trigger.** Live tools locked, or needing team enablement that will not arrive before the interview. If only a feature is missing (for example custom headers), adapt the contract first, for example a long random token in the URL path. Before suggesting any other platform I stop and report.

**Single source of truth.** This application is the only authority for appointments, availability, hours and services. No calendar is used.

### The booking rule (Phase 2b design, approved)
- An appointment occupies the interval from its start to start plus its duration. A booking is refused if, at any instant in its interval, the number of overlapping active bookings reaches `capacity_per_slot`.
- Rows with no stored duration (every old row and every website booking) count as one slot length.
- A normal start must end by closing time. A start on an admin-included slot may run outside hours but cannot overlap anything or cross midnight.
- A blocked slot occupies one slot length and no appointment may run across it.
- Starts stay on the existing grid. A 45-minute booking at 10:00 means the next start is 11:00.
- Intervals that start in, or run across, a skipped or repeated local time are rejected.
- The public `booked` field reports the peak overlap. Public response shapes stay the same.
- The same function serves public check-in, the assistant, cancel and later reschedule, and the overlap check runs inside the lock.
- Service name comes from `service_id`. The note is display-only (for example `Service: Cut + Beard Combo (60 min)`) and is never read by code.

### Idempotency
- Key: normalised phone, date and time among active bookings. Inside the lock, an existing active booking is returned with `created: false` before capacity is checked.
- A retry for the same time with a different service returns the first booking, so the assistant reads back the stored service.
- After a timeout on a booking call, the assistant calls `find_booking` before trying again.
- Cancel then rebook is allowed.

### Time zones
- `APP_TIMEZONE=Europe/Zurich` for the demo. The Mac's time zone is never used.
- Appointments stay as naive local date and time. Offer expiry uses UTC-aware timestamps.
- Tool inputs are `YYYY-MM-DD` and `HH:MM` shop-local. Responses include the weekday. `info` returns the current local date and time.

### Public access
- Run `uvicorn app.assistant_main:app --port 8003` and tunnel only that port with `cloudflared tunnel --url http://localhost:8003`. A quick tunnel address changes on restart.
- The staff app stays on port 8002 and is never tunnelled.
- The key lives in `.env` as `ASSISTANT_API_KEY`. Do not reuse `ADMIN_PASSWORD`.
- Logs hold tool name, result code and duration only.

### Migration (Phase 2b)
- Two nullable columns on `queue_entries`: `service_id` and `duration_minutes`. `create_all` does not add columns to existing tables.
- Both apps check the columns at startup and refuse to start with a clear message that includes the exact `ALTER TABLE` commands for SQLite and Postgres.
- This repository needs no migration: it has no `barbershop.db`, and a fresh database is created with both columns on first start.
- The `ALTER TABLE` commands matter only if older data is brought across. Back up that database outside the repository first, because the site links a real shop and the data may be real.
- The startup check also covers `call_requests` when that table exists. The table gained `suggested_times` and `suggested_as_of` after it was first added, so an older copy needs the `ALTER TABLE` commands printed at startup and listed in the README. A database without the table needs nothing. The Postgres commands are untested.
- On Postgres, appointment status is a native enum: `ALTER TYPE queuestatus ADD VALUE IF NOT EXISTS 'cancelled';` (README). Not tested on Postgres.

### Optional extra: read-only calendar feed

Not part of the Suisse Line path, and nothing in the phone flow depends on it. `app.calendar_main` runs on `127.0.0.1:8004` and mounts one route, `GET /calendar/<token>.ics`. It is never mounted on the website app (8002) or the public assistant app (8003).

- The token comes from `CALENDAR_FEED_TOKEN`, is compared in constant time and fails closed: unset, shorter than 24 characters or wrong all give 404.
- It covers slot-occupying appointments from yesterday through the next 60 days, capped at 500 events. Times are UTC, converted with `APP_TIMEZONE`.
- Privacy: the service name and times only. The query selects no customer column and no note.
- It only reads. It is an export, not a two-way sync, and it calls no external calendar service.
- The access log is off for this app and its own log redacts the path, so the token is never logged.
- A calendar service on the internet can subscribe only if this one port is tunnelled separately, which is the user's choice.

## 5. Architecture and data flows

One app, two entry points, one database and one booking function. The website and the phone assistant both go through it.

**Call to booking.** (1) `get_business_info` supplies the date and services. (2) The caller asks for a service and time, and the assistant calls `check_availability`, which offers only start times where the whole service fits. (3) It collects name and phone. (4) It reads back service, weekday, date, time, name and number and waits for a yes. (5) `create_booking`. The assistant confirms only if `ok` is true. The staff email goes out in the background.

**Smart Cancellation Waitlist.** Offers do not hold the slot. The first confirmed booking wins, enforced by the lock.
1. The caller asks for an unavailable time. The assistant says so and offers the waitlist.
2. It collects service, date, time range, name, phone and explicit permission. `join_waitlist` stores them with a consent timestamp in its own table and creates no customer record. Joining again with the same details returns the existing entry, and consent is required every time.
3. Manual demo step: `python -m app.scripts.release_slot <date> <time>` confirms the freed time with the same booking function that accept uses, and finds matching active entries, oldest first. **Matching is interval-aware:** the appointment must start within the entry's range and its whole service must fit. An entry has at most one open offer at a time, a time has at most 3 open offers, and running the command again creates no duplicates.
4. Each match gets an offer row, expiring after 30 minutes or at slot start, whichever is sooner. Slot start is converted with `APP_TIMEZONE` and stored in UTC. A skipped or repeated local time gets no offer.
5. Manual demo step: the output starts with `MANUAL CONTACT: nothing was sent. The slot is not reserved.` You contact the fictional customer yourself. The command uses `DATABASE_URL`, makes no network calls, and is the only place names and numbers are shown. They are never logged or sent through the tunnel.
6. The customer calls back. `accept_waitlist_offer` takes the lock, checks the offer is open and books in the same transaction, through the same function as every other booking. It reuses a customer and never renames one.
7. On success the offer becomes accepted, the entry becomes booked and other open offers for that time become lost. On failure the offer becomes lost, the entry stays active, and the assistant says someone else took the slot. After expiry, or once the slot start has passed, the answer is `offer_expired`.
8. A retry after a timeout returns the same booking while it is still active. Once that booking is cancelled or marked No Show, the answer is `no_open_offer`.

Automatic detection on cancellation is P1. Automatic notification is not planned.

**Error handling.** A timeout or 5xx on booking makes the assistant call `find_booking` once and report the true state. If that also fails it says no appointment is confirmed and offers a message. A taken slot leads to alternatives, then the waitlist. A wrong key returns 401 and is treated as a failed tool. A call changed before confirming makes no API call.

**Privacy.** Only name, phone, service and preferred time are collected. Waitlist entries need recorded consent. Expired waitlist rows are deleted after 30 days, checked on each waitlist write. `call_requests` hold names and numbers under the same 30-day rule, counted from when a row closes: a booked request closes when it arrives, and a follow-up when staff mark it handled. An open follow-up is never deleted by age, so staff must close it. The cleanup runs under the booking lock on each webhook and each run of `list_requests`, and never touches appointments. `find_booking` returns no names. Demos use fictional customers only. Suisse Line's own transcript retention is Unknown.

## 6. API contract for the assistant

All JSON, all behind an API key. Method, headers and parameter types may still change after dashboard checklist step 5.

**Conventions.** Base path `/assistant`, method POST with flat JSON fields. Auth: `X-API-Key` or `Authorization: Bearer`. A missing or wrong key returns **HTTP 401** (not `ok: false`). Business outcomes return HTTP 200 with `ok: true` or `ok: false, code, message`. Invalid input returns `code: invalid_input` and the message names the failing fields only, never their values. Empty strings for optional fields are treated as missing. Dates `YYYY-MM-DD`, times `HH:MM`, shop-local. The assistant must not treat any text as a confirmation unless `ok` is true.

| Tool and path | Request | Success | Failure codes | Status |
|---|---|---|---|---|
| `get_business_info` `/assistant/info` | none | today, weekday, now_local, timezone, open_days, open_time, close_time, services[id, name, price, currency CHF, duration_minutes, phone_bookable] | none | Built |
| `check_availability` `/assistant/availability` | date, service_id, from?, to? | date, weekday, slots[] (at most 5), more | date_past, shop_closed, unknown_service | Built. Uses real service duration (Phase 2b, committed `faf771f`). |
| `create_booking` `/assistant/bookings` | name, phone, date, time, service_id | created, booking{id, date, weekday, time, service} | slot_taken, invalid_time, date_past, booking_closed, unknown_service, invalid_phone | Built. Service stored in columns (Phase 2b, committed `faf771f`). |
| `find_booking` `/assistant/bookings/find` | phone, date? | bookings[] (upcoming, at most 3, no names) | invalid_phone | Built |
| `cancel_booking` `/assistant/bookings/cancel` | phone, date, time | cancelled, already_cancelled | not_found, invalid_phone | Built. Frees the full interval (Phase 2b, committed `faf771f`). |
| `join_waitlist` `/assistant/waitlist` | name, phone, service_id, date, from, to, consent | created, entry{id, date, from, to} | consent_required, date_past, invalid_phone, unknown_service | Built (Phase 3, committed `48d0b1b`). |
| `accept_waitlist_offer` `/assistant/waitlist/accept` | phone, date, time | booking{...} | no_open_offer, offer_expired, slot_taken, invalid_phone | Built (Phase 3, committed `48d0b1b`). |
| `reschedule_booking` `/assistant/bookings/reschedule` | phone, date, time, new_date, new_time | booking{...} | not_found, slot_taken, invalid_time, date_past | P1 |

**Removed:** `service_not_phone_bookable`. A service that cannot fit simply gets no slots. `phone_bookable` stays in `info` and is false only for a service longer than the whole opening day.

**Waitlist behaviour.** `join_waitlist` returns `created: false` with the existing entry for an identical request, and still requires consent. `from` and `to` bound the appointment's start time. `accept_waitlist_offer` returns `booking{...}`. Repeating it returns the same booking only while that booking is active. `slot_taken` means someone else took the time, and `offer_expired` covers both the 30 minutes and a slot start that has passed.

**Identity limit.** Cancelling needs phone, date and time. Without caller ID this is knowledge-based, not proof of identity. If a caller number reaches the tool, the backend will require a match.

**Not exposed to the tunnel:** every `/admin/*` route, `/queue/*`, `/api/login`, `/docs`, the OpenAPI file, and the "slot released" action (a local command). Verified live: all return 404 on the assistant app.

### Webhook fallback contract (assumptions unverified)

`POST /assistant/webhook/call-completed`. Used only if live tools are unavailable. Mounted on the public app only, and it does not use the API key.

| Item | Behaviour | Status |
| --- | --- | --- |
| Signature | HMAC-SHA256 of the raw body, compared in constant time, bare hex or `sha256=<hex>`. Header from `WEBHOOK_SIGNATURE_HEADER`, default `X-Signature` | Header name and format **unverified** |
| Secret | `SUISSELINE_WEBHOOK_SECRET`. If unset, every request gets 401 | Built |
| Limits | Over 64 KB returns 413, missing or wrong signature 401, invalid JSON 400 | Built |
| Payload | `callId` or `call_id`, optional `phoneNumber`, and `name`, `phone`, `service_id`, `date`, `time` in `extracted`, `data`, `data.extracted` or the top level | **Assumed.** Isolated in `parse_call_completed`. The fixture is marked ASSUMED |
| Other events | An `event` other than `call.completed` returns `status: ignored` and records nothing | Event names **unverified** |
| Response | HTTP 200 with `{ok, status}` for any validly signed JSON. Status is `booked`, `follow_up` or `ignored` | Built |
| Follow-up reasons | `incomplete`, `invalid_phone`, `unknown_service`, `invalid_date`, `invalid_time`, and any booking rejection such as `slot_taken` or `date_past` | Built |
| Duplicates | The same `call_id` returns the original outcome and creates nothing, including at the same moment | Built. Sender's retry policy **unverified** |
| Stored | Call id, status, reason, booking id, name, phone, service, date, time. Suggested times and when they were computed. Never the transcript or raw payload. Deleted 30 days after closing | Built |
| Calling number | Kept for staff to ring back, never used to book | Built |
| Suggested times | For `slot_taken`, `date_past`, or `incomplete` with a known service and date: up to 3 start times where the whole service fits, on the requested date or the next date with any within 7 days, never in the past. Computed with `slot_availability` under the same lock. Shown to staff in `list_requests` and the email, labelled as possibly taken since. Nothing is reserved | Built |

**Assistant instructions (kept short)**
1. Always call a tool for hours, prices, services and availability.
2. Confirm service, date with weekday, time, name and phone before `create_booking`.
3. Say a booking is confirmed only after `ok: true`.
4. If a booking call fails or times out, say you are checking and call `find_booking` once. Do not call `create_booking` again until it shows no booking.
5. On `slot_taken`, offer the alternatives from `check_availability`, then the waitlist.
6. Before `join_waitlist`, ask explicit permission to contact the caller. Say that a waitlist place is not an appointment.
7. Never promise a slot in advance. Say a slot is booked only after `accept_waitlist_offer` succeeds.
8. Ask again when unsure. Offer to take a message.
9. Collect only name, phone, service and preferred time.
10. If a booking returns `created: false`, read back the stored service and time, which may differ from what was just asked.

## 7. Roadmap

How the agent works in every phase: inspect, propose, you review, implement one small change, run tests, you review the diff, you verify by hand, commit. All code work happens on a branch, and the agent never commits without your say-so. Limits for every code phase: do not touch DataLayer, Voice Cloner, deployment settings, any `.env`, or the React admin pages without asking. Add no dependencies without asking. Evidence is the exact test command and output, plus the diff.

| Phase | Hours (plan) | Status |
|---|---|---|
| 0. Confirm unknowns: send the section 2 questions, agree the interview date | 1 | **Not reported done** |
| 1. Repair the foundation | 5 | **Done**, committed `d43fe88` |
| 2. Assistant API (five tools, auth, phone normalisation, shared services, entry point) | 6 | **Done**, committed `212272a`. Actual hours not recorded. |
| **2b. Interval booking and service durations** | **7 to 9** | **Built, committed `faf771f`** on branch `phone-assistant-build`. |
| 3. Smart Cancellation Waitlist | 6 | **Built, committed `48d0b1b`.** Actual hours not recorded. |
| 4. Public endpoint rehearsal (port 8003, tunnel, remote probes, latency, two-process race) | 1.5 | **Passed through a real tunnel, committed `6af4bf3`.** Median 173 to 226 ms per call, worst 1.2 s, measured from the Mac running the app. A tunnel took over a minute to start answering. |
| 4b. Webhook fallback (used only if live tools are unavailable) | Not planned | **Built, committed `212a98f`.** Untested against Suisse Line. |
| 4c. Activation-day package: `scripts/preflight.py` and `docs/demo-runbook.md` | Not planned | **Built and committed.** Local checks pass against a running app. The `--public-url` success path has not been run through a real tunnel, and nothing has been tested on a real call. |
| 4d. Read-only calendar feed (optional extra, not part of the Suisse Line path) | Not planned | **Built and committed.** 56 automated tests. No real Google or Apple subscription tested. |
| 5. Activate the trial and adapt | 2.5 | After section 10 |
| 6. Configure the assistant, first calls (about 12 min) | 3 | |
| 7. Failure drills and rehearsal (about 22 min) | 3 | |

Planned total about 35 to 37 h, of which phases 1, 2, 2b and 3 are done. Estimates are judgement, not measured.

**Phase 2b acceptance**
- Each of the reproduced problems is a test that fails before the change and passes after: the 15:15 overlap, the slot-length change, and the featured services being unbookable.
- A 60-minute booking at 10:00 blocks 10:00 and 10:30 and allows 11:00. Capacity 2 works with overlaps. Old rows count as one slot. Cancel frees the whole interval.
- 12 simultaneous overlapping bookings give exactly one success per round, repeated. Website and assistant cannot both succeed on overlapping times.
- DST, closing-time, included-slot, blocked-slot and midnight edge cases pass.
- The app refuses to start when the columns are missing.
- Four existing tests are rewritten because they assert removed behaviour (`phone_bookable` false, two `service_not_phone_bookable` cases, the note check). The other 146 are unedited. The development database hash is unchanged after the run.
- This plan is updated to match.

**Phase 3.** Two new tables and a service module. Tests: consent required, matching by date, range and duration, two concurrent acceptances give one booking, a direct booking beats an offer, expired offer refused, retention. Also add a deterministic test that `revive_no_show` takes the booking lock before `_load_day`, because the timing-based race test can miss a removed lock. Acceptance: all pass, and the release command prints a clearly labelled manual contact list.

**Phase 4.** Only `/assistant/*` responds through the tunnel. Remote probes: `/docs`, `/admin/shop-status` and `/queue/my-bookings` are not reachable. Wrong key gives 401. Latency recorded. The agent does not change application code here.

**Phases 5 to 7.** As in revision 1: the agent never asks for your Suisse Line password, you place every call and paste transcripts and tool logs, and the agent reports each scenario as passed, failed or not run.

## 8. Test plan and call budget

Booking logic is proved by automated tests. Real calls prove only speech, tool wiring and assistant behaviour. Budgeted call time is 34 minutes, leaving 26 of the 60.

**Automated (no trial minutes).** Temporary database and injected clock, never the Mac's zone. Current baseline: 660 passing (604 before the calendar feed, 150 at Phase 2, 205 with Phase 2b, 229 before Phase 3, 349 with Phase 3, 482 with the webhook fallback, 516 with its retention and suggested times). Cases: invalid or past dates, unknown service, off-grid times, past time today, closed day and blocked slot, conflicts, duplicate and retried bookings, 12-way concurrency across repeated rounds, phone format variants, book-cancel-rebook, auth missing/wrong/unset, admin paths 404 on the assistant app, near-midnight dates (23:30 and 00:30 Zurich), DST (2027-03-28 and 2027-10-31), failing email provider, **interval overlaps, capacity with overlaps, blocked slots inside an interval, closing time, missing-columns startup refusal for `queue_entries` and `call_requests`, changes to cancelled appointments, no-show revival, webhook signature and fail-closed secret, body limit, each follow-up reason, duplicate deliveries sequential and concurrent, no personal data in webhook logs, retention by closing date with an injected clock, suggested times that fit the service, fall through to the next open date, skip past times and never change a booking**, waitlist consent, matching, concurrent acceptance, expiry, retention, and no personal data in logs.

**Real calls (fictional customers only).**

| ID | Scenario | Pass condition | Min |
|---|---|---|---|
| R1 | Greeting and English quality | Understood, natural enough | 1.5 |
| R2 | First tool: tomorrow's availability | Times match the API | 2.5 |
| R3 | Complete booking | Confirmed only after success, visible in staff dashboard | 4 |
| R4 | Booking with corrected name and number | Corrected values stored | 4 |
| R5 | Requested slot taken | Alternatives offered, none invented | 2.5 |
| R6 | Caller changes mind before confirming | No booking written | 2 |
| R7 | Cancel a booking | Cancelled in the app | 2 |
| R8 | Assistant app stopped during the call | No false confirmation, offers a message | 1.5 |
| R9 | Join waitlist with consent | Entry stored, assistant says it is not an appointment | 3 |
| R10 | Manual release, call back to accept | Booked only after the API succeeds | 2.5 |
| R11 | Dress rehearsal | Completes without intervention | 4.5 |
| C1 | Optional extra: calendar feed (`tests/test_calendar_feed.py`, 56 automated tests) | Passes without a call. Uses no trial minutes. | 0 |
| | Reserve for prompt fixes | | 4 |
| | **Total** | | **34** |

**New:** include one booking of a long service (the Combo) in R3 or R4, so the interval rule is exercised by voice.

Record real duration after each call. Stop testing at 20 minutes remaining and protect them for the interview. The platform's tool timeout is Unknown, so no timeout call is budgeted. The automated retry case covers data safety, and R8 covers spoken behaviour.

## 9. Risks and fallbacks

The critical path has one external dependency: live tools usable in the trial. Everything before activation is in your control and uses no minutes.

| Risk | Likelihood | Impact | Mitigation or fallback |
|---|---|---|---|
| Live tools locked or need team enablement | Unknown | Critical | Ask support first. Fallback is the webhook path. Report before any platform change. |
| Tool cannot send a custom header | Unknown | High | Long random token in the URL path. |
| Tool timeout shorter than the round trip through the development machine | Unknown | High | Measure in Phase 4. Keep responses small and email off the request path. |
| Tunnel or Mac fails during the interview | Medium | High | Keep the Mac awake, test 30 minutes before, backup video. |
| Trial expires before the interview | Depends on date | High | Activate late, ask about extension, record a video. |
| Minutes run out | Low | High | 20-minute floor, automated tests first. |
| English recognition is weak | Unknown | Medium | R1 test, easy fictional names, always read details back. |
| Calling the Swiss number from abroad | Unknown | Medium | Ask support. A browser test call also counts. |
| Prompt ignores "never confirm early" | Medium | High | Backend success is the only source of a booking. Tests R3 and R8. |
| Assistant misresolves "next Tuesday" | Medium | Medium | Date by tool, read back weekday and date. |
| **Phase 2b migration breaks or loses older data, if any is brought across** | Low | High | Back up first, nullable columns, run `ALTER` only on a copy first, loud startup failure. |
| **Interval rule has an edge-case bug** | Medium | High | Reproduction tests that fail first, concurrency tests, DST and boundary tests. |
| Lock behaves differently on Postgres | Low for the demo | Low | Demo uses SQLite. Test on Postgres before any production use. |
| Adding `cancelled` affects a Postgres deployment | Unknown | Medium | README `ALTER TYPE`. Tell me what the deployment uses. |
| Scope grows beyond the interview | Medium | Medium | Stop at P0. P1 only as time remains. |
| Schedule: plan grew to 35 to 37 h | High | Medium | Interview date needed to confirm the order still fits. |

**Stop conditions.** If gate step 4 fails and the webhook fallback also cannot work in the trial, stop and explain the blocker. If an Unknown changes the contract, update section 6 before more code.

## 10. Priorities and readiness

| Priority | Tasks |
|---|---|
| **P0** | Support questions sent. **Done:** test isolation, `APP_TIMEZONE`, lock, past and closed slots, `cancelled`, API key, phone normalisation, shared services file, five tools, assistant-only entry point. **Built and committed:** interval booking (2b, `faf771f`), cancelled-entry block (item 14, `194e0e4`), no-show revival check (item 18, `1067b09`). **Built and committed (Phase 3, `48d0b1b`):** waitlist with consent and safe acceptance, two waitlist tools. **To do:** tunnel probes, assistant configuration, calls R1 to R11 |
| P1 | `reschedule_booking`. Automatic waitlist matching on cancel. Require a matching caller number if tools receive it. Backup demo video. Commit and verify the webhook fallback (becomes P0 if the gate fails) |
| P2 | Waitlist offers in the staff dashboard. Named tunnel. Flexible start times so a 45-minute booking does not waste 15 minutes. Second demo language. Show the refusal message on the history page |
| Out of scope | DataLayer. Voice Cloner. Multi-tenancy. Per-barber schedules. Provider abstractions, microservices, outboxes. CRM, analytics, billing. Two-way calendar sync (the read-only `.ics` export in section 4 is one-way and books nothing, so it is not a sync). Automated customer notifications. **Languages other than English.** |

**Readiness checklist before activating the trial**

Integration decision
- [ ] Support has answered, or you accept that dashboard steps 4 and 5 decide
- [ ] Option 1 agreed, option 4 ready as fallback
- [ ] Interview date known and inside 7 days of activation, or a recording will be shown

Code and data
- [ ] Phase 2b committed and reviewed
- [ ] `scripts/preflight.py --public-url` reports `READY` on the day
- [ ] `docs/demo-runbook.md` read through, and the backup video recorded
- [ ] Fresh demo database created, app starts
- [ ] Automated suite passes on the final branch
- [ ] Demo database seeded with fictional customers only

Credentials
- [ ] `ASSISTANT_API_KEY` generated and stored only in `.env`
- [ ] `APP_TIMEZONE=Europe/Zurich`
- [ ] Real `ADMIN_PASSWORD` and `SESSION_SECRET_KEY`
- [ ] `EMAIL_DRY_RUN=true` for testing, or a working Resend key

Public endpoint
- [ ] Phase 4 probes pass, only `/assistant/*` reachable
- [ ] Latency from outside recorded
- [ ] Mac set not to sleep during call sessions

Calls
- [ ] Assistant instructions and tool definitions drafted
- [ ] How you will call the test number decided
- [ ] Section 8 table reviewed: 34-minute target, 20-minute floor
- [ ] A log ready for the duration of every call
