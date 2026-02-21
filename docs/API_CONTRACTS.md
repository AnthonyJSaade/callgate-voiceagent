# API Contracts

## Current Endpoints

## Admin Auth

Admin endpoints under `/v1/admin/*` require header `X-Admin-Key`.

- `ENV=prod`: `ADMIN_API_KEY` must be configured and request key must match.
- `ENV=dev`/`development`/`local`: if `ADMIN_API_KEY` is unset, admin routes are allowed (warning is logged).

## Tenant Resolution Order (Tool Endpoints)

For Retell custom function tool endpoints (`/v1/tools/*`), business resolution uses this order:

1. `call.metadata.internal_customer_id`
2. `call.metadata.business_id`
3. `call.to_number` or `call.agent_id` (when present)
4. Dev-only fallback to demo business (`external_id = "demo"`)

Behavior by environment:

- `ENV=dev` (or `development`/`local`): if tenant metadata is missing or unmapped, fallback to demo business.
- `ENV=prod`: if tenant context is missing, return:
```json
{
  "ok": false,
  "error_code": "MISSING_TENANT_CONTEXT",
  "human_message": "Missing tenant context in call metadata"
}
```

### `GET /health`
- Success response: `200`
- Body:
```json
{"ok": true}
```

### `POST /v1/tools/check_availability`
- Requires header: `X-Retell-Signature`
- Retell wrapper request body:
```json
{
  "name": "check_availability",
  "args": {
    "desired_start": "2026-02-19T18:00:00+00:00",
    "party_size": 2,
    "flexibility_minutes": 60
  },
  "call": {
    "metadata": {
      "internal_customer_id": "demo_internal_customer_001"
    }
  }
}
```
- Business resolution:
  - Read `call.metadata.internal_customer_id`
  - Resolve `businesses.external_id = internal_customer_id`
- Success response: `200`
- Response envelope:
```json
{
  "ok": true,
  "data": {
    "result": "AVAILABLE",
    "available_start_times": [
      "2026-02-19T17:30:00+00:00",
      "2026-02-19T18:00:00+00:00",
      "2026-02-19T18:15:00+00:00"
    ]
  }
}
```
- No availability response:
```json
{
  "ok": true,
  "data": {
    "result": "NO_AVAILABILITY",
    "available_start_times": []
  }
}
```
- Error envelope:
```json
{
  "ok": false,
  "error_code": "INVALID_ARGS",
  "human_message": "Invalid args: ..."
}
```

### `POST /tools/check_availability`
- Alias to `/v1/tools/check_availability` (kept for compatibility)

### `POST /v1/tools/create_booking`
- Requires header: `X-Retell-Signature`
- Retell wrapper request body:
```json
{
  "name": "create_booking",
  "args": {
    "customer_name": "Alice",
    "customer_phone": "+15555550123",
    "start_time": "2026-02-20T18:00:00+00:00",
    "party_size": 4,
    "notes": "Window seat"
  },
  "call": {
    "call_id": "retell_call_123",
    "metadata": {
      "internal_customer_id": "demo_internal_customer_001"
    }
  }
}
```
- Rules:
  - Duration fixed to `90` minutes
  - Find or create customer by `(business_id, customer_phone)`
  - Insert booking with `status=confirmed`, `source=retell`
  - Idempotency key = `sha256(call.call_id + "|" + start_time + "|" + customer_phone)`
  - If idempotency key exists, return stored prior `response_json`
- Success response envelope:
```json
{
  "ok": true,
  "data": {
    "booking_id": 1,
    "customer_id": 1,
    "customer_name": "Alice",
    "customer_phone": "+15555550123",
    "start_time": "2026-02-20T18:00:00+00:00",
    "end_time": "2026-02-20T19:30:00+00:00",
    "party_size": 4,
    "status": "confirmed",
    "source": "retell",
    "notes": "Window seat"
  }
}
```

### `POST /tools/create_booking`
- Alias to `/v1/tools/create_booking` (kept for compatibility)

