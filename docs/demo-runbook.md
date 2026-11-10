# Demo runbook: the 20-minute interview

Only the fictional data from `scripts/seed_demo.py` is used. "Tomorrow" always means tomorrow in shop time, Europe/Zurich.

**What has been checked.** Every expected answer below was produced by playing the same requests through the token routes and the Suisse Line envelope on a freshly seeded database. The tools have also been exercised in Suisse Line's Chat test mode. **A real phone call has not been verified**, so what the agent says in its own words may differ.

## Plan, minute by minute

| Minutes | Part | What to show |
| --- | --- | --- |
| 0 to 2 | Intro | The problem and the result |
| 2 to 5 | Architecture | The README diagram, then the three decisions |
| 5 to 11 | Live call | Book, wrong slot, waitlist with consent, cancel, accept the offer |
| 11 to 14 | Tests | Run the fast tests live, then the full count |
| 14 to 17 | Questions | Prepared answers below |
| 17 to 20 | Reserve | Only for overruns. Never start a new call here |

### 0 to 2: Intro

Say: "A small barbershop misses calls while the barber is cutting hair. I built a phone assistant on Suisse Line that answers, checks real availability, books, cancels and keeps a waitlist. The booking system is my own, it is the single source of truth, and the website and the phone both book through the same function. My own feature is the Smart Cancellation Waitlist."

### 2 to 5: Architecture

Show the diagram at the top of `README.md`. Make three points:

1. **One booking function, one lock.** The website and the phone cannot double-book, and a retried booking returns the same appointment.
2. **Only port 8003 is public**, behind a token in the URL path, because the dashboard's tool form has no header field. A wrong token gets a plain 404, and the token never reaches a log.
3. **Suisse Line sends the whole call with each tool call**, including the transcript and the caller's number. The app reads only `tool.arguments` and ignores the rest.

### 5 to 11: Live call

Have the staff dashboard open on tomorrow, signed in, at `http://127.0.0.1:8002/admin/login`. The seeded day has Anna at 09:00, Ben at 10:00, Clara's combo from 11:00 to 12:00 and David at 14:00.

Call the test number, or use the dashboard's browser test call. Speak slowly. Say the numbers digit by digit.

**Call 1, book (about 90 seconds)**

| Who | Say or expect |
| --- | --- |
| You | "Hello, what times do you have tomorrow for a Cut and Beard Combo?" |
| Agent | Expected times: 12:00, 12:30, 13:00, 14:30, 15:00. Earlier times do not fit 60 minutes around the existing bookings. |
| You | "Twelve o'clock, please. My name is Erin Testcase." |
| Agent | Asks for a phone number. |
| You | "Zero seven nine, zero zero zero, zero one, zero five." |
| Agent | Reads back Cut + Beard Combo, tomorrow with its weekday, 12:00, Erin Testcase and the number, and waits. |
| You | "Yes, that's right." |
| Agent | Says it is booked, only after `create_booking` answers `ok: true`. |

Show Erin at 12:00 on the dashboard.

**Call 2, wrong slot, then the waitlist with consent (about 90 seconds)**

| Who | Say or expect |
| --- | --- |
| You | "Can I have a Classic Cut tomorrow at ten? My name is Finn Madeup, zero seven nine, zero zero zero, zero one, zero six." |
| Agent | Reads back and waits. |
| You | "Yes, that's right." |
| Agent | 10:00 is taken (`slot_taken`). Offers 09:30 and 10:30, then the waitlist. |
| You | "I really need ten. Can you put me on the waitlist?" |
| Agent | Asks permission to contact you about a freed time, and says a waitlist place is not an appointment. |
| You | "Yes, you can contact me." |
| Agent | Calls `join_waitlist` with consent "yes", for 10:00 to 10:00. |

Say: "Consent arrives as text, and only a clear yes counts. A no, a 1 or an 'on' is refused."

**Call 3, cancel (about 40 seconds)**

| Who | Say or expect |
| --- | --- |
| You | "Hello, I'd like to cancel my appointment tomorrow at ten. My number is zero seven nine, zero zero zero, zero one, zero two." |
| Agent | Confirms the number, date and time, then says it is cancelled after `ok: true`. |

**Release the time (about 20 seconds, no call).** In terminal 4:

```bash
venv/bin/python -m app.scripts.release_slot <tomorrow> 10:00
```

The output starts with `MANUAL CONTACT: nothing was sent. The slot is not reserved.` and lists Finn Madeup. Say: "The offer lasts 30 minutes, or until the slot starts. It does not reserve the time. Anyone can still book it, and the first confirmed booking wins."

**Call 4, accept the offer (about 40 seconds)**

| Who | Say or expect |
| --- | --- |
| You | "Hello, this is Finn Madeup. You offered me ten o'clock tomorrow. I'd like to take it. My number is zero seven nine, zero zero zero, zero one, zero six." |
| Agent | Calls `accept_waitlist_offer`, and says it is booked only after success. |

Show Finn at 10:00 and Ben's cancelled appointment on the dashboard.

If call 3 does not work by voice, cancel Ben off camera with this, then release as above:

```bash
curl -s -X POST "http://127.0.0.1:8003/assistant/t/$SUISSELINE_PATH_TOKEN/bookings/cancel" -H 'Content-Type: application/json' -d '{"phone":"+41790000102","date":"<tomorrow>","time":"10:00"}'
```

### 11 to 14: Tests

Run the two fast files live, 535 tests in about 12 seconds:

