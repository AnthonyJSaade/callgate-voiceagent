from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi.testclient import TestClient

import app.main as main_module
import app.webhooks.retell as retell_webhook_module
from app.db.models import Business, Call
from app.main import app
from app.security import retell_verify


client = TestClient(app)


class FakeQuery:
    def __init__(self, session, model):
        self.session = session
        self.model = model

    def all(self):
        return list(self.session.store.get(self.model, []))


class FakeSession:
    def __init__(self):
        self.store = {
            Business: [
                SimpleNamespace(
                    id=1,
                    external_id="demo",
                    name="Demo Restaurant",
                )
            ],
            Call: [],
        }
        self.next_id = {Call: 1}

    def query(self, model):
        return FakeQuery(self, model)

    def add(self, row):
        model = type(row)
        if model in self.next_id and getattr(row, "id", None) is None:
            row.id = self.next_id[model]
            self.next_id[model] += 1
        if model in self.store and row not in self.store[model]:
            self.store[model].append(row)

    def commit(self):
        return None

    def close(self):
        return None


def test_retell_webhook_valid_signature_stores_event_and_upserts(monkeypatch):
    fake_session = FakeSession()
    monkeypatch.setenv("RETELL_API_KEY", "test_key")
    monkeypatch.setattr(retell_verify.retell_client, "verify", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(main_module, "SessionLocal", lambda: fake_session)

    payload = {
        "event": "call_ended",
        "call": {
            "call_id": "retell_call_1",
            "metadata": {"internal_customer_id": "demo_internal_customer_001"},
            "ended_at": "2026-02-23T20:30:00+00:00",
            "outcome": "booked",
        },
    }
    headers = {"X-Retell-Signature": "valid_signature"}

    first = client.post("/v1/retell/webhook", json=payload, headers=headers)
    second = client.post("/v1/retell/webhook", json=payload, headers=headers)

    assert first.status_code == 204
    assert second.status_code == 204

    assert len(fake_session.store[Call]) == 1
    saved_call = fake_session.store[Call][0]
    assert saved_call.retell_call_id == "retell_call_1"
    assert saved_call.business_id == 1
    assert saved_call.outcome == "booked"
    assert saved_call.ended_at == datetime(2026, 2, 23, 20, 30, tzinfo=timezone.utc)
    assert isinstance(saved_call.raw_events_json, dict)
    assert isinstance(saved_call.raw_events_json.get("events"), list)
    assert len(saved_call.raw_events_json["events"]) == 2


def test_retell_webhook_missing_call_id_still_returns_204(monkeypatch):
    fake_session = FakeSession()
    monkeypatch.setenv("RETELL_API_KEY", "test_key")
    monkeypatch.setattr(retell_verify.retell_client, "verify", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(main_module, "SessionLocal", lambda: fake_session)

    response = client.post(
        "/v1/retell/webhook",
        json={"event": "call_started", "call": {}},
        headers={"X-Retell-Signature": "valid_signature"},
    )

    assert response.status_code == 204
    assert len(fake_session.store[Call]) == 0


def test_retell_webhook_unmatched_tenant_context_still_stores_event(monkeypatch):
    fake_session = FakeSession()
    monkeypatch.setenv("RETELL_API_KEY", "test_key")
    monkeypatch.setattr(retell_verify.retell_client, "verify", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(main_module, "SessionLocal", lambda: fake_session)
    monkeypatch.setattr(
        retell_webhook_module,
        "resolve_business",
        lambda _call: (_ for _ in ()).throw(LookupError("no business match")),
    )

    response = client.post(
        "/v1/retell/webhook",
        json={"event": "call_started", "call": {"call_id": "retell_call_unmatched"}},
        headers={"X-Retell-Signature": "valid_signature"},
    )

    assert response.status_code == 204
    assert len(fake_session.store[Call]) == 1
    saved_call = fake_session.store[Call][0]
    assert saved_call.retell_call_id == "retell_call_unmatched"
    assert saved_call.business_id is None
    assert isinstance(saved_call.raw_events_json.get("events"), list)
