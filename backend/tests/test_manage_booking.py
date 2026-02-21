from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from fastapi.testclient import TestClient

import app.main as main_module
from app.db.models import Booking, Customer
from app.main import app
from app.security import retell_verify


client = TestClient(app)


def _wrapper(name: str, args: dict):
    return {
        "name": name,
        "args": args,
        "call": {
            "call_id": "retell_call_mod_1",
            "metadata": {"internal_customer_id": "demo_internal_customer_001"},
        },
    }


class FakeQuery:
    def __init__(self, session, model):
        self.session = session
        self.model = model

    def all(self):
        return list(self.session.store.get(self.model, []))

    def filter(self, *_args, **_kwargs):
        return self


class FakeSession:
    def __init__(self, bookings, customers=None):
        self.store = {
            Booking: bookings,
            Customer: list(customers or []),
        }

    def query(self, model):
        return FakeQuery(self, model)

    def commit(self):
        return None

    def close(self):
        return None


def test_modify_booking_success(monkeypatch):
    start = datetime(2026, 2, 22, 18, 0, tzinfo=timezone.utc)
    booking = SimpleNamespace(
        id=1,
        business_id=1,
        customer_id=1,
        start_time=start,
        end_time=start + timedelta(minutes=90),
        party_size=2,
        status="confirmed",
        notes="Old",
        source="retell",
    )
    fake_session = FakeSession(bookings=[booking])

    monkeypatch.setenv("RETELL_API_KEY", "test_key")
    monkeypatch.setattr(retell_verify.retell_client, "verify", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(main_module, "SessionLocal", lambda: fake_session)
    monkeypatch.setattr(
        main_module,
        "get_business_from_call",
        lambda _call: SimpleNamespace(id=1, policies_json={"max_total_guests_per_15min": 40}),
    )

    response = client.post(
        "/v1/tools/modify_booking",
        json=_wrapper(
            "modify_booking",
            {
                "booking_id": 1,
                "start_time": "2026-02-22T19:00:00+00:00",
                "party_size": 4,
                "notes": "Updated",
            },
        ),
        headers={"X-Retell-Signature": "valid_signature"},
    )

    body = response.json()
    assert response.status_code == 200
    assert body["ok"] is True
    assert body["data"]["booking_id"] == 1
    assert body["data"]["party_size"] == 4
    assert body["data"]["notes"] == "Updated"


def test_cancel_booking_success(monkeypatch):
    start = datetime(2026, 2, 22, 18, 0, tzinfo=timezone.utc)
    booking = SimpleNamespace(
        id=2,
        business_id=1,
        customer_id=1,
        start_time=start,
        end_time=start + timedelta(minutes=90),
        party_size=2,
        status="confirmed",
        notes=None,
        source="retell",
    )
    fake_session = FakeSession(bookings=[booking])

    monkeypatch.setenv("RETELL_API_KEY", "test_key")
    monkeypatch.setattr(retell_verify.retell_client, "verify", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(main_module, "SessionLocal", lambda: fake_session)
    monkeypatch.setattr(
        main_module,
        "get_business_from_call",
        lambda _call: SimpleNamespace(id=1, policies_json={"max_total_guests_per_15min": 40}),
    )

    response = client.post(
        "/v1/tools/cancel_booking",
        json=_wrapper("cancel_booking", {"booking_id": 2}),
        headers={"X-Retell-Signature": "valid_signature"},
    )

    body = response.json()
    assert response.status_code == 200
    assert body["ok"] is True
    assert body["data"]["booking_id"] == 2
    assert body["data"]["status"] == "cancelled"


def test_modify_and_cancel_wrong_tenant_rejected(monkeypatch):
    start = datetime(2026, 2, 22, 18, 0, tzinfo=timezone.utc)
    other_tenant_booking = SimpleNamespace(
        id=3,
        business_id=999,
        customer_id=1,
        start_time=start,
        end_time=start + timedelta(minutes=90),
        party_size=2,
        status="confirmed",
        notes=None,
        source="retell",
    )
    fake_session = FakeSession(bookings=[other_tenant_booking])

    monkeypatch.setenv("RETELL_API_KEY", "test_key")
    monkeypatch.setattr(retell_verify.retell_client, "verify", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(main_module, "SessionLocal", lambda: fake_session)
    monkeypatch.setattr(
        main_module,
        "get_business_from_call",
        lambda _call: SimpleNamespace(id=1, policies_json={"max_total_guests_per_15min": 40}),
    )

    modify_response = client.post(
        "/v1/tools/modify_booking",
        json=_wrapper("modify_booking", {"booking_id": 3, "notes": "attempt"}),
        headers={"X-Retell-Signature": "valid_signature"},
    )
    cancel_response = client.post(
        "/v1/tools/cancel_booking",
        json=_wrapper("cancel_booking", {"booking_id": 3}),
        headers={"X-Retell-Signature": "valid_signature"},
    )

    assert modify_response.status_code == 200
    assert modify_response.json()["ok"] is False
    assert modify_response.json()["error_code"] == "BOOKING_NOT_FOUND"

    assert cancel_response.status_code == 200
    assert cancel_response.json()["ok"] is False
    assert cancel_response.json()["error_code"] == "BOOKING_NOT_FOUND"


def test_modify_booking_google_sync_success_keeps_external_event_id(monkeypatch):
    start = datetime(2026, 2, 22, 18, 0, tzinfo=timezone.utc)
    booking = SimpleNamespace(
        id=10,
        business_id=1,
        customer_id=123,
        start_time=start,
        end_time=start + timedelta(minutes=90),
        party_size=2,
        status="confirmed",
        notes="Old",
        source="retell",
        external_event_provider="google",
        external_event_id="evt_123",
    )
    customer = SimpleNamespace(id=123, business_id=1, name="Alice", phone="+15555550123")
    fake_session = FakeSession(bookings=[booking], customers=[customer])

    monkeypatch.setenv("RETELL_API_KEY", "test_key")
    monkeypatch.setattr(retell_verify.retell_client, "verify", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(main_module, "SessionLocal", lambda: fake_session)
    monkeypatch.setattr(
        main_module,
        "get_business_from_call",
        lambda _call: SimpleNamespace(
            id=1,
            policies_json={"max_total_guests_per_15min": 40},
            calendar_provider="google",
            calendar_oauth_status="connected",
            calendar_id="primary",
            timezone="America/New_York",
            name="Demo Restaurant",
        ),
    )
    monkeypatch.setattr("app.tools.manage_booking.update_google_calendar_event", lambda **_kwargs: {"id": "evt_123"})

    response = client.post(
        "/v1/tools/modify_booking",
        json=_wrapper("modify_booking", {"booking_id": 10, "party_size": 4, "notes": "Updated"}),
        headers={"X-Retell-Signature": "valid_signature"},
    )
    body = response.json()
    assert response.status_code == 200
    assert body["ok"] is True
    assert body["data"]["party_size"] == 4
    assert booking.external_event_id == "evt_123"
    assert "warning" not in body["data"]


def test_modify_booking_google_sync_failure_returns_warning(monkeypatch):
    start = datetime(2026, 2, 22, 18, 0, tzinfo=timezone.utc)
    booking = SimpleNamespace(
        id=11,
        business_id=1,
        customer_id=124,
        start_time=start,
        end_time=start + timedelta(minutes=90),
        party_size=2,
        status="confirmed",
        notes="Old",
        source="retell",
        external_event_provider="google",
        external_event_id="evt_999",
    )
    customer = SimpleNamespace(id=124, business_id=1, name="Bob", phone="+15555550000")
    fake_session = FakeSession(bookings=[booking], customers=[customer])

    monkeypatch.setenv("RETELL_API_KEY", "test_key")
    monkeypatch.setattr(retell_verify.retell_client, "verify", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(main_module, "SessionLocal", lambda: fake_session)
    monkeypatch.setattr(
        main_module,
        "get_business_from_call",
        lambda _call: SimpleNamespace(
            id=1,
            policies_json={"max_total_guests_per_15min": 40},
            calendar_provider="google",
            calendar_oauth_status="connected",
            calendar_id="primary",
            timezone="America/New_York",
            name="Demo Restaurant",
        ),
    )

    def _raise_google_error(**_kwargs):
        raise ValueError("google update error")

    monkeypatch.setattr("app.tools.manage_booking.update_google_calendar_event", _raise_google_error)

    response = client.post(
        "/v1/tools/modify_booking",
        json=_wrapper("modify_booking", {"booking_id": 11, "notes": "Changed note"}),
        headers={"X-Retell-Signature": "valid_signature"},
    )
    body = response.json()
    assert response.status_code == 200
    assert body["ok"] is True
    assert body["data"]["notes"] == "Changed note"
    assert body["data"]["warning"] == "Calendar sync failed"


def test_cancel_booking_google_sync_success_calls_delete(monkeypatch):
    start = datetime(2026, 2, 22, 18, 0, tzinfo=timezone.utc)
    booking = SimpleNamespace(
        id=20,
        business_id=1,
        customer_id=1,
        start_time=start,
        end_time=start + timedelta(minutes=90),
        party_size=2,
        status="confirmed",
        notes=None,
        source="retell",
        external_event_provider="google",
        external_event_id="evt_cancel_1",
    )
    fake_session = FakeSession(bookings=[booking])
    called = {"delete": False}

    monkeypatch.setenv("RETELL_API_KEY", "test_key")
    monkeypatch.setattr(retell_verify.retell_client, "verify", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(main_module, "SessionLocal", lambda: fake_session)
    monkeypatch.setattr(
        main_module,
        "get_business_from_call",
        lambda _call: SimpleNamespace(
            id=1,
            policies_json={"max_total_guests_per_15min": 40},
            calendar_provider="google",
            calendar_oauth_status="connected",
            calendar_id="primary",
            timezone="America/New_York",
            name="Demo Restaurant",
        ),
    )

    def _fake_delete(**_kwargs):
        called["delete"] = True

    monkeypatch.setattr("app.tools.manage_booking.delete_google_calendar_event", _fake_delete)

    response = client.post(
        "/v1/tools/cancel_booking",
        json=_wrapper("cancel_booking", {"booking_id": 20}),
        headers={"X-Retell-Signature": "valid_signature"},
    )
    body = response.json()
    assert response.status_code == 200
    assert body["ok"] is True
    assert body["data"]["status"] == "cancelled"
    assert called["delete"] is True
    assert "warning" not in body["data"]


def test_cancel_booking_google_sync_failure_returns_warning(monkeypatch):
    start = datetime(2026, 2, 22, 18, 0, tzinfo=timezone.utc)
    booking = SimpleNamespace(
        id=21,
        business_id=1,
        customer_id=1,
        start_time=start,
        end_time=start + timedelta(minutes=90),
        party_size=2,
        status="confirmed",
        notes=None,
        source="retell",
        external_event_provider="google",
        external_event_id="evt_cancel_2",
    )
    fake_session = FakeSession(bookings=[booking])

    monkeypatch.setenv("RETELL_API_KEY", "test_key")
    monkeypatch.setattr(retell_verify.retell_client, "verify", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(main_module, "SessionLocal", lambda: fake_session)
    monkeypatch.setattr(
        main_module,
        "get_business_from_call",
        lambda _call: SimpleNamespace(
            id=1,
            policies_json={"max_total_guests_per_15min": 40},
            calendar_provider="google",
            calendar_oauth_status="connected",
            calendar_id="primary",
            timezone="America/New_York",
            name="Demo Restaurant",
        ),
    )

    def _raise_delete_error(**_kwargs):
        raise ValueError("delete failed")

    monkeypatch.setattr("app.tools.manage_booking.delete_google_calendar_event", _raise_delete_error)

    response = client.post(
        "/v1/tools/cancel_booking",
        json=_wrapper("cancel_booking", {"booking_id": 21}),
        headers={"X-Retell-Signature": "valid_signature"},
    )
    body = response.json()
    assert response.status_code == 200
    assert body["ok"] is True
    assert body["data"]["status"] == "cancelled"
    assert body["data"]["warning"] == "Calendar sync failed"
