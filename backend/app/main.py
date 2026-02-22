import os
import json
import logging
import time
import uuid
from typing import Any
from datetime import timedelta

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import ValidationError

from app.admin.businesses import (
    CreateBusinessArgs,
    UpdateBusinessArgs,
    create_business,
    list_businesses,
    serialize_business,
    update_business,
)
from app.db.session import SessionLocal
from app.integrations.google_oauth import (
    build_google_auth_url,
    build_google_oauth_state,
    exchange_google_code_for_tokens,
    parse_google_oauth_state,
    persist_google_credentials_and_business,
)
from app.retell.request_parser import (
    MissingTenantContextError,
    RetellFunctionRequest,
    get_business_from_call,
)
from app.security.dependencies import (
    require_admin_api_key,
    require_retell_tool_signature,
    require_retell_webhook_signature,
)
from app.tools.check_availability import (
    DEFAULT_BOOKING_DURATION_MINUTES,
    DEFAULT_MAX_TOTAL_GUESTS_PER_15_MIN,
    fetch_existing_bookings,
    find_best_available_start_times,
    map_validation_error,
    parse_check_availability_args,
    resolve_requested_start_utc,
)
from app.tools.create_booking import create_booking_with_idempotency, parse_create_booking_args
from app.tools.find_booking import find_booking_candidates, parse_find_booking_args
from app.tools.manage_booking import (
    cancel_booking,
    modify_booking,
    parse_cancel_booking_args,
    parse_modify_booking_args,
)
from app.webhooks.retell import parse_retell_webhook_payload, upsert_call_event
from app.webhooks.retell import (
    build_inbound_metadata_response,
    parse_retell_inbound_payload,
    resolve_business_for_inbound,
)


def configure_logging() -> logging.Logger:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    return logging.getLogger("voiceagent.backend")


logger = configure_logging()
app = FastAPI(title="VoiceAgent Backend")


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
    start_time = time.perf_counter()

    response = await call_next(request)

    duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
    response.headers["x-request-id"] = request_id

    logger.info(
        json.dumps(
            {
                "event": "http_request",
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": duration_ms,
            }
        )
    )
    return response


@app.get("/health")
async def health():
    return JSONResponse(content={"ok": True})


@app.post("/v1/admin/businesses", dependencies=[Depends(require_admin_api_key)])
async def admin_create_business(payload: dict[str, Any]) -> JSONResponse:
    try:
        args = CreateBusinessArgs.model_validate(payload)
    except ValidationError as exc:
        return JSONResponse(content={"ok": False, **map_validation_error(exc)}, status_code=400)

    db = SessionLocal()
    try:
        business = create_business(db=db, args=args)
        return JSONResponse(content={"ok": True, "data": {"business": serialize_business(business)}})
    except ValueError as exc:
        return JSONResponse(
            status_code=409,
            content={
                "ok": False,
                "error_code": "DUPLICATE_EXTERNAL_ID",
                "human_message": str(exc),
            },
        )
    except Exception:
        return JSONResponse(
            status_code=500,
            content={
                "ok": False,
                "error_code": "SYSTEM_DOWN",
                "human_message": "Temporary issue creating business.",
            },
        )
    finally:
        db.close()


@app.get("/v1/admin/businesses", dependencies=[Depends(require_admin_api_key)])
async def admin_list_businesses() -> JSONResponse:
    db = SessionLocal()
    try:
        businesses = list_businesses(db=db)
        return JSONResponse(
            content={
                "ok": True,
                "data": {"businesses": [serialize_business(item) for item in businesses]},
            }
        )
    finally:
        db.close()


@app.patch("/v1/admin/businesses/{business_id}", dependencies=[Depends(require_admin_api_key)])
async def admin_update_business(business_id: int, payload: dict[str, Any]) -> JSONResponse:
    try:
        args = UpdateBusinessArgs.model_validate(payload)
    except ValidationError as exc:
        return JSONResponse(content={"ok": False, **map_validation_error(exc)}, status_code=400)

    db = SessionLocal()
    try:
        business = update_business(db=db, business_id=business_id, args=args)
        if business is None:
            return JSONResponse(
                status_code=404,
                content={
                    "ok": False,
                    "error_code": "BUSINESS_NOT_FOUND",
                    "human_message": "Business not found.",
                },
            )
        return JSONResponse(content={"ok": True, "data": {"business": serialize_business(business)}})
    except ValueError as exc:
        return JSONResponse(
            status_code=409,
            content={
                "ok": False,
                "error_code": "DUPLICATE_EXTERNAL_ID",
                "human_message": str(exc),
            },
        )
    except Exception:
        return JSONResponse(
            status_code=500,
            content={
                "ok": False,
                "error_code": "SYSTEM_DOWN",
                "human_message": "Temporary issue updating business.",
            },
        )
    finally:
        db.close()


