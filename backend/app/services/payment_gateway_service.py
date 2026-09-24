import asyncio
import hashlib
import json
import re
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
from .payment_store import (
    PaymentIdempotencyConflictError,
    PaymentMerchantReferenceConflictError,
    PaymentNotFoundError,
    PaymentStore,
    PaymentStoreError,
)


class PaymentGatewayDisabledError(RuntimeError):
    pass


class PaymentTipUnavailableError(RuntimeError):
    pass


_IDEMPOTENCY_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_MERCHANT_REFERENCE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_PAYMENT_LIST_STATUSES = frozenset(
    {
        "waiting",
        "partial",
        "paid_unconfirmed",
        "paid_confirmed",
        "overpaid",
        "expired",
    }
)


def _normalize_idempotency_key(value: str | None) -> str | None:
    if value is None:
        return None
    if not _IDEMPOTENCY_KEY_RE.fullmatch(value):
        raise InvalidPaymentParameterError(
            "invalid_idempotency_key",
            "Idempotency-Key must be 1-128 characters using letters, digits, '.', '_', ':', or '-'.",
        )
    return value


def _normalize_merchant_reference(value: str | None) -> str | None:
    if value is None:
        return None
    if not _MERCHANT_REFERENCE_RE.fullmatch(value):
        raise InvalidPaymentParameterError(
            "invalid_merchant_reference",
            "merchant_reference must be 1-128 characters using letters, digits, '.', '_', ':', '/', or '-'.",
        )
    return value


def _payment_create_request_hash(
    *,
    address: str,
    amount_sats: int,
    confirmations: int | None,
    expires_in: int | None,
    label: str | None,
    message: str | None,
    merchant_reference: str | None,
) -> str:
    payload = {
        "address": address,
        "amount_sats": int(amount_sats),
        "confirmations": None if confirmations is None else int(confirmations),
        "expires_in": None if expires_in is None else int(expires_in),
        "label": label or None,
        "message": message or None,
        "merchant_reference": merchant_reference,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


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


def _merchant_payment_response(
    payment: dict[str, Any],
    *,
    decimals: int,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    item = _payment_response(payment, decimals=decimals)
    item["merchant_reference"] = payment.get("merchant_reference")
    item["idempotency_key"] = (
        idempotency_key
        if idempotency_key is not None
        else payment.get("idempotency_key")
    )
    return item


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
    merchant_reference: str | None = None,
    idempotency_key: str | None = None,
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

    normalized_idempotency_key = _normalize_idempotency_key(idempotency_key)
    normalized_merchant_reference = _normalize_merchant_reference(merchant_reference)
    request_hash = _payment_create_request_hash(
        address=normalized_address,
        amount_sats=amount_sats,
        confirmations=confirmations,
        expires_in=expires_in,
        label=label,
        message=message,
        merchant_reference=normalized_merchant_reference,
    )
    store = _store_for_path(settings.payment_db_path)

    if normalized_idempotency_key is not None:
        replay = await asyncio.to_thread(
            store.get_payment_by_idempotency_key,
            normalized_idempotency_key,
            request_hash=request_hash,
        )
        if replay is not None:
            return _merchant_payment_response(
                replay,
                decimals=settings.pepew_decimals,
                idempotency_key=normalized_idempotency_key,
            )

    if normalized_merchant_reference is not None:
        reference_existing = await asyncio.to_thread(
            store.get_payment_by_merchant_reference,
            normalized_merchant_reference,
        )
        if reference_existing is not None:
            raise PaymentMerchantReferenceConflictError(normalized_merchant_reference)

    now = int(time.time())
    height, tip_hash, baseline_txids = await _snapshot_creation_state(settings, scripthash)
    payment_id = f"pay_{secrets.token_urlsafe(18)}"

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
        merchant_reference=normalized_merchant_reference,
        baseline_txids=baseline_txids,
        idempotency_key=normalized_idempotency_key,
        request_hash=request_hash if normalized_idempotency_key is not None else None,
    )
    return _merchant_payment_response(
        payment,
        decimals=settings.pepew_decimals,
        idempotency_key=normalized_idempotency_key,
    )


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


async def list_persisted_payments(
    *,
    status: str | None = None,
    merchant_reference: str | None = None,
    limit: int = 50,
    before_created_at: int | None = None,
    before_payment_id: str | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    _require_enabled(settings)

    normalized_merchant_reference = _normalize_merchant_reference(merchant_reference)

    if status is not None and status not in _PAYMENT_LIST_STATUSES:
        raise InvalidPaymentParameterError(
            "invalid_payment_status",
            "Unsupported payment status filter.",
        )
    if limit < 1 or limit > 100:
        raise InvalidPaymentParameterError(
            "invalid_payment_limit",
            "Payment list limit must be between 1 and 100.",
        )
    if (before_created_at is None) != (before_payment_id is None):
        raise InvalidPaymentParameterError(
            "invalid_payment_cursor",
            "before_created_at and before_payment_id must be provided together.",
        )
    if before_created_at is not None and before_created_at < 0:
        raise InvalidPaymentParameterError(
            "invalid_payment_cursor",
            "before_created_at must be non-negative.",
        )
    if before_payment_id is not None and (len(before_payment_id) < 8 or len(before_payment_id) > 96):
        raise InvalidPaymentParameterError(
            "invalid_payment_cursor",
            "before_payment_id is invalid.",
        )

    store = _store_for_path(settings.payment_db_path)
    rows, has_more = await asyncio.to_thread(
        store.list_payments,
        status=status,
        merchant_reference=normalized_merchant_reference,
        limit=limit,
        before_created_at=before_created_at,
        before_payment_id=before_payment_id,
    )

    payments: list[dict[str, Any]] = []
    for payment in rows:
        payments.append(
            _merchant_payment_response(
                payment,
                decimals=settings.pepew_decimals,
            )
        )

    next_before_created_at: int | None = None
    next_before_payment_id: str | None = None
    if has_more and payments:
        next_before_created_at = int(payments[-1]["created_at"])
        next_before_payment_id = str(payments[-1]["payment_id"])

    return {
        "ok": True,
        "payments": payments,
        "has_more": has_more,
        "next_before_created_at": next_before_created_at,
        "next_before_payment_id": next_before_payment_id,
    }


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
