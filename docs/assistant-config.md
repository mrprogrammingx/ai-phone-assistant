# Suisse Line assistant configuration

What will be entered into the Suisse Line dashboard after activation. No account exists yet, so how the dashboard names these fields, and whether it supports them, is unknown.

The fields, types and failure codes below come from section 6 of `docs/PLAN.md` and `app/routers/assistant.py`.

## System prompt

```text
You answer phone calls for a barbershop. Be brief and friendly. Speak English.

Facts
- Never state hours, prices, services, availability or the date from memory. Call get_business_info for the date, hours, prices and services. Call check_availability for times.
- Send dates as YYYY-MM-DD and times as HH:MM, in shop-local time.
- Collect only name, phone number, service and preferred time.
- Ask for the caller's phone number and read it back. It may not arrive automatically.

Booking
- Before create_booking, read back service, date with weekday, time, name and phone number, and wait for a yes.
- Say a booking is confirmed only after a tool returns ok: true. Never confirm early. If ok is false, nothing was booked.
- If created is false, read back the stored booking, which may differ from what was just asked.
- If a booking call fails or times out, say you are checking and call find_booking once. Never call create_booking again until find_booking shows no booking. If you still cannot tell, say no appointment is confirmed and offer to take a message.
- On slot_taken, offer the alternatives from check_availability, then the waitlist.

Cancelling
- Confirm the phone number, date and time, then call cancel_booking. Say it is cancelled only after ok: true.

Waitlist
- Before join_waitlist, ask explicit permission to contact the caller about a freed time. Set consent to true only if they say yes.
- Say a waitlist place is not an appointment and a slot is not reserved.
- Never promise a waitlist slot in advance. It is booked only after accept_waitlist_offer succeeds.

If you are unsure, ask again. If you cannot help, offer to take a message.
```

## First message

```text
Hello, thanks for calling the barbershop. How can I help you today?
```

## Tool definitions

Every tool uses method `POST` and a flat JSON body. Replace `https://YOUR-TUNNEL-ADDRESS` with the address of the running tunnel. It changes whenever a quick tunnel restarts.

A successful call returns `ok: true`. A business failure returns HTTP 200 with `ok: false`, `code` and `message`. Any tool can also return `code: invalid_input`. A missing or wrong key returns HTTP 401.

### get_business_info

```json
{
  "name": "get_business_info",
  "description": "Get today's date and weekday, the shop's hours, and its services with prices and durations. Call this before stating any of them.",
  "method": "POST",
  "path": "/assistant/info",
  "headers": { "Content-Type": "application/json", "X-API-Key": "YOUR-ASSISTANT-API-KEY" },
  "parameters": {
    "type": "object",
    "properties": {},
    "required": []
  }
}
```

Failure codes: none.

### check_availability

```json
{
  "name": "check_availability",
  "description": "List start times where the whole service fits on a date. Returns at most 5 times, and more is true if there are others.",
  "method": "POST",
  "path": "/assistant/availability",
  "headers": { "Content-Type": "application/json", "X-API-Key": "YOUR-ASSISTANT-API-KEY" },
  "parameters": {
    "type": "object",
    "properties": {
      "date": { "type": "string", "description": "Date as YYYY-MM-DD, shop-local." },
      "service_id": { "type": "string", "description": "A service id from get_business_info." },
      "from": { "type": "string", "description": "Earliest start time as HH:MM. Optional." },
      "to": { "type": "string", "description": "Latest start time as HH:MM. Optional." }
    },
    "required": ["date", "service_id"]
  }
}
```

Failure codes: `date_past`, `date_too_far`, `shop_closed`, `unknown_service`.

`date_too_far` means the date is more than 90 days ahead. The assistant should offer an earlier date.

### create_booking

```json
{
  "name": "create_booking",
  "description": "Book an appointment. Call only after the caller has confirmed every detail. The booking exists only if ok is true.",
  "method": "POST",
  "path": "/assistant/bookings",
  "headers": { "Content-Type": "application/json", "X-API-Key": "YOUR-ASSISTANT-API-KEY" },
  "parameters": {
    "type": "object",
    "properties": {
      "name": { "type": "string", "description": "The caller's name." },
      "phone": { "type": "string", "description": "The caller's phone number, as read back to them." },
      "date": { "type": "string", "description": "Date as YYYY-MM-DD, shop-local." },
      "time": { "type": "string", "description": "Start time as HH:MM, from check_availability." },
      "service_id": { "type": "string", "description": "A service id from get_business_info." }
    },
    "required": ["name", "phone", "date", "time", "service_id"]
  }
}
```

