from __future__ import annotations

from datetime import date, datetime, time as dt_time, timedelta, timezone
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.models import Booking, Customer


class FindBookingArgs(BaseModel):
    customer_phone: str = Field(min_length=1)
    customer_name: str | None = None
    date: str | None = None
    time: str | None = None
    lookahead_days: int = Field(default=30, ge=1, le=365)


def parse_find_booking_args(raw_args: dict[str, Any]) -> FindBookingArgs:
    return FindBookingArgs.model_validate(raw_args)


def find_booking_candidates(
    db: Session,
    business_id: int,
    args: FindBookingArgs,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    now_utc = _ensure_aware(now or datetime.now(timezone.utc))
    phone_target = _normalize_phone(args.customer_phone)

    customers = [c for c in db.query(Customer).all() if c.business_id == business_id]
    matching_customers = [
        c
        for c in customers
        if _normalize_phone(getattr(c, "phone", "")) == phone_target
        and _name_matches(getattr(c, "name", ""), args.customer_name)
    ]
    customer_ids = {c.id for c in matching_customers}
    if not customer_ids:
        return []

    range_end = now_utc + timedelta(days=args.lookahead_days)
    bookings = [
        b
        for b in db.query(Booking).all()
        if b.business_id == business_id
        and b.customer_id in customer_ids
        and str(getattr(b, "status", "")).lower() == "confirmed"
        and _ensure_aware(b.start_time) >= now_utc
        and _ensure_aware(b.start_time) <= range_end
    ]

    narrowed = _apply_time_filters(bookings, args)
    result = []
    for booking in sorted(narrowed, key=lambda b: _ensure_aware(b.start_time)):
        customer = next((c for c in matching_customers if c.id == booking.customer_id), None)
        if customer is None:
            continue
        result.append(
            {
                "booking_id": booking.id,
                "start_time": _ensure_aware(booking.start_time).isoformat(),
                "party_size": booking.party_size,
                "status": booking.status,
                "customer_name": customer.name,
                "customer_phone": customer.phone,
            }
        )
    return result


def _apply_time_filters(bookings: list[Booking], args: FindBookingArgs) -> list[Booking]:
    if not args.date and not args.time:
        return bookings

    if args.date and args.time:
        target_dt = _parse_datetime_parts(args.date, args.time)
        low = target_dt - timedelta(hours=2)
        high = target_dt + timedelta(hours=2)
        return [b for b in bookings if low <= _ensure_aware(b.start_time) <= high]

    if args.date and not args.time:
        d = date.fromisoformat(args.date)
        day_start = datetime.combine(d, dt_time.min, tzinfo=timezone.utc) - timedelta(hours=2)
        day_end = datetime.combine(d, dt_time.max, tzinfo=timezone.utc) + timedelta(hours=2)
        return [b for b in bookings if day_start <= _ensure_aware(b.start_time) <= day_end]

    target_time = _parse_time(args.time or "00:00")
    filtered = []
    for booking in bookings:
        booking_time = _ensure_aware(booking.start_time).time()
        booking_minutes = booking_time.hour * 60 + booking_time.minute
        target_minutes = target_time.hour * 60 + target_time.minute
        if abs(booking_minutes - target_minutes) <= 120:
            filtered.append(booking)
    return filtered


def _name_matches(existing_name: str, expected_name: str | None) -> bool:
    if not expected_name:
        return True
    return expected_name.strip().lower() in (existing_name or "").strip().lower()


def _normalize_phone(value: str | None) -> str:
    if not value:
        return ""
    return "".join(ch for ch in value if ch.isdigit())


def _ensure_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _parse_datetime_parts(date_text: str, time_text: str) -> datetime:
    parsed_date = date.fromisoformat(date_text)
    parsed_time = _parse_time(time_text)
    return datetime.combine(parsed_date, parsed_time, tzinfo=timezone.utc)


def _parse_time(time_text: str) -> dt_time:
    hour, minute = time_text.split(":")
    return dt_time(hour=int(hour), minute=int(minute))
