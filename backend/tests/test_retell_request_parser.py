from types import SimpleNamespace

from fastapi.testclient import TestClient

import app.main as main_module
from app.main import app
from app.retell.request_parser import MissingTenantContextError, get_business_from_call
from app.security import retell_verify


client = TestClient(app)


def test_get_business_from_call_resolves_by_internal_customer_id(monkeypatch):
    expected_business = SimpleNamespace(
        id=1,
        external_id="demo",
        name="Demo Restaurant",
        timezone="America/New_York",
        phone="+15555550100",
        transfer_phone="+15555550199",
        policies_json={"retell_agent_id": "agent_123"},
    )

    class FakeQuery:
        def all(self):
            return [expected_business]

    class FakeSession:
        def query(self, *_args, **_kwargs):
            return FakeQuery()

        def close(self):
            return None

    monkeypatch.setattr("app.retell.request_parser.SessionLocal", lambda: FakeSession())

    monkeypatch.setenv("ENV", "dev")
    business = get_business_from_call({"metadata": {"internal_customer_id": "demo"}})
    assert business.name == "Demo Restaurant"
    assert business.external_id == "demo"


def test_get_business_from_call_dev_fallback_when_missing_context(monkeypatch):
    expected_business = SimpleNamespace(
        id=1,
        external_id="demo",
        name="Demo Restaurant",
        timezone="America/New_York",
        phone="+15555550100",
        transfer_phone="+15555550199",
        policies_json={},
    )

    class FakeQuery:
        def all(self):
            return [expected_business]

    class FakeSession:
        def query(self, *_args, **_kwargs):
            return FakeQuery()

        def close(self):
            return None

    monkeypatch.setattr("app.retell.request_parser.SessionLocal", lambda: FakeSession())
    monkeypatch.setenv("ENV", "dev")
    business = get_business_from_call({})
    assert business.external_id == "demo"


def test_get_business_from_call_prod_missing_context_raises(monkeypatch):
    class FakeQuery:
        def all(self):
            return []

    class FakeSession:
        def query(self, *_args, **_kwargs):
            return FakeQuery()

        def close(self):
            return None

    monkeypatch.setattr("app.retell.request_parser.SessionLocal", lambda: FakeSession())
    monkeypatch.setenv("ENV", "prod")

    try:
        get_business_from_call({})
        assert False, "Expected MissingTenantContextError"
    except MissingTenantContextError:
        assert True


def test_resolve_business_endpoint_parses_wrapper_and_returns_business(monkeypatch):
    monkeypatch.setenv("RETELL_API_KEY", "test_key")
    monkeypatch.setattr(retell_verify.retell_client, "verify", lambda *_args, **_kwargs: True)

    monkeypatch.setattr(
        main_module,
        "get_business_from_call",
        lambda _call: SimpleNamespace(
            id=1,
            external_id="demo",
            name="Demo Restaurant",
            timezone="America/New_York",
        ),
    )

    response = client.post(
        "/tools/resolve_business",
        json={
            "name": "check_availability",
            "args": {"party_size": 2},
            "call": {"metadata": {"internal_customer_id": "demo"}},
        },
        headers={"X-Retell-Signature": "valid_signature"},
    )

    assert response.status_code == 200
    assert response.json()["resolved_business"]["name"] == "Demo Restaurant"


def test_check_availability_returns_missing_tenant_context_in_prod(monkeypatch):
    monkeypatch.setenv("RETELL_API_KEY", "test_key")
    monkeypatch.setenv("ENV", "prod")
    monkeypatch.setattr(retell_verify.retell_client, "verify", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        main_module,
        "get_business_from_call",
        lambda _call: (_ for _ in ()).throw(MissingTenantContextError("Missing tenant context")),
    )

    response = client.post(
        "/v1/tools/check_availability",
        json={
            "name": "check_availability",
            "args": {
                "desired_start": "2026-02-19T18:00:00+00:00",
                "party_size": 2,
            },
            "call": {},
        },
        headers={"X-Retell-Signature": "valid_signature"},
    )

    assert response.status_code == 200
    assert response.json()["ok"] is False
    assert response.json()["error_code"] == "MISSING_TENANT_CONTEXT"
