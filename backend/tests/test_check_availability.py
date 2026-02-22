from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

import app.main as main_module
from app.main import app
from app.security import retell_verify
from app.tools.check_availability import (
    CheckAvailabilityArgs,
    resolve_requested_start_utc,
)


client = TestClient(app)


def _retell_payload(args: dict):
    return {
        "name": "check_availability",
        "args": args,
        "call": {"metadata": {"internal_customer_id": "demo_internal_customer_001"}},
    }


def test_check_availability_returns_available_slots(monkeypatch):
    monkeypatch.setenv("RETELL_API_KEY", "test_key")
    monkeypatch.setattr(retell_verify.retell_client, "verify", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        main_module,
        "get_business_from_call",
        lambda _call: SimpleNamespace(id=1, policies_json={"max_total_guests_per_15min": 40}),
    )
    monkeypatch.setattr(main_module, "fetch_existing_bookings", lambda **_kwargs: [])

    response = client.post(
        "/v1/tools/check_availability",
        json=_retell_payload(
            {
                "requested_datetime_text": "2026-02-19 6:00 PM",
                "desired_start_iso": "2026-02-19T18:00:00+00:00",
                "party_size": 4,
                "flexibility_minutes": 30,
            }
        ),
        headers={"X-Retell-Signature": "valid_signature"},
    )

    body = response.json()
    assert response.status_code == 200
    assert body["ok"] is True
    assert body["data"]["result"] == "AVAILABLE"
    assert len(body["data"]["available_start_times"]) == 3


def test_check_availability_returns_no_availability(monkeypatch):
    monkeypatch.setenv("RETELL_API_KEY", "test_key")
    monkeypatch.setattr(retell_verify.retell_client, "verify", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        main_module,
        "get_business_from_call",
        lambda _call: SimpleNamespace(id=1, policies_json={"max_total_guests_per_15min": 4}),
    )

    desired_start = datetime(2026, 2, 19, 18, 0, tzinfo=timezone.utc)
    saturated = SimpleNamespace(
        start_time=desired_start - timedelta(hours=4),
        end_time=desired_start + timedelta(hours=4),
        party_size=4,
        status="confirmed",
    )
    monkeypatch.setattr(main_module, "fetch_existing_bookings", lambda **_kwargs: [saturated])

    response = client.post(
        "/v1/tools/check_availability",
        json=_retell_payload(
            {
                "requested_datetime_text": "2026-02-19 6:00 PM",
                "desired_start_iso": "2026-02-19T18:00:00+00:00",
                "party_size": 2,
                "flexibility_minutes": 60,
            }
        ),
        headers={"X-Retell-Signature": "valid_signature"},
    )

    body = response.json()
    assert response.status_code == 200
    assert body["ok"] is True
    assert body["data"]["result"] == "NO_AVAILABILITY"
    assert body["data"]["available_start_times"] == []


def test_check_availability_invalid_args(monkeypatch):
    monkeypatch.setenv("RETELL_API_KEY", "test_key")
    monkeypatch.setattr(retell_verify.retell_client, "verify", lambda *_args, **_kwargs: True)

    response = client.post(
        "/v1/tools/check_availability",
        json=_retell_payload({"party_size": -1}),
        headers={"X-Retell-Signature": "valid_signature"},
    )

    body = response.json()
    assert response.status_code == 200
    assert body["ok"] is False
    assert body["error_code"] == "INVALID_ARGS"


def test_resolve_requested_start_tomorrow_with_fixed_reference():
    args = CheckAvailabilityArgs.model_validate(
        {
            "requested_datetime_text": "tomorrow at 7pm",
            "party_size": 2,
        }
    )
    reference_local = datetime(2026, 2, 22, 12, 0, tzinfo=ZoneInfo("America/New_York"))
    resolved = resolve_requested_start_utc(
        args=args,
        business_timezone="America/New_York",
        call_context={},
        now_dt=reference_local,
    )
    assert resolved is not None
    assert resolved.astimezone(ZoneInfo("America/New_York")).strftime("%Y-%m-%d %H:%M") == "2026-02-23 19:00"


def test_resolve_requested_start_next_thursday_with_fixed_reference():
    args = CheckAvailabilityArgs.model_validate(
        {
            "requested_datetime_text": "Thursday at 6pm",
            "party_size": 2,
        }
    )
    reference_local = datetime(2026, 2, 22, 12, 0, tzinfo=ZoneInfo("America/New_York"))
    resolved = resolve_requested_start_utc(
        args=args,
        business_timezone="America/New_York",
        call_context={},
        now_dt=reference_local,
    )
    assert resolved is not None
    assert resolved.astimezone(ZoneInfo("America/New_York")).strftime("%Y-%m-%d %H:%M") == "2026-02-26 18:00"