Failure codes: `slot_taken`, `invalid_time`, `date_past`, `date_too_far`, `booking_closed`, `unknown_service`, `invalid_phone`.

Bookings are taken up to 90 days ahead. A later date returns `date_too_far`. `booking_closed` is also returned if the shop's stored opening hours are invalid.

### find_booking

```json
{
  "name": "find_booking",
  "description": "List a caller's upcoming bookings, at most 3. Use it to check whether a booking exists after a failed or timed-out call.",
  "method": "POST",
  "path": "/assistant/bookings/find",
  "headers": { "Content-Type": "application/json", "X-API-Key": "YOUR-ASSISTANT-API-KEY" },
  "parameters": {
    "type": "object",
    "properties": {
      "phone": { "type": "string", "description": "The caller's phone number." },
      "date": { "type": "string", "description": "Only this date, as YYYY-MM-DD. Optional." }
    },
    "required": ["phone"]
  }
}
```

Failure codes: `invalid_phone`.

### cancel_booking

```json
{
  "name": "cancel_booking",
  "description": "Cancel an upcoming booking. Repeating a cancellation is safe.",
  "method": "POST",
  "path": "/assistant/bookings/cancel",
  "headers": { "Content-Type": "application/json", "X-API-Key": "YOUR-ASSISTANT-API-KEY" },
  "parameters": {
    "type": "object",
    "properties": {
      "phone": { "type": "string", "description": "The caller's phone number." },
      "date": { "type": "string", "description": "Date as YYYY-MM-DD, shop-local." },
      "time": { "type": "string", "description": "Start time as HH:MM." }
    },
    "required": ["phone", "date", "time"]
  }
}
```

Failure codes: `not_found`, `invalid_phone`.

### join_waitlist

```json
{
  "name": "join_waitlist",
  "description": "Add a caller to the waitlist. Only after they give explicit permission to be contacted. A waitlist place is not an appointment.",
  "method": "POST",
  "path": "/assistant/waitlist",
  "headers": { "Content-Type": "application/json", "X-API-Key": "YOUR-ASSISTANT-API-KEY" },
  "parameters": {
    "type": "object",
    "properties": {
      "name": { "type": "string", "description": "The caller's name." },
      "phone": { "type": "string", "description": "The caller's phone number, as read back to them." },
      "service_id": { "type": "string", "description": "A service id from get_business_info." },
      "date": { "type": "string", "description": "Date as YYYY-MM-DD, shop-local." },
      "from": { "type": "string", "description": "Earliest acceptable start time as HH:MM." },
      "to": { "type": "string", "description": "Latest acceptable start time as HH:MM." },
      "consent": { "type": "boolean", "description": "True only if the caller agreed to be contacted about a freed time." }
    },
    "required": ["name", "phone", "service_id", "date", "from", "to", "consent"]
  }
}
```

Failure codes: `consent_required`, `date_past`, `date_too_far`, `invalid_phone`, `unknown_service`.

`consent` is optional to the server, but leaving it out returns `consent_required`, so it is required here.

### accept_waitlist_offer

```json
{
  "name": "accept_waitlist_offer",
  "description": "Accept an offered waitlist time when the caller rings back. The slot is booked only if ok is true. Someone else may have taken it first.",
  "method": "POST",
  "path": "/assistant/waitlist/accept",
  "headers": { "Content-Type": "application/json", "X-API-Key": "YOUR-ASSISTANT-API-KEY" },
  "parameters": {
    "type": "object",
    "properties": {
      "phone": { "type": "string", "description": "The caller's phone number." },
      "date": { "type": "string", "description": "Offered date as YYYY-MM-DD, shop-local." },
      "time": { "type": "string", "description": "Offered start time as HH:MM." }
    },
    "required": ["phone", "date", "time"]
  }
}
```

Failure codes: `no_open_offer`, `offer_expired`, `slot_taken`, `invalid_phone`.

## Authentication

Use variant (a) if the dashboard can send a custom header. The key is `ASSISTANT_API_KEY`, kept only in the shell that starts the assistant app. Never put it in this file.