@app.get(
    "/v1/admin/businesses/{business_id}/google/connect",
    dependencies=[Depends(require_admin_api_key)],
)
async def admin_google_connect(business_id: int) -> JSONResponse:
    client_id = os.getenv("GOOGLE_CLIENT_ID", "").strip()
    redirect_uri = os.getenv(
        "GOOGLE_REDIRECT_URI",
        "https://<ngrok-host>/v1/integrations/google/oauth/callback",
    ).strip()
    state_secret = os.getenv("GOOGLE_OAUTH_STATE_SECRET", "").strip()
    if not client_id or not redirect_uri or not state_secret:
        return JSONResponse(
            status_code=500,
            content={
                "ok": False,
                "error_code": "GOOGLE_OAUTH_NOT_CONFIGURED",
                "human_message": "Google OAuth configuration is incomplete.",
            },
        )

    state = build_google_oauth_state(business_id=business_id, secret=state_secret)
    auth_url = build_google_auth_url(client_id=client_id, redirect_uri=redirect_uri, state=state)
    return JSONResponse(content={"ok": True, "data": {"auth_url": auth_url}})


@app.get("/v1/integrations/google/oauth/callback")
async def google_oauth_callback(code: str | None = None, state: str | None = None) -> Response:
    if not code or not state:
        return JSONResponse(
            status_code=400,
            content={
                "ok": False,
                "error_code": "INVALID_OAUTH_CALLBACK",
                "human_message": "Missing OAuth code or state.",
            },
        )

    state_secret = os.getenv("GOOGLE_OAUTH_STATE_SECRET", "").strip()
    client_id = os.getenv("GOOGLE_CLIENT_ID", "").strip()
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()
    redirect_uri = os.getenv(
        "GOOGLE_REDIRECT_URI",
        "https://<ngrok-host>/v1/integrations/google/oauth/callback",
    ).strip()
    if not state_secret or not client_id or not client_secret or not redirect_uri:
        return JSONResponse(
            status_code=500,
            content={
                "ok": False,
                "error_code": "GOOGLE_OAUTH_NOT_CONFIGURED",
                "human_message": "Google OAuth configuration is incomplete.",
            },
        )

    try:
        business_id = parse_google_oauth_state(state=state, secret=state_secret)
    except ValueError as exc:
        return JSONResponse(
            status_code=400,
            content={
                "ok": False,
                "error_code": "INVALID_OAUTH_STATE",
                "human_message": str(exc),
            },
        )

    try:
        token_payload = exchange_google_code_for_tokens(
            code=code,
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
        )
    except ValueError as exc:
        return JSONResponse(
            status_code=400,
            content={
                "ok": False,
                "error_code": "OAUTH_TOKEN_EXCHANGE_FAILED",
                "human_message": str(exc),
            },
        )

    db = SessionLocal()
    try:
        persist_google_credentials_and_business(
            db=db,
            business_id=business_id,
            token_payload=token_payload,
        )
    except (LookupError, ValueError) as exc:
        db.rollback()
        return JSONResponse(
            status_code=400,
            content={
                "ok": False,
                "error_code": "GOOGLE_OAUTH_PERSIST_FAILED",
                "human_message": str(exc),
            },
        )
    except Exception:
        db.rollback()
        return JSONResponse(
            status_code=500,
            content={
                "ok": False,
                "error_code": "SYSTEM_DOWN",
                "human_message": "Temporary issue completing Google OAuth.",
            },
        )
    finally:
        db.close()

    return HTMLResponse("<html><body>Google Calendar connected. You can close this tab.</body></html>")


