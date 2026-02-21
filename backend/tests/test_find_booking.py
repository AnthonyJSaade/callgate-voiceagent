from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from fastapi.testclient import TestClient

import app.main as main_module
from app.db.models import Booking, Customer
from app.main import app
from app.security import retell_verify


client = TestClient(app)


def _wrapper(args: dict):
    return {
        "name": "find_booking",
        "args": args,
        "call": {
            "call_id": "retell_call_find_1",
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
    def __init__(self, customers, bookings):
        self.store = {Customer: customers, Booking: bookings}

    def query(self, model):
        return FakeQuery(self, model)

    def close(self):
        return None


def test_find_booking_not_found(monkeypatch):
    fake_session = FakeSession(customers=[], bookings=[])
    monkeypatch.setenv("RETELL_API_KEY", "test_key")
    monkeypatch.setattr(retell_verify.retell_client, "verify", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(main_module, "SessionLocal", lambda: fake_session)
    monkeypatch.setattr(
        main_module,
        "get_business_from_call",
        lambda _call: SimpleNamespace(id=1, external_id="demo"),
    )

    response = client.post(
        "/v1/tools/find_booking",
        json=_wrapper({"customer_phone": "+1 (555) 555-0123"}),
        headers={"X-Retell-Signature": "valid_signature"},
    )
    body = response.json()
    assert response.status_code == 200
    assert body["ok"] is False
    assert body["error_code"] == "BOOKING_NOT_FOUND"


def test_find_booking_single_match(monkeypatch):
    start = datetime.now(timezone.utc) + timedelta(days=2)
    customer = SimpleNamespace(id=10, business_id=1, name="Alice", phone="+15555550123")
    booking = SimpleNamespace(
        id=20,
        business_id=1,
        customer_id=10,
        start_time=start,
        end_time=start + timedelta(minutes=90),
        party_size=4,
        status="confirmed",
    )
    fake_session = FakeSession(customers=[customer], bookings=[booking])

    monkeypatch.setenv("RETELL_API_KEY", "test_key")
    monkeypatch.setattr(retell_verify.retell_client, "verify", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(main_module, "SessionLocal", lambda: fake_session)
    monkeypatch.setattr(
        main_module,
        "get_business_from_call",
        lambda _call: SimpleNamespace(id=1, external_id="demo"),
    )

    response = client.post(
        "/v1/tools/find_booking",
        json=_wrapper({"customer_phone": "1555-555-0123", "customer_name": "Ali"}),
        headers={"X-Retell-Signature": "valid_signature"},
    )

    body = response.json()
    assert response.status_code == 200
    assert body["ok"] is True
    assert body["data"]["booking"]["booking_id"] == 20
    assert body["data"]["booking"]["customer_phone"] == "+15555550123"


def test_find_booking_ambiguous(monkeypatch):
    now = datetime.now(timezone.utc)
    customer = SimpleNamespace(id=1, business_id=1, name="Bob", phone="+15555559999")
    booking1 = SimpleNamespace(
        id=31,
        business_id=1,
        customer_id=1,
        start_time=now + timedelta(days=1),
        end_time=now + timedelta(days=1, minutes=90),
        party_size=2,
        status="confirmed",
    )
    booking2 = SimpleNamespace(
        id=32,
        business_id=1,
        customer_id=1,
        start_time=now + timedelta(days=2),
        end_time=now + timedelta(days=2, minutes=90),
        party_size=3,
        status="confirmed",
    )
    fake_session = FakeSession(customers=[customer], bookings=[booking1, booking2])

    monkeypatch.setenv("RETELL_API_KEY", "test_key")
    monkeypatch.setattr(retell_verify.retell_client, "verify", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(main_module, "SessionLocal", lambda: fake_session)
    monkeypatch.setattr(
        main_module,
        "get_business_from_call",
        lambda _call: SimpleNamespace(id=1, external_id="demo"),
    )

    response = client.post(
        "/v1/tools/find_booking",
        json=_wrapper({"customer_phone": "+1 555 555 9999"}),
        headers={"X-Retell-Signature": "valid_signature"},
    )
    body = response.json()
    assert response.status_code == 200
    assert body["ok"] is False
    assert body["error_code"] == "AMBIGUOUS_BOOKING"
    assert body["data"]["count"] == 2
