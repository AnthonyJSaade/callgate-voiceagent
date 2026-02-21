from __future__ import annotations

from datetime import datetime
import logging
from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.db.models import Business, Call
from app.retell.request_parser import MissingTenantContextError, resolve_business

logger = logging.getLogger("voiceagent.webhooks")


class RetellWebhookPayload(BaseModel):
    event: str | None = None
    call: dict | None = None

    model_config = ConfigDict(extra="allow")


def parse_retell_webhook_payload(raw_payload: dict[str, Any]) -> RetellWebhookPayload:
    return RetellWebhookPayload.model_validate(raw_payload)


class RetellInboundPayload(BaseModel):
    model_config = ConfigDict(extra="allow")


def parse_retell_inbound_payload(raw_payload: dict[str, Any]) -> RetellInboundPayload:
    return RetellInboundPayload.model_validate(raw_payload)


def resolve_business_for_inbound(db: Session, payload: RetellInboundPayload) -> tuple[Business, str]:
    raw = payload.model_dump(mode="python")
    to_number = _pick_string(raw, ["to_number", "to", "called_number"])
    call_obj = raw.get("call") if isinstance(raw.get("call"), dict) else {}
    if to_number is None:
        to_number = _pick_string(call_obj, ["to_number", "to", "called_number"])
    if to_number:
        by_number = _find_business_by_phone(db=db, to_number=to_number)
        if by_number is not None:
            return by_number, "to_number"

    agent_id = _pick_string(raw, ["agent_id"])
    if agent_id is None:
        agent_id = _pick_string(call_obj, ["agent_id"])
    if agent_id:
        by_agent_id = _find_business_by_agent_id(db=db, agent_id=agent_id)
        if by_agent_id is not None:
            return by_agent_id, "agent_id"

    demo = _find_demo_business(db=db)
    if demo is None:
        raise LookupError("No business mapping found for inbound request.")
    return demo, "fallback_demo"


def build_inbound_metadata_response(business: Business, routing_reason: str) -> dict[str, Any]:
    business_ref = str(business.external_id or business.id)
    metadata: dict[str, Any] = {
        "internal_customer_id": business_ref,
        "business_id": business_ref,
        "routing_reason": routing_reason,
    }
    if routing_reason == "fallback_demo":
        metadata["debug_unmapped_tenant"] = True
    # Retell inbound webhook metadata shape; see docs/RETELL_SETUP.md section:
    # "Inbound Call Webhook (sets tenant metadata)".
    return {"metadata": metadata}


def upsert_call_event(db: Session, payload: RetellWebhookPayload) -> None:
    call_data = payload.call if isinstance(payload.call, dict) else {}
    call_id = call_data.get("call_id")
    if not call_id:
        logger.warning("Webhook payload missing call.call_id; skipping call upsert.")
        return

    existing_call = _find_call_by_retell_call_id(db=db, retell_call_id=str(call_id))
    business_id = _resolve_business_id_best_effort(
        call_data=call_data,
        existing_call=existing_call,
    )

    event_payload = payload.model_dump(mode="json")
    if existing_call is None:
        existing_call = Call(
            retell_call_id=str(call_id),
            business_id=business_id,
            raw_events_json={"events": [event_payload]},
        )
        started_at = _parse_datetime(call_data.get("started_at"))
        if started_at is not None:
            existing_call.started_at = started_at
        db.add(existing_call)
    else:
        existing_call.raw_events_json = _append_raw_event(existing_call.raw_events_json, event_payload)

    if payload.event in {"call_ended", "call_analyzed"}:
        ended_at = _parse_datetime(call_data.get("ended_at"))
        if ended_at is not None:
            existing_call.ended_at = ended_at
        if call_data.get("outcome") is not None:
            existing_call.outcome = str(call_data.get("outcome"))

    db.commit()

def _resolve_business_id_best_effort(
    call_data: dict[str, Any],
    existing_call: Call | None,
) -> int | None:
    try:
        return resolve_business(call_data).id
    except (LookupError, MissingTenantContextError):
        logger.warning("unmatched webhook tenant context")
        if existing_call is not None:
            return existing_call.business_id
        return None


def _find_call_by_retell_call_id(db: Session, retell_call_id: str) -> Call | None:
    for call in db.query(Call).all():
        if call.retell_call_id == retell_call_id:
            return call
    return None


def _find_business_by_phone(db: Session, to_number: str) -> Business | None:
    normalized_target = _normalize_phone(to_number)
    for business in db.query(Business).all():
        if _normalize_phone(business.phone) == normalized_target:
            return business
        if _normalize_phone(business.transfer_phone) == normalized_target:
            return business
    return None


def _find_business_by_agent_id(db: Session, agent_id: str) -> Business | None:
    for business in db.query(Business).all():
        policies = business.policies_json or {}
        if str(policies.get("retell_agent_id", "")).strip() == agent_id:
            return business
    return None


def _find_demo_business(db: Session) -> Business | None:
    for business in db.query(Business).all():
        if str(business.external_id or "") == "demo":
            return business
    for business in db.query(Business).all():
        if business.name == "Demo Restaurant":
            return business
    businesses = db.query(Business).all()
    if businesses:
        return businesses[0]
    return None


def _pick_string(raw: dict[str, Any], keys: list[str]) -> str | None:
    if not isinstance(raw, dict):
        return None
    for key in keys:
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _normalize_phone(value: str | None) -> str:
    if not value:
        return ""
    return "".join(ch for ch in value if ch.isdigit())


def _append_raw_event(existing: dict[str, Any] | None, event_payload: dict[str, Any]) -> dict[str, Any]:
    if isinstance(existing, dict) and isinstance(existing.get("events"), list):
        events = list(existing["events"])
    elif isinstance(existing, dict):
        events = [existing]
    else:
        events = []
    events.append(event_payload)
    return {"events": events}


def _parse_datetime(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
