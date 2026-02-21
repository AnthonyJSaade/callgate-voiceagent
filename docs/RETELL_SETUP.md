# Retell Setup Guide (Local Development)

This guide shows how to connect Retell to your local backend so calls can trigger your tool endpoints.

## What You Need

- Docker Desktop running
- This project running locally
- A Retell account
- ngrok installed (`https://ngrok.com/download`)

## 1) Start Your Local Backend

From the project root:

```bash
make up
```

Your backend should be available at:

- `http://localhost:8000/health`

Quick check:

```bash
curl http://localhost:8000/health
```

Expected:

```json
{"ok":true}
```

## 2) Expose Localhost with ngrok

In a new terminal window:

```bash
ngrok http 8000
```

ngrok will show a public URL like:

- `https://abc123.ngrok-free.app`

Keep this terminal running.

## 3) Configure Both Retell Webhooks (Important)

In Retell dashboard:

1. Go to your agent/webhook settings.
2. Configure **two different webhook URLs**:

### A) Call Event Webhook (after/during calls)

- **Purpose:** Receives events like `call_started`, `call_ended`, `call_analyzed`.
- **Backend URL:**  
  `https://YOUR_NGROK_URL/v1/retell/webhook`

### B) Inbound Call Webhook (before call connects)

- **Purpose:** Runs before the call is connected so you can attach tenant metadata.
- **Backend URL:**  
  `https://YOUR_NGROK_URL/v1/retell/inbound`

Example base URL:

- `https://abc123.ngrok-free.app`

Final URLs:

- `https://abc123.ngrok-free.app/v1/retell/webhook`
- `https://abc123.ngrok-free.app/v1/retell/inbound`

## 4) Configure Retell Custom Functions

Create one Retell Custom Function per tool endpoint.

Use the ngrok base URL with these paths:

- `POST /v1/tools/check_availability`
- `POST /v1/tools/create_booking`
- `POST /v1/tools/modify_booking`
- `POST /v1/tools/cancel_booking`

Full examples:

- `https://abc123.ngrok-free.app/v1/tools/check_availability`
- `https://abc123.ngrok-free.app/v1/tools/create_booking`
- `https://abc123.ngrok-free.app/v1/tools/modify_booking`
- `https://abc123.ngrok-free.app/v1/tools/cancel_booking`

### Parameter Schemas (Copy/Paste)

Use the files in:

- `docs/RETELL_FUNCTION_SCHEMAS/check_availability.json`
- `docs/RETELL_FUNCTION_SCHEMAS/create_booking.json`
- `docs/RETELL_FUNCTION_SCHEMAS/modify_booking.json`
- `docs/RETELL_FUNCTION_SCHEMAS/cancel_booking.json`

Paste each schema into Retell Custom Function -> **Define parameters**.

## 5) Inbound Call Webhook (sets tenant metadata)

### Why this is needed

This project is multi-tenant. Tool calls resolve the business from:

- `call.metadata.internal_customer_id`

If this metadata is missing, the backend cannot reliably map the caller to the correct business.

### How it works in Retell

1. A new incoming call hits Retell.
2. Retell calls your **Inbound Call Webhook** (`/v1/retell/inbound`) before connecting the call.
3. Your inbound webhook response sets metadata on the call.
4. Later custom function calls use that metadata for tenant resolution.

### Copy/paste prototype response (preferred shape)

Use a response that sets:

- `internal_customer_id` (tenant id used by backend)

Example:

```json
{
  "metadata": {
    "internal_customer_id": "demo_internal_customer_001"
  }
}
```

If your Retell UI expects metadata nested under another field (for example call-level updates), use the equivalent shape but keep:

- `metadata.internal_customer_id`

### Prototype fallback behavior

If no business mapping is found during inbound resolution:

- set `internal_customer_id` to `"demo"`
- add a debug flag in metadata so you can trace fallback routing

Example fallback:

```json
{
  "metadata": {
    "internal_customer_id": "demo",
    "debug_unmapped_tenant": true
  }
}
```

## 6) Signature + API Key Notes

Your backend verifies `X-Retell-Signature` with two keys:

- `RETELL_API_KEY`: general Retell API usage + `/v1/tools/*` custom function signature verification
- `RETELL_WEBHOOK_API_KEY`: webhook-badge key for `/v1/retell/webhook` and `/v1/retell/inbound`

Make sure your backend env has:

- `RETELL_API_KEY=<your_retell_api_key>`
- `RETELL_WEBHOOK_API_KEY=<your_webhook_badge_key>`

Behavior by environment:

- `ENV=prod`: webhook endpoints require `RETELL_WEBHOOK_API_KEY`.
- `ENV=dev`/`development`/`local`: webhook endpoints can fall back to `RETELL_API_KEY`, and backend logs a warning.

For this repo, env templates live under `infra/` (not `backend/`).

If needed, update your runtime env from `infra/.env.example` values and restart:

```bash
make down
make up
```

## 7) Agent Prompt Rules (Summary)

Use these rules in your Retell agent prompt/instructions:

1. **Human handoff on request**
   - If caller asks for a human, hand off immediately.
2. **Fallback on system/tool failure**
   - If a tool returns `SYSTEM_DOWN` or times out, apologize and transfer call (or take a message).
3. **Confirm before booking**
   - Confirm key details (time, party size, name, phone) before final booking.

These match project behavior expectations from `docs/CONTEXT.md`.

## 8) End-to-End Smoke Test

1. Trigger a call with your Retell agent.
2. Confirm inbound webhook is configured and setting `metadata.internal_customer_id`.
3. Ask for availability.
4. Ask to create/modify/cancel a booking.
5. Confirm backend still healthy:

```bash
curl http://localhost:8000/health
```

If calls are not reaching local backend:

- check `ngrok` terminal is still running
- confirm both webhook URLs and all function URLs use current ngrok URL
- verify Docker/backend are up with `make logs`
