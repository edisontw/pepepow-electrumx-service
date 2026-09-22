import base64
import hashlib
import hmac


class WebhookSigningError(RuntimeError):
    pass


def _require_master_key(master_key: str | None) -> bytes:
    value = (master_key or "").strip()
    if len(value) < 32:
        raise WebhookSigningError("Webhook master key is not configured.")
    return value.encode("utf-8")


def derive_webhook_secret(master_key: str | None, endpoint_id: str) -> str:
    key = _require_master_key(master_key)
    digest = hmac.new(
        key,
        f"pepew-webhook-secret-v1:{endpoint_id}".encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def sign_webhook_payload(
    secret: str,
    *,
    event_id: str,
    timestamp: int,
    body: bytes,
) -> str:
    message = (
        str(int(timestamp)).encode("ascii")
        + b"."
        + event_id.encode("utf-8")
        + b"."
        + body
    )
    digest = hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()
    return f"v1={digest}"


def verify_webhook_signature(
    secret: str,
    *,
    event_id: str,
    timestamp: int,
    body: bytes,
    signature: str,
) -> bool:
    expected = sign_webhook_payload(
        secret,
        event_id=event_id,
        timestamp=timestamp,
        body=body,
    )
    return hmac.compare_digest(expected, signature)
