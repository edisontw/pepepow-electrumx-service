import asyncio
import secrets
import time
from functools import lru_cache
from typing import Any

from ..config import get_settings
from ..electrumx.client import ElectrumXClient
from ..electrumx.errors import ElectrumXError
from ..electrumx.methods import headers_subscribe, scripthash_get_history
from .address_service import _identify_client, _normalize_history, _safe_address_parts
from .payment_service import (
    InvalidPaymentAmountError,
    InvalidPaymentParameterError,
    format_pepew_amount_from_sats,
    parse_pepew_amount,
)
from .payment_store import PaymentNotFoundError, PaymentStore, PaymentStoreError


class PaymentGatewayDisabledError(RuntimeError):
    pass


class PaymentTipUnavailableError(RuntimeError):
    pass


@lru_cache(maxsize=8)
def _store_for_path(path: str) -> PaymentStore:
    return PaymentStore(path)


def clear_payment_store_cache() -> None:
    _store_for_path.cache_clear()


def _payment_response(payment: dict[str, Any], *, decimals: int) -> dict[str, Any]:
    amount_sats = int(payment["amount_sats"])
    received_sats = int(payment["received_sats"])
    confirmed_sats = int(payment["confirmed_sats"])
    policy_confirmed_sats = int(payment["policy_confirmed_sats"])
    received = format_pepew_amount_from_sats(received_sats, decimals)
    confirmed = format_pepew_amount_from_sats(confirmed_sats, decimals)
    policy_confirmed = format_pepew_amount_from_sats(policy_confirmed_sats, decimals)
    overpaid_by_sats = max(0, received_sats - amount_sats)
    return {
        "ok": True,
        "payment_id": str(payment["payment_id"]),
        "address": str(payment["address"]),
        "amount": format_pepew_amount_from_sats(amount_sats, decimals),
        "amount_sats": amount_sats,
        "confirmations_required": int(payment["confirmations_required"]),
        "created_at": int(payment["created_at"]),
        "created_height": int(payment["created_height"]),
        "expires_at": int(payment["expires_at"]),
        "status": str(payment["status"]),
        "version": int(payment["version"]),
        "received": received,
        "received_sats": received_sats,
        "confirmed": confirmed,
        "confirmed_sats": confirmed_sats,
        "policy_confirmed": policy_confirmed,
        "policy_confirmed_sats": policy_confirmed_sats,
        "overpaid_by": format_pepew_amount_from_sats(overpaid_by_sats, decimals),
        "overpaid_by_sats": overpaid_by_sats,
        "label": payment.get("label"),
        "message": payment.get("message"),
        "updated_at": int(payment["updated_at"]),
    }


def _require_enabled(settings: Any) -> None:
    if not bool(settings.payment_api_enabled):
        raise PaymentGatewayDisabledError("Payment API is disabled.")


async def _snapshot_creation_state(settings: Any, scripthash: str) -> tuple[int, str | None, tuple[str, ...]]:
    """Capture chain tip and pre-existing confirmed/mempool txids on one ElectrumX session."""
    client = ElectrumXClient(settings)
    try:
        await _identify_client(client)
        header_result = await headers_subscribe(client)
        history_result = await scripthash_get_history(client, scripthash)
    except ElectrumXError as exc:
        raise PaymentTipUnavailableError("Payment creation snapshot is unavailable.") from exc
    finally:
        await client.close()

    if not isinstance(header_result, dict):
        raise PaymentTipUnavailableError("Current chain tip is unavailable.")

    height = header_result.get("height")
    if not isinstance(height, int) or height < 0:
        raise PaymentTipUnavailableError("Current chain tip is unavailable.")

    tip_hash = header_result.get("hash")
    if tip_hash is not None and not isinstance(tip_hash, str):
        tip_hash = None

    baseline_txids = tuple(
        item["tx_hash"].lower()
        for item in _normalize_history(history_result)
        if isinstance(item.get("tx_hash"), str)
    )
    return height, tip_hash, baseline_txids


async def create_persisted_payment(
    *,
    address: str,
    amount: str,
    confirmations: int | None = None,
    expires_in: int | None = None,
    label: str | None = None,
    message: str | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    _require_enabled(settings)

    amount_sats = parse_pepew_amount(amount, settings.pepew_decimals)
    normalized_address, _hash160, scripthash = _safe_address_parts(address)

    confirmations_required = (
        settings.pepew_min_confirmations if confirmations is None else int(confirmations)
    )
    if confirmations_required < 0 or confirmations_required > 100:
        raise InvalidPaymentParameterError(
            "invalid_confirmations",
            "Confirmations must be between 0 and 100.",
        )

    expiry_seconds = (
        settings.payment_default_expiry_seconds if expires_in is None else int(expires_in)
    )
    if expiry_seconds < 60 or expiry_seconds > settings.payment_max_expiry_seconds:
        raise InvalidPaymentParameterError(
            "invalid_expiry",
            f"Expiry seconds must be between 60 and {settings.payment_max_expiry_seconds}.",
        )

    now = int(time.time())
    height, tip_hash, baseline_txids = await _snapshot_creation_state(settings, scripthash)
    payment_id = f"pay_{secrets.token_urlsafe(18)}"
    store = _store_for_path(settings.payment_db_path)

    await asyncio.to_thread(
        store.set_chain_tip,
        int(height),
        tip_hash=tip_hash,
        updated_at=now,
    )
    payment = await asyncio.to_thread(
        store.create_payment,
        payment_id=payment_id,
        address=normalized_address,
        scripthash=scripthash,
        amount_sats=amount_sats,
        confirmations_required=confirmations_required,
        created_at=now,
        created_height=int(height),
        expires_at=now + expiry_seconds,
        label=label or None,
        message=message or None,
        baseline_txids=baseline_txids,
    )
    return _payment_response(payment, decimals=settings.pepew_decimals)


async def get_persisted_payment(payment_id: str) -> dict[str, Any]:
    settings = get_settings()
    _require_enabled(settings)
    store = _store_for_path(settings.payment_db_path)
    payment = await asyncio.to_thread(
        store.refresh_payment,
        payment_id,
        now=int(time.time()),
    )
    return _payment_response(payment, decimals=settings.pepew_decimals)


async def update_persisted_chain_tip(
    tip_height: int,
    *,
    tip_hash: str | None = None,
    observed_at: int | None = None,
) -> None:
    settings = get_settings()
    store = _store_for_path(settings.payment_db_path)
    timestamp = int(time.time()) if observed_at is None else int(observed_at)
    await asyncio.to_thread(
        store.set_chain_tip,
        int(tip_height),
        tip_hash=tip_hash,
        updated_at=timestamp,
    )
