from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field, model_validator
from sqlalchemy.orm import Session

from app.db.models import Booking, Business, Customer
from app.integrations.google_calendar import delete_event as delete_google_calendar_event
from app.integrations.google_calendar import update_event as update_google_calendar_event
from app.tools.check_availability import (
    DEFAULT_MAX_TOTAL_GUESTS_PER_15_MIN,
    fetch_existing_bookings,
    is_slot_available,
)
from app.tools.create_booking import BOOKING_DURATION_MINUTES

logger = logging.getLogger("voiceagent.tools.manage_booking")


class ModifyBookingArgs(BaseModel):
    booking_id: int
    start_time: datetime | None = None
    party_size: int | None = Field(default=None, gt=0)
    notes: str | None = None

    @model_validator(mode="after")
    def validate_changes_present(self) -> "ModifyBookingArgs":
        if self.start_time is None and self.party_size is None and self.notes is None:
            raise ValueError("At least one change is required.")
        return self


class CancelBookingArgs(BaseModel):
    booking_id: int


def parse_modify_booking_args(raw_args: dict[str, Any]) -> ModifyBookingArgs:
    return ModifyBookingArgs.model_validate(raw_args)


def parse_cancel_booking_args(raw_args: dict[str, Any]) -> CancelBookingArgs:
    return CancelBookingArgs.model_validate(raw_args)


def find_booking_for_business(db: Session, business_id: int, booking_id: int) -> Booking | None:
    for booking in db.query(Booking).all():
        if booking.id == booking_id and booking.business_id == business_id:
            return booking
    return None


def modify_booking(
    db: Session,
    business: Business,
    args: ModifyBookingArgs,
) -> dict[str, Any]:
    booking = find_booking_for_business(db=db, business_id=business.id, booking_id=args.booking_id)
    if booking is None:
        return {
            "ok": False,
            "error_code": "BOOKING_NOT_FOUND",
            "human_message": "Booking not found for this business.",
        }
    if str(booking.status).lower() == "cancelled":
        return {
            "ok": False,
            "error_code": "BOOKING_ALREADY_CANCELLED",
            "human_message": "Booking is already cancelled.",
        }

    new_start = args.start_time or booking.start_time
    new_party_size = args.party_size or booking.party_size
    new_notes = args.notes if args.notes is not None else booking.notes
    new_end = new_start + timedelta(minutes=BOOKING_DURATION_MINUTES)

    if args.start_time is not None:
        policies = business.policies_json or {}
        max_total_guests_per_15_min = int(
            policies.get("max_total_guests_per_15min", DEFAULT_MAX_TOTAL_GUESTS_PER_15_MIN)
        )
        existing = fetch_existing_bookings(
            db=db,
            business_id=business.id,
            search_start=new_start,
            search_end=new_start,
            booking_duration_minutes=BOOKING_DURATION_MINUTES,
        )
        existing_without_current = [b for b in existing if b.id != booking.id]
        if not is_slot_available(
            candidate_start=new_start,
            party_size=new_party_size,
            booking_duration_minutes=BOOKING_DURATION_MINUTES,
            max_total_guests_per_15_min=max_total_guests_per_15_min,
            existing_bookings=existing_without_current,
        ):
            return {
                "ok": False,
                "error_code": "NO_AVAILABILITY",
                "human_message": "No availability for requested updated start time.",
            }

    booking.start_time = new_start
    booking.end_time = new_end
    booking.party_size = new_party_size
    booking.notes = new_notes
    db.commit()

    warning: str | None = None
    if _should_sync_google_event(business=business, booking=booking):
        customer = _find_customer_for_booking(db=db, booking=booking)
        if customer is not None:
            try:
                update_google_calendar_event(
                    business=business,
                    booking=booking,
                    customer=customer,
                    external_event_id=booking.external_event_id,
                    db=db,
                )
            except Exception:
                db.rollback()
                logger.exception(
                    "Google calendar update failed for booking_id=%s business_id=%s",
                    booking.id,
                    business.id,
                )
                warning = "Calendar sync failed"

    response = {
        "ok": True,
        "data": {
            "booking_id": booking.id,
            "start_time": booking.start_time.isoformat(),
            "end_time": booking.end_time.isoformat(),
            "party_size": booking.party_size,
            "notes": booking.notes,
            "status": booking.status,
            "source": booking.source,
        },
    }
    if warning:
        response["data"]["warning"] = warning
    return response


def cancel_booking(
    db: Session,
    business: Business,
    args: CancelBookingArgs,
) -> dict[str, Any]:
    booking = find_booking_for_business(db=db, business_id=business.id, booking_id=args.booking_id)
    if booking is None:
        return {
            "ok": False,
            "error_code": "BOOKING_NOT_FOUND",
            "human_message": "Booking not found for this business.",
        }

    if str(booking.status).lower() == "cancelled":
        return {
            "ok": True,
            "data": {
                "booking_id": booking.id,
                "status": booking.status,
            },
        }

    booking.status = "cancelled"
    db.commit()

    warning: str | None = None
    if _should_sync_google_event(business=business, booking=booking):
        try:
            delete_google_calendar_event(
                business=business,
                external_event_id=booking.external_event_id,
                db=db,
            )
        except Exception:
            db.rollback()
            logger.exception(
                "Google calendar delete failed for booking_id=%s business_id=%s",
                booking.id,
                business.id,
            )
            warning = "Calendar sync failed"

    response = {
        "ok": True,
        "data": {
            "booking_id": booking.id,
            "status": booking.status,
        },
    }
    if warning:
        response["data"]["warning"] = warning
    return response


def _find_customer_for_booking(db: Session, booking: Booking) -> Customer | None:
    for customer in db.query(Customer).all():
        if customer.id == booking.customer_id and customer.business_id == booking.business_id:
            return customer
    return None


def _should_sync_google_event(business: Business, booking: Booking) -> bool:
    return (
        (getattr(business, "calendar_provider", "") or "").lower() == "google"
        and (getattr(business, "calendar_oauth_status", "") or "").lower() == "connected"
        and (getattr(booking, "external_event_provider", "") or "").lower() == "google"
        and bool(getattr(booking, "external_event_id", None))
    )