### `POST /v1/tools/modify_booking`
- Requires header: `X-Retell-Signature`
- Args:
  - `booking_id` (required)
  - allowed changes: `start_time`, `party_size`, `notes`
- Rules:
  - booking must belong to resolved business tenant
  - if `start_time` changes, availability is re-checked for the updated slot
  - booking is updated in place (status unchanged unless cancelled elsewhere)
- Success response envelope:
```json
{
  "ok": true,
  "data": {
    "booking_id": 1,
    "start_time": "2026-02-22T19:00:00+00:00",
    "end_time": "2026-02-22T20:30:00+00:00",
    "party_size": 4,
    "notes": "Updated",
    "status": "confirmed",
    "source": "retell"
  }
}
```

### `POST /tools/modify_booking`
- Alias to `/v1/tools/modify_booking` (kept for compatibility)

### `POST /v1/tools/cancel_booking`
- Requires header: `X-Retell-Signature`
- Args:
  - `booking_id` (required)
- Rules:
  - booking must belong to resolved business tenant
  - sets `status=cancelled` (no deletes)
- Success response envelope:
```json
{
  "ok": true,
  "data": {
    "booking_id": 1,
    "status": "cancelled"
  }
}
```

### `POST /tools/cancel_booking`
- Alias to `/v1/tools/cancel_booking` (kept for compatibility)

### `POST /v1/tools/find_booking`
- Requires header: `X-Retell-Signature`
- Args:
  - `customer_phone` (required)
  - `customer_name` (optional)
  - `date` (optional, `YYYY-MM-DD`)
  - `time` (optional, `HH:MM`)
  - `lookahead_days` (optional, default `30`)
- Rules:
  - business is resolved from existing tenant call context resolution
  - normalize incoming phone and customer phone values before matching
  - search only tenant bookings where:
    - `business_id` matches tenant
    - `status='confirmed'`
    - `start_time` is between now and now + lookahead window
  - optional date/time values narrow the candidate set
- Not found response:
```json
{
  "ok": false,
  "error_code": "BOOKING_NOT_FOUND",
  "human_message": "I couldn't find a reservation under that phone number."
}
```
- Single match response:
```json
{
  "ok": true,
  "data": {
    "booking": {
      "booking_id": 1,
      "start_time": "2026-02-20T18:00:00+00:00",
      "party_size": 4,
      "status": "confirmed",
      "customer_name": "Alice",
      "customer_phone": "+15555550123"
    }
  }
}
```
- Multiple match response:
```json
{
  "ok": false,
  "error_code": "AMBIGUOUS_BOOKING",
  "human_message": "I found multiple reservations. Please share date or time to narrow it down."
}
```

### `POST /tools/find_booking`
- Alias to `/v1/tools/find_booking` (kept for compatibility)

### `POST /tools/resolve_business`
- Requires header: `X-Retell-Signature`
- Expected Retell wrapper request body:
```json
{
  "name": "check_availability",
  "args": {
    "party_size": 2
  },
  "call": {
    "metadata": {
      "internal_customer_id": "demo_internal_customer_001"
    }
  }
}
```

### `POST /debug/retell/resolve_business`
- No signature required (local debugging only)
- Uses the same request body contract as `/tools/resolve_business`
- Resolves business using `call.metadata.internal_customer_id`
- Tenant resolution:
  - Read `call.metadata.internal_customer_id`
  - Resolve `businesses.external_id = internal_customer_id`
- Success response: `200`
- Body:
```json
{
  "name": "check_availability",
  "resolved_business": {
    "id": 1,
    "external_id": "demo_internal_customer_001",
    "name": "Demo Restaurant",
    "timezone": "America/New_York"
  }
}
```

### `POST /webhooks/retell`
- Requires header: `X-Retell-Signature`
- Alias to `/v1/retell/webhook` (kept for compatibility)

