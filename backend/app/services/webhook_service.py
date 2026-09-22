import asyncio
import secrets
import time
from typing import Any

from ..config import get_settings
from .payment_gateway_service import _store_for_path
from .payment_store import PaymentNotFoundError
from .webhook_security import WebhookUrlError, resolve_webhook_target
from .webhook_signing import WebhookSigningError, derive_webhook_secret

ALLOWED_WEBHOOK_EVENTS = {
    "payment.created",
    "payment.waiting",
    "payment.partial",
    "payment.paid_unconfirmed",
    "payment.paid_confirmed",
    "payment.overpaid",
    "payment.expired",
}


class WebhookDisabledError(RuntimeError):
    pass


class InvalidWebhookEventError(ValueError):
    pass


def _require_enabled(settings: Any) -> None:
    if not bool(settings.payment_webhook_enabled):
        raise WebhookDisabledError("Webhook service is disabled.")


def normalize_event_types(event_types: list[str] | None) -> tuple[str, ...] | None:
    if event_types is None:
        return None
    normalized = tuple(dict.fromkeys(item.strip() for item in event_types if item.strip()))
    if not normalized:
        return None
    invalid = [item for item in normalized if item not in ALLOWED_WEBHOOK_EVENTS]
    if invalid:
        raise InvalidWebhookEventError("Unsupported webhook event type.")
    return normalized


async def create_webhook_endpoint(
    *,
    url: str,
    event_types: list[str] | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    _require_enabled(settings)

    await resolve_webhook_target(
        url,
        timeout_seconds=float(settings.payment_webhook_timeout_seconds),
    )
    normalized_events = normalize_event_types(event_types)
    endpoint_id = f"wh_{secrets.token_urlsafe(18)}"
    signing_secret = derive_webhook_secret(
        settings.payment_webhook_master_key,
        endpoint_id,
    )
    now = int(time.time())
    store = _store_for_path(settings.payment_db_path)
    endpoint = await asyncio.to_thread(
        store.create_webhook_endpoint,
        endpoint_id=endpoint_id,
        url=url,
        event_types=normalized_events,
        created_at=now,
    )
    return {
        "ok": True,
        **endpoint,
        "signing_secret": signing_secret,
    }


async def list_webhook_endpoints() -> list[dict[str, Any]]:
    settings = get_settings()
    _require_enabled(settings)
    store = _store_for_path(settings.payment_db_path)
    return await asyncio.to_thread(store.list_webhook_endpoints)


async def disable_webhook_endpoint(endpoint_id: str) -> None:
    settings = get_settings()
    _require_enabled(settings)
    store = _store_for_path(settings.payment_db_path)
    await asyncio.to_thread(
        store.disable_webhook_endpoint,
        endpoint_id,
        updated_at=int(time.time()),
    )


async def list_webhook_deliveries(
    *,
    endpoint_id: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    settings = get_settings()
    _require_enabled(settings)
    store = _store_for_path(settings.payment_db_path)
    return await asyncio.to_thread(
        store.list_webhook_deliveries,
        endpoint_id=endpoint_id,
        limit=limit,
    )
