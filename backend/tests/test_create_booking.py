from types import SimpleNamespace

from fastapi.testclient import TestClient

import app.main as main_module
from app.db.models import Booking, Customer, IdempotencyKey
from app.main import app
from app.security import retell_verify


client = TestClient(app)


def _retell_payload(args: dict):
    return {
        "name": "create_booking",
        "args": args,
        "call": {
            "call_id": "retell_call_123",
            "metadata": {"internal_customer_id": "demo_internal_customer_001"},
        },
    }


class FakeQuery:
    def __init__(self, session, model):
        self.session = session
        self.model = model

    def all(self):
        return list(self.session.store.get(self.model, []))


class FakeSession:
    def __init__(self):
        self.store = {
            Customer: [],
            Booking: [],
            IdempotencyKey: [],
        }
        self.next_id = {
            Customer: 1,
            Booking: 1,
            IdempotencyKey: 1,
        }

    def query(self, model):
        return FakeQuery(self, model)

    def add(self, row):
        model = type(row)
        if getattr(row, "id", None) is None and model in self.next_id:
            row.id = self.next_id[model]
            self.next_id[model] += 1
        if model in self.store and row not in self.store[model]:
            self.store[model].append(row)

    def flush(self):
        return None

    def commit(self):
        return None

    def rollback(self):
        return None

    def close(self):
        return None


def test_create_booking_creates_new_booking(monkeypatch):
    fake_session = FakeSession()
    monkeypatch.setenv("RETELL_API_KEY", "test_key")
    monkeypatch.setattr(retell_verify.retell_client, "verify", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(main_module, "SessionLocal", lambda: fake_session)
    monkeypatch.setattr(
        main_module,
        "get_business_from_call",
        lambda _call: SimpleNamespace(id=1, external_id="demo_internal_customer_001"),
    )

    response = client.post(
        "/v1/tools/create_booking",
        json=_retell_payload(
            {
                "customer_name": "Alice",
                "customer_phone": "+15555550123",
                "start_time": "2026-02-20T18:00:00+00:00",
                "party_size": 4,
                "notes": "Window seat",
            }
        ),
        headers={"X-Retell-Signature": "valid_signature"},
    )

    body = response.json()
    assert response.status_code == 200
    assert body["ok"] is True
    assert body["data"]["booking_id"] == 1
    assert body["data"]["status"] == "confirmed"
    assert body["data"]["source"] == "retell"


def test_create_booking_idempotent_returns_same_booking_id(monkeypatch):
    fake_session = FakeSession()
    monkeypatch.setenv("RETELL_API_KEY", "test_key")
    monkeypatch.setattr(retell_verify.retell_client, "verify", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(main_module, "SessionLocal", lambda: fake_session)
    monkeypatch.setattr(
        main_module,
        "get_business_from_call",
        lambda _call: SimpleNamespace(id=1, external_id="demo_internal_customer_001"),
    )

    payload = _retell_payload(
        {
            "customer_name": "Alice",
            "customer_phone": "+15555550123",
            "start_time": "2026-02-20T18:00:00+00:00",
            "party_size": 4,
            "notes": "Window seat",
        }
    )
    headers = {"X-Retell-Signature": "valid_signature"}

    first = client.post("/v1/tools/create_booking", json=payload, headers=headers).json()
    second = client.post("/v1/tools/create_booking", json=payload, headers=headers).json()

    assert first["ok"] is True
    assert second["ok"] is True
    assert first["data"]["booking_id"] == second["data"]["booking_id"]
    assert len(fake_session.store[Booking]) == 1


def test_create_booking_google_sync_success_saves_external_event_id(monkeypatch):
    fake_session = FakeSession()
    monkeypatch.setenv("RETELL_API_KEY", "test_key")
    monkeypatch.setattr(retell_verify.retell_client, "verify", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(main_module, "SessionLocal", lambda: fake_session)
    monkeypatch.setattr(
        main_module,
        "get_business_from_call",
        lambda _call: SimpleNamespace(
            id=1,
            external_id="demo_internal_customer_001",
            calendar_provider="google",
            calendar_oauth_status="connected",
            calendar_id="primary",
            timezone="America/New_York",
            name="Demo Restaurant",
        ),
    )
    monkeypatch.setattr(
        "app.tools.create_booking.create_google_calendar_event",
        lambda **_kwargs: {"id": "evt_123"},
    )

    response = client.post(
        "/v1/tools/create_booking",
        json=_retell_payload(
            {
                "customer_name": "Alice",
                "customer_phone": "+15555550123",
                "start_time": "2026-02-20T18:00:00+00:00",
                "party_size": 4,
                "notes": "Window seat",
            }
        ),
        headers={"X-Retell-Signature": "valid_signature"},
    )

    body = response.json()
    assert response.status_code == 200
    assert body["ok"] is True
    assert fake_session.store[Booking][0].external_event_provider == "google"
    assert fake_session.store[Booking][0].external_event_id == "evt_123"
    assert "warning" not in body["data"]


def test_create_booking_google_sync_failure_returns_warning(monkeypatch):
    fake_session = FakeSession()
    monkeypatch.setenv("RETELL_API_KEY", "test_key")
    monkeypatch.setattr(retell_verify.retell_client, "verify", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(main_module, "SessionLocal", lambda: fake_session)
    monkeypatch.setattr(
        main_module,
        "get_business_from_call",
        lambda _call: SimpleNamespace(
            id=1,
            external_id="demo_internal_customer_001",
            calendar_provider="google",
            calendar_oauth_status="connected",
            calendar_id="primary",
            timezone="America/New_York",
            name="Demo Restaurant",
        ),
    )

    def _raise_google_error(**_kwargs):
        raise ValueError("google api error")

    monkeypatch.setattr("app.tools.create_booking.create_google_calendar_event", _raise_google_error)

    response = client.post(
        "/v1/tools/create_booking",
        json=_retell_payload(
            {
                "customer_name": "Alice",
                "customer_phone": "+15555550123",
                "start_time": "2026-02-20T18:00:00+00:00",
                "party_size": 4,
                "notes": "Window seat",
            }
        ),
        headers={"X-Retell-Signature": "valid_signature"},
    )

    body = response.json()
    assert response.status_code == 200
    assert body["ok"] is True
    assert body["data"]["warning"] == "Calendar sync failed"
    assert fake_session.store[Booking][0].external_event_id is None
