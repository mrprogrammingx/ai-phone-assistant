# Demo runbook

The live demo and the backup recording, using only the fictional data from `scripts/seed_demo.py`.

**Nothing here has been run on a real call.** No Suisse Line account exists, so how live tools behave, and what the assistant actually says, are unverified. The expected times below come from running the same steps against the API on a freshly seeded database. In the demo, `<tomorrow>` means tomorrow's date as `YYYY-MM-DD`.

## Starting everything on the day

Open four terminals in the repository. Paste this into each one first, with your own generated values. The `<...>` parts are shell errors on purpose, so it cannot run until you replace them. Never put these values on screen.

```bash
export APP_TIMEZONE=Europe/Zurich DATABASE_URL=sqlite:////tmp/phone-assistant-demo.db EMAIL_DRY_RUN=true ASSISTANT_API_KEY=<paste-the-generated-key> ADMIN_PASSWORD=<paste-a-generated-password> SESSION_SECRET_KEY=<paste-a-generated-secret>
```

Secrets must be at least 24 characters, because the preflight script fails shorter ones. Generate each value once with this command:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

**Before starting the apps,** in terminal 1, remove any old demo database:

```bash
rm -f /tmp/phone-assistant-demo.db
```

Then seed a new one:

```bash
venv/bin/python scripts/seed_demo.py
```

If the website has not been built in this repository yet, build it once, or the staff dashboard will not be there:

```bash
cd frontend && npm ci && npm run build && cd ..
```

**Terminal 1, website app first.** Wait until it says it is running. It stays on localhost and is never tunnelled.

```bash
venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8002
```

**Terminal 2, assistant app second,** on the same database.

```bash
venv/bin/uvicorn app.assistant_main:app --host 127.0.0.1 --port 8003
```

**Terminal 3, the tunnel, assistant app only.** It can take over a minute to start answering. Its address changes every time it restarts.

```bash
cloudflared tunnel --url http://localhost:8003
```

**Terminal 4, check before any call.** Put the tunnel's address in the dashboard, then run this. Start the call session only if the last line is `READY`.

```bash
venv/bin/python scripts/preflight.py --public-url https://YOUR-TUNNEL-ADDRESS
```

In webhook mode, add `--fallback`, and set `SUISSELINE_WEBHOOK_SECRET` in terminals 2 and 4 first.

```bash
venv/bin/python scripts/preflight.py --fallback --public-url https://YOUR-TUNNEL-ADDRESS
```

## The 5-minute demo

Open the staff dashboard first, at `http://127.0.0.1:8002/admin/login`, and sign in. Keep tomorrow's date selected. Log each call in `docs/call-log.md`.

The seeded day tomorrow has Anna at 09:00, Ben at 10:00, Clara's combo from 11:00 to 12:00, and David at 14:00.

### (a) Book the Cut + Beard Combo, about 90 seconds

| Who | What happens |
| --- | --- |
| Caller | "Hello, what times do you have tomorrow for a Cut and Beard Combo?" |
| Assistant | Calls `get_business_info` and `check_availability`. Expected first times: 12:00, 12:30, 13:00, 14:30, 15:00. Earlier times do not fit 60 minutes around the existing bookings. |
| Caller | "Twelve o'clock, please. My name is Erin Testcase." |
| Assistant | Asks for a phone number. |
| Caller | "Zero seven nine, zero zero zero, zero one, zero five." |
| Assistant | Reads back the Cut + Beard Combo, tomorrow with its weekday, 12:00, Erin Testcase and the number, and waits for a yes. |
| Caller | "Yes, that's right." |
| Assistant | Calls `create_booking`, and says it is booked only after `ok: true`. |

Show the 12:00 booking on the staff dashboard.

### (b) The full 10:00, then the waitlist, about 60 seconds

