import logging

from retell import Retell


logger = logging.getLogger("voiceagent.security")
retell_client = Retell(api_key="")


def verify_retell_signature(payload: bytes | str, signature_header: str, api_key: str) -> bool:
    if not api_key or not signature_header:
        return False

    if isinstance(payload, bytes):
        try:
            payload_text = payload.decode("utf-8")
        except UnicodeDecodeError:
            return False
    else:
        payload_text = payload

    try:
        return bool(
            retell_client.verify(
                payload_text,
                api_key=api_key,
                signature=signature_header,
            )
        )
    except Exception as exc:
        logger.error("Retell signature verification error: %s", str(exc))
        return False
