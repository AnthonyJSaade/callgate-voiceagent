from datetime import datetime, timezone

from fastapi.testclient import TestClient

import app.main as main_module
from app.db.models import Business
from app.main import app


client = TestClient(app)


class FakeQuery:
    def __init__(self, session, model):
        self.session = session
        self.model = model

    def all(self):
        return list(self.session.store.get(self.model, []))


class FakeSession:
    def __init__(self, businesses=None):
        self.store = {
            Business: list(businesses or []),
        }
        self.next_id = {Business: 1}
        if self.store[Business]:
            self.next_id[Business] = max(item.id for item in self.store[Business]) + 1

    def query(self, model):
        return FakeQuery(self, model)

    def add(self, row):
        model = type(row)
        if getattr(row, "id", None) is None and model in self.next_id:
            row.id = self.next_id[model]
            self.next_id[model] += 1
        if getattr(row, "created_at", None) is None:
            row.created_at = datetime.now(timezone.utc)
        if model in self.store and row not in self.store[model]:
            self.store[model].append(row)

    def commit(self):
        return None

    def rollback(self):
        return None

    def close(self):
        return None


def test_admin_auth_required(monkeypatch):
    monkeypatch.setenv("ENV", "prod")
    monkeypatch.setenv("ADMIN_API_KEY", "super-secret")

    response = client.get("/v1/admin/businesses")
    assert response.status_code == 401
    assert response.json()["detail"]["error_code"] == "INVALID_ADMIN_API_KEY"


def test_create_business_success(monkeypatch):
    fake_session = FakeSession()
    monkeypatch.setenv("ENV", "prod")
    monkeypatch.setenv("ADMIN_API_KEY", "super-secret")
    monkeypatch.setattr(main_module, "SessionLocal", lambda: fake_session)

    response = client.post(
        "/v1/admin/businesses",
        json={
            "name": "New Biz",
            "external_id": "new_biz_001",
            "calendar_provider": "google",
            "calendar_id": "primary",
        },
        headers={"X-Admin-Key": "super-secret"},
    )
    body = response.json()
    assert response.status_code == 200
    assert body["ok"] is True
    assert body["data"]["business"]["external_id"] == "new_biz_001"
    assert body["data"]["business"]["calendar_provider"] == "google"


def test_create_business_duplicate_external_id_returns_409(monkeypatch):
    existing = Business(
        id=1,
        name="Existing",
        external_id="dup_id",
        timezone="America/New_York",
        calendar_oauth_status="not_connected",
        calendar_settings_json={},
    )
    existing.created_at = datetime.now(timezone.utc)
    fake_session = FakeSession(businesses=[existing])

    monkeypatch.setenv("ENV", "prod")
    monkeypatch.setenv("ADMIN_API_KEY", "super-secret")
    monkeypatch.setattr(main_module, "SessionLocal", lambda: fake_session)

    response = client.post(
        "/v1/admin/businesses",
        json={"name": "Another", "external_id": "dup_id"},
        headers={"X-Admin-Key": "super-secret"},
    )
    body = response.json()
    assert response.status_code == 409
    assert body["ok"] is False
    assert body["error_code"] == "DUPLICATE_EXTERNAL_ID"


def test_patch_updates_calendar_fields(monkeypatch):
    existing = Business(
        id=2,
        name="Patch Me",
        external_id="patch_me",
        timezone="America/New_York",
        calendar_provider="none",
        calendar_oauth_status="not_connected",
        calendar_settings_json={},
    )
    existing.created_at = datetime.now(timezone.utc)
    fake_session = FakeSession(businesses=[existing])

    monkeypatch.setenv("ENV", "prod")
    monkeypatch.setenv("ADMIN_API_KEY", "super-secret")
    monkeypatch.setattr(main_module, "SessionLocal", lambda: fake_session)

    response = client.patch(
        "/v1/admin/businesses/2",
        json={"calendar_provider": "google", "calendar_id": "calendar_123"},
        headers={"X-Admin-Key": "super-secret"},
    )
    body = response.json()
    assert response.status_code == 200
    assert body["ok"] is True
    assert body["data"]["business"]["calendar_provider"] == "google"
    assert body["data"]["business"]["calendar_id"] == "calendar_123"