@app.post("/tools/check_availability", dependencies=[Depends(require_retell_tool_signature)])
@app.post("/v1/tools/check_availability", dependencies=[Depends(require_retell_tool_signature)])
async def check_availability_tool(payload: dict[str, Any]) -> JSONResponse:
    try:
        request_wrapper = RetellFunctionRequest.model_validate(payload)
    except ValidationError:
        return JSONResponse(
            content={
                "ok": False,
                "error_code": "INVALID_REQUEST",
                "human_message": "Invalid Retell function request wrapper.",
            }
        )

    try:
        args = parse_check_availability_args(request_wrapper.args)
    except ValidationError as exc:
        return JSONResponse(content={"ok": False, **map_validation_error(exc)})

    try:
        business = get_business_from_call(request_wrapper.call)
    except MissingTenantContextError as exc:
        return JSONResponse(
            content={
                "ok": False,
                "error_code": "MISSING_TENANT_CONTEXT",
                "human_message": str(exc),
            }
        )
    except (ValueError, LookupError) as exc:
        return JSONResponse(
            content={
                "ok": False,
                "error_code": "BUSINESS_RESOLUTION_FAILED",
                "human_message": str(exc),
            }
        )

    desired_start_utc = resolve_requested_start_utc(
        args=args,
        business_timezone=business.timezone,
        call_context=request_wrapper.call,
    )
    if desired_start_utc is None:
        return JSONResponse(
            content={
                "ok": False,
                "error_code": "CLARIFICATION_REQUIRED",
                "human_message": (
                    "I couldn't understand the requested date and time. "
                    "Please say a clear day and time, for example 'tomorrow at 7 PM'."
                ),
            }
        )

    policies = business.policies_json or {}
    try:
        booking_duration_minutes = int(
            policies.get("default_booking_duration_minutes", DEFAULT_BOOKING_DURATION_MINUTES)
        )
        max_total_guests_per_15_min = int(
            policies.get(
                "max_total_guests_per_15min",
                DEFAULT_MAX_TOTAL_GUESTS_PER_15_MIN,
            )
        )
    except (TypeError, ValueError):
        return JSONResponse(
            content={
                "ok": False,
                "error_code": "INVALID_BUSINESS_POLICY",
                "human_message": "Business policy values are invalid.",
            }
        )

    search_start = desired_start_utc - timedelta(minutes=args.flexibility_minutes)
    search_end = desired_start_utc + timedelta(minutes=args.flexibility_minutes)

    db = SessionLocal()
    try:
        existing_bookings = fetch_existing_bookings(
            db=db,
            business_id=business.id,
            search_start=search_start,
            search_end=search_end,
            booking_duration_minutes=booking_duration_minutes,
        )
    finally:
        db.close()

    available_slots = find_best_available_start_times(
        desired_start=desired_start_utc,
        flexibility_minutes=args.flexibility_minutes,
        party_size=args.party_size,
        booking_duration_minutes=booking_duration_minutes,
        max_total_guests_per_15_min=max_total_guests_per_15_min,
        existing_bookings=existing_bookings,
        max_results=3,
    )

    if not available_slots:
        return JSONResponse(
            content={
                "ok": True,
                "data": {
                    "result": "NO_AVAILABILITY",
                    "available_start_times": [],
                },
            }
        )

    return JSONResponse(
        content={
            "ok": True,
            "data": {
                "result": "AVAILABLE",
                "available_start_times": [slot.isoformat() for slot in available_slots],
            },
        }
    )


@app.post("/tools/create_booking", dependencies=[Depends(require_retell_tool_signature)])
@app.post("/v1/tools/create_booking", dependencies=[Depends(require_retell_tool_signature)])
async def create_booking_tool(payload: dict[str, Any]) -> JSONResponse:
    try:
        request_wrapper = RetellFunctionRequest.model_validate(payload)
    except ValidationError:
        return JSONResponse(
            content={
                "ok": False,
                "error_code": "INVALID_REQUEST",
                "human_message": "Invalid Retell function request wrapper.",
            }
        )

    try:
        args = parse_create_booking_args(request_wrapper.args)
    except ValidationError as exc:
        return JSONResponse(content={"ok": False, **map_validation_error(exc)})

    try:
        business = get_business_from_call(request_wrapper.call)
    except MissingTenantContextError as exc:
        return JSONResponse(
            content={
                "ok": False,
                "error_code": "MISSING_TENANT_CONTEXT",
                "human_message": str(exc),
            }
        )
    except (ValueError, LookupError) as exc:
        return JSONResponse(
            content={
                "ok": False,
                "error_code": "BUSINESS_RESOLUTION_FAILED",
                "human_message": str(exc),
            }
        )

    db = SessionLocal()
    try:
        response_json = create_booking_with_idempotency(
            db=db,
            business=business,
            call=request_wrapper.call,
            args=args,
        )
        return JSONResponse(content=response_json)
    except ValueError as exc:
        return JSONResponse(
            content={
                "ok": False,
                "error_code": "INVALID_ARGS",
                "human_message": str(exc),
            }
        )
    except Exception:
        return JSONResponse(
            content={
                "ok": False,
                "error_code": "SYSTEM_DOWN",
                "human_message": "Temporary issue creating booking. Please transfer call.",
            }
        )
    finally:
        db.close()


