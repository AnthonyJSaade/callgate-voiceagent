import json

from fastapi.testclient import TestClient

import app.main as main_module
from app.main import app
from app.security import retell_verify


client = TestClient(app)


def test_valid_retell_signature_allows_protected_endpoint(monkeypatch):
    monkeypatch.setenv("RETELL_API_KEY", "test_key")
    monkeypatch.setenv("RETELL_WEBHOOK_API_KEY", "test_key")

    body = {"event": "call_started", "call": {"call_id": "abc123"}}

    def fake_verify(payload: str, api_key: str, signature: str) -> bool:
        assert payload == json.dumps(body, separators=(",", ":"), ensure_ascii=False)
        assert api_key == "test_key"
        assert signature == "valid_signature"
        return True

    monkeypatch.setattr(retell_verify.retell_client, "verify", fake_verify)
    monkeypatch.setattr(main_module, "upsert_call_event", lambda **_kwargs: None)

    response = client.post(
        "/webhooks/retell",
        json=body,
        headers={"X-Retell-Signature": "valid_signature"},
    )
    assert response.status_code == 204


def test_invalid_or_missing_signature_rejected(monkeypatch):
    monkeypatch.setenv("RETELL_API_KEY", "test_key")

    monkeypatch.setattr(retell_verify.retell_client, "verify", lambda *_args, **_kwargs: False)

    invalid_response = client.post(
        "/tools/check_availability",
        json={"tool": "check_availability"},
        headers={"X-Retell-Signature": "invalid_signature"},
    )
    assert invalid_response.status_code == 401

    missing_response = client.post("/tools/check_availability", json={"tool": "check_availability"})
    assert missing_response.status_code == 401


def test_webhook_requires_webhook_api_key_in_prod(monkeypatch):
    monkeypatch.setenv("ENV", "prod")
    monkeypatch.setenv("RETELL_API_KEY", "general_key_only")
    monkeypatch.delenv("RETELL_WEBHOOK_API_KEY", raising=False)
    monkeypatch.setattr(retell_verify.retell_client, "verify", lambda *_args, **_kwargs: True)

    response = client.post(
        "/v1/retell/webhook",
        json={"event": "call_started", "call": {"call_id": "abc123"}},
        headers={"X-Retell-Signature": "valid_signature"},
    )
    assert response.status_code == 401
    assert response.json()["detail"]["error_code"] == "INVALID_RETELL_SIGNATURE"
