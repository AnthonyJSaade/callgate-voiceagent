from types import SimpleNamespace

from fastapi.testclient import TestClient

import app.main as main_module
from app.db.models import Business
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
                    phone="+15555550100",
                    transfer_phone="+15555550199",
                    policies_json={},
                )
            ]
        }

    def query(self, model):
        return FakeQuery(self, model)

    def close(self):
        return None


def test_retell_inbound_maps_to_demo_fallback(monkeypatch):
    fake_session = FakeSession()
    monkeypatch.setenv("RETELL_API_KEY", "test_key")
    monkeypatch.setattr(retell_verify.retell_client, "verify", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(main_module, "SessionLocal", lambda: fake_session)

    payload = {
        "call": {
            "to_number": "+19999999999"
        }
    }
    response = client.post(
        "/v1/retell/inbound",
        json=payload,
        headers={"X-Retell-Signature": "valid_signature"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["metadata"]["internal_customer_id"] == "demo"
    assert body["metadata"]["business_id"] == "demo"
    assert body["metadata"]["routing_reason"] == "fallback_demo"