@app.post("/tools/find_booking", dependencies=[Depends(require_retell_tool_signature)])
@app.post("/v1/tools/find_booking", dependencies=[Depends(require_retell_tool_signature)])
async def find_booking_tool(payload: dict[str, Any]) -> JSONResponse:
    try:
        request_wrapper = RetellFunctionRequest.model_validate(payload)
    except ValidationError:
        return JSONResponse(
            content={
                "ok": False,
                "error_code": "INVALID_REQUEST",
                "human_message": "Invalid Retell function request wrapper.",
            }
        )

    try:
        args = parse_find_booking_args(request_wrapper.args)
    except ValidationError as exc:
        return JSONResponse(content={"ok": False, **map_validation_error(exc)})

    try:
        business = get_business_from_call(request_wrapper.call)
    except MissingTenantContextError as exc:
        return JSONResponse(
            content={
                "ok": False,
                "error_code": "MISSING_TENANT_CONTEXT",
                "human_message": str(exc),
            }
        )
    except (ValueError, LookupError) as exc:
        return JSONResponse(
            content={
                "ok": False,
                "error_code": "BUSINESS_RESOLUTION_FAILED",
                "human_message": str(exc),
            }
        )

    db = SessionLocal()
    try:
        matches = find_booking_candidates(db=db, business_id=business.id, args=args)
    finally:
        db.close()

    if len(matches) == 0:
        return JSONResponse(
            content={
                "ok": False,
                "error_code": "BOOKING_NOT_FOUND",
                "human_message": "I couldn't find a reservation under that phone number.",
            }
        )

    if len(matches) == 1:
        return JSONResponse(content={"ok": True, "data": {"booking": matches[0]}})

    return JSONResponse(
        content={
            "ok": False,
            "error_code": "AMBIGUOUS_BOOKING",
            "human_message": "I found multiple reservations. Please share date or time to narrow it down.",
            "data": {"matches": matches[:3], "count": len(matches)},
        }
    )


@app.post("/tools/modify_booking", dependencies=[Depends(require_retell_tool_signature)])
@app.post("/v1/tools/modify_booking", dependencies=[Depends(require_retell_tool_signature)])
async def modify_booking_tool(payload: dict[str, Any]) -> JSONResponse:
    try:
        request_wrapper = RetellFunctionRequest.model_validate(payload)
    except ValidationError:
        return JSONResponse(
            content={
                "ok": False,
                "error_code": "INVALID_REQUEST",
                "human_message": "Invalid Retell function request wrapper.",
            }
        )

    try:
        args = parse_modify_booking_args(request_wrapper.args)
    except ValidationError as exc:
        return JSONResponse(content={"ok": False, **map_validation_error(exc)})

    try:
        business = get_business_from_call(request_wrapper.call)
    except MissingTenantContextError as exc:
        return JSONResponse(
            content={
                "ok": False,
                "error_code": "MISSING_TENANT_CONTEXT",
                "human_message": str(exc),
            }
        )
    except (ValueError, LookupError) as exc:
        return JSONResponse(
            content={
                "ok": False,
                "error_code": "BUSINESS_RESOLUTION_FAILED",
                "human_message": str(exc),
            }
        )

    db = SessionLocal()
    try:
        return JSONResponse(content=modify_booking(db=db, business=business, args=args))
    except Exception:
        return JSONResponse(
            content={
                "ok": False,
                "error_code": "SYSTEM_DOWN",
                "human_message": "Temporary issue modifying booking. Please transfer call.",
            }
        )
    finally:
        db.close()


