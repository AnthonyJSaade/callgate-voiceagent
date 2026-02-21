from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import Booking, Business, Customer, IdempotencyKey
from app.integrations.google_calendar import create_event as create_google_calendar_event


BOOKING_DURATION_MINUTES = 90
logger = logging.getLogger("voiceagent.tools.create_booking")


class CreateBookingArgs(BaseModel):
    customer_name: str = Field(min_length=1)
    customer_phone: str = Field(min_length=1)
    start_time: datetime
    party_size: int = Field(gt=0)
    notes: str | None = None


def parse_create_booking_args(raw_args: dict[str, Any]) -> CreateBookingArgs:
    return CreateBookingArgs.model_validate(raw_args)


def compute_create_booking_idempotency_key(call: dict, args: CreateBookingArgs) -> str:
    call_id = call.get("call_id") if isinstance(call, dict) else None
    if not call_id:
        raise ValueError("Missing call.call_id")

    key_source = f"{call_id}|{args.start_time.isoformat()}|{args.customer_phone}"
    return hashlib.sha256(key_source.encode("utf-8")).hexdigest()


def create_booking_with_idempotency(
    db: Session,
    business: Business,
    call: dict,
    args: CreateBookingArgs,
) -> dict[str, Any]:
    idempotency_key = compute_create_booking_idempotency_key(call=call, args=args)

    existing = _find_idempotency_key(db=db, key=idempotency_key)
    if existing and existing.response_json:
        return existing.response_json

    customer = _find_customer_by_phone(
        db=db,
        business_id=business.id,
        phone=args.customer_phone,
    )
    if customer is None:
        customer = Customer(
            business_id=business.id,
            name=args.customer_name,
            phone=args.customer_phone,
        )
        db.add(customer)
        db.flush()
    else:
        customer.name = args.customer_name

    end_time = args.start_time + timedelta(minutes=BOOKING_DURATION_MINUTES)
    booking = Booking(
        business_id=business.id,
        customer_id=customer.id,
        start_time=args.start_time,
        end_time=end_time,
        party_size=args.party_size,
        status="confirmed",
        notes=args.notes,
        source="retell",
    )
    db.add(booking)
    db.flush()

    response_json = {
        "ok": True,
        "data": {
            "booking_id": booking.id,
            "customer_id": customer.id,
            "customer_name": customer.name,
            "customer_phone": customer.phone,
            "start_time": booking.start_time.isoformat(),
            "end_time": booking.end_time.isoformat(),
            "party_size": booking.party_size,
            "status": booking.status,
            "source": booking.source,
            "notes": booking.notes,
        },
    }

    db.add(
        IdempotencyKey(
            key=idempotency_key,
            response_json=response_json,
        )
    )
    try:
        db.commit()
        if _is_google_calendar_connected(business):
            try:
                event_payload = create_google_calendar_event(
                    business=business,
                    booking=booking,
                    customer=customer,
                    db=db,
                )
                event_id = _pick_string(event_payload.get("id"))
                if event_id:
                    booking.external_event_provider = "google"
                    booking.external_event_id = event_id
                    db.commit()
            except Exception:
                db.rollback()
                logger.exception(
                    "Google calendar sync failed for booking_id=%s business_id=%s",
                    booking.id,
                    business.id,
                )
                response_json["data"]["warning"] = "Calendar sync failed"
                persisted = _find_idempotency_key(db=db, key=idempotency_key)
                if persisted:
                    persisted.response_json = response_json
                    db.commit()

        return response_json
    except IntegrityError:
        db.rollback()
        replay = _find_idempotency_key(db=db, key=idempotency_key)
        if replay and replay.response_json:
            return replay.response_json
        raise


def _find_idempotency_key(db: Session, key: str) -> IdempotencyKey | None:
    for row in db.query(IdempotencyKey).all():
        if row.key == key:
            return row
    return None


def _find_customer_by_phone(db: Session, business_id: int, phone: str) -> Customer | None:
    for row in db.query(Customer).all():
        if row.business_id == business_id and row.phone == phone:
            return row
    return None


def _is_google_calendar_connected(business: Business) -> bool:
    return (
        (getattr(business, "calendar_provider", "") or "").lower() == "google"
        and (getattr(business, "calendar_oauth_status", "") or "").lower() == "connected"
    )


def _pick_string(value: Any) -> str | None:
    if isinstance(value, str):
        cleaned = value.strip()
        return cleaned or None
    return None