### `POST /v1/retell/webhook`
- Requires header: `X-Retell-Signature`
- Payload:
```json
{
  "event": "call_ended",
  "call": {
    "call_id": "retell_call_1",
    "metadata": {
      "internal_customer_id": "demo_internal_customer_001"
    },
    "ended_at": "2026-02-23T20:30:00+00:00",
    "outcome": "booked"
  }
}
```
- Behavior:
  - upsert `calls` by `retell_call_id = call.call_id`
  - append full webhook payload into `raw_events_json.events`
  - on `call_ended` or `call_analyzed`, persist `ended_at` and `outcome` when present
  - repeated webhook events are handled safely (same call row is updated, not duplicated)
- Success response: `204`

### `POST /v1/admin/businesses`
- Requires header: `X-Admin-Key`
- Body:
  - `name` (required)
  - `external_id` (required, unique)
  - `timezone` (optional, default `America/New_York`)
  - `phone`, `transfer_phone` (optional)
  - `hours_json`, `policies_json` (optional)
  - `calendar_provider` (optional, default `none`)
  - `calendar_account_id`, `calendar_id` (optional)
  - `calendar_oauth_status` (optional, default `not_connected`)
  - `calendar_settings_json` (optional, default `{}`)
- Duplicate `external_id` response: `409`
```json
{
  "ok": false,
  "error_code": "DUPLICATE_EXTERNAL_ID",
  "human_message": "external_id already exists"
}
```

### `GET /v1/admin/businesses`
- Requires header: `X-Admin-Key`
- Success response:
```json
{
  "ok": true,
  "data": {
    "businesses": [
      {
        "id": 1,
        "name": "Demo Restaurant",
        "external_id": "demo",
        "timezone": "America/New_York",
        "calendar_provider": "none",
        "calendar_account_id": null,
        "calendar_id": null,
        "calendar_oauth_status": "not_connected",
        "calendar_settings_json": {}
      }
    ]
  }
}
```

### `PATCH /v1/admin/businesses/{business_id}`
- Requires header: `X-Admin-Key`
- Partial update for create fields
- Duplicate `external_id` response: `409`
- Not found response: `404`
```json
{
  "ok": false,
  "error_code": "BUSINESS_NOT_FOUND",
  "human_message": "Business not found."
}
```

### `GET /v1/admin/businesses/{business_id}/google/connect`
- Requires header: `X-Admin-Key`
- Returns Google OAuth authorization URL for that business.
- Success response:
```json
{
  "ok": true,
  "data": {
    "auth_url": "https://accounts.google.com/o/oauth2/v2/auth?..."
  }
}
```
- Uses:
  - `scope=https://www.googleapis.com/auth/calendar`
  - `access_type=offline`
  - `prompt=consent`
  - signed `state` containing `business_id` + timestamp

### `GET /v1/integrations/google/oauth/callback`
- Public endpoint (no admin key)
- Query params: `code`, `state`
- Behavior:
  - verifies signed state
  - exchanges auth code with Google token endpoint
  - upserts `google_oauth_credentials` for business (stores refresh/access/expiry/scopes)
  - updates business to `calendar_provider=google`, `calendar_oauth_status=connected`
  - sets `calendar_id=primary` when empty
- Success response: HTML page:
  - `Google Calendar connected. You can close this tab.`

## Calendar Integration Storage Notes

- Business-level calendar integration config is stored on `businesses`:
  - `calendar_provider`, `calendar_account_id`, `calendar_id`, `calendar_oauth_status`, `calendar_settings_json`
- Booking-level external provider linkage is stored on `bookings`:
  - `external_event_id`, `external_event_provider`
- Google OAuth credentials are stored in `google_oauth_credentials`:
  - `business_id` (unique), `refresh_token`, `access_token`, `token_expiry`, `scopes`
  - token fields are never included in admin route responses

## Tool Failure Envelope (for agent fallback)
- All tool endpoint failures must return:
  - `error_code` (machine-readable)
  - `human_message` (agent-safe user-facing fallback)
