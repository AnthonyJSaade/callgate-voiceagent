import json
import logging

from retell import Retell


logger = logging.getLogger("voiceagent.security")
retell_client = Retell(api_key="")


def verify_retell_signature(request_body_json: dict, signature_header: str, api_key: str) -> bool:
    if not api_key or not signature_header or not isinstance(request_body_json, dict):
        return False

    payload = json.dumps(request_body_json, separators=(",", ":"), ensure_ascii=False)
    try:
        return bool(
            retell_client.verify(
                payload,
                api_key=api_key,
                signature=signature_header,
            )
        )
    except Exception as exc:
        logger.error("Retell signature verification error: %s", str(exc))
        return False