@app.post("/tools/cancel_booking", dependencies=[Depends(require_retell_tool_signature)])
@app.post("/v1/tools/cancel_booking", dependencies=[Depends(require_retell_tool_signature)])
async def cancel_booking_tool(payload: dict[str, Any]) -> JSONResponse:
    try:
        request_wrapper = RetellFunctionRequest.model_validate(payload)
    except ValidationError:
        return JSONResponse(
            content={
                "ok": False,
                "error_code": "INVALID_REQUEST",
                "human_message": "Invalid Retell function request wrapper.",
            }
        )

    try:
        args = parse_cancel_booking_args(request_wrapper.args)
    except ValidationError as exc:
        return JSONResponse(content={"ok": False, **map_validation_error(exc)})

    try:
        business = get_business_from_call(request_wrapper.call)
    except MissingTenantContextError as exc:
        return JSONResponse(
            content={
                "ok": False,
                "error_code": "MISSING_TENANT_CONTEXT",
                "human_message": str(exc),
            }
        )
    except (ValueError, LookupError) as exc:
        return JSONResponse(
            content={
                "ok": False,
                "error_code": "BUSINESS_RESOLUTION_FAILED",
                "human_message": str(exc),
            }
        )

    db = SessionLocal()
    try:
        return JSONResponse(content=cancel_booking(db=db, business=business, args=args))
    except Exception:
        return JSONResponse(
            content={
                "ok": False,
                "error_code": "SYSTEM_DOWN",
                "human_message": "Temporary issue cancelling booking. Please transfer call.",
            }
        )
    finally:
        db.close()


@app.post("/tools/resolve_business", dependencies=[Depends(require_retell_tool_signature)])
async def resolve_business_tool(payload: RetellFunctionRequest) -> JSONResponse:
    try:
        business = get_business_from_call(payload.call)
    except MissingTenantContextError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error_code": "MISSING_TENANT_CONTEXT",
                "human_message": str(exc),
            },
        ) from exc
    except (ValueError, LookupError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error_code": "BUSINESS_RESOLUTION_FAILED",
                "human_message": str(exc),
            },
        ) from exc

    return JSONResponse(
        content={
            "name": payload.name,
            "resolved_business": {
                "id": business.id,
                "external_id": business.external_id,
                "name": business.name,
                "timezone": business.timezone,
            },
        }
    )


@app.post("/debug/retell/resolve_business")
async def resolve_business_debug(payload: RetellFunctionRequest) -> JSONResponse:
    try:
        business = get_business_from_call(payload.call)
    except MissingTenantContextError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error_code": "MISSING_TENANT_CONTEXT",
                "human_message": str(exc),
            },
        ) from exc
    except (ValueError, LookupError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error_code": "BUSINESS_RESOLUTION_FAILED",
                "human_message": str(exc),
            },
        ) from exc

    return JSONResponse(
        content={
            "name": payload.name,
            "resolved_business": {
                "id": business.id,
                "external_id": business.external_id,
                "name": business.name,
                "timezone": business.timezone,
            },
        }
    )


@app.post("/webhooks/retell", dependencies=[Depends(require_retell_webhook_signature)])
@app.post("/v1/retell/webhook", dependencies=[Depends(require_retell_webhook_signature)])
async def retell_webhook(payload: dict[str, Any]) -> Response:
    webhook_payload = parse_retell_webhook_payload(payload)

    db = SessionLocal()
    try:
        upsert_call_event(db=db, payload=webhook_payload)
    finally:
        db.close()

    return Response(status_code=204)


@app.post("/v1/retell/inbound", dependencies=[Depends(require_retell_webhook_signature)])
async def retell_inbound(payload: dict[str, Any]) -> JSONResponse:
    try:
        inbound_payload = parse_retell_inbound_payload(payload)
    except ValidationError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error_code": "INVALID_INBOUND_PAYLOAD",
                "human_message": "Invalid inbound webhook payload.",
            },
        )

    db = SessionLocal()
    try:
        business, routing_reason = resolve_business_for_inbound(db=db, payload=inbound_payload)
        response_payload = build_inbound_metadata_response(
            business=business,
            routing_reason=routing_reason,
        )
        logger.info(
            json.dumps(
                {
                    "event": "retell_inbound_mapped",
                    "routing_reason": routing_reason,
                    "business_ref": response_payload["metadata"]["internal_customer_id"],
                }
            )
        )
        return JSONResponse(content=response_payload)
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error_code": "INBOUND_MAPPING_FAILED",
                "human_message": str(exc),
            },
        ) from exc
    finally:
        db.close()
