from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import Business


class CreateBusinessArgs(BaseModel):
    name: str = Field(min_length=1)
    external_id: str = Field(min_length=1)
    timezone: str = "America/New_York"
    phone: str | None = None
    transfer_phone: str | None = None
    hours_json: dict[str, Any] | None = None
    policies_json: dict[str, Any] | None = None
    calendar_provider: str = "none"
    calendar_account_id: str | None = None
    calendar_id: str | None = None
    calendar_oauth_status: str = "not_connected"
    calendar_settings_json: dict[str, Any] = Field(default_factory=dict)


class UpdateBusinessArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    external_id: str | None = None
    timezone: str | None = None
    phone: str | None = None
    transfer_phone: str | None = None
    hours_json: dict[str, Any] | None = None
    policies_json: dict[str, Any] | None = None
    calendar_provider: str | None = None
    calendar_account_id: str | None = None
    calendar_id: str | None = None
    calendar_oauth_status: str | None = None
    calendar_settings_json: dict[str, Any] | None = None


def create_business(db: Session, args: CreateBusinessArgs) -> Business:
    if _external_id_exists(db, external_id=args.external_id):
        raise ValueError("external_id already exists")

    business = Business(
        name=args.name,
        external_id=args.external_id,
        timezone=args.timezone,
        phone=args.phone,
        transfer_phone=args.transfer_phone,
        hours_json=args.hours_json,
        policies_json=args.policies_json,
        calendar_provider=args.calendar_provider,
        calendar_account_id=args.calendar_account_id,
        calendar_id=args.calendar_id,
        calendar_oauth_status=args.calendar_oauth_status,
        calendar_settings_json=args.calendar_settings_json or {},
    )
    db.add(business)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        if "external_id" in str(exc).lower():
            raise ValueError("external_id already exists") from exc
        raise
    return business


def list_businesses(db: Session) -> list[Business]:
    return sorted(db.query(Business).all(), key=lambda b: b.id)


def update_business(db: Session, business_id: int, args: UpdateBusinessArgs) -> Business | None:
    business = _find_business(db, business_id=business_id)
    if business is None:
        return None

    patch = args.model_dump(exclude_unset=True)
    new_external_id = patch.get("external_id")
    if new_external_id and new_external_id != business.external_id:
        if _external_id_exists(db, external_id=new_external_id):
            raise ValueError("external_id already exists")

    for field, value in patch.items():
        setattr(business, field, value)

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        if "external_id" in str(exc).lower():
            raise ValueError("external_id already exists") from exc
        raise
    return business


def serialize_business(business: Business) -> dict[str, Any]:
    return {
        "id": business.id,
        "name": business.name,
        "external_id": business.external_id,
        "timezone": business.timezone,
        "phone": business.phone,
        "transfer_phone": business.transfer_phone,
        "hours_json": business.hours_json,
        "policies_json": business.policies_json,
        "calendar_provider": business.calendar_provider,
        "calendar_account_id": business.calendar_account_id,
        "calendar_id": business.calendar_id,
        "calendar_oauth_status": business.calendar_oauth_status,
        "calendar_settings_json": business.calendar_settings_json or {},
        "created_at": business.created_at.isoformat() if business.created_at else None,
    }


def _find_business(db: Session, business_id: int) -> Business | None:
    for business in db.query(Business).all():
        if business.id == business_id:
            return business
    return None


def _external_id_exists(db: Session, external_id: str) -> bool:
    target = (external_id or "").strip()
    if not target:
        return False
    for business in db.query(Business).all():
        if (business.external_id or "").strip() == target:
            return True
    return False