| Who | What happens |
| --- | --- |
| Caller | "Can I have a Classic Cut tomorrow at ten?" |
| Assistant | Should ask for name and number, and read them back. |
| Caller | "Finn Madeup. Zero seven nine, zero zero zero, zero one, zero six. Yes, that's right." |
| Assistant | Calls `create_booking`, which returns `slot_taken`. Says 10:00 is taken and offers alternatives. Expected between 09:00 and 11:00: 09:30 and 10:30. Then offers the waitlist. |
| Caller | "I really need ten. Can you put me on the waitlist?" |
| Assistant | Asks permission to contact the caller, and says a waitlist place is not an appointment and the slot is not reserved. |
| Caller | "Yes, you can contact me." |
| Assistant | Calls `join_waitlist` with consent true, for 10:00 to 10:00. |

### (c) Free the 10:00, about 30 seconds, no call

Cancel Ben's booking through the tool, with the key from your terminal's environment:

```bash
curl -s -X POST http://127.0.0.1:8003/assistant/bookings/cancel -H "X-API-Key: $ASSISTANT_API_KEY" -H "Content-Type: application/json" -d '{"phone":"+41790000102","date":"<tomorrow>","time":"10:00"}'
```

Then release the time:

```bash
venv/bin/python -m app.scripts.release_slot <tomorrow> 10:00
```

Show the output. It starts with `MANUAL CONTACT: nothing was sent. The slot is not reserved.` and lists Finn Madeup. Say out loud that you are now contacting Finn by hand. The offer lasts 30 minutes, or until the slot starts if that is sooner.

### (d) Accept the offer, about 40 seconds

| Who | What happens |
| --- | --- |
| Caller | "Hello, this is Finn Madeup. You offered me ten o'clock tomorrow. I'd like to take it. My number is zero seven nine, zero zero zero, zero one, zero six." |
| Assistant | Calls `accept_waitlist_offer`. Says it is booked only after success. |

Show Finn's 10:00 on the staff dashboard.

### (e) What staff see, about 30 seconds

Show the dashboard's list for tomorrow. In webhook mode only, also show follow-ups:

```bash
venv/bin/python -m app.scripts.list_requests
```

## What to say about the limits

- The waitlist contact step is manual. Nothing is sent to the customer automatically, and I contact them myself.
- An offer never reserves the slot. Anyone can still book it, and the first confirmed booking wins.
- The caller is identified by phone number only. That is knowledge, not proof of identity.
- In webhook mode the assistant cannot see the calendar and books nothing during the call. A complete request may be booked afterwards, but the caller only hears that the team will confirm, so staff must phone back.
- A website booking still takes one slot length, because the website has no service picker.
- The booking lock and everything concurrent has been tested on SQLite only, not Postgres.

## When it goes wrong

Stop testing at **20 minutes remaining** and keep those minutes for the interview.

| Problem | The one action to take |
| --- | --- |
| Tunnel not answering | Wait up to 2 minutes, then run the preflight with `--public-url`. If it still fails, use the backup video. |
| 401 from the tools | Re-enter the key in the dashboard from the value set in terminal 2, then run the preflight. |
| A tool times out | Check the staff dashboard for the booking before any retry, and let the assistant call `find_booking` once. If it repeats, use the backup video. |
| The assistant confirms early | End the call, log it as failed, and make one prompt change from the 4-minute reserve. |
| English not understood | Repeat once, slowly. If it still fails, end the call and use the backup video. |
| Minutes running low | At 20 minutes remaining, stop testing and keep the rest for the interview. |

## Recording checklist for the backup video

Before recording:

- [ ] A freshly seeded demo database, with fictional customers only
- [ ] The preflight says `READY`
- [ ] Terminal history and scrollback cleared
- [ ] Notifications off, and other windows closed
- [ ] The Suisse Line dashboard's API, tool and webhook settings out of frame

Capture, in this order:

- [ ] (a) The call booking the Cut + Beard Combo, then the dashboard showing it
- [ ] (b) The call asking for 10:00, hearing it is taken, and joining the waitlist with consent
- [ ] (c) The `release_slot` output with its banner. Run the cancel command off camera
- [ ] (d) The call accepting the offer, then the dashboard showing Finn at 10:00
- [ ] (e) The staff dashboard, and `list_requests` if using webhook mode

Check before keeping it:

- [ ] No key, password, secret or `.env` content on screen
- [ ] No tunnel address on screen, and none spoken
- [ ] No real name or phone number anywhere

Store it outside the repository, for example in `~/Documents/suisse-line-demo/`, and never commit it.
