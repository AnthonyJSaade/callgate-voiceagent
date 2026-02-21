from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib import parse, request

from sqlalchemy.orm import Session

from app.db.models import Booking, Business, Customer, GoogleOAuthCredential

GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
GOOGLE_CALENDAR_EVENT_ENDPOINT_TEMPLATE = (
    "https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events"
)
GOOGLE_CALENDAR_EVENT_UPDATE_ENDPOINT_TEMPLATE = (
    "https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events/{event_id}"
)


def get_access_token(business_id: int, db: Session | None = None) -> str:
    managed_session = db is None
    session = db
    if session is None:
        from app.db.session import SessionLocal

        session = SessionLocal()

    try:
        credentials = _find_credentials(db=session, business_id=business_id)
        if credentials is None:
            raise LookupError("Google OAuth credentials not found for business.")
        if not credentials.refresh_token:
            raise ValueError("Missing Google refresh token for business.")

        client_id = os.getenv("GOOGLE_CLIENT_ID", "").strip()
        client_secret = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()
        if not client_id or not client_secret:
            raise ValueError("Google OAuth client configuration is incomplete.")

        form_payload = parse.urlencode(
            {
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": credentials.refresh_token,
                "grant_type": "refresh_token",
            }
        ).encode("utf-8")

        req = request.Request(
            GOOGLE_TOKEN_ENDPOINT,
            data=form_payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=15) as resp:
                body = resp.read().decode("utf-8")
        except Exception as exc:
            raise ValueError("Google token refresh failed.") from exc

        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise ValueError("Google token refresh returned invalid JSON.") from exc

        access_token = payload.get("access_token")
        if not isinstance(access_token, str) or not access_token.strip():
            raise ValueError("Google token refresh missing access_token.")

        credentials.access_token = access_token.strip()
        credentials.token_expiry = _expiry_from_seconds(payload.get("expires_in"))
        scopes = payload.get("scope")
        if isinstance(scopes, str) and scopes.strip():
            credentials.scopes = scopes.strip()
        credentials.updated_at = datetime.now(timezone.utc)
        session.commit()
        return credentials.access_token
    finally:
        if managed_session:
            session.close()


def create_event(
    business: Business,
    booking: Booking,
    customer: Customer,
    db: Session | None = None,
) -> dict[str, Any]:
    access_token = get_access_token(business_id=business.id, db=db)

    calendar_id = (business.calendar_id or "primary").strip() or "primary"
    calendar_path = parse.quote(calendar_id, safe="")
    endpoint = GOOGLE_CALENDAR_EVENT_ENDPOINT_TEMPLATE.format(calendar_id=calendar_path)

    notes = booking.notes or ""
    description = (
        f"Party size: {booking.party_size}\n"
        f"Phone: {customer.phone}\n"
        f"Notes: {notes}"
    )

    payload = {
        "summary": f"{business.name} Reservation - {customer.name}",
        "description": description,
        "start": {
            "dateTime": booking.start_time.isoformat(),
            "timeZone": business.timezone,
        },
        "end": {
            "dateTime": booking.end_time.isoformat(),
            "timeZone": business.timezone,
        },
        "reminders": {"useDefault": True},
    }

    req = request.Request(
        endpoint,
        data=json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {access_token}",
        },
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode("utf-8")
    except Exception as exc:
        raise ValueError("Google calendar event creation failed.") from exc

    try:
        event_payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ValueError("Google calendar event response was invalid JSON.") from exc

    event_id = event_payload.get("id")
    if not isinstance(event_id, str) or not event_id.strip():
        raise ValueError("Google calendar event response missing id.")
    return event_payload


def update_event(
    business: Business,
    booking: Booking,
    customer: Customer,
    *,
    external_event_id: str,
    db: Session | None = None,
) -> dict[str, Any]:
    access_token = get_access_token(business_id=business.id, db=db)

    calendar_id = (business.calendar_id or "primary").strip() or "primary"
    calendar_path = parse.quote(calendar_id, safe="")
    event_path = parse.quote(external_event_id.strip(), safe="")
    endpoint = GOOGLE_CALENDAR_EVENT_UPDATE_ENDPOINT_TEMPLATE.format(
        calendar_id=calendar_path,
        event_id=event_path,
    )

    notes = booking.notes or ""
    description = (
        f"Party size: {booking.party_size}\n"
        f"Phone: {customer.phone}\n"
        f"Notes: {notes}"
    )
    payload = {
        "description": description,
        "start": {
            "dateTime": booking.start_time.isoformat(),
            "timeZone": business.timezone,
        },
        "end": {
            "dateTime": booking.end_time.isoformat(),
            "timeZone": business.timezone,
        },
    }

    req = request.Request(
        endpoint,
        data=json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {access_token}",
        },
        method="PATCH",
    )
    try:
        with request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode("utf-8")
    except Exception as exc:
        raise ValueError("Google calendar event update failed.") from exc

    try:
        event_payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ValueError("Google calendar event update response was invalid JSON.") from exc

    event_id = event_payload.get("id")
    if not isinstance(event_id, str) or not event_id.strip():
        raise ValueError("Google calendar event update response missing id.")
    return event_payload


def delete_event(
    business: Business,
    *,
    external_event_id: str,
    db: Session | None = None,
) -> None:
    access_token = get_access_token(business_id=business.id, db=db)

    calendar_id = (business.calendar_id or "primary").strip() or "primary"
    calendar_path = parse.quote(calendar_id, safe="")
    event_path = parse.quote(external_event_id.strip(), safe="")
    endpoint = GOOGLE_CALENDAR_EVENT_UPDATE_ENDPOINT_TEMPLATE.format(
        calendar_id=calendar_path,
        event_id=event_path,
    )

    req = request.Request(
        endpoint,
        headers={"Authorization": f"Bearer {access_token}"},
        method="DELETE",
    )
    try:
        with request.urlopen(req, timeout=15):
            return None
    except Exception as exc:
        raise ValueError("Google calendar event delete failed.") from exc


def _find_credentials(db: Session, business_id: int) -> GoogleOAuthCredential | None:
    for row in db.query(GoogleOAuthCredential).all():
        if row.business_id == business_id:
            return row
    return None


def _expiry_from_seconds(expires_in: Any) -> datetime | None:
    try:
        if expires_in is None:
            return None
        seconds = int(expires_in)
    except (TypeError, ValueError):
        return None
    return datetime.now(timezone.utc) + timedelta(seconds=seconds)
