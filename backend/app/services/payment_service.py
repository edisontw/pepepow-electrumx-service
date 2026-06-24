import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import quote

from ..config import get_settings
from ..electrumx.client import ElectrumXClient
from ..electrumx.errors import ElectrumXError
from ..electrumx.methods import (
    scripthash_get_balance,
    scripthash_get_history,
    scripthash_get_mempool,
)
from .address_service import (
    _electrumx_error_code,
    _identify_client,
    _normalize_balance,
    _normalize_history,
    _normalize_mempool,
    _safe_address_parts,
    _safe_electrumx_error_detail,
)


class PaymentCheckError(Exception):
    code = "payment_check_error"


class InvalidPaymentAmountError(PaymentCheckError):
    code = "invalid_amount"

    def __init__(self, message: str = "Please enter a positive PEPEW amount.") -> None:
        super().__init__(self.code)
        self.message = message


class InvalidPaymentParameterError(PaymentCheckError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(code)
        self.code = code
        self.message = message


class PaymentUpstreamError(PaymentCheckError):
    code = "electrumx_error"

    def __init__(self, code: str, detail: dict[str, Any] | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail or {}


def parse_pepew_amount(amount: str, decimals: int = 8) -> int:
    value = str(amount or "").strip()
    if not value:
        raise InvalidPaymentAmountError()

    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise InvalidPaymentAmountError() from exc

    if not parsed.is_finite() or parsed <= 0:
        raise InvalidPaymentAmountError()

    scale = Decimal(10) ** decimals
    atoms = parsed * scale
    if atoms != atoms.to_integral_value():
        raise InvalidPaymentAmountError(f"Amount supports up to {decimals} decimal places.")

    return int(atoms)


def format_pepew_amount_from_sats(amount_sats: int, decimals: int = 8) -> str:
    sign = "-" if amount_sats < 0 else ""
    absolute = abs(int(amount_sats))
    scale = 10**decimals
    whole = absolute // scale
    fraction = str(absolute % scale).zfill(decimals).rstrip("0")
    return f"{sign}{whole}{'.' + fraction if fraction else ''}"


def _explorer_address_url(base_url: str, address: str) -> str | None:
    normalized_base = (base_url or "").strip().rstrip("/")
    if not normalized_base:
        return None
    return f"{normalized_base}/address/{quote(address, safe='')}"


def _parse_expires_at(expires_at: str | None, expires_in: int | None) -> int | None:
    if expires_in is not None:
        if expires_in <= 0:
            raise InvalidPaymentParameterError("invalid_expiry", "Expiry seconds must be greater than zero.")
        return int(time.time()) + expires_in

    value = (expires_at or "").strip()
    if not value:
        return None

    try:
        normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise InvalidPaymentParameterError("invalid_expiry", "Expiry must be a valid ISO8601 timestamp.") from exc

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp())


def _payment_status(
    requested_sats: int,
    confirmed_sats: int,
    unconfirmed_sats: int,
    mempool_count: int,
    confirmations_required: int,
) -> str:
    total_sats = confirmed_sats + unconfirmed_sats

    if confirmed_sats > requested_sats or total_sats > requested_sats:
        return "overpaid"
    if confirmations_required == 0 and total_sats >= requested_sats:
        return "paid_confirmed"
    if confirmed_sats >= requested_sats:
        return "paid_confirmed"
    if total_sats >= requested_sats:
        return "paid_unconfirmed"
    if total_sats > 0:
        return "partial"
    if mempool_count > 0:
        return "seen_in_mempool"
    return "waiting"


async def check_payment(
    address: str,
    amount: str,
    confirmations: int | None = None,
    expires_at: str | None = None,
    expires_in: int | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    confirmations_required = settings.pepew_min_confirmations if confirmations is None else confirmations
    if confirmations_required < 0:
        raise InvalidPaymentParameterError("invalid_confirmations", "Confirmations must be zero or greater.")

    amount_sats = parse_pepew_amount(amount, settings.pepew_decimals)
    expiry_timestamp = _parse_expires_at(expires_at, expires_in)
    normalized_address, _hash160, scripthash = _safe_address_parts(address)

    client = ElectrumXClient(settings)
    started = time.perf_counter()
    try:
        await _identify_client(client)
        balance_result = await scripthash_get_balance(client, scripthash)
        history_result = await scripthash_get_history(client, scripthash)
        mempool_result = await scripthash_get_mempool(client, scripthash)
    except ElectrumXError as exc:
        raise PaymentUpstreamError(_electrumx_error_code(exc), _safe_electrumx_error_detail(exc)) from exc
    finally:
        await client.close()

    balance = _normalize_balance(balance_result)
    history = _normalize_history(history_result)
    mempool = _normalize_mempool(mempool_result)
    confirmed_sats = max(0, int(balance.get("confirmed") or 0))
    unconfirmed_sats = max(0, int(balance.get("unconfirmed") or 0))
    total_sats = confirmed_sats + unconfirmed_sats
    status = _payment_status(
        requested_sats=amount_sats,
        confirmed_sats=confirmed_sats,
        unconfirmed_sats=unconfirmed_sats,
        mempool_count=len(mempool),
        confirmations_required=confirmations_required,
    )
    expired = expiry_timestamp is not None and int(time.time()) > expiry_timestamp

    result: dict[str, Any] = {
        "ok": True,
        "address": normalized_address,
        "amount": str(amount).strip(),
        "amount_sats": amount_sats,
        "amount_pepew": format_pepew_amount_from_sats(amount_sats, settings.pepew_decimals),
        "pepew_decimals": settings.pepew_decimals,
        "explorer_address_url": _explorer_address_url(settings.pepew_explorer_base_url, normalized_address),
        "received_confirmed_sats": confirmed_sats,
        "received_unconfirmed_sats": unconfirmed_sats,
        "confirmations_required": confirmations_required,
        "status": status,
        "expired": expired,
        "history_count": len(history),
        "mempool_count": len(mempool),
        "checked_at": int(time.time()),
        "response_time_ms": round((time.perf_counter() - started) * 1000, 2),
    }
    if expiry_timestamp is not None:
        result["expires_at"] = expiry_timestamp
    if status == "overpaid":
        result["overpaid_by_sats"] = total_sats - amount_sats
        result["payment_state"] = "confirmed" if confirmed_sats >= amount_sats else "unconfirmed"
    return result
