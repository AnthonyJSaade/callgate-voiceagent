from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib import parse, request

from sqlalchemy.orm import Session

from app.db.models import Business, GoogleOAuthCredential

GOOGLE_AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
GOOGLE_CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar"


def build_google_oauth_state(
    business_id: int,
    secret: str,
    now: datetime | None = None,
) -> str:
    now_utc = now or datetime.now(timezone.utc)
    payload = {"business_id": business_id, "ts": int(now_utc.timestamp())}
    payload_json = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    payload_b64 = _urlsafe_b64encode(payload_json.encode("utf-8"))
    signature = hmac.new(secret.encode("utf-8"), payload_b64.encode("utf-8"), hashlib.sha256)
    return f"{payload_b64}.{signature.hexdigest()}"


def parse_google_oauth_state(
    state: str,
    secret: str,
    max_age_seconds: int = 3600,
    now: datetime | None = None,
) -> int:
    try:
        payload_b64, provided_sig = state.split(".", 1)
    except ValueError as exc:
        raise ValueError("Invalid OAuth state format.") from exc

    expected_sig = hmac.new(
        secret.encode("utf-8"),
        payload_b64.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected_sig, provided_sig):
        raise ValueError("Invalid OAuth state signature.")

    payload_raw = _urlsafe_b64decode(payload_b64).decode("utf-8")
    payload = json.loads(payload_raw)
    business_id = int(payload.get("business_id", 0))
    ts = int(payload.get("ts", 0))
    if business_id <= 0 or ts <= 0:
        raise ValueError("Invalid OAuth state payload.")

    now_utc = now or datetime.now(timezone.utc)
    age = int(now_utc.timestamp()) - ts
    if age < 0 or age > max_age_seconds:
        raise ValueError("OAuth state expired.")
    return business_id


def build_google_auth_url(client_id: str, redirect_uri: str, state: str) -> str:
    query = parse.urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": GOOGLE_CALENDAR_SCOPE,
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "true",
            "state": state,
        }
    )
    return f"{GOOGLE_AUTH_ENDPOINT}?{query}"


def exchange_google_code_for_tokens(
    *,
    code: str,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
) -> dict[str, Any]:
    form_payload = parse.urlencode(
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
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
        raise ValueError("Google token exchange failed.") from exc

    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ValueError("Google token response was invalid JSON.") from exc

    if "refresh_token" not in parsed and "access_token" not in parsed:
        raise ValueError("Google token response missing required fields.")
    return parsed


def persist_google_credentials_and_business(
    db: Session,
    *,
    business_id: int,
    token_payload: dict[str, Any],
) -> None:
    business = _find_business(db, business_id=business_id)
    if business is None:
        raise LookupError("Business not found for OAuth callback.")

    credentials = _find_google_credentials(db, business_id=business_id)
    refresh_token = _pick_text(token_payload.get("refresh_token"))
    access_token = _pick_text(token_payload.get("access_token"))
    scopes = _pick_text(token_payload.get("scope"))
    expires_in = token_payload.get("expires_in")
    token_expiry = _expiry_from_seconds(expires_in)

    if credentials is None:
        if not refresh_token:
            raise ValueError("Google OAuth callback missing refresh_token.")
        credentials = GoogleOAuthCredential(
            business_id=business_id,
            refresh_token=refresh_token,
            access_token=access_token,
            token_expiry=token_expiry,
            scopes=scopes,
        )
        db.add(credentials)
    else:
        if refresh_token:
            credentials.refresh_token = refresh_token
        credentials.access_token = access_token
        credentials.token_expiry = token_expiry
        credentials.scopes = scopes
        credentials.updated_at = datetime.now(timezone.utc)

    business.calendar_provider = "google"
    business.calendar_oauth_status = "connected"
    if not business.calendar_id:
        business.calendar_id = "primary"

    db.commit()


def _find_business(db: Session, business_id: int) -> Business | None:
    for business in db.query(Business).all():
        if business.id == business_id:
            return business
    return None


def _find_google_credentials(db: Session, business_id: int) -> GoogleOAuthCredential | None:
    for credential in db.query(GoogleOAuthCredential).all():
        if credential.business_id == business_id:
            return credential
    return None


def _expiry_from_seconds(expires_in: Any) -> datetime | None:
    try:
        if expires_in is None:
            return None
        seconds = int(expires_in)
    except (TypeError, ValueError):
        return None
    return datetime.now(timezone.utc) + timedelta(seconds=seconds)


def _pick_text(value: Any) -> str | None:
    if isinstance(value, str):
        trimmed = value.strip()
        return trimmed if trimmed else None
    return None


def _urlsafe_b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")


def _urlsafe_b64decode(raw: str) -> bytes:
    padding = "=" * ((4 - len(raw) % 4) % 4)
    return base64.urlsafe_b64decode(raw + padding)