```bash
venv/bin/python -m pytest -q tests/test_suisse_envelope.py tests/test_text_parameters.py
```

Then say:

- The full suite is 1720 tests and takes about 2.5 minutes, so it was run before the interview. Run it again the same morning and quote that result.
- The property tests generate names, phone spellings, dates and random booking sequences, and check that no moment ever has more appointments than the capacity.
- Mutation checks: each rule was broken on purpose, and the tests had to fail. For example, 20 faults were planted in the envelope adapter, and all 20 were caught.
- The Chat test found a real problem: the agent sent `service_id` "1" and skipped the read-back. The app refused the unknown service and booked nothing, and the prompt was tightened.

### 14 to 17: Questions

| Likely question | Short answer |
| --- | --- |
| Why your own booking system? | One source of truth, a real lock and retries that are safe. Suisse Line's native booking is not available, and a calendar sync would add a second truth. |
| What if two callers want the same time? | One atomic lock. Exactly one succeeds, tested with 12 at once over repeated rounds. |
| What if Suisse Line retries a booking? | The same phone, date and time returns the same booking with `created: false`. |
| Is the token in the URL safe? | It is the only option the form offers. It is long, compared in constant time and redacted from every log. Rotate it after the demo. |
| What about privacy? | Only name, phone, service and time are stored. Transcripts are never stored. Waitlist rows are deleted 30 days after closing. |
| What would you do next? | A real phone call test, rescheduling, and automatic waitlist matching when an appointment is cancelled. |

## Pre-flight checklist

Do this 30 minutes before. Never show a key, password, token or tunnel address on screen.

- [ ] `caffeinate -d` running in its own terminal, so the Mac does not sleep.
- [ ] A fresh demo database, seeded:

```bash
rm -f /tmp/phone-assistant-demo.db
```

```bash
DATABASE_URL=sqlite:////tmp/phone-assistant-demo.db venv/bin/python scripts/seed_demo.py
```

- [ ] In terminals 1, 2 and 4, the same settings. The `<...>` parts are shell errors on purpose, until you replace them with your own values:

```bash
export APP_TIMEZONE=Europe/Zurich DATABASE_URL=sqlite:////tmp/phone-assistant-demo.db EMAIL_DRY_RUN=true ASSISTANT_API_KEY=<your-key> SUISSELINE_PATH_TOKEN=<your-path-token> ADMIN_PASSWORD=<your-password> SESSION_SECRET_KEY=<your-secret>
```

- [ ] Terminal 1, the website first: `venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8002`. If `frontend/dist` is missing, build it first: `cd frontend && npm ci && npm run build && cd ..`.
- [ ] Terminal 2, the assistant: `venv/bin/uvicorn app.assistant_main:app --host 127.0.0.1 --port 8003`.
- [ ] Terminal 3, the tunnel is up: `cloudflared tunnel --url http://localhost:8003`. It can take over a minute to start answering.
- [ ] If the tunnel address changed, update all 7 Webhook URLs in the dashboard, per `docs/dashboard-entry.md`.
- [ ] Terminal 4, the preflight says `READY`:

```bash
venv/bin/python scripts/preflight.py --public-url https://YOUR-TUNNEL-ADDRESS
```

- [ ] The dashboard's system prompt matches `docs/assistant-config.md`, including the two newest Booking lines.
- [ ] Staff dashboard signed in, on tomorrow.
- [ ] At least 10 trial minutes are left for the demo. While rehearsing on earlier days, stop testing at 20 minutes remaining.
- [ ] Notifications off, other windows closed, terminal scrollback cleared.
- [ ] The backup video opens and plays.

## If the live call fails

Decide within 30 seconds, and say what you are doing.

| Problem | Do this |
| --- | --- |
| The call does not connect, or the voice is not understood | **Chat test.** Open the dashboard's Chat test and type the same sentences. It calls the same tools through the same tunnel, and was verified this way. |
| Tools fail: the agent says it cannot check, or the preflight fails | Check terminal 2 for requests. If the tunnel is down, restart it, update the 7 URLs and rerun the preflight. If that takes more than 2 minutes, go to the video. |
| Live tools themselves are unavailable on the platform | **Webhook mode.** Only if it was prepared beforehand: set `SUISSELINE_WEBHOOK_SECRET` in terminals 2 and 4, switch to the webhook prompt in `docs/assistant-config.md`, and run the preflight with `--fallback`. The agent books nothing during the call, and staff confirm afterwards with `venv/bin/python -m app.scripts.list_requests`. |
| Nothing works within 2 minutes | **Recorded video.** Play it, and narrate the same four calls. |
| The agent confirms before `ok: true` | Stop, say it is a prompt failure, and show that the dashboard has no booking. The backend is the only source of a booking. |

## What to say about the limits

- Verified in Chat test mode, not on a real phone call.
- The waitlist contact step is manual. Nothing is sent to the customer.
- An offer never reserves the slot, and the first confirmed booking wins.
- The caller is identified by phone number only. That is knowledge, not proof of identity.
- The lock and everything concurrent has been tested on SQLite only.
- A quick tunnel's address changes on restart, so the 7 URLs must then be updated.

## Recording the backup video

Record the four calls above, the `release_slot` output and the dashboard after each call, on a freshly seeded database. Before keeping it, check that no key, password, token, `.env` content or tunnel address is on screen or spoken, and that only fictional names and numbers appear. Store it outside the repository, for example in `~/Documents/suisse-line-demo/`, and never commit it.
