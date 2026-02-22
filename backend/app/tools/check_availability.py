from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import dateparser
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.orm import Session

from app.db.models import Booking


DEFAULT_BOOKING_DURATION_MINUTES = 90
DEFAULT_MAX_TOTAL_GUESTS_PER_15_MIN = 40
SLOT_INCREMENT_MINUTES = 15


class CheckAvailabilityArgs(BaseModel):
    requested_datetime_text: str = Field(min_length=1)
    party_size: int = Field(gt=0)
    flexibility_minutes: int = Field(default=60, ge=0)
    desired_start_iso: str | None = None


def parse_check_availability_args(raw_args: dict[str, Any]) -> CheckAvailabilityArgs:
    return CheckAvailabilityArgs.model_validate(raw_args)


def resolve_requested_start_utc(
    args: CheckAvailabilityArgs,
    business_timezone: str,
    call_context: dict[str, Any] | None = None,
    now_dt: datetime | None = None,
) -> datetime | None:
    tzinfo = _safe_zoneinfo(business_timezone)
    if tzinfo is None:
        return None

    reference_dt = _resolve_reference_datetime(
        call_context=call_context or {},
        business_tz=tzinfo,
        now_dt=now_dt,
    )
    parsed_local = _parse_requested_local_datetime(
        args=args,
        business_timezone=business_timezone,
        reference_dt=reference_dt,
    )
    if parsed_local is None:
        return None

    if (
        parsed_local.year < (reference_dt.year - 1)
        and not _contains_explicit_year(args.requested_datetime_text)
    ):
        return None

    return parsed_local.astimezone(timezone.utc)


def fetch_existing_bookings(
    db: Session,
    business_id: int,
    search_start: datetime,
    search_end: datetime,
    booking_duration_minutes: int,
) -> list[Booking]:
    interval_end = search_end + timedelta(minutes=booking_duration_minutes)
    return (
        db.query(Booking)
        .filter(Booking.business_id == business_id)
        .filter(Booking.end_time > search_start)
        .filter(Booking.start_time < interval_end)
        .all()
    )


def find_best_available_start_times(
    desired_start: datetime,
    flexibility_minutes: int,
    party_size: int,
    booking_duration_minutes: int,
    max_total_guests_per_15_min: int,
    existing_bookings: list[Any],
    max_results: int = 3,
) -> list[datetime]:
    window_start = desired_start - timedelta(minutes=flexibility_minutes)
    window_end = desired_start + timedelta(minutes=flexibility_minutes)

    candidates = list(_iter_15min_slots(window_start, window_end))
    candidates.sort(key=lambda dt: (abs((dt - desired_start).total_seconds()), dt))

    available: list[datetime] = []
    for candidate in candidates:
        if _is_slot_available(
            candidate,
            party_size,
            booking_duration_minutes,
            max_total_guests_per_15_min,
            existing_bookings,
        ):
            available.append(candidate)
            if len(available) >= max_results:
                break
    return available


def is_slot_available(
    candidate_start: datetime,
    party_size: int,
    booking_duration_minutes: int,
    max_total_guests_per_15_min: int,
    existing_bookings: list[Any],
) -> bool:
    return _is_slot_available(
        candidate_start=candidate_start,
        requested_party_size=party_size,
        booking_duration_minutes=booking_duration_minutes,
        max_total_guests_per_15_min=max_total_guests_per_15_min,
        existing_bookings=existing_bookings,
    )


def _iter_15min_slots(start: datetime, end: datetime):
    cursor = _floor_to_15_min(start)
    while cursor <= end:
        yield cursor
        cursor += timedelta(minutes=SLOT_INCREMENT_MINUTES)


def _floor_to_15_min(dt: datetime) -> datetime:
    minute = (dt.minute // SLOT_INCREMENT_MINUTES) * SLOT_INCREMENT_MINUTES
    return dt.replace(minute=minute, second=0, microsecond=0)


def _is_slot_available(
    candidate_start: datetime,
    requested_party_size: int,
    booking_duration_minutes: int,
    max_total_guests_per_15_min: int,
    existing_bookings: list[Any],
) -> bool:
    candidate_end = candidate_start + timedelta(minutes=booking_duration_minutes)
    bucket = candidate_start

    while bucket < candidate_end:
        total_guests = requested_party_size
        bucket_end = bucket + timedelta(minutes=SLOT_INCREMENT_MINUTES)
        for booking in existing_bookings:
            booking_status = getattr(booking, "status", "") or ""
            if str(booking_status).lower() == "cancelled":
                continue

            booking_start = _normalize_datetime(getattr(booking, "start_time"))
            booking_end = _normalize_datetime(getattr(booking, "end_time"))
            if booking_start is None or booking_end is None:
                continue

            if booking_start < bucket_end and booking_end > bucket:
                total_guests += int(getattr(booking, "party_size", 0) or 0)

            if total_guests > max_total_guests_per_15_min:
                return False
        bucket = bucket_end

    return True


def _normalize_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    return None


def map_validation_error(error: ValidationError) -> dict[str, str]:
    return {
        "error_code": "INVALID_ARGS",
        "human_message": f"Invalid args: {error.errors()[0]['msg']}",
    }


def _parse_requested_local_datetime(
    args: CheckAvailabilityArgs,
    business_timezone: str,
    reference_dt: datetime,
) -> datetime | None:
    if args.desired_start_iso:
        explicit = _parse_datetime_value(args.desired_start_iso)
        if explicit is not None:
            return explicit.astimezone(ZoneInfo(business_timezone))

    parsed = dateparser.parse(
        args.requested_datetime_text,
        settings={
            "RETURN_AS_TIMEZONE_AWARE": True,
            "TIMEZONE": business_timezone,
            "TO_TIMEZONE": business_timezone,
            "RELATIVE_BASE": reference_dt,
        },
    )
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=ZoneInfo(business_timezone))
    return parsed.astimezone(ZoneInfo(business_timezone))


def _resolve_reference_datetime(
    call_context: dict[str, Any],
    business_tz: ZoneInfo,
    now_dt: datetime | None = None,
) -> datetime:
    for key in (
        "start_timestamp",
        "start_time",
        "started_at",
        "call_start_time",
        "created_at",
    ):
        parsed = _parse_datetime_value(call_context.get(key))
        if parsed is not None:
            return parsed.astimezone(business_tz)

    metadata = call_context.get("metadata")
    if isinstance(metadata, dict):
        for key in ("start_timestamp", "started_at", "call_start_time", "created_at"):
            parsed = _parse_datetime_value(metadata.get(key))
            if parsed is not None:
                return parsed.astimezone(business_tz)

    fallback_now = now_dt or datetime.now(timezone.utc)
    if fallback_now.tzinfo is None:
        fallback_now = fallback_now.replace(tzinfo=timezone.utc)
    return fallback_now.astimezone(business_tz)


def _parse_datetime_value(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed
    return None


def _safe_zoneinfo(name: str) -> ZoneInfo | None:
    try:
        return ZoneInfo(name)
    except Exception:
        return None


def _contains_explicit_year(text: str) -> bool:
    return bool(re.search(r"\b(19|20)\d{2}\b", text))
