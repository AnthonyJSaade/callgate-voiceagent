import os

from pydantic import BaseModel, ConfigDict

from app.db.models import Business
from app.db.session import SessionLocal


class RetellFunctionRequest(BaseModel):
    name: str
    args: dict
    call: dict

    model_config = ConfigDict(extra="allow")


class MissingTenantContextError(ValueError):
    pass


def resolve_business(call: dict) -> Business:
    call_data = call if isinstance(call, dict) else {}
    metadata = call_data.get("metadata", {}) if isinstance(call_data.get("metadata"), dict) else {}

    internal_customer_id = _pick_string(metadata.get("internal_customer_id"))
    metadata_business_id = _pick_string(metadata.get("business_id"))
    to_number = _pick_string(call_data.get("to_number"))
    agent_id = _pick_string(call_data.get("agent_id"))

    session = SessionLocal()
    try:
        businesses = session.query(Business).all()

        if internal_customer_id:
            by_internal = _find_business_by_ref(businesses, internal_customer_id)
            if by_internal is not None:
                return by_internal

        if metadata_business_id:
            by_business_id = _find_business_by_ref(businesses, metadata_business_id)
            if by_business_id is not None:
                return by_business_id

        if to_number:
            by_number = _find_business_by_phone(businesses, to_number)
            if by_number is not None:
                return by_number

        if agent_id:
            by_agent = _find_business_by_agent_id(businesses, agent_id)
            if by_agent is not None:
                return by_agent

        any_context_present = bool(
            internal_customer_id or metadata_business_id or to_number or agent_id
        )
        if any_context_present:
            if _is_dev_env():
                demo = _find_demo_business(businesses)
                if demo is not None:
                    return demo
            raise LookupError("No business found for provided tenant context")

        if _is_dev_env():
            demo = _find_demo_business(businesses)
            if demo is not None:
                return demo
        raise MissingTenantContextError("Missing tenant context in call metadata")
    finally:
        session.close()


def get_business_from_call(call: dict) -> Business:
    return resolve_business(call)


def _is_dev_env() -> bool:
    return os.getenv("ENV", "dev").lower() in {"dev", "development", "local"}


def _pick_string(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _find_business_by_ref(businesses: list[Business], ref: str) -> Business | None:
    for business in businesses:
        if str(business.external_id or "") == ref:
            return business
        if str(business.id) == ref:
            return business
    return None


def _find_business_by_phone(businesses: list[Business], to_number: str) -> Business | None:
    target = _normalize_phone(to_number)
    for business in businesses:
        if _normalize_phone(business.phone) == target:
            return business
        if _normalize_phone(business.transfer_phone) == target:
            return business
    return None


def _find_business_by_agent_id(businesses: list[Business], agent_id: str) -> Business | None:
    for business in businesses:
        policies = business.policies_json or {}
        if str(policies.get("retell_agent_id", "")).strip() == agent_id:
            return business
    return None


def _find_demo_business(businesses: list[Business]) -> Business | None:
    for business in businesses:
        if str(business.external_id or "") == "demo":
            return business
    for business in businesses:
        if business.name == "Demo Restaurant":
            return business
    if businesses:
        return businesses[0]
    return None


def _normalize_phone(value: str | None) -> str:
    if not value:
        return ""
    return "".join(ch for ch in value if ch.isdigit())
