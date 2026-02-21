from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

import app.main as main_module
from app.db.models import Business, GoogleOAuthCredential
from app.integrations.google_oauth import build_google_oauth_state
from app.main import app


client = TestClient(app)


class FakeQuery:
    def __init__(self, session, model):
        self.session = session
        self.model = model

    def all(self):
        return list(self.session.store.get(self.model, []))


class FakeSession:
    def __init__(self, businesses=None, credentials=None):
        self.store = {
            Business: list(businesses or []),
            GoogleOAuthCredential: list(credentials or []),
        }
        self.next_id = {GoogleOAuthCredential: 1}

    def query(self, model):
        return FakeQuery(self, model)

    def add(self, row):
        model = type(row)
        if getattr(row, "id", None) is None and model in self.next_id:
            row.id = self.next_id[model]
            self.next_id[model] += 1
        now = datetime.now(timezone.utc)
        if getattr(row, "created_at", None) is None:
            row.created_at = now
        if getattr(row, "updated_at", None) is None and hasattr(row, "updated_at"):
            row.updated_at = now
        if model in self.store and row not in self.store[model]:
            self.store[model].append(row)

    def commit(self):
        return None

    def rollback(self):
        return None

    def close(self):
        return None


def test_google_connect_returns_auth_url(monkeypatch):
    monkeypatch.setenv("ENV", "prod")
    monkeypatch.setenv("ADMIN_API_KEY", "admin-secret")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "google-client-id")
    monkeypatch.setenv(
        "GOOGLE_REDIRECT_URI",
        "https://example.ngrok-free.dev/v1/integrations/google/oauth/callback",
    )
    monkeypatch.setenv("GOOGLE_OAUTH_STATE_SECRET", "state-secret")

    response = client.get(
        "/v1/admin/businesses/42/google/connect",
        headers={"X-Admin-Key": "admin-secret"},
    )
    body = response.json()
    assert response.status_code == 200
    assert body["ok"] is True

    parsed = urlparse(body["data"]["auth_url"])
    params = parse_qs(parsed.query)
    assert params["redirect_uri"][0] == "https://example.ngrok-free.dev/v1/integrations/google/oauth/callback"
    assert params["scope"][0] == "https://www.googleapis.com/auth/calendar"
    assert "state" in params and params["state"][0]


def test_google_callback_invalid_state_returns_400(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "google-client-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "google-client-secret")
    monkeypatch.setenv(
        "GOOGLE_REDIRECT_URI",
        "https://example.ngrok-free.dev/v1/integrations/google/oauth/callback",
    )
    monkeypatch.setenv("GOOGLE_OAUTH_STATE_SECRET", "state-secret")

    response = client.get("/v1/integrations/google/oauth/callback?code=abc&state=bad_state")
    body = response.json()
    assert response.status_code == 400
    assert body["ok"] is False
    assert body["error_code"] == "INVALID_OAUTH_STATE"


def test_google_callback_saves_credentials_and_updates_business(monkeypatch):
    business = Business(
        id=7,
        name="Biz",
        external_id="biz_7",
        timezone="America/New_York",
        calendar_provider="none",
        calendar_oauth_status="not_connected",
        calendar_settings_json={},
    )
    business.created_at = datetime.now(timezone.utc)
    fake_session = FakeSession(businesses=[business])

    monkeypatch.setenv("GOOGLE_CLIENT_ID", "google-client-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "google-client-secret")
    monkeypatch.setenv(
        "GOOGLE_REDIRECT_URI",
        "https://example.ngrok-free.dev/v1/integrations/google/oauth/callback",
    )
    monkeypatch.setenv("GOOGLE_OAUTH_STATE_SECRET", "state-secret")
    monkeypatch.setattr(main_module, "SessionLocal", lambda: fake_session)
    monkeypatch.setattr(
        main_module,
        "exchange_google_code_for_tokens",
        lambda **_kwargs: {
            "refresh_token": "refresh_123",
            "access_token": "access_123",
            "expires_in": 3600,
            "scope": "https://www.googleapis.com/auth/calendar",
        },
    )

    state = build_google_oauth_state(business_id=7, secret="state-secret")
    response = client.get(f"/v1/integrations/google/oauth/callback?code=abc123&state={state}")

    assert response.status_code == 200
    assert "Google Calendar connected. You can close this tab." in response.text
    assert business.calendar_provider == "google"
    assert business.calendar_oauth_status == "connected"
    assert business.calendar_id == "primary"
    assert len(fake_session.store[GoogleOAuthCredential]) == 1
    assert fake_session.store[GoogleOAuthCredential][0].refresh_token == "refresh_123"
