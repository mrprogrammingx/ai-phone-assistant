# Suisse Line dashboard entry

Copy each field into the Add tool form. Replace `YOUR-TUNNEL-ADDRESS` with the running tunnel's address and `YOUR-TOKEN` with your own token. Put the real values only in the dashboard, never in this file.

The same for all seven tools:

| Field | Enter |
| --- | --- |
| HTTP Method | `POST` |
| Preload before agent starts speaking | Leave at the default. Behaviour unverified |
| Don't wait for result | Leave off, because the assistant must speak each result. Judgement, not tested |
| Customize webhook payload fields | Leave at the default. The app reads the default envelope |

The form offers only String or Number and has no required flag, so every parameter below is **String**, and each description says whether it is required. Never choose Number: a phone or date sent as a number is refused.

Suisse Line sends each call as an envelope, with the parameters at `tool.arguments` next to the transcript, the agent prompt and the call's numbers. The app reads only `tool.arguments`. The observed shape is in section 4 of `docs/PLAN.md`.

The `consent` description must tell the model to send exactly "yes" or "no". Copy it as written below.

## `get_business_info`

| Field | Enter |
| --- | --- |
| Tool Name | `get_business_info` |
| When to Use (AI Instruction) | Get today's date and weekday, the shop's hours, and its services with prices and durations. Call this before stating any of them. |
| Webhook URL | `https://YOUR-TUNNEL-ADDRESS/assistant/t/YOUR-TOKEN/info` |

No parameters.

## `check_availability`

| Field | Enter |
| --- | --- |
| Tool Name | `check_availability` |
| When to Use (AI Instruction) | List start times where the whole service fits on a date. Returns at most 5 times, and more is true if there are others. |
| Webhook URL | `https://YOUR-TUNNEL-ADDRESS/assistant/t/YOUR-TOKEN/availability` |

| Parameter | Type | Description |
| --- | --- | --- |
| `date` | String | Required. Date as YYYY-MM-DD, shop-local. |
| `service_id` | String | Required. The exact id text from get_business_info, for example classic-cut. Never a number. |
| `from` | String | Optional. Earliest start time as HH:MM. Leave empty if the caller named none. |
| `to` | String | Optional. Latest start time as HH:MM. Leave empty if the caller named none. |

## `create_booking`

| Field | Enter |
| --- | --- |
| Tool Name | `create_booking` |
| When to Use (AI Instruction) | Book an appointment. Call only after the caller has confirmed every detail. The booking exists only if ok is true. |
| Webhook URL | `https://YOUR-TUNNEL-ADDRESS/assistant/t/YOUR-TOKEN/bookings` |

| Parameter | Type | Description |
| --- | --- | --- |
| `name` | String | Required. The caller's name. |
| `phone` | String | Required. The caller's phone number, as read back to them. Keep any leading + or 0. |
| `date` | String | Required. Date as YYYY-MM-DD, shop-local. |
| `time` | String | Required. Start time as HH:MM, from check_availability. |
| `service_id` | String | Required. The exact id text from get_business_info, for example classic-cut. Never a number. |

## `find_booking`

| Field | Enter |
| --- | --- |
| Tool Name | `find_booking` |
| When to Use (AI Instruction) | List a caller's upcoming bookings, at most 3. Use it to check whether a booking exists after a failed or timed-out call. |
| Webhook URL | `https://YOUR-TUNNEL-ADDRESS/assistant/t/YOUR-TOKEN/bookings/find` |

| Parameter | Type | Description |
| --- | --- | --- |
| `phone` | String | Required. The caller's phone number. Keep any leading + or 0. |
| `date` | String | Optional. Only this date, as YYYY-MM-DD. Leave empty for all upcoming bookings. |

## `cancel_booking`

| Field | Enter |
| --- | --- |
| Tool Name | `cancel_booking` |
| When to Use (AI Instruction) | Cancel an upcoming booking. Repeating a cancellation is safe. |
| Webhook URL | `https://YOUR-TUNNEL-ADDRESS/assistant/t/YOUR-TOKEN/bookings/cancel` |

| Parameter | Type | Description |
| --- | --- | --- |
| `phone` | String | Required. The caller's phone number. Keep any leading + or 0. |
| `date` | String | Required. Date as YYYY-MM-DD, shop-local. |
| `time` | String | Required. Start time as HH:MM. |

## `join_waitlist`

| Field | Enter |
| --- | --- |
| Tool Name | `join_waitlist` |
| When to Use (AI Instruction) | Add a caller to the waitlist. Only after they give explicit permission to be contacted. A waitlist place is not an appointment. |
| Webhook URL | `https://YOUR-TUNNEL-ADDRESS/assistant/t/YOUR-TOKEN/waitlist` |

| Parameter | Type | Description |
| --- | --- | --- |
| `name` | String | Required. The caller's name. |
| `phone` | String | Required. The caller's phone number, as read back to them. Keep any leading + or 0. |
| `service_id` | String | Required. The exact id text from get_business_info, for example classic-cut. Never a number. |
| `date` | String | Required. Date as YYYY-MM-DD, shop-local. |
| `from` | String | Required. Earliest acceptable start time as HH:MM. |
| `to` | String | Required. Latest acceptable start time as HH:MM. |
| `consent` | String | Required. Write exactly yes only if the caller agreed to be contacted about a freed time. Otherwise write no. |

## `accept_waitlist_offer`

| Field | Enter |
| --- | --- |
| Tool Name | `accept_waitlist_offer` |
| When to Use (AI Instruction) | Accept an offered waitlist time when the caller rings back. The slot is booked only if ok is true. Someone else may have taken it first. |
| Webhook URL | `https://YOUR-TUNNEL-ADDRESS/assistant/t/YOUR-TOKEN/waitlist/accept` |

| Parameter | Type | Description |
| --- | --- | --- |
| `phone` | String | Required. The caller's phone number. Keep any leading + or 0. |
| `date` | String | Required. Offered date as YYYY-MM-DD, shop-local. |
| `time` | String | Required. Offered start time as HH:MM. |
