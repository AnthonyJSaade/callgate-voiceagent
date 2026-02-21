import logging
import os

from fastapi import Header, HTTPException, Request, status

from app.security.retell_verify import verify_retell_signature

logger = logging.getLogger("voiceagent.security")


def _is_dev_env() -> bool:
    return os.getenv("ENV", "dev").lower() in {"dev", "development", "local"}


def _resolve_api_key_for_purpose(purpose: str) -> tuple[str, str]:
    retell_api_key = os.getenv("RETELL_API_KEY", "")
    retell_webhook_api_key = os.getenv("RETELL_WEBHOOK_API_KEY", "")

    if purpose == "tools":
        return retell_api_key, "RETELL_API_KEY"

    if retell_webhook_api_key:
        return retell_webhook_api_key, "RETELL_WEBHOOK_API_KEY"

    if _is_dev_env() and retell_api_key:
        logger.warning(
            "RETELL_WEBHOOK_API_KEY missing; falling back to RETELL_API_KEY for webhook verification in dev."
        )
        return retell_api_key, "RETELL_API_KEY_FALLBACK_DEV"

    return "", "MISSING_WEBHOOK_KEY"


async def _require_retell_signature(
    request: Request,
    x_retell_signature: str | None,
    purpose: str,
) -> None:
    unauthorized_detail = {
        "error_code": "INVALID_RETELL_SIGNATURE",
        "human_message": f"Could not verify {purpose} request signature.",
    }

    try:
        request_body_json = await request.json()
    except Exception as exc:
        logger.error("Failed parsing JSON body for %s signature verification.", purpose)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=unauthorized_detail,
        ) from exc

    api_key, key_source = _resolve_api_key_for_purpose(purpose=purpose)
    if not api_key:
        if purpose == "webhook":
            logger.error(
                "Webhook signature verification failed: RETELL_WEBHOOK_API_KEY is required in prod."
            )
        else:
            logger.error("Tool signature verification failed: RETELL_API_KEY is missing.")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=unauthorized_detail,
        )

    is_valid = verify_retell_signature(
        request_body_json=request_body_json if isinstance(request_body_json, dict) else {},
        signature_header=x_retell_signature or "",
        api_key=api_key,
    )
    if not is_valid:
        logger.warning(
            "Retell signature verification failed. purpose=%s path=%s key_source=%s",
            purpose,
            request.url.path,
            key_source,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=unauthorized_detail,
        )


async def require_retell_tool_signature(
    request: Request,
    x_retell_signature: str | None = Header(default=None, alias="X-Retell-Signature"),
) -> None:
    await _require_retell_signature(
        request=request,
        x_retell_signature=x_retell_signature,
        purpose="tools",
    )


async def require_retell_webhook_signature(
    request: Request,
    x_retell_signature: str | None = Header(default=None, alias="X-Retell-Signature"),
) -> None:
    await _require_retell_signature(
        request=request,
        x_retell_signature=x_retell_signature,
        purpose="webhook",
    )


def require_admin_api_key(
    x_admin_key: str | None = Header(default=None, alias="X-Admin-Key"),
) -> None:
    env = os.getenv("ENV", "dev").lower()
    is_dev = env in {"dev", "development", "local"}
    configured_key = os.getenv("ADMIN_API_KEY", "")

    if not configured_key:
        if is_dev:
            logger.warning(
                "ADMIN_API_KEY is not set in dev; allowing admin request without key."
            )
            return
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error_code": "ADMIN_AUTH_NOT_CONFIGURED",
                "human_message": "Admin API key is not configured.",
            },
        )

    if x_admin_key != configured_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error_code": "INVALID_ADMIN_API_KEY",
                "human_message": "Invalid admin API key.",
            },
        )
