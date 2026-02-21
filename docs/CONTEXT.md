# Project Context (Source of Truth)

## Goal
Build personalized voice receptionist agents for SMBs (restaurants first, expandable to appointment-based shops).
Voice runtime: Retell. Backend owns deterministic workflow + integrations.

## Non-negotiables
- Backend is deterministic system of record. LLM never directly "books"; it calls tools.
- Postgres is system of record (multi-tenant).
- Idempotency for booking creation/modification to prevent duplicates.
- Robust fallbacks: transfer_call or take_message on uncertainty or tool failure.
- Calendar sync is a downstream side effect; booking writes in Postgres remain authoritative.

## Core Tool/Function Set (called by Retell custom functions)
1) check_availability
2) create_booking
3) modify_booking
4) cancel_booking
5) find_booking
6) send_confirmation (optional)
7) transfer_call / handoff (Retell native tool)

## Key Runtime Behavior
- If user requests a human -> transfer_call immediately.
- If tool returns SYSTEM_DOWN / timeout -> apologize + transfer_call OR take_message.
- Confirm details before create_booking.
- If no availability -> propose 2–3 alternatives.
- Calendar create/update operations run as best-effort side effects after successful booking writes.
- Cancel flow currently deletes downstream Google Calendar events (future option: mark as CANCELLED instead).

## Data Model (minimum)
- businesses: hours/policies/transfer_phone/timezone + calendar integration fields (provider/account/calendar/oauth_status/settings)
- customers: business_id, name, phone
- bookings: business_id, customer_id, start/end, party_size, status, notes, source, external_event_id/provider
- idempotency_keys: key, response_json, created_at
- calls: retell_call_id, business_id, outcome, timestamps, transcript/summary refs
- google_oauth_credentials: business_id (unique), refresh/access token, expiry, scopes

## Webhooks
- Receive Retell events: call_started, call_ended, call_analyzed, transfer events
- Verify signature header
- Log to DB for audit/debug

## Integration Approach
Adapters behind canonical functions:
- Calendar/Reservation adapters later
- Prototype availability uses Postgres + simple capacity rules
- Google OAuth connect/callback is server-side and stores credentials in DB (never exposed via admin responses)
- `bookings.external_event_id` stores provider event linkage for later modify/cancel sync flows.