### (a) Header `X-API-Key`

Works today. Send this header on every tool, as shown above:

```text
X-API-Key: YOUR-ASSISTANT-API-KEY
```

The server also accepts `Authorization: Bearer YOUR-ASSISTANT-API-KEY`, if the dashboard offers only that.

### (b) Long random token in the URL path

Use this only if the dashboard cannot send any custom header. Each tool's URL would become:

```text
https://YOUR-TUNNEL-ADDRESS/assistant/YOUR-LONG-RANDOM-TOKEN/info
```

**This does not work today, and no change has been made.** It would need:

1. Mounting the assistant router a second time under a path that includes the token, with a dependency that compares the token in constant time. The current check, `require_assistant_key` in `app/auth.py`, reads headers only.
2. Changing the logging, because the token would then be in the path. `log_timing` in `app/assistant_main.py` and `_failure` in `app/routers/assistant.py` both write the path, and uvicorn's access log does too. Cloudflare would also see it.

## As entered

Fill this in after activation, using the field names the dashboard actually shows.

| Setting | Planned value | Dashboard field name | Value as entered | Checked |
| --- | --- | --- | --- | --- |
| System prompt | The prompt above | | | |
| First message | The message above | | | |
| Starting language | English | | | |
| Voice | | | | |
| Base URL | `https://YOUR-TUNNEL-ADDRESS` | | | |
| Authentication | Variant (a) | | | |
| Caller number variable | | | | |
| Call ID variable | | | | |
| Tool timeout | | | | |
| `get_business_info` | `/assistant/info` | | | |
| `check_availability` | `/assistant/availability` | | | |
| `create_booking` | `/assistant/bookings` | | | |
| `find_booking` | `/assistant/bookings/find` | | | |
| `cancel_booking` | `/assistant/bookings/cancel` | | | |
| `join_waitlist` | `/assistant/waitlist` | | | |
| `accept_waitlist_offer` | `/assistant/waitlist/accept` | | | |

## Fallback: webhook mode

Use this only if live tools are unavailable in the trial. The assistant collects details during the call and books nothing. After the call, Suisse Line sends a `call.completed` webhook, and the application books the time or records a follow-up for staff.

**Every field name in this section is unverified.** No account exists, so the webhook's shape, header name and retry behaviour are assumptions. They are isolated in `parse_call_completed` in `app/services/call_requests.py`.

### System prompt for webhook mode

```text
You take appointment requests for a barbershop by phone. Be brief and friendly. Speak English.

You cannot see the calendar. Never say a time is free, never state prices or hours, and never say an appointment is booked or confirmed.

Collect only name, phone number, service, date and preferred time. Read them back and wait for a yes. Ask for the phone number and read it back, because it may not arrive automatically.

Before ending the call, say: "I have your request and the team will confirm."

If the caller asks anything else, offer to take a message.
```

### Details to collect

| Detail | Assumed field name | Assumed format |
| --- | --- | --- |
| Name | `name` | Text |
| Phone number | `phone` | Text, read back to the caller |
| Service | `service_id` | One of the ids in `shared/services.json` |
| Date | `date` | `YYYY-MM-DD`, shop-local |
| Time | `time` | `HH:MM`, shop-local |

The application looks for these under `extracted`, `data`, `data.extracted` or at the top level. The calling number is assumed to arrive as `phoneNumber`. It is kept so staff can ring back, and it is never used to book.

### Webhook setup, to fill in after activation

| Setting | Planned value | Dashboard field name | Value as entered | Verified |
| --- | --- | --- | --- | --- |
| Webhook URL | `https://YOUR-TUNNEL-ADDRESS/assistant/webhook/call-completed` | | | No |
| Event | `call.completed` (name unverified) | | | No |
| Signing | HMAC-SHA256 over the raw body (documented as optional) | | | No |
| Signing secret | Same value as `SUISSELINE_WEBHOOK_SECRET`, kept out of files | | | No |
| Signature header | Set `WEBHOOK_SIGNATURE_HEADER` to the real name. Default `X-Signature` (unverified) | | | No |
| Signature format | Bare hex or `sha256=<hex>` (unverified) | | | No |
| Extracted fields | The five details above (support unverified) | | | No |
| Retry behaviour | Unknown. The endpoint is safe to retry | | | No |
